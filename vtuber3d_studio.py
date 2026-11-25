"""Estudio interactivo VTuber 3D (.vt3d) en su propia pestaña:
- Visor 3D en tiempo real con órbita 360° con ratón, zoom y encuadre.
- Modos de control: Solo Voz (auto-movimiento + 7 emociones), Cámara + Voz y Solo Cámara.
- Personalizador visual de avatar 3D (4 presets, 5 peinados, 4 atuendos, 6 accesorios 3D 1..6, luz Toon/Studio)
  + importación directa de modelos externos .VRM, .GLB y .OBJ.
- Ventana OBS limpia y envío directo 'Usar en Video' a pestañas 16:9 o TikTok 9:16."""
import copy
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

import icons
import model as M
import vtuber3d_engine as V3
from audio_engine import AudioChannel, list_input_devices
from face_tracker import FaceTracker
from theme import C
from voice_emotion import EMOTION_META, EMOTIONS
from widgets import CollapsibleSection, ColorWheelDialog, LevelMeter, LSlider, Segmented


class Output3DWindow(QWidget):
    """Ventana limpia independiente para capturar el VTuber 3D directamente en OBS (F11 pantalla completa)."""

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.setWindowTitle(f"OBS VTuber 3D — {studio.data.get('name', 'VTuber 3D')} (F11: Pantalla completa)")
        self.resize(760, 760)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        bg_type = self.studio.data.get("bg_type", "studio")
        if bg_type == "chroma":
            p.fillRect(self.rect(), QColor("#00b140"))
        elif bg_type == "transparent":
            p.fillRect(self.rect(), QColor("#00b140"))
        else:
            p.fillRect(self.rect(), QColor(self.studio.data.get("bg_color", "#14161D")))
        img = self.studio.last_frame
        if img is not None:
            sc = min(self.width() / img.width(), self.height() / img.height())
            dw, dh = img.width() * sc, img.height() * sc
            p.drawImage(QRectF((self.width() - dw) * 0.5, (self.height() - dh) * 0.5, dw, dh), img)


class Viewport3DWidget(QWidget):
    """Visor 3D interactivo: arrastrar izquierda órbita 360°, rueda zoom, doble clic centra, drop .VRM/.GLB/.OBJ."""
    cameraChanged = Signal()
    modelDropped = Signal(str)

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.mouse_norm = (0.0, 0.0)
        self._drag_btn = None
        self._drag_last = QPointF()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        bg_type = self.studio.data.get("bg_type", "studio")

        # Fondo profesional del estudio 3D
        if bg_type == "chroma":
            p.fillRect(r, QColor("#00b140"))
        elif bg_type == "transparent":
            tile = 28
            p.fillRect(r, QColor("#16161A"))
            c2 = QColor("#1F1F25")
            for y in range(0, int(r.height()), tile):
                for x in range(0, int(r.width()), tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        p.fillRect(x, y, tile, tile, c2)
        else:
            # Fondo estudio neutro calibrado con luz radial sutil y horizonte 3D discreto
            grad = QRadialGradient(r.center().x(), r.center().y() * 0.85, max(r.width(), r.height()) * 0.68)
            grad.setColorAt(0.0, QColor("#1F222C"))
            grad.setColorAt(0.55, QColor("#14151B"))
            grad.setColorAt(1.0, QColor("#0C0C0F"))
            p.fillRect(r, grad)
            # Rejilla sutil de suelo en perspectiva en la parte inferior
            p.setPen(QPen(QColor(255, 255, 255, 13), 1.0))
            hy = r.height() * 0.78
            cx = r.width() * 0.5
            for i in range(1, 6):
                yy = hy + (r.height() - hy) * ((i / 5.0) ** 1.4)
                p.drawLine(QPointF(0, yy), QPointF(r.width(), yy))
            for k in range(-6, 7):
                p.drawLine(QPointF(cx + k * 42, hy), QPointF(cx + k * 145, r.height()))

        # Imagen renderizada del avatar 3D
        img = self.studio.last_frame
        if img is not None:
            sc = min(r.width() / img.width(), r.height() / img.height())
            dw, dh = img.width() * sc, img.height() * sc
            p.drawImage(QRectF((r.width() - dw) * 0.5, (r.height() - dh) * 0.5, dw, dh), img)

        # Insignia superior izquierda: Modo de control + Emoción detectada
        tr = self.studio.data.get("tracking", {})
        cm = tr.get("mode", "voice")
        cm_lbl = {"voice": "SOLO VOZ", "cam_voice": "CÁMARA + VOZ", "camera": "SOLO CÁMARA"}.get(cm, "3D")
        em_id = self.studio.state.active_emotion
        em_m = EMOTION_META.get(em_id, EMOTION_META["neutral"])
        cam_tag = " · Rostro OK" if (cm != "voice" and self.studio.face_tracker.detected) else ""
        hud_txt = f"3D · {cm_lbl}{cam_tag}  ·  {em_m['label']}"
        b_col = QColor(em_m["color"] if em_id != "neutral" else "#38BDF8")
        p.setFont(QFont("Segoe UI", 8.2, QFont.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(hud_txt) + 22
        badge = QRectF(12, 10, tw, 24)
        p.setPen(QPen(b_col, 1.1))
        p.setBrush(QColor(16, 17, 22, 220))
        p.drawRoundedRect(badge, 6, 6)
        p.setPen(b_col)
        p.drawText(badge, Qt.AlignCenter, hud_txt)

        # Etiqueta sutil de órbita abajo-derecha
        cam = self.studio.data.get("camera", {})
        hint = f"Órbita {int(round(cam.get('yaw', 0)))}° · Arrastra para rotar 360° · Rueda: Zoom · Doble clic: Centrar"
        p.setFont(QFont("Segoe UI", 7.6))
        hw = p.fontMetrics().horizontalAdvance(hint) + 18
        hr = QRectF(r.width() - hw - 12, r.height() - 30, hw, 21)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(14, 14, 18, 170))
        p.drawRoundedRect(hr, 5, 5)
        p.setPen(QColor(C["muted"]))
        p.drawText(hr, Qt.AlignCenter, hint)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton):
            self._drag_btn = e.button()
            self._drag_last = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        nx = max(-1.0, min(1.0, (e.position().x() / max(1.0, self.width()) - 0.5) * 2.0))
        ny = max(-1.0, min(1.0, (e.position().y() / max(1.0, self.height()) - 0.5) * 2.0))
        self.mouse_norm = (nx, ny)
        if self._drag_btn is not None:
            d = e.position() - self._drag_last
            self._drag_last = e.position()
            cam = self.studio.data.setdefault("camera", {})
            if self._drag_btn == Qt.LeftButton:
                yaw = (float(cam.get("yaw", 0.0)) - d.x() * 0.55 + 180.0) % 360.0 - 180.0
                pitch = max(-35.0, min(40.0, float(cam.get("pitch", 4.0)) + d.y() * 0.42))
                cam["yaw"] = round(yaw, 1)
                cam["pitch"] = round(pitch, 1)
            else:
                cam["target_y"] = round(max(-0.45, min(0.65, float(cam.get("target_y", 0.20)) + d.y() * 0.004)), 3)
            self.cameraChanged.emit()
            self.studio.mark_dirty()

    def mouseReleaseEvent(self, e):
        self._drag_btn = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        self.studio.set_camera_preset("bust")

    def wheelEvent(self, e):
        cam = self.studio.data.setdefault("camera", {})
        dist = float(cam.get("distance", 2.95))
        dist *= (0.91 if e.angleDelta().y() > 0 else 1.09)
        cam["distance"] = round(max(1.55, min(4.8, dist)), 2)
        self.cameraChanged.emit()
        self.studio.mark_dirty()
        e.accept()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".vrm", ".glb", ".obj"):
                    e.acceptProposedAction()
                    return

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".vrm", ".glb", ".obj"):
                self.modelDropped.emit(u.toLocalFile())
                e.acceptProposedAction()
                return


