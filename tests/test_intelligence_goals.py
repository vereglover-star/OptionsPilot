"""GoalEngine, AchievementEngine and the goal store.

Goals are `(metric key, comparator, target, window)` rather than free text so
progress is computed and not self-reported. Achievements are derived on every
read and never stored, so a badge can't outlive the record that earned it.

The store tests are the "a file a user can open is a file a user will edit
badly" tests: a malformed goals.json must cost the user their goals, never the
app's startup.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from optionspilot.intelligence.achievements import SPECS, AchievementEngine
from optionspilot.intelligence.goals import (
    GoalEngine, TEMPLATES, _progress, validate,
)
from optionspilot.intelligence.models import Goal, Trend
from optionspilot.intelligence.performance import (
    METRIC_SPECS, PerformanceEngine, compute,
)
from optionspilot.intelligence.store import IntelligenceStore
from optionspilot.intelligence.windows import WINDOWS

from tests.intelligence_helpers import BASE, fact, series


def evaluate(goals, facts):
    perf = PerformanceEngine().analyze(tuple(facts))
    return GoalEngine().evaluate(goals, perf.windows, perf.trends)


class TestTemplates:
    def test_every_template_is_valid(self):
        """A shipped template that can never evaluate would be a goal the user
        sets and then watches read 'no data' forever."""
        for goal in TEMPLATES:
            assert validate(goal) is None, goal.id

    def test_template_ids_are_unique(self):
        ids = [g.id for g in TEMPLATES]
        assert len(ids) == len(set(ids))

    def test_templates_target_real_metrics_and_real_windows(self):
        for goal in TEMPLATES:
            assert goal.metric in METRIC_SPECS
            assert goal.window in WINDOWS


class TestValidation:
    def test_unknown_metric_is_rejected_at_creation(self):
        """The user finds out at the point they can fix it, not by watching a
        goal never evaluate."""
        problem = validate(Goal("x", "x", "made_up_metric", ">=", 1.0))
        assert problem and "unknown metric" in problem

    def test_bad_comparator_is_rejected(self):
        assert validate(Goal("x", "x", "avg_r", "~=", 1.0)) is not None

    def test_unknown_window_is_rejected(self):
        assert validate(Goal("x", "x", "avg_r", ">=", 1.0, "last_century")) \
            is not None


class TestEvaluation:
    def test_a_met_goal_reads_met(self):
        goal = Goal("g", "Positive expectancy", "expectancy", ">=", 0.0,
                    "lifetime", "$")
        progress = evaluate([goal], series(20))[0]
        assert progress.met is True
        assert progress.progress == 1.0

    def test_an_unmet_goal_reports_partial_progress(self):
        goal = Goal("g", "R above 4", "avg_r", ">=", 4.0, "lifetime", "R")
        progress = evaluate([goal], series(20, r_multiple=1.0))[0]
        assert progress.met is False
        assert progress.progress == pytest.approx(0.25)

    def test_less_than_or_equal_goals_work(self):
        goal = Goal("g", "Hold under an hour", "avg_hold_minutes", "<=", 60.0,
                    "lifetime", "min")
        assert evaluate([goal], series(20, hold_minutes=30))[0].met
        assert not evaluate([goal], series(20, hold_minutes=120))[0].met

    def test_an_unmeasurable_metric_reports_why_rather_than_zero(self):
        goal = Goal("g", "R above 2", "avg_r", ">=", 2.0, "lifetime", "R")
        progress = evaluate([goal], series(20, r_multiple=None))[0]
        assert progress.current is None
        assert progress.met is False
        assert "not measurable" in progress.detail.lower()

    def test_the_window_is_part_of_the_commitment(self):
        """Lifetime R above 2 is a different (and much harder) promise than R
        above 2 over the last twenty trades."""
        facts = series(10, r_multiple=0.5) + series(
            10, r_multiple=3.0, prefix="R", start=BASE + timedelta(days=30))
        recent = Goal("a", "recent", "avg_r", ">=", 2.0, "last_10_trades", "R")
        lifetime = Goal("b", "lifetime", "avg_r", ">=", 2.0, "lifetime", "R")
        results = {g.goal.id: g for g in evaluate([recent, lifetime], facts)}
        assert results["a"].met is True
        assert results["b"].met is False

    def test_inactive_goals_are_skipped(self):
        goal = Goal("g", "x", "expectancy", ">=", 0.0, active=False)
        assert evaluate([goal], series(10)) == ()

    def test_unmet_goals_sort_first_closest_first(self):
        goals = [
            Goal("met", "met", "expectancy", ">=", 0.0, "lifetime"),
            Goal("far", "far", "avg_r", ">=", 100.0, "lifetime"),
            Goal("near", "near", "avg_r", ">=", 1.25, "lifetime"),
        ]
        order = [g.goal.id for g in evaluate(goals, series(20, r_multiple=1.0))]
        assert order == ["near", "far", "met"]

    def test_trend_is_attached_from_the_performance_trends(self):
        facts = []
        for month in range(1, 5):
            base = BASE.replace(month=month + 3, day=6)
            facts.extend(fact(f"M{month}-{i}", pnl=month * 50,
                              entry=base + timedelta(days=i))
                         for i in range(6))
        goal = Goal("g", "x", "expectancy", ">=", 500.0, "lifetime", "$")
        assert evaluate([goal], facts)[0].trend is not Trend.UNKNOWN


class TestProgressArithmetic:
    def test_progress_is_clamped_and_never_exceeds_one(self):
        assert _progress(500.0, ">=", 100.0) == 1.0

    def test_progress_handles_a_zero_target(self):
        """expectancy >= 0 cannot be a ratio, so distance is measured against
        the target's own magnitude instead."""
        assert _progress(0.0, ">=", 0.0) == 1.0
        assert 0.0 <= _progress(-0.5, ">=", 0.0) < 1.0

    def test_progress_handles_a_negative_target(self):
        """'lose no more than $500 in a day' — worst_day_pnl >= -500."""
        assert _progress(-200.0, ">=", -500.0) == 1.0
        assert _progress(-2000.0, ">=", -500.0) < 1.0

    def test_progress_is_zero_when_unmeasurable(self):
        assert _progress(None, ">=", 5.0) == 0.0


