"""Motor 3D en tiempo real para avatares VTuber 3D (.vt3d) vectorizado en NumPy + OpenCV:
- Render 3D real a 60 FPS con proyección perspectiva, cámara orbital 360°, sombreado Cel-Shading (Toon) y PBR Studio,
  Rim Light (luz de contorno), contorno ink opcional y canal Alfa real para OBS y exportación transparente.
- Avatar 3D articulado personalizable (4 presets, 5 peinados 3D, 4 atuendos, 6 accesorios toggle 1..6,
  física 3D de resortes en pelo/coletas/orejas/alas, mirada 3D, lip-sync y 7 emociones reactivas).
- Cargador nativo sin dependencias externas de modelos 3D externos (.vrm, .glb y .obj)."""
import copy
import json
import math
import random
import struct
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF

import model as M
from voice_emotion import VoiceEmotionAnalyzer

try:
    import cv2
except Exception:
    cv2 = None

VT3D_PRESETS = {
    "anime_studio": {
        "label": "Anime Studio",
        "hair_style": "long",
        "outfit_style": "hoodie",
        "hair_color": "#394B72",
        "eye_color": "#2F7CF6",
        "skin_color": "#FFE0CE",
        "outfit_color": "#232631",
        "accent_color": "#2F7CF6",
        "head_scale": 100,
        "eye_size": 102,
        "shoulder_width": 100,
        "accessories": {"1": False, "2": False, "3": True, "4": False, "5": True, "6": False},
    },
    "chibi_streamer": {
        "label": "Chibi Streamer",
        "hair_style": "twintails",
        "outfit_style": "hoodie",
        "hair_color": "#D96B8A",
        "eye_color": "#38BDF8",
        "skin_color": "#FFE7DA",
        "outfit_color": "#2B2435",
        "accent_color": "#F59E0B",
        "head_scale": 116,
        "eye_size": 116,
        "shoulder_width": 92,
        "accessories": {"1": False, "2": True, "3": False, "4": False, "5": True, "6": False},
    },
    "cyber_minimal": {
        "label": "Cyber Minimal",
        "hair_style": "short",
        "outfit_style": "jacket",
        "hair_color": "#8E9AAF",
        "eye_color": "#2EB67D",
        "skin_color": "#FCDCC8",
        "outfit_color": "#191C24",
        "accent_color": "#2EB67D",
        "head_scale": 98,
        "eye_size": 96,
        "shoulder_width": 108,
        "accessories": {"1": True, "2": False, "3": True, "4": False, "5": False, "6": False},
    },
    "cozy_pastel": {
        "label": "Cozy Cálido",
        "hair_style": "bob",
        "outfit_style": "turtleneck",
        "hair_color": "#8A5A3C",
        "eye_color": "#E5A00D",
        "skin_color": "#F8D4BC",
        "outfit_color": "#36312E",
        "accent_color": "#E5A00D",
        "head_scale": 102,
        "eye_size": 104,
        "shoulder_width": 96,
        "accessories": {"1": True, "2": False, "3": False, "4": True, "5": True, "6": False},
    },
}

HAIR_STYLES = [
    ("long", "Largo fluido"),
    ("twintails", "Coletas 3D (física)"),
    ("bob", "Bob moderno"),
    ("short", "Corto estilizado"),
    ("beanie", "Gorro / Beanie 3D"),
]

OUTFIT_STYLES = [
    ("hoodie", "Sudadera Studio"),
    ("jacket", "Chaqueta Tech"),
    ("turtleneck", "Cuello alto"),
    ("collar", "Camisa & Corbata"),
]

ACCESSORY_DEFS = [
    ("1", "glasses", "Gafas 3D"),
    ("2", "cat_ears", "Orejas 3D"),
    ("3", "headset", "Auriculares"),
    ("4", "halo", "Halo 3D"),
    ("5", "blush", "Rubor"),
    ("6", "wings", "Alas 3D"),
]


def new_vtuber3d(name="Mi VTuber 3D", preset="anime_studio"):
    p = VT3D_PRESETS.get(preset, VT3D_PRESETS["anime_studio"])
    now = time.strftime("%Y-%m-%d %H:%M")
    return {
        "format": "vt3d",
        "version": 1,
        "name": name,
        "preset": preset,
        "bg_type": "studio",        # "transparent" | "studio" | "chroma"
        "bg_color": "#14161D",
        "model_path": "",           # opcional: ruta .vrm / .glb / .obj externo
        "appearance": {
            "hair_style": p["hair_style"],
            "outfit_style": p["outfit_style"],
            "hair_color": p["hair_color"],
            "eye_color": p["eye_color"],
            "skin_color": p["skin_color"],
            "outfit_color": p["outfit_color"],
            "accent_color": p["accent_color"],
            "head_scale": p["head_scale"],
            "eye_size": p["eye_size"],
            "shoulder_width": p["shoulder_width"],
        },
        "accessories": dict(p["accessories"]),
        "lighting": {
            "shader": "toon",       # "toon" | "studio"
            "azimuth": 34,          # -180..180°
            "elevation": 32,        # -20..80°
            "rim_strength": 62,     # 0..100
            "ambient": 42,          # 15..75
            "outline": True,
        },
        "camera": {
            "yaw": 0.0,             # grados (-180..180)
            "pitch": 4.0,           # grados (-35..40)
            "distance": 3.18,       # 1.6..4.8
            "target_y": 0.28,       # -0.4..0.6
        },
        "tracking": {
            "mode": "voice",        # "voice" | "cam_voice" | "camera"
            "auto_emotion": True,
            "emotion_sensitivity": 68,
            "auto_reactions": True,
            "manual_emotion": "auto",
            "camera_index": 0,
        },
        "audio": {
            "device": None,
            "gain": 100,
            "threshold": 36,
            "range": 12,
            "hold": 85,
        },
        "created_at": now,
        "modified_at": now,
    }


def apply_vt3d_preset(data, preset_id):
    p = VT3D_PRESETS.get(preset_id)
    if not p:
        return
    data["preset"] = preset_id
    ap = data.setdefault("appearance", {})
    for k in ("hair_style", "outfit_style", "hair_color", "eye_color", "skin_color",
              "outfit_color", "accent_color", "head_scale", "eye_size", "shoulder_width"):
        ap[k] = p[k]
    data["accessories"] = dict(p["accessories"])
    data["model_path"] = ""


def load_vtuber3d(path):
    p = Path(path)
    data = new_vtuber3d(p.stem)
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            for k, v in loaded.items():
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    data[k].update(v)
                else:
                    data[k] = v
        except Exception:
            pass
    data["format"] = "vt3d"
    data["version"] = 1
    return data


def save_vtuber3d(data, path):
    p = Path(path)
    if p.suffix.lower() != ".vt3d":
        p = p.with_suffix(".vt3d")
    p.parent.mkdir(parents=True, exist_ok=True)
    data["format"] = "vt3d"
    data["version"] = 1
    data["modified_at"] = time.strftime("%Y-%m-%d %H:%M")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    M.record_recent_project(p, data)
    return p


# =====================================================  Utilidades matemáticas 3D (NumPy)
def _hex_rgb(hx, default=(0.5, 0.5, 0.6)):
    c = QColor(hx)
    if not c.isValid():
        return np.array(default, dtype=np.float32)
    return np.array([c.red() / 255.0, c.green() / 255.0, c.blue() / 255.0], dtype=np.float32)


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float32)


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float32)


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)


def _euler_mat(yaw_deg, pitch_deg, roll_deg):
    return _rot_y(math.radians(yaw_deg)) @ _rot_x(math.radians(pitch_deg)) @ _rot_z(math.radians(roll_deg))


# =====================================================  Generadores de mallas 3D paramétricas limpias
def _make_uv_sphere(rx, ry, rz, segs=18, rings=12, center=(0, 0, 0), deform_fn=None):
    verts = []
    for i in range(rings + 1):
        phi = math.pi * i / rings
        sp, cp = math.sin(phi), math.cos(phi)
        for j in range(segs):
            th = math.tau * j / segs
            x = rx * sp * math.sin(th)
            y = ry * cp
            z = rz * sp * math.cos(th)
            if deform_fn is not None:
                x, y, z = deform_fn(x, y, z, phi, th)
            verts.append((x + center[0], y + center[1], z + center[2]))
    tris = []
    for i in range(rings):
        r0, r1 = i * segs, (i + 1) * segs
        for j in range(segs):
            jn = (j + 1) % segs
            v00, v01 = r0 + j, r0 + jn
            v10, v11 = r1 + j, r1 + jn
            if i > 0:
                tris.append((v00, v10, v01))
            if i < rings - 1:
                tris.append((v01, v10, v11))
    return np.array(verts, dtype=np.float32), np.array(tris, dtype=np.int32)


def _make_tapered_cyl(r_bot, r_top, h, segs=16, rings=5, center=(0, 0, 0), sx=1.0, sz=1.0, cap_top=True, cap_bot=True):
    verts = []
    for i in range(rings + 1):
        t = i / rings
        r = r_bot * (1.0 - t) + r_top * t
        y = -h * 0.5 + h * t
        for j in range(segs):
            th = math.tau * j / segs
            verts.append((r * sx * math.sin(th) + center[0], y + center[1], r * sz * math.cos(th) + center[2]))
    tris = []
    for i in range(rings):
        r0, r1 = i * segs, (i + 1) * segs
        for j in range(segs):
            jn = (j + 1) % segs
            tris.append((r0 + j, r0 + jn, r1 + j))
            tris.append((r0 + jn, r1 + jn, r1 + j))
    if cap_top and r_top > 1e-4:
        c_idx = len(verts)
        verts.append((center[0], center[1] + h * 0.5, center[2]))
        r_top_i = rings * segs
        for j in range(segs):
            tris.append((c_idx, r_top_i + j, r_top_i + (j + 1) % segs))
    if cap_bot and r_bot > 1e-4:
        c_idx = len(verts)
        verts.append((center[0], center[1] - h * 0.5, center[2]))
        for j in range(segs):
            tris.append((c_idx, (j + 1) % segs, j))
    return np.array(verts, dtype=np.float32), np.array(tris, dtype=np.int32)


