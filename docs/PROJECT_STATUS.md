# PROJECT_STATUS.md — structured project snapshot

A dashboard-style snapshot of the project, meant to be read in under a
minute. For the session-by-session narrative (why things are where they
are, exact stopping points, verification detail), see `PROJECT_STATE.md`.
For "what do I do right now," see `NEXT_SESSION.md`.

**Last verified:** 2026-07-18, V3.1 RC3 final release blockers
(`.\scripts\verify.ps1` — full test suite, HTML id references, doc
consistency, `pip check`, a headless-browser smoke check, and the 29-check
chart regression suite, all green — plus a rebuilt exe whose toolbar and
timeframe switching were driven by hand; see `PROJECT_STATE.md`).

---

## Current version

`0.1.0` (`pyproject.toml`) — pre-1.0, actively developed. No public release
process yet; the packaged artifact is a Windows desktop exe built on demand
via `scripts/build_exe.ps1`, not versioned/released independently of the
git history.

## Current phase

**V3.1 RC3 (release candidate), on branch `v3-ui` — awaiting user
approval/merge and market-hours validation.** The V3.1 sprint made the
charting system production-ready (chart-reliability root-cause fixes,
13 timeframes, infinite historical scroll, TradingView-style editable
drawings, a synced Trade-tab chart, flicker-free live updates). RC1 hardened
it (localStorage/WS guards, bounded cache); RC2 fixed the toolbar
capture-phase deselect, a market-aware stale banner, viewport recovery, and
single-owner viewport; RC3 closed the final blockers — root-caused the
"toolbar still broken" report to a STALE EXE and rebuilt it, killed the
stale-banner flapping (high-water mark), fixed timeframe-switch tiny-zoom,
and hardened rapid-switch overlay/legend state. No new features. Preceded by:
**V2 rewrite,
post-V2-4.** The original 8-phase v1 roadmap (foundation
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
| Developer scripts & automation | `dev`/`test`/`verify`/`docs`/`build`/`release`/`clean` `.ps1` entry points, `check_html_ids.py`, `check_docs.py`, `browser_check.py`, `chart_check.py`, `bump_version.py` | Committed (`7373c51`) |
| V3-0 — Chart reliability | Root cause of blank charts fixed (empty-fetch cache poisoning, no stale fallback, uncaught frontend failures); never-blank canvas with loading/error/stale states, 30s zoom-preserving refresh | Committed on `v3-ui`, browser-verified |
| V3-1 — Design system | Type/spacing/elevation tokens, inline-SVG icon nav, 56px responsive rail, flex/grid min-width blowout fix | Committed on `v3-ui` |
| V3-2 — Dashboard redesign | 2:1 layout, AI-opportunities + watchlist-movers side rail, action-oriented empty states | Committed on `v3-ui`, live-verified with a real scan |
| V3-3 — Trade screen | ATM quick-picks, risk-vs-buying-power line, on-tab positions with close-prefill, B/S/+/−/Enter order keys | Committed on `v3-ui`, browser-verified |
| V3-4 — Settings redesign | Grouped searchable config cards replace the JSON dump; live-trading flags visibly locked | Committed on `v3-ui`, browser-verified |
| V3-5 — Analytics presentation | Coach first-run explainer, journal filters + cumulative P&L curve, backtest drawdown/exit-reason panels, learning weight-shift bars | Committed on `v3-ui`, real backtest run |
| V3-6 — Accessibility | Skip link, toast live region, `scope="col"` on all 51 headers, `aria-current`, `?` shortcut overlay | Committed on `v3-ui`, browser-verified |
| V3-7 — Pre-merge audit fixes | `CandleCache` thread-safety (the disk cache silently never worked in the threaded live app), chart auto-retry for failed first loads, `?`-overlay order-key guard | Committed on `v3-ui`, each fix individually verified |
| Packaging fix (2026-07-18) | Exe shipped without yfinance (lazy `importlib` import invisible to PyInstaller since `f1bae42`): `--collect-all yfinance`, new `selftest` CLI as a post-build bundle gate, `tests/test_packaging.py` dynamic-import guard | `61a2c60`, exe rebuilt + endpoints verified live |
| V3.1-1 — Chart reliability | Per-ticker failures root-caused: drawings no longer drive the price scale (`autoscaleInfoProvider:null`), `validate_candles` drops NaN/inf/≤0 bars + zeroes bad volume + logs, indicator/serialization `isfinite` guards, renderer try/catch surfaces errors | `b93eac9`, reproduced + fixed in a headless browser |
| V3.1-2 — Timeframes | 6 → 13 intervals (1m/2m/3m/5m/10m/15m/30m/1h/2h/4h/1d/1w/1mo), table-driven (`_TF_LABEL`/`_FETCH_SPEC`/`_WINDOW_DAYS`/`CANDLE_TTL`), completeness-enforced by a test | `0d2c870`, all 13 verified against the live provider |
| V3.1-3 — Infinite history | Inverted prepend merge fixed (was replacing the window); logical-index trigger; viewport/zoom/drawings preserved; loading/end pill | `98551e1`, 206 → 407 bars on scroll-left verified |
| V3.1-4 — Editable drawings | Overlay-canvas object model: select/drag/resize/color/width/lock/hide/duplicate/rename/delete, instant tool arming, v1→v2 migration | `917d0c9`, 16-check browser lifecycle green |
| V3.1-5 — Trade-tab chart | The one chart instance relocated into a collapsible Trade slot (symbol/tf/drawings/indicators shared), preference remembered | `edfe2bc`, 10-check browser sync green |
| V3.1-6 — Live updates + perf | `chSig` includes last-bar OHLCV (forming candle no longer freezes); `series.update()` fast path for trailing bars (no flicker, no reflow) | `5e04506`, simulated intrabar tick, zero view jump |
| V3.1-7 — Chart test suite | `scripts/chart_check.py` 19-check headless-browser regression suite wired into `verify.ps1`; 10 tickers × 13 timeframes = 130/130 | `2bcb84a`, 19/19 green |
| V3.1 RC1 — Stabilization polish | Dead-code removal, `safeParse` localStorage-corruption guard, refresh-mid-interaction + wake refreshes, bounded LRU payload cache, WS frame-parse guard + reconnect-contract test; +2 chart_check checks (21) | `3a56145`, 21/21 browser + 374-test suite green |
| V3.1 RC2 — Final chart audit | Drawing-toolbar actions fixed (capture-phase deselect); market-aware stale banner (`market_open` in `/api/candles`); Reset-view / Go-to-latest + stranded-viewport recovery; single-owner viewport (one-way pane sync kills random jumps on indicator toggle); +6 chart_check checks (27) + 2 backend tests | `6f3643d`, 27/27 browser + 376 tests green |
| V3.1 RC3 — Final release blockers | Toolbar "still broken" root-caused to a STALE EXE (source fixed since RC2) → exe rebuilt; banner-flapping fixed (high-water mark: warn only when genuinely behind); timeframe-switch tiny-zoom fixed (single-owner viewport, fit on switch); stuck loading-overlay/skeleton-legend on rapid switch fixed; real-mouse toolbar test + anti-flap + tf-zoom checks (29) | `60f16a4`, 29/29 browser + 376 tests green |

## Features complete

- Multi-timeframe technical/structural/smart-money analysis library (pure functions)
- AI decision engine: confluence scoring → gate → contract selection → trade planning
- Risk-gated paper execution, identical enforcement for AI and manual entries
- SQLite trade journal, bounded/auditable learning system (AI trades only)
- Event-driven backtester sharing the live engine code
- Desktop app: FastAPI + pywebview + PyInstaller, single-file frontend
- Full manual paper-trading order book (market/limit/stop/take-profit/trailing, DAY/GTC)
- AI Mode / Human Mode toggle, deterministic post-trade coaching with a 14-tag mistake taxonomy
- Interactive chart workspace: candles/volume, indicator overlays, drawing tools (5 types), alias-safe cached revisits, automatic left-edge history backfill, position/order trade lines
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

None open. Fixed in-session (2026-07-18, packaging-fix session): the
packaged exe shipped without yfinance — every chart/quote/chain request
failed with "No module named 'yfinance'". The performance pass (`f1bae42`)
had made the yfinance import lazy via `importlib.import_module`, which
PyInstaller's static analysis cannot see, so every exe built since then
silently omitted it. Fixed with `--collect-all yfinance` in
`scripts/build_exe.ps1`, a `selftest` CLI command the build now runs
against the fresh exe (fails the build on an incomplete bundle), and
`tests/test_packaging.py` (fails the suite if any dynamic third-party
import isn't collected by the build script). One pre-existing limitation
noted while verifying: `OptionsPilot.exe serve` (the windowed exe running
the browser-serve subcommand) never binds its port — desktop `ui` mode
and dev-repo `serve` both work; tracked in `TODO.md`.

Fixed earlier (2026-07-17, automation session): the
`/favicon.ico` 404 (the one remaining browser console error from the prior
session), found immediately by the new `scripts/browser_check.py`'s first
real run. Fixed the same session before it: a halted paper account could
still place a manual market buy because `UIServer.place_order` never
called the risk preflight that existed but wasn't wired up.

## Current priorities

1. **User review of the `v3-ui` branch** — seven milestones (V3-0…V3-6)
   are committed there and verified; merging to `main` is the user's call.
2. Remaining `ROADMAP-V3-UX.md` items not yet built: notification center
   with persistence (H5), chart↔chain cross-links (N2), toast stacking
   (N4) — plus everything under "Long-term ideas."
3. Then the standing scope decision: V2-5 (replay engine), V2-6
   (journal/improvement dashboard), the deferred V2-4 workspace layout,
   or pausing feature work to accumulate paper-trading data.
4. Exe rebuild + smoke test — deferred until the V3 branch is approved.
5. Hygiene backlog: see `TODO.md` (deep per-flow browser regression
   tests, CI, linting — all recommended, none installed).

## Next milestone

Whichever of V2-5 / V2-6 / workspace-layout the user selects. See `ROADMAP.md` for scope detail on each, `NEXT_SESSION.md` for the immediate handoff.

## Test count

**376 tests, 100% passing** (`.\scripts\test.ps1`, ~13s). Frontend coverage
is real but shallow: `scripts/check_html_ids.py` (static id-reference
check), `scripts/browser_check.py` (headless browser, every tab, zero
console errors), and `scripts/chart_check.py` (chart alias, drawing, and
history regressions) all run automatically via `scripts/verify.ps1` — the
browser checks are still focused regressions, not exhaustive UI coverage
(see `TODO.md`).

## Last verified date

**2026-07-18** (V3.1 RC2 final chart audit) — `.\scripts\verify.ps1`
end to end: full pytest run (376/376), static `$("id")` reference check,
documentation consistency check, `pip check`, a headless-browser smoke
check across all 9 tabs (Playwright + system Edge) with zero console
errors, and the 29-check chart regression suite (`chart_check.py`) —
plus a 10-ticker × 13-timeframe provider sweep (130/130 monotonic) and a
rebuilt exe whose packaged charts/chains were confirmed serving live data.
