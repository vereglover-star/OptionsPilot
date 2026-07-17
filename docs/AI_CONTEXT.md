# AI_CONTEXT.md — permanent context for AI development sessions

This is the *why* document. `AI_HANDOFF.md` is the *how it's built* orientation
(endpoints, storage, exact data flow); `ARCHITECTURE.md` is the *shape*
(diagrams, layering); `CLAUDE.md` is the *rules* (checked into the repo,
auto-loaded, overrides default behavior). This file is what should persist in
an AI assistant's understanding across sessions even after the details of any
one session are forgotten: the project's intent, its non-negotiables, and the
mistakes worth not repeating.

If you're an AI assistant new to this repository, read in this order:
**this file → `CLAUDE.md` → `AI_HANDOFF.md` → `NEXT_SESSION.md`**, then only
the source files your specific task requires.

---

## Project vision

The user's own words, preserved verbatim because they're the actual spec:
*"a polished, professional desktop trading platform that combines the best
aspects of TradingView, Webull, and Thinkorswim, while adding an AI trading
coach that can both trade autonomously in AI Mode and teach me in Human
Mode."*

Unpacked, that's four commitments, all currently true:
1. It looks and feels like a real trading platform, not a script with a GUI
   bolted on (dark brokerage-style UI, live charts, order tickets, account
   metrics).
2. It can trade completely autonomously (AI Mode) with real risk management.
3. It can also just watch and advise while a human trades manually (Human
   Mode), with every manual trade getting a genuine post-mortem — not a
   vague "good job" but an itemized, process-focused critique.
4. It's paper money. Always, until the user makes a deliberate, separate
   decision to change that.

## Long-term goals

- Finish the V2 rewrite (replay engine, journal/improvement dashboard, and
  whatever workspace-layout polish the user still wants — see `ROADMAP.md`).
- Accumulate enough paper-trading history that the learning system and the
  coach have something real to say — this is a stated gate on ever
  considering live trading, not a formality.
- Keep the "one Python process, one static HTML file" architecture as long
  as it keeps working; only reconsider it if a genuine multi-window/
  multi-monitor requirement appears (see `ARCHITECTURE.md` §13's Electron/
  Tauri discussion).

## Current architecture (one paragraph — see `ARCHITECTURE.md` for the rest)

A single Python process runs a FastAPI backend wrapped in a pywebview
native window, packaged with PyInstaller into a one-folder Windows exe. The
`Orchestrator` is the one class that composes a market-data engine, a
deterministic multi-timeframe AI decision engine, a risk manager that gates
every entry (AI or human), a paper-trading broker, a manual order book, a
deterministic post-trade coach, a SQLite journal, and a learning system that
tunes the AI's evidence weights — all behind interfaces so any piece is
swappable. The frontend is one self-contained HTML file talking to the
backend over REST + one WebSocket. No LLM appears anywhere in the trading or
coaching path.

## Design philosophy

- **Deterministic and auditable beats clever and opaque.** Every scoring
  decision, every gate verdict, every coach review is a hand-authored,
  logged, explainable rule evaluation — not a model output. This is repeated
  intentionally across `engine/scorer.py`, `engine/gate.py`, and
  `coach/coach.py`. If you're tempted to replace one of these with an LLM
  call "to make it smarter," that is exactly the kind of change that needs
  an explicit, dedicated user request first (see "Things to never change").
- **Fail closed, not fail soft.** Data failures, quote gaps, and ambiguous
  states cause an order to *not* happen, a position to hold its current
  stop, or a cycle to skip a symbol — never a guess dressed up as a
  decision.
