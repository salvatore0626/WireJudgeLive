"""Live grades, retrospective edits, and worker-owned historical recalculation."""
import copy
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
import time
from contextlib import closing
from itertools import chain
from PySide6.QtCore import QObject,QEvent,Qt,Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QDialog,QDialogButtonBox,QDoubleSpinBox,
    QFormLayout,QCheckBox,QGroupBox,QTabWidget,QScrollArea,QFileDialog)
from wirejudge.approach.approach_scopes import ScopePane
from wirejudge.approach.approach_geometry import case1_volume, point
from wirejudge.scoring.score_odometer import LANDING_FADE_S, LANDING_HOLD_S, LANDING_SOUNDS, ScoreOdometer
from wirejudge.replay.replay_files import utc
from wirejudge.scoring.score_table import ScoreTable
from wirejudge.scoring.landing_scoring import DEFAULT_RULES,COMPONENTS,validate_rules,upgrade_rules,score_attempt,compact_sample
from wirejudge.approach.attempt_store import save_boundaries
from wirejudge.aircraft.pilot_identity import normalize_attempt_identity
from wirejudge.display_units import ALTITUDE_FACTORS,SPEED_FACTORS

PERSONAL_BEST = 'New Personal Best!'
CAW_RECORD = 'New CAW 8 Record!'


def landing_records(attempt, grade, records):
    """Use the same score fraction as the leaderboard's Overall Best filter."""
    if grade['provisional'] or grade['maximum'] <= 0 or not attempt.get('wire'):
        return ()
    fraction = grade['earned'] / grade['maximum']
    pilot = attempt['pilot'].casefold()
    identity = attempt.get('identity') or {}
    pilot_id = identity.get('player_id') or identity.get('steam_id')
    personal, overall = -1., -1.
    for record in records:
        if record['provisional'] or not record['maximum']:
            continue
        saved = record['score'] / record['maximum']
        overall = max(overall, saved)
        saved_id = record.get('pilot_id')
        same_pilot = (str(pilot_id) == str(saved_id) if pilot_id and saved_id
                      else record['pilot'].casefold() == pilot)
        if same_pilot:
            personal = max(personal, saved)
    labels = []
    if fraction > personal + 1e-9:
        labels.append(PERSONAL_BEST)
    if fraction > overall + 1e-9:
        labels.append(CAW_RECORD)
    return tuple(labels)


