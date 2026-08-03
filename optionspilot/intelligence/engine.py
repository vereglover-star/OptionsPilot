"""TradingIntelligence — the façade, and the only object anything outside this
package should hold.

Everything above the intelligence layer — the dashboard, the coach, the journal
detail view, the learning tab, the reports, and whatever comes next — reads one
`IntelligenceSnapshot` produced here. That is the whole architectural point of
the milestone: an insight is generated once and reused everywhere, so two
screens can never disagree about the same trader.

Three operational decisions worth understanding before changing anything:

* **Nothing is computed at construction.** Building a `TradingIntelligence`
  touches no data and costs microseconds, so the orchestrator can own one
  unconditionally without lengthening startup. The first analysis happens on the
  first read, or on an explicit background refresh.
* **The cache key is a fingerprint supplied by the caller**, not a timestamp.
  The composition root knows what changed (journal revision, experience store
  revision, review count); this layer does not, and a TTL would either recompute
  needlessly or serve a stale verdict about a trade the user just closed.
* **A failed analysis returns an empty snapshot, never an exception.** This is
  advisory intelligence attached to a trading application. A malformed review
  file must cost the user their dashboard panel, not their session — and
  emphatically not their ability to manage an open position.

Thread safety: `snapshot()` is guarded by a lock so the background refresh and a
UI request can't compute simultaneously and hand back different objects. The
engines below are pure functions of their inputs, so the lock is about wasted
work and cache coherence, not correctness.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from optionspilot.core.logging_setup import get_logger
from optionspilot.intelligence import stats
from optionspilot.intelligence.achievements import AchievementEngine
from optionspilot.intelligence.behavior import BehaviorEngine
from optionspilot.intelligence.confidence import ConfidenceEngine, ScoreInput
from optionspilot.intelligence.curriculum import CurriculumEngine
from optionspilot.intelligence.facts import FactSet, TradeFact
from optionspilot.intelligence.goals import GoalEngine, TEMPLATES, validate
from optionspilot.intelligence.models import (
    Confidence, Evidence, Goal, IntelligenceSnapshot,
)
from optionspilot.intelligence.patterns import DIMENSIONS, PatternEngine, _size_dimension
from optionspilot.intelligence.performance import PerformanceEngine
from optionspilot.intelligence.recommend import RecommendationEngine
from optionspilot.intelligence.reports import ReportEngine
from optionspilot.intelligence.risk import RiskIntelligence
from optionspilot.intelligence.store import IntelligenceStore
from optionspilot.intelligence.timeline import TimelineEngine
from optionspilot.intelligence.windows import RECENT_WINDOW, WINDOWS, resolve

log = get_logger("intelligence")

# Sufficiency bands. The dashboard uses these to decide how loudly to caveat
# everything it shows; the thresholds are the same sample floors the engines
# themselves use, so the caveat and the analysis can never disagree.
SUFFICIENCY = (
    (stats.MIN_SAMPLE_HIGH, "good"),
    (stats.MIN_SAMPLE_MEDIUM, "partial"),
    (stats.MIN_SAMPLE_ANY, "minimal"),
)


def _sufficiency(n: int) -> str:
    for threshold, label in SUFFICIENCY:
        if n >= threshold:
            return label
    return "none"


def empty_snapshot(now: datetime | None = None,
                   notes: tuple[str, ...] = ()) -> IntelligenceSnapshot:
    """A complete, valid snapshot describing a trader with no analyzable
    history. Every consumer can render it without a special case — which is why
    it carries the full metric registry (all values None) rather than an empty
    dict."""
    from optionspilot.intelligence.performance import compute
    return IntelligenceSnapshot(
        generated=now or datetime.now(timezone.utc),
        trades_analyzed=0, data_sufficiency="none",
        metrics=compute([]),
        notes=notes or ("No completed trades yet. Close a round trip and the "
                        "analysis starts immediately.",),
    )


class TradingIntelligence:
    """Owns the pipeline, the goal store and the snapshot cache."""

    def __init__(
        self,
        facts_provider: Callable[[], FactSet],
        *,
        fingerprint_provider: Callable[[], str] | None = None,
        store: IntelligenceStore | None = None,
    ):
        self._facts_provider = facts_provider
        self._fingerprint_provider = fingerprint_provider
        self._store = store
        self._lock = threading.RLock()
        self._cache: IntelligenceSnapshot | None = None
        self._cache_key: str | None = None
        self._last_duration_ms: float = 0.0

        self.performance = PerformanceEngine()
        self.behavior = BehaviorEngine()
        self.patterns = PatternEngine()
        self.risk = RiskIntelligence()
        self.confidence = ConfidenceEngine()
        self.goals = GoalEngine()
        self.curriculum = CurriculumEngine()
        self.timeline = TimelineEngine()
        self.achievements = AchievementEngine()
        self.reports = ReportEngine()

    # ── snapshot lifecycle ───────────────────────────────────────────────

    @property
    def last_duration_ms(self) -> float:
        """How long the most recent analysis took. Surfaced in the API payload
        so a slow snapshot is visible rather than merely felt."""
        return self._last_duration_ms

    def snapshot(self, *, force: bool = False,
                 now: datetime | None = None) -> IntelligenceSnapshot:
        """The current analysis, recomputing only when the fingerprint moved.

        With no `fingerprint_provider` the cache is never reused — correct for
        tests and for any caller that cannot say when its data changed, because
        serving a stale verdict is the one failure mode this cache must not have.
        """
        with self._lock:
            key = self._fingerprint_provider() if self._fingerprint_provider else None
            if (not force and key is not None and key == self._cache_key
                    and self._cache is not None):
                return self._cache
            started = time.perf_counter()
            try:
                snapshot = self.analyze(self._facts_provider(), now=now)
            except Exception as exc:  # noqa: BLE001 — advisory analysis, never fatal
                log.error("intelligence analysis failed: %s", exc, exc_info=True)
                snapshot = empty_snapshot(now, notes=(
                    "The analysis could not be completed from the stored trade "
                    "history. Nothing else is affected; see logs/app.log.",))
                key = None      # never cache a failure as if it were an answer
            self._last_duration_ms = (time.perf_counter() - started) * 1000
            self._cache = snapshot
            self._cache_key = key
            return snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_key = None

    # `refresh_in_background()` used to live here: it started a daemon thread
    # named "intelligence-refresh" and returned it. Removed in V0.9.1-C6.
    #
    # This layer imports `core` only (`tests/test_architecture.py`), so it can
    # never reach `services.runtime` — which means it could start that thread
    # but could not pause it, drain it at shutdown or report it, and the one
    # leak test in the suite matched neither its name nor its prefix. A module
    # that cannot own a thread's lifecycle should not begin one; the owner is
    # the caller. `UIServer.refresh_intelligence()` triggers an on-demand
    # runtime task that calls `snapshot(force=True)` on the worker lane.
    #
    # Nothing in the application had called it, which is why the gap survived
    # three milestones. Warming the cache is still available and still off the
    # request path — it now has a lifecycle owner.

    # ── the pipeline ─────────────────────────────────────────────────────

    def analyze(self, factset: FactSet,
                now: datetime | None = None) -> IntelligenceSnapshot:
        """Run every engine over one fact set. Pure: same facts in, same
        snapshot out (bar `generated`), which is what makes the whole layer
        testable without a database."""
        now = now or datetime.now(timezone.utc)
        facts = tuple(factset.facts) if isinstance(factset, FactSet) else tuple(factset)
        notes = list(factset.notes) if isinstance(factset, FactSet) else []

        if not facts:
            # Still evaluate goals. A user who sets a goal before their first
            # trade must see it listed as "not measurable yet", not silently
            # dropped — a goal that vanishes on save looks like a bug and reads
            # like one.
            snapshot = empty_snapshot(now, notes=tuple(notes) or ())
            empty_windows = {name: snapshot.metrics for name in WINDOWS}
            snapshot.goals = self.goals.evaluate(self._active_goals(),
                                                 empty_windows)
            snapshot.achievements = self.achievements.evaluate(
                (), snapshot.metrics, snapshot.goals)
            return snapshot

        perf = self.performance.analyze(facts)

        # Behaviour and the action plan reflect a recent window, not all of
        # history, so a habit the trader has stopped drops off as newer clean
        # trades push it out. The previous, equally-sized window is the
        # comparison that gives each finding its direction of travel.
        recent_spec = resolve(RECENT_WINDOW)
        recent = tuple(recent_spec.select(facts))
        prior_end = len(facts) - len(recent)
        previous = facts[max(0, prior_end - len(recent)):prior_end]

        behaviors = self.behavior.analyze(recent, previous)
        behavior_map = {b.id: b for b in behaviors}

        patterns = self.patterns.analyze(facts)
        risk = self.risk.analyze(facts, perf.metrics)

        scores = self.confidence.analyze(ScoreInput(
            facts=facts, metrics=perf.metrics, behaviors=behavior_map,
            monthly=perf.periods["month"], trends=perf.trends,
        ))
        score_map = {s.key: s for s in scores}

        goals = self.goals.evaluate(self._active_goals(), perf.windows, perf.trends)
        lessons = self.curriculum.recommend(behavior_map, perf.metrics, score_map)
        recommendations = self.recommendations_for(behaviors, patterns, scores,
                                                   goals, risk)
        timeline = self.timeline.build(facts)
        achievements = self.achievements.evaluate(facts, perf.metrics, goals)

        reports = tuple(
            report for report in (
                self.reports.build(
                    facts=facts, period=period, behaviors=behaviors,
                    patterns=patterns, scores=scores,
                    recommendations=recommendations, lessons=lessons,
                    goals=goals, timeline=timeline, generated=now)
                for period in ("week", "month"))
            if report is not None)

        notes.extend(self._notes(facts, behaviors, patterns))

        return IntelligenceSnapshot(
            generated=now, trades_analyzed=len(facts),
            span_start=facts[0].entry_date, span_end=facts[-1].entry_date,
            data_sufficiency=_sufficiency(len(facts)),
            metrics=perf.metrics, periods=perf.periods,
            behaviors=behaviors, patterns=patterns, scores=scores, risk=risk,
            goals=goals, recommendations=recommendations, lessons=lessons,
            timeline=timeline, achievements=achievements, reports=reports,
            notes=tuple(notes),
        )

    def recommendations_for(self, behaviors, patterns, scores, goals, risk):
        return RecommendationEngine().build(behaviors, patterns, scores, goals, risk)

    @staticmethod
    def _notes(facts: tuple[TradeFact, ...], behaviors, patterns) -> list[str]:
        """Honest caveats about this particular run. These are shown, not
        logged: a user reading a confident dashboard deserves to know what it
        could not see."""
        notes: list[str] = []
        n = len(facts)
        if n < stats.MIN_SAMPLE_MEDIUM:
            notes.append(
                f"Only {n} completed trade(s) analyzed. Nothing here separates "
                f"skill from luck yet — treat every figure as provisional.")
        elif n < stats.MIN_SAMPLE_HIGH:
            notes.append(
                f"{n} completed trades analyzed. Enough for direction, not "
                f"enough for confident conclusions about any single pattern.")
        unassessable = [b for b in behaviors if not b.assessable]
        if unassessable:
            notes.append(
                f"{len(unassessable)} behaviour(s) could not be assessed "
                f"because the data they need was never recorded. They are "
                f"listed with the reason rather than reported as 'not detected'.")
        if not patterns and n >= 15:
            notes.append(
                "No condition reached statistical significance against your own "
                "baseline — your results are, so far, evenly spread rather than "
                "concentrated anywhere.")
        return notes

    # ── goals ────────────────────────────────────────────────────────────

    def _active_goals(self) -> tuple[Goal, ...]:
        if self._store is None:
            return ()
        return tuple(self._store.load_goals())

    def list_goals(self) -> list[Goal]:
        return list(self._active_goals())

    def add_goal(self, goal: Goal) -> Goal:
        """Persist a goal, rejecting one that could never evaluate. Raises
        ValueError with the reason — the caller turns it into a 422 so the user
        finds out at the point they can fix it."""
        if self._store is None:
            raise ValueError("no goal store configured")
        problem = validate(goal)
        if problem:
            raise ValueError(problem)
        goals = [g for g in self._store.load_goals() if g.id != goal.id]
        goals.append(goal)
        self._store.save_goals(goals)
        self.invalidate()
        return goal

    def remove_goal(self, goal_id: str) -> bool:
        if self._store is None:
            return False
        goals = self._store.load_goals()
        remaining = [g for g in goals if g.id != goal_id]
        if len(remaining) == len(goals):
            return False
        self._store.save_goals(remaining)
        self.invalidate()
        return True

    @staticmethod
    def goal_templates() -> tuple[Goal, ...]:
        return TEMPLATES

    # ── per-trade projection (the Journal integration) ────────────────────

    def trade_insight(self, trade_id: str,
                      snapshot: IntelligenceSnapshot | None = None) -> dict:
        """Everything the analysis already knows that is relevant to ONE trade.

        Deliberately a *projection* of the snapshot rather than a fresh
        analysis: the patterns this trade belongs to, the recurring habits it
        contributed to, how its outcome ranks against the trader's history, and
        how the cohort of comparable trades performed. Nothing here recomputes
        anything — a second per-trade analysis pass would be exactly the
        duplicated intelligence this package was built to eliminate.
        """
        snapshot = snapshot or self.snapshot()
        facts = self._facts_provider().facts if snapshot.trades_analyzed else ()
        fact = next((f for f in facts if f.trade_id == trade_id), None)
        if fact is None:
            return {"trade_id": trade_id, "available": False,
                    "reason": "This trade is not in the analyzed history."}

        # Which discovered patterns this trade sits inside.
        dimensions = tuple(_size_dimension(facts) if d.name == "size_bucket" else d
                           for d in DIMENSIONS)
        buckets = {d.name: d.extract(fact) for d in dimensions}
        matched = [p.to_dict() for p in snapshot.patterns
                   if buckets.get(p.dimension) == p.bucket]

        # The habits this trade is evidence for. `detected` is load-bearing:
        # the comparative detectors (tilt, overconfidence) cite their whole
        # cohort as evidence even when the comparison came out clean, so
        # without it a perfectly ordinary trade is listed as evidence for
        # "Tilt after a loss — 0 of 50 trades (0%)".
        contributed = [
            {"id": b.id, "label": b.label, "summary": b.summary,
             "occurrences": b.occurrences, "sample": b.sample,
             "rate": round(b.rate, 4),
             "confidence": b.confidence.name.lower()}
            for b in snapshot.behaviors
            if b.assessable and b.detected
            and any(trade_id in e.trade_ids for e in b.evidence)
        ]

        # Where this result sits in the trader's own distribution.
        pnls = sorted(f.pnl for f in facts)
        rank = sum(1 for p in pnls if p < fact.pnl)
        percentile = round(rank / len(pnls) * 100, 1) if pnls else None

        cohort = [f for f in facts
                  if f.trade_id != trade_id and f.symbol == fact.symbol
                  and f.direction == fact.direction]
        cohort_stats = None
        if len(cohort) >= stats.MIN_SAMPLE_ANY:
            cohort_pnls = [f.pnl for f in cohort]
            cohort_stats = {
                "trades": len(cohort),
                "win_rate": round((stats.win_rate(cohort_pnls) or 0) * 100, 1),
                "expectancy": round(stats.expectancy(cohort_pnls) or 0.0, 2),
                "label": f"your other {fact.symbol} {fact.direction} trades",
            }

        # Recommendations whose evidence names this trade.
        relevant = [r.to_dict() for r in snapshot.recommendations
                    if any(trade_id in e.trade_ids for e in r.evidence)]

        return {
            "trade_id": trade_id, "available": True,
            "percentile": percentile,
            "pnl": round(fact.pnl, 2),
            "process_score": fact.process_score,
            "patterns": matched,
            "behaviors": contributed,
            "cohort": cohort_stats,
            "recommendations": relevant,
            "comparable_ids": [f.trade_id for f in cohort[-10:]],
            "observations": _trade_observations(fact, snapshot),
        }


def _trade_observations(fact: TradeFact,
                        snapshot: IntelligenceSnapshot) -> list[dict]:
    """Plain-language observations about one trade, each one a comparison to a
    figure the snapshot already computed. Never a verdict on the outcome — a
    winning trade taken badly still reads badly here, matching the coach."""
    out: list[dict] = []

    expectancy = snapshot.value("expectancy")
    if expectancy is not None:
        better = fact.pnl > expectancy
        out.append({
            "label": "Against your average trade",
            "detail": f"This trade returned {fact.pnl:+.2f} against your "
                      f"{expectancy:+.2f} average — "
                      f"{'above' if better else 'below'} your own baseline.",
            "kind": "positive" if better else "info",
        })

    typical_hold = snapshot.value("avg_hold_minutes")
    if typical_hold and fact.hold_minutes > 0:
        ratio = fact.hold_minutes / typical_hold
        if ratio >= 1.5 or ratio <= 0.66:
            out.append({
                "label": "Hold time",
                "detail": f"Held {fact.hold_minutes:.0f} minutes, "
                          f"{ratio:.1f}× your typical {typical_hold:.0f}.",
                "kind": "info",
            })

    typical_size = snapshot.value("avg_outlay")
    if typical_size and fact.outlay > 0 and fact.outlay > typical_size * 1.5:
        out.append({
            "label": "Position size",
            "detail": f"Committed {fact.outlay:,.0f}, "
                      f"{fact.outlay / typical_size:.1f}× your typical position.",
            "kind": "warning",
        })

    if fact.had_stop is False:
        out.append({
            "label": "No protective stop",
            "detail": "This trade ran with no resting stop order at any point.",
            "kind": "warning",
        })

    if fact.mistakes:
        out.append({
            "label": "Process mistakes tagged",
            "detail": ", ".join(m.replace("_", " ") for m in fact.mistakes),
            "kind": "warning",
        })
    return out


def build_evidence_index(snapshot: IntelligenceSnapshot) -> dict[str, list[str]]:
    """`trade_id -> [finding labels citing it]`.

    The reverse index behind the journal's "this trade contributed to N
    findings" badge. Built on demand rather than stored on the snapshot,
    because most consumers never need it and it is O(evidence), not O(trades).
    """
    index: dict[str, list[str]] = {}
    sources: list[tuple[str, tuple[Evidence, ...]]] = []
    sources.extend((b.label, b.evidence) for b in snapshot.behaviors
                   if b.assessable and b.detected)
    sources.extend((p.summary.split(":")[0], p.evidence) for p in snapshot.patterns)
    for label, evidence in sources:
        for item in evidence:
            for trade_id in item.trade_ids:
                index.setdefault(trade_id, [])
                if label not in index[trade_id]:
                    index[trade_id].append(label)
    return index


def confidence_of(snapshot: IntelligenceSnapshot) -> Confidence:
    """One overall confidence for the whole snapshot, for the dashboard's
    header caveat. The weakest link across the scores that could be computed."""
    levels = [s.confidence for s in snapshot.scores if s.value is not None]
    if not levels:
        return Confidence.NONE
    return stats.combine_confidence(*levels)
