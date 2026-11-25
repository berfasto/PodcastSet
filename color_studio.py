"""Componentes exactos de Color de 'Studio UI':
- ColorWheel HSV (anillo exterior 9.3% con 12 paradas + triángulo equilátero interior negro-arriba).
- _ScreenColorPicker + _Loupe (gotero multipantalla congelado con lupa circular de 108px y píldora Hex).
- ColorPalettePanel / ColorPaletteDialog (paletas guardadas en JSON, slider 18-42px, rejilla redondeada).
- ColorEditor / ColorPickerPanel (selector segmentado, rueda, Hex validado, gotero, copiar, opacidad y cajón)."""
import math
import re

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QGuiApplication, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

import icons
import theme
from theme import C, DENSITY, UI_PREFS, save_ui_prefs

_HEX_RE = re.compile(r"^#?(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


class _FocusSelectLineEdit(QLineEdit):
    """Auto-selecciona todo el código Hex al recibir foco."""

    def focusInEvent(self, e):
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)


def _clamp_barycentric_triangle(px, py, p0, p1, p2):
    """Proyecta (px, py) al punto más cercano dentro del triángulo (p0, p1, p2) y devuelve (w0, w1, w2)."""
    v0x, v0y = p1.x() - p0.x(), p1.y() - p0.y()
    v1x, v1y = p2.x() - p0.x(), p2.y() - p0.y()
    v2x, v2y = px - p0.x(), py - p0.y()
    d00 = v0x * v0x + v0y * v0y
    d01 = v0x * v1x + v0y * v1y
    d11 = v1x * v1x + v1y * v1y
    d20 = v2x * v0x + v2y * v0y
    d21 = v2x * v1x + v2y * v1y
    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-9:
        return 1.0, 0.0, 0.0
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    if u >= 0.0 and v >= 0.0 and w >= 0.0:
        return u, v, w

    # Proyectar sobre los 3 segmentos y elegir la menor distancia cuadrática
    best_dist = 1e18
    best_bary = (1.0, 0.0, 0.0)
    edges = (
        (p0, p1, lambda t: (1.0 - t, t, 0.0)),
        (p1, p2, lambda t: (0.0, 1.0 - t, t)),
        (p2, p0, lambda t: (t, 0.0, 1.0 - t)),
    )
    for a, b, mk in edges:
        ex, ey = b.x() - a.x(), b.y() - a.y()
        elen2 = ex * ex + ey * ey
        t = 0.0 if elen2 < 1e-9 else max(0.0, min(1.0, ((px - a.x()) * ex + (py - a.y()) * ey) / elen2))
        qx, qy = a.x() + t * ex, a.y() + t * ey
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        if d2 < best_dist:
            best_dist = d2
            best_bary = mk(t)
    return best_bary


