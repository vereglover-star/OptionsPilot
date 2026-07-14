"""TradeGate — the tradeability decision, with two modes.

Conservative (default): a signal trades iff confidence >= engine.min_confidence.
Exactly the pre-gate behaviour.

High-Risk: the required confidence adapts to *setup quality*, a structured
assessment over the scorer's evidence:

    quality      requirement (base = min_confidence, default 80)
    excellent    base - 18  (72% -> 62 by default)
    good         base - 10  (-> 70)
    average      base -  3  (-> 77)
    poor         never trades, at ANY confidence

bounded below by `high_risk_floor` (default 60) and never looser than the
quality justifies. Quality is judged on the confirmations that matter most:
higher-timeframe trend alignment, market structure, volume, momentum,
support/resistance positioning, divergence, and consolidation — the same
lenses the scorer weighs, examined here for *composition* (what agrees, what
conflicts) rather than just their weighted average.

Additionally, a "stretch" entry (high-risk mode, confidence below the
conservative bar) must clear `high_risk_min_rr_stretch` risk/reward — see
`stretch_rr_ok`, enforced at plan time. So 72% with excellent structure and
2.5:1 reward trades; 72% with a mediocre 1.6:1 target does not.

What high-risk mode does NOT change: stops, position sizing, loss limits,
cooldowns, liquidity filters, trading hours. Those all still apply verbatim
downstream — this gate only decides which signals are worth risking on.

Every assessment returns a GateReport: the quality label, the exact threshold
used, which confirmations passed/failed, and a one-line reason — logged for
every potential trade and surfaced in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

from optionspilot.config.settings import EngineConfig
from optionspilot.core.models import Evidence
from optionspilot.engine.scorer import ScoreResult

# Confidence discount per quality tier, relative to min_confidence.
QUALITY_OFFSETS = {"excellent": 18.0, "good": 10.0, "average": 3.0}

CONFLICT_SCORE = -0.25       # evidence at/below this opposes the trade
STRONG_HTF = 0.5             # HTF alignment for an excellent setup
ALIGNED_HTF = 0.25           # HTF alignment for a good setup
OPPOSING_HTF = -0.25         # HTF against the trade -> poor, full stop
PASS_SCORE = 0.15            # minimum supporting score to count as a confirmation

# The four confirmations that carry setup quality.
CORE = ("trend alignment", "market structure", "volume", "momentum")


@dataclass(frozen=True, slots=True)
class GateReport:
    mode: str
    setup_quality: str                      # excellent | good | average | poor
    confidence: float
    min_confidence_required: float | None   # None => untradeable at any confidence
    accepted: bool
    reason: str
    confirmations_passed: tuple[str, ...]
    confirmations_failed: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "setup_quality": self.setup_quality,
            "min_confidence_required": self.min_confidence_required,
            "accepted": self.accepted,
            "reason": self.reason,
            "confirmations_passed": list(self.confirmations_passed),
            "confirmations_failed": list(self.confirmations_failed),
        }


class TradeGate:
    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg

    def assess(self, result: ScoreResult) -> GateReport:
        quality, passed, failed = self._setup_quality(result.evidence)
        confidence = result.confidence
        base = self._cfg.min_confidence
        mode = self._cfg.trading_mode

        if mode == "conservative":
            required: float | None = base
            accepted = confidence >= base
        elif quality == "poor":
            required = None
            accepted = False
        else:
            required = max(self._cfg.high_risk_floor, base - QUALITY_OFFSETS[quality])
            required = min(required, base)   # never stricter than conservative
            accepted = confidence >= required

        if required is None:
            reason = (f"poor setup — not tradeable at any confidence "
                      f"({'; '.join(failed[:3]) or 'insufficient confirmations'})")
        elif accepted:
            reason = (f"{quality} setup: confidence {confidence:.1f}% ≥ "
                      f"required {required:.0f}% ({mode} mode)")
        else:
            reason = (f"{quality} setup: confidence {confidence:.1f}% < "
                      f"required {required:.0f}% ({mode} mode)")

        return GateReport(
            mode=mode, setup_quality=quality, confidence=confidence,
            min_confidence_required=required, accepted=accepted, reason=reason,
            confirmations_passed=tuple(passed), confirmations_failed=tuple(failed),
        )

    # ── setup quality ────────────────────────────────────────────────────────

    @staticmethod
    def _setup_quality(evidence: tuple[Evidence, ...]
                       ) -> tuple[str, list[str], list[str]]:
        """Classify the setup from evidence *composition*. Scores here are
        trade-relative (+ supports, - opposes) as presented by the scorer."""
        by_name = {e.name: e for e in evidence}

        def score(name: str) -> float | None:
            e = by_name.get(name)
            return e.score if e is not None else None

        def combined(*names: str) -> float | None:
            vals = [s for s in (score(n) for n in names) if s is not None]
            return sum(vals) / len(vals) if vals else None

        checks: list[tuple[str, float | None, float]] = [
            # (label, value, pass threshold)
            ("trend alignment", score("htf_trend"), ALIGNED_HTF),
            ("market structure", score("structure_break"), PASS_SCORE),
            ("volume", combined("volume_pressure", "relative_volume"), PASS_SCORE),
            ("momentum", combined("momentum_rsi", "momentum_macd"), PASS_SCORE),
            ("support/resistance", combined("range_position", "zone_confluence"), PASS_SCORE),
            ("trend strength (ADX)", score("trend_strength"), PASS_SCORE),
            ("no opposing divergence", score("divergence"), 0.0),
            ("liquidity sweep", score("liquidity_grab"), 0.0),
            ("candlestick confirmation", score("candlestick"), 0.0),
            ("no consolidation", score("consolidation"), 0.0),
        ]
        passed, failed = [], []
        for label, value, threshold in checks:
            if value is None:
                continue   # that lens produced no evidence — neither confirms nor conflicts
            if value >= threshold:
                passed.append(f"{label} ({value:+.2f})")
            else:
                failed.append(f"{label} ({value:+.2f})")

        conflicts = sum(1 for e in evidence if e.score <= CONFLICT_SCORE)
        htf = score("htf_trend") or 0.0
        core_passed = sum(
            1 for label in CORE if any(p.startswith(label) for p in passed)
        )

        if htf <= OPPOSING_HTF or conflicts >= 3 or core_passed <= 1:
            quality = "poor"
        elif (htf >= STRONG_HTF and core_passed == len(CORE) and conflicts == 0):
            quality = "excellent"
        elif htf >= ALIGNED_HTF and core_passed >= 3 and conflicts <= 1:
            quality = "good"
        else:
            quality = "average"
        return quality, passed, failed


def stretch_rr_ok(cfg: EngineConfig, confidence: float, risk_reward: float) -> bool:
    """Plan-time expected-value guard for high-risk mode: an entry below the
    conservative confidence bar must offer at least `high_risk_min_rr_stretch`
    risk/reward. Above the bar (or in conservative mode) nothing changes —
    the risk manager's usual min_risk_reward still applies to everyone."""
    if cfg.trading_mode != "high_risk":
        return True
    if confidence >= cfg.min_confidence:
        return True
    return risk_reward >= cfg.high_risk_min_rr_stretch
