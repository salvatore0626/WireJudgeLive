"""Carrier bearings and a rolling wind estimate from aircraft telemetry."""
from collections import deque
import math
from wirejudge.paths import ASSETS_DIR

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from wirejudge.approach.approach_scopes import ScopePane
from wirejudge.approach.approach_geometry import NM, point
from wirejudge.map.carrier_wireframe import deck_height_at, landing_area_world
from wirejudge.aircraft.flight_instruments import carrier_wind, wind_from
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion
from wirejudge.ui.theme import color

WINDOW_SECONDS = 180
COMPASS_POINTS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')
SPEED_BANDS = 8
HISTORY_SECTORS = 16
SECTOR_DEGREES = 360 / HISTORY_SECTORS
WIND_SCALE_STEP = 10 * NM / 3600


def wind_readings(sample, carrier):
    """Return ambient and carrier-relative wind as (speed, from bearing) pairs."""
    vector = carrier_wind(sample, carrier)
    if vector is None:
        return None, None
    ambient = math.hypot(vector[0], vector[2]), wind_from(vector)
    motion = carrier.get('velocity_world_mps') if carrier else None
    if not point(motion):
        return ambient, None
    relative = (vector[0] - motion[0], 0., vector[2] - motion[2])
    return ambient, (math.hypot(relative[0], relative[2]), wind_from(relative))


def carrier_at(sample, carrier_id):
    return next((c for c in sample['carriers'] if c['instance_id'] == carrier_id), None)


def wind_history_cells(history, scale):
    counts = [[0] * SPEED_BANDS for _ in range(HISTORY_SECTORS)]
    for _, (speed, bearing) in history:
        if bearing is not None:
            downwind = (bearing + 180 + SECTOR_DEGREES / 2) % 360
            sector = int(downwind / SECTOR_DEGREES)
            band = min(SPEED_BANDS - 1, int(speed / scale * SPEED_BANDS))
            counts[sector][band] += 1
    return counts


def heading_text(value):
    return '—' if value is None else f'{round(value) % 360:03d}°'


def crosswind_component(wind, final_bearing):
    """Signed crosswind in m/s. Positive means wind from the right on final."""
    if wind is None or final_bearing is None:
        return None
    speed, bearing = wind
    return 0. if bearing is None else speed * math.sin(math.radians(bearing - final_bearing))


def landing_area_occupied(sample, carrier, heading):
    area = landing_area_world(carrier, heading) if carrier else ()
    if not area:
        return None
    for aircraft in sample['aircraft']:
        kinematics = aircraft.get('kinematics') or {}
        wheels = (aircraft.get('systems') or {}).get('wheel_suspensions') or ()
        contacts = [wheel.get('point') for wheel in wheels if wheel.get('isTouching') is True]
        if contacts or (kinematics.get('flight_info') or {}).get('isLanded') is True:
            positions = [kinematics.get('position_world_m'), *contacts]
            if any(deck_height_at((p[0], p[2], p[1]), [area]) is not None
                   for p in positions if point(p)):
                return True
    return False


