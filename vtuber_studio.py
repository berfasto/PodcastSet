"""Estudio completo de creación y rigging de VTuber 2D (.vtr):
minimalista, fácil para empezar (rig base editable o arrastrar PNGs) y profundo (jerarquía padre-hijo,
pivotes editables, profundidad 2.5D, péndulo de pelo, seguimiento de pupilas, expresiones y envío directo a Video)."""
import copy
import math
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

import icons
import model as M
import vtuber_rig as R
from audio_engine import AudioChannel, list_input_devices
from face_tracker import FaceTracker, list_camera_devices
from theme import C
from voice_emotion import EMOTION_META, EMOTIONS
from widgets import CollapsibleSection, ColorWheelDialog, ElidedLabel, LevelMeter, LSlider, Segmented, enable_numeric_drag


class RigOutputWindow(QWidget):
    """Ventana limpia independiente para capturar el VTuber 2D directamente en OBS (F11 pantalla completa)."""

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.setWindowTitle(f"OBS — {studio.rig.get('name', 'VTuber 2D')} (F11: Pantalla completa)")
        self.resize(900, 900)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        bg = self.studio.rig.get("bg_type", "transparent")
        col = "#00b140" if bg == "chroma" else (self.studio.rig.get("bg_color", "#121215") if bg == "dark" else "#00b140")
        p.fillRect(self.rect(), QColor(col))
        rw, rh = float(self.studio.rig.get("canvas_w", R.RIG_W)), float(self.studio.rig.get("canvas_h", R.RIG_H))
        sc = min(self.width() / rw, self.height() / rh)
        p.translate((self.width() - rw * sc) * 0.5, (self.height() - rh * sc) * 0.5)
        p.scale(sc, sc)
        R.render_rig(p, self.studio.rig, self.studio.state, time.time(), live_mode=True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        super().keyPressEvent(e)


class RigCanvasWidget(QWidget):
    """Lienzo interactivo 60 FPS para rigging 2.5D (selección, mover, rotar, escalar y pivote) y prueba en vivo."""
    layerSelected = Signal(str)
    layerTransformed = Signal()
    filesDropped = Signal(list)

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.live_mode = False
        self.show_bones = True
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.mouse_norm = (0.0, 0.0)
        self._drag_mode = None      # 'pan' | 'move' | 'pivot' | 'rot' | 'scale'
        self._drag_start = None
        self._drag_orig = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def fit_view(self):
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.update()

    def _canvas_transform(self):
        rw = float(self.studio.rig.get("canvas_w", R.RIG_W))
        rh = float(self.studio.rig.get("canvas_h", R.RIG_H))
        base_sc = min((self.width() - 40) / rw, (self.height() - 40) / rh)
        sc = max(0.1, base_sc * self.zoom)
        ox = (self.width() - rw * sc) * 0.5 + self.pan.x()
        oy = (self.height() - rh * sc) * 0.5 + self.pan.y()
        tf = QTransform()
        tf.translate(ox, oy)
        tf.scale(sc, sc)
        return tf, sc

    def _to_rig(self, pos):
        tf, _ = self._canvas_transform()
        inv, ok = tf.inverted()
        return inv.map(pos) if ok else pos

    def _selected_layer(self):
        lid = self.studio.sel_layer
        return next((l for l in self.studio.rig.get("layers", []) if l["id"] == lid), None)

    def _sel_handles(self, lyr):
        tf = self.studio.state.world_transforms.get(lyr["id"])
        if tf is None:
            return None
        w, h = float(lyr["w"]), float(lyr["h"])
        px, py = float(lyr["pivot_x"]) * w, float(lyr["pivot_y"]) * h
        rect = QRectF(-px, -py, w, h)
        piv = tf.map(QPointF(0.0, 0.0))
        corners = [tf.map(rect.topLeft()), tf.map(rect.topRight()), tf.map(rect.bottomLeft()), tf.map(rect.bottomRight())]
        rot_pt = tf.map(QPointF(rect.center().x(), rect.bottom() + 34.0))
        return piv, corners, rot_pt, rect, tf

    def _hit_layer(self, rig_pt):
        # Buscar de mayor Z a menor Z
        for lyr in sorted(self.studio.rig.get("layers", []), key=lambda x: float(x.get("z", 0)), reverse=True):
            if lyr.get("locked") or not lyr.get("visible", True):
                continue
            tf = self.studio.state.world_transforms.get(lyr["id"])
            if tf is None:
                continue
            inv, ok = tf.inverted()
            if not ok:
                continue
            lp = inv.map(rig_pt)
            w, h = float(lyr["w"]), float(lyr["h"])
            px, py = float(lyr["pivot_x"]) * w, float(lyr["pivot_y"]) * h
            if QRectF(-px, -py, w, h).contains(lp):
                return lyr
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(C["canvas_bg"]))

        tf, sc = self._canvas_transform()
        rw = float(self.studio.rig.get("canvas_w", R.RIG_W))
        rh = float(self.studio.rig.get("canvas_h", R.RIG_H))

        p.save()
        p.setTransform(tf)

        # Fondo del área del rig
        bg_type = self.studio.rig.get("bg_type", "transparent")
        if bg_type == "chroma":
            p.fillRect(QRectF(0, 0, rw, rh), QColor("#00b140"))
        elif bg_type == "dark":
            p.fillRect(QRectF(0, 0, rw, rh), QColor(self.studio.rig.get("bg_color", "#141418")))
        else:
            tile = 36
            p.fillRect(QRectF(0, 0, rw, rh), QColor("#18181C"))
            c2 = QColor("#212127")
            for y in range(0, int(rh), tile):
                for x in range(0, int(rw), tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        p.fillRect(x, y, tile, tile, c2)

        # Eje central de simetría en modo Armar/Rig
        if not self.live_mode:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1.5 / sc, Qt.DashLine))
            p.drawLine(QPointF(rw * 0.5, 0), QPointF(rw * 0.5, rh))
            p.drawLine(QPointF(0, rh * 0.5), QPointF(rw, rh * 0.5))

        # Renderizado del Rig VTuber 2.5D
        R.render_rig(
            p, self.studio.rig, self.studio.state, time.time(),
            live_mode=self.live_mode,
            show_debug_layer=self.studio.sel_layer,
            show_bones=self.show_bones,
        )

        # Estado vacío limpio
        if not self.studio.rig.get("layers"):
            p.setPen(QColor(255, 255, 255, 130))
            p.setFont(QFont("Segoe UI", 28, QFont.DemiBold))
            p.drawText(QRectF(0, rh * 0.41, rw, 56), Qt.AlignCenter, "Estudio de creación VTuber 2D")
            p.setPen(QColor(255, 255, 255, 72))
            p.setFont(QFont("Segoe UI", 17))
            p.drawText(QRectF(40, rh * 0.48, rw - 80, 60), Qt.AlignCenter,
                       "Arrastra tus capas PNG aquí, pulsa «Importar PNGs» o carga la «Base editable»")

        # Borde del lienzo
        p.setPen(QPen(QColor("#33333C"), 2.0 / sc))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, 0, rw, rh))

        # Controles de transformación + pivote editable en modo Rigging
        if not self.live_mode:
            lyr = self._selected_layer()
            if lyr is not None:
                hd = self._sel_handles(lyr)
                if hd is not None:
                    piv, corners, rot_pt, rect, ltf = hd
                    inv_s = 1.0 / max(0.2, sc)
                    p.save()
                    p.setTransform(ltf, True)
                    p.setPen(QPen(QColor(C["accent"]), 1.8 * inv_s))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(rect)
                    p.restore()

                    # Tiradores de esquina (escala)
                    hs = 5.5 * inv_s
                    p.setBrush(QColor("#FFFFFF"))
                    p.setPen(QPen(QColor(C["accent"]), 1.6 * inv_s))
                    for pt in corners:
                        p.drawRect(QRectF(pt.x() - hs, pt.y() - hs, hs * 2, hs * 2))

                    # Tirador de rotación
                    bot_mid = ltf.map(QPointF(rect.center().x(), rect.bottom()))
                    p.setPen(QPen(QColor(C["accent"]), 1.5 * inv_s))
                    p.drawLine(bot_mid, rot_pt)
                    p.setBrush(QColor("#16161A"))
                    p.drawEllipse(rot_pt, 8.5 * inv_s, 8.5 * inv_s)

                    # Cruz de Pivote editable (ámbar) — arrastrar para mover el eje de giro/péndulo
                    pr = 9.0 * inv_s
                    p.setPen(QPen(QColor("#101012"), 3.2 * inv_s))
                    p.drawEllipse(piv, pr, pr)
                    p.setPen(QPen(QColor(C["warn"]), 2.0 * inv_s))
                    p.setBrush(QColor(229, 160, 13, 60))
                    p.drawEllipse(piv, pr, pr)
                    p.drawLine(QPointF(piv.x() - pr * 1.4, piv.y()), QPointF(piv.x() + pr * 1.4, piv.y()))
                    p.drawLine(QPointF(piv.x(), piv.y() - pr * 1.4), QPointF(piv.x(), piv.y() + pr * 1.4))

        p.restore()

        # Insignia sutil de modo y emoción en vivo arriba-izquierda
        if self.live_mode:
            tr = self.studio.rig.get("tracking", {})
            cm = tr.get("mode", "voice")
            cm_lbl = {"voice": "SOLO VOZ", "cam_voice": "CÁMARA + VOZ", "camera": "SOLO CÁMARA"}.get(cm, "EN VIVO")
            em_id = self.studio.state.active_emotion
            em_m = EMOTION_META.get(em_id, EMOTION_META["neutral"])
            cam_tag = " · Rostro OK" if (cm != "voice" and self.studio.face_tracker.detected) else ""
            mode_txt = f"{cm_lbl}{cam_tag}  ·  {em_m['label']}"
            b_col = QColor(em_m["color"] if em_id != "neutral" else C["accent2"])
        else:
            mode_txt = "EDITOR RIG · Arrastra capa, esquinas o cruz ámbar de pivote"
            b_col = QColor(C["border_light"])
        p.setFont(QFont("Segoe UI", 8.2, QFont.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(mode_txt) + 22
        badge = QRectF(12, 10, tw, 24)
        p.setPen(QPen(b_col, 1.1))
        p.setBrush(QColor(18, 18, 22, 220))
        p.drawRoundedRect(badge, 6, 6)
        p.setPen(b_col if self.live_mode else QColor(C["text_dim"]))
        p.drawText(badge, Qt.AlignCenter, mode_txt)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom = max(0.25, min(3.5, self.zoom * (1.15 if e.angleDelta().y() > 0 else 1.0 / 1.15)))
            self.update()
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MiddleButton:
            self._drag_mode = "pan"
            self._drag_start = e.position()
            self._drag_orig = QPointF(self.pan)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self.live_mode or e.button() != Qt.LeftButton:
            return

        rig_pt = self._to_rig(e.position())
        _, sc = self._canvas_transform()
        tol = 14.0 / max(0.2, sc)

        # 1. Comprobar tiradores de la capa seleccionada
        lyr = self._selected_layer()
        if lyr is not None and not lyr.get("locked"):
            hd = self._sel_handles(lyr)
            if hd is not None:
                piv, corners, rot_pt, rect, ltf = hd
                if math.hypot(rig_pt.x() - piv.x(), rig_pt.y() - piv.y()) <= tol:
                    self._drag_mode = "pivot"
                    self._drag_start = rig_pt
                    self._drag_orig = (lyr["x"], lyr["y"], lyr["pivot_x"], lyr["pivot_y"], ltf)
                    self.setCursor(Qt.CrossCursor)
                    return
                if math.hypot(rig_pt.x() - rot_pt.x(), rig_pt.y() - rot_pt.y()) <= tol:
                    self._drag_mode = "rot"
                    self._drag_start = rig_pt
                    self._drag_orig = (piv, lyr["rotation"])
                    self.setCursor(Qt.ClosedHandCursor)
                    return
                for pt in corners:
                    if math.hypot(rig_pt.x() - pt.x(), rig_pt.y() - pt.y()) <= tol:
                        d0 = max(12.0, math.hypot(rig_pt.x() - piv.x(), rig_pt.y() - piv.y()))
                        self._drag_mode = "scale"
                        self._drag_orig = (piv, d0, lyr["scale_x"], lyr["scale_y"])
                        self.setCursor(Qt.SizeFDiagCursor)
                        return

        # 2. Seleccionar / arrastrar capa bajo el cursor
        hit = self._hit_layer(rig_pt)
        if hit is not None:
            self.studio.select_layer(hit["id"])
            self.layerSelected.emit(hit["id"])
            if not hit.get("locked"):
                pid = hit.get("parent_id")
                ptf = self.studio.state.world_transforms.get(pid, QTransform()) if pid else QTransform()
                pinv, _ = ptf.inverted()
                self._drag_mode = "move"
                self._drag_start = pinv.map(rig_pt)
                self._drag_orig = (hit["x"], hit["y"], pinv)
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        # Siempre actualizar mouse_norm para seguimiento de mirada/cabeza en vivo
        nx = max(-1.0, min(1.0, (e.position().x() / max(1.0, self.width()) - 0.5) * 2.0))
        ny = max(-1.0, min(1.0, (e.position().y() / max(1.0, self.height()) - 0.5) * 2.0))
        self.mouse_norm = (nx, ny)

        if self._drag_mode == "pan":
            self.pan = self._drag_orig + (e.position() - self._drag_start)
            self.update()
            return

        lyr = self._selected_layer()
        if not self._drag_mode or lyr is None:
            return
        rig_pt = self._to_rig(e.position())

        if self._drag_mode == "move":
            x0, y0, pinv = self._drag_orig
            cur_p = pinv.map(rig_pt)
            lyr["x"] = round(x0 + (cur_p.x() - self._drag_start.x()), 1)
            lyr["y"] = round(y0 + (cur_p.y() - self._drag_start.y()), 1)
            self.layerTransformed.emit()
            self.update()
        elif self._drag_mode == "rot":
            piv, _ = self._drag_orig
            deg = math.degrees(math.atan2(rig_pt.y() - piv.y(), rig_pt.x() - piv.x())) - 90.0
            deg = ((deg + 180.0) % 360.0) - 180.0
            if e.modifiers() & Qt.ShiftModifier:
                deg = round(deg / 15.0) * 15.0
            lyr["rotation"] = round(deg, 1)
            self.layerTransformed.emit()
            self.update()
        elif self._drag_mode == "scale":
            piv, d0, sx0, sy0 = self._drag_orig
            d1 = max(10.0, math.hypot(rig_pt.x() - piv.x(), rig_pt.y() - piv.y()))
            f = max(0.1, min(4.0, d1 / d0))
            lyr["scale_x"] = round(sx0 * f, 3)
            lyr["scale_y"] = round(sy0 * f, 3)
            self.layerTransformed.emit()
            self.update()
        elif self._drag_mode == "pivot":
            # Mueve el pivote manteniendo la imagen visual exactamente en su sitio
            x0, y0, px0, py0, ltf = self._drag_orig
            inv, ok = ltf.inverted()
            if ok:
                lp = inv.map(rig_pt)
                w, h = max(1.0, float(lyr["w"])), max(1.0, float(lyr["h"]))
                npx = max(0.0, min(1.0, px0 + lp.x() / w))
                npy = max(0.0, min(1.0, py0 + lp.y() / h))
                pid = lyr.get("parent_id")
                ptf = self.studio.state.world_transforms.get(pid, QTransform()) if pid else QTransform()
                pinv, _ = ptf.inverted()
                delta_w = ltf.map(QPointF((npx - px0) * w, (npy - py0) * h)) - ltf.map(QPointF(0.0, 0.0))
                p0_loc = pinv.map(QPointF(0, 0))
                p1_loc = pinv.map(delta_w)
                lyr["pivot_x"] = round(npx, 3)
                lyr["pivot_y"] = round(npy, 3)
                lyr["x"] = round(x0 + (p1_loc.x() - p0_loc.x()), 1)
                lyr["y"] = round(y0 + (p1_loc.y() - p0_loc.y()), 1)
                self.layerTransformed.emit()
                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_mode:
            self._drag_mode = None
            self.unsetCursor()
            self.studio.mark_dirty()
            self.studio.load_inspector()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                    e.acceptProposedAction()
                    return

    def dropEvent(self, e):
        paths = [
            u.toLocalFile() for u in e.mimeData().urls()
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
        ]
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()


class VTuberStudioWorkspace(QWidget):
    """Espacio de trabajo independiente para crear, riggear y probar un VTuber 2D (.vtr) en su propia pestaña."""
    titleChanged = Signal(object)
    sendToVideoRequested = Signal(dict, object)   # (rig_copy, rig_path_or_None)
    statusMessage = Signal(str, int)

    def __init__(self, rig_data=None, rig_path=None):
        super().__init__()
        self.rig = rig_data if rig_data is not None else R.new_empty_rig("Nuevo VTuber 2D")
        self.rig_path = Path(rig_path) if rig_path else None
        self.state = R.RigRuntimeState()
        self.sel_layer = self.rig["layers"][0]["id"] if self.rig["layers"] else None
        self.dirty = False
        self._syncing = False
        self.devices = list_input_devices()
        self.mic = AudioChannel()
        self.face_tracker = FaceTracker()
        self.obs_win = None

        self._build_ui()
        self._refresh_tree()
        self._refresh_expr_and_toggles()
        self.load_inspector()
        self._sync_mic()
        # Arrancar directo en el modo intuitivo Fácil / En Vivo
        self._apply_studio_mode(0)

    def tab_title(self):
        mark = " •" if self.dirty else ""
        return f"{self.rig.get('name', 'VTuber 2D')}{mark}"

    def tab_kind(self):
        return "vtuber"

    def is_dirty(self):
        return self.dirty

    def mark_dirty(self):
        if not self._syncing:
            self.dirty = True
            self.titleChanged.emit(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barra superior intuitiva: Modo Fácil/Rig + Control (Solo Voz / Cámara+Voz / Solo Cámara)
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(42)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)

        self.mode_seg = Segmented("Fácil / En Vivo", "Editor Rig")
        self.mode_seg.changed.connect(self._apply_studio_mode)
        h.addWidget(self.mode_seg)

        sep1 = QFrame()
        sep1.setFixedSize(1, 20)
        sep1.setStyleSheet(f"background:{C['border']};")
        h.addWidget(sep1)

        self.ctrl_seg = Segmented("Solo Voz", "Cámara + Voz", "Solo Cámara")
        self.ctrl_seg.changed.connect(self._on_ctrl_mode_changed)
        h.addWidget(self.ctrl_seg)

        self.b_cam_bar = QPushButton(" Cámara")
        self.b_cam_bar.setObjectName("flat")
        self.b_cam_bar.setIcon(icons.icon("camera", C["text_dim"], 14))
        self.b_cam_bar.setCheckable(True)
        self.b_cam_bar.setFixedHeight(27)
        self.b_cam_bar.setToolTip("Encender o apagar seguimiento por cámara web")
        self.b_cam_bar.toggled.connect(self._toggle_rig_webcam)
        h.addWidget(self.b_cam_bar)

        self.b_bones = QPushButton(" Huesos")
        self.b_bones.setObjectName("flat")
        self.b_bones.setIcon(icons.icon("rig", C["text_dim"], 14))
        self.b_bones.setCheckable(True)
        self.b_bones.setChecked(True)
        self.b_bones.setFixedHeight(27)
        self.b_bones.toggled.connect(self._toggle_bones)
        h.addWidget(self.b_bones)

        self.bg_cb = QComboBox()
        self.bg_cb.setFixedHeight(27)
        for k, lbl in (("transparent", "Fondo Alfa"), ("dark", "Fondo Oscuro"), ("chroma", "Croma Verde")):
            self.bg_cb.addItem(lbl, k)
        self.bg_cb.setCurrentIndex(max(0, self.bg_cb.findData(self.rig.get("bg_type", "transparent"))))
        self.bg_cb.currentIndexChanged.connect(lambda: (self.rig.__setitem__("bg_type", self.bg_cb.currentData()), self.canvas.update()))
        h.addWidget(self.bg_cb)

        h.addStretch(1)

        b_imp = QPushButton(" + PNGs")
        b_imp.setIcon(icons.icon("image", C["text_dim"], 14))
        b_imp.setFixedHeight(27)
        b_imp.setToolTip("Importar tus propias capas PNG/GIF con auto-rigging")
        b_imp.clicked.connect(self.import_layers_dialog)
        h.addWidget(b_imp)

        b_obs = QPushButton(" OBS")
        b_obs.setObjectName("flat")
        b_obs.setIcon(icons.icon("monitor", C["text_dim"], 14))
        b_obs.setFixedHeight(27)
        b_obs.clicked.connect(self.open_obs_window)
        h.addWidget(b_obs)

        b_bake = QPushButton(" 12 PNGs")
        b_bake.setIcon(icons.icon("download", C["text_dim"], 14))
        b_bake.setFixedHeight(27)
        b_bake.setToolTip("Exportar paquete ordenado de 12 sprites PNG transparentes")
        b_bake.clicked.connect(self.export_12_sprites)
        h.addWidget(b_bake)

        b_send = QPushButton(" Usar en Video")
        b_send.setObjectName("primary")
        b_send.setIcon(icons.icon("send", "white", 13))
        b_send.setFixedHeight(27)
        b_send.setCursor(Qt.PointingHandCursor)
        b_send.setToolTip("Enviar este VTuber 2D vivo directamente a una pestaña de Video 16:9 o TikTok 9:16")
        b_send.clicked.connect(lambda: self.sendToVideoRequested.emit(copy.deepcopy(self.rig), self.rig_path))
        h.addWidget(b_send)
        root.addWidget(bar)

        # Cuerpo principal adaptable (Fácil = Lienzo amplio + Panel Rápido; Editor Rig = Árbol + Lienzo + Inspector Profundo)
        self.main_split = QSplitter(Qt.Horizontal)
        root.addWidget(self.main_split, 1)

        self.left_panel = self._build_left_hierarchy()
        self.main_split.addWidget(self.left_panel)

        # Centro: Lienzo + Barra inferior (Emociones en Vivo o Mesa de Parámetros 2.5D)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self.canvas = RigCanvasWidget(self)
        self.canvas.layerSelected.connect(self._on_canvas_layer_selected)
        self.canvas.layerTransformed.connect(self._sync_transform_numbers_only)
        self.canvas.filesDropped.connect(self.import_layer_paths)
        cv.addWidget(self.canvas, 1)

        self.bottom_stack = QStackedWidget()
        self.bottom_stack.setFixedHeight(86)
        self.bottom_stack.addWidget(self._build_emotion_bar())
        self.bottom_stack.addWidget(self._build_live_param_deck())
        cv.addWidget(self.bottom_stack)
        self.main_split.addWidget(center)

        self.right_stack = QStackedWidget()
        self.right_stack.setMinimumWidth(356)
        self.right_stack.setMaximumWidth(420)
        self.right_stack.addWidget(self._build_easy_panel())
        self.right_stack.addWidget(self._build_right_inspector())
        self.main_split.addWidget(self.right_stack)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([252, 840, 366])
        self._top_bar = bar
        self._detached_docks = False

    def detach_studio_panels(self):
        """Extrae los paneles para integrarlos en el ecosistema de docks de MainWindow."""
        self._detached_docks = True
        self.left_panel.setMinimumWidth(160)
        self.left_panel.setMaximumWidth(16777215)
        self.right_stack.setMinimumWidth(200)
        self.right_stack.setMaximumWidth(16777215)
        for idx in range(self.right_stack.count()):
            self.right_stack.widget(idx).setMaximumWidth(16777215)
        self.bottom_stack.setMinimumHeight(86)
        self.bottom_stack.setMaximumHeight(16777215)

        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        sv.addWidget(self._top_bar)
        sv.addWidget(self.canvas, 1)
        return self.left_panel, stage, self.right_stack, self.bottom_stack

    def _build_left_hierarchy(self):
        w = QWidget()
        w.setObjectName("panel")
        w.setMinimumWidth(230)
        w.setMaximumWidth(310)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        l1 = QLabel("JERARQUÍA DE CAPAS")
        l1.setObjectName("section")
        hdr.addWidget(l1)
        hdr.addStretch()
        b_add = QPushButton(" + PNG")
        b_add.setObjectName("primary")
        b_add.setFixedHeight(24)
        b_add.clicked.connect(self.import_layers_dialog)
        hdr.addWidget(b_add)
        v.addLayout(hdr)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background:{C['bg']}; border:1px solid {C['border']}; border-radius:6px; padding:3px; }}"
            f"QTreeWidget::item {{ padding:4px 4px; border-radius:4px; }}"
            f"QTreeWidget::item:selected {{ background:{C['accent_soft']}; color:{C['text']}; }}"
        )
        self.tree.currentItemChanged.connect(self._on_tree_sel)
        self.tree.itemChanged.connect(self._on_tree_check)
        v.addWidget(self.tree, 1)

        # Acciones rápidas de capa
        act_r = QHBoxLayout()
        act_r.setSpacing(4)
        for ic, tip, cb in (
            ("chevron_up", "Subir en orden Z", lambda: self._nudge_z(5)),
            ("chevron_down", "Bajar en orden Z", lambda: self._nudge_z(-5)),
            ("copy", "Duplicar capa", self.duplicate_layer),
            ("trash", "Eliminar capa", self.delete_layer),
        ):
            b = QPushButton()
            b.setObjectName("flat")
            b.setIcon(icons.icon(ic, C["danger"] if ic == "trash" else C["text_dim"], 13))
            b.setFixedSize(28, 26)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            act_r.addWidget(b)
        act_r.addStretch()
        v.addLayout(act_r)

        # Expresiones rápidas
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{C['border']};")
        v.addWidget(sep)

        eh = QHBoxLayout()
        el = QLabel("EXPRESIONES")
        el.setObjectName("section")
        eh.addWidget(el)
        eh.addStretch()
        b_ae = QPushButton()
        b_ae.setObjectName("flat")
        b_ae.setIcon(icons.icon("plus", C["text_dim"], 13))
        b_ae.setFixedSize(24, 22)
        b_ae.setToolTip("Nueva expresión")
        b_ae.clicked.connect(self.add_expression)
        eh.addWidget(b_ae)
        v.addLayout(eh)

        self.expr_wrap = QGridLayout()
        self.expr_wrap.setContentsMargins(0, 0, 0, 0)
        self.expr_wrap.setSpacing(4)
        v.addLayout(self.expr_wrap)

        tl = QLabel("ACCESORIOS TOGGLE (1..9)")
        tl.setObjectName("section")
        v.addWidget(tl)
        self.toggle_wrap = QGridLayout()
        self.toggle_wrap.setContentsMargins(0, 0, 0, 0)
        self.toggle_wrap.setSpacing(4)
        v.addLayout(self.toggle_wrap)
        return w

    def _build_emotion_bar(self):
        """Barra inferior limpia de emociones en vivo + selector 1-clic (Fácil / En Vivo)."""
        bar = QFrame()
        bar.setObjectName("footer")
        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.em_dot = QLabel("●")
        self.em_dot.setStyleSheet(f"color:{C['muted']}; font-size:10pt;")
        self.em_live_lbl = QLabel("Emoción: Normal")
        self.em_live_lbl.setStyleSheet("font-weight:600; font-size:8.6pt;")
        self.em_stat_lbl = QLabel("Voz en espera")
        self.em_stat_lbl.setProperty("muted", True)
        self.em_stat_lbl.setStyleSheet("font-size:7.8pt;")
        self.easy_meter = LevelMeter(height=9)
        self.easy_meter.setFixedWidth(130)

        self.cb_auto_em = QCheckBox("Auto Emoción")
        self.cb_auto_em.setChecked(bool(self.rig.get("tracking", {}).get("auto_emotion", True)))
        self.cb_auto_em.toggled.connect(lambda on: self._tset("auto_emotion", on))
        self.cb_auto_react = QCheckBox("Reacciones")
        self.cb_auto_react.setChecked(bool(self.rig.get("tracking", {}).get("auto_reactions", True)))
        self.cb_auto_react.toggled.connect(lambda on: self._tset("auto_reactions", on))

        top.addWidget(self.em_dot)
        top.addWidget(self.em_live_lbl)
        top.addWidget(self.em_stat_lbl)
        top.addWidget(self.easy_meter)
        top.addStretch()
        top.addWidget(self.cb_auto_em)
        top.addWidget(self.cb_auto_react)
        v.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(5)
        self.em_grp = QButtonGroup(self)
        self.em_btns = {}
        choices = [("auto", "Auto (Voz)", C["accent"])] + [(eid, lbl, col) for eid, lbl, col, _ in EMOTIONS]
        cur_man = self.rig.get("tracking", {}).get("manual_emotion", "auto")
        for eid, lbl, col in choices:
            b = QPushButton(lbl)
            b.setObjectName("expr_pill")
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setChecked(eid == cur_man)
            b.clicked.connect(lambda _, k=eid: self._set_manual_emotion(k))
            self.em_grp.addButton(b)
            self.em_btns[eid] = b
            row.addWidget(b, 1)
        v.addLayout(row)
        return bar

    def _build_easy_panel(self):
        """Panel derecho minimalista e intuitivo (Fácil / En Vivo): Voz/Cámara, Emociones, Colores y Accesorios."""
        w = QWidget()
        w.setObjectName("panel")
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        t = QLabel("Control Rápido VTuber 2D")
        t.setStyleSheet("font-size:10.5pt; font-weight:700;")
        hdr.addWidget(t)
        hdr.addStretch()
        b_adv = QPushButton("Avanzado")
        b_adv.setObjectName("flat")
        b_adv.setFixedHeight(24)
        b_adv.setToolTip("Abrir Editor Rig completo (capas, huesos y pivotes)")
        b_adv.clicked.connect(lambda: (self.mode_seg.set_index(1), self._apply_studio_mode(1)))
        hdr.addWidget(b_adv)
        v.addLayout(hdr)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 14, 10)
        lay.setSpacing(8)

        # 1. Voz y Cámara
        s1 = CollapsibleSection("Voz, cámara y emociones", True)
        self.easy_mode_hint = QLabel("Solo Voz: tu voz mueve la cabeza, boca y emociones automáticamente sin cámara.")
        self.easy_mode_hint.setProperty("muted", True)
        self.easy_mode_hint.setWordWrap(True)
        self.easy_mode_hint.setStyleSheet("font-size:7.8pt;")
        s1.content.addWidget(self.easy_mode_hint)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Micrófono"))
        self.easy_mic_cb = QComboBox()
        self.easy_mic_cb.addItem("Sin micrófono", None)
        for name, _ in self.devices:
            self.easy_mic_cb.addItem(name, name)
        self.easy_mic_cb.currentIndexChanged.connect(self._on_easy_mic_changed)
        mr.addWidget(self.easy_mic_cb, 1)
        s1.content.addLayout(mr)

        self.easy_sens = LSlider("Sensibilidad emociones", 25, 100, "%", default=68)
        self.easy_sens.changed.connect(self._on_emotion_sens_changed)
        s1.content.addWidget(self.easy_sens)

        self.easy_thr = LSlider("Umbral de voz (boca)", 5, 85, "%", default=36)
        self.easy_thr.changed.connect(lambda val: self._aset("threshold", val))
        s1.content.addWidget(self.easy_thr)

        rb = QHBoxLayout()
        rb.setSpacing(6)
        b_cal_v = QPushButton(" Recalibrar voz")
        b_cal_v.setObjectName("flat")
        b_cal_v.setIcon(icons.icon("refresh", C["text_dim"], 12))
        b_cal_v.clicked.connect(self._recalib_voice)
        self.b_cam_cal = QPushButton(" Calibrar cámara")
        self.b_cam_cal.setObjectName("flat")
        self.b_cam_cal.setIcon(icons.icon("camera", C["text_dim"], 12))
        self.b_cam_cal.clicked.connect(self._recalib_camera)
        rb.addWidget(b_cal_v, 1)
        rb.addWidget(self.b_cam_cal, 1)
        s1.content.addLayout(rb)

        # Vista previa compacta de la cámara cuando está encendida
        self.cam_preview = QLabel("Cámara apagada (activa «Cámara + Voz» o «Solo Cámara»)")
        self.cam_preview.setAlignment(Qt.AlignCenter)
        self.cam_preview.setFixedHeight(92)
        self.cam_preview.setWordWrap(True)
        self.cam_preview.setStyleSheet(
            f"background:{C['bg']}; border:1px solid {C['border']}; border-radius:6px; "
            f"color:{C['muted']}; font-size:7.6pt; padding:6px;"
        )
        s1.content.addWidget(self.cam_preview)
        lay.addWidget(s1)

        # 2. Personalizar estilo en 1 clic
        s2 = CollapsibleSection("Personalizar estilo", True)
        pg = QGridLayout()
        pg.setSpacing(5)
        for i, (pid, meta) in enumerate(R.RIG_PALETTES.items()):
            b = QPushButton(meta["label"])
            b.setObjectName("choicePill")
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, k=pid: self._apply_quick_palette(k))
            pg.addWidget(b, i // 2, i % 2)
        s2.content.addLayout(pg)

        cg = QGridLayout()
        cg.setSpacing(5)
        for i, (grp, lbl, init_c) in enumerate((
            ("hair", "Color Pelo", "#3B4A6B"),
            ("eyes", "Color Ojos", "#2F7CF6"),
            ("outfit", "Color Ropa", "#242730"),
            ("skin", "Tono Piel", "#FFE2D2"),
        )):
            b = QPushButton(lbl)
            b.setIcon(icons.icon("palette", C["text_dim"], 12))
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, g=grp, ic=init_c, nm=lbl: self._pick_group_color(g, ic, nm))
            cg.addWidget(b, i // 2, i % 2)
        s2.content.addLayout(cg)

        ra = QHBoxLayout()
        ra.setSpacing(6)
        b_i = QPushButton(" Importar PNGs")
        b_i.setIcon(icons.icon("image", C["text_dim"], 13))
        b_i.clicked.connect(self.import_layers_dialog)
        b_b = QPushButton(" Base 2.5D")
        b_b.setIcon(icons.icon("sparkle", C["text_dim"], 13))
        b_b.clicked.connect(self.load_starter_rig)
        ra.addWidget(b_i, 1)
        ra.addWidget(b_b, 1)
        s2.content.addLayout(ra)
        lay.addWidget(s2)

        # 3. Accesorios rápidos (teclas 1..9)
        s3 = CollapsibleSection("Accesorios rápidos (teclas 1..9)", True)
        self.easy_toggle_grid = QGridLayout()
        self.easy_toggle_grid.setSpacing(5)
        s3.content.addLayout(self.easy_toggle_grid)
        lay.addWidget(s3)

        lay.addStretch()
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        return w

    def _build_live_param_deck(self):
        deck = QFrame()
        deck.setObjectName("footer")
        v = QVBoxLayout(deck)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        lbl = QLabel("PARÁMETROS DE PRUEBA 2.5D")
        lbl.setObjectName("section")
        top.addWidget(lbl)
        self.deck_meter = LevelMeter(height=9)
        self.deck_meter.setFixedWidth(120)
        top.addWidget(self.deck_meter)
        top.addStretch()

        self.cb_test_mouth = QComboBox()
        self.cb_test_mouth.setFixedHeight(24)
        for lbl_m, val in (("Boca: Auto (Mic)", -1), ("Cerrada", 0), ("Media", 1), ("Abierta", 2), ("Gritando", 3)):
            self.cb_test_mouth.addItem(lbl_m, val)
        self.cb_test_mouth.currentIndexChanged.connect(lambda: setattr(self.state, "manual_mouth", self.cb_test_mouth.currentData()))

        self.cb_test_eye = QComboBox()
        self.cb_test_eye.setFixedHeight(24)
        for lbl_e, val in (("Ojos: Auto", -1), ("Abiertos", 0), ("Medios", 1), ("Cerrados", 2)):
            self.cb_test_eye.addItem(lbl_e, val)
        self.cb_test_eye.currentIndexChanged.connect(lambda: setattr(self.state, "manual_blink", self.cb_test_eye.currentData()))

        b_rst = QPushButton("Reset pose")
        b_rst.setObjectName("flat")
        b_rst.setFixedHeight(24)
        b_rst.clicked.connect(self._reset_test_pose)
        b_fit = QPushButton()
        b_fit.setObjectName("flat")
        b_fit.setIcon(icons.icon("fit", C["text_dim"], 13))
        b_fit.setFixedSize(26, 24)
        b_fit.setToolTip("Ajustar zoom")
        b_fit.clicked.connect(self.canvas.fit_view)
        for w in (self.cb_test_mouth, self.cb_test_eye, b_rst, b_fit):
            top.addWidget(w)
        v.addLayout(top)

        sl_row = QHBoxLayout()
        sl_row.setSpacing(12)
        self.sl_hx = LSlider("Cabeza X", -100, 100, "%", default=0)
        self.sl_hy = LSlider("Cabeza Y", -100, 100, "%", default=0)
        self.sl_hz = LSlider("Inclinación Z", -100, 100, "%", default=0)
        self.sl_wind = LSlider("Viento", 0, 100, "%", default=25)
        self.sl_hx.set(0)
        self.sl_hy.set(0)
        self.sl_hz.set(0)
        self.sl_wind.set(int(self.rig["physics_global"].get("wind_strength", 25)))
        self.sl_hx.changed.connect(lambda val: setattr(self.state, "manual_head_x", val / 100.0))
        self.sl_hy.changed.connect(lambda val: setattr(self.state, "manual_head_y", val / 100.0))
        self.sl_hz.changed.connect(lambda val: setattr(self.state, "manual_head_z", val / 100.0))
        self.sl_wind.changed.connect(lambda val: (self.rig["physics_global"].__setitem__("wind_strength", val), self.mark_dirty()))
        for s in (self.sl_hx, self.sl_hy, self.sl_hz, self.sl_wind):
            sl_row.addWidget(s, 1)
        v.addLayout(sl_row)
        return deck

    def _build_right_inspector(self):
        w = QWidget()
        w.setObjectName("panel")
        w.setMinimumWidth(340)
        w.setMaximumWidth(420)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        self.insp_title = ElidedLabel("Sin capa")
        self.insp_title.setObjectName("h1")
        hdr.addWidget(self.insp_title, 1)
        v.addLayout(hdr)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        v.addWidget(self.tabs, 1)

        for name, fn in (("Capa", self._tab_layer), ("Física", self._tab_physics),
                         ("Color", self._tab_color), ("Voz / Expr", self._tab_voice_expr)):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(2, 6, 4, 8)
            lay.setSpacing(6)
            fn(lay)
            lay.addStretch()
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sc.setWidget(page)
            self.tabs.addTab(sc, name)
        return w

    def _tab_layer(self, lay):
        s1 = CollapsibleSection("Identidad y jerarquía", True)
        nr = QHBoxLayout()
        nr.addWidget(QLabel("Nombre"))
        self.l_name = QLineEdit()
        self.l_name.editingFinished.connect(self._rename_layer_inline)
        nr.addWidget(self.l_name, 1)
        s1.content.addLayout(nr)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Padre"))
        self.l_parent = QComboBox()
        self.l_parent.currentIndexChanged.connect(self._on_parent_changed)
        pr.addWidget(self.l_parent, 1)
        s1.content.addLayout(pr)

        rr = QHBoxLayout()
        rr.addWidget(QLabel("Rol"))
        self.l_role = QComboBox()
        for k, lbl in R.LAYER_ROLES:
            self.l_role.addItem(lbl, k)
        self.l_role.currentIndexChanged.connect(lambda: (self._lset("role", self.l_role.currentData()), self._refresh_expr_and_toggles()))
        rr.addWidget(self.l_role, 1)
        s1.content.addLayout(rr)

        kr = QHBoxLayout()
        kr.addWidget(QLabel("Tecla Toggle"))
        self.l_hotkey = QComboBox()
        self.l_hotkey.addItem("Ninguna", "")
        for d in "123456789":
            self.l_hotkey.addItem(f"Tecla {d}", d)
        self.l_hotkey.currentIndexChanged.connect(lambda: (self._lset("hotkey", self.l_hotkey.currentData()), self._refresh_expr_and_toggles()))
        kr.addWidget(self.l_hotkey, 1)
        b_rep = QPushButton("Cambiar PNG...")
        b_rep.clicked.connect(self.replace_layer_image)
        kr.addWidget(b_rep)
        s1.content.addLayout(kr)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Profundidad 2.5D y mirada", True)
        self.l_depth = LSlider("Profundidad 2.5D (paralaje)", -100, 100, "", default=0,
                               tip="Positivo = se mueve más al girar (flequillo/ojos), Negativo = fondo (pelo trasero)")
        self.l_depth.changed.connect(lambda val: self._lset("depth_z", float(val)))
        self.l_gaze = LSlider("Rango mirada pupilas", 0, 40, " px", default=14)
        self.l_gaze.changed.connect(lambda val: self._lset("gaze_range", float(val)))
        s2.content.addWidget(self.l_depth)
        s2.content.addWidget(self.l_gaze)
        lay.addWidget(s2)

        s3 = CollapsibleSection("Transformación y pivote", True)
        xy = QHBoxLayout()
        self.sp_lx, self.sp_ly = QSpinBox(), QSpinBox()
        for lbl, sp, k in (("X", self.sp_lx, "x"), ("Y", self.sp_ly, "y")):
            sp.setRange(-2000, 2000)
            sp.valueChanged.connect(lambda val, key=k: self._lset(key, float(val)))
            lw = QLabel(lbl)
            enable_numeric_drag(sp, lw)
            xy.addWidget(lw)
            xy.addWidget(sp)
        s3.content.addLayout(xy)

        self.l_px = LSlider("Pivote X", 0, 100, "%", default=50)
        self.l_py = LSlider("Pivote Y", 0, 100, "%", default=50)
        self.l_sc = LSlider("Escala uniforme", 10, 300, "%", default=100)
        self.l_rot = LSlider("Rotación", -180, 180, "°", default=0)
        self.l_z = LSlider("Orden Z", -100, 200, "", default=30)
        self.l_op = LSlider("Opacidad", 0, 100, "%", default=100)
        self.l_px.changed.connect(lambda val: self._lset("pivot_x", val / 100.0))
        self.l_py.changed.connect(lambda val: self._lset("pivot_y", val / 100.0))
        self.l_sc.changed.connect(lambda val: (self._lset("scale_x", val / 100.0), self._lset("scale_y", val / 100.0)))
        self.l_rot.changed.connect(lambda val: self._lset("rotation", float(val)))
        self.l_z.changed.connect(lambda val: self._lset("z", val))
        self.l_op.changed.connect(lambda val: self._lset("opacity", val))
        for s in (self.l_px, self.l_py, self.l_sc, self.l_rot, self.l_z, self.l_op):
            s3.content.addWidget(s)

        br = QHBoxLayout()
        self.l_flip = QCheckBox("Voltear H")
        self.l_flip.toggled.connect(lambda val: self._lset("flip_x", val))
        self.l_lock = QCheckBox("Bloquear")
        self.l_lock.toggled.connect(lambda val: self._lset("locked", val))
        self.l_blend = QComboBox()
        for k, lbl, _ in R.BLEND_MODES:
            self.l_blend.addItem(lbl, k)
        self.l_blend.currentIndexChanged.connect(lambda: self._lset("blend_mode", self.l_blend.currentData()))
        br.addWidget(self.l_flip)
        br.addWidget(self.l_lock)
        br.addWidget(self.l_blend, 1)
        s3.content.addLayout(br)
        lay.addWidget(s3)

    def _tab_physics(self, lay):
        s1 = CollapsibleSection("Física de la capa seleccionada", True)
        self.p_en = QCheckBox("Activar física secundaria en esta capa")
        self.p_en.toggled.connect(lambda val: self._pset("enabled", val))
        s1.content.addWidget(self.p_en)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Modo"))
        self.p_mode = QComboBox()
        for k, lbl in R.PHYSICS_MODES:
            self.p_mode.addItem(lbl, k)
        self.p_mode.currentIndexChanged.connect(lambda: self._pset("mode", self.p_mode.currentData()))
        mr.addWidget(self.p_mode, 1)
        s1.content.addLayout(mr)

        self.p_sl = {}
        for k, lbl, lo, hi, suf, dft in (
            ("swing_deg", "Oscilación angular", 0, 40, "°", 12),
            ("stiffness", "Rigidez", 4, 35, "", 18),
            ("damping", "Amortiguación", 20, 95, "%", 65),
            ("inertia_x", "Inercia horizontal", 0, 100, "%", 35),
            ("inertia_y", "Inercia vertical", 0, 100, "%", 25),
            ("wind_amt", "Sensibilidad al viento", 0, 100, "%", 20),
            ("breathe_amt", "Amplitud respiración", 0, 100, "%", 15),
        ):
            sl = LSlider(lbl, lo, hi, suf, default=dft)
            sl.changed.connect(lambda val, key=k: self._pset(key, val))
            self.p_sl[k] = sl
            s1.content.addWidget(sl)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Física global y tracking del avatar", True)
        self.g_mouse = QCheckBox("Seguir cursor (mirada + cabeza 2.5D)")
        self.g_mouse.toggled.connect(lambda val: self._gset("mouse_track", val))
        self.g_face = QCheckBox("Seguimiento facial por Webcam (OpenCV)")
        self.g_face.toggled.connect(self._toggle_rig_webcam)
        self.g_blink = QCheckBox("Parpadeo automático natural")
        self.g_blink.toggled.connect(lambda val: self._gset("blink_auto", val))
        for w in (self.g_mouse, self.g_face, self.g_blink):
            s2.content.addWidget(w)

        self.g_hx = LSlider("Rango giro cabeza X", 0, 100, " px", default=42)
        self.g_hy = LSlider("Rango giro cabeza Y", 0, 80, " px", default=28)
        self.g_hr = LSlider("Inclinación cabeza Z", 0, 30, "°", default=11)
        self.g_bt = LSlider("Rebote al hablar", 0, 50, " px", default=18)
        self.g_hx.changed.connect(lambda val: self._gset("head_x_px", float(val)))
        self.g_hy.changed.connect(lambda val: self._gset("head_y_px", float(val)))
        self.g_hr.changed.connect(lambda val: self._gset("head_rot_deg", float(val)))
        self.g_bt.changed.connect(lambda val: self._gset("bounce_talk", val))
        for s in (self.g_hx, self.g_hy, self.g_hr, self.g_bt):
            s2.content.addWidget(s)
        lay.addWidget(s2)

    def _tab_color(self, lay):
        s1 = CollapsibleSection("Personalizar color de esta capa", True)
        s1.content.addWidget(
            QLabel("Recolorea pelo, ojos, piel o ropa al instante sin redibujar (mantiene sombras y contornos).")
        )
        s1.content.itemAt(0).widget().setProperty("muted", True)
        s1.content.itemAt(0).widget().setWordWrap(True)

        tr = QHBoxLayout()
        self.b_tint = QPushButton(" Elegir color de tinte...")
        self.b_tint.setIcon(icons.icon("palette", C["text_dim"], 14))
        self.b_tint.clicked.connect(self._pick_layer_tint)
        b_clr = QPushButton("Restablecer")
        b_clr.setObjectName("flat")
        b_clr.clicked.connect(self._reset_layer_color)
        tr.addWidget(self.b_tint, 1)
        tr.addWidget(b_clr)
        s1.content.addLayout(tr)

        self.c_tint_amt = LSlider("Intensidad de tinte", 0, 100, "%", default=0)
        self.c_hue = LSlider("Tono (Hue)", -180, 180, "°", default=0)
        self.c_sat = LSlider("Saturación", -100, 100, "%", default=0)
        self.c_br = LSlider("Brillo", -50, 50, "%", default=0)
        self.c_tint_amt.changed.connect(lambda val: self._cset("tint_amt", val))
        self.c_hue.changed.connect(lambda val: self._cset("hue", val))
        self.c_sat.changed.connect(lambda val: self._cset("sat", val))
        self.c_br.changed.connect(lambda val: self._cset("bright", val))
        for s in (self.c_tint_amt, self.c_hue, self.c_sat, self.c_br):
            s1.content.addWidget(s)
        lay.addWidget(s1)

    def _tab_voice_expr(self, lay):
        s1 = CollapsibleSection("Micrófono en vivo (prueba lip-sync)", True)
        mr = QHBoxLayout()
        self.mic_cb = QComboBox()
        self.mic_cb.addItem("Sin micrófono", None)
        for name, _ in self.devices:
            self.mic_cb.addItem(name, name)
        self.mic_cb.currentIndexChanged.connect(self._on_mic_changed)
        mr.addWidget(self.mic_cb, 1)
        s1.content.addLayout(mr)

        self.mic_meter = LevelMeter(interactive=True, height=16)
        self.mic_meter.thresholdChanged.connect(lambda val: (self.a_thr.set(val), self._aset("threshold", val)))
        s1.content.addWidget(self.mic_meter)

        self.a_thr = LSlider("Umbral vocal", 1, 90, "%", default=36)
        self.a_rng = LSlider("Separación bocas", 3, 40, "%", default=12)
        self.a_gain = LSlider("Sensibilidad", 20, 400, "%", default=100)
        self.a_thr.changed.connect(lambda val: self._aset("threshold", val))
        self.a_rng.changed.connect(lambda val: self._aset("range", val))
        self.a_gain.changed.connect(lambda val: (self._aset("gain", val), setattr(self.mic, "gain", val / 100.0)))
        for s in (self.a_thr, self.a_rng, self.a_gain):
            s1.content.addWidget(s)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Ajuste de capa en expresión actual", True)
        self.exp_info = QLabel("Expresión: Normal")
        self.exp_info.setProperty("muted", True)
        s2.content.addWidget(self.exp_info)
        b_save_ov = QPushButton("Guardar visibilidad de esta capa en la expresión")
        b_save_ov.clicked.connect(self._save_layer_expr_override)
        b_clr_ov = QPushButton("Quitar override en esta expresión")
        b_clr_ov.setObjectName("flat")
        b_clr_ov.clicked.connect(self._clear_layer_expr_override)
        s2.content.addWidget(b_save_ov)
        s2.content.addWidget(b_clr_ov)
        lay.addWidget(s2)

    # =====================================================  Lógica y acciones
    def _cur_layer(self):
        return next((l for l in self.rig.get("layers", []) if l["id"] == self.sel_layer), None)

    def _apply_studio_mode(self, idx):
        """0 = Fácil / En Vivo (limpio e intuitivo) | 1 = Editor Rig (capas, huesos y pivotes)."""
        is_easy = (idx == 0)
        self.canvas.live_mode = is_easy
        if not getattr(self, "_detached_docks", False):
            self.left_panel.setVisible(not is_easy)
        self.b_bones.setVisible(not is_easy)
        self.right_stack.setCurrentIndex(0 if is_easy else 1)
        self.bottom_stack.setCurrentIndex(0 if is_easy else 1)
        self.canvas.update()

    def _set_live_mode(self, idx):
        """Compatibilidad: 0 = Rigging, 1 = En Vivo (invertido respecto al nuevo segmented Fácil/Rig)."""
        self.mode_seg.set_index(0 if idx == 1 else 1)
        self._apply_studio_mode(0 if idx == 1 else 1)

    def _on_ctrl_mode_changed(self, idx):
        modes = ("voice", "cam_voice", "camera")
        m = modes[max(0, min(2, idx))]
        tr = self.rig.setdefault("tracking", {})
        tr["mode"] = m
        hints = {
            "voice": "Solo Voz: tu voz mueve la cabeza, boca y emociones automáticamente sin cámara.",
            "cam_voice": "Cámara + Voz: la webcam rastrea cabeza/ojos y tu voz impulsa boca y emociones.",
            "camera": "Solo Cámara: la webcam rastrea cabeza, inclinación, parpadeo y sonrisa sin micrófono.",
        }
        self.easy_mode_hint.setText(hints[m])
        want_cam = m in ("cam_voice", "camera")
        if want_cam and not self.face_tracker.running:
            self.b_cam_bar.setChecked(True)
        elif not want_cam and self.face_tracker.running:
            self.b_cam_bar.setChecked(False)
        self.mark_dirty()
        self.canvas.update()

    def _tset(self, key, val):
        if not self._syncing:
            self.rig.setdefault("tracking", {})[key] = val
            self.mark_dirty()
            self.canvas.update()

    def _set_manual_emotion(self, eid):
        self.rig.setdefault("tracking", {})["manual_emotion"] = eid
        if eid in self.em_btns:
            self.em_btns[eid].setChecked(True)
        self.mark_dirty()
        self.canvas.update()

    def _on_emotion_sens_changed(self, val):
        self.mic.emotion.sensitivity = val / 100.0
        self._tset("emotion_sensitivity", val)

    def _on_easy_mic_changed(self, _):
        if self._syncing:
            return
        dev = self.easy_mic_cb.currentData()
        self.rig["audio"]["device"] = dev
        self._syncing = True
        self.mic_cb.setCurrentIndex(max(0, self.mic_cb.findData(dev)))
        self._syncing = False
        self._sync_mic()
        self.mark_dirty()

    def _recalib_voice(self):
        self.mic.emotion.reset_calibration()
        self.statusMessage.emit("Línea base vocal reiniciada: habla normal 2 segundos para calibrar.", 3500)

    def _recalib_camera(self):
        if self.face_tracker.running:
            self.face_tracker.recalibrate()
            self.statusMessage.emit("Centro de cámara recalibrado.", 2500)
        else:
            self.ctrl_seg.set_index(1)
            self._on_ctrl_mode_changed(1)

    def _apply_quick_palette(self, pid):
        R.apply_rig_palette(self.rig, pid)
        self.mark_dirty()
        self.canvas.update()
        self.statusMessage.emit(f"Paleta aplicada: {R.RIG_PALETTES[pid]['label']}", 2500)

    def _pick_group_color(self, group, init_hex, title):
        def _live(c):
            R.set_rig_group_color(self.rig, group, c.name(), 84 if group != "skin" else 42)
            self.canvas.update()
        ColorWheelDialog.pick(init_hex, title, self, on_change=_live)
        self.mark_dirty()

    def _toggle_bones(self, on):
        self.canvas.show_bones = on
        self.canvas.update()

    def _reset_test_pose(self):
        self.sl_hx.set(0)
        self.sl_hy.set(0)
        self.sl_hz.set(0)
        self.state.manual_head_x = self.state.manual_head_y = self.state.manual_head_z = 0.0
        self.cb_test_mouth.setCurrentIndex(0)
        self.cb_test_eye.setCurrentIndex(0)

    def load_starter_rig(self):
        if self.rig.get("layers"):
            if QMessageBox.question(self, "Cargar base editable",
                                    "¿Reemplazar las capas actuales por la base editable VTuber 2.5D de 17 capas?") != QMessageBox.Yes:
                return
        name = self.rig.get("name", "Mi VTuber 2D")
        self.rig = R.build_starter_vtuber_rig(name)
        self.sel_layer = self.rig["layers"][0]["id"] if self.rig["layers"] else None
        self.mark_dirty()
        self._refresh_tree()
        self._refresh_expr_and_toggles()
        self.load_inspector()
        self.mode_seg.set_index(0)
        self._apply_studio_mode(0)
        self.statusMessage.emit("Rig base 2.5D cargado (mueve el cursor para ver física de pelo y mirada)", 4500)

    def import_layers_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Importar capas PNG/GIF para tu VTuber 2D", str(M.BASE), M.EXTS)
        if paths:
            self.import_layer_paths(paths)

    def import_layer_paths(self, paths):
        added = R.auto_rig_from_files(self.rig, paths)
        if added:
            self.sel_layer = added[-1]["id"]
            self.mark_dirty()
            self._refresh_tree()
            self._refresh_expr_and_toggles()
            self.load_inspector()
            self.statusMessage.emit(f"{len(added)} capa(s) importadas con auto-rigging 2.5D", 3500)

    def replace_layer_image(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        p, _ = QFileDialog.getOpenFileName(self, f"Imagen para «{lyr['name']}»", str(M.BASE), M.EXTS)
        if p:
            lyr["path"] = M.import_asset(p)
            self.mark_dirty()
            self.canvas.update()

    def duplicate_layer(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        dup = copy.deepcopy(lyr)
        dup["id"] = "lyr_" + str(int(time.time() * 1000))[-6:]
        dup["name"] = lyr["name"] + " (copia)"
        dup["x"] += 24
        self.rig["layers"].append(dup)
        self.sel_layer = dup["id"]
        self.mark_dirty()
        self._refresh_tree()
        self.load_inspector()

    def delete_layer(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        lid = lyr["id"]
        for other in self.rig["layers"]:
            if other.get("parent_id") == lid:
                other["parent_id"] = lyr.get("parent_id")
        self.rig["layers"] = [l for l in self.rig["layers"] if l["id"] != lid]
        self.sel_layer = self.rig["layers"][0]["id"] if self.rig["layers"] else None
        self.mark_dirty()
        self._refresh_tree()
        self._refresh_expr_and_toggles()
        self.load_inspector()

    def _nudge_z(self, delta):
        lyr = self._cur_layer()
        if lyr:
            lyr["z"] = int(lyr.get("z", 0)) + delta
            self.mark_dirty()
            self.load_inspector()
            self.canvas.update()

    def _refresh_tree(self):
        self._syncing = True
        self.tree.blockSignals(True)
        self.tree.clear()
        items = {}
        layers = self.rig.get("layers", [])
        for lyr in layers:
            role_short = "" if lyr.get("role", "always") == "always" else f"  [{lyr['role']}]"
            it = QTreeWidgetItem([f"{lyr['name']}{role_short}"])
            it.setData(0, Qt.UserRole, lyr["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if lyr.get("visible", True) else Qt.Unchecked)
            items[lyr["id"]] = it
        for lyr in layers:
            pid = lyr.get("parent_id")
            if pid and pid in items and pid != lyr["id"]:
                items[pid].addChild(items[lyr["id"]])
            else:
                self.tree.addTopLevelItem(items[lyr["id"]])
        self.tree.expandAll()
        if self.sel_layer in items:
            self.tree.setCurrentItem(items[self.sel_layer])
        self.tree.blockSignals(False)
        self._syncing = False

    def _on_tree_sel(self, cur, _prev):
        if self._syncing or cur is None:
            return
        lid = cur.data(0, Qt.UserRole)
        if lid:
            self.sel_layer = lid
            self.load_inspector()
            self.canvas.update()

    def _on_tree_check(self, item, _col):
        if self._syncing:
            return
        lid = item.data(0, Qt.UserRole)
        for lyr in self.rig.get("layers", []):
            if lyr["id"] == lid:
                lyr["visible"] = (item.checkState(0) == Qt.Checked)
                self.mark_dirty()
                self.canvas.update()
                break

    def _on_canvas_layer_selected(self, lid):
        self.sel_layer = lid
        self._refresh_tree()
        self.load_inspector()

    def select_layer(self, lid):
        self.sel_layer = lid
        self.load_inspector()

    def _refresh_expr_and_toggles(self):
        while self.expr_wrap.count():
            w = self.expr_wrap.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for i, ex in enumerate(self.rig.get("expressions", [])[:6]):
            b = QPushButton(ex["name"])
            b.setObjectName("expr_pill")
            b.setCheckable(True)
            b.setChecked(i == self.rig.get("expr", 0))
            b.clicked.connect(lambda _, idx=i: self._set_expr(idx))
            self.expr_wrap.addWidget(b, i // 2, i % 2)

        while self.toggle_wrap.count():
            w = self.toggle_wrap.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        toggles = self.rig.setdefault("toggles", {})
        tog_layers = [l for l in self.rig.get("layers", []) if l.get("role") == "toggle" and l.get("hotkey")]
        tog_layers.sort(key=lambda l: l.get("hotkey", "9"))
        for idx_t, lyr in enumerate(tog_layers):
            hk = lyr["hotkey"]
            on = toggles.get(hk, lyr.get("visible", False))
            short_nm = lyr["name"].split()[0][:10]
            b = QPushButton(f"{hk} · {short_nm}")
            b.setObjectName("expr_pill")
            b.setCheckable(True)
            b.setChecked(bool(on))
            b.toggled.connect(lambda v, k=hk: (self.rig.setdefault("toggles", {}).__setitem__(k, v), self.canvas.update()))
            self.toggle_wrap.addWidget(b, idx_t // 2, idx_t % 2)
        if not tog_layers:
            hl = QLabel("Asigna rol Toggle + tecla 1..9 a un accesorio")
            hl.setProperty("muted", True)
            hl.setStyleSheet("font-size:7.6pt;")
            self.toggle_wrap.addWidget(hl, 0, 0, 1, 2)

        # Sincronizar también la rejilla del Panel Rápido Fácil
        if hasattr(self, "easy_toggle_grid"):
            while self.easy_toggle_grid.count():
                w = self.easy_toggle_grid.takeAt(0).widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            for idx_t, lyr in enumerate(tog_layers):
                hk = lyr["hotkey"]
                on = toggles.get(hk, lyr.get("visible", False))
                b = QPushButton(f"{hk} · {lyr['name'].split()[0][:10]}")
                b.setObjectName("expr_pill")
                b.setCheckable(True)
                b.setFixedHeight(26)
                b.setChecked(bool(on))
                b.clicked.connect(lambda v, k=hk: (self.rig.setdefault("toggles", {}).__setitem__(k, v), self._refresh_expr_and_toggles(), self.canvas.update()))
                self.easy_toggle_grid.addWidget(b, idx_t // 2, idx_t % 2)
            if not tog_layers:
                hl2 = QLabel("Sin accesorios toggle en este rig.")
                hl2.setProperty("muted", True)
                self.easy_toggle_grid.addWidget(hl2, 0, 0, 1, 2)

    def _set_expr(self, idx):
        if 0 <= idx < len(self.rig.get("expressions", [])):
            self.rig["expr"] = idx
            self._refresh_expr_and_toggles()
            self.load_inspector()
            self.canvas.update()

    def add_expression(self):
        name, ok = QInputDialog.getText(self, "Nueva expresión", "Nombre (Feliz, Triste, Sorprendido...):")
        if ok and name.strip():
            self.rig.setdefault("expressions", []).append(R.new_rig_expression(name.strip()))
            self.rig["expr"] = len(self.rig["expressions"]) - 1
            self.mark_dirty()
            self._refresh_expr_and_toggles()
            self.load_inspector()

    def _save_layer_expr_override(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        ex = self.rig["expressions"][self.rig["expr"]]
        ex.setdefault("overrides", {})[lyr["id"]] = {"visible": bool(lyr.get("visible", True))}
        self.mark_dirty()
        self.statusMessage.emit(f"Guardado estado de «{lyr['name']}» en expresión «{ex['name']}»", 3000)

    def _clear_layer_expr_override(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        ex = self.rig["expressions"][self.rig["expr"]]
        ex.get("overrides", {}).pop(lyr["id"], None)
        self.mark_dirty()
        self.canvas.update()

    def _sync_transform_numbers_only(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        self._syncing = True
        self.sp_lx.setValue(int(round(lyr["x"])))
        self.sp_ly.setValue(int(round(lyr["y"])))
        self.l_px.set(int(round(lyr["pivot_x"] * 100)))
        self.l_py.set(int(round(lyr["pivot_y"] * 100)))
        self.l_sc.set(int(round(lyr["scale_x"] * 100)))
        self.l_rot.set(int(round(lyr["rotation"])))
        self._syncing = False

    def _sync_global_controls(self):
        pg = self.rig["physics_global"]
        self.g_mouse.setChecked(bool(pg.get("mouse_track", True)))
        self.g_face.setChecked(bool(pg.get("face_track", False)))
        self.g_blink.setChecked(bool(pg.get("blink_auto", True)))
        self.g_hx.set(int(round(pg.get("head_x_px", 42))))
        self.g_hy.set(int(round(pg.get("head_y_px", 28))))
        self.g_hr.set(int(round(pg.get("head_rot_deg", 11))))
        self.g_bt.set(int(pg.get("bounce_talk", 18)))

        au = self.rig["audio"]
        self.mic_cb.setCurrentIndex(max(0, self.mic_cb.findData(au.get("device"))))
        if hasattr(self, "easy_mic_cb"):
            self.easy_mic_cb.setCurrentIndex(max(0, self.easy_mic_cb.findData(au.get("device"))))
            self.easy_thr.set(int(au.get("threshold", 36)))
        self.a_thr.set(int(au.get("threshold", 36)))
        self.a_rng.set(int(au.get("range", 12)))
        self.a_gain.set(int(au.get("gain", 100)))
        self.mic_meter.set_params(au["threshold"], au["range"])
        self.deck_meter.set_params(au["threshold"], au["range"])
        if hasattr(self, "easy_meter"):
            self.easy_meter.set_params(au["threshold"], au["range"])
        tr = self.rig.setdefault("tracking", {})
        cm_idx = {"voice": 0, "cam_voice": 1, "camera": 2}.get(tr.get("mode", "voice"), 0)
        self.ctrl_seg.set_index(cm_idx)
        sens_v = int(tr.get("emotion_sensitivity", 68))
        self.mic.emotion.sensitivity = sens_v / 100.0
        if hasattr(self, "easy_sens"):
            self.easy_sens.set(sens_v)
            self.cb_auto_em.setChecked(bool(tr.get("auto_emotion", True)))
            self.cb_auto_react.setChecked(bool(tr.get("auto_reactions", True)))
            man_e = tr.get("manual_emotion", "auto")
            if man_e in self.em_btns:
                self.em_btns[man_e].setChecked(True)
        ex = self.rig["expressions"][self.rig["expr"]]
        self.exp_info.setText(f"Expresión activa: {ex['name']}")

    def load_inspector(self):
        lyr = self._cur_layer()
        self._syncing = True
        self._sync_global_controls()
        self.tabs.setEnabled(lyr is not None)
        if lyr is None:
            self.insp_title.setText("Sin capa seleccionada")
            self._syncing = False
            return

        self.insp_title.setText(lyr["name"])
        self.l_name.setText(lyr["name"])

        self.l_parent.blockSignals(True)
        self.l_parent.clear()
        self.l_parent.addItem("Ninguno (Capa raíz)", None)
        for other in self.rig.get("layers", []):
            if other["id"] != lyr["id"]:
                self.l_parent.addItem(other["name"], other["id"])
        self.l_parent.setCurrentIndex(max(0, self.l_parent.findData(lyr.get("parent_id"))))
        self.l_parent.blockSignals(False)

        self.l_role.setCurrentIndex(max(0, self.l_role.findData(lyr.get("role", "always"))))
        self.l_hotkey.setCurrentIndex(max(0, self.l_hotkey.findData(lyr.get("hotkey", ""))))
        self.l_depth.set(int(round(lyr.get("depth_z", 0.0))))
        self.l_gaze.set(int(round(lyr.get("gaze_range", 14.0))))
        self.sp_lx.setValue(int(round(lyr["x"])))
        self.sp_ly.setValue(int(round(lyr["y"])))
        self.l_px.set(int(round(lyr["pivot_x"] * 100)))
        self.l_py.set(int(round(lyr["pivot_y"] * 100)))
        self.l_sc.set(int(round(lyr["scale_x"] * 100)))
        self.l_rot.set(int(round(lyr["rotation"])))
        self.l_z.set(int(lyr["z"]))
        self.l_op.set(int(lyr["opacity"]))
        self.l_flip.setChecked(bool(lyr.get("flip_x", False)))
        self.l_lock.setChecked(bool(lyr.get("locked", False)))
        self.l_blend.setCurrentIndex(max(0, self.l_blend.findData(lyr.get("blend_mode", "normal"))))

        # Física de capa
        ph = lyr["physics"]
        self.p_en.setChecked(bool(ph.get("enabled", False)))
        self.p_mode.setCurrentIndex(max(0, self.p_mode.findData(ph.get("mode", "pendulum"))))
        for k, sl in self.p_sl.items():
            sl.set(int(ph.get(k, 0)))

        # Color
        col = lyr["color"]
        self.c_tint_amt.set(int(col.get("tint_amt", 0)))
        self.c_hue.set(int(col.get("hue", 0)))
        self.c_sat.set(int(col.get("sat", 0)))
        self.c_br.set(int(col.get("bright", 0)))
        self._syncing = False

    def _lset(self, key, val):
        lyr = self._cur_layer()
        if lyr and not self._syncing:
            lyr[key] = val
            self.mark_dirty()
            self.canvas.update()

    def _pset(self, key, val):
        lyr = self._cur_layer()
        if lyr and not self._syncing:
            lyr["physics"][key] = val
            self.mark_dirty()

    def _gset(self, key, val):
        if not self._syncing:
            self.rig["physics_global"][key] = val
            self.mark_dirty()

    def _cset(self, key, val):
        lyr = self._cur_layer()
        if lyr and not self._syncing:
            lyr["color"][key] = val
            self.mark_dirty()
            self.canvas.update()

    def _aset(self, key, val):
        if not self._syncing:
            self.rig["audio"][key] = val
            thr, rng = self.rig["audio"]["threshold"], self.rig["audio"]["range"]
            self.mic_meter.set_params(thr, rng)
            self.deck_meter.set_params(thr, rng)
            if hasattr(self, "easy_meter"):
                self.easy_meter.set_params(thr, rng)
            if key == "threshold":
                self._syncing = True
                self.a_thr.set(int(val))
                if hasattr(self, "easy_thr"):
                    self.easy_thr.set(int(val))
                self._syncing = False
            self.mark_dirty()

    def _rename_layer_inline(self):
        lyr = self._cur_layer()
        if lyr and self.l_name.text().strip() and not self._syncing:
            lyr["name"] = self.l_name.text().strip()
            self.insp_title.setText(lyr["name"])
            self.mark_dirty()
            self._refresh_tree()

    def _on_parent_changed(self, _):
        lyr = self._cur_layer()
        if not lyr or self._syncing:
            return
        new_pid = self.l_parent.currentData()
        # Evitar ciclos en jerarquía
        cur = new_pid
        by_id = {l["id"]: l for l in self.rig["layers"]}
        while cur:
            if cur == lyr["id"]:
                self.load_inspector()
                return
            cur = by_id.get(cur, {}).get("parent_id")
        lyr["parent_id"] = new_pid
        self.mark_dirty()
        self._refresh_tree()
        self.canvas.update()

    def _pick_layer_tint(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        init = lyr["color"].get("tint_hex") or "#2F7CF6"
        if lyr["color"].get("tint_amt", 0) == 0:
            lyr["color"]["tint_amt"] = 75
            self.c_tint_amt.set(75)
        def _live(c):
            lyr["color"]["tint_hex"] = c.name()
            self.canvas.update()
        ColorWheelDialog.pick(init, f"Color para «{lyr['name']}»", self, on_change=_live)
        self.mark_dirty()

    def _reset_layer_color(self):
        lyr = self._cur_layer()
        if not lyr:
            return
        lyr["color"] = {"tint_hex": "", "tint_amt": 0, "hue": 0, "sat": 0, "bright": 0}
        self.mark_dirty()
        self.load_inspector()
        self.canvas.update()

    def _on_mic_changed(self, _):
        if self._syncing:
            return
        dev = self.mic_cb.currentData()
        self.rig["audio"]["device"] = dev
        if hasattr(self, "easy_mic_cb"):
            self._syncing = True
            self.easy_mic_cb.setCurrentIndex(max(0, self.easy_mic_cb.findData(dev)))
            self._syncing = False
        self._sync_mic()
        self.mark_dirty()

    def _sync_mic(self):
        dev = self.rig["audio"].get("device")
        self.mic.gain = self.rig["audio"].get("gain", 100) / 100.0
        idx = next((i for n, i in self.devices if n == dev), None)
        self.mic.open_mic(idx)

    def _toggle_rig_webcam(self, on):
        if self._syncing:
            return
        self.rig["physics_global"]["face_track"] = on
        if on:
            ok, err = self.face_tracker.start(int(self.rig.get("tracking", {}).get("camera_index", 0)))
            if not ok:
                for btn in (self.g_face, self.b_cam_bar):
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
                self.rig["physics_global"]["face_track"] = False
                QMessageBox.warning(self, "Cámara no disponible", err)
                return
            self.statusMessage.emit("Cámara activada: seguimiento facial en vivo (cabeza, inclinación, parpadeo y sonrisa)", 4000)
        else:
            self.face_tracker.stop()
            if hasattr(self, "cam_preview"):
                self.cam_preview.setPixmap(QPixmap())
                self.cam_preview.setText("Cámara apagada (activa «Cámara + Voz» o «Solo Cámara»)")
        for btn in (self.g_face, self.b_cam_bar):
            if btn.isChecked() != on:
                btn.blockSignals(True)
                btn.setChecked(on)
                btn.blockSignals(False)

    def open_obs_window(self):
        if self.obs_win is not None:
            try:
                self.obs_win.close()
            except Exception:
                pass
        self.obs_win = RigOutputWindow(self)
        self.obs_win.show()

    def export_12_sprites(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta donde guardar los 12 sprites PNG", str(M.RECORDINGS))
        if not d:
            return
        out = Path(d) / f"{self.rig.get('name', 'VTuber').strip()}_Sprites"
        written = R.bake_rig_to_12_sprites(self.rig, out, size=1024)
        res = QMessageBox.information(self, "Sprites exportados",
                                      f"Se generaron {len(written)} sprites PNG transparentes (1024×1024) en:\n{out}",
                                      QMessageBox.Open | QMessageBox.Ok)
        if res == QMessageBox.Open:
            M.open_path(out)

    def save(self):
        if self.rig_path is None:
            return self.save_as()
        self.rig_path = R.save_rig(self.rig, self.rig_path)
        self.dirty = False
        self.titleChanged.emit(self)
        self.statusMessage.emit(f"Rig VTuber guardado: {self.rig_path.name}", 2500)
        return True

    def save_as(self):
        default = str(self.rig_path or (M.BASE / f"{self.rig.get('name', 'mi_vtuber')}.vtr"))
        p, _ = QFileDialog.getSaveFileName(self, "Guardar Rig VTuber 2D (.vtr)", default, M.RIG_FILTER)
        if not p:
            return False
        self.rig_path = Path(p)
        self.rig["name"] = self.rig_path.stem
        return self.save()

    def tick(self, dt, now):
        lvl = self.mic.level
        em_st = self.mic.emotion.get_state()
        face_full = self.face_tracker.get_full_pose() if self.face_tracker.running else None
        face = (face_full["x"], face_full["y"], face_full["zoom"]) if face_full else (0.0, 0.0, 0.0)
        self.state.step(self.rig, lvl, now, dt, mouse_norm=self.canvas.mouse_norm,
                        face_norm=face, live_mode=self.canvas.live_mode,
                        emotion_state=em_st, face_full=face_full)
        self.deck_meter.set_level(lvl, self.state.mouth_state)
        self.mic_meter.set_level(lvl, self.state.mouth_state)
        if hasattr(self, "easy_meter"):
            self.easy_meter.set_level(lvl, self.state.mouth_state)
            em_id = self.state.active_emotion
            em_m = EMOTION_META.get(em_id, EMOTION_META["neutral"])
            self.em_dot.setStyleSheet(f"color:{em_m['color']}; font-size:10pt;")
            self.em_live_lbl.setText(f"Emoción: {em_m['label']}")
            if em_st.get("voiced"):
                self.em_stat_lbl.setText(f"Tono {int(em_st['pitch_hz'])} Hz · Ritmo {em_st['rhythm_hz']:.1f} Hz")
            elif face_full and face_full.get("detected"):
                self.em_stat_lbl.setText("Cámara rastreando rostro")
            else:
                self.em_stat_lbl.setText(em_m["desc"])

            if self.face_tracker.running:
                rgb, _ = self.face_tracker.get_preview_rgb()
                if rgb is not None:
                    h_p, w_p, _ = rgb.shape
                    qim = QImage(rgb.data, w_p, h_p, w_p * 3, QImage.Format_RGB888).copy()
                    self.cam_preview.setPixmap(QPixmap.fromImage(qim).scaled(
                        self.cam_preview.width() - 8, self.cam_preview.height() - 8,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation
                    ))
        self.canvas.update()
        if self.obs_win is not None and self.obs_win.isVisible():
            self.obs_win.update()

    def on_tab_deactivated(self):
        pass

    def close_cleanup(self):
        self.mic.close()
        self.face_tracker.stop()
        if self.obs_win is not None:
            try:
                self.obs_win.close()
            except Exception:
                pass
