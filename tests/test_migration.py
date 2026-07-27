"""Tests for storage initialization + one-time legacy migration + backups +
the versioned-migration framework. The overriding guarantee under test: a user
never loses data when the executable is replaced."""

import json
import os

import pytest

from optionspilot.core.migration import (
    BASELINE_VERSION, Migration, MIGRATIONS, create_backup, find_legacy_install,
    initialize_storage, _load_marker,
)
from optionspilot.core.paths import AppPaths


@pytest.fixture(autouse=True)
def _clean_cwd(tmp_path, monkeypatch):
    """Run every migration test from an empty CWD so legacy detection never
    picks up the repository's own ./data. Legacy tests opt in by chdir-ing to
    the dir they seeded."""
    d = tmp_path / "cwd"
    d.mkdir()
    monkeypatch.chdir(d)


def seed_legacy(base, *, data=None, logs=None):
    """Create a legacy CWD-style install at `base` with data/ and logs/."""
    data = data or {"journal.db": "journal-v1", "settings.json": '{"watchlist":["SPY"]}',
                    "coach/t1.json": '{"score":80}', "state/manual_trades.json": "{}"}
    logs = logs or {"app.log": "log line 1\n"}
    for rel, content in data.items():
        f = base / "data" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    for rel, content in logs.items():
        f = base / "logs" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return base


class TestFreshInstall:
    def test_creates_layout_and_marker(self, tmp_path, monkeypatch):
        (tmp_path / "emptycwd").mkdir()
        monkeypatch.chdir(tmp_path / "emptycwd")  # no legacy here
        paths = AppPaths(tmp_path / "root")
        report = initialize_storage(paths)
        assert report["fresh_install"] is True
        assert report["legacy_import"] is None
        for d in paths.all_dirs():
            assert d.is_dir()
        marker = _load_marker(paths)
        assert marker["schema_version"] == BASELINE_VERSION

    def test_no_legacy_when_cwd_empty(self, tmp_path):
        # the autouse _clean_cwd fixture already placed us in an empty CWD
        assert find_legacy_install(tmp_path / "root") is None