def _make_torus(R, r, segs=22, tube=8, center=(0, 0, 0), tilt_x=0.0):
    verts = []
    rm = _rot_x(math.radians(tilt_x)) if abs(tilt_x) > 1e-3 else np.eye(3, dtype=np.float32)
    for i in range(segs):
        u = math.tau * i / segs
        cu, su = math.cos(u), math.sin(u)
        for j in range(tube):
            v = math.tau * j / tube
            cv, sv = math.cos(v), math.sin(v)
            p = np.array([(R + r * cv) * cu, r * sv, (R + r * cv) * su], dtype=np.float32)
            p = rm @ p
            verts.append((p[0] + center[0], p[1] + center[1], p[2] + center[2]))
    tris = []
    for i in range(segs):
        in_ = (i + 1) % segs
        for j in range(tube):
            jn = (j + 1) % tube
            a, b = i * tube + j, in_ * tube + j
            c, d = in_ * tube + jn, i * tube + jn
            tris.append((a, b, c))
            tris.append((a, c, d))
    return np.array(verts, dtype=np.float32), np.array(tris, dtype=np.int32)


def _make_box(w, h, d, center=(0, 0, 0)):
    hw, hh, hd = w * 0.5, h * 0.5, d * 0.5
    cx, cy, cz = center
    v = np.array([
        [cx - hw, cy - hh, cz + hd], [cx + hw, cy - hh, cz + hd],
        [cx + hw, cy + hh, cz + hd], [cx - hw, cy + hh, cz + hd],
        [cx - hw, cy - hh, cz - hd], [cx + hw, cy - hh, cz - hd],
        [cx + hw, cy + hh, cz - hd], [cx - hw, cy + hh, cz - hd],
    ], dtype=np.float32)
    t = np.array([
        [0, 1, 2], [0, 2, 3], [5, 4, 7], [5, 7, 6],
        [4, 0, 3], [4, 3, 7], [1, 5, 6], [1, 6, 2],
        [3, 2, 6], [3, 6, 7], [4, 5, 1], [4, 1, 0],
    ], dtype=np.int32)
    return v, t


def _make_hair_lock(length, width, thickness, curve_z=-0.06, center=(0, 0, 0), rot_y=0.0, rot_z=0.0, segs_y=6):
    """Mechón 3D esculpido con punta afilada y volumen prismático suave."""
    rings = []
    for i in range(segs_y + 1):
        t = i / segs_y
        # Ancho máximo a 30% y afinado elegante hasta la punta
        w_t = width * (0.72 + 0.35 * math.sin(min(1.0, t * 1.45) * math.pi * 0.5)) * (1.0 - t ** 1.65)
        th_t = thickness * (1.0 - t * 0.78)
        y = -t * length
        z = curve_z * (t ** 1.35)
        w_t = max(0.005, w_t)
        th_t = max(0.004, th_t)
        rings.append([
            (0.0, y, z + th_t),
            (w_t, y, z),
            (0.0, y, z - th_t * 0.75),
            (-w_t, y, z),
        ])
    verts = []
    for r in rings:
        verts.extend(r)
    tris = []
    for i in range(segs_y):
        r0, r1 = i * 4, (i + 1) * 4
        for j in range(4):
            jn = (j + 1) % 4
            tris.append((r0 + j, r1 + j, r0 + jn))
            tris.append((r0 + jn, r1 + j, r1 + jn))
    v = np.array(verts, dtype=np.float32)
    rm = _rot_y(math.radians(rot_y)) @ _rot_z(math.radians(rot_z))
    v = (v @ rm.T) + np.array(center, dtype=np.float32)
    return v, np.array(tris, dtype=np.int32)


def _merge_parts(parts):
    """Combina [(verts, tris, rgb_float3, spec_float, layer_tag)] en un bloque compacto por hueso."""
    if not parts:
        return None
    vs, ts, cs, ss = [], [], [], []
    off = 0
    for v, t, col, spec in parts:
        vs.append(v)
        ts.append(t + off)
        n_t = len(t)
        cs.append(np.broadcast_to(np.asarray(col, dtype=np.float32), (n_t, 3)).copy())
        ss.append(np.full(n_t, float(spec), dtype=np.float32))
        off += len(v)
    return {
        "verts": np.vstack(vs).astype(np.float32),
        "tris": np.vstack(ts).astype(np.int32),
        "colors": np.vstack(cs).astype(np.float32),
        "spec": np.concatenate(ss).astype(np.float32),
    }


