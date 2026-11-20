"""A deck crew runner indicating background reads in the editor map."""
import math
import random
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from wirejudge.ui.theme import color
from wirejudge.map.deck_crew import CREW_COLORS


class TrackLoading(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName('Loading approach data')
        self.started = time.monotonic()
        self.message = ''
        self.progress = 0.
        self.finish_timer = QTimer(self, singleShot=True, interval=250)
        self.finish_timer.timeout.connect(self.finish)
        self.crew_color = QColor.fromRgbF(*CREW_COLORS[0])
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self.update)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.center()
        return super().eventFilter(watched, event)

    def center(self):
        parent = self.parentWidget()
        width = min(320, max(1, parent.width() - 24))
        self.setGeometry((parent.width() - width) // 2,
                         max(0, (parent.height() - 96) // 2), width, 96)

    def finish(self):
        self.timer.stop()
        self.hide()

    def set_loading(self, message, progress=0.):
        if not message:
            if self.timer.isActive() and not self.finish_timer.isActive():
                self.progress = 1.
                self.message = 'Attempt editor ready'
                self.finish_timer.start()
                self.update()
            return
        completing = self.finish_timer.isActive()
        self.finish_timer.stop()
        self.message = message
        if completing:
            self.progress = min(self.progress, .99)
        if not self.timer.isActive():
            self.progress = 0.
            self.crew_color = QColor.fromRgbF(*random.choice(CREW_COLORS))
            self.started = time.monotonic()
            self.timer.start()
        self.progress = max(self.progress, min(.99, progress))
        self.center()
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color('border')), 1))
        painter.setBrush(QColor(color('panel')))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)
        elapsed = time.monotonic() - self.started
        left, width = 18., max(1., self.width() - 36.)
        bar = QRectF(left, 56, width, 5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color('recessed')))
        painter.drawRoundedRect(bar, 2, 2)
        progress = self.progress
        painter.setBrush(QColor(color('accent')))
        painter.drawRoundedRect(QRectF(left, 56, width * progress, 5), 2, 2)

        painter.save()
        painter.translate(left + 9 + progress * max(1., width - 18), 54)
        painter.scale(.85, .85)
        phase = elapsed * math.tau * 2
        # Fixed limb lengths and bent knees keep the gait readable at icon size.
        legs = []
        for side in (0, math.pi):
            stride = phase + side
            thigh = .85 * math.sin(stride)
            shin = thigh - (.25 + 1.4 * max(0., -math.cos(stride)))
            knee = QPointF(-1 + 7 * math.sin(thigh), -13 + 7 * math.cos(thigh))
            foot = knee + QPointF(7 * math.sin(shin), 7 * math.cos(shin))
            legs.append((knee, foot))
        floor = max(foot.y() for _, foot in legs)
        painter.translate(0, -floor - .8 * (1 - math.cos(phase * 2)))
        pen = QPen(self.crew_color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for knee, foot in legs:
            painter.drawLine(QPointF(-1, -13), knee)
            painter.drawLine(knee, foot)
            painter.drawLine(foot, foot + QPointF(2, 0))
        for side in (0, math.pi):
            upper = -.85 * math.sin(phase + side)
            forearm = upper + 1.4
            elbow = QPointF(1 + 5 * math.sin(upper), -22 + 5 * math.cos(upper))
            hand = elbow + QPointF(5 * math.sin(forearm), 5 * math.cos(forearm))
            painter.drawLine(QPointF(1, -22), elbow)
            painter.drawLine(elbow, hand)
        pen.setWidthF(4)
        painter.setPen(pen)
        painter.drawLine(QPointF(1, -22), QPointF(-1, -13))
        pen.setWidthF(1)
        painter.setPen(pen)
        painter.setBrush(self.crew_color)
        painter.drawEllipse(QRectF(0, -30, 6, 6))
        painter.restore()
        painter.setPen(QColor(color('text')))
        painter.drawText(QRectF(8, 68, self.width() - 16, 20),
                         Qt.AlignmentFlag.AlignCenter, f'{self.message}  {int(progress * 100)}%')
