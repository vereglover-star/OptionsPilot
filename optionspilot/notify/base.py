"""Notification core: event model, notifier interface, and the fan-out center.

Cardinal rule: a notification failure must NEVER interrupt trading. The center
catches and logs everything; notifiers are best-effort by contract.
"""

from __future__ import annotations

import abc
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from optionspilot.config.settings import NotifyConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import utcnow

log = get_logger("ui")

# Known event kinds; summaries are individually gated by config.
KINDS = ("trade_opened", "trade_closed", "risk_limit", "large_move",
         "daily_summary", "weekly_summary", "error")


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    kind: str
    title: str
    body: str
    ts: datetime = field(default_factory=utcnow)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dedupe_key: str | None = None
    action: dict | None = None


class Notifier(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def send(self, event: NotificationEvent) -> None:
        """Deliver one event. May raise — the center handles failures."""


class NotificationCenter:
    def __init__(self, cfg: NotifyConfig, notifiers: list[Notifier], store=None):
        self._cfg = cfg
        self._notifiers = notifiers
        self._store = store
        self._lock = threading.RLock()
        self._closed = False
        self.history: list[NotificationEvent] = []   # last events, for the UI
        if self._store is not None:
            try:
                self.history = self._store.recent(200)[::-1]
            except Exception:
                log.debug("notification inbox could not be loaded", exc_info=True)

    def notify(self, kind: str, title: str, body: str = "", *,
               action: dict | None = None, dedupe_key: str | None = None) -> None:
        with self._lock:
            if self._closed:
                log.debug("dropping notification after shutdown: %s", kind)
                return
        if kind not in KINDS:
            log.warning("unknown notification kind %r — sending anyway", kind)
        if kind == "daily_summary" and not self._cfg.daily_summary:
            return
        if kind == "weekly_summary" and not self._cfg.weekly_summary:
            return
        event = NotificationEvent(kind=kind, title=title, body=body,
                                  action=action, dedupe_key=dedupe_key)
        if self._store is not None:
            try:
                if not self._store.append(event):
                    return
            except Exception as exc:
                log.error("notification persistence failed: %s", exc)
        self.history.append(event)
        del self.history[:-200]
        log.info("[%s] %s — %s", kind, title, body.replace("\n", " | ")[:300])
        for notifier in self._notifiers:
            try:
                notifier.send(event)
            except Exception as exc:  # noqa: BLE001 — never break the loop
                log.error("notifier %s failed: %s", notifier.name, exc)

    def dismiss(self, event_id: str) -> bool:
        if self._store is None:
            return False
        try:
            removed = self._store.dismiss(event_id)
            if removed:
                self.history = [e for e in self.history if e.event_id != event_id]
            return removed
        except Exception:
            log.debug("notification dismiss failed", exc_info=True)
            return False

    def set_action_handler(self, handler) -> None:
        """Bind a host-specific action callback without coupling the center.

        Notification creation remains application-owned; only a host knows how
        to restore a window or select a destination after a native action.
        """
        for notifier in self._notifiers:
            setter = getattr(notifier, "set_action_handler", None)
            if setter is not None:
                setter(handler)

    def close(self) -> None:
        """Close durable and platform delivery resources exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            owners = [*self._notifiers, self._store]
        for owner in owners:
            close = getattr(owner, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    log.debug("notification resource close failed: %s",
                              type(owner).__name__, exc_info=True)


def build_notification_center(cfg: NotifyConfig, store_path=None) -> NotificationCenter:
    """Assemble the center from config flags."""
    from optionspilot.notify.desktop import DesktopNotifier
    from optionspilot.notify.email import EmailNotifier

    notifiers: list[Notifier] = []
    if cfg.desktop:
        notifiers.append(DesktopNotifier())
    if cfg.email:
        notifiers.append(EmailNotifier(cfg))
    store = None
    if store_path is not None:
        try:
            from optionspilot.notify.store import NotificationStore
            store = NotificationStore(store_path)
        except Exception as exc:
            log.warning("durable notification inbox unavailable: %s", exc)
    return NotificationCenter(cfg, notifiers, store=store)
