"""Portable DVR identity and consistent live SQLite snapshots."""
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing

MIN_AUTO_REPLAY_SECONDS = 30
AUTO_REPLAY_NAME = re.compile(r'WireJudge_\d{8}_\d{6}_\d{6}\.wjlive\.sqlite')


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z') if value is not None else None


def replay_info(path):
    path=str(Path(path).resolve())
    with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        metadata=dict(db.execute('SELECT key,value FROM metadata'))
        if metadata.get('format')!='WireJudgeLive direct-game telemetry':raise ValueError('Choose a Wire Judge Live replay.')
        row=db.execute('SELECT min(received_monotonic_s),max(received_monotonic_s),min(received_unix_s),max(received_unix_s),count(*) FROM samples').fetchone()
        first=db.execute('SELECT connection_id FROM samples ORDER BY received_monotonic_s,id LIMIT 1').fetchone()
        identity=metadata.get('recording_id') or uuid.uuid5(uuid.NAMESPACE_URL,'WireJudgeLive:'+metadata.get('created_utc','')+':'+str(first)+':'+str(row[2])).hex
        return dict(path=path,recording_id=identity,name=metadata.get('replay_name') or Path(path).name.removesuffix('.wjlive.sqlite'),
                    start_s=row[0],end_s=row[1],start_unix_s=row[2],end_unix_s=row[3],samples=row[4],
                    session_id=metadata.get('session_id'),created_utc=metadata.get('created_utc'))


def landing_count(db):
    return db.execute("SELECT count(*) FROM approaches WHERE outcome LIKE 'Wire %' OR outcome='Bolter'").fetchone()[0]


def list_replays(folder, indexed_paths):
    """Read only session summaries; avoid decoding samples or scanning payloads."""
    paths = set(Path(folder).glob('*.wjlive.sqlite'))
    paths.update(Path(path) for path in indexed_paths if isinstance(path, str))
    rows = []
    for path in paths:
        try:
            path = path.resolve()
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                metadata = dict(db.execute('SELECT key,value FROM metadata'))
                if metadata.get('format') != 'WireJudgeLive direct-game telemetry':
                    continue
                first = db.execute('SELECT received_unix_s, received_monotonic_s FROM samples ORDER BY received_monotonic_s LIMIT 1').fetchone()
                last = db.execute('SELECT received_monotonic_s FROM samples ORDER BY received_monotonic_s DESC LIMIT 1').fetchone()
                attempts = landing_count(db)
                rows.append(dict(path=str(path), name=metadata.get('replay_name') or path.name.removesuffix('.wjlive.sqlite'),
                                 start_unix_s=first[0] if first else None, duration=last[0]-first[1] if first else 0,
                                 landings=attempts, ready=bool(first)))
        except (sqlite3.Error, OSError):
            continue
    return sorted({row['path']:row for row in rows}.values(), key=lambda row:row['start_unix_s'] or 0, reverse=True)


def delete_replay(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=5)) as db:
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        if metadata.get('format') != 'WireJudgeLive direct-game telemetry':
            raise ValueError('Choose a Wire Judge Live replay.')
        busy, _, _ = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise ValueError('This recording is still in use.')
    path.unlink()
    return str(path)


def cleanup_reason(path):
    """Identify unfinished or empty automatic recordings without opening a viewer."""
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        if (metadata.get('format') != 'WireJudgeLive direct-game telemetry'
                or metadata.get('saved_utc') or metadata.get('replay_name')):
            return None
        automatic = metadata.get('automatic_recording')
        if automatic != '1' and (automatic is not None or not AUTO_REPLAY_NAME.fullmatch(path.name)):
            return None
        first = db.execute('SELECT received_monotonic_s FROM samples ORDER BY received_monotonic_s LIMIT 1').fetchone()
        if first is None:
            return 'No telemetry'
        if landing_count(db) or db.execute('SELECT 1 FROM attempt_edits LIMIT 1').fetchone():
            return None
        last = db.execute('SELECT received_monotonic_s FROM samples ORDER BY received_monotonic_s DESC LIMIT 1').fetchone()
        if last[0] - first[0] < MIN_AUTO_REPLAY_SECONDS:
            return 'Under 30 seconds, no landings'
        if not db.execute("SELECT 1 FROM samples WHERE json_array_length(payload_json, '$.aircraft') > 0 LIMIT 1").fetchone():
            return 'No aircraft data'
        return None


