"""Lienzo 2D interactivo de estudio: personajes VTuber, props/overlays, tiradores de escala y rotación,
guías inteligentes de alineación, zoom/paneo, transiciones de escena y salida limpia OBS."""
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView, QMenu

import model as M
import theme
from theme import C
from vtuber_engine import CharAnimState, SHARED_ASSETS, draw_character_full, draw_prop


def _snap_to_guides(win, exclude_id, x, y, w, h):
    """Guías inteligentes (centros, tercios y alineación con otros elementos)."""
    guides = []
    if not win.data.get("settings", {}).get("smart_guides", True):
        return x, y, guides
    cw = float(win.data.get("canvas_w", M.W))
    ch = float(win.data.get("canvas_h", M.H))
    tol = 10.0

    xs_ref = [cw * 0.5, cw / 3.0, cw * 2.0 / 3.0, cw * 0.05, cw * 0.95]
    ys_ref = [ch * 0.5, ch / 3.0, ch * 2.0 / 3.0, ch * 0.05, ch * 0.95]

    cx, cy = x + w * 0.5, y + h * 0.5
    best_dx, gx = tol + 1.0, None
    for rx in xs_ref:
        d = rx - cx
        if abs(d) < abs(best_dx):
            best_dx, gx = d, rx
    if gx is not None and abs(best_dx) <= tol:
        x += best_dx
        guides.append(("v", gx))

    best_dy, gy = tol + 1.0, None
    for ry in ys_ref:
        d = ry - cy
        if abs(d) < abs(best_dy):
            best_dy, gy = d, ry
    if gy is not None and abs(best_dy) <= tol:
        y += best_dy
        guides.append(("h", gy))

    return x, y, guides


