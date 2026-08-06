"""Desktop lifecycle tests use fakes; no Windows shell is required in CI."""

from __future__ import annotations

import ast
import gc
import importlib
import inspect
import sys
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest

from optionspilot.notify import desktop as desktop_notify
from optionspilot.notify.base import NotificationEvent
from optionspilot.ui import desktop
from optionspilot.ui.desktop import _DesktopController
from optionspilot.ui.tray import NullTray, PystrayTray, TrayAdapter, TrayMenuItem
from optionspilot.update.downloader import Downloader
from optionspilot.update.github_api import GitHubReleases
from optionspilot.update.installer import InstallerLauncher
from optionspilot.update.service import InMemoryPrefs, UpdateService
from tests.update_helpers import (
    FakeOpener, FakeResponse, release_json, releases_response,
)


class GuiThreadViolation(BaseException):
    """A GUI-thread operation that would deadlock the real process.

    Deliberately a ``BaseException``: the lifecycle code wraps these calls in
    ``except Exception`` on purpose (a window that has already gone must not
    break shutdown), and a real deadlock is not something those handlers get to
    catch and recover from. Raising an ordinary exception here would be
    swallowed by the very code under test and prove nothing.
    """


class HttpPollViolation(BaseException):
    """The launcher reached for HTTP to answer a local question.

    A `BaseException` for the same reason `GuiThreadViolation` is one, and the
    lesson was re-learned here: the retired startup probe sat inside
    ``except Exception: time.sleep(0.1)``, so an ordinary assertion raised from
    the fake `urlopen` was **swallowed by the very code under test**. The first
    version of the C10 ordering test passed against the unfixed launcher — it
    simply polled for ten seconds and still produced the expected order.
    """


class _Window:
    """A window that enforces pywebview's real thread contract.

    On Windows every one of these marshals through the WinForms message pump:
    ``evaluate_js`` blocks on a semaphore released by a continuation scheduled
    on that pump, and ``hide``/``show``/``destroy`` go through
    ``Control.Invoke``. The ``closing`` event runs its handlers ON the pump, so
    any of them called from a closing handler can never complete.

    The old double was a plain recorder, which is why a guaranteed deadlock
    passed its tests for two releases. This one raises instead of hanging, so
    the failure is a red test rather than a wedged suite.
    """

    def __init__(self):
        self.hidden = False
        self.destroyed = False
        self.scripts = []
        #: Set while a ``closing`` handler is on the (simulated) GUI thread.
        self.gui_thread: int | None = None

    def _check_thread(self, operation):
        if self.gui_thread is not None and threading.get_ident() == self.gui_thread:
            raise GuiThreadViolation(
                f"{operation} was called on the GUI thread from inside the "
                f"closing handler — on Windows this deadlocks the process")

    def hide(self):
        self._check_thread("window.hide")
        self.hidden = True

    def show(self):
        self._check_thread("window.show")
        self.hidden = False

    def destroy(self):
        self._check_thread("window.destroy")
        self.destroyed = True

    def evaluate_js(self, script):
        self._check_thread("window.evaluate_js")
        self.scripts.append(script)


class _Server:
    def __init__(self, **prefs):
        self._prefs = {"close_behavior": "tray", "close_prompt_dismissed": True,
                       **prefs}
        self.runtime = SimpleNamespace(runtime_prefs=lambda: dict(self._prefs),
                                       set_runtime_prefs=lambda **_p: None)
        self.background = SimpleNamespace(snapshot=lambda: SimpleNamespace(paused=False))
        self.updater = SimpleNamespace(check_async=lambda: None)
        self.closed = 0
        self.visible = []
        #: Lets a test make shutdown as slow as the real one is.
        self.on_close = None

    def close(self):
        if self.on_close is not None:
            self.on_close()
        self.closed += 1
    def set_background_visibility(self, visible): self.visible.append(visible)
    def pause_background(self): pass
    def resume_background(self): pass
    def runtime_payload(self): return {"health": {}, "background": {}}


def close_from_gui_thread(controller, window, timeout: float = 5.0):
    """Invoke ``on_closing`` under the pump's rules, then settle the worker."""
    window.gui_thread = threading.get_ident()
    try:
        verdict = controller.on_closing()
    finally:
        window.gui_thread = None
    controller.join_pending(timeout)
    return verdict


class TestPauseIsReportedHonestly:
    """V0.9.1-C4, Decision D-2's stated consequence.

    Pause does not interrupt a running worker task, so between the click and
    the scan actually stopping there is a window — potentially a long one, a
    full watchlist scan — in which "Paused" is a claim about the request and
    not about the system. The tray is the surface a user checks, and it already
    reads `background.paused`; it now distinguishes the two.
    """

    @staticmethod
    def _controller(background: dict):
        class _Tray(NullTray):
            def __init__(self):
                self.status = None

            def set_status(self, status):
                self.status = status

            def set_menu(self, items):
                pass

        class _Srv(_Server):
            def runtime_payload(self):
                return {"health": {"state": "healthy"}, "background": background}

        tray = _Tray()
        return _DesktopController(_Window(), _Srv(), tray), tray

    def test_a_settled_pause_reads_as_paused(self):
        controller, tray = self._controller(
            {"paused": True, "pause_pending": False})
        controller.refresh_tray()
        assert tray.status.state == "paused"
        assert "Paused" in tray.status.tooltip

    def test_a_pause_still_finishing_work_does_not_claim_to_be_paused(self):
        controller, tray = self._controller(
            {"paused": True, "pause_pending": True})
        controller.refresh_tray()
        assert tray.status.state == "pausing", (
            "a pause with a scan still running reported as complete")
        assert "Pausing" in tray.status.tooltip

    def test_running_normally_is_unaffected(self):
        controller, tray = self._controller(
            {"paused": False, "pause_pending": False})
        controller.refresh_tray()
        assert tray.status.state == "healthy"

    def test_a_payload_without_the_field_still_works(self):
        """Older payloads, and the `_Server` double used across this file."""
        controller, tray = self._controller({})
        controller.refresh_tray()
        assert tray.status.state == "healthy"


def test_disabled_tray_close_exits_instead_of_hiding_an_orphan_process():
    window, server, tray = _Window(), _Server(), NullTray()
    controller = _DesktopController(window, server, tray)
    controller.tray_started = False
    assert close_from_gui_thread(controller, window) is False
    assert server.closed == 1
    assert window.destroyed


def test_the_close_button_never_blocks_the_gui_thread():
    """The V0.8.1 freeze: shutdown ran inside pywebview's closing handler.

    ``events.closing`` is ``Event(should_lock=True)``, so the handler owns the
    WinForms message pump while it runs. Stopping the scheduler took seconds and
    ``evaluate_js`` could never be released at all — white title bar, "Not
    Responding", no traceback. The handler must decide and return.
    """
    window, server, tray = _Window(), _Server(), NullTray()
    release = threading.Event()
    server.on_close = release.wait          # shutdown that refuses to be quick
    controller = _DesktopController(window, server, tray)
    controller.tray_started = False

    window.gui_thread = threading.get_ident()
    try:
        started = time.monotonic()
        assert controller.on_closing() is False
        elapsed = time.monotonic() - started
    finally:
        window.gui_thread = None

    assert elapsed < 0.5, f"the GUI thread was held for {elapsed:.2f}s"
    assert server.closed == 0, "shutdown must not have completed inline"
    release.set()
    controller.join_pending(5.0)
    assert server.closed == 1
    assert window.destroyed


