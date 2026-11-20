"""Shared score breakdown and filters for current and saved attempts."""
from datetime import datetime

from PySide6.QtCore import QDate, QItemSelectionModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QToolButton,
)

from wirejudge.aircraft.aircraft_menu import AircraftMenu
from wirejudge.display_units import SPEED_FACTORS
from wirejudge.ui.control_icons import podium_crowns
from wirejudge.scoring.landing_scoring import COMPONENTS
from wirejudge.scoring.leaderboard_store import utc_duplicate_ids
from wirejudge.ui.theme import color as theme_color


class ScoreTable(QWidget):
    def __init__(self, leaderboard=False):
        super().__init__()
        self.leaderboard = leaderboard
        self.facets = {}
        self.records = []
        self.duplicate_ids = set()
        self.crowns = podium_crowns()
        self.visible_records = []
        self.collapsed_pilots = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter username, airframe, carrier or result')
        self.search.textChanged.connect(self.render)
        filters.addWidget(self.search, 1)
        if leaderboard:
            self.facets = {name: set() for name in ('Case', 'Airframe', 'Result', 'Status', 'Duplicates')}
            button = self.filter_button = QToolButton()
            button.setText('Filters')
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self.filter_menu = AircraftMenu(button)
            self.filter_menu.aboutToShow.connect(self.build_filter_menu)
            button.setMenu(self.filter_menu)
            filters.addWidget(button)
        else:
            self.case = QComboBox()
            self.case.addItems(['All cases', 'Case 1', 'Case 3'])
            self.case.currentIndexChanged.connect(self.render)
            filters.addWidget(self.case)
        if leaderboard:
            self.date_filter = QCheckBox('Date range')
            self.date_filter.toggled.connect(self.render)
            filters.addWidget(self.date_filter)
            self.start = QDateEdit(QDate.currentDate().addMonths(-1))
            self.end = QDateEdit(QDate.currentDate())
            for field in (self.start, self.end):
                field.setCalendarPopup(True)
                field.setDisplayFormat('yyyy-MM-dd')
                field.dateChanged.connect(self.render)
                filters.addWidget(field)
        layout.addLayout(filters)
        modes = QHBoxLayout()
        self.best_only = QCheckBox('Best Only')
        self.group_airframe = QCheckBox('Group by airframe')
        self.last_only = QCheckBox('Last Only')
        self.best_only.setToolTip('Show each pilot’s final attempt with the highest percentage score in the filtered results.')
        self.group_airframe.setToolTip('Keep each airframe’s attempts together and apply Best Only or Last Only per airframe.')
        self.last_only.setToolTip('Show each pilot’s most recent attempt in the filtered results.')
        for toggle in (self.best_only, self.group_airframe, self.last_only):
            modes.addWidget(toggle)
        self.best_only.toggled.connect(lambda checked: self.toggle_mode(checked, self.last_only))
        self.last_only.toggled.connect(lambda checked: self.toggle_mode(checked, self.best_only))
        self.group_airframe.toggled.connect(self.render)
        modes.addStretch()
        layout.addLayout(modes)

        self.tree = QTreeWidget()
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        headings = ['Username', 'Case', 'Wire', 'Airframe', 'Date / time',
                    'Plat. Pos.', 'Plat. Spd.', *COMPONENTS[2:-1], 'Wire Pts.', 'Total', 'Status', 'Notes']
        self.tree.setHeaderLabels(headings)
        self.total_column = headings.index('Total')
        self.tree.headerItem().setToolTip(headings.index('Notes'), 'Hover for trap weight and approach crosswind')
        self.tree.headerItem().setToolTip(5, 'Platform Position')
        self.tree.headerItem().setToolTip(6, 'Platform Speed')
        self.tree.headerItem().setToolTip(headings.index('Wire Pts.'), 'Wire score')
        self.tree.itemExpanded.connect(lambda item: self.collapsed_pilots.discard(item.text(0)))
        self.tree.itemCollapsed.connect(lambda item: self.collapsed_pilots.add(item.text(0)))
        self.tree.setHorizontalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setResizeContentsPrecision(-1)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(0)
        header.setSectionsMovable(True)
        header.setFirstSectionMovable(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(headings.index('Total'), Qt.SortOrder.DescendingOrder)
        header.sortIndicatorChanged.connect(self.render)
        layout.addWidget(self.tree)

    @staticmethod
    def status(record):
        return 'Provisional' if record['provisional'] else record.get('status', 'Final')

    @staticmethod
    def notes_tooltip(notes):
        weight = notes.get('mass_kg')
        weight_text = 'Unavailable' if weight is None else f'{weight:,.0f} kg'
        average = notes.get('average_crosswind_mps')
        peak = notes.get('peak_crosswind_mps')
        factor = SPEED_FACTORS['Knots']
        average_text = 'Unavailable' if average is None else f'{average * factor:.1f} kt'
        peak_text = 'Unavailable'
        if peak is not None:
            side = ' from right' if peak > 0 else ' from left' if peak < 0 else ''
            peak_text = f'{abs(peak) * factor:.1f} kt{side}'
        return (f'Trap weight: {weight_text}\nAverage crosswind: {average_text}'
                f'\nPeak crosswind: {peak_text}')

    def build_filter_menu(self):
        for action in self.filter_menu.actions():
            if action.menu():
                action.menu().deleteLater()
        self.filter_menu.clear()
        clear = self.filter_menu.addAction('Clear filters')
        clear.setCheckable(True)
        clear.triggered.connect(self.clear_facets)
        values = {
            'Case': {'Case 1', 'Case 3'},
            'Airframe': {r.get('aircraft') or 'Unknown airframe' for r in self.records},
            'Result': {r.get('outcome') or '—' for r in self.records},
            'Status': {'Final', 'Provisional'},
            'Duplicates': {'Possible duplicate', 'No UTC match'},
        }
        for facet, options in values.items():
            menu = AircraftMenu(facet, self.filter_menu)
            self.filter_menu.addMenu(menu)
            for option in sorted(options | self.facets[facet], key=str.casefold):
                action = menu.addAction(option)
                action.setCheckable(True)
                action.setChecked(option in self.facets[facet])
                action.triggered.connect(
                    lambda checked, key=facet, value=option: self.set_facet(key, value, checked))
            menu.setEnabled(bool(options or self.facets[facet]))

    def set_facet(self, facet, value, checked):
        if checked:
            self.facets[facet].add(value)
        else:
            self.facets[facet].discard(value)
        self.render()

    def clear_facets(self):
        for options in self.facets.values():
            options.clear()
        for action in self.filter_menu.actions():
            action.setChecked(False)
            if action.menu():
                for option in action.menu().actions():
                    option.setChecked(False)
        self.render()

    def toggle_mode(self, checked, other):
        if checked and other.isChecked():
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        self.render()

    def reduce_rows(self, rows):
        last = self.last_only.isChecked()
        if not last and not self.best_only.isChecked():
            return rows
        by_airframe = self.group_airframe.isChecked()
        best = {}
        for row in rows:
            keys, _, _, pilot, airframe, record = row
            started = keys[4] if keys[4] is not None else float('-inf')
            group = (pilot, airframe) if by_airframe else (pilot,)
            if last:
                rank = (started, record['attempt_id'])
            else:
                if self.status(record) != 'Final' or not record['maximum']:
                    continue
                rank = (record['score']/record['maximum'], started, record['attempt_id'])
            if group not in best or rank > best[group][0]:
                best[group] = (rank, row)
        return [row for _, row in best.values()]

    def set_records(self, records):
        if self.records != records:
            self.records = records
            if self.leaderboard:
                self.duplicate_ids = utc_duplicate_ids(records)
            self.render()

    def rankings(self, records):
        best_by_pilot = {}
        best_by_case = {}
        for record in records:
            if record['provisional'] or not record['maximum']:
                continue
            pilot = record.get('pilot') or 'Unknown username'
            key = (pilot, record['case'])
            score = record['score']
            best_by_pilot[pilot] = max(score, best_by_pilot.get(pilot, score))
            best_by_case[key] = max(score, best_by_case.get(key, score))
        leaders = sorted(best_by_pilot, key=lambda pilot: (-best_by_pilot[pilot], pilot.casefold()))[:3]
        return dict(zip(leaders, self.crowns)), best_by_case

    def render(self, *_):
        if self.leaderboard:
            count = sum(map(len, self.facets.values()))
            self.filter_button.setText(f'Filters ({count})' if count else 'Filters')
        role = Qt.ItemDataRole.UserRole
        tree = self.tree
        selected = {item.data(0, role) for item in tree.selectedItems()}
        current = tree.currentItem()
        current_id = current.data(0, role) if current else None
        scroll = tree.verticalScrollBar().value()
        text = self.search.text().strip().casefold()
        case = 'All cases' if self.leaderboard else self.case.currentText()
        dated = self.leaderboard and self.date_filter.isChecked()
        if dated:
            start, end = self.start.date().toPython(), self.end.date().toPython()
        rows = []
        date_format = '%Y-%m-%d %H:%M:%S'
        for record in self.records:
            pilot = record.get('pilot') or 'Unknown username'
            airframe = record.get('aircraft') or 'Unknown airframe'
            outcome = record.get('outcome') or '—'
            if text and text not in ' '.join((pilot, airframe, record.get('carrier') or '', outcome)).casefold():
                continue
            if case != 'All cases' and record['case'] != case:
                continue
            status = self.status(record)
            fields = {'Case': record['case'], 'Airframe': airframe, 'Result': outcome, 'Status': status}
            fields['Duplicates'] = ('Possible duplicate' if record['attempt_id'] in self.duplicate_ids
                                    else 'No UTC match')
            if any(options and fields[key] not in options for key, options in self.facets.items()):
                continue
            timestamp = record.get('started_utc')
            started = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone() if timestamp else None
            if dated and (started is None or not start <= started.date() <= end):
                continue
            date = started.strftime(date_format) if started else '—'
            total, maximum = record['score'], record['maximum']
            wire = record.get('wire')
            if wire is None and outcome in ('Wire 1', 'Wire 2', 'Wire 3', 'Wire 4'):
                wire = int(outcome[-1])
            display_status = status
            if outcome not in ('—', 'Active', 'Final') and not outcome.startswith('Wire '):
                display_status = outcome if status == 'Final' else f'{status} · {outcome}'
            labels = ['', record['case'], str(wire) if wire is not None else '—', airframe, date]
            keys = [pilot.casefold(), record['case'], wire, airframe.casefold(),
                    started.timestamp() if started else None]
            tips = {}
            for component in COMPONENTS:
                value = record['components'].get(component)
                points = value['score'] if value else None
                pending = component == 'Wire' and record.get('wire_pending')
                labels.append('Pending' if pending else '—' if points is None else f'{points:,.1f}')
                keys.append(None if pending else points)
                if value:
                    tip = f"{value['score']:,.1f} / {value['maximum']:,.1f} points"
                    if component in ('Glide', 'Localizer') and record.get('final_reference'):
                        tip += f"\nFinal reference: {record['final_reference']}"
                    tips[len(labels)-1] = tip
            notes = record.get('notes') or {}
            labels.extend([f'{total:,.1f} / {maximum:,.0f}', display_status, 'Details' if notes else '—'])
            keys.extend([total, display_status.casefold(), bool(notes)])
            tips[len(labels) - 1] = self.notes_tooltip(notes)
            rows.append((keys, labels, tips, pilot, airframe, record))

        _, best_by_case = self.rankings(self.records)
        green = QColor(theme_color('slow'))
        yellow = QColor(theme_color('on'))
        rows = self.reduce_rows(rows)
        crowns, _ = self.rankings(row[-1] for row in rows)
        header = tree.header()
        column = header.sortIndicatorSection()
        descending = header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        rows.sort(key=lambda row: (row[3].casefold(), row[4].casefold()))
        known = [row for row in rows if row[0][column] is not None]
        known.sort(key=lambda row: row[0][column], reverse=descending)
        ordered = known + [row for row in rows if row[0][column] is None]
        by_pilot = {}
        for row in ordered:
            by_pilot.setdefault(row[3], []).append(row)
        if self.group_airframe.isChecked() and column != 3:
            for attempts in by_pilot.values():
                attempts.sort(key=lambda row: row[4].casefold())
        ordered = [row for attempts in by_pilot.values() for row in attempts]
        self.visible_records = [row[-1] for row in ordered]
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            groups = {}
            for _, labels, tips, pilot, _, record in ordered:
                if pilot not in groups:
                    group = QTreeWidgetItem(tree, [pilot])
                    group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    if pilot in crowns:
                        group.setIcon(0, crowns[pilot])
                    groups[pilot] = group
                item = QTreeWidgetItem(groups[pilot], labels)
                if record['provisional']:
                    for index in range(len(labels)):
                        item.setForeground(index, yellow)
                elif record['maximum'] and record['score'] == best_by_case.get((pilot, record['case'])):
                    item.setForeground(self.total_column, green)
                identifier = record['attempt_id']
                item.setData(0, role, identifier)
                tooltip = f"Attempt ID: {identifier}\n{record.get('replay_name', '')}"
                if identifier in self.duplicate_ids:
                    tooltip += '\nPossible duplicate · another saved attempt is within 5 seconds UTC'
                item.setToolTip(0, tooltip)
                for index, tooltip in tips.items():
                    item.setToolTip(index, tooltip)
                if identifier == current_id:
                    tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                item.setSelected(identifier in selected)
            for pilot, group in groups.items():
                group.setExpanded(pilot not in self.collapsed_pilots)
            tree.verticalScrollBar().setValue(scroll)
        finally:
            tree.setUpdatesEnabled(True)
