"""Storage initialization, one-time legacy import, and the versioned-migration
framework.

`initialize_storage(paths)` is called once at startup. It:

1. creates the directory layout (idempotent);
2. on first run, imports a legacy CWD/exe-relative `data/` + `logs/` install
   into the new root — a lossless copy that preserves timestamps, never
   overwrites a newer file, verifies every file, and never deletes the source;
3. runs any registered versioned migrations (backing up `data/` first);
4. records completion in `migrations/migration_version.json`.

Everything is idempotent and self-healing: re-running after a partial copy
completes it (copy is skip-if-newer), a corrupted marker cannot cause data loss
(the copy never clobbers newer files, and absent a legacy source nothing is
copied), and once the marker records the current schema version, subsequent
launches do nothing.

The versioned framework (`MIGRATIONS`) is intentionally **empty** — this commit
builds the mechanism, not any future data migration. A future version appends a
`Migration(version=2, …)` and it will run once, after an automatic backup.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.paths import AppPaths

log = get_logger("storage")

# Baseline once the storage layout is initialized. Future *data* migrations
# register below with version >= 2 and bump the marker's schema_version.
BASELINE_VERSION = 1


class Migration(NamedTuple):
    version: int
    description: str
    apply: Callable[[AppPaths], None]


# Future migrations register here, ordered by version (001 is the baseline init,
# so registered data migrations start at version 2). DO NOT implement future
# migrations now — this is the framework only.
MIGRATIONS: list[Migration] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── low-level lossless copy ─────────────────────────────────────────────────

def _copy_tree(src_dir: Path, dst_dir: Path) -> dict:
    """Copy every file from src_dir into dst_dir, preserving timestamps
    (`copy2`), never overwriting a destination that is newer or identical, and
    verifying each copy by size. Never deletes anything in src_dir.

    Returns {"copied", "skipped", "errors": [...]}.
    """
    copied = skipped = 0
    errors: list[str] = []
    if not src_dir.is_dir():
        return {"copied": 0, "skipped": 0, "errors": []}
    for src in sorted(src_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        try:
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                skipped += 1                     # keep the newer/identical copy
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)               # copies content + mtime
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                errors.append(f"verify failed: {rel}")
            else:
                copied += 1
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
    return {"copied": copied, "skipped": skipped, "errors": errors}


# ── legacy detection + import ───────────────────────────────────────────────

def _legacy_candidates(root: Path) -> list[Path]:
    """Where a pre-relocation install might have written its `data/`/`logs/`:
    the current working directory, and (when frozen) the executable's folder.
    The new root itself is always excluded."""
    cands = [Path.cwd()]
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent)
    out: list[Path] = []
    seen: set[Path] = set()
    root_resolved = root.resolve()
    for c in cands:
        rc = c.resolve()
        if rc == root_resolved or rc in seen:
            continue
        seen.add(rc)
        out.append(c)
    return out


def find_legacy_install(root: Path) -> Path | None:
    """First candidate directory that holds a non-empty legacy `data/` or
    `logs/` and is not inside the new root."""
    root_resolved = root.resolve()
    for c in _legacy_candidates(root):
        data, logs = c / "data", c / "logs"
        has_data = data.is_dir() and any(data.iterdir())
        has_logs = logs.is_dir() and any(logs.iterdir())
        if not (has_data or has_logs):
            continue
        # never treat the new root's own subtree as "legacy"
        if root_resolved == data.resolve() or root_resolved in data.resolve().parents:
            continue
        return c
    return None


def _import_legacy(legacy: Path, paths: AppPaths) -> dict:
    d = _copy_tree(legacy / "data", paths.get_data_dir())
    lg = _copy_tree(legacy / "logs", paths.get_logs_dir())
    return {
        "source": str(legacy),
        "copied": d["copied"] + lg["copied"],
        "skipped": d["skipped"] + lg["skipped"],
        "errors": d["errors"] + lg["errors"],
        "at": _now(),
    }


# ── backups ─────────────────────────────────────────────────────────────────

def create_backup(paths: AppPaths, label: str = "backup") -> Path | None:
    """Timestamped snapshot of the `data/` subtree into `backups/`. Returns the
    backup directory, or None if there is nothing to back up. Groundwork for
    future migrations (run automatically before any versioned migration)."""
    data = paths.get_data_dir()
    if not data.is_dir() or not any(f.is_file() for f in data.rglob("*")):
        return None   # only empty scaffold dirs — nothing worth backing up
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
    dest = paths.get_backups_dir() / f"{stamp}_{safe}"
    dest.mkdir(parents=True, exist_ok=True)
    result = _copy_tree(data, dest / "data")
    log.info("backup created at %s (%d files)", dest, result["copied"])
    return dest


# ── marker I/O ──────────────────────────────────────────────────────────────

def _load_marker(paths: AppPaths) -> dict | None:
    """The migration marker, or None if absent or corrupted. A corrupted marker
    is treated as absent — safe, because the import/copy never clobbers existing
    newer data."""
    path = paths.get_migration_marker()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("migration marker unreadable (%s) — treating as uninitialized", exc)
        return None


def _save_marker(paths: AppPaths, marker: dict) -> None:
    path = paths.get_migration_marker()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    tmp.replace(path)     # atomic on the same filesystem


# ── versioned migrations ────────────────────────────────────────────────────

def _run_versioned(paths: AppPaths, marker: dict) -> list[dict]:
    """Apply every registered migration newer than the marker's schema version,
    backing up first. Returns the applied records. No-op today (MIGRATIONS empty)."""
    current = marker.get("schema_version", 0)
    applied: list[dict] = []
    for m in sorted(MIGRATIONS, key=lambda x: x.version):
        if m.version <= current:
            continue
        create_backup(paths, f"pre-v{m.version}")
        log.info("applying storage migration v%d: %s", m.version, m.description)
        m.apply(paths)
        current = m.version
        record = {"version": m.version, "description": m.description, "at": _now()}
        marker.setdefault("history", []).append(record)
        applied.append(record)
    marker["schema_version"] = max(current, marker.get("schema_version", 0))
    return applied


# ── entry point ─────────────────────────────────────────────────────────────

def initialize_storage(paths: AppPaths) -> dict:
    """Create the layout, import a legacy install once, run versioned
    migrations, and record completion. Idempotent and safe to call every launch.
    Returns a report dict describing what happened."""
    paths.ensure()
    marker = _load_marker(paths)
    first_time = marker is None
    report: dict = {
        "root": str(paths.root), "fresh_install": False,
        "legacy_import": None, "migrations_applied": [],
        "already_initialized": False,
    }

    if first_time:
        legacy = find_legacy_install(paths.root)
        if legacy is not None:
            imp = _import_legacy(legacy, paths)
            report["legacy_import"] = imp
            if imp["errors"]:
                log.error("legacy import completed with %d error(s): %s",
                          len(imp["errors"]), imp["errors"][:5])
            else:
                log.info("imported legacy install from %s (%d files copied, %d kept)",
                         legacy, imp["copied"], imp["skipped"])
        else:
            report["fresh_install"] = True
            log.info("fresh install — new storage root at %s", paths.root)
        marker = {"schema_version": 0, "initialized_at": _now(),
                  "legacy_import": report["legacy_import"], "history": []}

    applied = _run_versioned(paths, marker)
    report["migrations_applied"] = applied

    changed = first_time or bool(applied) or marker.get("schema_version", 0) < BASELINE_VERSION
    marker["schema_version"] = max(marker.get("schema_version", 0), BASELINE_VERSION)
    if changed:
        marker["updated_at"] = _now()
        _save_marker(paths, marker)

    report["schema_version"] = marker["schema_version"]
    report["already_initialized"] = not first_time and not applied
    return report
