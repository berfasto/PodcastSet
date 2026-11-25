"""Motor compartido de animación 2D VTuber, física de resorte, accesorios con inercia,
efectos visuales (sombra, contorno reactivo, brillo/saturación), partículas emote, props y renderizado.
Seguro para hilos (opera sobre QImage/QPainter) -> vista en vivo y exportación 100% idénticas."""
import math
import random
import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QImageReader, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QTransform)

import model as M
from assets_gfx import placeholder_character_image

try:
    import cv2
except Exception:
    cv2 = None

DEFAULT_W, DEFAULT_H = 400, 560
MAX_REF_W, MAX_REF_H = 560, 560
MAX_DECODE_DIM = 900


# =====================================================  Gestor cacheado de sprites, GIF/WebP y FX
class SpriteAssetManager:
    """Decodifica y cachea sprites (PNG/JPG/WebP/GIF animado), recortes uniformes, sombras y contornos."""

    def __init__(self):
        self._lock = threading.RLock()
        self._img_cache = {}       # (path, mtime, max_dim) -> (frames_list, delays_ms, orig_sz, crop_box)
        self._norm_cache = {}      # key -> QImage ARGB32_Premultiplied
        self._fx_cache = {}        # key -> QImage con sombra/contorno/color
        self._bg_cache = {}        # key -> QImage fondo procesado
        self._thumb_cache = {}     # (path, mtime, size) -> QPixmap

    def clear_fx_cache(self):
        with self._lock:
            self._fx_cache.clear()
            self._bg_cache.clear()

    def load_image_info(self, path, max_dim=MAX_DECODE_DIM):
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return None
        key = (str(p), mtime, max_dim)
        with self._lock:
            if key in self._img_cache:
                return self._img_cache[key]

        reader = QImageReader(str(p))
        reader.setAutoTransform(True)
        orig_sz = reader.size()
        scaled_sz = None
        if orig_sz.isValid() and max(orig_sz.width(), orig_sz.height()) > max_dim:
            scaled_sz = orig_sz.scaled(max_dim, max_dim, Qt.KeepAspectRatio)
            reader.setScaledSize(scaled_sz)

        frames = []
        delays = []
        is_anim = reader.supportsAnimation() and reader.imageCount() > 1
        max_frames = 48 if is_anim else 1
        for _ in range(max_frames):
            img = reader.read()
            if img.isNull():
                break
            if img.format() != QImage.Format_RGBA8888:
                img = img.convertToFormat(QImage.Format_RGBA8888)
            frames.append(img)
            d = reader.nextImageDelay()
            delays.append(max(30, d if d > 0 else 80))
            if not is_anim:
                break

        if not frames:
            return None

        img0 = frames[0]
        w, h = img0.width(), img0.height()
        ow = orig_sz.width() if orig_sz.isValid() and orig_sz.width() > 0 else w
        oh = orig_sz.height() if orig_sz.isValid() and orig_sz.height() > 0 else h

        arr = np.frombuffer(img0.constBits(), np.uint8).reshape(h, img0.bytesPerLine() // 4, 4)[:, :w, 3]
        ys = np.where(arr.max(axis=1) > 8)[0]
        xs = np.where(arr.max(axis=0) > 8)[0]
        if len(xs) and len(ys):
            rx0, rx1 = float(xs[0]) / w, float(xs[-1] + 1) / w
            ry0, ry1 = float(ys[0]) / h, float(ys[-1] + 1) / h
        else:
            rx0, ry0, rx1, ry1 = 0.0, 0.0, 1.0, 1.0

        val = (frames, delays, (ow, oh), (rx0, ry0, rx1, ry1))
        with self._lock:
            if len(self._img_cache) > 120:
                self._img_cache.pop(next(iter(self._img_cache)))
            self._img_cache[key] = val
        return val

    def frame_at(self, info, now):
        frames, delays = info[0], info[1]
        if len(frames) <= 1:
            return frames[0]
        total_ms = sum(delays)
        if total_ms <= 0:
            return frames[0]
        t_ms = (now * 1000.0) % total_ms
        acc = 0.0
        for fr, d in zip(frames, delays):
            acc += d
            if t_ms < acc:
                return fr
        return frames[-1]

    def load_thumb_pixmap(self, path, size=140):
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return None
        key = (str(p), mtime, size)
        with self._lock:
            if key in self._thumb_cache:
                return self._thumb_cache[key]
        info = self.load_image_info(str(p), max_dim=480)
        if info is None:
            return None
        img = info[0][0]
        rx0, ry0, rx1, ry1 = info[3]
        w, h = img.width(), img.height()
        rect = QRect(
            max(0, int(math.floor(rx0 * w))),
            max(0, int(math.floor(ry0 * h))),
            max(1, int(math.ceil((rx1 - rx0) * w))),
            max(1, int(math.ceil((ry1 - ry0) * h))),
        ).intersected(QRect(0, 0, w, h))
        cropped = img.copy(rect)
        pm = QPixmap.fromImage(cropped.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        with self._lock:
            if len(self._thumb_cache) > 180:
                self._thumb_cache.pop(next(iter(self._thumb_cache)))
            self._thumb_cache[key] = pm
        return pm

    def character_crop_boxes(self, ch, expr_idx=None):
        groups = {}
        exprs = ch["expressions"]
        if expr_idx is not None and 0 <= expr_idx < len(exprs):
            scan_exprs = [exprs[expr_idx]]
        else:
            scan_exprs = exprs
        for ex in scan_exprs:
            sp = ex.get("sprites", {})
            for k, _, _ in M.SLOTS:
                path = M.resolve(sp.get(k, ""))
                info = self.load_image_info(path)
                if info is None:
                    continue
                _, _, orig_sz, (rx0, ry0, rx1, ry1) = info
                if orig_sz not in groups:
                    groups[orig_sz] = [rx0, ry0, rx1, ry1]
                else:
                    g = groups[orig_sz]
                    g[0] = min(g[0], rx0)
                    g[1] = min(g[1], ry0)
                    g[2] = max(g[2], rx1)
                    g[3] = max(g[3], ry1)
        out = {}
        for orig_sz, (x0, y0, x1, y1) in groups.items():
            pad = 0.006
            out[orig_sz] = (max(0.0, x0 - pad), max(0.0, y0 - pad), min(1.0, x1 + pad), min(1.0, y1 + pad))
        return out

    def reference_size(self, ch, crop_boxes=None):
        if crop_boxes is None:
            crop_boxes = self.character_crop_boxes(ch)
        exprs = ch["expressions"]
        order = [exprs[ch["expr"]]] + [e for i, e in enumerate(exprs) if i != ch["expr"]] if exprs else []
        for ex in order:
            sp = ex.get("sprites", {})
            for k in list(M.KEYS) + [s[0] for s in M.SLOTS if s[0] not in M.KEYS]:
                path = M.resolve(sp.get(k, ""))
                info = self.load_image_info(path)
                if info is None:
                    continue
                orig_sz = info[2]
                x0, y0, x1, y1 = crop_boxes.get(orig_sz, (0.0, 0.0, 1.0, 1.0))
                cw = max(1.0, orig_sz[0] * (x1 - x0))
                ch_px = max(1.0, orig_sz[1] * (y1 - y0))
                scale = min(MAX_REF_W / cw, MAX_REF_H / ch_px)
                return max(80, int(round(cw * scale))), max(80, int(round(ch_px * scale)))
        return DEFAULT_W, DEFAULT_H

    def normalize_sprite_image(self, img, crop_rect, ref_w, ref_h, flip=False, dpr=1):
        w, h = img.width(), img.height()
        x0, y0, x1, y1 = crop_rect
        rect = QRect(
            max(0, int(math.floor(x0 * w))),
            max(0, int(math.floor(y0 * h))),
            max(1, int(math.ceil((x1 - x0) * w))),
            max(1, int(math.ceil((y1 - y0) * h))),
        ).intersected(QRect(0, 0, w, h))
        cropped = img.copy(rect)
        if flip:
            cropped = cropped.mirrored(True, False)
        bw, bh = ref_w * dpr, ref_h * dpr
        scaled = cropped.scaled(bw, bh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QImage(bw, bh, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        px = (bw - scaled.width()) // 2
        py = bh - scaled.height()
        p.drawImage(px, py, scaled)
        p.end()
        return canvas

    def build_character_images(self, ch, expr_idx=None, dpr=1):
        """Devuelve (ref_w, ref_h, slot_frames_dict, has_custom).
        slot_frames_dict[slot_key] = ([QImage...], [delay_ms...]) o None."""
        rig3d_data = ch.get("rig3d_data")
        if rig3d_data is not None and isinstance(rig3d_data, dict):
            import vtuber3d_engine as V3
            st3 = V3.VTuber3DState(seed=1)
            st3.step(rig3d_data, 0.0, 0.0, 0.016)
            thumb = V3.render_vtuber3d_to_image(rig3d_data, st3, 0.0, out_w=540, out_h=540, transparent=True)
            imgs = {k: ([thumb], [100]) if k in M.KEYS else None for k, _, _ in M.SLOTS}
            return 540, 540, imgs, True
        rig_data = ch.get("rig_data")
        if rig_data is not None and isinstance(rig_data, dict):
            import vtuber_rig as VR
            rs = VR.RigRuntimeState(seed=1)
            rs.step(rig_data, 0.0, 0.0, 0.016, live_mode=True)
            thumb = VR.render_rig_to_image(rig_data, rs, 0.0, out_w=540, out_h=540, live_mode=True)
            imgs = {k: ([thumb], [100]) if k in M.KEYS else None for k, _, _ in M.SLOTS}
            return 540, 540, imgs, True
        eidx = ch["expr"] if expr_idx is None else max(0, min(len(ch["expressions"]) - 1, int(expr_idx)))
        crop_boxes = self.character_crop_boxes(ch, expr_idx=eidx)
        ref_w, ref_h = self.reference_size(ch, crop_boxes)
        sp = ch["expressions"][eidx]["sprites"]
        flip = bool(ch.get("flip", False))

        loaded = {}
        for k, _, _ in M.SLOTS:
            path = M.resolve(sp.get(k, ""))
            loaded[k] = self.load_image_info(path)

        has_custom = any(v is not None for v in loaded.values())
        imgs = {}
        if not has_custom:
            for k, _, _ in M.SLOTS:
                raw = placeholder_character_image(ch["name"], k, ch.get("color", "#3B82F6"))
                norm = self.normalize_sprite_image(raw, (0.0, 0.0, 1.0, 1.0), ref_w, ref_h, flip=flip, dpr=dpr)
                imgs[k] = ([norm], [100])
        else:
            for k, info in loaded.items():
                if info is not None:
                    frames, delays, orig_sz, _ = info
                    crop_rect = crop_boxes.get(orig_sz, (0.0, 0.0, 1.0, 1.0))
                    n_frames = [self.normalize_sprite_image(fr, crop_rect, ref_w, ref_h, flip=flip, dpr=dpr) for fr in frames]
                    imgs[k] = (n_frames, delays)
                else:
                    imgs[k] = None
            if not any(imgs.get(k) is not None for k in M.KEYS):
                for k in M.KEYS:
                    raw = placeholder_character_image(ch["name"], k, ch.get("color", "#3B82F6"))
                    norm = self.normalize_sprite_image(raw, (0.0, 0.0, 1.0, 1.0), ref_w, ref_h, flip=flip, dpr=dpr)
                    imgs[k] = ([norm], [100])
        return ref_w, ref_h, imgs, has_custom

    def apply_sprite_fx(self, base_img, cache_tag, shadow=False, shadow_blur=14, shadow_alpha=45,
                        shadow_dx=0, shadow_dy=12, outline=False, outline_w=6, outline_color="#FFFFFF",
                        brightness=0, saturation=0):
        """Aplica sombra paralela, contorno reactivo y ajustes de brillo/saturación (cacheado en RGBA)."""
        if not shadow and not outline and brightness == 0 and saturation == 0:
            return base_img, (0, 0)
        pad = (int(outline_w) + 2 if outline else 0) + (int(shadow_blur) + max(abs(int(shadow_dx)), abs(int(shadow_dy))) + 4 if shadow else 0)
        key = (cache_tag, base_img.width(), base_img.height(), bool(shadow), int(shadow_blur), int(shadow_alpha),
               int(shadow_dx), int(shadow_dy), bool(outline), int(outline_w), str(outline_color),
               int(brightness), int(saturation))
        with self._lock:
            if key in self._fx_cache:
                return self._fx_cache[key]

        src = base_img.convertToFormat(QImage.Format_RGBA8888)
        w, h = src.width(), src.height()
        arr = np.frombuffer(src.constBits(), np.uint8).reshape(h, src.bytesPerLine() // 4, 4)[:, :w, :].copy()

        # Brillo y saturación sobre píxeles visibles
        if brightness != 0 or saturation != 0:
            rgb = arr[..., :3].astype(np.float32)
            if saturation != 0:
                luma = rgb[..., 0:1] * 0.299 + rgb[..., 1:2] * 0.587 + rgb[..., 2:3] * 0.114
                sat_f = max(0.0, 1.0 + saturation / 100.0)
                rgb = luma + (rgb - luma) * sat_f
            if brightness != 0:
                rgb = rgb * (1.0 + brightness / 100.0) + brightness * 0.6
            arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)

        if not shadow and not outline:
            out_img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy().convertToFormat(QImage.Format_ARGB32_Premultiplied)
            res = (out_img, (0, 0))
            with self._lock:
                if len(self._fx_cache) > 180:
                    self._fx_cache.pop(next(iter(self._fx_cache)))
                self._fx_cache[key] = res
            return res

        ow, oh = w + pad * 2, h + pad * 2
        alpha_pad = np.zeros((oh, ow), dtype=np.uint8)
        alpha_pad[pad:pad + h, pad:pad + w] = arr[..., 3]

        comp = QImage(ow, oh, QImage.Format_ARGB32_Premultiplied)
        comp.fill(Qt.transparent)
        p = QPainter(comp)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        if shadow and shadow_alpha > 0:
            sh_a = alpha_pad
            if cv2 is not None and shadow_blur > 0:
                k = max(3, int(shadow_blur) * 2 + 1)
                sh_a = cv2.GaussianBlur(sh_a, (k, k), 0)
            sh_rgba = np.zeros((oh, ow, 4), dtype=np.uint8)
            sh_rgba[..., 3] = (sh_a.astype(np.float32) * (shadow_alpha / 100.0)).astype(np.uint8)
            sh_img = QImage(sh_rgba.data, ow, oh, ow * 4, QImage.Format_RGBA8888)
            p.drawImage(int(shadow_dx), int(shadow_dy), sh_img)

        if outline and outline_w > 0:
            oc = QColor(outline_color)
            if cv2 is not None:
                r = max(1, int(outline_w))
                ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r * 2 + 1, r * 2 + 1))
                dil = cv2.dilate(alpha_pad, ker)
                dil = cv2.GaussianBlur(dil, (3, 3), 0)
            else:
                dil = alpha_pad
            ol_rgba = np.zeros((oh, ow, 4), dtype=np.uint8)
            ol_rgba[..., 0] = oc.red()
            ol_rgba[..., 1] = oc.green()
            ol_rgba[..., 2] = oc.blue()
            ol_rgba[..., 3] = dil
            ol_img = QImage(ol_rgba.data, ow, oh, ow * 4, QImage.Format_RGBA8888)
            p.drawImage(0, 0, ol_img)

        fg_img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888)
        p.drawImage(pad, pad, fg_img)
        p.end()

        res = (comp, (pad, pad))
        with self._lock:
            if len(self._fx_cache) > 180:
                self._fx_cache.pop(next(iter(self._fx_cache)))
            self._fx_cache[key] = res
        return res

    def render_background(self, scene_data, bg_color, w, h, transparent_override=False):
        """Genera y cachea el fondo procesado (color, degradado, imagen con blur/dim/viñeta o transparente)."""
        bg_type = "transparent" if transparent_override else scene_data.get("bg_type", "color")
        bg_path = M.resolve(scene_data.get("background", "")) if not transparent_override else ""
        mtime = None
        if bg_path:
            pp = Path(bg_path)
            mtime = pp.stat().st_mtime_ns if pp.exists() else None
        key = (w, h, bg_type, bg_color, scene_data.get("bg_grad_color2", "#1D2430"),
               int(scene_data.get("bg_grad_angle", 135)), bg_path, mtime,
               scene_data.get("bg_mode", "cover"), int(scene_data.get("bg_blur", 0)),
               int(scene_data.get("bg_dim", 0)), int(scene_data.get("bg_vignette", 0)))
        with self._lock:
            if key in self._bg_cache:
                return self._bg_cache[key]

        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        if bg_type == "transparent":
            img.fill(Qt.transparent)
        else:
            p = QPainter(img)
            if bg_type == "gradient":
                ang = math.radians(float(scene_data.get("bg_grad_angle", 135)))
                cx, cy = w * 0.5, h * 0.5
                dx, dy = math.cos(ang) * w * 0.6, math.sin(ang) * h * 0.6
                grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
                grad.setColorAt(0.0, QColor(bg_color))
                grad.setColorAt(1.0, QColor(scene_data.get("bg_grad_color2", "#1D2430")))
                p.fillRect(0, 0, w, h, grad)
            else:
                p.fillRect(0, 0, w, h, QColor(bg_color))

            if bg_path and mtime is not None:
                info = self.load_image_info(bg_path, max_dim=max(w, h, 1920))
                if info is not None:
                    src = info[0][0]
                    mode = scene_data.get("bg_mode", "cover")
                    if mode == "stretch":
                        scaled = src.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                        p.drawImage(0, 0, scaled)
                    elif mode == "contain":
                        scaled = src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        p.drawImage((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
                    else:
                        scaled = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        p.drawImage((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
            p.end()

            blur = int(scene_data.get("bg_blur", 0))
            if blur > 0 and cv2 is not None:
                rgba = img.convertToFormat(QImage.Format_RGBA8888)
                arr = np.frombuffer(rgba.constBits(), np.uint8).reshape(h, rgba.bytesPerLine() // 4, 4)[:, :w, :].copy()
                k = max(3, blur * 2 + 1)
                arr = cv2.GaussianBlur(arr, (k, k), 0)
                img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy().convertToFormat(QImage.Format_ARGB32_Premultiplied)

            dim = int(scene_data.get("bg_dim", 0))
            vig = int(scene_data.get("bg_vignette", 0))
            if dim > 0 or vig > 0:
                p = QPainter(img)
                if dim > 0:
                    p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(dim * 2.55)))
                if vig > 0:
                    rg = QRadialGradient(w * 0.5, h * 0.5, math.hypot(w, h) * 0.55)
                    rg.setColorAt(0.45, QColor(0, 0, 0, 0))
                    rg.setColorAt(1.0, QColor(0, 0, 0, int(vig * 2.55)))
                    p.fillRect(0, 0, w, h, rg)
                p.end()

        with self._lock:
            if len(self._bg_cache) > 16:
                self._bg_cache.pop(next(iter(self._bg_cache)))
            self._bg_cache[key] = img
        return img


SHARED_ASSETS = SpriteAssetManager()


# =====================================================  Estado físico 2D VTuber + partículas Emote
class CharAnimState:
    """Estado animado por personaje (compartido entre el lienzo en vivo y el motor de exportación)."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.state = 0
        self.cur_key = "closed"
        self.hold_until = 0.0
        self.step_at = 0.0
        self.amp = 0.0
        self.force = -1
        self.blink_at = 0.0
        self.blink_start = 0.0
        self.blink_end = 0.0
        self.silent_since = 0.0
        # Física de resorte 2D
        self.y_pos = 0.0
        self.y_vel = 0.0
        self.rot_val = 0.0
        self.rot_vel = 0.0
        self.off_x = 0.0
        self.off_y = 0.0
        self.sx = 1.0
        self.sy = 1.0
        self.dim_factor = 1.0
        self.zoom_factor = 1.0
        self.acc_phys = {}     # acc_id -> [dy, vdy, rot, vrot]
        self.emotes = []       # list of active emote particle dicts

    def trigger_emote(self, kind, now):
        color_map = {k: col for k, _, _, col in M.EMOTE_CATALOG}
        sym_map = {k: sym for k, sym, _, _ in M.EMOTE_CATALOG}
        col = color_map.get(kind, "#2F7CF6")
        sym = sym_map.get(kind, "★")
        count = 14 if kind == "confetti" else (5 if kind in ("heart", "spark", "note", "star") else 1)
        palette = ["#E5484D", "#2F7CF6", "#E5A00D", "#2EB67D", "#EC4899", "#38BDF8"]
        for i in range(count):
            spread = (i - (count - 1) / 2.0) * 32.0 if count > 1 else 0.0
            self.emotes.append({
                "kind": kind,
                "sym": sym,
                "color": palette[i % len(palette)] if kind == "confetti" else col,
                "born": now,
                "life": 1.35 + self.rng.uniform(0.0, 0.45) if count > 1 else 1.25,
                "x0": spread + self.rng.uniform(-18, 18),
                "y0": self.rng.uniform(-25, 10),
                "vx": self.rng.uniform(-55, 55) if count > 1 else self.rng.uniform(-12, 12),
                "vy": self.rng.uniform(-145, -75) if count > 1 else -45.0,
                "rot": self.rng.uniform(-25, 25),
                "vrot": self.rng.uniform(-120, 120) if kind == "confetti" else self.rng.uniform(-18, 18),
                "size": self.rng.uniform(18, 28) if count > 1 else 42.0,
            })

    def pick_key(self, avail_keys):
        for i in range(self.state, -1, -1):
            if M.KEYS[i] in avail_keys:
                return M.KEYS[i]
        return next((k for k in M.KEYS if k in avail_keys), "closed")

    def step(self, ch, level, now, dt, avail_keys, mouse_norm=(0.0, 0.0), face_norm=(0.0, 0.0, 0.0),
             someone_else_talking=False, emotion_state=None, face_full=None):
        dt = max(0.004, min(0.06, float(dt)))
        T, R = ch["threshold"], ch["range"]
        lvl = 0.0 if ch.get("mute") else level * 100.0
        target = self.force if self.force >= 0 else int(lvl > T) + int(lvl > T + R) + int(lvl > T + 2 * R)
        prev_state = self.state
        if target >= self.state:
            self.state = target
            if target > 0:
                self.hold_until = now + ch.get("hold", 90) / 1000.0
                self.silent_since = 0.0
        elif now > self.hold_until and now > self.step_at:
            self.state -= 1
            self.step_at = now + 0.05

        if self.state == 0:
            if self.silent_since == 0.0:
                self.silent_since = now
        else:
            self.silent_since = 0.0

        # Parpadeo suave 3 fases
        if ch.get("blink", True):
            be = max(1.0, float(ch.get("blink_every", 4)))
            if self.blink_at == 0.0:
                self.blink_at = now + self.rng.uniform(0.5, 1.2) * be
            elif now > self.blink_at:
                bd = max(0.08, ch.get("blink_ms", 200) / 1000.0)
                self.blink_start = now
                self.blink_end = now + bd
                self.blink_at = now + self.rng.uniform(0.6, 1.4) * be

        mouth_key = M.KEYS[self.state]
        key = self.pick_key(avail_keys)

        if self.state == 0 and self.silent_since > 0.0 and (now - self.silent_since) > 3.0:
            if "idle_alt" in avail_keys:
                key = "idle_alt"

        if ch.get("blink", True) and self.blink_start <= now < self.blink_end:
            frac = (now - self.blink_start) / max(0.01, self.blink_end - self.blink_start)
            hk = M.HALF_OF[mouth_key]
            bk = M.BLINK_OF[mouth_key]
            if bk not in avail_keys and mouth_key == "wide":
                bk = "blink_open"
            if frac < 0.24 or frac > 0.76:
                if hk in avail_keys:
                    key = hk
                elif 0.16 < frac < 0.84 and bk in avail_keys:
                    key = bk
            else:
                if bk in avail_keys:
                    key = bk
                elif hk in avail_keys:
                    key = hk
        self.cur_key = key

        # Amplitud vocal suavizada
        target_amp = self.state / 3.0
        self.amp += (target_amp - self.amp) * min(1.0, dt * 15.0)
        if self.amp < 0.001 and self.state == 0:
            self.amp = 0.0
        a = self.amp

        # Física 2D según estilo
        style = ch.get("motion_style", "spring")
        bounce = float(ch.get("bounce", 16))
        sway = float(ch.get("sway", 4))
        squash = float(ch.get("squash", 5))
        breathe = float(ch.get("breathe", 1))
        float_amp = float(ch.get("float_amp", 0))
        float_spd = float(ch.get("float_speed", 10)) / 10.0
        shout_shake = float(ch.get("shout_shake", 6))

        if style == "spring":
            k_stiff = float(ch.get("spring_stiffness", 16)) * 12.0
            damp = max(0.15, float(ch.get("spring_damping", 68)) / 100.0) * 2.0 * math.sqrt(k_stiff) * 0.45
            if self.state > prev_state:
                self.y_vel -= bounce * (3.8 + self.state * 1.4)
                self.rot_vel += sway * (1.8 if (int(now * 5) % 2 == 0) else -1.8) * 6.0
            target_y = -bounce * a * (0.45 + 0.55 * abs(math.sin(now * 9.5)))
            acc_y = (target_y - self.y_pos) * k_stiff - self.y_vel * damp
            self.y_vel += acc_y * dt
            self.y_pos += self.y_vel * dt
            self.y_pos = max(-bounce * 2.4 - 40.0, min(40.0, self.y_pos))

            target_rot = sway * a * math.sin(now * 6.5)
            acc_r = (target_rot - self.rot_val) * (k_stiff * 0.7) - self.rot_vel * (damp * 0.9)
            self.rot_vel += acc_r * dt
            self.rot_val += self.rot_vel * dt
            off_y = self.y_pos
            rot_dyn = self.rot_val
        elif style == "energetic":
            off_y = -bounce * 1.35 * a * abs(math.sin(now * 13.0))
            rot_dyn = sway * 1.3 * a * math.sin(now * 9.0)
        elif style == "subtle":
            off_y = -bounce * 0.45 * a * abs(math.sin(now * 6.5))
            rot_dyn = sway * 0.45 * a * math.sin(now * 4.5)
        else:  # bounce clásico
            off_y = -bounce * a * abs(math.sin(now * 10.0))
            rot_dyn = sway * a * math.sin(now * 7.0)

        # Flotación continua (levitación)
        if float_amp > 0.1:
            off_y += math.sin(now * float_spd * 2.0) * float_amp

        # Temblor expresivo al gritar (estado 3 = wide)
        off_x = 0.0
        if self.state >= 3 and shout_shake > 0.1:
            off_x += math.sin(now * 67.0) * shout_shake
            off_y += math.cos(now * 79.0) * shout_shake * 0.7

        # Seguimiento de mouse y webcam (paralaje e inclinación 2D)
        mt = float(ch.get("mouse_track", 0)) / 100.0
        if mt > 0.01:
            off_x += mouse_norm[0] * 34.0 * mt
            off_y += mouse_norm[1] * 22.0 * mt
            rot_dyn += mouse_norm[0] * 6.5 * mt

        if ch.get("face_track", False):
            fi = float(ch.get("face_intensity", 60)) / 100.0
            off_x += face_norm[0] * 55.0 * fi
            off_y += face_norm[1] * 38.0 * fi
            rot_dyn += face_norm[0] * 9.0 * fi

        # Respiración + Squash & Stretch
        sy = 1.0 + (breathe / 100.0) * math.sin(now * 2.2) - (squash / 100.0) * a * abs(math.sin(now * 10.0))
        if style == "spring":
            sy += max(-0.12, min(0.12, -self.y_vel * 0.00018))
        sx = 1.0 + (1.0 - sy) * 0.52

        # Foco al hablante activo (atenuación y sutil zoom al hablar)
        dim_target = 1.0
        if ch.get("dim_inactive", False) and self.state == 0 and someone_else_talking:
            dim_target = 1.0 - max(0.0, min(0.75, float(ch.get("dim_amount", 38)) / 100.0))
        self.dim_factor += (dim_target - self.dim_factor) * min(1.0, dt * 8.0)

        tz_amt = float(ch.get("talk_zoom", 0)) / 100.0
        face_z = (face_norm[2] * 0.12 * (float(ch.get("face_intensity", 60)) / 100.0)) if ch.get("face_track") else 0.0
        zoom_target = 1.0 + (tz_amt if self.state > 0 else 0.0) + face_z
        self.zoom_factor += (zoom_target - self.zoom_factor) * min(1.0, dt * 10.0)

        self.off_x = off_x
        self.off_y = off_y
        self.rot_val = rot_dyn
        self.sx = sx
        self.sy = sy
        self._last_mouse = mouse_norm
        self._last_face = face_norm
        self._last_emotion = emotion_state
        self._last_face_full = face_full

        # Física secundaria por accesorio acoplado (follow-through con inercia)
        for acc in ch.get("accessories", []):
            aid = acc["id"]
            st_a = self.acc_phys.setdefault(aid, [0.0, 0.0, 0.0, 0.0])
            mode = acc.get("physics_mode", "spring")
            amt = float(acc.get("physics_amount", 45)) / 100.0
            if mode == "spring":
                target_dy = -off_y * 0.45 * amt
                target_r = -rot_dyn * 0.85 * amt
                st_a[1] += ((target_dy - st_a[0]) * 110.0 - st_a[1] * 9.0) * dt
                st_a[0] += st_a[1] * dt
                st_a[3] += ((target_r - st_a[2]) * 95.0 - st_a[3] * 8.0) * dt
                st_a[2] += st_a[3] * dt
            elif mode == "sway":
                st_a[0] = 0.0
                st_a[2] = math.sin(now * 3.4) * 14.0 * amt + rot_dyn * 0.5
            elif mode == "float":
                st_a[0] = math.sin(now * 2.6 + hash(aid) % 7) * 18.0 * amt
                st_a[2] = math.cos(now * 1.9) * 5.0 * amt
            elif mode == "spin":
                st_a[0] = 0.0
                st_a[2] = (now * 120.0 * amt) % 360.0
            else:
                st_a[0] = st_a[2] = 0.0

        # Limpiar emotes expirados
        if self.emotes:
            self.emotes = [em for em in self.emotes if (now - em["born"]) < em["life"]]


# =====================================================  Dibujo de Props, Emotes y Escena completa
def draw_emotes(p, emotes, now, cx, top_y):
    if not emotes:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for em in emotes:
        age = now - em["born"]
        t = max(0.0, min(1.0, age / em["life"]))
        alpha = 1.0 - max(0.0, (t - 0.55) / 0.45)
        x = cx + em["x0"] + em["vx"] * age
        grav = 160.0 if em["kind"] == "confetti" else 18.0
        y = top_y + em["y0"] + em["vy"] * age + 0.5 * grav * age * age
        sc = min(1.0, t * 5.5) * (1.0 - 0.15 * t)
        p.save()
        p.setOpacity(max(0.0, min(1.0, alpha)))
        p.translate(x, y)
        p.rotate(em["rot"] + em["vrot"] * age)
        p.scale(sc, sc)
        col = QColor(em["color"])
        sz = em["size"]
        if em["kind"] == "confetti":
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(-sz * 0.35, -sz * 0.18, sz * 0.7, sz * 0.36), 2, 2)
        elif em["kind"] == "sweat":
            path = QPainterPath()
            path.moveTo(0, -sz * 0.55)
            path.cubicTo(sz * 0.45, 0, sz * 0.38, sz * 0.48, 0, sz * 0.52)
            path.cubicTo(-sz * 0.38, sz * 0.48, -sz * 0.45, 0, 0, -sz * 0.55)
            p.setBrush(col)
            p.setPen(QPen(QColor("#FFFFFF"), 1.6))
            p.drawPath(path)
        else:
            p.setPen(QPen(QColor("#101012"), 3))
            f = QFont("Segoe UI", int(sz * 0.72), QFont.Bold)
            p.setFont(f)
            r = QRectF(-sz, -sz, sz * 2, sz * 2)
            p.setPen(QColor("#101012"))
            p.drawText(r.translated(1.5, 2.0), Qt.AlignCenter, em["sym"])
            p.setPen(col)
            p.drawText(r, Qt.AlignCenter, em["sym"])
        p.restore()
    p.restore()


def draw_prop(p, prop, now, asset_mgr, spectrum=None, master_level=0.0):
    """Dibuja un prop de escena (Imagen/Logo, Texto/Lower Third o Visualizador de audio 2D)."""
    if not prop.get("visible", True):
        return
    w = float(prop.get("w", 360))
    h = float(prop.get("h", 120))
    sc = float(prop.get("scale", 1.0))
    rot = float(prop.get("rotation", 0.0))
    op = max(0.0, min(1.0, float(prop.get("opacity", 100)) / 100.0))
    anim = prop.get("anim", "none")
    amt = float(prop.get("anim_amount", 35)) / 100.0

    dy = 0.0
    sc_mod = 1.0
    if anim == "breathe":
        sc_mod = 1.0 + math.sin(now * 2.2) * 0.06 * amt
    elif anim == "float":
        dy = math.sin(now * 2.4) * 20.0 * amt
    elif anim == "pulse":
        sc_mod = 1.0 + master_level * 0.22 * amt

    p.save()
    p.setOpacity(p.opacity() * op)
    cx = float(prop.get("x", 0.0)) + (w * sc) * 0.5
    cy = float(prop.get("y", 0.0)) + (h * sc) * 0.5 + dy
    p.translate(cx, cy)
    p.rotate(rot)
    p.scale(sc * sc_mod, sc * sc_mod)
    p.translate(-w * 0.5, -h * 0.5)

    kind = prop.get("kind", "image")
    if kind == "image":
        path = M.resolve(prop.get("path", ""))
        info = asset_mgr.load_image_info(path)
        if info is not None:
            fr = asset_mgr.frame_at(info, now)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            # Encajar respetando relación de aspecto dentro de (w, h)
            iw, ih = fr.width(), fr.height()
            if iw > 0 and ih > 0:
                fit = min(w / iw, h / ih)
                dw, dh = iw * fit, ih * fit
                p.drawImage(QRectF((w - dw) * 0.5, (h - dh) * 0.5, dw, dh), fr)
        else:
            p.setPen(QPen(QColor("#52525B"), 2, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
            p.setPen(QColor("#9B9BA3"))
            p.setFont(QFont("Segoe UI", 14, QFont.DemiBold))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, prop.get("name", "Imagen"))

    elif kind == "text":
        r = QRectF(0, 0, w, h)
        rad = float(prop.get("radius", 10))
        pad = float(prop.get("padding", 16))
        if prop.get("bg_enabled", True):
            bg = QColor(prop.get("bg_color", "#141418"))
            bg.setAlpha(int(max(0, min(100, prop.get("bg_opacity", 78))) * 2.55))
            p.setPen(QPen(QColor(255, 255, 255, 24), 1.5))
            p.setBrush(bg)
            p.drawRoundedRect(r, rad, rad)
            if prop.get("accent_bar", True):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(prop.get("accent_color", "#2F7CF6")))
                p.drawRoundedRect(QRectF(6, 10, 5, max(8.0, h - 20)), 2.5, 2.5)
        align_map = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}
        al = align_map.get(prop.get("align", "center"), Qt.AlignHCenter) | Qt.AlignVCenter
        txt = prop.get("text", "")
        sub = prop.get("subtext", "")
        f = QFont(prop.get("font_family", "Segoe UI"), int(prop.get("font_size", 42)),
                  QFont.Bold if prop.get("bold", True) else QFont.Normal)
        p.setFont(f)
        tr = r.adjusted(pad + (8 if prop.get("accent_bar", True) else 0), pad * 0.4, -pad, -pad * 0.4)
        if sub:
            top_r = QRectF(tr.x(), tr.y(), tr.width(), tr.height() * 0.62)
            bot_r = QRectF(tr.x(), tr.y() + tr.height() * 0.60, tr.width(), tr.height() * 0.40)
            p.setPen(QColor(prop.get("color", "#FFFFFF")))
            p.drawText(top_r, al, txt)
            sf = QFont(prop.get("font_family", "Segoe UI"), max(12, int(prop.get("font_size", 42) * 0.48)))
            p.setFont(sf)
            p.setPen(QColor(255, 255, 255, 185))
            p.drawText(bot_r, al, sub)
        else:
            p.setPen(QColor(prop.get("color", "#FFFFFF")))
            p.drawText(tr, al, txt)

    elif kind == "visualizer":
        bars = max(8, min(64, int(prop.get("bars", 32))))
        sens = float(prop.get("sensitivity", 120)) / 100.0
        style = prop.get("style", "bars")
        c1 = QColor(prop.get("color", "#ECECEF"))
        c2 = QColor(prop.get("color2", "#2F7CF6"))
        rad = float(prop.get("roundness", 4))
        if spectrum is not None and len(spectrum) > 0:
            idx = np.linspace(0, len(spectrum) - 1, bars)
            vals = np.interp(idx, np.arange(len(spectrum)), spectrum) * sens
        else:
            vals = np.zeros(bars, dtype=np.float32)
        vals = np.clip(vals, 0.03, 1.0)
        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, c2)
        grad.setColorAt(1.0, c1)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        gap = max(2.0, (w / bars) * 0.28)
        bw = max(2.0, (w - gap * (bars - 1)) / bars)
        if style == "bars":
            for i, v in enumerate(vals):
                bh = max(4.0, float(v) * h)
                p.drawRoundedRect(QRectF(i * (bw + gap), h - bh, bw, bh), rad, rad)
        elif style == "mirror":
            mid = h * 0.5
            for i, v in enumerate(vals):
                bh = max(4.0, float(v) * h)
                p.drawRoundedRect(QRectF(i * (bw + gap), mid - bh * 0.5, bw, bh), rad, rad)
        elif style == "dots":
            mid = h * 0.5
            for i, v in enumerate(vals):
                r_dot = max(3.0, float(v) * min(bw, h * 0.45))
                cx_d = i * (bw + gap) + bw * 0.5
                p.drawEllipse(QPointF(cx_d, mid), r_dot, r_dot)
        elif style == "wave":
            path = QPainterPath()
            mid = h * 0.5
            for i, v in enumerate(vals):
                x = i * (w / max(1, bars - 1))
                sign = 1.0 if (i % 2 == 0) else -1.0
                y = mid + sign * float(v) * (h * 0.46)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QBrush(grad), max(2.5, bw * 0.45), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)

    p.restore()


def draw_character_full(p, ch, pl, anim_state, char_imgs, ref_size, now, asset_mgr, expr_idx=None):
    """Dibuja un personaje 2D VTuber completo: accesorios traseros, sprite con FX, accesorios delanteros y emotes."""
    if not pl.get("visible", True):
        return
    ref_w, ref_h = ref_size
    base_scale = float(pl.get("scale", 1.0)) * anim_state.zoom_factor
    base_rot = float(pl.get("rotation", 0.0))
    op = max(0.0, min(1.0, float(ch.get("opacity", 100)) / 100.0))

    # Soporte nativo para personajes VTuber 3D (.vt3d) y VTuber 2.5D riggeados (.vtr)
    rig3d_data = ch.get("rig3d_data")
    rig_data = ch.get("rig_data")
    if rig3d_data is not None and isinstance(rig3d_data, dict):
        import vtuber3d_engine as V3
        if not hasattr(anim_state, "_rig3d_state") or anim_state._rig3d_state is None:
            anim_state._rig3d_state = V3.VTuber3DState(seed=hash(ch["id"]) & 0xFFFF)
        st3 = anim_state._rig3d_state
        st3.mouth_state = anim_state.state
        st3.step(rig3d_data, max(anim_state.amp, anim_state.state / 3.0), now, 0.016,
                 mouse_norm=getattr(anim_state, "_last_mouse", (0.0, 0.0)),
                 face_full=getattr(anim_state, "_last_face_full", None),
                 emotion_state=getattr(anim_state, "_last_emotion", None))
        base_img = V3.render_vtuber3d_to_image(rig3d_data, st3, now, out_w=int(ref_w), out_h=int(ref_h), transparent=True)
        if ch.get("flip", False):
            base_img = base_img.mirrored(True, False)
        f_idx = int(now * 60)
    elif rig_data is not None and isinstance(rig_data, dict):
        import vtuber_rig as VR
        if not hasattr(anim_state, "_rig_state") or anim_state._rig_state is None:
            anim_state._rig_state = VR.RigRuntimeState(seed=hash(ch["id"]) & 0xFFFF)
        rs = anim_state._rig_state
        rs.mouth_state = anim_state.state
        rs.manual_mouth = anim_state.state
        rig_copy = rig_data
        rig_copy["expr"] = min(ch.get("expr", 0) if expr_idx is None else expr_idx, max(0, len(rig_copy.get("expressions", [])) - 1))
        rs.step(rig_copy, anim_state.amp, now, 0.016,
                mouse_norm=getattr(anim_state, "_last_mouse", (0.0, 0.0)),
                face_norm=getattr(anim_state, "_last_face", (0.0, 0.0, 0.0)), live_mode=True,
                emotion_state=getattr(anim_state, "_last_emotion", None),
                face_full=getattr(anim_state, "_last_face_full", None))
        base_img = VR.render_rig_to_image(rig_copy, rs, now, out_w=int(ref_w), out_h=int(ref_h), live_mode=True)
        if ch.get("flip", False):
            base_img = base_img.mirrored(True, False)
        f_idx = int(now * 60)
    else:
        # Elegir frame actual (incluyendo animación GIF/WebP)
        slot_entry = char_imgs.get(anim_state.cur_key)
        if slot_entry is None:
            fallback = anim_state.pick_key({k for k, v in char_imgs.items() if v is not None})
            slot_entry = char_imgs.get(fallback)
        if slot_entry is None:
            return
        frames, delays = slot_entry
        if len(frames) > 1:
            total_ms = sum(delays)
            t_ms = (now * 1000.0) % max(1, total_ms)
            acc = 0.0
            base_img = frames[-1]
            for fr, d in zip(frames, delays):
                acc += d
                if t_ms < acc:
                    base_img = fr
                    break
            f_idx = frames.index(base_img)
        else:
            base_img = frames[0]
            f_idx = 0

    # Efectos visuales (sombra, contorno reactivo, brillo/saturación + atenuación cuantizada para máxima velocidad)
    use_outline = bool(ch.get("outline", False)) and (
        not ch.get("outline_talk_only", True) or anim_state.state > 0
    )
    dim_br = int(round(((anim_state.dim_factor - 1.0) * 65.0) / 8.0) * 8)
    eff_br = max(-70, min(60, int(ch.get("brightness", 0)) + dim_br))
    eff_sat = int(ch.get("saturation", 0))
    if anim_state.dim_factor < 0.96:
        eff_sat = max(-100, eff_sat - int(round(((1.0 - anim_state.dim_factor) * 45.0) / 8.0) * 8))

    cache_tag = (ch["id"], ch["expr"] if expr_idx is None else expr_idx, anim_state.cur_key, f_idx, ch.get("flip", False))
    draw_img, (pad_x, pad_y) = asset_mgr.apply_sprite_fx(
        base_img, cache_tag,
        shadow=bool(ch.get("shadow", False)),
        shadow_blur=ch.get("shadow_blur", 14),
        shadow_alpha=ch.get("shadow_alpha", 45),
        shadow_dx=ch.get("shadow_dx", 0),
        shadow_dy=ch.get("shadow_dy", 12),
        outline=use_outline,
        outline_w=ch.get("outline_width", 6),
        outline_color=ch.get("outline_color", "#FFFFFF"),
        brightness=eff_br,
        saturation=eff_sat,
    )

    # Pivote
    pivot = ch.get("pivot", "bottom")
    piv_y = ref_h if pivot == "bottom" else (ref_h * 0.5 if pivot == "center" else 0.0)

    p.save()
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.setOpacity(p.opacity() * op)

    # Transformación 100% idéntica a QGraphicsItem (origin en centro local cx_local, cy_local)
    cx_local, cy_local = ref_w * 0.5, ref_h * 0.5
    p.translate(float(pl["x"]) + cx_local, float(pl["y"]) + cy_local)
    p.rotate(base_rot + anim_state.rot_val)
    p.scale(base_scale, base_scale)
    p.translate(-cx_local + anim_state.off_x, -cy_local + anim_state.off_y)
    p.translate(cx_local, piv_y)
    p.scale(anim_state.sx, anim_state.sy)
    p.translate(-cx_local, -piv_y)

    def _draw_accessories(layer_side):
        for acc in ch.get("accessories", []):
            if not acc.get("visible", True) or acc.get("z_rel", "front") != layer_side:
                continue
            apath = M.resolve(acc.get("path", ""))
            ainfo = asset_mgr.load_image_info(apath, max_dim=800)
            if ainfo is None:
                continue
            afr = asset_mgr.frame_at(ainfo, now)
            phys = anim_state.acc_phys.get(acc["id"], (0.0, 0.0, 0.0, 0.0))
            p.save()
            p.setOpacity(p.opacity() * max(0.0, min(1.0, float(acc.get("opacity", 100)) / 100.0)))
            ax = cx_local + float(acc.get("dx", 0.0))
            ay = cy_local + float(acc.get("dy", 0.0)) + phys[0]
            asc = float(acc.get("scale", 0.5))
            arot = float(acc.get("rotation", 0.0)) + phys[2]
            p.translate(ax, ay)
            p.rotate(arot)
            p.scale(asc, asc)
            iw, ih = afr.width(), afr.height()
            fit = min(ref_w / max(1, iw), ref_h / max(1, ih))
            dw, dh = iw * fit, ih * fit
            p.drawImage(QRectF(-dw * 0.5, -dh * 0.5, dw, dh), afr)
            p.restore()

    _draw_accessories("back")
    # Dibujar sprite principal compensando el padding de sombra/contorno
    dpr_x = base_img.width() / max(1.0, float(ref_w))
    dpr_y = base_img.height() / max(1.0, float(ref_h))
    dst_rect = QRectF(-pad_x / dpr_x, -pad_y / dpr_y, draw_img.width() / dpr_x, draw_img.height() / dpr_y)
    p.drawImage(dst_rect, draw_img)
    _draw_accessories("front")
    p.restore()

    # Emotes por encima del personaje en coordenadas de escena
    if anim_state.emotes:
        em_cx = float(pl["x"]) + cx_local
        em_top = float(pl["y"]) + cy_local - (ref_h * base_scale * 0.48)
        draw_emotes(p, anim_state.emotes, now, em_cx, em_top)


def render_scene_frame(painter, out_w, out_h, scene_data, project_data, char_states, char_bank,
                       now, asset_mgr, spectrum=None, master_level=0.0, transparent=False):
    """Renderiza una escena 2D completa (fondo + personajes + props ordenados por Z) a (out_w, out_h)."""
    cw = float(project_data.get("canvas_w", M.W))
    ch_h = float(project_data.get("canvas_h", M.H))

    bg = asset_mgr.render_background(scene_data, project_data.get("bg_color", "#121215"),
                                     out_w, out_h, transparent_override=transparent)
    painter.drawImage(0, 0, bg)

    painter.save()
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    # Escalado uniforme preservando aspecto (con recorte/centrado si el destino tiene otro ratio, ej. 9:16)
    sx = out_w / cw
    sy = out_h / ch_h
    if abs(sx - sy) < 0.01:
        painter.scale(sx, sy)
    else:
        sc = max(sx, sy)
        ox = (out_w - cw * sc) * 0.5
        oy = (out_h - ch_h * sc) * 0.5
        painter.translate(ox, oy)
        painter.scale(sc, sc)

    # Lista unificada ordenada por Z: personajes + props
    draw_list = []
    placements = scene_data.get("placements", {})
    for idx, ch in enumerate(project_data.get("characters", [])):
        cid = ch["id"]
        pl = placements.get(cid) or M.default_placement(idx, int(cw), int(ch_h))
        draw_list.append((float(pl.get("z", idx)), 0, cid, ch, pl))
    for pr in scene_data.get("props", []):
        draw_list.append((float(pr.get("z", 50)), 1, pr["id"], pr, None))
    draw_list.sort(key=lambda t: (t[0], t[1]))

    for _, kind_flag, oid, obj, pl in draw_list:
        if kind_flag == 0:
            st = char_states.get(oid)
            bank = char_bank.get(oid)
            if st is not None and bank is not None:
                ref_sz, imgs, expr_i = bank
                draw_character_full(painter, obj, pl, st, imgs, ref_sz, now, asset_mgr, expr_idx=expr_i)
        else:
            sp = spectrum
            lvl = master_level
            draw_prop(painter, obj, now, asset_mgr, spectrum=sp, master_level=lvl)

    painter.restore()
