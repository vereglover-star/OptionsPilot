"""PositionManager — per-cycle management of open positions.

Called by the orchestrator (and backtester) once per scan with the current
underlying spot. Decides, in priority order:

  1. stop hit           -> close everything
  2. target reached     -> close everything
  3. invalidation       -> close everything (opposing CHoCH on the entry TF)
  4. partial level hit  -> close half, move the stop to breakeven

All levels live on the *underlying* (that's what the plan reasoned about);
the broker translates a decision into an option fill at the current bid.

`review()` mutates the position's management fields (consumed partial levels,
trailed stop) and returns the exit intents; the caller executes them via the
broker and persists the mutation with `broker.update_position_management()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Direction, Position

log = get_logger("broker")


@dataclass(frozen=True, slots=True)
class ExitIntent:
    kind: str          # "stop" | "target" | "invalidation" | "partial"
    quantity: int
    reason: str


class PositionManager:
    def review(
        self,
        position: Position,
        spot: float,
        ts: datetime,
        opposing_choch: bool = False,
    ) -> list[ExitIntent]:
        is_long = position.direction is Direction.LONG

        stop_hit = spot <= position.stop_current if is_long else spot >= position.stop_current
        if stop_hit:
            return [ExitIntent("stop", position.quantity,
                               f"stop hit: spot {spot:.2f} vs stop {position.stop_current:.2f}")]

        target_hit = spot >= position.target if is_long else spot <= position.target
        if target_hit:
            return [ExitIntent("target", position.quantity,
                               f"target reached: spot {spot:.2f} vs target {position.target:.2f}")]

        if opposing_choch:
            return [ExitIntent(
                "invalidation", position.quantity,
                f"market structure flipped against position (CHoCH) at spot {spot:.2f}",
            )]

        if position.partials_remaining:
            level = position.partials_remaining[0]
            level_hit = spot >= level if is_long else spot <= level
            if level_hit:
                half = position.quantity // 2
                position.partials_remaining = position.partials_remaining[1:]
                if half >= 1:
                    old_stop = position.stop_current
                    position.stop_current = (
                        max(position.stop_current, position.entry_spot) if is_long
                        else min(position.stop_current, position.entry_spot)
                    )
                    log.info(
                        "%s: partial level %.2f hit — selling %d, stop %.2f -> %.2f",
                        position.contract.symbol, level, half,
                        old_stop, position.stop_current,
                    )
                    return [ExitIntent(
                        "partial", half,
                        f"partial profit at {level:.2f}; stop moved to breakeven "
                        f"{position.stop_current:.2f}",
                    )]
                # 1-lot positions can't split: consume the level, let it run
                log.info("%s: partial level %.2f hit but position is 1 contract — "
                         "letting it run", position.contract.symbol, level)
        return []
