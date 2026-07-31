"""Dynamic provider ranking — the registry ordering by health, not by fiat.

Two properties matter more than any individual number here:

  1. **A cold system must reproduce the old static order exactly.** That is what
     makes shipping this safe; if it were not true, V0.5.3 would have silently
     changed which provider answers every first request.
  2. **A degraded provider must actually lose its place**, and get it back when
     it recovers — otherwise the feature is decoration.

`dynamic_ranking: false` is asserted to pin the static order, because that is
the escape hatch documented for anyone who needs the old behaviour back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from optionspilot.core.models import Timeframe
from optionspilot.data.config import MarketDataConfig
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def bars(n=20, timeframe=Timeframe.M5):
    return frame(n, timeframe, end=NOW)


def names(adapters):
    return [a.provider_name for a in adapters]


def chain():
    return (ScriptedAdapter("primary", [bars()], priority=10),
            ScriptedAdapter("secondary", [bars()], priority=20),
            ScriptedAdapter("tertiary", [bars()], priority=30))


class TestColdStart:
    def test_with_no_traffic_the_order_is_the_configured_priority(self):
        a, b, c = chain()
        registry = ProviderRegistry([c, a, b])       # registered out of order
        assert names(registry.candidates("SPY", Timeframe.M5)) == \
            ["primary", "secondary", "tertiary"]

    def test_the_shipped_chain_still_starts_yahoo_first(self):
        from optionspilot.data.registry import default_registry

        registry = default_registry()
        assert names(registry.candidates("SPY", Timeframe.M5))[0] == "yahoo"
        assert names(registry.candidates("SPY", Timeframe.D1)) == \
            ["yahoo", "yfinance", "stooq"]


class TestHealthReorders:
    def test_a_slow_primary_falls_behind_a_fast_tertiary(self):
        """Yahoo at 2.4s vs Stooq at 260ms — the scenario the feature exists
        for. The middle provider at 900ms lands between them, which is the
        correct answer and the reason this asserts relative order rather than
        just 'tertiary is first'."""
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(30):
            a.monitor.record_success(2400.0)         # 2.4s
            b.monitor.record_success(900.0)
            c.monitor.record_success(260.0)          # 0.26s
        order = names(registry.candidates("SPY", Timeframe.M5))
        assert order[-1] == "primary"
        assert order.index("tertiary") < order.index("primary")

    def test_a_modestly_slower_primary_keeps_its_place(self):
        """One priority step is worth a full second of latency — a provider
        that is merely a little slower must not be demoted, or the chain would
        thrash on noise."""
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(30):
            a.monitor.record_success(180.0)
            b.monitor.record_success(200.0)
            c.monitor.record_success(320.0)
        assert names(registry.candidates("SPY", Timeframe.M5)) == \
            ["primary", "secondary", "tertiary"]

    def test_a_failing_primary_is_demoted_before_its_breaker_trips(self):
        """Between the first failure and the third, the old registry kept
        asking the sick provider first every single time."""
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(30):
            b.monitor.record_success(50.0)
        a.monitor.record_failure("unavailable", "down")
        assert registry.candidates("SPY", Timeframe.M5)[0].provider_name != "primary"
        assert a.monitor.available() is True         # not yet out of rotation

    def test_recovery_is_gradual_and_completes(self):
        """A provider that has just come back should not instantly reclaim
        first place, but it must get there — the ranking's failure rate is
        measured over a moving window precisely so it decays. With a lifetime
        rate it never would: five failures during a two-minute outage would
        demote a provider for thousands of subsequent requests."""
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(5):
            a.monitor.record_failure("unavailable", "down")
            a.monitor.evaluate_breaker()
        a.monitor.record_success(50.0)
        # Back in rotation immediately (the breaker closed) but still distrusted.
        assert a.monitor.available() is True
        assert names(registry.candidates("SPY", Timeframe.M5))[0] != "primary"

        for _ in range(60):
            a.monitor.record_success(50.0)
        assert names(registry.candidates("SPY", Timeframe.M5))[0] == "primary"

    def test_a_provider_serving_poor_data_is_demoted(self):
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(20):
            a.monitor.observe_quality(30.0)
            b.monitor.observe_quality(100.0)
        assert names(registry.candidates("SPY", Timeframe.M5))[0] == "secondary"


class TestRankingCanBeDisabled:
    def test_static_ordering_is_restored_when_dynamic_ranking_is_off(self):
        a, b, c = chain()
        registry = ProviderRegistry(
            [a, b, c], config=MarketDataConfig(dynamic_ranking=False))
        for _ in range(30):
            a.monitor.record_success(9000.0)         # ruinously slow
            c.monitor.record_success(10.0)
        assert names(registry.candidates("SPY", Timeframe.M5)) == \
            ["primary", "secondary", "tertiary"]


class TestRankingReport:
    def test_ranking_lists_every_provider_with_its_position(self):
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        rows = registry.ranking()
        assert [r["name"] for r in rows] == ["primary", "secondary", "tertiary"]
        assert [r["position"] for r in rows] == [1, 2, 3]
        assert all("rank" in r and "available" in r for r in rows)

    def test_ranking_can_be_filtered_to_providers_serving_an_interval(self):
        from optionspilot.data.capabilities import STOOQ_CAPABILITIES

        daily_only = ScriptedAdapter("daily", [bars(20, Timeframe.D1)],
                                     priority=30,
                                     capabilities=STOOQ_CAPABILITIES)
        registry = ProviderRegistry([ScriptedAdapter("all", [bars()],
                                                     priority=10), daily_only])
        assert [r["name"] for r in registry.ranking("SPY", Timeframe.M5)] == ["all"]
        assert len(registry.ranking("SPY", Timeframe.D1)) == 2

    def test_healthiest_names_the_provider_that_would_answer(self):
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        for _ in range(30):
            a.monitor.record_success(5000.0)         # 5s: 50 rank points
            b.monitor.record_success(4000.0)         # 4s: 40 rank points
            c.monitor.record_success(20.0)
        assert registry.healthiest("SPY", Timeframe.M5).provider_name == "tertiary"

    def test_health_report_is_ordered_best_first_and_carries_capabilities(self):
        a, b, c = chain()
        registry = ProviderRegistry([a, b, c])
        report = registry.health_report()
        assert [r["name"] for r in report] == \
            ["primary", "secondary", "tertiary"]
        assert "intervals" in report[0] and "config" in report[0]


class TestRankingDrivesRealRequests:
    def test_the_service_asks_the_healthiest_provider_first(self):
        """The end-to-end property: ranking is not just a report, it changes
        which provider is actually contacted."""
        slow = ScriptedAdapter("slow", [bars()], priority=10)
        quick = ScriptedAdapter("quick", [bars()], priority=30)
        for _ in range(30):
            slow.monitor.record_success(4000.0)
            quick.monitor.record_success(50.0)
        service = MarketDataService(ProviderRegistry([slow, quick]),
                                    clock=lambda: NOW)
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW - timedelta(hours=2), NOW)
        assert result.provider == "quick"
        assert slow.calls == []

    def test_a_validation_reject_counts_against_the_provider_and_demotes_it(self):
        """Before V0.5.3 a provider answering promptly with unusable bars was
        recorded by the adapter as a SUCCESS and never counted as a failure
        anywhere, so a source serving consistently-broken data kept its place
        at the head of the chain indefinitely.

        Now one reject is enough to move it behind a healthy peer — and the
        request is still counted exactly once, not twice.
        """
        junk = frame(40, Timeframe.M5, end=NOW + timedelta(days=400))  # future
        bad = ScriptedAdapter("bad", [junk], priority=10)
        good = ScriptedAdapter("good", [bars()], priority=20)
        registry = ProviderRegistry([bad, good])
        service = MarketDataService(registry, clock=lambda: NOW)
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW - timedelta(hours=2), NOW)

        assert result.provider == "good"
        assert bad.monitor.failures == 1
        assert bad.monitor.successes == 0
        assert bad.monitor.requests == 1          # counted once, not twice
        assert bad.monitor.by_kind["validation"] == 1
        # And it is no longer the provider the next request would try first.
        assert names(registry.candidates("SPY", Timeframe.M5))[0] == "good"

    def test_a_persistently_invalid_provider_trips_its_breaker(self):
        junk = frame(40, Timeframe.M5, end=NOW + timedelta(days=400))
        bad = ScriptedAdapter("bad", [junk], priority=10)
        registry = ProviderRegistry([bad])
        service = MarketDataService(registry, clock=lambda: NOW)
        for _ in range(3):
            service.invalidate()
            service.get_history("SPY", Timeframe.M5,
                                NOW - timedelta(hours=2), NOW)
        assert bad.monitor.available() is False
        assert registry.candidates("SPY", Timeframe.M5) == []
