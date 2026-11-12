from wirejudge.ui.theme import color as theme_color
"""Custom DVR timeline with a proportional ruler and recovery markers."""
import math
from PySide6.QtCore import Qt,Signal,QRectF,QEvent,QPointF
from PySide6.QtGui import QPainter,QColor,QPen,QFont
from PySide6.QtWidgets import QSlider,QToolTip


def recovery_events(catalog):
    events=[]
    for a in catalog:
        wire=a.get('wire');when=a.get('wire_catch_s');symbol=None
        if wire in (1,2,3,4) and when is not None:symbol=str(wire)
        elif a.get('outcome')=='Bolter':symbol='B';when=a.get('end_s')
        elif a.get('outcome')=='Aborted':symbol='A';when=a.get('end_s')
        if symbol is not None and when is not None:
            events.append(dict(id=a['id'],time=when,symbol=symbol,aircraft_id=a['aircraft_id'],
                               source=a['source'],pilot=a['pilot'],identity=a.get('identity') or {}))
    return sorted(events,key=lambda e:(e['time'],e['id']))


class EventTimeline(QSlider):
    marker_clicked=Signal(float)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setAccessibleName('Replay timeline');self.setFixedHeight(88);self.time_labels=('00:00:00','00:00:00','00:00:00');self.events=[];self.bounds=None;self.hits=[]

    def track_bounds(self):
        return 10., max(11., self.width() - 10.)

    def x_for_fraction(self, fraction):
        left, right = self.track_bounds()
        if self.invertedAppearance() != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft):
            fraction = 1 - fraction
        return left + (right - left) * fraction

    def set_events(self,events,bounds):
        if events==self.events and bounds==self.bounds:return
        self.events,self.bounds=events,bounds;self.update()

    def set_times(self,start,end,current):
        values=(start,end,current)
        if values!=self.time_labels:self.time_labels=values;self.update()

    def paintEvent(self,event):
        self.hits=[]
        start,end=self.bounds or (0,1)
        left,right=self.track_bounds()
        fraction = (self.sliderPosition() - self.minimum()) / max(1, self.maximum() - self.minimum())
        x_head = self.x_for_fraction(fraction)
        origin = self.x_for_fraction(0)
        lanes=[-999.,-999.]
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(theme_color('panel')))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(theme_color('accent' if self.isEnabled() else 'muted'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme_color('border')))
        painter.drawRoundedRect(QRectF(left,42,right-left,7),3.5,3.5)
        if self.isEnabled():
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(min(origin,x_head),42,abs(x_head-origin),7),3.5,3.5)
        duration = end - start
        if self.bounds and duration > 0:
            desired = duration / max(2, (right-left)/65)
            magnitude = 10 ** math.floor(math.log10(desired))
            step = next(magnitude * scale for scale in (1,2,5,10) if magnitude * scale >= desired)
            painter.setPen(QColor(theme_color('muted')))
            for i in range(1, math.floor(duration / step) + 1):
                x = self.x_for_fraction(i * step / duration)
                painter.drawLine(QPointF(x,51), QPointF(x,55))
        painter.setPen(QPen(accent, 1.5))
        painter.drawLine(QPointF(x_head,36), QPointF(x_head,55))
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(x_head,45.5),5,5)
        font=QFont(self.font());font.setPointSizeF(8);font.setBold(True);painter.setFont(font)
        for marker in self.events if self.bounds and end>start else []:
            if not start<=marker['time']<=end:continue
            fraction=(marker['time']-start)/(end-start)
            x=self.x_for_fraction(fraction)
            lane=0 if abs(x-lanes[0])>=18 else 1
            lanes[lane]=x;y=9+18*lane;rect=QRectF(x-7.5,y-7.5,15,15)
            painter.setPen(QPen(QColor(theme_color('#10151B')),1));painter.setBrush(QColor.fromRgbF(*marker['color']))
            painter.drawEllipse(rect);painter.setPen(QColor(theme_color('#10151B')))
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,marker['symbol'])
            painter.setPen(QPen(QColor.fromRgbF(*marker['color']),1))
            painter.drawLine(int(x),int(y+8),int(x),37)
            self.hits.append((rect,marker))
        painter.setPen(QColor(theme_color('#93A4B1')));font.setBold(False);painter.setFont(font)
        start_text,end_text,current_text=self.time_labels
        painter.drawText(QRectF(0,self.height()-18,self.width(),16),Qt.AlignmentFlag.AlignLeft,start_text)
        painter.drawText(QRectF(0,self.height()-18,self.width(),16),Qt.AlignmentFlag.AlignRight,end_text)
        width=painter.fontMetrics().horizontalAdvance(current_text)+12
        x=max(0,min(self.width()-width,x_head-width/2))
        painter.setPen(QColor(theme_color('#E1E8ED')))
        painter.drawText(QRectF(x,55,width,16),Qt.AlignmentFlag.AlignCenter,current_text)
        painter.end()

    def marker_at(self,point):
        return next((marker for rect,marker in reversed(self.hits) if rect.contains(point)),None)

    def mousePressEvent(self,event):
        marker=self.marker_at(event.position())
        if marker and event.button()==Qt.MouseButton.LeftButton:
            self.marker_clicked.emit(marker['time']);event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and self.isEnabled():
            self.setSliderDown(True);self.seek_at(event.position().x());event.accept();return
        super().mousePressEvent(event)

    def seek_at(self,x):
        left,right = self.track_bounds()
        fraction = max(0., min(1., (x-left)/(right-left)))
        if self.x_for_fraction(0) == right:
            fraction = 1 - fraction
        self.setValue(round(self.minimum() + fraction * (self.maximum()-self.minimum())))

    def mouseMoveEvent(self,event):
        if self.isSliderDown():self.seek_at(event.position().x());event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.isSliderDown():
            self.seek_at(event.position().x());self.setSliderDown(False);event.accept();return
        super().mouseReleaseEvent(event)

    def event(self,event):
        if event.type()==QEvent.Type.ToolTip:
            marker=self.marker_at(event.pos())
            if marker:
                outcome={'A':'Abort','B':'Bolter'}.get(marker['symbol'],'Wire '+marker['symbol'])
                seconds=max(0,marker['time']-self.bounds[0])
                QToolTip.showText(event.globalPos(),f"{marker['pilot']} · {outcome} · {int(seconds//60):02d}:{seconds%60:04.1f}",self)
                return True
        return super().event(event)
