"""ExperienceStore — the scalable, migratable system of memory.

Storage strategy (designed for 100,000+ trades without a redesign):
  - A HYBRID row: the fields the documented queries filter on live in real,
    indexed SQLite columns; the complete `ExperienceRecord` is also stored as a
    JSON `payload` so full fidelity survives even as the schema grows. The
    payload is authoritative on read — columns exist only to make filtering
    fast, so they can never drift from the record.
  - Coarse SQL filtering bounds the candidate set BEFORE any Python-side
    similarity distance runs (see SimilarityEngine). At 100k rows an indexed
    filter returns a few thousand candidates that a vectorized distance pass
    ranks in well under a second.

Migrations use the shared `core.sqlite` foundation (`PRAGMA user_version`).
Append a step to `_MIGRATIONS` for any structural change (the target version is
`len(_MIGRATIONS)`); most new per-trade fields need no migration at all because
they ride in the record's `extra` blob.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.sqlite import connect as sqlite_connect
from optionspilot.core.sqlite import run_migrations, schema_version
from optionspilot.experience.models import ExperienceRecord

log = get_logger("experience")

_DT_FIELDS = ("recorded_ts", "entry_ts", "exit_ts")
_RECORD_FIELDS = {f.name for f in fields(ExperienceRecord)}

# Indexed columns that `aggregate()` is allowed to GROUP BY. A hard whitelist
# so no caller can inject an arbitrary column name into SQL.
_GROUPABLE = frozenset({
    "strategy", "market_regime", "market_session", "volatility_bucket",
    "setup_quality", "direction", "managed_by",
})


def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS experiences (
            trade_id           TEXT PRIMARY KEY,
            recorded_ts        TEXT NOT NULL,
            entry_ts           TEXT NOT NULL,
            symbol             TEXT NOT NULL,
            direction          TEXT NOT NULL,
            strategy           TEXT NOT NULL,
            managed_by         TEXT NOT NULL,
            setup_quality      TEXT,
            market_session     TEXT,
            volatility_bucket  TEXT,
            exploration        INTEGER NOT NULL DEFAULT 0,
            is_win             INTEGER NOT NULL,
            confidence_entry   REAL,
            pnl                REAL,
            exit_reason        TEXT,
            payload            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_exp_symbol       ON experiences (symbol);
        CREATE INDEX IF NOT EXISTS idx_exp_entry        ON experiences (entry_ts);
        CREATE INDEX IF NOT EXISTS idx_exp_direction    ON experiences (direction);
        CREATE INDEX IF NOT EXISTS idx_exp_strategy     ON experiences (strategy);
        CREATE INDEX IF NOT EXISTS idx_exp_setup        ON experiences (setup_quality);
        CREATE INDEX IF NOT EXISTS idx_exp_session      ON experiences (market_session);
        CREATE INDEX IF NOT EXISTS idx_exp_vol          ON experiences (volatility_bucket);
        CREATE INDEX IF NOT EXISTS idx_exp_win          ON experiences (is_win);
        """
    )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Add columns used by the aggregate/regime statistics (Phase 3), backfilled
    from each row's authoritative JSON payload (a no-op on an empty DB)."""
    conn.execute("ALTER TABLE experiences ADD COLUMN market_regime TEXT")
    conn.execute("ALTER TABLE experiences ADD COLUMN return_pct REAL")
    conn.execute("ALTER TABLE experiences ADD COLUMN hold_minutes REAL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_regime ON experiences (market_regime)")
    from optionspilot.experience.features import market_regime  # lazy: avoid import cycle
    for tid, payload in conn.execute(
            "SELECT trade_id, payload FROM experiences").fetchall():
        doc = json.loads(payload)
        regime = doc.get("market_regime") or market_regime(
            doc.get("htf_trend"), doc.get("iv"))
        conn.execute(
            "UPDATE experiences SET market_regime=?, return_pct=?, hold_minutes=? "
            "WHERE trade_id=?",
            (regime, doc.get("return_pct"), doc.get("hold_minutes"), tid),
        )


# Ordered; index i applies to reach user_version i+1.
_MIGRATIONS = [_migration_1, _migration_2]


