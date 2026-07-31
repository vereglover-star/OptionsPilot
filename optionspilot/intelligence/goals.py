"""GoalEngine — measurable commitments, scored automatically.

A goal here is deliberately not free text. It is `(metric key, comparator,
target, window)`, which buys three things a "write your intention in a box"
design cannot:

* **Progress is computed, never self-reported.** The trader cannot mark
  "followed my stop rules" complete; `stop_discipline_rate` over the last fifty
  trades either reads 95 or it does not.
* **A goal cannot reference something the system doesn't measure.** Validation
  rejects an unknown metric key at creation, so there is no such thing as a
  goal that silently never evaluates.
* **The window is part of the commitment.** "Average R above 2" means nothing
  without saying over what — lifetime R above 2 is a different (and much
  harder) promise than R above 2 over the last twenty trades.

`TEMPLATES` ships a starting set covering the commitments most worth making.
They are suggestions, not defaults: nothing is activated on a user's behalf.
"""

from __future__ import annotations

from optionspilot.intelligence import stats
from optionspilot.intelligence.models import Goal, GoalProgress, Metric, Trend
from optionspilot.intelligence.performance import METRIC_SPECS
from optionspilot.intelligence.windows import WINDOWS

# Suggested goals, keyed by id. Each is a real commitment expressed against a
# real metric — no vanity targets, and nothing that can be satisfied by trading
# less rather than trading better.
TEMPLATES: tuple[Goal, ...] = (
    Goal("avg_r_above_2", "Keep average R above 2.0",
         "avg_r", ">=", 2.0, "last_20_trades", "R"),
    Goal("daily_loss_cap", "Never lose more than $500 in a day",
         "worst_day_pnl", ">=", -500.0, "last_30d", "$"),
    Goal("stop_discipline_95", "Follow the stop-loss rule 95% of the time",
         "stop_discipline_rate", ">=", 95.0, "last_50_trades", "%"),
    Goal("shorter_holds", "Bring average hold time under two hours",
         "avg_hold_minutes", "<=", 120.0, "last_20_trades", "min"),
    Goal("consistency_60", "Raise the consistency score above 60",
         "consistency", ">=", 60.0, "last_50_trades"),
    Goal("fewer_mistakes", "Average under half a process mistake per trade",
         "mistake_rate", "<=", 0.5, "last_20_trades"),
    Goal("positive_expectancy", "Keep expectancy positive over 30 days",
         "expectancy", ">=", 0.0, "last_30d", "$"),
    Goal("profit_factor_15", "Reach a profit factor of 1.5",
         "profit_factor", ">=", 1.5, "lifetime"),
    Goal("plan_every_trade", "Enter 90% of trades with a stop and a target",
         "plan_rate", ">=", 90.0, "last_20_trades", "%"),
    Goal("clean_trades_70", "Keep 70% of trades free of process mistakes",
         "clean_trade_rate", ">=", 70.0, "last_50_trades", "%"),
    Goal("size_discipline", "Keep sizing consistency above 70",
         "size_consistency", ">=", 70.0, "last_50_trades"),
)

TEMPLATES_BY_ID = {g.id: g for g in TEMPLATES}


def validate(goal: Goal) -> str | None:
    """Reject a goal that could never evaluate. Returns the reason, or None when
    the goal is sound. Called at creation so a bad goal fails loudly at the
    point the user can fix it, rather than quietly reading 'no data' forever."""
    if goal.metric not in METRIC_SPECS:
        return (f"unknown metric {goal.metric!r} — goals must target a metric "
                f"the system measures")
    if goal.comparator not in (">=", "<="):
        return f"comparator must be '>=' or '<=', not {goal.comparator!r}"
    if goal.window not in WINDOWS:
        return f"unknown window {goal.window!r}"
    return None


def _met(current: float | None, comparator: str, target: float) -> bool:
    if current is None:
        return False
    return current >= target if comparator == ">=" else current <= target


def _progress(current: float | None, comparator: str, target: float) -> float:
    """Fraction of the way to the target, 0..1.

    Progress on a goal with a non-positive target (expectancy ≥ 0, "lose no more
    than $500") cannot be a simple ratio, so distance is measured against the
    target's own magnitude instead. The result is always clamped and always 1.0
    once the goal is met — a goal cannot read 140% complete.
    """
    if current is None:
        return 0.0
    if _met(current, comparator, target):
        return 1.0
    scale = max(abs(target), 1.0)
    if comparator == ">=":
        if target > 0:
            return stats.clamp01(current / target)
        return stats.clamp01(1 - (target - current) / scale)
    if target > 0 and current > 0:
        return stats.clamp01(target / current)
    return stats.clamp01(1 - (current - target) / scale)


class GoalEngine:
    """Evaluates goals against the per-window metric registries."""

    def evaluate(self, goals: tuple[Goal, ...] | list[Goal],
                 window_metrics: dict[str, dict[str, Metric]],
                 trends: dict[str, Trend] | None = None,
                 ) -> tuple[GoalProgress, ...]:
        trends = trends or {}
        out: list[GoalProgress] = []
        for goal in goals:
            if not goal.active:
                continue
            metrics = window_metrics.get(goal.window) or window_metrics.get("lifetime", {})
            metric = metrics.get(goal.metric)
            current = metric.value if metric else None
            sample = metric.sample if metric else 0
            met = _met(current, goal.comparator, goal.target)
            window_label = WINDOWS[goal.window].label if goal.window in WINDOWS \
                else goal.window

            if current is None:
                detail = (f"Not measurable yet over {window_label.lower()} — "
                          f"{METRIC_SPECS.get(goal.metric, ('this metric',))[0]} "
                          f"needs data this history doesn't have.")
            else:
                unit = goal.unit or (metric.unit if metric else "")
                comparison = "at least" if goal.comparator == ">=" else "no more than"
                detail = (
                    f"{_fmt(current, unit)} over {window_label.lower()} "
                    f"({sample} trades) against a target of {comparison} "
                    f"{_fmt(goal.target, unit)}.")

            out.append(GoalProgress(
                goal=goal, current=current, met=met,
                progress=_progress(current, goal.comparator, goal.target),
                sample=sample, detail=detail,
                trend=trends.get(goal.metric, Trend.UNKNOWN),
            ))
        # Unmet goals first, then by how close they are — the nearly-there goal
        # is the motivating one to show at the top of an unmet list.
        out.sort(key=lambda g: (g.met, -g.progress))
        return tuple(out)


def _fmt(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "R":
        return f"{value:.2f}R"
    if unit == "min":
        return f"{value:.0f} min"
    return f"{value:g}"
