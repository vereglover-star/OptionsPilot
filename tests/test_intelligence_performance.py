"""PerformanceEngine and the time-window machinery.

The metric registry is a public contract — goals target metrics by key, the UI
renders them by key, the report writer looks them up by key — so a chunk of this
file is about the registry's shape and completeness rather than about any single
average.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.intelligence import stats, windows
from optionspilot.intelligence.models import Trend
from optionspilot.intelligence.performance import (
    METRIC_SPECS, PerformanceEngine, compute, period_series,
)

from tests.intelligence_helpers import BASE, fact, series


class TestMetricRegistry:
    def test_every_key_has_a_spec_and_every_spec_a_value(self):
        """The registry is addressed by key from four other modules; a key with
        no spec or a spec with no computation is a silent hole in all of them."""
        metrics = compute(series(5))
        assert set(metrics) == set(METRIC_SPECS)

    def test_registry_is_complete_even_for_an_empty_history(self):
        """A consumer must always be able to look up a key it knows about and
        never branch on absence.

        Counts are 0 (a real measurement — you have zero trades); every ratio,
        average and derived figure is None (no measurement was possible). That
        distinction is what keeps a new user's dashboard from reading "$0.00
        expectancy" as though it had been earned.
        """
        metrics = compute([])
        assert set(metrics) == set(METRIC_SPECS)
        counts = {"trades", "wins", "losses"}
        assert all(metrics[k].value == 0 for k in counts)
        assert all(m.value is None for k, m in metrics.items() if k not in counts)

    def test_specs_declare_direction(self):
        assert METRIC_SPECS["expectancy"][2] is True
        assert METRIC_SPECS["max_drawdown"][2] is False
        assert METRIC_SPECS["mistake_rate"][2] is False

    def test_metric_carries_its_own_sample_size(self):
        facts = series(4, wins="WWLL")
        metrics = compute(facts)
        assert metrics["avg_win"].sample == 2
        assert metrics["avg_loss"].sample == 2
        assert metrics["trades"].sample == 4


class TestHeadlineMetrics:
    def test_expectancy_and_win_rate(self):
        metrics = compute(series(4, wins="WWWL", pnl_win=100, pnl_loss=-100))
        assert metrics["expectancy"].value == pytest.approx(50.0)
        assert metrics["win_rate"].value == pytest.approx(75.0)

    def test_win_rate_is_a_percentage_not_a_fraction(self):
        """The unit says '%', so the value must be 75 and not 0.75 — a goal
        targeting this metric compares against a percentage."""
        metrics = compute(series(4, wins="WWWL"))
        assert metrics["win_rate"].unit == "%"
        assert metrics["win_rate"].value > 1

    def test_profit_factor_is_rounded_for_display(self):
        metrics = compute(series(4, wins="WWWL", pnl_win=100, pnl_loss=-50))
        assert metrics["profit_factor"].value == 6.0

    def test_hold_asymmetry_below_one_means_losers_held_longer(self):
        facts = [fact("W1", pnl=100, hold_minutes=20),
                 fact("W2", pnl=100, hold_minutes=20),
                 fact("L1", pnl=-100, hold_minutes=200),
                 fact("L2", pnl=-100, hold_minutes=200)]
        assert compute(facts)["hold_asymmetry"].value == pytest.approx(0.1)

    def test_r_multiple_is_none_when_never_measurable(self):
        """Different from zero: 'you make no R' and 'your R was never
        measurable' need different advice."""
        metrics = compute(series(5, r_multiple=None))
        assert metrics["avg_r"].value is None
        assert metrics["avg_r"].sample == 0


class TestStreaks:
    def test_current_streak_is_signed(self):
        assert compute(series(3, wins="WWW"))["current_streak"].value == 3
        assert compute(series(3, wins="LLL"))["current_streak"].value == -3

    def test_longest_streaks(self):
        metrics = compute(series(8, wins="WWWLWWLL"))
        assert metrics["longest_win_streak"].value == 3
        assert metrics["longest_loss_streak"].value == 2

    def test_a_scratch_neither_extends_nor_breaks_a_streak(self):
        """A flat trade is not evidence in either direction."""
        facts = [fact("A", pnl=10), fact("B", pnl=0.0), fact("C", pnl=10)]
        assert compute(facts)["current_streak"].value == 2


