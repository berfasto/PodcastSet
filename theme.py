"""Sistema de temas 'Studio UI' (Dark Studio + Light Studio), densidad responsiva,
persistencia de preferencias visuales, QPalette sincronizada y QSS global exacto."""
import json
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

UI_PREFS_FILE = Path(__file__).resolve().parent / "studio_ui_prefs.json"

DARK_STUDIO = dict(
    mode="dark",
    bg="#0D0F12",
    canvas="#0E1014",
    canvas_bg="#0E1014",
    panel="#14171C",
    bg2="#14171C",
    input="#181C22",
    surface="#1B1F26",
    panel2="#1B1F26",
    raised="#1B1F26",
    card="#1B1F26",
    hover="#242933",
    card2="#242933",
    card_hover="#242933",
    border="#262C36",
    border_subtle="#1E232B",
    border_light="#323A47",
    text="#F0F2F6",
    dim="#A1A9B8",
    text_dim="#A1A9B8",
    muted="#697284",
    accent="#6366F1",
    accent_hover="#7578F5",
    accent_pressed="#4F46E5",
    selection_bg="rgba(99, 102, 241, 0.16)",
    accent_soft="#1D203A",
    selection_text="#A5B4FC",
    slider_fill="rgba(99, 102, 241, 0.28)",
    danger_close="#E81123",
    dialog_close="#C42B1C",
    invalid="#E5484D",
    alert="#E5484D",
    danger="#E5484D",
    accent2="#2EB67D",
    warn="#F59E0B",
)

LIGHT_STUDIO = dict(
    mode="light",
    bg="#ECEEF2",
    canvas="#E4E7ED",
    canvas_bg="#E4E7ED",
    panel="#F7F8FA",
    bg2="#F7F8FA",
    input="#FFFFFF",
    surface="#FFFFFF",
    panel2="#FFFFFF",
    raised="#FFFFFF",
    card="#FFFFFF",
    hover="#E8EBF1",
    card2="#E8EBF1",
    card_hover="#E8EBF1",
    border="#D8DCE5",
    border_subtle="#E4E7EE",
    border_light="#C4CAD8",
    text="#141821",
    dim="#525B6C",
    text_dim="#525B6C",
    muted="#8A93A5",
    accent="#4F46E5",
    accent_hover="#6366F1",
    accent_pressed="#4338CA",
    selection_bg="rgba(79, 70, 229, 0.12)",
    accent_soft="#E3E4F8",
    selection_text="#4F46E5",
    slider_fill="rgba(79, 70, 229, 0.18)",
    danger_close="#E81123",
    dialog_close="#C42B1C",
    invalid="#E5484D",
    alert="#E5484D",
    danger="#E5484D",
    accent2="#10B981",
    warn="#F59E0B",
)

# Compatibilidad con proyectos .setxd guardados con claves de acento previas
ACCENT_PRESETS = {
    "indigo": ("Índigo Studio", "#6366F1", "#7578F5", "#1D203A"),
    "cobalt": ("Índigo Studio", "#6366F1", "#7578F5", "#1D203A"),
    "amber": ("Ámbar Broadcast", "#D97706", "#F59E0B", "#312310"),
    "emerald": ("Verde Señal", "#1F9D68", "#2EB67D", "#122B22"),
    "slate": ("Monocromo", "#6366F1", "#7578F5", "#1D203A"),
}

C = dict(DARK_STUDIO)

DENSITY = dict(
    compact=False,
    base_pt=9.0,
    sub_pt=8.2,
    title_h=35,
    tool_btn=26,
    canvas_btn=28,
    tab_min_w=112,
    wheel_size=152,
    flyout_w=256,
    dock_left_w=218,
    dock_right_w=286,
)

