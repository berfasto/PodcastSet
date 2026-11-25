"""Ventana de Configuración ('PreferencesDialog' / 'SettingsDialog') y 'ShortcutsDialog' en 'Studio UI':
- Modal frameless (min-size: 760x500), marco #preferencesFrame (bg, 1px border, radius 8px).
- Barra propia #preferencesTitleBar (36px, #14171C, arrastrable, título 9.5pt semibold, #preferencesCloseButton 26x26 hover #C42B1C).
- 2 columnas:
  - Izquierda (#settingsNavigation, 190px fijo, #14171C, borde derecho 1px border, padding 6px, seleccionado con selection_bg, selection_text y borde izquierdo 2px accent).
  - Derecha: QStackedWidget con QScrollArea sin bordes (bg, padding 14px) y QGroupBox (1px border, radius 5px, título muted).
- Selector visual de tema con tarjetas #themePresetCard (84px alto, mini-layout 84x46 dibujado con los colores exactos del tema, vista previa en vivo con debounce 32ms, guardar al aceptar y revertir al cancelar).
- Todas las preferencias reales del proyecto preservadas e integradas."""
import copy
import subprocess
import sys
import zipfile
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

import icons
import model as M
import theme
from export_engine import detect_gpu_encoder
from theme import C, DARK_STUDIO, LIGHT_STUDIO, UI_PREFS, save_ui_prefs
from widgets import ColorWheelDialog, LevelMeter, LSlider, enable_numeric_drag

BOT_SCRIPT_TEMPLATE = '''"""Bot ligero para enviar niveles de voz en vivo por usuario desde Discord a PodcastSet."""
import json, socket, sys
import numpy as np
import discord

PORT = {port}
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
bot = discord.Bot()

class LiveSink(discord.sinks.Sink):
    def write(self, data, user):
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm * pcm))) if len(pcm) else 0.0
        db = 20.0 * math.log10(max(rms, 1e-6))
        lvl = float(np.clip((db + 60.0) / 60.0, 0.0, 1.0))
        msg = json.dumps({{"user": str(user), "level": round(lvl, 3)}}).encode("utf-8")
        sock.sendto(msg, ("127.0.0.1", PORT))

@bot.slash_command(description="Conecta PodcastSet a tu canal de voz actual")
async def conectar(ctx):
    if not ctx.author.voice:
        return await ctx.respond("Entra primero a un canal de voz.")
    vc = await ctx.author.voice.channel.connect()
    vc.start_recording(LiveSink(), lambda sink, *a: None)
    await ctx.respond(f"PodcastSet escuchando voces separadas en {{ctx.author.voice.channel.name}}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    bot.run(sys.argv[1])
'''

ALL_SHORTCUTS = [
    ("ARCHIVO Y PESTAÑAS", [
        ("Nuevo proyecto / Ir a Inicio", "Ctrl + T"),
        ("Abrir proyecto (.setxd / .vtr / .vt3d)", "Ctrl + O"),
        ("Guardar pestaña activa", "Ctrl + S"),
        ("Guardar como…", "Ctrl + Shift + S"),
        ("Exportar producción", "Ctrl + E"),
        ("Cerrar pestaña actual", "Ctrl + W"),
        ("Siguiente / Anterior pestaña", "Ctrl + Tab / Ctrl + Shift + Tab"),
        ("Configuración del estudio", "Ctrl + ,"),
    ]),
    ("ESCENARIO, TRACKING Y AVATARES", [
        ("Alternar modo Editar / Seleccionar", "E"),
        ("Modo En Vivo / Presentación limpia", "F10"),
        ("Acercar / Alejar zoom", "Ctrl + Rueda"),
        ("Paneo libre de vista", "Botón central"),
        ("Cambiar escena con transición", "F1 .. F9"),
        ("Cambiar expresión del avatar activo", "Alt + 1 .. 9"),
        ("Alternar accesorios Toggle 2D / 3D", "1 .. 9"),
    ]),
    ("AUDIO Y LÍNEA DE TIEMPO", [
        ("Reproducir / Pausar línea de tiempo", "Espacio"),
        ("Grabar voz en pistas armadas (R)", "Ctrl + R"),
        ("Herramientas Selección · Cuchilla · Mano", "V  ·  C  ·  H"),
        ("Dividir clip de audio en el cursor", "Ctrl + K"),
        ("Marcar zona Inicio (In) y Fin (Out)", "I  ·  O"),
        ("Deshacer / Rehacer edición", "Ctrl + Z / Ctrl + Y"),
        ("Mostrar / Ocultar línea de tiempo", "T"),
    ]),
]


