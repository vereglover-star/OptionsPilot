# CHANGELOG.md

Major features by development phase. Committed history is authoritative for
exact dates/diffs (`git log`); this file summarizes intent and scope for
someone who doesn't want to read 12 commit bodies.

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
