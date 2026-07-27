"""Provider health — one owner for everything operational about one provider.

Before this module, a provider's operational state lived in two places that had
to agree: `adapter.ProviderHealth` counted requests and failures, while
`registry._Breaker` decided whether the provider was still in rotation — and it
decided that by *reading the adapter's counter*. Two objects, one invariant,
and a policy ("a range error is not a health event") duplicated across three
files. This module collapses that into a single `ProviderHealthMonitor` that
owns the counters, the latency history, the rate-limit window, the circuit
breaker and the ranking score together.

The consolidation is what makes the rest of V0.5.3 possible: a dashboard, a
ranking, and a benchmark all need the same numbers, and they must be the same
numbers.

## What a monitor knows

Volume (`requests`, `successes`, `failures`, `empties`, `bars`), the shape of
the failures (`timeouts`, `validation_failures`, `rate_limits`, and a per-kind
breakdown), latency (an EWMA plus a bounded sample window that yields a real
p95), recency (`last_success`, `last_failure`, `last_error`), the breaker
(state, trips, remaining cooldown) and a rolling data-quality score.

## Failure kinds, and the one policy that matters

Every failure is recorded with a `kind`. The kind decides whether it counts
against health, and that decision now exists exactly once (`COUNTS_AGAINST_
HEALTH`) instead of being re-derived by the adapter and the service:

    unavailable · timeout · rate_limited · validation · internal   count
    range · symbol                                                 do not

A range or symbol error is a *correct answer to an impossible question*.
Counting it would trip the breaker on a provider that is working perfectly —
which is precisely what used to happen every time a user scrolled a 5-minute
chart past Yahoo's 59-day floor.

## Ranking

`rank()` returns a sort key where **lower is better**, anchored on the
provider's static priority so a cold system behaves exactly like the old fixed
order. Health then moves a provider off that anchor:

    rank = priority
         + min(avg_latency_ms / 100, 50)      one priority step == 1s of latency
         + failure_rate * 50
         + consecutive_failures * 15
         + min(breaker_trips, 4) * 5
         + max(0, 100 - quality) * 0.5

The scale is deliberate and is the answer to "when should a slower provider
win?": priorities are spaced 10 apart, and 10 rank points is one second of
latency. So the shipped chain (Yahoo 10, yfinance 20, Stooq 30) keeps its order
while every provider is healthy and fast, a provider one second slower than its
neighbour loses one step, and a provider that is failing loses its place
immediately rather than after three more chart loads.
"""

from __future__ import annotations

import threading
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from optionspilot.core.logging_setup import get_logger

log = get_logger("data")

# ── failure taxonomy ─────────────────────────────────────────────────────────

KIND_UNAVAILABLE = "unavailable"      # transport, 5xx, unparseable payload
KIND_TIMEOUT = "timeout"              # the request did not come back in time
KIND_RATE_LIMITED = "rate_limited"    # upstream asked us to slow down
KIND_RANGE = "range"                  # outside this provider's supported depth
KIND_SYMBOL = "symbol"                # this provider does not list the symbol
KIND_VALIDATION = "validation"        # answered, but the bars were not usable
KIND_INTERNAL = "internal"            # an adapter bug

ALL_KINDS = (KIND_UNAVAILABLE, KIND_TIMEOUT, KIND_RATE_LIMITED, KIND_RANGE,
             KIND_SYMBOL, KIND_VALIDATION, KIND_INTERNAL)

#: The single definition of "does this failure say anything about the provider's
#: health?". Everything else reads this rather than re-deriving it.
COUNTS_AGAINST_HEALTH: dict[str, bool] = {
    KIND_UNAVAILABLE: True,
    KIND_TIMEOUT: True,
    KIND_RATE_LIMITED: True,
    KIND_VALIDATION: True,
    KIND_INTERNAL: True,
    KIND_RANGE: False,
    KIND_SYMBOL: False,
}

# ── breaker ──────────────────────────────────────────────────────────────────

STATE_CLOSED = "closed"        # in rotation
STATE_OPEN = "open"            # out of rotation until the cooldown expires
STATE_HALF_OPEN = "half_open"  # cooldown expired; one probe request allowed


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    """When to take a provider out of rotation, and for how long.

    Per-provider so a flaky-but-valuable source can be given more rope than a
    strict one without touching code (see `data/config.py`).
    """

    threshold: int = 3               # consecutive health-counting failures
    base_cooldown: float = 15.0      # first cooldown, doubling per trip
    max_cooldown: float = 300.0

    def cooldown_for(self, trips: int) -> float:
        return min(self.base_cooldown * (2 ** max(0, trips - 1)), self.max_cooldown)


