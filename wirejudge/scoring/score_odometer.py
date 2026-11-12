"""Animated score wheels for the nearest active landing attempt."""
from array import array
from math import cos, pi, sin
from pathlib import Path
from wirejudge.paths import ASSETS_DIR
import sys
from tempfile import TemporaryDirectory
import time
import wave

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from wirejudge.ui.theme import color as theme_color


COLOR_FADE_S = .24
ROLL_S = .45
SOUND_INTERVAL_S = .14
SOUND_DIRECTORY = ASSETS_DIR / 'sounds'
# Accuracy thresholds and matching sound names.
ACCURACY_TIERS = ((.9, 'blue'), (.75, 'green'), (.5, 'orange'), (0., 'red'))
LANDING_SOUNDS = {'Wire 1': 'wire-1', 'Wire 2': 'wire-2', 'Wire 3': 'wire-3',
                  'Wire 4': 'wire-4', 'Bolter': 'bolter'}
LANDING_SOUND_WINDOW_S = .65
LANDING_HOLD_S = 7.
LANDING_FADE_S = 3.
WIRE_STAR_ROLES = {f'Wire {number}': f'wire_{number}' for number in range(1, 5)}
STAR_BURST_S = 1.7
STAR_POINTS = tuple((radius * sin(i * pi / 5), -radius * cos(i * pi / 5))
                    for i, radius in enumerate((14.4, 6.) * 5))
SPARK_INTERVAL_S = .065 / 2
SPARK_LIFE_S = .38
STAR_LIFE_S = 2 * SPARK_LIFE_S
# Digit slot, horizontal direction, vertical direction.
SPARK_PATHS = ((0, -.85, -.65), (2, .8, -.85), (1, -.4, .95),
               (3, 1., -.45), (2, .65, .8), (0, -.6, .65),
               (3, .5, .95), (1, -.8, -.95))