def read_recording(path, progress=None):
    """Rebuild compact tracks from original snapshots, never from cached grades."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        attempts=[]
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'samples','approaches'}<=tables:raise ValueError('This is not a Wire Judge Live recording.')
        from wirejudge.replay.replay_files import replay_info, replay_attempt_rows
        info=replay_info(path)
        for _, _, a in replay_attempt_rows(db, 'attempt_edits' in tables):
            if a.get('start_unix_s') is None and info.get('start_unix_s') is not None:
                a=dict(a,start_unix_s=info['start_unix_s']+a['start_s']-info['start_s'])
            attempts.append(normalize_attempt_identity(a))
        wanted={(a['connection_id'],a['aircraft_id'],a['carrier_id']):a for a in attempts}
        histories={key:[] for key in wanted}
        last_progress = time.monotonic()
        for count, (raw,) in enumerate(db.execute(
                'SELECT payload_json FROM samples ORDER BY received_monotonic_s,id'), 1):
            s=json.loads(raw);ships={c['instance_id']:c for c in s.get('carriers',[])}
            aircrafts={a['instance_id']:a for a in s.get('aircraft',[])}
            connection=s['capture']['connection_id']
            for key,a in wanted.items():
                if connection!=key[0] or key[1] not in aircrafts or key[2] not in ships:continue
                row=compact_sample(s,aircrafts[key[1]],ships[key[2]],a['glide_deg'],a['offset_deg'])
                if row:histories[key].append(row)
            if progress and count % 64 == 0:
                now = time.monotonic()
                if now - last_progress >= .08:
                    progress(count / max(1, info['samples']))
                    last_progress = now
        return attempts,histories


class ScoreWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True);self.tasks=queue.Queue();self.results=queue.SimpleQueue();self.cache={}
        self.stopping=threading.Event()
    def stop(self):self.stopping.set();self.tasks.put(None)
    def run(self):
        while True:
            task=self.tasks.get()
            if task is None:return
            kind,data=task
            try:
                if kind=='score':
                    revision,rows,rules,epoch=data;grades={}
                    rule_key=json.dumps(rules,sort_keys=True)
                    alive=set()
                    for attempt,samples in rows:
                        identifier=attempt['id'];alive.add(identifier)
                        key=(json.dumps(attempt,sort_keys=True),len(samples),samples[-1][0] if samples else None,rule_key)
                        cached=self.cache.get(identifier)
                        if cached and cached[0]==key:grade=cached[1]
                        else:grade=score_attempt(attempt,samples,rules);self.cache[identifier]=(key,grade)
                        grades[identifier]=grade
                    self.cache={k:v for k,v in self.cache.items() if k in alive}
                    result=dict(kind=kind,grades=grades,rows=[a for a,_ in rows],revision=revision,epoch=epoch,rules=rules)
                elif kind=='timeline':
                    key, timestamp, attempt, samples, rules, initialize = data
                    grade = score_attempt(attempt, samples, rules)
                    baseline = None
                    if initialize:
                        earlier = timestamp - .5
                        baseline = score_attempt(dict(attempt, last_s=earlier, wire=None), samples, rules)
                    result = dict(kind=kind, key=key, timestamp=timestamp, grade=grade, baseline=baseline)
                elif kind=='open':
                    attempts,histories=read_recording(data)
                    result=dict(kind=kind,path=data,attempts=attempts,histories=histories)
                elif kind=='edit':
                    path,attempt,values=data
                    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=5)) as db:
                        metadata,points=save_boundaries(db,attempt,**values)
                    result=dict(kind=kind,metadata=metadata,points=points)
                self.results.put(result)
            except Exception as exc:self.results.put(dict(kind=kind,error=str(exc)))


class RulesDialog(QDialog):
    def __init__(self,rules,units,parent):
        super().__init__(parent);self.setWindowTitle('Landing scoring settings');self.resize(650,650)
        self.original=copy.deepcopy(rules);self.controls={};self.units=units
        outer=QVBoxLayout(self)
        hint=QLabel('Apply recalculates every active and past attempt. Distances are measured inbound from wire 3; platform distances use DME.')
        hint.setWordWrap(True);outer.addWidget(hint)
        tabs=QTabWidget();outer.addWidget(tabs,1)
        for case in ('Case 1','Case 3'):
            content=QWidget();layout=QVBoxLayout(content)
            for title,keys in [('Point values',[k for k in COMPONENTS if k in rules[case]]),
                               ('Graded legs',[k for k in rules[case] if k.endswith('_nm')])]:
                group=QGroupBox(title);form=QFormLayout(group);layout.addWidget(group)
                names={'start_nm':'Final start','end_nm':'Final stop','platform_start_nm':'Platform start','platform_end_nm':'Platform stop'}
                for key in keys:
                    distance=key.endswith('_nm');factor=units.distance_nm(1) if distance else 1.
                    self.add(form,case,key,names.get(key,key),factor,units.distance_symbol if distance else 'points',30 if distance else 100000)
            layout.addStretch();tabs.addTab(content,case)
        content=QWidget();layout=QVBoxLayout(content);group=QGroupBox('Targets and zero-credit deviation');form=QFormLayout(group);layout.addWidget(group)
        names={'glide_target':'Target glideslope', 'glide_tolerance':'Glide deviation','loc_tolerance':'Localizer deviation','aoa_target':'Target AoA','aoa_tolerance':'AoA deviation',
               'platform_alt_ft':'Platform altitude ASL','platform_alt_tolerance_ft':'Platform altitude deviation',
               'speed_outer_kt':'Outer platform groundspeed','speed_inner_kt':'Inner platform groundspeed','speed_change_nm':'Speed transition center',
               'speed_deadzone_nm':'Ungraded transition width','speed_tolerance_kt':'Groundspeed deviation'}
        for key in rules['targets']:
            if key.endswith('_ft'):factor=.3048*ALTITUDE_FACTORS[units.altitude_name];suffix=units.altitude_symbol;maximum=50000
            elif key.endswith('_kt'):factor=(1852/3600)*SPEED_FACTORS[units.speed_name];suffix=units.speed_symbol;maximum=2000
            elif key.endswith('_nm'):factor=units.distance_nm(1);suffix=units.distance_symbol;maximum=30
            else:factor=1.;suffix='°';maximum=180
            self.add(form,'targets',key,names[key],factor,suffix,maximum)
        layout.addStretch();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(content);tabs.addTab(scroll,'Targets')
        content=QWidget();form=QFormLayout(content)
        note=QLabel('Award as a percentage of that case’s Wire points.');note.setWordWrap(True);form.addRow(note)
        for key in rules['wires']:self.add(form,'wires',key,key,100.,'%',1)
        tabs.addTab(content,'Wires')
        self.error=QLabel();self.error.setWordWrap(True);outer.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject);outer.addWidget(buttons)
    def add(self,form,section,key,title,factor,suffix,maximum):
        spin=QDoubleSpinBox();spin.setDecimals(3 if suffix not in ('points','%') else 1)
        spin.setRange(0,maximum*factor);spin.setValue(self.original[section][key]*factor)
        spin.setSuffix(' '+suffix);spin.setKeyboardTracking(False);spin.setMaximumWidth(230)
        spin.setSingleStep(factor*(.05 if key.endswith('_nm') else 1.))
        self.controls[(section,key)]=(spin,factor);form.addRow(title,spin)
    def apply(self):
        rules=copy.deepcopy(self.original)
        for (section,key),(spin,factor) in self.controls.items():rules[section][key]=spin.value()/factor
        try:self.rules=validate_rules(rules)
        except ValueError as exc:self.error.setText(str(exc));return
        self.accept()


class EditScoreDialog(QDialog):
    def __init__(self,attempt,origin,parent):
        super().__init__(parent);self.setWindowTitle('Edit landing attempt');self.origin=origin
        layout=QVBoxLayout(self);layout.addWidget(QLabel(attempt['pilot']));form=QFormLayout();layout.addLayout(form)
        self.original_start=attempt['start_s'];self.original_end=attempt.get('end_s') or attempt['last_s']
        self.start=QDoubleSpinBox();self.end=QDoubleSpinBox()
        for spin,value,title in ((self.start,attempt['start_s'],'Start'),(self.end,attempt.get('end_s') or attempt['last_s'],'End')):
            spin.setDecimals(3);spin.setRange(0,1e9);spin.setValue(value-origin);spin.setSuffix(' s');form.addRow(title,spin)
        self.display_start=self.start.value();self.display_end=self.end.value()
        self.case=QComboBox();self.case.addItems(['Case 1','Case 3']);self.case.setCurrentText(attempt.get('case','Case 1'));form.addRow('Case',self.case)
        self.outcome=QComboBox();self.outcome.addItems(['Keep detected result','Bolter','Wire 1','Wire 2','Wire 3','Wire 4','Aborted','Waveoff','Ended manually']);form.addRow('Result',self.outcome)
        self.follow_live=QCheckBox('Keep scoring this active attempt');self.follow_live.setChecked(attempt.get('end_s') is None);self.follow_live.setVisible(attempt.get('end_s') is None)
        layout.addWidget(self.follow_live);self.outcome.currentIndexChanged.connect(lambda i:self.follow_live.setChecked(False) if i else None);self.follow_live.toggled.connect(lambda checked:self.end.setEnabled(not checked));self.end.setEnabled(not self.follow_live.isChecked())
        note=QLabel('Saving an end point closes this edited time range and recalculates it. The original detected attempt and telemetry are preserved.');note.setWordWrap(True);layout.addWidget(note)
        self.error=QLabel();layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def validate(self):
        if self.start.value()>=self.end.value():self.error.setText('Start must be before end.');return
        self.accept()
    def values(self):
        return dict(start=self.original_start if self.start.value()==self.display_start else self.start.value()+self.origin,
                    end=self.original_end if self.end.value()==self.display_end else self.end.value()+self.origin,
                    case=self.case.currentText(),outcome=None if self.outcome.currentIndex()==0 else self.outcome.currentText(),follow_live=self.follow_live.isChecked(),allow_missing=True)


class ScoreView(QWidget):
    def __init__(self, kind, manager):
        super().__init__()
        manager.view = self
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 9)
        self.pilot = QLabel('No active landing attempt')
        self.pilot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pilot)
        self.odometer = ScoreOdometer(manager.owner.map)
        self.odometer.setToolTip('Double-click to edit this attempt. Color shows the share of newly available approach points earned.')
        self.odometer.edit_requested.connect(lambda: manager.edit_selected(self.identifier))
        layout.addWidget(self.odometer, 1)
        self.identifier = None
        self.previous_grade = None
        self.quality = None

    @staticmethod
    def approach_points(grade):
        components = [value for name, value in grade['components'].items() if name != 'Wire']
        earned = sum(value['earned'] for value in components)
        return earned, earned + sum(value['lost'] for value in components)

    def show_attempt(self, attempt, grade, mode, epoch, scored_time, baseline=None):
        identifier = attempt['id'] if attempt else None
        key = (identifier, mode, epoch)
        if key != self.odometer.key:
            self.previous_grade = None
            self.quality = None
        self.identifier = identifier
        label = attempt['pilot'] if attempt else 'No active landing attempt'
        if label != self.pilot.text():
            self.pilot.setText(label)
        score = None
        wire_scored = grade is not None and not grade['wire_pending']
        if grade:
            earned, available = self.approach_points(grade)
            wire = grade['components']['Wire']
            if available > 1e-6 or wire['earned'] > 0 or wire['lost'] > 0:
                score = grade['earned'] if mode == 'Additive' else grade['deductive']
            if self.previous_grade is None and baseline is not None:
                old_earned, old_available = self.approach_points(baseline)
                self.previous_grade = (scored_time - .5, old_earned, old_available)
            if self.previous_grade is not None and scored_time > self.previous_grade[0]:
                _, old_earned, old_available = self.previous_grade
                offered = available - old_available
                self.quality = min(1., max(0., (earned - old_earned) / offered)) if offered > 1e-6 else None
            self.previous_grade = (scored_time, earned, available)
        quality = self.quality
        if wire_scored:
            quality = score / grade['maximum'] if score is not None and grade['maximum'] > 0 else None
        self.odometer.set_reading(key if identifier is not None else None, score, quality, final=wire_scored)


class ScoringTool(QObject):
    grades_changed=Signal()
    rules_changed=Signal()
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.epoch=0;self.grades={};self.attempts={};self.busy=False;self.last_request=0.;self.request_key=None
        self.graded_rules = None
        self.worker=ScoreWorker();self.worker.start()
        self.timeline_busy = False
        self.timeline_result = None
        self.landing_cursor = None
        self.held_landing = None
        self.record_cursor = None
        self.pending_record_checks = {}
        self.record_awards = {}
        self.session_records = []
        self.timeline_request = None
        self.timeline_requested_at = 0.
        try:self.rules=upgrade_rules(json.loads(owner.settings.store.value('scoring/rules',json.dumps(DEFAULT_RULES))))
        except (ValueError,TypeError):self.rules=copy.deepcopy(DEFAULT_RULES)
        self.mode=owner.settings.store.value('scoring/mode','Additive')
        if self.mode not in ('Additive','Deductive'):self.mode='Additive'
        owner.capture.configure_scoring(self.rules)
        self.pane=ScopePane('Live Scoring','scoring',self,view_factory=ScoreView)
        self.volume_slider = self.pane.header.add_volume('Live Scoring volume')
        self.volume_slider.setValue(owner.settings.store.value('scoring/volume', 100, type=int))
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.set_volume(self.volume_slider.value())
        owner.map.installEventFilter(self);self.board_tree=None;self.status=None
        self.layout_panes()
    def set_volume(self, volume):
        self.view.odometer.set_volume(volume)
        self.volume_slider.setToolTip(f'Live Scoring volume: {volume}%')
        self.owner.settings.store.setValue('scoring/volume', volume)

    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.layout_panes()
        return False
    def layout_panes(self):
        if not hasattr(self,'pane') or self.pane.detached:return
        view=self.owner.map;w,h=self.pane.user_size or (360,210)
        self.pane.place(8,max(8,view.height()-h-8),min(w,max(246,view.width()-16)),min(h,max(140,view.height()-24)))
        self.pane.raise_();self.owner.map_controls.raise_()
    def sync_timer(self):pass
    def board(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.scoreboard = ScoreTable()
        self.board_tree = self.scoreboard.tree
        layout.addWidget(self.scoreboard, 1)
        self.board_tree.itemDoubleClicked.connect(self.open_attempt_editor)
        row = QHBoxLayout()
        edit = QPushButton('Edit Attempt')
        edit.clicked.connect(lambda: self.open_attempt_editor())
        row.addWidget(edit)
        b=QPushButton('Review approach graphs');b.clicked.connect(self.review_selected);row.addWidget(b)
        for title,slot in [('Save attempts to Leaderboards',lambda:self.owner.leaderboards.save_scoreboard()),('Export scoreboard',lambda:self.owner.leaderboards.export_scoreboard())]:
            b=QPushButton(title);b.clicked.connect(slot);row.addWidget(b)
        row.addStretch();layout.addLayout(row)
        return page
    def set_board_status(self, text=''):
        if self.status:
            self.status.setText(text)
            self.status.setVisible(bool(text))

    def open_attempt_editor(self, item=None, column=0):
        item = item or self.board_tree.currentItem()
        identifier = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if identifier is None:
            return
        editor = self.owner.attempt_editor
        tabs = self.owner.tabs
        tabs.setTabEnabled(tabs.indexOf(editor), True)
        tabs.setCurrentWidget(editor)
        editor.refresh(force=True, origin=self.owner.playback.start)
        if identifier in editor.items:
            target = editor.items[identifier]
            target.parent().setExpanded(True)
            editor.tree.setCurrentItem(target)
            editor.tree.scrollToItem(target)

    def visible_board_ids(self):
        return {record['attempt_id'] for record in self.scoreboard.visible_records}

    def review_selected(self):
        item=self.board_tree.currentItem();identifier=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if identifier is None:return
        self.owner.tabs.setCurrentIndex(1);view=self.owner.approach_view;view.refresh(force=True)
        if identifier in view.items:view.tree.setCurrentItem(view.items[identifier])
    def set_mode(self,mode):
        self.mode=mode;self.owner.settings.store.setValue('scoring/mode',mode)
        self.render()
    def settings_dialog(self):
        dialog=RulesDialog(self.rules,self.owner.units,self.owner)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        self.set_rules(dialog.rules)

    def set_rules(self, rules):
        if rules == self.rules:
            return
        self.rules=copy.deepcopy(rules);self.epoch+=1;self.request_key=None
        self.owner.settings.store.setValue('scoring/rules',json.dumps(self.rules));self.owner.settings.store.sync()
        self.owner.capture.configure_scoring(self.rules)
        self.rules_changed.emit()
        self.set_board_status('Recalculating active and past attempts…')
        self.refresh(force=True)
    def open_recording(self):
        path,_=QFileDialog.getOpenFileName(self.owner,'Open Wire Judge Live recording',self.owner.settings.get('recording_folder'),'Wire Judge recordings (*.wjlive.sqlite *.sqlite)')
        if not path:return
        self.worker.tasks.put(('open',path));self.set_board_status('Reading recorded telemetry…')
    def edit_selected(self, identifier):
        attempt=self.attempts.get(identifier)
        if not attempt:return
        # Resolve fresh metadata; the UI may be one scoring frame behind capture.
        attempt=next((a for a in self.owner.capture.approach_catalog()[1] if a['id']==identifier),attempt)
        origin=self.owner.playback.start
        if origin is None or origin>attempt['start_s']:origin=min(a['start_s'] for a in self.attempts.values())
        dialog=EditScoreDialog(attempt,origin,self.owner)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        path=self.owner.capture.attempt_path(identifier)
        if path:
            self.worker.tasks.put(('edit',(path,attempt,dialog.values())))
        else:
            try:self.owner.capture.edit_attempt_memory(attempt,**dialog.values())
            except ValueError as exc:self.owner.notify(str(exc));return
            self.request_key=None;self.refresh(force=True)
    def refresh(self,force=False):
        while True:
            try:r=self.worker.results.get_nowait()
            except queue.Empty:break
            if r['kind']=='score':self.busy=False
            if r['kind']=='timeline':self.timeline_busy=False
            if r.get('error'):
                self.owner.notify(r['error'])
                self.set_board_status(r['error'])
                continue
            if r['kind']=='score':
                if r['epoch']!=self.epoch:continue
                self.graded_rules = r['rules']
                self.grades=r['grades'];self.attempts={a['id']:a for a in r['rows']};self.render()
                self.set_board_status()
            elif r['kind']=='timeline':
                self.timeline_result = r
            elif r['kind']=='edit':
                self.owner.capture.install_approach_edit(r['metadata'],r['points']);self.request_key=None
                self.owner.notify('Attempt saved. Recalculating its score.')
            elif r['kind']=='open':
                self.owner.capture.install_archive(r['path'],r['attempts'],r['histories']);self.request_key=None
                for i in (1,2,3):self.owner.tabs.setTabEnabled(i,True)
                self.set_board_status()
        now=time.monotonic()
        if self.busy or (not force and now-self.last_request<.5):return
        revision=self.owner.capture.approach_catalog()[0];key=(revision,self.epoch)
        if key==self.request_key:return
        revision,rows=self.owner.capture.score_inputs();self.request_key=key;self.last_request=now;self.busy=True
        self.worker.tasks.put(('score',(revision,rows,copy.deepcopy(self.rules),self.epoch)))
    def render(self):
        records = []
        visible = self.owner.replay_manager.visible_ids() if hasattr(self.owner, 'replay_manager') else set(self.attempts)
        for identifier, attempt in self.attempts.items():
            grade = self.grades.get(identifier)
            if not grade:
                continue
            additive = self.mode == 'Additive'
            points = grade['earned'] if additive else grade['deductive']
            active = attempt.get('end_s') is None
            if identifier in visible:
                start = attempt.get('start_unix_s')
                if start is not None:
                    start += attempt['start_s'] - attempt.get('detected_start_s', attempt['start_s'])
                components = {key: {'score': value['earned'] if additive else value['maximum'] - value['lost'],
                                    'maximum': value['maximum']}
                              for key, value in grade['components'].items()}
                records.append(dict(
                    attempt_id=identifier, pilot=attempt['pilot'], aircraft=attempt.get('airframe'),
                    carrier=attempt.get('carrier'), started_utc=utc(start),
                    case=attempt.get('case', 'Case 1'), outcome=attempt['outcome'],
                    attempt_label=f"{attempt['outcome']} · {attempt.get('case', 'Case 1')}",
                    score=points, maximum=grade['maximum'], components=components,
                    status='Active' if active else 'Final', provisional=grade['provisional'],
                    final_reference=grade.get('final_reference'),
                    wire_pending=grade['wire_pending'] and not grade['complete'],
                ))
        if self.board_tree is not None:
            self.scoreboard.set_records(records)
        for view in (self.owner.approach_view, self.owner.attempt_editor):
            view.scores = self.grades
            view.score_mode = self.mode

    @staticmethod
    def active_attempt(sample, attempts, timestamp, selected):
        aircraft = {a['instance_id']: a for a in sample.get('aircraft', [])}
        carriers = {c['instance_id']: c for c in sample.get('carriers', [])}
        connection = sample['capture']['connection_id']
        best = None
        nearest = None
        for attempt in attempts:
            identifier = attempt['aircraft_id']
            end = attempt.get('end_s')
            if (attempt['connection_id'] != connection or identifier not in aircraft
                    or timestamp < attempt['start_s'] or (end is not None and timestamp >= end)):
                continue
            position = (aircraft[identifier].get('kinematics') or {}).get('position_world_m')
            ship = carriers.get(attempt['carrier_id'], {})
            volume = case1_volume(ship, attempt['glide_deg'], attempt['offset_deg'])
            landing_point = volume['origin'] if volume else ship.get('position_world_m')
            distance = math.dist(position, landing_point) if point(position) and point(landing_point) else math.inf
            rank = (identifier != selected, distance, attempt['id'])
            if best is None or rank < best:
                best, nearest = rank, attempt
        return nearest

    def update_landing_sound(self, sample, attempts, timestamp, playback_epoch, attempt):
        previous = self.landing_cursor
        self.landing_cursor = None
        odometer = self.view.odometer
        if sample is None or timestamp is None or not odometer.isVisible():
            if timestamp is None and getattr(self.owner, 'seek_pending', False):
                self.clear_held_landing()
                odometer.stop_sound()
            return
        context = playback_epoch, sample['capture']['connection_id']
        self.landing_cursor = context, timestamp, attempt['id'] if attempt else None
        if previous is None:
            return
        previous_context, previous_time, followed_id = previous
        if previous_context != context or timestamp < previous_time:
            self.clear_held_landing()
            odometer.stop_sound()
            return
        if timestamp == previous_time:
            return
        fallback_catch = attempt.get('wire_catch_s') if attempt and attempt.get('gate_missed') else None
        followed = (attempt if fallback_catch is not None and previous_time < fallback_catch <= timestamp
                    else next((row for row in attempts if row['id'] == followed_id), None))
        if not followed:
            return
        catch = followed.get('wire_catch_s')
        end = followed.get('end_s')
        outcome = followed['outcome']
        if catch is not None and previous_time < catch <= timestamp:
            wire_outcome = f"Wire {followed.get('wire')}"
            odometer.play_landing_sound(wire_outcome)
            odometer.celebrate_wire(wire_outcome)
        if end is not None and previous_time < end <= timestamp and outcome in LANDING_SOUNDS:
            if catch is None or outcome == 'Bolter':
                odometer.play_landing_sound(outcome)
                odometer.celebrate_wire(outcome)
            self.held_landing = followed, time.monotonic(), playback_epoch

    def clear_held_landing(self):
        self.held_landing = None
        self.view.odometer.clear_landing_display()

    def update_record_celebrations(self, sample, attempts, timestamp, playback_epoch, *, advancing):
        previous = self.record_cursor
        if sample is None or timestamp is None:
            self.record_cursor = None
            self.pending_record_checks.clear()
            return
        connection = sample['capture']['connection_id']
        context = playback_epoch, connection
        self.record_cursor = context, timestamp
        if previous is None or previous[0] != context or timestamp < previous[1]:
            self.pending_record_checks.clear()
            return
        if not advancing:
            return
        if previous[1] < timestamp:
            for attempt in attempts:
                end = attempt.get('end_s')
                identifier = attempt['id']
                if (attempt['connection_id'] == connection and attempt.get('wire') and end is not None
                        and previous[1] < end <= timestamp and identifier not in self.record_awards):
                    self.pending_record_checks[identifier] = attempt

        leaders = self.owner.leaderboards
        if not leaders.loaded or not self.pending_record_checks:
            return
        for identifier, attempt in sorted(self.pending_record_checks.items(), key=lambda entry: entry[1]['end_s']):
            grade = self.grades.get(identifier)
            if not grade or not grade['complete']:
                # Resolve landings in order so a delayed grade can't be overtaken by a later record.
                break
            del self.pending_record_checks[identifier]
            labels = landing_records(attempt, grade, chain(leaders.records, self.session_records))
            self.record_awards[identifier] = labels
            if labels:
                identity = attempt.get('identity') or {}
                self.session_records.append(dict(
                    pilot=attempt['pilot'], pilot_id=identity.get('player_id') or identity.get('steam_id'),
                    score=grade['earned'], maximum=grade['maximum'], provisional=False))
                caw_record = CAW_RECORD in labels
                self.owner.map.holidays.celebrate_record(attempt['carrier_id'], caw_record)
                self.owner.deck_crew_tool.announce_record(attempt['pilot'], caw_record)

    def show_held_landing(self, playback_epoch):
        if self.held_landing is None:
            return False
        attempt, started, epoch = self.held_landing
        now = time.monotonic()
        if epoch != playback_epoch or now - started >= LANDING_HOLD_S + LANDING_FADE_S:
            self.clear_held_landing()
            return False
        odometer = self.view.odometer
        grade = self.grades.get(attempt['id'])
        if self.view.identifier != attempt['id']:
            odometer.set_reading(('landing', attempt['id'], self.mode), None, None, final=True)
        self.view.pilot.setText(attempt['pilot'])
        self.view.identifier = attempt['id']
        records = self.record_awards.get(attempt['id'], ())
        if grade and grade['complete']:
            points = grade['earned'] if self.mode == 'Additive' else grade['deductive']
            quality = points / grade['maximum'] if grade['maximum'] > 0 else None
            odometer.set_reading(('landing', attempt['id'], self.mode), points, quality, final=True)
        odometer.set_landing_labels(attempt['outcome'], records)
        odometer.hold_landing(started, now)
        return True

    def update_timeline_score(self, sample, attempts, timestamp, selected, playback_epoch):
        if not self.pane.enabled:
            self.landing_cursor = None
            self.clear_held_landing()
            return
        attempt = self.active_attempt(sample, attempts, timestamp, selected) if sample and timestamp is not None else None
        self.update_landing_sound(sample, attempts, timestamp, playback_epoch, attempt)
        if self.show_held_landing(playback_epoch):
            return
        if not attempt:
            self.view.show_attempt(None, None, self.mode, self.epoch, None)
            self.view.odometer.set_landing_labels()
            return

        # Score a prefix as an in-progress attempt. The saved finish and future
        # wire catch must never influence an earlier frame.
        catch = attempt.get('wire_catch_s')
        wire = attempt.get('wire') if catch is not None and catch <= timestamp else None
        self.view.odometer.set_landing_labels(f'Wire {wire}' if wire else None)
        key = (playback_epoch, self.epoch, attempt['id'], attempt['start_s'], attempt.get('case'))
        request = (key, timestamp, wire)
        result = self.timeline_result
        if result and result['key'] == key and result['timestamp'] <= timestamp:
            grade, scored_time, baseline = result['grade'], result['timestamp'], result['baseline']
        else:
            grade, scored_time, baseline = None, None, None
        self.view.show_attempt(attempt, grade, self.mode, key, scored_time, baseline)

        now = time.monotonic()
        if self.timeline_busy or request == self.timeline_request:
            return
        if self.timeline_request and key == self.timeline_request[0] and now - self.timeline_requested_at < .15:
            return
        prefix = dict(attempt, last_s=timestamp, end_s=None, outcome='Active', wire=wire)
        samples = self.owner.capture.timeline_score_samples(attempt, timestamp)
        initialize = grade is None or self.view.previous_grade is None
        self.timeline_busy = True
        self.timeline_request = request
        self.timeline_requested_at = now
        self.worker.tasks.put(('timeline', (key, timestamp, prefix, samples, self.rules, initialize)))

    def apply_theme(self):
        self.pane.header.refresh_volume_icon()
        self.view.odometer.apply_theme()
        if self.pane.floating_window:self.pane.floating_window.setStyleSheet(self.owner.styleSheet())
        self.render()
    def shutdown(self):
        self.pane.save_layout();self.worker.stop()
        if self.pane.floating_window:self.pane.floating_window.hide()
        self.pane.hide()