class ColorWheel(QWidget):
    """Rueda cromática HSV de alta precisión:
    - Anillo exterior de Matiz (RING = size * 0.093) con QConicalGradient de 12 paradas (13 puntos).
    - Triángulo equilátero interior (Negro arriba, Blanco abajo-izq, Matiz puro abajo-der) fila por fila HiDPI.
    - Doble contorno en marcadores (6.5px en anillo, 6px en triángulo) y bloqueo de zona al arrastrar."""
    colorChanged = Signal(QColor)
    hsvChanged = Signal(float, float, float)

    def __init__(self, size=None, parent=None):
        super().__init__(parent)
        sz = int(size or DENSITY["wheel_size"])
        self.setFixedSize(sz, sz)
        self.setCursor(Qt.CrossCursor)
        self._h = 0.66
        self._s = 0.72
        self._v = 0.95
        self._a = 1.0
        self._drag_zone = None  # None | "ring" | "triangle"
        self._ring_cache = None
        self._ring_key = None
        self._tri_cache = None
        self._tri_key = None

    def set_wheel_size(self, sz):
        sz = int(sz)
        if sz != self.width():
            self.setFixedSize(sz, sz)
            self._ring_cache = None
            self._tri_cache = None
            self.update()

    def hsv(self):
        return self._h, self._s, self._v

    def current_color(self):
        c = QColor.fromHsvF(max(0.0, min(1.0, self._h)), max(0.0, min(1.0, self._s)), max(0.0, min(1.0, self._v)), self._a)
        return c

    def set_color(self, col, emit=False):
        qc = QColor(col) if not isinstance(col, QColor) else col
        if not qc.isValid():
            return
        h, s, v, a = qc.getHsvF()
        if h >= 0.0:
            self._h = h
        self._s = max(0.0, min(1.0, s))
        self._v = max(0.0, min(1.0, v))
        self._a = max(0.0, min(1.0, a))
        self.update()
        if emit:
            self._emit()

    def set_hsv(self, h, s, v, emit=False):
        self._h = h % 1.0
        self._s = max(0.0, min(1.0, float(s)))
        self._v = max(0.0, min(1.0, float(v)))
        self.update()
        if emit:
            self._emit()

    def _geom(self):
        sz = float(self.width())
        margin = 5.0
        cx, cy = sz * 0.5, sz * 0.5
        outer = sz * 0.5 - margin
        ring_w = sz * 0.093
        inner = outer - ring_w
        r_tri = max(12.0, inner - 5.0)
        half_w = r_tri * math.sqrt(3.0) * 0.5
        p_blk = QPointF(cx, cy - r_tri)
        p_wht = QPointF(cx - half_w, cy + r_tri * 0.5)
        p_hue = QPointF(cx + half_w, cy + r_tri * 0.5)
        return sz, cx, cy, outer, inner, ring_w, r_tri, p_blk, p_wht, p_hue

    def _ensure_ring(self, sz, dpr):
        key = (int(sz), round(dpr, 2))
        if self._ring_key == key and self._ring_cache is not None:
            return
        px = max(16, int(round(sz * dpr)))
        img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        img.setDevicePixelRatio(dpr)

        _, cx, cy, outer, inner, _, _, _, _, _ = self._geom()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)

        outer_path = QPainterPath()
        outer_path.addEllipse(QPointF(cx, cy), outer, outer)
        inner_path = QPainterPath()
        inner_path.addEllipse(QPointF(cx, cy), inner, inner)
        ring_path = outer_path.subtracted(inner_path)

        grad = QConicalGradient(QPointF(cx, cy), 0.0)
        for i in range(13):
            t = i / 12.0
            grad.setColorAt(t, QColor.fromHsvF(t % 1.0, 1.0, 1.0))

        p.fillPath(ring_path, grad)
        p.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), outer, outer)
        p.drawEllipse(QPointF(cx, cy), inner, inner)
        p.end()

        self._ring_cache = QPixmap.fromImage(img)
        self._ring_key = key

    def _ensure_triangle(self, sz, dpr):
        h_q = round(self._h % 1.0, 4)
        key = (int(sz), round(dpr, 2), h_q)
        if self._tri_key == key and self._tri_cache is not None:
            return
        px = max(16, int(round(sz * dpr)))
        img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)

        _, cx, cy, _, _, _, r_tri, p_blk, p_wht, p_hue = self._geom()
        # Coordenadas físicas para render fila por fila ultra nítido
        s_cx = cx * dpr
        y_top = p_blk.y() * dpr
        y_bot = p_wht.y() * dpr
        half_w_px = (p_hue.x() - cx) * dpr

        tri_phys = QPainterPath()
        tri_phys.moveTo(p_blk.x() * dpr, p_blk.y() * dpr)
        tri_phys.lineTo(p_wht.x() * dpr, p_wht.y() * dpr)
        tri_phys.lineTo(p_hue.x() * dpr, p_hue.y() * dpr)
        tri_phys.closeSubpath()

        pure = QColor.fromHsvF(h_q % 1.0, 1.0, 1.0)
        pr, pg, pb = pure.red(), pure.green(), pure.blue()

        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.save()
        p.setClipPath(tri_phys)

        y0 = max(0, int(math.floor(y_top)) - 1)
        y1 = min(px - 1, int(math.ceil(y_bot)) + 1)
        span = max(1.0, y_bot - y_top)
        for yy in range(y0, y1 + 1):
            t = max(0.0, min(1.0, (yy + 0.5 - y_top) / span))
            hw = max(0.5, t * half_w_px)
            xl, xr = s_cx - hw, s_cx + hw
            gv = int(round(255.0 * t))
            c_left = QColor(gv, gv, gv)
            c_right = QColor(int(round(pr * t)), int(round(pg * t)), int(round(pb * t)))
            lg = QLinearGradient(xl, yy, xr, yy)
            lg.setColorAt(0.0, c_left)
            lg.setColorAt(1.0, c_right)
            p.fillRect(QRectF(xl - 1.0, yy, (xr - xl) + 2.0, 1.25), lg)
        p.restore()

        p.setPen(QPen(QColor(0, 0, 0, 70), max(1.0, dpr)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(tri_phys)
        p.end()

        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._tri_cache = pm
        self._tri_key = key

    def _draw_marker(self, p: QPainter, pt: QPointF, radius: float, fill: QColor):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(fill)
        p.setPen(QPen(QColor(0, 0, 0, 150), 3.4))
        p.drawEllipse(pt, radius, radius)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#FFFFFF"), 1.8))
        p.drawEllipse(pt, radius, radius)
        p.restore()

    def paintEvent(self, e):
        dpr = max(1.0, self.devicePixelRatioF())
        sz, cx, cy, outer, inner, _, _, p_blk, p_wht, p_hue = self._geom()
        self._ensure_ring(sz, dpr)
        self._ensure_triangle(sz, dpr)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._ring_cache)
        p.drawPixmap(0, 0, self._tri_cache)

        active_col = QColor.fromHsvF(self._h % 1.0, self._s, self._v)

        # Marcador del anillo (en QConicalGradient el ángulo crece antihorario desde +X)
        r_mid = (outer + inner) * 0.5
        ang = -2.0 * math.pi * (self._h % 1.0)
        ring_pt = QPointF(cx + math.cos(ang) * r_mid, cy + math.sin(ang) * r_mid)
        self._draw_marker(p, ring_pt, 6.5, active_col)

        # Marcador del triángulo (coordenadas baricéntricas exactas)
        w_blk = 1.0 - self._v
        w_wht = self._v * (1.0 - self._s)
        w_hue = self._v * self._s
        tri_pt = QPointF(
            w_blk * p_blk.x() + w_wht * p_wht.x() + w_hue * p_hue.x(),
            w_blk * p_blk.y() + w_wht * p_wht.y() + w_hue * p_hue.y(),
        )
        self._draw_marker(p, tri_pt, 6.0, active_col)

    def _emit(self):
        self.hsvChanged.emit(self._h, self._s, self._v)
        self.colorChanged.emit(self.current_color())

    def _update_from_pos(self, pos: QPointF):
        _, cx, cy, outer, inner, _, _, p_blk, p_wht, p_hue = self._geom()
        if self._drag_zone == "ring":
            dx, dy = pos.x() - cx, pos.y() - cy
            self._h = (-math.atan2(dy, dx) / (2.0 * math.pi)) % 1.0
            self.update()
            self._emit()
        elif self._drag_zone == "triangle":
            w_blk, w_wht, w_hue = _clamp_barycentric_triangle(pos.x(), pos.y(), p_blk, p_wht, p_hue)
            v = max(0.0, min(1.0, 1.0 - w_blk))
            s = self._s if v < 1e-4 else max(0.0, min(1.0, w_hue / v))
            self._s, self._v = s, v
            self.update()
            self._emit()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        _, cx, cy, outer, inner, _, _, p_blk, p_wht, p_hue = self._geom()
        dist = math.hypot(pos.x() - cx, pos.y() - cy)
        if inner - 3.0 <= dist <= outer + 5.0:
            self._drag_zone = "ring"
        else:
            self._drag_zone = "triangle"
        self._update_from_pos(pos)

    def mouseMoveEvent(self, e):
        if self._drag_zone is not None:
            self._update_from_pos(e.position())

    def mouseReleaseEvent(self, e):
        self._drag_zone = None


# =====================================================  Gotero de Pantalla + Lupa Circular 108px
class _Loupe:
    """Renderizador de la lupa circular de 108px + píldora inferior con punto de color y Hex."""

    @staticmethod
    def draw(p: QPainter, cursor_pt: QPointF, desktop_img: QImage, vrect: QRect):
        diam = 108.0
        crop_n = 17
        half_n = crop_n // 2

        # Posicionar la lupa ligeramente desplazada para no tapar el píxel exacto o centrada sobre el cursor
        lx = cursor_pt.x() + 18.0
        ly = cursor_pt.y() - diam - 28.0
        if lx + diam + 12 > vrect.width():
            lx = cursor_pt.x() - diam - 18.0
        if ly < 8:
            ly = cursor_pt.y() + 20.0

        lens_rect = QRectF(lx, ly, diam, diam)

        # Sombra suave rgba(0,0,0,70)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        for extra, alpha in ((5.0, 18), (3.0, 34), (1.5, 70)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawEllipse(lens_rect.adjusted(-extra, -extra + 2, extra, extra + 2))

        # Extraer recorte 17x17 alrededor del cursor en coordenadas de imagen física
        dpr = max(1.0, desktop_img.devicePixelRatio())
        ix = max(0, min(desktop_img.width() - 1, int(round(cursor_pt.x() * dpr))))
        iy = max(0, min(desktop_img.height() - 1, int(round(cursor_pt.y() * dpr))))
        center_col = desktop_img.pixelColor(ix, iy)

        sub = QImage(crop_n, crop_n, QImage.Format_RGB32)
        for dy in range(-half_n, half_n + 1):
            sy = max(0, min(desktop_img.height() - 1, iy + dy))
            for dx in range(-half_n, half_n + 1):
                sx = max(0, min(desktop_img.width() - 1, ix + dx))
                sub.setPixelColor(dx + half_n, dy + half_n, desktop_img.pixelColor(sx, sy))

        clip = QPainterPath()
        clip.addEllipse(lens_rect)
        p.save()
        p.setClipPath(clip)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawImage(lens_rect, sub)

        # Retícula central doble sobre el píxel central (negro 2.2px + blanco 1.2px)
        cell = diam / crop_n
        c_box = QRectF(lx + half_n * cell, ly + half_n * cell, cell, cell)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 220), 2.2))
        p.drawRect(c_box)
        p.setPen(QPen(QColor("#FFFFFF"), 1.2))
        p.drawRect(c_box)
        p.restore()

        # Doble borde circular (halo oscuro 1px exterior + blanco 2.4px interior)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 140), 3.4))
        p.drawEllipse(lens_rect)
        p.setPen(QPen(QColor("#FFFFFF"), 2.4))
        p.drawEllipse(lens_rect)

        # Píldora inferior (108x22, radius 11px, rgba(20, 23, 28, 240), border 1px)
        pill = QRectF(lx, ly + diam + 6.0, 108.0, 22.0)
        p.setPen(QPen(QColor(C["border"]), 1.0))
        p.setBrush(QColor(20, 23, 28, 240))
        p.drawRoundedRect(pill, 11.0, 11.0)

        # Punto circular 12x12 + código Hex 8.2pt DemiBold #F0F2F6
        dot = QRectF(pill.left() + 8.0, pill.top() + 5.0, 12.0, 12.0)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
        p.setBrush(center_col)
        p.drawEllipse(dot)

        p.setPen(QColor("#F0F2F6"))
        p.setFont(QFont("Consolas", 8, QFont.DemiBold))
        p.drawText(QRectF(dot.right() + 5.0, pill.top(), pill.width() - 28.0, pill.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, center_col.name().upper())
        p.restore()
        return center_col


class _ScreenColorPicker(QWidget):
    """Captura multipantalla congelada sin fugas de clic al viewport."""
    colorPicked = Signal(QColor)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

        screens = QGuiApplication.screens()
        vrect = QRect()
        for s in screens:
            vrect = vrect.united(s.geometry())
        if vrect.isEmpty():
            vrect = QRect(0, 0, 1920, 1080)
        self._vrect = vrect

        dpr = screens[0].devicePixelRatio() if screens else 1.0
        comp = QImage(max(1, int(vrect.width() * dpr)), max(1, int(vrect.height() * dpr)), QImage.Format_RGB32)
        comp.setDevicePixelRatio(dpr)
        comp.fill(QColor("#0D0F12"))

        p = QPainter(comp)
        for s in screens:
            pm = s.grabWindow(0)
            g = s.geometry()
            p.drawPixmap(QPoint(g.x() - vrect.x(), g.y() - vrect.y()), pm)
        p.end()

        self._desktop_img = comp
        self._desktop_pm = QPixmap.fromImage(comp)
        from PySide6.QtGui import QCursor
        gp = QCursor.pos()
        self._cursor = QPointF(gp.x() - vrect.x(), gp.y() - vrect.y())
        self._cur_col = QColor("#6366F1")
        self.setGeometry(vrect)

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabMouse()
        self.grabKeyboard()

    def _finish(self):
        self.releaseKeyboard()
        self.releaseMouse()
        self.close()
        self.deleteLater()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._desktop_pm)
        self._cur_col = _Loupe.draw(p, self._cursor, self._desktop_img, self._vrect)

    def mouseMoveEvent(self, e):
        self._cursor = e.position()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            col = QColor(self._cur_col)
            self._finish()
            self.colorPicked.emit(col)
        elif e.button() == Qt.RightButton:
            self._finish()
            self.cancelled.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish()
            self.cancelled.emit()


