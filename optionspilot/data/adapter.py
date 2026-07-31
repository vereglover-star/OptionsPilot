"""The provider adapter contract — one shape for every market-data source.

Everything upstream of this file (`MarketDataService`, the registry, the cache,
the UI) is provider-agnostic. Everything provider-specific — URL shapes, JSON
layouts, CSV columns, cookie dances, error dialects — lives *below* it, inside
one `HistoryAdapter` subclass. That boundary is the point of the whole design:
adding a provider must be writing one file, not editing five.

An adapter implements exactly two things:

    _fetch_native(symbol, spec, start, end, prepost) -> DataFrame
    _probe()                                          -> None   (health check)

and declares a `capabilities` table. The base class supplies everything else:
interval mapping, resampling to non-native timeframes, canonical-shape
normalization, timing, rate-limit bookkeeping, health/error state, and the
`data_quality_score` rolling average. Concrete adapters therefore contain
almost nothing but the transport and the parser, which is what makes them
cheap to add and cheap to audit.

Failures are typed, because the service reacts differently to each:

    ProviderRangeError    the request is outside what this provider CAN serve.
                          Never retry, never fail over blindly — a deeper
                          provider might help, a retry never will.
    ProviderRateLimited   back off, then try a different provider first.
    ProviderSymbolError   this symbol does not exist here. Failover may help
                          (different feeds list different symbols).
    ProviderUnavailable   transport/parse trouble. Retry, then fail over.

A bare empty frame is *not* a failure type in this layer — adapters raise
rather than return empties, so "no data" can never be silently confused with
"the network hiccuped". That confusion is the single most expensive bug this
subsystem has had.
"""

from __future__ import annotations

import abc
import threading
import time as _time
from dataclasses import dataclass, field, replace as _replace
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.base import session_index, validate_candles
from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities
from optionspilot.data.config import ProviderConfig
from optionspilot.data.faults import FAULTS
from optionspilot.data.health import (
    KIND_AUTH, KIND_ENTITLEMENT, KIND_INTERNAL, KIND_QUOTA, KIND_RANGE,
    KIND_RATE_LIMITED, KIND_SYMBOL, KIND_TIMEOUT, KIND_UNAVAILABLE,
    STATUS_DISABLED, STATUS_NO_API_KEY, ProviderHealth, ProviderHealthMonitor,
)
from optionspilot.data.ratelimit import QuotaTracker, RateLimitPolicy, UNMETERED

log = get_logger("data")

#: How long a LOCALLY-refused request (our own budget said no) asks the caller
#: to wait. Deliberately modest: the registry already keeps a budgeted-out
#: provider out of the candidate list, so this only ever covers the race
#: between that check and the request, and a minute-window refusal clears in
#: well under a minute.
_QUOTA_BACKOFF = 60.0


