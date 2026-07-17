"""DecisionEngine: the one entry point the orchestrator and backtester call.

evaluate()  candles -> views -> scored signal (always returned when evidence
            exists, even below threshold — sub-threshold signals are logged
            for the learning system but flagged not tradeable).
build_plan() signal + option chain -> concrete TradePlan, or None with the
            reason logged.

The engine never talks to a broker and never sizes positions — that is the
risk manager's monopoly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import OptionContract, Signal, Timeframe, TradePlan
from optionspilot.engine.contracts import ContractSelector, SelectionResult
from optionspilot.engine.gate import GateReport, TradeGate, stretch_rr_ok
from optionspilot.engine.planner import TradePlanner
from optionspilot.engine.scorer import ConfluenceScorer
from optionspilot.engine.views import MultiTimeframeAnalyzer, TimeframeView

log = get_logger("engine")

STRATEGY_NAME = "confluence_v1"


@dataclass(frozen=True, slots=True)
class EngineDecision:
    signal: Signal | None
    tradeable: bool                     # gate accepted (mode-aware threshold)
    views: dict[Timeframe, TimeframeView]
    entry_view: TimeframeView | None
    gate: GateReport | None = None      # full accept/reject reasoning


class DecisionEngine:
    def __init__(self, config: AppConfig,
                 learned_weights: dict[str, float] | None = None):
        self._cfg = config
        self.analyzer = MultiTimeframeAnalyzer(config)
        self.scorer = ConfluenceScorer(config.engine, config.indicators,
                                       learned_weights)
        self.selector = ContractSelector(config.engine)
        self.planner = TradePlanner(config.engine)
        self.gate = TradeGate(config.engine)

    def evaluate(
        self, symbol: str, candles_by_tf: dict[Timeframe, pd.DataFrame]
    ) -> EngineDecision:
        views = self.analyzer.analyze(candles_by_tf, key=symbol)
        result = self.scorer.score(views)
        entry_view = next(
            (views[Timeframe.from_string(s)] for s in self._cfg.engine.entry_timeframes
             if Timeframe.from_string(s) in views),
            None,
        )
        if result is None or entry_view is None:
            log.info("%s: no evaluable signal (insufficient data)", symbol)
            return EngineDecision(None, False, views, entry_view)

        signal = Signal(
            symbol=symbol,
            ts=entry_view.ts,
            direction=result.direction,
            confidence=result.confidence,
            evidence=result.evidence,
            strategy=STRATEGY_NAME,
            timeframe=entry_view.timeframe,
        )
        gate = self.gate.assess(result)
        log.info(
            "%s: %s %.1f%% | %s | %s\n  gate: %s\n  passed: %s\n  failed: %s\n  %s",
            symbol, signal.direction.value, signal.confidence,
            gate.mode, "TRADEABLE" if gate.accepted else "no trade",
            gate.reason,
            "; ".join(gate.confirmations_passed) or "none",
            "; ".join(gate.confirmations_failed) or "none",
            "\n  ".join(signal.reasons),
        )
        return EngineDecision(signal, gate.accepted, views, entry_view, gate)

    def build_plan(
        self,
        decision: EngineDecision,
        chain: list[OptionContract],
        spot: float,
        today: date,
    ) -> TradePlan | None:
        if decision.signal is None or decision.entry_view is None:
            return None
        selection: SelectionResult = self.selector.select(
            decision.signal.direction, chain, spot, today
        )
        if selection.contract is None:
            log.info("%s: no contract — %s", decision.signal.symbol, selection.reason)
            return None
        plan = self.planner.plan(decision.signal, decision.entry_view,
                                 selection.contract, spot)
        if plan is not None and not stretch_rr_ok(
            self._cfg.engine, decision.signal.confidence, plan.risk_reward
        ):
            log.info(
                "%s: stretch entry rejected — confidence %.1f%% is below the "
                "conservative bar (%.0f%%), so RR %.2f must be ≥ %.2f",
                decision.signal.symbol, decision.signal.confidence,
                self._cfg.engine.min_confidence, plan.risk_reward,
                self._cfg.engine.high_risk_min_rr_stretch,
            )
            return None
        if plan is not None:
            log.info(
                "%s: plan %s x %s | entry %.2f stop %.2f target %.2f RR %.2f",
                decision.signal.symbol, decision.signal.direction.value,
                plan.contract.symbol, plan.entry_price,
                plan.stop_underlying, plan.target_underlying, plan.risk_reward,
            )
        return plan
