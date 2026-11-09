# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one onedir bundle with two launchers
   WhisperSub.exe      – GUI (windowed)
   whispersub-cli.exe  – console CLI
Run from repo root:  pyinstaller build/whispersub.spec --noconfirm
"""
import glob
import os
import sys
import sysconfig
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from whispersub import __version__  # noqa: E402

site = sysconfig.get_paths()["purelib"]

datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface_hub"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# CUDA runtime from pip nvidia-* wheels → <bundle>/nvidia/<pkg>/bin/*.dll  (loaded via os.add_dll_directory)
for pkg in ("cublas", "cudnn", "cuda_nvrtc"):
    for dll in glob.glob(os.path.join(site, "nvidia", pkg, "bin", "*.dll")):
        binaries.append((dll, os.path.join("nvidia", pkg, "bin")))

datas += [(os.path.join(ROOT, "assets", "icon.ico"), "assets"),
          (os.path.join(ROOT, "assets", "icon.png"), "assets")]

excludes = ["tkinter", "matplotlib", "scipy", "pandas", "torch", "IPython", "jedi",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
            "PySide6.Qt3DRender", "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
            "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtBluetooth",
            "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "PySide6.QtTest", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtDesigner", "PySide6.QtHelp",
            "PySide6.QtSql", "PySide6.QtXml", "PySide6.QtSvgWidgets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
            "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
            "PySide6.QtNfc", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtGraphs", "PySide6.QtQuickWidgets"]

a = Analysis([os.path.join(ROOT, "launcher.py")],
             pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports + ["whispersub", "whispersub.gui", "whispersub.pipeline", "whispersub.runtime"],
             hookspath=[], runtime_hooks=[], excludes=excludes, noarchive=False)
# --- MSVC runtime: PyInstaller may pick an older msvcp140/vcruntime140 from PATH (e.g. conda), which breaks Qt 6.12
#     ("DLL load failed while importing QtCore: specified procedure could not be found").
#     Ship the newest copies from System32 at the top level instead, so they win the DLL search.
import re
_RT = re.compile(r"^(msvcp140.*|vcruntime140.*|concrt140|vcomp140)\.dll$", re.I)
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
a.binaries = [b for b in a.binaries if not (os.path.dirname(b[0]) == "" and _RT.match(os.path.basename(b[0])))]
# Qt 6.12 links against the Windows system ICU (System32\icuuc.dll). PyInstaller may bundle an incompatible icuuc.dll
# found on PATH (e.g. conda's ICU 73) which shadows the system one -> drop any top-level ICU / dbghelp copies.
_SYS_ONLY = re.compile(r"^(icu\w*|dbghelp|dbgcore)\.dll$", re.I)
a.binaries = [b for b in a.binaries if not (os.path.dirname(b[0]) == "" and _SYS_ONLY.match(os.path.basename(b[0])))]
for _n in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
           "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll", "concrt140.dll", "vcomp140.dll"):
    _src = os.path.join(_sys32, _n)
    if os.path.exists(_src):
        a.binaries.append((_n, _src, "BINARY"))

pyz = PYZ(a.pure)

icon = os.path.join(ROOT, "assets", "icon.ico")
exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WhisperSub", icon=icon,
              console=False, debug=False, strip=False, upx=False,
              version=None)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="whispersub-cli", icon=icon,
              console=True, debug=False, strip=False, upx=False)
coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="WhisperSub")
