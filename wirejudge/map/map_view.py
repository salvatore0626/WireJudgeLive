from wirejudge.map.map_rendering import MapLines, MapScaleBar, RadarOutlineLabels, ProcedureLines, CarrierHullLines, DeckDepthMask, TrailLines, GL_DEPTH_TEST, MAP_LINE_OPTIONS
"""GPU map surface. Coordinates: east, north, altitude, in metres."""
import math
import time
from wirejudge.ui import theme
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMatrix4x4, QVector3D
from pyqtgraph.opengl import GLViewWidget, GLMeshItem
from wirejudge.approach.approach_geometry import case1_glide_display, case1_volume, grading_gate_sections, point
from wirejudge.scoring.landing_scoring import DEFAULT_RULES
from wirejudge.map.deck_crew import DeckCrew
from wirejudge.map.carrier_holidays import CarrierHolidays
from wirejudge.map.carrier_wireframe import carrier_geometry, deck_height_at, deck_projection, tower_details, tower_flag, tower_numbers
from wirejudge.map.case3_overlay import Case3Overlay, ProcedureLabels
from wirejudge.map.case1_overlay import Case1MarshalOverlay
from wirejudge.map.procedure_colors import DEFAULT_COLORS, rgba, vertex_colors
from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.aircraft.aircraft_labels import (aircraft_callsign, aircraft_label, label_volume, DEFAULT_LABEL_FIELDS,
                             rtb_distance, rtb_speed, settled_aircraft)
from wirejudge.aircraft.aircraft_label_overlay import AircraftLabels
from wirejudge.display_units import DisplayUnits
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion
from wirejudge.aircraft.flight_colors import FlightColors
from wirejudge.aircraft.flight_instruments import carrier_wind, gear_state
from wirejudge.ui.settings_store import DEFAULTS
from wirejudge.map.map_orientation import compass_axes
from wirejudge.map.north_indicator import NorthIndicator
from wirejudge.approach.approach_status import status_color
from wirejudge.aircraft.aircraft_glyphs import arrow_directions, velocity_vector, FACES, OUTLINE_EDGES, SIZE_SCALE, airframe_key, detailed_glyph
from wirejudge.map.map_trails import MapTrails
from wirejudge.map.map_guides import levels, opacity, weights, geometry, NM
from wirejudge.map.marshal_vectors import MarshalVectors
from wirejudge.telemetry.larp_radar import PAR_HALF_ANGLE, PAR_RANGE, SEARCH_MAX_RANGE, par_offset, search_bearing

RADAR_CIRCLE=np.asarray([(math.cos(a),math.sin(a),0)
    for i in range(20) for a in (2*math.pi*i/20,2*math.pi*(i+1)/20)])