# =====================================================  Cargador nativo .OBJ / .GLB / .VRM (NumPy puro)
_EXTERNAL_MESH_CACHE = {}
_PROCEDURAL_RIG_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _decimate_mesh_if_heavy(verts, tris, colors, max_tris=6500):
    if len(tris) <= max_tris:
        return verts, tris, colors
    step = max(1, len(tris) // max_tris)
    tris_s = tris[::step]
    cols_s = colors[::step]
    return verts, tris_s, cols_s


def load_external_3d_model(path):
    """Carga un archivo .obj, .glb o .vrm en un bloque 3D centrado y escalado al estudio (sin dependencias)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return None
    ck = (str(p.resolve()), mtime)
    with _CACHE_LOCK:
        if ck in _EXTERNAL_MESH_CACHE:
            return _EXTERNAL_MESH_CACHE[ck]

    ext = p.suffix.lower()
    mesh = None
    try:
        if ext == ".obj":
            mesh = _parse_obj(p)
        elif ext in (".glb", ".vrm"):
            mesh = _parse_glb_vrm(p)
    except Exception:
        mesh = None

    if mesh is not None and len(mesh["verts"]) > 0 and len(mesh["tris"]) > 0:
        v = mesh["verts"]
        vmin, vmax = v.min(axis=0), v.max(axis=0)
        size = vmax - vmin
        max_dim = float(max(size[0], size[1], size[2], 1e-4))
        center = (vmin + vmax) * 0.5
        # Normalizar para que el modelo encaje cómodamente en altura ~1.85 unidades de estudio
        v = (v - center) * (1.85 / max_dim)
        v[:, 1] += 0.12
        v, t, c = _decimate_mesh_if_heavy(v, mesh["tris"], mesh["colors"])
        mesh = {
            "verts": v.astype(np.float32),
            "tris": t.astype(np.int32),
            "colors": c.astype(np.float32),
            "spec": np.full(len(t), 0.18, dtype=np.float32),
            "name": p.stem,
        }
        with _CACHE_LOCK:
            if len(_EXTERNAL_MESH_CACHE) > 12:
                _EXTERNAL_MESH_CACHE.pop(next(iter(_EXTERNAL_MESH_CACHE)))
            _EXTERNAL_MESH_CACHE[ck] = mesh
    return mesh


def _parse_obj(p):
    verts, tris, cols = [], [], []
    cur_col = np.array([0.78, 0.80, 0.86], dtype=np.float32)
    mtl_colors = {}

    text = p.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        cmd = parts[0]
        if cmd == "mtllib" and len(parts) >= 2:
            mp = p.parent / " ".join(parts[1:])
            if mp.exists():
                m_cur = None
                for ml in mp.read_text(encoding="utf-8", errors="ignore").splitlines():
                    ms = ml.strip().split()
                    if not ms:
                        continue
                    if ms[0] == "newmtl" and len(ms) >= 2:
                        m_cur = ms[1]
                    elif ms[0] == "Kd" and len(ms) >= 4 and m_cur:
                        try:
                            mtl_colors[m_cur] = np.array([float(ms[1]), float(ms[2]), float(ms[3])], dtype=np.float32)
                        except ValueError:
                            pass
        elif cmd == "usemtl" and len(parts) >= 2:
            if parts[1] in mtl_colors:
                cur_col = mtl_colors[parts[1]]
        elif cmd == "v" and len(parts) >= 4:
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                pass
        elif cmd == "f" and len(parts) >= 4:
            idxs = []
            for tok in parts[1:]:
                vi = int(tok.split("/")[0])
                idxs.append(vi - 1 if vi > 0 else len(verts) + vi)
            for k in range(1, len(idxs) - 1):
                tris.append((idxs[0], idxs[k], idxs[k + 1]))
                cols.append(cur_col)
    if not verts or not tris:
        return None
    return {
        "verts": np.array(verts, dtype=np.float32),
        "tris": np.array(tris, dtype=np.int32),
        "colors": np.array(cols, dtype=np.float32),
    }


def _parse_glb_vrm(p):
    """Lector ligero de glTF 2.0 binario (.glb / .vrm) en Python + NumPy puro."""
    raw = p.read_bytes()
    if len(raw) < 20 or raw[:4] != b"glTF":
        return None
    pos = 12
    gltf_json = None
    bin_blob = b""
    while pos + 8 <= len(raw):
        clen, ctype = struct.unpack_from("<II", raw, pos)
        pos += 8
        chunk = raw[pos:pos + clen]
        pos += clen
        if ctype == 0x4E4F534A:      # JSON
            gltf_json = json.loads(chunk.decode("utf-8", errors="ignore"))
        elif ctype == 0x004E4942:    # BIN
            bin_blob = chunk
    if not gltf_json or not bin_blob:
        return None

    accessors = gltf_json.get("accessors", [])
    views = gltf_json.get("bufferViews", [])
    mats = gltf_json.get("materials", [])
    comp_dt = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    type_n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def read_acc(idx):
        acc = accessors[idx]
        bv = views[acc["bufferView"]]
        dt = comp_dt.get(acc["componentType"], np.float32)
        nc = type_n.get(acc["type"], 1)
        count = int(acc["count"])
        off = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
        stride = int(bv.get("byteStride", 0))
        item_bytes = np.dtype(dt).itemsize * nc
        if stride and stride > item_bytes:
            out = np.empty((count, nc), dtype=dt)
            for k in range(count):
                out[k] = np.frombuffer(bin_blob, dtype=dt, count=nc, offset=off + k * stride)
            return out if nc > 1 else out.ravel()
        arr = np.frombuffer(bin_blob, dtype=dt, count=count * nc, offset=off)
        return arr.reshape(count, nc) if nc > 1 else arr

    all_v, all_t, all_c = [], [], []
    v_off = 0
    for mesh in gltf_json.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            pos_acc = prim.get("attributes", {}).get("POSITION")
            if pos_acc is None:
                continue
            v = read_acc(pos_acc).astype(np.float32)
            if "indices" in prim:
                ind = read_acc(prim["indices"]).astype(np.int32)
                n_tri = len(ind) // 3
                t = ind[:n_tri * 3].reshape(n_tri, 3)
            else:
                n_tri = len(v) // 3
                t = np.arange(n_tri * 3, dtype=np.int32).reshape(n_tri, 3)
            if len(t) == 0:
                continue
            col = np.array([0.82, 0.84, 0.90], dtype=np.float32)
            mi = prim.get("material")
            if mi is not None and 0 <= mi < len(mats):
                pbr = mats[mi].get("pbrMetallicRoughness", {})
                bf = pbr.get("baseColorFactor")
                if bf and len(bf) >= 3:
                    col = np.array(bf[:3], dtype=np.float32)
            all_v.append(v)
            all_t.append(t + v_off)
            all_c.append(np.broadcast_to(col, (len(t), 3)).copy())
            v_off += len(v)
    if not all_v:
        return None
    return {
        "verts": np.vstack(all_v),
        "tris": np.vstack(all_t),
        "colors": np.vstack(all_c),
    }


# =====================================================  Constructor de Avatar 3D Articulado
def _build_procedural_3d_rig(data):
    """Construye y cachea las submallas esqueléticas 3D del avatar personalizable."""
    ap = data.get("appearance", {})
    acc = data.get("accessories", {})
    key = (
        ap.get("hair_style", "long"), ap.get("outfit_style", "hoodie"),
        ap.get("hair_color", "#394B72"), ap.get("eye_color", "#2F7CF6"),
        ap.get("skin_color", "#FFE0CE"), ap.get("outfit_color", "#232631"),
        ap.get("accent_color", "#2F7CF6"), int(ap.get("head_scale", 100)),
        int(ap.get("shoulder_width", 100)),
        tuple(sorted((k, bool(v)) for k, v in acc.items())),
    )
    with _CACHE_LOCK:
        if key in _PROCEDURAL_RIG_CACHE:
            return _PROCEDURAL_RIG_CACHE[key]

    c_hair = _hex_rgb(ap.get("hair_color", "#394B72"), (0.22, 0.29, 0.45))
    c_hair_d = np.clip(c_hair * 0.78, 0.0, 1.0)
    c_hair_l = np.clip(c_hair * 1.16 + 0.04, 0.0, 1.0)
    c_skin = _hex_rgb(ap.get("skin_color", "#FFE0CE"), (1.0, 0.88, 0.81))
    c_skin_sh = np.clip(c_skin * 0.88, 0.0, 1.0)
    c_outfit = _hex_rgb(ap.get("outfit_color", "#232631"), (0.14, 0.15, 0.19))
    c_outfit_l = np.clip(c_outfit * 1.32 + 0.04, 0.0, 1.0)
    c_accent = _hex_rgb(ap.get("accent_color", "#2F7CF6"), (0.18, 0.49, 0.96))
    c_white = np.array([0.92, 0.93, 0.96], dtype=np.float32)
    c_dark = np.array([0.11, 0.12, 0.15], dtype=np.float32)
    c_pink = np.array([0.95, 0.68, 0.74], dtype=np.float32)
    c_gold = np.array([0.96, 0.75, 0.25], dtype=np.float32)

    hs = float(ap.get("head_scale", 100)) / 100.0
    sw = float(ap.get("shoulder_width", 100)) / 100.0
    hstyle = ap.get("hair_style", "long")
    ostyle = ap.get("outfit_style", "hoodie")

    # ---- 1. Hueso TORSO (espacio local: origen en base del torso y = -0.70) ----
    torso_parts = []
    # Cuello esculpido
    v, t = _make_tapered_cyl(0.115, 0.105, 0.24, segs=14, rings=3, center=(0, 0.60, -0.01))
    torso_parts.append((v, t, c_skin, 0.06))
    # Pecho / torso con forma orgánica
    def _torso_deform(x, y, z, phi, th):
        if y < 0:
            x *= 0.94 + 0.10 * (-y / 0.48)
        return x, y, z
    v, t = _make_uv_sphere(0.36 * sw, 0.46, 0.24, segs=14, rings=8, center=(0, 0.14, 0.0), deform_fn=_torso_deform)
    torso_parts.append((v, t, c_outfit, 0.08))
    # Hombros y mangas 3D
    for side in (-1.0, 1.0):
        v, t = _make_uv_sphere(0.145 * sw, 0.14, 0.145, segs=10, rings=6, center=(side * 0.34 * sw, 0.37, -0.01))
        torso_parts.append((v, t, c_outfit_l, 0.08))
        v, t = _make_tapered_cyl(0.115, 0.135, 0.50, segs=10, rings=2, center=(side * 0.40 * sw, 0.12, -0.01))
        # Inclinar suavemente brazo hacia afuera
        rm = _rot_z(math.radians(-side * 10.0))
        c0 = np.array([side * 0.40 * sw, 0.35, -0.01], dtype=np.float32)
        v = ((v - c0) @ rm.T) + c0
        torso_parts.append((v, t, c_outfit, 0.07))

    # Detalles según estilo de ropa
    if ostyle == "hoodie":
        # Cuello de capucha acolchado + cordones + acento frontal
        v, t = _make_torus(0.21, 0.065, segs=18, tube=8, center=(0, 0.48, -0.02), tilt_x=16)
        torso_parts.append((v, t, c_outfit_l, 0.10))
        v, t = _make_box(0.036, 0.52, 0.03, center=(0, 0.18, 0.228))
        torso_parts.append((v, t, c_accent, 0.25))
        for side in (-1.0, 1.0):
            v, t = _make_tapered_cyl(0.010, 0.010, 0.22, segs=6, rings=1, center=(side * 0.075, 0.34, 0.235))
            torso_parts.append((v, t, c_white, 0.15))
    elif ostyle == "jacket":
        # Camiseta interior clara + solapas tech con ribete de acento
        v, t = _make_tapered_cyl(0.15, 0.18, 0.44, segs=12, rings=2, center=(0, 0.24, 0.09), sx=0.95, sz=0.75)
        torso_parts.append((v, t, c_white, 0.05))
        for side in (-1.0, 1.0):
            v, t = _make_box(0.09, 0.34, 0.05, center=(side * 0.14, 0.32, 0.21))
            rm = _rot_z(math.radians(side * 16))
            c0 = np.array([side * 0.14, 0.32, 0.21], dtype=np.float32)
            v = ((v - c0) @ rm.T) + c0
            torso_parts.append((v, t, c_accent, 0.28))
    elif ostyle == "turtleneck":
        v, t = _make_tapered_cyl(0.145, 0.135, 0.16, segs=16, rings=3, center=(0, 0.55, -0.005))
        torso_parts.append((v, t, c_outfit_l, 0.08))
        v, t = _make_torus(0.24, 0.025, segs=18, tube=6, center=(0, 0.44, 0.02), tilt_x=18)
        torso_parts.append((v, t, c_accent, 0.30))
    else:  # collar
        v, t = _make_tapered_cyl(0.16, 0.15, 0.09, segs=14, rings=2, center=(0, 0.50, 0.01))
        torso_parts.append((v, t, c_white, 0.06))
        v, t = _make_box(0.055, 0.36, 0.03, center=(0, 0.28, 0.235))
        torso_parts.append((v, t, c_accent, 0.25))

    # ---- 2. Hueso CABEZA (origen en pivote de cuello; centro del cráneo en y = +0.44 * hs) ----
    head_parts = []
    head_front_parts = []  # flequillo frontal + gafas + micro boom (render después de rasgos faciales!)
    hy = 0.44 * hs
    hr = 0.40 * hs

    # Cráneo anime esculpido (barbilla fina en V suave, pómulos redondeados)
    def _head_deform(x, y, z, phi, th):
        yn = y / max(1e-4, hr * 1.06)
        if yn < 0.0:
            # Afinar barbilla suavemente hacia abajo
            taper = 1.0 - 0.36 * (abs(yn) ** 1.45)
            x *= taper
            z *= (0.98 - 0.16 * abs(yn))
            if z > 0:
                z *= 1.04
        # Pequeña proyección nasal sutil en el frente
        if z > 0 and abs(x) < hr * 0.14 and -hr * 0.20 < y < hr * 0.05:
            z += hr * 0.045 * (1.0 - abs(x) / (hr * 0.14)) * (1.0 - abs(y + hr * 0.07) / (hr * 0.14))
        return x, y, z

    v, t = _make_uv_sphere(hr * 0.96, hr * 1.06, hr * 0.92, segs=20, rings=12, center=(0, hy, 0), deform_fn=_head_deform)
    head_parts.append((v, t, c_skin, 0.06))

    # Orejas humanas laterales
    for side in (-1.0, 1.0):
        v, t = _make_uv_sphere(0.055 * hs, 0.095 * hs, 0.045 * hs, segs=8, rings=5,
                               center=(side * hr * 0.94, hy - 0.03 * hs, -0.03 * hs))
        head_parts.append((v, t, c_skin_sh, 0.04))

    # Casquete superior del cabello (cubre coronilla y parte posterior superior)
    def _crown_deform(x, y, z, phi, th):
        # Subir el borde frontal para descubrir la cara
        if z > 0.08 * hr and y < 0.18 * hr:
            y = 0.18 * hr + (y - 0.18 * hr) * 0.18
        return x, y, z

    v, t = _make_uv_sphere(hr * 1.08, hr * 1.12, hr * 1.06, segs=15, rings=9,
                           center=(0, hy + 0.05 * hs, -0.04 * hs), deform_fn=_crown_deform)
    head_parts.append((v, t, c_hair, 0.28))

    # Flequillos frontales 3D (van en head_front_parts para quedar delante de las cejas/frente!)
    bang_specs = [
        (-0.22, 0.34, 0.34, 0.34, 0.095, 0.042, 12, -16),
        (-0.10, 0.38, 0.38, 0.30, 0.088, 0.040, 6, -6),
        (0.01,  0.39, 0.39, 0.28, 0.085, 0.040, 0, 2),
        (0.12,  0.38, 0.38, 0.31, 0.088, 0.040, -6, 8),
        (0.23,  0.34, 0.34, 0.35, 0.095, 0.042, -12, 16),
    ]
    for bx, by, bz, blen, bw, bth, ry, rz in bang_specs:
        v, t = _make_hair_lock(blen * hs, bw * hs, bth * hs, curve_z=0.04 * hs,
                               center=(bx * hs, hy + by * hs, bz * hs), rot_y=ry, rot_z=rz, segs_y=5)
        head_front_parts.append((v, t, c_hair_l if abs(bx) < 0.12 else c_hair, 0.32))

    # Mechones laterales que enmarcan el rostro
    side_len = {"long": 0.72, "twintails": 0.56, "bob": 0.52, "short": 0.38, "beanie": 0.44}.get(hstyle, 0.56)
    for side in (-1.0, 1.0):
        v, t = _make_hair_lock(side_len * hs, 0.115 * hs, 0.055 * hs, curve_z=-0.03 * hs,
                               center=(side * 0.38 * hs, hy + 0.16 * hs, 0.14 * hs),
                               rot_y=side * 22, rot_z=-side * 6, segs_y=6)
        head_front_parts.append((v, t, c_hair, 0.25))

    # Estilo Gorro / Beanie 3D
    if hstyle == "beanie":
        v, t = _make_uv_sphere(hr * 1.15, hr * 0.85, hr * 1.12, segs=18, rings=10,
                               center=(0, hy + 0.24 * hs, -0.04 * hs))
        head_front_parts.append((v, t, c_accent, 0.12))
        v, t = _make_torus(hr * 1.08, 0.068 * hs, segs=20, tube=8, center=(0, hy + 0.20 * hs, -0.02 * hs), tilt_x=-8)
        head_front_parts.append((v, t, c_outfit_l, 0.10))

    # ---- 3. Huesos de CABELLO TRASERO / COLETAS con física 3D de péndulo ----
    hair_back_parts = []
    tail_l_parts = []
    tail_r_parts = []
    if hstyle == "long":
        for i, ax in enumerate((-0.28, -0.14, 0.0, 0.14, 0.28)):
            llen = (0.98 - abs(ax) * 0.45) * hs
            v, t = _make_hair_lock(llen, 0.175 * hs, 0.085 * hs, curve_z=-0.12 * hs,
                                   center=(ax * hs, 0.0, -0.10 * hs - abs(ax) * 0.08),
                                   rot_y=-ax * 40, rot_z=-ax * 14, segs_y=7)
            hair_back_parts.append((v, t, c_hair_d if i % 2 == 0 else c_hair, 0.22))
    elif hstyle == "bob":
        for ax in (-0.26, -0.13, 0.0, 0.13, 0.26):
            v, t = _make_hair_lock(0.56 * hs, 0.16 * hs, 0.085 * hs, curve_z=-0.04 * hs,
                                   center=(ax * hs, 0.0, -0.12 * hs), rot_y=-ax * 35, rot_z=-ax * 10, segs_y=5)
            hair_back_parts.append((v, t, c_hair_d, 0.22))
    elif hstyle == "twintails":
        # Cabello trasero corto + dos coletas 3D independientes con lazos de acento!
        for ax in (-0.16, 0.0, 0.16):
            v, t = _make_hair_lock(0.36 * hs, 0.15 * hs, 0.075 * hs, curve_z=-0.06 * hs,
                                   center=(ax * hs, 0.0, -0.10 * hs), segs_y=4)
            hair_back_parts.append((v, t, c_hair_d, 0.20))
        for side, bucket in ((-1.0, tail_l_parts), (1.0, tail_r_parts)):
            v, t = _make_torus(0.085 * hs, 0.032 * hs, segs=12, tube=6, center=(0, 0, 0), tilt_x=70)
            bucket.append((v, t, c_accent, 0.30))
            v, t = _make_hair_lock(0.95 * hs, 0.165 * hs, 0.11 * hs, curve_z=-0.14 * hs,
                                   center=(side * 0.05 * hs, -0.02 * hs, 0.0), rot_z=-side * 16, segs_y=7)
            bucket.append((v, t, c_hair, 0.28))
            v, t = _make_hair_lock(0.82 * hs, 0.11 * hs, 0.08 * hs, curve_z=-0.08 * hs,
                                   center=(side * 0.11 * hs, -0.04 * hs, 0.05 * hs), rot_z=-side * 24, segs_y=6)
            bucket.append((v, t, c_hair_l, 0.30))
    else:  # short / beanie
        for ax in (-0.18, 0.0, 0.18):
            v, t = _make_hair_lock(0.32 * hs, 0.14 * hs, 0.07 * hs, curve_z=-0.05 * hs,
                                   center=(ax * hs, 0.0, -0.10 * hs), segs_y=4)
            hair_back_parts.append((v, t, c_hair_d, 0.20))

    # ---- 4. Accesorios 3D Toggleables (teclas 1..6) ----
    # 1: Gafas 3D
    if acc.get("1", False):
        gy = hy + 0.01 * hs
        gz = hr * 0.94
        for side in (-1.0, 1.0):
            v, t = _make_torus(0.115 * hs, 0.016 * hs, segs=16, tube=6,
                               center=(side * 0.155 * hs, gy, gz), tilt_x=90)
            head_front_parts.append((v, t, c_dark, 0.45))
            # Patilla hacia la oreja
            v, t = _make_box(0.018 * hs, 0.018 * hs, 0.36 * hs,
                             center=(side * 0.275 * hs, gy + 0.01 * hs, gz - 0.18 * hs))
            head_front_parts.append((v, t, c_dark, 0.35))
        v, t = _make_box(0.09 * hs, 0.018 * hs, 0.02 * hs, center=(0, gy + 0.015 * hs, gz))
        head_front_parts.append((v, t, c_dark, 0.45))

    # 2: Orejas de gato 3D (con hueso spring propio para rebotar!)
    ears_parts = []
    if acc.get("2", False):
        for side in (-1.0, 1.0):
            v, t = _make_tapered_cyl(0.12 * hs, 0.012 * hs, 0.26 * hs, segs=4, rings=2, center=(0, 0.13 * hs, 0), sx=1.05, sz=0.62)
            rm = _rot_z(math.radians(-side * 24)) @ _rot_y(math.radians(side * 16))
            v = (v @ rm.T) + np.array([side * 0.24 * hs, 0.0, 0.02 * hs], dtype=np.float32)
            ears_parts.append((v, t, c_hair, 0.22))
            v2, t2 = _make_tapered_cyl(0.068 * hs, 0.008 * hs, 0.17 * hs, segs=3, rings=1, center=(0, 0.10 * hs, 0.036 * hs), sx=1.0, sz=0.45)
            v2 = (v2 @ rm.T) + np.array([side * 0.24 * hs, 0.0, 0.02 * hs], dtype=np.float32)
            ears_parts.append((v2, t2, c_pink, 0.10))

    # 3: Auriculares Podcast / Streamer 3D
    if acc.get("3", False):
        # Diadema superior en arco
        segs_a = 14
        for i in range(segs_a):
            a0 = math.radians(-82 + i * (164.0 / segs_a))
            ax = math.sin(a0) * hr * 1.14
            ay = hy + math.cos(a0) * hr * 1.18 + 0.02 * hs
            v, t = _make_box(0.065 * hs, 0.042 * hs, 0.085 * hs, center=(ax, ay, -0.02 * hs))
            head_front_parts.append((v, t, c_dark, 0.35))
        for side in (-1.0, 1.0):
            v, t = _make_tapered_cyl(0.125 * hs, 0.125 * hs, 0.095 * hs, segs=14, rings=2, center=(0, 0, 0))
            v = (v @ _rot_z(math.radians(90)).T) + np.array([side * hr * 1.07, hy - 0.02 * hs, -0.02 * hs], dtype=np.float32)
            head_front_parts.append((v, t, c_dark, 0.38))
            v2, t2 = _make_torus(0.085 * hs, 0.018 * hs, segs=14, tube=6, center=(0, 0, 0))
            v2 = (v2 @ _rot_z(math.radians(90)).T) + np.array([side * hr * 1.13, hy - 0.02 * hs, -0.02 * hs], dtype=np.float32)
            head_front_parts.append((v2, t2, c_accent, 0.55))
        # Brazo de micrófono boom lado izquierdo
        v, t = _make_tapered_cyl(0.012 * hs, 0.012 * hs, 0.30 * hs, segs=6, rings=2, center=(0, 0, 0))
        v = (v @ (_rot_x(math.radians(78)) @ _rot_z(math.radians(32))).T) + np.array([-hr * 0.85, hy - 0.14 * hs, 0.16 * hs], dtype=np.float32)
        head_front_parts.append((v, t, c_dark, 0.30))
        v, t = _make_uv_sphere(0.036 * hs, 0.030 * hs, 0.044 * hs, segs=8, rings=6, center=(-hr * 0.42, hy - 0.21 * hs, hr * 0.94))
        head_front_parts.append((v, t, c_dark, 0.12))

    # 4: Halo 3D flotante (hueso propio que flota y gira suavemente)
    halo_parts = []
    if acc.get("4", False):
        v, t = _make_torus(0.30 * hs, 0.028 * hs, segs=24, tube=8, center=(0, 0, 0), tilt_x=10)
        halo_parts.append((v, t, c_gold, 0.65))

    # 6: Alas 3D estilizadas (huesos izq/der que aletean detrás del torso)
    wing_l_parts, wing_r_parts = [], []
    if acc.get("6", False):
        for side, bucket in ((-1.0, wing_l_parts), (1.0, wing_r_parts)):
            for k, (dy, ang, ln, wd) in enumerate(((0.12, -26, 0.58, 0.12), (0.02, -40, 0.48, 0.10), (-0.07, -56, 0.36, 0.085))):
                v, t = _make_hair_lock(ln, wd, 0.03, curve_z=-0.06, center=(0, 0, 0), rot_z=side * (90 + ang), segs_y=4)
                v += np.array([side * 0.06, dy, 0.0], dtype=np.float32)
                bucket.append((v, t, c_white if k == 0 else c_accent, 0.32))

    rig_meshes = {
        "torso": _merge_parts(torso_parts),
        "head": _merge_parts(head_parts),
        "head_front": _merge_parts(head_front_parts),
        "hair_back": _merge_parts(hair_back_parts),
        "tail_l": _merge_parts(tail_l_parts),
        "tail_r": _merge_parts(tail_r_parts),
        "ears": _merge_parts(ears_parts),
        "halo": _merge_parts(halo_parts),
        "wing_l": _merge_parts(wing_l_parts),
        "wing_r": _merge_parts(wing_r_parts),
        "head_r": hr,
        "head_y": hy,
        "head_s": hs,
    }
    with _CACHE_LOCK:
        if len(_PROCEDURAL_RIG_CACHE) > 20:
            _PROCEDURAL_RIG_CACHE.pop(next(iter(_PROCEDURAL_RIG_CACHE)))
        _PROCEDURAL_RIG_CACHE[key] = rig_meshes
    return rig_meshes


# =====================================================  Estado dinámico 3D (Física + Emociones + Lip-Sync)
class VTuber3DState:
    """Estado animado en tiempo real para el avatar 3D (cabeza 3D, pelo spring-bone, mirada, boca y emociones)."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.mouth_state = 0
        self.mouth_open = 0.0
        self.hold_until = 0.0
        self.step_at = 0.0
        self.blink_at = 0.0
        self.blink_start = 0.0
        self.blink_end = 0.0
        self.blink_amt = 0.0

        # Pose 3D de cabeza (grados) y mirada (-1..1)
        self.head_yaw = 0.0
        self.head_pitch = 0.0
        self.head_roll = 0.0
        self.prev_yaw = 0.0
        self.prev_pitch = 0.0
        self.body_yaw = 0.0
        self.body_bob = 0.0
        self.gaze_x = 0.0
        self.gaze_y = 0.0

        # Spring-bones 3D: [val, vel]
        self.hair_pitch = [0.0, 0.0]
        self.hair_roll = [0.0, 0.0]
        self.ear_bounce = [0.0, 0.0]

        # Emoción activa
        self.active_emotion = "neutral"
        self.emotion_conf = 0.0
        self.emotion_since = 0.0
        self._choreo = VoiceEmotionAnalyzer()

    def step(self, data, level, now, dt, mouse_norm=(0.0, 0.0), face_full=None, emotion_state=None):
        dt = max(0.004, min(0.05, float(dt)))
        tr = data.get("tracking", {})
        au = data.get("audio", {})
        ctrl_mode = tr.get("mode", "voice")

        # 1. Resolver emoción activa (manual / voz / sonrisa cámara)
        man_em = tr.get("manual_emotion", "auto")
        if man_em and man_em != "auto":
            target_em, target_cf = man_em, 1.0
        elif tr.get("auto_emotion", True):
            target_em, target_cf = "neutral", 0.0
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

        # 2. Lip-sync (voz o cámara)
        T, R = float(au.get("threshold", 36)), float(au.get("range", 12))
        lvl = level * 100.0
        if ctrl_mode == "camera" and face_full and face_full.get("detected"):
            mo = float(face_full.get("mouth_open", 0.0))
            target_m = 3 if mo > 0.78 else (2 if mo > 0.45 else (1 if mo > 0.22 else 0))
        else:
            target_m = int(lvl > T) + int(lvl > T + R) + int(lvl > T + 2 * R)
            if self.active_emotion == "shout" and (lvl > T * 0.6 or man_em == "shout"):
                target_m = 3
            elif self.active_emotion == "laugh" and (lvl > T * 0.5 or man_em == "laugh"):
                target_m = 2 if math.sin(now * 16.0) > -0.2 else 1
        if target_m >= self.mouth_state:
            self.mouth_state = target_m
            if target_m > 0:
                self.hold_until = now + au.get("hold", 85) / 1000.0
        elif now > self.hold_until and now > self.step_at:
            self.mouth_state -= 1
            self.step_at = now + 0.05
        self.mouth_open += ((self.mouth_state / 3.0) - self.mouth_open) * min(1.0, dt * 16.0)

        # 3. Parpadeo
        if ctrl_mode in ("camera", "cam_voice") and face_full and face_full.get("detected") and face_full.get("blink", 0.0) > 0.35:
            b_tgt = min(1.0, face_full["blink"] * 1.2)
        else:
            if self.blink_at == 0.0:
                self.blink_at = now + self.rng.uniform(2.2, 4.8)
            elif now > self.blink_at:
                self.blink_start = now
                self.blink_end = now + 0.18
                self.blink_at = now + self.rng.uniform(2.5, 5.4)
            if self.blink_start <= now < self.blink_end:
                frac = (now - self.blink_start) / max(0.01, self.blink_end - self.blink_start)
                b_tgt = math.sin(frac * math.pi)
            else:
                b_tgt = 0.0
        self.blink_amt += (b_tgt - self.blink_amt) * min(1.0, dt * 26.0)

        # 4. Pose 3D de cabeza y cuerpo según Modo de Control (Solo Voz / Cámara + Voz / Solo Cámara)
        use_cam = ctrl_mode in ("camera", "cam_voice")
        use_voice = (ctrl_mode == "voice") or (use_cam and not (face_full and face_full.get("detected")))

        tyaw = tpitch = troll = gx = gy = 0.0
        if use_cam and face_full and face_full.get("detected"):
            tyaw += float(face_full["x"]) * 32.0
            tpitch += float(face_full["y"]) * 22.0
            troll += -float(face_full.get("roll", 0.0)) * 26.0
            gx += float(face_full["x"]) * 0.9
            gy += float(face_full["y"]) * 0.75

        if use_voice:
            vhx, vhy, vhz, vgx, vgy = self._choreo.voice_choreography(now, max(level, self.mouth_open), self.active_emotion)
            tyaw += vhx * 26.0
            tpitch += vhy * 18.0
            troll += -vhz * 16.0
            gx += vgx
            gy += vgy
        elif self.active_emotion != "neutral":
            vhx, vhy, vhz, _, _ = self._choreo.voice_choreography(now, max(level, self.mouth_open), self.active_emotion)
            tyaw += vhx * 10.0
            tpitch += vhy * 10.0
            troll += -vhz * 8.0

        # Pequeña influencia natural del cursor sobre la mirada
        if abs(mouse_norm[0]) > 1e-3 or abs(mouse_norm[1]) > 1e-3:
            tyaw += mouse_norm[0] * 10.0
            tpitch += mouse_norm[1] * 7.0
            gx = gx * 0.55 + mouse_norm[0] * 0.65
            gy = gy * 0.55 + mouse_norm[1] * 0.55

        self.prev_yaw, self.prev_pitch = self.head_yaw, self.head_pitch
        self.head_yaw += (max(-42.0, min(42.0, tyaw)) - self.head_yaw) * min(1.0, dt * 11.0)
        self.head_pitch += (max(-28.0, min(28.0, tpitch)) - self.head_pitch) * min(1.0, dt * 11.0)
        self.head_roll += (max(-28.0, min(28.0, troll)) - self.head_roll) * min(1.0, dt * 10.0)
        self.body_yaw += (self.head_yaw * 0.32 - self.body_yaw) * min(1.0, dt * 6.0)

        # Respiración + rebote vocal + energía emocional
        bob = math.sin(now * 2.2) * 0.014 + self.mouth_open * 0.036 * abs(math.sin(now * 10.0))
        if self.active_emotion == "laugh":
            bob += abs(math.sin(now * 13.5)) * 0.048
        elif self.active_emotion == "shout":
            bob += math.sin(now * 42.0) * 0.018
        elif self.active_emotion == "sad":
            bob -= 0.038
        elif self.active_emotion == "surprised":
            bob += math.exp(-max(0.0, now - self.emotion_since) * 7.0) * 0.085
        self.body_bob += (bob - self.body_bob) * min(1.0, dt * 16.0)

        self.gaze_x += (max(-1.0, min(1.0, gx)) - self.gaze_x) * min(1.0, dt * 15.0)
        self.gaze_y += (max(-1.0, min(1.0, gy)) - self.gaze_y) * min(1.0, dt * 15.0)

        # 5. Física Spring-Bone 3D (cabello trasero, coletas y orejas)
        vyaw = (self.head_yaw - self.prev_yaw) / dt
        vpitch = (self.head_pitch - self.prev_pitch) / dt

        def _spring(st, target, k=110.0, d=10.5, lo=-32.0, hi=32.0):
            acc = (target - st[0]) * k - st[1] * d
            st[1] += acc * dt
            st[0] = max(lo, min(hi, st[0] + st[1] * dt))

        wind_r = math.sin(now * 2.5) * 4.5 + math.sin(now * 4.1) * 2.0
        _spring(self.hair_roll, -vyaw * 0.12 - self.head_roll * 0.65 + wind_r, k=95.0, d=9.0)
        _spring(self.hair_pitch, -vpitch * 0.10 + self.head_pitch * 0.45 + abs(math.sin(now * 2.2)) * 2.5, k=95.0, d=9.5)
        _spring(self.ear_bounce, self.body_bob * 160.0 + abs(vyaw) * 0.03, k=160.0, d=11.0, lo=-18.0, hi=18.0)


# =====================================================  Rasterizador 3D Vectorizado (Toon/Studio + Alfa real)
def _project_points(pts_world, cam_pos, cam_rot, f_px, cx, cy):
    rel = pts_world - cam_pos
    cam_pts = rel @ cam_rot.T
    # En nuestra cámara mirando hacia -Z, puntos visibles tienen z_cam < -0.15
    depth = -cam_pts[:, 2]
    safe_d = np.maximum(depth, 0.15)
    sx = cx + (cam_pts[:, 0] / safe_d) * f_px
    sy = cy - (cam_pts[:, 1] / safe_d) * f_px
    return sx, sy, depth, cam_pts


def _rasterize_mesh_into_rgba(buf_rgba, mesh, model_rot, model_pos, cam_pos, cam_rot,
                              f_px, cx, cy, light_dir, shader="toon", rim_amt=0.6, amb=0.42, outline=True):
    """Transforma, ilumina (Toon/Studio + Rim Light + Especular), ordena en Z y rasteriza en RGBA."""
    if mesh is None or cv2 is None:
        return
    verts = mesh["verts"]
    tris = mesh["tris"]
    if len(tris) == 0:
        return

    w_verts = (verts @ model_rot.T) + model_pos
    sx, sy, depth, cam_pts = _project_points(w_verts, cam_pos, cam_rot, f_px, cx, cy)

    # Coordenadas de los 3 vértices de cada triángulo
    p0 = w_verts[tris[:, 0]]
    p1 = w_verts[tris[:, 1]]
    p2 = w_verts[tris[:, 2]]

    # Normales de cara en espacio mundo
    e1 = p1 - p0
    e2 = p2 - p0
    normals = np.cross(e1, e2)
    nlen = np.linalg.norm(normals, axis=1, keepdims=True)
    valid = (nlen[:, 0] > 1e-7)
    normals = np.divide(normals, np.maximum(nlen, 1e-7))

    # Backface culling en espacio cámara (triángulos que miran hacia la cámara)
    tri_centers = (p0 + p1 + p2) * (1.0 / 3.0)
    view_dir = cam_pos - tri_centers
    vlen = np.linalg.norm(view_dir, axis=1, keepdims=True)
    view_dir = np.divide(view_dir, np.maximum(vlen, 1e-7))
    ndotv = np.einsum("ij,ij->i", normals, view_dir)

    d_tri = (depth[tris[:, 0]] + depth[tris[:, 1]] + depth[tris[:, 2]]) * (1.0 / 3.0)
    vis_mask = valid & (ndotv > -0.04) & (d_tri > 0.25)
    if not np.any(vis_mask):
        return

    vis_idx = np.nonzero(vis_mask)[0]
    # Ordenar de más lejano a más cercano (algoritmo del pintor)
    order = vis_idx[np.argsort(-d_tri[vis_idx])]

    n_vis = normals[order]
    v_vis = view_dir[order]
    col_vis = mesh["colors"][order]
    spec_vis = mesh["spec"][order]

    # Iluminación principal + relleno cálido/frío
    ndotl = np.einsum("ij,j->i", n_vis, light_dir)
    if shader == "toon":
        # Cel-shading anime suave (transición sedosa sin saltos duros en mejillas)
        t1 = np.clip((ndotl - 0.18) / 0.34, 0.0, 1.0)
        s1 = t1 * t1 * (3.0 - 2.0 * t1)
        t2 = np.clip((ndotl + 0.28) / 0.36, 0.0, 1.0)
        s2 = t2 * t2 * (3.0 - 2.0 * t2)
        diff = (0.72 + 0.17 * s2 + 0.15 * s1).astype(np.float32)
        shade = amb * 0.42 + diff * (1.0 - amb * 0.32)
    else:
        # Sombreado suave PBR Studio (half-Lambert para rostros suaves sin sombras duras)
        hl = np.clip(ndotl * 0.5 + 0.5, 0.0, 1.0)
        shade = amb + (1.0 - amb) * (hl * hl * 0.92 + 0.08)

    # Luz especular suave (Blinn-Phong)
    half_v = light_dir + v_vis
    hlen = np.linalg.norm(half_v, axis=1, keepdims=True)
    half_v = np.divide(half_v, np.maximum(hlen, 1e-7))
    spec_term = (np.clip(np.einsum("ij,ij->i", n_vis, half_v), 0.0, 1.0) ** 22.0) * spec_vis * 0.55

    # Rim Light Fresnel (contorno luminoso de estudio!)
    fresnel = np.clip(1.0 - np.clip(np.einsum("ij,ij->i", n_vis, v_vis), 0.0, 1.0), 0.0, 1.0) ** 2.6
    rim = fresnel * rim_amt * 0.42

    rgb = col_vis * shade[:, None] + spec_term[:, None] + rim[:, None] * np.array([0.78, 0.88, 1.0], dtype=np.float32)
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)

    # Polígonos 2D enteros para cv2.fillConvexPoly
    t_ord = tris[order]
    px = np.stack([sx[t_ord[:, 0]], sx[t_ord[:, 1]], sx[t_ord[:, 2]]], axis=1)
    py = np.stack([sy[t_ord[:, 0]], sy[t_ord[:, 1]], sy[t_ord[:, 2]]], axis=1)
    polys = np.rint(np.stack([px, py], axis=2)).astype(np.int32)
    fill = cv2.fillConvexPoly
    r_ch, g_ch, b_ch = rgb[:, 0].tolist(), rgb[:, 1].tolist(), rgb[:, 2].tolist()
    for k in range(len(order)):
        fill(buf_rgba, polys[k], (r_ch[k], g_ch[k], b_ch[k], 255), cv2.LINE_8)