# =====================================================  Paleta de Colores (ColorPalettePanel / Dialog)
def _make_swatch_icon(hex_col: str, size: int) -> QIcon:
    dpr = 2
    px = max(8, int(size) * dpr)
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(1.2, 1.2, px - 2.4, px - 2.4)
    p.setBrush(QColor(hex_col))
    p.setPen(QPen(QColor(0, 0, 0, 60), 1.2 * dpr * 0.5))
    p.drawRoundedRect(r, 5.0, 5.0)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


class ColorPalettePanel(QWidget):
    """Panel de Paletas guardadas y editables en JSON con tamaño de muestras ajustable (18-42px)."""
    colorSelected = Signal(QColor)
    targetChanged = Signal(int)

    def __init__(self, standalone=True, targets=None, parent=None):
        super().__init__(parent)
        self._standalone = standalone
        self._current_color = QColor("#6366F1")
        self._swatch_size = int(UI_PREFS.get("swatch_size", 24))

        v = QVBoxLayout(self)
        v.setContentsMargins(10 if standalone else 0, 8 if standalone else 4, 10 if standalone else 0, 8 if standalone else 0)
        v.setSpacing(6)

        # Cabecera: Combo de paletas + Nueva (+) + Borrar (papelera)
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self.pal_cb = QComboBox()
        self.pal_cb.currentIndexChanged.connect(self._on_palette_changed)
        hdr.addWidget(self.pal_cb, 1)

        self.b_new_pal = QToolButton()
        self.b_new_pal.setFixedSize(24, 24)
        self.b_new_pal.setIcon(icons.state_icon("plus", 14))
        self.b_new_pal.setToolTip("Nueva paleta")
        self.b_new_pal.clicked.connect(self._new_palette)
        hdr.addWidget(self.b_new_pal)

        self.b_del_pal = QToolButton()
        self.b_del_pal.setFixedSize(24, 24)
        self.b_del_pal.setIcon(icons.state_icon("trash", 14))
        self.b_del_pal.setToolTip("Borrar paleta")
        self.b_del_pal.clicked.connect(self._delete_palette)
        hdr.addWidget(self.b_del_pal)
        v.addLayout(hdr)

        if standalone and targets:
            from widgets import SegmentedChoice
            self.target_seg = SegmentedChoice(targets)
            self.target_seg.changed.connect(self.targetChanged.emit)
            v.addWidget(self.target_seg)
        else:
            self.target_seg = None

        # Slider de tamaño de muestras (18-42px, defecto 24px)
        sz_row = QHBoxLayout()
        sz_row.setSpacing(6)
        lbl_sz = QLabel("Tamaño")
        lbl_sz.setObjectName("fieldLabel")
        from widgets import ClickableSlider
        self.sz_slider = ClickableSlider(Qt.Horizontal)
        self.sz_slider.setRange(18, 42)
        self.sz_slider.setValue(max(18, min(42, self._swatch_size)))
        self.sz_slider.valueChanged.connect(self._on_size_changed)
        sz_row.addWidget(lbl_sz)
        sz_row.addWidget(self.sz_slider, 1)
        v.addLayout(sz_row)

        # Rejilla de muestras (QListWidget en IconMode)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setMovement(QListView.Static)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.setMinimumHeight(88)
        self.grid.setMaximumHeight(152)
        self.grid.itemClicked.connect(self._on_item_clicked)
        v.addWidget(self.grid)

        # Acciones inferiores: Añadir (+) y Quitar (-)
        act = QHBoxLayout()
        act.setSpacing(6)
        self.b_add = QPushButton(" Añadir")
        self.b_add.setIcon(icons.state_icon("plus", 13))
        self.b_add.setFixedHeight(24)
        self.b_add.clicked.connect(self.add_current_color)
        self.b_rem = QPushButton(" Quitar")
        self.b_rem.setIcon(icons.state_icon("minus", 13))
        self.b_rem.setFixedHeight(24)
        self.b_rem.clicked.connect(self.remove_selected_color)
        act.addWidget(self.b_add, 1)
        act.addWidget(self.b_rem, 1)
        v.addLayout(act)

        self.reload_palettes()
        theme.bus.changed.connect(lambda _: self._refresh_icons())

    def _refresh_icons(self):
        self.b_new_pal.setIcon(icons.state_icon("plus", 14))
        self.b_del_pal.setIcon(icons.state_icon("trash", 14))
        self.b_add.setIcon(icons.state_icon("plus", 13))
        self.b_rem.setIcon(icons.state_icon("minus", 13))

    def set_current_color(self, col: QColor):
        if col.isValid():
            self._current_color = QColor(col)

    def _palettes(self):
        return UI_PREFS.setdefault("palettes", [dict(p, colors=list(p["colors"])) for p in theme.DEFAULT_PALETTES])

    def reload_palettes(self):
        pals = self._palettes()
        self.pal_cb.blockSignals(True)
        self.pal_cb.clear()
        for p in pals:
            self.pal_cb.addItem(p["name"])
        idx = max(0, min(len(pals) - 1, int(UI_PREFS.get("active_palette", 0))))
        self.pal_cb.setCurrentIndex(idx)
        self.pal_cb.blockSignals(False)
        self._populate_swatches()

    def _on_palette_changed(self, idx):
        if idx >= 0:
            UI_PREFS["active_palette"] = idx
            save_ui_prefs(UI_PREFS)
            self._populate_swatches()

    def _on_size_changed(self, val):
        self._swatch_size = int(val)
        UI_PREFS["swatch_size"] = self._swatch_size
        save_ui_prefs(UI_PREFS)
        self._populate_swatches()

    def _populate_swatches(self):
        self.grid.clear()
        sz = self._swatch_size
        self.grid.setIconSize(QSize(sz, sz))
        self.grid.setGridSize(QSize(sz + 8, sz + 8))
        pals = self._palettes()
        idx = self.pal_cb.currentIndex()
        if not (0 <= idx < len(pals)):
            return
        for hx in pals[idx].get("colors", []):
            up = hx.upper()
            it = QListWidgetItem(_make_swatch_icon(up, sz), "")
            it.setData(Qt.UserRole, up)
            it.setToolTip(up)
            it.setSizeHint(QSize(sz + 6, sz + 6))
            self.grid.addItem(it)
        self.b_del_pal.setEnabled(len(pals) > 1)

    def _on_item_clicked(self, item):
        hx = item.data(Qt.UserRole)
        if hx:
            c = QColor(hx)
            if c.isValid():
                self._current_color = c
                self.colorSelected.emit(c)

    def _new_palette(self):
        name, ok = QInputDialog.getText(self, "Nueva paleta", "Nombre de la paleta:")
        if ok and name.strip():
            pals = self._palettes()
            pals.append({"name": name.strip(), "colors": [self._current_color.name().upper()]})
            UI_PREFS["active_palette"] = len(pals) - 1
            save_ui_prefs(UI_PREFS)
            self.reload_palettes()

    def _delete_palette(self):
        pals = self._palettes()
        idx = self.pal_cb.currentIndex()
        if len(pals) > 1 and 0 <= idx < len(pals):
            del pals[idx]
            UI_PREFS["active_palette"] = max(0, idx - 1)
            save_ui_prefs(UI_PREFS)
            self.reload_palettes()

    def add_current_color(self):
        pals = self._palettes()
        idx = self.pal_cb.currentIndex()
        if 0 <= idx < len(pals):
            hx = self._current_color.name().upper()
            if hx not in pals[idx]["colors"]:
                pals[idx]["colors"].append(hx)
                save_ui_prefs(UI_PREFS)
                self._populate_swatches()

    def remove_selected_color(self):
        pals = self._palettes()
        idx = self.pal_cb.currentIndex()
        row = self.grid.currentRow()
        if 0 <= idx < len(pals) and 0 <= row < len(pals[idx]["colors"]):
            del pals[idx]["colors"][row]
            save_ui_prefs(UI_PREFS)
            self._populate_swatches()