DEFAULT_BREAKER = BreakerPolicy()

# ── ranking weights ──────────────────────────────────────────────────────────

#: Milliseconds of average latency that cost one rank point. At 100, one
#: priority step (providers are spaced 10 apart) equals one second of latency.
LATENCY_MS_PER_RANK_POINT = 100.0
#: Ceiling on the latency term, so one 30-second timeout cannot banish a
#: provider for longer than its EWMA takes to recover.
MAX_LATENCY_PENALTY = 50.0
FAILURE_RATE_WEIGHT = 50.0
CONSECUTIVE_FAILURE_WEIGHT = 15.0
BREAKER_TRIP_WEIGHT = 5.0
MAX_COUNTED_TRIPS = 4
QUALITY_WEIGHT = 0.5

#: How many recent latency samples to retain for percentiles. Small enough to
#: stay cheap, large enough that p95 means something.
LATENCY_WINDOW = 100

#: How many recent attempts the RANKING's failure rate is measured over.
#:
#: Ranking deliberately uses a moving window rather than the lifetime totals.
#: A lifetime rate never decays: a provider that failed five times during a
#: two-minute outage would sit at an 83% failure rate, and — because every
#: subsequent success only moves the denominator — would stay demoted for
#: thousands of requests after it was demonstrably healthy again. The window
#: lets a recovered provider climb back at a rate proportional to how long it
#: has been well, which is the behaviour "self-healing" actually means here.
#: Lifetime totals are still reported; they are just not what orders the chain.
OUTCOME_WINDOW = 50


