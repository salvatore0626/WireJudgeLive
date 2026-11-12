"""Sortable live and replay aircraft roster."""
import time
from collections import defaultdict, deque
from statistics import mean

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.aircraft.aircraft_labels import rtb_distance, rtb_speed
from wirejudge.approach.approach_scopes import ScopePane
from wirejudge.aircraft.flight_instruments import fuel_values, low_fuel
from wirejudge.map.marshal_vectors import compact_duration, vector_owner
from wirejudge.ui.theme import color as theme_color

SPEED_WINDOW = 15.


def roster_rows(sample, carrier_id, grouped, speeds=None):
    carriers = sample.get('carriers', [])
    carrier = next((c for c in carriers if c['instance_id'] == carrier_id),
                   max(carriers, key=lambda c: len(c.get('cables') or []), default={}))
    rows, flights = [], defaultdict(list)
    for aircraft in sample.get('aircraft', []):
        name, _ = vector_owner(aircraft)
        group = flight_identity(aircraft)[0]
        observed_aircraft, observed_carrier = aircraft.get('_radar_observation', (aircraft, carrier))
        distance = rtb_distance(observed_aircraft, observed_carrier)
        instance = aircraft['instance_id']
        speed = speeds[instance] if speeds and instance in speeds else rtb_speed(observed_aircraft)
        eta = distance / speed if distance is not None and speed else None
        fuel = fuel_values((aircraft.get('systems') or {}).get('fuel_tanks'))[2]
        row = (name, eta, distance, fuel, aircraft)
        if grouped and group != 'Ungrouped':
            flights[group].append(row)
        else:
            rows.append(row)
    for group, members in flights.items():
        values = [[v for v in column if v is not None] for column in list(zip(*members))[1:4]]
        metrics = [aggregate(column) if column else None
                   for aggregate, column in zip((mean, mean, min), values)]
        rows.append((group, *metrics, members[0][4]))
    return rows


class RosterView(QWidget):
    def __init__(self, kind, manager):
        super().__init__()
        self.manager = manager
        manager.view = self
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 5)
        self.grouped = QCheckBox('Group by flight')
        self.grouped.setChecked(manager.owner.settings.store.value('roster/grouped', True, type=bool))
        self.grouped.toggled.connect(manager.set_grouped)
        layout.addWidget(self.grouped)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.table.setHeaderLabels(['Aircraft', 'RTB Time', 'Distance', 'Fuel'])
        self.table.itemDoubleClicked.connect(self.center_flight)
        self.table.headerItem().setToolTip(1, 'Straight-line time to the carrier using airspeed')
        header = self.table.header()
        header.setSectionsClickable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sortIndicatorChanged.connect(self.render)
        layout.addWidget(self.table)
        self.rows = []
        self.sample = None
        self.units = manager.owner.units
        self.render()

    def render(self, *_):
        header = self.table.header()
        column = header.sortIndicatorSection()
        descending = header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        rows = sorted(self.rows, key=lambda row: row[0].casefold())
        known = [row for row in rows if row[column] is not None]
        missing = [row for row in rows if row[column] is None]
        known.sort(key=lambda row: row[0].casefold() if column == 0 else row[column], reverse=descending)
        bar = self.table.verticalScrollBar()
        offset = bar.value()
        self.table.setUpdatesEnabled(False)
        self.table.clear()
        lowest_fuel = min((row[3] for row in rows if row[3] is not None), default=None)
        decimals = 0 if self.units.distance_name == 'NM' else 2
        for name, eta, distance, fuel, aircraft in known + missing:
            item = QTreeWidgetItem([
                name, compact_duration(eta) if eta is not None else '—',
                f'{self.units.distance(distance):.{decimals}f} {self.units.distance_symbol}' if distance is not None else '—',
                f'{fuel:.0f}%' if fuel is not None else '—',
            ])
            if self.grouped.isChecked() and name == flight_identity(aircraft)[0] and name != 'Ungrouped':
                item.setData(0, Qt.ItemDataRole.UserRole, name)
            for col in (1, 2, 3):
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setForeground(0, QColor.fromRgbF(*self.manager.owner.map.color_for(aircraft, self.sample)))
            if lowest_fuel is not None and fuel == lowest_fuel:
                for col in (0, 3):
                    font = self.table.font()
                    font.setBold(True)
                    item.setFont(col, font)
                item.setForeground(3, QColor(theme_color('accent')))
            if low_fuel(fuel):
                item.setForeground(3, QColor(theme_color('warning')))
            self.table.addTopLevelItem(item)
        bar.setValue(offset)
        self.table.setUpdatesEnabled(True)

    def center_flight(self, item, column):
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name is not None:
            self.manager.owner.center_flight(name)

    def content_width(self):
        header = self.table.header()
        columns = sum(max(self.table.sizeHintForColumn(i), header.sectionSizeHint(i))
                      for i in range(header.count()))
        return columns + self.table.verticalScrollBar().sizeHint().width() + 26