class TestAchievements:
    def test_every_spec_evaluates_on_an_empty_history(self):
        result = AchievementEngine().evaluate([], compute([]))
        assert len(result) == len(SPECS)
        assert all(not a.earned for a in result)
        assert all(a.detail for a in result)

    def test_none_can_be_earned_by_a_single_trade(self):
        """Not gamification for its own sake: every achievement needs a
        sustained streak, a measured improvement, or a threshold held across a
        minimum sample."""
        facts = [fact("A", pnl=10_000)]
        assert not any(a.earned for a in
                       AchievementEngine().evaluate(facts, compute(facts)))

    def test_stop_keeper_needs_a_25_trade_streak(self):
        short = series(24)
        long = series(25)
        assert not _earned(short, "stop_discipline_25")
        assert _earned(long, "stop_discipline_25")

    def test_a_streak_is_broken_by_a_violation(self):
        facts = series(40)
        facts[-3] = fact(facts[-3].trade_id, entry=facts[-3].entry_ts,
                         had_stop=False)
        assert not _earned(facts, "stop_discipline_25")

    def test_an_unobserved_trade_neither_extends_nor_breaks_a_streak(self):
        """A trade nobody reviewed could not have broken the rule, so it must
        not be allowed to count toward keeping it either."""
        facts = series(30)
        for i in (10, 11, 12):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            had_stop=None, widened_stop=None, reviewed=False)
        engine = AchievementEngine()
        badge = next(a for a in engine.evaluate(facts, compute(facts))
                     if a.id == "stop_discipline_25")
        assert "27" in badge.detail       # 30 trades, 3 of them unobserved

    def test_outcome_achievements_need_a_large_sample(self):
        """Two outcome-flavoured achievements exist and both require enough
        trades that they cannot be a hot streak."""
        assert not _earned(series(40), "profit_factor_15")
        assert _earned(series(60), "profit_factor_15")

    def test_improvement_compares_two_halves(self):
        facts = [fact(f"E{i}", pnl=10, entry=BASE + timedelta(days=i))
                 for i in range(25)]
        facts += [fact(f"L{i}", pnl=200, entry=BASE + timedelta(days=25 + i))
                  for i in range(25)]
        assert _earned(facts, "improving")

    def test_earned_first_then_closest(self):
        facts = series(60)
        result = AchievementEngine().evaluate(facts, compute(facts))
        earned = [i for i, a in enumerate(result) if a.earned]
        unearned = [i for i, a in enumerate(result) if not a.earned]
        assert not earned or not unearned or max(earned) < min(unearned)

    def test_progress_is_always_a_fraction(self):
        facts = series(60)
        for badge in AchievementEngine().evaluate(facts, compute(facts)):
            assert 0.0 <= badge.progress <= 1.0


