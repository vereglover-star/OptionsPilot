"""NotificationService — one API, many future delivery mechanisms.

Today OptionsPilot raises notifications in three unrelated ways. The
`NotificationCenter` fans trading events out to a Windows toast and an email
notifier. The frontend reads `orchestrator.notifier.history[-15:]` straight off
the status payload and renders its own in-app list. And the intelligence,
onboarding and updater layers surface their news by being *polled* — a goal that
completes, a lesson that unlocks, a provider that goes offline and an update
that lands are all discovered by whichever screen happens to be open.

That is fine for exactly one client on one machine, and it is the reason none of
those events can ever reach a phone: three mechanisms, only one of which has a
sink abstraction, and the sink abstraction is the one carrying the smallest
share of the events.

This service is the single entry point. Its job is narrow on purpose:

  * **Own the catalogue.** Every kind the application can raise, with its
    severity and whether it is worth waking a device for. A catalogue is what
    lets a future push sink decide *without* knowing what a "choch invalidation"
    is, and what stops each new sink re-deriving that judgement.
  * **Route, never render.** It produces `NotificationView`s. It contains no
    prose about any event — the caller supplies title and body, exactly as
    `NotificationCenter.notify` already required, because a service that
    authored the text would become a second place trading language lives.
  * **Never break the caller.** Inherited verbatim from `NotificationCenter`:
    a notification failure must never interrupt trading. Every method here
    swallows, logs and continues.

The center also receives a durable inbox in the V0.8 runtime. This service
continues to expose a bounded view-model projection while persistence,
deduplication, and platform delivery remain behind the notification core.
"""

from __future__ import annotations

from dataclasses import dataclass

from optionspilot.core.logging_setup import get_logger

log = get_logger("ui")

#: Severity, lowest to highest. Ordered so a sink can filter with `>=` rather
#: than enumerating kinds it has never heard of — which is what makes a sink
#: written today survive a kind added tomorrow.
SEVERITIES = ("info", "notice", "important", "critical")


@dataclass(frozen=True, slots=True)
class NotificationKind:
    """One thing the application can tell the user about."""

    key: str
    severity: str
    #: Plain-English description of when this fires. For a settings screen that
    #: lets a user choose what reaches their phone, and for the next person
    #: reading the catalogue.
    when: str
    #: Whether this is worth delivering to a device that is not in front of the
    #: user. Deliberately separate from severity: `provider_offline` is
    #: important and belongs in the app, and pushing it to a phone at 3am would
    #: teach the user to turn notifications off entirely.
    pushable: bool = False

    def to_dict(self) -> dict:
        return {"key": self.key, "severity": self.severity,
                "when": self.when, "pushable": self.pushable}


#: The complete catalogue. The first seven are `notify.base.KINDS` — the
#: trading events that have always existed, unchanged in name and meaning. The
#: rest are the events V0.6.0/V0.6.1 introduced and which have never had a
#: delivery path off the screen they were computed for.
CATALOGUE: dict[str, NotificationKind] = {
    k.key: k for k in (
        NotificationKind("trade_opened", "notice",
                         "a position was opened", pushable=True),
        NotificationKind("trade_closed", "notice",
                         "a round trip completed", pushable=True),
        NotificationKind("risk_limit", "critical",
                         "a risk limit tripped or trading halted", pushable=True),
        NotificationKind("large_move", "important",
                         "an unusual move on a watchlist symbol", pushable=True),
        NotificationKind("daily_summary", "info",
                         "the end-of-day summary"),
        NotificationKind("weekly_summary", "info",
                         "the weekly summary"),
        NotificationKind("error", "critical",
                         "a subsystem failed in a way worth surfacing",
                         pushable=True),
        # ── V0.7.0: events that existed but could only be discovered by polling
        NotificationKind("goal_achieved", "important",
                         "a trading goal reached its target", pushable=True),
        NotificationKind("lesson_unlocked", "info",
                         "the curriculum triggered a new lesson"),
        NotificationKind("ai_recommendation", "notice",
                         "the intelligence engine ranked a new action"),
        NotificationKind("provider_offline", "important",
                         "a market-data provider stopped answering"),
        NotificationKind("update_available", "notice",
                         "a newer version was published"),
        NotificationKind("tutorial_recommended", "info",
                         "the guide has a walkthrough worth offering"),
    )
}


