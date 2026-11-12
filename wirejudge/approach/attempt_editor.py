from wirejudge.ui.theme import blend, color as theme_color
"""Pilot aircraft-life timeline with editable attempt boundaries."""
import math
import queue
import time
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QDoubleSpinBox, QComboBox,
    QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QMessageBox, QTreeWidgetItem)
from wirejudge.approach.approach_view import ApproachView
from wirejudge.ui.control_icons import approach_case_badge
from wirejudge.approach.attempt_store import AttemptWorker
from wirejudge.scoring.landing_scoring import DEFAULT_RULES


class AttemptEditor(ApproachView):
    def __init__(self, capture, colors):
        super().__init__(capture,colors,with_plots=False)
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels(['Username / Airframe / Attempt'])
        self.worker = AttemptWorker(); self.worker.start()
        self.path = None; self.selected_id = None
        self.recording_path = None
        self.list_signature = None
        self.token = 0; self.last_request = 0.
        self.record_end = None
        self.lives = []
        self.life_index_key = None
        self.life_pending = False
        self.life_next_read = 0.
        self.life_signature = None
        self.saving = False; self.dirty = False
        self.bounds = None; self.draft = None
        self.full_extent = None
        self.track_key = None
        self.track_token = 0
        self.track_points = np.empty((0, 3))
        self.track_wire_points = np.empty((0, 2))
        self.track_deck_points = np.empty((0, 2))
        self.approach_guide_key = None
        self.approach_guide_labels = []
        self.summary.setStyleSheet("font-size: 22px; font-weight: 600;")
        col = self.content_layout
        self.hint = QLabel('Select a pilot and aircraft life. Drag the shaded range handles, then choose Add Attempt. Click an attempt bar to edit or delete it.')
        self.hint.setWordWrap(True); col.addWidget(self.hint)
        flight_controls = QHBoxLayout()
        self.pilot_choice, self.life_choice = QComboBox(), QComboBox()
        flight_controls.addWidget(QLabel('Pilot'))
        flight_controls.addWidget(self.pilot_choice, 1)
        flight_controls.addWidget(QLabel('Aircraft life'))
        flight_controls.addWidget(self.life_choice, 2)
        self.full_timeline = QPushButton('Full timeline')
        self.full_timeline.clicked.connect(self.fit_lives)
        flight_controls.addWidget(self.full_timeline)
        col.addLayout(flight_controls)
        self.life_plot = pg.PlotWidget(background=theme_color('map'),
                                       axisItems={'bottom': RecordingTimeAxis(orientation='bottom')})
        self.life_plot.setFixedHeight(92)
        self.life_plot.getPlotItem().hideButtons()
        self.life_plot.getAxis('bottom').enableAutoSIPrefix(False)
        self.life_plot.setMenuEnabled(False)
        self.life_plot.setMouseEnabled(x=True, y=False)
        self.life_plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.life_plot.showGrid(x=True, alpha=.15)
        self.life_range = pg.LinearRegionItem((0, 0), brush=pg.mkBrush(128, 128, 128, 38), swapMode='block')
        self.life_range.hide()
        self.life_range.setZValue(3)
        start, stop = self.life_range.lines
        start.addMarker('<|', position=.12, size=15)
        stop.addMarker('o', position=.12, size=13)
        square = QPainterPath()
        square.addRect(-.5, -.5, 1, 1)
        stop.markers[0] = (square, .12, 13)
        self.life_range.sigRegionChanged.connect(self.region_changed)
        self.life_range.sigRegionChangeFinished.connect(self.load_track_map)
        self.life_plot.addItem(self.life_range)
        self.life_items = []
        self.pilot_choice.currentIndexChanged.connect(self.select_pilot)
        self.life_choice.currentIndexChanged.connect(self.select_life)
        col.addWidget(self.life_plot)
        actions = QHBoxLayout()
        self.add_button = QPushButton('Add Attempt')
        self.delete_button = QPushButton('Delete Attempt')
        self.add_button.clicked.connect(self.prompt_add)
        self.delete_button.clicked.connect(self.prompt_delete)
        for button in (self.add_button, self.delete_button):
            actions.addWidget(button)
        self.case_type = QComboBox()
        self.case_type.addItems(['Case 1', 'Case 3'])
        self.case_type.currentTextChanged.connect(self.case_changed)
        actions.addWidget(self.case_type)
        self.apply_button = QPushButton('Apply')
        self.apply_button.clicked.connect(self.apply)
        self.apply_flash = QTimer(self, interval=80)
        self.apply_flash.timeout.connect(self.flash_apply)
        actions.addWidget(self.apply_button)
        self.revert_button = QPushButton('Discard')
        self.revert_button.clicked.connect(self.discard)
        actions.addWidget(self.revert_button)
        fit = QPushButton('Fit Track')
        fit.clicked.connect(self.fit_track)
        actions.addWidget(fit)
        actions.addStretch()
        col.addLayout(actions)
        self.timeline_key = QLabel()
        col.addWidget(self.timeline_key)
        self.track_map = pg.PlotWidget(background=theme_color('map'))
        self.track_map.setTitle('Top-down track')
        self.track_map.setAspectLocked(True)
        self.track_map.setMenuEnabled(False)
        self.track_map.setMinimumHeight(150)
        self.track_map.showGrid(x=True, y=True, alpha=.15)
        self.track_localizer = self.track_map.plot(connect='finite')
        self.track_centerline = self.track_map.plot()
        self.track_grade_gates = self.track_map.plot(connect='finite')
        for guide in (self.track_localizer, self.track_centerline, self.track_grade_gates):
            guide.setZValue(-1)
        self.track_context = self.track_map.plot()
        self.track_selection = self.track_map.plot()
        self.track_wires = self.track_map.plot()
        self.track_deck = self.track_map.plot(connect='finite')
        self.track_boundary_bars = [self.track_map.plot(), self.track_map.plot()]
        self.tree.itemClicked.connect(self.open_track_group)
        col.addWidget(self.track_map, 1)
        self.set_distance_units(self.units)
        self.fit_timer = QTimer(self)
        self.fit_timer.setInterval(100)
        self.fit_timer.timeout.connect(self.update_full_timeline)
        self.life_plot.getViewBox().sigXRangeChanged.connect(self.update_full_timeline)
        self.update_timeline_theme()
        self.set_controls(False)

    def refresh(self, cursor=None, force=False, origin=None):
        if self.origin != origin:
            self.origin = origin
            self.selected_id = None
        _, attempts = self.capture.approach_catalog()
        ordered = sorted(attempts, key=lambda a: (
            a['pilot'].casefold(), (a.get('airframe') or '').casefold(), a['start_s'], a['id']))
        self.catalog = {a['id']: a for a in ordered}
        starts = self.recording_origins(ordered, origin)
        counts, numbers = {}, {}
        for attempt in sorted(ordered, key=lambda a: (a['start_s'], a['id'])):
            pilot = attempt['pilot']
            counts[pilot] = counts.get(pilot, 0) + 1
            numbers[attempt['id']] = counts[pilot]
        rows = {
            a['id']: f"#{numbers[a['id']]} · {self.time_text(a['start_s'] - starts[a['connection_id']])} - "
                     f"{self.score_text(a['id'])} - {a['outcome']}"
            for a in ordered
        }
        signature = (
            tuple(theme_color(role) for role in ('accent', 'text', 'recessed')),
            [(a['id'], a['pilot'], a.get('airframe'), a.get('case'), rows[a['id']], outcome_color(a['outcome']))
             for a in ordered],
        )
        if signature != self.list_signature:
            self.list_signature = signature
            role = Qt.ItemDataRole.UserRole
            previous = self.tree.currentItem()
            selected = previous.data(0, role) if previous else None
            collapsed = {key for key, item in self.groups.items() if not item.isExpanded()}
            self.tree.blockSignals(True)
            try:
                self.tree.clear()
                self.items.clear()
                self.groups.clear()
                for attempt in ordered:
                    pilot = attempt['pilot']
                    frame = attempt.get('airframe') or 'Unknown airframe'
                    for key, label in (((pilot,), pilot), ((pilot, frame), frame)):
                        if key not in self.groups:
                            parent = self.groups[key[:-1]] if len(key) == 2 else self.tree
                            group = QTreeWidgetItem(parent, [label])
                            group.setData(0, int(role) + 1, key)
                            self.groups[key] = group
                    item = QTreeWidgetItem(self.groups[pilot, frame], [rows[attempt['id']]])
                    item.setData(0, role, attempt['id'])
                    item.setIcon(0, approach_case_badge(attempt.get('case')))
                    item.setToolTip(0, f"{attempt.get('case') or 'Case 1'} · {rows[attempt['id']]}")
                    item.setForeground(0, pg.mkColor(outcome_color(attempt['outcome'])))
                    self.items[attempt['id']] = item
                for key, group in self.groups.items():
                    group.setExpanded(key not in collapsed)
                if self.items:
                    self.tree.setCurrentItem(self.items.get(selected) or self.items[next(reversed(self.items))])
            finally:
                self.tree.blockSignals(False)
        self.draw_selected()
        self.draw_lives()
        self.add_button.setEnabled(bool(self.recording_path and self.life_range.isVisible()) and not self.saving)
        self.delete_button.setEnabled(bool(self.current_attempt() and self.path) and not self.saving)

    def open_track_group(self, item, _column):
        key = item.data(0, int(Qt.ItemDataRole.UserRole) + 1)
        if not key:
            return
        pilot, *frames = key
        matches = [a for a in self.catalog.values() if a['pilot'] == pilot and
                   (not frames or (a.get('airframe') or 'Unknown airframe') == frames[0])]
        if matches:
            attempt = max(matches, key=lambda a: a['start_s'])
            self.tree.setCurrentItem(self.items[attempt['id']])
            self.fit_lives()

    @staticmethod
    def life_key(life):
        return (life['connection_id'], life['aircraft_id'], life['start_s']) if life else None

    def install_lives(self, lives):
        old_pilot = self.pilot_choice.currentText()
        self.lives = lives
        pilots = sorted({life['pilot'] for life in lives}, key=str.casefold)
        self.pilot_choice.blockSignals(True)
        self.pilot_choice.clear()
        self.pilot_choice.addItems(pilots)
        attempt = self.current_attempt()
        selected = old_pilot or (attempt['pilot'] if attempt else '')
        if selected in pilots:
            self.pilot_choice.setCurrentText(selected)
        self.pilot_choice.blockSignals(False)
        self.select_pilot()

    def select_pilot(self, *_):
        previous = self.life_choice.currentData()
        self.life_choice.blockSignals(True)
        self.life_choice.clear()
        origin = self.origin or 0.
        for life in self.lives:
            if life['pilot'] == self.pilot_choice.currentText():
                self.life_choice.addItem(
                    f"{life.get('airframe') or 'Aircraft'} · {self.time_text(life['start_s']-origin)}", life)
                if self.life_key(previous) == self.life_key(life):
                    self.life_choice.setCurrentIndex(self.life_choice.count()-1)
        self.life_choice.blockSignals(False)
        attempt = self.current_attempt()
        if attempt and attempt['pilot'] != self.pilot_choice.currentText():
            self.tree.setCurrentItem(None)
        self.select_life(preserve=bool(previous and self.life_key(previous)==self.life_key(self.life_choice.currentData())))
        if not previous or previous['pilot'] != self.pilot_choice.currentText():
            self.fit_lives()

    def select_life(self, *_, preserve=False):
        life = self.life_choice.currentData()
        attempt = self.current_attempt()
        if attempt and life and not self.life_matches(life, attempt):
            self.tree.setCurrentItem(None)
        self.sync_range(preserve=preserve)
        self.draw_lives()
        self.set_controls(bool(self.bounds))

    @staticmethod
    def life_matches(life, attempt):
        return ((life['connection_id'], life['aircraft_id']) == (attempt['connection_id'], attempt['aircraft_id'])
                and life['start_s'] <= attempt['start_s'] <= life['end_s'])

    def sync_range(self, preserve=False):
        life = self.life_choice.currentData()
        origin = self.origin or 0.
        self.bounds = [life['start_s'], life['end_s']] if life else None
        self.life_range.blockSignals(True)
        if self.bounds:
            low, high = (t-origin for t in self.bounds)
            self.life_range.setBounds((low, high))
            if self.draft is not None:
                self.life_range.setRegion([t-origin for t in self.draft])
            elif not preserve:
                self.life_range.setRegion((max(low, high-30), high))
        self.life_range.blockSignals(False)
        self.life_range.setVisible(bool(self.bounds and self.bounds[0] < self.bounds[1]))
        self.update_boundary_labels()

    def fit_lives(self):
        if self.full_extent:
            self.life_plot.setXRange(*self.full_extent, padding=.03)

    def fit_track(self):
        if len(self.track_points):
            self.track_map.autoRange()
        if self.draft:
            origin = self.origin or 0.
            self.life_plot.setXRange(*(t-origin for t in self.draft), padding=.12)
        else:
            life = self.life_choice.currentData()
            if life:
                origin = self.origin or 0.
                self.life_plot.setXRange(life['start_s']-origin, life['end_s']-origin, padding=.05)

    def update_full_timeline(self, *_):
        active = False
        if self.full_extent and self.isVisible():
            left, right = self.life_plot.viewRange()[0]
            first, last = self.full_extent
            active = left > first + .001 or right < last - .001
        if active:
            if not self.fit_timer.isActive():
                self.fit_timer.start()
            color = pg.mkColor(theme_color('accent'))
            color.setAlpha(round(65 + 125*(1+math.cos(time.monotonic()*5))/2))
            style = f'QPushButton {{ background: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()}); }}'
        else:
            self.fit_timer.stop()
            style = ''
        if self.full_timeline.styleSheet() != style:
            self.full_timeline.setStyleSheet(style)

    def update_timeline_theme(self):
        for line, bar, role in zip(self.life_range.lines, self.track_boundary_bars, ('slow', 'warning')):
            color = theme_color(role)
            line.setPen(pg.mkPen(color, width=4))
            line.setHoverPen(pg.mkPen(color, width=8))
            bar.setPen(pg.mkPen(color, width=3))
        self.life_plot.getAxis('bottom').invalidate_labels()
        green, red, accent = (theme_color(role) for role in ('slow', 'warning', 'accent'))
        self.track_map.setBackground(theme_color('map'))
        self.track_context.setPen(pg.mkPen(theme_color('muted'), width=1))
        self.track_wires.setPen(pg.mkPen(theme_color('carrier'), width=2))
        self.track_deck.setPen(pg.mkPen(theme_color('carrier'), width=1))
        self.track_selection.setPen(pg.mkPen(accent, width=2))
        self.draw_approach_guide()
        self.timeline_key.setText(f'<span style="color:{green}">▶ Start</span> &nbsp; '
                                  f'<span style="color:{red}">■ Stop</span> &nbsp; '
                                  f'<span style="color:{accent}">━ Selected life</span>')
        self.draw_lives()
        self.update_full_timeline()

    def update_boundary_labels(self):
        axis = self.life_plot.getAxis('bottom')
        axis.set_boundaries(self.life_range.getRegion() if self.life_range.isVisible() else ())

    def set_distance_units(self, units):
        self.units = units
        for axis, label in (('bottom', 'Right of landing course'), ('left', 'Along landing course')):
            self.track_map.setLabel(axis, label, units=units.distance_symbol)
            self.track_map.getAxis(axis).enableAutoSIPrefix(False)
        self.draw_track_map()

    def load_track_map(self, *_):
        attempt = self.current_attempt()
        if not attempt or not self.path or not self.draft or self.saving or not self.isVisible():
            return
        start, end = self.draft
        key = (self.path, attempt['id'], start, end)
        if self.track_key == key:
            return
        self.track_key = key
        self.track_token += 1
        self.worker.request('read', self.path, attempt, self.track_token, start=start, end=end)

    def draw_track_map(self):
        self.draw_approach_guide()
        scale = self.units.distance_nm(1)
        points = self.track_points
        self.track_context.setData(points[:, 1]*scale, points[:, 2]*scale, connect='finite')
        selected = points[(points[:, 0]>=self.draft[0]) & (points[:, 0]<=self.draft[1])] if self.draft else points[:0]
        self.track_selection.setData(selected[:, 1]*scale, selected[:, 2]*scale, connect='finite')
        wires = self.track_wire_points
        self.track_wires.setData(wires[:, 0]*scale, wires[:, 1]*scale, connect='finite')
        deck = self.track_deck_points
        self.track_deck.setData(deck[:, 0]*scale, deck[:, 1]*scale, connect='finite')
        for index, bar in enumerate(self.track_boundary_bars):
            segment = boundary_bar(points, self.draft[index]) if self.draft else None
            if segment is None:
                bar.setData([], [])
            else:
                bar.setData(segment[:, 0]*scale, segment[:, 1]*scale)

    def draw_approach_guide(self):
        case = self.case_type.currentText()
        rules = getattr(self.capture, 'scoring_rules', DEFAULT_RULES)
        leg = rules[case]
        gates = [('Final start', leg['start_nm']), ('Final stop', leg['end_nm'])]
        if case == 'Case 3':
            gates += [('Platform start', leg['platform_start_nm']), ('Platform stop', leg['platform_end_nm'])]
        scale = self.units.distance_nm(1)
        angle = rules['targets']['loc_tolerance']
        colors = (theme_color('accent'), theme_color('on'), theme_color('muted'))
        selected = self.current_attempt() is not None
        key = (selected, case, tuple(gates), scale, angle, colors)
        if key == self.approach_guide_key:
            return
        self.approach_guide_key = key
        for label in self.approach_guide_labels:
            self.track_map.removeItem(label)
        self.approach_guide_labels.clear()
        if not selected:
            for guide in (self.track_localizer, self.track_centerline, self.track_grade_gates):
                guide.setData([], [])
            return

        localizer, grade, center = colors
        self.track_localizer.setPen(pg.mkPen(localizer, width=1.5))
        self.track_centerline.setPen(pg.mkPen(center, style=Qt.PenStyle.DashLine))
        self.track_grade_gates.setPen(pg.mkPen(grade, width=1.5, style=Qt.PenStyle.DotLine))
        slope = math.tan(math.radians(angle))
        far = max(10. if case == 'Case 3' else 1., *(distance for _, distance in gates))*scale
        edges_x = [-far*slope, 0., far*slope, -far*slope]
        edges_y = [-far, 0., -far, -far]
        if case == 'Case 3':
            intercept = 3.*scale
            edges_x.extend((np.nan, -intercept*slope, intercept*slope))
            edges_y.extend((np.nan, -intercept, -intercept))
        self.track_localizer.setData(edges_x, edges_y)
        self.track_centerline.setData([0., 0.], [0., -far])
        xs, ys = [], []
        for title, distance in gates:
            y = -distance*scale
            width = -y*slope
            xs.extend((-width, width, np.nan))
            ys.extend((y, y, np.nan))
            label = pg.TextItem(f'{title} · {distance:g} NM', color=grade, anchor=(1, .5))
            label.setPos(-width, y)
            self.track_map.addItem(label, ignoreBounds=True)
            self.approach_guide_labels.append(label)
        self.track_grade_gates.setData(xs, ys)

    def clear_track_map(self):
        self.track_key = None
        self.track_token += 1
        self.track_points = np.empty((0, 3))
        self.track_wire_points = np.empty((0, 2))
        self.track_deck_points = np.empty((0, 2))
        self.draw_track_map()

    def draw_lives(self):
        lives = [self.life_choice.itemData(i) for i in range(self.life_choice.count())]
        origin = self.origin or 0.
        attempts = [a for a in self.catalog.values() if a['pilot']==self.pilot_choice.currentText()]
        signature = (lives, attempts, origin, self.life_choice.currentIndex(), self.selected_id,
                     theme_color('accent'), theme_color('on'), theme_color('warning'))
        if signature == self.life_signature:
            return
        self.life_signature = signature
        self.full_extent = (min(l['start_s'] for l in lives)-origin,
                            max(l['end_s'] for l in lives)-origin) if lives else None
        self.life_plot.setFixedHeight(64 + min(max(len(lives), 1), 10)*20)
        for item in self.life_items:
            self.life_plot.removeItem(item)
        self.life_items.clear()
        ticks = []
        for lane, life in enumerate(lives):
            ticks.append((lane, life.get('airframe') or 'Aircraft'))
            start, end = life['start_s'], life['end_s']
            color = theme_color('accent' if lane == self.life_choice.currentIndex() else 'muted')
            track = self.life_plot.plot([start-origin, end-origin], [lane, lane],
                                        pen=pg.mkPen(color, width=3), symbol='o', symbolSize=4)
            track.setCurveClickable(True, width=9)
            track.sigClicked.connect(lambda *_, i=lane: self.select_timeline_life(i))
            self.life_items.append(track)
            for a in attempts:
                if (a['connection_id'], a['aircraft_id']) != (life['connection_id'], life['aircraft_id']):
                    continue
                left, right = max(start, a['start_s']), min(end, a['end_s'] or a['last_s'])
                if left >= right:
                    continue
                bar = self.life_plot.plot([left-origin, right-origin], [lane, lane],
                    pen=pg.mkPen(outcome_color(a['outcome']), width=11 if a['id']==self.selected_id else 7))
                bar.setZValue(1)
                bar.setCurveClickable(True, width=12)
                bar.sigClicked.connect(lambda *_, i=lane, identifier=a['id']: self.select_timeline_attempt(i, identifier))
                self.life_items.append(bar)
        self.life_plot.getAxis('left').setTicks([ticks])
        self.life_plot.setYRange(-.6, max(0, len(lives)-1)+.6, padding=0)
        self.update_full_timeline()

    def select_timeline_attempt(self, lane, identifier):
        self.life_choice.setCurrentIndex(lane)
        if identifier in self.items:
            self.tree.setCurrentItem(self.items[identifier])

    def select_timeline_life(self, lane):
        self.tree.setCurrentItem(None)
        self.life_choice.setCurrentIndex(lane)

    def prompt_add(self):
        life = self.life_choice.currentData()
        if not self.recording_path or not life or self.saving:
            return
        origin = self.origin if self.origin is not None else 0.
        if origin is None or self.record_end is None or self.record_end <= origin:
            self.hint.setText('Wait for recorded telemetry or load a replay to add an attempt.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Add Attempt')
        form = QFormLayout(dialog)
        start, end = QDoubleSpinBox(), QDoubleSpinBox()
        duration = self.record_end - origin
        low, high = life['start_s']-origin, min(life['end_s']-origin, duration)
        selection = self.life_range.getRegion()
        for label, field, value in [('Start', start, selection[0]), ('End', end, selection[1])]:
            field.setDecimals(3)
            field.setRange(low, high)
            field.setValue(value)
            field.setSuffix(' s')
            form.addRow(label, field)
        case = QComboBox()
        case.addItems(['Case 1', 'Case 3'])
        case.setCurrentText(self.case_type.currentText())
        outcome = QComboBox()
        outcome.addItems(['Ended manually', 'Bolter', 'Wire 1', 'Wire 2', 'Wire 3', 'Wire 4', 'Waveoff', 'Aborted', 'Track lost'])
        form.addRow('Case', case)
        form.addRow('Result', outcome)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        path = self.recording_path
        if dialog.exec() != QDialog.DialogCode.Accepted or path != self.recording_path:
            return
        if start.value() >= end.value():
            self.hint.setText('Start must be before end.')
            return
        self.manual_case, self.manual_outcome = case.currentText(), outcome.currentText()
        self.saving = True
        self.set_controls(False)
        self.hint.setText('Finding aircraft and carriers in the selected interval…')
        self.worker.request('candidates', path, {'id': None}, self.token,
                            start=origin+start.value(), end=min(origin+end.value(), origin+duration),
                            glide=self.capture.approaches.glide, offset=self.capture.approaches.offset, life=life)

    def prompt_delete(self):
        attempt = self.current_attempt()
        path = self.path
        if not attempt or not path or self.saving:
            return
        answer = QMessageBox.question(self, 'Delete Attempt', f"Delete the selected attempt for {attempt['pilot']}?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes and path == self.path:
            self.saving = True
            self.set_controls(False)
            self.hint.setText('Deleting attempt…')
            self.worker.request('delete', path, attempt, self.token)

    def current_attempt(self):
        item = self.tree.currentItem()
        return self.catalog.get(item.data(0,Qt.ItemDataRole.UserRole)) if item else None

    def draw_selected(self):
        attempt = self.current_attempt()
        key = attempt['id'] if attempt else None
        if key != self.selected_id:
            self.selected_id = key
            self.token += 1
            self.dirty = False
            self.draft = None
            if not self.track_key or self.track_key[:2] != (self.path, key):
                self.clear_track_map()
            if attempt:
                self.path = self.capture.attempt_path(key, self.recording_path)
                if self.pilot_choice.findText(attempt['pilot']) >= 0:
                    self.pilot_choice.setCurrentText(attempt['pilot'])
                    for i in range(self.life_choice.count()):
                        if self.life_matches(self.life_choice.itemData(i), attempt):
                            self.life_choice.setCurrentIndex(i)
                            break
                self.draft = [attempt['start_s'], attempt['end_s'] or attempt['last_s']]
                self.case_type.blockSignals(True)
                self.case_type.setCurrentText(attempt.get('case', 'Case 1'))
                self.case_type.blockSignals(False)
            self.sync_range()
        elif attempt and not self.dirty:
            saved = [attempt['start_s'], attempt['end_s'] or attempt['last_s']]
            if self.draft != saved:
                self.draft = saved
                self.sync_range()
        pilot = attempt['pilot'] if attempt else self.pilot_choice.currentText()
        self.summary.setText(pilot or 'Select a pilot')
        self.set_controls(bool(self.bounds))
        self.draw_track_map()
        if not self.dirty:
            self.load_track_map()

    def case_changed(self, *_):
        if self.current_attempt():
            self.dirty = True
        self.set_controls(bool(self.bounds))
        self.draw_approach_guide()

    def set_controls(self, enabled):
        available = self.life_range.isVisible() and not self.saving
        self.add_button.setEnabled(bool(self.recording_path) and available)
        self.delete_button.setEnabled(bool(self.current_attempt() and self.path) and not self.saving)
        self.case_type.setEnabled(not self.saving)
        valid = self.draft is not None and self.draft[0] < self.draft[1]
        can_apply = enabled and valid and self.dirty and not self.saving
        self.apply_button.setEnabled(can_apply)
        if can_apply and not self.apply_flash.isActive():
            self.apply_flash.start()
            self.flash_apply()
        elif not can_apply and self.apply_flash.isActive():
            self.apply_flash.stop()
            self.apply_button.setStyleSheet('')
        self.revert_button.setEnabled(enabled and self.dirty and not self.saving)
        self.life_range.setMovable(available)

    def flash_apply(self):
        if self.apply_button.isVisible():
            weight = .16 + .14 * (1 + math.sin(time.monotonic() * 5))
            accent = theme_color('accent')
            fill = blend(theme_color('control'), accent, weight)
            self.apply_button.setStyleSheet(f'QPushButton {{background:{fill}; border-color:{accent};}}')

    def poll(self, path):
        if path != self.recording_path:
            self.recording_path = path
            self.life_index_key = None
            self.life_pending = False
            self.life_next_read = 0.
            self.install_lives([])
        index_key = (path, self.record_end)
        now = time.monotonic()
        if (path and self.isVisible() and not self.life_pending and not self.saving
                and now >= self.life_next_read and index_key != self.life_index_key):
            self.life_index_key = index_key
            self.life_pending = True
            self.life_next_read = now + 5.
            if not self.lives:
                self.hint.setText('Indexing recorded aircraft lives…')
            self.worker.request('lives', path, {'id': None}, self.token)
        a=self.current_attempt()
        path=self.capture.attempt_path(a['id'],path) if a else path
        if self.path != path:
            self.path = path; self.token += 1
            self.selected_id = None; self.draft = None; self.dirty = False
            self.sync_range()
            self.clear_track_map()
        while True:
            try:r = self.worker.results.get_nowait()
            except queue.Empty:break
            if r['kind'] == 'read':
                if r['token'] != self.track_token or r['path'] != self.path or r['id'] != self.selected_id:
                    continue
                if not r['error']:
                    fit = not len(self.track_points)
                    self.track_points = np.asarray([p[:3] for p in r['track']], dtype=float).reshape((-1, 3))
                    self.track_wire_points = np.asarray(
                        [point for line in r['wires'] for point in (*line, (np.nan, np.nan))],
                        dtype=float).reshape((-1, 2))
                    self.track_deck_points = np.asarray(
                        [point for line in r['deck'] for point in (*line, (np.nan, np.nan))],
                        dtype=float).reshape((-1, 2))
                    self.draw_track_map()
                    if fit:
                        self.track_map.autoRange()
                continue
            if r['kind'] == 'lives':
                if r['path'] == self.recording_path:
                    self.life_pending = False
                    if r['error']:
                        self.life_index_key = None
                        self.hint.setText(r['error'])
                    else:
                        had_lives = bool(self.lives)
                        self.install_lives(r['lives'])
                        if not had_lives and self.current_attempt():
                            self.selected_id = None
                            self.draw_selected()
                        self.hint.setText('Drag the timeline range handles to add an attempt. Click an attempt bar to edit or delete it.'
                                          if self.lives else 'No recorded aircraft lives yet.')
                continue
            if r['kind'] in ('candidates', 'add', 'delete'):
                if r['kind'] == 'candidates' and r['path'] == self.recording_path and not r['error']:
                    candidates = r['candidates']
                    if candidates:
                        labels = [f"{a['pilot']} · {a['airframe'] or 'Unknown airframe'} · {a['carrier']} ({i+1})"
                                  for i, a in enumerate(candidates)]
                        label, accepted = QInputDialog.getItem(self, 'Add Attempt', 'Aircraft and carrier', labels, editable=False)
                        if accepted and r['path'] == self.recording_path:
                            self.worker.request('add', r['path'], candidates[labels.index(label)], self.token,
                                                case=self.manual_case, outcome=self.manual_outcome)
                            continue
                    else:
                        self.hint.setText('No aircraft and carrier telemetry in that interval.')
                self.saving = False
                if r['error']:
                    self.hint.setText(r['error'])
                elif r['kind'] in ('add', 'delete'):
                    self.capture.apply_attempt_change(r['path'], r['id'], r.get('metadata'),
                                                      r.get('points'), r.get('history'))
                    self.hint.setText('Attempt added.' if r['kind'] == 'add' else 'Attempt deleted.')
                    self.refresh(origin=self.origin)
                    if r['kind'] == 'add' and r['id'] in self.items:
                        self.tree.setCurrentItem(self.items[r['id']])
                self.set_controls(bool(self.bounds))
                continue
            if r['kind']=='save':
                self.saving = False
                if r['path']==self.path:
                    if r['error']:
                        self.hint.setText(r['error'])
                    else:
                        self.capture.install_approach_edit(r['metadata'],r['points'])
                        self.hint.setText('Boundaries saved. Approach graphs use the edited range.')
                        if r['id']==self.selected_id:
                            self.selected_id = None
                            self.refresh(origin=self.origin)
                        self.last_key = None
                    self.set_controls(bool(self.bounds))
                continue
    def region_changed(self):
        self.update_boundary_labels()
        if self.current_attempt() and self.bounds and self.draft is not None:
            origin = self.origin or 0.
            self.draft = [value+origin for value in self.life_range.getRegion()]
            self.dirty = True
            self.set_controls(True)
            self.draw_track_map()

    def discard(self):
        self.selected_id = None
        self.draw_selected()

    def apply(self):
        a=self.current_attempt()
        if not a or not self.draft or not self.path:return
        self.saving=True;self.set_controls(False)
        self.hint.setText('Saving boundaries…')
        self.worker.request('save',self.path,a,self.token,start=self.draft[0],end=self.draft[1],case=self.case_type.currentText())


def outcome_color(outcome):
    name = outcome.casefold()
    if name.startswith('wire '):
        return theme_color('slow')
    if 'track lost' in name:
        return theme_color('warning')
    if 'bolter' in name or 'abort' in name:
        return theme_color('on')
    return theme_color('text')



def boundary_bar(points, stamp):
    """Interpolate an endpoint and draw across its local flight direction."""
    for first, second in zip(points, points[1:]):
        if not first[0] <= stamp <= second[0] or not np.isfinite([first, second]).all():
            continue
        duration = second[0] - first[0]
        delta = second[1:] - first[1:]
        length = np.linalg.norm(delta)
        if duration <= 0 or length <= 1e-9:
            continue
        center = first[1:] + delta * ((stamp-first[0])/duration)
        normal = np.array([-delta[1], delta[0]]) * (.04/length)
        return np.array([center-normal, center+normal])
    return None


class RecordingTimeAxis(pg.AxisItem):
    boundaries = ()

    def set_boundaries(self, values):
        values = tuple(values)
        if values != self.boundaries:
            self.boundaries = values
            self.invalidate_labels()

    def invalidate_labels(self):
        self.picture = None
        self.update()

    def tickValues(self, minVal, maxVal, size):
        levels = super().tickValues(minVal, maxVal, size)
        endpoints = sorted({v for v in self.boundaries if minVal <= v <= maxVal})
        if not endpoints or size <= 0:
            return levels
        clearance = 76 * (maxVal-minVal)/size
        return [(0, endpoints)] + [
            (spacing, [v for v in values if all(abs(v-end) >= clearance for end in endpoints)])
            for spacing, values in levels]

    def tickStrings(self, values, scale, spacing):
        labels = [('−' if value < 0 else '') + ApproachView.time_text(abs(value))
                  for value in values]
        if spacing != 0:
            return labels
        roles = dict(zip(self.boundaries, ('slow', 'warning')))
        return [BoundaryTimeLabel(label, roles[value]) for value, label in zip(values, labels)]

    def drawPicture(self, painter, axis_spec, tick_specs, text_specs):
        ordinary = [spec for spec in text_specs if not isinstance(spec[2], BoundaryTimeLabel)]
        super().drawPicture(painter, axis_spec, tick_specs, ordinary)
        for rect, flags, label in text_specs:
            if isinstance(label, BoundaryTimeLabel):
                painter.setPen(pg.mkPen(theme_color(label.role)))
                painter.drawText(rect, int(flags), label)


class BoundaryTimeLabel(str):
    def __new__(cls, text, role):
        label = super().__new__(cls, text)
        label.role = role
        return label
