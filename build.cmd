@echo off
rem Build WhisperSub: venv -> deps -> icon -> PyInstaller (dist\WhisperSub) -> Inno Setup (dist\WhisperSub-Setup-x.y.z.exe)
rem Requires: python.org CPython 3.11/3.12 x64 (NOT conda - its older MSVC runtime breaks Qt 6.12), Inno Setup 6
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

rem --- pick a python.org interpreter (override with:  set BASEPY=C:\path\python.exe)
if not defined BASEPY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "BASEPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined BASEPY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "BASEPY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined BASEPY if exist "%ProgramFiles%\Python312\python.exe" set "BASEPY=%ProgramFiles%\Python312\python.exe"
if not defined BASEPY if exist "%ProgramFiles%\Python311\python.exe" set "BASEPY=%ProgramFiles%\Python311\python.exe"
if not defined BASEPY set "BASEPY=python"

if not exist .venv\Scripts\python.exe (
    echo [1/5] creating .venv with %BASEPY%
    "%BASEPY%" -m venv .venv || goto :fail
)
set "PY=%CD%\.venv\Scripts\python.exe"

echo [2/5] installing dependencies
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt -r requirements-build.txt || goto :fail
"%PY%" -c "import PySide6.QtCore" || (echo PySide6 import failed - use a python.org interpreter, not conda & goto :fail)

echo [3/5] generating icon
"%PY%" tools\make_icon.py || goto :fail

echo [4/5] PyInstaller
if exist dist\WhisperSub rmdir /s /q dist\WhisperSub
"%PY%" -m PyInstaller build\whispersub.spec --noconfirm --clean --log-level WARN || goto :fail
if not exist dist\WhisperSub\WhisperSub.exe goto :fail

"%PY%" -c "import whispersub;open('build\\version.txt','w').write(whispersub.__version__)"
set /p VER=<build\version.txt
del build\version.txt
echo [5/5] Inno Setup (version %VER%)
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo ISCC.exe not found - install Inno Setup 6:  winget install JRSoftware.InnoSetup
    goto :fail
)
"%ISCC%" /Q /DAppVersion=%VER% build\installer.iss || goto :fail

echo.
echo DONE:  dist\WhisperSub\WhisperSub.exe   and   dist\WhisperSub-Setup-%VER%.exe
exit /b 0

:fail
echo BUILD FAILED
exit /b 1
