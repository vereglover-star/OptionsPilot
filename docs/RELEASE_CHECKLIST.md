# RELEASE_CHECKLIST.md — the exact process for shipping a release

There is no formal release process yet (no GitHub Releases have been cut;
the version is `0.3.1`, bumped from `0.1.0` at V3.2 and patched at V3.2.1). This document is
the process for when that changes, built around `scripts/release.ps1`,
which automates everything on this list except the explicitly-marked
manual steps.

## The one-command version

```powershell
.\scripts\release.ps1 -Version 0.2.0
```

This runs steps 1–6 below automatically and prints a PASS/FAIL report
plus the exact remaining commands to run yourself. If you don't want a new
version number yet, omit `-Version`. If you don't want the exe rebuilt
yet (e.g. you're just checking release-readiness), add `-SkipBuild`.

## What `release.ps1` automates

1. **Git state check** — confirms the working tree is clean (`git status`
   **and** `git diff --stat`, both — see `AI_CONTEXT.md` "Common mistakes
   to avoid" for why both matter). Fails loudly if there's anything
   uncommitted.
2. **Version bump** (only if `-Version` is passed) — `scripts/bump_version.py`
   updates `pyproject.toml` and `optionspilot/__init__.py` together, so
   they can never drift out of sync.
3. **Full verification** (`scripts/verify.ps1`) — the complete automated
   gate:
   - Full pytest suite (100% must pass).
   - Static HTML `$("id")` reference check on `static/index.html`.
   - Documentation consistency check (cross-references resolve, test
     counts agree, version is in sync).
   - `pip check` (no broken/conflicting dependencies).
   - Headless-browser smoke check across every tab (zero console errors) —
     if the `[browser]` extra is installed.
4. **Executable build** (unless `-SkipBuild`) — `scripts/build.ps1`, which
   refuses to run on a red test suite and wraps the existing
   `scripts/build_exe.ps1` (data backup/restore, running-instance guard,
   unchanged).
5. **Release-readiness report** — every step above as PASS/FAIL, with a
   final verdict.
6. **Printed next steps** — the exact manual commands for the steps below,
   pre-filled with the version number if one was given.

## What stays manual (by design — see "Why these stay manual" below)

- [ ] **Review `docs/CHANGELOG.md`** — confirm there's a dated entry
      (or `[Uncommitted]` section, renamed to a real date) covering
      everything in this release. `release.ps1` does not write this for
      you; it's a judgment call about what to say, not a mechanical check.
- [ ] **Review the other doc-update checklist** in `CONTRIBUTING.md`
      "Documentation requirements" if you haven't already this session —
      `PROJECT_STATE.md`, `PROJECT_STATUS.md`, `NEXT_SESSION.md`,
      `TODO.md`, `ROADMAP.md`/`ROADMAP-V2.md` should all reflect reality
      before you tag.
- [ ] **`git add -A; git commit -m "..."`** — following the convention in
      `CONTRIBUTING.md` "Commit message conventions."
- [ ] **`git tag v<version>`** (only if you bumped the version).
- [ ] **`git push origin main --tags`**.
- [ ] **`gh release create v<version> dist\OptionsPilot -F docs\CHANGELOG.md`**
      (or your platform's equivalent) — attaches the built exe and uses the
      changelog as release notes. Requires the GitHub CLI, and a GitHub
      remote is already configured (`origin` → the project's repo).
- [ ] **Smoke-test the actual built exe** once, by hand, in a real window —
      `dist\OptionsPilot\OptionsPilot.exe` — before calling a release done.
      The automated browser check in step 3 exercises the FastAPI/frontend
      stack in serve mode; it does not launch the packaged pywebview
      window or exercise the PyInstaller-specific code paths
      (single-instance guard, windowed-mode logging, icon).

## Why these stay manual

Per this project's standing safety rules: pushing code, creating a public
release, and tagging are all actions with an external, hard-to-reverse
footprint (a force-push or a bad tag is a pain to unwind once someone else
has pulled it). `release.ps1` deliberately stops short of them — it earns
trust by proving everything *automatable* is green, then hands you the
exact commands rather than running them itself. This mirrors the same
judgment call already made in `scripts/build_exe.ps1` (the *decision* to
rebuild the exe is a human one, made deliberately last, not automated) and
in `CLAUDE.md`'s git safety protocol more broadly.

## First real release checklist (when it happens)

The above is process, not history — there's no prior release to point to
for "how did we actually do it last time." When the first real release
ships, add a dated entry here (or a footnote) recording anything this
checklist got wrong or missed, the same way `CHANGELOG.md` tracks feature
history. Don't let this document drift the way `README.md` and `CLAUDE.md`
were found to have drifted in earlier sessions (see `AI_CONTEXT.md`) —
`scripts/docs.ps1` checks cross-references and test counts, but nothing
mechanically checks that this checklist still matches how a release
actually gets made; that's a judgment call for whoever runs it.
