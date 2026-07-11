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
from optionspilot.engine.planner import TradePlanner
from optionspilot.engine.scorer import ConfluenceScorer
from optionspilot.engine.views import MultiTimeframeAnalyzer, TimeframeView

log = get_logger("engine")

STRATEGY_NAME = "confluence_v1"


@dataclass(frozen=True, slots=True)
class EngineDecision:
    signal: Signal | None
    tradeable: bool                     # confidence >= configured threshold
    views: dict[Timeframe, TimeframeView]
    entry_view: TimeframeView | None


class DecisionEngine:
    def __init__(self, config: AppConfig,
                 learned_weights: dict[str, float] | None = None):
        self._cfg = config
        self.analyzer = MultiTimeframeAnalyzer(config)
        self.scorer = ConfluenceScorer(config.engine, config.indicators,
                                       learned_weights)
        self.selector = ContractSelector(config.engine)
        self.planner = TradePlanner(config.engine)

    def evaluate(
        self, symbol: str, candles_by_tf: dict[Timeframe, pd.DataFrame]
    ) -> EngineDecision:
        views = self.analyzer.analyze(candles_by_tf)
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
        tradeable = signal.confidence >= self._cfg.engine.min_confidence
        log.info(
            "%s: %s %.1f%% (%s threshold %.0f%%)\n  %s",
            symbol, signal.direction.value, signal.confidence,
            "TRADEABLE, meets" if tradeable else "below",
            self._cfg.engine.min_confidence,
            "\n  ".join(signal.reasons),
        )
        return EngineDecision(signal, tradeable, views, entry_view)

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
        if plan is not None:
            log.info(
                "%s: plan %s x %s | entry %.2f stop %.2f target %.2f RR %.2f",
                decision.signal.symbol, decision.signal.direction.value,
                plan.contract.symbol, plan.entry_price,
                plan.stop_underlying, plan.target_underlying, plan.risk_reward,
            )
        return plan
