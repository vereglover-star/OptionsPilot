# CHANGELOG.md

Major features by development phase. Committed history is authoritative for
exact dates/diffs (`git log`); this file summarizes intent and scope for
someone who doesn't want to read 12 commit bodies.

## [Uncommitted] 2026-07-22 — V3.3.1: chart reliability investigation (blank-chart root cause)

*Version 0.3.3 → 0.3.4. 388 tests (+1 backend), chart_check 41 → 44. A pure
root-cause investigation of the intermittent "switch symbols enough times and a
chart loads blank and stays blank until restart" report. The chart was
instrumented (fetch start/finish/superseded/abort/empty, gen, cache, render,
timers), the failure reproduced under load + fault injection, and each cause
traced to a concrete mechanism before any code changed. No new features.*

**Root causes found (each a lifecycle/resource bug, not a rendering bug):**
- **No timeout on the chart's data fetch → permanent blank.** Under a backend
  throttle backlog (measured: concurrent fetches serialize through yfinance's
  single 0.15s-per-request lock, pushing latency to 10–15s+) or a hung upstream
  connection, an unbounded `fetch()` left the first-paint loading spinner up
  forever — exactly "loads blank, stays blank until restart" (restart cleared
  the backlog). Confirmed: after 6s of a slow backend the overlay was still a
  spinner with no recovery.
- **Superseded fetches were never cancelled.** Each symbol switch fired a fetch;
  a rapid burst left every superseded request running to completion, and because
  each holds a slot in the serialized throttle, the pile-up starved the ONE
  symbol the user actually landed on — "after enough switching, charts stop
  loading." (A 12-switch burst now aborts 11 fetches instead of running all 12.)
- **Backend `yfinance.history()` had no request timeout.** A hung Yahoo
  connection blocked the worker thread while it held the CachedProvider's
  per-key in-flight slot, so every later request for that symbol piled up behind
  it — a second "restart fixes it" path.
- **A hung history fetch left `CH.historyLoading` stuck true forever**, silently
  disabling all further history loading for the session.
- **Malformed payloads (duplicate / out-of-order bar times) threw an uncaught
  "Value is null" from lightweight-charts' own later paint frame** — uncatchable
  by the `setData` try/catch. (The backend `validate_candles` already
  dedupes+sorts, so real data never triggers it; this is defense-in-depth.)
- **Backend `_mem` cache was unbounded** — one entry per distinct
  (symbol, timeframe, ext), accumulating candle DataFrames over a long session.

**Fixes (root-cause, not retries):**
- Frontend chart fetch now uses an `AbortController`: a 15s timeout converts a
  hung/slow load into the normal recoverable error path (error overlay +
  existing auto-retry on first paint; keep the chart on a refresh) instead of a
  permanent spinner, and the previous in-flight fetch is aborted on every new
  load/switch so superseded requests stop consuming the throttle. Same treatment
  for the history fetch (with the timeout also clearing the stuck-flag path).
- Backend `yfinance.history()` gained a `REQUEST_TIMEOUT` (10s) so a hung
  connection can't block the throttle-lock holder; failures fall through to the
  next symbol variant / an empty frame the caller already handles.
- `chEnsureMonotonic()` sanitizes any non-ascending/duplicate bars before
  `setData` (fast O(n) no-alloc scan on the normal clean path), and the rAF
  overlay loop is wrapped so a transient render throw can't kill the loop.
- Backend `_mem` cache is now a bounded LRU (`MEM_CACHE_MAX=400`).
- Tests: `chart_check` +3 (hung-backend timeout→recover, rapid-switch abort,
  non-monotonic sanitize) and a backend `TestMemCacheBounded`. Verified: 250
  rapid symbol switches = 0 blanks / 0 console errors; fault injection (empty /
  malformed / flapping) recovers 7/7; memory plateaus; all prior 41 checks green.

**Provider limitation (confirmed, not fixed here):** yfinance serializes all
requests through one process-wide throttle, so heavy concurrent load (scan loop +
rapid chart switching) still adds latency. The bounded fetch makes this
*recoverable* rather than a permanent blank; a real-time streaming provider (the
documented upgrade path) would remove the serialization entirely.

## [Uncommitted] 2026-07-20 — V3.3: chart stabilization & market validation

*Version 0.3.2 → 0.3.3. 387 tests, chart_check 36 → 41. A correctness sprint
verified against LIVE market data (reproduced Monday during regular trading
hours), not just tests. Every issue was reproduced in a real browser, root-caused,
fixed at the architecture level, and re-verified in the browser and in the
rebuilt exe. Two behaviours turned out to be **yfinance provider limitations,
not app bugs** — documented, not papered over.*

- **Live sync cadence (Issue 1).** The forming candle updated in visible ~30s
  chunks because the refresh was a fixed 30s poll on top of a 20s backend TTL.
  The refresh is now timeframe-adaptive (≈7s for minute frames while the market
  is open, slower for hourly/daily, idle when closed) and re-arms on every load
  so a tf switch adopts the new cadence immediately; `CANDLE_TTL` for fine
  intraday frames was lowered in lockstep so a fast poll returns fresh bars.
  **Provider limit (documented):** yfinance is poll-only (no streaming) and
  returns the *current forming bar* as a flat placeholder with `volume=0` until
  it completes, so a true tick-by-tick forming candle like TradingView's is not
  possible on this feed — it needs a streaming provider. Completed bars match
  yfinance to the cent/share and arrive within one poll of the minute closing.
- **Timezone (Issue 2).** The x-axis and crosshair rendered in UTC (a 13:00-ET
  bar showed "17:00"). Bars carry true UTC-epoch timestamps and are NOT shifted
  (that would ripple into drawings/history/timeIndex); instead the *labels* are
  formatted in America/New_York via `Intl` (`tickMarkFormatter` +
  `localization.timeFormatter`). Daily bars are anchored at ET midnight, so the
  same ET conversion yields the correct calendar date with no off-by-one.
- **Candle countdown timer (Issue 3).** New TradingView-style "time until this
  bar closes" pill, updated every second, computed from the bar boundary and the
  real wall clock (not a faked data timer). Shown for intraday frames while the
  market is open; hidden on daily/closed.
- **Drawing render lag on zoom/pan (Issue 4).** lightweight-charts fires a
  time-range event on horizontal pan but NONE for vertical changes (price-axis
  drag, autoscale), so the drawing overlay froze on vertical moves and snapped
  later (reproduced: 0 overlay redraws during a price-axis drag). Added an
  animation-frame sync loop that redraws the overlay whenever the chart's
  coordinate mapping changes — horizontal or vertical — so drawings stay glued
  to the chart. Idle cost is one cheap compare per frame; skipped when off-screen.
- **Drawing creation preview (Issue 5).** The first click now anchors the drawing
  and shows it immediately, and the second endpoint rubber-bands to the cursor
  until the finalizing click (was: nothing appeared until the second click).
  Preview is purely visual — never added to the model, hit-tested, or persisted.
- **Refresh discarded paged-in history & moved the viewport (Issues 7 & 8, the
  key root cause).** The periodic refresh re-fetches only the base window; it was
  replacing `CH.data` with it, discarding older bars the user had scrolled in —
  collapsing e.g. 2025 bars back to ~470 and shifting every logical index, which
  yanked the viewport and made scrolled history randomly vanish. The refresh now
  MERGES the fresh recent window onto the retained older bars (`chMergeRefresh`),
  and the pre-fetch cache paint is restricted to genuine symbol/timeframe
  switches. History is preserved across refreshes and the viewport holds exactly.
- **Verified-not-regressed (Issues 6, 9, 10, 11, 12).** Drawing persistence
  across timeframes (same object renders on 1m→1d, never duplicated); candle
  correctness (app matches yfinance bar-for-bar across SPY/AAPL/NVDA); blank
  charts (all 13 named symbols incl. BRK.B render; invalid symbols show the error
  overlay, not a silent blank); memory (heap plateaus, canvas count and payload
  cache bounded — no leak across 80 symbol/timeframe switches); Auto Follow
  (OFF by default, manual pan disables, Latest re-enables) — all reproduced with
  real mouse against live data.
- **Tests.** `chart_check` 36 → 41: America/New_York display, countdown timer,
  drawing creation preview, overlay tracking a real vertical price-axis drag, and
  a refresh preserving paged-in history + holding the viewport — each fails on
  pre-V3.3 code. Also hardened the flaky "viewport recovery" check (its extreme
  overscroll strand was clamped non-deterministically by the library depending on
  the live bar count; a narrow whitespace window strands deterministically).

## [Uncommitted] 2026-07-20 — V3.2.2: viewport ownership unification + Auto Follow

