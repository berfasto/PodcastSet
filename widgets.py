"""Controles reutilizables de 'Studio UI':
- Section / CollapsibleSection, SegmentedChoice / Segmented, ClickableSlider.
- NumericScrubLabel + enable_numeric_drag (spinboxes sin flechas con arrastre horizontal 1 step cada 4px).
- VectorKnob (perilla rotatoria vectorial #6366F1 / #262C36).
- LayersPanel + _LayerDelegate (filas de 34px, barra acento 3px, ojo, guías verticales, pill contador y candado).
- ToolPalette + ElementPreviewPopup (biblioteca de Avatares, Props, Expresiones y Soundboard con preview 200ms).
- ColorWheelDialog modal frameless usando ColorWheel(216) + Gotero 108px + Paleta."""
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QFrame,
                               QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStyledItemDelegate, QStyle, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import icons
import model as M
import theme
from assets_gfx import guide_pixmap
from color_studio import ColorEditor, ColorWheel
from theme import C
from vtuber_engine import SHARED_ASSETS

STATE_COLORS = ["#697284", "#2EB67D", "#F59E0B", "#E5484D"]


# =====================================================  Sliders y Campos con Arrastre Numérico
class ClickableSlider(QSlider):
    """Clic directo en cualquier punto de la pista (3px, input, slider_fill, handle circular 12x12 en accent)."""

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._jump_to(e.position())
            e.accept()
        super().mousePressEvent(e)

    def _jump_to(self, pos: QPointF):
        lo, hi = self.minimum(), self.maximum()
        if hi <= lo:
            return
        if self.orientation() == Qt.Horizontal:
            m = 6.0
            span = max(1.0, self.width() - 2 * m)
            ratio = max(0.0, min(1.0, (pos.x() - m) / span))
        else:
            m = 6.0
            span = max(1.0, self.height() - 2 * m)
            ratio = max(0.0, min(1.0, 1.0 - (pos.y() - m) / span))
        val = int(round(lo + ratio * (hi - lo)))
        if val != self.value():
            self.setValue(val)


class NumericScrubLabel(QLabel):
    """Etiqueta de campo (8pt dim) con arrastre horizontal para ajustar numéricamente 1 step cada 4px."""
    scrubbed = Signal(int)

    def __init__(self, text="", spinbox=None, fixed_w=None, parent=None):
        super().__init__(text, parent)
        self.setObjectName("fieldLabel")
        self.setCursor(Qt.SizeHorCursor)
        self._spin = spinbox
        self._drag_x = None
        self._acc = 0.0
        if fixed_w:
            self.setFixedWidth(fixed_w)

    def set_spinbox(self, sp):
        self._spin = sp

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x = e.globalPosition().x()
            self._acc = 0.0
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_x is not None:
            gx = e.globalPosition().x()
            self._acc += (gx - self._drag_x)
            self._drag_x = gx
            steps = int(self._acc / 4.0)
            if steps != 0:
                self._acc -= steps * 4.0
                if self._spin is not None and self._spin.isEnabled():
                    self._spin.setValue(self._spin.value() + steps * self._spin.singleStep())
                self.scrubbed.emit(steps)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_x = None
        super().mouseReleaseEvent(e)


class _SpinLeftDragFilter(QObject):
    """Habilita arrastre horizontal en los primeros 22px izquierdos de QSpinBox / QDoubleSpinBox."""

    def __init__(self, spinbox):
        super().__init__(spinbox)
        self.sp = spinbox
        self._dragging = False
        self._last_x = 0.0
        self._acc = 0.0

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseMove:
            pos = ev.position()
            if self._dragging:
                gx = ev.globalPosition().x()
                self._acc += (gx - self._last_x)
                self._last_x = gx
                steps = int(self._acc / 4.0)
                if steps != 0:
                    self._acc -= steps * 4.0
                    self.sp.setValue(self.sp.value() + steps * self.sp.singleStep())
                return True
            if pos.x() <= 22:
                obj.setCursor(Qt.SizeHorCursor)
            else:
                obj.setCursor(Qt.IBeamCursor)
        elif t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            if ev.position().x() <= 22:
                self._dragging = True
                self._last_x = ev.globalPosition().x()
                self._acc = 0.0
                return True
        elif t == QEvent.MouseButtonRelease and self._dragging:
            self._dragging = False
            return True
        return False


def enable_numeric_drag(spinbox, label=None):
    """Oculta flechas up/down vía QSS y activa arrastre horizontal (1 singleStep cada 4px)."""
    flt = _SpinLeftDragFilter(spinbox)
    spinbox._scrub_filter = flt
    spinbox.installEventFilter(flt)
    le = spinbox.lineEdit()
    if le is not None:
        le.setMouseTracking(True)
        flt_le = _SpinLeftDragFilter(spinbox)
        le._scrub_filter = flt_le
        le.installEventFilter(flt_le)
    if isinstance(label, NumericScrubLabel):
        label.set_spinbox(spinbox)
    elif isinstance(label, QLabel):
        label.setCursor(Qt.SizeHorCursor)


