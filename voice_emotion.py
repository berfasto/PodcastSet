"""Motor de detección de emociones y prosodia vocal en tiempo real (100% NumPy, <0.4 ms/bloque).
Detecta automáticamente: normal, feliz, riendo, gritando, triste, enojado y sorprendido
a partir del micrófono o clips de audio, con autocalibración al timbre de cada persona.
También genera coreografía expresiva de cabeza para el modo 'Solo Voz' sin cámara."""
import math
import threading
import time
from collections import deque

import numpy as np

EMOTIONS = [
    ("neutral",   "Normal",      "#8E8E98", "Voz natural en calma"),
    ("happy",     "Feliz",       "#2EB67D", "Tono alegre, brillante y melódico"),
    ("laugh",     "Riendo",      "#F59E0B", "Ráfagas rítmicas de risa (ja-ja-ja)"),
    ("shout",     "Gritando",    "#E5484D", "Volumen alto sostenido / euforia"),
    ("sad",       "Triste",      "#38BDF8", "Voz suave, grave, apagada o descendente"),
    ("angry",     "Enojado",     "#F97316", "Voz fuerte, tensa y con ataque duro"),
    ("surprised", "Sorprendido", "#A78BFA", "Pico repentino de tono y volumen"),
]
EMOTION_IDS = [e[0] for e in EMOTIONS]
EMOTION_META = {e[0]: {"label": e[1], "color": e[2], "desc": e[3]} for e in EMOTIONS}


