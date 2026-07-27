"""ProviderRegistry: ordering, eligibility, and circuit breaking.

(The broker registry's tests live in `test_registry.py`; this file is the
market-data provider registry.)

The registry's job is to spend as few requests as possible on providers that
cannot or will not answer. Two behaviours carry almost all of that value and
are asserted hardest here: filtering by capability BEFORE the network, and
taking a sick provider out of rotation so its timeout stops being added to
every chart load.
"""

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderRateLimited, ProviderUnavailable,
)
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, STOOQ_CAPABILITIES, YAHOO_CAPABILITIES,
)
from optionspilot.data.registry import (
    BREAKER_BASE_COOLDOWN, BREAKER_THRESHOLD, ProviderRegistry, default_registry,
)
from tests.marketdata_helpers import ScriptedAdapter, UNLIMITED

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def make(name, priority=100, capabilities=None, script=None):
    return ScriptedAdapter(name, script, priority=priority,
                           capabilities=capabilities or UNLIMITED)


def _req():
    return HistoryRequest("SPY", Timeframe.M5, NOW - timedelta(days=5), NOW)


class TestMembership:
    def test_adapters_are_ordered_by_priority_not_registration(self):
        registry = ProviderRegistry([make("c", 30), make("a", 10), make("b", 20)])
        assert [a.provider_name for a in registry.adapters] == ["a", "b", "c"]

    def test_a_duplicate_name_is_rejected(self):
        """Two providers under one name would make health, breaker state and
        diagnostics ambiguous."""
        registry = ProviderRegistry([make("a")])
        with pytest.raises(ValueError, match="already registered"):
            registry.register(make("a"))

    def test_unregister_removes_the_provider(self):
        registry = ProviderRegistry([make("a"), make("b")])
        registry.unregister("a")
        assert [x.provider_name for x in registry.adapters] == ["b"]
        assert registry.get("a") is None


