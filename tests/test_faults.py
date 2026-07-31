"""QA-mode fault injection — and the proof that it is inert without QA mode.

Two separate things are being asserted here, and the second matters as much as
the first:

  TestInertByDefault   a normal install cannot reach any of this
  TestEachFault        each fault produces the REAL failure, not a lookalike
  TestThroughTheStack  an injected failure is handled by the same machinery a
                       genuine one would be — breaker, ranking, failover

`TestThroughTheStack` is the point of the whole module. Every failure mode this
subsystem claims to survive is documented and unit-tested against canned
payloads; none of it had ever been *watched* happening. If an injected outage
did not trip a breaker and fail over exactly like a real one, the fault would
be a simulation of the tests rather than of the system.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderAuthError, ProviderQuotaExceeded,
    ProviderRateLimited, ProviderTimeout, ProviderUnavailable,
)
from optionspilot.data.faults import (
    ALL_FAULTS, FAULT_AUTH, FAULT_EMPTY, FAULT_LATENCY, FAULT_OUTAGE,
    FAULT_QUOTA, FAULT_RATE_LIMIT, FAULT_TIMEOUT, FAULT_UNUSABLE, FAULTS,
    FaultInjector,
)
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame


@pytest.fixture(autouse=True)
def clean_faults():
    """No test may leak an armed fault into another. A stray global fault would
    make an unrelated test fail somewhere it has no business failing."""
    FAULTS.clear_all()
    yield
    FAULTS.clear_all()


def _request(tf: Timeframe = Timeframe.M5) -> HistoryRequest:
    now = datetime.now(timezone.utc)
    return HistoryRequest("SPY", tf, now - timedelta(days=3), now)


class TestInertByDefault:
    def test_the_global_injector_starts_empty_and_inactive(self):
        assert FAULTS.armed() == {}
        assert FAULTS.active is False

    def test_a_fetch_with_nothing_armed_is_untouched(self):
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        assert len(adapter.fetch_history(_request())) == 20
        assert len(adapter.calls) == 1

    def test_clearing_the_last_fault_deactivates_the_hot_path_check(self):
        """`active` is what makes the check free in a normal install; leaving it
        True after the last fault is cleared would cost a lock acquisition on
        every request forever."""
        FAULTS.arm("alpha", FAULT_OUTAGE)
        assert FAULTS.active is True
        FAULTS.clear("alpha")
        assert FAULTS.active is False

    def test_an_unknown_fault_is_refused_at_arming_time(self):
        with pytest.raises(ValueError, match="unknown fault"):
            FAULTS.arm("alpha", "meltdown")

    def test_every_declared_fault_has_a_plain_english_description(self):
        """The QA panel picks a fault by what it PROVES, not by its slug."""
        from optionspilot.data.faults import FAULT_TEXT
        for kind in ALL_FAULTS:
            assert FAULT_TEXT.get(kind), f"{kind} has no description"


class TestEachFault:
    """Each fault must raise the genuine `ProviderError` subclass.

    The classes are what the retry-vs-failover policy is written against
    (`health.COUNTS_AGAINST_HEALTH`, `adapter.failure_kind`), so a bespoke
    exception would exercise a path that does not exist in production.
    """

    @pytest.mark.parametrize("kind,error", [
        (FAULT_OUTAGE, ProviderUnavailable),
        (FAULT_TIMEOUT, ProviderTimeout),
        (FAULT_RATE_LIMIT, ProviderRateLimited),
        (FAULT_QUOTA, ProviderQuotaExceeded),
        (FAULT_AUTH, ProviderAuthError),
    ])
    def test_a_raising_fault_raises_the_real_class(self, kind, error):
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        FAULTS.arm("alpha", kind)
        with pytest.raises(error):
            adapter.fetch_history(_request())
        # The real transport was never reached — the fault stands in for it.
        assert not adapter.calls

    def test_an_empty_fault_is_an_empty_answer_not_a_failure(self):
        """A weekend is not an outage. This is the distinction the entire
        subsystem was rebuilt around, so it must be simulable."""
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        FAULTS.arm("alpha", FAULT_EMPTY)
        assert adapter.fetch_history(_request()).empty
        assert adapter.monitor.empties == 1
        assert adapter.monitor.failures == 0
        assert adapter.monitor.available() is True

    def test_an_unusable_fault_answers_with_bars_validation_will_refuse(self):
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        FAULTS.arm("alpha", FAULT_UNUSABLE)
        served = adapter.fetch_history(_request())
        assert not served.empty
        from optionspilot.data.quality import validate_history
        _, report = validate_history(served, Timeframe.M5)
        assert report.usable is False

    def test_a_latency_fault_produces_real_measured_latency(self):
        """The ranking must demote it because the latency is REAL, not because
        a number was written down somewhere.

        This asserted `avg_latency_ms >= 50.0` against a fault armed for
        exactly 0.05s — a tight enough margin that GitHub-hosted Windows
        runners (whose default timer resolution is ~15.6ms, and whose
        `time.sleep` can return a few ms short of the requested duration
        under scheduler load) measured 47.0ms and failed, even though the
        fault fired correctly. The behavior being proven is that the
        latency is real and scales with what was armed — not that it hits
        an exact millisecond floor — so this arms a longer, noise-dominant
        duration and checks it against a wide tolerance and against a
        same-process unfaulted baseline, rather than a threshold sized to
        beat clock jitter by only a few percent.
        """
        baseline = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        baseline.fetch_history(_request())

        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        seconds = 0.3
        FAULTS.arm("alpha", FAULT_LATENCY, seconds=seconds)
        adapter.fetch_history(_request())

        # Comfortably below `seconds`, so ordinary scheduler slop can never
        # trip it, but still an order of magnitude above the unfaulted
        # baseline — the latency is genuinely being measured, not faked.
        assert adapter.monitor.avg_latency_ms >= seconds * 1000 * 0.8
        assert adapter.monitor.avg_latency_ms > baseline.monitor.avg_latency_ms * 10
        assert adapter.monitor.successes == 1

    def test_a_counted_fault_expires_by_itself(self):
        """A finite count proves recovery happens on its own, rather than
        because somebody remembered to disarm it."""
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        FAULTS.arm("alpha", FAULT_OUTAGE, count=2)
        for _ in range(2):
            with pytest.raises(ProviderUnavailable):
                adapter.fetch_history(_request())
        assert len(adapter.fetch_history(_request())) == 20
        assert FAULTS.armed() == {}

    def test_a_fault_only_affects_the_provider_it_names(self):
        alpha = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        beta = ScriptedAdapter("beta", [frame(20, Timeframe.M5)])
        FAULTS.arm("alpha", FAULT_OUTAGE)
        with pytest.raises(ProviderUnavailable):
            alpha.fetch_history(_request())
        assert len(beta.fetch_history(_request())) == 20


class TestThroughTheStack:
    """An injected failure must be indistinguishable from a real one to every
    layer above the adapter."""

    def _service(self, config=None):
        adapters = [ScriptedAdapter("alpha", [frame(20, Timeframe.M5)], priority=10),
                    ScriptedAdapter("beta", [frame(20, Timeframe.M5)], priority=20)]
        return MarketDataService(ProviderRegistry(adapters, config=config),
                                 config=config)

    def test_an_outage_fails_over_to_the_next_provider(self):
        service = self._service()
        FAULTS.arm("alpha", FAULT_OUTAGE)
        now = datetime.now(timezone.utc)
        result = service.get_history("SPY", Timeframe.M5, now - timedelta(days=3), now)
        assert result.ok
        assert result.provider == "beta"

    def test_the_diagnostics_trace_records_the_injected_failure(self):
        """A maintainer watching the QA panel is watching the traces. If the
        fault did not appear in one, the drill would prove nothing."""
        service = self._service()
        FAULTS.arm("alpha", FAULT_OUTAGE)
        now = datetime.now(timezone.utc)
        service.get_history("SPY", Timeframe.M5, now - timedelta(days=3), now)
        trace = service.diagnostics.recent(1)[0]
        assert "alpha=" in trace["chain"]
        assert trace["provider"] == "beta"

    def test_one_outage_is_enough_for_dynamic_ranking_to_route_around_it(self):
        """The breaker is the SECOND line of defence, not the first.

        A single failed request already costs a provider enough rank
        (`CONSECUTIVE_FAILURE_WEIGHT` is 15, one and a half priority steps)
        that the next request goes to its neighbour instead. That is why the
        breaker does not trip in this scenario, and it is the correct outcome:
        the point of the ranking is that a sick provider stops being asked
        BEFORE it has failed enough times to be formally benched.
        """
        service = self._service()
        FAULTS.arm("alpha", FAULT_OUTAGE)
        now = datetime.now(timezone.utc)
        for _ in range(5):
            # The memo must be dropped between calls, or only the first request
            # reaches a provider at all — which would make this test pass or
            # fail for a reason that has nothing to do with the ranking.
            service.invalidate()
            assert service.get_history("SPY", Timeframe.M5,
                                       now - timedelta(days=3), now).provider == "beta"
        alpha = service.registry.get("alpha")
        assert alpha.monitor.failures == 2          # one request, one retry
        assert alpha.monitor.rank() > service.registry.get("beta").monitor.rank()
        assert alpha.monitor.health_state()[0] in ("offline", "degraded")

    def test_with_static_ordering_repeated_outages_trip_the_breaker(self):
        """Pin the order and the ranking cannot route around the failure — so
        the breaker is what eventually stops the doomed requests. This is the
        path `ordering_mode: static` users are on, and the reason the breaker
        still has to work when the ranking is switched off."""
        from optionspilot.data.config import ORDER_STATIC, MarketDataConfig
        service = self._service(MarketDataConfig(ordering_mode=ORDER_STATIC))
        FAULTS.arm("alpha", FAULT_OUTAGE)
        now = datetime.now(timezone.utc)
        for _ in range(4):
            service.invalidate()
            service.get_history("SPY", Timeframe.M5, now - timedelta(days=3), now)
        alpha = service.registry.get("alpha")
        assert alpha.monitor.breaker_trips >= 1
        assert alpha.monitor.available() is False
        assert alpha.monitor.health_state()[0] == "circuit_open"
        # And the chart keeps working throughout — that is the whole point.
        service.invalidate()
        assert service.get_history("SPY", Timeframe.M5,
                                   now - timedelta(days=3), now).provider == "beta"

    def test_an_auth_fault_benches_the_provider_stickily(self):
        """The remedy for a rejected key is a new key, so retrying it on every
        chart load only burns requests — and on some providers gets an IP
        blocked."""
        service = self._service()
        FAULTS.arm("alpha", FAULT_AUTH)
        now = datetime.now(timezone.utc)
        service.get_history("SPY", Timeframe.M5, now - timedelta(days=3), now)
        alpha = service.registry.get("alpha")
        assert alpha.monitor.auth_failed is True
        assert alpha.monitor.health_state()[0] == "unavailable"

    def test_a_provider_serving_unusable_bars_is_not_credited_with_a_success(self):
        """The V0.5.3 defect, made reproducible on demand: a source answering
        promptly with garbage used to keep the head of the chain forever."""
        service = self._service()
        FAULTS.arm("alpha", FAULT_UNUSABLE)
        now = datetime.now(timezone.utc)
        result = service.get_history("SPY", Timeframe.M5,
                                     now - timedelta(days=3), now)
        alpha = service.registry.get("alpha")
        assert alpha.monitor.successes == 0
        assert alpha.monitor.failures >= 1
        assert result.provider == "beta"

    def test_an_isolated_injector_does_not_touch_the_global_one(self):
        """`FaultInjector` is constructible in isolation; the process-wide
        `FAULTS` is what the shipped adapter consults. Arming one must not arm
        the other, or a test using a private injector would silently change the
        behaviour of every adapter in the suite."""
        private = FaultInjector()
        private.arm("alpha", FAULT_OUTAGE)
        assert private.armed()["alpha"]["kind"] == FAULT_OUTAGE
        assert FAULTS.armed() == {}
        assert FAULTS.active is False
        with pytest.raises(ProviderUnavailable):
            private.check("alpha")
        # And the shipped adapter, which reads the global, is unaffected.
        adapter = ScriptedAdapter("alpha", [frame(20, Timeframe.M5)])
        assert len(adapter.fetch_history(_request())) == 20
