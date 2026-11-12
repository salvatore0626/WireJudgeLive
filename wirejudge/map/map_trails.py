"""Incremental map geometry for the visible portion of a telemetry track."""
from bisect import bisect_left, bisect_right
from collections import deque
import math

import numpy as np


TRAIL_SECONDS = 30.
TELEPORT_DISTANCE_M = 5 * 1852


def frame_time(frame):
    return frame['capture']['monotonic_s']


class MapTrails:
    def __init__(self):
        self.clear()

    def clear(self):
        self.key = None
        self.last = None
        self.oldest = None
        self.previous = {}
        self.batches = deque()

    def update(self, history, timestamp, session, key, transform_for, color_for):
        """Return new line arrays, or None when the existing geometry still applies."""
        first = bisect_left(history, timestamp - TRAIL_SECONDS, key=frame_time)
        end = bisect_right(history, timestamp, lo=first, key=frame_time)
        oldest = frame_time(history[first]) if first < end else None
        start = first
        if self.key == key and self.last is not None and oldest is not None and oldest >= self.oldest:
            resume = bisect_left(history, self.last, lo=first, hi=end, key=frame_time)
            if resume < end and frame_time(history[resume]) == self.last:
                start = resume + 1
        reset = start == first
        if reset:
            if oldest is None and self.last is None and self.key == key:
                return None
            self.clear()
            self.key = key
        elif start == end and self.oldest == oldest:
            return None

        self.oldest = oldest
        for index in range(start, end):
            frame = history[index]
            if frame['capture']['connection_id'] == session:
                self.append(frame, transform_for(frame), color_for)
            else:
                self.previous.clear()
            self.last = frame_time(frame)

        while self.batches and self.batches[0][0] < oldest:
            self.batches.popleft()
        if not self.batches:
            return np.empty((0, 3), dtype=np.float32), np.empty((0, 4), dtype=np.float32)

        positions = np.concatenate([batch[1] for batch in self.batches])
        colors = np.concatenate([batch[2] for batch in self.batches])
        times = np.concatenate([batch[3] for batch in self.batches])
        # The oldest visible points fade out as the window rolls forward.
        np.minimum(colors[:, 3], .5 * np.clip((times - oldest) / 3., 0., 1.), out=colors[:, 3])
        return positions, colors

    def append(self, frame, transform, color_for):
        stamp = frame_time(frame)
        current = {}
        positions, colors, times = [], [], []
        for aircraft in frame['aircraft']:
            instance = aircraft['instance_id']
            k = aircraft['kinematics']
            altitude = (k.get('flight_info') or {}).get('altitudeASL')
            if frame.get('_larp_radar'):
                position = transform(k.get('position_world_m'), altitude, aircraft)
            else:
                position = transform(k.get('position_world_m'), altitude)
            if position is None:
                continue
            start = stamp
            if instance in self.previous:
                old, old_time, old_start = self.previous[instance]
                if frame.get('_larp_radar') and position == old:
                    current[instance] = (position, old_time, old_start)
                    continue
                if math.dist(old, position) < TELEPORT_DISTANCE_M:
                    start = old_start
                    rgb = color_for(aircraft, frame)[:3]
                    positions.extend((old, position))
                    colors.extend((*rgb, .5 * min(1., (t - start) / 3.)) for t in (old_time, stamp))
                    times.extend((old_time, stamp))
            current[instance] = (position, stamp, start)
        if positions:
            self.batches.append((self.last, np.asarray(positions, dtype=np.float32),
                                 np.asarray(colors, dtype=np.float32), np.asarray(times)))
        self.previous = current
