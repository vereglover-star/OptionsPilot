"""Desktop lifecycle tests use fakes; no Windows shell is required in CI."""

from __future__ import annotations

import gc
import importlib
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


class GuiThreadViolation(BaseException):
    """A GUI-thread operation that would deadlock the real process.

    Deliberately a ``BaseException``: the lifecycle code wraps these calls in
    ``except Exception`` on purpose (a window that has already gone must not
    break shutdown), and a real deadlock is not something those handlers get to
    catch and recover from. Raising an ordinary exception here would be
    swallowed by the very code under test and prove nothing.
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

    monkeypatch.setattr(desktop_notify, "_AVAILABLE", True)
    monkeypatch.setattr(desktop_notify, "InteractableWindowsToaster", Interactable)
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

        def run(self):
            return None

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
    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda *_args, **_kwargs: object())
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
