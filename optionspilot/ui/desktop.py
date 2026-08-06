"""Desktop shell: uvicorn, pywebview, and the system-tray lifecycle."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger, uvicorn_logging_kwargs
from optionspilot.host import current_host
from optionspilot.services.runtime import TaskSpec
from optionspilot.ui.tray import TrayMenuItem, TrayStatus, create_tray

log = get_logger("ui")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


#: How long the launcher will wait for the transport to bind before opening the
#: window anyway. The retired HTTP poll gave up after ~100 attempts for the same
#: reason: a window against a dead port is a bad outcome, but a process that
#: never draws one and never exits is a worse one.
SERVER_START_TIMEOUT = 10.0


def _await_transport(transport_server, timeout: float | None = None) -> bool:
    """Wait for uvicorn to report its listener is up. Returns whether it did.

    V0.9.1-C10. This used to be an HTTP poll — up to 100 `urlopen` calls
    against `/api/status`, 0.1s apart, asking over the network a question the
    object in this process can answer. `uvicorn.Server.started` is set the
    moment the listener is bound.

    **The cost is latency, not CPU, and that is worth stating precisely
    because the obvious guess is wrong.** Building the `/api/status` payload
    was measured at 0.02 ms on a fresh account, so a hundred probes are not a
    meaningful amount of work — the argument for deleting the poll is not that
    it was expensive. It is that the loop only *notices* readiness on a 0.1s
    sleep boundary, so every launch waited up to 100 ms after the server was
    already serving; a flag read notices within 0.02s. A liveness probe that
    builds a full application payload is also simply the wrong shape, and it
    would get more expensive with a real account, but that is a reason to
    prefer the flag rather than a measurement anyone has taken.

    Bounded, and it *reports* rather than raises: the old loop fell through
    silently after its attempts, so a failure to bind produced a window
    pointing at a dead port with nothing in the log to say why. It also
    swallowed every exception, including a genuine one.
    """
    deadline = time.monotonic() + (SERVER_START_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if getattr(transport_server, "started", False):
            return True
        time.sleep(0.02)
    log.warning("the HTTP transport did not report started within %.1fs; "
                "opening the window anyway", SERVER_START_TIMEOUT)
    return False


def _acquire_single_instance():
    """Claim the right to be the only running copy, via the host adapter.

    This used to be a second, independent implementation of the same socket
    mutex on the same hard-coded port as ``host.adapter.DesktopHost`` — one
    fact in two places, which is the drift this codebase has been bitten by
    repeatedly. The host owns "everything the app needs from the machine under
    it"; a single-instance mutex is exactly that, so the launcher asks rather
    than reimplements.
    """
    return current_host().acquire_single_instance()


def _relaunch_command() -> list[str]:
    """The command line that starts this build again.

    In a frozen PyInstaller build ``sys.executable`` *is* the application and
    ``sys.argv[0]`` repeats it, so relaunching with both hands the exe its own
    path as the first argument.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


_ALREADY_RUNNING_HTML = (
    "<body style='background:#0d0d0d;color:#e6e8eb;font-family:system-ui;"
    "display:grid;place-items:center;height:95vh'>"
    "<div><h2>OptionsPilot is already running</h2>"
    "<p>Close the other window first.</p></div></body>"
)


