# RELEASE.md — CI/CD & release pipeline (Professional Release Pipeline 1.0)

How OptionsPilot is built, tested, packaged, and published. A version tag
produces a full GitHub Release: the professional **Windows installer**
(`OptionsPilot-Setup-vX.Y.Z.exe`), the portable zip (`OptionsPilot-vX.Y.Z.zip`)
and a `SHA256SUMS` manifest. Installer specifics live in `docs/INSTALLER.md`.

## Releasing is one command

```powershell
.\scripts\release.ps1 0.9.3
```

That is the whole process. No manual `git` and no manual GitHub steps are
required afterwards unless the script reports an unrecoverable error.

Run it with `-DryRun` first — see "The dry run" below.

## Overview

```
 developer                         GitHub Actions                        output
 ─────────                         ──────────────                        ──────
 push / PR         ──────────▶  ci.yml (test)                     green check ✓
   any branch                    · pip install -e .[dev,ui]
                                 · pytest (full suite)
                                 · optionspilot selftest
                                 · check_html_ids + check_docs

 .\scripts\release.ps1 X.Y.Z ─▶  (local)                          the tag
   1 preflight                     · clean tree, no merge/rebase in progress,
                                     right branch, not behind, version moves
                                     forward, tag free locally AND on origin,
                                     CHANGELOG section exists
   2 bump                          · scripts/bump_version.py X.Y.Z
   3 verify                        · scripts/check_docs.py, then verify.ps1
   4 commit + tag                  · "Release vX.Y.Z", annotated tag
   5 push                          · branch, then tag
   6 monitor                ──────▶ watches release.yml below

 git tag vX.Y.Z    ──────────▶  release.yml
   (pushed by the script)        ├─ test  (reuses ci.yml — no drift)
                                 └─ build (needs: test)            GitHub Release
                                    · verify tag == __version__      vX.Y.Z
                                    · scripts/build.ps1  (PyInstaller + selftest gate)
                                    · scripts/package_release.ps1  ─▶ OptionsPilot-vX.Y.Z.zip
                                    · scripts/build_installer.ps1  ─▶ OptionsPilot-Setup-vX.Y.Z.exe
                                    · SHA256SUMS over both
                                    · scripts/release_notes.py     ─▶ notes from CHANGELOG
                                    · gh release create + upload
```

Two workflows, both on `windows-latest` (the target platform; pywebview and
windows-toasts are Windows-only):

- **`.github/workflows/ci.yml`** — runs on every branch push and PR, and is
  exposed as a reusable workflow (`workflow_call`).
- **`.github/workflows/release.yml`** — runs on `v*` tags; its `test` job
  **reuses `ci.yml`** so a tag can never ship without the exact same green suite.

## Versioning — single source of truth

The version lives in **one place**: `optionspilot/__init__.py` (`__version__`).

- `pyproject.toml` derives it dynamically:
  `[tool.setuptools.dynamic] version = {attr = "optionspilot.__version__"}`.
- The app UI reads `optionspilot.__version__` (surfaced at `/api/status`).
- The installer's `AppVersion` and setup filename come from
  `scripts/build_installer.ps1 → ISCC /DMyAppVersion=`.
- The portable zip's filename comes from `scripts/package_release.ps1`.
- The GitHub Release name/tag and the release notes derive from it.
- `scripts/check_docs.py` fails if `pyproject.toml` ever hardcodes a second copy,
  and `release.yml` fails fast if the pushed tag ≠ `__version__`.

**One place in the *code*, and exactly one copy in the *documentation*.**
`docs/PROJECT_STATUS.md` states the current version in prose, and that copy is
enforced by `check_docs.py::check_documented_version` — which is how it was
caught announcing `0.5.0` while the code was `0.8.2`, through four releases.

The authoritative list of which files hold a literal copy and which merely
derive one is a single table, `scripts/lib/release_support.py::LOCATIONS` and
`DERIVED`. Print it any time:

```powershell
python scripts/lib/release_support.py locations
```

`scripts/bump_version.py X.Y.Z` writes every literal from that table (and
`--check` verifies they all agree). Adding a new location means adding a row
there, in one place — and a location whose pattern stops matching is a hard
**error**, never a silent skip, because the tolerant behaviour would ship a
release whose code says one version and whose docs say another.

## Artifacts

Each release publishes **two** artifacts:

1. **`OptionsPilot-Setup-vX.Y.Z.exe`** — the Windows installer (Inno Setup;
   installs to `C:\Program Files\OptionsPilot`, Start Menu + desktop shortcuts,
   Programs-and-Features registration, upgrade/uninstall). Full details in
   `docs/INSTALLER.md`. Built by `scripts/build_installer.ps1`.
