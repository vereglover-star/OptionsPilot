"""MarketDataService — the one way to get price history in this application.

Everything above this line asks a single question:

    service.get_history(symbol, timeframe, start, end) -> HistoryResult

and everything below it — which provider answered, whether it came off disk,
how many retries it took, what validation threw away — is implementation
detail. Charts, the scan engine, the backtester and the coach all read through
the same call, so they cannot drift apart in what they consider valid data.

## The tier chain

Each request walks a fixed ladder and stops at the first tier that can answer:

    memo (hot, TTL'd, request-deduplicated)
      ↓
    disk cache (warm start / restart within the freshness window)
      ↓
    provider 1 -> provider 2 -> provider 3   (retry, then fail over)
      ↓
    half-open circuit-breaker probes         (self-heal a total outage)
      ↓
    disk cache, knowingly stale              (display surfaces only)
      ↓
    an explicit, explained failure

The ladder never silently skips a rung and never returns an unexplained empty
frame: `HistoryResult.outcome` names the tier that answered, and every result
carries the diagnostics trace id that recorded the walk.

## The distinction that fixes the historical bug class

"No data" is not one condition, it is four, and conflating them is what made
charts flaky:

  - `exhausted` — the window is older than ANY provider can serve. Retrying is
    pointless; this is the true start of history and the chart says so.
  - `empty` — the providers answered, with zero bars, because the window is a
    weekend or a holiday. Legitimate.
  - `stale` — nothing live could be reached, but we hold older bars on disk.
  - `failed` — nothing could answer and there is nothing on disk. The only
    case that deserves an error state in the UI.

The old code produced one empty DataFrame for all four and let the frontend
guess, which is why a scroll into last spring's 5-minute bars retried forever.

## Trading-path safety

`get_history` is strict: it never returns stale data unless the caller passes
`allow_stale=True`, which only display surfaces do. The engine's fail-closed
rule — no data means skip the symbol — is preserved exactly.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data import diagnostics as diag
from optionspilot.data.adapter import (
    HistoryAdapter, HistoryRequest, ProviderError, ProviderRangeError,
    ProviderRateLimited, ProviderSymbolError,
)
from optionspilot.data.base import validate_candles
from optionspilot.data.cache import CandleCache
from optionspilot.data.config import MarketDataConfig
from optionspilot.data.diagnostics import Diagnostics, RequestTrace
from optionspilot.data.health import KIND_INTERNAL, KIND_VALIDATION
from optionspilot.data.quality import (
    FUTURE_TOLERANCE, HistoryReport, disagreement, validate_history,
)
from optionspilot.data.registry import ProviderRegistry, default_registry

log = get_logger("data")

# How long a fetched frame stays fresh, per timeframe (seconds). Sized to the
# bar interval so the Charts tab's adaptive live poll (~7s for minute frames
# while the market is open) actually receives a fresh frame each time instead
# of the same cached one — the forming bar then advances every few seconds
# instead of in 30s chunks. Higher timeframes cannot grow new bars quickly, so
# they stay looser to bound upstream request volume. The scan loop polls far
# slower than any of these, so lowering them does not increase its fetch rate;
# only the actively-viewed chart refetches this often.
CANDLE_TTL: dict[Timeframe, float] = {
    Timeframe.M1: 6.0,
    Timeframe.M2: 6.0,
    Timeframe.M3: 6.0,
    Timeframe.M5: 10.0,
    Timeframe.M10: 15.0,
    Timeframe.M15: 20.0,
    Timeframe.M30: 30.0,
    Timeframe.H1: 45.0,
    Timeframe.H2: 90.0,
    Timeframe.H4: 120.0,
    Timeframe.D1: 60.0,
    Timeframe.W1: 600.0,
    Timeframe.MN1: 600.0,
}
DEFAULT_CANDLE_TTL = 60.0

# A failed/empty result is memoized only briefly. Caching it for the full TTL
# poisons every retry for up to a minute — the root cause of the app opening
# with blank charts (root-caused 2026-07-17). Long enough to stop a tight
# retry loop from hammering upstream, short enough to recover promptly.
EMPTY_CANDLE_TTL = 3.0

# Upper bound on the in-memory memo. One entry per distinct
# (symbol, timeframe, extended_hours); generous enough that ordinary use never
# evicts, but caps accumulation over a long session browsing many symbols.
MEM_CACHE_MAX = 400

# Retries per provider for RETRYABLE failures, and the backoff between them.
# Deliberately small: a second provider is a better answer than a third attempt
# at a sick one, and the user is watching a chart.
MAX_ATTEMPTS_PER_PROVIDER = 2
RETRY_BACKOFF = 0.4

# Median relative close difference above which two providers are considered to
# genuinely disagree (rather than to differ by rounding). 0.5% is well above
# float noise and well below a dividend adjustment step.
DISAGREEMENT_THRESHOLD = 0.005


@dataclass(slots=True)
class HistoryResult:
    """What a history request produced, and everything needed to explain it."""

    frame: pd.DataFrame
    outcome: str = diag.OUTCOME_FAILED
    provider: str | None = None
    #: True when the bars are knowingly older than the caller asked for.
    stale: bool = False
    #: True when the requested window predates anything any provider can serve.
    exhausted: bool = False
    #: The oldest bar any provider could serve for this interval right now.
    earliest_available: datetime | None = None
    report: HistoryReport | None = None
    trace_id: int | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.frame.empty

    @property
    def bars(self) -> int:
        return len(self.frame)

    def as_meta(self) -> dict:
        """The subset the API sends to the frontend state machine."""
        return {
            "outcome": self.outcome,
            "provider": self.provider,
            "stale": self.stale,
            "exhausted": self.exhausted,
            "earliest_available": (self.earliest_available.isoformat()
                                   if self.earliest_available else None),
            "quality": round(self.report.score, 1) if self.report else None,
            "trace_id": self.trace_id,
            "message": self.message,
        }


@dataclass(slots=True)
class _Entry:
    result: HistoryResult
    fetched_at: float
    start: datetime


class MarketDataService:
    """Provider-agnostic history acquisition. Thread-safe."""

    def __init__(self, registry: ProviderRegistry | None = None, *,
                 cache: CandleCache | None = None,
                 cache_db: str | Path | None = None,
                 diagnostics: Diagnostics | None = None,
                 config: MarketDataConfig | None = None,
                 clock=None):
        # A registry carries its own config; a bare service without one falls
        # back to the defaults, so every construction path has exactly one
        # answer to "which config is in force".
        self.config = config or (registry.config if registry is not None
                                 else MarketDataConfig())
        self.registry = (registry if registry is not None
                         else default_registry(config=self.config))
        self.cache = cache if cache is not None else (
            CandleCache(cache_db, config=self.config.cache)
            if cache_db is not None and self.config.cache.enabled else None)
        self.diagnostics = diagnostics or Diagnostics(
            structured_logging=self.config.structured_logging)
        # Injectable so tests drive time deterministically instead of sleeping.
        self._now = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._mem: dict[tuple, _Entry] = {}
        self._inflight: dict[tuple, threading.Event] = {}
        self._memo_max = max(1, self.config.memo_max_entries)
        #: How many times a cached frame has been discarded as unusable and
        #: re-downloaded. Surfaced in `health()["cache"]` — a number that keeps
        #: climbing means something upstream is writing bars that cannot be
        #: served, which is a defect to chase rather than a cost to absorb.
        self._quarantines = 0

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()

    # ── public API ───────────────────────────────────────────────────────────

    def get_history(self, symbol: str, timeframe: Timeframe,
                    start: datetime, end: datetime, *,
                    extended_hours: bool = False,
                    allow_stale: bool = False) -> HistoryResult:
        """Candles for `[start, end)`. See the module docstring for the ladder.

        `allow_stale=True` is a DISPLAY-only opt-in. The trading path leaves it
        False so that "no live data" stays "skip this symbol".
        """
        symbol = symbol.upper()
        request = HistoryRequest(symbol, timeframe, start, end, extended_hours)
        key = request.key()
        ttl = CANDLE_TTL.get(timeframe, DEFAULT_CANDLE_TTL)

        # A history-paging request (the user scrolling into older bars) asks for
        # a window that ENDS in the past. It must not share the memo with the
        # live window: the memo is keyed by (symbol, timeframe, session) only,
        # so an older window would overwrite the live frame under the same key,
        # and the next live load would then be answered from it — sliced down to
        # whatever few bars happened to overlap. That is exactly the "chart
        # suddenly shows one candle" / "scrolled-in history randomly vanishes"
        # failure, reproduced from `scripts/chart_check.py`.
        #
        # Keying the memo by the window instead is not an option: the live poll
        # moves `end` forward every few seconds, so every poll would miss. So
        # history-paging requests simply bypass the memo — they are driven by a
        # user scroll rather than a timer, and the disk cache already absorbs
        # repeats of the same older window.
        live_window = self._is_live_window(request)

        def valid(entry: _Entry) -> bool:
            if entry.result.frame.empty:
                return _fresh(entry.fetched_at, EMPTY_CANDLE_TTL)
            # A memo only satisfies a request that reaches no further back than
            # the memoized frame does, otherwise a scroll into history would be
            # answered with the window already on screen.
            return _fresh(entry.fetched_at, ttl) and entry.start <= start

        cached = self._memo_lookup(key, valid) if live_window else None
        if cached is not None:
            trace = self.diagnostics.start(symbol, timeframe, start, end,
                                           extended_hours)
            trace.memo_hit = True
            result = _sliced(cached.result, start)
            # The RESULT's outcome must agree with the trace's: a memo hit is
            # its own tier, not a second copy of whichever tier originally
            # produced the frame. Reporting the original outcome would make
            # `live_rate` in the diagnostics summary meaningless and would tell
            # the UI a cached frame had just come off the wire.
            result = _retiered(result, diag.OUTCOME_MEMO)
            trace.validation = (result.report.as_dict() if result.report else None)
            trace.earliest_available = result.earliest_available
            self.diagnostics.record(trace.finish(
                diag.OUTCOME_MEMO, provider=result.provider, bars=result.bars))
            return _with_trace(result, trace.id)

        if not live_window:
            # Straight to the ladder: no memo read, no memo write, no in-flight
            # slot (a scroll-back is not a stampede risk the way a poll is).
            return self._walk_tiers(request, ttl, allow_stale)
        # Not memoized (or stale): become the single fetcher for this key.
        return self._fetch_once(key, request, ttl, allow_stale, start)

    def _is_live_window(self, request: HistoryRequest) -> bool:
        """Does this request reach the present, or is it a scroll into history?

        One bar of slack: a caller that computes `end` a moment before a bar
        boundary is still asking for the live window, not for history.
        """
        now = self._now()
        slack = timedelta(minutes=max(request.timeframe.minutes, 1))
        return request.end >= now - slack

    def get_latest(self, symbol: str, timeframe: Timeframe,
                   bars: int = 2) -> HistoryResult:
        """Newest few bars — the cheap refresh path for a live chart."""
        now = self._now()
        span = max(timedelta(minutes=timeframe.minutes * max(bars, 2) * 3),
                   timedelta(days=3))
        return self.get_history(symbol, timeframe, now - span, now)

    def health(self) -> dict:
        """Everything an operator (or a bug report) needs in one object.

        This is the diagnostics dashboard's single data source and the payload
        the JSON export writes verbatim, so anything the page shows is here and
        nothing it shows is computed twice.
        """
        return {
            "providers": self.registry.health_report(),
            "ranking": self.registry.ranking(),
            "cache": ({**self.cache.stats(), "quarantines": self._quarantines}
                      if self.cache else None),
            "requests": self.diagnostics.summary(),
            "memo": {
                "entries": len(self._mem),
                "max_entries": self._memo_max,
            },
            # Kept flat as well: several callers and one test read it directly.
            "memo_entries": len(self._mem),
            "config": self.config.as_dict(),
        }

    def invalidate(self, symbol: str | None = None) -> int:
        """Drop memoized frames (all, or for one symbol). Returns the count."""
        with self._lock:
            keys = [k for k in self._mem
                    if symbol is None or k[0] == symbol.upper()]
            for k in keys:
                del self._mem[k]
        return len(keys)

    # ── the ladder ───────────────────────────────────────────────────────────

    def _fetch_once(self, key: tuple, request: HistoryRequest, ttl: float,
                    allow_stale: bool, requested_start: datetime) -> HistoryResult:
        """Run the tier ladder for `key`, with concurrent callers deduplicated.

        One caller fetches; the rest wait for its result rather than stampeding
        the network. A fetcher that dies without publishing lets the next waiter
        take over, so a crash cannot wedge a key permanently.
        """
        while True:
            with self._lock:
                entry = self._mem.get(key)
                if entry is not None and _fresh(entry.fetched_at, ttl) \
                        and entry.start <= requested_start \
                        and not entry.result.frame.empty:
                    return _retiered(_sliced(entry.result, requested_start),
                                     diag.OUTCOME_MEMO)
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break                      # we are the fetcher
            # Bounded wait: a hung fetcher must not block every other caller
            # forever — on timeout we loop and try to become the fetcher.
            event.wait(timeout=30.0)
            with self._lock:
                entry = self._mem.get(key)
            if entry is not None and not entry.result.frame.empty \
                    and entry.start <= requested_start:
                return _sliced(entry.result, requested_start)
        try:
            result = self._walk_tiers(request, ttl, allow_stale)
            self._remember(key, result, request.start)
            return result
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            event.set()

    def _walk_tiers(self, request: HistoryRequest, ttl: float,
                    allow_stale: bool) -> HistoryResult:
        symbol, tf = request.symbol, request.timeframe
        now = self._now()
        trace = self.diagnostics.start(symbol, tf, request.start, request.end,
                                       request.extended_hours)
        floor = self.registry.deepest_earliest(symbol, tf, now)
        trace.earliest_available = floor

        # ── tier: the window has not happened yet ────────────────────────────
        # The mirror image of the exhaustion guard below, and it exists for the
        # same reason: a window nothing can serve must cost no requests. Yahoo
        # answers a future window with HTTP 400 and yfinance with an empty
        # frame, which between them were enough to trip EVERY provider's
        # circuit breaker — so one absurd request took the whole chain out of
        # rotation for real charts (found in the V0.5.2 self-audit).
        if request.start >= now + FUTURE_TOLERANCE:
            return self._settle(trace, _empty(), diag.OUTCOME_EMPTY, None, floor,
                                tf=tf, now=now,
                                message="that window is in the future")

        # ── tier: the window predates every provider ─────────────────────────
        if floor is not None and request.end <= floor:
            frame = self._from_cache(request)
            if not frame.empty:
                # Same rule as the warm tier below: an unusable cached frame
                # declines and quarantines rather than failing the request. No
                # provider can serve a window this old, so the honest answer
                # after quarantine is `exhausted`, not `failed`.
                usable, report = self._validated(frame, request, now,
                                                 diag.OUTCOME_CACHE)
                if usable is not None:
                    trace.validation = report.as_dict()
                    return self._settle(
                        trace, usable, diag.OUTCOME_CACHE, "cache", floor,
                        exhausted=True, tf=tf, now=now,
                        message="older bars served from the local cache")
                self._quarantine(request, report, trace)
            return self._settle(trace, _empty(), diag.OUTCOME_EXHAUSTED, None,
                                floor, exhausted=True, tf=tf, now=now,
                                message=_exhausted_message(tf, floor))

        # ── tier: warm start from disk ───────────────────────────────────────
        warm = self._warm_from_cache(request, ttl, now)
        if warm is not None:
            # Validate HERE, inside the tier, not at settle time. Validation
            # used to run in `_settle` — i.e. *after* the ladder had already
            # committed to this tier — so a cache that failed validation became
            # `outcome=failed` with no way back to the providers, and the
            # offending rows stayed on disk to fail again on the next request
            # and on every press of Retry. That is how a real (and separately
            # fixed) daily-bar defect turned into "every symbol on 1D is stuck
            # behind a validation screen, permanently".
            #
            # A tier that cannot answer must decline, not abort the ladder.
            usable, report = self._validated(warm, request, now, diag.OUTCOME_CACHE)
            if usable is not None:
                trace.cache_hit = True
                trace.validation = report.as_dict()
                return self._settle(trace, usable, diag.OUTCOME_CACHE, "cache",
                                    floor, tf=tf, now=now)
            # Unusable: quarantine the rows so the NEXT request cannot inherit
            # the same bad frame, then fall through to the providers, which will
            # re-download and re-store the window. This is the self-healing
            # path — it is what makes a bad cache a hiccup instead of a wall.
            self._quarantine(request, report, trace)

        # ── tier: providers ──────────────────────────────────────────────────
        frame, provider, all_empty = self._try_providers(request, trace, now)
        if frame is not None and not frame.empty:
            self._store(symbol, tf, frame, provider, request.extended_hours)
            return self._settle(trace, frame, diag.OUTCOME_LIVE, provider, floor,
                                tf=tf, now=now)

        # ── tier: half-open probes (self-heal a total outage) ────────────────
        probes = self.registry.half_open_candidates()
        if probes:
            frame, provider, probe_empty = self._try_providers(
                request, trace, now, adapters=probes)
            all_empty = all_empty and probe_empty
            if frame is not None and not frame.empty:
                self._store(symbol, tf, frame, provider, request.extended_hours)
                return self._settle(trace, frame, diag.OUTCOME_LIVE, provider,
                                    floor, tf=tf, now=now)

        # ── tier: knowingly stale disk data ──────────────────────────────────
        if allow_stale and self.cache is not None and not request.extended_hours:
            stale = self._from_cache(request, newest_fallback=True)
            if not stale.empty:
                # Last resort, and it must not hand the chart a frame the
                # renderer would refuse. If even these bars are unusable there
                # is nothing left to serve, so quarantine and fall to the
                # explained-failure tier below.
                usable, report = self._validated(stale, request, now,
                                                 diag.OUTCOME_STALE)
                if usable is None:
                    self._quarantine(request, report, trace)
                    stale = _empty()
            if not stale.empty:
                return self._settle(trace, stale, diag.OUTCOME_STALE, "cache",
                                    floor, stale=True, tf=tf, now=now,
                                    # These bars are deliberately older than the
                                    # request — see `_settle`'s `trim` note.
                                    trim=False,
                                    message="live data unavailable — showing the "
                                            "last bars saved locally")

        # ── tier: nothing left ───────────────────────────────────────────────
        if all_empty:
            # Every provider answered, and every answer was "no bars here".
            # That is a legitimate result (a holiday, or a pre-listing window),
            # not a failure, and it must not raise an error state in the UI.
            return self._settle(trace, _empty(), diag.OUTCOME_EMPTY, None, floor,
                                tf=tf, now=now,
                                message="no bars exist for this window")
        return self._settle(trace, _empty(), diag.OUTCOME_FAILED, None, floor,
                            tf=tf, now=now,
                            message=_failure_message(trace))

    def _try_providers(self, request: HistoryRequest, trace: RequestTrace,
                       now: datetime,
                       adapters: list[HistoryAdapter] | None = None
                       ) -> tuple[pd.DataFrame | None, str | None, bool]:
        """Walk the provider chain. Returns (frame|None, provider, all_empty).

        `all_empty` is True only when every provider consulted answered
        successfully with zero bars — the difference between "there is nothing
        here" and "nothing could tell us".
        """
        if adapters is None:
            adapters = self.registry.candidates(
                request.symbol, request.timeframe,
                extended_hours=request.extended_hours,
                end=request.end, now=now)
        if not adapters:
            trace.attempt("-", "skipped", detail="no provider serves this request")
            return None, None, False

        all_empty = True
        first = True
        for adapter in adapters:
            if not first:
                trace.fallbacks += 1
            first = False
            frame, empty = self._try_one(adapter, request, trace, now)
            all_empty = all_empty and empty
            if frame is not None and not frame.empty:
                return frame, adapter.provider_name, False
            if empty and adapter.reports_empty_reliably:
                # This provider raises on every failure path, so its empty
                # answer means the window genuinely holds no bars — a weekend,
                # a holiday, a pre-listing range. That is authoritative. Asking
                # a provider that CANNOT tell the two apart could only turn a
                # correct "empty" into a spurious "failed", and tripped that
                # provider's breaker every weekend (V0.5.2 self-audit).
                trace.attempt("-", "skipped",
                              detail=f"{adapter.provider_name} confirmed the "
                                     "window is empty; later providers not consulted")
                return None, None, True
        return None, None, all_empty

    def _try_one(self, adapter: HistoryAdapter, request: HistoryRequest,
                 trace: RequestTrace,
                 now: datetime) -> tuple[pd.DataFrame | None, bool]:
        """One provider, with retries. Returns (frame|None, answered_empty).

        Attempt count and backoff come from the provider's own configuration,
        so a slow-but-valuable feed can be given a second chance and a fast one
        can be told to fail over immediately, without editing this loop.
        """
        cfg = adapter.config
        max_attempts = max(1, cfg.max_attempts)
        for attempt in range(1, max_attempts + 1):
            t0 = _time.monotonic()
            try:
                raw = adapter.fetch_history(request, now=now)
            except ProviderError as exc:
                elapsed = (_time.monotonic() - t0) * 1000.0
                trace.attempt(adapter.provider_name, type(exc).__name__,
                              elapsed, detail=str(exc)[:200])
                # Range and symbol errors are correct answers to an impossible
                # question — they must not count against the provider's health
                # or trip its breaker (the adapter already excludes them).
                if not isinstance(exc, (ProviderRangeError, ProviderSymbolError)):
                    self.registry.record_failure(adapter)
                if not exc.retryable or attempt == max_attempts:
                    return None, False
                trace.retries += 1
                delay = (exc.retry_after if isinstance(exc, ProviderRateLimited)
                         else cfg.retry_backoff * attempt)
                if isinstance(exc, ProviderRateLimited):
                    # Never sleep out a rate limit inline — a chart is waiting.
                    # Move to the next provider; the breaker keeps this one out
                    # of rotation until the window expires.
                    return None, False
                _time.sleep(min(delay, 2.0))
                continue
            except Exception as exc:  # noqa: BLE001 — an adapter bug must not
                elapsed = (_time.monotonic() - t0) * 1000.0   # kill the request
                log.exception("provider %s raised an unexpected error",
                              adapter.provider_name)
                trace.attempt(adapter.provider_name, "InternalError", elapsed,
                              detail=str(exc)[:200])
                # The adapter never saw this — it escaped past its own handler
                # — so the count has to be made here or an adapter bug would be
                # invisible in the provider's health forever.
                adapter.monitor.record_failure(KIND_INTERNAL, str(exc)[:200])
                self.registry.record_failure(adapter)
                return None, False

            elapsed = (_time.monotonic() - t0) * 1000.0
            if raw.empty:
                trace.attempt(adapter.provider_name, "empty", elapsed,
                              detail="provider returned no bars")
                return None, True

            frame, report = validate_history(
                raw, request.timeframe, now=now,
                context=f"{adapter.provider_name} {request.symbol} {request.timeframe}")
            adapter.observe_quality(report.score)
            usable = report.usable and report.score >= cfg.min_quality_score
            if not usable:
                reason = ("; ".join(report.issues)[:200] if not report.usable
                          else f"quality {report.score:.0f} below the configured "
                               f"minimum {cfg.min_quality_score:.0f}")
                trace.attempt(adapter.provider_name, "rejected", elapsed,
                              bars=len(raw), detail=reason)
                # A provider that answers promptly with unusable bars is
                # failing, and before V0.5.3 it was recorded as a SUCCESS by
                # the adapter and never counted here — so a source serving
                # consistently-broken data never tripped its breaker and stayed
                # first in the chain indefinitely. Demote rather than record
                # anew: the adapter already counted this request.
                adapter.monitor.demote_last_success(KIND_VALIDATION, reason)
                self.registry.record_failure(adapter)
                return None, False

            trace.attempt(adapter.provider_name, "ok", elapsed, bars=len(frame))
            trace.validation = report.as_dict()
            self.registry.record_success(adapter)
            self._note_disagreement(adapter, request, frame, trace)
            return frame, False
        return None, False  # pragma: no cover — the loop always returns

    def _note_disagreement(self, adapter: HistoryAdapter, request: HistoryRequest,
                           frame: pd.DataFrame, trace: RequestTrace) -> None:
        """When a fallback provider answered, check it against what we already
        hold on disk (which a different provider wrote). Silent agreement is the
        expected case; disagreement is recorded, never acted on — deciding which
        source is 'right' is not something this layer can know."""
        if not trace.fallbacks or self.cache is None or request.extended_hours:
            return
        try:
            local = self.cache.load(request.symbol, request.timeframe,
                                    request.start, request.end)
            delta = disagreement(local, frame)
        except Exception:  # noqa: BLE001 — a diagnostic must never break a fetch
            return
        if delta is not None and delta > DISAGREEMENT_THRESHOLD:
            trace.attempt(adapter.provider_name, "disagreement",
                          detail=f"median close differs {delta:.2%} from cached bars")
            log.warning("provider disagreement for %s %s: %s differs %.2f%% "
                        "from the cached series",
                        request.symbol, request.timeframe,
                        adapter.provider_name, delta * 100)

    # ── cache helpers ────────────────────────────────────────────────────────

    def _validated(self, frame: pd.DataFrame, request: HistoryRequest,
                   now: datetime, tier: str
                   ) -> tuple[pd.DataFrame | None, HistoryReport]:
        """Semantic validation for a frame taken off disk.

        Returns `(frame, report)` when the bars are usable and
        `(None, report)` when they are not, so a tier can DECLINE rather than
        fail the whole request. Live provider frames are validated on their own
        path; this is the disk equivalent, moved out of `_settle` so that
        "these bars are unusable" and "this request failed" stop being the same
        thing.
        """
        cleaned, report = validate_history(
            frame, request.timeframe, now=now,
            context=f"{tier} {request.symbol} {request.timeframe}")
        if not report.usable or cleaned.empty:
            return None, report
        return cleaned, report

    def _quarantine(self, request: HistoryRequest, report: HistoryReport,
                    trace: RequestTrace) -> None:
        """Drop cached rows that failed validation, so the next request can't
        inherit them.

        Deliberately scoped to `(symbol, timeframe)` rather than the requested
        window: the defects that reach here — a wrong interval, duplicated
        sessions, a timestamp convention change — are properties of the whole
        series, and purging only the visible window leaves the same trap one
        scroll to the left. The bars are re-downloadable; a wall the user cannot
        get past is not recoverable at all.
        """
        if self.cache is None:
            return
        issues = "; ".join(report.issues) or "unusable"
        try:
            removed = self.cache.purge(request.symbol, request.timeframe)
        except Exception as exc:  # noqa: BLE001 — never let cleanup fail a request
            log.warning("cache quarantine failed for %s %s: %s",
                        request.symbol, request.timeframe, exc)
            return
        # Memoized frames can hold the same bad bars; drop them too or the very
        # next poll re-serves what we just purged from disk.
        self.invalidate(request.symbol)
        log.warning("quarantined %d cached %s %s bars that failed validation "
                    "(%s); refetching from providers",
                    removed, request.symbol, request.timeframe, issues)
        trace.attempt("cache", "quarantined",
                      detail=f"discarded {removed} unusable cached bars: {issues}")
        self._quarantines += 1

    def _warm_from_cache(self, request: HistoryRequest, ttl: float,
                         now: datetime) -> pd.DataFrame | None:
        """Serve from disk when the newest cached bar is still current — an app
        restart mid-session should not re-download everything.

        Extended-hours frames bypass the store entirely: it is keyed by
        symbol+timeframe with no session dimension, so its RTH bars would be a
        misleading answer to an extended-hours request.
        """
        if self.cache is None or request.extended_hours:
            return None
        try:
            cached = self.cache.load(request.symbol, request.timeframe,
                                     request.start, request.end)
        except Exception:  # noqa: BLE001 — a cache miss is not a failure
            return None
        if cached.empty:
            return None
        age = (pd.Timestamp(now) - cached.index[-1]).total_seconds()
        # The last cached bar's OPEN must be within one bar + ttl of now for the
        # frame to still be the current one.
        if age <= request.timeframe.minutes * 60 + ttl:
            return cached
        return None

    def _from_cache(self, request: HistoryRequest, *,
                    newest_fallback: bool = False) -> pd.DataFrame:
        if self.cache is None or request.extended_hours:
            return _empty()
        try:
            frame = self.cache.load(request.symbol, request.timeframe,
                                    request.start, request.end)
            if frame.empty and newest_fallback:
                frame = self.cache.load_newest(request.symbol, request.timeframe)
            return frame
        except Exception as exc:  # noqa: BLE001 — fallback is best-effort
            log.error("cache fallback failed %s %s: %s",
                      request.symbol, request.timeframe, exc)
            return _empty()

    def _store(self, symbol: str, timeframe: Timeframe, frame: pd.DataFrame,
               provider: str | None, extended_hours: bool) -> None:
        if self.cache is None or extended_hours or frame.empty:
            return
        try:
            self.cache.store(symbol, timeframe, frame, provider=provider)
        except Exception as exc:  # noqa: BLE001 — the cache is best-effort
            log.error("candle cache store failed %s %s: %s", symbol, timeframe, exc)

    # ── memo ─────────────────────────────────────────────────────────────────

    def _memo_lookup(self, key: tuple, valid) -> _Entry | None:
        with self._lock:
            entry = self._mem.get(key)
            return entry if entry is not None and valid(entry) else None

    def _remember(self, key: tuple, result: HistoryResult,
                  start: datetime) -> None:
        # Never memoize a knowingly-stale answer: the next caller should get a
        # fresh attempt at the live providers, not a copy of our fallback.
        if result.stale:
            return
        with self._lock:
            # Bounded LRU-ish store: re-insert to move the key to the newest
            # slot and evict the oldest when over the cap. Without this, `_mem`
            # grows one entry per distinct key forever.
            self._mem.pop(key, None)
            self._mem[key] = _Entry(result, _time.monotonic(), start)
            while len(self._mem) > self._memo_max:
                self._mem.pop(next(iter(self._mem)))

    # ── result assembly ──────────────────────────────────────────────────────

    def _settle(self, trace: RequestTrace, frame: pd.DataFrame, outcome: str,
                provider: str | None, floor: datetime | None, *,
                tf: Timeframe, now: datetime, stale: bool = False,
                exhausted: bool = False, message: str = "",
                trim: bool = True) -> HistoryResult:
        # Trim to the window that was actually asked for. Providers routinely
        # return a little more than requested (Yahoo rounds to session edges),
        # and the disk cache holds whatever it holds — but the SAME request must
        # always produce the SAME frame, whichever tier answered it. Without
        # this, a first load returned the provider's wider frame while every
        # later memo hit returned the sliced one, so a refresh silently changed
        # the bar count and shifted every logical index under the viewport.
        #
        # `trim=False` is used by the LAST-RESORT stale tier only. Those bars
        # are, by definition, older than the caller asked for — that is what
        # "stale" means, and the UI labels them with an as-of time. Trimming
        # them to the requested window could empty the frame completely and
        # produce `outcome=stale, bars=0`: a banner promising the last saved
        # bars with nothing behind it. The viewport-stability argument above
        # does not apply, because a stale answer is never memoized.
        if trim and not frame.empty and trace.start is not None:
            frame = frame[frame.index >= pd.Timestamp(trace.start)]
        report = None
        if not frame.empty:
            # Bars taken from the cache have not been through semantic
            # validation in this request; live bars already have. Validating
            # twice is cheap relative to a network call and guarantees nothing
            # unvalidated can reach a chart, whichever tier answered.
            if trace.validation is None:
                frame, report = validate_history(
                    frame, tf, now=now,
                    context=f"{outcome} {trace.symbol} {trace.timeframe}")
                trace.validation = report.as_dict()
                if not report.usable:
                    # A backstop, not the primary gate. Every disk tier now
                    # validates *before* committing to answer (see `_validated`
                    # / `_quarantine`), because failing here left the user
                    # permanently stuck: the ladder had already passed the
                    # providers, so there was nothing left to fall through to
                    # and the bad rows survived to fail identically on Retry.
                    # Reaching this line means a frame arrived from a path that
                    # skipped that check, so say so plainly and — since the
                    # cache is the only tier that can hand over unvalidated
                    # bars — quarantine it here too.
                    frame = _empty()
                    outcome = diag.OUTCOME_FAILED
                    message = ("the bars failed validation and were discarded: "
                               + ("; ".join(report.issues) or "unusable"))
                    log.error("validation backstop fired for %s %s (%s) — a "
                              "tier answered without validating first",
                              trace.symbol, trace.timeframe, message)
            elif trace.validation is not None:
                report = HistoryReport(
                    bars=trace.validation["bars"],
                    score=trace.validation["score"],
                    usable=trace.validation["usable"],
                    issues=list(trace.validation["issues"]),
                    counts=dict(trace.validation["counts"]),
                )
        result = HistoryResult(
            frame=frame, outcome=outcome, provider=provider, stale=stale,
            exhausted=exhausted, earliest_available=floor, report=report,
            message=message,
        )
        self.diagnostics.record(trace.finish(
            outcome, provider=provider, bars=len(frame), message=message))
        result.trace_id = trace.id
        return result


# ── helpers ──────────────────────────────────────────────────────────────────

def _fresh(fetched_at: float, ttl: float) -> bool:
    return _time.monotonic() - fetched_at < ttl


def _empty() -> pd.DataFrame:
    return validate_candles(pd.DataFrame())


def _sliced(result: HistoryResult, start: datetime) -> HistoryResult:
    """A memoized frame may reach further back than this caller asked for."""
    frame = result.frame
    if frame.empty or frame.index[0] >= start:
        return result
    return HistoryResult(
        frame=frame[frame.index >= start], outcome=result.outcome,
        provider=result.provider, stale=result.stale, exhausted=result.exhausted,
        earliest_available=result.earliest_available, report=result.report,
        trace_id=result.trace_id, message=result.message,
    )


def _retiered(result: HistoryResult, outcome: str) -> HistoryResult:
    """The same bars, relabelled with the tier that actually served them."""
    if result.outcome == outcome:
        return result
    return HistoryResult(
        frame=result.frame, outcome=outcome, provider=result.provider,
        stale=result.stale, exhausted=result.exhausted,
        earliest_available=result.earliest_available, report=result.report,
        trace_id=result.trace_id, message=result.message,
    )


def _with_trace(result: HistoryResult, trace_id: int) -> HistoryResult:
    result.trace_id = trace_id
    return result


def _exhausted_message(tf: Timeframe, floor: datetime | None) -> str:
    if floor is None:  # pragma: no cover — exhausted implies a floor exists
        return "no earlier data is available"
    return (f"{tf} history only goes back to {floor.date().isoformat()} — "
            "no provider serves this interval any earlier")


def _failure_message(trace: RequestTrace) -> str:
    details = [f"{a.provider}: {a.detail or a.outcome}" for a in trace.attempts]
    if not details:
        return "no market data provider could serve this request"
    return "market data unavailable (" + "; ".join(details[:3]) + ")"


__all__ = ["MarketDataService", "HistoryResult", "CANDLE_TTL",
           "DEFAULT_CANDLE_TTL", "EMPTY_CANDLE_TTL", "MEM_CACHE_MAX",
           "MAX_ATTEMPTS_PER_PROVIDER", "DISAGREEMENT_THRESHOLD"]
