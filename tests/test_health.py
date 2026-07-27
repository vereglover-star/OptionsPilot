"""ProviderHealthMonitor — counters, failure policy, breaker, and ranking.

The monitor is the single owner of a provider's operational state (V0.5.3
consolidated it out of `adapter.ProviderHealth` + `registry._Breaker`), so it
carries three separable responsibilities that are tested separately here:

  1. it counts, and counts each upstream request exactly once;
  2. it decides what a failure MEANS (a range error is not an outage);
  3. it turns that into an order the registry can sort by.

Every test drives an injected clock. Nothing here sleeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.data.health import (
    COUNTS_AGAINST_HEALTH, KIND_INTERNAL, KIND_RANGE, KIND_RATE_LIMITED,
    KIND_SYMBOL, KIND_TIMEOUT, KIND_UNAVAILABLE, KIND_VALIDATION,
    LATENCY_MS_PER_RANK_POINT, STATE_CLOSED, STATE_HALF_OPEN, STATE_OPEN,
    BreakerPolicy, ProviderHealth, ProviderHealthMonitor,
)


class FakeClock:
    """A monotonic clock the test advances explicitly."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(name: str = "p", priority: int = 10, clock=None, **kw
         ) -> ProviderHealthMonitor:
    return ProviderHealthMonitor(name, priority=priority,
                                 clock=clock or FakeClock(), **kw)


class TestCounting:
    def test_a_success_counts_one_request_and_one_success(self):
        m = make()
        m.record_success(120.0, bars=300)
        assert (m.requests, m.successes, m.failures) == (1, 1, 0)
        assert m.bars == 300
        assert m.consecutive_failures == 0

    def test_an_empty_answer_is_neither_a_success_nor_a_failure(self):
        """A weekend is not an outage — but it is not proof of health either,
        so it must not reset a failure streak on its own."""
        m = make()
        m.record_failure(KIND_UNAVAILABLE, "down")
        m.record_empty(10.0)
        assert m.empties == 1
        assert m.successes == 0
        assert m.failures == 1
        assert m.consecutive_failures == 1     # NOT cleared by the empty

    def test_requests_today_rolls_over_at_midnight(self):
        day = {"d": datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc)}
        m = ProviderHealthMonitor("p", clock=FakeClock(),
                                  wall_clock=lambda: day["d"])
        m.record_success(10.0)
        m.record_success(10.0)
        assert m.requests_today == 2
        day["d"] += timedelta(hours=2)          # next calendar day
        m.record_success(10.0)
        assert m.requests_today == 1
        assert m.requests == 3                  # the lifetime total is untouched

    def test_demoting_a_success_moves_it_rather_than_adding_a_request(self):
        """The adapter records success as soon as the transport parses; the
        service may then reject the bars. One upstream call must stay one
        request in every total, or the failure rate is halved."""
        m = make()
        m.record_success(50.0)
        m.demote_last_success(KIND_VALIDATION, "wrong interval")
        assert m.requests == 1                  # not 2
        assert m.successes == 0
        assert m.failures == 1
        assert m.consecutive_failures == 1

    def test_demotion_restores_the_streak_the_success_had_cleared(self):
        """Recording the success zeroes the failure streak. If demotion merely
        incremented from zero, a provider answering EVERY request with unusable
        bars would oscillate 0 -> 1 -> 0 -> 1 and never reach the breaker
        threshold — it would stay in rotation forever."""
        m = make()
        for expected in (1, 2, 3):
            m.record_success(10.0)
            m.demote_last_success(KIND_VALIDATION, "unusable")
            assert m.consecutive_failures == expected

    def test_a_genuine_success_after_a_demotion_still_clears_the_streak(self):
        m = make()
        m.record_success(10.0)
        m.demote_last_success(KIND_VALIDATION, "unusable")
        m.record_success(10.0)
        assert m.consecutive_failures == 0


