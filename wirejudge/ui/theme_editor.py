"""Edit named palettes and preview the result without changing the active theme."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QVector3D
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from wirejudge.ui import theme
from wirejudge.map.case3_overlay import ProcedureLabels
from wirejudge.display_units import DisplayUnits
from wirejudge.aircraft.gauge_card import GaugeCard
from wirejudge.map.map_view import MapView
from wirejudge.map.procedure_colors import defaults as procedure_defaults


def preview_snapshot():
    wires = [
        dict(instance_id=i, position_world_m=[0, 20, z],
             endpoint1_world_m=[-16, 20, z+3], endpoint2_world_m=[16, 20, z-3])
        for i, z in enumerate((-58, -48, -38, -28), 1)
    ]
    return {
        'capture': {'connection_id': 'theme-preview', 'monotonic_s': 0.},
        'source': {'process_id': 'theme-preview'},
        'carriers': [dict(instance_id=100, position_world_m=[0, 0, 0],
                          rotation_xyzw=[0, 0, 0, 1], cables=wires)],
        'aircraft': [
            dict(instance_id=i, identity={'actor_name': name, 'player_vehicle': 'F/A-26B'},
                 kinematics={'position_world_m': [x, 41+i*4, z], 'rotation_xyzw': [0, 0, 0, 1],
                             'sea_level_world_y_m': 0., 'flight_info': {
                                 'altitudeASL': 41+i*4, 'heading': 0., 'aoa': 8.,
                                 'indicatedAirspeed': 72. + i}},
                 systems={'gear': [{'state': 'Extended'}], 'tailhook': {'isDeployed': True}})
            for i, (name, x, z) in enumerate((
                ('Raven', -18, -205), ('Viper', -125, -80), ('Hawk', -93, 85),
                ('Falcon', 140, 100), ('Eagle', 148, -36), ('Owl', 128, -170),
            ), 1)
        ],
    }


class ThemePreview(QTabWidget):
    def __init__(self):
        super().__init__()
        self.map = MapView()
        self.map.playback_paused = True
        self.map.is_3d = True
        self.map.north_up = False
        self.map.heading = -135.
        self.map.tilt = 48.
        self.carrier_label = ProcedureLabels(self.map)
        self.map.text_overlays += (self.carrier_label,)
        self.map.installEventFilter(self)
        self.overview = False
        self.map.span = 640.
        self.map.guide_spacing = .15
        self.map.icon_scale_3d = 1.8
        self.map.label_fields = ['name', 'ias']
        self.snapshot = preview_snapshot()
        map_page = QWidget()
        map_layout = QVBoxLayout(map_page)
        modes = QComboBox()
        modes.addItems(['3D carrier and aircraft', '2D carrier and aircraft', 'Case I procedures', 'Case III procedures'])
        modes.currentIndexChanged.connect(self.change_dimension)
        map_layout.addWidget(modes)
        map_layout.addWidget(self.map, 1)
        self.addTab(map_page, 'Map')

        graphs = QWidget()
        graphs_layout = QVBoxLayout(graphs)
        self.plots = []
        distance = np.linspace(0., 1., 90)
        for title, units, role, values in (
            ('Glide path', 'ft', 'chart_glide', 20 + 365*distance + 13*np.sin(distance*13)),
            ('Localizer deviation', 'ft', 'chart_localizer', 13*np.sin(distance*14) - 7*distance),
            ('Angle of attack', '°', 'chart_aoa', 8. + .55*np.sin(distance*14)),
            ('Indicated airspeed', 'kt', 'chart_ias', 139. + 9*distance + 3*np.sin(distance*11)),
        ):
            plot = pg.PlotWidget()
            plot.setLabel('left', units)
            plot.getViewBox().invertX(True)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.showGrid(x=True, y=True, alpha=.22)
            curve = plot.plot(distance, values)
            graphs_layout.addWidget(plot, 1)
            self.plots.append((plot, curve, title, role))
        self.plots[-1][0].setLabel('bottom', 'Distance aft of wire 3', units='NM')
        self.addTab(graphs, 'Graphs')

        instruments = QWidget()
        instruments_layout = QVBoxLayout(instruments)
        title = QLabel('RAVEN 21 · F/A-26B')
        title.setObjectName('aircraftTitle')
        instruments_layout.addWidget(title)
        grid = QGridLayout()
        self.gauges = []
        samples = {
            'attitude': {'pitch': 4., 'roll': -12., 'heading': 350., 'track': 355., 'flight_path_angle': -3.5},
            'ias': {'ias': 145.},
            'configured': {'gear': 'down', 'hook': 'up', 'inside': True},
            'inputs': {'throttle': 66., 'input_pitch': .22, 'input_yaw': -.12, 'input_roll': -.38},
            'fuel': {'fuel': 84., 'fuel_capacity': 1200., 'fuel_percent': 7.},
            'battery': {'battery_percent': 32., 'battery_connected': True, 'battery_energized': False},
            'g_meter': {'gs_load': 1.27, 'g_min': .82, 'g_max': 3.65, 'g_span_s': 30},
        }
        for i, (kind, values) in enumerate(samples.items()):
            gauge = GaugeCard(kind, self)
            gauge.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
            gauge.setAcceptDrops(False)
            gauge.set_values(values, DisplayUnits(), None, 8., 1.)
            grid.addWidget(gauge, i // 3, i % 3)
            self.gauges.append(gauge)
        instruments_layout.addLayout(grid)
        black_box = QLabel('BLACK BOX')
        black_box.setObjectName('eyebrow')
        instruments_layout.addWidget(black_box)
        plot = pg.PlotWidget()
        plot.setMinimumHeight(155)
        plot.setLabel('bottom', 'Time', units='s')
        plot.setLabel('left', 'IAS', units='kt')
        plot.setXRange(-30, 0, padding=0)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.showGrid(x=True, y=True, alpha=.12)
        seconds = np.linspace(-30., 0., 120)
        speed = 145. - seconds * .65 + 2.5 * np.sin(seconds * .56)
        curve = plot.plot(seconds, speed)
        self.black_box_cursor = pg.InfiniteLine(-8, angle=90, movable=False)
        plot.addItem(self.black_box_cursor)
        self.black_box_readout = pg.TextItem(anchor=(1, 1))
        sample_speed = float(np.interp(-8, seconds, speed))
        self.black_box_readout.setPos(-8, sample_speed)
        self.black_box_readout.setText(f'-8.0 s · {sample_speed:.1f} kt')
        plot.addItem(self.black_box_readout, ignoreBounds=True)
        self.plots.append((plot, curve, 'Indicated airspeed', 'accent'))
        instruments_layout.addWidget(plot)
        selected = QPushButton('Approach selected')
        selected.setCheckable(True)
        selected.setChecked(True)
        instruments_layout.addWidget(selected)
        self.note = QLabel('Sample approach · hook up')
        self.note.setObjectName('muted')
        instruments_layout.addWidget(self.note)
        instruments_layout.addStretch()
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setWidget(instruments)
        self.addTab(inspector_scroll, 'Inspector')

    def eventFilter(self, watched, event):
        if watched is self.map and event.type() == QEvent.Type.Resize and self.overview:
            QTimer.singleShot(0, self.frame_procedures)
        return super().eventFilter(watched, event)

    def change_dimension(self, index):
        self.overview = index >= 2
        self.map.is_3d = index == 0
        self.map.north_up = index != 0
        self.map.heading = -135. if index == 0 else -90.
        self.map.span = 640.
        self.map.guide_spacing = 5. if self.overview else .15
        self.map.opts['center'] = QVector3D()
        self.map.approach_case = 'Case 3' if index == 3 else 'Case 1'
        self.draw_map()
        if self.overview:
            self.frame_procedures()

    def frame_procedures(self):
        if not self.overview:
            return
        overlay = self.map.case3_overlay if self.map.approach_case == 'Case 3' else self.map.case1_marshal
        paths = [item.pos[:, :2] for item in overlay.items
                 if getattr(item, 'pos', None) is not None and len(item.pos)]
        if not paths:
            return
        points = np.concatenate([np.zeros((1, 2)), *paths])
        low, high = points.min(axis=0), points.max(axis=0)
        x, y = (low + high) / 2
        width, height = high - low
        aspect = self.map.width() / max(1, self.map.height())
        # In north-up plan view world Y is screen vertical.
        self.map.opts['center'] = QVector3D(x, y, 0)
        self.map.span = 1.34 * max(width, height * aspect)
        self.map.apply_camera()

    def draw_map(self):
        # A zoomed-out procedure view would stack six aircraft tags on the ship.
        snapshot = {**self.snapshot, 'aircraft': []} if self.overview else self.snapshot
        if self.overview:
            snapshot['aircraft'] = [{
                'instance_id': -1,
                'kinematics': {'sea_level_world_y_m': 0.},
            }]
        self.map.render_telemetry(snapshot, [], 100, trails=False, verticals=True, vectors=False)
        self.carrier_label.set_labels([((0, 0, 20), 'Carrier', self.map.paint_color('carrier'), True)]
                                      if self.overview else [])
        self.carrier_label.setVisible(self.overview)

    def apply_palette(self, palette, procedures, flights, font):
        self.setStyleSheet(theme.stylesheet(palette, font))
        self.map.preview_palette = palette
        self.map.setBackgroundColor(palette['map'])
        self.map.flight_colors.set_overrides(dict(zip(map(str, range(6)), flights)))
        self.map.set_procedure_colors(procedures)
        self.map.apply_carrier_theme()
        self.map.guide_alphas.clear()
        for overlay in self.map.text_overlays:
            overlay.refresh_label_font()
        self.draw_map()
        for plot, curve, title, role in self.plots:
            plot.setBackground(palette['chart_background'])
            plot.setTitle(title, color=palette['muted'])
            curve.setPen(pg.mkPen(palette[role], width=2))
            for name in ('left', 'bottom'):
                axis = plot.getAxis(name)
                axis.setPen(palette['border'])
                axis.setTextPen(palette['muted'])
        for gauge in self.gauges:
            gauge.preview_palette = palette
            gauge.dial_face_key = None
            gauge.update()
        self.black_box_cursor.setPen(pg.mkPen(palette['text'], style=Qt.PenStyle.DashLine))
        self.black_box_readout.fill = pg.mkBrush(palette['panel'])
        self.black_box_readout.setColor(palette['chart_cursor'])


class ThemeEditor(QDialog):
    def __init__(self, parent, name='', definition=None):
        super().__init__(parent)
        self.setWindowTitle('Edit custom theme' if name else 'Add custom theme')
        self.resize(940, 680)
        self.original = name
        self.overrides = dict(definition['colors']) if definition else {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name)
        self.name.setMaxLength(60)
        form.addRow('Name', self.name)
        self.base = QComboBox()
        choices = sorted((candidate for candidate in theme.theme_names() if candidate != name), key=str.casefold)
        self.base.addItems(['Blank', *choices])
        self.base.setCurrentText(definition['base'] if definition else theme.CURRENT_THEME)
        form.addRow('Start from', self.base)
        self.font = QComboBox()
        self.font.addItems(['Theme default', *theme.FONT_OPTIONS])
        self.font.setCurrentText(definition.get('font', 'Theme default') if definition else 'Theme default')
        form.addRow('Font', self.font)
        layout.addLayout(form)

        body = QHBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls = QWidget()
        color_form = QFormLayout(controls)
        color_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.swatches = {}
        self.category_headers = []
        advanced = theme.ADVANCED_COLORS
        for group, roles in (
            ('Basic colors', theme.BASIC_COLORS),
            ('Graphs and gauges', {key: title for key, title in advanced.items()
                                   if not key.startswith(('flight_', 'procedure_'))}),
            ('Procedures', {key: title for key, title in advanced.items() if key.startswith('procedure_')}),
            ('Flight colors', {key: title for key, title in advanced.items() if key.startswith('flight_')}),
            ('Map grid', {}),
        ):
            heading = QLabel(group)
            self.category_headers.append(heading)
            color_form.addRow(heading)
            for role, title in roles.items():
                row = QHBoxLayout()
                swatch = QPushButton()
                swatch.clicked.connect(lambda _, key=role: self.choose(key))
                reset = QPushButton('Reset')
                reset.clicked.connect(lambda _, key=role: self.reset(key))
                row.addWidget(swatch, 1)
                row.addWidget(reset)
                color_form.addRow(title, row)
                self.swatches[role] = (swatch, reset)
        self.grid_opacity = QSpinBox()
        self.grid_opacity.setRange(0, 100)
        self.grid_opacity.setSuffix('%')
        self.grid_opacity.valueChanged.connect(self.set_grid_opacity)
        self.reset_opacity = QPushButton('Reset')
        self.reset_opacity.clicked.connect(lambda: self.reset('guide_alpha'))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.grid_opacity, 1)
        opacity_row.addWidget(self.reset_opacity)
        color_form.addRow('Map grid opacity', opacity_row)
        scroll.setWidget(controls)
        body.addWidget(scroll, 1)

        self.preview = ThemePreview()
        self.preview.setMinimumWidth(420)
        body.addWidget(self.preview, 2)
        layout.addLayout(body, 1)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.name.textChanged.connect(self.update_name)
        self.base.currentTextChanged.connect(self.refresh_preview)
        self.font.currentTextChanged.connect(self.refresh_preview)
        self.refresh_preview()
        self.update_name()

    def update_name(self):
        name = self.name.text().strip()
        exists = any(n.casefold() == name.casefold() for n in theme.theme_names() if n != self.original)
        valid = bool(name) and not exists and name.casefold() != 'blank'
        self.save_button.setEnabled(valid)
        self.error.setText('Choose a unique theme name.' if name and not valid else '')

    def definition(self):
        choice = self.base.currentText()
        inherited = theme.CUSTOM_THEMES.get(choice, {'base': choice, 'colors': {}})
        font = self.font.currentText()
        if font == 'Theme default':
            font = inherited.get('font', font)
        return {'base': inherited['base'], 'colors': {**inherited['colors'], **self.overrides}, 'font': font}

    def choose(self, role):
        previous = dict(self.overrides)
        dialog = QColorDialog(QColor(self.values[role]), self)
        dialog.setWindowTitle('Theme color')

        def preview(value):
            if value.isValid():
                self.overrides[role] = value.name()
                self.refresh_preview()

        dialog.currentColorChanged.connect(preview)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            preview(dialog.selectedColor())
        else:
            self.overrides = previous
            self.refresh_preview()

    def set_grid_opacity(self, percent):
        self.overrides['guide_alpha'] = percent / 100
        self.refresh_preview()

    def reset(self, role):
        self.overrides.pop(role, None)
        self.refresh_preview()

    def refresh_preview(self):
        definition = self.definition()
        base, colors = definition['base'], definition['colors']
        palette = theme.custom_palette(base, colors)
        procedures = {role: colors.get(f'procedure_{role}', value)
                      for role, value in procedure_defaults(base).items()}
        flights = [colors.get(f'flight_{i}', value)
                   for i, value in enumerate(theme.flight_palette(base))]
        self.values = {**palette, **{f'procedure_{key}': value for key, value in procedures.items()},
                       **{f'flight_{i}': value for i, value in enumerate(flights)}}
        for heading in self.category_headers:
            heading.setStyleSheet(f"color:{palette['accent']}; background:{palette['raised']};"
                                  f"border-bottom:1px solid {palette['accent']};"
                                  "font-size:15px; font-weight:700; padding:7px;")
        for role, (swatch, reset) in self.swatches.items():
            value = self.values[role]
            swatch.setText(value.upper())
            contrast = '#111111' if QColor(value).lightness() > 135 else '#FFFFFF'
            swatch.setStyleSheet(f'background:{value}; color:{contrast};')
            reset.setEnabled(role in self.overrides)
        self.grid_opacity.blockSignals(True)
        self.grid_opacity.setValue(round(palette['guide_alpha'] * 100))
        self.grid_opacity.blockSignals(False)
        self.reset_opacity.setEnabled('guide_alpha' in self.overrides)
        font = definition['font']
        if font == 'Theme default':
            font = theme.default_font(base)
        self.preview.apply_palette(palette, procedures, flights, font)
