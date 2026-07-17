# QUICK_START.md — the minimum to start productive work

Only what you need to go from a fresh checkout to running code. For
context on *why* the project is built this way, read `AI_CONTEXT.md` and
`CLAUDE.md` — but you can start working before you finish reading them.

## 1. Set up and confirm it works

```powershell
.\scripts\verify.ps1
```

One command: creates `.venv` if missing, installs everything, runs the
full test suite, checks the frontend and docs for drift, and (if the
`[browser]` extra is installed) drives the app in a real headless browser.
Should end in `VERIFY: PASS`. If it doesn't, stop here and fix that first —
nothing below is meaningful on top of a red baseline.

## 2. Run the app

```powershell
.\scripts\dev.ps1
```

Opens a dev server at `http://127.0.0.1:8787` with the live scan loop
disabled (`--no-loop`) — safe to leave running, nothing will place a paper
trade on its own. Add `-Ui` for the real desktop window instead of a
browser tab, or `-Loop` to also run the live scan loop.

## 3. Make a change, then verify again

```powershell
.\scripts\test.ps1                    # fast: just the test suite
.\scripts\test.ps1 tests\test_x.py    # one file
.\scripts\verify.ps1                  # everything, before you're done
```

## 4. Read `NEXT_SESSION.md` for what to actually do

`docs/NEXT_SESSION.md` has the current handoff: what was just finished,
what's stable, what to work on next, and what not to touch. Start there,
not here, for task-level direction — this file only gets you *able* to work,
not *told what to do*.

## The scripts, in one line each

| Script | Does |
|---|---|
| `scripts/dev.ps1` | Start the app for local development |
| `scripts/test.ps1` | Run the test suite (optionally one file / `-k` filter) |
| `scripts/verify.ps1` | Run every automated check — the pre-commit gate |
| `scripts/docs.ps1` | Documentation consistency only (part of `verify.ps1`) |
| `scripts/build.ps1` | Build the Windows exe (tests run first, always) |
| `scripts/release.ps1` | Full release-readiness check + report |
| `scripts/clean.ps1` | Remove `__pycache__`/`.pytest_cache`/`*.egg-info` clutter |

Full detail on each: `docs/CONTRIBUTING.md`. Release process: `docs/RELEASE_CHECKLIST.md`.

## The one rule you cannot miss

**This is a paper-trading-only system by design.** Never implement a
real order-placing broker adapter, never weaken the live-trading gate in
`config/settings.py`, unless the user explicitly asks for that specific
change in a dedicated request. Full detail: `CLAUDE.md`.