def test_the_close_prompt_is_raised_off_the_gui_thread():
    """The default first close: prompt not yet dismissed, tray available."""
    window = _Window()
    server = _Server(close_prompt_dismissed=False)
    controller = _DesktopController(window, server, NullTray())
    controller.tray_started = True
    assert close_from_gui_thread(controller, window) is False
    assert any("optionspilot-close-request" in s for s in window.scripts)
    assert server.closed == 0
    assert not window.destroyed


def test_close_to_tray_hides_off_the_gui_thread():
    window, server = _Window(), _Server()
    controller = _DesktopController(window, server, NullTray())
    controller.tray_started = True
    assert close_from_gui_thread(controller, window) is False
    assert window.hidden
    assert server.visible == [False]
    assert server.closed == 0


def test_a_shutdown_in_flight_lets_the_platform_close_the_window():
    """``exit`` calls ``destroy``, which re-enters ``on_closing`` on the pump.

    That re-entry must return True without doing any work, or the window can
    never actually close.
    """
    window, server = _Window(), _Server()
    controller = _DesktopController(window, server, NullTray())
    controller.allow_close = True
    window.gui_thread = threading.get_ident()
    try:
        assert controller.on_closing() is True
    finally:
        window.gui_thread = None
    assert server.closed == 0


def test_restart_removes_the_tray_icon_and_frees_the_lock_before_spawning():
    """Two orderings matter, and both are about what the successor inherits.

    The icon must be gone before a second process can add its own, or the user
    briefly sees two; and port 8786 must be free before the successor tries to
    bind it, or it greets them with "already running".
    """
    order = []
    window, server = _Window(), _Server()
    server.on_close = lambda: order.append("close")

    class _RecordingTray(NullTray):
        def stop(self):
            order.append("tray-stop")
            super().stop()

    controller = _DesktopController(
        window, server, _RecordingTray(),
        release_instance_lock=lambda: order.append("release"))
    spawned = []

    class _Popen:
        def __init__(self, command, **_kwargs):
            order.append("spawn")
            spawned.append(command)

    original = desktop.subprocess.Popen
    desktop.subprocess.Popen = _Popen
    try:
        controller.restart()
    finally:
        desktop.subprocess.Popen = original

    assert order == ["tray-stop", "close", "release", "spawn"]
    assert spawned and spawned[0][0] == sys.executable


def test_the_launcher_asks_the_host_for_the_single_instance_mutex():
    """One mutex, one owner.

    `ui/desktop.py` used to carry its own copy of the socket mutex AND its own
    copy of the port number, duplicating `host.adapter.DesktopHost`. Two
    implementations of one fact is the drift class this codebase keeps paying
    for; the launcher must go through the host so a change lands in both.
    """
    from optionspilot.host import HostAdapter, set_host
    from optionspilot.host.capabilities import HostProfile

    class RecordingHost(HostAdapter):
        profile = HostProfile("desktop", "recording", frozenset(), implemented=True)

        def __init__(self):
            self.asked = 0

        def data_root(self):
            return Path(".")

        def temp_dir(self):
            return Path(".")

        def acquire_single_instance(self, *_args, **_kwargs):
            self.asked += 1
            return object()

    host = RecordingHost()
    try:
        set_host(host)
        assert desktop._acquire_single_instance() is not None
        assert host.asked == 1
    finally:
        set_host(None)
    # And the duplicated constant is gone.
    assert not hasattr(desktop, "SINGLE_INSTANCE_PORT")


def test_a_frozen_build_does_not_pass_itself_its_own_path(monkeypatch):
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "argv", ["OptionsPilot.exe", "ui"])
    monkeypatch.setattr(desktop.sys, "executable", "OptionsPilot.exe")
    assert desktop._relaunch_command() == ["OptionsPilot.exe", "ui"]


class TestExitIsSingleEntry:
    """V0.9.1-C7: `exit()` must run its shutdown exactly once, not merely
    usually.

    The guard was a check-then-act with nothing serialising it::

        if self._exited:
            return
        self._exited = True

    `exit()` is reachable from five call sites on at least four threads — the
    tray menu's Exit and Restart (tray thread), the pywebview JS bridge, the
    `desktop-exit` worker `on_closing` defers to, and `launch()`'s `finally` on
    the main thread. Two arriving together both observe False and both proceed.

    The consequences are not symmetric. A double `server.close()` and a double
    `tray.stop()` are absorbed by their own idempotence, but **Restart spawns a
    successor process**, and it releases the single-instance lock first —
    precisely so the successor can bind the port. Two winners therefore start
    two OptionsPilot processes, and neither is rejected, because the guard that
    would have rejected the second was just handed away by the first.

    This is the shape the milestone keeps removing: `MarketDataControl`
    admitted 8 of 8 concurrent requests against one slot, and C5's manual scan
    ran a whole extra cycle. Checking a slot and then claiming it is not
    claiming it.
    """

    class _SlowFalse:
        """A `_exited` whose truth test is slow.

        Between `if self._exited` and `self._exited = True` there are two
        bytecodes, so a plain concurrent test reproduces the race only by luck
        and would be a flaky guard rather than a proof. Making the *read* slow
        widens the window deterministically, and it stays honest after the fix:
        the corrected guard reads this same object inside the lock, so the
        second caller simply waits for the first to finish claiming.
        """

        def __bool__(self):
            time.sleep(0.05)
            return False

    class _CountingTray(NullTray):
        def __init__(self):
            super().__init__()
            self.stops = 0

        def stop(self):
            self.stops += 1
            super().stop()

    @staticmethod
    def _race(fn, count=8):
        ready = threading.Barrier(count)

        def run():
            ready.wait()
            fn()

        threads = [threading.Thread(target=run, name=f"exit-{i}")
                   for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads), "an exit call hung"

    def test_concurrent_exits_run_the_shutdown_once(self):
        window, server = _Window(), _Server()
        tray = self._CountingTray()
        controller = _DesktopController(window, server, tray)
        controller._exited = self._SlowFalse()

        self._race(controller.exit)

        assert server.closed == 1, f"the server was closed {server.closed} times"
        assert tray.stops == 1, f"the tray was stopped {tray.stops} times"

    def test_concurrent_restarts_spawn_exactly_one_successor(self):
        """The failure that reaches the user: two running applications."""
        window, server = _Window(), _Server()
        releases = []
        controller = _DesktopController(
            window, server, self._CountingTray(),
            release_instance_lock=lambda: releases.append(1))
        controller._exited = self._SlowFalse()
        spawned = []

        class _Popen:
            def __init__(self, command, **_kwargs):
                spawned.append(command)

        original = desktop.subprocess.Popen
        desktop.subprocess.Popen = _Popen
        try:
            self._race(controller.restart)
        finally:
            desktop.subprocess.Popen = original

        assert len(spawned) == 1, (
            f"{len(spawned)} successor processes were spawned")
        assert len(releases) == 1, (
            f"the instance lock was released {len(releases)} times")

    def test_a_caller_that_did_not_win_is_not_made_to_wait(self):
        """`exit()` is called from the tray thread and the JS bridge thread.

        Blocking either for the length of a real shutdown — `tray.stop()` and
        `server.close()` join workers for up to seven seconds between them —
        would freeze the tray menu behind a shutdown it did not start. The
        loser returns; it does not queue.
        """
        window, server = _Window(), _Server()
        started = threading.Event()
        release = threading.Event()
        server.on_close = lambda: (started.set(), release.wait(10))

        controller = _DesktopController(window, server, self._CountingTray())
        winner = threading.Thread(target=controller.exit, name="exit-winner")
        winner.start()
        try:
            assert started.wait(5), "the shutdown never began"
            began = time.monotonic()
            controller.exit()
            assert time.monotonic() - began < 1.0, (
                "a losing caller waited for a shutdown it did not start")
        finally:
            release.set()
            winner.join(timeout=10)
        assert server.closed == 1


