"""Toma de grabación: pistas, cortes (estilo Audition), mezcla y exportación."""
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

ENV_HZ, FPS = 20, 30


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "pista"


class Take:
    def __init__(self, folder, mode):
        self.folder, self.mode = Path(folder), mode
        self.tracks = {}          # cid -> {name, color, env}
        self.cuts, self.duration = [], 0.0
        self.recording, self.busy, self.has_video = True, False, False

    def add_track(self, cid, name, color):
        self.tracks[cid] = {"name": name, "color": color, "env": []}

    def wav_of(self, cid):
        return self.folder / f"{safe(self.tracks[cid]['name'])}.wav"

    def add_cut(self, a, b):
        a, b = max(0.0, min(a, b)), min(self.duration, max(a, b))
        if b - a > 0.02:
            self.cuts.append((a, b))

    def undo_cut(self):
        if self.cuts:
            self.cuts.pop()

    def merged_cuts(self):
        out = []
        for a, b in sorted(self.cuts):
            if out and a <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out


# ---------------- utilidades WAV / video ----------------
def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), np.int16)


def _write_wav(path, sr, data):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.ascontiguousarray(data).tobytes())


def _merge_timeline_wavs(take):
    """Combina los archivos temporales _tl_{cid}.wav de la línea de tiempo con la pista de cada personaje."""
    for cid in take.tracks:
        tl_p = take.folder / f"_tl_{cid}.wav"
        if not tl_p.exists():
            continue
        dst = take.wav_of(cid)
        try:
            sr_t, d_t = _read_wav(tl_p)
            has_sound = len(d_t) > 0 and int(np.abs(d_t).max()) > 0
            if not dst.exists():
                if has_sound:
                    _write_wav(dst, sr_t, d_t)
            elif has_sound:
                sr_m, d_m = _read_wav(dst)
                a = d_m.astype(np.float32)
                b = d_t.astype(np.float32)
                if sr_t != sr_m and len(b) > 1:
                    b = np.interp(np.linspace(0, len(b) - 1, int(len(b) * sr_m / sr_t)), np.arange(len(b)), b)
                mix = np.zeros(max(len(a), len(b)), np.float32)
                mix[:len(a)] += a
                mix[:len(b)] += b
                peak = float(np.abs(mix).max())
                if peak > 32767:
                    mix *= 32767 / peak
                _write_wav(dst, sr_m, mix.astype(np.int16))
        finally:
            try:
                tl_p.unlink()
            except Exception:
                pass


def _cut_samples(data, rate, cuts):
    keep, pos = [], 0
    for a, b in cuts:
        ia, ib = int(a * rate), int(b * rate)
        if ia > pos:
            keep.append(data[pos:ia])
        pos = max(pos, ib)
    keep.append(data[pos:])
    return np.concatenate(keep) if keep else data[:0]


def _cut_video(path, cuts):
    if cv2 is None or not Path(path).exists():
        return
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    tmp = Path(path).with_suffix(".tmp.mp4")
    wr = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    ranges, i = [(int(a * fps), int(b * fps)) for a, b in cuts], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if not any(a <= i < b for a, b in ranges):
            wr.write(frame)
        i += 1
    cap.release()
    wr.release()
    tmp.replace(path)


def apply_cuts(take):
    cuts = take.merged_cuts()
    if not cuts:
        return
    for wav in take.folder.glob("*.wav"):
        sr, d = _read_wav(wav)
        _write_wav(wav, sr, _cut_samples(d, sr, cuts))
    _cut_video(take.folder / "Video.mp4", cuts)
    for tr in take.tracks.values():
        tr["env"] = list(_cut_samples(np.array(tr["env"], np.float32), ENV_HZ, cuts))
    take.duration -= sum(min(b, take.duration) - a for a, b in cuts)
    take.cuts = []


def make_mix(take):
    arrs, sr0 = [], None
    for cid in take.tracks:
        p = take.wav_of(cid)
        if not p.exists():
            continue
        sr, d = _read_wav(p)
        d = d.astype(np.float32)
        if sr0 is None:
            sr0 = sr
        elif sr != sr0 and len(d) > 1:
            d = np.interp(np.linspace(0, len(d) - 1, int(len(d) * sr0 / sr)), np.arange(len(d)), d)
        arrs.append(d)
    if not arrs:
        return
    mix = np.zeros(max(len(a) for a in arrs), np.float32)
    for a in arrs:
        mix[:len(a)] += a
    peak = float(np.abs(mix).max())
    if peak > 32767:
        mix *= 32767 / peak
    _write_wav(take.folder / "Mezcla.wav", sr0, mix.astype(np.int16))


def mux(take):
    ff, video, audio = shutil.which("ffmpeg"), take.folder / "Video.mp4", take.folder / "Mezcla.wav"
    final = take.folder / "Final.mp4"
    if final.exists():
        final.unlink()
    if ff and video.exists() and audio.exists():
        subprocess.run([ff, "-y", "-i", str(video), "-i", str(audio), "-c:v", "copy", "-c:a", "aac",
                        "-shortest", str(final)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def finalize(take):
    """Combina audio de línea de tiempo, aplica cortes, genera mezcla y une audio+video si hay ffmpeg."""
    take.busy = True
    try:
        _merge_timeline_wavs(take)
        apply_cuts(take)
        separate = [take.wav_of(c) for c in take.tracks if take.wav_of(c).exists()]
        mixed = take.folder / "Mezcla.wav"
        if separate and take.mode in ("mixed", "both"):
            make_mix(take)
        if take.mode == "separate" and mixed.exists():
            mixed.unlink()
        if take.mode == "mixed" and mixed.exists():
            for p in separate:
                p.unlink()
        mux(take)
    finally:
        take.busy = False
