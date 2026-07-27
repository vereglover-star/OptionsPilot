"""SimilarityEngine — the beginning of AI memory: given a setup, find the most
comparable historical trades and summarize how they turned out.

Deliberately NOT machine-learned. Similarity is a hand-authored weighted
distance over the same features a human trader would compare (direction, the
supporting evidence set, setup quality, higher-timeframe trend, and the
normalized indicator readings). Every match is explainable and reproducible —
the same guard-rail philosophy as the deterministic scorer and the coach.

The one number that blends model + history — `calibrated_confidence` — is
ADVISORY. It is returned for explanations and dashboards and is never fed into
the trading gate (see `experience/__init__.py`).
"""

from __future__ import annotations

from collections import Counter

from optionspilot.experience.models import ExperienceRecord, SimilarityResult
from optionspilot.experience.store import ExperienceStore

# Component weights. Direction and the evidence composition dominate: a long
# setup is simply not comparable to a short one, and two setups built on the
# same confluence of evidence are the most alike.
_W_DIRECTION = 3.0
_W_EVIDENCE = 3.0
_W_SETUP = 1.5
_W_HTF = 1.5
_W_TIMEFRAME = 1.0
_W_SESSION = 0.5
_W_NUMERIC = 2.0

# Shrinkage constant for confidence calibration: with far fewer than K similar
# trades, the calibrated number stays close to the model's own estimate; only a
# large, consistent cohort meaningfully moves it.
_CALIBRATION_K = 20

# Readable phrases for the scorer's evidence names, used to render grounded
# "success pattern" strings. Keys mirror scorer.DEFAULT_WEIGHTS.
EVIDENCE_LABELS = {
    "htf_trend": "higher-timeframe trend alignment",
    "htf_supertrend": "higher-timeframe supertrend",
    "structure_break": "market-structure break (BOS/CHoCH)",
    "ema_alignment": "EMA stack alignment",
    "momentum_rsi": "RSI momentum",
    "momentum_macd": "MACD momentum",
    "trend_strength": "trend strength (ADX/DI)",
    "vwap": "price vs VWAP",
    "volume_pressure": "buying/selling volume pressure",
    "relative_volume": "elevated relative volume",
    "divergence": "momentum divergence",
    "candlestick": "candlestick confirmation",
    "range_position": "premium/discount range position",
    "liquidity_grab": "liquidity grab / sweep",
    "zone_confluence": "unmitigated FVG / order block",
}