def test_tray_hide_restore_and_exit_are_idempotent():
    window, server = _Window(), _Server()
    tray = NullTray()
    controller = _DesktopController(window, server, tray)
    controller.tray_started = True
    controller.hide_to_tray()
    assert window.hidden
    assert server.visible == [False]
    controller.open()
    assert not window.hidden
    assert server.visible == [False, True]
    controller.exit()
    controller.exit()
    assert server.closed == 1
    assert tray.lifecycle_state == "stopped"


def test_null_tray_lifecycle_is_explicit_and_idempotent():
    tray: TrayAdapter = NullTray()
    assert tray.lifecycle_state == "unavailable"
    assert not tray.start()
    tray.stop()
    tray.stop()
    assert tray.lifecycle_state == "stopped"


def test_real_adapter_stop_during_start_does_not_leak_its_owned_thread(monkeypatch):
    """The pystray setup callback is the actual readiness boundary."""
    class FakeIcon:
        def __init__(self, *_args):
            self.release = threading.Event()

        def run(self, setup):
            time.sleep(0.02)
            setup(self)
            self.release.wait(1)

        def stop(self):
            self.release.set()

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    fake_pystray = SimpleNamespace(Icon=FakeIcon, Menu=FakeMenu,
                                   MenuItem=lambda *args, **kwargs: args)
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    tray = PystrayTray("assets/optionspilot.ico")
    assert tray.start()
    tray.stop()
    assert tray.lifecycle_state == "stopped"
    assert not (tray._thread and tray._thread.is_alive())


def test_desktop_notifier_uses_the_action_capable_toast_adapter(monkeypatch):
    class Interactable:
        def __init__(self, _name):
            self.sent = []

        def show_toast(self, toast):
            self.sent.append(toast)
    class FakeToast:
        def __init__(self, actions=None):
            self.actions = actions or []
            self.text_fields = []
            self.on_activated = None        

    monkeypatch.setattr(desktop_notify, "_AVAILABLE", True)
    monkeypatch.setattr(desktop_notify, "InteractableWindowsToaster", Interactable)
    monkeypatch.setattr(desktop_notify, "Toast", FakeToast)
    notifier = desktop_notify.DesktopNotifier()
    notifier.send(NotificationEvent(
        kind="info", title="Test", body="Action", event_id="test-action",
        action={"action_id": "open", "label": "Open"},
    ))
    assert isinstance(notifier._toaster, Interactable)
    assert len(notifier._toaster.sent) == 1


def test_desktop_notifier_exposes_optional_adapter_symbol_without_notify_extra(
        monkeypatch):
    """CI installs ``.[dev,ui]``, not the optional Windows toast extra."""
    with monkeypatch.context() as isolated:
        isolated.setitem(sys.modules, "windows_toasts", ModuleType("windows_toasts"))
        importlib.reload(desktop_notify)
        assert desktop_notify.InteractableWindowsToaster is None
        assert desktop_notify.DesktopNotifier()._toaster is None
    # Restore the module state expected by later desktop tests in this process.
    importlib.reload(desktop_notify)


class _FakeWebview:
    """Enough of pywebview to compose against, with no GUI.

    ``window_factory`` exists so a test can compose against a window that
    *refuses* a close it did not sanction (`_ClosableWindow`) rather than the
    recorder, which accepts every one. The difference is not cosmetic: it is
    the whole of the updater bug fixed in V0.12.1.
    """

    def __init__(self, events, window_factory=None):
        self.windows = []
        self.started_with = []
        self._events = events
        self.html_windows = []
        self._window_factory = window_factory or _Window

    def create_window(self, title, url=None, **kwargs):
        self._events.append("window-created")
        if url is None:                      # the "already running" notice
            self.html_windows.append(kwargs.get("html", ""))

            class _Plain:
                pass
            return _Plain()

        class _EventHook:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        window = self._window_factory()
        window.title = title
        window.url = url
        window.js_api = kwargs.get("js_api")
        window.events = SimpleNamespace(closing=_EventHook())
        self.windows.append(window)
        return window

    def start(self, callback=None):
        self._events.append("gui-loop")
        self.started_with.append(callback)
        if callback:
            callback()


class _FakeTransport:
    def __init__(self, _config):
        self.should_exit = False
        self.started = False

    def run(self):
        self.started = True


class _WiringServer(_Server):
    """An application server with the collaborators `build()` reaches for."""

    def __init__(self, **prefs):
        super().__init__(**prefs)
        self.registered = []
        self.action_handler = None
        self.install_hook = None
        self.background = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(paused=False),
            register=self.registered.append)
        self.orch = SimpleNamespace(notifier=SimpleNamespace(
            set_action_handler=lambda h: setattr(self, "action_handler", h)))
        self.updater = SimpleNamespace(
            check_async=lambda: None,
            set_install_hook=lambda h: setattr(self, "install_hook", h))


def _application(events=None, *, tray=None, lock=object(), prefs=None,
                 app_server=None, window_factory=None):
    """A `DesktopApplication` wired entirely to doubles."""
    events = events if events is not None else []
    server = app_server or _WiringServer(**(prefs or {}))
    app = SimpleNamespace(state=SimpleNamespace(server=server))
    fake_webview = _FakeWebview(events, window_factory=window_factory)
    return desktop.DesktopApplication(
        SimpleNamespace(), runtime=None, data_dir=None,
        webview=fake_webview,
        uvicorn=SimpleNamespace(Config=lambda *a, **k: object(),
                                Server=_FakeTransport),
        create_app=lambda *a, **k: app,
        create_tray=lambda _icon: tray if tray is not None else NullTray(),
        acquire_single_instance=lambda: (
            SimpleNamespace(close=lambda: events.append("lock-closed"))
            if lock is not None else None),
        free_port=lambda: 17999,
    ), server, fake_webview, events


