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

import pandas as pd

from optionspilot.core.models import OptionContract, Quote, Timeframe

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize and assert the canonical candle DataFrame shape."""
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
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Drop rows with any NaN in OHLC (half-formed bars from providers)
    df = df.dropna(subset=["open", "high", "low", "close"])
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
