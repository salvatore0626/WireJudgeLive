"""Online Case 1/3 detection from native game geometry; no look-ahead or Qt work.

Point columns: receive time, aft distance NM, GS error deg, LOC error deg, AoA deg.
The capture owner holds its lock when calling this model.
"""
import math
import uuid
from wirejudge.approach.approach_geometry import case1_volume, point, NM, inside_ils
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion
from wirejudge.aircraft.pilot_identity import pilot_identity

ENTRY_HEADING_LIMIT_DEG = 60.0  # Half of the 120-degree cone toward the carrier.


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def aircraft_heading(aircraft, flight_info):
    heading = flight_info.get('heading')
    if number(heading):
        return heading
    return heading_from_quaternion((aircraft.get('kinematics') or {}).get('rotation_xyzw'))


def heading_error(heading, target):
    return abs((heading - target + 180) % 360 - 180)


def landing_course_position(position, volume):
    if not point(position):
        return None
    origin = volume['origin']
    heading = math.radians(volume['runway_heading_deg'])
    x, y, z = (position[i] - origin[i] for i in range(3))
    aft = -x*math.sin(heading)-z*math.cos(heading)
    left = -x*math.cos(heading)+z*math.sin(heading)
    return aft, left, y


def measurement(aircraft, volume, glide):
    k = aircraft.get('kinematics') or {}
    position = landing_course_position(k.get('position_world_m'), volume)
    if position is None:
        return None
    aft, left, y = position
    gs = math.degrees(math.atan2(y, aft))-glide if aft > 0 else None
    loc = math.degrees(math.atan2(left, aft)) if aft > 0 else None
    f = k.get('flight_info') or {}
    aoa = f.get('aoa')
    return {'aft':aft, 'left':left, 'height':y, 'gs':gs, 'loc':loc,
            'aoa':aoa if number(aoa) else None}


