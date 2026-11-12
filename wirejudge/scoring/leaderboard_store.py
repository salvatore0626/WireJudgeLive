"""Portable score-only history. No aircraft samples, tracks, or DVR payloads."""
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import uuid
from wirejudge.scoring.landing_scoring import DEFAULT_RULES,validate_rules
from wirejudge.replay.replay_files import utc
from wirejudge.aircraft.pilot_identity import split_actor_label

FORMAT='WireJudgeLive.Leaderboards'
DUPLICATE_WINDOW = timedelta(seconds=5)
FIELDS={'attempt_id','recording_id','replay_name','pilot','pilot_id','aircraft','carrier','case',
        'started_utc','ended_utc','updated_utc','outcome','wire','score','maximum','components',
        'provisional','rules','final_reference','attempt_start_offset_s','attempt_end_offset_s'}


def now_utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def attempt_timestamp(record):
    value = record.get('ended_utc') or record.get('started_utc')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def utc_duplicate_ids(records):
    """Find both sides of each saved UTC match using adjacent timestamps."""
    ordered = sorted((attempt_timestamp(r), r['attempt_id']) for r in records)
    duplicates = set()
    for (earlier, first), (later, second) in zip(ordered, ordered[1:]):
        if later - earlier <= DUPLICATE_WINDOW:
            duplicates.update((first, second))
    return duplicates