# =====================================================  Diálogo de Atajos (ShortcutsDialog)
class ShortcutsDialog(QDialog):
    """Diálogo frameless con buscador en vivo, categorías sectionLabel y botón Restablecer todo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(560, 480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        frame = QFrame()
        frame.setObjectName("preferencesFrame")
        sh = QGraphicsDropShadowEffect(frame)
        sh.setBlurRadius(24)
        sh.setOffset(0, 5)
        sh.setColor(QColor(0, 0, 0, 95))
        frame.setGraphicsEffect(sh)
        outer.addWidget(frame)

        root = QVBoxLayout(frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QFrame()
        hdr.setObjectName("preferencesTitleBar")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 0, 6, 0)
        t = QLabel("Atajos de Teclado y Hotkeys")
        t.setObjectName("preferencesDialogTitle")
        hl.addWidget(t)
        hl.addStretch()
        bx = QToolButton()
        bx.setObjectName("preferencesCloseButton")
        bx.setFixedSize(26, 26)
        bx.setIcon(icons.state_icon("close", 13))
        bx.clicked.connect(self.accept)
        hl.addWidget(bx)
        root.addWidget(hdr)

        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 10)
        body.setSpacing(10)

        srow = QHBoxLayout()
        self.search_ed = QLineEdit()
        self.search_ed.setPlaceholderText("Buscar acción o combinación de teclas…")
        self.search_ed.textChanged.connect(self._rebuild)
        srow.addWidget(self.search_ed, 1)
        body.addLayout(srow)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(2, 2, 6, 4)
        self.host_lay.setSpacing(6)
        sc.setWidget(self.host)
        body.addWidget(sc, 1)
        root.addLayout(body, 1)

        foot = QFrame()
        foot.setObjectName("modalFooter")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 8, 14, 8)
        b_rst = QPushButton("Restablecer todo")
        b_rst.setObjectName("confirmGhost")
        b_rst.clicked.connect(self._reset_all)
        fl.addWidget(b_rst)
        fl.addStretch()
        b_ok = QPushButton("Cerrar")
        b_ok.setObjectName("confirmPrimary")
        b_ok.clicked.connect(self.accept)
        fl.addWidget(b_ok)
        root.addWidget(foot)

        self._rebuild()

    def _reset_all(self):
        UI_PREFS["shortcuts"] = {}
        save_ui_prefs(UI_PREFS)
        self.search_ed.clear()
        self._rebuild()

    def _rebuild(self):
        while self.host_lay.count():
            w = self.host_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        q = self.search_ed.text().strip().lower()
        for cat_title, entries in ALL_SHORTCUTS:
            vis = [(desc, key) for desc, key in entries if not q or q in desc.lower() or q in key.lower()]
            if not vis:
                continue
            cl = QLabel(cat_title)
            cl.setObjectName("sectionLabel")
            self.host_lay.addWidget(cl)
            for desc, key in vis:
                row_w = QFrame()
                row_w.setObjectName("dspBlock")
                rl = QHBoxLayout(row_w)
                rl.setContentsMargins(10, 5, 10, 5)
                rl.setSpacing(8)
                dl = QLabel(desc)
                dl.setStyleSheet("font-size: 8.4pt;")
                kl = QLabel(key)
                kl.setObjectName("badge")
                rl.addWidget(dl, 1)
                rl.addWidget(kl)
                self.host_lay.addWidget(row_w)
        self.host_lay.addStretch()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 50:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


# =====================================================  Tarjeta Visual de Tema (#themePresetCard)
def _draw_theme_mini_layout(tokens: dict) -> QPixmap:
    """Dibuja el mini-layout de 84x46 px con los colores reales del tema."""
    dpr = 2
    w, h = 84, 46
    pm = QPixmap(w * dpr, h * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    r = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
    p.setBrush(QColor(tokens["bg"]))
    p.setPen(QPen(QColor(tokens["border"]), 1.0))
    p.drawRoundedRect(r, 4, 4)

    # Barra superior + acento
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(tokens["panel"]))
    p.drawRoundedRect(QRectF(1, 1, w - 2, 9), 3, 3)
    p.setBrush(QColor(tokens["accent"]))
    p.drawRect(QRectF(8, 8, 18, 2))

    # Dock izquierdo, viewport central, dock derecho
    p.setBrush(QColor(tokens["panel"]))
    p.drawRoundedRect(QRectF(3, 12, 16, 23), 2, 2)
    p.drawRoundedRect(QRectF(w - 21, 12, 18, 23), 2, 2)
    p.setBrush(QColor(tokens["canvas"]))
    p.drawRoundedRect(QRectF(21, 12, w - 44, 23), 2, 2)

    # Timeline inferior con onda acento
    p.setBrush(QColor(tokens["surface"]))
    p.drawRoundedRect(QRectF(3, 37, w - 6, 6), 2, 2)
    p.setBrush(QColor(tokens["accent"]))
    p.drawRoundedRect(QRectF(18, 38.5, 28, 3), 1.5, 1.5)
    p.end()
    return pm


class ThemePresetCard(QFrame):
    """Tarjeta #themePresetCard de 84px de alto con icono mini-layout 84x46 + nombre."""
    selected = Signal(str)

    def __init__(self, mode_key: str, label_txt: str, tokens: dict):
        super().__init__()
        self.mode_key = mode_key
        self.setObjectName("themePresetCard")
        self.setFixedHeight(84)
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(5)

        prev = QLabel()
        prev.setFixedSize(84, 46)
        prev.setPixmap(_draw_theme_mini_layout(tokens))
        prev.setAttribute(Qt.WA_TransparentForMouseEvents)
        v.addWidget(prev, 0, Qt.AlignHCenter)

        self.lbl = QLabel(label_txt)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("font-size: 8.2pt; font-weight: 600;")
        self.lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        v.addWidget(self.lbl)

    def set_active(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.lbl.setStyleSheet(
            f"font-size: 8.2pt; font-weight: 600; color: {C['selection_text'] if on else C['text']};"
        )
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self.mode_key)
            e.accept()
            return
        super().mousePressEvent(e)


class FilterRow(QFrame):
    selected = Signal(int)
    toggled = Signal(int, bool)

    def __init__(self, idx, flt, is_sel=False):
        super().__init__()
        self.idx = idx
        self.setObjectName("card")
        self.setProperty("sel", is_sel)
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        self.cb = QCheckBox()
        self.cb.setChecked(bool(flt.get("enabled", True)))
        self.cb.toggled.connect(lambda on: self.toggled.emit(self.idx, on))
        lbl = QLabel(flt["name"])
        lbl.setStyleSheet(
            f"font-weight:{'600' if is_sel else '500'}; "
            f"color:{C['selection_text'] if is_sel else (C['text'] if flt.get('enabled', True) else C['muted'])};"
        )
        row.addWidget(self.cb)
        row.addWidget(lbl, 1)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self.idx)
        super().mousePressEvent(e)


