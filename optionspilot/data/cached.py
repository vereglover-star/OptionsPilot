"""CachedProvider — a caching/deduplicating layer over any MarketDataProvider.

Sits between the orchestrator/UI and the real (network) provider so that one
scan cycle never fetches the same thing twice, and consecutive cycles only
refetch what could actually have changed:

  - Candles are memoized per (symbol, timeframe) with a timeframe-aware
    freshness cap (a 4h frame can't grow a new bar every 30 seconds), and
    written through to the SQLite CandleCache so a restart within the
    freshness window starts warm instead of re-downloading everything.
  - Quotes, option chains, and expirations get short in-memory TTLs sized to
    the ~15-minute-delayed upstream data: a 5s quote memo or 30s chain memo
    cannot lose information the free feed didn't have anyway.
  - Concurrent requests for the same key are deduplicated: one caller fetches,
    the rest wait for its result (important once fetches run in parallel).

The wrapper implements the same MarketDataProvider interface, so everything
downstream (engine, risk, broker, backtester) is unaware it exists. Tests that
inject fake providers bypass it entirely.
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
from optionspilot.data.cache import CandleCache

log = get_logger("data")

# How long a fetched candle frame stays fresh, per timeframe (seconds).
# Sized to the bar interval and the free feed's ~15-minute delay: the entry
# timeframe and daily bars (used for live price display) stay tight; higher
# timeframes can't grow new bars quickly and don't need refetching to show one.
CANDLE_TTL: dict[Timeframe, float] = {
    Timeframe.M1: 20.0,
    Timeframe.M2: 20.0,
    Timeframe.M3: 20.0,
    Timeframe.M5: 30.0,
    Timeframe.M10: 45.0,
    Timeframe.M15: 60.0,
    Timeframe.M30: 90.0,
    Timeframe.H1: 120.0,
    Timeframe.H2: 180.0,
    Timeframe.H4: 240.0,
    Timeframe.D1: 60.0,
    Timeframe.W1: 600.0,
    Timeframe.MN1: 600.0,
}
DEFAULT_CANDLE_TTL = 60.0

# An EMPTY fetch result is almost always a transient upstream failure
# (yfinance returns an empty frame on rate limits and network hiccups,
# indistinguishable from "no such data"). Caching it for the full TTL
# poisons every retry for up to a minute — the root cause of the app
# opening with blank charts (root-caused 2026-07-17). Cache empties just
# long enough to stop a tight retry loop from hammering Yahoo.
EMPTY_CANDLE_TTL = 3.0

QUOTE_TTL = 5.0
CHAIN_TTL = 30.0
EXPIRATIONS_TTL = 3600.0


class _Entry:
    __slots__ = ("value", "fetched_at", "start")

    def __init__(self, value, fetched_at: float, start: datetime | None = None):
        self.value = value
        self.fetched_at = fetched_at
        self.start = start


class CachedProvider(MarketDataProvider):
    name = "cached"

    def __init__(self, inner: MarketDataProvider,
                 cache_db: str | Path | None = None):
        self._inner = inner
        self.name = f"cached({inner.name})"
        self._store = CandleCache(cache_db) if cache_db is not None else None
        self._lock = threading.Lock()
        self._mem: dict[tuple, _Entry] = {}
        self._inflight: dict[tuple, threading.Event] = {}

    # ── generic memo with in-flight dedup ────────────────────────────────────

    def _memo(self, key: tuple, valid, fetch):
        """Return the cached value for `key` while `valid(entry)` holds, else
        call `fetch()` — with concurrent callers for the same key waiting on a
        single fetch instead of stampeding the network."""
        while True:
            with self._lock:
                entry = self._mem.get(key)
                if entry is not None and valid(entry):
                    return entry.value
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break  # we are the fetcher
            event.wait(timeout=60.0)
            with self._lock:
                entry = self._mem.get(key)
            if entry is not None and valid(entry):
                return entry.value
            # the fetcher failed — loop and try to become the fetcher ourselves
        try:
            return fetch()
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            event.set()

    def _fresh(self, ttl: float):
        return lambda e: _time.monotonic() - e.fetched_at < ttl

    def _put(self, key: tuple, value, start: datetime | None = None):
        with self._lock:
            self._mem[key] = _Entry(value, _time.monotonic(), start=start)
        return value

    # ── candles ──────────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: Timeframe,
                    start: datetime, end: datetime,
                    *, extended_hours: bool = False) -> pd.DataFrame:
        symbol = symbol.upper()
        # Extended-hours frames are keyed (and memoized) separately so they never
        # mix with the RTH-only frames the engine/trading path reads. They are a
        # display-only surface, so they also bypass the on-disk store (which is
        # keyed by symbol+tf and has no session dimension) — the short-lived mem
        # cache is enough to serve chart refreshes.
        key = ("candles", symbol, timeframe, extended_hours)
        use_store = self._store is not None and not extended_hours
        ttl = CANDLE_TTL.get(timeframe, DEFAULT_CANDLE_TTL)
        fresh = self._fresh(ttl)

        def valid(entry: _Entry) -> bool:
            # the cached frame must cover the requested window and be fresh —
            # an empty (failed-fetch) frame only for EMPTY_CANDLE_TTL, so a
            # transient upstream failure never poisons retries for the full TTL
            if entry.value.empty:
                return self._fresh(EMPTY_CANDLE_TTL)(entry)
            return fresh(entry) and entry.start is not None and entry.start <= start

        def fetch() -> pd.DataFrame:
            df = None
            with self._lock:
                cold = key not in self._mem
            if cold and use_store:
                # cold start: reuse the on-disk cache if its last bar is still
                # inside the freshness window (e.g. an app restart mid-session)
                df = self._warm_from_store(symbol, timeframe, start, end, ttl)
            if df is None:
                # only pass the kwarg when set, so plain 4-arg providers
                # (test fakes, any legacy adapter) keep working unchanged
                df = (self._inner.get_candles(symbol, timeframe, start, end,
                                              extended_hours=True)
                      if extended_hours
                      else self._inner.get_candles(symbol, timeframe, start, end))
                if df.empty:
                    log.warning("empty candle fetch %s %s (upstream failure "
                                "or no data) — cached for %.0fs only",
                                symbol, timeframe, EMPTY_CANDLE_TTL)
                elif use_store:
                    try:
                        self._store.store(symbol, timeframe, df)
                    except Exception as exc:  # noqa: BLE001 — cache is best-effort
                        log.error("candle cache store failed %s %s: %s",
                                  symbol, timeframe, exc)
            return self._put(key, df, start=start)

        return _slice(self._memo(key, valid, fetch), start)

    def get_candles_stale_ok(self, symbol: str, timeframe: Timeframe,
                             start: datetime, end: datetime,
                             *, extended_hours: bool = False,
                             ) -> tuple[pd.DataFrame, bool]:
        """Candles for DISPLAY surfaces (the Charts tab): same as
        `get_candles`, but when the live fetch fails/returns empty, fall
        back to the newest data on disk regardless of age, flagged stale.

        Returns `(frame, is_stale)`. The trading path must never use this —
        the engine's fail-closed rule (empty data ⇒ skip the symbol) depends
        on `get_candles` staying strict. A chart showing clearly-labeled
        yesterday's bars is useful; a trade placed on them is not.
        """
        df = self.get_candles(symbol, timeframe, start, end,
                              extended_hours=extended_hours)
        if not df.empty:
            return df, False
        # No disk fallback for extended-hours (the store has no session
        # dimension); its RTH bars would be a misleading "stale" surface.
        if self._store is not None and not extended_hours:
            try:
                cached = self._store.load(symbol.upper(), timeframe, start, end)
            except Exception as exc:  # noqa: BLE001 — fallback is best-effort
                log.error("stale candle fallback failed %s %s: %s",
                          symbol, timeframe, exc)
                cached = pd.DataFrame()
            if not cached.empty:
                log.warning("serving stale cached candles for %s %s "
                            "(last bar %s) — live fetch unavailable",
                            symbol, timeframe, cached.index[-1])
                return _slice(cached, start), True
        return df, False

    def _warm_from_store(self, symbol, timeframe, start, end, ttl):
        try:
            cached = self._store.load(symbol, timeframe, start, end)
        except Exception:  # noqa: BLE001 — fall back to a live fetch
            return None
        if cached.empty:
            return None
        age = (pd.Timestamp.now(tz="UTC") - cached.index[-1]).total_seconds()
        # the last cached bar's *open* must be within one bar + ttl of now for
        # the frame to still be current
        if age <= timeframe.minutes * 60 + ttl:
            return cached
        return None

    # ── quotes / chains / expirations ────────────────────────────────────────

    def get_quote(self, symbol: str) -> Quote:
        key = ("quote", symbol.upper())
        return self._memo(key, self._fresh(QUOTE_TTL),
                          lambda: self._put(key, self._inner.get_quote(symbol)))

    def get_expirations(self, symbol: str) -> list[date]:
        key = ("expirations", symbol.upper())
        return self._memo(key, self._fresh(EXPIRATIONS_TTL),
                          lambda: self._put(key, self._inner.get_expirations(symbol)))

    def get_option_chain(self, symbol: str,
                         expiration: date) -> list[OptionContract]:
        key = ("chain", symbol.upper(), expiration)
        return self._memo(
            key, self._fresh(CHAIN_TTL),
            lambda: self._put(key, self._inner.get_option_chain(symbol, expiration)))

    def invalidate_quotes(self) -> None:
        """Drop quote/chain memos (e.g. right before filling an order where
        maximum freshness matters more than saving one request)."""
        with self._lock:
            for key in [k for k in self._mem if k[0] in ("quote", "chain")]:
                del self._mem[key]

    # feature-detected extras (e.g. get_market_cap) pass through untouched
    def __getattr__(self, item):
        return getattr(self._inner, item)


def _slice(df: pd.DataFrame, start: datetime) -> pd.DataFrame:
    if df.empty or df.index[0] >= start:
        return df
    return df[df.index >= start]


__all__ = ["CachedProvider", "CANDLE_TTL", "EMPTY_CANDLE_TTL", "QUOTE_TTL",
           "CHAIN_TTL", "EXPIRATIONS_TTL"]
