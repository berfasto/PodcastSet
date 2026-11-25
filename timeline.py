"""Línea de tiempo multipista y Editor de Ondas en 'Studio UI':
- Fondo #0D0F12 (bg), carriles de pista en #14171C (panel) separados por 1px solid #1E232B (border_subtle).
- Regla de tiempo superior en #14171C con texto 7.5pt monoespaciado en #697284 (muted).
- Clips de audio como tarjetas redondeadas (border-radius: 5px, fondo #1B1F26, borde 1px solid #262C36;
  clip seleccionado con fondo rgba(99,102,241,0.16) y borde 1px solid #6366F1).
- Forma de onda nítida en #A5B4FC / #6366F1 y cursor de reproducción (playhead) de 1.5px en #6366F1."""
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

import icons
import model as M
import theme
from audio_engine import PEAKS_HZ, clip_effective_duration, gain_to_db
from theme import C

RULER = 24
DIRECTOR_H = 22
TOP_H = RULER + DIRECTOR_H
ROW = 52
HEADER_W = 220
EDGE_PX = 8


def fmt_tc(t, tenths=False):
    t = max(0.0, float(t))
    m = int(t) // 60
    s = int(t) % 60
    if tenths:
        d = int((t - int(t)) * 10)
        return f"{m:02d}:{s:02d}.{d}"
    return f"{m:02d}:{s:02d}"


def _valid_audio_urls(mime):
    if not mime.hasUrls():
        return []
    out = []
    for u in mime.urls():
        if u.isLocalFile():
            p = Path(u.toLocalFile())
            if p.suffix.lower() in M.AUDIO_SUFFIXES:
                out.append(str(p))
    return out


