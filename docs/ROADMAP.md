# OptionsPilot — Roadmap

This is the top-level, always-current roadmap. For granular per-phase
checklists and acceptance detail on the V2 rewrite, see `ROADMAP-V2.md`
(this file summarizes; that file itemizes). For exact dates, commit hashes,
and prose descriptions of what shipped, see `CHANGELOG.md`.

---

## Completed

### v1 — Original 8-phase build (2026-07-11, commit `40eb1ea`)

Foundation, analysis suite, AI decision engine, risk manager + paper
broker, journal/learning/backtester, orchestrator + notifications, desktop
UI, hardening (soak harness, TradingView webhook, broker registry stubs,
performance pass). Fully committed, tested, packaged. See `ROADMAP-V2.md`
"Phases" section header for the original phase-by-phase list if needed —
it predates that file and is preserved in git history.

### Trading modes (2026-07-14)

Conservative (fixed confidence bar) and High-Risk (setup-quality-adaptive
bar) `trading_mode`, with identical risk management underneath both.

### V2-0 — Stabilize

Watchlist manager (autocomplete against a bundled 12k-symbol directory,
presets, favorites, pin/reorder), `RuntimeSettings` overlay system, in-app
trading-mode toggle — all live, no-restart.

### V2-1 — True desktop application

`--windowed` no-console PyInstaller build, generated app icon,
single-instance guard, windowed-safe logging.

### V2-2 — Manual trading engine

`OrderManager` (market/limit/stop-loss/take-profit/trailing, DAY/GTC), the
Trade tab, account metrics. `Position.managed_by` separates AI-managed from
user-managed positions — this distinction is load-bearing throughout the
codebase (see `AI_CONTEXT.md`).

### V2-3 — AI Mode vs. Human Mode

The `operating_mode` axis (independent of `trading_mode`), the manual-trade
reconciliation loop, and `TradeCoach` — a deterministic, process-scored
post-trade review system with a 14-tag mistake taxonomy. Live-verified in a
real browser; the exe was rebuilt and smoke-tested the same day.

### Performance & polish pass (2026-07-16)

Scan cycle profiled and optimized end-to-end (14.9s → ~0.1s warm),
non-blocking `/api/scan`, brokerage-style UI redesign.

### V2-4 — Chart workspace

Vendored `lightweight-charts`, `/api/candles` (indicators from the same
`analysis/` code the engine trades with), a five-timeframe interactive
chart with EMA/VWAP/Bollinger overlays and synced RSI/MACD subpanes,
fullscreen, five drawing tools (horizontal level, trend line, fib
retracement, zone rectangle, bar note — all persisted per symbol or
symbol+timeframe in localStorage), position/order price lines on the
chart, and trade-from-chart deep links. **The three-panel workspace layout
and multi-chart layouts are explicitly deferred** — see "Deferred" below.

### V3 product-quality sprint, milestones 0–6 (2026-07-17, branch `v3-ui`)

A UX/reliability sprint, not a feature sprint — scoped by the full audit in
`ROADMAP-V3-UX.md`. Chart reliability root-caused and fixed (never-blank
canvas, stale-data fallback for display only, 30s zoom-preserving
refresh), a design-token system + responsive icon-rail nav, and redesigns
of Dashboard, Trade (ATM quick-picks, risk context, order-entry keys),
Settings (structured cards replace the JSON dump), the four analytics
tabs, and an accessibility pass. Seven commits, each browser-verified.
**On `v3-ui`, awaiting user review — not merged to `main`.**

---

## In Progress

**The `v3-ui` branch is awaiting the user's review/merge decision.**
Remaining audit items deliberately not built (see `ROADMAP-V3-UX.md`):
notification center with persistence (H5), chart↔chain cross-links (N2),
toast stacking (N4), and everything under "Long-term ideas." Beyond that,
nothing is actively in progress — each phase ships as a complete, tested,
documented unit before the next begins (see `CLAUDE.md`).

---

## Planned

Listed in the order they appear in `ROADMAP-V2.md`; no priority is implied
beyond that ordering. Which one comes next is the user's call.