DEFAULT_PALETTES = [
    {
        "name": "Estudio",
        "colors": [
            "#6366F1", "#818CF8", "#A5B4FC", "#38BDF8", "#2EB67D", "#F59E0B",
            "#E5484D", "#EC4899", "#A855F7", "#F0F2F6", "#697284", "#0D0F12",
        ],
    },
    {
        "name": "Iluminación Escena",
        "colors": [
            "#FFF4E6", "#FFE0B2", "#FFD166", "#FF8FA3", "#C77DFF", "#72EFDD",
            "#4EA8DE", "#5E60CE", "#F8F9FA", "#343A40",
        ],
    },
    {
        "name": "Chroma y Neón",
        "colors": [
            "#00B140", "#00FF00", "#0047BB", "#FF00FF", "#00F5D4", "#FEE440",
            "#F15BB5", "#9B5DE5", "#00BBF9", "#FB5607",
        ],
    },
    {
        "name": "Neutros",
        "colors": [
            "#FFFFFF", "#ECEEF2", "#D8DCE5", "#A1A9B8", "#697284", "#323A47",
            "#262C36", "#1B1F26", "#14171C", "#0D0F12",
        ],
    },
]


class _ThemeBus(QObject):
    changed = Signal(str)  # "dark" | "light"


bus = _ThemeBus()


def load_ui_prefs():
    base = {
        "theme_mode": "dark",
        "language": "es",
        "viewport_dark": True,
        "swatch_size": 24,
        "active_palette": 0,
        "palettes": [dict(p, colors=list(p["colors"])) for p in DEFAULT_PALETTES],
        "rail_panels": ["color", "palette", "tracking", "audio_fx", "emotes", "output"],
        "shortcuts": {},
        "workspace_preset": "default",
    }
    if UI_PREFS_FILE.exists():
        try:
            raw = json.loads(UI_PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base.update(raw)
        except Exception:
            pass
    if not base.get("palettes"):
        base["palettes"] = [dict(p, colors=list(p["colors"])) for p in DEFAULT_PALETTES]
    return base


def save_ui_prefs(prefs):
    try:
        UI_PREFS_FILE.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


UI_PREFS = load_ui_prefs()


def is_dark():
    return C.get("mode", "dark") == "dark"


def selection_qcolor():
    return QColor(99, 102, 241, 41) if is_dark() else QColor(79, 70, 229, 31)


def detect_density(app=None):
    compact = False
    try:
        scr = (app or QGuiApplication.instance()).primaryScreen()
        if scr is not None:
            avail = scr.availableGeometry()
            compact = avail.width() <= 1480 or avail.height() <= 860
    except Exception:
        compact = False
    DENSITY.update(
        compact=compact,
        base_pt=8.2 if compact else 9.0,
        sub_pt=7.6 if compact else 8.2,
        title_h=30 if compact else 35,
        tool_btn=24 if compact else 26,
        canvas_btn=26 if compact else 28,
        tab_min_w=86 if compact else 112,
        wheel_size=132 if compact else 152,
        flyout_w=236 if compact else 256,
        dock_left_w=184 if compact else 218,
        dock_right_w=236 if compact else 286,
    )
    return DENSITY


def set_theme_tokens(mode="dark"):
    src = LIGHT_STUDIO if mode == "light" else DARK_STUDIO
    C.clear()
    C.update(src)


def set_accent(preset_key="indigo"):
    """Mantiene compatibilidad sin romper los tokens exactos de Dark/Light Studio."""
    if preset_key in ("amber", "emerald") and preset_key in ACCENT_PRESETS and is_dark():
        _, acc, acc_h, acc_s = ACCENT_PRESETS[preset_key]
        C["accent"] = acc
        C["accent_hover"] = acc_h
        C["accent_soft"] = acc_s


def build_qss():
    b_pt = DENSITY["base_pt"]
    s_pt = DENSITY["sub_pt"]
    tab_min = DENSITY["tab_min_w"]
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
    font-size: {b_pt}pt;
}}
QMainWindow, QDialog, QWidget#root {{
    background: {C['bg']};
    color: {C['text']};
}}
QWidget {{
    color: {C['text']};
}}
QLabel {{
    background: transparent;
}}
QToolTip {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['border']};
    padding: 4px 7px;
    border-radius: 4px;
    font-size: 8.5pt;
}}

