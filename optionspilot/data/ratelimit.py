"""Request budgeting — spend a provider's quota deliberately, not accidentally.

The V0.5.2/V0.5.3 chain had no concept of a *budget*, because it did not need
one: Yahoo, yfinance and Stooq have soft, unmetered limits, so "back off when
told to" (the rate-limit window on `ProviderHealthMonitor`) was the whole story.

Keyed providers break that assumption hard. Alpha Vantage's free tier is **25
requests per day**. A single chart session switching symbols can spend that in
under a minute, and the failure is silent and lasts until midnight. Reacting to
a 429 *after* the fact is far too late; the budget has to be known and enforced
*before* the request.

So this module answers two questions the health monitor cannot:

    can I afford this request?      `QuotaTracker.allow()`
    how much is left?               `QuotaTracker.state()`

## Two windows, because providers publish two limits

A per-minute burst limit and a per-day total are genuinely different
constraints. Twelve Data allows 8/min *and* 800/day; exhausting the minute is a
short pause, exhausting the day is an outage until tomorrow. Both are tracked,
and `state()` reports which one is binding so diagnostics can say *why*.

The minute window is a real sliding window (a deque of timestamps), not a
fixed bucket. A fixed bucket lets 2× the limit through across a boundary — 8
requests at 10:00:59 and 8 more at 10:01:00 — which is exactly the burst that
gets an API key throttled.

## Why the daily count is persisted

A desktop app restarts. If the daily counter lived only in memory, quitting and
reopening OptionsPilot would appear to grant a fresh 25 Alpha Vantage requests;
the app would then spend requests it does not have and receive errors it cannot
explain. The count is therefore written to a small JSON file (atomically, like
`discovery.CapabilityStore`), keyed by provider and UTC date.

**Known limitation, deliberately accepted:** providers reset their daily quota
in their own timezone (Alpha Vantage at US/Eastern midnight), and this tracks
UTC days. The mismatch means the local budget can free up a few hours early or
late relative to the provider's. Since the local budget is only ever *more*
conservative than reacting to a live 429 — and a real quota error still
surfaces as `ProviderQuotaExceeded` from the adapter — the mismatch costs a few
unspent requests rather than correctness. Modelling per-provider reset
timezones would be more precise and considerably more code for that.

Nothing here is a scheduler. Distributing load *between* providers is already
what the ranking does; `pressure()` feeds budget consumption into that ranking
so traffic drifts off a nearly-exhausted provider before it is exhausted,
rather than after.
"""

from __future__ import annotations

import json
import threading
import time as _time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from optionspilot.core.logging_setup import get_logger

log = get_logger("data")

#: Reasons a request can be refused before it is made. Surfaced verbatim in
#: diagnostics so a user reads "daily quota exhausted", not "unavailable".
REFUSAL_MINUTE = "per-minute rate limit reached"
REFUSAL_DAILY = "daily quota exhausted"

#: Share of the daily budget at which the UI starts warning. Set at 80% because
#: the warning has to arrive while the user can still DO something about it —
#: adding a second provider, or simply not switching symbols for a while. A
#: warning at 95% of a 25-request budget arrives with one request left, which
#: is a notification, not a warning.
WARN_PRESSURE = 0.8


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """A provider's published limits. `None` means "no documented limit"."""

    per_minute: int | None = None
    per_day: int | None = None

    @property
    def metered(self) -> bool:
        """True when this provider has a limit worth counting against.

        The unmetered providers (Yahoo, yfinance, Stooq) get a tracker that
        always says yes, so there is one code path rather than two.
        """
        return self.per_minute is not None or self.per_day is not None

    def as_dict(self) -> dict:
        return {"per_minute": self.per_minute, "per_day": self.per_day}


UNMETERED = RateLimitPolicy()


