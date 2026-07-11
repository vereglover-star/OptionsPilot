import pytest

from optionspilot.analysis.structure import Trend
from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Direction, Timeframe
from optionspilot.engine.scorer import DEFAULT_WEIGHTS, ConfluenceScorer
from tests.engine_helpers import bearish_entry_view, bullish_entry_view, htf_view


def cfg(**engine_overrides) -> AppConfig:
    engine = {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"]}
    engine.update(engine_overrides)
    return AppConfig.model_validate({"engine": engine})


def scorer(**engine_overrides) -> ConfluenceScorer:
    c = cfg(**engine_overrides)
    return ConfluenceScorer(c.engine, c.indicators)


class TestScoring:
    def test_strong_bullish_confluence(self):
        views = {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        res = scorer().score(views)
        assert res.direction is Direction.LONG
        assert res.confidence > 75
        assert len(res.reasons) >= 10
        # supporting evidence dominates
        assert sum(1 for e in res.evidence if e.score > 0) > len(res.evidence) * 0.8

    def test_strong_bearish_confluence(self):
        views = {Timeframe.H1: htf_view(False), Timeframe.M5: bearish_entry_view()}
        res = scorer().score(views)
        assert res.direction is Direction.SHORT
        assert res.confidence > 75
        # presented scores support the SHORT (positive = supports the trade)
        assert sum(1 for e in res.evidence if e.score > 0) > len(res.evidence) * 0.8

    def test_neutral_market_scores_low(self):
        views = {
            Timeframe.H1: htf_view(True),
            Timeframe.M5: bullish_entry_view(
                trend=Trend.RANGE,
                last_event=None, bars_since_event=None,
                rsi=50.0, macd_hist=0.0, macd_hist_prev=0.0,
                adx=15.0, supertrend_dir=-1, ema_stack=0, above_vwap=False,
                rvol=0.9, pressure=-0.1, patterns=(),
                range_ctx=None, open_zones=(),
            ),
        }
        res = scorer().score(views)
        assert res.confidence < 50

    def test_conflicting_timeframes_reduce_confidence(self):
        aligned = scorer().score(
            {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        )
        conflicted = scorer().score(
            {Timeframe.H1: htf_view(False), Timeframe.M5: bullish_entry_view()}
        )
        assert conflicted.confidence < aligned.confidence

    def test_no_entry_timeframe_returns_none(self):
        assert scorer().score({Timeframe.H1: htf_view(True)}) is None

    def test_consolidation_damps_confidence(self):
        base = scorer().score(
            {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        )
        damped = scorer().score(
            {Timeframe.H1: htf_view(True),
             Timeframe.M5: bullish_entry_view(consolidating=True)}
        )
        assert damped.confidence == pytest.approx(base.confidence * 0.75, abs=0.2)
        assert any(e.name == "consolidation" for e in damped.evidence)

    def test_reasons_are_ranked_and_readable(self):
        res = scorer().score(
            {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        )
        assert all(r.startswith(("+", "-")) for r in res.reasons)
        impacts = [abs(e.score * e.weight)
                   for r in res.reasons
                   for e in res.evidence if r.endswith(e.detail)]
        assert impacts == sorted(impacts, reverse=True)


class TestWeights:
    def test_override_changes_score(self):
        views = {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        base = scorer().score(views)
        boosted = scorer(evidence_weights={"htf_trend": 5.0}).score(views)
        assert boosted.confidence != base.confidence

    def test_unknown_weight_key_rejected(self):
        with pytest.raises(ValueError, match="Unknown evidence_weights"):
            scorer(evidence_weights={"htf_trnd": 2.0})

    def test_negative_weight_rejected_by_config(self):
        with pytest.raises(Exception, match="must be >= 0"):
            cfg(evidence_weights={"htf_trend": -1.0})


class TestIndicatorGating:
    def test_disabled_indicator_emits_no_evidence(self):
        views = {Timeframe.H1: htf_view(True), Timeframe.M5: bullish_entry_view()}
        c = AppConfig.model_validate({
            "engine": {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"]},
            "indicators": {"rsi": False, "macd": False},
        })
        res = ConfluenceScorer(c.engine, c.indicators).score(views)
        names = {e.name for e in res.evidence}
        assert "momentum_rsi" not in names and "momentum_macd" not in names
        assert DEFAULT_WEIGHTS.keys() >= names - {"consolidation"}
