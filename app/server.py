# -*- coding: utf-8 -*-
"""HTTP-сервер конструктора: отдаёт интерфейс и считает геометрию.

Запуск в браузере:   python -m app.server --port 8080
Как настольное окно: python -m app.main
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import threading
import traceback
import zipfile

from flask import Flask, jsonify, request, send_file, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import core                                              # noqa: E402

APP_NAME = "НОМЕРОК 3D"
APP_VERSION = "1.0"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=None)
app.config["JSON_AS_ASCII"] = False

STATE = {"last_folder": None, "last_files": [], "picker": None}
_lock = threading.Lock()


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/api/env")
def env():
    return jsonify({
        "app": APP_NAME,
        "version": APP_VERSION,
        "letters": list(core.LETTERS),
        "digits": list(core.DIGITS),
        "mounts": core.MOUNTS,
        "outdir": core.default_outdir(),
        "desktop": STATE["picker"] is not None,
        "defaults": core.Spec().__dict__,
    })


@app.post("/api/preview")
def preview():
    try:
        spec = core.spec_from_dict(request.get_json(force=True) or {})
        with _lock:
            d = core.design(spec)
            svg = core.preview(d)
            st = core.stats(spec, d)
        return jsonify({"ok": True, "svg": svg, "stats": st, "spec": spec.__dict__})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/generate")
def generate():
    data = request.get_json(force=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "error": "Список пуст"}), 400
    outdir = (data.get("outdir") or core.default_outdir()).strip()
    try:
        specs = [core.spec_from_dict(it) for it in items]
        with _lock:
            res = core.generate(specs, outdir, want_stl=bool(data.get("stl", True)))
        STATE["last_folder"] = res["folder"]
        STATE["last_files"] = res["files"]
        return jsonify({"ok": True, **res})
    except PermissionError:
        return jsonify({"ok": False, "error": "Нет доступа к этой папке. "
                                              "Выбери другую — например, на рабочем столе."}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": f"Не получилось записать файлы: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/open-folder")
def open_folder():
    path = (request.get_json(force=True) or {}).get("path") or STATE["last_folder"]
    if not path or not os.path.isdir(path):
        return jsonify({"ok": False, "error": "Папки ещё нет"}), 400
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                                    # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/pick-folder")
def pick_folder():
    picker = STATE.get("picker")
    if not picker:
        return jsonify({"ok": False, "error": "Диалог доступен только в приложении"}), 400
    path = picker()
    return jsonify({"ok": bool(path), "path": path or ""})


@app.get("/api/zip")
def zip_result():
    """Скачать всё, что сгенерировано (нужно, когда приложение открыто в браузере)."""
    folder = STATE["last_folder"]
    if not folder or not os.path.isdir(folder):
        return jsonify({"ok": False, "error": "Сначала создай файлы"}), 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, names in os.walk(folder):
            for n in names:
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, folder))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="breloki_3d.zip")


def run(host="127.0.0.1", port=8770, debug=False):
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    print(f"{APP_NAME} {APP_VERSION} → http://{a.host}:{a.port}")
    run(a.host, a.port, a.debug)
