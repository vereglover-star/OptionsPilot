"""PaperBroker — a high-fidelity fill simulator with a persistent account.

Realism model:
  - Buys fill at the ask, sells at the bid (you always cross the spread).
  - Slippage worsens the fill by `slippage_pct` of the premium.
  - Commission per contract on every fill, both sides.
  - One position per contract symbol (adds to an existing position are
    averaged in).

Accounting: `avg_price` is the average *fill* price (commissions excluded);
entry commissions reduce cash immediately, exit commissions are charged inside
the realized P&L of each close. Account, positions, and the full fill log are
persisted to SQLite, so the paper account survives restarts.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from optionspilot.broker.base import AccountState, Broker, BrokerError
from optionspilot.config.settings import BrokerConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.sqlite import connect as sqlite_connect
from optionspilot.core.sqlite import run_migrations
from optionspilot.core.models import (
    Direction, Fill, OptionContract, OptionRight, Position, TradePlan,
)

log = get_logger("broker")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    realized_pnl REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    underlying TEXT NOT NULL, expiration TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL,
    quantity INTEGER NOT NULL, avg_price REAL NOT NULL,
    opened_at TEXT NOT NULL, direction TEXT NOT NULL,
    entry_spot REAL NOT NULL, stop_current REAL NOT NULL,
    target REAL NOT NULL, partials TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, order_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, quantity INTEGER NOT NULL,
    price REAL NOT NULL, commission REAL NOT NULL, reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS equity_history (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL
);
"""

def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _migration_2(conn: sqlite3.Connection) -> None:
    # v2: manual trading — who manages this position's exits. Idempotent: a
    # database created before user_version tracking already has this column
    # (added by the prior ALTER-on-every-open code and left at user_version 0),
    # so a duplicate-column error is expected and swallowed — identical to the
    # behavior this replaced.
    try:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'ai'")
    except sqlite3.OperationalError:
        pass  # column already present


_MIGRATIONS = [_migration_1, _migration_2]


