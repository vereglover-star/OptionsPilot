"""TradingIntelligence — the façade, its cache, and the guarantees that make it
safe to attach to a live trading application.

The two properties that matter most operationally:

* **A failed analysis never raises.** This is advisory intelligence bolted onto
  something that manages money. A malformed review file must cost the user a
  dashboard panel, not their session.
* **A stale verdict is worse than a slow one.** The cache is keyed on a
  fingerprint the caller owns, and a failure is never cached as though it were
  an answer.

Scale and edge-case coverage lives here too: zero trades, one trade, thousands
of trades, a history with no reviews at all, and a history where every source
disagrees.
"""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.intelligence import (
    IntelligenceStore, TradingIntelligence, build_evidence_index, confidence_of,
)
from optionspilot.intelligence.facts import FactSet
from optionspilot.intelligence.models import Confidence, Goal
from optionspilot.intelligence.windows import RECENT_WINDOW, resolve

from tests.intelligence_helpers import BASE, fact, factset, series


def engine(facts, *, store=None, fingerprint=None):
    return TradingIntelligence(lambda: factset(list(facts)),
                               fingerprint_provider=fingerprint, store=store)


class TestEmptyAndTiny:
    def test_zero_trades_produces_a_complete_renderable_snapshot(self):
        """Every consumer renders a fixed shape; a missing key is a hole in the
        page, while a None value is a legible 'not enough data'."""
        snapshot = engine([]).snapshot()
        assert snapshot.trades_analyzed == 0
        assert snapshot.data_sufficiency == "none"
        assert snapshot.metrics                     # full registry, all None
        assert snapshot.notes
        assert json.dumps(snapshot.to_dict())

    def test_one_trade_says_so_rather_than_drawing_conclusions(self):
        snapshot = engine(series(1)).snapshot()
        assert snapshot.trades_analyzed == 1
        assert snapshot.data_sufficiency == "none"
        assert any("provisional" in note for note in snapshot.notes)
        assert snapshot.patterns == ()
        assert snapshot.recommendations == ()

    def test_sufficiency_bands_climb_with_history(self):
        assert engine(series(4)).snapshot().data_sufficiency == "minimal"
        assert engine(series(15)).snapshot().data_sufficiency == "partial"
        assert engine(series(40)).snapshot().data_sufficiency == "good"

    def test_a_new_user_gets_an_actionable_note_not_an_error(self):
        note = " ".join(engine([]).snapshot().notes)
        assert "Close a round trip" in note


class TestPipeline:
    def test_a_full_history_populates_every_output(self):
        facts = series(80, wins="WWL", spacing_days=2)
        snapshot = engine(facts).snapshot()
        assert snapshot.metrics and snapshot.periods
        assert snapshot.behaviors and snapshot.scores
        assert snapshot.achievements
        assert snapshot.span_start and snapshot.span_end

    def test_behaviour_reflects_the_recent_window_not_all_history(self):
        """A habit the trader has stopped drops off as newer clean trades push
        it out — which is the point of an action plan."""
        window = resolve(RECENT_WINDOW).count or 50
        old = series(window, had_stop=False, prefix="OLD")
        new = series(window, prefix="NEW",
                     start=BASE + timedelta(days=window + 10))
        snapshot = engine(old + new).snapshot()
        finding = snapshot.behavior("trading_without_stops")
        assert finding.occurrences == 0

    def test_recommendations_and_lessons_agree_with_the_behaviours(self):
        facts = series(60, had_stop=False, spacing_days=1)
        snapshot = engine(facts).snapshot()
        assert any(r.id.endswith("trading_without_stops")
                   for r in snapshot.recommendations)
        assert any(x.lesson_id == "stops_that_hold" for x in snapshot.lessons)

    def test_reports_are_generated_for_week_and_month(self):
        snapshot = engine(series(60, spacing_days=2)).snapshot()
        assert {r.kind for r in snapshot.reports} == {"weekly", "monthly"}

    def test_the_whole_snapshot_serialises_to_valid_json(self):
        """A payload `json.dumps` would emit as `Infinity` or `NaN` breaks a
        browser parse. Profit factor reaches infinity legitimately."""
        facts = series(60, wins="WWWWWWWWWW")   # no losers at all
        payload = json.dumps(engine(facts).snapshot().to_dict())
        assert "Infinity" not in payload and "NaN" not in payload
        assert json.loads(payload)

    def test_analysis_is_deterministic(self):
        facts = series(60, wins="WWL", spacing_days=2)
        a = engine(facts).snapshot().to_dict()
        b = engine(facts).snapshot().to_dict()
        a.pop("generated"), b.pop("generated")
        for report in a["reports"] + b["reports"]:
            report.pop("generated")
        assert a == b


