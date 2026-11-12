"""Sweep-gated VTOL VR display data. Recordings keep their original telemetry."""
import math
from bisect import bisect_left

from wirejudge.map.map_trails import TRAIL_SECONDS, frame_time

from wirejudge.approach.approach_geometry import NM, case1_volume, point
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion

SEARCH_PERIOD = 5.
SEARCH_MAX_RANGE = 50 * NM
SEARCH_BANDS = ((2500, SEARCH_MAX_RANGE), (1500, 40 * NM), (900, 30 * NM), (200, 20 * NM))
DECK_UPDATE_RANGE = .5 * NM
PAR_PASS = .1
PAR_HALF_ANGLE = 12.5
PAR_RANGE = 12 * NM
EPSILON = 1e-8


def search_range(altitude_m):
    for altitude_ft, distance_m in SEARCH_BANDS:
        if altitude_m >= altitude_ft * .3048:
            return distance_m
    return 0.


def crossed(start, end, phase, period):
    """Whether a repeating event falls in the scan interval (start, end]."""
    return math.floor((end - phase + EPSILON) / period) > math.floor((start - phase + EPSILON) / period)


def search_bearing(seconds):
    return seconds % SEARCH_PERIOD * 360 / SEARCH_PERIOD


def par_offset(seconds):
    fraction = seconds % (2 * PAR_PASS) / PAR_PASS
    return PAR_HALF_ANGLE * (1 - 2 * abs(1 - fraction))


def par_hit(start, end, bearing):
    if abs(bearing) > PAR_HALF_ANGLE + EPSILON:
        return False
    outward = PAR_PASS * (bearing + PAR_HALF_ANGLE) / (2 * PAR_HALF_ANGLE)
    return (crossed(start, end, outward, 2 * PAR_PASS)
            or crossed(start, end, -outward, 2 * PAR_PASS))


def polar(origin, position):
    east = position[0] - origin[0]
    north = position[2] - origin[2]
    return math.hypot(east, north), math.degrees(math.atan2(east, north)) % 360


def translation(sample):
    return (sample.get('derived') or {}).get('world_translation_m') or (0., 0., 0.)


class RadarContacts:
    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None
        self.stamp = None
        self.origin_time = None
        self.contacts = {}
        self.history = []
        self.snapshot = None
        self.coverage = None
        self.next_age = math.inf
        self.sea_offset = None

    def update(self, sample, carrier_id, glide, runway_offset, playback_epoch, clock_origin=None, display_time=None):
        if sample is None:
            self.reset()
            return None
        capture = sample['capture']
        stamp = capture['monotonic_s']
        now = max(stamp, display_time) if display_time is not None else stamp
        carriers = sample['carriers']
        carrier = next((ship for ship in carriers if ship['instance_id'] == carrier_id),
                       max(carriers, key=lambda ship: len(ship.get('cables') or []), default=None))
        identifier = carrier['instance_id'] if carrier else None
        key = (capture['connection_id'], identifier, runway_offset, playback_epoch)
        if key != self.key or (self.stamp is not None and stamp < self.stamp):
            self.reset()
            self.key = key
            self.origin_time = stamp if clock_origin is None else clock_origin
        new_sample = stamp != self.stamp
        if not new_sample and self.snapshot is not None and now < self.next_age:
            return self.snapshot
        end = stamp - self.origin_time
        start = self.stamp - self.origin_time if self.stamp is not None else end - 2 * EPSILON
        self.stamp = stamp
        search_origin = carrier.get('position_world_m') if carrier else None
        volume = case1_volume(carrier, glide, runway_offset) if carrier else None
        heading = volume['runway_heading_deg'] if volume else None
        if heading is None and carrier:
            heading = heading_from_quaternion(carrier.get('rotation_xyzw'))
            if heading is not None:
                heading -= runway_offset
        par_origin = volume['origin'] if volume else search_origin
        par_bearing = (heading + 180) % 360 if heading is not None else None
        correction = translation(sample)
        if new_sample:
            for aircraft in sample['aircraft']:
                kinematics = aircraft.get('kinematics') or {}
                sea_level = kinematics.get('sea_level_world_y_m')
                position = kinematics.get('position_world_m')
                altitude = (kinematics.get('flight_info') or {}).get('altitudeASL')
                if isinstance(sea_level, (int, float)) and math.isfinite(sea_level):
                    self.sea_offset = -sea_level
                    break
                if point(position) and isinstance(altitude, (int, float)) and math.isfinite(altitude):
                    self.sea_offset = altitude - position[1]
                    break
        if point(search_origin):
            self.coverage = (search_origin, par_origin, par_bearing)
            for aircraft in sample['aircraft'] if new_sample else ():
                kinematics = aircraft.get('kinematics') or {}
                position = kinematics.get('position_world_m')
                if not point(position):
                    continue
                altitude = (kinematics.get('flight_info') or {}).get('altitudeASL')
                if not isinstance(altitude, (int, float)) or not math.isfinite(altitude):
                    altitude = position[1] + self.sea_offset if self.sea_offset is not None else 0.
                distance, bearing = polar(search_origin, position)
                hit = distance <= DECK_UPDATE_RANGE or (
                    distance <= search_range(altitude) and
                    crossed(start, end, bearing / 360 * SEARCH_PERIOD, SEARCH_PERIOD))
                if not hit and par_bearing is not None:
                    distance, bearing = polar(par_origin, position)
                    delta = (bearing - par_bearing + 180) % 360 - 180
                    hit = distance <= PAR_RANGE and par_hit(start, end, delta)
                if hit:
                    self.contacts[aircraft['instance_id']] = (stamp, aircraft, correction, carrier)
        else:
            self.coverage = None
            self.contacts.clear()
        display = []
        self.next_age = math.inf
        for identifier, (seen, aircraft, old_correction, observed_carrier) in list(self.contacts.items()):
            age = now - seen
            if age >= 2 * SEARCH_PERIOD:
                del self.contacts[identifier]
                continue
            stale = age >= SEARCH_PERIOD
            self.next_age = min(self.next_age, seen + SEARCH_PERIOD * (2 if stale else 1))
            kinematics = aircraft['kinematics']
            position = kinematics['position_world_m']
            # Held contacts survive floating-origin shifts without jumping.
            if old_correction != correction:
                position = [p + before - now for p, before, now in zip(position, old_correction, correction)]
            display.append(dict(aircraft, kinematics=dict(kinematics, position_world_m=position),
                                _radar_hit_s=seen, _radar_stale=stale,
                                _radar_observation=(aircraft, observed_carrier)))
        self.snapshot = dict(sample, aircraft=display, _larp_radar=True,
                             _radar_display_s=now, _radar_sea_offset=self.sea_offset)
        if new_sample:
            self.history.append(self.snapshot)
            del self.history[:bisect_left(self.history, stamp - TRAIL_SECONDS, key=frame_time)]
        return self.snapshot
