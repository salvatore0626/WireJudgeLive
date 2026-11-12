from wirejudge.map.map_rendering import ProcedureLines, MapTextOverlay, MAP_LINE_OPTIONS
from wirejudge.display_units import DisplayUnits
"""Batched Case 3 map geometry; rebuild only when its carrier/reference changes."""
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QPainter,QColor,QStaticText,QTransform,QVector3D
from pyqtgraph.opengl import GLMeshItem,GLScatterPlotItem
from wirejudge.approach.case3_geometry import case3_geometry
from wirejudge.map.procedure_colors import DEFAULT_COLORS, rgba, vertex_colors


class ProcedureLabels(MapTextOverlay):
    """One painter for all labels; cache glyph layouts and skip off-screen text."""
    def __init__(self, view):
        super().__init__(view)
        self.label_font = self.map_label_font()
        self.opacity = 1.
        self.entries=[];self.text_cache={}

    def set_opacity(self, opacity):
        if self.opacity != opacity:
            self.opacity = opacity
            self.update()

    def refresh_label_font(self):
        font = self.map_label_font()
        if font != self.label_font:
            self.label_font = font
            for layouts in self.text_cache.values():
                for layout in layouts:
                    layout.prepare(QTransform(), font)
            self.update()

    def set_labels(self,entries):
        self.entries=[]
        for i,entry in enumerate(entries):
            position,text=entry[:2]
            if text not in self.text_cache:
                layouts=[]
                for line in text.splitlines():
                    layout=QStaticText(line);layout.setTextFormat(Qt.TextFormat.PlainText)
                    layout.prepare(QTransform(),self.label_font);layouts.append(layout)
                self.text_cache[text]=layouts
            self.entries.append((position,self.text_cache[text],QColor(entry[2] if len(entry)>2 else ('#b0e6f3' if i<4 else '#f5c17d')),entry[3] if len(entry)>3 else i>=4))
        # Course text changes with a turning carrier; bound cached text layouts.
        if len(self.text_cache)>32:
            self.text_cache={e[1]:self.text_cache[e[1]] for e in entries}
        self.update()

    def paintEvent(self, event):
        if not self.entries or self.opacity == 0:return
        project=self.compute_projection();visible=[];view=self
        for position,layouts,color,left in self.entries:
            p=project.map(QVector3D(*position))
            width=max(layout.size().width() for layout in layouts)
            height=sum(layout.size().height() for layout in layouts)
            x,y=(p.x()-width-10 if left else p.x()+8),p.y()-height-6
            if 0<=p.z()<=1 and x<view.width() and x+width>0 and y<view.height() and y+height>0:
                visible.append((QPointF(x,y),layouts,color))
        if not visible:return
        painter=QPainter(self);painter.setFont(self.label_font)
        painter.setOpacity(self.opacity)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for position,layouts,color in visible:
            painter.setPen(color)
            for layout in layouts:
                painter.drawStaticText(position,layout)
                position=QPointF(position.x(),position.y()+layout.size().height())
        painter.end()