class DesktopApplication:
    """The desktop composition, separated from the GUI loop that runs it.

    V0.9.1-C11. This was 85 lines inside `launch()` under
    ``# pragma: no cover - GUI entry point`` — an honest label and also the
    problem. It is the one place the transport, the application server, the
    tray, the controller, the window, the notifier and the background runtime
    are joined, and none of it could be asserted: every binding was a local
    that died with the frame, so a test could only observe whichever side
    effects happened to escape into a double.

    That is precisely where this file's worst defects have lived. The tray was
    once handed **Uvicorn's transport object** instead of the application
    server, and `tray_started` was once set from "a thread was created" rather
    than "an icon exists" (V0.8.2), so the app hid itself into a tray that was
    not there. Both are composition mistakes; composition was the untested
    part.

    So the wiring is now **state on an object** rather than locals in a
    function, and every collaborator is **injected** — no `sys.modules`
    patching to test it. The imports stay lazy for the real ones: `webview`
    and `uvicorn` are heavy, and nothing but a desktop launch should pay for
    them.

    The split is deliberate about which half is testable:

    ``acquire`` / ``build`` / ``shutdown``
        Pure wiring. Assertable with doubles, and asserted.
    ``run``
        The GUI loop. Blocks until the user closes the window; there is
        nothing to unit-test in `webview.start()` itself.
    """

    def __init__(self, config: AppConfig, runtime=None, data_dir=None, *,
                 webview=None, uvicorn=None, create_app=None,
                 create_tray=None, acquire_single_instance=None,
                 free_port=None):
        self.config = config
        self.runtime = runtime
        self.data_dir = data_dir
        self._webview = webview
        self._uvicorn = uvicorn
        self._create_app = create_app
        self._create_tray = create_tray
        self._acquire_instance = acquire_single_instance or _acquire_single_instance
        self._free_port = free_port or _free_port
        #: Composition, populated by `acquire()` and `build()`. Public on
        #: purpose: being able to look at what was wired is the point.
        self.instance_lock = None
        self.port: int | None = None
        self.url: str | None = None
        self.transport = None
        self.server = None
        self.tray = None
        self.controller: _DesktopController | None = None
        self.window = None
        self.transport_ready = False

    # ── lazily-resolved real collaborators ───────────────────────────────

    @property
    def webview(self):
        if self._webview is None:
            import webview
            self._webview = webview
        return self._webview

    @property
    def uvicorn(self):
        if self._uvicorn is None:
            import uvicorn
            self._uvicorn = uvicorn
        return self._uvicorn

    @property
    def tray_factory(self):
        # Resolved on every read rather than captured at construction: a
        # collaborator meant to be replaceable must be reached through the
        # attribute, or a later reassignment is silently ignored — the seam
        # `ServiceRegistry` already had to learn not to freeze.
        return self._create_tray if self._create_tray is not None else create_tray

    @property
    def create_app(self):
        if self._create_app is None:
            # Imported at call time, not module import time: the UI server
            # pulls in FastAPI, and a test that replaces `create_app` on the
            # module must be able to.
            from optionspilot.ui import server as ui_server
            return ui_server.create_app
        return self._create_app

    # ── composition ──────────────────────────────────────────────────────

    def acquire(self) -> bool:
        """Claim the right to be the only running copy."""
        self.instance_lock = self._acquire_instance()
        if self.instance_lock is None:
            log.warning("another OptionsPilot instance is already running")
            return False
        return True

    def show_already_running(self) -> None:
        self.webview.create_window("OptionsPilot",
                                   html=_ALREADY_RUNNING_HTML,
                                   width=460, height=220)
        self.webview.start()

    def build(self) -> None:
        """Wire everything except the GUI loop."""
        self.port = self._free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        app = self.create_app(self.config, run_loop=True, runtime=self.runtime,
                              data_dir=self.data_dir)
        # Transport serving stays separate from application lifecycle
        # ownership. The controller drives runtime/tray operations on
        # UIServer; the Uvicorn Server only owns HTTP process shutdown.
        # `uvicorn_logging_kwargs()` is not optional here: without it, merely
        # constructing this Config kills a windowed build, because uvicorn's
        # default formatter reads `sys.stdout.isatty()` and there is no stdout.
        self.transport = self.uvicorn.Server(self.uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning",
            **uvicorn_logging_kwargs()))
        threading.Thread(target=self.transport.run, daemon=True,
                         name="uvicorn").start()
        self.server = app.state.server
        self.server.updater.set_install_hook(self._on_install_launched)

        self.transport_ready = _await_transport(self.transport)

        self.tray = self.tray_factory(
            Path(__file__).parent / "static" / "favicon.ico")
        self.controller = _DesktopController(
            None, self.server, self.tray,
            release_instance_lock=self.instance_lock.close)
        self.server.orch.notifier.set_action_handler(
            self.controller.handle_notification_action)
        self.window = self.webview.create_window(
            "OptionsPilot — Paper Trading", self.url,
            width=1280, height=860, min_size=(980, 640),
            background_color="#0d0d0d",
            js_api=_DesktopBridge(self.controller),
        )
        self.controller.window = self.window
        self.window.events.closing += self.controller.on_closing
        self.tray.set_menu(self.controller.menu())
        self.tray.set_status(TrayStatus("healthy", "OptionsPilot — Healthy"))
        self.controller.tray_started = self.tray.start()
        if self.controller.tray_started:
            try:
                self.server.background.register(
                    TaskSpec("tray_status", 10, self.controller.refresh_tray,
                             policy="essential"))
            except ValueError:
                pass

    def _on_install_launched(self) -> None:
        """The installer is running and waiting for this exe to be released.

        Goes through the lifecycle owner rather than destroying windows
        directly: a bare `window.destroy()` is a REQUEST to close, and
        `_DesktopController.on_closing` refuses every request it did not
        sanction. Bypassing it turned the update into a hide-to-tray.
        """
        self.transport.should_exit = True
        try:
            self.controller.shutdown_for_install()
        except Exception:  # noqa: BLE001 - best-effort shutdown
            log.exception("could not shut down for the installer")

    def run(self) -> None:
        # `create_app` owns the effective settings object when the launcher was
        # not passed one explicitly. Read that object so startup preferences
        # work for both embedded and normal desktop launches.
        prefs = self.server.runtime.runtime_prefs()

        def on_ready():
            if prefs.get("start_minimized_to_tray") and \
                    self.controller.tray_started:
                self.controller.hide_to_tray()

        self.webview.start(on_ready)

    def shutdown(self) -> None:
        # The GUI loop can return while a deferred close worker is still
        # stopping the scheduler. Let it finish rather than racing the
        # interpreter's own teardown against a half-closed database.
        self.controller.join_pending()
        self.controller.exit()
        self.transport.should_exit = True
        self.instance_lock.close()

    def launch(self) -> None:
        if not self.acquire():
            self.show_already_running()
            return
        self.build()
        try:
            self.run()
        finally:
            self.shutdown()