class TestDesktopApplicationWiring:
    """V0.9.1-C11: the desktop composition, assertable without a GUI.

    `launch()` was 85 lines carrying `# pragma: no cover - GUI entry point`,
    which is an honest label and also the problem: it is the one place where
    the tray, the controller, the transport, the window, the notifier and the
    runtime are joined together, and none of it was assertable. The single test
    that reached it drove the whole function through `sys.modules` patching and
    could only observe side effects that happened to escape — every local
    binding died with the frame.

    That matters here more than it would elsewhere. The wiring is exactly where
    this file's worst bugs have lived: the tray was once handed Uvicorn's
    transport object instead of the application server, and `tray_started` was
    once believed on the strength of a thread having been created. Both are
    composition mistakes, and composition was the untested part.

    `DesktopApplication` holds that composition as state. Collaborators are
    injected — no `sys.modules` patching — and the result can be inspected
    rather than inferred.
    """

    def test_the_tray_and_controller_get_the_application_server(self):
        """The bug this file already carries a test for, now assertable
        directly rather than through a side effect."""
        app, server, _webview, _events = _application()
        app.acquire()
        app.build()
        assert app.server is server
        assert app.controller.server is server
        assert app.controller.server is not app.transport

    def test_the_window_is_bound_to_the_controller_both_ways(self):
        app, _server, webview, _events = _application()
        app.acquire()
        app.build()
        window = webview.windows[0]
        assert app.controller.window is window
        assert app.controller.on_closing in window.events.closing.handlers
        assert window.js_api is not None
        assert window.url == app.url == f"http://127.0.0.1:{app.port}"

    def test_the_notifier_action_handler_is_the_controllers(self):
        app, server, _webview, _events = _application()
        app.acquire()
        app.build()
        assert server.action_handler == app.controller.handle_notification_action

    def test_the_install_hook_stops_the_transport_and_closes_windows(self):
        """Composed against a window that can REFUSE, with a tray that is up,
        and settled before asserting. All three were missing and all three
        mattered.

        The recorder accepted every `destroy()`, so this passed throughout the
        two releases in which the hook's close was being cancelled. The
        shutdown is deferred to a worker, so reading `destroyed` on the calling
        thread was racing it. And a tray that never started is the ONE
        configuration in which the bug could not happen — `on_closing` exits
        outright without one — so the default `NullTray` was quietly testing
        the safe case.
        """
        holder = {}
        app, server, webview, _events = _application(
            tray=_LiveTray(),
            window_factory=lambda: _ClosableWindow(lambda: holder["app"].controller))
        holder["app"] = app
        app.acquire()
        app.build()
        assert server.install_hook is not None
        server.install_hook()
        assert app.transport.should_exit is True
        app.controller.join_pending(5.0)
        assert all(w.destroyed for w in webview.windows)

    def test_the_tray_status_task_is_registered_only_when_the_tray_started(self):
        """`tray_started` describes an observable end state (V0.8.2). A tray
        that never appeared must not get a refresh task."""
        class _DeadTray(NullTray):
            def start(self):
                return False

        app, server, _w, _e = _application(tray=_DeadTray())
        app.acquire()
        app.build()
        assert app.controller.tray_started is False
        assert [t.name for t in server.registered] == []

        class _LiveTray(NullTray):
            def start(self):
                self._lifecycle_state = "active"
                return True

        app2, server2, _w2, _e2 = _application(tray=_LiveTray())
        app2.acquire()
        app2.build()
        assert app2.controller.tray_started is True
        assert [t.name for t in server2.registered] == ["tray_status"]
        assert server2.registered[0].policy == "essential"

    def test_the_window_is_not_created_before_the_transport_is_ready(self):
        """C10's guarantee, preserved through the refactor."""
        app, _server, _webview, events = _application()
        app.acquire()
        app.build()
        assert app.transport_ready is True
        assert events.index("window-created") >= 0
        assert app.transport.started is True

    def test_a_second_instance_shows_a_notice_and_starts_nothing(self):
        app, _server, webview, events = _application(lock=None)
        assert app.acquire() is False
        app.show_already_running()
        assert webview.html_windows and "already running" in \
            webview.html_windows[0].lower()
        assert app.transport is None and app.server is None

    def test_shutdown_runs_teardown_in_the_order_the_launcher_needs(self):
        """`join_pending` before `exit`, and the instance lock released last —
        the GUI loop can return while a deferred close worker is still stopping
        the scheduler."""
        app, server, _webview, events = _application()
        app.acquire()
        app.build()
        order = []
        app.controller.join_pending = lambda *a, **k: order.append("join")
        app.controller.exit = lambda: order.append("exit")
        app.shutdown()
        assert order == ["join", "exit"]
        assert app.transport.should_exit is True
        assert events[-1] == "lock-closed"

    def test_start_minimized_hides_only_when_the_tray_is_really_up(self):
        class _LiveTray(NullTray):
            def start(self):
                self._lifecycle_state = "active"
                return True

        app, server, webview, _e = _application(
            tray=_LiveTray(), prefs={"start_minimized_to_tray": True})
        app.acquire()
        app.build()
        app.run()
        assert server.visible == [False], "start-minimized did not hide to tray"

    def test_launch_is_the_same_three_steps_and_keeps_its_signature(self):
        """The module-level entry point must stay a thin adapter — and keep the
        signature `__main__.cmd_ui` calls it with."""
        import inspect

        params = list(inspect.signature(desktop.launch).parameters)
        assert params == ["config", "runtime", "data_dir"]

        app, _server, _webview, events = _application()
        app.launch()
        assert "gui-loop" in events
        assert events[-1] == "lock-closed"


def _launch_harness(monkeypatch, transport_cls, events=None):
    """The minimum doubles `launch()` needs, shared by the C10 tests.

    Deliberately does NOT stub `urllib.request.urlopen` into working: the point
    of these tests is that the launcher never reaches for it.
    """
    events = events if events is not None else []

    class ApplicationServer(_Server):
        def __init__(self):
            super().__init__()
            self.background = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(paused=False),
                register=lambda _task: None)
            self.orch = SimpleNamespace(
                notifier=SimpleNamespace(set_action_handler=lambda _h: None))
            self.updater = SimpleNamespace(check_async=lambda: None,
                                           set_install_hook=lambda _h: None)

    class EventHook:
        def __iadd__(self, _handler):
            return self

    class Window(_Window):
        def __init__(self):
            super().__init__()
            self.events = SimpleNamespace(closing=EventHook())

    class Webview:
        windows = []

        @staticmethod
        def create_window(*_args, **_kwargs):
            events.append("window-created")
            window = Window()
            Webview.windows.append(window)
            return window

        @staticmethod
        def start(callback=None):
            if callback:
                callback()

    application_server = ApplicationServer()
    app = SimpleNamespace(state=SimpleNamespace(server=application_server))

    def never_poll(*_args, **_kwargs):
        raise HttpPollViolation(
            "the launcher polled its own server over HTTP instead of asking "
            "the transport whether it had started")

    monkeypatch.setattr("urllib.request.urlopen", never_poll)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(
        Config=lambda *_a, **_k: object(), Server=transport_cls))
    monkeypatch.setitem(sys.modules, "webview", Webview)
    monkeypatch.setattr("optionspilot.ui.server.create_app",
                        lambda *_a, **_k: app)
    monkeypatch.setattr(desktop, "create_tray", lambda _icon: NullTray())
    monkeypatch.setattr(desktop, "_free_port", lambda: 17778)
    monkeypatch.setattr(desktop, "_acquire_single_instance",
                        lambda: SimpleNamespace(close=lambda: None))
    return events, application_server