class TestDailyAggregation:
    def test_worst_day_sums_the_day_not_the_trade(self):
        """A daily loss limit is written in days. Three small losses inside an
        hour is one bad day, not three survivable trades."""
        day = BASE
        facts = [fact(f"T{i}", pnl=-40, entry=day + timedelta(minutes=i * 10))
                 for i in range(3)]
        facts.append(fact("T9", pnl=-50, entry=day + timedelta(days=1)))
        metrics = compute(facts)
        assert metrics["worst_day_pnl"].value == pytest.approx(-120.0)
        assert metrics["worst_trade"].value == pytest.approx(-50.0)

    def test_trades_per_active_day(self):
        day = BASE
        facts = [fact(f"T{i}", entry=day + timedelta(minutes=i * 5))
                 for i in range(4)]
        facts.append(fact("T9", entry=day + timedelta(days=1)))
        metrics = compute(facts)
        assert metrics["active_days"].value == 2
        assert metrics["trades_per_active_day"].value == pytest.approx(2.5)


class TestProcessMetrics:
    def test_stop_discipline_counts_only_observed_trades(self):
        """An unreviewed trade could not have violated the rule, so it must not
        appear in the denominator."""
        facts = [fact("A", had_stop=True, widened_stop=False, reviewed=True),
                 fact("B", had_stop=False, reviewed=True),
                 fact("C", had_stop=None, widened_stop=None, reviewed=False)]
        metrics = compute(facts)
        assert metrics["stop_discipline_rate"].value == pytest.approx(50.0)
        assert metrics["stop_discipline_rate"].sample == 2

    def test_widening_a_stop_breaks_discipline_even_with_a_stop_present(self):
        facts = [fact("A", had_stop=True, widened_stop=True, reviewed=True)]
        assert compute(facts)["stop_discipline_rate"].value == 0.0

    def test_clean_trade_rate_and_mistake_rate(self):
        facts = [fact("A", mistakes=(), reviewed=True),
                 fact("B", mistakes=("no_stop", "chased_entry"), reviewed=True)]
        metrics = compute(facts)
        assert metrics["clean_trade_rate"].value == pytest.approx(50.0)
        assert metrics["mistake_rate"].value == pytest.approx(1.0)

    def test_process_metrics_are_none_with_no_reviews(self):
        metrics = compute(series(5, reviewed=False, had_stop=None,
                                 widened_stop=None, had_target=None,
                                 process_score=None))
        assert metrics["stop_discipline_rate"].value is None
        assert metrics["avg_process_score"].value is None


class TestConsistencyGrain:
    def test_consistency_is_measured_over_periods_not_single_trades(self):
        """Per-trade option P/L varies enormously for everybody, so scoring it
        would hand every trader the same ~20 and say nothing. What a trader
        means by 'consistent' is that their WEEKS look alike."""
        # Six weeks, two trades each, every week netting exactly the same.
        facts = []
        for week in range(6):
            base = BASE + timedelta(days=7 * week)
            facts.append(fact(f"A{week}", pnl=300, entry=base))
            facts.append(fact(f"B{week}", pnl=-100, entry=base + timedelta(days=1)))
        metrics = compute(facts)
        assert metrics["consistency"].value == pytest.approx(100.0)
        # …even though the individual trades are wildly different.
        assert stats.consistency([f.pnl for f in facts]) < 60

    def test_falls_back_to_days_then_to_trades(self):
        facts = [fact(f"T{i}", entry=BASE + timedelta(minutes=i * 30))
                 for i in range(3)]
        assert compute(facts)["consistency"].value is not None


class TestPeriodSeries:
    def test_buckets_are_chronological_and_labelled(self):
        facts = series(6, spacing_days=10)
        months = period_series(facts, "month")
        assert [p.key for p in months] == sorted(p.key for p in months)
        assert all(p.label for p in months)

    def test_period_totals_reconcile_with_the_whole(self):
        facts = series(9, wins="WLW", spacing_days=12)
        months = period_series(facts, "month")
        assert sum(p.trades for p in months) == len(facts)
        assert sum(p.pnl for p in months) == pytest.approx(
            compute(facts)["total_pnl"].value)

    def test_empty_history_yields_no_periods(self):
        assert period_series([], "month") == ()