def launch(config: AppConfig, runtime=None, data_dir=None) -> None:
    """Desktop entry point. A thin adapter over `DesktopApplication`."""
    DesktopApplication(config, runtime, data_dir).launch()


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
    """Desktop lifecycle owner.

    THREAD OWNERSHIP — the single most important fact about this class.

    ``on_closing`` is bound to pywebview's ``closing`` event, which is
    constructed as ``Event(window, should_lock=True)``.  ``should_lock`` means
    handlers run **synchronously on the GUI thread** — the WinForms message
    pump, inside ``Form.FormClosing``.  Three of the operations this controller
    performs are therefore forbidden from that handler:

    * ``window.evaluate_js`` posts a script to WebView2 and blocks on a
      semaphore released by a continuation scheduled *on the message pump*.
      Called from the pump, it can never be released: the process wedges with a
      white title bar, "Not Responding", and no Python traceback.
    * ``window.destroy`` / ``window.hide`` marshal through ``Control.Invoke``,
      which needs the same pump.
    * ``server.close()`` and ``tray.stop()`` join worker threads for up to five
      and two seconds respectively.  Windows marks a window unresponsive after
      five, so even without a deadlock the close would be visibly broken.

    So ``on_closing`` *decides* and returns immediately; every consequence runs
    on a worker via ``_defer``.  Callbacks arriving from the tray thread, the
    pywebview JS-bridge thread, the toast-activation thread and the background
    runtime are all already off the pump and may call these methods directly.
    """

    def __init__(self, window, server, tray, release_instance_lock=None):
        self.window = window
        self.server = server
        self.tray = tray
        self.tray_started = False
        self.allow_close = False
        self._exited = False
        #: Serialises the claim in `exit()` ONLY — never the shutdown itself.
        #: See `exit`'s docstring for why the body must run outside it.
        self._exit_lock = threading.Lock()
        self._prompt_shown = False
        self._release_instance_lock = release_instance_lock
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()

    # ── GUI-thread hand-off ──────────────────────────────────────────────────

    def _defer(self, name: str, fn, *args) -> threading.Thread:
        """Run pump-hostile work off the GUI thread. See the class docstring."""
        worker = threading.Thread(target=fn, args=args, name=name, daemon=True)
        with self._workers_lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            self._workers.append(worker)
        worker.start()
        return worker

    def join_pending(self, timeout: float = 10.0) -> None:
        """Wait for deferred lifecycle work. Used by the launcher's teardown."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._workers_lock:
            workers = list(self._workers)
        for worker in workers:
            if worker is threading.current_thread():
                continue
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def menu(self) -> list[TrayMenuItem]:
        def open_tab(tab):
            return lambda: self.open(tab)

        items = [
            TrayMenuItem("open", "Open OptionsPilot", self.open, default=True),
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
        """Decide what the close button means. Runs ON the GUI thread.

        Nothing here may block: reading preferences is a dict copy under a
        lock, and every consequence is handed to a worker. The handler always
        cancels the platform close (``False``) except when a shutdown already
        in flight is asking the window to go — the worker calls
        ``window.destroy()`` once ``allow_close`` is set, and that re-enters
        here, where returning ``True`` lets the form close for real.
        """
        if self.allow_close:
            return True
        try:
            prefs = self.server.runtime.runtime_prefs()
        except Exception:  # noqa: BLE001 - an unreadable pref must not wedge
            log.exception("close preferences unreadable — exiting")
            prefs = {"close_behavior": "exit", "close_prompt_dismissed": True}
        if prefs.get("close_behavior", "tray") == "exit" or not self.tray_started:
            self._defer("desktop-exit", self._exit_from_close_button)
            return False
        if not prefs.get("close_prompt_dismissed") and not self._prompt_shown:
            self._prompt_shown = True
            self._defer("desktop-close-prompt", self._request_close_choice)
            return False
        self._defer("desktop-hide", self.hide_to_tray)
        return False

    def _request_close_choice(self):
        """Ask the page to show the close dialog. Never on the GUI thread."""
        try:
            self.window.evaluate_js(
                "window.dispatchEvent(new Event('optionspilot-close-request'))")
        except Exception:  # noqa: BLE001 - a page that cannot be asked
            log.debug("close prompt could not be raised", exc_info=True)
            self._prompt_shown = False
            self.hide_to_tray()

    def shutdown_for_install(self):
        """Close for an installer that is waiting to replace this exe.

        `on_closing` cancels every platform close except one a shutdown already
        in flight is asking for, and it recognises that by `allow_close`. An
        installer holding a lock on the running binary IS such a shutdown, so
        the flag is set here before the close is requested.

        Without it the updater's `window.destroy()` reached `on_closing` with
        `allow_close` still False, which cancelled the close and deferred
        "hide to tray" instead — the default preferences send every ordinary
        install down exactly that branch. The window went to the tray, the
        process kept the exe locked, the silent installer could never replace
        it, and the dialog sat on "Installing..." forever.

        `exit()` sets `allow_close` itself, which is why this routes through it
        rather than setting the flag and destroying the window here: the flag
        stays owned by the one method that also stops the tray, closes the
        server and releases the port. A second owner would be a second thing to
        keep in step.

        Deferred for the same reason `_exit_from_close_button` is: this runs on
        the HTTP worker thread serving `POST /api/update/apply`, and `exit()`
        joins the tray and the scheduler for several seconds — inline it would
        hold that response open across the shutdown of the server writing it.
        """
        self._defer("desktop-install-exit", self.exit)

    def _exit_from_close_button(self):
        """Acknowledge the click before the (slow) shutdown starts.

        Hiding first is what the user reads as "it closed"; ``exit`` then takes
        as long as stopping the scheduler and the databases honestly takes,
        with no window left on screen to look frozen.
        """
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001 - best effort
            pass
        self.exit()

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
            # Decision D-2: pause never interrupts a running worker task, so
            # between the click and the scan actually stopping "Paused" is a
            # claim about the request rather than the system. Say which.
            state = ("pausing" if snap["background"].get("pause_pending")
                     else "paused")
        self.tray.set_status(TrayStatus(state, f"OptionsPilot — {state.title()}"))
        self.tray.set_menu(self.menu())

    def restart(self):
        self.exit(restart_command=_relaunch_command())

    def exit(self, restart_command=None):
        """Shut the application down. Runs its body exactly once, ever.

        V0.9.1-C7. The guard used to be ``if self._exited: return`` followed by
        ``self._exited = True``, with nothing between them — and `exit()` is
        reachable from five call sites on at least four threads (tray Exit,
        tray Restart, the JS bridge, the `desktop-exit` worker, and `launch()`'s
        `finally`). Measured with eight concurrent callers, **all eight ran the
        shutdown and Restart spawned eight successor processes** — and the
        successors were not rejected, because releasing the single-instance
        lock is the first thing a restart does. Checking a slot and then
        claiming it is not claiming it; `MarketDataControl` shipped the same
        shape and admitted 8 of 8 against one slot.

        **The lock covers the claim, not the work.** `tray.stop()` and
        `server.close()` join workers for up to seven seconds between them, and
        this method is called from the tray thread and the pywebview JS-bridge
        thread — holding a lock across that would freeze the tray menu behind a
        shutdown it did not start. A caller that does not win returns
        immediately, which is what the old guard did for a *sequential* second
        call and is safe for a concurrent one too: nothing in the body runs
        after ``window.destroy()``, so there is no later step a loser could
        race ahead of.
        """
        with self._exit_lock:
            if self._exited:
                return
            self._exited = True
        self.allow_close = True
        self.tray.stop()
        self.server.close()
        if restart_command:
            # Release the single-instance port BEFORE the successor tries to
            # bind it. Spawning first made the new process lose the race against
            # its own parent and greet the user with "already running" — the
            # launcher only closed the lock after the GUI loop had returned.
            self._release_lock()
            try:
                subprocess.Popen(restart_command, close_fds=True)
            except OSError:
                log.exception("could not restart OptionsPilot")
        try:
            self.window.destroy()
        except Exception:
            pass

    def _release_lock(self) -> None:
        if self._release_instance_lock is None:
            return
        try:
            self._release_instance_lock()
        except Exception:  # noqa: BLE001 - releasing twice is not an error
            log.debug("single-instance lock already released", exc_info=True)
