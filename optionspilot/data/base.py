"""Market data provider interface.

Candle data convention used across the entire codebase:
    pandas DataFrame with
      - index:   tz-aware UTC DatetimeIndex named 'ts' (bar open time), ascending
      - columns: open, high, low, close, volume (float64)

Every provider must return this shape; every consumer may assume it.
`validate_candles()` enforces it at the boundary.
"""

from __future__ import annotations

import abc
from datetime import date, datetime

import numpy as np
import pandas as pd

from optionspilot.core.models import OptionContract, Quote, Timeframe
from optionspilot.core.logging_setup import get_logger

log = get_logger("data")

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]

# The exchange this app trades. A daily bar's identity is its SESSION DATE, and
# a date only becomes an instant relative to a timezone — so every daily+ bar in
# this system is stamped at 00:00 in this zone. See `session_index`.
SESSION_TZ = "America/New_York"


def session_index(index: pd.DatetimeIndex,
                  tz: str = SESSION_TZ) -> pd.DatetimeIndex:
    """Snap daily-or-coarser timestamps to 00:00 exchange-local, as UTC.

    **This is the one convention for daily+ bars, and it is load-bearing.** The
    cache is keyed `(symbol, timeframe, ts)`, so two providers that stamp the
    same trading day at different instants do not collide — they produce two
    rows for one day. That is not hypothetical: measured against a real
    `cache.db`, Yahoo stamped 2026-07-24 daily at **13:30 UTC** (the 09:30 ET
    session open, straight from the v8 chart JSON) while yfinance stamped the
    same bar at **04:00 UTC** (00:00 ET, its tz-aware index). Every day fetched
    by both providers had two rows 9.5 hours apart, which made the frame's
    tightest spacing 0.40 days instead of 1.0 — so `validate_history` correctly
    reported "wrong interval served" and the chart refused to draw ANY symbol on
    1D. 6,517 SPY daily rows for ~3,258 trading days.

    Why exchange midnight and not UTC midnight (which `http_adapter` used, and
    which the Stooq adapter's comment claimed everything used): the chart
    renders every axis and crosshair label through an America/New_York
    formatter (V3.3 Issue 2). 00:00 UTC is 19:00 or 20:00 ET on the PREVIOUS
    day, so a UTC-midnight daily bar labels itself one day early. Exchange
    midnight is the only choice that is both collision-free and correct on
    screen, and it is what V3.3 documented ("daily bars sit at ET midnight → no
    off-by-one date").

    Idempotent: a frame already on the convention is returned unchanged.
    """
    if len(index) == 0:
        return index
    return (pd.DatetimeIndex(index).tz_convert(tz).normalize()
            .tz_convert("UTC"))


def validate_candles(df: pd.DataFrame, context: str = "") -> pd.DataFrame:
    """Normalize and assert the canonical candle DataFrame shape.

    Beyond shape, this is the one place malformed provider bars are removed:
    NaN/±inf/non-positive OHLC rows are dropped (half-formed or glitched bars
    — yfinance emits these intermittently), NaN/±inf volume is coerced to 0
    (routine on the in-progress bar; a bad volume must never invalidate a
    priced bar). Every removal is logged with `context` so a chart that lost
    bars is explainable from data.log instead of failing silently downstream.
    """
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS,
                            index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    missing = [c for c in CANDLE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Candle data missing columns: {missing}")
    df = df[CANDLE_COLUMNS].astype("float64")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Candle data index must be a DatetimeIndex")
    if df.index.tz is None:
        raise ValueError("Candle data index must be timezone-aware")
    df.index = df.index.tz_convert("UTC")
    df.index.name = "ts"
    # A NaT index entry survives every later step and only detonates at the
    # very end of /api/candles, where `int(ts.timestamp())` raises and 500s the
    # whole response — one unparseable bar taking the entire chart down. Two
    # real sources: `pd.to_datetime(..., errors="coerce")` in the HTTP adapters
    # turns any timestamp a provider malforms into NaT, and http_adapter's
    # `localize` maps a DST fall-back ambiguity to NaT by design. Drop them
    # here, at the one boundary that already exists for exactly this job.
    n_nat = int(df.index.isna().sum())
    if n_nat:
        df = df[df.index.notna()]
    dups = int(df.index.duplicated().sum())
    df = df[~df.index.duplicated(keep="last")].sort_index()
    ohlc = df[["open", "high", "low", "close"]]
    bad = (~np.isfinite(ohlc)).any(axis=1) | (ohlc <= 0).any(axis=1)
    n_bad = int(bad.sum())
    if n_bad:
        df = df[~bad]
    bad_vol = ~np.isfinite(df["volume"])
    n_bad_vol = int(bad_vol.sum())
    if n_bad_vol:
        df = df.copy()
        df.loc[bad_vol, "volume"] = 0.0
    if dups or n_bad or n_bad_vol or n_nat:
        log.warning("validate_candles%s: dropped %d unparseable ts, removed %d "
                    "duplicate ts, dropped %d bad-OHLC bars, zeroed %d "
                    "bad-volume bars",
                    f" [{context}]" if context else "", n_nat, dups, n_bad,
                    n_bad_vol)
    return df


class MarketDataProvider(abc.ABC):
    """All market data flows through this interface — live engine and
    backtester alike. Implementations must be stateless or internally
    thread-safe."""

    name: str = "abstract"

    @abc.abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        extended_hours: bool = False,
    ) -> pd.DataFrame:
        """Historical candles in the canonical shape (see module docstring).

        `extended_hours` is a DISPLAY-only opt-in: when True (and the interval
        is intraday) the frame includes pre-/after-market bars. The engine and
        every trading path leave it False so execution stays RTH-only.
        Providers that cannot supply extended-hours data may ignore it and
        return RTH bars; callers must not assume extra sessions are present.
        """

    @abc.abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Latest quote for the underlying."""

    @abc.abstractmethod
    def get_expirations(self, symbol: str) -> list[date]:
        """Available option expiration dates, ascending."""

    @abc.abstractmethod
    def get_option_chain(self, symbol: str, expiration: date) -> list[OptionContract]:
        """Full chain (calls and puts) for one expiration."""
