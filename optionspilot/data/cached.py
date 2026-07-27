"""CachedProvider — the `MarketDataProvider` face of the market-data stack.

The engine, the risk manager, the backtester and the UI all speak the
`MarketDataProvider` interface, and none of them should learn about providers,
fallback ladders or diagnostics. This class is the adapter between that
interface and `MarketDataService`:

    engine / UI  ──MarketDataProvider──▶  CachedProvider
                                              │
                          candles ────────────┴──▶ MarketDataService
                          quotes / chains / expirations ──▶ inner provider

Candles come from the service (which owns the provider chain, the cache, the
validation, and the diagnostics). Quotes, option chains and expirations still
come from a single `inner` provider — the options data has no second source in
this build — memoized with short TTLs sized to the ~15-minute-delayed upstream
feed, and deduplicated so concurrent callers share one fetch.

Two contracts here are load-bearing and must not be relaxed:

  - `get_candles` never returns stale data. The engine's fail-closed rule (no
    data ⇒ skip the symbol) depends on it. Stale bars are available only via
    `get_candles_stale_ok`, which display surfaces call and the trading path
    does not.
  - Anything the service could not validate comes back as an EMPTY frame, not
    as an approximation. A chart drawing yesterday's bars with a banner is
    useful; a trade placed on them is not.
"""

from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import OptionContract, Quote, Timeframe
from optionspilot.data.base import MarketDataProvider
from optionspilot.data.legacy import LegacyProviderAdapter
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.service import (
    CANDLE_TTL, DEFAULT_CANDLE_TTL, EMPTY_CANDLE_TTL, MEM_CACHE_MAX,
    HistoryResult, MarketDataService,
)

log = get_logger("data")

QUOTE_TTL = 5.0
CHAIN_TTL = 30.0
EXPIRATIONS_TTL = 3600.0


class _Entry:
    __slots__ = ("value", "fetched_at")

    def __init__(self, value, fetched_at: float):
        self.value = value
        self.fetched_at = fetched_at


class CachedProvider(MarketDataProvider):
    """`MarketDataProvider` backed by `MarketDataService` (candles) and a
    memoized inner provider (quotes/chains/expirations)."""

    name = "cached"

    def __init__(self, inner: MarketDataProvider,
                 cache_db: str | Path | None = None, *,
                 service: MarketDataService | None = None):
        self._inner = inner
        self.name = f"cached({inner.name})"
        if service is not None:
            self.service = service
        else:
            # No service supplied: adapt `inner` into the new chain as its sole
            # provider. This is what keeps every existing caller — and every
            # test that injects a fake provider — working unchanged, while
            # still routing through the validation/cache/diagnostics ladder.
            self.service = MarketDataService(
                ProviderRegistry([LegacyProviderAdapter(inner)]),
                cache_db=cache_db)
        self._lock = threading.Lock()
        self._mem: dict[tuple, _Entry] = {}
        self._inflight: dict[tuple, threading.Event] = {}

    # ── candles ──────────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: Timeframe,
                    start: datetime, end: datetime,
                    *, extended_hours: bool = False) -> pd.DataFrame:
        """Strict: live (or cache-fresh) bars only, empty on failure."""
        return self.service.get_history(
            symbol, timeframe, start, end,
            extended_hours=extended_hours, allow_stale=False).frame

    def get_candles_stale_ok(self, symbol: str, timeframe: Timeframe,
                             start: datetime, end: datetime,
                             *, extended_hours: bool = False,
                             ) -> tuple[pd.DataFrame, bool]:
        """Candles for DISPLAY surfaces: falls back to locally cached bars,
        flagged stale, rather than showing nothing. Kept for callers that only
        need the pair; `get_history` returns the full result."""
        result = self.get_history(symbol, timeframe, start, end,
                                  extended_hours=extended_hours)
        return result.frame, result.stale

    def get_history(self, symbol: str, timeframe: Timeframe,
                    start: datetime, end: datetime, *,
                    extended_hours: bool = False) -> HistoryResult:
        """The full result — outcome, provider, staleness, history exhaustion,
        validation report and diagnostics id. This is what `/api/candles` uses
        so the frontend can be explicit about which state it is in."""
        return self.service.get_history(symbol, timeframe, start, end,
                                        extended_hours=extended_hours,
                                        allow_stale=True)

    # ── quotes / chains / expirations ────────────────────────────────────────

    def get_quote(self, symbol: str) -> Quote:
        key = ("quote", symbol.upper())
        return self._memo(key, QUOTE_TTL, lambda: self._inner.get_quote(symbol))

    def get_expirations(self, symbol: str) -> list[date]:
        key = ("expirations", symbol.upper())
        return self._memo(key, EXPIRATIONS_TTL,
                          lambda: self._inner.get_expirations(symbol))

    def get_option_chain(self, symbol: str,
                         expiration: date) -> list[OptionContract]:
        key = ("chain", symbol.upper(), expiration)
        return self._memo(key, CHAIN_TTL,
                          lambda: self._inner.get_option_chain(symbol, expiration))

    def invalidate_quotes(self) -> None:
        """Drop quote/chain memos (e.g. right before filling an order, where
        maximum freshness matters more than saving one request)."""
        with self._lock:
            for key in [k for k in self._mem if k[0] in ("quote", "chain")]:
                del self._mem[key]

    # ── observability ────────────────────────────────────────────────────────

    def health(self) -> dict:
        """Provider health, cache stats and request diagnostics in one object."""
        return self.service.health()

    @property
    def diagnostics(self):
        return self.service.diagnostics

    # ── memo with in-flight dedup ────────────────────────────────────────────

    def _memo(self, key: tuple, ttl: float, fetch):
        """Return the memoized value while fresh, else call `fetch()` — with
        concurrent callers for the same key waiting on a single fetch instead
        of stampeding the network."""
        while True:
            with self._lock:
                entry = self._mem.get(key)
                if entry is not None and _fresh(entry.fetched_at, ttl):
                    return entry.value
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break                      # we are the fetcher
            event.wait(timeout=30.0)
            with self._lock:
                entry = self._mem.get(key)
            if entry is not None and _fresh(entry.fetched_at, ttl):
                return entry.value
            # the fetcher failed — loop and try to become the fetcher ourselves
        try:
            return self._put(key, fetch())
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            event.set()

    def _put(self, key: tuple, value):
        with self._lock:
            # Bounded LRU-ish store: re-insert to move the key to the newest
            # slot, evicting the oldest when over the cap.
            self._mem.pop(key, None)
            self._mem[key] = _Entry(value, _time.monotonic())
            while len(self._mem) > MEM_CACHE_MAX:
                self._mem.pop(next(iter(self._mem)))
        return value

    # feature-detected extras (e.g. get_market_cap) pass through untouched
    def __getattr__(self, item):
        return getattr(self._inner, item)


def _fresh(fetched_at: float, ttl: float) -> bool:
    return _time.monotonic() - fetched_at < ttl


__all__ = ["CachedProvider", "CANDLE_TTL", "DEFAULT_CANDLE_TTL",
           "EMPTY_CANDLE_TTL", "QUOTE_TTL", "CHAIN_TTL", "EXPIRATIONS_TTL",
           "MEM_CACHE_MAX"]
