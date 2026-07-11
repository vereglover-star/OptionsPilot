"""SQLite-backed candle cache.

Sits between the engine/backtester and any provider so repeated scans and
backtests never re-download the same bars. Keyed by (symbol, timeframe, ts).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from optionspilot.core.models import Timeframe
from optionspilot.data.base import CANDLE_COLUMNS, validate_candles

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol    TEXT    NOT NULL,
    timeframe INTEGER NOT NULL,          -- Timeframe minutes
    ts        INTEGER NOT NULL,          -- epoch seconds, UTC, bar open
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
) WITHOUT ROWID;
"""


class CandleCache:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def store(self, symbol: str, timeframe: Timeframe, candles: pd.DataFrame) -> int:
        """Upsert candles; returns number of rows written."""
        candles = validate_candles(candles)
        if candles.empty:
            return 0
        rows = [
            (symbol.upper(), timeframe.minutes, int(ts.timestamp()),
             r.open, r.high, r.low, r.close, r.volume)
            for ts, r in zip(candles.index, candles.itertuples(index=False))
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows
        )
        self._conn.commit()
        return len(rows)

    def load(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Cached candles in [start, end), canonical shape."""
        cur = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol.upper(), timeframe.minutes,
             int(start.timestamp()), int(end.timestamp())),
        )
        rows = cur.fetchall()
        if not rows:
            return validate_candles(pd.DataFrame())
        df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return validate_candles(df.set_index("ts"))

    def coverage(self, symbol: str, timeframe: Timeframe) -> tuple[datetime, datetime] | None:
        """(first, last) cached bar time, or None if nothing cached."""
        cur = self._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol.upper(), timeframe.minutes),
        )
        lo, hi = cur.fetchone()
        if lo is None:
            return None
        return (
            datetime.fromtimestamp(lo, tz=timezone.utc),
            datetime.fromtimestamp(hi, tz=timezone.utc),
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CandleCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
