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
(chart workspace); the V3 product-quality sprint and the V0.5–V0.7 milestones
are on branch `feature/v0.7`. **2824 tests, 100% passing.** See
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for the current snapshot,
[docs/ROADMAP.md](docs/ROADMAP.md) for what's next.

| Area | Status |
|------|--------|
| v1 (analysis engine, AI decision engine, risk + paper broker, journal/learning/backtest, orchestrator, desktop UI, hardening) | ✅ done |
| Trading modes (conservative / high-risk / custom) | ✅ done |
| Manual paper trading (order book, Trade tab, account metrics) | ✅ done |
| AI Mode vs. Human Mode + deterministic Trade Coach | ✅ done |
| Interactive chart workspace (drawing tools, trade lines) | ✅ done |
| Market data: 6 providers, health ranking, budgets, diagnostics | ✅ done |
| Market Data Control Centre (keys, order, tests, maintenance) | ✅ done |
| Replay engine (V2-5) | ⬜ not started |
| Journal & improvement dashboard (V2-6) | ⬜ partially covered by the Coach tab |

## Setup

```powershell
cd optionspilot
.\scripts\verify.ps1
```

One command: creates `.venv`, installs everything, runs the full test
suite, and checks the frontend/docs for drift. Should end in
`VERIFY: PASS`. See [docs/QUICK_START.md](docs/QUICK_START.md) for the
minimal path to a running app, or do it by hand:

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\pip install windows-toasts   # optional: desktop notifications
.venv\Scripts\python -m pytest             # full test suite
```

## Usage

### Desktop app

```powershell
.\scripts\dev.ps1 -Ui
# or in a browser, with the live scan loop off by default:
.\scripts\dev.ps1
```

By hand:

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

### Install on Windows (end users)

Download from the [Releases](https://github.com/vereglover-star/OptionsPilot/releases)
page — each release has two options:

- **`OptionsPilot-Setup-vX.Y.Z.exe`** — the installer. Installs to
  `C:\Program Files\OptionsPilot`, adds Start Menu + desktop shortcuts, and
  registers in Windows "Installed Apps" (upgrade/uninstall supported).
- **`OptionsPilot-vX.Y.Z.zip`** — portable; extract and run `OptionsPilot.exe`,
  no install.

Your data (paper account, journal, coach reviews, settings, watchlists, backups)
lives under `%LOCALAPPDATA%\OptionsPilot`, **separate from the program files**, so
**upgrades and reinstalls never lose it**. Uninstalling asks whether to also
delete that data — the default is **No**. Details: [docs/INSTALLER.md](docs/INSTALLER.md).

Once installed, OptionsPilot **keeps itself up to date**: it quietly checks
GitHub Releases on launch and, when a newer version exists, offers a one-click
update that backs up your data, installs, and restarts — no manual downloads. It
is configurable in **Settings ▸ Software updates** (auto-check on/off, frequency,
opt-in beta channel) and available any time from **Help ▸ Check for Updates…**.
Your data is never touched by an update. Details: [docs/AUTO_UPDATER.md](docs/AUTO_UPDATER.md).

The setup exe is not yet code-signed, so Windows SmartScreen may warn on first
run ("More info" → "Run anyway").

### Package / build the installer yourself (developers)

```powershell
.\scripts\build_exe.ps1         # -> dist\OptionsPilot\OptionsPilot.exe
.\scripts\build_installer.ps1   # -> installer\Output\OptionsPilot-Setup-v<version>.exe  (needs Inno Setup 6)
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

## Market data

Charts and scans are fed by a chain of providers, asked in order until one
answers with usable bars. **Settings → Market data** is where you see and steer
all of it:

- **One card per provider** — its state in a word plus a sentence explaining
  it, latency, success rate, remaining quota, and what kind of feed it is
  (free / rate-limited, real-time / delayed, key or no key).
- **API keys.** Twelve Data and Alpha Vantage each offer a free key (no card).
  Paste it in and the provider is usable immediately — no restart.
  **Twelve Data is the one to add** if you want a second, genuinely independent
  source: Finnhub also offers a free key, but has moved historical prices to its
  paid plans, so a free Finnhub key authenticates and is then refused chart data
  (the app says exactly that, rather than blaming your key).
  Keys are stored in `data/credentials.json` with owner-only permissions, shown
  only as `••••••••abcd`, and stripped from every diagnostics export, report
  and log line, so a bug report is always safe to share. An environment
  variable (`FINNHUB_API_KEY`, …) still takes precedence, and the page says so
  when one is.
- **Provider order**, with Move Up / Down / Reset, and three ordering modes:
  *static* (exactly your order), *hybrid* (your order, but a failing provider
  loses its place), and *dynamic* (fastest healthy provider per request — the
  default).
- **Test connection** — a real request end to end, reporting what happened and
  what to do about it.
- **Maintenance** — clear or rebuild the chart cache, verify its integrity,
  re-run validation, replay a request across every provider, benchmark them, or
  re-measure how far back each one's history really goes. Each shows progress,
  produces a summary, and says up front whether it spends live requests.
