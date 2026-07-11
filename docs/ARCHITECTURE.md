# OptionsPilot — System Architecture

**Status:** Living document. Updated as each phase lands.
**Mode:** Paper trading only. Live trading is architecturally possible but gated behind
configuration (`broker.live_trading_enabled`) that defaults to `false` and requires an
explicit, multi-step opt-in that does not exist in v1.

---

## 1. Design Principles

1. **Safety first.** No code path can place a real-money order in v1. The only broker
   implementation is a simulator. Live adapters, when added, sit behind the same
   `Broker` interface and a hard config gate.
2. **Everything is a module behind an interface.** Data providers, brokers, strategies,
   indicators, and notifiers are all pluggable. Swapping yfinance for Polygon, or the
   paper broker for Alpaca, touches one adapter file and one config line.
3. **The same analysis code runs live and in backtests.** The AI engine consumes a
   stream of candles; it does not know whether they come from the live data engine or
   the backtester. This eliminates the classic "backtest says X, live does Y" bug class.
4. **Deterministic and auditable.** Every decision is logged with its inputs. Every
   trade records the full reasoning chain. Random seeds are configurable.
5. **Fail closed.** Any error in risk checks, data quality, or broker communication
   halts trading rather than guessing.

---

## 2. Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        UI Layer (Phase 7)                        │
│   pywebview desktop window ── FastAPI ── WebSocket live feed     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                     Orchestrator (Phase 6)                       │
│   Event loop: schedules scans, routes signals → risk → broker    │
└──┬──────────┬──────────┬───────────┬───────────┬────────────┬───┘
   │          │          │           │           │            │
┌──┴───┐ ┌────┴───┐ ┌────┴────┐ ┌────┴────┐ ┌────┴─────┐ ┌────┴───┐
│ Data │ │   AI   │ │  Risk   │ │ Broker  │ │ Journal  │ │ Notify │
│Engine│ │ Engine │ │ Manager │ │ (Paper) │ │ & Learn  │ │        │
└──────┘ └────────┘ └─────────┘ └─────────┘ └──────────┘ └────────┘
                │
        ┌───────┴────────┐
        │  Backtester    │  (drives AI Engine with historical data)
        └────────────────┘
