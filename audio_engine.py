"""Motor de audio optimizado: DSP vectorizado estilo OBS, MicHub compartido, carga asíncrona,
mezclador multipista con fades, espectro en vivo, niveles vectorizados y normalización -16 LUFS."""
import json
import math
import re
import shutil
import socket
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from voice_emotion import VoiceEmotionAnalyzer

try:
    import soundfile as sf
except Exception:
    sf = None

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None

_BAD = ("sound mapper", "primary sound", "controlador primario", "asignador de sonido", "stereo mix")
PEAKS_HZ = 50
SPEC_BINS = 32
_AUDIO_CACHE = {}
_CACHE_MAX_ITEMS = 24
_CACHE_LOCK = threading.Lock()


def get_ffmpeg():
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


def _safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "pista"


def get_output_samplerate():
    try:
        idx = sd.default.device[1]
        if idx is not None and idx >= 0:
            sr = int(sd.query_devices(idx)["default_samplerate"])
            if 8000 <= sr <= 192000:
                return sr
    except Exception:
        pass
    return 44100


def list_input_devices():
    out, seen = [], set()
    try:
        apis = sd.query_hostapis()
        names = [a["name"] for a in apis]
        pref = next((i for i, n in enumerate(names) if "WASAPI" in n), None)
        if pref is None:
            pref = sd.default.hostapi if sd.default.hostapi >= 0 else 0
        for i, d in enumerate(sd.query_devices()):
            if d["hostapi"] != pref or d["max_input_channels"] <= 0:
                continue
            name = d["name"].strip()
            if any(b in name.lower() for b in _BAD) or name in seen:
                continue
            seen.add(name)
            out.append((name, i))
    except Exception:
        pass
    return out


def level_from(x, gain=1.0):
    if len(x) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(x * x))) * gain
    db = 20.0 * math.log10(max(rms, 1e-6))
    return float(max(0.0, min(1.0, (db + 60.0) / 60.0)))


def compute_spectrum(mono_block, sr, n_bins=SPEC_BINS):
    """Calcula n_bins bandas logarítmicas (80Hz..12kHz) normalizadas 0..1 en <0.1ms."""
    n = len(mono_block)
    if n < 32:
        return np.zeros(n_bins, dtype=np.float32)
    win = np.hanning(n).astype(np.float32)
    mag = np.abs(np.fft.rfft(mono_block * win)) / (n * 0.5)
    freqs = np.fft.rfftfreq(n, 1.0 / max(8000, sr))
    edges = np.geomspace(80.0, min(12000.0, sr * 0.45), n_bins + 1)
    out = np.zeros(n_bins, dtype=np.float32)
    idx = np.searchsorted(freqs, edges)
    for i in range(n_bins):
        a = int(idx[i])
        b = max(a + 1, int(idx[i + 1]))
        if a < len(mag):
            val = float(mag[a:min(b, len(mag))].mean())
            db = 20.0 * math.log10(max(val, 1e-6))
            out[i] = max(0.0, min(1.0, (db + 58.0) / 50.0))
    return out


def gain_to_db(g):
    if g <= 0.001:
        return "-∞ dB"
    return f"{20.0 * math.log10(g):+.1f} dB"


