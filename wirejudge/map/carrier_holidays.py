"""Local-calendar decorations and batched particle effects for the 3D carrier."""
from datetime import date
from functools import lru_cache
import math
import time

import numpy as np

from wirejudge.map.carrier_wireframe import deck_geometry, local_point
from wirejudge.map.map_rendering import MAP_LINE_OPTIONS, MapLines

FIREWORK_COLORS = ((1., .14, .23), (1., .98, .93), (.18, .48, 1.))
GOLD_FIREWORK_COLORS = ((1., .64, .10), (1., .82, .30), (1., .94, .66))
LIGHT_COLORS = ((1., .19, .23, 1.), (.20, .95, .40, 1.), (1., .76, .20, 1.), (.25, .63, 1., 1.))
SPARK_STROKES = np.array(((-1, 0, 0), (1, 0, 0), (0, 0, -1), (0, 0, 1), (0, -1, 0), (0, 1, 0)))
BURST_HEIGHT_M = (650 * .3048, 750 * .3048)
MAX_LAUNCH_TILT_DEG = 20


def holiday_dates(day):
    decoration = None
    if day.month == 12 and 15 <= day.day <= 28:
        decoration = 'christmas'
    elif day.month == 10 and 15 <= day.day <= 31:
        decoration = 'halloween'
    return day.month == 7 and 3 <= day.day <= 5, decoration


@lru_cache(maxsize=1)
def christmas_geometry():
    points, colors = [], []

    def stroke(a, b, color):
        points.extend((a, b))
        colors.extend((color, color))

    _, outline, _, _ = deck_geometry()
    ring = [(math.cos(i * math.tau / 8), math.sin(i * math.tau / 8)) for i in range(8)]
    bulb_count = 0
    for a, b in zip(outline, outline[1:] + outline[:1]):
        length = math.dist(a, b)
        if not length:
            continue
        count = max(1, round(length / 6))
        bays = max(1, round(length / 24))
        dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for i in range(count):
            t = i / count
            x, y = (a[j] + t * (b[j] - a[j]) for j in range(2))
            z = -.7 - .8 * math.sin(math.pi * ((t * bays) % 1))
            color = LIGHT_COLORS[bulb_count % len(LIGHT_COLORS)]
            # Two small round profiles keep the bulb readable from above and alongside.
            for profile in (
                [(x + .4 * u, y + .4 * v, z) for u, v in ring],
                [(x + .4 * u * dx, y + .4 * u * dy, z + .4 * v) for u, v in ring],
            ):
                for start, end in zip(profile, profile[1:] + profile[:1]):
                    stroke(start, end, color)
            bulb_count += 1

    x, y = local_point(453, 887)
    front, height = y + 12.7, 13.
    green = (.20, .86, .39, 1.)
    for i in range(36):
        a, b = i * math.tau / 36, (i + 1) * math.tau / 36
        for radius in (2.5, 3., 3.5):
            stroke((x + radius * math.cos(a), front, height + radius * math.sin(a)),
                   (x + radius * math.cos(b), front, height + radius * math.sin(b)),
                   green if i % 6 else LIGHT_COLORS[0])
    bow = ((0, -2.8), (-1.5, -2.), (-1.5, -3.6), (0, -2.8),
           (1.5, -2.), (1.5, -3.6), (0, -2.8), (-.75, -4.3))
    for a, b in zip(bow, bow[1:]):
        stroke((x + a[0], front, height + a[1]), (x + b[0], front, height + b[1]), LIGHT_COLORS[0])
    stroke((x, front, height - 2.8), (x + .75, front, height - 4.3), LIGHT_COLORS[0])
    phases = np.repeat(np.arange(bulb_count) * math.pi * (3 - math.sqrt(5)), 4 * len(ring))
    return points, np.asarray(colors, dtype=np.float32), phases


def christmas_colors(now):
    _, base, phases = christmas_geometry()
    colors = base.copy()
    colors[:len(phases), 3] *= .72 + .28 * (.5 + .5 * np.sin(now * 3 + phases))**4
    return colors