/* Jerarquía tipográfica Studio */
QLabel#welcomeTitle {{
    font-size: 20pt;
    font-weight: 600;
    letter-spacing: -0.3px;
    color: {C['text']};
}}
QLabel#cardTitle {{
    font-size: 10.5pt;
    font-weight: 600;
    color: {C['text']};
}}
QLabel#headerTitle, QLabel#h2 {{
    font-size: 10pt;
    font-weight: 600;
    color: {C['text']};
}}
QLabel#h1 {{
    font-size: 10.5pt;
    font-weight: 600;
    letter-spacing: -0.2px;
    color: {C['text']};
}}
QLabel#preferencesDialogTitle, QLabel#confirmTitle {{
    font-size: 9.5pt;
    font-weight: 600;
    color: {C['text']};
}}
QLabel#fieldLabel {{
    color: {C['dim']};
    font-size: 8pt;
}}
QLabel#mutedLabel, QLabel[muted="true"] {{
    color: {C['muted']};
    font-size: 8pt;
}}
QLabel#section, QLabel#sectionLabel {{
    color: {C['muted']};
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 0.75px;
    text-transform: uppercase;
}}
QLabel#badge {{
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 7.6pt;
    font-weight: 600;
    color: {C['dim']};
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 2px 7px;
    max-height: 22px;
}}
QLabel#timecode {{
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 8.5pt;
    font-weight: 600;
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 2px 8px;
    color: {C['text']};
}}

/* Chrome Superior (3 franjas exactas) */
QWidget#chrome {{ background: {C['panel']}; }}
QWidget#titleBar {{
    background: {C['panel']};
    border: none;
    border-bottom: 1px solid {C['border_subtle']};
}}
QMenuBar {{
    background: transparent;
    color: {C['dim']};
    border: none;
    padding: 0px;
    spacing: 2px;
}}
QMenuBar::item {{
    background: transparent;
    color: {C['dim']};
    padding: 5px 10px;
    margin: 2px 1px;
    border-radius: 6px;
    font-weight: 500;
}}
QMenuBar::item:selected {{
    background: {C['hover']};
    color: {C['text']};
}}
QMenuBar::item:pressed {{
    background: {C['selection_bg']};
    color: {C['selection_text']};
}}
QMenu {{
    background: {C['panel']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 5px;
}}
QMenu::item {{
    background: transparent;
    color: {C['text']};
    padding: 6px 26px 6px 10px;
    margin: 1px 0px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {C['selection_bg']};
    color: {C['selection_text']};
}}
QMenu::item:disabled {{
    color: {C['muted']};
}}
QMenu::separator {{
    height: 1px;
    background: {C['border_subtle']};
    margin: 5px 8px;
}}
QMenu::indicator {{
    width: 13px;
    height: 13px;
    margin-left: 6px;
    border-radius: 3px;
    border: 1px solid {C['border']};
    background: {C['input']};
}}
QMenu::indicator:checked {{
    background: {C['accent']};
    border-color: {C['accent']};
}}

QWidget#toolbarRow, QFrame#toolbar, QFrame#tlbar {{
    background: {C['panel']};
    border: none;
    border-bottom: 1px solid {C['border_subtle']};
}}
QToolButton#toolbarButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 0px;
    color: {C['dim']};
}}
QToolButton#toolbarButton:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#toolbarButton:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
}}
QToolButton#toolbarButton:disabled {{
    color: {C['muted']};
    background: transparent;
}}
QFrame#toolSeparator {{
    background: {C['border_subtle']};
    border: none;
}}

/* Franja 3: Barra de pestañas de escenas/proyectos */
QWidget#tabStrip {{
    background: {C['bg']};
    border: none;
    border-bottom: 1px solid {C['border']};
}}
QTabBar#projectTabBar {{
    background: transparent;
    border: none;
}}
QTabBar#projectTabBar::tab {{
    background: transparent;
    color: {C['dim']};
    padding: 6px 12px;
    border-right: 1px solid {C['border_subtle']};
    border-top: 2px solid transparent;
    min-width: {tab_min}px;
    font-size: {s_pt}pt;
    font-weight: 500;
}}
QTabBar#projectTabBar::tab:first {{
    min-width: 18px;
    max-width: 38px;
    padding: 6px 9px;
}}
QTabBar#projectTabBar::tab:selected {{
    background: {C['panel']};
    color: {C['text']};
    border-top: 2px solid {C['accent']};
}}
QTabBar#projectTabBar::tab:hover:!selected {{
    color: {C['text']};
    background: {C['hover']};
}}
QToolButton#tabClose {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 0px;
}}
QToolButton#tabClose:hover {{
    background: {C['hover']};
}}
QToolButton#tabAddButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 0px;
    color: {C['dim']};
}}
QToolButton#tabAddButton:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#tabAddButton::menu-indicator {{
    image: none;
    width: 0px;
}}