2. **`OptionsPilot-vX.Y.Z.zip`** — the portable, no-install bundle, from
   `scripts/package_release.ps1`:

   ```
   OptionsPilot-v0.5.0.zip
     OptionsPilot/        ← the PyInstaller one-dir bundle (OptionsPilot.exe + _internal\ + config.yaml)
     LICENSE
     README.md
     CHANGELOG.md
   ```

The zip **excludes** source, tests, build caches, and any local user state
(`data\`, `logs\`) a local build may have left beside the exe — a release never
ships a developer's paper account.

**The installer asset name matters (V0.5.0+):** the in-app auto-updater
(`docs/AUTO_UPDATER.md`) finds a release's installer by matching
`OptionsPilot-Setup-vX.Y.Z.exe` and will only ever download that asset. Keep the
naming produced by `scripts/build_installer.ps1` stable — renaming the setup
asset would silently make releases invisible to installed apps. The updater also
skips **drafts** always and **prereleases** unless the user opts into the beta
channel, so mark experimental tags as prereleases on GitHub.

## How to publish a release

### Before you start

Two things are content, not mechanics, and the script will not invent either:

1. **`docs/CHANGELOG.md` has a section naming the new version.** The release's
   published notes are the first `## ` heading that mentions it
   (`scripts/release_notes.py`). Preflight refuses to release without one —
   the alternative is a GitHub Release whose body is a one-line stub.
2. **The rest of the doc checklist in `CONTRIBUTING.md` is done** —
   `PROJECT_STATE.md`, `PROJECT_STATUS.md`, `NEXT_SESSION.md`, `TODO.md`,
   `ROADMAP.md` should already reflect reality.

Commit both. The working tree must be clean when the release starts.

### The dry run

```powershell
.\scripts\release.ps1 0.9.3 -DryRun
```

Runs **every** check for real — against the real repository, the real remote
and the real tag namespace — and prints exactly what it would do, including the
commit message it would use. It modifies nothing, anywhere. Add `-SkipVerify`
(accepted **only** with `-DryRun`) to turn a ~6-minute rehearsal into a
~10-second one when you only want the preflight verdict.

### The release

```powershell
.\scripts\release.ps1 0.9.3
```

Six phases. Everything before phase 5 is reversible and is reversed
automatically on any failure:

| # | Phase | What it does | On failure |
|---|---|---|---|
| 1 | **Preflight** | clean tree (`status`, `diff` **and** the staged index); no merge/rebase/cherry-pick/revert/bisect in progress; on the configured release branch; not behind upstream; version strictly newer; existing version literals agree; tag free locally **and** on `origin`; CHANGELOG section exists | aborts; nothing was modified |
| 2 | **Bump** | writes the version to every literal location | rolled back |
| 3 | **Verify** | `scripts/check_docs.py`, then the full `scripts/verify.ps1` | rolled back |
| 4 | **Commit + tag** | `Release vX.Y.Z`, staging only the bumped paths, plus an **annotated** `vX.Y.Z` tag | rolled back |
| 5 | **Push** | branch, then tag — the tag triggers the build | see below |
| 6 | **Monitor** | watches `release.yml` to completion | reports the failing step |

On success phase 6 prints the **Release URL**, the **workflow URL** and every
**artifact** with its size. On failure it prints the failed job, the failed
step and number, the exact reason, the workflow URL and the job URL — plus the
tail of the failing job's log if a token is available (see "Monitoring" below).

### Useful flags

| Flag | Effect |
|---|---|
| `-DryRun` | validate everything, modify nothing |
| `-Branch <name>` | release from a different branch, once |
| `-SkipBrowser` | passed through to `verify.ps1` (skips the six browser suites) |
| `-SkipVerify` | **dry run only** — skip the verification phase |
| `-NoMonitor` | push and stop; don't watch the build |
| `-AllowMissingChangelog` | release with a stub release-notes body |
| `-TimeoutMinutes <n>` | how long to watch before giving up (default 60) |

### Rollback

Anything that fails **before the push** restores the repository to exactly
where it started: the version bump is reverted, the release commit is reset
away, the tag is deleted. Each mutating step registers its own undo at the
moment it succeeds and the stack unwinds in reverse, so a failure in phase 3
does not try to delete a tag that phase 4 never created. If an undo itself
fails it is reported and the unwind continues — a half-restored tree with no
explanation is the worst possible outcome.

**After the branch push the rollback is disarmed**, deliberately. Undoing a
published commit or tag means rewriting published history, which this project's
standing rules forbid and which a script must never choose on someone's behalf.
If the *tag* push fails after the branch push succeeded, the script says so and
prints the one command to retry.

### Configuration

`scripts/lib/ReleaseConfig.ps1` holds the fixed choices in one place: the
release branch, the remote, the tag prefix, the workflow filename and the
polling/timeout values. **`ReleaseBranch` is the one most likely to need
changing** — it is `V3-ui` because that is where releases are actually cut
today (`v0.9.2` points at the head of `V3-ui`, which is well ahead of `main`).
Set it to `main` when that branch merges.

### Monitoring, and the GitHub token

Monitoring uses `api.github.com` directly and **does not require the GitHub
CLI** — `gh` is not installed on the machine that cuts these releases, and a
monitor that degraded to "go and look at the Actions tab" precisely there would
not be a monitor.

Reading a public repository's runs needs no credentials. Setting `$env:GH_TOKEN`
(or `GITHUB_TOKEN`, or being logged into `gh`) gets you two things:

- **Faster polling.** The anonymous API allows 60 requests/hour; a 60-minute
  watch at the configured 15s interval would be 240 of them, so an anonymous
  run automatically slows to 75s to stay inside the budget.
- **The failing job's log tail**, printed under the failure report.

### Re-running a failed release

If the build fails, the tag is already public. Fix the cause, commit it, then
remove the tag and release again:

```powershell
git push --delete origin v0.9.3
git tag -d v0.9.3
.\scripts\release.ps1 0.9.3
```

The script prints these three lines for you when a build fails.

## How to trigger each pipeline

| Pipeline | Trigger | Command |
|---|---|---|
| **CI** | push to any branch, or open/update a PR | `git push` / open a PR |
| **CI (manual reuse)** | called by release.yml | automatic on a tag |
| **Release** | a `v*` tag reaching `origin` | `.\scripts\release.ps1 X.Y.Z` |

## What is still manual, and why

Only two things, and neither is mechanical:

- **Writing the CHANGELOG entry.** It is a judgement about what to say.
  Preflight enforces that one *exists*; it cannot write it.
- **Smoke-testing the built exe by hand.** The browser suites drive the
  FastAPI/frontend stack in serve mode; they do not launch the packaged
  pywebview window or exercise the PyInstaller-specific paths (single-instance
  guard, windowed-mode logging, tray, icon). Download the published installer
  and run it once.

Everything else that used to be manual — the bump, the doc copy, the commit,
the tag, both pushes, and watching the build — is now the one command.

## Installer (Professional Windows Installer 1.0)

The Windows installer is built and published automatically. It installs to
`C:\Program Files\OptionsPilot` (admin), registers with Programs and Features,
creates Start Menu + optional Desktop shortcuts, upgrades in place, and prompts
at uninstall before removing user data (default No). Because all user data lives
under `%LOCALAPPDATA%\OptionsPilot` (via `core/paths.py::AppPaths`, separate from
the install dir), upgrades and reinstalls never touch it. **Full details:
`docs/INSTALLER.md`.**

In this pipeline: `release.yml` installs Inno Setup (`choco install innosetup`),
runs `scripts/build_installer.ps1` (which reuses the built `dist\OptionsPilot\`
and stamps `/DMyAppVersion=<__version__>`), and uploads the resulting
`OptionsPilot-Setup-vX.Y.Z.exe` as a second Release asset.

**Still open (a real prerequisite for a friction-free public release):
Authenticode code signing** — the setup and app exe are unsigned, so SmartScreen
warns on first run. A `SignTool` hook is stubbed in the `.iss`; wire it once a
certificate is available. Optional wizard bitmaps can be added for extra polish
(see `docs/INSTALLER.md` "Missing / optional assets").

## The release automation itself

```
scripts/release.ps1              the orchestrator: phase order, and nothing else
scripts/lib/
  ReleaseConfig.ps1              the fixed choices, in one place
  ReleaseLog.ps1                 phases, verdicts, the WOULD voice of a dry run
  ReleaseRollback.ps1            the undo stack
  ReleaseGit.ps1                 every git call the pipeline makes, and its undo
  ReleaseVersion.ps1             version + changelog, bridging to Python
  ReleaseGitHub.ps1              the API client and the workflow watcher
  release_support.py             the decisions, in a language pytest can reach
scripts/bump_version.py          the standalone bump, a thin CLI over the above
```

Two rules hold this together, and `tests/test_release_automation.py` asserts
both:

**`release.ps1` makes no git call itself.** All of it goes through
`ReleaseGit.ps1`. That is what makes "does a release ever push before it
verifies" answerable by reading one short file rather than grepping a long one.

**PowerShell orchestrates; Python decides.** Anything with an edge case —
which files hold a version, whether `0.9.10` is newer than `0.9.9`, which
owner/repo a remote URL points at, which of several runs for one commit is the
one just triggered, which job and step a failed run failed at — lives in
`release_support.py`, because a release path made entirely of shell is a
release path with no tests. The structural properties that *can't* be
executed from pytest are pinned by greps instead: verification before the tag,
the tag before the push, an undo registered for every mutation, no `--force` or
`--no-verify` anywhere in the pipeline, and `-SkipVerify` unreachable without
`-DryRun`.

Five PowerShell 5.1 hazards are handled once, and documented at the point they
are handled, because each one produced a failure that looked like something
else:

- **Native stderr under `$ErrorActionPreference = "Stop"`** becomes a
  *terminating* error when the host's stderr is redirected — which a CI log, a
  `*> file` capture and any non-interactive runner all do. `git push` writes all
  of its progress to stderr, and so does pip's "a new release of pip is
  available" notice. `Invoke-GitCapture` / `Invoke-GitStream` (and
  `Ensure-Environment` in `_common.ps1`) drop to `Continue` and decide from
  `$LASTEXITCODE`, which is the only honest signal a native process gives.
- **Multi-line messages never go through `git -m`.** PowerShell 5.1 re-quotes
  native arguments on the way to `CreateProcess` and mangles embedded newlines
  and quotes doing it. Commit and tag messages are written to a temp file and
  passed with `-F`, UTF-8 with no BOM (a BOM would become the first three
  characters of the subject line).
- **Rollback actions are plain scriptblocks over `$script:` state, not
  closures.** `.GetNewClosure()` binds a block into a fresh dynamic module, and
  a dynamic module cannot see functions dot-sourced into the calling script —
  so the undo would fail with "not recognized" at the exact moment it was
  needed.
- **`[int]` is `Int32`, and every id GitHub issues outgrew it years ago.** Run
  and job ids are around 1.7e10. The monitor used to choose which run to watch
  with `Sort-Object -Property {[int]$_.id}`, and the interesting part is not the
  overflow — it is that a cast failure inside a sort expression is
  **non-terminating**, and `release.ps1` runs under `$ErrorActionPreference =
  "Continue"`. So the sort returned its input unsorted, `-Descending` reversed
  GitHub's newest-first response, and the watcher followed the **oldest** run
  for the tag while reporting its conclusion as the release's. Selecting the run
  is now a `release_support.py` decision (`pick-run`), where Python's arbitrary
  precision makes the range question moot and pytest can reach the edge cases.
  Timeouts, poll intervals, status codes and step counters remain `Int32`
  deliberately; a test bans `[int]` on an *identifier* only.
- **A string piped to a native process carries a UTF-8 BOM.** `json.load`
  refuses it outright — *"Unexpected UTF-8 BOM (decode using utf-8-sig)"* — and
  this affected every sub-command reading stdin, including `summarize-run`,
  which is the failure-reporting path. Nothing caught it because pytest hands
  Python a clean string; it appeared the first time the actual PowerShell →
  Python hand-off was executed. `release_support.read_stdin_json` decodes from
  the byte stream with `utf-8-sig`, so it is the one place that has to be right.
  Note this is the same byte as the `git -F` note above, met from the other
  side: there the fix is to stop writing one, here it is to tolerate one.

## Maintaining the workflows

- **Pinned actions:** `actions/checkout@v4`, `actions/setup-python@v5`. Bump
  deliberately; watch their release notes.
- **Python version:** `3.12` in both workflows (project requires ≥3.12). To test
  more, add a `strategy.matrix.python-version` to `ci.yml`'s `test` job.
- **Dependency caching:** `actions/setup-python`'s `cache: pip` keyed on
  `pyproject.toml`. Editing dependencies naturally invalidates the cache.
- **Single build definition:** the PyInstaller invocation lives **only** in
  `scripts/build_exe.ps1`; the workflow calls `scripts/build.ps1`. Never
  duplicate the PyInstaller flags into YAML — change them in the script.
- **Fail-fast:** `concurrency.cancel-in-progress` on CI; the tag/version guard
  and the packaged `selftest` gate stop a bad release before it publishes.
- **Secrets:** none required — the Release uses the automatic `GITHUB_TOKEN`
  (`contents: write`). Code-signing will add a certificate secret later.
- **Test the release path safely:** push a throwaway pre-release tag on a branch
  (e.g. `v0.0.0-test`) to exercise `release.yml` end-to-end, then delete the tag
  and the draft/Release it creates. (Or temporarily add `workflow_dispatch`.)
