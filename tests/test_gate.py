import pytest
from pydantic import ValidationError

from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Direction, Evidence
from optionspilot.engine.gate import TradeGate, stretch_rr_ok
from optionspilot.engine.scorer import ScoreResult


def ev(name, score, weight=1.0):
    return Evidence(name=name, detail=f"{name} detail", score=score, weight=weight)


def result(evidence, confidence, direction=Direction.LONG):
    return ScoreResult(direction=direction, confidence=confidence,
                       net=confidence / 100, evidence=tuple(evidence))


def gate(mode="high_risk", **engine_overrides) -> TradeGate:
    cfg = AppConfig.model_validate({"engine": {"trading_mode": mode,
                                               **engine_overrides}})
    return TradeGate(cfg.engine)


# Canonical setups used across tests
EXCELLENT = [ev("htf_trend", 0.8), ev("structure_break", 1.0),
             ev("volume_pressure", 0.6), ev("momentum_macd", 0.9),
             ev("momentum_rsi", 0.5), ev("trend_strength", 0.5),
             ev("range_position", 0.4)]
GOOD = [ev("htf_trend", 0.4), ev("structure_break", 0.6),
        ev("volume_pressure", 0.3), ev("momentum_macd", 0.4),
        ev("candlestick", -0.4)]                       # one conflict
AVERAGE = [ev("htf_trend", 0.2), ev("structure_break", 0.6),
           ev("volume_pressure", 0.3), ev("momentum_macd", 0.4)]
CONFLICTED = [ev("htf_trend", 0.3), ev("structure_break", 0.5),
              ev("volume_pressure", -0.4), ev("momentum_macd", -0.5),
              ev("divergence", -1.0)]                  # three conflicts
HTF_OPPOSING = [ev("htf_trend", -0.6), ev("structure_break", 1.0),
                ev("volume_pressure", 0.6), ev("momentum_macd", 0.7)]


class TestSetupQuality:
    def test_excellent_lowers_bar_to_62(self):
        r = gate().assess(result(EXCELLENT, 72.0))
        assert r.setup_quality == "excellent"
        assert r.min_confidence_required == 62.0
        assert r.accepted
        assert "excellent setup" in r.reason and "≥" in r.reason

    def test_good_needs_70(self):
        g = gate()
        ok = g.assess(result(GOOD, 72.0))
        assert ok.setup_quality == "good" and ok.min_confidence_required == 70.0
        assert ok.accepted
        no = g.assess(result(GOOD, 68.0))
        assert not no.accepted and "68.0% <" in no.reason

    def test_average_needs_77(self):
        r = gate().assess(result(AVERAGE, 75.0))
        assert r.setup_quality == "average"
        assert r.min_confidence_required == 77.0
        assert not r.accepted

    def test_conflicting_indicators_are_poor(self):
        # 65% confidence but conflicting indicators -> skip (spec example)
        r = gate().assess(result(CONFLICTED, 65.0))
        assert r.setup_quality == "poor"
        assert r.min_confidence_required is None
        assert not r.accepted
        assert "poor setup" in r.reason

    def test_poor_never_trades_at_any_confidence(self):
        r = gate().assess(result(HTF_OPPOSING, 92.0))
        assert r.setup_quality == "poor" and not r.accepted

    def test_confirmations_are_itemized(self):
        r = gate().assess(result(GOOD, 72.0))
        assert any(p.startswith("trend alignment") for p in r.confirmations_passed)
        assert any(f.startswith("candlestick") for f in r.confirmations_failed)
        d = r.to_dict()
        assert d["setup_quality"] == "good" and d["accepted"] is True
        assert isinstance(d["confirmations_passed"], list)


class TestThresholdBounds:
    def test_floor_is_respected(self):
        r = gate(high_risk_floor=70).assess(result(EXCELLENT, 65.0))
        assert r.min_confidence_required == 70.0 and not r.accepted

    def test_never_stricter_than_conservative_base(self):
        r = gate(min_confidence=60).assess(result(AVERAGE, 60.0))
        # average would be 60-3=57, floored at 60, capped at base 60
        assert r.min_confidence_required == 60.0 and r.accepted


class TestConservativeMode:
    def test_fixed_bar_unchanged(self):
        g = gate(mode="conservative")
        below = g.assess(result(EXCELLENT, 79.9))
        assert not below.accepted and below.min_confidence_required == 80.0
        at = g.assess(result(AVERAGE, 80.0))
        assert at.accepted                    # quality never blocks conservative
        assert below.setup_quality == "excellent"  # still reported for display


class TestStretchRR:
    def cfg(self, mode="high_risk"):
        return AppConfig.model_validate({"engine": {"trading_mode": mode}}).engine

    def test_stretch_entry_needs_better_rr(self):
        assert not stretch_rr_ok(self.cfg(), confidence=70.0, risk_reward=1.6)
        assert stretch_rr_ok(self.cfg(), confidence=70.0, risk_reward=2.4)

    def test_full_confidence_uses_normal_rr(self):
        assert stretch_rr_ok(self.cfg(), confidence=85.0, risk_reward=1.6)

    def test_conservative_mode_is_unaffected(self):
        assert stretch_rr_ok(self.cfg("conservative"), 70.0, 1.6)


class TestConfig:
    def test_unknown_mode_rejected(self):
        with pytest.raises(ValidationError, match="trading_mode"):
            AppConfig.model_validate({"engine": {"trading_mode": "yolo"}})

    def test_defaults_stay_conservative(self):
        assert AppConfig().engine.trading_mode == "conservative"