*Version 0.3.1 → 0.3.2. 387 tests, chart_check 33 → 36. V3.2.1 fixed three
symptoms (drawings, viewport snapping, tf-context loss); every NEW bug
reported afterward (random recentering, history intermittently failing,
losing viewport while scrolling) was another symptom of the same underlying
conflict — the viewport had no single owner. This sprint finds and fixes the
architecture, not the symptoms.*

- **Viewport ownership audit.** Every `fitContent()` / `setVisibleLogicalRange()`
  / `setVisibleRange()` / `scrollToRealTime()` call site in `static/index.html`
  was enumerated by owner and reason (Reset, Latest, Auto Follow, tf-switch
  focal restore, same-key refresh, history prepend, symbol switch). All of
  them now go through one function, `chMoveViewport()` — no subsystem calls a
  `timeScale()` mutator directly anymore.
- **Bug 4 (one controller).** `chMoveViewport(fn)` is the single gate that sets
  `CH.restoringViewport` around every sanctioned move; the range-change
  subscription is the only place that reads it to tell a programmatic move
  apart from a real user pan/zoom/drag.
- **Real root cause of "history loading intermittently fails" (Bug 2, part
  1).** The old `wheel`/`touchstart`/`pointerdown` listeners armed history a
  DOM-event tick *after* the library's own range-change fired during the same
  pan, so a scroll-into-history sometimes silently did nothing. Fixed by
  arming directly off the range-change subscription itself, in the same
  synchronous pass that decides whether the change was user-driven.
- **Real root cause of "history loading intermittently fails" (Bug 2, part
  2 — the deeper one).** Instrumenting the vendored lightweight-charts'
  actual callback timing showed `subscribeVisibleLogicalRangeChange` does
  **not** fire synchronously inside `setVisibleLogicalRange()`/`fitContent()`
  — it fires on a *later* animation frame. Every "sanctioned" move that reset
  `CH.restoringViewport` synchronously right after its call (the V3.2.1
  pattern) closed the guard window *before that callback ever arrived* — so
  one frame later, every guarded move looked like a user pan: silently
  re-arming history-load and (see Bug 3 below) breaking Auto Follow the
  instant it was enabled. Fixed by deferring the reset two animation frames
  in `chMoveViewport`, past when the callback reliably fires.
- **Auto Follow (Bug 3) — new TradingView-style "go to realtime" toggle.**
  OFF by default: the user owns the viewport; nothing auto-recenters except
  Reset and Latest. ON: the chart always keeps the newest bar in view across
  refreshes, live tail updates, and switches. Manual pan/zoom (detected in
  the range-change subscription) turns it back off; pressing Latest turns it
  back on; the preference persists (`localStorage`). New `#ch-follow` button
  next to Reset/Latest, and the `A` keyboard shortcut.
- **`scrollToRealTime()` discovery.** Auto Follow initially wouldn't stay on:
  turning it on and immediately re-checking showed it already false. Root
  cause: unlike `setVisibleLogicalRange()`/`fitContent()`, `scrollToRealTime()`
  runs a multi-frame SMOOTH-SCROLL ANIMATION — every intermediate animation
  tick fired the range-change subscription, and by the second or third tick
  the (2-frame) guard window had already closed, so the animation's own
  motion was read as a user pan and immediately disabled Auto Follow before
  the scroll even finished. Fixed by replacing `scrollToRealTime()` everywhere
  with `chScrollToLatest()` — a single non-animated `setVisibleLogicalRange`
  call computed to land on the same destination — sidestepping the animation
  entirely instead of chasing its frame count.
- **Bug 5 (history prepend stationarity).** Verified (not just assumed): a
  history prepend must never move bars already on screen — only new, older
  bars appear at the left. Covered by a new regression test that captures the
  on-screen time range immediately before and after a real-drag-triggered
  merge.
- **Tests.** `chart_check` 33 → 36: a real drag pan with no `historyArmed`
  manual-set "cheat" (exercises the exact race the Bug 2 fix closes) plus
  on-screen stationarity; Auto Follow OFF-by-default/toggle/persist/manual-pan-
  disables/Latest-re-enables; live tail updates respecting Auto Follow
  ON vs OFF. Also hardened `chart_check.py` itself: the extended-hours route
  stub could occasionally double-fulfill the same request (a pre-existing
  test-harness race, unrelated to app logic) — now defensively swallowed; and
  added a `window.__chNoAutoRefresh` test-only escape hatch so the chart's
  30s background refresh timer can't race a route stub mid-teardown once a
  suite run's wall-clock time exceeds that cadence.

## [Uncommitted] 2026-07-20 — V3.2.1: critical chart regression fixes

*Version 0.3.0 → 0.3.1. 387 tests, chart_check 31 → 33. Three release-blocker
regressions that the V3.2 tests reported as "fixed" but the real app still hit —
because the tests measured internal state, not what the user sees. The V3.2.1
tests assert user-visible behaviour (actual coordinates, actual viewport).*

- **Drawings STILL disappeared across timeframes (Bug 1).** V3.2 made the
  visibility *filter* timeframe-independent, and the test checked
  `chDrawVisible().length` — which passed. But a drawing anchored on 1m has bar
  times that are NOT bars on 5m/1d, so `chX()` fell through to
  `timeToCoordinate()`, which returns null for a non-bar time → the drawing
  painted nothing (painted_px = 0). Root cause verified by pixel count, not the
  filter. Fix: `chX()` maps any timestamp to a fractional logical index
  (`chLogicalAt`, interpolating between bracketing bars) and — because the
  vendored lightweight-charts' `logicalToCoordinate` returns 0 for FRACTIONAL
  indices but maps INTEGER ones fine (even off-screen, extrapolating) —
  interpolates the pixel between the two bracketing integer-bar coordinates.
  Drawings now render on every timeframe.
- **Timeframe switching lost chart context (Bug 3).** V3.1-RC3 made switches
  `fitContent` (to kill a stale-zoom bug); that threw the user's place away — a
  switch jumped to an unrelated date (measured 82-day drift). Fix: capture the
  focal date region before the switch and re-center the new resolution on it
  (`chCaptureFocal`/`chApplyFocal`), clamping each endpoint to the nearest real
  bar so a finer timeframe's shorter history lands on the "closest candle"
  instead of exploding the window. Recent focal now preserved with ~0 drift; a
  focal older than the destination's history lands on its earliest bar.
- **Viewport auto-reset fought the user (Bug 2).** A same-key refresh restored
  the *time* range, which is null when panned into whitespace past the newest
  candle — so the chart snapped back. Fix: same-key refresh preserves the
  LOGICAL range (always defined), captured before `setData`. The stranded
  auto-fit net now fires only on a symbol-switch/first-paint fallback, never
  over a refresh or a deliberate focal restore — Latest/Reset remain the only
  auto-recenters.
- **Root cause tying Bugs 1+3 together: history-load corruption.** `setData`
  fires the visible-range subscription (its auto-fit) *before*
  `restoringViewport` was set, so `chMaybeLoadHistory` ran mid-switch and
  prepended history, shifting logical indices and corrupting drawings AND the
  viewport (n grew 468→806→1248). Fix: set `restoringViewport` before `setData`,
  and disarm history on a switch (history loads on a user SCROLL, never on a
  timeframe/symbol switch).
- **Tests.** `chart_check` 9b now asserts a drawing's anchor coordinates
  RESOLVE (finite, distinct) on every timeframe — it fails on the old x=0 bug.
  New 9d (focal-date preserved across a cascade, no jump/sliver) and 9e
  (viewport not yanked by a refresh while panned past newest; Latest works).

## [`62cbcb4`+`409cfc0`+`9721e1f`] 2026-07-19 — V3.2: chart-system completion + Extended Hours

*Version bumped 0.1.0 → 0.3.0. 387 tests, chart_check 29 → 31. The final
evolution of the chart subsystem before Replay Mode / AI Visualization /
Mobile / Broker work begins.*

- **Timeframe-independent drawing engine (PARTS 1/2/5).** Drawings used to
  vanish on a timeframe switch because the model was tf-LOCKED
  (`chDrawVisible` filtered on `it.tf === CH.tf`). The v3 model stores each
  drawing once with a `visibility` policy ("all" by default, or {min,max}
  tf-rank bounds), `createdTf` metadata, a `source` tag (user/ai/replay), and
  freeform `meta`; the renderer decides per-timeframe whether to show an object
  and never mutates or destroys it on a switch. Legacy v1/v2 drawings migrate
  to visibility "all". One creation entry point — `chAddDrawing(spec)`, exposed
  on `window` — serves the user tools today and the AI scanner / replay engine
  later, so there is exactly one drawing engine and no parallel implementations.
