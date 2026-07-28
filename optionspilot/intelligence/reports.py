"""ReportEngine — the weekly and monthly coaching reports, in prose.

The instruction was "do not simply dump statistics; explain them naturally", and
the difference is structural rather than cosmetic. A statistics dump lists what
was measured. A report answers, in order: what happened, what changed, why, what
to do about it, and what to aim at next — and it only mentions a number when
that number is load-bearing for one of those answers.

Everything here is assembled from the snapshot the other engines already
produced. `ReportEngine` computes nothing of its own; if it needed to, that
would be a sign the calculation belongs in a real engine where the dashboard can
reach it too. Its whole job is selection and phrasing.

Reports are generated for the most recent *complete-enough* period, not for a
period that has barely started: a Monday-morning weekly report over one trade
would be noise, so a period below `MIN_PERIOD_TRADES` produces a short,
explicitly-provisional report rather than a confident one.
"""

from __future__ import annotations

from datetime import datetime

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import (
    BehaviorFinding, Confidence, GoalProgress, LessonRecommendation, Metric,
    Pattern, Recommendation, Report, ReportSection, ScoreCard, TimelineEntry,
    Trend,
)
from optionspilot.intelligence.performance import METRIC_SPECS, compute
from optionspilot.intelligence.windows import bucket, period_label

# A period below this is reported, but marked provisional and never compared
# against another period. A four-trade week that lost money is not "expectancy
# down 176%" — it is four trades, and saying otherwise manufactures a crisis.
MIN_PERIOD_TRADES = 5

# The handful of numbers a report leads with. Everything else has to earn its
# place by having changed or by explaining a recommendation.
HEADLINE = ("trades", "win_rate", "total_pnl", "expectancy", "profit_factor",
            "avg_r", "max_drawdown")


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "R":
        return f"{value:.2f}R"
    if unit == "min":
        return f"{value:.0f} min"
    if unit == "trades" or unit == "days":
        return f"{value:.0f}"
    return f"{value:.2f}"


def _phrase(metric: Metric) -> str:
    return f"{metric.label} {_fmt(metric.value, metric.unit)}"


