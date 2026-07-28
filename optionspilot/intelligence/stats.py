"""Statistical primitives for the intelligence layer.

Pure functions over plain sequences — no domain types, no I/O, no clock. This is
the only module in `intelligence/` allowed to contain a formula; every engine
above it composes these rather than re-deriving arithmetic, so "profit factor"
means exactly one thing across the whole system.

Two deliberate positions:

- **Every function tolerates an empty or degenerate input** and returns None
  rather than raising or inventing. A trader with one trade must not crash a
  dashboard, and must not be shown a standard deviation of 0 presented as
  perfect consistency.
- **Nothing here is annualised.** `sharpe_like` is a *per-trade* ratio and is
  named to say so. Annualising a handful of discretionary option trades against
  a 252-day convention produces an impressive number with no meaning, and this
  system does not ship numbers it can't defend.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from optionspilot.intelligence.models import Confidence, Trend

# Sample-size floors. These are the single source of truth for "is there enough
# here to say anything", and every engine imports them rather than choosing its
# own — otherwise the dashboard would call a pattern significant while the
# report writer called the same pattern noise.
MIN_SAMPLE_ANY = 3        # below this, an engine reports nothing at all
MIN_SAMPLE_LOW = 5        # enough for a low-confidence observation
MIN_SAMPLE_MEDIUM = 12    # enough for a medium-confidence claim
MIN_SAMPLE_HIGH = 30      # enough for a high-confidence claim

# A relative move smaller than this is noise, not a trend, for ratio metrics.
TREND_EPSILON = 0.05


def mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else None


def median(xs: Sequence[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def pstdev(xs: Sequence[float]) -> float | None:
    """Population standard deviation. None below two points — a single
    observation has no spread, and reporting 0.0 would read as perfect
    consistency."""
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))


def percentile(xs: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile, `q` in 0..1."""
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def win_rate(pnls: Sequence[float]) -> float | None:
    """Fraction of strictly-positive outcomes. A scratch counts as a non-win,
    matching `TradeRecord.is_win`."""
    if not pnls:
        return None
    return sum(1 for p in pnls if p > 0) / len(pnls)


def expectancy(pnls: Sequence[float]) -> float | None:
    """Average P/L per trade — the single most honest headline number, because
    it folds win rate and payoff size into one figure that survives a change in
    either."""
    return mean(pnls)


def profit_factor(pnls: Sequence[float]) -> float | None:
    """Gross wins / gross losses.

    Returns `inf` for a sample with wins and no losses (a true, if fragile,
    reading — the caller renders it, `models._finite` keeps it out of JSON) and
    None when there is nothing to divide at all.
    """
    if not pnls:
        return None
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return math.inf if gross_win > 0 else None
    return gross_win / gross_loss


def avg_win(pnls: Sequence[float]) -> float | None:
    return mean([p for p in pnls if p > 0])


def avg_loss(pnls: Sequence[float]) -> float | None:
    """Mean of losing trades, returned NEGATIVE so sign conventions never have
    to be remembered at a call site."""
    return mean([p for p in pnls if p < 0])


def payoff_ratio(pnls: Sequence[float]) -> float | None:
    """Average winner / |average loser| — the "how much bigger are my winners"
    number that pairs with win rate."""
    w, loss = avg_win(pnls), avg_loss(pnls)
    if w is None or loss is None or loss == 0:
        return None
    return w / abs(loss)


def equity_curve(pnls: Sequence[float]) -> list[float]:
    """Cumulative P/L, starting at 0.0. The curve every drawdown figure is
    measured on."""
    out, running = [0.0], 0.0
    for p in pnls:
        running += p
        out.append(running)
    return out


def max_drawdown(pnls: Sequence[float]) -> float:
    """Largest peak-to-trough decline of the cumulative P/L curve, as a positive
    number in account currency. 0.0 for a curve that never declines."""
    peak, worst = 0.0, 0.0
    for point in equity_curve(pnls):
        peak = max(peak, point)
        worst = max(worst, peak - point)
    return worst


def recovery_factor(pnls: Sequence[float]) -> float | None:
    """Net profit / max drawdown — how much profit each unit of pain bought.

    None when there was no drawdown to recover from (undefined, not infinite
    skill) and None on an empty sample.
    """
    if not pnls:
        return None
    dd = max_drawdown(pnls)
    if dd <= 0:
        return None
    return sum(pnls) / dd


def sharpe_like(pnls: Sequence[float]) -> float | None:
    """Mean P/L divided by its standard deviation — a **per-trade** reward-to-
    variability ratio.

    This is not the Sharpe ratio: there is no risk-free rate and no annualisation,
    because neither is defensible over an irregular number of discretionary
    option trades. It answers "how large is my average result relative to how
    much it bounces around", which is the question that actually matters here.
    """
    sd = pstdev(pnls)
    mu = mean(pnls)
    if sd is None or mu is None or sd == 0:
        return None
    return mu / sd