@dataclass(frozen=True, slots=True)
class NotificationView:
    """One notification as a client renders it."""

    kind: str
    title: str
    body: str
    ts: str
    severity: str
    event_id: str = ""
    action: dict | None = None
    dismissed: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "body": self.body,
                "ts": self.ts, "severity": self.severity,
                "event_id": self.event_id, "action": self.action,
                "dismissed": self.dismissed}


def severity_of(kind: str) -> str:
    """The catalogued severity, defaulting to `info` for an unknown kind.

    Unknown rather than fatal, and `info` rather than `critical`, because the
    center has always accepted an uncatalogued kind and sent it anyway. A
    service that started rejecting them would silently drop notifications an
    older caller still raises; one that escalated them to critical would let a
    typo wake a phone.
    """
    entry = CATALOGUE.get(kind)
    return entry.severity if entry else "info"


def is_pushable(kind: str) -> bool:
    entry = CATALOGUE.get(kind)
    return bool(entry and entry.pushable)


class NotificationService:
    """The one place the application raises a notification.

    `center` is duck-typed to `notify()` plus a `history` list —
    `NotificationCenter` satisfies it, and so does a test double, which is why
    `notify/` is not imported here.
    """

    def __init__(self, center, runtime=None):
        self._center = center
        self._runtime = runtime

    # ── raising ──────────────────────────────────────────────────────────────

    def raise_event(self, kind: str, title: str, body: str = "", *,
                    action: dict | None = None,
                    dedupe_key: str | None = None) -> bool:
        """Send one notification. Returns whether it was accepted.

        Never raises. The cardinal rule from `notify/base.py` — a notification
        failure must NEVER interrupt trading — is not weakened by adding a layer
        above it, so this catches everything the center might let through.
        """
        if kind not in CATALOGUE:
            log.warning("uncatalogued notification kind %r — sending anyway "
                        "(add it to services/notifications.CATALOGUE)", kind)
        try:
            if action is None and dedupe_key is None:
                self._center.notify(kind, title, body)
            else:
                self._center.notify(kind, title, body, action=action,
                                    dedupe_key=dedupe_key)
            return True
        except Exception as exc:  # noqa: BLE001 — a notification must never
            log.error("notification %r failed: %s", kind, exc)   # break a caller
            return False

    # ── reading ──────────────────────────────────────────────────────────────

    def recent(self, limit: int = 15) -> list[NotificationView]:
        """The newest events first, as view models.

        Newest-first is the service's decision, not each client's. The desktop
        did this with a `[::-1]` inside the status payload builder; a second
        client getting it wrong would show a user their oldest notification as
        their newest, which is the sort of disagreement that is impossible to
        debug from either side.
        """
        limit = max(0, min(int(limit), 200))
        if limit == 0:
            return []
        try:
            history = list(self._center.history)[-limit:]
        except Exception:  # noqa: BLE001 — a broken history must not break a page
            log.debug("notification history unreadable", exc_info=True)
            return []
        views = [
            NotificationView(
                kind=event.kind, title=event.title, body=event.body,
                ts=event.ts.isoformat() if hasattr(event.ts, "isoformat")
                else str(event.ts),
                severity=severity_of(event.kind),
                event_id=getattr(event, "event_id", ""),
                action=getattr(event, "action", None),
            )
            for event in history
        ]
        mode = "normal"
        if self._runtime is not None:
            try:
                mode = self._runtime.runtime_prefs().get("notification_mode", "normal")
            except Exception:
                pass
        if mode != "normal":
            allowed = {"critical"}
            if mode == "reduced":
                allowed.add("important")
            views = [v for v in views if v.severity in allowed or
                     v.kind in {"update_available", "goal_achieved", "provider_offline"}]
        return views[::-1]

    def dismiss(self, event_id: str) -> bool:
        """Dismiss one durable event when the backing center supports it."""
        try:
            return bool(self._center.dismiss(event_id))
        except Exception:
            return False

    def catalogue(self) -> list[dict]:
        """Every kind this build can raise. For a preferences screen, and for a
        future push sink deciding what it is willing to forward."""
        return [k.to_dict() for k in CATALOGUE.values()]
