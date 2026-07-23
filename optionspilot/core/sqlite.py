"""Shared SQLite persistence foundation.

One home for the connection + schema-migration pattern every store repeats.
Before this, five stores independently reimplemented `sqlite3.connect(...)` +
directory creation + schema setup, and only the experience store had a real
versioned-migration mechanism. This module gives every store the same proven
`PRAGMA user_version` migration path, so the journal (system of record), the
paper account, working orders, and future Replay/Analytics databases can all
evolve their schema safely and identically.

Behavior-preserving adoption: a store makes migration 1 its EXACT current
schema (idempotent `CREATE TABLE IF NOT EXISTS ...`). An existing on-disk
database sits at `user_version = 0`, so opening it runs that idempotent
migration (a no-op against already-present tables) and lands at exactly the
schema it already had — no data touched, no columns changed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Sequence

# A migration advances the schema by one version. migrations[0] initializes a
# fresh database to v1; migrations[i] takes v(i) -> v(i+1).
Migration = Callable[[sqlite3.Connection], None]


def connect(db_path: str | Path, *, wal: bool = True) -> sqlite3.Connection:
    """Open a connection with the app's standard settings.

    - `check_same_thread=False`: stores are read from worker/threadpool threads
      in the UI server; access is serialized by the caller's own lock (sqlite is
      fine with cross-thread use as long as calls don't overlap).
    - parent directory is created for a real path (skipped for ':memory:').
    - WAL journaling when `wal=True` and the database is on disk. Callers that
      must preserve legacy rollback-journal behavior pass `wal=False`.
    """
    is_memory = str(db_path) == ":memory:"
    if not is_memory:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    if wal and not is_memory:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def run_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration],
    *,
    label: str = "database",
    on_migrate: Callable[[int], None] | None = None,
) -> int:
    """Apply every pending migration in order, advancing `PRAGMA user_version`
    one step at a time, and commit. Returns the resulting version.

    Refuses to open a database whose version is NEWER than this build supports
    (a forward-incompatible downgrade) rather than corrupting it — the same
    guard the experience store shipped with.
    """
    target = len(migrations)
    current = schema_version(conn)
    if current > target:
        raise RuntimeError(
            f"{label} schema v{current} is newer than this build supports "
            f"(v{target}); upgrade OptionsPilot"
        )
    for version in range(current, target):
        migrations[version](conn)
        conn.execute(f"PRAGMA user_version = {version + 1}")
        if on_migrate is not None:
            on_migrate(version + 1)
    conn.commit()
    return target