# =====================================================  Section y SegmentedChoice
class Section(QWidget):
    """Acordeón plegable Studio UI: cabecera #sectionHeader con chevron down/right (14px) + título 8pt muted."""

    def __init__(self, title, expanded=True, subtitle=""):
        super().__init__()
        self._expanded = expanded
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self.btn = QPushButton(f" {title}")
        self.btn.setObjectName("sectionHeader")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setIconSize(QSize(14, 14))
        self.btn.clicked.connect(self.toggle)
        hl.addWidget(self.btn, 1)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("mutedLabel")
            sub.setStyleSheet("font-size:7.6pt; padding-right:2px;")
            hl.addWidget(sub)
        outer.addWidget(hdr)

        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(2, 6, 2, 6)
        self.content.setSpacing(6)
        outer.addWidget(self.body)
        self._sync()
        theme.bus.changed.connect(lambda _: self._sync())

    def add_row(self, label_txt: str, control: QWidget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lbl = NumericScrubLabel(label_txt, fixed_w=84)
        if hasattr(control, "singleStep") and hasattr(control, "setValue"):
            enable_numeric_drag(control, lbl)
        row.addWidget(lbl)
        row.addWidget(control, 1)
        self.content.addLayout(row)
        return lbl

    def toggle(self):
        self._expanded = not self._expanded
        self._sync()

    def set_expanded(self, on):
        self._expanded = bool(on)
        self._sync()

    def _sync(self):
        self.body.setVisible(self._expanded)
        self.btn.setIcon(icons.icon("chevron_down" if self._expanded else "chevron_right", C["muted"], 14))


# Alias 100% compatible con todo el proyecto
CollapsibleSection = Section


class SegmentedChoice(QWidget):
    """Contenedor #segmentedChoice (#0D0F12, 1px border, radius 4px, padding 2px, botones 23px).
    1 fila hasta 4 opciones o rejilla de 3 columnas si hay 5+."""
    changed = Signal(int)

    def __init__(self, options=(), parent=None):
        super().__init__(parent)
        self.setObjectName("segmentedChoice")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(2)
        self.grp = QButtonGroup(self)
        self.grp.setExclusive(True)
        self.btns = []
        if options:
            self.set_options(options)

    def set_options(self, options):
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                self.grp.removeButton(w)
                w.setParent(None)
                w.deleteLater()
        self.btns = []
        n = len(options)
        cols = n if n <= 4 else 3
        for i, lbl in enumerate(options):
            b = QToolButton()
            b.setText(str(lbl))
            b.setObjectName("segmentedChoiceButton")
            b.setCheckable(True)
            b.setFixedHeight(23)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, idx=i: self.changed.emit(idx))
            self.grp.addButton(b, i)
            self.btns.append(b)
            self._grid.addWidget(b, i // max(1, cols), i % max(1, cols))
        if self.btns:
            self.btns[0].setChecked(True)

    def set_index(self, i):
        if 0 <= i < len(self.btns):
            self.btns[i].blockSignals(True)
            self.btns[i].setChecked(True)
            self.btns[i].blockSignals(False)

    def current_index(self):
        return max(0, self.grp.checkedId())


class Segmented(SegmentedChoice):
    """Compatibilidad directa con Segmented('Opción 1', 'Opción 2', ...)."""

    def __init__(self, *labels):
        super().__init__(list(labels))


# =====================================================  Perilla Vectorial Studio (Mezclador / Rack DSP)
class VectorKnob(QWidget):
    """Perilla rotatoria vectorial Studio: arco de fondo #262C36 y progreso #6366F1."""
    valueChanged = Signal(int)

    def __init__(self, lo=0, hi=100, val=50, default=50, suffix="", label="", size=34):
        super().__init__()
        self._lo, self._hi = int(lo), int(hi)
        self._val = int(val)
        self._default = int(default)
        self._suffix = suffix
        self._label = label
        self._drag_pos = None
        self._start_val = val
        self.setFixedSize(size, size + (14 if label else 0))
        self.setCursor(Qt.SizeVerCursor)
        self._update_tip()

    def value(self):
        return self._val

    def setValue(self, v):
        nv = max(self._lo, min(self._hi, int(v)))
        if nv != self._val:
            self._val = nv
            self._update_tip()
            self.update()
            self.valueChanged.emit(nv)

    def _update_tip(self):
        prefix = f"{self._label}: " if self._label else ""
        self.setToolTip(f"{prefix}{self._val}{self._suffix} (doble clic o clic derecho restablece)")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sz = float(self.width())
        r = QRectF(4, 4, sz - 8, sz - 8)

        p.setBrush(QColor(C["input"]))
        p.setPen(QPen(QColor(C["border"]), 1.2))
        p.drawEllipse(r.adjusted(2, 2, -2, -2))

        # Arco total 270 grados (de 225° a -45°)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(C["border"]), 2.8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 225 * 16, -270 * 16)

        norm = (self._val - self._lo) / max(1, self._hi - self._lo)
        p.setPen(QPen(QColor(C["accent"]), 2.8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 225 * 16, int(round(-270 * 16 * norm)))

        # Aguja indicadora
        ang = math.radians(225.0 - 270.0 * norm)
        cx, cy = r.center().x(), r.center().y()
        rad = r.width() * 0.30
        p.setPen(QPen(QColor(C["text"]), 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(ang) * rad, cy - math.sin(ang) * rad))

        if self._label:
            p.setPen(QColor(C["dim"]))
            p.setFont(QFont("Segoe UI", 7.2))
            p.drawText(QRectF(0, sz - 1, sz, 14), Qt.AlignCenter, self._label)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.setValue(self._default)
            return
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition()
            self._start_val = self._val

    def mouseDoubleClickEvent(self, e):
        self.setValue(self._default)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            dy = self._drag_pos.y() - e.globalPosition().y()
            dx = e.globalPosition().x() - self._drag_pos.x()
            delta = (dy + dx * 0.5) / 110.0 * (self._hi - self._lo)
            self.setValue(int(round(self._start_val + delta)))

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


# =====================================================  ColorWheelDialog Modal Studio
class ColorWheelDialog(QDialog):
    """Diálogo modal frameless con ColorWheel(216), Hex validado, Gotero 108px, Copiar y Paleta."""
    colorChanged = Signal(QColor)

    def __init__(self, initial="#6366F1", title="Selector de Color", parent=None):
        super().__init__(parent)
        self._drag_pos = None
        c = QColor(initial) if not isinstance(initial, QColor) else initial
        if not c.isValid():
            c = QColor("#6366F1")

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(316)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        shell = QFrame()
        shell.setObjectName("preferencesFrame")
        sh = QGraphicsDropShadowEffect(shell)
        sh.setBlurRadius(24)
        sh.setOffset(0, 5)
        sh.setColor(QColor(0, 0, 0, 95))
        shell.setGraphicsEffect(sh)
        outer.addWidget(shell)

        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QFrame()
        hdr.setObjectName("preferencesTitleBar")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 6, 0)
        t = QLabel(title)
        t.setObjectName("preferencesDialogTitle")
        hl.addWidget(t)
        hl.addStretch()
        bx = QToolButton()
        bx.setObjectName("preferencesCloseButton")
        bx.setIcon(icons.state_icon("close", 13))
        bx.setFixedSize(26, 26)
        bx.clicked.connect(self.reject)
        hl.addWidget(bx)
        root.addWidget(hdr)

        self.editor = ColorEditor(wheel_size=216, show_drawer=True)
        self.editor.set_color(c, emit=False)
        self.editor.colorChanged.connect(self.colorChanged.emit)
        self.editor.heightHintChanged.connect(self.adjustSize)
        root.addWidget(self.editor)

        foot = QFrame()
        foot.setObjectName("modalFooter")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 8, 12, 8)
        fl.addStretch()
        b_no = QPushButton("Cancelar")
        b_no.setObjectName("confirmGhost")
        b_no.clicked.connect(self.reject)
        b_ok = QPushButton("Aplicar")
        b_ok.setObjectName("confirmPrimary")
        b_ok.clicked.connect(self.accept)
        fl.addWidget(b_no)
        fl.addSpacing(6)
        fl.addWidget(b_ok)
        root.addWidget(foot)

        self.adjustSize()
        if parent:
            g = parent.window().frameGeometry()
            self.move(max(20, g.center().x() - self.width() // 2), max(20, g.center().y() - self.height() // 2))

    def current_color(self):
        return self.editor.current_color()

    def set_color(self, c):
        self.editor.set_color(c, emit=True)

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

    @staticmethod
    def pick(initial="#6366F1", title="Elegir color", parent=None, on_change=None):
        dlg = ColorWheelDialog(initial, title, parent)
        if on_change is not None:
            dlg.colorChanged.connect(on_change)
        if dlg.exec() == QDialog.Accepted:
            return dlg.current_color()
        if on_change is not None:
            orig = QColor(initial) if not isinstance(initial, QColor) else initial
            if orig.isValid():
                on_change(orig)
        return QColor()


# =====================================================  Etiquetas, Vúmetros y Sliders de Estudio
class ElidedLabel(QLabel):
    def __init__(self, text=""):
        super().__init__()
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, t):
        self._full = t
        super().setText(self.fontMetrics().elidedText(t, Qt.ElideRight, max(10, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(10, self.width())))


class LevelMeter(QWidget):
    """Vúmetro minimalista Studio (4-6px o interactivo): fondo #181C22, barra suave sin estética recargada."""
    thresholdChanged = Signal(int)

    def __init__(self, interactive=False, height=6):
        super().__init__()
        self.interactive = interactive
        self.level = 0.0
        self.peak_hold = 0.0
        self.state = 0
        self.T = 40
        self.R = 12
        self.setFixedHeight(max(5, int(height)))
        self.setMinimumWidth(40)
        if interactive:
            self.setCursor(Qt.SizeHorCursor)
            self.setToolTip("Arrastra para ajustar el umbral de detección vocal")

    def set_params(self, T, R):
        self.T, self.R = T, R
        self.update()

    def set_level(self, lvl, state):
        if lvl > self.peak_hold:
            self.peak_hold = lvl
        else:
            self.peak_hold = max(lvl, self.peak_hold - 0.012)
        if abs(lvl - self.level) > 0.005 or state != self.state or abs(self.peak_hold - lvl) > 0.01:
            self.level, self.state = lvl, state
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = min(2.5, r.height() / 2.0)
        p.setPen(QPen(QColor(C["border"]), 0.8) if self.interactive else Qt.NoPen)
        p.setBrush(QColor(C["input"]))
        p.drawRoundedRect(r.adjusted(0.4, 0.4, -0.4, -0.4), rad, rad)

        fill_w = r.width() * self.level
        if fill_w > 1.0:
            g = QLinearGradient(0, 0, r.width(), 0)
            g.setColorAt(0.0, QColor(C["accent"]))
            g.setColorAt(0.72, QColor("#2EB67D"))
            g.setColorAt(0.88, QColor("#F59E0B"))
            g.setColorAt(1.0, QColor(C["invalid"]))
            p.setPen(Qt.NoPen)
            p.setBrush(g if self.state > 0 else QColor(C["muted"]))
            p.drawRoundedRect(QRectF(0, 0, fill_w, r.height()), rad, rad)

        if self.peak_hold > 0.03:
            px = min(r.width() - 2, r.width() * self.peak_hold)
            p.setPen(QPen(QColor(C["invalid"] if self.peak_hold > 0.88 else C["selection_text"]), 1.4))
            p.drawLine(QPointF(px, 1.0), QPointF(px, r.height() - 1.0))

        if self.interactive:
            for i in range(3):
                x = r.width() * min(100, self.T + i * self.R) / 100.0
                p.setPen(QPen(QColor(STATE_COLORS[i + 1]), 1.4))
                p.drawLine(QPointF(x, 1), QPointF(x, r.height() - 1))

    def _set_from_x(self, x):
        self.thresholdChanged.emit(int(max(1, min(90, x / max(1, self.width()) * 100))))

    def mousePressEvent(self, e):
        if self.interactive:
            self._set_from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if self.interactive:
            self._set_from_x(e.position().x())


class LSlider(QWidget):
    """Control de parámetro Studio: NumericScrubLabel + valor monoespaciado + ClickableSlider."""
    changed = Signal(int)

    def __init__(self, label, lo, hi, suffix="", tip="", default=None):
        super().__init__()
        self.suffix = suffix
        self._default = default
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 2)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.lbl = NumericScrubLabel(label)
        self.lbl.scrubbed.connect(lambda steps: self.s.setValue(self.s.value() + steps * max(1, self.s.singleStep())))
        top.addWidget(self.lbl)
        top.addStretch()
        self.val = QLabel()
        self.val.setStyleSheet("font-family:'Consolas','Cascadia Mono',monospace; font-size:8pt;")
        top.addWidget(self.val)
        lay.addLayout(top)
        self.s = ClickableSlider(Qt.Horizontal)
        self.s.setRange(lo, hi)
        self.s.valueChanged.connect(self._c)
        lay.addWidget(self.s)
        if tip:
            self.setToolTip(tip)

    def _c(self, v):
        self.val.setText(f"{v}{self.suffix}")
        self.changed.emit(v)

    def set(self, v):
        self.s.blockSignals(True)
        self.s.setValue(int(v))
        self.s.blockSignals(False)
        self.val.setText(f"{int(v)}{self.suffix}")

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton and self._default is not None:
            self.s.setValue(int(self._default))
            return
        super().mousePressEvent(e)


# =====================================================  Jerarquía de Escena / Capas (34px Delegate)
ROLE_LAYER_ID = Qt.UserRole
ROLE_KIND = Qt.UserRole + 1
ROLE_VISIBLE = Qt.UserRole + 2
ROLE_LOCKED = Qt.UserRole + 3
ROLE_COUNT = Qt.UserRole + 4
ROLE_COLOR = Qt.UserRole + 5


class _LayerDelegate(QStyledItemDelegate):
    """Filas personalizadas de 34px:
    - Tarjeta redondeada (radius 6px) + barra vertical de acento 3px a la izquierda en ítem activo.
    - Icono ojo a la izquierda (30px, accent si activo, 45% si oculto).
    - Sangría 16px por nivel con guías verticales 1px en border_subtle.
    - Chevron + icono de carpeta/avatar/prop/luz/capa + pill contador a la derecha (16px, radius 8px) + candado (30px)."""

    def __init__(self, tree: QTreeWidget):
        super().__init__(tree)
        self.tree = tree

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 34)

    def _depth(self, index):
        d = 0
        p = index.parent()
        while p.isValid():
            d += 1
            p = p.parent()
        return d

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)

        # Ocupar el ancho total de la fila ignorando el rect indentado de QTreeView
        vp_w = self.tree.viewport().width()
        row_r = QRectF(2, option.rect.top() + 1, max(60, vp_w - 4), option.rect.height() - 2)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        visible = index.data(ROLE_VISIBLE)
        if visible is None:
            visible = True
        locked = bool(index.data(ROLE_LOCKED))
        kind = index.data(ROLE_KIND) or "layer"
        cnt = index.data(ROLE_COUNT)

        if selected:
            p.setBrush(theme.selection_qcolor())
            p.setPen(QPen(QColor(C["accent"]), 1.0))
            p.drawRoundedRect(row_r, 6, 6)
            # Barra vertical de acento 3px a la izquierda
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(row_r.left() + 1, row_r.top() + 5, 3.0, row_r.height() - 10), 1.5, 1.5)
            p.fillPath(bar, QColor(C["accent"]))
        elif hovered:
            p.setBrush(QColor(C["hover"]))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(row_r, 6, 6)

        # Ojo de visibilidad a la izquierda (zona 30px)
        eye_col = QColor(C["accent"] if selected else C["dim"])
        if not visible:
            eye_col.setAlpha(int(255 * 0.45))
        pm_eye = icons.pixmap("eye" if visible else "eye_off", eye_col, 14)
        p.drawPixmap(int(row_r.left() + 9), int(row_r.center().y() - 7), pm_eye)

        # Guías verticales de profundidad (16px por nivel)
        depth = self._depth(index)
        base_x = row_r.left() + 32.0
        if depth > 0:
            p.setPen(QPen(QColor(C["border_subtle"]), 1.0))
            for lv in range(depth):
                gx = base_x + lv * 16.0 + 6.0
                p.drawLine(QPointF(gx, option.rect.top()), QPointF(gx, option.rect.bottom()))

        cur_x = base_x + depth * 16.0
        item = self.tree.itemFromIndex(index)
        has_children = item is not None and item.childCount() > 0
        if has_children or kind == "folder":
            exp = item.isExpanded() if item else True
            pm_ch = icons.pixmap("chevron_down" if exp else "chevron_right", C["muted"], 12)
            p.drawPixmap(int(cur_x), int(row_r.center().y() - 6), pm_ch)
            cur_x += 15.0

        ic_map = {
            "folder": "folder",
            "avatar": "user",
            "vtuber": "rig",
            "vtuber3d": "cube",
            "image": "image",
            "text": "text",
            "visualizer": "wave",
            "light": "sun",
            "layer": "layers",
        }
        ic_col = index.data(ROLE_COLOR) or (C["selection_text"] if selected else C["dim"])
        pm_ic = icons.pixmap(ic_map.get(kind, "layers"), ic_col, 14)
        p.drawPixmap(int(cur_x), int(row_r.center().y() - 7), pm_ic)
        cur_x += 20.0

        # Candado a la derecha (30px; oculto en reposo salvo hover o bloqueado)
        right_edge = row_r.right() - 4.0
        if locked or hovered:
            lk_col = C["accent"] if locked else C["muted"]
            pm_lk = icons.pixmap("lock" if locked else "unlock", lk_col, 13)
            p.drawPixmap(int(row_r.right() - 22), int(row_r.center().y() - 6.5), pm_lk)
            right_edge -= 26.0

        # Pill contador si es carpeta / grupo con hijos
        if cnt:
            cnt_txt = str(cnt)
            p.setFont(QFont("Segoe UI", 7.4, QFont.DemiBold))
            pw = max(18.0, p.fontMetrics().horizontalAdvance(cnt_txt) + 10.0)
            pill = QRectF(right_edge - pw - 2, row_r.center().y() - 8.0, pw, 16.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["hover"]))
            p.drawRoundedRect(pill, 8.0, 8.0)
            p.setPen(QColor(C["dim"]))
            p.drawText(pill, Qt.AlignCenter, cnt_txt)
            right_edge = pill.left() - 6.0

        # Texto del ítem
        txt = index.data(Qt.DisplayRole) or ""
        txt_col = QColor(C["selection_text"] if selected else (C["text"] if visible else C["muted"]))
        p.setPen(txt_col)
        f = QFont("Segoe UI", 8.4)
        f.setWeight(QFont.DemiBold if selected else QFont.Normal)
        p.setFont(f)
        avail_w = max(20, int(right_edge - cur_x))
        p.drawText(QRectF(cur_x, row_r.top(), avail_w, row_r.height()),
                   Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(txt, Qt.ElideRight, avail_w))
        p.restore()


