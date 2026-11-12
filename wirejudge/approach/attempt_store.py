"""Worker-owned track reads and boundary edits; original detection/data stay intact."""
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
from uuid import uuid4
from contextlib import closing
from wirejudge.approach.approach_detection import measurement, number
from wirejudge.approach.approach_geometry import case1_volume, NM, point
from wirejudge.scoring.landing_scoring import compact_sample
from wirejudge.aircraft.pilot_identity import normalize_attempt_identity, pilot_identity
from wirejudge.map.carrier_wireframe import carrier_geometry
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion


def read_track(db, attempt, start, end, cancel=lambda:False, score_rows=None, deck=None):
    rows = db.execute('SELECT received_monotonic_s,payload_json FROM samples '
        'WHERE connection_id=? AND received_monotonic_s BETWEEN ? AND ? '
        'ORDER BY received_monotonic_s,id', (attempt['connection_id'], start, end))
    track, wires = [], []
    previous = None
    for when, payload in rows:
        if cancel():
            raise ValueError('Track request cancelled')
        s = json.loads(payload)
        aircraft = next((a for a in s['aircraft'] if a['instance_id']==attempt['aircraft_id']),None)
        ship = next((c for c in s['carriers'] if c['instance_id']==attempt['carrier_id']),None)
        v = case1_volume(ship,attempt['glide_deg'],attempt['offset_deg']) if ship else None
        m = measurement(aircraft,v,attempt['glide_deg']) if aircraft and v else None
        if m is None:
            if track and previous is not None:
                track.append([when,None,None,None,None,None,None])
            previous = None
            continue
        if score_rows is not None:
            score_rows.append(compact_sample(s, aircraft, ship, attempt['glide_deg'], attempt['offset_deg'], volume=v))
        # A discontinuity is a gap, never an invented connecting flight path.
        source = s.get('source') or {}
        clock = next((source.get(k) for k in ('mission_time_s','real_time_s') if number(source.get(k))),when)
        if previous is not None and (clock < previous or clock-previous > 2):
            track.append([when,None,None,None,None,None,None])
        previous = clock
        track.append([when,-m['left']/NM,-m['aft']/NM,m['aft']/NM,m['gs'],m['loc'],m['aoa']])
        if deck is not None and not deck and point(ship.get('position_world_m')):
            runway = v['runway_heading_deg']
            angle = math.radians(runway)
            origin = v['origin']

            def transform(p):
                x, y, z = (p[i]-origin[i] for i in range(3))
                return (x*math.cos(angle)-z*math.sin(angle), x*math.sin(angle)+z*math.cos(angle), y)

            heading = heading_from_quaternion(ship.get('rotation_xyzw'))
            if heading is not None:
                lines, _, _ = carrier_geometry(ship, transform(ship['position_world_m']),
                                                math.radians(heading-runway), transform, False)
                deck.extend([[(x/NM, y/NM) for x, y, _ in line] for line in lines])
        if not wires:
            angle = math.radians(v['runway_heading_deg']); o = v['origin']
            for cable in ship.get('cables') or []:
                ends = [cable.get('endpoint1_world_m'),cable.get('endpoint2_world_m')]
                if all(point(p) for p in ends):
                    line=[]
                    for p in ends:
                        x,z = p[0]-o[0],p[2]-o[2]
                        line.append([(x*math.cos(angle)-z*math.sin(angle))/NM,
                                     (x*math.sin(angle)+z*math.cos(angle))/NM])
                    wires.append(line)
    return track, wires


def read_editor(db, attempt, start=None, end=None, cursor=None, cancel=lambda:False):
    row = db.execute('SELECT min(received_monotonic_s),max(received_monotonic_s) '
        'FROM samples WHERE connection_id=?',(attempt['connection_id'],)).fetchone()
    if row[0] is None:
        raise ValueError('Waiting for recorded samples to reach disk.')
    low, high = row
    if cursor is not None:
        high = min(high,cursor)
    start = attempt['start_s'] if start is None else start
    end = (attempt.get('end_s') or attempt['last_s']) if end is None else end
    end = min(end,high)
    deck = []
    track,wires = read_track(db,attempt,max(low,start-30),min(high,end+30),cancel,deck=deck)
    return {'bounds':[low,high], 'track':track, 'wires':wires, 'deck':deck, 'start':start, 'end':end}


def edited_metadata(attempt, start, end, case=None, outcome=None):
    if not all(number(v) for v in (start,end)) or start>=end:
        raise ValueError('Start must be before end.')
    metadata=dict(attempt,start_s=start,end_s=end,last_s=end,edited=True,
        detected_start_s=attempt.get('detected_start_s',attempt['start_s']),
        detected_end_s=attempt.get('detected_end_s',attempt['end_s']),
        detected_outcome=attempt.get('detected_outcome',attempt['outcome']))
    metadata['case']=case or attempt.get('case','Case 1')
    if metadata['case'] not in ('Case 1','Case 3'):raise ValueError('Choose Case 1 or Case 3.')
    catch=attempt.get('wire_catch_s')
    if catch is not None and start<=catch<=end and attempt.get('wire'):
        metadata['outcome']=f"Wire {attempt['wire']}"
    elif not attempt.get('manual') and (catch is not None or end < (attempt.get('detected_end_s') or attempt.get('end_s') or attempt['last_s']) or attempt['end_s'] is None):
        metadata['outcome'],metadata['wire']='Ended manually',None
    if outcome is not None:
        metadata['outcome']=outcome
        metadata['wire']=int(outcome[-1]) if outcome.startswith('Wire ') else None
        metadata['wire_catch_s']=None
    return normalize_attempt_identity(metadata)


