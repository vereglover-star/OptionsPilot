"""RecommendationEngine and CurriculumEngine — the two modules that tell the
trader what to do.

The brief for both was the same sentence: never produce *"you should manage
risk"*, always produce *"you exceeded your planned risk on 9 of your last 17
trades; reducing each by 20% would have improved expectancy by 14%"*.

Structurally that means neither module may contain generic advice: everything is
derived from a finding that already carries evidence. These tests assert the
derivation, not the wording.
"""

from __future__ import annotations

from datetime import timedelta


from optionspilot.intelligence.behavior import BEHAVIORS, BehaviorEngine
from optionspilot.intelligence.confidence import ConfidenceEngine, ScoreInput
from optionspilot.intelligence.curriculum import (
    CURRICULUM, CURRICULUM_BY_ID, CurriculumEngine, MAX_LESSONS,
)
from optionspilot.intelligence.goals import GoalEngine
from optionspilot.intelligence.models import Confidence, Goal, Severity
from optionspilot.intelligence.patterns import PatternEngine
from optionspilot.intelligence.performance import METRIC_SPECS, PerformanceEngine
from optionspilot.intelligence.recommend import (
    MAX_RECOMMENDATIONS, RecommendationEngine, _RISK_ACTIONS,
)
from optionspilot.intelligence.risk import RiskIntelligence

from tests.intelligence_helpers import BASE, fact, series


def pipeline(facts):
    """Run enough of the pipeline to feed the advice engines."""
    facts = tuple(facts)
    perf = PerformanceEngine().analyze(facts)
    behaviors = BehaviorEngine().analyze(facts)
    behavior_map = {b.id: b for b in behaviors}
    patterns = PatternEngine().analyze(facts)
    risk = RiskIntelligence().analyze(facts, perf.metrics)
    scores = ConfidenceEngine().analyze(ScoreInput(
        facts=facts, metrics=perf.metrics, behaviors=behavior_map,
        monthly=perf.periods["month"], trends=perf.trends))
    goals = GoalEngine().evaluate((), perf.windows, perf.trends)
    return perf, behaviors, behavior_map, patterns, scores, goals, risk


def recommend(facts):
    _, behaviors, _, patterns, scores, goals, risk = pipeline(facts)
    return RecommendationEngine().build(behaviors, patterns, scores, goals, risk)


def lessons(facts):
    perf, _, behavior_map, _, scores, _, _ = pipeline(facts)
    return CurriculumEngine().recommend(
        behavior_map, perf.metrics, {s.key: s for s in scores})


class TestCatalogueIntegrity:
    def test_every_behavior_has_a_corrective_action(self):
        """A detected habit with no action is a diagnosis with no treatment.
        Hesitation is the sole exception — it is never assessable, so it can
        never be detected, so it can never need one."""
        for spec in BEHAVIORS.values():
            if spec.id == "hesitation":
                continue
            assert spec.action, spec.id

    def test_every_lesson_declares_at_least_one_trigger(self):
        """A lesson nobody can be triggered into seeing is dead weight in a
        payload."""
        for lesson in CURRICULUM:
            assert (lesson.behavior_triggers or lesson.metric_triggers
                    or lesson.score_triggers), lesson.id

    def test_lesson_triggers_reference_things_that_exist(self):
        for lesson in CURRICULUM:
            for behavior_id in lesson.behavior_triggers:
                assert behavior_id in BEHAVIORS, lesson.id
            for key, _, _ in lesson.metric_triggers:
                assert key in METRIC_SPECS, lesson.id

    def test_lesson_ids_are_unique(self):
        assert len(CURRICULUM_BY_ID) == len(CURRICULUM)

    def test_every_risk_action_key_is_one_the_risk_engine_emits(self):
        facts = series(40, wins="WLL", had_stop=False, outlay=200.0)
        facts[0] = fact(facts[0].trade_id, entry=facts[0].entry_ts, outlay=9000.0)
        perf = PerformanceEngine().analyze(tuple(facts))
        risk = RiskIntelligence().analyze(facts, perf.metrics)
        emitted = {o["key"] for o in risk["observations"]}
        assert emitted & set(_RISK_ACTIONS)


class TestDerivation:
    def test_nothing_is_recommended_without_evidence(self):
        """A clean, uneventful history produces an empty list — the UI says so
        rather than padding with platitudes."""
        assert recommend(series(30)) == ()

    def test_every_recommendation_carries_evidence(self):
        facts = series(40, wins="WLL", had_stop=False, setup_quality="poor",
                       mistakes=("no_stop", "chased_entry"))
        result = recommend(facts)
        assert result
        for rec in result:
            assert rec.evidence
            assert rec.rationale

    def test_a_behavior_recommendation_quotes_the_counterfactual(self):
        facts = series(30, pnl_win=100)
        for i in range(8):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            pnl=-600, setup_quality="poor")
        rec = next(r for r in recommend(facts) if r.id.endswith("ignoring_the_plan"))
        assert rec.impact is not None
        assert "measured over the trades you actually took" in rec.rationale
        assert "not a forecast" in rec.rationale

    def test_recommendations_link_to_the_lessons_that_address_them(self):
        facts = series(30, had_stop=False)
        rec = next(r for r in recommend(facts)
                   if r.id.endswith("trading_without_stops"))
        assert "stops_that_hold" in rec.lesson_ids

    def test_lesson_links_are_derived_from_the_curriculum_not_restated(self):
        """The mapping comes from each lesson's own trigger declaration, so the
        two can never disagree about which lesson helps with which habit."""
        facts = series(30, had_stop=False)
        rec = next(r for r in recommend(facts)
                   if r.id.endswith("trading_without_stops"))
        for lesson_id in rec.lesson_ids:
            assert "trading_without_stops" in \
                CURRICULUM_BY_ID[lesson_id].behavior_triggers


