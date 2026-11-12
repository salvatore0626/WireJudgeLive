"""A carrier approach that follows replay loading progress."""
import math
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from wirejudge.ui.theme import color


class ReplayLoading(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.setFixedSize(300, 132)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName('Replay loading')
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self.advance)
        owner.installEventFilter(self)
        owner.tabs.currentChanged.connect(self.sync_visibility)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.center()
        return super().eventFilter(watched, event)

    def center(self):
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, (parent.height() - self.height()) // 2)

    def begin(self):
        self.position = self.target = 0.
        self.last_tick = time.monotonic()
        self.caught_at = None
        self.center()
        self.timer.start()
        self.sync_visibility()

    def sync_visibility(self, *_):
        tabs = self.parentWidget().tabs
        visible = self.timer.isActive() and tabs.tabText(tabs.currentIndex()) not in ('Leaderboards', 'Settings')
        self.setVisible(visible)
        if visible:
            self.raise_()

    def progress(self, fraction):
        if self.timer.isActive():
            self.target = max(self.target, min(.96, fraction * .96))

    def ready(self):
        if self.timer.isActive():
            self.target = 1.

    def stop(self):
        self.timer.stop()
        self.hide()

    def advance(self):
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        if self.caught_at is not None:
            if now - self.caught_at >= .55:
                self.stop()
            return
        self.position += (self.target - self.position) * (1 - math.exp(-dt * 9))
        if self.target - self.position < .002:
            self.position = self.target
        if self.position == 1.:
            self.caught_at = now
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(color('accent'))
        painter.setPen(QPen(QColor(color('border')), 1))
        painter.setBrush(QColor(color('panel')))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 10, 10)

        start = QPointF(33, 47)
        wire = QPointF(232, 91)
        painter.setPen(QPen(QColor(color('muted')), 1, Qt.PenStyle.DashLine))
        painter.drawLine(start, wire)
        painter.setPen(QPen(QColor(color('carrier')), 1.5))
        painter.setBrush(QColor(color('recessed')))
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in
                                      ((198, 98), (282, 98), (261, 112), (212, 112))]))
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in
                                       ((212, 98), (212, 84), (226, 84), (226, 98))]))
        painter.drawLine(QPointF(218, 84), QPointF(218, 76))
        painter.setPen(QPen(accent, 2))
        painter.drawLine(QPointF(wire.x(), 96), QPointF(wire.x(), 101))

        jet = start + (wire - start) * self.position
        painter.save()
        painter.translate(jet)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        silhouette = QPainterPath(QPointF(-19, 1))
        silhouette.lineTo(-16, -2)
        silhouette.lineTo(-19, -13)
        silhouette.lineTo(-15, -13)
        silhouette.lineTo(-8, -3)
        silhouette.lineTo(1, -3)
        silhouette.cubicTo(4, -6, 9, -6, 14, -3)
        silhouette.lineTo(23, 0)
        silhouette.quadTo(20, 2, 13, 2)
        silhouette.lineTo(7, 3)
        silhouette.lineTo(4, 5)
        silhouette.lineTo(-3, 5)
        silhouette.lineTo(-6, 3)
        silhouette.lineTo(-19, 3)
        silhouette.closeSubpath()
        painter.drawPath(silhouette)
        painter.setPen(QPen(accent, 1.25))
        painter.drawLine(QPointF(10, 3), QPointF(10, 7))
        painter.drawLine(QPointF(-4, 4), QPointF(-5, 7))
        if self.caught_at is not None:
            painter.drawPolyline(QPolygonF([QPointF(3, 4), QPointF(0, 7), QPointF(1, 7)]))
        painter.restore()

        painter.setPen(QColor(color('text')))
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        label = '100% · Wire caught' if self.caught_at is not None else (
            f'Loading replay…  {int(self.position * 100)}%')
        painter.drawText(QRectF(14, 9, 272, 24), Qt.AlignmentFlag.AlignCenter, label)
