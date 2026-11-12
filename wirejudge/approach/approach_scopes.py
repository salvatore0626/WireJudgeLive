"""Two lightweight picture-in-picture views sharing the map's displayed telemetry."""
import json
import math
import time
from PySide6.QtCore import Qt,QRectF,QPointF,QEvent,QTimer,QObject
from PySide6.QtGui import QPainter,QPen,QColor,QFont,QPolygonF
from PySide6.QtWidgets import QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QMainWindow,QSlider
from wirejudge.approach.approach_geometry import NM,ILS_GS_FULL_SCALE_DEG,ILS_LOC_FULL_SCALE_DEG
from wirejudge.approach.approach_status import approach_states,scope_projection,status_color,scope_appearance,landing_scope_state,COLOR_LOC_LIMIT_DEG
from wirejudge.display_units import DisplayUnits
from wirejudge.ui.theme import color as theme_color
from wirejudge.ui.control_icons import control_icon

GLIDE_ZOOM_NM = (.5, .75, 1., 2., 3., 5.)


class GlideZoomTrack(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setFixedSize(56, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, y = 5, self.width()-5, self.height()/2
        painter.setPen(QPen(QColor(theme_color('muted')), 1))
        painter.drawLine(QPointF(left,y),QPointF(right,y))
        for x in (left,right):
            painter.drawLine(QPointF(x,y-4),QPointF(x,y+4))
        fraction = self.view.zoom_index / (len(GLIDE_ZOOM_NM)-1)
        painter.setBrush(QColor(theme_color('panel')))
        painter.setPen(QPen(QColor(theme_color('accent')), 1.5))
        painter.drawEllipse(QPointF(right-(right-left)*fraction,y),3.5,3.5)


class ScopeView(QWidget):
    def __init__(self,kind,manager):
        super().__init__();self.kind,self.manager=kind,manager
        self.setMinimumSize(180,110)
        self.setToolTip('Aircraft use AoA colors on final; Case 3 level legs use IAS. Warning aircraft pulse red. Click to select or double-click to follow an aircraft.')
        self.hits=[];self.hit_radii={};self.depth_range=(0.,3.)
        if kind == 'slice':
            self.custom_zoom = None
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0,0,8,3)
            layout.addStretch()
            zoom = QHBoxLayout()
            zoom.setSpacing(3)
            zoom.addStretch()
            self.zoom_out = QPushButton('−')
            self.zoom_in = QPushButton('+')
            self.zoom_track = GlideZoomTrack(self)
            for button, title, delta in ((self.zoom_out, 'Zoom out', 1), (self.zoom_in, 'Zoom in', -1)):
                button.setFixedSize(22,22)
                button.setStyleSheet('padding: 0;')
                button.setToolTip(title)
                button.setAccessibleName(title)
                button.clicked.connect(lambda checked=False, step=delta: self.change_zoom(step))
            zoom.addWidget(self.zoom_out)
            zoom.addWidget(self.zoom_track)
            zoom.addWidget(self.zoom_in)
            layout.addLayout(zoom)
            self.sync_zoom()

    @property
    def zoom_index(self):
        if self.custom_zoom is not None:
            return self.custom_zoom
        return 4 if self.manager.case == 'Case 3' else 3

    @property
    def range_nm(self):
        return GLIDE_ZOOM_NM[self.zoom_index]

    def change_zoom(self, step):
        self.custom_zoom = max(0,min(len(GLIDE_ZOOM_NM)-1,self.zoom_index+step))
        self.sync_zoom()
        self.update()

    def sync_zoom(self):
        self.zoom_in.setEnabled(self.zoom_index > 0)
        self.zoom_out.setEnabled(self.zoom_index < len(GLIDE_ZOOM_NM)-1)
        self.zoom_track.setToolTip(f'Glide Slope range: {self.range_nm:g} NM')
        self.zoom_track.update()

    def text(self,p,rect,text,align=Qt.AlignmentFlag.AlignCenter,color=None):
        p.setPen(QColor(color or theme_color('#93A4B1')));p.drawText(rect,int(align),text)

    def paintEvent(self,event):
        m=self.manager;p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor(theme_color('#10151B')));font=QFont(self.font());font.setPointSizeF(8);p.setFont(font)
        self.hits=[];self.hit_radii={}
        if m.volume is None:
            self.text(p,QRectF(self.rect()),'Waiting for carrier / wire 3 geometry');return
        rect = self.plot_rect()
        p.setPen(QPen(QColor(theme_color('#2B3843')),1));p.drawRect(rect)
        if self.kind=='slice':self.draw_slice(p,rect)
        else:self.draw_scope(p,rect)

    def plot_rect(self):
        return QRectF(48, 7, max(1, self.width()-62),
                      max(1, self.height()-7-(44 if self.kind == 'slice' else 24)))

    def scope_contacts(self, rect, focal):
        center = rect.center()
        contacts = []
        for state in self.manager.states.values():
            if not 0 < state['dme'] <= 3.5 + 1e-9:
                continue
            point = scope_projection(state, self.manager.glide)
            if point is not None:
                pos = QPointF(center.x() + focal*point[0], center.y() - focal*point[1])
                if rect.contains(pos):
                    contacts.append((state, pos))
        return contacts

    @staticmethod
    def focal_length(rect):
        return min(rect.width()/(2*math.tan(math.radians(6))),
                   rect.height()/(2*math.tan(math.radians(4))))

    def symbol(self,p,position,state):
        m=self.manager;base=m.owner.map.flight_colors.get(m.source,state['aircraft'])
        color_state=landing_scope_state(state) if self.kind=='scope' else state
        color=QColor.fromRgbF(*status_color(base,color_state,m.phase,theme_color('#FF626A')))
        if self.kind=='scope':
            angle=math.radians(m.glide)
            depth=state['aft']*math.cos(angle)+state['height']*math.sin(angle)
            radius,opacity=scope_appearance(state['dme'],*self.depth_range,
                focal_pixels=getattr(self,'scope_focal',1000.),max_span_px=getattr(self,'scope_max_span',144.),depth_m=depth)
        else:radius,opacity=3.5*2.25,1.
        if state['aircraft'].get('_radar_stale'):
            opacity *= .45
        p.save();p.setOpacity(opacity);p.setPen(QPen(QColor.fromRgbF(*base),2 if self.kind=='slice' and state.get('pitch') is not None else 3));p.setBrush(color)
        if self.kind=='slice' and state.get('pitch') is not None:
            p.save();p.translate(position);p.rotate(-state['pitch'])
            p.drawPolygon(QPolygonF([QPointF(radius*4/3,0),QPointF(-radius*2/3,-radius),QPointF(-radius*2/3,radius)]))
            p.restore()
        else:
            # A circle conveys position without inventing a level attitude.
            p.drawEllipse(position,radius,radius)
        if self.kind=='scope' and state.get('nose_on'):
            p.save();p.translate(position);p.rotate(-(state.get('roll') or 0));p.setPen(QPen(QColor.fromRgbF(*base),3))
            p.drawLine(QPointF(-radius*3,0),QPointF(-radius,0));p.drawLine(QPointF(radius,0),QPointF(radius*3,0))
            p.drawLine(QPointF(0,-radius),QPointF(0,-radius*2.6));p.restore()
        if state['id']==m.selected:
            p.setPen(QPen(QColor(theme_color('#E1E8ED')),1));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(position,(radius*4/3 if self.kind=='slice' else radius)+3,(radius*4/3 if self.kind=='slice' else radius)+3)
        p.setPen(QColor.fromRgbF(*base));p.drawText(QPointF(position.x()+radius+6,position.y()+13),state['label'])
        if state['warnings'] and not state.get('resting'):
            p.setPen(QColor(theme_color('#FF626A')));p.drawText(QPointF(position.x()+radius+6,position.y()+25),' / '.join(state['warnings']))
        p.restore();self.hits.append((position,state['id']))
        self.hit_radii[state['id']]=max(18.,radius*3 if self.kind=='scope' and state.get('nose_on') else radius*4/3)

    def draw_slice(self,p,r):
        m=self.manager;range_nm=self.range_nm;maximum=range_nm*NM
        high=max(50,maximum*math.tan(math.radians(m.glide+ILS_GS_FULL_SCALE_DEG)))*1.05
        low=-5
        rough=m.units.altitude(high)/4;power=10**math.floor(math.log10(rough))
        tick=next(mult*power for mult in (1,2,2.5,5,10) if mult*power>=rough)
        count=math.floor(m.units.altitude(high)/tick)
        at=lambda aft,height:QPointF(r.right()-aft/maximum*r.width(),r.bottom()-(height-low)/(high-low)*r.height())
        self.setToolTip(f'Height above wire 3 ({m.units.altitude_symbol}) · Localizer ±{COLOR_LOC_LIMIT_DEG:g}° · {m.units.distance_nm(range_nm):g} {m.units.distance_symbol}')
        self.text(p,QRectF(r.left()+4,r.top()+2,r.width()-8,14),'Height ('+m.units.altitude_symbol+')',Qt.AlignmentFlag.AlignRight)
        p.setPen(QPen(QColor(theme_color('#2B3843')),1))
        tick_nm = .25 if range_nm <= 1 else .5
        steps = round(range_nm / tick_nm)
        label_width=max(32,p.fontMetrics().horizontalAdvance(f'{m.units.distance_nm(range_nm):.2f}')+8)
        stride=max(1,math.ceil(label_width/(r.width()/steps)))
        labeled=list(range(0,steps+1,stride))
        if steps not in labeled:
            if steps-labeled[-1]<stride:labeled.pop()
            labeled.append(steps)
        for i in range(steps+1):
            d=i*tick_nm
            x=at(d*NM,0).x();p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom()))
            if i in labeled:
                left=x-label_width if i==0 else (x if i==steps else x-label_width/2)
                self.text(p,QRectF(left,r.bottom()+1,label_width,16),f'{m.units.distance_nm(d):.2f}'.rstrip('0').rstrip('.'))
        self.text(p,QRectF(r.left(),self.height()-21,72,16),m.units.distance_symbol,Qt.AlignmentFlag.AlignLeft)
        for i in range(count+1):
            height=i*tick/m.units.altitude(1);pos=at(0,height)
            p.setPen(QPen(QColor(theme_color('#2B3843')),1));p.drawLine(QPointF(r.left(),pos.y()),QPointF(r.right(),pos.y()))
            self.text(p,QRectF(0,pos.y()-8,43,16),f'{m.units.altitude(height):.0f}',Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        p.save();p.setClipRect(r)
        wedge=QPolygonF([at(0,0),at(maximum,maximum*math.tan(math.radians(m.glide-ILS_GS_FULL_SCALE_DEG))),at(maximum,maximum*math.tan(math.radians(m.glide+ILS_GS_FULL_SCALE_DEG)))])
        guide=QColor(theme_color('#20EF55'));guide.setAlpha(22)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(guide);p.drawPolygon(wedge)
        p.setPen(QPen(QColor(theme_color('#20EF55')),1));p.drawLine(at(0,0),at(maximum,maximum*math.tan(math.radians(m.glide))))
        for state in m.states.values():
            if state['lateral'] and 0<state['aft']<=maximum+1e-6:
                pos=at(state['aft'],state['height'])
                if r.contains(pos):self.symbol(p,pos,state)
        p.restore()

    def draw_scope(self,p,r):
        m=self.manager;range_nm=3.5
        self.setToolTip(f'Wire 3 perspective · {m.units.distance_nm(3.5):.2f} {m.units.distance_symbol} · Heading ±7° for wings and AoA fill · 18 m wingspan perspective with minimum readable size · Double-click an aircraft to follow')
        f=self.focal_length(r)
        self.scope_focal=f
        self.scope_max_span=min(144.,r.width()*.65,r.height()*.9)
        center=r.center();at=lambda x,y:QPointF(center.x()+f*x,center.y()-f*y)
        p.save();p.setClipRect(r)
        extent=math.ceil(math.degrees(math.atan(max(r.width(),r.height())/(2*f))))
        for degree in range(-extent,extent+1):
            t=f*math.tan(math.radians(degree));x,y=center.x()+t,center.y()-t
            p.setPen(QPen(QColor(theme_color('#2B3843')),1))
            if r.left()<=x<=r.right():p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom()))
            if r.top()<=y<=r.bottom():p.drawLine(QPointF(r.left(),y),QPointF(r.right(),y))
        p.setPen(QPen(QColor(theme_color('#20EF55')),1,Qt.PenStyle.DashLine))
        gate=[]
        for loc,gs in ((-ILS_LOC_FULL_SCALE_DEG,-ILS_GS_FULL_SCALE_DEG),(ILS_LOC_FULL_SCALE_DEG,-ILS_GS_FULL_SCALE_DEG),(ILS_LOC_FULL_SCALE_DEG,ILS_GS_FULL_SCALE_DEG),(-ILS_LOC_FULL_SCALE_DEG,ILS_GS_FULL_SCALE_DEG)):
            ray=scope_projection({'aft':1.,'height':math.tan(math.radians(m.glide+gs)),'left':math.tan(math.radians(loc))},m.glide)
            gate.append(at(*ray))
        p.drawPolygon(QPolygonF(gate))
        p.setPen(QPen(QColor(theme_color('#E1E8ED')),1))
        p.drawLine(QPointF(r.left(),center.y()),QPointF(r.right(),center.y()))
        p.drawLine(QPointF(center.x(),r.top()),QPointF(center.x(),r.bottom()))
        contacts = self.scope_contacts(r, f)
        if contacts:
            distances=[state['dme'] for state,pos in contacts];self.depth_range=(min(distances),max(distances))
            for state,pos in sorted(contacts,key=lambda entry:entry[0]['dme'],reverse=True):self.symbol(p,pos,state)
        p.restore()
        for degree in range(-extent,extent+1):
            t=f*math.tan(math.radians(degree));x,y=center.x()+t,center.y()-t
            if r.left()<=x<=r.right():self.text(p,QRectF(x-14,r.bottom()+1,28,16),f'{degree:+d}°' if degree else '0°')
            if r.top()<=y<=r.bottom():self.text(p,QRectF(1,y-8,43,16),f'{degree:+d}°' if degree else '0°',Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)

    def aircraft_at(self, point):
        candidates = []
        for position, instance in self.hits:
            delta = position - point
            distance = math.hypot(delta.x(), delta.y())
            if distance <= self.hit_radii.get(instance, 18.):
                candidates.append((distance, instance))
        return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            instance = self.aircraft_at(event.position())
            if instance is not None:
                self.manager.owner.aircraft_map_clicked(instance)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            instance = self.aircraft_at(event.position())
            if instance is not None:
                self.manager.owner.focus_aircraft(instance)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class ScopeTitleBar(QWidget):
    """Drag the title or empty header space without stealing button clicks."""
    def __init__(self,pane):
        super().__init__(pane);self.pane=pane;self.start=None
        self.setCursor(Qt.CursorShape.OpenHandCursor);self.setToolTip('Drag to move this panel')
        row=QHBoxLayout(self);row.setContentsMargins(21,2,2,2);row.setSpacing(3)
        title=QLabel(pane.title);title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(title,1)
        pane.hide_button=QPushButton('Hide');pane.hide_button.setStyleSheet('QPushButton {padding:3px 6px;}')
        pane.hide_button.setToolTip('Hide '+pane.title+' · restore it from the map toolbox menu')
        pane.hide_button.clicked.connect(pane.request_hide);row.addWidget(pane.hide_button)
        pane.pop=QPushButton('Pop out');pane.pop.setStyleSheet('QPushButton {padding:3px 6px;}')
        pane.pop.clicked.connect(pane.toggle);row.addWidget(pane.pop)

    def add_volume(self, name):
        self.volume_icon = QLabel(self)
        self.volume_icon.setToolTip(name)
        self.refresh_volume_icon()
        self.layout().insertWidget(1, self.volume_icon)
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(0, 100)
        slider.setFixedWidth(60)
        slider.setCursor(Qt.CursorShape.ArrowCursor)
        slider.setAccessibleName(name)
        self.layout().insertWidget(2, slider)
        return slider

    def refresh_volume_icon(self):
        self.volume_icon.setPixmap(control_icon('volume').pixmap(16, 16))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.start=event.globalPosition()
            target=self.pane.floating_window if self.pane.detached else self.pane
            self.original=target.pos();self.setCursor(Qt.CursorShape.ClosedHandCursor)
            target.raise_();event.accept()
    def mouseMoveEvent(self,event):
        if self.start is None:return
        delta=(event.globalPosition()-self.start).toPoint();pane=self.pane
        if pane.detached:pane.floating_window.move(self.original+delta)
        else:
            pane.user_position=(self.original.x()+delta.x(),self.original.y()+delta.y())
            pane.user_size=(pane.width(),pane.height());pane.manager.layout_panes();pane.raise_();pane.resize_grip.raise_()
        event.accept()
    def mouseReleaseEvent(self,event):
        if self.start is not None:
            self.start=None;self.setCursor(Qt.CursorShape.OpenHandCursor);self.pane.save_layout();event.accept()


