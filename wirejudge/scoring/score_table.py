"""Shared score breakdown and filters for current and saved attempts."""
from datetime import datetime

from PySide6.QtCore import QDate, QItemSelectionModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QToolButton, QButtonGroup,
)

from wirejudge.aircraft.aircraft_menu import AircraftMenu
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
        if leaderboard:
            modes = QHBoxLayout()
            self.mode_group = QButtonGroup(self)
            self.mode_group.setExclusive(False)
            for mode, text in enumerate(('Overall: Show Best', 'Airframe: Show Best', 'Show Last')):
                toggle = QCheckBox(text)
                self.mode_group.addButton(toggle, mode)
                modes.addWidget(toggle)
            self.mode_group.idClicked.connect(self.toggle_mode)
            modes.addStretch()
            layout.addLayout(modes)

        self.tree = QTreeWidget()
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        headings = ['Username / Airframe', 'Date' if leaderboard else 'Date / time', 'Case', 'Result',
                    'Plat. Pos.', 'Plat. Spd.', *COMPONENTS[2:],
                    'Total', 'Status']
        self.tree.setHeaderLabels(headings)
        self.tree.headerItem().setToolTip(4, 'Platform Position')
        self.tree.headerItem().setToolTip(5, 'Platform Speed')
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

    def toggle_mode(self, mode):
        if self.mode_group.button(mode).isChecked():
            for button in self.mode_group.buttons():
                if button is not self.mode_group.button(mode):
                    button.setChecked(False)
        self.render()

    def reduce_rows(self, rows):
        if not self.leaderboard:
            return rows
        mode = next((self.mode_group.id(button) for button in self.mode_group.buttons()
                     if button.isChecked()), None)
        if mode is None:
            return rows
        best = {}
        for row in rows:
            keys, _, _, pilot, airframe, record = row
            started = keys[1] if keys[1] is not None else float('-inf')
            group = (pilot, airframe) if mode == 1 else (pilot,)
            if mode == 2:
                rank = (started, record['attempt_id'])
            else:
                if record['provisional'] or not record['maximum']:
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
        group_role = role + 1
        tree = self.tree
        selected = {item.data(0, role) for item in tree.selectedItems()}
        current = tree.currentItem()
        current_id = current.data(0, role) if current else None
        collapsed = set()
        for index in range(tree.topLevelItemCount()):
            player = tree.topLevelItem(index)
            if not player.isExpanded():
                collapsed.add(tuple(player.data(0, group_role)))
            for child in range(player.childCount()):
                airframe = player.child(child)
                if not airframe.isExpanded():
                    collapsed.add(tuple(airframe.data(0, group_role)))
        scroll = tree.verticalScrollBar().value()
        text = self.search.text().strip().casefold()
        case = 'All cases' if self.leaderboard else self.case.currentText()
        dated = self.date_filter.isChecked()
        start, end = self.start.date().toPython(), self.end.date().toPython()
        rows = []
        date_format = '%Y-%m-%d' if self.leaderboard else '%Y-%m-%d %H:%M:%S'
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
            labels = [record.get('attempt_label', outcome), date, record['case'], outcome]
            keys = [pilot.casefold(), started.timestamp() if started else None,
                    record['case'], outcome.casefold()]
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
            labels.extend([f'{total:,.1f} / {maximum:,.0f}', status])
            keys.extend([total, status])
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
        self.visible_records = [row[-1] for row in ordered]
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            groups = {}
            for _, labels, tips, pilot, airframe, record in ordered:
                for key, label in (((pilot,), pilot), ((pilot, airframe), airframe)):
                    if key not in groups:
                        group = QTreeWidgetItem(groups[key[:-1]] if len(key) == 2 else tree, [label])
                        group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                        group.setData(0, group_role, list(key))
                        group.setExpanded(key not in collapsed)
                        if len(key) == 1 and pilot in crowns:
                            group.setIcon(0, crowns[pilot])
                        groups[key] = group
                item = QTreeWidgetItem(groups[pilot, airframe], labels)
                if record['provisional']:
                    for index in range(len(labels)):
                        item.setForeground(index, yellow)
                elif record['maximum'] and record['score'] == best_by_case.get((pilot, record['case'])):
                    item.setForeground(len(labels) - 2, green)
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
            tree.verticalScrollBar().setValue(scroll)
        finally:
            tree.setUpdatesEnabled(True)
