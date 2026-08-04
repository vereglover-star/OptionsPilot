"""The decidable parts of the release pipeline, in Python so they are testable.

`scripts/release.ps1` orchestrates a release; this module *decides*. The split
is deliberate and follows the same reasoning as every other gate in this repo:
a release path made entirely of shell is a release path with no tests, and the
one thing a release script must never do is be wrong in a way nobody noticed
until the tag was public.

So PowerShell keeps what it is good at — running git, streaming a build's
output, polling an HTTP endpoint with a progress line — and everything that
involves a *judgement* lives here, reachable from
`tests/test_release_automation.py`:

  * which files hold a literal copy of the version, and which merely derive it
  * whether one version is newer than another
  * which owner/repo a git remote URL points at
  * whether the CHANGELOG has a section for this release
  * which job, which step, and which reason a failed workflow run failed at

Stdlib only. Nothing here touches the network, and only `sync()` writes a file.

Sub-commands (see `main()`), all of which print ASCII-safe output:

    python scripts/lib/release_support.py version
    python scripts/lib/release_support.py locations [--json]
    python scripts/lib/release_support.py compare X.Y.Z
    python scripts/lib/release_support.py sync X.Y.Z [--dry-run] [--json]
    python scripts/lib/release_support.py check
    python scripts/lib/release_support.py repo-slug <url>
    python scripts/lib/release_support.py changelog-has X.Y.Z
    python scripts/lib/release_support.py summarize-run      < payload.json
    python scripts/lib/release_support.py summarize-release  < release.json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: A release version is a plain `X.Y.Z`. Pre-release suffixes are refused
#: rather than half-supported: the tag, the zip name, the installer's
#: `AppVersion` and the auto-updater's comparison would each need a decision
#: about them, and making four decisions implicitly is how a release ships
#: something nobody chose.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


# ── where the version lives ──────────────────────────────────────────────────

@dataclass(frozen=True)
class VersionLocation:
    """A file holding a LITERAL copy of the version that must be written.

    `pattern` must contain a named group `version` spanning exactly the
    characters to replace, so the surrounding text is preserved byte for byte
    and this module never needs a second "how do I write it back" template.
    """

    path: str
    pattern: str
    occurrences: int
    why: str


@dataclass(frozen=True)
class DerivedLocation:
    """A place the version APPEARS but is never written.

    This list exists to be printed. "Avoid duplicated version logic" is easy to
    assert and hard to believe; naming every consumer and how it reaches
    `optionspilot.__version__` is what makes the claim checkable by a reader.
    """

    what: str
    how: str


#: THE source of truth is the first entry. Everything else in this tuple is a
#: copy that some other check already enforces — which is precisely why it must
#: be written here rather than by hand.
LOCATIONS: tuple[VersionLocation, ...] = (
    VersionLocation(
        path="optionspilot/__init__.py",
        pattern=r'__version__\s*=\s*"(?P<version>\d+\.\d+\.\d+)"',
        occurrences=1,
        why="THE SOURCE OF TRUTH. Everything below derives from this line.",
    ),
    VersionLocation(
        path="docs/PROJECT_STATUS.md",
        pattern=r"(?m)^##\s+Current version\s*$\n+`(?P<version>\d+\.\d+\.\d+)`",
        occurrences=1,
        why=(
            "The one documented copy, and a copy with history: PROJECT_STATUS.md "
            "announced 0.5.0 while the code was 0.8.2, through four releases. "
            "scripts/check_docs.py::check_documented_version fails on drift, so "
            "a release that did not write this file could not pass its own "
            "verification gate."
        ),
    ),
)


#: Consumers that read the version at build or run time. Nothing to write.
DERIVED: tuple[DerivedLocation, ...] = (
    DerivedLocation(
        "pyproject.toml / installed metadata",
        '[tool.setuptools.dynamic] version = {attr = "optionspilot.__version__"}',
    ),
    DerivedLocation(
        "UI version display (Settings, About, updater dialog)",
        "ui/static/index.html reads /api/status -> optionspilot.__version__",
    ),
    DerivedLocation(
        "Windows installer (AppVersion, setup filename)",
        "scripts/build_installer.ps1 -> ISCC /DMyAppVersion=<__version__>",
    ),
    DerivedLocation(
        "Portable zip filename",
        "scripts/package_release.ps1 -> dist/OptionsPilot-v<__version__>.zip",
    ),
    DerivedLocation(
        "Git tag and GitHub Release name",
        "scripts/release.ps1 tags v<version>; release.yml refuses a tag that "
        "disagrees with __version__",
    ),
    DerivedLocation(
        "GitHub Release notes",
        "scripts/release_notes.py extracts the CHANGELOG section for the version",
    ),
    DerivedLocation(
        "SHA256SUMS and the in-app auto-updater",
        "release.yml hashes the built assets; update/ compares the release tag "
        "against optionspilot.__version__",
    ),
)


# ── version reading, writing, comparing ──────────────────────────────────────

def _source_location() -> VersionLocation:
    return LOCATIONS[0]


def read_version(root: Path = ROOT) -> str:
    """The current version, read from the source of truth."""
    loc = _source_location()
    text = (root / loc.path).read_text(encoding="utf-8")
    match = re.search(loc.pattern, text)
    if match is None:
        raise ValueError(f"no version found in {loc.path}")
    return match.group("version")


def parse_version(value: str) -> tuple[int, int, int]:
    if not VERSION_RE.match(value):
        raise ValueError(f"not a plain X.Y.Z version: {value!r}")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def compare_versions(left: str, right: str) -> int:
    """-1, 0 or 1 for `left` older than, equal to, or newer than `right`."""
    a, b = parse_version(left), parse_version(right)
    return (a > b) - (a < b)


def _substitute(text: str, pattern: str, version: str) -> tuple[str, int]:
    """Replace every `version` group in `text`, leaving all other bytes alone."""
    out: list[str] = []
    last = 0
    count = 0
    for match in re.finditer(pattern, text):
        start, end = match.span("version")
        out.append(text[last:start])
        out.append(version)
        last = end
        count += 1
    out.append(text[last:])
    return "".join(out), count


def sync(root: Path, version: str, dry_run: bool = False) -> dict:
    """Write `version` into every literal location.

    Returns a report rather than printing one, so both the CLI and the tests
    read the same structure. A location whose pattern no longer matches is an
    ERROR, never a silent skip: a refactor that moves the version out of a file
    this table names must fail the release, because the alternative is a
    release that half-bumped and passed.
    """
    report: dict = {
        "version": version,
        "previous": None,
        "dry_run": dry_run,
        "changed": [],
        "unchanged": [],
        "errors": [],
        "derived": [{"what": d.what, "how": d.how} for d in DERIVED],
    }
    if not VERSION_RE.match(version):
        report["errors"].append(f"not a plain X.Y.Z version: {version!r}")
        return report

    try:
        report["previous"] = read_version(root)
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    for loc in LOCATIONS:
        path = root / loc.path
        if not path.is_file():
            report["errors"].append(f"{loc.path}: file not found")
            continue
        text = path.read_text(encoding="utf-8")
        new_text, count = _substitute(text, loc.pattern, version)
        if count != loc.occurrences:
            report["errors"].append(
                f"{loc.path}: expected {loc.occurrences} version literal(s), "
                f"found {count}. The version moved or the file was restructured "
                f"— update LOCATIONS in scripts/lib/release_support.py "
                f"deliberately rather than letting the release skip it."
            )
            continue
        if new_text == text:
            report["unchanged"].append(loc.path)
            continue
        report["changed"].append(loc.path)
        if not dry_run:
            # newline="" is not used: these files are committed with LF and
            # write_text preserves whatever the substitution produced.
            path.write_text(new_text, encoding="utf-8")

    return report


def check(root: Path = ROOT) -> list[str]:
    """Every literal location must already agree with the source of truth."""
    problems: list[str] = []
    try:
        version = read_version(root)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    for loc in LOCATIONS:
        path = root / loc.path
        if not path.is_file():
            problems.append(f"{loc.path}: file not found")
            continue
        found = [m.group("version")
                 for m in re.finditer(loc.pattern, path.read_text(encoding="utf-8"))]
        if len(found) != loc.occurrences:
            problems.append(
                f"{loc.path}: expected {loc.occurrences} version literal(s), "
                f"found {len(found)}")
            continue
        for claim in found:
            if claim != version:
                problems.append(
                    f"{loc.path}: states {claim}, but the source of truth "
                    f"({_source_location().path}) says {version}")
    return problems


# ── the remote, the changelog ────────────────────────────────────────────────

#: `https://host/owner/repo(.git)`, `git@host:owner/repo(.git)`,
#: `ssh://git@host/owner/repo(.git)`. Anything else is not a slug we can build
#: an API URL from, and guessing one would point the monitor at the wrong repo.
_SLUG_RE = re.compile(
    r"""^
    (?: [a-zA-Z][a-zA-Z0-9+.-]* :// (?: [^/@]+ @ )? [^/]+ /   # scheme://[user@]host/
      | (?: [^/@\s]+ @ )? [^/:\s]+ :                          # [user@]host:
    )
    (?P<owner> [^/\s]+ ) / (?P<repo> [^/\s]+? ) (?: \.git )? /? $
    """,
    re.VERBOSE,
)


def repo_slug(url: str) -> str | None:
    """`owner/repo` for a GitHub remote URL, or None if it cannot be read."""
    match = _SLUG_RE.match((url or "").strip())
    if match is None:
        return None
    owner, repo = match.group("owner"), match.group("repo")
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def changelog_heading(root: Path, version: str) -> str | None:
    """The first `## ` heading naming `version`, or None.

    Deliberately the same rule `scripts/release_notes.py` uses to pick the
    GitHub Release body. If this returns None the published release notes will
    be the generic one-line fallback — which is a thing to be told before the
    tag is pushed, not after.
    """
    changelog = root / "docs" / "CHANGELOG.md"
    if not changelog.is_file():
        return None
    for line in changelog.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and version in line:
            return line.strip()
    return None


# ── reading a workflow run ───────────────────────────────────────────────────

#: Conclusions that mean the job did not do its work. `skipped` and `neutral`
#: are absent on purpose: `build` is legitimately skipped when `test` fails,
#: and reporting the skipped job as the failure would name the consequence
#: instead of the cause.
FAILING_CONCLUSIONS = ("failure", "cancelled", "timed_out", "action_required",
                       "startup_failure", "stale")


def summarize_run(run: dict | None, jobs: dict | None,
                  log_tail: str | None = None) -> dict:
    """Turn a workflow run + its jobs into something worth printing.

    The contract this satisfies is "if it fails, print the failed step, the
    workflow URL and the exact failure reason". Two of those are fields; the
    third is a judgement, and it is the reason this function is tested rather
    than inlined into PowerShell.
    """
    run = run or {}
    job_list = list((jobs or {}).get("jobs") or [])

    summary: dict = {
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "ok": run.get("conclusion") == "success",
        "run_url": run.get("html_url"),
        "run_number": run.get("run_id") or run.get("id"),
        "attempt": run.get("run_attempt"),
        "display_title": run.get("display_title") or run.get("name"),
        "jobs": [
            {
                "name": j.get("name"),
                "conclusion": j.get("conclusion"),
                "status": j.get("status"),
                "url": j.get("html_url"),
            }
            for j in job_list
        ],
        "failed_job": None,
        "failed_job_url": None,
        "failed_job_id": None,
        "failed_step": None,
        "failed_step_number": None,
        "failed_step_conclusion": None,
        "log_tail": log_tail or None,
        "reason": "",
    }

    if summary["ok"]:
        summary["reason"] = "The Release workflow completed successfully."
        return summary

    if run.get("status") and run.get("status") != "completed":
        summary["reason"] = (
            f"The Release workflow is still {run.get('status')} — this summary "
            f"was produced before it finished.")
        return summary

    failed = next(
        (j for j in job_list if j.get("conclusion") in FAILING_CONCLUSIONS), None)

    if failed is None:
        conclusion = run.get("conclusion") or "unknown"
        summary["reason"] = (
            f"The Release workflow concluded '{conclusion}', but no job reported "
            f"a failing step. That usually means the failure is before any step "
            f"ran — a `needs:` dependency, runner allocation, or a permissions "
            f"problem on the workflow itself.")
        return summary

    summary["failed_job"] = failed.get("name")
    summary["failed_job_url"] = failed.get("html_url")
    summary["failed_job_id"] = failed.get("id")

    step = next(
        (s for s in (failed.get("steps") or [])
         if s.get("conclusion") in FAILING_CONCLUSIONS), None)

    if step is None:
        summary["reason"] = (
            f"Job '{failed.get('name')}' concluded "
            f"'{failed.get('conclusion')}' without any single step reporting a "
            f"failure — check the job log for a setup or checkout problem.")
        return summary

    summary["failed_step"] = step.get("name")
    summary["failed_step_number"] = step.get("number")
    summary["failed_step_conclusion"] = step.get("conclusion")
    summary["reason"] = (
        f"Job '{failed.get('name')}' {step.get('conclusion')} at step "
        f"{step.get('number')}: '{step.get('name')}'.")
    return summary


def summarize_release(release: dict | None) -> dict:
    """The published release, reduced to what is worth printing."""
    release = release or {}
    assets = []
    for asset in release.get("assets") or []:
        size = asset.get("size")
        assets.append({
            "name": asset.get("name"),
            "size": size,
            "size_mb": round(size / (1024 * 1024), 1) if isinstance(size, int) else None,
            "url": asset.get("browser_download_url"),
        })
    return {
        "url": release.get("html_url"),
        "tag": release.get("tag_name"),
        "name": release.get("name"),
        "draft": bool(release.get("draft")),
        "prerelease": bool(release.get("prerelease")),
        "assets": assets,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_json(payload: object) -> None:
    # ensure_ascii (the default) keeps every byte printable on a cp1252
    # console, which is what PowerShell 5.1 hands us.
    print(json.dumps(payload, indent=2))


def _cmd_locations(as_json: bool) -> int:
    if as_json:
        _print_json({
            "written": [{"path": loc.path, "why": loc.why} for loc in LOCATIONS],
            "derived": [{"what": d.what, "how": d.how} for d in DERIVED],
        })
        return 0
    print("Version literals this release writes:")
    for loc in LOCATIONS:
        print(f"  {loc.path}")
        print(f"      {loc.why}")
    print("\nConsumers that derive the version (nothing to write):")
    for derived in DERIVED:
        print(f"  {derived.what}")
        print(f"      {derived.how}")
    return 0


def _cmd_sync(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    as_json = "--json" in argv
    positional = [a for a in argv if not a.startswith("--")]
    if len(positional) != 1:
        print("usage: release_support.py sync X.Y.Z [--dry-run] [--json]",
              file=sys.stderr)
        return 2
    report = sync(ROOT, positional[0], dry_run=dry_run)
    if as_json:
        _print_json(report)
    else:
        verb = "would write" if dry_run else "wrote"
        for path in report["changed"]:
            print(f"  {verb} {report['version']} to {path}")
        for path in report["unchanged"]:
            print(f"  already {report['version']}: {path}")
        for problem in report["errors"]:
            print(f"  FAIL: {problem}")
    return 1 if report["errors"] else 0


def _cmd_check() -> int:
    problems = check(ROOT)
    if problems:
        print(f"FAIL: {len(problems)} version location(s) disagree:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: every version literal reads {read_version(ROOT)}.")
    return 0


def _cmd_compare(candidate: str) -> int:
    current = read_version(ROOT)
    try:
        order = compare_versions(candidate, current)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 2
    word = {1: "newer", 0: "same", -1: "older"}[order]
    print(f"{word} (current {current}, requested {candidate})")
    return 0 if order == 1 else 1


def _cmd_summarize_run() -> int:
    payload = json.load(sys.stdin)
    _print_json(summarize_run(payload.get("run"), payload.get("jobs"),
                              payload.get("log_tail")))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    command, rest = args[0], args[1:]

    if command == "version":
        print(read_version(ROOT))
        return 0
    if command == "locations":
        return _cmd_locations("--json" in rest)
    if command == "sync":
        return _cmd_sync(rest)
    if command == "check":
        return _cmd_check()
    if command == "compare":
        if len(rest) != 1:
            print("usage: release_support.py compare X.Y.Z", file=sys.stderr)
            return 2
        return _cmd_compare(rest[0])
    if command == "repo-slug":
        if len(rest) != 1:
            print("usage: release_support.py repo-slug <url>", file=sys.stderr)
            return 2
        slug = repo_slug(rest[0])
        if slug is None:
            print(f"FAIL: cannot read owner/repo from {rest[0]!r}")
            return 1
        print(slug)
        return 0
    if command == "changelog-has":
        if len(rest) != 1:
            print("usage: release_support.py changelog-has X.Y.Z", file=sys.stderr)
            return 2
        heading = changelog_heading(ROOT, rest[0])
        if heading is None:
            print(f"FAIL: docs/CHANGELOG.md has no '## ' section naming {rest[0]}")
            return 1
        print(heading)
        return 0
    if command == "summarize-run":
        return _cmd_summarize_run()
    if command == "summarize-release":
        _print_json(summarize_release(json.load(sys.stdin)))
        return 0

    print(f"unknown sub-command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
