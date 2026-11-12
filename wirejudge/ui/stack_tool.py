"""Compact shared altitude bar with draggable flight assignments."""
import json
import math
import time
from PySide6.QtCore import Qt,QObject,QEvent,QRectF,QPointF,QTimer
from PySide6.QtGui import QPainter,QPen,QColor,QFont
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QPushButton
from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.aircraft.aircraft_labels import aircraft_label
from wirejudge.display_units import ALTITUDE_FACTORS
from wirejudge.approach.approach_scopes import ScopePane
from wirejudge.ui.theme import color as theme_color

MAX_ALT_M=15000*.3048
DEVIATION_FEET_M=500*.3048
DEVIATION_METRIC_M=200.


def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def bounds(widget):return 32.,max(33.,widget.height()-20.)


def altitude_y(widget,metres):
    top,bottom=bounds(widget);m=widget.manager
    fraction=(metres-m.min_m)/(m.max_m-m.min_m)
    return bottom-max(0.,min(1.,fraction))*(bottom-top)


def altitude_at(widget,y):
    top,bottom=bounds(widget);m=widget.manager
    return max(m.min_m,min(m.max_m,m.min_m+(bottom-y)/(bottom-top)*(m.max_m-m.min_m)))


def spread_labels(items,top,bottom):
    """Keep labels in order and spread crowded neighbors within the canvas."""
    ordered=sorted(items,key=lambda item:(item[1],str(item[0])))
    if not ordered:return {}
    gap=min(18.,(bottom-top)/max(1,len(ordered)-1))
    positions=[]
    for key,y in ordered:positions.append([key,max(top,y,positions[-1][1]+gap if positions else top)])
    if positions[-1][1]>bottom:
        positions[-1][1]=bottom
        for i in range(len(positions)-2,-1,-1):positions[i][1]=min(positions[i][1],positions[i+1][1]-gap)
    return dict(positions)