class CharacterItem(QGraphicsItem):
    """Personaje 2D VTuber interactivo: renderizado idéntico a exportación + 4 tiradores de escala + rotación."""

    def __init__(self, win, cid):
        super().__init__()
        self.win = win
        self.cid = cid
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        self.anim = CharAnimState()
        self.imgs = {}             # slot_key -> ([QImage...], [delay_ms...])
        self.avail_keys = set()
        self.ref_size = (400, 560)
        self.base_img = QImage()
        self.has_custom_sprites = False
        self.ch_ref = None
        self.edit = True
        self.label = ""
        self.color = C["accent"]
        self.base_rotation = 0.0
        self._now = 0.0
        self._hovered = False
        self._drag_mode = None     # None | 'rot' | 'scale_tl' | 'scale_tr' | 'scale_bl' | 'scale_br'
        self._scale_anchor = None

    @property
    def state(self):
        return self.anim.state

    @property
    def cur_key(self):
        return self.anim.cur_key

    @property
    def force(self):
        return self.anim.force

    @force.setter
    def force(self, v):
        self.anim.force = v

    def load(self, ch):
        self.ch_ref = ch
        self.label = ch["name"]
        self.color = ch.get("color", C["accent"])
        ref_w, ref_h, imgs, has_custom = SHARED_ASSETS.build_character_images(ch, dpr=1)
        if (ref_w, ref_h) != self.ref_size:
            self.prepareGeometryChange()
            self.ref_size = (ref_w, ref_h)
        self.imgs = imgs
        self.avail_keys = {k for k, v in imgs.items() if v is not None}
        self.has_custom_sprites = has_custom
        first_k = next((k for k in M.KEYS if k in self.avail_keys), None)
        self.base_img = imgs[first_k][0][0] if first_k else QImage()
        self.anim.cur_key = self.anim.pick_key(self.avail_keys)
        self.setTransformOriginPoint(ref_w * 0.5, ref_h * 0.5)
        self.update()

    def boundingRect(self):
        w, h = self.ref_size
        # Margen holgado para física de rebote/flotación, sombra, contorno, accesorios y emotes
        return QRectF(-160, -200, w + 320, h + 300)

    def shape(self):
        path = QPainterPath()
        w, h = self.ref_size
        path.addRect(QRectF(0, 0, w, h))
        if self.edit and self.isSelected():
            hr = max(14.0, 18.0 / max(0.2, self.scale()))
            for pt in self._corner_pts():
                path.addEllipse(pt, hr, hr)
            path.addEllipse(self._rot_handle_pos(), hr * 1.2, hr * 1.2)
        return path

    def set_base_rotation(self, deg):
        deg = ((float(deg) + 180.0) % 360.0) - 180.0
        if abs(deg) < 0.05:
            deg = 0.0
        self.base_rotation = deg
        self.update()

    def set_edit(self, on):
        self.edit = on
        self.setFlag(QGraphicsItem.ItemIsMovable, on)
        self.setFlag(QGraphicsItem.ItemIsSelectable, on)
        if not on:
            self.setSelected(False)
        self.update()

    def update_anim(self, ch, level, now, dt=0.02, mouse_norm=(0.0, 0.0), face_norm=(0.0, 0.0, 0.0),
                    someone_else_talking=False, emotion_state=None, face_full=None):
        self.ch_ref = ch
        self._now = now
        self.anim.step(ch, level, now, dt, self.avail_keys,
                       mouse_norm=mouse_norm, face_norm=face_norm,
                       someone_else_talking=someone_else_talking,
                       emotion_state=emotion_state, face_full=face_full)
        self.update()

    def _corner_pts(self):
        w, h = self.ref_size
        return [QPointF(0, 0), QPointF(w, 0), QPointF(0, h), QPointF(w, h)]

    def _rot_handle_pos(self):
        w, h = self.ref_size
        return QPointF(w * 0.5, h + 32.0)

    def _hit_handle(self, pos):
        if not (self.edit and self.isSelected()):
            return None
        hr = max(14.0, 18.0 / max(0.2, self.scale()))
        hp = self._rot_handle_pos()
        if math.hypot(pos.x() - hp.x(), pos.y() - hp.y()) <= hr * 1.15:
            return "rot"
        for tag, pt in zip(("tl", "tr", "bl", "br"), self._corner_pts()):
            if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= hr:
                return f"scale_{tag}"
        return None

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.edit:
            x, y = value.x(), value.y()
            if self.win.snap:
                g = max(5, int(self.win.data.get("settings", {}).get("grid_size", 20)))
                x, y = round(x / g) * g, round(y / g) * g
            w, h = self.ref_size[0] * self.scale(), self.ref_size[1] * self.scale()
            x, y, guides = _snap_to_guides(self.win, self.cid, x, y, w, h)
            if hasattr(self.win, "view"):
                self.win.view.set_smart_guides(guides)
            return QPointF(x, y)
        return super().itemChange(change, value)

    def hoverMoveEvent(self, e):
        if not self._hovered:
            self._hovered = True
            self.update()
        h = self._hit_handle(e.pos())
        if h == "rot":
            self.setCursor(Qt.OpenHandCursor)
        elif h in ("scale_tl", "scale_br"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif h in ("scale_tr", "scale_bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        self._hovered = False
        if not self._drag_mode:
            self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            h = self._hit_handle(e.pos())
            if h == "rot":
                self._drag_mode = "rot"
                self.setCursor(Qt.ClosedHandCursor)
                e.accept()
                return
            if h and h.startswith("scale_"):
                self._drag_mode = h
                w, h_px = self.ref_size
                c_sc = self.mapToScene(QPointF(w * 0.5, h_px * 0.5))
                d0 = max(20.0, math.hypot(e.scenePos().x() - c_sc.x(), e.scenePos().y() - c_sc.y()))
                self._scale_anchor = (c_sc, d0, self.scale(), self.pos())
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self._hit_handle(e.pos()) == "rot":
            self._drag_mode = None
            self.set_base_rotation(0.0)
            self.win.store_item(self)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_mode == "rot":
            w, h = self.ref_size
            c_scene = self.mapToScene(QPointF(w * 0.5, h * 0.5))
            sp = e.scenePos()
            deg = math.degrees(math.atan2(sp.y() - c_scene.y(), sp.x() - c_scene.x())) - 90.0
            deg = ((deg + 180.0) % 360.0) - 180.0
            if e.modifiers() & Qt.ShiftModifier:
                deg = round(deg / 15.0) * 15.0
            self.set_base_rotation(deg)
            self.win.sync_rotation_live(self)
            e.accept()
            return
        if self._drag_mode and self._drag_mode.startswith("scale_") and self._scale_anchor is not None:
            c_sc, d0, sc0, orig_pos = self._scale_anchor
            d1 = max(12.0, math.hypot(e.scenePos().x() - c_sc.x(), e.scenePos().y() - c_sc.y()))
            new_sc = max(0.05, min(4.0, sc0 * (d1 / d0)))
            self.setScale(new_sc)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if hasattr(self.win, "view"):
            self.win.view.set_smart_guides([])
        if self._drag_mode:
            self._drag_mode = None
            self._scale_anchor = None
            self.unsetCursor()
            self.win.store_item(self)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)
        self.win.store_item(self)

    def wheelEvent(self, e):
        if self.edit:
            if e.modifiers() & Qt.ShiftModifier:
                step = 5.0 if e.delta() > 0 else -5.0
                self.set_base_rotation(self.base_rotation + step)
                self.win.store_item(self)
            else:
                self.setScale(max(0.05, min(4.0, self.scale() * (1.05 ** (e.delta() / 120)))))
                self.win.store_item(self)
            e.accept()

    def contextMenuEvent(self, e):
        m = QMenu()
        col = m.addAction("Cambiar color...")
        rst_rot = m.addAction("Restablecer rotación (0°)") if abs(self.base_rotation) > 0.1 else None
        m.addSeparator()
        front, back = m.addAction("Traer al frente"), m.addAction("Enviar al fondo")
        a = m.exec(e.screenPos())
        if a == col:
            self.win.pick_char_color(self.cid)
        elif rst_rot is not None and a == rst_rot:
            self.set_base_rotation(0.0)
            self.win.store_item(self)
        elif a in (front, back):
            self.win.change_z(self, 1 if a == front else -1)

    def paint(self, p, opt, widget=None):
        if self.ch_ref is None:
            return
        # Dibujar en coordenadas locales usando exactamente la misma función que el exportador
        pl_local = {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": self.base_rotation, "visible": True}
        draw_character_full(p, self.ch_ref, pl_local, self.anim, self.imgs, self.ref_size, self._now, SHARED_ASSETS)

        clean = widget is None or getattr(widget.parent(), "clean", False)
        sel = self.isSelected()
        if self.edit and not clean and (sel or self._hovered):
            r = QRectF(0, 0, self.ref_size[0], self.ref_size[1])
            inv_s = 1.0 / max(0.25, self.scale())
            col = QColor(C["accent"] if sel else "#ffffff66")
            p.save()
            p.setPen(QPen(col, (2.0 if sel else 1.2) * inv_s, Qt.SolidLine if sel else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)

            if sel:
                hs = 5.5 * inv_s
                p.setBrush(QColor("#FFFFFF"))
                p.setPen(QPen(col, 1.8 * inv_s))
                for pt in self._corner_pts():
                    p.drawRect(QRectF(pt.x() - hs, pt.y() - hs, hs * 2, hs * 2))

                hp = self._rot_handle_pos()
                p.setPen(QPen(col, 1.8 * inv_s))
                p.drawLine(QPointF(r.center().x(), r.bottom()), QPointF(hp.x(), hp.y() - 10 * inv_s))
                p.setBrush(col if self._drag_mode == "rot" else QColor("#151518"))
                p.drawEllipse(hp, 10 * inv_s, 10 * inv_s)
                if self._drag_mode == "rot":
                    p.setFont(QFont("Cascadia Mono", int(max(9, 11 * inv_s)), QFont.Bold))
                    pill = QRectF(hp.x() + 16 * inv_s, hp.y() - 12 * inv_s, 54 * inv_s, 24 * inv_s)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("#151518ee"))
                    p.drawRoundedRect(pill, 5 * inv_s, 5 * inv_s)
                    p.setPen(QColor("white"))
                    p.drawText(pill, Qt.AlignCenter, f"{int(round(self.base_rotation))}°")

            # Etiqueta limpia en la esquina superior izquierda
            tag_h = 24 * inv_s
            f = QFont("Segoe UI", max(8, int(11 * inv_s)), QFont.DemiBold)
            p.setFont(f)
            tw = max(72 * inv_s, p.fontMetrics().horizontalAdvance(self.label) + 26 * inv_s)
            tag = QRectF(r.x(), r.y() - tag_h - 6 * inv_s, tw, tag_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["accent"] if sel else "#151518dd"))
            p.drawRoundedRect(tag, 4 * inv_s, 4 * inv_s)
            if self.anim.state > 0:
                p.setBrush(QColor(C["accent2"]))
                p.drawEllipse(QPointF(tag.left() + 10 * inv_s, tag.center().y()), 3.5 * inv_s, 3.5 * inv_s)
            p.setPen(QColor("white"))
            p.drawText(tag.adjusted(8 * inv_s if self.anim.state > 0 else 0, 0, 0, 0), Qt.AlignCenter, self.label)
            p.restore()


class PropItem(QGraphicsItem):
    """Capa de escena interactiva (Imagen/Logo, Texto/Lower Third o Visualizador de audio 2D)."""

    def __init__(self, win, prop):
        super().__init__()
        self.win = win
        self.pid = prop["id"]
        self.prop = prop
        self.edit = True
        self._now = 0.0
        self._spectrum = None
        self._master_lvl = 0.0
        self._drag_mode = None
        self._scale_anchor = None
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.sync_from_prop()

    def sync_from_prop(self):
        self.prepareGeometryChange()
        self.setPos(float(self.prop.get("x", 0.0)), float(self.prop.get("y", 0.0)))
        self.setZValue(float(self.prop.get("z", 50)))
        self.setVisible(bool(self.prop.get("visible", True)))
        locked = bool(self.prop.get("locked", False))
        self.setFlag(QGraphicsItem.ItemIsMovable, self.edit and not locked)
        self.setFlag(QGraphicsItem.ItemIsSelectable, self.edit and not locked)
        self.update()

    def set_edit(self, on):
        self.edit = on
        locked = bool(self.prop.get("locked", False))
        self.setFlag(QGraphicsItem.ItemIsMovable, on and not locked)
        self.setFlag(QGraphicsItem.ItemIsSelectable, on and not locked)
        if not on:
            self.setSelected(False)
        self.update()

    def tick(self, now, spectrum, master_lvl):
        self._now = now
        self._spectrum = spectrum
        self._master_lvl = master_lvl
        if self.prop.get("kind") == "visualizer" or self.prop.get("anim", "none") != "none":
            self.update()

    def boundingRect(self):
        w = float(self.prop.get("w", 360)) * float(self.prop.get("scale", 1.0))
        h = float(self.prop.get("h", 120)) * float(self.prop.get("scale", 1.0))
        return QRectF(-60, -60, w + 120, h + 120)

    def shape(self):
        path = QPainterPath()
        w = float(self.prop.get("w", 360)) * float(self.prop.get("scale", 1.0))
        h = float(self.prop.get("h", 120)) * float(self.prop.get("scale", 1.0))
        path.addRect(QRectF(0, 0, w, h))
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.edit:
            x, y = value.x(), value.y()
            if self.win.snap:
                g = max(5, int(self.win.data.get("settings", {}).get("grid_size", 20)))
                x, y = round(x / g) * g, round(y / g) * g
            w = float(self.prop.get("w", 360)) * float(self.prop.get("scale", 1.0))
            h = float(self.prop.get("h", 120)) * float(self.prop.get("scale", 1.0))
            x, y, guides = _snap_to_guides(self.win, self.pid, x, y, w, h)
            if hasattr(self.win, "view"):
                self.win.view.set_smart_guides(guides)
            return QPointF(x, y)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e):
        if hasattr(self.win, "view"):
            self.win.view.set_smart_guides([])
        super().mouseReleaseEvent(e)
        self.prop["x"] = self.pos().x()
        self.prop["y"] = self.pos().y()
        self.win.store_prop_item(self)

    def wheelEvent(self, e):
        if self.edit and not self.prop.get("locked", False):
            sc = max(0.1, min(4.0, float(self.prop.get("scale", 1.0)) * (1.05 ** (e.delta() / 120))))
            self.prop["scale"] = round(sc, 3)
            self.sync_from_prop()
            self.win.store_prop_item(self)
            e.accept()

    def paint(self, p, opt, widget=None):
        local_prop = dict(self.prop, x=0.0, y=0.0)
        draw_prop(p, local_prop, self._now, SHARED_ASSETS, spectrum=self._spectrum, master_level=self._master_lvl)

        clean = widget is None or getattr(widget.parent(), "clean", False)
        if self.edit and not clean and self.isSelected():
            w = float(self.prop.get("w", 360)) * float(self.prop.get("scale", 1.0))
            h = float(self.prop.get("h", 120)) * float(self.prop.get("scale", 1.0))
            r = QRectF(0, 0, w, h)
            p.save()
            p.setPen(QPen(QColor(C["accent2"]), 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setBrush(QColor("white"))
            for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))
            p.restore()


