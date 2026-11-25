"""Ventana principal Frameless 'Studio UI' (MainWindow):
- FramelessWindowHint + redimensionamiento nativo Win32 (bordes 5px, esquinas 14px, WM_NCCALCSIZE, WM_NCHITTEST,
  HTMAXBUTTON para Windows 11 Snap Layouts y HTCAPTION con Aero Snap).
- Encabezado #chrome de 3 franjas exactas sin huecos:
  1. #titleBar (35px / 30px compacto): icono 17x17 + QMenuBar embebido (8 menús completos) + arrastre + WindowControl 46x35 dibujados a mano (1px).
  2. #toolbarRow: botones 26x26 (24x24 compacto), iconos 17x17, separadores 1x15.
  3. #tabStrip (34px): QTabBar movible/cerrable con Pestaña 0 Inicio fija (casa 15x15), indicador •, #tabClose 17x17 y #tabAddButton 26x24.
- Ecosistema QDockWidget (4 lados + pestañas + flotantes) + PanelRail (30px) + RailFlyout + #dockDropGuide + CanvasDock (estilo Figma).
- Guardado/restauración automática al visitar Inicio, Modo En Vivo y exportación/importación de distribuciones .json."""
import ctypes
import ctypes.wintypes
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QElapsedTimer, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMenuBar, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QStackedWidget, QTabBar,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

import icons
import model as M
import theme
import vtuber3d_engine as V3
import vtuber_rig as VR
from audio_rack import AudioMixerRackPanel, TrackingMonitorPanel, play_soundboard_sfx
from color_studio import ColorPalettePanel, ColorPickerPanel
from dock_system import CanvasDock, PanelRail, RailFlyout, StudioDockWidget, TearOffDragController
from settings_dialog import SettingsDialog, ShortcutsDialog
from start_hub import StartHubWidget
from theme import C, DENSITY, UI_PREFS, save_ui_prefs
from video_studio import VideoStudioWorkspace
from vtuber3d_studio import VTuber3DWorkspace
from vtuber_studio import VTuberStudioWorkspace
from widgets import LayersPanel, Section, ToolPalette


