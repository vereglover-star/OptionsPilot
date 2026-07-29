"""Desktop lifecycle tests use fakes; no Windows shell is required in CI."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

from optionspilot.notify import desktop as desktop_notify
from optionspilot.notify.base import NotificationEvent
from optionspilot.ui.desktop import _DesktopController
from optionspilot.ui.tray import NullTray, PystrayTray, TrayAdapter


class _Window:
    def __init__(self):
        self.hidden = False
        self.destroyed = False
        self.scripts = []

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def destroy(self):
        self.destroyed = True

    def evaluate_js(self, script):
        self.scripts.append(script)


class _Server:
    def __init__(self):
        self.runtime = SimpleNamespace(runtime_prefs=lambda: {
            "close_behavior": "tray", "close_prompt_dismissed": True,
        }, set_runtime_prefs=lambda **_patch: None)
        self.background = SimpleNamespace(snapshot=lambda: SimpleNamespace(paused=False))
        self.updater = SimpleNamespace(check_async=lambda: None)
        self.closed = 0
        self.visible = []

    def close(self): self.closed += 1
    def set_background_visibility(self, visible): self.visible.append(visible)
    def pause_background(self): pass
    def resume_background(self): pass
    def runtime_payload(self): return {"health": {}, "background": {}}


def test_disabled_tray_close_exits_instead_of_hiding_an_orphan_process():
    window, server, tray = _Window(), _Server(), NullTray()
    controller = _DesktopController(window, server, tray)
    controller.tray_started = False
    assert controller.on_closing() is False
    assert server.closed == 1
    assert window.destroyed
    assert not window.hidden


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
