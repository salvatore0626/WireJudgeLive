"""DVR viewing clock and coalesced, read-only asynchronous SQLite seeks."""
import json
from collections import deque
from contextlib import closing
from pathlib import Path
import queue
import sqlite3
import threading
import time


class PlaybackHistory:
    """Reuse decoded snapshots while the playback cursor advances."""

    def __init__(self):
        self.frames = deque()
        self.bytes = 0
        self.key = None
        self.start = None
        self.last = None

    def read(self, db, row, history_s, key, stopped):
        stamp, row_id, connection = row
        start = stamp - history_s
        advancing = (self.key == key and self.last is not None
                     and start >= self.start and start <= self.last[0]
                     and (stamp, row_id) >= self.last)
        if not advancing:
            self.frames.clear()
            self.bytes = 0
        self.key, self.start = key, start
        while self.frames and self.frames[0][0] < start:
            self.bytes -= self.frames.popleft()[1]

        lower = self.last if advancing else (start, -1)
        fresh = []
        size = 0
        for frame_time, raw in db.execute(
                'SELECT received_monotonic_s,payload_json FROM samples INDEXED BY samples_time '
                'WHERE (received_monotonic_s,id)>(?,?) AND (received_monotonic_s,id)<=(?,?) '
                'AND connection_id=? ORDER BY received_monotonic_s DESC,id DESC LIMIT 5000',
                (*lower, stamp, row_id, connection)):
            if stopped():
                break
            frame_size = len(raw.encode('utf-8'))
            size += frame_size
            if size > 64 * 1024 * 1024:
                self.frames.clear()
                self.bytes = 0
                break
            fresh.append((frame_time, frame_size, json.loads(raw)))
        self.frames.extend(reversed(fresh))
        self.bytes += sum(frame[1] for frame in fresh)
        while len(self.frames) > 5000 or self.bytes > 64 * 1024 * 1024:
            self.bytes -= self.frames.popleft()[1]
        self.last = (stamp, row_id)
        history = [frame[2] for frame in self.frames]
        if history:
            return history[-1], history
        # A single unusually large snapshot can exceed the history budget.
        raw, = db.execute('SELECT payload_json FROM samples WHERE id=?', (row_id,)).fetchone()
        return json.loads(raw), history


class PlaybackClock:
    def __init__(self):
        self.start = None
        self.end = None
        self.cursor = None
        self.state = 'live'
        self.last_tick = time.monotonic()

    def reset(self):
        self.__init__()

    def bounds(self, start, end):
        self.start, self.end = start, end
        if self.cursor is None or self.state == 'live':
            self.cursor = end
        else:
            self.cursor = max(start, min(end, self.cursor))

    def pause(self):
        if self.cursor is not None:
            self.state = 'paused'
        self.last_tick = time.monotonic()

    def seek(self, value, *, keep_playing=False):
        if self.start is not None:
            self.cursor = max(self.start, min(self.end, value))
            if not keep_playing or self.state != 'playing':
                self.state = 'paused'
        self.last_tick = time.monotonic()

    def play(self):
        if self.cursor is not None:
            self.state = 'playing'
        self.last_tick = time.monotonic()

    def live(self):
        self.state = 'live'
        self.cursor = self.end
        self.last_tick = time.monotonic()

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        dt = max(0., now-self.last_tick)
        self.last_tick = now
        if self.state == 'playing' and self.cursor is not None:
            self.cursor = min(self.end, self.cursor+dt)
            if self.cursor >= self.end:
                self.state = 'live'
        elif self.state == 'live':
            self.cursor = self.end


class DVRReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.condition = threading.Condition()
        self.pending = None
        self.stopped = False
        self.results = queue.Queue(maxsize=1)

    def request(self, path, target=None, history_s=30, epoch=0):
        with self.condition:
            if not self.stopped:
                self.pending = (str(path), target, history_s, epoch)
                self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.pending = None
            self.condition.notify_all()

    def publish(self, result):
        try:
            self.results.put_nowait(result)
        except queue.Full:
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass
            self.results.put_nowait(result)

    def run(self):
        history = PlaybackHistory()
        while True:
            with self.condition:
                while self.pending is None and not self.stopped:
                    self.condition.wait()
                if self.stopped:
                    return
                path, target, history_s, epoch = self.pending
                self.pending = None
            result = {'path':path, 'epoch':epoch, 'target':target, 'error':None}
            try:
                # Close after every request so an idle reader cannot block deletion on Windows.
                with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',
                                             uri=True, timeout=.25)) as db:
                    db.execute('BEGIN')
                    first = db.execute('SELECT received_monotonic_s FROM samples ORDER BY received_monotonic_s LIMIT 1').fetchone()
                    last = db.execute('SELECT received_monotonic_s FROM samples ORDER BY received_monotonic_s DESC LIMIT 1').fetchone()
                    result['bounds'] = (first[0], last[0]) if first and last else None
                    if target is not None and first:
                        row = db.execute('SELECT received_monotonic_s,id,connection_id FROM samples '
                            'WHERE received_monotonic_s<=? ORDER BY received_monotonic_s DESC,id DESC LIMIT 1',
                            (max(first[0], target),)).fetchone()
                        if row:
                            result['sample'], result['history'] = history.read(
                                db, row, history_s, (path, epoch, row[2]), lambda: self.stopped)
            except Exception as exc:
                history = PlaybackHistory()
                result['error'] = str(exc)
            if self.stopped:
                return
            self.publish(result)
