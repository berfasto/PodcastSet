"""Módulos especializados de Audio, Mezclador, Rack DSP y Monitores de Tracking en 'Studio UI':
- AudioMixerRackPanel: canales por personaje + Master como tarjetas #1B1F26, faders ClickableSlider,
  perillas VectorKnob (#6366F1 / #262C36), botones compactos M (#E5484D), S (#F59E0B), FX (#6366F1) de 22x22
  y vúmetros delgados de 5px; más cadena de efectos de voz en vivo (EQ 3 bandas con curva, Compresor,
  Noise Gate, Supresor, Pitch/Formant, Reverb y Limitador).
- TrackingMonitorPanel: monitor gráfico sobre #181C22 con rejilla 1px en #1E232B, curva suave #6366F1
  con relleno degradado rgba(99,102,241,0.16) y barras de BlendShapes / Lip-Sync de 4px."""
import math
from collections import deque

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

import icons
import model as M
import theme
from theme import C
from widgets import ClickableSlider, ElidedLabel, LevelMeter, Section, SegmentedChoice, VectorKnob


# =====================================================  Sintetizador Ligero de Soundboard
def play_soundboard_sfx(kind: str):
    """Genera y reproduce un efecto limpio de estudio sin bloquear el hilo de interfaz."""
    try:
        sr = 44100
        if kind == "chime":
            t = np.linspace(0, 0.48, int(sr * 0.48), dtype=np.float32)
            env = np.exp(-t * 6.5)
            sig = (np.sin(2 * np.pi * 880 * t) * 0.5 + np.sin(2 * np.pi * 1318.5 * t) * 0.35) * env
        elif kind == "pop":
            t = np.linspace(0, 0.14, int(sr * 0.14), dtype=np.float32)
            freqs = np.linspace(260, 680, len(t), dtype=np.float32)
            sig = np.sin(2 * np.pi * np.cumsum(freqs) / sr) * np.exp(-t * 22.0) * 0.65
        elif kind == "lvlup":
            notes = (523.25, 659.25, 783.99, 1046.5)
            parts = []
            for f in notes:
                t = np.linspace(0, 0.11, int(sr * 0.11), dtype=np.float32)
                parts.append(np.sin(2 * np.pi * f * t) * np.exp(-t * 9.0) * 0.45)
            sig = np.concatenate(parts)
        else:  # buzzer
            t = np.linspace(0, 0.34, int(sr * 0.34), dtype=np.float32)
            sig = np.tanh(np.sin(2 * np.pi * 115 * t) * 2.5) * np.exp(-t * 4.5) * 0.42
        sd.play(sig.astype(np.float32), samplerate=sr, blocking=False)
    except Exception:
        pass