class TestFailurePolicy:
    def test_range_and_symbol_errors_do_not_count_against_health(self):
        """A correct answer to an impossible question. Counting it would trip
        the breaker on a provider working perfectly — exactly what happened
        every time a user scrolled past Yahoo's 59-day intraday floor."""
        m = make()
        for _ in range(10):
            m.record_failure(KIND_RANGE, "too old")
            m.record_failure(KIND_SYMBOL, "unknown")
        assert m.failures == 0
        assert m.consecutive_failures == 0
        assert m.requests == 20                 # still counted as traffic
        assert m.by_kind[KIND_RANGE] == 10

    def test_operational_failures_all_count(self):
        for kind in (KIND_UNAVAILABLE, KIND_TIMEOUT, KIND_RATE_LIMITED,
                     KIND_VALIDATION, KIND_INTERNAL):
            m = make()
            m.record_failure(kind, "x")
            assert m.failures == 1, kind
            assert COUNTS_AGAINST_HEALTH[kind] is True

    def test_a_rate_limit_takes_the_provider_out_until_the_window_expires(self):
        clock = FakeClock()
        m = make(clock=clock)
        m.record_failure(KIND_RATE_LIMITED, "429", retry_after=60.0)
        assert m.available() is False
        clock.advance(59.0)
        assert m.available() is False
        clock.advance(2.0)
        assert m.available() is True

    def test_a_success_clears_the_rate_limit(self):
        m = make()
        m.record_failure(KIND_RATE_LIMITED, "429", retry_after=60.0)
        m.record_success(10.0)
        assert m.available() is True


class TestBreaker:
    def _sicken(self, m, times=3):
        for _ in range(times):
            m.record_failure(KIND_UNAVAILABLE, "down")
            m.evaluate_breaker()

    def test_recording_a_failure_alone_never_trips(self):
        """Counting and tripping are separate steps: the adapter counts, the
        service decides the attempt was the provider's fault. Tripping inside
        `record_failure` too would double every trip and double the cooldown."""
        m = make()
        for _ in range(10):
            m.record_failure(KIND_UNAVAILABLE, "down")
        assert m.breaker_trips == 0
        assert m.available() is True

    def test_the_breaker_opens_at_the_threshold(self):
        m = make(breaker=BreakerPolicy(threshold=3))
        self._sicken(m, 2)
        assert m.available() is True
        self._sicken(m, 1)
        assert m.available() is False
        assert m.breaker_trips == 1

    def test_the_threshold_is_configurable_per_provider(self):
        m = make(breaker=BreakerPolicy(threshold=5))
        self._sicken(m, 4)
        assert m.available() is True
        self._sicken(m, 1)
        assert m.available() is False

    def test_the_cooldown_doubles_per_trip_and_is_capped(self):
        policy = BreakerPolicy(threshold=1, base_cooldown=10.0, max_cooldown=25.0)
        assert policy.cooldown_for(1) == 10.0
        assert policy.cooldown_for(2) == 20.0
        assert policy.cooldown_for(3) == 25.0      # capped
        assert policy.cooldown_for(9) == 25.0

    def test_a_success_closes_the_breaker(self):
        m = make()
        self._sicken(m)
        assert m.available() is False
        m.record_success(10.0)
        assert m.available() is True
        assert m.breaker_trips == 0

    def test_half_open_offers_exactly_one_probe(self):
        """Recovery must cost one request, not a restart — and a stampede of
        chart loads must not turn 'one probe' into fifty."""
        clock = FakeClock()
        m = make(clock=clock, breaker=BreakerPolicy(threshold=3,
                                                    base_cooldown=15.0))
        self._sicken(m)
        assert m.take_half_open_probe() is False   # still cooling down
        clock.advance(16.0)
        assert m.take_half_open_probe() is True
        assert m.take_half_open_probe() is False   # only one

    def test_a_closed_breaker_never_offers_a_probe(self):
        assert make().take_half_open_probe() is False

    def test_state_reports_half_open_once_the_cooldown_lapses(self):
        clock = FakeClock()
        m = make(clock=clock)
        self._sicken(m)
        assert m.snapshot()["state"] == STATE_OPEN
        clock.advance(999.0)
        assert m.snapshot()["state"] == STATE_HALF_OPEN
        m.record_success(5.0)
        assert m.snapshot()["state"] == STATE_CLOSED


