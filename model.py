"""Modelo de datos del proyecto (.setxd v3), plantillas, historial reciente y migración compatible."""
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
PROJECT = BASE / "proyecto.setxd"
LEGACY_PROJECT = BASE / "project.json"
RECENT_FILE = BASE / "recent_projects.json"
RECORDINGS = BASE / "recordings"
W, H = 1920, 1080

SLOTS = [
    ("closed", "Cerrada", "Silencio"),
    ("mid", "Media", "Voz suave"),
    ("open", "Abierta", "Voz normal"),
    ("wide", "Gritando", "Voz fuerte"),
    ("half_closed", "Ojos medios", "Boca cerrada"),
    ("half_mid", "Ojos medios", "Boca media"),
    ("half_open", "Ojos medios", "Boca abierta"),
    ("blink_closed", "Ojos cerrados", "Boca cerrada"),
    ("blink_mid", "Ojos cerrados", "Boca media"),
    ("blink_open", "Ojos cerrados", "Boca abierta"),
    ("blink_wide", "Ojos cerrados", "Gritando"),
    ("idle_alt", "Reposo extra", "Silencio largo"),
]
SIMPLE_KEYS = ["closed", "mid", "open"]
KEYS = ["closed", "mid", "open", "wide"]
HALF_OF = {"closed": "half_closed", "mid": "half_mid", "open": "half_open", "wide": "half_open"}
BLINK_OF = {"closed": "blink_closed", "mid": "blink_mid", "open": "blink_open", "wide": "blink_wide"}

# Paleta profesional para diferenciar personajes en pistas y avatares
PALETTE = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#EC4899", "#06B6D4", "#84CC16", "#F97316"]

EXTS = "Imágenes y GIF (*.png *.jpg *.jpeg *.webp *.gif *.bmp)"
AUDIO_EXTS = "Audio (*.wav *.mp3 *.flac *.ogg *.aiff *.m4a)"
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif", ".m4a"}
PROJECT_FILTER = "Proyecto PodcastSet (*.setxd *.vtr *.vt3d);;Proyecto Video (*.setxd);;VTuber 2D (*.vtr);;VTuber 3D (*.vt3d);;JSON heredado (*.json)"
RIG_FILTER = "Rig VTuber 2D (*.vtr)"
RIG3D_FILTER = "Avatar VTuber 3D (*.vt3d)"

# Plantillas de proyecto para el espacio vacío inicial
PROJECT_TEMPLATES = [
    {
        "id": "podcast_1080",
        "title": "Podcast / Streaming 16:9",
        "subtitle": "1920 × 1080 · 30 FPS · Estándar YouTube / OBS",
        "w": 1920, "h": 1080, "fps": 30, "bg": "#121215", "transparent": False,
    },
    {
        "id": "vtuber_60",
        "title": "VTuber Fluido 60 FPS",
        "subtitle": "1920 × 1080 · 60 FPS · Animación ultra suave",
        "w": 1920, "h": 1080, "fps": 60, "bg": "#121215", "transparent": False,
    },
    {
        "id": "shorts_vertical",
        "title": "Vertical Shorts / TikTok",
        "subtitle": "1080 × 1920 · 60 FPS · Formato 9:16",
        "w": 1080, "h": 1920, "fps": 60, "bg": "#121215", "transparent": False,
    },
    {
        "id": "overlay_alpha",
        "title": "Overlay VTuber Transparente",
        "subtitle": "1920 × 1080 · 60 FPS · Fondo transparente / Alfa",
        "w": 1920, "h": 1080, "fps": 60, "bg": "#00b140", "transparent": True,
    },
    {
        "id": "square_social",
        "title": "Clip Cuadrado 1:1",
        "subtitle": "1080 × 1080 · 30 FPS · Redes sociales",
        "w": 1080, "h": 1080, "fps": 30, "bg": "#121215", "transparent": False,
    },
    {
        "id": "studio_4k",
        "title": "Producción 4K UHD",
        "subtitle": "3840 × 2160 · 30 FPS · Máxima nitidez",
        "w": 3840, "h": 2160, "fps": 30, "bg": "#121215", "transparent": False,
    },
]

