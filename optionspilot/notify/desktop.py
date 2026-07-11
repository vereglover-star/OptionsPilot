"""Windows desktop toast notifications, degrading gracefully to log-only when
the windows-toasts package (or the WinRT runtime) is unavailable."""

from __future__ import annotations

from optionspilot.core.logging_setup import get_logger
from optionspilot.notify.base import NotificationEvent, Notifier

log = get_logger("ui")

try:
    from windows_toasts import Toast, WindowsToaster
    _AVAILABLE = True
except Exception:  # pragma: no cover - environment-dependent
    _AVAILABLE = False


class DesktopNotifier(Notifier):
    name = "desktop"

    def __init__(self, app_name: str = "OptionsPilot"):
        self._toaster = None
        if _AVAILABLE:
            try:
                self._toaster = WindowsToaster(app_name)
            except Exception as exc:  # pragma: no cover
                log.warning("desktop toasts unavailable (%s) — log-only", exc)

    def send(self, event: NotificationEvent) -> None:
        if self._toaster is None:
            log.info("desktop(fallback) [%s] %s", event.kind, event.title)
            return
        toast = Toast()
        toast.text_fields = [event.title, event.body[:200]]
        self._toaster.show_toast(toast)