def cleanup_candidates(folder, protected):
    rows = []
    for path in Path(folder).expanduser().glob('*.wjlive.sqlite'):
        try:
            if path.is_symlink():
                continue
            path = path.resolve()
            if str(path) in protected:
                continue
            reason = cleanup_reason(path)
            if reason:
                rows.append((str(path), path.name.removesuffix('.wjlive.sqlite'), reason))
        except (sqlite3.Error, OSError):
            continue
    return sorted(rows, key=lambda row: row[1], reverse=True)


def delete_cleanup_candidates(paths):
    deleted, errors = [], []
    for filename in paths:
        try:
            path = Path(filename)
            if not path.is_symlink() and cleanup_reason(path):
                deleted.append(delete_replay(path))
        except (sqlite3.Error, OSError, ValueError) as exc:
            errors.append(f'{Path(filename).name}: {exc}')
    return deleted, errors


def rename_replay(path, name):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=5)) as db, db:
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('replay_name', name))
    return str(path), name


def save_replay(source,destination,name,rules,writer=None):
    """SQLite backup includes WAL and never renames a live writer's open file."""
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination:raise ValueError('Choose a different filename for the saved replay.')
    if writer is not None and writer.done.is_set() and writer.is_alive():
        writer.join(10)
        if writer.is_alive():raise ValueError('The recording is still finishing. Try exporting again shortly.')
    if writer is not None and not writer.done.is_set():
        barrier=writer.flush()
        if not barrier.wait(10):raise ValueError('Recording is still flushing to disk. Try exporting again.')
        if writer.failed:raise ValueError(writer.failed)
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.wj-replay-',suffix='.sqlite',dir=destination.parent);os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst,pages=256)
                dst.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('recording_id',replay_info(source)['recording_id']))
                dst.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('replay_name',name))
                dst.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('scoring_rules',json.dumps(rules,allow_nan=False)))
                dst.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('saved_utc',datetime.now(timezone.utc).isoformat()))
                dst.commit();dst.execute('PRAGMA journal_mode=DELETE')
        # Atomic replacement: a failed save leaves an existing destination intact.
        os.replace(temporary,destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return replay_info(destination)


def replay_attempt_rows(db, has_edits):
    """Yield the stored attempt and its effective edited version."""
    edits = {}
    if has_edits:
        edits = {key: json.loads(raw) for key, raw in
                 db.execute('SELECT id, metadata_json FROM attempt_edits')}
    for key, raw in db.execute('SELECT id, metadata_json FROM approaches ORDER BY start_s'):
        detected = json.loads(raw)
        effective = edits.get(key, detected)
        if effective.get('deleted'):
            continue
        if effective.get('follow_live'):
            effective = dict(effective, **{field: detected.get(field) for field in
                             ('last_s', 'end_s', 'outcome', 'wire', 'wire_catch_s')})
        if effective['end_s'] is None:
            effective = dict(effective, end_s=effective['last_s'], outcome='Interrupted')
        yield key, detected, effective


def _trim_attempts(db, has_edits, start, end):
    for key, detected, attempt in replay_attempt_rows(db, has_edits):
        # The DVR markers use the detected recovery even if its grading bounds were edited.
        event = detected
        if not (detected.get('wire') in (1, 2, 3, 4) or
                (detected.get('outcome') or '').startswith(('Wire ', 'Bolter'))):
            event = attempt
        outcome = event.get('outcome') or ''
        marker = None
        if outcome == 'Bolter':
            marker = event.get('end_s')
        elif event.get('wire') in (1, 2, 3, 4) or outcome.startswith('Wire '):
            marker = event.get('wire_catch_s')
            if marker is None:
                marker = event.get('end_s')
            outcome = 'Wire'
        if marker is not None and not start <= marker <= end:
            marker = None
        first = max(start, attempt['start_s'])
        last = min(end, attempt['end_s'])
        if first <= last:
            yield key, first, last, marker, outcome


def trim_preview(path):
    """Read bounds and attempt metadata without decoding telemetry snapshots."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        start = db.execute('SELECT min(received_monotonic_s) FROM samples').fetchone()[0]
        end = db.execute('SELECT max(received_monotonic_s) FROM samples').fetchone()[0]
        if start is None or end <= start:
            raise ValueError('This replay needs at least two distinct sample times to trim.')
        has_edits = db.execute("SELECT 1 FROM sqlite_master WHERE name='attempt_edits'").fetchone()
        return start, end, list(_trim_attempts(db, has_edits, start, end))


def attempt_fits(row, start, end):
    _, first, last, marker, _ = row
    return start <= first <= last <= end and (marker is None or start <= marker <= end)


def trim_replay(source, folder, name, start, end):
    """Write a compact replay containing only the retained window."""
    source = Path(source).resolve()
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    replay_id = uuid.uuid4().hex
    destination = folder / f'WireJudge_Trim_{replay_id}.wjlive.sqlite'
    fd, temporary = tempfile.mkstemp(prefix='.wj-trim-', suffix='.sqlite', dir=folder)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=5)) as db, db:
            db.execute('ATTACH DATABASE ? AS trimmed', (temporary,))
            db.execute('BEGIN')
            metadata = dict(db.execute('SELECT key, value FROM main.metadata'))
            if metadata.get('format') != 'WireJudgeLive direct-game telemetry':
                raise ValueError('Choose a Wire Judge Live replay.')
            first = db.execute('SELECT min(received_monotonic_s) FROM samples').fetchone()[0]
            last = db.execute('SELECT max(received_monotonic_s) FROM samples').fetchone()[0]
            if first is None or not first <= start < end <= last or (start, end) == (first, last):
                raise ValueError('Choose a shorter time range within this replay.')
            schema = list(db.execute("SELECT type, name, sql FROM main.sqlite_master "
                                     "WHERE sql IS NOT NULL AND type IN ('table', 'index')"))
            tables = {'metadata', 'samples', 'approaches', 'approach_points', 'attempt_edits'}
            copied = {name for kind, name, _ in schema if kind == 'table' and name in tables}
            for kind, table, sql in schema:
                if kind == 'table' and table in copied:
                    db.execute(sql.replace(f'CREATE TABLE {table}', f'CREATE TABLE trimmed.{table}', 1))
            db.execute('INSERT INTO trimmed.samples SELECT * FROM main.samples '
                       'WHERE received_monotonic_s BETWEEN ? AND ?', (start, end))
            kept_first, kept_last = db.execute('SELECT min(received_monotonic_s), max(received_monotonic_s) '
                                              'FROM trimmed.samples').fetchone()
            if kept_first is None or kept_first == kept_last:
                raise ValueError('Keep a range with at least two distinct sample times.')
            rows = _trim_attempts(db, 'attempt_edits' in copied, first, last)
            db.execute('CREATE TEMP TABLE retained(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO retained VALUES (?)',
                           ((row[0],) for row in rows if attempt_fits(row, start, end)))
            for table in ('approaches', 'attempt_edits'):
                if table in copied:
                    db.execute(f'INSERT INTO trimmed.{table} SELECT * FROM main.{table} '
                               'WHERE id IN (SELECT id FROM retained)')
            if 'approach_points' in copied:
                db.execute('INSERT INTO trimmed.approach_points SELECT * FROM main.approach_points '
                           'WHERE attempt_id IN (SELECT id FROM retained) '
                           'AND received_monotonic_s BETWEEN ? AND ?', (start, end))
            metadata.update(recording_id=replay_id, replay_name=name,
                            saved_utc=datetime.now(timezone.utc).isoformat())
            db.executemany('INSERT INTO trimmed.metadata VALUES (?, ?)', metadata.items())
            for kind, index, sql in schema:
                if kind == 'index' and index in ('samples_time', 'samples_source', 'approach_points_attempt'):
                    db.execute(sql.replace(f'CREATE INDEX {index}', f'CREATE INDEX trimmed.{index}', 1))
        info = replay_info(temporary)
        os.replace(temporary, destination)
        return dict(info, path=str(destination))
    finally:
        Path(temporary).unlink(missing_ok=True)
