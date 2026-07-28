"""PerformanceEngine — what the record says, as numbers.

Produces the **metric registry**: a flat `{key: Metric}` map that is the
addressable vocabulary of the whole intelligence layer. Goals target metrics by
key. Scorecards cite them by key. The report writer looks them up by key. The
dashboard renders them by key. Nothing above this module recomputes an average.

Design notes worth keeping:

* **One computation, many windows.** `compute(facts)` is a pure function of a
  list of facts, so the same code produces lifetime metrics, last-30-day
  metrics and per-month metrics. There is no separate "recent" implementation to
  drift from the lifetime one.
* **A metric is None when it cannot be computed**, and its `sample` says over
  how many trades the attempt was made. `avg_r` over trades that never recorded
  a protective stop is None, not 0 — the difference between "you make no R" and
  "your R was never measurable" is the difference between a useful coaching
  statement and a false accusation.
* **Streaks are computed on P/L sign**, not on the coach's won/lost/scratch
  verdict, because the verdict only exists for reviewed trades and a streak with
  holes in it is worse than no streak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import Metric, PeriodStat, Trend
from optionspilot.intelligence.windows import (
    PERIODS, WINDOWS, bucket, period_label,
)

# The metric registry's schema. Keys are a stable public contract — goals,
# scorecards and the UI all address metrics through them, so adding is safe and
# renaming is a breaking change. `higher_is_better` is what lets a goal, a trend
# and a score all agree on which direction is progress without each restating it.
METRIC_SPECS: dict[str, tuple[str, str, bool, str]] = {
    # key: (label, unit, higher_is_better, explanation)
    "trades": ("Trades", "trades", True,
               "Completed round trips in the measured window."),
    "wins": ("Winning trades", "trades", True, "Trades closed for a profit."),
    "losses": ("Losing trades", "trades", False, "Trades closed at a loss."),
    "win_rate": ("Win rate", "%", True,
                 "Share of trades closed profitably. On its own it says little "
                 "— a 35% win rate with large winners beats a 70% win rate with "
                 "larger losers."),
    "total_pnl": ("Net P/L", "$", True,
                  "Sum of every closed trade's profit and loss, after commissions."),
    "expectancy": ("Expectancy", "$", True,
                   "Average profit or loss per trade. The headline number: it "
                   "folds win rate and payoff size together, so it stays honest "
                   "when either one changes."),
    "profit_factor": ("Profit factor", "", True,
                      "Gross profit divided by gross loss. Above 1.0 is "
                      "profitable; above 1.5 is durable."),
    "avg_win": ("Average winner", "$", True, "Mean profit of winning trades."),
    "avg_loss": ("Average loser", "$", False, "Mean loss of losing trades."),
    "payoff_ratio": ("Payoff ratio", "", True,
                     "Average winner divided by average loser — how much bigger "
                     "your wins are than your losses."),
    "avg_r": ("Average R multiple", "R", True,
              "Mean realized profit in units of the risk planned at entry. "
              "Measured only over trades whose protective stop was recorded."),
    "max_drawdown": ("Max drawdown", "$", False,
                     "Largest peak-to-trough fall of cumulative P/L."),
    "recovery_factor": ("Recovery factor", "", True,
                        "Net profit divided by max drawdown — how much profit "
                        "each unit of drawdown bought."),
    "sharpe_like": ("Reward / variability", "", True,
                    "Average P/L divided by its standard deviation, per trade. "
                    "Not an annualised Sharpe ratio — there is no risk-free "
                    "rate and no time-scaling here."),
    "consistency": ("Consistency", "", True,
                    "How steady your results are week to week. Measures "
                    "repeatability, not profitability — a consistent loser "
                    "scores well here and badly everywhere else."),
    "avg_hold_minutes": ("Average hold time", "min", True,
                         "Mean minutes from entry fill to final exit."),
    "avg_win_hold": ("Average winner hold", "min", True,
                     "Mean hold time of winning trades."),
    "avg_loss_hold": ("Average loser hold", "min", False,
                      "Mean hold time of losing trades."),
    "hold_asymmetry": ("Hold asymmetry", "", True,
                       "Winner hold time divided by loser hold time. Below 1.0 "
                       "means losers are held longer than winners — the "
                       "signature of cutting winners early and hoping on losers."),
    "avg_outlay": ("Average position size", "$", True,
                   "Mean premium committed per trade."),
    "size_consistency": ("Sizing consistency", "", True,
                         "How uniform position sizes are. Erratic sizing means "
                         "one impulsive trade can undo ten disciplined ones."),
    "avg_return_pct": ("Average return", "%", True,
                       "Mean profit or loss as a percentage of premium paid."),
    "best_trade": ("Best trade", "$", True, "Largest single winner."),
    "worst_trade": ("Worst trade", "$", False, "Largest single loser."),
    "best_day_pnl": ("Best day", "$", True,
                     "Largest net profit across a single exchange day."),
    "worst_day_pnl": ("Worst day", "$", True,
                      "Largest net loss across a single exchange day, as a "
                      "negative number. The day — not the trade — is the unit a "
                      "daily loss limit is written in."),
    "current_streak": ("Current streak", "trades", True,
                       "Consecutive wins (positive) or losses (negative) right now."),
    "longest_win_streak": ("Longest win streak", "trades", True, ""),
    "longest_loss_streak": ("Longest loss streak", "trades", False, ""),
    "active_days": ("Active days", "days", True,
                    "Distinct exchange days on which a trade was entered."),
    "trades_per_active_day": ("Trades per active day", "trades", True,
                              "Average entries on days you traded at all."),
    "avg_process_score": ("Average process score", "", True,
                          "Mean coach score, which rates decision quality, not "
                          "outcome. Only reviewed trades count."),
    "stop_discipline_rate": ("Stop discipline", "%", True,
                             "Share of reviewed trades that had a resting "
                             "protective stop and never widened it."),
    "plan_rate": ("Planning rate", "%", True,
                  "Share of reviewed trades with both a stop and a profit "
                  "target defined."),
    "mistake_rate": ("Mistakes per trade", "", False,
                     "Average number of distinct process mistakes tagged per "
                     "reviewed trade."),
    "clean_trade_rate": ("Clean trade rate", "%", True,
                         "Share of reviewed trades with no process mistake at all."),
}


def _metric(key: str, value: float | None, sample: int) -> Metric:
    label, unit, higher, explanation = METRIC_SPECS[key]
    return Metric(key=key, label=label, value=value, unit=unit, sample=sample,
                  explanation=explanation, higher_is_better=higher)


def _streaks(facts: list[TradeFact]) -> tuple[int, int, int]:
    """(current, longest_win, longest_loss). A scratch (exactly 0.0 P/L) does
    not extend a streak in either direction and does not break one — it is not
    evidence of anything."""
    current = longest_win = longest_loss = 0
    run_kind: str | None = None
    run = 0
    for fact in facts:
        if fact.pnl == 0:
            continue
        kind = "win" if fact.pnl > 0 else "loss"
        run = run + 1 if kind == run_kind else 1
        run_kind = kind
        if kind == "win":
            longest_win = max(longest_win, run)
        else:
            longest_loss = max(longest_loss, run)
    if run_kind == "win":
        current = run
    elif run_kind == "loss":
        current = -run
    return current, longest_win, longest_loss


def compute(facts: list[TradeFact] | tuple[TradeFact, ...]) -> dict[str, Metric]:
    """The full metric registry for one set of trades.

    Pure and total: any list of facts, including an empty one, produces a
    complete registry. Metrics that could not be computed are present with
    `value=None`, so a consumer can always look up a key it knows about and
    never has to branch on absence.
    """
    facts = list(facts)
    n = len(facts)
    pnls = [f.pnl for f in facts]
    wins = [f for f in facts if f.pnl > 0]
    losses = [f for f in facts if f.pnl < 0]

    holds = [f.hold_minutes for f in facts if f.hold_minutes > 0]
    win_holds = [f.hold_minutes for f in wins if f.hold_minutes > 0]
    loss_holds = [f.hold_minutes for f in losses if f.hold_minutes > 0]
    outlays = [f.outlay for f in facts if f.outlay > 0]
    returns = [f.return_pct for f in facts if f.return_pct is not None]
    rs = [f.r_multiple for f in facts if f.r_multiple is not None]
    scores = [float(f.process_score) for f in facts if f.process_score is not None]

    reviewed = [f for f in facts if f.reviewed]
    stopped = [f for f in reviewed if f.had_stop is not None]
    disciplined = [f for f in stopped if f.had_stop and not f.widened_stop]
    planned = [f for f in reviewed if f.had_stop and f.had_target]
    clean = [f for f in reviewed if not f.mistakes]

    days = {f.entry_date for f in facts if f.entry_date}
    # Per-day net P/L: the unit a daily loss limit is actually written in.
    # Attributed to the ENTRY date so a position opened Thursday and closed
    # Friday counts against the day the decision was made.
    by_day: dict[str, float] = {}
    for f in facts:
        if f.entry_date:
            by_day[f.entry_date] = by_day.get(f.entry_date, 0.0) + f.pnl
    day_pnls = list(by_day.values())
    current, longest_win, longest_loss = _streaks(facts)

    avg_win_hold = stats.mean(win_holds)
    avg_loss_hold = stats.mean(loss_holds)
    asymmetry = (avg_win_hold / avg_loss_hold
                 if avg_win_hold is not None and avg_loss_hold not in (None, 0)
                 else None)

    wr = stats.win_rate(pnls)
    consistency_series, consistency_grain = _consistency_series(facts)

    values: dict[str, tuple[float | None, int]] = {
        "trades": (float(n), n),
        "wins": (float(len(wins)), n),
        "losses": (float(len(losses)), n),
        "win_rate": (round(wr * 100, 2) if wr is not None else None, n),
        "total_pnl": (round(sum(pnls), 2) if facts else None, n),
        "expectancy": (round(stats.expectancy(pnls), 2) if facts else None, n),
        "profit_factor": (_round(stats.profit_factor(pnls), 3), n),
        "avg_win": (_round(stats.avg_win(pnls)), len(wins)),
        "avg_loss": (_round(stats.avg_loss(pnls)), len(losses)),
        "payoff_ratio": (_round(stats.payoff_ratio(pnls), 3), n),
        "avg_r": (_round(stats.mean(rs), 3), len(rs)),
        "max_drawdown": (round(stats.max_drawdown(pnls), 2) if facts else None, n),
        "recovery_factor": (_round(stats.recovery_factor(pnls), 3), n),
        "sharpe_like": (_round(stats.sharpe_like(pnls), 3), n),
        "consistency": (stats.consistency(consistency_series),
                        len(consistency_series)),
        "avg_hold_minutes": (_round(stats.mean(holds), 1), len(holds)),
        "avg_win_hold": (_round(avg_win_hold, 1), len(win_holds)),
        "avg_loss_hold": (_round(avg_loss_hold, 1), len(loss_holds)),
        "hold_asymmetry": (_round(asymmetry, 3), len(win_holds) + len(loss_holds)),
        "avg_outlay": (_round(stats.mean(outlays)), len(outlays)),
        "size_consistency": (stats.consistency(outlays), len(outlays)),
        "avg_return_pct": (_round(stats.mean(returns)), len(returns)),
        "best_trade": (round(max(pnls), 2) if pnls else None, n),
        "worst_trade": (round(min(pnls), 2) if pnls else None, n),
        "best_day_pnl": (round(max(day_pnls), 2) if day_pnls else None,
                         len(day_pnls)),
        "worst_day_pnl": (round(min(day_pnls), 2) if day_pnls else None,
                          len(day_pnls)),
        "current_streak": (float(current) if facts else None, n),
        "longest_win_streak": (float(longest_win) if facts else None, n),
        "longest_loss_streak": (float(longest_loss) if facts else None, n),
        "active_days": (float(len(days)) if days else None, n),
        "trades_per_active_day": (round(n / len(days), 2) if days else None, n),
        "avg_process_score": (_round(stats.mean(scores), 1), len(scores)),
        "stop_discipline_rate": (
            round(len(disciplined) / len(stopped) * 100, 2) if stopped else None,
            len(stopped)),
        "plan_rate": (
            round(len(planned) / len(reviewed) * 100, 2) if reviewed else None,
            len(reviewed)),
        "mistake_rate": (
            round(sum(len(f.mistakes) for f in reviewed) / len(reviewed), 3)
            if reviewed else None, len(reviewed)),
        "clean_trade_rate": (
            round(len(clean) / len(reviewed) * 100, 2) if reviewed else None,
            len(reviewed)),
    }
    return {key: _metric(key, value, sample) for key, (value, sample) in values.items()}


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    # Infinity is a legitimate profit factor (a sample with no losers) and
    # round() preserves it; models._finite keeps it out of the JSON payload.
    return round(value, digits) if math.isfinite(value) else value


# The grain `consistency` is measured at, coarsest first, with the number of
# populated buckets each grain needs. Per-trade P/L is the last resort and
# deliberately so: the spread of individual option results is enormous for
# everybody, so scoring it would hand every trader the same ~20 and say nothing.
# What a trader means by "consistent" is that their WEEKS look alike.
_CONSISTENCY_LADDER = (("week", 3), ("day", 5))


def _consistency_series(facts: list[TradeFact]) -> tuple[list[float], str]:
    """The series `consistency` is computed over, plus a label for it."""
    for period, minimum in _CONSISTENCY_LADDER:
        groups = bucket(facts, period)
        # A bucket needs more than one trade before its total means anything.
        totals = [sum(f.pnl for f in group)
                  for group in groups.values() if len(group) >= 2]
        if len(totals) >= minimum:
            return totals, period
    return [f.pnl for f in facts], "trade"


def period_series(facts: list[TradeFact] | tuple[TradeFact, ...],
                  period: str) -> tuple[PeriodStat, ...]:
    """Per-period headline stats, oldest first. The backing data for every
    "daily / weekly / monthly / quarterly" trend in the system."""
    out: list[PeriodStat] = []
    for key, group in sorted(bucket(facts, period).items()):
        pnls = [f.pnl for f in group]
        rs = [f.r_multiple for f in group if f.r_multiple is not None]
        wins = sum(1 for p in pnls if p > 0)
        out.append(PeriodStat(
            key=key, label=period_label(key, period), trades=len(group),
            wins=wins, win_rate=round(wins / len(group), 4),
            pnl=round(sum(pnls), 2),
            expectancy=round(sum(pnls) / len(group), 2),
            profit_factor=stats.profit_factor(pnls),
            avg_r=_round(stats.mean(rs), 3),
        ))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class PerformanceResult:
    metrics: dict[str, Metric]
    periods: dict[str, tuple[PeriodStat, ...]]
    windows: dict[str, dict[str, Metric]]
    trends: dict[str, Trend]

    def metric(self, key: str) -> Metric | None:
        return self.metrics.get(key)

    def window_value(self, window: str, key: str) -> float | None:
        m = self.windows.get(window, {}).get(key)
        return m.value if m else None


# Metrics whose month-over-month direction the timeline and reports narrate.
# Deliberately short: a trend line on thirty metrics is a wall, not a signal.
TRENDED = ("expectancy", "win_rate", "profit_factor", "avg_r", "consistency",
           "avg_process_score", "stop_discipline_rate", "mistake_rate",
           "avg_outlay", "avg_hold_minutes", "clean_trade_rate")


class PerformanceEngine:
    """Computes the metric registry over the lifetime, every named window, and
    every calendar period, in one pass per grouping."""

    def analyze(self, facts: tuple[TradeFact, ...] | list[TradeFact]
                ) -> PerformanceResult:
        facts = list(facts)
        metrics = compute(facts)
        periods = {p: period_series(facts, p) for p in PERIODS}
        windows = {name: compute(spec.select(facts))
                   for name, spec in WINDOWS.items()}

        # Trends are read off the monthly series, which is the coarsest grain
        # that still updates within a normal trading cadence. A trend needs at
        # least two qualifying months (windows.previous_and_latest enforces the
        # per-month floor elsewhere; here the series itself is the input).
        monthly = periods["month"]
        trends: dict[str, Trend] = {}
        if len(monthly) >= 2:
            by_month = bucket(facts, "month")
            series: dict[str, list[float]] = {k: [] for k in TRENDED}
            for key in sorted(by_month):
                month_metrics = compute(by_month[key])
                for metric_key in TRENDED:
                    value = month_metrics[metric_key].value
                    if value is not None:
                        series[metric_key].append(value)
            for metric_key, values in series.items():
                trends[metric_key] = stats.trend_of(
                    values,
                    higher_is_better=METRIC_SPECS[metric_key][2])
        else:
            trends = {k: Trend.UNKNOWN for k in TRENDED}

        return PerformanceResult(metrics=metrics, periods=periods,
                                 windows=windows, trends=trends)
