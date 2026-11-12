"""Always-record UI, named replay snapshots, and independent replay viewing."""
import copy
from datetime import datetime
from pathlib import Path
import queue
import json
import time
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QHBoxLayout,QVBoxLayout,QLabel,QPushButton,QFileDialog,
                               QDialog,QDialogButtonBox,QInputDialog,QTreeWidget,QTreeWidgetItem,QMessageBox)
from wirejudge.ui.background_files import FileWorker
from wirejudge.replay.replay_loading import ReplayLoading
from wirejudge.replay.replay_files import (list_replays, rename_replay, replay_info, save_replay, delete_replay,
                          trim_preview, trim_replay, cleanup_candidates, delete_cleanup_candidates)
from wirejudge.replay.replay_trim import ReplayTrimDialog
from wirejudge.ui.theme import color


def local_stamp(value):
    return datetime.fromtimestamp(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') if value is not None else 'Waiting for samples'


def load_replay(path, progress=None):
    from wirejudge.scoring.scoring_tool import read_recording
    info=replay_info(path)
    if not info['samples']:raise ValueError('This replay has no recorded telemetry yet.')
    attempts,histories=read_recording(path, progress)
    return info,attempts,histories


class ReplayManager:
    def __init__(self,owner):
        self.owner=owner;self.worker=FileWorker();self.worker.start();self.infos={};self.live_path=None;self.info=None;self.loading=False
        try:self.index=json.loads(owner.settings.store.value('replays/index','{}'))
        except (ValueError,TypeError):self.index={}
        if not isinstance(self.index,dict):self.index={}
        self.imported_paths = set(owner.settings.store.value('replays/imported_paths', [], type=list))
        self.browser = None
        self.browser_rows = []
        self.scanning = False
        self.last_scan = 0.
        self.rescan = False
        self.cancel_load = False
        self.loading_view = ReplayLoading(owner)
        self.waiting_first_frame = False
        self.deleting_path = None
        self.trimming = None
        self.saving = False
        self.cleaning = False
        self.cleanup_paths = set()
        self.return_live = QPushButton('Return to Live')
        self.return_live.clicked.connect(owner.go_live)
        self.return_live.hide()
    def remember(self,info):
        self.index[info['recording_id']]=info['path']
        self.owner.settings.store.setValue('replays/index',json.dumps(self.index))

    def connected_file(self,path):
        self.live_path=path
        if not self.owner.external_replay:
            if self.owner.dvr_path!=path:self.owner.reset_playback()
            self.owner.dvr_path=path
        self.refresh_browser()
        self.update_banner()
    def forget_deleted(self, path):
        target = Path(path).resolve()
        index = {key: value for key, value in self.index.items()
                 if isinstance(value, str) and Path(value).resolve() != target}
        if index != self.index:
            self.index = index
            self.owner.settings.store.setValue('replays/index', json.dumps(index))
        self.infos.pop(path, None)
        if str(target) in self.imported_paths:
            self.imported_paths.remove(str(target))
            self.owner.settings.store.setValue('replays/imported_paths', sorted(self.imported_paths))
        if self.live_path and Path(self.live_path).resolve() == target:
            self.live_path = None
        if self.owner.dvr_path and Path(self.owner.dvr_path).resolve() == target:
            if self.owner.external_replay:
                self.go_live()
            else:
                self.owner.dvr_path = None
                self.owner.reset_playback()
        self.browser_rows = [row for row in self.browser_rows if row['path'] != path]
        self.populate_browser()

    def current_info(self):
        if self.owner.external_replay:return self.info
        writer=self.owner.capture.writer
        if writer is None and self.live_path:
            writer=next((w for w in reversed(self.owner.capture.writers) if w.path and str(w.path)==self.live_path),None)
        if writer and writer.path:
            path=str(writer.path);known=self.infos.get(path,{})
            info=dict(known,path=path,recording_id=writer.recording_id,session_id=writer.session_id,
                      name=known.get('name',Path(path).name.removesuffix('.wjlive.sqlite')),start_s=writer.first_monotonic_s,
                      start_unix_s=writer.first_unix_s,end_unix_s=writer.last_unix_s)
            self.infos[path]=info
            if info['recording_id'] not in self.index or not Path(self.index[info['recording_id']]).is_file():self.remember(info)
            return info
        return self.infos.get(self.live_path)
    def info_for_attempt(self,attempt):
        path=self.owner.capture.attempt_path(attempt['id'])
        info=self.current_info()
        if info and (path==info.get('path') or (not path and not self.owner.external_replay)):return info
        return self.infos.get(path,{})
    def show_browser(self):
        if self.browser is None:
            self.browser = QDialog(self.owner)
            self.browser.setWindowTitle('Replays')
            self.browser.resize(800, 420)
            layout = QVBoxLayout(self.browser)
            self.replay_list = QTreeWidget()
            self.replay_list.setObjectName('replayList')
            self.replay_list.setRootIsDecorated(False)
            self.replay_list.setHeaderLabels(['Recording'])
            self.replay_list.itemDoubleClicked.connect(lambda *_: self.open_selected())
            self.replay_list.currentItemChanged.connect(self.update_inspector)
            body = QHBoxLayout()
            body.addWidget(self.replay_list, 3)
            self.inspector = QLabel('Select a recording to see its details.')
            self.inspector.setTextFormat(Qt.TextFormat.PlainText)
            self.inspector.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.inspector.setWordWrap(True)
            self.inspector.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            details = QVBoxLayout()
            details.addWidget(self.inspector, 1)
            actions = QHBoxLayout()
            for title, slot, attribute in (
                    ('Rename', self.rename_selected, 'rename_button'),
                    ('Trim', self.trim_selected, 'trim_button'),
                    ('Export', self.export_selected, 'export_button'),
                    ('Save', self.save_selected, 'save_button'),
                    ('Delete', self.delete_selected, 'delete_button')):
                button = QPushButton(title)
                button.clicked.connect(slot)
                actions.addWidget(button)
                setattr(self, attribute, button)
            details.addLayout(actions)
            body.addLayout(details, 2)
            layout.addLayout(body, 1)
            self.browser_status = QLabel()
            layout.addWidget(self.browser_status)
            controls = QHBoxLayout()
            for title, slot in (('Open',self.open_selected), ('Import',self.import_replay),
                                ('Clean Up…',self.scan_cleanup),
                                ('Close',self.browser.close)):
                button = QPushButton(title)
                button.clicked.connect(slot)
                controls.addWidget(button)
                if title == 'Open':
                    self.open_button = button
                elif title == 'Clean Up…':
                    self.cleanup_button = button
            self.update_inspector()
            layout.addLayout(controls)
        self.browser.show()
        self.browser.raise_()
        self.refresh_browser()

    def refresh_browser(self):
        if self.browser is None or not self.browser.isVisible():
            return
        if self.scanning:
            self.rescan = True
            return
        self.scanning = True
        self.last_scan = time.monotonic()
        if not self.browser_rows:
            self.browser_status.setText('Checking recordings…')
        self.worker.request('list', list_replays, self.owner.settings.get('recording_folder'), tuple(self.index.values()))

    def populate_browser(self):
        if self.browser is None:
            return
        selected = self.selected_row()
        active_path = self.active_path()
        rows = sorted(self.browser_rows,
                      key=lambda row: (row['start_unix_s'] or 0, row['path'] == active_path),
                      reverse=True)
        self.replay_list.clear()
        for row in rows:
            active = row['path'] == active_path
            item = QTreeWidgetItem([('- Rec ' if active else '')+row['name']])
            if active:
                warning = QColor(color('warning'))
                item.setForeground(0, warning)
                dot = QPixmap(10, 10)
                dot.fill(Qt.GlobalColor.transparent)
                painter = QPainter(dot)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(warning)
                painter.drawEllipse(1, 1, 8, 8)
                painter.end()
                item.setIcon(0, QIcon(dot))
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            item.setToolTip(0, row['path'])
            self.replay_list.addTopLevelItem(item)
            if selected and row['path'] == selected['path']:
                self.replay_list.setCurrentItem(item)
        if self.replay_list.currentItem() is None and rows:
            self.replay_list.setCurrentItem(self.replay_list.topLevelItem(0))
        self.update_inspector()
        if not self.trimming and not self.saving and not self.cleaning:
            self.browser_status.setText(f'{len(rows)} recordings' if rows else 'Recordings will appear when telemetry connects.')

    def selected_row(self):
        item = self.replay_list.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def active_path(self):
        writer = self.owner.capture.writer
        return str(writer.path.resolve()) if writer and writer.path and not writer.done.is_set() else None

    def deletion_blocker(self, path):
        if (path in self.cleanup_paths or self.deleting_path is not None or self.loading
                or (self.trimming and self.trimming['path'] == path)):
            return 'Wait for the current replay operation to finish.'
        if any(w.path and w.path.resolve() == Path(path) and w.is_alive()
               for w in self.owner.capture.writers):
            return 'This recording is still being written.'
        if (self.owner.dvr_path and Path(self.owner.dvr_path).resolve() == Path(path)
                and (self.owner.external_replay or self.owner.playback.state != 'live')):
            return 'Return to Live before deleting the replay you are watching.'
        return ''

    def update_inspector(self, *_):
        if not hasattr(self, 'delete_button'):
            return
        row = self.selected_row()
        self.cleanup_button.setEnabled(not self.cleaning)
        active = row is not None and row['path'] == self.active_path()
        self.save_button.setVisible(active)
        self.delete_button.setVisible(row is not None and not active)
        self.save_button.setEnabled(active and row['ready'] and not self.saving)
        self.rename_button.setEnabled(row is not None)
        self.export_button.setEnabled(row is not None and row['ready'])
        self.open_button.setEnabled(row is not None and row['ready'])
        if not row:
            self.inspector.setText('Select a recording to see its details.')
            self.delete_button.setEnabled(False)
            self.trim_button.setEnabled(False)
            return
        seconds = int(max(0, row['duration']))
        duration = f'{seconds//3600}:{seconds//60%60:02d}:{seconds%60:02d}'
        self.inspector.setText(f"{row['name']}\n\nStarted: {local_stamp(row['start_unix_s'])}"
                               f"\nDuration: {duration}\nLandings: {row['landings']}\n\n{row['path']}")
        blocker = self.deletion_blocker(row['path'])
        self.delete_button.setEnabled(not blocker)
        self.delete_button.setToolTip(blocker or 'Permanently delete this recording')
        blocker = self.trim_blocker(row)
        self.trim_button.setEnabled(not blocker)
        self.trim_button.setToolTip(blocker or 'Choose a time range and save it as a new replay')

    def trim_blocker(self, row):
        if self.trimming or self.loading or self.deleting_path:
            return 'Wait for the current replay operation to finish.'
        if not row['ready'] or row['duration'] <= 0:
            return 'This recording has no time range to trim.'
        if any(w.path and w.path.resolve() == Path(row['path']) and w.is_alive()
               for w in self.owner.capture.writers):
            return 'Wait for this recording to finish before trimming.'
        return ''

    def trim_selected(self):
        row = self.selected_row()
        if not row or self.trim_blocker(row):
            return
        self.trimming = row
        self.update_inspector()
        self.browser_status.setText('Reading approach markers…')
        self.worker.request('trim_preview', trim_preview, row['path'])

    def show_trim(self, preview):
        dialog = ReplayTrimDialog(self.browser, self.trimming['name'], preview)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(lambda result: self.finish_trim(result, dialog))
        dialog.open()

    def finish_trim(self, result, dialog):
        row = self.trimming
        if result != QDialog.DialogCode.Accepted:
            self.trimming = None
            self.browser_status.clear()
            self.update_inspector()
            return
        self.browser_status.setText('Saving trimmed replay…')
        self.worker.request('trim', trim_replay, row['path'],
                            self.owner.settings.get('recording_folder'),
                            f"{row['name']} (trimmed)", *dialog.bounds)

    def delete_selected(self):
        row = self.selected_row()
        if not row or self.deletion_blocker(row['path']):
            return
        answer = QMessageBox.question(self.browser, 'Delete replay',
                                      f"Permanently delete “{row['name']}”?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes and not self.deletion_blocker(row['path']):
            self.deleting_path = row['path']
            self.update_inspector()
            self.worker.request('delete', delete_replay, row['path'])

    def scan_cleanup(self):
        if self.cleaning:
            return
        self.cleaning = True
        self.update_inspector()
        protected = self.imported_paths | {row['path'] for row in self.browser_rows
                                           if self.deletion_blocker(row['path'])}
        self.browser_status.setText('Checking for empty recordings…')
        self.worker.request('cleanup_scan', cleanup_candidates,
                            self.owner.settings.get('recording_folder'), protected)

    def preview_cleanup(self, candidates):
        candidates = [row for row in candidates if row[0] not in self.imported_paths
                      and not self.deletion_blocker(row[0])]
        if self.browser.isVisible() and candidates:
            dialog = QDialog(self.browser)
            dialog.setWindowTitle('Clean Up Recordings')
            dialog.resize(600, 320)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel('Check the recordings you want to permanently delete.'))
            listing = QTreeWidget()
            listing.setRootIsDecorated(False)
            listing.setHeaderLabels(['Recording', 'Reason'])
            listing.setColumnWidth(0, 330)
            for path, name, reason in candidates:
                item = QTreeWidgetItem([name, reason])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setCheckState(0, Qt.CheckState.Checked)
                listing.addTopLevelItem(item)
            layout.addWidget(listing)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            remove = buttons.addButton('Delete checked recordings', QDialogButtonBox.ButtonRole.AcceptRole)
            remove.clicked.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                checked = [listing.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                           for i in range(listing.topLevelItemCount())
                           if listing.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
                self.cleanup_paths = {path for path in checked if path not in self.imported_paths
                                      and not self.deletion_blocker(path)}
                if self.cleanup_paths:
                    self.update_inspector()
                    self.browser_status.setText('Deleting checked recordings…')
                    self.worker.request('cleanup_delete', delete_cleanup_candidates, tuple(self.cleanup_paths))
                    return
        self.cleaning = False
        self.update_inspector()
        self.browser_status.setText('Cleanup canceled.' if candidates else 'No empty recordings found.')

    def open_selected(self):
        row = self.selected_row()
        if row:
            if not row['ready']:
                self.refresh_browser()
                self.browser_status.setText('Waiting for recorded samples. Try opening again shortly.')
                return
            self.load_path(row['path'])
            self.browser.close()

    def rename_selected(self):
        row = self.selected_row()
        if not row:
            return
        name, accepted = QInputDialog.getText(self.browser, 'Rename replay', 'Title', text=row['name'])
        if accepted and name.strip():
            self.worker.request('rename', rename_replay, row['path'], name.strip())

    def export_selected(self):
        row = self.selected_row()
        if not row or not row['ready']:
            return
        path = row['path']
        suggested = Path(path).with_name(Path(path).name.removesuffix('.wjlive.sqlite')+'_export.wjlive.sqlite')
        destination,_ = QFileDialog.getSaveFileName(self.browser,'Export Replay',str(suggested),'Wire Judge Replay (*.wjlive.sqlite)')
        if not destination:
            return
        if not destination.endswith('.wjlive.sqlite'):
            destination += '.wjlive.sqlite'
        writer = next((w for w in reversed(self.owner.capture.writers) if w.path and w.path.resolve()==Path(path)), None)
        self.worker.request('export', save_replay, path, destination, row['name'], copy.deepcopy(self.owner.scoring_tool.rules), writer)
        self.browser_status.setText('Exporting replay…')

    def save_selected(self):
        row = self.selected_row()
        if not row or not row['ready'] or row['path'] != self.active_path() or self.saving:
            return
        name, accepted = QInputDialog.getText(self.browser, 'Save replay', 'Title', text=row['name'])
        if not accepted or not name.strip() or row['path'] != self.active_path():
            return
        destination = Path(self.owner.settings.get('recording_folder')) / f'WireJudge_{uuid4().hex}.wjlive.sqlite'
        self.saving = True
        self.update_inspector()
        self.browser_status.setText('Saving replay…')
        self.worker.request('save', save_replay, row['path'], destination, name.strip(),
                            copy.deepcopy(self.owner.scoring_tool.rules), self.owner.capture.writer)

    def import_replay(self):
        path,_ = QFileDialog.getOpenFileName(self.browser,'Import Replay',self.owner.settings.get('recording_folder'),'Wire Judge Replay (*.wjlive.sqlite *.sqlite)')
        if path:
            self.worker.request('import', replay_info, path)
            self.browser_status.setText('Adding replay…')

    def load_path(self,path,attempt=None,score_record=None):
        if str(Path(path).resolve()) in self.cleanup_paths:
            self.owner.notify('This recording is being deleted.')
            return
        if self.loading or (self.deleting_path and Path(path).resolve() == Path(self.deleting_path)):return
        self.cancel_load=False;self.loading=True;self.jump_attempt=attempt;self.jump_score=score_record
        self.loading_view.begin()
        self.worker.request('load', load_replay, path, self.report_load_progress)
        self.owner.notify('Loading replay… live telemetry continues recording.')
    def report_load_progress(self, fraction):
        self.worker.results.put({'kind': 'load_progress', 'value': fraction})

    def first_frame_loaded(self, success):
        if not self.waiting_first_frame:
            return
        self.waiting_first_frame = False
        self.loading = False
        if success:
            self.loading_view.ready()
            self.owner.deck_crew_tool.replay_chatter_pending = True
        else:
            self.loading_view.stop()

    def visible_ids(self):
        if self.owner.external_replay:return {a['id'] for a in self.owner.capture.approach_catalog()[1]}
        connection=self.owner.capture.connection_id
        return {a['id'] for a in self.owner.capture.approach_catalog()[1] if a['connection_id']==connection}
    def poll(self):
        while True:
            try:r=self.worker.results.get_nowait()
            except queue.Empty:break
            kind = r['kind']
            if kind == 'load_progress':
                if not self.cancel_load:
                    self.loading_view.progress(r['value'])
                continue
            if kind == 'load':
                if self.cancel_load or r.get('error'):
                    self.loading = False
                    self.loading_view.stop()
                if self.cancel_load:
                    continue
            if kind == 'list':
                self.scanning = False
            if kind == 'delete':
                self.deleting_path = None
                self.update_inspector()
            if kind == 'save':
                self.saving = False
                self.update_inspector()
            if kind == 'cleanup_delete' or (kind == 'cleanup_scan' and r.get('error')):
                self.cleanup_paths.clear()
                self.cleaning = False
                self.update_inspector()
            if kind == 'trim' or (kind == 'trim_preview' and r.get('error')):
                self.trimming = None
                self.update_inspector()
            if r.get('error'):
                if self.browser and self.browser.isVisible():
                    self.browser_status.setText(r['error'])
                self.owner.notify(r['error'])
                continue
            if kind == 'list':
                self.browser_rows = r['value']
                self.populate_browser()
                if self.rescan:
                    self.rescan = False
                    self.refresh_browser()
            elif kind == 'delete':
                path = r['value']
                self.forget_deleted(path)
                self.browser_status.setText('Recording deleted.')
                self.refresh_browser()
            elif kind == 'cleanup_scan':
                self.preview_cleanup(r['value'])
            elif kind == 'cleanup_delete':
                paths, errors = r['value']
                for path in paths:
                    self.forget_deleted(path)
                self.refresh_browser()
                message = f'Deleted {len(paths)} recordings.'
                if errors:
                    message += ' ' + ' | '.join(errors)
                self.browser_status.setText(message)
                self.owner.notify(message)
            elif kind == 'rename':
                path, name = r['value']
                if path in self.infos:
                    self.infos[path]['name'] = name
                if self.info and self.info['path'] == path:
                    self.info['name'] = name
                self.refresh_browser()
            elif kind == 'trim_preview':
                if self.browser.isVisible():
                    self.browser_status.clear()
                    self.show_trim(r['value'])
                else:
                    self.trimming = None
                    self.update_inspector()
            elif kind in ('import','export','trim','save'):
                info = r['value']
                if kind == 'import':
                    self.imported_paths.add(info['path'])
                    self.owner.settings.store.setValue('replays/imported_paths', sorted(self.imported_paths))
                if kind in ('import','trim'):
                    self.remember(info)
                if kind in ('import','trim','save'):
                    self.refresh_browser()
                if self.browser and self.browser.isVisible():
                    verb = {'import': 'Added', 'export': 'Exported', 'trim': 'Saved', 'save': 'Saved'}[kind]
                    self.browser_status.setText(f"{verb} {info['name']}")
            elif kind == 'load':
                info,attempts,histories=r['value']
                if self.jump_score and self.jump_score.get('recording_id')!=info['recording_id']:
                    self.loading = False
                    self.loading_view.stop()
                    self.owner.notify('This replay does not match the saved attempt’s recording ID.');continue
                self.remember(info)
                self.info=info;self.infos[info['path']]=info;self.replay_ids={a['id'] for a in attempts}
                self.owner.capture.select_archive(info['path'],attempts,histories)
                self.owner.external_replay=True;self.owner.dvr_path=info['path'];self.owner.reset_playback()
                self.owner.playback.bounds(info['start_s'],info['end_s'])
                target=self.jump_attempt['start_s'] if self.jump_attempt else info['start_s']
                if self.jump_score:
                    offset=self.jump_score.get('attempt_start_offset_s')
                    if offset is not None:target=info['start_s']+offset
                    elif self.jump_score.get('started_utc'):
                        target=info['start_s']+datetime.fromisoformat(self.jump_score['started_utc'].replace('Z','+00:00')).timestamp()-info['start_unix_s']
                partial=False
                if self.jump_score:
                    end_offset=self.jump_score.get('attempt_end_offset_s')
                    partial=(self.jump_score['attempt_id'] not in self.replay_ids or target>info['end_s'] or
                        (end_offset is not None and info['start_s']+end_offset>info['end_s']))
                self.owner.timeline_toggle.setChecked(True)
                self.waiting_first_frame = True
                self.loading_view.progress(1.)
                self.owner.seek_playback(target)
                if not self.owner.larp_enabled:
                    for i in (1,2,3):self.owner.tabs.setTabEnabled(i,True)
                self.owner.scoring_tool.request_key=None
                self.owner.scoring_tool.epoch+=1
                if self.jump_attempt:
                    self.owner.current_aircraft=self.jump_attempt['aircraft_id'];self.owner.current_carrier=self.jump_attempt['carrier_id']
                    self.owner.tabs.setCurrentIndex(0)
                self.owner.notify(f"Loaded {info['name']} · {len(attempts)} attempts"+(' · linked attempt has incomplete replay coverage' if partial else ''))
        if self.owner.capture.writer and not self.scanning and time.monotonic() - self.last_scan >= 2:
            self.refresh_browser()
        self.update_banner()
    def update_banner(self):
        self.owner.replays_button.set_recording(bool(self.active_path()))
        info = self.current_info()
        watching = self.owner.external_replay or self.owner.playback.state != 'live'
        self.return_live.setVisible(watching)
        tooltip = f"Return to live from {info['name']}" if watching and info else 'Return to live telemetry'
        if self.return_live.toolTip() != tooltip:
            self.return_live.setToolTip(tooltip)
    def linked_replay_path(self, record):
        path = self.index.get(record.get('recording_id'))
        return path if path and Path(path).is_file() else None

    def open_score_record(self, record):
        path = self.linked_replay_path(record)
        if path:
            self.load_path(path, score_record=record)
        else:
            self.owner.notify('Import the linked recording from Replays to open this score.')

    def go_live(self):
        self.cancel_load = True
        if self.waiting_first_frame:
            self.first_frame_loaded(False)
        self.loading_view.stop()
        self.owner.capture.clear_analysis_archive()
        self.owner.scoring_tool.request_key=None;self.owner.scoring_tool.epoch+=1
        self.owner.external_replay=False;self.info=None
        if self.owner.dvr_path!=self.live_path:
            self.owner.dvr_path=self.live_path;self.owner.reset_playback()
        self.update_banner()
