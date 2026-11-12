"""Map annotations with fixed compass bearings and ground-speed ETAs."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QVector3D

from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.map.map_rendering import MapTextOverlay
from wirejudge.ui.theme import color as theme_color


def vector_owner(aircraft):
    if aircraft is None:
        return None
    identity = aircraft.get('identity') or {}
    group, _, title = flight_identity(aircraft)
    callsign = (title.split()[0] if group != 'Ungrouped' else
                identity.get('designation') or identity.get('actor_name') or '—')
    velocity = (aircraft.get('kinematics') or {}).get('velocity_world_mps')
    speed = (math.hypot(velocity[0], velocity[2]) if isinstance(velocity, (list, tuple))
             and len(velocity) == 3 and all(isinstance(v, (float, int)) and math.isfinite(v)
                                           for v in (velocity[0], velocity[2])) else None)
    return str(callsign), speed


def compact_duration(seconds):
    hours, rest = divmod(round(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return ' '.join(part for part in (f'{hours}h' if hours else '',
                                     f'{minutes}m' if minutes else '', f'{seconds}s') if part)


def vector_text(start, end, owner, units):
    east, north = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(east, north)
    bearing = round(math.degrees(math.atan2(east, north))) % 360
    label = f'{bearing:03d}°\n{units.distance(distance):.2f} {units.distance_symbol}'
    if owner is None:
        return label
    callsign, speed = owner
    eta = compact_duration(distance / speed) if speed else 'ETA —'
    return f'{callsign}\n{label}\n{eta}'


class MarshalVectors(MapTextOverlay):
    def __init__(self, view):
        super().__init__(view)
        self.lines = []
        self.draft = None
        self.frame = (0., 0., 0.)
        self.session = None
        self.aircraft = {}
        self.carrier = None
        self.carrier_frames = {}
        self.refresh_label_font()

    def refresh_label_font(self):
        self.label_font = self.map_label_font()
        self.update()

    def set_reference(self, session, origin, heading, aircraft, carrier=None, carrier_frames=None):
        if self.session != session:
            self.clear()
            self.carrier_frames.clear()
            self.session = session
        self.frame = (*origin, math.radians(heading))
        self.aircraft = {item['instance_id']: item for item in aircraft}
        self.carrier = carrier
        if carrier_frames:
            self.carrier_frames.update(carrier_frames)

    def world_point(self, screen):
        view = self.parentWidget()
        viewport = (0, 0, view.width(), view.height())
        if min(viewport[2:]) <= 0:
            return None
        inverse, ok = (view.projectionMatrix(viewport, viewport) * view.viewMatrix()).inverted()
        if not ok:
            return None
        x, y = 2 * screen.x() / view.width() - 1, 1 - 2 * screen.y() / view.height()
        near = inverse.map(QVector3D(x, y, -1))
        far = inverse.map(QVector3D(x, y, .99))
        ray = far - near
        if abs(ray.z()) < 1e-6:
            return None
        fraction = -near.z() / ray.z()
        if fraction < 0:
            return None
        point = near + ray * fraction
        if view.project_point((point.x(), point.y(), 0.)) is None:
            return None
        east, north, angle = self.frame
        c, s = math.cos(angle), math.sin(angle)
        return east + c * point.x() + s * point.y(), north - s * point.x() + c * point.y()

    @staticmethod
    def to_local(point, frame):
        east, north, angle = frame
        x, y = point[0] - east, point[1] - north
        c, s = math.cos(angle), math.sin(angle)
        return c * x - s * y, s * x + c * y

    def anchored_point(self, screen, carrier):
        point = self.world_point(screen)
        if point is not None and carrier is not None:
            point = self.to_local(point, self.carrier_frames[carrier])
        return point

    def anchored_world(self, point, carrier):
        if carrier is None:
            return point
        east, north, angle = self.carrier_frames[carrier]
        x, y = point
        c, s = math.cos(angle), math.sin(angle)
        return east + c * x + s * y, north - s * x + c * y

    def begin(self, screen, selected):
        start = self.anchored_point(screen, self.carrier)
        if start is not None:
            self.draft = (start, start, vector_owner(self.aircraft.get(selected)), self.carrier)
            self.repaint_lines()

    def move(self, screen):
        if self.draft is not None:
            start, _, owner, carrier = self.draft
            end = self.anchored_point(screen, carrier)
            if end is not None:
                self.draft = (start, end, owner, carrier)
                self.repaint_lines()

    def finish(self, screen, dragged):
        if dragged and self.draft is not None:
            start, _, owner, carrier = self.draft
            end = self.anchored_point(screen, carrier)
            if end is not None and end != start:
                self.lines.append((start, end, owner, carrier))
                self.cancel()
                return True
        self.cancel()
        return False

    def cancel(self):
        self.draft = None
        self.repaint_lines()

    def clear(self):
        self.lines.clear()
        self.cancel()

    def repaint_lines(self):
        self.setVisible(bool(self.lines or self.draft))
        self.update()

    def paintEvent(self, _):
        view = self.parentWidget()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.label_font)
        zoom = max(0., math.log2(view.span / 1852.))
        font.setPointSizeF(max(7., 9. - zoom * .3))
        painter.setFont(font)
        accent = QColor(theme_color('accent'))
        panel = QColor(theme_color('panel'))
        metrics = painter.fontMetrics()
        lines = self.lines + [self.draft] if self.draft else self.lines
        for start, end, owner, carrier in lines:
            start, end = (self.anchored_world(p, carrier) for p in (start, end))
            a, b = (view.project_point((*self.to_local(p, self.frame), 0.)) for p in (start, end))
            if a is None or b is None:
                continue
            first, last = QPointF(*a[:2]), QPointF(*b[:2])
            delta = last - first
            length = math.hypot(delta.x(), delta.y())
            if length < 1:
                continue
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(first, last)
            direction = delta / length
            normal = QPointF(-direction.y(), direction.x())
            size = min(11, length / 2)
            base = last - direction * size
            painter.drawLine(last, base + normal * (size / 2))
            painter.drawLine(last, base - normal * (size / 2))
            label = vector_text(start, end, owner, view.label_units)
            rows = label.splitlines()
            width = max(metrics.horizontalAdvance(row) for row in rows) + 10
            height = metrics.lineSpacing() * len(rows) + 4
            middle = (first + last) / 2
            if not self.rect().contains(middle.toPoint()):
                continue
            x = max(0., min(self.width() - width, middle.x() - width / 2))
            y = max(0., middle.y() - height - 7)
            box = QRectF(x, y, width, height)
            painter.setBrush(panel)
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(theme_color('text')))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
