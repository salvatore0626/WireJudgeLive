"""Simulation LSO phrase hints from glide/AoA state and pilot-relative lineup."""
from PySide6.QtCore import QObject,Qt
from PySide6.QtWidgets import QLabel
from wirejudge.approach.approach_detection import number
from wirejudge.approach.approach_geometry import NM
from wirejudge.ui.theme import PALETTE

# App hint bands, independent of the wider map coloring and detection gates.
GS_SLIGHT=.25
GS_LARGE=.7
LOC_SLIGHT=.5
LOC_LARGE=2.5
HOLD_SECONDS=.5

MATRIX={
    2:("You're high","You're high","You're high","You're high","You're high and fast"),
    1:("You're a little high","You're a little high","You're a little high","You're a little fast","You're fast"),
    0:("You're slow","You're a little slow","You're on glideslope","You're a little fast","You're fast"),
    -1:("You're slow","You're a little slow","You're a little low","You're a little low","You're a little low"),
    -2:("You're low and slow","You're low and slow","You're low","You're low","You're low"),
}


def signed_band(value,slight,large):
    if not number(value):return None
    level=2 if abs(value)>=large else (1 if abs(value)>=slight else 0)
    return -level if value<0 else level


def speed_band(aoa):
    if not number(aoa):return None
    if aoa<6:return 2
    if aoa<7:return 1
    if aoa>10:return -2
    if aoa>9:return -1
    return 0


def throttle_value(aircraft):
    # Use the provenance-aware value already resolved by telemetry capture.
    throttle=(aircraft.get('unified_controls') or {}).get('throttle') or {}
    if throttle.get('kind') == 'replicated_engine_input':
        return None
    value=throttle.get('value') if throttle.get('available') else None
    return value if number(value) and 0<=value<=1 else None


def phrase(state):
    gs=signed_band(state.get('gs'),GS_SLIGHT,GS_LARGE);speed=speed_band(state.get('aoa'))
    loc=signed_band(state.get('loc'),LOC_SLIGHT,LOC_LARGE)
    if gs is None or loc is None:return '',False
    if speed is None:
        main={-2:"You're low",-1:"You're a little low",0:'',1:"You're a little high",2:"You're high"}[gs]
    else:main=MATRIX[gs][speed+2]
    throttle=throttle_value(state['aircraft'])
    if throttle is not None:
        if throttle<.55 and gs<0 and speed is not None and speed<=0:
            main='Power' if gs==-2 and speed<0 else ('A little power' if gs==-2 or speed<0 else main)
        elif throttle>.85 and gs==2 and speed is not None and speed>0:main="You're overpowered"
    # Positive LOC is aircraft left of course, requiring a pilot-right correction.
    lineup={-2:'Come left',-1:'Come a little left',0:'',1:'A little right for lineup',2:'Right for lineup'}[loc]
    if lineup:
        if gs==0 and speed in (None,0):main=lineup
        elif main:main+=' · '+lineup
        else:main=lineup
    urgent=gs==-2 and speed is not None and speed<0
    return main,urgent


def eligible(state,case):
    aircraft=state.get('aircraft') or {};info=(aircraft.get('kinematics') or {}).get('flight_info') or {}
    return (info.get('isLanded') is False and not state.get('resting') and state.get('nose_on')
        and number(state.get('dme')) and 0<state['dme']<=(3. if case=='Case 3' else 1.)
        and number(state.get('loc')) and abs(state['loc'])<=5.
        and number(state.get('gs')))


class HintEngine:
    def __init__(self):self.reset()
    def reset(self):
        self.context=None;self.last_time=None;self.text='';self.pending=None;self.since=None;self.target=None
    def update(self,sample,states,case,selected,valid=True,geometry=None):
        choices=[s for s in states.values() if eligible(s,case)] if valid else []
        chosen=next((s for s in choices if s['id']==selected),None)
        if chosen is None:chosen=min(choices,key=lambda s:s['dme'],default=None)
        if chosen is None:self.reset();return ''
        source=sample.get('source') or {};capture=sample.get('capture') or {}
        context=(source.get('process_id'),source.get('epoch'),capture.get('connection_id'),case,chosen['id'],geometry)
        stamp=source.get('mission_time_s')
        if not number(stamp):stamp=capture.get('monotonic_s')
        if not number(stamp):self.reset();return ''
        candidate,urgent=phrase(chosen)
        if context!=self.context or (self.last_time is not None and (stamp<self.last_time or stamp-self.last_time>2.)):
            self.reset();self.context=context;self.text=candidate
        self.target=chosen['label'];self.last_time=stamp
        if candidate==self.text:self.pending=None;self.since=None
        elif urgent:self.text=candidate;self.pending=None;self.since=None
        elif candidate!=self.pending:self.pending=candidate;self.since=stamp
        elif stamp-self.since>=HOLD_SECONDS:self.text=candidate;self.pending=None;self.since=None
        return self.text


class LSOHints(QObject):
    def __init__(self,owner,toggle,pane):
        super().__init__(owner);self.owner=owner;self.toggle=toggle;self.pane=pane;self.engine=HintEngine()
        self.box=QLabel(pane);self.box.setObjectName('lsoHint');self.box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box.setWordWrap(True);self.box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.box.setMinimumWidth(0);pane.layout().addWidget(self.box)
        self.box.hide();self.apply_theme()
        toggle.toggled.connect(self.set_enabled)
    def layout_box(self):
        self.box.updateGeometry();self.pane.layout().activate()
    def set_enabled(self,enabled):
        self.owner.settings.store.setValue('ui/lso_hints',enabled);self.owner.settings.store.sync()
        self.engine.reset();self.box.hide()
        if hasattr(self.owner,'timeline'):self.owner.refresh_capture(force=True)
    def set_frame(self,sample,states,case,selected,valid=True,geometry=None):
        if not self.toggle.isChecked():self.box.hide();self.engine.reset();return
        text=self.engine.update(sample or {},states,case,selected,valid,geometry)
        self.box.setText(text);self.box.setToolTip(self.engine.target or '')
        self.box.setVisible(bool(text));self.layout_box()
    def apply_theme(self):
        self.box.setStyleSheet(f'QLabel#lsoHint {{background:{PALETTE["panel"]};color:{PALETTE["text"]};border:1px solid {PALETTE["accent"]};border-radius:6px;padding:9px 14px;font-size:18px;font-weight:600;}}')
        self.layout_box()
