# PROJECT_STATE.md — exactly where this project stands right now

Read `AI_HANDOFF.md` first if you haven't. This file is the "what's done,
what's next" tracker — keep it current as you work.

**Last updated:** 2026-07-23, after **V0.4.2 architecture audit + three
refactors** (branch `v3-ui`, uncommitted — see "Exact stopping point" below). A
read-only audit (`docs/ARCHITECTURE-AUDIT-V0.4.2.md`) found the codebase healthy,
so only three low-risk behavior-preserving changes were made, each separately
tested: a shared `core/sqlite.py` foundation (`connect` + `user_version`
migrations) adopted by all five stores; `ui/server.py` import cleanup + public
`orchestrator.WINDOW_DAYS`; executable layering-guard tests. No behavior change.
470 tests (+16); `selftest` PASS.

**Prior update:** 2026-07-23, after **V0.4.1 Experience Engine phase 3** — a
centralized `build_snapshot`, advisory historical-similarity on tradeable
signals, the Experience API, and storage schema v2. 454-test suite (+30).

**Prior update:** 2026-07-23, after **V0.4.0 Experience Engine phases 1–2** — the
`optionspilot/experience/` subsystem (rich 100k-scalable `ExperienceStore` +
deterministic Similarity Engine) recorded alongside the journal.