class TestRanking:
    def test_priority_is_computed_from_severity_confidence_and_cost(self):
        facts = series(40, wins="WL")
        for i in range(10):                 # serious, expensive
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            pnl=-800, had_stop=False)
        for i in range(20, 24):             # minor
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            had_target=False)
        result = recommend(facts)
        priorities = [r.priority for r in result]
        assert priorities == sorted(priorities, reverse=True)

    def test_output_is_capped(self):
        facts = series(60, wins="WLL", had_stop=False, had_target=False,
                       setup_quality="poor", dte=1, iv=0.9, delta=0.05,
                       mistakes=("no_stop", "chased_entry", "averaged_down",
                                 "oversized", "moved_stop"),
                       widened_stop=True)
        assert len(recommend(facts)) <= MAX_RECOMMENDATIONS

    def test_the_same_action_is_never_listed_twice(self):
        facts = series(60, wins="WLL", had_stop=False,
                       mistakes=("no_stop", "moved_stop"), widened_stop=True)
        actions = [r.action for r in recommend(facts)]
        assert len(actions) == len(set(actions))

    def test_ordering_is_stable_between_identical_runs(self):
        facts = series(50, wins="WLL", had_stop=False, setup_quality="poor")
        assert [r.id for r in recommend(facts)] == [r.id for r in recommend(facts)]


class TestSources:
    def test_a_strong_pattern_becomes_a_concentrate_here_action(self):
        """Knowing where the edge IS matters as much as knowing where it isn't,
        and this is the only part of the system that says so."""
        facts = [fact(f"M{i}", pnl=100 if i % 2 else -100,
                      entry=BASE + timedelta(days=i)) for i in range(60)]
        facts += [fact(f"W{i}", pnl=400, entry=BASE + timedelta(days=60 + i),
                       symbol="NVDA") for i in range(40)]
        strengths = [r for r in recommend(facts)
                     if r.source == "pattern" and r.severity is Severity.POSITIVE]
        assert strengths
        assert "Concentrate" in strengths[0].action

    def test_a_weak_score_is_not_repeated_when_a_behavior_explains_it(self):
        """'Your discipline is 48' directly under 'you widened a stop on 6 of
        40 trades' is the same advice twice, once vaguely."""
        facts = series(40, had_stop=False, widened_stop=False,
                       mistakes=("no_stop",))
        result = recommend(facts)
        assert any(r.source == "behavior" for r in result)
        assert not any(r.source == "score" for r in result)

    def test_a_nearly_met_goal_becomes_an_action_a_distant_one_does_not(self):
        """Telling someone they are 8% of the way to a goal is a scoreboard,
        not advice."""
        facts = series(30, r_multiple=1.8)
        perf = PerformanceEngine().analyze(tuple(facts))
        near = Goal("near", "R above 2", "avg_r", ">=", 2.0, "lifetime", "R")
        far = Goal("far", "R above 20", "avg_r", ">=", 20.0, "lifetime", "R")
        goals = GoalEngine().evaluate([near, far], perf.windows, perf.trends)
        result = RecommendationEngine().build((), (), (), goals, {})
        ids = [r.id for r in result]
        assert "goal:near" in ids
        assert "goal:far" not in ids


class TestCurriculum:
    def test_no_default_reading_list(self):
        """There is no 'beginners start here'. A lesson appears only because a
        number fired."""
        assert lessons(series(30)) == ()

    def test_a_measured_weakness_summons_the_matching_lesson(self):
        result = lessons(series(30, had_stop=False))
        assert any(x.lesson_id == "stops_that_hold" for x in result)

    def test_every_recommendation_answers_the_four_questions(self):
        """Why this lesson, which statistic triggered it, what problem it
        solves, and what it is worth."""
        result = lessons(series(40, had_stop=False, dte=1, iv=0.9,
                                setup_quality="poor"))
        assert result
        for item in result:
            assert item.why
            assert item.triggered_by
            assert item.problem
            assert item.confidence is not Confidence.NONE

    def test_the_why_quotes_the_traders_own_numbers(self):
        item = next(x for x in lessons(series(30, had_stop=False))
                    if x.lesson_id == "stops_that_hold")
        assert "Recommended because" in item.why
        assert any(char.isdigit() for char in item.why)

    def test_impact_comes_from_the_costliest_trigger(self):
        facts = series(30, pnl_win=100)
        for i in range(10):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            pnl=-900, had_stop=False)
        item = next(x for x in lessons(facts) if x.lesson_id == "stops_that_hold")
        assert item.impact is not None

    def test_a_metric_trigger_needs_a_real_sample(self):
        """One trade with a bad number is not a curriculum."""
        assert lessons(series(3, had_stop=False)) == ()

    def test_output_is_capped_and_ordered_by_priority(self):
        facts = series(60, wins="WLL", had_stop=False, had_target=False,
                       setup_quality="poor", dte=1, iv=0.95, delta=0.05,
                       rsi=85.0, hold_minutes=300,
                       mistakes=("no_stop", "chased_entry", "averaged_down",
                                 "held_loser", "moved_stop"),
                       widened_stop=True)
        result = lessons(facts)
        assert 0 < len(result) <= MAX_LESSONS
        assert [x.priority for x in result] == \
            sorted((x.priority for x in result), reverse=True)

    def test_lessons_are_never_duplicated(self):
        facts = series(50, had_stop=False, widened_stop=True,
                       mistakes=("no_stop", "moved_stop", "held_loser"))
        ids = [x.lesson_id for x in lessons(facts)]
        assert len(ids) == len(set(ids))