# Reacciones / Emotes 2D vectoriales disparables en vivo o en timeline
EMOTE_CATALOG = [
    ("excl", "!", "Alerta / Sorpresa", "#E5484D"),
    ("quest", "?", "Duda", "#2F7CF6"),
    ("heart", "♥", "Corazón", "#E55381"),
    ("star", "★", "Estrella", "#E5A00D"),
    ("spark", "✦", "Chispas", "#38BDF8"),
    ("sweat", "●", "Gota nerviosa", "#60A5FA"),
    ("note", "♪", "Música", "#2EB67D"),
    ("confetti", "❖", "Confeti", "#F97316"),
]

# Catálogo de filtros modulares estilo OBS Studio
FILTER_CATALOG = [
    ("noise_suppress", "Eliminador de ruido", {"strength": 65}),
    ("noise_gate", "Puerta anti-ruido", {"close_db": -46, "open_db": -38}),
    ("highpass", "Filtro pasa-altos", {"hz": 90}),
    ("compressor", "Compresor de voz", {"threshold_db": -18, "ratio": 3.5, "makeup_db": 3.0}),
    ("eq3", "Ecualizador 3 bandas", {"low_db": 0.0, "mid_db": 1.0, "high_db": 2.0}),
    ("pitch_shift", "Cambiador de voz / Pitch", {"semitones": 0.0, "mix": 100}),
    ("reverb", "Reverb de Estudio", {"room": 42, "mix": 22}),
    ("gain", "Ganancia", {"db": 3.0}),
    ("limiter", "Limitador", {"ceiling_db": -1.5}),
]


def new_filter(ftype):
    for k, name, params in FILTER_CATALOG:
        if k == ftype:
            return {"id": uuid.uuid4().hex[:6], "type": k, "name": name, "enabled": True, "params": copy.deepcopy(params)}
    return {"id": uuid.uuid4().hex[:6], "type": ftype, "name": ftype, "enabled": True, "params": {}}


def default_settings():
    return {
        "save_dir": str(RECORDINGS),
        "grid_size": 20,
        "smart_guides": True,
        "accent": "cobalt",
        "mic_filters": [
            new_filter("highpass"),
            new_filter("noise_suppress"),
            new_filter("compressor"),
            new_filter("limiter"),
        ],
        "vcam_res": "1280x720",
        "vcam_fps": 30,
        "discord_port": 8765,
        "discord_live": False,
        "discord_map": {},
        "webcam_index": 0,
        "webcam_enabled": False,
        "mouse_track_global": True,
        "export_organize": True,
        "export_gpu": True,
        "export_normalize": True,
    }


def resolve(p):
    if not p:
        return ""
    return str(p) if Path(p).is_absolute() else str(BASE / p)


def open_path(path):
    p = Path(path)
    if p.is_file():
        p = p.parent
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(p))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])


def import_asset(src):
    ASSETS.mkdir(exist_ok=True)
    src = Path(src)
    try:
        if src.resolve().parent == ASSETS.resolve():
            return f"assets/{src.name}"
    except Exception:
        pass
    dst = ASSETS / f"{src.stem}_{uuid.uuid4().hex[:4]}{src.suffix}"
    shutil.copy2(src, dst)
    return f"assets/{dst.name}"


def new_expression(name="Normal"):
    return {"name": name, "sprites": {k: "" for k, _, _ in SLOTS}}


def new_accessory(name="Accesorio", path=""):
    return {
        "id": "acc_" + uuid.uuid4().hex[:6],
        "name": name,
        "path": path,
        "visible": True,
        "z_rel": "front",          # "front" | "back"
        "dx": 0.0,
        "dy": -80.0,
        "scale": 0.5,
        "rotation": 0.0,
        "opacity": 100,
        "physics_mode": "spring",  # "rigid" | "spring" | "sway" | "float" | "spin"
        "physics_amount": 45,
        "hotkey": "",
    }


