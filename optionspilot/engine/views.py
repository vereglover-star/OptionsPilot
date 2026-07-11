"""Per-timeframe analysis snapshot (TimeframeView) and the analyzer that
builds one per configured timeframe.

The analyzer is pure computation over candle frames passed *in* — it performs
no I/O. The orchestrator (live) and the backtester (replay) both hand it the
same canonical DataFrames, which is what guarantees identical behaviour in both
worlds.

Indicator enable/disable flags from config are respected here: a disabled
indicator's fields are NaN/None/0 and the scorer emits no evidence for them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from optionspilot.analysis import candlesticks as cs
from optionspilot.analysis import indicators as ind
from optionspilot.analysis import smart_money as smc
from optionspilot.analysis import volume as vol
from optionspilot.analysis.structure import (
    StructureEvent, Swing, Trend, detect_events, find_swings, is_consolidating,
    trend_state,
)
from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Timeframe

MIN_BARS = 40           # below this, a timeframe is skipped rather than guessed at
RECENT_GRAB_BARS = 10   # liquidity grabs older than this are stale context


@dataclass(frozen=True, slots=True)
class TimeframeView:
    """Everything the scorer/planner needs to know about one timeframe,
    computed strictly from closed bars (no lookahead)."""

    timeframe: Timeframe
    ts: datetime                      # time of the last closed bar
    close: float
    atr: float
    trend: Trend = Trend.RANGE
    swings: tuple[Swing, ...] = ()
    last_event: StructureEvent | None = None
    bars_since_event: int | None = None
    consolidating: bool = False
    # Indicators (NaN / 0 / None when disabled or undefined)
    rsi: float = float("nan")
    macd_hist: float = float("nan")
    macd_hist_prev: float = float("nan")
    adx: float = float("nan")
    plus_di: float = float("nan")
    minus_di: float = float("nan")
    supertrend_dir: int = 0
    ema_stack: int = 0                # +1 close>fast>mid>slow, -1 inverse, 0 mixed
    above_vwap: bool | None = None
    rvol: float = float("nan")
    pressure: float = float("nan")
    divergence: int = 0
    # Patterns fired on the last closed bar
    patterns: tuple[str, ...] = ()
    # Smart money context
    range_ctx: smc.RangeContext | None = None
    open_zones: tuple[smc.Zone, ...] = ()
    recent_grabs: tuple[smc.LiquidityGrab, ...] = ()


class MultiTimeframeAnalyzer:
    def __init__(self, config: AppConfig):
        self._cfg = config

    def analyze(
        self, candles_by_tf: dict[Timeframe, pd.DataFrame]
    ) -> dict[Timeframe, TimeframeView]:
        return {
            tf: self._build_view(tf, df)
            for tf, df in candles_by_tf.items()
            if len(df) >= MIN_BARS
        }

    def _build_view(self, tf: Timeframe, df: pd.DataFrame) -> TimeframeView:
        icfg = self._cfg.indicators
        close = df["close"]
        last_close = float(close.iloc[-1])
        atr_val = float(ind.atr(df, icfg.atr_period).iloc[-1])

        swings = find_swings(df, strength=2)
        events = detect_events(df, swings)
        last_event = events[-1] if events else None
        bars_since = (
            len(df) - 1 - df.index.get_loc(last_event.ts) if last_event else None
        )

        rsi_val = float(ind.rsi(close, icfg.rsi_period).iloc[-1]) if icfg.rsi else float("nan")

        macd_hist = macd_hist_prev = float("nan")
        if icfg.macd:
            hist = ind.macd(close)["macd_hist"]
            macd_hist = float(hist.iloc[-1])
            macd_hist_prev = float(hist.iloc[-2])

        adx_val = plus_di = minus_di = float("nan")
        if icfg.adx:
            adx_df = ind.adx(df)
            adx_val = float(adx_df["adx"].iloc[-1])
            plus_di = float(adx_df["plus_di"].iloc[-1])
            minus_di = float(adx_df["minus_di"].iloc[-1])

        st_dir = 0
        if icfg.supertrend:
            d = ind.supertrend(df)["supertrend_dir"].iloc[-1]
            st_dir = int(d) if not math.isnan(d) else 0

        ema_stack = 0
        if icfg.ema and len(icfg.ema_periods) >= 3:
            fast, mid, slow = (
                float(ind.ema(close, p).iloc[-1]) for p in icfg.ema_periods[:3]
            )
            if last_close > fast > mid > slow:
                ema_stack = 1
            elif last_close < fast < mid < slow:
                ema_stack = -1

        above_vwap = None
        if icfg.vwap and tf is not Timeframe.D1:
            v = ind.vwap(df).iloc[-1]
            above_vwap = bool(last_close > v) if not math.isnan(v) else None

        rvol_val = float(ind.relative_volume(df).iloc[-1])
        pressure_val = float(vol.pressure(df).iloc[-1])
        divergence = vol.detect_divergence(df) if icfg.obv else 0

        pattern_row = cs.detect_all(df).iloc[-1]
        patterns = tuple(pattern_row.index[pattern_row])

        open_zones = tuple(
            z for z in (smc.find_fvgs(df, min_size_atr=0.3) + smc.find_order_blocks(df))
            if z.mitigated_ts is None
        )
        cutoff = df.index[-min(RECENT_GRAB_BARS, len(df))]
        recent_grabs = tuple(
            g for g in smc.find_liquidity_grabs(df, swings) if g.ts >= cutoff
        )

        return TimeframeView(
            timeframe=tf,
            ts=df.index[-1].to_pydatetime(),
            close=last_close,
            atr=atr_val,
            trend=trend_state(swings),
            swings=tuple(swings),
            last_event=last_event,
            bars_since_event=bars_since,
            consolidating=bool(is_consolidating(df).iloc[-1]),
            rsi=rsi_val,
            macd_hist=macd_hist,
            macd_hist_prev=macd_hist_prev,
            adx=adx_val,
            plus_di=plus_di,
            minus_di=minus_di,
            supertrend_dir=st_dir,
            ema_stack=ema_stack,
            above_vwap=above_vwap,
            rvol=rvol_val,
            pressure=pressure_val,
            divergence=divergence,
            patterns=patterns,
            range_ctx=smc.premium_discount(last_close, swings),
            open_zones=open_zones,
            recent_grabs=recent_grabs,
        )
