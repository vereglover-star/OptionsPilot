"""PatternEngine — discovers where a trader's edge actually lives.

The brief was "automatically discover patterns instead of hardcoding them", and
the distinction this module draws is between the *dimensions* and the *patterns*.

The dimensions are declared, and have to be: a dimension is a way of slicing a
trade, and the set of ways is fixed by what a trade records. Inventing new
dimensions would mean inventing new data. What is emphatically **not** declared
is any statement about the trader. Nothing here says "mornings are good" or
"0DTE is bad". The engine buckets every trade along every dimension, measures
each bucket against that trader's own baseline, and keeps whatever survives a
sample floor and a significance test. A trader who genuinely trades best on
Friday afternoons in high IV will be told so; the same code says the opposite to
the trader for whom that is true.

Three guards, all of which exist because a naive version of this is a
pattern-generating machine that is confidently wrong:

* **Baseline is the trader, never a norm.** `edge` is the bucket's win rate
  minus the rest of that trader's own trades. There is no external "good win
  rate" anywhere in this file.
* **Significance, not just size.** Every candidate faces a two-proportion test
  against the complement of its own bucket. Four wins out of five is a 100%
  win rate and means nothing; `p_value` is what keeps it off the dashboard.
* **Multiplicity is corrected for, not merely acknowledged.** Around seventy
  bucket tests run per analysis, so a raw p<0.20 threshold produces about
  fourteen "patterns" from pure noise — measured: `scripts/intelligence_benchmark.py`
  on 100 random trades reported thirteen, which is the expected count almost
  exactly. Every candidate now goes through a Benjamini–Hochberg false-discovery
  correction over the whole run (`FDR_Q`), so what survives is a set whose
  expected false-positive share is bounded rather than a list of whichever
  buckets happened to look extreme.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import Confidence, Evidence, Pattern

# A bucket needs this many trades before it is even considered.
MIN_BUCKET_TRADES = 5
# The whole analysis needs this much history before any pattern is reported.
MIN_TOTAL_TRADES = 15
# The complement must also be substantial, or "this bucket differs from the
# other three trades" is not a comparison worth making.
MIN_COMPLEMENT_TRADES = 5
# Win-rate edge below this is noise regardless of what the p-value says.
MIN_EDGE = 0.10
# Nothing above this raw p-value is considered at all — a coarse pre-filter, not
# the real gate. The real gate is the false-discovery correction below.
MAX_P_VALUE = 0.20
# Benjamini–Hochberg target false-discovery rate: of the patterns reported, at
# most ~10% are expected to be noise. Chosen rather than a Bonferroni family-wise
# bound because Bonferroni over seventy tests demands p<0.0007, which at the
# sample sizes a discretionary trader actually produces would report nothing,
# ever — a system that can never find a pattern is not more honest, it is just
# useless.
FDR_Q = 0.10
# How many patterns of each kind reach the caller.
MAX_PATTERNS = 12


@dataclass(frozen=True, slots=True)
class Dimension:
    """One way of slicing a trade. `extract` returns the bucket label for a
    fact, or None when that fact cannot be placed — a trade with no recorded
    delta is absent from the delta analysis rather than lumped into 'unknown',
    which would create a spurious bucket out of a data gap."""

    name: str
    label: str
    extract: Callable[[TradeFact], str | None]


def _bucketed(value: float | None, edges: tuple[tuple[float, str], ...],
              above: str) -> str | None:
    if value is None:
        return None
    for edge, label in edges:
        if value < edge:
            return label
    return above


def _hour_block(f: TradeFact) -> str | None:
    """Session blocks a trader actually thinks in, not clock hours. The opening
    range, the morning trend, the midday drift and the closing hour behave
    differently enough that splitting them at 10:00/12:00/15:00 says more than
    thirteen one-hour buckets each holding three trades."""
    if f.hour_et is None:
        return None
    minute = f.minute_et or 0
    total = f.hour_et * 60 + minute
    if total < 9 * 60 + 30:
        return "Pre-market"
    if total < 9 * 60 + 45:
        return "Opening 15 min"
    if total < 11 * 60:
        return "Morning (9:45–11:00)"
    if total < 14 * 60:
        return "Midday (11:00–14:00)"
    if total < 15 * 60 + 30:
        return "Afternoon (14:00–15:30)"
    if total < 16 * 60:
        return "Closing 30 min"
    return "After hours"


def _hold_bucket(f: TradeFact) -> str | None:
    if f.hold_minutes <= 0:
        return None
    return _bucketed(f.hold_minutes,
                     ((15, "Under 15 min"), (60, "15–60 min"),
                      (390, "Intraday (1–6.5 h)"), (390 * 2, "Overnight")),
                     "Multi-day")


# NOTE — exit reason is deliberately NOT a dimension.
#
# It was one, and it produced the strongest-looking patterns in the whole
# system: "how it ended — stop loss: 0% win rate over 51 trades against 100%
# elsewhere, p<0.0001". Which is true, and circular. A trade that ended at its
# stop is a losing trade BY DEFINITION, so bucketing on the exit and then
# measuring the win rate can only ever rediscover the definition — with a
# crushing p-value, at the top of the ranking, pushing every real finding down.
# It also generated the recommendation "stop taking stop-loss trades", which is
# not advice anyone can act on.
#
# The rule this encodes: a dimension must describe a CHOICE MADE BEFORE OR
# DURING THE TRADE (symbol, time, strike, size, hold), never a consequence of
# how it turned out. Exit reason belongs in the journal and in `experience`'s
# failure-mode reporting, which present it as description rather than as edge.


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("weekday", "Day of week", lambda f: f.weekday or None),
    Dimension("hour_block", "Time of day", _hour_block),
    Dimension("session", "Market session", lambda f: f.session or None),
    Dimension("symbol", "Symbol", lambda f: f.symbol or None),
    Dimension("direction", "Direction", lambda f: f.direction or None),
    Dimension("strategy", "Strategy", lambda f: f.strategy or None),
    Dimension("managed_by", "Managed by",
              lambda f: {"ai": "AI-managed", "manual": "Your own entries"}.get(
                  f.managed_by)),
    Dimension("timeframe", "Entry timeframe", lambda f: f.timeframe or None),
    Dimension("setup_quality", "Setup grade",
              lambda f: f.setup_quality if f.setup_quality
              and f.setup_quality != "unknown" else None),
    Dimension("market_regime", "Market regime",
              lambda f: f.market_regime if f.market_regime
              and "unknown" not in f.market_regime else None),
    Dimension("htf_trend", "Higher-timeframe trend",
              lambda f: f.htf_trend if (f.htf_trend or "").lower()
              in ("up", "down", "neutral", "ranging") else None),
    Dimension("dte_bucket", "Days to expiration",
              lambda f: _bucketed(
                  None if f.dte is None else float(f.dte),
                  ((1, "0 DTE"), (8, "1–7 DTE"), (15, "8–14 DTE"),
                   (31, "15–30 DTE"), (61, "31–60 DTE")), "60+ DTE")),
    Dimension("delta_bucket", "Strike delta",
              lambda f: _bucketed(
                  None if f.delta is None else abs(f.delta),
                  ((0.20, "Far OTM (<0.20)"), (0.35, "OTM (0.20–0.35)"),
                   (0.55, "Near the money (0.35–0.55)")), "ITM (0.55+)")),
    Dimension("iv_bucket", "Implied volatility",
              lambda f: _bucketed(f.iv,
                                  ((0.30, "Low IV (<30%)"),
                                   (0.60, "Medium IV (30–60%)")),
                                  "High IV (60%+)")),
    Dimension("confidence_bucket", "Entry confidence",
              lambda f: _bucketed(f.confidence,
                                  ((40, "Under 40%"), (60, "40–60%"),
                                   (80, "60–80%")), "80%+")),
    Dimension("rvol_bucket", "Relative volume",
              lambda f: _bucketed(f.rvol,
                                  ((0.7, "Quiet (<0.7×)"), (1.5, "Normal (0.7–1.5×)")),
                                  "Busy (1.5×+)")),
    Dimension("adx_bucket", "Trend strength (ADX)",
              lambda f: _bucketed(f.adx, ((20, "Ranging (ADX<20)"),
                                          (35, "Trending (ADX 20–35)")),
                                  "Strong trend (ADX 35+)")),
    Dimension("hold_bucket", "Hold time", _hold_bucket),
    Dimension("size_bucket", "Position size",
              lambda f: None),   # replaced per-run: needs the trader's own median
)


def _size_dimension(facts: tuple[TradeFact, ...]) -> Dimension:
    """Position size only means anything relative to the trader's own typical
    size, so this dimension is built per run rather than declared as constants.
    A $200 position is large for one account and a rounding error for another."""
    outlays = [f.outlay for f in facts if f.outlay > 0]
    typical = stats.median(outlays)

    def extract(f: TradeFact) -> str | None:
        if not typical or f.outlay <= 0:
            return None
        ratio = f.outlay / typical
        if ratio < 0.6:
            return "Smaller than usual"
        if ratio < 1.5:
            return "Your usual size"
        if ratio < 2.5:
            return "Larger than usual"
        return "Much larger than usual"

    return Dimension("size_bucket", "Position size vs. your usual", extract)


def _pattern_confidence(trades: int, p_value: float | None,
                        edge: float) -> Confidence:
    """Sample, significance and effect size all have to agree.

    Set conservatively on purpose: roughly eighteen dimensions are tested, so a
    handful of p<0.05 results are expected from chance alone in any sufficiently
    varied history. Requiring p<0.01 *and* thirty trades for HIGH keeps the
    dashboard's strongest claims from being multiplicity artefacts.
    """
    if p_value is None or trades < MIN_BUCKET_TRADES:
        return Confidence.NONE
    if p_value <= 0.01 and trades >= stats.MIN_SAMPLE_HIGH and abs(edge) >= 0.15:
        return Confidence.HIGH
    if p_value <= 0.05 and trades >= stats.MIN_SAMPLE_MEDIUM:
        return Confidence.MEDIUM
    if p_value <= MAX_P_VALUE:
        return Confidence.LOW
    return Confidence.NONE


def _summary(dim: Dimension, bucket: str, kind: str, trades: int,
             win_rate: float, baseline: float, expectancy: float,
             baseline_expectancy: float) -> str:
    direction = "better" if kind == "strength" else "worse"
    return (
        f"{dim.label} — {bucket}: {win_rate:.0%} win rate over {trades} trades "
        f"against {baseline:.0%} elsewhere, and {expectancy:+.2f} per trade "
        f"against {baseline_expectancy:+.2f}. You do measurably {direction} here."
    )


class PatternEngine:
    """Sweeps every dimension and reports the buckets that survive."""

    def analyze(self, facts: tuple[TradeFact, ...] | list[TradeFact]
                ) -> tuple[Pattern, ...]:
        facts = tuple(facts)
        total = len(facts)
        if total < MIN_TOTAL_TRADES:
            return ()

        dimensions = tuple(
            _size_dimension(facts) if d.name == "size_bucket" else d
            for d in DIMENSIONS)

        # Two passes. The first EVALUATES every bucket and records its p-value —
        # including buckets that will be discarded — because the count of tests
        # performed is what the false-discovery correction needs. Discarding
        # early and then correcting over the survivors would understate the
        # multiplicity and defeat the whole point.
        candidates: list[Pattern] = []
        tests = 0
        for dim in dimensions:
            buckets: dict[str, list[TradeFact]] = {}
            for fact in facts:
                key = dim.extract(fact)
                if key:
                    buckets.setdefault(str(key), []).append(fact)
            # A dimension with only one populated bucket says nothing: every
            # trade is in it, so there is no comparison to make.
            if len(buckets) < 2:
                continue
            placed = {f.trade_id for group in buckets.values() for f in group}
            for name, group in buckets.items():
                pattern, tested = self._evaluate(dim, name, group, facts, placed)
                tests += 1 if tested else 0
                if pattern is not None:
                    candidates.append(pattern)

        found = _survivors(candidates, tests)

        # Rank by how much the finding matters: confidence first (a
        # high-confidence 12-point edge beats a low-confidence 40-point one),
        # then the size of the edge, then the sample behind it.
        found.sort(key=lambda p: (-p.confidence.value, -abs(p.edge), -p.trades))
        strengths = [p for p in found if p.kind == "strength"][:MAX_PATTERNS]
        weaknesses = [p for p in found if p.kind == "weakness"][:MAX_PATTERNS]
        merged = strengths + weaknesses
        merged.sort(key=lambda p: (-p.confidence.value, -abs(p.edge), -p.trades))
        return tuple(merged)

    def _evaluate(self, dim: Dimension, name: str, group: list[TradeFact],
                  facts: tuple[TradeFact, ...], placed: set[str]
                  ) -> tuple[Pattern | None, bool]:
        """Returns (pattern or None, whether a statistical test was performed).

        The second element is what the false-discovery correction counts: a
        bucket that was too small to test does not add to the multiplicity, but
        one that was tested and rejected on effect size very much does.
        """
        if len(group) < MIN_BUCKET_TRADES:
            return None, False
        # The comparison set is the trades that this dimension could place but
        # placed elsewhere — NOT every other trade. Comparing "high IV" against
        # trades that never recorded IV would measure data coverage, not skill.
        group_ids = {f.trade_id for f in group}
        rest = [f for f in facts
                if f.trade_id in placed and f.trade_id not in group_ids]
        if len(rest) < MIN_COMPLEMENT_TRADES:
            return None, False

        group_pnls = [f.pnl for f in group]
        rest_pnls = [f.pnl for f in rest]
        wins = sum(1 for p in group_pnls if p > 0)
        rest_wins = sum(1 for p in rest_pnls if p > 0)
        win_rate = wins / len(group)
        baseline = rest_wins / len(rest)
        edge = win_rate - baseline

        expectancy = stats.expectancy(group_pnls) or 0.0
        baseline_expectancy = stats.expectancy(rest_pnls) or 0.0

        p_value = stats.two_proportion_p(wins, len(group), rest_wins, len(rest))
        if p_value is None:
            # No usable variance (all wins or all losses across both groups) —
            # nothing was tested, so nothing is added to the multiplicity.
            return None, False
        # From here the test HAS been performed and counts toward the
        # false-discovery correction, whatever we decide about this bucket.
        if abs(edge) < MIN_EDGE or p_value > MAX_P_VALUE:
            return None, True
        confidence = _pattern_confidence(len(group), p_value, edge)
        if confidence is Confidence.NONE:
            return None, True

        # Win rate and expectancy must agree on the sign. A bucket that wins
        # more often while losing more money is not a strength, and calling it
        # one would send the trader toward their most expensive habit.
        if (edge > 0) != (expectancy > baseline_expectancy):
            return None, True

        kind = "strength" if edge > 0 else "weakness"
        interval = stats.wilson_interval(wins, len(group))
        evidence = (
            Evidence("win rate in this bucket", round(win_rate, 4), len(group),
                     f"{wins} wins in {len(group)} trades",
                     tuple(f.trade_id for f in group[:25])),
            Evidence("win rate everywhere else", round(baseline, 4), len(rest),
                     f"{rest_wins} wins in {len(rest)} comparable trades"),
            Evidence("expectancy here", round(expectancy, 2), len(group),
                     f"vs {baseline_expectancy:+.2f} elsewhere"),
            Evidence("statistical significance", round(p_value, 4), len(group),
                     "two-proportion test against your other trades on this "
                     "dimension; lower is stronger"),
        )
        if interval:
            evidence += (Evidence(
                "95% confidence interval on that win rate",
                round(interval[0], 3), len(group),
                f"{interval[0]:.0%} to {interval[1]:.0%} — the range the true "
                f"rate plausibly sits in at this sample size"),)

        return Pattern(
            dimension=dim.name, dimension_label=dim.label, bucket=name,
            kind=kind, trades=len(group), wins=wins, win_rate=win_rate,
            baseline_win_rate=baseline, edge=edge,
            expectancy=expectancy, baseline_expectancy=baseline_expectancy,
            pnl=round(sum(group_pnls), 2), confidence=confidence,
            p_value=p_value,
            summary=_summary(dim, name, kind, len(group), win_rate, baseline,
                             expectancy, baseline_expectancy),
            evidence=evidence,
        ), True


def _survivors(candidates: list[Pattern], tests: int) -> list[Pattern]:
    """Benjamini–Hochberg false-discovery control over one analysis run.

    Sort the candidate p-values ascending; the largest rank `k` whose p-value is
    at or below `k/m · q` sets the cutoff, and everything at or below it
    survives. `m` is every test PERFORMED in the run, not just the candidates
    that got this far — using the survivor count would understate the number of
    chances noise had, which is the whole thing being corrected for.

    Without this, ~70 bucket tests at a raw p≤0.20 threshold yielded thirteen
    "patterns" from 100 uniformly random trades. With it, the same input yields
    none, and a genuinely concentrated edge still comes through.
    """
    if not candidates:
        return []
    m = max(tests, len(candidates))
    ranked = sorted(candidates, key=lambda p: p.p_value)
    cutoff_rank = 0
    for i, pattern in enumerate(ranked, start=1):
        if pattern.p_value <= (i / m) * FDR_Q:
            cutoff_rank = i
    return ranked[:cutoff_rank]
