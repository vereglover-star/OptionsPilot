"""Tests for the AppPaths storage layer (single source of truth for paths)."""

import sys

import pytest

from optionspilot.core.paths import APP_NAME, ENV_HOME, AppPaths, default_root


class TestDefaultRoot:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_HOME, str(tmp_path / "custom"))
        assert default_root() == tmp_path / "custom"

    def test_windows_uses_localappdata(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_HOME, raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
        assert default_root() == tmp_path / "AppData" / "Local" / APP_NAME

    def test_linux_uses_xdg(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_HOME, raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        assert default_root() == tmp_path / "share" / APP_NAME

    def test_root_is_named_optionspilot(self, monkeypatch):
        monkeypatch.delenv(ENV_HOME, raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert default_root().name == APP_NAME


class TestLayout:
    def test_all_paths_under_root(self, tmp_path):
        p = AppPaths(tmp_path)
        assert p.root == tmp_path
        for path in [p.get_data_dir(), p.get_logs_dir(), p.get_backups_dir(),
                     p.get_exports_dir(), p.get_migrations_dir(),
                     p.get_coach_dir(), p.get_state_dir(),
                     p.get_journal_db(), p.get_paper_db(), p.get_orders_db(),
                     p.get_experience_db(), p.get_cache_db(),
                     p.get_settings_file(), p.get_weights_file(),
                     p.get_migration_marker()]:
            assert tmp_path in path.parents or path == tmp_path

    def test_known_locations(self, tmp_path):
        p = AppPaths(tmp_path)
        assert p.get_data_dir() == tmp_path / "data"
        assert p.get_logs_dir() == tmp_path / "logs"
        assert p.get_journal_db() == tmp_path / "data" / "journal.db"
        assert p.get_coach_dir() == tmp_path / "data" / "coach"
        assert p.get_settings_file() == tmp_path / "data" / "settings.json"
        assert p.get_weights_file() == tmp_path / "data" / "learning" / "weights.json"
        assert p.get_manual_trades_file() == tmp_path / "data" / "state" / "manual_trades.json"
        assert p.get_migration_marker() == tmp_path / "migrations" / "migration_version.json"

    def test_trade_history_is_journal(self, tmp_path):
        p = AppPaths(tmp_path)
        assert p.get_trade_history_file() == p.get_journal_db()

    def test_backtest_journal_lowercases_symbol(self, tmp_path):
        assert AppPaths(tmp_path).get_backtest_journal_db("SPY").name == "backtest_spy.db"

    def test_constructing_touches_no_disk(self, tmp_path):
        AppPaths(tmp_path / "never")   # no ensure()
        assert not (tmp_path / "never").exists()


class TestEnsure:
    def test_creates_all_dirs(self, tmp_path):
        p = AppPaths(tmp_path).ensure()
        for d in p.all_dirs():
            assert d.is_dir()

    def test_idempotent(self, tmp_path):
        p = AppPaths(tmp_path)
        p.ensure()
        (p.get_data_dir() / "keep.txt").write_text("x")
        p.ensure()   # again — must not wipe anything
        assert (p.get_data_dir() / "keep.txt").read_text() == "x"

    def test_expanduser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        p = AppPaths("~/opdata")
        assert "~" not in str(p.root)
