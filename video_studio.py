"""Espacio de trabajo independiente para proyectos de Video 16:9 y Vertical TikTok 9:16 (.setxd).
Cada proyecto abierto corre aislado en su propia pestaña estilo Adobe Illustrator."""
import copy
import time
import uuid
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget, QGraphicsScene)

import icons
import model as M
import vtuber3d_engine as V3
import vtuber_rig as VR
from audio_engine import (PEAKS_HZ, AsyncAudioLoader, AudioChannel, DiscordLiveReceiver,
                          TimelineMixer, clip_effective_duration, list_input_devices)
from canvas import CanvasView, CharacterItem, PropItem
from export_dialog import ExportDialog
from face_tracker import FaceTracker
from settings_dialog import SettingsDialog
from theme import C
from timeline import TimelinePanel
from vtuber_engine import SHARED_ASSETS, render_scene_frame
from widgets import (CharacterCard, CollapsibleSection, ColorWheelDialog, ElidedLabel, LevelMeter,
                     LSlider, PropCard, Segmented, SpriteSlot, enable_numeric_drag)

try:
    import pyvirtualcam
except Exception:
    pyvirtualcam = None


def _vline():
    f = QFrame()
    f.setFixedWidth(1)
    f.setFixedHeight(18)
    f.setStyleSheet(f"background:{C['border']};")
    return f


def _section(text):
    l = QLabel(text.upper())
    l.setObjectName("section")
    return l


def _hint(text):
    l = QLabel(text)
    l.setProperty("muted", True)
    l.setWordWrap(True)
    return l


