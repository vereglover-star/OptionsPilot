# OptionsPilot

AI-powered options **paper-trading** system: multi-timeframe market analysis,
confidence-scored trade decisions, strict risk management, a learning journal,
and backtesting — designed so live trading could later be enabled by
configuration only (and deliberately impossible in v1).

> **Safety:** v1 contains no live-trading code path. The only broker is a
> simulator. See `docs/ARCHITECTURE.md` §2.5 for the live-trading gate design.

## Status

Phase 1 of 8 complete — see [docs/ROADMAP.md](docs/ROADMAP.md).

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: config, logging, models, data engine, indicators | ✅ done |
| 2 | Analysis suite: candlesticks, structure, SMC, volume, options math | ✅ done |
| 3 | AI decision engine | ✅ done |
| 4 | Risk manager + paper broker | ✅ done |
| 5 | Journal, learning, backtesting | ✅ done |
| 6 | Orchestrator + notifications + CLI | ✅ done |
| 7 | Desktop UI (pywebview + FastAPI, PyInstaller packaging) | ✅ done |
| 8 | Hardening: soak harness, TradingView webhook, broker adapter slots, performance pass | ✅ done |

All 8 planned phases are complete. See [docs/ROADMAP.md](docs/ROADMAP.md)
for candidate post-v1 work.

## Setup

```powershell
cd optionspilot
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\pip install windows-toasts   # optional: desktop notifications
.venv\Scripts\python -m pytest             # full test suite
```

## Usage

### Desktop app

```powershell
.venv\Scripts\pip install -e .[ui]
.venv\Scripts\python -m optionspilot ui           # native window + live loop
# or in a browser:
.venv\Scripts\python -m optionspilot serve --port 8787
```

The dashboard shows equity, P&L today/week/month, per-symbol AI confidence
meters against the trade threshold, open positions, notifications, the full
journal with each trade's reasoning, a backtest runner, and the learning
system's weights and performance slices.

### Package as a normal Windows app

```powershell
.\scripts\build_exe.ps1        # -> dist\OptionsPilot\OptionsPilot.exe
```

Double-click the exe to open the desktop app. CLI commands pass through:
`OptionsPilot.exe scan`, `OptionsPilot.exe backtest SPY --days 25`.

### CLI

```powershell
.venv\Scripts\python -m optionspilot run          # headless paper-trading loop
.venv\Scripts\python -m optionspilot scan         # one scan cycle now
.venv\Scripts\python -m optionspilot status       # account + positions + risk
.venv\Scripts\python -m optionspilot journal      # recent trades + stats
.venv\Scripts\python -m optionspilot backtest SPY --days 25 --min-confidence 40
.venv\Scripts\python -m optionspilot learn        # learning cycle over the journal
.venv\Scripts\python scripts\soak.py --cycles 10  # stability soak (scratch account)
```

All state lives under `data/` (paper account, journal, learned weights,
backtest reports). Logs rotate under `logs/` per subsystem.

### TradingView alerts (optional)

Enable in `config.yaml` (`integrations.tradingview_webhook: true` plus a 16+
character `tradingview_secret`), then point a TradingView alert webhook at
`http://<host>:<port>/webhook/tradingview` with the message:

```json
{"secret": "<your secret>", "symbol": "{{ticker}}", "note": "optional"}
```

An alert triggers a scan of that symbol through the full engine + risk
pipeline — it changes *when* the system looks, never *whether* it trades.

## Configuration

Edit `config.yaml` (validated at startup; typos and out-of-range values fail
fast). Environment variables override the file:

```powershell
$env:OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT = "0.5"
```

## Layout

```
optionspilot/
  config/        layered, validated configuration
  core/          domain models, logging
  data/          provider interface, yfinance adapter, SQLite candle cache
  analysis/      indicators, candlesticks, structure, smart money, volume,
                 options math (pure functions, shared by live + backtest)
  engine/        multi-timeframe analyzer, confluence scorer, contract
                 selector, trade planner
  risk/          the gate: limits, circuit breaker, position sizing
  broker/        Broker ABC, paper simulator, position manager, registry
  journal/       SQLite trade journal
  learning/      performance slicing, bounded weight updates, WeightStore
  backtest/      event-driven replay + JSON/HTML reports
  notify/        desktop + email notification center
  integrations/  TradingView webhook parsing
  ui/            FastAPI backend, static dashboard, pywebview shell
  orchestrator.py  the live event loop
scripts/         build_exe.ps1, soak.py
docs/            ARCHITECTURE.md, ROADMAP.md, MODULES.md
tests/           pytest suite (225 tests)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full system design, data flow,
  technology choices, and honest v1 limitations (data quality, TradingView/Webull
  API realities).
- [docs/ROADMAP.md](docs/ROADMAP.md) — phase-by-phase plan with acceptance items.
