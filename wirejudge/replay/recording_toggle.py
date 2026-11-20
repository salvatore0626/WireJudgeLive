"""Return to Live button with a pulsing telemetry indicator."""
import math
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPushButton
from wirejudge.ui.theme import color


class ReturnToLiveButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__('Return to Live', parent)
        self.pulsing = False
        self.pulse_started = 0.
        self.pulse_timer = QTimer(self, interval=50)
        self.pulse_timer.timeout.connect(self.update)

    def set_pulsing(self, active):
        if self.pulsing == active:
            return
        self.pulsing = active
        self.pulse_started = time.monotonic()
        self.apply_theme()
        if active and self.isVisible():
            self.pulse_timer.start()
        else:
            self.pulse_timer.stop()

    def apply_theme(self):
        warning = color('warning')
        self.setStyleSheet(
            f'QPushButton {{color: {warning}; border-color: {warning}; padding-left: 27px;}}'
            if self.pulsing else ''
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.pulsing:
            elapsed = time.monotonic() - self.pulse_started
            dot = QColor(color('warning'))
            dot.setAlphaF(.3 + .7 * (.5 + .5 * math.cos(elapsed * math.tau / 2)))
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(10, self.height() // 2 - 4, 8, 8)

    def hideEvent(self, event):
        self.pulse_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if self.pulsing:
            self.pulse_timer.start()
        super().showEvent(event)