class Case3Overlay:
    def __init__(self,view):
        self.last_key=None;self.geometry=None;self.palette=dict(DEFAULT_COLORS)
        self.opacity = 1.
        self.edges=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(.25,.85,1.,.8),width=1,antialias=False)
        self.routes=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(.35,.85,1.,.8),width=1,antialias=False)
        self.missed=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',color=(1.,.68,.25,.85),width=1,antialias=False)
        self.fill=GLMeshItem(smooth=False,color=(.2,.7,1.,.08),glOptions='translucent',drawEdges=False)
        self.fill.setDepthValue(100)
        self.glide=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',width=1,antialias=False)
        self.marshal=ProcedureLines(glOptions=MAP_LINE_OPTIONS,mode='lines',width=1,antialias=False)
        self.labels=ProcedureLabels(view)
        self.waypoint=GLScatterPlotItem(glOptions=MAP_LINE_OPTIONS,pos=np.empty((0,3)),color=(.5,.9,1.,1.),size=8,pxMode=True)
        self.items=[self.fill,self.edges,self.routes,self.glide,self.marshal,self.missed,self.waypoint]
        self.groups={'marshal': (self.marshal, self.waypoint),
                     'approach': (self.fill, self.edges, self.routes), 'missed': (self.missed,),
                     'glide': (self.glide,)}
        for item in self.items:view.addItem(item);item.setVisible(False)

    def set_palette(self, palette, opacity=1.):
        if self.palette != palette:
            self.palette = dict(palette)
            self.last_key = None
        self.edges.setData(color=rgba(palette['approach'], .8 * opacity))
        self.routes.setData(color=rgba(palette['approach'], .8 * opacity))
        self.fill.setColor(rgba(palette['approach'], .08 * opacity))
        self.opacity = opacity
        if self.geometry is not None:
            self.glide.setData(color=vertex_colors(palette['glide'], .85 * opacity, self.geometry['glide_alpha']))
        self.marshal.setData(color=rgba(palette['marshal'], .75 * opacity))
        self.missed.setData(color=rgba(palette['missed'], .85 * opacity))
        self.waypoint.setData(color=rgba(palette['marshal'], opacity))
        self.labels.set_opacity(opacity)

    def hide(self):
        self.labels.hide()
        for item in self.items:
            if item.visible():item.setVisible(False)

    def update(self,carrier,sea_offset,glide,offset,is_3d,transform,reference_key,units=None,visibility=None,show_labels=True,final_stop_m=0.):
        units=units or DisplayUnits()
        visibility = visibility if visibility is not None else dict.fromkeys(self.groups, True)
        key=(carrier.get('position_world_m'),carrier.get('rotation_xyzw'),[(c.get('position_world_m'),c.get('endpoint1_world_m'),
             c.get('endpoint2_world_m')) for c in carrier.get('cables') or []],
             sea_offset,glide,offset,is_3d,reference_key,units.speed_name,units.altitude_name,units.distance_name,
             tuple(visibility[key] for key in self.groups),final_stop_m)
        if key==self.last_key and self.geometry is not None:
            self.apply_visibility(visibility, show_labels)
            return
        geometry=case3_geometry(carrier,sea_offset,glide,offset,final_stop_m)
        self.last_key=key;self.geometry=geometry
        if geometry is None:self.hide();return
        vertices=geometry['vertices' if is_3d else 'plan_vertices']
        faces=geometry['faces' if is_3d else 'plan_faces']
        for item,name in ((self.edges,'edges' if is_3d else 'plan_edges'),(self.routes,'routes'),(self.marshal,'marshal_routes'),(self.missed,'missed')):
            item.setData(pos=np.asarray([transform(p) for p in geometry[name]],dtype=np.float32))
        self.glide.setData(pos=np.asarray([transform(p) for p in geometry['glide_routes']], dtype=np.float32).reshape(-1, 3),
                           color=vertex_colors(self.palette['glide'], .85 * self.opacity, geometry['glide_alpha']))
        self.fill.setMeshData(vertexes=np.asarray([transform(p) for p in vertices],dtype=np.float32),
                              faces=np.asarray(faces,dtype=np.uint32))
        categories = ('marshal','approach','approach','glide','missed','missed')
        self.labels.set_labels([
            (transform(position), units.procedure_text(text), self.palette[category], i>=4)
            for i, ((position, text), category) in enumerate(zip(geometry['labels'], categories))
            if visibility[category]
        ])
        self.waypoint.setData(pos=np.asarray([transform(geometry['iaf'])],dtype=np.float32))
        self.apply_visibility(visibility, show_labels)

    def apply_visibility(self, visibility, show_labels=True):
        for category, items in self.groups.items():
            for item in items:
                if item.visible() != visibility[category]:
                    item.setVisible(visibility[category])
        self.labels.setVisible(show_labels and any(visibility.values()))
