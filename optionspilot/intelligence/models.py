"""The shared vocabulary of the Trading Intelligence Engine.

Every engine in `intelligence/` produces these types and nothing else, which is
what lets the dashboard, the coach, the journal, the reports and any future
consumer read one another's output without a translation layer.

Three rules run through every model here and are load-bearing:

1. **Nothing is stated without evidence.** Every conclusion-shaped object
   (`BehaviorFinding`, `Pattern`, `Recommendation`, `ScoreCard`, `TimelineEntry`)
   carries an `evidence` tuple of measured `Evidence` items, each with the
   sample it was measured over and — where it makes sense — the exact
   `trade_ids` behind it. That tuple is what the UI's "Why?" affordance shows.
2. **Insufficient evidence is a first-class answer.** A score is `None`, not 50.
   A behavior is `assessable=False` with a stated reason, not "not detected".
   `Confidence.NONE` exists precisely so an engine can say "I don't know".
3. **Values are measured, never modelled.** No estimate here is extrapolated
   beyond the trades it was computed from. Counterfactual figures (the "would
   have improved expectancy by 14%" style) are recomputed over the actual
   historical sample and labelled as such — see `Impact`.

These are plain dataclasses, matching `core/models.py`; validation lives at the
edges. `to_dict()` on each is the JSON contract the API and UI consume.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from datetime import datetime


class Confidence(enum.Enum):
    """How much weight a conclusion deserves.

    Ordered, and comparable, so callers can filter (`>= Confidence.MEDIUM`).
    NONE is not "zero confidence in a negative finding" — it means *the data
    could not answer the question at all*.
    """

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    def __lt__(self, other: "Confidence") -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: "Confidence") -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: "Confidence") -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: "Confidence") -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.value >= other.value

    @property
    def label(self) -> str:
        return {"NONE": "insufficient data", "LOW": "low",
                "MEDIUM": "medium", "HIGH": "high"}[self.name]


class Severity(enum.Enum):
    """How much a finding should worry the trader. `POSITIVE` is deliberately in
    the same enum: the engine reports what is going right with the same
    machinery it reports what is going wrong."""

    POSITIVE = "positive"
    INFO = "info"
    MINOR = "minor"
    MODERATE = "moderate"
    SERIOUS = "serious"

    @property
    def rank(self) -> int:
        return {"positive": 0, "info": 1, "minor": 2,
                "moderate": 3, "serious": 4}[self.value]


class Trend(enum.Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    UNKNOWN = "unknown"


def _finite(value: float | int | None) -> float | int | None:
    """JSON has no infinity or NaN. Profit factor legitimately reaches infinity
    (a sample with no losers), so it is serialised as None with the sample size
    still attached — the caller renders '∞' or '—' from context, and never ships
    a payload that `json.dumps` would emit as invalid JSON."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        return None
    return value


@dataclass(frozen=True, slots=True)
class Evidence:
    """One measured fact. The atom every conclusion in this package is built
    from — a label, the number that was actually computed, and how many trades
    it came from.

    `trade_ids` is the explainability hook: when it is populated, the UI can
    take the user straight from "you chase trades" to the exact trades that
    produced the claim. It is capped by the producing engine (see
    `intelligence.behavior.MAX_CITED`) so a payload can't grow without bound.
    """

    label: str
    value: float | int | str | None
    sample: int = 0
    detail: str = ""
    trade_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "value": _finite(self.value) if not isinstance(self.value, str) else self.value,
            "sample": self.sample,
            "detail": self.detail,
            "trade_ids": list(self.trade_ids),
        }


@dataclass(frozen=True, slots=True)
class Metric:
    """A named number the whole system can address by `key`.

    The `key` is the stable contract: goals target metrics by key, scorecards
    cite them by key, and the report writer looks them up by key. Renaming one
    is a breaking change; adding one is not.

    `value` is None whenever the metric could not be computed from the available
    trades — which is a different statement from zero, and rendered differently.
    """

    key: str
    label: str
    value: float | None
    unit: str = ""              # "", "%", "$", "R", "min", "trades"
    sample: int = 0
    explanation: str = ""
    higher_is_better: bool = True

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "value": _finite(self.value),
            "unit": self.unit, "sample": self.sample,
            "explanation": self.explanation,
            "higher_is_better": self.higher_is_better,
        }


