"""Desktop shell: uvicorn in a background thread + a pywebview native window.

Closing the window stops the process (and with it the cycle loop) — the paper
account, journal, and open-trade context are all persisted, so next launch
resumes exactly where this one stopped.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request

from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger

log = get_logger("ui")


SINGLE_INSTANCE_PORT = 8786   # held open as a cross-process mutex


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _acquire_single_instance() -> socket.socket | None:
    """Two instances sharing one SQLite account file is corruption waiting to
    happen — hold a localhost port for the app's lifetime as a mutex."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        return lock
    except OSError:
        lock.close()
        return None


def launch(config: AppConfig, runtime=None) -> None:  # pragma: no cover - GUI entry point
    import uvicorn
    import webview

    from optionspilot.ui.server import create_app

    instance_lock = _acquire_single_instance()
    if instance_lock is None:
        log.warning("another OptionsPilot instance is already running — exiting")
        webview.create_window(
            "OptionsPilot", html="<body style='background:#0d0d0d;color:#e6e8eb;"
            "font-family:system-ui;display:grid;place-items:center;height:95vh'>"
            "<div><h2>OptionsPilot is already running</h2>"
            "<p>Close the other window first — two instances would fight over "
            "the same paper account.</p></div></body>",
            width=460, height=220,
        )
        webview.start()
        return

    port = _free_port()
    app = create_app(config, run_loop=True, runtime=runtime)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"
    ))
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):  # wait for the server to come up
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    log.info("desktop shell starting at %s", url)
    webview.create_window(
        "OptionsPilot — Paper Trading", url,
        width=1280, height=860, min_size=(980, 640),
        background_color="#0d0d0d",
    )
    webview.start()
    server.should_exit = True
    instance_lock.close()