def _ibtn(name, tip, cb, text="", checkable=False, size=14):
    b = QPushButton((" " + text) if text else "")
    b.setObjectName("flat")
    b.setIcon(icons.icon(name, C["text_dim"], size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    if not text:
        b.setFixedSize(28, 28)
    (b.toggled if checkable else b.clicked).connect(cb)
    return b


class VideoStudioWorkspace(QWidget):
    """Espacio completo de producción de Video 16:9 / TikTok 9:16 instanciable en múltiples pestañas."""
    titleChanged = Signal(object)
    statusMessage = Signal(str, int)
    loadProgressChanged = Signal(int, int)
    openRigInTabRequested = Signal(dict, object)   # (rig_data, rig_path)
    openRig3DInTabRequested = Signal(dict, object) # (vt3d_data, vt3d_path)
    timelineVisibilityRequested = Signal(bool)

    def __init__(self, data, project_path=None):
        super().__init__()
        self.data = data
        self.project_path = Path(project_path) if project_path else None
        self.items, self.prop_items, self.channels, self.cards, self.prop_cards = {}, {}, {}, {}, {}
        self.tl_clips = []
        self._undo_stack = []
        self._redo_stack = []
        self._tracks_cache = None
        self._solo_cids_cache = set()

        self.mixer = TimelineMixer()
        self.discord_rx = DiscordLiveReceiver()
        self.face_tracker = FaceTracker()
        self.audio_loader = AsyncAudioLoader(self)
        self.audio_loader.clipLoaded.connect(self._on_clip_loaded)
        self.audio_loader.clipFailed.connect(self._on_clip_failed)
        self.audio_loader.progressChanged.connect(self.loadProgressChanged.emit)

        self.cur_scene = 0
        self.sel = None
        self.sel_prop = None
        self.sel_acc = None
        self.snap, self.edit = False, True
        self.devices = list_input_devices()
        self.out_win = None
        self.vcam = None
        self.tl_rec = None
        self.dirty = False
        self._syncing = False
        self._alive = True
        self._last_play_t = 0.0
        self._ui_div = 0

        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        self.scene = QGraphicsScene(0, 0, cw, ch_h)
        self.bg_item = self.scene.addPixmap(QPixmap())
        self.bg_item.setZValue(-1000)

        self._make_actions()
        self._build_ui()
        self.scene.selectionChanged.connect(self.on_canvas_selection)
        self._populate_project()

        if self.data["settings"].get("discord_live"):
            self.discord_rx.start(self.data["settings"].get("discord_port", 8765))

    # =====================================================  interfaz estándar de pestaña
    def tab_title(self):
        mark = " •" if self.dirty else ""
        pname = self.data.get("project_name") or (self.project_path.stem if self.project_path else "Sin título")
        return f"{pname}{mark}"

    def tab_kind(self):
        return "tiktok" if int(self.data.get("canvas_h", 1080)) > int(self.data.get("canvas_w", 1920)) else "video"

    def is_dirty(self):
        return self.dirty

    def mark_dirty(self):
        if not self._syncing:
            self.dirty = True
            self.titleChanged.emit(self)

    def on_tab_deactivated(self):
        if self.tl_rec is not None:
            self.toggle_tl_rec(False)
        if self.mixer.playing:
            self.mixer.pause()
            self.timeline.set_playhead(self.mixer.playhead, False, force=True)

    def _msg(self, txt, ms=3000):
        self.statusMessage.emit(txt, ms)

    def _make_actions(self):
        def mk(text, cb, ic, checkable=False, checked=False):
            a = QAction(text, self)
            a.setIcon(icons.icon(ic, C["text_dim"], 15))
            a.setCheckable(checkable)
            a.setChecked(checked)
            (a.toggled if checkable else a.triggered).connect(cb if checkable else (lambda _=False: cb()))
            return a

        self.a_edit = mk("Editar (E)", self.set_edit, "move", True, True)
        self.a_guides = mk("Guías", self.set_guides, "grid", True, True)
        self.a_snap = mk("Imán", lambda v: setattr(self, "snap", v), "magnet", True, False)
        self.a_webcam = mk("Webcam 2D", self._on_webcam_action, "webcam", True, False)
        self.a_timeline = mk("Línea de tiempo (T)", self.show_timeline, "timeline", True, True)
        self.a_output = mk("Ventana OBS", self.open_output, "monitor")
        self.a_shot = mk("Captura PNG", self.screenshot, "camera")
        self.a_settings = mk("Preferencias", lambda: self.open_settings(0), "settings")

    def _tool(self, action, text=False):
        b = QToolButton()
        b.setDefaultAction(action)
        b.setObjectName("flat")
        b.setIconSize(QSize(15, 15))
        if text:
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setFixedHeight(27)
        if not text:
            b.setFixedWidth(27)
        return b

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Barra compacta del proyecto
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)

        self.res_badge = QLabel("")
        self.res_badge.setObjectName("badge")
        h.addWidget(self.res_badge)
        h.addWidget(_vline())

        for a in (self.a_edit, self.a_guides, self.a_snap, self.a_webcam):
            h.addWidget(self._tool(a))
        h.addWidget(self._tool(self.a_timeline, True))
        h.addWidget(_vline())

        self.emote_btn = QPushButton(" Reacciones")
        self.emote_btn.setObjectName("flat")
        self.emote_btn.setIcon(icons.icon("emote", C["text_dim"], 14))
        self.emote_btn.setFixedHeight(27)
        self.emote_btn.clicked.connect(self._open_emote_menu)
        h.addWidget(self.emote_btn)

        h.addStretch()

        self.b_panel_l = _ibtn("panel_left", "Panel izquierdo", self._toggle_left_panel, checkable=True)
        self.b_panel_l.setChecked(True)
        self.b_panel_r = _ibtn("panel_right", "Inspector derecho", self._toggle_right_panel, checkable=True)
        self.b_panel_r.setChecked(True)
        h.addWidget(self.b_panel_l)
        h.addWidget(self.b_panel_r)
        h.addWidget(_vline())
        h.addWidget(self._tool(self.a_output, True))
        h.addWidget(self._tool(self.a_shot))
        h.addWidget(self._tool(self.a_settings))
        h.addSpacing(4)
        b_exp = QPushButton(" Exportar")
        b_exp.setObjectName("primary")
        b_exp.setIcon(icons.icon("export", "white", 14))
        b_exp.setFixedHeight(27)
        b_exp.setCursor(Qt.PointingHandCursor)
        b_exp.clicked.connect(self.open_export)
        h.addWidget(b_exp)
        v.addWidget(bar)

        self.hsplit = QSplitter(Qt.Horizontal)
        v.addWidget(self.hsplit, 1)

        self.left_panel = self._build_left()
        self.hsplit.addWidget(self.left_panel)

        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self.vsplit = QSplitter(Qt.Vertical)
        self.view = CanvasView(self.scene)
        self.view.imageDropped.connect(self.on_canvas_image_dropped)
        self.view.zoomChanged.connect(lambda pct: self.zoom_lbl.setText(f"{pct}%"))
        self.vsplit.addWidget(self.view)

        self.timeline = TimelinePanel()
        self.timeline.closeRequested.connect(lambda: self.a_timeline.setChecked(False))
        self.timeline.playToggled.connect(self.toggle_timeline_play)
        self.timeline.stopRequested.connect(self.stop_timeline)
        self.timeline.loopToggled.connect(lambda on: setattr(self.mixer, "loop", on))
        self.timeline.recTracksToggled.connect(self.toggle_tl_rec)
        self.timeline.seekRequested.connect(self.seek_timeline)
        self.timeline.inOutChanged.connect(self.on_in_out_changed)
        self.timeline.fileDropped.connect(self.on_timeline_file_dropped)
        self.timeline.addAudioRequested.connect(self.browse_timeline_clip)
        self.timeline.addSubtrackRequested.connect(self.add_subtrack)
        self.timeline.reassignSubtrackRequested.connect(self.reassign_subtrack)
        self.timeline.renameSubtrackRequested.connect(self.rename_subtrack)
        self.timeline.deleteSubtrackRequested.connect(self.delete_subtrack)
        self.timeline.clipEdited.connect(self.edit_timeline_clip)
        self.timeline.clipSplit.connect(self.split_timeline_clip)
        self.timeline.clipDeleted.connect(self.delete_timeline_clip)
        self.timeline.clipDuplicated.connect(self.duplicate_timeline_clip)
        self.timeline.undoRequested.connect(self.undo_timeline)
        self.timeline.redoRequested.connect(self.redo_timeline)
        self.timeline.armToggled.connect(self.on_track_arm)
        self.timeline.soloToggled.connect(self.on_track_solo)
        self.timeline.muteToggled.connect(self.on_track_mute)
        self.timeline.trackSelected.connect(self.select_char)
        self.timeline.markerAddRequested.connect(self.add_marker_interactive)
        self.timeline.markerMoved.connect(self.move_marker)
        self.timeline.markerDeleted.connect(self.delete_marker)
        self.vsplit.addWidget(self.timeline)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        self.vsplit.setSizes([520, 225])
        cv.addWidget(self.vsplit, 1)

        foot = QFrame()
        foot.setObjectName("footer")
        foot.setFixedHeight(34)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 0, 10, 0)
        fl.setSpacing(6)
        self.strip_host = QWidget()
        self.strip = QHBoxLayout(self.strip_host)
        self.strip.setContentsMargins(0, 0, 0, 0)
        self.strip.setSpacing(5)
        fl.addWidget(self.strip_host, 1)
        fl.addWidget(_vline())
        fl.addWidget(_ibtn("zoom_out", "Alejar", lambda: self.view.zoom_by(0.85), size=13))
        self.zoom_lbl = QLabel("Ajustado")
        self.zoom_lbl.setStyleSheet(f"font-family:'Cascadia Mono','Consolas',monospace; font-size:8pt; color:{C['text_dim']}; min-width:44px;")
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        fl.addWidget(self.zoom_lbl)
        fl.addWidget(_ibtn("zoom_in", "Acercar", lambda: self.view.zoom_by(1.18), size=13))
        fl.addWidget(_ibtn("fit", "Ajustar lienzo", self.view.fit_canvas, size=13))
        cv.addWidget(foot)

        self.hsplit.addWidget(centre)
        self.right_panel = self._build_inspector()
        self.hsplit.addWidget(self.right_panel)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setSizes([244, 890, 380])
        self._top_bar = bar
        self._centre_wrap = centre
        self._foot_bar = foot
        self._detached_docks = False

    def detach_studio_panels(self):
        """Extrae limpiamente los paneles para alojarlos en los QDockWidget modulares de MainWindow."""
        self._detached_docks = True
        self.left_panel.setMinimumWidth(160)
        self.left_panel.setMaximumWidth(16777215)
        self.right_panel.setMinimumWidth(200)
        self.right_panel.setMaximumWidth(16777215)
        self.b_panel_l.setVisible(False)
        self.b_panel_r.setVisible(False)

        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        sv.addWidget(self._top_bar)
        sv.addWidget(self.view, 1)
        sv.addWidget(self._foot_bar)
        return self.left_panel, stage, self.right_panel, self.timeline

    def _toggle_left_panel(self, on):
        if hasattr(self, "left_panel"):
            self.left_panel.setVisible(on)

    def _toggle_right_panel(self, on):
        if hasattr(self, "right_panel"):
            self.right_panel.setVisible(on)

    def _build_left(self):
        w = QWidget()
        w.setObjectName("panel")
        w.setMinimumWidth(220)
        w.setMaximumWidth(295)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(_section("Personajes 2D"))
        top.addStretch()
        self.b_add_char = QPushButton(" Nuevo")
        self.b_add_char.setObjectName("primary")
        self.b_add_char.setIcon(icons.icon("plus", "white", 13))
        self.b_add_char.setFixedHeight(24)
        self.b_add_char.clicked.connect(self._menu_add_char)
        top.addWidget(self.b_add_char)
        v.addLayout(top)

        sc1 = QScrollArea()
        sc1.setWidgetResizable(True)
        sc1.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner1 = QWidget()
        self.card_lay = QVBoxLayout(inner1)
        self.card_lay.setContentsMargins(0, 0, 2, 0)
        self.card_lay.setSpacing(5)
        sc1.setWidget(inner1)
        v.addWidget(sc1, 3)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{C['border']};")
        v.addWidget(sep)

        top2 = QHBoxLayout()
        top2.addWidget(_section("Capas y Overlays"))
        top2.addStretch()
        self.b_add_prop = QPushButton(" Capa")
        self.b_add_prop.setIcon(icons.icon("plus", C["text_dim"], 13))
        self.b_add_prop.setFixedHeight(24)
        self.b_add_prop.clicked.connect(self._menu_add_prop)
        top2.addWidget(self.b_add_prop)
        v.addLayout(top2)

        sc2 = QScrollArea()
        sc2.setWidgetResizable(True)
        sc2.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner2 = QWidget()
        self.prop_lay = QVBoxLayout(inner2)
        self.prop_lay.setContentsMargins(0, 0, 2, 0)
        self.prop_lay.setSpacing(5)
        sc2.setWidget(inner2)
        v.addWidget(sc2, 2)
        return w

    def _menu_add_char(self):
        m = QMenu(self)
        a_sp = m.addAction(icons.icon("user", C["text_dim"], 14), "Nuevo personaje por Sprites (PNG/GIF)...")
        a_rig = m.addAction(icons.icon("rig", C["warn"], 14), "Importar Rig VTuber 2.5D (.vtr)...")
        a_3d = m.addAction(icons.icon("cube", "#38BDF8", 14), "Importar Avatar VTuber 3D (.vt3d)...")
        chosen = m.exec(self.b_add_char.mapToGlobal(self.b_add_char.rect().bottomLeft()))
        if chosen == a_sp:
            self.add_char()
        elif chosen == a_rig:
            self.import_vtr_character()
        elif chosen == a_3d:
            self.import_vt3d_character()

    def _menu_add_prop(self):
        m = QMenu(self)
        a_img = m.addAction(icons.icon("image", C["text_dim"], 14), "Imagen / Logo / GIF...")
        a_txt = m.addAction(icons.icon("text", C["text_dim"], 14), "Texto / Rótulo Lower Third")
        a_viz = m.addAction(icons.icon("wave", C["text_dim"], 14), "Visualizador de Audio 2D")
        chosen = m.exec(self.b_add_prop.mapToGlobal(self.b_add_prop.rect().bottomLeft()))
        if chosen == a_img:
            self.add_prop("image")
        elif chosen == a_txt:
            self.add_prop("text")
        elif chosen == a_viz:
            self.add_prop("visualizer")

    def _build_inspector(self):
        w = QWidget()
        w.setObjectName("panel")
        w.setMinimumWidth(355)
        w.setMaximumWidth(445)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(6)

        self.insp_stack = QStackedWidget()
        v.addWidget(self.insp_stack, 1)

        # Página 0: Escena vacía
        p_empty = QWidget()
        ev = QVBoxLayout(p_empty)
        ev.setContentsMargins(4, 4, 4, 4)
        ev.setSpacing(9)
        eh = QLabel("Escena actual")
        eh.setObjectName("h1")
        ev.addWidget(eh)
        ev.addWidget(_hint("Añade personajes, importa un Rig VTuber 2.5D (.vtr) o agrega capas y overlays:"))
        for lbl, ic, cb in (
            ("Añadir personaje por sprites", "user", self.add_char),
            ("Importar VTuber 2D (.vtr)...", "rig", self.import_vtr_character),
            ("Importar VTuber 3D (.vt3d)...", "cube", self.import_vt3d_character),
            ("Añadir imagen / logo / GIF", "image", lambda: self.add_prop("image")),
            ("Añadir rótulo de texto", "text", lambda: self.add_prop("text")),
            ("Añadir visualizador de audio 2D", "wave", lambda: self.add_prop("visualizer")),
        ):
            b = QPushButton(f"  {lbl}")
            b.setIcon(icons.icon(ic, C["accent_hover"], 15))
            b.setFixedHeight(33)
            b.setStyleSheet("text-align:left; padding-left:12px;")
            b.clicked.connect(cb)
            ev.addWidget(b)
        s_bg = CollapsibleSection("Fondo rápido", True)
        r_bg = QHBoxLayout()
        r_bg.addWidget(_ibtn("image", "Imagen de fondo", self.set_background, "Imagen"))
        r_bg.addWidget(_ibtn("palette", "Color de fondo", self.pick_color, "Color"))
        r_bg.addWidget(_ibtn("x", "Quitar imagen", self.clear_background, "Quitar"))
        s_bg.content.addLayout(r_bg)
        ev.addWidget(s_bg)
        ev.addStretch()
        self.insp_stack.addWidget(p_empty)

        # Página 1: Inspector de Personaje
        p_char = QWidget()
        cv = QVBoxLayout(p_char)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        trow = QHBoxLayout()
        self.insp_title = ElidedLabel("Personaje")
        self.insp_title.setObjectName("h1")
        trow.addWidget(self.insp_title, 1)
        trow.addWidget(_ibtn("palette", "Color", lambda: self.pick_char_color()))
        trow.addWidget(_ibtn("edit", "Renombrar", lambda: self.rename_char()))
        cv.addLayout(trow)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        cv.addWidget(self.tabs, 1)

        for name, fn in (("Sprites", self._tab_sprites), ("Física", self._tab_physics),
                         ("Accesorios", self._tab_accessories), ("FX/Audio", self._tab_fx_audio),
                         ("Escena", self._tab_scene)):
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
        self.insp_stack.addWidget(p_char)

        # Página 2: Inspector de Capa / Prop
        self.insp_stack.addWidget(self._build_prop_inspector())
        return w

    def _tab_sprites(self, lay):
        # Banner de Rig VTuber 2.5D si el personaje usa un .vtr
        self.rig_banner = QFrame()
        self.rig_banner.setObjectName("propCard")
        rbl = QHBoxLayout(self.rig_banner)
        rbl.setContentsMargins(10, 8, 10, 8)
        self.rig_lbl = QLabel("Rig VTuber 2.5D vivo")
        self.rig_lbl.setStyleSheet(f"color:{C['warn']}; font-weight:600; font-size:8.5pt;")
        b_edit_rig = QPushButton("Editar Rig")
        b_edit_rig.setFixedHeight(28)
        b_edit_rig.clicked.connect(self._open_cur_rig_in_tab)
        b_clr_rig = _ibtn("x", "Quitar Rig (volver a sprites)", self._detach_cur_rig)
        rbl.addWidget(self.rig_lbl, 1)
        rbl.addWidget(b_edit_rig)
        rbl.addWidget(b_clr_rig)
        self.rig_banner.setVisible(False)
        lay.addWidget(self.rig_banner)

        s1 = CollapsibleSection("Emociones y modo", True, "Alt+1..9")
        row = QHBoxLayout()
        self.mode_seg = Segmented("Simple", "Avanzado")
        self.mode_seg.changed.connect(self.set_sprite_mode)
        row.addWidget(self.mode_seg)
        row.addStretch()
        row.addWidget(_ibtn("rig", "Asignar Rig VTuber 2.5D (.vtr)", self._attach_vtr_to_cur, ".vtr"))
        row.addWidget(_ibtn("image", "Importar lote de sprites", self.load_many, "Lote"))
        s1.content.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.expr_cb = QComboBox()
        self.expr_cb.currentIndexChanged.connect(self.set_expr)
        row2.addWidget(self.expr_cb, 1)
        row2.addWidget(_ibtn("plus", "Nueva emoción", self.add_expr))
        row2.addWidget(_ibtn("edit", "Renombrar emoción", self.ren_expr))
        row2.addWidget(_ibtn("trash", "Eliminar emoción", self.del_expr))
        s1.content.addLayout(row2)

        self.expr_pills_lay = QHBoxLayout()
        self.expr_pills_lay.setContentsMargins(0, 0, 0, 0)
        self.expr_pills_lay.setSpacing(4)
        s1.content.addLayout(self.expr_pills_lay)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Ranuras de sprites (PNG / GIF / WebP)", True)
        info = {k: (t, hnt) for k, t, hnt in M.SLOTS}
        self.slots, self.adv_widgets = {}, []
        grid = QGridLayout()
        grid.setSpacing(5)
        layout = [
            (0, 0, "closed"), (0, 1, "mid"), (0, 2, "open"),
            (1, 0, "half_closed"), (1, 1, "half_mid"), (1, 2, "half_open"),
            (2, 0, "blink_closed"), (2, 1, "blink_mid"), (2, 2, "blink_open"),
            (3, 0, "wide"), (3, 1, "blink_wide"), (3, 2, "idle_alt"),
        ]
        for r, c, k in layout:
            sl = SpriteSlot(k, *info[k])
            sl.changed.connect(self.set_sprite)
            self.slots[k] = sl
            grid.addWidget(sl, r, c)
            if r > 0:
                self.adv_widgets.append(sl)
        s2.content.addLayout(grid)
        lay.addWidget(s2)

        s3 = CollapsibleSection("Vista previa y orientación", True)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Probar boca"))
        self.force_cb = QComboBox()
        self.force_cb.addItems(["Automática", "Cerrada", "Media", "Abierta", "Gritando"])
        self.force_cb.currentIndexChanged.connect(self.set_force)
        pr.addWidget(self.force_cb, 1)
        s3.content.addLayout(pr)
        self.flip_cb = QCheckBox("Voltear horizontalmente")
        self.flip_cb.toggled.connect(self.set_flip)
        s3.content.addWidget(self.flip_cb)
        lay.addWidget(s3)

    def _tab_physics(self, lay):
        s1 = CollapsibleSection("Física vocal y resorte 2D", True)
        mr = QHBoxLayout()
        mr.addWidget(QLabel("Estilo"))
        self.motion_cb = QComboBox()
        for k, lbl in (("spring", "Resorte natural (VTuber)"), ("bounce", "Rebote clásico"),
                       ("energetic", "Energético / Vivo"), ("subtle", "Sutil / Editorial")):
            self.motion_cb.addItem(lbl, k)
        self.motion_cb.currentIndexChanged.connect(lambda: self.cset("motion_style", self.motion_cb.currentData()))
        mr.addWidget(self.motion_cb, 1)
        s1.content.addLayout(mr)

        self.anim_sl = {}
        for key, lbl, lo, hi, suf, dft in (
            ("bounce", "Rebote vertical", 0, 80, " px", 16),
            ("sway", "Inclinación / Balanceo", 0, 25, "°", 4),
            ("squash", "Estirar y aplastar", 0, 25, "%", 5),
            ("shout_shake", "Vibración al gritar", 0, 20, " px", 6),
            ("spring_stiffness", "Rigidez del resorte", 4, 30, "", 16),
            ("spring_damping", "Amortiguación", 20, 95, "%", 68),
        ):
            sl = LSlider(lbl, lo, hi, suf, default=dft)
            sl.changed.connect(lambda v, k=key: self.cset(k, v))
            self.anim_sl[key] = sl
            s1.content.addWidget(sl)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Pivote"))
        self.pivot_cb = QComboBox()
        for k, lbl in (("bottom", "Base / Pies"), ("center", "Centro"), ("top", "Superior")):
            self.pivot_cb.addItem(lbl, k)
        self.pivot_cb.currentIndexChanged.connect(lambda: self.cset("pivot", self.pivot_cb.currentData()))
        pr.addWidget(self.pivot_cb, 1)
        rst = QPushButton("Restablecer")
        rst.setObjectName("flat")
        rst.clicked.connect(self.reset_anim)
        pr.addWidget(rst)
        s1.content.addLayout(pr)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Vida en reposo y parpadeo", True)
        for key, lbl, lo, hi, suf, dft in (
            ("breathe", "Respiración", 0, 10, "%", 1),
            ("float_amp", "Flotación / Levitación", 0, 40, " px", 0),
            ("float_speed", "Velocidad flotación", 3, 35, "", 10),
        ):
            sl = LSlider(lbl, lo, hi, suf, default=dft)
            sl.changed.connect(lambda v, k=key: self.cset(k, v))
            self.anim_sl[key] = sl
            s2.content.addWidget(sl)
        self.blink_cb = QCheckBox("Parpadeo natural automático")
        self.blink_cb.toggled.connect(lambda v: self.cset("blink", v))
        s2.content.addWidget(self.blink_cb)
        self.s_blink = LSlider("Parpadear cada", 1, 10, " s", default=4)
        self.s_blink.changed.connect(lambda v: self.cset("blink_every", v))
        self.s_blink_ms = LSlider("Duración parpadeo", 90, 420, " ms", default=200)
        self.s_blink_ms.changed.connect(lambda v: self.cset("blink_ms", v))
        s2.content.addWidget(self.s_blink)
        s2.content.addWidget(self.s_blink_ms)
        lay.addWidget(s2)

        s3 = CollapsibleSection("Foco de podcast y tracking 2D", True)
        self.dim_cb = QCheckBox("Atenuar cuando otro personaje habla")
        self.dim_cb.toggled.connect(lambda v: self.cset("dim_inactive", v))
        s3.content.addWidget(self.dim_cb)
        self.s_dim = LSlider("Intensidad atenuación", 0, 75, "%", default=38)
        self.s_dim.changed.connect(lambda v: self.cset("dim_amount", v))
        self.s_tzoom = LSlider("Acercamiento al hablar", 0, 20, "%", default=4)
        self.s_tzoom.changed.connect(lambda v: self.cset("talk_zoom", v))
        self.s_mtrack = LSlider("Seguimiento cursor", 0, 100, "%", default=0)
        self.s_mtrack.changed.connect(lambda v: self.cset("mouse_track", v))
        for sl in (self.s_dim, self.s_tzoom, self.s_mtrack):
            s3.content.addWidget(sl)
        self.ftrack_cb = QCheckBox("Seguimiento cabeza por Webcam")
        self.ftrack_cb.toggled.connect(lambda v: self.cset("face_track", v))
        self.s_fint = LSlider("Sensibilidad Webcam", 10, 150, "%", default=60)
        self.s_fint.changed.connect(lambda v: self.cset("face_intensity", v))
        s3.content.addWidget(self.ftrack_cb)
        s3.content.addWidget(self.s_fint)
        lay.addWidget(s3)

    def _tab_accessories(self, lay):
        s1 = CollapsibleSection("Accesorios acoplados", True)
        br = QHBoxLayout()
        b_add = QPushButton(" Añadir accesorio (PNG/GIF)...")
        b_add.setObjectName("primary")
        b_add.setIcon(icons.icon("plus", "white", 13))
        b_add.clicked.connect(self.add_accessory)
        br.addWidget(b_add, 1)
        br.addWidget(_ibtn("trash", "Eliminar accesorio", self.del_accessory))
        s1.content.addLayout(br)

        self.acc_list = QListWidget()
        self.acc_list.setFixedHeight(106)
        self.acc_list.currentRowChanged.connect(self._on_acc_selected)
        s1.content.addWidget(self.acc_list)
        lay.addWidget(s1)

        self.acc_props_sec = CollapsibleSection("Propiedades del accesorio", True)
        ar = QHBoxLayout()
        self.acc_vis_cb = QCheckBox("Visible")
        self.acc_vis_cb.toggled.connect(lambda v: self._aset("visible", v))
        self.acc_layer_cb = QComboBox()
        self.acc_layer_cb.addItem("Delante", "front")
        self.acc_layer_cb.addItem("Detrás", "back")
        self.acc_layer_cb.currentIndexChanged.connect(lambda: self._aset("z_rel", self.acc_layer_cb.currentData()))
        ar.addWidget(self.acc_vis_cb)
        ar.addWidget(self.acc_layer_cb, 1)
        self.acc_props_sec.content.addLayout(ar)

        self.acc_dx = LSlider("X", -400, 400, " px", default=0)
        self.acc_dy = LSlider("Y", -400, 400, " px", default=-80)
        self.acc_sc = LSlider("Escala", 10, 250, "%", default=50)
        self.acc_rot = LSlider("Rotación", -180, 180, "°", default=0)
        self.acc_op = LSlider("Opacidad", 0, 100, "%", default=100)
        self.acc_dx.changed.connect(lambda v: self._aset("dx", float(v)))
        self.acc_dy.changed.connect(lambda v: self._aset("dy", float(v)))
        self.acc_sc.changed.connect(lambda v: self._aset("scale", v / 100.0))
        self.acc_rot.changed.connect(lambda v: self._aset("rotation", float(v)))
        self.acc_op.changed.connect(lambda v: self._aset("opacity", v))
        for sl in (self.acc_dx, self.acc_dy, self.acc_sc, self.acc_rot, self.acc_op):
            self.acc_props_sec.content.addWidget(sl)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Física"))
        self.acc_phys_cb = QComboBox()
        for k, lbl in (("spring", "Resorte con inercia"), ("sway", "Balanceo"), ("float", "Flotar"), ("spin", "Girar"), ("rigid", "Rígido")):
            self.acc_phys_cb.addItem(lbl, k)
        self.acc_phys_cb.currentIndexChanged.connect(lambda: self._aset("physics_mode", self.acc_phys_cb.currentData()))
        pr.addWidget(self.acc_phys_cb, 1)
        self.acc_props_sec.content.addLayout(pr)
        self.acc_pamt = LSlider("Intensidad", 0, 100, "%", default=45)
        self.acc_pamt.changed.connect(lambda v: self._aset("physics_amount", v))
        self.acc_props_sec.content.addWidget(self.acc_pamt)
        lay.addWidget(self.acc_props_sec)

    def _tab_fx_audio(self, lay):
        s_fx = CollapsibleSection("Efectos visuales 2D", True)
        self.s_op = LSlider("Opacidad", 0, 100, "%", default=100)
        self.s_op.changed.connect(lambda v: self.cset("opacity", v))
        s_fx.content.addWidget(self.s_op)

        self.sh_cb = QCheckBox("Sombra paralela")
        self.sh_cb.toggled.connect(lambda v: self.cset("shadow", v))
        s_fx.content.addWidget(self.sh_cb)
        self.s_sh_blur = LSlider("Suavizado sombra", 2, 30, " px", default=14)
        self.s_sh_alpha = LSlider("Opacidad sombra", 10, 90, "%", default=45)
        self.s_sh_blur.changed.connect(lambda v: self.cset("shadow_blur", v))
        self.s_sh_alpha.changed.connect(lambda v: self.cset("shadow_alpha", v))
        s_fx.content.addWidget(self.s_sh_blur)
        s_fx.content.addWidget(self.s_sh_alpha)

        orow = QHBoxLayout()
        self.ol_cb = QCheckBox("Contorno")
        self.ol_cb.toggled.connect(lambda v: self.cset("outline", v))
        self.ol_talk_cb = QCheckBox("Solo al hablar")
        self.ol_talk_cb.toggled.connect(lambda v: self.cset("outline_talk_only", v))
        orow.addWidget(self.ol_cb)
        orow.addWidget(self.ol_talk_cb)
        orow.addStretch()
        orow.addWidget(_ibtn("palette", "Color contorno", self._pick_outline_color))
        s_fx.content.addLayout(orow)
        self.s_ol_w = LSlider("Grosor contorno", 1, 16, " px", default=6)
        self.s_ol_w.changed.connect(lambda v: self.cset("outline_width", v))
        s_fx.content.addWidget(self.s_ol_w)

        self.s_br = LSlider("Brillo", -50, 50, "%", default=0)
        self.s_sat = LSlider("Saturación", -100, 100, "%", default=0)
        self.s_br.changed.connect(lambda v: self.cset("brightness", v))
        self.s_sat.changed.connect(lambda v: self.cset("saturation", v))
        s_fx.content.addWidget(self.s_br)
        s_fx.content.addWidget(self.s_sat)
        lay.addWidget(s_fx)

        s_mic = CollapsibleSection("Micrófono y detección vocal", True)
        r0 = QHBoxLayout()
        self.dev_cb = QComboBox()
        self.dev_cb.currentIndexChanged.connect(self.set_device)
        r0.addWidget(self.dev_cb, 1)
        r0.addWidget(_ibtn("refresh", "Actualizar dispositivos", lambda: self.refresh_devices()))
        r0.addWidget(_ibtn("sliders", "Filtros DSP (OBS)", lambda: self.open_settings(1)))
        s_mic.content.addLayout(r0)

        self.big_meter = LevelMeter(interactive=True, height=18)
        self.big_meter.thresholdChanged.connect(lambda v: (self.s_thr.set(v), self.cset("threshold", v), self.sync_meters()))
        s_mic.content.addWidget(self.big_meter)

        self.s_thr = LSlider("Umbral de voz", 1, 90, "%", default=40)
        self.s_rng = LSlider("Separación bocas", 3, 40, "%", default=12)
        self.s_hold = LSlider("Retención", 0, 400, " ms", default=90)
        self.s_gain = LSlider("Sensibilidad", 20, 500, "%", default=100)
        self.s_thr.changed.connect(lambda v: (self.cset("threshold", v), self.sync_meters()))
        self.s_rng.changed.connect(lambda v: (self.cset("range", v), self.sync_meters()))
        self.s_hold.changed.connect(lambda v: self.cset("hold", v))
        self.s_gain.changed.connect(self.set_gain)
        for sl in (self.s_thr, self.s_rng, self.s_hold, self.s_gain):
            s_mic.content.addWidget(sl)
        self.mute_cb = QCheckBox("Silenciar micrófono")
        self.mute_cb.toggled.connect(lambda v: (self.cset("mute", v), self.refresh_cards(), self.sync_timeline()))
        s_mic.content.addWidget(self.mute_cb)
        lay.addWidget(s_mic)

    def _tab_scene(self, lay):
        s1 = CollapsibleSection("Transformación", True)
        self.vis_cb = QCheckBox("Visible en esta escena")
        self.vis_cb.toggled.connect(lambda v: (self.pset("visible", v), self.refresh_cards()))
        s1.content.addWidget(self.vis_cb)
        self.s_size = LSlider("Tamaño", 5, 300, "%", default=100)
        self.s_size.changed.connect(lambda v: self.pset("scale", v / 100.0))
        s1.content.addWidget(self.s_size)

        rot_row = QHBoxLayout()
        self.s_rot = LSlider("Rotación", -180, 180, "°", default=0)
        self.s_rot.changed.connect(lambda v: self.pset("rotation", float(v)))
        b_rot0 = QPushButton("0°")
        b_rot0.setObjectName("flat")
        b_rot0.setFixedSize(32, 26)
        b_rot0.clicked.connect(lambda: (self.s_rot.set(0), self.pset("rotation", 0.0)))
        rot_row.addWidget(self.s_rot, 1)
        rot_row.addWidget(b_rot0)
        s1.content.addLayout(rot_row)

        xy = QHBoxLayout()
        self.sp_x, self.sp_y = QSpinBox(), QSpinBox()
        for lbl, sp, k in (("X", self.sp_x, "x"), ("Y", self.sp_y, "y")):
            sp.setRange(-4000, 6000)
            sp.valueChanged.connect(lambda val, k=k: self.pset(k, float(val)))
            lbl_w = QLabel(lbl)
            enable_numeric_drag(sp, lbl_w)
            xy.addWidget(lbl_w)
            xy.addWidget(sp)
        s1.content.addLayout(xy)

        zr = QHBoxLayout()
        for txt, d in (("Al frente", 1), ("Al fondo", -1)):
            b = QPushButton(txt)
            b.clicked.connect(lambda _, d=d: self.sel in self.items and self.change_z(self.items[self.sel], d))
            zr.addWidget(b)
        zr.addWidget(_ibtn("center", "Centrar", self.center_char, "Centrar"))
        s1.content.addLayout(zr)
        lay.addWidget(s1)

        s2 = CollapsibleSection("Escena y fondo", True)
        self.scene_name = QLineEdit()
        self.scene_name.editingFinished.connect(self.rename_scene_inline)
        s2.content.addWidget(self.scene_name)

        bt_row = QHBoxLayout()
        bt_row.addWidget(QLabel("Tipo fondo"))
        self.bg_type_cb = QComboBox()
        for k, lbl in (("color", "Color sólido / Croma"), ("gradient", "Degradado estudio"), ("transparent", "Transparente (Alfa)")):
            self.bg_type_cb.addItem(lbl, k)
        self.bg_type_cb.currentIndexChanged.connect(lambda: self._sc_set("bg_type", self.bg_type_cb.currentData()))
        bt_row.addWidget(self.bg_type_cb, 1)
        s2.content.addLayout(bt_row)

        r_bg = QHBoxLayout()
        r_bg.addWidget(_ibtn("image", "Imagen", self.set_background, "Imagen"))
        r_bg.addWidget(_ibtn("x", "Quitar", self.clear_background, "Quitar"))
        r_bg.addWidget(_ibtn("palette", "Color", self.pick_color, "Color"))
        r_bg.addWidget(_ibtn("palette", "Color 2", self._pick_grad_color2, "Color 2"))
        s2.content.addLayout(r_bg)

        self.s_bg_blur = LSlider("Desenfoque fondo", 0, 28, " px", default=0)
        self.s_bg_dim = LSlider("Oscurecer fondo", 0, 80, "%", default=0)
        self.s_bg_vig = LSlider("Viñeta", 0, 80, "%", default=0)
        self.s_bg_blur.changed.connect(lambda v: self._sc_set("bg_blur", v))
        self.s_bg_dim.changed.connect(lambda v: self._sc_set("bg_dim", v))
        self.s_bg_vig.changed.connect(lambda v: self._sc_set("bg_vignette", v))
        for sl in (self.s_bg_blur, self.s_bg_dim, self.s_bg_vig):
            s2.content.addWidget(sl)

        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("Transición"))
        self.trans_cb = QComboBox()
        for k, lbl in (("fade", "Fundido suave"), ("slide", "Deslizamiento"), ("cut", "Corte directo")):
            self.trans_cb.addItem(lbl, k)
        self.trans_cb.currentIndexChanged.connect(lambda: self._sc_set("transition", self.trans_cb.currentData()))
        tr_row.addWidget(self.trans_cb, 1)
        s2.content.addLayout(tr_row)
        lay.addWidget(s2)

    def _build_prop_inspector(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        hdr = QHBoxLayout()
        self.prop_title = ElidedLabel("Capa")
        self.prop_title.setObjectName("h1")
        hdr.addWidget(self.prop_title, 1)
        hdr.addWidget(_ibtn("trash", "Eliminar capa", lambda: self.sel_prop and self.del_prop(self.sel_prop)))
        v.addLayout(hdr)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 4, 4, 8)
        lay.setSpacing(8)

        s_tr = CollapsibleSection("Transformación", True)
        r_v = QHBoxLayout()
        self.p_vis_cb = QCheckBox("Visible")
        self.p_vis_cb.toggled.connect(lambda val: self._propset("visible", val))
        self.p_lock_cb = QCheckBox("Bloquear")
        self.p_lock_cb.toggled.connect(lambda val: self._propset("locked", val))
        r_v.addWidget(self.p_vis_cb)
        r_v.addWidget(self.p_lock_cb)
        s_tr.content.addLayout(r_v)

        self.p_sc = LSlider("Escala", 10, 350, "%", default=100)
        self.p_rot = LSlider("Rotación", -180, 180, "°", default=0)
        self.p_op = LSlider("Opacidad", 0, 100, "%", default=100)
        self.p_z = LSlider("Orden Z", -50, 200, "", default=50)
        self.p_sc.changed.connect(lambda val: self._propset("scale", val / 100.0))
        self.p_rot.changed.connect(lambda val: self._propset("rotation", float(val)))
        self.p_op.changed.connect(lambda val: self._propset("opacity", val))
        self.p_z.changed.connect(lambda val: self._propset("z", val))
        for sl in (self.p_sc, self.p_rot, self.p_op, self.p_z):
            s_tr.content.addWidget(sl)

        ra = QHBoxLayout()
        ra.addWidget(QLabel("Animación"))
        self.p_anim_cb = QComboBox()
        for k, lbl in (("none", "Estática"), ("breathe", "Respiración"), ("float", "Flotación"), ("pulse", "Pulso con audio")):
            self.p_anim_cb.addItem(lbl, k)
        self.p_anim_cb.currentIndexChanged.connect(lambda: self._propset("anim", self.p_anim_cb.currentData()))
        ra.addWidget(self.p_anim_cb, 1)
        s_tr.content.addLayout(ra)
        lay.addWidget(s_tr)

        self.prop_kind_stack = QStackedWidget()
        pi = QWidget()
        il = QVBoxLayout(pi)
        il.setContentsMargins(0, 0, 0, 0)
        b_img = QPushButton(" Elegir imagen o GIF...")
        b_img.setObjectName("primary")
        b_img.setIcon(icons.icon("image", "white", 14))
        b_img.clicked.connect(self._pick_prop_image)
        il.addWidget(b_img)
        il.addStretch()
        self.prop_kind_stack.addWidget(pi)

        pt = QWidget()
        tl = QVBoxLayout(pt)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(7)
        self.pt_text = QLineEdit()
        self.pt_text.textChanged.connect(lambda t: self._propset("text", t))
        self.pt_sub = QLineEdit()
        self.pt_sub.setPlaceholderText("Subtítulo opcional")
        self.pt_sub.textChanged.connect(lambda t: self._propset("subtext", t))
        self.pt_size = LSlider("Fuente", 14, 120, " pt", default=42)
        self.pt_size.changed.connect(lambda val: self._propset("font_size", val))
        self.pt_w = LSlider("Ancho", 180, 1800, " px", default=620)
        self.pt_w.changed.connect(lambda val: self._propset("w", val))
        self.pt_bg = QCheckBox("Tarjeta de fondo")
        self.pt_bg.toggled.connect(lambda val: self._propset("bg_enabled", val))
        self.pt_bar = QCheckBox("Barra de acento")
        self.pt_bar.toggled.connect(lambda val: self._propset("accent_bar", val))
        rc = QHBoxLayout()
        rc.addWidget(_ibtn("palette", "Color texto", lambda: self._pick_prop_color("color"), "Texto"))
        rc.addWidget(_ibtn("palette", "Color fondo", lambda: self._pick_prop_color("bg_color"), "Fondo"))
        rc.addWidget(_ibtn("palette", "Color acento", lambda: self._pick_prop_color("accent_color"), "Acento"))
        for w in (self.pt_text, self.pt_sub, self.pt_size, self.pt_w, self.pt_bg, self.pt_bar):
            tl.addWidget(w)
        tl.addLayout(rc)
        tl.addStretch()
        self.prop_kind_stack.addWidget(pt)

        pv = QWidget()
        vl = QVBoxLayout(pv)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(7)
        rv = QHBoxLayout()
        rv.addWidget(QLabel("Estilo"))
        self.pv_style = QComboBox()
        for k, lbl in (("bars", "Barras"), ("mirror", "Espejo"), ("wave", "Onda"), ("dots", "Puntos")):
            self.pv_style.addItem(lbl, k)
        self.pv_style.currentIndexChanged.connect(lambda: self._propset("style", self.pv_style.currentData()))
        rv.addWidget(self.pv_style, 1)
        vl.addLayout(rv)
        self.pv_bars = LSlider("Bandas", 8, 64, "", default=36)
        self.pv_sens = LSlider("Sensibilidad", 40, 260, "%", default=120)
        self.pv_w = LSlider("Ancho", 160, 1800, " px", default=560)
        self.pv_h = LSlider("Alto", 40, 400, " px", default=110)
        self.pv_bars.changed.connect(lambda val: self._propset("bars", val))
        self.pv_sens.changed.connect(lambda val: self._propset("sensitivity", val))
        self.pv_w.changed.connect(lambda val: self._propset("w", val))
        self.pv_h.changed.connect(lambda val: self._propset("h", val))
        rvc = QHBoxLayout()
        rvc.addWidget(_ibtn("palette", "Color 1", lambda: self._pick_prop_color("color"), "Color 1"))
        rvc.addWidget(_ibtn("palette", "Color 2", lambda: self._pick_prop_color("color2"), "Color 2"))
        for w in (self.pv_bars, self.pv_sens, self.pv_w, self.pv_h):
            vl.addWidget(w)
        vl.addLayout(rvc)
        vl.addStretch()
        self.prop_kind_stack.addWidget(pv)

        lay.addWidget(self.prop_kind_stack)
        lay.addStretch()
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        return page

    # =====================================================  Integración Rig VTuber 2.5D (.vtr)
    def add_vtuber_rig_character(self, rig_data, rig_path=None):
        name = rig_data.get("name", "Mi VTuber 2D")
        ch = M.new_char(name, len(self.data["characters"]))
        ch["rig_data"] = copy.deepcopy(rig_data)
        ch["rig_path"] = str(rig_path) if rig_path else ""
        ch["expressions"] = [M.new_expression(ex.get("name", f"E{i+1}")) for i, ex in enumerate(rig_data.get("expressions", []))] or [M.new_expression()]
        self.data["characters"].append(ch)
        self.spawn(ch)
        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        ref_w, ref_h = self.items[ch["id"]].ref_size
        pl = self.placement(ch["id"])
        pl["x"] = (cw - ref_w) / 2.0
        pl["y"] = (ch_h - ref_h) * (0.46 if ch_h > cw else 0.56)
        pl["scale"] = 1.45 if ch_h > cw else 1.12
        self.sel = ch["id"]
        self.sel_prop = None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.sync_timeline()
        self.load_inspector()
        self._msg(f"VTuber 2.5D «{name}» añadido a la escena", 3500)
        return ch

    def import_vtr_character(self):
        p, _ = QFileDialog.getOpenFileName(self, "Importar Rig VTuber 2D (.vtr)", str(M.BASE), M.RIG_FILTER)
        if p:
            rig = VR.load_rig(Path(p))
            self.add_vtuber_rig_character(rig, Path(p))

    def add_vtuber3d_character(self, vt3d_data, vt3d_path=None):
        name = vt3d_data.get("name", "Mi VTuber 3D")
        ch = M.new_char(name, len(self.data["characters"]))
        ch["rig3d_data"] = copy.deepcopy(vt3d_data)
        ch["rig3d_path"] = str(vt3d_path) if vt3d_path else ""
        self.data["characters"].append(ch)
        self.spawn(ch)
        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        ref_w, ref_h = self.items[ch["id"]].ref_size
        pl = self.placement(ch["id"])
        pl["x"] = (cw - ref_w) / 2.0
        pl["y"] = (ch_h - ref_h) * (0.46 if ch_h > cw else 0.56)
        pl["scale"] = 1.48 if ch_h > cw else 1.15
        self.sel = ch["id"]
        self.sel_prop = None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.sync_timeline()
        self.load_inspector()
        self._msg(f"VTuber 3D «{name}» añadido en vivo a la escena", 3500)
        return ch

    def import_vt3d_character(self):
        p, _ = QFileDialog.getOpenFileName(self, "Importar Avatar VTuber 3D (.vt3d)", str(M.BASE), M.RIG3D_FILTER)
        if p:
            d3 = V3.load_vtuber3d(Path(p))
            self.add_vtuber3d_character(d3, Path(p))

    def _attach_vtr_to_cur(self):
        ch = self.cur()
        if not ch:
            return
        p, _ = QFileDialog.getOpenFileName(self, "Asignar VTuber 2D (.vtr) o 3D (.vt3d)", str(M.BASE),
                                           "VTuber 2D/3D (*.vtr *.vt3d);;VTuber 2D (*.vtr);;VTuber 3D (*.vt3d)")
        if p:
            pp = Path(p)
            if pp.suffix.lower() == ".vt3d":
                ch["rig3d_data"] = V3.load_vtuber3d(pp)
                ch["rig3d_path"] = str(pp)
                ch["rig_data"] = None
                ch["rig_path"] = ""
            else:
                ch["rig_data"] = VR.load_rig(pp)
                ch["rig_path"] = str(pp)
                ch["rig3d_data"] = None
                ch["rig3d_path"] = ""
            self._reload_char_keep_center(ch)
            self.mark_dirty()
            self.refresh_cards()
            self.load_inspector()

    def _open_cur_rig_in_tab(self):
        ch = self.cur()
        if not ch:
            return
        if ch.get("rig3d_data"):
            self.openRig3DInTabRequested.emit(copy.deepcopy(ch["rig3d_data"]), Path(ch["rig3d_path"]) if ch.get("rig3d_path") else None)
        elif ch.get("rig_data"):
            self.openRigInTabRequested.emit(copy.deepcopy(ch["rig_data"]), Path(ch["rig_path"]) if ch.get("rig_path") else None)

    def _detach_cur_rig(self):
        ch = self.cur()
        if ch:
            ch["rig_data"] = None
            ch["rig_path"] = ""
            ch["rig3d_data"] = None
            ch["rig3d_path"] = ""
            self._reload_char_keep_center(ch)
            self.mark_dirty()
            self.refresh_cards()
            self.load_inspector()

    # =====================================================  Poblado y formato
    def _populate_project(self):
        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        self.view.set_canvas_size(cw, ch_h)
        kind_tag = "9:16 TikTok" if ch_h > cw else "16:9 Video"
        self.res_badge.setText(f"{kind_tag} · {cw}×{ch_h} · {self.data.get('fps', 30)}fps")
        for ch in self.data["characters"]:
            self.spawn(ch)
        self.sel = self.data["characters"][0]["id"] if self.data["characters"] else None
        self.load_saved_clips_async()
        io = self.data.get("in_out", [None, None])
        self.timeline.set_in_out(io[0], io[1])
        self.mixer.set_loop_range(io[0], io[1])
        self.timeline.set_markers(self.data.get("markers", []))
        self.apply_scene()
        self.refresh_cards()
        self.refresh_strip()
        self.sync_timeline()
        self.load_inspector()

    def set_canvas_format(self, w, h, fps):
        self.data["canvas_w"], self.data["canvas_h"], self.data["fps"] = int(w), int(h), int(fps)
        self.view.set_canvas_size(w, h)
        kind_tag = "9:16 TikTok" if h > w else "16:9 Video"
        self.res_badge.setText(f"{kind_tag} · {w}×{h} · {fps}fps")
        self.apply_scene()
        self.mark_dirty()

    # =====================================================  Accesorios, Props, Emotes, Webcam
    def _cur_acc(self):
        ch = self.cur()
        return next((a for a in ch.get("accessories", []) if a["id"] == self.sel_acc), None) if (ch and self.sel_acc) else None

    def add_accessory(self):
        ch = self.cur()
        if not ch:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Elegir imagen/GIF", str(M.BASE), M.EXTS)
        if not path:
            return
        acc = M.new_accessory(Path(path).stem[:24], M.import_asset(path))
        ch.setdefault("accessories", []).append(acc)
        self.sel_acc = acc["id"]
        self.mark_dirty()
        self.load_inspector()
        self.refresh_cards()

    def del_accessory(self):
        ch = self.cur()
        acc = self._cur_acc()
        if not ch or not acc:
            return
        ch["accessories"] = [a for a in ch.get("accessories", []) if a["id"] != acc["id"]]
        self.sel_acc = ch["accessories"][0]["id"] if ch["accessories"] else None
        self.mark_dirty()
        self.load_inspector()
        self.refresh_cards()

    def _on_acc_selected(self, row):
        if self._syncing:
            return
        ch = self.cur()
        if not ch:
            return
        accs = ch.get("accessories", [])
        self.sel_acc = accs[row]["id"] if 0 <= row < len(accs) else None
        self._load_acc_fields()

    def _aset(self, key, val):
        acc = self._cur_acc()
        if acc and not self._syncing:
            acc[key] = val
            self.mark_dirty()
            if self.sel in self.items:
                self.items[self.sel].update()

    def _load_acc_fields(self):
        acc = self._cur_acc()
        self.acc_props_sec.setEnabled(acc is not None)
        if not acc:
            return
        self._syncing = True
        self.acc_vis_cb.setChecked(bool(acc.get("visible", True)))
        self.acc_layer_cb.setCurrentIndex(max(0, self.acc_layer_cb.findData(acc.get("z_rel", "front"))))
        self.acc_dx.set(int(round(acc.get("dx", 0.0))))
        self.acc_dy.set(int(round(acc.get("dy", -80.0))))
        self.acc_sc.set(int(round(acc.get("scale", 0.5) * 100)))
        self.acc_rot.set(int(round(acc.get("rotation", 0.0))))
        self.acc_op.set(int(acc.get("opacity", 100)))
        self.acc_phys_cb.setCurrentIndex(max(0, self.acc_phys_cb.findData(acc.get("physics_mode", "spring"))))
        self.acc_pamt.set(int(acc.get("physics_amount", 45)))
        self._syncing = False

    def _cur_prop(self):
        return next((p for p in self.sdata().get("props", []) if p["id"] == self.sel_prop), None) if self.sel_prop else None

    def add_prop(self, kind, image_path=None, pos=None):
        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        pr = M.new_prop(kind, canvas_w=cw, canvas_h=ch_h)
        if kind == "image":
            if not image_path:
                image_path, _ = QFileDialog.getOpenFileName(self, "Elegir imagen/GIF", str(M.BASE), M.EXTS)
                if not image_path:
                    return
            pr["path"] = M.import_asset(image_path)
            pr["name"] = Path(image_path).stem[:24]
        if pos is not None:
            pr["x"], pr["y"] = pos[0] - pr["w"] * 0.5, pos[1] - pr["h"] * 0.5
        self.sdata().setdefault("props", []).append(pr)
        self.sel, self.sel_prop = None, pr["id"]
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.load_inspector()

    def on_canvas_image_dropped(self, path, sx, sy):
        self.add_prop("image", image_path=path, pos=(sx, sy))

    def select_prop(self, pid):
        self.sel, self.sel_prop = None, pid
        for c in self.cards.values():
            c.set_selected(False)
        for k, c in self.prop_cards.items():
            c.set_selected(k == pid)
        self._syncing = True
        self.scene.clearSelection()
        if self.edit and pid in self.prop_items:
            self.prop_items[pid].setSelected(True)
        self._syncing = False
        self.load_inspector()

    def toggle_prop_vis(self, pid, v):
        pr = next((p for p in self.sdata().get("props", []) if p["id"] == pid), None)
        if pr:
            pr["visible"] = v
            if pid in self.prop_items:
                self.prop_items[pid].sync_from_prop()
            self.mark_dirty()
            if pid == self.sel_prop:
                self.load_inspector()

    def rename_prop(self, pid):
        pr = next((p for p in self.sdata().get("props", []) if p["id"] == pid), None)
        if pr:
            name, ok = QInputDialog.getText(self, "Renombrar capa", "Nombre:", text=pr["name"])
            if ok and name.strip():
                pr["name"] = name.strip()
                self.mark_dirty()
                self.refresh_cards()
                self.load_inspector()

    def del_prop(self, pid):
        self.sdata()["props"] = [p for p in self.sdata().get("props", []) if p["id"] != pid]
        if self.sel_prop == pid:
            self.sel_prop = None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.load_inspector()

    def _propset(self, key, val):
        pr = self._cur_prop()
        if pr and not self._syncing:
            pr[key] = val
            if pr["id"] in self.prop_items:
                self.prop_items[pr["id"]].sync_from_prop()
            self.mark_dirty()

    def _pick_prop_image(self):
        pr = self._cur_prop()
        if pr:
            path, _ = QFileDialog.getOpenFileName(self, "Elegir imagen/GIF", str(M.BASE), M.EXTS)
            if path:
                pr["path"] = M.import_asset(path)
                if pr["id"] in self.prop_items:
                    self.prop_items[pr["id"]].sync_from_prop()
                self.mark_dirty()

    def _pick_prop_color(self, key):
        pr = self._cur_prop()
        if not pr:
            return
        def _live(c):
            pr[key] = c.name()
            if pr["id"] in self.prop_items:
                self.prop_items[pr["id"]].update()
        ColorWheelDialog.pick(pr.get(key, "#FFFFFF"), "Color de capa", self, on_change=_live)
        self.mark_dirty()

    def store_prop_item(self, item):
        self.mark_dirty()
        if item.pid == self.sel_prop:
            self.load_inspector()

    def _open_emote_menu(self):
        ch = self.cur()
        if not ch:
            self._msg("Selecciona un personaje primero para disparar una reacción 2D.", 2500)
            return
        m = QMenu(self)
        acts = {m.addAction(f"{sym}   {name}"): k for k, sym, name, _ in M.EMOTE_CATALOG}
        chosen = m.exec(self.emote_btn.mapToGlobal(self.emote_btn.rect().bottomLeft()))
        if chosen in acts:
            self.trigger_emote(ch["id"], acts[chosen])

    def trigger_emote(self, cid, kind):
        if cid in self.items:
            self.items[cid].anim.trigger_emote(kind, time.time())

    def _on_webcam_action(self, on):
        if on and not self.face_tracker.running:
            ok, err = self.face_tracker.start(self.data["settings"].get("webcam_index", 0))
            if not ok:
                self.a_webcam.blockSignals(True)
                self.a_webcam.setChecked(False)
                self.a_webcam.blockSignals(False)
                QMessageBox.warning(self, "Webcam no disponible", err)
            else:
                self._msg("Tracking facial 2D activado", 3500)
        elif not on and self.face_tracker.running:
            self.face_tracker.stop()

    def toggle_face_tracking(self):
        self.a_webcam.setChecked(not self.face_tracker.running)
        return self.face_tracker.running

    # =====================================================  Diálogos y utilidades
    def reset_anim(self):
        ch = self.cur()
        if not ch:
            return
        for k, v in (("motion_style", "spring"), ("bounce", 16), ("sway", 4), ("squash", 5),
                     ("breathe", 1), ("float_amp", 0), ("float_speed", 10), ("shout_shake", 6),
                     ("spring_stiffness", 16), ("spring_damping", 68)):
            ch[k] = v
        self.mark_dirty()
        self.load_inspector()

    def center_char(self):
        ch = self.cur()
        if not ch:
            return
        it = self.items[ch["id"]]
        pl = self.placement(ch["id"])
        w, h = it.ref_size
        cw, ch_h = self.data.get("canvas_w", M.W), self.data.get("canvas_h", M.H)
        pl["x"], pl["y"] = (cw - w) / 2.0, (ch_h - h) / 2.0
        self.mark_dirty()
        self.apply_scene()
        self.load_inspector()

    def _pick_outline_color(self):
        ch = self.cur()
        if not ch:
            return
        def _live(c):
            ch["outline_color"] = c.name()
            if ch["id"] in self.items:
                self.items[ch["id"]].update()
        ColorWheelDialog.pick(ch.get("outline_color", "#FFFFFF"), "Color contorno", self, on_change=_live)
        self.mark_dirty()

    def _pick_grad_color2(self):
        s = self.sdata()
        def _live(c):
            s["bg_grad_color2"] = c.name()
            self.apply_scene()
        ColorWheelDialog.pick(s.get("bg_grad_color2", "#1D2430"), "Segundo color", self, on_change=_live)
        self.mark_dirty()

    def open_export(self):
        if self.tl_rec is not None:
            self.toggle_tl_rec(False)
        self.mixer.pause()
        ExportDialog(self).exec()

    def open_settings(self, start_page=0):
        SettingsDialog(self, start_page=start_page).exec()

    def apply_mic_filters(self):
        flts = self.data["settings"].get("mic_filters", [])
        for c in self.channels.values():
            c.set_filters(flts)

    def toggle_virtual_cam(self):
        if self.vcam is not None:
            try:
                self.vcam.close()
            except Exception:
                pass
            self.vcam = None
            return False
        if pyvirtualcam is None:
            return False
        try:
            w, h = [int(x) for x in self.data["settings"].get("vcam_res", "1280x720").split("x")]
            fps = int(self.data["settings"].get("vcam_fps", 30))
            self.vcam = pyvirtualcam.Camera(width=w, height=h, fps=fps, fmt=pyvirtualcam.PixelFormat.RGB)
            self._msg(f"Cámara Virtual activa: {self.vcam.device}", 4000)
            return True
        except Exception as e:
            QMessageBox.warning(self, "Cámara Virtual", str(e))
            self.vcam = None
            return False

    # =====================================================  Pistas y Timeline
    def all_tracks(self):
        if self._tracks_cache is not None:
            return self._tracks_cache
        out = []
        subtracks = self.data.get("subtracks", [])
        for ch in self.data["characters"]:
            cid = ch["id"]
            out.append({
                "id": cid, "cid": cid, "label": ch["name"], "char_name": ch["name"], "color": ch["color"],
                "is_sub": False, "arm": ch.get("arm", True), "solo": ch.get("solo", False), "mute": ch.get("mute", False),
            })
            for st in subtracks:
                if st["cid"] == cid:
                    out.append({
                        "id": st["id"], "cid": cid, "label": st["label"], "char_name": ch["name"], "color": ch["color"],
                        "is_sub": True, "arm": st.get("arm", False), "solo": st.get("solo", False), "mute": st.get("mute", False),
                    })
        self._tracks_cache = out
        self._solo_cids_cache = {t["cid"] for t in out if t.get("solo")}
        return out

    def _find_track_obj(self, tid):
        for ch in self.data["characters"]:
            if ch["id"] == tid:
                return ch, False
        for st in self.data.get("subtracks", []):
            if st["id"] == tid:
                return st, True
        return None, False

    def add_subtrack(self, cid):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None)
        if not ch:
            return
        n = sum(1 for st in self.data.get("subtracks", []) if st["cid"] == cid) + 2
        label, ok = QInputDialog.getText(self, "Nueva sub-pista", f"Sub-pista para {ch['name']}:", text=f"Pista {n}")
        if ok and label.strip():
            self.data.setdefault("subtracks", []).append(M.new_subtrack(cid, label.strip()))
            self.mark_dirty()
            self.sync_timeline()

    def reassign_subtrack(self, tid, new_cid):
        for st in self.data.get("subtracks", []):
            if st["id"] == tid:
                st["cid"] = new_cid
                for cl in self.tl_clips:
                    if cl.get("tid") == tid:
                        cl["cid"] = new_cid
                break
        self.mark_dirty()
        self.sync_timeline()

    def rename_subtrack(self, tid):
        for st in self.data.get("subtracks", []):
            if st["id"] == tid:
                name, ok = QInputDialog.getText(self, "Renombrar sub-pista", "Nombre:", text=st["label"])
                if ok and name.strip():
                    st["label"] = name.strip()
                    self.mark_dirty()
                    self.sync_timeline()
                break

    def delete_subtrack(self, tid):
        self._push_undo()
        self.data["subtracks"] = [st for st in self.data.get("subtracks", []) if st["id"] != tid]
        self.tl_clips = [c for c in self.tl_clips if c.get("tid") != tid]
        self.mark_dirty()
        self.sync_timeline()

    def on_track_arm(self, tid, v):
        obj, _ = self._find_track_obj(tid)
        if obj is not None:
            obj["arm"] = v
            self.sync_timeline()

    def on_track_solo(self, tid, v):
        obj, _ = self._find_track_obj(tid)
        if obj is not None:
            obj["solo"] = v
            self.sync_timeline()

    def on_track_mute(self, tid, v):
        obj, is_sub = self._find_track_obj(tid)
        if obj is not None:
            obj["mute"] = v
            if not is_sub and tid == self.sel:
                self.load_inspector()
            if not is_sub:
                self.refresh_cards()
            self.sync_timeline()

    def add_marker_interactive(self, t, kind):
        if kind == "scene":
            names = [f"{i + 1}. {s['name']}" for i, s in enumerate(self.data["scenes"])]
            choice, ok = QInputDialog.getItem(self, "Cambio de escena", "Escena:", names, self.cur_scene, False)
            if not ok:
                return
            idx = names.index(choice)
            mk = M.new_marker(t, "scene", f"Escena: {self.data['scenes'][idx]['name']}", value=idx)
        elif kind == "expr":
            ch = self.cur() or (self.data["characters"][0] if self.data["characters"] else None)
            if not ch:
                return
            enames = [f"{i + 1}. {e['name']}" for i, e in enumerate(ch["expressions"])]
            choice, ok = QInputDialog.getItem(self, f"Emoción de {ch['name']}", "Emoción:", enames, ch["expr"], False)
            if not ok:
                return
            idx = enames.index(choice)
            mk = M.new_marker(t, "expr", f"{ch['name']}: {ch['expressions'][idx]['name']}", target_id=ch["id"], value=idx)
        elif kind == "emote":
            ch = self.cur() or (self.data["characters"][0] if self.data["characters"] else None)
            if not ch:
                return
            items = [f"{sym} {name}" for _, sym, name, _ in M.EMOTE_CATALOG]
            choice, ok = QInputDialog.getItem(self, f"Reacción para {ch['name']}", "Reacción:", items, 0, False)
            if not ok:
                return
            k, sym, _, col = M.EMOTE_CATALOG[items.index(choice)]
            mk = M.new_marker(t, "emote", f"{sym} {ch['name']}", target_id=ch["id"], value=k, color=col)
        else:
            title, ok = QInputDialog.getText(self, "Capítulo YouTube", "Título:")
            if not ok or not title.strip():
                return
            mk = M.new_marker(t, "chapter", title.strip())
        self.data.setdefault("markers", []).append(mk)
        self.data["markers"].sort(key=lambda m: float(m.get("time", 0.0)))
        self.timeline.set_markers(self.data["markers"])
        self.mark_dirty()

    def move_marker(self, mid, new_t):
        for mk in self.data.get("markers", []):
            if mk["id"] == mid:
                mk["time"] = round(max(0.0, float(new_t)), 3)
                break
        self.data["markers"].sort(key=lambda m: float(m.get("time", 0.0)))
        self.timeline.set_markers(self.data["markers"])
        self.mark_dirty()

    def delete_marker(self, mid):
        self.data["markers"] = [m for m in self.data.get("markers", []) if m["id"] != mid]
        self.timeline.set_markers(self.data["markers"])
        self.mark_dirty()

    def cur(self):
        return next((c for c in self.data["characters"] if c["id"] == self.sel), None)

    def sdata(self):
        return self.data["scenes"][self.cur_scene]

    def placement(self, cid):
        idx = next((i for i, c in enumerate(self.data["characters"]) if c["id"] == cid), 0)
        return self.sdata()["placements"].setdefault(
            cid, M.default_placement(idx, int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H)))
        )

    def cset(self, key, value):
        ch = self.cur()
        if ch and not self._syncing:
            ch[key] = value
            self.mark_dirty()
            if ch["id"] in self.items:
                self.items[ch["id"]].update()

    def _sc_set(self, key, value):
        if not self._syncing:
            self.sdata()[key] = value
            self.mark_dirty()
            self.apply_scene()

    def pset(self, key, value):
        ch = self.cur()
        if not ch or self._syncing:
            return
        pl, it = self.placement(ch["id"]), self.items[ch["id"]]
        pl[key] = value
        it.setPos(pl["x"], pl["y"])
        it.setScale(pl["scale"])
        it.set_base_rotation(pl.get("rotation", 0.0))
        it.setVisible(pl["visible"])
        self.mark_dirty()

    def _clip_snapshot(self):
        return [
            {k: c.get(k, 0.0 if k in ("fade_in", "fade_out") else None)
             for k in ("id", "cid", "tid", "path", "name", "start", "src_duration", "trim_in", "trim_out", "gain", "fade_in", "fade_out")}
            for c in self.tl_clips
        ]

    def _push_undo(self):
        self._undo_stack.append(self._clip_snapshot())
        self._redo_stack.clear()
        if len(self._undo_stack) > 40:
            self._undo_stack.pop(0)

    def undo_timeline(self):
        if self._undo_stack:
            self._redo_stack.append(self._clip_snapshot())
            self._restore_clip_snap(self._undo_stack.pop())

    def redo_timeline(self):
        if self._redo_stack:
            self._undo_stack.append(self._clip_snapshot())
            self._restore_clip_snap(self._redo_stack.pop())

    def _restore_clip_snap(self, snap):
        by_path = {c["path"]: (c.get("samples"), c.get("peaks"), c.get("src_duration")) for c in self.tl_clips if c.get("samples") is not None}
        self.tl_clips = []
        for entry in snap:
            cl = self._make_clip_shell(entry)
            if entry["path"] in by_path:
                cl["samples"], cl["peaks"], cl["src_duration"] = by_path[entry["path"]]
                cl["duration"] = clip_effective_duration(cl)
                cl["loading"] = False
            else:
                self.audio_loader.enqueue(cl["id"], M.resolve(entry["path"]), self.mixer.sr)
            self.tl_clips.append(cl)
        self.sync_timeline()

    def _make_clip_shell(self, entry):
        path = M.resolve(entry["path"])
        src_dur = float(entry.get("src_duration", entry.get("duration", 2.0)))
        return {
            "id": entry.get("id") or uuid.uuid4().hex[:8], "cid": entry["cid"], "tid": entry.get("tid") or entry["cid"],
            "path": entry["path"], "name": entry.get("name") or Path(path).name, "start": max(0.0, float(entry.get("start", 0.0))),
            "src_duration": src_dur, "duration": clip_effective_duration(entry),
            "trim_in": max(0.0, float(entry.get("trim_in", 0.0))), "trim_out": max(0.0, float(entry.get("trim_out", 0.0))),
            "gain": float(entry.get("gain", 1.0)), "fade_in": float(entry.get("fade_in", 0.0)), "fade_out": float(entry.get("fade_out", 0.0)),
            "samples": None, "peaks": None, "loading": True, "missing": False,
        }

    def load_saved_clips_async(self):
        valid_cids = {c["id"] for c in self.data["characters"]}
        self.tl_clips = []
        for entry in self.data.get("timeline_clips", []):
            if entry.get("cid") in valid_cids:
                cl = self._make_clip_shell(entry)
                self.tl_clips.append(cl)
                self.audio_loader.enqueue(cl["id"], M.resolve(entry["path"]), self.mixer.sr)

    def _on_clip_loaded(self, clip_id, info):
        if not self._alive:
            return
        for cl in self.tl_clips:
            if cl["id"] == clip_id:
                cl["samples"], cl["peaks"], cl["src_duration"] = info["samples"], info["peaks"], info["duration"]
                cl["duration"] = clip_effective_duration(cl)
                cl["loading"] = cl["missing"] = False
                break
        self.sync_timeline()

    def _on_clip_failed(self, clip_id, err):
        if not self._alive:
            return
        for cl in self.tl_clips:
            if cl["id"] == clip_id:
                cl["loading"], cl["missing"] = False, True
                break
        self.sync_timeline()

    def _persist_clips(self):
        self.data["timeline_clips"] = [
            {
                "id": c["id"], "cid": c["cid"], "tid": c.get("tid") or c["cid"], "path": c["path"], "name": c["name"],
                "start": round(c["start"], 4), "src_duration": round(float(c.get("src_duration", c["duration"])), 4),
                "duration": round(clip_effective_duration(c), 4), "trim_in": round(float(c.get("trim_in", 0.0)), 4),
                "trim_out": round(float(c.get("trim_out", 0.0)), 4), "gain": round(float(c.get("gain", 1.0)), 3),
                "fade_in": round(float(c.get("fade_in", 0.0)), 3), "fade_out": round(float(c.get("fade_out", 0.0)), 3),
            }
            for c in self.tl_clips
        ]

    def sync_timeline(self):
        self._tracks_cache = None
        tracks = self.all_tracks()
        cids = [c["id"] for c in self.data["characters"]]
        gains = {c["id"]: c["gain"] / 100.0 for c in self.data["characters"]}
        self.mixer.set_state(cids, gains, {t["id"] for t in tracks if t.get("mute")}, {t["id"] for t in tracks if t.get("solo")})
        self.mixer.set_clips(self.tl_clips)
        self.timeline.set_tracks(tracks, self.data["characters"], self.sel)
        self.timeline.set_clips(self.tl_clips)
        self._persist_clips()

    def on_in_out_changed(self, a, b):
        self.data["in_out"] = [a, b]
        self.mixer.set_loop_range(a, b)
        self.mark_dirty()

    def on_timeline_file_dropped(self, tid, path, start):
        tr = next((t for t in self.all_tracks() if t["id"] == tid), None)
        if tr:
            self.add_timeline_clip(tr["cid"], path, start, tid=tid)

    def add_timeline_clip(self, cid, path, start, tid=None):
        self._push_undo()
        cl = self._make_clip_shell({
            "id": uuid.uuid4().hex[:8], "cid": cid, "tid": tid or cid, "path": path, "name": Path(path).name,
            "start": max(0.0, float(start)), "src_duration": 3.0, "duration": 3.0,
        })
        self.tl_clips.append(cl)
        self.timeline.canvas.sel_clip = cl["id"]
        self.mark_dirty()
        self.sync_timeline()
        self.audio_loader.enqueue(cl["id"], M.resolve(path), self.mixer.sr)

    def browse_timeline_clip(self, tid, start):
        tr = next((t for t in self.all_tracks() if t["id"] == tid), None)
        if not tr:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Añadir audio", str(M.BASE), M.AUDIO_EXTS)
        for idx, p in enumerate(paths):
            self.add_timeline_clip(tr["cid"], p, start + idx * 0.2, tid=tid)

    def edit_timeline_clip(self, clip_id, new_tid, new_start, trim_in, trim_out, gain, fade_in, fade_out):
        tr = next((t for t in self.all_tracks() if t["id"] == new_tid), None)
        self._push_undo()
        for c in self.tl_clips:
            if c["id"] == clip_id:
                c["tid"] = new_tid
                if tr:
                    c["cid"] = tr["cid"]
                c["start"], c["trim_in"], c["trim_out"] = max(0.0, float(new_start)), max(0.0, float(trim_in)), max(0.0, float(trim_out))
                c["gain"], c["fade_in"], c["fade_out"] = max(0.0, min(2.0, float(gain))), max(0.0, float(fade_in)), max(0.0, float(fade_out))
                c["duration"] = clip_effective_duration(c)
                break
        self.mark_dirty()
        self.sync_timeline()

    def split_timeline_clip(self, clip_id, split_t, all_tracks):
        targets = [c for c in self.tl_clips if (all_tracks or not clip_id or c["id"] == clip_id)
                   and (c["start"] + 0.06 < split_t < c["start"] + clip_effective_duration(c) - 0.06)]
        if not targets:
            return
        self._push_undo()
        new_clips = []
        for c in targets:
            eff = clip_effective_duration(c)
            cut_rel = split_t - c["start"]
            orig_tin, orig_tout = float(c.get("trim_in", 0.0)), float(c.get("trim_out", 0.0))
            src_dur = float(c.get("src_duration", eff))
            right = dict(c, id=uuid.uuid4().hex[:8], start=split_t, trim_in=orig_tin + cut_rel, trim_out=orig_tout, fade_in=0.0)
            right["duration"] = clip_effective_duration(right)
            c["trim_out"], c["fade_out"] = max(0.0, src_dur - (orig_tin + cut_rel)), 0.0
            c["duration"] = clip_effective_duration(c)
            new_clips.append(right)
        self.tl_clips.extend(new_clips)
        self.timeline.canvas.sel_clip = new_clips[-1]["id"]
        self.mark_dirty()
        self.sync_timeline()

    def delete_timeline_clip(self, clip_id):
        self._push_undo()
        self.tl_clips = [c for c in self.tl_clips if c["id"] != clip_id]
        self.mark_dirty()
        self.sync_timeline()

    def duplicate_timeline_clip(self, clip_id):
        src = next((c for c in self.tl_clips if c["id"] == clip_id), None)
        if src:
            self._push_undo()
            dup = dict(src, id=uuid.uuid4().hex[:8], start=src["start"] + clip_effective_duration(src) + 0.15)
            self.tl_clips.append(dup)
            self.timeline.canvas.sel_clip = dup["id"]
            self.mark_dirty()
            self.sync_timeline()

    def toggle_timeline_play(self, on):
        if on:
            self._last_play_t = self.mixer.playhead
            self.mixer.play()
        else:
            self.mixer.pause()
        self.timeline.set_playhead(self.mixer.playhead, self.mixer.playing, force=True)

    def stop_timeline(self):
        if self.tl_rec is not None:
            self.toggle_tl_rec(False)
        self.mixer.stop()
        self._last_play_t = self.mixer.playhead
        self.timeline.set_playhead(self.mixer.playhead, False, force=True)

    def seek_timeline(self, t):
        self.mixer.seek(t)
        self._last_play_t = t
        self.timeline.set_playhead(t, self.mixer.playing, force=True)

    def toggle_tl_rec(self, on):
        if on:
            armed = [t for t in self.all_tracks() if t.get("arm")]
            if not armed:
                self.timeline.set_tl_rec(None)
                QMessageBox.information(self, "Sin pistas armadas", "Activa R en las pistas que quieras grabar.")
                return
            folder = Path(self.data["settings"].get("save_dir", str(M.RECORDINGS))) / "tomas_voz"
            folder.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            start_t = self.mixer.playhead
            files, started_cids = {}, set()
            for tr in armed:
                cid = tr["cid"]
                if cid in started_cids:
                    continue
                chan = self.channels.get(cid)
                if not chan or not chan.can_record():
                    continue
                p = folder / f"{tr['label']}_{ts}.wav"
                if chan.start_rec(p):
                    files[tr["id"]] = (cid, p)
                    started_cids.add(cid)
            if not files:
                self.timeline.set_tl_rec(None)
                QMessageBox.warning(self, "Sin micrófono", "Asigna un micrófono al personaje armado en FX/Audio.")
                return
            peaks = {tid: [] for tid in files}
            self.tl_rec = {"start_t": start_t, "t0": time.time(), "files": files, "tids": set(files.keys()), "cids": started_cids, "peaks": peaks}
            self.mixer.play(recording=True)
            self.timeline.set_tl_rec((start_t, start_t, set(files.keys()), peaks))
        elif self.tl_rec is not None:
            info = self.tl_rec
            self.tl_rec = None
            self.mixer.pause()
            for cid in info["cids"]:
                if cid in self.channels:
                    self.channels[cid].stop_rec()
            self.timeline.set_tl_rec(None)
            for tid, (cid, p) in info["files"].items():
                if p.exists() and p.stat().st_size > 100:
                    self.add_timeline_clip(cid, str(p), info["start_t"], tid=tid)

    # =====================================================  Personajes
    def spawn(self, ch):
        it = CharacterItem(self, ch["id"])
        it.load(ch)
        it.set_edit(self.edit)
        self.scene.addItem(it)
        self.items[ch["id"]] = it
        self.channels[ch["id"]] = AudioChannel(self.data["settings"].get("mic_filters", []))
        self.activate_source(ch)

    def activate_source(self, ch):
        chan = self.channels[ch["id"]]
        chan.set_filters(self.data["settings"].get("mic_filters", []))
        chan.gain = ch["gain"] / 100.0
        chan.open_mic(self.find_device(ch["device"]))
        chan.gain = ch["gain"] / 100.0

    def find_device(self, name):
        if not name:
            return None
        for n, i in self.devices:
            if n == name:
                return i
        key = name[:25].lower()
        return next((i for n, i in self.devices if n.lower().startswith(key) or key.startswith(n[:25].lower())), None)

    def add_char(self):
        name, ok = QInputDialog.getText(self, "Nuevo personaje VTuber", "Nombre:")
        if not ok or not name.strip():
            return
        ch = M.new_char(name.strip(), len(self.data["characters"]))
        self.data["characters"].append(ch)
        self.spawn(ch)
        self.sel, self.sel_prop = ch["id"], None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.sync_timeline()
        self.load_inspector()
        self.tabs.setCurrentIndex(0)

    def duplicate_char(self, cid=None):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None) if cid else self.cur()
        if not ch:
            return
        dup = copy.deepcopy(ch)
        dup["id"], dup["name"] = uuid.uuid4().hex[:8], f"{ch['name']} (copia)"
        self.data["characters"].append(dup)
        pl0 = self.placement(ch["id"])
        self.sdata()["placements"][dup["id"]] = dict(pl0, x=pl0["x"] + 80, y=pl0["y"] + 20, z=pl0["z"] + 1)
        self.spawn(dup)
        self.sel, self.sel_prop = dup["id"], None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.sync_timeline()
        self.load_inspector()

    def rename_char(self, cid=None):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None) if cid else self.cur()
        if not ch:
            return
        name, ok = QInputDialog.getText(self, "Renombrar personaje", "Nombre:", text=ch["name"])
        if ok and name.strip():
            ch["name"] = name.strip()
            it = self.items[ch["id"]]
            it.label = ch["name"]
            it.update() if it.has_custom_sprites else it.load(ch)
            self.mark_dirty()
            self.refresh_cards()
            self.sync_timeline()
            self.load_inspector()

    def del_char(self, cid=None):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None) if cid else self.cur()
        if not ch or QMessageBox.question(self, "Eliminar", f"¿Eliminar a {ch['name']}?") != QMessageBox.Yes:
            return
        cid = ch["id"]
        self.channels.pop(cid).close()
        self.scene.removeItem(self.items.pop(cid))
        self.data["characters"].remove(ch)
        self.data["subtracks"] = [st for st in self.data.get("subtracks", []) if st["cid"] != cid]
        self.tl_clips = [c for c in self.tl_clips if c["cid"] != cid]
        for s in self.data["scenes"]:
            s["placements"].pop(cid, None)
        self.sel = self.data["characters"][0]["id"] if self.data["characters"] else None
        self.mark_dirty()
        self.apply_scene()
        self.refresh_cards()
        self.sync_timeline()
        self.load_inspector()

    def toggle_char_vis(self, cid, v):
        self.placement(cid)["visible"] = v
        if cid in self.items:
            self.items[cid].setVisible(v)
        self.mark_dirty()
        if cid == self.sel:
            self.load_inspector()

    def _apply_char_color_live(self, ch, col):
        hx = col.name()
        if ch.get("color") == hx:
            return
        ch["color"] = hx
        it = self.items.get(ch["id"])
        if it is not None:
            if it.has_custom_sprites:
                it.color = hx
                it.update()
            else:
                it.load(ch)
        if ch["id"] in self.cards and it is not None:
            self.cards[ch["id"]].refresh(ch, it.base_img, visible=self.placement(ch["id"]).get("visible", True))
        if ch["id"] == self.sel:
            self.load_slots(ch)
        self.sync_timeline()
        self.mark_dirty()

    def pick_char_color(self, cid=None):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None) if cid else self.cur()
        if ch:
            ColorWheelDialog.pick(ch.get("color", C["accent"]), f"Color de {ch['name']}", self,
                                  on_change=lambda c, char=ch: self._apply_char_color_live(char, c))

    def refresh_cards(self):
        while self.card_lay.count():
            w = self.card_lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.cards = {}
        for ch in self.data["characters"]:
            c = CharacterCard(ch)
            it = self.items[ch["id"]]
            c.refresh(ch, it.base_img, visible=self.placement(ch["id"]).get("visible", True))
            c.set_selected(ch["id"] == self.sel)
            c.clicked.connect(self.select_char)
            c.muteToggled.connect(self.on_track_mute)
            c.visToggled.connect(self.toggle_char_vis)
            c.renameRequested.connect(self.rename_char)
            c.duplicateRequested.connect(self.duplicate_char)
            c.deleteRequested.connect(self.del_char)
            c.colorRequested.connect(self.pick_char_color)
            self.cards[ch["id"]] = c
            self.card_lay.addWidget(c)
        if not self.data["characters"]:
            self.card_lay.addWidget(_hint("Sin personajes. Pulsa «Nuevo» o importa un VTuber .vtr."))
        self.card_lay.addStretch()

        while self.prop_lay.count():
            w = self.prop_lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.prop_cards = {}
        for pr in self.sdata().get("props", []):
            pc = PropCard(pr)
            pc.set_selected(pr["id"] == self.sel_prop)
            pc.clicked.connect(self.select_prop)
            pc.visToggled.connect(self.toggle_prop_vis)
            pc.deleteRequested.connect(self.del_prop)
            pc.renameRequested.connect(self.rename_prop)
            self.prop_cards[pr["id"]] = pc
            self.prop_lay.addWidget(pc)
        if not self.sdata().get("props"):
            self.prop_lay.addWidget(_hint("Añade logos, textos o visualizadores."))
        self.prop_lay.addStretch()

        self.view.is_empty = (not self.data["characters"] and not self.sdata().get("props"))
        self.view.viewport().update()

    def select_char(self, cid):
        self.sel, self.sel_prop = cid, None
        for k, c in self.cards.items():
            c.set_selected(k == cid)
        for c in self.prop_cards.values():
            c.set_selected(False)
        self._syncing = True
        self.scene.clearSelection()
        if self.edit and cid in self.items:
            self.items[cid].setSelected(True)
        self._syncing = False
        self.timeline.headers.selected_cid = cid
        self.timeline.headers.update()
        self.load_inspector()

    def on_canvas_selection(self):
        if self._syncing or not self._alive:
            return
        try:
            s = self.scene.selectedItems()
        except RuntimeError:
            return
        if not s:
            return
        if isinstance(s[0], CharacterItem) and s[0].cid != self.sel:
            self.select_char(s[0].cid)
        elif isinstance(s[0], PropItem) and s[0].pid != self.sel_prop:
            self.select_prop(s[0].pid)

    def _refresh_expr_pills(self, ch):
        while self.expr_pills_lay.count():
            it = self.expr_pills_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, e in enumerate(ch["expressions"][:6]):
            b = QPushButton(f"{i + 1}. {e['name']}")
            b.setObjectName("expr_pill")
            b.setCheckable(True)
            b.setChecked(i == ch["expr"])
            b.clicked.connect(lambda _, idx=i: self.set_expr(idx))
            self.expr_pills_lay.addWidget(b)
        self.expr_pills_lay.addStretch()

    def load_inspector(self):
        self._syncing = True
        pr = self._cur_prop()
        ch = self.cur()
        if pr is not None:
            self.insp_stack.setCurrentIndex(2)
            self.prop_title.setText(pr.get("name", "Capa"))
            self.p_vis_cb.setChecked(bool(pr.get("visible", True)))
            self.p_lock_cb.setChecked(bool(pr.get("locked", False)))
            self.p_sc.set(int(round(pr.get("scale", 1.0) * 100)))
            self.p_rot.set(int(round(pr.get("rotation", 0.0))))
            self.p_op.set(int(pr.get("opacity", 100)))
            self.p_z.set(int(pr.get("z", 50)))
            self.p_anim_cb.setCurrentIndex(max(0, self.p_anim_cb.findData(pr.get("anim", "none"))))
            kind = pr.get("kind", "image")
            self.prop_kind_stack.setCurrentIndex({"image": 0, "text": 1}.get(kind, 2))
            if kind == "text":
                self.pt_text.setText(pr.get("text", ""))
                self.pt_sub.setText(pr.get("subtext", ""))
                self.pt_size.set(int(pr.get("font_size", 42)))
                self.pt_w.set(int(pr.get("w", 620)))
                self.pt_bg.setChecked(bool(pr.get("bg_enabled", True)))
                self.pt_bar.setChecked(bool(pr.get("accent_bar", True)))
            elif kind == "visualizer":
                self.pv_style.setCurrentIndex(max(0, self.pv_style.findData(pr.get("style", "bars"))))
                self.pv_bars.set(int(pr.get("bars", 36)))
                self.pv_sens.set(int(pr.get("sensitivity", 120)))
                self.pv_w.set(int(pr.get("w", 560)))
                self.pv_h.set(int(pr.get("h", 110)))
            self._syncing = False
            return

        if ch is None:
            self.insp_stack.setCurrentIndex(0)
            self._syncing = False
            return

        self.insp_stack.setCurrentIndex(1)
        self.insp_title.setText(ch["name"])
        has_rig3d = isinstance(ch.get("rig3d_data"), dict)
        has_rig = isinstance(ch.get("rig_data"), dict)
        self.rig_banner.setVisible(has_rig or has_rig3d)
        if has_rig3d:
            self.rig_lbl.setText(f"VTuber 3D: {ch['rig3d_data'].get('name', 'Avatar 3D')}")
            self.rig_lbl.setStyleSheet("color:#38BDF8; font-weight:600; font-size:8.5pt;")
        elif has_rig:
            self.rig_lbl.setText(f"Rig 2.5D: {ch['rig_data'].get('name', 'VTuber')}")
            self.rig_lbl.setStyleSheet(f"color:{C['warn']}; font-weight:600; font-size:8.5pt;")

        is_adv = self.data["sprite_mode"] == "advanced"
        self.mode_seg.set_index(1 if is_adv else 0)
        for w in self.adv_widgets:
            w.setVisible(is_adv)
        self.expr_cb.blockSignals(True)
        self.expr_cb.clear()
        for i, e in enumerate(ch["expressions"]):
            self.expr_cb.addItem(f"{i + 1}. {e['name']}")
        self.expr_cb.setCurrentIndex(ch["expr"])
        self.expr_cb.blockSignals(False)
        self._refresh_expr_pills(ch)
        self.load_slots(ch)
        self.force_cb.setCurrentIndex(self.items[ch["id"]].force + 1)
        self.flip_cb.setChecked(ch["flip"])

        self.motion_cb.setCurrentIndex(max(0, self.motion_cb.findData(ch.get("motion_style", "spring"))))
        self.pivot_cb.setCurrentIndex(max(0, self.pivot_cb.findData(ch.get("pivot", "bottom"))))
        for k, sl in self.anim_sl.items():
            sl.set(ch.get(k, 0))
        self.blink_cb.setChecked(ch["blink"])
        self.s_blink.set(ch["blink_every"])
        self.s_blink_ms.set(ch.get("blink_ms", 200))
        self.dim_cb.setChecked(bool(ch.get("dim_inactive", False)))
        self.s_dim.set(int(ch.get("dim_amount", 38)))
        self.s_tzoom.set(int(ch.get("talk_zoom", 4)))
        self.s_mtrack.set(int(ch.get("mouse_track", 0)))
        self.ftrack_cb.setChecked(bool(ch.get("face_track", False)))
        self.s_fint.set(int(ch.get("face_intensity", 60)))

        self.acc_list.blockSignals(True)
        self.acc_list.clear()
        accs = ch.get("accessories", [])
        if accs and not any(a["id"] == self.sel_acc for a in accs):
            self.sel_acc = accs[0]["id"]
        elif not accs:
            self.sel_acc = None
        sel_idx = -1
        for i, a in enumerate(accs):
            self.acc_list.addItem(QListWidgetItem(f"{a['name']} ({'Delante' if a.get('z_rel', 'front') == 'front' else 'Detrás'})"))
            if a["id"] == self.sel_acc:
                sel_idx = i
        if sel_idx >= 0:
            self.acc_list.setCurrentRow(sel_idx)
        self.acc_list.blockSignals(False)
        self._syncing = False
        self._load_acc_fields()
        self._syncing = True

        self.s_op.set(int(ch.get("opacity", 100)))
        self.sh_cb.setChecked(bool(ch.get("shadow", False)))
        self.s_sh_blur.set(int(ch.get("shadow_blur", 14)))
        self.s_sh_alpha.set(int(ch.get("shadow_alpha", 45)))
        self.ol_cb.setChecked(bool(ch.get("outline", False)))
        self.ol_talk_cb.setChecked(bool(ch.get("outline_talk_only", True)))
        self.s_ol_w.set(int(ch.get("outline_width", 6)))
        self.s_br.set(int(ch.get("brightness", 0)))
        self.s_sat.set(int(ch.get("saturation", 0)))
        self.refresh_devices(keep=True)
        self.s_thr.set(ch["threshold"])
        self.s_rng.set(ch["range"])
        self.s_hold.set(ch["hold"])
        self.s_gain.set(ch["gain"])
        self.mute_cb.setChecked(ch["mute"])
        self.sync_meters()

        pl = self.placement(ch["id"])
        self.vis_cb.setChecked(pl["visible"])
        self.s_size.set(int(round(pl["scale"] * 100)))
        self.s_rot.set(int(round(pl.get("rotation", 0.0))))
        self.sp_x.setValue(int(pl["x"]))
        self.sp_y.setValue(int(pl["y"]))
        sc = self.sdata()
        self.scene_name.setText(sc["name"])
        self.bg_type_cb.setCurrentIndex(max(0, self.bg_type_cb.findData(sc.get("bg_type", "color"))))
        self.s_bg_blur.set(int(sc.get("bg_blur", 0)))
        self.s_bg_dim.set(int(sc.get("bg_dim", 0)))
        self.s_bg_vig.set(int(sc.get("bg_vignette", 0)))
        self.trans_cb.setCurrentIndex(max(0, self.trans_cb.findData(sc.get("transition", "fade"))))
        self._syncing = False

    def load_slots(self, ch):
        sp = ch["expressions"][ch["expr"]]["sprites"]
        for k, s in self.slots.items():
            s.set_path(sp.get(k, ""), ch["color"])

    def sync_meters(self):
        ch = self.cur()
        if ch:
            self.big_meter.set_params(ch["threshold"], ch["range"])
            if ch["id"] in self.cards:
                self.cards[ch["id"]].meter.set_params(ch["threshold"], ch["range"])

    def refresh_devices(self, keep=False):
        if not keep:
            self.devices = list_input_devices()
        ch = self.cur()
        self.dev_cb.blockSignals(True)
        self.dev_cb.clear()
        self.dev_cb.addItem("Sin micrófono", None)
        for name, _ in self.devices:
            self.dev_cb.addItem(name, name)
        if ch:
            i = self.dev_cb.findData(ch["device"])
            self.dev_cb.setCurrentIndex(max(0, i))
        self.dev_cb.blockSignals(False)

    def set_device(self, _):
        ch = self.cur()
        if ch and not self._syncing:
            ch["device"] = self.dev_cb.currentData()
            self.activate_source(ch)
            self.mark_dirty()
            self.refresh_cards()

    def set_gain(self, v):
        ch = self.cur()
        if ch and not self._syncing:
            ch["gain"] = v
            self.channels[ch["id"]].gain = v / 100.0
            self.mark_dirty()
            self.sync_timeline()

    def set_force(self, i):
        ch = self.cur()
        if ch and not self._syncing:
            self.items[ch["id"]].force = i - 1

    def set_flip(self, v):
        ch = self.cur()
        if ch and not self._syncing:
            ch["flip"] = v
            self.items[ch["id"]].load(ch)
            self.mark_dirty()
            self.refresh_cards()

    def set_sprite_mode(self, i):
        self.data["sprite_mode"] = "simple" if i == 0 else "advanced"
        for w in self.adv_widgets:
            w.setVisible(i == 1)

    def _reload_char_keep_center(self, ch):
        it = self.items[ch["id"]]
        old_w, old_h = it.ref_size
        it.load(ch)
        new_w, new_h = it.ref_size
        if (old_w, old_h) != (new_w, new_h):
            for s in self.data["scenes"]:
                pl = s["placements"].get(ch["id"])
                if pl is not None:
                    pl["x"] += (old_w - new_w) / 2.0
                    pl["y"] += (old_h - new_h) / 2.0
            self.apply_scene()

    def set_sprite(self, key, path):
        ch = self.cur()
        if ch:
            ch["expressions"][ch["expr"]]["sprites"][key] = M.import_asset(path) if path else ""
            self._reload_char_keep_center(ch)
            self.mark_dirty()
            self.refresh_cards()
            self.load_inspector()

    def load_many(self):
        ch = self.cur()
        if not ch:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Elegir imágenes del personaje", str(M.BASE), M.EXTS)
        if not paths:
            return
        mapping = M.auto_map(paths)
        if not mapping:
            return
        if any(k not in M.SIMPLE_KEYS for k in mapping):
            self.mode_seg.set_index(1)
            self.set_sprite_mode(1)
        sp = ch["expressions"][ch["expr"]]["sprites"]
        for key, path in mapping.items():
            sp[key] = M.import_asset(path) if path else ""
        self._reload_char_keep_center(ch)
        self.mark_dirty()
        self.refresh_cards()
        self.load_inspector()

    def set_expr(self, i, cid=None):
        ch = next((c for c in self.data["characters"] if c["id"] == cid), None) if cid else self.cur()
        if ch and not self._syncing and 0 <= i < len(ch["expressions"]):
            ch["expr"] = i
            self._reload_char_keep_center(ch)
            if ch["id"] == self.sel:
                self.load_inspector()
            self.refresh_cards()

    def add_expr(self):
        ch = self.cur()
        name, ok = QInputDialog.getText(self, "Nueva emoción", "Nombre:")
        if ch and ok and name.strip():
            ch["expressions"].append(M.new_expression(name.strip()))
            self.mark_dirty()
            self.set_expr(len(ch["expressions"]) - 1)

    def ren_expr(self):
        ch = self.cur()
        if ch:
            e = ch["expressions"][ch["expr"]]
            name, ok = QInputDialog.getText(self, "Renombrar emoción", "Nombre:", text=e["name"])
            if ok and name.strip():
                e["name"] = name.strip()
                self.mark_dirty()
                self.load_inspector()

    def del_expr(self):
        ch = self.cur()
        if ch and len(ch["expressions"]) > 1:
            del ch["expressions"][ch["expr"]]
            self.mark_dirty()
            self.set_expr(max(0, ch["expr"] - 1))

    # =====================================================  Escenas
    def apply_scene(self):
        s = self.sdata()
        cw, ch_h = int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))
        is_trans = s.get("bg_type") == "transparent"
        self.view.transparent_bg = is_trans
        bg_img = SHARED_ASSETS.render_background(s, self.data.get("bg_color", "#121215"), cw, ch_h, transparent_override=is_trans)
        self.bg_item.setPixmap(QPixmap.fromImage(bg_img))
        for ch in self.data["characters"]:
            pl, it = self.placement(ch["id"]), self.items[ch["id"]]
            it.setPos(pl["x"], pl["y"])
            it.setScale(pl["scale"])
            it.set_base_rotation(pl.get("rotation", 0.0))
            it.setVisible(pl["visible"])
            it.setZValue(pl["z"])
        for pi in self.prop_items.values():
            self.scene.removeItem(pi)
        self.prop_items = {}
        for pr in s.get("props", []):
            pi = PropItem(self, pr)
            pi.set_edit(self.edit)
            self.scene.addItem(pi)
            self.prop_items[pr["id"]] = pi
        self.view.viewport().update()

    def refresh_strip(self):
        while self.strip.count():
            w = self.strip.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.strip.addWidget(_section("Escenas"))
        for i, s in enumerate(self.data["scenes"]):
            b = QPushButton(f"F{i + 1}  {s['name']}" if i < 9 else s["name"])
            b.setObjectName("scene")
            b.setCheckable(True)
            b.setChecked(i == self.cur_scene)
            b.clicked.connect(lambda _, i=i: self.switch_scene(i))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, i=i, b=b: self.scene_menu(i, b.mapToGlobal(pos)))
            self.strip.addWidget(b)
        add = QPushButton()
        add.setObjectName("scene")
        add.setIcon(icons.icon("plus", C["text_dim"], 13))
        add.clicked.connect(self.add_scene)
        self.strip.addWidget(add)
        self.strip.addStretch()

    def scene_menu(self, i, pos):
        m = QMenu(self)
        ren = m.addAction(icons.icon("edit"), "Renombrar")
        dele = m.addAction(icons.icon("trash", C["danger"]), "Eliminar")
        a = m.exec(pos)
        if a == ren:
            self.switch_scene(i)
            self.rename_scene()
        elif a == dele:
            self.switch_scene(i)
            self.del_scene()

    def switch_scene(self, i):
        if 0 <= i < len(self.data["scenes"]) and i != self.cur_scene:
            prev_pm = QPixmap.fromImage(self.render_frame(int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))))
            self.cur_scene = i
            self.sel_prop = None
            self.apply_scene()
            self.view.start_transition(prev_pm, self.sdata().get("transition", "fade"))
            self.refresh_strip()
            self.refresh_cards()
            self.load_inspector()
        else:
            self.refresh_strip()

    def add_scene(self):
        name, ok = QInputDialog.getText(self, "Nueva escena", "Nombre:", text=f"Escena {len(self.data['scenes']) + 1}")
        if ok and name.strip():
            s = copy.deepcopy(self.sdata())
            s["name"] = name.strip()
            self.data["scenes"].append(s)
            self.cur_scene = len(self.data["scenes"]) - 1
            self.mark_dirty()
            self.refresh_strip()
            self.apply_scene()
            self.refresh_cards()
            self.load_inspector()

    def rename_scene(self):
        name, ok = QInputDialog.getText(self, "Renombrar escena", "Nombre:", text=self.sdata()["name"])
        if ok and name.strip():
            self.sdata()["name"] = name.strip()
            self.mark_dirty()
            self.refresh_strip()
            self.load_inspector()

    def del_scene(self):
        if len(self.data["scenes"]) > 1:
            del self.data["scenes"][self.cur_scene]
            self.cur_scene = 0
            self.mark_dirty()
            self.refresh_strip()
            self.apply_scene()
            self.refresh_cards()
            self.load_inspector()

    def rename_scene_inline(self):
        if self.scene_name.text().strip() and not self._syncing:
            self.sdata()["name"] = self.scene_name.text().strip()
            self.mark_dirty()
            self.refresh_strip()

    def set_background(self):
        path, _ = QFileDialog.getOpenFileName(self, "Imagen de fondo", str(M.BASE), M.EXTS)
        if path:
            self.sdata()["background"] = M.import_asset(path)
            if self.sdata().get("bg_type") == "transparent":
                self.sdata()["bg_type"] = "color"
            self.mark_dirty()
            self.apply_scene()

    def clear_background(self):
        self.sdata()["background"] = ""
        self.mark_dirty()
        self.apply_scene()

    def pick_color(self):
        def _live(c):
            self.data["bg_color"] = c.name()
            self.apply_scene()
        ColorWheelDialog.pick(self.data.get("bg_color", "#121215"), "Color de fondo", self, on_change=_live)
        self.mark_dirty()

    def sync_rotation_live(self, item):
        self.placement(item.cid)["rotation"] = round(item.base_rotation, 1)
        if item.cid == self.sel:
            self.s_rot.set(round(item.base_rotation))

    def store_item(self, item):
        pl = self.placement(item.cid)
        pl["x"], pl["y"], pl["scale"], pl["rotation"] = item.pos().x(), item.pos().y(), item.scale(), round(item.base_rotation, 1)
        self.mark_dirty()
        if item.cid == self.sel:
            self.load_inspector()

    def change_z(self, item, d):
        zs = [self.placement(c["id"])["z"] for c in self.data["characters"]]
        pl = self.placement(item.cid)
        pl["z"] = (max(zs) + 1) if d > 0 else (min(zs) - 1)
        item.setZValue(pl["z"])
        self.mark_dirty()

    def set_edit(self, on):
        self.edit = on
        for it in self.items.values():
            it.set_edit(on)
        for pi in self.prop_items.values():
            pi.set_edit(on)

    def set_guides(self, on):
        self.view.guides = on
        self.view.viewport().update()

    def show_timeline(self, on):
        if getattr(self, "_detached_docks", False):
            self.timelineVisibilityRequested.emit(bool(on))
        else:
            self.timeline.setVisible(on)
        if self.a_timeline.isChecked() != on:
            self.a_timeline.setChecked(on)

    def open_output(self):
        if self.out_win is not None:
            try:
                self.out_win.close()
            except Exception:
                pass
        self.out_win = CanvasView(self.scene, guides=False, clean=True)
        self.out_win.set_canvas_size(int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H)))
        self.out_win.setWindowFlags(Qt.Window)
        self.out_win.setWindowTitle(f"OBS — {self.data.get('project_name', 'PodcastSet')} (F11: Pantalla completa)")
        self.out_win.resize(1280, 720)
        self.out_win.show()

    def render_frame(self, w, h, transparent=False):
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent if transparent else Qt.black)
        p = QPainter(img)
        cur_bank = {ch["id"]: (self.items[ch["id"]].ref_size, self.items[ch["id"]].imgs, ch["expr"])
                    for ch in self.data["characters"] if ch["id"] in self.items}
        states = {cid: it.anim for cid, it in self.items.items()}
        render_scene_frame(
            p, w, h, self.sdata(), self.data, states, cur_bank, time.time(), SHARED_ASSETS,
            spectrum=self.mixer.master_spectrum, master_level=0.0,
            transparent=transparent or (self.sdata().get("bg_type") == "transparent"),
        )
        p.end()
        return img

    def screenshot(self):
        folder = Path(self.data["settings"].get("save_dir", str(M.RECORDINGS))) / "capturas"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"captura_{time.strftime('%Y%m%d_%H%M%S')}.png"
        self.render_frame(int(self.data.get("canvas_w", M.W)), int(self.data.get("canvas_h", M.H))).save(str(path))
        self._msg(f"Captura guardada: {path.name}", 4000)

    # =====================================================  Tick 60 FPS (solo cuando pestaña activa)
    def tick(self, dt, now):
        self.view.step_transition(dt, int(self.sdata().get("transition_ms", 280)))
        if self.mixer.playing:
            pt = self.mixer.playhead
            if pt > self._last_play_t:
                for mk in self.data.get("markers", []):
                    mt = float(mk.get("time", 0.0))
                    if self._last_play_t < mt <= pt:
                        k = mk.get("kind")
                        if k == "scene" and isinstance(mk.get("value"), int):
                            self.switch_scene(mk["value"])
                        elif k == "expr" and mk.get("target_id"):
                            self.set_expr(int(mk.get("value", 0)), cid=mk["target_id"])
                        elif k == "emote" and mk.get("target_id"):
                            self.trigger_emote(mk["target_id"], str(mk.get("value", "star")))
            self._last_play_t = pt

        solo_cids = self._solo_cids_cache
        dmap = self.data["settings"].get("discord_map", {})
        mouse_norm = self.view.mouse_norm if self.data["settings"].get("mouse_track_global", True) else (0.0, 0.0)
        face_full = self.face_tracker.get_full_pose() if self.face_tracker.running else None
        face_norm = (face_full["x"], face_full["y"], face_full["zoom"]) if face_full else (0.0, 0.0, 0.0)

        lvls, talking_cids = {}, set()
        for ch in self.data["characters"]:
            cid = ch["id"]
            if solo_cids and cid not in solo_cids:
                lvl = 0.0
            else:
                dc_lvl = self.discord_rx.level_for_char(cid, dmap) if self.discord_rx.running else 0.0
                lvl = max(self.channels[cid].level, self.mixer.level_of(cid), dc_lvl)
            lvls[cid] = lvl
            if not ch.get("mute") and lvl * 100.0 > ch["threshold"]:
                talking_cids.add(cid)

        self._ui_div = (self._ui_div + 1) % 2
        upd_ui = (self._ui_div == 0)

        for ch in self.data["characters"]:
            cid = ch["id"]
            it = self.items[cid]
            lvl = lvls[cid]
            em_st = self.channels[cid].emotion.get_state() if cid in self.channels else None
            it.update_anim(ch, lvl, now, dt=dt, mouse_norm=mouse_norm, face_norm=face_norm,
                           someone_else_talking=bool(talking_cids - {cid}),
                           emotion_state=em_st, face_full=face_full)
            if upd_ui:
                c = self.cards.get(cid)
                if c:
                    c.meter.set_level(lvl, it.state)
                    c.avatar.set_talking(it.state > 0)
                if cid == self.sel and self.insp_stack.currentIndex() == 1:
                    self.big_meter.set_level(lvl, it.state)
                    if self.tabs.currentIndex() == 0:
                        for k, sl in self.slots.items():
                            sl.set_live(k == it.cur_key)

        if self.prop_items:
            master_lvl = max(lvls.values(), default=0.0)
            spec = self.mixer.master_spectrum
            if not self.mixer.playing and self.channels:
                sp_live = None
                for ch_a in self.channels.values():
                    if ch_a._active:
                        sp_live = ch_a.spectrum if sp_live is None else np.maximum(sp_live, ch_a.spectrum)
                if sp_live is not None:
                    spec = sp_live
            for pi in self.prop_items.values():
                pi.tick(now, spec, master_lvl)

        if self.tl_rec is not None:
            el = now - self.tl_rec["t0"]
            cur_t = self.tl_rec["start_t"] + el
            target = int(el * PEAKS_HZ)
            for tid, (cid, _) in self.tl_rec["files"].items():
                arr = self.tl_rec["peaks"][tid]
                pk = max(self.channels[cid].peak, self.channels[cid].level * 0.5) if cid in self.channels else 0.0
                while len(arr) < target:
                    arr.append(pk)
            self.timeline.set_tl_rec((self.tl_rec["start_t"], cur_t, self.tl_rec["tids"], self.tl_rec["peaks"]))
            self.timeline.set_playhead(cur_t, True)
        elif self.mixer.playing or self.timeline._playing:
            self.timeline.set_playhead(self.mixer.playhead, self.mixer.playing)

        if self.vcam is not None:
            try:
                img = self.render_frame(self.vcam.width, self.vcam.height, transparent=False)
                arr = np.frombuffer(img.constBits(), np.uint8).reshape(
                    self.vcam.height, img.bytesPerLine() // 4, 4
                )[:, :self.vcam.width, :3][:, :, ::-1]
                self.vcam.send(np.ascontiguousarray(arr))
            except Exception:
                self.toggle_virtual_cam()

    # =====================================================  Guardado y cierre
    def save(self):
        if self.project_path is None:
            return self.save_as()
        self._persist_clips()
        self.project_path = M.save_project(self.data, self.project_path)
        self.dirty = False
        self.titleChanged.emit(self)
        self._msg(f"Guardado: {self.project_path.name}", 2500)
        return True

    def save_as(self):
        default = str(self.project_path or (M.BASE / f"{self.data.get('project_name', 'proyecto')}.setxd"))
        p, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto (.setxd)", default, "Proyecto PodcastSet (*.setxd)")
        if not p:
            return False
        self.project_path = Path(p)
        self.data["project_name"] = self.project_path.stem
        return self.save()

    def close_cleanup(self):
        self._alive = False
        try:
            self.scene.selectionChanged.disconnect(self.on_canvas_selection)
        except Exception:
            pass
        if self.tl_rec is not None:
            self.toggle_tl_rec(False)
        if self.vcam is not None:
            self.toggle_virtual_cam()
        self.face_tracker.stop()
        self.discord_rx.stop()
        self.mixer.close()
        for c in self.channels.values():
            c.close()
        if self.out_win:
            try:
                self.out_win.close()
            except Exception:
                pass