# ── typed failures ───────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Base for every adapter failure. `retryable` drives the service's retry
    loop; `failover` drives whether trying a different provider can help."""

    retryable = True
    failover = True


class ProviderUnavailable(ProviderError):
    """Transport, timeout, HTTP 5xx, or unparseable payload."""


class ProviderRateLimited(ProviderError):
    """Upstream asked us to slow down (HTTP 429, or a documented quota)."""

    def __init__(self, message: str, retry_after: float = 30.0):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    """The API key is missing, invalid, or revoked.

    Never retried and never failed *back* to: no number of attempts fixes a
    wrong key, and re-testing it on every chart load spends requests to learn
    something already known (and on some providers gets an IP blocked). The
    service marks the provider auth-failed, which takes it out of rotation
    until the configuration changes.

    **This used to cover entitlement failures too, and that was wrong.** See
    `ProviderEntitlementError` immediately below.
    """

    retryable = False


class ProviderEntitlementError(ProviderError):
    """**The key is valid. The plan does not include this data.**

    Deliberately NOT a subclass of `ProviderAuthError`, because the two are
    opposite diagnoses that happen to look alike from a distance, and
    conflating them produced a real, reproducible support failure:

    > Finnhub moved `/stock/candle` behind a paid plan. A brand-new, verified,
    > correctly-pasted free key gets **HTTP 403** from it. The shared status
    > mapping treated 401 and 403 alike, so the app told the user their API key
    > had been rejected — sending them to regenerate a key that was never the
    > problem, repeatedly, with the same result.

    The empirical difference is unambiguous and is what this class encodes:
    Finnhub answers an **invalid** key with `401 {"error":"Invalid API key."}`
    and a **valid key on the wrong plan** with `403 {"error":"You don't have
    access to this resource."}`. 401 means "I do not know who you are"; 403
    means "I know exactly who you are and you may not have this". That is the
    standard HTTP meaning of the two codes, and every keyed provider gets the
    distinction for free from `http_adapter._from_status`.

    Operationally it behaves like an auth failure — sticky, out of rotation,
    contributing no history floor — because the provider genuinely cannot serve
    this install. What changes is what the user is *told*, and therefore what
    they do about it.
    """

    retryable = False


class ProviderQuotaExceeded(ProviderRateLimited):
    """The plan's allowance is spent — a day's worth, not a minute's.

    A subclass of `ProviderRateLimited` so every existing back-off path keeps
    working, but distinguished because the remedy is completely different:
    a rate limit clears in seconds, a quota clears tomorrow (or on a paid
    plan). Diagnostics must not tell a user to "wait a moment" when the honest
    answer is "this key is done until tomorrow".
    """

    def __init__(self, message: str, retry_after: float = 3600.0):
        super().__init__(message, retry_after=retry_after)


class ProviderTimeout(ProviderUnavailable):
    """The request did not come back in time.

    A subclass of `ProviderUnavailable` so every existing `except` keeps
    working, but distinguished because a timeout and a 500 mean different
    things operationally: a provider that times out is *slow*, which the
    ranking should react to, while one that 500s is *broken*.
    """


def is_timeout(exc: BaseException) -> bool:
    """Does this transport exception mean "it did not come back in time"?

    urllib raises `socket.timeout` (an alias of the builtin `TimeoutError`)
    directly, but wraps it in `URLError` when the timeout happens during connect
    rather than during read — so the reason has to be unwrapped. yfinance and
    `requests` surface their own classes whose names end in `Timeout`, which is
    matched by name so this module need not import either.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, TimeoutError):        # socket.timeout is this
            return True
        if type(exc).__name__.endswith(("Timeout", "TimeoutError")):
            return True
        reason = getattr(exc, "reason", None)    # urllib.error.URLError
        exc = reason if isinstance(reason, BaseException) else exc.__cause__
    return False


def timeout_or_unavailable(message: str, exc: BaseException) -> ProviderError:
    """The typed failure for a transport error — `ProviderTimeout` when the
    request ran out of time, `ProviderUnavailable` otherwise. Adapters use this
    rather than classifying by hand, so 'slow' and 'broken' stay distinguishable
    in provider health without every adapter re-deriving the distinction."""
    kind = ProviderTimeout if is_timeout(exc) else ProviderUnavailable
    return kind(message)


def failure_kind(exc: BaseException) -> str:
    """Classify a provider failure for the health monitor. The one mapping from
    exception type to health-policy kind."""
    if isinstance(exc, ProviderTimeout):
        return KIND_TIMEOUT
    if isinstance(exc, ProviderAuthError):
        return KIND_AUTH
    if isinstance(exc, ProviderEntitlementError):
        return KIND_ENTITLEMENT
    # Order matters: ProviderQuotaExceeded IS a ProviderRateLimited, and the
    # more specific answer is the useful one.
    if isinstance(exc, ProviderQuotaExceeded):
        return KIND_QUOTA
    if isinstance(exc, ProviderRateLimited):
        return KIND_RATE_LIMITED
    if isinstance(exc, ProviderRangeError):
        return KIND_RANGE
    if isinstance(exc, ProviderSymbolError):
        return KIND_SYMBOL
    if isinstance(exc, ProviderError):
        return KIND_UNAVAILABLE
    return KIND_INTERNAL


class ProviderRangeError(ProviderError):
    """The requested window is outside this provider's supported depth."""

    retryable = False


class ProviderSymbolError(ProviderError):
    """The provider does not know this symbol."""

    retryable = False


