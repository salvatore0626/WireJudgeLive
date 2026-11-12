"""The replay trim range and its recovery markers."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from wirejudge.replay.replay_files import attempt_fits
from wirejudge.ui.theme import color as theme_color


def clock(seconds):
    seconds = int(round(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class TrimTimeline(QWidget):
    moved = Signal(int, float)

    def __init__(self, duration, spans):
        super().__init__()
        self.duration = duration
        self.spans = spans
        self.bounds = (0., duration)
        self.handle = None
        self.setMinimumHeight(94)
        self.setAccessibleName('Replay trim timeline')

    def x_at(self, offset):
        return 32 + offset / self.duration * (self.width() - 64)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = self.x_at(0), self.x_at(self.duration)
        start, end = map(self.x_at, sorted(self.bounds))
        painter.fillRect(QRectF(left, 35, right - left, 20), QColor(theme_color('border')))
        painter.fillRect(QRectF(start, 35, end - start, 20), QColor(theme_color('selected')))
        for _, first, last, marker, outcome in self.spans:
            x = self.x_at(first)
            painter.fillRect(QRectF(x, 39, max(1, self.x_at(last) - x), 12),
                             QColor(theme_color('accent')))
            if marker is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme_color('score_green' if outcome == 'Wire' else 'score_orange')))
                painter.drawEllipse(QPointF(self.x_at(marker), 25), 4, 4)
        shade = QColor(theme_color('panel'))
        shade.setAlpha(175)
        for a, b in ((left, start), (end, right)):
            painter.fillRect(QRectF(a, 18, b - a, 38), shade)
        for offset, role in zip(self.bounds, ('score_green', 'warning')):
            x = self.x_at(offset)
            painter.fillRect(QRectF(x - 2, 33, 4, 26), QColor(theme_color(role)))
            painter.fillRect(QRectF(x - 5, 51, 10, 10), QColor(theme_color(role)))
        painter.setPen(QColor(theme_color('muted')))
        for fraction in (0, .25, .5, .75, 1):
            seconds = fraction * self.duration
            rect = QRectF(self.x_at(seconds) - 32, 66, 64, 20)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, clock(seconds))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        self.handle = min(range(2), key=lambda i: abs(self.x_at(self.bounds[i]) - x))
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self.handle is not None:
            fraction = (event.position().x() - 32) / (self.width() - 64)
            self.moved.emit(self.handle, max(0., min(1., fraction)) * self.duration)

    def mouseReleaseEvent(self, event):
        self.handle = None


class ReplayTrimDialog(QDialog):
    def __init__(self, parent, name, preview):
        super().__init__(parent)
        self.setWindowTitle(f'Trim Replay — {name}')
        self.setMinimumWidth(600)
        self.first, self.last, self.attempts = preview
        duration = self.last - self.first
        spans = [(key, start - self.first, end - self.first,
                  None if marker is None else marker - self.first, outcome)
                 for key, start, end, marker, outcome in self.attempts]
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Drag the handles or enter the start and end in elapsed seconds.'))
        self.timeline = TrimTimeline(duration, spans)
        self.timeline.moved.connect(self.move_handle)
        layout.addWidget(self.timeline)
        legend = QHBoxLayout()
        for label, role in (('━ Approach', 'accent'), ('● Wire', 'score_green'),
                            ('● Bolter', 'score_orange')):
            widget = QLabel(label)
            widget.setStyleSheet(f'color: {theme_color(role)};')
            legend.addWidget(widget)
        legend.addStretch()
        layout.addLayout(legend)
        controls = QHBoxLayout()
        self.fields = []
        for title, offset in (('Start', 0), ('End', duration)):
            field = QDoubleSpinBox()
            field.setAccessibleName(f'{title} time in elapsed seconds')
            field.setDecimals(3)
            field.setRange(0, duration)
            field.setSingleStep(1)
            field.setSuffix(' s')
            field.setValue(offset)
            controls.addWidget(QLabel(title))
            controls.addWidget(field)
            self.fields.append(field)
            field.valueChanged.connect(self.update_range)
        controls.addStretch()
        layout.addLayout(controls)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save = QPushButton('Save trimmed copy')
        self.save.clicked.connect(self.accept)
        buttons.addWidget(self.save)
        layout.addLayout(buttons)
        self.update_range()

    def move_handle(self, handle, offset):
        other = self.fields[1 - handle].value()
        self.fields[handle].setValue(max(offset, other) if handle else min(offset, other))

    def update_range(self):
        start, end = (field.value() for field in self.fields)
        self.bounds = (self.first if start == 0 else min(self.last, self.first + start),
                       self.last if end == self.fields[1].maximum() else self.first + end)
        self.timeline.bounds = tuple(value - self.first for value in self.bounds)
        self.timeline.update()
        self.save.setEnabled(start < end and self.bounds != (self.first, self.last))
        kept = sum(attempt_fits(row, *self.bounds) for row in self.attempts)
        omitted = len(self.attempts) - kept
        summary = f'Keep {clock(max(0, end - start))} · {kept} of {len(self.attempts)} approaches'
        if omitted:
            summary += '. Approaches outside the range or cut by a handle are omitted.'
        self.summary.setText(summary)
