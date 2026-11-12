"""A bounded crew chat with themed names and crew-colored silhouette avatars."""
from collections import deque
import random
import time

from PySide6.QtCore import QObject, QEvent, Qt, QRectF, QUrl
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QTextBlockFormat,
                          QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextBrowser, QWidget
from wirejudge.approach.approach_scopes import ScopePane
from wirejudge.ui.theme import color
from wirejudge.aircraft.aircraft_labels import aircraft_callsign
from wirejudge.approach.approach_geometry import case1_volume
from wirejudge.map.deck_crew import CrewState


class CrewLog(QTextBrowser):
    def __init__(self, kind, manager):
        super().__init__()
        self.setOpenLinks(False)
        self.anchorClicked.connect(manager.focus_member)
        self.document().setMaximumBlockCount(500)
        self.messages = deque(maxlen=500)
        self.avatars = {}
        manager.view = self

    def avatar(self, rgba):
        crew = QColor.fromRgbF(*rgba)
        key = crew.name(), color('control'), color('border')
        if key not in self.avatars:
            image = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(color('control')))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor(color('border')))
            painter.drawRect(QRectF(.5, .5, 23, 23))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(crew)
            painter.drawEllipse(QRectF(8, 4, 8, 8))
            shoulders = QPainterPath()
            shoulders.moveTo(4, 22)
            shoulders.cubicTo(4, 11, 20, 11, 20, 22)
            shoulders.closeSubpath()
            painter.drawPath(shoulders)
            painter.end()
            url = QUrl('crew-avatar:' + ''.join(value.lstrip('#') for value in key))
            self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
            self.avatars[key] = url
        return self.avatars[key]

    def insert_message(self, message):
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = QTextBlockFormat()
        block.setTopMargin(1.)
        block.setBottomMargin(1.)
        block.setLeftMargin(30.)
        block.setTextIndent(-30.)
        if not self.document().isEmpty():
            cursor.insertBlock(block)
        else:
            cursor.setBlockFormat(block)
        image = QTextImageFormat()
        image.setName(self.avatar(message['color']).toString())
        image.setWidth(24)
        image.setHeight(24)
        cursor.insertImage(image)
        name = QTextCharFormat()
        if message.get('crew_id'):
            name.setAnchor(True)
            name.setAnchorHref('crew:' + message['crew_id'])
        name.setFontWeight(700)
        name.setForeground(QColor(color('accent')))
        cursor.insertText(' ' + message['name'] + ': ', name)
        body = QTextCharFormat()
        body.setForeground(QColor(color('text')))
        cursor.insertText(message['text'], body)

    def append_message(self, message):
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        self.messages.append(message)
        self.insert_message(message)
        if at_bottom:
            bar.setValue(bar.maximum())

    def apply_theme(self):
        self.setStyleSheet(
            f'QTextBrowser {{background:{color("recessed")}; color:{color("text")};'
            f'border:1px solid {color("border")}; selection-background-color:{color("selected")};}}')
        bar = self.verticalScrollBar()
        offset, at_bottom = bar.value(), bar.value() >= bar.maximum() - 2
        self.clear()
        self.avatars.clear()
        for message in self.messages:
            self.insert_message(message)
        bar.setValue(bar.maximum() if at_bottom else offset)


