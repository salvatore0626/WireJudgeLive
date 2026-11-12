"""Persistent score-only attempt library with sharing, filtering and deletion."""
from datetime import datetime
from pathlib import Path
import queue
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QFileDialog, QMessageBox
from wirejudge.scoring.score_table import ScoreTable
from wirejudge.ui.background_files import FileWorker
from wirejudge.scoring.leaderboard_store import LeaderboardStore,score_record,import_scores,export_scores
from wirejudge.scoring.landing_scoring import DEFAULT_RULES
from wirejudge.paths import leaderboard_path


class Leaderboards:
    def __init__(self,owner):
        self.owner=owner;self.records=[];self.worker=FileWorker();self.worker.start();self.pending=False
        self.loaded = False
        self.path = str(leaderboard_path(owner.settings.store.value('leaderboards/path')))
        self.widget=QWidget();layout=QVBoxLayout(self.widget)
        self.table = ScoreTable(leaderboard=True)
        self.tree = self.table.tree
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in [('Import', self.import_dialog), ('Export', self.export_dialog),
                           ('Open Replay', self.open_linked), ('Delete', self.delete_selected)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
            if text == 'Open Replay':
                self.open_replay_button = button
                button.setEnabled(False)
        self.tree.itemSelectionChanged.connect(self.update_open_replay)
        self.last_link_check = 0.
        row.addStretch()
        self.scope = QComboBox()
        self.scope.addItems(['All saved attempts', 'Visible attempts', 'Selected attempts'])
        row.addWidget(self.scope)
        layout.addLayout(row)
        self.worker.request('load',self.read_records)
    def update_open_replay(self):
        manager = getattr(self.owner, 'replay_manager', None)
        records = self.selected_records() if self.tree.selectedItems() else []
        enabled = bool(manager and len(records) == 1 and manager.linked_replay_path(records[0]))
        self.open_replay_button.setEnabled(enabled)
        self.open_replay_button.setToolTip('Open the linked recording' if enabled else
                                           'Select one attempt with a local replay. Add missing files from Replays.')

    def open_linked(self):
        records=self.selected_records()
        if len(records)!=1:self.owner.notify('Select one saved attempt to open its linked replay.');return
        self.owner.replay_manager.open_score_record(records[0])
    def merge_records(self,records,replace=False,check_utc=False):
        return LeaderboardStore(self.path).merge(records,replace,check_utc)
    def delete_records(self,identifiers):return LeaderboardStore(self.path).delete(identifiers)
    def read_records(self):return LeaderboardStore(self.path).records()

    def merge(self,records):
        self.worker.request('merge',self.merge_records,records)
    def poll(self):
        now = time.monotonic()
        if self.widget.isVisible() and now - self.last_link_check >= 1:
            self.last_link_check = now
            self.update_open_replay()
        while True:
            try:r=self.worker.results.get_nowait()
            except queue.Empty:break
            if r.get('error'):self.owner.notify(r['error']);continue
            value=r.get('value')
            if r['kind'] == 'load':
                self.owner.update_leaderboard_storage_path()
            if r['kind'] in ('load','refresh'):
                self.loaded = True
                self.records=value;self.table.set_records(value)
            elif r['kind'] in ('merge','replace','import_merge'):
                conflicts=value['conflicts']
                text=f"{value['added']} added · {value['duplicates']} duplicates skipped · {value['updated']} updated"
                self.owner.notify(text)
                if value['utc_matches']:
                    self.review_utc_matches(value['utc_matches'])
                if conflicts:
                    answer=QMessageBox.question(self.owner,'Conflicting saved attempts',f'{len(conflicts)} attempt IDs have different saved scores or edits. Replace your saved versions with these incoming versions?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
                    if answer==QMessageBox.StandardButton.Yes:self.worker.request('replace',self.merge_records,conflicts,True)
                self.worker.request('refresh',self.read_records)
            elif r['kind']=='import':
                if self.allow_records(value):
                    self.worker.request('import_merge',self.merge_records,value,False,True)
            elif r['kind']=='delete':self.worker.request('refresh',self.read_records)
            elif r['kind']=='export':self.owner.notify('Leaderboard data exported. No telemetry included.')
    def review_utc_matches(self, records):
        dialog = QMessageBox(self.owner)
        dialog.setWindowTitle('Possible duplicate attempts')
        dialog.setText(f'{len(records)} incoming attempts are within 5 seconds UTC of another attempt.')
        dialog.setInformativeText('Other imports have been saved. Choose whether to keep these flagged attempts too.')
        keep = dialog.addButton('Import Anyway', QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton('Discard Flagged', QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(discard)
        dialog.exec()
        if dialog.clickedButton() == keep:
            self.merge(records)
        else:
            self.owner.notify(f'Discarded {len(records)} flagged attempts.')
    def selected_records(self):
        identifiers={i.data(0,Qt.ItemDataRole.UserRole) for i in self.tree.selectedItems()}
        return [r for r in self.records if r['attempt_id'] in identifiers]
    def export_dialog(self):
        records=self.records if self.scope.currentIndex()==0 else self.table.visible_records if self.scope.currentIndex()==1 else self.selected_records()
        self.export_records(records)
    def export_records(self,records):
        if not records:self.owner.notify('There are no attempts to export.');return
        if not self.allow_records(records):
            return
        name='Leaderboard_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.wjleaderboard.json'
        path,_=QFileDialog.getSaveFileName(self.owner,'Export leaderboard data',str(Path(self.owner.settings.get('recording_folder'))/name),'Leaderboard data (*.wjleaderboard.json)')
        if path:
            if not path.endswith('.json'):path+='.wjleaderboard.json'
            self.worker.request('export',export_scores,path,records)
    def import_dialog(self):
        path,_=QFileDialog.getOpenFileName(self.owner,'Import leaderboard data',self.owner.settings.get('recording_folder'),'Leaderboard data (*.wjleaderboard.json *.json)')
        if path:self.worker.request('import',import_scores,path)
    def scoreboard_records(self):
        scorer=self.owner.scoring_tool;ids=scorer.visible_board_ids() & self.owner.replay_manager.visible_ids();records=[]
        for identifier,a in scorer.attempts.items():
            if identifier not in ids or identifier not in scorer.grades:continue
            records.append(score_record(a,scorer.grades[identifier],scorer.graded_rules,self.owner.replay_manager.info_for_attempt(a)))
        return records

    def allow_records(self, records):
        if any(record.get('rules') != DEFAULT_RULES for record in records):
            QMessageBox.warning(self.owner, 'Default scoring required',
                                'This selection contains scores calculated with custom or unknown scoring rules. '
                                'Use scores calculated with the default rules for Leaderboards.')
            return False
        return True

    def allow_scoreboard(self):
        scorer = self.owner.scoring_tool
        if scorer.rules != DEFAULT_RULES:
            QMessageBox.warning(self.owner, 'Default scoring required',
                                'Leaderboards only accepts scores calculated with the default scoring rules. '
                                'Restore the defaults in Scoring Rules and let the app recalculate the scores.')
            return False
        if scorer.grades and scorer.graded_rules != DEFAULT_RULES:
            QMessageBox.warning(self.owner, 'Scores are recalculating',
                                'Wait for the scores to finish recalculating with the default scoring rules.')
            return False
        return True

    def save_scoreboard(self):
        if not self.allow_scoreboard():
            return
        try:records=self.scoreboard_records()
        except ValueError as exc:self.owner.notify(str(exc));return
        if not records:self.owner.notify('No visible scored attempts to save.');return
        count = len(records)
        noun = 'attempt' if count == 1 else 'attempts'
        answer = QMessageBox.question(
            self.owner, 'Save to Leaderboards',
            f'Are you sure you want to save {count} {noun} to Leaderboards?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.merge(records)
    def export_scoreboard(self):
        if not self.allow_scoreboard():
            return
        try:records=self.scoreboard_records()
        except ValueError as exc:self.owner.notify(str(exc));return
        self.export_records(records)
    def delete_selected(self, checked=False):
        records = self.selected_records()
        if not records:return
        count = len(records)
        noun = 'record' if count == 1 else 'records'
        if QMessageBox.question(self.owner,'Delete saved attempts',f"Delete {count} selected scoring {noun}?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.worker.request('delete',self.delete_records,[r['attempt_id'] for r in records])
    def shutdown(self):self.worker.stop()
