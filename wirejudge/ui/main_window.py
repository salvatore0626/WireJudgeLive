from PySide6.QtCore import Qt, QTimer, QSize, QEvent, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
import json
import math
from pathlib import Path
import queue
import time
from wirejudge.telemetry.telemetry_capture import CaptureService
from wirejudge.replay.dvr_playback import PlaybackClock, DVRReader
from wirejudge.display_units import DisplayUnits, SPEED_FACTORS, ALTITUDE_FACTORS, DISTANCE_FACTORS
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton, QComboBox,
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QTabWidget, QSplitter,
    QTreeWidget, QTableWidget, QHeaderView, QSlider, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QFrame, QScrollArea, QTreeWidgetItem, QToolButton, QMenu, QDialog, QMessageBox, QWidgetAction, QFileDialog, QGraphicsOpacityEffect)
import pyqtgraph as pg
from wirejudge.map.map_view import MapView
from wirejudge.ui.settings_store import DEFAULTS, GAUGE_DISPLAY_DEFAULTS, Settings
from wirejudge.scoring.landing_scoring import DEFAULT_RULES
from wirejudge.approach.approach_view import ApproachView
from wirejudge.approach.attempt_editor import AttemptEditor
from wirejudge.aircraft.aircraft_list import AircraftList, NAMES
from wirejudge.replay.replay_manager import ReplayManager
from wirejudge.replay.recording_toggle import ReplayButton
from wirejudge.scoring.leaderboards import Leaderboards
from wirejudge.replay.event_timeline import EventTimeline,recovery_events
from wirejudge.approach.approach_geometry import NM
from wirejudge.telemetry.larp_radar import RadarContacts, SEARCH_PERIOD
from wirejudge.map.map_trails import TRAIL_SECONDS
from wirejudge.ui.control_icons import control_icon
from wirejudge.telemetry.control_fields import aircraft_throttle
from wirejudge.aircraft.aircraft_menu import AircraftMenu,AircraftMenuButton
from wirejudge.aircraft.aircraft_panel import AircraftPanel
from wirejudge.approach.approach_scopes import ApproachDisplays
from wirejudge.ui.stack_tool import StackTool
from wirejudge.ui.deck_crew_tool import DeckCrewTool
from wirejudge.ui.roster_tool import RosterTool, SPEED_WINDOW
from wirejudge.ui.conditions_tool import ConditionsTool, WINDOW_SECONDS
from wirejudge.scoring.scoring_tool import ScoringTool
from wirejudge.approach.lso_hints import LSOHints
from wirejudge.aircraft.instrument_panel import InstrumentPanel
from wirejudge.aircraft.aircraft_labels import LABEL_FIELDS,DEFAULT_LABEL_FIELDS
from wirejudge.map.procedure_colors import defaults as procedure_defaults, is_preset
from wirejudge.ui.theme_editor import ThemeEditor
from wirejudge.ui.settings_layout import SettingsColumns
from wirejudge.ui import theme


