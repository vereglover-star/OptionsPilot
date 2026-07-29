"""Durable, transport-neutral idempotency records for application mutations."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


class IdempotencyStore:
    """Stores the result of a completed mutation by operation/key.

    The store deliberately knows no HTTP concepts.  Transports supply a stable
    operation name and a key; the application result remains JSON data that can
    be replayed consistently after a desktop restart or by another client.
    """

    def __init__(self, path: str | Path, limit: int = 1000):
        self._path = Path(path)
        self._limit = max(100, int(limit))
        self._lock = threading.RLock()
        self._closed = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS idempotency_records (
                operation TEXT NOT NULL, key TEXT NOT NULL, result_json TEXT NOT NULL,
                PRIMARY KEY(operation, key))""")

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

    def execute(self, operation: str, key: str | None,
                callback: Callable[[], Any]) -> tuple[Any, bool]:
        if not key:
            return callback(), False
        with self._lock, self._session() as db:
            row = db.execute(
                "SELECT result_json FROM idempotency_records WHERE operation=? AND key=?",
                (operation, key)).fetchone()
            if row is not None:
                return json.loads(row[0]), True
            result = callback()
            encoded = json.dumps(result, default=str, separators=(",", ":"))
            db.execute("INSERT INTO idempotency_records(operation,key,result_json) VALUES(?,?,?)",
                       (operation, key, encoded))
            db.execute("""DELETE FROM idempotency_records WHERE rowid IN (
                SELECT rowid FROM idempotency_records ORDER BY rowid DESC
                LIMIT -1 OFFSET ?)""", (self._limit,))
            return result, False

    def close(self) -> None:
        with self._lock:
            self._closed = True
