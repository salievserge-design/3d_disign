# -*- mode: python ; coding: utf-8 -*-
"""Сборка НОМЕРОК 3D в один .exe:  pyinstaller --noconfirm --clean nomerok3d.spec"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),
    ("fonts/RoadNumbers2.0.ttf", "fonts"),
    ("fonts/DejaVuSans-Bold.ttf", "fonts"),
    ("fonts/DejaVu-LICENSE.txt", "fonts"),
]
binaries, hiddenimports = [], ["app", "app.core", "app.server", "generate_keychain"]

# шапки-невидимки: shapely/manifold3d/trimesh тащат за собой бинарники и данные
for pkg in ("shapely", "trimesh", "manifold3d", "mapbox_earcut", "fontTools", "flask", "webview"):
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
    name="NOMEROK-3D",
    icon="app/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,               # обычное оконное приложение, без чёрной консоли
    disable_windowed_traceback=False,
    version=None,
)