@dataclass(frozen=True, slots=True)
class PeriodStat:
    """One bucket of a time series (a day, week, month, quarter or year).

    `key` is the sortable period identifier produced by `intelligence.windows`
    ("2026-07", "2026-W30", "2026-Q3"); `label` is how a human reads it.
    """

    key: str
    label: str
    trades: int
    wins: int
    win_rate: float
    pnl: float
    expectancy: float
    profit_factor: float | None
    avg_r: float | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "trades": self.trades,
            "wins": self.wins, "win_rate": self.win_rate,
            "pnl": self.pnl, "expectancy": self.expectancy,
            "profit_factor": _finite(self.profit_factor),
            "avg_r": _finite(self.avg_r),
        }


@dataclass(frozen=True, slots=True)
class Impact:
    """A quantified, historical counterfactual.

    Deliberately narrow: `baseline` and `adjusted` are both recomputed over the
    trades that actually happened, so this is "what your record would have shown
    if these specific trades had gone differently" — NOT a forecast. `basis`
    states the assumption in words, and the UI is expected to show it alongside
    the number so the claim can never be read as a prediction.
    """

    metric: str                 # metric key the impact is expressed in
    baseline: float | None
    adjusted: float | None
    delta: float | None
    unit: str = ""
    basis: str = ""             # the assumption, in words
    sample: int = 0

    @property
    def pct_change(self) -> float | None:
        if self.baseline in (None, 0) or self.adjusted is None:
            return None
        return round((self.adjusted - self.baseline) / abs(self.baseline) * 100, 1)

    def to_dict(self) -> dict:
        return {
            "metric": self.metric, "baseline": _finite(self.baseline),
            "adjusted": _finite(self.adjusted), "delta": _finite(self.delta),
            "unit": self.unit, "basis": self.basis, "sample": self.sample,
            "pct_change": _finite(self.pct_change),
        }


@dataclass(frozen=True, slots=True)
class BehaviorFinding:
    """The verdict on one behavior, for one trader, over one window.

    `assessable=False` is the honest answer for behaviors the captured data
    genuinely cannot speak to (see `behavior.BEHAVIORS` — hesitation needs
    signal-to-entry latency, which nothing in this system records). It is not
    the same as `detected=False`, which is a real, evidenced negative.
    """

    id: str
    label: str
    assessable: bool
    detected: bool
    severity: Severity
    confidence: Confidence
    occurrences: int
    sample: int
    rate: float                 # occurrences / sample, 0..1
    summary: str                # one sentence, references the numbers
    evidence: tuple[Evidence, ...] = ()
    impact: Impact | None = None
    unassessable_reason: str = ""
    trend: Trend = Trend.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "assessable": self.assessable,
            "detected": self.detected, "severity": self.severity.value,
            "confidence": self.confidence.name.lower(),
            "confidence_label": self.confidence.label,
            "occurrences": self.occurrences, "sample": self.sample,
            "rate": round(self.rate, 4),
            "summary": self.summary,
            "evidence": [e.to_dict() for e in self.evidence],
            "impact": self.impact.to_dict() if self.impact else None,
            "unassessable_reason": self.unassessable_reason,
            "trend": self.trend.value,
        }