class _LayerTreeWidget(QTreeWidget):
    eyeToggled = Signal(object)
    lockToggled = Signal(object)
    hierarchyDropped = Signal()

    def __init__(self):
        super().__init__()
        self._drop_target_rect = None
        self.setHeaderHidden(True)
        self.setIndentation(0)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setStyleSheet("QTreeWidget { background: transparent; border: none; padding: 0px; }")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            idx = self.indexAt(pos)
            if idx.isValid():
                if pos.x() <= 30:
                    it = self.itemFromIndex(idx)
                    self.eyeToggled.emit(it)
                    e.accept()
                    return
                if pos.x() >= self.viewport().width() - 30:
                    it = self.itemFromIndex(idx)
                    self.lockToggled.emit(it)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def dragMoveEvent(self, e):
        super().dragMoveEvent(e)
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid():
            r = self.visualRect(idx)
            self._drop_target_rect = QRectF(2, r.top() + 1, self.viewport().width() - 4, r.height() - 2)
        else:
            self._drop_target_rect = None
        self.viewport().update()

    def dragLeaveEvent(self, e):
        self._drop_target_rect = None
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._drop_target_rect = None
        super().dropEvent(e)
        self.viewport().update()
        self.hierarchyDropped.emit()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_target_rect is not None:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            ac = QColor(C["accent"])
            fill = QColor(ac.red(), ac.green(), ac.blue(), 72)
            p.setBrush(fill)
            p.setPen(QPen(ac, 1.5))
            p.drawRoundedRect(self._drop_target_rect, 6, 6)


