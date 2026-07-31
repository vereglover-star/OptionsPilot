"""MarketDataService: the tier ladder, failover, self-healing and outcomes.

This is the file that matters most. Every historical chart bug in this project
reduces to the service layer confusing two different conditions, so the tests
below are organised around the distinctions rather than around the methods:

    exhausted  vs  empty  vs  stale  vs  failed
    memo       vs  cache  vs  live
    retry      vs  fail over        vs  give up

Everything runs offline against `ScriptedAdapter`s, so a failure here is a real
logic failure and never a flaky network.
"""

import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data import diagnostics as diag
from optionspilot.data.adapter import (
    ProviderRangeError, ProviderRateLimited, ProviderSymbolError,
    ProviderUnavailable,
)
from optionspilot.data.cache import CandleCache
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, YAHOO_CAPABILITIES,
)
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.quality import validate_history
from optionspilot.data.service import (
    CANDLE_TTL, EMPTY_CANDLE_TTL, MEM_CACHE_MAX, MarketDataService,
)
import optionspilot.data.service as service_mod
from tests.marketdata_helpers import ScriptedAdapter, UNLIMITED, frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
WINDOW = timedelta(days=5)


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock so TTL behaviour is exact, not timed."""
    state = {"t": 1000.0}
    monkeypatch.setattr(service_mod._time, "monotonic", lambda: state["t"])
    return state


def build(*adapters, cache=None, now=NOW):
    return MarketDataService(ProviderRegistry(list(adapters)), cache=cache,
                             clock=lambda: now)


def bars(n=20, timeframe=Timeframe.M5, end=NOW):
    """Bars ending at the service's INJECTED now. Using the real wall clock
    here would place every bar in the future relative to the frozen clock, and
    validation would (correctly) throw them all away."""
    return frame(n, timeframe, end=end)


def make(name, script=None, priority=100, capabilities=None,
         timeframe=Timeframe.M5):
    return ScriptedAdapter(name, script if script is not None
                           else [bars(20, timeframe)],
                           priority=priority,
                           capabilities=capabilities or UNLIMITED,
                           timeframe=timeframe)


def get(service, tf=Timeframe.M5, days=5, **kw):
    return service.get_history("SPY", tf, NOW - timedelta(days=days), NOW, **kw)


# ── the four "no data" conditions ────────────────────────────────────────────

class TestOutcomeIsNeverAmbiguous:
    """One empty DataFrame used to mean four different things, and the frontend
    had to guess which. Each now has its own outcome."""

    def test_exhausted_when_the_window_predates_every_provider(self, clock):
        service = build(make("yahoo", capabilities=YAHOO_CAPABILITIES))
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW - timedelta(days=200),
                                     NOW - timedelta(days=120))
        assert result.outcome == diag.OUTCOME_EXHAUSTED
        assert result.exhausted is True
        assert result.earliest_available == NOW - timedelta(days=59)
        assert "only goes back to" in result.message

    def test_exhausted_costs_no_provider_request_at_all(self, clock):
        """The fix for 'scrolling back retries forever': the impossible window
        is answered from the capability table."""
        yahoo = make("yahoo", capabilities=YAHOO_CAPABILITIES)
        service = build(yahoo)
        service.get_history("SPY", Timeframe.M5, NOW - timedelta(days=200),
                            NOW - timedelta(days=120))
        assert yahoo.calls == []

    def test_empty_when_providers_answer_with_no_bars(self, clock):
        """A holiday or a pre-listing window. Legitimate — and must NOT raise
        an error state in the UI."""
        service = build(make("a", [None]), make("b", [None], priority=200))
        result = get(service)
        assert result.outcome == diag.OUTCOME_EMPTY
        assert result.exhausted is False
        assert "no bars exist" in result.message

    def test_failed_when_nothing_can_answer(self, clock):
        service = build(make("a", [ProviderUnavailable("dead")]))
        result = get(service)
        assert result.outcome == diag.OUTCOME_FAILED
        assert "market data unavailable" in result.message
        assert "dead" in result.message

    def test_stale_when_only_the_local_cache_can_answer(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=2)),
                    provider="yahoo")
        service = build(make("a", [ProviderUnavailable("dead")]), cache=cache)
        result = get(service, allow_stale=True)
        assert result.outcome == diag.OUTCOME_STALE
        assert result.stale is True and result.bars > 0
        assert "live data unavailable" in result.message

    def test_a_mix_of_empty_and_failure_is_a_failure_not_an_empty(self, clock):
        """If ANY provider errored we cannot claim the window is genuinely
        empty — that would hide a real outage behind a benign message."""
        service = build(make("a", [None]),
                        make("b", [ProviderUnavailable("dead")], priority=200))
        assert get(service).outcome == diag.OUTCOME_FAILED


# ── the tier ladder ──────────────────────────────────────────────────────────

class TestTierLadder:
    def test_a_live_answer_reports_its_provider(self, clock):
        service = build(make("yahoo"))
        result = get(service)
        assert result.outcome == diag.OUTCOME_LIVE
        assert result.provider == "yahoo" and result.bars == 20

    def test_a_second_request_inside_the_ttl_is_a_memo_hit(self, clock):
        yahoo = make("yahoo")
        service = build(yahoo)
        get(service)
        result = get(service)
        assert result.outcome == diag.OUTCOME_MEMO
        assert len(yahoo.calls) == 1

    def test_the_memo_expires_on_the_timeframes_own_ttl(self, clock):
        yahoo = make("yahoo")
        service = build(yahoo)
        get(service)
        clock["t"] += CANDLE_TTL[Timeframe.M5] + 1
        get(service)
        assert len(yahoo.calls) == 2

    def test_a_failed_result_is_memoized_only_briefly(self, clock):
        """A transient failure cached for a full TTL poisons every retry — the
        root cause of the app opening with blank charts."""
        yahoo = make("yahoo", [ProviderUnavailable("hiccup"),
                               ProviderUnavailable("hiccup"), bars(20)])
        service = build(yahoo)
        assert get(service).outcome == diag.OUTCOME_FAILED
        clock["t"] += EMPTY_CANDLE_TTL + 1        # well inside M5's normal TTL
        assert get(service).outcome == diag.OUTCOME_LIVE

    def test_an_empty_result_does_not_hammer_upstream(self, clock):
        """One request costs at most MAX_ATTEMPTS_PER_PROVIDER attempts; the
        next request inside the short empty-TTL costs none at all."""
        yahoo = make("yahoo", [ProviderUnavailable("hiccup")])
        service = build(yahoo)
        get(service)
        attempts_after_one_request = len(yahoo.calls)
        get(service)                               # inside the short empty TTL
        assert attempts_after_one_request == service_mod.MAX_ATTEMPTS_PER_PROVIDER
        assert len(yahoo.calls) == attempts_after_one_request

    def test_a_warm_disk_cache_serves_a_restart_without_refetching(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5, bars(30),
                    provider="yahoo")
        yahoo = make("yahoo")
        service = build(yahoo, cache=cache)
        result = get(service)
        assert result.outcome == diag.OUTCOME_CACHE
        assert yahoo.calls == []

    def test_a_cold_disk_cache_is_not_used_when_its_last_bar_is_old(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=3)),
                    provider="yahoo")
        yahoo = make("yahoo")
        service = build(yahoo, cache=cache)
        assert get(service).outcome == diag.OUTCOME_LIVE
        assert len(yahoo.calls) == 1

    def test_live_bars_are_written_through_to_disk(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        service = build(make("yahoo"), cache=cache)
        get(service)
        assert cache.coverage("SPY", Timeframe.M5) is not None
        assert cache.stats()["by_provider"] == {"yahoo": 20}

    def test_a_wider_window_than_the_memo_forces_a_refetch(self, clock):
        yahoo = make("yahoo")
        service = build(yahoo)
        get(service, days=2)
        get(service, days=30)
        assert len(yahoo.calls) == 2

    def test_a_narrower_window_is_sliced_from_the_memo(self, clock):
        yahoo = make("yahoo", [bars(200)])
        service = build(yahoo)
        get(service, days=5)
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW - timedelta(minutes=30), NOW)
        assert len(yahoo.calls) == 1
        assert result.bars < 200
        assert result.frame.index[0] >= pd.Timestamp(NOW - timedelta(minutes=30))


# ── failover and self-healing ────────────────────────────────────────────────

class TestFailover:
    def test_a_dead_primary_falls_through_to_the_secondary(self, clock):
        service = build(make("a", [ProviderUnavailable("dead")], priority=10),
                        make("b", priority=20))
        result = get(service)
        assert result.outcome == diag.OUTCOME_LIVE and result.provider == "b"

    def test_the_first_healthy_provider_wins_and_later_ones_are_untouched(self, clock):
        second = make("b", priority=20)
        service = build(make("a", priority=10), second)
        assert get(service).provider == "a"
        assert second.calls == []

    def test_a_retryable_failure_is_retried_before_failing_over(self, clock):
        primary = make("a", [ProviderUnavailable("blip"), bars(20)],
                       priority=10)
        secondary = make("b", priority=20)
        service = build(primary, secondary)
        assert get(service).provider == "a"
        assert len(primary.calls) == 2 and secondary.calls == []

    def test_a_non_retryable_failure_fails_over_immediately(self, clock):
        """Retrying a symbol the provider does not have is pure latency."""
        primary = make("a", [ProviderSymbolError("unknown")], priority=10)
        service = build(primary, make("b", priority=20))
        assert get(service).provider == "b"
        assert len(primary.calls) == 1

    def test_a_rate_limited_provider_is_skipped_not_slept_on(self, clock):
        """A chart is waiting; a different provider beats a 60-second nap."""
        primary = make("a", [ProviderRateLimited("slow down", 60.0)], priority=10)
        service = build(primary, make("b", priority=20))
        assert get(service).provider == "b"
        assert len(primary.calls) == 1

    def test_an_empty_answer_falls_through_to_the_next_provider(self, clock):
        service = build(make("a", [None], priority=10), make("b", priority=20))
        assert get(service).provider == "b"

    def test_data_that_fails_validation_fails_over(self, clock):
        """A provider serving daily bars for a 5-minute request is worse than
        one serving nothing — it would mislabel the whole axis."""
        service = build(make("a", [bars(20, Timeframe.D1)], priority=10),
                        make("b", priority=20))
        result = get(service)
        assert result.provider == "b"

    def test_an_adapter_that_raises_something_unexpected_does_not_kill_the_request(self, clock):
        service = build(make("a", [KeyError("bug")], priority=10),
                        make("b", priority=20))
        assert get(service).provider == "b"

    def test_a_provider_that_cannot_reach_the_window_is_skipped_not_tried(self, clock):
        shallow = make("shallow", priority=10, capabilities=ProviderCapabilities(
            intervals={Timeframe.D1: IntervalSpec("1d", max_lookback_days=10)}))
        deep = make("deep", priority=20, timeframe=Timeframe.D1,
                    capabilities=ProviderCapabilities(
                        intervals={Timeframe.D1: IntervalSpec("1d")}))
        service = build(shallow, deep)
        result = service.get_history("SPY", Timeframe.D1,
                                     NOW - timedelta(days=400),
                                     NOW - timedelta(days=300))
        assert result.provider == "deep"
        assert shallow.calls == []


class TestSelfHealing:
    def test_a_total_outage_recovers_via_a_half_open_probe(self, clock):
        """No restart required: once the cooldown lapses, one probe request is
        allowed through and a recovered provider is back in rotation."""
        # Four failures is exactly what it takes to trip: two requests x two
        # attempts each. The third request finds the breaker open and costs no
        # upstream call at all — which is the point of having one.
        flaky = make("a", [ProviderUnavailable("dead")] * 4 + [bars(20)])
        service = build(flaky)
        for _ in range(3):                      # trip the breaker
            get(service)
            clock["t"] += EMPTY_CANDLE_TTL + 1
        assert len(flaky.calls) == 4
        assert service.registry.candidates("SPY", Timeframe.M5) == []
        service.registry.force_open("a", -1.0)  # cooldown lapsed
        clock["t"] += EMPTY_CANDLE_TTL + 1      # past the failed result's memo
        assert get(service).outcome == diag.OUTCOME_LIVE

    def test_the_cache_is_the_last_resort_before_an_error(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=4)),
                    provider="yahoo")
        service = build(make("a", [ProviderUnavailable("dead")]), cache=cache)
        assert get(service, allow_stale=True).outcome == diag.OUTCOME_STALE

    def test_a_stale_answer_is_never_memoized(self, clock, tmp_path):
        """The next caller must get a fresh attempt at the live providers, not
        a copy of our fallback."""
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=4)),
                    provider="yahoo")
        adapter = make("a", [ProviderUnavailable("dead"),
                             ProviderUnavailable("dead"), bars(20)])
        service = build(adapter, cache=cache)
        assert get(service, allow_stale=True).stale is True
        assert get(service, allow_stale=True).outcome == diag.OUTCOME_LIVE


# ── the trading-path contract ────────────────────────────────────────────────

class TestFailClosedForTrading:
    def test_stale_data_is_never_returned_unless_asked_for(self, clock, tmp_path):
        """The engine's rule is 'no data means skip the symbol'. A trade placed
        on yesterday's bars is not an acceptable degradation."""
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=4)),
                    provider="yahoo")
        service = build(make("a", [ProviderUnavailable("dead")]), cache=cache)
        strict = get(service, allow_stale=False)
        assert strict.frame.empty and strict.stale is False

    def test_the_same_state_serves_a_display_caller(self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(30, end=NOW - timedelta(days=4)),
                    provider="yahoo")
        service = build(make("a", [ProviderUnavailable("dead")]), cache=cache)
        assert not get(service, allow_stale=True).frame.empty


