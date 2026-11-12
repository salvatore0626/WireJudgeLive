"""Small vector icons painted in the active theme color."""
import math
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QIcon,QPixmap,QPainter,QPen,QColor,QPolygonF
from wirejudge.ui.theme import PALETTE

_CACHE={}


def control_icon(kind, *, trailing_space=0):
    key=(kind,PALETTE['text'],trailing_space)
    if key in _CACHE:return _CACHE[key]
    image=QPixmap(24+trailing_space,24);image.fill(Qt.GlobalColor.transparent)
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color=QColor(PALETTE['text']);p.setPen(QPen(color,2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    if kind=='home':
        p.setBrush(Qt.BrushStyle.NoBrush);p.drawPolyline(QPolygonF([QPointF(3,11),QPointF(12,3),QPointF(21,11)]))
        p.drawPolyline(QPolygonF([QPointF(6,10),QPointF(6,21),QPointF(18,21),QPointF(18,10)]))
        p.drawRect(QRectF(10,14,4,7))
    elif kind in ('case1','case3'):
        p.setBrush(Qt.BrushStyle.NoBrush);p.drawArc(QRectF(3,3,18,18),45*16,270*16)
        p.drawPolyline(QPolygonF([QPointF(16,2),QPointF(20,5),QPointF(16,7)]))
        font=p.font();font.setBold(True);font.setPointSize(10);p.setFont(font)
        p.drawText(QRectF(5,4,14,16),Qt.AlignmentFlag.AlignCenter,'3' if kind=='case3' else '1')
    elif kind in ('locked','unlocked'):
        p.setBrush(Qt.BrushStyle.NoBrush);p.drawRoundedRect(QRectF(5,11,14,11),2,2)
        if kind=='locked':
            p.drawArc(QRectF(7,2,10,14),0,180*16)
            p.drawLine(QPointF(7,9),QPointF(7,11));p.drawLine(QPointF(17,9),QPointF(17,11))
        else:
            p.drawArc(QRectF(11,2,10,14),0,180*16);p.drawLine(QPointF(11,9),QPointF(11,11))
        p.drawEllipse(QPointF(12,16),1,1);p.drawLine(QPointF(12,17),QPointF(12,19))
    elif kind=='plane':
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(12,2),QPointF(14,5),QPointF(14,10),QPointF(22,15),QPointF(22,17),QPointF(14,14),QPointF(14,19),QPointF(17,21),QPointF(17,23),QPointF(12,21),QPointF(7,23),QPointF(7,21),QPointF(10,19),QPointF(10,14),QPointF(2,17),QPointF(2,15),QPointF(10,10),QPointF(10,5)]))
    elif kind=='toolbox':
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3,8,18,13),2,2)
        p.drawPolyline(QPolygonF([QPointF(8,8),QPointF(8,4),QPointF(16,4),QPointF(16,8)]))
        p.drawLine(QPointF(3,13),QPointF(21,13));p.drawLine(QPointF(12,11),QPointF(12,15))
    elif kind=='vector':
        p.drawEllipse(QPointF(5,19),2,2)
        p.drawLine(QPointF(7,17),QPointF(20,4))
        p.drawPolyline(QPolygonF([QPointF(12,4),QPointF(20,4),QPointF(20,12)]))
    elif kind=='volume':
        p.drawPolygon(QPolygonF([QPointF(3,9),QPointF(7,9),QPointF(12,5),
                                 QPointF(12,19),QPointF(7,15),QPointF(3,15)]))
        p.drawArc(QRectF(10,8,10,8),-55*16,110*16)
        p.drawArc(QRectF(9,4,15,16),-55*16,110*16)
    elif kind=='gear':
        p.setBrush(Qt.BrushStyle.NoBrush)
        points=[]
        for i in range(32):
            angle=math.tau*i/32-math.pi/2
            radius=9.5 if i%4 in (1,2) else 7.
            points.append(QPointF(12+radius*math.cos(angle),12+radius*math.sin(angle)))
        p.drawPolygon(QPolygonF(points));p.drawEllipse(QPointF(12,12),3.3,3.3)
    elif kind in ('glide_slope','landing_scope','stack','live_score','roster','clock','windsock','radio'):
        draw_tool_icon(p,kind)
    else:
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color)
        if kind=='play':p.drawPolygon(QPolygonF([QPointF(7,4),QPointF(20,12),QPointF(7,20)]))
        else:p.drawRect(QRectF(6,4,4,16));p.drawRect(QRectF(14,4,4,16))
    p.end();_CACHE[key]=QIcon(image);return _CACHE[key]


def draw_tool_icon(p,kind):
    def line(x1,y1,x2,y2):
        p.drawLine(QPointF(x1,y1),QPointF(x2,y2))

    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind=='glide_slope':
        line(3,20,22,20)
        line(3,20,21,6)
        line(7,14,11,14)
        line(13,9,17,9)
    elif kind=='landing_scope':
        p.drawEllipse(QPointF(12,12),8,8)
        for x,y in ((1,12),(23,12),(12,1),(12,23)):
            line(x,y,12+(x-12)*.7,12+(y-12)*.7)
        line(12,7,12,16)
        line(7,13,12,10)
        line(12,10,17,13)
    elif kind=='stack':
        line(6,3,6,21)
        for y in (4,8,12,16,20):
            line(6,y,13 if y in (4,12,20) else 10,y)
        p.drawEllipse(QPointF(17,8),2,2)
        p.drawEllipse(QPointF(17,17),2,2)
    elif kind=='live_score':
        font=p.font();font.setBold(True);font.setPixelSize(8);p.setFont(font)
        p.drawText(QRectF(0,0,24,24),Qt.AlignmentFlag.AlignCenter,'XXXX')
    elif kind=='roster':
        p.drawRoundedRect(QRectF(5,5,14,17),2,2)
        p.drawRoundedRect(QRectF(9,2,6,5),1,1)
        for y in (11,15,18):
            line(8,y,16,y)
    elif kind=='clock':
        p.drawEllipse(QPointF(12,12),9,9)
        line(12,6,12,12);line(12,12,16,14)
    elif kind=='windsock':
        line(4,3,4,22)
        p.drawPolygon(QPolygonF([QPointF(4,4),QPointF(21,8),QPointF(21,12),QPointF(4,14)]))
        line(10,5.5,10,13.2);line(16,7,16,12.5)
    elif kind=='radio':
        line(8,6,8,2)
        p.drawRoundedRect(QRectF(6,6,12,16),2,2)
        p.drawRect(QRectF(9,9,6,4))
        line(9,16,15,16);line(9,19,15,19)


def approach_case_badge(case):
    digit = '3' if case == 'Case 3' else '1'
    key = ('approach badge', digit, PALETTE['accent'], PALETTE['text'], PALETTE['recessed'])
    if key not in _CACHE:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(PALETTE['accent']))
        painter.setBrush(QColor(PALETTE['recessed']))
        painter.drawRoundedRect(QRectF(1.5, 1.5, 13, 13), 2, 2)
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(PALETTE['text']))
        painter.drawText(QRectF(0, 0, 16, 16), Qt.AlignmentFlag.AlignCenter, digit)
        painter.end()
        _CACHE[key] = QIcon(pixmap)
    return _CACHE[key]


def podium_crowns():
    return tuple(crown(color) for color in ('#D4A72C', '#A8B4C4', '#B5784E'))


def crown(color):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in
        ((1, 4), (5, 7), (8, 2), (11, 7), (15, 4), (13, 13), (3, 13))]))
    painter.end()
    return QIcon(pixmap)