- **Two mode axes, never conflated.** `operating_mode` (who trades) and
  `trading_mode` (how aggressive the AI's bar is) are orthogonal by
  construction. Any new mode-like setting must decide explicitly which axis
  it belongs to, or whether it's a third independent axis — never silently
  coupled to an existing one.
- **The backend and the trading logic are the product; the frontend is a
  thin, disposable view onto it.** This is why the frontend has no build
  step and no framework — every rewrite temptation there should be resisted
  unless it demonstrably serves the user, because rebuilding the view layer
  risks nothing structurally important and gains little.
- **A phase isn't done until it's tested, documented, and (for anything
  touching money/positions) live-verified.** This project's history is a
  sequence of complete, shippable increments, not a pile of half-finished
  branches. Don't leave a session with code that "should work."

## Technology choices (and why — see `ARCHITECTURE.md` §13 for the full table)

- **Python 3.12+, pandas/numpy**: the analysis library is inherently
  numeric/dataframe work; there's no reason to fight that with another
  language for the backend.
- **Dataclasses for domain models, pydantic v2 for config only**: config
  needs validation-with-good-errors at a system boundary (user-edited YAML);
  domain models don't need that overhead internally. Don't blur this split.
- **SQLite + JSON files, no external database, no ORM**: this is a
  single-user desktop app. Zero-ops storage is a feature, not a shortcut.
- **pywebview + PyInstaller, not Electron/Tauri**: evaluated and rejected
  explicitly (see `ARCHITECTURE.md`) because the backend is Python either
  way and a JS-shell rewrite would only replace window chrome at real cost
  to the existing test suite.
- **No frontend build step**: `ui/static/index.html` is one file with inline
  `<style>`/`<script>`. The only vendored asset is `lightweight-charts.js`
  (Apache-2.0). No CDN references anywhere in the codebase, ever — the app
  must work fully offline.

## Coding standards

See `CLAUDE.md`'s "Coding standards & conventions" for the authoritative,
enforced list (Python version, dataclasses-vs-pydantic split, pure-function
`analysis/` rule, `RiskManager` gatekeeper rule, `managed_by` discipline,
mode-axis orthogonality, deterministic-not-LLM rule, no-frontend-build-step
rule, module naming). This file won't restate it — read `CLAUDE.md` and
trust it as current; it is checked into the repo specifically so it can't
drift the way a `docs/` file might.

## Testing standards

- `pytest`, one test file per module (`broker/orders.py` ↔
  `tests/test_orders.py`), `class Test<Thing>` / `def test_<behavior>`.
- 345 tests as of 2026-07-17, all passing, ~13s to run the full suite.
- New backend code needs new tests in the matching file. Boundary
  conditions get explicit tests for anything touching money/positions/risk
  (empty positions, zero quantities, missing quotes, restart-persistence) —
  this codebase has a strong existing pattern here (`test_orders.py`'s
  rejection tests, `test_coach.py`'s `TestMissingContext`, `test_risk.py`'s
  `TestManualEntry`).
- **Frontend test coverage is real but shallow.** `scripts/check_html_ids.py`
  (static — every `$("id")` resolves) and `scripts/browser_check.py` (a
  real headless browser visits every tab, fails on any console error) both
  run as part of `scripts/verify.ps1`. Neither is deep per-flow regression
  coverage — any change to a specific flow (mode toggle, manual order
  placement, coach review rendering) still needs manual verification in a
  real browser (`scripts/dev.ps1`) before it's considered done.
- No CI is configured yet. `scripts/verify.ps1` is the local equivalent —
  run before committing.

## Documentation requirements

See `CLAUDE.md`'s "How documentation should be updated" for the exact
per-session checklist (`CHANGELOG.md`, `PROJECT_STATE.md`, `TODO.md`,
`ROADMAP-V2.md`, module docs, `AI_HANDOFF.md`). As of 2026-07-17 that
checklist also covers this file's siblings — see `CONTRIBUTING.md`
"Documentation requirements" for the full current list, since it now spans
more files than fit comfortably in `CLAUDE.md`'s original checklist.
Documentation updates happen in the same session as the code change they
describe, never deferred to "a future session."

## Future desktop plans

Windows-only today (pywebview's WebView2 backend, PyInstaller `--windowed`
build). No macOS/Linux build has been attempted; pywebview does support
other backends (GTK/Qt on Linux, Cocoa on macOS) so a cross-platform build
is plausible if ever requested, but it is unscoped, untested, and not a
current priority. No multi-window or multi-monitor workspace exists; if
that becomes a real requirement, `ARCHITECTURE.md` already flags Tauri as
the preferred alternative to revisit the Electron/Tauri decision with.

## Future mobile plans

None exist, and none are anticipated in the current roadmap. The analysis
engine is pandas/numpy-heavy (not resource-light), the UI assumes a
desktop-sized viewport with dense tables and multi-pane layouts, and the
whole point of the architecture (one local process, an embedded broker
simulator, local SQLite) doesn't map cleanly onto a mobile app's
sandboxing and connectivity model. A hypothetical mobile client would more
realistically be a thin remote view onto a server-hosted OptionsPilot
instance than a port of the desktop app — that would be a different
project, not a phase of this one.

## Current limitations

See `ARCHITECTURE.md` §16 and `PROJECT_STATUS.md` "Known limitations" for
the full, current list (data delay/quality, no historical option chains,
no intrabar order simulation, no browser test coverage, etc.). All of these
are documented *because they were deliberate trade-offs*, not because
nobody noticed — don't "fix" one without understanding why it was accepted
in the first place.

## Technical debt

- **Frontend coverage is shallow, not absent** (see "Testing standards"
  above) — `browser_check.py` proves every tab loads cleanly, not that any
  specific interaction still works. Extending it with deep per-flow checks
  is the single remaining highest-leverage gap; see `TODO.md`.
- No linting, formatting, or type-checking tooling is configured
  (`pyproject.toml` has no `[tool.ruff]`/`[tool.black]`/`[tool.mypy]`
  section, no `.pre-commit-config.yaml`). The codebase is consistently
  styled by convention/discipline, not by tooling — this has held up so
  far but is worth watching as the codebase grows. See `CONTRIBUTING.md`
  "Automation: what's implemented vs. still just recommended" — this one
  is still just recommended.
- No CI is configured. `scripts/verify.ps1` exists specifically so a human
  running it locally is (currently) the only gate — see the same
  "Automation" section for the recommended, not-yet-applied CI scope.
- `optionspilot_app.py`'s `OptionsPilot.spec` is PyInstaller-generated and
  gitignored, which is correct, but means the exact PyInstaller invocation
  lives only in `scripts/build_exe.ps1` — if that script and the spec ever
  drift, there's no test catching it (only manual smoke-testing via
  `scripts/build.ps1`, which wraps it but doesn't launch/exercise the
  resulting exe).