```

### 2.1 Data Engine (`optionspilot/data/`)
- `MarketDataProvider` (abstract): `get_candles(symbol, timeframe, start, end)`,
  `get_quote(symbol)`, `get_option_chain(symbol, expiration)`.
- `YFinanceProvider`: free provider used in v1. Delayed/EOD-quality data — fine for
  paper trading and backtesting. Rate-limited and cached.
- `CandleCache`: SQLite-backed cache so backtests and repeated scans don't re-download.
- Future adapters: Polygon, Tradier (market data), Alpaca, TradingView webhooks
  (as an *alert input*, since TradingView has no data/trading API).

### 2.2 Analysis Library (`optionspilot/analysis/`)
Pure, stateless functions over pandas DataFrames. No I/O, no side effects — fully
unit-testable and reusable by both live engine and backtester.
- `indicators.py` — EMA, SMA, VWAP, MACD, RSI, Stoch RSI, ATR, Bollinger, Supertrend,
  ADX, OBV, relative volume.
- `candlesticks.py` — engulfing, hammer, shooting star, doji, morning/evening star,
  inside/outside bars, marubozu, three white soldiers / three black crows.
- `structure.py` — swing highs/lows, HH/HL/LH/LL classification, BOS, CHoCH,
  consolidation detection, trend state.
- `smart_money.py` — fair value gaps, order blocks, liquidity pools (equal highs/lows),
  liquidity grabs, premium/discount zones, supply/demand.
- `volume.py` — volume spikes, buying/selling pressure, volume divergence.
- `options_metrics.py` — Black-Scholes greeks, IV solving, liquidity scoring
  (spread %, OI, volume), expected move.

### 2.3 AI Decision Engine (`optionspilot/engine/`)
- `MultiTimeframeAnalyzer`: runs the analysis library across configured timeframes
  (e.g. 1D/4H/1H/15m/5m), producing a `TimeframeView` per timeframe.
- `ConfluenceScorer`: weighted scoring of evidence (trend alignment, structure,
  patterns, volume, momentum, SMC signals) → confidence score 0–100 with an
  itemized reason list. Weights come from config and are later tuned by the
  learning system.
- `ContractSelector`: given a directional signal, scans the option chain and picks
  the contract by delta target, DTE window, liquidity score, and spread threshold.
  Rejects illiquid chains outright.
- `TradePlanner`: builds a full `TradePlan` — entry, stop (structure/ATR based),
  targets, partial-profit levels, max hold time, invalidation conditions.
- Signals below `engine.min_confidence` (default 80) are logged but never traded.

### 2.4 Risk Manager (`optionspilot/risk/`)
A gatekeeper every order must pass through — the AI engine cannot reach the broker
directly. Enforces: max daily/weekly loss, max consecutive losses, max drawdown,
risk-per-trade %, max contracts, daily trade limit, trading-hours window,
cooldown after losses, and automatic shutdown ("circuit breaker") on limit breach.
Position sizing is computed here (account balance × risk % ÷ max loss per contract),
never by the strategy.

### 2.5 Broker Layer (`optionspilot/broker/`)
- `Broker` (abstract): `submit_order`, `cancel_order`, `get_positions`,
  `get_account`, order/fill event callbacks.
- `PaperBroker`: full simulator — realistic fills using bid/ask with configurable
  slippage, commissions, and partial fills; tracks account equity, positions, P&L.
  Persists state to SQLite so the paper account survives restarts.
- Future: `AlpacaBroker`, `TradierBroker`, `WebullBroker` (pending official API
  approval), `IBKRBroker`. Live adapters refuse to initialize unless
  `broker.live_trading_enabled: true` AND `broker.i_understand_the_risks: true`.

### 2.6 Journal & Learning (`optionspilot/journal/`, `optionspilot/learning/`)
- SQLite journal: every trade stores entry/exit, P&L, timeframe context snapshot,
  indicators used, confidence score, full entry/exit reasoning, market regime,
  chart data snapshot (for rendering later), tags, mistakes, lessons.
- Learning system: periodic batch analysis over the journal — win rate and
  expectancy sliced by indicator, time of day, DTE, delta bucket, market regime,
  strategy. Produces updated `ConfluenceScorer` weights via regularized,
  sample-size-aware updates (no weight moves on < N trades of evidence).
  All weight changes are versioned and logged so learning is auditable and
  reversible.

### 2.7 Backtesting (`optionspilot/backtest/`)
Event-driven backtester that replays cached historical candles bar-by-bar through
the *same* AI engine and risk manager, using the PaperBroker in simulation mode.
Options prices are reconstructed via Black-Scholes from underlying + IV estimates
(documented limitation: historical option chains aren't available for free; the
report flags this). Outputs: net profit, win rate, profit factor, max drawdown,
average win/loss, Sharpe, trade distribution, monthly/yearly returns, equity curve
— as JSON + rendered HTML report.

### 2.8 Orchestrator (`optionspilot/orchestrator.py`)
The event loop: market-hours aware scheduler that pulls fresh candles, runs the
engine per watchlist symbol, routes signals through risk to the broker, manages
open positions (stop moves, partials, early exits on invalidation), and emits
events to journal/notifications/UI.

### 2.9 UI (`optionspilot/ui/`)
FastAPI backend + single-page dashboard (dark-mode-first), served locally and
wrapped in a pywebview native window; packaged with PyInstaller → normal Windows
app. WebSocket pushes live state. Views: dashboard (equity, P&L today/week/month,
confidence meter, open positions, watchlist), trade history, journal browser,
backtest runner + reports, learning insights, settings editor.

### 2.10 Notifications (`optionspilot/notify/`)
`Notifier` interface; desktop toasts (win11toast) and SMTP email adapters.
Events: trade opened/closed, risk limit hit, large move, daily/weekly summary.

### 2.11 Cross-cutting
- **Config** (`optionspilot/config/`): layered — packaged defaults → user
  `config.yaml` → environment variables. Validated with pydantic; unknown keys and
  out-of-range values fail fast at startup.
- **Logging** (`optionspilot/core/logging_setup.py`): structured, rotating file logs
  per subsystem + rich console output. Every trade decision is reconstructable
  from logs alone.
- **Models** (`optionspilot/core/models.py`): typed domain objects (Candle, Quote,
  OptionContract, Signal, TradePlan, Order, Fill, Position, TradeRecord) shared by
  every module — the vocabulary of the system.

---

## 3. Data Flow (one scan cycle)

1. Orchestrator wakes on schedule (e.g. every 60s during market hours).
2. Data Engine returns fresh candles for each watchlist symbol × timeframe (cached).
3. MultiTimeframeAnalyzer produces per-timeframe views (trend, structure, patterns,
   indicators, volume, SMC).
4. ConfluenceScorer merges views → confidence + reasons. Below threshold → log & skip.
5. ContractSelector picks the option contract; liquidity check can veto.
6. TradePlanner builds the full plan (entry/stop/targets/invalidation).
7. Risk Manager approves/vetoes and sets position size.
8. PaperBroker executes; fills flow back as events.
9. Journal records everything; Notifier fires; UI updates via WebSocket.
10. Open-position manager runs every cycle: trail stops, take partials, exit on
    invalidation or risk events.

---

## 4. Technology Choices

| Concern        | Choice                     | Why |
|----------------|----------------------------|-----|
| Language       | Python 3.14                | Ecosystem for market data, pandas, ML; installed on this machine |
| Data wrangling | pandas + numpy             | Standard, fast enough for candle-scale data |
| Market data    | yfinance (v1), pluggable   | Free, no API key; adapters for paid feeds later |
| Storage        | SQLite (stdlib)            | Zero-ops, single-file, perfect for desktop app |
| Validation     | pydantic v2                | Config + model validation, fail-fast |
| API/UI backend | FastAPI + uvicorn          | Async, WebSocket support, well-documented |
| Desktop shell  | pywebview (WebView2)       | Native window on Win11 without Electron weight |
| Packaging      | PyInstaller                | One-file Windows executable |
| Tests          | pytest                     | Standard |

## 5. Known Limitations (v1, documented deliberately)

- yfinance data is delayed and rate-limited; intraday history is limited (~60 days
  of 5m bars). Good for paper trading and strategy development; a paid feed is the
  upgrade path for serious intraday work.
- Free historical *option chain* data does not exist; backtests price options via
  Black-Scholes reconstruction and label results accordingly.
- TradingView integration is inbound-only (webhook alerts), by TradingView's design.
- Webull requires official OpenAPI approval; the adapter slot exists but ships empty.
