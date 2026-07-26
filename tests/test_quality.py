"""Semantic validation of candle history — the last gate before a chart.

`base.validate_candles` guarantees the frame's SHAPE. This module guarantees
its MEANING: that the bars are self-consistent, in the past, spaced like the
interval they claim to be, and not obviously fabricated. Every check here
exists because rendering the defect it catches produced a visible bug (an
inverted candle, a mislabelled axis, an inf-poisoned indicator) or would have.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.quality import (
    CALENDAR_TOLERANCE, HistoryReport, disagreement, validate_history,
)
from tests.marketdata_helpers import frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _at(start="2026-07-20 13:30", n=20, tf=Timeframe.M5):
    return frame(n, tf, start=pd.Timestamp(start, tz="UTC"))


class TestHappyPath:
    def test_clean_frame_scores_perfectly(self):
        df, report = validate_history(_at(), Timeframe.M5, now=NOW)
        assert len(df) == 20
        assert report.usable and report.score == 100.0
        assert report.issues == []
        assert report.first == df.index[0] and report.last == df.index[-1]

    def test_empty_input_is_unusable_but_does_not_raise(self):
        df, report = validate_history(pd.DataFrame(), Timeframe.M5, now=NOW)
        assert df.empty and not report.usable
        assert "no bars" in report.issues[0]

    def test_malformed_input_is_a_result_not_an_exception(self):
        """A provider handing back nonsense must fail over, not traceback."""
        bad = pd.DataFrame({"nope": [1, 2, 3]})
        df, report = validate_history(bad, Timeframe.M5, now=NOW)
        assert df.empty and not report.usable
        assert report.counts.get("malformed") == 1


class TestImpossibleTimestamps:
    def test_future_bars_are_dropped(self):
        df = _at(n=5)
        future = df.copy()
        future.index = pd.DatetimeIndex(
            list(df.index[:4]) + [pd.Timestamp(NOW + timedelta(days=2))],
            name="ts")
        out, report = validate_history(future, Timeframe.M5, now=NOW)
        assert len(out) == 4
        assert report.counts["future_ts"] == 1
        assert report.score < 100

    def test_the_forming_bar_is_not_treated_as_future(self):
        """Providers stamp the in-progress bar at its OPEN, which is a moment
        in the past; a tight tolerance would delete the live candle."""
        df = frame(5, Timeframe.M5, end=NOW)
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 5 and report.usable

    def test_an_all_future_frame_is_rejected_entirely(self):
        df = frame(5, Timeframe.M5, end=NOW + timedelta(days=5))
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert out.empty and not report.usable


class TestOHLCConsistency:
    def test_high_below_low_is_dropped(self):
        df = _at(n=6)
        df.iloc[2, df.columns.get_loc("high")] = 1.0     # below the low
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 5
        assert report.counts["ohlc_inconsistent"] == 1

    def test_high_below_the_body_is_dropped(self):
        """An inverted candle renders as a visibly broken bar."""
        df = _at(n=6)
        row = 3
        df.iloc[row, df.columns.get_loc("high")] = \
            float(df.iloc[row]["close"]) - 5.0
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 5
        assert report.counts["ohlc_inconsistent"] == 1

    def test_float_noise_is_tolerated(self):
        """Equal high/close differing in the 12th decimal is not a defect."""
        df = _at(n=6)
        df.iloc[1, df.columns.get_loc("high")] = \
            float(df.iloc[1]["close"]) * (1 - 1e-12)
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 6 and report.usable

    def test_every_bar_inconsistent_is_rejected_not_emptied_silently(self):
        df = _at(n=6)
        df["high"] = 0.5
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert out.empty and not report.usable
        assert any("OHLC" in i for i in report.issues)


class TestVolume:
    def test_negative_volume_is_zeroed_not_dropped(self):
        """A bad volume must never invalidate a correctly-priced bar."""
        df = _at(n=6)
        df.iloc[2, df.columns.get_loc("volume")] = -50.0
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 6
        assert out["volume"].iloc[2] == 0.0
        assert report.counts["negative_volume"] == 1

    def test_nonfinite_volume_is_zeroed_by_the_shape_gate(self):
        df = _at(n=6)
        df.iloc[3, df.columns.get_loc("volume")] = np.inf
        out, _ = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 6 and np.isfinite(out["volume"]).all()


class TestSpikes:
    def test_isolated_bad_print_is_removed(self):
        """A single bar 100x the frame's range, with neighbours agreeing with
        each other, is a bad print — the class of glitch that crushed the
        price scale and made real candles invisible."""
        df = _at(n=30)
        i = 15
        df.iloc[i, df.columns.get_loc("high")] = 100_000.0
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 29
        assert report.counts["price_spike"] == 1

    def test_a_real_trend_is_not_mistaken_for_spikes(self):
        df = _at(n=40)
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 40 and "price_spike" not in report.counts

    def test_the_newest_bar_is_never_dropped_as_a_spike(self):
        """The last bar is legitimately half-formed and often has an odd
        range; deleting it would make the live candle flicker in and out."""
        df = _at(n=30)
        df.iloc[-1, df.columns.get_loc("high")] = 100_000.0
        out, _ = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 30

    def test_short_frames_are_left_alone(self):
        """Fewer than five bars gives no meaningful median to compare against."""
        df = _at(n=4)
        df.iloc[1, df.columns.get_loc("high")] = 100_000.0
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert "price_spike" not in report.counts


class TestIntervalConformance:
    def test_daily_bars_served_for_a_minute_request_are_rejected(self):
        """Yahoo silently downgrades granularity under load. Rendering the
        result would mislabel the entire x-axis."""
        df = _at(n=10, tf=Timeframe.D1)
        out, report = validate_history(df, Timeframe.M1, now=NOW)
        assert out.empty and not report.usable and not report.interval_ok

    def test_minute_bars_served_for_a_daily_request_are_rejected(self):
        df = _at(n=10, tf=Timeframe.M1)
        out, report = validate_history(df, Timeframe.D1, now=NOW)
        assert out.empty and not report.usable

    def test_four_hour_bars_with_overnight_gaps_are_accepted(self):
        """THE regression this check was rewritten for: a US equity 4h chart
        has two bars per session and a ~20h overnight gap, so a 'share of bars
        on the grid' test rejected perfectly good data. Conformance is judged
        on the TIGHTEST spacing instead."""
        stamps = []
        day = pd.Timestamp("2026-07-06 12:00", tz="UTC")
        for d in range(8):
            base = day + pd.Timedelta(days=d)
            stamps += [base, base + pd.Timedelta(hours=4)]
        idx = pd.DatetimeIndex(stamps, name="ts")
        df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                           "close": 100.5, "volume": 10.0}, index=idx)
        out, report = validate_history(df, Timeframe.H4, now=NOW)
        assert len(out) == 16 and report.usable and report.interval_ok
        assert report.max_gap_intervals > 1        # recorded, not penalised
        assert report.score == 100.0               # gaps are not a defect

    def test_weekend_gaps_in_intraday_data_are_accepted(self):
        df = pd.concat([_at("2026-07-17 13:30", 30), _at("2026-07-20 13:30", 30)])
        out, report = validate_history(df, Timeframe.M5, now=NOW)
        assert len(out) == 60 and report.usable

    def test_monthly_bars_of_uneven_length_are_accepted(self):
        idx = pd.DatetimeIndex(pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
            utc=True), name="ts")
        df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                           "close": 100.5, "volume": 10.0}, index=idx)
        out, report = validate_history(df, Timeframe.MN1, now=NOW)
        assert len(out) == 4 and report.usable

    def test_two_bar_frames_are_not_judged(self):
        """Too short to infer spacing from — accepting beats guessing."""
        df = _at(n=2)
        out, report = validate_history(df, Timeframe.H4, now=NOW)
        assert len(out) == 2 and report.usable


class TestDuplicatesAndOrder:
    def test_duplicate_timestamps_collapse_to_the_newest(self):
        df = _at(n=5)
        dup = pd.concat([df, df.iloc[[2]]])
        out, _ = validate_history(dup, Timeframe.M5, now=NOW)
        assert len(out) == 5
        assert out.index.is_unique

    def test_out_of_order_bars_come_back_sorted(self):
        df = _at(n=6).iloc[::-1]
        out, _ = validate_history(df, Timeframe.M5, now=NOW)
        assert out.index.is_monotonic_increasing


class TestDisagreement:
    def test_identical_series_agree(self):
        a = _at(n=10)
        assert disagreement(a, a.copy()) == pytest.approx(0.0, abs=1e-12)

    def test_a_shifted_series_is_reported(self):
        a = _at(n=10)
        b = a.copy()
        b["close"] = b["close"] * 1.02
        assert disagreement(a, b) == pytest.approx(0.02, rel=0.05)

    def test_insufficient_overlap_returns_none(self):
        a = _at("2026-07-20 13:30", 10)
        b = _at("2026-08-20 13:30", 10)
        assert disagreement(a, b) is None

    def test_empty_frames_return_none(self):
        assert disagreement(pd.DataFrame(), _at()) is None


def test_report_serializes_for_the_diagnostics_endpoint():
    _, report = validate_history(_at(), Timeframe.M5, now=NOW)
    payload = report.as_dict()
    assert payload["usable"] is True
    assert payload["score"] == 100.0
    assert set(payload) >= {"bars", "score", "usable", "issues", "counts",
                            "first", "last", "interval_ok"}


def test_calendar_tolerance_is_symmetric():
    """The calendar rule accepts spacings from half to double the nominal
    interval; anything outside that is a different interval, not a holiday."""
    assert 0 < CALENDAR_TOLERANCE < 1
    report = HistoryReport()
    assert report.usable and report.score == 100.0
