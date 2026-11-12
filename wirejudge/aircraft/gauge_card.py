from wirejudge.ui.theme import color as theme_color
"""Lightweight painted instrument tiles; drag anywhere to reorder."""
import math
from wirejudge.aircraft.flight_instruments import low_fuel
from wirejudge.approach.approach_geometry import ILS_GS_FULL_SCALE_DEG, ILS_LOC_FULL_SCALE_DEG
from PySide6.QtCore import Qt,QPointF,QRectF,QMimeData
from PySide6.QtGui import QPainter,QPen,QColor,QFont,QFontMetricsF,QPolygonF,QDrag,QPixmap,QPainterPath
from PySide6.QtWidgets import QWidget,QApplication,QMenu

GAUGES={'ias':'IAS','ground_speed':'GROUND SPEED','vertical_speed':'VERTICAL SPEED','aoa':'AOA INDEXER','configured':'CONFIGURED','dme':'DME','altitude':'ALTIMETER','radar_alt':'RADAR ALT','hsi':'CDI', 'inputs':'INPUTS','fuel':'FUEL','flaps':'FLAPS','g_meter':'G-METER','attitude':'ATTITUDE','heading_track':'HEADING / TRACK','wind':'WIND','brakes':'BRAKES'}
GAUGES['battery'] = 'BATTERY'
MIME='application/x-wjl-gauge'
VSI_MAX_FPM = 6000
VSI_SWEEP_DEG = 168


def vsi_sweep(fpm):
    """Signed degrees from the zero mark. Climb is clockwise from 9 o'clock."""
    limited = max(-VSI_MAX_FPM, min(VSI_MAX_FPM, fpm))
    return limited * VSI_SWEEP_DEG / VSI_MAX_FPM


def rounded_vertical_speed(value):
    return int(math.copysign(math.floor(abs(value)/10 + .5)*10, value))


def compact(value):
    if value is None:return '—'
    if abs(value)>=1e6:return f'{value/1e6:.1f}M'
    if abs(value)>=10000:return f'{value/1000:.1f}k'
    return f'{value:.0f}'


