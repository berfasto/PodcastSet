"""Pantalla de Inicio 'HomePage' (StartHubWidget) en 'Studio UI':
- QScrollArea sin marco, márgenes 56, 44, 56, 40, spacing 16px.
- Título 20pt peso 600 + subtítulo 8.5pt dim.
- Fila horizontal de Tarjetas de Creación (#creationCard: min-height 76px, padding 14px 16px, icono 32x32,
  2 líneas alineadas a la izquierda, hover borde accent + fondo hover) + barra desplegable de formato a medida.
- Cabecera Recientes (10pt semibold + botón 'Abrir archivo…' icono 16x16).
- Cuadrícula de 4 columnas (spacing 14px) de tarjetas #recentCard (212x168 px, miniatura 190x118 HiDPI 2x)
  o #hintBox centrado cuando está vacía."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

import icons
import model as M
import theme
import vtuber3d_engine as V3
from theme import C
from widgets import ColorWheelDialog, ElidedLabel, Section, Segmented, enable_numeric_drag


def _render_recent_thumbnail(meta: dict, w: int = 190, h: int = 118) -> QPixmap:
    """Genera miniatura 190x118 HiDPI (2x) limpia y representativa para cada proyecto reciente."""
    dpr = 2
    img = QImage(w * dpr, h * dpr, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

    r = QRectF(0, 0, w * dpr, h * dpr)
    grad = QLinearGradient(0, 0, w * dpr, h * dpr)
    grad.setColorAt(0.0, QColor(C["canvas"]))
    grad.setColorAt(1.0, QColor(C["panel"]))
    p.setBrush(grad)
    p.setPen(QPen(QColor(C["border_subtle"]), 1.5))
    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)

    kind = meta.get("kind", "video")
    accent_map = {
        "video": C["accent"],
        "tiktok": "#2EB67D",
        "vtuber": "#F59E0B",
        "vtuber3d": "#38BDF8",
    }
    ic_map = {
        "video": "tv",
        "tiktok": "smartphone",
        "vtuber": "rig",
        "vtuber3d": "cube",
    }
    col = QColor(accent_map.get(kind, C["accent"]))
    cx, cy = r.center().x(), r.center().y() - 6 * dpr

    # Rejilla sutil de estudio
    p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 26), 1.0))
    for gx in (0.25, 0.5, 0.75):
        p.drawLine(QPointF(r.width() * gx, 6), QPointF(r.width() * gx, r.height() - 6))
    p.drawLine(QPointF(6, r.height() * 0.68), QPointF(r.width() - 6, r.height() * 0.68))

    # Halo e icono vectorial
    p.setBrush(QColor(col.red(), col.green(), col.blue(), 42))
    p.setPen(QPen(col, 2.0))
    p.drawEllipse(QPointF(cx, cy), 23 * dpr, 23 * dpr)

    pm_ic = icons.pixmap(ic_map.get(kind, "tv"), col.name(), 24)
    p.drawPixmap(int(cx - 12 * dpr), int(cy - 12 * dpr),
                 pm_ic.scaled(24 * dpr, 24 * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # Píldora inferior de formato
    tag = (
        "VTUBER 3D · .VT3D" if kind == "vtuber3d"
        else ("VTUBER 2D · .VTR" if kind == "vtuber"
              else f"{meta.get('w', 1920)}×{meta.get('h', 1080)} · {meta.get('fps', 30)}FPS")
    )
    p.setFont(QFont("Consolas", 7 * dpr, QFont.DemiBold))
    tw = p.fontMetrics().horizontalAdvance(tag) + 14 * dpr
    pill = QRectF(cx - tw * 0.5, r.bottom() - 24 * dpr, tw, 16 * dpr)
    p.setBrush(QColor(C["surface"]))
    p.setPen(QPen(QColor(C["border"]), 1.2))
    p.drawRoundedRect(pill, 6, 6)
    p.setPen(QColor(C["dim"]))
    p.drawText(pill, Qt.AlignCenter, tag)
    p.end()

    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


class CreationCard(QFrame):
    """Tarjeta de creación Studio UI (#creationCard: min-height 76px, padding 14x16, icono 32x32 + 2 líneas)."""
    clicked = Signal(str)

    def __init__(self, mode_id: str, icon_name: str, title: str, subtitle: str):
        super().__init__()
        self.mode_id = mode_id
        self.icon_name = icon_name
        self.setObjectName("creationCard")
        self.setMinimumHeight(76)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)

        self.ic_lbl = QLabel()
        self.ic_lbl.setFixedSize(32, 32)
        self.ic_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.ic_lbl, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        self.t_lbl = QLabel(title)
        self.t_lbl.setObjectName("cardTitle")
        self.t_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.s_lbl = QLabel(subtitle)
        self.s_lbl.setObjectName("fieldLabel")
        self.s_lbl.setWordWrap(True)
        self.s_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addWidget(self.t_lbl)
        col.addWidget(self.s_lbl)
        row.addLayout(col, 1)

        self.refresh_icon(False)
        theme.bus.changed.connect(lambda _: self.refresh_icon(False))

    def refresh_icon(self, hovered: bool):
        col = C["selection_text"] if hovered else C["accent"]
        self.ic_lbl.setPixmap(icons.pixmap(self.icon_name, col, 32))

    def enterEvent(self, e):
        self.refresh_icon(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.refresh_icon(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.mode_id)
            e.accept()
            return
        super().mousePressEvent(e)


class RecentProjectCard(QFrame):
    """Tarjeta #recentCard de 212x168 px con miniatura 190x118 px (HiDPI 2x) arriba y nombre debajo."""
    openRequested = Signal(str)
    revealRequested = Signal(str)
    removeRequested = Signal(str)

    def __init__(self, meta: dict):
        super().__init__()
        self.meta = meta
        self.path = meta["path"]
        self.setObjectName("recentCard")
        self.setFixedSize(212, 168)
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(6)

        self.thumb = QLabel()
        self.thumb.setFixedSize(190, 118)
        self.thumb.setPixmap(_render_recent_thumbnail(meta, 190, 118))
        self.thumb.setAttribute(Qt.WA_TransparentForMouseEvents)
        v.addWidget(self.thumb, 0, Qt.AlignHCenter)

        bot = QHBoxLayout()
        bot.setContentsMargins(2, 0, 2, 0)
        bot.setSpacing(4)
        nm = ElidedLabel(meta.get("name") or Path(self.path).stem)
        nm.setStyleSheet("font-weight: 600; font-size: 8.5pt;")
        nm.setAttribute(Qt.WA_TransparentForMouseEvents)
        bot.addWidget(nm, 1)

        mod = meta.get("modified", "")
        if mod:
            short_dt = mod.split(" ")[0]
            dt_lbl = QLabel(short_dt)
            dt_lbl.setObjectName("mutedLabel")
            dt_lbl.setStyleSheet("font-size: 7.2pt;")
            dt_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            bot.addWidget(dt_lbl)
        v.addLayout(bot)

        self.setToolTip(f"{meta.get('name', '')}\n{self.path}\nClic derecho para abrir carpeta o quitar")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.openRequested.emit(self.path)
            e.accept()
            return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a_open = m.addAction(icons.state_icon("folder_open", 14), "Abrir proyecto")
        a_rev = m.addAction(icons.state_icon("folder", 14), "Mostrar en carpeta")
        m.addSeparator()
        a_rem = m.addAction(icons.icon("trash", C["danger"], 14), "Quitar de recientes")
        ch = m.exec(e.globalPos())
        if ch == a_open:
            self.openRequested.emit(self.path)
        elif ch == a_rev:
            self.revealRequested.emit(self.path)
        elif ch == a_rem:
            self.removeRequested.emit(self.path)


class StartHubWidget(QWidget):
    """HomePage de Studio UI (mantiene intactas todas las señales y modos de creación)."""
    createProjectRequested = Signal(dict)
    openProjectDialogRequested = Signal()
    openProjectPathRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("homePage")
        self._mode = "video_1080"
        self._bg_color = "#0E1014"
        self._transparent = False
        self._vtuber_starter = True
        self._vt3d_preset = "anime_studio"
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(sc, 1)

        page = QWidget()
        page.setObjectName("homePage")
        v = QVBoxLayout(page)
        v.setContentsMargins(56, 44, 56, 40)
        v.setSpacing(16)
        sc.setWidget(page)

        # 1. Título (20pt, peso 600, -0.3px) + Subtítulo (8.5pt dim)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        h1 = QLabel("PodcastSet Studio")
        h1.setObjectName("welcomeTitle")
        sub = QLabel("Suite de avatares VTuber 2D / 3D, tracking facial/vocal, edición multipista de audio y streaming OBS.")
        sub.setObjectName("fieldLabel")
        sub.setStyleSheet("font-size: 8.5pt;")
        title_box.addWidget(h1)
        title_box.addWidget(sub)
        v.addLayout(title_box)

        # 2. Fila horizontal de Tarjetas de Creación (#creationCard)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        self.cards = {}
        card_specs = [
            ("vtuber_2d", "rig", "Avatar 2D / Rig 2.5D", "Voz, webcam, física 2.5D y 7 emociones (.vtr)"),
            ("vtuber_3d", "cube", "Avatar 3D / VRM", "Órbita 360°, .VRM/.GLB/.OBJ y luz Toon (.vt3d)"),
            ("video_1080", "tv", "Escena / Stream 16:9", "1920×1080 · Podcast, OBS y pistas de audio"),
            ("tiktok_916", "smartphone", "Vertical / Audio Voz", "1080×1920 · Shorts, TikTok y edición de sonido"),
        ]
        for mid, ic, t_txt, s_txt in card_specs:
            c = CreationCard(mid, ic, t_txt, s_txt)
            c.clicked.connect(self._on_card_clicked)
            self.cards[mid] = c
            cards_row.addWidget(c, 1)
        v.addLayout(cards_row)

        # Sección plegable de formato personalizado (conserva 100% de opciones de lienzo/FPS/fondo/preset)
        self.custom_sec = Section("Formato personalizado y ajustes de nuevo proyecto", expanded=False)
        cf = QVBoxLayout()
        cf.setSpacing(8)

        r_top = QHBoxLayout()
        r_top.setSpacing(8)
        self.mode_seg = Segmented("Video 16:9", "Vertical 9:16", "VTuber 2D", "VTuber 3D")
        self.mode_seg.changed.connect(self._on_seg_mode)
        r_top.addWidget(self.mode_seg, 2)
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("Nombre del proyecto o avatar…")
        self.name_ed.returnPressed.connect(self._emit_create)
        r_top.addWidget(self.name_ed, 2)
        self.b_create = QPushButton("Crear proyecto")
        self.b_create.setObjectName("primaryButton")
        self.b_create.setFixedHeight(26)
        self.b_create.clicked.connect(self._emit_create)
        r_top.addWidget(self.b_create)
        cf.addLayout(r_top)

        self.opts_stack = QStackedWidget()
        self.opts_stack.setFixedHeight(32)

        p_vid = QWidget()
        pvl = QHBoxLayout(p_vid)
        pvl.setContentsMargins(0, 0, 0, 0)
        pvl.setSpacing(8)
        self.sp_w = QSpinBox()
        self.sp_w.setRange(320, 3840)
        self.sp_w.setSingleStep(10)
        self.sp_w.setValue(1920)
        self.sp_w.setSuffix(" px")
        self.sp_h = QSpinBox()
        self.sp_h.setRange(320, 2160)
        self.sp_h.setSingleStep(10)
        self.sp_h.setValue(1080)
        self.sp_h.setSuffix(" px")
        enable_numeric_drag(self.sp_w)
        enable_numeric_drag(self.sp_h)
        self.cb_fps = QComboBox()
        for f in (24, 30, 60):
            self.cb_fps.addItem(f"{f} FPS", f)
        self.cb_fps.setCurrentIndex(1)
        self.cb_bg = QComboBox()
        self.cb_bg.addItem("Fondo Estudio", "dark")
        self.cb_bg.addItem("Chroma Key Verde", "chroma")
        self.cb_bg.addItem("Transparente (Alfa)", "transparent")
        self.cb_bg.addItem("Color personalizado…", "custom")
        self.cb_bg.currentIndexChanged.connect(self._on_bg_choice)
        pvl.addWidget(QLabel("Ancho"))
        pvl.addWidget(self.sp_w)
        pvl.addWidget(QLabel("Alto"))
        pvl.addWidget(self.sp_h)
        pvl.addWidget(self.cb_fps)
        pvl.addWidget(self.cb_bg, 1)
        self.opts_stack.addWidget(p_vid)

        p_vt = QWidget()
        vtl = QHBoxLayout(p_vt)
        vtl.setContentsMargins(0, 0, 0, 0)
        vtl.setSpacing(8)
        vtl.addWidget(QLabel("Plantilla 2D:"))
        self.vt_start_seg = Segmented("Base editable 2.5D (17 capas)", "Lienzo vacío (mis PNGs)")
        self.vt_start_seg.changed.connect(lambda i: setattr(self, "_vtuber_starter", i == 0))
        vtl.addWidget(self.vt_start_seg, 1)
        self.opts_stack.addWidget(p_vt)

        p_3d = QWidget()
        v3l = QHBoxLayout(p_3d)
        v3l.setContentsMargins(0, 0, 0, 0)
        v3l.setSpacing(8)
        v3l.addWidget(QLabel("Preset 3D:"))
        self.cb_3d_preset = QComboBox()
        for pid, meta in V3.VT3D_PRESETS.items():
            self.cb_3d_preset.addItem(meta["label"], pid)
        self.cb_3d_preset.currentIndexChanged.connect(lambda: setattr(self, "_vt3d_preset", self.cb_3d_preset.currentData()))
        v3l.addWidget(self.cb_3d_preset, 1)
        self.opts_stack.addWidget(p_3d)

        cf.addWidget(self.opts_stack)
        self.custom_sec.content.addLayout(cf)
        v.addWidget(self.custom_sec)

        v.addSpacing(4)

        # 3. Cabecera Recientes (10pt semibold + botón 'Abrir archivo…' 16x16)
        rec_hdr = QHBoxLayout()
        r_lbl = QLabel("Recientes")
        r_lbl.setObjectName("headerTitle")
        rec_hdr.addWidget(r_lbl)
        rec_hdr.addStretch()
        self.b_open = QPushButton(" Abrir archivo…")
        self.b_open.setIcon(icons.state_icon("folder_open", 16))
        self.b_open.setIconSize(QSize(16, 16))
        self.b_open.setCursor(Qt.PointingHandCursor)
        self.b_open.clicked.connect(self.openProjectDialogRequested.emit)
        rec_hdr.addWidget(self.b_open)
        v.addLayout(rec_hdr)

        # 4. Cuadrícula de recientes (4 columnas, spacing 14px) o #hintBox
        self.rec_container = QWidget()
        self.rec_container_lay = QVBoxLayout(self.rec_container)
        self.rec_container_lay.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.rec_container)
        v.addStretch(1)

        self.refresh_recents()
        theme.bus.changed.connect(lambda _: self._on_theme_changed())

    def _on_theme_changed(self):
        self.b_open.setIcon(icons.state_icon("folder_open", 16))
        self.refresh_recents()

    def _on_card_clicked(self, mode_id: str):
        self._mode = mode_id
        if mode_id == "video_1080":
            self.sp_w.setValue(1920)
            self.sp_h.setValue(1080)
            self.cb_fps.setCurrentIndex(1)
        elif mode_id == "tiktok_916":
            self.sp_w.setValue(1080)
            self.sp_h.setValue(1920)
            self.cb_fps.setCurrentIndex(2)
        self._emit_create()

    def _on_seg_mode(self, idx: int):
        modes = ("video_1080", "tiktok_916", "vtuber_2d", "vtuber_3d")
        m = modes[max(0, min(3, idx))]
        self._mode = m
        if m == "video_1080":
            self.opts_stack.setCurrentIndex(0)
            self.sp_w.setValue(1920)
            self.sp_h.setValue(1080)
        elif m == "tiktok_916":
            self.opts_stack.setCurrentIndex(0)
            self.sp_w.setValue(1080)
            self.sp_h.setValue(1920)
        elif m == "vtuber_2d":
            self.opts_stack.setCurrentIndex(1)
        else:
            self.opts_stack.setCurrentIndex(2)

    def _on_bg_choice(self, _):
        key = self.cb_bg.currentData()
        if key == "dark":
            self._bg_color, self._transparent = "#0E1014", False
        elif key == "chroma":
            self._bg_color, self._transparent = "#00b140", False
        elif key == "transparent":
            self._bg_color, self._transparent = "#0E1014", True
        elif key == "custom":
            c = ColorWheelDialog.pick(self._bg_color, "Color de fondo", self)
            if c.isValid():
                self._bg_color, self._transparent = c.name(), False

    def _emit_create(self):
        if self._mode == "vtuber_2d":
            name = self.name_ed.text().strip() or "Mi VTuber 2D"
            self.createProjectRequested.emit({
                "kind": "vtuber",
                "name": name,
                "starter_rig": self._vtuber_starter,
            })
        elif self._mode == "vtuber_3d":
            name = self.name_ed.text().strip() or "Mi VTuber 3D"
            self.createProjectRequested.emit({
                "kind": "vtuber3d",
                "name": name,
                "preset": self._vt3d_preset,
            })
        else:
            w, h = self.sp_w.value(), self.sp_h.value()
            kind = "tiktok" if h > w else "video"
            default_nm = "Clip TikTok" if kind == "tiktok" else "Nuevo Proyecto"
            name = self.name_ed.text().strip() or default_nm
            self.createProjectRequested.emit({
                "kind": kind,
                "name": name,
                "w": w,
                "h": h,
                "fps": int(self.cb_fps.currentData() or 30),
                "bg": self._bg_color,
                "transparent": self._transparent,
            })
        self.name_ed.clear()

    def refresh_recents(self):
        while self.rec_container_lay.count():
            w = self.rec_container_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        items = M.load_recent_projects()
        if not items:
            hint = QFrame()
            hint.setObjectName("hintBox")
            hint.setMinimumHeight(140)
            hl = QVBoxLayout(hint)
            hl.setContentsMargins(24, 24, 24, 24)
            hl.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("folder_open", C["muted"], 26))
            ic.setAlignment(Qt.AlignCenter)
            txt = QLabel("Aún no hay proyectos recientes.\nElige una tarjeta arriba o arrastra archivos .setxd, .vtr o .vt3d aquí.")
            txt.setObjectName("mutedLabel")
            txt.setAlignment(Qt.AlignCenter)
            hl.addWidget(ic)
            hl.addWidget(txt)
            self.rec_container_lay.addWidget(hint)
            return

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        for i, meta in enumerate(items[:12]):
            card = RecentProjectCard(meta)
            card.openRequested.connect(self.openProjectPathRequested.emit)
            card.revealRequested.connect(lambda p: M.open_path(p))
            card.removeRequested.connect(self._remove_recent)
            grid.addWidget(card, i // 4, i % 4, Qt.AlignLeft | Qt.AlignTop)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        self.rec_container_lay.addWidget(grid_host)

    def _remove_recent(self, path):
        M.remove_recent_project(path)
        self.refresh_recents()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".setxd", ".vtr", ".vt3d", ".json"):
                    e.acceptProposedAction()
                    return

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".setxd", ".vtr", ".vt3d", ".json"):
                self.openProjectPathRequested.emit(u.toLocalFile())
                e.acceptProposedAction()
                return