def new_char(name, index=0):
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "color": PALETTE[index % len(PALETTE)],
        "source": "mic",
        "device": None,
        "mute": False,
        "solo": False,
        "arm": True,
        "gain": 100,
        "threshold": 40,
        "range": 12,
        "hold": 90,
        "flip": False,
        # Física 2D VTuber
        "motion_style": "spring",   # "spring" | "bounce" | "energetic" | "subtle"
        "bounce": 16,
        "sway": 4,
        "breathe": 1,
        "squash": 5,
        "float_amp": 0,
        "float_speed": 10,          # /10 = 1.0 Hz
        "shout_shake": 6,
        "spring_stiffness": 16,
        "spring_damping": 68,       # 0..100
        "pivot": "bottom",          # "bottom" | "center" | "top"
        # Interactividad y foco de podcast
        "mouse_track": 0,           # 0..100 %
        "face_track": False,
        "face_intensity": 60,
        "dim_inactive": False,
        "dim_amount": 38,           # 0..75 %
        "talk_zoom": 4,             # 0..20 %
        # Parpadeo
        "blink": True,
        "blink_every": 4,
        "blink_ms": 200,
        # Efectos visuales 2D
        "opacity": 100,
        "shadow": False,
        "shadow_blur": 14,
        "shadow_alpha": 45,
        "shadow_dx": 0,
        "shadow_dy": 12,
        "outline": False,
        "outline_width": 6,
        "outline_color": "#FFFFFF",
        "outline_talk_only": True,
        "brightness": 0,            # -50..50
        "saturation": 0,            # -100..100
        # Expresiones y accesorios acoplados
        "expressions": [new_expression()],
        "expr": 0,
        "accessories": [],
        "rig_path": "",             # Ruta opcional a un Rig VTuber 2D (.vtr)
        "rig_data": None,           # Rig embebido opcional (se renderiza vivo 2.5D)
        "rig3d_path": "",           # Ruta opcional a un Avatar VTuber 3D (.vt3d)
        "rig3d_data": None,         # Avatar 3D embebido opcional (se renderiza vivo en 3D)
    }


def new_prop(kind, name=None, canvas_w=1920, canvas_h=1080):
    pid = "prop_" + uuid.uuid4().hex[:6]
    base = {
        "id": pid,
        "kind": kind,               # "image" | "text" | "visualizer"
        "name": name or {"image": "Imagen / Logo", "text": "Texto", "visualizer": "Visualizador"}.get(kind, "Capa"),
        "visible": True,
        "locked": False,
        "x": canvas_w * 0.5 - 200,
        "y": canvas_h * 0.15,
        "w": 400,
        "h": 220,
        "scale": 1.0,
        "rotation": 0.0,
        "opacity": 100,
        "z": 50,
        "anim": "none",             # "none" | "breathe" | "float" | "pulse"
        "anim_amount": 35,
    }
    if kind == "image":
        base.update({"path": "", "w": 360, "h": 360})
    elif kind == "text":
        base.update({
            "text": "TÍTULO DEL EPISODIO",
            "subtext": "",
            "font_family": "Segoe UI",
            "font_size": 42,
            "bold": True,
            "align": "center",
            "color": "#FFFFFF",
            "bg_enabled": True,
            "bg_color": "#141418",
            "bg_opacity": 78,
            "accent_bar": True,
            "accent_color": "#2F7CF6",
            "radius": 10,
            "padding": 18,
            "w": 620,
            "h": 92,
            "x": (canvas_w - 620) / 2,
            "y": canvas_h - 150,
        })
    elif kind == "visualizer":
        base.update({
            "source_cid": "master",
            "style": "bars",        # "bars" | "mirror" | "wave" | "dots"
            "bars": 36,
            "color": "#ECECEF",
            "color2": "#2F7CF6",
            "sensitivity": 120,
            "roundness": 4,
            "w": 560,
            "h": 110,
            "x": (canvas_w - 560) / 2,
            "y": canvas_h - 160,
        })
    return base


def new_marker(time_s, kind="scene", label="", target_id="", value=None, color=""):
    colors = {"scene": "#2F7CF6", "expr": "#E5A00D", "emote": "#EC4899", "chapter": "#2EB67D"}
    return {
        "id": "mk_" + uuid.uuid4().hex[:6],
        "time": round(max(0.0, float(time_s)), 3),
        "kind": kind,               # "scene" | "expr" | "emote" | "chapter"
        "target_id": target_id,
        "value": value,
        "label": label or kind.capitalize(),
        "color": color or colors.get(kind, "#2F7CF6"),
    }


