"""Tests for the SimilarityEngine: the weighted-distance metric, top-k search,
cohort aggregation, and the advisory confidence calibration."""

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.experience.models import ExperienceRecord
from optionspilot.experience.features import build_feature_vector
from optionspilot.experience.similarity import SimilarityEngine, similarity
from optionspilot.experience.store import ExperienceStore

TS = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def exp(tid, *, direction="long", is_win=True, pnl=100.0, return_pct=50.0,
        hold_minutes=60.0, exit_reason="target reached", setup_quality="good",
        htf_trend="up", timeframe="5m", evidence=("htf_trend", "vwap"),
        confidence=80.0, rsi=55.0, adx=25.0, mistakes=()) -> ExperienceRecord:
    rec = ExperienceRecord(
        trade_id=tid, recorded_ts=TS, symbol="SPY",
        contract_symbol="SPY260731C00100000", direction=direction,
        strategy="confluence_v1", managed_by="ai", quantity=1,
        entry_ts=TS, entry_price=2.0, exit_ts=TS + timedelta(hours=1),
        exit_price=3.0, pnl=pnl, return_pct=return_pct, is_win=is_win,
        hold_minutes=hold_minutes, exit_reason=exit_reason, timeframe=timeframe,
        confidence_entry=confidence, setup_quality=setup_quality,
        htf_trend=htf_trend, rsi=rsi, adx=adx,
        evidence_names=list(evidence), mistakes=list(mistakes),
    )
    rec.features = build_feature_vector(rec)
    return rec


class TestMetric:
    def test_identical_is_one(self):
        a, b = exp("a"), exp("b")
        assert similarity(a, b) == pytest.approx(1.0)

    def test_opposite_direction_is_low(self):
        a = exp("a", direction="long")
        b = exp("b", direction="short")
        # Direction carries the heaviest single weight, so flipping it clearly
        # reduces similarity from an otherwise-identical setup (and in practice
        # restrict_direction prunes opposite-direction candidates entirely).
        assert similarity(a, b) < similarity(a, exp("c", direction="long"))
        assert similarity(a, b) < 0.8

    def test_different_evidence_reduces_similarity(self):
        a = exp("a", evidence=("htf_trend", "vwap"))
        b = exp("b", evidence=("liquidity_grab", "divergence"))
        assert similarity(a, b) < similarity(a, exp("c"))

    def test_missing_features_are_not_penalized(self):
        a = exp("a")
        b = exp("b", rsi=None, adx=None)
        # b simply carries no info on those axes; still highly similar
        assert similarity(a, b) > 0.9

    def test_incomparable_returns_zero(self):
        a = ExperienceRecord(
            trade_id="a", recorded_ts=TS, symbol="SPY", contract_symbol="x",
            direction="", strategy="s", managed_by="ai", quantity=1,
            entry_ts=TS, entry_price=1.0, exit_ts=TS, exit_price=1.0,
            pnl=0.0, return_pct=0.0, is_win=False, hold_minutes=0.0,
            exit_reason="", setup_quality=None, htf_trend=None, timeframe=None,
        )
        b = exp("b")
        assert similarity(a, b) == 0.0


class TestFindSimilar:
    def _engine(self, tmp_path, recs):
        store = ExperienceStore(tmp_path / "exp.db")
        for r in recs:
            store.record(r)
        return SimilarityEngine(store)

    def test_ranks_and_excludes_self(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("self"),
            exp("twin"),  # identical setup
            exp("diff", evidence=("liquidity_grab",), htf_trend="down"),
        ])
        matches = eng.find_similar(exp("self"), min_similarity=0.0)
        ids = [m.trade_id for m, _ in matches]
        assert "self" not in ids           # never matches itself
        assert ids[0] == "twin"            # most similar first

    def test_min_similarity_threshold(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("twin"),
            exp("weak", evidence=("liquidity_grab",), htf_trend="down",
                setup_quality="poor"),
        ])
        strong = eng.find_similar(exp("q"), min_similarity=0.9)
        assert [m.trade_id for m, _ in strong] == ["twin"]

    def test_restrict_direction_prunes(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("long1", direction="long"),
            exp("short1", direction="short"),
        ])
        matches = eng.find_similar(exp("q", direction="long"),
                                   min_similarity=0.0, restrict_direction=True)
        assert [m.trade_id for m, _ in matches] == ["long1"]


class TestSummarize:
    def _engine(self, tmp_path, recs):
        store = ExperienceStore(tmp_path / "exp.db")
        for r in recs:
            store.record(r)
        return SimilarityEngine(store)

    def test_empty_history_returns_raw_confidence(self, tmp_path):
        eng = self._engine(tmp_path, [])
        res = eng.summarize(exp("q", confidence=76.0))
        assert res.n_similar == 0
        assert not res.has_evidence
        assert res.calibrated_confidence == 76.0
        assert "model estimate only" in res.explain(76.0)

    def test_aggregates_cohort(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("w1", is_win=True, return_pct=40.0, hold_minutes=30.0),
            exp("w2", is_win=True, return_pct=60.0, hold_minutes=90.0),
            exp("l1", is_win=False, return_pct=-100.0, hold_minutes=60.0,
                exit_reason="stop loss"),
        ])
        res = eng.summarize(exp("q"), min_similarity=0.0)
        assert res.n_similar == 3
        assert res.win_rate == pytest.approx(2 / 3, abs=1e-3)
        assert res.avg_return_pct == pytest.approx(0.0)
        assert res.avg_hold_minutes == pytest.approx(60.0)
        # only loser exited via stop loss -> that's the failure mode
        assert res.typical_failure_mode == "stop loss"

    def test_failure_mode_falls_back_to_mistake(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("l1", is_win=False, exit_reason="", mistakes=("chased_entry",)),
        ])
        res = eng.summarize(exp("q"), min_similarity=0.0)
        assert res.typical_failure_mode == "chased_entry"

    def test_calibration_small_sample_stays_near_raw(self, tmp_path):
        eng = self._engine(tmp_path, [exp("l1", is_win=False)])
        res = eng.summarize(exp("q", confidence=80.0), min_similarity=0.0)
        # one losing sample should barely move an 80% model estimate
        assert 74.0 <= res.calibrated_confidence <= 80.0

    def test_calibration_large_consistent_cohort_moves(self, tmp_path):
        recs = [exp(f"w{i}", is_win=True) for i in range(40)]
        eng = self._engine(tmp_path, recs)
        res = eng.summarize(exp("q", confidence=80.0), min_similarity=0.0, k=100)
        # 40 winners at a 100% historical rate should lift the number well above raw
        assert res.calibrated_confidence > 90.0
        assert res.n_similar == 40
        assert "40 historical" in res.explain(80.0)

    def test_common_success_and_failure_patterns(self, tmp_path):
        eng = self._engine(tmp_path, [
            exp("w1", is_win=True, exit_reason="target reached",
                evidence=("htf_trend", "vwap")),
            exp("w2", is_win=True, exit_reason="target reached",
                evidence=("htf_trend",)),
            exp("l1", is_win=False, exit_reason="stop loss",
                mistakes=("chased_entry",)),
        ])
        res = eng.summarize(exp("q"), min_similarity=0.0)
        assert "target reached" in res.common_successes
        # supporting evidence rendered into a readable phrase
        assert any("trend alignment" in s for s in res.common_successes)
        assert "stop loss" in res.common_failures
