"""Theme-aware controls for starting a connected telemetry mod."""
import math
import time

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from wirejudge.ui import theme


class TelemetryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.pulsing = False
        self.timer = QTimer(self, interval=60)
        self.timer.timeout.connect(self.apply_theme)

    def set_pulsing(self, enabled):
        if self.pulsing == enabled:
            return
        self.pulsing = enabled
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()
        self.apply_theme()

    def apply_theme(self):
        if self.pulsing:
            warning = theme.color('warning')
            weight = .18 + .22 * (1 + math.sin(time.monotonic() * 5))
            fill = theme.blend(theme.color('control'), warning, weight)
            self.setStyleSheet(f'QPushButton {{color:{warning}; border-color:{warning}; background:{fill};}}')
        else:
            self.setStyleSheet('')


class TelemetryStatusButton(QPushButton):
    """Compact status-bar control using the fixed telemetry status colors."""
    def __init__(self, parent=None):
        super().__init__('Start', parent)
        self.running = False
        self.pulsing = False
        self.timer = QTimer(self, interval=60)
        self.timer.timeout.connect(self.apply_theme)
        self.setFixedHeight(22)
        self.setMinimumWidth(52)
        self.setMaximumWidth(64)
        self.apply_theme()

    def set_running(self, running):
        if self.running != running:
            self.running = running
            self.apply_theme()

    def set_pulsing(self, enabled):
        if self.pulsing == enabled:
            return
        self.pulsing = enabled
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()
        self.apply_theme()

    def apply_theme(self):
        color = '#FF626A' if self.running else '#20EF55'
        background = '#26282B'
        if self.pulsing and not self.running and self.isEnabled():
            alpha = round(40 + 35 * (1 + math.sin(time.monotonic() * 3)))
            background = f'rgba(32,239,85,{alpha})'
        self.setStyleSheet(
            f'QPushButton {{color:{color};background:{background};border:1px solid {color};'
            'border-radius:3px;padding:1px 7px;font-size:11px;min-height:0;min-width:0;}'
            'QPushButton:hover {background:#34383E;}'
            'QPushButton:pressed {background:#1C1E21;}'
            'QPushButton:disabled {color:#7D828A;border-color:#50545A;background:#26282B;}')


class TelemetryConnection(QFrame):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setObjectName('telemetryConnection')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel('Telemetry mod connected')
        title.setStyleSheet('font-size:18px;font-weight:600')
        layout.addWidget(title)
        layout.addWidget(QLabel('Choose a refresh rate to start receiving telemetry.'))
        row = QHBoxLayout()
        row.addWidget(QLabel('Refresh rate'))
        self.rate = QComboBox()
        for hz in (1, 2, 5, 10, 15, 20, 25, 30, 40, 50):
            self.rate.addItem(f'{hz} Hz', hz)
        row.addWidget(self.rate, 1)
        self.start = TelemetryButton('Start')
        self.start.clicked.connect(self.begin)
        row.addWidget(self.start)
        layout.addLayout(row)
        owner.installEventFilter(self)
        owner.tabs.currentChanged.connect(self.raise_prompt)
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.center()
        return super().eventFilter(watched, event)

    def center(self):
        self.adjustSize()
        self.move(max(0, (self.owner.width() - self.width()) // 2),
                  max(0, (self.owner.height() - self.height()) // 2))

    def raise_prompt(self, *_):
        if self.isVisible():
            self.raise_()

    def present(self, hz):
        index = self.rate.findData(hz)
        if index < 0:
            self.rate.addItem(f'{hz} Hz', hz)
            index = self.rate.count() - 1
        self.rate.setCurrentIndex(index)
        self.apply_theme()
        self.center()
        self.show()
        self.raise_()
        self.start.set_pulsing(True)

    def dismiss(self):
        self.start.set_pulsing(False)
        self.hide()

    def begin(self):
        if self.owner.capture.request_telemetry(True, self.rate.currentData()):
            self.dismiss()

    def apply_theme(self):
        self.setStyleSheet(f'QFrame#telemetryConnection {{background:{theme.color("panel")}; '
                          f'border:1px solid {theme.color("accent")};border-radius:8px;}}')
        self.start.apply_theme()