def label(text, name=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    return w


def button(text, slot=None, enabled=True, checkable=False):
    w = QPushButton(text)
    w.setEnabled(enabled)
    w.setCheckable(checkable)
    if slot:
        w.clicked.connect(slot)
    return w


def empty(title, detail):
    w = QWidget()
    box = QVBoxLayout(w)
    box.addStretch()
    a, b = label(title, 'emptyTitle'), label(detail, 'muted')
    a.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setWordWrap(True)
    box.addWidget(a)
    box.addWidget(b)
    box.addStretch()
    return w


def tree():
    w = QTreeWidget()
    w.setHeaderLabels(['Pilot / attempt', 'Outcome'])
    w.setMinimumWidth(225)
    return w


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        try:
            definitions = json.loads(self.settings.get('custom_themes'))
        except (TypeError, ValueError):
            definitions = {}
        theme.CUSTOM_THEMES.clear()
        if isinstance(definitions, dict):
            theme.CUSTOM_THEMES.update({name: definition for name, definition in definitions.items()
                                        if theme.valid_custom_theme(name, definition)})
        theme.set_theme(self.settings.get('theme'))
        try:
            overrides = json.loads(self.settings.get('theme_fonts'))
        except (ValueError, TypeError):
            overrides = {}
        self.theme_fonts = {name: font for name, font in overrides.items()
                            if isinstance(font, str) and font in theme.FONT_OPTIONS} if isinstance(overrides, dict) else {}
        legacy_font = self.settings.get('font')
        if not self.settings.store.contains('theme_fonts') and legacy_font in theme.FONT_OPTIONS and legacy_font != 'System Default':
            self.theme_fonts[theme.CURRENT_THEME] = legacy_font
            self.settings.save({'theme_fonts': json.dumps(self.theme_fonts)})
        theme.set_font(self.theme_fonts.get(theme.CURRENT_THEME, theme.default_font(theme.CURRENT_THEME)))
        self.units = DisplayUnits(self.settings.get('speed_unit'), self.settings.get('altitude_unit'),self.settings.get('distance_unit'))
        self.capture = CaptureService(self.settings.get('buffer_s'))
        self.capture.auto_record_folder=self.settings.get('recording_folder')
        self.playback = PlaybackClock()
        self.playback_reader = DVRReader()
        self.playback_reader.start()
        self.playback_epoch = 0
        self.larp_enabled = False
        self.larp_lite = False
        self.radar_contacts = RadarContacts()
        self.radar_review_key = None
        self.radar_reviewing = False
        self.raw_sample = None
        self.review_sample = None
        self.review_history = []
        self.blackbox_disk_history = []
        self.blackbox_history_request_key=None
        self.seek_pending = False
        self.play_after_seek=False
        self.pending_attempt_jump=None
        self.last_seek_request = 0.
        self.last_seek_key = None
        self.drag_bounds = None
        self.last_revision = -1
        self.last_render_key = None
        self.current_sample = None
        self.current_carrier = None
        self.current_aircraft = None
        self.last_details_update = 0.
        self.dvr_path = None
        self.external_replay=False
        self.dvr_error = None
        self.closing = False
        self.listening_requested = True
        self.receiver_endpoint = None
        self.setWindowTitle('Wire Judge Live')
        self.resize(1440, 900)
        self.setMinimumSize(1060, 700)
        self.setStyleSheet(theme.stylesheet())
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QFrame()
        top.setObjectName('topbar')
        row = QHBoxLayout(top)
        row.setContentsMargins(22, 16, 22, 16)
        brand = QHBoxLayout();brand.setSpacing(7)
        brand.addWidget(label('Wire Judge', 'brand'))
        brand.addWidget(label('Live','brandLive'))
        row.addLayout(brand)
        row.addStretch()
        self.replays_button = ReplayButton()
        self.replays_button.clicked.connect(lambda: self.replay_manager.show_browser())
        row.addWidget(self.replays_button)
        self.timeline_toggle=button('Timeline',checkable=True)
        self.timeline_toggle.setChecked(self.settings.store.value('ui/timeline_visible',False,type=bool))
        self.timeline_toggle.setToolTip('Show or hide the playback timeline')
        self.timeline_toggle.toggled.connect(self.set_timeline_visible)
        row.addWidget(self.timeline_toggle)
        row.addWidget(button('Settings', lambda: self.tabs.setCurrentIndex(5)))
        outer.addWidget(top)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.map_page(), 'Map')
        self.tabs.addTab(self.approach_page(), 'Approach')
        self.tabs.addTab(self.editor_page(), 'Attempt Editor')
        self.tabs.addTab(self.score_page(), 'Scoreboard')
        self.leaderboards=Leaderboards(self)
        self.tabs.addTab(self.leaderboards.widget,'Leaderboards')
        self.tabs.addTab(self.settings_page(), 'Settings')
        self.replay_manager=ReplayManager(self)
        row.insertWidget(row.indexOf(self.timeline_toggle), self.replay_manager.return_live)
        self.timeline = self.timeline_panel()
        self.tabs.widget(0).layout().insertWidget(0, self.timeline)
        self.timeline.setVisible(self.tabs.currentIndex() == 0 and self.timeline_toggle.isChecked())
        outer.addWidget(self.tabs, 1)
        for i in (1,2):self.tabs.setTabEnabled(i,False)
        self.statusBar().setStyleSheet('QStatusBar {background:#26282B;color:#FFFFFF;border-top:1px solid #383B40;} QStatusBar::item {border:0;} QSizeGrip {background:#26282B;} QLabel {background:transparent;color:#FFFFFF;}')
        self.telemetry_status = button('Telemetry 0.0 Hz',lambda:self.tabs.setCurrentIndex(5))
        self.telemetry_status.setStyleSheet('QPushButton {color:#FF626A;border:none;background:transparent;text-align:left;padding:4px 10px;}')
        self.statusBar().addPermanentWidget(self.telemetry_status,1)
        self.last_receiving = False
        self.notify('Starting automatic receiver • waiting for mod or replay sender')
        self.render_label = label('App UI 0 FPS · Map 0 FPS', 'muted')
        self.render_label.setToolTip('Measured window updates and map draws. While running: UI targets 30 FPS, map targets 45 FPS. Paused playback redraws on demand.')
        self.statusBar().addPermanentWidget(self.render_label)
        self.ui_frames = 0
        self.fps_sample_time = time.perf_counter()
        self.fps_timer = QTimer(self)
        self.fps_timer.setInterval(1000)
        self.fps_timer.timeout.connect(self.update_render_stats)
        self.fps_timer.start()
        self.ui_redraw_timer = QTimer(self)
        self.ui_redraw_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.ui_redraw_timer.setInterval(33)  # ~30 FPS with integer-millisecond Qt timers.
        self.ui_redraw_timer.timeout.connect(self.refresh_visible_ui)
        self.ui_redraw_timer.start()
        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(100)
        self.capture_timer.timeout.connect(self.refresh_capture)
        self.capture_timer.start()
        self.previous_tab = 0
        self.tabs.currentChanged.connect(self.analysis_tab_changed)
        self.receive_timer = QTimer(self)
        self.receive_timer.setInterval(1000)
        self.receive_timer.timeout.connect(self.ensure_receiver)
        self.receive_timer.start()
        theme.refresh_widgets(self, dict(theme.PALETTE))
        if self.settings.store.value('ui/larp_lite', False, type=bool):
            self.larp_lite_action.setChecked(True)
        else:
            self.larp_action.setChecked(self.settings.store.value('ui/larp_mode', False, type=bool))
        QTimer.singleShot(0,self.ensure_receiver)

    def event(self, event):
        if event.type() == QEvent.Type.UpdateRequest and hasattr(self, 'ui_frames'):
            self.ui_frames += 1
        return super().event(event)

    def refresh_visible_ui(self):
        if self.playback.state != 'paused' and self.isVisible() and not self.isMinimized():
            if self.larp_enabled:
                self.update_radar_sweep()
            self.update()

    def update_render_stats(self):
        now = time.perf_counter()
        elapsed = now - self.fps_sample_time
        text = f'App UI {self.ui_frames / elapsed:.0f} FPS · Map {self.map.frames / elapsed:.0f} FPS'
        self.ui_frames = self.map.frames = 0
        self.fps_sample_time = now
        if self.render_label.text() != text:
            self.render_label.setText(text)

    def notify(self, text):
        if hasattr(self,'telemetry_status'):
            self.telemetry_status.setToolTip(text+'\nClick to open Settings')

    def update_telemetry_status(self, stats):
        receiving=stats['status']=='Connected' and stats['age_s'] is not None and stats['age_s']<=2.
        text = f'Telemetry {stats["hz"] if receiving else 0.:.1f} Hz'
        if self.telemetry_status.text() != text:
            self.telemetry_status.setText(text)
        if receiving!=self.last_receiving:
            self.last_receiving=receiving
            color='#20EF55' if receiving else '#FF626A'
            self.telemetry_status.setStyleSheet(f'QPushButton {{color:{color};border:none;background:transparent;text-align:left;padding:4px 10px;}}')

    def analysis_tab_changed(self, index):
        if self.previous_tab == self.tabs.indexOf(self.settings_widget) and self.settings_dirty():
            if not self.confirm_settings_exit():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentWidget(self.settings_widget)
                self.tabs.blockSignals(False)
                return
        selected = None
        if self.previous_tab in (1,2) and index in (1,2):
            source = self.approach_view if self.previous_tab==1 else self.attempt_editor
            item = source.tree.currentItem()
            selected = item.data(0,Qt.ItemDataRole.UserRole) if item else None
        self.previous_tab = index
        self.refresh_capture(force=True)
        if selected:
            target = self.approach_view if index==1 else self.attempt_editor
            if selected in target.items:
                target.tree.setCurrentItem(target.items[selected])

    def map_page(self):
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(14, 12, 14, 10)
        self.home_button=button('',self.home_to_carrier,enabled=False)
        self.home_button.setIcon(control_icon('home'));self.home_button.setAccessibleName('Home')
        self.home_button.setToolTip('Home · Center on the selected carrier at the current zoom')
        self.dimension=button('3D',self.toggle_3d,checkable=True)
        self.case_selector=QComboBox(page);self.case_selector.addItems(['Case 1','Case 3'])
        self.case_selector.setCurrentText('Case 1');self.case_selector.hide()
        self.case_button=button('')
        self.case_button.setIcon(control_icon('case1'));self.case_button.setAccessibleName('Case 1')
        self.case_button.setToolTip('Switch Case 1 / Case 3 approach display')
        self.case_button.clicked.connect(lambda _:self.case_selector.setCurrentText('Case 1' if self.case_selector.currentText()=='Case 3' else 'Case 3'))
        self.case_selector.currentTextChanged.connect(self.apply_case_settings)
        self.carrier=QComboBox(page);self.carrier.addItem('No carriers available');self.carrier.setEnabled(False);self.carrier.hide()
        self.carrier.currentIndexChanged.connect(self.carrier_changed)
        self.trails=QCheckBox('Trails');self.trails.setChecked(self.settings.get('trails'))
        self.verticals=QCheckBox('Alt Line');self.verticals.setChecked(self.settings.get('verticals'))
        self.vectors=QCheckBox('Vectors');self.vectors.setChecked(self.settings.get('vectors'))
        for control in (self.trails,self.verticals,self.vectors):control.toggled.connect(lambda _:self.refresh_capture(force=True))
        self.labels_menu=AircraftMenu('Aircraft Settings',self);self.label_actions={}
        self.show_labels_action=self.labels_menu.addAction('Labels');self.show_labels_action.setCheckable(True)
        self.show_labels_action.setChecked(self.settings.store.value('ui/aircraft_labels_visible',True,type=bool))
        self.show_labels_action.toggled.connect(self.apply_aircraft_label_settings)
        try:label_fields=json.loads(self.settings.get('aircraft_label_fields'))
        except (TypeError,ValueError):label_fields=list(DEFAULT_LABEL_FIELDS)
        if not isinstance(label_fields,list):label_fields=list(DEFAULT_LABEL_FIELDS)
        for key,title in LABEL_FIELDS.items():
            action=self.labels_menu.addAction(title);action.setCheckable(True);action.setChecked(key in label_fields)
            action.setEnabled(self.show_labels_action.isChecked())
            action.toggled.connect(self.apply_aircraft_label_settings);self.label_actions[key]=action
        aircraft_menu=self.labels_menu;aircraft_menu.addSeparator();self.aircraft_display_actions={}
        for title,control in (('Trails',self.trails),('Alt Line',self.verticals),('Vectors',self.vectors)):
            control.setParent(page);control.hide()
            action=aircraft_menu.addAction(title);action.setCheckable(True);action.setChecked(control.isChecked())
            action.toggled.connect(control.setChecked);control.toggled.connect(action.setChecked)
            self.aircraft_display_actions[title]=action
        self.labels_button=AircraftMenuButton();self.labels_button.setIcon(control_icon('plane'));self.labels_button.setMenu(aircraft_menu)
        self.labels_button.setAccessibleName('Aircraft Settings');self.labels_button.setToolTip('Aircraft Settings · Labels, Trails, Alt Line, Vectors')
        self.labels_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.glide_button=button('Glide Slope',checkable=True);self.glide_button.setChecked(False)
        self.landing_button=button('Landing Scope',checkable=True);self.landing_button.setChecked(False)
        self.scoring_button=button('Live Scoring',checkable=True)
        self.scoring_button.setChecked(False)
        self.stack_button=button('Stack',checkable=True);self.stack_button.setChecked(False)
        self.roster_button = button('Roster', checkable=True)
        self.conditions_button = button('Conditions', checkable=True)
        self.deck_crew_button = button('Deck Crew Radio', checkable=True)
        self.mission_clock_button = button('Mission Clock', checkable=True)
        self.mission_clock_button.setChecked(self.settings.store.value('ui/mission_clock', False, type=bool))
        self.scopes_menu=QMenu(self)
        self.map_workspace=QMainWindow()
        self.map_workspace.setDockNestingEnabled(False)
        self.aircraft_tree = AircraftList()
        self.aircraft_tree.itemSelectionChanged.connect(self.aircraft_selected)
        self.aircraft_tree.itemDoubleClicked.connect(self.aircraft_list_double_clicked)
        center = QWidget()
        c = QVBoxLayout(center)
        c.setContentsMargins(0, 0, 0, 0)
        self.map = MapView()
        for key, callback in (('Shift+F', self.map.holidays.launch),
                              ('Shift+C', lambda: self.map.holidays.toggle_decoration('christmas')),
                              ('Shift+H', lambda: self.map.holidays.toggle_decoration('halloween'))):
            shortcut = QShortcut(QKeySequence(key), page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        self.procedure_menu = AircraftMenu('Map Settings', self)
        self.lock = self.procedure_menu.addAction(control_icon('locked'), 'Carrier Lock')
        self.lock.setCheckable(True)
        self.lock.setChecked(True)
        self.lock.setEnabled(False)
        self.lock.setToolTip('Carrier Lock · locked')
        self.lock.toggled.connect(lambda _: self.refresh_capture(force=True))
        self.procedure_menu.addSeparator()
        self.procedure_button = AircraftMenuButton()
        self.procedure_button.setIcon(control_icon('gear'))
        self.procedure_button.setAccessibleName('Map Settings')
        self.procedure_button.setToolTip('Map Settings · Carrier Lock and procedure overlays')
        self.procedure_button.setMenu(self.procedure_menu)
        self.procedure_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for key, title in (('marshal', 'Marshal'), ('approach', 'Approach'), ('missed', 'Missed Approach'),
                           ('glide', 'Glide / Localizer'), ('gates', 'Grading Gates')):
            action = self.procedure_menu.addAction(title)
            action.setCheckable(True)
            visible = self.settings.store.value(f'ui/procedure_{key}', True, type=bool)
            self.map.procedure_visibility[key] = visible
            action.setChecked(visible)
            action.toggled.connect(lambda checked, category=key: self.set_procedure_visible(category, checked))
        self.procedure_labels_action = self.procedure_menu.addAction('Procedure Labels')
        self.procedure_labels_action.setCheckable(True)
        self.procedure_labels_action.setChecked(self.settings.store.value('ui/procedure_labels', True, type=bool))
        self.procedure_labels_action.toggled.connect(self.set_procedure_labels_visible)
        self.procedure_menu.addSeparator()
        opacity_action = QWidgetAction(self.procedure_menu)
        opacity_control = QWidget(self.procedure_menu)
        opacity_layout = QVBoxLayout(opacity_control)
        opacity_layout.setContentsMargins(16, 5, 16, 10)
        opacity_label = QLabel()
        self.procedure_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.procedure_opacity_slider.setAccessibleName('Procedure opacity')
        self.procedure_opacity_slider.setRange(0, 100)
        self.procedure_opacity_slider.setMinimumWidth(160)
        self.procedure_opacity_slider.setValue(self.settings.store.value('ui/procedure_opacity', 100, type=int))

        def update_procedure_opacity(value):
            opacity_label.setText(f'Procedure Opacity · {value}%')
            self.map.set_procedure_opacity(value / 100)
            self.settings.store.setValue('ui/procedure_opacity', value)

        self.procedure_opacity_slider.valueChanged.connect(update_procedure_opacity)
        update_procedure_opacity(self.procedure_opacity_slider.value())
        opacity_layout.addWidget(opacity_label)
        opacity_layout.addWidget(self.procedure_opacity_slider)
        opacity_action.setDefaultWidget(opacity_control)
        self.procedure_menu.addAction(opacity_action)
        self.procedure_menu.addSeparator()
        self.larp_action = self.procedure_menu.addAction('LARP Mode')
        self.larp_action.setCheckable(True)
        self.larp_action.toggled.connect(lambda checked: self.set_larp_mode(checked, False))
        self.larp_lite_action = self.procedure_menu.addAction('LARP Lite')
        self.larp_lite_action.setCheckable(True)
        self.larp_lite_action.toggled.connect(lambda checked: self.set_larp_mode(checked, True))
        for title, setting in (('Show Search Radar', 'radar_search_visible'),
                               ('Show PAR', 'radar_par_visible'),
                               ('PAR Sweep', 'radar_par_sweep_visible')):
            action = self.procedure_menu.addAction(title)
            action.setCheckable(True)
            visible = self.settings.store.value(f'ui/{setting}', True, type=bool)
            action.setChecked(visible)
            setattr(self.map, setting, visible)
            action.toggled.connect(lambda value, key=setting: self.set_radar_display(key, value))
        radar_action = QWidgetAction(self.procedure_menu)
        radar_control = QWidget(self.procedure_menu)
        radar_layout = QVBoxLayout(radar_control)
        radar_layout.setContentsMargins(16, 5, 16, 10)
        radar_label = QLabel()
        radar_slider = QSlider(Qt.Orientation.Horizontal)
        radar_slider.setAccessibleName('Radar opacity')
        radar_slider.setRange(0, 100)
        radar_slider.setValue(self.settings.store.value('ui/radar_opacity', 100, type=int))

        def update_radar_opacity(value):
            radar_label.setText(f'Radar Opacity · {value}%')
            self.set_radar_display('radar_opacity', value)

        radar_slider.valueChanged.connect(update_radar_opacity)
        update_radar_opacity(radar_slider.value())
        radar_layout.addWidget(radar_label)
        radar_layout.addWidget(radar_slider)
        radar_action.setDefaultWidget(radar_control)
        self.procedure_menu.addAction(radar_action)
        self.map_controls=QFrame(self.map);self.map_controls.setObjectName('mapControls')
        controls=QVBoxLayout(self.map_controls);controls.setContentsMargins(5,5,5,5);controls.setSpacing(4)
        self.map_controls_toggle=button('',self.set_map_controls_expanded,checkable=True)
        self.map_controls_toggle.setIcon(control_icon('toolbox'));self.map_controls_toggle.setAccessibleName('Tools')
        self.map_controls_toggle.setToolTip('Show tools')
        self.vector_button = button('', checkable=True)
        self.vector_button.setIcon(control_icon('vector'))
        self.vector_button.setAccessibleName('Draw marshal vector')
        self.vector_button.setToolTip('Draw vector · left-drag to draw, right-drag to pan · right-click here to clear')
        self.vector_button.toggled.connect(self.map.set_vector_mode)
        self.map.vector_mode_changed.connect(self.vector_button.setChecked)
        self.vector_button.installEventFilter(self)
        self.lso_hint_button=button('Callouts',checkable=True)
        self.lso_hint_button.setChecked(self.settings.store.value('ui/lso_hints',True,type=bool))
        self.lso_hint_button.setToolTip('Suggested LSO words for the selected aircraft on final, or the nearest qualifying inbound aircraft')
        self.lso_hint_button.setAccessibleName('Callouts')
        top_controls=QHBoxLayout();top_controls.setContentsMargins(0,0,0,0);top_controls.setSpacing(9)
        for control in (self.home_button,self.dimension,self.case_button,self.procedure_button,self.labels_button,self.map_controls_toggle,self.vector_button):
            control.setFixedSize(38,38);control.setIconSize(QSize(22,22))
            control.setStyleSheet('QPushButton, QToolButton {padding:5px;} QToolButton::menu-indicator {image:none;width:0px;}')
            top_controls.addWidget(control)
        top_controls.addStretch();controls.addLayout(top_controls)
        self.map_control_details=QWidget(self.map_controls)
        detail_controls=QVBoxLayout(self.map_control_details);detail_controls.setContentsMargins(0,0,0,0);detail_controls.setSpacing(4)
        self.tool_icons = {
            self.glide_button: 'glide_slope', self.landing_button: 'landing_scope',
            self.stack_button: 'stack', self.scoring_button: 'live_score',
            self.roster_button: 'roster', self.mission_clock_button: 'clock',
            self.conditions_button: 'windsock', self.deck_crew_button: 'radio',
        }
        for control in self.tool_icons:
            control.setMinimumHeight(34)
            control.setIconSize(QSize(28,22))
            control.setStyleSheet('text-align: left;')
            detail_controls.addWidget(control)
        controls.addWidget(self.map_control_details)
        self.map_controls.move(8,8);self.map_controls.show();self.set_map_controls_expanded(False)
        self.mission_clock = label('--:--:--', 'missionClock')
        self.mission_clock.setParent(self.map)
        self.mission_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mission_clock.setAccessibleName('Mission time')
        self.mission_clock.setToolTip('Elapsed mission time reported by the game')
        self.mission_clock.setVisible(self.mission_clock_button.isChecked())
        self.mission_clock_button.toggled.connect(self.set_mission_clock_visible)
        self.map.installEventFilter(self)
        self.position_mission_clock()
        self.map.label_fields=[key for key,action in self.label_actions.items() if action.isChecked()] if self.show_labels_action.isChecked() else []
        self.map.label_units=self.units
        self.map.aircraft_clicked.connect(self.aircraft_map_clicked)
        self.map.aircraft_double_clicked.connect(self.focus_aircraft)
        c.addWidget(self.map, 1)
        self.approach_displays=ApproachDisplays(self,self.scopes_menu)
        self.stack_tool=StackTool(self)
        self.roster_tool = RosterTool(self)
        self.roster_button.toggled.connect(self.roster_tool.pane.set_enabled)
        self.deck_crew_tool = DeckCrewTool(self)
        self.deck_crew_button.toggled.connect(self.deck_crew_tool.pane.set_enabled)
        self.conditions_tool = ConditionsTool(self)
        self.conditions_button.toggled.connect(self.set_conditions_visible)
        self.scoring_tool=ScoringTool(self)
        self.scoring_button.toggled.connect(self.scoring_tool.pane.set_enabled)
        landing_pane=next(pane for pane in self.approach_displays.panes if pane.kind=='scope')
        self.lso_hint_button.setStyleSheet('QPushButton {padding:3px 6px;}')
        landing_pane.header.layout().insertWidget(1,self.lso_hint_button)
        self.lso_hints=LSOHints(self,self.lso_hint_button,landing_pane)
        self.stack_button.toggled.connect(self.stack_tool.pane.set_enabled)
        for key,control in (('slice',self.glide_button),('scope',self.landing_button)):
            action=self.approach_displays.actions[key]
            control.toggled.connect(action.setChecked);action.toggled.connect(control.setChecked)
        self.map_status = label('Disconnected — waiting for telemetry', 'muted')
        self.map_status.setParent(page)
        self.map_status.hide()
        self.map_workspace.setCentralWidget(center)
        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 0, 0)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_widget = QWidget()
        detail_col = QVBoxLayout(detail_widget)
        self.flight_panel=InstrumentPanel(self.settings.store)
        self.flight_panel.black_box.history_window_changed.connect(self.blackbox_window_changed)
        detail_col.addWidget(self.flight_panel)
        self.raw_toggle=button('Raw telemetry',checkable=True)
        detail_col.addWidget(self.raw_toggle)
        self.aircraft_detail = label('Select an aircraft to view captured values.', 'muted')
        self.aircraft_detail.setWordWrap(True)
        self.aircraft_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.aircraft_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.aircraft_detail.hide()
        self.raw_toggle.toggled.connect(self.aircraft_detail.setVisible)
        detail_col.addWidget(self.aircraft_detail)
        self.carrier_detail_header=label('CARRIER / WIRES', 'eyebrow');self.carrier_detail_header.hide()
        self.raw_toggle.toggled.connect(self.carrier_detail_header.setVisible)
        detail_col.addWidget(self.carrier_detail_header)
        self.carrier_detail = label('No carrier telemetry received.', 'muted')
        self.carrier_detail.setWordWrap(True)
        self.carrier_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.carrier_detail.hide()
        self.raw_toggle.toggled.connect(self.carrier_detail.setVisible)
        self.raw_toggle.toggled.connect(lambda checked:self.update_details() if checked else None)
        detail_col.addWidget(self.carrier_detail)
        detail_col.addStretch()
        detail_scroll.setWidget(detail_widget)
        r.addWidget(detail_scroll)
        self.aircraft_panel=AircraftPanel(self.map_workspace,self,self.aircraft_tree,right)
        col.addWidget(self.map_workspace, 1)
        self.camera_info = label('', 'muted')
        self.camera_info.setParent(page)
        self.map.camera_changed.connect(self.camera_info.setText)
        self.map.apply_camera()
        self.camera_info.hide()
        return page

    def set_procedure_visible(self, category, visible):
        self.map.procedure_visibility[category] = visible
        self.settings.store.setValue(f'ui/procedure_{category}', visible)
        self.refresh_capture(force=True)

    def set_radar_display(self, key, value):
        setattr(self.map, key, value / 100 if key == 'radar_opacity' else value)
        self.settings.store.setValue(f'ui/{key}', value)
        self.update_radar_sweep()

    @property
    def radar_tools(self):
        return self.larp_enabled and not self.larp_lite

    def set_larp_mode(self, enabled, lite=False):
        was_enabled = self.larp_enabled
        self.larp_enabled = enabled
        self.larp_lite = enabled and lite
        for action, checked in ((self.larp_action, enabled and not lite),
                                (self.larp_lite_action, self.larp_lite)):
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        self.settings.store.setValue('ui/larp_mode', enabled and not lite)
        self.settings.store.setValue('ui/larp_lite', self.larp_lite)
        if was_enabled != enabled:
            self.radar_contacts.reset()
            self.radar_review_key = None
        self.last_revision = None
        self.current_sample = None
        self.approach_displays.key = None
        self.lso_hints.set_frame(None, {}, self.map.approach_case, None)
        trails_action = self.aircraft_display_actions['Trails']
        trails_action.blockSignals(True)
        trails_action.setChecked(self.trails.isChecked() and not enabled)
        trails_action.setEnabled(not enabled)
        trails_action.blockSignals(False)
        self.flight_panel.set_radar_mode(self.radar_tools)
        self.raw_toggle.setVisible(not self.radar_tools)
        self.aircraft_detail.setVisible(not self.radar_tools and self.raw_toggle.isChecked())
        self.carrier_detail.setVisible(not self.radar_tools and self.raw_toggle.isChecked())
        self.carrier_detail_header.setVisible(not self.radar_tools and self.raw_toggle.isChecked())
        self.map.show_radar_sweep(None)
        self.last_seek_key = None
        self.refresh_capture(force=True)

    def radar_frame(self, sample, reviewing):
        self.radar_reviewing = reviewing
        if sample is None or (reviewing and self.seek_pending):
            self.radar_review_key = None
            return self.radar_contacts.update(None, None, 0., 0., self.playback_epoch)
        args = (self.current_carrier, self.map.case1_glide_deg,
                self.map.case1_runway_offset_deg, self.playback_epoch, self.playback.start)
        key = (self.playback_epoch, sample['capture']['connection_id'], args[:4])
        if reviewing and self.radar_review_key != key:
            self.radar_contacts.reset()
            stamp = sample['capture']['monotonic_s']
            for frame in self.review_history:
                capture = frame['capture']
                if (capture['connection_id'] == key[1] and
                        stamp - (TRAIL_SECONDS + 2 * SEARCH_PERIOD) <= capture['monotonic_s'] < stamp):
                    self.radar_contacts.update(frame, *args)
            self.radar_review_key = key
        return self.radar_contacts.update(sample, *args, display_time=None if reviewing else time.monotonic())

    def update_radar_sweep(self):
        radar = self.radar_contacts
        if not self.larp_enabled or not radar.coverage or not self.map.isVisible():
            return
        now = time.monotonic()
        if self.radar_reviewing:
            stamp = self.playback.cursor if self.playback.cursor is not None else radar.stamp
            if self.playback.state == 'playing':
                stamp = min(self.playback.end, stamp + max(0., now - self.playback.last_tick))
        else:
            stamp = max(radar.stamp, now)
        self.map.show_radar_sweep(radar.coverage, max(0., stamp - radar.origin_time))

    def set_procedure_labels_visible(self, visible):
        self.map.procedure_labels_visible = visible
        for control in (self.procedure_labels_action, self.procedure_labels_setting):
            previous = control.blockSignals(True)
            control.setChecked(visible)
            control.blockSignals(previous)
        self.settings.store.setValue('ui/procedure_labels', visible)
        self.update_settings_buttons()
        self.refresh_capture(force=True)

    def apply_aircraft_label_settings(self,*_):
        for action in self.label_actions.values():action.setEnabled(self.show_labels_action.isChecked())
        self.map.label_fields=[key for key,action in self.label_actions.items() if action.isChecked()] if self.show_labels_action.isChecked() else []
        self.settings.store.setValue('aircraft_label_fields',json.dumps([key for key,action in self.label_actions.items() if action.isChecked()]))
        self.settings.store.setValue('ui/aircraft_labels_visible',self.show_labels_action.isChecked())
        self.settings.store.sync()
        if hasattr(self,'timeline'):self.refresh_capture(force=True)

    def home_to_carrier(self):
        self.map.home_to_carrier(self.current_carrier)

    def set_timeline_visible(self,visible):
        if hasattr(self,'timeline'):self.timeline.setVisible(self.tabs.currentIndex() == 0 and visible)
        self.settings.store.setValue('ui/timeline_visible',visible);self.settings.store.sync()

    def eventFilter(self, watched, event):
        if (watched is getattr(self, 'map', None) and hasattr(self, 'mission_clock') and
                event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.StyleChange)):
            self.position_mission_clock()
        if (watched is getattr(self, 'vector_button', None) and
                event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton):
            self.map.clear_marshal_vectors()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if watched is not self.map_controls and not self.map_controls.isAncestorOf(watched):
                self.set_map_controls_expanded(False)
        return super().eventFilter(watched, event)

    def set_map_controls_expanded(self,expanded):
        if expanded:
            QApplication.instance().installEventFilter(self)
        else:
            QApplication.instance().removeEventFilter(self)
        self.map_controls_toggle.setChecked(expanded)
        self.map_control_details.setVisible(expanded)
        self.map_controls.layout().invalidate();self.map_controls.layout().activate();self.map_controls.adjustSize()
        self.map_controls_toggle.setToolTip('Hide tools' if expanded else 'Show tools')
        self.map_controls.raise_()
        if hasattr(self, 'mission_clock'):
            self.position_mission_clock()

    def set_mission_clock_visible(self, visible):
        self.settings.store.setValue('ui/mission_clock', visible)
        self.mission_clock.setVisible(visible)
        self.position_mission_clock()

    def set_conditions_visible(self, visible):
        self.conditions_tool.pane.set_enabled(visible)
        if visible and not self.radar_tools:
            self.request_seek()

    def position_mission_clock(self):
        clock = self.mission_clock
        clock.adjustSize()
        x = max(0, (self.map.width() - clock.width()) // 2)
        toolbar_right = self.map_controls.geometry().right()
        y = 8 if x > toolbar_right + 8 else self.map_controls.geometry().bottom() + 8
        clock.move(x, y)
        self.map_controls.raise_()

    def update_mission_clock(self, sample):
        value = ((sample or {}).get('source') or {}).get('mission_time_s')
        text = '--:--:--'
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            hours, remainder = divmod(int(value), 3600)
            minutes, seconds = divmod(remainder, 60)
            text = f'{hours:02d}:{minutes:02d}:{seconds:02d}'
        if self.mission_clock.text() != text:
            self.mission_clock.setText(text)
            self.position_mission_clock()

    def refresh_control_icons(self):
        for button, icon in self.tool_icons.items():
            button.setIcon(control_icon(icon, trailing_space=7))
        if hasattr(self,'home_button'):self.home_button.setIcon(control_icon('home'))
        if hasattr(self,'map_controls_toggle'):self.map_controls_toggle.setIcon(control_icon('toolbox'))
        if hasattr(self,'vector_button'):self.vector_button.setIcon(control_icon('vector'))
        if hasattr(self,'labels_button'):self.labels_button.setIcon(control_icon('plane'));self.labels_menu.apply_theme()
        if hasattr(self, 'procedure_button'):
            self.procedure_button.setIcon(control_icon('gear'))
            self.procedure_menu.apply_theme()
        if hasattr(self,'case_button'):
            case=self.case_selector.currentText();self.case_button.setIcon(control_icon('case3' if case=='Case 3' else 'case1'))
            self.case_button.setAccessibleName(case);self.case_button.setToolTip(case+' · click to switch to '+('Case 1' if case=='Case 3' else 'Case 3'))
        if hasattr(self,'lock'):
            self.lock.setIcon(control_icon('locked' if self.lock.isChecked() else 'unlocked'))
            self.lock.setToolTip('Carrier Lock · '+('locked' if self.lock.isChecked() else 'unlocked'))
        if hasattr(self,'play_button'):
            paused=self.playback.state=='paused'
            self.play_button.setIcon(control_icon('play' if paused else 'pause'))
            self.play_button.setToolTip('Play' if paused else 'Pause')
            self.play_button.setAccessibleName('Play' if paused else 'Pause')

    def toggle_3d(self, enabled):
        self.map.set_3d(enabled)
        self.refresh_capture(force=True)

    def approach_page(self):
        self.approach_view = ApproachView(self.capture, self.map.flight_colors)
        self.approach_view.attempt_activated.connect(self.open_attempt_on_map)
        self.approach_tree = self.approach_view.tree
        self.plots = self.approach_view.plots
        return self.approach_view

    def editor_page(self):
        self.attempt_editor = AttemptEditor(self.capture,self.map.flight_colors)
        self.attempt_editor.attempt_activated.connect(self.open_attempt_on_map)
        self.editor_tree = self.attempt_editor.tree
        return self.attempt_editor

    def score_page(self):
        page=self.scoring_tool.board()
        self.score_table=self.scoring_tool.board_tree
        return page

    def settings_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(30, 20, 30, 20)
        col.addWidget(label('APPLICATION SETTINGS', 'eyebrow'))
        cards=[]
        scoring_box=QGroupBox('Landing scoring')
        scoring_form=QFormLayout(scoring_box)
        score_mode=QComboBox()
        score_mode.addItems(['Additive','Deductive'])
        score_mode.setCurrentText(self.scoring_tool.mode)
        score_mode.currentTextChanged.connect(self.scoring_tool.set_mode)
        scoring_form.addRow('Score display',score_mode)
        self.scoring_settings_button = button('Edit scoring rules', self.scoring_tool.settings_dialog)
        scoring_form.addRow(self.scoring_settings_button)
        cards.append(scoring_box)
        try:overrides=json.loads(self.settings.get('flight_palette_overrides'))
        except (ValueError,TypeError):overrides={}
        if not isinstance(overrides,dict):overrides={}
        # Carry earlier named-flight customizations into the six shared slots.
        if not self.settings.store.contains('flight_palette_overrides'):
            try:legacy=json.loads(self.settings.get('flight_color_overrides'))
            except (ValueError,TypeError):legacy={}
            if isinstance(legacy,dict):
                for index,(name,value) in enumerate(legacy.items()):
                    prefix=name.removesuffix(' Flight') if isinstance(name,str) else ''
                    slot=str((NAMES.index(prefix) if prefix in NAMES else index)%6)
                    if isinstance(value,str) and QColor(value).isValid():overrides.setdefault(slot,QColor(value).name())
            self.settings.save({'flight_palette_overrides':json.dumps(overrides)})
        self.legacy_flight_colors = {name:QColor(value).name() for name,value in overrides.items()
            if name in tuple(str(i) for i in range(6)) and isinstance(value,str) and QColor(value).isValid()}
        self.apply_theme_colors()
        self.fields = {}
        appearance = QGroupBox('Appearance')
        appearance_form = QFormLayout(appearance)
        self.theme_picker = QComboBox()
        self.theme_picker.addItems(sorted(theme.theme_names(), key=str.casefold))
        self.theme_picker.setCurrentText(theme.CURRENT_THEME)
        self.theme_picker.setMinimumWidth(286)
        self.theme_picker.setToolTip('Applies immediately and is remembered between launches.')
        self.fields['theme'] = self.theme_picker
        appearance_form.addRow('Color theme', self.theme_picker)
        theme_actions = QHBoxLayout()
        add_theme = button('Add…', lambda: self.edit_custom_theme())
        theme_actions.addWidget(add_theme)
        self.edit_theme_button = button('Edit…', lambda: self.edit_custom_theme(theme.CURRENT_THEME))
        self.remove_theme_button = button('Remove…', self.remove_custom_theme)
        theme_actions.addWidget(self.edit_theme_button)
        theme_actions.addWidget(self.remove_theme_button)
        for action in (add_theme, self.edit_theme_button, self.remove_theme_button):
            action.setStyleSheet('padding: 3px 5px;')
        theme_actions.addStretch()
        appearance_form.addRow(theme_actions)
        self.update_theme_actions()
        self.font_picker = QComboBox()
        self.font_picker.addItems(['Theme default', *theme.FONT_OPTIONS])
        self.font_picker.setCurrentText(self.theme_fonts.get(theme.CURRENT_THEME, 'Theme default'))
        self.font_picker.setToolTip('Override the font for the selected theme, or use its assigned default.')
        self.font_picker.currentTextChanged.connect(self.change_font)
        appearance_form.addRow('Font', self.font_picker)
        self.flight_palette_preview=label('')
        self.flight_palette_preview.setMinimumWidth(230)
        self.flight_palette_preview.setToolTip('Each flight shares one color across all views.')
        appearance_form.addRow('Flight colors',self.flight_palette_preview)
        self.update_theme_preview()
        cards.append(appearance)
        self.theme_picker.currentTextChanged.connect(self.change_theme)
        connection = QGroupBox('Connection')
        form = QFormLayout(connection)
        host = QLineEdit(self.settings.get('host'))
        self.fields['host'] = host
        form.addRow('Listen address', host)
        port = QSpinBox()
        port.setRange(0, 65535)
        port.setSpecialValueText('Set mod port')
        port.setValue(self.settings.get('port'))
        self.fields['port'] = port
        form.addRow('TCP port', port)
        cards.append(connection)
        folder = QLineEdit(self.settings.get('recording_folder'))
        self.fields['recording_folder'] = folder
        folder.setToolTip(folder.text())
        folder.textChanged.connect(folder.setToolTip)
        unit_box = QGroupBox('Units')
        unit_form = QFormLayout(unit_box)
        speed_unit = QComboBox()
        speed_unit.addItems(list(SPEED_FACTORS))
        speed_unit.setCurrentText(self.units.speed_name)
        self.fields['speed_unit'] = speed_unit
        altitude_unit = QComboBox()
        altitude_unit.addItems(list(ALTITUDE_FACTORS))
        altitude_unit.setCurrentText(self.units.altitude_name)
        self.fields['altitude_unit'] = altitude_unit
        distance_unit=QComboBox();distance_unit.addItems(list(DISTANCE_FACTORS));distance_unit.setCurrentText(self.units.distance_name)
        self.fields['distance_unit']=distance_unit
        unit_form.addRow('Airspeed', speed_unit)
        unit_form.addRow('Altitude', altitude_unit)
        unit_form.addRow('Distance',distance_unit)
        speed_unit.setToolTip('1 banana = 0.2 metres. 1 banana tree = 7.5 metres.')
        speed_unit.currentTextChanged.connect(self.apply_unit_settings)
        altitude_unit.currentTextChanged.connect(self.apply_unit_settings)
        distance_unit.currentTextChanged.connect(self.apply_unit_settings)
        self.apply_unit_settings()
        cards.append(unit_box)
        gauge_box = QGroupBox('Gauge displays')
        gauge_form = QFormLayout(gauge_box)
        for gauge, title in (('altitude', 'Altimeter'), ('ias', 'IAS'),
                             ('vertical_speed', 'Vertical speed'), ('heading_track', 'Heading / Track')):
            key = f'{gauge}_display'
            mode = QComboBox()
            mode.addItems(['Analog', 'Digital'])
            mode.setCurrentText(self.settings.get(key))
            self.fields[key] = mode
            card = self.flight_panel.cards[gauge]
            card.set_display_mode(mode.currentText())
            mode.currentTextChanged.connect(card.set_display_mode)
            gauge_form.addRow(title, mode)
        cards.append(gauge_box)
        history = QGroupBox('Recording && history')
        form = QFormLayout(history)
        folder_row=QWidget();folder_line=QVBoxLayout(folder_row);folder_line.setContentsMargins(0,0,0,0)
        folder.setMinimumWidth(600)
        folder_line.addWidget(folder)
        def browse_folder():
            path=QFileDialog.getExistingDirectory(self,'Recording folder',folder.text())
            if path:folder.setText(path)
        folder_line.addWidget(button('Browse…',browse_folder), 0, Qt.AlignmentFlag.AlignLeft)
        form.addRow('DVR folder',folder_row)
        for key, text, minimum, maximum, suffix in [
            ('buffer_s', 'Live RAM buffer', 5, 600, ' seconds'),
            ('far_hz', '10 NM and farther', 1, 10, ' Hz'),
            ('mid_hz', '3–10 NM', 1, 10, ' Hz'),
            ('near_hz', 'Inside 3 NM', 1, 10, ' Hz'),
            ('region_hz', 'Glideslope / localizer / platform regions', 1, 10, ' Hz')]:
            spin = QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSuffix(suffix)
            spin.setValue(self.settings.get(key))
            self.fields[key] = spin
            if key=='buffer_s':form.addRow(text,spin)
            else:spin.setParent(history);spin.hide()
        for key in ('far_hz', 'mid_hz', 'near_hz', 'region_hz'):
            self.fields[key].setEnabled(False)
        display = QGroupBox('Aircraft display')
        aircraft_form = QFormLayout(display)
        self.carrier.setMinimumWidth(330)
        aircraft_form.addRow('Selected carrier',self.carrier);self.carrier.show()
        guides=QGroupBox('Map guides');form=QFormLayout(guides)
        grid = QCheckBox('Show map guides')
        grid.setChecked(self.settings.get('grid'))
        self.fields['grid'] = grid
        style = QComboBox()
        style.addItems(['Rings', 'Grid'])
        style.setCurrentText(self.settings.get('guide_style'))
        self.fields['guide_style'] = style
        spacing = QDoubleSpinBox()
        spacing.setRange(self.units.distance_nm(.1),self.units.distance_nm(100))
        spacing.setDecimals(2)
        spacing.setSingleStep(self.units.distance_nm(.5))
        spacing.setSuffix(' '+self.units.distance_symbol)
        spacing.setValue(self.units.distance_nm(self.settings.get('guide_spacing_nm')))
        self.map.guide_spacing=self.settings.get('guide_spacing_nm')
        self.fields['guide_spacing_nm'] = spacing
        def update_guides(*_):
            self.map.set_guides(style.currentText(), self.map.guide_spacing, grid.isChecked())
        grid.toggled.connect(update_guides)
        style.currentTextChanged.connect(update_guides)
        spacing.valueChanged.connect(lambda value:self.map.set_guides(spacing=self.units.nm_from_distance(value)))
        update_guides()
        form.addRow(grid)
        form.addRow('Style', style)
        form.addRow('Base spacing', spacing)
        for key,title,attribute in [('icon_scale_2d','2D aircraft icon scale','icon_scale_2d'),('icon_scale_3d','3D aircraft icon scale','icon_scale_3d')]:
            control=QDoubleSpinBox();control.setRange(25,400);control.setDecimals(0);control.setSingleStep(10);control.setSuffix(' %')
            control.setValue(self.settings.get(key));self.fields[key]=control
            def update_scale(value,attribute=attribute):
                setattr(self.map,attribute,value/100);self.map.glyph_camera_key=None;self.map.update_aircraft_glyphs();self.map.update()
            control.valueChanged.connect(update_scale);update_scale(control.value())
            aircraft_form.addRow(title,control)
        cards.extend([display,guides])
        vector_time=QDoubleSpinBox()
        vector_time.setRange(1,120)
        vector_time.setDecimals(0)
        vector_time.setSuffix(' s')
        vector_time.setValue(self.settings.get('velocity_vector_seconds'))
        self.fields['velocity_vector_seconds']=vector_time
        def update_vector(*_):
            self.map.velocity_vector_seconds=vector_time.value()
            if hasattr(self,'timeline'):
                self.refresh_capture(force=True)
        vector_time.valueChanged.connect(update_vector)
        self.map.velocity_vector_seconds=vector_time.value()
        aircraft_form.addRow('Vector horizon',vector_time)

        case_box = QGroupBox('Approach guidance')
        case_form = QFormLayout(case_box)
        procedure_labels = QCheckBox('Procedure Labels')
        procedure_labels.setToolTip('Show speed, altitude, AoA, and other procedure markings on the map')
        procedure_labels.setChecked(self.settings.store.value('ui/procedure_labels', True, type=bool))
        self.map.procedure_labels_visible = procedure_labels.isChecked()
        procedure_labels.toggled.connect(self.set_procedure_labels_visible)
        case_form.addRow(procedure_labels)
        procedure_width = QSpinBox()
        procedure_width.setRange(1, 5)
        procedure_width.setSuffix(' px')
        procedure_width.setValue(self.settings.get('procedure_line_width'))
        self.fields['procedure_line_width'] = procedure_width
        self.procedure_stroke_sample = QFrame()
        self.procedure_stroke_sample.setFixedWidth(58)
        stroke_row = QHBoxLayout()
        stroke_row.addWidget(procedure_width)
        stroke_row.addWidget(self.procedure_stroke_sample)
        stroke_row.addStretch()
        case_form.addRow('Procedure line thickness', stroke_row)
        procedure_width.valueChanged.connect(self.update_procedure_stroke)
        self.update_procedure_stroke()

        for key, title, low, high in [('case1_glide_deg', 'Glideslope angle', .1, 15.),
                                     ('case1_runway_offset_deg', 'Runway offset to port', -45., 45.)]:
            control = QDoubleSpinBox()
            control.setRange(low,high)
            control.setDecimals(1)
            control.setSingleStep(.1)
            control.setSuffix('°')
            control.setValue(self.settings.get(key))
            self.fields[key] = control
            control.valueChanged.connect(self.apply_case_settings)
            case_form.addRow(title,control)
        cards.append(case_box)
        carrier_details_box = QGroupBox('3D carrier')
        carrier_details_form = QFormLayout(carrier_details_box)
        carrier_flag = QCheckBox('Show American flag')
        carrier_flag.setChecked(self.settings.get('carrier_flag'))
        self.fields['carrier_flag'] = carrier_flag
        carrier_details_form.addRow(carrier_flag)
        carrier_cvn = QSpinBox()
        carrier_cvn.setRange(1, 999)
        carrier_cvn.setValue(self.settings.get('carrier_cvn'))
        self.fields['carrier_cvn'] = carrier_cvn
        carrier_details_form.addRow('CVN number', carrier_cvn)
        deck_crew_enabled = QCheckBox('Deck crew')
        deck_crew_enabled.setChecked(self.settings.get('deck_crew_enabled'))
        self.fields['deck_crew_enabled'] = deck_crew_enabled
        carrier_details_form.addRow(deck_crew_enabled)
        deck_crew_count = QSpinBox()
        deck_crew_count.setRange(0, 150)
        deck_crew_count.setValue(self.settings.get('deck_crew_count'))
        self.fields['deck_crew_count'] = deck_crew_count
        carrier_details_form.addRow('Crew count', deck_crew_count)

        def update_carrier_details():
            self.map.carrier_flag = carrier_flag.isChecked()
            self.map.carrier_cvn = carrier_cvn.value()
            deck_crew_count.setEnabled(deck_crew_enabled.isChecked())
            self.map.deck_crew.configure(deck_crew_enabled.isChecked(), deck_crew_count.value())
            self.deck_crew_tool.set_available(deck_crew_enabled.isChecked())
            if hasattr(self, 'timeline'):
                self.refresh_capture(force=True)

        deck_crew_enabled.toggled.connect(update_carrier_details)
        deck_crew_count.valueChanged.connect(update_carrier_details)
        carrier_flag.toggled.connect(update_carrier_details)
        carrier_cvn.valueChanged.connect(update_carrier_details)
        update_carrier_details()
        cards.append(carrier_details_box)
        self.apply_case_settings()
        categories = {
            scoring_box: (), appearance: ('theme',), connection: ('host', 'port'),
            unit_box: ('speed_unit', 'altitude_unit', 'distance_unit'),
            gauge_box: tuple(f'{gauge}_display' for gauge in GAUGE_DISPLAY_DEFAULTS),
            history: ('recording_folder', 'buffer_s'),
            display: ('icon_scale_2d', 'icon_scale_3d', 'velocity_vector_seconds'),
            guides: ('grid', 'guide_style', 'guide_spacing_nm'),
            case_box: ('case1_glide_deg', 'case1_runway_offset_deg', 'procedure_line_width'),
            carrier_details_box: ('carrier_flag', 'carrier_cvn', 'deck_crew_enabled', 'deck_crew_count'),
        }
        self.settings_category_resets = {}
        for card, keys in categories.items():
            reset = button('Reset to default', lambda _, category=card: self.reset_settings_category(category))
            card.layout().addRow(reset)
            self.settings_category_resets[card] = (keys, reset)
        self.scoring_category = scoring_box
        self.appearance_category = appearance
        self.guidance_category = case_box
        self.procedure_labels_setting = procedure_labels
        self.score_mode_setting = score_mode
        for card in (*cards, history):
            layout=card.layout()
            layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
            for description in card.findChildren(QLabel):
                description.setWordWrap(True)
            for control in card.findChildren(QLineEdit)+card.findChildren(QComboBox):
                if control not in (folder, self.carrier, self.theme_picker):
                    control.setMaximumWidth(min(control.maximumWidth(),220))
            for control in card.findChildren(QSpinBox)+card.findChildren(QDoubleSpinBox):
                control.setMaximumWidth(150)
        self.settings_columns=SettingsColumns(cards)
        col.addWidget(self.settings_columns)
        col.addWidget(history)
        col.addWidget(self.storage_locations())
        col.addStretch()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        wrapper=QWidget();wrapper_layout=QVBoxLayout(wrapper);wrapper_layout.setContentsMargins(0,0,0,0)
        wrapper_layout.addWidget(scroll,1)
        actions = QHBoxLayout();actions.setContentsMargins(30,8,30,12)
        self.saved = label('', 'muted');self.saved.setWordWrap(True)
        actions.addWidget(self.saved,1)
        self.default_settings_button = button('Default Settings', self.reset_all_settings)
        self.save_settings_button = button('Save Settings', self.save_settings)
        actions.addWidget(self.default_settings_button)
        actions.addWidget(self.save_settings_button)
        self.settings_flash = QTimer(self, interval=45)
        self.settings_flash.timeout.connect(self.flash_save_settings)
        for widget in self.fields.values():
            signal = (widget.textChanged if isinstance(widget, QLineEdit) else
                      widget.currentTextChanged if isinstance(widget, QComboBox) else
                      widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged)
            signal.connect(self.update_settings_buttons)
        for signal in (self.scoring_tool.rules_changed, score_mode.currentTextChanged,
                       procedure_labels.toggled, self.font_picker.currentTextChanged):
            signal.connect(self.update_settings_buttons)
        self.scoring_tool.rules_changed.connect(lambda: self.refresh_capture(force=True))
        self.settings_widget = wrapper
        self.update_settings_buttons()
        wrapper_layout.addLayout(actions)
        return wrapper

    def storage_locations(self):
        group = QGroupBox('Storage locations')
        form = QFormLayout(group)
        locations = (
            ('Recordings folder', self.settings.get('recording_folder'), False),
            ('Leaderboard database', str(self.leaderboards.path), True),
            ('Preferences', self.settings.store.fileName(), None),
        )
        for title, value, is_file in locations:
            field = QLineEdit(value)
            field.setReadOnly(True)
            if is_file is None:
                form.addRow(title, field)
                continue
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button('Open folder',
                lambda _, path=field, file=is_file: self.open_storage_folder(path.text(), file)))
            form.addRow(title, row)
            if not is_file:
                self.active_recordings_path = field
            else:
                self.active_leaderboard_path = field
        return group

    def update_leaderboard_storage_path(self):
        path = Path(self.leaderboards.path)
        try:
            path = path.resolve()
        except OSError:
            pass
        self.active_leaderboard_path.setText(str(path))

    def open_storage_folder(self, path, is_file=False):
        try:
            if not path:
                raise ValueError('No location is configured.')
            folder = Path(path).expanduser().resolve()
            if is_file:
                folder = folder.parent
            folder.mkdir(parents=True, exist_ok=True)
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                return
            raise OSError(f'Could not open {folder}')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Storage location', str(exc))

    def update_procedure_stroke(self, *_):
        width = self.fields['procedure_line_width'].value()
        self.map.set_procedure_line_width(width)
        self.procedure_stroke_sample.setFixedHeight(width)
        glide = self.map.procedure_colors['glide']
        self.procedure_stroke_sample.setStyleSheet(f'background-color:{glide}; border:0;')

    def apply_case_settings(self, *_):
        if not hasattr(self, 'map'):
            return
        self.map.approach_case = self.case_selector.currentText()
        self.case_button.setText('')
        self.refresh_control_icons()
        if hasattr(self, 'fields'):
            self.map.case1_glide_deg = self.fields['case1_glide_deg'].value() if 'case1_glide_deg' in self.fields else self.settings.get('case1_glide_deg')
            self.map.case1_runway_offset_deg = self.fields['case1_runway_offset_deg'].value() if 'case1_runway_offset_deg' in self.fields else self.settings.get('case1_runway_offset_deg')
        self.map.apply_camera()
        self.capture.configure_approaches(self.map.approach_case,
            self.map.case1_glide_deg, self.map.case1_runway_offset_deg)
        if hasattr(self, 'timeline'):
            self.refresh_capture(force=True)

    def apply_unit_settings(self, *_):
        self.units = DisplayUnits(self.fields['speed_unit'].currentText(),
                                  self.fields['altitude_unit'].currentText(),self.fields['distance_unit'].currentText())
        if 'guide_spacing_nm' in self.fields:
            spacing=self.fields['guide_spacing_nm'];spacing.blockSignals(True)
            spacing.setRange(self.units.distance_nm(.1),self.units.distance_nm(100));spacing.setSingleStep(self.units.distance_nm(.5))
            spacing.setSuffix(' '+self.units.distance_symbol);spacing.setValue(self.units.distance_nm(self.map.guide_spacing));spacing.blockSignals(False)
        self.approach_view.set_distance_units(self.units)
        self.attempt_editor.set_distance_units(self.units)
        self.map.altitude_unit_symbol = self.units.altitude_symbol
        self.map.label_units=self.units
        self.map.apply_camera()
        self.update_details()
        self.update_instruments(self.current_sample,True)
        if hasattr(self,'timeline'):self.refresh_capture(force=True)

    def update_theme_preview(self):
        self.flight_palette_preview.setText(' '.join(f'<span style="color:{c};font-size:20px">●</span>' for c in [QColor.fromRgbF(*self.map.flight_colors.preview(i)).name() for i in range(6)]))

    def apply_theme_colors(self):
        custom = theme.CUSTOM_THEMES.get(theme.CURRENT_THEME)
        self.map.flight_colors.set_overrides({} if custom else self.legacy_flight_colors)
        if hasattr(self, 'roster_tool'):
            self.roster_tool.view.render()
        base = custom['base'] if custom else theme.CURRENT_THEME
        colors = dict(procedure_defaults(base))
        if custom:
            for role in colors:
                colors[role] = custom['colors'].get(f'procedure_{role}', colors[role])
        else:
            for role in colors:
                value = self.settings.get(role + '_color')
                if (self.settings.store.value('procedure_custom/'+role, not is_preset(role, value), type=bool)
                        and QColor(value).isValid()):
                    colors[role] = value
        self.map.set_procedure_colors(colors)

    def update_theme_actions(self):
        editable = theme.CURRENT_THEME in theme.CUSTOM_THEMES
        self.edit_theme_button.setEnabled(editable)
        self.remove_theme_button.setEnabled(editable)

    def save_custom_themes(self, selection):
        self.settings.save({'custom_themes': json.dumps(theme.CUSTOM_THEMES),
                            'theme_fonts': json.dumps(self.theme_fonts)})
        self.theme_picker.blockSignals(True)
        self.theme_picker.clear()
        self.theme_picker.addItems(sorted(theme.theme_names(), key=str.casefold))
        self.theme_picker.setCurrentText(selection)
        self.theme_picker.blockSignals(False)
        self.change_theme(selection)

    def edit_custom_theme(self, name=''):
        editor = ThemeEditor(self, name, theme.CUSTOM_THEMES.get(name))
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = editor.name.text().strip()
        definition = editor.definition()
        if theme.valid_custom_theme(new_name, definition):
            theme.CUSTOM_THEMES.pop(name, None)
            if name != new_name and name in self.theme_fonts:
                self.theme_fonts[new_name] = self.theme_fonts.pop(name)
            theme.CUSTOM_THEMES[new_name] = definition
            self.save_custom_themes(new_name)

    def remove_custom_theme(self):
        name = theme.CURRENT_THEME
        if name not in theme.CUSTOM_THEMES:
            return
        if QMessageBox.question(self, 'Remove custom theme', f'Remove “{name}”?') == QMessageBox.StandardButton.Yes:
            del theme.CUSTOM_THEMES[name]
            self.theme_fonts.pop(name, None)
            self.save_custom_themes(theme.DEFAULT_THEME)

    def refresh_flight_colors(self):
        self.approach_view.refresh(force=True, origin=self.playback.start)
        self.attempt_editor.update_timeline_theme()
        self.last_revision=-1
        self.timeline_event_revision=None
        self.refresh_capture(force=True)

    def change_font(self, name):
        if name == 'Theme default':
            self.theme_fonts.pop(theme.CURRENT_THEME, None)
        else:
            self.theme_fonts[theme.CURRENT_THEME] = name
        theme.set_font(self.theme_fonts.get(theme.CURRENT_THEME, theme.default_font(theme.CURRENT_THEME)))
        theme.refresh_widgets(self, theme.PALETTE)
        self.settings.save({'theme_fonts': json.dumps(self.theme_fonts)})

    def change_theme(self, name):
        old_name = theme.CURRENT_THEME
        previous = dict(theme.PALETTE)
        theme.set_theme(name)
        if theme.CURRENT_THEME != old_name:
            self.theme_fonts.pop(old_name, None)
            self.theme_fonts.pop(theme.CURRENT_THEME, None)
            self.settings.save({'theme_fonts': json.dumps(self.theme_fonts)})
        override = self.theme_fonts.get(theme.CURRENT_THEME)
        self.font_picker.blockSignals(True)
        self.font_picker.setCurrentText(override or 'Theme default')
        self.font_picker.blockSignals(False)
        theme.set_font(override or theme.default_font(theme.CURRENT_THEME))
        theme.refresh_widgets(self, previous)
        self.scoring_tool.scoreboard.render()
        self.leaderboards.table.render()
        self.apply_theme_colors()
        self.update_procedure_stroke()
        self.update_theme_preview()
        self.update_theme_actions()
        self.refresh_flight_colors()
        self.last_receiving=None
        self.settings.save({'theme': theme.CURRENT_THEME})
        self.update_settings_buttons()
        self.refresh_capture(force=True)

    def settings_values(self):
        values = {}
        for key, widget in self.fields.items():
            values[key] = (widget.text().strip() if isinstance(widget, QLineEdit) else
                           widget.currentText() if isinstance(widget, QComboBox) else
                           widget.isChecked() if isinstance(widget, QCheckBox) else widget.value())
        values['guide_spacing_nm'] = self.map.guide_spacing
        return values

    def settings_dirty(self):
        return any(value != self.settings.get(key) for key, value in self.settings_values().items())

    def update_settings_buttons(self, *_):
        values = self.settings_values()
        custom_rules = self.scoring_tool.rules != DEFAULT_RULES
        warning = theme.color('warning')
        warning_fill = theme.blend(theme.color('control'), warning, .18)
        self.scoring_settings_button.setStyleSheet(
            f'QPushButton {{color:{warning}; border-color:{warning}; background:{warning_fill};}}'
            if custom_rules else '')
        nondefault = False
        for card, (keys, reset) in self.settings_category_resets.items():
            changed = any(values[key] != DEFAULTS[key] for key in keys)
            if card is self.scoring_category:
                changed |= custom_rules or self.score_mode_setting.currentText() != 'Additive'
            if card is self.appearance_category:
                changed |= self.font_picker.currentText() != 'Theme default'
            if card is self.guidance_category:
                changed |= not self.procedure_labels_setting.isChecked()
            reset.setEnabled(changed)
            nondefault |= changed
        self.default_settings_button.setEnabled(nondefault)
        dirty = self.settings_dirty()
        self.save_settings_button.setEnabled(dirty)
        if dirty:
            if not self.settings_flash.isActive():
                self.settings_flash.start()
            self.flash_save_settings()
        else:
            self.settings_flash.stop()
            self.save_settings_button.setStyleSheet('')

    def flash_save_settings(self):
        weight = .16 + .14 * (1 + math.sin(time.monotonic() * 5))
        fill = theme.blend(theme.color('control'), theme.color('accent'), weight)
        self.save_settings_button.setStyleSheet(
            f'QPushButton {{background:{fill}; border-color:{theme.color("accent")};}}')

    def set_settings_values(self, values):
        for key, value in values.items():
            widget = self.fields[key]
            if isinstance(widget, QLineEdit):
                widget.setText(value)
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif key == 'guide_spacing_nm':
                self.map.set_guides(spacing=value)
                widget.blockSignals(True)
                widget.setValue(self.units.distance_nm(value))
                widget.blockSignals(False)
            else:
                widget.setValue(value)
        self.update_settings_buttons()

    def reset_settings_category(self, card):
        keys, _ = self.settings_category_resets[card]
        self.set_settings_values({key: DEFAULTS[key] for key in keys})
        if card is self.scoring_category:
            self.score_mode_setting.setCurrentText('Additive')
            self.scoring_tool.set_rules(DEFAULT_RULES)
        elif card is self.appearance_category:
            self.font_picker.setCurrentText('Theme default')
        elif card is self.guidance_category:
            self.procedure_labels_setting.setChecked(True)
        self.update_settings_buttons()

    def reset_all_settings(self):
        for card in self.settings_category_resets:
            self.reset_settings_category(card)

    def confirm_settings_exit(self):
        answer = QMessageBox.question(self, 'Unsaved settings', 'Save your changes before leaving Settings?',
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.save_settings()
        else:
            self.set_settings_values({key: self.settings.get(key) for key in self.fields})
        return True

    def save_settings(self):
        self.aircraft_panel.save_layout()
        values = self.settings_values()
        values.update(trails=self.trails.isChecked(), verticals=self.verticals.isChecked(), vectors=self.vectors.isChecked(),
                      approach_case=self.case_selector.currentText())
        values['guide_spacing_nm']=self.map.guide_spacing
        self.settings.save(values)
        self.capture.set_buffer(values['buffer_s'])
        self.capture.auto_record_folder=values['recording_folder']
        self.active_recordings_path.setText(values['recording_folder'])
        self.saved.setText('Settings saved')
        self.update_settings_buttons()

    def timeline_panel(self):
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(14,4,14,4)
        self.timeline_status = QLabel('No recording yet',panel)
        self.timeline_status.hide()
        self.playback_state=label('Waiting for recording','muted')
        self.scrubber = EventTimeline()
        self.timeline_event_revision=None
        self.timeline_events=[]
        self.scrubber.marker_clicked.connect(self.seek_playback)
        self.scrubber.setRange(0,1_000_000)
        self.scrubber.setSingleStep(1000)
        self.scrubber.setPageStep(10000)
        self.scrubber.setEnabled(False)
        self.scrubber.sliderPressed.connect(self.begin_scrub)
        self.scrubber.valueChanged.connect(self.scrub_to)
        self.scrubber.sliderReleased.connect(self.end_scrub)
        row = QHBoxLayout()
        self.playback_state.hide()
        self.play_button = button('', self.toggle_playback, enabled=False)
        self.play_button.setIcon(control_icon('pause'));self.play_button.setAccessibleName('Pause')
        self.back_button = button('−10', lambda: self.step_playback(-10), enabled=False)
        self.forward_button = button('+10', lambda: self.step_playback(10), enabled=False)
        self.live_button = button('Live', self.go_live, enabled=False,checkable=True)
        for control in (self.back_button,self.play_button,self.forward_button,self.live_button):
            row.addWidget(control)
        row.addWidget(self.scrubber,1)
        self.playback_position = label('00:00:00', 'muted')
        self.playback_position.hide()
        col.addLayout(row)
        return panel

    @staticmethod
    def elapsed_text(seconds):
        seconds=max(0.,seconds)
        hours=int(seconds//3600);minutes=int(seconds%3600//60)
        return f'{hours:02d}:{minutes:02d}:{seconds%60:04.1f}'

    def open_attempt_on_map(self,attempt):
        path=self.capture.attempt_path(attempt['id'])
        if path and path!=self.dvr_path:
            self.replay_manager.load_path(path,attempt=attempt);return
        if not self.dvr_path:
            self.notify('No DVR recording is available for this attempt.');return
        if self.playback.start is None:
            self.pending_attempt_jump=attempt
            self.playback_reader.request(self.dvr_path,attempt['start_s'],self.settings.get('buffer_s'),self.playback_epoch)
            return
        if not self.playback.start<=attempt['start_s']<=self.playback.end:
            self.notify('This attempt is outside the current DVR recording.');return
        self.pending_attempt_jump=None
        self.current_aircraft=attempt['aircraft_id'];self.current_carrier=attempt['carrier_id']
        self.map.set_selection(self.current_aircraft)
        self.seek_playback(attempt['start_s'])
        self.play_after_seek=True
        self.tabs.setCurrentIndex(0)
        self.refresh_capture(force=True)

    def reset_playback(self):
        self.play_after_seek=False;self.pending_attempt_jump=None

        self.playback.reset()
        self.playback_epoch += 1
        self.review_sample = None
        self.review_history = []
        self.blackbox_disk_history=[]
        self.blackbox_history_request_key=None
        self.flight_panel.black_box.clear()
        self.seek_pending = False
        self.last_seek_key = None
        self.last_seek_request = 0.
        self.drag_bounds = None
        self.last_revision = -1

    def go_live(self):
        self.replay_manager.go_live()
        self.play_after_seek=False;self.pending_attempt_jump=None
        self.playback.live()
        self.playback_epoch += 1
        self.seek_pending = False
        self.review_sample = None
        self.review_history = []
        self.last_revision = -1
        if hasattr(self, 'timeline'):
            self.refresh_capture(force=True)

    def toggle_playback(self):
        self.play_after_seek=False
        if self.playback.start is None:
            return
        self.playback_epoch += 1
        if self.playback.state == 'paused':
            self.playback.play()
        else:
            was_live=self.playback.state=='live'
            self.playback.pause()
            # Freeze the exact displayed sample, rather than advancing to a disk commit boundary.
            raw_sample = self.raw_sample
            if raw_sample is not None:
                self.review_sample=raw_sample
                frozen_time=raw_sample['capture']['monotonic_s']
                self.playback.seek(frozen_time)
                if was_live:
                    self.review_history=[s for s in self.capture.history_snapshot()
                        if s['capture']['monotonic_s']<=frozen_time]
        self.last_seek_key = None
        self.request_seek()
        self.refresh_capture(force=True)

    def begin_scrub(self):
        self.play_after_seek=False;self.pending_attempt_jump=None
        if self.playback.start is not None:
            self.drag_bounds = (self.playback.start,self.playback.end)
            self.playback.pause()

    def scrub_to(self, value):
        if self.playback.start is not None:
            start,end=self.drag_bounds or (self.playback.start,self.playback.end)
            self.seek_playback(start+(end-start)*value/1_000_000)

    def end_scrub(self):
        self.drag_bounds = None

    def seek_playback(self, target, *, keep_playing=False):
        self.play_after_seek=False;self.pending_attempt_jump=None
        self.playback.seek(target, keep_playing=keep_playing)
        self.playback_epoch += 1
        self.seek_pending = True
        self.last_seek_key = None
        self.request_seek()
        self.update_timeline()

    def step_playback(self, delta):
        if self.playback.cursor is not None:
            self.seek_playback(self.playback.cursor+delta, keep_playing=True)

    def request_seek(self):
        if self.dvr_path and self.playback.state != 'live' and self.playback.cursor is not None:
            conditions_span = WINDOW_SECONDS if self.conditions_button.isChecked() and not self.radar_tools else 0
            self.playback_reader.request(self.dvr_path,self.playback.cursor,
                max(self.settings.get('buffer_s'),self.flight_panel.black_box.span,conditions_span,SPEED_WINDOW,
                    TRAIL_SECONDS + 2 * SEARCH_PERIOD if self.larp_enabled else 0),self.playback_epoch)
            self.last_seek_request=time.monotonic()
            self.last_seek_key=(self.playback.cursor,self.playback_epoch)

    def poll_playback(self, live_sample):
        while True:
            try:
                result=self.playback_reader.results.get_nowait()
            except queue.Empty:
                break
            if result['path'] != self.dvr_path or result['epoch'] != self.playback_epoch:
                continue
            if result['error']:
                self.replay_manager.first_frame_loaded(False)
                self.play_after_seek=False;self.pending_attempt_jump=None
                self.notify('DVR read error: '+result['error'])
                continue
            if result.get('bounds'):
                start,end=result['bounds']
                if self.external_replay and self.replay_manager.info:
                    start,end=self.replay_manager.info['start_s'],self.replay_manager.info['end_s']
                writer=self.capture.writer
                if not self.external_replay and writer is not None and str(writer.path)==self.dvr_path and live_sample is not None:
                    end=max(end,live_sample['capture']['monotonic_s'])
                self.playback.bounds(start,end)
                if self.pending_attempt_jump is not None:
                    attempt=self.pending_attempt_jump;self.pending_attempt_jump=None
                    self.open_attempt_on_map(attempt)
                    continue
            if result.get('sample') is not None and self.playback.state != 'live':
                # Pausing the live edge may be ahead of the last committed disk row.
                # Keep the exact frozen RAM sample until the disk contains that point.
                current_time=self.review_sample['capture']['monotonic_s'] if self.review_sample else float('-inf')
                read_time=result['sample']['capture']['monotonic_s']
                if self.seek_pending or self.playback.state=='playing' or read_time>=current_time:
                    self.review_sample=result['sample']
                    self.review_history=result.get('history',[])
                    self.replay_manager.first_frame_loaded(True)
                    self.seek_pending=False
                    if self.play_after_seek:
                        self.play_after_seek=False
                        self.playback.play()
            elif result.get('sample') is not None and self.playback.state=='live':
                self.blackbox_disk_history=result.get('history',[])
        writer=self.capture.writer
        if not self.external_replay and self.playback.start is not None and writer is not None and str(writer.path)==self.dvr_path and live_sample:
            self.playback.bounds(self.playback.start,max(self.playback.end,live_sample['capture']['monotonic_s']))
        self.playback.tick()
        if self.external_replay and self.playback.state=='live':self.playback.pause()
        now=time.monotonic()
        if self.dvr_path and not self.closing and bool(self.dvr_path):
            if self.playback.state=='live':
                if now-self.last_seek_request>=.25:
                    history_s=max(self.settings.get('buffer_s'),self.flight_panel.black_box.span)
                    request_key=(self.playback_epoch,self.current_aircraft,self.current_carrier,history_s,self.map.approach_case,
                                 self.map.case1_glide_deg,self.map.case1_runway_offset_deg,self.units.speed_name,self.units.altitude_name)
                    need_history=(not self.radar_tools and live_sample and self.current_aircraft is not None and self.flight_panel.isVisible()
                                  and self.flight_panel.black_box.enabled and history_s>self.capture.buffer_s)
                    target=None
                    if need_history and request_key!=self.blackbox_history_request_key:
                        target=live_sample['capture']['monotonic_s'];self.blackbox_history_request_key=request_key
                    self.playback_reader.request(self.dvr_path,target,history_s,epoch=self.playback_epoch)
                    self.last_seek_request=now
            else:
                key=(self.playback.cursor,self.playback_epoch)
                # A resolved pause already has the frame and its history. Re-reading
                # the same JSON window competes with camera input on the GUI thread.
                retry = self.seek_pending and now-self.last_seek_request>=.5
                if key!=self.last_seek_key or retry:
                    self.request_seek()
        self.update_timeline()

    def update_timeline(self):
        self.map.playback_paused = self.playback.state == 'paused'
        ready=self.playback.start is not None and bool(self.dvr_path)
        self.timeline.setVisible(self.tabs.currentIndex() == 0 and self.timeline_toggle.isChecked())
        for control in (self.scrubber,self.play_button,self.back_button,self.forward_button):
            control.setEnabled(ready)
        self.live_button.setEnabled(ready and not self.external_replay)
        icon_key = (theme.CURRENT_THEME, self.case_selector.currentText(),
                    self.lock.isChecked(), self.map.playback_paused)
        if icon_key != getattr(self, '_timeline_icon_key', None):
            self.refresh_control_icons()
            self._timeline_icon_key = icon_key
        event_revision,event_catalog=self.capture.approach_event_catalog()
        if self.external_replay:
            event_catalog=[a for a in self.capture.approach_catalog()[1] if a['id'] in self.replay_manager.visible_ids()]
            event_revision=self.capture.approach_catalog()[0]
        if event_revision!=self.timeline_event_revision:
            self.timeline_event_revision=event_revision
            self.timeline_events=[dict(e,color=self.map.flight_colors.get(e['source'],{'instance_id':e['aircraft_id'],'identity':e.get('identity') or {}}))
                                  for e in recovery_events(event_catalog)]
        self.scrubber.set_events(self.timeline_events,
                                 (self.playback.start,self.playback.end) if ready else None)
        if ready:
            duration=max(0.,self.playback.end-self.playback.start)
            position=max(0.,self.playback.cursor-self.playback.start)
            state={'live':'LIVE','paused':'PAUSED','playing':'PLAYING'}[self.playback.state]
            self.playback_state.setText(state+(' · Seeking…' if self.seek_pending else ''))
            self.playback_position.setText(self.elapsed_text(position).split('.')[0])
            self.playback_position.setToolTip(f'{self.elapsed_text(position)} / {self.elapsed_text(duration)}')
            self.scrubber.set_times('00:00:00',self.elapsed_text(duration).split('.')[0],self.elapsed_text(position).split('.')[0])
            self.live_button.setChecked(self.playback.state=='live')
            if not self.scrubber.isSliderDown():
                value=round(position/duration*1_000_000) if duration>0 else 1_000_000
                self.scrubber.blockSignals(True)
                self.scrubber.setValue(value)
                self.scrubber.blockSignals(False)
        else:
            self.playback_state.setText('Waiting for recorded samples')
            self.playback_position.setText('00:00:00')
            self.scrubber.set_times('00:00:00','00:00:00','00:00:00')

    def ensure_receiver(self):
        if self.closing:
            return
        host = self.settings.get('host')
        port = self.settings.get('port')
        if not host or not 1 <= port <= 65535:
            self.notify('Set a listen address and port 1–65535 in Settings')
            return
        endpoint=(host,port)
        if self.capture.worker and self.capture.worker.is_alive():
            if self.receiver_endpoint!=endpoint:
                self.capture.stop()
            return
        self.receiver_endpoint=endpoint
        self.notify('Starting receiver')
        self.capture.start(host, port)

    @staticmethod
    def show_value(value):
        if value is None:
            return 'Unavailable'
        if isinstance(value, float):
            return f'{value:.3f}'
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def carrier_changed(self, index):
        self.current_carrier = self.carrier.itemData(index)
        self.refresh_capture(force=True)

    def aircraft_map_clicked(self,instance):
        if instance is None:
            self.aircraft_tree.clearSelection()
            self.current_aircraft=None
            self.map.set_selection(None)
            self.update_details()
            return
        self.aircraft_tree.select_aircraft(instance)

    def focus_aircraft(self, instance):
        self.aircraft_map_clicked(instance)
        if self.current_aircraft == instance:
            self.tabs.setCurrentIndex(0)
            self.map.follow_aircraft(instance)

    def aircraft_selected(self):
        selected = self.aircraft_tree.selectedItems()
        self.current_aircraft = selected[0].data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.map.set_selection(self.current_aircraft)
        self.update_details()
        self.update_instruments(self.current_sample,True)

    def blackbox_window_changed(self):
        self.last_seek_key=None
        self.request_seek()
        self.update_instruments(self.current_sample,True)

    def update_instruments(self,sample,force=False):
        if not hasattr(self,'flight_panel'):return
        if not self.flight_panel.isVisible() and not force:
            self.blackbox_history_request_key=None
            self.blackbox_disk_history=[]
            return
        if self.current_aircraft is None:self.blackbox_disk_history=[]
        reviewing=bool(self.dvr_path) and (self.external_replay or self.playback.state!='live')
        history=() if self.radar_tools else self.review_history if reviewing else self.capture.history_snapshot()
        if not self.radar_tools and not reviewing and bool(self.dvr_path) and self.blackbox_disk_history:
            merged={frame['capture']['monotonic_s']:frame for frame in self.blackbox_disk_history}
            merged.update({frame['capture']['monotonic_s']:frame for frame in history})
            history=[merged[t] for t in sorted(merged)]
        processed=self.flight_panel.set_frame(sample,history,self.current_aircraft,self.current_carrier,
            self.map.approach_case,self.map.case1_glide_deg,self.map.case1_runway_offset_deg,self.units,8.,1.,force,
            retain=bool(self.dvr_path) or reviewing)
        if processed:self.blackbox_disk_history=[]

    def aircraft_list_double_clicked(self, item, column):
        instance = item.data(0, Qt.ItemDataRole.UserRole)
        if instance is not None:
            self.map.follow_aircraft(instance)
        elif item.parent() is None:
            self.center_flight(item.text(0))

    def center_flight(self, name):
        flight = self.aircraft_tree.flight_items.get(name)
        if flight is not None and name != 'Ungrouped':
            self.map.follow_flight(name, {
                flight.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(flight.childCount())
            })

    def update_details(self):
        self.last_details_update=time.monotonic()
        if not self.current_sample:
            self.aircraft_detail.setText('Select an aircraft to view captured values.')
            self.carrier_detail.setText('No current carrier telemetry.')
            return
        if self.radar_tools or not self.raw_toggle.isChecked():return
        a = next((a for a in self.current_sample['aircraft'] if a['instance_id'] == self.current_aircraft), None)
        if a is None:
            self.aircraft_detail.setText('Select an aircraft to view captured values.')
        else:
            k = a['kinematics']; f = k.get('flight_info') or {}; c = a['controls']; identity = a['identity']
            gear = a['systems'].get('gear')
            gear_text = ', '.join(str(g.get('state', 'Unavailable')) for g in gear) if gear else None
            hook = a['systems'].get('tailhook')
            hook_down = hook.get('isDeployed') if hook else None
            hook_text = 'Down' if hook_down is True else ('Up' if hook_down is False else None)
            unified = a.get('unified_controls') or {}
            throttle = aircraft_throttle(a)
            stick = unified.get('stick_pyr') or {}
            ap = unified.get('autopilot') or {}
            modes = ap.get('modes') or {}
            names = {'heading': 'Heading', 'altitude': 'Altitude', 'hover': 'Hover',
                     'navigation': 'Navigation', 'speed': 'Speed'}
            active = [name for key, name in names.items() if modes.get(key) is True]
            unknown = [name for key, name in names.items() if modes.get(key) is None]
            if active:
                ap_text = ', '.join(active) + (' · other modes unavailable' if unknown else '')
            elif modes and all(modes.get(key) is False for key in names):
                ap_text = 'Off'
            else:
                ap_text = None
            remote_command = any(f.get('kind') == 'received_control_command' for f in (throttle, stick))
            control_note = 'Received network commands; physical controller positions not supplied' if remote_command else 'Local input values'
            fields = [('Airframe', identity.get('player_vehicle')), ('Callsign', identity.get('designation')),
                ('Actor', identity.get('actor_name')), ('Owner ID', identity.get('control_owner_id')),
                ('Seat occupants', identity.get('seat_occupants')), ('Position world (m)', k.get('position_world_m')),
                ('Pitch', f.get('pitch')), ('Roll', f.get('roll')), ('Heading', f.get('heading')),
                (f'IAS ({self.units.speed_symbol})', self.units.speed(f.get('indicatedAirspeed'))),
                (f'TAS ({self.units.speed_symbol})', self.units.speed(f.get('airspeed'))),
                ('Player Gs', f.get('playerGs')), ('Gear', gear_text),
                ('Hook', hook_text), (f'Radar altitude ({self.units.altitude_symbol})', self.units.altitude(f.get('radarAltitude'))),
                (f'ASL altitude ({self.units.altitude_symbol})', self.units.altitude(f.get('altitudeASL'))), ('AoA', f.get('aoa')),
                (f'Relative wind ({self.units.speed_symbol})', self.units.speed_vector(f.get('relativeWind'))),
                (f'Wind ({self.units.speed_symbol})', self.units.speed_vector(f.get('wind'))),
                (f'Wind speed ({self.units.speed_symbol})', self.units.speed(f.get('windSpeed'))),
                ('Throttle', throttle.get('value')), ('Throttle source', throttle.get('source')),
                ('Stick / control P/Y/R', stick.get('value')),
                ('Control source', control_note), ('Autopilot', ap_text),
                ('Flight data source', a.get('provenance', {}).get('kinematics'))]
            self.aircraft_detail.setText('\n\n'.join(f'{name}\n{self.show_value(value)}' for name, value in fields))
        carrier = next((c for c in self.current_sample['carriers'] if c['instance_id'] == self.current_carrier), None)
        if carrier:
            lines = [carrier.get('airport_name') or carrier.get('actor_name') or str(carrier['instance_id']),
                'Game world position: ' + self.show_value(carrier.get('position_world_m')),
                'Heading (app): ' + self.show_value(carrier['derived'].get('heading_deg'))]
            cables = carrier.get('cables') or []
            lines.append(f'{len(cables)} wires reported')
            for cable in cables:
                state = 'Broken' if cable.get('broken') is True else ('Engaged' if cable.get('hook_component_id') else
                    ('Ready' if cable.get('broken') is False else 'Unavailable'))
                lines.append(f"{cable.get('name', 'Cable')} · {state}")
            self.carrier_detail.setText('\n\n'.join(lines))
        else:
            self.carrier_detail.setText('No current carrier telemetry.')

    def update_attention(self, timestamp, connected):
        scope = next(pane.view for pane in self.approach_displays.panes if pane.kind == 'scope')
        states = self.approach_displays.states if timestamp is not None else {}
        contacts = []
        if states:
            rect = scope.plot_rect()
            contacts = [state for state, _ in scope.scope_contacts(rect, scope.focal_length(rect))
                        if not state.get('resting')]
        maximum = self.approach_displays.glide_view.range_nm * NM
        glide_contact = any(state['lateral'] and 0 < state['aft'] <= maximum + 1e-6
                            and not state.get('resting') for state in states.values())
        landing_contact = bool(contacts)
        toolbox_alert = ((landing_contact and not self.landing_button.isChecked())
                         or (glide_contact and not self.glide_button.isChecked()))
        now = time.monotonic()
        phase = (1 + math.cos(now * math.tau / 1.2)) / 2
        toolbox = self.map_controls_toggle
        toolbox_style = ''
        if toolbox_alert and toolbox.isVisible():
            base = QColor(theme.PALETTE['control'])
            selected = QColor(theme.PALETTE['selected'])
            background = QColor(*(round(a + (b - a) * phase)
                                  for a, b in zip(base.getRgb()[:3], selected.getRgb()[:3])))
            toolbox_style = f'QPushButton {{background:{background.name()};}}'
        if toolbox.styleSheet() != toolbox_style:
            toolbox.setStyleSheet(toolbox_style)
        return_live = self.replay_manager.return_live
        live_alert = self.external_replay and connected
        red_style = (f"QPushButton {{background:{theme.PALETTE['warning']}; color:{theme.PALETTE['map']};}}"
                     if live_alert else '')
        if return_live.styleSheet() != red_style:
            return_live.setStyleSheet(red_style)
        fade = .52 + .48 * phase
        for control, active in ((self.landing_button, landing_contact),
                                (self.glide_button, glide_contact), (return_live, live_alert)):
            effect = control.graphicsEffect()
            active = active and control.isVisible()
            if effect is None and active:
                effect = QGraphicsOpacityEffect(control)
                control.setGraphicsEffect(effect)
            if effect is not None:
                effect.setEnabled(active)
                if active:
                    effect.setOpacity(.76 + .24 * math.cos(now * math.tau / 2.4)
                                      if control is return_live else fade)

    def refresh_capture(self, force=False):
        while True:
            try:
                kind, data = self.capture.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                pass
            elif kind == 'listening':
                self.notify(f"Listening on {data['host']}:{data['port']} • waiting for mod or replay sender")

            elif kind == 'connected':
                self.notify('Receiving telemetry from ' + data['peer'])
            elif kind == 'disconnected':
                self.notify('Waiting for telemetry · receiver remains enabled')
            elif kind == 'capture_error':
                self.notify('Capture error: ' + data['message']+' • receiver retries automatically')
            elif kind == 'receiver_finished':
                if self.closing:
                    self.capture.end_dvr()
            elif kind == 'dvr_started':
                if self.capture.writers and data.get('recording_id')!=self.capture.writers[-1].recording_id:continue
                self.replay_manager.connected_file(data['path'])
                self.timeline_status.setText('Recording automatically: '+data['path'])
            elif kind == 'dvr_error':
                self.capture.end_dvr()
                self.dvr_error = data['message']
                self.replays_button.setToolTip('DVR error: '+data['message'])
                self.timeline_status.setText(data['message'])
                self.notify(data['message'])
            elif kind == 'dvr_finished':
                if data['discarded']:
                    self.replay_manager.forget_deleted(data['path'])
                self.replay_manager.refresh_browser()
                if self.capture.writer is not None and not self.capture.writer.is_alive():
                    self.capture.writer = None
                if data['error']:
                    self.replays_button.setToolTip('DVR error: '+data['error'])
                elif self.capture.writer is None:
                    self.replays_button.setToolTip('Browse automatically recorded sessions')
                    reason = data['discarded']
                    self.timeline_status.setText(f'Discarded recording ({reason})' if reason else
                                                 f"Saved {data['samples']} samples: {data['path']}")
        live_revision, live_sample, stats = self.capture.snapshot()
        self.update_telemetry_status(stats)
        self.replay_manager.poll()
        self.leaderboards.poll()
        self.scoring_tool.refresh(force=force)
        _, attempts = self.capture.approach_catalog()
        self.tabs.setTabEnabled(1, self.larp_enabled or bool(attempts))
        self.tabs.setTabEnabled(2, self.larp_enabled or bool(attempts or self.dvr_path))
        self.attempt_editor.poll(self.dvr_path)
        self.poll_playback(live_sample)
        reviewing=bool(self.dvr_path) and (self.external_replay or self.playback.state!='live')
        raw_sample = self.review_sample if reviewing else live_sample
        self.raw_sample = raw_sample
        map_sample = self.radar_frame(raw_sample, reviewing) if self.larp_enabled else raw_sample
        sample = map_sample if self.radar_tools else raw_sample
        age = stats['age_s']
        score_time = None
        if reviewing and not self.seek_pending:
            score_time = self.playback.cursor
        elif not reviewing and raw_sample and age is not None and age <= 2:
            score_time = raw_sample['capture']['monotonic_s']
        advancing = not reviewing or self.playback.state == 'playing'
        radio_attempts = attempts
        if self.radar_tools:
            contacts = {aircraft['instance_id'] for aircraft in (sample or {}).get('aircraft', ())}
            radio_attempts = [attempt for attempt in attempts if attempt['aircraft_id'] in contacts]
        self.deck_crew_tool.update_lso(radio_attempts, score_time,
            (self.dvr_path, self.playback_epoch, self.capture.connection_id, self.current_carrier), sample,
            advancing=advancing)
        self.scoring_tool.update_record_celebrations(raw_sample, attempts, score_time, self.playback_epoch,
                                                     advancing=advancing)
        self.scoring_tool.update_timeline_score(raw_sample, attempts, score_time, self.current_aircraft,
                                                self.playback_epoch)
        if self.tabs.currentIndex() == 1:
            self.approach_view.refresh(force=force,origin=self.playback.start)
        elif self.tabs.currentIndex() == 2:
            self.attempt_editor.record_end = self.playback.end
            # Reload active tracks at 2 Hz; lists/selection still share the Approach model.
            self.attempt_editor.refresh(force=force or time.monotonic()-self.attempt_editor.last_request>.5,origin=self.playback.start)
        display_history=self.review_history if reviewing else None
        if reviewing:
            sample_time=sample['capture']['monotonic_s'] if sample else None
            revision=('review',self.playback_epoch,sample_time)
            label_time=self.elapsed_text(sample_time-self.playback.start) if sample_time is not None and self.playback.start is not None else 'loading'
            self.map_status.setText(f"DVR {self.playback.state.upper()} · Recorded sample {label_time} · Live capture: {stats['status']} / {stats['samples']} samples")
        else:
            revision=('live',live_revision)
            age=f" · Last sample {stats['age_s']:.1f}s ago" if stats['age_s'] is not None else ''
            self.map_status.setText(f"{stats['status']} · {stats['samples']} samples · {stats['hz']:.1f} Hz · RAM {stats['buffer_samples']} samples / {stats['buffer_bytes']/1048576:.1f} MiB"+age)
        if self.larp_enabled:
            revision = (*revision, self.radar_contacts.key, (map_sample or {}).get('_radar_display_s'))
        self.current_sample=sample
        self.update_mission_clock(sample)
        self.stack_tool.set_frame(sample,self.map.approach_case,self.units)
        self.update_instruments(sample,force)
        if sample is None:
            self.conditions_tool.set_frame(None, None, self.units, None)
            self.roster_tool.set_frame(None, None, self.units)
            self.approach_displays.set_frame(None,self.current_carrier,self.map.approach_case,self.map.case1_glide_deg,self.map.case1_runway_offset_deg,self.units,self.current_aircraft)
            self.lso_hints.set_frame(None,{},self.map.approach_case,None)
            self.update_attention(None, stats['status'] == 'Connected')
            if revision != self.last_revision:
                self.aircraft_detail.setText('Select an aircraft to view captured values.')
                self.carrier_detail.setText('No current carrier telemetry.')
                self.carrier.blockSignals(True)
                self.carrier.clear()
                self.carrier.blockSignals(False)
                self.carrier.setEnabled(False)
                self.lock.setEnabled(False)
                self.home_button.setEnabled(False)
                self.aircraft_tree.clear()
                self.map.clear_telemetry()
            self.deck_crew_tool.refresh_status(reviewing)
            self.last_revision = revision
            return
        changed = revision != self.last_revision
        if changed:
            self.last_revision = revision
            present = self.aircraft_tree.update_aircraft(sample,self.map.color_for)
            if self.current_aircraft not in present:
                self.current_aircraft = None
                self.map.set_selection(None)
            recovery_carriers = [c for c in sample['carriers'] if c.get('cables')]
            ids = [c['instance_id'] for c in recovery_carriers]
            old_ids = [self.carrier.itemData(i) for i in range(self.carrier.count())]
            if ids != old_ids:
                self.carrier.blockSignals(True)
                self.carrier.clear()
                for c in recovery_carriers:
                    name = c.get('airport_name') or c.get('actor_name') or str(c['instance_id'])
                    self.carrier.addItem(name + f" ({len(c.get('cables') or [])} wires)", c['instance_id'])
                if self.current_carrier not in ids:
                    primary = max(recovery_carriers, key=lambda c: len(c.get('cables') or []), default=None)
                    self.current_carrier = primary['instance_id'] if primary else None
                if self.current_carrier in ids:
                    self.carrier.setCurrentIndex(ids.index(self.current_carrier))
                self.carrier.blockSignals(False)
            self.carrier.setEnabled(bool(ids))
            self.lock.setEnabled(bool(ids))
            self.home_button.setEnabled(bool(ids))
            if force or (reviewing and self.playback.state=='paused') or time.monotonic()-self.last_details_update>=.25:
                self.update_details()
        rtb_context = (self.radar_tools, self.playback_epoch if reviewing else None)
        radar_history = self.radar_contacts.history if self.larp_enabled else ()
        self.roster_tool.set_frame(sample, self.current_carrier, self.units,
                                   radar_history if self.radar_tools else (display_history or ()),
                                   context=rtb_context)
        self.conditions_tool.set_frame(sample, self.current_carrier, self.units, rtb_context,
                                       radar_history if reviewing and self.radar_tools else (display_history or ()))
        self.approach_displays.set_frame(sample,self.current_carrier,self.map.approach_case,self.map.case1_glide_deg,self.map.case1_runway_offset_deg,self.units,self.current_aircraft)
        self.update_attention(score_time, stats['status'] == 'Connected')
        fresh=reviewing or (stats.get('age_s') is not None and stats['age_s']<=2.)
        self.lso_hints.set_frame(sample,self.approach_displays.states,self.map.approach_case,self.current_aircraft,fresh,
            (self.current_carrier,self.map.case1_glide_deg,self.map.case1_runway_offset_deg,self.playback_epoch))
        if self.tabs.currentIndex() == 0 and (changed or force):
            history = () if self.larp_enabled else display_history if reviewing else self.capture.history_snapshot()
            self.map.render_telemetry(map_sample, history, self.current_carrier, self.lock.isChecked(),
                self.trails.isChecked() and not self.larp_enabled, self.verticals.isChecked(), self.vectors.isChecked(),
                scoring_rules=self.scoring_tool.rules, rtb_context=(rtb_context, self.larp_enabled),
                live_data=raw_sample if self.larp_lite else None)
            self.update_radar_sweep()
        self.deck_crew_tool.refresh_status(reviewing)

    def closeEvent(self, event):
        if not self.closing and self.settings_dirty() and not self.confirm_settings_exit():
            event.ignore()
            return
        self.receive_timer.stop()
        self.playback_reader.stop()
        if not self.scoring_tool.worker.stopping.is_set():self.scoring_tool.worker.stop()
        if not self.replay_manager.worker.stopping.is_set():self.replay_manager.worker.stop()
        if not self.leaderboards.worker.stopping.is_set():self.leaderboards.shutdown()
        if not self.attempt_editor.worker.stopping.is_set():
            self.attempt_editor.worker.stop()
        if self.capture.busy() or self.playback_reader.is_alive() or self.attempt_editor.worker.is_alive() or self.scoring_tool.worker.is_alive() or self.replay_manager.worker.is_alive() or self.leaderboards.worker.is_alive():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.save_settings()
                self.capture.shutdown()
                self.notify('Closing receiver and saving DVR...')
            QTimer.singleShot(100, self.close)
            return
        self.save_settings()
        self.capture_timer.stop()
        self.map.timer.stop()
        self.closing=True
        self.aircraft_panel.shutdown()
        self.approach_displays.shutdown()
        self.stack_tool.shutdown()
        self.roster_tool.shutdown()
        self.deck_crew_tool.shutdown()
        self.conditions_tool.shutdown()
        self.scoring_tool.shutdown()
        super().closeEvent(event)
