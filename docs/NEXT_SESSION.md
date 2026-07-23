# NEXT_SESSION.md — start here

Concise session-to-session handoff. Keep this current — update it at the end
of every significant session, not "later." For the detailed narrative behind
any of this, see `PROJECT_STATE.md`; for the structured snapshot, see
`PROJECT_STATUS.md`.

**Last updated:** 2026-07-23, end of V0.4.2 architecture audit + three refactors.

## What was completed most recently? (V0.4.2 — architecture audit + three refactors)

A read-only architecture audit (full report: `docs/ARCHITECTURE-AUDIT-V0.4.2.md`)
concluded the codebase is in good health — clean *verified* layering, no SQL
outside the persistence modules, thin route handlers, zero real debt markers — so
only **three** low-risk, behavior-preserving improvements were implemented, each
a separate change with its own regression tests. **No user-visible behavior
changed.** Version 0.4.1 → 0.4.2. 454 → **470 tests** (+16).

1. **Shared SQLite foundation** (`core/sqlite.py`): `connect()` + versioned
   `run_migrations()` (`PRAGMA user_version`), adopted by **all five** stores in
   order `cache → journal → orders → paper → experience`. Migration 1 of each
   store is its *exact current schema*, so existing `data/*.db` files open
   unchanged. `paper.db`'s `managed_by` ALTER became an idempotent migration 2.
   This gives the journal/paper/orders databases (and future Replay/Analytics/
   Live-Broker DBs) the safe schema-evolution the experience store already had.
   +13 in `test_sqlite.py`, incl. legacy-db + idempotent-ALTER hazards.
2. **UI/server import cleanup**: ~15 imports hoisted from `ui/server.py` bodies
   to the module top; the **private** `orchestrator._WINDOW_DAYS` reach-through
   (in `ui/server.py` and `__main__.py`) removed by promoting it to public
   `orchestrator.WINDOW_DAYS`.
3. **Layering-guard tests** (`test_architecture.py`, +6): the dependency graph
   is now executable — an AST allow-list asserts each subpackage imports only its
   permitted siblings, composition roots don't import upward, and `ui/server.py`
   stays free of function-level imports.

**Optional / not done** (per the audit report, judgment over churn): orchestrator
decomposition (Finding 2), `core→config` inversion (5), snapshot-bypass tidy (6).

## What was completed before that? (V0.4.1 phase 3 — Experience Engine integration)

Completed the integration of the Experience Engine into the rest of OptionsPilot:
every AI recommendation now carries advisory historical context. **Backend + API
only — no frontend** (dashboard is Phase 5). Version 0.4.0 → 0.4.1, a **454-test
suite** (+30). Full design in `docs/ROADMAP-V0.4-EXPERIENCE.md` §12.

1. **Centralized AI snapshot** (`experience/snapshot.py::build_snapshot`) — the
   ONE place a deterministic decision context is captured (score, reasoning, HTF
   trend, full evidence breakdown, gate result + rejection reasons, RSI/ADX/rvol/
   ATR/EMA/MACD/VWAP/supertrend/divergence, contract Greeks, stop/target/RR,
   operating/trading/learning modes). Duck-types the `EngineDecision` (no runtime
   `engine/` dependency). Uncomputed fields (Bollinger, volume-profile histogram)
   stored as None — never invented.
2. **Feature symmetry** — AI entry (`_scan_symbol`→`_register_meta`, snapshot in
   `_TradeMeta.entry_context`) and the manual/coach path (`_capture_context`, now
   also built by `build_snapshot`) go through the one builder. Shared
   `features._entry_fields` backs both a closed trade and a live query.
3. **Advisory historical-similarity explanation** — for *tradeable* signals only,
   `_attach_historical` attaches `explain_setup(snapshot)` (n similar / win rate /
   return / calibrated confidence / grounded success & failure patterns) to the
   status payload and the Human-Mode advice notification. Computed AFTER the
   deterministic decision; never feeds back into it.
4. **Experience API** (`ExperienceEngine`, no SQL past the store): `recent`,
   `similar_trades`/`similar_to_snapshot` (→ `SimilarTrade` rows), `statistics`,
   `strategy_statistics`, `regime_statistics`, `failure_modes`,
   `success_patterns`, `explain_setup`. Over `GET /api/experience` and
   `GET /api/experience/similar?symbol=`.
5. **Storage v2** (`_migration_2`) — indexed `market_regime` (trend × IV vol) +
   `return_pct`/`hold_minutes`, backfilled from payloads; SQL-only aggregates.