# =====================================================  DSP modular vectorizado (0 bucles Python)
class MicProcessor:
    """Cadena modular de filtros de micrófono estilo OBS completamente vectorizada en NumPy."""

    def __init__(self, filters=None):
        self._filters = list(filters or [])
        self._lock = threading.Lock()
        self._state = {}

    def update(self, filters):
        with self._lock:
            self._filters = [dict(f, params=dict(f.get("params", {}))) for f in (filters or [])]

    def process(self, x_float, sr):
        n = len(x_float)
        if n == 0:
            return x_float
        with self._lock:
            chain = list(self._filters)
        if not chain:
            return x_float
        y = x_float.astype(np.float32, copy=True)
        freqs = None

        for flt in chain:
            if not flt.get("enabled", True):
                continue
            ft = flt.get("type")
            p = flt.get("params", {})
            st = self._state.setdefault(flt.get("id", ft), {})

            if ft == "highpass":
                # Pasa-altos Butterworth 2º orden vectorizado en frecuencia (<0.05ms, sin bucle Python)
                fc = max(20.0, min(400.0, float(p.get("hz", 90))))
                if n >= 32:
                    if freqs is None:
                        freqs = np.fft.rfftfreq(n, 1.0 / max(8000, sr))
                    spec = np.fft.rfft(y)
                    ratio = fc / np.maximum(freqs, 1e-3)
                    resp = (1.0 / np.sqrt(1.0 + ratio ** 4)).astype(np.float32)
                    resp[0] = 0.0
                    y = np.fft.irfft(spec * resp, n=n).astype(np.float32)

            elif ft == "noise_suppress":
                strength = max(0.0, min(1.0, float(p.get("strength", 65)) / 100.0))
                if strength > 0.01 and n >= 64:
                    spec = np.fft.rfft(y)
                    mag = np.abs(spec)
                    nf = st.get("nf")
                    if nf is None or len(nf) != len(mag):
                        nf = mag.copy() * 0.5
                    else:
                        nf = np.minimum(nf * 0.96 + mag * 0.04, mag * 1.15)
                    st["nf"] = nf
                    gain_mask = np.clip(1.0 - (strength * 1.35 * nf) / (mag + 1e-6), 0.04, 1.0)
                    y = np.fft.irfft(spec * gain_mask, n=n).astype(np.float32)

            elif ft == "noise_gate":
                rms = float(np.sqrt(np.mean(y * y)))
                db = 20.0 * math.log10(max(rms, 1e-6))
                open_db = float(p.get("open_db", -38))
                close_db = min(open_db - 2.0, float(p.get("close_db", -46)))
                is_open = st.get("open", False)
                if db >= open_db:
                    is_open = True
                elif db <= close_db:
                    is_open = False
                st["open"] = is_open
                env0 = float(st.get("env", 1.0))
                target = 1.0 if is_open else 0.0
                env1 = env0 + (target - env0) * (0.45 if target > env0 else 0.16)
                st["env"] = env1
                if abs(env1 - env0) > 1e-4:
                    y *= np.linspace(env0, env1, n, dtype=np.float32)
                else:
                    y *= env1

            elif ft == "compressor":
                thr_db = float(p.get("threshold_db", -18))
                ratio = max(1.0, float(p.get("ratio", 3.5)))
                makeup_db = float(p.get("makeup_db", 3.0))
                thr_lin = 10.0 ** (thr_db / 20.0)
                ay = np.abs(y)
                over = ay > thr_lin
                if np.any(over):
                    y[over] = np.sign(y[over]) * (thr_lin + (ay[over] - thr_lin) / ratio)
                if abs(makeup_db) > 0.05:
                    y *= 10.0 ** (makeup_db / 20.0)

            elif ft == "eq3":
                low_db = float(p.get("low_db", 0.0))
                mid_db = float(p.get("mid_db", 0.0))
                high_db = float(p.get("high_db", 0.0))
                if (abs(low_db) > 0.1 or abs(mid_db) > 0.1 or abs(high_db) > 0.1) and n >= 64:
                    if freqs is None:
                        freqs = np.fft.rfftfreq(n, 1.0 / max(8000, sr))
                    spec = np.fft.rfft(y)
                    # Transiciones suaves entre bandas para evitar artefactos
                    g = np.ones_like(freqs, dtype=np.float32)
                    low_w = 1.0 / (1.0 + (freqs / 250.0) ** 4)
                    high_w = 1.0 / (1.0 + (3000.0 / np.maximum(freqs, 1.0)) ** 4)
                    mid_w = np.clip(1.0 - low_w - high_w, 0.0, 1.0)
                    db_curve = low_w * low_db + mid_w * mid_db + high_w * high_db
                    g *= (10.0 ** (db_curve / 20.0)).astype(np.float32)
                    y = np.fft.irfft(spec * g, n=n).astype(np.float32)

            elif ft == "pitch_shift":
                semi = float(p.get("semitones", 0.0))
                mix = max(0.0, min(1.0, float(p.get("mix", 100)) / 100.0))
                if abs(semi) > 0.1 and n >= 64:
                    factor = 2.0 ** (semi / 12.0)
                    spec = np.fft.rfft(y)
                    nb = len(spec)
                    src_idx = np.arange(nb, dtype=np.float32) / factor
                    valid = src_idx < (nb - 1)
                    i0 = np.clip(src_idx.astype(np.int32), 0, nb - 2)
                    frac = src_idx - i0
                    shifted = np.zeros_like(spec)
                    shifted[valid] = spec[i0[valid]] * (1.0 - frac[valid]) + spec[i0[valid] + 1] * frac[valid]
                    wet = np.fft.irfft(shifted, n=n).astype(np.float32)
                    y = y * (1.0 - mix) + wet * mix

            elif ft == "reverb":
                room = max(0.05, min(0.95, float(p.get("room", 42)) / 100.0))
                mix = max(0.0, min(0.85, float(p.get("mix", 22)) / 100.0))
                if mix > 0.01 and n >= 64:
                    d_len = max(256, int(sr * (0.028 + 0.055 * room)))
                    buf = st.get("rev_buf")
                    if buf is None or len(buf) != d_len:
                        buf = np.zeros(d_len, dtype=np.float32)
                    wet = np.empty(n, dtype=np.float32)
                    pos = int(st.get("rev_pos", 0)) % d_len
                    fb = 0.25 + 0.55 * room
                    for k_i in range(0, n, d_len):
                        chunk = y[k_i:k_i + d_len]
                        cl = len(chunk)
                        idxs = (pos + np.arange(cl)) % d_len
                        delayed = buf[idxs]
                        wet[k_i:k_i + cl] = delayed
                        buf[idxs] = chunk + delayed * fb
                        pos = (pos + cl) % d_len
                    st["rev_buf"] = buf
                    st["rev_pos"] = pos
                    y = y * (1.0 - mix * 0.35) + wet * mix

            elif ft == "gain":
                db = float(p.get("db", 0.0))
                if abs(db) > 0.05:
                    y *= 10.0 ** (db / 20.0)

            elif ft == "limiter":
                ceil_db = float(p.get("ceiling_db", -1.5))
                ceil_lin = max(0.05, min(1.0, 10.0 ** (ceil_db / 20.0)))
                y = np.tanh(y / ceil_lin) * ceil_lin

        return np.clip(y, -1.0, 1.0)


