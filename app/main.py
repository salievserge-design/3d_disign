# -*- coding: utf-8 -*-
"""Точка входа настольного приложения НОМЕРОК 3D.

Поднимает локальный сервер на свободном порту и открывает его в окне:
 • если есть pywebview — в обычном окне приложения (на Windows это встроенный
   в систему движок Edge WebView2, доустанавливать ничего не нужно);
 • если нет — в браузере по умолчанию.

Ключ --selftest прогоняет всю цепочку без окна: этим проверяется собранный exe
на сборочной машине, чтобы пользователю не досталась молчащая программа.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server                                            # noqa: E402

TITLE = f"{server.APP_NAME} {server.APP_VERSION} — конструктор брелоков-автономеров"
LOG = os.path.join(tempfile.gettempdir(), "nomerok3d.log")
QUIET = "--selftest" in sys.argv     # на сборочной машине диалоги показывать некому


def log(text: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S ") + text + "\n")
    except OSError:
        pass
    try:
        print(text)
    except Exception:            # в оконном режиме stdout может отсутствовать
        pass


def message_box(text: str, title: str = server.APP_NAME) -> None:
    """Сообщение пользователю там, где консоли нет.
    В режиме самопроверки молчим: модальное окно на CI повесило бы сборку."""
    log(text)
    if QUIET:
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_up(port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.12)
    return False


def selftest(stage: int = 0) -> int:
    """Проверка собранного exe по стадиям: так по номеру шага на CI видно,
    что именно сломалось, даже не читая логов."""
    if stage in (0, 1):
        import numpy, shapely, trimesh, flask, fontTools          # noqa: F401
        from app import core                                      # noqa: F401
        import generate_keychain as g
        log(f"[1] модули ок: numpy {numpy.__version__}, shapely {shapely.__version__}, "
            f"trimesh {trimesh.__version__}, данные в {g.HERE}")
        assert os.path.exists(os.path.join(g.HERE, "fonts", "RoadNumbers2.0.ttf")), "нет шрифта"

    if stage in (0, 2):
        from app import core
        spec = core.Spec().clean()
        d = core.design(spec)
        svg, st = core.preview(d), core.stats(spec, d)
        assert len(svg["front"]) > 2000 and len(svg["back"]) > 500, "пустое превью"
        assert st["pause_layer"] == 13, f"неожиданный слой паузы: {st['pause_layer']}"
        log(f"[2] геометрия ок: {st['size_x']}×{st['size_y']} мм, {st['grams']} г")

    if stage in (0, 3):
        from app import core
        out = os.path.join(tempfile.mkdtemp(prefix="nomerok_"), "проверка")
        specs = [core.Spec().clean(),
                 core.Spec(number="А123ВС", region="36", back_text="",
                           mount="hole", hole_d=4.0).clean()]
        res = core.generate(specs, out, want_stl=True)
        assert res["combined"], "общий стол не собрался"
        assert len(res["files"]) >= 16, f"мало файлов: {len(res['files'])}"
        for f in res["files"]:
            assert os.path.getsize(f) > 0, f"пустой файл: {f}"
        log(f"[3] файлы ок: {len(res['files'])} шт, {res['grams']} г")

    if stage in (0, 4):
        try:
            import webview
            log(f"[4] pywebview {getattr(webview, '__version__', '?')} на месте")
        except Exception as e:
            log(f"[4] pywebview недоступен ({type(e).__name__}: {e}) — "
                f"приложение откроется в браузере")

    log(f"SELFTEST OK (стадия {stage or 'все'})")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        stage = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 0
        code = selftest(stage)
        # выходим жёстко: в собранном exe фоновые потоки (TBB у manifold3d и т.п.)
        # могут держать процесс живым, а на CI это выглядит как зависание
        sys.stderr.flush() if sys.stderr else None
        os._exit(code)

    port = free_port()
    threading.Thread(target=server.run, kwargs=dict(host="127.0.0.1", port=port),
                     daemon=True).start()
    if not wait_until_up(port):
        message_box("Не удалось запустить внутренний сервер приложения.\n"
                    f"Подробности: {LOG}")
        return 1
    url = f"http://127.0.0.1:{port}/"
    log(f"сервер поднят: {url}")

    try:
        import webview
    except Exception as e:                # не только ImportError: pythonnet, WebView2…
        log(f"pywebview недоступен: {e}")
        webview = None

    if webview is None:
        import webbrowser
        webbrowser.open(url)
        message_box(f"НОМЕРОК 3D открыт в браузере:\n{url}\n\n"
                    "Это окно закрывать нельзя — пока оно открыто, работает "
                    "программа. Нажми ОК, когда закончишь.")
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
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        message_box("НОМЕРОК 3D не смог запуститься.\n\n"
                    f"Технические подробности записаны в файл:\n{LOG}\n\n"
                    "Пришли этот файл — починим.")
        os._exit(1)
