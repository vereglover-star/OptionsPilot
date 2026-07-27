"""Tests for the centralized AI decision snapshot (experience/snapshot.py)."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from optionspilot.analysis.structure import Trend
from optionspilot.core.models import (
    Direction, Evidence, OptionContract, OptionRight, Signal, Timeframe, TradePlan,
)
from optionspilot.engine.engine import EngineDecision
from optionspilot.engine.gate import GateReport
from optionspilot.experience.snapshot import build_snapshot
from tests.engine_helpers import bullish_entry_view, make_view

TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def make_decision(*, signal=True, gate=True):
    entry = bullish_entry_view()                       # 5m, everything up
    htf = make_view(timeframe=Timeframe.H1, trend=Trend.UP)
    views = {Timeframe.M5: entry, Timeframe.H1: htf}
    sig = None
    if signal:
        sig = Signal(
            symbol="SPY", ts=TS, direction=Direction.LONG, confidence=84.0,
            evidence=(
                Evidence("htf_trend", "1D and 4H up", 0.8, 2.0),
                Evidence("vwap", "above VWAP", 0.5, 0.8),
                Evidence("divergence", "bearish divergence", -0.3, 1.0),
            ),
            strategy="confluence_v1", timeframe=Timeframe.M5,
        )
    g = None
    if gate:
        g = GateReport(
            mode="conservative", setup_quality="good", confidence=84.0,
            min_confidence_required=80.0, accepted=True, reason="clears bar",
            confirmations_passed=("trend alignment", "volume"),
            confirmations_failed=(),
        )
    return EngineDecision(sig, bool(g and g.accepted), views, entry, g)


def make_plan(sig):
    con = OptionContract(
        underlying="SPY", expiration=date(2026, 8, 21), strike=100.0,
        right=OptionRight.CALL, bid=2.0, ask=2.1, delta=0.5,
        implied_volatility=0.35,
    )
    return TradePlan(signal=sig, contract=con, entry_price=2.05, spot=100.0,
                     stop_underlying=98.0, target_underlying=104.0,
                     risk_reward=2.0)


class TestBuildSnapshot:
    def test_captures_full_decision_context(self):
        d = make_decision()
        snap = build_snapshot(d, spot=100.0, operating_mode="ai",
                              trading_mode="conservative")
        assert snap["symbol"] == "SPY"
        assert snap["direction"] == "long"
        assert snap["confidence"] == 84.0
        assert snap["deterministic_score"] == 84.0
        assert snap["htf_trend"] == Trend.UP.value
        assert snap["operating_mode"] == "ai"
        assert snap["trading_mode"] == "conservative"
        assert snap["learning_mode"] == "normal"
        assert snap["bollinger"] is None           # not computed → never invented
        assert snap["reasoning"]

    def test_entry_timeframe_indicators(self):
        snap = build_snapshot(make_decision(), spot=100.0)
        tf = snap["entry_tf"]
        assert tf["rsi"] == 62.0
        assert tf["adx"] == 30.0
        assert tf["rvol"] == 1.8
        assert tf["pressure"] == 0.6
        assert tf["atr"] == 1.0
        assert tf["ema_stack"] == 1
        assert tf["above_vwap"] is True
        assert tf["macd_hist"] == pytest.approx(0.15)
        assert tf["supertrend_dir"] == 1

    def test_evidence_breakdown_and_supporting_names(self):
        snap = build_snapshot(make_decision(), spot=100.0)
        assert len(snap["evidence"]) == 3
        # only positively-scored evidence counts as supporting
        assert snap["evidence_names"] == ["htf_trend", "vwap"]
        assert snap["gate"]["setup_quality"] == "good"
        assert "trend alignment" in snap["gate"]["confirmations_passed"]

    def test_plan_fields_present(self):
        d = make_decision()
        snap = build_snapshot(d, spot=100.0, plan=make_plan(d.signal))
        assert snap["stop"] == 98.0
        assert snap["target"] == 104.0
        assert snap["entry"] == 2.05
        assert snap["risk_reward"] == 2.0
        assert snap["contract"]["delta"] == 0.5
        assert snap["contract"]["iv"] == pytest.approx(0.35)
        assert snap["contract"]["spread_pct"] is not None

    def test_no_signal_is_safe(self):
        snap = build_snapshot(make_decision(signal=False, gate=False), spot=100.0)
        assert snap["direction"] == "unknown"
        assert snap["confidence"] == 0.0
        assert snap["evidence"] == []
        # entry-timeframe indicators are still captured from the view
        assert snap["entry_tf"]["rsi"] == 62.0

    def test_duck_typed_empty_decision_never_raises(self):
        fake = SimpleNamespace(signal=None, gate=None, entry_view=None, views={})
        snap = build_snapshot(fake)
        assert snap["direction"] == "unknown"
        assert snap["htf_trend"] is None
        assert snap["entry_tf"] == {}