- **Ray tool (PART 2).** Two-click, starts at the first point, passes through
  the second, and extends infinitely past it (clamped to the canvas edge). It
  reuses the existing select/drag/endpoint/recolor/width/lock/hide/duplicate/
  delete/persist machinery — no isolated implementation.
- **Extended Hours (PART 4).** Confirmed first that yfinance reliably supplies
  pre-/after-market candles via `history(prepost=True)` for every intraday
  interval (04:00–20:00 ET). `extended_hours` is a display-only opt-in threaded
  provider→cache→`candles_payload`→`/api/candles?ext=1`, kept off the trading
  path so paper execution is unchanged; ext frames are cache-keyed separately
  and bypass the on-disk store. `optionspilot/data/sessions.py` classifies each
  bar (pre/rth/post) by US-Eastern time; the payload tags bars and computes
  indicators on the session-correct series. Frontend: an "Ext" toggle
  (persisted, disabled on daily) plus TradingView-style pre-market/after-hours
  shading on the overlay canvas. Architecture is provider-agnostic so a future
  feed (Polygon/broker) can supply the same data without a chart rewrite.
- **Version (PART 8).** `0.1.0` → `0.3.0` in pyproject + `__init__`; the footer
  and About surface read it from `/api/status`.

## [`60f16a4`] 2026-07-18 — V3.1 RC3: final release blockers

*376 tests, chart_check 27 → 29. Reproduced the user's exact manual
workflows before touching code; each fix has a browser test that fails
before it and passes after. The key process lesson: RC2's toolbar test set
`DRAW.sel` in JS, bypassing the real select→click path, so it couldn't have
caught what the user hit — the RC3 tests drive the real mouse.*

- **Drawing toolbar actions "still broken" — root cause was a STALE EXE, not
  the source.** Driving the *real* mouse (draw a trendline, click it to
  select, click the toolbar) confirmed the source fix works. But the shipped
  `dist/OptionsPilot` bundle was built Jul 18 12:02 — before RC1/RC2 — so its
  `index.html` has none of the toolbar/viewport/banner fixes. On that build,
  select/drag/resize work while recolour/duplicate/lock/hide/delete no-op —
  exactly the reported symptoms. Fix: **the exe was rebuilt** from fixed
  source. The regression test was rewritten to drive the real mouse end to
  end (draw→select→recolour/width/duplicate/lock/hide/delete), verified to
  fail on the pre-fix source (colour unchanged, selection cleared) and pass
  after.
- **"Live data unavailable" appeared far too often — banner flapping.** With
  the market open and the feed intermittently rate-limited (stale/fresh/
  stale), the yellow banner re-raised on every stale tick even though the
  newest bar never changed — the same current data, warned about repeatedly.
  Root cause: the banner keyed off each fetch's instantaneous `stale` flag.
  Fix: a per-(symbol·tf) high-water mark (`CH.freshHigh`) of the newest bar
  we've shown from a successful fetch; a stale payload only warns when its
  newest bar is genuinely OLDER than that. Same-or-newer ⇒ we still hold the
  current data ⇒ no banner. Verified: alternating stale/fresh on the same bar
  now yields zero banner re-shows (was 4/8); a genuinely-older bar still
  warns.
- **Timeframe switching zoomed into a sliver (one candle).** Each (symbol·tf)
  cached its own viewport and restoring it on switch snapped the chart back to
  whatever tight zoom you last left there — so 5m→1m→5m dropped you onto ~5
  candles. Fix: viewport restoration now has exactly ONE owner — a switch
  (symbol or timeframe) always lands on a sensible default (fit); only a
  same-key refresh preserves the live viewport. The per-key viewport cache was
  removed entirely (dead once switches stopped restoring it). Verified: every
  switch across 1m/2m/3m/5m/…/1d now shows tens-to-hundreds of bars, never a
  sliver.
- **Stuck loading-overlay / skeleton legend on a rapid symbol burst.** Found
  while hardening the tests: a rapid switch (SPY→…→QQQ) where earlier
  first-paint symbols raise the "loading" overlay + skeleton legend, and the
  final already-cached symbol's refresh comes back empty, left the overlay and
  legend stuck even though data was on screen. Fix: a non-first-paint load now
  clears the overlay and restores the legend from the data it already holds.
- **Tests.** `chart_check.py` → 29 checks: the real-mouse toolbar test
  (replacing the JS-state one), an anti-flap banner test, and a
  timeframe-switch tiny-zoom test; the stale-banner test now simulates a
  GENUINELY-behind feed (dropped trailing bars) and its routes serve one
  captured payload instead of re-hitting the live feed (deterministic).

## [`6f3643d`] 2026-07-18 — V3.1 RC2: final chart release audit

*376 tests (+2), chart_check 27 (+6). The last stabilization pass before
`v3-ui` merges to `main`. Four remaining chart bugs, each reproduced in a
real browser, root-caused, fixed at the architecture level, and re-verified.
No redesign, no new chart library, no feature work.*

- **Drawing edit-toolbar actions were dead** (recolour / duplicate / lock /
  hide / width / delete all no-op'd). Root cause: the edit toolbar floats
  *inside* `#ch-main`, so the capture-phase `pointerdown` there fired before
  a toolbar button's own click. The click landed on the toolbar, not on the
  drawing, so `chPointerDown` took its "clicked empty space → deselect"
  branch and cleared `DRAW.sel`; by the time the button handler ran,
  `chSelItem()` was null. Fix: the capture handler ignores events originating
  in `#ch-draw-bar`, leaving the selection intact for the control's handler.
- **The "Live data unavailable — showing cached bars" banner over-fired.**
  It shows whenever a live fetch fails and disk-cached bars are served, but
  while the market is CLOSED those cached bars already ARE the last session —
  identical to what a live fetch would return — so the banner is a false
  alarm that flaps every time a background refresh trips Yahoo's rate
  limiter. Fix: `/api/candles` now reports `market_open` (computed from the
  existing `Orchestrator.market_open`); the frontend suppresses the banner
  while closed (a closed market shows a normal last-session chart, exactly
  as the non-stale closed case does) and shows it — a genuine "you're behind
  live prices" warning — only while open. Two backend tests lock the field
  and the stale-path behaviour in.
- **The chart could strand the user.** Lightweight-charts clamps pan/zoom so
  the view is never literally empty, but bars could still be shoved to the
  far edge behind a screen of whitespace ("the chart disappeared"). Added
  **Reset view** (fit all bars) and **Latest** (jump to the newest bar)
  controls, bound to **R** and **L**; a whitespace-aware `chViewportStranded()`
  detector (logical-range based, so it survives the whitespace case that
  makes `getVisibleRange()` null) that distinguishes a stranded view from a
  deliberate deep zoom; and a render-time safety net that fits content when a
  restored viewport lands off-data — but never on a same-key refresh, so it
  can't yank a viewport the user chose.
- **Random viewport jumps on indicator toggle.** Enabling RSI/MACD recentred
  the main chart: the two-way main↔subpane range sync let a freshly-created
  pane's auto-fit-on-first-`setData` (full history) push its range back onto
  main. Fix: the **main chart is the sole owner** of the visible time range;
  subpanes are one-way followers, realigned to main's range after their data
  is set (`chAlignPane`). Reproduced as [166,205] → [0,191] before the fix,
  unchanged after.
- **Tests.** `chart_check.py` +6 checks: toolbar actions mutate the object,
  indicator toggle leaves the viewport put, Reset/Latest rescue a stranded
  chart, the banner tracks market state, a rapid-abuse stress burst stays
  render-clean, and a new-bar append (the market-hours rollover) grows the
  series without a view jump. `test_ui_server.py` +2 (`market_open`).
- **Performance / market-hours audit.** Evidence-gathered, not speculative:
  one candle request per symbol load (no duplicate fetches), stable canvas
  count across 15 Trade↔Charts reparents (no leak), single chart instance;
  the pane-sync fix also removed a viewport-churn feedback loop. The live
  forming-candle / new-bar / indicator paths were exercised with simulated
  ticks (market closed); see PROJECT_STATE for the market-hours checklist.

## [`3a56145`] 2026-07-18 — V3.1 RC1: stabilization & hardening polish

*374 tests (+1). A release-candidate polish pass over the V3.1 chart work —
no new features, no redesign. A full code/stability/performance audit,
fixing only legitimate findings; the chart architecture is unchanged.*

- **Dead code removed.** The four `CH.priceLines`/`trendSeries`/`fibLines`/
  `rectSeries` arrays were orphaned when V3.1-4 moved drawings onto the
  overlay canvas — declared but never referenced; removed. No source
  TODO/FIXME/XXX markers exist, and every chart/trade function is called.
- **localStorage corruption can't brick the app or the chart.** `chInds`
  (parsed at script-eval) and per-symbol chart drawings (parsed mid-render)
  went through bare `JSON.parse`; a corrupt/hand-edited value threw — the
  first would fail app init, the second the chart for that symbol. A new
  `safeParse` helper resets a bad key to its default and continues.
- **Refreshes never yank the chart out from under an interaction.** The 30s
  auto-refresh (and the new visibility refresh) now skip while a drawing is
  being dragged, a two-click tool is mid-placement, a note is being typed,
  or history is loading — a full re-render then would have moved the bars
  under the cursor. It runs on the next idle tick.
- **Prompt refresh on wake.** After minimize/sleep/tab-away (when
  `document.hidden` suppressed the interval), a `visibilitychange` handler
  refreshes the chart on return instead of waiting up to a full cadence.
- **Bounded payload cache.** The per-(symbol·timeframe) payload cache was
  unbounded — each entry can hold a full paged candle payload (hundreds of
  KB), so a long session flipping through many symbols grew memory without
  limit. It's now an LRU capped at 24 entries (a re-fetch on eviction, never
  a correctness issue).