class TestCaching:
    def test_an_unchanged_fingerprint_reuses_the_snapshot(self):
        calls = []

        def facts():
            calls.append(1)
            return factset(series(20))

        intel = TradingIntelligence(facts, fingerprint_provider=lambda: "v1")
        first = intel.snapshot()
        assert intel.snapshot() is first
        assert len(calls) == 1

    def test_a_changed_fingerprint_recomputes(self):
        version = ["v1"]
        intel = TradingIntelligence(lambda: factset(series(20)),
                                    fingerprint_provider=lambda: version[0])
        first = intel.snapshot()
        version[0] = "v2"
        assert intel.snapshot() is not first

    def test_no_fingerprint_means_never_reuse(self):
        """Serving a stale verdict about a trade the user just closed is the
        one failure mode this cache must not have."""
        intel = engine(series(20))
        assert intel.snapshot() is not intel.snapshot()

    def test_force_bypasses_the_cache(self):
        intel = TradingIntelligence(lambda: factset(series(20)),
                                    fingerprint_provider=lambda: "v1")
        first = intel.snapshot()
        assert intel.snapshot(force=True) is not first

    def test_invalidate_drops_the_cache(self):
        intel = TradingIntelligence(lambda: factset(series(20)),
                                    fingerprint_provider=lambda: "v1")
        first = intel.snapshot()
        intel.invalidate()
        assert intel.snapshot() is not first

    def test_background_refresh_completes(self):
        intel = TradingIntelligence(lambda: factset(series(30)),
                                    fingerprint_provider=lambda: "v1")
        intel.refresh_in_background().join(timeout=30)
        assert intel.snapshot().trades_analyzed == 30

    def test_concurrent_reads_are_serialised(self):
        intel = TradingIntelligence(lambda: factset(series(40)),
                                    fingerprint_provider=lambda: "v1")
        results = []
        threads = [threading.Thread(target=lambda: results.append(intel.snapshot()))
                   for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(results) == 6
        assert all(r is results[0] for r in results)


class TestFailureIsolation:
    def test_a_broken_fact_provider_never_raises(self):
        """Advisory analysis attached to a trading app. It must not be able to
        take down the session."""
        def explode():
            raise RuntimeError("experience.db is a directory somehow")

        snapshot = TradingIntelligence(explode).snapshot()
        assert snapshot.trades_analyzed == 0
        assert any("could not be completed" in n for n in snapshot.notes)

    def test_a_failure_is_never_cached_as_an_answer(self):
        state = {"broken": True}

        def facts():
            if state["broken"]:
                raise RuntimeError("boom")
            return factset(series(10))

        intel = TradingIntelligence(facts, fingerprint_provider=lambda: "v1")
        assert intel.snapshot().trades_analyzed == 0
        state["broken"] = False
        assert intel.snapshot().trades_analyzed == 10

    def test_duration_is_recorded_even_for_a_failure(self):
        intel = TradingIntelligence(lambda: (_ for _ in ()).throw(ValueError()))
        intel.snapshot()
        assert intel.last_duration_ms >= 0


class TestGoals:
    def test_goals_round_trip_through_the_store(self, tmp_path):
        store = IntelligenceStore(tmp_path)
        intel = engine(series(30), store=store)
        goal = Goal("g1", "R above 1", "avg_r", ">=", 1.0, "lifetime", "R")
        intel.add_goal(goal)
        assert [g.id for g in intel.list_goals()] == ["g1"]
        assert any(g.goal.id == "g1" for g in intel.snapshot().goals)

    def test_an_invalid_goal_is_refused_with_a_reason(self, tmp_path):
        intel = engine(series(10), store=IntelligenceStore(tmp_path))
        with pytest.raises(ValueError, match="unknown metric"):
            intel.add_goal(Goal("g", "x", "not_a_metric", ">=", 1.0))

    def test_adding_a_goal_invalidates_the_cache(self, tmp_path):
        intel = TradingIntelligence(lambda: factset(series(20)),
                                    fingerprint_provider=lambda: "v1",
                                    store=IntelligenceStore(tmp_path))
        intel.snapshot()
        intel.add_goal(Goal("g", "x", "expectancy", ">=", 0.0))
        assert any(g.goal.id == "g" for g in intel.snapshot().goals)

    def test_removing_a_goal(self, tmp_path):
        intel = engine(series(10), store=IntelligenceStore(tmp_path))
        intel.add_goal(Goal("g", "x", "expectancy", ">=", 0.0))
        assert intel.remove_goal("g") is True
        assert intel.remove_goal("g") is False

    def test_goals_are_shown_before_the_first_trade(self, tmp_path):
        """A goal that vanishes on save looks like a bug and reads like one."""
        intel = engine([], store=IntelligenceStore(tmp_path))
        intel.add_goal(Goal("g", "R above 2", "avg_r", ">=", 2.0,
                            "last_20_trades", "R"))
        goals = intel.snapshot().goals
        assert len(goals) == 1
        assert goals[0].current is None
        assert "not measurable" in goals[0].detail.lower()

    def test_goal_operations_are_a_no_op_without_a_store(self):
        intel = engine(series(10))
        assert intel.remove_goal("anything") is False
        with pytest.raises(ValueError):
            intel.add_goal(Goal("g", "x", "expectancy", ">=", 0.0))


class TestTradeInsight:
    def test_an_unknown_trade_is_reported_honestly(self):
        result = engine(series(20)).trade_insight("nope")
        assert result["available"] is False
        assert result["reason"]

    def test_a_known_trade_is_projected_from_the_snapshot(self):
        facts = series(60, wins="WWL", spacing_days=2)
        intel = engine(facts)
        result = intel.trade_insight(facts[10].trade_id)
        assert result["available"] is True
        assert result["percentile"] is not None
        assert result["observations"]

    def test_it_names_the_habits_the_trade_is_evidence_for(self):
        facts = series(40, had_stop=False, spacing_days=1)
        intel = engine(facts)
        result = intel.trade_insight(facts[-1].trade_id)
        assert any(b["id"] == "trading_without_stops" for b in result["behaviors"])

    def test_an_undetected_comparison_is_not_listed_as_evidence(self):
        """The comparative detectors (tilt, overconfidence) cite their whole
        cohort even when the comparison came out clean. Without a `detected`
        filter an ordinary trade gets listed as evidence for "Tilt after a
        loss — 0 of 50 trades (0%)"."""
        facts = series(60, wins="WWL", spacing_days=1)
        result = engine(facts).trade_insight(facts[-1].trade_id)
        assert all(b["occurrences"] > 0 for b in result["behaviors"])

    def test_the_cohort_is_comparable_trades_not_all_trades(self):
        facts = series(30, symbol="SPY", direction="long")
        facts += series(10, symbol="QQQ", direction="short", prefix="Q",
                        start=BASE + timedelta(days=60))
        result = engine(facts).trade_insight("Q000")
        assert result["cohort"]["trades"] == 9
        assert "QQQ" in result["cohort"]["label"]

    def test_observations_judge_process_not_outcome(self):
        """A winning trade taken badly still reads badly, matching the coach."""
        facts = series(30)
        facts[5] = fact(facts[5].trade_id, entry=facts[5].entry_ts, pnl=5000,
                        had_stop=False)
        result = engine(facts).trade_insight(facts[5].trade_id)
        kinds = {o["kind"] for o in result["observations"]}
        assert "warning" in kinds

    def test_no_second_analysis_is_run(self):
        """A per-trade analysis pass would be exactly the duplicated
        intelligence this package was built to eliminate."""
        facts = series(30)
        intel = TradingIntelligence(lambda: factset(facts),
                                    fingerprint_provider=lambda: "v1")
        snapshot = intel.snapshot()
        before = intel.last_duration_ms
        intel.trade_insight(facts[0].trade_id, snapshot)
        assert intel.last_duration_ms == before


class TestHelpers:
    def test_evidence_index_maps_trades_to_the_findings_citing_them(self):
        facts = series(40, had_stop=False)
        index = build_evidence_index(engine(facts).snapshot())
        assert index
        assert all(isinstance(v, list) and v for v in index.values())

    def test_evidence_index_is_empty_for_a_clean_history(self):
        assert build_evidence_index(engine(series(10)).snapshot()) == {}

    def test_overall_confidence_is_the_weakest_link(self):
        assert confidence_of(engine([]).snapshot()) is Confidence.NONE
        assert confidence_of(engine(series(60)).snapshot()) is not Confidence.NONE


class TestScale:
    """Large, ugly and adversarial datasets. The engine is on a UI request
    path, so 'it finishes' is a correctness property, not a nicety."""

    @staticmethod
    def _big(n: int, seed: int = 11):
        rng = random.Random(seed)
        start = datetime(2020, 1, 6, 15, 0, tzinfo=timezone.utc)
        out = []
        for i in range(n):
            won = rng.random() < 0.45
            out.append(fact(
                f"T{i:06d}",
                pnl=round(rng.uniform(20, 500) if won else -rng.uniform(20, 400), 2),
                entry=start + timedelta(minutes=37 * i),
                hold_minutes=rng.choice([15, 45, 120, 400]),
                symbol=rng.choice(["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]),
                direction=rng.choice(["long", "short"]),
                setup_quality=rng.choice(["excellent", "good", "average", "poor"]),
                outlay=round(rng.uniform(100, 1200), 2),
                had_stop=rng.random() < 0.8,
                mistakes=("no_stop",) if rng.random() < 0.2 else (),
            ))
        return out

    def test_five_thousand_trades_analyse_in_reasonable_time(self):
        facts = self._big(5_000)
        intel = engine(facts)
        started = time.perf_counter()
        snapshot = intel.snapshot()
        elapsed = time.perf_counter() - started
        assert snapshot.trades_analyzed == 5_000
        # Generous: this runs on CI hardware of unknown speed. The point is to
        # catch an accidental O(n²) — the revenge-window scan was written with a
        # binary search precisely because the naive version is quadratic.
        assert elapsed < 30, f"analysis took {elapsed:.1f}s"

    def test_scaling_is_not_quadratic(self):
        small = self._big(1_000)
        large = self._big(4_000)
        t0 = time.perf_counter(); engine(small).snapshot()
        small_time = time.perf_counter() - t0
        t0 = time.perf_counter(); engine(large).snapshot()
        large_time = time.perf_counter() - t0
        # 4× the data must not cost 16× the time. A wide bound so a noisy CI
        # box doesn't produce flakes; a genuine O(n²) blows straight past it.
        assert large_time < small_time * 10 + 5

    def test_a_large_payload_stays_bounded(self):
        """Evidence cites trade IDs. Unbounded, a 100k history would put a
        megabyte of them into a status payload."""
        payload = json.dumps(engine(self._big(5_000)).snapshot().to_dict())
        assert len(payload) < 4_000_000

    def test_ten_years_of_history_buckets_correctly(self):
        facts = self._big(3_000)
        snapshot = engine(facts).snapshot()
        assert len(snapshot.periods["year"]) >= 1
        assert sum(p.trades for p in snapshot.periods["month"]) == 3_000


class TestAdversarialData:
    def test_all_wins_no_losses(self):
        snapshot = engine(series(40, wins="W" * 40)).snapshot()
        assert snapshot.value("profit_factor") is None or \
            snapshot.metrics["profit_factor"].value == float("inf")
        assert json.dumps(snapshot.to_dict())

    def test_all_losses_no_wins(self):
        snapshot = engine(series(40, wins="L" * 40)).snapshot()
        assert snapshot.value("win_rate") == 0.0
        assert json.dumps(snapshot.to_dict())

    def test_every_trade_flat(self):
        facts = [fact(f"T{i}", pnl=0.0, entry=BASE + timedelta(days=i))
                 for i in range(30)]
        snapshot = engine(facts).snapshot()
        assert snapshot.value("current_streak") == 0
        assert json.dumps(snapshot.to_dict())

    def test_every_trade_at_the_same_instant(self):
        facts = [fact(f"T{i}", entry=BASE) for i in range(30)]
        snapshot = engine(facts).snapshot()
        assert snapshot.trades_analyzed == 30
        assert snapshot.value("active_days") == 1

    def test_no_reviews_at_all(self):
        """A trader who has only ever run AI Mode. Process metrics are None,
        behaviours that need a review decline, and nothing pretends otherwise."""
        facts = series(40, reviewed=False, had_stop=None, widened_stop=None,
                       had_target=None, process_score=None, category_scores={},
                       mistakes=())
        snapshot = engine(facts).snapshot()
        assert snapshot.value("stop_discipline_rate") is None
        assert not snapshot.behavior("trading_without_stops").assessable
        assert snapshot.score("discipline").value is None

    def test_no_indicator_context_at_all(self):
        facts = series(40, rsi=None, adx=None, rvol=None, iv=None, delta=None,
                       dte=None, setup_quality=None, htf_trend=None,
                       market_regime=None, confidence=None, r_multiple=None)
        snapshot = engine(facts).snapshot()
        assert snapshot.trades_analyzed == 40
        assert json.dumps(snapshot.to_dict())

    def test_enormous_outliers_do_not_break_anything(self):
        facts = series(30)
        facts[10] = fact(facts[10].trade_id, entry=facts[10].entry_ts,
                         pnl=1e9, outlay=1e9)
        facts[20] = fact(facts[20].trade_id, entry=facts[20].entry_ts,
                         pnl=-1e9, outlay=1e-9)
        payload = json.dumps(engine(facts).snapshot().to_dict())
        assert "Infinity" not in payload and "NaN" not in payload

    def test_a_factset_carrying_notes_propagates_them(self):
        fs = FactSet(facts=tuple(series(20)),
                     notes=("42 trades predate the review system.",))
        snapshot = TradingIntelligence(lambda: fs).snapshot()
        assert "42 trades predate the review system." in snapshot.notes