class TestRanking:
    """Lower is better. The anchor is the configured priority; health moves it."""

    def test_a_cold_provider_ranks_exactly_at_its_priority(self):
        """This is what makes dynamic ranking safe to ship: with no traffic
        recorded, the chain starts in its documented static order."""
        assert make(priority=10).rank() == 10.0
        assert make(priority=30).rank() == 30.0

    def test_latency_costs_one_rank_point_per_100ms(self):
        m = make(priority=10)
        for _ in range(30):
            m.record_success(LATENCY_MS_PER_RANK_POINT * 5)   # 500ms
        assert m.rank() == 15.0

    def test_a_degraded_primary_loses_to_a_healthy_tertiary(self):
        """The scenario the feature exists for: Yahoo at 2.4s vs Stooq at
        260ms should reorder, while Yahoo at 180ms vs Stooq at 320ms should
        not."""
        def with_latency(name, priority, ms):
            m = make(name, priority)
            for _ in range(30):
                m.record_success(ms)
            return m

        fast_yahoo = with_latency("yahoo", 10, 180)
        slow_stooq = with_latency("stooq", 30, 320)
        assert fast_yahoo.rank() < slow_stooq.rank()

        slow_yahoo = with_latency("yahoo", 10, 2400)
        fast_stooq = with_latency("stooq", 30, 260)
        assert fast_stooq.rank() < slow_yahoo.rank()

    def test_the_latency_penalty_is_capped(self):
        """One 30-second timeout must not banish a provider for longer than
        its moving average takes to recover."""
        m = make(priority=10)
        for _ in range(50):
            m.record_success(600_000.0)
        assert m.rank() <= 10.0 + 50.0 + 0.001

    def test_consecutive_failures_dominate_a_priority_step(self):
        """A failing primary should lose its place immediately, not after
        three more chart loads."""
        good = make("good", 30)
        bad = make("bad", 10)
        for _ in range(3):
            bad.record_failure(KIND_UNAVAILABLE, "down")
        assert good.rank() < bad.rank()

    def test_the_failure_rate_decays_over_a_moving_window(self):
        """A lifetime rate never recovers: five failures in a two-minute outage
        would demote a provider for thousands of later requests. The ranking
        measures over a window so recovery is proportional to how long the
        provider has been healthy."""
        m = make(priority=10)
        for _ in range(5):
            m.record_failure(KIND_UNAVAILABLE, "down")
        sick = m.rank()
        for _ in range(60):
            m.record_success(10.0)
        assert m.rank() < sick
        assert m.rank() == pytest.approx(10.1, abs=0.2)
        # The LIFETIME totals are untouched — they are reported, not ranked on.
        assert m.failures == 5

    def test_poor_data_quality_costs_rank(self):
        clean, dirty = make("clean", 10), make("dirty", 10)
        for _ in range(20):
            clean.observe_quality(100.0)
            dirty.observe_quality(40.0)
        assert clean.rank() < dirty.rank()


class TestSnapshot:
    def test_a_snapshot_carries_everything_the_dashboard_shows(self):
        m = make()
        m.record_success(120.0, bars=10)
        m.record_failure(KIND_TIMEOUT, "slow")
        m.record_failure(KIND_VALIDATION, "bad bars")
        snap = m.snapshot()
        for key in ("name", "priority", "rank", "available", "state", "requests",
                    "successes", "failures", "empties", "success_rate",
                    "failure_rate", "avg_latency_ms", "p95_latency_ms",
                    "timeouts", "validation_failures", "rate_limits",
                    "breaker_trips", "circuit_open_for", "data_quality_score",
                    "last_success_at", "last_error", "requests_today"):
            assert key in snap, key
        assert snap["timeouts"] == 1
        assert snap["validation_failures"] == 1

    def test_a_snapshot_is_json_serializable(self):
        import json

        m = make()
        m.record_success(10.0)
        m.record_failure(KIND_UNAVAILABLE, "boom")
        json.dumps(m.snapshot())      # must not raise

    def test_percentiles_come_from_real_samples(self):
        m = make()
        for ms in range(1, 101):
            m.record_success(float(ms))
        assert 90 <= m.latency_percentile(95) <= 100

    def test_the_legacy_health_view_still_reports_the_old_fields(self):
        """`adapter.health()` has always returned this shape; several tests and
        scripts read it directly, so the consolidation must be invisible."""
        m = make()
        m.record_failure(KIND_UNAVAILABLE, "boom")
        view = ProviderHealth.of(m)
        assert view.name == "p"
        assert view.total_requests == 1
        assert view.total_failures == 1
        assert view.consecutive_failures == 1
        assert "boom" in view.last_error
        assert isinstance(view.as_dict(), dict)