class ScopeResizeGrip(QWidget):
    """Top-left grip keeps the docked panel's bottom-right corner anchored."""
    def __init__(self,pane):
        super().__init__(pane);self.pane=pane;self.start=None
        self.setFixedSize(17,17);self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip('Drag this corner to resize the scope')
    def paintEvent(self,event):
        p=QPainter(self);p.setPen(QPen(QColor(theme_color('#93A4B1')),1))
        for n in (5,9,13):p.drawLine(n,2,2,n)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.start=event.globalPosition();self.original=self.pane.floating_window.geometry() if self.pane.detached else self.pane.geometry();event.accept()
    def mouseMoveEvent(self,event):
        if self.start is None:return
        delta=event.globalPosition()-self.start;pane=self.pane
        width=max(190 if pane.kind=='stack' else 240,int(self.original.width()-delta.x()));height=max(260 if pane.kind=='stack' else 160,int(self.original.height()-delta.y()))
        if pane.detached:
            pane.floating_window.setGeometry(self.original.right()-width+1,self.original.bottom()-height+1,width,height)
        else:
            pane.user_size=(width,height)
            pane.user_position=(self.original.right()-width+1,self.original.bottom()-height+1)
            pane.manager.layout_panes()
        event.accept()
    def mouseReleaseEvent(self,event):
        if self.start is not None:self.pane.save_layout()
        self.start=None;event.accept()


