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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(config: AppConfig, runtime=None) -> None:  # pragma: no cover - GUI entry point
    import uvicorn
    import webview

    from optionspilot.ui.server import create_app

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