class TestThreadSafety:
    def test_concurrent_recording_loses_no_counts(self):
        import threading

        m = ProviderHealthMonitor("p", clock=FakeClock())
        def work():
            for _ in range(200):
                m.record_success(1.0)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert m.requests == 1600
        assert m.successes == 1600


class TestTimeoutClassification:
    """A provider that is SLOW and one that is BROKEN want different responses,
    and the ranking reacts to the difference — so the `timeouts` counter has to
    reflect reality rather than always reading zero.

    `ProviderTimeout` subclasses `ProviderUnavailable`, so every existing
    handler and every retry/failover decision is unchanged; only the health
    classification differs.
    """

    def test_a_bare_socket_timeout_is_recognised(self):
        from optionspilot.data.adapter import is_timeout

        assert is_timeout(TimeoutError("timed out")) is True

    def test_a_timeout_wrapped_in_a_urlerror_is_recognised(self):
        """urllib wraps a CONNECT timeout in URLError but raises a read timeout
        bare, so the reason has to be unwrapped."""
        import urllib.error

        from optionspilot.data.adapter import is_timeout

        assert is_timeout(urllib.error.URLError(TimeoutError("timed out"))) is True

    def test_a_third_party_timeout_class_is_recognised_by_name(self):
        """yfinance and `requests` raise their own classes; matching by name
        keeps this module from importing either."""
        from optionspilot.data.adapter import is_timeout

        class ReadTimeout(Exception):
            pass

        assert is_timeout(ReadTimeout("too slow")) is True

    def test_an_ordinary_transport_error_is_not_a_timeout(self):
        import urllib.error

        from optionspilot.data.adapter import is_timeout

        assert is_timeout(ConnectionResetError("reset")) is False
        assert is_timeout(urllib.error.URLError("dns failure")) is False

    def test_a_cyclic_exception_chain_terminates(self):
        from optionspilot.data.adapter import is_timeout

        a, b = Exception("a"), Exception("b")
        a.__cause__ = b
        b.__cause__ = a
        assert is_timeout(a) is False        # must not hang

    def test_the_helper_picks_the_right_exception_type(self):
        from optionspilot.data.adapter import (
            ProviderTimeout, ProviderUnavailable, timeout_or_unavailable,
        )

        slow = timeout_or_unavailable("slow", TimeoutError())
        broken = timeout_or_unavailable("broken", ConnectionResetError())
        assert isinstance(slow, ProviderTimeout)
        assert type(broken) is ProviderUnavailable
        # Still a ProviderUnavailable, so nothing that catches that changes.
        assert isinstance(slow, ProviderUnavailable)

    def test_a_timeout_counts_against_health_and_shows_on_the_dashboard(self):
        from optionspilot.data.adapter import ProviderTimeout, failure_kind

        m = make()
        m.record_failure(failure_kind(ProviderTimeout("slow")), "slow")
        assert m.failures == 1
        assert m.snapshot()["timeouts"] == 1

    def test_a_real_adapter_reports_a_timeout_as_one(self):
        """End to end through the transport, since the classification is only
        useful if the adapters actually produce it."""
        from optionspilot.data.adapter import HistoryRequest, ProviderTimeout
        from optionspilot.core.models import Timeframe
        from optionspilot.data.yahoo_provider import YahooChartAdapter

        def timing_out(request, timeout=None):
            raise TimeoutError("timed out")

        adapter = YahooChartAdapter(opener=timing_out)
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(ProviderTimeout):
            adapter.fetch_history(
                HistoryRequest("SPY", Timeframe.M5,
                               now - timedelta(hours=2), now), now=now)
        assert adapter.monitor.snapshot()["timeouts"] == 1