/* Pestañas internas (Inspector / Docks tabificados) */
QTabWidget::pane {{
    border: none;
    background: {C['panel']};
}}
QTabBar::tab {{
    background: transparent;
    color: {C['dim']};
    padding: 5px 10px;
    border-right: 1px solid {C['border_subtle']};
    border-top: 2px solid transparent;
    font-size: {s_pt}pt;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    background: {C['panel']};
    color: {C['text']};
    border-top: 2px solid {C['accent']};
}}
QTabBar::tab:hover:!selected {{
    color: {C['text']};
    background: {C['hover']};
}}

/* Pantalla de Inicio (HomePage) */
QWidget#homePage {{
    background: {C['bg']};
}}
QFrame#creationCard {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 6px;
}}
QFrame#creationCard:hover, QFrame#creationCard[selected="true"] {{
    background: {C['hover']};
    border: 1px solid {C['accent']};
}}
QFrame#recentCard {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 6px;
}}
QFrame#recentCard:hover {{
    background: {C['hover']};
    border: 1px solid {C['accent']};
}}
QFrame#hintBox {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 5px;
}}

/* Docks + PanelRail + RailFlyout + CanvasDock */
QDockWidget {{
    background: {C['panel']};
    border: none;
}}
QDockWidget[isFloating="true"] {{
    background: {C['panel']};
    border: 1px solid {C['border']};
}}
QFrame#dockTitleBar {{
    background: {C['panel']};
    border: none;
    border-bottom: 1px solid {C['border_subtle']};
}}
QLabel#dockTitleLabel {{
    color: {C['muted']};
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 0.7px;
    text-transform: uppercase;
}}
QToolButton#dockActionButton {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 2px;
    color: {C['dim']};
}}
QToolButton#dockActionButton:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QMainWindow::separator {{
    background: {C['border']};
    width: 1px;
    height: 1px;
}}
QMainWindow::separator:hover {{
    background: {C['accent']};
}}
QSplitter::handle {{
    background: {C['border']};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {C['accent']}; }}

QFrame#panelRail {{
    background: {C['panel']};
    border-left: 1px solid {C['border_subtle']};
    border-right: 1px solid {C['border_subtle']};
}}
QFrame#panelRail[dropTarget="true"] {{
    background: {C['selection_bg']};
    border-left: 2px solid {C['accent']};
}}
QToolButton#railButton {{
    padding: 0px;
    border-radius: 4px;
    border: 1px solid transparent;
    background: transparent;
    color: {C['dim']};
}}
QToolButton#railButton:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#railButton:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
}}
QToolButton#railButton::menu-indicator {{
    image: none;
    width: 0px;
}}
QFrame#railFlyout {{
    background: {C['panel']};
    border: 1px solid {C['border']};
    border-radius: 5px;
}}
QWidget#railFlyoutHeader {{
    background: {C['panel']};
    border-bottom: 1px solid {C['border_subtle']};
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
}}
QFrame#dockDropGuide {{
    background: {C['selection_bg']};
    border: 2px solid {C['accent']};
    border-radius: 5px;
}}

QFrame#canvasDock {{
    background: {C['panel']};
    border: 1px solid {C['border']};
    border-radius: 10px;
}}
QFrame#canvasDockDivider {{
    background: {C['border']};
    border: none;
}}
QToolButton#canvasDockButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0px;
    color: {C['dim']};
}}
QToolButton#canvasDockButton:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#canvasDockButton:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
}}

/* Contenedores y paneles internos */
QWidget#panel, QFrame#footer {{
    background: {C['panel']};
}}
QFrame#footer {{
    border-top: 1px solid {C['border_subtle']};
}}
QFrame#timeline {{
    background: {C['bg']};
    border-top: 1px solid {C['border']};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QFrame#propCard, QFrame#card, QFrame#dspBlock {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 5px;
}}
QFrame#card:hover, QFrame#folderTile:hover {{
    background: {C['hover']};
    border-color: {C['accent']};
}}
QFrame#card[sel="true"] {{
    background: {C['selection_bg']};
    border: 1px solid {C['accent']};
}}
QPushButton#folderTile, QToolButton#folderTile {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 5px;
    padding: 6px 8px;
    text-align: left;
}}
QPushButton#folderTile:hover, QToolButton#folderTile:hover {{
    background: {C['hover']};
    border-color: {C['accent']};
}}

