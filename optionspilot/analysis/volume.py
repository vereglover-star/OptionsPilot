"""Volume analysis: spikes, net buying/selling pressure, price-volume divergence."""

from __future__ import annotations

import numpy as np
import pandas as pd

from optionspilot.analysis.indicators import obv, relative_volume


def volume_spikes(df: pd.DataFrame, period: int = 20, threshold: float = 2.0) -> pd.Series:
    """True where volume is at least `threshold`× its trailing average."""
    return (relative_volume(df, period) >= threshold).fillna(False).rename("volume_spike")


def pressure(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Net buying/selling pressure in [-1, +1].

    Each bar's Close Location Value ((close-low) - (high-close)) / range says
    where within its range the bar closed; weighting by volume and rolling over
    `period` bars gives who has been winning the auction: +1 = buyers closing
    every bar at its high on size, -1 = sellers pinning closes to the lows.
    """
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    weighted = (clv * df["volume"]).rolling(period).sum()
    total = df["volume"].rolling(period).sum()
    return (weighted / total.replace(0, np.nan)).rename("pressure")


def detect_divergence(df: pd.DataFrame, lookback: int = 30) -> int:
    """Price/OBV divergence over the trailing window.

    Splits the window in half and compares extremes:
      -1  bearish — price made a higher high, OBV did not confirm
      +1  bullish — price made a lower low, OBV did not confirm
       0  no divergence (or not enough data)
    Deliberately coarse: divergence is confluence evidence, never a standalone
    trigger.
    """
    if len(df) < lookback:
        return 0
    win = df.iloc[-lookback:]
    o = obv(df).iloc[-lookback:]
    half = lookback // 2

    price_hh = win["close"].iloc[half:].max() > win["close"].iloc[:half].max()
    obv_lh = o.iloc[half:].max() < o.iloc[:half].max()
    bearish = price_hh and obv_lh

    price_ll = win["close"].iloc[half:].min() < win["close"].iloc[:half].min()
    obv_hl = o.iloc[half:].min() > o.iloc[:half].min()
    bullish = price_ll and obv_hl

    if bearish and not bullish:
        return -1
    if bullish and not bearish:
        return 1
    return 0
