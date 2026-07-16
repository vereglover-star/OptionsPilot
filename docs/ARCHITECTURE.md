# OptionsPilot — System Architecture

**Status:** Living document. Updated as each phase lands.
**Mode:** Paper trading only. Live trading is architecturally possible but gated behind
configuration (`broker.live_trading_enabled`) that defaults to `false` and requires an
explicit, multi-step opt-in that does not exist anywhere in this codebase — there is no
live-broker implementation to enable even if the flags are set.

---

## 1. Design Principles

1. **Safety first.** No code path can place a real-money order. The only broker
   implementation is a simulator. Live adapters, when added, sit behind the same
   `Broker` interface and a hard config gate re-checked at construction time.
2. **Everything is a module behind an interface.** Data providers, brokers, strategies,
   indicators, and notifiers are all pluggable. Swapping yfinance for Polygon, or the
   paper broker for Alpaca, touches one adapter file and one config line.
3. **The same analysis code runs live and in backtests.** The AI engine consumes a
   stream of candles; it does not know whether they come from the live data engine or
   the backtester. This eliminates the classic "backtest says X, live does Y" bug class.
4. **Deterministic and auditable.** Every decision is logged with its inputs. Every
   trade records the full reasoning chain. The AI coach is deterministic too — built
   entirely on the existing analysis engine, not an external LLM call.
5. **Fail closed.** Any error in risk checks, data quality, or broker communication
   halts trading rather than guessing.
6. **Two independent mode axes, never conflated.** `operating_mode` (who places
   entries: AI vs. the human user) and `trading_mode` (how the confidence threshold
   behaves: conservative/high-risk/custom) are orthogonal. Switching one must never
   change the other — enforced explicitly in `RuntimeSettings._apply_mode`.

---

## 2. Component Map

```
┌───────────────────────────────────────────────────────────────────────┐
│                             UI Layer                                   │
│   pywebview desktop window ── FastAPI ── WebSocket live feed           │
│   static/index.html: Dashboard · Trade · Coach · Watchlist ·           │
│                       Journal · Backtest · Learning · Settings         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────┴────────────────────────────────────────┐
│                        Orchestrator                                   │
│  Event loop: fetch candles → manage AI positions → evaluate manual    │
│  orders → reconcile manual round trips (+ coach) → mark/risk → scan   │
│  for AI entries (gated by operating_mode) → notify                    │
└─┬──────┬──────┬────────┬─────────┬─────────┬─────────┬──────────┬───┘
  │      │      │        │         │         │         │          │
┌─┴──┐ ┌─┴──┐ ┌─┴───┐ ┌──┴───┐ ┌───┴────┐ ┌──┴───┐ ┌───┴────┐ ┌───┴───┐
│Data│ │ AI │ │Risk │ │Broker│ │ Orders │ │Coach │ │Journal │ │Notify │
│    │ │Eng.│ │ Mgr │ │(Paper│ │ Mgr    │ │      │ │& Learn │ │       │
│    │ │    │ │     │ │+ PM) │ │(manual)│ │      │ │        │ │       │
└────┘ └────┘ └─────┘ └──────┘ └────────┘ └──────┘ └────────┘ └───────┘
                │
        ┌───────┴────────┐
        │  Backtester    │  (drives AI Engine with historical data)
        └────────────────┘

              config.yaml ──┐
                            ▼
                   RuntimeSettings (data/settings.json)
                   overlays watchlist / trading_mode / operating_mode
                   onto the live config, mutated by UI actions
```

### 2.1 Data Engine (`optionspilot/data/`)
- `MarketDataProvider` (abstract): `get_candles(symbol, timeframe, start, end)`,
  `get_quote(symbol)`, `get_option_chain(symbol, expiration)`.
- `YFinanceProvider`: free provider used in v1. Delayed/EOD-quality data — fine for
  paper trading and backtesting. Rate-limited and cached.
- `CandleCache`: SQLite-backed cache so backtests and repeated scans don't re-download.
- `symbols.py` + bundled `optionspilot/data_assets/symbols.csv` (12,472 NASDAQ/NYSE tickers):
  offline ticker validation and autocomplete search for the watchlist manager.