/* Botones generales y variantes Studio */
QPushButton, QToolButton {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 5px 11px;
    color: {C['text']};
    font-size: 8.5pt;
}}
QPushButton:hover, QToolButton:hover {{
    border-color: {C['accent']};
    background: {C['hover']};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: {C['selection_bg']};
    border-color: {C['accent_pressed']};
    color: {C['selection_text']};
}}
QPushButton:checked, QToolButton:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {C['muted']};
    background: {C['panel']};
    border-color: {C['border_subtle']};
}}

QPushButton#primary, QPushButton#primaryButton, QPushButton#confirmPrimary {{
    background: {C['accent']};
    border: 1px solid {C['accent']};
    color: #FFFFFF;
    font-weight: 600;
    border-radius: 4px;
}}
QPushButton#primary:hover, QPushButton#primaryButton:hover, QPushButton#confirmPrimary:hover {{
    background: {C['accent_hover']};
    border-color: {C['accent_hover']};
}}
QPushButton#primary:pressed, QPushButton#primaryButton:pressed, QPushButton#confirmPrimary:pressed {{
    background: {C['accent_pressed']};
    border-color: {C['accent_pressed']};
}}
QPushButton#confirmGhost, QPushButton#modalBtn {{
    background: transparent;
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 5px 12px;
    color: {C['text']};
}}
QPushButton#confirmGhost:hover, QPushButton#modalBtn:hover {{
    background: {C['hover']};
    border-color: {C['accent']};
}}
QPushButton#flat, QToolButton#flat {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 6px;
    color: {C['dim']};
}}
QPushButton#flat:hover, QToolButton#flat:hover {{
    background: {C['hover']};
    border-color: {C['border']};
    color: {C['text']};
}}
QPushButton#flat:checked, QToolButton#flat:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
}}
QPushButton#drawerToggle {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 0px 8px;
    font-size: 8pt;
    color: {C['dim']};
    text-align: left;
}}
QPushButton#drawerToggle:hover {{
    background: {C['hover']};
    color: {C['text']};
}}

QPushButton#choicePill, QPushButton#expr_pill, QPushButton#scene {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 3px 9px;
    color: {C['dim']};
    font-size: 8pt;
    font-weight: 500;
}}
QPushButton#choicePill:hover, QPushButton#expr_pill:hover, QPushButton#scene:hover {{
    background: {C['hover']};
    border-color: {C['accent']};
    color: {C['text']};
}}
QPushButton#choicePill:checked, QPushButton#expr_pill:checked, QPushButton#scene:checked {{
    background: {C['selection_bg']};
    border: 1px solid {C['accent']};
    color: {C['selection_text']};
    font-weight: 600;
}}

QPushButton#rec, QToolButton#rec {{
    font-weight: 600;
    padding: 3px 10px;
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 4px;
}}
QPushButton#rec:hover, QToolButton#rec:hover {{
    border-color: {C['invalid']};
}}
QPushButton#rec:checked, QToolButton#rec:checked {{
    background: {C['invalid']};
    border-color: {C['invalid']};
    color: #FFFFFF;
}}

/* Botones Mezclador M / S / FX (22x22) */
QToolButton#mixMute, QToolButton#mixSolo, QToolButton#mixFx {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 0px;
    font-size: 7.5pt;
    font-weight: 700;
    color: {C['dim']};
}}
QToolButton#mixMute:hover, QToolButton#mixSolo:hover, QToolButton#mixFx:hover {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#mixMute:checked {{
    background: {C['invalid']};
    border-color: {C['invalid']};
    color: #FFFFFF;
}}
QToolButton#mixSolo:checked {{
    background: #F59E0B;
    border-color: #F59E0B;
    color: #0D0F12;
}}
QToolButton#mixFx:checked {{
    background: {C['accent']};
    border-color: {C['accent']};
    color: #FFFFFF;
}}

/* Section / Acordeón y SegmentedChoice */
QPushButton#sectionHeader, QPushButton#secHdr {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {C['border_subtle']};
    border-radius: 0px;
    padding: 5px 2px;
    text-align: left;
    color: {C['muted']};
    font-size: 8pt;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QPushButton#sectionHeader:hover, QPushButton#secHdr:hover {{
    color: {C['text']};
    background: transparent;
}}