class ScopeWindow(QMainWindow):
    def __init__(self,pane):
        super().__init__(None,Qt.WindowType.Window);self.pane=pane
        self.setWindowTitle(pane.title+' — Wire Judge Live');self.setMinimumSize(300,220)
        self.resize(640,400);self.setStyleSheet(pane.manager.owner.styleSheet())
        if pane.kind=='stack':self.setMinimumSize(190,260);self.resize(190,540)
        saved=pane.manager.owner.settings.store.value('panels/'+pane.kind+'/floating_geometry')
        try:self.restored=bool(saved and self.restoreGeometry(saved))
        except TypeError:self.restored=False

    def moveEvent(self,event):
        super().moveEvent(event)
        if self.pane.detached and self.isVisible():self.pane.save_layout(sync=False)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.pane.detached and self.isVisible():self.pane.save_layout(sync=False)
    def closeEvent(self,event):
        self.pane.save_layout()
        if self.pane.manager.owner.closing:super().closeEvent(event)
        else:event.ignore();self.pane.dock()


class ScopePane(QFrame):
    def __init__(self,title,kind,manager,view_factory=None):
        super().__init__(manager.owner.map);self.title,self.kind,self.manager=title,kind,manager
        self.control = getattr(manager.owner, {'scope':'landing_button','slice':'glide_button',
                                               'stack':'stack_button','scoring':'scoring_button',
                                               'roster':'roster_button',
                                               'conditions':'conditions_button', 'crew':'deck_crew_button'}[kind])
        self.floating_window=None;self.detached=False;self.enabled=self.control.isChecked();self.user_size=None;self.user_position=None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout=QVBoxLayout(self);layout.setContentsMargins(1,1,1,1);layout.setSpacing(0)
        self.header=ScopeTitleBar(self);layout.addWidget(self.header)
        try:saved=json.loads(manager.owner.settings.store.value('panels/'+kind+'/rect','null'))
        except (ValueError,TypeError):saved=None
        if isinstance(saved,list) and len(saved)==4 and all(isinstance(v,int) for v in saved) and saved[2]>=190 and saved[3]>=160:
            self.user_position=tuple(saved[:2]);self.user_size=tuple(saved[2:])
        self.view=(view_factory or ScopeView)(kind,manager);layout.addWidget(self.view,1)
        self.resize_grip=ScopeResizeGrip(self);self.resize_grip.move(1,1);self.resize_grip.raise_()
        self.setVisible(self.enabled)

    def place(self,x,y,w,h):
        view=self.manager.owner.map
        if self.user_position is not None:x,y=self.user_position
        x=max(4,min(x,max(4,view.width()-w-4)));y=max(4,min(y,max(4,view.height()-h-4)))
        self.setGeometry(x,y,w,h)
    def save_layout(self,sync=True):
        store=self.manager.owner.settings.store
        if self.detached:
            store.setValue('panels/'+self.kind+'/floating_geometry',self.floating_window.saveGeometry())
        elif self.user_position is not None or self.user_size is not None:
            self.user_position=(self.x(),self.y());self.user_size=(self.width(),self.height())
            store.setValue('panels/'+self.kind+'/rect',json.dumps([self.x(),self.y(),self.width(),self.height()]))
        if sync:store.sync()
    def toggle(self):
        if self.detached:self.dock();return
        first=self.floating_window is None
        if first:self.floating_window=ScopeWindow(self)
        self.setParent(None);self.floating_window.setCentralWidget(self);self.detached=True;self.pop.setText('Dock')
        screen=self.manager.owner.screen()
        if first and not self.floating_window.restored and screen:
            bounds=screen.availableGeometry();self.floating_window.move(bounds.center()-self.floating_window.rect().center())
        self.floating_window.show();self.show();self.floating_window.raise_();self.manager.layout_panes()

    def dock(self):
        if not self.detached:return
        self.save_layout()
        self.floating_window.takeCentralWidget();self.setParent(self.manager.owner.map)
        self.detached=False;self.floating_window.hide();self.pop.setText('Pop out')
        self.setVisible(self.enabled);self.manager.layout_panes()

    def request_hide(self):
        self.control.setChecked(False)
        if self.enabled:self.set_enabled(False)

    def set_enabled(self,checked):
        self.enabled=checked
        if self.detached:self.floating_window.setVisible(checked)
        self.setVisible(checked);self.manager.layout_panes();self.manager.sync_timer()