def _draw_3d_face_overlay(painter, data, state, now, head_rot, head_pos, rig_meshes,
                          cam_pos, cam_rot, f_px, cx, cy):
    """Proyecta ojos, pupilas con mirada 3D, cejas, rubor y boca lip-sync sobre la superficie frontal 3D de la cabeza."""
    # Comprobar si la cara mira hacia la cámara (al orbitar detrás de la cabeza se oculta naturalmente!)
    head_fwd = head_rot @ np.array([0.0, 0.0, 1.0], dtype=np.float32)
    cam_dir = cam_pos - (head_pos + head_rot @ np.array([0.0, rig_meshes["head_y"], 0.0], dtype=np.float32))
    cam_dir /= max(1e-5, float(np.linalg.norm(cam_dir)))
    facing = float(np.dot(head_fwd, cam_dir))
    if facing < 0.14:
        return

    ap = data.get("appearance", {})
    hy = rig_meshes["head_y"]
    hr = rig_meshes["head_r"]
    hs = rig_meshes["head_s"]
    esz = float(ap.get("eye_size", 100)) / 100.0
    eye_col = QColor(ap.get("eye_color", "#2F7CF6"))
    hair_col = QColor(ap.get("hair_color", "#394B72")).darker(145)
    ink = QColor("#181922")
    em = state.active_emotion

    def proj(lx, ly, lz):
        pw = head_pos + head_rot @ np.array([lx, hy + ly, lz], dtype=np.float32)
        rel = pw - cam_pos
        cp = cam_rot @ rel
        d = max(0.2, float(-cp[2]))
        return QPointF(cx + float(cp[0]) / d * f_px, cy - float(cp[1]) / d * f_px), f_px / d

    # Escala proyectada local y ángulo de roll aparente en pantalla
    p_l, sc_p = proj(-0.16 * hs, 0.0, hr * 0.92)
    p_r, _ = proj(0.16 * hs, 0.0, hr * 0.92)
    roll_2d = math.degrees(math.atan2(p_r.y() - p_l.y(), p_r.x() - p_l.x()))
    foreshort_x = max(0.28, min(1.0, facing))
    px_u = sc_p * hs * 0.115

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setOpacity(min(1.0, (facing - 0.14) / 0.28))

    # 1. Rubor 3D en mejillas (si accesorio 5 activo o emoción feliz/riendo)
    if data.get("accessories", {}).get("5", True) or em in ("happy", "laugh"):
        b_alpha = 155 if em in ("happy", "laugh") else 95
        for side in (-1.0, 1.0):
            pb, _ = proj(side * 0.21 * hs, -0.08 * hs, hr * 0.87)
            painter.save()
            painter.translate(pb)
            painter.rotate(roll_2d)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(244, 114, 152, b_alpha))
            painter.drawEllipse(QPointF(0, 0), px_u * 0.72 * foreshort_x, px_u * 0.36)
            painter.restore()

    # 2. Ojos y Pupilas 3D con parpadeo + emociones
    for side in (-1.0, 1.0):
        pe, _ = proj(side * 0.148 * hs, 0.02 * hs, hr * 0.91)
        ew = px_u * 0.82 * esz * foreshort_x
        eh = px_u * 0.74 * esz
        painter.save()
        painter.translate(pe)
        painter.rotate(roll_2d)

        if em == "laugh":
            # Ojos curvos felices (^ ^)
            painter.setPen(QPen(ink, max(2.2, px_u * 0.18), Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            arc = QPainterPath()
            arc.moveTo(-ew, eh * 0.22)
            arc.quadTo(0, -eh * 0.85, ew, eh * 0.22)
            painter.drawPath(arc)
        elif state.blink_amt > 0.72:
            # Parpadeo cerrado
            painter.setPen(QPen(ink, max(2.0, px_u * 0.16), Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            arc = QPainterPath()
            arc.moveTo(-ew, eh * 0.08)
            arc.quadTo(0, eh * 0.34, ew, eh * 0.08)
            painter.drawPath(arc)
        else:
            open_h = eh * max(0.24, 1.0 - state.blink_amt * 0.82)
            if em == "surprised":
                open_h *= 1.18
            elif em == "angry":
                open_h *= 0.78
            elif em == "sad":
                open_h *= 0.86

            # Esclerótica
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(0, 0), ew, open_h)

            # Iris + pupila desplazada por mirada 3D
            gx = state.gaze_x * ew * 0.30
            gy = state.gaze_y * open_h * 0.26
            ir_w = ew * (0.52 if em == "surprised" else 0.62)
            ir_h = open_h * (0.58 if em == "surprised" else 0.78)
            painter.setBrush(eye_col)
            painter.setPen(QPen(ink, max(1.0, px_u * 0.06)))
            painter.drawEllipse(QPointF(gx, gy), ir_w, ir_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#11131C"))
            painter.drawEllipse(QPointF(gx, gy + ir_h * 0.05), ir_w * 0.44, ir_h * 0.48)
            # Reflejos especulares del ojo
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(gx - ir_w * 0.36, gy - ir_h * 0.34), max(1.6, ir_w * 0.26), max(1.6, ir_h * 0.24))
            painter.drawEllipse(QPointF(gx + ir_w * 0.30, gy + ir_h * 0.32), max(1.0, ir_w * 0.13), max(1.0, ir_h * 0.12))

            if em == "sad":
                # Brillo húmedo de lágrima en borde inferior del ojo
                painter.setBrush(QColor(96, 185, 250, 200))
                painter.drawEllipse(QPointF(side * ew * 0.42, open_h * 0.72), ew * 0.28, open_h * 0.20)

            # Pestaña superior
            painter.setPen(QPen(ink, max(2.2, px_u * 0.19), Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            if em == "angry":
                painter.drawLine(QPointF(-ew * 1.05, -open_h * (0.95 if side < 0 else 0.38)),
                                 QPointF(ew * 1.05, -open_h * (0.38 if side < 0 else 0.95)))
            elif em == "sad":
                painter.drawLine(QPointF(-ew * 1.05, -open_h * (0.45 if side < 0 else 0.95)),
                                 QPointF(ew * 1.05, -open_h * (0.95 if side < 0 else 0.45)))
            else:
                lash = QPainterPath()
                lash.moveTo(-ew * 1.08, -open_h * 0.18)
                lash.quadTo(0, -open_h * 1.28, ew * 1.08, -open_h * 0.18)
                painter.drawPath(lash)

        # Cejas expresivas
        painter.setPen(QPen(hair_col, max(2.0, px_u * 0.15), Qt.SolidLine, Qt.RoundCap))
        by = -eh * (1.42 if em == "surprised" else 1.18)
        if em == "angry" or em == "shout":
            painter.drawLine(QPointF(-ew * 0.85, by + (0.28 * eh if side > 0 else -0.22 * eh)),
                             QPointF(ew * 0.85, by + (-0.22 * eh if side > 0 else 0.28 * eh)))
        elif em == "sad":
            painter.drawLine(QPointF(-ew * 0.85, by + (-0.18 * eh if side > 0 else 0.26 * eh)),
                             QPointF(ew * 0.85, by + (0.26 * eh if side > 0 else -0.18 * eh)))
        else:
            br = QPainterPath()
            br.moveTo(-ew * 0.82, by + 0.08 * eh)
            br.quadTo(0, by - (0.38 if em in ("happy", "laugh", "surprised") else 0.18) * eh, ew * 0.82, by + 0.06 * eh)
            painter.drawPath(br)
        painter.restore()

    # 3. Boca 3D con Lip-Sync y Emociones
    pm, _ = proj(0.0, -0.175 * hs, hr * 0.91)
    mw = px_u * 0.78 * foreshort_x
    mo = state.mouth_open
    painter.save()
    painter.translate(pm)
    painter.rotate(roll_2d)

    if mo < 0.14:
        painter.setPen(QPen(ink, max(2.0, px_u * 0.14), Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        if em == "sad":
            m = QPainterPath()
            m.moveTo(-mw * 0.65, px_u * 0.18)
            m.quadTo(0, -px_u * 0.14, mw * 0.65, px_u * 0.18)
            painter.drawPath(m)
        elif em == "angry":
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(QRectF(-mw * 0.65, -px_u * 0.14, mw * 1.30, px_u * 0.28), 3, 3)
        elif em == "surprised":
            painter.setBrush(QColor("#7A2237"))
            painter.drawEllipse(QPointF(0, 0), mw * 0.34, px_u * 0.34)
        else:
            curve = px_u * (0.42 if em in ("happy", "laugh") else 0.22)
            m = QPainterPath()
            m.moveTo(-mw * (0.95 if em in ("happy", "laugh") else 0.68), -px_u * 0.04)
            m.quadTo(0, curve, mw * (0.95 if em in ("happy", "laugh") else 0.68), -px_u * 0.04)
            painter.drawPath(m)
    else:
        mh = px_u * (0.28 + mo * 0.74)
        rw_m = mw * (1.18 if em in ("laugh", "shout") else 0.95)
        painter.setPen(QPen(ink, max(1.8, px_u * 0.12)))
        painter.setBrush(QColor("#782036"))
        painter.drawRoundedRect(QRectF(-rw_m, -mh * 0.45, rw_m * 2.0, mh), mh * 0.42, mh * 0.45)
        if mo > 0.38:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(QRectF(-rw_m * 0.78, -mh * 0.40, rw_m * 1.56, mh * 0.24), 2, 2)
            painter.setBrush(QColor("#EC768D"))
            painter.drawEllipse(QPointF(0, mh * 0.26), rw_m * 0.56, mh * 0.26)
    painter.restore()
    painter.restore()


def _draw_3d_emotion_reactions(painter, data, state, now, out_w, out_h):
    """Dibuja reacciones vectoriales nítidas alrededor del avatar 3D según la emoción activa."""
    em = state.active_emotion
    if em == "neutral" or not data.get("tracking", {}).get("auto_reactions", True):
        return
    cx, cy = out_w * 0.5, out_h * 0.42
    sc = min(out_w, out_h) / 640.0
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)

    if em == "shout":
        painter.setPen(QPen(QColor(229, 72, 77, 170), max(2.0, 3.4 * sc), Qt.SolidLine, Qt.RoundCap))
        for i in range(16):
            ang = (i / 16.0) * math.tau + math.sin(now * 28.0 + i) * 0.05
            r0 = (210.0 + ((i * 29) % 40) + math.sin(now * 34.0 + i) * 12.0) * sc
            r1 = 315.0 * sc
            painter.drawLine(QPointF(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                             QPointF(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1))
    elif em == "laugh":
        painter.setFont(QFont("Segoe UI", max(14, int(24 * sc)), QFont.Bold))
        for i, (sym, dx, dy, col) in enumerate((
            ("✦", -155, -75, "#F59E0B"), ("♪", 155, -85, "#FBBF24"), ("✦", 165, 25, "#F59E0B")
        )):
            bob = math.sin(now * 8.0 + i * 1.4) * 12.0 * sc
            painter.setPen(QColor(col))
            painter.drawText(QRectF(cx + dx * sc - 24, cy + dy * sc + bob - 24, 48, 48), Qt.AlignCenter, sym)
    elif em == "happy":
        painter.setFont(QFont("Segoe UI", max(13, int(22 * sc)), QFont.Bold))
        for i, (sym, dx, dy, col) in enumerate((
            ("★", -150, -70, "#2EB67D"), ("✦", 150, -80, "#38BDF8"), ("♥", 142, 30, "#F472B6")
        )):
            bob = math.sin(now * 4.0 + i * 1.8) * 10.0 * sc
            painter.setPen(QColor(col))
            painter.drawText(QRectF(cx + dx * sc - 24, cy + dy * sc + bob - 24, 48, 48), Qt.AlignCenter, sym)
    elif em == "sad":
        painter.setPen(QPen(QColor(96, 185, 250, 125), max(1.6, 2.2 * sc), Qt.SolidLine, Qt.RoundCap))
        for i in range(7):
            rx = cx + (-170.0 + i * 56.0) * sc
            ry = ((now * 160.0 + i * 70.0) % 280.0) * sc + cy - 140.0 * sc
            painter.drawLine(QPointF(rx, ry), QPointF(rx - 3.0 * sc, ry + 18.0 * sc))
    elif em == "angry":
        pulse = 1.0 + 0.14 * math.sin(now * 16.0)
        painter.save()
        painter.translate(cx + 108.0 * sc, cy - 82.0 * sc)
        painter.scale(pulse * sc, pulse * sc)
        painter.setPen(QPen(QColor("#E5484D"), 4.8, Qt.SolidLine, Qt.RoundCap))
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            path = QPainterPath()
            path.moveTo(sx * 16, sy * 4)
            path.quadTo(sx * 5, sy * 5, sx * 4, sy * 16)
            painter.drawPath(path)
        painter.restore()
    elif em == "surprised":
        dt_em = max(0.0, now - state.emotion_since)
        pop = 1.0 + math.exp(-dt_em * 9.0) * 0.4
        painter.save()
        painter.translate(cx + 145.0 * sc, cy - 85.0 * sc)
        painter.scale(pop * sc, pop * sc)
        painter.setPen(QPen(QColor("#101012"), 3.2))
        painter.setBrush(QColor("#A78BFA"))
        painter.drawRoundedRect(QRectF(-9, -34, 18, 38), 7, 7)
        painter.drawEllipse(QPointF(0, 17), 9, 9)
        painter.restore()
    painter.restore()


def render_vtuber3d_to_image(data, state, now, out_w=640, out_h=640, transparent=True):
    """Renderiza el avatar VTuber 3D completo en un QImage RGBA listo para estudio, OBS, Video 16:9/TikTok o exportación."""
    out_w = max(64, int(out_w))
    out_h = max(64, int(out_h))
    buf = np.zeros((out_h, out_w, 4), dtype=np.uint8)

    # Cámara orbital 3D
    cam_cfg = data.get("camera", {})
    c_yaw = math.radians(float(cam_cfg.get("yaw", 0.0)))
    c_pitch = math.radians(max(-40.0, min(45.0, float(cam_cfg.get("pitch", 4.0)))))
    c_dist = max(1.4, min(5.5, float(cam_cfg.get("distance", 2.95))))
    tgt = np.array([0.0, float(cam_cfg.get("target_y", 0.20)), 0.0], dtype=np.float32)

    cam_offset = np.array([
        c_dist * math.cos(c_pitch) * math.sin(c_yaw),
        c_dist * math.sin(c_pitch),
        c_dist * math.cos(c_pitch) * math.cos(c_yaw),
    ], dtype=np.float32)
    cam_pos = tgt + cam_offset

    fwd = tgt - cam_pos
    fwd /= max(1e-6, float(np.linalg.norm(fwd)))
    world_up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    right = np.cross(fwd, world_up)
    rlen = float(np.linalg.norm(right))
    right = right / rlen if rlen > 1e-6 else np.array([1.0, 0.0, 0.0], dtype=np.float32)
    up = np.cross(right, fwd)
    # Matriz de vista: filas = right, up, -fwd
    cam_rot = np.vstack([right, up, -fwd]).astype(np.float32)

    fov_rad = math.radians(38.0)
    f_px = (min(out_w, out_h) * 0.5) / math.tan(fov_rad * 0.5)
    cx = out_w * 0.5 + (math.sin(now * 58.0) * 4.0 if state.active_emotion == "shout" else 0.0)
    cy = out_h * 0.52 + (math.cos(now * 50.0) * 3.0 if state.active_emotion == "shout" else 0.0)

    # Iluminación de estudio
    lt = data.get("lighting", {})
    shader = lt.get("shader", "toon")
    az = math.radians(float(lt.get("azimuth", 34)))
    el = math.radians(float(lt.get("elevation", 32)))
    light_dir = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)], dtype=np.float32)
    light_dir /= max(1e-6, float(np.linalg.norm(light_dir)))
    rim_amt = float(lt.get("rim_strength", 62)) / 100.0
    amb = float(lt.get("ambient", 42)) / 100.0
    use_outline = bool(lt.get("outline", True))

    # ¿Usa modelo externo .vrm / .glb / .obj?
    ext_path = M.resolve(data.get("model_path", "")) if data.get("model_path") else ""
    ext_mesh = load_external_3d_model(ext_path) if ext_path else None

    if ext_mesh is not None:
        m_rot = _euler_mat(state.head_yaw * 0.75, state.head_pitch * 0.55, state.head_roll * 0.55)
        m_pos = np.array([0.0, state.body_bob, 0.0], dtype=np.float32)
        _rasterize_mesh_into_rgba(buf, ext_mesh, m_rot, m_pos, cam_pos, cam_rot,
                                  f_px, cx, cy, light_dir, shader, rim_amt, amb, use_outline)
        img = _finalize_rgba_to_qimage(buf, use_outline)
        p = QPainter(img)
        _draw_3d_emotion_reactions(p, data, state, now, out_w, out_h)
        p.end()
        return img

    # ---- Avatar 3D Articulado Integrado ----
    meshes = _build_procedural_3d_rig(data)
    hs = meshes["head_s"]
    hy = meshes["head_y"]
    hr = meshes["head_r"]

    torso_rot = _euler_mat(state.body_yaw, state.head_pitch * 0.14, state.head_roll * 0.18)
    torso_pos = np.array([0.0, -0.58 + state.body_bob, 0.0], dtype=np.float32)

    head_local = np.array([0.0, 0.64, -0.01], dtype=np.float32)
    head_pos = torso_pos + torso_rot @ head_local
    head_rot = torso_rot @ _euler_mat(state.head_yaw * 0.78, state.head_pitch * 0.88, state.head_roll * 0.84)

    # Orden de profundidad inteligente según si la cámara mira de frente o desde atrás (órbita 360° limpia!)
    from_back = math.cos(c_yaw) < -0.05
    draw_list = []

    def _add_wings():
        if meshes["wing_l"] is not None:
            flap = math.sin(now * 3.4) * 14.0 + state.mouth_open * 8.0
            for side, w_mesh in ((-1.0, meshes["wing_l"]), (1.0, meshes["wing_r"])):
                w_pos = torso_pos + torso_rot @ np.array([side * 0.14, 0.28, -0.22], dtype=np.float32)
                w_rot = torso_rot @ _euler_mat(side * (24.0 + flap), 0.0, side * flap * 0.3)
                draw_list.append((w_mesh, w_rot, w_pos))

    def _add_hair_back():
        if meshes["hair_back"] is not None:
            hb_pos = head_pos + head_rot @ np.array([0.0, hy + 0.12 * hs, -hr * 0.45], dtype=np.float32)
            hb_rot = head_rot @ _euler_mat(0.0, state.hair_pitch[0], state.hair_roll[0])
            draw_list.append((meshes["hair_back"], hb_rot, hb_pos))
        if meshes["tail_l"] is not None:
            for side, tm in ((-1.0, meshes["tail_l"]), (1.0, meshes["tail_r"])):
                tp = head_pos + head_rot @ np.array([side * 0.42 * hs, hy + 0.18 * hs, -0.18 * hs], dtype=np.float32)
                tr_m = head_rot @ _euler_mat(0.0, state.hair_pitch[0] * 1.25, state.hair_roll[0] * 1.35 + side * 6.0)
                draw_list.append((tm, tr_m, tp))

    def _add_core():
        draw_list.append((meshes["torso"], torso_rot, torso_pos))
        draw_list.append((meshes["head"], head_rot, head_pos))
        if meshes["ears"] is not None:
            ep = head_pos + head_rot @ np.array([0.0, hy + hr * 0.82, -0.02 * hs], dtype=np.float32)
            er = head_rot @ _euler_mat(0.0, state.ear_bounce[0] * 0.55, state.ear_bounce[0] * 0.2)
            draw_list.append((meshes["ears"], er, ep))
        if meshes["halo"] is not None:
            hp = head_pos + head_rot @ np.array([0.0, hy + hr * 1.48 + math.sin(now * 2.6) * 0.04, -0.02 * hs], dtype=np.float32)
            hr_m = head_rot @ _euler_mat(now * 32.0, 0.0, 0.0)
            draw_list.append((meshes["halo"], hr_m, hp))

    if from_back:
        if meshes["head_front"] is not None:
            draw_list.append((meshes["head_front"], head_rot, head_pos))
        _add_core()
        _add_hair_back()
        _add_wings()
    else:
        _add_wings()
        _add_hair_back()
        _add_core()

    for m_obj, m_rot, m_pos in draw_list:
        _rasterize_mesh_into_rgba(buf, m_obj, m_rot, m_pos, cam_pos, cam_rot,
                                  f_px, cx, cy, light_dir, shader, rim_amt, amb, use_outline)

    # Aplicar contorno ink limpio directamente en el buffer NumPy (cero copias QImage extra!)
    if not from_back and meshes["head_front"] is not None:
        front_buf = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        _rasterize_mesh_into_rgba(front_buf, meshes["head_front"], head_rot, head_pos, cam_pos, cam_rot,
                                  f_px, cx, cy, light_dir, shader, rim_amt, amb, use_outline)
    else:
        front_buf = None

    if use_outline and cv2 is not None:
        _apply_ink_outline_inplace(buf, front_buf)

    # Construir un único QImage final y dibujar rasgos faciales 3D + capa frontal + reacciones
    img = QImage(buf.data, out_w, out_h, out_w * 4, QImage.Format_RGBA8888).copy()
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    _draw_3d_face_overlay(p, data, state, now, head_rot, head_pos, meshes, cam_pos, cam_rot, f_px, cx, cy)
    if front_buf is not None:
        f_img = QImage(front_buf.data, out_w, out_h, out_w * 4, QImage.Format_RGBA8888)
        p.drawImage(0, 0, f_img)
    _draw_3d_emotion_reactions(p, data, state, now, out_w, out_h)
    p.end()
    return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def _finalize_rgba_to_qimage(buf_rgba, use_outline=False):
    if use_outline and cv2 is not None:
        _apply_ink_outline_inplace(buf_rgba, None)
    h, w, _ = buf_rgba.shape
    return QImage(buf_rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy().convertToFormat(QImage.Format_ARGB32_Premultiplied)


_OUTLINE_KER = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)) if cv2 is not None else None


def _apply_ink_outline_inplace(buf_rgba, front_buf=None):
    """Agrega trazo ink limpio (2 px) en silueta exterior en <0.6 ms operando directo sobre NumPy."""
    alpha = buf_rgba[..., 3] if front_buf is None else np.maximum(buf_rgba[..., 3], front_buf[..., 3])
    dil = cv2.dilate(alpha, _OUTLINE_KER)
    ring = (dil > 0) & (buf_rgba[..., 3] == 0)
    buf_rgba[ring] = (22, 23, 30, 240)
