"""Selected-aircraft instruments and Black Box; reused when popped out."""
import json
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QToolButton,QMenu
from wirejudge.aircraft.gauge_card import GaugeCard,GAUGES
from wirejudge.aircraft.black_box import BlackBox
from wirejudge.aircraft.flight_instruments import flight_values,radar_trend,g_extrema
from wirejudge.aircraft.aircraft_list import flight_identity

GAUGE_ZOOM_LEVELS = (.68, .82, 1., 1.25, 1.5)


class InstrumentPanel(QWidget):
    def __init__(self,store):
        super().__init__()
        self.store=store;self.columns=0;self.visible_keys=[]
        self.zoom_index = max(0,min(len(GAUGE_ZOOM_LEVELS)-1,store.value('instruments/zoom',2,type=int)))
        self.last_sample_key=None
        self.extrema_key=None;self.extrema=(None,None);self.extrema_clock=None
        self.empty=True
        self.radar_mode = False
        try:self.order=json.loads(store.value('instruments/order',json.dumps(list(GAUGES))))
        except (ValueError,TypeError):self.order=list(GAUGES)
        self.order=['configured' if k in ('gear','hook') else k for k in self.order]
        self.order=list(dict.fromkeys(k for k in self.order if k in GAUGES))
        self.order += [k for k in GAUGES if k not in self.order]
        try:self.hidden=set(json.loads(store.value('instruments/hidden','[]')))
        except (ValueError,TypeError):self.hidden=set()
        if {'gear','hook'}<=self.hidden:self.hidden.add('configured')
        self.hidden.intersection_update(GAUGES)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.title=QLabel('Select an aircraft');self.title.setWordWrap(True);self.title.setObjectName('aircraftTitle');row.addWidget(self.title,1)
        self.menu=QMenu(self);self.actions={}
        manage=QToolButton();manage.setText('Gauges');manage.setMenu(self.menu);manage.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for key,name in GAUGES.items():
            action=self.menu.addAction(name);action.setCheckable(True);action.setChecked(key not in self.hidden)
            action.toggled.connect(lambda checked,k=key:self.set_gauge_visible(k,checked));self.actions[key]=action
        self.menu.addSeparator();self.menu.addAction('Restore default layout',self.restore_defaults)
        row.addWidget(manage)
        self.manage_gauges = manage
        self.zoom_buttons = []
        for text, step, description in (('−', -1, 'Smaller gauges'), ('+', 1, 'Larger gauges')):
            button = QToolButton()
            button.setText(text);button.setToolTip(description);button.setAccessibleName(description)
            button.clicked.connect(lambda checked=False, delta=step: self.set_gauge_zoom(self.zoom_index+delta))
            row.addWidget(button)
            self.zoom_buttons.append(button)
        layout.addLayout(row)
        self.grid=QGridLayout();self.grid.setSpacing(6);self.grid.setContentsMargins(0,0,0,0)
        self.cards={key:GaugeCard(key,self) for key in GAUGES}
        layout.addLayout(self.grid)
        self.black_box=BlackBox(store);layout.addWidget(self.black_box)
        self.flash=True
        self.flash_timer=QTimer(self);self.flash_timer.setInterval(500);self.flash_timer.timeout.connect(self.blink)
        self.set_gauge_zoom(self.zoom_index, save=False)

    def set_gauge_zoom(self, index, save=True):
        self.zoom_index = max(0,min(len(GAUGE_ZOOM_LEVELS)-1,index))
        self.zoom_buttons[0].setEnabled(self.zoom_index > 0)
        self.zoom_buttons[1].setEnabled(self.zoom_index < len(GAUGE_ZOOM_LEVELS)-1)
        for card in self.cards.values():
            card.set_zoom(GAUGE_ZOOM_LEVELS[self.zoom_index])
        self.reflow(force=True)
        if save:
            self.store.setValue('instruments/zoom',self.zoom_index)

    def save_layout(self):
        self.store.setValue('instruments/order',json.dumps(self.order))
        self.store.setValue('instruments/hidden',json.dumps(sorted(self.hidden)));self.store.sync()

    def set_gauge_visible(self,key,visible):
        if key not in GAUGES:return
        if visible:self.hidden.discard(key)
        else:self.hidden.add(key)
        self.actions[key].blockSignals(True);self.actions[key].setChecked(visible);self.actions[key].blockSignals(False)
        self.reflow(force=True);self.save_layout();self.update_flash()

    def reorder_gauge(self,source,target):
        if source not in self.order or target not in self.order or source==target:return
        self.order.remove(source);self.order.insert(self.order.index(target),source)
        self.reflow(force=True);self.save_layout()

    def restore_defaults(self):
        self.order=list(GAUGES);self.hidden.clear()
        for action in self.actions.values():action.blockSignals(True);action.setChecked(True);action.blockSignals(False)
        self.set_gauge_zoom(2);self.save_layout();self.update_flash()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.reflow()

    def reflow(self,force=False):
        column_width = round(124 * GAUGE_ZOOM_LEVELS[self.zoom_index]) + 6
        columns=max(1,min(6,(self.width()+6)//column_width))
        keys=([key for key in self.order if key in ('hsi', 'dme', 'vertical_speed', 'altitude', 'ias')]
              if self.radar_mode else [key for key in self.order if key not in self.hidden])
        if not force and columns==self.columns and keys==self.visible_keys:return
        while self.grid.count():self.grid.takeAt(0)
        for key,card in self.cards.items():card.setVisible(key in keys)
        for i,key in enumerate(keys):self.grid.addWidget(self.cards[key],i//columns,i%columns,Qt.AlignmentFlag.AlignTop)
        for i in range(6):self.grid.setColumnStretch(i,1 if i<columns else 0)
        self.columns,self.visible_keys=columns,keys

    def set_radar_mode(self, enabled):
        if self.radar_mode == enabled:
            return
        self.radar_mode = enabled
        self.last_sample_key = None
        self.manage_gauges.setVisible(not self.radar_mode)
        self.black_box.clear()
        self.black_box.setVisible(not enabled)
        self.reflow(force=True)
        self.update_flash()

    def update_flash(self):
        active=self.isVisible() and any(self.cards[k].warning() and not self.cards[k].visibleRegion().isEmpty() for k in self.visible_keys)
        if active and not self.flash_timer.isActive():
            self.flash=True
            for key in self.visible_keys:self.cards[key].flash=True
            self.flash_timer.start()
        elif not active:self.flash_timer.stop()

    def blink(self):
        self.flash=not self.flash
        for key in self.visible_keys:
            card=self.cards[key]
            if card.warning():
                card.flash=self.flash
                if not card.visibleRegion().isEmpty():card.update()

    def hideEvent(self,event):
        self.flash_timer.stop();super().hideEvent(event)

    def set_frame(self,sample,history,aircraft_id,carrier_id,case,glide,offset,units,target=8.,tolerance=.5,force=False,retain=False):
        a=next((a for a in (sample or {}).get('aircraft',[]) if a['instance_id']==aircraft_id),None)
        if a is None:
            if self.empty:return
            self.title.setText('Select an aircraft')
            for card in self.cards.values():card.set_values({},None,None,target,tolerance)
            self.flash_timer.stop();self.black_box.clear();self.last_sample_key=None
            self.empty=True
            return
        self.empty=False
        self.title.setText(flight_identity(a)[2])
        if self.radar_mode:
            observation = a.get('_radar_observation')
            if observation:
                aircraft, carrier = observation
                key = (sample['capture']['connection_id'], aircraft_id, a['_radar_hit_s'],
                       case, glide, offset, units.speed_name, units.altitude_name, units.distance_name, target, tolerance)
                if key != self.last_sample_key or force:
                    observed = dict(sample, carriers=[carrier] if carrier else [])
                    data = flight_values(observed, aircraft, carrier['instance_id'] if carrier else None,
                                         case, glide, offset, units)
                    for name in self.visible_keys:
                        self.cards[name].set_values(data, units, None, target, tolerance)
                    self.last_sample_key = key
            return
        key=(sample['capture']['connection_id'],sample['capture']['monotonic_s'],aircraft_id,carrier_id,case,glide,offset,units.speed_name,units.altitude_name,units.distance_name,target,tolerance)
        if key!=self.last_sample_key or force:
            data=flight_values(sample,a,carrier_id,case,glide,offset,units)
            previous=next((frame for frame in reversed(history) if frame['capture']['monotonic_s']<sample['capture']['monotonic_s'] and frame['capture']['connection_id']==sample['capture']['connection_id']),None)
            trend=radar_trend(sample,previous,aircraft_id)
            # Buffer extrema update at 4 Hz; seeking backwards always uses a new key.
            extrema_key=(sample['capture']['connection_id'],aircraft_id,int(sample['capture']['monotonic_s']*4))
            if force or extrema_key!=self.extrema_key or (self.extrema_clock is not None and sample['capture']['monotonic_s']<self.extrema_clock):
                self.extrema=g_extrema(sample,history,aircraft_id);self.extrema_key=extrema_key
            self.extrema_clock=sample['capture']['monotonic_s']
            current=data.get('gs_load')
            low,high=self.extrema
            if current is not None:
                low=current if low is None else min(low,current)
                high=current if high is None else max(high,current)
            data['g_min'],data['g_max']=low,high
            now = sample['capture']['monotonic_s']
            oldest = next((frame['capture']['monotonic_s'] for frame in history
                           if frame['capture']['connection_id'] == sample['capture']['connection_id']
                           and frame['capture']['monotonic_s'] <= now), now)
            data['g_span_s'] = now - oldest
            for card in self.cards.values():card.set_values(data,units,trend,target,tolerance)
            self.last_sample_key=key
        self.update_flash()
        return self.black_box.update_history(sample,history,aircraft_id,carrier_id,case,glide,offset,units,target,force,retain)