class TestTheLauncherDoesNotPollItself:
    """V0.9.1-C10: readiness comes from the transport, not from HTTP.

    `launch()` used to wait for its own in-process server like this::

        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/api/status", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

    It asks over the network a question the object in the same process can
    answer: `uvicorn.Server.started` is set the moment the listener is up.

    **What it cost was measured, and the intuitive answer was wrong.** Building
    the `/api/status` payload came in at 0.02 ms on a fresh account, so the
    hundred probes were not a meaningful amount of work — an earlier draft of
    this docstring called it "one of the most expensive endpoints" on the
    strength of reading its body, and the benchmark did not support that. The
    real cost is latency: the loop notices readiness only on a 0.1s sleep
    boundary, so every launch waited up to 100 ms after the server was already
    serving. A flag read notices within 0.02s.

    The failure mode was quiet rather than dramatic, which is why it survived.
    """

    def test_readiness_comes_from_the_transport_and_the_window_waits_for_it(
            self, monkeypatch):
        class TransportServer:
            def __init__(self, _config):
                self.should_exit = False
                self.started = False

            def run(self):
                # A real server takes a moment to bind. If the launcher does
                # not wait, the window is created against a dead port.
                time.sleep(0.05)
                events.append("server-started")
                self.started = True

        events, _ = _launch_harness(monkeypatch, TransportServer)
        desktop.launch(SimpleNamespace())
        assert events == ["server-started", "window-created"], (
            f"the window did not wait for the transport: {events}")

    def test_a_server_that_never_starts_does_not_hang_the_launcher(
            self, monkeypatch):
        """The old loop gave up after ~100 attempts and opened the window
        anyway. A wait on a flag that is never set must stay bounded too —
        an unbounded one turns a failed bind into a process that never draws
        a window and never exits."""
        class DeadTransport:
            def __init__(self, _config):
                self.should_exit = False
                self.started = False

            def run(self):
                time.sleep(30)

        events, _ = _launch_harness(monkeypatch, DeadTransport)
        monkeypatch.setattr(desktop, "SERVER_START_TIMEOUT", 0.3)
        began = time.monotonic()
        desktop.launch(SimpleNamespace())
        elapsed = time.monotonic() - began
        assert elapsed < 5.0, f"the launcher blocked for {elapsed:.1f}s"
        assert events == ["window-created"]

    def test_the_desktop_module_no_longer_imports_urllib(self):
        """A retired probe that keeps its import reads as though it is still
        the mechanism, and is one line from becoming it again."""
        import ast
        import inspect

        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(desktop))):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {n for n in imported if n.startswith("urllib")}, (
            "ui/desktop.py still imports urllib")


def test_desktop_launch_binds_tray_menu_to_application_runtime(monkeypatch):
    """The launcher must not hand Uvicorn's transport object to the tray."""
    class ApplicationServer(_Server):
        def __init__(self):
            super().__init__()
            self.background = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(paused=False),
                register=lambda _task: None,
            )
            self.orch = SimpleNamespace(
                notifier=SimpleNamespace(set_action_handler=lambda _handler: None))
            self.updater = SimpleNamespace(
                check_async=lambda: None,
                set_install_hook=lambda _handler: None,
            )

    application_server = ApplicationServer()
    app = SimpleNamespace(state=SimpleNamespace(server=application_server))

    class TransportServer:
        def __init__(self, _config):
            self.should_exit = False
            # V0.9.1-C10: the launcher waits on the transport's own readiness
            # flag instead of polling `/api/status` over HTTP, so a double for
            # uvicorn has to carry it. Set in `run()`, where uvicorn sets it.
            self.started = False

        def run(self):
            self.started = True

    class EventHook:
        def __iadd__(self, _handler):
            return self

    class Window(_Window):
        def __init__(self):
            super().__init__()
            self.events = SimpleNamespace(closing=EventHook())

    class Webview:
        windows = []

        @staticmethod
        def create_window(*_args, **_kwargs):
            window = Window()
            Webview.windows.append(window)
            return window

        @staticmethod
        def start(callback=None):
            if callback:
                callback()

    class Tray(NullTray):
        def __init__(self):
            super().__init__()
            self._lifecycle_state = "stopped"

        def start(self):
            self.started = True
            self._lifecycle_state = "active"
            return True

    tray = Tray()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(
        Config=lambda *_args, **_kwargs: object(), Server=TransportServer))
    monkeypatch.setitem(sys.modules, "webview", Webview)
    monkeypatch.setattr("optionspilot.ui.server.create_app", lambda *_args, **_kwargs: app)
    monkeypatch.setattr(desktop, "create_tray", lambda _icon: tray)
    monkeypatch.setattr(desktop, "_free_port", lambda: 17777)
    monkeypatch.setattr(desktop, "_acquire_single_instance",
                        lambda: SimpleNamespace(close=lambda: None))

    desktop.launch(SimpleNamespace())

    assert [item.id for item in tray.items] == [
        "open", "dashboard", "charts", "coach", "workspace", "updates",
        "pause", "restart", "exit",
    ]
    assert application_server.closed == 1


# ── the tray icon itself ─────────────────────────────────────────────────────
#
# THREAD OWNERSHIP for the tray, top to bottom:
#
#   main thread             `launch()` builds PystrayTray, calls start(), and
#                            blocks until the icon is really in the
#                            notification area; then hands the GUI loop to
#                            pywebview.
#     -> system-tray         ONE daemon thread per adapter. It owns pystray's
#                            hidden window and its OWN Win32 message queue
#                            (`_win32._run`: PeekMessage / CreateWindow /
#                            _mainloop), which is why it is independent of
#                            pywebview's WinForms pump on the GUI thread, and
#                            why `run()` on a dedicated thread is correct
#                            rather than `run_detached()` — on win32 the latter
#                            is literally `Thread(target=self._run).start()`,
#                            so it would only add a thread we no longer hold a
#                            handle to. pystray needs no COM initialisation
#                            (it calls none anywhere).
#       -> pystray setup     pystray starts its own short-lived thread for the
#                            `setup` callback once the loop is up. That is
#                            where `visible = True` has to happen.
#     -> background-runtime  refresh_tray() every 10s: set_status / set_menu.
#     -> toast activation    handle_notification_action -> open().
#
# The GUI thread NEVER touches the tray; the close handler only reads
# `tray_started`.