class TrackHeaderColumn(QWidget):
    """Cabeceras de pista Studio UI: botones compactos R / S / M / + de 22x22 px."""
    armToggled = Signal(str, bool)
    soloToggled = Signal(str, bool)
    muteToggled = Signal(str, bool)
    addAudioClicked = Signal(str)
    trackSelected = Signal(str)
    fileDropped = Signal(str, str)
    addSubtrackRequested = Signal(str)
    reassignSubtrackRequested = Signal(str, str)
    renameSubtrackRequested = Signal(str)
    deleteSubtrackRequested = Signal(str)
    addMarkerClicked = Signal()

    def __init__(self):
        super().__init__()
        self.tracks = []
        self.characters = []
        self.selected_cid = None
        self._drop_row = -1
        self._hover_btn = None
        self.setFixedWidth(HEADER_W)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

    def fit(self):
        h = TOP_H + max(1, len(self.tracks)) * ROW + 8
        self.setMinimumHeight(h)
        self.setFixedHeight(h)

    def _btn_rects(self, i):
        y = TOP_H + i * ROW
        cy = y + ROW / 2.0
        bw, gap = 22.0, 3.0
        x_add = HEADER_W - 8.0 - bw
        x_mute = x_add - gap - bw
        x_solo = x_mute - gap - bw
        x_arm = x_solo - gap - bw
        return {
            "arm": QRectF(x_arm, cy - 11.0, bw, 22.0),
            "solo": QRectF(x_solo, cy - 11.0, bw, 22.0),
            "mute": QRectF(x_mute, cy - 11.0, bw, 22.0),
            "add": QRectF(x_add, cy - 11.0, bw, 22.0),
        }

    def _dir_add_rect(self):
        return QRectF(HEADER_W - 26, RULER + 2, 18, DIRECTOR_H - 4)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(C["panel"]))

        # Regla cabecera
        p.fillRect(QRectF(0, 0, HEADER_W, RULER), QColor(C["panel"]))
        p.setPen(QColor(C["border_subtle"]))
        p.drawLine(0, RULER - 1, HEADER_W, RULER - 1)
        p.setPen(QColor(C["muted"]))
        p.setFont(QFont("Segoe UI", 7.5, QFont.Bold))
        p.drawText(QRectF(10, 0, HEADER_W - 20, RULER), Qt.AlignVCenter | Qt.AlignLeft, "PISTAS  (R · S · M)")

        # Cabecera Dirección
        p.fillRect(QRectF(0, RULER, HEADER_W, DIRECTOR_H), QColor(C["bg"]))
        p.setPen(QColor(C["border_subtle"]))
        p.drawLine(0, TOP_H - 1, HEADER_W, TOP_H - 1)
        p.setPen(QColor(C["dim"]))
        p.setFont(QFont("Segoe UI", 7.4, QFont.DemiBold))
        p.drawText(QRectF(10, RULER, HEADER_W - 40, DIRECTOR_H), Qt.AlignVCenter | Qt.AlignLeft, "DIRECCIÓN · EVENTOS")
        dr = self._dir_add_rect()
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(QColor(C["surface"]))
        p.drawRoundedRect(dr, 4, 4)
        p.drawPixmap(int(dr.center().x() - 5), int(dr.center().y() - 5), icons.pixmap("plus", C["dim"], 10))

        for i, tr in enumerate(self.tracks):
            y = TOP_H + i * ROW
            row_r = QRectF(0, y, HEADER_W, ROW)
            sel = tr["cid"] == self.selected_cid and not tr.get("is_sub")
            if i == self._drop_row:
                p.fillRect(row_r, theme.selection_qcolor())
            elif sel:
                p.fillRect(row_r, theme.selection_qcolor())
            else:
                p.fillRect(row_r, QColor(C["panel"]))

            p.setPen(QColor(C["border_subtle"]))
            p.drawLine(0, y + ROW - 1, HEADER_W, y + ROW - 1)

            col = QColor(C["accent"] if sel else tr["color"])
            is_sub = tr.get("is_sub", False)
            p.fillRect(QRectF(0, y + 4, 3.0, ROW - 8), col if not is_sub else col.darker(135))

            if is_sub:
                p.setPen(QColor(C["muted"]))
                p.setFont(QFont("Segoe UI", 7.8))
                p.drawText(QRectF(10, y + 5, HEADER_W - 118, 18), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(f"↳ {tr['label']}", Qt.ElideRight, HEADER_W - 120))
                p.setPen(QColor(tr["color"]))
                p.setFont(QFont("Segoe UI", 7.2))
                p.drawText(QRectF(19, y + 23, HEADER_W - 124, 16), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(tr["char_name"], Qt.ElideRight, HEADER_W - 126))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(tr["color"]))
                p.drawEllipse(QRectF(10, y + ROW / 2.0 - 3.5, 7, 7))
                p.setPen(QColor(C["selection_text"] if sel else (C["text"] if not tr["mute"] else C["muted"])))
                p.setFont(QFont("Segoe UI", 8.4, QFont.DemiBold if sel else QFont.Normal))
                name = p.fontMetrics().elidedText(tr["label"], Qt.ElideRight, HEADER_W - 130)
                p.drawText(QRectF(22, y, HEADER_W - 128, ROW), Qt.AlignVCenter | Qt.AlignLeft, name)

            rects = self._btn_rects(i)
            for kind, r in rects.items():
                hov = self._hover_btn == (i, kind)
                active = (kind == "arm" and tr["arm"]) or (kind == "solo" and tr["solo"]) or (kind == "mute" and tr["mute"])
                if active:
                    if kind in ("arm", "mute"):
                        bg = QColor("#E5484D")
                    else:
                        bg = QColor("#F59E0B")
                    border = bg
                else:
                    bg = QColor(C["hover"] if hov else C["input"])
                    border = QColor(C["accent"] if hov else C["border"])
                p.setPen(QPen(border, 1))
                p.setBrush(bg)
                p.drawRoundedRect(r, 4, 4)

                if kind == "add":
                    pm = icons.pixmap("plus", C["text"] if hov else C["dim"], 11)
                    p.drawPixmap(int(r.center().x() - 5.5), int(r.center().y() - 5.5), pm)
                else:
                    lbl = {"arm": "R", "solo": "S", "mute": "M"}[kind]
                    txt_col = "#0D0F12" if (kind == "solo" and active) else ("#FFFFFF" if active else (C["text"] if hov else C["dim"]))
                    p.setPen(QColor(txt_col))
                    p.setFont(QFont("Segoe UI", 7.5, QFont.Bold))
                    p.drawText(r, Qt.AlignCenter, lbl)

        p.setPen(QColor(C["border"]))
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, self.height())

    def _row_at(self, y):
        if y < TOP_H:
            return -1
        i = int((y - TOP_H) // ROW)
        return i if 0 <= i < len(self.tracks) else -1

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._dir_add_rect().contains(pos):
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Añadir marcador de Escena, Emoción, Reacción o Capítulo en el cursor")
            return
        i = self._row_at(pos.y())
        hov = None
        tips = {
            "arm": "Armar pista para grabar voz (R)",
            "solo": "Escuchar solo esta pista (S)",
            "mute": "Silenciar pista (M)",
            "add": "Añadir archivo de audio a esta pista",
        }
        if i >= 0:
            for kind, r in self._btn_rects(i).items():
                if r.contains(pos):
                    hov = (i, kind)
                    self.setToolTip(tips[kind])
                    break
            if hov is None:
                self.setToolTip("Clic derecho: añadir sub-pista · Arrastra archivos de audio aquí")
        if hov != self._hover_btn:
            self._hover_btn = hov
            self.setCursor(Qt.PointingHandCursor if hov else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover_btn = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        if self._dir_add_rect().contains(pos):
            self.addMarkerClicked.emit()
            return
        i = self._row_at(pos.y())
        if i < 0:
            return
        tr = self.tracks[i]
        rects = self._btn_rects(i)
        if rects["arm"].contains(pos):
            self.armToggled.emit(tr["id"], not tr["arm"])
        elif rects["solo"].contains(pos):
            self.soloToggled.emit(tr["id"], not tr["solo"])
        elif rects["mute"].contains(pos):
            self.muteToggled.emit(tr["id"], not tr["mute"])
        elif rects["add"].contains(pos):
            self.addAudioClicked.emit(tr["id"])
        else:
            self.trackSelected.emit(tr["cid"])

    def contextMenuEvent(self, e):
        i = self._row_at(e.pos().y())
        if i < 0:
            return
        tr = self.tracks[i]
        m = QMenu(self)
        a_sub = m.addAction(icons.state_icon("plus", 14), f"Añadir sub-pista para «{tr['char_name']}»")
        a_ren = a_del = None
        re_acts = {}
        if tr.get("is_sub"):
            sub = m.addMenu("Asignar a personaje")
            for ch in self.characters:
                act = sub.addAction(ch["name"])
                act.setCheckable(True)
                act.setChecked(ch["id"] == tr["cid"])
                re_acts[act] = ch["id"]
            a_ren = m.addAction(icons.state_icon("edit", 14), "Renombrar sub-pista")
            m.addSeparator()
            a_del = m.addAction(icons.icon("trash", C["danger"], 14), "Eliminar sub-pista")
        chosen = m.exec(e.globalPos())
        if chosen == a_sub:
            self.addSubtrackRequested.emit(tr["cid"])
        elif chosen == a_ren:
            self.renameSubtrackRequested.emit(tr["id"])
        elif chosen == a_del:
            self.deleteSubtrackRequested.emit(tr["id"])
        elif chosen in re_acts:
            self.reassignSubtrackRequested.emit(tr["id"], re_acts[chosen])

    def dragEnterEvent(self, e):
        if _valid_audio_urls(e.mimeData()) and self.tracks:
            e.acceptProposedAction()
            r = self._row_at(e.position().y())
            self._drop_row = max(0, min(len(self.tracks) - 1, r if r >= 0 else 0))
            self.update()

    def dragMoveEvent(self, e):
        if _valid_audio_urls(e.mimeData()) and self.tracks:
            e.acceptProposedAction()
            r = self._row_at(e.position().y())
            self._drop_row = max(0, min(len(self.tracks) - 1, r if r >= 0 else 0))
            self.update()

    def dragLeaveEvent(self, e):
        self._drop_row = -1
        self.update()

    def dropEvent(self, e):
        urls = _valid_audio_urls(e.mimeData())
        r = self._drop_row if self._drop_row >= 0 else 0
        self._drop_row = -1
        self.update()
        if urls and 0 <= r < len(self.tracks):
            tid = self.tracks[r]["id"]
            for path in urls:
                self.fileDropped.emit(tid, path)
            e.acceptProposedAction()


class TrackCanvas(QWidget):
    """Lienzo multipista Studio UI con ondas decimadas en NumPy (#A5B4FC / #6366F1) y playhead 1.5px."""
    changed = Signal()
    seekRequested = Signal(float)
    inOutChanged = Signal(object, object)
    fileDropped = Signal(str, str, float)
    addAudioRequested = Signal(str, float)
    clipEdited = Signal(str, str, float, float, float, float, float, float)
    clipSplit = Signal(str, float, bool)
    clipDeleted = Signal(str)
    clipDuplicated = Signal(str)
    trackSelected = Signal(str)
    markerAddRequested = Signal(float, str)
    markerMoved = Signal(str, float)
    markerDeleted = Signal(str)

    def __init__(self):
        super().__init__()
        self.tracks = []
        self.clips = []
        self.markers = []
        self.pps = 55.0
        self.playhead = 0.0
        self.in_t = None
        self.out_t = None
        self.tool = "select"
        self.snap = True
        self.sel_clip = None
        self.sel_marker = None
        self.tl_rec = None
        self._drag_mode = None
        self._drag_clip = None
        self._drag_marker = None
        self._drag_offset = 0.0
        self._drag_orig = None
        self._pan_origin = None
        self._hover_t = None
        self._snap_x = None
        self._drop_preview = None
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def content_duration(self):
        t_clips = max((c["start"] + clip_effective_duration(c) for c in self.clips), default=0.0)
        t_rec = self.tl_rec[1] if self.tl_rec else 0.0
        t_mk = max((m.get("time", 0.0) for m in self.markers), default=0.0)
        return max(t_clips, t_rec, t_mk)

    def max_time(self):
        io_end = self.out_t or 0.0
        return max(15.0, self.content_duration() + 6.0, io_end + 4.0, self.playhead + 6.0)

    def fit(self):
        n = max(1, len(self.tracks))
        w = max(860, int(self.max_time() * self.pps) + 120)
        h = TOP_H + n * ROW + 8
        self.setMinimumSize(w, h)
        self.setFixedHeight(h)

    def _row_index(self, tid):
        for i, tr in enumerate(self.tracks):
            if tr["id"] == tid:
                return i
        return -1

    def _row_at(self, y):
        if y < TOP_H:
            return -1
        i = int((y - TOP_H) // ROW)
        return i if 0 <= i < len(self.tracks) else -1

    def _t(self, x):
        return max(0.0, x / self.pps)

    def _clip_rect(self, clip):
        tid = clip.get("tid") or clip["cid"]
        row = self._row_index(tid)
        if row < 0:
            row = self._row_index(clip["cid"])
        if row < 0:
            return None
        dur = clip_effective_duration(clip)
        x = clip["start"] * self.pps
        w = max(14.0, dur * self.pps)
        y = TOP_H + row * ROW + 4
        return QRectF(x, y, w, ROW - 8)

    def _gain_y(self, r, gain):
        top = r.top() + 17
        bot = r.bottom() - 4
        norm = max(0.0, min(2.0, float(gain))) / 2.0
        return bot - norm * (bot - top)

    def _y_to_gain(self, r, y):
        top = r.top() + 17
        bot = r.bottom() - 4
        norm = (bot - y) / max(1.0, (bot - top))
        return round(max(0.0, min(2.0, norm * 2.0)), 3)

    def _hit_marker(self, pos):
        if not (RULER <= pos.y() <= TOP_H):
            return None
        for mk in reversed(self.markers):
            mx = mk["time"] * self.pps
            if mx - 8 <= pos.x() <= mx + 82:
                return mk
        return None

    def _hit_clip(self, pos):
        for clip in reversed(self.clips):
            r = self._clip_rect(clip)
            if r and r.contains(pos):
                if r.width() >= 32 and pos.y() <= r.top() + 15:
                    if pos.x() - r.left() <= 12:
                        return clip, "fade_l"
                    if r.right() - pos.x() <= 12:
                        return clip, "fade_r"
                if r.width() >= 24:
                    if pos.x() - r.left() <= EDGE_PX:
                        return clip, "left"
                    if r.right() - pos.x() <= EDGE_PX:
                        return clip, "right"
                gy = self._gain_y(r, clip.get("gain", 1.0))
                if pos.y() > r.top() + 15 and abs(pos.y() - gy) <= 5.5:
                    return clip, "gain"
                return clip, "body"
        return None, None

    def _snap_time(self, t, exclude_id=None):
        self._snap_x = None
        if not self.snap:
            return max(0.0, t)
        tol = 9.0 / self.pps
        candidates = [0.0, self.playhead]
        if self.in_t is not None:
            candidates.append(self.in_t)
        if self.out_t is not None:
            candidates.append(self.out_t)
        for mk in self.markers:
            if mk["id"] != exclude_id:
                candidates.append(mk["time"])
        for c in self.clips:
            if c["id"] == exclude_id:
                continue
            candidates.append(c["start"])
            candidates.append(c["start"] + clip_effective_duration(c))
        best, best_d = t, tol
        for cand in candidates:
            d = abs(cand - t)
            if d < best_d:
                best, best_d = cand, d
        if best != t:
            self._snap_x = best * self.pps
        return max(0.0, best)

    def _draw_waveform_fast(self, p, peaks, r, vis, tin, eff_dur, cg, wcol):
        if peaks is None or len(peaks) == 0:
            return
        wave_top = r.top() + 16
        wave_h = max(4.0, r.height() - 19)
        mid_y = wave_top + wave_h * 0.5
        pps = self.pps

        vx0 = max(float(vis.left()), r.left())
        vx1 = min(float(vis.right()), r.right())
        if vx1 <= vx0:
            return
        t0_rel = max(0.0, (vx0 - r.left()) / pps)
        t1_rel = min(eff_dur, (vx1 - r.left()) / pps)
        k0 = max(0, int((tin + t0_rel) * PEAKS_HZ))
        k1 = min(len(peaks), int(np.ceil((tin + t1_rel) * PEAKS_HZ)) + 1)
        if k1 <= k0:
            return
        seg = peaks[k0:k1]
        vis_px = vx1 - vx0
        n_bars = min(len(seg), max(1, int(vis_px / 2.2)))
        if n_bars < len(seg):
            edges = np.linspace(0, len(seg), n_bars + 1, dtype=np.int32)
            vals = np.maximum.reduceat(seg, edges[:-1])
        else:
            vals = seg
        step_x = vis_px / max(1, len(vals))
        bar_w = max(1.15, step_x * 0.78)
        amps = np.minimum(1.0, vals * cg)
        bhs = np.maximum(1.2, amps * (wave_h - 2.0))
        p.setPen(Qt.NoPen)
        p.setBrush(wcol)
        for i in range(len(bhs)):
            bh = float(bhs[i])
            p.drawRect(QRectF(vx0 + i * step_x, mid_y - bh * 0.5, bar_w, bh))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        vis = e.rect()
        # Fondo base #0D0F12 (bg)
        p.fillRect(vis, QColor(C["bg"]))

        pps = self.pps
        # Regla superior en #14171C (panel)
        p.fillRect(QRectF(vis.left(), 0, vis.width(), RULER), QColor(C["panel"]))
        # Carril de Dirección en #0D0F12 (bg)
        p.fillRect(QRectF(vis.left(), RULER, vis.width(), DIRECTOR_H), QColor(C["bg"]))

        # Zona In / Out en acento índigo
        if self.in_t is not None and self.out_t is not None and self.out_t > self.in_t + 0.02:
            x0, x1 = self.in_t * pps, self.out_t * pps
            ac = QColor(C["accent"])
            p.fillRect(QRectF(x0, 0, x1 - x0, RULER), QColor(ac.red(), ac.green(), ac.blue(), 65))
            p.fillRect(QRectF(x0, TOP_H, x1 - x0, self.height() - TOP_H), QColor(ac.red(), ac.green(), ac.blue(), 22))
            p.setPen(QPen(ac, 1.4))
            p.drawLine(QPointF(x0, 0), QPointF(x0, self.height()))
            p.drawLine(QPointF(x1, 0), QPointF(x1, self.height()))

        # Carriles de pista en #14171C (panel) separados por 1px solid #1E232B (border_subtle)
        for i, tr in enumerate(self.tracks):
            y = TOP_H + i * ROW
            p.fillRect(QRectF(vis.left(), y, vis.width(), ROW), QColor(C["panel"]))
            p.setPen(QColor(C["border_subtle"]))
            p.drawLine(vis.left(), y + ROW - 1, vis.right(), y + ROW - 1)

            if self.tl_rec and tr["id"] in self.tl_rec[2]:
                r0, r1 = self.tl_rec[0], self.tl_rec[1]
                peaks_map = self.tl_rec[3] if len(self.tl_rec) > 3 else {}
                rr = QRectF(r0 * pps, y + 4, max(6.0, (r1 - r0) * pps), ROW - 8)
                path = QPainterPath()
                path.addRoundedRect(rr, 5, 5)
                p.fillPath(path, QColor(229, 72, 77, 45))
                p.save()
                p.setClipPath(path)
                p.fillRect(QRectF(rr.left(), rr.top(), rr.width(), 15), QColor(C["invalid"]))
                p.setPen(QColor("#FFFFFF"))
                p.setFont(QFont("Segoe UI", 7.4, QFont.Bold))
                p.drawText(QRectF(rr.left() + 6, rr.top(), max(20.0, rr.width() - 10), 15),
                           Qt.AlignVCenter | Qt.AlignLeft, "● REC")
                live_peaks = peaks_map.get(tr["id"])
                if live_peaks:
                    self._draw_waveform_fast(p, np.asarray(live_peaks, dtype=np.float32),
                                             rr, vis, 0.0, max(0.05, r1 - r0), 1.0, QColor(C["invalid"]))
                p.restore()
                p.setPen(QPen(QColor(C["invalid"]), 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rr, 5, 5)

        # Marcas de tiempo (7.5pt monoespaciado en #697284 muted)
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * pps >= 76), 600)
        sub = step / 4.0
        p.setFont(QFont("Consolas", 7.5))

        t_sub = max(0.0, int(vis.left() / pps / sub) * sub)
        while t_sub * pps < vis.right() + 40:
            x = int(t_sub * pps)
            p.setPen(QColor(C["border_subtle"]))
            p.drawLine(x, RULER - 4, x, RULER)
            t_sub += sub

        t = max(0.0, int(vis.left() / pps / step) * step)
        while t * pps < vis.right() + 80:
            x = int(t * pps)
            p.setPen(QColor(C["muted"]))
            p.drawLine(x, RULER - 7, x, RULER)
            p.drawText(x + 4, 15, fmt_tc(t, tenths=step < 1))
            p.setPen(QColor(C["border_subtle"]))
            p.drawLine(x, TOP_H, x, self.height())
            t += step

        p.setPen(QColor(C["border_subtle"]))
        p.drawLine(vis.left(), RULER - 1, vis.right(), RULER - 1)
        p.drawLine(vis.left(), TOP_H - 1, vis.right(), TOP_H - 1)

        # Marcadores de Dirección
        p.setFont(QFont("Segoe UI", 7.3, QFont.DemiBold))
        for mk in self.markers:
            mx = mk["time"] * pps
            if mx + 120 < vis.left() or mx - 20 > vis.right():
                continue
            mc = QColor(mk.get("color", C["accent"]))
            sel_m = mk["id"] == self.sel_marker
            p.setPen(QPen(QColor(mc.red(), mc.green(), mc.blue(), 65), 1, Qt.DashLine))
            p.drawLine(QPointF(mx, TOP_H), QPointF(mx, self.height()))
            lbl = mk.get("label", "")
            tw = min(130, max(36, p.fontMetrics().horizontalAdvance(lbl) + 14))
            flag = QRectF(mx, RULER + 2.5, tw, DIRECTOR_H - 5)
            p.setPen(QPen(QColor("#FFFFFF") if sel_m else mc, 1.2 if sel_m else 1.0))
            p.setBrush(QColor(mc.red(), mc.green(), mc.blue(), 200 if sel_m else 140))
            p.drawRoundedRect(flag, 3, 3)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(flag.adjusted(5, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(lbl, Qt.ElideRight, int(tw - 9)))

        # Clips de audio como tarjetas Studio (radius 5px, #1B1F26 + #262C36; seleccionado selection_bg + #6366F1)
        for clip in self.clips:
            r = self._clip_rect(clip)
            if r is None or r.right() < vis.left() or r.left() > vis.right():
                continue
            tid = clip.get("tid") or clip["cid"]
            row = self._row_index(tid)
            if row < 0:
                row = self._row_index(clip["cid"])
            tr = self.tracks[row]
            sel = clip["id"] == self.sel_clip
            missing = clip.get("missing", False)
            loading = clip.get("loading", False)
            eff_dur = clip_effective_duration(clip)
            tin = max(0.0, float(clip.get("trim_in", 0.0)))
            cg = float(clip.get("gain", 1.0))

            path = QPainterPath()
            path.addRoundedRect(r, 5, 5)
            p.fillPath(path, QColor(C["surface"]))
            if sel:
                p.fillPath(path, theme.selection_qcolor())

            p.save()
            p.setClipPath(path)
            hdr = QRectF(r.left(), r.top(), r.width(), 15)
            hdr_bg = QColor(C["invalid"] if missing else (C["accent"] if sel else C["hover"]))
            hdr_bg.setAlpha(210 if (sel or missing) else 190)
            p.fillRect(hdr, hdr_bg)
            # Indicador lateral sutil del color del personaje
            p.fillRect(QRectF(r.left(), r.top(), 3.0, r.height()), QColor(tr["color"]))

            if not missing and not loading:
                wcol = QColor(C["selection_text"] if sel else C["accent"])
                wcol.setAlpha(235 if sel else 195)
                self._draw_waveform_fast(p, clip.get("peaks"), r, vis, tin, eff_dur, cg, wcol)

                fi = min(eff_dur * 0.5, max(0.0, float(clip.get("fade_in", 0.0))))
                fo = min(eff_dur * 0.5, max(0.0, float(clip.get("fade_out", 0.0))))
                if fi > 0.02:
                    fw = fi * pps
                    poly = QPolygonF([QPointF(r.left(), r.top() + 15), QPointF(r.left() + fw, r.top() + 15), QPointF(r.left(), r.bottom())])
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(0, 0, 0, 85))
                    p.drawPolygon(poly)
                    p.setPen(QPen(QColor(C["selection_text"]), 1.1))
                    p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left() + fw, r.top() + 15))
                if fo > 0.02:
                    fw = fo * pps
                    poly = QPolygonF([QPointF(r.right(), r.top() + 15), QPointF(r.right() - fw, r.top() + 15), QPointF(r.right(), r.bottom())])
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(0, 0, 0, 85))
                    p.drawPolygon(poly)
                    p.setPen(QPen(QColor(C["selection_text"]), 1.1))
                    p.drawLine(QPointF(r.right() - fw, r.top() + 15), QPointF(r.right(), r.bottom()))

                gy = self._gain_y(r, cg)
                line_col = QColor(C["warn"] if (self._drag_mode == "gain" and self._drag_clip is clip) else C["dim"])
                line_col.setAlpha(155)
                p.setPen(QPen(line_col, 1.1))
                p.drawLine(QPointF(r.left() + 4, gy), QPointF(r.right() - 3, gy))

            p.setPen(QColor("#FFFFFF" if (sel or missing) else C["text"]))
            p.setFont(QFont("Segoe UI", 7.4, QFont.DemiBold))
            if missing:
                label = f"Falta archivo: {clip['name']}"
            elif loading:
                label = f"Cargando onda... {clip['name']}"
            else:
                db_str = f" · {gain_to_db(cg)}" if abs(cg - 1.0) > 0.02 else ""
                label = f"{clip['name']}  ·  {fmt_tc(eff_dur)}{db_str}"
            elided = p.fontMetrics().elidedText(label, Qt.ElideRight, max(10, int(r.width() - 12)))
            p.drawText(QRectF(r.left() + 7, r.top(), r.width() - 11, 15), Qt.AlignVCenter | Qt.AlignLeft, elided)
            p.restore()

            border_col = QColor(C["invalid"] if missing else (C["accent"] if sel else C["border"]))
            p.setPen(QPen(border_col, 1.0, Qt.DashLine if missing else Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 5, 5)

        if self._drag_mode == "gain" and self._drag_clip is not None:
            r = self._clip_rect(self._drag_clip)
            if r:
                cg = float(self._drag_clip.get("gain", 1.0))
                txt = f"{gain_to_db(cg)} ({int(cg * 100)}%)"
                p.setFont(QFont("Consolas", 7.8, QFont.Bold))
                tw = p.fontMetrics().horizontalAdvance(txt) + 14
                bx = max(4.0, r.center().x() - tw / 2)
                by = max(TOP_H + 2, r.top() - 21)
                p.setPen(QPen(QColor(C["border"]), 1.0))
                p.setBrush(QColor(C["surface"]))
                p.drawRoundedRect(QRectF(bx, by, tw, 18), 4, 4)
                p.setPen(QColor(C["text"]))
                p.drawText(QRectF(bx, by, tw, 18), Qt.AlignCenter, txt)

        if self._snap_x is not None:
            p.setPen(QPen(QColor(C["accent"]), 1.1, Qt.DashLine))
            p.drawLine(QPointF(self._snap_x, RULER), QPointF(self._snap_x, self.height()))

        if self.tool == "razor" and self._hover_t is not None:
            rx = self._hover_t * pps
            p.setPen(QPen(QColor(C["invalid"]), 1.4, Qt.DashLine))
            p.drawLine(QPointF(rx, TOP_H), QPointF(rx, self.height()))

        if self._drop_preview is not None:
            row, dt, fname = self._drop_preview
            if 0 <= row < len(self.tracks):
                y = TOP_H + row * ROW
                p.fillRect(QRectF(0, y, self.width(), ROW), theme.selection_qcolor())
                dx = dt * pps
                p.setPen(QPen(QColor(C["accent"]), 1.8))
                p.drawLine(QPointF(dx, TOP_H), QPointF(dx, y + ROW))
                tag = f"Soltar «{fname}» ({fmt_tc(dt, True)})"
                p.setFont(QFont("Segoe UI", 8.0, QFont.DemiBold))
                tw = p.fontMetrics().horizontalAdvance(tag) + 14
                tr_rect = QRectF(dx + 6, y + 6, tw, 20)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(C["accent"]))
                p.drawRoundedRect(tr_rect, 4, 4)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(tr_rect, Qt.AlignCenter, tag)

        if self.tracks and not self.clips and not self.tl_rec and self._drop_preview is None:
            p.setPen(QColor(C["muted"]))
            p.setFont(QFont("Segoe UI", 8.3))
            p.drawText(QRectF(20, TOP_H, 820, ROW * len(self.tracks)),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       "Arrastra audios aquí o arma (R) y graba voz · Carril superior: clic derecho para escenas/emociones · I / O: zona")

        # Cursor de reproducción (playhead) de 1.5px en #6366F1 (accent)
        px = self.playhead * pps
        p.setPen(QPen(QColor(C["accent"]), 1.5))
        p.drawLine(QPointF(px, RULER - 2), QPointF(px, self.height()))
        tri = QPolygonF([QPointF(px - 5.0, 2), QPointF(px + 5.0, 2), QPointF(px + 5.0, 10),
                         QPointF(px, RULER - 1), QPointF(px - 5.0, 10)])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["accent"]))
        p.drawPolygon(tri)

    # ---------- interacción ----------
    def _start_pan(self, e):
        sb_h = self.parent_panel.scroll.horizontalScrollBar()
        sb_v = self.parent_panel.scroll.verticalScrollBar()
        self._drag_mode = "pan"
        gp = e.globalPosition()
        self._pan_origin = (gp.x(), gp.y(), sb_h.value(), sb_v.value())
        self.setCursor(Qt.ClosedHandCursor)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self.tool == "hand"):
            self._start_pan(e)
            return
        if e.button() != Qt.LeftButton:
            return

        pos = e.position()
        t = self._t(pos.x())
        if pos.y() < RULER:
            if self.in_t is not None and abs(pos.x() - self.in_t * self.pps) <= 9:
                self._drag_mode = "in_h"
                return
            if self.out_t is not None and abs(pos.x() - self.out_t * self.pps) <= 9:
                self._drag_mode = "out_h"
                return
            self._drag_mode = "scrub"
            self.playhead = t
            self.seekRequested.emit(t)
            self.update()
            return

        if RULER <= pos.y() < TOP_H:
            mk = self._hit_marker(pos)
            if mk is not None:
                self.sel_marker = mk["id"]
                self._drag_marker = mk
                self._drag_mode = "marker"
                self._drag_offset = t - mk["time"]
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return
            self.sel_marker = None
            self._drag_mode = "scrub"
            self.playhead = t
            self.seekRequested.emit(t)
            self.update()
            return

        if self.tool == "razor":
            all_tracks = bool(e.modifiers() & Qt.ShiftModifier)
            clip, _ = self._hit_clip(pos)
            split_t = self._snap_time(t)
            if all_tracks:
                self.clipSplit.emit("", split_t, True)
            elif clip is not None:
                self.clipSplit.emit(clip["id"], split_t, False)
            return

        clip, zone = self._hit_clip(pos)
        if clip is not None:
            self.sel_clip = clip["id"]
            self.sel_marker = None
            self._drag_clip = clip
            self._drag_orig = (
                clip["start"], float(clip.get("trim_in", 0.0)), float(clip.get("trim_out", 0.0)),
                float(clip.get("gain", 1.0)), float(clip.get("fade_in", 0.0)), float(clip.get("fade_out", 0.0)),
            )
            if zone == "left":
                self._drag_mode = "trim_l"
                self.setCursor(Qt.SizeHorCursor)
            elif zone == "right":
                self._drag_mode = "trim_r"
                self.setCursor(Qt.SizeHorCursor)
            elif zone == "fade_l":
                self._drag_mode = "fade_l"
                self.setCursor(Qt.SizeHorCursor)
            elif zone == "fade_r":
                self._drag_mode = "fade_r"
                self.setCursor(Qt.SizeHorCursor)
            elif zone == "gain":
                self._drag_mode = "gain"
                self.setCursor(Qt.SizeVerCursor)
            else:
                self._drag_mode = "clip"
                self._drag_offset = t - clip["start"]
                self.setCursor(Qt.ClosedHandCursor)
            self.trackSelected.emit(clip["cid"])
            self.changed.emit()
            self.update()
            return

        self.sel_clip = None
        self.sel_marker = None
        row = self._row_at(pos.y())
        if row >= 0:
            self.trackSelected.emit(self.tracks[row]["cid"])
        self._drag_mode = "scrub"
        self.playhead = t
        self.seekRequested.emit(t)
        self.changed.emit()
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        t = self._t(pos.x())
        self._hover_t = t

        if self._drag_mode == "pan" and self._pan_origin is not None:
            gx, gy, h0, v0 = self._pan_origin
            gp = e.globalPosition()
            self.parent_panel.scroll.horizontalScrollBar().setValue(int(h0 - (gp.x() - gx)))
            self.parent_panel.scroll.verticalScrollBar().setValue(int(v0 - (gp.y() - gy)))
            return

        if self._drag_mode == "marker" and self._drag_marker is not None:
            self._drag_marker["time"] = self._snap_time(max(0.0, t - self._drag_offset), exclude_id=self._drag_marker["id"])
            self.fit()
            self.update()
            return

        if self._drag_mode == "in_h":
            lim = (self.out_t - 0.1) if self.out_t is not None else 1e9
            self.in_t = max(0.0, min(lim, self._snap_time(t)))
            self.update()
            return

        if self._drag_mode == "out_h":
            lim = (self.in_t + 0.1) if self.in_t is not None else 0.1
            self.out_t = max(lim, self._snap_time(t))
            self.update()
            return

        if self._drag_mode == "scrub":
            self.playhead = t
            self.seekRequested.emit(t)
            self.update()
            return

        if self._drag_mode == "clip" and self._drag_clip is not None:
            raw_start = max(0.0, t - self._drag_offset)
            self._drag_clip["start"] = self._snap_time(raw_start, exclude_id=self._drag_clip["id"])
            row = self._row_at(pos.y())
            if 0 <= row < len(self.tracks):
                tr = self.tracks[row]
                self._drag_clip["tid"] = tr["id"]
                self._drag_clip["cid"] = tr["cid"]
            self.fit()
            self.update()
            return

        if self._drag_mode == "trim_l" and self._drag_clip is not None:
            c = self._drag_clip
            s0, tin0, tout0 = self._drag_orig[:3]
            src_dur = float(c.get("src_duration", c["duration"]))
            end_fixed = s0 + max(0.05, src_dur - tin0 - tout0)
            new_s = max(max(0.0, s0 - tin0), min(end_fixed - 0.1, self._snap_time(t, exclude_id=c["id"])))
            c["start"] = new_s
            c["trim_in"] = max(0.0, tin0 + (new_s - s0))
            self.update()
            return

        if self._drag_mode == "trim_r" and self._drag_clip is not None:
            c = self._drag_clip
            s0, tin0 = self._drag_orig[:2]
            src_dur = float(c.get("src_duration", c["duration"]))
            max_end = s0 + (src_dur - tin0)
            new_end = max(s0 + 0.1, min(max_end, self._snap_time(t, exclude_id=c["id"])))
            c["trim_out"] = max(0.0, max_end - new_end)
            self.fit()
            self.update()
            return

        if self._drag_mode == "fade_l" and self._drag_clip is not None:
            c = self._drag_clip
            eff = clip_effective_duration(c)
            c["fade_in"] = round(max(0.0, min(eff * 0.5, t - c["start"])), 3)
            self.update()
            return

        if self._drag_mode == "fade_r" and self._drag_clip is not None:
            c = self._drag_clip
            eff = clip_effective_duration(c)
            c["fade_out"] = round(max(0.0, min(eff * 0.5, (c["start"] + eff) - t)), 3)
            self.update()
            return

        if self._drag_mode == "gain" and self._drag_clip is not None:
            r = self._clip_rect(self._drag_clip)
            if r:
                self._drag_clip["gain"] = self._y_to_gain(r, pos.y())
                self.update()
            return

        if self.tool == "hand":
            self.setCursor(Qt.OpenHandCursor)
        elif self.tool == "razor":
            self.setCursor(Qt.SplitHCursor)
            self.update()
        elif pos.y() < RULER:
            self.setCursor(Qt.SizeHorCursor)
        elif pos.y() < TOP_H:
            self.setCursor(Qt.OpenHandCursor if self._hit_marker(pos) else Qt.ArrowCursor)
        else:
            clip, zone = self._hit_clip(pos)
            if clip is not None:
                cur = Qt.SizeHorCursor if zone in ("left", "right", "fade_l", "fade_r") else (Qt.SizeVerCursor if zone == "gain" else Qt.OpenHandCursor)
                self.setCursor(cur)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag_mode in ("clip", "trim_l", "trim_r", "fade_l", "fade_r", "gain") and self._drag_clip is not None:
            c = self._drag_clip
            tid = c.get("tid") or c["cid"]
            self.clipEdited.emit(
                c["id"], tid, c["start"],
                float(c.get("trim_in", 0.0)), float(c.get("trim_out", 0.0)),
                float(c.get("gain", 1.0)), float(c.get("fade_in", 0.0)), float(c.get("fade_out", 0.0)),
            )
        elif self._drag_mode == "marker" and self._drag_marker is not None:
            self.markerMoved.emit(self._drag_marker["id"], float(self._drag_marker["time"]))
        elif self._drag_mode in ("in_h", "out_h"):
            self.inOutChanged.emit(self.in_t, self.out_t)
        self._drag_mode = None
        self._drag_clip = None
        self._drag_marker = None
        self._drag_orig = None
        self._pan_origin = None
        self._snap_x = None
        self.setCursor(Qt.OpenHandCursor if self.tool == "hand" else (Qt.SplitHCursor if self.tool == "razor" else Qt.ArrowCursor))
        self.changed.emit()
        self.update()

    def leaveEvent(self, e):
        self._hover_t = None
        if self.tool == "razor":
            self.update()

    def contextMenuEvent(self, e):
        pos = e.pos()
        t = self._t(pos.x())
        m = QMenu(self)
        if pos.y() < RULER:
            a_in = m.addAction(icons.state_icon("in_pt", 14), f"Marcar inicio de zona (I) en {fmt_tc(t, True)}")
            a_out = m.addAction(icons.state_icon("out_pt", 14), f"Marcar fin de zona (O) en {fmt_tc(t, True)}")
            a_clr = m.addAction(icons.state_icon("close", 14), "Limpiar zona In / Out")
            chosen = m.exec(e.globalPos())
            if chosen == a_in:
                self.parent_panel.set_in_point(t)
            elif chosen == a_out:
                self.parent_panel.set_out_point(t)
            elif chosen == a_clr:
                self.parent_panel.clear_in_out()
            return

        if RULER <= pos.y() < TOP_H:
            mk = self._hit_marker(QPointF(pos))
            if mk is not None:
                a_del = m.addAction(icons.icon("trash", C["danger"], 14), f"Eliminar marcador «{mk['label']}»")
                if m.exec(e.globalPos()) == a_del:
                    self.markerDeleted.emit(mk["id"])
                return
            a_sc = m.addAction(icons.state_icon("layers", 14), f"Cambio de escena en {fmt_tc(t, True)}...")
            a_ex = m.addAction(icons.state_icon("emote", 14), f"Cambio de emoción en {fmt_tc(t, True)}...")
            a_em = m.addAction(icons.state_icon("sparkle", 14), f"Disparar reacción/emote en {fmt_tc(t, True)}...")
            a_ch = m.addAction(icons.state_icon("chapter", 14), f"Capítulo YouTube en {fmt_tc(t, True)}...")
            chosen = m.exec(e.globalPos())
            for act, k in ((a_sc, "scene"), (a_ex, "expr"), (a_em, "emote"), (a_ch, "chapter")):
                if chosen == act:
                    self.markerAddRequested.emit(t, k)
            return

        clip, _ = self._hit_clip(QPointF(pos))
        if clip is not None:
            self.sel_clip = clip["id"]
            self.update()
            a_split = m.addAction(icons.state_icon("scissors", 14), f"Dividir clip aquí ({fmt_tc(t, True)})")
            a_vol = m.addAction(icons.state_icon("volume", 14), "Restablecer volumen (0 dB)")
            a_fades = m.addAction(icons.state_icon("wave", 14), "Fade suave (0.3s entrada/salida)")
            a_dup = m.addAction(icons.state_icon("copy", 14), "Duplicar clip")
            sub = m.addMenu("Mover a pista")
            move_acts = {}
            cur_tid = clip.get("tid") or clip["cid"]
            for tr in self.tracks:
                lbl = f"{tr['char_name']} · {tr['label']}" if tr.get("is_sub") else tr["label"]
                act = sub.addAction(lbl)
                act.setEnabled(tr["id"] != cur_tid)
                move_acts[act] = tr["id"]
            m.addSeparator()
            a_del = m.addAction(icons.icon("trash", C["danger"], 14), "Eliminar clip")
            chosen = m.exec(e.globalPos())
            fi, fo = float(clip.get("fade_in", 0.0)), float(clip.get("fade_out", 0.0))
            if chosen == a_split:
                self.clipSplit.emit(clip["id"], t, False)
            elif chosen == a_vol:
                self.clipEdited.emit(clip["id"], cur_tid, clip["start"],
                                     float(clip.get("trim_in", 0.0)), float(clip.get("trim_out", 0.0)), 1.0, fi, fo)
            elif chosen == a_fades:
                self.clipEdited.emit(clip["id"], cur_tid, clip["start"],
                                     float(clip.get("trim_in", 0.0)), float(clip.get("trim_out", 0.0)),
                                     float(clip.get("gain", 1.0)), 0.3, 0.3)
            elif chosen == a_dup:
                self.clipDuplicated.emit(clip["id"])
            elif chosen == a_del:
                self.clipDeleted.emit(clip["id"])
            elif chosen in move_acts:
                self.clipEdited.emit(clip["id"], move_acts[chosen], clip["start"],
                                     float(clip.get("trim_in", 0.0)), float(clip.get("trim_out", 0.0)),
                                     float(clip.get("gain", 1.0)), fi, fo)
        else:
            row = self._row_at(pos.y())
            if 0 <= row < len(self.tracks):
                tr = self.tracks[row]
                a_add = m.addAction(icons.state_icon("music", 14), f"Añadir audio a «{tr['label']}» en {fmt_tc(t, True)}...")
                a_in = m.addAction(icons.state_icon("in_pt", 14), "Marcar inicio de zona aquí (I)")
                a_out = m.addAction(icons.state_icon("out_pt", 14), "Marcar fin de zona aquí (O)")
                chosen = m.exec(e.globalPos())
                if chosen == a_add:
                    self.addAudioRequested.emit(tr["id"], t)
                elif chosen == a_in:
                    self.parent_panel.set_in_point(t)
                elif chosen == a_out:
                    self.parent_panel.set_out_point(t)

    def dragEnterEvent(self, e):
        urls = _valid_audio_urls(e.mimeData())
        if urls and self.tracks:
            e.acceptProposedAction()
            row = self._row_at(e.position().y())
            row = max(0, min(len(self.tracks) - 1, row if row >= 0 else 0))
            self._drop_preview = (row, self._t(e.position().x()), Path(urls[0]).name)
            self.update()

    def dragMoveEvent(self, e):
        urls = _valid_audio_urls(e.mimeData())
        if urls and self.tracks:
            e.acceptProposedAction()
            row = self._row_at(e.position().y())
            row = max(0, min(len(self.tracks) - 1, row if row >= 0 else 0))
            self._drop_preview = (row, self._t(e.position().x()), Path(urls[0]).name)
            self.update()

    def dragLeaveEvent(self, e):
        self._drop_preview = None
        self.update()

    def dropEvent(self, e):
        urls = _valid_audio_urls(e.mimeData())
        preview = self._drop_preview
        self._drop_preview = None
        self.update()
        if urls and self.tracks:
            row = preview[0] if preview else max(0, self._row_at(e.position().y()))
            row = max(0, min(len(self.tracks) - 1, row))
            t = preview[1] if preview else self._t(e.position().x())
            tid = self.tracks[row]["id"]
            for path in urls:
                self.fileDropped.emit(tid, path, t)
            e.acceptProposedAction()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.sel_marker:
                self.markerDeleted.emit(self.sel_marker)
                self.sel_marker = None
                return
            if self.sel_clip:
                self.clipDeleted.emit(self.sel_clip)
                self.sel_clip = None
                return
        elif e.key() == Qt.Key_V:
            self.parent_panel.set_tool("select")
        elif e.key() == Qt.Key_C:
            self.parent_panel.set_tool("razor")
        elif e.key() == Qt.Key_H:
            self.parent_panel.set_tool("hand")
        elif e.key() == Qt.Key_I:
            self.parent_panel.set_in_point(self.playhead)
        elif e.key() == Qt.Key_O:
            self.parent_panel.set_out_point(self.playhead)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.parent_panel.zoom(1.25 if e.angleDelta().y() > 0 else 0.8)
            e.accept()
        elif e.modifiers() & Qt.ShiftModifier:
            sb = self.parent_panel.scroll.horizontalScrollBar()
            sb.setValue(sb.value() - int(e.angleDelta().y() * 0.8))
            e.accept()
        else:
            super().wheelEvent(e)