class GaugeCard(QWidget):
    def __init__(self,key,owner):
        super().__init__(owner)
        self.key,self.owner=key,owner
        self.data={};self.units=None;self.trend=None;self.flash=True
        self.target=8.;self.tolerance=.5;self.press=None;self.hover_drop=False
        self.paint_key=None;self.dial_face=None;self.dial_face_key=None
        self.zoom = 1.
        self.preview_palette = None
        self.digital = key == 'vertical_speed'
        self.setMinimumWidth(110)
        self.setFixedHeight(190)
        self.setAcceptDrops(True)
        notes={'g_meter':'\nCurrent reported G; min/max use available history through the displayed time.',
               'attitude':'\nPitch and bank with a hollow marker for the direction of motion.',
               'heading_track':'\nAircraft heading and direction of travel over the ground.',
               'wind':'\nWorld wind direction FROM, in degrees; speed follows display units.',
               'battery':'\nReported charge and battery state. A dash means telemetry is unavailable.',
               'brakes':'\nReported airbrake deployment and maximum wheel-brake demand; missing values stay unavailable.'}
        self.setToolTip(notes.get(key,'')+'\nDrag anywhere to reorder · Right-click to hide this gauge'+
            ('\nUses supplied local inputs or received remote control commands; unavailable inputs stay blank.' if key=='inputs' else
             '\nFull deflection: localizer ±2.5°; glideslope ±0.7°.' if key=='hsi' else ''))

    def set_values(self,data,units,trend,target,tolerance):
        self.data,self.units,self.trend,self.target,self.tolerance=data,units,trend,target,tolerance
        # Other instruments changing must not repaint this tile. Keep exact values
        # in data; quantize only the repaint decision to visible display precision.
        def q(name,step=1):
            value=data.get(name)
            return None if value is None else round(value/step)
        if units is None:key=None
        elif self.key in ('ias','altitude') and self.digital:
            key=q(self.key)
        elif self.key=='ias':
            base=units.speed(400*1852/3600)
            key=(q('ias',.5),q('ias'),math.ceil((data.get('ias') or 0)/base))
        elif self.key=='vertical_speed':
            value=data.get('vertical_speed')
            key=None if value is None else (rounded_vertical_speed(value),
                None if self.digital else round(vsi_sweep(value / units.altitude(.3048))*2))
        elif self.key=='aoa':
            value=data.get('aoa')
            band=None if value is None else (1 if value>target+tolerance else -1 if value<target-tolerance else 0)
            key=(q('aoa',.1),band,target)
        elif self.key=='configured':key=(data.get('gear'),data.get('hook'),bool(data.get('inside')))
        elif self.key=='fuel':key=(q('fuel_percent',.1),q('fuel'),q('fuel_capacity'),low_fuel(data.get('fuel_percent')))
        elif self.key=='battery':
            key = (q('battery_percent'), data.get('battery_connected'), data.get('battery_energized'))
        elif self.key=='flaps':key=q('flaps',.01)
        elif self.key=='g_meter':key=(q('gs_load',.01),q('g_min',.01),q('g_max',.01),q('g_span_s'))
        elif self.key=='attitude':key=(q('pitch',.1),q('roll',.1),q('heading',.1),q('track',.1),q('flight_path_angle',.1))
        elif self.key=='heading_track':key=(q('heading',.5),q('track',.5),q('heading'),q('track'))
        elif self.key=='wind':key=(q('wind',.1),q('wind_from'),data.get('wind_calm'),q('heading'))
        elif self.key=='brakes':key=(q('airbrake'),q('wheel_brake'))
        elif self.key=='inputs':key=(q('throttle',.5),q('throttle'),q('input_pitch',.01),q('input_yaw',.01),q('input_roll',.01))
        elif self.key=='dme':key=q('dme',.01)
        elif self.key=='ground_speed':key=q('ground_speed')
        elif self.key=='altitude':key=(q('altitude',.5),q('altitude'))
        elif self.key=='radar_alt':key=(q('radar_alt'),trend)
        else:key=(q('heading',.5),q('heading'),q('course'),q('loc',.05),q('gs',.01),q('loc',.1),q('gs',.1))
        key=(key,units.speed_name,units.altitude_name,units.distance_name) if units else None
        if key!=self.paint_key:
            self.paint_key=key
            if not self.visibleRegion().isEmpty():self.update()

    def warning(self):
        return self.key=='configured' and self.data.get('inside') and (self.data.get('gear') in ('up','moving') or self.data.get('hook')=='up')

    def paint_color(self, role):
        return theme_color(role, self.preview_palette)

    def text(self,p,rect,text,size=12,color='#E1E8ED',bold=False):
        font=QFont(self.font());font.setPointSizeF(size);font.setBold(bold)
        width=QFontMetricsF(font).horizontalAdvance(str(text))
        if width>rect.width():font.setPointSizeF(max(6,size*rect.width()/max(width,1)))
        p.setFont(font);p.setPen(QColor(self.paint_color(color)));p.drawText(rect,int(Qt.AlignmentFlag.AlignCenter),str(text))

    def boxed_readout(self,p,rect,text):
        p.setPen(QPen(QColor(self.paint_color('border')),1))
        p.setBrush(QColor(self.paint_color('recessed')))
        p.drawRoundedRect(rect,4,4)
        self.text(p,rect,text,10,bold=True)

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.setMinimumWidth(math.ceil(110 * zoom))
        self.setFixedHeight(round(190 * zoom))
        self.dial_face_key = None
        self.update()

    def set_display_mode(self, mode):
        self.digital = mode == 'Digital'
        self.paint_key = None
        self.update()

    def drawing_width(self):
        return self.width() / self.zoom

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.zoom, self.zoom)
        p.setBrush(QColor(self.paint_color('#192129')));p.setPen(QPen(QColor(self.paint_color('#70B7C9') if self.hover_drop else self.paint_color('#2B3843')),1))
        p.drawRoundedRect(QRectF(.5,.5,self.drawing_width()-1,189),6,6)
        self.text(p,QRectF(5,4,self.drawing_width()-10,27),GAUGES[self.key],11,self.paint_color('text'),True)
        p.setPen(QPen(QColor(self.paint_color('divider')),1))
        p.drawLine(QPointF(10,32),QPointF(self.drawing_width()-10,32))
        if self.units is None:
            self.text(p,QRectF(4,65,self.drawing_width()-8,75),'—',36);return
        if self.key in ('ias','altitude') and not self.digital:self.draw_dial(p)
        elif self.key=='vertical_speed':self.draw_vertical_speed(p)
        elif self.key=='aoa':self.draw_indexer(p)
        elif self.key=='configured':self.draw_configured(p)
        elif self.key=='fuel':self.draw_fuel(p)
        elif self.key=='battery':self.draw_battery(p)
        elif self.key=='flaps':self.draw_flaps(p)
        elif self.key=='hsi':self.draw_hsi(p)
        elif self.key=='inputs':self.draw_inputs(p)
        elif self.key=='g_meter':self.draw_g_meter(p)
        elif self.key=='attitude':self.draw_attitude(p)
        elif self.key=='heading_track':self.draw_heading_track(p)
        elif self.key=='wind':self.draw_wind(p)
        elif self.key=='brakes':self.draw_brakes(p)
        else:
            value=self.data.get(self.key)
            if self.key=='radar_alt':
                arrow={1:'↑ ',-1:'↓ ',0:'→ ',None:''}[self.trend]
                self.draw_digital(p,arrow+compact(value),self.units.altitude_symbol)
            elif self.key=='dme':
                self.draw_digital(p,'—' if value is None else f'{value:.2f}',self.units.distance_symbol)
            else:
                unit=self.units.altitude_symbol if self.key=='altitude' else self.units.speed_symbol
                self.draw_digital(p,'—' if value is None else f'{value:,.0f}',unit)

    def draw_digital(self,p,shown,unit):
        w=self.drawing_width()
        self.text(p,QRectF(8,71,w-16,64),shown,min(40,max(28,w*.23)),bold=True)
        self.text(p,QRectF(4,137,w-8,25),unit,12,self.paint_color('#93A4B1'))

    def draw_vertical_speed(self,p):
        value=self.data.get('vertical_speed')
        unit=self.units.altitude_symbol+'/min'
        rounded=None if value is None else rounded_vertical_speed(value)
        shown='—' if rounded is None else f'{rounded:+,}' if rounded else '0'
        w=self.drawing_width()
        if self.digital:
            box=QRectF(8,71,w-16,64)
            p.setPen(QPen(QColor(self.paint_color('border')),1))
            p.setBrush(QColor(self.paint_color('recessed')))
            p.drawRoundedRect(box,4,4)
            if rounded:
                arrow='↑' if rounded>0 else '↓'
                shown=f'{arrow} {abs(rounded):,}'
            self.draw_digital(p,shown,unit)
            return

        r=min(w*.40,50.)
        cx,cy=w/2,91.
        p.setPen(QPen(QColor(self.paint_color('border')),1))
        p.setBrush(QColor(self.paint_color('recessed')))
        p.drawEllipse(QPointF(cx,cy),r,r)
        def at(angle,radius):
            radians=math.radians(180 + angle)
            return QPointF(cx+math.cos(radians)*radius,cy+math.sin(radians)*radius)

        tick_pen=QPen(QColor(self.paint_color('muted')),1)
        for fpm in range(-VSI_MAX_FPM,VSI_MAX_FPM+1,500):
            major=fpm%2000==0
            angle=vsi_sweep(fpm)
            p.setPen(tick_pen)
            p.drawLine(at(angle,r-2),at(angle,r-(8 if major else 5)))
            if major:
                pos=at(angle,r-17)
                mark=f'{fpm/1000:+g}' if fpm else '0'
                self.text(p,QRectF(pos.x()-11,pos.y()-7,22,14),mark,7)
        if value is not None:
            angle=vsi_sweep(value / self.units.altitude(.3048))
            p.setPen(QPen(QColor(self.paint_color('needle')),2))
            p.drawLine(QPointF(cx,cy),at(angle,r-10))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.paint_color('needle')))
            p.drawEllipse(QPointF(cx,cy),2.5,2.5)
        self.text(p,QRectF(4,143,w-8,16),'×1000 FPM',8,'muted')
        self.boxed_readout(p,QRectF(6,162,w-12,22),f'{shown} {unit}')

    def draw_g_meter(self,p):
        value=self.data.get('gs_load');lo=self.data.get('g_min');hi=self.data.get('g_max')
        self.text(p,QRectF(5,52,self.drawing_width()-10,56),'—' if value is None else f'{value:.2f} G',30,bold=True)
        available=[v for v in (value,lo,hi) if v is not None]
        if available:
            minimum=min(-3,min(available));maximum=max(9,max(available))
            scale=lambda v:12+(self.drawing_width()-24)*(v-minimum)/max(1,maximum-minimum)
            p.setPen(QPen(QColor(self.paint_color('#627785')),1))
            p.drawLine(QPointF(12,111),QPointF(self.drawing_width()-12,111))
            for mark in (lo,hi):
                if mark is not None:
                    x=scale(mark);p.drawLine(QPointF(x,106),QPointF(x,116))
            if value is not None:
                x=scale(value);p.setPen(QPen(QColor(self.paint_color('#70B7C9')),3))
                p.drawLine(QPointF(x,106),QPointF(x,116))
        for x,v,title in ((0,lo,'MIN'),(self.drawing_width()/2,hi,'MAX')):
            self.text(p,QRectF(x,119,self.drawing_width()/2,20),title,8,self.paint_color('#93A4B1'))
            self.text(p,QRectF(x,140,self.drawing_width()/2,22),'—' if v is None else f'{v:.2f}',14)
        seconds = round(self.data.get('g_span_s', 0))
        minutes, remainder = divmod(seconds, 60)
        duration = f'{minutes} min {remainder} sec' if minutes else f'{remainder} sec'
        self.text(p,QRectF(4,171,self.drawing_width()-8,14),duration,8,self.paint_color('#93A4B1'))

    def draw_attitude(self,p):
        pitch,roll=self.data.get('pitch'),self.data.get('roll')
        if pitch is None or roll is None:
            self.text(p,QRectF(4,64,self.drawing_width()-8,65),'—',36)
            self.text(p,QRectF(4,149,self.drawing_width()-8,20),'ATTITUDE UNAVAILABLE',8,self.paint_color('#93A4B1'));return
        cx,cy=self.drawing_width()/2,111;r=min(54,self.drawing_width()*.40)
        p.save();path=QPainterPath();path.addEllipse(QPointF(cx,cy),r,r);p.setClipPath(path)
        p.translate(cx,cy);p.rotate(-roll);p.translate(0,max(-90,min(90,pitch))*r/35)
        p.fillRect(QRectF(-400,-800,800,800),QColor(self.paint_color('sky')))
        p.fillRect(QRectF(-400,0,800,800),QColor(self.paint_color('ground')))
        p.setPen(QPen(QColor(self.paint_color('horizon')),1.5));p.drawLine(QPointF(-200,0),QPointF(200,0))
        for degree in (-30,-20,-10,10,20,30):
            y=-degree*r/35;half=16 if abs(degree)%20==0 else 10
            p.drawLine(QPointF(-half,y),QPointF(half,y))
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.drawEllipse(QPointF(cx,cy),r,r)
        p.setPen(QPen(QColor(self.paint_color('#70B7C9')),2.5))
        for a,b in ((-r*.7,-r*.15),(r*.15,r*.7)):
            p.drawLine(QPointF(cx+a,cy),QPointF(cx+b,cy))
        p.drawEllipse(QPointF(cx,cy),2,2)
        p.save()
        p.translate(cx,cy)
        p.rotate(-roll)
        p.setPen(QPen(QColor(self.paint_color('text')),1))
        for angle in (-60,-45,-30,-20,-10,0,10,20,30,45,60):
            p.save()
            p.rotate(angle)
            p.drawLine(QPointF(0,-r),QPointF(0,-r+(8 if angle%30==0 else 4)))
            p.restore()
        p.restore()
        p.setPen(QPen(QColor(self.paint_color('accent')),2))
        p.drawPolyline(QPolygonF([
            QPointF(cx-4,cy-r+13),QPointF(cx,cy-r+7),QPointF(cx+4,cy-r+13),
        ]))
        heading = self.data.get('heading')
        self.draw_velocity_marker(p,cx,cy,r,pitch,roll,heading)
        if heading is not None:
            readout = QRectF(cx-20,34,40,20)
            step = math.floor(heading/10)*10
            for bearing in range(step-30,step+41,10):
                x = cx+(bearing-heading)*r/36
                if 22 < abs(x-cx) <= r:
                    p.setPen(QPen(QColor(self.paint_color('muted')),1))
                    p.drawLine(QPointF(x,48),QPointF(x,52))
                if bearing%30==0 and 34 <= abs(x-cx) <= cx-15:
                    self.text(p,QRectF(x-13,34,26,13),f'{bearing%360:03d}',7)
            self.boxed_readout(p,readout,f'{round(heading)%360:03d}°')
        self.text(p,QRectF(3,169,self.drawing_width()-6,18),f'P {pitch:+.1f}°   R {roll:+.1f}°',9)

    def draw_velocity_marker(self,p,cx,cy,r,pitch,roll,heading):
        path_angle = self.data.get('flight_path_angle')
        track = self.data.get('track')
        if path_angle is None or (track is not None and heading is None):
            return
        drift = 0. if track is None else (track - heading + 180) % 360 - 180
        x, y = drift*r/35, (pitch-path_angle)*r/35
        angle = math.radians(-roll)
        c, s = math.cos(angle), math.sin(angle)
        limit = min(1., (r-14)/max(1., math.hypot(x,y)))
        center = QPointF(cx+(c*x-s*y)*limit, cy+(s*x+c*y)*limit)
        p.save()
        p.translate(center)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for role, width in (('recessed',3.5),('accent',1.5)):
            p.setPen(QPen(QColor(self.paint_color(role)),width))
            p.drawEllipse(QPointF(0,0),5,5)
            p.drawLine(QPointF(-12,0),QPointF(-5,0))
            p.drawLine(QPointF(5,0),QPointF(12,0))
            p.drawLine(QPointF(0,-5),QPointF(0,-11))
        p.restore()

    def draw_heading_track(self,p):
        heading = self.data.get('heading')
        track = self.data.get('track')
        hdg_text = '—' if heading is None else f'{round(heading)%360:03d}°'
        trk_text = '—' if track is None else f'{round(track)%360:03d}°'
        w = self.drawing_width()
        if self.digital:
            for y, title, value, color in ((47,'HEADING',hdg_text,'text'),(117,'TRACK',trk_text,'accent')):
                self.text(p,QRectF(4,y,w-8,16),title,9,'muted',True)
                self.text(p,QRectF(4,y+16,w-8,42),value,28,color,True)
            p.setPen(QPen(QColor(self.paint_color('divider')),1))
            p.drawLine(QPointF(14,110),QPointF(w-14,110))
            return

        cx, cy, r = w/2, 96., min(47.,w*.40)
        p.setBrush(QColor(self.paint_color('recessed')))
        p.setPen(QPen(QColor(self.paint_color('border')),1))
        p.drawEllipse(QPointF(cx,cy),r,r)
        if heading is not None:
            self.draw_compass_ticks(p,cx,cy,r,heading)
            if track is not None:
                p.save()
                p.translate(cx,cy)
                p.rotate(track-heading)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self.paint_color('accent')))
                p.drawPolygon(QPolygonF([QPointF(0,-r+23),QPointF(-5,-r+32),QPointF(5,-r+32)]))
                p.restore()
        p.setPen(QPen(QColor(self.paint_color('needle')),2))
        p.drawLine(QPointF(cx,cy-r-6),QPointF(cx,cy-r+2))
        self.text(p,QRectF(4,148,w-8,17),f'HDG {hdg_text}',9,'text',True)
        self.text(p,QRectF(4,165,w-8,17),f'TRK {trk_text}',9,'accent',True)

    def draw_wind(self,p):
        value=self.data.get('wind');direction=self.data.get('wind_from');calm=self.data.get('wind_calm')
        cx,cy=self.drawing_width()/2,81;r=min(33,self.drawing_width()*.24)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.drawEllipse(QPointF(cx,cy),r,r)
        for angle,title in ((0,'N'),(90,'E'),(180,'S'),(270,'W')):
            a=math.radians(angle);x=cx+math.sin(a)*(r+9);y=cy-math.cos(a)*(r+9)
            self.text(p,QRectF(x-6,y-6,12,12),title,7,self.paint_color('#93A4B1'))
        if direction is not None and not calm:
            p.save();p.translate(cx,cy);p.rotate(direction + 180)
            p.setPen(QPen(QColor(self.paint_color('#70B7C9')),2));p.drawLine(QPointF(0,r*.6),QPointF(0,-r*.8))
            p.drawPolyline(QPolygonF([QPointF(-5,-r*.8+7),QPointF(0,-r*.8),QPointF(5,-r*.8+7)]));p.restore()
        heading = self.data.get('heading')
        if heading is not None:
            p.save();p.translate(cx,cy);p.rotate(heading)
            p.setPen(QPen(QColor(self.paint_color('#E1E8ED')),2))
            p.drawPolyline(QPolygonF([QPointF(-4, -r+4), QPointF(0, -r-1), QPointF(4, -r+4)]))
            p.restore()
        self.text(p,QRectF(4,127,self.drawing_width()-8,24),'—' if value is None else f'{value:.1f}',21,bold=True)
        symbol=self.units.speed_symbol
        self.text(p,QRectF(4,150,self.drawing_width()-8,16),symbol,8,self.paint_color('#93A4B1'))
        self.text(p,QRectF(4,170,self.drawing_width()-8,16),'CALM' if calm else ('FROM —' if direction is None else f'FROM {direction:03.0f}°'),8)

    def draw_brakes(self,p):
        for y,key,title in ((50,'airbrake','AIRBRAKE'),(116,'wheel_brake','WHEEL BRAKE')):
            value=self.data.get(key)
            self.text(p,QRectF(4,y,self.drawing_width()-8,18),title,9,self.paint_color('#93A4B1'),True)
            self.text(p,QRectF(4,y+19,self.drawing_width()-8,24),'—' if value is None else f'{value:.0f}%',16,bold=True)
            rect=QRectF(14,y+46,self.drawing_width()-28,9)
            p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.setBrush(QColor(self.paint_color('recessed')));p.drawRect(rect)
            if value is not None:
                p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.paint_color('#70B7C9')))
                p.drawRect(QRectF(rect.x()+1,rect.y()+1,(rect.width()-2)*value/100,rect.height()-2))

    def draw_dial(self,p):
        w=self.drawing_width();r=min(w*.40,46);cx,cy=w/2,40+r
        value=self.data.get(self.key)
        is_ias=self.key=='ias'
        # A 400-knot base dial, converted to the selected units; expand for faster aircraft.
        base=self.units.speed(400*1852/3600)
        maximum=base*max(1,math.ceil((value or 0)/base))
        face_key=(self.drawing_width(),190,self.devicePixelRatioF()*self.zoom,maximum if is_ias else None,self.font().toString())
        if face_key!=self.dial_face_key:
            dpr=self.devicePixelRatioF()*self.zoom
            self.dial_face=QPixmap(round(self.drawing_width()*dpr),round(190*dpr));self.dial_face.setDevicePixelRatio(dpr)
            self.dial_face.fill(Qt.GlobalColor.transparent)
            face=QPainter(self.dial_face);face.setRenderHint(QPainter.RenderHint.Antialiasing)
            face.setBrush(QColor(self.paint_color('recessed')));face.setPen(QPen(QColor(self.paint_color('#627785')),2));face.drawEllipse(QPointF(cx,cy),r,r)
            for i in range(41 if is_ias else 50):
                major=i%5==0
                angle=math.radians(135+i*270/40 if is_ias else -90+i*360/50)
                inner=r-(9 if major else 4)
                face.setPen(QPen(QColor(self.paint_color('#E1E8ED')),1))
                face.drawLine(QPointF(cx+inner*math.cos(angle),cy+inner*math.sin(angle)),QPointF(cx+(r-2)*math.cos(angle),cy+(r-2)*math.sin(angle)))
                if major:
                    tick=maximum*i/40 if is_ias else i/5
                    tx,ty=cx+(r-19)*math.cos(angle),cy+(r-19)*math.sin(angle)
                    self.text(face,QRectF(tx-13,ty-8,26,16),compact(tick),7)
            face.end();self.dial_face_key=face_key
        p.drawPixmap(0,0,self.dial_face)
        def needle(angle,length,width,color):
            a=math.radians(angle);p.setPen(QPen(QColor(color),width))
            p.drawLine(QPointF(cx,cy),QPointF(cx+length*math.cos(a),cy+length*math.sin(a)))
        if value is not None:
            if is_ias:needle(135+270*min(value/maximum,1),r-12,2,self.paint_color('needle'))
            else:
                needle(-90+(value%100000)/100000*360,r*.35,3,self.paint_color('#93A4B1'))
                needle(-90+(value%10000)/10000*360,r*.52,3,self.paint_color('needle'))
                needle(-90+(value%1000)/1000*360,r*.80,1.5,self.paint_color('needle'))
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.paint_color('#E1E8ED')));p.drawEllipse(QPointF(cx,cy),3,3)
        bottom=cy+r+4
        unit=self.units.speed_symbol if is_ias else self.units.altitude_symbol
        self.boxed_readout(p,QRectF(5,bottom,w-10,22),('—' if value is None else f'{value:.0f}')+' '+unit)

    def draw_indexer(self,p):
        cx=self.drawing_width()/2;value=self.data.get('aoa')
        half=min(24,max(18,self.drawing_width()*.13));radius=min(18,max(14,self.drawing_width()*.095))
        active=None if value is None else ('high' if value>self.target+self.tolerance else 'low' if value<self.target-self.tolerance else 'on')
        for y,name,color,direction in ((61,'high',self.paint_color('#20EF55'),1),(141,'low',self.paint_color('#FF626A'),-1)):
            p.setPen(QPen(QColor(color if active==name else self.paint_color('#35434F')),5))
            p.drawPolyline(QPolygonF([QPointF(cx-half,y-direction*8),QPointF(cx,y+direction*10),QPointF(cx+half,y-direction*8)]))
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(self.paint_color('#FFE36D') if active=='on' else self.paint_color('#35434F')),5))
        p.drawEllipse(QPointF(cx,101),radius,radius)
        self.boxed_readout(p,QRectF(5,164,self.drawing_width()-10,22),'—' if value is None else f'{value:.1f}°')

    def draw_configured(self,p):
        radius=min(23,max(16,self.drawing_width()*.15))
        for x,key,title in ((self.drawing_width()*.27,'gear','GEAR'),(self.drawing_width()*.73,'hook','HOOK')):
            state=self.data.get(key,'unknown')
            warning=self.data.get('inside') and (state=='up' or (key=='gear' and state=='moving'))
            color={'unknown':self.paint_color('#35434F'),'up':self.paint_color('gear_up'),'down':self.paint_color('#20EF55'),'moving':self.paint_color('#E4CD7A')}.get(state,self.paint_color('#35434F'))
            if warning:color=self.paint_color('#FF626A') if self.flash else self.paint_color('warning_dim')
            self.text(p,QRectF(x-self.drawing_width()*.24,48,self.drawing_width()*.48,24),title,10,self.paint_color('#E1E8ED'),True)
            p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.setBrush(QColor(color));p.drawEllipse(QPointF(x,103),radius,radius)
            self.text(p,QRectF(x-self.drawing_width()*.24,136,self.drawing_width()*.48,24),{'up':'UP','down':'DOWN','moving':'MOVING'}.get(state,'—'),9,bold=True)
        if self.warning():self.text(p,QRectF(4,166,self.drawing_width()-8,18),'APPROACH',9,self.paint_color('#FF626A'))

    def draw_fuel(self,p):
        value=self.data.get('fuel_percent');fuel=self.data.get('fuel');capacity=self.data.get('fuel_capacity')
        fuel_color=self.paint_color('#FF626A') if low_fuel(value) else self.paint_color('#20EF55')
        bar=QRectF(16,115,self.drawing_width()-32,20)
        p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.setBrush(QColor(self.paint_color('recessed')));p.drawRect(bar)
        if value is not None:
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(fuel_color))
            p.drawRect(QRectF(bar.x()+1,bar.y()+1,(bar.width()-2)*max(0,min(1,value/100)),bar.height()-2))
        self.text(p,QRectF(6,55,self.drawing_width()-12,51),'—' if value is None else f'{value:.1f}%',27,color=fuel_color if low_fuel(value) else self.paint_color('#E1E8ED'),bold=True)
        self.text(p,QRectF(4,145,self.drawing_width()-8,20),'—' if fuel is None else f'{fuel:,.0f} / {capacity:,.0f} L',10)

    def draw_flaps(self,p):
        value=self.data.get('flaps')
        self.text(p,QRectF(6,77,self.drawing_width()-12,65),'—' if value is None else f'{value:.0%}',32,bold=True)

    def draw_battery(self, p):
        width = self.drawing_width()
        value = self.data.get('battery_percent')
        green = QColor(self.paint_color('slow'))
        red = QColor(self.paint_color('warning'))
        if value is None:
            shown, charge_color = '—', self.paint_color('muted')
        else:
            shown = f'{value:.0f}%'
            blend = min(1., round(value) / 50.)
            charge_color = QColor.fromRgbF(
                red.redF() + blend * (green.redF() - red.redF()),
                red.greenF() + blend * (green.greenF() - red.greenF()),
                red.blueF() + blend * (green.blueF() - red.blueF()),
            ).name()
        self.text(p, QRectF(6, 43, width-12, 63), shown, 32, charge_color, True)
        for y, key, label in ((122, 'battery_connected', 'Connected'),
                              (151, 'battery_energized', 'Energized')):
            state = self.data.get(key)
            p.setPen(QPen(QColor(self.paint_color('muted')), 1))
            p.setBrush(green if state is True else QColor(self.paint_color('recessed')))
            p.drawEllipse(QPointF(16, y+10), 5, 5)
            self.text(p, QRectF(24, y, width-57, 20), label, 10)
            status = 'ON' if state is True else 'OFF' if state is False else '—'
            self.text(p, QRectF(width-33, y, 26, 20), status, 9, self.paint_color('muted'))

    def draw_compass_ticks(self,p,cx,cy,r,heading):
        tick_pen=QPen(QColor(self.paint_color('#93A4B1')),1)
        for bearing in range(0,360,10):
            angle=math.radians(bearing-heading-90)
            c,s=math.cos(angle),math.sin(angle)
            major=bearing%30==0
            inner=r-(7 if major else 4)
            p.setPen(tick_pen)
            p.drawLine(QPointF(cx+(r-2)*c,cy+(r-2)*s),QPointF(cx+inner*c,cy+inner*s))
            if major:
                name={0:'N',90:'E',180:'S',270:'W'}.get(bearing,str(bearing//10))
                self.text(p,QRectF(cx+(r-15)*c-9,cy+(r-15)*s-8,18,16),name,7)

    def draw_hsi(self,p):
        cx=self.drawing_width()/2;r=min(self.drawing_width()*.40,51);cy=40+r
        heading=self.data.get('heading');course=self.data.get('course')
        p.setBrush(QColor(self.paint_color('recessed')));p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.drawEllipse(QPointF(cx,cy),r,r)
        # Compass rotates with aircraft heading. Deviation bars stay screen-aligned.
        if heading is not None:
            self.draw_compass_ticks(p,cx,cy,r,heading)
        # Fixed center and deviation dots: LOC full scale 2.5 degrees; GS 0.7 degree.
        extent=r*.49
        p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.setBrush(QColor(self.paint_color('#627785')))
        for step in (-2,-1,1,2):
            d=extent*step/2
            p.drawEllipse(QPointF(cx+d,cy),1.5,1.5);p.drawEllipse(QPointF(cx,cy+d),1.5,1.5)
        p.drawLine(QPointF(cx-4,cy),QPointF(cx+4,cy));p.drawLine(QPointF(cx,cy-4),QPointF(cx,cy+4))
        loc,gs=self.data.get('loc'),self.data.get('gs')
        p.setPen(QPen(QColor(self.paint_color('#20EF55')),2))
        if loc is not None:
            # Positive LOC means aircraft left of course: fly the bar to the right.
            x=cx+max(-1,min(1,loc/ILS_LOC_FULL_SCALE_DEG))*extent
            p.drawLine(QPointF(x,cy-extent),QPointF(x,cy+extent))
        if gs is not None:
            # Positive GS means high: fly the horizontal bar down.
            y=cy+max(-1,min(1,gs/ILS_GS_FULL_SCALE_DEG))*extent
            p.drawLine(QPointF(cx-extent,y),QPointF(cx+extent,y))
        p.setBrush(QColor(self.paint_color('needle')));p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(cx,cy-r+5),QPointF(cx-4,cy-r-1),QPointF(cx+4,cy-r-1)]))
        self.text(p,QRectF(2,cy+r+3,self.drawing_width()-4,18),'HDG '+('—' if heading is None else f'{heading%360:03.0f}°')+'  CRS '+('—' if course is None else f'{course:03.0f}°'),8)
        self.text(p,QRectF(2,cy+r+21,self.drawing_width()-4,18),'LOC '+('—' if loc is None else f'{loc:+.1f}°')+'  GS '+('—' if gs is None else f'{gs:+.1f}°'),8)

    def draw_inputs(self,p):
        left,top,right,bottom=39.,46.,self.drawing_width()-12.,137.
        width,height=right-left,bottom-top;cx,cy=(left+right)/2,(top+bottom)/2
        def clamp(value):return max(-1,min(1,value))
        p.setPen(QPen(QColor(self.paint_color('#627785')),1));p.setBrush(QColor(self.paint_color('recessed')))
        p.drawRect(QRectF(left,top,width,height));p.drawRect(QRectF(10,top,12,height))
        p.drawRect(QRectF(left,151,width,10))
        p.setPen(QPen(QColor(self.paint_color('#2B3843')),1))
        p.drawLine(QPointF(cx,top),QPointF(cx,bottom));p.drawLine(QPointF(left,cy),QPointF(right,cy))
        p.drawLine(QPointF(cx,148),QPointF(cx,164))
        throttle=self.data.get('throttle');pitch=self.data.get('input_pitch');roll=self.data.get('input_roll');yaw=self.data.get('input_yaw')
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.paint_color('#70B7C9')))
        if throttle is not None:
            fill=max(0,min(1,throttle/100))*height
            p.drawRect(QRectF(11,bottom-fill,10,fill))
        else:self.text(p,QRectF(8,cy-9,16,18),'—',9)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.paint_color('#70B7C9')))
        if yaw is not None:
            end=cx+clamp(yaw)*width/2
            p.drawRect(QRectF(min(cx,end),152,abs(end-cx),8))
        else:self.text(p,QRectF(left,148,width,16),'—',9)
        if pitch is not None and roll is not None:
            p.setPen(QPen(QColor('#ffffff'),1));p.setBrush(QColor(self.paint_color('#70B7C9')))
            # VTOL VR reports right stick as negative roll.
            p.drawEllipse(QPointF(cx-clamp(roll)*(width/2-4),cy-clamp(pitch)*(height/2-4)),4,4)
        else:self.text(p,QRectF(left,cy-10,width,20),'—',16)
        self.text(p,QRectF(4,30,26,15),'THR',7,self.paint_color('#93A4B1'))
        self.text(p,QRectF(left,30,width,15),'PITCH / ROLL',7,self.paint_color('#93A4B1'))
        self.text(p,QRectF(left,165,width,16),'YAW',8,self.paint_color('#93A4B1'))
        self.text(p,QRectF(3,165,32,16),'—' if throttle is None else f'{throttle:.0f}%',7,self.paint_color('#93A4B1'))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.press=event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.press is not None and event.buttons()&Qt.MouseButton.LeftButton and (event.position().toPoint()-self.press).manhattanLength()>=QApplication.startDragDistance():
            self.press=None
            drag=QDrag(self);mime=QMimeData();mime.setData(MIME,self.key.encode());drag.setMimeData(mime);drag.setPixmap(self.grab());drag.exec(Qt.DropAction.MoveAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self.press=None;super().mouseReleaseEvent(event)

    def contextMenuEvent(self,event):
        menu=QMenu(self);action=menu.addAction('Hide '+GAUGES[self.key])
        if menu.exec(event.globalPos())==action:self.owner.set_gauge_visible(self.key,False)

    def dragEnterEvent(self,event):
        if isinstance(event.source(),GaugeCard) and event.source().owner is self.owner:
            self.hover_drop=True;self.update();event.acceptProposedAction()

    def dragLeaveEvent(self,event):
        self.hover_drop=False;self.update()

    def dropEvent(self,event):
        self.hover_drop=False;self.update()
        if isinstance(event.source(),GaugeCard) and event.source().owner is self.owner:
            key=bytes(event.mimeData().data(MIME)).decode()
            self.owner.reorder_gauge(key,self.key);event.acceptProposedAction()