# =====================================================  ColorEditor y ColorPickerPanel
class ColorEditor(QWidget):
    """Editor completo Studio UI:
    1. Selector segmentado de destino (opcional/contextual).
    2. ColorWheel centrado.
    3. Preview circular 22x22 + #hexInput validado + Gotero 24x24 + Copiar Hex 24x24 (feedback check 1.4s).
    4. Opacidad / Intensidad (NumericScrubLabel + ClickableSlider + QSpinBox 0-100%).
    5. Cajón desplegable (#drawerToggle, 22px) con ColorPalettePanel embebida."""
    colorChanged = Signal(QColor)
    opacityChanged = Signal(int)
    targetChanged = Signal(int)
    heightHintChanged = Signal()

    def __init__(self, targets=None, wheel_size=None, show_drawer=True, parent=None):
        super().__init__(parent)
        self._syncing = False
        self._picker = None

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        from widgets import ClickableSlider, NumericScrubLabel, SegmentedChoice, enable_numeric_drag

        if targets:
            self.target_seg = SegmentedChoice(targets)
            self.target_seg.changed.connect(self.targetChanged.emit)
            v.addWidget(self.target_seg)
        else:
            self.target_seg = None

        # Rueda centrada
        w_row = QHBoxLayout()
        w_row.setContentsMargins(0, 2, 0, 2)
        w_row.addStretch()
        self.wheel = ColorWheel(size=wheel_size)
        self.wheel.colorChanged.connect(self._on_wheel_color)
        w_row.addWidget(self.wheel)
        w_row.addStretch()
        v.addLayout(w_row)

        # Fila Hex y utilidades
        h_row = QHBoxLayout()
        h_row.setSpacing(5)
        self.preview_dot = QFrame()
        self.preview_dot.setFixedSize(22, 22)

        self.hex_input = _FocusSelectLineEdit("#6366F1")
        self.hex_input.setObjectName("hexInput")
        self.hex_input.setFixedHeight(24)
        self.hex_input.setMaxLength(9)
        self.hex_input.textEdited.connect(self._on_hex_edited)
        self.hex_input.editingFinished.connect(self._on_hex_finished)

        self.b_pick = QToolButton()
        self.b_pick.setFixedSize(24, 24)
        self.b_pick.setIcon(icons.state_icon("pipette", 16))
        self.b_pick.setIconSize(QSize(16, 16))
        self.b_pick.setToolTip("Gotero de pantalla con lupa")
        self.b_pick.clicked.connect(self.start_screen_pick)

        self.b_copy = QToolButton()
        self.b_copy.setFixedSize(24, 24)
        self.b_copy.setIcon(icons.state_icon("copy", 15))
        self.b_copy.setIconSize(QSize(15, 15))
        self.b_copy.setToolTip("Copiar código Hex")
        self.b_copy.clicked.connect(self._copy_hex)

        h_row.addWidget(self.preview_dot)
        h_row.addWidget(self.hex_input, 1)
        h_row.addWidget(self.b_pick)
        h_row.addWidget(self.b_copy)
        v.addLayout(h_row)

        # Fila de Opacidad / Intensidad
        o_row = QHBoxLayout()
        o_row.setSpacing(6)
        self.op_lbl = NumericScrubLabel("Opacidad")
        self.op_slider = ClickableSlider(Qt.Horizontal)
        self.op_slider.setRange(0, 100)
        self.op_slider.setValue(100)
        self.op_spin = QSpinBox()
        self.op_spin.setRange(0, 100)
        self.op_spin.setSuffix("%")
        self.op_spin.setFixedSize(54, 22)
        enable_numeric_drag(self.op_spin, self.op_lbl)

        self.op_slider.valueChanged.connect(self._on_op_slider)
        self.op_spin.valueChanged.connect(self._on_op_spin)
        o_row.addWidget(self.op_lbl)
        o_row.addWidget(self.op_slider, 1)
        o_row.addWidget(self.op_spin)
        v.addLayout(o_row)

        # Cajón desplegable de paleta embebida
        if show_drawer:
            self.drawer_btn = QPushButton("  Paleta de muestras")
            self.drawer_btn.setObjectName("drawerToggle")
            self.drawer_btn.setFixedHeight(22)
            self.drawer_btn.setIcon(icons.state_icon("chevron_down", 13))
            self.drawer_btn.clicked.connect(self._toggle_drawer)
            v.addWidget(self.drawer_btn)

            self.palette_drawer = ColorPalettePanel(standalone=False)
            self.palette_drawer.setVisible(False)
            self.palette_drawer.colorSelected.connect(lambda c: self.set_color(c, emit=True))
            v.addWidget(self.palette_drawer)
        else:
            self.drawer_btn = None
            self.palette_drawer = None

        self._sync_preview(self.wheel.current_color())
        theme.bus.changed.connect(lambda _: self._refresh_theme())

    def _refresh_theme(self):
        self.b_pick.setIcon(icons.state_icon("pipette", 16))
        self.b_copy.setIcon(icons.state_icon("copy", 15))
        if self.drawer_btn is not None and self.palette_drawer is not None:
            ic = "chevron_up" if self.palette_drawer.isVisible() else "chevron_down"
            self.drawer_btn.setIcon(icons.state_icon(ic, 13))
        if self.wheel.width() in (132, 152):
            self.wheel.set_wheel_size(DENSITY["wheel_size"])

    def set_targets(self, labels):
        if self.target_seg is not None:
            self.target_seg.set_options(labels)

    def current_color(self):
        c = self.wheel.current_color()
        c.setAlpha(int(round(self.op_spin.value() * 2.55)))
        return c

    def set_color(self, col, opacity=None, emit=False):
        qc = QColor(col) if not isinstance(col, QColor) else QColor(col)
        if not qc.isValid():
            return
        self._syncing = True
        self.wheel.set_color(qc, emit=False)
        if opacity is not None:
            self.op_slider.setValue(int(opacity))
            self.op_spin.setValue(int(opacity))
        self._set_hex_valid(True)
        self.hex_input.setText(qc.name().upper())
        self._sync_preview(qc)
        if self.palette_drawer is not None:
            self.palette_drawer.set_current_color(qc)
        self._syncing = False
        if emit:
            self.colorChanged.emit(self.current_color())

    def _sync_preview(self, qc: QColor):
        self.preview_dot.setStyleSheet(
            f"background: {qc.name()}; border-radius: 11px; border: 1px solid rgba(128, 128, 140, 150);"
        )

    def _set_hex_valid(self, ok: bool):
        cur = bool(self.hex_input.property("invalid"))
        want = not ok
        if cur != want:
            self.hex_input.setProperty("invalid", want)
            self.hex_input.style().unpolish(self.hex_input)
            self.hex_input.style().polish(self.hex_input)

    def _on_wheel_color(self, qc: QColor):
        if self._syncing:
            return
        self._syncing = True
        self._set_hex_valid(True)
        self.hex_input.setText(qc.name().upper())
        self._sync_preview(qc)
        if self.palette_drawer is not None:
            self.palette_drawer.set_current_color(qc)
        self._syncing = False
        self.colorChanged.emit(self.current_color())

    def _on_hex_edited(self, txt: str):
        s = txt.strip()
        if not _HEX_RE.match(s):
            self._set_hex_valid(False)
            return
        norm = s if s.startswith("#") else f"#{s}"
        qc = QColor(norm)
        if not qc.isValid():
            self._set_hex_valid(False)
            return
        self._set_hex_valid(True)
        self._syncing = True
        self.wheel.set_color(qc, emit=False)
        self._sync_preview(qc)
        if len(norm) == 9:
            pct = int(round(qc.alpha() / 2.55))
            self.op_slider.setValue(pct)
            self.op_spin.setValue(pct)
        if self.palette_drawer is not None:
            self.palette_drawer.set_current_color(qc)
        self._syncing = False
        self.colorChanged.emit(self.current_color())

    def _on_hex_finished(self):
        if self.hex_input.property("invalid"):
            self._set_hex_valid(True)
            self.hex_input.setText(self.wheel.current_color().name().upper())
        else:
            s = self.hex_input.text().strip()
            if s and not s.startswith("#"):
                self.hex_input.setText("#" + s.upper())
            else:
                self.hex_input.setText(s.upper())

    def _on_op_slider(self, val):
        if self._syncing:
            return
        self._syncing = True
        self.op_spin.setValue(val)
        self._syncing = False
        self.opacityChanged.emit(val)
        self.colorChanged.emit(self.current_color())

    def _on_op_spin(self, val):
        if self._syncing:
            return
        self._syncing = True
        self.op_slider.setValue(val)
        self._syncing = False
        self.opacityChanged.emit(val)
        self.colorChanged.emit(self.current_color())

    def start_screen_pick(self):
        self._picker = _ScreenColorPicker(self)
        self._picker.colorPicked.connect(lambda c: self.set_color(c, emit=True))
        self._picker.start()

    def _copy_hex(self):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.wheel.current_color().name().upper())
        self.b_copy.setIcon(icons.icon("check", C["selection_text"], 15))
        QTimer.singleShot(1400, lambda: self.b_copy.setIcon(icons.state_icon("copy", 15)))

    def _toggle_drawer(self):
        if self.palette_drawer is None:
            return
        vis = not self.palette_drawer.isVisible()
        self.palette_drawer.setVisible(vis)
        self.drawer_btn.setIcon(icons.state_icon("chevron_up" if vis else "chevron_down", 13))
        self.updateGeometry()
        self.heightHintChanged.emit()


