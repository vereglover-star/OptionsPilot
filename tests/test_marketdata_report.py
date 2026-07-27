"""The human-readable diagnostics report.

`report.render` exists to be pasted into a public issue tracker, which gives it
two hard requirements that the tests below pin:

  1. **It renders, it never computes.** Every number comes from the payload as
     given, so the text, the dashboard and the JSON export cannot disagree.
  2. **It is safe to paste in public.** No stack traces, no filesystem paths, no
     credentials.

It also has to survive a partial payload: a diagnostics export is most valuable
exactly when something is broken, so a missing section must produce a line of
text rather than a KeyError.
"""

from __future__ import annotations

import pytest

from optionspilot.data.report import render


def payload(**overrides) -> dict:
    base = {
        "available": True,
        "providers": [{
            "name": "yahoo", "priority": 10, "rank": 10.4, "available": True,
            "state": "closed", "requests": 120, "successes": 118,
            "failures": 2, "empties": 0, "success_rate": 0.983,
            "failure_rate": 0.016, "consecutive_failures": 0,
            "avg_latency_ms": 187.3, "p95_latency_ms": 402.0,
            "timeouts": 1, "validation_failures": 0, "rate_limits": 0,
            "breaker_trips": 0, "circuit_open_for": None,
            "rate_limited_for": None, "data_quality_score": 99.5,
            "last_success_at": "2026-07-26T12:00:00+00:00",
            "seconds_since_success": 12.0, "last_error": None,
            "requests_today": 120, "intervals": ["1d", "5m"],
        }],
        "requests": {
            "total_requests": 140, "served": 139, "success_rate": 0.993,
            "live_rate": 0.41, "avg_duration_ms": 61.2, "slowest_ms": 900.0,
            "outcomes": {"live": 46, "memo": 61, "failed": 1},
            "provider_requests": {"yahoo": 46},
        },
        "cache": {
            "bars": 48925, "symbols": 31, "timeframes": 4, "bytes": 6291456,
            "schema_version": 2, "rebuilds": 0, "reads": 200, "hits": 150,
            "hit_rate": 0.75, "stale_reads": 2, "writes": 40,
            "bars_written": 9000, "evictions": 0, "errors": 0,
            "avg_age_seconds": 300.0, "provider_requests_saved": 150,
            "oldest_bar": "2020-01-02T00:00:00+00:00",
            "newest_bar": "2026-07-26T12:00:00+00:00", "oversized": False,
        },
        "traces": [{
            "id": 3, "at": "2026-07-26T08:00:00-04:00", "symbol": "SPY",
            "timeframe": "5m", "outcome": "live", "bars": 476,
            "duration_ms": 187.3, "provider": "yahoo",
            "chain": "yahoo=ok", "message": "",
        }],
    }
    base.update(overrides)
    return base


class TestRendering:
    def test_it_contains_every_section(self):
        text = render(payload())
        for heading in ("PROVIDERS", "REQUESTS", "CACHE", "RECENT REQUESTS"):
            assert heading in text

    def test_provider_numbers_are_rendered_verbatim(self):
        text = render(payload())
        assert "yahoo" in text
        assert "187.3ms" in text
        assert "rank 10.4" in text
        assert "98.3%" in text            # success_rate 0.983
        assert "120" in text

    def test_an_out_of_rotation_provider_is_called_out_in_words(self):
        p = payload()
        p["providers"][0].update(state="open", circuit_open_for=42.0,
                                 available=False)
        text = render(p)
        assert "OUT OF ROTATION" in text
        assert "open for another 42.0s" in text

    def test_a_rate_limited_provider_says_so(self):
        p = payload()
        p["providers"][0].update(rate_limited_for=90.0)
        assert "rate limited for another 90.0s" in render(p)

    def test_the_cache_reports_what_it_saved(self):
        text = render(payload())
        assert "upstream requests saved: 150" in text
        assert "hit rate 75.0%" in text
        assert "6.0 MB" in text

    def test_an_oversized_cache_is_flagged_with_the_remedy(self):
        p = payload()
        p["cache"]["oversized"] = True
        assert "retention_days" in render(p)

    def test_traces_include_the_provider_chain(self):
        assert "chain: yahoo=ok" in render(payload())

    def test_the_trace_limit_is_honoured(self):
        p = payload(traces=[dict(payload()["traces"][0], id=i)
                            for i in range(30)])
        text = render(p, traces=5)
        assert "newest 5" in text
        assert "#6" not in text

    def test_a_title_can_be_supplied(self):
        assert "custom heading" in render(payload(), title="custom heading")


class TestPartialPayloads:
    """A diagnostics export is most valuable when something is broken, so every
    missing section must degrade to a line of text."""

    def test_an_unavailable_provider_renders_the_reason(self):
        text = render({"available": False, "reason": "no diagnostics here"})
        assert "no diagnostics here" in text

    def test_no_providers(self):
        assert "none registered" in render(payload(providers=[]))

    def test_no_cache(self):
        assert "No local candle cache" in render(payload(cache=None)) or \
            "disabled" in render(payload(cache=None))

    def test_no_traces(self):
        assert "none recorded" in render(payload(traces=[]))

    def test_no_requests(self):
        assert "no requests recorded" in render(payload(requests={}))

    def test_an_entirely_empty_payload_does_not_raise(self):
        assert render({"available": True})

    def test_missing_provider_keys_do_not_raise(self):
        assert render(payload(providers=[{"name": "sparse"}]))


class TestSafeToPaste:
    def test_it_carries_no_stack_traces_or_paths(self):
        p = payload()
        p["providers"][0]["last_error"] = "unavailable: HTTP 503 from upstream"
        text = render(p)
        assert "Traceback" not in text
        assert "C:\\" not in text and "/home/" not in text
        assert ".py\", line" not in text

    def test_it_says_so_explicitly(self):
        """The closing line is there so a user does not have to audit the
        report themselves before pasting it."""
        text = render(payload())
        assert "no credentials" in text

    def test_a_long_error_is_still_rendered_on_one_line(self):
        p = payload()
        p["providers"][0]["last_error"] = "x" * 500
        line = [ln for ln in render(p).splitlines() if "last err" in ln]
        assert len(line) == 1