QWidget#segmentedChoice {{
    background: {C['bg']};
    border: 1px solid {C['border']};
    border-radius: 4px;
}}
QToolButton#segmentedChoiceButton, QPushButton#segmentedChoiceButton {{
    background: transparent;
    color: {C['dim']};
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 8pt;
    font-weight: 500;
}}
QToolButton#segmentedChoiceButton:hover:!checked, QPushButton#segmentedChoiceButton:hover:!checked {{
    background: {C['hover']};
    color: {C['text']};
}}
QToolButton#segmentedChoiceButton:checked, QPushButton#segmentedChoiceButton:checked {{
    background: {C['selection_bg']};
    border-color: {C['accent']};
    color: {C['selection_text']};
    font-weight: 600;
}}

/* Inputs, SpinBoxes (sin flechas), Combos y Listas */
QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox, QKeySequenceEdit {{
    background: {C['input']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 2px 6px;
    min-height: 20px;
    font-size: 8.5pt;
    selection-background-color: {C['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {{
    border-color: {C['accent']};
}}
QLineEdit#hexInput {{
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 8.5pt;
}}
QLineEdit[invalid="true"] {{
    border: 1px solid {C['invalid']};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px;
    height: 0px;
    border: none;
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background: {C['panel']};
    color: {C['text']};
    selection-background-color: {C['selection_bg']};
    selection-color: {C['selection_text']};
    border: 1px solid {C['border']};
    border-radius: 5px;
    outline: none;
    padding: 4px;
}}

QListWidget, QTreeWidget {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 3px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 4px 6px;
    border-radius: 4px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {C['selection_bg']};
    color: {C['selection_text']};
}}
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected {{
    background: {C['hover']};
}}

/* Sliders y Faders */
QSlider::groove:horizontal {{
    height: 3px;
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 1.5px;
}}
QSlider::handle:horizontal {{
    background: {C['accent']};
    border: 1px solid {C['accent']};
    width: 10px;
    height: 10px;
    margin: -5px 0;
    border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{
    background: {C['accent_hover']};
    border-color: {C['accent_hover']};
}}
QSlider::sub-page:horizontal {{
    background: {C['slider_fill']};
    border-radius: 1.5px;
}}
QSlider::groove:vertical {{
    width: 3px;
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 1.5px;
}}
QSlider::handle:vertical {{
    background: {C['accent']};
    border: 1px solid {C['accent']};
    height: 10px;
    width: 10px;
    margin: 0 -5px;
    border-radius: 6px;
}}
QSlider::add-page:vertical {{
    background: {C['slider_fill']};
    border-radius: 1.5px;
}}

/* Scrollbars ultrafinos (7px) */
QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 1px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C['muted']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 7px;
    margin: 1px;
}}
QScrollBar::handle:horizontal {{
    background: {C['border']};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C['muted']};
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    width: 0px;
    height: 0px;
    background: none;
}}

/* CheckBox y ProgressBar */
QCheckBox {{
    spacing: 7px;
    font-size: 8.5pt;
    color: {C['text']};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
QProgressBar {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background: {C['accent']};
    border-radius: 2px;
}}

/* GroupBox, Barra de Estado y Diálogos Modales (Configuración / Confirmación / Exportar) */
QGroupBox {{
    border: 1px solid {C['border']};
    border-radius: 5px;
    margin-top: 10px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
    font-size: 8.5pt;
    background: transparent;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {C['muted']};
}}

QStatusBar {{
    background: {C['panel']};
    color: {C['dim']};
    border-top: 1px solid {C['border_subtle']};
    min-height: 24px;
    font-size: 8pt;
}}
QStatusBar::item {{ border: none; }}
QLabel#statusChip {{
    color: {C['dim']};
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 3px;
    padding: 1px 7px;
    font-size: 7.8pt;
}}
QToolButton#statusButton {{
    color: {C['dim']};
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 1px 7px;
    font-size: 7.8pt;
    font-family: "Consolas", "Cascadia Mono", monospace;
}}
QToolButton#statusButton:hover {{
    background: {C['hover']};
    color: {C['text']};
    border-color: {C['border']};
}}
QToolButton#statusButton::menu-indicator {{
    image: none;
    width: 0px;
}}

