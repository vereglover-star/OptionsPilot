# AI_HANDOFF.md — Read this first if you are a new Claude Code session

This document is a complete orientation to OptionsPilot for an AI assistant
that has never seen this codebase before. Read this, then `PROJECT_STATE.md`
(what's done / what's next), then `ARCHITECTURE.md` (how it fits together) —
you should not need to read source files just to get oriented.

## What this project is

**OptionsPilot** is an AI-powered **options paper-trading** desktop
application. It is explicitly **not** a live-trading system — there is no
code path that can place a real order with real money (see "Safety
architecture" below). It:

- Analyzes markets continuously across multiple timeframes using a large
  technical/structural/smart-money analysis library.
- Scores every potential trade 0–100% confidence with a full itemized
  reasoning trail.
- Can trade a simulated account **autonomously** ("AI Mode") *or* let the
  user trade manually with a full order ticket while an AI coach reviews
  every trade ("Human Mode").
- Manages risk (position sizing, daily/weekly loss limits, circuit breakers)
  identically in every mode.
- Journals every trade, learns from the journal (bounded, auditable weight
  adjustments), and can backtest a strategy on historical data.
- Ships as a real Windows desktop app: a single `.exe`, no console window,
  no terminal required to run it.

The user's own words on intent: "a polished, professional desktop trading
platform that combines the best aspects of TradingView, Webull, and
Thinkorswim, while adding an AI trading coach that can both trade
autonomously in AI Mode and teach me in Human Mode."

## Safety architecture (do not weaken this without being asked)

- The only `Broker` implementation is `PaperBroker` — a simulator. Real
  broker adapters (`broker/registry.py`) exist only as named stubs that raise
  `BrokerError` with guidance; there is no live order-placement code at all.
- `BrokerConfig.name` must be `"paper"` unless *both* `live_trading_enabled`
  and `i_understand_the_risks` are set — and even then, no real adapter
  exists to receive the flag. This is enforced by a pydantic validator in
  `config/settings.py`.
- Every subsystem is designed so a future live adapter would slot into the
  same `Broker` interface without touching the engine, risk manager, or UI.
- Do not build a live-trading adapter unless the user explicitly asks for it
  in a dedicated request — it is a deliberate, separate decision gate.

## Application architecture

**Not Electron/Tauri.** This was a deliberate decision (see
`docs/ROADMAP-V2.md` "Architecture decision" section): the backend is Python
(pandas/numpy-heavy analysis engine) and would need to be embedded either
way, so a JS-shell rewrite would only replace the window chrome at the cost
of the existing test suite. The actual shell is:

```
┌─────────────────────────────────────────────────────────┐
│  OptionsPilot.exe (PyInstaller --onedir --windowed)      │
│  ┌─────────────────────────────────────────────────┐    │
│  │ pywebview window  ──HTTP/WS──▶  FastAPI (uvicorn) │    │
│  │ (native OS window,               in a background  │    │
│  │  loads static/index.html)        thread            │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                 │
│                         ▼                                 │
│              UIServer.orch (Orchestrator)                 │
│         owns: engine, risk, broker, orders, journal,       │
│               coach, notifier, provider                    │
└─────────────────────────────────────────────────────────┘
```

Everything lives in **one Python process**. There is no separate frontend
build step — `optionspilot/ui/static/index.html` is a single self-contained
HTML file with inline `<style>` and `<script>` (no React/Vue/bundler, no
`npm`). It talks to the backend exclusively via `fetch()` to `/api/*` REST
endpoints plus one WebSocket (`/ws`) that pushes the full status payload
every second when the payload changed (a tiny heartbeat otherwise, which
the frontend ignores — no re-render).

## Backend architecture

Layered, each layer only depends on layers below it:

```
config/       → layered settings (yaml + env + runtime-mutable overlay)
core/         → domain models (Candle, OptionContract, Signal, TradePlan,
                Position, Order, TradeRecord), logging setup
data/         → market data provider interface + yfinance implementation,
                candle cache, symbol directory (12k tickers), preset lists
analysis/     → PURE FUNCTIONS ONLY, no I/O: indicators, candlestick
                patterns, market structure (BOS/CHoCH), smart money concepts
                (FVG/order blocks/liquidity), volume analysis, options math
                (Black-Scholes, IV solver). Shared verbatim by live trading
                AND the backtester — this is what guarantees backtest/live
                parity.
engine/       → MultiTimeframeAnalyzer → ConfluenceScorer → TradeGate →
                ContractSelector → TradePlanner, composed by DecisionEngine
risk/         → RiskManager: the ONLY path to the broker. All entries pass
                through approve(); exits never do (a stop must always fire).
broker/       → PaperBroker (simulator), OrderManager (manual orders),
                PositionManager (AI stop/target management), registry.py
                (live-broker stubs)
journal/      → SQLite trade record store
learning/     → performance slicing + bounded, auditable weight tuning
backtest/     → event-driven replay through the SAME engine/risk/broker
coach/        → TradeCoach (deterministic post-trade review) + CoachProfile
                (aggregated strengths/weaknesses) — NEW in V2-3
notify/       → desktop toast / email notifications
orchestrator.py → composes everything into one scan cycle; the only class
                   the UI and CLI actually drive
ui/           → FastAPI app (server.py), pywebview shell (desktop.py),
                   static/index.html (the entire frontend)
__main__.py   → CLI: run / ui / serve / scan / status / journal / backtest / learn
```

### The one-cycle data flow (`Orchestrator.run_cycle`)

Called every `engine.scan_interval_seconds` (default 60) while the market is
open, or on demand via `/api/scan`:

1. Fetch candles for every watchlist symbol × timeframe — in parallel,
   through the `CachedProvider` (timeframe-aware TTLs, so most cycles only
   refetch the entry timeframe and daily bars). In the UI server this phase
   runs OUTSIDE the orchestrator lock, so status reads never block on it.
2. **Manage AI positions**: `PositionManager.review()` checks stop/target/
   CHoCH-invalidation/partial-exit for positions where `managed_by == "ai"`.
   It explicitly ignores `managed_by == "manual"` positions (V2-3 change).
3. **Evaluate working orders**: `OrderManager.evaluate()` checks every
   manual limit/stop/take-profit/trailing-stop order against fresh quotes.
4. **Reconcile manual trades**: capture analysis context for open manual
   positions; when one closes, rebuild the round trip from broker fill
   history and hand it to `TradeCoach.review()` (V2-3 — see below).
5. Mark positions to market, update the risk manager's equity, persist an
   equity snapshot (for max-drawdown / total-return metrics).
6. Surface circuit-breaker halts as notifications.
7. **Scan for entries** — for symbols with no open position:
   - `DecisionEngine.evaluate()` → confidence score + `TradeGate` verdict
     (conservative/high-risk/custom quality-adaptive threshold).
   - **If `operating_mode == "human"`**: a tradeable signal becomes a
     one-time "advice" notification. The AI **never places an order.**
   - **If `operating_mode == "ai"`** (default): tradeable signals go to
     `DecisionEngine.build_plan()` → `RiskManager.approve()` →
     `PaperBroker.open_position()`.
8. Check for large moves (notification only).

Exits are never risk-gated — only entries are. This is intentional: a stop
must always be honorable regardless of the daily loss limit.

## Operating modes (there are TWO independent mode axes — don't conflate them)

1. **`operating_mode`**: `"ai"` (default) or `"human"`. Controls *who places
   entries*. Set via `POST /api/operating_mode`, persisted in
   `data/settings.json`, switches instantly, no restart.
2. **`trading_mode`**: `"conservative"` (default) / `"high_risk"` /
   `"custom"`. Controls *the confidence threshold logic* the AI's gate uses
   — this applies whether the AI is placing the trade itself (AI Mode) or
   just advising (Human Mode advice notifications use the same gate).

These are orthogonal: switching `trading_mode` must never flip
`operating_mode` and vice versa — see `RuntimeSettings._apply_mode()`,
which explicitly preserves `operating_mode` when applying a trading-mode
baseline restore.

## Trading logic overview

`ConfluenceScorer` (`engine/scorer.py`) computes 15 evidence signals (HTF
trend, structure breaks, EMA stack, RSI/MACD/ADX, VWAP, volume pressure,
divergence, candlesticks, premium/discount range position, liquidity grabs,
zone confluence), each scored −1..+1 and weighted, damped 25% during
consolidation. `TradeGate` (`engine/gate.py`) then decides tradeability:

- **Conservative**: fixed `min_confidence` bar (default 80%).
- **High-Risk**: bar adapts to a deterministic *setup quality* classification
  (excellent/good/average/poor) built from evidence composition — poor
  setups never trade at any confidence; entries below the conservative bar
  additionally require risk/reward ≥ `high_risk_min_rr_stretch`.
- **Custom**: user-set fixed thresholds (six tunable risk/engine fields),
  validated through the same pydantic models as `config.yaml`.

`ContractSelector` picks a specific option contract (delta target, DTE
window, liquidity filters). `TradePlanner` builds stop/target/partial levels
from market structure (swing highs/lows) with an ATR fallback.

## Paper trading implementation

`PaperBroker` (`broker/paper.py`) is a SQLite-backed simulator:
- Buys fill at ask + slippage; sells fill at bid − slippage; commission per
  contract on both sides.
- `open_position()` is the AI path (takes a `TradePlan`); `open_manual()` is
  the Human Mode path (no plan, `managed_by="manual"`).
- Persists account (cash, realized P/L), positions (including AI stop/
  target/partials OR manual-managed flag), fill history, and periodic equity
  snapshots (`equity_history` table) for lifetime drawdown/return metrics.
- Survives restarts — positions and account state reload from disk.

`OrderManager` (`broker/orders.py`, **new in V2-2**) is the manual order
book: MARKET (immediate), LIMIT (option premium), STOP_LOSS / TAKE_PROFIT /
TRAILING_STOP (underlying price levels, put-aware — mirrors direction),
DAY (expires 16:00 ET) / GTC time-in-force. Evaluated once per scan cycle
against fresh quotes (no intrabar fills — documented limitation of delayed
data). Persisted to `data/orders.db`; sell orders auto-cancel if the
position closes first; reservation checks prevent overselling a position
across multiple bracket orders.

## Trade Coach implementation (V2-3, newest subsystem)

`coach/coach.py` — `TradeCoach.review()` takes a closed `TradeRecord` plus
entry/exit context snapshots (captured by the orchestrator near the moment
of interest — HTF trend, gate verdict, RSI/ADX/rvol, contract Greeks/IV/DTE,
time of day) and the contract's order history, and produces a `CoachReview`:

- **Before-the-trade findings**: setup quality agreement, trend confirmation,
  chased-entry check (RSI extremes), volume sufficiency, DTE/IV/delta
  sanity, position sizing (% of equity), opening-chop timing, revenge-trade
  detection (entered <15 min after a loss).
- **During-the-trade findings**: was a stop ever placed, was a target
  defined, was the stop moved *against* the position, was the position
  averaged down.
- **After-the-trade analysis**: win/loss/scratch verdict, why (direction vs.
  premium decay), held-loser detection (<-50% premium), cut-winner-early
  detection.
- **Score 0–100 — deliberately scores PROCESS, not outcome.** A disciplined
  stopped-out loser scores well; a reckless winner (no stop, counter-trend,
  chased, oversized) scores low. This is a documented design decision, not
  an accident — see the module docstring and `test_coach.py`'s
  `test_disciplined_loser_scores_well` / `test_reckless_winner_scores_badly`.
- **Mistake taxonomy** (`MISTAKES` dict, 14 tags): each tag carries a label,
  a "what a professional would do" note, and a concrete practice exercise.
- Reviews persist as JSON files under `data/coach/<trade_id>.json`.

`coach/profile.py` — `CoachProfile` aggregates all persisted reviews into:
recurring mistakes ranked by frequency, top strengths, score trend
(improving/declining over time), win rate by setup quality, and the top 3
recommended exercises. Rebuilt fresh from disk every time — never drifts
from the underlying evidence.

**Reconciliation loop** (`Orchestrator._reconcile_manual`): manual round
trips aren't journaled at order-placement time — they're detected by diffing
open `managed_by="manual"` positions cycle-to-cycle. Context is captured
while the position is open (best-effort; survives missing data), and on
close the round trip is rebuilt from `PaperBroker.fills_for()` +
`OrderManager.orders_for()`, coached, and journaled with
`strategy="manual"`, `mistakes`, `lessons` (= coach's improvement
exercises), and `market_conditions["coach_score"]`.

## Journaling system

`journal/journal.py` — SQLite `TradeRecord` store. AI-mode trades are
journaled directly by the orchestrator when a position fully closes (using
`_TradeMeta` restart-safe context in `data/state/open_trades.json`). Manual
trades are journaled by the reconciliation loop above. Both paths converge
on the same `TradeRecord` schema and the same `TradeJournal.record()` call,
so `/api/journal` and the Journal tab show AI and manual trades uniformly
(distinguishable by `strategy` field: engine name vs `"manual"`).

## Human Mode vs AI Mode — the exact behavioral contract

| | AI Mode (default) | Human Mode |
|---|---|---|
| Who places entries | `Orchestrator._scan_symbol` → `RiskManager.approve()` → `PaperBroker.open_position()` | User, via Trade tab → `/api/orders` → `PaperBroker.open_manual()` |
| Does the engine still scan? | Yes | Yes — same `DecisionEngine.evaluate()` call |
| What happens on a tradeable signal | Auto-trades | One-time "advice only" notification (never repeated for the same bar) |
| Who manages exits | `PositionManager` (AI stops/targets, `managed_by="ai"`) | `OrderManager` working orders the user places (`managed_by="manual"`) |
| Risk limits (daily loss, position sizing, hours, etc.) | Fully enforced | Fully enforced (same `RiskManager`, same journal-based state rebuild) |
| Coached? | No (learning system tunes evidence weights instead) | Yes — every closed round trip gets a `TradeCoach` review |

Switching `operating_mode` is instant (no restart) and does **not** close
open positions or cancel working orders of the mode you're leaving — an AI
position keeps its AI-managed stop even if you flip to Human Mode mid-trade,
and vice versa. This is intentional (see `PositionManager.review()`'s
`managed_by` guard and `OrderManager`'s independence from `operating_mode`).

## Runtime settings vs config.yaml

Two config layers, by design:

1. **`config.yaml`** (+ `OPTIONSPILOT__SECTION__KEY` env vars) — the
   structural, startup-only configuration: broker, data provider, indicator
   enable flags, logging, integrations. Validated by pydantic in
   `config/settings.py`; invalid values refuse to start.
2. **`data/settings.json`** (via `config/runtime.py::RuntimeSettings`) — the
   in-app-editable overlay: watchlist (+ pinned + favorites), `trading_mode`
   (+ custom-mode tunables), `operating_mode`. Applied on top of the yaml
   config at startup (`RuntimeSettings.apply()`), then mutated live by UI
   actions under the `UIServer.lock`. A `baseline` snapshot (yaml values,
   taken before any runtime overlay) lets mode switches restore exact yaml
   values when leaving `custom` mode.

**Known minor gap**: `operating_mode` is a real, validated `EngineConfig`
field and CAN be set directly in `config.yaml` (`engine: operating_mode:
human`), but this isn't yet documented with an inline comment there the way
`trading_mode` is. Low priority — see `TODO.md`.

## APIs and endpoints (FastAPI, `optionspilot/ui/server.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves `static/index.html` |
| GET | `/api/status` | Full dashboard payload (account, positions, signals, notifications, watchlist, modes, scan progress) — also pushed over `/ws` |
| POST | `/api/scan` | Run one cycle: non-blocking by default (background thread; progress streams in the status payload's `scan` field); `{"wait": true}` for synchronous |
| GET | `/api/journal` | Trade history + stats |
| GET | `/api/learning` | Evidence weights + performance slices |
| GET | `/api/config` | Effective config.yaml values (read-only) |
| GET | `/api/chain` | Option chain for a symbol/expiration (Greeks, liquidity) — manual trading ticket data |
| GET/POST | `/api/orders`, `/api/orders/cancel` | Working manual orders: place/list/cancel |
| GET | `/api/account/metrics` | Buying power, P/L windows, win rate, PF, max drawdown |
| GET/POST | `/api/watchlist*` | Add/remove/reorder/pin/favorites/presets, symbol search |
| POST | `/api/mode` | Switch trading_mode (conservative/high_risk/custom) |
| POST | `/api/operating_mode` | Switch AI Mode ↔ Human Mode |
| GET | `/api/coach` | Coach reviews + aggregated profile |
| POST | `/api/risk/reset_halt` | Manual circuit-breaker reset |
| GET/POST | `/api/backtest` | Backtest job (background thread, polled status) |
| POST | `/webhook/tradingview` | Inbound TradingView alert → triggers a scan (never a direct order) |
| WS | `/ws` | 1s cadence with change detection: full `status_payload()` when something changed, tiny heartbeat otherwise |

All mutating endpoints acquire `UIServer.lock` (an `RLock`) — the
orchestrator is not thread-safe, and this lock serializes the background
cycle-loop thread against API request threads.

## Database / storage approach

Everything is **SQLite + JSON files**, no external database, no ORM:
- `data/paper.db` — account, positions, fills (PaperBroker)
- `data/cache.db` — candle cache (CachedProvider write-through; safe to delete)
- `data/orders.db` — working + historical manual orders
- `data/journal.db` — trade records
- `data/settings.json` — runtime-mutable settings (watchlist, modes)
- `data/state/open_trades.json` — AI trade context (restart-safe journaling)
- `data/state/manual_trades.json` — manual trade context (V2-3)
- `data/coach/<trade_id>.json` — one file per coach review
- `data/learning/weights.json` — versioned evidence-weight history
- `data/reports/` — backtest JSON/HTML reports
- `logs/*.log` — rotating per-subsystem logs

All of `data/` and `logs/` are gitignored — they are per-user runtime state,
never committed.

## Environment variables

- `OPTIONSPILOT__<SECTION>__<KEY>` — overrides any `config.yaml` value, e.g.
  `OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT=0.5`. Parsed via
  `config/settings.py::load_config()`.
- No `.env` file convention exists; no secrets are read from environment
  besides this override mechanism. TradingView webhook secret lives in
  `config.yaml` under `integrations.tradingview_secret`, not an env var.

## Dependencies

Core (`pyproject.toml`): `pandas`, `numpy`, `yfinance`, `pydantic>=2.7`,
`PyYAML`. UI extra: `fastapi`, `uvicorn[standard]`, `pywebview`. Dev extra:
`pytest`, `httpx` (FastAPI TestClient). Notify extra: `windows-toasts`
(optional; falls back to log-only without it). Icon generation
(`scripts/make_icon.py`) needs `Pillow` (not in pyproject — installed ad hoc
during V2-1; **should be added to a `dev` or `assets` extra**, see TODO).

No JS package manager, no `package.json`, no build step for the frontend.

## Build and run instructions

```powershell
# Dev setup
cd optionspilot
python -m venv .venv
.venv\Scripts\pip install -e .[dev,ui]
.venv\Scripts\pip install windows-toasts   # optional, desktop notifications

# Run
.venv\Scripts\python -m optionspilot ui            # desktop window + live loop
.venv\Scripts\python -m optionspilot serve --port 8787   # browser, no window
.venv\Scripts\python -m optionspilot run            # headless loop, no UI
.venv\Scripts\python -m optionspilot scan           # one cycle, print JSON
.venv\Scripts\python -m optionspilot backtest SPY --days 25

# Tests (335 tests as of this writing, all passing)
.venv\Scripts\python -m pytest

# Package as a Windows exe (no console window; data/ preserved across rebuilds)
.\scripts\build_exe.ps1   # -> dist\OptionsPilot\OptionsPilot.exe
```

`scripts/build_exe.ps1` refuses to build over a running instance (open
SQLite handles) and backs up/restores `dist\OptionsPilot\data\` around the
PyInstaller `--clean` wipe. The exe has a single-instance guard
(`ui/desktop.py`) — a second launch shows a friendly "already running"
window instead of corrupting the shared account database.

## Assumptions made during development

- **Free yfinance data is acceptable for v1.** ~15-minute delayed quotes,
  limited intraday history (~60 days of 5m bars). Explicitly documented as
  the upgrade path (paid feed) rather than fixed now.
- **Fills are simulated per scan cycle, not intrabar.** A limit/stop order
  placed between cycles fills (or doesn't) based on the quote fetched at the
  *next* cycle boundary — there is no tick-by-tick simulation.
- **"Screenshots" in the original spec were reinterpreted as re-renderable
  chart-context snapshots** (candle window + entry/exit markers stored as
  data, not PNGs) — this is a deliberate substitution documented in
  `ROADMAP-V2.md`, not yet implemented (V2-6, not started).
- **The AI coach is deterministic, not an LLM call.** Built entirely on the
  existing analysis engine (same code that scores AI trades). No external
  API dependency, fully offline, fully testable.
- **Emotional/behavioral tags (revenge trading, chased entry, etc.) are
  inferred from observable order/timing patterns**, not literal mind-reading
  — documented as an honest limitation in `coach/coach.py`'s module
  docstring.

## Known issues / technical debt

1. `pyproject.toml`'s `package-data` only lists `ui/static/*` — it is
   **missing `data_assets/*`** (the 12k-symbol CSV). This doesn't break the
   PyInstaller build (`build_exe.ps1` explicitly passes
   `--add-data optionspilot\data_assets;...`) or running from the repo
   directly, but a `pip install` of a built wheel/sdist would ship without
   the symbol directory. Fix: add `"data_assets/*"` to `package-data`.
2. `Pillow` (used only by `scripts/make_icon.py`) is not declared in any
   `pyproject.toml` extra — it was installed ad hoc. Low priority since the
   icon is a generated, committed asset (`assets/optionspilot.ico`) that
   rarely needs regenerating, but should be added to a `dev` extra for
   reproducibility.
3. `operating_mode` is not documented with an inline comment in
   `config.yaml` the way `trading_mode` is, even though it's a real,
   settable field. Cosmetic — the field works correctly either way.
4. V2-2's roadmap line item "stock (share) positions" was explicitly
   deferred — the entire trading engine (chain, orders, coach) is
   options-only. Adding shares would need a new `OptionContract`-shaped
   "stock leg" type and touch `broker/orders.py`, `PaperBroker`, and the
   Trade tab chain UI.
5. No automated UI/browser test coverage — `tests/test_ui_server.py`
   exercises the FastAPI layer via `TestClient` (335 tests cover this
   thoroughly), but nothing drives `static/index.html` in a real browser.
   V2-1 through V2-3 frontend surfaces (Trade tab, Coach tab, AI/Human
   toggle) have all been manually live-verified, but there is no regression
   coverage. **If you touch `static/index.html`, manually verify
   in a browser** (see `PROJECT_STATE.md` for the exact verification steps
   used previously) since there's no test safety net for it.
6. (resolved 2026-07-16) The packaged exe now includes V2-3 — rebuilt and
   smoke-tested (mode toggle, manual round trip, coach review) after the
   V2-3 commit. `build_exe.ps1` preserved the app's `data/` across the
   rebuild as designed.

## Future considerations (beyond the current roadmap)

See `docs/ROADMAP-V2.md` "Beyond v1" and "Phases" sections for V2-4 (chart
workspace with `lightweight-charts`), V2-5 (replay engine), V2-6 (journal
screenshots + improvement dashboard) — none of these have been started.
Also: candle cache for the live loop, a real Alpaca paper-API adapter (only
after sustained paper profitability), news/sentiment evidence, portfolio-
level risk (correlated positions).