# =====================================================  MicHub compartido por dispositivo físico
class _SharedMicDevice:
    """Un único InputStream físico compartido por todos los personajes asignados al mismo micrófono."""

    def __init__(self, device_index):
        self.device_index = device_index
        self.stream = None
        self.sr = get_output_samplerate()
        self.error = ""
        self._subs = set()
        self._lock = threading.Lock()
        self._open()

    def _open(self):
        try:
            info = sd.query_devices(self.device_index)
            self.sr = int(info["default_samplerate"])
        except Exception as e:
            self.error = str(e)
            return
        extra = None
        try:
            if "WASAPI" in sd.query_hostapis(info["hostapi"])["name"]:
                extra = sd.WasapiSettings(auto_convert=True)
        except Exception:
            extra = None
        maxc = max(1, int(info["max_input_channels"]))
        for ch, ex in ((1, extra), (min(2, maxc), extra), (1, None)):
            try:
                kw = {"extra_settings": ex} if ex is not None else {}
                self.stream = sd.InputStream(
                    device=self.device_index, channels=ch, samplerate=self.sr,
                    blocksize=1024, dtype="int16", callback=self._cb, **kw
                )
                self.stream.start()
                self.error = ""
                return
            except Exception as e:
                self.error, self.stream = str(e), None

    def _cb(self, indata, frames, t, status):
        mono_f = indata[:, 0].astype(np.float32) / 32768.0
        with self._lock:
            subs = list(self._subs)
        for ch in subs:
            try:
                ch._on_audio(mono_f, self.sr)
            except Exception:
                pass

    def add(self, channel):
        with self._lock:
            self._subs.add(channel)

    def remove(self, channel):
        with self._lock:
            self._subs.discard(channel)
            return len(self._subs) == 0

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class MicHub:
    _devices = {}
    _lock = threading.Lock()

    @classmethod
    def subscribe(cls, device_index, channel):
        with cls._lock:
            dev = cls._devices.get(device_index)
            if dev is None or dev.stream is None:
                dev = _SharedMicDevice(device_index)
                if dev.stream is not None:
                    cls._devices[device_index] = dev
            if dev.stream is not None:
                dev.add(channel)
            return dev.sr, dev.error, dev.stream is not None

    @classmethod
    def unsubscribe(cls, device_index, channel):
        if device_index is None:
            return
        with cls._lock:
            dev = cls._devices.get(device_index)
            if dev is not None and dev.remove(channel):
                dev.close()
                cls._devices.pop(device_index, None)


# =====================================================  Discord Live UDP
class DiscordLiveReceiver:
    """Puente UDP local (127.0.0.1) para recibir niveles de voz en vivo por usuario desde Discord."""

    def __init__(self):
        self.running = False
        self.port = 8765
        self._sock = None
        self._thread = None
        self._lock = threading.Lock()
        self.speakers = {}

    def start(self, port=8765):
        self.stop()
        self.port = int(port)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.port))
            s.settimeout(0.25)
            self._sock = s
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True, ""
        except Exception as e:
            self.running = False
            return False, str(e)

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _loop(self):
        while self.running and self._sock:
            try:
                data, _ = self._sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8", errors="ignore"))
                user = str(msg.get("user", "")).strip()
                lvl = float(np.clip(float(msg.get("level", 0.0)), 0.0, 1.0))
                if user:
                    with self._lock:
                        self.speakers[user] = (lvl, time.time())
            except socket.timeout:
                continue
            except Exception:
                if not self.running:
                    break

    def level_for_char(self, cid, discord_map):
        now = time.time()
        best = 0.0
        with self._lock:
            for user, mapped_cid in (discord_map or {}).items():
                if mapped_cid == cid and user in self.speakers:
                    lvl, ts = self.speakers[user]
                    if now - ts < 0.35:
                        best = max(best, lvl)
        return best

    def active_users(self):
        now = time.time()
        with self._lock:
            return [u for u, (_, ts) in self.speakers.items() if now - ts < 30.0]