class VoiceEmotionAnalyzer:
    """Analizador acústico de emociones en tiempo real con autocalibración adaptativa."""

    def __init__(self):
        self._lock = threading.Lock()
        self.sensitivity = 0.65        # 0.2 .. 1.0
        self.enabled = True

        # Línea base autocalibrada de la voz del usuario
        self._base_rms_db = -28.0
        self._base_pitch = 165.0
        self._base_centroid = 1450.0
        self._calib_frames = 0

        # Ventanas cortas (~1.1 s a ~45 Hz de bloques)
        self._env_hist = deque(maxlen=48)      # (t, rms_lin)
        self._pitch_hist = deque(maxlen=36)    # (t, f0_hz)
        self._cent_hist = deque(maxlen=28)     # centroid Hz
        self._onset_times = deque(maxlen=24)   # timestamps de picos silábicos (risa)

        self._prev_rms = 0.0
        self._prev_voiced = False
        self._last_onset_t = 0.0
        self._silence_since = time.time()
        self._was_silent = True

        # Puntuaciones suavizadas por emoción
        self._scores = {k: (1.0 if k == "neutral" else 0.0) for k in EMOTION_IDS}
        self._cur_emotion = "neutral"
        self._cur_conf = 0.0
        self._hold_until = 0.0
        self._last_change_t = 0.0

        # Telemetría instantánea para la interfaz y coreografía 'Solo Voz'
        self.pitch_hz = 0.0
        self.energy = 0.0
        self.brightness = 0.0
        self.rhythm_hz = 0.0
        self.is_voiced = False
        self._voice_phase = 0.0

    def reset_calibration(self):
        with self._lock:
            self._calib_frames = 0
            self._base_rms_db = -28.0
            self._base_pitch = 165.0
            self._base_centroid = 1450.0
            self._env_hist.clear()
            self._pitch_hist.clear()
            self._cent_hist.clear()
            self._onset_times.clear()

    def _estimate_pitch_and_spectrum(self, x, sr):
        """Estima F0 (75..520 Hz), centroide espectral y ZCR en <0.25 ms con NumPy puro."""
        n = len(x)
        if n < 128:
            return 0.0, 0.0, 1200.0, 0.0
        # Ventana Hann para espectro limpio
        win = np.hanning(n).astype(np.float32)
        xw = x * win
        spec = np.fft.rfft(xw)
        mag = np.abs(spec)
        power = mag * mag
        mag_sum = float(mag.sum()) + 1e-8
        freqs = np.fft.rfftfreq(n, 1.0 / max(8000, sr))
        centroid = float(np.dot(freqs, mag) / mag_sum)
        # Relación agudos (>2.2 kHz) / energía total (aspereza / tensión)
        hi_mask = freqs >= 2200.0
        hi_ratio = float(mag[hi_mask].sum() / mag_sum) if np.any(hi_mask) else 0.0

        # Zero-crossing rate
        zcr = float(np.mean(x[:-1] * x[1:] < 0.0))

        # Autocorrelación rápida vía FFT (potencia espectral -> irfft) para F0
        n_fft = 1 << (2 * n - 1).bit_length()
        sp2 = np.fft.rfft(xw, n=n_fft)
        ac = np.fft.irfft(sp2 * np.conj(sp2), n=n_fft)[:n]
        ac0 = float(ac[0]) + 1e-9
        ac_norm = ac / ac0

        lag_min = max(8, int(sr / 520.0))
        lag_max = min(n - 2, int(sr / 75.0))
        if lag_max <= lag_min + 2:
            return 0.0, 0.0, centroid, hi_ratio

        seg = ac_norm[lag_min:lag_max]
        best_i = int(np.argmax(seg))
        peak_val = float(seg[best_i])
        if peak_val < 0.28 or zcr > 0.34:
            return 0.0, peak_val, centroid, hi_ratio

        # Interpolación parabólica para precisión sub-muestra
        idx = lag_min + best_i
        if 1 <= idx < len(ac_norm) - 1:
            y0, y1, y2 = float(ac_norm[idx - 1]), float(ac_norm[idx]), float(ac_norm[idx + 1])
            denom = (y0 - 2.0 * y1 + y2)
            shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-6 else 0.0
            lag = idx + max(-0.5, min(0.5, shift))
        else:
            lag = float(idx)
        f0 = float(sr / max(1.0, lag))
        return f0, peak_val, centroid, hi_ratio

    def process_block(self, mono_f, sr, gain=1.0, now=None):
        """Analiza un bloque de audio mono float32 (-1..1) en tiempo real."""
        if not self.enabled or mono_f is None or len(mono_f) < 64:
            return
        t = time.time() if now is None else float(now)
        x = mono_f * float(gain)
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.abs(x).max())
        db = 20.0 * math.log10(max(rms, 1e-6))
        lvl = max(0.0, min(1.0, (db + 60.0) / 60.0))

        f0, voicing, centroid, hi_ratio = self._estimate_pitch_and_spectrum(x, sr)
        sens = max(0.25, min(1.0, float(self.sensitivity)))
        # Factor de ganancia emocional según sensibilidad del usuario
        s_boost = 0.65 + sens * 0.70

        with self._lock:
            self.energy = lvl
            self.brightness = max(0.0, min(1.0, (centroid - 400.0) / 3600.0))
            self._env_hist.append((t, rms, db))
            speaking = db > -44.0

            if not speaking:
                if not self._was_silent:
                    self._silence_since = t
                    self._was_silent = True
                self.is_voiced = False
                self.pitch_hz *= 0.85
                self.rhythm_hz *= 0.90
                self._prev_rms = rms
                # Regreso gradual a neutro tras silencio prolongado
                if t > self._hold_until:
                    for k in EMOTION_IDS:
                        target = 0.85 if k == "neutral" else 0.0
                        self._scores[k] += (target - self._scores[k]) * 0.14
                    if self._scores[self._cur_emotion] < 0.22:
                        self._cur_emotion = "neutral"
                        self._cur_conf = 0.0
                return

            # Inicio brusco tras silencio -> señal clave para 'sorprendido'
            silent_gap = (t - self._silence_since) if self._was_silent else 0.0
            sudden_onset = self._was_silent and silent_gap > 0.35 and (rms - self._prev_rms) > 0.065
            self._was_silent = False

            # Detección de pulsos silábicos rítmicos (4..9 Hz = carcajada / risa)
            rms_jump = rms - self._prev_rms
            if rms_jump > max(0.014, self._prev_rms * 0.38) and (t - self._last_onset_t) > 0.095:
                self._onset_times.append(t)
                self._last_onset_t = t
            self._prev_rms = rms

            # Limpiar onsets viejos (> 1.15 s)
            while self._onset_times and (t - self._onset_times[0]) > 1.15:
                self._onset_times.popleft()

            # Calcular ritmo de pulsos y regularidad (firma de risa)
            rhythm_hz = 0.0
            rhythm_regularity = 0.0
            if len(self._onset_times) >= 3:
                dts = np.diff(np.array(self._onset_times, dtype=np.float64))
                valid = dts[(dts >= 0.09) & (dts <= 0.34)]
                if len(valid) >= 2:
                    mean_dt = float(valid.mean())
                    std_dt = float(valid.std())
                    rhythm_hz = 1.0 / max(0.05, mean_dt)
                    # Regularidad alta cuando los 'ja-ja-ja' tienen cadencia pareja
                    cv = std_dt / max(0.03, mean_dt)
                    rhythm_regularity = max(0.0, min(1.0, 1.0 - cv * 1.4))
            self.rhythm_hz = self.rhythm_hz * 0.65 + rhythm_hz * 0.35

            if f0 > 0.0:
                self.is_voiced = True
                self.pitch_hz = f0 if self.pitch_hz < 10.0 else (self.pitch_hz * 0.55 + f0 * 0.45)
                self._pitch_hist.append((t, f0))
            else:
                self.is_voiced = False

            self._cent_hist.append(centroid)
            while self._pitch_hist and (t - self._pitch_hist[0][0]) > 1.1:
                self._pitch_hist.popleft()

            # Autocalibración suave solo durante habla moderada normal
            if -36.0 < db < -16.0 and f0 > 75.0:
                alpha = 0.08 if self._calib_frames < 40 else 0.006
                self._base_rms_db += (db - self._base_rms_db) * alpha
                self._base_pitch += (f0 - self._base_pitch) * alpha
                self._base_centroid += (centroid - self._base_centroid) * alpha
                self._calib_frames += 1

            # Rasgos relativos a la línea base personal del hablante
            db_rel = db - self._base_rms_db                # +12 dB = muy fuerte, -10 dB = suave
            pitch_ratio = (self.pitch_hz / max(80.0, self._base_pitch)) if self.pitch_hz > 60.0 else 1.0
            cent_ratio = centroid / max(500.0, self._base_centroid)

            p_vals = [p for _, p in self._pitch_hist]
            if len(p_vals) >= 4:
                p_arr = np.array(p_vals, dtype=np.float32)
                pitch_std = float(p_arr.std()) / max(80.0, self._base_pitch)
                pitch_slope = float(p_arr[-1] - p_arr[0]) / max(80.0, self._base_pitch)
            else:
                pitch_std = 0.05
                pitch_slope = 0.0

            # Envolvente de modulación en los últimos ~0.8s (profundidad de valles entre sílabas -> risa)
            env_vals = np.array([e[1] for e in self._env_hist if (t - e[0]) <= 0.85], dtype=np.float32)
            env_mod = float(env_vals.std() / (env_vals.mean() + 1e-5)) if len(env_vals) >= 6 else 0.0

            # ================= Puntuación heurística calibrada por emoción =================
            raw = {k: 0.0 for k in EMOTION_IDS}

            # 1. GRITANDO (shout): volumen muy elevado sobre base + energía sostenida / cresta alta
            if db > -15.5 or db_rel > 10.5 or peak > 0.72:
                sh_v = max(0.0, (db + 18.0) / 12.0) * 0.55 + max(0.0, (db_rel - 7.0) / 10.0) * 0.45
                if peak > 0.78:
                    sh_v += 0.25
                raw["shout"] = min(1.3, sh_v * s_boost)

            # 2. RIENDO (laugh): ráfagas rítmicas 3.8..9.5 Hz + buena modulación de envolvente + brillo/tono vivo
            if len(self._onset_times) >= 3 and 3.4 <= rhythm_hz <= 9.8 and env_mod > 0.34:
                r_fit = 1.0 - min(1.0, abs(rhythm_hz - 5.8) / 4.2)
                la_v = (0.42 * r_fit + 0.32 * rhythm_regularity + 0.26 * min(1.0, env_mod / 0.75))
                if pitch_ratio >= 0.96 or cent_ratio >= 0.95:
                    la_v += 0.18
                if len(self._onset_times) >= 4:
                    la_v += 0.18
                raw["laugh"] = min(1.3, la_v * s_boost)

            # 3. SORPRENDIDO (surprised): ataque súbito tras silencio + tono agudo o subida rápida
            if sudden_onset and (pitch_ratio > 1.14 or db_rel > 6.5 or pitch_slope > 0.16):
                su_v = 0.55 + max(0.0, pitch_ratio - 1.08) * 1.4 + max(0.0, db_rel - 5.0) * 0.05
                raw["surprised"] = min(1.3, su_v * s_boost)
            elif pitch_slope > 0.28 and db_rel > 5.0:
                raw["surprised"] = min(1.1, (0.45 + pitch_slope) * s_boost)

            # 4. ENOJADO (angry): voz fuerte/tensa con mucha energía en agudos/aspereza pero tono más bajo/duro que el grito eufórico
            if (db_rel > 5.5 or db > -20.0) and hi_ratio > 0.28 and pitch_ratio < 1.18 and raw["laugh"] < 0.35:
                an_v = max(0.0, (db_rel - 4.0) / 10.0) * 0.48 + max(0.0, (hi_ratio - 0.24) / 0.35) * 0.52
                if raw["shout"] > 0.75 and pitch_ratio < 1.05:
                    an_v += 0.20
                raw["angry"] = min(1.2, an_v * s_boost)

            # 5. FELIZ (happy): tono alto + variabilidad melódica + timbre brillante + volumen animado
            if pitch_ratio > 1.08 and cent_ratio > 1.04 and -33.0 < db < -12.0:
                hp_v = (min(1.0, (pitch_ratio - 1.05) / 0.32) * 0.48
                        + min(1.0, (cent_ratio - 1.0) / 0.45) * 0.26
                        + min(1.0, pitch_std / 0.16) * 0.26)
                if pitch_slope > 0.04:
                    hp_v += 0.12
                raw["happy"] = min(1.25, hp_v * s_boost)

            # 6. TRISTE (sad): voz suave/baja, tono más grave o descendente, poca melodía, timbre oscuro/apagado
            if -43.0 < db < -24.0 and db_rel < -2.0 and pitch_ratio < 0.94 and cent_ratio < 0.95:
                sd_v = (min(1.0, (-db_rel - 1.5) / 10.0) * 0.36
                        + min(1.0, (0.95 - pitch_ratio) / 0.22) * 0.36
                        + min(1.0, (0.96 - cent_ratio) / 0.35) * 0.28)
                if pitch_slope < -0.04 or pitch_std < 0.055:
                    sd_v += 0.14
                raw["sad"] = min(1.2, sd_v * s_boost)

            # 7. NEUTRAL (fallback limpio)
            max_other = max(v for k, v in raw.items() if k != "neutral")
            raw["neutral"] = max(0.15, 0.72 - max_other * 0.85)

            # Suavizado EMA con ataque rápido para risa/grito/sorpresa y decaimiento natural
            for k in EMOTION_IDS:
                target = raw[k]
                fast_atk = 0.42 if k in ("shout", "laugh", "surprised") else 0.24
                alpha = fast_atk if target > self._scores[k] else 0.11
                self._scores[k] += (target - self._scores[k]) * alpha

            # Elección con histéresis para evitar parpadeos
            best_k = max(EMOTION_IDS, key=lambda k: self._scores[k])
            best_v = self._scores[best_k]
            thr_switch = 0.42 - (sens - 0.5) * 0.18   # sensibilidad alta = umbral más bajo (~0.33)
            if best_k != "neutral" and best_v >= thr_switch:
                if best_k != self._cur_emotion:
                    if t >= self._hold_until or best_v > self._scores.get(self._cur_emotion, 0.0) + 0.18:
                        self._cur_emotion = best_k
                        self._last_change_t = t
                        hold_s = 0.95 if best_k in ("laugh", "surprised") else 0.65
                        self._hold_until = t + hold_s
                else:
                    self._hold_until = max(self._hold_until, t + 0.35)
                self._cur_conf = min(1.0, best_v)
            elif t >= self._hold_until and self._scores.get(self._cur_emotion, 0.0) < thr_switch * 0.72:
                self._cur_emotion = "neutral"
                self._cur_conf = max(0.0, self._scores["neutral"])

    def get_state(self):
        """Devuelve dict ligero con emoción actual, confianza, métricas y mapa de puntuaciones."""
        with self._lock:
            return {
                "emotion": self._cur_emotion,
                "confidence": round(float(self._cur_conf), 3),
                "pitch_hz": round(float(self.pitch_hz), 1),
                "energy": round(float(self.energy), 3),
                "brightness": round(float(self.brightness), 3),
                "rhythm_hz": round(float(self.rhythm_hz), 2),
                "voiced": bool(self.is_voiced),
                "scores": dict(self._scores),
            }

    def voice_choreography(self, now, level, emotion=None):
        """Genera movimiento orgánico de cabeza (hx, hy, hz) y mirada (gx, gy) impulsado SOLO por la voz."""
        em = emotion or self._cur_emotion
        lv = max(0.0, min(1.0, float(level)))
        # Cadencia suave basada en tiempo + energía vocal (sin sentirse robótico)
        talk_w = min(1.0, lv * 1.65)
        hx = math.sin(now * 1.35) * 0.16 + math.sin(now * 2.75) * 0.28 * talk_w
        hy = math.sin(now * 1.90 + 0.7) * 0.10 - lv * 0.22
        hz = math.sin(now * 2.15 + 1.2) * 0.22 * talk_w

        if em == "happy":
            hx += math.sin(now * 3.4) * 0.22
            hy -= 0.12 + abs(math.sin(now * 4.2)) * 0.14
            hz += math.sin(now * 3.4) * 0.28
        elif em == "laugh":
            # Asentimientos rápidos rítmicos de carcajada
            hx += math.sin(now * 5.2) * 0.16
            hy += math.sin(now * 13.5) * 0.24 - 0.08
            hz += math.sin(now * 6.8) * 0.24
        elif em == "shout":
            # Inclinación hacia atrás / empuje dramático + micro-vibración
            hx += math.sin(now * 29.0) * 0.08
            hy -= 0.26 + math.sin(now * 24.0) * 0.06
            hz += math.sin(now * 19.0) * 0.14
        elif em == "sad":
            # Cabeza ligeramente caída y balanceo lento melancólico
            hx = math.sin(now * 0.75) * 0.14
            hy = 0.34 + math.sin(now * 0.95) * 0.07
            hz = -0.22 + math.sin(now * 0.65) * 0.08
        elif em == "angry":
            # Inclinación hacia adelante tensa y directa
            hx = math.sin(now * 2.4) * 0.10 + math.sin(now * 18.0) * 0.04 * talk_w
            hy = 0.16 + math.sin(now * 5.0) * 0.06 * talk_w
            hz = math.sin(now * 3.8) * 0.18
        elif em == "surprised":
            # Sobresalto hacia atrás
            hy = -0.34 + math.sin(now * 8.0) * 0.05
            hz = math.sin(now * 4.0) * 0.10

        gx = max(-1.0, min(1.0, hx * 0.95 + math.sin(now * 0.85) * 0.20))
        gy = max(-1.0, min(1.0, hy * 0.75))
        return (
            max(-1.0, min(1.0, hx)),
            max(-1.0, min(1.0, hy)),
            max(-1.0, min(1.0, hz)),
            gx,
            gy,
        )