# =====================================================  Curva de Respuesta EQ / Tracking
class _ResponseCurveWidget(QWidget):
    """Gráfica Studio sobre fondo #181C22 con rejilla 1px en #1E232B, curva #6366F1 y relleno rgba(99,102,241,0.16)."""

    def __init__(self, height=62):
        super().__init__()
        self.setFixedHeight(height)
        self._pts = [0.5] * 48

    def set_points(self, values):
        self._pts = [max(0.0, min(1.0, float(v))) for v in values]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        p.setBrush(QColor(C["input"]))
        p.setPen(QPen(QColor(C["border"]), 1.0))
        p.drawRoundedRect(r, 4, 4)

        # Rejilla interna 1px en border_subtle
        p.setPen(QPen(QColor(C["border_subtle"]), 1.0))
        for gx in (0.25, 0.5, 0.75):
            x = r.left() + r.width() * gx
            p.drawLine(QPointF(x, r.top() + 1), QPointF(x, r.bottom() - 1))
        for gy in (0.25, 0.5, 0.75):
            y = r.top() + r.height() * gy
            p.drawLine(QPointF(r.left() + 1, y), QPointF(r.right() - 1, y))

        if len(self._pts) < 2:
            return
        n = len(self._pts)
        pad_x, pad_y = 4.0, 5.0
        w = max(4.0, r.width() - pad_x * 2)
        h = max(4.0, r.height() - pad_y * 2)

        pts = [
            QPointF(r.left() + pad_x + (i / (n - 1)) * w, r.bottom() - pad_y - self._pts[i] * h)
            for i in range(n)
        ]

        line_path = QPainterPath(pts[0])
        for i in range(1, n):
            mid = QPointF((pts[i - 1].x() + pts[i].x()) * 0.5, (pts[i - 1].y() + pts[i].y()) * 0.5)
            line_path.quadTo(pts[i - 1], mid)
        line_path.lineTo(pts[-1])

        fill_path = QPainterPath(line_path)
        fill_path.lineTo(QPointF(pts[-1].x(), r.bottom() - 1))
        fill_path.lineTo(QPointF(pts[0].x(), r.bottom() - 1))
        fill_path.closeSubpath()

        ac = QColor(C["accent"])
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(ac.red(), ac.green(), ac.blue(), 58))
        grad.setColorAt(1.0, QColor(ac.red(), ac.green(), ac.blue(), 6))
        p.fillPath(fill_path, grad)

        p.setPen(QPen(ac, 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line_path)


class _ThinBar(QWidget):
    """Barra de nivel de BlendShape / Lip-Sync de 4px de alto (border-radius: 2px)."""

    def __init__(self):
        super().__init__()
        self._val = 0.0
        self.setFixedHeight(4)

    def set_value(self, v: float):
        nv = max(0.0, min(1.0, float(v)))
        if abs(nv - self._val) > 0.008:
            self._val = nv
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["input"]))
        p.drawRoundedRect(r, 2.0, 2.0)
        if self._val > 0.01:
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRectF(0, 0, r.width() * self._val, r.height()), 2.0, 2.0)


# =====================================================  Mezclador + Rack de Efectos DSP de Voz
class _MixerChannelCard(QFrame):
    """Tarjeta #1B1F26 de canal de audio con M/S/FX de 22x22, fader ClickableSlider, knob umbral y vúmetro 5px."""

    def __init__(self, title: str, color_hex: str, gain_pct: int, thr_pct: int,
                 muted: bool, solo: bool, fx_on: bool,
                 on_gain, on_thr, on_mute, on_solo, on_fx):
        super().__init__()
        self.setObjectName("dspBlock")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 7, 8, 7)
        v.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(5)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{color_hex}; border-radius:4px;")
        nm = ElidedLabel(title)
        nm.setStyleSheet("font-weight:600; font-size:8.3pt;")
        top.addWidget(dot)
        top.addWidget(nm, 1)

        self.b_m = QToolButton()
        self.b_m.setObjectName("mixMute")
        self.b_m.setText("M")
        self.b_m.setCheckable(True)
        self.b_m.setChecked(muted)
        self.b_m.setFixedSize(22, 22)
        self.b_m.setToolTip("Silenciar (Mute)")
        self.b_m.toggled.connect(on_mute)

        self.b_s = QToolButton()
        self.b_s.setObjectName("mixSolo")
        self.b_s.setText("S")
        self.b_s.setCheckable(True)
        self.b_s.setChecked(solo)
        self.b_s.setFixedSize(22, 22)
        self.b_s.setToolTip("Solo")
        self.b_s.toggled.connect(on_solo)

        self.b_fx = QToolButton()
        self.b_fx.setObjectName("mixFx")
        self.b_fx.setText("FX")
        self.b_fx.setCheckable(True)
        self.b_fx.setChecked(fx_on)
        self.b_fx.setFixedSize(22, 22)
        self.b_fx.setToolTip("Activar / Bypass cadena DSP")
        self.b_fx.toggled.connect(on_fx)

        for b in (self.b_m, self.b_s, self.b_fx):
            top.addWidget(b)
        v.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(8)
        self.knob = VectorKnob(1, 90, thr_pct, 40, "%", "Umbral", size=30)
        self.knob.valueChanged.connect(on_thr)
        mid.addWidget(self.knob)

        fcol = QVBoxLayout()
        fcol.setSpacing(3)
        frow = QHBoxLayout()
        lbl_g = QLabel("Fader")
        lbl_g.setObjectName("fieldLabel")
        self.db_lbl = QLabel(self._fmt_db(gain_pct))
        self.db_lbl.setStyleSheet("font-family:'Consolas',monospace; font-size:7.6pt;")
        frow.addWidget(lbl_g)
        frow.addStretch()
        frow.addWidget(self.db_lbl)
        fcol.addLayout(frow)

        self.fader = ClickableSlider(Qt.Horizontal)
        self.fader.setRange(0, 200)
        self.fader.setValue(max(0, min(200, int(gain_pct))))
        self.fader.valueChanged.connect(lambda val: (self.db_lbl.setText(self._fmt_db(val)), on_gain(val)))
        fcol.addWidget(self.fader)

        self.meter = LevelMeter(height=5)
        fcol.addWidget(self.meter)
        mid.addLayout(fcol, 1)
        v.addLayout(mid)

    @staticmethod
    def _fmt_db(pct: int) -> str:
        if pct <= 0:
            return "-∞ dB"
        db = 20.0 * math.log10(max(0.01, pct / 100.0))
        return f"{db:+.1f} dB"


