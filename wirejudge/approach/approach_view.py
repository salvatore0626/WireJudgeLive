from wirejudge.ui.theme import color as theme_color
"""Distance-based approach review, refreshed at telemetry rate, not render rate."""
import math
from wirejudge.display_units import DisplayUnits, ALTITUDE_FACTORS, SPEED_FACTORS
from wirejudge.scoring.landing_scoring import DEFAULT_RULES
from wirejudge.approach.approach_geometry import NM, ILS_GS_FULL_SCALE_DEG, ILS_LOC_FULL_SCALE_DEG
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from wirejudge.ui.control_icons import approach_case_badge, podium_crowns
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem, QLabel, QHeaderView, QPushButton, QCheckBox


class ApproachView(QWidget):
    attempt_activated=Signal(object)
    def __init__(self, capture, colors, with_plots=True):
        super().__init__()
        self.capture, self.colors = capture, colors
        self.units = DisplayUnits()
        self.chart_case = None
        self.measurements = np.empty((0, 6))
        self.pinned = False
        self.crowns = podium_crowns()
        self.last_key = None
        self.cursor = None
        self.origin = None
        self.catalog = {}
        self.items = {}
        self.groups = {}
        self.scores = {}
        self.last_scores = None
        self.score_mode = 'Additive'
        self.attempt_order = ()
        row = QHBoxLayout(self)
        split = QSplitter()
        row.addWidget(split)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Pilot / attempt'] if with_plots else ['Pilot / attempt', 'Outcome'])
        self.tree.setMinimumWidth(265)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        if not with_plots:
            self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.tree)
        content = QWidget(); col = QVBoxLayout(content)
        self.content_layout = col
        self.summary = QLabel('No recorded approaches. Fly an approach, or open a DVR recording from Scoreboard.')
        self.summary.setWordWrap(True)
        if with_plots:
            self.summary.setStyleSheet("font-size: 19px; font-weight: 600;")
        col.addWidget(self.summary)
        if with_plots:
            controls = QHBoxLayout()
            controls.addWidget(QLabel('Drag to pan · Scroll to zoom · Click to pin or unpin a sample'),1)
            self.reset_graphs_button = QPushButton('Reset graph views')
            self.reset_graphs_button.clicked.connect(self.reset_graph_views)
            controls.addWidget(self.reset_graphs_button)
            self.loc_degrees = QCheckBox("Localizer in degrees")
            self.loc_degrees.toggled.connect(self.change_localizer_scale)
            controls.addWidget(self.loc_degrees)
            col.addLayout(controls)
        self.plots, self.curves, self.limits, self.crosshairs = [], [], [], []
        if with_plots:
            self.readouts, self.grade_markers = [], []
            for index, title in enumerate(('Glide path', 'Localizer deviation · + left / − right', 'AoA / Airspeed')):
                plot = pg.PlotWidget(background=theme_color('map'))
                plot.setTitle(title, color=theme_color('muted'), size='10pt')
                plot.getAxis('left').setWidth(76)
                plot.showAxis('right')
                plot.getAxis('right').setWidth(65)
                plot.getAxis('right').setStyle(showValues=False)
                plot.setLabel('bottom', 'Distance aft of wire 3', units='NM')
                for axis in ('left', 'bottom', 'right'):
                    plot.getAxis(axis).enableAutoSIPrefix(False)
                plot.showGrid(x=True, y=True, alpha=.12)
                plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
                plot.setMenuEnabled(False)
                plot.hideButtons()
                plot.getViewBox().invertX(True)
                if self.plots:
                    plot.setXLink(self.plots[0])
                curve = plot.plot(pen=pg.mkPen(theme_color(('chart_glide', 'chart_localizer', 'chart_aoa')[index]), width=2))
                axis_view = plot.getViewBox()
                if index == 2:
                    self.speed_view = pg.ViewBox(enableMouse=False)
                    plot.scene().addItem(self.speed_view)
                    plot.getAxis('right').linkToView(self.speed_view)
                    self.speed_view.invertX(True)
                    self.speed_view.setXLink(axis_view)
                    axis_view.sigResized.connect(self.align_speed_axis)
                    self.ias_curve = pg.PlotCurveItem(pen=pg.mkPen(theme_color('chart_ias'), width=2))
                    self.speed_view.addItem(self.ias_curve)
                limits = []
                for _ in range(3):
                    line = pg.PlotCurveItem(pen=pg.mkPen(theme_color('chart_limit'),
                                                       style=Qt.PenStyle.DashLine))
                    (self.speed_view if index == 2 else axis_view).addItem(line)
                    limits.append(line)
                crosshair = pg.InfiniteLine(pen=pg.mkPen(theme_color('muted')))
                plot.addItem(crosshair, ignoreBounds=True)
                crosshair.hide()
                plot.scene().sigMouseMoved.connect(lambda pos, p=plot: self.inspect_point(p, pos))
                plot.scene().sigMouseClicked.connect(lambda event, p=plot: self.pin_point(p, event))
                readout = pg.TextItem(anchor=(0, 1), fill=pg.mkBrush(theme_color('panel')))
                plot.addItem(readout, ignoreBounds=True)
                readout.setZValue(30)
                readout.hide()
                self.readouts.append(readout)
                self.grade_markers.append([])
                self.plots.append(plot)
                self.curves.append(curve)
                self.limits.append(limits)
                self.crosshairs.append(crosshair)
                col.addWidget(plot, 1)
            self.reset_graph_views()
        split.addWidget(content); split.setSizes([330, 1050])
        self.tree.itemSelectionChanged.connect(self.draw_selected)
        self.tree.itemDoubleClicked.connect(self.activate_attempt)

    def activate_attempt(self,item,column=0):
        attempt=self.catalog.get(item.data(0,Qt.ItemDataRole.UserRole))
        if attempt:self.attempt_activated.emit(attempt)

    def set_distance_units(self, units):
        changed = units.altitude_name != self.units.altitude_name
        self.units = units
        if changed:
            self.draw_selected()
            self.reset_graph_views()

    def apply_theme(self):
        if not self.plots:
            return
        for curve, role in zip((*self.curves, self.ias_curve),
                               ('chart_glide', 'chart_localizer', 'chart_aoa', 'chart_ias')):
            curve.setPen(pg.mkPen(theme_color(role), width=2))
        for limits in self.limits:
            for line in limits:
                line.setPen(pg.mkPen(theme_color('chart_limit'), style=Qt.PenStyle.DashLine))
        for crosshair, readout in zip(self.crosshairs, self.readouts):
            crosshair.setPen(pg.mkPen(theme_color('chart_cursor')))
            readout.setColor(theme_color('chart_cursor'))
            readout.fill = pg.mkBrush(theme_color('panel'))
            readout.update()
        for markers in self.grade_markers:
            for marker in markers:
                marker.setPen(pg.mkPen(theme_color('chart_gate'), style=Qt.PenStyle.DotLine))
                label = getattr(marker, 'label', None)
                if label is not None:
                    label.setColor(theme_color('chart_gate'))

    def align_speed_axis(self):
        if self.plots:
            view = self.plots[-1].getViewBox()
            self.speed_view.setGeometry(view.sceneBoundingRect())
            self.speed_view.linkedViewChanged(view, self.speed_view.XAxis)

    def reset_graph_views(self):
        if not self.plots:
            return
        case3 = self.chart_case == 'Case 3'
        scale = ALTITUDE_FACTORS[self.units.altitude_name]
        self.plots[0].setXRange(0, 10.5 if case3 else 1, padding=0)
        for plot, (low, high) in zip(self.plots, ((-10*scale, (450 if case3 else 150)*scale),
                                                ((-5, 5) if self.loc_degrees.isChecked() else ((-850 if case3 else -90)*scale, (850 if case3 else 90)*scale)),
                                                (4, 12))):
            plot.setYRange(low, high, padding=0)
        self.speed_view.setYRange(100, 300, padding=0)

    def change_localizer_scale(self, *_):
        self.pinned = False
        for item in (*self.readouts, *self.crosshairs):
            item.hide()
        self.draw_selected()
        scale = ALTITUDE_FACTORS[self.units.altitude_name]
        extent = 5 if self.loc_degrees.isChecked() else (850 if self.chart_case == 'Case 3' else 90)*scale
        self.plots[1].setYRange(-extent, extent, padding=0)

    def pin_point(self, plot, event):
        if event.button() == Qt.MouseButton.LeftButton and plot.getViewBox().sceneBoundingRect().contains(event.scenePos()):
            self.pinned = False if self.pinned else self.inspect_point(plot, event.scenePos())

    def inspect_point(self, plot, position):
        if self.pinned or not len(self.measurements) or not plot.getViewBox().sceneBoundingRect().contains(position):
            return False
        distance = plot.getViewBox().mapSceneToView(position).x()
        valid = self.measurements[np.isfinite(self.measurements[:, 0])]
        if not len(valid):
            return False
        x, height, lateral, aoa, ias, loc_degrees = valid[np.argmin(abs(valid[:, 0] - distance))]
        for line in self.crosshairs:
            line.setPen(pg.mkPen(theme_color('chart_cursor')))
            line.setValue(x)
            line.show()
        unit = self.units.altitude_symbol
        def value(n, suffix):
            return f'{n:,.2f} {suffix}' if math.isfinite(n) else '—'
        loc_feet = x * NM * math.tan(math.radians(loc_degrees)) * ALTITUDE_FACTORS['Feet']
        values = [(height, value(height, unit)),
                  (lateral, f'{value(loc_degrees, "°")}  /  {value(loc_feet, "ft")}'),
                  (aoa, value(aoa, '°') + (f'  /  {value(ias, "kt")}' if self.chart_case == 'Case 3' else ''))]
        for chart, label, (y, text) in zip(self.plots, self.readouts, values):
            low, high = chart.viewRange()[1]
            y = min(max(y, low), high) if math.isfinite(y) else (low+high)/2
            label.setColor(theme_color('chart_cursor'))
            label.setText(f'{x:.3f} NM  ·  {text}')
            # Distance decreases to the right. Keep the label inside the plot.
            label.setAnchor((1 if x < sum(chart.viewRange()[0])/2 else 0, 0 if y > (low+high)/2 else 1))
            label.setPos(x, y)
            label.show()
        return True

    @staticmethod
    def time_text(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'

    def recording_origins(self, attempts, origin):
        starts = {}
        for attempt in attempts:
            connection = attempt['connection_id']
            starts[connection] = min(starts.get(connection, attempt['start_s']), attempt['start_s'])
        for writer in self.capture.writers:
            if writer.first_monotonic_s is not None:
                starts[writer.session_id] = writer.first_monotonic_s
        if origin is not None:
            if self.capture.analysis_archive:
                return dict.fromkeys(starts, origin)
            starts[self.capture.connection_id] = origin
        return starts

    def score_text(self, identifier):
        grade = self.scores.get(identifier)
        if not grade:
            return '—'
        return f"{grade['earned' if self.score_mode == 'Additive' else 'deductive']:,.1f}"

    def refresh(self, cursor=None, force=False, origin=None):
        # Analysis always consumes the latest catalog/points, never the map clock.
        cursor=None
        revision, catalog = self.capture.approach_catalog()
        key = revision, origin, self.score_mode
        if not force and key == self.last_key and self.scores is self.last_scores:
            return
        self.last_key, self.cursor = key, cursor
        self.last_scores = self.scores
        self.origin = origin
        catalog = sorted(catalog, key=lambda a: (a['pilot'].casefold(), a['pilot'], a['start_s'], a['id']))
        self.catalog = {a['id']: a for a in catalog}
        order = tuple((a['pilot'], a['id']) for a in catalog)
        current = self.tree.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        previous_block = self.tree.blockSignals(True)
        try:
            if order != self.attempt_order:
                expanded = {pilot: group.isExpanded() for pilot, group in self.groups.items()}
                self.tree.clear()
                self.groups.clear()
                self.items.clear()
                for pilot, identifier in order:
                    if pilot not in self.groups:
                        group = QTreeWidgetItem(self.tree, [pilot])
                        group.setExpanded(expanded.get(pilot, True))
                        self.groups[pilot] = group
                    item = QTreeWidgetItem(self.groups[pilot])
                    item.setData(0, Qt.ItemDataRole.UserRole, identifier)
                    self.items[identifier] = item
                self.attempt_order = order
            starts = self.recording_origins(catalog, origin)
            counts = dict.fromkeys(self.groups, 0)
            for a in catalog:
                pilot, identifier = a['pilot'], a['id']
                counts[pilot] += 1
                timestamp = self.time_text(a['start_s'] - starts[a['connection_id']])
                score = self.score_text(identifier)
                row = f"Attempt {counts[pilot]} - {a.get('airframe') or 'Aircraft'} - {score} - {timestamp}"
                item = self.items[identifier]
                item.setText(0, row)
                item.setIcon(0, approach_case_badge(a.get('case')))
                item.setToolTip(0, f"{a.get('case') or 'Case 1'} · {row}")
                item.setForeground(0, QColor(theme_color('text')))
            if catalog:
                if selected not in self.items:
                    selected = max(catalog, key=lambda a: a['start_s'])['id']
                self.tree.setCurrentItem(self.items[selected])
            best = {}
            for attempt in catalog:
                identifier = attempt['id']
                grade = self.scores.get(identifier)
                if grade and attempt.get('end_s') is not None:
                    score = grade['earned' if self.score_mode == 'Additive' else 'deductive']
                    pilot = attempt['pilot']
                    if pilot not in best or score > best[pilot][0]:
                        best[pilot] = (score, identifier)
            leaders = sorted(best, key=lambda pilot: (-best[pilot][0], pilot.casefold()))[:3]
            for pilot, group in self.groups.items():
                group.setIcon(0, self.crowns[leaders.index(pilot)] if pilot in leaders else QIcon())
            for _, identifier in best.values():
                self.items[identifier].setForeground(0, QColor(theme_color('slow')))
        finally:
            self.tree.blockSignals(previous_block)
        self.draw_selected()

    def draw_selected(self):
        selected = self.tree.currentItem()
        a = self.catalog.get(selected.data(0, Qt.ItemDataRole.UserRole)) if selected else None
        if not self.plots:
            return
        identifier = a['id'] if a else None
        if identifier != getattr(self, 'chart_attempt', None):
            self.chart_attempt = identifier
            self.pinned = False
            for item in (*self.crosshairs, *self.readouts):
                item.hide()
        for plot, markers in zip(self.plots, self.grade_markers):
            for marker in markers:
                plot.removeItem(marker)
            markers.clear()
        if not a:
            self.measurements = np.empty((0, 6))
            self.speed_view.hide()
            axis = self.plots[-1].getAxis('right')
            axis.setLabel('')
            axis.setStyle(showValues=False)
            for curve in [*self.curves, self.ias_curve, *(line for group in self.limits for line in group)]:
                curve.setData([], [])
            self.summary.setText('No recorded approaches. Fly an approach, or open a DVR recording from Scoreboard.' if not self.catalog else 'Select an attempt to review its flight history.')
            return
        case = a.get('case', 'Case 1')
        case3 = case == 'Case 3'
        axis = self.plots[-1].getAxis('right')
        axis.setLabel('IAS' if case3 else '', units='kt' if case3 else '')
        axis.setStyle(showValues=case3)
        self.speed_view.setVisible(case3)
        unit = self.units.altitude_symbol
        self.plots[0].setLabel('left', 'Above deck (AGL)', units=unit)
        degrees = self.loc_degrees.isChecked()
        self.plots[1].setLabel('left', 'Deviation', units='°' if degrees else unit)
        self.plots[2].setLabel('left', 'AoA', units='°')
        if case != self.chart_case:
            self.chart_case = case
            self.reset_graph_views()
        limit = 10.5 if case3 else 1
        speed_by_time = {row[0]: row[9] for row in self.capture.timeline_score_samples(a, a.get('end_s') or a['last_s'])
                         if len(row) > 9} if case3 else {}
        rows = []
        scale = ALTITUDE_FACTORS[self.units.altitude_name]
        for stamp, distance, glide, loc, aoa in self.capture.approach_points(identifier):
            if distance is None:
                rows.append([None] * 6)
            elif 0 <= distance <= limit:
                along = distance * NM * scale
                rows.append([distance, along * math.tan(math.radians(a['glide_deg'] + glide)) if glide is not None else None,
                             (loc if degrees else along * math.tan(math.radians(loc))) if loc is not None else None,
                             aoa, speed_by_time.get(stamp), loc])
        data = np.asarray(rows, dtype=float).reshape((-1, 6))
        data[:, 4] *= SPEED_FACTORS['Knots']
        self.measurements = data
        for column, curve in enumerate(self.curves, 1):
            curve.setData(data[:, 0], data[:, column], connect='finite')
        self.ias_curve.setData(data[:, 0], data[:, 4], connect='finite')
        for line, angle in zip(self.limits[0], (a['glide_deg'], a['glide_deg'] - ILS_GS_FULL_SCALE_DEG,
                                               a['glide_deg'] + ILS_GS_FULL_SCALE_DEG)):
            end = 3 if case3 else 1
            line.setData([0, end], [0, end * NM * scale * math.tan(math.radians(angle))])
        for line, angle in zip(self.limits[1], (0, -ILS_LOC_FULL_SCALE_DEG, ILS_LOC_FULL_SCALE_DEG)):
            line.setData([0, limit], [angle, angle] if degrees else [0, limit * NM * scale * math.tan(math.radians(angle))])
        for line, offset in zip(self.limits[2], (0, -25, 25)):
            line.setData([3, 6, 6, 10], [200+offset, 200+offset, 250+offset, 250+offset])
        self.align_speed_axis()
        rules = getattr(self.capture, 'scoring_rules', DEFAULT_RULES)[case]
        boundaries = [(rules['start_nm'], 'Final start'), (rules['end_nm'], 'Final end')]
        if case3:
            boundaries += [(rules['platform_start_nm'], 'Platform start'), (rules['platform_end_nm'], 'Platform end')]
        for index, (plot, markers) in enumerate(zip(self.plots, self.grade_markers)):
            for distance, title in boundaries:
                marker = pg.InfiniteLine(distance, angle=90,
                    pen=pg.mkPen(theme_color('chart_gate'), style=Qt.PenStyle.DotLine),
                    label=title if index == 0 else None,
                    labelOpts={'color': theme_color('chart_gate'), 'position': .82})
                plot.addItem(marker, ignoreBounds=True)
                markers.append(marker)
        self.summary.setText(f"{a['pilot']} - {a.get('airframe') or 'Aircraft'} - {a['outcome']}  ·  {case}")
