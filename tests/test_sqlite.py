"""Tests for the shared SQLite persistence foundation (core/sqlite.py) and the
behavior-preserving adoption of it by the existing stores."""

import sqlite3

import pytest

from optionspilot.core.sqlite import connect, run_migrations, schema_version


class TestConnect:
    def test_creates_parent_directory(self, tmp_path):
        db = tmp_path / "nested" / "deep" / "x.db"
        conn = connect(db, wal=False)
        assert db.parent.is_dir()
        conn.close()

    def test_cross_thread_allowed(self, tmp_path):
        # check_same_thread=False → usable from another thread without error.
        import threading
        conn = connect(tmp_path / "x.db", wal=False)
        conn.execute("CREATE TABLE t (a)")
        conn.commit()
        errors = []

        def use():
            try:
                conn.execute("INSERT INTO t VALUES (1)")
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        th = threading.Thread(target=use)
        th.start(); th.join()
        assert not errors

    def test_wal_enabled_and_disabled(self, tmp_path):
        wal = connect(tmp_path / "wal.db", wal=True)
        assert wal.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        wal.close()
        plain = connect(tmp_path / "plain.db", wal=False)
        assert plain.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        plain.close()

    def test_memory_db_is_fine(self):
        conn = connect(":memory:", wal=True)   # WAL skipped for memory, no error
        conn.execute("CREATE TABLE t (a)")
        conn.close()


def _mk(sql):
    return lambda conn: conn.executescript(sql)


class TestRunMigrations:
    def test_fresh_runs_all(self, tmp_path):
        conn = connect(tmp_path / "x.db", wal=False)
        v = run_migrations(conn, [_mk("CREATE TABLE a (x)"),
                                  _mk("CREATE TABLE b (y)")])
        assert v == 2
        assert schema_version(conn) == 2
        # both tables exist
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"a", "b"} <= names

    def test_idempotent_on_reopen(self, tmp_path):
        path = tmp_path / "x.db"
        migrations = [_mk("CREATE TABLE a (x)")]
        c1 = connect(path, wal=False)
        run_migrations(c1, migrations)
        c1.execute("INSERT INTO a VALUES (1)"); c1.commit(); c1.close()
        # reopen: migration must not re-run or lose data
        c2 = connect(path, wal=False)
        run_migrations(c2, migrations)
        assert schema_version(c2) == 1
        assert c2.execute("SELECT COUNT(*) FROM a").fetchone()[0] == 1

    def test_incremental_upgrade(self, tmp_path):
        path = tmp_path / "x.db"
        c1 = connect(path, wal=False)
        run_migrations(c1, [_mk("CREATE TABLE a (x)")])
        c1.close()
        # a later build adds migration 2
        c2 = connect(path, wal=False)
        run_migrations(c2, [_mk("CREATE TABLE a (x)"),
                            _mk("CREATE TABLE b (y)")])
        assert schema_version(c2) == 2
        assert c2.execute("SELECT name FROM sqlite_master WHERE name='b'").fetchone()

    def test_refuses_newer_schema(self, tmp_path):
        path = tmp_path / "x.db"
        c1 = connect(path, wal=False)
        c1.execute("PRAGMA user_version = 5"); c1.commit(); c1.close()
        c2 = connect(path, wal=False)
        with pytest.raises(RuntimeError, match="newer than this build"):
            run_migrations(c2, [_mk("CREATE TABLE a (x)")], label="x.db")


class TestBehaviorPreservingAdoption:
    def test_legacy_db_without_user_version_adopts_cleanly(self, tmp_path):
        """A store created before user_version tracking (schema present,
        user_version 0) must open unchanged when the foundation runs migration
        1 == its current schema."""
        path = tmp_path / "legacy.db"
        raw = sqlite3.connect(str(path))
        raw.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        raw.execute("INSERT INTO t VALUES (1, 'keep')")
        raw.commit(); raw.close()
        assert sqlite3.connect(str(path)).execute("PRAGMA user_version").fetchone()[0] == 0

        conn = connect(path, wal=False)
        run_migrations(conn, [_mk("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")])
        assert schema_version(conn) == 1
        assert conn.execute("SELECT v FROM t WHERE id=1").fetchone()[0] == "keep"

    def test_idempotent_alter_migration_tolerates_existing_column(self, tmp_path):
        """The paper.db hazard: a legacy db already has the v2 column but sits at
        user_version 0. The idempotent ALTER migration must swallow the
        duplicate-column error and still reach v2."""
        path = tmp_path / "legacy_paper.db"
        raw = sqlite3.connect(str(path))
        raw.executescript(
            "CREATE TABLE positions (symbol TEXT PRIMARY KEY, "
            "managed_by TEXT NOT NULL DEFAULT 'ai')")  # column ALREADY present
        raw.commit(); raw.close()

        def _add_managed_by(conn):
            try:
                conn.execute("ALTER TABLE positions ADD COLUMN managed_by TEXT "
                             "NOT NULL DEFAULT 'ai'")
            except sqlite3.OperationalError:
                pass

        conn = connect(path, wal=False)
        run_migrations(conn, [
            _mk("CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY)"),
            _add_managed_by,
        ])
        assert schema_version(conn) == 2
        cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
        assert "managed_by" in cols