# =====================================================  Diálogo de Confirmación Studio (UnsavedChangesDialog)
class UnsavedChangesDialog(QDialog):
    """Tarjeta modal frameless translúcida y arrastrable con sombra (blur 24px, offset 0,5, rgba(0,0,0,90))."""
    SAVE = 1
    DISCARD = 2
    CANCEL = 0

    def __init__(self, doc_title: str, parent=None):
        super().__init__(parent)
        self.choice = self.CANCEL
        self._drag_pos = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        frame = QFrame()
        frame.setObjectName("confirmFrame")
        sh = QGraphicsDropShadowEffect(frame)
        sh.setBlurRadius(24)
        sh.setOffset(0, 5)
        sh.setColor(QColor(0, 0, 0, 90))
        frame.setGraphicsEffect(sh)
        outer.addWidget(frame)

        v = QVBoxLayout(frame)
        v.setContentsMargins(16, 12, 14, 14)
        v.setSpacing(10)

        hdr = QHBoxLayout()
        t = QLabel("Cambios sin guardar")
        t.setObjectName("confirmTitle")
        hdr.addWidget(t)
        hdr.addStretch()
        bx = QToolButton()
        bx.setObjectName("preferencesCloseButton")
        bx.setFixedSize(24, 24)
        bx.setIcon(icons.state_icon("close", 12))
        bx.clicked.connect(self.reject)
        hdr.addWidget(bx)
        v.addLayout(hdr)

        msg = QLabel(f"¿Deseas guardar los cambios en «{doc_title}» antes de cerrar la pestaña?")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {C['dim']}; font-size: 8.5pt;")
        v.addWidget(msg)

        v.addSpacing(4)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        b_no = QPushButton("No guardar")
        b_no.setObjectName("confirmGhost")
        b_no.clicked.connect(lambda: self._done(self.DISCARD))
        b_can = QPushButton("Cancelar")
        b_can.setObjectName("confirmGhost")
        b_can.clicked.connect(self.reject)
        b_save = QPushButton("Guardar")
        b_save.setObjectName("confirmPrimary")
        b_save.setDefault(True)
        b_save.clicked.connect(lambda: self._done(self.SAVE))
        btns.addStretch()
        btns.addWidget(b_no)
        btns.addWidget(b_can)
        btns.addWidget(b_save)
        v.addLayout(btns)

        self.adjustSize()
        if parent:
            g = parent.window().frameGeometry()
            self.move(max(20, g.center().x() - self.width() // 2), max(20, g.center().y() - self.height() // 2))

    def _done(self, code: int):
        self.choice = code
        self.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 48:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


# =====================================================  Botones Vectoriales de Ventana (46x35, trazo 1px)
class WindowControl(QWidget):
    """Botón de ventana dibujado a mano con trazo fino de 1px (min, max/restore, close)."""
    clicked = Signal()

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind  # "min" | "max" | "close"
        self._hovered = False
        self._pressed = False
        self.setFixedSize(46, DENSITY["title_h"])
        self.setMouseTracking(True)

    def set_nc_hover(self, on: bool):
        if self._hovered != on:
            self._hovered = on
            self.update()

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit()
            e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        if self._hovered or self._pressed:
            if self.kind == "close":
                p.fillRect(r, QColor("#E81123"))
            else:
                p.fillRect(r, QColor(C["hover"]))

        cx, cy = r.width() // 2, r.height() // 2
        if self.kind == "close" and (self._hovered or self._pressed):
            fg = QColor("#FFFFFF")
        elif self._hovered:
            fg = QColor(C["text"])
        else:
            fg = QColor(C["dim"])

        if self.kind == "min":
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(QPen(fg, 1.0))
            p.drawLine(cx - 5, cy + 3, cx + 3, cy + 3)

        elif self.kind == "max":
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(QPen(fg, 1.0))
            p.setBrush(Qt.NoBrush)
            is_max = self.window().isMaximized()
            if not is_max:
                # Cuadrado nítido de 9x9 px (cx-5, cy-5, ancho/alto 8 en QPainter entero = 9px inclusivo)
                p.drawRect(cx - 5, cy - 5, 8, 8)
            else:
                # Restaurar: cuadrado frontal 7x7 en (cx-5, cy-2) + marco trasero hasta (cx+4, cy-5)
                p.drawRect(cx - 5, cy - 2, 6, 6)
                p.drawLine(cx - 3, cy - 2, cx - 3, cy - 5)
                p.drawLine(cx - 3, cy - 5, cx + 3, cy - 5)
                p.drawLine(cx + 3, cy - 5, cx + 3, cy + 1)
                p.drawLine(cx + 3, cy + 1, cx + 1, cy + 1)

        else:  # "close" X de 10x10 px con antialiasing
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(fg, 1.15, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
            p.drawLine(QPointF(cx + 4.5, cy - 4.5), QPointF(cx - 4.5, cy + 4.5))


# =====================================================  Paneles Rápidos Secundarios del Rail
class _EmotesQuickPanel(QWidget):
    """Panel rápido del Rail para Expresiones, 7 Emociones Vocales, Emotes y Hotkeys 1..9."""

    def __init__(self, main_win):
        super().__init__()
        self.win = main_win
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        s1 = Section("Reacciones en Vivo (Emotes)", True)
        for k, sym, nm, col in M.EMOTE_CATALOG:
            b = QPushButton(f" {sym}   {nm}")
            b.setFixedHeight(26)
            b.setStyleSheet("text-align: left; padding-left: 8px;")
            b.clicked.connect(lambda _, ek=k: self.win._trigger_active_emote(ek))
            s1.content.addWidget(b)
        v.addWidget(s1)

        s2 = Section("Atajos de Escena y Expresión", True)
        lbl = QLabel("• F1 .. F9: Cambiar escena con fundido\n• Alt + 1 .. 9: Cambiar expresión\n• 1 .. 9: Alternar accesorios Toggle")
        lbl.setObjectName("mutedLabel")
        lbl.setWordWrap(True)
        s2.content.addWidget(lbl)
        b_hk = QPushButton(" Ver todos los atajos…")
        b_hk.setIcon(icons.state_icon("sparkle", 14))
        b_hk.clicked.connect(self.win.open_shortcuts_dialog)
        s2.content.addWidget(b_hk)
        v.addWidget(s2)
        v.addStretch()


class _OutputQuickPanel(QWidget):
    """Panel rápido del Rail para Streaming OBS, Cámara Virtual, Captura y Exportación."""

    def __init__(self, main_win):
        super().__init__()
        self.win = main_win
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        s1 = Section("Salida en Directo y OBS", True)
        b_obs = QPushButton(" Abrir Ventana Limpia OBS (F11)")
        b_obs.setObjectName("primaryButton")
        b_obs.setIcon(icons.icon("monitor", "#FFFFFF", 14))
        b_obs.clicked.connect(self.win._open_obs_active)
        s1.content.addWidget(b_obs)

        self.b_vcam = QPushButton(" Iniciar / Detener Cámara Virtual")
        self.b_vcam.setIcon(icons.state_icon("video", 14))
        self.b_vcam.clicked.connect(self.win._toggle_vcam_active)
        s1.content.addWidget(self.b_vcam)

        b_chroma = QPushButton(" Alternar Chroma Key / Alfa")
        b_chroma.setIcon(icons.state_icon("alpha", 14))
        b_chroma.clicked.connect(self.win._toggle_chroma_active)
        s1.content.addWidget(b_chroma)
        v.addWidget(s1)

        s2 = Section("Captura y Exportación", True)
        b_shot = QPushButton(" Guardar Captura PNG")
        b_shot.setIcon(icons.state_icon("camera", 14))
        b_shot.clicked.connect(self.win._screenshot_active)
        b_exp = QPushButton(" Exportar Video / Audio / Sprites…")
        b_exp.setIcon(icons.state_icon("export", 14))
        b_exp.clicked.connect(self.win.export_active)
        s2.content.addWidget(b_shot)
        s2.content.addWidget(b_exp)
        v.addWidget(s2)
        v.addStretch()


# =====================================================  Contenedor Central del Viewport + Overlays
class _ViewportStageContainer(QWidget):
    """Aloja el stack de viewports a la izquierda, PanelRail (30px) a la derecha, y superpone RailFlyout + CanvasDock."""
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background: {C['canvas']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.viewport_stack = QStackedWidget()
        lay.addWidget(self.viewport_stack, 1)

        self.rail = PanelRail(self)
        lay.addWidget(self.rail)

        self.flyout = RailFlyout(self)
        self.canvas_dock = CanvasDock(self)

        theme.bus.changed.connect(lambda _: self.setStyleSheet(f"background: {C['canvas']};"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        rw = self.rail.width() if self.rail.isVisible() else 0
        if self.flyout.isVisible():
            self.flyout.reposition(rw)
        if self.canvas_dock.isVisible():
            self.canvas_dock.reposition(rw)
        self.resized.emit()


# =====================================================  Barra de Pestañas Studio (#tabStrip)
class _StudioTabBar(QTabBar):
    """QTabBar sin base, movible, con Pestaña 0 (Inicio) bloqueada en el índice 0."""

    def __init__(self):
        super().__init__()
        self.setObjectName("projectTabBar")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setMovable(True)
        self.setTabsClosable(False)  # Usamos botón custom #tabClose de 17x17
        self.setElideMode(Qt.ElideRight)
        self.setUsesScrollButtons(True)

    def mousePressEvent(self, e):
        idx = self.tabAt(e.position().toPoint())
        if e.button() == Qt.MiddleButton and idx > 0:
            self.tabCloseRequested.emit(idx)
            e.accept()
            return
        # Impedir arrastrar la pestaña 0 (Inicio)
        self.setMovable(idx > 0)
        super().mousePressEvent(e)


# =====================================================  MainWindow Principal
class MainWindow(QMainWindow):
    """Suite VTuber 2D/3D + Audio Multipista en arquitectura Studio UI."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint | Qt.WindowSystemMenuHint
        )
        self.resize(1540, 910)
        self.setMinimumSize(980, 620)
        self.setWindowTitle("PodcastSet Studio")

        # Opciones de QDockWidget de estudio profesional
        self.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        self.setTabPosition(Qt.AllDockWidgetAreas, QTabWidget.North)

        self.workspaces = []          # [0 = StartHubWidget, 1..N = workspaces]
        self._ws_panel_map = {}       # id(ws) -> (left_w, center_w, right_w, bottom_w)
        self._active_idx = 0
        self._live_presentation = False
        self._viewport_light = not bool(UI_PREFS.get("viewport_dark", True))
        self._saved_dock_vis = None
        self._right_collapsed_mem = None
        self._drag_fallback_pos = None
        self._fps_acc = 0.0
        self._fps_cnt = 0

        self.docks = {}               # panel_id -> StudioDockWidget
        self._rail_ids = list(UI_PREFS.get("rail_panels", ["color", "palette", "tracking", "audio_fx", "emotes", "output"]))

        self._build_chrome()
        self._build_central_and_docks()
        self._build_menus()
        self._build_statusbar()
        self._setup_shortcuts()

        # Activar Inicio (oculta docks/rail automáticamente recordando la configuración inicial)
        self._rebuild_tab_bar()
        self.switch_tab(0)

        theme.bus.changed.connect(self._on_theme_changed)

        # Reloj maestro único a 60 FPS
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_ms = 0
        self.timer = QTimer(self, timeout=self._tick)
        self.timer.start(16)

    # =====================================================  Frameless Nativo Win32 (WM_NCCALCSIZE / WM_NCHITTEST / Snap)
    def init_native_frameless(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            dwmapi = ctypes.windll.dwmapi
            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_SYSMENU = 0x00080000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(
                hwnd, GWL_STYLE,
                style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU,
            )

            class MARGINS(ctypes.Structure):
                _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                            ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

            m = MARGINS(1, 1, 1, 1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            dark_v = ctypes.c_int(1 if theme.is_dark() else 0)
            dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_v), ctypes.sizeof(dark_v))
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                WM_NCCALCSIZE = 0x0083
                WM_NCHITTEST = 0x0084
                WM_NCLBUTTONDOWN = 0x00A1
                WM_NCLBUTTONUP = 0x00A2
                WM_NCMOUSELEAVE = 0x02A2

                if msg.message == WM_NCCALCSIZE:
                    if msg.wParam:
                        return True, 0

                elif msg.message == WM_NCHITTEST:
                    gp = QCursor.pos()
                    lp = self.mapFromGlobal(gp)
                    w, h = self.width(), self.height()
                    maximized = self.isMaximized() or self.isFullScreen()

                    if not maximized:
                        edge = 5
                        corner = 14
                        on_l = 0 <= lp.x() < edge
                        on_r = w - edge <= lp.x() <= w
                        on_t = 0 <= lp.y() < edge
                        on_b = h - edge <= lp.y() <= h
                        if lp.y() < corner and lp.x() < corner and (on_t or on_l):
                            return True, 13  # HTTOPLEFT
                        if lp.y() < corner and lp.x() >= w - corner and (on_t or on_r):
                            return True, 14  # HTTOPRIGHT
                        if lp.y() >= h - corner and lp.x() < corner and (on_b or on_l):
                            return True, 16  # HTBOTTOMLEFT
                        if lp.y() >= h - corner and lp.x() >= w - corner and (on_b or on_r):
                            return True, 17  # HTBOTTOMRIGHT
                        if on_l:
                            return True, 10  # HTLEFT
                        if on_r:
                            return True, 11  # HTRIGHT
                        if on_t:
                            return True, 12  # HTTOP
                        if on_b:
                            return True, 15  # HTBOTTOM

                    if hasattr(self, "title_bar") and self.title_bar.isVisible():
                        tb_pos = self.title_bar.mapFromGlobal(gp)
                        if self.title_bar.rect().contains(tb_pos):
                            # Botón maximizar: HTMAXBUTTON (9) habilita Snap Layouts en Windows 11
                            max_pos = self.btn_max.mapFromGlobal(gp)
                            if self.btn_max.rect().contains(max_pos) and not maximized:
                                self.btn_max.set_nc_hover(True)
                                return True, 9  # HTMAXBUTTON
                            self.btn_max.set_nc_hover(False)

                            for w_ctrl in (self.btn_min, self.btn_max, self.btn_close):
                                if w_ctrl.rect().contains(w_ctrl.mapFromGlobal(gp)):
                                    return True, 1  # HTCLIENT

                            mb_pos = self.menu_bar.mapFromGlobal(gp)
                            if self.menu_bar.rect().contains(mb_pos) and self.menu_bar.actionAt(mb_pos) is not None:
                                return True, 1  # HTCLIENT

                            return True, 2  # HTCAPTION

                    if hasattr(self, "btn_max"):
                        self.btn_max.set_nc_hover(False)

                elif msg.message == WM_NCLBUTTONDOWN and msg.wParam == 9:
                    self._toggle_max_restore()
                    return True, 0
                elif msg.message == WM_NCLBUTTONUP and msg.wParam == 9:
                    return True, 0
                elif msg.message == WM_NCMOUSELEAVE and hasattr(self, "btn_max"):
                    self.btn_max.set_nc_hover(False)
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and hasattr(self, "chrome_lay"):
            if self.isMaximized() and sys.platform == "win32":
                pad = 7
                self.setContentsMargins(pad, pad, pad, pad)
            else:
                self.setContentsMargins(0, 0, 0, 0)
            if hasattr(self, "btn_max"):
                self.btn_max.update()
        super().changeEvent(e)

    def _toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.btn_max.update()

    # =====================================================  Chrome Superior (3 Franjas Exactas)
    def _build_chrome(self):
        self.chrome = QWidget(self)
        self.chrome.setObjectName("chrome")
        self.chrome_lay = QVBoxLayout(self.chrome)
        self.chrome_lay.setContentsMargins(0, 0, 0, 0)
        self.chrome_lay.setSpacing(0)

        # --- Franja 1: Barra de Título Integrada (#titleBar) ---
        self.title_bar = QWidget()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(DENSITY["title_h"] + 1)
        self.title_bar.installEventFilter(self)
        tb = QHBoxLayout(self.title_bar)
        tb.setContentsMargins(12, 0, 0, 1)
        tb.setSpacing(0)

        self.app_icon_lbl = QLabel()
        self.app_icon_lbl.setFixedSize(17, 17)
        self.app_icon_lbl.setPixmap(icons.pixmap("app_logo", C["dim"], 17))
        tb.addWidget(self.app_icon_lbl, 0, Qt.AlignVCenter)
        tb.addSpacing(8)

        self.menu_bar = QMenuBar(self.title_bar)
        self.menu_bar.setNativeMenuBar(False)
        tb.addWidget(self.menu_bar, 0, Qt.AlignVCenter)

        tb.addStretch(1)

        self.doc_title_hint = QLabel("PodcastSet Studio")
        self.doc_title_hint.setObjectName("mutedLabel")
        self.doc_title_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        tb.addWidget(self.doc_title_hint, 0, Qt.AlignVCenter)
        tb.addStretch(1)

        self.btn_min = WindowControl("min", self.title_bar)
        self.btn_max = WindowControl("max", self.title_bar)
        self.btn_close = WindowControl("close", self.title_bar)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        self.btn_close.clicked.connect(self.close)
        tb.addWidget(self.btn_min)
        tb.addWidget(self.btn_max)
        tb.addWidget(self.btn_close)
        self.chrome_lay.addWidget(self.title_bar)

        # --- Franja 2: Barra de Herramientas Rápida (#toolbarRow) ---
        self.toolbar_row = QWidget()
        self.toolbar_row.setObjectName("toolbarRow")
        tr = QHBoxLayout(self.toolbar_row)
        tr.setContentsMargins(11, 3, 11, 3)
        tr.setSpacing(3)

        self._tb_btns = {}

        def add_tb(key, ic_name, tip, cb, checkable=False):
            b = QToolButton()
            b.setObjectName("toolbarButton")
            sz = DENSITY["tool_btn"]
            b.setFixedSize(sz, sz)
            b.setIconSize(QSize(17, 17))
            b.setIcon(icons.state_icon(ic_name, 17))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor)
            if checkable:
                b.toggled.connect(cb)
            else:
                b.clicked.connect(cb)
            self._tb_btns[key] = (b, ic_name)
            tr.addWidget(b)
            return b

        def add_sep():
            tr.addSpacing(4)
            s = QFrame()
            s.setObjectName("toolSeparator")
            s.setFixedSize(1, 15)
            tr.addWidget(s, 0, Qt.AlignVCenter)
            tr.addSpacing(4)

        add_tb("new", "file_new", "Nuevo proyecto / Ir a Inicio (Ctrl+T)", lambda: self.switch_tab(0))
        add_tb("open", "folder_open", "Abrir proyecto (.setxd / .vtr / .vt3d) (Ctrl+O)", self.open_project_dialog)
        add_tb("save", "save", "Guardar pestaña activa (Ctrl+S)", self.save_active)
        add_sep()
        add_tb("undo", "undo", "Deshacer (Ctrl+Z)", lambda: self._call_video("undo_timeline"))
        add_tb("redo", "redo", "Rehacer (Ctrl+Y)", lambda: self._call_video("redo_timeline"))
        add_sep()
        self.tb_track = add_tb("track", "webcam", "Activar / Desactivar Tracking por Cámara", self._on_tb_tracking, checkable=True)
        add_tb("calib", "calibrate", "Calibrar pose neutral y voz", self._calibrate_active)
        self.tb_mic = add_tb("mic", "mic", "Micrófono / Audio en vivo ON/OFF", self._on_tb_mic, checkable=True)
        self.tb_mic.blockSignals(True)
        self.tb_mic.setChecked(True)
        self.tb_mic.blockSignals(False)
        add_sep()
        add_tb("zout", "zoom_out", "Alejar vista", lambda: self._zoom_active(0.85))
        add_tb("zin", "zoom_in", "Acercar vista", lambda: self._zoom_active(1.18))
        add_tb("fit", "fit", "Restablecer cámara / encuadre", self._fit_active)
        add_sep()
        self.tb_live = add_tb("live", "play", "Modo En Vivo / Presentación (escenario limpio)", self._toggle_live_presentation, checkable=True)

        tr.addStretch(1)

        self.tb_vp_bg = add_tb("vp_bg", "moon" if self._viewport_light else "moon_fill",
                               "Alternar fondo del viewport oscuro / claro", self._toggle_viewport_bg, checkable=True)
        self.tb_vp_bg.blockSignals(True)
        self.tb_vp_bg.setChecked(not self._viewport_light)
        self.tb_vp_bg.blockSignals(False)
        add_tb("prefs", "settings", "Configuración del estudio (Ctrl+,)", lambda: self.open_preferences(0))
        self.chrome_lay.addWidget(self.toolbar_row)

        # --- Franja 3: Barra de Pestañas de Escenas / Proyectos (#tabStrip, 34px) ---
        self.tab_strip = QWidget()
        self.tab_strip.setObjectName("tabStrip")
        self.tab_strip.setFixedHeight(34)
        ts = QHBoxLayout(self.tab_strip)
        ts.setContentsMargins(6, 0, 8, 0)
        ts.setSpacing(4)

        self.tab_bar = _StudioTabBar()
        self.tab_bar.currentChanged.connect(self._on_tab_bar_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)
        ts.addWidget(self.tab_bar, 0, Qt.AlignBottom)

        self.b_tab_add = QToolButton()
        self.b_tab_add.setObjectName("tabAddButton")
        self.b_tab_add.setFixedSize(26, 24)
        self.b_tab_add.setIconSize(QSize(15, 15))
        self.b_tab_add.setIcon(icons.state_icon("plus", 15))
        self.b_tab_add.setPopupMode(QToolButton.InstantPopup)
        self.b_tab_add.setToolTip("Nueva escena o proyecto")
        self.add_tab_menu = QMenu(self)
        self.add_tab_menu.addAction(icons.state_icon("rig", 14), "Nuevo Avatar VTuber 2D (.vtr)",
                                    lambda: self.create_project_tab({"kind": "vtuber", "name": "Mi VTuber 2D", "starter_rig": True}))
        self.add_tab_menu.addAction(icons.state_icon("cube", 14), "Nuevo Avatar VTuber 3D (.vt3d)",
                                    lambda: self.create_project_tab({"kind": "vtuber3d", "name": "Mi VTuber 3D", "preset": "anime_studio"}))
        self.add_tab_menu.addSeparator()
        self.add_tab_menu.addAction(icons.state_icon("tv", 14), "Nueva Escena Video 1920 × 1080",
                                    lambda: self.create_project_tab({"kind": "video", "name": "Nuevo Video", "w": 1920, "h": 1080, "fps": 30, "bg": "#0E1014", "transparent": False}))
        self.add_tab_menu.addAction(icons.state_icon("smartphone", 14), "Nuevo Vertical TikTok 1080 × 1920",
                                    lambda: self.create_project_tab({"kind": "tiktok", "name": "Clip TikTok", "w": 1080, "h": 1920, "fps": 60, "bg": "#0E1014", "transparent": False}))
        self.add_tab_menu.addSeparator()
        self.add_tab_menu.addAction(icons.state_icon("home", 14), "Ir a Pantalla de Inicio", lambda: self.switch_tab(0))
        self.b_tab_add.setMenu(self.add_tab_menu)
        ts.addWidget(self.b_tab_add, 0, Qt.AlignVCenter)
        ts.addStretch(1)
        self.chrome_lay.addWidget(self.tab_strip)

        self.setMenuWidget(self.chrome)

    def eventFilter(self, obj, ev):
        # Fallback multiplataforma de arrastre/doble clic en #titleBar si no actúa WM_NCHITTEST
        if obj is self.title_bar and sys.platform != "win32":
            if ev.type() == QEvent.MouseButtonDblClick and ev.button() == Qt.LeftButton:
                self._toggle_max_restore()
                return True
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._drag_fallback_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if ev.type() == QEvent.MouseMove and self._drag_fallback_pos is not None and (ev.buttons() & Qt.LeftButton):
                if self.isMaximized():
                    ratio = ev.position().x() / max(1, self.width())
                    self.showNormal()
                    self._drag_fallback_pos = QPoint(int(self.width() * ratio), int(ev.position().y()))
                self.move(ev.globalPosition().toPoint() - self._drag_fallback_pos)
                return True
            if ev.type() == QEvent.MouseButtonRelease:
                self._drag_fallback_pos = None
        return super().eventFilter(obj, ev)

    # =====================================================  Central Widget + Ecosistema de Docks y Rail
    def _build_central_and_docks(self):
        self.central_stack = QStackedWidget()
        self.setCentralWidget(self.central_stack)

        # Página 0: HomePage
        self.start_hub = StartHubWidget()
        self.start_hub.createProjectRequested.connect(self.create_project_tab)
        self.start_hub.openProjectDialogRequested.connect(self.open_project_dialog)
        self.start_hub.openProjectPathRequested.connect(lambda p: self.open_project_path(Path(p)))
        self.workspaces.append(self.start_hub)
        self.central_stack.addWidget(self.start_hub)

        # Página 1: Contenedor de Escenario Activo + Rail + Flyout + CanvasDock
        self.stage_container = _ViewportStageContainer()
        self.viewport_stack = self.stage_container.viewport_stack
        self.rail = self.stage_container.rail
        self.flyout = self.stage_container.flyout
        self.canvas_dock = self.stage_container.canvas_dock
        self.central_stack.addWidget(self.stage_container)

        self.tear_off = TearOffDragController(self)

        # --- Docks Primarios ---
        self.library_panel = ToolPalette(self)
        self.library_panel.actionTriggered.connect(self._on_library_action)

        self.scene_stack = QStackedWidget()
        self.scene_stack.addWidget(QWidget())  # placeholder 0

        self.inspector_stack = QStackedWidget()
        self.inspector_stack.addWidget(QWidget())  # placeholder 0

        self.layers_panel = LayersPanel(self)

        self.timeline_stack = QStackedWidget()
        self.timeline_stack.addWidget(QWidget())  # placeholder 0

        # --- Paneles Secundarios / Rail ---
        self.color_panel = ColorPickerPanel(self)
        self.palette_panel = ColorPalettePanel(standalone=True, targets=["Fondo", "Avatar", "Capa"])
        self.palette_panel.colorSelected.connect(lambda c: self.color_panel.editor.set_color(c, emit=True))
        self.tracking_panel = TrackingMonitorPanel(self)
        self.tracking_panel.calibrateRequested.connect(self._calibrate_active)
        self.audio_rack_panel = AudioMixerRackPanel(self)
        self.emotes_panel = _EmotesQuickPanel(self)
        self.output_panel = _OutputQuickPanel(self)

        # Registro completo de paneles acoplables
        self._panel_defs = [
            ("library", "Biblioteca y Soundboard", "folder", self.library_panel, Qt.LeftDockWidgetArea),
            ("scene", "Personajes y Escena", "user", self.scene_stack, Qt.LeftDockWidgetArea),
            ("inspector", "Propiedades / Inspector", "sliders", self.inspector_stack, Qt.RightDockWidgetArea),
            ("layers", "Jerarquía de Escena", "layers", self.layers_panel, Qt.RightDockWidgetArea),
            ("timeline", "Editor de Onda y Pistas", "timeline", self.timeline_stack, Qt.BottomDockWidgetArea),
            ("color", "Rueda de Color", "palette", self.color_panel, Qt.RightDockWidgetArea),
            ("palette", "Paleta de Muestras", "swatches", self.palette_panel, Qt.RightDockWidgetArea),
            ("tracking", "Tracking y Curvas", "face_track", self.tracking_panel, Qt.RightDockWidgetArea),
            ("audio_fx", "Mezclador y Efectos DSP", "wave", self.audio_rack_panel, Qt.BottomDockWidgetArea),
            ("emotes", "Expresiones y Hotkeys", "emote", self.emotes_panel, Qt.RightDockWidgetArea),
            ("output", "Salida OBS y Stream", "monitor", self.output_panel, Qt.RightDockWidgetArea),
        ]

        for pid, title, ic, wdg, area in self._panel_defs:
            d = StudioDockWidget(pid, title, ic, wdg, self)
            d.floatingMoved.connect(self._on_floating_dock_moved)
            d.floatingReleased.connect(self._on_floating_dock_released)
            self.docks[pid] = d
            self.addDockWidget(area, d)

        # Apilar en pestañas izquierda y derecha según especificación
        self.tabifyDockWidget(self.docks["scene"], self.docks["library"])
        self.docks["scene"].raise_()
        self.tabifyDockWidget(self.docks["inspector"], self.docks["layers"])
        self.docks["inspector"].raise_()

        # Dimensionar laterales predeterminados (218 / 286 o compactos)
        self.resizeDocks([self.docks["scene"], self.docks["inspector"]],
                         [DENSITY["dock_left_w"], DENSITY["dock_right_w"]], Qt.Horizontal)
        self.resizeDocks([self.docks["timeline"]], [220], Qt.Vertical)

        # Absorber inicialmente los paneles secundarios en PanelRail
        self.rail.set_catalog([(pid, title, ic) for pid, title, ic, _, _ in self._panel_defs])
        for pid in list(self._rail_ids):
            if pid in self.docks:
                self.docks[pid]._in_rail = True
                self.docks[pid].hide()
        self._sync_rail_buttons()

        self.rail.panelClicked.connect(self._on_rail_panel_clicked)
        self.rail.collapseRightToggled.connect(self._toggle_collapse_right_docks)
        self.rail.reordered.connect(self._on_rail_reordered)
        self.rail.tearOffRequested.connect(self._on_rail_tear_off)
        self.rail.togglePanelInRailRequested.connect(self._on_toggle_panel_in_rail)
        self.flyout.closed.connect(lambda: self.rail.set_active(None))
        self.flyout.tearOffStarted.connect(self._on_rail_tear_off)
        self.canvas_dock.actionTriggered.connect(self._on_canvas_dock_action)

        # Guardar estado limpio por defecto de docks
        self._default_studio_state = self.saveState()

    # =====================================================  Lógica de PanelRail / Flyout / Re-absorción
    def _sync_rail_buttons(self):
        meta_by_id = {pid: (pid, title, ic) for pid, title, ic, _, _ in self._panel_defs}
        entries = [meta_by_id[pid] for pid in self._rail_ids if pid in meta_by_id]
        active = self.flyout.panel_id if self.flyout.isVisible() else None
        self.rail.set_entries(entries, active_id=active)
        UI_PREFS["rail_panels"] = list(self._rail_ids)
        save_ui_prefs(UI_PREFS)

    def _on_rail_panel_clicked(self, pid: str):
        if self.flyout.isVisible() and self.flyout.panel_id == pid:
            self.flyout.close_flyout()
            self.rail.set_active(None)
            return
        dock = self.docks.get(pid)
        if dock is None:
            return
        wdg = dock.take_content()
        self.flyout.show_panel(pid, dock.title_str, dock.icon_name, wdg, self.rail.width())
        self.rail.set_active(pid)

    def _on_rail_reordered(self, new_ids: list):
        self._rail_ids = list(new_ids)
        UI_PREFS["rail_panels"] = list(self._rail_ids)
        save_ui_prefs(UI_PREFS)

    def _on_rail_tear_off(self, pid: str, gp: QPoint):
        if self.flyout.panel_id == pid:
            self.flyout.clear_content()
            self.flyout.hide()
        if pid in self._rail_ids:
            self._rail_ids.remove(pid)
        dock = self.docks.get(pid)
        if dock is None:
            return
        dock._in_rail = False
        self._sync_rail_buttons()
        self.tear_off.begin(dock, gp)

    def absorb_dock_into_rail(self, pid: str, index: int = None):
        dock = self.docks.get(pid)
        if dock is None:
            return
        if self.flyout.panel_id == pid:
            self.flyout.clear_content()
            self.flyout.hide()
        dock.hide()
        dock.setFloating(False)
        dock._in_rail = True
        if pid in self._rail_ids:
            self._rail_ids.remove(pid)
        if index is None:
            self._rail_ids.append(pid)
        else:
            self._rail_ids.insert(max(0, min(len(self._rail_ids), index)), pid)
        self._sync_rail_buttons()

    def _on_toggle_panel_in_rail(self, pid: str, put_in_rail: bool):
        if put_in_rail:
            self.absorb_dock_into_rail(pid)
        else:
            if self.flyout.panel_id == pid:
                self.flyout.clear_content()
                self.flyout.hide()
            if pid in self._rail_ids:
                self._rail_ids.remove(pid)
            dock = self.docks.get(pid)
            if dock is not None:
                dock._in_rail = False
                dock.restore_content()
                dock.show()
            self._sync_rail_buttons()

    def _on_floating_dock_moved(self, dock: StudioDockWidget, gp: QPoint):
        if self.rail.isVisible() and self.rail._is_near_rail(gp, tol=20):
            self.rail.set_drop_target(True, gp)
        else:
            self.rail.set_drop_target(False)

    def _on_floating_dock_released(self, dock: StudioDockWidget, gp: QPoint):
        if self.rail.isVisible() and self.rail._is_near_rail(gp, tol=20):
            idx = self.rail.insert_index_at(gp)
            self.rail.set_drop_target(False)
            self.absorb_dock_into_rail(dock.panel_id, idx)
        else:
            self.rail.set_drop_target(False)

    def _toggle_collapse_right_docks(self):
        right_docks = [
            d for d in self.docks.values()
            if not d._in_rail and not d.isFloating() and self.dockWidgetArea(d) == Qt.RightDockWidgetArea
        ]
        any_vis = any(d.isVisible() for d in right_docks)
        if any_vis:
            self._right_collapsed_mem = [d.panel_id for d in right_docks if d.isVisible()]
            for d in right_docks:
                d.hide()
            self.rail.set_right_collapsed_visual(True)
        else:
            to_show = self._right_collapsed_mem or ["inspector", "layers"]
            for pid in to_show:
                if pid in self.docks and not self.docks[pid]._in_rail:
                    self.docks[pid].restore_content()
                    self.docks[pid].show()
            self.rail.set_right_collapsed_visual(False)

    # =====================================================  8 Menús Completos Embebidos en #titleBar
    def _act(self, text, cb, icon=None, key=None, checkable=False):
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.state_icon(icon, 14))
        if key:
            a.setShortcut(QKeySequence(key))
        a.setCheckable(checkable)
        if checkable:
            a.toggled.connect(cb)
        else:
            a.triggered.connect(lambda _=False: cb())
        return a

    def _build_menus(self):
        mb = self.menu_bar
        mb.clear()
        A = self._act

        # 1. Archivo
        m_f = mb.addMenu("Archivo")
        sub_new = m_f.addMenu(icons.state_icon("file_new", 14), "Nuevo proyecto / escena")
        sub_new.addAction(A("Avatar VTuber 2D / Rig 2.5D (.vtr)", lambda: self.create_project_tab({"kind": "vtuber", "name": "Mi VTuber 2D", "starter_rig": True}), "rig"))
        sub_new.addAction(A("Avatar VTuber 3D / VRM (.vt3d)", lambda: self.create_project_tab({"kind": "vtuber3d", "name": "Mi VTuber 3D", "preset": "anime_studio"}), "cube"))
        sub_new.addSeparator()
        sub_new.addAction(A("Escena Video 1920 × 1080 (.setxd)", lambda: self.create_project_tab({"kind": "video", "name": "Nuevo Video", "w": 1920, "h": 1080, "fps": 30, "bg": "#0E1014", "transparent": False}), "tv"))
        sub_new.addAction(A("Vertical TikTok / Audio 1080 × 1920", lambda: self.create_project_tab({"kind": "tiktok", "name": "Clip TikTok", "w": 1080, "h": 1920, "fps": 60, "bg": "#0E1014", "transparent": False}), "smartphone"))
        sub_new.addAction(A("Overlay Transparente 60 FPS", lambda: self.create_project_tab({"kind": "video", "name": "Overlay VTuber", "w": 1920, "h": 1080, "fps": 60, "bg": "#00b140", "transparent": True}), "alpha"))
        m_f.addAction(A("Abrir…", self.open_project_dialog, "folder_open", "Ctrl+O"))

        self.recent_menu = m_f.addMenu(icons.state_icon("folder", 14), "Abrir reciente")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)

        sub_imp = m_f.addMenu(icons.state_icon("download", 14), "Importar")
        sub_imp.addAction(A("Rig VTuber 2D (.vtr)…", lambda: self._import_in_active("vtr"), "rig"))
        sub_imp.addAction(A("Avatar / Modelo 3D (.vt3d / .vrm / .glb / .obj)…", lambda: self._import_in_active("3d"), "cube"))
        sub_imp.addAction(A("Imagen / Prop / GIF…", lambda: self._import_in_active("image"), "image"))
        sub_imp.addAction(A("Fondo de escena…", lambda: self._call_video("set_background"), "palette"))
        sub_imp.addAction(A("Pista de Audio / Craig (.zip / .wav / .mp3)…", lambda: self._import_in_active("audio"), "music"))
        m_f.addSeparator()
        m_f.addAction(A("Guardar", self.save_active, "save", "Ctrl+S"))
        m_f.addAction(A("Guardar como…", self.save_active_as, "save", "Ctrl+Shift+S"))
        sub_exp = m_f.addMenu(icons.state_icon("export", 14), "Exportar")
        sub_exp.addAction(A("Exportar Producción (MP4 / WebM Alfa / Audio)…", self.export_active, "export", "Ctrl+E"))
        sub_exp.addAction(A("Guardar Captura PNG", self._screenshot_active, "camera"))
        sub_exp.addAction(A("Hornear 12 Sprites PNG (VTuber 2D)…", lambda: self._call_vtuber2d("export_12_sprites"), "image"))
        m_f.addSeparator()
        m_f.addAction(A("Ir a Inicio", lambda: self.switch_tab(0), "home", "Ctrl+T"))
        m_f.addAction(A("Cerrar pestaña", lambda: self.close_tab(self._active_idx), "close", "Ctrl+W"))
        m_f.addAction(A("Salir", self.close, None, "Ctrl+Q"))

        # 2. Editar
        m_e = mb.addMenu("Editar")
        m_e.addAction(A("Deshacer", lambda: self._call_video("undo_timeline"), "undo", "Ctrl+Z"))
        m_e.addAction(A("Rehacer", lambda: self._call_video("redo_timeline"), "redo", "Ctrl+Y"))
        m_e.addSeparator()
        m_e.addAction(A("Duplicar elemento seleccionado", self._duplicate_active, "copy", "Ctrl+D"))
        m_e.addAction(A("Eliminar elemento seleccionado", self._delete_active, "trash"))
        m_e.addAction(A("Dividir clip en el cursor", lambda: self._call_video_tl("_split_at_playhead"), "scissors", "Ctrl+K"))
        m_e.addSeparator()
        m_e.addAction(A("Preferencias / Configuración…", lambda: self.open_preferences(0), "settings", "Ctrl+,"))
        m_e.addAction(A("Atajos y Hotkeys…", self.open_shortcuts_dialog, "sparkle"))

        # 3. Avatar / Escena
        m_a = mb.addMenu("Avatar / Escena")
        m_a.addAction(A("Nuevo personaje por Sprites (PNGTuber)", lambda: self._call_video("add_char"), "user", "Ctrl+N"))
        m_a.addAction(A("Importar Avatar VTuber 2D (.vtr)…", lambda: self._import_in_active("vtr"), "rig"))
        m_a.addAction(A("Importar Avatar VTuber 3D (.vt3d / .vrm)…", lambda: self._import_in_active("3d"), "cube"))
        m_a.addSeparator()
        m_a.addAction(A("Añadir Imagen / Logo / Prop", lambda: self._call_video("add_prop", "image"), "image"))
        m_a.addAction(A("Añadir Rótulo de Texto", lambda: self._call_video("add_prop", "text"), "text"))
        m_a.addAction(A("Añadir Visualizador de Audio", lambda: self._call_video("add_prop", "visualizer"), "wave"))
        m_a.addAction(A("Acoplar Accesorio a Personaje…", lambda: self._call_video("add_accessory"), "accessories"))
        m_a.addSeparator()
        m_a.addAction(A("Fondos / Chroma Key…", self._toggle_chroma_active, "alpha"))
        m_a.addAction(A("Iluminación, Shaders y Color", lambda: self._focus_panel("color"), "palette"))
        m_a.addAction(A("Nueva Escena en proyecto", lambda: self._call_video("add_scene"), "layers"))

        # 4. Tracking / Expresiones
        m_t = mb.addMenu("Tracking / Expresiones")
        m_t.addAction(A("Activar / Detener Webcam Tracking", lambda: self.tb_track.setChecked(not self.tb_track.isChecked()), "webcam"))
        m_t.addAction(A("Calibrar pose neutral y voz", self._calibrate_active, "calibrate"))
        m_t.addSeparator()
        m_t.addAction(A("Monitor de Tracking, Curvas y BlendShapes", lambda: self._focus_panel("tracking"), "face_track"))
        m_t.addAction(A("Expresiones y Disparadores / Emotes", lambda: self._focus_panel("emotes"), "emote"))
        m_t.addAction(A("Configurar Cámara y Seguimiento…", lambda: self.open_preferences(3), "settings"))

        # 5. Audio / Sonido
        m_s = mb.addMenu("Audio / Sonido")
        m_s.addAction(A("Mezclador de Audio", lambda: self._open_audio_panel(0), "sliders"))
        m_s.addAction(A("Editor de Onda / Pistas (Timeline)", self._toggle_timeline_dock, "timeline"))
        m_s.addAction(A("Rack de Efectos de Voz (EQ, Compresor, Pitch, Reverb)", lambda: self._open_audio_panel(1), "fx"))
        m_s.addSeparator()
        m_s.addAction(A("Grabar voz en pistas armadas", self._toggle_rec_active, "record", "Ctrl+R"))
        m_s.addAction(A("Soundboard y Disparadores SFX", lambda: self._open_soundboard(), "music"))
        m_s.addAction(A("Discord en vivo e importación Craig…", lambda: self.open_preferences(4), "mic"))

        # 6. Ver / Salida
        m_vo = mb.addMenu("Ver / Salida")
        m_vo.addAction(A("Acercar", lambda: self._zoom_active(1.18), "zoom_in", "Ctrl++"))
        m_vo.addAction(A("Alejar", lambda: self._zoom_active(0.85), "zoom_out", "Ctrl+-"))
        m_vo.addAction(A("Restablecer cámara / encuadre", self._fit_active, "fit", "Ctrl+0"))
        m_vo.addSeparator()
        self.act_guides = A("Cuadrícula y Guías de encuadre OBS", self._on_menu_guides, "grid", checkable=True)
        self.act_guides.setChecked(True)
        m_vo.addAction(self.act_guides)
        m_vo.addAction(A("Alternar Fondo Transparente / Chroma Key", self._toggle_chroma_active, "alpha"))
        m_vo.addAction(A("Alternar fondo viewport oscuro / claro", lambda: self.tb_vp_bg.setChecked(not self.tb_vp_bg.isChecked()), "moon"))
        m_vo.addSeparator()
        m_vo.addAction(A("Ventana Limpia de Captura OBS (F11)", self._open_obs_active, "monitor"))
        m_vo.addAction(A("Iniciar / Detener Cámara Virtual", self._toggle_vcam_active, "video"))
        self.act_live_mode = A("Modo En Vivo / Presentación", self._toggle_live_presentation, "play", "F10", checkable=True)
        m_vo.addAction(self.act_live_mode)

        # 7. Ventana
        self.window_menu = mb.addMenu("Ventana")
        self.window_menu.aboutToShow.connect(self._populate_window_menu)

        # 8. Ayuda
        m_h = mb.addMenu("Ayuda")
        m_h.addAction(A("Diagnóstico y Rendimiento GPU/Audio", self._show_diagnostics, "gpu"))
        m_h.addAction(A("Atajos de teclado…", self.open_shortcuts_dialog, "sparkle"))
        m_h.addSeparator()
        m_h.addAction(A("Acerca de PodcastSet Studio", self._show_about, "app_logo"))

    def _populate_recent_menu(self):
        self.recent_menu.clear()
        rec = M.load_recent_projects()
        if not rec:
            a = self.recent_menu.addAction("Sin proyectos recientes")
            a.setEnabled(False)
            return
        for it in rec[:10]:
            p = it["path"]
            self.recent_menu.addAction(f"{it.get('name', Path(p).stem)}  ({Path(p).suffix})",
                                       lambda _=False, pt=p: self.open_project_path(Path(pt)))

    def _populate_window_menu(self):
        m = self.window_menu
        m.clear()

        for pid, title, ic, _, _ in self._panel_defs:
            dock = self.docks[pid]
            act = m.addAction(icons.state_icon(ic, 14), title)
            act.setCheckable(True)
            is_open = (self.flyout.isVisible() and self.flyout.panel_id == pid) if dock._in_rail else dock.isVisible()
            act.setChecked(is_open)
            act.triggered.connect(lambda checked, p=pid: self._toggle_panel_visibility(p, checked))

        m.addSeparator()
        org = m.addMenu("Organizar paneles")
        for pid, title, ic, _, _ in self._panel_defs:
            pm = org.addMenu(icons.state_icon(ic, 14), title)
            pm.addAction("Acoplar a la izquierda", lambda _=False, p=pid: self._dock_panel_to(p, Qt.LeftDockWidgetArea))
            pm.addAction("Acoplar a la derecha", lambda _=False, p=pid: self._dock_panel_to(p, Qt.RightDockWidgetArea))
            pm.addAction("Acoplar abajo", lambda _=False, p=pid: self._dock_panel_to(p, Qt.BottomDockWidgetArea))
            pm.addAction("Desacoplar (Flotante)", lambda _=False, p=pid: self._float_panel(p))
            pm.addAction("Enviar al Rail derecho", lambda _=False, p=pid: self.absorb_dock_into_rail(p))

        m.addAction("Mostrar / Ocultar todos los paneles", self._toggle_all_panels)
        m.addAction("Desacoplar todos los paneles visibles", self._float_all_visible)
        m.addAction("Acoplar todos los paneles flotantes", self._dock_all_floating)

        m.addSeparator()
        ws_menu = m.addMenu("Espacio de trabajo")
        ws_menu.addAction("Estudio predeterminado", lambda: self.apply_workspace_preset("default"))
        ws_menu.addAction("Modo Compacto / Stream", lambda: self.apply_workspace_preset("stream"))
        ws_menu.addAction("Modo Edición de Audio", lambda: self.apply_workspace_preset("audio"))
        ws_menu.addSeparator()
        ws_menu.addAction("Intercambiar laterales", self._swap_side_docks)
        ws_menu.addSeparator()
        ws_menu.addAction("Guardar distribución como… (.json)", self._save_layout_json)
        ws_menu.addAction("Importar distribución… (.json)", self._load_layout_json)

        m.addSeparator()
        m.addAction(self._act("Siguiente pestaña", lambda: self._cycle_tab(1), None, "Ctrl+Tab"))
        m.addAction(self._act("Pestaña anterior", lambda: self._cycle_tab(-1), None, "Ctrl+Shift+Tab"))

    def _focus_panel(self, pid: str):
        if self._active_idx == 0:
            return
        dock = self.docks.get(pid)
        if dock is None:
            return
        if dock._in_rail:
            if not (self.flyout.isVisible() and self.flyout.panel_id == pid):
                self._on_rail_panel_clicked(pid)
        else:
            dock.restore_content()
            dock.show()
            dock.raise_()

    def _toggle_panel_visibility(self, pid: str, show: bool):
        dock = self.docks[pid]
        if dock._in_rail:
            if show:
                self._on_rail_panel_clicked(pid)
            elif self.flyout.panel_id == pid:
                self.flyout.close_flyout()
        else:
            if show:
                dock.restore_content()
                dock.show()
                dock.raise_()
            else:
                dock.hide()

    def _dock_panel_to(self, pid: str, area):
        if self.flyout.panel_id == pid:
            self.flyout.clear_content()
            self.flyout.hide()
        if pid in self._rail_ids:
            self._rail_ids.remove(pid)
            self._sync_rail_buttons()
        d = self.docks[pid]
        d._in_rail = False
        d.restore_content()
        d.setFloating(False)
        self.addDockWidget(area, d)
        d.show()
        d.raise_()

    def _float_panel(self, pid: str):
        if self.flyout.panel_id == pid:
            self.flyout.clear_content()
            self.flyout.hide()
        if pid in self._rail_ids:
            self._rail_ids.remove(pid)
            self._sync_rail_buttons()
        d = self.docks[pid]
        d._in_rail = False
        d.restore_content()
        d.setFloating(True)
        d.resize(280, 380)
        d.show()
        d.raise_()

    def _toggle_all_panels(self):
        vis_docks = [d for d in self.docks.values() if not d._in_rail and d.isVisible()]
        if vis_docks:
            self._toggle_all_mem = [d.panel_id for d in vis_docks]
            for d in vis_docks:
                d.hide()
        else:
            for pid in getattr(self, "_toggle_all_mem", ["scene", "inspector", "timeline"]):
                if pid in self.docks and not self.docks[pid]._in_rail:
                    self.docks[pid].restore_content()
                    self.docks[pid].show()

    def _float_all_visible(self):
        x0, y0 = self.x() + 80, self.y() + 110
        for i, d in enumerate(self.docks.values()):
            if not d._in_rail and d.isVisible():
                d.setFloating(True)
                d.move(x0 + i * 28, y0 + i * 24)

    def _dock_all_floating(self):
        area_map = {pid: area for pid, _, _, _, area in self._panel_defs}
        for pid, d in self.docks.items():
            if not d._in_rail and d.isFloating():
                d.setFloating(False)
                self.addDockWidget(area_map.get(pid, Qt.RightDockWidgetArea), d)
                d.show()

    def _swap_side_docks(self):
        lefts = [d for d in self.docks.values() if not d._in_rail and not d.isFloating() and self.dockWidgetArea(d) == Qt.LeftDockWidgetArea]
        rights = [d for d in self.docks.values() if not d._in_rail and not d.isFloating() and self.dockWidgetArea(d) == Qt.RightDockWidgetArea]
        for d in lefts:
            self.addDockWidget(Qt.RightDockWidgetArea, d)
        for d in rights:
            self.addDockWidget(Qt.LeftDockWidgetArea, d)

    def apply_workspace_preset(self, preset: str):
        self.flyout.clear_content()
        self.flyout.hide()
        UI_PREFS["workspace_preset"] = preset
        save_ui_prefs(UI_PREFS)

        if preset == "default":
            self._rail_ids = ["color", "palette", "tracking", "audio_fx", "emotes", "output"]
            for pid, d in self.docks.items():
                d._in_rail = pid in self._rail_ids
                if d._in_rail:
                    d.hide()
                else:
                    d.restore_content()
            self.restoreState(self._default_studio_state)
            for pid in ("scene", "library", "inspector", "layers", "timeline"):
                self.docks[pid].show()
            self.docks["scene"].raise_()
            self.docks["inspector"].raise_()

        elif preset == "stream":
            # Escenario máximo: laterales en el rail, solo controles esenciales al vuelo
            self._rail_ids = ["scene", "inspector", "layers", "color", "tracking", "audio_fx", "emotes", "output"]
            for pid, d in self.docks.items():
                d._in_rail = pid in self._rail_ids
                if d._in_rail:
                    d.hide()
            self.docks["library"].hide()
            self.docks["timeline"].hide()

        elif preset == "audio":
            # Edición de audio + mezclador + timeline expandidos abajo y derecha
            self._rail_ids = ["color", "palette", "tracking", "emotes", "output"]
            for pid, d in self.docks.items():
                d._in_rail = pid in self._rail_ids
                if d._in_rail:
                    d.hide()
                else:
                    d.restore_content()
            self._dock_panel_to("scene", Qt.LeftDockWidgetArea)
            self._dock_panel_to("audio_fx", Qt.RightDockWidgetArea)
            self._dock_panel_to("timeline", Qt.BottomDockWidgetArea)
            self.docks["inspector"].show()
            self.tabifyDockWidget(self.docks["audio_fx"], self.docks["inspector"])
            self.docks["audio_fx"].raise_()
            self.resizeDocks([self.docks["timeline"]], [320], Qt.Vertical)

        self._sync_rail_buttons()
        if self._active_idx == 0:
            self._hide_docks_for_home()

    def _save_layout_json(self):
        p, _ = QFileDialog.getSaveFileName(self, "Guardar distribución de espacio de trabajo", str(M.BASE / "mi_espacio_studio.json"), "Distribución JSON (*.json)")
        if not p:
            return
        payload = {
            "qt_state": bytes(self.saveState().toBase64()).decode("ascii"),
            "rail_ids": list(self._rail_ids),
            "visible_docks": [pid for pid, d in self.docks.items() if not d._in_rail and d.isVisible()],
        }
        Path(p).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"Distribución guardada: {Path(p).name}", 3000)

    def _load_layout_json(self):
        p, _ = QFileDialog.getOpenFileName(self, "Importar distribución de espacio de trabajo", str(M.BASE), "Distribución JSON (*.json)")
        if not p:
            return
        try:
            payload = json.loads(Path(p).read_text(encoding="utf-8"))
            self._rail_ids = list(payload.get("rail_ids", []))
            for pid, d in self.docks.items():
                d._in_rail = pid in self._rail_ids
                if d._in_rail:
                    d.hide()
                else:
                    d.restore_content()
            if payload.get("qt_state"):
                self.restoreState(QByteArray.fromBase64(payload["qt_state"].encode("ascii")))
            vis = set(payload.get("visible_docks", []))
            for pid, d in self.docks.items():
                if not d._in_rail:
                    d.setVisible(pid in vis)
            self._sync_rail_buttons()
            self.statusBar().showMessage(f"Distribución cargada: {Path(p).name}", 3000)
        except Exception as e:
            QMessageBox.warning(self, "Error al cargar distribución", str(e))

    # =====================================================  Barra de Estado Studio (Sin Size Grip)
    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)

        self.load_prog = QProgressBar()
        self.load_prog.setFixedSize(100, 8)
        self.load_prog.setTextVisible(False)
        self.load_prog.setVisible(False)
        sb.addPermanentWidget(self.load_prog)

        self.stat_track_chip = QLabel("Tracking: Voz")
        self.stat_track_chip.setObjectName("statusChip")
        sb.addPermanentWidget(self.stat_track_chip)

        self.stat_res_chip = QLabel("Inicio")
        self.stat_res_chip.setObjectName("statusChip")
        sb.addPermanentWidget(self.stat_res_chip)

        self.stat_fps_chip = QLabel("60 FPS")
        self.stat_fps_chip.setObjectName("statusChip")
        self.stat_fps_chip.setStyleSheet("font-family:'Consolas','Cascadia Mono',monospace;")
        sb.addPermanentWidget(self.stat_fps_chip)

        self.zoom_btn = QToolButton()
        self.zoom_btn.setObjectName("statusButton")
        self.zoom_btn.setText("100% ▾")
        self.zoom_btn.setPopupMode(QToolButton.InstantPopup)
        z_menu = QMenu(self)
        for pct, f in (("50%", 0.5), ("75%", 0.75), ("100%", 1.0), ("150%", 1.5), ("200%", 2.0)):
            z_menu.addAction(pct, lambda _=False, sc=f: self._set_zoom_exact(sc))
        z_menu.addSeparator()
        z_menu.addAction("Ajustar al escenario", self._fit_active)
        self.zoom_btn.setMenu(z_menu)
        sb.addPermanentWidget(self.zoom_btn)

    # =====================================================  Atajos de Teclado
    def _setup_shortcuts(self):
        for i in range(9):
            QShortcut(QKeySequence(f"F{i + 1}"), self, lambda i=i: self._call_video("switch_scene", i))
            QShortcut(QKeySequence(f"Alt+{i + 1}"), self, lambda i=i: self._hotkey_alt_num(i))
            QShortcut(QKeySequence(str(i + 1)), self, lambda d=str(i + 1): self._hotkey_vtuber_toggle(d))
        QShortcut(QKeySequence(Qt.Key_Space), self, lambda: self._hotkey(self._toggle_play_active))
        QShortcut(QKeySequence("E"), self, lambda: self._hotkey(lambda: self._toggle_action("a_edit")))
        QShortcut(QKeySequence("T"), self, lambda: self._hotkey(self._toggle_timeline_dock))
        QShortcut(QKeySequence("V"), self, lambda: self._hotkey(lambda: self._call_video_tl("set_tool", "select")))
        QShortcut(QKeySequence("C"), self, lambda: self._hotkey(lambda: self._call_video_tl("set_tool", "razor")))
        QShortcut(QKeySequence("H"), self, lambda: self._hotkey(lambda: self._call_video_tl("set_tool", "hand")))
        QShortcut(QKeySequence("I"), self, lambda: self._hotkey(self._mark_in_active))
        QShortcut(QKeySequence("O"), self, lambda: self._hotkey(self._mark_out_active))

    def _typing(self):
        if QApplication.activeModalWidget() is not None:
            return True
        return isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox))

    def _hotkey(self, fn):
        if not self._typing():
            fn()

    # =====================================================  Gestión de Pestañas + Ocultación en Inicio
    def _hide_docks_for_home(self):
        if self._saved_dock_vis is None:
            self._saved_dock_vis = {
                pid: (not d.isHidden()) for pid, d in self.docks.items() if not d._in_rail
            }
        self.flyout.close_flyout()
        for d in self.docks.values():
            d.hide()

    def _restore_docks_from_home(self):
        if self._live_presentation:
            return
        if self._saved_dock_vis is not None:
            for pid, was_vis in self._saved_dock_vis.items():
                if pid in self.docks and not self.docks[pid]._in_rail:
                    if was_vis:
                        self.docks[pid].restore_content()
                        self.docks[pid].show()
                    else:
                        self.docks[pid].hide()
            if "scene" in self.docks and not self.docks["scene"].isHidden():
                self.docks["scene"].raise_()
            if "inspector" in self.docks and not self.docks["inspector"].isHidden():
                self.docks["inspector"].raise_()
            self._saved_dock_vis = None

    def _rebuild_tab_bar(self):
        self.tab_bar.blockSignals(True)
        while self.tab_bar.count():
            self.tab_bar.removeTab(0)

        ic_map = {
            "home": "home",
            "video": "tv",
            "tiktok": "smartphone",
            "vtuber": "rig",
            "vtuber3d": "cube",
        }
        for i, ws in enumerate(self.workspaces):
            if i == 0:
                self.tab_bar.addTab(icons.state_icon("home", 15), "")
                self.tab_bar.setTabToolTip(0, "Inicio")
            else:
                kind = ws.tab_kind()
                title = ws.tab_title()
                t_idx = self.tab_bar.addTab(icons.state_icon(ic_map.get(kind, "tv"), 15), title)
                self.tab_bar.setTabToolTip(t_idx, title)
                close_btn = QToolButton()
                close_btn.setObjectName("tabClose")
                close_btn.setFixedSize(17, 17)
                close_btn.setIconSize(QSize(12, 12))
                close_btn.setIcon(icons.state_icon("close", 12))
                close_btn.setToolTip("Cerrar pestaña (Ctrl+W)")
                close_btn.clicked.connect(lambda _=False, w_ref=ws: self._close_ws_ref(w_ref))
                self.tab_bar.setTabButton(t_idx, QTabBar.RightSide, close_btn)

        self.tab_bar.setCurrentIndex(self._active_idx)
        self.tab_bar.blockSignals(False)

    def _close_ws_ref(self, ws):
        if ws in self.workspaces:
            self.close_tab(self.workspaces.index(ws))

    def _on_tab_bar_changed(self, idx: int):
        if 0 <= idx < len(self.workspaces) and idx != self._active_idx:
            self.switch_tab(idx)

    def _on_tab_moved(self, from_idx: int, to_idx: int):
        # La pestaña 0 (Inicio) siempre permanece fija en 0
        if from_idx == 0 or to_idx == 0:
            self._rebuild_tab_bar()
            return
        ws = self.workspaces.pop(from_idx)
        self.workspaces.insert(to_idx, ws)
        self._active_idx = self.tab_bar.currentIndex()

    def _on_ws_title_changed(self, ws):
        if ws in self.workspaces:
            idx = self.workspaces.index(ws)
            if 0 < idx < self.tab_bar.count():
                self.tab_bar.setTabText(idx, ws.tab_title())
                self.tab_bar.setTabToolTip(idx, ws.tab_title())
            if idx == self._active_idx:
                self._sync_window_title()

    def _sync_window_title(self):
        ws = self.active_workspace()
        if ws is None or self._active_idx == 0:
            txt = "PodcastSet Studio"
        else:
            txt = f"{ws.tab_title()} — PodcastSet Studio"
        self.setWindowTitle(txt)
        if hasattr(self, "doc_title_hint"):
            self.doc_title_hint.setText(txt)

    def active_workspace(self):
        if 0 <= self._active_idx < len(self.workspaces):
            return self.workspaces[self._active_idx]
        return None

    def switch_tab(self, idx: int):
        if not (0 <= idx < len(self.workspaces)):
            return
        prev = self.active_workspace()
        if prev is not None and idx != self._active_idx and hasattr(prev, "on_tab_deactivated"):
            prev.on_tab_deactivated()

        self._active_idx = idx
        self.tab_bar.blockSignals(True)
        self.tab_bar.setCurrentIndex(idx)
        self.tab_bar.blockSignals(False)

        if idx == 0:
            self._hide_docks_for_home()
            self.central_stack.setCurrentIndex(0)
            self.start_hub.refresh_recents()
            self.stat_res_chip.setText("Inicio")
            self.stat_track_chip.setText("Listo")
            self._bind_auxiliary_panels(None)
        else:
            ws = self.workspaces[idx]
            self.central_stack.setCurrentIndex(1)
            self._restore_docks_from_home()
            panels = self._ws_panel_map.get(id(ws))
            if panels is not None:
                lw, cw, rw, bw = panels
                self.scene_stack.setCurrentWidget(lw)
                self.viewport_stack.setCurrentWidget(cw)
                self.inspector_stack.setCurrentWidget(rw)
                self.timeline_stack.setCurrentWidget(bw)
            self._bind_auxiliary_panels(ws)
            self._sync_toolbar_to_ws(ws)

        self._sync_window_title()

    def _bind_auxiliary_panels(self, ws):
        self.layers_panel.bind_workspace(ws)
        self.color_panel.bind_workspace(ws)
        self.audio_rack_panel.bind_workspace(ws)
        self.tracking_panel.bind_workspace(ws)

    def _sync_toolbar_to_ws(self, ws):
        kind = ws.tab_kind()
        if kind in ("video", "tiktok"):
            cw, ch = ws.data.get("canvas_w", 1920), ws.data.get("canvas_h", 1080)
            self.stat_res_chip.setText(f"{cw}×{ch} · {ws.data.get('fps', 30)} FPS")
            cam_on = ws.face_tracker.running
            self.stat_track_chip.setText("Tracking: Webcam + Voz" if cam_on else "Tracking: Voz / Lip-Sync")
            self.tb_track.blockSignals(True)
            self.tb_track.setChecked(cam_on)
            self.tb_track.blockSignals(False)
            self.canvas_dock.set_checked_silent("webcam", cam_on)
            self.canvas_dock.set_checked_silent("guides", ws.view.guides)
            self.canvas_dock.set_checked_silent("chroma", ws.sdata().get("bg_type") == "transparent")
            if hasattr(ws.view, "set_viewport_light"):
                ws.view.set_viewport_light(self._viewport_light)
        elif kind == "vtuber":
            self.stat_res_chip.setText("VTuber 2D · 1080×1080")
            cam_on = ws.face_tracker.running
            self.stat_track_chip.setText("VTuber 2D: Cámara + Voz" if cam_on else "VTuber 2D: Solo Voz")
            self.tb_track.blockSignals(True)
            self.tb_track.setChecked(cam_on)
            self.tb_track.blockSignals(False)
            self.canvas_dock.set_checked_silent("webcam", cam_on)
        elif kind == "vtuber3d":
            self.stat_res_chip.setText("VTuber 3D · Tiempo Real")
            cam_on = ws.face_tracker.running
            self.stat_track_chip.setText("VTuber 3D: Cámara + Voz" if cam_on else "VTuber 3D: Solo Voz")
            self.tb_track.blockSignals(True)
            self.tb_track.setChecked(cam_on)
            self.tb_track.blockSignals(False)
            self.canvas_dock.set_checked_silent("webcam", cam_on)

    def _cycle_tab(self, delta):
        if len(self.workspaces) > 1:
            self.switch_tab((self._active_idx + delta) % len(self.workspaces))

    def _add_workspace_tab(self, ws):
        ws.titleChanged.connect(self._on_ws_title_changed)
        ws.statusMessage.connect(self.statusBar().showMessage)
        if isinstance(ws, VideoStudioWorkspace):
            ws.loadProgressChanged.connect(self._on_load_progress)
            ws.openRigInTabRequested.connect(self._open_rig_data_in_tab)
            ws.openRig3DInTabRequested.connect(self._open_vt3d_data_in_tab)
            ws.view.zoomChanged.connect(lambda pct: self.zoom_btn.setText(f"{pct}% ▾"))
            ws.timelineVisibilityRequested.connect(lambda on: self._toggle_panel_visibility("timeline", on))
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.sendToVideoRequested.connect(self._on_send_rig_to_video)
        elif isinstance(ws, VTuber3DWorkspace):
            ws.sendToVideoRequested.connect(self._on_send_vt3d_to_video)

        lw, cw, rw, bw = ws.detach_studio_panels()
        self.scene_stack.addWidget(lw)
        self.viewport_stack.addWidget(cw)
        self.inspector_stack.addWidget(rw)
        self.timeline_stack.addWidget(bw)
        self._ws_panel_map[id(ws)] = (lw, cw, rw, bw)

        self.workspaces.append(ws)
        self._active_idx = len(self.workspaces) - 1
        self._rebuild_tab_bar()
        self.switch_tab(self._active_idx)
        return ws

    # =====================================================  Creación / Apertura / Cierre de Proyectos
    def create_project_tab(self, spec):
        kind = spec.get("kind", "video")
        if kind == "vtuber":
            name = spec.get("name", "Mi VTuber 2D")
            rig = VR.build_starter_vtuber_rig(name) if spec.get("starter_rig", True) else VR.new_empty_rig(name)
            ws = VTuberStudioWorkspace(rig_data=rig, rig_path=None)
            self._add_workspace_tab(ws)
            self.statusBar().showMessage(f"Estudio VTuber 2D «{name}» abierto en nueva pestaña", 4000)
            return ws
        if kind == "vtuber3d":
            name = spec.get("name", "Mi VTuber 3D")
            d3 = V3.new_vtuber3d(name=name, preset=spec.get("preset", "anime_studio"))
            ws = VTuber3DWorkspace(vt3d_data=d3, vt3d_path=None)
            self._add_workspace_tab(ws)
            self.statusBar().showMessage(f"Estudio VTuber 3D «{name}» abierto en nueva pestaña", 4000)
            return ws

        data = M.new_empty_project(
            name=spec.get("name", "Nuevo Proyecto"),
            canvas_w=spec.get("w", 1920),
            canvas_h=spec.get("h", 1080),
            fps=spec.get("fps", 30),
            bg_color=spec.get("bg", "#0E1014"),
            transparent=spec.get("transparent", False),
        )
        ws = VideoStudioWorkspace(data, project_path=None)
        self._add_workspace_tab(ws)
        self.statusBar().showMessage(
            f"Nuevo espacio {data['canvas_w']}×{data['canvas_h']} ({data['fps']} FPS) listo", 4000
        )
        return ws

    def open_project_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Abrir proyecto (.setxd, .vtr o .vt3d)", str(M.BASE), M.PROJECT_FILTER)
        if p:
            self.open_project_path(Path(p))

    def open_project_path(self, path):
        p = Path(path).resolve()
        for i, ws in enumerate(self.workspaces[1:], 1):
            w_path = getattr(ws, "project_path", None) or getattr(ws, "rig_path", None) or getattr(ws, "vt3d_path", None)
            if w_path and Path(w_path).resolve() == p:
                self.switch_tab(i)
                self.statusBar().showMessage(f"Pestaña ya abierta: {p.name}", 2500)
                return ws

        if p.suffix.lower() == ".vt3d":
            d3 = V3.load_vtuber3d(p)
            M.record_recent_project(p, d3)
            ws = VTuber3DWorkspace(vt3d_data=d3, vt3d_path=p)
            self._add_workspace_tab(ws)
            self.statusBar().showMessage(f"VTuber 3D abierto: {p.name}", 3500)
            return ws

        if p.suffix.lower() == ".vtr":
            rig = VR.load_rig(p)
            M.record_recent_project(p, rig)
            ws = VTuberStudioWorkspace(rig_data=rig, rig_path=p)
            self._add_workspace_tab(ws)
            self.statusBar().showMessage(f"Rig VTuber abierto: {p.name}", 3500)
            return ws

        data = M.load_project(p)
        M.record_recent_project(p, data)
        ws = VideoStudioWorkspace(data, project_path=p)
        self._add_workspace_tab(ws)
        self.statusBar().showMessage(f"Proyecto abierto: {p.name}", 3500)
        return ws

    def _open_rig_data_in_tab(self, rig_data, rig_path):
        ws = VTuberStudioWorkspace(rig_data=rig_data, rig_path=rig_path)
        self._add_workspace_tab(ws)

    def _open_vt3d_data_in_tab(self, vt3d_data, vt3d_path):
        ws = VTuber3DWorkspace(vt3d_data=vt3d_data, vt3d_path=vt3d_path)
        self._add_workspace_tab(ws)

    def _on_send_vt3d_to_video(self, vt3d_data, vt3d_path):
        video_tabs = [(i, ws) for i, ws in enumerate(self.workspaces) if isinstance(ws, VideoStudioWorkspace)]
        options = [f"Pestaña: {ws.tab_title()}" for _, ws in video_tabs]
        options += ["Nuevo Video Horizontal (1920 × 1080)", "Nuevo Vertical TikTok (1080 × 1920)"]
        choice, ok = QInputDialog.getItem(
            self, "Enviar VTuber 3D a escena de video",
            f"¿Dónde quieres usar a «{vt3d_data.get('name', 'VTuber 3D')}»?",
            options, 0, False,
        )
        if not ok:
            return
        idx = options.index(choice)
        if idx < len(video_tabs):
            tab_i, target_ws = video_tabs[idx]
            target_ws.add_vtuber3d_character(vt3d_data, vt3d_path)
            self.switch_tab(tab_i)
        elif idx == len(video_tabs):
            target_ws = self.create_project_tab({"kind": "video", "name": f"Video - {vt3d_data.get('name', 'VTuber 3D')}", "w": 1920, "h": 1080, "fps": 60, "bg": "#0E1014", "transparent": False})
            target_ws.add_vtuber3d_character(vt3d_data, vt3d_path)
        else:
            target_ws = self.create_project_tab({"kind": "tiktok", "name": f"TikTok - {vt3d_data.get('name', 'VTuber 3D')}", "w": 1080, "h": 1920, "fps": 60, "bg": "#0E1014", "transparent": False})
            target_ws.add_vtuber3d_character(vt3d_data, vt3d_path)

    def _on_send_rig_to_video(self, rig_data, rig_path):
        video_tabs = [(i, ws) for i, ws in enumerate(self.workspaces) if isinstance(ws, VideoStudioWorkspace)]
        options = [f"Pestaña: {ws.tab_title()}" for _, ws in video_tabs]
        options += ["Nuevo Video Horizontal (1920 × 1080)", "Nuevo Vertical TikTok (1080 × 1920)"]
        choice, ok = QInputDialog.getItem(
            self, "Enviar VTuber 2D a escena de video",
            f"¿Dónde quieres usar a «{rig_data.get('name', 'VTuber')}»?",
            options, 0, False,
        )
        if not ok:
            return
        idx = options.index(choice)
        if idx < len(video_tabs):
            tab_i, target_ws = video_tabs[idx]
            target_ws.add_vtuber_rig_character(rig_data, rig_path)
            self.switch_tab(tab_i)
        elif idx == len(video_tabs):
            target_ws = self.create_project_tab({"kind": "video", "name": f"Video - {rig_data.get('name', 'VTuber')}", "w": 1920, "h": 1080, "fps": 60, "bg": "#0E1014", "transparent": False})
            target_ws.add_vtuber_rig_character(rig_data, rig_path)
        else:
            target_ws = self.create_project_tab({"kind": "tiktok", "name": f"TikTok - {rig_data.get('name', 'VTuber')}", "w": 1080, "h": 1920, "fps": 60, "bg": "#0E1014", "transparent": False})
            target_ws.add_vtuber_rig_character(rig_data, rig_path)

    def close_tab(self, idx: int) -> bool:
        if idx <= 0 or idx >= len(self.workspaces):
            return True
        ws = self.workspaces[idx]
        if ws.is_dirty():
            dlg = UnsavedChangesDialog(ws.tab_title().rstrip(" •"), self)
            if dlg.exec() != QDialog.Accepted or dlg.choice == UnsavedChangesDialog.CANCEL:
                return False
            if dlg.choice == UnsavedChangesDialog.SAVE and not ws.save():
                return False

        self._bind_auxiliary_panels(None)
        panels = self._ws_panel_map.pop(id(ws), None)
        if panels is not None:
            lw, cw, rw, bw = panels
            for stk, wdg in ((self.scene_stack, lw), (self.viewport_stack, cw),
                             (self.inspector_stack, rw), (self.timeline_stack, bw)):
                stk.removeWidget(wdg)
                wdg.setParent(None)
                wdg.deleteLater()

        ws.close_cleanup()
        self.workspaces.pop(idx)
        ws.setParent(None)
        ws.deleteLater()

        new_idx = min(self._active_idx, len(self.workspaces) - 1)
        if idx < self._active_idx:
            new_idx = max(0, self._active_idx - 1)
        self._active_idx = new_idx
        self._rebuild_tab_bar()
        self.switch_tab(new_idx)
        return True

    # =====================================================  Acciones Conectadas de Barra, Menús y CanvasDock
    def _call_video(self, method, *args):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            getattr(ws, method)(*args)
            self.layers_panel.refresh()

    def _call_vtuber2d(self, method, *args):
        ws = self.active_workspace()
        if isinstance(ws, VTuberStudioWorkspace):
            getattr(ws, method)(*args)

    def _call_video_tl(self, method, *args):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            getattr(ws.timeline, method)(*args)

    def _import_in_active(self, kind: str):
        ws = self.active_workspace()
        if ws is None or self._active_idx == 0:
            if kind == "vtr":
                self.open_project_dialog()
            elif kind == "3d":
                ws3 = self.create_project_tab({"kind": "vtuber3d", "name": "Mi VTuber 3D"})
                ws3.import_external_model_dialog()
            else:
                wsv = self.create_project_tab({"kind": "video", "name": "Nuevo Proyecto"})
                if kind == "image":
                    wsv.add_prop("image")
            return
        if isinstance(ws, VideoStudioWorkspace):
            if kind == "vtr":
                ws.import_vtr_character()
            elif kind == "3d":
                ws.import_vt3d_character()
            elif kind == "image":
                ws.add_prop("image")
            elif kind == "audio":
                tid = ws.sel or (ws.data["characters"][0]["id"] if ws.data["characters"] else None)
                if tid:
                    ws.browse_timeline_clip(tid, ws.mixer.playhead)
                else:
                    QMessageBox.information(self, "Añadir audio", "Crea primero un personaje para añadir audio a su pista.")
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.import_layers_dialog()
        elif isinstance(ws, VTuber3DWorkspace):
            ws.import_external_model_dialog()
        self.layers_panel.refresh()

    def _on_library_action(self, _cat: str, act):
        if isinstance(act, tuple):
            kind, val = act
            if kind == "emote":
                self._trigger_active_emote(val)
            elif kind == "sfx":
                play_soundboard_sfx(val)
                self.statusBar().showMessage(f"Soundboard SFX: {val}", 2000)
            return

        if act == "new_rig_2d":
            self.create_project_tab({"kind": "vtuber", "name": "Mi VTuber 2D", "starter_rig": True})
            return
        if act == "new_vtuber_3d":
            self.create_project_tab({"kind": "vtuber3d", "name": "Mi VTuber 3D", "preset": "anime_studio"})
            return

        ws = self.active_workspace()
        if not isinstance(ws, VideoStudioWorkspace):
            ws = self.create_project_tab({"kind": "video", "name": "Escena Estudio"})

        if act == "add_char_sprite":
            ws.add_char()
        elif act == "import_vtr":
            ws._attach_vtr_to_cur() if ws.cur() else ws.import_vtr_character()
        elif act == "prop_image":
            ws.add_prop("image")
        elif act == "prop_text":
            ws.add_prop("text")
        elif act == "prop_viz":
            ws.add_prop("visualizer")
        elif act == "add_acc":
            ws.add_accessory()
        elif act == "bg_dark":
            ws.data["bg_color"] = "#0E1014"
            ws.sdata()["bg_type"] = "color"
            ws.apply_scene()
        elif act == "bg_chroma":
            ws.data["bg_color"] = "#00b140"
            ws.sdata()["bg_type"] = "color"
            ws.apply_scene()
        elif act == "bg_alpha":
            ws.sdata()["bg_type"] = "transparent"
            ws.apply_scene()
        elif act == "bg_image":
            ws.set_background()
        elif act == "import_audio_clip":
            self._import_in_active("audio")
        self.layers_panel.refresh()

    def _on_canvas_dock_action(self, aid: str, on: bool):
        ws = self.active_workspace()
        if ws is None or self._active_idx == 0:
            return
        if aid == "select":
            self.canvas_dock.set_checked_silent("pan", False)
            if isinstance(ws, VideoStudioWorkspace):
                ws.a_edit.setChecked(True)
            elif isinstance(ws, VTuberStudioWorkspace):
                ws.mode_seg.set_index(1)
                ws._apply_studio_mode(1)
        elif aid == "pan":
            self.canvas_dock.set_checked_silent("select", False)
            if isinstance(ws, VideoStudioWorkspace):
                ws.a_edit.setChecked(False)
            elif isinstance(ws, VTuberStudioWorkspace):
                ws.mode_seg.set_index(0)
                ws._apply_studio_mode(0)
        elif aid == "calibrate":
            self._calibrate_active()
        elif aid == "emote":
            self._focus_panel("emotes")
        elif aid == "guides":
            if isinstance(ws, VideoStudioWorkspace):
                ws.a_guides.setChecked(on)
        elif aid == "chroma":
            self._toggle_chroma_active()
        elif aid == "webcam":
            self.tb_track.setChecked(on)
        elif aid == "mic":
            self.tb_mic.setChecked(on)

    def _on_tb_tracking(self, on: bool):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.a_webcam.setChecked(on)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.b_cam_bar.setChecked(on)
        elif isinstance(ws, VTuber3DWorkspace):
            ws.b_cam.setChecked(on)
        self.canvas_dock.set_checked_silent("webcam", on)
        if ws is not None and self._active_idx > 0:
            self._sync_toolbar_to_ws(ws)

    def _on_tb_mic(self, on: bool):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            for ch in ws.data["characters"]:
                ws.on_track_mute(ch["id"], not on)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.mic.gain = (ws.rig["audio"].get("gain", 100) / 100.0) if on else 0.0
        elif isinstance(ws, VTuber3DWorkspace):
            ws.mic.gain = (ws.data.get("audio", {}).get("gain", 100) / 100.0) if on else 0.0
        self.canvas_dock.set_checked_silent("mic", on)

    def _calibrate_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.face_tracker.recalibrate()
            for ch_a in ws.channels.values():
                ch_a.emotion.reset_calibration()
            self.statusBar().showMessage("Pose neutral y línea base vocal recalibradas", 3000)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws._recalib_voice()
            if ws.face_tracker.running:
                ws.face_tracker.recalibrate()
        elif isinstance(ws, VTuber3DWorkspace):
            ws.mic.emotion.reset_calibration()
            if ws.face_tracker.running:
                ws.face_tracker.recalibrate()
            self.statusBar().showMessage("Pose 3D y línea base vocal recalibradas", 3000)

    def _zoom_active(self, factor: float):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.view.zoom_by(factor)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.canvas.zoom = max(0.25, min(3.5, ws.canvas.zoom * factor))
            ws.canvas.update()
            self.zoom_btn.setText(f"{int(round(ws.canvas.zoom * 100))}% ▾")
        elif isinstance(ws, VTuber3DWorkspace):
            cam = ws.data.setdefault("camera", {})
            cam["distance"] = round(max(1.55, min(4.8, float(cam.get("distance", 2.95)) / factor)), 2)

    def _set_zoom_exact(self, scale_f: float):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.view.resetTransform()
            ws.view.scale(scale_f, scale_f)
            ws.view._user_zoom = True
            self.zoom_btn.setText(f"{int(round(scale_f * 100))}% ▾")
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.canvas.zoom = scale_f
            ws.canvas.update()
            self.zoom_btn.setText(f"{int(round(scale_f * 100))}% ▾")

    def _fit_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.view.fit_canvas()
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.canvas.fit_view()
            self.zoom_btn.setText("100% ▾")
        elif isinstance(ws, VTuber3DWorkspace):
            ws.set_camera_preset("bust")
            self.zoom_btn.setText("100% ▾")

    def _toggle_viewport_bg(self, dark_on: bool):
        self._viewport_light = not dark_on
        UI_PREFS["viewport_dark"] = bool(dark_on)
        save_ui_prefs(UI_PREFS)
        b, _ = self._tb_btns["vp_bg"]
        ic = "moon_fill" if dark_on else "moon"
        self._tb_btns["vp_bg"] = (b, ic)
        b.setIcon(icons.state_icon(ic, 17))
        for ws in self.workspaces[1:]:
            if isinstance(ws, VideoStudioWorkspace) and hasattr(ws.view, "set_viewport_light"):
                ws.view.set_viewport_light(self._viewport_light)

    def _toggle_live_presentation(self, on: bool):
        self._live_presentation = bool(on)
        for ctrl in (self.tb_live, self.act_live_mode):
            ctrl.blockSignals(True)
            ctrl.setChecked(self._live_presentation)
            ctrl.blockSignals(False)

        if self._live_presentation:
            self._live_mem = {pid: d.isVisible() for pid, d in self.docks.items() if not d._in_rail}
            self.flyout.close_flyout()
            for d in self.docks.values():
                d.hide()
            self.tab_strip.setVisible(False)
            self.statusBar().showMessage("Modo En Vivo activado (pulsa F10 o el botón Play para restaurar paneles)", 4000)
        else:
            self.tab_strip.setVisible(True)
            if self._active_idx > 0:
                for pid, vis in getattr(self, "_live_mem", {}).items():
                    if pid in self.docks and not self.docks[pid]._in_rail and vis:
                        self.docks[pid].restore_content()
                        self.docks[pid].show()

    def _toggle_chroma_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            cur = ws.sdata().get("bg_type", "color")
            if cur == "transparent":
                ws.sdata()["bg_type"] = "color"
                ws.data["bg_color"] = "#00b140"
                self.statusBar().showMessage("Fondo cambiado a Chroma Key Verde (#00B140)", 2500)
            elif ws.data.get("bg_color", "").lower() == "#00b140":
                ws.data["bg_color"] = "#0E1014"
                self.statusBar().showMessage("Fondo cambiado a Estudio Oscuro", 2500)
            else:
                ws.sdata()["bg_type"] = "transparent"
                self.statusBar().showMessage("Fondo cambiado a Transparente (Alfa)", 2500)
            ws.apply_scene()
            ws.load_inspector()
        elif isinstance(ws, VTuberStudioWorkspace):
            nxt = {"transparent": "chroma", "chroma": "dark", "dark": "transparent"}.get(ws.rig.get("bg_type", "transparent"), "transparent")
            ws.rig["bg_type"] = nxt
            ws.bg_cb.setCurrentIndex(max(0, ws.bg_cb.findData(nxt)))
            ws.canvas.update()
        elif isinstance(ws, VTuber3DWorkspace):
            nxt = {"studio": "chroma", "chroma": "transparent", "transparent": "studio"}.get(ws.data.get("bg_type", "studio"), "studio")
            ws.data["bg_type"] = nxt
            ws.bg_cb.setCurrentIndex(max(0, ws.bg_cb.findData(nxt)))
            ws.viewport.update()

    def _trigger_active_emote(self, kind: str):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ch = ws.cur() or (ws.data["characters"][0] if ws.data["characters"] else None)
            if ch:
                ws.trigger_emote(ch["id"], kind)
        elif isinstance(ws, (VTuberStudioWorkspace, VTuber3DWorkspace)):
            em_map = {"heart": "happy", "excl": "surprised", "star": "laugh", "sweat": "sad", "quest": "neutral", "confetti": "happy"}
            ws._set_manual_emotion(em_map.get(kind, "happy"))

    def _open_audio_panel(self, page_idx: int):
        self.audio_rack_panel.mode_seg.set_index(page_idx)
        self.audio_rack_panel._switch_page(page_idx)
        self._focus_panel("audio_fx")

    def _open_soundboard(self):
        self.library_panel._open_folder("soundboard")
        self._focus_panel("library")

    def _toggle_timeline_dock(self):
        if self._active_idx == 0:
            return
        d = self.docks["timeline"]
        self._toggle_panel_visibility("timeline", not d.isVisible())

    def _on_menu_guides(self, on: bool):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.a_guides.setChecked(on)
            self.canvas_dock.set_checked_silent("guides", on)

    def _duplicate_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace) and ws.sel:
            ws.duplicate_char(ws.sel)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.duplicate_layer()
        self.layers_panel.refresh()

    def _delete_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            if ws.sel_prop:
                ws.del_prop(ws.sel_prop)
            elif ws.sel:
                ws.del_char(ws.sel)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.delete_layer()
        self.layers_panel.refresh()

    def _toggle_play_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.toggle_timeline_play(not ws.mixer.playing)

    def _toggle_rec_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.toggle_tl_rec(ws.tl_rec is None)

    def _toggle_action(self, attr):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            act = getattr(ws, attr, None)
            if act:
                act.setChecked(not act.isChecked())

    def _mark_in_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.timeline.set_in_point(ws.mixer.playhead)

    def _mark_out_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.timeline.set_out_point(ws.mixer.playhead)

    def _hotkey_alt_num(self, idx):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.set_expr(idx)
        elif isinstance(ws, VTuberStudioWorkspace):
            ws._set_expr(idx)

    def _hotkey_vtuber_toggle(self, digit):
        if self._typing():
            return
        ws = self.active_workspace()
        if isinstance(ws, VTuberStudioWorkspace):
            togs = ws.rig.setdefault("toggles", {})
            togs[digit] = not togs.get(digit, False)
            ws._refresh_expr_and_toggles()
            ws.canvas.update()
        elif isinstance(ws, VTuber3DWorkspace):
            ws.toggle_hotkey(digit)

    def save_active(self):
        ws = self.active_workspace()
        if ws and hasattr(ws, "save"):
            ws.save()

    def save_active_as(self):
        ws = self.active_workspace()
        if ws and hasattr(ws, "save_as"):
            ws.save_as()

    def export_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.open_export()
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.export_12_sprites()
        elif isinstance(ws, VTuber3DWorkspace):
            ws.export_png()

    def _screenshot_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.screenshot()
        elif isinstance(ws, VTuber3DWorkspace):
            ws.export_png()
        elif isinstance(ws, VTuberStudioWorkspace):
            ws.export_12_sprites()

    def _open_obs_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.open_output()
        elif isinstance(ws, (VTuberStudioWorkspace, VTuber3DWorkspace)):
            ws.open_obs_window()

    def _toggle_vcam_active(self):
        ws = self.active_workspace()
        if isinstance(ws, VideoStudioWorkspace):
            ws.toggle_virtual_cam()
        else:
            QMessageBox.information(self, "Cámara Virtual", "Abre o envía tu avatar a una pestaña de Escena Video para emitir por Cámara Virtual.")

    def open_preferences(self, start_page: int = 0):
        ws = self.active_workspace()
        vid_ws = ws if isinstance(ws, VideoStudioWorkspace) else None
        SettingsDialog(vid_ws, start_page=start_page, main_win=self).exec()

    def open_shortcuts_dialog(self):
        ShortcutsDialog(self).exec()

    def _on_load_progress(self, done, total):
        if total > 0:
            self.load_prog.setVisible(True)
            self.load_prog.setRange(0, total)
            self.load_prog.setValue(done)
        else:
            self.load_prog.setVisible(False)

    def _show_diagnostics(self):
        from export_engine import detect_gpu_encoder
        _, gpu = detect_gpu_encoder()
        QMessageBox.information(
            self, "Diagnóstico del Estudio",
            f"• Tema activo: {'Dark Studio' if theme.is_dark() else 'Light Studio'}\n"
            f"• Densidad: {'Compacta (<=1480×860)' if DENSITY['compact'] else 'Estándar'}\n"
            f"• Codificador de hardware: {gpu}\n"
            f"• Pestañas activas: {len(self.workspaces) - 1}",
        )

    def _show_about(self):
        QMessageBox.about(
            self, "PodcastSet Studio",
            "PodcastSet Studio — Edición Studio UI\n\n"
            "Suite multipestaña de avatares VTuber 2D & 3D, tracking y audio:\n"
            "• Avatares 2D PNGTuber y Rigging 2.5D con física (.vtr)\n"
            "• Avatares 3D en tiempo real (.vt3d / .vrm / .glb / .obj)\n"
            "• Tracking por Webcam OpenCV, Voz, 7 Emociones y Discord\n"
            "• Línea de tiempo multipista, Mezclador, Rack DSP y Streaming OBS",
        )

    def _on_theme_changed(self, _mode: str):
        icons.clear_cache()
        self.app_icon_lbl.setPixmap(icons.pixmap("app_logo", C["dim"], 17))
        for b, ic in self._tb_btns.values():
            b.setIcon(icons.state_icon(ic, 17))
        self.b_tab_add.setIcon(icons.state_icon("plus", 15))
        self._build_menus()
        self._rebuild_tab_bar()
        self.init_native_frameless()

    # =====================================================  Tick Maestro 60 FPS
    def _tick(self):
        cur_ms = self._clock.elapsed()
        dt = max(0.004, min(0.05, (cur_ms - self._last_ms) / 1000.0)) if self._last_ms else 0.016
        self._last_ms = cur_ms
        now = time.time()

        self._fps_acc += dt
        self._fps_cnt += 1
        if self._fps_acc >= 0.8:
            self.stat_fps_chip.setText(f"{int(round(self._fps_cnt / self._fps_acc))} FPS")
            self._fps_acc = 0.0
            self._fps_cnt = 0

        ws = self.active_workspace()
        if ws is not None and hasattr(ws, "tick"):
            ws.tick(dt, now)
            if self._fps_cnt % 2 == 0:
                self.audio_rack_panel.tick_levels()
                self.tracking_panel.tick_telemetry()

    def closeEvent(self, e):
        for idx in range(len(self.workspaces) - 1, 0, -1):
            if not self.close_tab(idx):
                e.ignore()
                return
        e.accept()
