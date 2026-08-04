# CLAUDE.md — instructions for Claude Code sessions working on OptionsPilot

This file is permanent guidance for any Claude Code session touching this
repository. Read it before making changes. It does not replace `docs/` —
see `docs/AI_CONTEXT.md` for the project's vision/philosophy/standards,
`docs/AI_HANDOFF.md` for technical architecture orientation, and
`docs/NEXT_SESSION.md` for what to do next.

## Before you do anything

1. Read `docs/AI_CONTEXT.md` — vision, design philosophy, standards, and
   the curated list of things never to change without careful review.
2. Read `docs/AI_HANDOFF.md` in full — complete technical orientation
   (endpoints, storage layout, exact behavioral contracts).
3. Read `docs/NEXT_SESSION.md` to find out exactly where the last session
   stopped and what's recommended next. `docs/PROJECT_STATE.md` has the
   full narrative if more detail is needed; `docs/PROJECT_STATUS.md` has a
   structured snapshot (test count, milestones, known bugs).
4. Run `git log --oneline -10` and `git status` **and** `git diff --stat`
   yourself — documentation can go stale between sessions, and `git
   status` alone has previously reported "clean" while `git diff --stat`
   showed real uncommitted changes (see `docs/AI_CONTEXT.md` "Common
   mistakes to avoid"). Verify before trusting either. Then run
   `.\scripts\verify.ps1` once to confirm the environment and baseline are
   actually green before you start changing anything (see
   `docs/QUICK_START.md` if you need the minimal path to a working setup).
5. Only read source files you actually need for the task at hand. The docs
   above exist specifically so you don't have to read the whole codebase to
   get oriented — `docs/ARCHITECTURE.md` (system design + diagrams) and
   `docs/MODULES.md` (per-module API map) are the reference to reach for
   once you know which area a task touches.

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
- `optionspilot/data/capabilities.py` — the *measured* per-provider history
  depth table. It is not a tuning knob: the numbers came from
  `scripts/marketdata_probe.py` walking each interval back until the provider
  refused, and `tests/test_capabilities.py` asserts them. Re-measure, don't
  guess.
- `optionspilot/data/credentials.py` — **the only module in the codebase that
  holds a secret.** A plaintext API key leaves it through exactly one method
  (`resolve()`), and every other accessor returns a mask. Do not add a
  `redact=False` parameter, do not add a second place a key can be written, and
  do not import it from anything that serialises a payload. If you add a new
  export, diagnostics endpoint or report, add it to
  `tests/test_credentials.py::TestNoLeak` **before** shipping it — that test is
  the enumeration of everything users are invited to attach to a public bug
  report, and it is the only thing standing between a new payload and a leaked
  key.
- `optionspilot/intelligence/` — the Trading Intelligence Engine (V0.6.0). Two
  rules govern changes here, and both are enforced by tests. **It imports `core`
  only**: it reads journal/experience/coach records *structurally*, never by
  import, which keeps it BELOW the coach so the coach can become a presentation
  layer over it (`tests/test_architecture.py`). And **it never states what it
  cannot evidence** — see the "insufficient evidence" trap below and
  `docs/TRADING_INTELLIGENCE.md` §4. If you add an engine, a metric, a behaviour,
  a dimension or a lesson, read that §4 first; each of its rules exists because
  the naive alternative shipped something confidently wrong.
- `optionspilot/services/guide.py` — the guided-onboarding domain layer (V0.6.1;
  moved out of `ui/` in V0.9.2-C6). Two
  rules govern it, both enforced by tests. **It shares only IDS with the
  frontend**: a `Recommendation` names a tutorial by id and the title comes from
  `GUIDE_TUTORIALS` in `index.html` at render time, because two catalogues
  holding the same prose is a second place tracking one fact (`data/health.py`
  V0.5.3, the settings ranking V0.5.7). Both drift failures are silent and look
  implemented, so `tests/test_guide.py::TestCatalogueContract` asserts the id
  sets match **in both directions**. And **it recommends TUTORIALS from FEATURE
  usage, never trading behaviour** — that is `intelligence/`'s job, done from the
  trade record with a false-discovery correction underneath it. Read
  `docs/ONBOARDING.md` §7 before adding a rule.
- `optionspilot/services/` — the platform-independent application layer (V0.7.0).
  Two rules, both enforced by `tests/test_architecture.py`, and the second is the
  one that matters. It **must not import `ui/`** — and it **must not import
  FastAPI, Starlette, uvicorn, pywebview or any other transport package at all**,
  because a service that is free of `optionspilot.ui` but imports `fastapi` for a
  response model still forces a CLI, a test or a future mobile backend to pull a
  web server in to compute a win rate. Every service takes **injected,
  duck-typed** collaborators and returns **frozen view models of primitives**; a
  view model holds no domain object and computes nothing. If you are about to put
  a decision about *what a client is shown* into a route handler, it belongs
  here. Read `docs/ARCHITECTURE-PLATFORM.md` §2 first.
- `optionspilot/host/` — everything the app needs from the machine under it
  (V0.7.0). Core-only and transport-free. `capabilities.py` is **data**: a
  `HostProfile` per target including three (`web`, `ios`, `android`) that do not
  exist and are marked `implemented=False`, each missing capability carrying a
  stated reason. Do not grant a capability to an unimplemented profile to make
  something convenient — `ios` lacking `BIND_LISTENER` is where the entire
  desktop-as-host model comes from. `adapter.py` is **behaviour**; no host call
  may raise, because every one sits where an OS refusal is a normal state.
- `optionspilot/services/sync.py` — the classified inventory of every durable
  object. **It syncs nothing and must not start.** Adding a store means adding an
  entry (a test fails otherwise), and the policy is a decision, not a formality:
  `APPEND_ONLY` on `journal.db` because last-write-wins would silently delete a
  device's trades, `SINGLE_WRITER` on `paper.db` because two writers is the
  corruption the instance lock already prevents on one machine, and `NEVER` on
  `data/credentials.json`, which is a prohibition rather than a strategy.
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

After finishing a feature or a phase (full checklist and rationale in
`docs/CONTRIBUTING.md` "Documentation requirements" — this is the short
version):
1. Update `docs/CHANGELOG.md` — append a new dated (or `[Uncommitted]`)
   section following the existing format (what was built, in prose, at the
   level of detail the existing entries use — not a raw diff summary).
2. Update `docs/PROJECT_STATE.md` — move the item from "not started"/"in
   progress" to "completed", update "exact stopping point" and "next
   recommended task" to reflect new reality. This file goes stale fastest;
   keep it honest.
3. Update `docs/PROJECT_STATUS.md` — the structured fields that changed
   (test count, completed milestones, known bugs, current priorities).
4. Rewrite `docs/NEXT_SESSION.md` to reflect the new handoff state — it
   should never be more than one session stale.
5. Update `docs/TODO.md` — check off or remove completed items, add any new
   ones discovered while building.
6. Update `docs/ROADMAP.md` and, for V2-scope work, `docs/ROADMAP-V2.md`'s
   checklist for the relevant phase.
7. If you touched a module described in `docs/MODULES.md` or
   `docs/ARCHITECTURE.md`, update those sections too — don't let them drift.
8. If you touched `docs/AI_HANDOFF.md`-covered ground (new API endpoints,
   new storage files, new modes, new dependencies), update that file too —
   it's meant to be a new session's *complete* orientation, and an
   incomplete one is worse than an obviously-stale one.
9. If something durable about the project's philosophy, standards, or
   things-never-to-change list changed, update `docs/AI_CONTEXT.md` too.

Do not leave documentation updates for "a future session" — do them in the
same session as the code change, before ending your turn.

## How testing should be performed

```powershell
.\scripts\test.ps1                    # full suite (~80s), explicit PASS/FAIL
.\scripts\test.ps1 tests\test_orders.py   # one module
.\scripts\verify.ps1                  # tests + HTML ids + docs + pip check + browser suites
```

`scripts/test.ps1`/`verify.ps1` are wrappers, not a different testing
system — they ensure the venv/deps, run `pytest`, and print an explicit
`TESTS: PASS`/`TESTS: FAIL` line derived from pytest's exit code (not from
parsing its printed summary, which output capture has swallowed before —
see "Known traps"). The raw command still works identically:
`.venv\Scripts\python -m pytest`.