class MapView(GLViewWidget):
    camera_changed = Signal(str)
    aircraft_clicked = Signal(object)
    aircraft_double_clicked = Signal(object)
    vector_mode_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.preview_palette = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundColor(self.paint_color('#10151B'))
        self.is_3d = False
        self.carrier_flag = DEFAULTS['carrier_flag']
        self.carrier_cvn = DEFAULTS['carrier_cvn']
        self.carrier_number_faces = []
        self.carrier_flag_frames = []
        self.flag_time = 0.
        self.flag_updated_at = time.perf_counter()
        self.visible_number_faces = None
        self.approach_case = 'Case 1'
        self.case1_glide_deg = 3.5
        self.case1_runway_offset_deg = 10.0
        self.span = 12 * 1852.0
        self.heading = -90.0
        self.north_up = True;self.carrier_up=False
        self.tilt = 40.0
        self.keys = set()
        self.flight_colors = FlightColors()
        self.trail_cache = MapTrails()
        self.approach_states={};self.warning_phase=1.
        self.carrier_heading_deg=None
        self.reference_heading_deg = 0.
        self.selected_id = None
        self.follow_id = None
        self.follow_flight_name = None
        self.flight_follow_altitude = 0.
        self.follow_height_offset = 0.
        self.selection_point = None
        self.selection_color = (1.,1.,1.,1.)
        self.pick_points = []
        self.marker_specs = []
        self.airframe_keys = {}
        self.aircraft_deck_heights = {}
        self.focus_deck_faces = []
        self.focus_cursor_key = None
        self.focus_floor = 0.
        self.focus_snap = None
        self.rtb_label_context = None
        self.rtb_label_values = {}
        self.radar_specs=[]
        self.glyph_base_colors={}
        self.label_fields=list(DEFAULT_LABEL_FIELDS)
        self.procedure_labels_visible = True
        self.procedure_opacity = 1.
        self.radar_transform = None
        self.radar_search_visible = True
        self.radar_par_visible = True
        self.radar_par_sweep_visible = True
        self.radar_opacity = 1.
        self.radar_outline_key = None
        self.procedure_visibility = dict.fromkeys(('marshal', 'approach', 'missed', 'glide', 'gates'), True)
        self.label_units=DisplayUnits()
        self.glyph_revision = 0
        self.glyph_camera_key = None
        self.wire_specs = []
        self.wire_camera_key = None
        self.velocity_vector_seconds = 3.
        self.icon_scale_2d=1.;self.icon_scale_3d=1.
        self.carrier_positions={}
        self.aircraft_centers = np.empty((0,3),dtype=np.float32)
        self.press_position = None
        self.click_dragged = False
        self.compass_pressed = False
        self.last_tick = time.perf_counter()
        self.grid_step = None
        self.grid_anchor = None
        self.altitude_unit_symbol = 'ft'
        self.guide_style = 'Rings'
        self.guide_spacing = 5.0
        self.guides_visible = True
        self.guide_layers = []
        self.guide_keys = {}
        self.guide_alphas = {}
        for _ in range(5):
            item = MapLines(glOptions=MAP_LINE_OPTIONS,mode='lines', color=(.208, .259, .302, .45), antialias=False, width=1)
            self.addItem(item)
            self.guide_layers.append(item)
        self.grid = self.guide_layers[0]  # Compatibility for existing integrations.

        self.case_lines = ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines', color=(.25,.85,1.,.8), antialias=False, width=1)
        self.case_fill = GLMeshItem(smooth=False, color=(.2,.7,1.,.1), shader='balloon',
                                    glOptions='translucent', drawEdges=False)
        self.case_lines.setVisible(False)
        self.case_fill.setVisible(False)
        self.case_fill.setDepthValue(100)  # Tint after entities so the transparent volume cannot hide them.
        self.case_glide_fade = None
        self.addItem(self.case_fill)
        self.addItem(self.case_lines)
        self.grade_gates = ProcedureLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
        self.grade_gates.setDepthValue(101)
        self.grade_gates.setVisible(False)
        self.addItem(self.grade_gates)
        self.grade_gate_labels = ProcedureLabels(self)
        self.grade_gate_labels.hide()
        self.grade_gate_key = None
        self.sweep_lines = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
        self.sweep_lines.setDepthValue(101)
        self.sweep_lines.setVisible(False)
        self.addItem(self.sweep_lines)
        self.radar_outline = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
        self.radar_outline.setDepthValue(101)
        self.radar_outline.hide()
        self.addItem(self.radar_outline)
        self.radar_labels = RadarOutlineLabels(self)
        self.case3_overlay=Case3Overlay(self)
        self.case1_marshal=Case1MarshalOverlay(self)
        self.set_procedure_colors(DEFAULT_COLORS)
        self.selection_lines = MapLines(glOptions=MAP_LINE_OPTIONS,mode='lines',antialias=False,width=1)
        self.selection_lines.setDepthValue(200)
        self.selection_lines.setVisible(False)
        self.addItem(self.selection_lines)
        ring = RADAR_CIRCLE[::2]
        self.focus_disk = GLMeshItem(
            vertexes=np.vstack((np.zeros(3), ring)).astype(np.float32),
            faces=np.array([(0, i + 1, (i + 1) % len(ring) + 1)
                            for i in range(len(ring))], dtype=np.uint32),
            smooth=False, drawEdges=False, glOptions=MAP_LINE_OPTIONS)
        self.focus_disk.setDepthValue(130)
        self.focus_altitude_line = TrailLines(glOptions={**MAP_LINE_OPTIONS, GL_DEPTH_TEST: True},
                                             mode='lines', antialias=False, width=1)
        self.focus_altitude_line.setDepthValue(2.6)  # Draw against the deck mask before the hull clears it.
        for item in (self.focus_disk, self.focus_altitude_line):
            item.hide()
            self.addItem(item)
        self.aircraft_labels=AircraftLabels(self)
        self.marshal_vectors = MarshalVectors(self)
        self.vector_mode = False
        self.text_overlays = (self.case3_overlay.labels, self.case1_marshal.labels, self.grade_gate_labels,
                              self.aircraft_labels, self.marshal_vectors, self.radar_labels)
        self.north_indicator=NorthIndicator(self)
        self.holidays = CarrierHolidays(self)
        self.deck_crew = DeckCrew(self)
        self.deck_aircraft_count = 0
        self.text_overlays += (self.deck_crew.labels,)
        self.scale_bar = MapScaleBar(self)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.frames = 0
        self.playback_paused = False
        self.redraw_timer = QTimer(self)
        self.redraw_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.redraw_timer.setInterval(22)  # ~45 FPS with integer-millisecond Qt timers.
        self.redraw_timer.timeout.connect(self.refresh_visible_map)
        self.apply_camera()

    def paint_color(self, role):
        return theme.color(role, self.preview_palette)

    def apply_carrier_theme(self):
        if hasattr(self, 'carrier_deck_lines'):
            color = rgba(self.paint_color('carrier'), .9)
            self.carrier_deck_lines.setData(color=color)
            self.carrier_hull.setData(color=color)
        if hasattr(self, 'focus_disk'):
            self.update_focus_cursor()
        self.scale_bar.update()

    def set_procedure_colors(self,palette):
        self.procedure_colors=dict(palette)
        opacity = self.procedure_opacity
        if self.case_glide_fade is not None:
            wall_alpha, edge_alpha = self.case_glide_fade
            self.case_lines.setData(color=vertex_colors(palette['glide'], .8 * opacity, edge_alpha))
            self.case_fill.opts['meshdata'].setVertexColors(vertex_colors(palette['glide'], .1 * opacity, wall_alpha))
            self.case_fill.meshDataChanged()
        self.grade_gates.setData(color=rgba(self.paint_color('chart_gate'), .95 * opacity))
        self.grade_gate_labels.set_opacity(opacity)
        self.case3_overlay.set_palette(palette, opacity)
        self.case1_marshal.set_palette(palette, opacity)
        self.update()

    def set_procedure_opacity(self, opacity):
        if self.procedure_opacity != opacity:
            self.procedure_opacity = opacity
            self.set_procedure_colors(self.procedure_colors)

    def set_procedure_line_width(self, width):
        for item in (self.case_lines, self.grade_gates, *self.case1_marshal.items, *self.case3_overlay.items):
            if isinstance(item, ProcedureLines):
                item.thickness = width
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'guide_layers'):
            self.refresh_grid()
        if hasattr(self,'north_indicator'):
            self.update_compass()
        if hasattr(self, 'deck_crew'):
            self.deck_crew.sync_visibility(time.monotonic())
            self.update_focus_cursor()
        for overlay in getattr(self, 'text_overlays', ()):
            overlay.setGeometry(self.rect())
        if hasattr(self, 'scale_bar'):
            self.scale_bar.refresh()

    def projectionMatrix(self, region, viewport):
        if self.is_3d:
            return super().projectionMatrix(region, viewport)
        # Genuine orthographic top-down projection; zoom has no perspective distortion.
        _, _, w, h = viewport
        aspect = h / max(w, 1)
        m = QMatrix4x4()
        m.ortho(-self.span / 2, self.span / 2,
                -self.span * aspect / 2, self.span * aspect / 2,
                -max(self.span * 20, 100000.0), max(self.span * 20, 100000.0))
        return m

    def set_3d(self, enabled):
        self.is_3d = enabled
        if not enabled:
            self.holidays.set_frame(None)
        self.apply_camera()
        self.setFocus()

    def home_to_carrier(self,instance):
        self.deck_crew.following = False
        position=self.carrier_positions.get(instance)
        if position is None:return
        self.follow_id=None
        self.follow_flight_name = None
        self.focus_snap = None
        self.opts['center']=QVector3D(position[0],position[1],0)
        self.apply_camera()

    def reset_camera(self):
        self.deck_crew.following = False
        self.follow_id = None
        self.follow_flight_name = None
        self.north_up = True;self.carrier_up=False
        self.focus_snap = None
        self.opts['center'] = QVector3D(0, 0, 0)
        self.span, self.heading, self.tilt = 12 * 1852.0, -90.0+self.reference_heading_deg, 40.0
        self.apply_camera()

    def toggle_orientation(self):
        if self.north_up and self.carrier_heading_deg is not None:
            self.north_up=False;self.carrier_up=True
        else:self.north_up=True;self.carrier_up=False
        self.apply_camera()

    def apply_camera(self):
        if self.north_up:self.heading=-90.+self.reference_heading_deg
        elif self.carrier_up and self.carrier_heading_deg is not None:self.heading=-90.+self.reference_heading_deg-self.carrier_heading_deg
        self.deck_crew.sync_visibility(time.monotonic())
        crew_position = self.deck_crew.positions.get(self.deck_crew.selected_id)
        if self.deck_crew.following and crew_position is not None:
            self.opts['center'] = QVector3D(*crew_position)
        elif self.is_3d and self.follow_flight_name is not None:
            self.opts['center'].setZ(max(0., self.flight_follow_altitude + self.follow_height_offset))
        elif self.is_3d and (position := self.followed_aircraft_position()) is not None:
            self.opts['center'].setZ(max(0., position[2] + self.follow_height_offset))
        else:
            self.opts['center'].setZ(max(0., self.opts['center'].z()) if self.is_3d else 0.)
        self.snap_camera_to_deck()
        self.setCameraPosition(distance=self.span / (2 * math.tan(math.radians(30))),
                               elevation=self.tilt if self.is_3d else 90,
                               azimuth=self.heading)
        self.deck_crew.labels.refresh()
        self.refresh_grid()
        self.update_aircraft_glyphs()
        self.update_wire_glyphs()
        self.update_carrier_numbers()
        self.update_selection()
        self.update_focus_cursor()
        self.update_compass()
        self.scale_bar.refresh()
        for overlay in self.text_overlays:
            overlay.update()
        self.camera_changed.emit(
            f"{'3D' if self.is_3d else '2D'}  ·  {self.label_units.distance(self.span):.1f} {self.label_units.distance_symbol} across  ·  "
            f"{self.guide_style} {self.label_units.procedure_text(self.guide_label+' NM')}  ·  {self.approach_case}  ·  Pivot {self.label_units.altitude(self.opts['center'].z()):.0f} {self.altitude_unit_symbol} ASL")

    def has_free_3d_camera(self):
        return (self.is_3d and self.follow_id is None and self.follow_flight_name is None
                and not self.deck_crew.following and self.preview_palette is None)

    def snap_camera_to_deck(self):
        snap, self.focus_snap = self.focus_snap, None
        self.focus_floor = 0.
        if not self.has_free_3d_camera():
            return
        center = self.opts['center']
        x, y, z = center.x(), center.y(), center.z()
        if snap is not None and z == snap[0]:
            z = snap[1]
        for triangle in self.focus_deck_faces:
            height = triangle[0][2]
            if height > self.focus_floor and deck_height_at((x, y, height), (triangle,)) is not None:
                self.focus_floor = height
        center.setZ(max(z, self.focus_floor))
        if center.z() > z:
            self.focus_snap = center.z(), z

    def update_focus_cursor(self):
        free = self.has_free_3d_camera()
        self.focus_disk.setVisible(free)
        if not free:
            self.focus_altitude_line.hide()
            return
        center = self.opts['center']
        x, y, z = center.x(), center.y(), center.z()
        radius = 4 * self.pixelSize(center) if self.width() else 0.
        color = self.paint_color('accent')
        floor = self.focus_floor
        above_floor = z - floor > .001
        self.focus_altitude_line.setVisible(above_floor)
        key = (x, y, z, radius, color, floor)
        if key == self.focus_cursor_key:
            return
        self.focus_cursor_key = key
        transform = QMatrix4x4()
        transform.translate(x, y, z)
        transform.scale(radius, radius, 1.)
        self.focus_disk.setTransform(transform)
        self.focus_disk.setColor(rgba(color))
        if above_floor:
            self.focus_altitude_line.setData(pos=np.array([(x, y, floor), (x, y, z)], dtype=np.float32),
                                             color=rgba(color, .55))

    def set_guides(self, style=None, spacing=None, visible=None):
        if style is not None:
            self.guide_style = style
        if spacing is not None:
            self.guide_spacing = max(.1, float(spacing))
        if visible is not None:
            self.guides_visible = visible
        self.refresh_grid()
        self.apply_camera()

    def update_carrier_numbers(self):
        if not hasattr(self, 'carrier_numbers'):
            return
        camera = self.cameraPosition()
        faces = tuple(i for i, (center, normal, _) in enumerate(self.carrier_number_faces)
                      if self.is_3d and ((camera.x() - center[0]) * normal[0]
                                         + (camera.y() - center[1]) * normal[1]) > 0)
        self.carrier_numbers.setVisible(bool(faces))
        if faces and faces != self.visible_number_faces:
            vertices = [p for i in faces for p in self.carrier_number_faces[i][2]]
            self.carrier_numbers.setData(pos=np.asarray(vertices, dtype=np.float32),
                                         color=rgba(self.paint_color('text')))
        self.visible_number_faces = faces

    def refresh_grid(self):
        # Cache geometry across zoom/pan ranges. Camera rotation only changes matrices.
        steps = levels(self.guide_spacing)
        span_nm = self.span / NM
        bucket = 2 ** math.ceil(math.log2(max(span_nm, .1))) * NM
        cell = bucket / 2
        center = self.opts['center']
        cx, cy = round(center.x()/cell)*cell, round(center.y()/cell)*cell
        aspect = max(1, self.height()/max(self.width(), 1))
        reach = bucket * (4 if self.is_3d else 1.5) * aspect
        visible_steps = []
        alphas = weights(span_nm, steps)
        for index, item in enumerate(self.guide_layers):
            if index >= len(steps):
                item.setVisible(False)
                continue
            step_nm = steps[index]
            coarse = None
            alpha = alphas[index]
            item.setVisible(self.guides_visible and alpha > .001)
            if alpha > .001:
                visible_steps.append(step_nm)
            if alpha <= .001 or not self.guides_visible:
                continue
            key = (self.guide_style, step_nm, coarse, bucket, cx, cy, round(aspect, 2), self.is_3d, self.width()//128)
            if self.guide_keys.get(index) != key:
                item.setData(pos=geometry(self.guide_style, step_nm*NM, coarse,
                    (cx, cy), reach, self.width(), bucket))
                self.guide_keys[index] = key
            if self.guide_alphas.get(index) != alpha:
                item.setData(color=rgba(self.paint_color('guide'),self.paint_color('guide_alpha')*alpha))
                self.guide_alphas[index] = alpha
        finest = visible_steps[0] if visible_steps else steps[-1]
        self.grid_step = finest*NM
        self.guide_label = f'{finest:g}'
        if len(visible_steps)>1 and opacity(span_nm, finest) < .999:
            self.guide_label += f' → {visible_steps[1]:g}'

    def move_ground(self, dx, dy):
        if dx or dy:
            self.deck_crew.following = False
            self.follow_id = None
            self.follow_flight_name = None
        a = math.radians(self.heading)
        right = QVector3D(-math.sin(a), math.cos(a), 0)
        up = QVector3D(-math.cos(a), -math.sin(a), 0)
        self.opts['center'] += right * dx + up * dy

    def keyPressEvent(self, event):
        if self.vector_mode and event.key() == Qt.Key.Key_Escape:
            self.set_vector_mode(False)
            event.accept()
            return
        allowed = {Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D,
                   Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_R, Qt.Key.Key_F}
        if event.key() not in allowed:
            return super().keyPressEvent(event)
        if not event.isAutoRepeat():
            self.keys.add(event.key())
            if not self.timer.isActive():
                self.last_tick = time.perf_counter()
                self.timer.start()
        event.accept()

    def keyReleaseEvent(self, event):
        if not event.isAutoRepeat():
            self.keys.discard(event.key())
            if not self.keys:
                self.timer.stop()
        event.accept()

    def focusOutEvent(self, event):
        self.keys.clear()
        self.timer.stop()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.set_vector_mode(False)
        self.keys.clear()
        self.timer.stop()
        self.redraw_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.redraw_timer.start()

    def refresh_visible_map(self):
        if not self.window().isMinimized():
            self.holidays.tick(time.monotonic())
        if not self.playback_paused and not self.window().isMinimized():
            if self.deck_crew.enabled:
                self.deck_crew.tick(time.monotonic())
            if self.carrier_flag_frames and self.is_3d and self.carrier_flag:
                now = time.perf_counter()
                elapsed = now - self.flag_updated_at
                if elapsed >= .04:
                    self.flag_time += min(elapsed, .05)
                    self.flag_updated_at = now
                    self.update_carrier_flags()
            self.update()

    def update_carrier_flags(self):
        if not self.carrier_flag_frames or not self.is_3d or not self.carrier_flag:
            self.carrier_flags.setVisible(False)
            return
        positions, colors, palette = [], [], {}
        banana = theme.CURRENT_THEME == 'Banana' and self.preview_palette is None
        for project, deck_z, wind in self.carrier_flag_frames:
            for a, b, role in tower_flag(project, deck_z, wind, self.flag_time, banana=banana):
                if role not in palette:
                    palette[role] = rgba(self.paint_color(role))
                positions.extend((a, b))
                colors.extend((palette[role],) * 2)
        self.carrier_flags.setData(pos=np.asarray(positions, dtype=np.float32),
                                   color=np.asarray(colors, dtype=np.float32))
        self.carrier_flags.setVisible(True)

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last_tick, .05)
        self.last_tick = now
        k = self.keys
        x = int(Qt.Key.Key_D in k) - int(Qt.Key.Key_A in k)
        y = int(Qt.Key.Key_W in k) - int(Qt.Key.Key_S in k)
        norm = max(math.hypot(x, y), 1)
        self.move_ground(x / norm * self.span * dt * .45,
                         y / norm * self.span * dt * .45)
        if Qt.Key.Key_E in k or Qt.Key.Key_Q in k:self.north_up=False;self.carrier_up=False
        self.heading += (int(Qt.Key.Key_E in k) - int(Qt.Key.Key_Q in k)) * dt * 65
        if self.is_3d:
            self.tilt = max(5, min(90, self.tilt +
                (int(Qt.Key.Key_R in k) - int(Qt.Key.Key_F in k)) * dt * 45))
        self.apply_camera()

    def set_vector_mode(self, enabled):
        if self.vector_mode == enabled:
            return
        self.vector_mode = enabled
        if enabled:
            self.setFocus()
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.marshal_vectors.cancel()
            self.unsetCursor()
        self.vector_mode_changed.emit(enabled)

    def clear_marshal_vectors(self):
        self.marshal_vectors.clear()
        self.set_vector_mode(False)

    def mousePressEvent(self, event):
        self.setFocus()
        self.last_mouse = event.position()
        self.press_position = event.position()
        self.click_dragged = False
        self.compass_pressed = event.button()==Qt.MouseButton.LeftButton and self.north_indicator.contains(event.position())
        if self.vector_mode and event.button() == Qt.MouseButton.LeftButton and not self.compass_pressed:
            self.marshal_vectors.begin(event.position(), self.selected_id)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if not self.vector_mode and event.button() == Qt.MouseButton.LeftButton and not self.north_indicator.contains(event.position()):
            instance = self.pick_at(event.position())
            if instance is not None:
                self.press_position = None
                self.aircraft_double_clicked.emit(instance)
                event.accept()
                return
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.compass_pressed:
            event.accept()
            return
        if not hasattr(self, 'last_mouse'):
            self.last_mouse = event.position()
        d = event.position() - self.last_mouse
        self.last_mouse = event.position()
        if self.vector_mode:
            if event.buttons() & Qt.MouseButton.RightButton:
                metres = self.span / max(self.width(), 1)
                self.move_ground(-d.x() * metres, d.y() * metres)
                self.apply_camera()
            elif event.buttons() & Qt.MouseButton.LeftButton:
                self.marshal_vectors.move(event.position())
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self.press_position is not None and (event.position()-self.press_position).manhattanLength()>5:
                self.click_dragged = True
            if not self.click_dragged:
                return
            self.follow_id = None
            metres = self.span / max(self.width(), 1)
            self.move_ground(-d.x() * metres, d.y() * metres)
        elif event.buttons() & Qt.MouseButton.RightButton:
            if self.press_position is not None and (event.position()-self.press_position).manhattanLength()>5:
                self.click_dragged = True
            if d.x():self.north_up=False;self.carrier_up=False
            self.heading += d.x() * .3
            if self.is_3d:
                self.tilt = max(5, min(90, self.tilt + d.y() * .3))
        else:
            return
        self.apply_camera()
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.compass_pressed:
            if self.press_position is not None and (event.position()-self.press_position).manhattanLength()<=5:
                self.toggle_orientation()
            self.compass_pressed=False
            self.press_position=None
            event.accept()
            return
        if self.vector_mode and event.button() == Qt.MouseButton.LeftButton:
            dragged = self.press_position is not None and (event.position() - self.press_position).manhattanLength() > 5
            self.press_position = None
            if self.marshal_vectors.finish(event.position(), dragged):
                self.set_vector_mode(False)
            event.accept()
            return
        if event.button()==Qt.MouseButton.LeftButton and self.press_position is not None:
            if not self.click_dragged and (event.position()-self.press_position).manhattanLength()<=5:
                self.aircraft_clicked.emit(self.pick_at(event.position()))
        self.press_position=None
        event.accept()

    def project_point(self,point):
        width,height=self.width(),self.height()
        if width<=0 or height<=0:
            return None
        viewport=(0,0,width,height)
        matrix=self.projectionMatrix(viewport,viewport)*self.viewMatrix()
        row=matrix.row(3)
        w=row.x()*point[0]+row.y()*point[1]+row.z()*point[2]+row.w()
        if w<=0:
            return None
        projected=matrix.map(QVector3D(*point))
        x,y,z=projected.x(),projected.y(),projected.z()
        if not -1<=z<=1:
            return None
        return ((x+1)*width*.5,(1-y)*height*.5,z)

    def pick_at(self,position,radius=20):
        candidates=[]
        for instance,point,color in self.pick_points:
            projected=self.project_point(point)
            if projected is None:
                continue
            x,y,depth=projected
            if not (0<=x<=self.width() and 0<=y<=self.height()):
                continue
            distance=math.hypot(x-position.x(),y-position.y())
            if distance<=radius:
                candidates.append((distance,depth,instance))
        return min(candidates,key=lambda item:(item[0],item[1]))[2] if candidates else None

    def color_for(self,aircraft,sample):
        color = self.flight_colors.get(sample['source'],aircraft)
        if aircraft.get('_radar_stale'):
            background = rgba(self.paint_color('map'))
            return tuple(a * .45 + b * .55 for a, b in zip(color, background))
        return color

    def show_radar_sweep(self, coverage, seconds=0.):
        if not coverage or self.radar_transform is None or not self.radar_opacity:
            self.sweep_lines.setVisible(False)
            self.radar_outline.hide()
            self.radar_labels.hide()
            return
        search, par, center = coverage
        vertices, colors = [], []
        radar = rgba(self.paint_color('radar'))

        def polar_point(origin, angle, radius):
            angle = math.radians(angle)
            return self.radar_transform(
                [origin[0] + radius * math.sin(angle), origin[1], origin[2] + radius * math.cos(angle)], 0.)

        def segment(a, b, opacity, end_opacity=None):
            vertices.extend((a, b))
            end_opacity = opacity if end_opacity is None else end_opacity
            colors.extend(((*radar[:3], opacity * self.radar_opacity),
                           (*radar[:3], end_opacity * self.radar_opacity)))

        def radial(origin, angle, radius, opacity):
            distances = (.15 * NM, .25 * NM, .99 * radius, radius)
            points = [polar_point(origin, angle, distance) for distance in distances]
            for a, b, lo, hi in zip(points, points[1:], (0., opacity, opacity), (opacity, opacity, 0.)):
                segment(a, b, lo, hi)

        show_par = self.radar_par_visible and center is not None
        key = (polar_point(search, 0, 0), polar_point(par, 0, 0), self.reference_heading_deg,
               center, self.radar_search_visible, show_par, self.radar_opacity, radar)
        if key != self.radar_outline_key:
            self.radar_outline_key = key
            anchors = []

            def dashed_arc(origin, radius, low, high, steps, opacity):
                step = (high - low) / steps
                for index in range(steps):
                    start = low + index * step
                    segment(polar_point(origin, start, radius), polar_point(origin, start + step * .6, radius), opacity)

            if self.radar_search_visible:
                dashed_arc(search, SEARCH_MAX_RANGE, 0., 360., 120, .28)
                for angle in range(0, 360, 45):
                    anchors.append(('Surveillance Radar', polar_point(search, angle - 1, SEARCH_MAX_RANGE),
                                    polar_point(search, angle + 1, SEARCH_MAX_RANGE)))
            if show_par:
                low, high = center - PAR_HALF_ANGLE, center + PAR_HALF_ANGLE
                dashed_arc(par, PAR_RANGE, low, high, 24, .28)
                for edge in (low, high):
                    for start in np.linspace(.15 * NM, .99 * PAR_RANGE, 32, endpoint=False):
                        end = start + PAR_RANGE / 32 * .6
                        segment(polar_point(par, edge, start), polar_point(par, edge, end),
                                .35 * min(1., (start / NM - .15) / .1), .35)
                    for fraction in (.7, .45, .9):
                        anchors.append(('PAR', polar_point(par, edge, (fraction - .02) * PAR_RANGE),
                                        polar_point(par, edge, (fraction + .02) * PAR_RANGE)))
            self.radar_outline.setData(pos=np.asarray(vertices, dtype=np.float32).reshape(-1, 3),
                                       color=np.asarray(colors, dtype=np.float32).reshape(-1, 4))
            self.radar_labels.anchors = anchors
            self.radar_labels.update()
            vertices.clear()
            colors.clear()
        outline_visible = self.radar_search_visible or show_par
        self.radar_outline.setVisible(outline_visible)
        self.radar_labels.setVisible(outline_visible)
        if self.radar_search_visible:
            radial(search, search_bearing(seconds), SEARCH_MAX_RANGE, .85)
        if show_par and self.radar_par_sweep_visible:
            radial(par, center + par_offset(seconds), PAR_RANGE, .85)
        if vertices:
            self.sweep_lines.setData(pos=np.asarray(vertices, dtype=np.float32),
                                     color=np.asarray(colors, dtype=np.float32))
        self.sweep_lines.setVisible(bool(vertices))
        self.update()

    def set_selection(self,instance):
        self.selected_id=instance
        self.aircraft_labels.selected_id=instance
        match=next((item for item in self.pick_points if item[0]==instance),None)
        self.selection_point=match[1] if match else None
        if match:
            self.selection_color=match[2]
        self.glyph_camera_key=None
        self.update_aircraft_glyphs()
        self.update_selection()
        self.update_focus_cursor()
        self.update()

    def focus_crew(self, crew_id):
        crew = self.deck_crew
        if not self.is_3d or crew.model is None or not any(p['id'] == crew_id for p in crew.model.people):
            return
        self.span = 35.
        crew.sync_visibility(time.monotonic())
        if crew_id not in self.deck_crew.positions:
            self.apply_camera()
            return
        self.follow_id = self.follow_flight_name = None
        self.deck_crew.selected_id = crew_id
        self.deck_crew.following = True
        self.apply_camera()

    def follow_aircraft(self, instance):
        self.deck_crew.selected_id = None
        self.deck_crew.following = False
        self.follow_flight_name = None
        self.set_selection(instance)
        if instance != self.follow_id:
            self.follow_height_offset = 0.
        self.follow_id = instance
        self.update_follow()
        self.apply_camera()

    def flight_bounds(self, instances, axes):
        points = [position for instance, position, _ in self.pick_points if instance in instances]
        if not points:
            return None
        positions = np.asarray(points)
        screen = positions[:, :2] @ axes.T
        midpoint = (screen.min(axis=0) + screen.max(axis=0)) / 2
        heights = positions[:, 2]
        altitude = max(0., (heights.min() + heights.max()) / 2)
        return midpoint @ axes, altitude, np.abs(screen - midpoint), heights

    def follow_flight(self, name, instances):
        self.deck_crew.following = False
        self.deck_crew.selected_id = None
        angle = math.radians(self.heading + 90.)
        c, s = math.cos(angle), math.sin(angle)
        axes = np.array(((c, s), (-s, c)))
        bounds = self.flight_bounds(instances, axes)
        if bounds is None:
            return
        (x, y), altitude, offsets, heights = bounds
        dx, dy = offsets.T
        aspect = max(1, self.width()) / max(1, self.height())
        # Fit once at snap time. Later camera changes belong to the user.
        half_span = np.maximum(dx, dy * aspect) / .8
        if self.is_3d:
            self.tilt = 90.
            half_span = np.maximum(half_span, 92.6) + (heights - altitude) * math.tan(math.radians(30))
        self.span = max(185.2, 2 * half_span.max())
        self.follow_id = None
        self.follow_flight_name = name
        self.flight_follow_axes = axes
        self.flight_follow_altitude = altitude
        self.follow_height_offset = 0.
        self.opts['center'] = QVector3D(x, y, 0.)
        self.apply_camera()

    def followed_aircraft_position(self):
        if self.follow_id is None:
            return None
        return next((p for instance, p, _ in self.pick_points if instance == self.follow_id), None)

    def update_follow(self, aircraft=()):
        if self.follow_flight_name is not None:
            members = {a['instance_id'] for a in aircraft if flight_identity(a)[0] == self.follow_flight_name}
            bounds = self.flight_bounds(members, self.flight_follow_axes)
            if bounds is not None:
                (x, y), self.flight_follow_altitude, _, _ = bounds
                self.opts['center'] = QVector3D(x, y, 0.)
        elif (p := self.followed_aircraft_position()) is not None:
            self.opts['center']=QVector3D(p[0],p[1],max(0., p[2] + self.follow_height_offset) if self.is_3d else 0)

    def approach_color(self,instance,base):
        return status_color(base,self.approach_states.get(instance),self.warning_phase,self.paint_color('#FF626A'))

    def set_approach_states(self,states,phase,force=False,repaint=True):
        if not force and states is self.approach_states and phase==self.warning_phase:return
        self.approach_states,self.warning_phase=states,phase
        if not repaint or not self.isVisible() or self.is_3d:return
        self.marker_specs=[(i,p,d,self.glyph_base_colors.get(i,c),gear,hook)
                           for i,p,d,c,gear,hook in self.marker_specs]
        self.radar_specs=[(i,p,h,self.glyph_base_colors.get(i,c)) for i,p,h,c in self.radar_specs]
        self.glyph_revision+=1;self.glyph_camera_key=None
        self.update_aircraft_glyphs();self.update()

    def update_aircraft_glyphs(self):
        if not hasattr(self,'aircraft_mesh'):
            return
        key=(self.glyph_revision,self.span,self.width(),self.selected_id,self.is_3d,self.icon_scale_2d,self.icon_scale_3d,self.devicePixelRatioF())
        if self.is_3d:
            c=self.opts['center']
            key+=(self.heading,self.tilt,c.x(),c.y(),c.z())
        if key==self.glyph_camera_key:
            return
        self.glyph_camera_key=key
        vertices=[];faces=[];face_colors=[];edges=[];edge_colors=[]
        if not self.is_3d:
            # Heading-only radar symbols: bank and pitch do not distort plan-view marks.
            scale=self.span/max(self.width(),1)*self.icon_scale_2d*1.33*1.35
            radius = 3.5 * scale
            half_stroke = 1.5 * self.span / max(self.width() * self.devicePixelRatioF(), 1)
            for instance,position,heading,color in self.radar_specs:
                center=np.asarray(position)
                state=self.approach_states.get(instance)
                if state and (state.get('band') or state.get('warnings')):
                    # Fill alone carries approach status; rim and heading remain flight-colored.
                    inner=max(0,radius-half_stroke)
                    base=len(vertices);vertices.append(center)
                    vertices.extend(center+RADAR_CIRCLE[::2]*inner)
                    fill=self.approach_color(instance,color)
                    for j in range(len(RADAR_CIRCLE)//2):
                        faces.append((base,base+1+j,base+1+(j+1)%(len(RADAR_CIRCLE)//2)))
                        face_colors.append(fill)
                edges.extend(center+RADAR_CIRCLE*radius)
                edge_colors.extend([color]*len(RADAR_CIRCLE))
                if heading is not None:
                    angle=math.radians(heading);direction=np.array((math.sin(angle),math.cos(angle),0))
                    edges.extend((center+direction*radius,center+direction*(radius+7*scale)))
                    edge_colors.extend((color,color))
            for i in range(0,len(edges),2):
                a,b=np.asarray(edges[i]),np.asarray(edges[i+1])
                delta=b-a;length=math.hypot(delta[0],delta[1])
                if length<1e-12:continue
                side=np.array((-delta[1],delta[0],0))/length*half_stroke
                base=len(vertices)
                vertices.extend((a-side,a+side,b-side,b+side))
                faces.extend(((base,base+1,base+2),(base+1,base+3,base+2)))
                face_colors.extend((edge_colors[i],edge_colors[i]))
            edges=[];edge_colors=[]
        for instance,position,directions,color,gear_down,hook_down in self.marker_specs if self.is_3d else []:
            if directions is None:
                continue  # Missing native orientation is not replaced by an invented attitude.
            metres_per_pixel=self.pixelSize(QVector3D(*position)) if self.is_3d else self.span/max(self.width(),1)
            detail = detailed_glyph(self.airframe_keys.get(instance), directions,
                                    self.icon_scale_3d * 1.25 * 1.35, metres_per_pixel,
                                    gear_down=gear_down, hook_down=hook_down)
            if detail is not None:
                shape = np.asarray(position) + detail
                deck_height = self.aircraft_deck_heights.get(instance)
                if gear_down and deck_height is not None:
                    shape[:, 2] += max(0., deck_height + .02 - shape[:, 2].min())
                edges.extend(shape)
                edge_colors.extend([color] * len(detail))
                continue
            length=metres_per_pixel*self.icon_scale_3d*1.25*1.35*SIZE_SCALE*(28 if instance==self.selected_id else 23)
            arrow=np.asarray(position)+directions*length
            base=len(vertices)
            vertices.extend(arrow)
            faces.extend(FACES+base)
            fin_color=tuple(.65*component+.35 for component in color[:3])+(color[3],)
            face_colors.extend((color,fin_color))
            for a,b in OUTLINE_EDGES:
                edges.extend((arrow[a],arrow[b]))
                edge_color=color if a<3 else fin_color
                edge_colors.extend((edge_color,edge_color))
        self.aircraft_mesh.setVisible(bool(vertices))
        self.arrow_outline.setVisible(bool(edges))
        if vertices:
            self.aircraft_mesh.setMeshData(vertexes=np.asarray(vertices,dtype=np.float32),
                faces=np.asarray(faces,dtype=np.uint32),faceColors=np.asarray(face_colors,dtype=np.float32))
        if edges:
            self.arrow_outline.setData(pos=np.asarray(edges,dtype=np.float32),
                color=np.asarray(edge_colors,dtype=np.float32))

    def update_compass(self):
        if not hasattr(self,'north_indicator'):
            return
        north,_=compass_axes(self.reference_heading_deg)
        projected=self.viewMatrix().mapVector(QVector3D(*north))
        self.north_indicator.set_direction(projected.x(),-projected.y())

    def update_wire_glyphs(self):
        if not hasattr(self,'wire_mesh'):
            return
        key=(self.glyph_revision,self.span,self.width(),self.height(),self.heading,self.tilt,self.is_3d,self.devicePixelRatioF())
        if key==self.wire_camera_key:
            return
        self.wire_camera_key=key
        vertices=[];faces=[];colors=[]
        matrix=self.viewMatrix()
        r,u=matrix.row(0),matrix.row(1)
        right=np.array((r.x(),r.y(),r.z()))
        up=np.array((u.x(),u.y(),u.z()))
        half_width=self.span/max(1,self.width()*self.devicePixelRatioF())
        for a,b,color,pixels in self.wire_specs:
            a,b=np.asarray(a),np.asarray(b)
            delta=b-a
            sx,sy=float(np.dot(delta,right)),float(np.dot(delta,up))
            length=math.hypot(sx,sy)
            if length<1e-9:continue
            offset=(-sy*right+sx*up)/length*half_width*(pixels/2)
            i=len(vertices)
            vertices.extend((a-offset,a+offset,b-offset,b+offset))
            faces.extend(((i,i+1,i+2),(i+1,i+3,i+2)))
            colors.extend((color,color))
        self.wire_mesh.setVisible(bool(faces))
        if faces:
            self.wire_mesh.setMeshData(vertexes=np.asarray(vertices,dtype=np.float32),
                faces=np.asarray(faces,dtype=np.uint32),faceColors=np.asarray(colors,dtype=np.float32))

    def update_selection(self):
        if not hasattr(self,'selection_lines'):
            return
        p=self.selection_point
        self.selection_lines.setVisible(p is not None)
        if p is None:
            return
        north,east=compass_axes(self.reference_heading_deg)
        extent=self.span*max(2.,self.height()/max(self.width(),1)*2)
        points=[]
        for direction in (east,north):
            points.extend(tuple(p[i]+sign*direction[i]*extent for i in range(3)) for sign in (-1,1))
        if (self.is_3d and getattr(self,'sea_level_known',False)
                and self.selected_id not in self.aircraft_deck_heights):
            points.extend((p,(p[0],p[1],0)))
        self.selection_lines.setData(pos=np.asarray(points,dtype=np.float32),
                                    color=rgba(self.paint_color('selection'),.5))

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if self.is_3d and steps:
                delta = steps * max(5., min(500., self.span * .03))
                center = self.opts['center']
                height = max(0., center.z() + delta)
                self.focus_snap = None
                if self.follow_flight_name is not None:
                    self.follow_height_offset = height - self.flight_follow_altitude
                elif (position := self.followed_aircraft_position()) is not None:
                    self.follow_height_offset = height - position[2]
                center.setZ(height)
                self.apply_camera()
        else:
            self.span = max(5., min(1000 * 1852, self.span * math.exp(-steps * .14)))
            self.apply_camera()
        event.accept()

    def paintGL(self):
        super().paintGL()
        self.frames += 1

    def clear_telemetry(self):
        self.deck_aircraft_count = 0
        self.radar_transform = None
        self.holidays.set_frame(None)
        self.deck_crew.clear()
        self.show_radar_sweep(None)
        self.marshal_vectors.aircraft.clear()
        self.trail_cache.clear()
        self.carrier_positions={}
        self.follow_id = None
        self.follow_flight_name = None
        self.wire_specs=[]
        self.wire_camera_key=None
        self.pick_points=[]
        self.marker_specs=[]
        self.airframe_keys.clear()
        self.aircraft_deck_heights.clear()
        self.focus_deck_faces = []
        self.rtb_label_values.clear()
        self.radar_specs=[]
        self.aircraft_labels.set_records([])
        self.aircraft_centers=np.empty((0,3),dtype=np.float32)
        self.glyph_revision+=1
        self.selection_point=None
        self.selection_lines.setVisible(False)
        self.case_lines.setVisible(False)
        self.case_fill.setVisible(False)
        self.case3_overlay.hide()
        self.case1_marshal.hide()
        self.grade_gates.hide()
        self.grade_gate_labels.hide()
        self.reference_heading_deg=0.;self.carrier_heading_deg=None
        self.update_compass()
        if not hasattr(self, 'aircraft_mesh'):
            self.aircraft_mesh=GLMeshItem(smooth=False,glOptions='opaque',drawEdges=False)
            self.aircraft_mesh.setDepthValue(5)
            self.arrow_outline=MapLines(glOptions=MAP_LINE_OPTIONS,mode='lines',antialias=False,width=1)
            self.arrow_outline.setDepthValue(110)
            self.telemetry_lines=MapLines(glOptions=MAP_LINE_OPTIONS,mode='lines',antialias=False,width=2)
            self.telemetry_lines.setDepthValue(4)
            self.carrier_deck_lines = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines',
                                               color=rgba(self.paint_color('carrier'), .9), width=1)
            self.carrier_deck_lines.setDepthValue(105)
            self.addItem(self.carrier_deck_lines)
            self.carrier_deck_mask = DeckDepthMask(smooth=False, glOptions='opaque', drawEdges=False)
            # Run the carrier-only depth pass before the aircraft mesh at 5.
            self.carrier_deck_mask.setDepthValue(2)
            self.carrier_hull = CarrierHullLines(glOptions={**MAP_LINE_OPTIONS, GL_DEPTH_TEST: True},
                                               mode='lines', color=rgba(self.paint_color('carrier'), .9), width=1)
            self.carrier_hull.setDepthValue(3)
            self.addItem(self.carrier_deck_mask)
            self.addItem(self.carrier_hull)
            self.carrier_details = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
            self.carrier_details.setDepthValue(106)
            self.addItem(self.carrier_details)
            self.carrier_flags = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
            self.carrier_flags.setDepthValue(106)
            self.addItem(self.carrier_flags)
            self.carrier_numbers = MapLines(glOptions=MAP_LINE_OPTIONS, mode='lines', width=1)
            self.carrier_numbers.setDepthValue(107)
            self.addItem(self.carrier_numbers)
            self.altitude_lines=MapLines(glOptions=MAP_LINE_OPTIONS,mode='lines',antialias=False,width=2)
            self.wire_mesh=GLMeshItem(smooth=False,glOptions='translucent',drawEdges=False)
            self.wire_mesh.setDepthValue(120)
            self.trail_lines=TrailLines(glOptions={**MAP_LINE_OPTIONS, GL_DEPTH_TEST: True},
                                            mode='lines',antialias=False,width=2)
            self.trail_lines.setDepthValue(2.5)  # Respect the deck mask before the hull clears depth.
            for item in (self.aircraft_mesh,self.arrow_outline,self.telemetry_lines,self.altitude_lines,self.trail_lines,self.wire_mesh):
                self.addItem(item)
        self.carrier_deck_mask.setVisible(False)
        self.carrier_hull.setVisible(False)
        self.carrier_details.setVisible(False)
        self.carrier_flag_frames = []
        self.carrier_flags.setVisible(False)
        self.carrier_number_faces = []
        self.visible_number_faces = None
        self.carrier_numbers.setVisible(False)
        self.aircraft_mesh.setVisible(False)
        self.wire_mesh.setVisible(False)
        self.arrow_outline.setVisible(False)
        empty = np.empty((0, 3), dtype=np.float32)
        self.telemetry_lines.setData(pos=empty)
        self.carrier_deck_lines.setData(pos=empty)
        self.altitude_lines.setData(pos=empty)
        self.trail_lines.setData(pos=empty)
        self.snap_camera_to_deck()
        self.update_focus_cursor()

    def update_grade_gates(self, volume, sea_offset, transform, rules):
        visible = volume is not None and self.procedure_visibility['gates']
        self.grade_gates.setVisible(visible)
        self.grade_gate_labels.setVisible(visible and self.procedure_labels_visible)
        if not visible:
            return

        case = self.approach_case
        color = self.paint_color('chart_gate')
        key = (case, self.is_3d, transform(volume['origin']), self.reference_heading_deg,
               volume['runway_heading_deg'], sea_offset, rules[case], rules['targets'],
               color, self.label_units.distance_name)
        if key == self.grade_gate_key:
            return
        self.grade_gate_key = key
        dashes, labels = [], []
        for title, distance, corners in grading_gate_sections(volume, case, rules, sea_offset):
            a, b, c, d = np.asarray([transform(p) for p in corners])
            left, right = (a + d) / 2, (b + c) / 2
            sides = ((a, b), (b, c), (c, d), (d, a)) if self.is_3d and a[2] != d[2] else ((left, right),)
            for start, end in sides:
                count = 4 if math.dist(start, end) < 50 else 8
                delta = (end - start) / count
                for i in range(count):
                    dashes.extend((start + i * delta, start + (i + .62) * delta))
            label = self.label_units.procedure_text(f'{title} · {distance:g} NM')
            labels.append((left, label, color, True))
        self.grade_gates.setData(pos=np.asarray(dashes, dtype=np.float32))
        self.grade_gate_labels.set_labels(labels)

    def render_telemetry(self, sample, history, carrier_id=None, locked=False,
                         trails=True, verticals=True, vectors=True, scoring_rules=None, rtb_context=None,
                         live_data=None):
        if not hasattr(self, 'aircraft_mesh'):
            self.clear_telemetry()
        if not sample:
            self.clear_telemetry()
            return
        data_sample = live_data if live_data is not None else sample
        live_aircraft = {a['instance_id']: a for a in live_data['aircraft']} if live_data is not None else {}
        session = sample['capture']['connection_id']
        reference_key = (session, carrier_id if locked else None)
        carriers = [ship for ship in sample['carriers'] if ship.get('cables')]
        selected = next((c for c in carriers if c.get('instance_id') == carrier_id), None)
        if selected is None:
            selected = next(iter(carriers), None)
        stamp = sample['capture']['monotonic_s']
        label_context = (session, selected['instance_id'] if selected else None, rtb_context)
        if label_context != self.rtb_label_context or 'rtb' not in self.label_fields:
            self.rtb_label_values.clear()
            self.rtb_label_context = label_context
        self.carrier_heading_deg=((selected.get('derived') or {}).get('heading_deg') or 0.) if selected else None
        self.reference_heading_deg=self.carrier_heading_deg if locked and selected else 0.
        anchor = selected.get('position_world_m') if selected else None
        if anchor is None and sample['aircraft']:
            anchor = sample['aircraft'][0]['kinematics'].get('position_world_m')
        if anchor is None:
            self.clear_telemetry()
            return
        if getattr(self, 'reference_key', None) != reference_key:
            self.reference_key = reference_key
            correction = (sample.get('derived') or {}).get('world_translation_m', [0, 0, 0])
            self.world_anchor = [anchor[i] + correction[i] for i in range(3)]
            self.focus_snap = None
            self.opts['center'] = QVector3D(0, 0, 0)
        sea_offset = sample.get('_radar_sea_offset')
        for a in sample['aircraft']:
            if sea_offset is not None:
                break
            k = a['kinematics']
            f = k.get('flight_info') or {}
            if k.get('sea_level_world_y_m') is not None:
                sea_offset = -k['sea_level_world_y_m']
                break
            if k.get('position_world_m') and f.get('altitudeASL') is not None:
                sea_offset = f['altitudeASL'] - k['position_world_m'][1]
                break
        self.sea_level_known = sea_offset is not None
        sea_offset = sea_offset or 0

        def transform_for(frame):
            ship = next((c for c in frame['carriers'] if c.get('instance_id') == carrier_id), None) if locked else None
            origin = ship.get('position_world_m') if ship else self.world_anchor
            angle = math.radians(((ship.get('derived') or {}).get('heading_deg') or 0) if ship else 0)
            cosine, sine = math.cos(angle), math.sin(angle)
            correction = [0, 0, 0] if ship else (frame.get('derived') or {}).get('world_translation_m', [0, 0, 0])

            def transform_point(p, altitude=None, aircraft=None):
                # Radar trails use the carrier pose recorded with each contact.
                observation = aircraft.get('_radar_observation') if ship and aircraft else None
                if observation:
                    observed_aircraft, observed_carrier = observation
                    if observed_carrier and observed_carrier['instance_id'] == ship['instance_id']:
                        return transform_for({'carriers': [observed_carrier]})(
                            observed_aircraft['kinematics'].get('position_world_m'),
                            altitude if altitude is not None else p[1] + sea_offset)

                if not isinstance(p, list) or len(p) != 3:
                    return None
                dx, dz = p[0] + correction[0] - origin[0], p[2] + correction[2] - origin[2]
                return (cosine * dx - sine * dz, sine * dx + cosine * dz,
                        altitude if altitude is not None else p[1] + sea_offset)
            return transform_point

        transform = transform_for(sample)
        self.radar_transform = transform if sample.get('_larp_radar') else None
        correction = (sample.get('derived') or {}).get('world_translation_m', [0, 0, 0])
        locked_ship = next((c for c in carriers if c.get('instance_id') == carrier_id), None) if locked else None
        origin = ([locked_ship['position_world_m'][axis] + correction[axis] for axis in (0, 2)]
                  if locked_ship else [self.world_anchor[axis] for axis in (0, 2)])
        heading = ((locked_ship.get('derived') or {}).get('heading_deg') or 0.) if locked_ship else 0.
        carrier_frames = {c['instance_id']: (c['position_world_m'][0] + correction[0],
                          c['position_world_m'][2] + correction[2],
                          math.radians((c.get('derived') or {}).get('heading_deg') or 0.))
                          for c in carriers if point(c.get('position_world_m'))}
        anchor_id = locked_ship['instance_id'] if locked_ship and locked_ship['instance_id'] in carrier_frames else None
        self.marshal_vectors.set_reference(session, origin, heading, data_sample['aircraft'], anchor_id, carrier_frames)

        if selected and self.sea_level_known:
            def transform_procedure(p):
                return transform([p[0],p[1]-sea_offset,p[2]],p[1])
            key=(reference_key,locked,list(self.world_anchor),self.reference_heading_deg,
                 (sample.get('derived') or {}).get('world_translation_m'))
        rules = scoring_rules or DEFAULT_RULES
        final_stop_m = rules[self.approach_case]['end_nm'] * NM
        if self.approach_case=='Case 3' and selected and self.sea_level_known:
            self.case3_overlay.update(selected,sea_offset,self.case1_glide_deg,
                self.case1_runway_offset_deg,self.is_3d,transform_procedure,key,self.label_units,self.procedure_visibility,
                show_labels=self.procedure_labels_visible, final_stop_m=final_stop_m)
        else:self.case3_overlay.hide()
        if self.approach_case=='Case 1' and selected and self.sea_level_known:
            self.case1_marshal.update(selected,sea_offset,self.case1_glide_deg,
                self.case1_runway_offset_deg,transform_procedure,key,self.label_units,self.procedure_visibility,
                show_labels=self.procedure_labels_visible)
        else:self.case1_marshal.hide()

        volume = case1_volume(selected, self.case1_glide_deg, self.case1_runway_offset_deg) if selected else None
        self.update_grade_gates(volume, sea_offset, transform, rules)
        if self.approach_case != 'Case 1' or not self.procedure_visibility['glide']:
            volume = None
        visible = False
        if volume:
            vertices, faces, edges, wall_alpha, edge_alpha = case1_glide_display(volume, final_stop_m, self.is_3d)
            if vertices:
                color = self.procedure_colors['glide']
                opacity = self.procedure_opacity
                self.case_lines.setData(pos=np.asarray([transform(p) for p in edges], dtype=np.float32),
                                        color=vertex_colors(color, .8 * opacity, edge_alpha))
                self.case_fill.setMeshData(vertexes=np.asarray([transform(p) for p in vertices], dtype=np.float32),
                                           faces=np.asarray(faces, dtype=np.uint32),
                                           vertexColors=vertex_colors(color, .1 * opacity, wall_alpha))
                self.case_glide_fade = wall_alpha, edge_alpha
                visible = True
        self.case_lines.setVisible(visible)
        self.case_fill.setVisible(visible)

        points, lines, colors = [], [], []
        self.marker_specs=[]
        self.airframe_keys.clear()
        self.aircraft_deck_heights.clear()
        self.radar_specs=[]
        label_records=[]
        resting=settled_aircraft(data_sample, history if live_data is None else ())
        approach_volume=label_volume(selected,self.case1_glide_deg,self.case1_runway_offset_deg) if self.label_fields else None
        self.glyph_revision+=1
        self.pick_points=[]
        self.selection_point=None
        def segment(a, b, color):
            if a is not None and b is not None:
                lines.extend((a, b))
                colors.extend((color, color))
        self.wire_specs=[]
        self.carrier_positions={}
        deck_vertices, hull_vertices, deck_faces = [], [], []
        crew_deck_faces = []
        self.deck_aircraft_count = 0
        details, detail_colors = [], []
        number_faces = []
        flag_frames = []
        holiday_frame = None
        crew_frame = None
        for ship in carriers:
            p = transform(ship.get('position_world_m'))
            if p is not None:
                self.carrier_positions[ship['instance_id']]=p
                h = (ship.get('derived') or {}).get('heading_deg')
                if h is None:
                    h = heading_from_quaternion(ship.get('rotation_xyzw'))
                reference_h = self.reference_heading_deg if locked and selected and selected.get('instance_id') == carrier_id else 0.
                angle = math.radians((h or 0.) - reference_h)
                deck, hull, mask = carrier_geometry(
                    ship, p, angle, transform, self.is_3d,
                    sea_level=0. if self.sea_level_known else None)
                deck_vertices.extend(vertex for edge in deck for vertex in edge)
                hull_vertices.extend(vertex for edge in hull for vertex in edge)
                deck_faces.extend(mask)
                if ship is selected:
                    crew_deck_faces = mask
                if self.is_3d or (ship is selected and self.deck_crew.enabled):
                    project, deck_z, _ = deck_projection(ship, p, angle, transform)
                    if ship is selected and self.preview_palette is None:
                        crew_frame = ((session, ship['instance_id']), project, deck_z)
                        if self.is_3d:
                            holiday_frame = crew_frame
                if self.is_3d:
                    wind = carrier_wind(data_sample, ship)
                    velocity = ship.get('velocity_world_mps')
                    east, north = (wind[0], wind[2]) if wind is not None else (0., 0.)
                    if point(velocity):
                        east -= velocity[0]
                        north -= velocity[2]
                    c, s = math.cos(math.radians(h or 0.)), math.sin(math.radians(h or 0.))
                    local_wind = c * east - s * north, s * east + c * north
                    number_faces.extend(tower_numbers(project, deck_z, self.carrier_cvn))
                    if self.carrier_flag:
                        flag_frames.append((project, deck_z, local_wind))
                    for a, b, role in tower_details(project, deck_z, local_wind):
                        details.extend((a, b))
                        detail_colors.extend((rgba(self.paint_color(role)),) * 2)
            for cable in ship.get('cables') or []:
                color = rgba(self.paint_color('warning' if cable.get('broken') else ('on'
                    if cable.get('hook_component_id') else 'carrier')))
                a=transform(cable.get('endpoint1_world_m'))
                b=transform(cable.get('endpoint2_world_m'))
                if a is not None and b is not None:
                    self.wire_specs.append((a,b,color,3 if cable.get('broken') or cable.get('hook_component_id') else 2))
        altitude_segments=[];altitude_colors=[]
        for contact in sample['aircraft']:
            a = live_aircraft.get(contact['instance_id'], contact)
            k = a['kinematics']; f = k.get('flight_info') or {}
            contact_k = contact['kinematics']
            reference = locked_ship
            label_aircraft, label_carrier, volume = a, selected, approach_volume
            observation = contact.get('_radar_observation')
            contact_transform = transform
            altitude = (contact_k.get('flight_info') or {}).get('altitudeASL')
            if observation:
                observed_aircraft, carrier_at_hit = observation
                if a is contact:
                    label_aircraft, label_carrier = observation
                    if self.label_fields:
                        volume = label_volume(carrier_at_hit, self.case1_glide_deg, self.case1_runway_offset_deg)
                if locked_ship and carrier_at_hit and carrier_at_hit['instance_id'] == locked_ship['instance_id']:
                    # Use the same carrier pose for position, heading, and motion.
                    # Combining a held aircraft with today's carrier pose causes drift.
                    if a is contact:
                        reference = carrier_at_hit
                    contact_transform = transform_for({'carriers': [carrier_at_hit]})
                    if altitude is None:
                        altitude = contact_k['position_world_m'][1] + sea_offset
                    contact_k = observed_aircraft['kinematics']
            p = contact_transform(contact_k.get('position_world_m'), altitude)
            if p is None:
                continue
            points.append(p)
            flight_color=self.color_for(a,sample)
            self.glyph_base_colors[a['instance_id']]=flight_color
            color=flight_color
            self.pick_points.append((a['instance_id'],p,color))
            if a['instance_id']==self.selected_id:
                self.selection_point=p
                self.selection_color=color
            systems = a.get('systems') or {}
            touching = any(wheel.get('isTouching') is True for wheel in systems.get('wheel_suspensions') or ())
            on_ground = f.get('isLanded') is True or touching or a['instance_id'] in resting
            deck_height = deck_height_at(p, deck_faces) if self.is_3d and on_ground else None
            if deck_height is not None:
                self.aircraft_deck_heights[a['instance_id']] = deck_height
            if self.deck_crew.enabled and on_ground and live_data is None:
                selected_deck = (deck_height if self.is_3d and len(carriers) == 1
                                 else deck_height_at(p, crew_deck_faces))
                if selected_deck is not None:
                    self.deck_aircraft_count += 1
            if verticals and self.is_3d and self.sea_level_known and deck_height is None:
                altitude_segments.extend((p,(p[0],p[1],0)))
                altitude_colors.extend(((*color[:3],.4),)*2)
            reference_heading=(reference.get('derived') or {}).get('heading_deg') if reference else 0
            directions=arrow_directions(k.get('rotation_xyzw'),reference_heading or 0)
            gear_down = gear_state(systems.get('gear')) == 'down'
            hook_down = (systems.get('tailhook') or {}).get('isDeployed') is True
            self.marker_specs.append((a['instance_id'],p,directions,color,gear_down,hook_down))
            self.airframe_keys[a['instance_id']] = airframe_key((a.get('identity') or {}).get('player_vehicle'))
            heading=f.get('heading')
            if not isinstance(heading,(int,float)) or isinstance(heading,bool) or not math.isfinite(heading):
                heading=heading_from_quaternion(k.get('rotation_xyzw'))
            self.radar_specs.append((a['instance_id'],p,(heading-(reference_heading or 0))%360 if heading is not None else None,color))
            if self.label_fields:
                instance = a['instance_id']
                rtb_seconds = None
                if 'rtb' in self.label_fields and instance not in resting:
                    previous, rtb_seconds = self.rtb_label_values.get(instance, (float('-inf'), None))
                    if stamp < previous or stamp - previous >= 3.:
                        speed = rtb_speed(label_aircraft)
                        distance = rtb_distance(label_aircraft, label_carrier)
                        rtb_seconds = distance / speed if speed and distance is not None else None
                        self.rtb_label_values[instance] = stamp, rtb_seconds
                else:
                    self.rtb_label_values.pop(instance, None)
                text=aircraft_label(label_aircraft,self.label_fields,self.label_units,volume,self.approach_case,
                                    self.case1_glide_deg,instance in resting,rtb_seconds)
                if text:
                    flight = flight_identity(label_aircraft)[0] if 'flight' in self.label_fields else 'Ungrouped'
                    callsign = aircraft_callsign(label_aircraft) if 'flight' in self.label_fields else ''
                    label_records.append((a['instance_id'], p, flight_color, text, flight, callsign))
            motion=velocity_vector(k.get('position_world_m'),k.get('velocity_world_mps'),reference)
            if vectors and motion is not None and np.linalg.norm(motion)>=.1:
                tip=tuple(p[i]+motion[i]*self.velocity_vector_seconds for i in range(3))
                segment(p,tip,color)
        if trails:
            trail_key = (reference_key, carrier_id, locked, sea_offset, theme.flight_palette(),
                         tuple(self.flight_colors.overrides.items()), rtb_context)
            geometry = self.trail_cache.update(history, sample['capture']['monotonic_s'],
                                               session, trail_key, transform_for, self.color_for)
            if geometry is not None:
                positions, colors = geometry
                self.trail_lines.setData(pos=positions, color=colors)
        elif self.trail_cache.key is not None:
            self.trail_cache.clear()
            self.trail_lines.setData(pos=np.empty((0, 3), dtype=np.float32))

        array = lambda x: np.asarray(x, dtype=np.float32).reshape((-1, 3))
        self.glyph_base_colors={a['instance_id']:self.glyph_base_colors[a['instance_id']] for a in sample['aircraft'] if a['instance_id'] in self.glyph_base_colors}
        self.aircraft_labels.set_records(label_records)
        self.rtb_label_values = {instance: self.rtb_label_values[instance]
                                 for instance, _, _ in self.pick_points if instance in self.rtb_label_values}
        self.focus_deck_faces = deck_faces
        self.carrier_deck_lines.setData(pos=array(deck_vertices))
        self.carrier_deck_mask.setVisible(bool(deck_faces))
        self.carrier_hull.setVisible(bool(deck_faces))  # Also clears mask depth in 2D.
        self.carrier_details.setVisible(bool(details))
        self.carrier_flag_frames = flag_frames
        self.holidays.set_frame(holiday_frame)
        if self.deck_crew.enabled:
            crew_points = self.pick_points
            if live_data is not None:
                crew_points = []
                for aircraft in live_data['aircraft']:
                    k = aircraft['kinematics']
                    f = k.get('flight_info') or {}
                    p = transform(k.get('position_world_m'), f.get('altitudeASL'))
                    if p is None:
                        continue
                    instance = aircraft['instance_id']
                    crew_points.append((instance, p, None))
                    touching = any(wheel.get('isTouching') is True for wheel in
                                   (aircraft.get('systems') or {}).get('wheel_suspensions') or ())
                    if (f.get('isLanded') is True or touching or instance in resting) and (
                            deck_height_at(p, crew_deck_faces) is not None):
                        self.deck_aircraft_count += 1
            self.deck_crew.set_frame(crew_frame, stamp, crew_points, data_sample['aircraft'])
        self.update_carrier_flags()
        self.carrier_number_faces = number_faces
        self.visible_number_faces = None
        if details:
            self.carrier_details.setData(pos=array(details), color=np.asarray(detail_colors, dtype=np.float32))
        if deck_faces:
            vertices = array(deck_faces)
            faces = np.arange(len(vertices), dtype=np.uint32).reshape((-1, 3))
            self.carrier_deck_mask.setMeshData(vertexes=vertices, faces=faces)
            self.carrier_hull.setData(pos=array(hull_vertices))
        self.aircraft_centers=array(points)
        if lines:
            self.telemetry_lines.setData(pos=array(lines), color=np.asarray(colors, dtype=np.float32))
        else:
            self.telemetry_lines.setData(pos=array([]))
        self.altitude_lines.setData(pos=array(altitude_segments),color=np.asarray(altitude_colors,dtype=np.float32).reshape((-1,4)))
        self.update_follow(sample['aircraft'])
        self.apply_camera()
