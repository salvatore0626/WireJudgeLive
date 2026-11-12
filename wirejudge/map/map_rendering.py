"""Alpha-composited map strokes, visible on both light and dark backgrounds."""
import ctypes
from functools import lru_cache
import math
import numpy as np
from OpenGL import GL
from OpenGL.GL import (GL_BLEND,GL_DEPTH_TEST,GL_CULL_FACE,GL_SRC_ALPHA,
                       GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_DEPTH_BUFFER_BIT,
                       glClear,glColorMask,glDepthMask)
from pyqtgraph.opengl import GLLinePlotItem, GLMeshItem
from pyqtgraph.opengl.items.GLLinePlotItem import DirtyFlag
from PySide6.QtCore import QLineF, QRectF, Qt
from PySide6.QtGui import QColor, QMatrix4x4, QOpenGLContext, QPainter, QVector3D
from PySide6.QtWidgets import QWidget
from wirejudge.approach.approach_geometry import NM
from wirejudge.ui.settings_store import DEFAULTS

# Keep strokes layered as before, but blend with the background instead of
# adding light to it. Additive blending erases dark strokes on a light map.
MAP_LINE_OPTIONS={GL_DEPTH_TEST:False,GL_BLEND:True,GL_CULL_FACE:False,
    'glBlendFuncSeparate':(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_ONE_MINUS_SRC_ALPHA)}


class MapTextOverlay(QWidget):
    """Paint map text in Qt's widget pass, outside the OpenGL draw pass."""

    def __init__(self, view):
        super().__init__(view)
        self.setStyleSheet('background: transparent;')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(view.rect())
        self.hide()

    def map_label_font(self):
        self.ensurePolished()
        font = self.font()
        font.setPointSize(9)
        return font

    def compute_projection(self):
        view = self.parentWidget()
        width, height = self.width(), self.height()
        viewport = (0, 0, width, height)
        screen = QMatrix4x4()
        screen.viewport(0, height, width, -height)
        return screen * view.projectionMatrix(viewport, viewport) * view.viewMatrix()


class MapScaleBar(MapTextOverlay):
    """Corner distance reference at the camera's focus depth."""

    def __init__(self, view):
        super().__init__(view)
        self.scale = None

    def refresh(self):
        view = self.parentWidget()
        available = min(132, view.width() - 34)
        if available < 40 or view.height() < 52:
            self.hide()
            return

        metres_per_pixel = (view.pixelSize(view.opts['center']) if view.is_3d
                            else view.span / view.width())
        limit_nm = available * metres_per_pixel / NM
        exponent = math.floor(math.log10(limit_nm))
        decade = 10. ** exponent
        distance_nm = max(step * decade for step in (1, 2, 5) if step * decade <= limit_nm * (1 + 1e-12))
        scale = distance_nm * NM / metres_per_pixel, f'{distance_nm:.{max(0, -exponent)}f} NM'
        self.setGeometry(10, view.height() - 44, min(155, view.width() - 20), 34)
        if self.scale != scale:
            self.scale = scale
            self.update()
        if self.isHidden():
            self.show()

    def paintEvent(self, event):
        length, label = self.scale
        painter = QPainter(self)
        painter.setFont(self.map_label_font())
        label_width = painter.fontMetrics().horizontalAdvance(label)
        left = max(6., (label_width - length) / 2 + 2)
        right = left + length
        line_y = 27.5
        color = QColor(self.parentWidget().paint_color('muted'))
        color.setAlphaF(.50)
        painter.setPen(color)
        painter.drawLines([QLineF(left, line_y, right, line_y),
                           QLineF(left, line_y - 4, left, line_y + 1),
                           QLineF(right, line_y - 4, right, line_y + 1)])
        color.setAlphaF(.85)
        painter.setPen(color)
        text_width = max(length, label_width + 2)
        painter.drawText(QRectF((left + right - text_width) / 2, 0, text_width, 21),
                         Qt.AlignmentFlag.AlignCenter, label)
        painter.end()


