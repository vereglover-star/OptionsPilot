"""Notification core: event model, notifier interface, and the fan-out center.

Cardinal rule: a notification failure must NEVER interrupt trading. The center
catches and logs everything; notifiers are best-effort by contract.
"""

from __future__ import annotations

import abc
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


class Notifier(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def send(self, event: NotificationEvent) -> None:
        """Deliver one event. May raise — the center handles failures."""


class NotificationCenter:
    def __init__(self, cfg: NotifyConfig, notifiers: list[Notifier]):
        self._cfg = cfg
        self._notifiers = notifiers
        self.history: list[NotificationEvent] = []   # last events, for the UI

    def notify(self, kind: str, title: str, body: str = "") -> None:
        if kind not in KINDS:
            log.warning("unknown notification kind %r — sending anyway", kind)
        if kind == "daily_summary" and not self._cfg.daily_summary:
            return
        if kind == "weekly_summary" and not self._cfg.weekly_summary:
            return
        event = NotificationEvent(kind=kind, title=title, body=body)
        self.history.append(event)
        del self.history[:-200]
        log.info("[%s] %s — %s", kind, title, body.replace("\n", " | ")[:300])
        for notifier in self._notifiers:
            try:
                notifier.send(event)
            except Exception as exc:  # noqa: BLE001 — never break the loop
                log.error("notifier %s failed: %s", notifier.name, exc)


def build_notification_center(cfg: NotifyConfig) -> NotificationCenter:
    """Assemble the center from config flags."""
    from optionspilot.notify.desktop import DesktopNotifier
    from optionspilot.notify.email import EmailNotifier

    notifiers: list[Notifier] = []
    if cfg.desktop:
        notifiers.append(DesktopNotifier())
    if cfg.email:
        notifiers.append(EmailNotifier(cfg))
    return NotificationCenter(cfg, notifiers)