### V2-5 — Replay engine

- Pick a historical day/session; future candles hidden server-side.
- Play / pause / step-one-candle / speed control.
- Separate replay paper account; orders fill against replay bars.
- `TradeCoach` reviews replay trades exactly like live ones.

### V2-6 — Journal & improvement dashboard

- Chart-context snapshot per trade (candle window + entry/exit markers,
  re-rendered on demand — the deliberate substitute for static
  screenshots, documented in `ROADMAP-V2.md`).
- Notes + emotions fields; filter by strategy/symbol/P&L/date/mistake.
- Improvement dashboard: win-rate trend, weaknesses, best hours/days/
  conditions, mistake frequency over time, recommended exercises.
- **Partial overlap already shipped**: the V2-3 Coach tab's `CoachProfile`
  already covers recurring mistakes, score trend, and win rate by setup
  quality. V2-6 is additive on top of that, not a rebuild — the remaining
  scope is chart snapshots, notes/emotions capture, and journal filtering
  UI specifically.

### V2-4 workspace remainder (optional, large)

The full three-panel layout (top bar / right sidebar / bottom panel) and
multi-chart layouts. Deferred as a deliberate, separate design decision —
the current single-chart-plus-toolbar Charts tab is a complete, usable
substitute, so this is a UI-restructuring project rather than a missing
feature.

---

## Deferred

Explicitly considered and pushed out, with the reason recorded so it isn't
re-litigated by accident:

- **Stock/share (non-option) manual positions** — deferred from V2-2. Would
  need a new "stock leg" position shape and touch `broker/orders.py`,
  `PaperBroker`, and the Trade tab's chain/ticket UI (currently
  options-only).
- **A real live-broker adapter** (Alpaca's options paper API is the natural
  first candidate) — explicitly gated on sustained paper profitability.
  Building this without a direct, dedicated user request is against the
  project's core safety rule; see `CLAUDE.md`.
- **A paid market-data feed** (Polygon/Tradier) — the free yfinance
  provider is adequate for paper trading and strategy development; this is
  the documented upgrade path for serious intraday work, not a current need.
- **News / economic-calendar / sentiment evidence** — would be a new
  `ConfluenceScorer` evidence type; no design work started.
- **Portfolio-level risk** (correlated positions, sector exposure limits) —
  the current `RiskManager` reasons per-position only.
- **Candle cache for the live loop** (incremental fetch + merge) — the
  `CachedProvider` already made this low-urgency (warm cycles are ~0.1s);
  worth revisiting only if yfinance rate-limiting becomes a problem at a
  larger watchlist size.

---

## Long-term Vision

From the user's own framing of the project (see `AI_HANDOFF.md`): *"a
polished, professional desktop trading platform that combines the best
aspects of TradingView, Webull, and Thinkorswim, while adding an AI trading
coach that can both trade autonomously in AI Mode and teach me in Human
Mode."*

That vision has two structural pillars that are not up for casual
revision:

1. **Paper trading only, permanently, unless the user explicitly asks
   otherwise in a dedicated request.** The system is deliberately built so
   a live-broker adapter *could* slot into the existing `Broker` interface
   someday, gated behind sustained paper profitability and a two-flag
   opt-in — but building that adapter is a decision the user makes once,
   explicitly, not a natural next step to infer from "make it better."
2. **Deterministic, auditable logic over ML/LLM black boxes.** The scorer,
   the gate, and the coach are all hand-authored rule systems by design —
   this is what makes every trade decision fully reconstructable from logs
   and every coaching verdict explainable in plain English. An LLM call in
   the trading or coaching path would break that property.

Beyond the currently-scoped V2-5/V2-6 phases, longer-horizon ideas that
have been discussed but not scoped: a desktop app for other platforms
(macOS/Linux — currently Windows-only via pywebview/PyInstaller; see
`AI_CONTEXT.md` "Future desktop plans"), and no mobile plans exist or are
anticipated (the analysis engine is pandas/numpy-heavy and the UI assumes
a desktop-sized viewport; see `AI_CONTEXT.md` "Future mobile plans").
