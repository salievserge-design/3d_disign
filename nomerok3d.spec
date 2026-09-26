# -*- mode: python ; coding: utf-8 -*-
"""Сборка НОМЕРОК 3D в один .exe:  pyinstaller --noconfirm --clean nomerok3d.spec"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# NOMEROK_CONSOLE=1 собирает консольный вариант: он печатает причину падения
# текстом вместо молчаливого окна. Им проверяется сборка на CI, и он же идёт
# в релиз вторым файлом — «запусти этот, если обычный не открывается».
CONSOLE = os.environ.get("NOMEROK_CONSOLE") == "1"

datas = [
    ("app/static", "app/static"),
    ("fonts/RoadNumbers2.0.ttf", "fonts"),
    ("fonts/DejaVuSans-Bold.ttf", "fonts"),
    ("fonts/DejaVu-LICENSE.txt", "fonts"),
]
binaries, hiddenimports = [], ["app", "app.core", "app.server", "generate_keychain"]

# шапки-невидимки: numpy/shapely/manifold3d/trimesh тащат за собой бинарники,
# данные и подмодули, которые автоматика PyInstaller находит не всегда
for pkg in ("numpy", "shapely", "trimesh", "manifold3d", "mapbox_earcut",
            "fontTools", "flask", "webview"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:                      # пакета может не быть — не беда
        print(f"[spec] пропускаю {pkg}: {e}")

hiddenimports += collect_submodules("trimesh.exchange")

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "sympy", "IPython", "jupyter", "notebook",
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "test", "pydoc_data",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NOMEROK-3D-console" if CONSOLE else "NOMEROK-3D",
    icon="app/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=CONSOLE,             # обычно — оконное приложение, без чёрной консоли
    disable_windowed_traceback=False,
    version=None,
)