class CanvasView(QGraphicsView):
    """Vista del lienzo con zoom, paneo, damero de transparencia, guías inteligentes y drag&drop."""
    imageDropped = Signal(str, float, float)
    zoomChanged = Signal(int)

    def __init__(self, scene, guides=True, clean=False):
        super().__init__(scene)
        self.guides = guides
        self.clean = clean
        self.canvas_w = M.W
        self.canvas_h = M.H
        self.transparent_bg = False
        self.is_empty = False
        self._smart_guides = []
        self._user_zoom = False
        self._zoom_factor = 1.0
        self._panning = False
        self._pan_start = None
        self.mouse_norm = (0.0, 0.0)
        self._trans_pm = None
        self._trans_alpha = 0.0
        self._trans_mode = "fade"
        self._viewport_light = False

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._sync_bg_brush()
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setAcceptDrops(not clean)
        theme.bus.changed.connect(lambda _: self._sync_bg_brush())

    def set_viewport_light(self, light_on: bool):
        self._viewport_light = bool(light_on)
        self._sync_bg_brush()

    def _sync_bg_brush(self):
        col = "#E4E7ED" if self._viewport_light else C["canvas"]
        self.setBackgroundBrush(QBrush(QColor(col)))
        self.viewport().update()

    def set_canvas_size(self, w, h):
        self.canvas_w, self.canvas_h = int(w), int(h)
        self.scene().setSceneRect(0, 0, self.canvas_w, self.canvas_h)
        self.fit_canvas()

    def set_smart_guides(self, guides):
        if guides != self._smart_guides:
            self._smart_guides = list(guides)
            self.viewport().update()

    def start_transition(self, prev_pixmap, mode="fade"):
        if mode == "cut" or prev_pixmap.isNull():
            self._trans_pm = None
            self._trans_alpha = 0.0
            return
        self._trans_pm = prev_pixmap
        self._trans_mode = mode
        self._trans_alpha = 1.0
        self.viewport().update()

    def step_transition(self, dt, duration_ms=280):
        if self._trans_pm is not None and self._trans_alpha > 0.0:
            self._trans_alpha = max(0.0, self._trans_alpha - dt / max(0.05, duration_ms / 1000.0))
            if self._trans_alpha <= 0.001:
                self._trans_pm = None
            self.viewport().update()

    def fit_canvas(self):
        self._user_zoom = False
        m = 28 if not self.clean else 0
        r = QRectF(-m, -m, self.canvas_w + 2 * m, self.canvas_h + 2 * m)
        self.fitInView(r, Qt.KeepAspectRatio)
        self._zoom_factor = self.transform().m11()
        self.zoomChanged.emit(int(round(self._zoom_factor * 100)))

    def zoom_by(self, factor):
        self._user_zoom = True
        cur = self.transform().m11()
        target = max(0.12, min(4.0, cur * factor))
        ratio = target / max(1e-4, cur)
        self.scale(ratio, ratio)
        self._zoom_factor = target
        self.zoomChanged.emit(int(round(target * 100)))

    def zoom_100(self):
        self._user_zoom = True
        self.resetTransform()
        self.centerOn(self.canvas_w * 0.5, self.canvas_h * 0.5)
        self._zoom_factor = 1.0
        self.zoomChanged.emit(100)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._user_zoom:
            self.fit_canvas()

    def wheelEvent(self, e):
        if not self.clean and (e.modifiers() & Qt.ControlModifier):
            self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1.0 / 1.15)
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if not self.clean and e.button() == Qt.MiddleButton:
            self._panning = True
            self._user_zoom = True
            self._pan_start = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # Actualizar coordenadas normalizadas del cursor para tracking de mirada/paralaje
        sp = self.mapToScene(e.position().toPoint())
        nx = max(-1.0, min(1.0, (sp.x() / max(1.0, self.canvas_w) - 0.5) * 2.0))
        ny = max(-1.0, min(1.0, (sp.y() / max(1.0, self.canvas_h) - 0.5) * 2.0))
        self.mouse_norm = (nx, ny)
        if self._panning and self._pan_start is not None:
            delta = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning and e.button() == Qt.MiddleButton:
            self._panning = False
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if self.clean and e.key() == Qt.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if not self.clean and e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                    e.acceptProposedAction()
                    return

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        for u in e.mimeData().urls():
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                self.imageDropped.emit(u.toLocalFile(), sp.x(), sp.y())
                e.acceptProposedAction()
                return

    def drawBackground(self, p, rect):
        super().drawBackground(p, rect)
        if self.transparent_bg:
            # Damero profesional discreto dentro del área del lienzo
            W, H = self.canvas_w, self.canvas_h
            tile = 32
            p.save()
            p.setClipRect(QRectF(0, 0, W, H))
            p.fillRect(QRectF(0, 0, W, H), QColor("#1A1A1E"))
            c2 = QColor("#242429")
            for y in range(0, H, tile):
                for x in range(0, W, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        p.fillRect(x, y, tile, tile, c2)
            p.restore()

    def drawForeground(self, p, rect):
        W, H = self.canvas_w, self.canvas_h
        if self._trans_pm is not None and self._trans_alpha > 0.0:
            p.save()
            if self._trans_mode == "slide":
                dx = (1.0 - self._trans_alpha) * W
                p.setOpacity(self._trans_alpha)
                p.drawPixmap(QRectF(-dx, 0, W, H), self._trans_pm, QRectF(self._trans_pm.rect()))
            else:
                p.setOpacity(self._trans_alpha)
                p.drawPixmap(QRectF(0, 0, W, H), self._trans_pm, QRectF(self._trans_pm.rect()))
            p.restore()

        if not self.clean:
            p.setPen(QPen(QColor("#33333A"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(0, 0, W, H)

            if self.is_empty:
                p.save()
                is_vert = H > W
                p.setPen(QColor(255, 255, 255, 118))
                p.setFont(QFont("Segoe UI", 38 if is_vert else 28, QFont.DemiBold))
                p.drawText(QRectF(W * 0.06, H * 0.41, W * 0.88, 72), Qt.AlignCenter, "Espacio de trabajo vacío")
                p.setPen(QColor(255, 255, 255, 72))
                p.setFont(QFont("Segoe UI", 24 if is_vert else 18))
                p.drawText(QRectF(W * 0.08, H * 0.47, W * 0.84, 140),
                           Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                           "Añade un personaje VTuber, importa un rig .vtr o arrastra capas desde el panel lateral")
                p.restore()

        if self.guides and not self.clean:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1.5, Qt.DashLine))
            for i in (1, 2):
                p.drawLine(int(W * i / 3), 0, int(W * i / 3), H)
                p.drawLine(0, int(H * i / 3), W, int(H * i / 3))
            ac = QColor(C["accent"])
            ac.setAlpha(90)
            p.setPen(QPen(ac, 1.5))
            p.drawLine(W // 2, 0, W // 2, H)
            p.drawRect(int(W * 0.05), int(H * 0.05), int(W * 0.9), int(H * 0.9))

            # Guías de zona segura TikTok / Reels / Shorts en formato vertical 9:16
            if H > W:
                p.save()
                # Columna derecha de botones (likes, comentarios, compartir)
                right_ui = QRectF(W * 0.83, H * 0.45, W * 0.17, H * 0.37)
                # Franja inferior de descripción / usuario / sonido
                bot_ui = QRectF(0, H * 0.82, W, H * 0.18)
                # Franja superior (Siguiendo / Para ti)
                top_ui = QRectF(0, 0, W, H * 0.09)
                p.setPen(QPen(QColor(229, 72, 77, 110), 2, Qt.DashLine))
                p.setBrush(QColor(229, 72, 77, 22))
                for zr in (top_ui, right_ui, bot_ui):
                    p.drawRect(zr)
                p.setPen(QColor(255, 255, 255, 115))
                p.setFont(QFont("Segoe UI", 16, QFont.DemiBold))
                p.drawText(bot_ui.adjusted(24, 10, -24, -10), Qt.AlignTop | Qt.AlignLeft, "Zona UI TikTok / Shorts / Reels (evitar texto aquí)")
                p.restore()

        # Guías inteligentes al arrastrar
        if self._smart_guides and not self.clean:
            p.setPen(QPen(QColor(C["accent2"]), 2.0))
            for axis, val in self._smart_guides:
                if axis == "v":
                    p.drawLine(QPointF(val, 0), QPointF(val, H))
                else:
                    p.drawLine(QPointF(0, val), QPointF(W, val))