@dataclass(frozen=True, slots=True)
class Pattern:
    """A discovered relationship between one dimension of a trade and its
    outcome — "you win 71% on Tuesdays", "you lose money above 60% IV".

    Patterns are *discovered*, not authored: `patterns.py` walks every declared
    dimension, buckets the trades, and keeps whatever clears the sample and
    significance floors. `edge` is the bucket's win rate minus the trader's own
    baseline, so a pattern is always relative to that trader, never to an
    external norm.
    """

    dimension: str              # "weekday", "setup_quality", "iv_bucket", …
    dimension_label: str
    bucket: str                 # "Tuesday", "excellent", "high (>60%)"
    kind: str                   # "strength" | "weakness"
    trades: int
    wins: int
    win_rate: float
    baseline_win_rate: float
    edge: float                 # win_rate - baseline_win_rate
    expectancy: float
    baseline_expectancy: float
    pnl: float
    confidence: Confidence
    p_value: float | None       # two-proportion test vs. the rest of the sample
    summary: str
    evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "dimension_label": self.dimension_label,
            "bucket": self.bucket, "kind": self.kind, "trades": self.trades,
            "wins": self.wins, "win_rate": round(self.win_rate, 4),
            "baseline_win_rate": round(self.baseline_win_rate, 4),
            "edge": round(self.edge, 4),
            "expectancy": round(self.expectancy, 2),
            "baseline_expectancy": round(self.baseline_expectancy, 2),
            "pnl": round(self.pnl, 2),
            "confidence": self.confidence.name.lower(),
            "confidence_label": self.confidence.label,
            "p_value": _finite(self.p_value),
            "summary": self.summary,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One weighted input to a composite score, kept so the score can explain
    itself down to the number that moved it."""

    key: str
    label: str
    value: float | None         # the component's own 0..100 reading
    weight: float
    detail: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label,
                "value": _finite(self.value), "weight": self.weight,
                "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ScoreCard:
    """A composite 0–100 score that always shows its working.

    `value` is None when no component could be computed — a trader with three
    trades gets "not enough data", never a flattering or damning number pulled
    out of nothing.
    """

    key: str
    label: str
    value: float | None
    grade: str
    confidence: Confidence
    sample: int
    explanation: str
    components: tuple[ScoreComponent, ...] = ()
    trend: float | None = None          # change vs. the previous window

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "value": _finite(self.value),
            "grade": self.grade, "confidence": self.confidence.name.lower(),
            "confidence_label": self.confidence.label,
            "sample": self.sample, "explanation": self.explanation,
            "components": [c.to_dict() for c in self.components],
            "trend": _finite(self.trend),
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One thing to do next, with the numbers that produced it.

    `action` is the imperative ("place the stop within 60 seconds of the fill");
    `rationale` is the evidence in a sentence; `impact` is the historical
    counterfactual where one is computable. `priority` orders the list and is
    derived from severity × confidence × measured cost — never hand-assigned.
    """

    id: str
    title: str
    action: str
    rationale: str
    priority: float
    confidence: Confidence
    severity: Severity
    source: str                 # "behavior" | "pattern" | "risk" | "goal" | "score"
    evidence: tuple[Evidence, ...] = ()
    impact: Impact | None = None
    lesson_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "action": self.action,
            "rationale": self.rationale, "priority": round(self.priority, 3),
            "confidence": self.confidence.name.lower(),
            "confidence_label": self.confidence.label,
            "severity": self.severity.value, "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
            "impact": self.impact.to_dict() if self.impact else None,
            "lesson_ids": list(self.lesson_ids),
        }


@dataclass(frozen=True, slots=True)
class LessonRecommendation:
    """A curriculum item surfaced *because of* a measured weakness.

    The four fields the Learning tab is contractually required to show are all
    here and all mandatory: `why` (why this lesson), `triggered_by` (which
    statistic fired it), `problem` (what it will fix) and `impact` (what it is
    worth, historically). A lesson with no trigger is never recommended.
    """

    lesson_id: str
    title: str
    topic: str
    why: str
    triggered_by: tuple[Evidence, ...]
    problem: str
    priority: float
    confidence: Confidence
    impact: Impact | None = None
    minutes: int = 0

    def to_dict(self) -> dict:
        return {
            "lesson_id": self.lesson_id, "title": self.title, "topic": self.topic,
            "why": self.why, "problem": self.problem,
            "triggered_by": [e.to_dict() for e in self.triggered_by],
            "priority": round(self.priority, 3),
            "confidence": self.confidence.name.lower(),
            "confidence_label": self.confidence.label,
            "impact": self.impact.to_dict() if self.impact else None,
            "minutes": self.minutes,
        }


