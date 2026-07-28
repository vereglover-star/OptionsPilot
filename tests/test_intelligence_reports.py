"""TimelineEngine and ReportEngine — the narrative outputs.

These two are where a coaching system most easily becomes dishonest, because
prose hides its own sample size. Both guards are asserted here: a comparison
needs enough trades on *both* sides, and a streak is counted only over trades
that could have broken it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.intelligence.reports import MIN_PERIOD_TRADES, ReportEngine
from optionspilot.intelligence.timeline import TimelineEngine

from tests.intelligence_helpers import BASE, fact, series

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def months(*specs, prefix="M"):
    """Build facts across consecutive months. Each spec is (count, pnl)."""
    out = []
    for index, (count, pnl) in enumerate(specs):
        base = datetime(2026, 3 + index, 6, 15, 0, tzinfo=timezone.utc)
        out.extend(fact(f"{prefix}{index}-{i}", pnl=pnl,
                        entry=base + timedelta(days=i)) for i in range(count))
    return out


class TestTimelineComparisons:
    def test_nothing_is_narrated_without_two_qualifying_months(self):
        """'Your average risk has fallen 18% since May' requires a May worth
        comparing."""
        entries = TimelineEngine().build(tuple(months((2, 100), (9, 300))))
        assert not [e for e in entries if e.kind in ("improvement", "decline")]

    def test_an_improvement_is_narrated_with_both_sides_shown(self):
        entries = TimelineEngine().build(tuple(months((8, 50), (8, 400))))
        improvement = next(e for e in entries if e.kind == "improvement")
        assert "since" in improvement.headline
        assert len(improvement.evidence) == 2
        assert all(e.sample >= MIN_PERIOD_TRADES for e in improvement.evidence)

    def test_a_decline_is_narrated_too(self):
        entries = TimelineEngine().build(tuple(months((8, 400), (8, 50))))
        assert any(e.kind == "decline" for e in entries)

    def test_a_sign_change_is_stated_not_expressed_as_a_percentage(self):
        """'Expectancy declined 114%' is arithmetically true and tells the
        trader nothing."""
        entries = TimelineEngine().build(tuple(months((8, 200), (8, -200))))
        expectancy = [e for e in entries if "expectancy" in e.headline.lower()]
        assert expectancy
        assert any("turned negative" in e.headline for e in expectancy)
        assert not any("%" in e.headline for e in expectancy)

    def test_a_small_move_is_not_narrated(self):
        entries = TimelineEngine().build(tuple(months((8, 100), (8, 103))))
        assert not [e for e in entries if e.kind in ("improvement", "decline")]


class TestTimelineStreaks:
    def test_a_streak_counts_only_trades_that_could_have_broken_it(self):
        """'27 consecutive trades without a stop violation' is a lie if twenty
        of them were never reviewed."""
        facts = series(12)
        for i in (2, 3, 4):
            facts[i] = fact(facts[i].trade_id, entry=facts[i].entry_ts,
                            had_stop=None, widened_stop=None, reviewed=False)
        streak = next(e for e in TimelineEngine().build(tuple(facts))
                      if "stop-loss rule" in e.headline)
        assert "9 consecutive" in streak.headline
        assert "could not have broken the streak" in streak.detail

    def test_no_streak_is_claimed_below_five(self):
        facts = series(10)
        facts[-2] = fact(facts[-2].trade_id, entry=facts[-2].entry_ts,
                         had_stop=False)
        assert not any("stop-loss rule" in e.headline
                       for e in TimelineEngine().build(tuple(facts)))

    def test_clean_trade_streaks_are_reported(self):
        entries = TimelineEngine().build(tuple(series(20, mistakes=())))
        assert any("no process mistake" in e.headline for e in entries)

    def test_streaks_cite_the_trades_behind_them(self):
        for entry in TimelineEngine().build(tuple(series(20))):
            if entry.kind == "streak":
                assert entry.evidence

    def test_win_streaks_come_with_a_warning_not_a_celebration(self):
        entries = TimelineEngine().build(tuple(series(12, wins="WWWWWWWWWWWW")))
        win_streak = next(e for e in entries if "winning trades in a row" in e.headline)
        assert "distrusting" in win_streak.detail


class TestTimelineMilestones:
    def test_milestones_are_dated_from_the_trade_that_reached_them(self):
        facts = series(60)
        milestone = next(e for e in TimelineEngine().build(tuple(facts))
                         if e.kind == "milestone")
        assert milestone.period == facts[49].entry_date or \
            milestone.period == facts[24].entry_date

    def test_at_most_two_milestones_so_they_do_not_bury_real_changes(self):
        entries = TimelineEngine().build(tuple(series(600)))
        assert len([e for e in entries if e.kind == "milestone"]) <= 2

    def test_no_milestone_below_the_first_threshold(self):
        assert not [e for e in TimelineEngine().build(tuple(series(10)))
                    if e.kind == "milestone"]


class TestTimelineOrdering:
    def test_biggest_change_first(self):
        entries = TimelineEngine().build(tuple(months((10, 50), (10, 500))))
        magnitudes = [e.magnitude for e in entries]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_empty_history_produces_no_timeline(self):
        assert TimelineEngine().build(()) == ()


def build_report(facts, period="month", **kwargs):
    defaults = dict(behaviors=(), patterns=(), scores=(), recommendations=(),
                    lessons=(), goals=(), timeline=(), generated=NOW)
    defaults.update(kwargs)
    return ReportEngine().build(facts=tuple(facts), period=period, **defaults)


class TestReports:
    def test_no_report_without_any_period_to_report_on(self):
        """An empty report implies the analysis ran and found nothing, rather
        than that it never had anything to run on."""
        assert build_report([]) is None

    def test_a_report_has_a_title_summary_and_sections(self):
        report = build_report(months((8, 100), (8, 200)))
        assert report.title and report.summary and report.sections
        assert report.kind == "monthly"

    def test_weekly_and_monthly_are_both_available(self):
        facts = series(30, spacing_days=2)
        assert build_report(facts, "week").kind == "weekly"
        assert build_report(facts, "month").kind == "monthly"

    def test_a_thin_period_is_explicitly_provisional(self):
        report = build_report(series(3, spacing_days=1))
        assert "provisional" in report.summary.lower()

    def test_a_thin_period_is_never_compared_against_a_full_one(self):
        """Comparing a full month against a four-trade week produces confident
        percentages out of noise."""
        facts = months((20, 100)) + [
            fact("late", pnl=-500,
                 entry=datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc))]
        report = build_report(facts)
        changed = next(s for s in report.sections if s.heading == "What changed")
        assert "not a pair of periods" in " ".join(changed.body) or \
            "no pair of periods" in " ".join(changed.body)

    def test_a_real_change_is_narrated_in_both_directions(self):
        report = build_report(months((10, 50), (10, 400)))
        changed = next(s for s in report.sections if s.heading == "What changed")
        assert any("Improved" in line for line in changed.body)

    def test_an_unchanged_period_says_so_rather_than_inventing_movement(self):
        report = build_report(months((10, 100), (10, 100)))
        changed = next(s for s in report.sections if s.heading == "What changed")
        assert any("more than 10%" in line for line in changed.body)

    def test_empty_sections_are_dropped(self):
        report = build_report(series(20, spacing_days=2))
        assert all(section.body for section in report.sections)

    def test_headline_metrics_ride_along_with_the_prose(self):
        report = build_report(months((10, 100), (10, 200)))
        happened = next(s for s in report.sections
                        if s.heading == "What happened")
        assert happened.metrics
        assert all(m.value is not None for m in happened.metrics)

    def test_focus_section_says_so_when_nothing_needs_changing(self):
        report = build_report(series(20, spacing_days=2))
        focus = next(s for s in report.sections
                     if s.heading == "What to focus on next")
        assert any("Nothing measurable" in line for line in focus.body)

    def test_the_report_reports_the_most_recent_period(self):
        facts = months((10, 100), (10, 200), (10, 300))
        report = build_report(facts)
        assert report.period == "2026-05"
        assert report.trades == 10

    def test_serialises_cleanly(self):
        import json
        report = build_report(months((10, 100), (10, 200)))
        payload = json.dumps(report.to_dict())
        assert "Infinity" not in payload and "NaN" not in payload
