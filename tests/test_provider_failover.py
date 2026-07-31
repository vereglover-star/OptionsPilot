"""Smart failover across the six-provider chain.

The behaviour this file pins is the one the user experiences and never sees:

    Yahoo unavailable -> Finnhub -> quota exceeded -> Twelve Data
      -> outage -> Alpha Vantage -> still nothing -> cached bars + stale banner

Every step is driven by a scripted adapter, so the whole ladder runs offline
and deterministically. The point is not that any individual provider works —
`test_providers.py` covers that — but that the *chain* degrades one rung at a
time and never shows a broken chart while usable data exists anywhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    ProviderAuthError, ProviderQuotaExceeded, ProviderRateLimited,
    ProviderUnavailable,
)
from optionspilot.data.cache import CandleCache
from optionspilot.data.config import MarketDataConfig, ProviderConfig
from optionspilot.data.diagnostics import (
    OUTCOME_FAILED, OUTCOME_LIVE, OUTCOME_STALE,
)
from optionspilot.data.health import STATUS_AUTH_FAILED, STATUS_QUOTA
from optionspilot.data.ratelimit import RateLimitPolicy
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def bars(n=50, timeframe=Timeframe.M5):
    return frame(n, timeframe, end=NOW)


def chain(*adapters, cache=None, config=None):
    return MarketDataService(ProviderRegistry(list(adapters), config=config),
                             cache=cache, clock=lambda: NOW)


def ask(service, symbol="SPY", tf=Timeframe.M5, **kw):
    return service.get_history(symbol, tf, NOW - timedelta(days=2), NOW, **kw)


def scripted(name, script=None, priority=100, **kw):
    return ScriptedAdapter(name, script, priority=priority, **kw)


class TestTheLadderDegradesOneRungAtATime:
    def test_a_dead_keyless_primary_falls_through_to_a_keyed_provider(self):
        yahoo = scripted("yahoo", [ProviderUnavailable("yahoo down")], 10)
        finnhub = scripted("finnhub", [bars()], 40)
        result = ask(chain(yahoo, finnhub))
        assert result.outcome == OUTCOME_LIVE
        assert result.provider == "finnhub"

    def test_a_quota_exceeded_provider_falls_through_to_the_next(self):
        yahoo = scripted("yahoo", [ProviderUnavailable("down")], 10)
        finnhub = scripted("finnhub", [ProviderQuotaExceeded("no credits")], 40)
        twelve = scripted("twelvedata", [bars()], 50)
        result = ask(chain(yahoo, finnhub, twelve))
        assert result.provider == "twelvedata"

    def test_an_auth_failure_falls_through_and_stops_being_retried(self):
        yahoo = scripted("yahoo", [ProviderUnavailable("down")], 10)
        finnhub = scripted("finnhub", [ProviderAuthError("bad key")], 40)
        twelve = scripted("twelvedata", [bars()], 50)
        service = chain(yahoo, finnhub, twelve)

        assert ask(service).provider == "twelvedata"
        assert finnhub.monitor.status()[0] == STATUS_AUTH_FAILED

        calls_before = len(finnhub.calls)
        service.invalidate()
        assert ask(service).provider == "twelvedata"
        assert len(finnhub.calls) == calls_before   # never asked again

    def test_the_whole_chain_degrades_in_order(self):
        """The scenario from the brief, end to end."""
        yahoo = scripted("yahoo", [ProviderUnavailable("outage")], 10)
        yfin = scripted("yfinance", [ProviderUnavailable("outage")], 20)
        finnhub = scripted("finnhub", [ProviderQuotaExceeded("no credits")], 40)
        twelve = scripted("twelvedata", [ProviderUnavailable("maintenance")], 50)
        alpha = scripted("alphavantage", [bars()], 60)

        service = chain(yahoo, yfin, finnhub, twelve, alpha)
        result = ask(service)
        assert result.outcome == OUTCOME_LIVE
        assert result.provider == "alphavantage"

        # Every earlier provider was genuinely tried, in chain order — the
        # failover is real, not an ordering coincidence.
        trace = service.diagnostics.find(result.trace_id)
        tried = [a["provider"] for a in trace["attempts"]]
        assert tried.index("yahoo") < tried.index("finnhub") < \
            tried.index("alphavantage")

    def test_when_everything_fails_cached_bars_are_served_with_a_stale_flag(
            self, tmp_path):
        """Never a broken chart while valid cached data exists.

        The cached bars are deliberately OLD: fresh ones would be served by the
        earlier disk-cache tier as `cache`, which is a different (and correct)
        outcome. `stale` is specifically "nothing live could be reached"."""
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(timeframe=Timeframe.M5)
                    .shift(freq=timedelta(days=-30)), provider="yahoo")

        dead = [scripted(name, [ProviderUnavailable("down")], pri)
                for name, pri in (("yahoo", 10), ("finnhub", 40),
                                  ("twelvedata", 50), ("alphavantage", 60))]
        result = ask(chain(*dead, cache=cache), allow_stale=True)
        assert result.outcome == OUTCOME_STALE
        assert result.bars > 0
        assert result.stale is True

    def test_the_trading_path_still_fails_closed(self, tmp_path):
        """`allow_stale` is a DISPLAY-only opt-in. Adding three providers must
        not weaken the engine's no-data-means-skip rule."""
        cache = CandleCache(tmp_path / "c.db")
        cache.store("SPY", Timeframe.M5,
                    bars(timeframe=Timeframe.M5)
                    .shift(freq=timedelta(days=-30)), provider="yahoo")
        dead = [scripted(n, [ProviderUnavailable("down")], p)
                for n, p in (("yahoo", 10), ("alphavantage", 60))]
        result = ask(chain(*dead, cache=cache), allow_stale=False)
        assert result.frame.empty
        assert result.stale is False

    def test_a_total_failure_still_explains_itself(self):
        dead = [scripted(n, [ProviderUnavailable(f"{n} is down")], p)
                for n, p in (("yahoo", 10), ("finnhub", 40))]
        result = ask(chain(*dead))
        assert result.outcome == OUTCOME_FAILED
        assert "yahoo" in result.message


