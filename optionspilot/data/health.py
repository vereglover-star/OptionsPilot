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
KIND_AUTH = "auth"                    # the API key is missing/invalid/revoked
KIND_QUOTA = "quota"                  # the plan's allowance is spent
KIND_ENTITLEMENT = "entitlement"      # the key is VALID; the plan lacks access

ALL_KINDS = (KIND_UNAVAILABLE, KIND_TIMEOUT, KIND_RATE_LIMITED, KIND_RANGE,
             KIND_SYMBOL, KIND_VALIDATION, KIND_INTERNAL, KIND_AUTH,
             KIND_QUOTA, KIND_ENTITLEMENT)

#: The single definition of "does this failure say anything about the provider's
#: health?". Everything else reads this rather than re-deriving it.
#:
#: `auth` and `quota` count. They are not the provider being *broken*, but they
#: ARE reasons this install cannot use it, and a provider we cannot use must
#: fall to the back of the ranking rather than being retried on every chart
#: load. They are distinguished from `unavailable` because the remedy differs
#: completely — one needs a key, one needs tomorrow — and the diagnostics page
#: has to tell the user which.
COUNTS_AGAINST_HEALTH: dict[str, bool] = {
    KIND_UNAVAILABLE: True,
    KIND_TIMEOUT: True,
    KIND_RATE_LIMITED: True,
    KIND_VALIDATION: True,
    KIND_INTERNAL: True,
    KIND_AUTH: True,
    KIND_QUOTA: True,
    # An entitlement refusal counts, for the same reason `auth` does: the
    # provider is working perfectly and this install still cannot use it, so it
    # must fall to the back of the ranking rather than be retried on every
    # chart load. It is a SEPARATE kind because the remedy is completely
    # different — an auth failure means "fix your key", an entitlement failure
    # means "your key is fine, your plan does not include this data" — and
    # telling a user with a valid key that it was rejected sends them to
    # regenerate a key that was never the problem.
    KIND_ENTITLEMENT: True,
    KIND_RANGE: False,
    KIND_SYMBOL: False,
}

# ── why a provider is (un)available ──────────────────────────────────────────
#
# One vocabulary, shared by the monitor, the diagnostics payload, the text
# export and the dashboard, so "why can't I use Finnhub?" has exactly one
# answer everywhere it is asked.

STATUS_OK = "ok"                        # in rotation
STATUS_DISABLED = "disabled"            # switched off in configuration
STATUS_NO_API_KEY = "missing_api_key"   # needs a key; none configured
STATUS_AUTH_FAILED = "auth_failure"     # key present and rejected
#: The key is VALID and the plan does not include this data. Distinct from
#: `auth_failure` because the two look identical from the outside and have
#: opposite remedies — see `KIND_ENTITLEMENT` above and
#: `docs/MARKET_DATA.md` §28.
STATUS_PREMIUM_REQUIRED = "premium_required"
STATUS_QUOTA = "quota_exceeded"         # plan allowance spent
STATUS_RATE_LIMITED = "rate_limited"    # told to back off; recovers on its own
STATUS_CIRCUIT_OPEN = "temporarily_unavailable"   # breaker open after failures

#: Human wording for each status. The dashboard and the text report both render
#: from this rather than inventing their own phrasing.
STATUS_TEXT: dict[str, str] = {
    STATUS_OK: "available",
    STATUS_DISABLED: "disabled in configuration",
    STATUS_NO_API_KEY: "no API key configured",
    STATUS_AUTH_FAILED: "the API key was rejected",
    STATUS_PREMIUM_REQUIRED: "the API key is valid, but this provider's plan "
                             "does not include historical price data",
    STATUS_QUOTA: "the plan's request allowance is spent",
    STATUS_RATE_LIMITED: "rate limited — backing off",
    STATUS_CIRCUIT_OPEN: "temporarily out of rotation after repeated failures",
}