def consistency(values: Sequence[float]) -> float | None:
    """A 0–100 reading of how *repeatable* a series is, from its coefficient of
    variation.

    100 means every observation is identical; the score decays as spread grows
    relative to the mean. Uses |mean| so a losing-but-steady series is still
    scored on its steadiness — this measures repeatability, not profitability,
    and combining the two would make it impossible to tell a consistent loser
    from an erratic winner.
    """
    if len(values) < 2:
        return None
    sd = pstdev(values)
    mu = mean(values)
    if sd is None or mu is None:
        return None
    if mu == 0:
        return 0.0 if sd > 0 else 100.0
    cv = sd / abs(mu)
    return round(max(0.0, min(100.0, 100.0 / (1.0 + cv))), 1)


def wilson_interval(successes: int, n: int, z: float = 1.96
                    ) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves at the small samples this system actually sees — a 3-of-4 win rate
    must not be reported as "75% ± 42%" spilling past 100%.
    """
    if n <= 0 or successes < 0 or successes > n:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _norm_sf(x: float) -> float:
    """Upper-tail probability of the standard normal, via erfc. Avoids pulling
    in scipy for one function."""
    return 0.5 * math.erfc(x / math.sqrt(2))


def two_proportion_p(successes_a: int, n_a: int,
                     successes_b: int, n_b: int) -> float | None:
    """Two-sided p-value for "these two win rates differ", pooled z-test.

    Used to keep `patterns.py` from announcing that four Tuesday trades prove
    anything. Returns None when either group is empty or the pooled proportion
    is degenerate (all wins or all losses across both groups — the test has no
    variance to work with and the honest answer is "can't tell").
    """
    if n_a <= 0 or n_b <= 0:
        return None
    p_pool = (successes_a + successes_b) / (n_a + n_b)
    if p_pool in (0.0, 1.0):
        return None
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None
    z = (successes_a / n_a - successes_b / n_b) / se
    return min(1.0, 2 * _norm_sf(abs(z)))


def linear_slope(values: Sequence[float]) -> float | None:
    """Ordinary least-squares slope of `values` against their index — "how much
    does this metric move per period". None below two points."""
    ys = [v for v in values if v is not None and math.isfinite(v)]
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def trend_of(values: Sequence[float], *, higher_is_better: bool = True,
             epsilon: float | None = None) -> Trend:
    """Classify a series as improving / stable / declining.

    Compares the slope against a scale-relative epsilon so a metric measured in
    dollars and one measured in percent are judged on the same terms, and a
    metric that wobbles by a fraction of a percent per period is correctly
    called stable rather than "improving".
    """
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if len(clean) < 2:
        return Trend.UNKNOWN
    slope = linear_slope(clean)
    if slope is None:
        return Trend.UNKNOWN
    scale = max(abs(mean(clean) or 0.0), 1e-9)
    threshold = (epsilon if epsilon is not None else TREND_EPSILON) * scale
    if abs(slope) < threshold:
        return Trend.STABLE
    improving = slope > 0 if higher_is_better else slope < 0
    return Trend.IMPROVING if improving else Trend.DECLINING


def sample_confidence(n: int) -> Confidence:
    """Map a raw sample size to a confidence band.

    The single place this mapping lives. Note the floors are *necessary but not
    sufficient*: `patterns.py` additionally requires statistical significance,
    and `behavior.py` additionally requires a minimum occurrence count, so a
    30-trade sample does not automatically confer high confidence on a one-off.
    """
    if n >= MIN_SAMPLE_HIGH:
        return Confidence.HIGH
    if n >= MIN_SAMPLE_MEDIUM:
        return Confidence.MEDIUM
    if n >= MIN_SAMPLE_LOW:
        return Confidence.LOW
    return Confidence.NONE


def combine_confidence(*levels: Confidence) -> Confidence:
    """The weakest link. A conclusion drawn from a strong sample and a weak one
    is only as trustworthy as the weak one."""
    if not levels:
        return Confidence.NONE
    return min(levels, key=lambda c: c.value)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def comparable(before: float | None, after: float | None) -> bool:
    """Whether two readings of a metric can be compared at all.

    Profit factor is legitimately infinite for a period with no losing trades,
    and `inf` compared against `inf` produces a NaN percentage that renders as
    "your profit factor has declined nan% since March". Every narrative
    comparison in the package gates on this rather than each remembering to
    check — the timeline and the report writer both shipped that sentence
    before it existed.
    """
    return (before is not None and after is not None
            and math.isfinite(before) and math.isfinite(after))


def pct_change(old: float | None, new: float | None) -> float | None:
    """Percent change from `old` to `new`. None when `old` is 0 or missing —
    "improved by infinity%" is not a sentence this system will produce."""
    if old in (None, 0) or new is None:
        return None
    return (new - old) / abs(old) * 100.0