class RosterTool(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.frame = (None, None, owner.units)
        self.last_key = None
        self.last_update = float('-inf')
        self.speed_history = deque()
        self.history_context = None
        self.pane = ScopePane('Roster', 'roster', self, view_factory=RosterView)
        owner.map.installEventFilter(self)
        self.layout_panes()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.layout_panes()
            self.sync_timer()
        return False

    def layout_panes(self):
        if self.pane.detached:
            if self.pane.isVisible():
                self.fit_width()
            return
        view = self.owner.map
        width, height = self.pane.user_size or (350, 295)
        width = max(width, self.view.content_width())
        self.pane.place(8, 66, min(width, max(190, view.width() - 16)),
                        min(height, max(160, view.height() - 74)))
        self.pane.raise_()
        self.owner.map_controls.raise_()

    def set_frame(self, sample, carrier_id, units, history=(), context=None):
        self.frame = (sample, carrier_id, units)
        capture = (sample or {}).get('capture') or {}
        stamp = capture.get('monotonic_s')
        key = (capture.get('connection_id'), context)
        if stamp is None:
            self.speed_history.clear()
        else:
            if key != self.history_context or (self.speed_history and stamp < self.speed_history[-1][0]):
                self.speed_history.clear()
            cutoff = stamp - SPEED_WINDOW
            previous = self.speed_history[-1][0] if self.speed_history else float('-inf')
            if stamp > previous:
                fresh = []
                for frame in reversed(history):
                    source = frame.get('capture') or {}
                    when = source.get('monotonic_s')
                    if when is None or when >= stamp:
                        continue
                    if when <= previous or when < cutoff:
                        break
                    if source.get('connection_id') == key[0]:
                        fresh.append((when, frame))
                for when, frame in reversed(fresh):
                    self.record_speeds(frame, when)
                self.record_speeds(sample, stamp)
            while self.speed_history and self.speed_history[0][0] < cutoff:
                self.speed_history.popleft()
        self.history_context = key
        self.sync_timer()

    def record_speeds(self, sample, stamp):
        values = {}
        for aircraft in sample.get('aircraft', []):
            observed = aircraft.get('_radar_observation', (aircraft, None))[0]
            speed = rtb_speed(observed)
            if speed is not None:
                values[aircraft['instance_id']] = speed
        self.speed_history.append((stamp, values))

    def set_grouped(self, checked):
        self.owner.settings.store.setValue('roster/grouped', checked)
        self.sync_timer()

    def sync_timer(self):
        if not self.pane.isVisible():
            return
        sample, carrier_id, units = self.frame
        grouped = self.view.grouped.isChecked()
        key = (sample is None, carrier_id, units.distance_name, grouped)
        now = time.monotonic()
        if key == self.last_key and now - self.last_update < 3.:
            return
        self.last_key, self.last_update = key, now
        samples = defaultdict(list)
        for _, speeds in self.speed_history:
            for instance, speed in speeds.items():
                samples[instance].append(speed)
        averages = {instance: mean(values) for instance, values in samples.items()}
        self.view.units = units
        self.view.sample = sample
        self.view.rows = roster_rows(sample or {}, carrier_id, grouped, averages)
        self.view.render()
        self.fit_width()

    def fit_width(self):
        needed = self.view.content_width()
        if self.pane.detached:
            window = self.pane.floating_window
            screen = window.screen()
            limit = screen.availableGeometry().width() if screen else needed
            if window.width() < min(needed, limit):
                window.resize(min(needed, limit), window.height())
        elif self.pane.width() < min(needed, max(190, self.owner.map.width() - 16)):
            self.layout_panes()

    def apply_theme(self):
        if self.pane.floating_window:
            self.pane.floating_window.setStyleSheet(self.owner.styleSheet())
        self.view.render()
        if self.pane.isVisible():
            self.fit_width()

    def shutdown(self):
        if self.pane.floating_window:
            self.pane.floating_window.hide()
        self.pane.hide()
