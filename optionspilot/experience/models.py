"""Domain models for the Experience Engine.

`ExperienceRecord` is a deliberately *rich* superset of `TradeRecord`: it
captures everything the AI might later want to learn from, best-effort, with
nullable fields for anything a given trade path couldn't supply (AI trades
carry less indicator context than manually-coached ones, and MFE/MAE need
intrabar data we don't have yet — see docstrings). Anything not modelled as a
first-class field can be stashed in `extra` with no schema change.

These are plain dataclasses like the rest of `core/models.py` — validation
lives at the store boundary, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# Fixed normalization ranges for the numeric similarity features. They are
# INTENTIONALLY dataset-independent: a record's feature vector must never change
# as more trades accumulate, or every historical similarity would silently
# drift. (lo, hi) — values are clamped and scaled to [0, 1].
FEATURE_RANGES: dict[str, tuple[float, float]] = {
    "confidence": (0.0, 100.0),
    "rsi": (0.0, 100.0),
    "adx": (0.0, 100.0),
    "rvol": (0.0, 4.0),
    "pressure": (-1.0, 1.0),
    "iv": (0.0, 2.0),
    "delta": (0.0, 1.0),
    "dte": (0.0, 90.0),
    "risk_reward": (0.0, 5.0),
    "hour_et": (0.0, 24.0),
}


@dataclass(slots=True)
class ExperienceRecord:
    """One completed paper trade, enriched for learning and similarity search.

    Keyed by `trade_id` (== the journal `TradeRecord.id`) so the two stores
    stay joinable. Only the identity/outcome fields are guaranteed populated;
    everything else is best-effort and may be None when the originating trade
    path (AI vs. manual) or the market data of the moment couldn't supply it.
    """

    # ── identity ──────────────────────────────────────────────────────────
    trade_id: str
    recorded_ts: datetime
    symbol: str
    contract_symbol: str
    direction: str            # "long" | "short"
    strategy: str             # engine strategy name, or "manual"
    managed_by: str           # "ai" | "manual"

    # ── trade shape ───────────────────────────────────────────────────────
    quantity: int
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float

    # ── outcome ───────────────────────────────────────────────────────────
    pnl: float
    return_pct: float
    is_win: bool
    hold_minutes: float
    exit_reason: str
    timeframe: str | None = None
    risk_multiple: float | None = None   # realized P/L in units of initial risk (R)
    # Maximum favorable/adverse excursion. Require intrabar tracking we don't
    # have on delayed, per-cycle data — modelled now, populated later (a
    # streaming provider or a tick recorder). See the roadmap doc.
    mfe: float | None = None
    mae: float | None = None

    # ── decision context ──────────────────────────────────────────────────
    confidence_entry: float = 0.0
    confidence_exit: float | None = None
    setup_quality: str | None = None
    gate_mode: str | None = None
    risk_reward: float | None = None

    # ── market / session context ──────────────────────────────────────────
    market_session: str = "regular"      # "regular" | "pre" | "post"
    hour_et: int | None = None
    minute_et: int | None = None
    htf_trend: str | None = None         # "up" | "down" | "neutral" | ...
    entry_trend: str | None = None
    consolidating: bool | None = None
    rsi: float | None = None
    adx: float | None = None
    rvol: float | None = None
    pressure: float | None = None
    iv: float | None = None
    delta: float | None = None
    dte: int | None = None
    spread_pct: float | None = None

    # ── reasoning ─────────────────────────────────────────────────────────
    entry_reasons: list[str] = field(default_factory=list)
    evidence_names: list[str] = field(default_factory=list)  # supporting evidence
    mistakes: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)

    # ── AI decision snapshot (Phase 3) ────────────────────────────────────
    # The full deterministic decision context, captured centrally at entry
    # (experience/snapshot.py). `confidence_entry` IS the deterministic score —
    # there is only one score in this system; the "historical" confidence is the
    # advisory calibrated blend, computed at query time and never stored here.
    atr: float | None = None
    ema_state: int | None = None         # -1 bearish stack / 0 mixed / +1 bullish
    macd_hist: float | None = None
    above_vwap: bool | None = None
    supertrend_dir: int | None = None
    divergence: int | None = None
    stop: float | None = None
    target: float | None = None
    market_regime: str | None = None     # derived: trend × volatility (indexed column)
    operating_mode: str | None = None    # "ai" | "human" at entry
    trading_mode: str | None = None      # conservative | high_risk | custom
    learning_mode: str = "normal"        # normal | exploration (future axis)
    reasoning: str = ""                  # human-readable entry rationale
    # Bollinger bands and a full volume-profile histogram are NOT computed by the
    # engine today, so they are stored as None rather than invented. The full
    # verbose snapshot (evidence breakdown, gate confirmations) lives in `extra`.

    # ── learning ──────────────────────────────────────────────────────────
    exploration: bool = False            # tagged when learning_mode="exploration" (future)

    # ── expansion ─────────────────────────────────────────────────────────
    # Future per-trade fields (screenshot_ref, news, sentiment, ...) live here
    # and need NO schema migration.
    extra: dict = field(default_factory=dict)

    # Normalized similarity feature vector (numeric features in [0, 1]),
    # computed by experience.features.build_feature_vector at record time.
    features: dict = field(default_factory=dict)

    @property
    def volatility_bucket(self) -> str:
        """Coarse IV regime for the 'high volatility' style of query."""
        if self.iv is None:
            return "unknown"
        if self.iv < 0.30:
            return "low"
        if self.iv < 0.60:
            return "medium"
        return "high"


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    """The aggregated verdict of searching historical experience for a setup.

    This is the AI's *evidence*, not its decision: `calibrated_confidence` is
    advisory and deliberately never reaches the trading gate (see the module
    docstring in `experience/__init__.py`).
    """

    n_similar: int
    win_rate: float                 # 0..1 over the matched cohort
    avg_return_pct: float
    avg_hold_minutes: float
    avg_pnl: float
    most_common_exit: str
    typical_failure_mode: str       # dominant exit reason / mistake among losers
    calibrated_confidence: float    # 0..100 advisory blend of model + history
    matches: list[tuple[str, float]]  # (trade_id, similarity 0..1), best first
    common_failures: list[str] = field(default_factory=list)   # grounded loser patterns
    common_successes: list[str] = field(default_factory=list)  # grounded winner patterns

    @property
    def has_evidence(self) -> bool:
        return self.n_similar > 0

    def explain(self, raw_confidence: float) -> str:
        """One-line human-readable calibration statement for explanations."""
        if not self.has_evidence:
            return (f"{raw_confidence:.0f}% confident (no comparable historical "
                    f"trades yet — model estimate only)")
        return (
            f"{self.calibrated_confidence:.0f}% confident — this setup resembles "
            f"{self.n_similar} historical trade(s) with a {self.win_rate:.0%} "
            f"win rate (model estimate {raw_confidence:.0f}%)"
        )


@dataclass(frozen=True, slots=True)
class SimilarTrade:
    """One row of the Similar Trade Viewer — a historical experience matched to
    the current setup, flattened to the fields a viewer needs."""

    trade_id: str
    date: str                # entry date, ISO (YYYY-MM-DD)
    symbol: str
    timeframe: str | None
    direction: str
    outcome: str             # "win" | "loss"
    return_pct: float
    confidence: float        # deterministic score at entry
    similarity: float        # 0..1
    failure_reason: str      # exit reason / mistake if it lost, else ""
    success_reason: str      # exit reason / top evidence if it won, else ""

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id, "date": self.date, "symbol": self.symbol,
            "timeframe": self.timeframe, "direction": self.direction,
            "outcome": self.outcome, "return_pct": self.return_pct,
            "confidence": self.confidence,
            "similarity": round(self.similarity, 4),
            "failure_reason": self.failure_reason,
            "success_reason": self.success_reason,
        }
