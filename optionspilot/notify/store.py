"""Small durable notification inbox.

The store is intentionally independent of the delivery sinks.  A toast, email,
tray badge, or future push client may fail without losing the event.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from optionspilot.notify.base import NotificationEvent


class NotificationStore:
    def __init__(self, path: str | Path, limit: int = 500):
        self.path = Path(path)
        self.limit = max(50, int(limit))
        self._lock = threading.RLock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    dedupe_key TEXT UNIQUE,
                    action_json TEXT,
                    dismissed INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.commit()

    def _connect(self):
        if self._closed:
            raise RuntimeError("notification store is closed")
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _session(self):
        """Yield one short-lived SQLite connection and always release its handle.

        ``sqlite3.Connection`` commits/rolls back when used as a context
        manager but deliberately does *not* close.  The previous implementation
        therefore leaked handles until garbage collection, which kept the inbox
        locked during shutdown on Windows.  A store owns no long-lived database
        connection: each operation owns and closes its own connection.
        """
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self) -> None:
        """Mark the durable inbox closed; safe to invoke during repeated exit."""
        with self._lock:
            self._closed = True

    def append(self, event: NotificationEvent) -> bool:
        event_id = event.event_id or uuid.uuid4().hex
        with self._lock, self._session() as db:
            try:
                db.execute(
                    "INSERT INTO notifications(event_id,kind,title,body,ts,dedupe_key,action_json) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (event_id, event.kind, event.title, event.body,
                     event.ts.isoformat(), event.dedupe_key,
                     json.dumps(event.action) if event.action else None),
                )
            except sqlite3.IntegrityError:
                return False
            db.execute("""
                DELETE FROM notifications WHERE event_id IN (
                    SELECT event_id FROM notifications
                    ORDER BY ts DESC LIMIT -1 OFFSET ?
                )
            """, (self.limit,))
        return True

    def recent(self, limit: int = 15) -> list[NotificationEvent]:
        limit = max(0, min(int(limit), self.limit))
        if not limit:
            return []
        with self._lock, self._session() as db:
            rows = db.execute(
                "SELECT * FROM notifications WHERE dismissed=0 ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._event(row) for row in rows]

    def dismiss(self, event_id: str) -> bool:
        with self._lock, self._session() as db:
            cur = db.execute("UPDATE notifications SET dismissed=1 WHERE event_id=?",
                             (event_id,))
            return cur.rowcount == 1

    @staticmethod
    def _event(row) -> NotificationEvent:
        try:
            ts = datetime.fromisoformat(row["ts"])
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        return NotificationEvent(
            row["kind"], row["title"], row["body"], ts=ts,
            event_id=row["event_id"], dedupe_key=row["dedupe_key"],
            action=json.loads(row["action_json"]) if row["action_json"] else None,
        )