class TestUpgradeMigration:
    def test_copies_all_user_data(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        report = initialize_storage(paths)

        assert report["fresh_install"] is False
        assert report["legacy_import"]["copied"] >= 5
        assert report["legacy_import"]["errors"] == []
        # every legacy file now present in the new root with identical content
        assert paths.get_journal_db().read_text() == "journal-v1"
        assert (paths.get_coach_dir() / "t1.json").read_text() == '{"score":80}'
        assert paths.get_manual_trades_file().read_text() == "{}"
        assert (paths.get_logs_dir() / "app.log").read_text() == "log line 1\n"
        # marker records the import
        assert _load_marker(paths)["legacy_import"]["copied"] >= 5

    def test_preserves_timestamps(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        old = 1_600_000_000  # a fixed past time
        os.utime(legacy / "data" / "journal.db", (old, old))
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        initialize_storage(paths)
        assert int(paths.get_journal_db().stat().st_mtime) == old

    def test_never_deletes_originals(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        initialize_storage(AppPaths(tmp_path / "root"))
        assert (legacy / "data" / "journal.db").exists()  # source untouched
        assert (legacy / "logs" / "app.log").exists()


class TestIdempotency:
    def test_second_launch_does_nothing(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        initialize_storage(paths)
        # user edits their migrated data
        paths.get_journal_db().write_text("journal-EDITED")
        report2 = initialize_storage(paths)
        assert report2["already_initialized"] is True
        assert report2["legacy_import"] is None
        # the edit is preserved — migration did NOT re-copy the legacy file
        assert paths.get_journal_db().read_text() == "journal-EDITED"

    def test_many_launches_stable(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        for _ in range(4):
            initialize_storage(paths)
        assert paths.get_journal_db().read_text() == "journal-v1"


class TestPartialAndCorrupt:
    def test_partial_migration_completes(self, tmp_path, monkeypatch):
        # A prior run copied journal.db but crashed before writing the marker.
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        paths.ensure()
        paths.get_journal_db().write_text("journal-v1")   # partially present
        assert _load_marker(paths) is None                # no marker yet
        initialize_storage(paths)
        # the rest of the legacy data got copied in
        assert (paths.get_coach_dir() / "t1.json").exists()
        assert paths.get_settings_file().exists()

    def test_never_overwrites_newer(self, tmp_path, monkeypatch):
        legacy = seed_legacy(tmp_path / "install")
        old = 1_600_000_000
        os.utime(legacy / "data" / "journal.db", (old, old))
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        paths.ensure()
        paths.get_journal_db().write_text("journal-NEWER")  # newer than legacy
        initialize_storage(paths)
        assert paths.get_journal_db().read_text() == "journal-NEWER"  # kept

    def test_corrupted_marker_does_not_lose_data(self, tmp_path, monkeypatch):
        # An older legacy install exists; the in-use new root has newer data and
        # a corrupted marker. The newer data must survive (skip-if-newer guard).
        legacy = seed_legacy(tmp_path / "install")
        old = 1_600_000_000
        os.utime(legacy / "data" / "journal.db", (old, old))
        monkeypatch.chdir(legacy)
        paths = AppPaths(tmp_path / "root")
        paths.ensure()
        paths.get_journal_db().write_text("precious")     # newer than legacy
        paths.get_migration_marker().write_text("}{ not json", encoding="utf-8")
        initialize_storage(paths)
        assert paths.get_journal_db().read_text() == "precious"
        assert isinstance(_load_marker(paths)["schema_version"], int)  # marker repaired


class TestExistingAppData:
    def test_valid_marker_skips_import(self, tmp_path, monkeypatch):
        paths = AppPaths(tmp_path / "root")
        initialize_storage(paths)                 # establishes marker
        paths.get_journal_db().write_text("in-use")
        legacy = seed_legacy(tmp_path / "install")
        monkeypatch.chdir(legacy)
        report = initialize_storage(paths)
        assert report["already_initialized"] is True
        assert paths.get_journal_db().read_text() == "in-use"


class TestBackups:
    def test_backup_copies_data_subtree(self, tmp_path):
        paths = AppPaths(tmp_path / "root").ensure()
        paths.get_journal_db().write_text("data-to-back-up")
        dest = create_backup(paths, label="test")
        assert dest is not None and dest.parent == paths.get_backups_dir()
        assert (dest / "data" / "journal.db").read_text() == "data-to-back-up"

    def test_backup_none_when_empty(self, tmp_path):
        paths = AppPaths(tmp_path / "root").ensure()
        assert create_backup(paths) is None


class TestVersionedFramework:
    def test_no_migrations_registered_by_default(self):
        assert MIGRATIONS == []   # framework only — no future migrations implemented

    def test_registered_migration_runs_once_with_backup(self, tmp_path, monkeypatch):
        applied_to = []
        mig = Migration(version=BASELINE_VERSION + 1, description="test bump",
                        apply=lambda paths: applied_to.append(paths.root))
        monkeypatch.setattr("optionspilot.core.migration.MIGRATIONS", [mig])
        paths = AppPaths(tmp_path / "root")
        paths.ensure()
        paths.get_journal_db().write_text("x")   # so a backup is created
        r1 = initialize_storage(paths)
        assert len(r1["migrations_applied"]) == 1
        assert applied_to == [paths.root]
        assert any(paths.get_backups_dir().iterdir())   # backed up before applying
        # second launch: already at that version, does not re-run
        r2 = initialize_storage(paths)
        assert r2["migrations_applied"] == []
        assert _load_marker(paths)["schema_version"] == BASELINE_VERSION + 1


class TestReadWrite:
    def test_stores_can_write_after_init(self, tmp_path):
        from optionspilot.journal import TradeJournal
        paths = AppPaths(tmp_path / "root")
        initialize_storage(paths)
        j = TradeJournal(paths.get_journal_db())   # opens under the new root
        assert j.all() == []
        j.close()