class LayersPanel(QWidget):
    """Panel unificado de Jerarquía de Escena / Capas / Objetos conectado al espacio de trabajo activo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._syncing = False

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(3)
        self.count_lbl = QLabel("0 elementos")
        self.count_lbl.setObjectName("mutedLabel")
        hdr.addWidget(self.count_lbl)
        hdr.addStretch()

        self.btns = {}
        for key, ic, tip, cb in (
            ("add", "plus", "Añadir elemento / capa", self._on_add),
            ("folder", "folder_add", "Nueva carpeta / escena", self._on_folder),
            ("up", "arrow_up", "Subir orden Z", lambda: self._on_move(1)),
            ("down", "arrow_down", "Bajar orden Z", lambda: self._on_move(-1)),
            ("group", "merge", "Duplicar / Agrupar", self._on_duplicate),
            ("del", "trash", "Eliminar seleccionado", self._on_delete),
        ):
            b = QToolButton()
            b.setObjectName("flat")
            b.setFixedSize(24, 24)
            b.setIcon(icons.state_icon(ic, 14))
            b.setIconSize(QSize(14, 14))
            b.setToolTip(tip)
            b.clicked.connect(cb)
            self.btns[key] = (b, ic)
            hdr.addWidget(b)
        v.addLayout(hdr)

        self.tree = _LayerTreeWidget()
        self.delegate = _LayerDelegate(self.tree)
        self.tree.setItemDelegate(self.delegate)
        self.tree.currentItemChanged.connect(self._on_sel_changed)
        self.tree.eyeToggled.connect(self._on_eye_toggled)
        self.tree.lockToggled.connect(self._on_lock_toggled)
        self.tree.hierarchyDropped.connect(self._on_dropped)
        v.addWidget(self.tree, 1)

        theme.bus.changed.connect(lambda _: self._refresh_icons())

    def _refresh_icons(self):
        for b, ic in self.btns.values():
            b.setIcon(icons.state_icon(ic, 14))
        self.tree.viewport().update()

    def bind_workspace(self, ws):
        self._ws = ws
        self.refresh()

    def refresh(self):
        self._syncing = True
        self.tree.blockSignals(True)
        self.tree.clear()
        total = 0

        if self._ws is not None:
            kind = getattr(self._ws, "tab_kind", lambda: "video")()
            if kind == "vtuber":
                layers = self._ws.rig.get("layers", [])
                total = len(layers)
                by_id = {}
                for lyr in layers:
                    it = QTreeWidgetItem([lyr["name"]])
                    it.setData(0, ROLE_LAYER_ID, ("rig_layer", lyr["id"]))
                    it.setData(0, ROLE_KIND, "vtuber" if lyr.get("role") == "toggle" else "layer")
                    it.setData(0, ROLE_VISIBLE, bool(lyr.get("visible", True)))
                    it.setData(0, ROLE_LOCKED, bool(lyr.get("locked", False)))
                    by_id[lyr["id"]] = it
                for lyr in layers:
                    pid = lyr.get("parent_id")
                    it = by_id[lyr["id"]]
                    if pid and pid in by_id and pid != lyr["id"]:
                        by_id[pid].addChild(it)
                    else:
                        self.tree.addTopLevelItem(it)
                for it in by_id.values():
                    if it.childCount() > 0:
                        it.setData(0, ROLE_COUNT, it.childCount())
                self.tree.expandAll()
                if self._ws.sel_layer in by_id:
                    self.tree.setCurrentItem(by_id[self._ws.sel_layer])

            elif kind == "vtuber3d":
                import vtuber3d_engine as V3
                root_av = QTreeWidgetItem([self._ws.data.get("name", "Avatar 3D")])
                root_av.setData(0, ROLE_LAYER_ID, ("3d_root", "root"))
                root_av.setData(0, ROLE_KIND, "vtuber3d")
                root_av.setData(0, ROLE_VISIBLE, True)
                acc_map = self._ws.data.get("accessories", {})
                root_av.setData(0, ROLE_COUNT, len(V3.ACCESSORY_DEFS))
                self.tree.addTopLevelItem(root_av)
                for hk, key, lbl in V3.ACCESSORY_DEFS:
                    ch_it = QTreeWidgetItem([f"{hk} · {lbl}"])
                    ch_it.setData(0, ROLE_LAYER_ID, ("3d_acc", hk))
                    ch_it.setData(0, ROLE_KIND, "vtuber")
                    ch_it.setData(0, ROLE_VISIBLE, bool(acc_map.get(hk, False)))
                    root_av.addChild(ch_it)
                    total += 1
                light_it = QTreeWidgetItem(["Iluminación Toon / Studio"])
                light_it.setData(0, ROLE_LAYER_ID, ("3d_light", "light"))
                light_it.setData(0, ROLE_KIND, "light")
                light_it.setData(0, ROLE_VISIBLE, True)
                self.tree.addTopLevelItem(light_it)
                total += 2
                self.tree.expandAll()

            else:  # video / tiktok
                chars = self._ws.data.get("characters", [])
                props = self._ws.sdata().get("props", [])
                total = len(chars) + len(props)
                sel_item = None

                f_chars = QTreeWidgetItem(["Avatares / Personajes"])
                f_chars.setData(0, ROLE_LAYER_ID, ("folder", "chars"))
                f_chars.setData(0, ROLE_KIND, "folder")
                f_chars.setData(0, ROLE_VISIBLE, True)
                f_chars.setData(0, ROLE_COUNT, len(chars))
                self.tree.addTopLevelItem(f_chars)
                for ch in chars:
                    pl = self._ws.placement(ch["id"])
                    ck = "vtuber3d" if ch.get("rig3d_data") else ("vtuber" if ch.get("rig_data") else "avatar")
                    it = QTreeWidgetItem([ch["name"]])
                    it.setData(0, ROLE_LAYER_ID, ("char", ch["id"]))
                    it.setData(0, ROLE_KIND, ck)
                    it.setData(0, ROLE_VISIBLE, bool(pl.get("visible", True)))
                    it.setData(0, ROLE_COLOR, ch.get("color"))
                    if ch.get("accessories"):
                        it.setData(0, ROLE_COUNT, len(ch["accessories"]))
                    f_chars.addChild(it)
                    if ch["id"] == self._ws.sel:
                        sel_item = it

                f_props = QTreeWidgetItem(["Capas y Overlays"])
                f_props.setData(0, ROLE_LAYER_ID, ("folder", "props"))
                f_props.setData(0, ROLE_KIND, "folder")
                f_props.setData(0, ROLE_VISIBLE, True)
                f_props.setData(0, ROLE_COUNT, len(props))
                self.tree.addTopLevelItem(f_props)
                for pr in props:
                    it = QTreeWidgetItem([pr.get("name", "Capa")])
                    it.setData(0, ROLE_LAYER_ID, ("prop", pr["id"]))
                    it.setData(0, ROLE_KIND, pr.get("kind", "image"))
                    it.setData(0, ROLE_VISIBLE, bool(pr.get("visible", True)))
                    it.setData(0, ROLE_LOCKED, bool(pr.get("locked", False)))
                    f_props.addChild(it)
                    if pr["id"] == self._ws.sel_prop:
                        sel_item = it

                self.tree.expandAll()
                if sel_item is not None:
                    self.tree.setCurrentItem(sel_item)

        self.count_lbl.setText(f"{total} elemento(s)")
        self.tree.blockSignals(False)
        self._syncing = False

    def _on_sel_changed(self, cur, _prev):
        if self._syncing or cur is None or self._ws is None:
            return
        tag = cur.data(0, ROLE_LAYER_ID)
        if not tag:
            return
        tp, ident = tag
        if tp == "char":
            self._ws.select_char(ident)
        elif tp == "prop":
            self._ws.select_prop(ident)
        elif tp == "rig_layer":
            self._ws.select_layer(ident)
            self._ws.canvas.update()
        elif tp == "3d_light" and hasattr(self._ws, "tabs"):
            self._ws.tabs.setCurrentIndex(2)
        elif tp in ("3d_root", "3d_acc") and hasattr(self._ws, "tabs"):
            self._ws.tabs.setCurrentIndex(0 if tp == "3d_root" else 1)

    def _on_eye_toggled(self, item):
        if item is None or self._ws is None:
            return
        tag = item.data(0, ROLE_LAYER_ID)
        if not tag:
            return
        tp, ident = tag
        cur_v = bool(item.data(0, ROLE_VISIBLE))
        nv = not cur_v
        if tp == "char":
            self._ws.toggle_char_vis(ident, nv)
            self._ws.refresh_cards()
        elif tp == "prop":
            self._ws.toggle_prop_vis(ident, nv)
            self._ws.refresh_cards()
        elif tp == "rig_layer":
            for lyr in self._ws.rig.get("layers", []):
                if lyr["id"] == ident:
                    lyr["visible"] = nv
                    self._ws.mark_dirty()
                    self._ws._refresh_tree()
                    self._ws.canvas.update()
                    break
        elif tp == "3d_acc":
            self._ws._toggle_acc(ident, nv)
        item.setData(0, ROLE_VISIBLE, nv)
        self.tree.viewport().update()

    def _on_lock_toggled(self, item):
        if item is None or self._ws is None:
            return
        tag = item.data(0, ROLE_LAYER_ID)
        if not tag:
            return
        tp, ident = tag
        nl = not bool(item.data(0, ROLE_LOCKED))
        if tp == "prop":
            for pr in self._ws.sdata().get("props", []):
                if pr["id"] == ident:
                    pr["locked"] = nl
                    if ident in self._ws.prop_items:
                        self._ws.prop_items[ident].sync_from_prop()
                    self._ws.mark_dirty()
                    break
        elif tp == "rig_layer":
            for lyr in self._ws.rig.get("layers", []):
                if lyr["id"] == ident:
                    lyr["locked"] = nl
                    self._ws.mark_dirty()
                    break
        item.setData(0, ROLE_LOCKED, nl)
        self.tree.viewport().update()

    def _on_dropped(self):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            by_id = {l["id"]: l for l in self._ws.rig.get("layers", [])}
            def _walk(parent_item, parent_lid):
                for i in range(parent_item.childCount()):
                    ch = parent_item.child(i)
                    tag = ch.data(0, ROLE_LAYER_ID)
                    if tag and tag[0] == "rig_layer" and tag[1] in by_id:
                        by_id[tag[1]]["parent_id"] = parent_lid
                        _walk(ch, tag[1])
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                tag = top.data(0, ROLE_LAYER_ID)
                if tag and tag[0] == "rig_layer" and tag[1] in by_id:
                    by_id[tag[1]]["parent_id"] = None
                    _walk(top, tag[1])
            self._ws._refresh_tree()
            self._ws.mark_dirty()
            self._ws.canvas.update()
        self.refresh()

    def _on_add(self):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            self._ws.import_layers_dialog()
        elif kind == "vtuber3d":
            self._ws.import_external_model_dialog()
        else:
            m = QMenu(self)
            a_c = m.addAction(icons.state_icon("user", 14), "Nuevo personaje por Sprites...")
            a_v2 = m.addAction(icons.state_icon("rig", 14), "Importar VTuber 2D (.vtr)...")
            a_v3 = m.addAction(icons.state_icon("cube", 14), "Importar VTuber 3D (.vt3d)...")
            m.addSeparator()
            a_i = m.addAction(icons.state_icon("image", 14), "Capa de Imagen / GIF...")
            a_t = m.addAction(icons.state_icon("text", 14), "Rótulo de Texto")
            a_w = m.addAction(icons.state_icon("wave", 14), "Visualizador de Audio")
            ch = m.exec(self.btns["add"][0].mapToGlobal(self.btns["add"][0].rect().bottomLeft()))
            if ch == a_c:
                self._ws.add_char()
            elif ch == a_v2:
                self._ws.import_vtr_character()
            elif ch == a_v3:
                self._ws.import_vt3d_character()
            elif ch == a_i:
                self._ws.add_prop("image")
            elif ch == a_t:
                self._ws.add_prop("text")
            elif ch == a_w:
                self._ws.add_prop("visualizer")
        self.refresh()

    def _on_folder(self):
        if self._ws is not None and hasattr(self._ws, "add_scene"):
            self._ws.add_scene()
            self.refresh()

    def _on_move(self, delta):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            self._ws._nudge_z(5 * delta)
        elif kind in ("video", "tiktok"):
            if self._ws.sel and self._ws.sel in self._ws.items:
                self._ws.change_z(self._ws.items[self._ws.sel], delta)
            elif self._ws.sel_prop:
                pr = self._ws._cur_prop()
                if pr:
                    self._ws._propset("z", int(pr.get("z", 50)) + 5 * delta)
        self.refresh()

    def _on_duplicate(self):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            self._ws.duplicate_layer()
        elif kind in ("video", "tiktok") and self._ws.sel:
            self._ws.duplicate_char(self._ws.sel)
        self.refresh()

    def _on_delete(self):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind == "vtuber":
            self._ws.delete_layer()
        elif kind in ("video", "tiktok"):
            if self._ws.sel_prop:
                self._ws.del_prop(self._ws.sel_prop)
            elif self._ws.sel:
                self._ws.del_char(self._ws.sel)
        self.refresh()


# =====================================================  Biblioteca + Soundboard (ToolPalette + Preview 200ms)
def _render_library_thumb(kind: str, accent_hex: str, w: int, h: int) -> QPixmap:
    dpr = 2
    img = QImage(w * dpr, h * dpr, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(0, 0, w * dpr, h * dpr)
    p.setBrush(QColor(C["canvas"]))
    p.setPen(QPen(QColor(C["border"]), 1.5))
    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)

    col = QColor(accent_hex)
    cx, cy = r.center().x(), r.center().y()
    p.setBrush(QColor(col.red(), col.green(), col.blue(), 48))
    p.setPen(QPen(col, 2.2))
    p.drawEllipse(QPointF(cx, cy), min(w, h) * 0.56, min(w, h) * 0.56)
    pm_ic = icons.pixmap(kind, accent_hex, int(min(w, h) * 0.52))
    p.drawPixmap(int(cx - pm_ic.width() / pm_ic.devicePixelRatio() * 0.5 * dpr),
                 int(cy - pm_ic.height() / pm_ic.devicePixelRatio() * 0.5 * dpr),
                 pm_ic.scaled(int(min(w, h) * dpr * 0.52), int(min(w, h) * dpr * 0.52), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    p.end()
    out = QPixmap.fromImage(img)
    out.setDevicePixelRatio(dpr)
    return out


class ElementPreviewPopup(QFrame):
    """Popup flotante de vista previa (220px ancho, miniatura 200x126, título y descripción tras 200ms)."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(220)
        self.setStyleSheet(
            f"QFrame {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 6px; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        self.thumb = QLabel()
        self.thumb.setFixedSize(200, 126)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600; font-size: 9pt; border: none;")
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color: {C['dim']}; font-size: 7.8pt; border: none;")
        v.addWidget(self.thumb)
        v.addWidget(self.title)
        v.addWidget(self.desc)

    def show_item(self, item_meta: dict, anchor_widget: QWidget):
        self.setStyleSheet(
            f"QFrame {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 6px; }}"
        )
        self.desc.setStyleSheet(f"color: {C['dim']}; font-size: 7.8pt; border: none;")
        self.thumb.setPixmap(_render_library_thumb(item_meta["icon"], item_meta["color"], 200, 126))
        self.title.setText(item_meta["title"])
        self.desc.setText(item_meta["desc"])
        self.adjustSize()
        gp = anchor_widget.mapToGlobal(QPoint(anchor_widget.width() + 8, 0))
        scr = anchor_widget.screen().availableGeometry() if anchor_widget.screen() else QRect(0, 0, 1920, 1080)
        if gp.x() + self.width() > scr.right():
            gp.setX(anchor_widget.mapToGlobal(QPoint(0, 0)).x() - self.width() - 8)
        if gp.y() + self.height() > scr.bottom():
            gp.setY(scr.bottom() - self.height() - 8)
        self.move(gp)
        self.show()


class _LibraryItemButton(QPushButton):
    hoveredItem = Signal(dict, object)
    leftItem = Signal()

    def __init__(self, meta: dict):
        super().__init__()
        self.meta = meta
        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 8, 5)
        lay.setSpacing(8)

        th = QLabel()
        th.setFixedSize(54, 38)
        th.setPixmap(_render_library_thumb(meta["icon"], meta["color"], 54, 38))
        th.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(th)

        col = QVBoxLayout()
        col.setSpacing(1)
        t = ElidedLabel(meta["title"])
        t.setStyleSheet("font-weight: 600; font-size: 8.4pt;")
        t.setAttribute(Qt.WA_TransparentForMouseEvents)
        s = ElidedLabel(meta["sub"])
        s.setObjectName("mutedLabel")
        s.setStyleSheet("font-size: 7.5pt;")
        s.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addWidget(t)
        col.addWidget(s)
        lay.addLayout(col, 1)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.hoveredItem.emit(self.meta, self)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.leftItem.emit()