- The full suite must pass (100% green — `docs/PROJECT_STATUS.md` has the
  current count, or just run it) before you consider work done. If a test
  fails and you don't understand why, investigate the root cause — don't
  weaken or delete the test to make it pass.
- New backend code needs new tests in the matching `tests/test_*.py` file,
  following the existing `class Test<Thing>` / `def test_<behavior>`
  structure already used throughout.
- Frontend coverage is real but shallow: `scripts/check_html_ids.py`
  (static — every `$("id")` reference resolves), `scripts/browser_check.py`
  (a real headless browser visits every tab, fails on any console error),
  `scripts/chart_check.py` (65 chart/drawing/history regressions),
  `scripts/marketdata_check.py` (46 checks over Settings ▸ Market data — key
  management, ordering, maintenance, quota display, accessibility and secret
  redaction, entirely offline), `scripts/guide_check.py` (135 checks over the
  guided onboarding, contextual help, glossary, help search, empty states,
  accessibility and the order-ticket guardrails — its spotlight assertions test
  that the highlight *intersects* the element it names and that the card is not
  covering it, not that a step declared a target; the ONE stubbed thing is
  `/api/chain`, because a guardrail cannot be exercised without an option chain),
  `scripts/workspace_check.py` (21 checks over the server-owned workspace — the
  canonical one wipes `localStorage` in a real browser, reloads, and asserts that
  the symbol and timeframe coming back are the ones ON SCREEN, because the claim
  the feature makes is about surviving the loss of *client* storage and no Python
  test can reach that) and `scripts/intelligence_check.py` (54 checks
  over the Trading Intelligence UI against a seeded, deliberately flawed
  history — score cards, working "Why?" disclosures, unassessable-behaviour
  reasons, goals, the per-trade journal analysis and lesson triggers, also
  entirely offline) all run as part of `.\scripts\verify.ps1`.
  None is deep per-flow coverage — for any change to a specific flow,
  still verify it by hand in a real browser
  (`.\scripts\dev.ps1`, or `python -m optionspilot serve --port 8787 --no-loop`
  directly) before calling it done.
- `.\scripts\build.ps1` runs the full test suite first and refuses to
  invoke PyInstaller on a red suite — this is the enforcement mechanism
  for the rule immediately below, not a separate rule.
- Before rebuilding the exe, run the full test suite first — don't waste a
  multi-minute PyInstaller build on code that fails its own tests.

## How releases are made

**One command.** Do not hand-type `git tag` or `git push` for a release.