class ApproachDetector:
    def __init__(self):
        self.entry_nm = {'Case 1': 1., 'Case 3': 8.6}
        self.case = 'Case 1'
        self.glide = 3.5
        self.offset = 10.
        self.attempts = {}
        self.active = {}
        self.blocked = set()
        self.revision = 0
        self.session = None
        self.last_clock = None
        self.session_start = None
        self.cables = {}

    def configure(self, case, glide, offset):
        # Detection geometry is preserved as provenance; scoring rules remain editable.
        self.case, self.glide, self.offset = case, float(glide), float(offset)

    def reset(self):
        self.attempts.clear(); self.active.clear(); self.blocked.clear()
        self.session = self.last_clock = None
        self.session_start = None
        self.cables.clear()
        self.revision += 1

    @staticmethod
    def metadata(attempt):
        return {k:v for k,v in attempt.items() if k not in ('points', '_abort_since', '_near','_catch_clock')}

    def finish(self, attempt, outcome, when):
        if attempt.get('wire_catch_s') is not None and outcome in ('Interrupted','Track lost','Geometry unavailable'):
            outcome=f"Wire {attempt['wire']}"
            attempt['post_catch_truncated']=True
        attempt['outcome'], attempt['end_s'] = outcome, when
        self.active.pop(attempt['aircraft_id'], None)
        self.blocked.add(attempt['aircraft_id'])
        self.revision += 1
        return {'attempt':self.metadata(attempt)}

    def stop(self):
        return [self.finish(a, 'Interrupted', a['last_s']) for a in list(self.active.values())]

    def process(self, sample, recent_scoring=None):
        rx, source = sample['capture'], sample.get('source') or {}
        when = rx['monotonic_s']
        clock = next((source.get(k) for k in ('mission_time_s', 'real_time_s')
                      if number(source.get(k))), when)
        session = rx['connection_id']
        events = []
        if self.session is not None and (self.session != session or
                (self.last_clock is not None and clock < self.last_clock)):
            events.extend(self.stop()); self.blocked.clear()
            self.session_start = None
            self.cables.clear()
        if self.session_start is None:
            self.session_start = when
        gap = self.last_clock is not None and clock-self.last_clock > 2.
        self.session, self.last_clock = session, clock
        carriers = {c['instance_id']:c for c in sample.get('carriers', [])}
        # Compute once per ship per incoming frame, rather than once per aircraft.
        volumes = {key:case1_volume(c, self.glide, self.offset) for key,c in carriers.items()}
        wire_carriers = {wire: ship for ship, volume in volumes.items() if volume
                         for wire in volume['wire_ids']}
        aircrafts = {a['instance_id']:a for a in sample.get('aircraft', [])}
        for key, active in list(self.active.items()):
            if key not in aircrafts:
                events.append(self.finish(active, 'Track lost', active['last_s']))
        for key, aircraft in aircrafts.items():
            active = self.active.get(key)
            f = (aircraft.get('kinematics') or {}).get('flight_info') or {}
            cable = (aircraft.get('landing') or {}).get('current_cable_id')
            caught_ship = wire_carriers.get(cable) if cable != self.cables.get(key) else None
            if cable is None:
                self.cables.pop(key, None)
            else:
                self.cables[key] = cable
            if active is None:
                backfill = ()
                candidates = []
                for ship, volume in volumes.items():
                    m = measurement(aircraft, volume, self.glide) if volume else None
                    gate = (inside_ils(m['gs'],m['loc']) if self.case == 'Case 1' else abs(m['loc'] or 0)<=10) if m else False
                    if m and 0 < m['aft'] <= self.entry_nm[self.case]*NM and gate:
                        candidates.append((m['aft'], ship, volume, m))
                case, start = self.case, when
                if caught_ship is not None:
                    ship, volume = caught_ship, volumes[caught_ship]
                    m = measurement(aircraft, volume, self.glide)
                    if m is None:
                        continue
                    case, start = 'Case 1', max(self.session_start, when - 60.)
                    if recent_scoring is not None:
                        backfill = [[row[0], row[2], row[4] - self.glide if row[4] is not None else None,
                                     row[5], row[6]] for row in recent_scoring(session, key, ship, start)]
                else:
                    if not candidates:
                        self.blocked.discard(key)
                        continue
                    if key in self.blocked or f.get('isLanded') is True or cable in wire_carriers:
                        continue
                    heading = aircraft_heading(aircraft, f)
                    if not number(heading):
                        continue
                    candidates = [
                        (distance, ship, volume, m) for distance, ship, volume, m in candidates
                        if heading_error(heading, volume['runway_heading_deg'] + m['loc'])
                        <= ENTRY_HEADING_LIMIT_DEG + 1e-9
                    ]
                    if not candidates:
                        continue
                    _, ship, volume, m = min(candidates, key=lambda c:c[0])
                identity = aircraft.get('identity') or {}
                pilot, airframe = pilot_identity(identity, key)
                unix = rx.get('unix_s')
                active = {'id':uuid.uuid4().hex, 'aircraft_id':key, 'carrier_id':ship,
                    'pilot':pilot,
                    'airframe':airframe, 'identity':dict(identity),
                    'carrier':carriers[ship].get('airport_name') or carriers[ship].get('actor_name') or str(ship),
                    'source':{k:source.get(k) for k in ('process_id', 'epoch')},
                    'connection_id':session, 'start_unix_s':unix - (when - start) if unix is not None else None,
                    'last_unix_s':unix, 'start_s':start, 'last_s':when, 'end_s':None,
                    'outcome':'Active', 'case':case, 'wire':None, 'glide_deg':self.glide,
                    'offset_deg':self.offset, 'points':list(backfill), '_abort_since':None, '_near':False,
                    'wire_catch_s':None,'_catch_clock':None}
                if caught_ship is not None:
                    active['gate_missed'] = True
                self.active[key] = active
                self.attempts[active['id']] = active
            else:
                backfill = ()
                ship = carriers.get(active['carrier_id'])
                volume = case1_volume(ship, active['glide_deg'], active['offset_deg']) if ship else None
                m = measurement(aircraft, volume, active['glide_deg']) if volume else None
                if m is None:
                    events.append(self.finish(active, 'Geometry unavailable', active['last_s']))
                    continue
            active['last_s'] = when
            active['last_unix_s']=rx.get('unix_s')
            p = [when, m['aft']/NM, m['gs'], m['loc'], m['aoa']]
            active['points'].append(p)
            # Near deck: within 150 m horizontally of wire 3 and 50 m vertically.
            if math.hypot(m['aft'], m['left']) <= 150 and abs(m['height']) <= 50:
                active['_near'] = True
            outcome = None
            if active['_catch_clock'] is None and cable is not None and cable in volume['wire_ids']:
                active['wire'] = volume['wire_ids'].index(cable)+1
                active['wire_catch_s']=when
                active['_catch_clock']=clock
            if active['_catch_clock'] is not None:
                elapsed = (when - active['wire_catch_s'] if active.get('gate_missed')
                           else clock - active['_catch_clock'])
                if elapsed >= 3.:
                    outcome = f"Wire {active['wire']}"
            elif active['_near'] and m['aft'] <= -150:
                outcome = 'Bolter'
            else:
                heading = aircraft_heading(aircraft, f)
                off = heading_error(heading, volume['runway_heading_deg']) if number(heading) else None
                if gap or off is None or off <= 25:
                    active['_abort_since'] = None
                elif active['_abort_since'] is None:
                    active['_abort_since'] = clock
                elif clock-active['_abort_since'] > 5:
                    outcome = 'Aborted'
            if outcome:
                event = self.finish(active, outcome, when)
            else:
                event = {'attempt':self.metadata(active)}
                self.revision += 1
            event['point'] = p
            if backfill:
                event['backfill_points'] = backfill
            events.append(event)
        return events

    def catalog(self):
        return self.revision, [self.metadata(a) for a in self.attempts.values()]

    def points(self, attempt_id):
        a = self.attempts.get(attempt_id)
        return list(a['points']) if a else []
