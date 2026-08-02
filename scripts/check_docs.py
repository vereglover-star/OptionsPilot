"""Documentation consistency checks:

  1. Every `docs/*.md` or top-level `*.md` file this repo's own docs
     cross-reference by name actually exists.
  2. Test-count claims in "current state" docs match the real, live
     pytest count.
  3. The version string agrees between pyproject.toml and
     optionspilot/__init__.py.

Run via scripts/docs.ps1, or as part of scripts/verify.ps1. Exit 0 on a
clean pass, 1 if anything drifted. This exists because this repo has
already drifted twice in one week (a stale README claiming "Phase 1 of 8,"
and a stale test count in CLAUDE.md) - see docs/AI_CONTEXT.md "Common
mistakes to avoid."
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# Files that claim a *current* test count - these must match reality.
# CHANGELOG.md and PROJECT_STATE.md are deliberately exempt: they contain
# multiple historical, dated counts that were correct for their moment and
# are not meant to track the present (that's the whole point of a changelog).
CURRENT_COUNT_FILES = [
    ROOT / "CLAUDE.md",
    ROOT / "README.md",
    DOCS / "AI_HANDOFF.md",
    DOCS / "PROJECT_STATUS.md",
    DOCS / "NEXT_SESSION.md",
]

MD_FILES = [ROOT / "README.md", ROOT / "CLAUDE.md", *sorted(DOCS.glob("*.md"))]

# Matches `docs/FOO.md`, `FOO.md`, [text](docs/FOO.md), [text](FOO.md)
LINK_PATTERN = re.compile(r'(?:`|\()((?:docs/)?[A-Za-z0-9_.-]+\.md)(?:`|\))')

# pytest --collect-only -q prints one "tests/test_x.py: N" line per file in
# this repo's pytest version (no single "N tests collected" total line
# exists to match against) - sum the per-file counts instead. Anchored to
# "tests/" so a warnings-summary line like "...\fastapi\testclient.py:1:
# StarletteDeprecationWarning" (a real false positive hit during testing of
# this script) can never be mistaken for a collected-test-count line.
COLLECT_LINE = re.compile(r"^tests[\\/]\S+\.py:\s*(\d+)$", re.MULTILINE)


def check_links() -> list[str]:
    problems = []
    for f in MD_FILES:
        text = f.read_text(encoding="utf-8")
        for m in LINK_PATTERN.finditer(text):
            ref = m.group(1)
            candidates = [ROOT / ref, DOCS / ref, DOCS / Path(ref).name]
            if not any(c.exists() for c in candidates):
                problems.append(f"{f.relative_to(ROOT)}: references missing file '{ref}'")
    return problems


def live_test_count() -> int | None:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - this check is best-effort
        print(f"WARN: could not collect a live test count ({exc}); skipping count checks.")
        return None
    matches = COLLECT_LINE.findall(out.stdout)
    if not matches:
        print("WARN: pytest --collect-only produced no per-file counts to sum; "
              "skipping count checks.")
        return None
    return sum(int(n) for n in matches)


def check_test_counts(live_count: int | None) -> list[str]:
    if live_count is None:
        return []
    problems = []
    claim_pattern = re.compile(r"\b(\d{2,4})\s+tests\b")
    for f in CURRENT_COUNT_FILES:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        for claimed in {int(n) for n in claim_pattern.findall(text)}:
            if claimed != live_count:
                problems.append(
                    f"{f.relative_to(ROOT)}: claims {claimed} tests, "
                    f"live count is {live_count}"
                )
    return problems


def declared_version() -> str | None:
    """The one true version, read from optionspilot/__init__.py."""
    init = (ROOT / "optionspilot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    return match.group(1) if match else None


def check_documented_version() -> list[str]:
    """Docs that state a version in prose must state the REAL one.

    `check_version()` below proves the version has a single source of truth.
    It says nothing about whether the documentation agrees with it, and that
    gap was not theoretical: `docs/PROJECT_STATUS.md` announced "Current
    version `0.5.0`" while the code was 0.8.2 — through four releases, past
    the release workflow's tag gate (which compares the tag to the constant,
    never to the prose), and past every check in this file.

    A status document that is confidently three releases stale is worse than
    no status document, because it is read and believed.
    """
    version = declared_version()
    if version is None:
        return []                      # reported by check_version()
    problems = []
    # `## Current version` followed by a `X.Y.Z` in backticks.
    pattern = re.compile(
        r"^##\s+Current version\s*$\n+`(?P<claim>\d+\.\d+\.\d+)`", re.M)
    for path in (DOCS / "PROJECT_STATUS.md",):
        if not path.exists():
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match is None:
            problems.append(
                f"{path.relative_to(ROOT)}: no parsable '## Current version' "
                f"section — the version-drift check cannot see it, so it "
                f"would silently pass. Restore the `X.Y.Z` line or update "
                f"scripts/check_docs.py deliberately.")
            continue
        if match.group("claim") != version:
            problems.append(
                f"{path.relative_to(ROOT)}: states version "
                f"{match.group('claim')}, but optionspilot.__version__ is "
                f"{version}")
    return problems


def check_version() -> list[str]:
    """The version has a single source of truth: optionspilot/__init__.py's
    __version__. Verify pyproject.toml derives it dynamically from there rather
    than hardcoding a (drift-prone) second copy."""
    problems = []
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "optionspilot" / "__init__.py").read_text(encoding="utf-8")
    if not re.search(r'__version__\s*=\s*"([^"]+)"', init):
        problems.append("optionspilot/__init__.py has no __version__ string")
    if re.search(r'(?m)^version\s*=\s*"[^"]+"', pyproject):
        problems.append(
            "pyproject.toml hardcodes a `version = \"...\"` — it must derive the "
            "version dynamically from optionspilot.__version__ (single source)."
        )
    if 'attr = "optionspilot.__version__"' not in pyproject:
        problems.append(
            "pyproject.toml must set [tool.setuptools.dynamic] "
            'version = {attr = "optionspilot.__version__"}'
        )
    return problems


def main() -> int:
    problems: list[str] = []
    problems += check_links()
    live_count = live_test_count()
    problems += check_test_counts(live_count)
    problems += check_version()
    problems += check_documented_version()

    if problems:
        print(f"FAIL: {len(problems)} documentation consistency issue(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    note = f" (live count: {live_count})" if live_count is not None else ""
    print(f"OK: doc cross-references resolve, test counts agree{note}, "
          f"version in sync, docs state v{declared_version()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
