from wirejudge.aircraft.landed_labels import LandedLabelTracker
"""TCP ingestion + bounded live history; separate from both Qt and disk writing."""
from collections import deque
import json
import bisect
import queue
import socket
import threading
import time
import uuid
from wirejudge.telemetry.telemetry_protocol import Decoder, ProtocolError, normalize
from wirejudge.replay.dvr_store import DVRWriter
from wirejudge.telemetry.world_coordinates import WorldCoordinates
from wirejudge.approach.approach_detection import ApproachDetector
from wirejudge.approach.approach_geometry import case1_volume
from wirejudge.scoring.landing_scoring import compact_sample, DEFAULT_RULES


class CaptureService:
    def __init__(self, buffer_s=30, max_bytes=128 * 1024 * 1024):
        self.lock = threading.RLock()
        self.buffer_s, self.max_bytes = buffer_s, max_bytes
        self.history = deque()
        self.history_bytes = 0
        self.latest = None
        self.revision = 0
        self.messages = queue.SimpleQueue()
        self.worker = None
        self.stop_event = threading.Event()
        self.writer = None
        self.writers = []
        self.total_samples = 0
        self.rate_times = deque(maxlen=100)
        self.status = 'Disconnected'
        self.connection_id = None
        self.source_key = None
        self.bound_port = None
        self.world_coordinates = WorldCoordinates()
        self.approaches = ApproachDetector()
        self.label_landings=LandedLabelTracker()
        self.approach_edits = {}
        self.deleted_attempts = set()
        self.edit_revision = 0
        self.score_history = {}
        self.archived_attempts = {}
        self.attempt_paths = {}
        self.recording_ids = set()
        self.recording_bindings={}
        self.auto_record_folder=None
        self.analysis_archive=None
        self.analysis_revision=0

    def configure_approaches(self, case, glide, offset):
        with self.lock:
            self.approaches.configure(case, glide, offset)

    def approach_catalog(self):
        with self.lock:
            if self.analysis_archive:
                return ('replay',self.analysis_archive['path'],self.analysis_revision),list(self.analysis_archive['attempts'].values())
            revision, catalog = self.approaches.catalog()
            result=[]
            for a in list(self.archived_attempts.values())+catalog:
                if a['id'] in self.deleted_attempts:
                    continue
                edited=self.approach_edits.get(a['id'],(a,None))[0]
                if edited.get('follow_live'):
                    edited=dict(edited,**{k:a.get(k) for k in ('last_s','end_s','outcome','wire','wire_catch_s')})
                result.append(edited)
            return (revision, self.edit_revision), result

    def approach_points(self, attempt_id):
        with self.lock:
            if self.analysis_archive:return list(self.analysis_archive['points'].get(attempt_id,[]))
            if attempt_id in self.approach_edits:
                a,points=self.approach_edits[attempt_id]
                if a.get('follow_live'):
                    return [p for p in self.approaches.points(attempt_id) if p[0]>=a['start_s']]
                return list(points)
            return self.approaches.points(attempt_id)

    def approach_event_catalog(self):
        """Detected events retain their real timestamps even after boundary edits."""
        with self.lock:
            revision, attempts = self.approaches.catalog()
            return (revision, self.edit_revision), [a for a in attempts if a['id'] not in self.deleted_attempts]

    def apply_attempt_change(self, path, identifier, metadata=None, points=None, history=None):
        with self.lock:
            archive = self.analysis_archive
            if archive and archive['path'] == path:
                if metadata is None:
                    archive['attempts'].pop(identifier, None)
                    archive['points'].pop(identifier, None)
                else:
                    archive['attempts'][identifier] = metadata
                    archive['points'][identifier] = points
                    key = (metadata['connection_id'], metadata['aircraft_id'], metadata['carrier_id'])
                    archive['histories'][key] = history
                self.analysis_revision += 1
                # A loaded replay may be the same file as the live DVR.
                writer_path = str(self.writer.path) if self.writer and self.writer.path else None
                if self.attempt_paths.get(identifier) != path and writer_path != path:
                    return
            if metadata is None:
                if self.attempt_paths.get(identifier) == path:
                    self.deleted_attempts.add(identifier)
                    self.edit_revision += 1
            else:
                key = (metadata['connection_id'], metadata['aircraft_id'], metadata['carrier_id'])
                self.install_archive(path, [metadata], {key: history})

    def install_approach_edit(self, metadata, points):
        with self.lock:
            if self.analysis_archive and metadata['id'] in self.analysis_archive['attempts']:
                self.analysis_archive['attempts'][metadata['id']]=metadata
                self.analysis_archive['points'][metadata['id']]=points
                self.analysis_revision+=1
                return
            if metadata['id'] not in self.approaches.attempts and metadata['id'] not in self.archived_attempts:
                return
            self.approach_edits[metadata['id']] = (metadata, points)
            if self.writer is not None and metadata['id'] not in self.attempt_paths:
                self.writer.submit({'attempt_edits':[{'metadata':metadata,'points':points}]})
            self.edit_revision += 1

    def configure_scoring(self, rules):
        with self.lock:
            self.scoring_rules = rules
            self.approaches.entry_nm = {'Case 1':max(1.,rules['Case 1']['start_nm']),
                'Case 3':max(3.,rules['Case 3']['platform_start_nm'] + .1)}

    def retain_scoring(self, sample):
        for ship in sample.get('carriers',[]):
            volume=case1_volume(ship,self.approaches.glide,self.approaches.offset)
            if volume is None:continue
            for aircraft in sample.get('aircraft',[]):
                row=compact_sample(sample,aircraft,ship,self.approaches.glide,self.approaches.offset,volume=volume)
                if row is None or not -.5<=row[2]<=30:continue
                key=(sample['capture']['connection_id'],aircraft['instance_id'],ship['instance_id'])
                self.score_history.setdefault(key,[]).append(row)

    def timeline_score_samples(self, attempt, timestamp):
        with self.lock:
            histories = self.analysis_archive['histories'] if self.analysis_archive else self.score_history
            key = (attempt['connection_id'], attempt['aircraft_id'], attempt['carrier_id'])
            history = histories.get(key, [])
            start = bisect.bisect_left(history, attempt['start_s'], key=lambda row: row[0])
            stop = bisect.bisect_right(history, timestamp, key=lambda row: row[0])
            return history[start:stop]

    def recent_scoring(self, connection, aircraft, carrier, start):
        rows = self.score_history.get((connection, aircraft, carrier), [])
        return rows[bisect.bisect_left(rows, start, key=lambda row: row[0]):]

    def score_inputs(self):
        with self.lock:
            revision,catalog=self.approach_catalog()
            rows=[]
            for a in catalog:
                histories=self.analysis_archive['histories'] if self.analysis_archive else self.score_history
                history=histories.get((a['connection_id'],a['aircraft_id'],a['carrier_id']),[])
                end=a.get('end_s') if a.get('end_s') is not None else a['last_s']
                if 'missing_bounds' in a:
                    a=dict(a,missing_bounds=not history or a['start_s']<history[0][0] or end>history[-1][0])
                lo=bisect.bisect_left(history,a['start_s'],key=lambda p:p[0])
                hi=bisect.bisect_right(history,end,key=lambda p:p[0])
                rows.append((a,list(history[lo:hi])))
            return revision,rows

    def attempt_path(self,identifier,default=None):
        if self.analysis_archive and identifier in self.analysis_archive['attempts']:return self.analysis_archive['path']
        return self.attempt_paths.get(identifier,default)

    def select_archive(self,path,attempts,histories):
        with self.lock:
            points={}
            for a in attempts:
                rows=histories.get((a['connection_id'],a['aircraft_id'],a['carrier_id']),[])
                points[a['id']]=[[p[0],p[2],p[4]-a['glide_deg'] if p[4] is not None else None,p[5],p[6]]
                    for p in rows if a['start_s']<=p[0]<=(a['end_s'] or a['last_s'])]
            self.analysis_archive=dict(path=path,attempts={a['id']:a for a in attempts},histories=histories,points=points)
            self.analysis_revision+=1

    def clear_analysis_archive(self):
        with self.lock:self.analysis_archive=None;self.analysis_revision+=1

    def install_archive(self, path, attempts, histories):
        with self.lock:
            for a in attempts:
                if a['id'] not in self.approaches.attempts:self.archived_attempts[a['id']]=a
                self.attempt_paths[a['id']]=path
            for key,rows in histories.items():
                # An opened recording may also be the current recording; keep newer live data.
                existing=self.score_history.get(key,[])
                combined={p[0]:p for p in rows}
                combined.update({p[0]:p for p in existing})
                self.score_history[key]=sorted(combined.values(),key=lambda p:p[0])
            for a in attempts:
                if a['id'] in self.approaches.attempts:continue
                history=self.score_history.get((a['connection_id'],a['aircraft_id'],a['carrier_id']),[])
                chosen=[p for p in history if a['start_s']<=p[0]<=(a['end_s'] or a['last_s'])]
                points=[[p[0],p[2],p[4]-a['glide_deg'] if p[4] is not None else None,p[5],p[6]] for p in chosen]
                self.approach_edits[a['id']]=(a,points)
            self.edit_revision+=1

    def edit_attempt_memory(self, attempt, start, end, case, outcome, follow_live=False, allow_missing=False):
        from wirejudge.approach.attempt_store import edited_metadata
        with self.lock:
            history=self.score_history.get((attempt['connection_id'],attempt['aircraft_id'],attempt['carrier_id']),[])
            if not history or (not allow_missing and (start<history[0][0] or end>history[-1][0])):
                raise ValueError('Choose boundaries inside the retained telemetry, or open the DVR recording.')
            for other in self.approach_catalog()[1]:
                if (other['id']!=attempt['id'] and other['connection_id']==attempt['connection_id'] and
                    other['aircraft_id']==attempt['aircraft_id'] and start<(other['end_s'] or other['last_s']) and end>other['start_s']):
                    raise ValueError('These boundaries overlap another attempt for this aircraft.')
            rows=[p for p in history if start<=p[0]<=end]
            if len(rows)<2 and not allow_missing:raise ValueError('Choose at least two retained samples.')
            metadata=edited_metadata(attempt,start,end,case,outcome)
            metadata['missing_bounds']=start<history[0][0] or end>history[-1][0]
            if follow_live:metadata.update(end_s=None,outcome=attempt['outcome'],follow_live=True)
            points=[[p[0],p[2],p[4]-metadata['glide_deg'] if p[4] is not None else None,p[5],p[6]] for p in rows]
            self.install_approach_edit(metadata,points)
            return metadata

    def report(self, kind, **data):
        if kind=='dvr_started':
            with self.lock:
                for identifier in self.recording_bindings.get(data.get('recording_id'),self.recording_ids):self.attempt_paths[identifier]=data['path']
        if kind == 'dvr_finished':
            with self.lock:
                identifiers = self.recording_bindings.pop(data['recording_id'], ())
                if data['discarded']:
                    for identifier in identifiers:
                        if self.attempt_paths.get(identifier) == data['path']:
                            del self.attempt_paths[identifier]
        self.messages.put((kind, data))

    def set_buffer(self, seconds):
        with self.lock:
            self.buffer_s = seconds
            self._prune(time.monotonic())

    def _prune(self, now):
        cutoff = now - self.buffer_s
        removed = False
        while self.history and (self.history[0][0] < cutoff or self.history_bytes > self.max_bytes):
            _, _, size = self.history.popleft()
            self.history_bytes -= size
            removed = True
        if removed:
            self.revision += 1

    def snapshot(self):
        # Published snapshots are never mutated. Qt only consumes these references.
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            recent = [t for t in self.rate_times if now - t <= 3]
            hz = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0
            return self.revision, self.latest, {
                'samples': self.total_samples, 'buffer_samples': len(self.history),
                'buffer_bytes': self.history_bytes, 'hz': hz,
                'status': self.status, 'port': self.bound_port,
                'age_s': now - self.latest['capture']['monotonic_s'] if self.latest else None}

    def history_snapshot(self):
        with self.lock:
            self._prune(time.monotonic())
            return [s for _, s, _ in self.history]

    def start(self, host, port):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.bound_port = None
        self.worker = threading.Thread(target=self._run, args=(host, port), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()

    def set_status(self, status):
        with self.lock:
            self.status = status
        self.report('status', message=status)

    def _new_connection(self):
        with self.lock:
            self.connection_id = uuid.uuid4().hex
            self.source_key = None
            self.world_coordinates.reset()
            self.latest = None
            self.rate_times.clear()
            self.revision += 1
            if self.auto_record_folder and self.writer is None:self.begin_dvr(self.auto_record_folder)

    def ingest(self, source, trailer=''):
        now = time.monotonic()
        sample = normalize(source)
        key = (source.get('process_id'), source.get('epoch'))
        with self.lock:
            # New mission/exporter epoch: never bridge old trails to a new coordinate space.
            if self.source_key is not None and key != self.source_key:
                if self.auto_record_folder:
                    events=self.approaches.stop()
                    if events and self.writer:self.writer.submit({'approach_events':events})
                    self.end_dvr()
                self.connection_id = uuid.uuid4().hex
                self.rate_times.clear()
                self.world_coordinates.reset()
            self.source_key = key
            if self.connection_id is None:
                self.connection_id = uuid.uuid4().hex
            if self.auto_record_folder and self.writer is None:self.begin_dvr(self.auto_record_folder)
            self.world_coordinates.update(sample)
            sample['capture'] = {'connection_id': self.connection_id,
                'monotonic_s': now, 'unix_s': time.time(), 'transport_trailer_hex': trailer}
            self.label_landings.update(sample)
            sample['approach_events'] = self.approaches.process(sample, self.recent_scoring)
            self.retain_scoring(sample)
            # Bound retained RAM by measured serialized payload sizes as well as duration.
            size = len(json.dumps(sample, separators=(',', ':'), allow_nan=False).encode('utf-8'))
            self.history.append((now, sample, size))
            self.history_bytes += size
            self.latest = sample
            self.total_samples += 1
            self.rate_times.append(now)
            self.revision += 1
            self._prune(now)
            if self.writer is not None:
                for event in sample.get('approach_events',[]):
                    identifier=event['attempt']['id'];self.recording_ids.add(identifier)
                    if self.writer.path:self.attempt_paths[identifier]=str(self.writer.path)
                if not self.writer.submit(sample):
                    self.writer = None
                    self.approaches.stop()
        return sample

    def begin_dvr(self, folder):
        with self.lock:
            if self.writer is not None:
                return
            self._prune(time.monotonic())
            # Existing bounded history is passed by reference, not duplicated into the queue.
            # Holding the lock makes the handoff exact: pre-roll once, then new arrivals.
            pre_roll = []
            self.recording_ids=set()
            for _, sample, _ in self.history:
                if self.auto_record_folder and sample['capture']['connection_id']!=self.connection_id:continue
                # Copies leave already published Live snapshots immutable.
                recorded = dict(sample)
                recorded['approach_events'] = sample.get('approach_events',[])
                pre_roll.append(recorded)
                self.recording_ids.update(e['attempt']['id'] for e in recorded['approach_events'])
            edits=[{'metadata':a,'points':points} for identifier,(a,points) in self.approach_edits.items() if identifier in self.recording_ids]
            if edits:pre_roll.append({'attempt_edits':edits})
            writer = DVRWriter(folder, self.report, tuple(pre_roll),session_id=self.connection_id,
                               discard_incomplete=bool(self.auto_record_folder))
            self.recording_bindings[writer.recording_id]=self.recording_ids
            self.writers.append(writer)
            self.writer = writer
            writer.start()

    def end_dvr(self):
        with self.lock:
            writer = self.writer
            self.writer = None
            # Stopping recording must not terminate an aircraft's live grade.
            events = [{'attempt':dict(a,end_s=a['last_s'],outcome='Interrupted')} for a in self.approaches.catalog()[1] if a['end_s'] is None]
            if writer is not None:
                if events:
                    writer.submit({'approach_events':events})
                writer.close()

    def shutdown(self):
        self.stop()
        with self.lock:
            if self.auto_record_folder:
                events = self.approaches.stop()
                if events and self.writer:
                    self.writer.submit({'approach_events': events})
            self.end_dvr()

    def busy(self):
        return (self.worker is not None and self.worker.is_alive()) or any(w.is_alive() for w in self.writers)

    def _run(self, host, port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((host, port))
                self.bound_port = server.getsockname()[1]
                server.listen(1)
                server.settimeout(.2)
                self.set_status('Listening')
                self.report('listening', host=host, port=self.bound_port)
                while not self.stop_event.is_set():
                    try:
                        client, peer = server.accept()
                    except socket.timeout:
                        continue
                    self._new_connection()
                    self.set_status('Connected')
                    self.report('connected', peer=f'{peer[0]}:{peer[1]}')
                    decoder = Decoder()
                    try:
                        with client:
                            client.settimeout(.2)
                            while not self.stop_event.is_set():
                                try:
                                    chunk = client.recv(65536)
                                except socket.timeout:
                                    continue
                                if not chunk:
                                    decoder.finish()
                                    break
                                for source, trailer in decoder.feed(chunk):
                                    self.ingest(source, trailer)
                    except (OSError, ProtocolError, ValueError, TypeError) as exc:
                        self.report('capture_error', message=str(exc))
                    finally:
                        with self.lock:
                            events = self.approaches.stop()
                            if events and self.writer is not None:
                                self.writer.submit({'approach_events':events})
                        if self.auto_record_folder:self.end_dvr()
                        self.set_status('Listening' if not self.stop_event.is_set() else 'Disconnected')
                        self.report('disconnected')
        except Exception as exc:
            self.set_status('Error')
            self.report('capture_error', message=f'Cannot receive on {host}:{port}: {exc}')
        finally:
            if self.status != 'Error':
                self.set_status('Disconnected')
            self.report('receiver_finished')