```powershell
.\scripts\release.ps1 0.9.3 -DryRun   # every check, modifies nothing
.\scripts\release.ps1 0.9.3           # release
```

Preflight → version bump → `check_docs.py` + `verify.ps1` → `Release vX.Y.Z`
commit + annotated tag → push → watch `release.yml` and report the Release URL
and artifacts, or the exact failing job and step. Anything failing **before the
push** rolls the repository back to exactly where it started; after the push
the rollback is disarmed deliberately, because undoing published history means
a force-push. Full guide: `docs/RELEASE.md`.

Two things the script will not do for you, both deliberate:
- **Write the `docs/CHANGELOG.md` entry.** Preflight *refuses* to release
  without a `## ` section naming the version, because that section becomes the
  published release notes. What it says is a judgement, not a computation.
- **Smoke-test the built exe.** Nothing in CI launches the packaged pywebview
  window or exercises the PyInstaller-specific paths.

If you add anything that holds a literal copy of the version, add a row to
`scripts/lib/release_support.py::LOCATIONS` — that table is the single place
the release knows what to write, and a location whose pattern stops matching is
a hard error rather than a silent skip. Everything else (pyproject, the UI
display, the installer, the zip name, the tag, the release notes) derives from
`optionspilot.__version__` and must stay that way.

If you change the release path itself: `scripts/release.ps1` must make **no git
call of its own** (they all live in `scripts/lib/ReleaseGit.ps1`), and any
decision with an edge case belongs in `scripts/lib/release_support.py` where
pytest can reach it. `tests/test_release_automation.py` enforces both, plus
phase order, rollback registration, and the absence of `--force`/`--no-verify`.

## How commits should be written

