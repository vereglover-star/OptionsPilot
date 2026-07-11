"""Shared builders for engine tests."""

from datetime import date, datetime, timedelta, timezone

from optionspilot.analysis.smart_money import RangeContext, Zone
from optionspilot.analysis.structure import StructureEvent, Swing, Trend
from optionspilot.core.models import Direction, OptionContract, OptionRight, Timeframe
from optionspilot.engine.views import TimeframeView

TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
TODAY = date(2026, 7, 10)


def make_view(timeframe=Timeframe.M5, close=100.0, atr=1.0, **overrides) -> TimeframeView:
    return TimeframeView(timeframe=timeframe, ts=TS, close=close, atr=atr, **overrides)


def bullish_entry_view(**overrides) -> TimeframeView:
    """A 5m view where every enabled lens points up."""
    defaults = dict(
        trend=Trend.UP,
        swings=(
            Swing(TS - timedelta(hours=2), TS - timedelta(hours=2), 95.0, False, ""),
            Swing(TS - timedelta(hours=1), TS - timedelta(hours=1), 105.0, True, ""),
            Swing(TS - timedelta(minutes=30), TS - timedelta(minutes=30), 98.0, False, "HL"),
        ),
        last_event=StructureEvent(TS, "BOS", Direction.LONG, 99.5),
        bars_since_event=3,
        rsi=62.0,
        macd_hist=0.15,
        macd_hist_prev=0.10,
        adx=30.0, plus_di=28.0, minus_di=12.0,
        supertrend_dir=1,
        ema_stack=1,
        above_vwap=True,
        rvol=1.8,
        pressure=0.6,
        patterns=("bullish_engulfing",),
        range_ctx=RangeContext(high=105.0, low=95.0, equilibrium=100.0,
                               position=0.3, zone="discount"),
        recent_grabs=(),
        open_zones=(Zone("fvg_bull", TS, top=99.8, bottom=99.2),),
    )
    defaults.update(overrides)
    return make_view(**defaults)


def bearish_entry_view(**overrides) -> TimeframeView:
    defaults = dict(
        trend=Trend.DOWN,
        swings=(
            Swing(TS - timedelta(hours=1), TS - timedelta(hours=1), 105.0, True, ""),
            Swing(TS - timedelta(minutes=30), TS - timedelta(minutes=30), 102.0, True, "LH"),
            Swing(TS - timedelta(minutes=15), TS - timedelta(minutes=15), 94.0, False, "LL"),
        ),
        last_event=StructureEvent(TS, "BOS", Direction.SHORT, 100.5),
        bars_since_event=3,
        rsi=38.0,
        macd_hist=-0.15,
        macd_hist_prev=-0.10,
        adx=30.0, plus_di=12.0, minus_di=28.0,
        supertrend_dir=-1,
        ema_stack=-1,
        above_vwap=False,
        rvol=1.8,
        pressure=-0.6,
        patterns=("bearish_engulfing",),
        range_ctx=RangeContext(high=105.0, low=95.0, equilibrium=100.0,
                               position=0.7, zone="premium"),
        open_zones=(Zone("fvg_bear", TS, top=100.8, bottom=100.2),),
    )
    defaults.update(overrides)
    return make_view(**defaults)


def htf_view(bullish: bool, timeframe=Timeframe.H1) -> TimeframeView:
    return make_view(
        timeframe=timeframe,
        trend=Trend.UP if bullish else Trend.DOWN,
        supertrend_dir=1 if bullish else -1,
        ema_stack=1 if bullish else -1,
        pressure=0.4 if bullish else -0.4,
        rsi=60.0 if bullish else 40.0,
        macd_hist=0.1 if bullish else -0.1,
        macd_hist_prev=0.05 if bullish else -0.05,
    )


def make_call(strike, delta, bid=2.00, ask=2.10, oi=1000, volume=500,
              expiration=None, iv=0.20) -> OptionContract:
    return OptionContract(
        "SPY", expiration or (TODAY + timedelta(days=21)), strike, OptionRight.CALL,
        bid=bid, ask=ask, volume=volume, open_interest=oi,
        implied_volatility=iv, delta=delta, gamma=0.02, theta=-0.03, vega=0.10,
    )


def make_put(strike, delta, **kw) -> OptionContract:
    c = make_call(strike, delta, **kw)
    import dataclasses
    return dataclasses.replace(c, right=OptionRight.PUT)


def make_plan(direction=Direction.LONG, entry_price=2.05, spot=100.0,
              stop=97.75, target=105.0, partials=(102.25,), rr=2.22,
              contract=None, confidence=85.0):
    from optionspilot.core.models import Signal, TradePlan

    signal = Signal(symbol="SPY", ts=TS, direction=direction,
                    confidence=confidence, evidence=(), strategy="test",
                    timeframe=Timeframe.M5)
    return TradePlan(
        signal=signal,
        contract=contract if contract is not None else (
            make_call(100, 0.45) if direction is Direction.LONG else make_put(100, -0.45)
        ),
        entry_price=entry_price, spot=spot,
        stop_underlying=stop, target_underlying=target,
        partial_levels=partials, invalidation="test", risk_reward=rr,
    )
