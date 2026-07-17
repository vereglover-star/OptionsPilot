# NEXT_SESSION.md — start here

Concise session-to-session handoff. Keep this current — update it at the end
of every significant session, not "later." For the detailed narrative behind
any of this, see `PROJECT_STATE.md`; for the structured snapshot, see
`PROJECT_STATUS.md`.

**Last updated:** 2026-07-17.

## What was completed?

The 2026-07-17 session finished two things as one commit (`50c75aa`):

1. **V2-4's remaining chart scope**: labeled trade lines on the chart
   (entry/stop/target for open positions, trigger levels for working manual
   orders), plus three new drawing tools — Fib retracement, Zone rectangle,
   and bar Notes — alongside the existing Level/Trend tools.
2. **Completed manual-entry risk gating**: a prior session had added
   `RiskManager.approve_manual_entry` but never wired it into the immediate
   market-buy path, so a halted account could still place a manual order.
   That's fixed and covered by tests now.

A separate documentation-and-workflow session (this one, in progress) is
building the permanent AI-development doc framework described in
`AI_CONTEXT.md`.

## What is currently stable?

Everything. 345 tests pass. The last live browser verification (chart
drawing tools, trade lines, a real manual buy + protective stop, the coach
reviewing the round trip) was 2026-07-17 and found no bugs. No known open
bugs (`PROJECT_STATUS.md` "Known bugs": none).

## What should be worked on next?

This is an open scope decision, not a technical blocker — see
`ROADMAP.md` "Planned":

1. **V2-5** — replay engine (historical day replay, separate replay
   account, coach reviews replay trades).
2. **V2-6** — journal & improvement dashboard (chart snapshots per trade,
   notes/emotions fields, journal filtering — partially covered already by
   the Coach tab's `CoachProfile`).
3. **V2-4 workspace remainder** — the full three-panel layout and
   multi-chart layouts (large, optional, deliberately deferred).
4. Or: pause feature work and let paper-trading data accumulate — also a
   legitimate choice per the project's stated gate on ever considering live
   trading.

If starting fresh with no specific instruction, ask the user which of these
they want rather than guessing.

## What files are currently important?

- `optionspilot/orchestrator.py` — the one event loop; almost any
  cross-cutting feature touches this.
- `optionspilot/risk/manager.py` — just modified (manual-entry gating);
  understand `_entry_veto`, `approve`, and `approve_manual_entry` before
  touching risk logic again.
- `optionspilot/ui/static/index.html` — the entire frontend; the Charts tab
  (`CH` object, `ch*` functions) was just substantially extended.
- `docs/ROADMAP.md` / `docs/ROADMAP-V2.md` — read before starting any new
  feature to check whether it's already scoped.

## What should NOT be modified?

See `AI_CONTEXT.md` "Things future AI assistants should never change
without careful review" for the full list. The two with the highest cost if
gotten wrong: never implement a real live-broker adapter, and never touch
`optionspilot/core/models.py` without grepping every usage first.

## Known issues

- No favicon served (`/favicon.ico` 404s — the only console error found
  during 2026-07-17's browser verification). Low priority, in `TODO.md`.
- No automated browser/UI test coverage for `static/index.html` — see
  `TODO.md` and `CONTRIBUTING.md` "Automation opportunities."
- `pyproject.toml` has no linting/formatting/type-checking configuration —
  see `CONTRIBUTING.md` "Automation opportunities" for a recommended,
  not-yet-applied starting point.

## Suggested first prompt for the next AI session

> Read `docs/AI_CONTEXT.md`, `CLAUDE.md`, and this file, then run
> `git log --oneline -10` and `git status` yourself to verify current state
> before trusting anything written here. Then: [describe the specific task —
> e.g. "implement V2-5's replay engine per `docs/ROADMAP.md`'s scope," or
> "investigate X," or paste a bug report]. If no specific task is given, ask
> which of `ROADMAP.md`'s "Planned" items to work on rather than guessing.