**Safety reaffirmed:** nothing touches the gate/risk/sizing/entries/exits; the
deterministic score is the sole trading input; every new call site is
best-effort. All 424 prior tests still pass. Measured perf at 20k rows:
similarity summarize well under 3s, SQL aggregate under 0.5s.

## What was completed before that? (V0.4.0 phases 1–2 — the AI Experience Engine)

The first two phases of the V0.4.0 sprint that turns the AI from a static
analyzer into a system that learns from paper-trading experience. **Backend
only — no frontend change** (so the shallow-frontend-coverage risk doesn't
apply this session). Version 0.3.5 → 0.4.0. 392 → **424-test suite** (+32). Full
design/rationale/forward-plan in **`docs/ROADMAP-V0.4-EXPERIENCE.md`** — read it
before continuing this line of work.

**What was built** — a new `optionspilot/experience/` subsystem, the AI's
long-term memory, recorded **alongside** the journal (never instead of it; the
journal stays the system of record and the sole learning input):

1. **Experience Engine + store (Phase 1).** `ExperienceRecord` = a rich,
   expandable superset of `TradeRecord` (outcome, decision context,
   market/session indicators, reasoning, an exploration flag, and an `extra`
   JSON blob for future fields — screenshots/news — with *no* migration).
   `ExperienceStore` (`data/experience.db`) is built for 100k+ trades without a
   redesign: indexed query columns + full-fidelity JSON payload + a
   `PRAGMA user_version` migration framework (refuses a newer-than-supported
   schema). `features.py` extracts the record + a fixed-range normalized feature
   vector, purely.
2. **Similarity Engine (Phase 2).** `SimilarityEngine` — deterministic weighted
   distance (direction anchor + evidence Jaccard + setup/trend/tf/session +
   numerics) → the k most similar historical trades, aggregated into evidence:
   win rate, avg return/hold, most-common exit, typical failure mode, and an
   **advisory** calibrated confidence (shrinkage blend of model + history).

**Three decisions taken with the user** (see the roadmap doc §2): (A) calibrated
confidence is **advisory/display-only** — the deterministic scorer remains the
sole live-trading input; (B) "Exploration mode" becomes a **future orthogonal
`learning_mode` axis**, not a `trading_mode` value (already modelled as
`ExperienceRecord.exploration`); (C) this session = Foundation + Similarity only.

**Integration:** `Orchestrator` builds `self.experience` and calls
`record_trade` after both `journal.record` sites (AI + manual). Best-effort — a
failure is logged and swallowed, never disturbing journaling/risk/trading
(`test_record_trade_is_best_effort`). All 392 prior tests still pass unchanged.

## What was completed before that? (V0.3.5 — downloaded release crashed on launch)

The exe worked from the dev machine's `dist\` folder but crashed on another
machine (or any re-downloaded copy) with `RuntimeError: Failed to resolve
Python.Runtime.Loader.Initialize from Python.Runtime.dll` before any app code
ran. Root cause (reproduced end-to-end, not guessed): pywebview's only Windows
backend is WinForms, which drives WebView2 through pythonnet (`import clr`),
and a browser-downloaded zip extracted with Explorer flags every file with the
Mark-of-the-Web (`Zone.Identifier` ADS) — **.NET Framework refuses to load a
MOTW-flagged managed assembly** (HRESULT 0x80131515); clr_loader swallows the
exception into the opaque "Failed to resolve" error. Locally built files carry
no flag, which is why every dev-side launch worked. `loadFromRemoteSources`
config opt-outs were tested and do NOT reach clr_loader 0.3.1's load path.
Fix: `optionspilot_app.py::unblock_bundle()` deletes the `Zone.Identifier`
stream from the app's own files at startup (frozen Windows only, before
webview loads clr) — programmatically identical to Explorer's "Unblock".
Tests: `TestUnblockBundle` (+3, in `tests/test_packaging.py`). Version
0.3.4 → 0.3.5, 392-test suite. Verified by MOTW-flagging a full release copy
outside the repo and launching: the desktop window opens.

## What was completed before that? (V3.3.1 — chart reliability investigation)

A pure root-cause investigation (NO new features) of the intermittent
"switch symbols enough times → a chart loads blank and stays blank until
restart." The lifecycle was instrumented and the failure reproduced under
load + fault injection before any code changed. Version 0.3.3 → 0.3.4.
chart_check 41 → 44. 388-test suite (recount: 389).

**Root causes (all lifecycle/resource, not rendering):**
1. **No timeout on the chart fetch → permanent blank.** yfinance serializes
   every fetch through one 0.15s-per-request throttle lock; under concurrent
   load (scan loop + rapid switching) latency was measured at 10–15s+, and a
   hung upstream connection is unbounded — the first-paint spinner stayed up
   forever ("restart fixes it" = restart clears the backlog).