class QuotaTracker:
    """Per-provider request budget. Thread-safe.

    `allow()` and `record()` are separate on purpose. Checking and spending in
    one call would make it impossible to ask "would this be allowed?" without
    consuming budget — which the diagnostics page, the ranking and the registry
    all need to do.
    """

    def __init__(self, name: str, policy: RateLimitPolicy | None = None, *,
                 store: "QuotaStore | None" = None,
                 clock=None, wall_clock=None):
        self.name = name
        self.policy = policy or UNMETERED
        self._store = store
        self._clock = clock or _time.monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        # Bounded by the limit it enforces. `_trim_minute` drops entries by
        # age, but age alone is not a bound: a provider with no per-minute
        # limit, or a burst inside one 60-second window, would grow this
        # without a ceiling. Capping at the limit is safe because the only
        # question asked of it is `len(...) >= per_minute`, and a provider
        # with no per-minute limit never asks at all.
        self._minute: deque[float] = deque(
            maxlen=self.policy.per_minute if self.policy.per_minute else 1)
        self._day: date = self._wall().date()
        self._used_today = 0
        if store is not None:
            restored, day = store.load(name)
            # Only restore a count from the SAME day; a stale count from last
            # week must not eat today's budget.
            if day == self._day:
                self._used_today = restored

    # ── the budget ───────────────────────────────────────────────────────────

    def allow(self) -> tuple[bool, str]:
        """(may we spend a request, why not). Never mutates the budget."""
        if not self.policy.metered:
            return True, ""
        with self._lock:
            self._roll_day()
            self._trim_minute()
            if (self.policy.per_day is not None
                    and self._used_today >= self.policy.per_day):
                return False, REFUSAL_DAILY
            if (self.policy.per_minute is not None
                    and len(self._minute) >= self.policy.per_minute):
                return False, REFUSAL_MINUTE
            return True, ""

    def record(self, count: int = 1) -> None:
        """Spend `count` requests. Called once per outbound attempt — including
        failed ones, because a rejected request still consumed quota upstream."""
        if not self.policy.metered:
            return
        with self._lock:
            self._roll_day()
            now = self._clock()
            # Only tracked when there IS a per-minute limit to enforce; with
            # none, the window would be a pure memory cost answering a question
            # nobody asks.
            if self.policy.per_minute:
                for _ in range(max(1, count)):
                    self._minute.append(now)
                self._trim_minute()
            self._used_today += max(1, count)
            used, day = self._used_today, self._day
        if self._store is not None:
            self._store.save(self.name, used, day)

    def exhaust_day(self) -> None:
        """Mark the daily budget spent — the provider told us so.

        A live "you have run out of API credits" is authoritative and beats our
        local count, which can drift low if requests were made by another
        process (or another install) sharing the same key.
        """
        if self.policy.per_day is None:
            return
        with self._lock:
            self._roll_day()
            self._used_today = max(self._used_today, self.policy.per_day)
            used, day = self._used_today, self._day
        log.warning("provider %s reported its daily quota exhausted (%d/%d)",
                    self.name, used, self.policy.per_day)
        if self._store is not None:
            self._store.save(self.name, used, day)

    def pressure(self) -> float:
        """Share of the daily budget consumed, 0.0–1.0.

        Feeds the health ranking so traffic drifts off a nearly-exhausted
        provider *before* it is exhausted. A provider with no daily limit
        reports 0 and is never penalised.
        """
        if self.policy.per_day is None:
            return 0.0
        with self._lock:
            self._roll_day()
            return min(1.0, self._used_today / max(1, self.policy.per_day))

    def state(self) -> dict:
        """What the diagnostics page shows for this provider's budget."""
        allowed, reason = self.allow()
        with self._lock:
            self._roll_day()
            self._trim_minute()
            remaining_day = (None if self.policy.per_day is None
                             else max(0, self.policy.per_day - self._used_today))
            remaining_minute = (None if self.policy.per_minute is None
                                else max(0, self.policy.per_minute
                                         - len(self._minute)))
            pressure = round(
                0.0 if self.policy.per_day is None
                else min(1.0, self._used_today / max(1, self.policy.per_day)), 3)
            return {
                "metered": self.policy.metered,
                "per_minute": self.policy.per_minute,
                "per_day": self.policy.per_day,
                "used_today": self._used_today,
                "remaining_today": remaining_day,
                "remaining_this_minute": remaining_minute,
                "allowed": allowed,
                "reason": reason,
                "pressure": pressure,
                # Computed here rather than in the page, so the text report,
                # the JSON export and the dashboard warn at the same moment.
                # `exhausted` is deliberately not `not allowed`: a provider
                # that has merely hit its per-MINUTE ceiling is back in under a
                # minute and must not be reported as spent for the day.
                "warning": bool(self.policy.per_day) and pressure >= WARN_PRESSURE,
                "exhausted": (self.policy.per_day is not None
                              and self._used_today >= self.policy.per_day),
            }

    def reset(self) -> None:
        """Clear the budget (tests, and an explicit user action)."""
        with self._lock:
            self._minute.clear()
            self._used_today = 0
            self._day = self._wall().date()

    # ── internals ────────────────────────────────────────────────────────────

    def _trim_minute(self) -> None:
        """Drop timestamps older than 60s. Called with the lock held.

        A real sliding window: a fixed bucket would let 2x the limit through
        across a boundary, which is precisely the burst that gets a key
        throttled.
        """
        cutoff = self._clock() - 60.0
        while self._minute and self._minute[0] <= cutoff:
            self._minute.popleft()

    def _roll_day(self) -> None:
        """Called with the lock held."""
        today = self._wall().date()
        if today != self._day:
            self._day = today
            self._used_today = 0


class QuotaStore:
    """Daily quota counts on disk, so a restart cannot mint free requests.

    Deliberately tiny and tolerant: an unreadable or malformed file degrades to
    "no history", never to a failed launch. Losing the count costs a few
    over-spent requests; refusing to start costs the whole application.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._doc: dict = {"version": 1, "providers": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("quota store at %s is unreadable (%s) — starting fresh",
                        self.path, exc)
            return
        if isinstance(doc, dict) and isinstance(doc.get("providers"), dict):
            self._doc = doc

    def load(self, provider: str) -> tuple[int, date | None]:
        """(requests used, the day they were used on)."""
        with self._lock:
            raw = (self._doc.get("providers") or {}).get(provider) or {}
        try:
            return int(raw.get("used", 0)), date.fromisoformat(raw["day"])
        except (KeyError, TypeError, ValueError):
            return 0, None

    def save(self, provider: str, used: int, day: date) -> None:
        with self._lock:
            self._doc.setdefault("providers", {})[provider] = {
                "used": int(used), "day": day.isoformat()}
            doc = json.dumps(self._doc, indent=2)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(doc, encoding="utf-8")
                tmp.replace(self.path)      # atomic: never a half-written file
            except OSError as exc:
                # Budget accounting must never break a chart. Worst case the
                # count resets on restart, which the next real 429 corrects.
                log.warning("could not persist quota state: %s", exc)


__all__ = ["RateLimitPolicy", "QuotaTracker", "QuotaStore", "UNMETERED",
           "REFUSAL_MINUTE", "REFUSAL_DAILY", "WARN_PRESSURE"]