def _earned(facts, achievement_id):
    result = AchievementEngine().evaluate(facts, compute(facts))
    return next(a for a in result if a.id == achievement_id).earned


class TestStore:
    def test_missing_file_is_not_an_error(self, tmp_path):
        assert IntelligenceStore(tmp_path / "nope").load_goals() == []

    def test_round_trip(self, tmp_path):
        store = IntelligenceStore(tmp_path)
        store.save_goals(list(TEMPLATES[:3]))
        assert [g.id for g in store.load_goals()] == [g.id for g in TEMPLATES[:3]]

    def test_directory_is_created_on_demand_not_at_startup(self, tmp_path):
        """A user who never opens the Goals panel should not accumulate empty
        directories."""
        target = tmp_path / "intelligence"
        IntelligenceStore(target)
        assert not target.exists()
        IntelligenceStore(target).save_goals([TEMPLATES[0]])
        assert target.exists()

    @pytest.mark.parametrize("payload", [
        "not json at all",
        "[]",
        "{}",
        '{"goals": "a string"}',
        '{"goals": [1, 2, 3]}',
        '{"goals": [{"no": "id"}]}',
        '{"goals": [{"id": "x", "metric": "avg_r", "target": "abc"}]}',
        '{"goals": [{"id": "x", "metric": "avg_r", "comparator": "!!", "target": 1}]}',
        '{"goals": [{"id": "x", "metric": "avg_r", "comparator": ">=", "target": null}]}',
    ])
    def test_a_badly_edited_file_costs_goals_never_startup(self, tmp_path, payload):
        """`or {}` is not a type check — the lesson data/control.py learned when
        `{"providers": [1,2]}` took down the whole app at the composition root."""
        store = IntelligenceStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(payload, encoding="utf-8")
        assert store.load_goals() == []

    def test_a_bare_list_is_accepted(self, tmp_path):
        store = IntelligenceStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps(
            [{"id": "x", "label": "X", "metric": "avg_r",
              "comparator": ">=", "target": 2.0}]), encoding="utf-8")
        assert [g.id for g in store.load_goals()] == ["x"]

    def test_good_entries_survive_a_bad_neighbour(self, tmp_path):
        store = IntelligenceStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"goals": [
            {"id": "good", "metric": "avg_r", "comparator": ">=", "target": 2.0},
            "garbage",
            {"broken": True},
        ]}), encoding="utf-8")
        assert [g.id for g in store.load_goals()] == ["good"]

    def test_duplicate_ids_collapse(self, tmp_path):
        store = IntelligenceStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"goals": [
            {"id": "x", "metric": "avg_r", "comparator": ">=", "target": 1.0},
            {"id": "x", "metric": "avg_r", "comparator": ">=", "target": 9.0},
        ]}), encoding="utf-8")
        assert len(store.load_goals()) == 1

    def test_a_scripted_file_cannot_make_every_snapshot_evaluate_thousands(
            self, tmp_path):
        store = IntelligenceStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"goals": [
            {"id": f"g{i}", "metric": "avg_r", "comparator": ">=", "target": 1.0}
            for i in range(5000)]}), encoding="utf-8")
        assert len(store.load_goals()) <= 50

    def test_write_is_atomic(self, tmp_path):
        """A truncated write must not be able to destroy the previous file."""
        store = IntelligenceStore(tmp_path)
        store.save_goals([TEMPLATES[0]])
        store.save_goals([TEMPLATES[1]])
        assert len(list(tmp_path.glob("*.tmp"))) == 0
        assert [g.id for g in store.load_goals()] == [TEMPLATES[1].id]
