"""ContractSelector: turn a directional signal into a specific option contract.

Filters the chain hard (DTE window, spread, open interest, volume, liquidity
score) and only then optimizes for the delta target. Every rejection is
counted by reason so a "no contract found" outcome is fully explainable — the
journal records why the system stayed flat.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from optionspilot.analysis.options_metrics import enrich_greeks, liquidity_score
from optionspilot.config.settings import EngineConfig
from optionspilot.core.models import Direction, OptionContract, OptionRight


@dataclass(frozen=True, slots=True)
class SelectionResult:
    contract: OptionContract | None
    considered: int
    rejections: dict[str, int] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        if self.contract is not None:
            return "selected"
        if not self.considered:
            return "empty chain"
        detail = ", ".join(f"{k}: {n}" for k, n in sorted(self.rejections.items()))
        return f"all {self.considered} contracts rejected ({detail})"


class ContractSelector:
    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg

    def select(
        self,
        direction: Direction,
        chain: list[OptionContract],
        spot: float,
        today: date,
    ) -> SelectionResult:
        cfg = self._cfg
        want = OptionRight.CALL if direction is Direction.LONG else OptionRight.PUT
        rejections: Counter[str] = Counter()
        candidates: list[tuple[float, float, OptionContract]] = []
        considered = 0

        for c in chain:
            if c.right is not want:
                continue
            considered += 1
            dte = c.dte(today)
            if not (cfg.min_dte <= dte <= cfg.max_dte):
                rejections["dte_out_of_window"] += 1
                continue
            if c.mid <= 0:
                rejections["no_market"] += 1
                continue
            if c.spread_pct > cfg.max_spread_pct:
                rejections["spread_too_wide"] += 1
                continue
            if c.open_interest < cfg.min_open_interest:
                rejections["open_interest_too_low"] += 1
                continue
            if c.volume < cfg.min_option_volume:
                rejections["volume_too_low"] += 1
                continue
            if c.delta == 0.0:
                c = enrich_greeks(c, spot, today)
            if c.delta == 0.0:
                rejections["greeks_unavailable"] += 1
                continue
            liq = liquidity_score(c)
            if liq < cfg.min_liquidity_score:
                rejections["liquidity_score_too_low"] += 1
                continue
            delta_error = abs(abs(c.delta) - cfg.target_delta)
            candidates.append((delta_error, -liq, c))

        if not candidates:
            return SelectionResult(None, considered, dict(rejections))
        candidates.sort(key=lambda t: (t[0], t[1]))
        return SelectionResult(candidates[0][2], considered, dict(rejections))
