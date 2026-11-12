"""GPU compass overlay with a lightweight screen-space hit target."""
from wirejudge.map.map_rendering import MapLines, MAP_LINE_OPTIONS
from wirejudge.map.procedure_colors import rgba
import math
import numpy as np
from PySide6.QtGui import QMatrix4x4


class NorthIndicator(MapLines):
    def __init__(self,parent_view):
        super().__init__(glOptions=MAP_LINE_OPTIONS,mode='lines',antialias=False,width=1)
        self.parent_view=parent_view
        self.direction=(0.,-1.)
        self.geometry_key=None
        self.hud_matrix=QMatrix4x4()
        self.setDepthValue(300)
        parent_view.addItem(self)

    def mvpMatrix(self):
        # This item alone bypasses the scene transform: its positions are clip-space.
        return self.hud_matrix

    def contains(self, position):
        cx,cy=max(42,self.parent_view.width()-54),54
        return math.hypot(position.x()-cx,position.y()-cy)<=46

    def set_direction(self,x,y):
        length=math.hypot(x,y)
        if length<1e-9:
            return
        self.direction=(round(x/length,5),round(y/length,5))
        view=self.parent_view
        width,height=view.width(),view.height()
        if width<=0 or height<=0:
            return
        muted, warning = view.paint_color('muted'), view.paint_color('warning')
        key=(width,height,self.direction,muted,warning)
        if key==self.geometry_key:
            return
        self.geometry_key=key
        cx,cy=max(42,width-54),54
        dx,dy=self.direction
        segments=[];colors=[]
        def line(a,b,color,thickness=1):
            # Parallel one-pixel strokes avoid wide-GL-line limits on macOS.
            length=max(math.hypot(b[0]-a[0],b[1]-a[1]),1e-9)
            nx,ny=-(b[1]-a[1])/length,(b[0]-a[0])/length
            for offset in range(-(thickness//2),thickness//2+1):
                segments.extend(((a[0]+nx*offset,a[1]+ny*offset),(b[0]+nx*offset,b[1]+ny*offset)))
                colors.extend((color,color))
        angles=np.linspace(0,math.tau,65)
        circle=np.column_stack((cx+32*np.cos(angles),cy+32*np.sin(angles)))
        for a,b in zip(circle[:-1],circle[1:]):
            line(a,b,rgba(muted,.7))
        tip=(cx+dx*24,cy+dy*24)
        tail=(cx-dx*18,cy-dy*18)
        base=(tip[0]-dx*10,tip[1]-dy*10)
        left=(base[0]-dy*5,base[1]+dx*5)
        right=(base[0]+dy*5,base[1]-dx*5)
        red=rgba(warning)
        line(tail,tip,red,3)
        # Fill the head with one-pixel spans in the same screen-space batch.
        for depth in range(1, 11):
            x,y=tip[0]-dx*depth,tip[1]-dy*depth
            half=depth*.5
            line((x-dy*half,y+dx*half),(x+dy*half,y-dx*half),red)
        for a,b in ((tip,left),(left,right),(right,tip)):
            line(a,b,red,2)
        # Upright stroked N, placed just beyond the north arrowhead.
        nx,ny=cx+dx*39,cy+dy*39
        a,b,c,d=(nx-4,ny+6),(nx-4,ny-6),(nx+4,ny+6),(nx+4,ny-6)
        for start,end in ((a,b),(b,c),(c,d)):
            line(start,end,red,3)
        pixels=np.asarray(segments,dtype=np.float32)
        clip=np.column_stack((pixels[:,0]*2/width-1,1-pixels[:,1]*2/height,np.zeros(len(pixels))))
        self.setData(pos=clip.astype(np.float32),color=np.asarray(colors,dtype=np.float32))
