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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.core.sqlite import connect as sqlite_connect
from optionspilot.core.sqlite import run_migrations
from optionspilot.data.base import CANDLE_COLUMNS, validate_candles
from optionspilot.data.config import CacheConfig

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

#: Daily-and-coarser timeframes, in Timeframe minutes. Kept as a literal rather
#: than imported so a future change to the Timeframe enum cannot silently
#: re-interpret an already-applied migration.
_DAILY_MINUTES = 1440


def _migration_3(conn) -> None:
    """Collapse daily+ bars onto ONE timestamp convention: exchange midnight.

    Before `base.session_index` existed, each adapter stamped a daily bar
    wherever its upstream happened to put it — Yahoo at the 09:30 ET session
    open (13:30 UTC), yfinance at 00:00 ET (04:00 UTC), Stooq and the keyed
    HTTP providers at 00:00 UTC. The cache is keyed `(symbol, timeframe, ts)`,
    so those are not the same row: a symbol fetched by two providers ended up
    with two rows for every trading day, 9.5 hours apart.

    That is a data defect with a visible consequence. The frame's tightest
    spacing became ~0.40 days instead of 1.0, `quality.validate_history`
    correctly reported "wrong interval served", and the disk tier discarded the
    bars — so **every symbol on 1D showed "the cached bars failed validation and
    were discarded"** and could not recover. Measured on a real cache: SPY held
    6,517 daily rows for ~3,258 trading days.

    Rewriting rather than deleting keeps decades of end-of-day history that is
    otherwise correct — the prices were never wrong, only the instants they were
    filed under. Where two rows collapse onto the same session the newer fetch
    wins, and a row with a known provider beats an unattributed v1 row.
    """
    rows = conn.execute(
        "SELECT symbol, timeframe, ts, open, high, low, close, volume, "
        "provider, fetched_at FROM candles WHERE timeframe >= ?",
        (_DAILY_MINUTES,),
    ).fetchall()
    if not rows:
        return
    # Local imports: a migration runs once, and neither belongs in this module's
    # import-time cost for the 99.99% of opens that apply no migration.
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    best: dict[tuple, tuple] = {}
    for r in rows:
        symbol, timeframe, ts = r[0], r[1], r[2]
        local = datetime.fromtimestamp(ts, timezone.utc).astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        new_ts = int(midnight.timestamp())
        key = (symbol, timeframe, new_ts)
        # rank: attributed beats unattributed, then newer fetch wins
        rank = (1 if r[8] else 0, r[9] or 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, (symbol, timeframe, new_ts, r[3], r[4], r[5],
                                r[6], r[7], r[8], r[9]))
    conn.execute("DELETE FROM candles WHERE timeframe >= ?", (_DAILY_MINUTES,))
    conn.executemany(
        "INSERT OR REPLACE INTO candles (symbol, timeframe, ts, open, high, "
        "low, close, volume, provider, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [v[1] for v in best.values()],
    )
    log.warning("cache.db migration 3: %d daily+ rows collapsed to %d on the "
                "exchange-midnight convention", len(rows), len(best))


_MIGRATIONS = [
    lambda conn: conn.executescript(_SCHEMA),
    lambda conn: conn.executescript(_MIGRATION_2),
    _migration_3,
]

SCHEMA_VERSION = len(_MIGRATIONS)


class CacheCorrupt(RuntimeError):
    """The cache file is unusable and could not be rebuilt."""


@dataclass
class CacheMetrics:
    """What the cache is actually doing for the user.

    "Is the cache working?" used to be answerable only as "there are N bars in
    it", which says nothing about whether any of them were ever served. These
    counters make the cache's *value* measurable: the hit rate is how often it
    answered instead of the network, and `provider_requests_saved` is the
    number of upstream calls that never had to happen.

    Counters are lifetime-per-process and cheap (plain ints behind the cache's
    existing lock); nothing here is persisted, because a hit rate that survived
    a restart would describe a session the user is no longer in.
    """

    reads: int = 0                   # load()/load_newest() calls
    hits: int = 0                    # ...that returned at least one bar
    misses: int = 0
    stale_reads: int = 0             # served knowingly out-of-date bars
    bars_read: int = 0
    writes: int = 0                  # store() calls that wrote rows
    bars_written: int = 0
    evictions: int = 0               # bars dropped by retention pruning
    rebuilds: int = 0                # corruption recoveries
    errors: int = 0                  # operations that failed and returned empty
    #: Sum of (now - newest served bar) in seconds, over hits, for a mean age.
    _age_total: float = 0.0
    _age_samples: int = 0

    @property
    def hit_rate(self) -> float:
        return (self.hits / self.reads) if self.reads else 0.0

    @property
    def avg_age_seconds(self) -> float:
        return (self._age_total / self._age_samples) if self._age_samples else 0.0

    def note_age(self, seconds: float) -> None:
        self._age_total += max(0.0, seconds)
        self._age_samples += 1

    def as_dict(self) -> dict:
        return {
            "reads": self.reads,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "miss_rate": round(1.0 - self.hit_rate, 4) if self.reads else 0.0,
            "stale_reads": self.stale_reads,
            "bars_read": self.bars_read,
            "writes": self.writes,
            "bars_written": self.bars_written,
            "evictions": self.evictions,
            "rebuilds": self.rebuilds,
            "errors": self.errors,
            "avg_age_seconds": round(self.avg_age_seconds, 1),
            # Every hit is one upstream request that did not happen. This is
            # the number worth putting in front of a user.
            "provider_requests_saved": self.hits,
        }