class ColorPickerPanel(QWidget):
    """Panel autónomo de Rueda de Color contextual conectado al espacio de trabajo activo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._targets = [("bg", "Fondo / Escena"), ("char", "Avatar / Pista"), ("accent", "Contorno / Luz")]
        self._cur_idx = 0
        self._updating = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.editor = ColorEditor(targets=[t[1] for t in self._targets], show_drawer=True)
        self.editor.targetChanged.connect(self._on_target_changed)
        self.editor.colorChanged.connect(self._on_color_changed)
        self.editor.opacityChanged.connect(self._on_opacity_changed)
        v.addWidget(self.editor)

    def bind_workspace(self, ws):
        self._ws = ws
        if ws is None:
            return
        kind = getattr(ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            self._targets = [("layer", "Tinte Capa"), ("hair", "Pelo"), ("eyes", "Ojos"), ("bg", "Fondo Rig")]
        elif kind == "vtuber3d":
            self._targets = [("hair", "Cabello 3D"), ("eyes", "Ojos 3D"), ("outfit", "Ropa 3D"), ("accent", "Luz / Acento")]
        else:
            self._targets = [("bg", "Fondo / Chroma"), ("char", "Avatar / Pista"), ("outline", "Contorno / Capa")]
        self._cur_idx = 0
        self.editor.set_targets([t[1] for t in self._targets])
        self._pull_from_ws()

    def _on_target_changed(self, idx):
        self._cur_idx = max(0, min(len(self._targets) - 1, idx))
        self._pull_from_ws()

    def _pull_from_ws(self):
        if self._ws is None or not self._targets:
            return
        self._updating = True
        key = self._targets[self._cur_idx][0]
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        col = "#6366F1"
        op = 100
        try:
            if kind == "vtuber":
                if key == "layer":
                    lyr = self._ws._cur_layer()
                    if lyr:
                        col = lyr["color"].get("tint_hex") or "#6366F1"
                        op = int(lyr.get("opacity", 100))
                elif key == "bg":
                    col = self._ws.rig.get("bg_color", "#0E1014")
            elif kind == "vtuber3d":
                ap = self._ws.data.get("appearance", {})
                map3 = {"hair": "hair_color", "eyes": "eye_color", "outfit": "outfit_color", "accent": "accent_color"}
                col = ap.get(map3.get(key, "hair_color"), "#6366F1")
            else:
                if key == "bg":
                    col = self._ws.data.get("bg_color", "#0E1014")
                elif key == "char":
                    ch = self._ws.cur()
                    if ch:
                        col = ch.get("color", "#6366F1")
                        op = int(ch.get("opacity", 100))
                elif key == "outline":
                    pr = self._ws._cur_prop()
                    ch = self._ws.cur()
                    if pr:
                        col = pr.get("color", "#FFFFFF")
                        op = int(pr.get("opacity", 100))
                    elif ch:
                        col = ch.get("outline_color", "#FFFFFF")
        except Exception:
            pass
        self.editor.set_color(col, opacity=op, emit=False)
        self._updating = False

    def _on_color_changed(self, qc: QColor):
        if self._updating or self._ws is None or not self._targets:
            return
        hx = qc.name()
        key = self._targets[self._cur_idx][0]
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        try:
            if kind == "vtuber":
                import vtuber_rig as VR
                if key == "layer":
                    lyr = self._ws._cur_layer()
                    if lyr:
                        lyr["color"]["tint_hex"] = hx
                        if lyr["color"].get("tint_amt", 0) == 0:
                            lyr["color"]["tint_amt"] = 75
                        self._ws.mark_dirty()
                        self._ws.canvas.update()
                elif key in ("hair", "eyes"):
                    VR.set_rig_group_color(self._ws.rig, key, hx, 84)
                    self._ws.mark_dirty()
                    self._ws.canvas.update()
                elif key == "bg":
                    self._ws.rig["bg_type"] = "dark"
                    self._ws.rig["bg_color"] = hx
                    self._ws.mark_dirty()
                    self._ws.canvas.update()
            elif kind == "vtuber3d":
                map3 = {"hair": "hair_color", "eyes": "eye_color", "outfit": "outfit_color", "accent": "accent_color"}
                ck = map3.get(key, "hair_color")
                self._ws.data.setdefault("appearance", {})[ck] = hx
                if hasattr(self._ws, "_col_btns") and ck in self._ws._col_btns:
                    self._ws._col_btns[ck].setIcon(icons.icon("palette", hx, 14))
                self._ws.mark_dirty()
            else:
                if key == "bg":
                    self._ws.data["bg_color"] = hx
                    if self._ws.sdata().get("bg_type") == "transparent":
                        self._ws.sdata()["bg_type"] = "color"
                    self._ws.apply_scene()
                    self._ws.mark_dirty()
                elif key == "char":
                    ch = self._ws.cur()
                    if ch:
                        self._ws._apply_char_color_live(ch, qc)
                elif key == "outline":
                    pr = self._ws._cur_prop()
                    ch = self._ws.cur()
                    if pr:
                        pr["color"] = hx
                        if pr["id"] in self._ws.prop_items:
                            self._ws.prop_items[pr["id"]].update()
                        self._ws.mark_dirty()
                    elif ch:
                        ch["outline_color"] = hx
                        ch["outline"] = True
                        if ch["id"] in self._ws.items:
                            self._ws.items[ch["id"]].update()
                        self._ws.mark_dirty()
        except Exception:
            pass

    def _on_opacity_changed(self, val: int):
        if self._updating or self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        try:
            if kind == "vtuber":
                lyr = self._ws._cur_layer()
                if lyr:
                    lyr["opacity"] = int(val)
                    self._ws.canvas.update()
            elif kind in ("video", "tiktok"):
                pr = self._ws._cur_prop()
                ch = self._ws.cur()
                if pr:
                    self._ws._propset("opacity", int(val))
                elif ch:
                    self._ws.cset("opacity", int(val))
        except Exception:
            pass
