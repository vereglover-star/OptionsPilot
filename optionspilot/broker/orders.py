"""OrderManager — working orders for manual (Human Mode) paper trading.

Order types and semantics (documented fill model — delayed data, evaluated
once per scan cycle, no intrabar fills):

  MARKET         executed immediately at ask (buy) / bid (sell) + slippage.
                 Never stored as a working order.
  LIMIT buy      fills when the option ASK trades at/below the limit price.
  LIMIT sell     fills when the option BID trades at/above the limit price.
  STOP_LOSS      sell trigger on the UNDERLYING: long-call positions trigger
                 when spot <= level, put positions when spot >= level; executes
                 as a market sell at the option bid.
  TAKE_PROFIT    the opposite cross of the same underlying level.
  TRAILING_STOP  trails the best favorable underlying price since placement by
                 `trail` points (or `trail_pct` %); triggers like a stop.

Time in force: DAY orders expire at 16:00 ET on their creation day; GTC
orders work until filled or cancelled. Sell-side orders are validated against
the open position and auto-cancel (status CANCELLED, reason recorded) if the
position closes first.

Everything is persisted to SQLite (data/orders.db) — working orders survive
restarts, and the full order history (filled / cancelled / expired) is the
audit trail behind the Orders panel.

Why levels live on the underlying for stops/TP: that is how the plans and the
AI position manager already reason, and delayed option quotes make premium
stops fire erratically. Limit orders use the premium because that's what a
limit means when buying or selling a contract.
"""

from __future__ import annotations

import enum
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from optionspilot.broker.base import BrokerError
from optionspilot.broker.paper import PaperBroker
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Direction, OptionContract, OptionRight

log = get_logger("broker")

ET = ZoneInfo("America/New_York")
SESSION_END = time(16, 0)