**Prior update:** 2026-07-22, after the **V0.3.5 distribution fix** (branch
`v3-ui`, pending the merge decision).
V0.3.5 root-caused the "downloaded release crashes on launch" report: the exe
worked from the dev machine's `dist\` but a zip → GitHub → download → extract
copy died with `RuntimeError: Failed to resolve
Python.Runtime.Loader.Initialize from Python.Runtime.dll` before any app code
ran. Mechanism (reproduced end-to-end by MOTW-flagging a release copy):
pywebview's only Windows backend (WinForms → WebView2) needs pythonnet, and
.NET Framework refuses to load managed assemblies carrying the Mark-of-the-Web
(`Zone.Identifier` ADS, HRESULT 0x80131515) that Explorer stamps on every file
extracted from a browser-downloaded zip; clr_loader swallows the exception
into the opaque "Failed to resolve" error. Local builds carry no flag — hence
"works here, crashes there". `loadFromRemoteSources` config opt-outs were
tested and don't reach clr_loader 0.3.1's load path, so the fix strips the
marker itself: `optionspilot_app.py::unblock_bundle()` (frozen Windows only,
runs before webview can `import clr`, the programmatic twin of Explorer's
"Unblock" checkbox). +3 tests in `test_packaging.py` (stream removed across
the tree; dev interpreter strictly a no-op; entry point provably calls the
gate before `main()`). 0.3.4 → 0.3.5, 392 tests. Before it: V3.3.1 root-caused the intermittent "switch symbols
enough → chart loads blank and stays blank until restart": no timeout on the
chart fetch (a backend throttle backlog / hung upstream left the first-paint
spinner up forever), superseded fetches never aborted (rapid-switch pile-up on
the serialized yfinance throttle), no backend request timeout, a hung history
fetch leaving `historyLoading` stuck, an uncaught "Value is null" on
non-monotonic data, and an unbounded backend cache. Fixed with bounded
`AbortController` fetches (timeout→recoverable + abort-on-switch), a backend
`REQUEST_TIMEOUT`, `chEnsureMonotonic` + guarded rAF loop, and `MEM_CACHE_MAX`
(0.3.3 → 0.3.4, no new features). Before it: V3.3 was a correctness sprint
verified against LIVE market data during regular trading hours: adaptive live refresh cadence,
America/New_York time display, a candle countdown timer, drawing creation
previews, an overlay that tracks vertical price-axis moves, and the key root
cause — a periodic refresh that no longer discards paged-in history or moves
the viewport (it merges the fresh recent window onto the retained older bars).
Two behaviours were identified as **yfinance provider limitations, not app
bugs** (no streaming feed; forming bar arrives as a flat `volume=0` placeholder)
and documented with the smallest recommended path to a real-time provider.
Version 0.3.2 → 0.3.3. Before it: V3.2.2 audited every viewport mutation,
routed them through one controller, found two root causes (a history-arming
race and an async-callback-timing bug), and added Auto Follow (0.3.1 → 0.3.2).
Before that: V3.2.1 fixed three release-blocker symptoms
(drawings, viewport snapping, tf-context loss); V3.2 made the drawing engine
timeframe-independent (drawings no longer vanish on a tf switch), added a
Ray tool and one unified `chAddDrawing` API for user/AI/replay, shipped
Extended Hours (pre/after-market candles + session shading, display-only),
and bumped the version 0.1.0 → 0.3.0. Before that, RC1–RC3 stabilized the
chart. Earlier the same week: the V3 chart
follow-up session (chart reliability root-caused and fixed, design system,
and redesigns of every tab — seven commits `7176843`…`79138da` on branch
**`v3-ui`**, kept off `main` pending the user's review). Three earlier sessions also landed
this date: V2-4 finish (`50c75aa`), the docs/AI framework (`1029fb0`),
and developer automation (`7373c51`). As always, trust `git log`, not
this file, for whether anything landed.

## Verified facts about current state (checked 2026-07-18)

- Full test suite: **374 tests, 100% passing** (338 from the V2-4-core
  commit, plus the endpoint-level halt test and the new `TestManualEntry`
  suite in `tests/test_risk.py`). Static `$("id")` reference check clean.
- **A `git status` printed "working tree clean" this session while
  `git diff --stat` showed 13 dirty files** (the 2026-07-16 session's
  uncommitted manual-risk-gating work). The output-capture trap in
  `CLAUDE.md` applies to git too — cross-check `git status` with
  `git diff --stat` before trusting either.
- **V2-4-finish work live-verified 2026-07-17** in serve mode against
  scratch data dirs, including a real headless-browser drive (Playwright
  driving system Edge): fib/zone/note tools drawn, persisted across
  reload, cleared; Esc disarm; entry + stop-loss price lines rendered on
  the chart after a real manual buy + protective stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  quantity vetoes surfaced as 422s through the real endpoint. Only
  console error: the pre-existing missing `/favicon.ico` (now in TODO).
- Earlier verified milestones (V2-3 frontend, performance pass, V2-3 exe
  rebuild + smoke test) are recorded in `CHANGELOG.md` and the git log —
  all committed and unchanged by this session.

## Completed (phases 1–8, the original v1 roadmap — see `docs/ROADMAP.md`)

All 8 original phases are done: foundation, analysis suite, AI decision
engine, risk manager + paper broker, journal/learning/backtester,
orchestrator + notifications, desktop UI, hardening (perf pass, TradingView
webhook, broker registry stubs, soak-test harness). This was a prior
session's work and is fully committed, tested, and packaged.

## Completed in the V2 rewrite

### V2-0 — Stabilize (committed)
Watchlist manager (quick add, bulk paste, autocomplete against a bundled
12k-symbol directory, 9 preset lists, favorites, pin/drag/sort/filter,
30-symbol cap) + `RuntimeSettings` overlay system + in-app trading-mode
toggle, all with no-restart live application. 272 tests at the time.

### V2-1 — True desktop application (committed, `0ce001d`)
`--windowed` no-console PyInstaller build, generated app icon
(`scripts/make_icon.py` → `assets/optionspilot.ico`), single-instance guard
(localhost-port mutex; second launch shows a friendly notice window instead
of corrupting the shared SQLite files), windowed-safe logging (skips the
console `StreamHandler` when `sys.stderr is None`).

### V2-2 — Trading engine: orders, manual trading, account metrics (committed, `0ce001d`)
`OrderManager` (`broker/orders.py`): MARKET / LIMIT / STOP_LOSS /
TAKE_PROFIT / TRAILING_STOP orders, DAY/GTC time-in-force, position scaling,
reservation checks (can't oversell a position across bracket orders),
auto-cancel on position close, full SQLite persistence, restart-safe (fills
after restart use live quotes, never stale stored ones). Manual trading API
(`/api/chain`, `/api/orders*`, `/api/account/metrics`) and the Trade tab UI
(account cards, live option chain with Greeks, order ticket, working orders
+ history, one-click position close). `Position.managed_by` field
(`"ai"`/`"manual"`) separates AI-managed from user-managed positions.
Deferred: stock/share positions (options only for now).

### V2-3 — AI Mode vs Human Mode (committed, verified 2026-07-16)

- `EngineConfig.operating_mode: "ai" | "human"` (default `"ai"`), validated,
  independent of `trading_mode` (switching one never flips the other — see
  `RuntimeSettings._apply_mode`'s explicit preservation of
  `operating_mode`).
- `RuntimeSettings.set_operating_mode()` — instant, persisted, no restart.
- `Orchestrator._scan_symbol`: in Human Mode, a tradeable signal becomes a
  one-time "advice only" notification per bar instead of an order — the AI
  genuinely never calls `open_position()` in this mode.
- `Orchestrator._reconcile_manual` / `_capture_context` /
  `_capture_context_for_symbol` / `_finalize_manual`: detects manual
  position open/close by diffing `managed_by="manual"` positions
  cycle-to-cycle, captures analysis context while open (best-effort),
  rebuilds the round trip from `PaperBroker.fills_for()` +
  `OrderManager.orders_for()` on close, and journals it with a
  `TradeCoach` review attached.
- `coach/coach.py` — `TradeCoach.review()`: full before/during/after
  breakdown, 14-tag mistake taxonomy (each with a "what a pro would do" note
  and a concrete exercise), process-based score 0–100 (NOT outcome-based —
  this is load-bearing and tested explicitly:
  `test_disciplined_loser_scores_well` / `test_reckless_winner_scores_badly`
  in `tests/test_coach.py`).
- `coach/profile.py` — `CoachProfile.build()`: aggregates all persisted
  reviews into recurring mistakes, top strengths, score trend, win rate by
  setup quality, top-3 recommended exercises.
- API: `POST /api/operating_mode`, `GET /api/coach`.
- UI: header segmented control (`#op-seg`, "🤖 AI trades" / "👤 You trade"),
  new Coach tab (`#tab-coach`) with cards, recurring-mistakes panel,
  strengths/exercises panel, expandable review-detail table. **Live-verified
  in a real browser this session — see "Verified facts" above.**
- Tests: `tests/test_coach.py` (13 tests) and `tests/test_human_mode.py`.
  All passing (310 total).

## Not started

- **V2-4 — Chart workspace**: bundling `lightweight-charts`, the
  TradingView-inspired layout (top bar/right sidebar/bottom panel), drawing
  tools overlay, trade-from-chart. Nothing exists yet beyond the roadmap
  entry.
- **V2-5 — Replay engine**: historical day replay with hidden future
  candles, play/pause/step/speed, separate replay account, coach review of
  replay trades. Nothing exists yet.
- **V2-6 — Journal & improvement dashboard**: chart-context snapshots per
  trade (the deliberate re-renderable-data substitute for screenshots),
  notes/emotions fields, filtering by strategy/symbol/P&L/date/mistake, and
  a dedicated improvement-trend dashboard (the Coach tab built in V2-3
  covers *some* of this via `CoachProfile`, but the full V2-6 spec — journal
  filtering UI, notes/emotions capture, chart snapshots — is not built).
- Stock/share positions (deferred from V2-2).
- Everything in `ROADMAP-V2.md`'s "Beyond v1" section: live-loop candle
  cache, a real Alpaca paper-API adapter, news/sentiment evidence,
  portfolio-level risk.

## Exact stopping point

**2026-07-23, V0.4.2 architecture audit + three refactors (branch `v3-ui`,
uncommitted at time of writing).** Ran a full read-only architecture audit
(report: `docs/ARCHITECTURE-AUDIT-V0.4.2.md`) and implemented the three approved
low-risk, behavior-preserving improvements, each as a separate change with its
own regression tests: (1) new `core/sqlite.py` (`connect` + `run_migrations` on
`PRAGMA user_version`) adopted by all five stores — `cache` → `journal` →
`orders` → `paper` (idempotent `managed_by` migration) → `experience` (refactored
onto the base); migration 1 of each store is its exact current schema, so
existing `data/*.db` files open unchanged; +13 tests (`test_sqlite.py`). (2)
`ui/server.py` imports hoisted to module top, and the private
`orchestrator._WINDOW_DAYS` promoted to public `WINDOW_DAYS` (also updated
`__main__.py` + the `test_models` wiring test). (3) `tests/test_architecture.py`
(+6) makes the layering executable. Version 0.4.1 → 0.4.2, **470 tests green**,
`selftest` PASS. No user-visible behavior changed. Nothing committed (the user
hasn't asked). **Next:** optional audit follow-ups (report §11) or V0.4 Phase 4
(`learning_mode` axis) — see `docs/ROADMAP-V0.4-EXPERIENCE.md` §11.

**Before that: 2026-07-23, V0.4.1 Experience Engine phase 3 (branch `v3-ui`,
uncommitted at time of writing).** Integrated the Experience Engine into the app. New
`experience/snapshot.py` (`build_snapshot`) is the single centralized capture of
an AI decision context, used by both the AI entry path (`_scan_symbol` →
`_register_meta`, stored in `_TradeMeta.entry_context`) and the manual/coach path
(`_capture_context` now routes through it) for feature symmetry. Tradeable
signals get an advisory `_attach_historical` block (surfaced in the status
payload + advice notification); `Orchestrator.experience_for_symbol` +
`GET /api/experience[/similar]` expose the Experience API. `ExperienceRecord`
gained the full snapshot fields; `ExperienceStore` migrated to schema v2
(`market_regime`/`return_pct`/`hold_minutes` + SQL aggregates). New tests:
`tests/test_snapshot.py` (+6), plus additions to `test_experience.py`,
`test_similarity.py`, `test_ui_server.py`. Version 0.4.0 → 0.4.1, **454 tests
green**. Advisory only — nothing touches the gate/risk/execution. Nothing
committed (the user hasn't asked). No frontend change, so `verify.ps1`'s
browser/chart checks were not re-run (the `pytest` suite was). **Next:** Phase 4
(`learning_mode` axis + Exploration) — see the roadmap doc §11.

**Before that: 2026-07-23, V0.4.0 Experience Engine phases 1–2 (branch `v3-ui`,
uncommitted at time of writing).** Built the two load-bearing phases of the
V0.4.0 sprint: the Experience Engine + store (Phase 1) and the Similarity Engine
(Phase 2). New `optionspilot/experience/` package (`models.py`, `features.py`,
`store.py`, `similarity.py`, `engine.py`, `__init__.py`); `tests/test_experience.py`
(+20) and `tests/test_similarity.py` (+12); wired into `Orchestrator` after both
`journal.record` sites, best-effort. Version 0.3.5 → 0.4.0, **424 tests green**.
Three decisions taken with the user (calibration advisory-only; exploration →
future orthogonal `learning_mode` axis; scope = Foundation + Similarity) are
recorded in `docs/ROADMAP-V0.4-EXPERIENCE.md` §2. Nothing was committed (the
user hasn't asked). No frontend change, so `verify.ps1`'s browser/chart checks
were not re-run this session (the `pytest` suite was). **Next:** Phase 3
(calibration surfacing + AI entry-context capture) — see the roadmap doc §11.

**Before that: 2026-07-22, V3.3.1 chart reliability investigation (branch
`v3-ui`, uncommitted at time of writing).** A pure root-cause investigation (no new
features) of the intermittent "blank chart until restart." The market was
CLOSED, so the live-load failure couldn't be reproduced directly; instead the
lifecycle was instrumented (fetch start/finish/superseded/abort/empty, gen,
cache, render, timers) and the failure characterized under (a) a 250-switch
real-provider stress run, (b) a direct concurrent-load backend hammer, and (c)
deterministic fault injection. Findings — each a concrete mechanism:

- **No timeout on the chart fetch → permanent spinner.** A single cold fetch is
  ~0.2s, but under concurrent load yfinance's one 0.15s-per-request throttle
  lock pushed latency to 10–15s+ (measured), and a hung upstream is unbounded;
  the un-bounded `fetch()` left the first-paint loading overlay up forever
  (confirmed: still a spinner after 6s of a slow backend). "Restart fixes it" =
  restart clears the backlog. Fix: `AbortController` with a 15s timeout that
  routes into the existing recoverable error path.
- **Superseded fetches never aborted** → a rapid switch burst piled requests on
  the serialized throttle and starved the wanted symbol. Fix: abort the previous
  fetch on every new load/switch (a 12-switch burst now aborts 11).
- **Backend `yfinance.history()` had no request timeout** (hang blocked the
  in-flight slot) → added `REQUEST_TIMEOUT=10s`.
- **Hung history fetch left `historyLoading` stuck** → history-fetch timeout.
- **Non-monotonic payload → uncaught "Value is null" from the library's paint
  frame** → `chEnsureMonotonic()` before setData + guarded rAF loop (backend
  `validate_candles` already dedupes+sorts; this is defense-in-depth).
- **Unbounded backend `_mem` cache** → `MEM_CACHE_MAX=400` LRU.

Verified: 250 switches = 0 blanks / 0 console errors; fault injection 7/7
recover; hung backend → recoverable error → auto-recovers; memory plateaus;
chart_check 41 → 44 (all green); +1 backend test (`TestMemCacheBounded`).
Version 0.3.3 → 0.3.4. Remaining limitation: yfinance's global throttle still
adds latency under heavy concurrent load — now recoverable, not a permanent
blank; a streaming provider removes the serialization.

### Before that: V3.3 chart stabilization & market validation

**2026-07-20, V3.3 chart stabilization & market validation (branch `v3-ui`,
uncommitted at time of writing).** A correctness sprint run while the US market
was OPEN, so every issue was reproduced and re-verified against live data in a
real browser (and finally in the rebuilt v0.3.3 exe). 13 issues addressed:

- **Fixed at the architecture level:** live refresh cadence (adaptive ~7s
  intraday poll + lowered `CANDLE_TTL`, re-armed on every load); ET display
  (Intl label formatters, timestamps unchanged); candle countdown timer;
  drawing creation preview (rubber-band); drawing overlay rAF sync loop
  (tracks vertical price-axis moves — lightweight-charts fires no price-scale
  event); and **the key root cause** behind "history randomly vanishes /
  viewport jumps on refresh": the periodic refresh was replacing `CH.data` with
  the base window, discarding paged-in history and shifting logical indices —
  now it MERGES the fresh recent window onto retained older bars
  (`chMergeRefresh`), and the pre-fetch cache paint is limited to real switches.
- **Verified not-regressed with real mouse against live data:** drawing
  persistence across timeframes (same object 1m→1d, never duplicated); candle
  correctness (SPY/AAPL/NVDA match yfinance bar-for-bar); blank charts (13
  named symbols incl. BRK.B render; invalid → error overlay); memory (heap
  plateaus, canvas/cache bounded across 80 switches — no leak); Auto Follow.
- **Provider limitations (documented, not app bugs):** yfinance is poll-only
  (no streaming) and returns the forming bar as a flat `volume=0` placeholder
  until it completes — so a true tick-by-tick forming candle needs a streaming
  provider (smallest path: a new `MarketDataProvider` adapter pushing bar
  updates over a WebSocket; the frontend already applies trailing-bar updates).
- **Tests:** `chart_check` 36 → 41 (ET display, timer, drawing preview,
  vertical-drag overlay tracking, refresh-preserves-history — each fails on
  pre-V3.3 code); hardened the flaky viewport-recovery strand. `verify.ps1`
  green; exe rebuilt (v0.3.3, selftest PASS) and driven by hand.

Version 0.3.2 → 0.3.3. Known limitations unchanged from below plus the yfinance
streaming/forming-bar note now recorded in `PROJECT_STATUS.md`.

### Before that: V3.2.2 viewport ownership unification + Auto Follow

**2026-07-20, V3.2.2 viewport ownership unification + Auto Follow (branch
`v3-ui`, uncommitted at time of writing).** V3.2.1 fixed three symptoms;
every new bug reported afterward (random recentering, history intermittently
failing, losing viewport while scrolling) was another symptom of the same
underlying conflict — the viewport had no single owner. This sprint audited
every viewport mutation in `static/index.html`, routed all of them through
one controller, and found two genuine root causes (not just the arming race
suspected going in):

- **One controller (`chMoveViewport`).** Every sanctioned mover (Reset,
  Latest, Auto Follow, tf-switch focal restore, same-key refresh, history
  prepend, symbol switch) now routes through a single function instead of
  each caller remembering the `restoringViewport` convention individually.
- **Root cause 1 (history-load arming race).** The old wheel/touchstart/
  pointerdown listeners armed history a DOM-event tick after the library's
  own range-change fired during the same pan — a scroll-into-history
  sometimes silently did nothing. Fixed by arming directly off the
  range-change subscription itself.
- **Root cause 2 (the deeper one — async subscription timing).**
  Instrumented the vendored lightweight-charts' real callback timing:
  `subscribeVisibleLogicalRangeChange` fires on a LATER animation frame, not
  synchronously inside `setVisibleLogicalRange()`/`fitContent()`. Resetting
  `restoringViewport` synchronously right after the call (the V3.2.1 pattern)
  closed the guard window before that callback ever arrived — one frame
  later, every sanctioned move looked like a user pan, silently re-arming
  history-load and breaking Auto Follow the instant it was enabled. Fixed by
  deferring the reset two animation frames.
- **Auto Follow (new toggle).** OFF by default (user owns the viewport,
  nothing auto-recenters except Reset/Latest); ON keeps the newest bar in
  view across refreshes/live updates/switches; manual pan disables it;
  Latest re-enables it; persisted. `scrollToRealTime()` turned out to run a
  multi-frame animation (each tick misread as a user pan under the 2-frame
  guard) — replaced everywhere with `chScrollToLatest()`, a single
  non-animated `setVisibleLogicalRange` landing on the same destination.
- Verified: history loads via a real drag pan (no manual-arm cheat) every
  time; on-screen bars stay stationary during a prepend; Auto Follow
  toggles/persists/disables-on-pan/re-enables-on-Latest; live updates respect
  Auto Follow ON vs OFF. `chart_check` 33 → 36. 388 tests.

Version 0.3.1 → 0.3.2. Known limitations: same as V3.2.1 below, unchanged —
extended-hours VWAP not separately RTH-anchored; session classification
time-of-day only; live-update paths still market-hours-unverified.

### Before that: V3.2.1 critical chart regression fixes

**2026-07-20, V3.2.1 critical chart regression fixes (branch `v3-ui`,
uncommitted at time of writing).** Three release-blockers the user reproduced in
the real app that V3.2's tests had reported "fixed" — the tests measured
internal state, not user-visible behaviour. Reproduced each by pixel/viewport
inspection before touching code:

- **Bug 1 — drawings still vanished across timeframes.** V3.2 fixed the
  visibility *filter*, but a 1m-anchored drawing's bar times aren't bars on
  5m/1d, so `chX()` fell to `timeToCoordinate()` → null → painted_px = 0.
  Probed the vendored lightweight-charts: `logicalToCoordinate` returns 0 for
  FRACTIONAL indices but maps INTEGER ones (even off-screen, extrapolating).
  Fix: `chX()` interpolates the pixel between the two bracketing integer bars
  (`chLogicalAt`). Renders on every tf now (verified by painted-pixel count +
  distinct/finite coordinates).
- **Bug 3 — timeframe switch lost context.** RC3's fit-on-switch jumped to an
  unrelated date (82-day drift). Fix: `chCaptureFocal`/`chApplyFocal` capture
  the focal date and re-center the new resolution on it, clamping each endpoint
  to the nearest real bar (finer tfs have shorter history → closest candle).
  Recent focal drift ~0 across a 1h→30m→15m→5m cascade.
- **Bug 2 — viewport auto-reset fought the user.** Same-key refresh restored the
  *time* range, null in whitespace past newest → snap. Fix: preserve the LOGICAL
  range (captured before `setData`); stranded auto-fit only on a symbol-switch
  fallback. Latest/Reset unaffected.
- **Shared root cause:** `setData` fires the range subscription before
  `restoringViewport` was set → `chMaybeLoadHistory` ran mid-switch, prepending
  history and shifting logical indices (n 468→1248), corrupting drawings AND
  viewport. Fixed by guarding before `setData` + disarming history on a switch.

Version 0.3.0 → 0.3.1. chart_check 31 → 33 (9b now asserts drawings RENDER, +9d
focal preservation, +9e viewport stability). 388 tests. Known limitations:
extended-hours VWAP not separately RTH-anchored; session classification
time-of-day only; live-update paths still market-hours-unverified.

### Before that: V3.2 chart-system completion + Extended Hours

**2026-07-19, V3.2 chart-system completion + Extended Hours (branch `v3-ui`,
committed `62cbcb4` + `409cfc0` + docs/version).** The final chart subsystem
sprint before Replay/AI-Viz/Mobile/Broker work:

- **Drawing engine v3 (PARTS 1/2/5, `62cbcb4`).** Root cause of drawings
  vanishing on a tf switch: the model was timeframe-LOCKED (`chDrawVisible`
  filtered `it.tf === CH.tf`). Now every drawing is stored once with a
  `visibility` policy ("all" default / {min,max} tf bounds), `createdTf`,
  `source` (user/ai/replay), and `meta`; the renderer decides visibility
  per-tf and never destroys the object. Legacy drawings migrate to "all". One
  `chAddDrawing` API (on `window`) is the sole creation path for user, AI, and
  replay drawings. **Ray** tool added (two-click, infinite one-way extension),
  reusing the edit machinery. Verified real-mouse in a browser: a 1m trend
  stays visible on 5m/1d; Ray hit-tests its extension; programmatic AI drawing
  + {min,max} policy render correctly; all persist across reload.
- **Extended Hours (PART 4, `409cfc0`).** Confirmed FIRST that yfinance
  supplies pre/after-market bars via `prepost=True` for all intraday intervals
  (04:00–20:00 ET). `extended_hours` is a display-only flag threaded
  provider→cache→payload→`/api/candles?ext=1`, kept OFF the trading path (paper
  execution unchanged); ext frames are cache-keyed separately and skip the disk
  store. `data/sessions.py` labels bars pre/rth/post; the payload tags them and
  computes indicators on the session-correct series. Frontend: persisted "Ext"
  toggle (disabled on daily) + overlay session shading. Verified: ext 5m = 1134
  bars {pre,rth,post} vs 468 RTH; daily forces it off; zero console errors.
- **Version (PART 8):** 0.1.0 → 0.3.0.

Known limitations: session classification is time-of-day only (no holiday/
half-day calendar — documented in `data/sessions.py`); extended-hours VWAP is
computed over the displayed series (not separately RTH-anchored); live-update
correctness for ext bars is architecturally ready but market-hours-unverified.

### Before that: V3.1 RC3 final release blockers

**2026-07-18, V3.1 RC3 final release blockers (branch `v3-ui`, committed
`60f16a4`).** Three bugs the user hit in MANUAL testing that the
green automated suite had missed — reproduced by driving the real mouse/UI
before any code changed:

1. **Toolbar actions "still broken."** The real-mouse workflow (draw → click
   to select → click toolbar) proved the RC2 source fix works; the failure
   was a **stale packaged exe** (`dist/OptionsPilot` built Jul 18 12:02,
   before RC1/RC2 — its bundled `index.html` has none of the fixes; on it
   select/drag/resize work but the toolbar actions no-op, exactly as
   reported). The RC2 regression test had set `DRAW.sel` in JS, bypassing the
   real select→click path, so it couldn't catch this. Fix: **rebuilt the
   exe**; rewrote the test to drive the real mouse (verified fail-before:
   colour unchanged, selection cleared).
2. **Stale banner flapping ("appears far too often").** Market open + a
   rate-limited feed alternating stale/fresh re-raised the warning on every
   stale tick though the newest bar never changed (4 re-shows in 8 refreshes).
   Fix: a per-(symbol·tf) high-water mark (`CH.freshHigh`); a stale payload
   warns only when its newest bar is genuinely older than the freshest bar
   already shown. Verified: 0 re-shows on unchanged data, still warns when
   genuinely behind.
3. **Timeframe switch zoomed into one candle.** Per-key cached viewports were
   restored on switch, snapping to a stale tight zoom. Fix: viewport
   restoration has ONE owner — a switch (symbol or tf) fits; only a same-key
   refresh preserves the live viewport. The per-key viewport cache was
   removed. Verified: every switch shows tens-to-hundreds of bars.

Also fixed (found while hardening tests): a rapid symbol burst ending on an
already-cached symbol left the loading overlay + skeleton legend stuck when
that symbol's refresh returned empty — a non-first-paint load now clears the
overlay and restores the legend. `chart_check.py` → 29 checks (real-mouse
toolbar, anti-flap, tf tiny-zoom). **376 tests**, all green.

### Before that: V3.1 RC2 final chart release audit

**2026-07-18, V3.1 RC2 final chart release audit (branch `v3-ui`).** The
last stabilization pass before the `v3-ui` → `main` merge decision. Four
remaining chart bugs, each reproduced in a real browser before any code
changed, root-caused, fixed at the architecture level, re-verified:

1. **Drawing edit-toolbar actions were dead** (recolour / duplicate / lock
   / hide / width / delete all no-op'd). The toolbar floats inside
   `#ch-main`, so the capture-phase `pointerdown` there fired first; the
   click was on the toolbar, not the drawing, so `chPointerDown` ran its
   "empty space → deselect" branch and cleared `DRAW.sel` before the
   button's click handler saw it. Fix: the capture handler ignores events
   originating in `#ch-draw-bar`.
2. **The "Live data unavailable" banner over-fired / flapped.** It shows on
   any failed live fetch that falls back to disk — but with the market
   CLOSED those disk bars ARE the last session, so it was a false alarm
   that toggled every time a background refresh hit Yahoo's rate limiter.
   Fix: `/api/candles` now reports `market_open` (from the existing
   `Orchestrator.market_open`); the banner is suppressed when closed and
   shown as a real "behind live prices" warning only when open.
3. **The chart could strand the user in whitespace.** Added **Reset view**
   and **Latest** controls (R / L keys), a logical-range `chViewportStranded()`
   detector that tells a stranded view from a deliberate deep zoom, and a
   render-time safety net (switch/first-paint only — never a same-key
   refresh, so it can't yank a chosen viewport).
4. **Random viewport jumps on RSI/MACD toggle** — the two-way subpane sync
   let a new pane's auto-fit shove its full-history range onto main
   ([166,205] → [0,191], reproduced). Fix: the main chart is the sole
   time-range owner; subpanes are one-way followers realigned via
   `chAlignPane`.

Tests: `chart_check.py` → **27 checks** (+ toolbar actions, indicator
no-jump, viewport recovery, market-aware banner, rapid-abuse stress, new-bar
append); `test_ui_server.py` +2 (`market_open`). **376 tests**, `verify.ps1`
green end to end. Performance was audited on evidence (one fetch per load,
no canvas/instance leak across 15 reparents, pane-churn loop removed) — no
speculative optimizations. Market-hours live validation remains the one
open item (market closed); the forming-candle / new-bar / indicator paths
are architecturally verified with simulated ticks. **RC2 committed as
`6f3643d` after `verify.ps1` passed end to end; `v3-ui` is still not merged
to `main` (the user's call).**

### Before that: V3.1 RC1 stabilization polish

**2026-07-18, V3.1 RC1 stabilization polish (branch `v3-ui`, `3a56145`).**
Treated the V3.1 charting work as Release Candidate 1 and ran a full
code/stability/performance audit — no new features, no redesign, the
chart architecture untouched. Findings fixed: removed four orphaned
`CH.*Series/priceLines` arrays left dead by V3.1-4; hardened the two
`JSON.parse(localStorage…)` sites behind a `safeParse` helper so a
corrupt `chInds` (app-init) or drawings store (per-symbol render) resets
to default instead of throwing; guarded the 30s auto-refresh so it never
re-renders mid-drag / mid-tool-placement / mid-history-load; added a
`visibilitychange` refresh so the chart isn't stale for up to a cadence
after wake; bounded the previously-unbounded per-(symbol·tf) payload
cache to a 24-entry LRU; and guarded the WebSocket frame parse. Expanded
coverage: a WS-contract backend test (full-payload-on-connect →
heartbeat, the basis of reconnect catch-up) and two `chart_check`
checks (corrupt-localStorage recovery, LRU bound) → 21 browser checks,
**374 tests**. No source TODO/FIXME/XXX; version consistent at 0.1.0.
Remaining work is genuine market-hours validation (architecture verified
capable) — see the RC1 checklist. **This RC1 pass is uncommitted pending
`verify.ps1` green.**

Before that, 2026-07-18, the V3.1 chart-stabilization sprint (committed
`61a2c60`…`2bcb84a`) made the charting system the strongest part of the app. Seven milestones, each root-caused and
browser-verified before commit: (1) chart reliability — the "some
tickers fail / IWM only shows volume" bug traced to three causes
(drawings driving the price scale, NaN volume 500-ing the endpoint,
non-finite indicator values) and fixed at the data boundary
(`validate_candles` sanitization + logging) and the renderer; (2)
timeframes expanded 6 → 13 (1m…1mo), table-driven; (3) infinite
historical scroll fixed (the merge was inverted — replaced the window
instead of prepending; the trigger mixed bar-index and timestamp units);
(4) editable TradingView-style drawing objects on an overlay canvas
(select/drag/resize/color/width/lock/hide/duplicate/rename/delete +
migration); (5) a collapsible chart on the Trade page (the one instance
relocated between tabs, so everything is shared); (6) live-update
correctness (the forming candle froze because `chSig` ignored intrabar
changes) + a flicker-free `series.update()` fast path; (7) a 19-check
headless-browser regression suite (`scripts/chart_check.py`) wired into
`verify.ps1`. Verified: all 10 required tickers × 13 timeframes return
monotonic real data (130/130); `chart_check` 19/19 green; **374 tests**.
The exe was rebuilt and the packaged charts confirmed working. The
`v3-ui` merge decision remains the user's to make.

Before that, 2026-07-18 packaging-fix session (committed `61a2c60`). The
user found the freshly built exe release-blocked: every chart and
option-chain request failed with "No module named 'yfinance'". Root
cause (traced, not guessed): the performance pass `f1bae42` made the
yfinance import lazy via `importlib.import_module()`, which PyInstaller's
static import scan cannot see — every exe built since then silently
shipped without yfinance and its whole dependency tree. The dev venv was
never affected, and this is not a V3 UI regression (V3-0's error
surfacing is what made it visible instead of a blank canvas). Fixed:
`--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
CLI command (forces the lazy imports, offline) that the build script now
runs against the freshly built exe and fails the build on; and
`tests/test_packaging.py` (+4) failing the ordinary suite if any dynamic
third-party import isn't collected by the build script. Verified: exe
rebuilt (selftest gate PASS, `yfinance 1.5.1` + `curl_cffi` physically
in `_internal`), packaged desktop app served 206 daily SPY candles, 624
SMCI 5m candles, and a 231-contract chain live over HTTP; full
browser-flow sweep of the chart system green (load, indicators,
drawings, trade lines, stale banner, retry, 30s auto-refresh — zero
console errors); `verify.ps1` green end-to-end; **374 tests**. One
pre-existing quirk found and documented (not fixed): `OptionsPilot.exe
serve` from the windowed exe never binds its port (`TODO.md`). Committed
as `61a2c60`; the V3.1 sprint above built on it.

Before that, 2026-07-17, end of the V3 UI sprint (branch `v3-ui`, seven commits,
each browser-verified before committing — see `NEXT_SESSION.md` for the
per-milestone list and `CHANGELOG.md` for detail).** Key facts a next
session needs: the chart blank-canvas bug is fixed at the root
(empty-fetch cache poisoning in `CachedProvider` + missing stale
fallback + uncaught frontend failures + a mid-load switch race); the
engine's strict fail-closed candle path is deliberately unchanged; 351
tests pass; `scripts/verify.ps1` ran clean as the session's closing
action. The branch is **not merged** — that's the user's call. The
order-ticket *fill* path (post-fill stop-loss pre-arm) still needs one
market-hours manual pass; everything else was verified live, including
the risk gate visibly rejecting an after-hours order (correct behavior).

Earlier the same date, the V2-4-finish session ended as follows (kept
for the record):

- **Chart trade lines**: `loadChart` draws labeled price lines for the
  charted symbol — position entry (`entry_spot`, newly exposed in the
  status payload), AI stop/target, and working manual orders'
  underlying-level triggers (stop/take-profit/trailing; LIMIT orders are
  premium-space and deliberately not drawn).
- **Three new drawing tools**: Fib retracement, Zone rectangle, and bar
  Notes (inline text input → chart marker) — persisted per
  symbol+timeframe in localStorage like trend lines; Esc cancels an armed
  tool; old stored drawings load unchanged.
- **Manual-entry risk gating completed**: the 2026-07-16 session left
  `RiskManager.approve_manual_entry` + `OrderManager.evaluate`'s
  fill-time approval callback uncommitted AND unwired for immediate
  market buys — `UIServer.place_order` now preflights them (422 + veto
  text). The hard %-risk sizing veto was converted to an advisory note
  (it blocked nearly all manual buys at default settings and broke a
  committed test; sizing discipline is the coach's `oversized` tag's
  job). New `TestManualEntry` suite in `tests/test_risk.py`.
- **Hygiene cleared**: `pyproject.toml` ships `data_assets/*`, `Pillow`
  in the `dev` extra, `operating_mode` documented inline in `config.yaml`.
- **345 tests, 100% passing.** Static `$("id")` check clean.
- **Live-verified 2026-07-17** in serve mode against scratch data dirs,
  including a real headless-browser drive (Playwright + system Edge,
  installed ad hoc into `.venv` — not a project dependency): fib/zone/
  note drawn, persisted across reload, cleared; Esc disarm; entry +
  stop-loss lines rendered on the chart after a real manual buy + stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  qty-0 vetoes observed as 422s through the real endpoint. Only console
  error: the pre-existing missing `/favicon.ico`.
- Note for future browser driving: lightweight-charts coalesces clicks
  faster than ~500ms as double-clicks — pace scripted two-point drawing
  clicks ≥700ms apart.

**The exe was rebuilt 2026-07-18** with the packaging fix and verified
serving live data (see the current stopping point above).

## Next recommended task

1. **V0.4.0 Phase 4** — the `learning_mode` axis (normal/exploration) added to
   `config/settings.py` + `config/runtime.py` (orthogonal to
   operating_mode/trading_mode), plus exploration-mode tagged, risk-capped
   lower-confidence paper trades. Plumbing already exists
   (`ExperienceRecord.exploration`, snapshot `learning_mode`). Then Phases 5–6
   (AI Performance dashboard frontend, strategy discovery). Phase 3 (integration)
   is done. Full plan: `docs/ROADMAP-V0.4-EXPERIENCE.md` §11.
2. User review of the `v3-ui` branch → merge decision (V0.4.0 also lives here,
   uncommitted).
3. If V3 continues: the remaining `ROADMAP-V3-UX.md` items (H5 notification
   center, N2 chart↔chain links, N4 toast stacking).
4. Eventually: rebuild + smoke-test the exe (LAST, once the branch state
   settles).

## Current priorities

1. `v3-ui` review/merge — user's call.
2. The what-next scope decision after that.
3. Exe rebuild deliberately LAST.

## Blockers

None.