# ── value types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class HistoryRequest:
    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    extended_hours: bool = False

    def key(self) -> tuple:
        return (self.symbol.upper(), self.timeframe, self.extended_hours)


@dataclass(slots=True)
class Snapshot:
    """A provider's cheap "what is this symbol doing right now" answer."""

    symbol: str
    last: float
    previous_close: float | None = None
    currency: str = "USD"
    exchange: str = ""
    market_state: str = ""
    extra: dict = field(default_factory=dict)


# ── the adapter base ─────────────────────────────────────────────────────────

class HistoryAdapter(abc.ABC):
    """One market-data source, normalized.

    Subclasses set `provider_name`, `provider_priority`, `capabilities`, and
    implement `_fetch_native` (+ optionally `_probe`, `_fetch_snapshot_impl`).
    Instances must be thread-safe: the live app fetches from a ThreadPool and
    from FastAPI's threadpool concurrently.
    """

    provider_name: str = "abstract"
    #: Lower runs first. Ties are broken by registration order.
    provider_priority: int = 100
    capabilities: ProviderCapabilities

    #: Minimum seconds between outbound requests from this adapter.
    min_request_interval: float = 0.0

    #: Seconds an outbound request may take when configuration says nothing.
    #: Per-provider because the endpoints genuinely differ in speed.
    default_timeout: float = 10.0

    # ── credentials + budget (V0.5.4) ────────────────────────────────────────

    #: Does this provider need an API key? A keyed provider with no key
    #: configured disables ITSELF at construction rather than failing at the
    #: first request — the app must start and work with zero keys present.
    requires_api_key: bool = False
    #: Environment variables consulted for the key, in order, before falling
    #: back to `api_key` in the config file. Convention: `<PROVIDER>_API_KEY`.
    api_key_env_vars: tuple[str, ...] = ()
    #: The provider's published free-tier limits. Enforced BEFORE the request,
    #: because reacting to a 429 is too late for a 25-per-day allowance.
    rate_limit: RateLimitPolicy = UNMETERED
    #: Human-readable pointer for the "no API key" message, so a user is told
    #: where to get one instead of just that they lack it.
    signup_url: str = ""

    #: Does this provider's FREE tier actually serve historical candles?
    #:
    #: A measured fact, not a guess — the same standard `capabilities.py` is
    #: held to. Finnhub sets this False because it moved `/stock/candle` behind
    #: a paid plan (measured 2026-07-27; see `docs/MARKET_DATA.md` §41), so
    #: recommending it to a user who needs a second data source would send them
    #: to sign up for something that cannot do the job.
    #:
    #: This affects ADVICE only. It never gates a request: a user on a paid
    #: plan has a Finnhub key that works perfectly, and the app finds that out
    #: the ordinary way — by asking.
    free_tier_serves_history: bool = True

    #: Can this provider be BELIEVED when it says "no bars in this window"?
    #:
    #: True only for adapters that raise on every failure path, so an empty
    #: frame can mean nothing except "this window genuinely holds no bars" (a
    #: weekend, a holiday, a pre-listing range). The service stops the provider
    #: chain on such an answer: consulting a provider that CANNOT distinguish
    #: the two — yfinance returns an empty frame for both — can only turn a
    #: correct "empty" into a spurious "failed", and repeatedly trips that
    #: provider's circuit breaker every weekend. Conservative default: False.
    reports_empty_reliably: bool = False

    def __init__(self, config: ProviderConfig | None = None, *,
                 quota_store=None, environ: dict | None = None) -> None:
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.config = config or ProviderConfig()
        #: The effective API key, or None. Resolved once at construction:
        #: re-reading the environment per request would let a provider silently
        #: change behaviour mid-session.
        self.api_key = (
            self.config.resolve_api_key(*self.api_key_env_vars, environ=environ)
            if self.requires_api_key else None)
        #: The per-provider budget. Metered providers get a real tracker;
        #: keyless ones get an unmetered tracker rather than None, so there is
        #: one code path everywhere instead of two.
        self.quota = QuotaTracker(
            self.provider_name,
            self.config.rate_limit_policy(self.rate_limit),
            store=quota_store)
        #: The single owner of this provider's operational state — counters,
        #: latency, rate-limit window, circuit breaker, budget and ranking
        #: score. The registry and the diagnostics endpoint read it; nothing
        #: else writes to it except the `_record_*` methods below and the
        #: service's explicit validation verdict.
        self.monitor = ProviderHealthMonitor(
            self.provider_name,
            priority=(self.config.priority if self.config.priority is not None
                      else self.provider_priority),
            breaker=self.config.breaker_policy(),
            quota=self.quota,
            disabled_reason=self._disabled_reason())
        self.monitor.requires_api_key = self.requires_api_key
        self.monitor.api_key_configured = bool(self.api_key)
        if self.config.priority is not None:
            self.provider_priority = self.config.priority
        if self.config.min_request_interval is not None:
            self.min_request_interval = self.config.min_request_interval
        #: Seconds an outbound request may take. Adapters that own their
        #: transport read this; see `docs/MARKET_DATA.md` §6.
        self.timeout = (self.config.timeout if self.config.timeout is not None
                        else self.default_timeout)

    def can_spend_request(self) -> tuple[bool, str]:
        """May this adapter be sent a request at all, and if not, why?

        **Every code path that spends an upstream request must consult this.**
        There are four: the service (via `registry.candidates`), `fetch_history`
        itself, `replay.compare_providers`, and `discovery.discover`. Two of
        them originally checked only the circuit breaker, and so fired real
        requests at providers with no API key — collecting 401s, marking them
        auth-failed (which is sticky), and poisoning the health of providers
        the user had never configured. Centralising the question is what stops
        the fifth such path repeating it.

        Deliberately narrower than `monitor.available()`: this permits a
        provider whose breaker is open, because a debugging tool may legitimately
        want the answer behind a temporary benching. It refuses only what is
        *permanently* unusable (no key, disabled) or *budgeted out* — neither of
        which a request could ever satisfy.
        """
        if self.monitor.disabled_reason:
            return False, self.monitor.status()[1]
        if self.monitor.auth_failed:
            return False, "the API key was rejected"
        if self.monitor.entitlement_failed:
            # Refused for the same reason as a rejected key: no number of
            # attempts turns a free plan into a paid one, and every attempt is
            # a real request spent proving that again.
            return False, ("the API key is valid but the plan does not include "
                           "this data")
        allowed, refusal = self.quota.allow()
        if not allowed:
            return False, refusal
        return True, ""

    def _disabled_reason(self) -> str | None:
        """Why this provider cannot be used at all, or None.

        Resolved at construction so a missing key is a *quiet, explained*
        absence from the chain rather than a crash or a stream of failed
        requests. The application must start and serve charts with zero API
        keys present — that is the shipped default.
        """
        if not self.config.enabled:
            return STATUS_DISABLED
        if self.requires_api_key and not self.api_key:
            return STATUS_NO_API_KEY
        return None

    # ── live reconfiguration (V0.5.7) ────────────────────────────────────────
    #
    # Before the control centre, every one of these settings could only change
    # by editing a file and restarting. They are now editable from Settings,
    # which means an adapter has to be able to change its mind about them
    # mid-session — and, more importantly, that ONE method has to recompute
    # everything the change implies. `_refresh_availability` is that method:
    # each setter mutates its own field and then delegates, so a future setter
    # cannot forget to clear `disabled_reason` and leave a re-enabled provider
    # invisible to `registry.candidates`.

    def set_api_key(self, api_key: str | None, *,
                    environ: dict | None = None) -> str | None:
        """Install (or clear) this provider's key. Returns the effective key.

        The return value is the honest answer, not the argument: an environment
        variable outranks a stored key (see `data/credentials.py`), so passing
        a key here while `FINNHUB_API_KEY` is set leaves the environment's key
        in force. Callers report the difference to the user rather than
        pretending the paste took effect.
        """
        self.config = _replace(self.config, api_key=(api_key or None))
        self.api_key = (
            self.config.resolve_api_key(*self.api_key_env_vars, environ=environ)
            if self.requires_api_key else None)
        # A new key deserves a fresh chance. The auth failure is sticky
        # precisely so a rejected key is not retried on every chart load — but
        # the thing that made it sticky has just been replaced.
        self.monitor.clear_auth_failure()
        self._refresh_availability()
        return self.api_key

    def set_enabled(self, enabled: bool) -> None:
        """Switch this provider on or off without a restart.

        A disabled provider stays CONSTRUCTED and stays in the registry — it
        simply reports `disabled` and is never selected. That is what lets the
        settings page list it, explain it, and offer to turn it back on;
        dropping it from the registry (which is what `enabled: false` used to
        do at construction) would make "off" indistinguishable from "does not
        exist in this build".
        """
        self.config = _replace(self.config, enabled=bool(enabled))
        self._refresh_availability()

    def set_priority(self, priority: int) -> None:
        """Change the provider's static ordering anchor.

        Written to the monitor as well as to the adapter because the monitor is
        what `rank()` is computed from — updating one and not the other would
        leave the dashboard's "configured priority" disagreeing with the order
        requests are actually tried in.
        """
        priority = int(priority)
        self.config = _replace(self.config, priority=priority)
        self.provider_priority = priority
        self.monitor.priority = priority

    def _refresh_availability(self) -> None:
        """Recompute what the monitor believes about usability. Idempotent."""
        self.monitor.disabled_reason = self._disabled_reason()
        self.monitor.api_key_configured = bool(self.api_key)

    # ── declarative queries (no I/O) ─────────────────────────────────────────

    def supports_symbol(self, symbol: str) -> bool:
        return self.capabilities.supports_symbol(symbol)

    def supports_interval(self, timeframe: Timeframe) -> bool:
        return self.capabilities.supports_interval(timeframe)

    def supports_extended_hours(self, timeframe: Timeframe) -> bool:
        return self.capabilities.supports_extended_hours(timeframe)

    def earliest(self, timeframe: Timeframe, now: datetime) -> datetime | None:
        return self.capabilities.earliest(timeframe, now)

    # ── health / rate limiting ───────────────────────────────────────────────

    @property
    def last_error(self) -> str | None:
        return self.monitor.last_error

    @property
    def last_success(self) -> float | None:
        return self.monitor.last_success

    @property
    def data_quality_score(self) -> float:
        return self.monitor.data_quality_score

    @property
    def rate_limit_state(self) -> dict:
        now = _time.monotonic()
        until = self.monitor.rate_limited_until
        return {
            "limited": bool(until and until > now),
            "seconds_remaining": round(until - now, 1) if until and until > now else 0.0,
            "min_request_interval": self.min_request_interval,
            "requests_per_minute": self.capabilities.requests_per_minute,
        }

    def health(self) -> ProviderHealth:
        """Current state in the pre-V0.5.3 shape. Cheap, non-blocking, no I/O.

        `self.monitor` is the live object; this is a read-only view of it kept
        for the callers (and tests) written against the old dataclass.
        """
        return ProviderHealth.of(self.monitor)

    def connect(self) -> bool:
        """Establish/verify usability. Safe to call repeatedly; returns True on
        success and records the failure (rather than raising) otherwise."""
        try:
            self._probe()
        except Exception as exc:  # noqa: BLE001 — a probe failure is a health fact
            self._record_failure(exc)
            return False
        self._record_success(latency_ms=0.0, quality=None)
        return True

    def _probe(self) -> None:
        """Subclass hook: a minimal request proving the provider answers."""

    #: Can this provider check its credentials WITHOUT asking for history?
    #:
    #: False by default, meaning "the history endpoint is the only test there
    #: is". A provider sets this True when it exposes a cheap endpoint that its
    #: cheapest plan definitely includes — which is the only way to tell
    #: "your key is wrong" apart from "your key is right and your plan is too
    #: small". Finnhub is the reason this exists: its free tier authenticates
    #: perfectly and then refuses `/stock/candle` with a 403.
    can_verify_credentials: bool = False

    def verify_credentials(self) -> tuple[bool, str]:
        """(is this key accepted, detail). Costs one request.

        Deliberately answers a NARROWER question than `connect()`: not "can
        this provider serve me data" but "does this provider recognise my
        key". Those are the same question for most providers and emphatically
        not for a metered one with tiered endpoints, and only the narrow
        question can produce the sentence a user actually needs — *"your key is
        valid; your plan does not include historical prices."*

        The default implementation has no cheaper endpoint to ask, so it says
        so rather than guessing. Callers check `can_verify_credentials` first.
        """
        return False, "this provider has no separate credential check"

    # ── the main entry point ─────────────────────────────────────────────────

    def fetch_history(self, request: HistoryRequest, *,
                      now: datetime | None = None) -> pd.DataFrame:
        """Canonical-shape candles for `request`, or raise a `ProviderError`.

        Applies, in order: capability checks (so an impossible request costs no
        network), window clamping to the provider's real depth, throttling, the
        subclass fetch, canonical normalization, and resampling for timeframes
        the provider has no native interval for.
        """
        tf = request.timeframe
        symbol = request.symbol.upper()
        if not self.supports_symbol(symbol):
            raise ProviderSymbolError(
                f"{self.provider_name} does not serve {symbol}")
        spec = self.capabilities.spec(tf)
        if spec is None:
            raise ProviderRangeError(
                f"{self.provider_name} has no {tf} interval")
        now = now or datetime.now(tz=request.end.tzinfo or None)
        window = self.capabilities.window_for(tf, request.start, request.end, now)
        if window is None:
            raise ProviderRangeError(
                f"{self.provider_name} cannot serve {tf} history before "
                f"{self.capabilities.earliest(tf, now)}")
        start, end = window
        prepost = request.extended_hours and self.supports_extended_hours(tf)

        # Budget is checked here, at the last moment before the network, as
        # well as by the registry when it builds the candidate list. The
        # registry's check keeps an exhausted provider out of the running; this
        # one closes the race between that check and this request, and covers
        # callers that reach an adapter directly (replay, benchmark, probes).
        allowed, refusal = self.quota.allow()
        if not allowed:
            raise ProviderQuotaExceeded(
                f"{self.provider_name}: {refusal}",
                retry_after=_QUOTA_BACKOFF)

        self._throttle()
        # Counted BEFORE the call, not after: an upstream request consumes
        # quota whether or not it succeeds, and a request that raises must not
        # be free. Counting after would also let a burst of concurrent calls
        # each see a stale count and collectively overrun the limit.
        self.quota.record()
        t0 = _time.monotonic()
        try:
            # QA-mode fault injection sits INSIDE the try, in the exact place a
            # transport failure occurs, so a simulated outage is recorded,
            # classified, ranked and failed-over identically to a real one.
            # `FAULTS.active` is False in every normal install (the config flag
            # defaults off and the endpoints that arm a fault 404 without it),
            # so this costs one attribute read per request. See `data/faults.py`.
            injected = FAULTS.check(self.provider_name)
            raw = (injected if injected is not None
                   else self._fetch_native(symbol, spec, start, end, prepost))
        except ProviderError as exc:
            self._record_failure(exc, (_time.monotonic() - t0) * 1000.0)
            raise
        except Exception as exc:  # noqa: BLE001 — unknown transport faults
            self._record_failure(exc, (_time.monotonic() - t0) * 1000.0)
            raise ProviderUnavailable(
                f"{self.provider_name} {symbol} {tf}: {exc}") from exc
        latency_ms = (_time.monotonic() - t0) * 1000.0

        df = validate_candles(raw, context=f"{self.provider_name} {symbol} {tf}")
        if not df.empty and tf.minutes >= Timeframe.D1.minutes:
            # ONE convention for daily+ bars, enforced at the single boundary
            # every adapter's frame passes through rather than trusted to six
            # of them independently — which is how Yahoo (session open, 13:30
            # UTC) and yfinance (exchange midnight, 04:00 UTC) came to write
            # two cache rows for every trading day, and why 1D refused to draw
            # for any symbol. See `base.session_index`.
            df.index = session_index(df.index)
            df = df[~df.index.duplicated(keep="last")].sort_index()
        if df.empty:
            # An adapter that returns nothing without raising is either looking
            # at a genuinely empty window (a market holiday) or has silently
            # failed. We cannot tell them apart here, so we report it as a
            # normal empty result and let the service decide — but we do NOT
            # count it as a success for health purposes.
            self._record_empty(latency_ms)
            return df
        if spec.resample:
            df = _resample(df, spec.resample)
        self._record_success(latency_ms, quality=None, bars=len(df))
        return df

    def fetch_latest(self, symbol: str, timeframe: Timeframe, *,
                     bars: int = 2, now: datetime | None = None) -> pd.DataFrame:
        """The newest `bars` candles — the cheapest way to refresh a chart's
        forming bar. Implemented on top of `fetch_history` so an adapter gets it
        for free; adapters with a cheaper endpoint may override."""
        import datetime as _dt
        now = now or _dt.datetime.now(_dt.timezone.utc)
        # Reach back far enough that weekends/holidays can't produce an empty
        # window: bars * interval, floored at three calendar days.
        span = _dt.timedelta(minutes=timeframe.minutes * max(bars, 2) * 3)
        span = max(span, _dt.timedelta(days=3))
        df = self.fetch_history(
            HistoryRequest(symbol, timeframe, now - span, now), now=now)
        return df.tail(bars)

    def fetch_snapshot(self, symbol: str) -> Snapshot:
        """A light quote-like snapshot. Adapters without one raise
        `ProviderUnavailable`, and the service falls through."""
        return self._fetch_snapshot_impl(symbol)

    def _fetch_snapshot_impl(self, symbol: str) -> Snapshot:
        raise ProviderUnavailable(
            f"{self.provider_name} provides no snapshot endpoint")

    # ── subclass contract ────────────────────────────────────────────────────

    @abc.abstractmethod
    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        """Fetch `spec.native` bars for `[start, end)`.

        Return anything `validate_candles` can normalize (an OHLCV frame with a
        datetime index). Raise a `ProviderError` subclass on failure — never
        return an empty frame to signal an error.
        """

    # ── internal bookkeeping ─────────────────────────────────────────────────

    def _throttle(self) -> None:
        if self.min_request_interval <= 0:
            return
        with self._lock:
            wait = self.min_request_interval - (_time.monotonic() - self._last_request)
            if wait > 0:
                _time.sleep(wait)
            self._last_request = _time.monotonic()

    def _record_success(self, latency_ms: float, quality: float | None,
                        bars: int = 0) -> None:
        self.monitor.record_success(latency_ms, bars=bars)
        if quality is not None:
            self.monitor.observe_quality(quality)

    def _record_empty(self, latency_ms: float) -> None:
        self.monitor.record_empty(latency_ms)

    def _record_failure(self, exc: BaseException, latency_ms: float = 0.0) -> None:
        """Classify and record. Which kinds count against health is decided in
        exactly one place — `health.COUNTS_AGAINST_HEALTH` — rather than being
        re-derived here as it used to be.

        Two failures also carry state beyond the counters, because they change
        whether the provider is usable at all rather than merely how healthy it
        looks:

        - an auth failure marks the provider auth-failed, taking it out of
          rotation until the key changes;
        - a quota error is authoritative over our local count, which can drift
          low when the same key is used by another install or process.
        """
        kind = failure_kind(exc)
        if kind == KIND_AUTH:
            self.monitor.note_auth_failure()
        elif kind == KIND_ENTITLEMENT:
            self.monitor.note_entitlement_failure()
        elif kind == KIND_QUOTA:
            self.quota.exhaust_day()
        self.monitor.record_failure(
            kind, f"{type(exc).__name__}: {exc}", latency_ms,
            retry_after=(exc.retry_after
                         if isinstance(exc, ProviderRateLimited) else None))

    def observe_quality(self, score: float) -> None:
        """Feed a validated frame's quality score back into this provider's
        rolling average (called by the service after validation)."""
        self.monitor.observe_quality(score)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<{type(self).__name__} {self.provider_name} p={self.provider_priority}>"


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate finer native bars up to an interval the provider lacks."""
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    return validate_candles(out)


__all__ = [
    "HistoryAdapter", "HistoryRequest", "ProviderHealth", "Snapshot",
    "ProviderError", "ProviderUnavailable", "ProviderRateLimited",
    "ProviderRangeError", "ProviderSymbolError", "ProviderTimeout",
    "ProviderAuthError", "ProviderEntitlementError", "ProviderQuotaExceeded",
    "failure_kind", "is_timeout", "timeout_or_unavailable",
]