def ensure_edits_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS attempt_edits (id TEXT PRIMARY KEY, '
               'start_s REAL, end_s REAL, metadata_json TEXT NOT NULL, points_json TEXT NOT NULL)')


def save_boundaries(db, attempt, start, end, cursor=None, case=None, outcome=None, follow_live=False, allow_missing=False, create=False):
    if not all(number(v) for v in (start,end)) or start >= end:
        raise ValueError('Start must be before end.')
    # Decode/rebuild outside the write transaction so a long track cannot block capture.
    track,_ = read_track(db,attempt,start,end)
    valid = [p for p in track if p[1] is not None]
    if len(valid)<2 and not allow_missing:
        raise ValueError('Choose a range containing at least two recorded track samples.')
    db.execute('BEGIN IMMEDIATE')
    try:
        ensure_edits_table(db)
        bounds = db.execute('SELECT min(received_monotonic_s),max(received_monotonic_s) '
            'FROM samples WHERE connection_id=?',(attempt['connection_id'],)).fetchone()
        if bounds[0] is None or (not allow_missing and (start < bounds[0] or end > bounds[1])) or (cursor is not None and end>cursor):
            raise ValueError('Choose boundaries inside the available recording.')
        original = db.execute('SELECT metadata_json FROM approaches WHERE id=?',(attempt['id'],)).fetchone()
        if create:
            original = attempt
        elif original:
            original = json.loads(original[0])
            edit = db.execute('SELECT metadata_json FROM attempt_edits WHERE id=?', (attempt['id'],)).fetchone()
            if edit and json.loads(edit[0]).get('deleted'):
                raise ValueError('This attempt has been deleted.')
        else:
            raise ValueError('Attempt has not reached disk yet. Try again shortly.')
        others = db.execute('SELECT a.metadata_json,e.metadata_json FROM approaches a '
            'LEFT JOIN attempt_edits e ON e.id=a.id WHERE a.id<>?',(attempt['id'],))
        for raw, edited in others:
            other = json.loads(edited or raw)
            if other.get('deleted'):
                continue
            if (other['connection_id']==attempt['connection_id'] and other['aircraft_id']==attempt['aircraft_id']
                    and start < (other['end_s'] or other['last_s']) and end > other['start_s']):
                raise ValueError('These boundaries overlap another attempt for this aircraft.')
        # Save the requested boundary times; never manufacture endpoint telemetry.
        metadata = edited_metadata(dict(original,**{k:v for k,v in attempt.items() if k in ('case','wire','wire_catch_s','outcome','detected_start_s','detected_end_s','detected_outcome')}),start,end,case,outcome)
        metadata['missing_bounds']=start<bounds[0] or end>bounds[1]
        if follow_live:metadata.update(end_s=None,outcome=attempt['outcome'],follow_live=True)
        points = [[p[0],p[3],p[4],p[5],p[6]] for p in track]
        if create:
            db.execute('INSERT INTO approaches (id,start_s,end_s,outcome,metadata_json) VALUES (?,?,?,?,?)',
                       (attempt['id'],start,end,metadata['outcome'],json.dumps(metadata,allow_nan=False)))
        db.execute('INSERT OR REPLACE INTO attempt_edits VALUES (?,?,?,?,?)',
            (attempt['id'],start,metadata['end_s'],json.dumps(metadata,allow_nan=False),json.dumps(points,allow_nan=False)))
        db.commit()
        return metadata,points
    except Exception:
        db.rollback()
        raise


def read_aircraft_lives(db, index, stop):
    """Index contiguous aircraft appearances without retaining snapshot data."""
    last_id, lives, active = index
    rows = db.execute('SELECT id, received_monotonic_s, connection_id, payload_json FROM samples '
                      'WHERE id > ? ORDER BY id', (last_id,))
    for last_id, stamp, connection, raw in rows:
        if stop():
            break
        present = {}
        for aircraft in json.loads(raw).get('aircraft', []):
            aircraft_id = aircraft['instance_id']
            identity = aircraft.get('identity') or {}
            pilot, airframe = pilot_identity(identity, aircraft_id)
            key = (connection, aircraft_id, pilot, airframe)
            life = active.get(key)
            if life is None or stamp - life['end_s'] > 2:
                life = dict(connection_id=connection, aircraft_id=aircraft_id, pilot=pilot,
                            airframe=airframe, start_s=stamp, end_s=stamp)
                lives.append(life)
            life['end_s'] = stamp
            present[key] = life
        active = present
    return last_id, lives, active