- `presets.py`: static preset watchlists (Magnificent 7, S&P 500 Leaders, etc.).
- Future adapters: Polygon, Tradier (market data), Alpaca, TradingView webhooks
  (as an *alert input*, since TradingView has no data/trading API).

### 2.2 Analysis Library (`optionspilot/analysis/`)
Pure, stateless functions over pandas DataFrames. No I/O, no side effects — fully
unit-testable and reused verbatim by the live engine, the backtester, AND the trade
coach (see 2.6). This sharing is what guarantees live/backtest parity and lets the
coach reason about a manual trade with the exact same lenses the AI trades with.
- `indicators.py` — EMA, SMA, VWAP, MACD, RSI, Stoch RSI, ATR, Bollinger, Supertrend,
  ADX, OBV, relative volume.
- `candlesticks.py` — engulfing, hammer, shooting star, doji, morning/evening star,
  inside/outside bars, marubozu, three white soldiers / three black crows.
- `structure.py` — swing highs/lows, HH/HL/LH/LL classification, BOS, CHoCH,
  consolidation detection, trend state.
- `smart_money.py` — fair value gaps, order blocks, liquidity pools (equal highs/lows),
  liquidity grabs, premium/discount zones, supply/demand. Vectorized with numpy.
- `volume.py` — volume spikes, buying/selling pressure, volume divergence.
- `options_metrics.py` — Black-Scholes greeks, IV solving, liquidity scoring
  (spread %, OI, volume), expected move.

### 2.3 AI Decision Engine (`optionspilot/engine/`)
- `MultiTimeframeAnalyzer`: runs the analysis library across configured timeframes
  (e.g. 1D/4H/1H/15m/5m), producing a `TimeframeView` per timeframe. Capped to the
  trailing 400 bars per timeframe to bound per-scan cost.
- `ConfluenceScorer`: weighted scoring of 15 evidence signals (trend alignment,
  structure, patterns, volume, momentum, SMC signals) → confidence score 0–100 with an
  itemized reason list. Weights come from config and are later tuned by the learning
  system.
- `TradeGate`: decides tradeability from the score. **Conservative** mode: fixed
  `min_confidence` bar. **High-Risk** mode: the bar adapts to a deterministic *setup
  quality* classification (excellent/good/average/poor) — poor setups never trade at
  any confidence; sub-conservative-bar entries additionally require a stretched
  risk/reward. **Custom** mode: user-set fixed thresholds, validated through the same
  pydantic models as `config.yaml`. Every verdict is a `GateReport` (quality, threshold
  used, passed/failed confirmations, one-line reason).
- `ContractSelector`: given a directional signal, scans the option chain and picks
  the contract by delta target, DTE window, liquidity score, and spread threshold.
  Rejects illiquid chains outright.
- `TradePlanner`: builds a full `TradePlan` — entry, stop (structure/ATR based),
  targets, partial-profit levels, max hold time, invalidation conditions.
- `DecisionEngine`: the facade that composes the above; `evaluate()` always runs
  (even in Human Mode — it's what the AI's "advice" is built from), `build_plan()`
  only matters when something is actually going to trade.

### 2.4 Risk Manager (`optionspilot/risk/`)
A gatekeeper every *entry* must pass through — neither the AI engine nor a manual
order can reach the broker for an entry without it. Enforces: max daily/weekly loss,
max consecutive losses, max drawdown, risk-per-trade %, max contracts, daily trade
limit, trading-hours window, cooldown after losses, and automatic shutdown ("circuit
breaker") on limit breach. Position sizing is computed here (account balance ×
risk % ÷ max loss per contract), never by the strategy. **Exits are never risk-gated**
— a stop must always be honorable regardless of the daily loss limit.

### 2.5 Broker Layer (`optionspilot/broker/`)
- `Broker` (abstract): `open_position`, `close_position`, `get_positions`,
  `get_account`, mark-to-market.
- `PaperBroker`: full simulator — realistic fills using bid/ask with configurable
  slippage, commissions, and partial fills; tracks account equity, positions, P&L.
  Persists state to SQLite so the paper account survives restarts. Two entry paths:
  `open_position()` (AI, takes a `TradePlan`) and `open_manual()` (Human Mode, no
  plan). Every `Position` carries `managed_by: "ai" | "manual"`.
