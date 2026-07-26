"""Market-data diagnostics — the answer to "why did that chart look like that?"

Every history request records one `RequestTrace`: what was asked for, which
providers were tried and why each was skipped or failed, where the bars finally
came from, how long each stage took, what validation found, and what the caller
was ultimately handed. Traces live in a bounded in-memory ring, are summarised
into per-provider and per-outcome aggregates, and are exposed over
`GET /api/diagnostics/marketdata`.

The design goal is a specific one: **a chart complaint should be diagnosable
from one JSON response, without reproducing it.** Before this existed, working
out why a chart was blank meant correlating three log files against a user's
recollection of what they clicked. A trace records the decision path, so the
question "was that cache, Yahoo, or a validation reject?" is a lookup.

This module is deliberately free of I/O and of any dependency on the service
that feeds it, so tests can build traces directly and assert on the summary.
"""

from __future__ import annotations

import threading
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from optionspilot.core.logging_setup import get_logger

log = get_logger("data")

#: How many traces to retain. ~250 covers a long charting session; each trace is
#: a few hundred bytes, so the ring costs well under a megabyte.
MAX_TRACES = 250

#: Terminal states a request can end in. These are the same names the frontend
#: state machine uses, so a UI state and a backend trace can be compared
#: directly instead of translated.
OUTCOME_LIVE = "live"              # fresh bars from a provider
OUTCOME_MEMO = "memo"              # in-process memo hit (still fresh)
OUTCOME_CACHE = "cache"            # disk cache, within freshness
OUTCOME_STALE = "stale"            # disk cache, knowingly out of date
OUTCOME_EMPTY = "empty"            # no bars exist for this window (legitimate)
OUTCOME_EXHAUSTED = "exhausted"    # older than any provider can serve
OUTCOME_FAILED = "failed"          # nothing could answer; caller gets an error

ALL_OUTCOMES = (OUTCOME_LIVE, OUTCOME_MEMO, OUTCOME_CACHE, OUTCOME_STALE,
                OUTCOME_EMPTY, OUTCOME_EXHAUSTED, OUTCOME_FAILED)


@dataclass(slots=True)
class ProviderAttempt:
    """One provider's part in a request."""

    provider: str
    outcome: str                      # "ok" | "empty" | "skipped" | error class
    duration_ms: float = 0.0
    bars: int = 0
    detail: str = ""

    def as_dict(self) -> dict:
        return {"provider": self.provider, "outcome": self.outcome,
                "duration_ms": round(self.duration_ms, 1), "bars": self.bars,
                "detail": self.detail}


@dataclass(slots=True)
class RequestTrace:
    """The full decision path of one `MarketDataService.get_history` call."""

    id: int
    symbol: str
    timeframe: str
    start: datetime | None
    end: datetime | None
    extended_hours: bool
    # A lambda, not `_time.monotonic` itself: a bare reference would bind the
    # function object at class-definition time, so a test that swaps the clock
    # would see a start stamp from the real clock and a finish stamp from the
    # fake one (negative durations).
    started_at: float = field(default_factory=lambda: _time.monotonic())
    wall_clock: str = ""
    outcome: str = OUTCOME_FAILED
    provider: str | None = None
    bars: int = 0
    retries: int = 0
    fallbacks: int = 0
    cache_hit: bool = False
    memo_hit: bool = False
    duration_ms: float = 0.0
    validation: dict | None = None
    attempts: list[ProviderAttempt] = field(default_factory=list)
    message: str = ""
    earliest_available: datetime | None = None

    def attempt(self, provider: str, outcome: str, duration_ms: float = 0.0,
                bars: int = 0, detail: str = "") -> None:
        self.attempts.append(ProviderAttempt(provider, outcome, duration_ms,
                                             bars, detail))

    def finish(self, outcome: str, *, provider: str | None = None,
               bars: int = 0, message: str = "") -> "RequestTrace":
        self.outcome = outcome
        self.provider = provider
        self.bars = bars
        self.message = message
        self.duration_ms = (_time.monotonic() - self.started_at) * 1000.0
        return self

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "extended_hours": self.extended_hours,
            "at": self.wall_clock,
            "outcome": self.outcome,
            "provider": self.provider,
            "bars": self.bars,
            "retries": self.retries,
            "fallbacks": self.fallbacks,
            "cache_hit": self.cache_hit,
            "memo_hit": self.memo_hit,
            "duration_ms": round(self.duration_ms, 1),
            "validation": self.validation,
            "attempts": [a.as_dict() for a in self.attempts],
            "message": self.message,
            "earliest_available": (self.earliest_available.isoformat()
                                   if self.earliest_available else None),
        }


