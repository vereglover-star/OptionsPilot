"""TradeJournal — the searchable system of record for every completed trade.

One row per round trip, with the full decision context frozen at entry time:
confidence, itemized entry reasons, supporting evidence names (for the learning
system), market conditions (HTF trend, ET hour, DTE, …), exit reason,
commissions, and human/AI annotations (mistakes, lessons).

The journal is deliberately the *only* input to the learning system: if it
isn't recorded here, the system can't learn from it — which forces recording
discipline.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Direction, Fill, TradePlan, TradeRecord

log = get_logger("journal")

ET = ZoneInfo("America/New_York")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL, contract_symbol TEXT NOT NULL, direction TEXT NOT NULL,
    strategy TEXT NOT NULL, quantity INTEGER NOT NULL,
    entry_ts TEXT NOT NULL, entry_price REAL NOT NULL,
    exit_ts TEXT NOT NULL, exit_price REAL NOT NULL,
    commissions REAL NOT NULL, confidence REAL NOT NULL,
    entry_reasons TEXT NOT NULL, exit_reason TEXT NOT NULL,
    market_conditions TEXT NOT NULL, indicators_used TEXT NOT NULL,
    mistakes TEXT NOT NULL, lessons TEXT NOT NULL,
    pnl REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades (entry_ts);
"""


class TradeJournal:
    def __init__(self, db_path: str | Path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # cross-thread reads from the UI server, serialized by its lock
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Bumped on every write so read-heavy callers (the UI status payload
        # every 2s) can cache derived views and only recompute on change.
        self.revision = 0

    def record(self, trade: TradeRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trades VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade.id, trade.symbol, trade.contract_symbol,
                trade.direction.value, trade.strategy, trade.quantity,
                trade.entry_ts.isoformat(), trade.entry_price,
                trade.exit_ts.isoformat(), trade.exit_price,
                trade.commissions, trade.confidence,
                json.dumps(trade.entry_reasons), trade.exit_reason,
                json.dumps(trade.market_conditions),
                json.dumps(trade.indicators_used),
                json.dumps(trade.mistakes), json.dumps(trade.lessons),
                trade.pnl,
            ),
        )
        self._conn.commit()
        self.revision += 1
        log.info("journaled %s: %s %s pnl %+.2f (%s)",
                 trade.id, trade.direction.value, trade.contract_symbol,
                 trade.pnl, trade.exit_reason)

    def get(self, trade_id: str) -> TradeRecord | None:
        row = self._conn.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def query(
        self,
        symbol: str | None = None,
        strategy: str | None = None,
        direction: Direction | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        wins_only: bool | None = None,
    ) -> list[TradeRecord]:
        clauses, params = [], []
        if symbol is not None:
            clauses.append("symbol=?"); params.append(symbol.upper())
        if strategy is not None:
            clauses.append("strategy=?"); params.append(strategy)
        if direction is not None:
            clauses.append("direction=?"); params.append(direction.value)
        if start is not None:
            clauses.append("entry_ts>=?"); params.append(start.isoformat())
        if end is not None:
            clauses.append("entry_ts<?"); params.append(end.isoformat())
        if wins_only is True:
            clauses.append("pnl>0")
        elif wins_only is False:
            clauses.append("pnl<=0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM trades {where} ORDER BY entry_ts", params
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def all(self) -> list[TradeRecord]:
        return self.query()

    def annotate(self, trade_id: str,
                 mistakes: list[str] | None = None,
                 lessons: list[str] | None = None) -> None:
        trade = self.get(trade_id)
        if trade is None:
            raise KeyError(f"no trade {trade_id!r} in journal")
        if mistakes:
            trade.mistakes.extend(mistakes)
        if lessons:
            trade.lessons.extend(lessons)
        self._conn.execute(
            "UPDATE trades SET mistakes=?, lessons=? WHERE id=?",
            (json.dumps(trade.mistakes), json.dumps(trade.lessons), trade_id),
        )
        self._conn.commit()
        self.revision += 1

    def stats(self) -> dict:
        trades = self.all()
        if not trades:
            return {"trades": 0}
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "trades": len(trades),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(trades), 4),
            "total_pnl": round(sum(pnls), 2),
            "expectancy": round(sum(pnls) / len(trades), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        }

    def close(self) -> None:
        self._conn.close()


def build_trade_record(
    trade_id: str,
    plan: TradePlan,
    quantity: int,
    entry_fill: Fill,
    exits: list[tuple[Fill, str]],       # (fill, reason) per closing fill
    market_conditions: dict[str, str] | None = None,
) -> TradeRecord:
    """Assemble the journal record for one round trip (entry + one or more
    closing fills, e.g. a partial then a stop)."""
    if not exits:
        raise ValueError("cannot journal a trade with no exit fills")
    total_qty = sum(f.quantity for f, _ in exits)
    exit_price = sum(f.price * f.quantity for f, _ in exits) / total_qty
    commissions = entry_fill.commission + sum(f.commission for f, _ in exits)
    signal = plan.signal
    conditions = dict(market_conditions or {})
    conditions.setdefault("hour_et", str(entry_fill.ts.astimezone(ET).hour))
    conditions.setdefault("dte", str(plan.contract.dte(entry_fill.ts.date())))
    conditions.setdefault("risk_reward", f"{plan.risk_reward:.2f}")
    return TradeRecord(
        id=trade_id,
        symbol=signal.symbol,
        contract_symbol=plan.contract.symbol,
        direction=signal.direction,
        strategy=signal.strategy,
        quantity=quantity,
        entry_ts=entry_fill.ts,
        entry_price=entry_fill.price,
        exit_ts=exits[-1][0].ts,
        exit_price=exit_price,
        commissions=commissions,
        confidence=signal.confidence,
        entry_reasons=signal.reasons,
        exit_reason=exits[-1][1],
        market_conditions=conditions,
        indicators_used=[e.name for e in signal.evidence if e.score > 0],
    )


def _row_to_record(row: tuple) -> TradeRecord:
    (tid, symbol, contract_symbol, direction, strategy, quantity,
     entry_ts, entry_price, exit_ts, exit_price, commissions, confidence,
     entry_reasons, exit_reason, market_conditions, indicators_used,
     mistakes, lessons, _pnl) = row
    return TradeRecord(
        id=tid, symbol=symbol, contract_symbol=contract_symbol,
        direction=Direction(direction), strategy=strategy, quantity=quantity,
        entry_ts=datetime.fromisoformat(entry_ts).astimezone(timezone.utc),
        entry_price=entry_price,
        exit_ts=datetime.fromisoformat(exit_ts).astimezone(timezone.utc),
        exit_price=exit_price, commissions=commissions, confidence=confidence,
        entry_reasons=json.loads(entry_reasons), exit_reason=exit_reason,
        market_conditions=json.loads(market_conditions),
        indicators_used=json.loads(indicators_used),
        mistakes=json.loads(mistakes), lessons=json.loads(lessons),
    )