Follow the existing style exactly — look at `git log` for real examples.
Pattern:
```
<Short imperative summary, <70 chars, no period>

<Prose paragraphs explaining WHAT was built and WHY, organized by
sub-feature if the commit spans more than one (see 0ce001d for an example
of a two-part commit body: "V2-1 (...): ..." then "V2-2 (...): ..."). Name
the key new files/classes. Mention the current test count at the end of
the body (run the suite to get it — don't guess or copy an old number).>


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
  state. This applies to `git status` itself, too: it has previously
  printed "working tree clean" here while `git diff --stat` showed real
  uncommitted changes. Cross-check both before trusting either (full story
  in `docs/AI_CONTEXT.md` "Common mistakes to avoid").
- Adding a risk/validation gate function is not the same as it being
  active. A gate was once added (`RiskManager.approve_manual_entry`) but
  never actually called from the code path it was meant to protect, so it
  did nothing until a later session wired it up. When adding a gate, verify
  the call site with a test that would fail if the gate weren't wired in —
  not just a unit test of the gate function in isolation.
- **An empty DataFrame is not an error signal.** The market-data layer spent
  years unable to tell "there is no data" from "I can't reach the data" from
  "this data cannot exist", because `yfinance` returns an empty frame for all
  three. Every chart-history bug in this repo descends from that. Since V0.5.2
  adapters **raise typed `ProviderError`s** and the empty frame means only
  "this window genuinely holds no bars". Do not collapse them back together;
  read `docs/MARKET_DATA.md` §1–2 before touching `optionspilot/data/`.
- **A provider's history depth is measured from `now`, not from the request's
  end.** Yahoo's own 422 says "must be within the last 60 days" — from now. The
  old clamp measured from the request's end, so a scroll-back whose start was 62
  days old but whose end was 31 days old passed unclamped and 422'd every time.
  Depth lives in `data/capabilities.py` only; re-measure with
  `scripts/marketdata_probe.py` rather than adjusting a number by feel.
- **A cache keyed by `(symbol, timeframe)` cannot also hold a past-ending
  window.** A history-paging request overwrote the live-window memo under the
  same key, and the next live load rendered the sliced overlap — QQQ 1d came
  back as ONE candle from nine months earlier, with `outcome: memo` and no error
  anywhere. Only live-window requests may use that memo.
- **`scripts/chart_check.py` is worth running even when you think you haven't
  touched the chart.** It was silently failing at check 12 for several sessions
  before V0.5.2, and running it is what surfaced the memo bug above — a real
  data-layer defect that no unit test and no amount of clicking had found.
- **A provider's timestamps are not UTC until you have checked.** Twelve Data
  and Alpha Vantage send **naive local time in the exchange's timezone**.
  Reading it as UTC shifts every intraday bar by 4–5 hours and by a *different*
  amount either side of a DST boundary. The symptom is not a broken chart, it
  is a subtly wrong one — and because the cache is keyed `(symbol, timeframe,
  ts)`, the wrong bars also collide with the right ones already stored.
  `http_adapter.localize` is the ONE place this is handled, and daily+ bars
  must stamp at 00:00 UTC to match Yahoo and Stooq or every cached day gets a
  duplicate row.
- **A capability table is a promise about what can be SERVED, not about what a
  provider supports on paper.** `deepest_earliest` originally counted every
  provider that declared an interval — so a keyless Finnhub, which can never
  answer, still contributed its 180-day 5-minute floor and would have told the
  chart history reached back three times further than anything reachable. That
  is the retry-forever bug class V0.5.2 was built to eliminate. Permanently
  unusable providers (no key, disabled) contribute no floor; temporarily
  unavailable ones (breaker open, rate limited) still do.
- **Anything that can be exported must redact secrets by default.**
  `ProviderConfig.as_dict()` returns `***` for `api_key` unless explicitly
  asked otherwise, because that dict reaches the diagnostics endpoint, the JSON
  export and the text report — all of which users are invited to attach to
  public bug reports. Opt-in redaction would mean every future caller has to
  remember; default redaction means a leak requires someone to try.
- **Reacting to a rate-limit error is too late when the budget is 25/day.**
  Alpha Vantage's free tier is spent by one symbol-switching session. Budgets
  are checked BEFORE the request (`data/ratelimit.py`), counted before the call
  (a failed request still consumed quota upstream), and persisted across
  restarts (an in-memory counter would appear to grant a fresh allowance every
  launch).
- **A counter that two objects both maintain will drift, and the drift will
  hide bugs.** Provider health lived in `adapter.ProviderHealth` (counters) and
  `registry._Breaker` (rotation), with the breaker's trip condition being a read
  of the adapter's counter. Consolidating them into
  `data/health.py::ProviderHealthMonitor` in V0.5.3 immediately exposed two real
  defects that had shipped in V0.5.2: a provider answering every request with
  *unusable* bars was recorded by the adapter as a **success** (the service's
  validation reject was counted nowhere), so it never tripped its breaker and
  held the head of the chain forever; and once that was fixed, the demotion
  could only ever reach a streak of 1, because recording the success had already
  zeroed the streak. If you add a second place that tracks the same fact, expect
  the same class of bug.
- **When you reclassify an outcome, move the counters — don't add new ones.**
  The adapter records a success as soon as the transport parses; the service may
  then reject the bars. Recording a fresh failure there would make one upstream
  call count as two requests and halve every provider's apparent failure rate.
  `demote_last_success` exists for exactly this.
- **A lifetime failure rate never decays.** Ranking on one meant five failures
  during a two-minute outage kept a provider demoted for thousands of later
  requests. Anything that feeds a "how healthy is this right now" decision wants
  a bounded window (`health.OUTCOME_WINDOW`), not a lifetime total. Lifetime
  totals are still the right thing to *report*.
- **`config/` and `data/` may not import each other**, and
  `tests/test_architecture.py` enforces it in both directions — including
  imports inside a function body, which the AST walker still sees. A pydantic
  config section that needs a runtime counterpart in `data/` is translated in
  `orchestrator.py` (the composition root that already imports both), with tests
  asserting the two key sets are identical so a field added to one and forgotten
  in the other fails the suite.
- **"The data arrived" is not "the user can see it."** Every test in this repo
  — 1232 of them, plus 42 headless-browser checks and a whole diagnostics
  subsystem — asked whether the payload was correct. None asked whether the
  candles were on screen. lightweight-charts turns `autoScale` **off
  permanently** the first time the user drags the right-hand price axis, and
  nothing restored it, so one drag pinned the price band for the rest of the
  session: every later symbol whose price fell outside it rendered its candles
  off-screen while the volume histogram (its own `vol` scale) kept painting.
  Backend healthy, `CH.data` perfect, console clean, `data-ch-state="complete"`,
  canvas empty — and a restart "fixed" it because `autoScale` isn't persisted.
  The price axis now has an owner exactly like the time axis
  (`chAutoScalePrice` / `chEnsurePriceVisible`), and `chart_check` 43–48 assert
  the invariant: **the candles in the visible time window must intersect the
  visible PRICE window.** Assert what the user sees, not what the code computed.
- **A bar with null OHLC is not an error to lightweight-charts — it is
  WHITESPACE.** `setData` accepts it, the series draws nothing, the price scale
  ignores it, and the render reports success. Any display-side sanitizer must
  check the *values*, not just the bar times (`chBarUsable`); and a payload that
  sanitizes down to nothing is a FAILURE state, never a blank canvas reporting
  `complete`. For the same reason, an out-of-order payload must be **sorted**,
  not filtered — the old "keep only bars newer than the last kept one" scan
  turned a reversed response into a single candle and called it success.
- **One anomalous bar must not condemn a frame.** Interval conformance was
  judged on the strict TIGHTEST gap, and Yahoo closes every US session with a
  30-minute stub bar (15:30 → 16:00 ET, the closing auction) — so one 0.5-interval
  gap among 1,862 exact ones marked every 1h frame "wrong interval served",
  scored it 0, and charged the provider a validation failure. Judge a
  distribution on its **median**, not its extreme: a genuinely wrong interval is
  wrong in the bulk of its spacings. Keep the extreme as a reported statistic.
- **A daily bar's identity is its SESSION DATE, and a date is not an instant.**
  Every adapter used to stamp daily+ bars wherever its upstream put them —
  Yahoo at the 09:30 ET session open (13:30 UTC), yfinance at exchange midnight
  (04:00 UTC), Stooq and the keyed HTTP providers at 00:00 UTC. The cache is
  keyed `(symbol, timeframe, ts)`, so those are three rows for one trading day:
  measured, SPY held **6,517 daily rows for ~3,258 trading days**, the frame's
  tightest spacing read 0.40 intervals, `validate_history` correctly refused it
  as "wrong interval served", and **every symbol on 1D was stuck behind a
  validation screen**. `base.session_index` is now the ONE convention (00:00
  America/New_York, applied in `HistoryAdapter.fetch_history`), and
  `cache._migration_3` repairs installs already poisoned. Exchange midnight and
  not UTC midnight, because the chart labels every timestamp through an ET
  formatter — 00:00 UTC reads as 19:00 the previous day.
- **A tier that cannot answer must DECLINE, not abort the ladder.** Validation
  used to run in `service._settle` — after the ladder had already committed to
  the disk tier — so an unusable cache became `outcome=failed` with the
  providers already behind it and the bad rows still on disk. Retry repeated it
  forever. Disk tiers now validate *before* committing (`_validated`), and on
  failure `_quarantine` purges the rows and the ladder falls through. If you add
  a tier, validate inside it.
- **Nothing said what a LEGAL viewport is.** "One owner" (V3.2.2) said where a
  move comes from, not what it may leave on screen — so `chScrollToLatest`
  carried the previous symbol's zoom onto a new one, `chApplyFocal` ratcheted
  narrower on every step up the timeframe ladder, and a resize left 4 bars of
  281 visible. The invariants (V1–V6) now live in `chClampViewport`, called from
  `chMoveViewport`, with `CH_MIN_VISIBLE_BARS` as the single floor constant —
  `chApplyFocal` references it rather than keeping its own. They bind
  PROGRAMMATIC moves only; a user's wheel-zoom is never clamped.
- **`CH.restoringViewport` is a DEPTH COUNTER, not a boolean.** Guarded moves
  overlap (a ResizeObserver fires mid-layout while a render's move is still
  settling), and a boolean let the first `finally` clear the guard while another
  move was in flight — the next range change was read as a user pan and fired a
  spurious history fetch.
- **Do not re-clamp the viewport from the ResizeObserver.** It was tried and
  reverted: dragging the price axis changes the width of its own labels, so the
  canvas resizes a few pixels mid-gesture, and clamping there re-invalidated the
  chart and snapped the user's manual price scale back (caught by chart_check's
  "overlay tracks a vertical price-axis drag"). The narrow view a resize can
  leave is self-correcting on the next real viewport move; manual price scaling
  is not worth trading for it.
- **Stooq is dead (2026-07-27)** — it answers every request with a JavaScript
  proof-of-work challenge. Do not try to defeat it; the adapter correctly
  refuses. The consequence matters more than the cause: **with no API keys the
  app has exactly one real source (Yahoo, via two code paths sharing one
  upstream)**, and Yahoo rate-limits by IP. Don't reason about failover as if
  there were a keyless fallback — there isn't one.
- **A provider that is not CONSTRUCTED cannot explain itself.** `enabled:
  false` used to skip construction entirely, which read as tidy and left the
  settings page with a permanent blind spot exactly where a user needs to act:
  a switched-off provider could not be listed, could not say why it was absent,
  and could not be switched back on without editing a file and restarting.
  Since V0.5.7 it is built and benched, exactly like one with a missing API
  key. The invariant that matters is downstream: a benched provider must
  contribute **no `deepest_earliest` history floor**, or it tells the chart
  that history reaches further back than anything reachable — the retry-forever
  class V0.5.2 was built to eliminate.
- **A settings page that re-renders on a timer will eat what the user is
  typing.** The market-data panel polls every five seconds; without an explicit
  guard, an auto-refresh landing mid-paste wipes the API-key box, and the user
  has no idea why. Two rules, both asserted by `scripts/marketdata_check.py`:
  the poll is skipped entirely while focus is inside the panel, and half-typed
  values are captured before the `innerHTML` that destroys them and restored
  after. The same page must also stop polling when its tab is not visible — a
  settings screen fetching from a background tab becomes a meaningful share of
  the traffic in the system it is reporting on, and on a metered provider
  traffic is budget.
- **A gate on the route is not a gate on the capability.** The QA endpoints
  404 unless `market_data.qa_mode` is set, and every `MarketDataControl.qa_*`
  method *also* checks it. That is not belt-and-braces for its own sake: the
  next caller (a script, a test, a second endpoint) will reach the method
  directly, and a check that lives only in one route is a check that will be
  bypassed by the second one. Gate the capability, not just the door to it.
  Related: return **404, not 403** — a 403 confirms the endpoint exists, which
  is a small thing to hand an unattended local HTTP server.
- **Two objects tracking one fact will drift — including in the UI.** V0.5.3
  learned this for provider health; V0.5.7 had to keep learning it. The
  settings page does not compute a ranking, it renders `registry.ranking()`
  verbatim, because a page that derived its own order would eventually disagree
  with the chart about which provider goes first, and that disagreement is
  undebuggable from either side. Similarly `monitor.health_state()` is
  **derived on every read and stored nowhere**.
- **When a user reorders providers, priorities must stay spaced 10 apart.**
  The rank formula is calibrated so 10 rank points equals one second of latency
  (`health.LATENCY_MS_PER_RANK_POINT`). Rewriting the order as 1, 2, 3 would
  mean a provider 100 ms slower than its neighbour outranked it — dynamic
  ordering would silently collapse to almost-static the first time someone
  pressed Move Up, and nothing would report an error.
- **A preferences file a user can open is a file a user will edit badly.**
  `apply_control_state` was written tolerantly and still crashed the whole app
  at startup on `{"providers": [1,2]}` — `[].items()` raising out of the
  composition root. `or {}` is not a type check. Anything read from
  `<data>/*.json` at startup must validate the *shape* of every field, and the
  failure mode must be "you lose your preferences", never "the app will not
  start". `tests/test_marketdata_control.py::TestPersistence` parametrises the
  shapes.
- **HTTP 401 and 403 are different failures, and conflating them names the
  wrong cause with total confidence.** `http_adapter._from_status` mapped both
  to `ProviderAuthError` ("the API key was rejected"). Finnhub has moved
  `/stock/candle` to its paid tiers, so a brand-new verified free key
  authenticates and is then refused with **403** — and the app told the user
  their key was bad. They regenerated it. Repeatedly. It could never have
  worked. Measured live: Finnhub answers an invalid key with **401**
  `{"error":"Invalid API key."}` and a valid-but-unentitled one with **403**
  `{"error":"You don't have access to this resource."}`, so **401 is the only
  status it uses for a key problem and a 403 is positive evidence the key is
  good**. `ProviderEntitlementError` is deliberately NOT a subclass of
  `ProviderAuthError` so an `except` cannot re-merge them. The general lesson is
  bigger than Finnhub: a diagnostic that confidently names a *wrong but
  actionable* cause is worse than one that says "I don't know", because the
  user will act on it. Full account: `docs/MARKET_DATA.md` §41.
- **"Permanently unusable" is not the same as "unavailable", and only the
  former may be excluded from the history floor.** `registry.deepest_earliest`
  skipped `disabled_reason` only, so a provider with a *rejected key* or an
  *insufficient plan* still contributed its declared depth. Finnhub declares 180
  days of 5-minute history and on a free plan serves none of it — the chart
  would be told history reaches three times further than anything reachable,
  which is the retry-forever class V0.5.2 exists to prevent. It is now
  `monitor.permanently_unusable` (disabled ∪ auth-failed ∪ entitlement-failed).
  A breaker-open or rate-limited provider still contributes: it will be back,
  and the start of history must not lurch about as breakers cycle.
- **A provider's free tier is a MEASURED fact, like its history depth.**
  `adapter.free_tier_serves_history` is False for Finnhub because it was probed,
  not because a doc said so — the same standard `capabilities.py` is held to. It
  affects *advice only* (which provider the app suggests adding, and what the
  card says before you register); it never gates a request, because a paid key
  works fine and the app finds that out by asking.
- **A composite score built from what happened to be measurable is a lie with a
  caveat under it.** A trader with no coach reviews scored **Discipline 100/100,
  grade A** — because the single component that needs no review (revenge trading,
  which reads only timestamps) came back clean, and 20% coverage was enough to
  average. The "measured over 20% of the intended inputs" line underneath did not
  undo the large green A. `confidence.MIN_COVERAGE` now refuses to produce a
  number below 35% coverage. The general rule for any weighted score: dropping an
  unmeasurable component and renormalising is correct, but only up to the point
  where the remainder stops representing the thing being scored.
- **Testing seventy hypotheses at p≤0.20 finds fourteen patterns in pure noise —
  by construction, not by accident.** `scripts/intelligence_benchmark.py` measured
  thirteen "patterns" in 100 uniformly random trades before `patterns.py` had a
  false-discovery correction. Any code that sweeps many slices looking for a
  significant one needs a multiplicity correction (Benjamini–Hochberg here, at
  `FDR_Q`), and the correction's `m` must be **every test performed**, not the
  survivors — correcting over the survivors understates exactly the thing being
  corrected for. Bonferroni was rejected deliberately: over seventy tests it
  demands p<0.0007 and would report nothing, ever.
- **A pattern dimension must describe a CHOICE, never a CONSEQUENCE.** Exit
  reason was a dimension and produced the most impressive finding in the system:
  *"how it ended — stop loss: 0% win rate over 51 trades against 100% elsewhere,
  p<0.0001"*. Which is a definition — a trade that ended at its stop is a losing
  trade — dressed as an edge, ranked first, pushing every real finding down, and
  generating the recommendation *"stop taking stop-loss trades"*. If a bucket is
  determined by the outcome, measuring the outcome inside it can only rediscover
  the bucket.
- **Insufficient evidence is a first-class answer, and it is not the same as a
  negative one.** In `intelligence/`, a metric is `None` (not `0`), a score is
  `None` (not `50`), and a behaviour is `assessable=False` **with the reason
  quoted** rather than `detected=False` — because "not detected" is a claim about
  the trader, and one the data did not support. `hesitation` is permanently
  unassessable and says so: measuring it needs the signal-to-entry latency
  (recorded nowhere) and the setups skipped entirely (which produce no trade).
  Inferring it from hold times would be inventing a psychological claim out of
  unrelated numbers, which is the one thing a coaching system must never do.
- **`inf` is a legitimate value that produces `nan` on comparison.** Profit
  factor is genuinely infinite for a period with no losing trades. Both the
  improvement timeline and the report writer shipped *"your profit factor has
  declined nan% since March"* before `stats.comparable()` existed. Two corollaries:
  every narrative comparison must gate on finiteness, and `json.dumps` emits
  `Infinity`/`NaN` — neither is valid JSON, and a browser parse dies on both, so
  `models._finite()` strips them at the serialisation boundary.
- **Cite the MOST RECENT occurrences, not the first.** `behavior.MAX_CITED` caps
  the trade IDs a finding carries. Taking the head of the list means the newest
  evidence is the evidence that disappears — so the journal could not flag a
  trade the user just closed, which is the one they are most likely to open.
- **A `<details>` element is not its own click target.** In
  `scripts/intelligence_check.py`, clicking the `<details>` rather than its
  `<summary>` produced a check that passed while testing nothing. Related, and
  the reason every assertion in that file is case-insensitive: Playwright's
  `inner_text` returns **rendered** text, and the panel headings are
  `text-transform: uppercase`.
- **`.notif .b` is `white-space: pre-line`.** A template literal broken across
  source lines for readability renders those line breaks in the browser. Any
  string interpolated into that class stays on one source line.
- **A UI guardrail is a SECOND gate, never a replacement for the first.**
  `tkSyncTicket` stops a user assembling the three order combinations
  `OrderManager.place` refuses, and `place` still refuses all three. The
  long-standing lesson here — *adding a gate function is not the same as the gate
  being active* — is equally true in reverse, and cheaper to get wrong. Related:
  when a guardrail removes an option, **say what changed, why, and what to do
  instead**. A control that silently vanishes is a different kind of confusion,
  not less of it, which is why `#tk-kind-why` is an `aria-live` region.
- **Hiding a container is not clearing it.** `renderRecs` set the Coach's
  suggestion panel to `display:none` when there was nothing to suggest and left
  the previous markup inside it — live, clickable buttons for advice that had
  been withdrawn, invisible to a user and very much not to a test. Hide the
  container *and* empty the body.
- **`scrollIntoView({block:"center"})` is not "make this visible".** The guided
  tour's first step highlighted an element pinned to the foot of a full-height
  sidebar, and centring it threw the page to the bottom of the Dashboard before
  the user had read a word. Scroll only when a target is **not visible at all**;
  a tall target running past the fold is normal, not a problem. Found by
  screenshot review, not by an assertion — which is the standing rule here:
  **assert what the user sees, and then go and look at it.**
- **A CWD-relative storage path can survive a milestone that was written to
  remove it, and it will look right.** V0.4.4 moved storage to a stable per-user
  root precisely so replacing the exe could not orphan user data — and
  `/api/learning` kept building its `WeightStore` from `Path("data") / "learning"
  / "weights.json"` until V0.7.0. The engine loads the per-user file; the
  Learning tab was reading a *different* one: on a real install a file that does
  not exist (so it reported no learned weights however much had been learned),
  and in a dev checkout whichever `./data/learning/weights.json` happened to sit
  next to the process — which is why it read plausible values on the one machine
  anyone would test on. The `effective` column came from the live scorer and was
  genuinely correct, which is what made the other two look fine.
  `tests/test_architecture.py::test_no_cwd_relative_storage_paths` now forbids the
  class; use `AppPaths` or the injected `data_dir`.
- **`typeof X` does not protect against a temporal dead zone.** In `index.html`,
  `if (typeof Workspace !== "undefined")` inside `switchTab` — which is defined
  ~6,000 lines above the `const Workspace` it guards — **throws a
  ReferenceError** rather than evaluating false. `typeof` is only safe for
  genuinely undeclared identifiers, not for a `let`/`const` declared later in the
  same scope. Attach a late-initialised module to `window` and test
  `window.Workspace`.
- **`localStorage` is a cache, not storage, and the difference is silent.**
  Clearing the WebView2 profile, restoring a backup or reinstalling discards it
  with no error and no trace. V0.6.1 refused to accept that for onboarding
  progress; V0.7.0 found the entire chart workspace had it anyway. When adding
  client state, decide explicitly whether losing it is acceptable — and if it is
  not, it belongs in `settings.json` through `RuntimeSettings`, with
  `localStorage` kept only as the synchronous fast path.
- **A registry that captures a bound method freezes an overridable seam.**
  `ServiceRegistry` first took `self._live_symbol_check` directly, so a later
  reassignment — which a test does, to keep validation offline — was silently
  ignored. Pass a lambda that calls through the attribute when the collaborator
  is meant to be replaceable.
- **A declared category with no members reads as "nothing qualifies" and means
  "nobody classified it."** `SyncDomain.WORKSPACE` existed with zero inventory
  entries because every workspace fact had been folded into the
  `data/settings.json` preferences row, so the diagnostics report omitted the
  domain entirely — and the inventory looked complete while saying nothing about
  the one domain V0.7.0 built. A test now asserts every declared domain has at
  least one object.
- **A GUI event handler is not just "code that runs late" — it is code that runs
  ON the message pump.** pywebview binds `window.events.closing` as
  `Event(window, should_lock=True)`, so handlers execute **synchronously on the
  WinForms thread**, inside `Form.FormClosing`. `_DesktopController.on_closing`
  called `window.evaluate_js` there. WebView2 runs `ExecuteScriptAsync` and
  schedules its continuation on `syncContextTaskScheduler` — that same pump —
  then pywebview blocks on an **untimed** `semaphore.acquire()`. The release can
  never arrive. That is the whole V0.8.1 "clicking X freezes it, white title bar,
  Not Responding, no traceback" report, and the default preferences send a fresh
  install straight down that branch. `window.hide/show/destroy` are the same
  hazard through `Control.Invoke`, and `server.close()` + `tray.stop()` hold the
  pump for ~7s of thread joins, which alone is past the 5s the shell ghosts a
  window at. **A close handler decides and returns; the work goes to a worker.**
- **Replacing a library's default callback silently discards what the default
  DID.** `pystray.Icon.run(setup=...)` is an if/else: supply a `setup` and you
  lose `self.visible = True`, which is the only path to `_show()`, which is the
  only caller of `Shell_NotifyIcon(NIM_ADD)`. V0.8.1 added a custom `setup` to
  close a real start/stop race and thereby shipped a tray that could never
  appear — `Icon` constructed, thread alive, message loop running,
  `lifecycle_state == "active"`, no exception, and **zero** `NIM_ADD` calls. The
  same gate silently killed tooltip updates (`Icon.title` only refreshes `if
  self.visible`). When you override a hook, go and read what the default body
  did; a library's default is usually load-bearing.
- **`start()` must mean the thing STARTED, not that a thread was created.** The
  tray's `start()` returned True after `Thread.start()`, the launcher stored that
  in `tray_started`, and `on_closing` uses `tray_started` to decide between
  hiding and exiting — so the app hid itself into a tray with no icon. A boolean
  that a caller will act on must describe the observable end state, and where the
  end state is asynchronous, waiting for it is the honest implementation. The
  failure mode of lying is never "a smaller bug"; it is a *different, worse* bug
  in whatever acted on the lie.
- **A test double that models timing but not THREADS proves nothing about a GUI.**
  The entire suite passed over a guaranteed deadlock because `tests/test_desktop_tray.py`'s
  window was a recorder — `evaluate_js` appended a string and returned — and one
  test *asserted the blocking behaviour* by requiring `server.closed == 1` inside
  the handler. Two rules came out of it: the double now knows which thread it is
  on, and it raises a **`BaseException`**, because the lifecycle code legitimately
  wraps these calls in `except Exception` (a window that has already gone must not
  break shutdown) and an ordinary exception was silently swallowed by the very
  code under test — a guard that passed while testing nothing.
- **"Check then start a thread that claims the slot" is not claiming the slot.**
  `MarketDataControl.start_maintenance` tested `job.running` and then spawned a
  worker that called `job.begin()` — measured, 8 of 8 simultaneous requests were
  accepted, against a single slot whose entire purpose is that two concurrent
  cache rebuilds cannot happen. The tell was a *flaky test*: `wait_for_job` polled
  a job that had been accepted but had not yet begun, saw `running == False`, and
  read the previous job's 0% progress. A flaky concurrency test is usually a real
  race wearing a costume.
- **A `--windowed` build has `sys.stdout is sys.stderr is None`, and a
  dependency will eventually dereference one.** `core/logging_setup.py` has
  guarded `sys.stderr is not None` since the first windowed build — and the app
  still died before drawing a window, because *uvicorn* did not:
  `DefaultFormatter.__init__` ends with `self.use_colors = sys.stdout.isatty()`,
  and `uvicorn.Config.__init__` runs `dictConfig` over its own default config,
  so **constructing the config was fatal** (no bind, no request, no server).
  Handling absent stdio in your own code is not enough; anything you hand a
  stream-shaped question to must be checked too. The fix is `log_config=None`
  (`logging_setup.uvicorn_logging_kwargs()`) rather than `use_colors=False`,
  which would leave uvicorn's handlers bound to `ext://sys.stderr` — to None —
  swapping a loud failure for silently discarded records. And because
  `setup_logging` owns the `optionspilot` tree only, uvicorn's loggers are
  **adopted** onto its handlers; otherwise they fall to `logging.lastResort`,
  which writes to the `sys.stderr` that does not exist. The whole suite missed
  this because pytest always has real streams: **anything that only runs when
  stdio is absent must be tested with stdio removed.** The packaged `selftest`
  gate missed it too — it proves lazy imports resolve, it never touches the
  desktop launch path.
- **`$ErrorActionPreference = "Stop"` makes a native program's stderr FATAL —
  but only when the host's stderr is redirected.** PowerShell 5.1 wraps each
  stderr line from a native process in a `NativeCommandError`, and under "Stop"
  that terminates. `scripts/_common.ps1` sets "Stop" for every wrapper script,
  and `pip` exits 0 while writing "a new release of pip is available" to
  stderr — so `Ensure-Environment` threw on a completely successful install,
  taking `test.ps1`, `verify.ps1`, `build.ps1` and `release.ps1` with it. It
  never reproduced interactively, because an interactive console does not
  redirect stderr; it fires in CI, under `*> file`, and in any non-interactive
  runner. `git push` writes *all* of its progress to stderr, which is why
  `ReleaseGit.ps1` wraps every git call the same way. **The exit code is the
  only honest signal a native process gives** — drop to `Continue` around the
  call and consult `$LASTEXITCODE`.
- **Never pass a multi-line message to a native program with `-m`.** PowerShell
  5.1 re-quotes native arguments on the way to `CreateProcess` and mangles
  embedded newlines and quotes doing it. Commit and tag messages go through a
  temp file and `-F` (UTF-8 **without** a BOM — a BOM becomes the first three
  characters of the subject line). Related: `.GetNewClosure()` binds a
  scriptblock into a fresh dynamic module, and a dynamic module cannot see
  functions that were dot-sourced into the calling script — so a rollback
  action written as a closure fails with "not recognized" at exactly the moment
  it is needed. Use a plain scriptblock over `$script:` state.
- PyInstaller only bundles what it can see in literal `import` statements.
  A lazy `importlib.import_module("...")` (added for startup speed in
  `f1bae42`) silently dropped yfinance from every exe built afterwards —
  the build succeeded, the dev venv was fine, and the packaged app only
  failed at runtime on its first data request ("No module named
  'yfinance'", every chart/chain dead). If you make an import dynamic,
  add a `--collect-all` flag in `scripts/build_exe.ps1` in the same
  change; `tests/test_packaging.py` and the build script's packaged
  `selftest` gate both exist to catch this, so run them rather than
  assuming a green build means a complete bundle.