# =====================================================  PreferencesDialog / SettingsDialog
class SettingsDialog(QDialog):
    """Ventana de Configuración frameless con sidebar de 190px, vista previa de tema con debounce ~32ms
    y soporte tanto global como de proyecto activo."""

    def __init__(self, win=None, start_page=0, main_win=None):
        parent = main_win or (win.window() if win else None)
        super().__init__(parent)
        self.win = win            # VideoStudioWorkspace opcional
        self.main_win = main_win  # MainWindow opcional
        self._drag_pos = None
        self._dc_meters = {}
        self._sel_flt = 0

        self._orig_theme_mode = UI_PREFS.get("theme_mode", "dark")
        self._pending_theme_mode = self._orig_theme_mode
        self._theme_debounce = QTimer(self)
        self._theme_debounce.setSingleShot(True)
        self._theme_debounce.setInterval(32)
        self._theme_debounce.timeout.connect(self._apply_theme_preview_now)

        if self.win is not None:
            self._orig_settings = copy.deepcopy(self.win.data["settings"])
            self._orig_bg = self.win.data.get("bg_color", "#0E1014")
            self.st = self.win.data["settings"]
        else:
            self._orig_settings = None
            self._orig_bg = None
            self.st = M.default_settings()

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(760, 500)
        self.resize(830, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        shell = QFrame()
        shell.setObjectName("preferencesFrame")
        sh = QGraphicsDropShadowEffect(shell)
        sh.setBlurRadius(26)
        sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 95))
        shell.setGraphicsEffect(sh)
        outer.addWidget(shell)

        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barra de título propia (#preferencesTitleBar, 36px)
        hdr = QFrame()
        hdr.setObjectName("preferencesTitleBar")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 0, 6, 0)
        title = QLabel("Configuración")
        title.setObjectName("preferencesDialogTitle")
        hl.addWidget(title)
        hl.addStretch()
        self.b_close = QToolButton()
        self.b_close.setObjectName("preferencesCloseButton")
        self.b_close.setFixedSize(26, 26)
        self.b_close.setIcon(icons.state_icon("close", 13))
        self.b_close.clicked.connect(self._cancel)
        hl.addWidget(self.b_close)
        root.addWidget(hdr)

        # Cuerpo en 2 columnas: Sidebar 190px + QStackedWidget con QScrollArea
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("settingsNavigation")
        sidebar.setFixedWidth(190)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(6, 6, 6, 6)
        sl.setSpacing(3)

        self.stack = QStackedWidget()
        self.nav_grp = QButtonGroup(self)
        self.nav_btns = []

        pages = [
            ("Apariencia", "palette", self._page_appearance),
            ("Espacio de trabajo", "dock", self._page_workspace),
            ("Viewport 2D/3D", "grid", self._page_canvas),
            ("Cámara y Tracking", "webcam", self._page_tracking),
            ("Audio y DSP", "sliders", self._page_mic_filters),
            ("Discord y Craig", "mic", self._page_discord),
            ("Streaming y Salida", "monitor", self._page_obs_storage),
            ("Hotkeys y Atajos", "sparkle", self._page_shortcuts),
        ]
        for i, (label, ic, fn) in enumerate(pages):
            b = QPushButton(f"  {label}")
            b.setObjectName("navItem")
            b.setIcon(icons.state_icon(ic, 14))
            b.setIconSize(QSize(14, 14))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, idx=i: self.stack.setCurrentIndex(idx))
            self.nav_grp.addButton(b)
            self.nav_btns.append((b, ic))
            sl.addWidget(b)

            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            content_w = fn()
            content_w.layout().setContentsMargins(14, 14, 14, 14)
            sc.setWidget(content_w)
            self.stack.addWidget(sc)

        sl.addStretch()
        body.addWidget(sidebar)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # Pie inferior: Guardar y Cancelar a la derecha
        foot = QFrame()
        foot.setObjectName("modalFooter")
        foot.setFixedHeight(46)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 0, 14, 0)
        fl.addStretch()
        b_cancel = QPushButton("Cancelar")
        b_cancel.setObjectName("confirmGhost")
        b_cancel.clicked.connect(self._cancel)
        b_save = QPushButton("Guardar")
        b_save.setObjectName("primaryButton")
        b_save.clicked.connect(self._save)
        fl.addWidget(b_cancel)
        fl.addSpacing(8)
        fl.addWidget(b_save)
        root.addWidget(foot)

        # Compatibilidad con índices antiguos (ej. open_settings(1) -> Audio y DSP)
        idx = max(0, min(len(self.nav_btns) - 1, start_page))
        self.nav_btns[idx][0].setChecked(True)
        self.stack.setCurrentIndex(idx)

        self._meter_timer = QTimer(self, timeout=self._tick_meters)
        self._meter_timer.start(45)

        if parent:
            g = parent.window().frameGeometry()
            self.move(max(20, g.center().x() - self.width() // 2), max(20, g.center().y() - self.height() // 2))

    # =====================================================  1. Apariencia (Dark / Light Studio + Idioma)
    def _page_appearance(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        gb_theme = QGroupBox("Tema de Estudio (Vista previa en vivo)")
        gt = QVBoxLayout(gb_theme)
        gt.setSpacing(10)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self.card_dark = ThemePresetCard("dark", "Dark Studio", DARK_STUDIO)
        self.card_light = ThemePresetCard("light", "Light Studio", LIGHT_STUDIO)
        self.card_dark.selected.connect(self._preview_theme)
        self.card_light.selected.connect(self._preview_theme)
        cards_row.addWidget(self.card_dark, 1)
        cards_row.addWidget(self.card_light, 1)
        gt.addLayout(cards_row)
        self._sync_theme_cards(self._orig_theme_mode)
        v.addWidget(gb_theme)

        gb_lang = QGroupBox("Idioma y Densidad")
        gl = QVBoxLayout(gb_lang)
        gl.setSpacing(8)
        rl = QHBoxLayout()
        rl.addWidget(QLabel("Idioma de la interfaz"))
        self.lang_cb = QComboBox()
        self.lang_cb.addItem("Español (Estudio)", "es")
        self.lang_cb.addItem("English", "en")
        self.lang_cb.setCurrentIndex(max(0, self.lang_cb.findData(UI_PREFS.get("language", "es"))))
        rl.addWidget(self.lang_cb, 1)
        gl.addLayout(rl)

        dens_lbl = QLabel(
            f"Densidad adaptativa automática activa: {'Compacta (<=1480×860)' if theme.DENSITY['compact'] else 'Estándar (9pt)'}"
        )
        dens_lbl.setObjectName("mutedLabel")
        gl.addWidget(dens_lbl)
        v.addWidget(gb_lang)
        v.addStretch()
        return page

    def _preview_theme(self, mode_key: str):
        self._pending_theme_mode = mode_key
        self._theme_debounce.start()

    def _apply_theme_preview_now(self):
        theme.apply(QApplication.instance(), mode=self._pending_theme_mode, persist=False)
        self._sync_theme_cards(self._pending_theme_mode)
        self.b_close.setIcon(icons.state_icon("close", 13))
        for b, ic in self.nav_btns:
            b.setIcon(icons.state_icon(ic, 14))

    def _sync_theme_cards(self, mode_key: str):
        self.card_dark.set_active(mode_key == "dark")
        self.card_light.set_active(mode_key == "light")

    # =====================================================  2. Espacio de Trabajo
    def _page_workspace(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        gb = QGroupBox("Distribuciones de Paneles y Rail Lateral")
        gl = QVBoxLayout(gb)
        gl.setSpacing(8)
        desc = QLabel("Reorganiza, apila en pestañas, desacopla en monitores secundarios o absorbe cualquier panel en el Rail derecho.")
        desc.setObjectName("mutedLabel")
        desc.setWordWrap(True)
        gl.addWidget(desc)

        if self.main_win is not None:
            pr = QHBoxLayout()
            for lbl, key in (("Estudio Predeterminado", "default"), ("Compacto / Stream", "stream"), ("Edición de Audio", "audio")):
                b = QPushButton(lbl)
                b.clicked.connect(lambda _, k=key: self.main_win.apply_workspace_preset(k))
                pr.addWidget(b)
            gl.addLayout(pr)

            jr = QHBoxLayout()
            b_sv = QPushButton("Guardar distribución (.json)…")
            b_sv.clicked.connect(self.main_win._save_layout_json)
            b_ld = QPushButton("Importar distribución (.json)…")
            b_ld.clicked.connect(self.main_win._load_layout_json)
            jr.addWidget(b_sv)
            jr.addWidget(b_ld)
            gl.addLayout(jr)

        v.addWidget(gb)
        v.addStretch()
        return page

    # =====================================================  3. Viewport 2D/3D y Gráficos
    def _page_canvas(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        gb = QGroupBox("Dimensiones, Cuadrícula y Fondo del Escenario")
        gl = QVBoxLayout(gb)
        gl.setSpacing(10)

        if self.win is not None:
            r_sz = QHBoxLayout()
            r_sz.addWidget(QLabel("Resolución"))
            sp_w = QSpinBox()
            sp_w.setRange(320, 3840)
            sp_w.setValue(int(self.win.data.get("canvas_w", 1920)))
            sp_h = QSpinBox()
            sp_h.setRange(320, 2160)
            sp_h.setValue(int(self.win.data.get("canvas_h", 1080)))
            enable_numeric_drag(sp_w)
            enable_numeric_drag(sp_h)
            cb_fps = QComboBox()
            for f in (24, 30, 60):
                cb_fps.addItem(f"{f} FPS", f)
            cb_fps.setCurrentIndex(max(0, cb_fps.findData(int(self.win.data.get("fps", 30)))))
            b_apply_sz = QPushButton("Aplicar")
            b_apply_sz.clicked.connect(lambda: self.win.set_canvas_format(sp_w.value(), sp_h.value(), cb_fps.currentData()))
            r_sz.addWidget(sp_w)
            r_sz.addWidget(QLabel("×"))
            r_sz.addWidget(sp_h)
            r_sz.addWidget(cb_fps)
            r_sz.addWidget(b_apply_sz)
            gl.addLayout(r_sz)

            cb_g = QCheckBox("Mostrar guías de tercios y zona segura OBS")
            cb_g.setChecked(self.win.view.guides)
            cb_g.toggled.connect(self.win.a_guides.setChecked)
            gl.addWidget(cb_g)

            cb_s = QCheckBox("Imán a la cuadrícula")
            cb_s.setChecked(self.win.snap)
            cb_s.toggled.connect(self.win.a_snap.setChecked)
            gl.addWidget(cb_s)

            cb_sg = QCheckBox("Guías inteligentes de alineación al arrastrar")
            cb_sg.setChecked(bool(self.st.get("smart_guides", True)))
            cb_sg.toggled.connect(lambda on: self.st.__setitem__("smart_guides", on))
            gl.addWidget(cb_sg)

            r = QHBoxLayout()
            lbl_g = QLabel("Paso de cuadrícula (px)")
            sp_grid = QSpinBox()
            sp_grid.setRange(4, 200)
            sp_grid.setValue(int(self.st.get("grid_size", 20)))
            enable_numeric_drag(sp_grid, lbl_g)
            sp_grid.valueChanged.connect(lambda val: self.st.__setitem__("grid_size", val))
            r.addWidget(lbl_g)
            r.addWidget(sp_grid)
            r.addStretch()
            gl.addLayout(r)

            r_bg = QHBoxLayout()
            r_bg.addWidget(QLabel("Fondo rápido:"))
            for lbl, col in (("Estudio", "#0E1014"), ("Chroma Verde", "#00b140"), ("Chroma Azul", "#0047bb"), ("Personalizado…", "custom")):
                b = QPushButton(lbl)
                b.clicked.connect(lambda _, c=col: self._set_canvas_bg(c))
                r_bg.addWidget(b)
            gl.addLayout(r_bg)
        else:
            info = QLabel("Abre una pestaña de Escena Video / Stream para configurar su resolución, guías y croma.")
            info.setObjectName("mutedLabel")
            info.setWordWrap(True)
            gl.addWidget(info)

        v.addWidget(gb)
        v.addStretch()
        return page

    def _set_canvas_bg(self, col):
        if self.win is None:
            return
        if col == "custom":
            def _live(c):
                self.win.data["bg_color"] = c.name()
                self.win.apply_scene()
            ColorWheelDialog.pick(self.win.data.get("bg_color", "#0E1014"), "Color de fondo", self, on_change=_live)
        else:
            self.win.data["bg_color"] = col
            self.win.apply_scene()

    # =====================================================  4. Cámara y Tracking
    def _page_tracking(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        gb = QGroupBox("Seguimiento Facial por Webcam y Cursor")
        gl = QVBoxLayout(gb)
        gl.setSpacing(10)

        desc = QLabel("Rastrea cabeza X/Y, inclinación Roll Z, parpadeo y sonrisa mediante OpenCV en segundo plano.")
        desc.setObjectName("mutedLabel")
        desc.setWordWrap(True)
        gl.addWidget(desc)

        r1 = QHBoxLayout()
        lbl_c = QLabel("Índice de cámara")
        sp_cam = QSpinBox()
        sp_cam.setRange(0, 6)
        sp_cam.setValue(int(self.st.get("webcam_index", 0)))
        enable_numeric_drag(sp_cam, lbl_c)
        sp_cam.valueChanged.connect(lambda val: self.st.__setitem__("webcam_index", val))
        r1.addWidget(lbl_c)
        r1.addWidget(sp_cam)

        if self.win is not None:
            self.b_cam = QPushButton("Detener Webcam" if self.win.face_tracker.running else "Activar Webcam")
            self.b_cam.setObjectName("primaryButton")
            self.b_cam.clicked.connect(self._toggle_face_track)
            b_cal = QPushButton("Recentrar pose")
            b_cal.clicked.connect(self.win.face_tracker.recalibrate)
            r1.addWidget(self.b_cam)
            r1.addWidget(b_cal)
        r1.addStretch()
        gl.addLayout(r1)

        if self.win is not None:
            self.cam_status = QLabel(f"Estado: {self.win.face_tracker.status}")
            self.cam_status.setObjectName("mutedLabel")
            gl.addWidget(self.cam_status)
            cb_m = QCheckBox("Permitir seguimiento de cursor en personajes configurados")
            cb_m.setChecked(bool(self.st.get("mouse_track_global", True)))
            cb_m.toggled.connect(lambda on: self.st.__setitem__("mouse_track_global", on))
            gl.addWidget(cb_m)

        v.addWidget(gb)
        v.addStretch()
        return page

    def _toggle_face_track(self):
        if self.win is None:
            return
        on = self.win.toggle_face_tracking()
        self.b_cam.setText("Detener Webcam" if on else "Activar Webcam")
        self.cam_status.setText(f"Estado: {self.win.face_tracker.status}")

    # =====================================================  5. Audio y Cadena DSP
    def _page_mic_filters(self):
        page = QWidget()
        v = QVBoxLayout(page)
        gb = QGroupBox("Cadena de Efectos DSP de Micrófono (EQ, Compresor, Pitch, Reverb, Noise Gate)")
        outer = QVBoxLayout(gb)
        outer.setSpacing(10)

        m_row = QHBoxLayout()
        m_lbl = QLabel("Señal de entrada:")
        m_lbl.setObjectName("mutedLabel")
        self.fx_meter = LevelMeter(height=7)
        m_row.addWidget(m_lbl)
        m_row.addWidget(self.fx_meter, 1)
        outer.addLayout(m_row)

        gl = QHBoxLayout()
        gl.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(5)
        flt_sc = QScrollArea()
        flt_sc.setWidgetResizable(True)
        flt_sc.setFixedWidth(216)
        flt_sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._flt_host = QWidget()
        self._flt_lay = QVBoxLayout(self._flt_host)
        self._flt_lay.setContentsMargins(0, 0, 4, 0)
        self._flt_lay.setSpacing(4)
        flt_sc.setWidget(self._flt_host)
        left.addWidget(flt_sc, 1)

        br = QHBoxLayout()
        br.setSpacing(4)
        self.b_add_flt = QPushButton(" + Añadir")
        self.b_add_flt.setObjectName("primaryButton")
        self.b_add_flt.clicked.connect(self._menu_add_filter)
        b_up = QToolButton()
        b_up.setIcon(icons.state_icon("chevron_up", 13))
        b_up.setFixedSize(26, 26)
        b_up.clicked.connect(lambda: self._move_filter(-1))
        b_dn = QToolButton()
        b_dn.setIcon(icons.state_icon("chevron_down", 13))
        b_dn.setFixedSize(26, 26)
        b_dn.clicked.connect(lambda: self._move_filter(1))
        b_del = QToolButton()
        b_del.setIcon(icons.icon("trash", C["danger"], 13))
        b_del.setFixedSize(26, 26)
        b_del.clicked.connect(self._del_filter)
        for b in (self.b_add_flt, b_up, b_dn, b_del):
            br.addWidget(b)
        left.addLayout(br)
        gl.addLayout(left)

        self.param_scroll = QScrollArea()
        self.param_scroll.setWidgetResizable(True)
        self.param_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        gl.addWidget(self.param_scroll, 1)

        outer.addLayout(gl, 1)
        v.addWidget(gb, 1)
        self._refresh_filter_list(0)
        return page

    def _apply_filters_if_win(self):
        if self.win is not None:
            self.win.apply_mic_filters()

    def _refresh_filter_list(self, sel_row=None):
        flts = self.st["mic_filters"]
        if sel_row is not None:
            self._sel_flt = sel_row
        self._sel_flt = max(0, min(len(flts) - 1, self._sel_flt)) if flts else -1
        while self._flt_lay.count():
            w = self._flt_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, flt in enumerate(flts):
            row = FilterRow(i, flt, is_sel=(i == self._sel_flt))
            row.selected.connect(lambda idx: self._refresh_filter_list(idx))
            row.toggled.connect(self._toggle_filter)
            self._flt_lay.addWidget(row)
        self._flt_lay.addStretch()
        self._load_filter_params(self._sel_flt)
        self._apply_filters_if_win()

    def _toggle_filter(self, idx, on):
        if 0 <= idx < len(self.st["mic_filters"]):
            self.st["mic_filters"][idx]["enabled"] = on
            self._refresh_filter_list(self._sel_flt)

    def _menu_add_filter(self):
        m = QMenu(self)
        acts = {m.addAction(name): ftype for ftype, name, _ in M.FILTER_CATALOG}
        chosen = m.exec(self.b_add_flt.mapToGlobal(self.b_add_flt.rect().topLeft()))
        if chosen in acts:
            self.st["mic_filters"].append(M.new_filter(acts[chosen]))
            self._refresh_filter_list(len(self.st["mic_filters"]) - 1)

    def _move_filter(self, delta):
        r, nr = self._sel_flt, self._sel_flt + delta
        flts = self.st["mic_filters"]
        if 0 <= r < len(flts) and 0 <= nr < len(flts):
            flts[r], flts[nr] = flts[nr], flts[r]
            self._refresh_filter_list(nr)

    def _del_filter(self):
        r = self._sel_flt
        if 0 <= r < len(self.st["mic_filters"]):
            del self.st["mic_filters"][r]
            self._refresh_filter_list(max(0, r - 1))

    def _load_filter_params(self, row):
        panel = QFrame()
        panel.setObjectName("dspBlock")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(8)
        if not (0 <= row < len(self.st["mic_filters"])):
            lbl = QLabel("Pulsa «+ Añadir» para construir tu cadena de efectos de voz.")
            lbl.setObjectName("mutedLabel")
            pl.addWidget(lbl)
            pl.addStretch()
            self.param_scroll.setWidget(panel)
            return
        flt = self.st["mic_filters"][row]
        p, ft = flt["params"], flt["type"]
        hdr = QLabel(flt["name"])
        hdr.setObjectName("cardTitle")
        pl.addWidget(hdr)

        def add_sl(label, key, lo, hi, suf="", scale=1.0):
            s = LSlider(label, lo, hi, suf)
            s.set(int(round(p.get(key, lo) * scale)))
            s.changed.connect(lambda val, k=key, sc=scale: (p.__setitem__(k, val / sc), self._apply_filters_if_win()))
            pl.addWidget(s)

        if ft == "noise_suppress":
            add_sl("Intensidad de supresión", "strength", 0, 100, "%")
        elif ft == "noise_gate":
            add_sl("Umbral de cierre", "close_db", -70, -15, " dB")
            add_sl("Umbral de apertura", "open_db", -65, -10, " dB")
        elif ft == "highpass":
            add_sl("Frecuencia de corte", "hz", 40, 260, " Hz")
        elif ft == "compressor":
            add_sl("Umbral", "threshold_db", -40, -4, " dB")
            add_sl("Relación (×0.1)", "ratio", 10, 80, "", 10.0)
            add_sl("Ganancia de salida", "makeup_db", -6, 18, " dB")
        elif ft == "eq3":
            add_sl("Graves (<250 Hz)", "low_db", -12, 12, " dB")
            add_sl("Medios (250–3k Hz)", "mid_db", -12, 12, " dB")
            add_sl("Agudos (>3k Hz)", "high_db", -12, 12, " dB")
        elif ft == "pitch_shift":
            add_sl("Desplazamiento de tono", "semitones", -12, 12, " st")
            add_sl("Mezcla (Wet)", "mix", 0, 100, "%")
        elif ft == "reverb":
            add_sl("Tamaño de sala", "room", 10, 95, "%")
            add_sl("Mezcla Reverb", "mix", 0, 70, "%")
        elif ft == "gain":
            add_sl("Ganancia", "db", -18, 24, " dB")
        elif ft == "limiter":
            add_sl("Techo máximo", "ceiling_db", -12, 0, " dB")
        pl.addStretch()
        self.param_scroll.setWidget(panel)

    # =====================================================  6. Discord y Craig
    def _page_discord(self):
        page = QWidget()
        v = QVBoxLayout(page)
        gb = QGroupBox("Voces de Discord e Importación Multipista Craig")
        gl = QVBoxLayout(gb)
        gl.setSpacing(10)

        if self.win is None:
            lbl = QLabel("Abre una pestaña de Escena Video para asignar entradas de personajes, Bot de Discord o audios Craig.")
            lbl.setObjectName("mutedLabel")
            lbl.setWordWrap(True)
            gl.addWidget(lbl)
            v.addWidget(gb)
            v.addStretch()
            return page

        b_craig = QPushButton(" Importar grabación Craig (.zip) o audios por hablante…")
        b_craig.setObjectName("primaryButton")
        b_craig.setIcon(icons.icon("music", "#FFFFFF", 14))
        b_craig.clicked.connect(self._import_craig)
        gl.addWidget(b_craig)

        self._dc_meters = {}
        for ch in self.win.data["characters"]:
            row = QHBoxLayout()
            lbl = QLabel(ch["name"])
            lbl.setFixedWidth(95)
            lbl.setStyleSheet(f"font-weight:600; color:{ch['color']};")
            cb = QComboBox()
            cb.addItem("Sin micrófono", None)
            for name, _ in self.win.devices:
                cb.addItem(name, name)
            cb.setCurrentIndex(max(0, cb.findData(ch.get("device"))))
            cb.currentIndexChanged.connect(lambda _, c=ch, box=cb: self._assign_dev(c, box.currentData()))
            meter = LevelMeter(height=6)
            meter.setFixedWidth(90)
            meter.set_params(ch["threshold"], ch["range"])
            self._dc_meters[ch["id"]] = meter
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            row.addWidget(meter)
            gl.addLayout(row)

        r_bot = QHBoxLayout()
        self.bot_token_ed = QLineEdit(self.st.get("discord_token", ""))
        self.bot_token_ed.setEchoMode(QLineEdit.Password)
        self.bot_token_ed.setPlaceholderText("Token de Bot de Discord (opcional para vivo)")
        self.b_run_bot = QPushButton("Conectar Bot")
        self.b_run_bot.clicked.connect(self._launch_bot_direct)
        r_bot.addWidget(self.bot_token_ed, 1)
        r_bot.addWidget(self.b_run_bot)
        gl.addLayout(r_bot)

        r_map = QHBoxLayout()
        self.dc_user_ed = QLineEdit()
        self.dc_user_ed.setPlaceholderText("Usuario en Discord")
        self.dc_char_cb = QComboBox()
        for ch in self.win.data["characters"]:
            self.dc_char_cb.addItem(ch["name"], ch["id"])
        b_map = QPushButton("Asignar")
        b_map.clicked.connect(self._add_discord_map)
        r_map.addWidget(self.dc_user_ed, 1)
        r_map.addWidget(self.dc_char_cb)
        r_map.addWidget(b_map)
        gl.addLayout(r_map)

        self.dc_map_lbl = QLabel("")
        self.dc_map_lbl.setObjectName("mutedLabel")
        self._refresh_dc_map_lbl()
        gl.addWidget(self.dc_map_lbl)

        v.addWidget(gb)
        v.addStretch()
        return page

    # =====================================================  7. Streaming, OBS y Almacenamiento
    def _page_obs_storage(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        gb1 = QGroupBox("OBS Studio y Cámara Virtual")
        g1 = QVBoxLayout(gb1)
        g1.setSpacing(8)
        if self.win is not None:
            b_out = QPushButton(" Abrir Ventana Limpia OBS (F11)")
            b_out.setObjectName("primaryButton")
            b_out.setIcon(icons.icon("monitor", "#FFFFFF", 14))
            b_out.clicked.connect(lambda: (self._save(), QTimer.singleShot(60, self.win.open_output)))
            g1.addWidget(b_out, 0, Qt.AlignLeft)

            r = QHBoxLayout()
            r.addWidget(QLabel("Resolución Cámara Virtual"))
            cb = QComboBox()
            for res in ("1280x720", "1920x1080"):
                cb.addItem(res, res)
            cb.setCurrentText(self.st.get("vcam_res", "1280x720"))
            cb.currentTextChanged.connect(lambda t: self.st.__setitem__("vcam_res", t))
            r.addWidget(cb, 1)
            self.b_vcam = QPushButton("Detener Cámara Virtual" if self.win.vcam else "Iniciar Cámara Virtual")
            self.b_vcam.clicked.connect(self._toggle_vcam)
            r.addWidget(self.b_vcam)
            g1.addLayout(r)
        else:
            lbl = QLabel("Desde cualquier pestaña activa usa Ver / Salida → Ventana Limpia OBS.")
            lbl.setObjectName("mutedLabel")
            g1.addWidget(lbl)
        v.addWidget(gb1)

        gb2 = QGroupBox("Exportación, Aceleración GPU y Audio Broadcast")
        g2 = QVBoxLayout(gb2)
        g2.setSpacing(8)
        r_dir = QHBoxLayout()
        r_dir.addWidget(QLabel("Carpeta destino"))
        self.save_dir_ed = QLineEdit(self.st.get("save_dir", str(M.RECORDINGS)))
        self.save_dir_ed.textChanged.connect(lambda t: self.st.__setitem__("save_dir", t.strip() or str(M.RECORDINGS)))
        b_pick = QPushButton("Elegir…")
        b_pick.clicked.connect(self._pick_save_dir)
        r_dir.addWidget(self.save_dir_ed, 1)
        r_dir.addWidget(b_pick)
        g2.addLayout(r_dir)

        _, gpu_lbl = detect_gpu_encoder()
        g_info = QLabel(f"Codificador detectado: {gpu_lbl}")
        g_info.setObjectName("badge")
        g2.addWidget(g_info, 0, Qt.AlignLeft)

        cb_org = QCheckBox("Organizar exportaciones automáticamente en subcarpetas limpias")
        cb_org.setChecked(bool(self.st.get("export_organize", True)))
        cb_org.toggled.connect(lambda on: self.st.__setitem__("export_organize", on))
        g2.addWidget(cb_org)

        cb_norm = QCheckBox("Normalizar volumen broadcast (-16 LUFS) al exportar")
        cb_norm.setChecked(bool(self.st.get("export_normalize", True)))
        cb_norm.toggled.connect(lambda on: self.st.__setitem__("export_normalize", on))
        g2.addWidget(cb_norm)

        v.addWidget(gb2)
        v.addStretch()
        return page

    # =====================================================  8. Hotkeys y Atajos
    def _page_shortcuts(self):
        page = QWidget()
        v = QVBoxLayout(page)
        gb = QGroupBox("Atajos de Teclado y Disparadores Rápidos")
        gl = QVBoxLayout(gb)
        gl.setSpacing(10)

        desc = QLabel("Consulta y filtra todos los atajos de edición, transporte de audio, escenas y expresiones VTuber.")
        desc.setObjectName("mutedLabel")
        desc.setWordWrap(True)
        gl.addWidget(desc)

        b_open_hk = QPushButton(" Abrir catálogo interactivo de Atajos y Hotkeys…")
        b_open_hk.setObjectName("primaryButton")
        b_open_hk.setIcon(icons.icon("sparkle", "#FFFFFF", 14))
        b_open_hk.clicked.connect(lambda: ShortcutsDialog(self).exec())
        gl.addWidget(b_open_hk, 0, Qt.AlignLeft)

        for cat_title, entries in ALL_SHORTCUTS:
            cl = QLabel(cat_title)
            cl.setObjectName("sectionLabel")
            gl.addWidget(cl)
            for d_txt, k_txt in entries[:4]:
                r = QHBoxLayout()
                lk = QLabel(k_txt)
                lk.setObjectName("badge")
                lk.setFixedWidth(150)
                r.addWidget(lk)
                r.addWidget(QLabel(d_txt), 1)
                gl.addLayout(r)

        v.addWidget(gb)
        v.addStretch()
        return page

    # =====================================================  Auxiliares Discord / Craig / Guardado
    def _tick_meters(self):
        if self.win is None:
            return
        for cid, m in self._dc_meters.items():
            if cid in self.win.channels and cid in self.win.items:
                m.set_level(self.win.channels[cid].level, self.win.items[cid].state)
        if hasattr(self, "fx_meter") and self.win.sel in self.win.channels:
            ch = self.win.cur()
            if ch:
                self.fx_meter.set_params(ch["threshold"], ch["range"])
                self.fx_meter.set_level(self.win.channels[ch["id"]].level, self.win.items[ch["id"]].state)
        if hasattr(self, "cam_status"):
            self.cam_status.setText(f"Estado: {self.win.face_tracker.status}")

    def _assign_dev(self, ch, dev):
        ch["device"] = dev
        self.win.activate_source(ch)
        self.win.refresh_cards()
        if ch["id"] == self.win.sel:
            self.win.load_inspector()

    def _launch_bot_direct(self):
        tok = self.bot_token_ed.text().strip()
        if not tok:
            QMessageBox.information(self, "Falta Token", "Pega el Token de tu bot de Discord primero.")
            return
        self.st["discord_token"] = tok
        self.st["discord_live"] = True
        port = int(self.st.get("discord_port", 8765))
        self.win.discord_rx.start(port)
        bot_file = M.BASE / "discord_bot.py"
        bot_file.write_text(BOT_SCRIPT_TEMPLATE.format(port=port), encoding="utf-8")
        try:
            subprocess.Popen([sys.executable, str(bot_file), tok],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.b_run_bot.setText("● Bot activo")
            QMessageBox.information(self, "Bot iniciado", "En tu canal de voz de Discord escribe: /conectar")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _add_discord_map(self):
        u = self.dc_user_ed.text().strip()
        cid = self.dc_char_cb.currentData()
        if u and cid:
            self.st.setdefault("discord_map", {})[u] = cid
            self.dc_user_ed.clear()
            self._refresh_dc_map_lbl()

    def _refresh_dc_map_lbl(self):
        dm = self.st.get("discord_map", {})
        if not dm or self.win is None:
            self.dc_map_lbl.setText("")
            return
        cmap = {c["id"]: c["name"] for c in self.win.data["characters"]}
        self.dc_map_lbl.setText("Asignados: " + " · ".join(f"{u} → {cmap.get(c, '?')}" for u, c in dm.items()))

    def _import_craig(self):
        if self.win is None or not self.win.data["characters"]:
            QMessageBox.information(self, "Sin personajes", "Crea primero al menos un personaje para asignar las voces.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Elegir grabación de Craig (.zip) o audios", str(M.BASE),
            "Discord / Craig (*.zip *.wav *.flac *.ogg *.mp3)",
        )
        if not paths:
            return
        audio_files = []
        for p in paths:
            pp = Path(p)
            if pp.suffix.lower() == ".zip":
                out_dir = Path(self.st.get("save_dir", str(M.RECORDINGS))) / f"discord_{pp.stem}"
                out_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(pp, "r") as zf:
                    for name in zf.namelist():
                        if Path(name).suffix.lower() in M.AUDIO_SUFFIXES:
                            target = out_dir / Path(name).name
                            target.write_bytes(zf.read(name))
                            audio_files.append(target)
            elif pp.suffix.lower() in M.AUDIO_SUFFIXES:
                audio_files.append(pp)
        if not audio_files:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Asignar cada voz a su personaje")
        dv = QVBoxLayout(dlg)
        combos = []
        chars = self.win.data["characters"]
        for i, af in enumerate(audio_files):
            r = QHBoxLayout()
            cb = QComboBox()
            best_idx = i % len(chars)
            low_name = af.stem.lower()
            for j, ch in enumerate(chars):
                cb.addItem(ch["name"], ch["id"])
                if ch["name"].lower() in low_name:
                    best_idx = j
            cb.setCurrentIndex(best_idx)
            r.addWidget(QLabel(af.name), 1)
            r.addWidget(cb)
            dv.addLayout(r)
            combos.append((af, cb))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dv.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            for af, cb in combos:
                self.win.add_timeline_clip(cb.currentData(), str(af), 0.0, tid=cb.currentData())

    def _toggle_vcam(self):
        if self.win is not None:
            on = self.win.toggle_virtual_cam()
            self.b_vcam.setText("Detener Cámara Virtual" if on else "Iniciar Cámara Virtual")

    def _pick_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de exportaciones", self.save_dir_ed.text() or str(M.RECORDINGS))
        if d:
            self.save_dir_ed.setText(d)

    def _save(self):
        self._meter_timer.stop()
        self._theme_debounce.stop()
        UI_PREFS["theme_mode"] = self._pending_theme_mode
        UI_PREFS["language"] = self.lang_cb.currentData()
        save_ui_prefs(UI_PREFS)
        theme.apply(QApplication.instance(), mode=self._pending_theme_mode, persist=True)
        if self.win is not None:
            self.win.apply_mic_filters()
            self.win.mark_dirty()
        self.accept()

    def _cancel(self):
        self._meter_timer.stop()
        self._theme_debounce.stop()
        if self._pending_theme_mode != self._orig_theme_mode:
            theme.apply(QApplication.instance(), mode=self._orig_theme_mode, persist=False)
        if self.win is not None and self._orig_settings is not None:
            self.win.data["settings"] = self._orig_settings
            self.win.data["bg_color"] = self._orig_bg
            self.win.apply_scene()
            self.win.apply_mic_filters()
        self.reject()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 50:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


# Alias exacto según especificación
PreferencesDialog = SettingsDialog
