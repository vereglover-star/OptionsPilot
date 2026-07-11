"""Broker interface.

Live adapters (Alpaca, Tradier, Webull, IBKR — Phase 8) implement exactly this
surface. The orchestrator, backtester, and position manager only ever see a
`Broker`, which is what makes 'enable live trading by configuration only'
possible — and what makes it impossible in v1, where the sole implementation
is the simulator.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

from optionspilot.core.models import Fill, Position, TradePlan


class BrokerError(Exception):
    """Raised when an order cannot be executed (insufficient funds, unknown
    position, bad quantity). Callers must treat this as a rejected order, not
    a crash."""


@dataclass(frozen=True, slots=True)
class AccountState:
    cash: float
    equity: float          # cash + open positions at their latest marks
    realized_pnl: float    # lifetime, net of exit commissions


class Broker(abc.ABC):
    @abc.abstractmethod
    def open_position(self, plan: TradePlan, quantity: int, ts: datetime) -> Fill:
        """Buy to open `quantity` contracts of the plan's contract. The fill
        price comes from the contract snapshot inside the plan (fresh chain
        data), worsened by slippage."""

    @abc.abstractmethod
    def close_position(
        self, contract_symbol: str, quantity: int, bid: float, ts: datetime,
        reason: str = "",
    ) -> tuple[Fill, float]:
        """Sell to close. Returns (fill, realized_pnl_for_this_close)."""

    @abc.abstractmethod
    def mark_positions(self, marks: dict[str, float]) -> None:
        """Update option mid-price marks {contract_symbol: price} for equity."""

    @abc.abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abc.abstractmethod
    def get_account(self) -> AccountState: ...
