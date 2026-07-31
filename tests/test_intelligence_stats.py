"""Statistical primitives — the layer every other engine's arithmetic rests on.

Heavy on degenerate input by design: the whole package's promise is that a
trader with two trades gets "not enough data" rather than a confident number, and
that promise is kept or broken here.
"""

from __future__ import annotations

import json
import math

import pytest

from optionspilot.intelligence import stats
from optionspilot.intelligence.models import Confidence, Trend


class TestCentralTendency:
    def test_mean_and_median(self):
        assert stats.mean([1, 2, 3]) == 2
        assert stats.median([1, 2, 3]) == 2
        assert stats.median([1, 2, 3, 4]) == 2.5

    def test_empty_returns_none_never_zero(self):
        """Zero is a measurement; None is the absence of one. Collapsing them
        would let a dashboard show 0.00 expectancy for a trader with no trades."""
        assert stats.mean([]) is None
        assert stats.median([]) is None
        assert stats.pstdev([]) is None
        assert stats.win_rate([]) is None
        assert stats.expectancy([]) is None

    def test_nan_and_inf_are_dropped_not_propagated(self):
        assert stats.mean([1.0, float("nan"), 3.0]) == 2.0
        assert stats.mean([float("inf"), 2.0]) == 2.0

    def test_pstdev_needs_two_points(self):
        assert stats.pstdev([5.0]) is None
        assert stats.pstdev([2.0, 4.0]) == 1.0

    def test_percentile_interpolates_and_clamps(self):
        xs = [0, 10, 20, 30, 40]
        assert stats.percentile(xs, 0.5) == 20
        assert stats.percentile(xs, 0.0) == 0
        assert stats.percentile(xs, 1.0) == 40
        assert stats.percentile(xs, 2.0) == 40      # clamped, not an IndexError
        assert stats.percentile([], 0.5) is None


class TestTradingRatios:
    def test_win_rate_counts_scratches_as_non_wins(self):
        """Matches TradeRecord.is_win (pnl > 0). A scratch is not a win."""
        assert stats.win_rate([1.0, 0.0, -1.0]) == pytest.approx(1 / 3)

    def test_profit_factor(self):
        assert stats.profit_factor([100, -50]) == 2.0

    def test_profit_factor_is_infinite_with_no_losers(self):
        assert stats.profit_factor([10, 20]) == math.inf

    def test_profit_factor_none_when_nothing_to_divide(self):
        assert stats.profit_factor([]) is None
        assert stats.profit_factor([0.0, 0.0]) is None

    def test_avg_loss_is_returned_negative(self):
        assert stats.avg_loss([100, -40, -60]) == -50.0

    def test_payoff_ratio(self):
        assert stats.payoff_ratio([100, -50]) == 2.0
        assert stats.payoff_ratio([100, 200]) is None   # no losers to divide by

    def test_sharpe_like_is_none_without_variance(self):
        assert stats.sharpe_like([5.0, 5.0, 5.0]) is None
        assert stats.sharpe_like([10, -10, 10, -10]) == 0.0


class TestDrawdown:
    def test_equity_curve_starts_at_zero(self):
        assert stats.equity_curve([10, -5]) == [0.0, 10.0, 5.0]

    def test_max_drawdown_measures_peak_to_trough(self):
        assert stats.max_drawdown([100, -30, -20, 50]) == 50.0

    def test_no_drawdown_on_a_monotonic_curve(self):
        assert stats.max_drawdown([10, 20, 30]) == 0.0

    def test_recovery_factor_is_none_without_a_drawdown(self):
        """Undefined, not infinite skill — a two-trade winning start proves
        nothing about recovery."""
        assert stats.recovery_factor([10, 20]) is None
        assert stats.recovery_factor([]) is None

    def test_recovery_factor(self):
        # net +100, max drawdown 50
        assert stats.recovery_factor([100, -50, 50]) == 2.0


class TestConsistency:
    def test_identical_values_score_100(self):
        assert stats.consistency([5.0, 5.0, 5.0]) == 100.0

    def test_spread_lowers_the_score(self):
        steady = stats.consistency([100, 105, 95])
        erratic = stats.consistency([100, 500, -300])
        assert steady > erratic

    def test_needs_two_points(self):
        assert stats.consistency([1.0]) is None

    def test_measures_steadiness_not_profitability(self):
        """A consistent loser scores well here. That is deliberate: combining
        steadiness with profitability makes it impossible to tell a consistent
        loser from an erratic winner, and they need different advice."""
        assert stats.consistency([-50.0, -50.0, -50.0]) == 100.0


