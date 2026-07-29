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
    available = True

    def __init__(self, icon_path: str | Path, name: str = "OptionsPilot"):
        self.icon_path = Path(icon_path)
        self.name = name
        self._icon = None
        self._thread: threading.Thread | None = None
        self._items: list[TrayMenuItem] = []
        self._status = TrayStatus(tooltip=name)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._lifecycle_state = "stopped"

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

    def start(self) -> bool:  # pragma: no cover - requires desktop session
        with self._lock:
            if self._lifecycle_state == "active":
                return True
            if self._lifecycle_state == "starting":
                return False
            self._lifecycle_state = "starting"
            self._stop_requested.clear()
        if not self.supported():
            with self._lock:
                self._lifecycle_state = "unavailable"
            return False
        import pystray
        from PIL import Image

        try:
            image = Image.open(self.icon_path)
            with self._lock:
                self._icon = pystray.Icon(
                    self.name, image, self._status.tooltip, self._build_menu())
                icon = self._icon
            self._thread = threading.Thread(
                target=self._run_icon, args=(icon,), name="system-tray", daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            log.warning("system tray unavailable: %s", exc)
            with self._lock:
                self._icon = None
                self._lifecycle_state = "unavailable"
            return False

    def _run_icon(self, icon) -> None:  # pragma: no cover - requires desktop session
        """Own pystray readiness and the start/stop hand-off in one thread.

        ``Icon.stop`` issued before ``Icon.run`` reaches its setup callback can
        otherwise be ignored by pystray.  Recording the stop request here
        closes that start/stop race and guarantees the sole tray thread exits.
        """
        def ready(ready_icon) -> None:
            with self._lock:
                stopping = self._stop_requested.is_set()
                if not stopping:
                    self._lifecycle_state = "active"
            if stopping:
                ready_icon.stop()

        try:
            icon.run(setup=ready)
        except Exception:
            log.debug("system tray loop stopped with an error", exc_info=True)
        finally:
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                if self._lifecycle_state != "unavailable":
                    self._lifecycle_state = "stopped"

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
            entries.append(pystray.MenuItem(
                item.label, invoke(item), enabled=item.enabled,
                checked=lambda _item, value=item.checked: value,
            ))
        return pystray.Menu(*entries)


def create_tray(icon_path: str | Path) -> TrayAdapter:
    if PystrayTray.supported():
        return PystrayTray(icon_path)
    return NullTray()
