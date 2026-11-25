"""Ventana de Exportación profesional: presets modernos, vista previa en vivo, árbol de salida organizado,
aceleración GPU, normalización broadcast (-16 LUFS) y render 100% en segundo plano sin bloqueos."""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

import icons
import model as M
from audio_engine import clip_effective_duration
from export_engine import ExportWorker, _safe_name, detect_gpu_encoder
from theme import C
from timeline import fmt_tc


class ExportDialog(QDialog):
    def __init__(self, win):
        super().__init__(win.window() if win else None)
        self.win = win
        self._drag_pos = None
        self._mode = "both"
        self._use_zone = False
        self._worker = None
        self._done_dir = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(820, 610)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        shell = QFrame()
        shell.setObjectName("preferencesFrame")
        outer.addWidget(shell)

        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Cabecera fija Studio UI (36px)
        hdr = QFrame()
        hdr.setObjectName("preferencesTitleBar")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 0, 6, 0)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("export", C["accent"], 15))
        title = QLabel("Exportar producción")
        title.setObjectName("preferencesDialogTitle")
        hl.addWidget(ic)
        hl.addSpacing(6)
        hl.addWidget(title)
        hl.addStretch()
        self.gpu_badge = QLabel("Detectando aceleración...")
        self.gpu_badge.setObjectName("badge")
        hl.addWidget(self.gpu_badge)
        hl.addSpacing(8)
        b_x = QPushButton()
        b_x.setObjectName("preferencesCloseButton")
        b_x.setIcon(icons.state_icon("close", 13))
        b_x.setFixedSize(26, 26)
        b_x.clicked.connect(self._on_cancel)
        hl.addWidget(b_x)
        root.addWidget(hdr)

        # Cuerpo en 2 columnas: Configuración (izq) + Vista previa y árbol ordenado (der)
        main = QHBoxLayout()
        main.setContentsMargins(18, 14, 18, 12)
        main.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(10)

        # Selector de modo (5 perfiles profesionales)
        m_lbl = QLabel("FORMATO DE SALIDA")
        m_lbl.setObjectName("section")
        left.addWidget(m_lbl)

        m_row = QHBoxLayout()
        m_row.setSpacing(5)
        m_grp = QButtonGroup(self)
        self.mode_btns = {}
        modes = [
            ("both", "Video MP4"),
            ("vertical", "Shorts 9:16"),
            ("alpha", "Transparente"),
            ("video", "Solo Video"),
            ("audio", "Solo Audio"),
        ]
        for idx, (k, lbl) in enumerate(modes):
            b = QPushButton(lbl)
            b.setObjectName("choicePill")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, key=k, i=idx: self._set_mode(key, i))
            m_grp.addButton(b)
            self.mode_btns[k] = b
            m_row.addWidget(b, 1)
        self.mode_btns["both"].setChecked(True)
        left.addLayout(m_row)

        # Panel de ajustes por modo (altura fija -> 0 saltos)
        opt_box = QFrame()
        opt_box.setObjectName("propCard")
        ov = QVBoxLayout(opt_box)
        ov.setContentsMargins(14, 12, 14, 12)
        ov.setSpacing(9)

        self.mode_stack = QStackedWidget()
        self.mode_stack.setFixedHeight(148)

        # Página 0: Video MP4 / Vertical / Solo Video (compartida)
        p_vid = QWidget()
        vv = QVBoxLayout(p_vid)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(8)
        self.vid_desc = QLabel("Video H.264 con audio AAC 256 kbps listo para YouTube, Twitch o edición.")
        self.vid_desc.setProperty("muted", True)
        vv.addWidget(self.vid_desc)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("Resolución"))
        self.cb_res = QComboBox()
        self._fill_res_standard()
        r1.addWidget(self.cb_res, 1)
        r1.addWidget(QLabel("FPS"))
        self.cb_fps = QComboBox()
        for f in (24, 30, 60):
            self.cb_fps.addItem(f"{f} FPS", f)
        self.cb_fps.setCurrentIndex(max(0, self.cb_fps.findData(int(win.data.get("fps", 30)))))
        r1.addWidget(self.cb_fps)
        vv.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(QLabel("Calidad"))
        self.cb_qual = QComboBox()
        self.cb_qual.addItem("Rápido (Borrador)", "fast")
        self.cb_qual.addItem("Equilibrado (Recomendado)", "balanced")
        self.cb_qual.addItem("Master Alta Calidad", "high")
        self.cb_qual.setCurrentIndex(1)
        r2.addWidget(self.cb_qual, 1)
        self.cb_gpu = QCheckBox("Usar GPU")
        self.cb_gpu.setChecked(bool(win.data["settings"].get("export_gpu", True)))
        r2.addWidget(self.cb_gpu)
        vv.addLayout(r2)

        r3 = QHBoxLayout()
        r3.setSpacing(12)
        self.cb_extra_stems = QCheckBox("Incluir voces separadas (stems)")
        self.cb_extra_stems.toggled.connect(self._update_tree_preview)
        self.cb_extra_mix = QCheckBox("Incluir mezcla WAV aparte")
        self.cb_extra_mix.toggled.connect(self._update_tree_preview)
        r3.addWidget(self.cb_extra_stems)
        r3.addWidget(self.cb_extra_mix)
        vv.addLayout(r3)
        vv.addStretch()
        self.mode_stack.addWidget(p_vid)

        # Página 1: Overlay Transparente (Alpha)
        p_alp = QWidget()
        va = QVBoxLayout(p_alp)
        va.setContentsMargins(0, 0, 0, 0)
        va.setSpacing(8)
        da = QLabel("Exporta tus personajes y capas sin fondo (canal Alfa real) para Premiere, DaVinci, After Effects u OBS.")
        da.setProperty("muted", True)
        da.setWordWrap(True)
        va.addWidget(da)
        ra = QHBoxLayout()
        ra.setSpacing(8)
        ra.addWidget(QLabel("Contenedor"))
        self.cb_alpha_codec = QComboBox()
        self.cb_alpha_codec.addItem("WebM (VP9 + Alfa · Ligero)", "webm")
        self.cb_alpha_codec.addItem("MOV (QuickTime RGBA · Sin pérdida)", "mov")
        self.cb_alpha_codec.currentIndexChanged.connect(self._update_tree_preview)
        ra.addWidget(self.cb_alpha_codec, 1)
        ra.addWidget(QLabel("FPS"))
        self.cb_alpha_fps = QComboBox()
        for f in (30, 60):
            self.cb_alpha_fps.addItem(f"{f} FPS", f)
        ra.addWidget(self.cb_alpha_fps)
        va.addLayout(ra)
        self.cb_alpha_audio = QCheckBox("Incluir pista de audio en el overlay transparente")
        va.addWidget(self.cb_alpha_audio)
        va.addStretch()
        self.mode_stack.addWidget(p_alp)

        # Página 2: Solo Audio
        p_aud = QWidget()
        vu = QVBoxLayout(p_aud)
        vu.setContentsMargins(0, 0, 0, 0)
        vu.setSpacing(8)
        du = QLabel("Exporta únicamente el audio multipista con calidad de estudio y nombres ordenados.")
        du.setProperty("muted", True)
        vu.addWidget(du)
        ru = QHBoxLayout()
        ru.setSpacing(8)
        ru.addWidget(QLabel("Pistas"))
        self.cb_aud_mode = QComboBox()
        self.cb_aud_mode.addItem("Paquete completo (Mezcla + Voces)", "both")
        self.cb_aud_mode.addItem("Solo mezcla estéreo", "mixed")
        self.cb_aud_mode.addItem("Solo voces separadas", "separate")
        self.cb_aud_mode.currentIndexChanged.connect(self._update_tree_preview)
        ru.addWidget(self.cb_aud_mode, 1)
        ru.addWidget(QLabel("Formato"))
        self.cb_aud_fmt = QComboBox()
        for f in ("WAV", "MP3", "FLAC", "OGG"):
            self.cb_aud_fmt.addItem(f, f.lower())
        self.cb_aud_fmt.currentIndexChanged.connect(self._update_tree_preview)
        ru.addWidget(self.cb_aud_fmt)
        vu.addLayout(ru)
        vu.addStretch()
        self.mode_stack.addWidget(p_aud)

        ov.addWidget(self.mode_stack)
        left.addWidget(opt_box)

        # Tramo y opciones de empaquetado
        dst_box = QFrame()
        dst_box.setObjectName("propCard")
        dv = QVBoxLayout(dst_box)
        dv.setContentsMargins(14, 12, 14, 12)
        dv.setSpacing(8)

        total = max((c["start"] + clip_effective_duration(c) for c in win.tl_clips), default=0.0)
        io = win.data.get("in_out", [None, None])
        has_io = io[0] is not None and io[1] is not None and io[1] > io[0] + 0.05
        self._total_dur = total
        self._io = io if has_io else None

        r_rng = QHBoxLayout()
        r_rng.setSpacing(6)
        r_grp = QButtonGroup(self)
        b_full = QPushButton(f"Todo ({fmt_tc(total, True)})")
        b_full.setObjectName("choicePill")
        b_full.setCheckable(True)
        b_full.clicked.connect(lambda: setattr(self, "_use_zone", False))
        io_txt = f"Zona ({fmt_tc(io[0], True)} → {fmt_tc(io[1], True)})" if has_io else "Zona marcada (usa I / O)"
        b_zone = QPushButton(io_txt)
        b_zone.setObjectName("choicePill")
        b_zone.setCheckable(True)
        b_zone.setEnabled(has_io)
        b_zone.clicked.connect(lambda: setattr(self, "_use_zone", True))
        r_grp.addButton(b_full)
        r_grp.addButton(b_zone)
        if has_io:
            b_zone.setChecked(True)
            self._use_zone = True
        else:
            b_full.setChecked(True)
        r_rng.addWidget(b_full, 1)
        r_rng.addWidget(b_zone, 1)
        dv.addLayout(r_rng)

        rn = QHBoxLayout()
        rn.setSpacing(8)
        ln = QLabel("Nombre")
        ln.setFixedWidth(54)
        proj_nm = _safe_name(win.data.get("project_name") or (win.project_path.stem if win.project_path else "Proyecto"))
        self.name_ed = QLineEdit(proj_nm)
        self.name_ed.textChanged.connect(self._update_tree_preview)
        rn.addWidget(ln)
        rn.addWidget(self.name_ed, 1)
        dv.addLayout(rn)

        rd = QHBoxLayout()
        rd.setSpacing(8)
        ld = QLabel("Carpeta")
        ld.setFixedWidth(54)
        self.dir_ed = QLineEdit(win.data["settings"].get("save_dir", str(M.RECORDINGS)))
        b_dir = QPushButton("Elegir...")
        b_dir.clicked.connect(self._pick_dir)
        rd.addWidget(ld)
        rd.addWidget(self.dir_ed, 1)
        rd.addWidget(b_dir)
        dv.addLayout(rd)

        ro = QHBoxLayout()
        ro.setSpacing(12)
        self.cb_organize = QCheckBox("Carpeta ordenada")
        self.cb_organize.setChecked(bool(win.data["settings"].get("export_organize", True)))
        self.cb_organize.toggled.connect(self._update_tree_preview)
        self.cb_norm = QCheckBox("Normalizar (-16 LUFS)")
        self.cb_norm.setChecked(bool(win.data["settings"].get("export_normalize", True)))
        self.cb_thumb = QCheckBox("Miniatura PNG")
        self.cb_thumb.setChecked(True)
        self.cb_thumb.toggled.connect(self._update_tree_preview)
        for w in (self.cb_organize, self.cb_norm, self.cb_thumb):
            ro.addWidget(w)
        dv.addLayout(ro)

        left.addWidget(dst_box)
        left.addStretch()
        main.addLayout(left, 3)

        # Columna derecha: Vista previa + Estructura ordenada
        right = QVBoxLayout()
        right.setSpacing(10)

        p_lbl = QLabel("VISTA PREVIA")
        p_lbl.setObjectName("section")
        right.addWidget(p_lbl)

        self.prev_lbl = QLabel()
        self.prev_lbl.setFixedSize(270, 152)
        self.prev_lbl.setAlignment(Qt.AlignCenter)
        self.prev_lbl.setStyleSheet(f"background:{C['canvas_bg']}; border:1px solid {C['border']}; border-radius:6px;")
        pm = QPixmap.fromImage(win.render_frame(320, 180)).scaled(268, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.prev_lbl.setPixmap(pm)
        right.addWidget(self.prev_lbl)

        t_lbl = QLabel("ESTRUCTURA DE ARCHIVOS")
        t_lbl.setObjectName("section")
        right.addWidget(t_lbl)

        self.tree_lbl = QLabel()
        self.tree_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.tree_lbl.setWordWrap(True)
        self.tree_lbl.setStyleSheet(
            f"font-family:'Cascadia Mono','Consolas',monospace; font-size:7.8pt; color:{C['text_dim']}; "
            f"background:{C['bg2']}; border:1px solid {C['border']}; border-radius:6px; padding:10px;"
        )
        right.addWidget(self.tree_lbl, 1)
        main.addLayout(right, 2)

        root.addLayout(main, 1)

        # Barra de progreso reservada
        pbar_wrap = QVBoxLayout()
        pbar_wrap.setContentsMargins(18, 0, 18, 10)
        pbar_wrap.setSpacing(5)
        self.prog = QProgressBar()
        self.prog.setFixedHeight(7)
        self.prog.setTextVisible(False)
        self.prog.setValue(0)
        self.prog_lbl = QLabel("Listo para exportar")
        self.prog_lbl.setProperty("muted", True)
        pbar_wrap.addWidget(self.prog)
        pbar_wrap.addWidget(self.prog_lbl)
        root.addLayout(pbar_wrap)

        # Pie fijo
        foot = QFrame()
        foot.setObjectName("modalFooter")
        foot.setFixedHeight(54)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 0, 20, 0)
        self.b_open_dir = QPushButton(" Abrir carpeta")
        self.b_open_dir.setIcon(icons.icon("folder_open", C["text_dim"], 14))
        self.b_open_dir.setVisible(False)
        self.b_open_dir.clicked.connect(lambda: self._done_dir and M.open_path(self._done_dir))
        fl.addWidget(self.b_open_dir)
        fl.addStretch()
        self.b_cancel = QPushButton("Cerrar")
        self.b_cancel.setObjectName("modalBtn")
        self.b_cancel.setFixedSize(102, 32)
        self.b_cancel.clicked.connect(self._on_cancel)
        self.b_go = QPushButton("Exportar")
        self.b_go.setObjectName("primary")
        self.b_go.setFixedSize(116, 32)
        self.b_go.clicked.connect(self._start_export)
        fl.addWidget(self.b_cancel)
        fl.addSpacing(8)
        fl.addWidget(self.b_go)
        root.addWidget(foot)

        if int(win.data.get("canvas_h", 1080)) > int(win.data.get("canvas_w", 1920)):
            self.mode_btns["vertical"].setChecked(True)
            self._set_mode("vertical", 1)
        else:
            self._update_tree_preview()
        QTimer.singleShot(80, self._probe_gpu)

        if win:
            g = win.window().frameGeometry()
            self.move(max(20, g.center().x() - self.width() // 2), max(20, g.center().y() - self.height() // 2))

    def _probe_gpu(self):
        codec, label = detect_gpu_encoder()
        self.gpu_badge.setText(label)
        if not codec:
            self.cb_gpu.setChecked(False)
            self.cb_gpu.setEnabled(False)

    def _fill_res_standard(self):
        self.cb_res.clear()
        cw = int(self.win.data.get("canvas_w", 1920))
        ch = int(self.win.data.get("canvas_h", 1080))
        self.cb_res.addItem(f"Nativo del proyecto ({cw} × {ch})", (cw, ch))
        for label, sz in (("1920 × 1080 (Full HD)", (1920, 1080)),
                          ("1280 × 720 (HD · Rápido)", (1280, 720)),
                          ("3840 × 2160 (4K UHD)", (3840, 2160))):
            if sz != (cw, ch):
                self.cb_res.addItem(label, sz)

    def _fill_res_vertical(self):
        self.cb_res.clear()
        self.cb_res.addItem("1080 × 1920 (Full HD 9:16)", (1080, 1920))
        self.cb_res.addItem("720 × 1280 (HD 9:16 · Rápido)", (720, 1280))

    def _set_mode(self, mode, idx):
        self._mode = mode
        if mode == "audio":
            self.mode_stack.setCurrentIndex(2)
            self.cb_thumb.setEnabled(False)
        elif mode == "alpha":
            self.mode_stack.setCurrentIndex(1)
            self.cb_thumb.setEnabled(True)
        else:
            self.mode_stack.setCurrentIndex(0)
            self.cb_thumb.setEnabled(True)
            is_vert = (mode == "vertical")
            if is_vert:
                self._fill_res_vertical()
                self.vid_desc.setText("Encuadre vertical 9:16 listo para YouTube Shorts, TikTok y Reels.")
            else:
                self._fill_res_standard()
                self.vid_desc.setText(
                    "Solo video MP4 animado sin pista de sonido." if mode == "video"
                    else "Video H.264 con audio AAC 256 kbps listo para publicar."
                )
            has_aud = mode != "video"
            self.cb_extra_stems.setEnabled(has_aud)
            self.cb_extra_mix.setEnabled(has_aud)
        self._update_tree_preview()

    def _update_tree_preview(self):
        base = _safe_name(self.name_ed.text().strip() or "Podcast")
        org = self.cb_organize.isChecked()
        prefix = f"📁 {base}_{time.strftime('%Y-%m-%d_%H%M')}/\n" if org else "📁 (carpeta destino)/\n"
        ind = "  " if org else ""
        lines = []
        ext_a = self.cb_aud_fmt.currentData() or "wav"

        if self._mode == "audio":
            am = self.cb_aud_mode.currentData()
            a_pre = f"{ind}📁 Audio/\n" if org else ""
            if a_pre:
                lines.append(a_pre.rstrip())
            sub_ind = ind + ("  " if org else "")
            if am in ("mixed", "both"):
                lines.append(f"{sub_ind}♪ {base}_Mezcla.{ext_a}")
            if am in ("separate", "both"):
                if org:
                    lines.append(f"{sub_ind}📁 Voces/")
                for i, ch in enumerate(self.win.data["characters"][:4], 1):
                    lines.append(f"{sub_ind}  ♪ {i:02d}_{_safe_name(ch['name'])}.{ext_a}")
                if len(self.win.data["characters"]) > 4:
                    lines.append(f"{sub_ind}  … (+{len(self.win.data['characters']) - 4} voces)")
        else:
            if self._mode == "alpha":
                ext_v = "mov" if self.cb_alpha_codec.currentData() == "mov" else "webm"
                lines.append(f"{ind}🎬 {base}_Transparente.{ext_v}")
            else:
                lines.append(f"{ind}🎬 {base}.mp4")
            if self._mode in ("both", "vertical") and (self.cb_extra_mix.isChecked() or self.cb_extra_stems.isChecked()):
                if org:
                    lines.append(f"{ind}📁 Audio/")
                sub_ind = ind + ("  " if org else "")
                if self.cb_extra_mix.isChecked():
                    lines.append(f"{sub_ind}♪ {base}_Mezcla.wav")
                if self.cb_extra_stems.isChecked():
                    lines.append(f"{sub_ind}📁 Voces/ (01_..wav)")
            if self.cb_thumb.isChecked():
                lines.append(f"{ind}🖼 Miniatura.png")

        if any(m.get("kind") in ("chapter", "scene") for m in self.win.data.get("markers", [])):
            lines.append(f"{ind}📄 capitulos_youtube.txt")
        if org:
            lines.append(f"{ind}📄 resumen_exportacion.txt")
        self.tree_lbl.setText(prefix + "\n".join(lines))

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de exportación", self.dir_ed.text() or str(M.RECORDINGS))
        if d:
            self.dir_ed.setText(d)

    def _start_export(self):
        if self._worker is not None and self._worker.isRunning():
            return
        t0, t1 = (float(self._io[0]), float(self._io[1])) if (self._use_zone and self._io) else (0.0, self._total_dur)
        if t1 - t0 < 0.05:
            QMessageBox.information(self, "Sin contenido en línea de tiempo",
                                    "Añade o graba al menos un audio (o marcador) en la línea de tiempo antes de exportar.")
            return

        out_dir = Path(self.dir_ed.text().strip() or M.RECORDINGS)
        self.win.data["settings"]["save_dir"] = str(out_dir)
        self.win.data["settings"]["export_organize"] = self.cb_organize.isChecked()
        self.win.data["settings"]["export_gpu"] = self.cb_gpu.isChecked()
        self.win.data["settings"]["export_normalize"] = self.cb_norm.isChecked()

        if self._mode == "alpha":
            w, h = int(self.win.data.get("canvas_w", 1920)), int(self.win.data.get("canvas_h", 1080))
            fps = int(self.cb_alpha_fps.currentData() or 30)
        else:
            w, h = self.cb_res.currentData() or (1920, 1080)
            fps = int(self.cb_fps.currentData() or 30)

        cfg = {
            "mode": self._mode,
            "t0": t0,
            "t1": t1,
            "w": w,
            "h": h,
            "fps": fps,
            "quality": self.cb_qual.currentData() or "balanced",
            "use_gpu": self.cb_gpu.isChecked(),
            "alpha_codec": self.cb_alpha_codec.currentData() or "webm",
            "alpha_audio": self.cb_alpha_audio.isChecked(),
            "audio_mode": self.cb_aud_mode.currentData() or "both",
            "audio_fmt": self.cb_aud_fmt.currentData() or "wav",
            "extra_stems": self.cb_extra_stems.isChecked(),
            "extra_mix": self.cb_extra_mix.isChecked(),
            "organize": self.cb_organize.isChecked(),
            "normalize": self.cb_norm.isChecked(),
            "make_thumb": self.cb_thumb.isChecked() and self._mode != "audio",
            "out_dir": str(out_dir),
            "base_name": self.name_ed.text().strip() or "Podcast",
            "start_scene": self.win.cur_scene,
        }

        self._done_dir = None
        self.b_open_dir.setVisible(False)
        self.b_go.setEnabled(False)
        self.b_cancel.setText("Cancelar")
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_lbl.setText("Iniciando motor de render en segundo plano...")

        self._worker = ExportWorker(cfg, self.win.data, self.win.all_tracks(), self.win.tl_clips, self.win.mixer.sr)
        self._worker.progress.connect(self._on_progress)
        self._worker.previewReady.connect(self._on_preview)
        self._worker.finishedOk.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_progress(self, cur, tot, stage, spd, eta):
        if tot > 0:
            self.prog.setRange(0, tot)
            self.prog.setValue(cur)
            pct = int(cur * 100 / tot)
            extra = f" · {spd:.0f} fps · ~{int(eta)}s" if spd > 1.0 else ""
            self.prog_lbl.setText(f"{stage}  {pct}%{extra}")
        else:
            self.prog_lbl.setText(stage)

    def _on_preview(self, img):
        if not img.isNull():
            self.prev_lbl.setPixmap(QPixmap.fromImage(img).scaled(268, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_done(self, dest_dir, written, summary):
        self._done_dir = dest_dir
        self.b_go.setEnabled(True)
        self.b_cancel.setText("Cerrar")
        self.b_open_dir.setVisible(True)
        self.prog.setRange(0, 100)
        self.prog.setValue(100)
        self.prog_lbl.setText(f"✓ {summary}")
        names = "\n".join(f"• {Path(p).name}" for p in written[:10])
        if len(written) > 10:
            names += f"\n… (+{len(written) - 10} más)"
        res = QMessageBox.information(
            self, "Exportación completada",
            f"{summary}\n\nGuardado ordenadamente en:\n{dest_dir}\n\n{names}",
            QMessageBox.Open | QMessageBox.Ok,
        )
        if res == QMessageBox.Open:
            M.open_path(dest_dir)

    def _on_fail(self, err):
        self.b_go.setEnabled(True)
        self.b_cancel.setText("Cerrar")
        self.prog_lbl.setText("Error durante la exportación")
        QMessageBox.critical(self, "Error al exportar", err)

    def _on_cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self.prog_lbl.setText("Cancelando render...")
            self._worker.cancel()
            self._worker.wait(3000)
        self.reject()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 50:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)
