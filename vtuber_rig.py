"""Motor completo de creación y rigging 2.5D para avatares VTuber 2D (.vtr):
- Jerarquía padre-hijo con puntos de pivote editables.
- Profundidad 2.5D (paralaje cabeza X/Y/Z estilo Live2D).
- Seguimiento real de mirada en pupilas (cursor / webcam) con límites en el ojo.
- Física independiente por capa (péndulo de pelo, resorte, viento, respiración, aleteo, flotación).
- Visemas vocales (4 niveles), parpadeo natural de 3 fases, expresiones con overrides y teclas toggle (1..9).
- Generador de rig base vectorial cel-shaded editable + auto-rig desde lote de PNGs + exportación de 12 sprites PNG."""
import copy
import json
import math
import random
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPolygonF, QRadialGradient, QTransform)

import model as M
from voice_emotion import VoiceEmotionAnalyzer
from vtuber_engine import SHARED_ASSETS

RIG_W, RIG_H = 1080, 1080

LAYER_ROLES = [
    ("always", "Siempre visible (Base / Pelo / Ropa)"),
    ("eye_open", "Ojos abiertos"),
    ("eye_half", "Ojos semicerrados (transición parpadeo)"),
    ("eye_closed", "Ojos cerrados (parpadeo)"),
    ("pupil", "Pupilas (siguen mirada / cursor)"),
    ("mouth_closed", "Boca cerrada (silencio)"),
    ("mouth_mid", "Boca media (voz suave)"),
    ("mouth_open", "Boca abierta (voz normal)"),
    ("mouth_wide", "Boca gritando (voz fuerte)"),
    ("talk_only", "Solo visible al hablar"),
    ("idle_only", "Solo visible en silencio"),
    ("toggle", "Accesorio / Extra (tecla rápida 1..9)"),
]

PHYSICS_MODES = [
    ("pendulum", "Péndulo (cabello / accesorios colgantes)"),
    ("spring", "Resorte elástico (orejas / cabeza / rebote)"),
    ("breathe", "Respiración rítmica (torso / hombros)"),
    ("wind", "Brisa / Viento suave (cintas / flequillo)"),
    ("flap", "Aleteo / Ondulación (alas / cola)"),
    ("float", "Flotación continua (halo / mascota)"),
]

BLEND_MODES = [
    ("normal", "Normal", QPainter.CompositionMode_SourceOver),
    ("multiply", "Multiplicar (Sombras)", QPainter.CompositionMode_Multiply),
    ("screen", "Pantalla (Brillo)", QPainter.CompositionMode_Screen),
    ("plus", "Añadir (Resplandor)", QPainter.CompositionMode_Plus),
]
_BLEND_MAP = {k: m for k, _, m in BLEND_MODES}


def new_layer(name="Capa", path="", parent_id=None, z=10, role="always", x=0.0, y=0.0,
              w=360.0, h=360.0, pivot_x=0.5, pivot_y=0.5, depth_z=0.0):
    return {
        "id": "lyr_" + uuid.uuid4().hex[:6],
        "name": name,
        "parent_id": parent_id,
        "path": path,
        "visible": True,
        "locked": False,
        "z": int(z),
        "x": float(x),
        "y": float(y),
        "w": float(w),
        "h": float(h),
        "pivot_x": float(pivot_x),
        "pivot_y": float(pivot_y),
        "scale_x": 1.0,
        "scale_y": 1.0,
        "rotation": 0.0,
        "opacity": 100,
        "flip_x": False,
        "blend_mode": "normal",
        "role": role,
        "hotkey": "",
        "depth_z": float(depth_z),      # -100..+100 para paralaje 2.5D
        "gaze_range": 14.0,             # px para role == "pupil"
        "physics": {
            "enabled": False,
            "mode": "pendulum",
            "stiffness": 18,
            "damping": 65,
            "inertia_x": 35,
            "inertia_y": 25,
            "swing_deg": 12,
            "wind_amt": 20,
            "breathe_amt": 15,
            "phase": 0.0,
        },
        "color": {
            "tint_hex": "",
            "tint_amt": 0,              # 0..100
            "hue": 0,                   # -180..180
            "sat": 0,                   # -100..100
            "bright": 0,                # -50..50
        },
    }


def new_rig_expression(name="Normal", overrides=None):
    return {
        "id": "exp_" + uuid.uuid4().hex[:5],
        "name": name,
        "overrides": copy.deepcopy(overrides or {}),  # layer_id -> {visible?, rot?, dy?, opacity?}
    }


def new_empty_rig(name="Mi VTuber 2D"):
    now = time.strftime("%Y-%m-%d %H:%M")
    return {
        "format": "vtr",
        "version": 1,
        "name": name,
        "canvas_w": RIG_W,
        "canvas_h": RIG_H,
        "bg_type": "transparent",   # "transparent" | "dark" | "chroma"
        "bg_color": "#121215",
        "layers": [],
        "expressions": [new_rig_expression("Normal")],
        "expr": 0,
        "toggles": {},              # hotkey str -> bool
        "physics_global": {
            "head_x_px": 42.0,
            "head_y_px": 28.0,
            "head_rot_deg": 11.0,
            "wind_strength": 25,
            "breathe_speed": 10,    # /10 Hz
            "bounce_talk": 18,
            "blink_auto": True,
            "blink_every": 4,
            "blink_ms": 190,
            "mouse_track": True,
            "face_track": False,
        },
        "audio": {
            "device": None,
            "gain": 100,
            "threshold": 36,
            "range": 12,
            "hold": 85,
        },
        "tracking": {
            "mode": "voice",          # "voice" (Solo Voz) | "cam_voice" (Cámara + Voz) | "camera" (Solo Cámara)
            "auto_emotion": True,
            "emotion_sensitivity": 68,
            "auto_reactions": True,
            "manual_emotion": "auto", # "auto" | neutral | happy | laugh | shout | sad | angry | surprised
            "camera_index": 0,
        },
        "created_at": now,
        "modified_at": now,
    }


def load_rig(path):
    p = Path(path)
    data = new_empty_rig(p.stem)
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            data.update(loaded)
        except Exception:
            pass
    data["format"] = "vtr"
    data["version"] = 1
    # Normalizar campos faltantes en capas
    norm_layers = []
    for lyr in data.get("layers", []):
        base = new_layer(lyr.get("name", "Capa"))
        for k, v in base.items():
            if isinstance(v, dict):
                sub = copy.deepcopy(v)
                sub.update(lyr.get(k, {}) if isinstance(lyr.get(k), dict) else {})
                lyr[k] = sub
            else:
                lyr.setdefault(k, copy.deepcopy(v))
        norm_layers.append(lyr)
    data["layers"] = norm_layers
    if not data.get("expressions"):
        data["expressions"] = [new_rig_expression("Normal")]
    data["expr"] = max(0, min(len(data["expressions"]) - 1, int(data.get("expr", 0))))
    pg = new_empty_rig()["physics_global"]
    pg.update(data.get("physics_global", {}))
    data["physics_global"] = pg
    au = new_empty_rig()["audio"]
    au.update(data.get("audio", {}))
    data["audio"] = au
    tr = new_empty_rig()["tracking"]
    tr.update(data.get("tracking", {}))
    data["tracking"] = tr
    return data


def save_rig(data, path):
    p = Path(path)
    if p.suffix.lower() != ".vtr":
        p = p.with_suffix(".vtr")
    p.parent.mkdir(parents=True, exist_ok=True)
    data["format"] = "vtr"
    data["version"] = 1
    data["modified_at"] = time.strftime("%Y-%m-%d %H:%M")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    M.record_recent_project(p, data)
    return p


# =====================================================  Generador vectorial de piezas base cel-shaded
_BUILTIN_CACHE = {}
_COLOR_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _make_canvas(w, h):
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    return img, p


