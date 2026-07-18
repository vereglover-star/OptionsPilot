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
    if dups or n_bad or n_bad_vol:
        log.warning("validate_candles%s: removed %d duplicate ts, dropped %d "
                    "bad-OHLC bars, zeroed %d bad-volume bars",
                    f" [{context}]" if context else "", dups, n_bad, n_bad_vol)
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
    ) -> pd.DataFrame:
        """Historical candles in the canonical shape (see module docstring)."""

    @abc.abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Latest quote for the underlying."""

    @abc.abstractmethod
    def get_expirations(self, symbol: str) -> list[date]:
        """Available option expiration dates, ascending."""

    @abc.abstractmethod
    def get_option_chain(self, symbol: str, expiration: date) -> list[OptionContract]:
        """Full chain (calls and puts) for one expiration."""
