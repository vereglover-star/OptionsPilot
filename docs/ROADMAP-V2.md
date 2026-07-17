# OptionsPilot V2 — Professional Trading Platform Roadmap

Goal: evolve the working v1 paper-trading system into a polished desktop
trading platform — TradingView-inspired workspace, full manual paper trading
with an AI coach (Human Mode), autonomous trading (AI Mode), replay, and a
professional journal — while keeping the v1 discipline: every phase is built,
tested, and committed before the next begins.

## Architecture decision: keep the Python + pywebview + PyInstaller shell

Considered: Electron, Tauri. Rejected for now:
- The backend (pandas/numpy analysis, engine, broker sim) is Python and would
  be embedded either way; Electron/Tauri would only replace the *window*.
- The current shell already meets the standalone requirements: single exe,
  embedded backend auto-starts, clean stop on close, state beside the exe.
- A rewrite risks 240+ passing tests for zero user-visible gain.
Revisit if we ever need multi-window/multi-monitor layouts (Tauri preferred).

Remaining gaps for "professional desktop app" feel are addressed in V2-1:
windowed (no console) build, app icon, shutdown audit.

## Phases

### V2-0 — Stabilize (complete) 
- [x] Watchlist manager + runtime settings + mode toggle: 272 green, verified live

### V2-1 — True desktop application (complete)
- [x] `--windowed` no-console build (CLI stays available via
      `python -m optionspilot`)
- [x] Application icon (scripts/make_icon.py → assets/optionspilot.ico)
- [x] Single-instance guard (localhost mutex + friendly notice window)
- [x] Windowed-safe logging (no console handler when stderr is absent)

### V2-2 — Trading engine: orders, manual trading, account metrics (complete)
- [x] `OrderManager`: MARKET, LIMIT (premium), STOP_LOSS / TAKE_PROFIT /
      TRAILING_STOP (underlying, put-aware); DAY (expires 16:00 ET) / GTC;
      reservation checks; auto-cancel on position close; restart-safe
- [x] Manual trading API: /api/chain, /api/orders place/list/cancel,
      one-click position close; `managed_by` separation from AI positions
- [x] Trade tab: account cards, live chain, order ticket, working orders
- [x] /api/account/metrics: buying power, portfolio value, unrealized/
      realized/daily P/L, total return %, win rate, avg win/loss, PF,
      max drawdown from persisted equity snapshots
- [ ] Stock (share) positions — deferred until after V2-3
- [x] Fill realism documented (delayed data; per-cycle evaluation; pre-market
      zero-quote orders reject cleanly)

### V2-3 — AI Mode vs Human Mode (complete)
- [x] `operating_mode: ai | human` — instant switch, persisted (runtime store)
- [x] Human Mode: engine still scans/advises, NEVER auto-trades
- [x] `TradeCoach`: deterministic post-trade review from the analysis stack —
      before (setup quality, trend/momentum/volume/IV/theta/strike/DTE,
      chased?), during (stop moved? averaged down? exited vs plan?), after
      (why it won/lost, what pros would do differently), score /100, EV
      estimate, mistake tags
- [x] Mistake taxonomy + per-trade tags persisted in the journal
- [x] Coaching profile: recurring mistakes, strengths/weaknesses, long-term stats

### V2-4 — Chart workspace (TradingView-inspired) — SHIPPED except workspace layout
- [x] Bundle lightweight-charts (Apache-2.0, vendored at
      `ui/static/lightweight-charts.js`, offline in the exe)
- [ ] Workspace layout: top bar (ticker search, live price, market status,
      timeframe selector, mode toggles, settings), right sidebar (watchlist,
      positions, orders, account, AI analysis), bottom panel (history,
      journal, coaching, performance, strategy tester)
      *(deferred — the Charts tab has its own toolbar instead; the full
      three-panel workspace remains open, as do multi-chart layouts)*
- [x] Chart: candles, volume, zoom/pan/crosshair + OHLC legend, five
      timeframes, indicator overlays (EMA×3/VWAP/Bollinger) and synced
      RSI/MACD subpanes, fullscreen (F) *(Supertrend overlay not exposed)*
- [x] Position/order lines on the chart (2026-07-16): labeled entry/stop/
      target price lines per open position + underlying-level triggers of
      working manual orders (LIMIT orders are premium-space, not drawn)
- [x] Drawing tools: horizontal levels (persist per symbol); trend lines,
      fib retracement, zone rectangles, and bar notes (persist per
      symbol+timeframe); one-click clear; Esc cancels the active tool
- [x] Trade-from-chart (opens the order ticket with the symbol prefilled;
      deep links from watchlist rows, dashboard meters, and position cards)

### V2-5 — Replay engine
- [ ] Pick a historical day/session; future candles hidden server-side
- [ ] Play / pause / step-one-candle / speed control
- [ ] Separate replay paper account; orders fill against replay bars
- [ ] Coach reviews replay trades exactly like live ones

### V2-6 — Journal & improvement dashboard
- [ ] Chart-context snapshot per trade (candle window + entry/exit markers,
      re-rendered on demand — deliberate substitute for static screenshots)
- [ ] Notes + emotions fields; filter by strategy/symbol/P&L/date/mistake
- [ ] Improvement dashboard: win-rate trend, weaknesses, best hours/days/
      conditions, mistake frequency over time, recommended exercises

## Known constraints carried forward (honest limits)
- Free yfinance data is ~15-min delayed with limited intraday history; fills
  are simulated per scan cycle. A paid feed (Polygon/Tradier) is the upgrade
  path and slots into the existing provider interface.
- The AI coach is deterministic (built on the analysis engine), not an LLM.
- Live brokers stay out until sustained paper profitability (v1 gate stands).