2. **Superseded fetches were never cancelled.** A rapid switch burst left every
   superseded request running and holding a throttle slot, starving the symbol
   the user actually landed on.
3. **Backend `yfinance.history()` had no request timeout** — a hung Yahoo
   connection blocked the in-flight slot for that key.
4. **A hung history fetch left `historyLoading` stuck true** — history loading
   silently disabled for the session.
5. **Non-monotonic data threw an uncaught "Value is null"** from
   lightweight-charts' own paint frame (backend already sanitizes; frontend
   didn't).
6. **Backend `_mem` cache was unbounded.**

**Fixes (root-cause, not blind retries):** bounded `AbortController` chart +
history fetches (15s timeout → the existing recoverable error path, and
abort-on-switch so superseded fetches stop consuming the throttle); backend
`REQUEST_TIMEOUT=10s` on `yfinance.history()`; `chEnsureMonotonic()` sanitizer
before `setData` + a guarded rAF overlay loop; bounded `MEM_CACHE_MAX=400`.

**Verified:** 250 rapid symbol switches = 0 blanks / 0 console errors;
fault injection (empty / malformed / flapping) recovers 7/7; a hung backend
now times out to a recoverable error and auto-recovers (no restart); a
12-switch burst aborts 11 fetches; memory plateaus; all prior 41 checks green.
Files: `optionspilot/ui/static/index.html`, `optionspilot/data/yfinance_provider.py`,
`optionspilot/data/cached.py`, `scripts/chart_check.py` (+3), `tests/test_cached.py` (+1).

**Remaining provider limitation:** yfinance's single global throttle still adds
latency under heavy concurrent load — now *recoverable* (bounded fetch) rather
than a permanent blank. A streaming provider (documented upgrade path) removes
the serialization entirely.

## What was completed before that? (V3.3 — chart stabilization & market validation)

A correctness sprint verified against LIVE market data (the US market was open),
not just tests. Every issue was reproduced in a real browser, root-caused, fixed
at the architecture level, and re-verified in the browser and the rebuilt exe.
Version 0.3.2 → 0.3.3. chart_check 36 → 41. `verify.ps1` green.

1. **Live sync cadence (Issue 1).** Timeframe-adaptive refresh (~7s intraday
   while open, slower for hourly/daily, idle when closed), re-armed on every
   load; `CANDLE_TTL` for fine frames lowered in lockstep so a fast poll returns
   fresh bars. **yfinance is poll-only and gives the forming bar as a flat V=0
   placeholder until it closes** — a true tick-by-tick forming candle needs a
   streaming provider (documented). Completed bars match yfinance bar-for-bar.
2. **Timezone (Issue 2).** x-axis + crosshair now render in America/New_York via
   `Intl` label formatters (timestamps unchanged, so drawings/history/timeIndex
   are untouched). Daily bars sit at ET midnight → no off-by-one date.
3. **Countdown timer (Issue 3).** TradingView-style "time to bar close" pill,
   1s tick, from the real clock; intraday-open only.
4. **Drawing render lag (Issue 4).** Added an rAF overlay sync loop — the chart
   library fires no price-scale event, so drawings used to freeze on a vertical
   drag and snap; now they track every frame the coordinate mapping changes.
5. **Drawing creation preview (Issue 5).** First click anchors + rubber-bands the
   second endpoint to the cursor; second click finalizes.
6. **Refresh discarded history + moved viewport (Issues 7 & 8 — key root cause).**
   The periodic refresh re-fetches only the base window and was REPLACING
   `CH.data`, discarding paged-in history and shifting logical indices (viewport
   jump). Now it MERGES the fresh recent window onto retained older bars
   (`chMergeRefresh`); the pre-fetch cache paint is limited to real switches.
7. **Verified not-regressed (6/9/10/11/12):** persistence across tf, candle
   correctness, blank charts (13 symbols + BRK.B + invalid), memory (no leak),
   Auto Follow — all with real mouse against live data.

Files: `optionspilot/ui/static/index.html` (formatters, timer, rAF loop, preview,
`chMergeRefresh`, adaptive refresh), `optionspilot/data/cached.py` (TTLs),
`scripts/chart_check.py` (+5 checks, hardened strand). Exe rebuilt v0.3.3.

## What was completed before that? (V3.2.2 — viewport ownership unification + Auto Follow)

Every bug reported after V3.2.1 (random recentering, history intermittently
failing, losing viewport while scrolling) was another symptom of one
underlying conflict: the viewport had no single owner. Version 0.3.1 → 0.3.2.
chart_check 33 → 36.

