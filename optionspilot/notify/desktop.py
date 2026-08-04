"""Windows desktop toast notifications, degrading gracefully to log-only when
the windows-toasts package (or the WinRT runtime) is unavailable."""

from __future__ import annotations

import json

from optionspilot.core.logging_setup import get_logger
from optionspilot.notify.base import NotificationEvent, Notifier

log = get_logger("ui")

# Always bind optional platform symbols.  Desktop tests and alternate hosts can
# then replace the adapter deliberately, while a minimal server installation
# remains a harmless log-only notification sink.
InteractableWindowsToaster = None
Toast = None
_AVAILABLE = False
_PROBED = False


def _ensure_toaster_loaded() -> None:
    """Bind the optional Windows toast symbols, once, on FIRST USE.

    Deliberately not at import time. `optionspilot.notify` is reached by the
    reusable application layer (a `NotificationCenter` is injected into
    `ServiceRegistry`), so importing it must not drag a Windows GUI library
    into a Linux or mobile backend. V0.9.2-C11 caught exactly that: the static
    transport ban covers `services/`, and `windows_toasts` arrived
    transitively, which no import-graph check could see.

    The import stays a **literal** `from windows_toasts import ...` inside this
    function, not `importlib.import_module("windows_toasts")`. PyInstaller
    follows literal imports in function bodies; it cannot see a string, and a
    string here would silently drop the package from every build — the trap
    that cost this project yfinance in a shipped exe.

    Returns early when the symbols are already bound, so a test that installs a
    fake adapter is not overwritten by a probe.
    """
    global InteractableWindowsToaster, Toast, _AVAILABLE, _PROBED
    if _PROBED or _AVAILABLE or InteractableWindowsToaster is not None:
        return
    _PROBED = True
    try:
        from windows_toasts import InteractableWindowsToaster as _Interactable
        from windows_toasts import Toast as _Toast
        InteractableWindowsToaster = _Interactable
        Toast = _Toast
        _AVAILABLE = True
    except Exception:  # pragma: no cover - environment-dependent
        _AVAILABLE = False


class DesktopNotifier(Notifier):
    name = "desktop"

    def __init__(self, app_name: str = "OptionsPilot"):
        _ensure_toaster_loaded()
        self._action_handler = None
        self._toaster = None
        if _AVAILABLE and InteractableWindowsToaster is not None:
            try:
                # WindowsToaster silently drops ToastButton actions.  The
                # platform-neutral action contract requires the interactable
                # backend rather than a display-only notification surface.
                self._toaster = InteractableWindowsToaster(app_name)
            except Exception as exc:  # pragma: no cover
                log.warning("desktop toasts unavailable (%s) — log-only", exc)

    def set_action_handler(self, handler) -> None:
        self._action_handler = handler

    def send(self, event: NotificationEvent) -> None:
        if self._toaster is None:
            log.info("desktop(fallback) [%s] %s", event.kind, event.title)
            return
        actions = []
        action = event.action or {}
        action_id = action.get("action_id") or action.get("id")
        if action_id:
            try:
                from windows_toasts import ToastButton
                actions.append(ToastButton(
                    content=action.get("label", "Open OptionsPilot"),
                    arguments=json.dumps({"event_id": event.event_id,
                                          "action_id": action_id}),
                ))
            except Exception:  # pragma: no cover - package/version dependent
                log.debug("toast action button unavailable", exc_info=True)
        toast = Toast(actions=actions)
        toast.text_fields = [event.title, event.body[:200]]
        if self._action_handler is not None:
            def activated(args):
                try:
                    payload = json.loads(args.arguments or "{}")
                    self._action_handler(event, payload.get("action_id"),
                                         payload.get("event_id"))
                except Exception:
                    log.debug("toast action routing failed", exc_info=True)
            toast.on_activated = activated
        self._toaster.show_toast(toast)
