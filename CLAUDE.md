# CLAUDE.md — instructions for Claude Code sessions working on OptionsPilot

This file is permanent guidance for any Claude Code session touching this
repository. Read it before making changes. It does not replace `docs/` —
see `docs/AI_HANDOFF.md` for architecture orientation and
`docs/PROJECT_STATE.md` for what to do next.

## Before you do anything

1. Read `docs/AI_HANDOFF.md` in full.
2. Read `docs/PROJECT_STATE.md` to find out exactly where the last session
   stopped and what's recommended next.
3. Run `git log --oneline -10` and `git status` yourself — documentation
   can go stale between sessions; verify before trusting it.
4. Only read source files you actually need for the task at hand. The docs
   above exist specifically so you don't have to read the whole codebase to
   get oriented.

## The one rule that overrides everything else

**This is a paper-trading-only system by design, and that is not up for
casual revision.** There is no live-broker implementation anywhere in this
codebase — `broker/registry.py`'s Alpaca/Tradier/Webull/IBKR entries are
named stubs that raise `BrokerError`. Do not:
- Implement a real order-placing broker adapter unless the user explicitly
  and directly asks for it in a dedicated request (not implied by "make it
  better" or similar).
- Weaken the `broker.live_trading_enabled` / `broker.i_understand_the_risks`
  double-gate in `config/settings.py`.
- Add any code path that could place a real-money order without both flags
  AND a real adapter existing.

If a task seems to require live trading, stop and ask the user to confirm
that's really what they want before writing any code.

## Coding standards & conventions

- **Python 3.12+, standard library `dataclasses` for domain models
  (`core/models.py`), pydantic v2 for config validation only.** Don't mix
  the two — models are dataclasses, config is pydantic.
- **The analysis library (`analysis/`) is pure functions, no I/O, no
  side effects, no exceptions for normal control flow.** This is what lets
  the same code run in live trading, the backtester, and the coach. If you
  add a new analysis function, it must take data in and return data out —
  never touch a network, a file, or a database.
- **Everything that talks to money or positions goes through `RiskManager`
  for entries and through `Broker`/`OrderManager` for execution.** Don't
  let the engine, the coach, or the UI call broker methods directly to open
  a position — route through the existing gatekeepers.
- **`managed_by` discipline**: AI positions (`managed_by="ai"`) are only
  ever touched by `PositionManager`. Manual positions
  (`managed_by="manual"`) are only ever touched by `OrderManager` and the
  user. Do not blur this line — it's what keeps AI Mode and Human Mode from
  interfering with each other.
- **Two independent mode axes**: `operating_mode` (ai/human) and
  `trading_mode` (conservative/high_risk/custom) must stay orthogonal.
  Never write code where switching one implicitly changes the other — see
  `config/runtime.py::RuntimeSettings._apply_mode`'s explicit preservation
  pattern and follow it for any new mode-like setting.
- **Deterministic, not ML/LLM-based.** The scorer, the gate, and the coach
  are all hand-authored weighted rule systems, chosen deliberately for
  auditability and offline operation. Don't introduce an LLM call or a
  trained model into the trading or coaching path without the user
  explicitly asking for that specific change.
- **No frontend build step.** `ui/static/index.html` is one self-contained
  file (inline `<style>`/`<script>`, no bundler, no `package.json`). The
  single exception is the vendored `ui/static/lightweight-charts.js`
  (Apache-2.0, added for V2-4's chart workspace) — committed to the repo,
  served locally, offline-capable. Don't add CDN references, more vendored
  libraries, or an npm build pipeline unless truly necessary and the user
  agrees.
- **Naming**: modules are one word/concept per file
  (`gate.py`, `orders.py`, `coach.py`), classes are the primary export
  (`TradeGate`, `OrderManager`, `TradeCoach`). Follow the existing pattern
  when adding new modules rather than inventing a new convention.

## Architecture rules

- Respect the layering in `docs/ARCHITECTURE.md` §2: `analysis/` has no
  dependents below `engine/`; `engine/` doesn't import `broker/`; `broker/`
  doesn't import `ui/`; etc. If you find yourself importing "up" the stack,
  that's a sign the code belongs somewhere else.
- New settings go in `config/settings.py` (structural, pydantic-validated,
  startup-only) unless they're meant to be changed live from the UI without
  a restart, in which case they belong in `config/runtime.py`'s
  `RuntimeSettings` overlay pattern instead.
- New broker-adjacent behavior (new order types, new position lifecycle
  events) belongs in `broker/`, follows the existing `Broker`/
  `PositionManager`/`OrderManager` split, and must be reflected in
  `Position`'s persisted fields if it needs to survive a restart.
- The orchestrator (`orchestrator.py`) is the only place that composes
  engine + risk + broker + coach + notify into a cycle. Don't build a
  second code path that duplicates `run_cycle()`'s logic (e.g. for a UI
  action) — either call into the orchestrator or add a narrowly-scoped
  method to it that the UI can call directly (see `register_manual_entry`
  for the pattern: a small, single-purpose public method on `Orchestrator`
  that the UI server calls under its lock).

## Files that should not be unnecessarily modified

- `optionspilot/core/models.py` — the shared domain vocabulary. Changing a
  field here touches persistence (SQLite schemas), the engine, the broker,
  and the UI simultaneously. Only change it when a task genuinely requires
  a new/changed field, and grep for every usage before doing so.
- `assets/optionspilot.ico` — generated by `scripts/make_icon.py`. Don't
  hand-edit the binary; regenerate via the script if the icon design needs
  to change.
- `optionspilot/data_assets/symbols.csv` — generated by `scripts/fetch_symbols.py` from
  a public NASDAQ Trader listing. Don't hand-edit; regenerate via the
  script.
- Anything under `data/` or `logs/` in a working checkout — these are
  gitignored runtime state (the user's actual paper account, journal, and
  logs). Never commit them, never treat their current contents as fixtures
  to test against (tests use `tmp_path`, not the real `data/` directory).
- `docs/CHANGELOG.md`'s existing entries — append new entries, don't rewrite
  history.

## How new features should be implemented

1. Check `docs/ROADMAP-V2.md` first — is this feature already scoped as a
   phase (V2-4/5/6)? If so, follow that phase's stated scope rather than
   improvising a different shape for it.
2. Write the backend first, test it thoroughly with `pytest` (this
   codebase's existing convention: one test file per module, e.g.
   `broker/orders.py` ↔ `tests/test_orders.py`), *then* wire the frontend.
   V2-1 through V2-3 all followed this order and it caught real bugs before
   they reached the UI.
3. If the feature touches money/positions/risk, write tests for the boundary
   conditions explicitly (empty positions, zero quantities, missing quotes,
   restart-persistence) — this codebase has a strong existing pattern of
   testing "what happens when data is missing or a component fails" (see
   `test_orders.py`'s rejection tests, `test_coach.py`'s
   `TestMissingContext`).
4. If the feature adds a new mode, setting, or toggle, decide explicitly
   whether it's structural (`config/settings.py`) or live-editable
   (`config/runtime.py`) — don't leave it ambiguous.
5. Manually verify any frontend change in a real browser before considering
   it done. `static/index.html` has **no automated test coverage** — the
   FastAPI layer is thoroughly tested via `TestClient`, but nothing drives
   the actual page. This is the single biggest coverage gap in the project;
   don't make it worse by shipping unverified UI changes.

## How documentation should be updated

After finishing a feature or a phase:
1. Update `docs/CHANGELOG.md` — append a new dated (or `[Uncommitted]`)
   section following the existing format (what was built, in prose, at the
   level of detail the existing entries use — not a raw diff summary).
2. Update `docs/PROJECT_STATE.md` — move the item from "not started"/"in
   progress" to "completed", update "exact stopping point" and "next
   recommended task" to reflect new reality. This file goes stale fastest;
   keep it honest.
3. Update `docs/TODO.md` — check off or remove completed items, add any new
   ones discovered while building.
4. Update `docs/ROADMAP-V2.md`'s checklist for the relevant phase.
5. If you touched a module described in `docs/MODULES.md` or
   `docs/ARCHITECTURE.md`, update those sections too — don't let them drift.
6. If you touched `docs/AI_HANDOFF.md`-covered ground (new API endpoints,
   new storage files, new modes, new dependencies), update that file too —
   it's meant to be a new session's *complete* orientation, and an
   incomplete one is worse than an obviously-stale one.

Do not leave documentation updates for "a future session" — do them in the
same session as the code change, before ending your turn.

## How testing should be performed

```powershell
.venv\Scripts\python -m pytest          # full suite (fast, ~10s)
.venv\Scripts\python -m pytest tests\test_orders.py   # one module
```

- All 310 tests must pass before you consider work done. If a test fails
  and you don't understand why, investigate the root cause — don't weaken
  or delete the test to make it pass.
- New backend code needs new tests in the matching `tests/test_*.py` file,
  following the existing `class Test<Thing>` / `def test_<behavior>`
  structure already used throughout.
- There is no automated frontend test suite. For UI changes, the minimum
  bar is: (a) a static check that every `$("id")` reference in
  `index.html` resolves to a real element (see the one-liner used in the
  V2-3 documentation pass, reproducible with a short Python script grepping
  `id="..."` vs `$("...")`), and (b) manual verification in an actual
  browser — start the dev server
  (`python -m optionspilot serve --port 8787 --no-loop`) and click through
  the changed flow.
- Before rebuilding the exe, run the full test suite first — don't waste a
  multi-minute PyInstaller build on code that fails its own tests.

## How commits should be written

Follow the existing style exactly — look at `git log` for real examples.
Pattern:
```
<Short imperative summary, <70 chars, no period>

<Prose paragraphs explaining WHAT was built and WHY, organized by
sub-feature if the commit spans more than one (see 0ce001d for an example
of a two-part commit body: "V2-1 (...): ..." then "V2-2 (...): ..."). Name
the key new files/classes. Mention the test count at the end of the body,
e.g. "296 tests.">

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

- One commit per coherent unit of work (a phase, or a clearly-scoped bugfix)
  — not one commit per file, not one giant commit spanning unrelated work.
- Only commit when the user asks you to, or when it's an explicit part of a
  task they've asked for (e.g. "implement and commit V2-4"). Don't commit
  speculatively.
- Never use `--no-verify`, never force-push, never amend a commit that
  might already be reflected in something the user has seen — create a new
  commit instead.
- Update `Co-Authored-By` to match whichever Claude model is running the
  session, matching the pattern above.

## Known traps (learned the hard way in this codebase)

- Terminal output capture in this environment can silently swallow pytest's
  final summary line (`N passed in X.XXs`) depending on the shell tool used.
  If you don't see a summary line, don't assume failure — check for `F`/`E`
  markers in the dot-progress output, or use `--collect-only` cross-checks,
  before concluding something is broken.
- `git status`/`git log` are the only reliable source of truth for "is this
  committed" — documentation (including this file's own history) can
  describe work that was written and tested but never committed. Always
  verify with git directly rather than trusting a doc's claim about commit
  state.
