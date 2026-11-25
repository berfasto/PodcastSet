"""PodcastSet Studio — Suite de producción VTuber 2D & 3D, Audio DSP y Streaming (.setxd / .vtr / .vt3d).
Ejecutar:  python main.py
"""
import os
import subprocess
import sys
from pathlib import Path


def _ensure_venv():
    """Si se ejecuta con el Python global sin dependencias, relanza automáticamente con mi_set_entorno."""
    try:
        import PySide6  # noqa: F401
        return
    except ImportError:
        pass
    base = Path(__file__).resolve().parent
    for venv_py in (
        base / "mi_set_entorno" / "Scripts" / "python.exe",
        base / ".venv" / "Scripts" / "python.exe",
        base / "mi_set_entorno" / "bin" / "python",
        base / ".venv" / "bin" / "python",
    ):
        if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
            if not os.environ.get("_PODCASTSET_REEXEC"):
                env = os.environ.copy()
                env["_PODCASTSET_REEXEC"] = "1"
                sys.exit(subprocess.call([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]], env=env))


_ensure_venv()

from PySide6.QtWidgets import QApplication

import theme
from app_window import MainWindow


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("PodcastSet Studio")
    theme.apply(app)
    win = MainWindow()
    win.show()
    win.init_native_frameless()
    if "--check" in sys.argv:
        app.processEvents()
        win.close()
        print("PodcastSet Studio OK")
        sys.exit(0)
    sys.exit(app.exec())