class OrderKind(enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"


class TIF(enum.Enum):
    DAY = "day"
    GTC = "gtc"


class OrderStatus(enum.Enum):
    WORKING = "working"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(slots=True)
class WorkingOrder:
    id: str
    created_ts: datetime
    kind: OrderKind
    tif: TIF
    side: str                     # "buy_to_open" | "sell_to_close"
    contract: OptionContract      # instrument identity (quotes fetched live)
    quantity: int
    limit_price: float = 0.0      # LIMIT: option premium
    stop_level: float = 0.0       # STOP_LOSS / TAKE_PROFIT: underlying level
    trail: float = 0.0            # TRAILING_STOP: underlying points
    trail_pct: float = 0.0        # TRAILING_STOP: percent (alternative)
    best_spot: float = 0.0        # trailing high/low watermark
    status: OrderStatus = OrderStatus.WORKING
    result: str = ""              # fill/cancel/expiry detail
    filled_ts: datetime | None = None
    fill_price: float = 0.0

    @property
    def position_direction(self) -> Direction:
        return (Direction.LONG if self.contract.right is OptionRight.CALL
                else Direction.SHORT)

    def trail_distance(self) -> float:
        if self.trail > 0:
            return self.trail
        return self.best_spot * self.trail_pct / 100

    def to_dict(self) -> dict:
        return {
            "id": self.id, "created_ts": self.created_ts.isoformat(),
            "kind": self.kind.value, "tif": self.tif.value, "side": self.side,
            "contract": self.contract.symbol,
            "underlying": self.contract.underlying,
            "quantity": self.quantity,
            "limit_price": self.limit_price or None,
            "stop_level": self.stop_level or None,
            "trail": self.trail or None, "trail_pct": self.trail_pct or None,
            "trail_stop_at": (round(self.best_spot - self.trail_distance(), 2)
                              if self.kind is OrderKind.TRAILING_STOP
                              and self.position_direction is Direction.LONG
                              and self.best_spot else
                              round(self.best_spot + self.trail_distance(), 2)
                              if self.kind is OrderKind.TRAILING_STOP
                              and self.best_spot else None),
            "status": self.status.value, "result": self.result,
            "filled_ts": self.filled_ts.isoformat() if self.filled_ts else None,
            "fill_price": self.fill_price or None,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, created_ts TEXT NOT NULL,
    kind TEXT NOT NULL, tif TEXT NOT NULL, side TEXT NOT NULL,
    underlying TEXT NOT NULL, expiration TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    limit_price REAL NOT NULL, stop_level REAL NOT NULL,
    trail REAL NOT NULL, trail_pct REAL NOT NULL, best_spot REAL NOT NULL,
    status TEXT NOT NULL, result TEXT NOT NULL,
    filled_ts TEXT, fill_price REAL NOT NULL DEFAULT 0
);
"""


class OrderManager:
    def __init__(self, broker: PaperBroker, db_path: str | Path):
        self.broker = broker
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._orders: dict[str, WorkingOrder] = self._load_working()

    # ── placement ────────────────────────────────────────────────────────────

    def place(
        self,
        kind: OrderKind,
        side: str,
        contract: OptionContract,
        quantity: int,
        ts: datetime,
        tif: TIF = TIF.DAY,
        limit_price: float = 0.0,
        stop_level: float = 0.0,
        trail: float = 0.0,
        trail_pct: float = 0.0,
        spot: float = 0.0,
    ) -> tuple[WorkingOrder, dict | None]:
        """Validate and place. MARKET orders execute immediately and are
        returned already FILLED (with the fill event); everything else goes to
        the working book. Raises BrokerError / ValueError on invalid input."""
        if quantity < 1:
            raise ValueError(f"invalid quantity {quantity}")
        if side not in ("buy_to_open", "sell_to_close"):
            raise ValueError(f"invalid side {side!r}")
        if side == "sell_to_close":
            held = self._held_quantity(contract.symbol)
            reserved = self._reserved_quantity(contract.symbol)
            if held == 0:
                raise BrokerError(f"no open position in {contract.symbol}")
            if kind is not OrderKind.MARKET and reserved + quantity > held:
                raise BrokerError(
                    f"{contract.symbol}: {reserved} of {held} contracts already "
                    f"reserved by working sell orders"
                )
            if kind is OrderKind.MARKET and quantity > held:
                raise BrokerError(f"only {held} contract(s) held")
        if kind is OrderKind.LIMIT and limit_price <= 0:
            raise ValueError("limit orders need limit_price > 0")
        if kind in (OrderKind.STOP_LOSS, OrderKind.TAKE_PROFIT) and stop_level <= 0:
            raise ValueError(f"{kind.value} orders need stop_level > 0")
        if kind is OrderKind.TRAILING_STOP:
            if side != "sell_to_close":
                raise ValueError("trailing stops are exit orders")
            if (trail <= 0) == (trail_pct <= 0):
                raise ValueError("trailing stops need exactly one of trail / trail_pct")
        if kind in (OrderKind.STOP_LOSS, OrderKind.TAKE_PROFIT) \
                and side != "sell_to_close":
            raise ValueError(f"{kind.value} orders are exit orders")

        order = WorkingOrder(
            id=str(uuid.uuid4())[:8], created_ts=ts, kind=kind, tif=tif,
            side=side, contract=contract, quantity=quantity,
            limit_price=limit_price, stop_level=stop_level,
            trail=trail, trail_pct=trail_pct, best_spot=spot,
        )
        if kind is OrderKind.MARKET:
            event = self._execute(order, ts, spot=spot)
            return order, event
        self._orders[order.id] = order
        self._save(order)
        log.info("order placed: %s %s %s x%d (%s)", order.id, kind.value,
                 contract.symbol, quantity, tif.value)
        return order, None

    def cancel(self, order_id: str, reason: str = "cancelled by user") -> WorkingOrder:
        order = self._orders.get(order_id)
        if order is None:
            raise BrokerError(f"no working order {order_id!r}")
        self._finish(order, OrderStatus.CANCELLED, reason)
        return order

    def working(self) -> list[WorkingOrder]:
        return list(self._orders.values())

    def orders_for(self, contract_symbol: str) -> list[dict]:
        """Every order (any status) ever placed on one contract — used by the
        TradeCoach to judge stop/target discipline."""
        return [o.to_dict() for o in self._load_all()
                if o.contract.symbol == contract_symbol]

    def history(self, last: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id FROM orders WHERE status != 'working' "
            "ORDER BY created_ts DESC LIMIT ?", (last,),
        ).fetchall()
        ids = {r[0] for r in rows}
        return [o.to_dict() for o in self._load_all() if o.id in ids]

    # ── evaluation (called once per scan cycle) ──────────────────────────────

    def evaluate(
        self,
        now: datetime,
        get_spot: Callable[[str], float | None],
        get_option_quote: Callable[[OptionContract], tuple[float, float]],
        approve_entry: Callable[[WorkingOrder, float], str | None] | None = None,
    ) -> list[dict]:
        """Walk the working book against fresh quotes. Returns fill/expiry
        events. Callables may return None/(0,0) on data failure — the order
        simply stays working until the next cycle (fail safe, not fail fill)."""
        events: list[dict] = []
        for order in list(self._orders.values()):
            if self._expire_if_due(order, now):
                events.append({"event": "expired", "order": order.to_dict()})
                continue
            if order.side == "sell_to_close" \
                    and self._held_quantity(order.contract.symbol) == 0:
                self._finish(order, OrderStatus.CANCELLED,
                             "position closed before the order triggered")
                events.append({"event": "cancelled", "order": order.to_dict()})
                continue

            spot = get_spot(order.contract.underlying)
            bid, ask = get_option_quote(order.contract)
            triggered = False
            if order.kind is OrderKind.LIMIT:
                if order.side == "buy_to_open":
                    triggered = 0 < ask <= order.limit_price
                else:
                    triggered = bid >= order.limit_price > 0
            elif spot is not None and spot > 0:
                triggered = self._underlying_trigger(order, spot)

            if triggered:
                if order.side == "buy_to_open" and approve_entry is not None:
                    veto = approve_entry(order, ask)
                    if veto:
                        self._finish(order, OrderStatus.CANCELLED, f"rejected: {veto}")
                        events.append({"event": "rejected", "order": order.to_dict()})
                        continue
                event = self._execute(order, now, spot=spot or 0.0,
                                      bid=bid, ask=ask)
                if event is not None:
                    events.append(event)
        return events

    def _underlying_trigger(self, order: WorkingOrder, spot: float) -> bool:
        is_long = order.position_direction is Direction.LONG
        if order.kind is OrderKind.STOP_LOSS:
            return spot <= order.stop_level if is_long else spot >= order.stop_level
        if order.kind is OrderKind.TAKE_PROFIT:
            return spot >= order.stop_level if is_long else spot <= order.stop_level
        if order.kind is OrderKind.TRAILING_STOP:
            if order.best_spot <= 0:
                order.best_spot = spot
            order.best_spot = (max(order.best_spot, spot) if is_long
                               else min(order.best_spot, spot))
            self._save(order)
            distance = order.trail_distance()
            return (spot <= order.best_spot - distance if is_long
                    else spot >= order.best_spot + distance)
        return False

    # ── execution ────────────────────────────────────────────────────────────

    def _execute(self, order: WorkingOrder, ts: datetime, spot: float,
                 bid: float = 0.0, ask: float = 0.0) -> dict | None:
        try:
            if order.side == "buy_to_open":
                contract = order.contract
                if ask > 0 and contract.ask <= 0:
                    # restored-from-disk contract identity: graft the live quote
                    import dataclasses
                    contract = dataclasses.replace(contract, bid=bid, ask=ask)
                fill = self.broker.open_manual(contract, order.quantity,
                                               ts, entry_spot=spot)
                detail = f"bought {order.quantity} @ {fill.price:.2f}"
            else:
                quantity = min(order.quantity,
                               self._held_quantity(order.contract.symbol))
                sell_bid = bid if bid > 0 else order.contract.bid
                if sell_bid <= 0:
                    raise BrokerError("no bid to sell against")
                fill, realized = self.broker.close_position(
                    order.contract.symbol, quantity, sell_bid, ts,
                    reason=f"{order.kind.value} order {order.id}",
                )
                detail = (f"sold {quantity} @ {fill.price:.2f} "
                          f"(realized {realized:+.2f})")
        except BrokerError as exc:
            self._finish(order, OrderStatus.CANCELLED, f"rejected: {exc}")
            log.warning("order %s rejected: %s", order.id, exc)
            return {"event": "rejected", "order": order.to_dict()}

        order.fill_price = fill.price
        order.filled_ts = ts
        self._finish(order, OrderStatus.FILLED, detail)
        log.info("order %s filled: %s", order.id, detail)
        return {"event": "filled", "order": order.to_dict()}

    # ── internals ────────────────────────────────────────────────────────────

    def _held_quantity(self, contract_symbol: str) -> int:
        for p in self.broker.get_positions():
            if p.contract.symbol == contract_symbol:
                return p.quantity
        return 0

    def _reserved_quantity(self, contract_symbol: str) -> int:
        return sum(o.quantity for o in self._orders.values()
                   if o.side == "sell_to_close"
                   and o.contract.symbol == contract_symbol)

    def _expire_if_due(self, order: WorkingOrder, now: datetime) -> bool:
        if order.tif is not TIF.DAY:
            return False
        created_et = order.created_ts.astimezone(ET)
        now_et = now.astimezone(ET)
        expired = (now_et.date() > created_et.date()
                   or (now_et.date() == created_et.date()
                       and now_et.time() >= SESSION_END))
        if expired:
            self._finish(order, OrderStatus.EXPIRED,
                         "day order expired at the close")
        return expired

    def _finish(self, order: WorkingOrder, status: OrderStatus, result: str) -> None:
        order.status = status
        order.result = result
        self._orders.pop(order.id, None)
        self._save(order)

    # ── persistence ──────────────────────────────────────────────────────────

    def _save(self, o: WorkingOrder) -> None:
        c = o.contract
        self._conn.execute(
            "INSERT OR REPLACE INTO orders VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.id, o.created_ts.isoformat(), o.kind.value, o.tif.value, o.side,
             c.underlying, c.expiration.isoformat(), c.strike, c.right.value,
             o.quantity, o.limit_price, o.stop_level, o.trail, o.trail_pct,
             o.best_spot, o.status.value, o.result,
             o.filled_ts.isoformat() if o.filled_ts else None, o.fill_price),
        )
        self._conn.commit()

    def _load_all(self) -> list[WorkingOrder]:
        rows = self._conn.execute(
            "SELECT id, created_ts, kind, tif, side, underlying, expiration, "
            "strike, right, quantity, limit_price, stop_level, trail, trail_pct, "
            "best_spot, status, result, filled_ts, fill_price FROM orders "
            "ORDER BY created_ts"
        ).fetchall()
        out = []
        for (oid, created, kind, tif, side, underlying, expiration, strike,
             right, qty, limit_price, stop_level, trail, trail_pct, best_spot,
             status, result, filled_ts, fill_price) in rows:
            out.append(WorkingOrder(
                id=oid, created_ts=datetime.fromisoformat(created),
                kind=OrderKind(kind), tif=TIF(tif), side=side,
                contract=OptionContract(
                    underlying=underlying,
                    expiration=date.fromisoformat(expiration),
                    strike=strike, right=OptionRight(right),
                ),
                quantity=qty, limit_price=limit_price, stop_level=stop_level,
                trail=trail, trail_pct=trail_pct, best_spot=best_spot,
                status=OrderStatus(status), result=result,
                filled_ts=datetime.fromisoformat(filled_ts) if filled_ts else None,
                fill_price=fill_price,
            ))
        return out

    def _load_working(self) -> dict[str, WorkingOrder]:
        working = {o.id: o for o in self._load_all()
                   if o.status is OrderStatus.WORKING}
        if working:
            log.info("restored %d working order(s): %s",
                     len(working), ", ".join(working))
        return working

    def close(self) -> None:
        self._conn.close()