def number(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def normalize_record(record):
    if not isinstance(record,dict):raise ValueError('An attempt must be an object.')
    # Explicit allowlist prevents telemetry from entering either database or exports.
    r={key:record[key] for key in FIELDS if key in record}
    try:r['attempt_id']=uuid.UUID(str(r['attempt_id'])).hex
    except (KeyError,ValueError,TypeError):raise ValueError('Each attempt needs a valid unique ID.')
    for key in ('pilot','aircraft','carrier','outcome','replay_name','pilot_id'):
        value=r.get(key)
        if value is not None and not isinstance(value,(str,int)):raise ValueError(f'Invalid {key}.')
        r[key]=str(value or '')[:500]
    r['pilot'], r['aircraft'] = split_actor_label(r['pilot'], r['aircraft'])
    if r.get('case') not in ('Case 1','Case 3'):raise ValueError('Invalid case type.')
    for key in ('started_utc','ended_utc','updated_utc'):
        value=r.get(key)
        if value is None and key=='ended_utc':continue
        try:
            dt=datetime.fromisoformat(value.replace('Z','+00:00'))
            if dt.tzinfo is None:raise ValueError()
            r[key]=dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
        except (ValueError,TypeError,AttributeError):raise ValueError(f'Invalid {key} timestamp.')
    for key in ('score','maximum'):
        if not number(r.get(key)) or r[key]<0:raise ValueError('Score values must be finite and nonnegative.')
    if r['score']>r['maximum']+1e-6:raise ValueError('Score exceeds its maximum.')
    components=r.get('components')
    if not isinstance(components,dict):raise ValueError('Missing component scores.')
    r['components']={}
    for key in ('Position','Speed','Localizer','Glide','AoA','Wire'):
        c=components.get(key)
        if c is None:continue
        if not isinstance(c,dict) or any(not number(c.get(f)) or c[f]<0 for f in ('score','maximum')):raise ValueError('Invalid component score.')
        if c['score']>c['maximum']+1e-6:raise ValueError('Component score exceeds its maximum.')
        r['components'][key]={'score':float(c['score']),'maximum':float(c['maximum'])}
    rules=r.get('rules') or DEFAULT_RULES
    normalized=validate_rules(rules)
    if 'speed_deadzone_nm' not in rules.get('targets', {}):
        normalized['targets']['speed_deadzone_nm'] = 0.
    r['rules']={section:{key:normalized[section][key] for key in keys} for section,keys in DEFAULT_RULES.items()}
    r['provisional']=bool(r.get('provisional'))
    if r.get('final_reference') is None:
        r.pop('final_reference', None)
    elif r['final_reference'] not in ('Hook', 'Aircraft fallback', 'Hook + aircraft fallback'):
        raise ValueError('Invalid final scoring reference.')
    if r.get('wire') not in (None,1,2,3,4):raise ValueError('Invalid wire number.')
    for key in ('attempt_start_offset_s','attempt_end_offset_s'):
        if r.get(key) is not None and (not number(r[key]) or r[key]<0):raise ValueError('Invalid replay offset.')
    if r.get('recording_id') is not None:
        try:r['recording_id']=uuid.UUID(str(r['recording_id'])).hex
        except (ValueError,TypeError):raise ValueError('Invalid recording ID.')
    return r


def fingerprint(record):
    values={k:v for k,v in record.items() if k not in ('updated_utc','replay_name')}
    values['pilot'], values['aircraft'] = split_actor_label(values.get('pilot', ''), values.get('aircraft', ''))
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def score_record(attempt,grade,rules,info=None):
    info=info or {};start=attempt.get('start_unix_s')
    if start is None:
        if info.get('start_unix_s') is None:raise ValueError('This attempt has no calendar timestamp. Open its replay before saving the score.')
        start=info['start_unix_s']+attempt['start_s']-info['start_s']
    else:start+=attempt['start_s']-attempt.get('detected_start_s',attempt['start_s'])
    end=attempt.get('end_s');end_unix=start+(end-attempt['start_s']) if end is not None else None
    identity=attempt.get('identity') or {}
    return normalize_record(dict(attempt_id=attempt['id'],recording_id=info.get('recording_id'),replay_name=info.get('name',''),
        pilot=attempt['pilot'],pilot_id=identity.get('player_id') or identity.get('steam_id'),aircraft=attempt.get('airframe'),carrier=attempt['carrier'],
        case=attempt.get('case','Case 1'),started_utc=utc(start),ended_utc=utc(end_unix),updated_utc=now_utc(),
        outcome=attempt['outcome'],wire=attempt.get('wire'),score=grade['earned'],maximum=grade['maximum'],
        components={key:{'score':value['earned'],'maximum':value['maximum']} for key,value in grade['components'].items()},
        provisional=grade['provisional'] or end is None,rules=rules,final_reference=grade.get('final_reference'),
        attempt_start_offset_s=max(0,attempt['start_s']-info['start_s']) if info.get('start_s') is not None else None,
        attempt_end_offset_s=max(0,end-info['start_s']) if end is not None and info.get('start_s') is not None else None))


class LeaderboardStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')

    def records(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            records = [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM attempts')]
        for record in records:
            record['pilot'], record['aircraft'] = split_actor_label(record.get('pilot', ''), record.get('aircraft', ''))
        return records
    def merge(self,records,replace_conflicts=False,check_utc=False):
        records=[normalize_record(r) for r in records]
        report={'added':0,'duplicates':0,'conflicts':[],'updated':0,'utc_matches':[]}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            saved_times = []
            last_added = None
            if check_utc:
                saved_times = sorted(attempt_timestamp(json.loads(raw))
                                     for (raw,) in db.execute('SELECT payload_json FROM attempts'))
                records.sort(key=attempt_timestamp)
            for record in records:
                identifier=record['attempt_id'];row=db.execute('SELECT payload_json FROM attempts WHERE id=?',(identifier,)).fetchone()
                if row:
                    existing=json.loads(row[0])
                    if fingerprint(existing)==fingerprint(record):report['duplicates']+=1;continue
                    if not replace_conflicts:report['conflicts'].append(record);continue
                    report['updated']+=1
                if not row and check_utc:
                    stamp = attempt_timestamp(record)
                    nearby = bisect_left(saved_times, stamp)
                    neighbors = saved_times[max(0, nearby - 1):nearby + 1]
                    saved_match = any(abs(stamp - other) <= DUPLICATE_WINDOW for other in neighbors)
                    batch_match = last_added is not None and stamp - last_added <= DUPLICATE_WINDOW
                    if saved_match or batch_match:
                        report['utc_matches'].append(record)
                        continue
                    last_added = stamp
                if not row:
                    report['added'] += 1
                db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?)',(identifier,json.dumps(record,allow_nan=False,sort_keys=True)))
        return report
    def delete(self,identifiers):
        with closing(sqlite3.connect(self.path)) as db, db:db.executemany('DELETE FROM attempts WHERE id=?',[(identifier,) for identifier in identifiers])


def export_scores(path,records):
    records=[normalize_record(r) for r in records]
    payload={'format':FORMAT,'version':1,'exported_utc':now_utc(),'attempts':records}
    path=Path(path);fd,temp=tempfile.mkstemp(prefix='.wj-scores-',dir=path.parent);os.close(fd)
    try:
        Path(temp).write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
        os.replace(temp,path)
    finally:Path(temp).unlink(missing_ok=True)


def import_scores(path):
    path=Path(path)
    if path.stat().st_size>50*1024*1024:raise ValueError('Leaderboard files must be smaller than 50 MB.')
    payload=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or payload.get('format')!=FORMAT or payload.get('version')!=1:raise ValueError('Choose a Wire Judge Live leaderboard export.')
    attempts=payload.get('attempts')
    if not isinstance(attempts,list) or len(attempts)>100000:raise ValueError('Invalid leaderboard attempt list.')
    return [normalize_record(r) for r in attempts]