- **Advice** — if you are running on a single source, or a provider is nearly
  out of quota or repeatedly failing, the page says so and names the fix.

**Worth knowing:** out of the box there is effectively *one* real source.
Stooq now blocks automated requests entirely, and Yahoo and yfinance are two
different pieces of code reaching the same servers — so a Yahoo outage takes
both. Adding one free key is what buys a genuinely independent source, and the
panel exists so that takes thirty seconds. Full design:
`docs/MARKET_DATA.md`.

## Configuration

Edit `config.yaml` (validated at startup; typos and out-of-range values fail
fast). Environment variables override the file:

```powershell
$env:OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT = "0.5"
```

### Optional market-data API keys

OptionsPilot works fully with **no API keys** — Yahoo, yfinance and Stooq need
none, and that is the shipped default. Adding a key enables an extra provider
that is genuinely independent of Yahoo, so a Yahoo outage no longer takes
intraday charts with it. All three free tiers are optional and no key is ever
required to trade, chart or backtest.

```powershell
$env:FINNHUB_API_KEY      = "..."   # 60 req/min, real-time  finnhub.io/register
$env:TWELVEDATA_API_KEY   = "..."   # 800/day, 8/min         twelvedata.com/pricing
$env:ALPHAVANTAGE_API_KEY = "..."   # 25/day                 alphavantage.co
```

Keys can also live in `config.yaml` under
`market_data.providers.<name>.api_key`, though the environment is preferred —
`config.yaml` is the file people attach to bug reports. **Keys are redacted
from every diagnostics payload and export.** Help ▸ Diagnostics shows each
provider's status, remaining budget, and — when a key is missing — where to get
one. Full detail: [docs/MARKET_DATA.md](docs/MARKET_DATA.md) §23–27.

## Layout

```
optionspilot/
  config/        layered, validated configuration
  core/          domain models, logging
  data/          six-provider market-data subsystem (see docs/MARKET_DATA.md):
                 keyless Yahoo/yfinance/Stooq + keyed Finnhub/Twelve Data/
                 Alpha Vantage, health-ranked with circuit breakers, request
                 budgets, validation, diagnostics and a durable candle cache
  analysis/      indicators, candlesticks, structure, smart money, volume,
                 options math (pure functions, shared by live + backtest)
  engine/        multi-timeframe analyzer, confluence scorer, contract
                 selector, trade planner
  risk/          the gate: limits, circuit breaker, position sizing
  broker/        Broker ABC, paper simulator, order manager, position
                 manager, registry
  coach/         deterministic post-trade review + aggregated profile
  intelligence/  the Trading Intelligence Engine (see docs/TRADING_INTELLIGENCE.md):
                 one analysis of your whole trading record — performance,
                 behaviour, discovered patterns, risk, eight self-explaining
                 scores, goals, lessons, recommendations, timeline,
                 achievements and prose reports — consumed by every tab
  journal/       SQLite trade journal
  learning/      performance slicing, bounded weight updates, WeightStore
  backtest/      event-driven replay + JSON/HTML reports
  notify/        desktop + email notification center
  integrations/  TradingView webhook parsing
  ui/            FastAPI backend, static dashboard (incl. Charts tab),
                 pywebview shell
  orchestrator.py  the live event loop
scripts/         dev/test/verify/docs/build/release/clean .ps1 entry points
                 (see docs/CONTRIBUTING.md), build_exe.ps1, soak.py,
                 make_icon.py, fetch_symbols.py
  release.ps1    one-command release: preflight, bump, verify, commit, tag,
                 push, then watch the GitHub build (see docs/RELEASE.md)
  lib/           helper library behind release.ps1
docs/            see "Documentation" below
tests/           pytest suite (2824 tests)
```

## Documentation

Start with [docs/QUICK_START.md](docs/QUICK_START.md) for the minimum
steps to get running, or [docs/AI_CONTEXT.md](docs/AI_CONTEXT.md) if you're an AI assistant
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
- [docs/TRADING_INTELLIGENCE.md](docs/TRADING_INTELLIGENCE.md) — the Trading
  Intelligence Engine: architecture, the rules that keep its conclusions honest,
  the metric registry, measured performance, and its stated limitations.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — dated, prose changelog by feature.
- [docs/TODO.md](docs/TODO.md) — flat, actionable work queue.
- [docs/RELEASE.md](docs/RELEASE.md) — CI/CD & release pipeline;
  [docs/INSTALLER.md](docs/INSTALLER.md) — the Windows installer (build, upgrade,
  uninstall, AppData preservation);
  [docs/AUTO_UPDATER.md](docs/AUTO_UPDATER.md) — the in-app self-updater
  (architecture, flow, failure recovery, security).
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — coding conventions, commit
  style, testing expectations, definition of done, the developer scripts.
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) — the exact,
  scripted process for shipping a release.
- [CLAUDE.md](CLAUDE.md) — permanent instructions for AI coding sessions
  (safety rules, architecture rules, workflow) — read this first if you are one.
