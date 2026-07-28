"""ConfidenceEngine — the eight composite scores, each of which explains itself.

A composite score is the most dangerous object in a coaching system: it
compresses a lot of evidence into one number, and a number with no provenance is
indistinguishable from an opinion. So every `ScoreCard` this module produces
carries its `components` — the named inputs, their individual 0–100 readings and
their weights — and the UI is built to show them. "Discipline 62" is never
shown without "because stop discipline is 71%, you widened a stop on 3 of 40
trades, and 18% of trades carried no process mistake at all".

Rules:

* **A component that cannot be measured is dropped, and the remaining weights
  are renormalised.** It is never scored as zero. A trader who has never had a
  stop level recorded has no R-multiple component; that must not read as
  "terrible risk control".
* **A score with no measurable components is None**, and `Confidence.NONE`.
  There is no default 50.
* **Confidence is the weakest link**, combining the sample behind the components
  with how much of the intended weight was actually measurable. A score built
  from one of five components is reported at low confidence even if that one
  component had a thousand trades behind it.

Every raw metric is mapped onto 0–100 by `_scale`, whose anchors are documented
at each call site. The anchors are trading judgements, not statistics, and they
are the one place in this package where an external standard is applied rather
than the trader's own baseline — a 40% stop-discipline rate is bad in absolute
terms, and grading it against the trader's own history would tell someone who
has never used a stop that they are doing fine.
"""

from __future__ import annotations

from dataclasses import dataclass

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import (
    BehaviorFinding, Confidence, Metric, PeriodStat, ScoreCard, ScoreComponent,
    Trend, grade,
)

# Category names produced by coach/categories.py, read off the fact. Referenced
# by name rather than imported so `intelligence` stays below `coach` in the
# layering — a missing category is simply an unmeasurable component.
CAT_ENTRY = "Entry Quality"
CAT_EXIT = "Exit Quality"
CAT_RISK = "Risk Management"
CAT_SIZE = "Position Size"
CAT_EMOTION = "Emotional Discipline"
CAT_RULES = "Rule Following"
CAT_PATIENCE = "Patience"
CAT_TIMING = "Timing"
CAT_TREND = "Trend Alignment"
CAT_RR = "Reward/Risk Ratio"


# Minimum share of a score's intended weight that must actually be measurable
# before a number is produced at all.
#
# Without this, a trader who has never had a trade reviewed scores Discipline
# 100/100 grade A — because the single component that needs no review (revenge
# trading, which reads timestamps) came back clean, and 20% coverage was enough
# to average. An A earned by an absence of data is the most flattering lie this
# package could tell, and the "measured over 20% of the intended inputs" caveat
# under a large green A does not undo it.
MIN_COVERAGE = 0.35


def _scale(value: float | None, worst: float, best: float) -> float | None:
    """Map a raw value onto 0–100, clamped. `worst` may exceed `best` for
    metrics where lower is better (mistake rate, drawdown ratio)."""
    if value is None:
        return None
    if worst == best:
        return None
    ratio = (value - worst) / (best - worst)
    return round(max(0.0, min(1.0, ratio)) * 100, 1)


@dataclass(frozen=True, slots=True)
class ScoreInput:
    """Everything the scorers may read. Assembled once by the facade."""

    facts: tuple[TradeFact, ...]
    metrics: dict[str, Metric]
    behaviors: dict[str, BehaviorFinding]
    monthly: tuple[PeriodStat, ...]
    trends: dict[str, Trend]

    def metric_value(self, key: str) -> float | None:
        m = self.metrics.get(key)
        return m.value if m else None

    def metric_sample(self, key: str) -> int:
        m = self.metrics.get(key)
        return m.sample if m else 0

    def category_mean(self, name: str) -> tuple[float | None, int]:
        """Average of one coach category across every reviewed trade that
        scored it. Returns (value, sample)."""
        values = [f.category_scores[name] for f in self.facts
                  if name in f.category_scores]
        return stats.mean(values), len(values)

    def behavior_penalty(self, behavior_id: str, ceiling: float
                         ) -> tuple[float | None, int, str]:
        """A behavior's rate expressed as a 0–100 'you don't do this' reading.

        `ceiling` is the rate at which the component bottoms out — for a serious
        habit that is a low number, because doing it a fifth of the time is
        already a full failure of that component.
        """
        finding = self.behaviors.get(behavior_id)
        if finding is None or not finding.assessable:
            return None, 0, ""
        value = _scale(finding.rate, ceiling, 0.0)
        detail = (f"{finding.occurrences} of {finding.sample} trades "
                  f"({finding.rate:.0%})")
        return value, finding.sample, detail


