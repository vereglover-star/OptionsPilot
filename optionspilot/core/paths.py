"""AppPaths — the single source of truth for every filesystem location.

Before this, persistent paths were scattered and CWD-relative (`Path("data")`,
`data_dir="data"`, logs under `./logs`), so `data/` and `logs/` were written next
to the executable — and replacing the exe orphaned the user's data. AppPaths
moves the storage **root** to a stable per-user location outside the install
directory, so the binary can be replaced without touching any user data:

    Windows   %LOCALAPPDATA%\\OptionsPilot
    macOS     ~/Library/Application Support/OptionsPilot
    Linux     $XDG_DATA_HOME/OptionsPilot  (default ~/.local/share/OptionsPilot)

Override with the `OPTIONSPILOT_HOME` environment variable (used by the test
suite so nothing ever touches a developer's real AppData).

Every module receives the concrete paths it needs from here; no module decides
the storage root on its own. Directory layout under the root:

    OptionsPilot/
        data/            paper.db, journal.db, orders.db, experience.db,
                         cache.db, settings.json, coach/, state/, learning/,
                         reports/            (the canonical user-data subtree)
        logs/            rotating per-subsystem logs
        backups/         timestamped snapshots taken before future migrations
        exports/         user-initiated exports (CSV, …) — future
        migrations/      migration_version.json (schema marker + history)

`coach/`, `journal.db` and `cache.db` live inside `data/` (where they have
always lived) rather than as separate top-level trees: this keeps the one-time
migration a lossless copy of the `data/` and `logs/` trees, with no risk of
losing a file to a mis-mapped relocation. The accessor methods below
(`get_coach_dir`, `get_journal_db`, `get_cache_db`, …) remain the single API
callers use, so the physical layout can still evolve behind them later.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "OptionsPilot"
ENV_HOME = "OPTIONSPILOT_HOME"


def default_root() -> Path:
    """The platform-appropriate storage root (env override wins)."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".local" / "share")
    return base / APP_NAME


class AppPaths:
    """Resolves and (on request) creates every storage location under one root.

    Construct with an explicit `root` for tests/embedding; otherwise the
    platform default (honoring `OPTIONSPILOT_HOME`) is used. Pure path algebra —
    constructing an AppPaths touches no disk; call `ensure()` to create dirs.
    """

    def __init__(self, root: str | Path | None = None):
        self.root = (Path(root).expanduser() if root is not None else default_root())

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"AppPaths(root={self.root!r})"

    # ── top-level directories ────────────────────────────────────────────────
    def get_data_dir(self) -> Path:
        return self.root / "data"

    def get_logs_dir(self) -> Path:
        return self.root / "logs"

    def get_backups_dir(self) -> Path:
        return self.root / "backups"

    def get_exports_dir(self) -> Path:
        return self.root / "exports"

    def get_migrations_dir(self) -> Path:
        return self.root / "migrations"

    # ── data-subtree directories ─────────────────────────────────────────────
    def get_coach_dir(self) -> Path:
        return self.get_data_dir() / "coach"

    def get_state_dir(self) -> Path:
        return self.get_data_dir() / "state"

    def get_learning_dir(self) -> Path:
        return self.get_data_dir() / "learning"

    def get_reports_dir(self) -> Path:
        return self.get_data_dir() / "reports"

    def get_journal_dir(self) -> Path:
        """Directory holding the trade-history database (the data subtree)."""
        return self.get_data_dir()

    def get_cache_dir(self) -> Path:
        """Directory holding the (regenerable) candle cache."""
        return self.get_data_dir()

    # ── database files ───────────────────────────────────────────────────────
    def get_paper_db(self) -> Path:
        return self.get_data_dir() / "paper.db"

    def get_journal_db(self) -> Path:
        return self.get_data_dir() / "journal.db"

    def get_trade_history_file(self) -> Path:
        """Alias for the journal database — the user's trade history."""
        return self.get_journal_db()

    def get_orders_db(self) -> Path:
        return self.get_data_dir() / "orders.db"

    def get_experience_db(self) -> Path:
        return self.get_data_dir() / "experience.db"

    def get_cache_db(self) -> Path:
        return self.get_data_dir() / "cache.db"

    def get_notifications_db(self) -> Path:
        return self.get_data_dir() / "notifications.db"

    def get_backtest_journal_db(self, symbol: str) -> Path:
        return self.get_data_dir() / f"backtest_{symbol.lower()}.db"

    # ── json / settings files ────────────────────────────────────────────────
    def get_settings_file(self) -> Path:
        return self.get_data_dir() / "settings.json"

    def get_weights_file(self) -> Path:
        return self.get_learning_dir() / "weights.json"

    def get_open_trades_file(self) -> Path:
        return self.get_state_dir() / "open_trades.json"

    def get_manual_trades_file(self) -> Path:
        return self.get_state_dir() / "manual_trades.json"

    def get_symbol_meta_file(self) -> Path:
        return self.get_state_dir() / "symbol_meta.json"

    def get_migration_marker(self) -> Path:
        return self.get_migrations_dir() / "migration_version.json"

    # ── creation ─────────────────────────────────────────────────────────────
    def top_level_dirs(self) -> list[Path]:
        return [self.get_data_dir(), self.get_logs_dir(), self.get_backups_dir(),
                self.get_exports_dir(), self.get_migrations_dir()]

    def all_dirs(self) -> list[Path]:
        """Every directory the app expects to exist (top level + data subtree)."""
        return self.top_level_dirs() + [
            self.get_coach_dir(), self.get_state_dir(),
            self.get_learning_dir(), self.get_reports_dir(),
        ]

    def ensure(self) -> "AppPaths":
        """Create every expected directory (idempotent). Returns self."""
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
        return self