class ProviderHealthMonitor:
    """Live operational state for one provider. Thread-safe.

    Every mutation is a `record_*` call, so the set of things that can change a
    provider's standing is enumerable by reading this class.
    """

    def __init__(self, name: str, *, priority: int = 100,
                 breaker: BreakerPolicy | None = None,
                 clock=None, wall_clock=None):
        self.name = name
        self.priority = priority
        self.breaker_policy = breaker or DEFAULT_BREAKER
        # Injectable so tests drive cooldowns and day rollovers without sleeping
        # or waiting for midnight.
        self._clock = clock or _time.monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

        # volume
        self.requests = 0
        self.successes = 0
        self.failures = 0
        self.empties = 0
        self.bars = 0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        #: The failure streak as it was immediately before the last recorded
        #: success. `demote_last_success` restores it, because a success that
        #: is later reclassified as a failure must not have broken the streak.
        self._streak_before_success = 0
        self.by_kind: dict[str, int] = {}

        # latency
        self.avg_latency_ms = 0.0
        self._latencies: deque[float] = deque(maxlen=LATENCY_WINDOW)
        #: True for each recent health-counting failure, False for each success
        #: — the window the ranking's failure rate is computed over.
        self._recent: deque[bool] = deque(maxlen=OUTCOME_WINDOW)

        # recency (monotonic for durations, wall clock for display)
        self.last_success: float | None = None
        self.last_failure: float | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None
        self.last_outcome: str = "unknown"

        # quality
        self.data_quality_score = 100.0
        self._quality_samples = 0

        # rate limiting
        self.rate_limited_until: float | None = None

        # breaker
        self.breaker_state = STATE_CLOSED
        self.breaker_trips = 0
        self._open_until = 0.0

        # today
        self._day: date = self._wall().date()
        self.requests_today = 0
        self.failures_today = 0

    # ── recording ────────────────────────────────────────────────────────────

    def record_success(self, latency_ms: float = 0.0, bars: int = 0) -> None:
        """A provider answered with usable data. Closes the breaker: a working
        provider is in rotation, no matter what it did five minutes ago."""
        with self._lock:
            self._roll_day()
            self.requests += 1
            self.requests_today += 1
            self.successes += 1
            self._streak_before_success = self.consecutive_failures
            self.consecutive_failures = 0
            self.consecutive_successes += 1
            self.bars += max(0, bars)
            self.rate_limited_until = None
            self.last_success = self._clock()
            self.last_success_at = self._wall()
            self.last_outcome = "ok"
            self._recent.append(False)
            self._observe_latency(latency_ms)
            if self.breaker_state != STATE_CLOSED or self.breaker_trips:
                log.info("provider %s recovered — closing circuit breaker",
                         self.name)
            self.breaker_state = STATE_CLOSED
            self.breaker_trips = 0
            self._open_until = 0.0

    def record_empty(self, latency_ms: float = 0.0) -> None:
        """The provider answered, with zero bars. Neither a success nor a
        failure: a weekend is not an outage, but it is not proof of health
        either, so it moves no counter except volume."""
        with self._lock:
            self._roll_day()
            self.requests += 1
            self.requests_today += 1
            self.empties += 1
            self.last_outcome = "empty"
            self._observe_latency(latency_ms)

    def record_failure(self, kind: str, detail: str = "",
                       latency_ms: float = 0.0, *,
                       retry_after: float | None = None) -> None:
        """One failed attempt, classified. Counts against health (and may trip
        the breaker) only for kinds in `COUNTS_AGAINST_HEALTH`."""
        counts = COUNTS_AGAINST_HEALTH.get(kind, True)
        with self._lock:
            self._roll_day()
            self.requests += 1
            self.requests_today += 1
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
            self.last_error = f"{kind}: {detail}"[:300] if detail else kind
            self.last_failure = self._clock()
            self.last_failure_at = self._wall()
            self.last_outcome = kind
            self._observe_latency(latency_ms)
            if retry_after is not None:
                self.rate_limited_until = self._clock() + retry_after
            if not counts:
                return
            self.failures += 1
            self.failures_today += 1
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            self._recent.append(True)
        # Deliberately does NOT trip the breaker. Counting and tripping are
        # separate steps because the caller that records a transport failure
        # (the adapter) is not the one that decides the attempt counted against
        # the provider (the service, which also sees validation rejects and
        # adapter bugs the adapter never hears about). Tripping here as well
        # would double every trip and double the cooldown on the first failure
        # streak. `evaluate_breaker()` is the single trip point.

    def demote_last_success(self, kind: str, detail: str = "") -> None:
        """Reclassify the request just recorded as a success into a failure.

        The adapter records success as soon as the transport returns parseable
        bars, because that is all it can judge; whether those bars are *usable*
        is decided a layer up, after semantic validation. When validation
        rejects them, the earlier success was provisional and wrong.

        This moves the counters rather than adding a second request, so one
        upstream call stays one request in every total — the alternative
        (recording a fresh failure) inflated `requests` and halved every
        provider's apparent failure rate.

        The failure STREAK is restored from before the success, not merely
        incremented. Recording the success had zeroed it, so without this a
        provider that answers every single request with unusable bars would
        oscillate 0 → 1 → 0 → 1 and never reach the breaker threshold — it
        would stay in rotation forever, which is the exact failure this
        reclassification exists to catch.
        """
        with self._lock:
            self.successes = max(0, self.successes - 1)
            self.consecutive_successes = 0
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
            self.failures += 1
            self.failures_today += 1
            self.consecutive_failures = self._streak_before_success + 1
            self.last_error = f"{kind}: {detail}"[:300] if detail else kind
            self.last_failure = self._clock()
            self.last_failure_at = self._wall()
            self.last_outcome = kind
            # Replace this attempt's window entry rather than appending: the
            # success being demoted already recorded one.
            if self._recent:
                self._recent[-1] = True
            else:
                self._recent.append(True)

    def observe_quality(self, score: float) -> None:
        """Feed a validated frame's quality score into the rolling average."""
        with self._lock:
            self._quality_samples += 1
            self.data_quality_score = _ewma(self.data_quality_score, score,
                                            self._quality_samples)

    def close_breaker(self) -> None:
        """Put the provider back in rotation without recording a request.

        The counterpart to `evaluate_breaker`: the service says "that attempt
        succeeded" after the adapter has already recorded the request itself,
        so this must move breaker state only — recording a second success here
        would double every provider's request count.
        """
        with self._lock:
            if self.breaker_state != STATE_CLOSED or self.breaker_trips:
                log.info("provider %s recovered — closing circuit breaker",
                         self.name)
            self.consecutive_failures = 0
            self.breaker_state = STATE_CLOSED
            self.breaker_trips = 0
            self._open_until = 0.0

    def evaluate_breaker(self) -> None:
        """Re-check the trip condition without recording a new failure.

        The service calls this after deciding an attempt counted against the
        provider, which keeps the *policy* ("was this the provider's fault?") in
        the service and the *mechanism* here.
        """
        with self._lock:
            self._maybe_trip()

    def _maybe_trip(self) -> None:
        """Called with the lock held."""
        if self.consecutive_failures < self.breaker_policy.threshold:
            return
        self.breaker_trips += 1
        cooldown = self.breaker_policy.cooldown_for(self.breaker_trips)
        self._open_until = self._clock() + cooldown
        self.breaker_state = STATE_OPEN
        log.warning("provider %s tripped its circuit breaker for %.0fs "
                    "(%d consecutive failures, last: %s)",
                    self.name, cooldown, self.consecutive_failures,
                    self.last_error)

    # ── breaker queries ──────────────────────────────────────────────────────

    def available(self) -> bool:
        """Is this provider in rotation right now? Rate limiting is honoured as
        a form of breaker: a provider that told us to back off is skipped no
        matter how healthy it looks."""
        now = self._clock()
        with self._lock:
            if self.rate_limited_until:
                if self.rate_limited_until > now:
                    return False
                self.rate_limited_until = None
            return not self._is_open(now)

    def _is_open(self, now: float) -> bool:
        """Called with the lock held."""
        if self._open_until > now:
            return True
        if self.breaker_state == STATE_OPEN:
            self.breaker_state = STATE_HALF_OPEN
        return False

    def take_half_open_probe(self) -> bool:
        """Claim the single probe request a cooldown-expired breaker allows.

        Returns True to exactly one caller per cooldown, so recovery costs one
        request rather than a restart — and a stampede of chart loads cannot
        turn "one probe" into fifty.
        """
        now = self._clock()
        with self._lock:
            if not self._open_until or self._open_until > now:
                return False
            if self.breaker_state == STATE_CLOSED:
                return False
            self.breaker_state = STATE_HALF_OPEN
            self._open_until = 0.0
            return True

    def force_open(self, seconds: float) -> None:
        """Test/ops hook: take a provider out of rotation immediately. A
        negative value expresses "a cooldown that has already lapsed"."""
        with self._lock:
            self.breaker_trips += 1
            self._open_until = self._clock() + seconds
            self.breaker_state = STATE_OPEN

    def reset(self) -> None:
        """Clear the breaker and the failure streak, keeping lifetime totals."""
        with self._lock:
            self.consecutive_failures = 0
            self.breaker_trips = 0
            self._open_until = 0.0
            self.breaker_state = STATE_CLOSED
            self.rate_limited_until = None

    # ── ranking ──────────────────────────────────────────────────────────────

    def rank(self) -> float:
        """Sort key for provider selection — LOWER IS BETTER.

        Anchored on the static priority so a cold system reproduces the old
        fixed order exactly; see the module docstring for the scale.
        """
        with self._lock:
            return self._rank_locked()

    # ── reporting ────────────────────────────────────────────────────────────

    @property
    def failure_rate(self) -> float:
        return (self.failures / self.requests) if self.requests else 0.0

    @property
    def success_rate(self) -> float:
        answered = self.successes + self.failures
        return (self.successes / answered) if answered else 1.0

    def latency_percentile(self, pct: float = 95.0) -> float:
        with self._lock:
            samples = sorted(self._latencies)
        if not samples:
            return 0.0
        idx = min(len(samples) - 1,
                  max(0, int(round(pct / 100.0 * (len(samples) - 1)))))
        return samples[idx]

    def snapshot(self) -> dict:
        """Everything the dashboard, the export and the benchmark display.

        Durations are rendered as seconds-ago rather than raw monotonic stamps,
        because a monotonic clock means nothing outside this process.
        """
        now = self._clock()
        with self._lock:
            self._roll_day()
            open_for = (round(self._open_until - now, 1)
                        if self._open_until > now else None)
            state = (STATE_OPEN if self._open_until > now
                     else (STATE_CLOSED if self.breaker_state == STATE_CLOSED
                           else STATE_HALF_OPEN))
            limited_for = (round(self.rate_limited_until - now, 1)
                           if self.rate_limited_until
                           and self.rate_limited_until > now else None)
            return {
                "name": self.name,
                "priority": self.priority,
                "rank": round(self._rank_locked(), 2),
                "available": open_for is None and limited_for is None,
                "state": state,
                "requests": self.requests,
                "successes": self.successes,
                "failures": self.failures,
                "empties": self.empties,
                "bars": self.bars,
                "success_rate": round(
                    self.successes / (self.successes + self.failures), 4)
                if (self.successes + self.failures) else 1.0,
                "failure_rate": round(self.failures / self.requests, 4)
                if self.requests else 0.0,
                "recent_failure_rate": round(
                    sum(self._recent) / len(self._recent), 4)
                if self._recent else 0.0,
                "consecutive_failures": self.consecutive_failures,
                "avg_latency_ms": round(self.avg_latency_ms, 1),
                "p95_latency_ms": round(_percentile(sorted(self._latencies), 95.0), 1),
                "timeouts": self.by_kind.get(KIND_TIMEOUT, 0),
                "validation_failures": self.by_kind.get(KIND_VALIDATION, 0),
                "rate_limits": self.by_kind.get(KIND_RATE_LIMITED, 0),
                "failures_by_kind": dict(self.by_kind),
                "breaker_trips": self.breaker_trips,
                "circuit_open_for": open_for,
                "rate_limited_for": limited_for,
                "data_quality_score": round(self.data_quality_score, 1),
                "last_success_at": (self.last_success_at.isoformat()
                                    if self.last_success_at else None),
                "seconds_since_success": (round(now - self.last_success, 1)
                                          if self.last_success else None),
                "last_failure_at": (self.last_failure_at.isoformat()
                                    if self.last_failure_at else None),
                "last_error": self.last_error,
                "last_outcome": self.last_outcome,
                "requests_today": self.requests_today,
                "failures_today": self.failures_today,
            }

    def _rank_locked(self) -> float:
        latency = min(self.avg_latency_ms / LATENCY_MS_PER_RANK_POINT,
                      MAX_LATENCY_PENALTY)
        # The RECENT rate, not the lifetime one — see `OUTCOME_WINDOW`.
        rate = (sum(self._recent) / len(self._recent)) if self._recent else 0.0
        return (float(self.priority) + latency + rate * FAILURE_RATE_WEIGHT
                + self.consecutive_failures * CONSECUTIVE_FAILURE_WEIGHT
                + min(self.breaker_trips, MAX_COUNTED_TRIPS) * BREAKER_TRIP_WEIGHT
                + max(0.0, 100.0 - self.data_quality_score) * QUALITY_WEIGHT)

    # ── internals ────────────────────────────────────────────────────────────

    def _observe_latency(self, latency_ms: float) -> None:
        """Called with the lock held."""
        if latency_ms <= 0:
            return
        self._latencies.append(latency_ms)
        self.avg_latency_ms = _ewma(self.avg_latency_ms, latency_ms,
                                    len(self._latencies))

    def _roll_day(self) -> None:
        """Reset the per-day counters when the calendar day turns over. Called
        with the lock held, from every recording path, so "requests today" stays
        honest in a desktop app that is left running for weeks."""
        today = self._wall().date()
        if today != self._day:
            self._day = today
            self.requests_today = 0
            self.failures_today = 0