- `PositionManager`: manages AI-owned positions' exits each cycle (stop/target/
  CHoCH-invalidation/partial). **Explicitly ignores `managed_by="manual"` positions**
  — those are the `OrderManager`'s job, never the AI's.
- `OrderManager` (`orders.py`, new in V2-2): the manual working-order book. MARKET
  (immediate), LIMIT (option premium), STOP_LOSS / TAKE_PROFIT / TRAILING_STOP
  (underlying price levels, direction-mirrored for puts), DAY (expires 16:00 ET) /
  GTC time-in-force. Evaluated once per scan cycle against fresh quotes — no
  intrabar simulation (documented limitation of delayed data). Reservation checks
  prevent overselling a position across multiple bracket orders; sell orders
  auto-cancel if the position closes first; fully persisted and restart-safe.
- `registry.py`: `create_broker()` factory. `AlpacaBroker`/`TradierBroker`/
  `WebullBroker`/`IBKRBroker` are named extension slots that raise `BrokerError`
  with guidance — no live-order code exists yet anywhere. Live adapters, when
  built, refuse to initialize unless `broker.live_trading_enabled: true` AND
  `broker.i_understand_the_risks: true` (checked at construction, defense in depth
  even though nothing currently implements either flag's true path).

### 2.6 Trade Coach (`optionspilot/coach/`, new in V2-3)
- `TradeCoach.review()`: takes one closed `TradeRecord` (manual trades only — AI
  trades are tuned by the learning system instead, see 2.7) plus entry/exit context
  snapshots and the contract's order history, and produces a `CoachReview`:
  before-the-trade findings (setup quality agreement, trend confirmation, chased-entry
  detection, volume/DTE/IV/delta sanity, position sizing, opening-chop timing,
  revenge-trade detection), during-the-trade findings (stop placed? moved against the
  position? averaged down?), after-the-trade analysis (why it won/lost, held-loser /
  cut-winner-early detection), a 14-tag mistake taxonomy (each tag carries a
  professional-comparison note and a concrete practice exercise), and a **process-based
  score 0–100** — this is a deliberate design choice: a disciplined stopped-out loss
  scores well, a reckless lucky win scores poorly, because the coach is teaching
  process, not celebrating outcomes.
- `CoachProfile.build()`: aggregates every persisted review (`data/coach/*.json`) into
  recurring mistakes ranked by frequency, top strengths, a score trend over time
  (improving/declining), win rate sliced by setup quality, and the top-3 recommended
  exercises. Rebuilt fresh from disk on every call — never drifts from evidence.
- Reviews are triggered by the orchestrator's manual-trade reconciliation loop (see
  2.9), never called directly by the UI.

### 2.7 Journal & Learning (`optionspilot/journal/`, `optionspilot/learning/`)
- SQLite journal: every trade (AI or manual) stores entry/exit, P&L, confidence
  score, full entry/exit reasoning, market conditions, indicators used, and — for
  manual trades — `mistakes`/`lessons` populated from the coach review and a
  `coach_score` in `market_conditions`.
- Learning system: periodic batch analysis over **AI** trades in the journal — win
  rate and expectancy sliced by indicator, time of day, DTE, delta bucket, market
  regime, strategy. Produces updated `ConfluenceScorer` weights via regularized,
  sample-size-aware updates (no weight moves on < N trades of evidence). All weight
  changes are versioned and logged so learning is auditable and reversible.
  (Manual trades are coached individually instead of feeding the weight tuner — the
  two feedback loops are deliberately separate: AI trades teach the scorer, human
  trades teach the human.)

### 2.8 Backtesting (`optionspilot/backtest/`)
Event-driven backtester that replays cached historical candles bar-by-bar through
the *same* AI engine and risk manager, using the PaperBroker in simulation mode.
Options prices are reconstructed via Black-Scholes from underlying + IV estimates
(documented limitation: historical option chains aren't available for free; the
report flags this). Outputs: net profit, win rate, profit factor, max drawdown,
average win/loss, Sharpe, trade distribution, monthly/yearly returns, equity curve
— as JSON + rendered HTML report.

