from wirejudge.map.map_rendering import ProcedureLines, MAP_LINE_OPTIONS
from wirejudge.display_units import DisplayUnits
"""Batched Case 1 marshal route with cached labels, sharing the map's renderer."""
import numpy as np
from wirejudge.map.case1_marshal import case1_marshal_geometry
from wirejudge.map.case3_overlay import ProcedureLabels
from wirejudge.map.procedure_colors import DEFAULT_COLORS,rgba


class Case1MarshalOverlay:
    def __init__(self,view):
        self.last_key=None;self.geometry=None;self.palette=dict(DEFAULT_COLORS)
        self.marshal=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(.55,.7,1.,.65),width=1,antialias=False)
        self.arrival=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(.55,.7,1.,.65),width=1,antialias=False)
        self.approach=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(.4,.9,.8,.8),width=1,antialias=False)
        self.missed=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',width=1,antialias=False)
        self.labels=ProcedureLabels(view)
        self.items=[self.marshal,self.arrival,self.approach,self.missed]
        self.groups={'marshal': (self.marshal,), 'approach': (self.arrival, self.approach), 'missed': (self.missed,)}
        for item in self.items:view.addItem(item);item.setVisible(False)

    def set_palette(self, palette, opacity=1.):
        if self.palette != palette:
            self.palette = dict(palette)
            self.last_key = None
        self.marshal.setData(color=rgba(palette['marshal'], .8 * opacity))
        self.arrival.setData(color=rgba(palette['approach'], .8 * opacity))
        self.approach.setData(color=rgba(palette['approach'], .85 * opacity))
        self.missed.setData(color=rgba(palette['missed'], .85 * opacity))
        self.labels.set_opacity(opacity)

    def hide(self):
        self.labels.hide()
        for item in self.items:
            if item.visible():item.setVisible(False)

    def update(self,carrier,sea_offset,glide,offset,transform,reference_key,units=None,visibility=None,show_labels=True):
        units=units or DisplayUnits()
        visibility = visibility if visibility is not None else dict.fromkeys(self.groups, True)
        key=(carrier.get('position_world_m'),carrier.get('rotation_xyzw'),
             [(c.get('position_world_m'),c.get('endpoint1_world_m'),c.get('endpoint2_world_m')) for c in carrier.get('cables') or []],
             sea_offset,glide,offset,reference_key,units.speed_name,units.altitude_name,units.distance_name,tuple(visibility[key] for key in self.groups))
        if key==self.last_key and self.geometry is not None:
            self.apply_visibility(visibility, show_labels)
            return
        geometry=case1_marshal_geometry(carrier,sea_offset,glide,offset)
        self.last_key=key;self.geometry=geometry
        if geometry is None:self.hide();return
        for item,name in ((self.marshal,'marshal'),(self.arrival,'arrival'),(self.approach,'approach'),(self.missed,'missed')):
            item.setData(pos=np.asarray([transform(p) for p in geometry[name]],dtype=np.float32))
        categories = ('marshal','approach','approach','missed')
        self.labels.set_labels([
            (transform(position), units.procedure_text(text), self.palette[category], i>=2)
            for i, ((position, text), category) in enumerate(zip(geometry['labels'], categories))
            if visibility[category]
        ])
        self.apply_visibility(visibility, show_labels)

    def apply_visibility(self, visibility, show_labels=True):
        for category, items in self.groups.items():
            for item in items:
                if item.visible() != visibility[category]:
                    item.setVisible(visibility[category])
        self.labels.setVisible(show_labels and any(visibility.values()))