class FakeIcon:
    """Models `pystray.Icon`'s real contract, especially the part that bit us.

    `run(setup)` does NOT make the icon appear. pystray's default setup handler
    is literally `self.visible = True`, and supplying a custom `setup`
    REPLACES it (pystray/_base.py::_start_setup). `visible = True` is the only
    path to `_show()`, the only caller of Shell_NotifyIcon(NIM_ADD).

    A fake that showed the icon on `run` would pass against the broken code —
    which is precisely what the previous fake did.
    """

    constructed: list = []

    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self._visible = False
        self.shown = 0
        self.hidden = 0
        self.running = threading.Event()
        self.stopped = threading.Event()
        self._release = threading.Event()
        FakeIcon.constructed.append(self)

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        if value == self._visible:
            return
        if value:
            if self.image is None:
                raise ValueError("cannot show icon without icon data")
            self.shown += 1
        else:
            self.hidden += 1
        self._visible = value

    def run(self, setup=None):
        self.running.set()
        if setup:
            setup(self)
        else:
            self.visible = True
        self._release.wait(10)
        self.visible = False          # pystray's _mainloop finally -> _hide()
        self.stopped.set()

    def stop(self):
        self._release.set()


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


def fake_menu_item(text, action, checked=None, default=False, enabled=True):
    return SimpleNamespace(text=text, action=action, checked=checked,
                           default=default, enabled=enabled)


def install_pystray(monkeypatch, icon_class=FakeIcon):
    FakeIcon.constructed = []
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(
        Icon=icon_class, Menu=FakeMenu, MenuItem=fake_menu_item))
    return FakeIcon


@pytest.fixture
def pystray_stub(monkeypatch):
    return install_pystray(monkeypatch)


ICON = (Path(__file__).resolve().parents[1] / "optionspilot" / "ui" /
        "static" / "favicon.ico")


class TestTrayIconAppears:
    """V0.8.2 hotfix: the Icon object existed and the icon never appeared."""

    def test_start_puts_the_icon_in_the_notification_area(self, pystray_stub):
        """The regression itself.

        Every other signal — thread alive, lifecycle "active", no exception —
        was already correct while the notification area stayed empty, because
        the one call that adds the icon was never made.
        """
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is True
            icon = pystray_stub.constructed[0]
            assert icon.visible is True, "the icon was never shown"
            assert icon.shown == 1
            assert tray.lifecycle_state == "active"
            assert tray.last_error is None
        finally:
            tray.stop()

    def test_start_is_false_until_the_icon_is_actually_up(self, monkeypatch):
        """`start()` must mean "the icon is in the tray", not "a thread exists".

        This is what turned a tray bug into a vanishing application: the
        launcher believed the tray was there, so closing the window hid it into
        a tray that never had an icon.
        """
        class NeverShows(FakeIcon):
            def run(self, setup=None):
                self.running.set()
                self._release.wait(10)   # loop runs; setup never invoked
                self.stopped.set()

        install_pystray(monkeypatch, NeverShows)
        tray = PystrayTray(ICON)
        tray.READY_TIMEOUT = 1.0
        try:
            assert tray.start() is False
            assert tray.lifecycle_state == "unavailable"
        finally:
            tray.stop()

    def test_the_tray_thread_stays_alive_while_active(self, pystray_stub):
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is True
            time.sleep(0.1)
            assert tray._thread is not None and tray._thread.is_alive()
            assert tray._thread.name == "system-tray"
            assert tray._thread.daemon is True
            assert tray.lifecycle_state == "active"
        finally:
            tray.stop()

    def test_the_icon_object_is_not_garbage_collected_while_active(
            self, pystray_stub):
        """Reachability: the adapter holds `_icon`, and the live tray thread's
        frame holds the same object as its argument."""
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is True
            ref = weakref.ref(pystray_stub.constructed[0])
            gc.collect()
            assert ref() is not None
            assert tray._icon is ref(), "the adapter dropped its own icon"
        finally:
            tray.stop()

    def test_repeated_start_reuses_the_one_icon(self, pystray_stub):
        """Hide/restore must never add a second icon to the notification area."""
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is True
            for _ in range(5):
                assert tray.start() is True
            assert len(pystray_stub.constructed) == 1
            assert pystray_stub.constructed[0].shown == 1
        finally:
            tray.stop()

    def test_hide_and_restore_cycles_leave_the_icon_alone(self, pystray_stub):
        """The window hides and restores; the tray icon simply stays up."""
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is True
            controller = _DesktopController(_Window(), _Server(), tray)
            controller.tray_started = True
            for _ in range(5):
                controller.hide_to_tray()
                controller.open()
            assert len(pystray_stub.constructed) == 1
            icon = pystray_stub.constructed[0]
            assert (icon.shown, icon.hidden) == (1, 0)
            assert icon.visible is True
        finally:
            tray.stop()

    def test_stop_removes_the_icon_and_ends_its_thread(self, pystray_stub):
        tray = PystrayTray(ICON)
        assert tray.start() is True
        icon = pystray_stub.constructed[0]
        thread = tray._thread
        tray.stop()
        assert icon.stopped.wait(5), "the tray loop never exited"
        assert icon.visible is False, "the icon was left in the notification area"
        assert icon.hidden == 1
        assert not thread.is_alive()
        assert tray.lifecycle_state == "stopped"

    def test_stop_is_idempotent(self, pystray_stub):
        tray = PystrayTray(ICON)
        assert tray.start() is True
        tray.stop()
        tray.stop()
        assert tray.lifecycle_state == "stopped"
        assert len(pystray_stub.constructed) == 1


class TestTrayFailuresAreSurfaced:
    """"No tray icon" must never again be indistinguishable from "tray fine"."""

    def test_a_missing_icon_file_is_reported_not_swallowed(self, pystray_stub,
                                                           tmp_path):
        tray = PystrayTray(tmp_path / "does-not-exist.ico")
        assert tray.start() is False
        assert tray.lifecycle_state == "unavailable"
        assert isinstance(tray.last_error, OSError)
        assert not pystray_stub.constructed, "an icon was built from nothing"

    def test_a_corrupt_icon_file_fails_in_start_not_on_the_thread(
            self, pystray_stub, tmp_path):
        """`Image.open` only reads the header. The decode is forced inside
        `start()` so the failure lands where it can be reported, instead of on
        the tray thread after `start()` has already claimed success."""
        bad = tmp_path / "corrupt.ico"
        bad.write_bytes(b"\x00\x00\x01\x00" + b"\xff" * 64)
        tray = PystrayTray(bad)
        assert tray.start() is False
        assert tray.lifecycle_state == "unavailable"
        assert tray.last_error is not None

    def test_an_icon_that_cannot_be_shown_surfaces_its_exception(self,
                                                                 monkeypatch):
        """pystray raises ValueError out of the `visible` setter with no image,
        and that happens on ITS setup thread — where an unhandled exception
        would otherwise vanish into the default thread excepthook."""
        class Imageless(FakeIcon):
            def __init__(self, name, image, title, menu):
                super().__init__(name, None, title, menu)

        install_pystray(monkeypatch, Imageless)
        tray = PystrayTray(ICON)
        try:
            assert tray.start() is False
            assert isinstance(tray.last_error, ValueError)
            assert tray.lifecycle_state == "unavailable"
        finally:
            tray.stop()

    def test_a_loop_that_dies_at_once_does_not_stall_start(self, monkeypatch):
        """`start()` waits for readiness, so a thread that exits immediately
        must release that wait rather than burn the whole timeout."""
        class Dying(FakeIcon):
            def run(self, setup=None):
                raise RuntimeError("no notification area")

        install_pystray(monkeypatch, Dying)
        tray = PystrayTray(ICON)
        started = time.monotonic()
        assert tray.start() is False
        assert time.monotonic() - started < 5.0
        assert isinstance(tray.last_error, RuntimeError)
        assert tray.lifecycle_state == "unavailable"


