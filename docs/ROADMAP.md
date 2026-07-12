# OptionsPilot — Development Roadmap

Each phase is built, tested, and documented before the next begins.
Checkboxes are updated as work lands.

## Phase 1 — Foundation (this session)
- [x] Project scaffold, pyproject, virtualenv, git init
- [x] Architecture + roadmap documents
- [x] Layered configuration system (defaults → config.yaml → env), pydantic-validated
- [x] Structured logging (rotating files + console)
- [x] Core domain models (Candle, Quote, OptionContract, Signal, TradePlan, Order,
      Fill, Position, TradeRecord)
- [x] Data engine: provider interface, yfinance adapter, SQLite candle cache
- [x] Indicator library: EMA, SMA, VWAP, MACD, RSI, Stoch RSI, ATR, Bollinger,
      Supertrend, ADX, OBV, relative volume
- [x] Unit tests for all of the above

## Phase 2 — Analysis Suite (complete)
- [x] Candlestick pattern detectors (11 patterns) + tests against known fixtures
- [x] Market structure: swings, HH/HL/LH/LL, BOS, CHoCH, trend state, consolidation
- [x] Smart money concepts: FVG, order blocks, liquidity pools, equal highs/lows,
      premium/discount zones, liquidity grabs
- [x] Volume analysis: spikes, pressure, divergence
- [x] Options math: Black-Scholes greeks, IV solver, liquidity scorer, expected move

## Phase 3 — AI Decision Engine (complete)
- [x] MultiTimeframeAnalyzer (TimeframeView per timeframe)
- [x] ConfluenceScorer with configurable weights + itemized reasons
- [x] ContractSelector (delta/DTE/liquidity filters)
- [x] TradePlanner (entry/stop/targets/partials/invalidation)
- [x] Signal logging with full evidence trail

## Phase 4 — Risk & Execution (complete)
- [x] RiskManager: all limits, circuit breaker, position sizing
- [x] Broker interface + PaperBroker simulator (fills, slippage, commissions,
      persistent account state)
- [x] Open-position manager (trailing stops, partials, invalidation exits)

## Phase 5 — Journal, Learning, Backtesting (complete)
- [x] SQLite trade journal with full context snapshots
- [x] Learning system: performance slicing, sample-aware weight updates, versioned
- [x] Event-driven backtester reusing the live engine
- [x] Backtest reports (JSON + HTML): all required metrics + equity curve

## Phase 6 — Orchestrator & Notifications (complete)
- [x] Market-hours-aware event loop
- [x] Watchlist scanning, signal → risk → broker pipeline
- [x] Desktop + email notifications, daily/weekly summaries
- [x] CLI application: run / scan / status / journal / backtest / learn
- [x] Restart-safe open-trade context + risk-state rebuild from journal

## Phase 7 — Desktop UI (complete)
- [x] FastAPI backend + WebSocket live feed
- [x] Dashboard, positions, journal browser, backtest runner, learning insights,
      settings view (dark mode, responsive; config editing stays in config.yaml
      by design — validated at startup)
- [x] pywebview desktop shell (`python -m optionspilot ui`)
- [x] PyInstaller packaging → dist\OptionsPilot\OptionsPilot.exe
      (`scripts\build_exe.ps1`)

## Phase 8 — Hardening & Extension Points (complete)
- [x] Soak harness (`scripts/soak.py`): repeated live cycles with exception,
      memory-growth, and cycle-time tracking — rerun during market hours before
      long unattended sessions
- [x] TradingView webhook alert listener (`/webhook/tradingview`): secret-gated,
      triggers a full-pipeline scan, can never place an order directly
- [x] Broker adapter registry (`broker/registry.py`) with Alpaca / Tradier /
      Webull (pending API approval) / IBKR slots that fail loudly with guidance
- [x] Performance pass: smart-money detectors vectorized, analyzer capped to a
      400-bar window — backtest 7.9s → 4.7s on 520 bars, identical results

## Beyond v1 (candidate work, unscheduled)
- [ ] Candle cache in the live loop (incremental fetch + merge) to cut
      yfinance traffic during long sessions
- [ ] First real live-broker adapter (Alpaca options paper API is the natural
      candidate) — only after sustained paper profitability
- [ ] News / economic calendar / sentiment inputs as new evidence types
- [ ] Portfolio-level risk (correlated positions, sector exposure)