- **WebSocket frame parse hardened.** `ws.onmessage`'s `JSON.parse` is now
  guarded (a malformed frame is ignored, not thrown). Confirmed the server
  resends a full payload on every new connection (its change-digest starts
  empty), so a dropped-and-reconnected client catches up automatically — a
  new backend test (`test_ws_sends_full_payload_then_heartbeats_when_idle`)
  locks that contract in.
- **Regression coverage expanded.** `chart_check.py` gains two checks
  (corrupt-localStorage recovery, LRU cache bound) → 21 headless-browser
  checks; the WS-contract backend test brings the suite to 374.

Live market-hours items remaining (architecture verified capable; only real
market data can confirm) are enumerated in the RC1 manual-validation
checklist: forming-candle updates, new-bar creation, indicator recompute,
price/stop/target line movement, and option-chain refresh cadence (the
chain currently refreshes on demand — symbol change / Load / expiration /
post-order — with no auto-refresh timer, a deliberate choice to revisit if
market-hours use warrants it).

## 2026-07-18 — V3.1: chart-system stabilization sprint (`v3-ui`)

*373 tests (+17). A dedicated sprint making the charting system
production-ready — the strongest part of the app instead of the weakest.
Seven committed milestones, each root-caused (not papered over) and
browser-verified before commit.*

- **V3.1-1 chart reliability (`b93eac9`).** Root-caused the "some tickers
  randomly fail / IWM only shows volume" reports. Three distinct causes,
  each reproduced first: a stored drawing with a stray price drove the
  price scale and crushed the candles to a ~4px line (fixed with
  `autoscaleInfoProvider: () => null` — drawings never scale the axis);
  NaN volume on the forming bar raised in `int()` and, worse, during JSON
  serialization *after* the endpoint try/except, 500-ing the chart
  (`validate_candles` now zeroes non-finite volume, drops NaN/inf/≤0 OHLC
  rows, and logs every removal with symbol/timeframe context); and
  non-finite values poisoned computed indicators (payload runs through a
  single `validate_candles` choke point and `math.isfinite` guards). The
  frontend render block is wrapped so a renderer exception surfaces an
  error overlay instead of a half-painted canvas.
- **V3.1-2 expanded timeframes (`0d2c870`).** 1m/2m/3m/5m/10m/15m/30m/
  1h/2h/4h/1d/1w/1mo (6 → 13), table-driven so a new interval is one line
  per layer. A single `_TF_LABEL` table is the source of each wire label;
  `_FETCH_SPEC` maps to native yfinance intervals + resample rules (3m/10m/
  2h/4h are resampled); `_WINDOW_DAYS`/`CANDLE_TTL` gain matching entries;
  a test fails if any enum member isn't wired in all four.
- **V3.1-3 infinite historical scroll (`98551e1`).** The paging machinery
  existed but the merge was inverted — scrolling left *replaced* the
  window with an older one (206 → 202 bars) — and the trigger compared a
  logical bar-index against a Unix timestamp. Fixed: older bars are
  prepended in front of the visible ones with indicator series in lockstep
  (206 → 407), the trigger fires within N bars of the left edge, viewport/
  zoom/drawings are preserved, and a left-edge pill shows "Loading history"
  / "Start of available history".
- **V3.1-4 editable drawing objects (`917d0c9`).** Replaced one-shot,
  uneditable drawings with a first-class object model
  (`{id,type,tf,points,color,width,text,locked,hidden}`, stored
  `{version:2,items:[]}`, old format migrated) rendered on a `#ch-draw`
  overlay canvas: select, drag-move, endpoint-resize, color, width, lock,
  hide, duplicate, rename (notes), delete, persistence. Tools arm
  synchronously (instant). Interaction runs on capture-phase pointer
  listeners that freeze chart pan only while a drawing is grabbed.
- **V3.1-5 collapsible synced Trade chart (`edfe2bc`).** The one chart
  instance is relocated between the Charts tab and a collapsible Trade-tab
  slot, so symbol/timeframe/drawings/indicators are shared for free.
  Hidden by default, preference remembered; follows the ticket symbol.
- **V3.1-6 live-update correctness + performance (`5e04506`).** The
  refresh signature hashed only bar times, so the forming candle froze
  during market hours; `chSig` now includes the last bar's OHLCV. A
  live-update fast path pushes only updated/appended trailing bars via
  `series.update()` — no setData, no reflow, zero flicker; full setData is
  reserved for symbol/tf switches, history prepends, and window slides.
- **V3.1-7 automated chart regression suite (`2bcb84a`).**
  `scripts/chart_check.py` drives 19 headless-browser checks (loading,
  invalid ticker + recovery, all 13 timeframes, indicators, the full
  drawing lifecycle, scroll-back, zoom, stale banner + retry, rapid symbol
  changes, resize, live update, single-instance leak guard); wired into
  `scripts/verify.ps1`. Verified: all 10 required tickers × 13 timeframes
  return monotonic real data (130/130).

## 2026-07-18 — Packaged exe shipped without yfinance: lazy import invisible to PyInstaller

*Release-blocking regression found by the user in the
freshly built exe: every chart, quote, and option-chain request failed
with "No module named 'yfinance'". Root-caused and fixed at the packaging
layer; no trading logic changed.*

- **Root cause.** The performance pass (`f1bae42`, pre-V3, on `main`)
  deferred the yfinance import behind `importlib.import_module()` to cut
  app startup from 3.1s to 0.9s. PyInstaller discovers dependencies by
  statically scanning `import` statements, so the dynamic import is
  invisible to it — from that commit on, every exe built by
  `scripts/build_exe.ps1` silently omitted yfinance (and its entire
  dependency tree, including `curl_cffi`) from the bundle. The build
  itself cannot fail for this: the missing module only surfaces at
  runtime, on the first data request. It stayed latent because no
  post-`f1bae42` exe had its data path exercised until now — the V3
  pre-merge audit rebuilt the exe and verified the build *completed*,
  but the market was closed and launching the exe was left as a listed
  manual test. Not a V3 UI regression: V3-0's error surfacing is what
  made the failure visible (verbatim error + Retry) instead of a
  silently blank canvas, and the dev venv was never affected.
- **Fix.** `--collect-all yfinance` in `scripts/build_exe.ps1`, which
  also pulls yfinance's own (statically declared) dependency tree.
  Verified: `yfinance 1.5.1` and `curl_cffi` physically present in
  `dist/OptionsPilot/_internal`; the rebuilt exe serves 206 daily SPY
  candles, 624 SMCI 5m candles (the exact request from the failure
  logs), and a 231-contract chain, `stale: false` throughout.
- **Why it can't silently regress.** Three independent guards: (1) a new
  `selftest` CLI command (`OptionsPilot.exe selftest`) forces every
  lazily-imported dependency offline and exits nonzero if one is
  missing; `build_exe.ps1` now runs it against the freshly built exe and
  fails the build on a bad bundle. (2) New `tests/test_packaging.py`
  scans the source tree for dynamic third-party imports and fails the
  ordinary test suite if any isn't explicitly collected by the build
  script — catching the bug class (someone adds another lazy import)
  long before anyone builds. (3) A companion test asserts the build
  script still *runs* the packaged selftest, per the "a gate that isn't
  wired in protects nothing" lesson.
- **Chart-system regression sweep** (all green, zero console errors):
  first-load overlay → candles, rapid timeframe-switch race, invalid
  symbol → error overlay → Retry → recovery, indicators
  (EMA/VWAP/BB/RSI/MACD), fib/zone/note drawing tools + persistence +
  clear, position/order price lines, stale-banner path (forced via
  request interception) + return-to-live, and the 30s auto-refresh.
