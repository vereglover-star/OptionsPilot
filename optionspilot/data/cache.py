"""SQLite-backed candle cache — the app's local, durable copy of market history.

This is not just a speed optimisation. It is the last tier of the fallback
chain: when every provider is unreachable, these bars are what stands between
the user and a blank chart. It therefore has to behave like storage that is
expected to survive crashes, power loss, and a corrupted file, rather than like
a cache that can be thrown away silently.

Guarantees:

- **Symbol / timeframe / timestamp keyed**, with the provider that supplied each
  bar and when it was fetched recorded alongside (schema v2). Provider
  attribution is what makes "these two sources disagree" diagnosable later
  instead of mysterious.
- **Atomic writes.** A store is one transaction: either every bar lands or none
  does. A crash mid-write can never leave half a frame, and an exception rolls
  back explicitly rather than relying on connection teardown.
- **Integrity checked on open** (`PRAGMA quick_check`). A corrupt file is
  quarantined next to itself as `<name>.corrupt-<timestamp>` and a fresh
  database is created, so a damaged cache degrades to a cold cache instead of
  crashing the app on launch.
- **Self-healing at runtime.** A `DatabaseError` raised by any operation trips
  the same rebuild path, once, and the operation is retried against the new
  file. The alternative — every candle read failing until the user finds and
  deletes a file they don't know exists — is not acceptable in a desktop app.
- **Versioned.** Schema changes go through `core.sqlite.run_migrations`; an
  existing v1 cache.db from an older build opens, migrates, and keeps its rows.
- **Thread-safe.** One connection guarded by one lock: candle fetches run on
  ThreadPoolExecutor workers (parallel scans) and FastAPI threadpool threads
  (`/api/candles`) while the connection is created on the main thread.
  sqlite3's default `check_same_thread=True` made every cross-thread call raise
  `ProgrammingError`, which callers' best-effort excepts swallowed — silently
  disabling the disk cache in exactly the threaded mode that ships.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.core.sqlite import connect as sqlite_connect
from optionspilot.core.sqlite import run_migrations
from optionspilot.data.base import CANDLE_COLUMNS, validate_candles

log = get_logger("data")

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

# v2: provider attribution + fetch time. Both nullable so v1 rows migrate with
# no rewrite (a WITHOUT ROWID table of hundreds of thousands of bars should not
# be rebuilt to add two columns).
_MIGRATION_2 = """
ALTER TABLE candles ADD COLUMN provider TEXT;
ALTER TABLE candles ADD COLUMN fetched_at INTEGER;
"""

_MIGRATIONS = [
    lambda conn: conn.executescript(_SCHEMA),
    lambda conn: conn.executescript(_MIGRATION_2),
]

SCHEMA_VERSION = len(_MIGRATIONS)


class CacheCorrupt(RuntimeError):
    """The cache file is unusable and could not be rebuilt."""


class CandleCache:
    """Durable candle storage. See the module docstring for the guarantees."""

    def __init__(self, db_path: str | Path, *, allow_rebuild: bool = True):
        self._lock = threading.Lock()
        self._path = Path(db_path) if str(db_path) != ":memory:" else None
        self._db_path = db_path
        self._allow_rebuild = allow_rebuild
        self._rebuilds = 0
        self._conn = self._open()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        # `sqlite_connect` itself raises on a damaged file — the very first
        # PRAGMA it issues fails with "file is not a database" — so the connect
        # MUST be inside the recovery block. Leaving it outside meant a corrupt
        # cache crashed the app during Orchestrator construction, before any of
        # the recovery below could run.
        conn = None
        try:
            conn = sqlite_connect(self._db_path, wal=True)
            self._check_integrity(conn)
            run_migrations(conn, _MIGRATIONS, label="cache.db")
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                conn.close()
            log.error("candle cache at %s is unusable (%s)", self._db_path, exc)
            return self._rebuild(reason=str(exc))
        return conn

    @staticmethod
    def _check_integrity(conn: sqlite3.Connection) -> None:
        """`quick_check` is the cheap variant of `integrity_check` — it catches
        structural damage without a full scan, which matters because this runs
        on every launch and the file grows to hundreds of megabytes."""
        row = conn.execute("PRAGMA quick_check(1)").fetchone()
        if row and str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"quick_check reported: {row[0]}")

    def _rebuild(self, reason: str) -> sqlite3.Connection:
        """Quarantine the damaged file and start a fresh one.

        The old file is MOVED, never deleted: if it turns out to hold something
        recoverable, it is still there. A cache is reconstructible from the
        providers, so losing it costs re-downloads, not data.
        """
        if not self._allow_rebuild or self._path is None:
            raise CacheCorrupt(f"candle cache is corrupt and rebuild is "
                               f"disabled: {reason}")
        self._rebuilds += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantine = self._path.with_suffix(self._path.suffix + f".corrupt-{stamp}")
        try:
            for suffix in ("", "-wal", "-shm"):
                sidecar = Path(str(self._path) + suffix)
                if sidecar.exists():
                    shutil.move(str(sidecar), str(quarantine) + suffix)
            log.warning("quarantined the corrupt candle cache as %s and "
                        "started a fresh one", quarantine.name)
        except OSError as exc:
            raise CacheCorrupt(
                f"could not quarantine the corrupt cache: {exc}") from exc
        conn = sqlite_connect(self._db_path, wal=True)
        run_migrations(conn, _MIGRATIONS, label="cache.db")
        return conn

    def _recover(self, exc: sqlite3.DatabaseError) -> None:
        """Runtime corruption handler: swap in a fresh database so the *next*
        call works. Called with the lock held."""
        log.error("candle cache operation failed (%s) — rebuilding", exc)
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 — already broken; closing is best-effort
            pass
        self._conn = self._rebuild(reason=str(exc))

    # ── writes ───────────────────────────────────────────────────────────────

    def store(self, symbol: str, timeframe: Timeframe, candles: pd.DataFrame,
              *, provider: str | None = None) -> int:
        """Upsert candles atomically; returns the number of rows written.

        Never raises for ordinary trouble — a cache write failing must not take
        down a chart that already has its data. Corruption triggers one rebuild
        and one retry; a second failure is logged and reported as zero rows.
        """
        candles = validate_candles(candles, context=f"cache.store {symbol} {timeframe}")
        if candles.empty:
            return 0
        fetched_at = int(time.time())
        rows = [
            (symbol.upper(), timeframe.minutes, int(ts.timestamp()),
             r.open, r.high, r.low, r.close, r.volume, provider, fetched_at)
            for ts, r in zip(candles.index, candles.itertuples(index=False))
        ]
        for attempt in (1, 2):
            with self._lock:
                try:
                    with self._conn:      # one transaction: all rows or none
                        self._conn.executemany(
                            "INSERT OR REPLACE INTO candles "
                            "(symbol,timeframe,ts,open,high,low,close,volume,"
                            "provider,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            rows,
                        )
                    return len(rows)
                except sqlite3.DatabaseError as exc:
                    if attempt == 2 or not _is_corruption(exc):
                        log.error("candle cache store failed %s %s: %s",
                                  symbol, timeframe, exc)
                        return 0
                    self._recover(exc)
        return 0  # pragma: no cover — the loop always returns

    def purge(self, symbol: str | None = None,
              timeframe: Timeframe | None = None) -> int:
        """Delete cached bars. Both arguments None clears everything."""
        clauses, params = [], []
        if symbol is not None:
            clauses.append("symbol=?")
            params.append(symbol.upper())
        if timeframe is not None:
            clauses.append("timeframe=?")
            params.append(timeframe.minutes)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            try:
                with self._conn:
                    cur = self._conn.execute(f"DELETE FROM candles{where}", params)
                return cur.rowcount or 0
            except sqlite3.DatabaseError as exc:
                log.error("candle cache purge failed: %s", exc)
                return 0

    # ── reads ────────────────────────────────────────────────────────────────

    def load(self, symbol: str, timeframe: Timeframe,
             start: datetime, end: datetime) -> pd.DataFrame:
        """Cached candles in [start, end), canonical shape.

        A read is validated exactly like a provider response: the file is
        untrusted storage, and a bad row that reached it (an older build, a
        partially-recovered file) must not reach a chart.
        """
        rows = self._query(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol.upper(), timeframe.minutes,
             int(start.timestamp()), int(end.timestamp())),
        )
        if not rows:
            return validate_candles(pd.DataFrame())
        df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return validate_candles(df.set_index("ts"),
                                context=f"cache.load {symbol} {timeframe}")

    def load_newest(self, symbol: str, timeframe: Timeframe,
                    limit: int = 5000) -> pd.DataFrame:
        """The newest `limit` cached bars regardless of window — the last-resort
        tier when a provider window can't be served at all."""
        rows = self._query(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
            (symbol.upper(), timeframe.minutes, int(limit)),
        )
        if not rows:
            return validate_candles(pd.DataFrame())
        df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return validate_candles(df.set_index("ts").sort_index(),
                                context=f"cache.load_newest {symbol} {timeframe}")

    def coverage(self, symbol: str,
                 timeframe: Timeframe) -> tuple[datetime, datetime] | None:
        """(first, last) cached bar time, or None if nothing cached."""
        rows = self._query(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol.upper(), timeframe.minutes),
        )
        lo, hi = rows[0] if rows else (None, None)
        if lo is None:
            return None
        return (datetime.fromtimestamp(lo, tz=timezone.utc),
                datetime.fromtimestamp(hi, tz=timezone.utc))

    def stats(self) -> dict:
        """Size/shape of the cache, for the diagnostics endpoint."""
        rows = self._query(
            "SELECT COUNT(*), COUNT(DISTINCT symbol), COUNT(DISTINCT timeframe) "
            "FROM candles", ())
        bars, symbols, timeframes = rows[0] if rows else (0, 0, 0)
        by_provider = {str(name or "unknown"): int(count) for name, count in
                       self._query("SELECT provider, COUNT(*) FROM candles "
                                   "GROUP BY provider", ())}
        size = None
        if self._path is not None and self._path.exists():
            size = sum(Path(str(self._path) + s).stat().st_size
                       for s in ("", "-wal", "-shm")
                       if Path(str(self._path) + s).exists())
        return {
            "bars": int(bars or 0),
            "symbols": int(symbols or 0),
            "timeframes": int(timeframes or 0),
            "by_provider": by_provider,
            "bytes": size,
            "schema_version": SCHEMA_VERSION,
            "rebuilds": self._rebuilds,
        }

    def _query(self, sql: str, params) -> list[tuple]:
        """Run a read with one corruption-recovery retry."""
        for attempt in (1, 2):
            with self._lock:
                try:
                    return self._conn.execute(sql, params).fetchall()
                except sqlite3.DatabaseError as exc:
                    if attempt == 2 or not _is_corruption(exc):
                        log.error("candle cache read failed: %s", exc)
                        return []
                    self._recover(exc)
        return []  # pragma: no cover — the loop always returns

    # ── teardown ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — closing must never raise
                pass

    def __enter__(self) -> "CandleCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    """Distinguish "this file is damaged" from ordinary SQL errors.

    Only the former justifies throwing the file away; a programming error must
    surface as itself rather than silently wiping the user's cache.
    """
    if isinstance(exc, sqlite3.NotSupportedError):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in (
        "malformed", "not a database", "corrupt", "disk image"))


__all__ = ["CandleCache", "CacheCorrupt", "SCHEMA_VERSION"]