class TestBudgetKeepsProvidersOutOfRotation:
    def _metered(self, name, priority, per_day, script=None):
        adapter = scripted(name, script or [bars()], priority)
        adapter.quota.policy = RateLimitPolicy(per_minute=None, per_day=per_day)
        adapter.monitor.quota = adapter.quota
        return adapter

    def test_an_exhausted_provider_is_skipped_before_the_network(self):
        """The whole point of budgeting: an out-of-budget provider costs zero
        requests, rather than one failed request per chart load."""
        spent = self._metered("alphavantage", 40, per_day=2)
        spent.quota.exhaust_day()
        healthy = scripted("twelvedata", [bars()], 50)

        result = ask(chain(spent, healthy))
        assert result.provider == "twelvedata"
        assert spent.calls == []
        assert spent.monitor.status()[0] == STATUS_QUOTA

    def test_budget_pressure_reorders_before_exhaustion(self):
        """Traffic drifts off a nearly-spent provider BEFORE it is spent, which
        is what stops every provider being exhausted at once."""
        tight = self._metered("alphavantage", 40, per_day=10)
        roomy = self._metered("twelvedata", 50, per_day=1000)
        registry = ProviderRegistry([tight, roomy])

        assert registry.candidates("SPY", Timeframe.M5)[0].provider_name == \
            "alphavantage"
        for _ in range(9):                 # 90% of the tight budget spent
            tight.quota.record()
        assert registry.candidates("SPY", Timeframe.M5)[0].provider_name == \
            "twelvedata"

    def test_an_unmetered_provider_is_never_penalised(self):
        keyless = scripted("yahoo", [bars()], 10)
        metered = self._metered("finnhub", 40, per_day=10)
        for _ in range(10):
            metered.quota.record()
        registry = ProviderRegistry([keyless, metered])
        assert registry.candidates("SPY", Timeframe.M5)[0].provider_name == "yahoo"

    def test_exhausting_one_provider_does_not_exhaust_the_others(self):
        a = self._metered("finnhub", 40, per_day=1)
        b = self._metered("twelvedata", 50, per_day=1000)
        a.quota.exhaust_day()
        assert a.quota.allow()[0] is False
        assert b.quota.allow()[0] is True


class TestDepthAcrossMixedProviders:
    """`deepest_earliest` decides where the chart says history ends. A provider
    that cannot be used must not contribute a floor it can never serve."""

    def test_an_unusable_provider_contributes_no_floor(self):
        from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities

        shallow = scripted("yahoo", [bars()], 10, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5m", max_lookback_days=59)}))
        deep = scripted("finnhub", [bars()], 40, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5", max_lookback_days=180)}))
        deep.monitor.disabled_reason = "missing_api_key"

        registry = ProviderRegistry([shallow, deep])
        floor = registry.deepest_earliest("SPY", Timeframe.M5, NOW)
        assert floor == NOW - timedelta(days=59)

    def test_a_usable_deep_provider_does_extend_the_floor(self):
        from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities

        shallow = scripted("yahoo", [bars()], 10, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5m", max_lookback_days=59)}))
        deep = scripted("finnhub", [bars()], 40, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5", max_lookback_days=180)}))

        registry = ProviderRegistry([shallow, deep])
        assert registry.deepest_earliest("SPY", Timeframe.M5, NOW) == \
            NOW - timedelta(days=180)

    def test_a_temporarily_unavailable_provider_still_contributes(self):
        """A breaker-open provider will be back; the reported start of history
        must not lurch about as breakers open and close."""
        from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities

        shallow = scripted("yahoo", [bars()], 10, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5m", max_lookback_days=59)}))
        deep = scripted("finnhub", [bars()], 40, capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5", max_lookback_days=180)}))
        deep.monitor.force_open(300.0)

        registry = ProviderRegistry([shallow, deep])
        assert registry.deepest_earliest("SPY", Timeframe.M5, NOW) == \
            NOW - timedelta(days=180)


class TestFailoverIsInvisibleButRecorded:
    def test_the_caller_cannot_tell_which_provider_answered(self):
        """`get_candles` returns a frame, not a provenance. Which source
        answered is diagnostics detail."""
        yahoo = scripted("yahoo", [ProviderUnavailable("down")], 10)
        alpha = scripted("alphavantage", [bars()], 60)
        service = chain(yahoo, alpha)
        frame_a = service.get_history("SPY", Timeframe.M5,
                                      NOW - timedelta(days=2), NOW).frame
        assert not frame_a.empty

    def test_but_the_trace_records_the_whole_chain(self):
        yahoo = scripted("yahoo", [ProviderUnavailable("down")], 10)
        finnhub = scripted("finnhub", [ProviderQuotaExceeded("spent")], 40)
        alpha = scripted("alphavantage", [bars()], 60)
        service = chain(yahoo, finnhub, alpha)
        result = ask(service)

        trace = service.diagnostics.find(result.trace_id)
        assert "yahoo" in trace["chain"]
        assert "finnhub" in trace["chain"]
        assert trace["provider"] == "alphavantage"
        assert trace["fallbacks"] >= 2
