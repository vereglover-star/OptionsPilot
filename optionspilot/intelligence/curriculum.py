"""CurriculumEngine — turning measured weaknesses into targeted education.

This is the milestone's "Learning Engine". It is named `CurriculumEngine` and
lives in `curriculum.py` because `optionspilot.learning.LearningEngine` already
exists and does something completely different (it tunes the decision engine's
evidence weights from journal outcomes). Two classes with one name, one tuning a
scorer and one recommending lessons, is a debugging trap nobody needs.

The design constraint that shapes everything here: **a lesson is only ever
recommended because a number fired.** Every `Lesson` declares its triggers as
data — behaviors it responds to, metric thresholds it watches, scores it backs
up — and `recommend()` walks those triggers against the analysis. There is no
default reading list, no "beginners start here", and no way for a lesson to
appear without the evidence that summoned it. That is what makes the Learning
tab able to answer, for every item on it: *why this lesson, which statistic
triggered it, what problem it solves, and what it is worth.*

The catalogue is deliberately compact. Sixteen lessons that each map to a
measurable failure beat sixty that don't, and an item nobody can be triggered
into seeing is dead weight in a payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from optionspilot.intelligence import stats
from optionspilot.intelligence.models import (
    BehaviorFinding, Confidence, Evidence, Impact, LessonRecommendation, Metric,
    ScoreCard, Severity,
)


@dataclass(frozen=True, slots=True)
class Lesson:
    """One curriculum item plus the conditions that summon it.

    `metric_triggers` are `(metric key, comparator, threshold)` and fire when the
    comparison is TRUE — `("stop_discipline_rate", "<", 80.0)` fires when stop
    discipline has fallen below 80%. `score_triggers` are `(score key,
    threshold)` and fire when the composite score is below the threshold.
    """

    id: str
    title: str
    topic: str
    problem: str                # what it fixes, in the trader's terms
    teaches: str                # what it actually covers
    minutes: int = 10
    behavior_triggers: tuple[str, ...] = ()
    metric_triggers: tuple[tuple[str, str, float], ...] = ()
    score_triggers: tuple[tuple[str, float], ...] = ()
    prerequisites: tuple[str, ...] = field(default_factory=tuple)


CURRICULUM: tuple[Lesson, ...] = (
    Lesson(
        "stops_that_hold", "Stops that actually protect you", "Risk management",
        problem="Losses that grow past the point you decided to be wrong.",
        teaches="Placing a resting protective order before anything else, "
                "sizing the stop from structure rather than from a dollar "
                "figure you are willing to lose, and why a stop that moves away "
                "from price converts a planned loss into an unplanned one.",
        minutes=12,
        behavior_triggers=("trading_without_stops", "moving_stops",
                           "letting_losers_run"),
        metric_triggers=(("stop_discipline_rate", "<", 85.0),),
        score_triggers=(("risk_control", 70.0),),
    ),
    Lesson(
        "position_sizing", "Sizing so one trade can't undo ten", "Risk management",
        problem="A single oversized position erasing a month of disciplined work.",
        teaches="Fixed-fractional sizing, why premium outlay is the wrong "
                "risk unit on its own, and how to keep size constant when "
                "conviction isn't.",
        minutes=10,
        behavior_triggers=("oversizing", "inconsistent_sizing",
                           "overconfidence_after_wins"),
        metric_triggers=(("size_consistency", "<", 65.0),),
    ),
    Lesson(
        "r_multiples", "Thinking in R instead of dollars", "Risk management",
        problem="No common unit for comparing a good trade to a lucky one.",
        teaches="Expressing every result as a multiple of the risk you planned, "
                "why that makes wins and losses comparable across position "
                "sizes, and how it exposes a payoff ratio that dollars hide.",
        minutes=8,
        metric_triggers=(("avg_r", "<", 1.0), ("payoff_ratio", "<", 1.2)),
    ),
    Lesson(
        "expectancy", "Expectancy: the only number that settles the argument",
        "Performance",
        problem="Judging your trading by win rate, which can be high and still "
                "lose money.",
        teaches="How win rate and payoff combine into expectancy, why a 35% "
                "win rate can outperform a 70% one, and what sample size it "
                "takes before your expectancy means anything.",
        minutes=12,
        metric_triggers=(("expectancy", "<", 0.0), ("profit_factor", "<", 1.2)),
    ),
    Lesson(
        "theta", "Theta: paying rent on melting ice", "Options greeks",
        problem="Short-dated contracts decaying faster than your thesis resolves.",
        teaches="How time decay accelerates into expiry, why sub-week expiries "
                "are intraday instruments, and choosing an expiry from how long "
                "your setup needs rather than from its price.",
        minutes=14,
        behavior_triggers=("theta_neglect",),
    ),
    Lesson(
        "delta_strikes", "Choosing a strike that can actually pay", "Options greeks",
        problem="Far-OTM contracts that need a tail event just to break even.",
        teaches="Reading delta as an approximate probability, why 0.30–0.60 is "
                "where an ordinary favourable move gets paid, and the real cost "
                "of the cheap contract.",
        minutes=10,
        behavior_triggers=("lottery_tickets",),
    ),
    Lesson(
        "implied_volatility", "Buying volatility at the wrong price",
        "Options greeks",
        problem="Being right on direction and still losing to a volatility crush.",
        teaches="Implied volatility as the price of the option rather than a "
                "forecast, what happens to premium after an event, and checking "
                "IV before committing to a long option.",
        minutes=14,
        behavior_triggers=("iv_neglect",),
    ),
    Lesson(
        "confirmation", "Waiting for the setup to finish", "Entries",
        problem="Anticipating a setup and being wrong before it existed.",
        teaches="What confirmation looks like — a break of structure, a volume "
                "push, a completed retest — and why anticipating saves pennies "
                "and costs dollars.",
        minutes=10,
        behavior_triggers=("entering_too_early", "ignoring_the_plan"),
        score_triggers=(("decision_quality", 65.0),),
    ),
    Lesson(
        "chasing", "The pullback, not the breakout candle", "Entries",
        problem="Buying the worst price of the move with the widest stop.",
        teaches="Why an extended entry pairs the highest cost with the largest "
                "invalidation distance, how to plan the retest entry in "
                "advance, and how to let a missed move go.",
        minutes=10,
        behavior_triggers=("chasing", "fomo_entries"),
    ),
    Lesson(
        "opening_range", "The first fifteen minutes", "Timing",
        problem="Being stopped out by fake breakouts before the session settles.",
        teaches="What the opening auction does to price, why the open prints "
                "failed moves in both directions, and how to use the opening "
                "range instead of trading inside it.",
        minutes=8,
        behavior_triggers=("open_chop_trading",),
    ),
    Lesson(
        "exits", "Scaling out, and letting the rest run", "Exits",
        problem="Winners cut short while losers are given room.",
        teaches="Banking part of the position at the first target, trailing the "
                "remainder, and why the asymmetry between your winner and loser "
                "hold times is the whole game.",
        minutes=12,
        behavior_triggers=("cutting_winners_early", "letting_losers_run"),
        metric_triggers=(("hold_asymmetry", "<", 1.0),),
    ),
    Lesson(
        "trend_alignment", "Trading with the current, not against it", "Analysis",
        problem="Counter-trend entries that need to be perfect to survive.",
        teaches="Reading the higher-timeframe trend as the dominant force, what "
                "a counter-trend trade has to earn before it is worth taking, "
                "and why those trades need faster exits.",
        minutes=10,
        behavior_triggers=("counter_trend_trading",),
    ),
    Lesson(
        "tilt", "The trade after the loss", "Psychology",
        problem="One loss becoming three because the next entry was a reaction.",
        teaches="Why the trade taken in frustration is statistically the worst "
                "of the day, enforcing a cooling-off timer, and separating the "
                "decision to trade from the urge to be right.",
        minutes=12,
        behavior_triggers=("revenge_trading", "tilt_after_loss"),
    ),
    Lesson(
        "overtrading", "Fewer, better trades", "Psychology",
        problem="Volume spikes on days when nothing was actually set up.",
        teaches="Recognising the boredom trade, setting a daily entry budget, "
                "and why activity feels like progress while costing money.",
        minutes=10,
        behavior_triggers=("overtrading",),
    ),
    Lesson(
        "sample_size", "How many trades before you know anything", "Performance",
        problem="Rewriting a strategy after a bad week, or trusting a good one.",
        teaches="Why twenty trades cannot separate skill from luck, how "
                "confidence intervals widen at small samples, and what to "
                "change between now and then.",
        minutes=12,
        score_triggers=(("consistency", 55.0), ("learning", 55.0)),
    ),
    Lesson(
        "journaling", "Writing the entry down before you take it", "Process",
        problem="No record of what you intended, so no way to know if you "
                "followed it.",
        teaches="Capturing the thesis, the invalidation and the target before "
                "entry, and reviewing against what you wrote rather than "
                "against the outcome.",
        minutes=8,
        metric_triggers=(("plan_rate", "<", 70.0),),
        score_triggers=(("planning", 65.0),),
    ),
)

CURRICULUM_BY_ID = {lesson.id: lesson for lesson in CURRICULUM}

_COMPARATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}

# Weight a trigger contributes to a lesson's priority, by how it fired.
_BEHAVIOR_WEIGHT = {Severity.SERIOUS: 3.0, Severity.MODERATE: 2.0,
                    Severity.MINOR: 1.0, Severity.INFO: 0.5,
                    Severity.POSITIVE: 0.0}
MAX_LESSONS = 6


class CurriculumEngine:
    """Selects the lessons a trader's own numbers have earned them."""

    def recommend(self, behaviors: dict[str, BehaviorFinding],
                  metrics: dict[str, Metric],
                  scores: dict[str, ScoreCard],
                  ) -> tuple[LessonRecommendation, ...]:
        out: list[LessonRecommendation] = []

        for lesson in CURRICULUM:
            triggers: list[Evidence] = []
            priority = 0.0
            confidences: list[Confidence] = []
            impact: Impact | None = None

            for behavior_id in lesson.behavior_triggers:
                finding = behaviors.get(behavior_id)
                if finding is None or not finding.assessable or not finding.detected:
                    continue
                triggers.append(Evidence(
                    label=finding.label, value=finding.occurrences,
                    sample=finding.sample,
                    # The summary already opens with "N of M trades …", so the
                    # count is not repeated here; it rides in value/sample.
                    detail=finding.summary,
                    trade_ids=finding.evidence[0].trade_ids if finding.evidence else (),
                ))
                priority += _BEHAVIOR_WEIGHT.get(finding.severity, 1.0) \
                    * (finding.confidence.value / 3.0 or 0.33)
                confidences.append(finding.confidence)
                # The lesson's impact figure is "what fixing this would have
                # been worth", so only a trigger whose counterfactual IMPROVES
                # expectancy qualifies. A behaviour whose trades happened to be
                # profitable still triggers the lesson — it is still a risk —
                # but attaching its negative delta would print a number that
                # argues against the recommendation carrying it.
                delta = finding.impact.delta if finding.impact else None
                if delta is not None and delta > 0 and (
                        impact is None or delta > (impact.delta or 0)):
                    impact = finding.impact

            for key, comparator, threshold in lesson.metric_triggers:
                metric = metrics.get(key)
                if metric is None or metric.value is None:
                    continue
                if metric.sample < stats.MIN_SAMPLE_LOW:
                    continue
                if not _COMPARATORS[comparator](metric.value, threshold):
                    continue
                triggers.append(Evidence(
                    label=metric.label, value=metric.value, sample=metric.sample,
                    detail=f"{metric.label} is {_fmt(metric)} against a "
                           f"{comparator} {threshold:g} threshold, measured over "
                           f"{metric.sample} trades."))
                priority += 1.5
                confidences.append(stats.sample_confidence(metric.sample))

            for key, threshold in lesson.score_triggers:
                card = scores.get(key)
                if card is None or card.value is None or card.value >= threshold:
                    continue
                # A score the engine itself could not stand behind must not
                # summon a lesson. Without this, a three-trade history produces
                # a full reading list off scores computed from three trades —
                # exactly the confident nonsense the sample floors exist to
                # prevent, arriving through the one trigger type that has no
                # sample of its own to check.
                if card.confidence is Confidence.NONE:
                    continue
                triggers.append(Evidence(
                    label=f"{card.label} score", value=card.value,
                    sample=card.sample,
                    detail=f"{card.label} is {card.value:.0f}/100 "
                           f"(grade {card.grade}) — {card.explanation}"))
                priority += 1.0
                confidences.append(card.confidence)

            if not triggers:
                continue
            # Belt and braces on the same rule: whatever the trigger mix, a
            # recommendation the system cannot stand behind is not made.
            confidence = (stats.combine_confidence(*confidences)
                          if confidences else Confidence.LOW)
            if confidence is Confidence.NONE:
                continue

            out.append(LessonRecommendation(
                lesson_id=lesson.id, title=lesson.title, topic=lesson.topic,
                why=_why(lesson, triggers),
                triggered_by=tuple(triggers),
                problem=lesson.problem, priority=priority,
                confidence=confidence,
                impact=impact, minutes=lesson.minutes,
            ))

        out.sort(key=lambda x: (-x.priority, -x.confidence.value, x.lesson_id))
        return tuple(out[:MAX_LESSONS])


def _why(lesson: Lesson, triggers: list[Evidence]) -> str:
    """The one-sentence answer to "why is this here", built from the strongest
    trigger rather than from the lesson's own description — the trader needs to
    see their own data, not a course blurb."""
    lead = triggers[0]
    more = (f" (and {len(triggers) - 1} other signal"
            f"{'s' if len(triggers) > 2 else ''})" if len(triggers) > 1 else "")
    return (f"Recommended because {lead.detail.rstrip('.')}{more}. "
            f"It covers {lesson.teaches[0].lower()}{lesson.teaches[1:]}")


def _fmt(metric: Metric) -> str:
    if metric.value is None:
        return "—"
    if metric.unit == "%":
        return f"{metric.value:.0f}%"
    if metric.unit == "$":
        return f"${metric.value:,.2f}"
    if metric.unit == "R":
        return f"{metric.value:.2f}R"
    if metric.unit == "min":
        return f"{metric.value:.0f} min"
    return f"{metric.value:g}"