def _card(key: str, label: str, components: list[ScoreComponent],
          explanation_prefix: str, sample: int,
          trend: float | None = None) -> ScoreCard:
    """Combine measured components into one card, renormalising over whatever
    was measurable and explaining the result in terms of its inputs."""
    # A zero-weight component is informational only: it is shown to the user
    # but must never enter the average, and must never be named as the reason a
    # score is high or low. (Planning's "reward/risk recorded at entry" is one.)
    scoring = [c for c in components if c.weight > 0]
    measured = [c for c in scoring if c.value is not None]
    total_weight = sum(c.weight for c in scoring)
    measured_weight = sum(c.weight for c in measured)

    coverage = measured_weight / total_weight if total_weight else 0.0
    if not measured or measured_weight <= 0 or coverage < MIN_COVERAGE:
        missing = [c.label.lower() for c in scoring if c.value is None]
        detail = (f" Nothing was recorded for {', '.join(missing[:4])}."
                  if missing else "")
        return ScoreCard(
            key=key, label=label, value=None, grade="—",
            confidence=Confidence.NONE, sample=sample,
            explanation=f"{explanation_prefix} Not enough recorded data to "
                        f"score this yet — only {coverage:.0%} of the inputs "
                        f"this score needs were measurable.{detail}",
            components=tuple(components))

    value = round(sum(c.value * c.weight for c in measured) / measured_weight, 1)

    by_sample = stats.sample_confidence(sample)
    if coverage >= 0.8:
        by_coverage = Confidence.HIGH
    elif coverage >= 0.5:
        by_coverage = Confidence.MEDIUM
    else:
        by_coverage = Confidence.LOW
    confidence = stats.combine_confidence(by_sample, by_coverage)

    # "Held back most by" means the component that COST the most points, which
    # is (100 − value) × weight — not the smallest weighted value. Ranking on
    # `value × weight` picks whichever good component happens to carry a small
    # weight, and then tells the user a perfect 100/100 input is their problem.
    weakest = max(measured, key=lambda c: (100 - c.value) * c.weight)
    strongest = max(measured, key=lambda c: (c.value, c.weight))
    parts = [explanation_prefix]
    if weakest.key != strongest.key:
        parts.append(
            f"Held back most by {weakest.label.lower()} ({weakest.value:.0f}/100"
            + (f" — {weakest.detail}" if weakest.detail else "") + "); "
            f"strongest on {strongest.label.lower()} ({strongest.value:.0f}/100).")
    else:
        parts.append(f"Based on {strongest.label.lower()} "
                     f"({strongest.value:.0f}/100).")
    if coverage < 0.8:
        missing = [c.label.lower() for c in scoring if c.value is None]
        parts.append(
            f"Measured over {coverage:.0%} of the intended inputs — no data for "
            f"{', '.join(missing)}.")

    return ScoreCard(
        key=key, label=label, value=value, grade=grade(value),
        confidence=confidence, sample=sample,
        explanation=" ".join(parts), components=tuple(components), trend=trend)


