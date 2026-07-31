"""AchievementEngine — markers of durable habits, not participation points.

The brief was explicit: *not gamification for its own sake*. Every achievement
here obeys one rule — **it cannot be earned by a single trade, or by luck.**
Each requires either a sustained streak, a measured improvement between two
comparable periods, or a threshold held across a minimum sample. There is no
"first trade" badge, and nothing rewards volume.

The second rule is that achievements reward *process*, consistent with the
coach's philosophy: a disciplined loser outscores a reckless winner. Two
outcome-flavoured achievements exist (profit factor, positive expectancy) and
both require a sample large enough that they cannot be a hot streak.

Earned state is derived on every read, never stored. That is the same decision
`monitor.health_state()` made in V0.5.3 and for the same reason: a stored badge
and a live calculation are two objects tracking one fact, and they will drift —
here, in the direction of a trader keeping an achievement their record no longer
supports, which is worse than never awarding it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import Achievement, GoalProgress, Metric


@dataclass(frozen=True, slots=True)
class _Spec:
    id: str
    title: str
    description: str
    tier: str
    # Returns (progress 0..1, detail, earned_on date or "").
    evaluate: Callable[["_Data"], tuple[float, str, str]]


@dataclass(frozen=True, slots=True)
class _Data:
    facts: tuple[TradeFact, ...]
    metrics: dict[str, Metric]
    goals: tuple[GoalProgress, ...]

    def value(self, key: str) -> float | None:
        m = self.metrics.get(key)
        return m.value if m else None

    def sample(self, key: str) -> int:
        m = self.metrics.get(key)
        return m.sample if m else 0


def _streak(facts: tuple[TradeFact, ...],
            ok: Callable[[TradeFact], bool],
            eligible: Callable[[TradeFact], bool]) -> tuple[int, str]:
    """Length of the current run of `ok`, counted only over trades where
    `eligible` is true — a trade that could not have broken the streak must not
    be allowed to extend it either."""
    considered = [f for f in facts if eligible(f)]
    run = 0
    for fact in reversed(considered):
        if ok(fact):
            run += 1
        else:
            break
    date = considered[-1].entry_date if considered and run else ""
    return run, date


def _threshold(value: float | None, target: float, sample: int,
               min_sample: int, detail_fmt: str) -> tuple[float, str, str]:
    if value is None or sample < min_sample:
        have = 0.0 if value is None else stats.clamp01(value / target) if target else 0.0
        return (have * 0.5,
                f"Needs at least {min_sample} qualifying trades; you have {sample}.",
                "")
    progress = stats.clamp01(value / target) if target > 0 else (1.0 if value >= target else 0.0)
    return progress, detail_fmt.format(value=value, sample=sample), ""


def _stop_discipline_streak(d: _Data) -> tuple[float, str, str]:
    run, date = _streak(d.facts,
                        ok=lambda f: bool(f.had_stop) and not f.widened_stop,
                        eligible=lambda f: f.had_stop is not None)
    return (stats.clamp01(run / 25),
            f"{run} consecutive reviewed trades protected by a resting stop "
            f"that was never widened (25 needed).",
            date if run >= 25 else "")


def _clean_streak(d: _Data) -> tuple[float, str, str]:
    run, date = _streak(d.facts, ok=lambda f: not f.mistakes,
                        eligible=lambda f: f.reviewed)
    return (stats.clamp01(run / 15),
            f"{run} consecutive reviewed trades with no process mistake "
            f"(15 needed).",
            date if run >= 15 else "")


def _plan_streak(d: _Data) -> tuple[float, str, str]:
    run, date = _streak(d.facts,
                        ok=lambda f: bool(f.had_stop) and bool(f.had_target),
                        eligible=lambda f: f.had_stop is not None
                        and f.had_target is not None)
    return (stats.clamp01(run / 20),
            f"{run} consecutive trades entered with both a stop and a target "
            f"(20 needed).",
            date if run >= 20 else "")


def _size_discipline(d: _Data) -> tuple[float, str, str]:
    value, sample = d.value("size_consistency"), d.sample("size_consistency")
    if value is None or sample < 30:
        return (0.0, f"Needs 30 sized trades; you have {sample}.", "")
    return (stats.clamp01(value / 80),
            f"Sizing consistency {value:.0f}/100 across {sample} trades "
            f"(80 needed).",
            d.facts[-1].entry_date if value >= 80 else "")


def _consistency_badge(d: _Data) -> tuple[float, str, str]:
    value, sample = d.value("consistency"), d.sample("consistency")
    if value is None or sample < 30:
        return (0.0, f"Needs 30 trades; you have {sample}.", "")
    return (stats.clamp01(value / 65),
            f"Result consistency {value:.0f}/100 across {sample} trades "
            f"(65 needed).",
            d.facts[-1].entry_date if value >= 65 else "")


def _profit_factor(d: _Data) -> tuple[float, str, str]:
    value, sample = d.value("profit_factor"), d.sample("profit_factor")
    if value is None or sample < 50:
        return (0.0, f"Needs 50 trades to mean anything; you have {sample}.", "")
    return (stats.clamp01(value / 1.5),
            f"Profit factor {value:.2f} across {sample} trades (1.5 needed).",
            d.facts[-1].entry_date if value >= 1.5 else "")


def _expectancy(d: _Data) -> tuple[float, str, str]:
    value, sample = d.value("expectancy"), d.sample("expectancy")
    if value is None or sample < 50:
        return (0.0, f"Needs 50 trades; you have {sample}.", "")
    earned = value > 0
    return (1.0 if earned else 0.0,
            f"Expectancy {value:+.2f} per trade across {sample} trades.",
            d.facts[-1].entry_date if earned else "")


def _no_revenge(d: _Data) -> tuple[float, str, str]:
    """Fifty consecutive trades, none of them entered within fifteen minutes of
    a losing exit. Computed here rather than read off the behavior finding
    because an achievement is about a *run*, not a rate."""
    if len(d.facts) < 50:
        return (stats.clamp01(len(d.facts) / 50),
                f"Needs 50 trades; you have {len(d.facts)}.", "")
    recent = d.facts[-50:]
    loss_exits = sorted(f.exit_ts for f in d.facts if f.pnl < 0)
    import bisect
    breaches = 0
    for fact in recent:
        idx = bisect.bisect_left(loss_exits, fact.entry_ts)
        if idx > 0:
            gap = (fact.entry_ts - loss_exits[idx - 1]).total_seconds() / 60
            if 0 <= gap <= 15:
                breaches += 1
    earned = breaches == 0
    return (1.0 if earned else stats.clamp01(1 - breaches / 5),
            f"{breaches} of your last 50 entries came within 15 minutes of a "
            f"losing exit (0 needed).",
            recent[-1].entry_date if earned else "")


def _improvement(d: _Data) -> tuple[float, str, str]:
    """Measurable improvement between the first and second half of a history
    long enough for the halves to be comparable."""
    if len(d.facts) < 40:
        return (stats.clamp01(len(d.facts) / 40),
                f"Needs 40 trades to compare two halves; you have {len(d.facts)}.",
                "")
    half = len(d.facts) // 2
    early = stats.expectancy([f.pnl for f in d.facts[:half]])
    late = stats.expectancy([f.pnl for f in d.facts[half:]])
    if early is None or late is None:
        return (0.0, "Not computable.", "")
    improved = late > early
    change = stats.pct_change(early, late)
    return (1.0 if improved else 0.0,
            f"Expectancy moved from {early:+.2f} over your first {half} trades "
            f"to {late:+.2f} over your last {len(d.facts) - half}"
            + (f" ({change:+.0f}%)." if change is not None else "."),
            d.facts[-1].entry_date if improved else "")


def _goal_keeper(d: _Data) -> tuple[float, str, str]:
    active = [g for g in d.goals if g.goal.active]
    if not active:
        return (0.0, "Set at least one goal to work toward this.", "")
    met = sum(1 for g in active if g.met)
    return (stats.clamp01(met / max(3, 1)),
            f"{met} of {len(active)} active goals currently met (3 needed).",
            d.facts[-1].entry_date if met >= 3 and d.facts else "")


SPECS: tuple[_Spec, ...] = (
    _Spec("stop_discipline_25", "Stop Keeper",
          "25 consecutive trades protected by a resting stop that was never "
          "widened.", "gold", _stop_discipline_streak),
    _Spec("clean_15", "Clean Run",
          "15 consecutive reviewed trades with no process mistake.",
          "gold", _clean_streak),
    _Spec("planned_20", "Plan First",
          "20 consecutive trades entered with both a stop and a target defined.",
          "silver", _plan_streak),
    _Spec("size_discipline", "Steady Hand",
          "Position sizing consistency above 80 across at least 30 trades.",
          "silver", _size_discipline),
    _Spec("consistency_65", "Metronome",
          "Result consistency above 65 across at least 30 trades.",
          "silver", _consistency_badge),
    _Spec("no_revenge_50", "Cool Head",
          "50 consecutive trades with no entry inside 15 minutes of a losing "
          "exit.", "gold", _no_revenge),
    _Spec("improving", "Getting Better",
          "Expectancy in the second half of your history above the first half, "
          "over at least 40 trades.", "bronze", _improvement),
    _Spec("profit_factor_15", "Durable Edge",
          "Profit factor of 1.5 or better across at least 50 trades.",
          "gold", _profit_factor),
    _Spec("positive_expectancy", "In The Black",
          "Positive expectancy across at least 50 trades.", "silver", _expectancy),
    _Spec("goal_keeper", "Goal Keeper",
          "Three active goals met at the same time.", "bronze", _goal_keeper),
)


class AchievementEngine:
    """Derives every achievement's state on each read."""

    def evaluate(self, facts: tuple[TradeFact, ...] | list[TradeFact],
                 metrics: dict[str, Metric],
                 goals: tuple[GoalProgress, ...] = ()) -> tuple[Achievement, ...]:
        data = _Data(facts=tuple(facts), metrics=metrics, goals=tuple(goals))
        out: list[Achievement] = []
        for spec in SPECS:
            progress, detail, earned_on = spec.evaluate(data)
            out.append(Achievement(
                id=spec.id, title=spec.title, description=spec.description,
                earned=bool(earned_on), earned_on=earned_on,
                progress=stats.clamp01(progress), detail=detail, tier=spec.tier,
            ))
        # Earned first (newest first within that), then by how close the rest are.
        out.sort(key=lambda a: (not a.earned, -a.progress, a.id))
        return tuple(out)