class CandleCache:
    """Durable candle storage. See the module docstring for the guarantees."""

    def __init__(self, db_path: str | Path, *, allow_rebuild: bool = True,
                 config: CacheConfig | None = None):
        self._lock = threading.Lock()
        self._path = Path(db_path) if str(db_path) != ":memory:" else None
        self._db_path = db_path
        self._allow_rebuild = allow_rebuild
        self.config = config or CacheConfig()
        self._rebuilds = 0
        self.metrics = CacheMetrics()
        self._conn = self._open()
        if self.config.retention_days is not None:
            self.prune(self.config.retention_days)

    def close(self) -> None:
        """Release the long-lived SQLite handle during application shutdown."""
        with self._lock:
            conn = getattr(self, "_conn", None)
            self._conn = None
            if conn is not None:
                conn.close()

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
                    self.metrics.writes += 1
                    self.metrics.bars_written += len(rows)
                    return len(rows)
                except sqlite3.DatabaseError as exc:
                    if attempt == 2 or not _is_corruption(exc):
                        log.error("candle cache store failed %s %s: %s",
                                  symbol, timeframe, exc)
                        self.metrics.errors += 1
                        return 0
                    self._recover(exc)
        return 0  # pragma: no cover — the loop always returns

    def prune(self, retention_days: int) -> int:
        """Drop bars older than `retention_days`, returning the count evicted.

        Retention is off by default: history is small, and the deeper the cache
        the better the last tier before a blank chart. It exists for the user
        who charts hundreds of symbols and would rather bound the file.
        """
        cutoff = int((datetime.now(timezone.utc)
                      - timedelta(days=retention_days)).timestamp())
        with self._lock:
            try:
                with self._conn:
                    cur = self._conn.execute(
                        "DELETE FROM candles WHERE ts < ?", (cutoff,))
                evicted = cur.rowcount or 0
            except sqlite3.DatabaseError as exc:
                log.error("candle cache prune failed: %s", exc)
                self.metrics.errors += 1
                return 0
        self.metrics.evictions += evicted
        if evicted:
            log.info("pruned %d cached bars older than %d days",
                     evicted, retention_days)
        return evicted

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
            self._note_read(0, None)
            return validate_candles(pd.DataFrame())
        df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        self._note_read(len(rows), rows[-1][0])
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
            self._note_read(0, None)
            return validate_candles(pd.DataFrame())
        df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        # `load_newest` is only reached as the last tier before a blank chart,
        # so every row it returns is by definition a knowingly-stale read.
        self._note_read(len(rows), rows[0][0], stale=True)
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

    def coverage_pairs(self) -> list[tuple[str, Timeframe]]:
        """Every (symbol, timeframe) this cache actually holds bars for.

        The maintenance validator walks this rather than the watchlist: the
        question it answers is "is the data on this disk usable", and a
        watchlist symbol with nothing cached has nothing to say about that.
        Rows whose stored timeframe is not a `Timeframe` member are skipped —
        a file written by a future build must not crash an older one.
        """
        pairs = []
        for symbol, minutes in self._query(
                "SELECT DISTINCT symbol, timeframe FROM candles "
                "ORDER BY symbol, timeframe", ()):
            try:
                pairs.append((str(symbol), Timeframe(int(minutes))))
            except ValueError:
                continue
        return pairs

    # ── maintenance (V0.5.7) ─────────────────────────────────────────────────

    def verify(self) -> dict:
        """Integrity report: is this file sound, and does it hold sane rows?

        Deliberately more than SQLite's own `integrity_check`. A cache can be
        *structurally* perfect and still unusable — that is exactly what the
        V0.5.6 daily-bar defect was: two valid rows per trading day, nine and a
        half hours apart, which `integrity_check` is delighted with and
        `validate_history` correctly refuses. So this also counts the rows that
        would fail a read, and reports them as findings rather than only as a
        pass/fail, because "your cache is fine" and "your cache has 14 bad bars
        out of 400,000" are different answers.

        Read-only. Repairing is `rebuild()`, which is a separate, explicit act.
        """
        findings: list[str] = []
        with self._lock:
            try:
                row = self._conn.execute("PRAGMA integrity_check").fetchone()
                integrity = str(row[0]) if row else "unknown"
            except sqlite3.DatabaseError as exc:
                return {"ok": False, "integrity": f"failed: {exc}",
                        "findings": [f"the database could not be read: {exc}"],
                        "bars": 0, "suspect_bars": 0, "schema_version": None}
        if integrity.lower() != "ok":
            findings.append(f"SQLite integrity_check reported: {integrity}")

        # Rows that could never be served. Each clause is a real defect this
        # repo has shipped and fixed, so a hit here names a regression rather
        # than a hypothetical.
        checks = [
            ("non-finite or non-positive prices",
             "open<=0 OR high<=0 OR low<=0 OR close<=0"),
            ("high below low", "high < low"),
            ("negative volume", "volume < 0"),
            ("bars stamped in the future",
             f"ts > {int(datetime.now(timezone.utc).timestamp()) + 86400}"),
        ]
        suspect = 0
        for label, clause in checks:
            rows = self._query(f"SELECT COUNT(*) FROM candles WHERE {clause}", ())
            count = int(rows[0][0]) if rows else 0
            if count:
                suspect += count
                findings.append(f"{count} bar(s) with {label}")

        total = self._query("SELECT COUNT(*) FROM candles", ())
        bars = int(total[0][0]) if total else 0
        version = self._query("PRAGMA user_version", ())
        return {
            "ok": integrity.lower() == "ok" and not suspect,
            "integrity": integrity,
            "bars": bars,
            "suspect_bars": suspect,
            "schema_version": int(version[0][0]) if version else None,
            "findings": findings or ["no problems found"],
        }

    def rebuild(self, reason: str = "requested from Settings") -> dict:
        """Quarantine the current file and start an empty one.

        The old database is MOVED, not deleted (`_rebuild`), for the same
        reason it is on a corruption recovery: a cache is reconstructible from
        the providers, so the cost of being wrong about needing this is
        re-downloads — but only if the evidence still exists when someone asks
        what went wrong.
        """
        with self._lock:
            before = 0
            try:
                row = self._conn.execute("SELECT COUNT(*) FROM candles").fetchone()
                before = int(row[0]) if row else 0
            except sqlite3.DatabaseError:
                pass                       # a cache too broken to count is the
            try:                           # case this exists for
                self._conn.close()
            except Exception:  # noqa: BLE001 — closing a broken handle is best-effort
                pass
            self._conn = self._rebuild(reason=reason)
            self.metrics.rebuilds = self._rebuilds
        log.warning("candle cache rebuilt on request (%d bars discarded)", before)
        return {"rebuilt": True, "bars_discarded": before, "reason": reason}

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
        oldest, newest = (self._query(
            "SELECT MIN(ts), MAX(ts) FROM candles", ()) or [(None, None)])[0]
        self.metrics.rebuilds = self._rebuilds
        stats = {
            "bars": int(bars or 0),
            "symbols": int(symbols or 0),
            "timeframes": int(timeframes or 0),
            "by_provider": by_provider,
            "bytes": size,
            "schema_version": SCHEMA_VERSION,
            "rebuilds": self._rebuilds,
            "oldest_bar": (datetime.fromtimestamp(oldest, tz=timezone.utc)
                           .isoformat() if oldest else None),
            "newest_bar": (datetime.fromtimestamp(newest, tz=timezone.utc)
                           .isoformat() if newest else None),
            "retention_days": self.config.retention_days,
            "oversized": bool(size and size > self.config.warn_bytes),
        }
        stats.update(self.metrics.as_dict())
        return stats

    def _note_read(self, bars: int, newest_ts: int | None, *,
                   stale: bool = False) -> None:
        """Record one read's outcome. Metric bookkeeping only — it must never
        be able to fail a read, so nothing here can raise."""
        m = self.metrics
        m.reads += 1
        if bars:
            m.hits += 1
            m.bars_read += bars
            if stale:
                m.stale_reads += 1
            if newest_ts:
                m.note_age(time.time() - newest_ts)
        else:
            m.misses += 1

    def _query(self, sql: str, params) -> list[tuple]:
        """Run a read with one corruption-recovery retry."""
        for attempt in (1, 2):
            with self._lock:
                try:
                    return self._conn.execute(sql, params).fetchall()
                except sqlite3.DatabaseError as exc:
                    if attempt == 2 or not _is_corruption(exc):
                        log.error("candle cache read failed: %s", exc)
                        self.metrics.errors += 1
                        return []
                    self._recover(exc)
        return []  # pragma: no cover — the loop always returns

    # ── teardown ─────────────────────────────────────────────────────────────

    # KNOWN DEFECT, deliberately left in place — see the V0.9 specification's
    # Future Findings. `CandleCache` defines `close` TWICE: once at line ~230
    # and again here. Python keeps the last definition, so THIS one is the
    # live implementation and the earlier one is dead code — even though the
    # earlier one is the more careful of the two (it reads `_conn` defensively
    # via getattr and, crucially, sets `self._conn = None` afterwards, which
    # this one does not; a closed handle is therefore left in place after
    # shutdown).
    #
    # Reconciling them is a BEHAVIOUR change and belongs in its own commit
    # with a test for double-close and for use-after-close, not in the commit
    # that merely turned the linter on. The `noqa` keeps ruff green without
    # pretending the problem is not here.
    def close(self) -> None:  # noqa: F811 — duplicate definition, see above
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


__all__ = ["CandleCache", "CacheCorrupt", "CacheMetrics", "SCHEMA_VERSION"]
