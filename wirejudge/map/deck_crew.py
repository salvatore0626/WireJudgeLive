"""Small deck-crew state machines in a shared carrier-local walking area."""
from enum import Enum
from functools import cached_property, lru_cache
import heapq
import json
from wirejudge.paths import ASSETS_DIR
import math
import random
import time
import uuid

import numpy as np

from wirejudge.aircraft.aircraft_glyphs import AIRFRAME_SPAN_M, airframe_key, wireframe_edges
from PySide6.QtCore import QObject, Signal, Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QVector3D
from wirejudge.aircraft.aircraft_labels import aircraft_callsign
from wirejudge.aircraft.pilot_identity import pilot_identity
from wirejudge.map.carrier_wireframe import catapult_tracks, deck_geometry, local_point
from wirejudge.map.map_rendering import ProcedureLines, MapTextOverlay, MAP_LINE_OPTIONS
from wirejudge.ui.settings_store import DEFAULTS

CREW_COLORS = ((.29, .9, .38, 1.), (1., .84, .18, 1.),
               (.28, .59, 1., 1.), (.77, .37, .95, 1.))
GRID_STEP = 3.
CATAPULT_CLEARANCE_M = 2.


@lru_cache(maxsize=1)
def crew_dialogue():
    with (ASSETS_DIR / 'deck_crew_names.json').open(encoding='utf-8') as source:
        names = json.load(source)
    with (ASSETS_DIR / 'deck_crew_lines.json').open(encoding='utf-8') as source:
        lines = json.load(source)
    return names, lines


class CrewState(Enum):
    AVOID = 'Avoid zone'
    WANDER = 'Wander'
    HUDDLE = 'Huddle'
    WORK = 'Work'


def segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0., min(1., ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) if length else 0.
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def inside_polygon(p, edges):
    x, y = p
    inside = False
    for a, b in edges:
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def segments_distance(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    overlap = all(max(min(a[i], b[i]), min(c[i], d[i])) <= min(max(a[i], b[i]), max(c[i], d[i]))
                  for i in (0, 1))
    if overlap and cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0:
        return 0.
    return min(segment_distance(a, c, d), segment_distance(b, c, d),
               segment_distance(c, a, b), segment_distance(d, a, b))


class WalkingArea:
    def __init__(self):
        outline = deck_geometry()[1]
        self.edges = tuple(zip(outline, outline[1:] + outline[:1]))
        self.island = local_point(453, 887)
        x, y = self.island
        island = ((x - 10, y - 15), (x + 10, y - 15), (x + 10, y + 15), (x - 10, y + 15))
        self.island_edges = tuple(zip(island, island[1:] + island[:1]))
        # The traced landing area runs bow-left, bow-right, stern-right, stern-left.
        self.runway_bow, self.runway_stern = deck_geometry()[3][1:3]
        self.catapults = catapult_tracks()
        self.forward_limit = min(min(a[1], b[1]) for a, b in self.catapults[:2])
        self.aft = min(y for _, y in outline)
        self.huddle_limit = self.aft + .6 * (max(y for _, y in outline) - self.aft)
        xmin, xmax = min(x for x, _ in outline), max(x for x, _ in outline)
        ymin, ymax = min(y for _, y in outline), max(y for _, y in outline)
        self.nodes = {(i, j): (i * GRID_STEP, j * GRID_STEP)
                      for i in range(math.floor(xmin / GRID_STEP), math.ceil(xmax / GRID_STEP) + 1)
                      for j in range(math.floor(ymin / GRID_STEP), math.ceil(ymax / GRID_STEP) + 1)
                      if self.contains((i * GRID_STEP, j * GRID_STEP))}
        self.neighbors = {}
        for key, point in self.nodes.items():
            links = []
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                other = key[0] + dx, key[1] + dy
                if other in self.nodes and self.segment_inside(point, self.nodes[other]):
                    links.append(other)
            self.neighbors[key] = tuple(links)

    def contains(self, p):
        x, y = p
        bow, stern = self.runway_bow, self.runway_stern
        runway_right = stern[0] + (y - stern[1]) * (bow[0] - stern[0]) / (bow[1] - stern[1])
        return (y < self.forward_limit - 1.1 and x > runway_right + 1.1 and inside_polygon(p, self.edges)
                and all(segment_distance(p, a, b) > 1.1 for a, b in self.edges)
                and all(segment_distance(p, a, b) > CATAPULT_CLEARANCE_M for a, b in self.catapults)
                and not (abs(x - self.island[0]) < 10 and abs(y - self.island[1]) < 15))

    def segment_inside(self, a, b):
        return (self.contains(a) and self.contains(b)
                and all(segments_distance(a, b, c, d) > 1.1 for c, d in self.edges)
                and all(segments_distance(a, b, c, d) > CATAPULT_CLEARANCE_M for c, d in self.catapults)
                and all(segments_distance(a, b, c, d) > .01 for c, d in self.island_edges))

    def nearest(self, p):
        return min(self.nodes, key=lambda key: math.dist(p, self.nodes[key]))


@lru_cache(maxsize=1)
def walking_area():
    return WalkingArea()


class CrewModel:
    def __init__(self, on_message=None):
        self.on_message = on_message
        self.names, self.dialogue = crew_dialogue()
        self.callsigns, self.players = {}, ()
        self.rng = random.Random()
        self.people = []
        self.huddles = []
        self.aircraft = {}
        self.last_stamp = None
        self.next_avoid_check = 0.
        self.next_chatter = None

    @cached_property
    def area(self):
        return walking_area()

    def safe(self, p, margin=0.):
        return all(segment_distance(p, plane['position'], plane['end']) > plane['radius'] + margin
                   for plane in self.aircraft.values() if not plane['parked'])

    def set_count(self, count, *, simulate=True):
        del self.people[count:]
        if simulate:
            self.place_crew(count)
        used = {person['name'] for person in self.people}
        for _ in range(count - len(self.people)):
            available_names = [name for name in self.names if name not in used]
            name = self.rng.choice(available_names) if available_names else f'Crew {len(self.people) + 1}'
            used.add(name)
            self.people.append(dict(id=uuid.uuid4().hex, name=name, color=CREW_COLORS[len(self.people) % len(CREW_COLORS)],
                                    position=None, previous=None, target=None, path=[], heading=0.,
                                    state=CrewState.WANDER, arrived=False, until=0., replan=True,
                                    running=False, huddle=None, aircraft=None, retry_at=0.,
                                    phase=self.rng.uniform(0, math.tau), gait=0., previous_gait=0.,
                                    work_style=self.rng.choice(('hammer', 'search'))))
        if simulate:
            self.place_new_crew()

    def place_crew(self, count):
        huddle_count = max(1, math.ceil(count / 8))
        if len(self.huddles) != huddle_count:
            candidates = [p for p in self.area.nodes.values() if p[1] <= self.area.huddle_limit]
            self.huddles = []
            for _ in range(huddle_count):
                center = max(candidates, key=lambda p: min((math.dist(p, h) for h in self.huddles),
                                                           default=-abs(p[1] - (self.area.aft + self.area.huddle_limit) / 2)))
                self.huddles.append(center)
                candidates.remove(center)
            for person in self.people:
                if person['state'] == CrewState.HUDDLE:
                    person['replan'] = True
                    person['huddle'] = None

    def place_new_crew(self):
        unplaced = [person for person in self.people if person['position'] is None]
        if unplaced:
            choices = [p for p in self.area.nodes.values() if self.safe(p)]
            if choices:
                for person in unplaced:
                    p = self.rng.choice(choices)
                    person.update(position=p, previous=p, target=p)

    def observe(self, stamp, aircraft):
        """Input aircraft are (id, local position, displayed footprint radius)."""
        if self.last_stamp is not None and (stamp < self.last_stamp or stamp - self.last_stamp > 2):
            self.aircraft.clear()
        dt = stamp - self.last_stamp if self.last_stamp is not None else 0.
        previous, self.aircraft = self.aircraft, {}
        for instance, p, radius in aircraft:
            old = previous.get(instance)
            velocity, since = (0., 0.), stamp
            if old is not None:
                if dt == 0:
                    velocity, since = old['velocity'], old['since']
                elif 0 < dt <= 2:
                    velocity = tuple((p[i] - old['position'][i]) / dt for i in (0, 1))
                    if math.hypot(*velocity) < .6:
                        since = old['since']
            speed = math.hypot(*velocity)
            lookahead = min(4., 90. / speed) if speed > 1 else 0.
            end = tuple(p[i] + velocity[i] * lookahead for i in (0, 1))
            self.aircraft[instance] = dict(position=p, end=end, radius=radius, velocity=velocity,
                                          since=since, parked=speed < .6 and stamp - since >= 2.)
        self.last_stamp = stamp

    def route(self, start, target=None):
        """Shared small grid routes around the island and current moving aircraft."""
        first = self.area.nearest(start)
        last = self.area.nearest(target) if target is not None else None
        escaping = target is None
        containing = {key for key, plane in self.aircraft.items()
                      if not plane['parked'] and segment_distance(start, plane['position'], plane['end']) <= plane['radius']}

        def allowed(p):
            return all((escaping and key in containing) or plane['parked']
                       or segment_distance(p, plane['position'], plane['end']) > plane['radius']
                       for key, plane in self.aircraft.items())

        if not self.area.segment_inside(start, self.area.nodes[first]):
            return []
        queue, costs, parents = [(0., first)], {first: 0.}, {}
        while queue:
            _, node = heapq.heappop(queue)
            p = self.area.nodes[node]
            if (escaping and self.safe(p, .75)) or (not escaping and node == last):
                path = [p]
                while node in parents:
                    node = parents[node]
                    path.append(self.area.nodes[node])
                path.reverse()
                if not escaping and math.dist(path[-1], target) > .01:
                    if not self.area.segment_inside(path[-1], target) or not self.safe(target):
                        return []
                    path.append(target)
                return path
            for neighbor in self.area.neighbors[node]:
                end = self.area.nodes[neighbor]
                middle = (p[0] + end[0]) / 2, (p[1] + end[1]) / 2
                if not allowed(end) or not allowed(middle):
                    continue
                cost = costs[node] + math.dist(p, end)
                if cost >= costs.get(neighbor, float('inf')):
                    continue
                parents[neighbor], costs[neighbor] = node, cost
                estimate = 0. if escaping else math.dist(end, self.area.nodes[last])
                heapq.heappush(queue, (cost + estimate, neighbor))
        return []

    def enter(self, person, state, target, now, *, huddle=None, aircraft=None, run_chance=.2):
        path = self.route(person['position'], None if state == CrewState.AVOID else target)
        if not path:
            return False
        person.update(state=state, target=path[-1], path=path, arrived=False, until=0.,
                      replan=False, huddle=huddle, aircraft=aircraft, spoke=False,
                      running=state == CrewState.AVOID or self.rng.random() < run_chance, retry_at=now)
        return True

    def work_destination(self, person):
        parked = [(key, plane) for key, plane in self.aircraft.items() if plane['parked']]
        self.rng.shuffle(parked)
        for key, plane in parked:
            center = plane['position']
            spots = [center] + [
                (center[0] + radius * math.cos(i * math.tau / 8),
                 center[1] + radius * math.sin(i * math.tau / 8))
                for radius in (1.5, 3.) for i in range(8)]
            spots = [p for p in spots if self.area.contains(p) and self.safe(p)]
            self.rng.shuffle(spots)
            for target in spots[:4]:
                if self.route(person['position'], target):
                    return key, target
        return None

    def choose_state(self, person, now):
        # Release this person's reservation before counting huddle occupancy.
        person['huddle'] = None
        work = self.work_destination(person)
        run_chance = .6 if work is not None else .2
        choices = [CrewState.HUDDLE, CrewState.WANDER]
        weights = [70, 30]
        if work is not None:
            choices = [CrewState.WORK, CrewState.HUDDLE, CrewState.WANDER]
            weights = [60, 30, 10]
        # If an activity is unavailable, retry among the remaining weighted choices.
        while choices:
            state = self.rng.choices(choices, weights=weights)[0]
            index = choices.index(state)
            choices.pop(index)
            weights.pop(index)
            if state == CrewState.HUDDLE:
                counts = [sum(p['state'] == CrewState.HUDDLE and not p['replan'] and p['huddle'] == i
                              for p in self.people) for i in range(len(self.huddles))]
                available = [i for i, center in enumerate(self.huddles) if counts[i] < 10 and self.safe(center, 3.)]
                occupied = [i for i in available if counts[i]]
                self.rng.shuffle(occupied)
                empty = [i for i in available if not counts[i]]
                self.rng.shuffle(empty)
                for index in occupied + empty:
                    center = self.huddles[index]
                    reserved = [p['target'] for p in self.people
                                if p is not person and p['state'] == CrewState.HUDDLE and p['huddle'] == index]
                    spots = [center] + [
                        (center[0] + radius * math.cos(i * math.tau / 8),
                         center[1] + radius * math.sin(i * math.tau / 8))
                        for radius in (1.5, 3.) for i in range(8)]
                    spots = [p for p in spots if self.area.contains(p) and p[1] <= self.area.huddle_limit
                             and self.safe(p) and all(math.dist(p, other) > .8 for other in reserved)]
                    self.rng.shuffle(spots)
                    for target in spots:
                        if self.enter(person, state, target, now, huddle=index, run_chance=run_chance):
                            return
            elif state == CrewState.WORK:
                key, target = work
                if self.enter(person, state, target, now, aircraft=key, run_chance=run_chance):
                    person['work_style'] = self.rng.choice(('hammer', 'search'))
                    return
            else:
                spots = [p for p in self.area.nodes.values() if self.safe(p) and math.dist(p, person['position']) > 5]
                for target in self.rng.sample(spots, min(6, len(spots))):
                    if self.enter(person, state, target, now, run_chance=run_chance):
                        return
        person.update(path=[], replan=True, retry_at=now + 1., arrived=False)

    def speak(self, person, key=None):
        if self.on_message is None:
            return
        if key is None:
            if self.rng.random() >= min(.5, 1 / max(1, len(self.people))):
                return
            key = 'work' if person['state'] == CrewState.WORK else 'huddle'
        replacements = {
            '[aircraft]': self.callsigns.get(person['aircraft']),
            '[r_player]': self.rng.choice(self.players) if self.players else None,
            '[r_aircraft]': self.rng.choice(tuple(self.callsigns.values())) if self.callsigns else None,
        }
        lines = [line for line in self.dialogue.get(key, [])
                 if all(token not in line or value for token, value in replacements.items())]
        if not lines:
            return
        text = self.rng.choice(lines)
        for token, value in replacements.items():
            text = text.replace(token, value or '')
        self.on_message(dict(name=person['name'], text=text, color=person['color'], crew_id=person['id']))

    def random_chatter(self, now, *, immediate=False):
        if not self.people or self.on_message is None:
            return
        if immediate or (self.next_chatter is not None and now >= self.next_chatter):
            self.speak(self.rng.choice(self.people), 'random')
            self.next_chatter = None
        if self.next_chatter is None:
            self.next_chatter = now + self.rng.uniform(120, 300)

    def check_avoid(self, now):
        if now < self.next_avoid_check:
            return
        self.next_avoid_check = now + .25 - (now - self.next_avoid_check) % .25
        for person in self.people:
            if person['position'] is None:
                continue
            if not self.safe(person['position']):
                if person['state'] != CrewState.AVOID or not person['path'] or not self.safe(person['target']):
                    if not self.enter(person, CrewState.AVOID, None, now):
                        person.update(state=CrewState.AVOID, path=[], huddle=None, aircraft=None, arrived=False)
                continue
            if person['state'] == CrewState.AVOID:
                person['replan'] = True
            if person['state'] != CrewState.AVOID and not self.safe(person['target']):
                person['replan'] = True
            if person['state'] == CrewState.HUDDLE and person['huddle'] is not None:
                if not self.safe(self.huddles[person['huddle']], 3.):
                    person['replan'] = True
            if person['state'] == CrewState.WORK:
                plane = self.aircraft.get(person['aircraft'])
                if plane is None or not plane['parked']:
                    person['replan'] = True
            if person['replan'] and now >= person['retry_at']:
                self.choose_state(person, now)

    def step(self, now, dt=.1):
        self.random_chatter(now)
        self.check_avoid(now)
        for person in self.people:
            if person['position'] is None:
                continue
            person['previous'], person['previous_gait'] = person['position'], person['gait']
            if person['replan'] or not person['path']:
                continue
            if person['arrived']:
                if not person['spoke'] and now >= person['speak_at']:
                    person['spoke'] = True
                    self.speak(person)
                if now >= person['until']:
                    self.choose_state(person, now)
                continue
            remaining = (3.75 if person['running'] else 1.25) * dt
            while person['path'] and remaining > 0:
                start, target = person['position'], person['path'][0]
                distance = math.dist(start, target)
                if distance < .01:
                    person['path'].pop(0)
                    continue
                travel = min(distance, remaining)
                end = tuple(start[i] + (target[i] - start[i]) * travel / distance for i in (0, 1))
                escaping = person['state'] == CrewState.AVOID
                safe = all(plane['parked'] or
                           (escaping and segment_distance(start, plane['position'], plane['end']) <= plane['radius'])
                           or segment_distance(end, plane['position'], plane['end']) > plane['radius']
                           for plane in self.aircraft.values())
                if not self.area.segment_inside(start, end) or not safe:
                    person.update(path=[], replan=True, retry_at=now + .5)
                    break
                person['heading'] = math.atan2(end[1] - start[1], end[0] - start[0])
                person['position'] = end
                person['gait'] += travel * (3.8 if person['running'] else 6.)
                remaining -= travel
            if not person['path'] and not person['replan']:
                if person['state'] in (CrewState.WANDER, CrewState.AVOID):
                    self.choose_state(person, now)
                else:
                    person['arrived'] = True
                    person['path'] = [person['target']]
                    person['until'] = now + (self.rng.uniform(45, 150) if person['state'] == CrewState.HUDDLE
                                             else self.rng.uniform(22.5, 67.5))
                    person['speak_at'] = now + (person['until'] - now) / 2


class CrewLabels(MapTextOverlay):
    def __init__(self, view, crew):
        super().__init__(view)
        self.crew = crew
        self.label_font = self.map_label_font()

    def refresh_label_font(self):
        self.label_font = self.map_label_font()
        self.update()

    def refresh(self):
        self.setVisible(bool(self.crew.selected_id and self.crew.positions
                             and self.parentWidget().is_3d and self.parentWidget().span <= 250))
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        if self.crew.model is None:
            return
        projection = self.compute_projection()
        painter = QPainter(self)
        painter.setFont(self.label_font)
        metrics = painter.fontMetrics()
        occupied = []
        people = sorted(self.crew.model.people, key=lambda p: p['id'] != self.crew.selected_id)
        for person in people:
            head = self.crew.heads.get(person['id'])
            if head is None:
                continue
            position, outline = head
            p = projection.map(QVector3D(*position))
            if not 0 <= p.z() <= 1:
                continue
            top = min(projection.map(QVector3D(*point)).y() for point in outline)
            width, height = metrics.horizontalAdvance(person['name']) + 4, metrics.height()
            rect = QRectF(p.x() - width / 2, top - height - 3, width, height)
            if not rect.intersects(QRectF(self.rect())) or any(rect.intersects(other) for other in occupied):
                continue
            occupied.append(rect)
            role = 'accent' if person['id'] == self.crew.selected_id else 'text'
            painter.setPen(QColor(self.parentWidget().paint_color(role)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, person['name'])
        painter.end()


class DeckCrew(QObject):
    message = Signal(object)

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.enabled = False
        self.count = DEFAULTS['deck_crew_count']
        self.frame = None
        self.model = None
        self.telemetry = None
        self.simulating = False
        self.suspended_at = None
        self.last_step = self.last_draw = 0.
        self.selected_id = None
        self.following = False
        self.positions = {}
        self.heads = {}
        self.labels = CrewLabels(view, self)
        self.lines = ProcedureLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
        self.lines.thickness = 2
        self.lines.setDepthValue(108)
        self.lines.hide()
        view.addItem(self.lines)

    def configure(self, enabled, count):
        self.enabled, self.count = enabled, count
        if not enabled:
            self.clear()
        elif self.model is not None:
            self.model.set_count(count, simulate=self.simulating)
            self.sync_visibility(time.monotonic())
        self.view.update()

    def clear(self):
        self.selected_id = None
        self.following = False
        self.positions.clear()
        self.heads.clear()
        self.labels.hide()
        self.model = self.frame = None
        self.telemetry = None
        self.simulating = False
        self.suspended_at = None
        self.lines.hide()

    def set_frame(self, frame, stamp, points, aircraft):
        if not self.enabled:
            return
        if frame is None:
            self.clear()
            return
        if self.frame is None or self.frame[0] != frame[0]:
            self.clear()
            self.model = CrewModel(self.message.emit)
            self.model.set_count(self.count, simulate=False)
        self.frame = frame
        self.telemetry = stamp, points, aircraft
        if self.simulating and self.in_view():
            self.observe()
            self.draw(time.monotonic())

    def update_callsigns(self, aircraft):
        self.model.callsigns = {a['instance_id']: aircraft_callsign(a) for a in aircraft if aircraft_callsign(a)}
        self.model.players = tuple(dict.fromkeys(
            pilot_identity(a.get('identity') or {}, a['instance_id'])[0] for a in aircraft))

    def observe(self):
        stamp, points, aircraft = self.telemetry
        self.update_callsigns(aircraft)
        _, project, deck = self.frame
        origin = project((0., 0.), deck)
        right, forward = project((1., 0.), deck), project((0., 1.), deck)
        rx, ry = right[0] - origin[0], right[1] - origin[1]
        fx, fy = forward[0] - origin[0], forward[1] - origin[1]
        by_id = {a['instance_id']: a for a in aircraft}
        observations = []
        for instance, position, _ in points:
            if abs(position[2] - deck) > 8:
                continue
            dx, dy = position[0] - origin[0], position[1] - origin[1]
            local = dx * rx + dy * ry, dx * fx + dy * fy
            key = airframe_key((by_id[instance].get('identity') or {}).get('player_vehicle'))
            edges = wireframe_edges(key) if key is not None else None
            radius = float(np.linalg.norm(edges[:, :2], axis=1).max()) if edges is not None else AIRFRAME_SPAN_M.get(key, 18.) * .8
            observations.append((instance, local, radius * self.view.icon_scale_3d * 1.25 * 1.35 + 2.))
        self.model.observe(stamp, observations)
        self.model.place_new_crew()

    def in_view(self):
        # A roughly 1.8 m figure occupies one pixel at this zoom.
        return self.view.is_3d and self.view.span <= 1.8 * self.view.width()

    def sync_visibility(self, now):
        visible = bool(self.model is not None and self.count and self.in_view())
        if visible == self.simulating:
            return
        self.simulating = visible
        if not visible:
            self.suspended_at = now
            self.model.aircraft.clear()
            self.model.last_stamp = None
            self.following = False
            self.positions.clear()
            self.heads.clear()
            self.lines.hide()
            self.labels.hide()
            return
        if self.suspended_at is not None:
            elapsed = now - self.suspended_at
            for person in self.model.people:
                for key in ('until', 'speak_at', 'retry_at'):
                    if person.get(key):
                        person[key] += elapsed
        self.suspended_at = None
        self.model.next_avoid_check = now
        self.last_step = now
        self.observe()
        self.model.set_count(self.count)
        self.draw(now)

    def tick(self, now):
        if not self.enabled or self.frame is None or self.model is None:
            return
        if not self.simulating:
            if self.model.next_chatter is not None and now >= self.model.next_chatter:
                self.update_callsigns(self.telemetry[2])
            self.model.random_chatter(now)
            return
        self.model.check_avoid(now)
        if now - self.last_step >= .1:
            self.model.step(now)
            self.last_step = now
        if now - self.last_draw >= 1 / 30:
            self.draw(now)

    def draw(self, now):
        self.last_draw = now
        _, project, deck = self.frame
        t = max(0., min(1., (now - self.last_step) / .1))
        vertices, colors = [], []
        self.positions.clear()
        self.heads.clear()
        for person in self.model.people:
            old, new = person['previous'], person['position']
            if new is None:
                continue
            x, y = (old[j] + (new[j] - old[j]) * t for j in (0, 1))
            angle, phase = person['heading'], person['phase']
            c, s = math.cos(angle), math.sin(angle)
            moving = t < 1. and math.dist(old, new) > .001
            gait = person['previous_gait'] + (person['gait'] - person['previous_gait']) * t
            lean = .025 * math.sin(now * .7 + phase)
            gaze = .7 * math.sin(now * .5 + phase)
            head = (0., lean, 1.65)
            shoulders = ((-.22, 0., 1.32), (.22, 0., 1.32))
            hands = ((-.28, .10 * math.sin(now * 1.1 + phase), 1.),
                     (.28, .12 * math.cos(now * .9 + phase), 1.12))
            feet = ((-.16, 0., 0.), (.16, 0., 0.))
            if moving:
                stride = (.36 if person['running'] else .23) * math.sin(gait + phase)
                lean = .07 if person['running'] else .02
                hands = ((-.28, -stride, 1.), (.28, stride, 1.))
                feet = ((-.16, stride, 0.), (.16, -stride, 0.))
                head = (0., .02, 1.65)
                gaze = 0.
            elif person['state'] == CrewState.WORK and person['arrived']:
                if person['work_style'] == 'hammer':
                    head = (0., -.08, 1.68)
                    gaze = 0.
                    hands = ((-.18, .28, 1.72), (.22, .22, 1.65 + .22 * math.sin(now * 8 + phase)))
                else:
                    lean, head = .14, (.07 * math.sin(now + phase), .15, 1.60)
                    hands = ((-.2, .32, 1.5), (.22, .32 + .12 * math.sin(now * 2 + phase), 1.05))
            def at(right, ahead, height):
                return project((x - s * right + c * ahead, y + c * right + s * ahead), deck + height + .05)
            self.positions[person['id']] = project((x, y), deck + 1.)
            color = person['color']
            neck = (0., lean, 1.45)
            body = (((0., 0., .82), neck), (shoulders[0], shoulders[1]),
                    (shoulders[0], hands[0]), (shoulders[1], hands[1]),
                    ((0., 0., .85), feet[0]), ((0., 0., .85), feet[1]), (neck, head))
            for a, b in body:
                vertices.extend((at(*a), at(*b)))
                colors.extend((color, color))
            ring = [at(head[0] + .15 * math.cos(j * math.tau / 8),
                       head[1] + .15 * math.sin(j * math.tau / 8), head[2]) for j in range(8)]
            head_outline = ring
            for a, b in zip(ring, ring[1:] + ring[:1]):
                vertices.extend((a, b))
                colors.extend((color, color))
            # A second perpendicular ring gives the head volume from side views.
            ring = [at(head[0] + .15 * math.cos(j * math.tau / 8), head[1],
                       head[2] + .15 * math.sin(j * math.tau / 8)) for j in range(8)]
            self.heads[person['id']] = at(*head), head_outline + ring
            for a, b in zip(ring, ring[1:] + ring[:1]):
                vertices.extend((a, b))
                colors.extend((color, color))
            # A short face stroke makes looking around and looking up visible.
            looking_up = person['state'] == CrewState.WORK and person['arrived']
            face = (head[0] + .22 * math.sin(gaze), head[1] + .22 * math.cos(gaze),
                    head[2] + (.1 if looking_up else 0.))
            vertices.extend((at(*head), at(*face)))
            colors.extend((color, color))
        if vertices:
            self.lines.setData(pos=np.asarray(vertices, dtype=np.float32), color=np.asarray(colors, dtype=np.float32))
        self.lines.setVisible(bool(vertices))
        if self.selected_id not in self.positions:
            self.selected_id = None
            self.following = False
        self.labels.refresh()
        if self.following:
            self.view.apply_camera()
