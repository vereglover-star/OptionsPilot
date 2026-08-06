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
    #: Whether the user has seen it (M2-C6). Server-owned, because
    #: `localStorage` would mark a month of notifications unread again after a
    #: cleared profile, and because two clients disagreeing about an unread
    #: count is worse than having no count at all.
    read: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "body": self.body,
                "ts": self.ts, "severity": self.severity,
                "event_id": self.event_id, "action": self.action,
                "dismissed": self.dismissed, "read": self.read}


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
        read_ids = set(self._read_ids())
        views = [
            NotificationView(
                kind=event.kind, title=event.title, body=event.body,
                ts=event.ts.isoformat() if hasattr(event.ts, "isoformat")
                else str(event.ts),
                severity=severity_of(event.kind),
                event_id=getattr(event, "event_id", ""),
                action=getattr(event, "action", None),
                read=getattr(event, "event_id", "") in read_ids,
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

    # ── read state (M2-C6) ───────────────────────────────────────────────────
    #
    # The store is duck-typed exactly like `center` above: anything with
    # `notifications_read()` / `mark_notifications_read(ids)` satisfies it, so
    # this service still imports no config and a test double is two methods.

    def _read_ids(self) -> list[str]:
        if self._runtime is None:
            return []
        try:
            return list(self._runtime.notifications_read())
        except Exception:  # noqa: BLE001 — a lost read mark is not an outage
            log.debug("notification read state unreadable", exc_info=True)
            return []

    def mark_read(self, ids) -> int:
        """Mark ids read. Returns how many are now known-read.

        Never raises for the same reason nothing else here does: a notification
        subsystem that can break the caller is worse than one that forgets.
        """
        if self._runtime is None:
            return 0
        try:
            return len(self._runtime.mark_notifications_read(ids))
        except Exception:  # noqa: BLE001
            log.debug("could not persist notification read state", exc_info=True)
            return 0

    def unread_count(self, limit: int = 50) -> int:
        """How many of the recent notifications have not been seen."""
        return sum(1 for view in self.recent(limit) if not view.read)

    def highest_unread_severity(self, limit: int = 50) -> str | None:
        """The most severe unread severity, or None.

        The strip shows a count AND a glyph (`UI_V2_WIREFRAMES.md` §1.4), and
        the glyph is the half that says whether the count is worth interrupting
        for. Ranked here rather than in the client so two clients cannot
        disagree about which of three unread events is the loud one.
        """
        order = ["critical", "important", "notice", "info"]
        unread = [v.severity for v in self.recent(limit) if not v.read]
        for severity in order:
            if severity in unread:
                return severity
        return None

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