- **Known limitation discovered en route** (pre-existing, not fixed
  here): `OptionsPilot.exe serve` — the *windowed* exe running the
  browser-serve subcommand — starts its internals but never binds the
  port. Desktop `ui` mode (the default; verified) and dev-repo
  `python -m optionspilot serve` (verified) are unaffected. Logged in
  `TODO.md`.

## 2026-07-17 — V3-7: pre-merge audit — cache threading bug, chart auto-retry, key guard

*352 tests (+1). A senior-review pass over the whole `v3-ui` changeset
before merging to `main`, which found and fixed three real issues:*

- **`CandleCache` was unusable from worker threads — the disk candle
  cache silently never worked in the live app.** `sqlite3.connect()`
  defaults to `check_same_thread=True`, and the connection is created on
  the main thread — but every candle fetch in serve/desktop mode runs on
  a ThreadPoolExecutor worker (parallel scans) or a FastAPI threadpool
  thread (`/api/candles`). Every cross-thread `store`/`load` raised
  `ProgrammingError`, which callers' best-effort `except` blocks
  swallowed. Consequences: warm restarts always re-downloaded, and
  V3-0's stale-chart fallback would have returned empty in production
  (blank-chart error state instead of clearly-flagged stale bars).
  Fixed with `check_same_thread=False` plus an explicit lock serializing
  all connection use; proven with a before/after cross-thread script and
  a new multi-threaded regression test (`test_usable_from_other_threads`),
  plus an end-to-end reproduction of the production scenario (provider
  built on main thread, dead network, stale fallback served correctly
  from a worker thread). Pre-existing bug — exposed because the audit
  traced V3-0's fallback path all the way down.
- **Chart auto-retry never fired for a failed *first* load.** The 30s
  refresh loop gated on "data has loaded at least once," so the exact
  scenario V3-0 exists for (app opens, feed down, error overlay shown)
  recovered only via the manual Retry button. The gate is now "chart
  initialized," verified by watching the retry request fire from the
  error state in a real browser.
- **Enter could submit an order from behind the `?` shortcut overlay.**
  The order-entry key guard checked only the confirm modal; it now also
  suppresses B/S/+/−/Enter while the shortcut reference is open.
  Browser-verified both ways.

## 2026-07-17 — V3-6: accessibility & discoverability — skip link, live regions, ? overlay

*351 tests (unchanged — markup/CSS/small JS only).*

- **Skip-to-content link** (first focusable element, visible on focus).
- **Toast messages are now a polite live region** (`role="status"
  aria-live="polite"`) — order fills, rejections, and mode switches get
  announced to screen readers instead of appearing silently.
- **All 51 table headers** across every screen now carry `scope="col"`.
- **`aria-current="page"`** tracks the active nav tab (statically for the
  initial Dashboard state, dynamically on every switch).
- **`?` shortcut-reference overlay**: every keyboard affordance in the
  app (tabs, chart, order entry, watchlist) on one card; Esc or a click
  outside closes it.
- **Watchlist drag-handle affordance**: the ≡ handle now brightens on
  row hover instead of sitting permanently faint — the row's
  drag-to-reorder capability is visible before reading the caption.
- **Verified** in a real browser: ?-overlay open/close, aria-current
  follows tab switches, toast live-region and skip link present in the
  DOM; full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-5: analytics presentation — Coach, Journal, Backtest, Learning

*351 tests (unchanged — all four are frontend presentation over existing
endpoints; every new chart/branch is derived client-side).*

- **Coach**: a first-run explainer replaces the bare "0 reviews" stat —
  three numbered steps (trade in Human Mode → close a round trip → get
  scored on process) with a one-click "Switch to Human Mode" button; it
  disappears permanently once the first review exists.
- **Journal**: a cumulative-P&L curve panel (appears from the second
  closed trade), plus symbol/direction/win-loss filters with a live
  "N of M trades" count — all over the already-loaded trade list, no new
  requests.
- **Backtest**: results now include a drawdown-from-peak chart, a
  win/loss split on the trades card, and a "by exit reason" breakdown
  (count, win rate, total P&L per exit type) — the shape of report a
  desk would actually read. Verified by running a real 25-day SPY
  backtest through the UI (zero trades at the conservative bar is the
  correct outcome; the full layout renders).
- **Learning**: the evidence-weights table gains a centered shift bar per
  row — green right of center where learning has boosted a weight above
  its default, red left where it damped one — making the bounded
  0.25×–2× learning rule visible at a glance.

## 2026-07-17 — V3-4: settings redesign — structured config cards replace the JSON dump

*351 tests (unchanged — frontend only; the config stays read-only in-app
by design, matching the startup-validated `config.yaml` philosophy).*

- The raw `JSON.stringify` dump — the app's single biggest visual outlier
  (flagged in `ROADMAP-V3-UX.md` as C1) — is now a grid of grouped cards,
  one per config section (data/indicators/engine/risk/broker/notify/
  integrations/logging), each with a plain-English description of what
  the section controls and where its live counterpart lives (e.g. "the
  watchlist itself is managed on the Watchlist tab").
- Booleans render as ✓ on / – off; the two live-trading gate flags render
  as 🔒 off with a tooltip stating they're off by design with no live
  adapter in the build — the safety posture is now visible in the UI, not
  buried in a JSON blob.
- A search box filters across every section/key/value, hiding empty
  cards as it narrows.
- The restart-to-change rule is stated once, inline, next to the search —
  only where it's actually true (the Trading-mode panel above it remains
  fully live, unchanged).
- **Verified**: lock rendering and search behavior driven in a real
  browser (search "confidence" → 1/69 rows, only the engine card left);
  full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-3: trade screen — faster contract selection, risk context, order-entry keys

*351 tests (unchanged — frontend only; all order placement still routes
through the existing risk-gated `/api/orders` path).*

- **ATM quick-picks**: the ticket placeholder is no longer inert text —
  once a chain is loaded it offers "Nearest ATM call/put" buttons that
  select the closest-to-the-money contract in one click (with the
  Calls/Puts toggle following along).
- **Risk context in the ticket**: below the estimated cost, a live line
  shows the order as a % of buying power alongside the configured
  per-trade risk budget, turning amber when the order exceeds it —
  advisory only; the backend gate remains authoritative.
- **Open positions on the Trade tab** (new): a compact live list (You/AI
  chip, unrealized P/L) with a "Close…" action that loads the position's
  own expiration chain, selects its exact contract, and arms the ticket
  as sell-to-close with the position's quantity.
- **Order-entry keyboard shortcuts**: with the Trade tab open and a
  contract selected — B/S switch side, +/− step contracts, Enter opens
  the review modal (Esc already closes it). Documented inline under the
  ticket. Guarded against firing while typing in a field or while the
  confirm modal is open.
