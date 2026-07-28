"""Market-data replay — re-run a request that already happened, and compare.

When a user reports "the SPY 5-minute chart looked wrong at 10:32", the trace
ring already holds *what was asked and what happened*. What it cannot tell you
is whether it still happens, or whether a different provider would have
answered differently. That is what this module adds:

    replay(service, trace)        run the same request again through the ladder
    compare_providers(registry, request)
                                  ask EVERY provider the same question directly

The second is the useful one during a real investigation. It deliberately
bypasses the tier ladder — no memo, no disk cache, no failover, no breaker —
because the question being asked is "what does each source actually say?", and
a ladder that stops at the first success answers a different question. Bars,
latency, quality score and disagreement against the primary come back per
provider, so "Stooq and Yahoo disagree about last Tuesday" is a lookup rather
than a hunch.

## Why there is no separate recorder

There is no `ReplayRecorder` here, and that is deliberate. Every request is
already recorded — once, in `diagnostics.RequestTrace`, with the symbol,
timeframe, window and session flag replay needs. A second recorder would be a
second thing to keep in sync with the first, for no information gain. Replay
therefore takes a trace (or a trace id) as its input, which also means anything
in the dashboard's trace list can be replayed by pasting its id.

Nothing here is on the request path. It is a diagnostic tool: it costs real
upstream requests, so it is invoked explicitly by a script or an endpoint, and
never by the chart.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import HistoryRequest, ProviderError
from optionspilot.data.quality import disagreement, validate_history
from optionspilot.data.registry import ProviderRegistry

log = get_logger("data")


@dataclass(slots=True)
class ProviderAnswer:
    """One provider's unmediated answer to a replayed request."""

    provider: str
    ok: bool
    bars: int = 0
    duration_ms: float = 0.0
    quality: float | None = None
    first_bar: datetime | None = None
    last_bar: datetime | None = None
    #: Median relative close difference against the reference provider, or None
    #: when it is the reference / there is no overlap.
    disagreement: float | None = None
    error: str = ""
    skipped: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "bars": self.bars,
            "duration_ms": round(self.duration_ms, 1),
            "quality": round(self.quality, 1) if self.quality is not None else None,
            "first_bar": self.first_bar.isoformat() if self.first_bar else None,
            "last_bar": self.last_bar.isoformat() if self.last_bar else None,
            "disagreement": (round(self.disagreement, 6)
                             if self.disagreement is not None else None),
            "error": self.error,
            "skipped": self.skipped,
        }


@dataclass(slots=True)
class ReplayResult:
    """A replayed request and every provider's answer to it."""

    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    extended_hours: bool = False
    replayed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    answers: list[ProviderAnswer] = field(default_factory=list)
    #: The outcome the live ladder produced for the same request, when replay
    #: was asked to run it too.
    service_outcome: str | None = None
    service_provider: str | None = None
    service_bars: int = 0
    service_trace_id: int | None = None

    @property
    def agreed(self) -> bool:
        """True when no answering provider disagreed materially with the
        reference. Vacuously true when fewer than two providers answered."""
        return all(a.disagreement is None or a.disagreement <= DISAGREE_EPSILON
                   for a in self.answers if a.ok)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "extended_hours": self.extended_hours,
            "replayed_at": self.replayed_at.isoformat(),
            "service": {
                "outcome": self.service_outcome,
                "provider": self.service_provider,
                "bars": self.service_bars,
                "trace_id": self.service_trace_id,
            } if self.service_outcome is not None else None,
            "answers": [a.as_dict() for a in self.answers],
            "agreed": self.agreed,
        }


#: Below this median relative difference two providers are quoting the same
#: series with different rounding, not disagreeing.
DISAGREE_EPSILON = 0.005


def request_from_trace(trace: dict) -> HistoryRequest:
    """Rebuild the request a recorded trace describes.

    Raises ValueError on a trace that predates window recording, rather than
    silently replaying a made-up window.
    """
    if not trace.get("start") or not trace.get("end"):
        raise ValueError(f"trace {trace.get('id')} did not record a window")
    return HistoryRequest(
        symbol=str(trace["symbol"]).upper(),
        timeframe=Timeframe.from_string(str(trace["timeframe"])),
        start=datetime.fromisoformat(trace["start"]),
        end=datetime.fromisoformat(trace["end"]),
        extended_hours=bool(trace.get("extended_hours")),
    )


