"""Screen-space aircraft tags with compact labels and flight grouping."""
from collections import defaultdict

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QFont, QColor, QPainter, QStaticText, QTransform, QVector3D
from wirejudge.map.map_rendering import MapTextOverlay


class AircraftLabels(MapTextOverlay):
    def __init__(self, view):
        super().__init__(view)
        self.label_font = self.map_label_font()
        self.bold_font = QFont(self.label_font)
        self.bold_font.setBold(True)
        self.records = []
        self.cache = {}
        self.selected_id = None
        self.compact = set()
        self.merged = set()

    def refresh_label_font(self):
        font = self.map_label_font()
        if font != self.label_font:
            self.label_font = font
            self.bold_font = QFont(font)
            self.bold_font.setBold(True)
            self.cache.clear()
            self.update()

    def set_records(self, records):
        self.records = records
        present = {r[0] for r in records}
        self.compact.intersection_update(present)
        self.merged.intersection_update(present)
        self.setVisible(bool(records))
        self.update()

    def layout(self, lines):
        if lines not in self.cache:
            if len(self.cache) >= 1024:
                self.cache.clear()
            layouts = []
            for text, warning in lines:
                layout = QStaticText(text)
                layout.setTextFormat(Qt.TextFormat.PlainText)
                layout.prepare(QTransform(), self.bold_font if warning else self.label_font)
                layouts.append((layout, warning))
            size = (max(layout.size().width() for layout, _ in layouts),
                    sum(layout.size().height() for layout, _ in layouts))
            self.cache[lines] = layouts, size
        return self.cache[lines]

    @staticmethod
    def collisions(boxes, previous):
        crowded = set()
        for i, (instance, rect) in enumerate(boxes):
            for other, other_rect in boxes[i + 1:]:
                margin = 7 if instance in previous or other in previous else 2
                if rect.adjusted(-margin, -margin, margin, margin).intersects(other_rect):
                    crowded.update((instance, other))
        return crowded

    def arrange(self, contacts):
        boxes = [(i, QRectF(x + 12, y + 12, *self.layout(lines)[1]))
                 for i, x, y, _, lines, _, _ in contacts]
        self.compact = self.collisions(boxes, self.compact) - {self.selected_id}
        tags, candidates = [], defaultdict(list)
        for i, x, y, color, lines, flight, callsign in contacts:
            warnings = tuple(line for line in lines if line[1])
            if i in self.compact:
                short = next((line for line in lines if not line[1]), None)
                lines = (((callsign, False),) if callsign else (short,) if short else ()) + warnings
            tag = (i, x, y, color, lines, bool(warnings))
            tags.append(tag)
            if i in self.compact and not warnings and flight != 'Ungrouped':
                candidates[flight].append(tag)

        groups, merged = [], set()
        for flight, members in candidates.items():
            boxes = [(i, QRectF(x + 12, y + 12, *self.layout(lines)[1]))
                     for i, x, y, _, lines, _ in members]
            overlapping = self.collisions(boxes, self.merged)
            if overlapping:
                collapsed = [tag for tag in members if tag[0] in overlapping]
                count = len(collapsed)
                x = sum(tag[1] for tag in collapsed) / count
                y = sum(tag[2] for tag in collapsed) / count
                groups.append((None, x, y, collapsed[0][3], ((f'{flight} · {count}', False),), False))
                merged.update(overlapping)
        self.merged = merged
        return [tag for tag in tags if tag[0] not in merged] + groups

    def paintEvent(self, event):
        if not self.records:
            return
        project = self.compute_projection()
        contacts = []
        for instance, position, color, lines, flight, callsign in self.records:
            p = project.map(QVector3D(*position))
            if 0 <= p.z() <= 1 and 0 <= p.x() <= self.width() and 0 <= p.y() <= self.height():
                contacts.append((instance, p.x(), p.y(), color, lines, flight, callsign))
        tags = self.arrange(contacts)
        tags.sort(key=lambda tag: (tag[0] is None or tag[0] != self.selected_id, not tag[-1]))
        occupied, visible = [], []
        bounds = QRectF(self.rect())
        for instance, x, y, color, lines, warning in tags:
            layouts, (w, h) = self.layout(lines)
            positions = ((x+12, y+12), (x-w-12, y+12), (x+12, y-h-12), (x-w-12, y-h-12))
            choices = [QRectF(px, py, w, h) for px, py in positions]
            if warning:
                # Give warnings extra room when all four nearby positions are taken.
                for distance in (1, 2, 3):
                    offset = distance * (h + 4)
                    choices.extend(rect.translated(0, offset if i < 2 else -offset)
                                   for i, rect in enumerate(choices[:4]))
            box = next((rect for rect in choices if bounds.contains(rect) and not any(
                rect.adjusted(-2, -2, 2, 2).intersects(other) for other in occupied)), None)
            if box is None:
                if not warning and (instance is None or instance != self.selected_id):
                    continue
                box = choices[0]
                box.moveTo(max(0., min(box.x(), bounds.width()-w)), max(0., min(box.y(), bounds.height()-h)))
            occupied.append(box)
            visible.append((box.topLeft(), layouts, QColor.fromRgbF(*color)))
        if not visible:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        warning_color = QColor(self.parentWidget().paint_color('warning'))
        for position, layouts, color in reversed(visible):
            for layout, warning in layouts:
                painter.setFont(self.bold_font if warning else self.label_font)
                painter.setPen(warning_color if warning else color)
                painter.drawStaticText(position, layout)
                position = QPointF(position.x(), position.y() + layout.size().height())
        painter.end()