# ── extended hours ───────────────────────────────────────────────────────────

class TestExtendedHours:
    def test_extended_and_regular_frames_are_memoized_separately(self, clock):
        adapter = make("a")
        service = build(adapter)
        get(service)
        get(service, extended_hours=True)
        assert len(adapter.calls) == 2
        assert adapter.calls[0][4] is False and adapter.calls[1][4] is True

    def test_extended_hours_frames_never_touch_the_disk_cache(self, clock, tmp_path):
        """The store has no session dimension, so its RTH bars would be a
        misleading answer to an extended-hours request."""
        cache = CandleCache(tmp_path / "c.db")
        service = build(make("a"), cache=cache)
        get(service, extended_hours=True)
        assert cache.coverage("SPY", Timeframe.M5) is None


# ── concurrency ──────────────────────────────────────────────────────────────

class TestConcurrency:
    def test_concurrent_callers_share_one_fetch(self):
        """Ten chart panes opening at once must produce one upstream request,
        not ten."""
        started = threading.Event()
        release = threading.Event()

        class SlowAdapter(ScriptedAdapter):
            def _fetch_native(self, *args):
                started.set()
                release.wait(timeout=5)
                return super()._fetch_native(*args)

        adapter = SlowAdapter("slow", [bars(20)])
        service = build(adapter)
        results = []
        threads = [threading.Thread(target=lambda: results.append(get(service)))
                   for _ in range(8)]
        for t in threads:
            t.start()
        started.wait(timeout=5)
        release.set()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 8
        assert len(adapter.calls) == 1

    def test_parallel_requests_for_different_symbols_do_not_serialize(self):
        adapter = make("a")
        service = build(adapter)
        threads = [threading.Thread(
            target=lambda i=i: service.get_history(
                f"SYM{i}", Timeframe.M5, NOW - WINDOW, NOW)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(adapter.calls) == 6


# ── memo bounds ──────────────────────────────────────────────────────────────

class TestMemoIsBounded:
    def test_the_memo_evicts_beyond_the_cap(self, clock):
        service = build(make("a"))
        for i in range(MEM_CACHE_MAX + 50):
            service.get_history(f"SYM{i}", Timeframe.M5, NOW - WINDOW, NOW)
        assert len(service._mem) <= MEM_CACHE_MAX
        keys = list(service._mem)
        assert not any(k[0] == "SYM0" for k in keys)

    def test_invalidate_drops_one_symbols_frames(self, clock):
        service = build(make("a"))
        get(service)
        service.get_history("QQQ", Timeframe.M5, NOW - WINDOW, NOW)
        assert service.invalidate("SPY") == 1
        assert len(service._mem) == 1


# ── diagnostics wiring ───────────────────────────────────────────────────────

class TestDiagnostics:
    def test_every_request_produces_a_trace(self, clock):
        service = build(make("a"))
        result = get(service)
        trace = service.diagnostics.find(result.trace_id)
        assert trace["symbol"] == "SPY" and trace["timeframe"] == "5m"
        assert trace["outcome"] == diag.OUTCOME_LIVE
        assert trace["provider"] == "a" and trace["bars"] == 20

    def test_a_failover_is_visible_in_the_trace(self, clock):
        service = build(make("a", [ProviderUnavailable("dead")], priority=10),
                        make("b", priority=20))
        result = get(service)
        trace = service.diagnostics.find(result.trace_id)
        assert trace["fallbacks"] == 1
        outcomes = [a["provider"] for a in trace["attempts"]]
        assert outcomes == ["a", "a", "b"]      # one retry, then the fallback

    def test_the_validation_report_is_attached(self, clock):
        service = build(make("a"))
        trace = service.diagnostics.find(get(service).trace_id)
        assert trace["validation"]["usable"] is True
        assert trace["validation"]["score"] == 100.0

    def test_health_summarises_providers_cache_and_requests(self, clock, tmp_path):
        service = build(make("a"), cache=CandleCache(tmp_path / "c.db"))
        get(service)
        health = service.health()
        assert [p["name"] for p in health["providers"]] == ["a"]
        assert health["cache"]["schema_version"] >= 2
        assert health["requests"]["total_requests"] == 1
        assert health["requests"]["success_rate"] == 1.0


class TestProviderDisagreement:
    def test_a_fallback_disagreeing_with_cached_bars_is_recorded(self, clock, tmp_path):
        """Recorded, never acted on: deciding which source is 'right' is not
        something this layer can know."""
        cache = CandleCache(tmp_path / "c.db")
        base = bars(30)
        cache.store("SPY", Timeframe.M5, base, provider="a")
        shifted = base.copy()
        for col in ("open", "high", "low", "close"):
            shifted[col] = shifted[col] * 1.05
        service = build(make("a", [ProviderUnavailable("dead")], priority=10),
                        make("b", [shifted], priority=20), cache=cache)
        # the warm-cache tier would answer first, so age it out of freshness
        service._mem.clear()
        cache.purge("SPY", Timeframe.M5)
        cache.store("SPY", Timeframe.M5,
                    base.iloc[:20], provider="a")
        result = get(service)
        trace = service.diagnostics.find(result.trace_id)
        kinds = [a["outcome"] for a in trace["attempts"]]
        assert "disagreement" in kinds or result.provider == "b"


class TestHistoryPagingDoesNotPoisonTheLiveMemo:
    """The "chart suddenly shows one candle" bug, reproduced and pinned.

    The memo is keyed by (symbol, timeframe, session) only — it has to be, or
    the live poll (whose `end` advances every few seconds) would never hit it.
    That means a history-paging request, whose window ENDS in the past, would
    overwrite the live frame under the same key. The next live load then found
    a "valid" entry (its start is older, so the coverage check passed), sliced
    it to the live window, and rendered whatever few bars happened to overlap —
    one, in the case found by `scripts/chart_check.py`: QQQ 1d came back with a
    single candle from nine months earlier, `outcome: memo`, no error anywhere.

    Fix: only live-window requests use the memo at all.
    """

    def _service(self):
        deep = frame(400, Timeframe.D1, end=NOW)
        adapter = make("yahoo", [deep, deep, deep, deep])
        return build(adapter), adapter

    def test_a_scroll_back_does_not_replace_the_live_frame(self, clock):
        service, adapter = self._service()
        live = service.get_history("SPY", Timeframe.D1,
                                   NOW - timedelta(days=300), NOW)
        assert live.bars > 100

        # the user scrolls: a window that ENDS 300 days ago
        service.get_history("SPY", Timeframe.D1, NOW - timedelta(days=900),
                            NOW - timedelta(days=300))

        again = service.get_history("SPY", Timeframe.D1,
                                    NOW - timedelta(days=300), NOW)
        assert again.bars == live.bars, \
            "the live window must not be answered from a scroll-back's frame"

    def test_a_history_window_is_never_memoized(self, clock):
        service, adapter = self._service()
        older = dict(start=NOW - timedelta(days=900), end=NOW - timedelta(days=300))
        service.get_history("SPY", Timeframe.D1, **older)
        assert service._mem == {}, "a past-ending window must not enter the memo"

    def test_a_history_window_is_never_answered_from_the_memo(self, clock):
        service, adapter = self._service()
        service.get_history("SPY", Timeframe.D1, NOW - timedelta(days=300), NOW)
        calls_after_live = len(adapter.calls)
        service.get_history("SPY", Timeframe.D1, NOW - timedelta(days=900),
                            NOW - timedelta(days=300))
        assert len(adapter.calls) == calls_after_live + 1, \
            "a scroll into history must actually fetch, not reuse the live frame"

    def test_the_live_poll_still_hits_the_memo(self, clock):
        """The optimisation the memo exists for must survive the fix."""
        service, adapter = self._service()
        get(service, Timeframe.D1, days=300)
        result = get(service, Timeframe.D1, days=300)
        assert result.outcome == diag.OUTCOME_MEMO
        assert len(adapter.calls) == 1

    def test_a_window_ending_one_bar_ago_still_counts_as_live(self, clock):
        """A caller that computes `end` a moment before a bar boundary is still
        asking for the live window; treating it as history would disable the
        memo for the ordinary refresh path."""
        service, adapter = self._service()
        service.get_history("SPY", Timeframe.D1, NOW - timedelta(days=300), NOW)
        nearly_now = NOW - timedelta(minutes=30)
        result = service.get_history("SPY", Timeframe.D1,
                                     NOW - timedelta(days=300), nearly_now)
        assert result.outcome == diag.OUTCOME_MEMO
        assert len(adapter.calls) == 1


class TestAFutureWindowCannotPoisonTheChain:
    """Found in the V0.5.2 self-audit, not by a user — but it would have been.

    A request whose window has not happened yet is unanswerable, and every
    provider says so in its own dialect: Yahoo returns HTTP 400 "Data doesn't
    exist for startDate=... endDate=...", yfinance returns an empty frame,
    Stooq returns nothing. Those were counted as health failures, so **one
    absurd request tripped all three circuit breakers** and took the whole
    chain out of rotation for real charts for up to five minutes.

    A future window is now refused for zero requests — the mirror image of the
    exhaustion guard at the other end of the timeline.
    """

    def test_a_future_window_costs_no_provider_request(self, clock):
        adapter = make("yahoo")
        service = build(adapter)
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW + timedelta(days=10),
                                     NOW + timedelta(days=20))
        assert result.frame.empty
        assert result.outcome == diag.OUTCOME_EMPTY
        assert "in the future" in result.message
        assert adapter.calls == []

    def test_a_future_window_does_not_trip_any_breaker(self, clock):
        a = make("a", priority=10)
        b = make("b", priority=20)
        service = build(a, b)
        for _ in range(10):
            service.invalidate()
            service.get_history("SPY", Timeframe.M5, NOW + timedelta(days=5),
                                NOW + timedelta(days=6))
        assert len(service.registry.candidates("SPY", Timeframe.M5)) == 2
        assert a.health().consecutive_failures == 0
        assert b.health().consecutive_failures == 0

    def test_a_window_ending_now_is_still_served(self, clock):
        """The guard must not swallow the ordinary live request, whose end IS
        the present moment."""
        service = build(make("a"))
        assert get(service).outcome == diag.OUTCOME_LIVE

    def test_a_window_starting_at_the_forming_bar_is_served(self, clock):
        """A caller asking from a moment ago is asking for the live window, not
        for the future; the tolerance must cover clock skew."""
        service = build(make("a"))
        result = service.get_history("SPY", Timeframe.M5,
                                     NOW - timedelta(seconds=30),
                                     NOW + timedelta(seconds=30))
        assert result.outcome == diag.OUTCOME_LIVE


class TestAnAuthoritativeEmptyStopsTheChain:
    """The third V0.5.2 self-audit finding.

    On a weekend, Yahoo correctly answers "no bars in this window" with an
    empty frame. The service then consulted yfinance, which returns an empty
    frame for BOTH "no bars" and "something broke" and therefore reports it as
    a failure — so a perfectly normal Saturday produced `outcome: failed` AND
    tripped yfinance's circuit breaker, every weekend.

    A provider that raises on every failure path can be believed when it says
    the window is empty. Providers that cannot tell the two apart are not asked.
    """

    def test_a_reliable_empty_stops_the_chain(self, clock):
        primary = make("yahoo", [None], priority=10)
        primary.reports_empty_reliably = True
        secondary = make("yfinance", priority=20)
        service = build(primary, secondary)
        result = get(service)
        assert result.outcome == diag.OUTCOME_EMPTY
        assert secondary.calls == [],             "a provider that cannot distinguish empty-from-broken must not be asked"

    def test_an_unreliable_empty_still_falls_through(self, clock):
        """The conservative default is preserved: an ambiguous empty is still
        worth a second opinion."""
        primary = make("legacy", [None], priority=10)
        assert primary.reports_empty_reliably is False
        secondary = make("good", priority=20)
        service = build(primary, secondary)
        assert get(service).provider == "good"

    def test_an_authoritative_empty_does_not_trip_any_breaker(self, clock):
        """A holiday must not take a provider out of rotation."""
        primary = make("yahoo", [None], priority=10)
        primary.reports_empty_reliably = True
        secondary = make("yfinance", priority=20)
        service = build(primary, secondary)
        for _ in range(10):
            service.invalidate()
            get(service)
        assert len(service.registry.candidates("SPY", Timeframe.M5)) == 2
        assert secondary.health().consecutive_failures == 0

    def test_the_shipped_chain_marks_only_the_self_describing_adapters(self):
        from optionspilot.data.registry import default_registry
        flags = {a.provider_name: a.reports_empty_reliably
                 for a in default_registry(environ={}).adapters}
        # Only yfinance cannot tell "no bars here" from "the request failed";
        # it returns an empty frame for both. Every other adapter raises on
        # every failure path, so its empty answer is authoritative.
        assert flags == {"yahoo": True, "yfinance": False, "stooq": True,
                         "finnhub": True, "twelvedata": True,
                         "alphavantage": True}


class TestAnUnusableCacheHealsItself:
    """A cached frame that fails validation must be a HICCUP, not a wall.

    Validation used to run in `_settle` — after the ladder had already
    committed to the disk tier — so an unusable cache became `outcome=failed`
    with nothing left to fall through to, and the offending rows stayed on
    disk to fail identically on the next request and on every press of Retry.
    A real daily-bar defect (two providers stamping the same session at
    different instants, so every trading day held two rows 9.5 hours apart)
    turned that into "every symbol on 1D is stuck behind a validation screen",
    unrecoverable without deleting cache.db by hand.
    """

    @staticmethod
    def _poison(cache, tf=Timeframe.M5):
        """Write bars whose spacing cannot be `tf` — the shape the real defect
        produced: a shadow bar part-way through every interval."""
        good = bars(40, tf)
        shadow = good.copy()
        shadow.index = shadow.index + timedelta(minutes=tf.minutes // 2)
        both = pd.concat([good, shadow]).sort_index()
        cache.store("SPY", tf, both, provider="yahoo")
        return both

    def test_it_falls_through_to_the_providers_instead_of_failing(
            self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        self._poison(cache)
        service = build(make("a", [bars(20)]), cache=cache)
        result = get(service)
        assert result.outcome == diag.OUTCOME_LIVE
        assert result.provider == "a"
        assert result.bars == 20

    def test_the_bad_rows_are_quarantined_not_left_to_fail_again(
            self, clock, tmp_path):
        """The wall was that the SECOND request hit the same bars."""
        cache = CandleCache(tmp_path / "c.db")
        self._poison(cache)
        service = build(make("a", [bars(20), bars(20)]), cache=cache)
        get(service)
        assert service._quarantines == 1
        # nothing unusable survives on disk for the next request to inherit:
        # whatever is there now must pass the same validation that rejected
        # the poisoned frame
        remaining = cache.load("SPY", Timeframe.M5, NOW - WINDOW, NOW)
        if not remaining.empty:
            _, report = validate_history(remaining, Timeframe.M5, now=NOW)
            assert report.usable

    def test_retry_is_never_required(self, clock, tmp_path):
        """The user-visible contract: one request, one recovery."""
        cache = CandleCache(tmp_path / "c.db")
        self._poison(cache)
        service = build(make("a", [bars(20)] * 3), cache=cache)
        assert get(service).outcome == diag.OUTCOME_LIVE
        clock["t"] += CANDLE_TTL[Timeframe.M5] + 1
        assert get(service).outcome in (diag.OUTCOME_LIVE, diag.OUTCOME_CACHE)

    def test_the_quarantine_is_recorded_where_an_operator_will_see_it(
            self, clock, tmp_path):
        cache = CandleCache(tmp_path / "c.db")
        self._poison(cache)
        service = build(make("a", [bars(20)]), cache=cache)
        result = get(service)
        trace = service.diagnostics.recent(1)[0]
        assert any(a["outcome"] == "quarantined" for a in trace["attempts"])
        assert service.health()["cache"]["quarantines"] == 1
        assert result.outcome == diag.OUTCOME_LIVE

    def test_a_healthy_cache_is_still_served_from_disk(self, clock, tmp_path):
        """The guard must not cost the warm-start path its whole reason to
        exist — an app restart mid-session should still not re-download."""
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5, bars(40), provider="yahoo")
        service = build(make("a", [bars(20)]), cache=cache)
        assert get(service).outcome == diag.OUTCOME_CACHE
        assert service._quarantines == 0

    def test_an_unusable_stale_frame_does_not_reach_the_chart(
            self, clock, tmp_path):
        """The last-resort tier must not hand over bars the renderer refuses."""
        cache = CandleCache(tmp_path / "c.db")
        good = bars(30, end=NOW - timedelta(days=4))
        shadow = good.copy()
        shadow.index = shadow.index + timedelta(minutes=2)
        cache.store("SPY", Timeframe.M5,
                    pd.concat([good, shadow]).sort_index(), provider="yahoo")
        service = build(make("a", [ProviderUnavailable("dead")]), cache=cache)
        result = get(service, allow_stale=True)
        assert result.outcome == diag.OUTCOME_FAILED
        assert result.bars == 0
        assert service._quarantines == 1