class TestSignificance:
    def test_wilson_interval_stays_inside_zero_to_one(self):
        low, high = stats.wilson_interval(3, 4)
        assert 0.0 <= low <= high <= 1.0

    def test_wilson_interval_narrows_with_sample_size(self):
        small = stats.wilson_interval(15, 30)
        large = stats.wilson_interval(500, 1000)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_wilson_rejects_impossible_input(self):
        assert stats.wilson_interval(5, 0) is None
        assert stats.wilson_interval(11, 10) is None

    def test_two_proportion_p_detects_a_real_difference(self):
        p = stats.two_proportion_p(90, 100, 40, 100)
        assert p is not None and p < 0.001

    def test_two_proportion_p_is_high_for_identical_rates(self):
        assert stats.two_proportion_p(50, 100, 50, 100) == pytest.approx(1.0)

    def test_two_proportion_p_is_none_when_degenerate(self):
        """All wins on both sides leaves the test no variance to work with —
        'can't tell' is the honest answer, not p=1.0."""
        assert stats.two_proportion_p(10, 10, 10, 10) is None
        assert stats.two_proportion_p(0, 0, 5, 10) is None

    def test_small_samples_do_not_reach_significance(self):
        """4 wins from 5 vs 1 from 5 looks dramatic and proves nothing."""
        p = stats.two_proportion_p(4, 5, 1, 5)
        assert p is not None and p > 0.05


class TestTrend:
    def test_slope_of_a_rising_series(self):
        assert stats.linear_slope([1, 2, 3, 4]) == pytest.approx(1.0)

    def test_slope_needs_two_points(self):
        assert stats.linear_slope([5]) is None

    def test_flat_series_is_stable_not_improving(self):
        assert stats.trend_of([50, 50, 50]) is Trend.STABLE

    def test_tiny_wobble_is_stable(self):
        assert stats.trend_of([100.0, 100.4, 100.1, 100.3]) is Trend.STABLE

    def test_direction_respects_higher_is_better(self):
        rising = [1, 5, 9, 14]
        assert stats.trend_of(rising, higher_is_better=True) is Trend.IMPROVING
        assert stats.trend_of(rising, higher_is_better=False) is Trend.DECLINING

    def test_single_point_is_unknown(self):
        assert stats.trend_of([1]) is Trend.UNKNOWN


class TestConfidence:
    def test_bands_follow_the_sample_floors(self):
        assert stats.sample_confidence(2) is Confidence.NONE
        assert stats.sample_confidence(stats.MIN_SAMPLE_LOW) is Confidence.LOW
        assert stats.sample_confidence(stats.MIN_SAMPLE_MEDIUM) is Confidence.MEDIUM
        assert stats.sample_confidence(stats.MIN_SAMPLE_HIGH) is Confidence.HIGH

    def test_combine_takes_the_weakest_link(self):
        assert stats.combine_confidence(
            Confidence.HIGH, Confidence.LOW) is Confidence.LOW
        assert stats.combine_confidence() is Confidence.NONE

    def test_confidence_is_ordered(self):
        assert Confidence.HIGH > Confidence.MEDIUM > Confidence.LOW > Confidence.NONE
        assert Confidence.LOW <= Confidence.LOW


class TestSafeArithmetic:
    def test_pct_change_refuses_to_divide_by_zero(self):
        """'Improved by infinity percent' is not a sentence this system will
        produce."""
        assert stats.pct_change(0, 50) is None
        assert stats.pct_change(None, 50) is None
        assert stats.pct_change(50, None) is None

    def test_pct_change_uses_magnitude_so_signs_behave(self):
        assert stats.pct_change(-100, -50) == pytest.approx(50.0)

    def test_comparable_rejects_an_infinite_reading(self):
        """Profit factor is legitimately infinite for a period with no losers,
        and inf-vs-inf produces a NaN percentage. The timeline and the report
        writer both shipped 'your profit factor has declined nan%' before this
        gate existed."""
        assert stats.comparable(1.0, 2.0) is True
        assert stats.comparable(math.inf, math.inf) is False
        assert stats.comparable(1.0, math.inf) is False
        assert stats.comparable(None, 2.0) is False
        assert stats.comparable(float("nan"), 2.0) is False

    def test_safe_div(self):
        assert stats.safe_div(10, 2) == 5
        assert stats.safe_div(10, 0) is None
        assert stats.safe_div(None, 2) is None

    def test_clamp01(self):
        assert stats.clamp01(-3) == 0.0
        assert stats.clamp01(3) == 1.0


class TestJsonSafety:
    def test_infinite_profit_factor_survives_serialisation(self):
        """Profit factor legitimately reaches infinity. `json.dumps` emits
        `Infinity`, which is not valid JSON and breaks a browser parse — so the
        model layer converts it to None on the way out."""
        from optionspilot.intelligence.models import Metric

        metric = Metric("profit_factor", "Profit factor",
                        stats.profit_factor([10, 20]))
        payload = json.dumps(metric.to_dict())
        assert "Infinity" not in payload
        assert json.loads(payload)["value"] is None