class AudioMixerRackPanel(QWidget):
    """Mezclador + Rack de Efectos de Voz DSP conectado en vivo al proyecto activo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._chan_cards = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self.mode_seg = SegmentedChoice(["Mezclador", "Rack Efectos DSP"])
        self.mode_seg.changed.connect(self._switch_page)
        outer.addWidget(self.mode_seg)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(0, 2, 2, 4)
        self.host_lay.setSpacing(6)
        sc.setWidget(self.host)
        outer.addWidget(sc, 1)

        self._page = 0

    def _switch_page(self, idx: int):
        self._page = idx
        self.rebuild()

    def bind_workspace(self, ws):
        self._ws = ws
        self.rebuild()

    def tick_levels(self):
        if self._ws is None or self._page != 0:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind in ("video", "tiktok"):
            for cid, card in self._chan_cards.items():
                if cid in self._ws.channels and cid in self._ws.items:
                    card.meter.set_level(self._ws.channels[cid].level, self._ws.items[cid].state)
        elif kind in ("vtuber", "vtuber3d") and "single" in self._chan_cards:
            self._chan_cards["single"].meter.set_level(self._ws.mic.level, self._ws.state.mouth_state)

    def _get_filters_list(self):
        if self._ws is None:
            return None
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind in ("video", "tiktok"):
            return self._ws.data["settings"].setdefault("mic_filters", [])
        if kind == "vtuber":
            return self._ws.rig.setdefault("audio", {}).setdefault("mic_filters", [
                M.new_filter("highpass"), M.new_filter("noise_suppress"), M.new_filter("compressor")
            ])
        if kind == "vtuber3d":
            return self._ws.data.setdefault("audio", {}).setdefault("mic_filters", [
                M.new_filter("highpass"), M.new_filter("noise_suppress"), M.new_filter("compressor")
            ])
        return None

    def _push_filters(self):
        if self._ws is None:
            return
        flts = self._get_filters_list() or []
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind in ("video", "tiktok"):
            self._ws.apply_mic_filters()
            self._ws.mark_dirty()
        elif hasattr(self._ws, "mic"):
            self._ws.mic.set_filters(flts)
            self._ws.mark_dirty()

    def rebuild(self):
        while self.host_lay.count():
            w = self.host_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chan_cards = {}

        if self._ws is None:
            lbl = QLabel("Abre o crea una escena para usar el Mezclador y Rack DSP.")
            lbl.setObjectName("mutedLabel")
            lbl.setWordWrap(True)
            self.host_lay.addWidget(lbl)
            self.host_lay.addStretch()
            return

        if self._page == 0:
            self._build_mixer_cards()
        else:
            self._build_dsp_rack()

    def _build_mixer_cards(self):
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        if kind in ("video", "tiktok"):
            chars = self._ws.data.get("characters", [])
            if not chars:
                h = QLabel("Sin canales de personaje aún.\nAñade un avatar para mezclar su micrófono y pista.")
                h.setObjectName("mutedLabel")
                h.setWordWrap(True)
                self.host_lay.addWidget(h)
            for ch in chars:
                cid = ch["id"]
                card = _MixerChannelCard(
                    ch["name"], ch.get("color", C["accent"]),
                    int(ch.get("gain", 100)), int(ch.get("threshold", 40)),
                    bool(ch.get("mute", False)), bool(ch.get("solo", False)), True,
                    on_gain=lambda val, c=ch: self._set_char_gain(c, val),
                    on_thr=lambda val, c=ch: self._set_char_thr(c, val),
                    on_mute=lambda on, c=ch: self._ws.on_track_mute(c["id"], on),
                    on_solo=lambda on, c=ch: self._ws.on_track_solo(c["id"], on),
                    on_fx=lambda _on: self.mode_seg.set_index(1) or self._switch_page(1),
                )
                self._chan_cards[cid] = card
                self.host_lay.addWidget(card)
        else:
            au = self._ws.rig["audio"] if kind == "vtuber" else self._ws.data.setdefault("audio", {})
            nm = self._ws.rig.get("name", "VTuber 2D") if kind == "vtuber" else self._ws.data.get("name", "VTuber 3D")
            card = _MixerChannelCard(
                f"Mic · {nm}", C["accent"],
                int(au.get("gain", 100)), int(au.get("threshold", 36)),
                False, False, True,
                on_gain=lambda val: (au.__setitem__("gain", val), setattr(self._ws.mic, "gain", val / 100.0)),
                on_thr=lambda val: self._ws._aset("threshold", val),
                on_mute=lambda on: setattr(self._ws.mic, "gain", 0.0 if on else au.get("gain", 100) / 100.0),
                on_solo=lambda _on: None,
                on_fx=lambda _on: self.mode_seg.set_index(1) or self._switch_page(1),
            )
            self._chan_cards["single"] = card
            self.host_lay.addWidget(card)
        self.host_lay.addStretch()

    def _set_char_gain(self, ch, val):
        ch["gain"] = int(val)
        if ch["id"] in self._ws.channels:
            self._ws.channels[ch["id"]].gain = val / 100.0
        self._ws.sync_timeline()
        self._ws.mark_dirty()

    def _set_char_thr(self, ch, val):
        ch["threshold"] = int(val)
        if ch["id"] == self._ws.sel:
            self._ws.sync_meters()
        self._ws.mark_dirty()

    def _build_dsp_rack(self):
        flts = self._get_filters_list()
        if flts is None:
            return

        # Botón superior para añadir cualquier efecto DSP
        top = QHBoxLayout()
        lbl = QLabel("CADENA DE VOZ EN VIVO")
        lbl.setObjectName("sectionLabel")
        b_add = QPushButton(" + Efecto")
        b_add.setObjectName("primary")
        b_add.setFixedHeight(24)
        b_add.clicked.connect(lambda: self._menu_add_dsp(b_add, flts))
        top.addWidget(lbl, 1)
        top.addWidget(b_add)
        self.host_lay.addLayout(top)

        # Curva de respuesta EQ en vivo
        eq_flt = next((f for f in flts if f.get("type") == "eq3" and f.get("enabled", True)), None)
        curve = _ResponseCurveWidget(54)
        self._update_eq_curve(curve, eq_flt)
        self.host_lay.addWidget(curve)

        for idx, flt in enumerate(flts):
            box = QFrame()
            box.setObjectName("dspBlock")
            bv = QVBoxLayout(box)
            bv.setContentsMargins(8, 6, 8, 6)
            bv.setSpacing(5)

            hr = QHBoxLayout()
            hr.setSpacing(5)
            b_fx = QToolButton()
            b_fx.setObjectName("mixFx")
            b_fx.setText("FX")
            b_fx.setCheckable(True)
            b_fx.setChecked(bool(flt.get("enabled", True)))
            b_fx.setFixedSize(22, 22)
            b_fx.toggled.connect(lambda on, f=flt: (f.__setitem__("enabled", on), self._push_filters(), self._update_eq_curve(curve, eq_flt)))
            nm = ElidedLabel(flt.get("name", flt.get("type", "DSP")))
            nm.setStyleSheet("font-weight:600; font-size:8.2pt;")
            b_del = QToolButton()
            b_del.setObjectName("flat")
            b_del.setFixedSize(20, 20)
            b_del.setIcon(icons.state_icon("close", 12))
            b_del.clicked.connect(lambda _, i=idx: self._remove_dsp(flts, i))
            hr.addWidget(b_fx)
            hr.addWidget(nm, 1)
            hr.addWidget(b_del)
            bv.addLayout(hr)

            p = flt.setdefault("params", {})
            ft = flt.get("type")
            knobs = QHBoxLayout()
            knobs.setSpacing(8)

            def add_k(label, key, lo, hi, dft, suf="", scale=1.0):
                cur_v = int(round(float(p.get(key, dft)) * scale))
                k = VectorKnob(lo, hi, cur_v, int(round(dft * scale)), suf, label, size=32)
                k.valueChanged.connect(
                    lambda val, kk=key, sc=scale: (
                        p.__setitem__(kk, val / sc),
                        self._push_filters(),
                        self._update_eq_curve(curve, next((x for x in flts if x.get("type") == "eq3" and x.get("enabled", True)), None)),
                    )
                )
                knobs.addWidget(k)

            if ft == "eq3":
                add_k("Low", "low_db", -12, 12, 0, "dB")
                add_k("Mid", "mid_db", -12, 12, 1, "dB")
                add_k("High", "high_db", -12, 12, 2, "dB")
            elif ft == "compressor":
                add_k("Thr", "threshold_db", -40, -4, -18, "dB")
                add_k("Ratio", "ratio", 10, 80, 3.5, "", 10.0)
                add_k("Gain", "makeup_db", -6, 18, 3, "dB")
            elif ft == "noise_gate":
                add_k("Close", "close_db", -70, -15, -46, "dB")
                add_k("Open", "open_db", -65, -10, -38, "dB")
            elif ft == "noise_suppress":
                add_k("Supr", "strength", 0, 100, 65, "%")
            elif ft == "highpass":
                add_k("Corte", "hz", 40, 260, 90, "Hz")
            elif ft == "pitch_shift":
                add_k("Tono", "semitones", -12, 12, 0, "st")
                add_k("Mix", "mix", 0, 100, 100, "%")
            elif ft == "reverb":
                add_k("Sala", "room", 10, 95, 42, "%")
                add_k("Mix", "mix", 0, 70, 22, "%")
            elif ft == "gain":
                add_k("dB", "db", -18, 24, 3, "dB")
            elif ft == "limiter":
                add_k("Techo", "ceiling_db", -12, 0, -1.5, "dB")
            knobs.addStretch()
            bv.addLayout(knobs)
            self.host_lay.addWidget(box)

        self.host_lay.addStretch()

    @staticmethod
    def _update_eq_curve(curve_w: _ResponseCurveWidget, eq_flt):
        if not eq_flt:
            curve_w.set_points([0.5] * 40)
            return
        p = eq_flt.get("params", {})
        lo = float(p.get("low_db", 0.0)) / 24.0
        md = float(p.get("mid_db", 0.0)) / 24.0
        hi = float(p.get("high_db", 0.0)) / 24.0
        pts = []
        for i in range(40):
            x = i / 39.0
            w_l = math.exp(-((x / 0.28) ** 2))
            w_h = math.exp(-(((1.0 - x) / 0.30) ** 2))
            w_m = max(0.0, 1.0 - w_l * 0.75 - w_h * 0.75)
            pts.append(0.5 + w_l * lo + w_m * md + w_h * hi)
        curve_w.set_points(pts)

    def _menu_add_dsp(self, btn, flts):
        m = QMenu(self)
        acts = {m.addAction(name): ftype for ftype, name, _ in M.FILTER_CATALOG}
        chosen = m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen in acts:
            flts.append(M.new_filter(acts[chosen]))
            self._push_filters()
            self.rebuild()

    def _remove_dsp(self, flts, idx):
        if 0 <= idx < len(flts):
            del flts[idx]
            self._push_filters()
            self.rebuild()


# =====================================================  Monitor de Tracking / Curvas / BlendShapes
class TrackingMonitorPanel(QWidget):
    """Monitor Studio de Tracking Facial / Voz / BlendShapes (curva #6366F1 + barras de 4px)."""
    calibrateRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._hist = deque([0.0] * 48, maxlen=48)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        s_curve = Section("Respuesta Lip-Sync y Tracking", True)
        self.curve = _ResponseCurveWidget(64)
        s_curve.content.addWidget(self.curve)

        b_cal = QPushButton(" Calibrar pose neutral y voz")
        b_cal.setIcon(icons.state_icon("calibrate", 14))
        b_cal.setFixedHeight(26)
        b_cal.clicked.connect(self.calibrateRequested.emit)
        s_curve.content.addWidget(b_cal)
        v.addWidget(s_curve)

        s_bs = Section("BlendShapes y Parámetros en Vivo", True)
        self.bars = {}
        for key, lbl in (
            ("mouth", "JawOpen / Lip-Sync"),
            ("yaw", "Head Yaw (X)"),
            ("pitch", "Head Pitch (Y)"),
            ("roll", "Head Roll (Z)"),
            ("blink", "EyeBlink L/R"),
            ("smile", "MouthSmile / Emoción"),
        ):
            col = QVBoxLayout()
            col.setSpacing(2)
            r = QHBoxLayout()
            nl = QLabel(lbl)
            nl.setObjectName("fieldLabel")
            vl = QLabel("0%")
            vl.setStyleSheet("font-family:'Consolas',monospace; font-size:7.5pt;")
            r.addWidget(nl)
            r.addStretch()
            r.addWidget(vl)
            bar = _ThinBar()
            col.addLayout(r)
            col.addWidget(bar)
            s_bs.content.addLayout(col)
            self.bars[key] = (bar, vl)
        v.addWidget(s_bs)
        v.addStretch()

    def bind_workspace(self, ws):
        self._ws = ws

    def tick_telemetry(self):
        if self._ws is None:
            return
        kind = getattr(self._ws, "tab_kind", lambda: "video")()
        mouth = yaw = pitch = roll = blink = smile = 0.0
        try:
            ft = getattr(self._ws, "face_tracker", None)
            if ft is not None and ft.running:
                fp = ft.get_full_pose()
                yaw = (fp.get("x", 0.0) + 1.0) * 0.5
                pitch = (fp.get("y", 0.0) + 1.0) * 0.5
                roll = (fp.get("roll", 0.0) + 1.0) * 0.5
                blink = fp.get("blink", 0.0)
                smile = fp.get("smile", 0.0)
            else:
                yaw = pitch = roll = 0.5

            if kind in ("video", "tiktok"):
                if self._ws.sel and self._ws.sel in self._ws.channels:
                    mouth = self._ws.channels[self._ws.sel].level
                    em = self._ws.channels[self._ws.sel].emotion.get_state()
                    smile = max(smile, float(em.get("confidence", 0.0)))
            elif hasattr(self._ws, "mic"):
                mouth = self._ws.mic.level
                em = self._ws.mic.emotion.get_state()
                smile = max(smile, float(em.get("confidence", 0.0)))
        except Exception:
            pass

        self._hist.append(mouth)
        if self.isVisible():
            self.curve.set_points(self._hist)
            vals = {"mouth": mouth, "yaw": yaw, "pitch": pitch, "roll": roll, "blink": blink, "smile": smile}
            for k, v in vals.items():
                bar, vl = self.bars[k]
                bar.set_value(v)
                vl.setText(f"{int(round(v * 100))}%")