class ConfidenceEngine:
    """Builds all eight scorecards from one `ScoreInput`."""

    def analyze(self, data: ScoreInput) -> tuple[ScoreCard, ...]:
        n = len(data.facts)
        return (
            self._execution(data, n),
            self._discipline(data, n),
            self._risk_control(data, n),
            self._consistency(data, n),
            self._planning(data, n),
            self._adaptability(data, n),
            self._learning(data, n),
            self._decision_quality(data, n),
        )

    # ── individual scores ────────────────────────────────────────────────

    def _execution(self, d: ScoreInput, n: int) -> ScoreCard:
        entry_cat, entry_n = d.category_mean(CAT_ENTRY)
        exit_cat, exit_n = d.category_mean(CAT_EXIT)
        chase, chase_n, chase_detail = d.behavior_penalty("chasing", 0.35)
        # Anchors: a payoff ratio of 1.0 (winners the same size as losers) is a
        # pass at 0; 3.0 is exceptional. Below 1.0 with a sub-50% win rate is
        # arithmetic ruin, which is why the floor sits at 0.5.
        payoff = _scale(d.metric_value("payoff_ratio"), 0.5, 3.0)
        # Winners held at least as long as losers is the neutral point.
        asymmetry = _scale(d.metric_value("hold_asymmetry"), 0.5, 2.0)

        return _card("execution", "Execution Quality", [
            ScoreComponent("entry_quality", "Entry quality", entry_cat, 0.25,
                           f"coach category over {entry_n} reviewed trades"),
            ScoreComponent("exit_quality", "Exit quality", exit_cat, 0.25,
                           f"coach category over {exit_n} reviewed trades"),
            ScoreComponent("not_chasing", "Not chasing", chase, 0.15, chase_detail),
            ScoreComponent("payoff", "Payoff ratio", payoff, 0.20,
                           f"{d.metric_value('payoff_ratio')} average winner per "
                           f"unit of average loser"
                           if d.metric_value("payoff_ratio") else ""),
            ScoreComponent("hold_asymmetry", "Holding winners longer than losers",
                           asymmetry, 0.15,
                           f"ratio {d.metric_value('hold_asymmetry')}"
                           if d.metric_value("hold_asymmetry") else ""),
        ], "How well trades are entered and exited, independent of whether they "
           "won.", n)

    def _discipline(self, d: ScoreInput, n: int) -> ScoreCard:
        rules_cat, rules_n = d.category_mean(CAT_RULES)
        emotion_cat, _ = d.category_mean(CAT_EMOTION)
        clean = d.metric_value("clean_trade_rate")
        revenge, _, revenge_detail = d.behavior_penalty("revenge_trading", 0.20)
        moved, _, moved_detail = d.behavior_penalty("moving_stops", 0.15)
        averaged, _, averaged_detail = d.behavior_penalty("averaging_down", 0.15)

        return _card("discipline", "Discipline", [
            ScoreComponent("clean_trades", "Trades with no process mistake",
                           clean, 0.25,
                           f"{clean:.0f}% of reviewed trades" if clean is not None else ""),
            ScoreComponent("rule_following", "Rule following", rules_cat, 0.20,
                           f"coach category over {rules_n} reviewed trades"),
            ScoreComponent("emotional", "Emotional discipline", emotion_cat, 0.15, ""),
            ScoreComponent("no_revenge", "Not revenge trading", revenge, 0.20,
                           revenge_detail),
            ScoreComponent("stops_held", "Never widening a stop", moved, 0.10,
                           moved_detail),
            ScoreComponent("no_averaging", "Not averaging down", averaged, 0.10,
                           averaged_detail),
        ], "Whether your own rules survive contact with a live position.", n)

    def _risk_control(self, d: ScoreInput, n: int) -> ScoreCard:
        risk_cat, risk_n = d.category_mean(CAT_RISK)
        size_cat, _ = d.category_mean(CAT_SIZE)
        stop_rate = _scale(d.metric_value("stop_discipline_rate"), 40.0, 100.0)
        size_consistency = d.metric_value("size_consistency")
        oversized, _, oversized_detail = d.behavior_penalty("oversizing", 0.15)
        # Recovery factor: 0 means the drawdown ate everything; 3 means each
        # unit of drawdown bought three of profit. Undefined (no drawdown at
        # all) is left unmeasured rather than scored 100 — a two-trade winning
        # start is not proof of risk control.
        recovery = _scale(d.metric_value("recovery_factor"), 0.0, 3.0)

        return _card("risk_control", "Risk Control", [
            ScoreComponent("stop_discipline", "Stop discipline", stop_rate, 0.30,
                           f"{d.metric_value('stop_discipline_rate')}% of "
                           f"reviewed trades protected throughout"
                           if d.metric_value("stop_discipline_rate") is not None else ""),
            ScoreComponent("risk_category", "Risk management", risk_cat, 0.20,
                           f"coach category over {risk_n} reviewed trades"),
            ScoreComponent("position_size", "Position size", size_cat, 0.15, ""),
            ScoreComponent("sizing_consistency", "Sizing consistency",
                           size_consistency, 0.15,
                           "how uniform your position sizes are"),
            ScoreComponent("not_oversized", "Not oversizing", oversized, 0.10,
                           oversized_detail),
            ScoreComponent("recovery", "Recovery from drawdown", recovery, 0.10,
                           f"recovery factor {d.metric_value('recovery_factor')}"
                           if d.metric_value("recovery_factor") is not None else ""),
        ], "How much damage your losing trades are allowed to do.", n)

    def _consistency(self, d: ScoreInput, n: int) -> ScoreCard:
        pnl_consistency = d.metric_value("consistency")
        size_consistency = d.metric_value("size_consistency")

        # Month-to-month steadiness of expectancy: the spread of monthly
        # expectancy relative to its own average. Needs three months to mean
        # anything, so it simply does not contribute before then.
        monthly = [p.expectancy for p in d.monthly if p.trades >= stats.MIN_SAMPLE_ANY]
        month_consistency = (stats.consistency(monthly)
                             if len(monthly) >= 3 else None)

        # Steady participation: how evenly trades are spread across active days.
        by_day: dict[str, int] = {}
        for f in d.facts:
            by_day[f.entry_date] = by_day.get(f.entry_date, 0) + 1
        cadence = (stats.consistency(list(by_day.values()))
                   if len(by_day) >= stats.MIN_SAMPLE_LOW else None)

        return _card("consistency", "Consistency", [
            ScoreComponent("pnl_spread", "Week-to-week result spread",
                           pnl_consistency, 0.35,
                           "how much your periodic results vary"),
            ScoreComponent("size_spread", "Position size spread",
                           size_consistency, 0.25, ""),
            ScoreComponent("month_to_month", "Month-to-month steadiness",
                           month_consistency, 0.25,
                           f"across {len(monthly)} qualifying months"),
            ScoreComponent("cadence", "Daily trading cadence", cadence, 0.15,
                           f"across {len(by_day)} active days"),
        ], "How repeatable your results are — steadiness, not profitability.", n)

    def _planning(self, d: ScoreInput, n: int) -> ScoreCard:
        rr_cat, rr_n = d.category_mean(CAT_RR)
        patience_cat, _ = d.category_mean(CAT_PATIENCE)
        plan_rate = d.metric_value("plan_rate")
        no_target, _, target_detail = d.behavior_penalty("no_target_defined", 0.60)
        early, _, early_detail = d.behavior_penalty("entering_too_early", 0.30)
        # Share of trades that recorded a planned reward/risk ratio at all —
        # having one is itself the planning behaviour being measured.
        with_rr = sum(1 for f in d.facts if f.risk_reward is not None)
        rr_coverage = (round(with_rr / len(d.facts) * 100, 1) if d.facts else None)

        return _card("planning", "Planning", [
            ScoreComponent("stop_and_target", "Stop and target defined up front",
                           plan_rate, 0.30,
                           f"{plan_rate:.0f}% of reviewed trades"
                           if plan_rate is not None else ""),
            ScoreComponent("reward_risk", "Reward/risk shape", rr_cat, 0.20,
                           f"coach category over {rr_n} reviewed trades"),
            ScoreComponent("patience", "Patience", patience_cat, 0.20, ""),
            ScoreComponent("target_defined", "Target defined", no_target, 0.15,
                           target_detail),
            ScoreComponent("waited_for_confirmation", "Waited for confirmation",
                           early, 0.15, early_detail),
            ScoreComponent("rr_recorded", "Reward/risk recorded at entry",
                           rr_coverage, 0.0,
                           f"{with_rr} of {len(d.facts)} trades"),
        ], "Whether the decisions were made before the position existed.", n)

    def _adaptability(self, d: ScoreInput, n: int) -> ScoreCard:
        """Breadth, not improvement — can this trader make money in more than
        one set of conditions?

        Deliberately distinct from Learning Progress, which measures change over
        time. A trader can be highly adaptable and not improving, or improving
        rapidly within one narrow niche.
        """
        def breadth(attr: str, minimum: int = stats.MIN_SAMPLE_LOW
                    ) -> tuple[float | None, int]:
            groups: dict[str, list[float]] = {}
            for f in d.facts:
                key = getattr(f, attr, None)
                if key:
                    groups.setdefault(str(key), []).append(f.pnl)
            eligible = {k: v for k, v in groups.items() if len(v) >= minimum}
            if len(eligible) < 2:
                return None, len(eligible)
            positive = sum(1 for v in eligible.values()
                           if (stats.expectancy(v) or 0) > 0)
            return round(positive / len(eligible) * 100, 1), len(eligible)

        regime, regime_n = breadth("market_regime")
        session, session_n = breadth("session")
        direction, direction_n = breadth("direction")
        symbol, symbol_n = breadth("symbol")

        return _card("adaptability", "Adaptability", [
            ScoreComponent("regimes", "Profitable across market regimes",
                           regime, 0.35, f"{regime_n} regimes with enough trades"),
            ScoreComponent("sessions", "Profitable across sessions", session, 0.20,
                           f"{session_n} sessions with enough trades"),
            ScoreComponent("directions", "Profitable long and short",
                           direction, 0.25,
                           f"{direction_n} directions with enough trades"),
            ScoreComponent("symbols", "Profitable across instruments", symbol,
                           0.20, f"{symbol_n} symbols with enough trades"),
        ], "Whether your edge survives a change in conditions, or lives in one "
           "narrow niche.", n)

    def _learning(self, d: ScoreInput, n: int) -> ScoreCard:
        """Change over time. Every component needs two comparable periods, so
        this score is honestly unavailable for a trader's first month."""
        def from_trend(key: str) -> float | None:
            trend = d.trends.get(key, Trend.UNKNOWN)
            return {Trend.IMPROVING: 100.0, Trend.STABLE: 60.0,
                    Trend.DECLINING: 15.0}.get(trend)

        # Behavioral trends: what share of the assessable behaviors that have a
        # measured direction are moving the right way.
        directed = [b for b in d.behaviors.values()
                    if b.assessable and b.trend in (Trend.IMPROVING, Trend.DECLINING)]
        behavior_shift = (
            round(sum(1 for b in directed if b.trend is Trend.IMPROVING)
                  / len(directed) * 100, 1) if directed else None)

        return _card("learning", "Learning Progress", [
            ScoreComponent("mistake_trend", "Mistakes per trade falling",
                           from_trend("mistake_rate"), 0.25,
                           d.trends.get("mistake_rate", Trend.UNKNOWN).value),
            ScoreComponent("process_trend", "Process score rising",
                           from_trend("avg_process_score"), 0.25,
                           d.trends.get("avg_process_score", Trend.UNKNOWN).value),
            ScoreComponent("clean_trend", "Clean trade rate rising",
                           from_trend("clean_trade_rate"), 0.20,
                           d.trends.get("clean_trade_rate", Trend.UNKNOWN).value),
            ScoreComponent("expectancy_trend", "Expectancy rising",
                           from_trend("expectancy"), 0.15,
                           d.trends.get("expectancy", Trend.UNKNOWN).value),
            ScoreComponent("behavior_shift", "Habits moving the right way",
                           behavior_shift, 0.15,
                           f"{len(directed)} behaviors with a measured direction"),
        ], "Whether the same mistakes keep happening, measured across months.", n)

    def _decision_quality(self, d: ScoreInput, n: int) -> ScoreCard:
        process = d.metric_value("avg_process_score")
        trend_cat, _ = d.category_mean(CAT_TREND)
        plan, _, plan_detail = d.behavior_penalty("ignoring_the_plan", 0.20)

        # Share of trades taken on setups the analysis graded good or excellent.
        graded = [f for f in d.facts if f.setup_quality
                  and f.setup_quality != "unknown"]
        good = sum(1 for f in graded if f.setup_quality in ("excellent", "good"))
        selectivity = (round(good / len(graded) * 100, 1)
                       if len(graded) >= stats.MIN_SAMPLE_ANY else None)

        # Calibration: do higher-confidence entries actually win more often?
        calibration = self._calibration(d.facts)

        return _card("decision_quality", "Decision Quality", [
            ScoreComponent("process_score", "Coach process score", process, 0.35,
                           f"average over {d.metric_sample('avg_process_score')} "
                           f"reviewed trades"),
            ScoreComponent("selectivity", "Trading only good setups", selectivity,
                           0.25, f"{good} of {len(graded)} graded entries"),
            ScoreComponent("followed_plan", "Not overriding the analysis", plan,
                           0.20, plan_detail),
            ScoreComponent("trend_alignment", "Trend alignment", trend_cat, 0.10, ""),
            ScoreComponent("calibration", "Confidence calibration", calibration,
                           0.10,
                           "whether your higher-confidence entries really do "
                           "win more often"),
        ], "Whether the trades you chose were the right ones to take.", n)

    @staticmethod
    def _calibration(facts: tuple[TradeFact, ...]) -> float | None:
        """Do high-confidence entries win more than low-confidence ones?

        Splits at the trader's own median confidence and scores the gap: a
        20-point win-rate advantage for the high half is full marks, no
        advantage is 50, and an inverted relationship — the low-confidence half
        winning more — scores toward zero, which is a genuinely important thing
        to know about a scoring model that has been overridden too often.
        """
        scored = [f for f in facts if f.confidence is not None]
        if len(scored) < stats.MIN_SAMPLE_MEDIUM:
            return None
        cutoff = stats.median([f.confidence for f in scored])
        if cutoff is None:
            return None
        high = [f.pnl for f in scored if f.confidence >= cutoff]
        low = [f.pnl for f in scored if f.confidence < cutoff]
        if len(high) < stats.MIN_SAMPLE_ANY or len(low) < stats.MIN_SAMPLE_ANY:
            return None
        high_wr = stats.win_rate(high)
        low_wr = stats.win_rate(low)
        if high_wr is None or low_wr is None:
            return None
        return _scale(high_wr - low_wr, -0.20, 0.20)