@lru_cache(maxsize=1)
def halloween_geometry():
    x, y = local_point(453, 887)
    front, height = y + 12.7, 13.
    points, colors = [], []

    def path(vertices, color, offset=0.):
        for a, b in zip(vertices, vertices[1:]):
            points.extend((x + u, front + offset, height + v) for u, v in (a, b))
            colors.extend((color, color))

    orange = (1., .38, .08, 1.)
    glow = (1., .82, .20, 1.)
    outline = ((0, 2.3), (1.5, 2.6), (2.7, 2), (3.4, .9), (3.5, -.6),
               (2.8, -1.8), (1.5, -2.4), (0, -2.2))
    path(outline + tuple((-u, v) for u, v in reversed(outline[:-1])), orange)
    path(((-.45, 2.4), (-.32, 3.5), (.45, 3.8), (.68, 3.4), (.32, 2.45)),
         (.33, .75, .24, 1.))
    for side in (-1, 1):
        eye = ((.65, .45), (1.45, 1.6), (2.25, .45), (.65, .45))
        path(tuple((side * u, v) for u, v in eye), glow, .15)
    path(((-.38, -.35), (0, .38), (.38, -.35), (-.38, -.35)), glow, .15)
    path(((-2.35, -.6), (-1.3, -1.), (-.9, -1.), (-.9, -.7), (-.35, -.7),
          (-.35, -1.15), (.65, -1.15), (.65, -.75), (1.16, -.75), (1.16, -1.05),
          (2.35, -.6), (1.55, -1.65), (0, -1.96), (-1.55, -1.65), (-2.35, -.6)), glow, .15)
    return points, np.asarray(colors, dtype=np.float32)