def get_builtin_part(part_key):
    """Genera piezas limpias cel-shaded de alta resolución para el Rig Base Editable."""
    with _CACHE_LOCK:
        if part_key in _BUILTIN_CACHE:
            return _BUILTIN_CACHE[part_key]

    ink = QColor("#221E29")
    skin = QColor("#FFE4D1")
    skin_sh = QColor("#F3C3A8")
    hair = QColor("#3B4769")
    hair_hi = QColor("#576A9C")
    hair_sh = QColor("#283049")
    outfit = QColor("#242730")
    accent = QColor("#2F7CF6")

    if part_key == "hair_back":
        img, p = _make_canvas(520, 620)
        path = QPainterPath()
        path.moveTo(110, 120)
        path.cubicTo(50, 260, 42, 460, 76, 585)
        path.cubicTo(145, 555, 190, 595, 260, 590)
        path.cubicTo(330, 595, 375, 555, 444, 585)
        path.cubicTo(478, 460, 470, 260, 410, 120)
        path.cubicTo(350, 36, 170, 36, 110, 120)
        g = QLinearGradient(0, 50, 0, 590)
        g.setColorAt(0.0, hair)
        g.setColorAt(1.0, hair_sh)
        p.setBrush(g)
        p.setPen(QPen(ink, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.end()

    elif part_key == "body":
        img, p = _make_canvas(520, 420)
        # Sudadera / chaqueta moderna minimalista
        torso = QPainterPath()
        torso.moveTo(130, 120)
        torso.cubicTo(78, 155, 52, 260, 46, 415)
        torso.lineTo(474, 415)
        torso.cubicTo(468, 260, 442, 155, 390, 120)
        torso.cubicTo(340, 88, 180, 88, 130, 120)
        g = QLinearGradient(0, 90, 0, 415)
        g.setColorAt(0.0, QColor("#2F3340"))
        g.setColorAt(1.0, outfit)
        p.setBrush(g)
        p.setPen(QPen(ink, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(torso)
        # Detalle solapa / camiseta interior + cuello
        p.setBrush(QColor("#E8EAEF"))
        p.drawPolygon(QPolygonF([QPointF(195, 108), QPointF(260, 220), QPointF(325, 108)]))
        # Línea de acento central
        p.setPen(QPen(accent, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(260, 222), QPointF(260, 405))
        # Cuello
        p.setPen(QPen(ink, 6.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(skin)
        p.drawRoundedRect(QRectF(218, 22, 84, 110), 28, 28)
        p.setPen(Qt.NoPen)
        p.setBrush(skin_sh)
        p.drawEllipse(QRectF(224, 52, 72, 46))
        p.end()

    elif part_key == "head":
        img, p = _make_canvas(440, 440)
        # Orejas
        p.setPen(QPen(ink, 6.5))
        p.setBrush(skin)
        p.drawEllipse(QRectF(42, 200, 56, 78))
        p.drawEllipse(QRectF(342, 200, 56, 78))
        # Rostro anime limpio
        face = QPainterPath()
        face.moveTo(76, 175)
        face.cubicTo(74, 65, 366, 65, 364, 175)
        face.cubicTo(366, 290, 326, 376, 220, 396)
        face.cubicTo(114, 376, 74, 290, 76, 175)
        g = QLinearGradient(0, 80, 0, 396)
        g.setColorAt(0.0, QColor("#FFECE0"))
        g.setColorAt(1.0, skin)
        p.setBrush(g)
        p.setPen(QPen(ink, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(face)
        # Pequeña sombra sutil bajo nariz
        p.setPen(Qt.NoPen)
        p.setBrush(skin_sh)
        p.drawEllipse(QPointF(220, 282), 5, 3.5)
        p.end()

    elif part_key == "ears_cat":
        img, p = _make_canvas(420, 220)
        for sign, cx in ((-1, 105), (1, 315)):
            ear = QPainterPath()
            ear.moveTo(cx - sign * 62, 195)
            ear.quadTo(cx + sign * 8, 25, cx + sign * 58, 175)
            ear.closeSubpath()
            p.setBrush(hair)
            p.setPen(QPen(ink, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(ear)
            inner = QPainterPath()
            inner.moveTo(cx - sign * 36, 178)
            inner.quadTo(cx + sign * 6, 78, cx + sign * 34, 166)
            inner.closeSubpath()
            p.setBrush(QColor("#F4B6C2"))
            p.setPen(Qt.NoPen)
            p.drawPath(inner)
        p.end()

    elif part_key == "eyes_open":
        img, p = _make_canvas(320, 120)
        for cx in (82, 238):
            # Esclerótica blanca
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(cx, 66), 44, 36)
            # Pestaña superior gruesa limpia
            lash = QPainterPath()
            lash.moveTo(cx - 48, 56)
            lash.cubicTo(cx - 26, 22, cx + 26, 22, cx + 48, 56)
            p.setPen(QPen(ink, 10, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawPath(lash)
            # Línea inferior suave
            p.setPen(QPen(ink, 3.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - 34, 62, 68, 38), 210 * 16, 120 * 16)
        p.end()

    elif part_key == "pupils":
        img, p = _make_canvas(320, 120)
        for cx in (82, 238):
            rg = QRadialGradient(cx, 66, 28)
            rg.setColorAt(0.0, QColor("#183266"))
            rg.setColorAt(0.65, accent)
            rg.setColorAt(1.0, QColor("#60A5FA"))
            p.setPen(QPen(ink, 3.5))
            p.setBrush(rg)
            p.drawEllipse(QPointF(cx, 66), 24, 27)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#11131C"))
            p.drawEllipse(QPointF(cx, 67), 10, 12)
            # Brillos especulares
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(cx - 9, 55), 6.5, 6.5)
            p.drawEllipse(QPointF(cx + 8, 76), 3.2, 3.2)
        p.end()

    elif part_key == "eyes_half":
        img, p = _make_canvas(320, 120)
        for cx in (82, 238):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(cx - 42, 58, 84, 24), 12, 12)
            p.setBrush(accent)
            p.drawEllipse(QPointF(cx, 70), 19, 12)
            p.setBrush(QColor("#11131C"))
            p.drawEllipse(QPointF(cx, 70), 8, 6)
            p.setBrush(skin)
            p.drawRect(QRectF(cx - 48, 30, 96, 30))
            p.setPen(QPen(ink, 9, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 46, 60), QPointF(cx + 46, 60))
        p.end()

    elif part_key == "eyes_closed":
        img, p = _make_canvas(320, 120)
        p.setPen(QPen(ink, 8.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for cx in (82, 238):
            arc = QPainterPath()
            arc.moveTo(cx - 42, 66)
            arc.quadTo(cx, 82, cx + 42, 66)
            p.drawPath(arc)
        p.end()

    elif part_key == "brows":
        img, p = _make_canvas(320, 80)
        p.setPen(QPen(hair_sh, 7.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for sign, cx in ((-1, 82), (1, 238)):
            br = QPainterPath()
            br.moveTo(cx - 34, 46)
            br.quadTo(cx, 28, cx + 34, 42)
            p.drawPath(br)
        p.end()

    elif part_key == "blush":
        img, p = _make_canvas(320, 90)
        for cx in (62, 258):
            rg = QRadialGradient(cx, 45, 42)
            rg.setColorAt(0.0, QColor(244, 114, 152, 165))
            rg.setColorAt(1.0, QColor(244, 114, 152, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(cx, 45), 42, 24)
        p.end()

    elif part_key == "mouth_closed":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        m = QPainterPath()
        m.moveTo(56, 56)
        m.quadTo(90, 74, 124, 56)
        p.drawPath(m)
        p.end()

    elif part_key == "mouth_mid":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 5.5))
        p.setBrush(QColor("#8C2D41"))
        p.drawRoundedRect(QRectF(62, 46, 56, 26), 13, 13)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E36A80"))
        p.drawEllipse(QPointF(90, 68), 18, 7)
        p.end()

    elif part_key == "mouth_open":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 5.5))
        p.setBrush(QColor("#7A2237"))
        p.drawRoundedRect(QRectF(56, 38, 68, 44), 20, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(66, 41, 48, 10), 4, 4)
        p.setBrush(QColor("#E8758A"))
        p.drawEllipse(QPointF(90, 74), 24, 11)
        p.end()

    elif part_key == "mouth_wide":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 5.5))
        p.setBrush(QColor("#6E1B30"))
        p.drawRoundedRect(QRectF(48, 28, 84, 64), 26, 28)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(58, 31, 64, 12), 5, 5)
        p.setBrush(QColor("#EE7B90"))
        p.drawEllipse(QPointF(90, 80), 30, 14)
        p.end()

    elif part_key == "hair_front":
        img, p = _make_canvas(480, 420)
        bangs = QPainterPath()
        bangs.moveTo(56, 210)
        bangs.cubicTo(48, 52, 140, 16, 240, 18)
        bangs.cubicTo(340, 16, 432, 52, 424, 210)
        # Mechón lateral derecho
        bangs.cubicTo(418, 295, 396, 365, 376, 388)
        bangs.cubicTo(366, 305, 366, 232, 354, 174)
        # Flequillos frontales en picos limpios
        bangs.lineTo(318, 224)
        bangs.lineTo(290, 160)
        bangs.lineTo(246, 238)
        bangs.lineTo(212, 162)
        bangs.lineTo(164, 226)
        bangs.lineTo(126, 174)
        # Mechón lateral izquierdo
        bangs.cubicTo(114, 232, 114, 305, 104, 388)
        bangs.cubicTo(84, 365, 62, 295, 56, 210)
        g = QLinearGradient(0, 20, 0, 380)
        g.setColorAt(0.0, hair_hi)
        g.setColorAt(0.55, hair)
        g.setColorAt(1.0, hair_sh)
        p.setBrush(g)
        p.setPen(QPen(ink, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(bangs)
        # Reflejo anime limpio en arco
        p.setPen(QPen(QColor(255, 255, 255, 72), 8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(126, 72, 228, 70), 20 * 16, 140 * 16)
        p.end()

    elif part_key == "glasses":
        img, p = _make_canvas(340, 120)
        p.setPen(QPen(QColor("#1E1E24"), 7))
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(26, 24, 116, 72), 16, 16)
        p.drawRoundedRect(QRectF(198, 24, 116, 72), 16, 16)
        p.drawLine(QPointF(142, 54), QPointF(198, 54))
        p.setPen(QPen(QColor(255, 255, 255, 110), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(44, 42), QPointF(68, 36))
        p.drawLine(QPointF(216, 42), QPointF(240, 36))
        p.end()

    elif part_key == "halo":
        img, p = _make_canvas(300, 120)
        p.setPen(QPen(QColor("#F5B942"), 12))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(24, 24, 252, 68))
        p.setPen(QPen(QColor("#FFF6D6"), 4))
        p.drawEllipse(QRectF(24, 24, 252, 68))
        p.end()

    # ---- Variantes emocionales automáticas (Feliz / Riendo / Triste / Enojado / Sorprendido) ----
    elif part_key == "eyes_laugh":
        img, p = _make_canvas(320, 120)
        p.setPen(QPen(ink, 9.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for cx in (82, 238):
            arc = QPainterPath()
            arc.moveTo(cx - 42, 74)
            arc.quadTo(cx, 26, cx + 42, 74)
            p.drawPath(arc)
        p.end()

    elif part_key == "eyes_sad":
        img, p = _make_canvas(320, 120)
        for sign, cx in ((-1, 82), (1, 238)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(cx, 70), 42, 30)
            # Párpado inclinado hacia abajo afuera
            lash = QPainterPath()
            lash.moveTo(cx - sign * 44, 54)
            lash.quadTo(cx, 44, cx + sign * 46, 68)
            p.setPen(QPen(ink, 9, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawPath(lash)
            # Brillo húmedo de lágrima en borde inferior
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(96, 185, 250, 195))
            p.drawEllipse(QPointF(cx + sign * 24, 92), 11, 6.5)
        p.end()

    elif part_key == "eyes_angry":
        img, p = _make_canvas(320, 120)
        for sign, cx in ((-1, 82), (1, 238)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(cx, 68), 42, 28)
            # Párpado superior recto y angulado hacia el centro
            p.setBrush(skin)
            poly = QPolygonF([
                QPointF(cx - 50, 26), QPointF(cx + 50, 26),
                QPointF(cx + 50, 62 if sign < 0 else 44),
                QPointF(cx - 50, 44 if sign < 0 else 62),
            ])
            p.drawPolygon(poly)
            p.setPen(QPen(ink, 10, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 46, 46 if sign < 0 else 62), QPointF(cx + 46, 62 if sign < 0 else 46))
        p.end()

    elif part_key == "eyes_surprised":
        img, p = _make_canvas(320, 120)
        for cx in (82, 238):
            p.setPen(QPen(ink, 7))
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(cx, 64), 46, 44)
        p.end()

    elif part_key == "brows_happy":
        img, p = _make_canvas(320, 80)
        p.setPen(QPen(hair_sh, 7.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for cx in (82, 238):
            br = QPainterPath()
            br.moveTo(cx - 34, 38)
            br.quadTo(cx, 16, cx + 34, 36)
            p.drawPath(br)
        p.end()

    elif part_key == "brows_sad":
        img, p = _make_canvas(320, 80)
        p.setPen(QPen(hair_sh, 7.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for sign, cx in ((-1, 82), (1, 238)):
            br = QPainterPath()
            br.moveTo(cx - sign * 32, 32)
            br.quadTo(cx, 44, cx + sign * 36, 56)
            p.drawPath(br)
        p.end()

    elif part_key == "brows_angry":
        img, p = _make_canvas(320, 80)
        p.setPen(QPen(hair_sh, 8.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for sign, cx in ((-1, 82), (1, 238)):
            p.drawLine(QPointF(cx - sign * 32, 58), QPointF(cx + sign * 36, 30))
        p.end()

    elif part_key == "brows_surprised":
        img, p = _make_canvas(320, 80)
        p.setPen(QPen(hair_sh, 7.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        for cx in (82, 238):
            br = QPainterPath()
            br.moveTo(cx - 32, 34)
            br.quadTo(cx, 8, cx + 32, 34)
            p.drawPath(br)
        p.end()

    elif part_key == "mouth_happy":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 6.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        m = QPainterPath()
        m.moveTo(46, 50)
        m.quadTo(90, 88, 134, 50)
        p.drawPath(m)
        p.end()

    elif part_key == "mouth_laugh":
        img, p = _make_canvas(180, 120)
        m = QPainterPath()
        m.moveTo(42, 42)
        m.quadTo(90, 34, 138, 42)
        m.quadTo(132, 98, 90, 98)
        m.quadTo(48, 98, 42, 42)
        p.setPen(QPen(ink, 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor("#822238"))
        p.drawPath(m)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(52, 42, 76, 14), 5, 5)
        p.setBrush(QColor("#F07C94"))
        p.drawEllipse(QPointF(90, 82), 28, 13)
        p.end()

    elif part_key == "mouth_sad":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        m = QPainterPath()
        m.moveTo(58, 72)
        m.quadTo(90, 50, 122, 72)
        p.drawPath(m)
        p.end()

    elif part_key == "mouth_angry":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 6.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(58, 48, 64, 22), 6, 6)
        p.drawLine(QPointF(58, 59), QPointF(122, 59))
        p.end()

    elif part_key == "mouth_surprised":
        img, p = _make_canvas(180, 120)
        p.setPen(QPen(ink, 5.8))
        p.setBrush(QColor("#7A2237"))
        p.drawEllipse(QPointF(90, 62), 20, 26)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E8758A"))
        p.drawEllipse(QPointF(90, 74), 11, 7)
        p.end()

    else:
        img, p = _make_canvas(240, 240)
        p.setPen(QPen(QColor("#52525B"), 4, Qt.DashLine))
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(QRectF(10, 10, 220, 220), 16, 16)
        p.end()

    with _CACHE_LOCK:
        _BUILTIN_CACHE[part_key] = img
    return img


def build_starter_vtuber_rig(name="Mi VTuber 2D"):
    """Construye un rig 2.5D completo de 15 capas padre-hijo listo para personalizar o recolorear."""
    rig = new_empty_rig(name)
    cx = RIG_W * 0.5

    # 1. Raíz: Cuerpo / Torso (pivote en base)
    body = new_layer("Cuerpo / Torso", "builtin:body", parent_id=None, z=20,
                     x=cx, y=970, w=520, h=420, pivot_x=0.5, pivot_y=0.92, depth_z=-12)
    body["physics"].update({"enabled": True, "mode": "breathe", "breathe_amt": 22})

    # 2. Cabeza (hija de Cuerpo, pivote en el cuello!)
    head = new_layer("Cabeza", "builtin:head", parent_id=body["id"], z=30,
                     x=0, y=-390, w=440, h=440, pivot_x=0.5, pivot_y=0.84, depth_z=12)
    head["physics"].update({"enabled": True, "mode": "spring", "stiffness": 20, "damping": 70, "swing_deg": 7})

    # 3. Pelo trasero (hijo de Cabeza, detrás del cuerpo, péndulo real!)
    h_back = new_layer("Pelo Trasero", "builtin:hair_back", parent_id=head["id"], z=10,
                       x=0, y=-150, w=520, h=620, pivot_x=0.5, pivot_y=0.20, depth_z=-45)
    h_back["physics"].update({"enabled": True, "mode": "pendulum", "stiffness": 14, "damping": 55,
                              "inertia_x": 45, "swing_deg": 15, "wind_amt": 35, "phase": 0.6})

    # 4. Orejas decorativas (hijas de Cabeza, resorte)
    ears = new_layer("Orejas", "builtin:ears_cat", parent_id=head["id"], z=25,
                     x=0, y=-270, w=420, h=220, pivot_x=0.5, pivot_y=0.88, depth_z=-8)
    ears["visible"] = False
    ears["role"] = "toggle"
    ears["hotkey"] = "3"
    ears["physics"].update({"enabled": True, "mode": "spring", "stiffness": 24, "damping": 50, "swing_deg": 14})

    # 5. Ojos abiertos / Pupilas con tracking / Ojos medios / Ojos cerrados
    e_open = new_layer("Ojos (Abiertos)", "builtin:eyes_open", parent_id=head["id"], z=40, role="eye_open",
                       x=0, y=-148, w=320, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=24)
    pupils = new_layer("Pupilas (Mirada)", "builtin:pupils", parent_id=e_open["id"], z=42, role="pupil",
                       x=0, y=0, w=320, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=30)
    pupils["gaze_range"] = 14.0
    e_half = new_layer("Ojos (Semicerrados)", "builtin:eyes_half", parent_id=head["id"], z=43, role="eye_half",
                       x=0, y=-148, w=320, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=24)
    e_closed = new_layer("Ojos (Cerrados)", "builtin:eyes_closed", parent_id=head["id"], z=44, role="eye_closed",
                         x=0, y=-148, w=320, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=24)

    # 6. Cejas y Rubor
    brows = new_layer("Cejas", "builtin:brows", parent_id=head["id"], z=48, role="always",
                      x=0, y=-202, w=320, h=80, pivot_x=0.5, pivot_y=0.5, depth_z=28)
    blush = new_layer("Rubor", "builtin:blush", parent_id=head["id"], z=38, role="toggle",
                      x=0, y=-100, w=320, h=90, pivot_x=0.5, pivot_y=0.5, depth_z=20)
    blush["hotkey"] = "2"

    # 7. Bocas (4 niveles de lip-sync)
    m_c = new_layer("Boca (Cerrada)", "builtin:mouth_closed", parent_id=head["id"], z=45, role="mouth_closed",
                    x=0, y=-60, w=180, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=28)
    m_m = new_layer("Boca (Media)", "builtin:mouth_mid", parent_id=head["id"], z=45, role="mouth_mid",
                    x=0, y=-60, w=180, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=28)
    m_o = new_layer("Boca (Abierta)", "builtin:mouth_open", parent_id=head["id"], z=45, role="mouth_open",
                    x=0, y=-60, w=180, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=28)
    m_w = new_layer("Boca (Gritando)", "builtin:mouth_wide", parent_id=head["id"], z=45, role="mouth_wide",
                    x=0, y=-60, w=180, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=28)

    # 8. Flequillo / Pelo frontal (péndulo + viento!)
    h_front = new_layer("Flequillo / Pelo Frontal", "builtin:hair_front", parent_id=head["id"], z=55,
                        x=0, y=-280, w=480, h=420, pivot_x=0.5, pivot_y=0.14, depth_z=46)
    h_front["physics"].update({"enabled": True, "mode": "pendulum", "stiffness": 18, "damping": 62,
                               "inertia_x": 38, "swing_deg": 11, "wind_amt": 28, "phase": 1.2})

    # 9. Accesorios toggleables: Gafas (Tecla 1) y Halo flotante (Tecla 4)
    glasses = new_layer("Gafas", "builtin:glasses", parent_id=head["id"], z=60, role="toggle",
                        x=0, y=-146, w=340, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=54)
    glasses["visible"] = False
    glasses["hotkey"] = "1"

    halo = new_layer("Halo Flotante", "builtin:halo", parent_id=head["id"], z=65, role="toggle",
                     x=0, y=-385, w=300, h=120, pivot_x=0.5, pivot_y=0.5, depth_z=20)
    halo["visible"] = False
    halo["hotkey"] = "4"
    halo["physics"].update({"enabled": True, "mode": "float", "wind_amt": 35, "swing_deg": 6})

    rig["layers"] = [body, h_back, head, ears, blush, e_open, pupils, e_half, e_closed,
                     m_c, m_m, m_o, m_w, brows, h_front, glasses, halo]
    rig["expressions"] = [
        new_rig_expression("Normal"),
        new_rig_expression("Feliz", {
            blush["id"]: {"visible": True, "opacity": 100},
            brows["id"]: {"dy": -12.0},
            e_open["id"]: {"visible": False},
            pupils["id"]: {"visible": False},
            e_closed["id"]: {"visible": True},
        }),
        new_rig_expression("Sorprendido", {
            brows["id"]: {"dy": -22.0},
            pupils["id"]: {"scale": 0.82},
        }),
        new_rig_expression("Enojado", {
            brows["id"]: {"dy": 14.0, "rot": 4.0},
            blush["id"]: {"visible": False},
        }),
    ]
    return rig


def auto_rig_from_files(rig, paths):
    """Importa un lote de PNG/GIF, clasifica roles, jerarquía y profundidad 2.5D automáticamente."""
    cx, cy = rig.get("canvas_w", RIG_W) * 0.5, rig.get("canvas_h", RIG_H) * 0.55
    created = []
    body_id = next((l["id"] for l in rig["layers"] if "cuerpo" in l["name"].lower() or "body" in l["name"].lower()), None)
    head_id = next((l["id"] for l in rig["layers"] if "cabeza" in l["name"].lower() or "head" in l["name"].lower()), None)

    def _classify(stem):
        s = stem.lower().replace("_", " ").replace("-", " ")
        if any(w in s for w in ("pelo tras", "back hair", "cabello tras", "hair back")):
            return ("always", 10, -45, True, "pendulum", "head")
        if any(w in s for w in ("cuerpo", "body", "torso", "ropa", "outfit")):
            return ("always", 20, -10, True, "breathe", None)
        if any(w in s for w in ("cabeza", "head", "cara", "face", "rostro")):
            return ("always", 30, 12, True, "spring", "body")
        if any(w in s for w in ("pupila", "pupil", "iris")):
            return ("pupil", 42, 30, False, "spring", "head")
        if any(w in s for w in ("ojo cerr", "blink", "eye clos")):
            return ("eye_closed", 44, 24, False, "spring", "head")
        if any(w in s for w in ("ojo med", "half eye", "semi")):
            return ("eye_half", 43, 24, False, "spring", "head")
        if any(w in s for w in ("ojo", "eye")):
            return ("eye_open", 40, 24, False, "spring", "head")
        if any(w in s for w in ("boca grit", "wide", "shout")):
            return ("mouth_wide", 45, 28, False, "spring", "head")
        if any(w in s for w in ("boca abi", "mouth open")):
            return ("mouth_open", 45, 28, False, "spring", "head")
        if any(w in s for w in ("boca med", "mouth mid")):
            return ("mouth_mid", 45, 28, False, "spring", "head")
        if any(w in s for w in ("boca", "mouth")):
            return ("mouth_closed", 45, 28, False, "spring", "head")
        if any(w in s for w in ("flequillo", "bang", "pelo", "hair", "cabello")):
            return ("always", 55, 45, True, "pendulum", "head")
        if any(w in s for w in ("oreja", "ear", "ala", "wing", "cola", "tail")):
            return ("always", 25, -15, True, "spring", "head")
        if any(w in s for w in ("gafa", "lente", "glass", "sombrero", "hat", "rubor", "blush")):
            return ("toggle", 60, 50, False, "spring", "head")
        return ("always", 35, 10, False, "spring", "head")

    for p in paths:
        rel = M.import_asset(p)
        info = SHARED_ASSETS.load_image_info(M.resolve(rel))
        iw, ih = (info[2][0], info[2][1]) if info else (400, 400)
        fit = min(680.0 / max(1, iw), 680.0 / max(1, ih), 1.0)
        w, h = max(80.0, iw * fit), max(80.0, ih * fit)
        stem = Path(p).stem
        role, z, dz, phys_on, pmode, pref_parent = _classify(stem)
        lyr = new_layer(stem[:26], rel, parent_id=None, z=z, role=role, x=cx, y=cy, w=w, h=h, depth_z=dz)
        lyr["physics"]["enabled"] = phys_on
        lyr["physics"]["mode"] = pmode
        if role == "always" and pref_parent is None and body_id is None:
            body_id = lyr["id"]
        if role == "always" and pref_parent == "body" and head_id is None:
            head_id = lyr["id"]
        created.append((lyr, pref_parent))

    # Asignar jerarquía padre-hijo preservando posición visual
    for lyr, pref in created:
        if pref == "body" and body_id and lyr["id"] != body_id:
            lyr["parent_id"] = body_id
            lyr["x"], lyr["y"] = 0.0, 0.0
        elif pref == "head" and head_id and lyr["id"] != head_id:
            lyr["parent_id"] = head_id
            lyr["x"], lyr["y"] = 0.0, 0.0
        rig["layers"].append(lyr)
    return [l for l, _ in created]


# =====================================================  Paletas rápidas para personalización intuitiva
RIG_PALETTES = {
    "studio_blue":   {"label": "Azul Studio",  "hair": "#3B4A6B", "eyes": "#2F7CF6", "outfit": "#242730", "skin": "#FFE2D2"},
    "crimson_night": {"label": "Carmesí",      "hair": "#5C2434", "eyes": "#E5484D", "outfit": "#1E1F28", "skin": "#FFE4D6"},
    "mint_pastel":   {"label": "Menta Suave",  "hair": "#2E6F65", "eyes": "#2EB67D", "outfit": "#273038", "skin": "#FFECE0"},
    "golden_warm":   {"label": "Ámbar Cálido", "hair": "#8B5E34", "eyes": "#E5A00D", "outfit": "#2A2422", "skin": "#FAD6BF"},
    "silver_cyber":  {"label": "Plata Minimal","hair": "#9BA3B4", "eyes": "#38BDF8", "outfit": "#1A1D24", "skin": "#FFE6DA"},
}


def set_rig_group_color(rig, group, hex_col, amount=82):
    """Recolorea rápidamente grupos lógicos del rig (hair, eyes, outfit, skin) en el modo Fácil."""
    group_parts = {
        "hair": ("builtin:hair_back", "builtin:hair_front", "builtin:ears_cat", "builtin:brows"),
        "eyes": ("builtin:pupils",),
        "outfit": ("builtin:body",),
        "skin": ("builtin:head",),
    }
    targets = group_parts.get(group, ())
    for lyr in rig.get("layers", []):
        if lyr.get("path") in targets:
            col = lyr.setdefault("color", {"tint_hex": "", "tint_amt": 0, "hue": 0, "sat": 0, "bright": 0})
            col["tint_hex"] = hex_col
            col["tint_amt"] = int(amount)


def apply_rig_palette(rig, preset_id):
    pal = RIG_PALETTES.get(preset_id)
    if not pal:
        return
    if preset_id == "studio_blue":
        for lyr in rig.get("layers", []):
            lyr["color"] = {"tint_hex": "", "tint_amt": 0, "hue": 0, "sat": 0, "bright": 0}
        return
    set_rig_group_color(rig, "hair", pal["hair"], 86)
    set_rig_group_color(rig, "eyes", pal["eyes"], 88)
    set_rig_group_color(rig, "outfit", pal["outfit"], 78)
    set_rig_group_color(rig, "skin", pal["skin"], 38)


# =====================================================  Colorización rápida cacheada por capa
def _emotion_part_override(part_key, emotion):
    if not emotion or emotion == "neutral":
        return part_key
    if part_key == "eyes_open":
        return {"laugh": "eyes_laugh", "sad": "eyes_sad", "angry": "eyes_angry", "surprised": "eyes_surprised"}.get(emotion, part_key)
    if part_key == "brows":
        return {"happy": "brows_happy", "laugh": "brows_happy", "sad": "brows_sad",
                "angry": "brows_angry", "shout": "brows_angry", "surprised": "brows_surprised"}.get(emotion, part_key)
    if part_key == "mouth_closed":
        return {"happy": "mouth_happy", "laugh": "mouth_happy", "sad": "mouth_sad",
                "angry": "mouth_angry", "surprised": "mouth_surprised"}.get(emotion, part_key)
    if part_key in ("mouth_mid", "mouth_open") and emotion == "laugh":
        return "mouth_laugh"
    if part_key == "mouth_mid" and emotion == "surprised":
        return "mouth_surprised"
    return part_key


def _load_layer_image(lyr, now, emotion="neutral"):
    path = lyr.get("path", "")
    if not path:
        return None
    if path.startswith("builtin:"):
        pk = _emotion_part_override(path.split(":", 1)[1], emotion)
        base = get_builtin_part(pk)
        tag = f"builtin:{pk}"
    else:
        info = SHARED_ASSETS.load_image_info(M.resolve(path), max_dim=900)
        if info is None:
            return None
        base = SHARED_ASSETS.frame_at(info, now)
        tag = (path, len(info[0]), int(now * 24) if len(info[0]) > 1 else 0)

    col = lyr.get("color", {})
    tint_hex = col.get("tint_hex", "")
    tint_amt = int(col.get("tint_amt", 0))
    hue = int(col.get("hue", 0))
    sat = int(col.get("sat", 0))
    br = int(col.get("bright", 0))
    if not (tint_hex and tint_amt > 0) and hue == 0 and sat == 0 and br == 0:
        return base

    ck = (tag, base.width(), base.height(), tint_hex, tint_amt, hue, sat, br)
    with _CACHE_LOCK:
        if ck in _COLOR_CACHE:
            return _COLOR_CACHE[ck]

    rgba = base.convertToFormat(QImage.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    arr = np.frombuffer(rgba.constBits(), np.uint8).reshape(h, rgba.bytesPerLine() // 4, 4)[:, :w, :].copy()
    rgb = arr[..., :3].astype(np.float32) / 255.0

    # Ajuste HSV rápido si hue != 0 o sat != 0
    if hue != 0 or sat != 0:
        cmax = rgb.max(axis=2)
        cmin = rgb.min(axis=2)
        delta = cmax - cmin
        hh = np.zeros_like(cmax)
        mask = delta > 1e-5
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        m_r = mask & (cmax == r)
        m_g = mask & (cmax == g)
        m_b = mask & (cmax == b)
        hh[m_r] = ((g[m_r] - b[m_r]) / delta[m_r]) % 6.0
        hh[m_g] = ((b[m_g] - r[m_g]) / delta[m_g]) + 2.0
        hh[m_b] = ((r[m_b] - g[m_b]) / delta[m_b]) + 4.0
        hh = (hh / 6.0 + hue / 360.0) % 1.0
        ss = np.where(cmax > 1e-5, delta / np.maximum(cmax, 1e-5), 0.0)
        ss = np.clip(ss * (1.0 + sat / 100.0), 0.0, 1.0)
        vv = cmax
        h6 = hh * 6.0
        i = np.floor(h6).astype(np.int32) % 6
        f = h6 - np.floor(h6)
        p_v = vv * (1.0 - ss)
        q_v = vv * (1.0 - f * ss)
        t_v = vv * (1.0 - (1.0 - f) * ss)
        rgb[..., 0] = np.choose(i, [vv, q_v, p_v, p_v, t_v, vv])
        rgb[..., 1] = np.choose(i, [t_v, vv, vv, q_v, p_v, p_v])
        rgb[..., 2] = np.choose(i, [p_v, p_v, t_v, vv, vv, q_v])

    if tint_hex and tint_amt > 0:
        tc = QColor(tint_hex)
        if tc.isValid():
            t_rgb = np.array([tc.red() / 255.0, tc.green() / 255.0, tc.blue() / 255.0], dtype=np.float32)
            luma = rgb[..., 0:1] * 0.299 + rgb[..., 1:2] * 0.587 + rgb[..., 2:3] * 0.114
            # Tinte preserva el sombreado cel-shaded y el contorno oscuro
            colored = np.clip(t_rgb * (0.25 + luma * 1.35), 0.0, 1.0)
            a_t = tint_amt / 100.0
            if lyr.get("path") == "builtin:body":
                # Proteger piel del cuello al recolorear la sudadera/ropa
                skin_mask = (rgb[..., 0] > 0.72) & ((rgb[..., 0] - rgb[..., 2]) > 0.08) & (rgb[..., 1] > 0.55)
                a_map = np.where(skin_mask, 0.0, a_t)[..., None].astype(np.float32)
                rgb = rgb * (1.0 - a_map) + colored * a_map
            else:
                rgb = rgb * (1.0 - a_t) + colored * a_t

    if br != 0:
        rgb = np.clip(rgb * (1.0 + br / 100.0) + br / 260.0, 0.0, 1.0)

    arr[..., :3] = (np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    out = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    with _CACHE_LOCK:
        if len(_COLOR_CACHE) > 220:
            _COLOR_CACHE.pop(next(iter(_COLOR_CACHE)))
        _COLOR_CACHE[ck] = out
    return out


# =====================================================  Solver de física 2.5D y renderizador del Rig
class RigRuntimeState:
    """Estado dinámico en tiempo real de un Rig VTuber 2D."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.mouth_state = 0
        self.hold_until = 0.0
        self.step_at = 0.0
        self.amp = 0.0
        self.blink_at = 0.0
        self.blink_start = 0.0
        self.blink_end = 0.0
        self.blink_phase = 0       # 0 = abierto, 1 = medio, 2 = cerrado
        # Parámetros manuales (para pruebas tipo Live2D)
        self.manual_head_x = 0.0   # -1..1
        self.manual_head_y = 0.0   # -1..1
        self.manual_head_z = 0.0   # -1..1
        self.manual_mouth = -1     # -1 = auto, 0..3 = forzar boca
        self.manual_blink = -1     # -1 = auto, 0..2 = forzar ojo
        # Estado suave de cabeza/mirada y emociones reactivas
        self.head_x = 0.0
        self.head_y = 0.0
        self.head_z = 0.0
        self.prev_hx = 0.0
        self.prev_hy = 0.0
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.active_emotion = "neutral"
        self.emotion_conf = 0.0
        self.emotion_intensity = 0.0
        self.emotion_since = 0.0
        self._choreo = VoiceEmotionAnalyzer()
        # Física por capa: lyr_id -> [dx, vx, dy, vy, rot, vrot]
        self.phys = {}
        # Últimas matrices globales calculadas (para hit-testing / pivotes en el editor)
        self.world_transforms = {}   # lyr_id -> QTransform (desde espacio local de capa -> lienzo rig)
        self.world_pivots = {}       # lyr_id -> QPointF

    def step(self, rig, level, now, dt, mouse_norm=(0.0, 0.0), face_norm=(0.0, 0.0, 0.0),
             live_mode=True, emotion_state=None, face_full=None):
        dt = max(0.004, min(0.05, float(dt)))
        pg = rig.get("physics_global", {})
        au = rig.get("audio", {})
        tr = rig.get("tracking", {})
        ctrl_mode = tr.get("mode", "voice")  # "voice" | "cam_voice" | "camera"

        # 0. Resolver emoción activa (manual / automática por voz / sonrisa de cámara)
        man_em = tr.get("manual_emotion", "auto")
        if man_em and man_em != "auto":
            target_em = man_em
            target_cf = 1.0
        elif tr.get("auto_emotion", True) and live_mode:
            target_em = "neutral"
            target_cf = 0.0
            if ctrl_mode in ("voice", "cam_voice") and emotion_state:
                target_em = emotion_state.get("emotion", "neutral")
                target_cf = float(emotion_state.get("confidence", 0.0))
            if ctrl_mode in ("camera", "cam_voice") and face_full and face_full.get("detected"):
                if face_full.get("smile", 0.0) > 0.55 and target_em == "neutral":
                    target_em = "happy"
                    target_cf = max(target_cf, float(face_full["smile"]))
        else:
            target_em, target_cf = "neutral", 0.0

        if target_em != self.active_emotion:
            self.active_emotion = target_em
            self.emotion_since = now
        self.emotion_conf = target_cf
        em_goal = 1.0 if target_em != "neutral" else 0.0
        self.emotion_intensity += (em_goal - self.emotion_intensity) * min(1.0, dt * 8.0)

        # 1. Visema vocal (0..3) — voz o cámara según modo
        T, R = float(au.get("threshold", 36)), float(au.get("range", 12))
        lvl = level * 100.0
        if self.manual_mouth >= 0:
            target_m = self.manual_mouth
        elif ctrl_mode == "camera" and face_full and face_full.get("detected"):
            mo = float(face_full.get("mouth_open", 0.0))
            target_m = 3 if mo > 0.78 else (2 if mo > 0.45 else (1 if mo > 0.22 else 0))
        else:
            target_m = int(lvl > T) + int(lvl > T + R) + int(lvl > T + 2 * R)
            if self.active_emotion == "shout" and target_m < 3 and (lvl > T * 0.6 or man_em == "shout"):
                target_m = 3
            elif self.active_emotion == "laugh" and target_m < 2 and (lvl > T * 0.5 or man_em == "laugh"):
                target_m = 2 if math.sin(now * 16.0) > -0.2 else 1
        if target_m >= self.mouth_state:
            self.mouth_state = target_m
            if target_m > 0:
                self.hold_until = now + au.get("hold", 85) / 1000.0
        elif now > self.hold_until and now > self.step_at:
            self.mouth_state -= 1
            self.step_at = now + 0.05
        self.amp += ((self.mouth_state / 3.0) - self.amp) * min(1.0, dt * 15.0)

        # 2. Parpadeo 3 fases (cámara si activa y detectada, sino automático natural)
        if self.manual_blink >= 0:
            self.blink_phase = self.manual_blink
        elif ctrl_mode in ("camera", "cam_voice") and face_full and face_full.get("detected") and face_full.get("blink", 0.0) > 0.38:
            self.blink_phase = 2 if face_full["blink"] > 0.68 else 1
        elif pg.get("blink_auto", True) and live_mode:
            be = max(1.0, float(pg.get("blink_every", 4)))
            if self.blink_at == 0.0:
                self.blink_at = now + self.rng.uniform(0.5, 1.2) * be
            elif now > self.blink_at:
                bd = max(0.08, pg.get("blink_ms", 190) / 1000.0)
                self.blink_start = now
                self.blink_end = now + bd
                self.blink_at = now + self.rng.uniform(0.6, 1.4) * be
            if self.blink_start <= now < self.blink_end:
                frac = (now - self.blink_start) / max(0.01, self.blink_end - self.blink_start)
                self.blink_phase = 1 if (frac < 0.25 or frac > 0.75) else 2
            else:
                self.blink_phase = 0
        else:
            self.blink_phase = 0

        # 3. Pose de cabeza 2.5D y mirada según Modo de Control (Solo Voz / Cámara + Voz / Solo Cámara)
        tx, ty, tz = self.manual_head_x, self.manual_head_y, self.manual_head_z
        gx, gy = self.manual_head_x, self.manual_head_y
        if live_mode:
            use_cam = ctrl_mode in ("camera", "cam_voice")
            use_voice_choreo = (ctrl_mode == "voice") or (use_cam and not (face_full and face_full.get("detected")))

            if use_cam and face_full and face_full.get("detected"):
                tx += float(face_full["x"]) * 1.15
                ty += float(face_full["y"]) * 1.05
                tz += float(face_full.get("roll", 0.0)) * 1.10 + float(face_full["x"]) * 0.35
                gx += float(face_full["x"]) * 0.90
                gy += float(face_full["y"]) * 0.80
            elif use_cam and (abs(face_norm[0]) > 1e-3 or abs(face_norm[1]) > 1e-3):
                tx += face_norm[0] * 1.1
                ty += face_norm[1] * 1.0
                tz += face_norm[0] * 0.65
                gx += face_norm[0] * 0.85
                gy += face_norm[1] * 0.75

            if use_voice_choreo:
                vhx, vhy, vhz, vgx, vgy = self._choreo.voice_choreography(now, max(level, self.amp), self.active_emotion)
                tx += vhx
                ty += vhy
                tz += vhz
                gx += vgx
                gy += vgy
            elif self.active_emotion != "neutral":
                # Acentuar postura emocional incluso en modo Cámara + Voz
                vhx, vhy, vhz, _, _ = self._choreo.voice_choreography(now, max(level, self.amp), self.active_emotion)
                tx += vhx * 0.35
                ty += vhy * 0.45
                tz += vhz * 0.35

            if pg.get("mouse_track", True) and (abs(mouse_norm[0]) > 1e-3 or abs(mouse_norm[1]) > 1e-3):
                mw = 0.45 if ctrl_mode == "voice" else 0.30
                tx += mouse_norm[0] * mw
                ty += mouse_norm[1] * mw * 0.85
                gx = gx * 0.5 + mouse_norm[0] * 0.75
                gy = gy * 0.5 + mouse_norm[1] * 0.65

            if self.amp > 0.02 and ctrl_mode != "camera":
                ty -= self.amp * 0.20 * abs(math.sin(now * 9.5))
                tz += self.amp * 0.22 * math.sin(now * 6.5)

        tx, ty, tz = max(-1.25, min(1.25, tx)), max(-1.25, min(1.25, ty)), max(-1.25, min(1.25, tz))
        self.prev_hx, self.prev_hy = self.head_x, self.head_y
        self.head_x += (tx - self.head_x) * min(1.0, dt * 11.0)
        self.head_y += (ty - self.head_y) * min(1.0, dt * 11.0)
        self.head_z += (tz - self.head_z) * min(1.0, dt * 10.0)
        vel_hx = (self.head_x - self.prev_hx) / dt
        vel_hy = (self.head_y - self.prev_hy) / dt

        self.gaze_x += (max(-1.0, min(1.0, gx)) - self.gaze_x) * min(1.0, dt * 15.0)
        self.gaze_y += (max(-1.0, min(1.0, gy)) - self.gaze_y) * min(1.0, dt * 15.0)

        # 4. Física independiente por capa
        wind_g = float(pg.get("wind_strength", 25)) / 100.0
        b_spd = float(pg.get("breathe_speed", 10)) / 10.0
        bounce_talk = float(pg.get("bounce_talk", 18))

        for lyr in rig.get("layers", []):
            lid = lyr["id"]
            st = self.phys.setdefault(lid, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # dx, vx, dy, vy, rot, vrot
            ph = lyr.get("physics", {})
            if not live_mode or not ph.get("enabled", False):
                st[0] *= 0.8
                st[2] *= 0.8
                st[4] *= 0.8
                continue

            mode = ph.get("mode", "pendulum")
            k = max(4.0, float(ph.get("stiffness", 18))) * 10.0
            damp = max(0.18, float(ph.get("damping", 65)) / 100.0) * 2.0 * math.sqrt(k) * 0.45
            ix = float(ph.get("inertia_x", 35)) / 100.0
            iy = float(ph.get("inertia_y", 25)) / 100.0
            swing = float(ph.get("swing_deg", 12))
            w_amt = float(ph.get("wind_amt", 20)) / 100.0 * wind_g
            b_amt = float(ph.get("breathe_amt", 15)) / 100.0
            phase = float(ph.get("phase", 0.0))

            if mode == "pendulum":
                # Péndulo real excitado por inercia de cabeza + brisa
                wind_ang = math.sin(now * 2.6 + phase) * swing * 0.65 * w_amt + math.sin(now * 4.3 + phase) * swing * 0.25 * w_amt
                target_r = max(-45.0, min(45.0, -vel_hx * swing * 0.48 * ix + wind_ang))
                acc_r = (target_r - st[4]) * k - st[5] * damp
                st[5] += acc_r * dt
                st[4] = max(-50.0, min(50.0, st[4] + st[5] * dt))
                target_dy = -vel_hy * 14.0 * iy + math.sin(now * b_spd * 2.0 + phase) * 4.0 * b_amt
                st[3] += ((target_dy - st[2]) * k - st[3] * damp) * dt
                st[2] = max(-40.0, min(40.0, st[2] + st[3] * dt))
                st[0] = -st[4] * 0.45
            elif mode == "spring":
                target_y = -bounce_talk * self.amp * abs(math.sin(now * 9.5 + phase)) - vel_hy * 18.0 * iy
                st[3] += ((target_y - st[2]) * k - st[3] * damp) * dt
                st[2] = max(-60.0, min(60.0, st[2] + st[3] * dt))
                target_r = swing * self.amp * math.sin(now * 6.5 + phase) + self.head_z * swing * 0.75
                st[5] += ((target_r - st[4]) * k - st[5] * damp) * dt
                st[4] = max(-40.0, min(40.0, st[4] + st[5] * dt))
                st[0] = 0.0
            elif mode == "breathe":
                st[0] = 0.0
                st[2] = math.sin(now * b_spd * 2.1 + phase) * 14.0 * b_amt - bounce_talk * 0.5 * self.amp * abs(math.sin(now * 9.5))
                st[4] = math.sin(now * b_spd * 1.05 + phase) * 1.4 * b_amt
            elif mode == "wind":
                st[0] = math.sin(now * 3.1 + phase) * 16.0 * w_amt
                st[2] = math.cos(now * 2.3 + phase) * 6.0 * w_amt
                st[4] = math.sin(now * 2.8 + phase) * swing * (0.4 + w_amt)
            elif mode == "flap":
                st[0] = 0.0
                st[2] = math.sin(now * 5.0 + phase) * 10.0 * iy
                st[4] = math.sin(now * 5.2 + phase) * swing
            elif mode == "float":
                st[0] = math.cos(now * 1.6 + phase) * 10.0 * max(0.3, w_amt)
                st[2] = math.sin(now * 2.4 + phase) * 20.0 * max(0.3, w_amt)
                st[4] = math.sin(now * 1.8 + phase) * swing * 0.6


def _layer_visible_now(lyr, state, expr_overrides, toggles, live_mode):
    ov = expr_overrides.get(lyr["id"], {})
    base_vis = ov.get("visible", lyr.get("visible", True))
    if not live_mode:
        # En modo Armar/Rig mostrar todas las capas que el usuario tenga activadas (excepto variantes ocultas de boca/ojo)
        role = lyr.get("role", "always")
        if role in ("eye_half", "eye_closed", "mouth_mid", "mouth_open", "mouth_wide"):
            return False if not ov.get("visible", False) else True
        return bool(base_vis)

    role = lyr.get("role", "always")
    if role == "toggle":
        hk = lyr.get("hotkey", "")
        if hk and hk in toggles:
            return bool(toggles[hk])
        return bool(base_vis)
    if not base_vis and "visible" not in ov:
        return False
    if "visible" in ov:
        return bool(ov["visible"])

    if role == "eye_open":
        return state.blink_phase == 0
    if role == "pupil":
        # Ocultar pupilas durante ojos cerrados o sonrisa/risa cerrada (^ ^)
        return state.blink_phase == 0 and state.active_emotion != "laugh"
    if role == "eye_half":
        return state.blink_phase == 1
    if role == "eye_closed":
        return state.blink_phase == 2
    if role == "mouth_closed":
        return state.mouth_state == 0
    if role == "mouth_mid":
        return state.mouth_state == 1
    if role == "mouth_open":
        return state.mouth_state == 2
    if role == "mouth_wide":
        return state.mouth_state >= 3
    if role == "talk_only":
        return state.mouth_state > 0
    if role == "idle_only":
        return state.mouth_state == 0
    return bool(base_vis)


def render_rig(painter, rig, state, now, live_mode=True, show_debug_layer=None, show_bones=False):
    """Resuelve la jerarquía padre-hijo 2.5D y renderiza el Rig VTuber completo en coordenadas del rig."""
    layers = rig.get("layers", [])
    by_id = {l["id"]: l for l in layers}
    pg = rig.get("physics_global", {})
    hx_px = float(pg.get("head_x_px", 42.0))
    hy_px = float(pg.get("head_y_px", 28.0))
    h_rot = float(pg.get("head_rot_deg", 11.0))

    exprs = rig.get("expressions", [])
    eidx = max(0, min(len(exprs) - 1, int(rig.get("expr", 0)))) if exprs else -1
    expr_ov = exprs[eidx].get("overrides", {}) if eidx >= 0 else {}
    toggles = rig.get("toggles", {})

    world_tf = {}
    world_piv = {}
    visiting = set()

    def solve_tf(lid):
        if lid in world_tf:
            return world_tf[lid]
        if lid in visiting or lid not in by_id:
            return QTransform()
        visiting.add(lid)
        lyr = by_id[lid]
        pid = lyr.get("parent_id")
        parent_tf = solve_tf(pid) if (pid and pid in by_id) else QTransform()

        ov = expr_ov.get(lid, {})
        phys = state.phys.get(lid, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

        # Paralaje 2.5D según depth_z (-100..+100)
        dz = float(lyr.get("depth_z", 0.0)) / 100.0
        par_x = state.head_x * hx_px * dz if live_mode else 0.0
        par_y = state.head_y * hy_px * dz if live_mode else 0.0
        par_r = state.head_z * h_rot * (0.35 if pid is None else 0.55) * (1.0 if dz >= -0.15 else 0.35) if live_mode else 0.0

        # Mirada de pupilas restringida dentro del ojo
        gaze_dx = gaze_dy = 0.0
        if live_mode and lyr.get("role") == "pupil":
            gr = float(lyr.get("gaze_range", 14.0))
            gaze_dx = state.gaze_x * gr
            gaze_dy = state.gaze_y * gr * 0.72

        lx = float(lyr.get("x", 0.0)) + par_x + phys[0] + gaze_dx + float(ov.get("dx", 0.0))
        ly = float(lyr.get("y", 0.0)) + par_y + phys[2] + gaze_dy + float(ov.get("dy", 0.0))
        rot = float(lyr.get("rotation", 0.0)) + par_r + phys[4] + float(ov.get("rot", 0.0))
        ov_sc = float(ov.get("scale", 1.0))
        sx = float(lyr.get("scale_x", 1.0)) * ov_sc * (-1.0 if lyr.get("flip_x") else 1.0)
        sy = float(lyr.get("scale_y", 1.0)) * ov_sc

        tf = QTransform(parent_tf)
        tf.translate(lx, ly)
        world_piv[lid] = tf.map(QPointF(0.0, 0.0))
        tf.rotate(rot)
        tf.scale(sx, sy)

        world_tf[lid] = tf
        visiting.discard(lid)
        return tf

    for lyr in layers:
        solve_tf(lyr["id"])

    state.world_transforms = world_tf
    state.world_pivots = world_piv

    # Temblor de euforia/grito o salto de sorpresa aplicado al rig completo
    em = state.active_emotion if live_mode else "neutral"
    shake_x = shake_y = 0.0
    if live_mode and em == "shout":
        shake_x = math.sin(now * 62.0) * 7.5
        shake_y = math.cos(now * 54.0) * 5.5
    elif live_mode and em == "laugh":
        shake_y = -abs(math.sin(now * 13.5)) * 16.0
    elif live_mode and em == "surprised":
        dt_em = max(0.0, now - state.emotion_since)
        shake_y = -math.exp(-dt_em * 7.0) * 28.0

    # Dibujar ordenados por Z
    for lyr in sorted(layers, key=lambda item: float(item.get("z", 0))):
        lid = lyr["id"]
        force_show = (not live_mode and show_debug_layer == lid)
        if not force_show and not _layer_visible_now(lyr, state, expr_ov, toggles, live_mode):
            continue
        img = _load_layer_image(lyr, now, emotion=em)
        if img is None:
            continue
        ov = expr_ov.get(lid, {})
        op = max(0.0, min(1.0, float(ov.get("opacity", lyr.get("opacity", 100))) / 100.0))
        if lyr.get("path") == "builtin:blush" and em in ("happy", "laugh"):
            op = min(1.0, max(op, 0.92))
        if op <= 0.005:
            continue
        w, h = float(lyr.get("w", 300)), float(lyr.get("h", 300))
        px, py = float(lyr.get("pivot_x", 0.5)) * w, float(lyr.get("pivot_y", 0.5)) * h

        painter.save()
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if shake_x or shake_y:
            painter.translate(shake_x, shake_y)
        painter.setTransform(world_tf[lid], True)
        painter.setOpacity(painter.opacity() * op)
        painter.setCompositionMode(_BLEND_MAP.get(lyr.get("blend_mode", "normal"), QPainter.CompositionMode_SourceOver))
        painter.drawImage(QRectF(-px, -py, w, h), img)
        painter.restore()

    # Reacciones visuales automáticas según la emoción detectada
    if live_mode and em != "neutral" and rig.get("tracking", {}).get("auto_reactions", True):
        _draw_emotion_reactions(painter, rig, state, now, world_piv)

    # Huesos / conectores jerárquicos en modo Rigging
    if show_bones and not live_mode:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        for lyr in layers:
            lid, pid = lyr["id"], lyr.get("parent_id")
            if pid and pid in world_piv and lid in world_piv:
                p0, p1 = world_piv[pid], world_piv[lid]
                painter.setPen(QPen(QColor(229, 160, 13, 150), 2.0, Qt.DashLine))
                painter.drawLine(p0, p1)
        for lyr in layers:
            lid = lyr["id"]
            if lid in world_piv:
                pt = world_piv[lid]
                is_sel = (lid == show_debug_layer)
                painter.setPen(QPen(QColor("#101012"), 2.5))
                painter.setBrush(QColor("#E5A00D" if is_sel else "#2F7CF6"))
                r = 7.0 if is_sel else 4.5
                painter.drawEllipse(pt, r, r)
        painter.restore()


def _draw_emotion_reactions(painter, rig, state, now, world_piv):
    """Dibuja reacciones animadas vectoriales limpias según la emoción detectada (risa, grito, tristeza, enojo, sorpresa, felicidad)."""
    em = state.active_emotion
    rw, rh = float(rig.get("canvas_w", RIG_W)), float(rig.get("canvas_h", RIG_H))
    # Buscar pivote de la cabeza para anclar reacciones cerca del rostro
    head_pt = QPointF(rw * 0.5, rh * 0.44)
    for lyr in rig.get("layers", []):
        if lyr.get("path") == "builtin:head" or "cabeza" in lyr.get("name", "").lower() or "head" in lyr.get("name", "").lower():
            if lyr["id"] in world_piv:
                head_pt = QPointF(world_piv[lyr["id"]].x(), world_piv[lyr["id"]].y() - 160.0)
                break

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)

    if em == "shout":
        # Líneas cinéticas radiales anime en los bordes del lienzo (impacto visual limpio)
        cx, cy = head_pt.x(), head_pt.y() + 40.0
        painter.setPen(QPen(QColor(229, 72, 77, 175), 5.0, Qt.SolidLine, Qt.RoundCap))
        for i in range(18):
            ang = (i / 18.0) * math.tau + math.sin(now * 28.0 + i) * 0.04
            r0 = 335.0 + ((i * 37) % 55) + math.sin(now * 35.0 + i * 1.7) * 18.0
            r1 = 520.0
            painter.drawLine(QPointF(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                             QPointF(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1))
        # Rayo / chispas de énfasis junto a la cabeza
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#E5484D"))
        for side in (-1, 1):
            px = head_pt.x() + side * 245.0
            py = head_pt.y() - 75.0 + math.sin(now * 24.0 + side) * 10.0
            bolt = QPolygonF([
                QPointF(px, py - 34), QPointF(px + side * 24, py - 4),
                QPointF(px + side * 6, py + 2), QPointF(px + side * 22, py + 36),
                QPointF(px - side * 14, py + 2), QPointF(px + side * 2, py - 6),
            ])
            painter.drawPolygon(bolt)

    elif em == "laugh":
        # Chispas doradas + notas alegres flotando alrededor de la cabeza
        painter.setFont(QFont("Segoe UI", 34, QFont.Bold))
        syms = [("✦", -235, -90, "#F59E0B"), ("♪", 230, -110, "#FBBF24"),
                ("✦", 250, 20, "#F59E0B"), ("✧", -255, 30, "#FCD34D")]
        for i, (sym, dx, dy, col) in enumerate(syms):
            bob = math.sin(now * 7.5 + i * 1.4) * 18.0
            sc = 0.88 + 0.20 * math.sin(now * 9.0 + i)
            painter.save()
            painter.translate(head_pt.x() + dx, head_pt.y() + dy + bob)
            painter.scale(sc, sc)
            painter.setPen(QColor(col))
            painter.drawText(QRectF(-32, -32, 64, 64), Qt.AlignCenter, sym)
            painter.restore()

    elif em == "happy":
        # Estrellas suaves flotantes + destellos cálidos
        painter.setFont(QFont("Segoe UI", 28, QFont.Bold))
        stars = [("★", -225, -80, "#2EB67D"), ("✦", 225, -95, "#38BDF8"), ("♥", 210, 35, "#F472B6")]
        for i, (sym, dx, dy, col) in enumerate(stars):
            bob = math.sin(now * 3.8 + i * 1.9) * 14.0
            painter.setPen(QColor(col))
            painter.drawText(QRectF(head_pt.x() + dx - 28, head_pt.y() + dy + bob - 28, 56, 56), Qt.AlignCenter, sym)

    elif em == "sad":
        # Lluvia sutil melancólica + gota de lágrima cayendo suavemente
        painter.setPen(QPen(QColor(96, 185, 250, 120), 3.0, Qt.SolidLine, Qt.RoundCap))
        for i in range(7):
            rx = head_pt.x() - 250.0 + i * 82.0
            ry = ((now * 210.0 + i * 95.0) % 420.0) + head_pt.y() - 210.0
            painter.drawLine(QPointF(rx, ry), QPointF(rx - 4.0, ry + 26.0))
        # Gota animada junto a la mejilla
        drop_y = head_pt.y() + 65.0 + ((now * 68.0) % 58.0)
        drop_a = int(max(40, 225 * (1.0 - ((now * 68.0) % 58.0) / 58.0)))
        drop = QPainterPath()
        dx_d = head_pt.x() + 108.0
        drop.moveTo(dx_d, drop_y - 16)
        drop.quadTo(dx_d + 13, drop_y + 6, dx_d, drop_y + 14)
        drop.quadTo(dx_d - 13, drop_y + 6, dx_d, drop_y - 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(96, 185, 250, drop_a))
        painter.drawPath(drop)

    elif em == "angry":
        # Marca anime clásica de vena de enojo pulsando en la sien + vapor sutil
        pulse = 1.0 + 0.14 * math.sin(now * 16.0)
        ax, ay = head_pt.x() + 155.0, head_pt.y() - 95.0
        painter.save()
        painter.translate(ax, ay)
        painter.scale(pulse, pulse)
        painter.setPen(QPen(QColor("#E5484D"), 7.0, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            path = QPainterPath()
            path.moveTo(sx * 24, sy * 6)
            path.quadTo(sx * 7, sy * 7, sx * 6, sy * 24)
            painter.drawPath(path)
        painter.restore()
        # Pequeñas volutas de vapor
        for side in (-1, 1):
            phase = (now * 1.8 + (0.0 if side < 0 else 0.5)) % 1.0
            sx_p = head_pt.x() + side * (200.0 + phase * 32.0)
            sy_p = head_pt.y() - 60.0 - phase * 65.0
            a_p = int(max(0, (1.0 - phase) * 160))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(249, 115, 22, a_p))
            painter.drawEllipse(QPointF(sx_p, sy_p), 12.0 + phase * 14.0, 9.0 + phase * 10.0)

    elif em == "surprised":
        # Signo de exclamación '!' nítido con rebote pop
        dt_em = max(0.0, now - state.emotion_since)
        pop = 1.0 + math.exp(-dt_em * 9.0) * 0.45
        sx, sy = head_pt.x() + 225.0, head_pt.y() - 105.0
        painter.save()
        painter.translate(sx, sy)
        painter.scale(pop, pop)
        painter.setPen(QPen(QColor("#101012"), 5.0))
        painter.setBrush(QColor("#A78BFA"))
        painter.drawRoundedRect(QRectF(-13, -52, 26, 58), 10, 10)
        painter.drawEllipse(QPointF(0, 26), 13, 13)
        painter.restore()

    painter.restore()


def render_rig_to_image(rig, state, now, out_w=560, out_h=560, live_mode=True):
    """Renderiza el Rig 2.5D en un QImage transparente listo para el lienzo de video o exportación."""
    rw, rh = float(rig.get("canvas_w", RIG_W)), float(rig.get("canvas_h", RIG_H))
    img = QImage(out_w, out_h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    sc = min(out_w / rw, out_h / rh)
    p.translate((out_w - rw * sc) * 0.5, (out_h - rh * sc) * 0.5)
    p.scale(sc, sc)
    render_rig(p, rig, state, now, live_mode=live_mode)
    p.end()
    return img


def bake_rig_to_12_sprites(rig, out_dir, size=1024):
    """Hornea el Rig 2D en un paquete ordenado de 12 sprites PNG transparentes listos para cualquier app PNGTuber."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    combos = [
        ("closed", 0, 0), ("mid", 1, 0), ("open", 2, 0), ("wide", 3, 0),
        ("half_closed", 0, 1), ("half_mid", 1, 1), ("half_open", 2, 1),
        ("blink_closed", 0, 2), ("blink_mid", 1, 2), ("blink_open", 2, 2), ("blink_wide", 3, 2),
        ("idle_alt", 0, 0),
    ]
    written = []
    st = RigRuntimeState(seed=42)
    for key, m_st, b_st in combos:
        st.manual_mouth = m_st
        st.manual_blink = b_st
        st.mouth_state = m_st
        st.blink_phase = b_st
        st.step(rig, 0.0, 0.0, 0.016, live_mode=True)
        img = render_rig_to_image(rig, st, 0.0, out_w=size, out_h=size, live_mode=True)
        fp = out_dir / f"{key}.png"
        img.save(str(fp), "PNG")
        written.append(fp)
    return written