# ── the displayed state ──────────────────────────────────────────────────────
#
# `status()` above answers "may this provider be used, and if not, why" — it is
# a *gate*, and the registry reads it as one. It is not quite what a user needs
# to see, because it has no way to say "in rotation, but struggling": a provider
# failing one request in three is `ok` to the gate and alarming to a person.
#
# `health_state()` is the one-word answer for a human. It is DERIVED, never
# stored: a second stored copy of the same fact is how the adapter's counters
# and the registry's breaker came to disagree in V0.5.2 (see this module's
# docstring), and the whole point of consolidating them here was that there is
# now one place to derive it from.
#
# `testing` and `connecting` are in the vocabulary but are never returned by
# this module — they are transient states the UI owns while a Test Connection
# is in flight. They live here anyway so that the page and the backend share
# ONE list of states, and a badge style cannot be added for a state the backend
# can emit but the page has never heard of.

HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_OFFLINE = "offline"
HEALTH_DISABLED = "disabled"
HEALTH_MISSING_KEY = "missing_key"
HEALTH_RATE_LIMITED = "rate_limited"
HEALTH_CIRCUIT_OPEN = "circuit_open"
HEALTH_UNAVAILABLE = "unavailable"
HEALTH_PREMIUM_REQUIRED = "premium_required"
HEALTH_UNKNOWN = "unknown"
HEALTH_TESTING = "testing"        # UI-only, while a connection test runs
HEALTH_CONNECTING = "connecting"  # UI-only, while the first request is in flight

ALL_HEALTH_STATES = (
    HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_OFFLINE, HEALTH_DISABLED,
    HEALTH_MISSING_KEY, HEALTH_RATE_LIMITED, HEALTH_CIRCUIT_OPEN,
    HEALTH_UNAVAILABLE, HEALTH_PREMIUM_REQUIRED, HEALTH_TESTING,
    HEALTH_CONNECTING, HEALTH_UNKNOWN,
)

#: One plain-English sentence per state. Every place a state is displayed must
#: display this next to it — a coloured badge that says "degraded" and nothing
#: else tells a user they have a problem without telling them what it is.
HEALTH_TEXT: dict[str, str] = {
    HEALTH_HEALTHY: "Working normally and first in line for its rank.",
    HEALTH_DEGRADED: "Still in use, but failing some requests or returning "
                     "lower-quality data — it has been moved down the order.",
    HEALTH_OFFLINE: "Every recent request to this provider has failed. The "
                    "next one or two failures will take it out of rotation.",
    HEALTH_DISABLED: "Switched off here. It will not be asked for data until "
                     "you turn it back on.",
    HEALTH_MISSING_KEY: "This provider needs a free API key before it can be "
                        "used. Nothing is broken — it is simply not set up.",
    HEALTH_RATE_LIMITED: "Out of requests for now. It rejoins automatically "
                         "when the limit resets — no action needed.",
    HEALTH_CIRCUIT_OPEN: "Taken out of rotation after repeated failures. It "
                         "is retried automatically with one probe request "
                         "when the cooldown expires.",
    HEALTH_UNAVAILABLE: "Cannot be used right now. The reason is shown below "
                        "— it usually means the API key was rejected.",
    HEALTH_PREMIUM_REQUIRED: "Your API key works, but this provider's free "
                             "plan does not include historical price data. "
                             "Nothing is broken and there is nothing to fix — "
                             "it needs a paid plan, or you can simply leave it "
                             "switched off and use the other providers.",
    HEALTH_TESTING: "Running a live test request…",
    HEALTH_CONNECTING: "Contacting the provider…",
    HEALTH_UNKNOWN: "Not used yet in this session, so there is nothing "
                    "measured to report.",
}