class StackAxis(QWidget):
    def __init__(self,manager):
        super().__init__();self.manager=manager;self.flight_hits=[];self.aircraft_hits=[]
        self.drag_group=None;self.drag_offset=0.;self.selected_group=None
        self.setMouseTracking(True);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Stack altitude bar and flight assignment handles')
        self.setMinimumSize(180,170)
    def edges(self):
        center=self.width()*.63
        return center-16,center+16
    def paintEvent(self,event):
        m=self.manager;units=m.units;top,bottom=bounds(self);left,right=self.edges();center=(left+right)/2
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor(theme_color('map')));p.setFont(QFont('Segoe UI',8))
        p.setPen(QColor(theme_color('muted')))
        divisor=1000 if units.altitude_name=='Feet' else (100 if units.altitude_name=='Meters' else 1000)
        heading='kft ASL' if units.altitude_name=='Feet' else f'×{divisor} {units.altitude_symbol} ASL'
        p.drawText(QRectF(center-65,2,130,18),Qt.AlignmentFlag.AlignCenter,heading)
        p.setPen(QPen(QColor(theme_color('border')),1));p.setBrush(QColor(theme_color('panel')))
        p.drawRoundedRect(QRectF(left,top-8,right-left,bottom-top+16),3,3)
        factor=ALTITUDE_FACTORS[units.altitude_name];low=units.altitude(m.min_m);high=units.altitude(m.max_m)
        first=math.ceil(low/divisor-1e-9);last=math.floor(high/divisor+1e-9)
        pixels=(bottom-top)/max(1,(high-low)/divisor)
        label_every=max(1,math.ceil(17/max(pixels,.001)))
        # Draw thin assignment bands first so centered scale text stays readable.
        for group,color in m.group_colors.items():
            assigned=m.assignments[group]
            if not m.min_m<=assigned<=m.max_m:continue
            y=altitude_y(self,assigned);band=QColor.fromRgbF(*color);band.setAlphaF(.18)
            p.fillRect(QRectF(left,y-4,right-left,8),band)
            line=QColor.fromRgbF(*color);line.setAlphaF(.85);p.setPen(QPen(line,2))
            p.drawLine(QPointF(left,y),QPointF(right,y))
        p.setPen(QColor(theme_color('text')))
        for value in range(first,last+1):
            y=altitude_y(self,value*divisor/factor)
            p.drawLine(QPointF(left,y),QPointF(left+3,y));p.drawLine(QPointF(right-3,y),QPointF(right,y))
            if value%label_every==0:p.drawText(QRectF(left+3,y-8,right-left-6,16),Qt.AlignmentFlag.AlignCenter,str(value))
        # Exact upper bound remains explicit even between scale markings.
        if abs(high/divisor-round(high/divisor))>1e-6:
            p.drawText(QRectF(left-7,top-22,right-left+14,16),Qt.AlignmentFlag.AlignCenter,f'{high/divisor:g}')
        if m.min_m<=m.marshal_m<=m.max_m:
            y=altitude_y(self,m.marshal_m);p.setPen(QPen(QColor(theme_color('accent')),1.5,Qt.PenStyle.DashLine))
            p.drawLine(QPointF(2,y),QPointF(left-2,y));p.drawLine(QPointF(right+2,y),QPointF(self.width()-2,y))
            p.drawText(QRectF(2,y-17,max(1,left-5),16),Qt.AlignmentFlag.AlignLeft,'M')
        self.flight_hits=[];self.aircraft_hits=[]
        assignments=spread_labels([(g,altitude_y(self,m.assignments[g])) for g in m.group_colors],top,bottom)
        for group,y in assignments.items():
            assigned=m.assignments[group];actual_y=altitude_y(self,assigned);c=QColor.fromRgbF(*m.group_colors[group])
            box=QRectF(3,y-8,max(1,left-3),16)
            p.setPen(QPen(c,1.5 if group==self.selected_group else 1));p.setBrush(QColor(theme_color('panel')))
            p.drawRoundedRect(box,3,3)
            prefix='↑ ' if assigned>m.max_m else ('↓ ' if assigned<m.min_m else '')
            p.fillRect(QRectF(box.left()+1,box.top()+2,3,box.height()-4),c)
            p.setPen(QColor(theme_color('text')))
            p.drawText(box.adjusted(6,0,-2,0),Qt.AlignmentFlag.AlignCenter,prefix+group.removesuffix(' Flight'))
            if abs(y-actual_y)>1:
                p.setPen(QPen(c,1));p.drawLine(QPointF(box.right(),y),QPointF(left+2,actual_y))
            self.flight_hits.append((box,group))
        labels=spread_labels([(r['id'],altitude_y(self,r['alt'])) for r in m.aircraft],top,bottom)
        for row in m.aircraft:
            actual_y=altitude_y(self,row['alt']);y=labels[row['id']];c=m.aircraft_color(row)
            p.setPen(QPen(c,1));p.setBrush(c);p.drawEllipse(QPointF(right+5,actual_y),3,3)
            if abs(y-actual_y)>1:p.drawLine(QPointF(right+8,actual_y),QPointF(right+14,y))
            suffix=' ↑' if row['alt']>m.max_m else (' ↓' if row['alt']<m.min_m else '')
            rect=QRectF(right+15,y-8,max(1,self.width()-right-17),16)
            p.drawText(rect,Qt.AlignmentFlag.AlignLeft,row['label']+suffix)
            self.aircraft_hits.append((rect.adjusted(-1,-1,0,1),row))
            self.aircraft_hits.append((QRectF(right,actual_y-5,11,10),row))
        if not m.aircraft:
            p.setPen(QColor(theme_color('muted')));p.drawText(QRectF(right+5,top+15,max(1,self.width()-right-7),80),Qt.TextFlag.TextWordWrap,'No airborne aircraft')
    def assignment_step(self):return self.manager.assignment_step()
    def set_assignment(self,group,value):
        step=self.assignment_step()
        low=math.ceil(self.manager.min_m/step-1e-9)*step;high=math.floor(self.manager.max_m/step+1e-9)*step
        value=max(low,min(high,round(value/step)*step))
        self.manager.assignments[group]=round(value,3);self.update();self.manager.sync_timer()
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        group=next((g for rect,g in reversed(self.flight_hits) if rect.contains(event.position())),None)
        if group:
            self.drag_group=group;self.selected_group=group;self.drag_offset=event.position().y()-altitude_y(self,self.manager.assignments[group])
            self.setFocus();self.setCursor(Qt.CursorShape.SizeVerCursor);self.update();event.accept();return
        row=next((r for rect,r in reversed(self.aircraft_hits) if rect.contains(event.position())),None)
        if row:self.manager.owner.aircraft_map_clicked(row['id']);event.accept()
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            row = next((row for rect, row in reversed(self.aircraft_hits) if rect.contains(event.position())), None)
            if row:
                self.manager.owner.focus_aircraft(row['id'])
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self,event):
        m=self.manager
        if self.drag_group:
            self.set_assignment(self.drag_group,altitude_at(self,event.position().y()-self.drag_offset));event.accept();return
        group=next((g for rect,g in reversed(self.flight_hits) if rect.contains(event.position())),None)
        if group:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            self.setToolTip(f'{group}: {m.units.altitude(m.assignments[group]):,.0f} {m.units.altitude_symbol} ASL assigned · drag to change');return
        self.unsetCursor()
        row=next((r for rect,r in reversed(self.aircraft_hits) if rect.contains(event.position())),None)
        if row:
            assigned=m.assignments[row['group']]
            self.setToolTip(f"{row['label']}: {m.units.altitude(row['alt']):,.0f} {m.units.altitude_symbol} ASL\nAssigned {m.units.altitude(assigned):,.0f} · deviation {m.units.altitude(row['alt']-assigned):+,.0f} {m.units.altitude_symbol}")
        else:self.setToolTip('Scale numbers are thousands of feet or hundreds of meters. Flight boxes are draggable assignment handles.')
    def mouseReleaseEvent(self,event):
        if self.drag_group:
            self.set_assignment(self.drag_group,altitude_at(self,event.position().y()-self.drag_offset));self.drag_group=None;self.unsetCursor();self.manager.save_assignments();event.accept()
    def keyPressEvent(self,event):
        group=self.selected_group
        if group in self.manager.group_colors and event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down,Qt.Key.Key_PageUp,Qt.Key.Key_PageDown):
            amount=self.assignment_step()*(10 if event.key() in (Qt.Key.Key_PageUp,Qt.Key.Key_PageDown) else 1)
            if event.key() in (Qt.Key.Key_Down,Qt.Key.Key_PageDown):amount=-amount
            self.set_assignment(group,self.manager.assignments[group]+amount);self.manager.save_assignments();event.accept()
        else:super().keyPressEvent(event)