class TimelinePanel(QFrame):
    playToggled = Signal(bool)
    stopRequested = Signal()
    loopToggled = Signal(bool)
    recTracksToggled = Signal(bool)
    seekRequested = Signal(float)
    inOutChanged = Signal(object, object)
    fileDropped = Signal(str, str, float)
    addAudioRequested = Signal(str, float)
    addSubtrackRequested = Signal(str)
    reassignSubtrackRequested = Signal(str, str)
    renameSubtrackRequested = Signal(str)
    deleteSubtrackRequested = Signal(str)
    clipEdited = Signal(str, str, float, float, float, float, float, float)
    clipSplit = Signal(str, float, bool)
    clipDeleted = Signal(str)
    clipDuplicated = Signal(str)
    undoRequested = Signal()
    redoRequested = Signal()
    armToggled = Signal(str, bool)
    soloToggled = Signal(str, bool)
    muteToggled = Signal(str, bool)
    trackSelected = Signal(str)
    markerAddRequested = Signal(float, str)
    markerMoved = Signal(str, float)
    markerDeleted = Signal(str)
    closeRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("timeline")
        self._playing = False
        self._last_px = -1

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("tlbar")
        top = QHBoxLayout(bar)
        top.setContentsMargins(10, 3, 10, 3)
        top.setSpacing(4)

        self.tool_grp = QButtonGroup(self)
        self.tool_btns = {}
        for tid, ic, tip in (
            ("select", "cursor", "Selección / Mover / Recortar / Fades / Volumen (V)"),
            ("razor", "scissors", "Cuchilla: clic corta clip, Shift+clic corta todo (C)"),
            ("hand", "hand", "Mano: arrastrar para desplazarse (H o botón central)"),
        ):
            b = self._btn(ic, tip, lambda on, t=tid: on and self.set_tool(t), checkable=True)
            self.tool_grp.addButton(b)
            self.tool_btns[tid] = b
            top.addWidget(b)

        self.b_snap = self._btn("magnet", "Imán a bordes, marcadores y cursor", self._toggle_snap, checkable=True)
        self.b_undo = self._btn("undo", "Deshacer (Ctrl+Z)", self.undoRequested.emit)
        self.b_redo = self._btn("redo", "Rehacer (Ctrl+Y)", self.redoRequested.emit)
        top.addWidget(self.b_snap)
        top.addWidget(self.b_undo)
        top.addWidget(self.b_redo)

        top.addStretch(1)

        self.b_tl_rec = self._btn("record", "Grabar voz en pistas armadas con R (Ctrl+R)", self.recTracksToggled.emit, "Grabar", checkable=True)
        self.b_tl_rec.setObjectName("rec")
        self.b_play = self._btn("play", "Reproducir / Pausar (Espacio)", self.playToggled.emit, checkable=True)
        self.b_stop = self._btn("skip_back", "Detener y volver al inicio", self.stopRequested.emit)
        self.b_loop = self._btn("loop", "Bucle (proyecto o zona In/Out)", self.loopToggled.emit, checkable=True)
        self.tc_lbl = QLabel("00:00.0 / 00:00.0")
        self.tc_lbl.setObjectName("timecode")
        for w in (self.b_tl_rec, self.b_play, self.b_stop, self.b_loop, self.tc_lbl):
            top.addWidget(w)

        top.addStretch(1)

        self.b_mk = self._btn("chapter", "Añadir marcador de Escena / Emoción / Emote / Capítulo", self._open_marker_menu, "Evento")
        self.b_sub = self._btn("plus", "Añadir sub-pista de audio", self._on_add_subtrack, "Pista")
        self.b_add = self._btn("music", "Añadir archivo de audio", self._on_add_selected, "Audio")
        self.b_more = self._btn("more", "Zona In/Out, dividir en cursor, zoom y ocultar", self._open_more_menu)
        for w in (self.b_mk, self.b_sub, self.b_add, self.b_more):
            top.addWidget(w)
        v.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.headers = TrackHeaderColumn()
        self.headers.armToggled.connect(self.armToggled.emit)
        self.headers.soloToggled.connect(self.soloToggled.emit)
        self.headers.muteToggled.connect(self.muteToggled.emit)
        self.headers.addAudioClicked.connect(lambda tid: self.addAudioRequested.emit(tid, self.canvas.playhead))
        self.headers.trackSelected.connect(self.trackSelected.emit)
        self.headers.fileDropped.connect(lambda tid, path: self.fileDropped.emit(tid, path, self.canvas.playhead))
        self.headers.addSubtrackRequested.connect(self.addSubtrackRequested.emit)
        self.headers.reassignSubtrackRequested.connect(self.reassignSubtrackRequested.emit)
        self.headers.renameSubtrackRequested.connect(self.renameSubtrackRequested.emit)
        self.headers.deleteSubtrackRequested.connect(self.deleteSubtrackRequested.emit)
        self.headers.addMarkerClicked.connect(self._open_marker_menu)

        self.h_scroll = QScrollArea()
        self.h_scroll.setWidgetResizable(False)
        self.h_scroll.setWidget(self.headers)
        self.h_scroll.setFixedWidth(HEADER_W)
        self.h_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.h_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.h_scroll.setFrameShape(QFrame.NoFrame)

        self.canvas = TrackCanvas()
        self.canvas.parent_panel = self
        self.canvas.seekRequested.connect(self.seekRequested.emit)
        self.canvas.inOutChanged.connect(self._on_io_dragged)
        self.canvas.fileDropped.connect(self.fileDropped.emit)
        self.canvas.addAudioRequested.connect(self.addAudioRequested.emit)
        self.canvas.clipEdited.connect(self.clipEdited.emit)
        self.canvas.clipSplit.connect(self.clipSplit.emit)
        self.canvas.clipDeleted.connect(self.clipDeleted.emit)
        self.canvas.clipDuplicated.connect(self.clipDuplicated.emit)
        self.canvas.trackSelected.connect(self.trackSelected.emit)
        self.canvas.markerAddRequested.connect(self.markerAddRequested.emit)
        self.canvas.markerMoved.connect(self.markerMoved.emit)
        self.canvas.markerDeleted.connect(self.markerDeleted.emit)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setWidget(self.canvas)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.scroll.verticalScrollBar().valueChanged.connect(self.h_scroll.verticalScrollBar().setValue)
        self.h_scroll.verticalScrollBar().valueChanged.connect(self.scroll.verticalScrollBar().setValue)

        body.addWidget(self.h_scroll)
        body.addWidget(self.scroll, 1)
        v.addLayout(body, 1)

        self.tool_btns["select"].setChecked(True)
        self.b_snap.setChecked(True)
        self.setMinimumHeight(140)
        theme.bus.changed.connect(lambda _: self.refresh())

    def _btn(self, name, tip, cb, text="", checkable=False):
        if text:
            b = QPushButton(" " + text)
            b.setFixedHeight(25)
        else:
            b = QToolButton()
            b.setObjectName("toolbarButton")
            b.setFixedSize(25, 25)
        b.setIcon(icons.state_icon(name, 14))
        b.setIconSize(QSize(14, 14))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        (b.toggled if checkable else b.clicked).connect(cb)
        return b

    def set_tool(self, tool):
        self.canvas.tool = tool
        if tool in self.tool_btns:
            b = self.tool_btns[tool]
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        self.canvas.setCursor(Qt.OpenHandCursor if tool == "hand" else (Qt.SplitHCursor if tool == "razor" else Qt.ArrowCursor))
        self.canvas.update()

    def _toggle_snap(self, on):
        self.canvas.snap = on

    def _split_at_playhead(self):
        self.clipSplit.emit(self.canvas.sel_clip or "", self.canvas.playhead, not bool(self.canvas.sel_clip))

    def _on_add_selected(self):
        cid = self.headers.selected_cid or (self.headers.tracks[0]["id"] if self.headers.tracks else None)
        if cid:
            self.addAudioRequested.emit(cid, self.canvas.playhead)

    def _on_add_subtrack(self):
        cid = self.headers.selected_cid or (self.headers.characters[0]["id"] if self.headers.characters else None)
        if cid:
            self.addSubtrackRequested.emit(cid)

    def _open_marker_menu(self):
        t = self.canvas.playhead
        m = QMenu(self)
        a_sc = m.addAction(icons.state_icon("layers", 14), f"Cambio de escena en {fmt_tc(t, True)}...")
        a_ex = m.addAction(icons.state_icon("emote", 14), f"Cambio de emoción en {fmt_tc(t, True)}...")
        a_em = m.addAction(icons.state_icon("sparkle", 14), f"Reacción / Emote en {fmt_tc(t, True)}...")
        a_ch = m.addAction(icons.state_icon("chapter", 14), f"Capítulo YouTube en {fmt_tc(t, True)}...")
        chosen = m.exec(self.b_mk.mapToGlobal(self.b_mk.rect().bottomLeft()))
        for act, k in ((a_sc, "scene"), (a_ex, "expr"), (a_em, "emote"), (a_ch, "chapter")):
            if chosen == act:
                self.markerAddRequested.emit(t, k)

    def _open_more_menu(self):
        m = QMenu(self)
        a_sp = m.addAction(icons.state_icon("split", 14), "Dividir en el cursor (Ctrl+K)")
        m.addSeparator()
        a_in = m.addAction(icons.state_icon("in_pt", 14), "Marcar inicio de zona (I)")
        a_out = m.addAction(icons.state_icon("out_pt", 14), "Marcar fin de zona (O)")
        a_clr = m.addAction(icons.state_icon("close", 14), "Limpiar zona In / Out")
        a_clr.setEnabled(self.canvas.in_t is not None and self.canvas.out_t is not None)
        m.addSeparator()
        a_fit = m.addAction(icons.state_icon("fit", 14), "Ajustar zoom a todo")
        a_zin = m.addAction(icons.state_icon("zoom_in", 14), "Acercar (Ctrl+rueda)")
        a_zout = m.addAction(icons.state_icon("zoom_out", 14), "Alejar (Ctrl+rueda)")
        m.addSeparator()
        a_hide = m.addAction(icons.state_icon("close", 14), "Ocultar línea de tiempo (T)")
        chosen = m.exec(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))
        if chosen == a_sp:
            self._split_at_playhead()
        elif chosen == a_in:
            self.set_in_point(self.canvas.playhead)
        elif chosen == a_out:
            self.set_out_point(self.canvas.playhead)
        elif chosen == a_clr:
            self.clear_in_out()
        elif chosen == a_fit:
            self.zoom_fit()
        elif chosen == a_zin:
            self.zoom(1.25)
        elif chosen == a_zout:
            self.zoom(0.8)
        elif chosen == a_hide:
            self.closeRequested.emit()

    def set_in_out(self, in_t, out_t):
        self.canvas.in_t, self.canvas.out_t = in_t, out_t
        self.canvas.update()

    def set_in_point(self, t):
        t = max(0.0, float(t))
        out_t = self.canvas.out_t
        if out_t is None or out_t <= t + 0.05:
            out_t = max(t + 2.0, self.canvas.content_duration())
        self.set_in_out(t, out_t)
        self.inOutChanged.emit(t, out_t)

    def set_out_point(self, t):
        t = max(0.1, float(t))
        in_t = self.canvas.in_t
        if in_t is None or in_t >= t - 0.05:
            in_t = 0.0
        self.set_in_out(in_t, t)
        self.inOutChanged.emit(in_t, t)

    def clear_in_out(self):
        self.set_in_out(None, None)
        self.inOutChanged.emit(None, None)

    def _on_io_dragged(self, a, b):
        self.set_in_out(a, b)
        self.inOutChanged.emit(a, b)

    def set_tracks(self, tracks, characters, selected_cid=None):
        self.headers.tracks = self.canvas.tracks = list(tracks)
        self.headers.characters = list(characters)
        self.headers.selected_cid = selected_cid
        self.headers.fit()
        self.canvas.fit()
        self.headers.update()
        self.canvas.update()
        has_ch = bool(characters)
        self.b_add.setEnabled(has_ch)
        self.b_sub.setEnabled(has_ch)

    def set_clips(self, clips):
        self.canvas.clips = list(clips)
        self.canvas.fit()
        self.set_playhead(self.canvas.playhead, self._playing, force=True)
        self.canvas.update()

    def set_markers(self, markers):
        self.canvas.markers = list(markers)
        self.canvas.fit()
        self.canvas.update()

    def set_tl_rec(self, state):
        self.canvas.tl_rec = state
        on = state is not None
        self.b_tl_rec.blockSignals(True)
        self.b_tl_rec.setChecked(on)
        self.b_tl_rec.setText(" Detener" if on else " Grabar")
        self.b_tl_rec.setIcon(icons.icon("stop" if on else "record", "#FFFFFF" if on else C["invalid"], 13))
        self.b_tl_rec.blockSignals(False)
        if on:
            self.canvas.fit()
        self.canvas.update()

    def set_playhead(self, t, playing=None, force=False):
        self.canvas.playhead = t
        if playing is not None and playing != self._playing:
            self._playing = playing
            self.b_play.blockSignals(True)
            self.b_play.setChecked(playing)
            self.b_play.setIcon(icons.state_icon("pause" if playing else "play", 14))
            self.b_play.blockSignals(False)
        total = self.canvas.content_duration()
        self.tc_lbl.setText(f"{fmt_tc(t, True)} / {fmt_tc(total, True)}")
        px = int(t * self.canvas.pps)
        if playing:
            sb = self.scroll.horizontalScrollBar()
            vp = self.scroll.viewport().width()
            if px > sb.value() + vp - 40 or px < sb.value():
                sb.setValue(max(0, px - 60))
        if force or px != self._last_px:
            self._last_px = px
            self.canvas.update()

    def refresh(self):
        self.headers.fit()
        self.canvas.fit()
        self.canvas.update()
        self.headers.update()

    def zoom(self, f):
        self.canvas.pps = max(2.0, min(400.0, self.canvas.pps * f))
        self.refresh()

    def zoom_fit(self):
        total = self.canvas.content_duration()
        if total > 0.5:
            vp = max(200, self.scroll.viewport().width() - 40)
            self.canvas.pps = max(2.0, min(400.0, vp / total))
            self.scroll.horizontalScrollBar().setValue(0)
            self.refresh()
