# RELEASE.md — CI/CD & release pipeline (Professional Release Pipeline 1.0)

How OptionsPilot is built, tested, packaged, and published. A version tag now
produces a full GitHub Release: the professional **Windows installer**
(`OptionsPilot-Setup-vX.Y.Z.exe`) **and** the portable zip
(`OptionsPilot-vX.Y.Z.zip`). Installer specifics live in `docs/INSTALLER.md`.

## Overview

```
 developer                         GitHub Actions                        output
 ─────────                         ──────────────                        ──────
 push / PR         ──────────▶  ci.yml (test)                     green check ✓
   any branch                    · pip install -e .[dev,ui]
                                 · pytest (full suite)
                                 · optionspilot selftest
                                 · check_html_ids + check_docs

 git tag vX.Y.Z    ──────────▶  release.yml
   git push --tags               ├─ test  (reuses ci.yml — no drift)
                                 └─ build (needs: test)            GitHub Release
                                    · verify tag == __version__      vX.Y.Z
                                    · scripts/build.ps1  (PyInstaller + selftest gate)
                                    · scripts/package_release.ps1  ─▶ OptionsPilot-vX.Y.Z.zip
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
- The GitHub Release name/tag and the artifact filename derive from it.
- `scripts/bump_version.py X.Y.Z` edits that one line.
- `scripts/check_docs.py` fails if `pyproject.toml` ever hardcodes a second copy,
  and `release.yml` fails fast if the pushed tag ≠ `__version__`.

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

1. **Bump the version** and update the changelog:
   ```
   python scripts/bump_version.py 0.5.0
   # edit docs/CHANGELOG.md: retitle the top [Uncommitted] section to a dated 0.5.0 entry
   ```
2. **Verify locally** (optional but recommended — the same gates CI runs, plus
   the browser check CI skips):
   ```
   .\scripts\verify.ps1
   .\scripts\build.ps1            # optional: prove the exe builds + packaged selftest passes
   .\scripts\package_release.ps1  # optional: produce the zip locally
   ```
3. **Commit, tag, push:**
   ```
   git add -A
   git commit -m "Release v0.5.0: <summary>"
   git tag v0.5.0
   git push origin main --tags
   ```
4. **GitHub Actions does the rest**: `release.yml` runs the tests, builds the
   exe, packages the zip, compiles the installer, and creates the **GitHub
   Release `v0.5.0`** with **both** `OptionsPilot-Setup-v0.5.0.exe` and
   `OptionsPilot-v0.5.0.zip` attached and notes drawn from `docs/CHANGELOG.md`.
   Watch it under the repo's **Actions** tab.

`scripts/release.ps1 -Version 0.5.0` still works as the local dry run (verify +
build + a printed checklist); it never tags or publishes. The tag is the trigger.

## How to trigger each pipeline

| Pipeline | Trigger | Command |
|---|---|---|
| **CI** | push to any branch, or open/update a PR | `git push` / open a PR |
| **CI (manual reuse)** | called by release.yml | automatic on a tag |
| **Release / tag build** | push a `v*` tag | `git tag v0.5.0 && git push origin --tags` |

To re-run a failed release without changing code, delete and re-push the tag:
`git push --delete origin v0.5.0 && git tag -d v0.5.0 && git tag v0.5.0 && git push origin --tags`.

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
