"""Ecosistema modular de ventanas Studio UI:
- StudioDockWidget + DockTitleBar (26px, título UPPERCASE 7.5pt, acoplar/desacoplar y cerrar).
- PanelRail (30px fijo derecha) + RailFlyout (256px / 236px compacto, alto autoajustado 180-620px).
- Arrastre bidireccional en vivo:
  1. Reordenar dentro del rail con línea horizontal de inserción (2.2px accent + puntos de 4px).
  2. Desprender hacia fuera (opacity 0.90) + guía semitransparente #dockDropGuide en bordes (<=54px).
  3. Re-absorber cualquier QDockWidget flotante al soltarlo sobre el rail (±20px).
- CanvasDock flotante inferior estilo Figma (cápsula radius 10px, sombra blur 18px, botones 28x28)."""
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QDockWidget, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

import icons
import theme
from theme import C, DENSITY


class DockTitleBar(QFrame):
    """Cabecera personalizada de 26px para todos los QDockWidget."""

    def __init__(self, dock: QDockWidget, title: str, icon_name: str):
        super().__init__(dock)
        self.dock = dock
        self.icon_name = icon_name
        self.setObjectName("dockTitleBar")
        self.setFixedHeight(26)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(6)

        self.ic_lbl = QLabel()
        self.ic_lbl.setFixedSize(13, 13)
        lay.addWidget(self.ic_lbl)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setObjectName("dockTitleLabel")
        lay.addWidget(self.title_lbl, 1)

        self.b_float = QToolButton()
        self.b_float.setObjectName("dockActionButton")
        self.b_float.setFixedSize(19, 19)
        self.b_float.setIconSize(QSize(13, 13))
        self.b_float.setToolTip("Desacoplar / Acoplar panel")
        self.b_float.clicked.connect(self._toggle_float)
        lay.addWidget(self.b_float)

        self.b_close = QToolButton()
        self.b_close.setObjectName("dockActionButton")
        self.b_close.setFixedSize(19, 19)
        self.b_close.setIconSize(QSize(13, 13))
        self.b_close.setToolTip("Ocultar panel")
        self.b_close.clicked.connect(dock.close)
        lay.addWidget(self.b_close)

        self.refresh_icons()
        theme.bus.changed.connect(lambda _: self.refresh_icons())

    def set_title(self, title: str):
        self.title_lbl.setText(title.upper())

    def refresh_icons(self):
        self.ic_lbl.setPixmap(icons.pixmap(self.icon_name, C["muted"], 13))
        fl_ic = "dock" if self.dock.isFloating() else "float"
        self.b_float.setIcon(icons.state_icon(fl_ic, 13))
        self.b_close.setIcon(icons.state_icon("close", 13))

    def _toggle_float(self):
        self.dock.setFloating(not self.dock.isFloating())
        self.refresh_icons()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._toggle_float()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class StudioDockWidget(QDockWidget):
    """QDockWidget estilizado con soporte de absorción hacia PanelRail al arrastrarlo flotante."""
    floatingMoved = Signal(object, QPoint)
    floatingReleased = Signal(object, QPoint)
    visibilityChangedByUser = Signal(str, bool)

    def __init__(self, panel_id: str, title: str, icon_name: str, widget: QWidget, parent=None):
        super().__init__(title, parent)
        self.panel_id = panel_id
        self.title_str = title
        self.icon_name = icon_name
        self.content_widget = widget
        self._in_rail = False
        self._dragging_float = False

        self.setObjectName(f"dock_{panel_id}")
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )

        self.custom_title = DockTitleBar(self, title, icon_name)
        self.setTitleBarWidget(self.custom_title)
        self.custom_title.installEventFilter(self)

        # Envolver contenido en contenedor limpio
        self._host = QWidget()
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 0, 0)
        self._host_lay.setSpacing(0)
        if widget is not None:
            self._host_lay.addWidget(widget)
        self.setWidget(self._host)

        self.topLevelChanged.connect(self._on_top_level)
        self.visibilityChanged.connect(lambda vis: self.visibilityChangedByUser.emit(self.panel_id, vis))

    def take_content(self) -> QWidget:
        if self.content_widget is not None:
            self._host_lay.removeWidget(self.content_widget)
        return self.content_widget

    def restore_content(self):
        if self.content_widget is not None and self.content_widget.parent() is not self._host:
            self._host_lay.addWidget(self.content_widget)
            self.content_widget.show()

    def _on_top_level(self, floating: bool):
        self.setProperty("isFloating", "true" if floating else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.custom_title.refresh_icons()

    def eventFilter(self, obj, ev):
        if obj is self.custom_title and self.isFloating():
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._dragging_float = True
            elif ev.type() == QEvent.MouseMove and self._dragging_float and (ev.buttons() & Qt.LeftButton):
                self.floatingMoved.emit(self, QCursor.pos())
            elif ev.type() == QEvent.MouseButtonRelease and self._dragging_float:
                self._dragging_float = False
                self.floatingReleased.emit(self, QCursor.pos())
        return super().eventFilter(obj, ev)

    def moveEvent(self, e):
        super().moveEvent(e)
        if self.isFloating() and (QApplication.mouseButtons() & Qt.LeftButton):
            self._dragging_float = True
            self.floatingMoved.emit(self, QCursor.pos())


# =====================================================  RailFlyout (Ventana desplegable del Rail)
class RailFlyout(QFrame):
    """Ventana flotante desplegada a la izquierda del PanelRail (256px / 236px compacto, alto 180-620px)."""
    closed = Signal()
    tearOffStarted = Signal(str, QPoint)

    def __init__(self, parent_viewport: QWidget):
        super().__init__(parent_viewport)
        self.setObjectName("railFlyout")
        self.panel_id = None
        self._content = None
        self._drag_start = None

        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(20)
        sh.setOffset(0, 4)
        sh.setColor(QColor(0, 0, 0, 88))
        self.setGraphicsEffect(sh)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("railFlyoutHeader")
        self.header.setFixedHeight(26)
        self.header.setCursor(Qt.OpenHandCursor)
        self.header.installEventFilter(self)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(8, 0, 4, 0)
        hl.setSpacing(6)

        self.ic_lbl = QLabel()
        self.ic_lbl.setFixedSize(13, 13)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("dockTitleLabel")
        self.b_close = QToolButton()
        self.b_close.setObjectName("dockActionButton")
        self.b_close.setFixedSize(19, 19)
        self.b_close.setIcon(icons.state_icon("close", 13))
        self.b_close.clicked.connect(self.close_flyout)

        hl.addWidget(self.ic_lbl)
        hl.addWidget(self.title_lbl, 1)
        hl.addWidget(self.b_close)
        v.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.scroll, 1)

        self.hide()
        theme.bus.changed.connect(lambda _: self.b_close.setIcon(icons.state_icon("close", 13)))

    def show_panel(self, panel_id: str, title: str, icon_name: str, widget: QWidget, rail_width: int = 30):
        self.clear_content()
        self.panel_id = panel_id
        self._content = widget
        self.ic_lbl.setPixmap(icons.pixmap(icon_name, C["muted"], 13))
        self.title_lbl.setText(title.upper())

        if hasattr(widget, "editor") and hasattr(widget.editor, "heightHintChanged"):
            try:
                widget.editor.heightHintChanged.connect(self.reposition, Qt.UniqueConnection)
            except Exception:
                pass

        self.scroll.setWidget(widget)
        widget.show()
        self.reposition(rail_width)
        self.show()
        self.raise_()

    def clear_content(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.setParent(None)
        self._content = None
        self.panel_id = None

    def close_flyout(self):
        self.hide()
        self.closed.emit()

    def reposition(self, rail_width: int = 30):
        par = self.parentWidget()
        if par is None:
            return
        fw = DENSITY["flyout_w"]
        max_h = max(180, min(620, par.height() - 18))
        hint_h = 340
        if self._content is not None:
            sh = self._content.sizeHint().height()
            if sh > 40:
                hint_h = sh + 34
        fh = max(180, min(max_h, hint_h))
        x = max(6, par.width() - rail_width - fw - 4)
        self.setGeometry(x, 6, fw, fh)

    def eventFilter(self, obj, ev):
        if obj is self.header and self.panel_id:
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._drag_start = ev.globalPosition().toPoint()
                self.header.setCursor(Qt.ClosedHandCursor)
                return True
            elif ev.type() == QEvent.MouseMove and self._drag_start is not None and (ev.buttons() & Qt.LeftButton):
                gp = ev.globalPosition().toPoint()
                if (gp - self._drag_start).manhattanLength() > 10:
                    pid = self.panel_id
                    self._drag_start = None
                    self.header.setCursor(Qt.OpenHandCursor)
                    self.tearOffStarted.emit(pid, gp)
                    return True
            elif ev.type() == QEvent.MouseButtonRelease:
                self._drag_start = None
                self.header.setCursor(Qt.OpenHandCursor)
        return super().eventFilter(obj, ev)


# =====================================================  PanelRail (Barra vertical estilo Illustrator)
class _RailItemButton(QToolButton):
    dragMoved = Signal(str, QPoint)
    dragReleased = Signal(str, QPoint)

    def __init__(self, panel_id: str, icon_name: str, title: str):
        super().__init__()
        self.panel_id = panel_id
        self.icon_name = icon_name
        self._press_pos = None
        self._dragging = False
        self.setObjectName("railButton")
        self.setCheckable(True)
        self.setFixedSize(24, 24)
        self.setIconSize(QSize(16, 16))
        self.setToolTip(title)
        self.setCursor(Qt.PointingHandCursor)
        self.refresh_icon()

    def refresh_icon(self):
        self.setIcon(icons.state_icon(self.icon_name, 16))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_pos is not None and (e.buttons() & Qt.LeftButton):
            gp = e.globalPosition().toPoint()
            if not self._dragging and (gp - self._press_pos).manhattanLength() >= 6:
                self._dragging = True
            if self._dragging:
                self.dragMoved.emit(self.panel_id, gp)
                e.accept()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self._press_pos = None
            self.dragReleased.emit(self.panel_id, e.globalPosition().toPoint())
            e.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(e)


class PanelRail(QFrame):
    """Barra vertical fija de 30px pegada al borde derecho del viewport central."""
    panelClicked = Signal(str)
    collapseRightToggled = Signal()
    reordered = Signal(list)
    tearOffRequested = Signal(str, QPoint)
    togglePanelInRailRequested = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panelRail")
        self.setFixedWidth(30)
        self._entries = []          # [(panel_id, title, icon_name)]
        self._all_catalog = []      # [(panel_id, title, icon_name)]
        self._btns = {}
        self._insert_idx = -1
        self._insert_y = -1
        self._right_collapsed = False

        v = QVBoxLayout(self)
        v.setContentsMargins(3, 4, 3, 4)
        v.setSpacing(3)

        # Arriba: contraer / expandir docks derechos
        self.b_collapse = QToolButton()
        self.b_collapse.setObjectName("railButton")
        self.b_collapse.setFixedSize(24, 22)
        self.b_collapse.setIconSize(QSize(14, 14))
        self.b_collapse.setToolTip("Contraer / Expandir paneles derechos")
        self.b_collapse.clicked.connect(self.collapseRightToggled.emit)
        v.addWidget(self.b_collapse)

        div = QFrame()
        div.setObjectName("toolSeparator")
        div.setFixedHeight(1)
        v.addWidget(div)

        self.btn_host = QWidget()
        self.btn_lay = QVBoxLayout(self.btn_host)
        self.btn_lay.setContentsMargins(0, 2, 0, 2)
        self.btn_lay.setSpacing(3)
        v.addWidget(self.btn_host)

        v.addStretch(1)

        # Abajo: botón '...' InstantPopup
        self.b_more = QToolButton()
        self.b_more.setObjectName("railButton")
        self.b_more.setFixedSize(24, 24)
        self.b_more.setIconSize(QSize(15, 15))
        self.b_more.setPopupMode(QToolButton.InstantPopup)
        self.b_more.setToolTip("Elegir paneles de la barra rápida")
        self.more_menu = QMenu(self)
        self.more_menu.aboutToShow.connect(self._populate_more_menu)
        self.b_more.setMenu(self.more_menu)
        v.addWidget(self.b_more)

        self._refresh_chrome_icons()
        theme.bus.changed.connect(lambda _: self._refresh_all_icons())

    def set_right_collapsed_visual(self, collapsed: bool):
        self._right_collapsed = collapsed
        self._refresh_chrome_icons()

    def _refresh_chrome_icons(self):
        ic = "chevron_left" if self._right_collapsed else "chevron_right"
        self.b_collapse.setIcon(icons.state_icon(ic, 14))
        self.b_more.setIcon(icons.state_icon("more", 15))

    def _refresh_all_icons(self):
        self._refresh_chrome_icons()
        for b in self._btns.values():
            b.refresh_icon()

    def set_catalog(self, all_panels):
        self._all_catalog = list(all_panels)

    def set_entries(self, entries, active_id=None):
        self._entries = list(entries)
        while self.btn_lay.count():
            w = self.btn_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._btns = {}
        for pid, title, ic in self._entries:
            b = _RailItemButton(pid, ic, title)
            b.setChecked(pid == active_id)
            b.clicked.connect(lambda _, p=pid: self.panelClicked.emit(p))
            b.dragMoved.connect(self._on_btn_drag_moved)
            b.dragReleased.connect(self._on_btn_drag_released)
            self.btn_lay.addWidget(b)
            self._btns[pid] = b

    def set_active(self, active_id):
        for pid, b in self._btns.items():
            b.blockSignals(True)
            b.setChecked(pid == active_id)
            b.blockSignals(False)

    def panel_ids(self):
        return [e[0] for e in self._entries]

    def set_drop_target(self, active: bool, global_pos: QPoint = None):
        cur = self.property("dropTarget") == "true"
        if cur != active:
            self.setProperty("dropTarget", "true" if active else "false")
            self.style().unpolish(self)
            self.style().polish(self)
        if active and global_pos is not None:
            self._compute_insert_indicator(global_pos)
        else:
            self._insert_idx = -1
            self._insert_y = -1
        self.update()

    def insert_index_at(self, global_pos: QPoint) -> int:
        self._compute_insert_indicator(global_pos)
        return max(0, self._insert_idx)

    def _compute_insert_indicator(self, global_pos: QPoint):
        local = self.mapFromGlobal(global_pos)
        host_top = self.btn_host.y()
        if not self._entries:
            self._insert_idx = 0
            self._insert_y = host_top + 4
            return
        for i, (pid, _, _) in enumerate(self._entries):
            b = self._btns.get(pid)
            if b is None:
                continue
            by = host_top + b.y()
            mid = by + b.height() * 0.5
            if local.y() < mid:
                self._insert_idx = i
                self._insert_y = by - 1
                return
        last_b = self._btns[self._entries[-1][0]]
        self._insert_idx = len(self._entries)
        self._insert_y = host_top + last_b.y() + last_b.height() + 2

    def _is_near_rail(self, global_pos: QPoint, tol: int = 24) -> bool:
        tl = self.mapToGlobal(QPoint(0, 0))
        r = QRect(tl.x() - tol, tl.y() - 10, self.width() + tol * 2, self.height() + 20)
        return r.contains(global_pos)

    def _on_btn_drag_moved(self, pid: str, gp: QPoint):
        if self._is_near_rail(gp, tol=24):
            self._compute_insert_indicator(gp)
            self.update()
        else:
            self._insert_idx = -1
            self._insert_y = -1
            self.update()
            self.tearOffRequested.emit(pid, gp)

    def _on_btn_drag_released(self, pid: str, gp: QPoint):
        if self._insert_idx >= 0 and self._is_near_rail(gp, tol=26):
            ids = [e[0] for e in self._entries]
            if pid in ids:
                old_i = ids.index(pid)
                item = self._entries.pop(old_i)
                ins = self._insert_idx - (1 if self._insert_idx > old_i else 0)
                self._entries.insert(max(0, min(len(self._entries), ins)), item)
                self._insert_idx = -1
                self._insert_y = -1
                self.set_entries(self._entries)
                self.reordered.emit(self.panel_ids())
                return
        self._insert_idx = -1
        self._insert_y = -1
        self.update()

    def _populate_more_menu(self):
        self.more_menu.clear()
        in_rail = set(self.panel_ids())
        for pid, title, ic in self._all_catalog:
            act = self.more_menu.addAction(icons.state_icon(ic, 14), title)
            act.setCheckable(True)
            act.setChecked(pid in in_rail)
            act.toggled.connect(lambda on, p=pid: self.togglePanelInRailRequested.emit(p, on))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._insert_y >= 0:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            ac = QColor(C["accent"])
            y = float(self._insert_y)
            p.setPen(QPen(ac, 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(5.0, y), QPointF(self.width() - 5.0, y))
            p.setPen(Qt.NoPen)
            p.setBrush(ac)
            p.drawEllipse(QPointF(5.0, y), 2.0, 2.0)
            p.drawEllipse(QPointF(self.width() - 5.0, y), 2.0, 2.0)


# =====================================================  CanvasDock (Cápsula flotante estilo Figma)
class CanvasDock(QFrame):
    """Cápsula flotante centrada sobre la parte inferior del viewport (y = vh - dh - 18)."""
    actionTriggered = Signal(str, bool)

    def __init__(self, parent_viewport: QWidget):
        super().__init__(parent_viewport)
        self.setObjectName("canvasDock")

        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(18)
        self.shadow.setOffset(0, 4)
        self.setGraphicsEffect(self.shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 5, 7, 5)
        lay.setSpacing(4)

        self.btns = {}
        spec = [
            ("select", "cursor", "Seleccionar y transformar (V / E)", True, True),
            ("pan", "hand", "Mano / Orbitar vista (Rueda central)", True, False),
            ("calibrate", "calibrate", "Calibrar pose neutral / voz", False, False),
            ("emote", "emote", "Disparar expresión / reacción rápida", False, False),
            None,
            ("guides", "grid", "Cuadrícula y guías de zona segura OBS", True, True),
            ("chroma", "alpha", "Alternar Fondo Transparente / Chroma Key", True, False),
            ("webcam", "webcam", "Seguimiento por Cámara ON / OFF", True, False),
            ("mic", "mic", "Micrófono en vivo ON / OFF", True, True),
        ]
        for entry in spec:
            if entry is None:
                div = QFrame()
                div.setObjectName("canvasDockDivider")
                div.setFixedSize(1, 18)
                lay.addWidget(div)
                continue
            aid, ic, tip, chk, init_on = entry
            b = QToolButton()
            b.setObjectName("canvasDockButton")
            sz = DENSITY["canvas_btn"]
            b.setFixedSize(sz, sz)
            b.setIconSize(QSize(17, 17))
            b.setCheckable(chk)
            if chk:
                b.setChecked(init_on)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            if chk:
                b.toggled.connect(lambda on, k=aid: self.actionTriggered.emit(k, on))
            else:
                b.clicked.connect(lambda _, k=aid: self.actionTriggered.emit(k, True))
            self.btns[aid] = (b, ic)
            lay.addWidget(b)

        self.refresh_theme()
        theme.bus.changed.connect(lambda _: self.refresh_theme())

    def refresh_theme(self):
        sz = DENSITY["canvas_btn"]
        self.shadow.setColor(QColor(0, 0, 0, 82) if theme.is_dark() else QColor(15, 23, 42, 26))
        for b, ic in self.btns.values():
            b.setFixedSize(sz, sz)
            b.setIcon(icons.state_icon(ic, 17))
        self.adjustSize()
        self.reposition()

    def set_checked_silent(self, aid: str, on: bool):
        if aid in self.btns:
            b, _ = self.btns[aid]
            b.blockSignals(True)
            b.setChecked(bool(on))
            b.blockSignals(False)

    def reposition(self, right_margin: int = 30):
        par = self.parentWidget()
        if par is None:
            return
        self.adjustSize()
        avail_w = max(100, par.width() - right_margin)
        x = max(8, int((avail_w - self.width()) * 0.5))
        y = max(8, par.height() - self.height() - 18)
        self.move(x, y)


# =====================================================  Tear-Off Drag Controller + #dockDropGuide
class TearOffDragController(QObject):
    """Gestiona el desprendimiento en vivo desde el Rail hacia flotante o bordes (<=54px con #dockDropGuide)."""

    def __init__(self, main_win):
        super().__init__(main_win)
        self.win = main_win
        self.active_dock = None
        self.target_edge = None  # None | Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea

        self.guide = QFrame(main_win)
        self.guide.setObjectName("dockDropGuide")
        self.guide.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.guide.hide()

    def begin(self, dock: StudioDockWidget, global_pos: QPoint):
        self.active_dock = dock
        self.target_edge = None
        dock.restore_content()
        dock.setFloating(True)
        dock.setWindowOpacity(0.90)
        dock.resize(max(248, DENSITY["dock_right_w"]), 360)
        dock.move(global_pos.x() - 90, global_pos.y() - 14)
        dock.show()
        dock.raise_()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if self.active_dock is None:
            return False
        if ev.type() == QEvent.MouseMove:
            gp = QCursor.pos()
            self.active_dock.move(gp.x() - 90, gp.y() - 14)
            self._update_drop_guide(gp)
            return True
        if ev.type() == QEvent.MouseButtonRelease:
            gp = QCursor.pos()
            dock = self.active_dock
            edge = self.target_edge
            self.active_dock = None
            self.target_edge = None
            self.guide.hide()
            QApplication.instance().removeEventFilter(self)
            dock.setWindowOpacity(1.0)
            if self.win.rail._is_near_rail(gp, tol=22):
                idx = self.win.rail.insert_index_at(gp)
                self.win.rail.set_drop_target(False)
                self.win.absorb_dock_into_rail(dock.panel_id, idx)
            elif edge is not None:
                dock.setFloating(False)
                self.win.addDockWidget(edge, dock)
                dock.show()
            return True
        return False

    def _update_drop_guide(self, gp: QPoint):
        # Si vuelve sobre el rail, mostrar objetivo en el rail
        if self.win.rail.isVisible() and self.win.rail._is_near_rail(gp, tol=22):
            self.guide.hide()
            self.target_edge = None
            self.win.rail.set_drop_target(True, gp)
            return
        self.win.rail.set_drop_target(False)

        local = self.win.mapFromGlobal(gp)
        w, h = self.win.width(), self.win.height()
        top_y = self.win.chrome.height() if hasattr(self.win, "chrome") else 95
        bot_h = self.win.statusBar().height()
        body_h = max(120, h - top_y - bot_h)

        if local.x() <= 54:
            self.target_edge = Qt.LeftDockWidgetArea
            self.guide.setGeometry(6, top_y + 4, 248, body_h - 8)
            self.guide.show()
            self.guide.raise_()
        elif local.x() >= w - 54:
            self.target_edge = Qt.RightDockWidgetArea
            self.guide.setGeometry(w - 254, top_y + 4, 248, body_h - 8)
            self.guide.show()
            self.guide.raise_()
        elif local.y() >= h - bot_h - 54:
            self.target_edge = Qt.BottomDockWidgetArea
            self.guide.setGeometry(8, h - bot_h - 208, w - 16, 202)
            self.guide.show()
            self.guide.raise_()
        else:
            self.target_edge = None
            self.guide.hide()