class ToolPalette(QWidget):
    """Biblioteca Studio UI (Avatares 2D/3D, Props, Fondos, Expresiones y Soundboard):
    - Buscador con botón limpiar.
    - Carpetas en rejilla de 2 columnas (#folderTile, 68px alto, icono 22px + nombre y conteo) con '← Volver'.
    - Ítems de 48px con miniatura 54x38 y ElementPreviewPopup flotante tras 200ms de hover."""
    actionTriggered = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cur_folder = None
        self._hover_meta = None
        self._hover_widget = None
        self._popup = ElementPreviewPopup()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._show_preview)

        self._catalog = self._build_catalog()

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Buscador con botón limpiar
        s_row = QHBoxLayout()
        s_row.setSpacing(4)
        self.search_ed = QLineEdit()
        self.search_ed.setPlaceholderText("Buscar avatares, props, sonidos...")
        self.search_ed.textChanged.connect(self._rebuild)
        self.b_clear = QToolButton()
        self.b_clear.setObjectName("flat")
        self.b_clear.setFixedSize(22, 22)
        self.b_clear.setIcon(icons.state_icon("close", 12))
        self.b_clear.setVisible(False)
        self.b_clear.clicked.connect(self.search_ed.clear)
        self.search_ed.textChanged.connect(lambda t: self.b_clear.setVisible(bool(t)))
        s_row.addWidget(self.search_ed, 1)
        s_row.addWidget(self.b_clear)
        v.addLayout(s_row)

        # Barra de navegación ← Volver
        self.nav_row = QWidget()
        nl = QHBoxLayout(self.nav_row)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(6)
        self.b_back = QPushButton("← Volver")
        self.b_back.setObjectName("flat")
        self.b_back.setFixedHeight(24)
        self.b_back.clicked.connect(self._go_root)
        self.folder_lbl = QLabel("")
        self.folder_lbl.setObjectName("sectionLabel")
        nl.addWidget(self.b_back)
        nl.addWidget(self.folder_lbl, 1)
        self.nav_row.setVisible(False)
        v.addWidget(self.nav_row)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(0, 0, 2, 0)
        self.host_lay.setSpacing(6)
        sc.setWidget(self.host)
        v.addWidget(sc, 1)

        self._rebuild()
        theme.bus.changed.connect(lambda _: self._rebuild())

    def _build_catalog(self):
        return [
            {
                "id": "avatars", "name": "Avatares 2D/3D", "icon": "rig", "color": "#6366F1",
                "items": [
                    {"act": "add_char_sprite", "title": "Avatar PNGTuber", "sub": "Sprites + Lip-Sync", "icon": "user", "color": "#6366F1",
                     "desc": "Añade un personaje 2D por ranuras PNG/GIF con rebote físico y detección vocal."},
                    {"act": "new_rig_2d", "title": "Avatar Rig 2.5D", "sub": "17 capas + física pelo", "icon": "rig", "color": "#F59E0B",
                     "desc": "Crea o inserta un avatar VTuber 2.5D completo con paralaje, mirada y emociones."},
                    {"act": "new_vtuber_3d", "title": "Avatar Studio 3D", "sub": "Toon / PBR + 360°", "icon": "cube", "color": "#38BDF8",
                     "desc": "Genera un avatar 3D interactivo o importa archivos .VRM, .GLB u .OBJ."},
                    {"act": "import_vtr", "title": "Importar archivo .vtr/.vt3d", "sub": "Desde disco", "icon": "folder_open", "color": "#2EB67D",
                     "desc": "Carga un avatar VTuber 2D (.vtr) o 3D (.vt3d) guardado en la escena actual."},
                ],
            },
            {
                "id": "props", "name": "Props y Overlays", "icon": "layers", "color": "#38BDF8",
                "items": [
                    {"act": "prop_image", "title": "Imagen / Logo / GIF", "sub": "Capa superpuesta", "icon": "image", "color": "#38BDF8",
                     "desc": "Inserta logotipos, marcos de stream, mesas de podcast o stickers animados."},
                    {"act": "prop_text", "title": "Rótulo Lower Third", "sub": "Texto estilizado", "icon": "text", "color": "#A5B4FC",
                     "desc": "Añade títulos de episodio, nombres de invitados o cintillos en vivo."},
                    {"act": "prop_viz", "title": "Visualizador Espectral", "sub": "32 bandas en vivo", "icon": "wave", "color": "#2EB67D",
                     "desc": "Espectro reactivo en tiempo real sincronizado con las voces y pistas."},
                    {"act": "add_acc", "title": "Accesorio Acoplado", "sub": "Sigue al personaje", "icon": "accessories", "color": "#EC4899",
                     "desc": "Acopla gafas, auriculares o mascotas con física de resorte al avatar activo."},
                ],
            },
            {
                "id": "backgrounds", "name": "Fondos y Chroma", "icon": "palette", "color": "#2EB67D",
                "items": [
                    {"act": "bg_dark", "title": "Estudio Grafito", "sub": "#0E1014 limpio", "icon": "moon", "color": "#6366F1",
                     "desc": "Fondo oscuro neutro calibrado para grabación o podcast."},
                    {"act": "bg_chroma", "title": "Chroma Key Verde", "sub": "#00B140 broadcast", "icon": "monitor", "color": "#2EB67D",
                     "desc": "Fondo verde puro sin derrames listo para filtro Chroma Key en OBS."},
                    {"act": "bg_alpha", "title": "Transparente (Alfa)", "sub": "Canal RGBA limpio", "icon": "alpha", "color": "#38BDF8",
                     "desc": "Desactiva el fondo para exportar WebM/MOV con transparencia real."},
                    {"act": "bg_image", "title": "Imagen de Escenario...", "sub": "PNG / JPG + Blur", "icon": "image", "color": "#F59E0B",
                     "desc": "Carga tu propio set virtual con control de desenfoque y viñeta."},
                ],
            },
            {
                "id": "emotes", "name": "Expresiones", "icon": "emote", "color": "#F59E0B",
                "items": [
                    {"act": ("emote", k), "title": f"{sym}  {nm}", "sub": "Disparo en vivo", "icon": "emote", "color": col,
                     "desc": f"Dispara la reacción animada «{nm}» sobre el avatar seleccionado."}
                    for k, sym, nm, col in M.EMOTE_CATALOG
                ],
            },
            {
                "id": "soundboard", "name": "Soundboard SFX", "icon": "music", "color": "#EC4899",
                "items": [
                    {"act": ("sfx", "chime"), "title": "Campana Suave", "sub": "Tono limpio estudio", "icon": "music", "color": "#6366F1",
                     "desc": "Reproduce un tono cristalino de transición de tema en directo."},
                    {"act": ("sfx", "pop"), "title": "Pop / Alerta", "sub": "Disparo corto", "icon": "bolt", "color": "#2EB67D",
                     "desc": "Efecto percusivo limpio para resaltar reacciones o emotes."},
                    {"act": ("sfx", "lvlup"), "title": "Acorde Brillante", "sub": "Arpegio positivo", "icon": "sparkle", "color": "#F59E0B",
                     "desc": "Arpegio sintético suave para celebraciones en vivo."},
                    {"act": ("sfx", "buzzer"), "title": "Buzzer Cómico", "sub": "Efecto grave", "icon": "volume", "color": "#E5484D",
                     "desc": "Tono grave corto para momentos cómicos de podcast."},
                    {"act": "import_audio_clip", "title": "Importar Audio / SFX...", "sub": "WAV / MP3 / OGG", "icon": "folder", "color": "#38BDF8",
                     "desc": "Importa cualquier efecto de sonido o pista musical a la línea de tiempo."},
                ],
            },
        ]

    def _go_root(self):
        self._cur_folder = None
        self._popup.hide()
        self._rebuild()

    def _open_folder(self, fid):
        self._cur_folder = fid
        self._popup.hide()
        self._rebuild()

    def _on_hover(self, meta, widget):
        self._hover_meta = meta
        self._hover_widget = widget
        self._timer.start()

    def _on_leave(self):
        self._timer.stop()
        self._popup.hide()

    def _show_preview(self):
        if self._hover_meta and self._hover_widget and self._hover_widget.isVisible():
            self._popup.show_item(self._hover_meta, self._hover_widget)

    def _rebuild(self):
        self._popup.hide()
        while self.host_lay.count():
            it = self.host_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        q = self.search_ed.text().strip().lower()
        if q:
            self.nav_row.setVisible(False)
            matches = []
            for cat in self._catalog:
                for it in cat["items"]:
                    if q in it["title"].lower() or q in it["sub"].lower() or q in cat["name"].lower():
                        matches.append(it)
            for it in matches:
                btn = _LibraryItemButton(it)
                btn.hoveredItem.connect(self._on_hover)
                btn.leftItem.connect(self._on_leave)
                btn.clicked.connect(lambda _, a=it["act"]: self.actionTriggered.emit("item", a))
                self.host_lay.addWidget(btn)
            if not matches:
                lbl = QLabel("Sin coincidencias")
                lbl.setObjectName("mutedLabel")
                lbl.setAlignment(Qt.AlignCenter)
                self.host_lay.addWidget(lbl)
            self.host_lay.addStretch()
            return

        if self._cur_folder is None:
            self.nav_row.setVisible(False)
            grid_w = QWidget()
            grid = QGridLayout(grid_w)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(6)
            for i, cat in enumerate(self._catalog):
                tile = QPushButton()
                tile.setObjectName("folderTile")
                tile.setFixedHeight(68)
                tile.setCursor(Qt.PointingHandCursor)
                tl = QVBoxLayout(tile)
                tl.setContentsMargins(8, 7, 8, 7)
                tl.setSpacing(3)
                top = QHBoxLayout()
                ic = QLabel()
                ic.setPixmap(icons.pixmap(cat["icon"], cat["color"], 22))
                ic.setAttribute(Qt.WA_TransparentForMouseEvents)
                cnt = QLabel(str(len(cat["items"])))
                cnt.setObjectName("mutedLabel")
                cnt.setAttribute(Qt.WA_TransparentForMouseEvents)
                top.addWidget(ic)
                top.addStretch()
                top.addWidget(cnt)
                nm = ElidedLabel(cat["name"])
                nm.setStyleSheet("font-weight: 600; font-size: 8.2pt;")
                nm.setAttribute(Qt.WA_TransparentForMouseEvents)
                tl.addLayout(top)
                tl.addWidget(nm)
                tile.clicked.connect(lambda _, fid=cat["id"]: self._open_folder(fid))
                grid.addWidget(tile, i // 2, i % 2)
            self.host_lay.addWidget(grid_w)
            self.host_lay.addStretch()
        else:
            cat = next((c for c in self._catalog if c["id"] == self._cur_folder), None)
            if cat is None:
                self._go_root()
                return
            self.nav_row.setVisible(True)
            self.folder_lbl.setText(cat["name"].upper())
            for it in cat["items"]:
                btn = _LibraryItemButton(it)
                btn.hoveredItem.connect(self._on_hover)
                btn.leftItem.connect(self._on_leave)
                btn.clicked.connect(lambda _, a=it["act"]: self.actionTriggered.emit("item", a))
                self.host_lay.addWidget(btn)
            self.host_lay.addStretch()


# =====================================================  Tarjetas Existentes Estilizadas Studio UI
class SpriteSlot(QWidget):
    changed = Signal(str, str)

    def __init__(self, key, title, hint, color="#6366F1"):
        super().__init__()
        self.key, self.title, self.hint, self.color = key, title, hint, color
        self.pm, self.live, self.hover, self.is_anim = None, False, False, False
        self.setAcceptDrops(True)
        self.setMinimumSize(74, 102)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clic para cargar imagen/GIF · Arrastra aquí · Clic derecho para quitar")

    def set_path(self, rel, color):
        self.color = color
        path = M.resolve(rel)
        self.pm = SHARED_ASSETS.load_thumb_pixmap(path, 128) if path else None
        self.is_anim = bool(path and path.lower().endswith((".gif", ".webp")))
        self.update()

    def set_live(self, v):
        if v != self.live:
            self.live = v
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        border = C["accent2"] if self.live else (C["accent"] if self.hover else C["border"])
        bg = theme.selection_qcolor() if self.live else QColor(C["hover"] if self.hover else C["surface"])
        p.setBrush(bg)
        pen_style = Qt.SolidLine if (self.pm is not None or self.live or self.hover) else Qt.DashLine
        p.setPen(QPen(QColor(border), 1.6 if self.live else 1.0, pen_style))
        p.drawRoundedRect(r, 5, 5)

        area = QRectF(r.x() + 6, r.y() + 6, r.width() - 12, r.height() - 40)
        if self.pm is not None:
            s = self.pm.scaled(area.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(area.center().x() - s.width() / 2), int(area.center().y() - s.height() / 2), s)
            if self.is_anim:
                badge = QRectF(r.right() - 28, r.top() + 5, 23, 13)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(C["accent"]))
                p.drawRoundedRect(badge, 3, 3)
                p.setPen(QColor("#FFFFFF"))
                p.setFont(QFont("Segoe UI", 6.5, QFont.Bold))
                p.drawText(badge, Qt.AlignCenter, "GIF")
        else:
            side = int(min(area.width(), area.height()) * 0.84)
            p.setOpacity(0.40)
            p.drawPixmap(int(area.center().x() - side / 2), int(area.center().y() - side / 2),
                         guide_pixmap(self.key, max(24, side), self.color))
            p.setOpacity(1.0)

        if self.live:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["accent2"]))
            p.drawEllipse(QRectF(r.left() + 6, r.top() + 6, 6.5, 6.5))

        f = QFont("Segoe UI", 7.8, QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(r.x() + 2, r.bottom() - 34, r.width() - 4, 16), Qt.AlignCenter, self.title)
        f.setWeight(QFont.Normal)
        f.setPointSizeF(7.1)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(r.x() + 2, r.bottom() - 19, r.width() - 4, 14), Qt.AlignCenter, self.hint)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(self, f"{self.title} — {self.hint}", str(M.BASE), M.EXTS)
            if path:
                self.changed.emit(self.key, path)

    def contextMenuEvent(self, e):
        if self.pm is not None:
            m = QMenu(self)
            act = m.addAction(icons.icon("trash", C["danger"], 14), "Quitar sprite")
            if m.exec(e.globalPos()) == act:
                self.changed.emit(self.key, "")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.changed.emit(self.key, urls[0].toLocalFile())


