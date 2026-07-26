# STORAGE.md — persistent storage architecture (V0.4.4)

How OptionsPilot separates application binaries from user data so the
executable can be replaced without ever losing paper-trading history, coach
reviews, journal entries, settings, watchlists, learned weights, or logs.

## Why

Before V0.4.4 every persistent path was **current-working-directory relative**
(`Path("data")`, `data_dir="data"`, logs under `./logs`), so `data/` and `logs/`
were written next to the executable. Installing a new version — replacing the
exe or extracting a new folder — orphaned the old data. This milestone moves the
storage **root** out of the install directory into a stable per-user location and
migrates any pre-existing install into it once, automatically.

## The single source of truth: `AppPaths`

`optionspilot/core/paths.py::AppPaths` resolves and (on request) creates every
storage location. **No module constructs the storage root on its own** — they
receive concrete paths from `AppPaths` (the CLI bootstrap builds one and passes
`data_dir=paths.get_data_dir()` down; `Orchestrator`/`UIServer` default to
`AppPaths().get_data_dir()` when no path is supplied).

Root resolution (first match wins):

| Condition | Root |
|---|---|
| `OPTIONSPILOT_HOME` env var set | `$OPTIONSPILOT_HOME` |
| Windows | `%LOCALAPPDATA%\OptionsPilot` |
| macOS | `~/Library/Application Support/OptionsPilot` |
| Linux / other | `$XDG_DATA_HOME/OptionsPilot` (default `~/.local/share/OptionsPilot`) |

`OPTIONSPILOT_HOME` is what the test suite sets (a throwaway temp dir, via an
autouse fixture in `tests/conftest.py`) so tests never read or write a
developer's real AppData.

### Path API (selection)

`get_data_dir` · `get_logs_dir` · `get_backups_dir` · `get_exports_dir` ·
`get_migrations_dir` · `get_coach_dir` · `get_state_dir` · `get_journal_db` ·
`get_paper_db` · `get_orders_db` · `get_experience_db` · `get_cache_db` ·
`get_settings_file` · `get_weights_file` · `get_open_trades_file` ·
`get_manual_trades_file` · `get_symbol_meta_file` · `get_trade_history_file`
(alias of `get_journal_db`) · `get_backtest_journal_db(symbol)` ·
`get_migration_marker`. `ensure()` creates every expected directory (idempotent);
constructing an `AppPaths` touches no disk.

## Directory layout

```
%LOCALAPPDATA%\OptionsPilot\
    data\                     the canonical user-data subtree
        paper.db              paper account / positions / fills
        journal.db            trade history (system of record)
        orders.db             manual working + historical orders
        experience.db         AI memory (Experience Engine)
        cache.db              candle cache (regenerable; safe to delete)
        settings.json         runtime settings (watchlist, modes)
        coach\<id>.json       one AI Coach review per trade
        state\                open_trades.json, manual_trades.json, symbol_meta.json
        learning\weights.json versioned evidence weights
        reports\              backtest JSON/HTML output
    logs\                     rotating per-subsystem logs
    backups\                  timestamped snapshots taken before a migration
    exports\                  user-initiated exports (future)
    migrations\               migration_version.json (schema marker + history)
```

`coach/`, `journal.db`, and `cache.db` live **inside `data/`** (where they have
always lived) rather than as separate top-level trees. This is a deliberate
choice: it makes the one-time legacy import a **lossless copy of the `data/` and
`logs/` trees**, with no risk of losing a file to a mis-mapped relocation. The
`AppPaths` accessors remain the single API callers use, so the physical layout
can still evolve behind them later without touching call sites.

## Migration process (`core/migration.py::initialize_storage`)

Called once at startup (from `__main__._bootstrap`). Steps:

1. **`paths.ensure()`** — create the directory layout (idempotent).
2. **Read the marker** (`migrations/migration_version.json`). A missing *or
   corrupted* marker is treated as "not yet initialized" — safe, because the
   copy in step 3 never clobbers newer data.
3. **First run only — import a legacy install.** `find_legacy_install` looks for
   a non-empty `data/`/`logs/` in the current working directory and (when
   frozen) the executable's folder, excluding the new root. If found, every file
   under legacy `data/`→`data/` and `logs/`→`logs/` is copied with:
   - **`shutil.copy2`** — content **and** modification timestamp preserved;
   - **skip-if-newer** — a destination that is newer than or identical to the
     source is kept (never overwrite newer data);
   - **verification** — each copy is checked by size; failures are reported;
   - **non-destructive** — the source is never modified or deleted.
   This makes the import **idempotent and self-healing**: a partial copy
   (crash before the marker was written) is completed on the next launch, and a
   corrupted marker cannot cause data loss.
4. **Run versioned migrations** (see below) — none registered today.
5. **Write the marker** recording the schema version, the legacy-import summary,
   and a migration history list.

On every subsequent launch the marker is valid and current, so
`initialize_storage` is a no-op (`already_initialized: true`).

## Backup strategy (`create_backup`)

`create_backup(paths, label)` writes a timestamped snapshot of the `data/`
subtree to `backups/<YYYYMMDD-HHMMSS>_<label>/data/` (again `copy2`,
content+timestamps), returning the backup directory (or `None` if there is
nothing to back up). It runs **automatically before any versioned migration** is
applied, so a future data migration can never leave the user worse off than a
one-directory-copy rollback. Backups are never pruned automatically (groundwork
for the future updater to manage).

## Future migration framework (`MIGRATIONS`)

`core/migration.py` defines `Migration(version, description, apply)` and an
**empty** `MIGRATIONS` list. A future release registers a data migration:

```python
def _v2(paths: AppPaths) -> None:
    ...  # transform on-disk data
MIGRATIONS = [Migration(2, "describe the change", _v2)]
```

`_run_versioned` applies every registered migration whose `version` exceeds the
marker's `schema_version`, **taking an automatic backup first**, then records it
in the marker's `history`. It is intentionally a no-op today — this milestone
builds the mechanism, not any future migration. A database whose recorded schema
is newer than the running build is left untouched (forward-incompatible
downgrades are refused rather than corrupting data), mirroring the per-store
`core/sqlite.py` guard.

## Testing

`tests/test_paths.py` (path algebra + platform roots) and
`tests/test_migration.py` (fresh install, upgrade import, timestamp
preservation, idempotency, many launches, partial migration, never-overwrite-
newer, corrupted marker, existing-AppData skip, backups, the versioned
framework, and store read/write) cover the guarantees above. The autouse
isolation fixture in `tests/conftest.py` ensures no test touches real AppData.
`python -m optionspilot selftest` verifies at runtime that every directory
exists and is writable and that the migration marker is valid.