def manual_candidates(db, start, end, glide, offset, life=None):
    candidates = {}
    rows = db.execute(
        'SELECT payload_json FROM samples WHERE received_monotonic_s BETWEEN ? AND ? '
        'ORDER BY received_monotonic_s,id', (start, end))
    for raw, in rows:
        sample = json.loads(raw)
        capture = sample['capture']
        source = sample.get('source') or {}
        for ship in sample.get('carriers', []):
            if case1_volume(ship, glide, offset) is None:
                continue
            for aircraft in sample.get('aircraft', []):
                if life and (capture['connection_id'] != life['connection_id']
                             or aircraft['instance_id'] != life['aircraft_id']):
                    continue
                if not point((aircraft.get('kinematics') or {}).get('position_world_m')):
                    continue
                key = (capture['connection_id'], aircraft['instance_id'], ship['instance_id'])
                if key in candidates:
                    continue
                identity = aircraft.get('identity') or {}
                pilot, airframe = pilot_identity(identity, key[1])
                unix = capture.get('unix_s')
                candidates[key] = dict(
                    id=uuid4().hex, manual=True, connection_id=key[0], aircraft_id=key[1], carrier_id=key[2],
                    pilot=pilot,
                    airframe=airframe, identity=identity,
                    carrier=ship.get('airport_name') or ship.get('actor_name') or str(key[2]),
                    source={k: source.get(k) for k in ('process_id', 'epoch')},
                    start_s=start, end_s=end, last_s=end, outcome='Ended manually', wire=None,
                    start_unix_s=unix + start - capture['monotonic_s'] if unix is not None else None,
                    glide_deg=glide, offset_deg=offset,
                )
    return sorted(candidates.values(), key=lambda a: (a['pilot'].casefold(), a['airframe'] or '', a['carrier']))


def add_attempt(db, attempt, case, outcome):
    # Build the history before committing so a failed read cannot leave a partial add.
    history = []
    low, high = db.execute('SELECT min(received_monotonic_s),max(received_monotonic_s) FROM samples '
                           'WHERE connection_id=?', (attempt['connection_id'],)).fetchone()
    if low is None:
        raise ValueError('No recorded samples for that aircraft and carrier.')
    read_track(db, attempt, low, high, score_rows=history)
    metadata, points = save_boundaries(db, attempt, attempt['start_s'], attempt['end_s'],
                                       case=case, outcome=outcome, create=True)
    return metadata, points, history


def delete_attempt(db, attempt):
    with db:
        db.execute('BEGIN IMMEDIATE')
        ensure_edits_table(db)
        identifier = attempt['id']
        existing = db.execute('SELECT metadata_json,points_json FROM attempt_edits WHERE id=?',
                              (identifier,)).fetchone()
        metadata, points = (json.loads(existing[0]), existing[1]) if existing else (attempt, '[]')
        metadata = dict(metadata, deleted=True)
        db.execute('INSERT OR REPLACE INTO attempt_edits VALUES (?,?,?,?,?)',
                   (identifier, metadata['start_s'], metadata.get('end_s'), json.dumps(metadata), points))


class AttemptWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tasks = queue.Queue()
        self.results = queue.SimpleQueue()
        self.stopping = threading.Event()
        self.read_token = 0

    def request(self, kind, path, attempt, token, **values):
        if self.stopping.is_set():return
        if kind == 'read':self.read_token = token
        self.tasks.put((kind,str(path),attempt,token,values))

    def stop(self):
        self.stopping.set()
        self.tasks.put(None)

    def run(self):
        life_path = None
        life_index = (0, [], {})
        while True:
            task = self.tasks.get()
            if task is None:return
            kind,path,attempt,token,values = task
            if kind=='read' and (self.stopping.is_set() or token!=self.read_token):continue
            result = {'kind':kind,'path':path,'id':attempt['id'],'token':token,'error':None}
            try:
                with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=5)) as db:
                    if kind == 'save':
                        metadata,points = save_boundaries(db,attempt,**values)
                        result.update(metadata=metadata,points=points)
                    elif kind == 'lives':
                        if path != life_path:
                            life_path, life_index = path, (0, [], {})
                        life_index = read_aircraft_lives(db, life_index, self.stopping.is_set)
                        result['lives'] = [dict(life) for life in life_index[1]]
                    elif kind == 'candidates':
                        result['candidates'] = manual_candidates(db, **values)
                    elif kind == 'add':
                        metadata, points, history = add_attempt(db, attempt, **values)
                        result.update(metadata=metadata, points=points, history=history)
                    elif kind == 'delete':
                        delete_attempt(db, attempt)
                    else:
                        result.update(read_editor(db,attempt,**values,
                            cancel=lambda:self.stopping.is_set() or token!=self.read_token))
            except Exception as exc:
                result['error'] = str(exc)
            self.results.put(result)