class TestWindows:
    def test_trade_counted_window_takes_the_latest(self):
        facts = series(30)
        selected = windows.WINDOWS["last_20_trades"].select(facts)
        assert len(selected) == 20
        assert selected[-1].trade_id == facts[-1].trade_id

    def test_day_window_measures_from_the_last_trade_when_no_clock_given(self):
        """Reproducibility: a snapshot recomputed a week later with no new
        trades must not silently empty its own 30-day window and report that the
        trader's discipline has become unmeasurable."""
        facts = series(10, spacing_days=5)      # spans 45 days
        selected = windows.WINDOWS["last_30d"].select(facts)
        assert 0 < len(selected) < len(facts)

    def test_day_window_honours_an_explicit_now(self):
        facts = series(10, spacing_days=5)
        far_future = facts[-1].entry_ts + timedelta(days=400)
        assert windows.WINDOWS["last_30d"].select(facts, now=far_future) == []

    def test_lifetime_selects_everything(self):
        facts = series(7)
        assert len(windows.WINDOWS["lifetime"].select(facts)) == 7

    def test_unknown_window_degrades_to_lifetime(self):
        """A goal persisted by a newer build must widen, not disappear."""
        assert windows.resolve("invented_later").name == "lifetime"


class TestPeriodKeys:
    def test_keys_sort_lexically_in_chronological_order(self):
        """The whole package orders time series by plain string sort. A naive
        '2026-7' would sort after '2026-10' and silently reverse a trend."""
        ts = [datetime(2026, m, 15, 17, 0, tzinfo=timezone.utc)
              for m in (2, 7, 10, 12)]
        for period in ("day", "week", "month", "quarter", "year"):
            keys = [windows.period_key(t, period) for t in ts]
            assert keys == sorted(keys), period

    def test_bucketing_happens_in_exchange_time(self):
        """01:00 UTC Saturday is 21:00 ET Friday — a Friday trade. Bucketing it
        by UTC would move it into next week's report."""
        late_friday = datetime(2026, 3, 7, 1, 0, tzinfo=timezone.utc)
        assert windows.period_key(late_friday, "day") == "2026-03-06"

    def test_unknown_period_raises(self):
        with pytest.raises(ValueError):
            windows.period_key(BASE, "fortnight")

    def test_labels_never_raise_on_a_malformed_key(self):
        assert windows.period_label("garbage", "month") == "garbage"

    def test_previous_and_latest_requires_both_sides_to_qualify(self):
        buckets = {"2026-01": series(2), "2026-02": series(9)}
        previous, latest = windows.previous_and_latest(buckets, min_trades=5)
        assert previous is None
        assert latest[0] == "2026-02"


class TestPerformanceEngine:
    def test_produces_metrics_periods_windows_and_trends(self):
        result = PerformanceEngine().analyze(series(40, spacing_days=3))
        assert result.metrics["trades"].value == 40
        assert set(result.periods) == set(windows.PERIODS)
        assert set(result.windows) == set(windows.WINDOWS)
        assert result.trends

    def test_trend_is_unknown_below_two_months(self):
        result = PerformanceEngine().analyze(series(5, spacing_days=1))
        assert all(t is Trend.UNKNOWN for t in result.trends.values())

    def test_improving_expectancy_is_detected_across_months(self):
        facts = []
        for month, pnl in enumerate((10, 40, 90, 160), start=1):
            base = datetime(2026, month, 6, 15, 0, tzinfo=timezone.utc)
            facts.extend(fact(f"M{month}-{i}", pnl=pnl,
                              entry=base + timedelta(days=i))
                         for i in range(5))
        result = PerformanceEngine().analyze(facts)
        assert result.trends["expectancy"] is Trend.IMPROVING

    def test_window_value_helper(self):
        result = PerformanceEngine().analyze(series(30))
        assert result.window_value("last_20_trades", "trades") == 20
        assert result.window_value("nonexistent", "trades") is None