def new_subtrack(cid, label="Sub-pista"):
    return {
        "id": "sub_" + uuid.uuid4().hex[:6],
        "cid": cid,
        "label": label,
        "arm": False,
        "solo": False,
        "mute": False,
    }


def default_placement(n, canvas_w=1920, canvas_h=1080):
    spacing = min(380, max(200, (canvas_w - 240) / max(1, n + 1)))
    x = max(60, min(canvas_w - 420, 120 + spacing * n))
    y = max(80, canvas_h - 600)
    return {"x": float(x), "y": float(y), "scale": 1.0, "rotation": 0.0, "visible": True, "z": n}


def new_scene(name):
    return {
        "name": name,
        "background": "",
        "bg_type": "color",         # "color" | "gradient" | "transparent"
        "bg_grad_color2": "#1D2430",
        "bg_grad_angle": 135,
        "bg_mode": "cover",         # "cover" | "contain" | "stretch"
        "bg_blur": 0,               # 0..30
        "bg_dim": 0,                # 0..80 %
        "bg_vignette": 0,           # 0..80 %
        "transition": "fade",       # "cut" | "fade" | "slide"
        "transition_ms": 280,
        "placements": {},
        "props": [],
    }


def new_empty_project(name="Nuevo Proyecto", canvas_w=1920, canvas_h=1080, fps=30,
                      bg_color="#121215", transparent=False):
    scene = new_scene("Escena 1")
    if transparent:
        scene["bg_type"] = "transparent"
    now = time.strftime("%Y-%m-%d %H:%M")
    return {
        "format": "setxd",
        "version": 3,
        "project_name": name,
        "canvas_w": int(canvas_w),
        "canvas_h": int(canvas_h),
        "fps": int(fps),
        "bg_color": bg_color,
        "transparent_bg": bool(transparent),
        "sprite_mode": "simple",
        "characters": [],
        "scenes": [scene],
        "timeline_clips": [],
        "subtracks": [],
        "markers": [],
        "in_out": [None, None],
        "settings": default_settings(),
        "created_at": now,
        "modified_at": now,
    }


def auto_map(paths):
    half_eye_words = ("entrecerr", "ojos medios", "medio cerrado", "half blink", "half eye", "semi cerr", "intermedio")
    eye_words = ("ojos cerrados", "ojo cerrado", "closed eyes", "eyes closed", "parpadeo", "blink", "ojos", "eyes")
    idle_words = ("reposo extra", "idle alt", "descanso", "bostezo")
    wide_w = ("muy", "wide", "grit", "shout", "big", "grande")
    mid_w = ("entre", "mid", "medi", "half", "semi", "interm")
    open_w = ("abiert", "open")
    closed_w = ("cerrad", "closed", "silen", "idle", "rest", "reposo")
    out, rest = {}, []
    for p in paths:
        n = Path(p).stem.lower().replace("_", " ").replace("-", " ")
        if any(w in n for w in idle_words) and "idle_alt" not in out:
            out["idle_alt"] = p
            continue
        half_eye = any(w in n for w in half_eye_words)
        for w in half_eye_words:
            n = n.replace(w, " ")
        blink = (not half_eye) and any(w in n for w in eye_words)
        for w in eye_words:
            n = n.replace(w, " ")
        mouth = next((m for m, ws in (("mid", mid_w), ("wide", wide_w), ("open", open_w), ("closed", closed_w))
                      if any(w in n for w in ws)), None)
        if half_eye:
            key = "half_" + (mouth if mouth in ("closed", "mid", "open") else "open" if mouth == "wide" else "closed")
        elif blink:
            key = "blink_" + (mouth if mouth in ("closed", "mid", "open", "wide") else "closed")
        else:
            key = mouth
        if key and key not in out:
            out[key] = p
        else:
            rest.append(p)
    for p, k in zip(rest, [k for k in ("closed", "mid", "open") if k not in out]):
        out[k] = p
    return out