QFrame#preferencesFrame, QFrame#modalShell {{
    background: {C['bg']};
    border: 1px solid {C['border']};
    border-radius: 8px;
}}
QFrame#preferencesTitleBar, QFrame#modalHeader {{
    background: {C['panel']};
    border: none;
    border-bottom: 1px solid {C['border_subtle']};
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QFrame#modalFooter {{
    background: {C['panel']};
    border: none;
    border-top: 1px solid {C['border_subtle']};
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QToolButton#preferencesCloseButton, QPushButton#preferencesCloseButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
    color: {C['dim']};
}}
QToolButton#preferencesCloseButton:hover, QPushButton#preferencesCloseButton:hover {{
    background: {C['dialog_close']};
    color: #FFFFFF;
}}
QFrame#settingsNavigation, QFrame#settingsSidebar {{
    background: {C['panel']};
    border-right: 1px solid {C['border']};
}}
QPushButton#navItem {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 4px;
    padding: 7px 10px;
    text-align: left;
    color: {C['dim']};
    font-size: 8.5pt;
    font-weight: 500;
}}
QPushButton#navItem:hover:!checked {{
    background: {C['hover']};
    color: {C['text']};
}}
QPushButton#navItem:checked {{
    background: {C['selection_bg']};
    border-left: 2px solid {C['accent']};
    color: {C['selection_text']};
    font-weight: 600;
}}
QFrame#themePresetCard {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 5px;
}}
QFrame#themePresetCard:hover {{
    background: {C['hover']};
    border-color: {C['accent']};
}}
QFrame#themePresetCard[selected="true"] {{
    background: {C['selection_bg']};
    border: 2px solid {C['accent']};
}}

QFrame#confirmFrame {{
    background: {C['panel']};
    border: 1px solid {C['border']};
    border-radius: 6px;
}}
"""


class StudioProxyStyle(QProxyStyle):
    """Estilo Fusion + sin subrayados visuales en mnemónicos + checkboxes nítidos en accent."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, returnData)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_IndicatorCheckBox:
            r = option.rect.adjusted(1, 1, -1, -1)
            checked = bool(option.state & QStyle.State_On)
            hovered = bool(option.state & QStyle.State_MouseOver)
            enabled = bool(option.state & QStyle.State_Enabled)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            if checked:
                bg = QColor(C["accent"] if enabled else C["muted"])
                border = bg
            else:
                bg = QColor(C["input"] if enabled else C["panel"])
                border = QColor(C["accent"] if hovered and enabled else C["border"])
            painter.setBrush(bg)
            painter.setPen(QPen(border, 1.1))
            painter.drawRoundedRect(r, 3, 3)
            if checked:
                pen = QPen(QColor("#FFFFFF"), 1.85, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                x0, y0, w, h = r.x(), r.y(), r.width(), r.height()
                painter.drawLine(QPointF(x0 + w * 0.23, y0 + h * 0.52), QPointF(x0 + w * 0.43, y0 + h * 0.73))
                painter.drawLine(QPointF(x0 + w * 0.43, y0 + h * 0.73), QPointF(x0 + w * 0.78, y0 + h * 0.28))
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)


def apply(app=None, accent=None, mode=None, persist=False):
    app = app or QApplication.instance()
    if app is None:
        return
    if mode is None:
        mode = UI_PREFS.get("theme_mode", "dark")
    set_theme_tokens(mode)
    if accent:
        set_accent(accent)
    detect_density(app)

    if persist:
        UI_PREFS["theme_mode"] = mode
        save_ui_prefs(UI_PREFS)

    app.setStyle(StudioProxyStyle("Fusion"))
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(C["bg"]))
    pal.setColor(QPalette.WindowText, QColor(C["text"]))
    pal.setColor(QPalette.Base, QColor(C["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(C["surface"]))
    pal.setColor(QPalette.ToolTipBase, QColor(C["surface"]))
    pal.setColor(QPalette.ToolTipText, QColor(C["text"]))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(C["muted"]))
    pal.setColor(QPalette.Button, QColor(C["surface"]))
    pal.setColor(QPalette.ButtonText, QColor(C["text"]))
    pal.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    pal.setColor(QPalette.Highlight, QColor(C["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(C["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(C["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(build_qss())
    bus.changed.emit(mode)