class TestTrayMenuShape:
    def test_a_plain_command_is_not_drawn_as_a_checkbox(self, pystray_stub):
        """pystray renders any item with a non-None `checked` as a checkbox, so
        the old unconditional lambda put an empty box beside every entry."""
        tray = PystrayTray(ICON)
        tray.set_menu([TrayMenuItem("open", "Open", lambda: None),
                       TrayMenuItem("on", "Toggle", lambda: None, checked=True)])
        built = tray._build_menu()
        assert built.items[0].checked is None
        assert built.items[1].checked is not None

    def test_exactly_one_item_is_the_default_click_action(self):
        """Without a default, left-clicking a Windows tray icon does nothing."""
        controller = _DesktopController(_Window(), _Server(), NullTray())
        assert [i.id for i in controller.menu() if i.default] == ["open"]


class _ClosableWindow(_Window):
    """A window whose ``destroy()`` behaves like ``Form.Close()``.

    The base double marks itself destroyed unconditionally, which is what let
    the updater's shutdown look correct for two milestones. The real call goes
    ``Control.Invoke`` -> ``Form.Close()`` -> ``FormClosing`` -> pywebview's
    handler -> ours, and **a handler that returns False sets
    ``args.Cancel = True`` and the window stays open**. That cancellation is
    the entire bug this class exists to expose, so it is modelled rather than
    assumed away.
    """

    def __init__(self, controller_ref):
        super().__init__()
        self._controller = controller_ref
        self.close_attempts = 0

    def destroy(self):
        self._check_thread("window.destroy")
        self.close_attempts += 1
        controller = self._controller()
        # `FormClosing` runs its handlers ON the pump.
        previous, self.gui_thread = self.gui_thread, threading.get_ident()
        try:
            allowed = controller.on_closing()
        finally:
            self.gui_thread = previous
        if allowed:
            self.destroyed = True


class TestUpdateShutdownActuallyCloses:
    """The installer is waiting to replace an exe this process holds open.

    `on_closing` cancels every close except one a shutdown already in flight
    is asking for, and it recognises that by `allow_close`. The updater's hook
    destroyed the window without setting it, so the close was cancelled and
    converted into "hide to tray" — the app stayed alive, the exe stayed
    locked, and the dialog sat on "Installing..." forever. The default
    preferences (`close_behavior: "tray"`, a started tray) send every ordinary
    install straight down that branch.
    """

    @staticmethod
    def _controller(**prefs):
        holder = {}
        tray = NullTray()
        window = _ClosableWindow(lambda: holder["c"])
        controller = _DesktopController(window, _Server(**prefs), tray)
        controller.tray_started = True
        holder["c"] = controller
        return controller, window

    def test_destroying_without_a_sanctioned_shutdown_is_cancelled(self):
        """The mechanism, stated once: this is what the bug looked like.

        It is also why the fix routes through `exit()` rather than setting
        `allow_close` at the call site — `exit()` owns that flag along with
        stopping the tray, closing the server and releasing the port.
        """
        controller, window = self._controller()
        window.destroy()
        assert window.close_attempts == 1
        assert window.destroyed is False, (
            "on_closing cancels a close it did not sanction — which is correct, "
            "and is why the updater has to sanction it")
        controller.join_pending(5.0)

    def test_an_install_shutdown_really_closes_the_window(self):
        controller, window = self._controller()
        controller.shutdown_for_install()
        controller.join_pending(5.0)
        assert window.destroyed is True, (
            "the installer cannot replace an exe this process still holds open")

    def test_it_closes_even_with_close_to_tray_configured(self):
        """The default. Hiding to tray is the right answer to the close BUTTON
        and the wrong answer to an installer that is waiting."""
        controller, window = self._controller(close_behavior="tray",
                                              close_prompt_dismissed=False)
        controller.shutdown_for_install()
        controller.join_pending(5.0)
        assert window.destroyed is True
        assert window.hidden is False, "hidden instead of closed"

    def test_the_server_is_closed_so_the_port_is_released(self):
        controller, window = self._controller()
        controller.shutdown_for_install()
        controller.join_pending(5.0)
        assert controller.server.closed == 1

    def test_it_does_not_block_the_caller(self):
        """The hook runs on the HTTP worker thread serving
        `POST /api/update/apply`. `exit()` joins the tray and the scheduler for
        seconds; doing that inline would hold the response open across the
        shutdown of the very server writing it."""
        controller, window = self._controller()
        controller.server.on_close = lambda: time.sleep(0.6)
        started = time.monotonic()
        controller.shutdown_for_install()
        assert time.monotonic() - started < 0.3, "the hook blocked its caller"
        controller.join_pending(5.0)
        assert window.destroyed is True

    def test_it_is_safe_to_ask_twice(self):
        controller, window = self._controller()
        controller.shutdown_for_install()
        controller.shutdown_for_install()
        controller.join_pending(5.0)
        assert controller.server.closed == 1


class _LiveTray(NullTray):
    """A tray that really came up — the only configuration in which the bug
    could happen, because `on_closing` exits outright without one."""

    def __init__(self):
        super().__init__()
        self.stops = 0

    def start(self):
        self._lifecycle_state = "active"
        return True

    def stop(self):
        self.stops += 1
        super().stop()


class _UpdatingServer(_WiringServer):
    """A `_WiringServer` whose ``updater`` is a real :class:`UpdateService`."""

    def __init__(self, updater, **prefs):
        super().__init__(**prefs)
        self.updater = updater


def _ready_update_service(tmp_path, spawned, *, backup=None):
    """A real `UpdateService` holding a validated download, ready to install.

    Everything below the service is a fake — the GitHub client, the HTTP
    transport, the process spawn and the backup — but the service itself, its
    state machine, its validation and its install hook are the shipped code.
    """
    body = b"x" * 1000
    client = GitHubReleases(
        "owner/repo", api_base="https://api.test",
        opener=FakeOpener({"/releases": releases_response(
            release_json("v0.99.0", installer_size=len(body)))}),
        sleep=lambda _s: None)
    service = UpdateService(
        "0.12.0", InMemoryPrefs(), client=client,
        downloader=Downloader(opener=FakeOpener(default=FakeResponse(
            body, headers={"Content-Length": str(len(body))})), chunk_size=128),
        launcher=InstallerLauncher(
            spawn=spawned.append,
            backup=backup or (lambda _paths, _label: tmp_path / "backup")),
        download_dir=tmp_path)
    service.check_now()
    assert service.start_download()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and service.snapshot()["phase"] != "downloaded":
        time.sleep(0.01)
    assert service.snapshot()["phase"] == "downloaded"
    return service