class StackView(QWidget):
    def __init__(self,kind,manager):
        super().__init__();self.manager=manager;manager.view=self
        col=QVBoxLayout(self);col.setContentsMargins(2,2,2,2);col.setSpacing(2)
        row=QHBoxLayout();row.setContentsMargins(0,0,0,0);row.setSpacing(3)
        self.minus=QPushButton('−');self.plus=QPushButton('+');self.fit=QPushButton('Fit')
        for b in (self.minus,self.plus,self.fit):b.setStyleSheet('QPushButton {padding:3px 8px;}');row.addWidget(b)
        self.minus.setToolTip('Zoom out · increase maximum altitude');self.plus.setToolTip('Zoom in · decrease maximum altitude')
        self.fit.setToolTip('Fit every airborne aircraft. Pulses when aircraft are outside the visible altitude range.')
        self.minus.clicked.connect(lambda:manager.zoom(1));self.plus.clicked.connect(lambda:manager.zoom(-1));self.fit.clicked.connect(manager.fit_aircraft)
        row.addStretch();col.addLayout(row);self.axis=StackAxis(manager);col.addWidget(self.axis,1)
        self.setMinimumSize(184,220)
    def refresh(self):
        m=self.manager
        for group in m.group_colors:m.assignments.setdefault(group,round(m.marshal_m/m.assignment_step())*m.assignment_step())
        increment=f'{m.units.altitude(m.scale_step()):,.0f} {m.units.altitude_symbol}'
        self.minus.setToolTip('Zoom out · increase maximum altitude by '+increment)
        self.plus.setToolTip('Zoom in · decrease maximum altitude by '+increment)
        if self.axis.drag_group and self.axis.drag_group not in m.group_colors:self.axis.drag_group=None;self.axis.unsetCursor()
        self.axis.update();m.sync_timer()
    def showEvent(self,event):super().showEvent(event);QTimer.singleShot(0,self.manager.sync_timer)
    def hideEvent(self,event):super().hideEvent(event);QTimer.singleShot(0,self.manager.sync_timer)


