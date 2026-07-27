"""ProviderCapabilities: the declarative depth/interval contract.

These are cheap tests guarding an expensive class of bug. Every "chart history
stops at an arbitrary date" and "scrolling back retries forever" incident in
this project traces to a wrong answer to one of the three questions below, so
they are asserted directly rather than left implicit in provider code.
"""

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, STOOQ_CAPABILITIES, YAHOO_CAPABILITIES,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class TestCoverage:
    def test_yahoo_declares_every_app_timeframe(self):
        """A timeframe the UI offers but no provider declares would 404 at
        runtime. The primary provider must cover all of them."""
        assert set(YAHOO_CAPABILITIES.intervals) == set(Timeframe)

    def test_stooq_declares_only_what_it_can_actually_serve(self):
        """Stooq's intraday feed is not reliably available without an account.
        Declaring it would make the registry route intraday requests to a
        provider that cannot answer them."""
        assert set(STOOQ_CAPABILITIES.intervals) == {
            Timeframe.D1, Timeframe.W1, Timeframe.MN1}
        assert not STOOQ_CAPABILITIES.supports_interval(Timeframe.M5)

    def test_resampled_intervals_name_a_finer_native_source(self):
        """Yahoo has no 3m/10m/2h/4h; each must map to a finer native interval
        plus a resample rule, or the adapter would request an interval that
        does not exist."""
        for tf in (Timeframe.M3, Timeframe.M10, Timeframe.H2, Timeframe.H4):
            spec = YAHOO_CAPABILITIES.spec(tf)
            assert spec.resample, f"{tf} must declare a resample rule"
            assert spec.native in ("1m", "5m", "1h")

    def test_measured_depth_limits_match_what_yahoo_enforces(self):
        """The values probed live against Yahoo (see docs/MARKET_DATA.md §3).
        Hard-coding them here means a careless edit fails a test rather than
        silently reintroducing empty charts."""
        days = YAHOO_CAPABILITIES.max_lookback_days
        assert days(Timeframe.M1) == 7            # Yahoo allows 8; we sit inside
        assert days(Timeframe.M5) == 59           # 60 is rejected
        assert days(Timeframe.M30) == 59
        assert days(Timeframe.H1) == 729          # 730 is rejected
        assert days(Timeframe.D1) is None         # unlimited


class TestEarliest:
    def test_depth_is_measured_from_now(self):
        """THE root cause. Yahoo's limit is 'within the last N days' — from
        now, not from the request's end."""
        earliest = YAHOO_CAPABILITIES.earliest(Timeframe.M5, NOW)
        assert earliest == NOW - timedelta(days=59)

    def test_unlimited_intervals_report_no_floor(self):
        assert YAHOO_CAPABILITIES.earliest(Timeframe.D1, NOW) is None


class TestWindowFor:
    def test_window_wholly_inside_the_depth_is_untouched(self):
        start, end = NOW - timedelta(days=3), NOW
        assert YAHOO_CAPABILITIES.window_for(
            Timeframe.M5, start, end, NOW) == (start, end)

    def test_start_is_clamped_up_to_the_floor(self):
        """The logged production failure: start 62 days back, end 31 days back.
        The whole window is not unservable — only its older part is — so the
        servable part must still be fetched."""
        start = NOW - timedelta(days=62)
        end = NOW - timedelta(days=31)
        clamped = YAHOO_CAPABILITIES.window_for(Timeframe.M5, start, end, NOW)
        assert clamped == (NOW - timedelta(days=59), end)

    def test_window_entirely_older_than_the_floor_returns_none(self):
        """None means 'do not spend a request on this' — the signal that lets
        the chart say 'start of available history' instead of retrying."""
        start = NOW - timedelta(days=200)
        end = NOW - timedelta(days=120)
        assert YAHOO_CAPABILITIES.window_for(
            Timeframe.M5, start, end, NOW) is None

    def test_unlimited_interval_never_clamps(self):
        start = NOW - timedelta(days=9000)
        assert YAHOO_CAPABILITIES.window_for(
            Timeframe.D1, start, NOW, NOW) == (start, NOW)

    def test_inverted_or_empty_window_returns_none(self):
        assert YAHOO_CAPABILITIES.window_for(
            Timeframe.D1, NOW, NOW, NOW) is None


class TestSessionAndSymbolSupport:
    def test_extended_hours_is_forced_off_for_daily_and_coarser(self):
        """Daily+ bars are RTH aggregates upstream everywhere, so the flag is
        meaningless there no matter what a provider advertises."""
        assert YAHOO_CAPABILITIES.supports_extended_hours(Timeframe.M5)
        assert not YAHOO_CAPABILITIES.supports_extended_hours(Timeframe.D1)
        assert not YAHOO_CAPABILITIES.supports_extended_hours(Timeframe.W1)

    def test_provider_without_extended_hours_never_claims_it(self):
        assert not STOOQ_CAPABILITIES.supports_extended_hours(Timeframe.D1)

    def test_unsupported_symbols_are_declared_not_discovered(self):
        caps = ProviderCapabilities(
            intervals={Timeframe.D1: IntervalSpec("1d")},
            unsupported_symbols=frozenset({"^VIX"}))
        assert caps.supports_symbol("SPY")
        assert not caps.supports_symbol("^vix")     # case-insensitive


def test_capabilities_are_immutable():
    """Capabilities are shared between adapter instances; a mutable one would
    let a single provider's quirk leak into every other."""
    with pytest.raises(Exception):
        YAHOO_CAPABILITIES.extended_hours = False