class ExperienceStore:
    def __init__(self, db_path: str | Path):
        # cross-thread reads from the UI server, serialized by its lock.
        self._conn = sqlite_connect(db_path, wal=True)
        run_migrations(
            self._conn, _MIGRATIONS, label="experience.db",
            on_migrate=lambda v: log.info("experience.db migrated to schema v%d", v),
        )
        # Bumped on every write so read-heavy callers can cache derived views.
        self.revision = 0

    @property
    def schema_version(self) -> int:
        return schema_version(self._conn)

    # ── writes ──────────────────────────────────────────────────────────────

    _INSERT = (
        "INSERT OR REPLACE INTO experiences ("
        "trade_id, recorded_ts, entry_ts, symbol, direction, strategy, "
        "managed_by, setup_quality, market_session, volatility_bucket, "
        "market_regime, exploration, is_win, confidence_entry, pnl, "
        "return_pct, hold_minutes, exit_reason, payload"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )

    def record(self, rec: ExperienceRecord) -> None:
        self._conn.execute(self._INSERT, _row_tuple(rec))
        self._conn.commit()
        self.revision += 1
        log.info("experience recorded %s (%s %s pnl %+.2f)",
                 rec.trade_id, rec.managed_by, rec.direction, rec.pnl)

    def record_many(self, recs: list[ExperienceRecord]) -> None:
        """Bulk insert in one transaction — used by tests/imports; the live
        path records one trade at a time via `record`."""
        self._conn.executemany(self._INSERT, [_row_tuple(r) for r in recs])
        self._conn.commit()
        self.revision += 1

    # ── reads ───────────────────────────────────────────────────────────────

    def get(self, trade_id: str) -> ExperienceRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM experiences WHERE trade_id=?", (trade_id,)
        ).fetchone()
        return _from_payload(row[0]) if row else None

    def query(
        self,
        *,
        symbol: str | None = None,
        direction: str | None = None,
        strategy: str | None = None,
        managed_by: str | None = None,
        setup_quality: str | None = None,
        market_session: str | None = None,
        volatility_bucket: str | None = None,
        market_regime: str | None = None,
        exploration: bool | None = None,
        wins_only: bool | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[ExperienceRecord]:
        """Coarse indexed filter. Similarity ranking happens in Python on top
        of whatever candidate set this returns."""
        clauses: list[str] = []
        params: list = []
        eq = {
            "symbol": symbol.upper() if symbol else None,
            "direction": direction,
            "strategy": strategy,
            "managed_by": managed_by,
            "setup_quality": setup_quality,
            "market_session": market_session,
            "volatility_bucket": volatility_bucket,
            "market_regime": market_regime,
        }
        for col, val in eq.items():
            if val is not None:
                clauses.append(f"{col}=?")
                params.append(val)
        if exploration is not None:
            clauses.append("exploration=?"); params.append(1 if exploration else 0)
        if wins_only is True:
            clauses.append("is_win=1")
        elif wins_only is False:
            clauses.append("is_win=0")
        if start is not None:
            clauses.append("entry_ts>=?"); params.append(start.isoformat())
        if end is not None:
            clauses.append("entry_ts<?"); params.append(end.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT payload FROM experiences {where} ORDER BY entry_ts"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [_from_payload(r[0]) for r in rows]

    def all(self) -> list[ExperienceRecord]:
        return self.query()

    def recent(self, limit: int = 50) -> list[ExperienceRecord]:
        """Most recently entered experiences, newest first."""
        rows = self._conn.execute(
            "SELECT payload FROM experiences ORDER BY entry_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_from_payload(r[0]) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]

    def overview(self) -> dict:
        """Whole-store headline stats — SQL only, so it stays fast at 100k+."""
        n, wins, total_pnl, avg_ret, avg_hold, expl = self._conn.execute(
            "SELECT COUNT(*), SUM(is_win), SUM(pnl), AVG(return_pct), "
            "AVG(hold_minutes), SUM(exploration) FROM experiences"
        ).fetchone()
        by_mgmt = dict(self._conn.execute(
            "SELECT managed_by, COUNT(*) FROM experiences GROUP BY managed_by"
        ).fetchall())
        if not n:
            return {"total": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0,
                    "avg_return_pct": 0.0, "avg_hold_minutes": 0.0,
                    "exploration": 0, "by_management": {}}
        wins = wins or 0
        return {
            "total": n,
            "wins": wins,
            "win_rate": round(wins / n, 4),
            "total_pnl": round(total_pnl or 0.0, 2),
            "avg_return_pct": round(avg_ret, 2) if avg_ret is not None else 0.0,
            "avg_hold_minutes": round(avg_hold, 2) if avg_hold is not None else 0.0,
            "exploration": expl or 0,
            "by_management": by_mgmt,
        }

    # ── aggregate statistics (SQL only — never deserializes payloads) ─────────

    def aggregate(self, group_by: str) -> list[dict]:
        """Per-group performance stats over an indexed column. Fast at 100k+
        because it never touches the JSON payload — pure SQL COUNT/SUM/AVG.
        `group_by` must be one of the `_GROUPABLE` whitelist."""
        if group_by not in _GROUPABLE:
            raise ValueError(
                f"cannot group by {group_by!r}; allowed: {sorted(_GROUPABLE)}")
        rows = self._conn.execute(
            f"SELECT {group_by} AS g, COUNT(*), SUM(is_win), SUM(pnl), "
            f"AVG(return_pct), AVG(hold_minutes) FROM experiences "
            f"GROUP BY {group_by} ORDER BY COUNT(*) DESC"
        ).fetchall()
        out = []
        for g, n, wins, total_pnl, avg_ret, avg_hold in rows:
            wins = wins or 0
            out.append({
                "group": g if g is not None else "unknown",
                "trades": n,
                "wins": wins,
                "win_rate": round(wins / n, 4) if n else 0.0,
                "total_pnl": round(total_pnl or 0.0, 2),
                "avg_return_pct": round(avg_ret, 2) if avg_ret is not None else None,
                "avg_hold_minutes": round(avg_hold, 2) if avg_hold is not None else None,
            })
        return out

    def exit_reason_counts(self, *, wins: bool, limit: int = 10) -> list[dict]:
        """Most frequent exit reasons among winners (`wins=True`) or losers,
        for success-pattern / failure-mode reporting. SQL-only."""
        rows = self._conn.execute(
            "SELECT exit_reason, COUNT(*) AS n FROM experiences "
            "WHERE is_win=? AND exit_reason<>'' "
            "GROUP BY exit_reason ORDER BY n DESC LIMIT ?",
            (1 if wins else 0, int(limit)),
        ).fetchall()
        return [{"reason": r, "count": n} for r, n in rows]

    def close(self) -> None:
        self._conn.close()


# ── (de)serialization ────────────────────────────────────────────────────────

def _row_tuple(rec: ExperienceRecord) -> tuple:
    """The indexed-column values + JSON payload for one row (order matches
    `ExperienceStore._INSERT`)."""
    return (
        rec.trade_id,
        rec.recorded_ts.isoformat(),
        rec.entry_ts.isoformat(),
        rec.symbol,
        rec.direction,
        rec.strategy,
        rec.managed_by,
        rec.setup_quality,
        rec.market_session,
        rec.volatility_bucket,
        rec.market_regime,
        1 if rec.exploration else 0,
        1 if rec.is_win else 0,
        rec.confidence_entry,
        rec.pnl,
        rec.return_pct,
        rec.hold_minutes,
        rec.exit_reason,
        _to_payload(rec),
    )


def _to_payload(rec: ExperienceRecord) -> str:
    doc = asdict(rec)
    for k in _DT_FIELDS:
        doc[k] = doc[k].isoformat()
    return json.dumps(doc)


def _from_payload(payload: str) -> ExperienceRecord:
    doc = json.loads(payload)
    for k in _DT_FIELDS:
        doc[k] = datetime.fromisoformat(doc[k]).astimezone(timezone.utc)
    # Tolerate payloads written by an older build that lacked a field now in the
    # dataclass (forward-compat): drop unknown keys, let defaults fill new ones.
    known = {k: v for k, v in doc.items() if k in _RECORD_FIELDS}
    return ExperienceRecord(**known)
