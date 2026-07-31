"""RiskIntelligence — how much damage the trader is exposed to, and from where.

Distinct from `optionspilot/risk/manager.py`, which is a *gate*: it decides
whether the next entry is allowed. This module never gates anything. It looks
backwards at what actually happened and reports the shape of the risk that was
taken — drawdown, tail losses, concentration, sizing dispersion, stop coverage —
so the trader can see the exposure their rules have been producing.

Keeping the two apart matters. A gate has to be conservative and fast and is
consulted before every entry; an analysis can be thorough and slow and runs once
per snapshot. Merging them would put a heavyweight statistical pass on the
trading hot path, and would tempt someone to let an analysis result block a
trade — which is a trading-behaviour change this milestone deliberately does not
make.

Every figure here is measured over closed trades. Nothing is modelled forward:
there is no value-at-risk, no Monte Carlo, no ruin probability, because all
three would require assuming a return distribution this system has no basis to
assume.
"""

from __future__ import annotations

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import Evidence, Metric, Severity

# A day this far below the trader's own typical losing day is called out.
BAD_DAY_MULTIPLE = 2.0
# Concentration threshold: one symbol carrying more than this share of trades.
CONCENTRATION_SHARE = 0.40


def _observation(key: str, severity: Severity, headline: str,
                 evidence: tuple[Evidence, ...]) -> dict:
    return {
        "key": key, "severity": severity.value, "headline": headline,
        "evidence": [e.to_dict() for e in evidence],
    }


