"""Runtime helpers: DLL search paths (CUDA runtime from pip nvidia-* wheels), app data dirs, logging."""
import os
import sys
import logging
from pathlib import Path

from . import APP_NAME


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) is not False


def bundle_dir() -> Path:
    """Directory holding bundled resources (PyInstaller _internal) or the package parent in dev mode."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup_dll_paths() -> list:
    """Register nvidia/{cublas,cudnn,cuda_nvrtc}/bin so CTranslate2 can find the CUDA runtime on Windows."""
    if os.name != "nt":
        return []
    roots = []
    if is_frozen():
        roots.append(bundle_dir() / "nvidia")
    try:
        import nvidia  # namespace package from nvidia-*-cu12 wheels
        roots += [Path(p) for p in nvidia.__path__]
    except ImportError:
        pass
    added = []
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            p = root / sub / "bin"
            if p.is_dir():
                try:
                    os.add_dll_directory(str(p))
                except (AttributeError, OSError):
                    pass
                os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(p))
    return added


def setup_logging() -> Path:
    log_path = app_data_dir() / "whispersub.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    return log_path
