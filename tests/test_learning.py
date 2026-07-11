import json
from datetime import timedelta

import pytest

from optionspilot.config.settings import AppConfig
from optionspilot.engine.scorer import DEFAULT_WEIGHTS, ConfluenceScorer
from optionspilot.journal import TradeJournal
from optionspilot.learning import LearningEngine, WeightStore
from tests.test_journal import TS, make_trade


def seeded_journal(tmp_path) -> TradeJournal:
    """40 trades: 'vwap' evidence appears on 20 (16 winners — strong),
    'divergence' on the other 20 (6 winners — weak)."""
    j = TradeJournal(tmp_path / "j.db")
    n = 0
    for i in range(20):
        pnl = 100.0 if i < 16 else -80.0
        j.record(make_trade(f"v{i}", pnl, entry_ts=TS + timedelta(hours=n),
                            indicators=("vwap", "htf_trend")))
        n += 1
    for i in range(20):
        pnl = 100.0 if i < 6 else -80.0
        j.record(make_trade(f"d{i}", pnl, entry_ts=TS + timedelta(hours=n),
                            indicators=("divergence",)))
        n += 1
    return j


class TestSlicing:
    def test_by_evidence(self, tmp_path):
        eng = LearningEngine(seeded_journal(tmp_path), min_sample=10)
        stats = {s.label: s for s in eng.by_evidence()}
        assert stats["vwap"].trades == 20
        assert stats["vwap"].win_rate == pytest.approx(0.8)
        assert stats["divergence"].win_rate == pytest.approx(0.3)
        # best expectancy sorts first
        labels = [s.label for s in eng.by_evidence()]
        assert labels.index("vwap") < labels.index("divergence")

    def test_by_hour_and_dte_and_confidence(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        j.record(make_trade("a", 50.0))     # 15:00 UTC = 11:00 ET, dte 21, conf 85
        j.record(make_trade("b", -20.0, entry_ts=TS + timedelta(hours=3)))
        eng = LearningEngine(j, min_sample=1)
        assert any(s.label == "11:00 ET" for s in eng.by_hour_et())
        assert any(s.label == "15-30 DTE" for s in eng.by_dte())
        assert any(s.label == "80-89%" for s in eng.by_confidence())


class TestWeightRecommendation:
    def test_moves_weights_by_lift_with_bounds(self, tmp_path):
        eng = LearningEngine(seeded_journal(tmp_path), min_sample=10)
        weights, rationale = eng.recommend_weights()
        # baseline 22/40 = 55%; vwap 80% -> +20% (capped); divergence 30% -> -20%
        assert weights["vwap"] == pytest.approx(DEFAULT_WEIGHTS["vwap"] * 1.2)
        assert weights["divergence"] == pytest.approx(DEFAULT_WEIGHTS["divergence"] * 0.8)
        assert any("vwap" in r for r in rationale)

    def test_small_samples_do_not_move(self, tmp_path):
        eng = LearningEngine(seeded_journal(tmp_path), min_sample=10)
        weights, rationale = eng.recommend_weights()
        # htf_trend appeared on 20 trades (with vwap) -> moves; candlestick on 0
        assert weights["candlestick"] == DEFAULT_WEIGHTS["candlestick"]

    def test_insufficient_history_is_a_noop(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        j.record(make_trade("only", 100.0))
        weights, rationale = LearningEngine(j, min_sample=20).recommend_weights()
        assert weights == DEFAULT_WEIGHTS
        assert "insufficient history" in rationale[0]

    def test_range_bounds_hold_over_many_cycles(self, tmp_path):
        eng = LearningEngine(seeded_journal(tmp_path), min_sample=10)
        weights = dict(DEFAULT_WEIGHTS)
        for _ in range(20):   # 20 learning cycles of the same lucky data
            weights, _ = eng.recommend_weights(weights)
        assert weights["vwap"] <= DEFAULT_WEIGHTS["vwap"] * 2.0 + 1e-9
        assert weights["divergence"] >= DEFAULT_WEIGHTS["divergence"] * 0.25 - 1e-9


class TestWeightStore:
    def test_versioned_persistence(self, tmp_path):
        store = WeightStore(tmp_path / "learning" / "weights.json")
        assert store.current() == {} and store.version() == 0
        v1 = store.save({"vwap": 0.9}, ["first pass"])
        v2 = store.save({"vwap": 1.0}, ["second pass"])
        assert (v1, v2) == (1, 2)
        assert store.current() == {"vwap": 1.0}
        doc = json.loads((tmp_path / "learning" / "weights.json").read_text())
        assert len(doc["history"]) == 2
        assert doc["history"][0]["rationale"] == ["first pass"]


class TestScorerIntegration:
    def test_learned_weights_flow_into_scorer(self):
        cfg = AppConfig()
        s = ConfluenceScorer(cfg.engine, cfg.indicators,
                             learned_weights={"htf_trend": 3.5, "not_a_key": 9.0})
        assert s.weights["htf_trend"] == 3.5          # applied
        assert "not_a_key" not in s.weights           # unknown learned keys dropped

    def test_config_overrides_learned(self):
        cfg = AppConfig.model_validate({
            "engine": {"evidence_weights": {"htf_trend": 2.5}},
        })
        s = ConfluenceScorer(cfg.engine, cfg.indicators,
                             learned_weights={"htf_trend": 3.5})
        assert s.weights["htf_trend"] == 2.5          # human wins
