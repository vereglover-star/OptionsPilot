"""ConfidenceEngine — the eight composite scores.

A composite score is the most dangerous object in a coaching system: it
compresses a lot of evidence into one number, and a number with no provenance is
an opinion. The tests here are mostly about provenance and refusal — that an
unmeasurable component is dropped rather than scored zero, that a score with
nothing behind it is None rather than 50, and that the explanation names the
component that actually held the score back.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from optionspilot.intelligence.behavior import BehaviorEngine
from optionspilot.intelligence.confidence import ConfidenceEngine, ScoreInput
from optionspilot.intelligence.models import Confidence, Trend
from optionspilot.intelligence.performance import PerformanceEngine, compute

from tests.intelligence_helpers import BASE, fact, series

SCORE_KEYS = ("execution", "discipline", "risk_control", "consistency",
              "planning", "adaptability", "learning", "decision_quality")


def score_input(facts):
    facts = tuple(facts)
    perf = PerformanceEngine().analyze(facts)
    behaviors = {b.id: b for b in BehaviorEngine().analyze(facts)}
    return ScoreInput(facts=facts, metrics=perf.metrics, behaviors=behaviors,
                      monthly=perf.periods["month"], trends=perf.trends)


def cards(facts):
    return {c.key: c for c in ConfidenceEngine().analyze(score_input(facts))}


class TestShape:
    def test_all_eight_scores_are_always_produced(self):
        assert set(cards(series(30))) == set(SCORE_KEYS)

    def test_all_eight_are_produced_for_an_empty_history_too(self):
        """A consumer renders a fixed set of gauges; a missing key is a hole in
        the page, while a None value is a legible 'not enough data'."""
        result = cards([])
        assert set(result) == set(SCORE_KEYS)
        assert all(c.value is None for c in result.values())
        assert all(c.confidence is Confidence.NONE for c in result.values())

    def test_every_card_lists_its_components(self):
        for card in cards(series(30)).values():
            assert card.components
            assert all(c.label for c in card.components)

    def test_grades_follow_the_shared_scale(self):
        from optionspilot.intelligence.models import grade
        for card in cards(series(40)).values():
            assert card.grade == grade(card.value)


class TestRefusal:
    def test_no_measurable_component_yields_none_not_fifty(self):
        """There is no default 50 anywhere in this package."""
        result = cards([])
        assert result["discipline"].value is None
        assert "not enough" in result["discipline"].explanation.lower()

    def test_an_unmeasurable_component_is_dropped_not_scored_zero(self):
        """A trader who has never had a stop level recorded has no R-multiple
        component. That must not read as terrible risk control."""
        without_r = cards(series(30, r_multiple=None))["risk_control"]
        with_r = cards(series(30))["risk_control"]
        assert without_r.value is not None
        assert without_r.value == pytest.approx(with_r.value, abs=25)

    def test_partial_coverage_lowers_confidence_and_is_disclosed(self):
        """Above the coverage floor a score is still produced, but it says how
        much of itself it could actually measure."""
        sparse = cards(series(30, category_scores={}))
        card = sparse["discipline"]
        assert card.value is not None
        assert card.confidence <= Confidence.MEDIUM
        assert "no data for" in card.explanation

    def test_a_score_below_the_coverage_floor_refuses_to_produce_a_number(self):
        """A trader with no reviews at all used to score Discipline 100/100
        grade A — because the one component needing no review (revenge trading,
        which reads only timestamps) came back clean, and 20% coverage was
        enough to average. An A earned by an absence of data is the most
        flattering lie this package could tell."""
        blind = cards(series(30, reviewed=False, had_stop=None,
                             widened_stop=None, had_target=None,
                             process_score=None, category_scores={},
                             mistakes=()))
        card = blind["discipline"]
        assert card.value is None
        assert card.confidence is Confidence.NONE
        assert "% of the inputs" in card.explanation

    def test_learning_is_unavailable_in_a_traders_first_month(self):
        """Every component of Learning Progress needs two comparable periods."""
        card = cards(series(20, spacing_days=0.2))["learning"]
        assert card.value is None


class TestExplanations:
    def test_names_the_component_that_cost_the_most_points(self):
        """'Held back most by' means the component that LOST the most points —
        (100 − value) × weight. Ranking on value × weight instead picks whichever
        good component carries a small weight, and then tells the user their
        perfect 100/100 input is the problem."""
        facts = series(30, had_stop=False, widened_stop=False)
        card = cards(facts)["risk_control"]
        assert "stop discipline" in card.explanation.lower()

    def test_a_zero_weight_component_never_explains_the_score(self):
        """Planning carries an informational 'reward/risk recorded at entry'
        component at weight 0. It is shown, but it may not be blamed."""
        card = cards(series(30, had_target=False))["planning"]
        assert "reward/risk recorded" not in card.explanation.lower()
        assert any(c.key == "rr_recorded" and c.weight == 0
                   for c in card.components)

    def test_zero_weight_components_do_not_move_the_average(self):
        with_rr = cards(series(30))["planning"].value
        no_rr = cards(series(30, risk_reward=None))["planning"].value
        assert with_rr == pytest.approx(no_rr)


class TestScoreSemantics:
    def test_discipline_falls_when_rules_are_broken(self):
        clean = cards(series(30))["discipline"].value
        messy = cards(series(30, mistakes=("moved_stop", "averaged_down"),
                             widened_stop=True))["discipline"].value
        assert messy < clean

    def test_risk_control_falls_without_stops(self):
        protected = cards(series(30))["risk_control"].value
        unprotected = cards(series(30, had_stop=False))["risk_control"].value
        assert unprotected < protected

    def test_adaptability_measures_breadth_not_improvement(self):
        """Deliberately distinct from Learning Progress: a trader can be highly
        adaptable and not improving, or improving fast inside one niche."""
        narrow = []
        for i in range(30):
            narrow.append(fact(f"A{i}", pnl=100 if i % 3 else -100,
                               entry=BASE + timedelta(days=i),
                               market_regime="trending-up/low-vol",
                               direction="long"))
        for i in range(10):                # a regime they lose in
            narrow.append(fact(f"B{i}", pnl=-200,
                               entry=BASE + timedelta(days=40 + i),
                               market_regime="ranging/high-vol",
                               direction="short"))
        card = cards(narrow)["adaptability"]
        assert card.value is not None and card.value < 100

    def test_adaptability_is_unmeasurable_with_one_condition(self):
        card = cards(series(30, market_regime="trending-up/low-vol",
                            direction="long", symbol="SPY",
                            session="regular"))["adaptability"]
        assert card.value is None

    def test_decision_quality_falls_when_the_analysis_is_overridden(self):
        good = cards(series(30, setup_quality="good"))["decision_quality"].value
        poor = cards(series(30, setup_quality="poor"))["decision_quality"].value
        assert poor < good

    def test_calibration_rewards_confidence_that_predicts(self):
        aligned, inverted = [], []
        for i in range(40):
            high = i % 2 == 0
            aligned.append(fact(f"A{i}", entry=BASE + timedelta(days=i),
                                confidence=80 if high else 30,
                                pnl=200 if high else -200))
            inverted.append(fact(f"I{i}", entry=BASE + timedelta(days=i),
                                 confidence=80 if high else 30,
                                 pnl=-200 if high else 200))
        good = _component(cards(aligned)["decision_quality"], "calibration")
        bad = _component(cards(inverted)["decision_quality"], "calibration")
        assert good > bad

    def test_calibration_needs_a_real_sample(self):
        assert _component(cards(series(6))["decision_quality"],
                          "calibration") is None


def _component(card, key):
    return next(c.value for c in card.components if c.key == key)


class TestConsistencyScore:
    def test_month_to_month_component_needs_three_months(self):
        card = cards(series(20, spacing_days=0.5))["consistency"]
        assert _component(card, "month_to_month") is None

    def test_steady_weeks_score_higher_than_erratic_ones(self):
        steady, erratic = [], []
        for week in range(8):
            base = BASE + timedelta(days=7 * week)
            for i, pnl in enumerate((200, -100)):
                steady.append(fact(f"S{week}-{i}", pnl=pnl,
                                   entry=base + timedelta(days=i)))
            swing = 1000 if week % 2 else -900
            for i, pnl in enumerate((swing, -swing // 3)):
                erratic.append(fact(f"E{week}-{i}", pnl=pnl,
                                    entry=base + timedelta(days=i)))
        assert cards(steady)["consistency"].value > \
            cards(erratic)["consistency"].value


class TestTrendInputs:
    def test_learning_reads_the_performance_trends(self):
        facts = []
        for month, mistakes in enumerate(((), (), ("no_stop",), ("no_stop", "chased_entry")),
                                         start=1):
            base = BASE.replace(month=month + 3, day=6)
            facts.extend(fact(f"M{month}-{i}", entry=base + timedelta(days=i),
                              mistakes=mistakes)
                         for i in range(6))
        data = score_input(facts)
        assert data.trends["mistake_rate"] in (Trend.DECLINING, Trend.STABLE,
                                               Trend.IMPROVING)
        card = ConfidenceEngine()._learning(data, len(facts))
        assert card.value is not None
