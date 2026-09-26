# -*- coding: utf-8 -*-
"""Точка входа настольного приложения НОМЕРОК 3D.

Поднимает локальный сервер на свободном порту и открывает его в окне:
 • если есть pywebview — в нормальном окне приложения (на Windows это встроенный
   в систему движок Edge WebView2, ничего доустанавливать не нужно);
 • если нет — просто в браузере по умолчанию.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server                                            # noqa: E402

TITLE = f"{server.APP_NAME} {server.APP_VERSION} — конструктор брелоков-автономеров"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_up(port: int, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.12)
    return False


def main() -> int:
    port = free_port()
    threading.Thread(target=server.run, kwargs=dict(host="127.0.0.1", port=port),
                     daemon=True).start()
    if not wait_until_up(port):
        print("Не удалось запустить внутренний сервер", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/"

    try:
        import webview                                            # pywebview
    except ImportError:
        webview = None

    if webview is None:
        import webbrowser
        print(f"{TITLE}\nОткрой в браузере: {url}\nЗакрыть приложение — Ctrl+C в этом окне.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    window = webview.create_window(TITLE, url, width=1480, height=940,
                                   min_size=(1060, 720), background_color="#0c0e13")

    def pick_folder() -> str:
        try:
            res = window.create_file_dialog(webview.FOLDER_DIALOG)
            return res[0] if res else ""
        except Exception:
            return ""

    server.STATE["picker"] = pick_folder          # включает кнопку «Выбрать папку…»
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
