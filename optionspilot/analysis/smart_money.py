"""Smart Money Concepts: fair value gaps, order blocks, liquidity pools,
liquidity grabs, premium/discount positioning.

Definitions used (the SMC literature is informal; these are the concrete,
testable versions this system trades on):

- **Fair Value Gap** — three-bar imbalance: bullish when bar N's low sits above
  bar N-2's high, leaving untraded space. The zone is (N-2 high, N low).
- **Order Block** — the last opposite-direction candle before a displacement
  move that breaks its extreme. Bullish OB: a bearish candle whose high is
  broken within `lookahead` bars by a move of at least `displacement_atr` ATRs
  measured from its low. Order blocks double as supply/demand zones in v1.
- **Liquidity pool** — equal highs (or lows): consecutive swing highs within
  `tolerance_atr` ATRs of each other; resting stops cluster there.
- **Liquidity grab** — a bar that wicks through a confirmed swing level but
  closes back on the original side (stop hunt / sweep).
- **Premium/Discount** — price location inside the most recent confirmed
  swing-low → swing-high range; above equilibrium is premium (favour shorts /
  profit-taking), below is discount (favour longs).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from optionspilot.analysis.indicators import atr
from optionspilot.analysis.structure import Swing
from optionspilot.core.models import Direction


@dataclass(slots=True)
class Zone:
    kind: str                     # "fvg_bull" | "fvg_bear" | "ob_bull" | "ob_bear" | "eqh" | "eql"
    created_ts: datetime
    top: float
    bottom: float
    mitigated_ts: datetime | None = None   # first revisit into the zone, if any

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass(frozen=True, slots=True)
class LiquidityGrab:
    ts: datetime
    level: float
    direction: Direction   # implied reversal: sweep above highs -> SHORT bias


@dataclass(frozen=True, slots=True)
class RangeContext:
    high: float
    low: float
    equilibrium: float
    position: float        # 0 = at range low, 1 = at range high
    zone: str              # "premium" | "discount" | "equilibrium"


def find_fvgs(df: pd.DataFrame, min_size_atr: float = 0.0, atr_period: int = 14) -> list[Zone]:
    """Three-bar imbalances, oldest first, with mitigation tracked."""
    a = atr(df, atr_period)
    highs, lows = df["high"], df["low"]
    zones: list[Zone] = []
    for i in range(2, len(df)):
        floor = a.iloc[i] * min_size_atr if pd.notna(a.iloc[i]) else 0.0
        if lows.iloc[i] > highs.iloc[i - 2] and (lows.iloc[i] - highs.iloc[i - 2]) >= floor:
            zones.append(Zone("fvg_bull", df.index[i],
                              top=float(lows.iloc[i]), bottom=float(highs.iloc[i - 2])))
        if highs.iloc[i] < lows.iloc[i - 2] and (lows.iloc[i - 2] - highs.iloc[i]) >= floor:
            zones.append(Zone("fvg_bear", df.index[i],
                              top=float(lows.iloc[i - 2]), bottom=float(highs.iloc[i])))
    _track_mitigation(df, zones)
    return zones


def find_order_blocks(
    df: pd.DataFrame,
    displacement_atr: float = 1.5,
    lookahead: int = 3,
    atr_period: int = 14,
) -> list[Zone]:
    a = atr(df, atr_period)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    zones: list[Zone] = []
    for i in range(len(df) - 1):
        if pd.isna(a.iloc[i]) or a.iloc[i] <= 0:
            continue
        fwd = c.iloc[i + 1: i + 1 + lookahead]
        if fwd.empty:
            continue
        if c.iloc[i] < o.iloc[i]:  # bearish candle -> potential bullish OB
            if fwd.max() > h.iloc[i] and (fwd.max() - l.iloc[i]) >= displacement_atr * a.iloc[i]:
                zones.append(Zone("ob_bull", df.index[i],
                                  top=float(h.iloc[i]), bottom=float(l.iloc[i])))
        elif c.iloc[i] > o.iloc[i]:  # bullish candle -> potential bearish OB
            if fwd.min() < l.iloc[i] and (h.iloc[i] - fwd.min()) >= displacement_atr * a.iloc[i]:
                zones.append(Zone("ob_bear", df.index[i],
                                  top=float(h.iloc[i]), bottom=float(l.iloc[i])))
    _track_mitigation(df, zones, skip_bars=lookahead)
    return zones


def find_equal_levels(
    df: pd.DataFrame,
    swings: list[Swing],
    tolerance_atr: float = 0.25,
    atr_period: int = 14,
) -> list[Zone]:
    """Clusters of near-equal swing highs (eqh) / lows (eql) — liquidity pools."""
    a = atr(df, atr_period)
    zones: list[Zone] = []
    for is_high, kind in ((True, "eqh"), (False, "eql")):
        pts = [s for s in swings if s.is_high == is_high]
        for prev, cur in zip(pts, pts[1:]):
            tol_ref = a.asof(cur.ts)
            if pd.isna(tol_ref):
                continue
            if abs(cur.price - prev.price) <= tolerance_atr * tol_ref:
                zones.append(Zone(
                    kind, cur.confirmed_ts,
                    top=max(cur.price, prev.price),
                    bottom=min(cur.price, prev.price),
                ))
    return zones


def find_liquidity_grabs(df: pd.DataFrame, swings: list[Swing]) -> list[LiquidityGrab]:
    """Sweeps of confirmed swing levels: wick through, close back inside."""
    by_confirmation = sorted(swings, key=lambda s: s.confirmed_ts)
    grabs: list[LiquidityGrab] = []
    active_high: Swing | None = None
    active_low: Swing | None = None
    i = 0
    for ts, row in df.iterrows():
        while i < len(by_confirmation) and by_confirmation[i].confirmed_ts <= ts:
            s = by_confirmation[i]
            if s.is_high:
                active_high = s
            else:
                active_low = s
            i += 1
        if active_high and row["high"] > active_high.price and row["close"] < active_high.price:
            grabs.append(LiquidityGrab(ts, active_high.price, Direction.SHORT))
            active_high = None
        if active_low and row["low"] < active_low.price and row["close"] > active_low.price:
            grabs.append(LiquidityGrab(ts, active_low.price, Direction.LONG))
            active_low = None
    return grabs


def premium_discount(close_price: float, swings: list[Swing],
                     buffer: float = 0.05) -> RangeContext | None:
    """Locate price inside the most recent confirmed swing range.
    Returns None when no valid range exists yet."""
    hi = next((s.price for s in reversed(swings) if s.is_high), None)
    lo = next((s.price for s in reversed(swings) if not s.is_high), None)
    if hi is None or lo is None or hi <= lo:
        return None
    position = (close_price - lo) / (hi - lo)
    if position > 0.5 + buffer:
        zone = "premium"
    elif position < 0.5 - buffer:
        zone = "discount"
    else:
        zone = "equilibrium"
    return RangeContext(high=hi, low=lo, equilibrium=(hi + lo) / 2,
                        position=position, zone=zone)


def _track_mitigation(df: pd.DataFrame, zones: list[Zone], skip_bars: int = 0) -> None:
    """Mark each zone's first revisit. `skip_bars` ignores the displacement
    bars immediately after creation (they're part of the move that made the
    zone, not a retest of it)."""
    for z in zones:
        creation_pos = df.index.get_loc(z.created_ts)
        after = df.iloc[creation_pos + 1 + skip_bars:]
        if z.kind in ("fvg_bull", "ob_bull", "eql"):
            hit = after[after["low"] <= z.top]
        else:
            hit = after[after["high"] >= z.bottom]
        if not hit.empty:
            z.mitigated_ts = hit.index[0]
