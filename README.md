# OptionsPilot

AI-powered options **paper-trading** desktop platform: multi-timeframe market
analysis, confidence-scored trade decisions, strict risk management, a
deterministic trade coach, an interactive chart workspace, and backtesting —
designed so live trading could later be enabled by configuration only (and
deliberately impossible today).

> **Safety:** the only broker is a simulator — there is no live-trading code
> path anywhere in this codebase. See `docs/ARCHITECTURE.md` §1 and §6 for
> the live-trading gate design.

## Status

All 8 original v1 phases are complete, plus the V2 rewrite through V2-4
(chart workspace). **345 tests, 100% passing.** See
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for the current snapshot,
[docs/ROADMAP.md](docs/ROADMAP.md) for what's next.

| Area | Status |
|------|--------|
| v1 (analysis engine, AI decision engine, risk + paper broker, journal/learning/backtest, orchestrator, desktop UI, hardening) | ✅ done |
| Trading modes (conservative / high-risk / custom) | ✅ done |
| Manual paper trading (order book, Trade tab, account metrics) | ✅ done |
| AI Mode vs. Human Mode + deterministic Trade Coach | ✅ done |
| Interactive chart workspace (drawing tools, trade lines) | ✅ done |
| Replay engine (V2-5) | ⬜ not started |
| Journal & improvement dashboard (V2-6) | ⬜ partially covered by the Coach tab |

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

The app has nine tabs: **Dashboard** (equity, P&L, AI confidence meters,
position cards), **Charts** (interactive candles/volume with indicator
overlays, drawing tools, and position/order trade lines), **Trade** (manual
option chain, order ticket, working orders, account metrics), **Coach**
(process-scored review of every manual trade, recurring mistakes, practice
exercises), **Watchlist**, **Journal**, **Backtest**, **Learning**, and
**Settings**. Keyboard 1–9 switches tabs.

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
coach reviews, backtest reports). Logs rotate under `logs/` per subsystem.

### TradingView alerts (optional)

Enable in `config.yaml` (`integrations.tradingview_webhook: true` plus a 16+
character `tradingview_secret`), then point a TradingView alert webhook at
`http://<host>:<port>/webhook/tradingview` with the message:

```json
{"secret": "<your secret>", "symbol": "{{ticker}}", "note": "optional"}
```

An alert triggers a scan of that symbol through the full engine + risk
pipeline — it changes *when* the system looks, never *whether* it trades.

## AI Mode vs. Human Mode

Two independent axes, switchable live from the header (no restart):

- **`operating_mode`** — `ai` (default): the engine places entries itself and
  manages its own stops/targets. `human`: the engine still scans and scores
  every symbol, but only advises (one notification per tradeable signal) —
  you place every order from the Trade tab, and the **Trade Coach** reviews
  each closed round trip with a process-based score and a mistake taxonomy.
- **`trading_mode`** — conservative / high-risk / custom (see below).
  Applies to the confidence threshold in *both* operating modes.

Switching one axis never flips the other.

## Watchlist

Managed entirely from the app's **Watchlist** tab — no config editing:
type a ticker and press Enter (autocomplete as you type), or paste a whole
list from anywhere (commas, spaces, or new lines — `$AAPL`-style cashtags
work too). Every symbol is validated against a bundled 12k-symbol US
directory (unknown tickers fall back to a live quote check); duplicates and
invalid symbols are reported without blocking the rest. One-click preset
lists (Magnificent 7, AI Stocks, Semiconductors, …) plus a saveable
"My Favorites". Pin (★) to top, drag ≡ to reorder, one-click remove, search
within the list, sort by price / daily change / volume / market cap / AI
confidence. Keyboard: Enter adds, Ctrl+V pastes-and-parses, Delete removes
selected, Ctrl+A selects all. Everything saves automatically to
`data/settings.json` and survives restarts. Capped at 30 symbols (the free
data feed scans take seconds per symbol).

## Trading modes

Switch live from the app (segmented control in the header and Settings —
takes effect on the next scan, persists across restarts), or set the default
via `engine.trading_mode` in `config.yaml`:

- **conservative** (default) — trades only at ≥ `min_confidence` (80%).
  Accuracy over frequency.
- **high_risk** — the required confidence adapts to *setup quality*, a
  structured assessment of trend alignment, market structure, volume,
  momentum, S/R positioning, divergence, and consolidation:

  | Setup quality | Required confidence (base 80) |
  |---|---|
  | excellent | 62% |
  | good | 70% |
  | average | 77% |
  | poor | never trades, at any confidence |

  Bounded below by `high_risk_floor` (60%). Entries below the conservative
  bar additionally need risk/reward ≥ `high_risk_min_rr_stretch` (2.0) —
  selective aggression, not recklessness. Stops, position sizing, loss
  limits, cooldowns and liquidity filters are identical in both modes.
  Every accept/reject is logged with the passed/failed confirmations and
  shown per-symbol on the dashboard.
- **custom** — advanced users can set their own fixed confidence bar plus
  risk-per-trade %, max trades/day, max contracts, min risk/reward, and
  daily loss limit, from Settings → Advanced settings. Values are validated
  like config.yaml (out-of-range is rejected); switching back to
  conservative/high-risk restores the yaml values exactly.

In-app mode switches and watchlist edits live in `data/settings.json`
(they overlay config.yaml at startup; explicit `engine.evidence_weights`
etc. in yaml still apply).

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
  broker/        Broker ABC, paper simulator, order manager, position
                 manager, registry
  coach/         deterministic post-trade review + aggregated profile
  journal/       SQLite trade journal
  learning/      performance slicing, bounded weight updates, WeightStore
  backtest/      event-driven replay + JSON/HTML reports
  notify/        desktop + email notification center
  integrations/  TradingView webhook parsing
  ui/            FastAPI backend, static dashboard (incl. Charts tab),
                 pywebview shell
  orchestrator.py  the live event loop
scripts/         build_exe.ps1, soak.py, make_icon.py, fetch_symbols.py
docs/            see "Documentation" below
tests/           pytest suite (345 tests)
```

## Documentation

Start with [docs/AI_CONTEXT.md](docs/AI_CONTEXT.md) if you're an AI assistant
picking up this project, or [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) if
you're a human contributor. The full set:

- [docs/AI_CONTEXT.md](docs/AI_CONTEXT.md) — permanent orientation for AI
  sessions: vision, philosophy, standards, what never to change casually.
- [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md) — complete technical orientation
  for a session that has never seen this codebase.
- [docs/NEXT_SESSION.md](docs/NEXT_SESSION.md) — the concise "what to do
  right now" handoff, updated after every significant session.
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — structured snapshot
  (version, milestones, test count, priorities).
- [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) — the session-by-session
  narrative log.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full system design, data
  flow diagrams, technology choices, and honest limitations.
- [docs/MODULES.md](docs/MODULES.md) — quick API map per module.
- [docs/ROADMAP.md](docs/ROADMAP.md) — Completed / In Progress / Planned /
  Deferred / long-term vision. [docs/ROADMAP-V2.md](docs/ROADMAP-V2.md) has
  the granular per-phase checklist.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — dated, prose changelog by feature.
- [docs/TODO.md](docs/TODO.md) — flat, actionable work queue.
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — coding conventions, commit
  style, testing expectations, definition of done.
- [CLAUDE.md](CLAUDE.md) — permanent instructions for AI coding sessions
  (safety rules, architecture rules, workflow) — read this first if you are one.