### 2.9 Orchestrator (`optionspilot/orchestrator.py`)
The event loop: market-hours aware scheduler that, each cycle:
1. Pulls fresh candles for every watchlist symbol/timeframe.
2. Manages AI positions (`PositionManager`, `managed_by="ai"` only).
3. Evaluates working manual orders (`OrderManager.evaluate()`).
4. Reconciles manual round trips: detects newly opened/closed `managed_by="manual"`
   positions, captures analysis context while a position is open, and on close
   rebuilds the trade from fill/order history, journals it, and generates a coach
   review.
5. Marks positions to market, updates risk-manager equity, persists an equity
   snapshot.
6. Surfaces circuit-breaker halts as notifications.
7. Scans flat symbols for new signals. **If `operating_mode == "human"`**: a
   tradeable signal becomes a one-time advice notification, never an order. **If
   `operating_mode == "ai"`** (default): routes through risk → broker exactly as
   before.
8. Checks for large moves (notification only).

### 2.10 UI (`optionspilot/ui/`)
FastAPI backend (`server.py`) + single self-contained HTML/CSS/JS dashboard (no
build step, no bundler), served locally and wrapped in a pywebview native window
(`desktop.py`); packaged with PyInstaller (`--windowed`, no console) → normal
Windows app with a single-instance guard. WebSocket pushes the full status payload
every 2 seconds. Tabs: Dashboard (equity, P&L, confidence meters, positions),
**Trade** (account metrics, live option chain, order ticket, working orders —
manual paper trading), **Coach** (process-score reviews, recurring mistakes,
recommended exercises), Watchlist (quick-add/bulk-paste/presets/pin/reorder),
Journal, Backtest runner, Learning insights, Settings (mode toggle + advanced
custom-mode tuning).

### 2.11 Notifications (`optionspilot/notify/`)
`Notifier` interface; desktop toasts (`windows-toasts`, optional — falls back to
log-only) and SMTP email adapters. Events: trade opened/closed, AI advice (Human
Mode), risk limit hit, order filled/expired/rejected, large move, daily/weekly
summary.

### 2.12 Cross-cutting
- **Config** (`optionspilot/config/`): two layers by design.
  - `settings.py`: structural, startup-only config — packaged defaults → user
    `config.yaml` → environment variables (`OPTIONSPILOT__SECTION__KEY`). Validated
    with pydantic; unknown keys and out-of-range values fail fast at startup.
  - `runtime.py` (`RuntimeSettings`): the in-app-editable overlay — watchlist,
    `trading_mode` (+ custom tunables), `operating_mode`. Persisted to
    `data/settings.json`, applied on top of the yaml config at startup, then
    mutated live by UI actions under the server lock. A `baseline` snapshot lets
    `custom` mode restore exact yaml values on exit.
- **Logging** (`optionspilot/core/logging_setup.py`): structured, rotating file logs
  per subsystem + console output (skipped automatically in the windowed/no-console
  build, where `sys.stderr` is `None`). Every trade decision is reconstructable
  from logs alone.
- **Models** (`optionspilot/core/models.py`): typed domain objects (Candle, Quote,
  OptionContract, Signal, TradePlan, Order, Fill, Position, TradeRecord) shared by
  every module — the vocabulary of the system.

---

## 3. Design Patterns in Use

- **Strategy pattern**: `MarketDataProvider`, `Broker`, and `Notifier` are all
  abstract interfaces with swappable concrete implementations — the rest of the
  system codes against the interface, never the implementation.
- **Facade**: `DecisionEngine` hides the five-stage analyzer→scorer→gate→
  selector→planner pipeline behind two calls (`evaluate`, `build_plan`).
  `Orchestrator` is a facade over the whole application for the UI/CLI.
- **Gatekeeper / chain-of-responsibility-ish**: every entry order must pass through
  `RiskManager.approve()` — no component, AI or manual, has a shortcut around it.
- **Overlay / layered configuration**: `RuntimeSettings` overlays a mutable layer
  on top of the immutable `config.yaml` baseline rather than editing the yaml file
  or maintaining two independent config objects.