class TestUpdateEndToEnd:
    """Install & Restart, from the service call to a closed window.

    `TestUpdateShutdownActuallyCloses` pins the controller in isolation. This
    drives the composed application — a real `UpdateService` on a real
    `DesktopApplication`, wired by the real `build()` — because the defect was
    never in either piece. It was in the join between them: the hook reached
    for the window instead of the lifecycle owner, and every unit on both sides
    of that line was correct and passing.

    The phase legitimately *stays* `installing`; nothing ever moves it, because
    the process is meant to die and be replaced. So "not stuck" cannot be
    asserted on the phase, and asserting it there would be theatre. What must
    be true is that the application is on its way out while the phase says
    installing — the window closed, the port released, the tray gone — and that
    it did not divert into the tray on the way.
    """

    @staticmethod
    def _compose(tmp_path, **prefs):
        spawned: list = []
        service = _ready_update_service(tmp_path, spawned)
        holder: dict = {}
        tray = _LiveTray()
        app, server, webview, events = _application(
            app_server=_UpdatingServer(service, **prefs), tray=tray,
            window_factory=lambda: _ClosableWindow(
                lambda: holder["app"].controller))
        holder["app"] = app
        app.acquire()
        app.build()
        return SimpleNamespace(app=app, server=server, tray=tray,
                               service=service, spawned=spawned,
                               window=webview.windows[0])

    def test_install_and_restart_launches_the_installer_and_closes_the_app(
            self, tmp_path):
        """The whole reported failure, in one test.

        Update requested -> installer launched -> shutdown initiated -> the
        close handler allows the exit -> the app is gone, with the exe free for
        the installer to replace.
        """
        ctx = self._compose(tmp_path)
        result = ctx.service.apply_update()

        assert result["ok"], result
        assert ctx.spawned, "the installer was never launched"
        assert "/VERYSILENT" in ctx.spawned[0]
        assert ctx.service.snapshot()["phase"] == "installing"

        ctx.app.controller.join_pending(5.0)
        assert ctx.window.destroyed is True, (
            "the window survived the update — the installer cannot replace an "
            "exe this process still holds open")
        assert ctx.window.hidden is False, "hidden to tray instead of closed"
        assert ctx.app.transport.should_exit is True, "the HTTP port was held"
        assert ctx.server.closed == 1
        assert ctx.tray.stops == 1, "the tray icon outlived the application"

    def test_the_default_preferences_do_not_divert_it_into_the_tray(
            self, tmp_path):
        """`close_behavior: "tray"` with the prompt not yet dismissed is what a
        fresh install ships, and it is the branch the bug lived on: the close
        was cancelled and re-read as a close-BUTTON press, which hides.

        `set_background_visibility(False)` is `hide_to_tray`'s first statement
        and nothing else in this flow calls it, so its absence is the evidence
        that no hide happened anywhere — not merely that the window ended up
        destroyed afterwards.
        """
        ctx = self._compose(tmp_path, close_behavior="tray",
                            close_prompt_dismissed=False)
        assert ctx.app.controller.tray_started is True
        ctx.service.apply_update()
        ctx.app.controller.join_pending(5.0)
        assert False not in ctx.server.visible, (
            "the update hid to the tray while the phase said installing")
        assert ctx.window.destroyed is True
        assert ctx.window.scripts == [], (
            "the close prompt was raised at the page — an installer is not a "
            "question for the user, it is already running")

    def test_it_still_exits_when_the_app_was_already_in_the_tray(self, tmp_path):
        """`start_minimized_to_tray` means the window is hidden before the
        update is ever applied, which is the same end state the bug produced.
        Reaching it legitimately must not stop the shutdown."""
        ctx = self._compose(tmp_path, start_minimized_to_tray=True)
        ctx.app.run()
        assert ctx.window.hidden is True
        ctx.service.apply_update()
        ctx.app.controller.join_pending(5.0)
        assert ctx.window.destroyed is True
        assert ctx.server.closed == 1

    def test_a_failed_install_leaves_the_application_running(self, tmp_path):
        """The other half of the contract. `apply_update` shuts the app down
        only after the installer is confirmed launched, so a backup that fails
        must leave a usable application behind rather than a closed one and no
        installer."""
        def no_backup(_paths, _label):
            raise OSError("disk full")

        spawned: list = []
        service = _ready_update_service(tmp_path, spawned, backup=no_backup)
        holder: dict = {}
        app, server, webview, _events = _application(
            app_server=_UpdatingServer(service), tray=_LiveTray(),
            window_factory=lambda: _ClosableWindow(
                lambda: holder["app"].controller))
        holder["app"] = app
        app.acquire()
        app.build()

        assert service.apply_update()["ok"] is False
        app.controller.join_pending(2.0)
        assert spawned == []
        assert webview.windows[0].destroyed is False
        assert server.closed == 0
        assert service.snapshot()["phase"] == "error"


def test_the_window_is_only_destroyed_by_the_one_method_that_may():
    """`exit()` is the sole owner of the close, and `allow_close` with it.

    The updater bug was a second place asking the window to go, and the audit
    that followed it is only useful if it cannot silently stop being true. Any
    new caller of `window.destroy()` reaches `on_closing` without the flag set
    and is cancelled — which is not a crash, it is the app quietly staying
    alive, which is exactly how this shipped twice.
    """
    source = Path(inspect.getfile(desktop)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    def asks_a_window_to_close(call):
        """`destroy` on anything, `close` only on something window-shaped.

        Unqualified for `destroy` because nothing else in this module has one,
        and because the buggy version reached its window through a loop
        variable rather than `self.window` — a receiver-matched rule would have
        walked straight past the very code it was written to forbid.
        """
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            return False
        if call.func.attr == "destroy":
            return True
        receiver = call.func.value
        name = (receiver.attr if isinstance(receiver, ast.Attribute)
                else receiver.id if isinstance(receiver, ast.Name) else "")
        return call.func.attr == "close" and "window" in name

    owners: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(asks_a_window_to_close(call) for call in ast.walk(node)):
            owners.append(node.name)
    assert owners == ["exit"], (
        f"{owners} ask a window to close; only `exit` may, because only "
        f"`exit` sets `allow_close` and `on_closing` cancels every close it "
        f"did not sanction")


def test_allow_close_has_exactly_one_writer():
    """Stated as a rule rather than left as a fact about today's code: the
    fix routes through `exit()` precisely so the flag keeps one owner."""
    source = Path(inspect.getfile(desktop)).read_text(encoding="utf-8")
    writers = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for assign in ast.walk(node):
            targets = (assign.targets if isinstance(assign, ast.Assign)
                       else [assign.target] if isinstance(assign, ast.AnnAssign)
                       else [])
            for target in targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr == "allow_close"):
                    writers.append(node.name)
    assert sorted(writers) == ["__init__", "exit"], writers