class ConditionsView(QWidget):
    def __init__(self, kind, manager):
        super().__init__()
        self.manager = manager
        self.runway_button = QPushButton('Runway', self)
        self.runway_button.setCheckable(True)
        self.runway_button.setStyleSheet('padding: 2px 7px;')
        self.runway_button.setToolTip('Show the carrier landing runway aligned with final bearing.')
        self.relative_button = QPushButton('Relative Wind', self)
        self.relative_button.setCheckable(True)
        self.relative_button.setStyleSheet('padding: 2px 7px;')
        self.relative_button.setToolTip('Show current and historical wind over the moving deck.')
        controls = QVBoxLayout(self)
        controls.setContentsMargins(7, 22, 7, 4)
        controls.addWidget(self.runway_button, 0, Qt.AlignmentFlag.AlignRight)
        controls.addStretch()
        controls.addWidget(self.relative_button, 0, Qt.AlignmentFlag.AlignRight)

    def paintEvent(self, event):
        m = self.manager
        relative = self.relative_button.isChecked()
        current = m.wind_current[relative]
        history = m.wind_history[relative]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        p.setPen(QColor(color('text')))
        p.drawText(10, 19, f'BRC {heading_text(m.brc)}     FB {heading_text(m.final)}')
        reciprocal = None if m.brc is None else m.brc + 180
        p.drawText(10, 37, f'BRC Recip {heading_text(reciprocal)}')
        center = QPointF(width / 2, height / 2 - 19)
        radius = max(14., min(width / 2 - 26, height / 2 - 79))
        outline, accent = QColor(color('muted')), QColor(color('accent'))

        speeds = [wind[0] for _, wind in history]
        peak = max(max(speeds, default=0.), current[0] if current else 0.)
        scale = WIND_SCALE_STEP * max(1, math.ceil(peak / WIND_SCALE_STEP))

        def at(bearing, fraction):
            angle = math.radians(bearing)
            length = radius * fraction
            return center + QPointF(math.sin(angle) * length, -math.cos(angle) * length)

        cells = wind_history_cells(history, scale)
        densest = max(map(max, cells))
        if densest:
            shade = QColor(accent)
            p.setPen(Qt.PenStyle.NoPen)
            radial_gap = .45 / radius
            for sector, bands in enumerate(cells):
                start = (sector - .5) * SECTOR_DEGREES + .5
                angles = [start + (SECTOR_DEGREES - 1) * i / 8 for i in range(9)]
                for band, count in enumerate(bands):
                    if count:
                        low = band / SPEED_BANDS + (radial_gap if band else 0)
                        high = (band + 1) / SPEED_BANDS - radial_gap
                        shade.setAlpha(round(40 + 112 * math.sqrt(count / densest)))
                        p.setBrush(shade)
                        p.drawPolygon(QPolygonF([at(a, high) for a in angles]
                                                + [at(a, low) for a in reversed(angles)]))

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(outline, 1))
        p.drawEllipse(center, radius, radius)
        p.drawEllipse(center, radius / 2, radius / 2)
        p.save()
        font = p.font()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        for i, title in enumerate(COMPASS_POINTS):
            bearing = i * 360 / len(COMPASS_POINTS)
            p.setPen(QColor(color('warning')) if i == 0 else outline)
            p.drawLine(at(bearing, 1 - 3 / radius), at(bearing, 1 + 3 / radius))
            label_at = at(bearing, 1 + 13 / radius)
            p.drawText(QRectF(label_at.x() - 15, label_at.y() - 8, 30, 16),
                       Qt.AlignmentFlag.AlignCenter, title)
        p.restore()
        if self.runway_button.isChecked() and m.final is not None:
            self.draw_runway(p, center, radius, m.final)
        if current:
            speed, bearing = current
            if bearing is not None:
                downwind = bearing + 180
                tip = at(downwind, speed / scale)
                size = min(8., radius * speed / scale)
                head = []
                for side in (-150, 150):
                    a = math.radians(downwind + side)
                    head.append(tip + QPointF(math.sin(a) * size, -math.cos(a) * size))
                arrowhead = QPolygonF([head[0], tip, head[1]])
                for ink, weight in ((QColor(color('panel')), 4.5), (accent, 2)):
                    pen = QPen(ink, weight)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    p.drawLine(center, tip)
                    p.drawPolyline(arrowhead)
                text = f'Wind from {heading_text(bearing)} · {m.units.speed(speed):.0f} {m.units.speed_symbol}'
            else:
                text = f'Wind calm · {m.units.speed(speed):.0f} {m.units.speed_symbol}'
        else:
            text = 'Relative wind unavailable' if relative else 'Wind unavailable'
        self.draw_ring_labels(p, at, scale)
        if m.brc is not None:
            p.setPen(QPen(QColor(color('text')), 2))
            p.drawPolyline(QPolygonF([at(m.brc - 5, .91), at(m.brc, 1.04),
                                       at(m.brc + 5, .91)]))
            marker = at(m.brc, .76)
            if abs((m.brc + 180) % 360 - 180) < 25:
                marker += QPointF(-30 if marker.x() < center.x() else 30, 0)
            else:
                marker += at(m.brc + 90, 17 / radius) - center
            metrics = p.fontMetrics()
            p.drawText(marker + QPointF(-metrics.horizontalAdvance('BRC') / 2,
                                       (metrics.ascent() - metrics.descent()) / 2), 'BRC')
        p.setPen(QPen(accent, 2))
        p.drawText(10, height - 69, text)
        crosswind = crosswind_component(current, m.final)
        if crosswind is None:
            crosswind_text = 'Crosswind · —'
        else:
            magnitude = round(m.units.speed(abs(crosswind)))
            side = (' Left' if crosswind < 0 else ' Right') if magnitude else ''
            crosswind_text = f'{magnitude} {m.units.speed_symbol}{side} Crosswind'
        p.drawText(10, height - 51, crosswind_text)
        p.setPen(outline)
        if speeds:
            p.drawText(10, height - 32, f'3 min · {m.units.speed(min(speeds)):.0f}–'
                       f'{m.units.speed(max(speeds)):.0f} {m.units.speed_symbol}')
        else:
            p.drawText(10, height - 32, '3 min · —')
        state = ('Red Deck' if m.occupied else 'Green Deck' if m.occupied is False else 'Deck status unavailable')
        role = ('warning' if m.flash else 'warning_dim') if m.occupied else (
            'score_green' if m.occupied is False else 'muted')
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color(role)))
        p.drawEllipse(QPointF(15, height - 14), 5, 5)
        p.setPen(QColor(color('text')))
        width = max(0, self.relative_button.x() - 32)
        p.drawText(26, height - 9, p.fontMetrics().elidedText(state, Qt.TextElideMode.ElideRight, width))

    def draw_runway(self, painter, center, radius, final_bearing):
        half_length, half_width = radius / 4, radius / 18
        painter.save()
        painter.translate(center)
        painter.rotate(final_bearing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(color('text')), 1.2))
        painter.drawRect(QRectF(-half_width, -half_length, 2 * half_width, 2 * half_length))
        threshold = half_length * .73
        painter.drawLine(QPointF(-half_width, threshold), QPointF(half_width, threshold))
        painter.setPen(QPen(QColor(color('text')), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(0, -half_length * .8), QPointF(0, half_length * .55))
        painter.restore()

    def draw_ring_labels(self, painter, at, scale):
        units = self.manager.units
        painter.save()
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for fraction in (.5, 1.):
            speed = f'{units.speed(scale * fraction):.1f}'.removesuffix('.0')
            text = f'{speed} {units.speed_symbol}'
            width = metrics.horizontalAdvance(text) + 6
            top = at(0, fraction)
            rect = QRectF(top.x() - width / 2, top.y() + 2, width, metrics.height())
            painter.fillRect(rect, QColor(color('panel')))
            painter.setPen(QColor(color('text')))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ConditionsTool(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.units = owner.units
        self.wind_history = (deque(), deque())
        self.wind_current = (None, None)
        self.key = self.stamp = self.brc = self.final = None
        self.history_start = None
        self.occupied = None
        self.flash = True
        self.timer = QTimer(self)
        self.timer.setInterval(450)
        self.timer.timeout.connect(self.blink)
        self.pane = ScopePane('Conditions', 'conditions', self, view_factory=ConditionsView)
        runway_button = self.pane.view.runway_button
        runway_button.setChecked(owner.settings.store.value('conditions/show_runway', False, type=bool))
        runway_button.toggled.connect(self.set_runway_visible)
        relative_button = self.pane.view.relative_button
        relative_button.setChecked(owner.settings.store.value('conditions/relative_wind', False, type=bool))
        relative_button.toggled.connect(self.set_relative_wind)
        self.warning_sound = self.load_warning_sound()
        self.volume_slider = self.pane.header.add_volume('Red Deck warning volume')
        self.volume_slider.setValue(owner.settings.store.value('conditions/volume', 35, type=int))
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.set_volume(self.volume_slider.value())
        owner.map.installEventFilter(self)
        self.layout_panes()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Resize):
            self.layout_panes()
        return False

    def layout_panes(self):
        if self.pane.detached:
            return
        view = self.owner.map
        width, height = self.pane.user_size or (304, 328)
        width = min(width, max(190, view.width() - 16))
        height = min(height, max(160, view.height() - 78))
        self.pane.place(view.width() - width - 8, 78, width, height)
        self.pane.raise_()
        self.owner.map_controls.raise_()

    def sync_timer(self):
        if self.pane.isVisible() and self.occupied:
            if not self.timer.isActive():
                self.flash = True
                self.timer.start()
                if self.warning_sound is not None and self.volume_slider.value():
                    self.warning_sound.play()
        else:
            self.timer.stop()
        self.pane.view.update()

    def load_warning_sound(self):
        if QGuiApplication.platformName() in ('offscreen', 'minimal'):
            return None
        try:
            from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
        except ImportError:
            return None
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            return None
        sound = QSoundEffect(device, self)
        path = ASSETS_DIR / 'sounds' / 'red-deck.wav'
        sound.setSource(QUrl.fromLocalFile(str(path)))
        return sound

    def set_volume(self, volume):
        if self.warning_sound is not None:
            self.warning_sound.setVolume(volume / 100)
            if not volume:
                self.warning_sound.stop()
        self.volume_slider.setToolTip(f'Red Deck warning volume: {volume}%')
        self.owner.settings.store.setValue('conditions/volume', volume)

    def blink(self):
        self.flash = not self.flash
        self.pane.view.update()

    def set_relative_wind(self, enabled):
        self.owner.settings.store.setValue('conditions/relative_wind', enabled)
        self.pane.view.update()

    def set_runway_visible(self, visible):
        self.owner.settings.store.setValue('conditions/show_runway', visible)
        self.pane.view.update()

    def set_frame(self, sample, carrier_id, units, key, history=()):
        self.units = units
        if not sample:
            self.clear_history()
            self.wind_current = (None, None)
            self.key = self.brc = self.final = None
            self.occupied = None
            if self.pane.enabled:
                self.sync_timer()
            return
        capture = sample['capture']
        now = capture['monotonic_s']
        session = capture['connection_id']
        key = key, session, carrier_id
        if self.key != key or (self.stamp is not None and now < self.stamp):
            self.clear_history()
        self.key = key
        if history and self.pane.enabled:
            start = history[0]['capture']['monotonic_s']
            if self.history_start is None or start < self.history_start:
                self.clear_history()
            self.history_start = start
            for frame in history:
                capture = frame['capture']
                stamp = capture['monotonic_s']
                if capture['connection_id'] == session and now - WINDOW_SECONDS <= stamp < now and (
                        self.stamp is None or stamp > self.stamp):
                    self.append_wind(stamp, wind_readings(frame, carrier_at(frame, carrier_id)))
        carrier = carrier_at(sample, carrier_id)
        self.wind_current = wind_readings(sample, carrier)
        self.append_wind(now, self.wind_current)
        self.stamp = now
        for readings in self.wind_history:
            while readings and readings[0][0] < now - WINDOW_SECONDS:
                readings.popleft()
        self.brc = heading_from_quaternion(carrier.get('rotation_xyzw')) if carrier else None
        self.final = None if self.brc is None else (self.brc - self.owner.map.case1_runway_offset_deg) % 360
        if self.pane.enabled:
            self.occupied = landing_area_occupied(sample, carrier, self.brc)
            self.sync_timer()

    def clear_history(self):
        for readings in self.wind_history:
            readings.clear()
        self.stamp = self.history_start = None

    def append_wind(self, stamp, winds):
        for readings, wind in zip(self.wind_history, winds):
            if readings and readings[-1][0] == stamp:
                readings.pop()
            if wind is not None:
                readings.append((stamp, wind))

    def apply_theme(self):
        self.pane.header.refresh_volume_icon()
        if self.pane.floating_window:
            self.pane.floating_window.setStyleSheet(self.owner.styleSheet())
        self.sync_timer()

    def shutdown(self):
        self.timer.stop()
        if self.warning_sound is not None:
            self.warning_sound.stop()
        if self.pane.floating_window:
            self.pane.floating_window.hide()
        self.pane.hide()