def _jaccard_distance(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return 1.0 - len(sa & sb) / len(union) if union else 0.0


def similarity(query: ExperienceRecord, cand: ExperienceRecord) -> float:
    """0 (nothing in common / incomparable) .. 1 (identical setup).

    Only components where BOTH sides carry data contribute — a feature the
    query never captured is 'no information on this axis', not a mismatch.
    """
    # Direction is the mandatory anchor: every real experience carries it, and
    # two setups with no shared direction aren't meaningfully comparable (this
    # also stops a lone default-valued axis, e.g. market_session, from reading
    # as a spurious perfect match on an otherwise-empty record).
    if not query.direction or not cand.direction:
        return 0.0

    dist_sum = 0.0
    weight_sum = 0.0

    def add(weight: float, distance: float) -> None:
        nonlocal dist_sum, weight_sum
        dist_sum += weight * distance
        weight_sum += weight

    add(_W_DIRECTION, 0.0 if query.direction == cand.direction else 1.0)
    if query.setup_quality and cand.setup_quality:
        add(_W_SETUP, 0.0 if query.setup_quality == cand.setup_quality else 1.0)
    if query.htf_trend and cand.htf_trend:
        add(_W_HTF, 0.0 if query.htf_trend == cand.htf_trend else 1.0)
    if query.timeframe and cand.timeframe:
        add(_W_TIMEFRAME, 0.0 if query.timeframe == cand.timeframe else 1.0)
    if query.market_session and cand.market_session:
        add(_W_SESSION, 0.0 if query.market_session == cand.market_session else 1.0)
    if query.evidence_names and cand.evidence_names:
        add(_W_EVIDENCE, _jaccard_distance(query.evidence_names, cand.evidence_names))

    shared = set(query.features) & set(cand.features)
    if shared:
        mean_diff = sum(abs(query.features[k] - cand.features[k])
                        for k in shared) / len(shared)
        add(_W_NUMERIC, mean_diff)

    if weight_sum == 0.0:
        return 0.0
    return max(0.0, 1.0 - dist_sum / weight_sum)


class SimilarityEngine:
    def __init__(self, store: ExperienceStore):
        self._store = store

    def find_similar(
        self,
        query: ExperienceRecord,
        *,
        k: int = 50,
        min_similarity: float = 0.3,
        restrict_direction: bool = True,
    ) -> list[tuple[ExperienceRecord, float]]:
        """Top-k historical experiences most like `query`, best first.

        `restrict_direction` uses the store's indexed direction column as a
        coarse pre-filter — the load-bearing prune that keeps this fast at
        100k+ rows (a long setup is never meaningfully similar to a short one).
        """
        candidates = self._store.query(
            direction=query.direction if restrict_direction else None,
        )
        scored: list[tuple[ExperienceRecord, float]] = []
        for cand in candidates:
            if cand.trade_id == query.trade_id:
                continue
            sim = similarity(query, cand)
            if sim >= min_similarity:
                scored.append((cand, sim))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

    def summarize(
        self,
        query: ExperienceRecord,
        *,
        k: int = 50,
        min_similarity: float = 0.3,
        restrict_direction: bool = True,
    ) -> SimilarityResult:
        """Find similar trades and aggregate them into decision evidence."""
        matches = self.find_similar(
            query, k=k, min_similarity=min_similarity,
            restrict_direction=restrict_direction,
        )
        raw_conf = query.confidence_entry
        if not matches:
            return SimilarityResult(
                n_similar=0, win_rate=0.0, avg_return_pct=0.0,
                avg_hold_minutes=0.0, avg_pnl=0.0, most_common_exit="",
                typical_failure_mode="", calibrated_confidence=raw_conf,
                matches=[], common_failures=[], common_successes=[],
            )

        recs = [m for m, _ in matches]
        n = len(recs)
        wins = sum(1 for r in recs if r.is_win)
        win_rate = wins / n
        avg_return = sum(r.return_pct for r in recs) / n
        avg_hold = sum(r.hold_minutes for r in recs) / n
        avg_pnl = sum(r.pnl for r in recs) / n

        exits = Counter(r.exit_reason.split(":")[0] for r in recs if r.exit_reason)
        most_common_exit = exits.most_common(1)[0][0] if exits else ""

        losers = [r for r in recs if not r.is_win]
        winners = [r for r in recs if r.is_win]
        typical_failure = _dominant_failure(losers)

        calibrated = self._calibrate(raw_conf, win_rate, n)

        return SimilarityResult(
            n_similar=n,
            win_rate=round(win_rate, 4),
            avg_return_pct=round(avg_return, 2),
            avg_hold_minutes=round(avg_hold, 2),
            avg_pnl=round(avg_pnl, 2),
            most_common_exit=most_common_exit,
            typical_failure_mode=typical_failure,
            calibrated_confidence=round(calibrated, 2),
            matches=[(r.trade_id, round(s, 4)) for r, s in matches],
            common_failures=_failure_patterns(losers),
            common_successes=_success_patterns(winners),
        )

    @staticmethod
    def _calibrate(raw: float, win_rate: float, n: int) -> float:
        """Advisory blend of the model's estimate and the historical win rate,
        shrunk toward the model's number when the cohort is small. Bounded to
        [0, 100]. NOT used by the trading gate."""
        w = n / (n + _CALIBRATION_K)
        blended = (1.0 - w) * raw + w * (win_rate * 100.0)
        return max(0.0, min(100.0, blended))


def _dominant_failure(losers: list[ExperienceRecord]) -> str:
    """Most common exit reason among losing trades, falling back to the most
    common tagged mistake — the setup's characteristic failure mode."""
    if not losers:
        return ""
    exits = Counter(r.exit_reason.split(":")[0] for r in losers if r.exit_reason)
    if exits:
        return exits.most_common(1)[0][0]
    mistakes = Counter(m for r in losers for m in r.mistakes)
    return mistakes.most_common(1)[0][0] if mistakes else "unknown"


def _failure_patterns(losers: list[ExperienceRecord], limit: int = 4) -> list[str]:
    """Grounded (never invented) characteristic patterns of the losing cohort:
    dominant exit reasons, then the most common tagged mistakes."""
    out: list[str] = []
    for reason, _ in Counter(
            r.exit_reason.split(":")[0].strip()
            for r in losers if r.exit_reason).most_common(limit):
        if reason and reason not in out:
            out.append(reason)
    for mistake, _ in Counter(m for r in losers for m in r.mistakes).most_common(limit):
        if mistake not in out:
            out.append(mistake)
    return out[:limit]


def _success_patterns(winners: list[ExperienceRecord], limit: int = 4) -> list[str]:
    """Grounded characteristic patterns of the winning cohort: dominant exit
    reasons, then the supporting evidence most common among winners (rendered
    into readable phrases via EVIDENCE_LABELS)."""
    out: list[str] = []
    for reason, _ in Counter(
            r.exit_reason.split(":")[0].strip()
            for r in winners if r.exit_reason).most_common(2):
        if reason and reason not in out:
            out.append(reason)
    for name, _ in Counter(
            n for r in winners for n in r.evidence_names).most_common(limit):
        label = EVIDENCE_LABELS.get(name, name)
        if label not in out:
            out.append(label)
    return out[:limit]
