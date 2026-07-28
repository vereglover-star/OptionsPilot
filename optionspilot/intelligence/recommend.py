"""RecommendationEngine — the ranked answer to "what should I do next?".

The brief for this module was a single sentence: never produce *"you should
manage risk"*, always produce *"you exceeded your planned risk on 9 of your last
17 trades; reducing each by 20% would have improved expectancy by 14%"*.

Structurally, that means a recommendation may only ever be **derived** — every
one is built from a finding that already carries its own evidence, and it
inherits that evidence rather than restating it in prose. There is no list of
generic advice anywhere in this file. If nothing measurable fired, the engine
returns an empty list, and the UI says so.

Ranking is computed, not assigned: `priority` is severity × confidence ×
measured cost, so the item at the top is the one whose evidence is strongest and
whose historical cost was highest — not the one a developer thought was most
important. Ties break on the size of the counterfactual, then on id, so the
order is stable between runs with identical data.
"""

from __future__ import annotations

from optionspilot.intelligence.behavior import BEHAVIORS
from optionspilot.intelligence.curriculum import CURRICULUM
from optionspilot.intelligence.models import (
    BehaviorFinding, Confidence, Evidence, GoalProgress, Impact, Pattern,
    Recommendation, ScoreCard, Severity,
)

MAX_RECOMMENDATIONS = 8

# Severity → base weight. The spread is deliberately wide: a serious habit with
# medium confidence should outrank a minor one with high confidence, because
# the cost of being wrong about the serious one is asymmetric.
_SEVERITY_WEIGHT = {
    Severity.SERIOUS: 4.0, Severity.MODERATE: 2.5, Severity.MINOR: 1.2,
    Severity.INFO: 0.5, Severity.POSITIVE: 0.0,
}

# Behaviors → the lessons that address them, derived from the curriculum's own
# trigger declarations so the two can never disagree about which lesson helps
# with which habit.
_LESSONS_FOR_BEHAVIOR: dict[str, tuple[str, ...]] = {}
for _lesson in CURRICULUM:
    for _behavior in _lesson.behavior_triggers:
        _LESSONS_FOR_BEHAVIOR[_behavior] = \
            _LESSONS_FOR_BEHAVIOR.get(_behavior, ()) + (_lesson.id,)


def _cost_weight(impact: Impact | None) -> float:
    """Turn a measured counterfactual into a ranking multiplier.

    A habit that cost nothing measurable still ranks — it may be a rule
    violation that simply hasn't been punished yet — but it ranks below one that
    demonstrably cost money. Capped at 3× so a single catastrophic trade cannot
    pin one recommendation to the top forever.
    """
    if impact is None or impact.delta is None or impact.baseline in (None, 0):
        return 1.0
    relative = abs(impact.delta) / max(abs(impact.baseline), 1e-9)
    return 1.0 + min(2.0, relative)


