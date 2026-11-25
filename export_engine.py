"""Motor de exportación de alto rendimiento en segundo plano (QThread):
- Detección automática de GPU (NVENC / AMF / QSV) con respaldo libx264 multihilo.
- Pipe directo en formato nativo BGRA sin conversión extra + deduplicación inteligente de frames estáticos.
- Soporte para Video+Audio MP4, Shorts 9:16, Overlay Transparente (WebM VP9 Alpha / MOV RGBA), Solo Audio (WAV/FLAC/MP3/OGG).
- Empaquetado ordenado por carpetas, stems numerados, normalización -16 LUFS, miniatura PNG y capítulos YouTube."""
import copy
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QRectF, QThread, Qt, Signal
from PySide6.QtGui import QImage, QPainter

import model as M
from audio_engine import export_timeline_audio, get_ffmpeg, render_timeline_levels
from timeline import fmt_tc
from vtuber_engine import CharAnimState, SHARED_ASSETS, render_scene_frame

_GPU_CACHE = None


def _safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Proyecto"


def detect_gpu_encoder():
    """Prueba rápidamente si existe un codificador H.264 por hardware funcional en este equipo."""
    global _GPU_CACHE
    if _GPU_CACHE is not None:
        return _GPU_CACHE
    ff = get_ffmpeg()
    if not ff:
        _GPU_CACHE = (None, "ffmpeg no encontrado")
        return _GPU_CACHE
    candidates = [
        ("h264_nvenc", "NVIDIA NVENC (GPU)"),
        ("h264_amf", "AMD AMF (GPU)"),
        ("h264_qsv", "Intel QuickSync (GPU)"),
    ]
    for codec, label in candidates:
        try:
            cmd = [
                ff, "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=128x128:d=0.04",
                "-frames:v", "1", "-c:v", codec, "-f", "null", "-",
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=3.0,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                _GPU_CACHE = (codec, label)
                return _GPU_CACHE
        except Exception:
            continue
    _GPU_CACHE = (None, "CPU (libx264 multihilo)")
    return _GPU_CACHE


def _fmt_yt_tc(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


class ExportWorker(QThread):
    """Hilo dedicado de exportación: nunca congela la interfaz, cancelable al instante."""
    progress = Signal(int, int, str, float, float)   # frame, total, stage, fps_speed, eta_s
    previewReady = Signal(QImage)
    finishedOk = Signal(str, list, str)              # root_dir, written_paths, summary_text
    failed = Signal(str)

    def __init__(self, config, project_data, tracks, tl_clips, sr):
        super().__init__()
        self.cfg = dict(config)
        self.project_data = copy.deepcopy(project_data)
        self.tracks = copy.deepcopy(tracks)
        # Referencia ligera a los clips (los arrays NumPy son de solo lectura durante el export)
        self.tl_clips = list(tl_clips)
        self.sr = int(sr)
        self._cancel = False
        self._proc = None

    def cancel(self):
        self._cancel = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="podcastset_exp_"))
        log_fh = None
        try:
            self._run_export(tmp_dir)
        except Exception as e:
            if not self._cancel:
                self.failed.emit(str(e))
        finally:
            if log_fh:
                try:
                    log_fh.close()
                except Exception:
                    pass
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _run_export(self, tmp_dir):
        cfg = self.cfg
        mode = cfg["mode"]  # "both" | "vertical" | "alpha" | "video" | "audio"
        t0, t1 = float(cfg["t0"]), float(cfg["t1"])
        base = _safe_name(cfg.get("base_name", "Podcast"))
        out_root = Path(cfg["out_dir"])
        organize = bool(cfg.get("organize", True))
        normalize = bool(cfg.get("normalize", True))
        make_thumb = bool(cfg.get("make_thumb", True))
        extra_stems = bool(cfg.get("extra_stems", False))
        extra_mix = bool(cfg.get("extra_mix", False))
        aud_fmt = cfg.get("audio_fmt", "wav")
        aud_mode = cfg.get("audio_mode", "both")

        if organize:
            stamp = time.strftime("%Y-%m-%d_%H%M")
            dest_dir = out_root / f"{base}_{stamp}"
        else:
            dest_dir = out_root
        dest_dir.mkdir(parents=True, exist_ok=True)
        written = []
        t_wall0 = time.time()

        # ---------------- SOLO AUDIO ----------------
        if mode == "audio":
            self.progress.emit(15, 100, "Mezclando y normalizando pistas de audio...", 0.0, 0.0)
            audio_dir = (dest_dir / "Audio") if organize else dest_dir
            stems_sub = "Voces" if (organize and aud_mode in ("separate", "both")) else None
            w_aud, _ = export_timeline_audio(
                self.tl_clips, self.tracks, self.project_data["characters"], audio_dir,
                base_name=base, mode=aud_mode, fmt=aud_fmt,
                t_start=t0, t_end=t1, sr=self.sr, normalize=normalize, stems_subfolder=stems_sub,
            )
            written.extend(w_aud)
            self._write_chapters_and_report(dest_dir, written, t0, t1, time.time() - t_wall0, organize)
            self.progress.emit(100, 100, "Exportación de audio completada", 0.0, 0.0)
            self.finishedOk.emit(str(dest_dir), [str(p) for p in written], self._summary_text(written, time.time() - t_wall0))
            return

        # ---------------- VIDEO (MP4 / Vertical 9:16 / Overlay Transparente) ----------------
        ff = get_ffmpeg()
        if not ff:
            raise RuntimeError("No se encontró ffmpeg. Instala imageio-ffmpeg para exportar video.")

        w, h = int(cfg["w"]), int(cfg["h"])
        # Dimensiones pares obligatorias para yuv420p
        w = w - (w % 2)
        h = h - (h % 2)
        fps = int(cfg["fps"])
        is_alpha = (mode == "alpha")
        has_audio = mode in ("both", "vertical") or (is_alpha and cfg.get("alpha_audio", False))

        self.progress.emit(0, 100, "Analizando niveles de voz y audio...", 0.0, 0.0)
        has_viz = any(
            any(pr.get("kind") == "visualizer" and pr.get("visible", True) for pr in sc.get("props", []))
            for sc in self.project_data["scenes"]
        )
        if has_viz:
            levels, n_frames, master_spec = render_timeline_levels(
                self.tl_clips, self.tracks, self.project_data["characters"], t0, t1, fps,
                sr=self.sr, want_spectrum=True,
            )
        else:
            levels, n_frames = render_timeline_levels(
                self.tl_clips, self.tracks, self.project_data["characters"], t0, t1, fps,
                sr=self.sr, want_spectrum=False,
            )
            master_spec = None

        if self._cancel:
            return

        mix_wav = None
        if has_audio:
            self.progress.emit(0, n_frames, "Preparando mezcla de audio...", 0.0, 0.0)
            _, mix_wav = export_timeline_audio(
                self.tl_clips, self.tracks, self.project_data["characters"], tmp_dir,
                base_name=base, mode="mixed", fmt="wav",
                t_start=t0, t_end=t1, sr=self.sr, normalize=normalize,
            )
            if extra_mix and mix_wav and mix_wav.exists():
                audio_dir = (dest_dir / "Audio") if organize else dest_dir
                audio_dir.mkdir(parents=True, exist_ok=True)
                copy_mix = audio_dir / f"{base}_Mezcla.wav"
                shutil.copy2(mix_wav, copy_mix)
                written.append(copy_mix)
            if extra_stems:
                audio_dir = (dest_dir / "Audio") if organize else dest_dir
                stems, _ = export_timeline_audio(
                    self.tl_clips, self.tracks, self.project_data["characters"], audio_dir,
                    base_name=base, mode="separate", fmt=aud_fmt,
                    t_start=t0, t_end=t1, sr=self.sr, normalize=normalize,
                    stems_subfolder="Voces" if organize else None,
                )
                written.extend(stems)

        if self._cancel:
            return

        # Reutilizar caché de sprites ya decodificados (hilo-seguro) y cargar solo expresiones usadas
        self.progress.emit(0, n_frames, "Preparando sprites y escenas...", 0.0, 0.0)
        asset_mgr = SHARED_ASSETS
        chars = self.project_data["characters"]
        scenes = self.project_data["scenes"]
        char_states = {ch["id"]: CharAnimState(seed=1000 + i) for i, ch in enumerate(chars)}
        char_expr_cur = {ch["id"]: ch.get("expr", 0) for ch in chars}
        used_exprs = {ch["id"]: {ch.get("expr", 0)} for ch in chars}
        for mk in self.project_data.get("markers", []):
            if mk.get("kind") == "expr" and mk.get("target_id") in used_exprs:
                used_exprs[mk["target_id"]].add(int(mk.get("value", 0)))
        char_bank_all = {}
        for ch in chars:
            for eidx in used_exprs[ch["id"]]:
                eidx = max(0, min(len(ch["expressions"]) - 1, eidx))
                rw, rh, imgs, _ = asset_mgr.build_character_images(ch, expr_idx=eidx, dpr=1)
                char_bank_all[(ch["id"], eidx)] = ((rw, rh), imgs, eidx)

        markers = sorted(self.project_data.get("markers", []), key=lambda m: float(m.get("time", 0.0)))
        mk_cursor = 0
        cur_scene_idx = int(cfg.get("start_scene", 0))
        # Aplicar marcadores anteriores o en t0
        while mk_cursor < len(markers) and float(markers[mk_cursor].get("time", 0.0)) <= t0:
            mk = markers[mk_cursor]
            if mk.get("kind") == "scene" and isinstance(mk.get("value"), int):
                cur_scene_idx = max(0, min(len(scenes) - 1, mk["value"]))
            elif mk.get("kind") == "expr" and mk.get("target_id") in char_expr_cur:
                char_expr_cur[mk["target_id"]] = int(mk.get("value", 0))
            mk_cursor += 1

        # Configurar comando ffmpeg
        alpha_codec = cfg.get("alpha_codec", "webm")
        if is_alpha:
            if alpha_codec == "mov":
                final_vid = dest_dir / f"{base}_Transparente.mov"
                in_pix = "rgba"
                v_args = ["-c:v", "qtrle", "-pix_fmt", "argb"]
            else:
                final_vid = dest_dir / f"{base}_Transparente.webm"
                in_pix = "rgba"
                v_args = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24", "-deadline", "realtime", "-cpu-used", "5"]
        else:
            final_vid = dest_dir / f"{base}.mp4"
            in_pix = "bgra"
            qual = cfg.get("quality", "balanced")  # "fast" | "balanced" | "high"
            use_gpu = bool(cfg.get("use_gpu", True))
            gpu_codec, _ = detect_gpu_encoder() if use_gpu else (None, "")
            if gpu_codec == "h264_nvenc":
                cq = {"fast": "26", "balanced": "22", "high": "18"}[qual]
                pr = {"fast": "p1", "balanced": "p4", "high": "p6"}[qual]
                v_args = ["-c:v", "h264_nvenc", "-preset", pr, "-cq", cq, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            elif gpu_codec == "h264_amf":
                qp = {"fast": "26", "balanced": "22", "high": "18"}[qual]
                v_args = ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            elif gpu_codec == "h264_qsv":
                gq = {"fast": "26", "balanced": "22", "high": "18"}[qual]
                v_args = ["-c:v", "h264_qsv", "-global_quality", gq, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            else:
                crf = {"fast": "25", "balanced": "21", "high": "17"}[qual]
                pr = {"fast": "ultrafast", "balanced": "veryfast", "high": "medium"}[qual]
                v_args = ["-c:v", "libx264", "-preset", pr, "-crf", crf, "-threads", "0", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]

        cmd = [
            ff, "-y", "-hide_banner",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", in_pix,
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
        ]
        if has_audio and mix_wav and mix_wav.exists():
            a_codec = ["-c:a", "libopus", "-b:a", "192k"] if (is_alpha and alpha_codec == "webm") else ["-c:a", "aac", "-b:a", "256k"]
            cmd += ["-i", str(mix_wav)] + a_codec + ["-shortest"]
        cmd += v_args + [str(final_vid)]

        log_path = tmp_dir / "ffmpeg.log"
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_fh,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._proc = proc

        # Buffer de render reutilizable
        frame_img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        prev_scene_img = None
        trans_alpha = 0.0
        trans_mode = "fade"
        trans_dur = 0.28

        prev_sig = None
        prev_bytes = None
        dt = 1.0 / float(fps)
        thumb_frame = min(n_frames - 1, max(0, int(n_frames * 0.15)))
        thumb_saved = False
        t_render0 = time.time()
        last_ui_emit = 0.0

        for f in range(n_frames):
            if self._cancel:
                break
            sim_t = t0 + f * dt

            # Evaluar eventos de Dirección en el timeline
            while mk_cursor < len(markers) and float(markers[mk_cursor].get("time", 0.0)) <= sim_t:
                mk = markers[mk_cursor]
                kind = mk.get("kind")
                if kind == "scene" and isinstance(mk.get("value"), int):
                    new_idx = max(0, min(len(scenes) - 1, mk["value"]))
                    if new_idx != cur_scene_idx:
                        prev_scene_img = frame_img.copy()
                        cur_scene_idx = new_idx
                        sc_new = scenes[cur_scene_idx]
                        trans_mode = sc_new.get("transition", "fade")
                        trans_dur = max(0.05, float(sc_new.get("transition_ms", 280)) / 1000.0)
                        trans_alpha = 0.0 if trans_mode == "cut" else 1.0
                elif kind == "expr" and mk.get("target_id") in char_expr_cur:
                    char_expr_cur[mk["target_id"]] = int(mk.get("value", 0))
                elif kind == "emote" and mk.get("target_id") in char_states:
                    char_states[mk["target_id"]].trigger_emote(str(mk.get("value", "star")), sim_t)
                mk_cursor += 1

            # Avanzar física 2D de todos los personajes
            frame_lvls = {ch["id"]: float(levels[ch["id"]][f]) if ch["id"] in levels else 0.0 for ch in chars}
            talking_cids = {
                ch["id"] for ch in chars
                if not ch.get("mute") and frame_lvls[ch["id"]] * 100.0 > ch["threshold"]
            }
            master_lvl = max(frame_lvls.values(), default=0.0)
            sig_parts = [cur_scene_idx, int(trans_alpha * 100)]

            cur_bank = {}
            for ch in chars:
                cid = ch["id"]
                eidx = min(char_expr_cur[cid], len(ch["expressions"]) - 1)
                bank = char_bank_all[(cid, eidx)]
                cur_bank[cid] = bank
                avail = {k for k, v in bank[1].items() if v is not None}
                st = char_states[cid]
                other_talk = bool(talking_cids - {cid})
                st.step(ch, frame_lvls[cid], sim_t, dt, avail, someone_else_talking=other_talk)
                # Firma compacta para detectar frames idénticos
                slot_v = bank[1].get(st.cur_key)
                is_gif = bool(slot_v and len(slot_v[0]) > 1) or bool(ch.get("rig_data")) or bool(ch.get("rig3d_data"))
                sig_parts.append((
                    eidx, st.cur_key, round(st.off_x, 1), round(st.off_y, 1),
                    round(st.rot_val, 1), round(st.sx, 3), round(st.sy, 3),
                    round(st.dim_factor, 2), round(st.zoom_factor, 3),
                    len(st.emotes), int(sim_t * 24) if is_gif else 0,
                ))

            sc_cur = scenes[cur_scene_idx]
            has_dyn_prop = any(
                pr.get("visible", True) and (pr.get("kind") == "visualizer" or pr.get("anim", "none") != "none")
                for pr in sc_cur.get("props", [])
            )
            sig = None if has_dyn_prop else tuple(sig_parts)

            if sig is not None and sig == prev_sig and prev_bytes is not None:
                raw_bytes = prev_bytes
            else:
                frame_img.fill(Qt.transparent if is_alpha else Qt.black)
                p = QPainter(frame_img)
                sp_arr = master_spec[f] if master_spec is not None else None
                render_scene_frame(
                    p, w, h, sc_cur, self.project_data, char_states, cur_bank,
                    sim_t, asset_mgr, spectrum=sp_arr, master_level=master_lvl, transparent=is_alpha,
                )
                if prev_scene_img is not None and trans_alpha > 0.005:
                    p.save()
                    p.setOpacity(trans_alpha)
                    if trans_mode == "slide":
                        p.drawImage(int(-(1.0 - trans_alpha) * w), 0, prev_scene_img)
                    else:
                        p.drawImage(0, 0, prev_scene_img)
                    p.restore()
                p.end()

                if is_alpha:
                    rgba = frame_img.convertToFormat(QImage.Format_RGBA8888)
                    raw_bytes = bytes(memoryview(rgba.constBits())[:w * h * 4])
                else:
                    raw_bytes = bytes(memoryview(frame_img.constBits())[:w * h * 4])
                prev_sig = sig
                prev_bytes = raw_bytes

            if trans_alpha > 0.0:
                trans_alpha = max(0.0, trans_alpha - dt / trans_dur)
                if trans_alpha <= 0.001:
                    prev_scene_img = None

            proc.stdin.write(raw_bytes)

            if make_thumb and not thumb_saved and f >= thumb_frame:
                thumb_saved = True
                thumb_p = dest_dir / "Miniatura.png"
                frame_img.save(str(thumb_p), "PNG")
                written.append(thumb_p)

            now_wall = time.time()
            if now_wall - last_ui_emit >= 0.14 or f == n_frames - 1:
                last_ui_emit = now_wall
                el = max(0.05, now_wall - t_render0)
                spd = (f + 1) / el
                eta = (n_frames - (f + 1)) / max(0.5, spd)
                self.progress.emit(f + 1, n_frames, f"Renderizando {w}×{h} ({fps} fps)...", spd, eta)
                if f % max(1, fps) == 0 or f == 0:
                    self.previewReady.emit(frame_img.scaled(320, 180, Qt.KeepAspectRatio, Qt.FastTransformation))

        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass

        if self._cancel:
            try:
                proc.terminate()
            except Exception:
                pass
            log_fh.close()
            return

        self.progress.emit(n_frames, n_frames, "Finalizando empaquetado de video...", 0.0, 0.0)
        rc = proc.wait()
        log_fh.close()
        self._proc = None

        if rc != 0 or not final_vid.exists():
            err = log_path.read_text(encoding="utf-8", errors="ignore")[-450:] if log_path.exists() else "Error desconocido"
            raise RuntimeError(f"ffmpeg devolvió código {rc}:\n{err}")

        written.insert(0, final_vid)
        elapsed = time.time() - t_wall0
        self._write_chapters_and_report(dest_dir, written, t0, t1, elapsed, organize)
        self.finishedOk.emit(str(dest_dir), [str(p) for p in written], self._summary_text(written, elapsed))

    def _write_chapters_and_report(self, dest_dir, written, t0, t1, elapsed, organize):
        # Capítulos de YouTube a partir de marcadores de capítulo / escena
        chaps = [m for m in sorted(self.project_data.get("markers", []), key=lambda x: x.get("time", 0.0))
                 if m.get("kind") in ("chapter", "scene") and t0 <= float(m.get("time", 0.0)) <= t1]
        if chaps:
            lines = ["00:00 Inicio"] if float(chaps[0]["time"]) - t0 > 1.0 else []
            for m in chaps:
                lines.append(f"{_fmt_yt_tc(float(m['time']) - t0)} {m.get('label', 'Capítulo')}")
            cp = dest_dir / "capitulos_youtube.txt"
            cp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.append(cp)

        if organize:
            rep = [
                f"Proyecto: {self.project_data.get('project_name', 'Sin título')}",
                f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Duración exportada: {fmt_tc(t1 - t0, True)} ({fmt_tc(t0, True)} -> {fmt_tc(t1, True)})",
                f"Tiempo de render: {elapsed:.1f}s",
                f"Normalización audio (-16 LUFS): {'Sí' if self.cfg.get('normalize') else 'No'}",
                "",
                "Archivos generados:",
            ]
            for p in written:
                try:
                    rel = Path(p).relative_to(dest_dir)
                except Exception:
                    rel = Path(p).name
                sz_mb = Path(p).stat().st_size / (1024 * 1024) if Path(p).exists() else 0.0
                rep.append(f"  - {rel}  ({sz_mb:.2f} MB)")
            rp = dest_dir / "resumen_exportacion.txt"
            rp.write_text("\n".join(rep) + "\n", encoding="utf-8")

    def _summary_text(self, written, elapsed):
        return f"Completado en {elapsed:.1f}s · {len(written)} archivo(s) listos"
