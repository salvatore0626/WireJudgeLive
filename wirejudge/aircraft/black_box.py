from wirejudge.ui.theme import color as theme_color
"""Bounded, cached native history plots sharing a receive-time cursor."""
import math
import json
import time
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt,Signal,QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QToolButton,QMenu,QComboBox
from wirejudge.aircraft.flight_instruments import flight_values,source_clock

TRACES={'aoa':'AoA','altitude':'Altitude ASL','vertical_speed':'Vertical speed','ias':'IAS','tas':'TAS',
        'wind':'Wind speed','throttle':'Throttle','loc':'Localizer deviation','gs':'Glideslope deviation',
        'pitch':'Pitch','roll':'Roll','gs_load':'Player Gs'}
DEFAULT_TRACES=['aoa','altitude','vertical_speed','ias','wind','throttle','loc','gs']
MIN_Y_SPANS = {'aoa':8., 'loc':6., 'gs':2., 'throttle':100.,
               'pitch':10., 'roll':15., 'gs_load':1.}


class BlackBox(QWidget):
    history_window_changed=Signal()

    def __init__(self,store):
        super().__init__()
        self.store=store;self.cache={};self.plots={};self.curves={};self.cursors={};self.proxies=[]
        self.last_key=None;self.last_update=0.;self.x=np.empty(0);self.series={}
        self.references={};self.readouts={};self.min_y_spans=MIN_Y_SPANS
        self.hover_time=None;self.unit_labels={}
        self.plot_order=None
        self.data_version=0;self.plot_versions={};self.target=8.
        self.viewport_keys={}
        self.span=int(store.value('black_box/span',30))
        if self.span not in (10,30,60):self.span=30
        try:self.enabled=json.loads(store.value('black_box/traces',json.dumps(DEFAULT_TRACES)))
        except (TypeError,ValueError):self.enabled=list(DEFAULT_TRACES)
        self.enabled=[key for key in TRACES if key in self.enabled]
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();row.addWidget(QLabel('BLACK BOX'),1)
        self.window=QComboBox();self.window.addItems(['10 s','30 s','60 s']);self.window.setCurrentText(f'{self.span} s')
        self.window.currentIndexChanged.connect(self.change_window);row.addWidget(self.window)
        self.menu=QMenu(self);button=QToolButton();button.setText('Traces');button.setMenu(self.menu);button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions={}
        for key,name in TRACES.items():
            action=self.menu.addAction(name);action.setCheckable(True);action.setChecked(key in self.enabled)
            action.toggled.connect(lambda checked,k=key:self.toggle_trace(k,checked));self.actions[key]=action
        row.addWidget(button);layout.addLayout(row)
        self.plot_layout=QVBoxLayout();layout.addLayout(self.plot_layout)
        self.sync_plots()

    def change_window(self,*_):
        self.span=int(self.window.currentText().split()[0]);self.last_key=None
        self.store.setValue('black_box/span',self.span);self.store.sync()
        for plot in self.plots.values():plot.setXRange(-self.span,0,padding=0)
        self.history_window_changed.emit()

    def toggle_trace(self,key,checked):
        self.enabled=[k for k in TRACES if (k in self.enabled and k!=key) or (k==key and checked)]
        self.store.setValue('black_box/traces',json.dumps(self.enabled));self.store.sync()
        self.sync_plots();self.last_key=None

    def sync_plots(self):
        if self.plot_order==tuple(self.enabled):return
        for key in self.enabled:
            if key in self.plots:continue
            plot=pg.PlotWidget(background=theme_color('chart_background'));plot.setFixedHeight(132)
            plot.setTitle(TRACES[key],size='9pt',color=theme_color('#93A4B1'))
            plot.setLabel('bottom','Time',units='s');plot.getAxis('bottom').enableAutoSIPrefix(False)
            plot.getAxis('left').enableAutoSIPrefix(False);plot.getAxis('left').setWidth(55)
            plot.getAxis('left').setTextPen(theme_color('#93A4B1'));plot.getAxis('bottom').setTextPen(theme_color('#93A4B1'))
            plot.getAxis('left').setPen(theme_color('#2B3843'));plot.getAxis('bottom').setPen(theme_color('#2B3843'))
            plot.showGrid(x=True,y=True,alpha=.12);plot.setMenuEnabled(False);plot.hideButtons()
            plot.setMouseEnabled(x=False,y=False);plot.setXRange(-self.span,0,padding=0)
            plot.enableAutoRange(y=False)
            if key in ('aoa','loc','gs'):
                reference=pg.InfiniteLine(8 if key=='aoa' else 0,angle=0,pen=pg.mkPen(theme_color('#627785'),style=Qt.PenStyle.DashLine))
                plot.addItem(reference);self.references[key]=reference
            self.curves[key]=plot.plot([],[],pen=pg.mkPen(theme_color('#70B7C9'),width=1.5),connect='finite')
            cursor=pg.InfiniteLine(0,angle=90,pen=pg.mkPen(theme_color('#E1E8ED'),style=Qt.PenStyle.DashLine));cursor.hide();plot.addItem(cursor)
            readout=pg.TextItem()
            readout.setZValue(20);readout.hide()
            plot.addItem(readout,ignoreBounds=True)
            self.readouts[key]=readout
            self.cursors[key]=cursor;self.plots[key]=plot;self.plot_layout.addWidget(plot)
            self.viewport_keys[plot.viewport()]=key
            plot.viewport().installEventFilter(self)
            self.proxies.append(pg.SignalProxy(plot.scene().sigMouseMoved,rateLimit=20,slot=lambda args,k=key:self.hover(k,args[0])))
        for key,plot in self.plots.items():plot.setVisible(key in self.enabled)
        for i,key in enumerate(self.enabled):self.plot_layout.insertWidget(i,self.plots[key])
        self.plot_order=tuple(self.enabled)

    def hover(self,key,position):
        plot=self.plots[key]
        if not plot.getViewBox().sceneBoundingRect().contains(position) or not len(self.x):return
        x=plot.getViewBox().mapSceneToView(position).x()
        index=int(np.argmin(abs(self.x-x)))
        self.hover_time=float(self.x[index])
        self.show_cursor(index)

    def show_cursor(self,index):
        x = float(self.x[index])
        for name in self.enabled:
            if not self.plot_visible(name):
                continue
            cursor, label = self.cursors[name], self.readouts[name]
            cursor.setPos(x);cursor.show()
            values = self.series.get(name)
            if values is None or not math.isfinite(values[index]):
                label.hide()
                continue
            y = float(values[index])
            x_range, y_range = self.plots[name].viewRange()
            label.setAnchor((int(x > sum(x_range)/2), int(y < sum(y_range)/2)))
            label.fill = pg.mkBrush(theme_color('panel'))
            label.setColor(theme_color('chart_cursor'))
            label.setText(f'{x:.1f} s · {y:.1f} {self.unit_labels.get(name, "°")}')
            label.setPos(x,y)
            label.show()

    def hide_cursor(self):
        self.hover_time=None
        for item in (*self.cursors.values(), *self.readouts.values()):
            item.hide()

    def leaveEvent(self,event):
        self.hide_cursor()
        super().leaveEvent(event)

    def plot_visible(self,name):
        plot=self.plots[name]
        return plot.isVisible() and not plot.viewport().visibleRegion().isEmpty()

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Paint:
            name=self.viewport_keys.get(watched)
            if name in self.enabled:self.refresh_plot(name)
        return super().eventFilter(watched,event)

    def refresh_plot(self,name):
        if self.plot_versions.get(name)==self.data_version:return
        plot=self.plots[name]
        label_key=(name,self.unit_labels.get(name,'deg'))
        if plot.property('unit_label')!=str(label_key):
            plot.setLabel('left',label_key[1]);plot.setProperty('unit_label',str(label_key))
        if name=='aoa':self.references[name].setValue(self.target)
        values=self.series.get(name)
        self.plot_versions[name]=self.data_version
        if values is not None:
            self.curves[name].setData(self.x,values,connect='finite',symbol='o' if len(self.x)==1 else None,
                symbolSize=4,symbolPen=None,symbolBrush=theme_color('#70B7C9'))
        else:self.curves[name].setData([],[])
        finite = values[np.isfinite(values)] if values is not None else np.empty(0)
        reference = self.references.get(name)
        if reference is not None:
            finite = np.append(finite, reference.value())
        if name == 'throttle':
            finite = np.append(finite, (0,100))
        low, high = (float(finite.min()), float(finite.max())) if len(finite) else (0.,0.)
        center = (low + high)/2
        radius = max(self.min_y_spans.get(name, 1.)/2, (high-low)*.56)
        plot.setYRange(center-radius, center+radius, padding=0)

    def refresh_visible_plots(self):
        # Clipped QGraphicsViews still incur setData/layout costs.
        for name in self.enabled:
            if self.plot_visible(name):
                self.refresh_plot(name)
        if self.hover_time is not None and len(self.x):
            self.show_cursor(int(np.argmin(abs(self.x-self.hover_time))))

    def clear(self):
        self.cache.clear();self.x=np.empty(0);self.series={};self.last_key=None
        self.hide_cursor()
        self.data_version+=1;self.plot_versions.clear()
        for curve in self.curves.values():curve.setData([],[])

    def update_history(self,sample,history,aircraft_id,carrier_id,case,glide,offset,units,target=8.,force=False,retain=False):
        if sample is None or aircraft_id is None:
            if self.last_key is not None:self.clear()
            return
        now=time.monotonic();end=sample['capture']['monotonic_s'];connection=sample['capture']['connection_id']
        context=(aircraft_id,carrier_id,case,glide,offset,units.speed_name,units.altitude_name)
        key=(connection,end,self.span,tuple(self.enabled),context,len(history),target)
        if key==self.last_key and not force:
            self.refresh_visible_plots()
            return True
        if not force and now-self.last_update<.25:
            self.refresh_visible_plots()
            return
        self.last_key=key;self.last_update=now
        self.sync_plots()
        self.target=target
        frames=[f for f in history if f['capture']['connection_id']==connection and end-self.span<=f['capture']['monotonic_s']<=end]
        if not frames or frames[-1]['capture']['monotonic_s']<end:frames.append(sample)
        cache={ck:row for ck,row in self.cache.items() if retain and ck[0]==connection and ck[2]==context and end-self.span<=ck[1]<=end}
        for frame in frames:
            when=frame['capture']['monotonic_s'];ck=(connection,when,context)
            row=self.cache.get(ck)
            if row is None:
                a=next((a for a in frame.get('aircraft',[]) if a['instance_id']==aircraft_id),None)
                row=flight_values(frame,a,carrier_id,case,glide,offset,units) if a else {}
                row['_clock']=source_clock(frame)
            cache[ck]=row
        ordered=sorted(cache,key=lambda ck:ck[1])[-6000:]
        cache={ck:cache[ck] for ck in ordered}
        rows=[];previous_clock=None;previous_when=None
        for ck in ordered:
            when=ck[1];row=cache[ck];clock=row['_clock']
            if previous_clock is not None and (clock<previous_clock or clock-previous_clock>2 or when-previous_when>2):
                rows.append((when-end,{}))
            rows.append((when-end,row));previous_clock=clock;previous_when=when
        self.cache=cache
        self.x=np.asarray([t for t,_ in rows],dtype=float)
        self.series={name:np.asarray([r.get(name) if r.get(name) is not None else math.nan for _,r in rows]) for name in self.enabled}
        units_for={'ias':units.speed_symbol,'tas':units.speed_symbol,'wind':units.speed_symbol,'altitude':units.altitude_symbol,
                   'vertical_speed':units.altitude_symbol+'/min','throttle':'%','gs_load':'g'}
        self.unit_labels=units_for
        self.min_y_spans = dict(MIN_Y_SPANS, ias=units.speed(10*1852/3600),
                                tas=units.speed(10*1852/3600), wind=units.speed(5*1852/3600),
                                altitude=units.altitude(30.48), vertical_speed=units.altitude(60.96))
        self.data_version+=1
        self.refresh_visible_plots()
        return True
