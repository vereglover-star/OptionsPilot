# RELEASE_CHECKLIST.md — the exact process for shipping a release

> **The checklist is now a script.** Everything that used to be a numbered
> manual step — bump, verify, commit, tag, push, watch the build — is
> `scripts/release.ps1`. The authoritative reference is **`docs/RELEASE.md`**
> (phases, flags, rollback, configuration, monitoring). This file is the short
> operational version and the record of what deliberately stayed human.

## The whole process

```powershell
.\scripts\release.ps1 0.9.3 -DryRun   # rehearse: every check, no changes
.\scripts\release.ps1 0.9.3           # release
```

There is no step 3. No manual `git` and no manual GitHub action is required
afterwards unless the script reports an unrecoverable error.

## What to do before running it

- [ ] **Write the `docs/CHANGELOG.md` entry** for the new version — a `## `
      heading that names it. This is the release notes GitHub will publish
      (`scripts/release_notes.py` extracts it). Preflight refuses to release
      without one; it cannot write it, because what to say is a judgement.
- [ ] **Finish the documentation checklist** in `CONTRIBUTING.md`
      "Documentation requirements" — `PROJECT_STATE.md`, `PROJECT_STATUS.md`,
      `NEXT_SESSION.md`, `TODO.md`, `ROADMAP.md` should already reflect reality.
      (The release itself rewrites `PROJECT_STATUS.md`'s version line; the rest
      is yours.)
- [ ] **Commit both.** The working tree must be clean when the release starts.
- [ ] Optionally `$env:GH_TOKEN = "..."` — not required, but it speeds up the
      build monitoring and makes a failure report include the failing job's log.

## What the script does, and what it refuses

Full detail in `docs/RELEASE.md`. In brief, it will not proceed unless:

- the working tree is clean (`git status`, `git diff` **and** the staged index
  are all consulted — `git status` has printed "clean" in this repository while
  `git diff --stat` showed real changes; see `AI_CONTEXT.md` "Common mistakes")
- no merge, rebase, cherry-pick, revert or bisect is half-finished
- you are on the configured release branch (`ReleaseConfig.ps1`, or `-Branch`)
- the branch is not behind its upstream
- the new version is strictly newer than the current one
- the existing version literals all still agree with each other
- the tag does not exist **locally or on the remote**
- `docs/CHANGELOG.md` has a section for the version

and it will not commit, tag or push unless `scripts/check_docs.py` and the full
`scripts/verify.ps1` are both green against the bumped tree.

Anything that fails before the push is **rolled back automatically** — bump
reverted, commit reset, tag deleted. After the push the rollback is disarmed on
purpose: undoing published history is not a decision a script gets to make.

## What stays manual — and why

Only two things, and neither is mechanical:

- [ ] **The CHANGELOG entry** (above). A judgement about what to say, not a
      check that can be computed.
- [ ] **Smoke-test the published build once, by hand.** Download the installer
      from the Release the script just pointed you at, install it, and launch
      it. The six browser suites drive the FastAPI/frontend stack in serve
      mode; nothing in CI launches the packaged pywebview window or exercises
      the PyInstaller-specific paths — the single-instance guard, windowed-mode
      logging (where `sys.stdout is sys.stderr is None`), the tray icon, the
      icon itself. Every one of those has produced a real shipped bug.

## Why the rest stopped being manual

The previous version of this document argued that pushing, tagging and
publishing "have an external, hard-to-reverse footprint" and should therefore
stay human. The footprint argument was right; the conclusion was not. Six
hand-typed commands do not make a release safer than one — they make it *less*
reproducible, and they put the irreversible steps (`git tag`, `git push`) after
the point where a tired human has already decided the release is fine.

What actually makes the footprint safe is that nothing irreversible happens
until every reversible thing has been proven: eleven preflight checks against
the real repository and the real remote, a full verification suite against the
bumped tree, and an automatic rollback for every step before the push. That is
a stronger guarantee than the honour system it replaced, and it is the same
judgement `build.ps1` already made when it refused to invoke PyInstaller on a
red suite.

The parts that genuinely need a human — deciding what the release *says*, and
looking at the thing that shipped — are still above, and are now the only
things on this list.

## Testing the release path itself

`tests/test_release_automation.py` covers the decidable half (version
locations, semver ordering, remote-URL parsing, failure-report extraction) and
pins the structural half (phase order, rollback registration, no `--force`, no
`--no-verify`, `-SkipVerify` unreachable outside a dry run).

To exercise `release.yml` end-to-end without publishing anything real, push a
throwaway pre-release tag on a branch (e.g. `v0.0.0-test`), then delete the tag
and the Release it creates.
