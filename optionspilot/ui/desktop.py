"""Desktop shell: uvicorn, pywebview, and the system-tray lifecycle."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.services.runtime import TaskSpec
from optionspilot.ui.tray import TrayMenuItem, TrayStatus, create_tray

log = get_logger("ui")

SINGLE_INSTANCE_PORT = 8786


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _acquire_single_instance() -> socket.socket | None:
    """Hold a localhost port as a crash-safe process mutex."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        return lock
    except OSError:
        lock.close()
        return None


def launch(config: AppConfig, runtime=None, data_dir=None) -> None:  # pragma: no cover - GUI entry point
    import uvicorn
    import webview

    from optionspilot.ui.server import create_app

    instance_lock = _acquire_single_instance()
    if instance_lock is None:
        log.warning("another OptionsPilot instance is already running")
        webview.create_window(
            "OptionsPilot", html="<body style='background:#0d0d0d;color:#e6e8eb;"
            "font-family:system-ui;display:grid;place-items:center;height:95vh'>"
            "<div><h2>OptionsPilot is already running</h2>"
            "<p>Close the other window first.</p></div></body>",
            width=460, height=220,
        )
        webview.start()
        return

    port = _free_port()
    app = create_app(config, run_loop=True, runtime=runtime, data_dir=data_dir)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"
    ))
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()

    def _on_install_launched() -> None:
        server.should_exit = True
        try:
            for w in list(webview.windows):
                w.destroy()
        except Exception:  # noqa: BLE001 - best-effort shutdown
            pass

    app.state.server.updater.set_install_hook(_on_install_launched)

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    tray = create_tray(Path(__file__).parent / "static" / "favicon.ico")
    controller = _DesktopController(None, server, tray)
    app.state.server.orch.notifier.set_action_handler(
        controller.handle_notification_action)
    window = webview.create_window(
        "OptionsPilot — Paper Trading", url,
        width=1280, height=860, min_size=(980, 640),
        background_color="#0d0d0d", js_api=_DesktopBridge(controller),
    )
    controller.window = window
    window.events.closing += controller.on_closing
    tray.set_menu(controller.menu())
    tray.set_status(TrayStatus("healthy", "OptionsPilot — Healthy"))
    controller.tray_started = tray.start()
    if controller.tray_started:
        try:
            app.state.server.background.register(
                TaskSpec("tray_status", 10, controller.refresh_tray,
                         policy="essential"))
        except ValueError:
            pass

    # ``create_app`` owns the effective settings object when the launcher was
    # not passed one explicitly. Read that object so startup preferences work
    # for both embedded and normal desktop launches.
    prefs = app.state.server.runtime.runtime_prefs()

    def on_ready():
        if prefs.get("start_minimized_to_tray") and controller.tray_started:
            controller.hide_to_tray()

    try:
        webview.start(on_ready)
    finally:
        controller.exit()
        server.should_exit = True
        instance_lock.close()


class _DesktopBridge:
    """The only JS bridge: controls the close confirmation dialog."""

    def __init__(self, controller):
        self._controller = controller

    def hide_to_tray(self, dont_show_again=False):
        self._controller.hide_to_tray(dismiss_prompt=bool(dont_show_again))
        return True

    def exit(self):
        self._controller.exit()
        return True


class _DesktopController:
    def __init__(self, window, server, tray):
        self.window = window
        self.server = server
        self.tray = tray
        self.tray_started = False
        self.allow_close = False
        self._exited = False
        self._prompt_shown = False

    def menu(self) -> list[TrayMenuItem]:
        def open_tab(tab):
            return lambda: self.open(tab)

        items = [
            TrayMenuItem("open", "Open OptionsPilot", self.open),
            TrayMenuItem("dashboard", "Dashboard", open_tab("dashboard")),
            TrayMenuItem("charts", "Charts", open_tab("charts")),
            TrayMenuItem("coach", "AI Coach", open_tab("coach")),
            TrayMenuItem("workspace", "Workspace", open_tab("settings")),
            TrayMenuItem("updates", "Check for Updates", self.check_updates),
        ]
        if self.server.background.snapshot().paused:
            items.append(TrayMenuItem("resume", "Resume Background Tasks",
                                      self.resume))
        else:
            items.append(TrayMenuItem("pause", "Pause Background Tasks",
                                      self.pause))
        items.extend([
            TrayMenuItem("restart", "Restart", self.restart),
            TrayMenuItem("exit", "Exit", self.exit),
        ])
        return items

    def on_closing(self):
        if self.allow_close:
            return True
        prefs = self.server.runtime.runtime_prefs()
        if prefs["close_behavior"] == "exit" or not self.tray_started:
            self.exit()
            return False
        if not prefs["close_prompt_dismissed"] and not self._prompt_shown:
            self._prompt_shown = True
            try:
                self.window.evaluate_js(
                    "window.dispatchEvent(new Event('optionspilot-close-request'))")
            except Exception:
                self.hide_to_tray()
            return False
        self.hide_to_tray()
        return False

    def hide_to_tray(self, dismiss_prompt: bool = False):
        if dismiss_prompt:
            self.server.runtime.set_runtime_prefs(close_prompt_dismissed=True)
        self.server.set_background_visibility(False)
        try:
            self.window.evaluate_js(
                "window.dispatchEvent(new CustomEvent('optionspilot-visibility',"
                "{detail:{visible:false}}))")
            self.window.hide()
        except Exception:
            pass
        self._prompt_shown = False

    def open(self, tab: str = "dashboard"):
        self.server.set_background_visibility(True)
        try:
            self.window.show()
            self.window.evaluate_js(
                "window.dispatchEvent(new CustomEvent('optionspilot-visibility',"
                "{detail:{visible:true}}))")
            if tab != "dashboard":
                self.window.evaluate_js(f"switchTab({tab!r})")
        except Exception:
            pass

    def pause(self):
        self.server.pause_background()
        self.tray.set_menu(self.menu())

    def resume(self):
        self.server.resume_background()
        self.tray.set_menu(self.menu())

    def check_updates(self):
        self.server.updater.check_async()

    def handle_notification_action(self, event, action_id=None, event_id=None):
        """Route a native toast action through the application service."""
        action = getattr(event, "action", None) or {}
        destination = action.get("destination", "dashboard")
        self.open(destination)
        if event_id:
            self.server.services.notifications.dismiss(event_id)

    def refresh_tray(self):
        snap = self.server.runtime_payload()
        state = snap["health"].get("state", "healthy")
        if snap["background"].get("paused"):
            state = "paused"
        self.tray.set_status(TrayStatus(state, f"OptionsPilot — {state.title()}"))
        self.tray.set_menu(self.menu())

    def restart(self):
        self.exit(restart_command=[sys.executable, *sys.argv])

    def exit(self, restart_command=None):
        if self._exited:
            return
        self._exited = True
        self.allow_close = True
        self.tray.stop()
        self.server.close()
        if restart_command:
            try:
                subprocess.Popen(restart_command, close_fds=True)
            except OSError:
                log.exception("could not restart OptionsPilot")
        try:
            self.window.destroy()
        except Exception:
            pass