class ApproachDisplays(QObject):
    def __init__(self,owner,menu):
        super().__init__(owner);self.owner=owner;self.volume=None;self.states={};self.source={}
        self.glide=3.5;self.case='Case 1';self.units=DisplayUnits();self.selected=None;self.phase=1.;self.key=None;self.display_key=None
        self.panes=[ScopePane('Landing Scope','scope',self),ScopePane('Glide Slope','slice',self)];self.actions={}
        self.glide_view = self.panes[1].view
        for pane in self.panes:
            action=menu.addAction(pane.title.title());action.setCheckable(True);action.setChecked(pane.enabled)
            action.toggled.connect(pane.set_enabled);self.actions[pane.kind]=action
        owner.map.installEventFilter(self)
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.pulse)
        self.layout_panes()

    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.layout_panes();self.sync_timer()
        elif event.type()==QEvent.Type.Hide:self.sync_timer()
        return False

    def layout_panes(self):
        panes=[p for p in self.panes if p.enabled and not p.detached];view=self.owner.map
        side_by_side=view.width()>=700
        default_w=min(320,max(240,view.width()-16));default_h=min(220,max(160,(view.height()-110)//max(1,len(panes))))
        floor=view.height()-8
        cursor_x=view.width()-8;cursor_y=floor
        for pane in panes:
            w,h=pane.user_size or (default_w,default_h)
            w=min(w,max(240,view.width()-16));h=min(h,max(160,view.height()-24))
            x=cursor_x-w if side_by_side else view.width()-8-w
            y=floor-h if side_by_side else cursor_y-h
            pane.place(x,y,w,h);pane.raise_();pane.resize_grip.raise_()
            if side_by_side:cursor_x-=w+8
            else:cursor_y-=h+8
        controls=getattr(self.owner,'map_controls',None)
        if controls:controls.move(8,8);controls.raise_()

    def set_frame(self,sample,carrier_id,case,glide,offset,units,selected):
        key=((sample or {}).get('capture',{}).get('connection_id'),(sample or {}).get('capture',{}).get('monotonic_s'),
             (sample or {}).get('_radar_display_s'),carrier_id,case,glide,offset)
        display_key=(units.speed_name,units.altitude_name,units.distance_name,selected)
        changed=key!=self.key;display_changed=display_key!=self.display_key;self.display_key=display_key
        case_changed = self.case != case
        self.case,self.glide,self.units,self.selected=case,glide,units,selected
        if case_changed:
            self.glide_view.sync_zoom()
        if changed:
            self.key=key;self.volume,self.states=approach_states(sample,carrier_id,case,glide,offset)
            self.source=(sample or {}).get('source') or {}
        self.owner.map.set_approach_states(self.states,self.phase,repaint=False)
        if changed or display_changed:
            for pane in self.panes:
                if pane.enabled and pane.view.isVisible():pane.view.update()
        self.sync_timer()

    def sync_timer(self):
        active=any(s['warnings'] for s in self.states.values()) and ((self.owner.map.isVisible() and not self.owner.map.is_3d) or any(p.view.isVisible() for p in self.panes))
        if active and not self.timer.isActive():self.timer.start()
        elif not active:self.timer.stop()

    def pulse(self):
        self.phase=(1+math.cos(time.monotonic()*math.tau))/2
        self.owner.map.set_approach_states(self.states,self.phase)
        for pane in self.panes:
            if pane.enabled and pane.view.isVisible():pane.view.update()
        self.sync_timer()

    def apply_theme(self):
        for pane in self.panes:
            if pane.floating_window:pane.floating_window.setStyleSheet(self.owner.styleSheet())
            pane.view.update()
        self.owner.map.set_approach_states(self.states,self.phase,force=True)

    def shutdown(self):
        self.timer.stop()
        for pane in self.panes:
            if pane.floating_window:pane.floating_window.hide()
            pane.hide()