class Diagnostics:
    """Bounded trace ring + running aggregates. Thread-safe."""

    def __init__(self, max_traces: int = MAX_TRACES):
        self._lock = threading.Lock()
        self._traces: deque[RequestTrace] = deque(maxlen=max_traces)
        self._next_id = 1
        self._outcomes: dict[str, int] = {}
        self._provider_bars: dict[str, int] = {}
        self._provider_requests: dict[str, int] = {}
        self._total_duration_ms = 0.0
        self._total_requests = 0
        self._slowest_ms = 0.0

    def start(self, symbol: str, timeframe, start: datetime | None,
              end: datetime | None, extended_hours: bool) -> RequestTrace:
        with self._lock:
            trace_id = self._next_id
            self._next_id += 1
        return RequestTrace(
            id=trace_id, symbol=symbol.upper(), timeframe=str(timeframe),
            start=start, end=end, extended_hours=extended_hours,
            wall_clock=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def record(self, trace: RequestTrace) -> RequestTrace:
        with self._lock:
            self._traces.append(trace)
            self._outcomes[trace.outcome] = self._outcomes.get(trace.outcome, 0) + 1
            self._total_requests += 1
            self._total_duration_ms += trace.duration_ms
            self._slowest_ms = max(self._slowest_ms, trace.duration_ms)
            if trace.provider:
                self._provider_requests[trace.provider] = \
                    self._provider_requests.get(trace.provider, 0) + 1
                self._provider_bars[trace.provider] = \
                    self._provider_bars.get(trace.provider, 0) + trace.bars
        # A failed or knowingly-degraded request is worth a log line; a normal
        # one is not (the ring holds it either way).
        if trace.outcome in (OUTCOME_FAILED, OUTCOME_STALE):
            log.warning("history %s %s -> %s in %.0fms via %s: %s",
                        trace.symbol, trace.timeframe, trace.outcome,
                        trace.duration_ms, trace.provider or "-", trace.message)
        else:
            log.debug("history %s %s -> %s (%d bars) in %.0fms via %s",
                      trace.symbol, trace.timeframe, trace.outcome, trace.bars,
                      trace.duration_ms, trace.provider or "-")
        return trace

    # ── reporting ────────────────────────────────────────────────────────────

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            traces = list(self._traces)[-limit:]
        return [t.as_dict() for t in reversed(traces)]

    def summary(self) -> dict:
        with self._lock:
            total = self._total_requests
            outcomes = dict(self._outcomes)
            provider_requests = dict(self._provider_requests)
            provider_bars = dict(self._provider_bars)
            avg = self._total_duration_ms / total if total else 0.0
            slowest = self._slowest_ms
            live = self._outcomes.get(OUTCOME_LIVE, 0)
            served = total - self._outcomes.get(OUTCOME_FAILED, 0)
        return {
            "total_requests": total,
            "served": served,
            "success_rate": round(served / total, 4) if total else 1.0,
            "live_rate": round(live / total, 4) if total else 0.0,
            "avg_duration_ms": round(avg, 1),
            "slowest_ms": round(slowest, 1),
            "outcomes": {k: outcomes.get(k, 0) for k in ALL_OUTCOMES},
            "provider_requests": provider_requests,
            "provider_bars": provider_bars,
        }

    def find(self, trace_id: int) -> dict | None:
        with self._lock:
            for trace in self._traces:
                if trace.id == trace_id:
                    return trace.as_dict()
        return None

    def reset(self) -> None:
        with self._lock:
            self._traces.clear()
            self._outcomes.clear()
            self._provider_bars.clear()
            self._provider_requests.clear()
            self._total_duration_ms = 0.0
            self._total_requests = 0
            self._slowest_ms = 0.0


__all__ = ["Diagnostics", "RequestTrace", "ProviderAttempt", "MAX_TRACES",
           "ALL_OUTCOMES", "OUTCOME_LIVE", "OUTCOME_MEMO", "OUTCOME_CACHE",
           "OUTCOME_STALE", "OUTCOME_EMPTY", "OUTCOME_EXHAUSTED",
           "OUTCOME_FAILED"]
