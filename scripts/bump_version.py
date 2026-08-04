"""Set the project version at every location that holds a literal copy.

    python scripts/bump_version.py 0.5.0
    python scripts/bump_version.py 0.5.0 --dry-run
    python scripts/bump_version.py --check

The version's single source of truth is `optionspilot/__init__.py`
(`__version__`). `pyproject.toml` derives it dynamically, the app UI reads
`optionspilot.__version__`, and the installer, the zip name, the git tag and the
GitHub Release all derive from the same place — so almost nothing needs writing.

"Almost" is the point. `docs/PROJECT_STATUS.md` states the current version in
prose, `scripts/check_docs.py` fails when that prose disagrees, and for four
releases it did — the document announced 0.5.0 while the code was 0.8.2. The
authoritative list of what is a literal copy and what merely derives lives in
`scripts/lib/release_support.py::LOCATIONS` / `DERIVED`, in one table, so that
adding a location is a deliberate edit in one place rather than a habit spread
across a script, a checker and a checklist.

Normally invoked by `scripts/release.ps1 X.Y.Z`, which bumps, verifies, commits,
tags, pushes and then watches the GitHub release build. Run it directly when you
want the bump without the release.

Refuses anything that isn't a plain X.Y.Z (no pre-release suffixes) — if this
project ever needs those, extend `release_support.VERSION_RE` deliberately
rather than loosening it by accident.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not a package, so the shared module is loaded by path. Done with
# importlib rather than a sys.path insert so every import in this file stays at
# the top of it (ruff E402).
#
# The sys.modules registration is load-bearing, not tidiness: @dataclass
# resolves a field's type through `sys.modules[cls.__module__]`, so a module
# executed without being registered raises AttributeError on its first
# dataclass. Registering before exec_module is the documented order.
_SPEC = importlib.util.spec_from_file_location(
    "release_support", ROOT / "scripts" / "lib" / "release_support.py")
support = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = support
_SPEC.loader.exec_module(support)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    positional = [a for a in args if not a.startswith("--")]

    if "--check" in args:
        problems = support.check(ROOT)
        if problems:
            print(f"FAIL: {len(problems)} version location(s) disagree:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"OK: every version literal reads {support.read_version(ROOT)}.")
        return 0

    if len(positional) != 1 or not support.VERSION_RE.match(positional[0]):
        print("usage: python scripts/bump_version.py X.Y.Z [--dry-run]")
        print("       python scripts/bump_version.py --check")
        return 1

    report = support.sync(ROOT, positional[0], dry_run=dry_run)
    verb = "would set" if dry_run else "set"
    for path in report["changed"]:
        print(f"OK: {verb} version to {report['version']} in {path}")
    for path in report["unchanged"]:
        print(f"OK: {path} already reads {report['version']}")
    for problem in report["errors"]:
        print(f"FAIL: {problem}")
    if report["errors"]:
        return 1
    print("OK: every other consumer derives the version "
          "(pyproject, UI, installer, zip name, tag, release notes) - "
          "run `python scripts/lib/release_support.py locations` to see how.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