class RiskIntelligence:
    """Backward-looking risk analysis over a set of closed trades."""

    def analyze(self, facts: tuple[TradeFact, ...] | list[TradeFact],
                metrics: dict[str, Metric]) -> dict:
        facts = list(facts)
        if not facts:
            return {"assessable": False,
                    "reason": "No closed trades yet.",
                    "observations": [], "distribution": {}, "concentration": []}

        pnls = [f.pnl for f in facts]
        losses = [p for p in pnls if p < 0]
        outlays = [f.outlay for f in facts if f.outlay > 0]
        rs = [f.r_multiple for f in facts if f.r_multiple is not None]

        # Per-day aggregation: a trader's real risk unit is the day, not the
        # trade. Three small losses inside an hour is one bad decision.
        by_day: dict[str, float] = {}
        for f in facts:
            by_day[f.entry_date] = by_day.get(f.entry_date, 0.0) + f.pnl
        day_pnls = list(by_day.values())
        losing_days = [p for p in day_pnls if p < 0]

        observations: list[dict] = []

        # ── drawdown ──────────────────────────────────────────────────────
        curve = stats.equity_curve(pnls)
        peak = max(curve)
        current_dd = peak - curve[-1]
        max_dd = stats.max_drawdown(pnls)
        if max_dd > 0:
            recovery = stats.recovery_factor(pnls)
            severity = (Severity.SERIOUS if recovery is not None and recovery < 1
                        else Severity.INFO)
            observations.append(_observation(
                "drawdown", severity,
                f"Largest peak-to-trough fall was {max_dd:,.2f}"
                + (f"; you are {current_dd:,.2f} below your high-water mark now."
                   if current_dd > 0 else "; you are at your high-water mark."),
                (Evidence("max drawdown", round(max_dd, 2), len(facts)),
                 Evidence("current drawdown", round(current_dd, 2), len(facts)),
                 Evidence("recovery factor",
                          round(recovery, 2) if recovery is not None else None,
                          len(facts),
                          "net profit per unit of drawdown; below 1.0 means the "
                          "drawdown outweighs everything made since"))))

        # ── tail losses ───────────────────────────────────────────────────
        if len(losses) >= stats.MIN_SAMPLE_LOW:
            worst_decile = stats.percentile(losses, 0.10)
            typical_loss = stats.median(losses)
            ratio = (worst_decile / typical_loss
                     if worst_decile is not None and typical_loss else None)
            # Only worth saying when the tail is genuinely fatter than the body.
            # A ratio of 1.0 means every loss is the same size, which is the
            # opposite of a tail risk and reads as noise on the page.
            if ratio is not None and ratio >= 1.5:
                observations.append(_observation(
                    "tail_loss",
                    Severity.MODERATE if ratio >= 3 else Severity.INFO,
                    f"Your worst losses are {ratio:.1f}× your typical one — "
                    f"{worst_decile:,.2f} against a median of {typical_loss:,.2f}.",
                    (Evidence("worst-decile loss", round(worst_decile, 2),
                              len(losses)),
                     Evidence("median loss", round(typical_loss, 2), len(losses)),
                     Evidence("largest single loss", round(min(losses), 2),
                              len(losses)))))

        # ── worst days ────────────────────────────────────────────────────
        if len(losing_days) >= stats.MIN_SAMPLE_ANY:
            typical_bad_day = stats.median(losing_days) or 0.0
            threshold = typical_bad_day * BAD_DAY_MULTIPLE
            outliers = {d: p for d, p in by_day.items() if p <= threshold}
            if outliers:
                worst_day = min(by_day, key=lambda d: by_day[d])
                observations.append(_observation(
                    "bad_days",
                    Severity.MODERATE if len(outliers) >= 2 else Severity.INFO,
                    f"{len(outliers)} day(s) lost more than twice your typical "
                    f"losing day; the worst was {by_day[worst_day]:,.2f} on "
                    f"{worst_day}.",
                    (Evidence("outsized losing days", len(outliers), len(by_day)),
                     Evidence("typical losing day", round(typical_bad_day, 2),
                              len(losing_days)),
                     Evidence("worst day", round(by_day[worst_day], 2),
                              len(by_day), worst_day))))

        # ── sizing dispersion ─────────────────────────────────────────────
        if len(outlays) >= stats.MIN_SAMPLE_LOW:
            typical = stats.median(outlays) or 0.0
            largest = max(outlays)
            spread = largest / typical if typical else None
            if spread is not None and spread >= 2.0:
                observations.append(_observation(
                    "sizing_dispersion", Severity.MODERATE,
                    f"Your largest position was {spread:.1f}× your typical one "
                    f"({largest:,.0f} against {typical:,.0f}). Risk is only "
                    f"controlled if size is.",
                    (Evidence("largest premium outlay", round(largest, 2),
                              len(outlays)),
                     Evidence("typical premium outlay", round(typical, 2),
                              len(outlays)),
                     Evidence("sizing consistency",
                              metrics["size_consistency"].value, len(outlays),
                              "100 is identical every trade"))))

        # ── stop coverage ─────────────────────────────────────────────────
        stop_rate = metrics["stop_discipline_rate"]
        if stop_rate.value is not None and stop_rate.sample >= stats.MIN_SAMPLE_ANY:
            observations.append(_observation(
                "stop_coverage",
                Severity.SERIOUS if stop_rate.value < 70
                else Severity.POSITIVE if stop_rate.value >= 95 else Severity.MINOR,
                f"{stop_rate.value:.0f}% of reviewed trades ran with a resting "
                f"stop that was never widened.",
                (Evidence("stop discipline", stop_rate.value, stop_rate.sample,
                          "share of reviewed trades protected throughout"),)))

        # ── concentration ─────────────────────────────────────────────────
        by_symbol: dict[str, list[TradeFact]] = {}
        for f in facts:
            by_symbol.setdefault(f.symbol or "?", []).append(f)
        concentration = sorted(
            ({"symbol": sym,
              "trades": len(group),
              "share": round(len(group) / len(facts), 4),
              "pnl": round(sum(g.pnl for g in group), 2)}
             for sym, group in by_symbol.items()),
            key=lambda row: -row["trades"])[:10]
        if concentration and concentration[0]["share"] >= CONCENTRATION_SHARE \
                and len(by_symbol) > 1:
            top = concentration[0]
            observations.append(_observation(
                "concentration", Severity.MINOR,
                f"{top['symbol']} accounts for {top['share']:.0%} of your "
                f"trades and {top['pnl']:+,.2f} of P/L — your record is largely "
                f"a bet on one instrument.",
                (Evidence("share of trades in the top symbol", top["share"],
                          len(facts), f"{top['trades']} of {len(facts)}"),
                 Evidence("P/L from the top symbol", top["pnl"], top["trades"]))))

        distribution = {
            "r_multiple": {
                "measured": len(rs), "of": len(facts),
                "p10": _r(stats.percentile(rs, 0.10)),
                "median": _r(stats.median(rs)),
                "p90": _r(stats.percentile(rs, 0.90)),
                "mean": _r(stats.mean(rs)),
            },
            "pnl": {
                "p10": round(stats.percentile(pnls, 0.10) or 0.0, 2),
                "median": round(stats.median(pnls) or 0.0, 2),
                "p90": round(stats.percentile(pnls, 0.90) or 0.0, 2),
            },
            "daily": {
                "days": len(by_day),
                "worst": round(min(day_pnls), 2),
                "best": round(max(day_pnls), 2),
                "median": round(stats.median(day_pnls) or 0.0, 2),
                "losing_days": len(losing_days),
            },
        }

        observations.sort(key=lambda o: -Severity(o["severity"]).rank)
        return {
            "assessable": True,
            "observations": observations,
            "distribution": distribution,
            "concentration": concentration,
            "notes": [] if rs else [
                "No trade recorded a protective stop level, so R multiples "
                "could not be measured. Place resting stops and this becomes "
                "the most useful number on the page."],
        }


def _r(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
