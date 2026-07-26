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
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.base import validate_candles
from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities

log = get_logger("data")


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
class ProviderHealth:
    """A provider's live operational state, as the registry and the diagnostics
    endpoint see it."""

    name: str
    available: bool = True
    last_success: float | None = None       # monotonic seconds
    last_error: str | None = None
    last_error_at: float | None = None
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    rate_limited_until: float | None = None
    data_quality_score: float = 100.0
    avg_latency_ms: float = 0.0

    def as_dict(self, now: float | None = None) -> dict:
        now = _time.monotonic() if now is None else now
        return {
            "name": self.name,
            "available": self.available,
            "seconds_since_success": (round(now - self.last_success, 1)
                                      if self.last_success else None),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "failure_rate": (round(self.total_failures / self.total_requests, 3)
                             if self.total_requests else 0.0),
            "rate_limited_for": (round(self.rate_limited_until - now, 1)
                                 if self.rate_limited_until and
                                 self.rate_limited_until > now else None),
            "data_quality_score": round(self.data_quality_score, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._health = ProviderHealth(name=self.provider_name)
        self._quality_samples = 0

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
        return self._health.last_error

    @property
    def last_success(self) -> float | None:
        return self._health.last_success

    @property
    def data_quality_score(self) -> float:
        return self._health.data_quality_score

    @property
    def rate_limit_state(self) -> dict:
        now = _time.monotonic()
        until = self._health.rate_limited_until
        return {
            "limited": bool(until and until > now),
            "seconds_remaining": round(until - now, 1) if until and until > now else 0.0,
            "min_request_interval": self.min_request_interval,
            "requests_per_minute": self.capabilities.requests_per_minute,
        }

    def health(self) -> ProviderHealth:
        """Current state. Cheap and non-blocking — never performs I/O."""
        with self._lock:
            h = self._health
            now = _time.monotonic()
            if h.rate_limited_until and h.rate_limited_until <= now:
                h.rate_limited_until = None
            return h

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

        self._throttle()
        t0 = _time.monotonic()
        try:
            raw = self._fetch_native(symbol, spec, start, end, prepost)
        except ProviderError as exc:
            self._record_failure(exc)
            raise
        except Exception as exc:  # noqa: BLE001 — unknown transport faults
            self._record_failure(exc)
            raise ProviderUnavailable(
                f"{self.provider_name} {symbol} {tf}: {exc}") from exc
        latency_ms = (_time.monotonic() - t0) * 1000.0

        df = validate_candles(raw, context=f"{self.provider_name} {symbol} {tf}")
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
        self._record_success(latency_ms, quality=None)
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

    def _record_success(self, latency_ms: float, quality: float | None) -> None:
        with self._lock:
            h = self._health
            h.available = True
            h.last_success = _time.monotonic()
            h.consecutive_failures = 0
            h.total_requests += 1
            h.rate_limited_until = None
            h.avg_latency_ms = _ewma(h.avg_latency_ms, latency_ms, h.total_requests)
            if quality is not None:
                self._quality_samples += 1
                h.data_quality_score = _ewma(h.data_quality_score, quality,
                                             self._quality_samples)

    def _record_empty(self, latency_ms: float) -> None:
        with self._lock:
            h = self._health
            h.total_requests += 1
            h.avg_latency_ms = _ewma(h.avg_latency_ms, latency_ms, h.total_requests)

    def _record_failure(self, exc: BaseException) -> None:
        with self._lock:
            h = self._health
            h.total_requests += 1
            h.total_failures += 1
            h.consecutive_failures += 1
            h.last_error = f"{type(exc).__name__}: {exc}"[:300]
            h.last_error_at = _time.monotonic()
            if isinstance(exc, ProviderRateLimited):
                h.rate_limited_until = _time.monotonic() + exc.retry_after
            # A range/symbol error says nothing about the provider's health —
            # it is a correct answer to an impossible question.
            if isinstance(exc, (ProviderRangeError, ProviderSymbolError)):
                h.consecutive_failures = max(0, h.consecutive_failures - 1)
                h.total_failures -= 1

    def observe_quality(self, score: float) -> None:
        """Feed a validated frame's quality score back into this provider's
        rolling average (called by the service after validation)."""
        with self._lock:
            self._quality_samples += 1
            self._health.data_quality_score = _ewma(
                self._health.data_quality_score, score, self._quality_samples)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<{type(self).__name__} {self.provider_name} p={self.provider_priority}>"


def _ewma(current: float, sample: float, n: int) -> float:
    """Exponentially-weighted average that behaves like a plain mean for the
    first few samples (so one slow first request doesn't dominate forever)."""
    alpha = max(0.1, 1.0 / max(n, 1))
    return current * (1 - alpha) + sample * alpha


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
    "ProviderRangeError", "ProviderSymbolError",
]
