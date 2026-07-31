"""System tray adapters.

The menu/status models are platform-neutral.  ``PystrayTray`` is the Windows
desktop implementation; ``NullTray`` makes unsupported hosts and tests safe.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from optionspilot.core.logging_setup import get_logger

log = get_logger("ui")


@dataclass(frozen=True, slots=True)
class TrayMenuItem:
    id: str
    label: str
    callback: Callable[[], None] | None = None
    enabled: bool = True
    checked: bool = False
    separator: bool = False
    #: The item a plain left-click / double-click on the icon invokes. Without
    #: one, clicking a Windows tray icon does nothing at all and the menu is
    #: reachable only by right-click.
    default: bool = False


@dataclass(frozen=True, slots=True)
class TrayStatus:
    state: str = "healthy"
    tooltip: str = "OptionsPilot"


class TrayAdapter:
    """Minimal tray contract used by the desktop lifecycle controller."""

    available = False

    @property
    def lifecycle_state(self) -> str:
        return "unavailable"

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        return None

    def set_menu(self, items: list[TrayMenuItem]) -> None:
        return None

    def set_status(self, status: TrayStatus) -> None:
        return None

    def show(self) -> None:
        return None

    def hide(self) -> None:
        return None


class NullTray(TrayAdapter):
    """No-op adapter for tests and hosts without a tray capability."""

    def __init__(self) -> None:
        self.items: list[TrayMenuItem] = []
        self.status = TrayStatus()
        self.started = False
        self._lifecycle_state = "unavailable"

    @property
    def lifecycle_state(self) -> str:
        return self._lifecycle_state

    def start(self) -> bool:
        self.started = True
        self._lifecycle_state = "unavailable"
        return False

    def stop(self) -> None:
        self.started = False
        self._lifecycle_state = "stopped"

    def set_menu(self, items: list[TrayMenuItem]) -> None:
        self.items = list(items)

    def set_status(self, status: TrayStatus) -> None:
        self.status = status


class PystrayTray(TrayAdapter):
    """The Windows tray icon.

    ``start()`` returns True only when the icon is actually **in the
    notification area**, not merely when a thread has been created. That
    distinction is the whole point: V0.8.1 returned True for "I started a
    thread", the desktop controller believed the tray existed, and closing the
    window hid it into a tray that was never there.
    """

    available = True

    #: How long ``start`` waits for the icon to appear. The setup callback runs
    #: as soon as pystray's message loop is up, so this is normally
    #: milliseconds; the ceiling only bounds a pathological startup.
    READY_TIMEOUT = 10.0

    def __init__(self, icon_path: str | Path, name: str = "OptionsPilot"):
        self.icon_path = Path(icon_path)
        self.name = name
        self._icon = None
        self._thread: threading.Thread | None = None
        self._items: list[TrayMenuItem] = []
        self._status = TrayStatus(tooltip=name)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        #: Set once the icon is visible, has failed, or its loop has exited —
        #: whichever happens first. `start` must never wait on a dead thread.
        self._ready = threading.Event()
        self._ready_error: BaseException | None = None
        self._lifecycle_state = "stopped"

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._ready_error

    @property
    def lifecycle_state(self) -> str:
        with self._lock:
            return self._lifecycle_state

    @staticmethod
    def supported() -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except Exception:
            return False

    def start(self) -> bool:
        """Create the icon and wait until it is really in the notification area.

        Blocking is deliberate. A tray adapter that answers "yes" before the
        icon exists hands every caller a claim it cannot check, and the desktop
        controller acts on that claim by hiding the only window.
        """
        with self._lock:
            if self._lifecycle_state == "active":
                return True
            if self._lifecycle_state == "starting":
                return False
            self._lifecycle_state = "starting"
            self._stop_requested.clear()
            self._ready.clear()
            self._ready_error = None
        if not self.supported():
            log.warning("system tray unsupported: pystray/Pillow unavailable")
            with self._lock:
                self._lifecycle_state = "unavailable"
            return False
        import pystray
        from PIL import Image

        try:
            image = Image.open(self.icon_path)
            # `Image.open` only reads the header; decoding is lazy. Force it
            # here so a missing or corrupt icon fails in the caller's hands
            # with a real traceback, rather than on the tray thread later.
            image.load()
        except Exception as exc:
            log.exception("system tray icon could not be loaded from %s",
                          self.icon_path)
            with self._lock:
                self._icon = None
                self._ready_error = exc
                self._lifecycle_state = "unavailable"
            return False

        try:
            with self._lock:
                self._icon = pystray.Icon(
                    self.name, image, self._status.tooltip, self._build_menu())
                icon = self._icon
            self._thread = threading.Thread(
                target=self._run_icon, args=(icon,), name="system-tray",
                daemon=True)
            self._thread.start()
        except Exception as exc:
            log.exception("system tray could not be started")
            with self._lock:
                self._icon = None
                self._ready_error = exc
                self._lifecycle_state = "unavailable"
            return False

        if not self._ready.wait(self.READY_TIMEOUT):
            log.error("system tray did not appear within %.0fs — treating it "
                      "as unavailable", self.READY_TIMEOUT)
            with self._lock:
                self._lifecycle_state = "unavailable"
            return False
        with self._lock:
            if self._ready_error is not None:
                log.error("system tray failed to appear: %r", self._ready_error)
                return False
            return self._lifecycle_state == "active"

    def _run_icon(self, icon) -> None:
        """Own pystray readiness and the start/stop hand-off in one thread.

        ``Icon.stop`` issued before ``Icon.run`` reaches its setup callback can
        otherwise be ignored by pystray.  Recording the stop request here
        closes that start/stop race and guarantees the sole tray thread exits.
        """
        def ready(ready_icon) -> None:
            try:
                with self._lock:
                    stopping = self._stop_requested.is_set()
                if stopping:
                    ready_icon.stop()
                    return
                # THE line this whole subsystem turns on. pystray's default
                # setup callback is exactly `self.visible = True`, and passing
                # a custom `setup` REPLACES it rather than adding to it
                # (pystray/_base.py::_start_setup). `visible = True` is the
                # only path to `_show()`, which is the only caller of
                # Shell_NotifyIcon(NIM_ADD). Without it the Icon exists, the
                # message loop runs, every status read says "healthy", and no
                # icon is ever added to the notification area — which is
                # exactly how V0.8.1 shipped.
                ready_icon.visible = True
                with self._lock:
                    self._lifecycle_state = "active"
            except BaseException as exc:  # noqa: BLE001 - reported, not hidden
                log.exception("system tray icon could not be shown")
                with self._lock:
                    self._ready_error = exc
                    self._lifecycle_state = "unavailable"
            finally:
                # Always release `start`, including on the stop-during-start
                # path, so it can never wait out its timeout on a thread that
                # has already decided not to run.
                self._ready.set()

        try:
            icon.run(setup=ready)
        except BaseException as exc:  # noqa: BLE001 - reported, not hidden
            log.exception("system tray loop stopped with an error")
            with self._lock:
                if self._ready_error is None:
                    self._ready_error = exc
                self._lifecycle_state = "unavailable"
        finally:
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                if self._lifecycle_state != "unavailable":
                    self._lifecycle_state = "stopped"
            # A loop that exits before ever becoming ready must not leave
            # `start` blocked for the full timeout.
            self._ready.set()

    def stop(self) -> None:  # pragma: no cover - requires desktop session
        with self._lock:
            if self._lifecycle_state in {"stopped", "unavailable"} and self._icon is None:
                return
            self._lifecycle_state = "stopping"
            self._stop_requested.set()
            icon = self._icon
            self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                log.debug("tray stop failed", exc_info=True)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._lifecycle_state = "stopped"

    def set_menu(self, items: list[TrayMenuItem]) -> None:
        with self._lock:
            self._items = list(items)
            if self._icon is not None:
                self._icon.menu = self._build_menu()

    def set_status(self, status: TrayStatus) -> None:
        with self._lock:
            self._status = status
            if self._icon is not None:
                self._icon.title = status.tooltip

    def _build_menu(self):
        import pystray

        def invoke(item: TrayMenuItem):
            def action(_icon, _menu_item):
                if item.callback:
                    item.callback()
            return action

        entries = []
        for item in self._items:
            if item.separator:
                entries.append(pystray.Menu.SEPARATOR)
                continue
            # `checked` must stay None for a plain command. pystray renders any
            # item with a non-None `checked` as a CHECKBOX, so the previous
            # unconditional lambda drew an empty checkbox beside every entry.
            checked = (lambda _item: True) if item.checked else None
            entries.append(pystray.MenuItem(
                item.label, invoke(item), enabled=item.enabled,
                checked=checked, default=item.default,
            ))
        return pystray.Menu(*entries)


def create_tray(icon_path: str | Path) -> TrayAdapter:
    if PystrayTray.supported():
        return PystrayTray(icon_path)
    return NullTray()