class AvatarBadge(QWidget):
    def __init__(self, size=38):
        super().__init__()
        self.setFixedSize(size, size)
        self._pm = QPixmap()
        self._color = C["accent"]
        self._talking = False

    def set_data(self, img_or_pm, color):
        if img_or_pm is not None and not img_or_pm.isNull():
            pm = QPixmap.fromImage(img_or_pm) if isinstance(img_or_pm, QImage) else img_or_pm
            sz = max(16, self.width() - 4)
            scaled = pm.scaled(sz, sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(1.0)
            self._pm = scaled
        else:
            self._pm = QPixmap()
        self._color = color
        self.update()

    def set_talking(self, v):
        if v != self._talking:
            self._talking = v
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 5, 5)
        p.fillPath(path, QColor(C["input"]))
        if not self._pm.isNull():
            p.save()
            p.setClipPath(path)
            s = self._pm
            p.drawPixmap(int(r.center().x() - s.width() / 2), int(r.center().y() - s.height() / 2), s)
            p.restore()
        p.setPen(QPen(QColor(C["accent2"] if self._talking else self._color), 2.0 if self._talking else 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 5, 5)


class CharacterCard(QFrame):
    clicked = Signal(str)
    muteToggled = Signal(str, bool)
    visToggled = Signal(str, bool)
    renameRequested = Signal(str)
    duplicateRequested = Signal(str)
    deleteRequested = Signal(str)
    colorRequested = Signal(str)

    def __init__(self, ch):
        super().__init__()
        self.cid = ch["id"]
        self.setObjectName("card")
        self.setFixedHeight(54)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 6, 6)
        row.setSpacing(8)

        self.avatar = AvatarBadge(38)
        row.addWidget(self.avatar)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = ElidedLabel()
        self.name.setStyleSheet("font-weight:600; font-size:8.6pt;")
        self.sub = ElidedLabel()
        self.sub.setObjectName("mutedLabel")
        self.sub.setStyleSheet("font-size:7.5pt;")
        self.meter = LevelMeter(height=5)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        col.addWidget(self.meter)
        row.addLayout(col, 1)

        btns = QHBoxLayout()
        btns.setSpacing(2)
        btns.setContentsMargins(0, 0, 0, 0)
        self.vis = QToolButton()
        self.vis.setObjectName("flat")
        self.vis.setCheckable(True)
        self.vis.setFixedSize(23, 23)
        self.vis.setToolTip("Mostrar / Ocultar en escena")
        self.vis.toggled.connect(lambda v: self.visToggled.emit(self.cid, v))
        self.mute = QToolButton()
        self.mute.setObjectName("mixMute")
        self.mute.setText("M")
        self.mute.setCheckable(True)
        self.mute.setFixedSize(22, 22)
        self.mute.setToolTip("Silenciar micrófono (M)")
        self.mute.toggled.connect(lambda v: self.muteToggled.emit(self.cid, v))
        btns.addWidget(self.vis)
        btns.addWidget(self.mute)
        row.addLayout(btns)

    def refresh(self, ch, img_or_pm, visible=True):
        col = ch.get("color", C["accent"])
        self.name.setText(ch["name"])
        n_acc = len(ch.get("accessories", []))
        dev = ch.get("device") or "Sin micrófono"
        self.sub.setText(f"{dev} · {n_acc} acc." if n_acc else dev)
        self.meter.set_params(ch["threshold"], ch["range"])
        self.vis.blockSignals(True)
        self.vis.setChecked(bool(visible))
        self.vis.setIcon(icons.icon("eye" if visible else "eye_off", C["dim"] if visible else C["muted"], 13))
        self.vis.blockSignals(False)
        self.mute.blockSignals(True)
        self.mute.setChecked(bool(ch["mute"]))
        self.mute.blockSignals(False)
        self.avatar.set_data(img_or_pm, col)

    def set_selected(self, v):
        self.setProperty("sel", v)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.cid)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a_col = m.addAction(icons.state_icon("palette", 14), "Cambiar color...")
        a_ren = m.addAction(icons.state_icon("edit", 14), "Renombrar")
        a_dup = m.addAction(icons.state_icon("copy", 14), "Duplicar personaje")
        m.addSeparator()
        a_del = m.addAction(icons.icon("trash", C["danger"], 14), "Eliminar")
        chosen = m.exec(e.globalPos())
        if chosen == a_col:
            self.colorRequested.emit(self.cid)
        elif chosen == a_ren:
            self.renameRequested.emit(self.cid)
        elif chosen == a_dup:
            self.duplicateRequested.emit(self.cid)
        elif chosen == a_del:
            self.deleteRequested.emit(self.cid)