def _migrate_scene(sc):
    base = new_scene(sc.get("name", "Escena"))
    for k, v in base.items():
        sc.setdefault(k, copy.deepcopy(v))
    for pr in sc.get("props", []):
        pbase = new_prop(pr.get("kind", "image"), pr.get("name"))
        for k, v in pbase.items():
            pr.setdefault(k, copy.deepcopy(v))
    return sc


def _migrate(ch, i):
    base = new_char(ch.get("name", f"P{i + 1}"), i)
    if "idle" in ch and "expressions" not in ch:
        ex = new_expression()
        ex["sprites"]["closed"], ex["sprites"]["open"] = ch.get("idle", ""), ch.get("talk", "")
        ch["expressions"] = [ex]
    for k, v in base.items():
        ch.setdefault(k, copy.deepcopy(v))
    ch["source"] = "mic"
    for ex in ch["expressions"]:
        sp = ex["sprites"]
        if sp.get("blink") and not sp.get("blink_closed"):
            sp["blink_closed"] = sp["blink"]
        for k, _, _ in SLOTS:
            sp.setdefault(k, "")
    for acc in ch.get("accessories", []):
        abase = new_accessory(acc.get("name", "Accesorio"))
        for k, v in abase.items():
            acc.setdefault(k, copy.deepcopy(v))
    if ch.get("device"):
        ch["device"] = ch["device"].split(" [")[0]
    ch["expr"] = min(ch["expr"], max(0, len(ch["expressions"]) - 1))
    return ch


def load_project(path=None):
    p = Path(path) if path else (PROJECT if PROJECT.exists() else LEGACY_PROJECT)
    data = new_empty_project(name=p.stem if p else "Nuevo Proyecto")
    if p and p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            data.update(loaded)
        except Exception:
            pass
    data["format"] = "setxd"
    data["version"] = 3
    data.setdefault("project_name", p.stem if p else "Nuevo Proyecto")
    data["canvas_w"] = max(320, min(7680, int(data.get("canvas_w", W))))
    data["canvas_h"] = max(320, min(4320, int(data.get("canvas_h", H))))
    data["fps"] = max(15, min(120, int(data.get("fps", 30))))
    data["characters"] = [_migrate(c, i) for i, c in enumerate(data.get("characters", []))]
    scenes = data.get("scenes") or [new_scene("Escena 1")]
    data["scenes"] = [_migrate_scene(s) for s in scenes]
    data.setdefault("timeline_clips", [])
    for cl in data["timeline_clips"]:
        cl.setdefault("fade_in", 0.0)
        cl.setdefault("fade_out", 0.0)
    data.setdefault("subtracks", [])
    data.setdefault("markers", [])
    data.setdefault("in_out", [None, None])
    st = default_settings()
    if isinstance(data.get("settings"), dict):
        for k, v in data["settings"].items():
            st[k] = v
    if not isinstance(st.get("mic_filters"), list):
        st["mic_filters"] = default_settings()["mic_filters"]
    data["settings"] = st
    return data


def save_project(data, path=None):
    p = Path(path) if path else PROJECT
    if p.suffix.lower() not in (".setxd", ".json"):
        p = p.with_suffix(".setxd")
    p.parent.mkdir(parents=True, exist_ok=True)
    data["format"] = "setxd"
    data["version"] = 3
    data["modified_at"] = time.strftime("%Y-%m-%d %H:%M")
    if not data.get("project_name"):
        data["project_name"] = p.stem
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    record_recent_project(p, data)
    return p


