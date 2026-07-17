# PROJECT_STATUS.md — structured project snapshot

A dashboard-style snapshot of the project, meant to be read in under a
minute. For the session-by-session narrative (why things are where they
are, exact stopping points, verification detail), see `PROJECT_STATE.md`.
For "what do I do right now," see `NEXT_SESSION.md`.

**Last verified:** 2026-07-17 (`.\scripts\verify.ps1` — full test suite,
HTML id references, doc consistency, `pip check`, and a headless-browser
smoke check, all green; see `PROJECT_STATE.md` for detail).

---

## Current version

`0.1.0` (`pyproject.toml`) — pre-1.0, actively developed. No public release
process yet; the packaged artifact is a Windows desktop exe built on demand
via `scripts/build_exe.ps1`, not versioned/released independently of the
git history.

## Current phase

**V2 rewrite, post-V2-4.** The original 8-phase v1 roadmap (foundation
through hardening) is complete and stable. V2 layers a professional desktop
trading-platform experience on top: watchlist management, a full manual
paper-trading order book, AI Mode vs. Human Mode with a deterministic trade
coach, and an interactive chart workspace. V2-4's tractable scope (chart
drawing tools + trade lines) shipped 2026-07-17; V2-5 (replay engine) and
V2-6 (journal/improvement dashboard) are not started.

## Completed milestones

| Milestone | What it shipped | Status |
|---|---|---|
| v1 Phases 1–8 | Full analysis suite, AI decision engine, risk-gated paper broker, journal/learning/backtester, orchestrator + notifications, desktop UI, hardening (soak harness, TradingView webhook, broker registry stubs) | Committed, stable |
| Trading modes (2026-07-14) | Conservative (fixed bar) vs. High-Risk (setup-quality-adaptive bar) `trading_mode` | Committed, stable |
| V2-0 — Stabilize | Watchlist manager (autocomplete, presets, favorites, pin/reorder), `RuntimeSettings` overlay, in-app trading-mode toggle | Committed, stable |
| V2-1 — True desktop app | `--windowed` no-console PyInstaller build, app icon, single-instance guard, windowed-safe logging | Committed, stable |
| V2-2 — Manual trading engine | `OrderManager` (market/limit/stop/take-profit/trailing), Trade tab, account metrics | Committed, stable |
| V2-3 — AI Mode vs. Human Mode | `operating_mode` axis, manual-trade reconciliation loop, `TradeCoach` (process-scored reviews), Coach tab | Committed, live-verified, exe rebuilt |
| Performance & polish pass | Scan cycle 14.9s → ~0.1s warm, non-blocking scans, brokerage-style UI redesign | Committed, stable |
| V2-4 — Chart workspace | Vendored lightweight-charts, `/api/candles`, five-timeframe chart with indicator overlays/subpanes, drawing tools (level/trend/fib/zone/note), position/order trade lines, trade-from-chart | Committed, live-verified |
| Documentation & AI framework | `PROJECT_STATUS.md`/`ROADMAP.md`/`ARCHITECTURE.md` (with diagrams)/`AI_CONTEXT.md`/`NEXT_SESSION.md`/`CONTRIBUTING.md` | Committed |
| Developer scripts & automation | `dev`/`test`/`verify`/`docs`/`build`/`release`/`clean` `.ps1` entry points, `check_html_ids.py`, `check_docs.py`, `browser_check.py`, `bump_version.py` | Not yet committed (see "Exact stopping point" in `PROJECT_STATE.md`) |

## Features complete

- Multi-timeframe technical/structural/smart-money analysis library (pure functions)
- AI decision engine: confluence scoring → gate → contract selection → trade planning
- Risk-gated paper execution, identical enforcement for AI and manual entries
- SQLite trade journal, bounded/auditable learning system (AI trades only)
- Event-driven backtester sharing the live engine code
- Desktop app: FastAPI + pywebview + PyInstaller, single-file frontend
- Full manual paper-trading order book (market/limit/stop/take-profit/trailing, DAY/GTC)
- AI Mode / Human Mode toggle, deterministic post-trade coaching with a 14-tag mistake taxonomy
- Interactive chart workspace: candles/volume, indicator overlays, drawing tools (5 types), position/order trade lines
- Watchlist manager with a bundled 12k-symbol offline directory
- TradingView inbound webhook (scan-trigger only, never places an order)

## Features in progress / partially built

- **V2-4 remainder**: the full three-panel workspace layout (top bar / right sidebar / bottom panel) and multi-chart layouts are explicitly deferred — not started, no code exists.
- **V2-6 scope overlap**: the Coach tab's `CoachProfile` already covers *some* of V2-6's "improvement dashboard" intent (recurring mistakes, score trend, win rate by setup quality) — the full V2-6 spec (chart-context snapshots, notes/emotions fields, journal filtering UI) is not built.

## Known limitations (deliberate, documented — not bugs)

- yfinance data is ~15-minute delayed with limited intraday history (~60 days of 5m bars); paid-feed adapters are the documented upgrade path.
- No historical option-chain data exists for free; the backtester reconstructs option prices via Black-Scholes.
- Manual/working orders evaluate once per scan cycle against fresh quotes — no intrabar/tick simulation.
- The coach infers behavioral tags (revenge trading, chased entry) from observable timing patterns, not literal intent.
- No live-broker implementation exists anywhere — this is the core safety property of the system, not a gap to close casually (see `CLAUDE.md`).

## Known bugs

None open. Fixed in-session (2026-07-17, automation session): the
`/favicon.ico` 404 (the one remaining browser console error from the prior
session), found immediately by the new `scripts/browser_check.py`'s first
real run. Fixed the same session before it: a halted paper account could
still place a manual market buy because `UIServer.place_order` never
called the risk preflight that existed but wasn't wired up.

## Current priorities

1. **Commit this session's automation work** (`scripts/*`, `pyproject.toml`
   extras, `docs/QUICK_START.md`, `docs/RELEASE_CHECKLIST.md`, the favicon
   fix, and doc updates) — currently uncommitted working-tree changes.
2. Scope decision for what's next: V2-5 (replay engine), V2-6 (journal/improvement dashboard), the deferred V2-4 workspace layout, or pausing feature work to accumulate paper-trading data — this is the user's call, not a technical blocker.
3. Exe rebuild + smoke test — deliberately deferred until feature-complete (user's stated preference), not urgent.
4. Hygiene backlog: see `TODO.md` (currently: deep per-flow browser regression tests, CI, linting — all recommended, none installed).

## Next milestone

Whichever of V2-5 / V2-6 / workspace-layout the user selects. See `ROADMAP.md` for scope detail on each, `NEXT_SESSION.md` for the immediate handoff.

## Test count

**345 tests, 100% passing** (`.\scripts\test.ps1`, ~13s). Frontend coverage
is real but shallow: `scripts/check_html_ids.py` (static id-reference
check) and `scripts/browser_check.py` (headless browser, every tab, zero
console errors) both run automatically via `scripts/verify.ps1` — neither
is deep per-flow regression coverage (see `TODO.md`).

## Last verified date

**2026-07-17** — `.\scripts\verify.ps1` end to end: full pytest run
(345/345), static `$("id")` reference check, documentation consistency
check, `pip check`, and a headless-browser smoke check across all 9 tabs
(Playwright + system Edge) with zero console errors.
