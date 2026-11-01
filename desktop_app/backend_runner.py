"""Запускает существующий API; закрытие канала приложения завершает сервер."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import msvcrt
import os
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    project = Path(args.project).resolve(strict=True)
    sys.path.insert(0, str(project))
    os.chdir(project)
    stopping = threading.Event()
    server_holder: list = []
    input_fd = sys.stdin.fileno()
    input_handle = msvcrt.get_osfhandle(input_fd)
    peek_pipe = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek_pipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    peek_pipe.restype = wintypes.BOOL

    # Канал принадлежит родительскому приложению. EOF означает его закрытие
    # или аварию; никаких поисков и завершений чужих python.exe здесь нет.
    def watch_parent() -> None:
        try:
            # Не блокируем чтение CRT во время импорта NumPy: на Windows это
            # может удерживать внутренний lock загрузчика математических DLL.
            # Сначала проверяем наличие байтов нативным неблокирующим вызовом.
            pending = b""
            while True:
                available = wintypes.DWORD()
                if not peek_pipe(input_handle, None, 0, None, ctypes.byref(available), None):
                    break
                if available.value:
                    pending += os.read(input_fd, min(available.value, 4096))
                    if b"stop\n" in pending:
                        break
                    pending = pending[-4096:]
                time.sleep(0.15)
        except (OSError, ValueError):
            pass
        finally:
            stopping.set()
            if server_holder:
                server_holder[0].should_exit = True
            # Даже зависший импорт или незавершённый запрос не оставят сироту.
            time.sleep(15)
            os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    print("[desktop] Загружаем зависимости и собственную нейросеть…", flush=True)
    if os.environ.get("VATES_STARTUP_TRACE") == "1":
        import faulthandler
        faulthandler.dump_traceback_later(20)
    import uvicorn
    from ai_core.api_server import app
    if os.environ.get("VATES_STARTUP_TRACE") == "1":
        faulthandler.cancel_dump_traceback_later()

    @app.middleware("http")
    async def identify_owned_server(request, call_next):
        response = await call_next(request)
        # Это случайный идентификатор экземпляра, не пароль и не API-ключ.
        response.headers["X-Vates-Instance"] = args.instance
        return response

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=args.port, workers=1,
        access_log=False, timeout_graceful_shutdown=8,
    ))
    server_holder.append(server)
    if stopping.is_set():
        return
    server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
