# CHANGELOG.md

Major features by development phase. Committed history is authoritative for
exact dates/diffs (`git log`); this file summarizes intent and scope for
someone who doesn't want to read 12 commit bodies.

## [Uncommitted] 2026-07-18 — Packaged exe shipped without yfinance: lazy import invisible to PyInstaller

*356 tests (+4). Release-blocking regression found by the user in the
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
