"""MultiTimeframeAnalyzer + DecisionEngine integration tests on synthetic data."""

from datetime import date, timedelta

import numpy as np

from optionspilot.analysis.structure import Trend
from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Direction, Timeframe
from optionspilot.engine import DecisionEngine, MultiTimeframeAnalyzer
from tests.conftest import make_candles, zigzag
from tests.engine_helpers import make_call

CFG = AppConfig.model_validate({
    "engine": {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"]},
})


def uptrend_5m():
    # Clean higher-highs / higher-lows path, ~55 bars
    return zigzag([100, 104, 102, 107, 105, 111, 108, 114], bars_per_leg=8,
                  freq="5min")


def uptrend_1h():
    return zigzag([90, 98, 94, 104, 100, 112], bars_per_leg=9, freq="1h")


class TestAnalyzer:
    def test_builds_view_from_candles(self):
        df = uptrend_5m()
        views = MultiTimeframeAnalyzer(CFG).analyze({Timeframe.M5: df})
        v = views[Timeframe.M5]
        assert v.ts == df.index[-1].to_pydatetime()
        assert v.close == float(df["close"].iloc[-1])
        assert v.atr > 0
        assert v.trend is Trend.UP
        assert len(v.swings) >= 4
        assert v.last_event is not None       # BOS happened on the way up
        assert isinstance(v.patterns, tuple)

    def test_short_history_skipped(self):
        rng = np.random.default_rng(1)
        df = make_candles(100 + rng.normal(0, 1, 20))  # < MIN_BARS
        assert MultiTimeframeAnalyzer(CFG).analyze({Timeframe.M5: df}) == {}

    def test_disabled_indicators_are_nan(self):
        cfg = AppConfig.model_validate({
            "engine": {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"]},
            "indicators": {"rsi": False, "vwap": False},
        })
        v = MultiTimeframeAnalyzer(cfg).analyze({Timeframe.M5: uptrend_5m()})[Timeframe.M5]
        import math
        assert math.isnan(v.rsi) and v.above_vwap is None


class TestDecisionEngine:
    def test_bullish_market_produces_long_signal(self):
        engine = DecisionEngine(CFG)
        decision = engine.evaluate("SPY", {
            Timeframe.H1: uptrend_1h(), Timeframe.M5: uptrend_5m(),
        })
        assert decision.signal is not None
        assert decision.signal.direction is Direction.LONG
        assert decision.signal.confidence > 30
        assert decision.signal.reasons          # evidence trail present
        assert decision.entry_view.timeframe is Timeframe.M5

    def test_insufficient_data_is_no_signal(self):
        engine = DecisionEngine(CFG)
        rng = np.random.default_rng(2)
        decision = engine.evaluate("SPY", {
            Timeframe.M5: make_candles(100 + rng.normal(0, 1, 10)),
        })
        assert decision.signal is None and not decision.tradeable

    def test_full_pipeline_to_trade_plan(self):
        engine = DecisionEngine(CFG)
        df5 = uptrend_5m()
        spot = float(df5["close"].iloc[-1])
        decision = engine.evaluate("SPY", {
            Timeframe.H1: uptrend_1h(), Timeframe.M5: df5,
        })
        today = date(2026, 7, 10)
        chain = [
            make_call(round(spot) - 2, 0.58, expiration=today + timedelta(days=21)),
            make_call(round(spot), 0.47, expiration=today + timedelta(days=21)),
            make_call(round(spot) + 3, 0.33, expiration=today + timedelta(days=21)),
        ]
        plan = engine.build_plan(decision, chain, spot=spot, today=today)
        assert plan is not None
        assert plan.contract.delta == 0.47
        assert plan.stop_underlying < spot < plan.target_underlying
        assert plan.risk_reward > 0
        assert plan.signal.direction is Direction.LONG

    def test_tradeable_flag_respects_threshold(self):
        low_bar = AppConfig.model_validate({
            "engine": {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"],
                       "min_confidence": 1},
        })
        engine = DecisionEngine(low_bar)
        decision = engine.evaluate("SPY", {
            Timeframe.H1: uptrend_1h(), Timeframe.M5: uptrend_5m(),
        })
        assert decision.tradeable
