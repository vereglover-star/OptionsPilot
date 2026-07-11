"""Market structure: swing points, HH/HL/LH/LL, BOS, CHoCH, trend, consolidation.

Lookahead discipline: a swing pivot needs `strength` bars on each side, so it is
only *knowable* `strength` bars after it forms. Every Swing carries both its
pivot time (`ts`) and the time it became knowable (`confirmed_ts`), and
`detect_events` only arms a level once it is confirmed — the live engine and
backtester therefore see identical, causally-valid structure.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from optionspilot.analysis.indicators import atr
from optionspilot.core.models import Direction


class Trend(enum.Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class Swing:
    ts: datetime            # time of the pivot bar
    confirmed_ts: datetime  # time the pivot became knowable
    price: float
    is_high: bool
    label: str              # "HH" | "LH" | "HL" | "LL" | "" for the first of its kind


@dataclass(frozen=True, slots=True)
class StructureEvent:
    ts: datetime
    kind: str               # "BOS" | "CHOCH"
    direction: Direction    # LONG = broke up through a swing high
    level: float            # the swing level that was broken


def find_swings(df: pd.DataFrame, strength: int = 2) -> list[Swing]:
    """Fractal swing points: a bar whose high (low) is the strict extreme of the
    2*strength+1 bars centred on it. Labeled against the previous swing of the
    same kind: HH/LH for highs, HL/LL for lows."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    idx = df.index
    n = len(df)
    swings: list[Swing] = []
    last_high: float | None = None
    last_low: float | None = None

    for i in range(strength, n - strength):
        window_h = highs[i - strength: i + strength + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            label = "" if last_high is None else ("HH" if highs[i] > last_high else "LH")
            swings.append(Swing(idx[i], idx[i + strength], float(highs[i]), True, label))
            last_high = float(highs[i])
        window_l = lows[i - strength: i + strength + 1]
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            label = "" if last_low is None else ("HL" if lows[i] > last_low else "LL")
            swings.append(Swing(idx[i], idx[i + strength], float(lows[i]), False, label))
            last_low = float(lows[i])

    swings.sort(key=lambda s: (s.ts, s.is_high))
    return swings


def trend_state(swings: list[Swing]) -> Trend:
    """UP when the most recent labeled high is a HH and the most recent labeled
    low is a HL; DOWN for LH+LL; anything mixed (or too little data) is RANGE."""
    hi = next((s.label for s in reversed(swings) if s.is_high and s.label), None)
    lo = next((s.label for s in reversed(swings) if not s.is_high and s.label), None)
    if hi == "HH" and lo == "HL":
        return Trend.UP
    if hi == "LH" and lo == "LL":
        return Trend.DOWN
    return Trend.RANGE


def detect_events(df: pd.DataFrame, swings: list[Swing]) -> list[StructureEvent]:
    """Walk the bars chronologically; a close through the most recent confirmed
    swing level is a BOS when it continues the standing bias and a CHoCH when it
    flips it. The very first break (no bias yet) counts as BOS."""
    by_confirmation = sorted(swings, key=lambda s: s.confirmed_ts)
    events: list[StructureEvent] = []
    active_high: Swing | None = None
    active_low: Swing | None = None
    bias: Direction | None = None
    i = 0

    for ts, close in df["close"].items():
        while i < len(by_confirmation) and by_confirmation[i].confirmed_ts <= ts:
            s = by_confirmation[i]
            if s.is_high:
                active_high = s
            else:
                active_low = s
            i += 1
        if active_high is not None and close > active_high.price:
            kind = "CHOCH" if bias is Direction.SHORT else "BOS"
            events.append(StructureEvent(ts, kind, Direction.LONG, active_high.price))
            bias = Direction.LONG
            active_high = None  # spent; wait for the next confirmed high
        if active_low is not None and close < active_low.price:
            kind = "CHOCH" if bias is Direction.LONG else "BOS"
            events.append(StructureEvent(ts, kind, Direction.SHORT, active_low.price))
            bias = Direction.SHORT
            active_low = None
    return events


def is_consolidating(
    df: pd.DataFrame, lookback: int = 15, atr_mult: float = 2.5, atr_period: int = 14
) -> pd.Series:
    """True where the total price range of the trailing `lookback` bars is
    small relative to volatility — i.e. the market is coiling, not trending."""
    span = df["high"].rolling(lookback).max() - df["low"].rolling(lookback).min()
    a = atr(df, atr_period)
    return ((span / a.replace(0, np.nan)) <= atr_mult).fillna(False).rename("consolidating")