class PaperBroker(Broker):
    def __init__(self, cfg: BrokerConfig, db_path: str | Path, starting_cash: float):
        self._cfg = cfg
        # check_same_thread=False: the UI server calls from worker threads;
        # access is serialized by UIServer's lock. wal=False preserves the
        # account database's historical rollback-journal mode.
        self._conn = sqlite_connect(db_path, wal=False)
        run_migrations(self._conn, _MIGRATIONS, label="paper.db")
        row = self._conn.execute("SELECT cash, realized_pnl FROM account").fetchone()
        if row is None:
            self._cash, self._realized = starting_cash, 0.0
            self._conn.execute("INSERT INTO account VALUES (1, ?, ?)",
                               (self._cash, self._realized))
            self._conn.commit()
            log.info("paper account created with %.2f cash", starting_cash)
        else:
            self._cash, self._realized = row
            log.info("paper account restored: cash %.2f, realized %.2f",
                     self._cash, self._realized)
        self._positions: dict[str, Position] = self._load_positions()
        self._marks: dict[str, float] = {}

    # ── Broker interface ─────────────────────────────────────────────────────

    def open_position(self, plan: TradePlan, quantity: int, ts: datetime) -> Fill:
        if quantity < 1:
            raise BrokerError(f"invalid quantity {quantity}")
        contract = plan.contract
        if contract.ask <= 0:
            raise BrokerError(f"{contract.symbol}: no ask price to fill against")
        fill_price = round(contract.ask * (1 + self._cfg.slippage_pct), 4)
        commission = self._cfg.commission_per_contract * quantity
        cost = fill_price * 100 * quantity + commission
        if cost > self._cash:
            raise BrokerError(
                f"insufficient cash: need {cost:.2f}, have {self._cash:.2f}"
            )

        fill = Fill(order_id=str(uuid.uuid4()), ts=ts, quantity=quantity,
                    price=fill_price, commission=commission)
        self._cash -= cost

        existing = self._positions.get(contract.symbol)
        if existing is not None:
            total = existing.quantity + quantity
            existing.avg_price = (
                existing.avg_price * existing.quantity + fill_price * quantity
            ) / total
            existing.quantity = total
            position = existing
        else:
            position = Position(
                contract=contract, quantity=quantity, avg_price=fill_price,
                opened_at=ts, plan=plan,
                direction=plan.signal.direction,
                entry_spot=plan.spot,
                stop_current=plan.stop_underlying,
                target=plan.target_underlying,
                partials_remaining=plan.partial_levels,
            )
            self._positions[contract.symbol] = position

        self._marks.setdefault(contract.symbol, contract.mid or fill_price)
        self._persist(position, fill, side="buy_to_open", reason=plan.signal.strategy)
        log.info("OPEN %s x%d @ %.2f (commission %.2f, cash %.2f)",
                 contract.symbol, quantity, fill_price, commission, self._cash)
        return fill

    def close_position(
        self, contract_symbol: str, quantity: int, bid: float, ts: datetime,
        reason: str = "",
    ) -> tuple[Fill, float]:
        position = self._positions.get(contract_symbol)
        if position is None:
            raise BrokerError(f"no open position in {contract_symbol}")
        if not (1 <= quantity <= position.quantity):
            raise BrokerError(
                f"invalid close quantity {quantity} (held: {position.quantity})"
            )
        if bid <= 0:
            raise BrokerError(f"{contract_symbol}: no bid price to fill against")

        fill_price = round(bid * (1 - self._cfg.slippage_pct), 4)
        commission = self._cfg.commission_per_contract * quantity
        proceeds = fill_price * 100 * quantity - commission
        realized = (fill_price - position.avg_price) * 100 * quantity - commission

        fill = Fill(order_id=str(uuid.uuid4()), ts=ts, quantity=quantity,
                    price=fill_price, commission=commission)
        self._cash += proceeds
        self._realized += realized
        position.quantity -= quantity
        if position.quantity == 0:
            del self._positions[contract_symbol]
            self._marks.pop(contract_symbol, None)
            self._conn.execute("DELETE FROM positions WHERE symbol=?", (contract_symbol,))
            self._persist(None, fill, side="sell_to_close",
                          symbol=contract_symbol, reason=reason)
        else:
            self._persist(position, fill, side="sell_to_close", reason=reason)
        log.info("CLOSE %s x%d @ %.2f -> realized %+.2f (%s)",
                 contract_symbol, quantity, fill_price, realized, reason or "n/a")
        return fill, round(realized, 2)

    def mark_positions(self, marks: dict[str, float]) -> None:
        for symbol, price in marks.items():
            if symbol in self._positions and price > 0:
                self._marks[symbol] = price

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def current_marks(self) -> dict[str, float]:
        """Latest option marks per contract symbol (for UI display)."""
        return dict(self._marks)

    def get_account(self) -> AccountState:
        open_value = sum(
            p.quantity * self._marks.get(s, p.avg_price) * 100
            for s, p in self._positions.items()
        )
        return AccountState(
            cash=round(self._cash, 2),
            equity=round(self._cash + open_value, 2),
            realized_pnl=round(self._realized, 2),
        )

    def open_manual(self, contract: OptionContract, quantity: int, ts: datetime,
                    entry_spot: float = 0.0) -> Fill:
        """Buy to open WITHOUT a TradePlan — the manual (Human Mode) path.
        Exits are the user's job (market/limit/stop orders via the
        OrderManager); the AI position manager leaves these alone."""
        if quantity < 1:
            raise BrokerError(f"invalid quantity {quantity}")
        if contract.ask <= 0:
            raise BrokerError(f"{contract.symbol}: no ask price to fill against")
        fill_price = round(contract.ask * (1 + self._cfg.slippage_pct), 4)
        commission = self._cfg.commission_per_contract * quantity
        cost = fill_price * 100 * quantity + commission
        if cost > self._cash:
            raise BrokerError(
                f"insufficient cash: need {cost:.2f}, have {self._cash:.2f}"
            )
        fill = Fill(order_id=str(uuid.uuid4()), ts=ts, quantity=quantity,
                    price=fill_price, commission=commission)
        self._cash -= cost
        existing = self._positions.get(contract.symbol)
        if existing is not None:
            total = existing.quantity + quantity
            existing.avg_price = (
                existing.avg_price * existing.quantity + fill_price * quantity
            ) / total
            existing.quantity = total
            position = existing
        else:
            position = Position(
                contract=contract, quantity=quantity, avg_price=fill_price,
                opened_at=ts, plan=None,
                direction=(Direction.LONG if contract.right is OptionRight.CALL
                           else Direction.SHORT),
                entry_spot=entry_spot, managed_by="manual",
            )
            self._positions[contract.symbol] = position
        self._marks.setdefault(contract.symbol, contract.mid or fill_price)
        self._persist(position, fill, side="buy_to_open", reason="manual")
        log.info("OPEN(manual) %s x%d @ %.2f (cash %.2f)",
                 contract.symbol, quantity, fill_price, self._cash)
        return fill

    def fills_for(self, contract_symbol: str) -> list[dict]:
        """Full fill history for one contract — the raw material for
        reconstructing manual round trips."""
        rows = self._conn.execute(
            "SELECT ts, side, quantity, price, commission, reason FROM fills "
            "WHERE symbol=? ORDER BY id", (contract_symbol,),
        ).fetchall()
        return [
            {"ts": ts, "side": side, "quantity": qty, "price": price,
             "commission": commission, "reason": reason}
            for ts, side, qty, price, commission, reason in rows
        ]

    def record_equity_snapshot(self, ts: datetime) -> None:
        """Persist an equity point (minute resolution) for lifetime metrics
        like max drawdown and total-return charts."""
        stamp = ts.replace(second=0, microsecond=0).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO equity_history VALUES (?, ?)",
            (stamp, self.get_account().equity),
        )
        self._conn.commit()

    def equity_history(self, last: int = 5000) -> list[tuple[str, float]]:
        rows = self._conn.execute(
            "SELECT ts, equity FROM equity_history ORDER BY ts DESC LIMIT ?",
            (last,),
        ).fetchall()
        return rows[::-1]

    def update_position_management(self, position: Position) -> None:
        """Persist trailed stops / consumed partial levels."""
        self._save_position(position)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── persistence ──────────────────────────────────────────────────────────

    def _persist(self, position: Position | None, fill: Fill, side: str,
                 symbol: str | None = None, reason: str = "") -> None:
        self._conn.execute(
            "UPDATE account SET cash=?, realized_pnl=? WHERE id=1",
            (self._cash, self._realized),
        )
        if position is not None:
            self._save_position(position)
            symbol = position.contract.symbol
        self._conn.execute(
            "INSERT INTO fills (ts, order_id, symbol, side, quantity, price, "
            "commission, reason) VALUES (?,?,?,?,?,?,?,?)",
            (fill.ts.isoformat(), fill.order_id, symbol, side,
             fill.quantity, fill.price, fill.commission, reason),
        )
        self._conn.commit()

    def _save_position(self, p: Position) -> None:
        c = p.contract
        self._conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.symbol, c.underlying, c.expiration.isoformat(), c.strike,
             c.right.value, p.quantity, p.avg_price, p.opened_at.isoformat(),
             p.direction.value, p.entry_spot, p.stop_current, p.target,
             ",".join(str(x) for x in p.partials_remaining), p.managed_by),
        )

    def _load_positions(self) -> dict[str, Position]:
        rows = self._conn.execute(
            "SELECT symbol, underlying, expiration, strike, right, quantity, "
            "avg_price, opened_at, direction, entry_spot, stop_current, target, "
            "partials, managed_by FROM positions"
        ).fetchall()
        out: dict[str, Position] = {}
        for (symbol, underlying, expiration, strike, right, quantity, avg_price,
             opened_at, direction, entry_spot, stop_current, target, partials,
             managed_by) in rows:
            contract = OptionContract(
                underlying=underlying, expiration=date.fromisoformat(expiration),
                strike=strike, right=OptionRight(right),
            )
            out[symbol] = Position(
                contract=contract, quantity=quantity, avg_price=avg_price,
                opened_at=datetime.fromisoformat(opened_at).astimezone(timezone.utc),
                plan=None,
                direction=Direction(direction), entry_spot=entry_spot,
                stop_current=stop_current, target=target,
                partials_remaining=tuple(
                    float(x) for x in partials.split(",") if x
                ),
                managed_by=managed_by,
            )
        if out:
            log.info("restored %d open position(s): %s",
                     len(out), ", ".join(out))
        return out