class VTuber3DWorkspace(QWidget):
    """Espacio completo de estudio VTuber 3D (.vt3d) en pestaña independiente."""
    titleChanged = Signal(object)
    sendToVideoRequested = Signal(dict, object)   # (vt3d_data_copy, path_or_None)
    statusMessage = Signal(str, int)

    def __init__(self, vt3d_data=None, vt3d_path=None):
        super().__init__()
        self.data = vt3d_data if vt3d_data is not None else V3.new_vtuber3d("Mi VTuber 3D")
        self.vt3d_path = Path(vt3d_path) if vt3d_path else None
        self.state = V3.VTuber3DState()
        self.last_frame = None
        self.dirty = False
        self._syncing = False
        self.devices = list_input_devices()
        self.mic = AudioChannel()
        self.face_tracker = FaceTracker()
        self.obs_win = None

        self._build_ui()
        self._sync_ui_from_data()
        self._sync_mic()
        # Render inicial inmediato para evitar primer frame vacío
        self.state.step(self.data, 0.0, time.time(), 0.016)
        self.last_frame = V3.render_vtuber3d_to_image(self.data, self.state, time.time(), 540, 540)

    def tab_title(self):
        mark = " •" if self.dirty else ""
        return f"{self.data.get('name', 'VTuber 3D')}{mark}"

    def tab_kind(self):
        return "vtuber3d"

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

        # Barra superior limpia del Estudio 3D
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(42)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)

        self.ctrl_seg = Segmented("Solo Voz", "Cámara + Voz", "Solo Cámara")
        self.ctrl_seg.changed.connect(self._on_ctrl_mode_changed)
        h.addWidget(self.ctrl_seg)

        self.b_cam = QPushButton(" Cámara")
        self.b_cam.setObjectName("flat")
        self.b_cam.setIcon(icons.icon("camera", C["text_dim"], 14))
        self.b_cam.setCheckable(True)
        self.b_cam.setFixedHeight(27)
        self.b_cam.toggled.connect(self._toggle_webcam)
        h.addWidget(self.b_cam)

        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet(f"background:{C['border']};")
        h.addWidget(sep)

        self.bg_cb = QComboBox()
        self.bg_cb.setFixedHeight(27)
        for k, lbl in (("studio", "Estudio 3D"), ("transparent", "Fondo Alfa"), ("chroma", "Croma Verde")):
            self.bg_cb.addItem(lbl, k)
        self.bg_cb.currentIndexChanged.connect(lambda: (self.data.__setitem__("bg_type", self.bg_cb.currentData()), self.mark_dirty(), self.viewport.update()))
        h.addWidget(self.bg_cb)

        self.cam_cb = QComboBox()
        self.cam_cb.setFixedHeight(27)
        for k, lbl in (("bust", "Cámara: Busto"), ("closeup", "Primer plano"), ("profile", "Perfil 3/4"), ("wide", "Medio cuerpo")):
            self.cam_cb.addItem(lbl, k)
        self.cam_cb.currentIndexChanged.connect(lambda: self.set_camera_preset(self.cam_cb.currentData()))
        h.addWidget(self.cam_cb)

        h.addStretch(1)

        b_imp = QPushButton(" Importar .VRM/.OBJ")
        b_imp.setIcon(icons.icon("cube", C["text_dim"], 14))
        b_imp.setFixedHeight(27)
        b_imp.setToolTip("Cargar tu propio avatar 3D en formato .vrm, .glb o .obj")
        b_imp.clicked.connect(self.import_external_model_dialog)
        h.addWidget(b_imp)

        b_obs = QPushButton(" OBS")
        b_obs.setObjectName("flat")
        b_obs.setIcon(icons.icon("monitor", C["text_dim"], 14))
        b_obs.setFixedHeight(27)
        b_obs.clicked.connect(self.open_obs_window)
        h.addWidget(b_obs)

        b_send = QPushButton(" Usar en Video")
        b_send.setObjectName("primary")
        b_send.setIcon(icons.icon("send", "white", 13))
        b_send.setFixedHeight(27)
        b_send.setCursor(Qt.PointingHandCursor)
        b_send.setToolTip("Enviar este VTuber 3D vivo a una pestaña de Video 16:9 o TikTok 9:16")
        b_send.clicked.connect(lambda: self.sendToVideoRequested.emit(copy.deepcopy(self.data), self.vt3d_path))
        h.addWidget(b_send)
        root.addWidget(bar)

        # Cuerpo principal: Visor 3D amplio + Barra de emociones abajo | Panel derecho 4 pestañas
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self.viewport = Viewport3DWidget(self)
        self.viewport.modelDropped.connect(self.load_external_model_path)
        cv.addWidget(self.viewport, 1)
        self.emotion_bar = self._build_emotion_bar()
        cv.addWidget(self.emotion_bar)
        split.addWidget(center)

        self.right_panel = self._build_right_panel()
        split.addWidget(self.right_panel)
        split.setStretchFactor(0, 1)
        split.setSizes([1040, 360])
        self._top_bar = bar

    def _build_left_quick_3d(self):
        w = QWidget()
        w.setObjectName("panel")
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        s1 = CollapsibleSection("Presets de Avatar 3D", True)
        for pid, meta in V3.VT3D_PRESETS.items():
            b = QPushButton(meta["label"])
            b.setObjectName("choicePill")
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, k=pid: self._apply_preset(k))
            s1.content.addWidget(b)
        v.addWidget(s1)

        s2 = CollapsibleSection("Cámaras 3D", True)
        for pid, lbl in (("bust", "Busto Frontal"), ("closeup", "Primer Plano"), ("profile", "Perfil 3/4"), ("wide", "Medio Cuerpo"), ("back", "Vista Trasera")):
            b = QPushButton(lbl)
            b.setFixedHeight(25)
            b.clicked.connect(lambda _, k=pid: self.set_camera_preset(k))
            s2.content.addWidget(b)
        v.addWidget(s2)

        s3 = CollapsibleSection("Modelo Externo", True)
        b_imp = QPushButton(" Importar .VRM / .GLB / .OBJ")
        b_imp.setObjectName("primary")
        b_imp.setIcon(icons.icon("cube", "#FFFFFF", 14))
        b_imp.clicked.connect(self.import_external_model_dialog)
        s3.content.addWidget(b_imp)
        v.addWidget(s3)
        v.addStretch()
        return w

    def detach_studio_panels(self):
        """Extrae los paneles del Estudio 3D hacia los QDockWidget modulares de MainWindow."""
        self.right_panel.setMinimumWidth(200)
        self.right_panel.setMaximumWidth(16777215)
        self.emotion_bar.setMinimumHeight(86)
        self.emotion_bar.setMaximumHeight(16777215)
        left_3d = self._build_left_quick_3d()

        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        sv.addWidget(self._top_bar)
        sv.addWidget(self.viewport, 1)
        return left_3d, stage, self.right_panel, self.emotion_bar

    def _build_emotion_bar(self):
        bar = QFrame()
        bar.setObjectName("footer")
        bar.setFixedHeight(86)
        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.em_dot = QLabel("●")
        self.em_dot.setStyleSheet(f"color:{C['muted']}; font-size:10pt;")
        self.em_live_lbl = QLabel("Emoción 3D: Normal")
        self.em_live_lbl.setStyleSheet("font-weight:600; font-size:8.6pt;")
        self.em_stat_lbl = QLabel("Habla o prueba una emoción")
        self.em_stat_lbl.setProperty("muted", True)
        self.em_stat_lbl.setStyleSheet("font-size:7.8pt;")
        self.meter = LevelMeter(height=9)
        self.meter.setFixedWidth(130)

        self.cb_auto_em = QCheckBox("Auto Emoción")
        self.cb_auto_em.toggled.connect(lambda on: self._tset("auto_emotion", on))
        self.cb_auto_react = QCheckBox("Reacciones")
        self.cb_auto_react.toggled.connect(lambda on: self._tset("auto_reactions", on))

        top.addWidget(self.em_dot)
        top.addWidget(self.em_live_lbl)
        top.addWidget(self.em_stat_lbl)
        top.addWidget(self.meter)
        top.addStretch()
        top.addWidget(self.cb_auto_em)
        top.addWidget(self.cb_auto_react)
        v.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(5)
        self.em_grp = QButtonGroup(self)
        self.em_btns = {}
        choices = [("auto", "Auto (Voz)", C["accent"])] + [(eid, lbl, col) for eid, lbl, col, _ in EMOTIONS]
        for eid, lbl, col in choices:
            b = QPushButton(lbl)
            b.setObjectName("expr_pill")
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, k=eid: self._set_manual_emotion(k))
            self.em_grp.addButton(b)
            self.em_btns[eid] = b
            row.addWidget(b, 1)
        v.addLayout(row)
        return bar

    def _build_right_panel(self):
        w = QWidget()
        w.setObjectName("panel")
        w.setMinimumWidth(330)
        w.setMaximumWidth(400)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("cube", "#38BDF8", 16))
        t = QLabel("Estudio VTuber 3D")
        t.setStyleSheet("font-size:10.8pt; font-weight:700;")
        hdr.addWidget(ic)
        hdr.addSpacing(4)
        hdr.addWidget(t)
        hdr.addStretch()
        v.addLayout(hdr)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for name, fn in (
            ("Avatar 3D", self._tab_avatar),
            ("Accesorios", self._tab_accessories),
            ("Luz / 3D", self._tab_lighting),
            ("Voz / Cámara", self._tab_tracking),
        ):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(2, 6, 12, 10)
            lay.setSpacing(8)
            fn(lay)
            lay.addStretch()
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sc.setWidget(page)
            self.tabs.addTab(sc, name)
        v.addWidget(self.tabs, 1)
        return w

    def _tab_avatar(self, lay):
        # Banner si hay un modelo externo .vrm/.glb/.obj cargado
        self.ext_banner = QFrame()
        self.ext_banner.setObjectName("propCard")
        ebl = QHBoxLayout(self.ext_banner)
        ebl.setContentsMargins(10, 8, 10, 8)
        self.ext_lbl = QLabel("Modelo 3D externo")
        self.ext_lbl.setStyleSheet("color:#38BDF8; font-weight:600; font-size:8.4pt;")
        b_clr = QPushButton("Usar Avatar Integrado")
        b_clr.setObjectName("flat")
        b_clr.setFixedHeight(26)
        b_clr.clicked.connect(self._clear_external_model)
        ebl.addWidget(self.ext_lbl, 1)
        ebl.addWidget(b_clr)
        self.ext_banner.setVisible(False)
        lay.addWidget(self.ext_banner)

        s0 = CollapsibleSection("Estilos rápidos 3D", True)
        pg = QGridLayout()
        pg.setSpacing(5)
        for i, (pid, meta) in enumerate(V3.VT3D_PRESETS.items()):
            b = QPushButton(meta["label"])
            b.setObjectName("choicePill")
            b.setFixedHeight(28)
            b.clicked.connect(lambda _, k=pid: self._apply_preset(k))
            pg.addWidget(b, i // 2, i % 2)
        s0.content.addLayout(pg)
        lay.addWidget(s0)

        s1 = CollapsibleSection("Peinado, atuendo y proporciones", True)
        hr = QHBoxLayout()
        hr.addWidget(QLabel("Peinado 3D"))
        self.cb_hair = QComboBox()
        for k, lbl in V3.HAIR_STYLES:
            self.cb_hair.addItem(lbl, k)
        self.cb_hair.currentIndexChanged.connect(lambda: self._apset("hair_style", self.cb_hair.currentData()))
        hr.addWidget(self.cb_hair, 1)
        s1.content.addLayout(hr)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("Atuendo 3D"))
        self.cb_outfit = QComboBox()
        for k, lbl in V3.OUTFIT_STYLES:
            self.cb_outfit.addItem(lbl, k)
        self.cb_outfit.currentIndexChanged.connect(lambda: self._apset("outfit_style", self.cb_outfit.currentData()))
        orow.addWidget(self.cb_outfit, 1)
        s1.content.addLayout(orow)

        self.sl_head = LSlider("Tamaño de cabeza", 85, 130, "%", default=100)
        self.sl_head.changed.connect(lambda val: self._apset("head_scale", val))
        self.sl_eyes = LSlider("Tamaño de ojos", 75, 130, "%", default=102)
        self.sl_eyes.changed.connect(lambda val: self._apset("eye_size", val))
        self.sl_sh = LSlider("Ancho de hombros", 82, 122, "%", default=100)
        self.sl_sh.changed.connect(lambda val: self._apset("shoulder_width", val))
        for sl in (self.sl_head, self.sl_eyes, self.sl_sh):
            s1.content.addWidget(sl)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Colores del avatar 3D", True)
        cg = QGridLayout()
        cg.setSpacing(6)
        self._col_btns = {}
        for i, (ckey, lbl) in enumerate((
            ("hair_color", "Cabello"),
            ("eye_color", "Ojos"),
            ("skin_color", "Piel"),
            ("outfit_color", "Ropa"),
            ("accent_color", "Acento"),
        )):
            b = QPushButton(f"  {lbl}")
            b.setFixedHeight(28)
            b.clicked.connect(lambda _, k=ckey, nm=lbl: self._pick_color(k, nm))
            self._col_btns[ckey] = b
            cg.addWidget(b, i // 2, i % 2)
        s2.content.addLayout(cg)
        lay.addWidget(s2)

    def _tab_accessories(self, lay):
        s1 = CollapsibleSection("Accesorios 3D (teclas rápidas 1..6)", True)
        hint = QLabel("Activa o desactiva accesorios 3D en vivo — los auriculares, gafas, orejas spring y alas siguen la cabeza en 3D.")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:7.9pt;")
        s1.content.addWidget(hint)

        g = QGridLayout()
        g.setSpacing(6)
        self.acc_btns = {}
        for i, (hk, key, lbl) in enumerate(V3.ACCESSORY_DEFS):
            b = QPushButton(f"{hk} · {lbl}")
            b.setObjectName("choicePill")
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.clicked.connect(lambda on, k=hk: self._toggle_acc(k, on))
            self.acc_btns[hk] = b
            g.addWidget(b, i // 2, i % 2)
        s1.content.addLayout(g)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Modelo 3D externo (.VRM / .GLB / .OBJ)", True)
        d = QLabel("También puedes arrastrar un archivo .vrm (VRoid), .glb o .obj al visor 3D para animarlo con voz, cámara y emociones.")
        d.setProperty("muted", True)
        d.setWordWrap(True)
        d.setStyleSheet("font-size:7.9pt;")
        s2.content.addWidget(d)
        b_m = QPushButton(" Importar modelo 3D...")
        b_m.setIcon(icons.icon("cube", C["text_dim"], 14))
        b_m.clicked.connect(self.import_external_model_dialog)
        s2.content.addWidget(b_m)
        lay.addWidget(s2)

    def _tab_lighting(self, lay):
        s1 = CollapsibleSection("Sombreado e iluminación de estudio", True)
        sr = QHBoxLayout()
        sr.addWidget(QLabel("Estilo"))
        self.cb_shader = QComboBox()
        self.cb_shader.addItem("Cel-Shading Anime (Toon)", "toon")
        self.cb_shader.addItem("Estudio PBR Suave", "studio")
        self.cb_shader.currentIndexChanged.connect(lambda: self._lset("shader", self.cb_shader.currentData()))
        sr.addWidget(self.cb_shader, 1)
        s1.content.addLayout(sr)

        self.cb_outline = QCheckBox("Contorno Ink Anime (silueta nítida)")
        self.cb_outline.toggled.connect(lambda on: self._lset("outline", on))
        s1.content.addWidget(self.cb_outline)

        self.sl_az = LSlider("Dirección luz", -180, 180, "°", default=34)
        self.sl_az.changed.connect(lambda val: self._lset("azimuth", val))
        self.sl_el = LSlider("Altura de luz", -20, 80, "°", default=32)
        self.sl_el.changed.connect(lambda val: self._lset("elevation", val))
        self.sl_rim = LSlider("Rim Light (contorno)", 0, 100, "%", default=62)
        self.sl_rim.changed.connect(lambda val: self._lset("rim_strength", val))
        self.sl_amb = LSlider("Luz ambiente", 15, 75, "%", default=42)
        self.sl_amb.changed.connect(lambda val: self._lset("ambient", val))
        for sl in (self.sl_az, self.sl_el, self.sl_rim, self.sl_amb):
            s1.content.addWidget(sl)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Ángulos de cámara 3D", True)
        cg = QGridLayout()
        cg.setSpacing(5)
        for i, (pid, lbl) in enumerate((
            ("bust", "Frente (Busto)"), ("closeup", "Primer plano"),
            ("profile", "Perfil 3/4"), ("back", "Vista trasera"),
        )):
            b = QPushButton(lbl)
            b.setObjectName("flat")
            b.setFixedHeight(28)
            b.clicked.connect(lambda _, k=pid: self.set_camera_preset(k))
            cg.addWidget(b, i // 2, i % 2)
        s2.content.addLayout(cg)
        lay.addWidget(s2)

    def _tab_tracking(self, lay):
        s1 = CollapsibleSection("Micrófono y detección de emociones", True)
        self.mode_hint = QLabel("Solo Voz: tu voz controla boca, movimiento natural 3D y emociones automáticamente.")
        self.mode_hint.setProperty("muted", True)
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("font-size:7.9pt;")
        s1.content.addWidget(self.mode_hint)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Micrófono"))
        self.mic_cb = QComboBox()
        self.mic_cb.addItem("Sin micrófono", None)
        for name, _ in self.devices:
            self.mic_cb.addItem(name, name)
        self.mic_cb.currentIndexChanged.connect(self._on_mic_changed)
        mr.addWidget(self.mic_cb, 1)
        s1.content.addLayout(mr)

        self.sl_sens = LSlider("Sensibilidad emociones", 25, 100, "%", default=68)
        self.sl_sens.changed.connect(self._on_sens_changed)
        self.sl_thr = LSlider("Umbral de voz", 5, 85, "%", default=36)
        self.sl_thr.changed.connect(lambda val: self._aset("threshold", val))
        self.sl_gain = LSlider("Ganancia micrófono", 10, 300, "%", default=100)
        self.sl_gain.changed.connect(lambda val: (self._aset("gain", val), setattr(self.mic, "gain", val / 100.0)))
        for sl in (self.sl_sens, self.sl_thr, self.sl_gain):
            s1.content.addWidget(sl)

        rb = QHBoxLayout()
        rb.setSpacing(6)
        b_rv = QPushButton(" Recalibrar voz")
        b_rv.setObjectName("flat")
        b_rv.setIcon(icons.icon("refresh", C["text_dim"], 12))
        b_rv.clicked.connect(lambda: (self.mic.emotion.reset_calibration(), self.statusMessage.emit("Voz recalibrada: habla normal 2s", 3000)))
        b_rc = QPushButton(" Calibrar cámara")
        b_rc.setObjectName("flat")
        b_rc.setIcon(icons.icon("camera", C["text_dim"], 12))
        b_rc.clicked.connect(self._recalib_camera)
        rb.addWidget(b_rv, 1)
        rb.addWidget(b_rc, 1)
        s1.content.addLayout(rb)

        self.cam_preview = QLabel("Cámara apagada (elige «Cámara + Voz» o «Solo Cámara»)")
        self.cam_preview.setAlignment(Qt.AlignCenter)
        self.cam_preview.setFixedHeight(96)
        self.cam_preview.setWordWrap(True)
        self.cam_preview.setStyleSheet(
            f"background:{C['bg']}; border:1px solid {C['border']}; border-radius:6px; "
            f"color:{C['muted']}; font-size:7.6pt; padding:6px;"
        )
        s1.content.addWidget(self.cam_preview)
        lay.addWidget(s1)

    # =====================================================  Sincronización y acciones
    def _sync_ui_from_data(self):
        self._syncing = True
        ap = self.data.setdefault("appearance", {})
        lt = self.data.setdefault("lighting", {})
        tr = self.data.setdefault("tracking", {})
        au = self.data.setdefault("audio", {})

        self.bg_cb.setCurrentIndex(max(0, self.bg_cb.findData(self.data.get("bg_type", "studio"))))
        self.cb_hair.setCurrentIndex(max(0, self.cb_hair.findData(ap.get("hair_style", "long"))))
        self.cb_outfit.setCurrentIndex(max(0, self.cb_outfit.findData(ap.get("outfit_style", "hoodie"))))
        self.sl_head.set(int(ap.get("head_scale", 100)))
        self.sl_eyes.set(int(ap.get("eye_size", 102)))
        self.sl_sh.set(int(ap.get("shoulder_width", 100)))

        for ck, btn in self._col_btns.items():
            hx = ap.get(ck, "#2F7CF6")
            btn.setIcon(icons.icon("palette", hx, 14))

        acc = self.data.setdefault("accessories", {})
        for hk, btn in self.acc_btns.items():
            btn.setChecked(bool(acc.get(hk, False)))

        has_ext = bool(self.data.get("model_path"))
        self.ext_banner.setVisible(has_ext)
        if has_ext:
            self.ext_lbl.setText(f"Modelo 3D: {Path(self.data['model_path']).name}")

        self.cb_shader.setCurrentIndex(max(0, self.cb_shader.findData(lt.get("shader", "toon"))))
        self.cb_outline.setChecked(bool(lt.get("outline", True)))
        self.sl_az.set(int(lt.get("azimuth", 34)))
        self.sl_el.set(int(lt.get("elevation", 32)))
        self.sl_rim.set(int(lt.get("rim_strength", 62)))
        self.sl_amb.set(int(lt.get("ambient", 42)))

        cm_idx = {"voice": 0, "cam_voice": 1, "camera": 2}.get(tr.get("mode", "voice"), 0)
        self.ctrl_seg.set_index(cm_idx)
        self.cb_auto_em.setChecked(bool(tr.get("auto_emotion", True)))
        self.cb_auto_react.setChecked(bool(tr.get("auto_reactions", True)))
        sens = int(tr.get("emotion_sensitivity", 68))
        self.sl_sens.set(sens)
        self.mic.emotion.sensitivity = sens / 100.0
        man_e = tr.get("manual_emotion", "auto")
        if man_e in self.em_btns:
            self.em_btns[man_e].setChecked(True)

        self.mic_cb.setCurrentIndex(max(0, self.mic_cb.findData(au.get("device"))))
        self.sl_thr.set(int(au.get("threshold", 36)))
        self.sl_gain.set(int(au.get("gain", 100)))
        self.meter.set_params(au.get("threshold", 36), au.get("range", 12))
        self._syncing = False

    def _apset(self, key, val):
        if not self._syncing:
            self.data.setdefault("appearance", {})[key] = val
            self.mark_dirty()

    def _lset(self, key, val):
        if not self._syncing:
            self.data.setdefault("lighting", {})[key] = val
            self.mark_dirty()

    def _tset(self, key, val):
        if not self._syncing:
            self.data.setdefault("tracking", {})[key] = val
            self.mark_dirty()

    def _aset(self, key, val):
        if not self._syncing:
            self.data.setdefault("audio", {})[key] = val
            self.meter.set_params(self.data["audio"].get("threshold", 36), self.data["audio"].get("range", 12))
            self.mark_dirty()

    def _apply_preset(self, pid):
        V3.apply_vt3d_preset(self.data, pid)
        self._sync_ui_from_data()
        self.mark_dirty()
        self.statusMessage.emit(f"Estilo 3D aplicado: {V3.VT3D_PRESETS[pid]['label']}", 2500)

    def _pick_color(self, key, title):
        init = self.data.get("appearance", {}).get(key, "#2F7CF6")
        def _live(c):
            self.data.setdefault("appearance", {})[key] = c.name()
            self._col_btns[key].setIcon(icons.icon("palette", c.name(), 14))
        ColorWheelDialog.pick(init, f"Color de {title}", self, on_change=_live)
        self.mark_dirty()

    def _toggle_acc(self, hk, on):
        self.data.setdefault("accessories", {})[hk] = bool(on)
        if hk in self.acc_btns and self.acc_btns[hk].isChecked() != bool(on):
            self.acc_btns[hk].setChecked(bool(on))
        self.mark_dirty()

    def toggle_hotkey(self, digit):
        if digit in self.acc_btns:
            cur = bool(self.data.get("accessories", {}).get(digit, False))
            self._toggle_acc(digit, not cur)

    def set_camera_preset(self, pid):
        presets = {
            "bust":    {"yaw": 0.0,   "pitch": 4.0,  "distance": 3.18, "target_y": 0.28},
            "closeup": {"yaw": 0.0,   "pitch": 2.0,  "distance": 2.15, "target_y": 0.44},
            "profile": {"yaw": 26.0,  "pitch": 5.0,  "distance": 3.22, "target_y": 0.30},
            "wide":    {"yaw": 0.0,   "pitch": 5.0,  "distance": 3.85, "target_y": 0.14},
            "back":    {"yaw": 165.0, "pitch": 6.0,  "distance": 3.25, "target_y": 0.26},
        }
        if pid in presets:
            self.data["camera"] = dict(presets[pid])
            self.mark_dirty()

    def _on_ctrl_mode_changed(self, idx):
        modes = ("voice", "cam_voice", "camera")
        m = modes[max(0, min(2, idx))]
        self.data.setdefault("tracking", {})["mode"] = m
        hints = {
            "voice": "Solo Voz: tu voz controla boca, movimiento natural 3D y emociones automáticamente.",
            "cam_voice": "Cámara + Voz: la webcam mueve tu avatar 3D y tu voz anima la boca y emociones.",
            "camera": "Solo Cámara: la webcam rastrea cabeza 3D, inclinación, parpadeo y sonrisa sin micrófono.",
        }
        self.mode_hint.setText(hints[m])
        want_cam = m in ("cam_voice", "camera")
        if want_cam and not self.face_tracker.running:
            self.b_cam.setChecked(True)
        elif not want_cam and self.face_tracker.running:
            self.b_cam.setChecked(False)
        self.mark_dirty()

    def _set_manual_emotion(self, eid):
        self.data.setdefault("tracking", {})["manual_emotion"] = eid
        if eid in self.em_btns:
            self.em_btns[eid].setChecked(True)
        self.mark_dirty()

    def _on_sens_changed(self, val):
        self.mic.emotion.sensitivity = val / 100.0
        self._tset("emotion_sensitivity", val)

    def _on_mic_changed(self, _):
        if self._syncing:
            return
        self.data.setdefault("audio", {})["device"] = self.mic_cb.currentData()
        self._sync_mic()
        self.mark_dirty()

    def _sync_mic(self):
        dev = self.data.get("audio", {}).get("device")
        self.mic.gain = self.data.get("audio", {}).get("gain", 100) / 100.0
        idx = next((i for n, i in self.devices if n == dev), None)
        self.mic.open_mic(idx)

    def _toggle_webcam(self, on):
        if self._syncing:
            return
        if on:
            ok, err = self.face_tracker.start(int(self.data.get("tracking", {}).get("camera_index", 0)))
            if not ok:
                self.b_cam.blockSignals(True)
                self.b_cam.setChecked(False)
                self.b_cam.blockSignals(False)
                QMessageBox.warning(self, "Cámara no disponible", err)
                return
            self.statusMessage.emit("Cámara activa para VTuber 3D (cabeza, roll, parpadeo y sonrisa)", 3500)
        else:
            self.face_tracker.stop()
            self.cam_preview.setPixmap(QPixmap())
            self.cam_preview.setText("Cámara apagada (elige «Cámara + Voz» o «Solo Cámara»)")

    def _recalib_camera(self):
        if self.face_tracker.running:
            self.face_tracker.recalibrate()
            self.statusMessage.emit("Centro de cámara 3D recalibrado.", 2500)
        else:
            self.ctrl_seg.set_index(1)
            self._on_ctrl_mode_changed(1)

    def import_external_model_dialog(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Importar modelo 3D (.vrm, .glb, .obj)", str(M.BASE),
            "Modelos 3D VTuber (*.vrm *.glb *.obj);;VRM (*.vrm);;GLB (*.glb);;OBJ (*.obj)"
        )
        if p:
            self.load_external_model_path(p)

    def load_external_model_path(self, path):
        mesh = V3.load_external_3d_model(path)
        if mesh is None:
            QMessageBox.warning(self, "No se pudo cargar el modelo 3D",
                                "Asegúrate de que el archivo sea un .vrm, .glb (glTF binario) o .obj válido con mallas triangulares.")
            return
        self.data["model_path"] = M.import_asset(path)
        self._sync_ui_from_data()
        self.mark_dirty()
        self.statusMessage.emit(f"Modelo 3D cargado: {Path(path).name} ({len(mesh['tris'])} triángulos)", 4000)

    def _clear_external_model(self):
        self.data["model_path"] = ""
        self._sync_ui_from_data()
        self.mark_dirty()

    def open_obs_window(self):
        if self.obs_win is not None:
            try:
                self.obs_win.close()
            except Exception:
                pass
        self.obs_win = Output3DWindow(self)
        self.obs_win.show()

    def export_png(self):
        default = str(M.RECORDINGS / f"{self.data.get('name', 'VTuber3D').strip()}_3D.png")
        M.RECORDINGS.mkdir(parents=True, exist_ok=True)
        p, _ = QFileDialog.getSaveFileName(self, "Exportar captura PNG transparente (1080×1080)", default, "Imagen PNG (*.png)")
        if not p:
            return
        img = V3.render_vtuber3d_to_image(self.data, self.state, time.time(), out_w=1080, out_h=1080, transparent=True)
        img.save(str(p), "PNG")
        self.statusMessage.emit(f"PNG 3D exportado: {Path(p).name}", 3500)

    def save(self):
        if self.vt3d_path is None:
            return self.save_as()
        self.vt3d_path = V3.save_vtuber3d(self.data, self.vt3d_path)
        self.dirty = False
        self.titleChanged.emit(self)
        self.statusMessage.emit(f"VTuber 3D guardado: {self.vt3d_path.name}", 2500)
        return True

    def save_as(self):
        default = str(self.vt3d_path or (M.BASE / f"{self.data.get('name', 'mi_vtuber_3d')}.vt3d"))
        p, _ = QFileDialog.getSaveFileName(self, "Guardar VTuber 3D (.vt3d)", default, M.RIG3D_FILTER)
        if not p:
            return False
        self.vt3d_path = Path(p)
        self.data["name"] = self.vt3d_path.stem
        return self.save()

    def tick(self, dt, now):
        lvl = self.mic.level
        em_st = self.mic.emotion.get_state()
        face_full = self.face_tracker.get_full_pose() if self.face_tracker.running else None
        self.state.step(self.data, lvl, now, dt, mouse_norm=self.viewport.mouse_norm,
                        face_full=face_full, emotion_state=em_st)
        # Render 3D optimizado a 460x460 -> ultra fluido a ~55-60 FPS
        self.last_frame = V3.render_vtuber3d_to_image(self.data, self.state, now, 460, 460)
        self.meter.set_level(lvl, self.state.mouth_state)

        em_id = self.state.active_emotion
        em_m = EMOTION_META.get(em_id, EMOTION_META["neutral"])
        self.em_dot.setStyleSheet(f"color:{em_m['color']}; font-size:10pt;")
        self.em_live_lbl.setText(f"Emoción 3D: {em_m['label']}")
        if em_st.get("voiced"):
            self.em_stat_lbl.setText(f"Tono {int(em_st['pitch_hz'])} Hz · Ritmo {em_st['rhythm_hz']:.1f} Hz")
        elif face_full and face_full.get("detected"):
            self.em_stat_lbl.setText("Cámara rastreando rostro 3D")
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
        self.viewport.update()
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