1. **Audit + one controller (Bug 4).** Every `fitContent()`/
   `setVisibleLogicalRange()`/`setVisibleRange()`/`scrollToRealTime()` call
   site was enumerated by owner and reason; all of them now route through
   one function, `chMoveViewport()`, instead of each caller remembering the
   `restoringViewport` convention individually.
2. **History-arming race (Bug 2, part 1).** The old wheel/touchstart/
   pointerdown listeners armed history a DOM-event tick after the library's
   own range-change fired during the same pan, so a scroll into history
   sometimes silently did nothing. Fix: arm directly off the range-change
   subscription itself.
3. **The deeper root cause (Bug 2, part 2).** Instrumented the vendored
   lightweight-charts' real callback timing: `subscribeVisibleLogicalRangeChange`
   fires on a LATER animation frame, not synchronously inside
   `setVisibleLogicalRange()`/`fitContent()`. Resetting the guard flag
   synchronously right after the call (the V3.2.1 pattern) closed the window
   before that callback ever arrived — one frame later every sanctioned move
   looked like a user pan, silently re-arming history-load. Fix: defer the
   reset two animation frames in `chMoveViewport`.
4. **Auto Follow (Bug 3) — new toggle.** OFF by default (user owns the
   viewport, nothing auto-recenters except Reset/Latest); ON keeps the
   newest bar in view across refreshes/live updates/switches; manual pan
   disables it; Latest re-enables it; persisted (`localStorage`). New
   `#ch-follow` button + `A` shortcut.
5. **`scrollToRealTime()` animation discovery.** Auto Follow wouldn't stay
   on: `scrollToRealTime()` runs a multi-frame smooth-scroll animation, and
   each intermediate tick was misread as a user pan once the 2-frame guard
   window closed — disabling Auto Follow before the animation even
   finished. Fix: `chScrollToLatest()`, a single non-animated
   `setVisibleLogicalRange` computed to the same destination, used
   everywhere instead.
6. **Bug 5 verified.** A history prepend never moves on-screen bars — only
   new (older) bars appear at the left; covered by a regression test
   capturing the on-screen time range immediately before/after a real-drag
   merge.

Tests: `chart_check` 33 → 36 (real-drag history load with no arming cheat +
stationarity; Auto Follow OFF-default/toggle/persist/manual-pan-disables/
Latest-re-enables; live updates respecting Auto Follow ON vs OFF). Also
hardened `chart_check.py` itself: the extended-hours route stub could
occasionally double-fulfill the same request (pre-existing test-harness
race) — now defensively swallowed; added a `window.__chNoAutoRefresh`
test-only flag so the chart's 30s background-refresh timer can't race a
route stub once a suite run's wall-clock time exceeds that cadence.
**388-test suite unchanged**, `verify.ps1` green end to end, exe rebuilt and
driven by hand.

## What was completed before that? (V3.2.1 — critical chart regression fixes)

Three release-blocker regressions the user still hit in the real app despite
V3.2's passing tests — because those tests measured internal state, not
user-visible behaviour. Version 0.3.0 → 0.3.1. chart_check 31 → 33.

1. **Drawings still disappeared across timeframes (Bug 1).** V3.2 fixed the
   visibility *filter* (`chDrawVisible().length` passed) but a 1m-anchored
   drawing's bar times aren't bars on 5m/1d, so `chX()` → `timeToCoordinate()`
   returned null and it painted nothing. Fix: `chX()` interpolates the pixel
   between the two bracketing INTEGER-bar coordinates (the vendored
   lightweight-charts `logicalToCoordinate` returns 0 for fractional indices but
   maps integers fine, even off-screen). Now renders on every timeframe.
2. **Timeframe switch lost context (Bug 3).** RC3's "fit on switch" threw the
   user's place away. Fix: capture the focal date before the switch and
   re-center the new resolution on it (`chCaptureFocal`/`chApplyFocal`), clamping
   each endpoint to the nearest real bar (finer tfs have shorter history → land
   on the closest candle). Recent focal preserved with ~0 drift.
3. **Viewport fought the user (Bug 2).** Same-key refresh restored the time
   range (null in whitespace past newest → snap). Fix: preserve the LOGICAL
   range (captured before `setData`); the stranded auto-fit fires only on a
   symbol-switch fallback. Latest/Reset remain the only auto-recenters.

Underlying both 1+3: `setData` fired the range-change subscription before
`restoringViewport` was set, so history loaded mid-switch and corrupted logical
indices (n grew 468→1248). Fixed by setting the guard before `setData` and
disarming history on a switch. Tests now assert real coordinates/viewport.