@dataclass(frozen=True, slots=True)
class Goal:
    """A measurable commitment, expressed against a metric key.

    Goals are intentionally not free text: `metric` must name a metric the
    engines actually produce, so progress is computed, never self-reported.
    `comparator` is ">=" or "<=" and `window` is a `windows.WindowSpec` name.
    """

    id: str
    label: str
    metric: str
    comparator: str             # ">=" | "<="
    target: float
    window: str = "lifetime"    # "lifetime" | "last_30d" | "last_20_trades" | …
    unit: str = ""
    created: str = ""           # ISO date
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "metric": self.metric,
            "comparator": self.comparator, "target": self.target,
            "window": self.window, "unit": self.unit,
            "created": self.created, "active": self.active,
        }

    @staticmethod
    def from_dict(doc: dict) -> "Goal | None":
        """Tolerant *structural* reader for the user-editable goals file.

        Returns None rather than raising on any malformed entry: a hand-edited
        `goals.json` must cost the user their goals, never the app's startup
        (the lesson `data/control.py` learned in V0.5.7).

        This checks shape only — that the required fields exist and the target
        is a real number. Whether the metric, comparator and window are ones the
        system can actually evaluate is `goals.validate()`'s job, and keeping
        the two separate is what lets the API answer "comparator must be '>=' or
        '<='" instead of a generic "this goal is malformed".
        """
        try:
            gid = str(doc["id"])
            metric = str(doc["metric"])
            comparator = str(doc.get("comparator", ">="))
            target = float(doc["target"])
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if not gid or not metric or not math.isfinite(target):
            return None
        return Goal(
            id=gid, label=str(doc.get("label") or gid), metric=metric,
            comparator=comparator, target=target,
            window=str(doc.get("window") or "lifetime"),
            unit=str(doc.get("unit") or ""),
            created=str(doc.get("created") or ""),
            active=bool(doc.get("active", True)),
        )


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """Where a goal stands right now. `progress` is 0..1 and clamped; `met` is
    the plain boolean the achievement system keys off."""

    goal: Goal
    current: float | None
    met: bool
    progress: float             # 0..1
    sample: int
    detail: str
    trend: Trend = Trend.UNKNOWN

    def to_dict(self) -> dict:
        return {
            **self.goal.to_dict(),
            "current": _finite(self.current), "met": self.met,
            "progress": round(self.progress, 4), "sample": self.sample,
            "detail": self.detail, "trend": self.trend.value,
        }


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One dated statement about how the trader has changed.

    Only ever emitted when both sides of the comparison clear the sample floor —
    "your average risk has fallen 18% since May" requires a May worth comparing.
    """

    period: str                 # the period the change was measured to
    kind: str                   # "improvement" | "decline" | "milestone" | "streak"
    headline: str
    detail: str
    evidence: tuple[Evidence, ...] = ()
    magnitude: float = 0.0      # absolute size of the change, for ranking

    def to_dict(self) -> dict:
        return {
            "period": self.period, "kind": self.kind, "headline": self.headline,
            "detail": self.detail, "magnitude": round(self.magnitude, 4),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class Achievement:
    """An earned marker of a durable habit. Deliberately *not* participation
    points: every achievement in `intelligence.achievements` requires either a
    sustained streak or a measured improvement, and none can be earned by a
    single lucky trade."""

    id: str
    title: str
    description: str
    earned: bool
    earned_on: str = ""         # ISO date, "" while unearned
    progress: float = 0.0       # 0..1 toward earning it
    detail: str = ""
    tier: str = "bronze"        # bronze | silver | gold

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "earned": self.earned, "earned_on": self.earned_on,
            "progress": round(self.progress, 4), "detail": self.detail,
            "tier": self.tier,
        }


@dataclass(frozen=True, slots=True)
class ReportSection:
    heading: str
    body: tuple[str, ...]       # paragraphs / bullet lines, already prose
    metrics: tuple[Metric, ...] = ()

    def to_dict(self) -> dict:
        return {"heading": self.heading, "body": list(self.body),
                "metrics": [m.to_dict() for m in self.metrics]}


@dataclass(frozen=True, slots=True)
class Report:
    """A generated coaching report — the narrative face of one analysis run."""

    period: str                 # "2026-W30" | "2026-07"
    kind: str                   # "weekly" | "monthly"
    title: str
    summary: str
    sections: tuple[ReportSection, ...]
    generated: str = ""         # ISO timestamp
    trades: int = 0

    def to_dict(self) -> dict:
        return {
            "period": self.period, "kind": self.kind, "title": self.title,
            "summary": self.summary, "generated": self.generated,
            "trades": self.trades,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass(slots=True)
class IntelligenceSnapshot:
    """Everything one analysis run produced — the single object every consumer
    reads.

    This is the "generated once, reusable everywhere" contract in concrete form:
    the dashboard, the coach, the journal detail view, the learning tab and the
    report writer all project from this one structure rather than each running
    their own pass over the trades.
    """

    generated: datetime
    trades_analyzed: int
    span_start: str = ""        # ISO date of the earliest analyzed trade
    span_end: str = ""
    data_sufficiency: str = "none"   # none | minimal | partial | good
    metrics: dict[str, Metric] = field(default_factory=dict)
    periods: dict[str, tuple[PeriodStat, ...]] = field(default_factory=dict)
    behaviors: tuple[BehaviorFinding, ...] = ()
    patterns: tuple[Pattern, ...] = ()
    scores: tuple[ScoreCard, ...] = ()
    risk: dict = field(default_factory=dict)
    goals: tuple[GoalProgress, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    lessons: tuple[LessonRecommendation, ...] = ()
    timeline: tuple[TimelineEntry, ...] = ()
    achievements: tuple[Achievement, ...] = ()
    reports: tuple[Report, ...] = ()
    notes: tuple[str, ...] = ()      # honest caveats about this run

    def metric(self, key: str) -> Metric | None:
        return self.metrics.get(key)

    def value(self, key: str) -> float | None:
        m = self.metrics.get(key)
        return m.value if m else None

    def score(self, key: str) -> ScoreCard | None:
        for s in self.scores:
            if s.key == key:
                return s
        return None

    def behavior(self, behavior_id: str) -> BehaviorFinding | None:
        for b in self.behaviors:
            if b.id == behavior_id:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "generated": self.generated.isoformat(),
            "trades_analyzed": self.trades_analyzed,
            "span_start": self.span_start, "span_end": self.span_end,
            "data_sufficiency": self.data_sufficiency,
            "metrics": {k: m.to_dict() for k, m in self.metrics.items()},
            "periods": {k: [p.to_dict() for p in v]
                        for k, v in self.periods.items()},
            "behaviors": [b.to_dict() for b in self.behaviors],
            "patterns": [p.to_dict() for p in self.patterns],
            "scores": [s.to_dict() for s in self.scores],
            "risk": self.risk,
            "goals": [g.to_dict() for g in self.goals],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "lessons": [x.to_dict() for x in self.lessons],
            "timeline": [t.to_dict() for t in self.timeline],
            "achievements": [a.to_dict() for a in self.achievements],
            "reports": [r.to_dict() for r in self.reports],
            "notes": list(self.notes),
        }


def grade(score: float | None) -> str:
    """The one grading scale in the intelligence layer.

    Deliberately identical to `coach/categories.py::_grade`'s thresholds so a
    category grade and an intelligence grade never disagree on screen. If those
    thresholds ever change, change them in both places in the same commit.
    """
    if score is None:
        return "—"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"
