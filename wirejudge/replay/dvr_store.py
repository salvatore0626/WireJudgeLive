"""Single-owner asynchronous SQLite writer. No disk work runs on the GUI thread."""
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from wirejudge.replay.replay_files import delete_replay, landing_count, MIN_AUTO_REPLAY_SECONDS


class DVRWriter(threading.Thread):
    def __init__(self, folder, report, pre_roll=(), session_id=None, discard_incomplete=False):
        super().__init__(daemon=True)
        self.folder = Path(folder).expanduser()
        self.report = report
        self.pre_roll = iter(pre_roll)
        self.pre_roll_done = False
        self.queue = queue.Queue(maxsize=2048)
        self.done = threading.Event()
        self.path = None
        self.failed = None
        self.written = 0
        self.recording_id=uuid.uuid4().hex
        self.session_id=session_id
        self.discard_incomplete = discard_incomplete
        self.first_monotonic_s=None
        self.first_unix_s=None
        self.last_unix_s=None

    def submit(self, sample):
        if self.failed or self.done.is_set():
            return False
        try:
            self.queue.put_nowait(sample)
            return True
        except queue.Full:
            self.failed = 'DVR disk queue is full; recording stopped. Live capture continues.'
            self.done.set()
            self.report('dvr_error', message=self.failed)
            return False

    def flush(self):
        barrier=threading.Event()
        if self.done.is_set():
            barrier.set()
        elif not self.submit({'flush_request':barrier}):barrier.set()
        return barrier

    def close(self):
        self.done.set()

    def run(self):
        db = None
        discard_reason = None
        has_aircraft = False
        last_monotonic_s = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
            self.path = self.folder / f'WireJudge_{stamp}.wjlive.sqlite'
            db = sqlite3.connect(self.path)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [
                ('schema_version', '5'), ('recording_id',self.recording_id), ('session_id',str(self.session_id or '')), ('format', 'WireJudgeLive direct-game telemetry'),
                ('native_units', '{"distance":"m","altitude":"m","speed":"m/s"}'),
                ('sampling', 'Full incoming snapshot rate; adaptive downsampling not enabled'),
                ('derived_fields', 'carriers[].derived.heading_deg, root derived.world_translation_m/origin_shift_* and root approach_events are app-calculated; game fields native'),
                ('created_utc', datetime.now(timezone.utc).isoformat()),
                ('automatic_recording', str(int(self.discard_incomplete)))])
            db.execute('''CREATE TABLE samples(id INTEGER PRIMARY KEY, received_unix_s REAL,
                       received_monotonic_s REAL, connection_id TEXT, source_time_s REAL,
                       mission_time_s REAL, seq INTEGER, payload_json TEXT NOT NULL)''')
            db.execute('CREATE INDEX samples_time ON samples(received_monotonic_s)')
            db.execute('CREATE INDEX samples_source ON samples(connection_id, source_time_s)')
            db.execute('''CREATE TABLE approaches(id TEXT PRIMARY KEY, start_s REAL,
                       end_s REAL, outcome TEXT, metadata_json TEXT NOT NULL)''')
            db.execute('''CREATE TABLE approach_points(attempt_id TEXT NOT NULL,
                       received_monotonic_s REAL, distance_nm REAL, glideslope_deg REAL,
                       localizer_deg REAL, aoa_deg REAL)''')
            db.execute('CREATE INDEX approach_points_attempt ON approach_points(attempt_id, received_monotonic_s)')
            db.execute('''CREATE TABLE attempt_edits(id TEXT PRIMARY KEY, start_s REAL,
                       end_s REAL, metadata_json TEXT NOT NULL, points_json TEXT NOT NULL)''')
            db.execute('INSERT INTO metadata VALUES (?,?)', ('approach_detection',
                'App-calculated Case 1/3; native wire 3 reference; configurable entry distance; Case 1 ILS envelope, Case 3 +/-10 deg localizer; heading error >25 deg continuously >5 source seconds aborts; wire catches retain 3 source seconds before attempt end'))
            db.commit()
            self.report('dvr_started', path=str(self.path),recording_id=self.recording_id)
            last_commit = time.monotonic()
            while not self.pre_roll_done or not self.done.is_set() or not self.queue.empty():
                batch = []
                if not self.pre_roll_done:
                    for _ in range(128):
                        try:
                            batch.append(next(self.pre_roll))
                        except StopIteration:
                            self.pre_roll_done = True
                            break
                else:
                    try:
                        sample = self.queue.get(timeout=.1)
                        batch.append(sample)
                        for _ in range(127):
                            try:
                                batch.append(self.queue.get_nowait())
                            except queue.Empty:
                                break
                    except queue.Empty:
                        pass
                if batch:
                    rows = []
                    barriers=[]
                    for sample in batch:
                        if 'flush_request' in sample:
                            barriers.append(sample['flush_request']);continue
                        for edit in sample.get('attempt_edits',[]):
                            a=edit['metadata']
                            db.execute('INSERT OR REPLACE INTO attempt_edits VALUES (?,?,?,?,?)',
                                (a['id'],a['start_s'],a['end_s'],json.dumps(a,allow_nan=False),json.dumps(edit['points'],allow_nan=False)))
                        for event in sample.get('approach_events', []):
                            a = event['attempt']
                            db.execute('INSERT OR REPLACE INTO approaches VALUES (?,?,?,?,?)',
                                (a['id'], a['start_s'], a['end_s'], a['outcome'],
                                 json.dumps(a, separators=(',', ':'), allow_nan=False)))
                            if event.get('backfill_points'):
                                db.executemany('INSERT INTO approach_points VALUES (?,?,?,?,?,?)',
                                               ((a['id'], *point) for point in event['backfill_points']))
                            if 'point' in event:
                                db.execute('INSERT INTO approach_points VALUES (?,?,?,?,?,?)',
                                           (a['id'], *event['point']))
                        if 'capture' not in sample:
                            continue
                        rx, source = sample['capture'], sample['source']
                        if self.first_unix_s is None:
                            self.first_unix_s=rx['unix_s'];self.first_monotonic_s=rx['monotonic_s']
                        self.last_unix_s=rx['unix_s']
                        last_monotonic_s = rx['monotonic_s']
                        has_aircraft = has_aircraft or bool(sample.get('aircraft'))
                        rows.append((rx['unix_s'], rx['monotonic_s'], rx['connection_id'],
                            source.get('real_time_s'), source.get('mission_time_s'), source.get('seq'),
                            json.dumps(sample, separators=(',', ':'), allow_nan=False)))
                    db.executemany('INSERT INTO samples VALUES (NULL,?,?,?,?,?,?,?)', rows)
                    self.written += len(rows)
                    if barriers:
                        db.commit()
                        for barrier in barriers:barrier.set()
                if time.monotonic() - last_commit > .5:
                    db.commit()
                    last_commit = time.monotonic()
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if self.discard_incomplete and not self.failed:
                if not has_aircraft:
                    discard_reason = 'no aircraft data'
                elif last_monotonic_s - self.first_monotonic_s < MIN_AUTO_REPLAY_SECONDS:
                    discard_reason = 'under 30 seconds'
                elif not landing_count(db):
                    discard_reason = 'no landings'
        except Exception as exc:
            self.failed = f'DVR recording failed: {exc}'
            self.report('dvr_error', message=self.failed)
        finally:
            self.done.set()
            if db is not None:
                db.close()
            if discard_reason:
                try:
                    for attempt in range(5):
                        try:
                            delete_replay(self.path)
                            break
                        except PermissionError:
                            if attempt == 4:
                                raise
                            time.sleep(.1)
                except (OSError, sqlite3.Error, ValueError) as exc:
                    self.failed = f'Could not discard automatic recording: {exc}'
                    discard_reason = None
            self.report('dvr_finished', path=str(self.path) if self.path else '',
                        recording_id=self.recording_id, discarded=discard_reason,
                        samples=self.written, error=self.failed)