class ScoreOdometer(QWidget):
    edit_requested = Signal()

    def __init__(self, map_view=None):
        super().__init__()
        self.particle_overlay = ScoreParticles(map_view, self) if map_view is not None else None
        self.setMinimumSize(216, 92)
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self.tick)
        self.key = None
        self.target = None
        self.start_value = 0
        self.started = 0.
        self.quality = None
        self.color, self.sparking = self.accuracy_style(None)
        self.color_from = QColor(self.color)
        self.color_until = 0.
        self.spark_start = None
        self.spark_stop = None
        self.wire_stars = None
        self.landing_display_start = None
        self.outcome = None
        self.records = ()
        self.sounds = None
        self.active_sound = None
        self.windows_audio = None
        self.volume = 100
        self.volume_sound_dir = None
        self.scaled_sounds = {}
        self.last_sound_at = float('-inf')
        self.landing_sound_until = 0.
        self.setAccessibleName('Landing score')

    def value_at(self, now):
        fraction = min(1., max(0., (now - self.started) / ROLL_S))
        return self.start_value + fraction * (self.target - self.start_value)

    def set_reading(self, key, score, quality, *, final=False):
        target = max(0, round(score)) if score is not None else None
        now = time.monotonic()
        changed = (key, target, quality) != (self.key, self.target, self.quality)
        if not changed:
            return
        reset = key != self.key or self.target is None or target is None
        if reset:
            self.start_value = target or 0
            self.started = now
        elif target != self.target:
            self.start_value = self.value_at(now)
            self.started = now

        if reset:
            if now >= self.landing_sound_until:
                self.stop_sound()
        elif target != self.target and self.isVisible() and not final:
            self.play_score_sound(quality, now)

        color, sparking = self.accuracy_style(quality if target is not None else None)
        sparking = sparking and not final
        if reset or sparking != self.sparking:
            if sparking:
                self.spark_start, self.spark_stop = now, None
            elif reset:
                self.spark_start, self.spark_stop = None, None
            else:
                self.spark_stop = now
        if reset or color != self.color:
            fade = (not final and not reset and self.quality is not None and quality is not None
                    and not self.sparking and not sparking)
            self.color_from = self.digit_color(now) if fade else QColor(color)
            self.color_until = now + COLOR_FADE_S if fade else now
            self.color = color
        self.sparking = sparking
        self.key, self.target, self.quality = key, target, quality
        self.setAccessibleDescription('No score available' if target is None else f'{target:,} points')
        if self.isVisible() and self.is_animating(now):
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
        self.update_effects()

    def celebrate_wire(self, outcome):
        role = WIRE_STAR_ROLES.get(outcome)
        if role is not None and self.isVisible():
            now = time.monotonic()
            self.wire_stars = (now, now + STAR_BURST_S, role)
            self.timer.start()
            self.update_effects()

    def hold_landing(self, started, now):
        self.landing_display_start = started
        if self.isVisible() and self.is_animating(now) and not self.timer.isActive():
            self.timer.start()

    def clear_landing_display(self):
        self.landing_display_start = None
        self.wire_stars = None
        self.set_landing_labels()

    def set_landing_labels(self, outcome=None, records=()):
        if (outcome, records) != (self.outcome, self.records):
            self.outcome, self.records = outcome, records
            self.update()

    def landing_opacity(self, now):
        if self.landing_display_start is None:
            return 1.
        age = now - self.landing_display_start
        return max(0., min(1., 1. - (age - LANDING_HOLD_S) / LANDING_FADE_S))

    def is_animating(self, now):
        rolling = self.target is not None and self.start_value != self.target and now < self.started + ROLL_S
        sparks = self.spark_start is not None and (
            self.spark_stop is None or now < self.spark_stop + SPARK_LIFE_S)
        stars = self.wire_stars is not None and now < self.wire_stars[1] + STAR_LIFE_S
        fading = 0. < self.landing_opacity(now) < 1.
        return rolling or now < self.color_until or sparks or stars or fading

    def digit_color(self, now):
        target = QColor(self.color)
        if now >= self.color_until:
            return target
        ratio = max(0., 1. - (self.color_until - now) / COLOR_FADE_S)
        return QColor.fromRgbF(*(a + (b - a) * ratio for a, b in
                                 zip(self.color_from.getRgbF()[:3], target.getRgbF()[:3])))

    def tick(self):
        if not self.is_animating(time.monotonic()):
            self.timer.stop()
        self.update_effects()

    def update_effects(self):
        self.update()
        overlay = self.particle_overlay
        if overlay is None:
            return
        now = time.monotonic()
        if not self.isVisible() or not overlay.parentWidget().isAncestorOf(self) or not self.active_particles(now):
            overlay.hide()
            return
        origin = self.mapTo(overlay.parentWidget(), self.rect().topLeft())
        overlay.setGeometry(self.rect().translated(origin).adjusted(-110, -110, 110, 110)
                            .intersected(overlay.parentWidget().rect()))
        overlay.raise_()
        overlay.show()
        overlay.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.sounds is None:
            self.load_sounds()
        if self.is_animating(time.monotonic()):
            self.timer.start()

    def hideEvent(self, event):
        if self.particle_overlay is not None:
            self.particle_overlay.hide()
        self.timer.stop()
        self.stop_sound()
        super().hideEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.key is not None:
            self.edit_requested.emit()
        else:
            super().mouseDoubleClickEvent(event)

    def load_sounds(self):
        self.sounds = {}
        if QGuiApplication.platformName() in ('offscreen', 'minimal'):
            return
        sources = {name: f'score-{name}' for _, name in ACCURACY_TIERS}
        sources.update(LANDING_SOUNDS)
        if sys.platform == 'win32':
            import winsound
            self.windows_audio = winsound
            self.sounds = {key: SOUND_DIRECTORY / f'{name}.wav' for key, name in sources.items()}
            return
        try:
            from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
        except ImportError:
            return
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            return
        for key, name in sources.items():
            sound = QSoundEffect(device, self)
            sound.setVolume(self.volume / 100)
            sound.setSource(QUrl.fromLocalFile(str(SOUND_DIRECTORY / f'{name}.wav')))
            self.sounds[key] = sound

    def set_volume(self, volume):
        if volume == self.volume:
            return
        self.volume = volume
        if self.windows_audio is not None:
            self.stop_sound()
            self.scaled_sounds.clear()
        elif self.sounds:
            for sound in self.sounds.values():
                sound.setVolume(volume / 100)
            if not volume:
                self.stop_sound()

    def windows_sound_path(self, source):
        # PlaySound has no per-effect volume. Scale the short PCM clips without
        # touching the Windows mixer or the bundled originals.
        if self.volume == 100:
            return source
        if source not in self.scaled_sounds:
            if self.volume_sound_dir is None:
                self.volume_sound_dir = TemporaryDirectory(prefix='wirejudge-score-')
            destination = Path(self.volume_sound_dir.name) / source.name
            with wave.open(str(source), 'rb') as clip:
                params = clip.getparams()
                samples = array('h', clip.readframes(clip.getnframes()))
            scaled = array('h', (round(sample * self.volume / 100) for sample in samples))
            with wave.open(str(destination), 'wb') as clip:
                clip.setparams(params)
                clip.writeframes(scaled.tobytes())
            self.scaled_sounds[source] = destination
        return self.scaled_sounds[source]

    def stop_sound(self):
        self.landing_sound_until = 0.
        if self.active_sound is None:
            return
        if self.windows_audio is not None:
            try:
                self.windows_audio.PlaySound(None, 0)
            except RuntimeError:
                pass
        else:
            self.active_sound.stop()
        self.active_sound = None

    def play_score_sound(self, quality, now):
        if now < max(self.landing_sound_until, self.last_sound_at + SOUND_INTERVAL_S):
            return
        if self.play_sound(self.accuracy_tier(quality)):
            self.last_sound_at = now

    def play_landing_sound(self, outcome):
        if outcome in LANDING_SOUNDS and self.isVisible() and self.play_sound(outcome):
            self.landing_sound_until = time.monotonic() + LANDING_SOUND_WINDOW_S

    def play_sound(self, key):
        sound = self.sounds.get(key) if self.volume and self.sounds else None
        if sound is None:
            return False
        if self.windows_audio is not None:
            audio = self.windows_audio
            try:
                path = self.windows_sound_path(sound)
                audio.PlaySound(str(path), audio.SND_FILENAME | audio.SND_ASYNC | audio.SND_NODEFAULT)
            except (OSError, RuntimeError, wave.Error):
                return False
        else:
            if not sound.isLoaded():
                return False
            self.stop_sound()
            sound.play()
        self.active_sound = sound
        return True

    @staticmethod
    def accuracy_tier(quality):
        if quality is not None:
            for threshold, name in ACCURACY_TIERS:
                if quality >= threshold and not (name == 'blue' and quality == threshold):
                    return name
        return 'idle'

    @classmethod
    def accuracy_style(cls, quality):
        tier = cls.accuracy_tier(quality)
        return theme_color(f'score_{tier}'), tier == 'blue'

    def apply_theme(self):
        self.color, _ = self.accuracy_style(self.quality if self.target is not None else None)
        self.color_from = QColor(self.color)
        self.color_until = 0.
        self.update_effects()

    def wheel_layout(self, bounds):
        digits = 4 if self.target is None else max(4, len(str(max(self.target, round(self.start_value)))))
        area = self.wheel_area(bounds)
        height = area.height()
        width = min((bounds.width() - 14) / digits - 4, height * .72)
        left = bounds.center().x() - digits * (width + 4) / 2 + 2
        return digits, height, width, left

    def wheel_area(self, bounds):
        row_height = self.caption_height(bounds)
        return bounds.adjusted(0, 6 + row_height * len(self.records), 0, -6 - row_height)

    @staticmethod
    def caption_height(bounds):
        return max(18., min(32., bounds.height() * .13))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QColor(theme_color('border')))
        painter.setBrush(QColor(theme_color('recessed')))
        painter.drawRoundedRect(bounds, 8, 8)
        alignment = Qt.AlignmentFlag.AlignCenter
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setBold(True)
        idle = self.target is None
        now = time.monotonic()
        value = 0 if idle else self.value_at(now)
        digits, height, width, left = self.wheel_layout(bounds)
        color = self.digit_color(now)
        top = self.wheel_area(bounds).top()
        font.setPixelSize(max(12, round(min(height * .75, width * 1.3))))
        painter.setFont(font)
        for index in range(digits):
            wheel = QRectF(left + index * (width + 4), top, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme_color('raised')))
            painter.drawRoundedRect(wheel, 4, 4)
            painter.save()
            painter.setClipRect(wheel)
            painter.setPen(color)
            painter.setOpacity(self.landing_opacity(now))
            place = 10 ** (digits - index - 1)
            digit = int(value // place) % 10
            roll = max(0., value % place - (place - 1))
            shifted = wheel.translated(0, -height * roll)
            painter.drawText(shifted, alignment, 'X' if idle else str(digit))
            if roll:
                painter.drawText(shifted.translated(0, height), alignment, str((digit + 1) % 10))
            painter.restore()
        row_height = self.caption_height(bounds)
        font = self.font()
        font.setBold(True)
        font.setPixelSize(round(row_height * .72))
        painter.setFont(font)
        painter.save()
        painter.setOpacity(self.landing_opacity(now))
        painter.setPen(QColor(theme_color('score_blue')))
        for index, caption in enumerate(self.records):
            row = QRectF(bounds.left(), bounds.top() + 4 + index * row_height, bounds.width(), row_height)
            painter.drawText(row, alignment, caption)
        if self.outcome:
            role = WIRE_STAR_ROLES.get(self.outcome, 'warning')
            painter.setPen(QColor(theme_color(role)))
            caption = 'BOLTER!' if self.outcome == 'Bolter' else f'{self.outcome.split()[-1]} Wire!'
            row = QRectF(bounds.left(), bounds.bottom() - row_height - 3, bounds.width(), row_height)
            painter.drawText(row, alignment, caption)
        painter.restore()
        if self.particle_overlay is None or not self.particle_overlay.isVisible():
            painter.setClipRect(bounds.adjusted(2, 2, -2, -2))
            self.paint_particles(painter, bounds, now)

    def active_particles(self, now):
        if self.wire_stars is not None and now < self.wire_stars[1] + STAR_LIFE_S:
            start, stop, role = self.wire_stars
            return start, stop, theme_color(role)
        if self.spark_start is not None and (self.spark_stop is None or now < self.spark_stop + SPARK_LIFE_S):
            return self.spark_start, self.spark_stop, None
        return None

    def paint_particles(self, painter, bounds, now):
        particles = self.active_particles(now)
        if particles:
            digits, _, width, left = self.wheel_layout(bounds)
            self.draw_sparks(painter, self.wheel_area(bounds), now, left, width, digits, *particles)

    def draw_sparks(self, painter, bounds, now, left, width, digits, start, stop, star_color=None):
        elapsed = now - start
        life = STAR_LIFE_S if star_color is not None else SPARK_LIFE_S
        earliest = max(0, int((elapsed - life) / SPARK_INTERVAL_S) + 1)
        last_emission = elapsed if stop is None else min(elapsed, stop - start)
        latest = int(last_emission / SPARK_INTERVAL_S)
        if latest < earliest:
            return

        trail = QPen(QColor(theme_color('spark_trail')), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        head = QPen(QColor(theme_color('spark_head')), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for emission in range(earliest, latest + 1):
            slot, vx, vy = SPARK_PATHS[emission % len(SPARK_PATHS)]
            age = (elapsed - emission * SPARK_INTERVAL_S) / life
            if not 0 <= age < 1:
                continue
            digit = slot * (digits - 1) // 3
            start_x = left + digit * (width + 4) + width * (.5 + .22 * vx)
            start_y = bounds.center().y() + 8 * vy
            dx = 78 * vx * age
            dy = 69 * vy * age + 18 * age * age
            point = QPointF(start_x + dx, start_y + dy)
            tail = QPointF(point.x() - 12 * vx * age, point.y() - 12 * vy * age)
            opacity = (1 - age) ** 1.5
            if star_color is not None:
                painter.setOpacity(opacity)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(star_color))
                painter.drawPolygon(QPolygonF([QPointF(point.x() + x, point.y() + y)
                                               for x, y in STAR_POINTS]))
                continue
            painter.setOpacity(.8 * opacity)
            painter.setPen(trail)
            painter.drawLine(tail, point)
            painter.setOpacity(opacity)
            painter.setPen(head)
            painter.drawPoint(point)


class ScoreParticles(QWidget):
    """Paint particles above the docked score card without intercepting clicks."""
    def __init__(self, map_view, odometer):
        super().__init__(map_view)
        self.odometer = odometer
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def paintEvent(self, event):
        if not self.parentWidget().isAncestorOf(self.odometer):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = self.odometer.mapTo(self.parentWidget(), self.odometer.rect().topLeft())
        painter.translate(origin - self.pos())
        bounds = QRectF(self.odometer.rect()).adjusted(1, 1, -1, -1)
        self.odometer.paint_particles(painter, bounds, time.monotonic())