class ReportEngine:
    """Writes one report per requested period from an already-computed analysis."""

    def build(self, *, facts: tuple[TradeFact, ...], period: str,
              behaviors: tuple[BehaviorFinding, ...],
              patterns: tuple[Pattern, ...],
              scores: tuple[ScoreCard, ...],
              recommendations: tuple[Recommendation, ...],
              lessons: tuple[LessonRecommendation, ...],
              goals: tuple[GoalProgress, ...],
              timeline: tuple[TimelineEntry, ...],
              generated: datetime) -> Report | None:
        """The report for the most recent `period` ("week" or "month").

        Returns None when there is no period to report on at all — an empty
        report is worse than no report, because it implies the analysis ran and
        found nothing rather than that it never had anything to run on.
        """
        buckets = bucket(facts, period)
        if not buckets:
            return None
        key = max(buckets)
        current = buckets[key]
        previous_keys = sorted(k for k in buckets if k < key)
        previous = buckets[previous_keys[-1]] if previous_keys else []

        kind = "weekly" if period == "week" else "monthly"
        label = period_label(key, period)
        metrics = compute(current)
        prior = compute(previous) if previous else None
        provisional = len(current) < MIN_PERIOD_TRADES

        sections: list[ReportSection] = [
            self._what_happened(label, current, metrics, provisional),
            self._what_changed(current, previous, metrics, prior, label),
            self._behavior_section(behaviors),
            self._risk_section(metrics, behaviors),
            self._patterns_section(patterns),
            self._focus_section(recommendations, lessons),
            self._goals_section(goals),
            self._timeline_section(timeline),
        ]
        sections = [s for s in sections if s.body]

        return Report(
            period=key, kind=kind,
            title=f"{kind.capitalize()} review — {label}",
            summary=self._summary(label, current, metrics, prior, behaviors,
                                  scores, provisional),
            sections=tuple(sections),
            generated=generated.isoformat(), trades=len(current),
        )

    # ── sections ─────────────────────────────────────────────────────────

    def _summary(self, label: str, current: list[TradeFact],
                 metrics: dict[str, Metric], prior: dict[str, Metric] | None,
                 behaviors: tuple[BehaviorFinding, ...],
                 scores: tuple[ScoreCard, ...], provisional: bool) -> str:
        if not current:
            return f"No trades were closed in {label}."
        pnl = metrics["total_pnl"].value or 0.0
        n = len(current)
        opening = (
            f"{label}: {n} trade{'s' if n != 1 else ''} closed for "
            f"{_fmt(pnl, '$')}, a {_fmt(metrics['win_rate'].value, '%')} win "
            f"rate and {_fmt(metrics['expectancy'].value, '$')} per trade.")
        if provisional:
            return (opening + " That is too few trades to draw conclusions "
                    "from — treat everything below as provisional.")

        parts = [opening]
        before = prior["expectancy"].value if prior else None
        after = metrics["expectancy"].value
        if stats.comparable(before, after):
            if (before > 0) != (after > 0):
                # A percentage across a sign change is arithmetically true and
                # useless to read ("expectancy declined 176%"). Say what
                # actually happened instead.
                parts.append(
                    f"Expectancy turned {'positive' if after > 0 else 'negative'} "
                    f"({before:+.2f} → {after:+.2f}).")
            else:
                change = stats.pct_change(before, after)
                if change is not None and abs(change) >= 10:
                    parts.append(
                        f"Expectancy is {'up' if change > 0 else 'down'} "
                        f"{abs(change):.0f}% on the previous period.")

        worst = next((b for b in behaviors
                      if b.assessable and b.detected and b.confidence >= Confidence.MEDIUM),
                     None)
        if worst:
            parts.append(f"The habit costing you most is {worst.label.lower()} "
                         f"({worst.occurrences} of {worst.sample} trades).")
        else:
            strong = next((s for s in scores
                           if s.value is not None and s.value >= 70), None)
            if strong:
                parts.append(f"No repeated behavioural problem showed up, and "
                             f"your {strong.label.lower()} is holding at "
                             f"{strong.value:.0f}/100.")
        return " ".join(parts)

    def _what_happened(self, label: str, current: list[TradeFact],
                       metrics: dict[str, Metric], provisional: bool
                       ) -> ReportSection:
        if not current:
            return ReportSection("What happened", ())
        wins = metrics["wins"].value or 0
        losses = metrics["losses"].value or 0
        body = [
            f"You closed {len(current)} trades in {label} — {wins:.0f} winners "
            f"and {losses:.0f} losers — for {_fmt(metrics['total_pnl'].value, '$')} "
            f"net.",
        ]
        payoff = metrics["payoff_ratio"].value
        if payoff is not None:
            shape = ("your winners are bigger than your losers"
                     if payoff >= 1 else
                     "your losers are bigger than your winners, which means the "
                     "win rate has to carry everything")
            body.append(
                f"Average winner {_fmt(metrics['avg_win'].value, '$')} against "
                f"an average loser {_fmt(metrics['avg_loss'].value, '$')} — "
                f"{shape}.")
        dd = metrics["max_drawdown"].value
        if dd:
            body.append(
                f"The deepest point of the period was {_fmt(dd, '$')} below its "
                f"high-water mark.")
        if provisional:
            body.append(
                "With this few trades none of these figures is stable; they are "
                "reported for the record, not for decisions.")
        return ReportSection(
            "What happened", tuple(body),
            tuple(metrics[k] for k in HEADLINE if metrics[k].value is not None))

    def _what_changed(self, current: list[TradeFact], previous: list[TradeFact],
                      metrics: dict[str, Metric],
                      prior: dict[str, Metric] | None, label: str
                      ) -> ReportSection:
        # BOTH sides must clear the floor. Comparing a full month against a
        # four-trade week produces confident percentages out of noise, which is
        # exactly the failure this section would otherwise be famous for.
        if not prior or len(previous) < MIN_PERIOD_TRADES \
                or len(current) < MIN_PERIOD_TRADES:
            return ReportSection(
                "What changed",
                ("There is no pair of periods with enough trades on both sides "
                 "to compare, so nothing here is a change — it is a starting "
                 "point.",)
                if current else ())
        improved: list[str] = []
        declined: list[str] = []
        for key in ("expectancy", "win_rate", "profit_factor", "avg_r",
                    "consistency", "avg_process_score", "stop_discipline_rate",
                    "mistake_rate", "clean_trade_rate", "avg_hold_minutes"):
            before, after = prior[key].value, metrics[key].value
            if not stats.comparable(before, after):
                continue
            spec_label, unit, higher_is_better, _ = METRIC_SPECS[key]
            better = (after > before) == higher_is_better
            if (before > 0) != (after > 0):
                sentence = (f"{spec_label} moved from {_fmt(before, unit)} to "
                            f"{_fmt(after, unit)}.")
            else:
                change = stats.pct_change(before, after)
                if change is None or abs(change) < 10:
                    continue
                sentence = (f"{spec_label} moved from {_fmt(before, unit)} to "
                            f"{_fmt(after, unit)} ({change:+.0f}%).")
            (improved if better else declined).append(sentence)

        body: list[str] = []
        if improved:
            body.append("Improved: " + " ".join(improved))
        if declined:
            body.append("Declined: " + " ".join(declined))
        if not body:
            body.append(
                "Nothing moved by more than 10% against the previous period — "
                "which, if your numbers are where you want them, is the point.")
        return ReportSection("What changed", tuple(body))

    def _behavior_section(self, behaviors: tuple[BehaviorFinding, ...]
                          ) -> ReportSection:
        detected = [b for b in behaviors if b.assessable and b.detected]
        body: list[str] = []
        for finding in detected[:4]:
            line = finding.summary
            if finding.impact and finding.impact.delta is not None:
                line += (f" Removing those trades would have moved expectancy "
                         f"from {finding.impact.baseline:+.2f} to "
                         f"{finding.impact.adjusted:+.2f}.")
            if finding.trend is Trend.IMPROVING:
                line += " This is happening less often than it was."
            elif finding.trend is Trend.DECLINING:
                line += " This is happening more often than it was."
            body.append(line)
        if not body:
            assessed = [b for b in behaviors if b.assessable]
            if assessed:
                body.append(
                    f"None of the {len(assessed)} behaviours the system can "
                    f"measure showed a repeated problem this period.")
        unassessable = [b for b in behaviors if not b.assessable]
        if unassessable and body:
            body.append(
                f"{len(unassessable)} behaviour(s) could not be assessed for "
                f"lack of recorded data — they are listed on the Coach tab with "
                f"the reason.")
        return ReportSection("Behaviour", tuple(body))

    def _risk_section(self, metrics: dict[str, Metric],
                      behaviors: tuple[BehaviorFinding, ...]) -> ReportSection:
        body: list[str] = []
        stop_rate = metrics["stop_discipline_rate"]
        if stop_rate.value is not None:
            body.append(
                f"{_fmt(stop_rate.value, '%')} of reviewed trades ran with a "
                f"resting stop that was never widened, across "
                f"{stop_rate.sample} trades.")
        size = metrics["size_consistency"]
        if size.value is not None:
            verdict = ("steady" if size.value >= 70
                       else "uneven" if size.value >= 50 else "erratic")
            body.append(f"Position sizing was {verdict} "
                        f"({size.value:.0f}/100 consistency).")
        worst_day = metrics["worst_day_pnl"]
        if worst_day.value is not None and worst_day.value < 0:
            body.append(f"The worst single day cost "
                        f"{_fmt(worst_day.value, '$')}.")
        return ReportSection("Risk", tuple(body))

    def _patterns_section(self, patterns: tuple[Pattern, ...]) -> ReportSection:
        usable = [p for p in patterns if p.confidence >= Confidence.MEDIUM]
        if not usable:
            return ReportSection("Where your edge lives", ())
        body: list[str] = []
        strengths = [p for p in usable if p.kind == "strength"][:2]
        weaknesses = [p for p in usable if p.kind == "weakness"][:2]
        for pattern in strengths:
            body.append(
                f"Strength — {pattern.dimension_label.lower()} "
                f"{pattern.bucket}: {pattern.win_rate:.0%} win rate over "
                f"{pattern.trades} trades against {pattern.baseline_win_rate:.0%} "
                f"elsewhere.")
        for pattern in weaknesses:
            body.append(
                f"Weakness — {pattern.dimension_label.lower()} "
                f"{pattern.bucket}: {pattern.win_rate:.0%} over "
                f"{pattern.trades} trades against "
                f"{pattern.baseline_win_rate:.0%} elsewhere, and "
                f"{pattern.expectancy:+.2f} per trade.")
        return ReportSection("Where your edge lives", tuple(body))

    def _focus_section(self, recommendations: tuple[Recommendation, ...],
                       lessons: tuple[LessonRecommendation, ...]
                       ) -> ReportSection:
        body: list[str] = []
        for i, rec in enumerate(recommendations[:3], start=1):
            body.append(f"{i}. {rec.action} — {rec.rationale}")
        if lessons:
            titles = ", ".join(f"“{x.title}”" for x in lessons[:3])
            body.append(f"Recommended reading, triggered by the numbers above: "
                        f"{titles}.")
        if not body:
            body.append(
                "Nothing measurable is currently worth changing. Keep the "
                "process identical and let the sample grow.")
        return ReportSection("What to focus on next", tuple(body))

    def _goals_section(self, goals: tuple[GoalProgress, ...]) -> ReportSection:
        if not goals:
            return ReportSection("Goals", ())
        met = [g for g in goals if g.met]
        unmet = [g for g in goals if not g.met]
        body: list[str] = []
        if met:
            body.append("Met: " + "; ".join(
                f"{g.goal.label} ({g.detail})" for g in met[:4]))
        if unmet:
            closest = unmet[0]
            body.append(
                f"Closest unmet goal: {closest.goal.label} — "
                f"{closest.progress:.0%} of the way there. {closest.detail}")
        return ReportSection("Goals", tuple(body))

    def _timeline_section(self, timeline: tuple[TimelineEntry, ...]
                          ) -> ReportSection:
        entries = [t for t in timeline if t.kind in ("improvement", "streak")][:3]
        if not entries:
            return ReportSection("Worth noticing", ())
        return ReportSection(
            "Worth noticing",
            tuple(f"{t.headline} {t.detail}" for t in entries))
