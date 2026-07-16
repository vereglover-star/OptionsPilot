"""Core domain models shared by every subsystem.

These are deliberately plain dataclasses (not pydantic) because they live on the
hot path — thousands are created per backtest bar. Validation happens at the
edges (config, broker adapters); internal code trusts these types.

All timestamps are timezone-aware UTC. All prices are floats in account currency.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, timezone


class Timeframe(enum.Enum):
    """Chart timeframes. Value is the duration in minutes."""

    M1 = 1
    M5 = 5
    M15 = 15
    H1 = 60
    H4 = 240
    D1 = 1440

    @property
    def minutes(self) -> int:
        return self.value

    @classmethod
    def from_string(cls, s: str) -> "Timeframe":
        mapping = {
            "1m": cls.M1, "5m": cls.M5, "15m": cls.M15,
            "1h": cls.H1, "4h": cls.H4, "1d": cls.D1,
        }
        try:
            return mapping[s.lower()]
        except KeyError:
            raise ValueError(f"Unknown timeframe {s!r}; expected one of {list(mapping)}") from None

    def __str__(self) -> str:
        return {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}[self.value]


class OptionRight(enum.Enum):
    CALL = "call"
    PUT = "put"


class Direction(enum.Enum):
    """Directional bias of a signal on the underlying."""

    LONG = "long"
    SHORT = "short"


class OrderSide(enum.Enum):
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class OrderType(enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar. `ts` is the bar's opening time, UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Candle.ts must be timezone-aware")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"Inconsistent OHLC at {self.ts}: O={self.open} H={self.high} L={self.low} C={self.close}"
            )

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


@dataclass(frozen=True, slots=True)
class Quote:
    """Point-in-time quote for any instrument."""

    symbol: str
    ts: datetime
    bid: float
    ask: float
    last: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        """Spread as a fraction of mid — the primary liquidity red flag."""
        return self.spread / self.mid if self.mid > 0 else float("inf")


@dataclass(frozen=True, slots=True)
class OptionContract:
    """A single option contract with its market snapshot and greeks."""

    underlying: str
    expiration: date
    strike: float
    right: OptionRight
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0

    @property
    def symbol(self) -> str:
        """OCC-style symbol, e.g. SPY260918C00450000."""
        r = "C" if self.right is OptionRight.CALL else "P"
        return f"{self.underlying}{self.expiration:%y%m%d}{r}{int(round(self.strike * 1000)):08d}"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")

    def dte(self, today: date) -> int:
        return (self.expiration - today).days


@dataclass(frozen=True, slots=True)
class Evidence:
    """One scored piece of evidence contributing to a signal's confidence."""

    name: str          # e.g. "htf_trend_alignment"
    detail: str        # human-readable, e.g. "1D and 4H both in uptrend"
    score: float       # -1.0 (strongly against) .. +1.0 (strongly for)
    weight: float      # relative importance, set by config / learning system


@dataclass(frozen=True, slots=True)
class Signal:
    """A trade opportunity produced by the AI engine. Not yet risk-approved."""

    symbol: str
    ts: datetime
    direction: Direction
    confidence: float                 # 0..100
    evidence: tuple[Evidence, ...]
    strategy: str                     # name of the strategy that produced it
    timeframe: Timeframe              # entry timeframe

    @property
    def reasons(self) -> list[str]:
        """Itemized human-readable reasoning, strongest evidence first."""
        ranked = sorted(self.evidence, key=lambda e: abs(e.score * e.weight), reverse=True)
        return [f"{'+' if e.score >= 0 else '-'} {e.detail}" for e in ranked]


@dataclass(frozen=True, slots=True)
class TradePlan:
    """Complete execution plan for a signal, built by the TradePlanner."""

    signal: Signal
    contract: OptionContract
    entry_price: float                # option premium to pay (limit)
    spot: float                       # underlying price when the plan was built
    stop_underlying: float            # exit if underlying crosses this
    target_underlying: float          # primary target on the underlying
    partial_levels: tuple[float, ...] = ()   # underlying levels to take partials
    max_hold_bars: int = 0            # 0 = no time stop
    invalidation: str = ""            # human-readable invalidation condition
    risk_reward: float = 0.0


@dataclass(slots=True)
class Order:
    """An order as submitted to (and updated by) a broker."""

    id: str
    contract: OptionContract
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: float | None
    submitted_at: datetime
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    reject_reason: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    ts: datetime
    quantity: int
    price: float
    commission: float


@dataclass(slots=True)
class Position:
    """An open position, maintained by the broker.

    The management fields (direction, entry_spot, stop_current, target,
    partials_remaining) are copied from the TradePlan at open and persisted, so
    position management survives a restart even though the full plan object is
    in-memory only.
    """

    contract: OptionContract
    quantity: int                     # contracts held (positive = long)
    avg_price: float                  # average premium paid per contract
    opened_at: datetime
    plan: TradePlan | None = None     # the plan that opened it (not persisted)
    direction: Direction = Direction.LONG
    entry_spot: float = 0.0           # underlying price at entry
    stop_current: float = 0.0         # working stop on the underlying (may trail)
    target: float = 0.0               # target on the underlying
    partials_remaining: tuple[float, ...] = ()
    managed_by: str = "ai"            # "ai": PositionManager runs stops/targets;
                                      # "manual": user's working orders do

    def unrealized_pnl(self, mark: float) -> float:
        return (mark - self.avg_price) * self.quantity * 100


@dataclass(slots=True)
class TradeRecord:
    """A completed round-trip trade — the unit of the journal and learning system."""

    id: str
    symbol: str
    contract_symbol: str
    direction: Direction
    strategy: str
    quantity: int
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    commissions: float
    confidence: float
    entry_reasons: list[str] = field(default_factory=list)
    exit_reason: str = ""
    market_conditions: dict[str, str] = field(default_factory=dict)
    indicators_used: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.quantity * 100 - self.commissions

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def hold_minutes(self) -> float:
        return (self.exit_ts - self.entry_ts).total_seconds() / 60


def utcnow() -> datetime:
    """The one sanctioned way to get 'now' — always aware UTC."""
    return datetime.now(timezone.utc)
