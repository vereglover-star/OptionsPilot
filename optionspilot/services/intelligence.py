"""IntelligenceService — the client-facing projections of one snapshot.

These three functions were written inside `ui/server.py`, and they are the
clearest example of what V0.7.0 is separating. None of them is transport: they
decide *which of thirty-eight metrics is a headline*, *how many periods of a
five-year series a client should receive*, and *which behaviours are worth
showing first*. Those are product decisions about how the analysis is presented,
and a second client that wanted the dashboard would have had two choices —
import a FastAPI module, or make the same decisions again and slowly disagree
with the desktop about what a trader's headline numbers are.

They are also, and this is the part that made the leak invisible, *correct*.
Nothing here changes; it moves. `_intelligence_payload` and
`_intelligence_summary` are reproduced field for field, including the trimming
comment that explains why the cut happens at the transport boundary — which is
still true, and is now enforced by the boundary being a different package.

The one import allowed upward: `intelligence.confidence_of`. `intelligence/` is
a core-only pure leaf by its own architectural rule, so depending on it costs
this layer nothing and re-deriving an overall confidence here would be exactly
the second-owner drift that layer exists to prevent.
"""

from __future__ import annotations

from optionspilot.intelligence import IntelligenceSnapshot, confidence_of

#: How much of each period series a client payload carries. The snapshot itself
#: holds every period — a five-year daily series is thousands of points, and
#: shipping all of it to a browser that draws the last quarter is how a status
#: payload becomes a megabyte. Trimming happens HERE, at the presentation
#: boundary, so nothing in `intelligence/` has to know a client exists.
PERIOD_LIMITS = {"day": 120, "week": 78, "month": 36, "quarter": 20, "year": 10}

#: Metrics the dashboard's compact summary carries. The full registry is
#: available from the full payload; this is the headline row.
SUMMARY_METRICS = (
    "trades", "win_rate", "expectancy", "profit_factor", "avg_r", "total_pnl",
    "max_drawdown", "consistency", "stop_discipline_rate", "avg_process_score",
    "current_streak", "clean_trade_rate",
)


def payload(snapshot: IntelligenceSnapshot, *, full: bool = True) -> dict:
    """JSON for one snapshot, with the period series trimmed to what a client
    will actually draw."""
    doc = snapshot.to_dict()
    doc["periods"] = {
        name: series[-PERIOD_LIMITS.get(name, 60):]
        for name, series in doc["periods"].items()
    }
    doc["overall_confidence"] = confidence_of(snapshot).name.lower()
    if not full:
        # The Coach view renders behaviours, patterns, reports and the timeline;
        # what it never draws is a time series, which is also the only part of
        # the payload that grows without bound. So "not full" drops exactly
        # that, and the coach still gets one analysis in one request.
        doc.pop("periods", None)
    return doc


def summary(snapshot: IntelligenceSnapshot) -> dict:
    """The compact dashboard projection — headline metrics, the scores, the top
    few actions, goal progress and the newest report's summary line.

    A projection of the same snapshot every other view reads, never a second
    analysis: two screens disagreeing about the same trader is the failure this
    whole layer exists to prevent.
    """
    doc = snapshot.to_dict()
    return {
        "generated": doc["generated"],
        "trades_analyzed": snapshot.trades_analyzed,
        "data_sufficiency": snapshot.data_sufficiency,
        "overall_confidence": confidence_of(snapshot).name.lower(),
        "span": {"start": snapshot.span_start, "end": snapshot.span_end},
        "metrics": [snapshot.metrics[k].to_dict() for k in SUMMARY_METRICS
                    if k in snapshot.metrics],
        "scores": doc["scores"],
        "recommendations": doc["recommendations"][:4],
        "behaviors": [b for b in doc["behaviors"] if b["detected"]][:5],
        "goals": doc["goals"],
        "achievements": doc["achievements"],
        "timeline": doc["timeline"][:6],
        # Lessons are already capped at MAX_LESSONS by the curriculum engine,
        # and the Learning view renders the full set from this same projection
        # rather than issuing a second request for the whole snapshot.
        "lessons": doc["lessons"],
        "risk": {"observations": snapshot.risk.get("observations", [])[:4],
                 "assessable": snapshot.risk.get("assessable", False)},
        "latest_report": doc["reports"][0] if doc["reports"] else None,
        "notes": doc["notes"],
    }


class IntelligenceService:
    """The projections bound to a source of snapshots.

    `provider` is duck-typed to `intelligence_snapshot(force: bool = False)` —
    `Orchestrator` satisfies it. Injected rather than imported so this service
    can be driven by a test double, a replay, or a future backend host that
    composes the stack differently.
    """

    def __init__(self, provider):
        self._provider = provider

    def snapshot(self, *, force: bool = False) -> IntelligenceSnapshot:
        return self._provider.intelligence_snapshot(force=force)

    def full(self, *, full: bool = True) -> dict:
        return payload(self.snapshot(), full=full)

    def summary(self) -> dict:
        return summary(self.snapshot())
