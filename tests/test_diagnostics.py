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
    ALL_OUTCOMES, Diagnostics, OUTCOME_EMPTY, OUTCOME_FAILED, OUTCOME_LIVE,
    OUTCOME_STALE,
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


# ── V0.5.3: structured logging ───────────────────────────────────────────────
#
# One line per request, key=value throughout. Deliberately greppable rather
# than JSON: `logs/data.log` is read by a human looking at a user's bug report,
# and `outcome=failed` is something you can find with Ctrl-F in Notepad.

class TestStructuredLogging:
    def _trace(self, diagnostics):
        t = diagnostics.start("spy", Timeframe.M5, None, None, False)
        t.attempt("yahoo", "RangeError", 12.0, detail="too old")
        t.attempt("yfinance", "ok", 88.0, bars=300)
        t.fallbacks = 1
        t.retries = 1
        t.validation = {"score": 98.0, "usable": True, "bars": 300,
                        "issues": [], "counts": {}}
        return t.finish(OUTCOME_LIVE, provider="yfinance", bars=300)

    def test_every_field_needed_to_diagnose_a_request_is_present(self):
        line = self._trace(Diagnostics()).log_line()
        for key in ("req=", "symbol=SPY", "tf=5m", "outcome=live",
                    "provider=yfinance", "bars=300", "duration_ms=",
                    "cache=miss", "memo=miss", "retries=1", "fallbacks=1",
                    "chain=", "quality=98.0", "usable=True"):
            assert key in line, key

    def test_the_fallback_chain_names_each_provider_and_its_verdict(self):
        """One glance answers 'who did we ask and what did they say' — the
        question a chart complaint always turns into."""
        line = self._trace(Diagnostics()).log_line()
        assert "chain=yahoo=RangeError > yfinance=ok" in line

    def test_a_request_with_no_attempts_still_renders(self):
        d = Diagnostics()
        trace = d.start("SPY", Timeframe.M5, None, None, False)
        assert "chain=-" in trace.finish(OUTCOME_EMPTY).log_line()

    def test_the_failure_reason_is_quoted_and_bounded(self):
        d = Diagnostics()
        trace = d.start("SPY", Timeframe.M5, None, None, False)
        line = trace.finish(OUTCOME_FAILED, message="x" * 500).log_line()
        assert 'reason="' in line
        assert len(line) < 400
        assert "\n" not in line

    def test_extended_hours_is_flagged(self):
        d = Diagnostics()
        trace = d.start("SPY", Timeframe.M5, None, None, True)
        assert "ext=1" in trace.finish(OUTCOME_LIVE).log_line()

    def test_the_chain_is_also_carried_in_the_trace_payload(self):
        """So a log excerpt and the dashboard agree without translation."""
        assert self._trace(Diagnostics()).as_dict()["chain"] == \
            "yahoo=RangeError > yfinance=ok"

    def test_structured_logging_can_be_switched_off(self):
        d = Diagnostics(structured_logging=False)
        trace = self._trace(d)
        d.record(trace)          # must not raise; falls back to the prose form
        assert d.summary()["total_requests"] == 1

    def test_a_failure_logs_at_warning_and_a_success_does_not(self, caplog):
        d = Diagnostics()
        with caplog.at_level("WARNING"):
            d.record(d.start("SPY", Timeframe.M5, None, None, False)
                     .finish(OUTCOME_FAILED, message="everything is down"))
            d.record(self._trace(d))
        assert "outcome=failed" in caplog.text
        assert "outcome=live" not in caplog.text