## What was completed before that? (V3.2 — chart completion + Extended Hours)

The final evolution of the chart subsystem, on branch **`v3-ui`** (still not
merged). Version bumped **0.1.0 → 0.3.0**. a **388-test suite**, chart_check **31**,
`verify.ps1` green; the exe was rebuilt and driven by hand.

1. **Timeframe-independent drawing engine (PARTS 1/2/5).** Drawings vanished on
   a tf switch because the model was tf-LOCKED. The v3 model stores each drawing
   once with a `visibility` policy ("all" default, or {min,max} tf bounds),
   `createdTf`, a `source` tag, and `meta`; the renderer decides per-tf whether
   to show it and never destroys it. Legacy drawings migrate to visibility
   "all". One `chAddDrawing(spec)` API (on `window`) serves user tools now and
   the AI scanner / replay engine later — one engine, no special cases.
2. **Ray tool (PART 2).** Two-click, extends infinitely past the second point;
   reuses the existing edit/persist machinery.
3. **Extended Hours (PART 4).** Confirmed yfinance supplies pre-/after-market
   candles via `prepost=True`. `extended_hours` is a display-only flag threaded
   provider→cache→payload→`/api/candles?ext=1` (trading path stays RTH-only);
   `data/sessions.py` classifies bars; the frontend has a persisted "Ext"
   toggle (disabled on daily) with pre/after-market session shading.

**Recommended next:** Replay Mode (inherits the drawing engine + session
architecture), then AI Visualization (draws via `chAddDrawing({source:"ai"})`),
then Mobile / Broker integrations. See `ROADMAP-V2.md`.

## What was completed before that? (V3.1 RC3 — final release blockers)

Three user-reported bugs from **manual** testing that the passing automated
suite had missed, each reproduced by driving the real mouse/UI before any fix:

1. **"Toolbar actions STILL don't work."** Reproducing with the real mouse
   (draw a trendline → click it to select → click the toolbar) showed the
   *source* fix from RC2 works. The culprit was the **packaged exe**: the
   `dist/OptionsPilot` bundle was built Jul 18 12:02, before RC1/RC2, so its
   `index.html` predates every toolbar/viewport/banner fix — on that build
   select/drag/resize work but recolour/duplicate/lock/hide/delete no-op,
   exactly as reported. Fix: **the exe was rebuilt.** The regression test was
   rewritten to drive the real mouse end to end and verified to fail on the
   pre-fix source.
2. **Stale banner "appears far too often."** With the market open and the
   feed flapping stale/fresh (rate-limiting), the banner re-raised on every
   stale tick for data whose newest bar never changed. Fix: a per-(symbol·tf)
   high-water mark (`CH.freshHigh`) — a stale payload only warns when its
   newest bar is genuinely older than the freshest bar already shown.
3. **Timeframe switching zoomed into one candle.** Per-(symbol·tf) cached
   viewports were restored on switch, snapping back to a stale tight zoom.
   Fix: one owner for viewport restoration — a switch always fits; only a
   same-key refresh preserves the live viewport. The per-key viewport cache
   was removed.

Also fixed while hardening the tests: a rapid symbol burst ending on an
already-cached symbol could leave the "loading" overlay and skeleton legend
stuck if that symbol's refresh came back empty — a non-first-paint load now
clears the overlay and restores the legend. `chart_check.py` → **29 checks**
(real-mouse toolbar, anti-flap banner, tf-switch tiny-zoom). **376-test suite.**

## What was completed before that? (V3.1 RC2 — final chart audit)

The **RC2 final chart release audit**, on branch **`v3-ui`** (still not
merged). Four remaining chart bugs, each reproduced in a real browser,
root-caused, fixed at the architecture level, and re-verified:

1. **Drawing toolbar actions were dead.** The capture-phase `pointerdown`
   on `#ch-main` fired before a toolbar button's click; because the click
   landed on the floating toolbar (not the drawing), `chPointerDown` took
   its "empty space → deselect" branch and cleared `DRAW.sel`, so every
   toolbar action (recolour/duplicate/lock/hide/width/delete) no-op'd.
   Fix: the capture handler now ignores events originating in `#ch-draw-bar`.
2. **The "Live data unavailable" banner over-fired.** It fires whenever a
   live fetch fails and disk-cached bars are served — but while the market
   is CLOSED those cached bars ARE the last session, so the banner is a
   false alarm that flaps whenever a background refresh trips Yahoo's rate
   limiter. Fix: `/api/candles` now reports `market_open`; the banner is
   suppressed when closed, shown (a real "behind live prices" warning) when
   open.