class CarrierHolidays:
    def __init__(self, view):
        self.view = view
        self.frame = None
        self.decorations = self.make_lines()
        self.sparks = self.make_lines()
        self.rng = np.random.default_rng()
        self.bursts = []
        self.next_launch = None
        self.decoration_override = None
        self.calendar_decoration = None
        self.july = False
        self.next_date_check = 0.
        self.next_twinkle = 0.
        self.update_date(time.monotonic())

    def make_lines(self):
        item = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
        item.setDepthValue(107)
        item.setVisible(False)
        self.view.addItem(item)
        return item

    def update_date(self, now):
        self.july, self.calendar_decoration = holiday_dates(date.today())
        if not self.july:
            self.next_launch = None
        self.next_date_check = now + 1.

    def set_frame(self, frame):
        old_carrier = self.frame[0] if self.frame else None
        self.frame = frame
        if frame is None or frame[0] != old_carrier:
            self.bursts.clear()
            self.sparks.hide()
            self.next_launch = None
        self.update_decorations()

    def toggle_decoration(self, decoration):
        self.update_date(time.monotonic())
        self.decoration_override = 'off' if self.decoration() == decoration else decoration
        self.update_decorations()
        self.view.update()

    def decoration(self):
        return self.calendar_decoration if self.decoration_override is None else self.decoration_override

    def update_decorations(self):
        decoration = self.decoration()
        visible = self.frame is not None and self.view.is_3d and decoration in ('christmas', 'halloween')
        if visible:
            _, project, deck_z = self.frame
            if decoration == 'christmas':
                points, _, _ = christmas_geometry()
                colors = christmas_colors(time.monotonic())
            else:
                points, colors = halloween_geometry()
            self.decorations.setData(pos=np.asarray([project((x, y), deck_z + z) for x, y, z in points],
                                                    dtype=np.float32), color=colors)
        self.decorations.setVisible(visible)

    def launch(self, now=None):
        if self.frame is None or not self.view.is_3d:
            return
        now = time.monotonic() if now is None else now
        holiday = [burst for burst in self.bursts if burst[3] in FIREWORK_COLORS]
        self.bursts = [burst for burst in self.bursts if burst[3] not in FIREWORK_COLORS] + holiday[-9:]
        for i, color in enumerate(FIREWORK_COLORS):
            self.launch_shell(now + .25 * i, i, color)

    def celebrate_record(self, carrier_id, caw_record):
        if self.frame is None or not self.view.is_3d or self.frame[0][1] != carrier_id:
            return
        now = time.monotonic()
        for index in range(30 if caw_record else 3):
            slot = index % len(GOLD_FIREWORK_COLORS)
            self.launch_shell(now + index / 3, slot, GOLD_FIREWORK_COLORS[slot])
        self.view.update()

    def launch_shell(self, start, slot, color):
        bearing = self.rng.uniform(0, math.tau)
        tilt = math.radians(self.rng.uniform(0, MAX_LAUNCH_TILT_DEG))
        origin = np.array((14., 95. - 20 * slot, 2.))
        rise = self.rng.uniform(*BURST_HEIGHT_M) - origin[2]
        drift = rise * math.tan(tilt)
        ascent = np.array((drift * math.cos(bearing), drift * math.sin(bearing), rise))
        vectors = self.rng.normal(size=(84, 3))
        vectors /= np.linalg.norm(vectors, axis=1)[:, None]
        velocities = vectors * self.rng.uniform(17, 30, size=(84, 1))
        phases = self.rng.uniform(0, math.tau, 84)
        self.bursts.append((start, origin, ascent, color, velocities, phases))

    def tick(self, now):
        if now >= self.next_date_check:
            previous = self.decoration()
            self.update_date(now)
            if previous != self.decoration():
                self.update_decorations()
                self.view.update()
        if self.frame is None or not self.view.is_3d:
            return
        if self.decoration() == 'christmas' and self.decorations.visible() and now >= self.next_twinkle:
            self.next_twinkle = now + .05
            self.decorations.setData(color=christmas_colors(now))
            self.view.update()
        if self.july:
            if self.next_launch is None:
                self.next_launch = now + self.rng.uniform(60, 120)
            elif now >= self.next_launch:
                self.launch(now)
                self.next_launch = now + self.rng.uniform(60, 120)
        if not self.bursts:
            return
        self.bursts = [burst for burst in self.bursts if now - burst[0] < 4.4]
        vertices, colors = [], []
        for start, origin, ascent, color, velocities, phases in self.bursts:
            age = now - start
            if age < 0:
                continue
            if age < 1.6:
                fraction = age / 1.6
                tip = origin + ascent * (1.35 * fraction - .35 * fraction**2)
                vertices.append(np.array((tip - ascent * .055, tip)))
                colors.append(np.array(((*color, .05), (*color, 1.))))
                continue
            age -= 1.6
            centers = origin + ascent + velocities * age
            centers[:, 2] -= 5. * age**2
            pulse = .5 + .5 * np.sin(phases + age * 23)
            gold = color in GOLD_FIREWORK_COLORS
            glitter = pulse**10 if gold else None
            twinkle = .14 + .86 * glitter if gold else .35 + .65 * pulse**2
            alpha = twinkle * (1 - age / 2.8)**.8
            strokes = centers[:, None, :] + SPARK_STROKES * (1.3 + twinkle[:, None, None])
            vertices.append(strokes.reshape(-1, 3))
            rgb = np.asarray(color) + (1. - np.asarray(color)) * glitter[:, None] * .75 if gold else np.broadcast_to(color, (len(alpha), 3))
            colors.append(np.repeat(np.column_stack((rgb, alpha)),
                                    len(SPARK_STROKES), axis=0))
        if vertices:
            _, project, deck_z = self.frame
            origin = np.asarray(project((0., 0.), deck_z))
            right = np.asarray(project((1., 0.), deck_z)) - origin
            forward = np.asarray(project((0., 1.), deck_z)) - origin
            basis = np.array((right, forward, (0., 0., 1.)))
            pos = np.concatenate(vertices) @ basis + origin
            self.sparks.setData(pos=pos.astype(np.float32),
                                color=np.concatenate(colors).astype(np.float32))
        self.sparks.setVisible(bool(vertices))
        self.view.update()
