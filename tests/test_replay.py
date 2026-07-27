"""Market-data replay — re-running a recorded request and comparing providers.

Replay's value is in what it bypasses. `compare_providers` deliberately skips
the memo, the disk cache, the failover chain and (by default) the circuit
breaker, because the question it answers is "what does each source actually
say?" — and a ladder that stops at the first success answers a different
question. Most of these tests assert exactly that bypassing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderUnavailable,
)
from optionspilot.data.capabilities import STOOQ_CAPABILITIES
from optionspilot.data.registry import ProviderRegistry
from optionspilot.data.replay import (
    compare_providers, replay, request_from_trace,
)
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def bars(n=20, timeframe=Timeframe.M5, shift=0.0):
    f = frame(n, timeframe, end=NOW)
    if shift:
        for col in ("open", "high", "low", "close"):
            f[col] = f[col] * (1.0 + shift)
    return f


def request(tf=Timeframe.M5):
    return HistoryRequest("SPY", tf, NOW - timedelta(hours=2), NOW)


def service_with(*adapters):
    return MarketDataService(ProviderRegistry(list(adapters)), clock=lambda: NOW)


class TestRequestFromTrace:
    def test_it_rebuilds_the_recorded_window(self):
        svc = service_with(ScriptedAdapter("a", [bars()]))
        result = svc.get_history("SPY", Timeframe.M5, NOW - timedelta(hours=2), NOW)
        trace = svc.diagnostics.find(result.trace_id)
        rebuilt = request_from_trace(trace)
        assert rebuilt.symbol == "SPY"
        assert rebuilt.timeframe == Timeframe.M5
        assert rebuilt.end == NOW

    def test_a_trace_without_a_window_is_refused_not_invented(self):
        with pytest.raises(ValueError, match="did not record a window"):
            request_from_trace({"id": 7, "symbol": "SPY", "timeframe": "5m"})


class TestCompareProviders:
    def test_every_provider_is_asked_even_after_one_answers(self):
        """The ladder stops at the first success; a comparison must not."""
        a = ScriptedAdapter("a", [bars()], priority=10)
        b = ScriptedAdapter("b", [bars()], priority=20)
        result = compare_providers(ProviderRegistry([a, b]), request(), now=NOW)
        assert len(a.calls) == 1 and len(b.calls) == 1
        assert [x.provider for x in result.answers] == ["a", "b"]
        assert all(x.ok for x in result.answers)

    def test_a_failing_provider_is_reported_not_hidden(self):
        a = ScriptedAdapter("a", [ProviderUnavailable("boom")], priority=10)
        b = ScriptedAdapter("b", [bars()], priority=20)
        result = compare_providers(ProviderRegistry([a, b]), request(), now=NOW)
        failed = result.answers[0]
        assert failed.ok is False
        assert "boom" in failed.error

    def test_an_adapter_raising_an_unexpected_error_does_not_crash_the_replay(self):
        class Exploding(ScriptedAdapter):
            def _fetch_native(self, *a, **kw):
                raise RuntimeError("catastrophe")

        result = compare_providers(
            ProviderRegistry([Exploding("boom"), ScriptedAdapter("ok", [bars()])]),
            request(), now=NOW)
        assert "catastrophe" in result.answers[0].error
        assert result.answers[1].ok is True

    def test_a_provider_that_cannot_serve_the_interval_is_marked_skipped(self):
        daily = ScriptedAdapter("daily", [bars(20, Timeframe.D1)], priority=20,
                                capabilities=STOOQ_CAPABILITIES)
        result = compare_providers(
            ProviderRegistry([ScriptedAdapter("all", [bars()]), daily]),
            request(Timeframe.M5), now=NOW)
        skipped = [x for x in result.answers if x.skipped]
        assert len(skipped) == 1
        assert "5m" in skipped[0].skipped

    def test_an_out_of_rotation_provider_is_still_asked_by_default(self):
        """When debugging, 'this provider is currently benched' is a fact you
        want to see the answer behind, not a reason to skip it."""
        a = ScriptedAdapter("a", [bars()], priority=10)
        a.monitor.force_open(300.0)
        assert a.monitor.available() is False
        result = compare_providers(ProviderRegistry([a]), request(), now=NOW)
        assert result.answers[0].ok is True

    def test_the_breaker_can_be_honoured_when_asked(self):
        a = ScriptedAdapter("a", [bars()], priority=10)
        a.monitor.force_open(300.0)
        result = compare_providers(ProviderRegistry([a]), request(), now=NOW,
                                   include_open_breakers=False)
        assert result.answers[0].skipped == "circuit breaker open"

    def test_agreeing_providers_report_agreement(self):
        result = compare_providers(
            ProviderRegistry([ScriptedAdapter("a", [bars()], priority=10),
                              ScriptedAdapter("b", [bars()], priority=20)]),
            request(), now=NOW)
        assert result.agreed is True
        assert result.answers[1].disagreement == pytest.approx(0.0, abs=1e-9)

    def test_disagreeing_providers_are_measured_against_the_first(self):
        """Deciding which source is 'right' is not something this layer can
        know — so it reports the difference and stops there."""
        result = compare_providers(
            ProviderRegistry([ScriptedAdapter("a", [bars()], priority=10),
                              ScriptedAdapter("b", [bars(shift=0.05)],
                                              priority=20)]),
            request(), now=NOW)
        assert result.agreed is False
        assert result.answers[1].disagreement == pytest.approx(0.05, abs=1e-3)

    def test_an_empty_answer_counts_as_an_answer(self):
        result = compare_providers(
            ProviderRegistry([ScriptedAdapter("empty", [None])]),
            request(), now=NOW)
        assert result.answers[0].ok is True
        assert result.answers[0].bars == 0

    def test_the_result_is_json_serializable(self):
        import json

        result = compare_providers(
            ProviderRegistry([ScriptedAdapter("a", [bars()], priority=10),
                              ScriptedAdapter("b", [ProviderUnavailable("x")],
                                              priority=20)]),
            request(), now=NOW)
        payload = json.loads(json.dumps(result.as_dict()))
        assert payload["symbol"] == "SPY"
        assert len(payload["answers"]) == 2


class TestReplay:
    def test_it_reruns_the_request_through_the_live_ladder(self):
        a = ScriptedAdapter("a", [bars()])
        svc = service_with(a)
        first = svc.get_history("SPY", Timeframe.M5,
                                NOW - timedelta(hours=2), NOW)
        trace = svc.diagnostics.find(first.trace_id)

        result = replay(svc, trace)
        assert result.service_outcome == "live"
        assert result.service_bars > 0
        assert result.service_trace_id != first.trace_id

    def test_replay_bypasses_the_memo(self):
        """Otherwise it would measure a five-second-old copy of the very answer
        being investigated."""
        a = ScriptedAdapter("a", [bars()])
        svc = service_with(a)
        first = svc.get_history("SPY", Timeframe.M5,
                                NOW - timedelta(hours=2), NOW)
        calls_before = len(a.calls)
        replay(svc, svc.diagnostics.find(first.trace_id), compare=False)
        assert len(a.calls) > calls_before

    def test_compare_false_skips_the_provider_poll(self):
        a = ScriptedAdapter("a", [bars()])
        svc = service_with(a)
        first = svc.get_history("SPY", Timeframe.M5,
                                NOW - timedelta(hours=2), NOW)
        result = replay(svc, svc.diagnostics.find(first.trace_id), compare=False)
        assert result.answers == []
        assert result.service_outcome is not None

    def test_replaying_a_failed_request_reports_why_each_provider_failed(self):
        a = ScriptedAdapter("a", [ProviderUnavailable("upstream 503")])
        svc = service_with(a)
        first = svc.get_history("SPY", Timeframe.M5,
                                NOW - timedelta(hours=2), NOW)
        result = replay(svc, svc.diagnostics.find(first.trace_id))
        assert result.service_outcome == "failed"
        assert "503" in result.answers[0].error
