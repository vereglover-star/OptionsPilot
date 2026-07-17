# NEXT_SESSION.md — start here

Concise session-to-session handoff. Keep this current — update it at the end
of every significant session, not "later." For the detailed narrative behind
any of this, see `PROJECT_STATE.md`; for the structured snapshot, see
`PROJECT_STATUS.md`.

**Last updated:** 2026-07-17.

## What was completed?

Three sessions landed on 2026-07-17, in order:

1. **V2-4 finish** (commit `50c75aa`): labeled trade lines on the chart,
   three new drawing tools (Fib/Zone/Note), and completed manual-entry risk
   gating that a prior session had left unwired.
2. **Documentation & AI framework** (commit `1029fb0`): `PROJECT_STATUS.md`,
   `ROADMAP.md`, `ARCHITECTURE.md` (with Mermaid diagrams), `AI_CONTEXT.md`,
   `NEXT_SESSION.md` (this file), `CONTRIBUTING.md`, plus fixes to
   `README.md` and `CLAUDE.md`'s cross-references.
3. **Developer automation** (this session, **not yet committed** — see
   "What files are currently important?" below): a `scripts/` layer
   (`dev`/`test`/`verify`/`docs`/`build`/`release`/`clean` `.ps1` entry
   points, each with one responsibility, composing rather than duplicating
   logic) plus the Python checks they run
   (`check_html_ids.py`/`check_docs.py`/`browser_check.py`/`bump_version.py`),
   two new optional `pyproject.toml` extras (`build`, `browser`), a real
   fix for the favicon 404 (found by the new browser check's first run),
   `docs/QUICK_START.md`, and `docs/RELEASE_CHECKLIST.md`.

## What is currently stable?

Everything. 345 tests pass. `scripts/verify.ps1` (tests + HTML id
references + doc consistency + `pip check` + headless-browser smoke check
across all 9 tabs) ran clean end-to-end as the last action of the
automation session. No known open bugs.

## What should be worked on next?

1. **Commit the automation session's work first** — it's currently an
   uncommitted working tree (`scripts/*`, `pyproject.toml`, the favicon
   fix, doc updates). Run `git status` to see exactly what.
2. Then, an open scope decision (not a technical blocker) — see
   `ROADMAP.md` "Planned":
   - **V2-5** — replay engine.
   - **V2-6** — journal & improvement dashboard.
   - **V2-4 workspace remainder** — the full three-panel layout (large,
     optional, deliberately deferred).
   - Or pause feature work and let paper-trading data accumulate.

If starting fresh with no specific instruction, ask the user which of
these they want rather than guessing.

## What files are currently important?

- `scripts/_common.ps1` — the shared bootstrap every other `scripts/*.ps1`
  dot-sources. Understand `Ensure-Environment` before touching any script.
- `scripts/verify.ps1` — the single command that answers "is the repo
  healthy right now." Run it before and after any nontrivial change.
- `optionspilot/orchestrator.py` — the one event loop; almost any
  cross-cutting feature touches this.
- `optionspilot/risk/manager.py` — manual-entry gating; understand
  `_entry_veto`, `approve`, and `approve_manual_entry` before touching risk
  logic again.
- `optionspilot/ui/static/index.html` — the entire frontend; the Charts tab
  (`CH` object, `ch*` functions) was substantially extended in the V2-4
  finish session.
- `docs/ROADMAP.md` / `docs/ROADMAP-V2.md` — read before starting any new
  feature to check whether it's already scoped.

## What should NOT be modified?

See `AI_CONTEXT.md` "Things future AI assistants should never change
without careful review" for the full list. The two with the highest cost if
gotten wrong: never implement a real live-broker adapter, and never touch
`optionspilot/core/models.py` without grepping every usage first. New this
session: don't automate the *decision* to rebuild the exe or to cut a
release (`scripts/build.ps1`/`release.ps1` automate the mechanics, not the
judgment call of *when* — see `RELEASE_CHECKLIST.md` "Why these stay
manual").

## Known issues

- Frontend coverage is real but shallow (`browser_check.py` is a
  tab-navigation smoke check, not deep per-flow regression testing) — see
  `TODO.md` for the specific remaining opportunity.
- `pyproject.toml` has no linting/formatting/type-checking configuration
  and there's no CI — both are deliberate, documented recommendations, not
  oversights; see `CONTRIBUTING.md` "Automation: what's implemented vs.
  still just recommended" before deciding whether to add either.
- No prior release has ever been cut, so `RELEASE_CHECKLIST.md` is
  unvalidated by a real run — the first time someone actually ships a
  release, check whether the checklist matched reality and fix it if not.

## Suggested first prompt for the next AI session

> Read `docs/AI_CONTEXT.md`, `CLAUDE.md`, and this file, then run
> `git log --oneline -10` and `git status` **and** `git diff --stat`
> yourself to verify current state before trusting anything written here —
> then run `.\scripts\verify.ps1` to confirm the baseline is actually
> green. Then: [describe the specific task — e.g. "implement V2-5's replay
> engine per `docs/ROADMAP.md`'s scope," or "investigate X," or paste a bug
> report]. If no specific task is given, ask which of `ROADMAP.md`'s
> "Planned" items to work on rather than guessing.