- **Verified** end-to-end in a real browser: chain load → ATM pick →
  risk line → keyboard flow → Enter → confirm modal → a real submission
  (correctly and *visibly* rejected by the manual-entry risk gate outside
  trading hours — the gate's toast surfaced as designed) → close-prefill
  flow. Full suite + browser smoke check green, zero console errors.

## 2026-07-17 — V3-2: dashboard redesign — trader-first layout, live side rail

*351 tests (unchanged — presentation + one new derived view over existing
status data; no new endpoints, no trading logic).*

- **Two-column layout** (`.dash-grid`, 2:1): main column — equity curve,
  open positions, the per-symbol AI-confidence meters; side rail — three
  new glanceable panels. Collapses to one column below 1000px.
- **AI opportunities** (new): the strongest current signals sorted
  tradeable-first then by confidence, each with direction chip,
  confidence %, and gate state ("✓ tradeable" / "needs N%") — click opens
  that symbol's chart. Derived entirely from the existing status payload.
- **Watchlist movers** (new): biggest daily changes first, price + colored
  ▲/▼ change, click-through to the chart. Uses the per-cycle quote
  snapshots the orchestrator already publishes.
- **Empty states now teach and act**: equity ("Run a scan now" button),
  positions (mode-aware: "Scan for setups" in AI Mode, "Open the Trade
  tab" in Human Mode), opportunities ("Scan the watchlist"), movers.
  Previously all four were inert one-line texts.
- **Verified**: populated end-to-end by running a real scan cycle in the
  scratch browser session (opportunities, movers, meters all live), plus
  full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-1: design system foundation — tokens, icon nav, responsive layout

*351 tests (unchanged — presentation only). Second V3 milestone: the shared
visual language every subsequent screen redesign builds on, plus a real
layout-overflow bug found and fixed by the new narrow-viewport check.*

- **Design tokens** (`:root`): a nine-step type scale (`--fs-xs`…
  `--fs-hero`) replacing ~75 ad hoc pixel font sizes (13 distinct values
  consolidated to 9 with sub-pixel-class visual drift); a spacing scale
  (`--sp-1`…`--sp-6`); three elevation levels (`--sh-1` resting cards,
  `--sh-2` popovers, `--sh-3` modal/toast) now applied to panels, cards,
  the autocomplete popover, the confirm modal, and toasts; `--r-pill`.
- **Icon navigation**: nine hand-authored inline SVG stroke icons
  (offline-safe, `currentColor`, no icon font or CDN) added to the nav
  rail alongside the labels.
- **Responsive collapse**: below 1180px the sidebar becomes a 56px icon
  rail (tooltips carry the labels, the logo shrinks to "OP", the PAPER
  TRADING badge turns vertical) — the header pills now fit on one row at
  1024px instead of wrapping and clipping.
- **Real bug fixed — flex/grid min-width blowout**: `main` is a flex item,
  and its implicit `min-width:auto` let the option-chain table push the
  whole layout wider than the viewport at ≤1280px (header clipped off
  screen rather than wrapping — pre-existing, exposed by the first
  narrow-viewport screenshot of the Trade tab). Fixed with `min-width:0`
  on `main` and `minmax(0,1fr)` grid columns; wide tables now scroll
  inside their own panel, never the page.
- **Verified**: 351 tests, HTML id check, browser smoke check (9 tabs,
  zero console errors), plus before/after screenshots at 1024/1280/1600px.

## 2026-07-17 — V3-0: chart reliability — root cause fixed, never-blank canvas

*351 tests (+6). First milestone of the V3 product-quality sprint (branch
`v3-ui`, planned in `ROADMAP-V3-UX.md`). The app could open with no usable
chart; instrumented diagnosis (not guesswork) found a three-part root
cause, each part fixed and separately verified.*

- **Root cause 1 — negative-cache poisoning (`data/cached.py`)**: yfinance
  returns an *empty frame* on transient failures (rate limits, hiccups) —
  indistinguishable from "no data" — and `CachedProvider` memoized that
  empty for the full timeframe TTL (up to 60s), so healthy retries kept
  being served the failure. Proven with a controlled fake provider before
  fixing. Empty results now expire in `EMPTY_CANDLE_TTL` (3s) — long
  enough to stop a hammering loop, short enough that recovery is instant.
  Good data keeps the full TTL.
- **Root cause 2 — no stale fallback**: the SQLite candle cache was never
  consulted when the live fetch failed, so disk full of yesterday's bars
  still meant a blank chart. New `CachedProvider.get_candles_stale_ok()`
  (display surfaces only) falls back to disk data of any age, flagged
  `(frame, is_stale)`. The strict `get_candles` path is byte-for-byte
  unchanged for the engine — fail-closed trading semantics preserved and
  covered by a test asserting the strict path still returns empty in the
  exact state where the stale path serves data. `/api/candles` now
  reports `stale`/`as_of`, and the Charts tab shows a warning banner with
  the last bar's date and a "Retry live data" button.
- **Root cause 3 — frontend failure handling**: `loadChart()` had no
  `catch` (a network error left a stuck skeleton and a blank canvas
  forever), no retry affordance, and a `CH.loading` guard that silently
  dropped symbol/timeframe switches issued mid-load. Rewritten around a
  request generation counter: the newest request always wins, rapid
  switches can't interleave or be dropped, every failure path lands in a
  visible state — a loading overlay (spinner + symbol) on first paint, an
  error overlay with a Retry button on failure, and for an
  already-rendered chart a stale banner instead of wiping the canvas.
- **Live refresh**: a visible chart now refreshes every 30s (cadence
  matched to the backend candle TTLs), preserving zoom/pan (`fitContent`
  only on symbol/timeframe change), pausing when the tab is hidden, and
  doubling as an automatic retry after failures. Drawing/trade-line
  restore paths were audited for idempotency under the refresh loop (both
  already fully remove before re-adding — no series leaks).
- **Verified**: full suite green (351), plus a 5-scenario Playwright
  run in real Edge — first-load overlay→candles, rapid-switch race
  (last click wins), invalid-symbol error overlay with working Retry,
  recovery to a valid symbol, and a same-key refresh keeping the chart —
  zero console errors.

## 2026-07-17 — Developer automation: scripts/, browser checks, doc-consistency checks

*345 tests (unchanged — no trading logic touched, per the session's explicit
scope). A repository-wide review for repetitive manual developer tasks,
turned into a `scripts/` automation layer with one clear responsibility per
script.*

- **`scripts/_common.ps1`**: shared bootstrap (`Ensure-Environment`) that
  every other script dot-sources — creates `.venv` if missing, installs the
  package editable with the requested extras, idempotent.
- **`scripts/dev.ps1` / `test.ps1` / `verify.ps1` / `docs.ps1` / `build.ps1`
  / `release.ps1` / `clean.ps1`**: start the app, run tests (with an
  exit-code-derived `TESTS: PASS`/`FAIL` line that can't be fooled by the
  documented terminal-output-swallowing trap), run every automated check in
  one command, check documentation consistency alone, build the exe
  (test-gated, wraps the untouched `build_exe.ps1`), run the full
  release-readiness pipeline (never commits/tags/pushes — prints the exact
  manual commands instead), and remove dev/build clutter without touching
  `data/`/`logs/`.
- **`scripts/check_html_ids.py`**: the static `index.html` `$("id")`
  reference check, previously ad hoc, now committed.
- **`scripts/check_docs.py`**: confirms every `docs/*.md` cross-reference
  resolves, that "current state" docs' claimed test counts match a live
  pytest count, and that `pyproject.toml`'s version agrees with
  `optionspilot/__init__.py`'s. Caught a real stale example on its first
  run (`CLAUDE.md`'s commit-message template hardcoded `"296 tests"`) —
  fixed by making the example describe the process instead of a number.
- **`scripts/browser_check.py`**: a committed, repeatable version of prior
  sessions' ad hoc Playwright verification. Launches the app against a
  scratch data directory, drives the system's installed Edge
  (`channel="msedge"`, no download), visits every tab, fails on any
  console error. Soft-skips if the new optional `[browser]` extra isn't
  installed. Found a real bug on its first run: `/favicon.ico` 404ing was
  the only console error — fixed by copying `assets/optionspilot.ico` into
  `optionspilot/ui/static/favicon.ico` (already bundled everywhere
  `ui/static/*` is) and serving it. Also found and fixed a bug in itself:
  scratch temp directories weren't reliably cleaned up because a Windows
  file handle can linger briefly past `subprocess.wait()` returning —
  fixed with a bounded retry instead of silently swallowing the error.
- **`scripts/bump_version.py`**: keeps `pyproject.toml` and
  `optionspilot/__init__.py`'s version strings in sync — the same class of
  drift `check_docs.py` guards against for test counts.
- Two new optional `pyproject.toml` extras: `build` (`pyinstaller` —
  previously installed ad hoc and undeclared anywhere, the same gap
  `Pillow` had before an earlier session fixed it) and `browser`
  (`playwright`).
- New `docs/QUICK_START.md` (minimum steps to start productive work) and
  `docs/RELEASE_CHECKLIST.md` (the exact release process, automated where
  possible, explicit about what stays a manual, human-approved step).
- `CONTRIBUTING.md`, `AI_CONTEXT.md`, `ARCHITECTURE.md`, `TODO.md`,
  `PROJECT_STATUS.md`, `NEXT_SESSION.md`, `README.md`, and `CLAUDE.md` all
  updated to reference the new scripts consistently rather than the old
  raw commands, and to record the concrete lessons from building this
  (PowerShell's `2>&1`-plus-`-Stop` native-stderr trap; Windows subprocess
  file-handle timing) in `AI_CONTEXT.md` "Common mistakes to avoid."

## 2026-07-17 — Documentation & AI development framework

*345 tests (unchanged — a documentation-and-workflow session, no trading
logic touched). Commit `1029fb0`.*

- New `docs/PROJECT_STATUS.md`: a structured, dashboard-style snapshot
  (version, milestones, features, known bugs/limitations, priorities, test
  count) distinct from `PROJECT_STATE.md`'s session-by-session narrative.
- Rewrote `docs/ROADMAP.md` into a unified Completed / In Progress /
  Planned / Deferred / Long-term Vision structure, absorbing the stale
  v1-only content it previously had; `docs/ROADMAP-V2.md` stays as the
  detailed per-phase checklist it already was.
- Expanded `docs/ARCHITECTURE.md`: an explicit directory tree, five Mermaid
  diagrams (component map, AI-engine pipeline, risk-gate flowchart, cycle
  sequence diagram, build pipeline), and dedicated sections for Charts,
  WebSockets, Settings, and the build pipeline (including
  `optionspilot_app.py`, previously undocumented).
- New `docs/AI_CONTEXT.md`: the permanent-memory document — vision, design
  philosophy, standards, future desktop/mobile plans, and a "Common
  mistakes to avoid" section recording real incidents from this repo's
  history so they don't repeat.
- New `docs/NEXT_SESSION.md`: the concise session-handoff format (what was
  completed, what's stable, what's next, what files matter, what not to
  touch, known issues, a ready-to-paste first prompt).
- New `docs/CONTRIBUTING.md`: coding/commit/testing/documentation
  conventions, Definition of Done, pre-commit checklist, and a first pass
  at automation recommendations (superseded/expanded by the automation
  session above).
- Fixed real staleness found during the audit: `README.md` claimed "Phase 1
  of 8 complete" and 225 tests while omitting the Trade/Coach/Charts tabs
  entirely; `CLAUDE.md` and `AI_HANDOFF.md`'s reading-order pointers
  updated to route through the new files.

## 2026-07-17 — V2-4 finish: trade lines + fib/zone/note tools; manual entries risk-gated

*345 tests. Completes the tractable remainder of the V2-4 drawing/overlay
scope, and finishes the manual-entry risk-gating work found uncommitted
from the 2026-07-16 session. The full three-panel workspace layout and
multi-chart layouts stay deferred (a larger design decision — see
`ROADMAP-V2.md`).*

**Manual entries now pass through the RiskManager** (completing work left
uncommitted and unwired by the previous session — its `_entry_veto`
refactor, `approve_manual_entry`, and `OrderManager.evaluate`'s fill-time
`approve_entry` callback existed but the immediate market-buy path in
`UIServer.place_order` never called them, so a halted account could still
buy):
- `RiskManager.approve_manual_entry`: every hard gate the AI has — halt,
  weekend/hours window, daily trade limit, max open positions (skipped
  when scaling into an already-held contract), cooldown after loss, max
  contracts (counting the existing position) — plus quantity/premium
  validity. The engine's %-risk position sizing is deliberately advisory
  only for manual trades: sizing a user-directed trade is the user's
  call, and oversizing is the coach's job to flag (`oversized` tag), not
  the risk manager's to block. The %-budget comparison is still computed,
  logged, and surfaced in `RiskDecision.notes`.
- `UIServer.place_order` preflights immediate market buys through
  `Orchestrator.approve_manual_entry` (422 with the veto text), and
  delayed working-order fills are approved at trigger time by
  `OrderManager.evaluate`'s callback (rejected orders cancel with the
  veto as the result). Manual fills are recorded against the daily trade
  limit via `register_manual_entry(entry_ts=...)`.
- New `TestManualEntry` unit tests in `tests/test_risk.py` (halt, hours,
  daily limit, max-contracts scaling, oversize-allowed-with-note,
  invalid inputs) alongside the endpoint-level halt test.

- **Position/order lines on the chart**: loading a chart now draws labeled
  price lines for that symbol's open positions — entry spot (blue, solid),
  working stop (red, dashed) and target (green, dashed), all in underlying
  space (`Position.entry_spot`/`stop_current`/`target`) — plus the
  underlying-level triggers of working manual orders (stop-loss/take-profit
  levels and the live trailing-stop level, orange dashed). LIMIT orders are
  premium-space and deliberately not drawn on an underlying chart. Each
  line is labeled with the position/order size and strike (e.g.
  "stop 2× 580C"). Backend: the status payload's positions now include
  `entry_spot` (was already persisted on `Position`, just not exposed).
- **Three new drawing tools** alongside Level/Trend: **Fib** (click swing
  start then swing end → 0/0.236/0.382/0.5/0.618/0.786/1 retracement
  levels as labeled dotted price lines), **Zone** (click two corners → a
  supply/demand rectangle drawn as top/bottom edges spanning the two
  bars), and **Note** (click a bar, type text in an inline input, Enter →
  a labeled square marker above that bar). All persist in localStorage per
  symbol+timeframe like trend lines; the existing Clear button removes
  them; old stored drawings load unchanged (missing keys default empty).
- Esc now cancels the active drawing tool anywhere on the Charts tab.
- Hygiene (from `TODO.md`): `pyproject.toml` `package-data` now ships
  `data_assets/*` in wheels/sdists; `Pillow` added to the `dev` extra
  (needed by `scripts/make_icon.py`); `engine.operating_mode` documented
  inline in `config.yaml` matching `trading_mode`'s comment style.

## 2026-07-16 — V2-4 core: interactive chart workspace

*338 tests. The Charts tab ships the core of the V2-4 roadmap phase.*

- Vendored TradingView's lightweight-charts 4.2.3 (Apache-2.0) at
  `ui/static/lightweight-charts.js` — served locally, fully offline, no
  CDN, bundled into the exe by the existing `--add-data ui\static` line.
- New `GET /api/candles?symbol&tf`: OHLCV plus indicator series (EMA×3,
  VWAP, Bollinger, RSI, MACD) computed by the SAME `analysis/` functions
  the engine trades with — what you see charted is exactly what the scorer
  saw. Provider-only (no orchestrator lock), so chart loads never contend
  with a running scan; ~8ms warm through the CachedProvider.
- New Charts tab (keyboard: 2): candlestick + volume chart with zoom/pan/
  crosshair and an OHLC+change+volume+indicator legend, five timeframes
  (5m–1D), indicator pills (EMA/VWAP/Bollinger overlays, RSI and MACD as
  height-synced subpanes), fullscreen (F), and drawing tools — horizontal
  levels persisted per symbol, trend lines persisted per symbol+timeframe,
  one-click clear.
- Trade-from-chart plus deep links everywhere: watchlist symbols, dashboard
  confidence meters, and position cards all open the chart; "Trade →" jumps
  to the ticket with the symbol loaded.
- Workflow: after a market buy fills, the ticket pre-arms itself as a
  protective stop-loss (side/type preset, level focused) — the single most
  common coach finding (`no_stop`) is now one keystroke to avoid.
- Accessibility: visible focus rings, aria-labels on icon-only controls.

## 2026-07-16 — Performance & polish pass (no new features)

*335 tests. Scan cycle profiled and optimized end-to-end; Trade tab and
dashboard redesigned in a modern-brokerage style; UI never blocks during
scans. Soak: warm cycles 0.1s, zero heap growth.*

**Performance (measured 5-symbol watchlist):**
- Profiled the cycle first: 83% of the old 14.9s was 25 *serial* candle
  fetches gated by the provider's 0.5s self-throttle; the rest was
  re-running the full analysis suite on unchanged frames.
- New `data/cached.py` `CachedProvider`: timeframe-aware candle TTLs,
  5s quote / 30s chain / 1h expirations memos, concurrent-request dedup,
  write-through to the SQLite `CandleCache` (`data/cache.db`) for warm
  restarts. Wraps `YFinanceProvider` by default; fake test providers
  bypass it.
- `Orchestrator.fetch_watchlist_candles()`: all (symbol × timeframe) pairs
  fetch in parallel (8 workers) with a per-symbol progress callback;
  `run_cycle(candles=...)` accepts the prefetched frames. Provider throttle
  lowered 0.5s → 0.15s (request count is now tiny).
- `MultiTimeframeAnalyzer` memoizes one view per (symbol, timeframe) on a
  data fingerprint — unchanged frames skip the entire indicator/pattern/
  smart-money rebuild. `candlesticks.detect_all` computes shared bar
  geometry once instead of per-detector. `evaluate()` ~495ms → ~76ms cold,
  ~0ms warm.
- Cycle time: 14.9s → 4.5s cold, **~0.1s warm** (the "Scan now" case).
- `/api/scan` is now non-blocking by default: the cycle runs on a
  background thread, candle fetching happens OUTSIDE the orchestrator
  lock, and progress (`scan.done/total`) streams over `/ws`; watchlist
  quotes tick in per-symbol while the scan runs. `{"wait": true}` keeps
  the old synchronous behavior for scripts/tests.
- `/ws` pushes at 1s with change detection — full payload only when
  something changed, else a tiny heartbeat the frontend ignores. Journal
  reads for the status payload are cached by a new `TradeJournal.revision`
  counter instead of rescanning SQLite every push.
- Startup: yfinance import deferred to first use; core import time
  ~3.1s → ~0.9s.

**UI/UX (single-file, no-build architecture unchanged):**
- Modern brokerage restyle: refreshed dark palette and type scale, tabular
  numerals everywhere, hover/press transitions, tab-switch animation,
  reduced-motion support.
- Dashboard: portfolio-value hero with today's P/L; open positions as
  cards (big colored P/L, qty/avg/mark/stop/target, Close with a
  confirmation dialog).
- Trade tab redesign: horizontal expiration pills with DTE labels, sticky-
  header chain with colored bid/ask and an inline spot-price row marker
  (auto-scrolled into view), selected-contract card with large mid price,
  Buy/Sell segmented control, quantity stepper, live estimated cost/credit,
  and a full order-confirmation modal before anything is placed.
- Skeleton loaders on chain/journal/coach/learning/metrics; DOM writes
  diffed (`setHTML`) so unchanged sections never re-render; keyboard
  shortcuts 1–8 switch tabs.

## 2026-07-16 — V2-3: AI Mode vs Human Mode

*310 tests. Frontend live-verified in a real browser (mode toggle + persistence
across reload, Coach tab empty state, full manual round trip → coach review
rendered with expandable detail, mode-axis orthogonality) against a scratch
data directory before committing. Exe rebuilt with V2-3 and the packaged
app smoke-tested the same day.*

- `EngineConfig.operating_mode`: `"ai"` (default, autonomous trading) or
  `"human"` (AI scans and advises only; never places an order). Instant,
  no-restart switching via `RuntimeSettings.set_operating_mode()`,
  independent of `trading_mode`.
- `Orchestrator`: in Human Mode, tradeable signals become one-time "advice"
  notifications per bar instead of orders.
- New manual-trade reconciliation loop: detects opened/closed
  `managed_by="manual"` positions cycle-to-cycle, captures analysis context
  while open, rebuilds the round trip from broker fill + order history on
  close, journals it, and generates a `TradeCoach` review.
- New `optionspilot/coach/` package:
  - `coach.py` — `TradeCoach.review()`: before/during/after breakdown,
    14-tag mistake taxonomy (each with a pro-comparison note and a concrete
    exercise), **process-based** 0–100 score (deliberately rewards
    discipline over luck — a stopped-out loser with a plan outscores a
    reckless winner).
  - `profile.py` — `CoachProfile.build()`: aggregates all reviews into
    recurring mistakes, strengths, score trend, win rate by setup quality,
    recommended exercises.
- New API: `POST /api/operating_mode`, `GET /api/coach`.
- New UI: header AI/Human segmented toggle, Coach tab (cards, mistakes
  panel, strengths/exercises panel, expandable review detail).
- New tests: `tests/test_coach.py` (13 tests), `tests/test_human_mode.py`
  (mode switching + full manual round-trip integration).

## 2026-07-16 — V2-1 & V2-2: windowed desktop app + manual trading engine

*Commit `0ce001d`, roadmap update `bec78fb`. 296 tests.*

**V2-1 — true desktop application:**
- PyInstaller `--windowed` build: no console window on launch.
- Generated candlestick app icon (`scripts/make_icon.py` →
  `assets/optionspilot.ico`).
- Single-instance guard: a localhost-port mutex; a second launch shows a
  friendly notice window instead of two processes fighting over the same
  SQLite files.
- Logging skips the console `StreamHandler` when `sys.stderr` is `None`
  (true in a windowed build).

**V2-2 — order engine + manual trading:**
- New `broker/orders.py` `OrderManager`: MARKET, LIMIT (option premium),
  STOP_LOSS / TAKE_PROFIT / TRAILING_STOP (underlying price levels,
  put-aware direction mirroring), DAY (expires 16:00 ET) / GTC time-in-force,
  position scaling, reservation checks (prevents overselling across bracket
  orders), auto-cancel of exit orders when the position closes first,
  SQLite persistence with restart-safe fills (uses live quotes on restart,
  never stale stored prices).
- `Position.managed_by: "ai" | "manual"` — `PositionManager` (AI) now
  explicitly skips manual positions.
- `PaperBroker.open_manual()` — plan-less entry path for manual trades.
- Equity snapshots persisted per cycle for lifetime max-drawdown / total-
  return metrics.
- New API: `GET /api/chain` (option chain with Greeks + liquidity score for
  the order ticket), `GET/POST /api/orders`, `POST /api/orders/cancel`,
  `GET /api/account/metrics` (buying power, portfolio value, unrealized/
  realized/daily P/L, total return %, win rate, avg win/loss, profit factor,
  max drawdown).
- New Trade tab UI: account metric cards, live option chain browser, full
  order ticket (side/type/qty/TIF/limit-or-stop fields), working orders +
  history tables, one-click position close from the Dashboard.

## 2026-07-16 — Watchlist manager + in-app trading mode toggle

*Commit `0bc3955`. 272 tests.*

- New `config/runtime.py` `RuntimeSettings`: overlays `data/settings.json`
  onto the yaml-loaded config at bootstrap, mutates the live config object
  under the server lock so changes apply on the next cycle with **no
  restart**. Baseline snapshot (pre-overlay yaml values) lets `custom` mode
  restore exact yaml values when switching away.
- New `data/symbols.py` + bundled 12,472-symbol NASDAQ/NYSE directory
  (`optionspilot/data_assets/symbols.csv`) for instant, offline ticker validation and
  autocomplete search.
- Watchlist manager: quick-add with autocomplete, bulk paste parsing
  (comma/space/newline), per-symbol valid/duplicate/invalid reporting, 9
  preset lists (Magnificent 7, S&P 500 Leaders, AI Stocks, etc.) + saved
  Favorites, pin/drag-reorder/sort/filter, keyboard shortcuts, 30-symbol
  cap, background name + market-cap metadata fetch.
- Trading-mode segmented control (Conservative / High-Risk / Custom) in the
  header and Settings tab, with an advanced tuning panel for Custom mode
  (six validated risk/engine fields). Switches apply instantly and persist.
- Build script hardening: bundles `data_assets`, backs up/restores the exe's
  `data/` folder across rebuilds, refuses to build over a running instance.

## 2026-07-14 — Trading modes: Conservative and High-Risk

*Commit `70abb06`. 239 tests.*

- New `engine/gate.py` `TradeGate`: Conservative mode keeps the fixed
  `min_confidence` bar (default 80%). High-Risk mode adapts the required
  confidence to a deterministic *setup quality* classification (excellent/
  good/average/poor) built from evidence composition — poor setups
  (opposing HTF trend, 3+ conflicting indicators, or too few core
  confirmations) never trade at any confidence; entries below the
  conservative bar also require risk/reward ≥ a configurable threshold.
- Every gate decision produces a `GateReport` (quality, threshold used,
  passed/failed confirmations, one-line reason) that flows into logs, scan
  summaries, journal `market_conditions`, and the dashboard.
- Conservative mode's behavior is byte-identical to pre-existing behavior —
  this was an additive change, not a rewrite of the scorer.

## 2026-07-11 — Phase 8: hardening

*Commit `268cac9` (+ `30cd974`, `39640ee`). 225 tests.*

- `scripts/soak.py`: repeated live-cycle harness tracking exceptions, heap
  growth, and per-cycle timing — first run: 8 cycles, 0 failures,
  +0.2 MB heap growth, ~15.5s/cycle.
- `/webhook/tradingview`: secret-validated (constant-time compare),
  config-gated inbound alert endpoint. An alert only *triggers a scan* of
  that symbol through the normal engine + risk pipeline — it can never
  place an order directly.
- `broker/registry.py`: `create_broker()` factory with Alpaca/Tradier/
  Webull/IBKR extension slots that raise `BrokerError` with guidance rather
  than silently no-op-ing; the live-trading gate is re-checked at
  construction time as defense in depth.
- Performance: vectorized the smart-money detectors (numpy instead of
  per-row pandas/`iterrows`) and capped `MultiTimeframeAnalyzer` to the
  trailing 400 bars — backtest time on 520 bars dropped from 7.9s to 4.7s
  with identical trade output.

## 2026-07-11 — Initial commit: phases 1–7

*Commit `40eb1ea`. 204 tests.*

The original v1 build in one commit: multi-timeframe technical/structural/
smart-money analysis suite, confluence-scored AI decision engine
(`ConfluenceScorer`), risk-gated paper execution (`RiskManager` +
`PaperBroker`), SQLite trade journal, bounded/auditable learning system
(evidence-weight tuning from journal history), event-driven backtester
sharing the live engine code, orchestrator + desktop/email notifications,
full CLI (`run`/`scan`/`status`/`journal`/`backtest`/`learn`), and a packaged
desktop dashboard (FastAPI + pywebview + PyInstaller). Paper trading only —
no live-broker code path exists anywhere in this codebase by design.
