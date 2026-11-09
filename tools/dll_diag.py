"""Diagnose 'DLL load failed: procedure not found' in a PyInstaller onedir bundle (Windows).
Walks the import tree from a module and checks that every import resolves to an export in the DLL Windows would pick
first (bundle top-level, then the DLL's own folder, then System32).
usage: .venv\\Scripts\\python tools\\dll_diag.py dist\\WhisperSub\\_internal PySide6\\QtCore.pyd
"""
import os
import sys

import pefile

root = os.path.abspath(sys.argv[1])
start = os.path.join(root, sys.argv[2])
sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
seen, problems = set(), []


def exports(path):
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        return {e.name.decode(errors="ignore") for e in getattr(pe, "DIRECTORY_ENTRY_EXPORT").symbols if e.name} \
            if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else set()
    except Exception:  # noqa: BLE001
        return None


def resolve(name, from_dir):
    for cand in (os.path.join(root, name), os.path.join(from_dir, name), os.path.join(sys32, name)):
        if os.path.exists(cand):
            return cand
    return None


def walk(path, depth=0):
    if path.lower() in seen or depth > 6:
        return
    seen.add(path.lower())
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    except Exception as ex:  # noqa: BLE001
        problems.append(f"cannot parse {path}: {ex}")
        return
    for imp in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = imp.dll.decode(errors="ignore")
        if dll.lower().startswith(("api-ms-", "ext-ms-")):
            continue
        target = resolve(dll, os.path.dirname(path))
        if not target:
            problems.append(f"{os.path.relpath(path, root)} -> {dll}: NOT FOUND")
            continue
        if target.lower().startswith(sys32.lower()):
            continue
        exp = exports(target)
        if exp is None:
            continue
        missing = [i.name.decode(errors="ignore") for i in imp.imports if i.name and i.name.decode(errors="ignore") not in exp]
        if missing:
            problems.append(f"{os.path.relpath(path, root)} -> {os.path.relpath(target, root)}: missing {len(missing)} e.g. {missing[:3]}")
        walk(target, depth + 1)


walk(start)
print(f"checked {len(seen)} modules")
for p in problems:
    print("  ", p)
if not problems:
    print("no static import problems found")