# ── legacy-compatible view ───────────────────────────────────────────────────

@dataclass(slots=True)
class ProviderHealth:
    """A read-only view of a monitor in the pre-V0.5.3 shape.

    `adapter.health()` has always returned an object with these fields and
    several tests and scripts read them directly. Keeping the shape means the
    consolidation above is invisible to every existing caller.
    """

    name: str
    available: bool = True
    last_success: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    rate_limited_until: float | None = None
    data_quality_score: float = 100.0
    avg_latency_ms: float = 0.0
    _snapshot: dict = field(default_factory=dict, repr=False)

    @classmethod
    def of(cls, monitor: ProviderHealthMonitor) -> "ProviderHealth":
        snap = monitor.snapshot()
        return cls(
            name=monitor.name,
            available=snap["available"],
            last_success=monitor.last_success,
            last_error=monitor.last_error,
            last_error_at=monitor.last_failure,
            consecutive_failures=monitor.consecutive_failures,
            total_requests=monitor.requests,
            total_failures=monitor.failures,
            rate_limited_until=monitor.rate_limited_until,
            data_quality_score=monitor.data_quality_score,
            avg_latency_ms=monitor.avg_latency_ms,
            _snapshot=snap,
        )

    def as_dict(self, now: float | None = None) -> dict:
        return dict(self._snapshot)


def _ewma(current: float, sample: float, n: int) -> float:
    """Exponentially-weighted average that behaves like a plain mean for the
    first few samples, so one slow first request cannot dominate forever."""
    alpha = max(0.1, 1.0 / max(n, 1))
    return current * (1 - alpha) + sample * alpha


def _percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = min(len(sorted_samples) - 1,
              max(0, int(round(pct / 100.0 * (len(sorted_samples) - 1)))))
    return sorted_samples[idx]


__all__ = [
    "ProviderHealthMonitor", "ProviderHealth", "BreakerPolicy",
    "DEFAULT_BREAKER", "STATE_CLOSED", "STATE_OPEN", "STATE_HALF_OPEN",
    "KIND_UNAVAILABLE", "KIND_TIMEOUT", "KIND_RATE_LIMITED", "KIND_RANGE",
    "KIND_SYMBOL", "KIND_VALIDATION", "KIND_INTERNAL", "ALL_KINDS",
    "COUNTS_AGAINST_HEALTH", "LATENCY_MS_PER_RANK_POINT",
]