class RecommendationEngine:
    """Derives, prices and ranks the action list."""

    def build(self, behaviors: tuple[BehaviorFinding, ...],
              patterns: tuple[Pattern, ...],
              scores: tuple[ScoreCard, ...],
              goals: tuple[GoalProgress, ...],
              risk: dict) -> tuple[Recommendation, ...]:
        out: list[Recommendation] = []
        out.extend(self._from_behaviors(behaviors))
        out.extend(self._from_patterns(patterns))
        out.extend(self._from_goals(goals))
        out.extend(self._from_risk(risk))
        out.extend(self._from_scores(scores, behaviors))

        # One action per underlying problem. A trader told the same thing three
        # ways stops reading the list.
        seen: set[str] = set()
        deduped: list[Recommendation] = []
        for rec in sorted(out, key=lambda r: (-r.priority, r.id)):
            fingerprint = rec.action.strip().lower()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            deduped.append(rec)
        return tuple(deduped[:MAX_RECOMMENDATIONS])

    # ── sources ──────────────────────────────────────────────────────────

    def _from_behaviors(self, behaviors: tuple[BehaviorFinding, ...]
                        ) -> list[Recommendation]:
        out: list[Recommendation] = []
        for finding in behaviors:
            if not finding.assessable or not finding.detected:
                continue
            spec = BEHAVIORS.get(finding.id)
            if spec is None or not spec.action:
                continue
            priority = (_SEVERITY_WEIGHT.get(finding.severity, 1.0)
                        * max(finding.confidence.value, 1)
                        * _cost_weight(finding.impact))
            out.append(Recommendation(
                id=f"behavior:{finding.id}",
                title=finding.label,
                action=spec.action,
                rationale=_rationale(finding),
                priority=priority, confidence=finding.confidence,
                severity=finding.severity, source="behavior",
                evidence=finding.evidence, impact=finding.impact,
                lesson_ids=_LESSONS_FOR_BEHAVIOR.get(finding.id, ()),
            ))
        return out

    def _from_patterns(self, patterns: tuple[Pattern, ...]
                       ) -> list[Recommendation]:
        """Patterns generate two kinds of advice: stop doing the thing that
        loses, and do more of the thing that wins. The second is not filler —
        a trader who knows where their edge lives can concentrate on it, and
        this is the only part of the system that tells them."""
        out: list[Recommendation] = []
        for pattern in patterns:
            if pattern.confidence < Confidence.MEDIUM:
                continue
            if pattern.kind == "weakness":
                action = (f"Stop taking {pattern.dimension_label.lower()} = "
                          f"{pattern.bucket} trades until you can explain why "
                          f"they lose. Paper-trade or replay them instead.")
                severity = Severity.MODERATE
                title = f"Your weakest condition: {pattern.bucket}"
            else:
                action = (f"Concentrate more of your risk on "
                          f"{pattern.dimension_label.lower()} = {pattern.bucket} "
                          f"setups — that is where your edge is measurable.")
                severity = Severity.POSITIVE
                title = f"Your strongest condition: {pattern.bucket}"
            out.append(Recommendation(
                id=f"pattern:{pattern.dimension}:{pattern.bucket}",
                title=title, action=action,
                rationale=pattern.summary,
                priority=(2.0 if pattern.kind == "weakness" else 1.2)
                * pattern.confidence.value * (1 + abs(pattern.edge)),
                confidence=pattern.confidence, severity=severity,
                source="pattern", evidence=pattern.evidence,
            ))
        return out

    def _from_goals(self, goals: tuple[GoalProgress, ...]
                    ) -> list[Recommendation]:
        out: list[Recommendation] = []
        for progress in goals:
            if progress.met or progress.current is None:
                continue
            # Only the goals within reach generate an action. Telling someone
            # they are 8% of the way to a goal is not advice, it is a scoreboard.
            if progress.progress < 0.5:
                continue
            out.append(Recommendation(
                id=f"goal:{progress.goal.id}",
                title=f"Nearly there: {progress.goal.label}",
                action=f"You are {progress.progress:.0%} of the way to "
                       f"'{progress.goal.label}'. {progress.detail}",
                rationale=progress.detail,
                priority=1.0 + progress.progress,
                confidence=Confidence.MEDIUM if progress.sample >= 12
                else Confidence.LOW,
                severity=Severity.INFO, source="goal",
                evidence=(Evidence(progress.goal.label, progress.current,
                                   progress.sample, progress.detail),),
            ))
        return out

    def _from_risk(self, risk: dict) -> list[Recommendation]:
        out: list[Recommendation] = []
        if not risk.get("assessable"):
            return out
        for observation in risk.get("observations", []):
            severity = Severity(observation.get("severity", "info"))
            if severity.rank < Severity.MODERATE.rank:
                continue
            action = _RISK_ACTIONS.get(observation.get("key", ""))
            if not action:
                continue
            evidence = tuple(
                Evidence(label=e.get("label", ""), value=e.get("value"),
                         sample=e.get("sample", 0), detail=e.get("detail", ""))
                for e in observation.get("evidence", []))
            out.append(Recommendation(
                id=f"risk:{observation['key']}",
                title=observation["headline"].split("—")[0].strip()[:80],
                action=action, rationale=observation["headline"],
                priority=_SEVERITY_WEIGHT.get(severity, 1.0) * 1.5,
                confidence=Confidence.MEDIUM, severity=severity,
                source="risk", evidence=evidence,
            ))
        return out

    def _from_scores(self, scores: tuple[ScoreCard, ...],
                     behaviors: tuple[BehaviorFinding, ...]
                     ) -> list[Recommendation]:
        """A weak composite score only earns a recommendation when no behavior
        already explains it. Otherwise the list says "your discipline is 48"
        directly under "you widened a stop on 6 of 40 trades", which is the same
        advice twice, once vaguely."""
        explained = {b.id for b in behaviors if b.assessable and b.detected}
        out: list[Recommendation] = []
        for card in scores:
            if card.value is None or card.value >= 55 or not card.components:
                continue
            if card.confidence < Confidence.MEDIUM:
                continue
            if explained:
                continue
            measured = [c for c in card.components if c.value is not None]
            if not measured:
                continue
            weakest = min(measured, key=lambda c: c.value)
            out.append(Recommendation(
                id=f"score:{card.key}",
                title=f"{card.label} is {card.value:.0f}/100",
                action=f"Focus on {weakest.label.lower()} — it is the weakest "
                       f"input to your {card.label.lower()} score at "
                       f"{weakest.value:.0f}/100.",
                rationale=card.explanation,
                priority=1.5 + (55 - card.value) / 55,
                confidence=card.confidence, severity=Severity.MODERATE,
                source="score",
                evidence=(Evidence(weakest.label, weakest.value, card.sample,
                                   weakest.detail),),
            ))
        return out


_RISK_ACTIONS: dict[str, str] = {
    "drawdown": "Cut position size until you are back at your high-water mark. "
                "Recovering a drawdown at the size that caused it is how a "
                "drawdown becomes a hole.",
    "tail_loss": "Find what your worst losses have in common — usually a "
                 "missing stop or an outsized position — and put a hard rule "
                 "in front of it.",
    "bad_days": "Set a daily loss limit and stop for the day when it is hit. "
                "Your outsized losing days, not your typical ones, are what "
                "sets your drawdown.",
    "sizing_dispersion": "Fix one position size and hold it for the next 20 "
                         "trades. Vary selectivity, not size.",
    "stop_coverage": "Place a resting protective stop within 60 seconds of "
                     "every fill, before doing anything else.",
    "concentration": "Spread risk across more than one instrument, or accept "
                     "explicitly that your record is a bet on one name.",
}


def _rationale(finding: BehaviorFinding) -> str:
    """The sentence that has to carry the numbers. Impact is appended when it
    exists, phrased as the historical counterfactual it actually is."""
    base = finding.summary
    if finding.impact and finding.impact.delta is not None:
        impact = finding.impact
        direction = "improved" if impact.delta > 0 else "reduced"
        pct = impact.pct_change
        change = f" ({pct:+.0f}%)" if pct is not None else ""
        base += (f" Removing those trades from your history {direction} "
                 f"expectancy from {impact.baseline:+.2f} to "
                 f"{impact.adjusted:+.2f}{change} — measured over the trades "
                 f"you actually took, not a forecast.")
    return base