class DeckCrewTool(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.lso_context = None
        self.lso_time = None
        self.lso_wires = {}
        self.lso_pending = set()
        self.last_praise = None
        self.replay_chatter_pending = False
        self.pane = ScopePane('Deck Crew Radio', 'crew', self, view_factory=CrewLog)
        counters = QWidget(self.pane)
        row = QHBoxLayout(counters)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(4)
        self.counter_labels = {}
        for title in ('Crew', 'Aircraft', 'Working', 'Huddle'):
            label = QLabel(f'{title}\n0')
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setToolTip('Aircraft on the selected carrier deck' if title == 'Aircraft' else title)
            self.counter_labels[title] = label
            row.addWidget(label, 1)
        self.pane.layout().insertWidget(1, counters)
        owner.map.deck_crew.message.connect(self.view.append_message)
        owner.map.installEventFilter(self)
        self.apply_theme()
        self.layout_panes()

    def focus_member(self, url):
        if url.scheme() == 'crew':
            self.owner.tabs.setCurrentIndex(0)
            self.owner.map.focus_crew(url.path())

    def lso_message(self, text):
        self.view.append_message(dict(name='LSO', text=text, color=(1., 1., 1., 1.)))

    def announce_record(self, pilot, caw_record):
        if self.owner.map.deck_crew.enabled:
            result = 'just set a new CAW 8 record' if caw_record else 'got a new personal best'
            self.lso_message(f'{pilot} {result}')

    def refresh_status(self, reviewing):
        crew = self.owner.map.deck_crew
        model = crew.model
        if not reviewing:
            self.replay_chatter_pending = False
        if (self.replay_chatter_pending and self.owner.tabs.currentIndex() == 0
                and model is not None and model.people):
            self.replay_chatter_pending = False
            crew.update_callsigns(crew.telemetry[2])
            model.random_chatter(time.monotonic(), immediate=True)
        if not self.pane.enabled:
            return
        people = model.people if model else ()
        active = [person['state'] for person in people if person['arrived']] if crew.simulating else ()
        counts = len(people), self.owner.map.deck_aircraft_count, active.count(CrewState.WORK), active.count(CrewState.HUDDLE)
        for (title, label), count in zip(self.counter_labels.items(), counts):
            text = f'{title}\n{count}'
            if label.text() != text:
                label.setText(text)

    def update_lso(self, attempts, stamp, context, sample=None, *, advancing=True):
        if stamp is None:
            return
        if not self.owner.map.deck_crew.enabled:
            self.lso_context, self.lso_time = None, None
            self.lso_pending.clear()
            self.lso_wires.clear()
            return
        reset = context != self.lso_context or self.lso_time is None or stamp < self.lso_time
        previous = stamp if reset else self.lso_time
        self.lso_context, self.lso_time = context, stamp
        if reset:
            self.lso_pending.clear()
            self.lso_wires.clear()

        events = []
        for attempt in attempts if advancing and not reset else ():
            if sample and attempt['connection_id'] != sample['capture']['connection_id']:
                continue
            caught, wire = attempt.get('wire_catch_s'), attempt.get('wire')
            callsign = aircraft_callsign(dict(instance_id=attempt.get('aircraft_id'),
                                             identity=attempt.get('identity') or {})) or attempt.get('pilot') or 'Aircraft'
            if caught is not None and wire and previous < caught <= stamp:
                events.append((caught, f'{callsign}, {wire} Wire!'))
                self.lso_pending.add(attempt['id'])
            elif attempt.get('outcome') == 'Bolter':
                ended = attempt.get('end_s')
                if ended is not None and previous < ended <= stamp:
                    events.append((ended, f'{callsign}, Bolter. Bolter.'))
        for _, text in sorted(events):
            self.lso_message(text)

        # A final grade may arrive asynchronously after the landing timestamp.
        # Keep checking pending landings even while the replay is paused.
        scorer = self.owner.scoring_tool
        for attempt in attempts:
            identifier = attempt['id']
            if identifier not in self.lso_pending:
                continue
            ended, grade = attempt.get('end_s'), scorer.grades.get(identifier)
            if ended is None or ended > stamp or not grade or not grade['complete']:
                continue
            self.lso_pending.discard(identifier)
            score = grade['earned'] if scorer.mode == 'Additive' else grade['deductive']
            if grade['maximum'] > 0 and score / grade['maximum'] > .9:
                lines = ("That one's going in the training video.",
                         "That trap just made the highlight reel.",
                         "Save that replay. That's how it's done.")
                choices = [line for line in lines if line != self.last_praise]
                self.last_praise = random.choice(choices)
                self.lso_message(self.last_praise)

        carriers = (sample or {}).get('carriers', [])
        carrier = next((c for c in carriers if c['instance_id'] == self.owner.current_carrier), None)
        if carrier is None:
            carrier = next((c for c in carriers if c.get('cables')), None)
        volume = case1_volume(carrier) if carrier else None
        if volume:
            wires = {}
            for cable in carrier['cables']:
                key = carrier['instance_id'], cable['instance_id']
                broken = cable.get('broken') is True
                wires[key] = broken
                if broken and advancing and not reset and not self.lso_wires.get(key, False):
                    number = volume['wire_ids'].index(cable['instance_id']) + 1
                    self.lso_message(f'{number} wire is broken!')
            self.lso_wires = wires

    def set_available(self, enabled):
        self.owner.deck_crew_button.setVisible(enabled)
        self.owner.deck_crew_button.setEnabled(enabled)
        if not enabled:
            self.owner.deck_crew_button.setChecked(False)
            self.pane.set_enabled(False)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.layout_panes()
        return False

    def layout_panes(self):
        if self.pane.detached:
            return
        view = self.owner.map
        width, height = self.pane.user_size or (390, 240)
        self.pane.place(8, max(66, view.height() - height - 8),
                        min(width, max(240, view.width() - 16)), height)
        if self.pane.isVisible():
            self.pane.raise_()
            self.owner.map_controls.raise_()

    def sync_timer(self):
        pass

    def apply_theme(self):
        self.view.apply_theme()
        for label in self.counter_labels.values():
            label.setStyleSheet(f'background:{color("control")}; border:1px solid {color("border")}; padding:2px;')
        if self.pane.floating_window:
            self.pane.floating_window.setStyleSheet(self.owner.styleSheet())

    def shutdown(self):
        if self.pane.floating_window:
            self.pane.floating_window.hide()
        self.pane.hide()