#: Recent failure rate at or above which an in-rotation provider is DEGRADED.
#: One failure in four is well past noise — the shipped chain answers >99% of
#: requests when healthy — and comfortably below the breaker's own threshold,
#: so "degraded" is a warning the user sees BEFORE the provider drops out.
DEGRADED_FAILURE_RATE = 0.25
#: Rolling quality score below which a provider is degraded even while it is
#: answering. A source returning bars that only just pass validation is a
#: source about to start failing it.
DEGRADED_QUALITY = 70.0

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
#: Rank points a provider carries at 100% of its daily budget spent (0 at 0%).
#: This is what distributes load *between* metered providers: as one approaches
#: its ceiling it drifts down the ranking, so traffic moves to a fresher key
#: BEFORE the quota is gone rather than after. Sized at 25 — larger than a
#: single priority step (10), so budget genuinely reorders the chain, but below
#: the failure weights, so a healthy-but-busy provider still outranks a broken
#: one. Providers with no daily limit contribute 0 and are never penalised.
QUOTA_PRESSURE_WEIGHT = 25.0

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
                 quota=None, disabled_reason: str | None = None,
                 clock=None, wall_clock=None):
        self.name = name
        self.priority = priority
        self.breaker_policy = breaker or DEFAULT_BREAKER
        #: Optional `ratelimit.QuotaTracker`. Availability is ONE question, so
        #: the budget is consulted here rather than by a second gate in the
        #: registry — the same reason the breaker and the rate-limit window
        #: live here (see this module's docstring).
        self.quota = quota
        #: Set when the provider cannot be used at all in this install (no API
        #: key, switched off in config). Distinct from the breaker: it does not
        #: expire, is not a health event, and names its own remedy.
        self.disabled_reason = disabled_reason
        #: Set when a live request proved the key wrong. Sticky, because
        #: retrying a rejected key on every chart load only burns requests.
        self.auth_failed = False
        #: Set when a live request proved the key VALID but the plan
        #: insufficient — the provider authenticated us and then refused the
        #: data (HTTP 403). Sticky for the same reason as `auth_failed`, and
        #: separate from it because the two have opposite remedies.
        self.entitlement_failed = False
        #: Declared by the adapter, reported by diagnostics. Lets the dashboard
        #: distinguish "keyless provider, working" from "keyed provider, key
        #: present" without knowing anything about individual providers.
        self.requires_api_key = False
        self.api_key_configured = False
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
        """Is this provider in rotation right now?"""
        return self.status()[0] == STATUS_OK

    def status(self) -> tuple[str, str]:
        """(status code, human detail) — the single answer to "why can't I use
        this provider?".

        Checked in order of how permanent each condition is, so the reported
        reason is the one the user would actually have to act on: a provider
        with no API key is not "temporarily unavailable", and one whose daily
        quota is spent is not "rate limited". Every consumer — the registry,
        the dashboard, the text export, the ranking — reads this rather than
        re-deriving availability from the individual flags.
        """
        if self.disabled_reason:
            code = (STATUS_NO_API_KEY if self.disabled_reason == STATUS_NO_API_KEY
                    else STATUS_DISABLED)
            return code, STATUS_TEXT.get(code, self.disabled_reason)
        if self.auth_failed:
            return STATUS_AUTH_FAILED, STATUS_TEXT[STATUS_AUTH_FAILED]
        # Checked after `auth_failed` because a rejected key is the more
        # fundamental problem: if we cannot authenticate at all, what the plan
        # includes is not yet a question worth answering.
        if self.entitlement_failed:
            return (STATUS_PREMIUM_REQUIRED,
                    STATUS_TEXT[STATUS_PREMIUM_REQUIRED])
        if self.quota is not None:
            allowed, reason = self.quota.allow()
            if not allowed:
                # A spent daily allowance and a hit per-minute ceiling are
                # different problems with different remedies, so they report
                # differently even though both refuse the request.
                code = (STATUS_RATE_LIMITED if "minute" in reason
                        else STATUS_QUOTA)
                return code, reason
        now = self._clock()
        with self._lock:
            if self.rate_limited_until:
                if self.rate_limited_until > now:
                    return (STATUS_RATE_LIMITED,
                            f"backing off for another "
                            f"{self.rate_limited_until - now:.0f}s")
                self.rate_limited_until = None
            if self._is_open(now):
                return (STATUS_CIRCUIT_OPEN,
                        f"out of rotation for another "
                        f"{self._open_until - now:.0f}s after "
                        f"{self.consecutive_failures} consecutive failures")
        return STATUS_OK, STATUS_TEXT[STATUS_OK]

    def health_state(self) -> tuple[str, str]:
        """(state, plain-English explanation) — the one-word answer for a human.

        Derived from `status()` plus the measured counters; see the vocabulary
        block at the top of this module for why it is separate from `status()`
        and why nothing stores it.
        """
        code, detail = self.status()
        if code == STATUS_DISABLED:
            return HEALTH_DISABLED, HEALTH_TEXT[HEALTH_DISABLED]
        if code == STATUS_NO_API_KEY:
            return HEALTH_MISSING_KEY, HEALTH_TEXT[HEALTH_MISSING_KEY]
        if code == STATUS_AUTH_FAILED:
            return HEALTH_UNAVAILABLE, detail
        if code == STATUS_PREMIUM_REQUIRED:
            return HEALTH_PREMIUM_REQUIRED, HEALTH_TEXT[HEALTH_PREMIUM_REQUIRED]
        if code in (STATUS_RATE_LIMITED, STATUS_QUOTA):
            # Both mean "out of requests for now, back on its own". They are
            # kept apart in `status()` because the remedy differs (wait a
            # minute vs wait for the daily reset) and `status_detail` says
            # which — the STATE is the same either way, and inventing two
            # badges for one situation is how a status page stops being read.
            return HEALTH_RATE_LIMITED, detail
        if code == STATUS_CIRCUIT_OPEN:
            return HEALTH_CIRCUIT_OPEN, detail
        with self._lock:
            answered = self.successes + self.failures
            recent = (sum(self._recent) / len(self._recent)) if self._recent else 0.0
            quality = self.data_quality_score
            streak = self.consecutive_failures
        if not answered:
            return HEALTH_UNKNOWN, HEALTH_TEXT[HEALTH_UNKNOWN]
        if self._recent and recent >= 1.0:
            return HEALTH_OFFLINE, HEALTH_TEXT[HEALTH_OFFLINE]
        if recent >= DEGRADED_FAILURE_RATE or quality < DEGRADED_QUALITY or streak:
            return HEALTH_DEGRADED, HEALTH_TEXT[HEALTH_DEGRADED]
        return HEALTH_HEALTHY, HEALTH_TEXT[HEALTH_HEALTHY]

    def note_auth_failure(self) -> None:
        """A live request proved the configured key wrong.

        Sticky until `clear_auth_failure()`: re-testing a rejected key on every
        chart load spends requests to learn something already known, and on
        some providers repeated auth failures get an IP blocked.
        """
        if not self.auth_failed:
            log.warning("provider %s rejected our API key — taking it out of "
                        "rotation until the key changes", self.name)
        self.auth_failed = True

    def clear_auth_failure(self) -> None:
        """The key changed (or an operator asked for a retry).

        Clears the entitlement verdict too. A different key may well be on a
        different plan — that is the single most likely reason someone pastes a
        new one after seeing "premium plan required" — so keeping the old
        verdict would bench an upgraded key for the rest of the session and
        make the upgrade look like it had not worked.
        """
        self.auth_failed = False
        self.entitlement_failed = False

    def note_entitlement_failure(self) -> None:
        """A live request proved the key valid and the PLAN insufficient.

        Sticky, exactly like `note_auth_failure`: an entitlement does not
        change between two chart loads, so retrying spends requests to learn
        something already known. Cleared when the key changes.
        """
        if not self.entitlement_failed:
            log.warning("provider %s authenticated us and then refused the "
                        "data — the key is valid but the plan does not include "
                        "it. Taking it out of rotation until the key changes.",
                        self.name)
        self.entitlement_failed = True

    @property
    def permanently_unusable(self) -> bool:
        """True when nothing but USER ACTION can bring this provider back.

        The distinction this draws is load-bearing for
        `registry.deepest_earliest`: a provider that is *temporarily* out
        (breaker open, rate limited, over budget) will return on its own and
        should still contribute a history floor, so the reported start of
        history does not lurch about as breakers open and close. One that is
        permanently out — switched off, no key, rejected key, insufficient
        plan — can never answer, and counting its declared depth would tell the
        chart that history reaches back further than anything reachable. That
        is the retry-forever bug class V0.5.2 was built to eliminate.
        """
        return bool(self.disabled_reason or self.auth_failed
                    or self.entitlement_failed)

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

    def rank(self, *, include_latency: bool = True) -> float:
        """Sort key for provider selection — LOWER IS BETTER.

        Anchored on the static priority so a cold system reproduces the old
        fixed order exactly; see the module docstring for the scale.

        `include_latency=False` is what HYBRID ordering means (see
        `data/config.py::ORDER_HYBRID`): the user's chosen order stands, and a
        provider only loses its place when it is genuinely *failing* — not when
        it is merely slower than the one behind it. Latency is the one term
        that reorders healthy providers, so dropping it is the whole difference
        between "respect my order unless something is broken" and "always use
        whatever is fastest right now".
        """
        # Budget pressure is read BEFORE taking our lock: the quota tracker has
        # a lock of its own, and acquiring it while holding this one — when
        # some other path could plausibly do the reverse — is how a deadlock is
        # built. Nothing here needs the two to be consistent to the instant.
        pressure = self._quota_pressure
        with self._lock:
            return self._rank_locked(pressure, include_latency=include_latency)

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
        # Taken OUTSIDE this monitor's lock: both consult the quota tracker,
        # which holds its own lock. Calling them while holding ours would nest
        # two locks in one order here and the reverse order in `rank()`, which
        # is how deadlocks are built.
        status_code, status_detail = self.status()
        health_code, health_detail = self.health_state()
        quota_state = self.quota.state() if self.quota is not None else None
        pressure = self._quota_pressure
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
                "rank": round(self._rank_locked(pressure), 2),
                "available": status_code == STATUS_OK,
                # The single vocabulary for "why can't I use this provider?".
                "status": status_code,
                "status_detail": status_detail,
                # The one-word answer for a HUMAN, plus the sentence that must
                # always accompany it. Derived from the same counters, never
                # stored — see the vocabulary block at the top of this module.
                "health_state": health_code,
                "health_detail": health_detail,
                "quota_pressure": round(pressure, 3),
                "requires_api_key": self.requires_api_key,
                "api_key_configured": self.api_key_configured,
                "quota": quota_state,
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
                "auth_failures": self.by_kind.get(KIND_AUTH, 0),
                "entitlement_failures": self.by_kind.get(KIND_ENTITLEMENT, 0),
                "premium_required": self.entitlement_failed,
                "permanently_unusable": self.permanently_unusable,
                "quota_failures": self.by_kind.get(KIND_QUOTA, 0),
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

    def _rank_locked(self, pressure: float = 0.0, *,
                     include_latency: bool = True) -> float:
        """Called with the lock held. `pressure` is passed IN rather than read
        here, so this never reaches for the quota tracker's lock while holding
        ours (see `rank()`)."""
        latency = (min(self.avg_latency_ms / LATENCY_MS_PER_RANK_POINT,
                       MAX_LATENCY_PENALTY) if include_latency else 0.0)
        # The RECENT rate, not the lifetime one — see `OUTCOME_WINDOW`.
        rate = (sum(self._recent) / len(self._recent)) if self._recent else 0.0
        return (float(self.priority) + latency + rate * FAILURE_RATE_WEIGHT
                + self.consecutive_failures * CONSECUTIVE_FAILURE_WEIGHT
                + min(self.breaker_trips, MAX_COUNTED_TRIPS) * BREAKER_TRIP_WEIGHT
                + max(0.0, 100.0 - self.data_quality_score) * QUALITY_WEIGHT
                + max(0.0, min(1.0, pressure)) * QUOTA_PRESSURE_WEIGHT)

    @property
    def _quota_pressure(self) -> float:
        """Share of the daily budget already spent, 0.0–1.0. Takes the quota
        tracker's lock, so it must be called WITHOUT holding ours."""
        return self.quota.pressure() if self.quota is not None else 0.0

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
    "KIND_SYMBOL", "KIND_VALIDATION", "KIND_INTERNAL", "KIND_AUTH",
    "KIND_QUOTA", "KIND_ENTITLEMENT", "ALL_KINDS", "COUNTS_AGAINST_HEALTH",
    "LATENCY_MS_PER_RANK_POINT", "QUOTA_PRESSURE_WEIGHT",
    "STATUS_OK", "STATUS_DISABLED", "STATUS_NO_API_KEY", "STATUS_AUTH_FAILED",
    "STATUS_PREMIUM_REQUIRED", "STATUS_QUOTA", "STATUS_RATE_LIMITED",
    "STATUS_CIRCUIT_OPEN", "STATUS_TEXT",
    "HEALTH_HEALTHY", "HEALTH_DEGRADED", "HEALTH_OFFLINE", "HEALTH_DISABLED",
    "HEALTH_MISSING_KEY", "HEALTH_RATE_LIMITED", "HEALTH_CIRCUIT_OPEN",
    "HEALTH_UNAVAILABLE", "HEALTH_PREMIUM_REQUIRED", "HEALTH_TESTING",
    "HEALTH_CONNECTING", "HEALTH_UNKNOWN", "ALL_HEALTH_STATES", "HEALTH_TEXT",
    "DEGRADED_FAILURE_RATE", "DEGRADED_QUALITY",
]