class TestEligibility:
    def test_a_provider_without_the_interval_is_not_offered(self):
        registry = ProviderRegistry([make("yahoo", 10, YAHOO_CAPABILITIES),
                                     make("stooq", 30, STOOQ_CAPABILITIES)])
        names = [a.provider_name for a in registry.candidates("SPY", Timeframe.M5)]
        assert names == ["yahoo"]

    def test_a_provider_that_cannot_reach_the_window_is_not_offered(self):
        """The check that turns 'scroll into old intraday history' from three
        guaranteed-422 requests into zero."""
        registry = ProviderRegistry([make("yahoo", 10, YAHOO_CAPABILITIES)])
        old_end = NOW - timedelta(days=120)
        assert registry.candidates("SPY", Timeframe.M5,
                                   end=old_end, now=NOW) == []

    def test_a_partially_reachable_window_is_still_offered(self):
        registry = ProviderRegistry([make("yahoo", 10, YAHOO_CAPABILITIES)])
        end = NOW - timedelta(days=10)
        assert len(registry.candidates("SPY", Timeframe.M5, end=end, now=NOW)) == 1

    def test_an_unsupported_symbol_is_not_offered(self):
        caps = ProviderCapabilities(
            intervals={Timeframe.D1: IntervalSpec("1d")},
            unsupported_symbols=frozenset({"^VIX"}))
        registry = ProviderRegistry([make("a", 10, caps)])
        assert registry.candidates("^VIX", Timeframe.D1) == []

    def test_extended_hours_capable_providers_sort_first_but_others_remain(self):
        """An RTH-only provider is still a useful fallback for the same window;
        dropping it would turn a degraded chart into no chart."""
        rth_only = make("rth", 10, ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5m")}, extended_hours=False))
        ext = make("ext", 20, YAHOO_CAPABILITIES)
        registry = ProviderRegistry([rth_only, ext])
        names = [a.provider_name for a in registry.candidates(
            "SPY", Timeframe.M5, extended_hours=True)]
        assert names == ["ext", "rth"]


class TestDeepestEarliest:
    def test_the_deepest_provider_sets_the_floor(self):
        registry = ProviderRegistry([
            make("shallow", 10, ProviderCapabilities(
                intervals={Timeframe.D1: IntervalSpec("1d", max_lookback_days=30)})),
            make("deeper", 20, ProviderCapabilities(
                intervals={Timeframe.D1: IntervalSpec("1d", max_lookback_days=900)})),
        ])
        assert registry.deepest_earliest("SPY", Timeframe.D1, NOW) == \
            NOW - timedelta(days=900)

    def test_one_unlimited_provider_means_no_floor_at_all(self):
        registry = ProviderRegistry([
            make("shallow", 10, ProviderCapabilities(
                intervals={Timeframe.D1: IntervalSpec("1d", max_lookback_days=30)})),
            make("unlimited", 20, ProviderCapabilities(
                intervals={Timeframe.D1: IntervalSpec("1d")})),
        ])
        assert registry.deepest_earliest("SPY", Timeframe.D1, NOW) is None

    def test_no_provider_for_the_interval_reports_no_floor(self):
        registry = ProviderRegistry([make("stooq", 30, STOOQ_CAPABILITIES)])
        assert registry.deepest_earliest("SPY", Timeframe.M5, NOW) is None

    def test_the_shipped_chain_reports_yahoos_intraday_floor(self):
        registry = default_registry()
        assert registry.deepest_earliest("SPY", Timeframe.M5, NOW) == \
            NOW - timedelta(days=59)
        assert registry.deepest_earliest("SPY", Timeframe.D1, NOW) is None


class TestCircuitBreaker:
    def _sicken(self, registry, adapter, times=BREAKER_THRESHOLD):
        for _ in range(times):
            try:
                adapter.fetch_history(_req(), now=NOW)
            except ProviderUnavailable:
                registry.record_failure(adapter)

    def test_a_healthy_provider_stays_in_rotation(self):
        good = make("good", 10)
        registry = ProviderRegistry([good])
        registry.record_success(good)
        assert len(registry.candidates("SPY", Timeframe.M5)) == 1

    def test_repeated_failures_take_a_provider_out_of_rotation(self):
        """Without this, one dead provider adds its full timeout to every
        chart load, forever."""
        bad = make("bad", 10, script=[ProviderUnavailable("dead")])
        registry = ProviderRegistry([bad])
        self._sicken(registry, bad)
        assert registry.candidates("SPY", Timeframe.M5) == []

    def test_the_breaker_opens_only_at_the_threshold(self):
        bad = make("bad", 10, script=[ProviderUnavailable("dead")])
        registry = ProviderRegistry([bad])
        self._sicken(registry, bad, times=BREAKER_THRESHOLD - 1)
        assert len(registry.candidates("SPY", Timeframe.M5)) == 1

    def test_a_success_closes_the_breaker(self):
        bad = make("bad", 10, script=[ProviderUnavailable("dead")])
        registry = ProviderRegistry([bad])
        self._sicken(registry, bad)
        registry.record_success(bad)
        assert len(registry.candidates("SPY", Timeframe.M5)) == 1

    def test_cooldown_grows_with_repeated_trips(self):
        bad = make("bad", 10, script=[ProviderUnavailable("dead")])
        registry = ProviderRegistry([bad])
        self._sicken(registry, bad)
        first = registry.health_report()[0]["circuit_open_for"]
        registry.record_failure(bad)          # trips again while already open
        second = registry.health_report()[0]["circuit_open_for"]
        assert first is not None and first <= BREAKER_BASE_COOLDOWN
        assert second > first

    def test_half_open_offers_exactly_one_probe_after_the_cooldown(self):
        """Recovery must be automatic and cost one request — not a restart."""
        registry = ProviderRegistry([make("bad", 10,
                                          script=[ProviderUnavailable("dead")])])
        registry.force_open("bad", -1.0)      # a cooldown that already expired
        assert [p.provider_name for p in registry.half_open_candidates()] == ["bad"]
        assert registry.half_open_candidates() == []

    def test_a_rate_limited_provider_is_skipped_regardless_of_the_breaker(self):
        limited = make("limited", 10, script=[ProviderRateLimited("slow", 120.0)])
        registry = ProviderRegistry([limited])
        with pytest.raises(ProviderRateLimited):
            limited.fetch_history(_req(), now=NOW)
        assert registry.candidates("SPY", Timeframe.M5) == []

    def test_reset_clears_every_breaker(self):
        bad = make("bad", 10, script=[ProviderUnavailable("dead")])
        registry = ProviderRegistry([bad])
        self._sicken(registry, bad)
        registry.reset()
        assert len(registry.candidates("SPY", Timeframe.M5)) == 1


class TestHealthReport:
    def test_the_report_covers_every_provider_with_its_state(self):
        report = default_registry().health_report()
        assert [r["name"] for r in report] == ["yahoo", "yfinance", "stooq"]
        for row in report:
            assert set(row) >= {"name", "available", "priority", "rate_limit",
                                "intervals", "circuit_open_for",
                                "data_quality_score", "failure_rate"}

    def test_stooq_advertises_only_daily_and_coarser(self):
        report = {r["name"]: r for r in default_registry().health_report()}
        assert set(report["stooq"]["intervals"]) == {"1d", "1w", "1mo"}


def test_the_shipped_chain_is_yahoo_then_yfinance_then_stooq():
    """Order is a deliberate design decision (docs/MARKET_DATA.md §5), not an
    accident of registration: fastest-and-self-describing first, then an
    independent code path to the same data, then the only non-Yahoo source."""
    assert [a.provider_name for a in default_registry().adapters] == \
        ["yahoo", "yfinance", "stooq"]


def test_stooq_can_be_left_out():
    assert [a.provider_name for a in
            default_registry(include_stooq=False).adapters] == ["yahoo", "yfinance"]
