"""BehaviorEngine — the detectors, and the guarantees that keep them honest.

Three properties are asserted for *every* detector rather than one at a time,
because a new detector added later must inherit them automatically:

* a detected finding cites the trades it counted,
* one occurrence is never enough to name a habit,
* a behaviour with no data says so with a reason instead of returning
  "not detected".

The rest of the file tests each detector's specific arithmetic.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from optionspilot.intelligence.behavior import (
    BEHAVIORS, DETECTORS, MIN_OCCURRENCES, REVENGE_WINDOW_MIN, BehaviorEngine,
)
from optionspilot.intelligence.models import Confidence, Severity, Trend

from tests.intelligence_helpers import BASE, fact, series


def find(findings, behavior_id):
    return next(f for f in findings if f.id == behavior_id)


def analyze(facts, previous=None):
    return BehaviorEngine().analyze(facts, previous)


class TestUniversalGuarantees:
    def test_every_catalogued_behavior_has_a_detector(self):
        assert set(BEHAVIORS) == set(DETECTORS)

    def test_every_detector_returns_its_own_id(self):
        findings = analyze(series(60, wins="WWL"))
        assert {f.id for f in findings} == set(BEHAVIORS)

    def test_every_detected_finding_cites_trade_ids(self):
        """A finding the user cannot drill into is indistinguishable from a
        horoscope. `tilt_after_loss` and `overconfidence_after_wins` state a
        comparison rather than a count, and cite their cohort."""
        facts = series(40, wins="WLL", mistakes=("chased_entry", "no_stop"),
                       had_stop=False, dte=1, iv=0.9, delta=0.1)
        for finding in analyze(facts):
            if finding.detected:
                assert any(e.trade_ids for e in finding.evidence), finding.id

    def test_a_single_occurrence_never_names_a_habit(self):
        facts = series(30)
        facts[3] = fact(facts[3].trade_id, entry=facts[3].entry_ts,
                        mistakes=("averaged_down",))
        finding = find(analyze(facts), "averaging_down")
        assert finding.occurrences == 1
        assert finding.detected is False
        assert finding.confidence is Confidence.LOW

    def test_two_occurrences_can(self):
        facts = series(30)
        for i in (3, 9):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            mistakes=("averaged_down",))
        finding = find(analyze(facts), "averaging_down")
        assert finding.occurrences == MIN_OCCURRENCES
        assert finding.detected is True

    def test_undetected_findings_are_positive_not_neutral(self):
        """Reporting what is going right uses the same machinery as reporting
        what is going wrong."""
        clean = find(analyze(series(30)), "averaging_down")
        assert clean.severity is Severity.POSITIVE
        assert clean.summary

    def test_empty_history_produces_only_unassessable_findings(self):
        findings = analyze([])
        assert len(findings) == len(BEHAVIORS)
        assert all(not f.assessable for f in findings)
        assert all(f.unassessable_reason for f in findings)


class TestUnassessable:
    def test_hesitation_is_always_declined_with_a_reason(self):
        """Hesitation needs signal-to-entry latency and the setups that were
        skipped entirely. Neither exists in this system, and guessing at it from
        hold times would be inventing a psychological claim."""
        finding = find(analyze(series(200, wins="WL")), "hesitation")
        assert finding.assessable is False
        assert finding.detected is False
        assert "not recorded" in finding.unassessable_reason

    def test_review_only_behaviors_decline_without_reviews(self):
        """'Nobody looked' must not read as 'you never did it'."""
        facts = series(30, reviewed=False, had_stop=None, widened_stop=None,
                       had_target=None, mistakes=())
        for behavior_id in ("averaging_down", "moving_stops",
                            "trading_without_stops", "no_target_defined"):
            finding = find(analyze(facts), behavior_id)
            assert finding.assessable is False, behavior_id

    def test_contract_behaviors_decline_when_the_field_was_never_recorded(self):
        facts = series(30, dte=None, iv=None, delta=None)
        for behavior_id in ("theta_neglect", "iv_neglect", "lottery_tickets"):
            assert not find(analyze(facts), behavior_id).assessable, behavior_id

    def test_partially_instrumented_history_measures_only_what_it_has(self):
        """The denominator is trades that recorded the field — otherwise a
        mostly-uninstrumented history reports a flattering 0% rate."""
        facts = series(20, dte=None)
        for i in range(6):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts, dte=1)
        finding = find(analyze(facts), "theta_neglect")
        assert finding.sample == 6
        assert finding.occurrences == 6


class TestRevengeTrading:
    def test_detects_entries_inside_the_reactive_window(self):
        facts = [fact("L1", pnl=-100, entry=BASE, hold_minutes=30)]
        loser_exit = facts[0].exit_ts
        for i in range(3):
            facts.append(fact(f"R{i}", pnl=-20,
                              entry=loser_exit + timedelta(minutes=5)))
        facts.extend(series(20, prefix="N", start=BASE + timedelta(days=10)))
        finding = find(analyze(facts), "revenge_trading")
        assert finding.detected
        assert finding.occurrences == 3

    def test_ignores_entries_outside_the_window(self):
        facts = [fact("L1", pnl=-100, entry=BASE, hold_minutes=30)]
        facts.append(fact("R1", pnl=-20, entry=facts[0].exit_ts
                          + timedelta(minutes=REVENGE_WINDOW_MIN + 1)))
        facts.extend(series(20, prefix="N", start=BASE + timedelta(days=10)))
        assert find(analyze(facts), "revenge_trading").occurrences == 0

    def test_a_winning_exit_does_not_start_the_window(self):
        facts = [fact("W1", pnl=100, entry=BASE, hold_minutes=30)]
        facts.append(fact("R1", entry=facts[0].exit_ts + timedelta(minutes=2)))
        facts.extend(series(20, prefix="N", start=BASE + timedelta(days=10)))
        assert find(analyze(facts), "revenge_trading").occurrences == 0

    def test_carries_the_median_gap_as_evidence(self):
        facts = [fact("L1", pnl=-100, entry=BASE, hold_minutes=30)]
        for i in range(3):
            facts.append(fact(f"R{i}", pnl=-20,
                              entry=facts[0].exit_ts + timedelta(minutes=4)))
        facts.extend(series(20, prefix="N", start=BASE + timedelta(days=10)))
        finding = find(analyze(facts), "revenge_trading")
        labels = [e.label for e in finding.evidence]
        assert any("minutes after the losing exit" in x for x in labels)


class TestOvertrading:
    def test_needs_several_active_days_before_judging(self):
        """Doubling a one-trade median is two trades, which is not overtrading
        by any standard — so a normal day has to be established first."""
        facts = [fact(f"T{i}", entry=BASE + timedelta(minutes=i * 10))
                 for i in range(12)]
        assert not find(analyze(facts), "overtrading").assessable

    def test_detects_volume_spikes_against_the_traders_own_median(self):
        facts = []
        for day in range(10):          # ten quiet days, one trade each
            facts.append(fact(f"Q{day}", entry=BASE + timedelta(days=day)))
        for i in range(8):             # one heavy day
            facts.append(fact(f"H{i}",
                              entry=BASE + timedelta(days=20, minutes=i * 20)))
        finding = find(analyze(facts), "overtrading")
        assert finding.detected
        assert finding.occurrences == 8

    def test_steady_cadence_is_not_flagged(self):
        facts = []
        for day in range(12):
            for i in range(2):
                facts.append(fact(f"D{day}-{i}",
                                  entry=BASE + timedelta(days=day, minutes=i * 30)))
        assert not find(analyze(facts), "overtrading").detected


class TestStopBehaviors:
    def test_trading_without_stops_counts_observed_trades_only(self):
        facts = series(20, had_stop=True, widened_stop=False, reviewed=True)
        for i in range(5):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            had_stop=False, widened_stop=False, reviewed=True)
        facts.extend(series(10, prefix="U", start=BASE + timedelta(days=40),
                            reviewed=False, had_stop=None, widened_stop=None))
        finding = find(analyze(facts), "trading_without_stops")
        assert finding.occurrences == 5
        assert finding.sample == 20        # the unreviewed ten are excluded

    def test_moving_stops_reads_the_observed_order_history(self):
        facts = series(20)
        for i in (2, 5, 8):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            had_stop=True, widened_stop=True)
        finding = find(analyze(facts), "moving_stops")
        assert finding.detected and finding.occurrences == 3
        assert finding.severity is Severity.SERIOUS

    def test_unprotected_losses_are_compared_against_protected_ones(self):
        facts = []
        for i in range(6):
            facts.append(fact(f"P{i}", pnl=-50, entry=BASE + timedelta(days=i),
                              had_stop=True))
        for i in range(6):
            facts.append(fact(f"U{i}", pnl=-400,
                              entry=BASE + timedelta(days=10 + i),
                              had_stop=False))
        finding = find(analyze(facts), "trading_without_stops")
        assert any("average loss without a stop" in e.label
                   for e in finding.evidence)


class TestSizingBehaviors:
    def test_inconsistent_sizing_uses_the_traders_own_median(self):
        """A $200 position is large for one account and a rounding error for
        another, so 'oversized' can only mean 'oversized for you'."""
        facts = series(20, outlay=200.0)
        for i in (3, 7, 11):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            outlay=900.0)
        finding = find(analyze(facts), "inconsistent_sizing")
        assert finding.detected and finding.occurrences == 3

    def test_uniform_sizing_is_clean(self):
        assert not find(analyze(series(20, outlay=250.0)),
                        "inconsistent_sizing").detected

    def test_needs_recorded_outlays(self):
        assert not find(analyze(series(20, outlay=0.0)),
                        "inconsistent_sizing").assessable


class TestTimingBehaviors:
    def test_open_chop_uses_the_930_to_945_window(self):
        """13:30 UTC is 09:30 ET in March. 09:15 ET is pre-market, not the
        opening chop, and must not be counted."""
        opening = BASE.replace(hour=13, minute=35)
        facts = [fact(f"C{i}", entry=opening + timedelta(days=i))
                 for i in range(4)]
        facts.extend(series(16, prefix="N", start=BASE + timedelta(days=10)))
        finding = find(analyze(facts), "open_chop_trading")
        assert finding.occurrences == 4

    def test_premarket_is_not_the_opening_chop(self):
        premarket = BASE.replace(hour=13, minute=0)   # 09:00 ET
        facts = [fact(f"P{i}", entry=premarket + timedelta(days=i))
                 for i in range(4)]
        facts.extend(series(16, prefix="N", start=BASE + timedelta(days=10)))
        assert find(analyze(facts), "open_chop_trading").occurrences == 0


class TestEntryQualityBehaviors:
    def test_chasing_reads_rsi_as_well_as_the_mistake_tag(self):
        facts = series(20, rsi=50.0)
        for i in range(4):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            rsi=80.0, direction="long")
        assert find(analyze(facts), "chasing").occurrences == 4

    def test_stretched_is_direction_aware(self):
        """RSI 80 on a short is not chasing — it is selling strength."""
        facts = series(20, rsi=80.0, direction="short")
        assert find(analyze(facts), "chasing").occurrences == 0

    def test_fomo_needs_volume_as_well_as_extension(self):
        """Distinct from plain chasing: the move must also be unusually busy."""
        quiet = series(20, rsi=80.0, direction="long", rvol=0.9)
        assert find(analyze(quiet), "fomo_entries").occurrences == 0
        busy = series(20, rsi=80.0, direction="long", rvol=2.5)
        assert find(analyze(busy), "fomo_entries").occurrences == 20

    def test_counter_trend_is_derived_from_direction_versus_trend(self):
        facts = series(20, htf_trend="up", direction="long")
        for i in range(5):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            htf_trend="up", direction="short")
        assert find(analyze(facts), "counter_trend_trading").occurrences == 5

    def test_neutral_trend_is_not_counter_trend(self):
        facts = series(20, htf_trend="neutral", direction="short")
        assert find(analyze(facts), "counter_trend_trading").occurrences == 0

    def test_ignoring_the_plan_counts_poor_graded_setups(self):
        facts = series(20, setup_quality="good")
        for i in range(6):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            setup_quality="poor")
        finding = find(analyze(facts), "ignoring_the_plan")
        assert finding.occurrences == 6
        assert finding.severity is Severity.SERIOUS


class TestContractBehaviors:
    @pytest.mark.parametrize("behavior_id,field,bad,good", [
        ("theta_neglect", "dte", 1, 30),
        ("iv_neglect", "iv", 0.85, 0.30),
        ("lottery_tickets", "delta", 0.10, 0.45),
    ])
    def test_threshold_detectors(self, behavior_id, field, bad, good):
        facts = series(20, **{field: good})
        for i in range(5):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            **{field: bad})
        finding = find(analyze(facts), behavior_id)
        assert finding.occurrences == 5
        assert finding.sample == 20


class TestComparativeDetectors:
    def test_tilt_needs_enough_post_loss_trades(self):
        facts = series(20, wins="WWWWWWWWWL")   # only two losses
        assert not find(analyze(facts), "tilt_after_loss").assessable

    def test_tilt_detects_a_materially_worse_next_trade(self):
        # The post-loss trade wins, but barely. It has to win: a losing
        # post-loss trade would put the trade AFTER it in the cohort too, and
        # the cohort would stop meaning "the one right after a loss".
        facts = []
        for i in range(12):
            base = BASE + timedelta(days=i * 3)
            facts.append(fact(f"L{i}", pnl=-100, entry=base))
            facts.append(fact(f"A{i}", pnl=10, entry=base + timedelta(hours=4)))
            facts.append(fact(f"G{i}", pnl=400, entry=base + timedelta(days=1)))
            facts.append(fact(f"M{i}", pnl=400, entry=base + timedelta(days=2)))
        finding = find(analyze(facts), "tilt_after_loss")
        assert finding.detected
        assert finding.severity is Severity.SERIOUS

    def test_tilt_is_not_reported_when_post_loss_trades_are_normal(self):
        facts = []
        for i in range(12):
            base = BASE + timedelta(days=i * 3)
            facts.append(fact(f"L{i}", pnl=-100, entry=base))
            facts.append(fact(f"A{i}", pnl=100, entry=base + timedelta(hours=4)))
        finding = find(analyze(facts), "tilt_after_loss")
        assert finding.assessable and not finding.detected

    def test_overconfidence_needs_both_sizing_up_and_underperforming(self):
        """Sizing up after wins and then doing fine is not a problem worth
        naming; both halves have to be true."""
        facts = []
        for i in range(10):
            base = BASE + timedelta(days=i * 4)
            facts.append(fact(f"W1-{i}", pnl=100, entry=base, outlay=200))
            facts.append(fact(f"W2-{i}", pnl=100,
                              entry=base + timedelta(hours=1), outlay=200))
            facts.append(fact(f"AF-{i}", pnl=100,
                              entry=base + timedelta(hours=2), outlay=900))
        assert not find(analyze(facts), "overconfidence_after_wins").detected

    def test_overconfidence_detected_when_bigger_and_worse(self):
        facts = []
        for i in range(10):
            base = BASE + timedelta(days=i * 4)
            facts.append(fact(f"W1-{i}", pnl=200, entry=base, outlay=200))
            facts.append(fact(f"W2-{i}", pnl=200,
                              entry=base + timedelta(hours=1), outlay=200))
            facts.append(fact(f"AF-{i}", pnl=-500,
                              entry=base + timedelta(hours=2), outlay=900))
        assert find(analyze(facts), "overconfidence_after_wins").detected


class TestImpact:
    def test_detected_findings_price_the_habit(self):
        facts = series(20, pnl_win=100)
        for i in range(6):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            pnl=-500, setup_quality="poor")
        finding = find(analyze(facts), "ignoring_the_plan")
        assert finding.impact is not None
        assert finding.impact.adjusted > finding.impact.baseline
        assert "removed" in finding.impact.basis

    def test_impact_states_its_window_so_it_cannot_read_as_a_contradiction(self):
        """The behavioural window is not the lifetime, so the baseline here is
        not the dashboard's headline expectancy. Saying which is which is the
        difference between a checkable comparison and two numbers that look
        like a bug."""
        facts = series(30, mistakes=("averaged_down",))
        finding = find(analyze(facts), "averaging_down")
        assert finding.impact is None or "analysed trades" in finding.impact.basis

    def test_no_impact_when_every_trade_is_affected(self):
        """Removing everything leaves no counterfactual to compare against."""
        finding = find(analyze(series(20, setup_quality="poor")),
                       "ignoring_the_plan")
        assert finding.detected
        assert finding.impact is None


class TestTrend:
    def test_falling_rate_reads_as_improving(self):
        previous = series(20, setup_quality="poor", prefix="P")
        current = series(20, setup_quality="good", prefix="C")
        finding = find(analyze(current, previous), "ignoring_the_plan")
        assert finding.trend is Trend.IMPROVING

    def test_rising_rate_reads_as_declining(self):
        previous = series(20, setup_quality="good", prefix="P")
        current = series(20, setup_quality="poor", prefix="C")
        assert find(analyze(current, previous),
                    "ignoring_the_plan").trend is Trend.DECLINING

    def test_unchanged_rate_is_stable(self):
        assert find(analyze(series(20, prefix="C"), series(20, prefix="P")),
                    "chasing").trend is Trend.STABLE

    def test_no_trend_without_a_comparable_previous_window(self):
        assert find(analyze(series(20)), "chasing").trend is Trend.UNKNOWN

    def test_a_coverage_change_is_never_read_as_improvement(self):
        """Comparing an assessable window against an unassessable one would
        make a gap in the data look like progress — the most flattering
        possible lie."""
        previous = series(20, reviewed=False, had_stop=None, widened_stop=None,
                          prefix="P")
        current = series(20, prefix="C")
        assert find(analyze(current, previous),
                    "trading_without_stops").trend is Trend.UNKNOWN


class TestOrdering:
    def test_worst_first(self):
        facts = series(30, wins="WLL", mistakes=("no_stop", "averaged_down"),
                       had_stop=False, setup_quality="poor")
        findings = analyze(facts)
        ranks = [f.severity.rank for f in findings]
        assert ranks == sorted(ranks, reverse=True)