# =====================================================  Carga eficiente y asíncrona de audio
def _resample_audio(data, src_sr, dst_sr):
    if src_sr == dst_sr or len(data) < 2:
        return data
    n_out = max(1, int(round(len(data) * dst_sr / src_sr)))
    x_old = np.arange(len(data), dtype=np.float64)
    x_new = np.linspace(0.0, len(data) - 1, n_out, dtype=np.float64)
    if data.ndim == 1:
        return np.interp(x_new, x_old, data).astype(np.float32)
    out = np.empty((n_out, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, data[:, ch]).astype(np.float32)
    return out


def _compute_peaks(mono, sr, hz=PEAKS_HZ):
    if len(mono) == 0:
        return np.zeros(1, dtype=np.float32)
    step = max(1, int(sr / hz))
    n = max(1, int(np.ceil(len(mono) / step)))
    pad = n * step - len(mono)
    arr = np.abs(mono) if pad == 0 else np.abs(np.pad(mono, (0, pad)))
    return arr.reshape(n, step).max(axis=1).astype(np.float32)


def _read_wav_fallback(path):
    with wave.open(str(path), "rb") as w:
        sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 1:
        arr = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        arr = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        arr = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Formato WAV no soportado ({sw * 8} bits)")
    return arr.reshape(-1, nch), sr


def _read_ffmpeg_fallback(path, dst_sr):
    ff = get_ffmpeg()
    if not ff:
        raise RuntimeError("No se encontró ffmpeg para decodificar este formato de audio.")
    cmd = [
        ff, "-v", "error", "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(dst_sr), "pipe:1",
    ]
    res = subprocess.run(cmd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(f"ffmpeg no pudo leer {Path(path).name}")
    arr = np.frombuffer(res.stdout, dtype=np.float32)
    if len(arr) % 2:
        arr = arr[:-1]
    return arr.reshape(-1, 2).copy(), dst_sr


def load_audio(path, target_sr=None):
    """Carga audio con uso reducido de RAM (mono 1D o estéreo 2D sin duplicación)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {p.name}")
    dst_sr = int(target_sr or get_output_samplerate())
    key = (str(p.resolve()), dst_sr, p.stat().st_mtime_ns)
    with _CACHE_LOCK:
        if key in _AUDIO_CACHE:
            return _AUDIO_CACHE[key]

    data, src_sr = None, None
    if sf is not None:
        try:
            data, src_sr = sf.read(str(p), dtype="float32", always_2d=True)
        except Exception:
            data = None
    if data is None and p.suffix.lower() == ".wav":
        try:
            data, src_sr = _read_wav_fallback(p)
        except Exception:
            data = None
    if data is None:
        data, src_sr = _read_ffmpeg_fallback(p, dst_sr)

    if data.ndim == 2 and data.shape[1] == 1:
        samples = np.ascontiguousarray(data[:, 0], dtype=np.float32)
    elif data.ndim == 2 and data.shape[1] >= 2:
        # Si L == R exactamente (mono guardado como estéreo), ahorrar 50% RAM
        if len(data) > 0 and np.allclose(data[:min(2048, len(data)), 0], data[:min(2048, len(data)), 1], atol=1e-5):
            samples = np.ascontiguousarray(data[:, 0], dtype=np.float32)
        else:
            samples = np.ascontiguousarray(data[:, :2], dtype=np.float32)
    else:
        samples = np.ascontiguousarray(data.reshape(-1), dtype=np.float32)

    if src_sr != dst_sr:
        samples = _resample_audio(samples, src_sr, dst_sr)

    mono_view = samples if samples.ndim == 1 else samples.mean(axis=1)
    duration = len(mono_view) / float(dst_sr) if dst_sr else 0.0
    peaks = _compute_peaks(mono_view, dst_sr, PEAKS_HZ)
    info = {
        "samples": samples,     # 1D (mono) o 2D (N, 2) float32 -> hasta 3x menos RAM
        "sr": dst_sr,
        "duration": duration,
        "peaks": peaks,
    }
    with _CACHE_LOCK:
        if len(_AUDIO_CACHE) >= _CACHE_MAX_ITEMS:
            _AUDIO_CACHE.pop(next(iter(_AUDIO_CACHE)))
        _AUDIO_CACHE[key] = info
    return info


def _clip_slice_mono(samples, c0, c1):
    if samples.ndim == 1:
        return samples[c0:c1]
    return samples[c0:c1].mean(axis=1)


def _clip_slice_stereo(samples, c0, c1):
    if samples.ndim == 1:
        s = samples[c0:c1]
        return np.column_stack((s, s))
    return samples[c0:c1]


class _AudioLoadTask(QRunnable):
    def __init__(self, clip_id, path, sr, callback_ok, callback_err):
        super().__init__()
        self.clip_id = clip_id
        self.path = path
        self.sr = sr
        self.callback_ok = callback_ok
        self.callback_err = callback_err

    def run(self):
        try:
            info = load_audio(self.path, target_sr=self.sr)
            self.callback_ok(self.clip_id, info)
        except Exception as e:
            self.callback_err(self.clip_id, str(e))


class AsyncAudioLoader(QObject):
    """Decodifica clips en segundo plano (QThreadPool) para 0 congelamientos al abrir proyectos."""
    clipLoaded = Signal(str, object)
    clipFailed = Signal(str, str)
    progressChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self._lock = threading.Lock()
        self._pending = 0
        self._total = 0

    def enqueue(self, clip_id, path, sr):
        with self._lock:
            self._pending += 1
            self._total += 1
            done, tot = self._total - self._pending, self._total
        self.progressChanged.emit(done, tot)
        task = _AudioLoadTask(clip_id, path, sr, self._on_ok, self._on_err)
        self.pool.start(task)

    def _finish_one(self):
        with self._lock:
            self._pending = max(0, self._pending - 1)
            done, tot = self._total - self._pending, self._total
            if self._pending == 0:
                self._total = 0
                done, tot = 0, 0
        self.progressChanged.emit(done, tot)

    def _on_ok(self, clip_id, info):
        self.clipLoaded.emit(clip_id, info)
        self._finish_one()

    def _on_err(self, clip_id, err):
        self.clipFailed.emit(clip_id, err)
        self._finish_one()


def clip_effective_duration(clip):
    src_dur = float(clip.get("src_duration", clip.get("duration", 0.0)))
    tin = max(0.0, float(clip.get("trim_in", 0.0)))
    tout = max(0.0, float(clip.get("trim_out", 0.0)))
    return max(0.05, src_dur - tin - tout)


def _fade_envelope(rel_start, count, total_eff, fade_in_s, fade_out_s, sr):
    """Curva suave de fade-in y fade-out para un segmento [rel_start : rel_start + count]."""
    fi = int(round(max(0.0, float(fade_in_s)) * sr))
    fo = int(round(max(0.0, float(fade_out_s)) * sr))
    if fi <= 0 and fo <= 0:
        return None
    env = None
    if fi > 0 and rel_start < fi:
        end_i = min(count, fi - rel_start)
        if end_i > 0:
            env = np.ones(count, dtype=np.float32)
            idx = np.arange(rel_start, rel_start + end_i, dtype=np.float32)
            env[:end_i] *= np.clip(idx / float(fi), 0.0, 1.0)
    fo_start = total_eff - fo
    if fo > 0 and rel_start + count > fo_start:
        if env is None:
            env = np.ones(count, dtype=np.float32)
        a = max(0, fo_start - rel_start)
        idx = np.arange(rel_start + a, rel_start + count, dtype=np.float32)
        env[a:] *= np.clip((total_eff - idx) / float(fo), 0.0, 1.0)
    return env


def _normalize_broadcast(audio, target_lufs=-16.0):
    """Normalización estilo podcast (-16 dBFS RMS gated) con limitador suave a -1 dBTP."""
    if len(audio) == 0:
        return audio
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    # Energía por bloques de ~2048 muestras ignorando silencios (gate -50 dB)
    blk = 2048
    n_blk = len(mono) // blk
    if n_blk > 0:
        rms_blocks = np.sqrt(np.mean(mono[:n_blk * blk].reshape(n_blk, blk) ** 2, axis=1))
        active = rms_blocks[rms_blocks > 0.003]
        rms = float(np.sqrt(np.mean(active ** 2))) if len(active) else float(np.sqrt(np.mean(mono ** 2)))
    else:
        rms = float(np.sqrt(np.mean(mono ** 2)))
    if rms > 1e-5:
        cur_db = 20.0 * math.log10(rms)
        gain_db = max(-12.0, min(18.0, target_lufs - cur_db))
        audio = audio * float(10.0 ** (gain_db / 20.0))
    # Techo suave -1 dBTP (0.891)
    peak = float(np.abs(audio).max())
    if peak > 0.89:
        audio = np.tanh(audio / 0.891) * 0.891
    return np.clip(audio, -1.0, 1.0)


def _write_audio_file(path, data, sr, fmt="wav"):
    path = Path(path)
    fmt = fmt.lower().lstrip(".")
    if fmt == "mp3":
        ff = get_ffmpeg()
        if ff:
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
            nch = 1 if pcm.ndim == 1 else pcm.shape[1]
            cmd = [
                ff, "-y", "-v", "error",
                "-f", "s16le", "-ar", str(sr), "-ac", str(nch), "-i", "pipe:0",
                "-c:a", "libmp3lame", "-b:a", "320k", str(path),
            ]
            subprocess.run(cmd, input=np.ascontiguousarray(pcm).tobytes(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
            return
        fmt = "wav"
        path = path.with_suffix(".wav")
    if fmt == "wav" or sf is None:
        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
        nch = 1 if pcm.ndim == 1 else pcm.shape[1]
        with wave.open(str(path), "wb") as w:
            w.setnchannels(nch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.ascontiguousarray(pcm).tobytes())
    else:
        sf.write(str(path), np.clip(data, -1.0, 1.0), sr)


def _active_clips_for_render(clips, tracks):
    tr_map = {t["id"]: t for t in tracks}
    solo_tids = {t["id"] for t in tracks if t.get("solo")}
    out = []
    for cl in clips:
        if cl.get("missing") or cl.get("samples") is None:
            continue
        tid = cl.get("tid") or cl["cid"]
        tr = tr_map.get(tid)
        if solo_tids:
            if tid not in solo_tids:
                continue
        elif tr and tr.get("mute"):
            continue
        out.append(cl)
    return out


def export_timeline_audio(clips, tracks, characters, folder, base_name="Podcast", mode="both",
                          fmt="wav", t_start=0.0, t_end=None, sr=None, normalize=False,
                          stems_subfolder=None):
    sr = int(sr or get_output_samplerate())
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    char_map = {c["id"]: c for c in characters}
    char_order = {c["id"]: i + 1 for i, c in enumerate(characters)}
    active = _active_clips_for_render(clips, tracks)
    if not active:
        return [], None

    t_start = max(0.0, float(t_start))
    auto_end = max(cl["start"] + clip_effective_duration(cl) for cl in active)
    t_end = auto_end if t_end is None else max(t_start + 0.05, float(t_end))
    total_samples = max(1, int(np.ceil((t_end - t_start) * sr)))
    s_win0 = int(round(t_start * sr))
    s_win1 = s_win0 + total_samples

    mix_stereo = np.zeros((total_samples, 2), dtype=np.float32)
    per_char = {}

    for cl in active:
        cid = cl["cid"]
        samples = cl["samples"]
        n_src = len(samples)
        tin = min(int(round(max(0.0, float(cl.get("trim_in", 0.0))) * sr)), n_src)
        eff_len = min(int(round(clip_effective_duration(cl) * sr)), n_src - tin)
        if eff_len <= 0:
            continue
        cs0 = int(round(cl["start"] * sr))
        ov0, ov1 = max(s_win0, cs0), min(s_win1, cs0 + eff_len)
        if ov1 <= ov0:
            continue
        o0, o1 = ov0 - s_win0, ov1 - s_win0
        c0, c1 = tin + (ov0 - cs0), tin + (ov1 - cs0)
        cg = float(cl.get("gain", 1.0))
        env = _fade_envelope(ov0 - cs0, ov1 - ov0, eff_len, cl.get("fade_in", 0.0), cl.get("fade_out", 0.0), sr)

        st_seg = _clip_slice_stereo(samples, c0, c1) * cg
        if env is not None:
            st_seg = st_seg * env[:, None]
        mix_stereo[o0:o1] += st_seg

        if mode in ("separate", "both"):
            mn_seg = _clip_slice_mono(samples, c0, c1) * cg
            if env is not None:
                mn_seg = mn_seg * env
            buf = per_char.get(cid)
            if buf is None:
                buf = np.zeros(total_samples, dtype=np.float32)
                per_char[cid] = buf
            buf[o0:o1] += mn_seg

    ext = fmt.lower().lstrip(".")
    if ext not in ("wav", "flac", "ogg", "mp3"):
        ext = "wav"
    written, mix_path = [], None

    if mode in ("separate", "both"):
        stems_dir = (folder / stems_subfolder) if stems_subfolder else folder
        stems_dir.mkdir(parents=True, exist_ok=True)
        for cid, buf in sorted(per_char.items(), key=lambda kv: char_order.get(kv[0], 99)):
            if normalize:
                buf = _normalize_broadcast(buf)
            else:
                peak = float(np.abs(buf).max())
                if peak > 1.0:
                    buf = buf / peak
            cname = _safe_name(char_map.get(cid, {}).get("name", cid))
            idx = char_order.get(cid, 0)
            fname = f"{idx:02d}_{cname}.{ext}" if stems_subfolder else f"{_safe_name(base_name)}_{cname}.{ext}"
            out_p = stems_dir / fname
            _write_audio_file(out_p, buf, sr, ext)
            written.append(out_p)

    if mode in ("mixed", "both"):
        if normalize:
            mix_stereo = _normalize_broadcast(mix_stereo)
        else:
            peak = float(np.abs(mix_stereo).max())
            if peak > 1.0:
                mix_stereo = mix_stereo / peak
        mix_path = folder / f"{_safe_name(base_name)}_Mezcla.{ext}"
        _write_audio_file(mix_path, mix_stereo, sr, ext)
        written.append(mix_path)

    return written, mix_path


def render_timeline_levels(clips, tracks, characters, t_start, t_end, fps, sr=None, want_spectrum=False):
    """Cálculo ultra rápido y vectorizado (por bloques) de niveles vocales y espectro para exportación."""
    sr = int(sr or get_output_samplerate())
    active = _active_clips_for_render(clips, tracks)
    n_frames = max(1, int(math.ceil(max(0.05, t_end - t_start) * fps)))
    frame_s = max(1, int(round(sr / float(fps))))
    gains = {c["id"]: c["gain"] / 100.0 for c in characters}
    levels = {c["id"]: np.zeros(n_frames, dtype=np.float32) for c in characters}
    master_spec = np.zeros((n_frames, SPEC_BINS), dtype=np.float32) if want_spectrum else None

    by_char = {}
    for cl in active:
        by_char.setdefault(cl["cid"], []).append(cl)

    chunk_frames = max(60, int(60 * fps))  # bloques de 60s -> RAM acotada (~10MB)

    for cid, c_clips in by_char.items():
        if cid not in levels:
            continue
        g = gains.get(cid, 1.0)
        prev = 0.0
        for f0 in range(0, n_frames, chunk_frames):
            f1 = min(n_frames, f0 + chunk_frames)
            nf = f1 - f0
            s_win0 = int(round((t_start + f0 / float(fps)) * sr))
            total_s = nf * frame_s
            s_win1 = s_win0 + total_s
            buf = np.zeros(total_s, dtype=np.float32)
            has_any = False
            for cl in c_clips:
                samples = cl["samples"]
                n_src = len(samples)
                tin = min(int(round(max(0.0, float(cl.get("trim_in", 0.0))) * sr)), n_src)
                eff_len = min(int(round(clip_effective_duration(cl) * sr)), n_src - tin)
                if eff_len <= 0:
                    continue
                cs0 = int(round(cl["start"] * sr))
                ov0, ov1 = max(s_win0, cs0), min(s_win1, cs0 + eff_len)
                if ov1 <= ov0:
                    continue
                has_any = True
                o0, o1 = ov0 - s_win0, ov1 - s_win0
                c0, c1 = tin + (ov0 - cs0), tin + (ov1 - cs0)
                seg = _clip_slice_mono(samples, c0, c1) * float(cl.get("gain", 1.0))
                env = _fade_envelope(ov0 - cs0, ov1 - ov0, eff_len, cl.get("fade_in", 0.0), cl.get("fade_out", 0.0), sr)
                if env is not None:
                    seg = seg * env
                buf[o0:o1] += seg
            if has_any:
                frames_2d = buf.reshape(nf, frame_s)
                rms = np.sqrt(np.mean(frames_2d * frames_2d, axis=1)) * g
                db = 20.0 * np.log10(np.maximum(rms, 1e-6))
                lvls = np.clip((db + 60.0) / 60.0, 0.0, 1.0).astype(np.float32)
                for i in range(nf):
                    lv = float(lvls[i])
                    prev = lv if lv > prev else prev * 0.82 + lv * 0.18
                    levels[cid][f0 + i] = prev
            else:
                for i in range(nf):
                    prev *= 0.82
                    levels[cid][f0 + i] = prev

    if want_spectrum and active:
        prev_sp = np.zeros(SPEC_BINS, dtype=np.float32)
        for f0 in range(0, n_frames, chunk_frames):
            f1 = min(n_frames, f0 + chunk_frames)
            nf = f1 - f0
            s_win0 = int(round((t_start + f0 / float(fps)) * sr))
            total_s = nf * frame_s
            s_win1 = s_win0 + total_s
            buf = np.zeros(total_s, dtype=np.float32)
            for cl in active:
                samples = cl["samples"]
                n_src = len(samples)
                tin = min(int(round(max(0.0, float(cl.get("trim_in", 0.0))) * sr)), n_src)
                eff_len = min(int(round(clip_effective_duration(cl) * sr)), n_src - tin)
                if eff_len <= 0:
                    continue
                cs0 = int(round(cl["start"] * sr))
                ov0, ov1 = max(s_win0, cs0), min(s_win1, cs0 + eff_len)
                if ov1 <= ov0:
                    continue
                buf[ov0 - s_win0:ov1 - s_win0] += _clip_slice_mono(samples, tin + (ov0 - cs0), tin + (ov1 - cs0)) * float(cl.get("gain", 1.0))
            frames_2d = buf.reshape(nf, frame_s)
            for i in range(nf):
                sp = compute_spectrum(frames_2d[i][:min(1024, frame_s)], sr, SPEC_BINS)
                prev_sp = np.maximum(sp, prev_sp * 0.72)
                master_spec[f0 + i] = prev_sp

    if want_spectrum:
        return levels, n_frames, master_spec
    return levels, n_frames


# =====================================================  Canal de micrófono (suscrito al MicHub)
class AudioChannel:
    """Canal de micrófono por personaje conectado a MicHub compartido + DSP modular."""

    def __init__(self, filters=None):
        self._device_index = None
        self._active = False
        self.level, self.peak, self.error, self.sr, self.gain = 0.0, 0.0, "", get_output_samplerate(), 1.0
        self.spectrum = np.zeros(SPEC_BINS, dtype=np.float32)
        self._wav = None
        self._lock = threading.Lock()
        self.dsp = MicProcessor(filters)
        self.emotion = VoiceEmotionAnalyzer()

    def set_filters(self, filters):
        self.dsp.update(filters)

    def _smooth(self, lvl):
        self.level = lvl if lvl > self.level else self.level * 0.85 + lvl * 0.15

    def open_mic(self, device_index):
        self.close()
        if device_index is None:
            return
        sr, err, ok = MicHub.subscribe(device_index, self)
        self.sr = sr
        self.error = err
        self._active = ok
        if ok:
            self._device_index = device_index

    def _on_audio(self, mono_raw, sr):
        self.sr = sr
        mono_f = self.dsp.process(mono_raw, sr)
        self.peak = float(np.clip(np.abs(mono_f).max() * self.gain, 0.0, 1.0)) if len(mono_f) else 0.0
        self._smooth(level_from(mono_f, self.gain))
        sp = compute_spectrum(mono_f, sr, SPEC_BINS)
        self.spectrum = np.maximum(sp, self.spectrum * 0.72)
        try:
            self.emotion.process_block(mono_f, sr, gain=self.gain)
        except Exception:
            pass
        with self._lock:
            if self._wav:
                pcm = (np.clip(mono_f, -1.0, 1.0) * 32767.0).astype(np.int16)
                self._wav.writeframes(np.ascontiguousarray(pcm).tobytes())

    def can_record(self):
        return self._active

    def start_rec(self, path):
        if not self.can_record():
            return False
        with self._lock:
            w = wave.open(str(path), "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            self._wav = w
        return True

    def stop_rec(self):
        with self._lock:
            if self._wav:
                self._wav.close()
                self._wav = None

    def close(self):
        self.stop_rec()
        if self._device_index is not None:
            MicHub.unsubscribe(self._device_index, self)
            self._device_index = None
        self._active = False
        self.level = 0.0
        self.peak = 0.0
        self.spectrum.fill(0.0)


# =====================================================  Mezclador multipista
class TimelineMixer:
    """Mezclador multipista con sub-pistas, volumen, fades, trim, solo/mute, bucle y espectro master."""

    def __init__(self):
        self.sr = get_output_samplerate()
        self.playing = False
        self.loop = False
        self.loop_range = None
        self.playhead = 0.0
        self._sample_pos = 0
        self._clips = []
        self._cids = []
        self._char_gains = {}
        self._muted_tids = set()
        self._solo_tids = set()
        self.levels = {}
        self.master_spectrum = np.zeros(SPEC_BINS, dtype=np.float32)
        self._recording = False
        self._lock = threading.Lock()
        self.out = None
        self.error = ""
        self._ensure_stream()

    def _ensure_stream(self):
        if self.out is not None:
            return
        try:
            self.out = sd.OutputStream(samplerate=self.sr, channels=2, dtype="float32",
                                       blocksize=1024, callback=self._cb)
            self.out.start()
            self.error = ""
        except Exception as e:
            self.error = str(e)
            self.out = None

    def set_clips(self, clips):
        with self._lock:
            self._clips = list(clips)

    def set_state(self, cids, gains, muted_tids, solo_tids=None):
        with self._lock:
            self._cids = list(cids)
            self._char_gains = dict(gains)
            self._muted_tids = set(muted_tids)
            self._solo_tids = set(solo_tids or ())
            for cid in self._cids:
                self.levels.setdefault(cid, 0.0)

    def set_loop_range(self, in_t, out_t):
        with self._lock:
            if in_t is not None and out_t is not None and out_t > in_t + 0.05:
                self.loop_range = (float(in_t), float(out_t))
            else:
                self.loop_range = None

    def play(self, recording=False):
        self._ensure_stream()
        with self._lock:
            self._recording = recording
            if not recording:
                if self.loop_range:
                    a, b = self.loop_range
                    if self.playhead < a or self.playhead >= b - 0.02:
                        self.playhead = a
                        self._sample_pos = int(round(a * self.sr))
                else:
                    end = max((c["start"] + clip_effective_duration(c) for c in self._clips), default=0.0)
                    if end > 0 and self.playhead >= end - 0.02:
                        self._sample_pos = 0
                        self.playhead = 0.0
            self.playing = True

    def pause(self):
        self.playing = False
        self._recording = False

    def stop(self):
        self.playing = False
        self._recording = False
        with self._lock:
            start_t = self.loop_range[0] if self.loop_range else 0.0
            self._sample_pos = int(round(start_t * self.sr))
            self.playhead = start_t
            for cid in self.levels:
                self.levels[cid] = 0.0
            self.master_spectrum.fill(0.0)

    def seek(self, sec):
        sec = max(0.0, float(sec))
        with self._lock:
            self._sample_pos = int(round(sec * self.sr))
            self.playhead = sec

    def level_of(self, cid):
        return self.levels.get(cid, 0.0)

    def _cb(self, outdata, frames, t, status):
        outdata.fill(0)
        with self._lock:
            if not self.playing:
                for cid in self._cids:
                    self.levels[cid] = self.levels.get(cid, 0.0) * 0.82
                self.master_spectrum *= 0.78
                return

            s0 = self._sample_pos
            s1 = s0 + frames
            per_char = {}

            for clip in self._clips:
                tid = clip.get("tid") or clip["cid"]
                cid = clip["cid"]
                if self._solo_tids:
                    if tid not in self._solo_tids:
                        continue
                elif tid in self._muted_tids:
                    continue
                samples = clip.get("samples")
                if samples is None or len(samples) == 0:
                    continue
                n_src = len(samples)
                tin = min(int(round(max(0.0, float(clip.get("trim_in", 0.0))) * self.sr)), n_src)
                eff_len = min(int(round(clip_effective_duration(clip) * self.sr)), n_src - tin)
                if eff_len <= 0:
                    continue
                cs0 = int(round(clip["start"] * self.sr))
                ov0, ov1 = max(s0, cs0), min(s1, cs0 + eff_len)
                if ov1 <= ov0:
                    continue
                o0, o1 = ov0 - s0, ov1 - s0
                c0, c1 = tin + (ov0 - cs0), tin + (ov1 - cs0)
                cg = float(clip.get("gain", 1.0))
                env = _fade_envelope(ov0 - cs0, ov1 - ov0, eff_len,
                                     clip.get("fade_in", 0.0), clip.get("fade_out", 0.0), self.sr)
                if samples.ndim == 1:
                    seg = samples[c0:c1] * cg
                    if env is not None:
                        seg = seg * env
                    outdata[o0:o1, 0] += seg
                    outdata[o0:o1, 1] += seg
                    mn = seg
                else:
                    seg = samples[c0:c1] * cg
                    if env is not None:
                        seg = seg * env[:, None]
                    outdata[o0:o1] += seg
                    mn = seg.mean(axis=1)
                buf = per_char.get(cid)
                if buf is None:
                    buf = np.zeros(frames, dtype=np.float32)
                    per_char[cid] = buf
                buf[o0:o1] += mn

            np.clip(outdata, -1.0, 1.0, out=outdata)
            sp = compute_spectrum(outdata.mean(axis=1), self.sr, SPEC_BINS)
            self.master_spectrum = np.maximum(sp, self.master_spectrum * 0.74)

            for cid in self._cids:
                buf = per_char.get(cid)
                if buf is not None:
                    lvl = level_from(buf, self._char_gains.get(cid, 1.0))
                    prev = self.levels.get(cid, 0.0)
                    self.levels[cid] = lvl if lvl > prev else prev * 0.82 + lvl * 0.18
                else:
                    self.levels[cid] = self.levels.get(cid, 0.0) * 0.82

            self._sample_pos = s1
            self.playhead = self._sample_pos / float(self.sr)

            if not self._recording:
                if self.loop_range:
                    a, b = self.loop_range
                    if self.playhead >= b:
                        if self.loop:
                            self.playhead = a
                            self._sample_pos = int(round(a * self.sr))
                        else:
                            self.playing = False
                            self.playhead = b
                            self._sample_pos = int(round(b * self.sr))
                else:
                    end = max((c["start"] + clip_effective_duration(c) for c in self._clips), default=0.0)
                    if end > 0.0 and self.playhead >= end:
                        if self.loop:
                            self._sample_pos = 0
                            self.playhead = 0.0
                        else:
                            self.playing = False
                            self.playhead = end
                            self._sample_pos = int(round(end * self.sr))

    def close(self):
        self.playing = False
        if self.out is not None:
            try:
                self.out.stop()
                self.out.close()
            except Exception:
                pass
            self.out = None
