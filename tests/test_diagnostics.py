"""Market-data diagnostics — the record that makes a chart complaint solvable.

The design goal being tested: a user saying "the chart looked wrong" should be
answerable from one JSON response, without reproducing it. So the trace has to
survive being serialized, has to name the decision path (not just the result),
and has to be bounded so a long session cannot grow it without limit.
"""

import json

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.diagnostics import (
    ALL_OUTCOMES, Diagnostics, OUTCOME_FAILED, OUTCOME_LIVE, OUTCOME_STALE,
)


def trace(diagnostics, symbol="SPY", outcome=OUTCOME_LIVE, bars=100,
          provider="yahoo"):
    t = diagnostics.start(symbol, Timeframe.M5, None, None, False)
    return diagnostics.record(t.finish(outcome, provider=provider, bars=bars))


class TestTraceContent:
    def test_a_trace_records_the_request_and_its_answer(self):
        d = Diagnostics()
        t = trace(d)
        payload = d.find(t.id)
        assert payload["symbol"] == "SPY"
        assert payload["timeframe"] == "5m"
        assert payload["outcome"] == OUTCOME_LIVE
        assert payload["provider"] == "yahoo" and payload["bars"] == 100
        assert payload["at"]                   # local wall clock, for the user

    def test_attempts_record_the_decision_path_not_just_the_result(self):
        """Which providers were tried, and why each one didn't answer. This is
        the difference between 'the chart was blank' and 'yahoo 422'd on range,
        yfinance timed out, stooq has no 5m'."""
        d = Diagnostics()
        t = d.start("SPY", Timeframe.M5, None, None, False)
        t.attempt("yahoo", "ProviderRangeError", 12.0, detail="out of range")
        t.attempt("yfinance", "ProviderUnavailable", 10_000.0, detail="timeout")
        t.attempt("stooq", "skipped", detail="no 5m interval")
        d.record(t.finish(OUTCOME_FAILED, message="nothing could answer"))
        attempts = d.find(t.id)["attempts"]
        assert [a["provider"] for a in attempts] == ["yahoo", "yfinance", "stooq"]
        assert attempts[1]["duration_ms"] == 10_000.0
        assert "timeout" in attempts[1]["detail"]

    def test_a_trace_is_json_serializable(self):
        """It is served over HTTP; a non-serializable field would 500 the
        diagnostics endpoint exactly when it is most needed."""
        d = Diagnostics()
        t = trace(d)
        assert json.loads(json.dumps(d.find(t.id)))

    def test_duration_is_measured_not_assumed(self):
        d = Diagnostics()
        t = d.start("SPY", Timeframe.M5, None, None, False)
        d.record(t.finish(OUTCOME_LIVE, bars=1))
        assert d.find(t.id)["duration_ms"] >= 0

    def test_an_unknown_id_returns_none_rather_than_raising(self):
        assert Diagnostics().find(999) is None


class TestRing:
    def test_the_ring_is_bounded(self):
        """A long charting session must not grow diagnostics without limit."""
        d = Diagnostics(max_traces=10)
        for i in range(50):
            trace(d, symbol=f"SYM{i}")
        recent = d.recent(100)
        assert len(recent) == 10
        assert recent[0]["symbol"] == "SYM49"        # newest first
        assert all(r["symbol"] != "SYM0" for r in recent)

    def test_recent_returns_newest_first(self):
        d = Diagnostics()
        trace(d, symbol="AAA")
        trace(d, symbol="BBB")
        assert [r["symbol"] for r in d.recent()] == ["BBB", "AAA"]

    def test_ids_are_unique_and_increasing(self):
        d = Diagnostics()
        ids = [trace(d).id for _ in range(5)]
        assert ids == sorted(ids) and len(set(ids)) == 5

    def test_aggregates_survive_eviction_from_the_ring(self):
        """Counters describe the whole session, not just the retained window —
        otherwise a 'success rate' would silently reset every 250 requests."""
        d = Diagnostics(max_traces=5)
        for _ in range(20):
            trace(d)
        assert d.summary()["total_requests"] == 20


class TestSummary:
    def test_success_rate_excludes_only_hard_failures(self):
        """Stale and cached answers ARE answers; counting them as failures
        would make the app look broken during a normal weekend."""
        d = Diagnostics()
        trace(d, outcome=OUTCOME_LIVE)
        trace(d, outcome=OUTCOME_STALE)
        trace(d, outcome=OUTCOME_FAILED)
        summary = d.summary()
        assert summary["total_requests"] == 3
        assert summary["served"] == 2
        assert summary["success_rate"] == pytest.approx(2 / 3, rel=1e-3)
        assert summary["live_rate"] == pytest.approx(1 / 3, rel=1e-3)

    def test_every_outcome_appears_even_at_zero(self):
        """A missing key would make a dashboard read 'undefined' rather than
        'this never happened'."""
        outcomes = Diagnostics().summary()["outcomes"]
        assert set(outcomes) == set(ALL_OUTCOMES)
        assert all(v == 0 for v in outcomes.values())

    def test_per_provider_totals_are_tracked(self):
        d = Diagnostics()
        trace(d, provider="yahoo", bars=100)
        trace(d, provider="yahoo", bars=50)
        trace(d, provider="stooq", bars=10)
        summary = d.summary()
        assert summary["provider_requests"] == {"yahoo": 2, "stooq": 1}
        assert summary["provider_bars"] == {"yahoo": 150, "stooq": 10}

    def test_an_empty_recorder_reports_a_sane_summary(self):
        summary = Diagnostics().summary()
        assert summary["total_requests"] == 0
        assert summary["success_rate"] == 1.0        # nothing has failed
        assert summary["avg_duration_ms"] == 0.0

    def test_reset_clears_traces_and_aggregates(self):
        d = Diagnostics()
        trace(d)
        d.reset()
        assert d.recent() == [] and d.summary()["total_requests"] == 0


def test_outcome_names_match_the_frontend_state_machine():
    """The UI's `data-ch-state` values and these outcomes are compared directly
    during debugging, so they must not drift into synonyms."""
    assert set(ALL_OUTCOMES) == {
        "live", "memo", "cache", "stale", "empty", "exhausted", "failed"}
