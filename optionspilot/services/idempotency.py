"""Durable, transport-neutral idempotency records for application mutations.

V0.9.2-C9 fixed two defects here, both of which only appear under concurrency
or under a misbehaving client — which is why neither had a test.

**N-1: one lock for every key.** `execute` held a single store-wide lock across
the callback. Spanning the callback is correct — "execute once per key" is the
whole contract — but the lock was not per key, so unrelated mutations were
serialised: a workspace update waited behind an update check talking to GitHub.
Locks are now per `(operation, key)` and reference-counted, so the table does
not grow with client input.

**N-2: no request fingerprint.** An idempotency key replayed with a *different*
body returned the first request's result and reported success. That is the one
case the idempotency-key specification says must fail: the client has reused a
key for a different operation, and silently answering with someone else's result
is worse than refusing, because nothing anywhere records that it happened. A
mismatch now raises `Conflict`, which the transport maps to 409.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from optionspilot.services.errors import Conflict


def fingerprint(payload: Any) -> str:
    """A stable hash of a request body.

    `sort_keys` because a JSON object is unordered and two clients serialising
    the same request must not disagree about whether it *is* the same request.
    `default=str` because the payload comes from a client: an un-encodable
    value must not turn a mutation into a 500 on the way to being fingerprinted.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStore:
    """Stores the result of a completed mutation by operation/key.

    The store deliberately knows no HTTP concepts.  Transports supply a stable
    operation name and a key; the application result remains JSON data that can
    be replayed consistently after a desktop restart or by another client.
    """

    def __init__(self, path: str | Path, limit: int = 1000):
        self._path = Path(path)
        self._limit = max(100, int(limit))
        #: Guards the lock table only — never held across a callback.
        self._registry = threading.Lock()
        #: `(operation, key)` -> `[lock, waiters]`. Reference-counted so a
        #: store that has served a million keys holds no locks at rest.
        self._key_locks: dict[tuple[str, str], list] = {}
        self._closed = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS idempotency_records (
                operation TEXT NOT NULL, key TEXT NOT NULL, result_json TEXT NOT NULL,
                PRIMARY KEY(operation, key))""")
            self._add_fingerprint_column(db)

    @staticmethod
    def _add_fingerprint_column(db) -> None:
        """Bring an existing store up to the C9 schema.

        `ALTER TABLE ... ADD COLUMN` rather than a rebuild: the column is
        nullable and the rows already on disk are still valid replays. Guarded
        by `table_info` because SQLite has no `ADD COLUMN IF NOT EXISTS`.
        """
        columns = {row[1] for row in
                   db.execute("PRAGMA table_info(idempotency_records)")}
        if "fingerprint" not in columns:
            db.execute("ALTER TABLE idempotency_records "
                       "ADD COLUMN fingerprint TEXT")

    @contextmanager
    def _session(self):
        if self._closed:
            raise RuntimeError("idempotency store is closed")
        db = sqlite3.connect(self._path, timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _key_lock(self, operation: str, key: str):
        """One lock per key, dropped when nobody holds or awaits it.

        The count is incremented under `_registry` *before* the lock is
        acquired, so a concurrent caller for the same key cannot find the entry
        missing and create a second lock for it — which would let both run, and
        is the exact failure this method exists to prevent.
        """
        ident = (operation, key)
        with self._registry:
            entry = self._key_locks.get(ident)
            if entry is None:
                entry = self._key_locks[ident] = [threading.Lock(), 0]
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._registry:
                entry[1] -= 1
                if entry[1] <= 0:
                    self._key_locks.pop(ident, None)

    def live_key_locks(self) -> int:
        """How many per-key locks are held or awaited right now.

        Test-facing: it is the only way to observe that the lock table is not a
        memory leak keyed by client input.
        """
        with self._registry:
            return len(self._key_locks)

    def execute(self, operation: str, key: str | None,
                callback: Callable[[], Any], *,
                fingerprint: str | None = None) -> tuple[Any, bool]:
        if not key:
            return callback(), False
        # The lock spans the callback because "execute once per key" is the
        # whole contract. The SQLite connection deliberately does NOT: a
        # mutation can be an update check that talks to GitHub, and holding an
        # open write transaction across a network round trip blocks every other
        # connection to this file until its ten-second busy timeout expires.
        with self._key_lock(operation, key):
            with self._session() as db:
                row = db.execute(
                    "SELECT result_json, fingerprint FROM idempotency_records "
                    "WHERE operation=? AND key=?", (operation, key)).fetchone()
            if row is not None:
                stored = row[1]
                # Both sides must be known before a mismatch can be claimed. A
                # NULL stored fingerprint is a row written before C9, and
                # refusing those would turn an upgrade into a wall of 409s for
                # keys a client legitimately still holds.
                if (fingerprint is not None and stored is not None
                        and stored != fingerprint):
                    raise Conflict(
                        "this idempotency key was already used for a "
                        "different request",
                        details={"operation": operation})
                return json.loads(row[0]), True
            result = callback()
            encoded = json.dumps(result, default=str, separators=(",", ":"))
            with self._session() as db:
                db.execute("INSERT OR REPLACE INTO idempotency_records"
                           "(operation,key,result_json,fingerprint) "
                           "VALUES(?,?,?,?)",
                           (operation, key, encoded, fingerprint))
                db.execute("""DELETE FROM idempotency_records WHERE rowid IN (
                    SELECT rowid FROM idempotency_records ORDER BY rowid DESC
                    LIMIT -1 OFFSET ?)""", (self._limit,))
            return result, False

    def close(self) -> None:
        with self._registry:
            self._closed = True