# =====================================================  Proyectos recientes
def _probe_project_meta(path):
    p = Path(path)
    suf = p.suffix.lower()
    is_rig = suf == ".vtr"
    is_3d = suf == ".vt3d"
    meta = {
        "path": str(p),
        "name": p.stem,
        "kind": "vtuber3d" if is_3d else ("vtuber" if is_rig else "video"),
        "w": 1080 if (is_rig or is_3d) else 1920,
        "h": 1080 if (is_rig or is_3d) else 1080,
        "fps": 60 if (is_rig or is_3d) else 30,
        "chars": 0,
        "layers": 0,
        "scenes": 1,
        "clips": 0,
        "modified": "",
    }
    try:
        st = p.stat()
        meta["modified"] = time.strftime("%d/%m/%Y %H:%M", time.localtime(st.st_mtime))
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("format") == "vt3d" or is_3d:
            meta["kind"] = "vtuber3d"
            meta["name"] = raw.get("name") or p.stem
            meta["fps"] = 60
        elif raw.get("format") == "vtr" or is_rig:
            meta["kind"] = "vtuber"
            meta["name"] = raw.get("name") or p.stem
            meta["w"] = int(raw.get("canvas_w", 1080))
            meta["h"] = int(raw.get("canvas_h", 1080))
            meta["fps"] = 60
            meta["layers"] = len(raw.get("layers", []))
        else:
            meta["name"] = raw.get("project_name") or p.stem
            meta["w"] = int(raw.get("canvas_w", 1920))
            meta["h"] = int(raw.get("canvas_h", 1080))
            meta["fps"] = int(raw.get("fps", 30))
            meta["kind"] = "tiktok" if meta["h"] > meta["w"] else "video"
            meta["chars"] = len(raw.get("characters", []))
            meta["scenes"] = len(raw.get("scenes", []))
            meta["clips"] = len(raw.get("timeline_clips", []))
    except Exception:
        pass
    return meta


def load_recent_projects():
    items = []
    if RECENT_FILE.exists():
        try:
            items = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
        except Exception:
            items = []
    seen = {str(Path(it["path"]).resolve()).lower() for it in items if isinstance(it, dict) and it.get("path")}
    cands = list(BASE.glob("*.setxd")) + list(BASE.glob("*.vtr")) + list(BASE.glob("*.vt3d"))
    for cand in sorted(cands, key=lambda x: x.stat().st_mtime, reverse=True):
        key = str(cand.resolve()).lower()
        if key not in seen:
            items.append(_probe_project_meta(cand))
            seen.add(key)
    valid = []
    for it in items:
        if not isinstance(it, dict) or not it.get("path"):
            continue
        p = Path(it["path"])
        if p.exists():
            valid.append(_probe_project_meta(p))
    return valid[:16]


def record_recent_project(path, data=None):
    p = Path(path).resolve()
    items = []
    if RECENT_FILE.exists():
        try:
            items = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
        except Exception:
            items = []
    key = str(p).lower()
    items = [it for it in items if isinstance(it, dict) and str(Path(it.get("path", "")).resolve()).lower() != key]
    meta = _probe_project_meta(p)
    if data:
        fmt = data.get("format") or ("vt3d" if p.suffix.lower() == ".vt3d" else ("vtr" if p.suffix.lower() == ".vtr" else "setxd"))
        if fmt == "vt3d":
            meta["kind"] = "vtuber3d"
            meta["name"] = data.get("name") or p.stem
            meta["w"] = 1080
            meta["h"] = 1080
            meta["fps"] = 60
        elif fmt == "vtr":
            meta["kind"] = "vtuber"
            meta["name"] = data.get("name") or p.stem
            meta["w"] = int(data.get("canvas_w", 1080))
            meta["h"] = int(data.get("canvas_h", 1080))
            meta["fps"] = 60
            meta["layers"] = len(data.get("layers", []))
        else:
            meta["name"] = data.get("project_name") or p.stem
            meta["w"] = int(data.get("canvas_w", 1920))
            meta["h"] = int(data.get("canvas_h", 1080))
            meta["fps"] = int(data.get("fps", 30))
            meta["kind"] = "tiktok" if meta["h"] > meta["w"] else "video"
            meta["chars"] = len(data.get("characters", []))
            meta["scenes"] = len(data.get("scenes", []))
            meta["clips"] = len(data.get("timeline_clips", []))
    items.insert(0, meta)
    try:
        RECENT_FILE.write_text(json.dumps(items[:14], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def remove_recent_project(path):
    if not RECENT_FILE.exists():
        return
    try:
        key = str(Path(path).resolve()).lower()
        items = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
        items = [it for it in items if isinstance(it, dict) and str(Path(it.get("path", "")).resolve()).lower() != key]
        RECENT_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