- See `TODO.md` for the live, itemized backlog — this section is the
  narrative context for *why* those items matter, not a duplicate list.

## Files that should be read first

For a new AI session with no prior context on this repo, in order:
1. This file (`AI_CONTEXT.md`).
2. `CLAUDE.md` — the enforced rules.
3. `AI_HANDOFF.md` — full technical orientation (endpoints, storage layout,
   exact behavioral contracts).
4. `NEXT_SESSION.md` — what to actually do right now.
5. Only then, source files specific to the task — `ARCHITECTURE.md` and
   `MODULES.md` exist precisely so a session doesn't need to read the whole
   codebase to get oriented; use them as a map, not a substitute for
   reading the specific files a task touches.

## Things future AI assistants should never change without careful review

This is a curated subset of `CLAUDE.md`'s rules — the ones with the highest
cost if gotten wrong, restated here because they're the ones worth burning
into long-term memory:

1. **Never implement a real order-placing broker adapter** unless the user
   explicitly and directly asks for it in a dedicated request. Not implied
   by "make it better," "add more brokers," or any refactor. If a task
   seems to require this, stop and ask for confirmation before writing any
   code.
2. **Never weaken the `broker.live_trading_enabled` /
   `broker.i_understand_the_risks` double-gate** in `config/settings.py`,
   and never add a code path that could place a real order without both
   flags AND a real adapter existing (which currently doesn't exist at
   all — `broker/registry.py`'s non-paper entries are stubs that raise).
3. **Never introduce an LLM call into the trading or coaching path**
   (`engine/scorer.py`, `engine/gate.py`, `coach/coach.py`) without the
   user explicitly asking for that specific change. Determinism and
   auditability here are a stated design decision, not an accident of
   what was easy to build first.
4. **Never edit `optionspilot/core/models.py` casually.** It's the shared
   domain vocabulary; a field change touches persistence (SQLite schemas),
   the engine, the broker, and the UI simultaneously. Grep for every usage
   before changing anything here.
5. **Never hand-edit generated/binary assets**: `assets/optionspilot.ico`
   (regenerate via `scripts/make_icon.py`), `optionspilot/data_assets/symbols.csv`
   (regenerate via `scripts/fetch_symbols.py`), `OptionsPilot.spec`
   (PyInstaller-generated, gitignored).
6. **Never treat `data/` or `logs/` in a working checkout as fixtures.**
   They're gitignored runtime state — the user's actual paper account,
   journal, and logs. Tests use `tmp_path`; verification uses scratch data
   directories, never the real one.
7. **Never rewrite `docs/CHANGELOG.md`'s existing entries.** Append new
   ones; history is append-only.
8. **Never build a second code path that duplicates
   `Orchestrator.run_cycle()`'s logic** for a UI action. Either call into
   the orchestrator or add a narrowly-scoped public method to it (the
   `register_manual_entry`/`approve_manual_entry` pattern).

## Common mistakes to avoid

Lessons the project has actually hit, not hypothetical ones:

- **A halted account bypassing risk gates via a code path that "looks"
  gated but isn't wired up.** Concretely: on 2026-07-16 a session added
  `RiskManager.approve_manual_entry` and `OrderManager.evaluate`'s
  fill-time approval callback, but never actually called them from the
  immediate market-buy path in `UIServer.place_order` — so a halted
  account could still place an instant manual buy. The fix (2026-07-17)
  wasn't just wiring the call; it was also realizing the inherited hard
  %-risk sizing veto was too strict for manual trades (it blocked nearly
  all manual buys at default settings) and converting it to advisory-only.
  **Lesson: adding a gate function is not the same as the gate being
  active — verify the call site, not just the function's existence, and
  write the endpoint-level test that would have caught it (see
  `tests/test_ui_server.py::test_market_buy_respects_risk_halt`).**
- **`git status` can print "working tree clean" while `git diff --stat`
  shows real dirt.** Happened in this exact repo on 2026-07-17 — a whole
  prior session's uncommitted work was nearly misdiagnosed as already
  committed. Cross-check `git status` with `git diff --stat` (and the
  session-start git snapshot, if one was provided) before trusting either.
  This is the same output-capture trap `CLAUDE.md` documents for pytest,
  applied to git.
- **Terminal output capture can silently swallow a command's final summary
  line** (pytest's `N passed in X.XXs`, and apparently git's status too).
  Don't assume failure just because you didn't see the expected summary —
  check for explicit failure markers before concluding something broke.
- **Trusting a doc's claim about commit state instead of `git log`.**
  Documentation (including this file's own history) can describe work that
  was written and tested but never actually committed. `git status`/
  `git log` are the only reliable source of truth for "is this committed."
- **`$ErrorActionPreference = "Stop"` plus `2>&1` on a native command can
  turn a benign stderr line into a fatal error.** Happened writing
  `scripts/_common.ps1` on 2026-07-17: piping a script's own invocation
  through `2>&1` (to combine stdout/stderr for a tool call) made
  PowerShell wrap `pip`'s routine "a new version is available" notice as a
  terminating `NativeCommandError`, even though `pip` itself exited 0. Not
  a bug in the script — a bug in how it was invoked. Don't `2>&1` a
  PowerShell script or native command unless you specifically need merged
  streams; if a native call fails mysteriously under `-Stop`, try it
  without any stderr redirection before assuming the command itself is
  broken.
- **A subprocess exiting doesn't mean its file handles are released yet on
  Windows.** `scripts/browser_check.py`'s first real run left two scratch
  temp directories behind despite an explicit `shutil.rmtree(..., 
  ignore_errors=True)` in a `finally` block after `server.wait()`
  returned — a SQLite or log file handle was still closing asynchronously.
  Fixed with a short retry loop instead of swallowing the error silently.
  **Lesson: `ignore_errors=True` (or bare `except: pass`) on cleanup code
  hides exactly the kind of leak this note is about — prefer a bounded
  retry with a visible warning on final failure**, especially for anything
  spawning a child process on Windows.

## Current UI philosophy

Dark, dense, brokerage-style — closer to a real trading terminal than a
typical SaaS dashboard. Tabular numerals everywhere numbers appear side by
side. Skeleton loaders over blank states during fetches. DOM writes are
diffed so unchanged sections never re-render (`setHTML` helper) — this
matters because the WebSocket pushes on a ~1s cadence and naive re-renders
at that frequency would be visibly janky. Keyboard shortcuts (1–9 for tabs,
F for chart fullscreen, Esc to cancel an armed drawing tool) are a
first-class citizen, not an afterthought. Confirmation modals gate anything
that places or closes a real (paper) position. Accessibility basics
(visible focus rings, aria-labels on icon-only controls) are expected, not
optional, on new UI surfaces.

## Current performance goals

The scan cycle is the core performance-sensitive path (it runs on every
watchlist symbol every ~60s, and synchronously on "Scan now"). Current
measured baseline (2026-07-16 performance pass): **~4.5s cold, ~0.1s warm**
for a 5-symbol watchlist, via a caching/dedup layer (`CachedProvider`),
parallel candle fetching, and per-(symbol, timeframe) analyzer memoization
on a data fingerprint. `/api/scan` is non-blocking by default; the UI never
blocks on a running scan. Any change to the analysis or engine layer that
risks reintroducing serial, uncached, or unmemoized work on the hot path
should be profiled before being called done — the soak harness
(`scripts/soak.py`) exists specifically to catch cycle-time and heap-growth
regressions over repeated cycles.
