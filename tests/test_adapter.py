"""The HistoryAdapter base class — the contract every provider inherits.

The base class is where a provider gets its capability checks, window clamping,
resampling, normalization, throttling and health bookkeeping. Testing it once
here is what lets each concrete adapter be a thin transport + parser, and what
guarantees a future provider cannot accidentally skip any of it.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderRangeError, ProviderRateLimited,
    ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, YAHOO_CAPABILITIES,
)
from tests.marketdata_helpers import ScriptedAdapter, frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def req(tf=Timeframe.M5, days=5, ext=False, symbol="SPY"):
    return HistoryRequest(symbol, tf, NOW - timedelta(days=days), NOW, ext)


class TestCapabilityGate:
    def test_an_unsupported_interval_costs_no_request(self):
        adapter = ScriptedAdapter("x", capabilities=ProviderCapabilities(
            intervals={Timeframe.D1: IntervalSpec("1d")}))
        with pytest.raises(ProviderRangeError):
            adapter.fetch_history(req(Timeframe.M5), now=NOW)
        assert adapter.calls == []

    def test_an_unsupported_symbol_costs_no_request(self):
        adapter = ScriptedAdapter("x", capabilities=ProviderCapabilities(
            intervals={Timeframe.M5: IntervalSpec("5m")},
            unsupported_symbols=frozenset({"SPY"})))
        with pytest.raises(ProviderSymbolError):
            adapter.fetch_history(req(), now=NOW)
        assert adapter.calls == []

    def test_a_window_below_the_depth_floor_costs_no_request(self):
        """The single most important behaviour in this file: an impossible
        window must be answered from the capability table, not from upstream."""
        adapter = ScriptedAdapter("x", capabilities=YAHOO_CAPABILITIES)
        old = HistoryRequest("SPY", Timeframe.M5,
                             NOW - timedelta(days=200), NOW - timedelta(days=120))
        with pytest.raises(ProviderRangeError):
            adapter.fetch_history(old, now=NOW)
        assert adapter.calls == []

    def test_a_partially_servable_window_is_clamped_and_fetched(self):
        adapter = ScriptedAdapter("x", capabilities=YAHOO_CAPABILITIES)
        partial = HistoryRequest("SPY", Timeframe.M5,
                                 NOW - timedelta(days=90), NOW - timedelta(days=10))
        adapter.fetch_history(partial, now=NOW)
        _, _, start, end, _ = adapter.calls[0]
        assert start == NOW - timedelta(days=59)
        assert end == NOW - timedelta(days=10)


class TestNormalization:
    def test_output_is_the_canonical_shape(self):
        adapter = ScriptedAdapter("x")
        df = adapter.fetch_history(req(), now=NOW)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.index.tz is not None and df.index.name == "ts"
        assert df.index.is_monotonic_increasing

    def test_non_native_intervals_are_resampled_by_the_base_class(self):
        """A consumer must never learn which intervals a provider had natively.
        The adapter asks for 1h and the caller receives 4h bars."""
        hourly = frame(24, Timeframe.H1,
                       start=pd.Timestamp("2026-07-20 00:00", tz="UTC"))
        adapter = ScriptedAdapter("x", [hourly], capabilities=YAHOO_CAPABILITIES)
        out = adapter.fetch_history(req(Timeframe.H4), now=NOW)
        assert adapter.calls[0][1] == "1h"          # asked upstream for 1h
        assert len(out) == 6                        # 24 hourly -> 6 four-hour
        assert (out.index[1] - out.index[0]) == pd.Timedelta(hours=4)
        assert out["volume"].iloc[0] == pytest.approx(4000.0)   # summed

    def test_resampled_bars_keep_ohlc_semantics(self):
        hourly = frame(8, Timeframe.H1,
                       start=pd.Timestamp("2026-07-20 00:00", tz="UTC"))
        adapter = ScriptedAdapter("x", [hourly], capabilities=YAHOO_CAPABILITIES)
        out = adapter.fetch_history(req(Timeframe.H4), now=NOW)
        first = out.iloc[0]
        assert first["open"] == hourly["open"].iloc[0]
        assert first["close"] == hourly["close"].iloc[3]
        assert first["high"] == hourly["high"].iloc[:4].max()
        assert first["low"] == hourly["low"].iloc[:4].min()


class TestExtendedHours:
    def test_prepost_is_passed_through_for_intraday(self):
        adapter = ScriptedAdapter("x", capabilities=YAHOO_CAPABILITIES)
        adapter.fetch_history(req(Timeframe.M5, ext=True), now=NOW)
        assert adapter.calls[0][4] is True

    def test_prepost_is_forced_off_for_daily(self):
        """Daily bars are RTH aggregates upstream; asking for pre/post there
        would be a lie about what the bars contain."""
        adapter = ScriptedAdapter("x", capabilities=YAHOO_CAPABILITIES,
                                  script=[frame(10, Timeframe.D1)])
        adapter.fetch_history(req(Timeframe.D1, ext=True), now=NOW)
        assert adapter.calls[0][4] is False


class TestHealthBookkeeping:
    def test_a_success_records_latency_and_clears_failures(self):
        adapter = ScriptedAdapter("x")
        adapter.fetch_history(req(), now=NOW)
        health = adapter.health()
        assert health.available and health.consecutive_failures == 0
        assert health.total_requests == 1 and health.last_success is not None

    def test_failures_accumulate(self):
        adapter = ScriptedAdapter("x", [ProviderUnavailable("boom")])
        for _ in range(3):
            with pytest.raises(ProviderUnavailable):
                adapter.fetch_history(req(), now=NOW)
        health = adapter.health()
        assert health.consecutive_failures == 3
        assert health.total_failures == 3
        assert "boom" in health.last_error

    def test_range_and_symbol_errors_do_not_count_against_health(self):
        """They are correct answers to an impossible question. Counting them
        would trip the circuit breaker on a provider that is working fine."""
        adapter = ScriptedAdapter("x", capabilities=YAHOO_CAPABILITIES)
        old = HistoryRequest("SPY", Timeframe.M5,
                             NOW - timedelta(days=300), NOW - timedelta(days=200))
        for _ in range(5):
            with pytest.raises(ProviderRangeError):
                adapter.fetch_history(old, now=NOW)
        assert adapter.health().consecutive_failures == 0
        assert adapter.health().total_failures == 0

    def test_rate_limiting_sets_a_cooldown_window(self):
        adapter = ScriptedAdapter("x", [ProviderRateLimited("slow down", 45.0)])
        with pytest.raises(ProviderRateLimited):
            adapter.fetch_history(req(), now=NOW)
        state = adapter.rate_limit_state
        assert state["limited"] is True
        assert 0 < state["seconds_remaining"] <= 45.0

    def test_an_empty_answer_is_not_counted_as_success_or_failure(self):
        """Empty is ambiguous at this layer; only the service can interpret it,
        so the adapter records the request without claiming either."""
        adapter = ScriptedAdapter("x", [None])
        out = adapter.fetch_history(req(), now=NOW)
        assert out.empty
        health = adapter.health()
        assert health.total_requests == 1
        assert health.total_failures == 0 and health.last_success is None

    def test_quality_observations_feed_the_rolling_score(self):
        adapter = ScriptedAdapter("x")
        assert adapter.data_quality_score == 100.0
        for _ in range(20):
            adapter.observe_quality(50.0)
        assert 50.0 <= adapter.data_quality_score < 100.0

    def test_connect_reports_failure_without_raising(self):
        adapter = ScriptedAdapter("x", [ProviderUnavailable("down")])
        assert adapter.connect() is False
        assert adapter.health().last_error


class TestUnexpectedErrors:
    def test_a_non_provider_exception_is_wrapped(self):
        """An adapter bug must reach the service as a typed provider failure so
        the chain fails over, rather than escaping as a raw traceback."""
        adapter = ScriptedAdapter("x", [ValueError("bad parse")])
        with pytest.raises(ProviderUnavailable) as exc:
            adapter.fetch_history(req(), now=NOW)
        assert "bad parse" in str(exc.value)


class TestFetchLatest:
    def test_latest_returns_only_the_tail(self):
        adapter = ScriptedAdapter("x", [frame(50, Timeframe.M5)])
        out = adapter.fetch_latest("SPY", Timeframe.M5, bars=3, now=NOW)
        assert len(out) == 3

    def test_latest_reaches_back_past_a_weekend(self):
        """A two-bar window over a Sunday would be empty; the span is floored
        at three calendar days so the refresh path cannot return nothing."""
        adapter = ScriptedAdapter("x")
        adapter.fetch_latest("SPY", Timeframe.M1, bars=2, now=NOW)
        _, _, start, end, _ = adapter.calls[0]
        assert (end - start) >= timedelta(days=3)


def test_snapshot_is_optional():
    adapter = ScriptedAdapter("x")
    with pytest.raises(ProviderUnavailable):
        adapter.fetch_snapshot("SPY")