class RadarOutlineLabels(MapTextOverlay):
    def __init__(self, view):
        super().__init__(view)
        self.anchors = []

    def refresh_label_font(self):
        self.update()

    def paintEvent(self, event):
        project = self.compute_projection()
        view = self.parentWidget()
        painter = QPainter(self)
        painter.setFont(self.map_label_font())
        ink = QColor(view.paint_color('radar'))
        ink.setAlphaF(.75 * view.radar_opacity)
        painter.setPen(ink)
        metrics = painter.fontMetrics()
        shown = set()
        for text, start, end in self.anchors:
            if text in shown:
                continue
            a, b = (project.map(QVector3D(*p)) for p in (start, end))
            x, y = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
            width = metrics.horizontalAdvance(text)
            if (not (0 < a.z() < 1 and 0 < b.z() < 1)
                    or not self.rect().adjusted(width // 2, 14, -width // 2, -14).contains(round(x), round(y))
                    or math.hypot(b.x() - a.x(), b.y() - a.y()) < 3):
                continue
            angle = (math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) + 90) % 180 - 90
            painter.save()
            painter.translate(x, y)
            painter.rotate(angle)
            painter.drawText(QRectF(-width / 2 - 2, -metrics.height() - 3, width + 4, metrics.height()),
                             Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
            shown.add(text)
        painter.end()


class DeckDepthMask(GLMeshItem):
    """Write the carrier deck to the depth buffer without painting over the map."""

    def paint(self):
        glColorMask(False, False, False, False)
        try:
            super().paint()
        finally:
            glColorMask(True, True, True, True)


class MapLines(GLLinePlotItem):
    """Draw map lines only with valid, bound vertex buffers."""

    @staticmethod
    def bind_vertices(buffer, array, location, components, dirty):
        if not buffer.isCreated() and not buffer.create():
            return False
        if not buffer.bind():
            return False
        try:
            if dirty:
                if buffer.size() != array.nbytes:
                    buffer.allocate(array, array.nbytes)
                else:
                    buffer.write(0, array, array.nbytes)
            if buffer.size() < array.nbytes:
                return False
            # A zero byte offset into the bound VBO, never a client array.
            GL.glVertexAttribPointer(location, components, GL.GL_FLOAT, False, 0, ctypes.c_void_p(0))
            GL.glEnableVertexAttribArray(location)
            return True
        finally:
            buffer.release()

    def stroke_matrices(self):
        yield self.mvpMatrix()

    def paint(self):
        context = QOpenGLContext.currentContext()
        if context is None or self.pos is None or len(self.pos) < 2:
            return
        per_vertex_color = isinstance(self.color, np.ndarray)
        if per_vertex_color and self.color.shape != (len(self.pos), 4):
            return
        self.setupGLState()
        GL.glDisableVertexAttribArray(1)
        try:
            if not self.bind_vertices(self.m_vbo_position, self.pos, 0, 3, DirtyFlag.POSITION in self.dirty_bits):
                return
            if per_vertex_color:
                if not self.bind_vertices(self.m_vbo_color, self.color, 1, 4, DirtyFlag.COLOR in self.dirty_bits):
                    return
            else:
                GL.glVertexAttrib4f(1, *self.color)
            self.dirty_bits = DirtyFlag(0)
            fmt = context.format()
            if not (fmt.profile() == fmt.OpenGLContextProfile.CoreProfile
                    and not fmt.testOption(fmt.FormatOption.DeprecatedFunctions)):
                GL.glLineWidth(self.width)
            program = self.getShaderProgram()
            with program:
                location = GL.glGetUniformLocation(program, 'u_mvp')
                mode = GL.GL_LINES if self.mode == 'lines' else GL.GL_LINE_STRIP
                for matrix in self.stroke_matrices():
                    GL.glUniformMatrix4fv(location, 1, False, np.asarray(matrix.data(), dtype=np.float32))
                    context.functions().glDrawArrays(mode, 0, len(self.pos))
        finally:
            GL.glDisableVertexAttribArray(0)
            GL.glDisableVertexAttribArray(1)
            GL.glLineWidth(1.)


@lru_cache(maxsize=5)
def stroke_offsets(width):
    radius = (width - 1) / 2
    steps = [i - radius for i in range(width)]
    return [(x, y) for x in steps for y in steps if x*x + y*y <= (radius + .25)**2]


class ProcedureLines(MapLines):
    """Expand one-pixel strokes in screen space, including on core OpenGL."""
    thickness = DEFAULTS['procedure_line_width']

    def stroke_matrices(self):
        matrix = self.mvpMatrix()
        if self.thickness == 1:
            yield matrix
            return
        view = self.view()
        scale = view.devicePixelRatioF()
        width = max(1., view.width() * scale)
        height = max(1., view.height() * scale)
        for x, y in stroke_offsets(self.thickness):
            offset = QMatrix4x4()
            offset.translate(2*x/width, 2*y/height, 0)
            yield offset * matrix


class TrailLines(MapLines):
    """Read the deck depth mask without writing trail depth into the hull pass."""

    def paint(self):
        glDepthMask(False)
        try:
            super().paint()
        finally:
            glDepthMask(True)


class CarrierHullLines(MapLines):
    """Finish the carrier-only depth pass before aircraft and wire rendering."""

    def paint(self):
        try:
            super().paint()
        finally:
            # The deck is a mask for the hull, not a depth surface for telemetry.
            glClear(GL_DEPTH_BUFFER_BIT)
