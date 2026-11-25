"""Seguimiento facial por webcam robusto y 100% nativo en OpenCV 5.x (sin archivos XML externos):
- Rastrea cabeza X, Y, inclinación (Roll Z), acercamiento (Zoom), parpadeo y sonrisa/boca.
- Combina detección de rostro (Haar si existe + YCrCb piel/elipse adaptativa) con Flujo Óptico
  Lucas-Kanade (calcOpticalFlowPyrLK + estimateAffinePartial2D) en hilo daemon a ~25 FPS.
- Entrega además una miniatura en vivo opcional con guías limpias para calibrar al instante."""
import math
import threading
import time
from pathlib import Path

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None


def list_camera_devices(max_probe=4):
    """Lista índices de webcams disponibles rápidamente (sin bloquear si no hay cámara)."""
    if cv2 is None:
        return []
    found = []
    for idx in range(max_probe):
        cap = None
        try:
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                found.append((f"Cámara {idx + 1}", idx))
        except Exception:
            pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    return found or [("Cámara predeterminada (0)", 0)]


class FaceTracker:
    """Rastreador facial 2D/3D ligero con Optical Flow + detección de parpadeo y sonrisa."""

    def __init__(self):
        self.running = False
        self.status = "Inactivo"
        self.device_index = 0
        self._thread = None
        self._lock = threading.Lock()
        self._want_recalib = False

        # Pose suavizada normalizada (-1..1)
        self.x = 0.0           # Yaw / movimiento horizontal (espejo)
        self.y = 0.0           # Pitch / movimiento vertical
        self.roll = 0.0        # Inclinación Z de cabeza (-1..1 ~ ±30°)
        self.zoom = 0.0        # Acercamiento / alejamiento
        self.blink = 0.0       # 0 = abierto, 1 = cerrado
        self.smile = 0.0       # 0 = neutro, 1 = sonrisa amplia
        self.mouth_open = 0.0  # 0..1 apertura estimada visualmente
        self.detected = False

        # Referencia de calibración
        self._center_x = 0.5
        self._center_y = 0.48
        self._center_s = 0.34
        self._base_eye_dark = None
        self._base_mouth_dark = None
        self._base_mouth_w = None

        # Último frame RGB compacto para vista previa opcional (160x90)
        self._preview_rgb = None
        self._preview_ts = 0.0

    def available(self):
        return cv2 is not None

    def start(self, device_index=0):
        self.stop()
        if cv2 is None:
            self.status = "OpenCV no disponible"
            return False, self.status
        self.device_index = int(device_index)
        self.running = True
        self._want_recalib = True
        self.status = "Iniciando cámara..."
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True, ""

    def stop(self):
        self.running = False
        with self._lock:
            self.detected = False
            self.x = self.y = self.roll = self.zoom = 0.0
            self.blink = self.smile = self.mouth_open = 0.0
            self._preview_rgb = None
            self.status = "Inactivo"

    def recalibrate(self):
        with self._lock:
            self._want_recalib = True
            self._base_eye_dark = None
            self._base_mouth_dark = None
            self._base_mouth_w = None

    def get_pose(self):
        """Compatibilidad: devuelve (x, y, zoom) en -1..1."""
        if not self.running:
            return 0.0, 0.0, 0.0
        with self._lock:
            return self.x, self.y, self.zoom

    def get_full_pose(self):
        """Devuelve diccionario completo con cabeza (x, y, roll, zoom) y rostro (blink, smile, mouth_open)."""
        if not self.running:
            return {
                "x": 0.0, "y": 0.0, "roll": 0.0, "zoom": 0.0,
                "blink": 0.0, "smile": 0.0, "mouth_open": 0.0,
                "detected": False, "status": self.status,
            }
        with self._lock:
            return {
                "x": float(self.x),
                "y": float(self.y),
                "roll": float(self.roll),
                "zoom": float(self.zoom),
                "blink": float(self.blink),
                "smile": float(self.smile),
                "mouth_open": float(self.mouth_open),
                "detected": bool(self.detected),
                "status": self.status,
            }

    def get_preview_rgb(self):
        """Devuelve (numpy_rgb_HxWx3, timestamp) o (None, 0.0) para mostrar miniatura opcional."""
        with self._lock:
            if self._preview_rgb is None:
                return None, 0.0
            return self._preview_rgb.copy(), self._preview_ts

    # ---- Detección y seguimiento por frame (también usable directo en tests sin webcam física) ----
    @staticmethod
    def _detect_face_box_skin(bgr_small, clf=None):
        """Localiza el rostro usando Haar (si existe) o detección adaptativa YCrCb + contorno elíptico."""
        h, w = bgr_small.shape[:2]
        gray = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2GRAY)
        if clf is not None:
            faces = clf.detectMultiScale(gray, scaleFactor=1.18, minNeighbors=4, minSize=(28, 28))
            if len(faces) > 0:
                fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                return (int(fx), int(fy), int(fw), int(fh))

        # Detector robusto de piel en YCrCb (funciona en OpenCV 5.1 sin ningún XML externo)
        ycrcb = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, np.array([35, 132, 76], dtype=np.uint8), np.array([245, 178, 130], dtype=np.uint8))
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ker)

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_score = 0.0
        for c in cnts:
            area = cv2.contourArea(c)
            if area < (w * h) * 0.025:
                continue
            bx, by, bw, bh = cv2.boundingRect(c)
            aspect = bh / max(1.0, float(bw))
            if aspect < 0.72 or aspect > 2.35:
                continue
            # Preferir región centrada y en la mitad superior (rostro frente a la cámara)
            cx_n = (bx + bw * 0.5) / float(w)
            cy_n = (by + bh * 0.5) / float(h)
            center_w = max(0.2, 1.0 - abs(cx_n - 0.5) * 1.4 - abs(cy_n - 0.44) * 1.1)
            score = area * center_w
            if score > best_score:
                best_score = score
                # Recortar cuello si el contorno incluye torso/cuello
                fh = min(bh, int(bw * 1.28))
                best = (bx, by, bw, fh)

        if best is not None:
            return best

        # Fallback por contraste/bordes centrados (si la iluminación es muy fría/RGB)
        edges = cv2.Canny(gray, 50, 140)
        ys, xs = np.where(edges > 0)
        if len(xs) > 45:
            x0, x1 = int(np.percentile(xs, 15)), int(np.percentile(xs, 85))
            y0, y1 = int(np.percentile(ys, 12)), int(np.percentile(ys, 82))
            bw, bh = max(36, x1 - x0), max(36, y1 - y0)
            if bw < w * 0.85 and bh < h * 0.9:
                return (x0, y0, bw, min(bh, int(bw * 1.25)))
        return None

    def _seed_lk_points(self, gray, box):
        bx, by, bw, bh = box
        h, w = gray.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)
        cx, cy = bx + bw // 2, by + bh // 2
        cv2.ellipse(mask, (cx, cy), (max(8, int(bw * 0.40)), max(8, int(bh * 0.42))), 0, 0, 360, 255, -1)
        pts = cv2.goodFeaturesToTrack(gray, maxCorners=36, qualityLevel=0.02, minDistance=4, mask=mask)
        return pts

    def _analyze_face_expressions(self, gray, box):
        """Estima parpadeo (ojos) y apertura de boca/sonrisa desde bandas locales del rostro."""
        h, w = gray.shape[:2]
        bx, by, bw, bh = box
        x0, x1 = max(0, bx), min(w, bx + bw)
        y0, y1 = max(0, by), min(h, by + bh)
        if x1 - x0 < 20 or y1 - y0 < 20:
            return 0.0, 0.0, 0.0

        roi = gray[y0:y1, x0:x1].astype(np.float32)
        rh, rw = roi.shape
        # Normalizar iluminación local del rostro para robustez
        roi_mean = float(roi.mean()) + 1e-4
        roi_norm = roi / roi_mean

        # Banda de ojos (26%..48% altura, 16%..84% ancho)
        eye_band = roi_norm[int(rh * 0.26):int(rh * 0.48), int(rw * 0.16):int(rw * 0.84)]
        # Medida de contraste vertical/oscuro de iris+pupila (al parpadear baja el contraste oscuro)
        eye_contrast = float(np.std(eye_band)) if eye_band.size else 0.15

        # Banda de boca (62%..86% altura, 22%..78% ancho)
        m_band = roi_norm[int(rh * 0.62):int(rh * 0.86), int(rw * 0.22):int(rw * 0.78)]
        if m_band.size:
            dark_mask = m_band < 0.74
            mouth_dark_frac = float(dark_mask.mean())
            cols_dark = dark_mask.any(axis=0)
            mouth_w_frac = float(cols_dark.mean())
        else:
            mouth_dark_frac, mouth_w_frac = 0.05, 0.2

        if self._base_eye_dark is None:
            self._base_eye_dark = eye_contrast
            self._base_mouth_dark = mouth_dark_frac
            self._base_mouth_w = mouth_w_frac
        else:
            self._base_eye_dark += (eye_contrast - self._base_eye_dark) * 0.015
            self._base_mouth_dark += (mouth_dark_frac - self._base_mouth_dark) * 0.012
            self._base_mouth_w += (mouth_w_frac - self._base_mouth_w) * 0.012

        # Parpadeo cuando el contraste de ojos cae claramente respecto a la base
        eye_drop = (self._base_eye_dark - eye_contrast) / max(0.05, self._base_eye_dark)
        blink = max(0.0, min(1.0, (eye_drop - 0.14) * 3.8))

        # Apertura de boca cuando crece la cavidad oscura central
        m_open = max(0.0, min(1.0, (mouth_dark_frac - self._base_mouth_dark - 0.03) * 5.0))

        # Sonrisa cuando se ensancha horizontalmente la banda de la boca sin abrir demasiado verticalmente
        smile = max(0.0, min(1.0, (mouth_w_frac - self._base_mouth_w - 0.05) * 3.4))
        return blink, smile, m_open

    def process_frame_sync(self, bgr_frame, state_dict, clf=None):
        """Procesa un frame BGR (240x135 o cualquiera) actualizando pose, flujo óptico y vista previa."""
        small = cv2.resize(bgr_frame, (240, 135))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        prev_gray = state_dict.get("prev_gray")
        prev_pts = state_dict.get("prev_pts")
        box = state_dict.get("box")
        frame_n = state_dict.get("frame_n", 0) + 1
        state_dict["frame_n"] = frame_n
        roll_deg = state_dict.get("roll_deg", 0.0)
        used_lk = False

        if prev_gray is not None and prev_pts is not None and box is not None and len(prev_pts) >= 6:
            nxt_pts, st, _ = cv2.calcOpticalFlowPyrLK(
                prev_gray, gray, prev_pts, None,
                winSize=(17, 17), maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.03),
            )
            if nxt_pts is not None and st is not None:
                good_old = prev_pts[st.ravel() == 1]
                good_new = nxt_pts[st.ravel() == 1]
                if len(good_new) >= 6:
                    M_aff, _ = cv2.estimateAffinePartial2D(good_old, good_new, method=cv2.RANSAC, ransacReprojThreshold=3.0)
                    if M_aff is not None:
                        a, b, tx = float(M_aff[0, 0]), float(M_aff[0, 1]), float(M_aff[0, 2])
                        c, d, ty = float(M_aff[1, 0]), float(M_aff[1, 1]), float(M_aff[1, 2])
                        sc = max(0.85, min(1.18, math.hypot(a, c)))
                        d_roll = math.degrees(math.atan2(c, a))
                        bx, by, bw, bh = box
                        cx = bx + bw * 0.5
                        cy = by + bh * 0.5
                        ncx = a * cx + b * cy + tx
                        ncy = c * cx + d * cy + ty
                        nbw = max(28.0, min(w * 0.82, bw * sc))
                        nbh = max(28.0, min(h * 0.90, bh * sc))
                        box = (
                            int(max(0, min(w - nbw, ncx - nbw * 0.5))),
                            int(max(0, min(h - nbh, ncy - nbh * 0.5))),
                            int(nbw),
                            int(nbh),
                        )
                        roll_deg = max(-35.0, min(35.0, roll_deg * 0.96 + d_roll))
                        prev_pts = good_new.reshape(-1, 1, 2)
                        used_lk = True

        # Re-anclaje periódico o inicial con detector de rostro (evita deriva de optical flow)
        need_detect = (not used_lk) or (box is None) or (frame_n % 14 == 0) or (prev_pts is None or len(prev_pts) < 10)
        if need_detect:
            det = self._detect_face_box_skin(small, clf=clf)
            if det is not None:
                if box is not None and used_lk:
                    # Fusión suave hacia la detección real para eliminar deriva sin saltos
                    box = (
                        int(box[0] * 0.65 + det[0] * 0.35),
                        int(box[1] * 0.65 + det[1] * 0.35),
                        int(box[2] * 0.65 + det[2] * 0.35),
                        int(box[3] * 0.65 + det[3] * 0.35),
                    )
                else:
                    box = det
                    roll_deg *= 0.85
                seeded = self._seed_lk_points(gray, box)
                if seeded is not None and len(seeded) >= 6:
                    prev_pts = seeded
                used_lk = True

        state_dict["prev_gray"] = gray
        state_dict["prev_pts"] = prev_pts
        state_dict["box"] = box
        state_dict["roll_deg"] = roll_deg

        # Preparar miniatura limpia RGB (160x90) con guía sutil del rostro
        prev_bgr = cv2.flip(cv2.resize(small, (160, 90)), 1)
        sx_p, sy_p = 160.0 / w, 90.0 / h

        if box is not None and used_lk:
            bx, by, bw, bh = box
            cx_n = (bx + bw * 0.5) / float(w)
            cy_n = (by + bh * 0.5) / float(h)
            sc_n = bw / float(w)
            blink, smile, m_open = self._analyze_face_expressions(gray, box)

            with self._lock:
                if self._want_recalib:
                    self._center_x = cx_n
                    self._center_y = cy_n
                    self._center_s = max(0.12, sc_n)
                    roll_deg = 0.0
                    state_dict["roll_deg"] = 0.0
                    self._want_recalib = False

                # Espejo horizontal natural
                tx = max(-1.0, min(1.0, -(cx_n - self._center_x) * 3.2))
                ty = max(-1.0, min(1.0, (cy_n - self._center_y) * 3.0))
                tz = max(-1.0, min(1.0, (sc_n - self._center_s) * 3.8))
                tr = max(-1.0, min(1.0, -roll_deg / 24.0))

                self.x += (tx - self.x) * 0.28
                self.y += (ty - self.y) * 0.28
                self.zoom += (tz - self.zoom) * 0.20
                self.roll += (tr - self.roll) * 0.26
                self.blink += (blink - self.blink) * 0.45
                self.smile += (smile - self.smile) * 0.25
                self.mouth_open += (m_open - self.mouth_open) * 0.35
                self.detected = True

            # Dibujar rectángulo espejo sobre la vista previa
            mx0 = int(160 - (bx + bw) * sx_p)
            my0 = int(by * sy_p)
            mx1 = int(160 - bx * sx_p)
            my1 = int((by + bh) * sy_p)
            cv2.rectangle(prev_bgr, (mx0, my0), (mx1, my1), (246, 124, 47), 1)
            cv2.circle(prev_bgr, ((mx0 + mx1) // 2, (my0 + my1) // 2), 2, (125, 182, 46), -1)
        else:
            with self._lock:
                self.x *= 0.92
                self.y *= 0.92
                self.roll *= 0.92
                self.zoom *= 0.92
                self.blink *= 0.85
                self.smile *= 0.88
                self.mouth_open *= 0.85
                self.detected = False

        prev_rgb = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._preview_rgb = prev_rgb
            self._preview_ts = time.time()

    def _loop(self):
        # Cargar Haar solo si realmente existe el XML (en OpenCV 5.1 puede no existir -> usamos nuestro tracker nativo)
        clf = None
        try:
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            if cascade_path.exists():
                c = cv2.CascadeClassifier(str(cascade_path))
                if not c.empty():
                    clf = c
        except Exception:
            clf = None

        cap = None
        try:
            cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.device_index)
            if not cap.isOpened():
                self.status = "No se encontró webcam"
                self.running = False
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 180)
            cap.set(cv2.CAP_PROP_FPS, 25)
            self.status = "Cámara activa (Tracking)"
            st_dict = {}

            while self.running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.04)
                    continue
                self.process_frame_sync(frame, st_dict, clf=clf)
                time.sleep(0.032)
        except Exception as e:
            self.status = f"Error cámara: {e}"
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self.running = False
            with self._lock:
                self.detected = False