def compare_providers(registry: ProviderRegistry, request: HistoryRequest, *,
                      now: datetime | None = None,
                      include_open_breakers: bool = True) -> ReplayResult:
    """Ask every registered provider the same question, directly.

    Breaker state is ignored by default: when you are debugging, "this provider
    is currently out of rotation" is a fact you want to see the answer *behind*,
    not a reason to skip it.

    A provider that is **permanently** unusable — no API key, disabled in
    configuration — is skipped regardless. `include_open_breakers` is about
    breakers, and stretching it to cover credentials would mean every replay
    fired a real request at every unconfigured provider, collected a 401, and
    marked it auth-failed. That poisons the health state of providers the user
    never set up, and spends requests to learn something already known.
    """
    now = now or datetime.now(timezone.utc)
    result = ReplayResult(
        symbol=request.symbol, timeframe=str(request.timeframe),
        start=request.start, end=request.end,
        extended_hours=request.extended_hours)

    reference: pd.DataFrame | None = None
    for adapter in registry.adapters:
        answer = ProviderAnswer(provider=adapter.provider_name, ok=False)
        if not adapter.supports_interval(request.timeframe):
            answer.skipped = f"no {request.timeframe} interval"
            result.answers.append(answer)
            continue
        if not adapter.supports_symbol(request.symbol):
            answer.skipped = "symbol not served"
            result.answers.append(answer)
            continue
        # The shared gate every request-spending path uses: refuses what is
        # permanently unusable (no key, disabled) or budgeted out, while still
        # permitting a provider whose breaker is merely open. Replay must not
        # be the thing that fires a doomed request at an unconfigured provider,
        # nor the thing that spends the last of a 25-a-day allowance.
        spendable, refusal = adapter.can_spend_request()
        if not spendable:
            answer.skipped = refusal
            result.answers.append(answer)
            continue
        if not include_open_breakers and not adapter.monitor.available():
            answer.skipped = "circuit breaker open"
            result.answers.append(answer)
            continue

        t0 = _time.monotonic()
        try:
            frame = adapter.fetch_history(request, now=now)
        except ProviderError as exc:
            answer.duration_ms = (_time.monotonic() - t0) * 1000.0
            answer.error = f"{type(exc).__name__}: {exc}"[:200]
            result.answers.append(answer)
            continue
        except Exception as exc:  # noqa: BLE001 — a replay must never crash
            answer.duration_ms = (_time.monotonic() - t0) * 1000.0
            answer.error = f"InternalError: {exc}"[:200]
            result.answers.append(answer)
            continue
        answer.duration_ms = (_time.monotonic() - t0) * 1000.0

        if frame.empty:
            answer.ok = True          # answering "no bars" IS an answer
            result.answers.append(answer)
            continue
        validated, report = validate_history(
            frame, request.timeframe, now=now,
            context=f"replay {adapter.provider_name} {request.symbol}")
        answer.ok = True
        answer.bars = len(validated)
        answer.quality = report.score
        if not validated.empty:
            answer.first_bar = validated.index[0].to_pydatetime()
            answer.last_bar = validated.index[-1].to_pydatetime()
        # The first provider that returned usable bars is the reference; every
        # later one is measured against it.
        if reference is None:
            reference = validated
        else:
            answer.disagreement = disagreement(reference, validated)
        result.answers.append(answer)

    return result


def replay(service, trace: dict, *, compare: bool = True) -> ReplayResult:
    """Re-run a recorded request through the live service, optionally polling
    every provider directly as well.

    The service run is what reproduces the user's experience; the comparison is
    what explains it.
    """
    request = request_from_trace(trace)
    now = datetime.now(timezone.utc)
    if compare:
        result = compare_providers(service.registry, request, now=now)
    else:
        result = ReplayResult(symbol=request.symbol,
                              timeframe=str(request.timeframe),
                              start=request.start, end=request.end,
                              extended_hours=request.extended_hours)
    # Bypass the memo so a replay measures the ladder, not a five-second-old
    # copy of the answer we are trying to explain.
    service.invalidate(request.symbol)
    live = service.get_history(request.symbol, request.timeframe,
                               request.start, request.end,
                               extended_hours=request.extended_hours,
                               allow_stale=True)
    result.service_outcome = live.outcome
    result.service_provider = live.provider
    result.service_bars = live.bars
    result.service_trace_id = live.trace_id
    log.info("replayed trace %s: %s %s -> %s (%d bars) across %d providers",
             trace.get("id"), request.symbol, request.timeframe,
             live.outcome, live.bars, len(result.answers))
    return result


__all__ = ["replay", "compare_providers", "request_from_trace",
           "ReplayResult", "ProviderAnswer", "DISAGREE_EPSILON"]
