"""TradePlanner: turn (signal, contract, market context) into a complete,
self-contained execution plan.

Stops are structure-based: behind the most recent confirmed swing on the entry
timeframe, padded by a configurable ATR buffer, with a pure-ATR fallback when
no usable swing exists. Targets prefer the opposing swing level; when price is
in open air (post-breakout), the fallback is a configured R multiple. The risk
manager — not the planner — decides whether the resulting risk/reward is
acceptable.
"""

from __future__ import annotations

import math

from optionspilot.config.settings import EngineConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Direction, OptionContract, Signal, TradePlan
from optionspilot.engine.views import TimeframeView

log = get_logger("engine")

ATR_FALLBACK_STOP_MULT = 1.5


class TradePlanner:
    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg

    def plan(
        self,
        signal: Signal,
        entry_view: TimeframeView,
        contract: OptionContract,
        spot: float,
    ) -> TradePlan | None:
        cfg = self._cfg
        atr = entry_view.atr
        if math.isnan(atr) or atr <= 0:
            log.warning("plan rejected: ATR undefined for %s", signal.symbol)
            return None
        if contract.mid <= 0:
            log.warning("plan rejected: contract %s has no market", contract.symbol)
            return None

        is_long = signal.direction is Direction.LONG
        buffer = cfg.stop_atr_buffer * atr

        if is_long:
            swing_stop = next(
                (s.price for s in reversed(entry_view.swings)
                 if not s.is_high and s.price < spot), None,
            )
            stop = (swing_stop - buffer) if swing_stop is not None else spot - ATR_FALLBACK_STOP_MULT * atr
            risk = spot - stop
            if risk <= 0:
                log.warning("plan rejected: non-positive risk for %s", signal.symbol)
                return None
            swing_target = next(
                (s.price for s in reversed(entry_view.swings)
                 if s.is_high and s.price > spot + risk), None,
            )
            target = swing_target if swing_target is not None else spot + cfg.fallback_target_rr * risk
            partials = (spot + risk,)                    # take partial at +1R
            rr = (target - spot) / risk
            invalidation = (f"{entry_view.timeframe} close below {stop:.2f} "
                            f"(structure invalidated) or CHoCH down")
        else:
            swing_stop = next(
                (s.price for s in reversed(entry_view.swings)
                 if s.is_high and s.price > spot), None,
            )
            stop = (swing_stop + buffer) if swing_stop is not None else spot + ATR_FALLBACK_STOP_MULT * atr
            risk = stop - spot
            if risk <= 0:
                log.warning("plan rejected: non-positive risk for %s", signal.symbol)
                return None
            swing_target = next(
                (s.price for s in reversed(entry_view.swings)
                 if not s.is_high and s.price < spot - risk), None,
            )
            target = swing_target if swing_target is not None else spot - cfg.fallback_target_rr * risk
            partials = (spot - risk,)
            rr = (spot - target) / risk
            invalidation = (f"{entry_view.timeframe} close above {stop:.2f} "
                            f"(structure invalidated) or CHoCH up")

        return TradePlan(
            signal=signal,
            contract=contract,
            entry_price=round(contract.mid, 2),
            spot=spot,
            stop_underlying=round(stop, 2),
            target_underlying=round(target, 2),
            partial_levels=tuple(round(p, 2) for p in partials),
            max_hold_bars=0,
            invalidation=invalidation,
            risk_reward=round(rr, 2),
        )
