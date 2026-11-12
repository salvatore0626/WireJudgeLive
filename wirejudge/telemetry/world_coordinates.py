"""Detect shared floating-origin translations using only native game observations."""
import math
from statistics import median


def observations(sample):
    result = {}
    # Ships are stable references. Do not combine untrusted remote control/sensor values.
    for c in sample['carriers']:
        p, v = c.get('position_world_m'), c.get('velocity_world_mps')
        if isinstance(p, list) and len(p) == 3 and isinstance(v, list) and len(v) == 3:
            if all(isinstance(x, (float, int)) and math.isfinite(x) for x in p + v):
                result[c['instance_id']] = (p, v)
    return result


class WorldCoordinates:
    def __init__(self):
        self.reset()

    def reset(self):
        self.offset = [0.0, 0.0, 0.0]
        self.previous = {}
        self.previous_time = None
        self.shifts = 0

    def update(self, sample):
        now = sample['source'].get('mission_time_s')
        current = observations(sample)
        shift = None
        supported = 0
        dt = now - self.previous_time if isinstance(now, (int, float)) and self.previous_time is not None else None
        if dt is not None and 0 <= dt <= 2:
            residuals = []
            for key, (p, v) in current.items():
                if key not in self.previous:
                    continue
                old, old_velocity = self.previous[key]
                residuals.append([p[i] - old[i] - (old_velocity[i] + v[i]) * .5 * dt for i in range(3)])
            if len(residuals) >= 2:
                common = [median(r[i] for r in residuals) for i in range(3)]
                # A large shared displacement, inconsistent with physical velocity,
                # must be supported by at least two ships. Ordinary ship movement is untouched.
                agreement = [r for r in residuals if math.dist(r, common) <= 2]
                if math.dist(common, (0, 0, 0)) >= 100 and len(agreement) >= 2 and len(agreement) >= len(residuals) * .75:
                    shift = [median(r[i] for r in agreement) for i in range(3)]
                    # Game translations are often whole metres; snap only within 15 cm
                    # to avoid accumulating tiny velocity-estimation noise at every rebase.
                    shift = [float(round(x)) if abs(x - round(x)) <= .15 else x for x in shift]
                    self.offset = [self.offset[i] - shift[i] for i in range(3)]
                    self.shifts += 1
                    supported = len(agreement)
        self.previous = current
        self.previous_time = now if isinstance(now, (int, float)) and math.isfinite(now) else None
        sample['derived'] = {'world_translation_m': self.offset.copy(),
            'origin_shift_m': shift, 'origin_shift_supporting_ships': supported,
            'origin_shift_count': self.shifts}