class StackTool(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.units=owner.units;self.marshal_m=2000*.3048
        self.aircraft=[];self.group_colors={};self.assignments={};self.display_key=None
        self.min_m=0.;self.max_m=MAX_ALT_M;self.out_of_view=False;self.has_deviation=False;self.alert_phase=0.
        try:saved=json.loads(owner.settings.store.value('stack/assignments','{}'))
        except (ValueError,TypeError):saved={}
        if isinstance(saved,dict):self.assignments={key:float(value) for key,value in saved.items() if isinstance(key,str) and finite(value) and abs(value)<=100000}
        try:saved_range=json.loads(owner.settings.store.value('stack/range_m','null'))
        except (ValueError,TypeError):saved_range=None
        if isinstance(saved_range,list) and len(saved_range)==2 and all(finite(v) for v in saved_range) and -10000<=saved_range[0]<saved_range[1]<=100000:self.min_m,self.max_m=saved_range
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.pulse)
        self.pane=ScopePane('Stack','stack',self,view_factory=StackView)
        owner.map.installEventFilter(self);self.layout_panes()
    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.layout_panes()
        if event.type() in (QEvent.Type.Show,QEvent.Type.Hide):QTimer.singleShot(0,self.sync_timer)
        return False
    def layout_panes(self):
        if not hasattr(self,'pane') or self.pane.detached:return
        view=self.owner.map;w,h=self.pane.user_size or (190,max(260,view.height()-250))
        self.pane.place(view.width()-w-8,8,min(w,max(190,view.width()-16)),min(h,max(240,view.height()-24)))
        self.pane.raise_()
        if hasattr(self.owner,'map_controls'):self.owner.map_controls.raise_()
    def set_frame(self,sample,case,units):
        self.units=units;self.marshal_m=(6000 if case=='Case 3' else 2000)*.3048
        rows=[];colors={}
        for aircraft in (sample or {}).get('aircraft',[]):
            info=(aircraft.get('kinematics') or {}).get('flight_info') or {};alt=info.get('altitudeASL')
            if info.get('isLanded') is not False or not finite(alt):continue
            group=flight_identity(aircraft)[0];color=self.owner.map.color_for(aircraft,sample)
            short=aircraft_label(aircraft,['flight'],units,None,case,3.5)[0][0]
            rows.append(dict(id=aircraft['instance_id'],group=group,label=short,alt=alt,color=color));colors[group]=color
        if rows!=self.aircraft or colors!=self.group_colors or self.display_key!=(units.altitude_name,case):
            self.aircraft,self.group_colors=rows,colors;self.display_key=(units.altitude_name,case);self.view.refresh()
    def assignment_step(self):return 1000*.3048 if self.units.altitude_name=='Feet' else 100.
    def scale_step(self):return 1000*.3048 if self.units.altitude_name=='Feet' else 200.
    def zoom(self,direction):
        step=self.scale_step()
        self.max_m=min(100000.,max(self.min_m+step,self.max_m+direction*step))
        self.save_range();self.view.refresh()
    def fit_aircraft(self):
        step=self.scale_step();values=[row['alt'] for row in self.aircraft]
        if not values:return
        lo=min(values);hi=max(values)
        self.min_m=max(-10000.,min(0.,math.floor(lo/step)*step))
        margin=max(step*.1,(hi-self.min_m)*.05)
        self.max_m=max(self.min_m+step,math.ceil((hi+margin)/step)*step)
        self.save_range();self.view.refresh()
    def save_range(self):
        self.owner.settings.store.setValue('stack/range_m',json.dumps([self.min_m,self.max_m]));self.owner.settings.store.sync()
    def deviating(self,row):
        assigned=self.assignments.get(row['group'])
        return assigned is not None and abs(row['alt']-assigned)>(DEVIATION_FEET_M if self.units.altitude_name=='Feet' else DEVIATION_METRIC_M)+1e-6
    def aircraft_color(self,row):
        rgb=row['color'][:3]
        if self.deviating(row):
            warning=QColor(theme_color('warning')).getRgbF()[:3]
            rgb=tuple(value+(target-value)*self.alert_phase for value,target in zip(rgb,warning))
        return QColor.fromRgbF(*rgb)
    def sync_timer(self):
        if not hasattr(self,'pane'):return
        self.out_of_view=any(r['alt']<self.min_m or r['alt']>self.max_m for r in self.aircraft)
        self.has_deviation=any(self.deviating(r) for r in self.aircraft)
        active=(self.out_of_view or self.has_deviation) and self.view.isVisible()
        if active and not self.timer.isActive():self.timer.start()
        elif not active:self.timer.stop()
        self.pulse()
    def pulse(self):
        self.alert_phase=(1-math.cos(time.monotonic()*math.tau/1.8))/2
        if self.has_deviation and self.view.isVisible():self.view.axis.update()
        button=self.view.fit
        if not self.out_of_view:
            button.setStyleSheet('QPushButton {padding:3px 8px;}');return
        amount=.12+.36*(1-math.cos(time.monotonic()*math.tau/2.4))/2
        panel=QColor(theme_color('control'));accent=QColor(theme_color('accent'))
        mixed=QColor.fromRgbF(*[panel.getRgbF()[i]*(1-amount)+accent.getRgbF()[i]*amount for i in range(3)])
        button.setStyleSheet(f'QPushButton {{padding:3px 8px;background:{mixed.name()};border:1px solid {accent.name()};color:{theme_color("text")};}}')
    def save_assignments(self):
        self.owner.settings.store.setValue('stack/assignments',json.dumps(self.assignments));self.owner.settings.store.sync()
    def apply_theme(self):
        if self.pane.floating_window:self.pane.floating_window.setStyleSheet(self.owner.styleSheet())
        self.view.refresh()
    def shutdown(self):
        self.timer.stop();self.save_assignments();self.save_range()
        if self.pane.floating_window:self.pane.floating_window.hide()
        self.pane.hide()