- **Event sourcing (light)**: `PaperBroker`'s fill log and `OrderManager`'s order
  history are the source of truth that the manual-trade reconciliation loop
  reconstructs journal entries from — the journal is a derived view, not the
  primary record, for manual trades.
- **Deterministic rules engine over ML/LLM**: both the `ConfluenceScorer` and the
  `TradeCoach` are hand-authored, weighted, auditable rule sets — not black-box
  models. This is a repeated, deliberate choice across the codebase, not an
  omission.

---

## 4. Data Flow (one scan cycle)

1. Orchestrator wakes on schedule (e.g. every 60s during market hours) or on
   demand (`POST /api/scan`).
2. Data Engine returns fresh candles for each watchlist symbol × timeframe (cached).
3. **AI position management**: `PositionManager` reviews `managed_by="ai"`
   positions for stop/target/CHoCH/partial exits.
4. **Manual order evaluation**: `OrderManager.evaluate()` checks every working
   order (limit/stop/target/trailing) against fresh quotes.
5. **Manual trade reconciliation**: context captured for open manual positions;
   closed ones are journaled and sent to `TradeCoach.review()`.
6. Positions marked to market; risk manager equity updated; equity snapshot
   persisted.
7. Circuit-breaker halts surfaced as notifications.
8. For each flat watchlist symbol: `MultiTimeframeAnalyzer` → `ConfluenceScorer` →
   `TradeGate` verdict.
   - **Human Mode**: tradeable signal → one-time advice notification, nothing else.
   - **AI Mode**: tradeable signal → `ContractSelector` → `TradePlanner` →
     `RiskManager.approve()` → `PaperBroker.open_position()` on approval.
9. Large-move detection (notification only).
10. Journal records everything; Notifier fires; UI updates via WebSocket within 2s.

---

## 5. Technology Choices

| Concern        | Choice                     | Why |
|----------------|----------------------------|-----|
| Language       | Python 3.12+                | Ecosystem for market data, pandas, ML |
| Data wrangling | pandas + numpy             | Standard, fast enough for candle-scale data |
| Market data    | yfinance (v1), pluggable   | Free, no API key; adapters for paid feeds later |
| Storage        | SQLite + JSON files (stdlib) | Zero-ops, single-file, perfect for desktop app |
| Validation     | pydantic v2                | Config + model validation, fail-fast |
| API/UI backend | FastAPI + uvicorn          | Async, WebSocket support, well-documented |
| Frontend       | Single static HTML/CSS/JS  | No build step, no bundler, works offline in the exe |
| Desktop shell  | pywebview (WebView2)       | Native window on Win11 without Electron weight |
| Packaging      | PyInstaller (`--windowed`) | One-folder Windows executable, no console window |
| Tests          | pytest                     | Standard; 310 tests as of the V2-3 session |

**Why not Electron or Tauri** (evaluated explicitly during V2-1 planning): the
backend is inherently Python (pandas/numpy-heavy analysis engine) and would need
embedding either way; a JS-shell rewrite would only replace window chrome at the
cost of the existing test suite. Revisit only if multi-window/multi-monitor
layouts become a real requirement (Tauri would be preferred over Electron then).

## 6. Known Limitations (documented deliberately, not oversights)

- yfinance data is delayed (~15 min) and rate-limited; intraday history is limited
  (~60 days of 5m bars). Good for paper trading and strategy development; a paid
  feed (Polygon/Tradier) is the upgrade path for serious intraday work.
- Free historical *option chain* data does not exist; backtests price options via
  Black-Scholes reconstruction and label results accordingly.
- Manual and working orders are evaluated once per scan cycle against fresh
  quotes, not simulated intrabar/tick-by-tick.
- TradingView integration is inbound-only (webhook alerts), by TradingView's design.
- Webull requires official OpenAPI approval; the adapter slot exists but ships empty.
- The trade coach infers behavioral tags (revenge trading, chased entry, etc.) from
  observable order/timing patterns, not literal intent — an honest approximation,
  documented in `coach/coach.py`'s module docstring.
- No browser-driven UI test coverage exists (see `docs/AI_HANDOFF.md` "Known
  issues" for the current verification status of the newest UI surfaces).