class PropCard(QFrame):
    clicked = Signal(str)
    visToggled = Signal(str, bool)
    deleteRequested = Signal(str)
    renameRequested = Signal(str)

    def __init__(self, prop):
        super().__init__()
        self.pid = prop["id"]
        self.setObjectName("card")
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 6, 5)
        row.setSpacing(7)

        ic_name = {"image": "image", "text": "text", "visualizer": "wave"}.get(prop.get("kind"), "layers")
        ic_lbl = QLabel()
        ic_lbl.setPixmap(icons.pixmap(ic_name, C["dim"], 15))
        row.addWidget(ic_lbl)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = ElidedLabel(prop.get("name", "Capa"))
        self.name.setStyleSheet("font-weight:600; font-size:8.4pt;")
        kind_txt = {"image": "Imagen / Overlay", "text": "Texto / Rótulo", "visualizer": "Visualizador 2D"}.get(prop.get("kind"), "Capa")
        self.sub = ElidedLabel(f"{kind_txt} · Z {int(prop.get('z', 50))}")
        self.sub.setObjectName("mutedLabel")
        self.sub.setStyleSheet("font-size:7.4pt;")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        row.addLayout(col, 1)

        self.vis = QToolButton()
        self.vis.setObjectName("flat")
        self.vis.setCheckable(True)
        self.vis.setChecked(bool(prop.get("visible", True)))
        self.vis.setFixedSize(22, 22)
        self.vis.setIcon(icons.icon("eye" if prop.get("visible", True) else "eye_off", C["dim"], 13))
        self.vis.toggled.connect(lambda v: self.visToggled.emit(self.pid, v))
        b_del = QToolButton()
        b_del.setObjectName("flat")
        b_del.setFixedSize(22, 22)
        b_del.setIcon(icons.state_icon("trash", 13))
        b_del.clicked.connect(lambda: self.deleteRequested.emit(self.pid))
        row.addWidget(self.vis)
        row.addWidget(b_del)

    def set_selected(self, v):
        self.setProperty("sel", v)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.pid)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a_ren = m.addAction(icons.state_icon("edit", 14), "Renombrar capa")
        a_del = m.addAction(icons.icon("trash", C["danger"], 14), "Eliminar capa")
        chosen = m.exec(e.globalPos())
        if chosen == a_ren:
            self.renameRequested.emit(self.pid)
        elif chosen == a_del:
            self.deleteRequested.emit(self.pid)