3. **The chart could strand the user.** Lightweight-charts clamps pan/zoom
   so it's never literally empty, but bars could be shoved to the far edge
   with a screen of whitespace ("the chart disappeared"). Fix: **Reset view**
   (fitContent) and **Latest** (scrollToRealTime) buttons + **R**/**L**
   keys, a whitespace-aware `chViewportStranded()` detector, and a
   render-time safety net that recovers a stranded restored viewport.
4. **Random viewport jumps.** Toggling RSI/MACD recentred the main chart:
   the two-way subpane sync let a freshly-created pane's auto-fit shove its
   full-history range back onto main. Fix: the **main chart is now the sole
   owner** of the time range — panes are one-way followers (`chAlignPane`).

Tests: `scripts/chart_check.py` grew to **27 checks** (+ toolbar actions,
indicator-no-jump, viewport recovery, market-aware banner, a rapid-abuse
stress burst, and a new-bar-append proxy for the market-hours rollover);
`tests/test_ui_server.py` gained 2 backend tests for the `market_open`
field. **376-test suite**, `verify.ps1` green end to end.

## What was completed before that? (V3.1 chart-stabilization sprint)

The **V3.1 chart-stabilization sprint**, on branch **`v3-ui`** (still not
merged — the user asked for `v3-ui` to stay isolated until reviewed).
Seven milestones, each root-caused and browser-verified before commit,
which made the charting system the strongest part of the app:

1. **V3.1-1 `b93eac9` — chart reliability.** The "some tickers randomly
   fail / IWM only shows volume" reports traced to three causes: a stored
   drawing with a stray price drove the price scale and crushed the
   candles (drawings now use `autoscaleInfoProvider:null`); NaN volume on
   the forming bar 500'd the endpoint during JSON serialization
   (`validate_candles` now drops NaN/inf/≤0 OHLC bars, zeroes non-finite
   volume, and logs every removal with symbol/tf context); and non-finite
   indicator values (payload runs one `validate_candles` choke point +
   `isfinite` guards). Renderer wrapped in try/catch → error overlay, not
   a half-painted canvas.
2. **V3.1-2 `0d2c870` — 13 timeframes.** 1m/2m/3m/5m/10m/15m/30m/1h/2h/
   4h/1d/1w/1mo, table-driven (`core/models._TF_LABEL`,
   `yfinance_provider._FETCH_SPEC`, `orchestrator._WINDOW_DAYS`,
   `cached.CANDLE_TTL`); a test fails if a member isn't in all four.
3. **V3.1-3 `98551e1` — infinite scroll-back.** The paging merge was
   inverted (replaced the window instead of prepending) and the trigger
   mixed bar-index vs timestamp units. Older bars now prepend with
   indicators in lockstep; viewport/zoom/drawings preserved.
4. **V3.1-4 `917d0c9` — editable drawings.** Overlay-canvas object model
   (`{id,type,tf,points,color,width,text,locked,hidden}`, stored
   `{version:2,items}`, old format migrated): select/drag/resize/color/
   width/lock/hide/duplicate/rename/delete, instant tool arming.
5. **V3.1-5 `edfe2bc` — Trade-tab chart.** The one chart instance
   relocated into a collapsible Trade slot; symbol/tf/drawings/indicators
   shared; preference remembered.
6. **V3.1-6 `5e04506` — live updates + perf.** `chSig` now includes the
   last bar's OHLCV (the forming candle no longer freezes intrabar); a
   `series.update()` fast path renders trailing bars with no flicker/reflow.
7. **V3.1-7 `2bcb84a` — chart test suite.** `scripts/chart_check.py`
   (19 headless-browser checks) wired into `verify.ps1`; verified 10
   tickers × 13 timeframes = 130/130.

Immediately preceding this sprint (same day, `61a2c60`): the packaged exe
shipped without yfinance (lazy `importlib` import invisible to
PyInstaller) — fixed with `--collect-all yfinance`, a `selftest` build
gate, and `tests/test_packaging.py`.

### Earlier: the V3 product-quality sprint (`v3-ui`)

Seven milestones (V3-0 … V3-6) + a pre-merge audit (V3-7), each built →
verified in a real browser → committed separately:

1. **V3-0 `7176843` — chart reliability.** The "app opens with no usable
   charts" bug was root-caused (not guessed): yfinance returns *empty*
   frames on transient failures, `CachedProvider` memoized those empties
   for the full TTL (poisoning retries), the disk cache was never used as
   a fallback, and the frontend had no catch/retry and silently dropped
   mid-load switches. Fixed at all four layers; the canvas can no longer
   be silently blank (loading overlay → error overlay with Retry → stale
   banner), charts auto-refresh every 30s preserving zoom. The engine's
   strict fail-closed data path is unchanged (tested explicitly).
2. **V3-1 `e06031c` — design system.** Type/spacing/elevation tokens,
   inline-SVG icon nav, 56px collapsed rail below 1180px, and a real
   pre-existing flex/grid min-width blowout fixed (`main{min-width:0}`,
   `minmax(0,1fr)`).
3. **V3-2 `641d617` — dashboard.** 2:1 layout, AI-opportunities and
   watchlist-movers side rail, action-oriented empty states.
4. **V3-3 `629c19d` — trade screen.** ATM quick-picks, risk-vs-buying-power
   line, on-tab positions with close-prefill, B/S/+/−/Enter order keys.
5. **V3-4 `a365871` — settings.** Grouped searchable config cards replace
   the JSON dump; live-trading flags visibly locked (🔒 off by design).
6. **V3-5 `776d23d` — analytics.** Coach first-run explainer, journal
   filters + cumulative-P&L curve, backtest drawdown/exit-reason panels,
   learning weight-shift bars.
7. **V3-6 `79138da` — accessibility.** Skip link, toast live region,
   `scope="col"` everywhere, `aria-current`, `?` shortcut overlay.
8. **V3-7 — pre-merge audit fixes.** A full senior-review pass over the
   branch found and fixed three real issues: `CandleCache` was unusable
   from worker threads (`check_same_thread` — the disk cache silently
   never worked in the live app, and V3-0's stale fallback would have
   returned empty in production; fixed with a locked shared connection +
   threading regression test), the chart's 30s timer never auto-retried a
   failed *first* load, and Enter could submit an order from behind the
   `?` overlay. Each fix browser- or thread-verified individually.

The audit that scoped all of this is `ROADMAP-V3-UX.md` (committed with
V3-0).

9. **Packaging fix (2026-07-18, this session, uncommitted).** The user
   found the freshly built exe unusable: every chart/quote/chain request
   failed with "No module named 'yfinance'". Root cause: the performance
   pass (`f1bae42`) made the yfinance import lazy via
   `importlib.import_module`, which PyInstaller cannot see, so every exe
   built since then silently shipped without yfinance. Fixed with
   `--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
   CLI command that the build script now runs against the fresh exe
   (build fails on an incomplete bundle); and `tests/test_packaging.py`
   (+4 tests — fails the ordinary suite if any dynamic third-party
   import isn't collected). Exe rebuilt and verified live: candles
   (daily + 5m) and a 231-contract chain served from the packaged app;
   full browser flow sweep of the chart system green. **376-test suite.**

## What is currently stable?

Everything on both branches. **376-test suite passes** (+6 cached-provider tests and a CandleCache threading regression test
added in V3-0, +4 packaging-guard tests + 2 `market_open` tests added 2026-07-18). `scripts/verify.ps1` ran clean end-to-end as the closing
action of the session, and every milestone additionally got scenario-level
Playwright verification (chart failure states, the full order-ticket flow —
including the manual-entry risk gate visibly rejecting an after-hours
order, which is correct behavior — settings search, a real 25-day backtest
run, the accessibility overlay).

## What should be worked on next?

**Optional architecture follow-ups (from the V0.4.2 audit, `docs/ARCHITECTURE-
AUDIT-V0.4.2.md` §11).** None urgent: Finding 2 (extract a `ManualTradeReconciler`
from `orchestrator.py`) only if that file keeps growing; Finding 5 (de-invert
`core→config` in `logging_setup`); a journal `overview()` SQL path when journaled
trades approach five figures. Leave unless there's a reason.

**V0.4.x continuation (Experience Engine).** The design doc
`docs/ROADMAP-V0.4-EXPERIENCE.md` §11 has the full forward plan. Phase 3 is done.
In order:

- **Phase 4 — `learning_mode` axis + Exploration.** Add the third orthogonal
  mode axis (normal/exploration) to `config/settings.py` + `config/runtime.py`
  following the `trading_mode`/`operating_mode` orthogonality pattern; in
  exploration mode take tagged, strictly risk-limited lower-confidence paper
  trades. The plumbing already exists: `ExperienceRecord.exploration`, its store
  column, the `learning_mode` snapshot field, and the exploration→record wiring.
  (Promoting calibration into the gate is a *separate*, dedicated decision —
  Decision A; do not fold it into Phase 4.)
- **Phase 5 — AI Performance dashboard.** New tab over `/api/experience` +
  `/api/experience/similar` (both already built). This is the **single-file
  frontend** — **manually browser-verify** (no automated UI coverage).
- **Phase 6 — Strategy discovery infrastructure.** Group experiences by shared
  characteristics (the `extra["snapshot"]` evidence breakdown is the raw
  material) for later pattern mining. Infra only.

**Pre-existing, still open:**

1. **The user reviews the `v3-ui` branch** (run `.\scripts\dev.ps1` on the
   branch and click through) and decides on merging to `main`. The V0.4.0
   Experience Engine work also lives on `v3-ui`, uncommitted.
2. **Market-hours chart validation** (couldn't be done — market closed):
   confirm live candles/volume/indicators/price-line update during a
   session, and that the forming candle updates in place (the V3.1-6
   fast path was verified with a simulated tick, not a real one). The
   live-update code path and the intrabar `chSig` fix are in place; this
   is confirmation, not new work.
3. Remaining, explicitly-not-done `ROADMAP-V3-UX.md` items if the user
   wants V3 continued: **H5** notification center with persistence (needs
   a small backend store — check `optionspilot/notify/` first), **N2**
   chart↔option-chain cross-links, **N4** toast stacking.
4. One deliberately-skipped verification: the order-ticket **fill** path
   (fill → stop-loss pre-arm → position row) was verified only up to the
   risk-gate rejection because the market was closed — worth one
   market-hours pass.
4. Then the standing scope decision (unchanged from before V3): V2-5
   replay engine, V2-6 journal/improvement dashboard, the deferred V2-4
   workspace layout, or letting paper-trading data accumulate.

## What files are currently important?

- `optionspilot/ui/static/index.html` — the entire frontend; V3/V3.1
  touched every tab. The chart lifecycle is the most intricate code:
  `loadChart`/`chRenderData` (with the `chTailUpdate` live-update fast
  path), the `chSig` signature (now includes last-bar OHLCV), the
  history-paging merge (`chMergeHistory`/`chLoadHistoryChunk`), and the
  editable-drawing overlay system (`DRAW` model, `chDrawRender`,
  `chPointerDown/Move/Up`, `chDrawAct`) rendered on the `#ch-draw` canvas.
- `optionspilot/data/base.py` — `validate_candles` is now the single
  sanitization choke point (drops NaN/inf/≤0 OHLC, zeroes bad volume,
  logs); do not weaken it.
- `scripts/chart_check.py` — the 41-check chart regression suite; run it
  (via `verify.ps1`) after any chart change.
- `optionspilot/data/cached.py` — `EMPTY_CANDLE_TTL` and
  `get_candles_stale_ok()` are new; the strict `get_candles` contract is
  unchanged and must stay that way (fail-closed trading).
- `scripts/verify.ps1` — still the "is the repo healthy" command.
- `docs/ROADMAP-V3-UX.md` — the audit + scope this sprint implemented;
  the unimplemented remainder lives there.

## What should NOT be modified?

See `AI_CONTEXT.md` "Things future AI assistants should never change
without careful review." New this session: **do not relax
`CachedProvider.get_candles` to serve stale data** — the stale fallback
exists only behind `get_candles_stale_ok()` for display surfaces, and the
engine's empty-means-skip behavior is load-bearing for trading safety.

## Known issues

- **`docs/ARCHITECTURE-MOBILE.md` is still untracked** — the mobile
  architecture proposal from a prior planning session; commit or discard
  when the user decides. Everything else this session is committed.
- `OptionsPilot.exe serve` (the windowed exe running the browser-serve
  subcommand) starts its internals but never binds the port —
  pre-existing, discovered 2026-07-18 while verifying the packaging fix.
  Desktop `ui` mode and dev-repo `python -m optionspilot serve` both
  work. Tracked in `TODO.md`.
- The Trade tab's fill-path UX (post-fill stop-loss pre-arm) has no
  market-hours verification from this session (see above).
- Frontend coverage remains shallow relative to the app's size
  (`browser_check.py` is tab-navigation only) — the session's per-flow
  Playwright scripts live in the session scratchpad, not the repo; making
  them permanent is a natural follow-up (see `TODO.md`).
- No CI / linting — still deliberate, still just recommended
  (`CONTRIBUTING.md`).

## Suggested first prompt for the next AI session

> Read `docs/AI_CONTEXT.md`, `CLAUDE.md`, and this file, then run
> `git log --oneline -10`, `git status`, `git branch --show-current`, and
> `git diff --stat` yourself — note that V3 work lives on the `v3-ui`
> branch, not `main` — then run `.\scripts\verify.ps1` to confirm the
> baseline is green. Then: [either "the v3-ui branch is approved — merge
> it to main," or a specific next task]. If no task is given, ask whether
> the V3 branch has been reviewed before proposing anything built on it.
