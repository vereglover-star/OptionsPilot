# PROJECT_STATE.md — exactly where this project stands right now

Read `AI_HANDOFF.md` first if you haven't. This file is the "what's done,
what's next" tracker — keep it current as you work.

**Last updated:** 2026-07-16, end of the V2-3 verification-and-commit
session (live browser verification of the V2-3 frontend, doc updates, and
the V2-3 commit itself).

## Verified facts about current state (checked this session)

- **V2-3 is committed.** The commit includes the coach package, human-mode
  orchestrator changes, new API endpoints, the frontend (AI/Human toggle,
  Coach tab), both new test files, and the full doc set (`CLAUDE.md`,
  `docs/AI_HANDOFF.md`, `docs/PROJECT_STATE.md`, `docs/TODO.md`,
  `docs/CHANGELOG.md`). Run `git log --oneline -5` to see it.
- Full test suite: **310 tests, 100% passing** (rerun this session before
  committing; zero `F`/`E` markers).
- **The V2-3 frontend was live-verified in a real browser this session**,
  against a scratch data directory (so the user's real paper account was
  untouched). Verified: AI/Human toggle switches, persists to
  `data/settings.json`, and survives page reload; Coach tab renders its
  empty state; a full manual round trip (SPY call chain load → contract
  select → market buy fill → sell-to-close fill) works through the Trade
  tab; a scan cycle reconciles the closed manual trade into a coach review
  (correct `no_stop` mistake tag, process score, verdict); the Coach tab
  renders the review with working expandable detail rows; switching
  `trading_mode` does not flip `operating_mode`; zero browser console
  errors throughout. No bugs found — no code changes were needed.
- **The exe was rebuilt with V2-3 and smoke-tested this session** (after
  the user closed their running instance). `build_exe.ps1` backed up and
  restored the app's real `data/` as designed. The packaged app was
  smoke-tested in serve mode against a scratch data directory: AI→Human
  toggle, SPY manual round trip via the Trade tab, scan cycle → coach
  review rendered in the Coach tab with correct `no_stop` tag, zero
  console errors. V2-3 has no remaining follow-ups.

## Completed (phases 1–8, the original v1 roadmap — see `docs/ROADMAP.md`)

All 8 original phases are done: foundation, analysis suite, AI decision
engine, risk manager + paper broker, journal/learning/backtester,
orchestrator + notifications, desktop UI, hardening (perf pass, TradingView
webhook, broker registry stubs, soak-test harness). This was a prior
session's work and is fully committed, tested, and packaged.

## Completed in the V2 rewrite

### V2-0 — Stabilize (committed)
Watchlist manager (quick add, bulk paste, autocomplete against a bundled
12k-symbol directory, 9 preset lists, favorites, pin/drag/sort/filter,
30-symbol cap) + `RuntimeSettings` overlay system + in-app trading-mode
toggle, all with no-restart live application. 272 tests at the time.

### V2-1 — True desktop application (committed, `0ce001d`)
`--windowed` no-console PyInstaller build, generated app icon
(`scripts/make_icon.py` → `assets/optionspilot.ico`), single-instance guard
(localhost-port mutex; second launch shows a friendly notice window instead
of corrupting the shared SQLite files), windowed-safe logging (skips the
console `StreamHandler` when `sys.stderr is None`).

### V2-2 — Trading engine: orders, manual trading, account metrics (committed, `0ce001d`)
`OrderManager` (`broker/orders.py`): MARKET / LIMIT / STOP_LOSS /
TAKE_PROFIT / TRAILING_STOP orders, DAY/GTC time-in-force, position scaling,
reservation checks (can't oversell a position across bracket orders),
auto-cancel on position close, full SQLite persistence, restart-safe (fills
after restart use live quotes, never stale stored ones). Manual trading API
(`/api/chain`, `/api/orders*`, `/api/account/metrics`) and the Trade tab UI
(account cards, live option chain with Greeks, order ticket, working orders
+ history, one-click position close). `Position.managed_by` field
(`"ai"`/`"manual"`) separates AI-managed from user-managed positions.
Deferred: stock/share positions (options only for now).

### V2-3 — AI Mode vs Human Mode (committed, verified 2026-07-16)

- `EngineConfig.operating_mode: "ai" | "human"` (default `"ai"`), validated,
  independent of `trading_mode` (switching one never flips the other — see
  `RuntimeSettings._apply_mode`'s explicit preservation of
  `operating_mode`).
- `RuntimeSettings.set_operating_mode()` — instant, persisted, no restart.
- `Orchestrator._scan_symbol`: in Human Mode, a tradeable signal becomes a
  one-time "advice only" notification per bar instead of an order — the AI
  genuinely never calls `open_position()` in this mode.
- `Orchestrator._reconcile_manual` / `_capture_context` /
  `_capture_context_for_symbol` / `_finalize_manual`: detects manual
  position open/close by diffing `managed_by="manual"` positions
  cycle-to-cycle, captures analysis context while open (best-effort),
  rebuilds the round trip from `PaperBroker.fills_for()` +
  `OrderManager.orders_for()` on close, and journals it with a
  `TradeCoach` review attached.
- `coach/coach.py` — `TradeCoach.review()`: full before/during/after
  breakdown, 14-tag mistake taxonomy (each with a "what a pro would do" note
  and a concrete exercise), process-based score 0–100 (NOT outcome-based —
  this is load-bearing and tested explicitly:
  `test_disciplined_loser_scores_well` / `test_reckless_winner_scores_badly`
  in `tests/test_coach.py`).
- `coach/profile.py` — `CoachProfile.build()`: aggregates all persisted
  reviews into recurring mistakes, top strengths, score trend, win rate by
  setup quality, top-3 recommended exercises.
- API: `POST /api/operating_mode`, `GET /api/coach`.
- UI: header segmented control (`#op-seg`, "🤖 AI trades" / "👤 You trade"),
  new Coach tab (`#tab-coach`) with cards, recurring-mistakes panel,
  strengths/exercises panel, expandable review-detail table. **Live-verified
  in a real browser this session — see "Verified facts" above.**
- Tests: `tests/test_coach.py` (13 tests) and `tests/test_human_mode.py`.
  All passing (310 total).

## Not started

- **V2-4 — Chart workspace**: bundling `lightweight-charts`, the
  TradingView-inspired layout (top bar/right sidebar/bottom panel), drawing
  tools overlay, trade-from-chart. Nothing exists yet beyond the roadmap
  entry.
- **V2-5 — Replay engine**: historical day replay with hidden future
  candles, play/pause/step/speed, separate replay account, coach review of
  replay trades. Nothing exists yet.
- **V2-6 — Journal & improvement dashboard**: chart-context snapshots per
  trade (the deliberate re-renderable-data substitute for screenshots),
  notes/emotions fields, filtering by strategy/symbol/P&L/date/mistake, and
  a dedicated improvement-trend dashboard (the Coach tab built in V2-3
  covers *some* of this via `CoachProfile`, but the full V2-6 spec — journal
  filtering UI, notes/emotions capture, chart snapshots — is not built).
- Stock/share positions (deferred from V2-2).
- Everything in `ROADMAP-V2.md`'s "Beyond v1" section: live-loop candle
  cache, a real Alpaca paper-API adapter, news/sentiment evidence,
  portfolio-level risk.

## Exact stopping point

This session (2026-07-16) live-verified the V2-3 frontend in a real
browser (no bugs found), updated all docs, committed V2-3, rebuilt the exe
with V2-3 included, and smoke-tested the packaged app end-to-end. **V2-3 is
fully done — code, tests, docs, commit, and packaged build.**

## Next recommended task

**Decide with the user** whether to start V2-4 (chart workspace) per
`ROADMAP-V2.md`, or pause feature work so the user can run the app in its
current state for a while (market-hours soak + accumulating paper trades
was the stated plan). This is a scope decision, not a technical one — ask
if it isn't clear from the conversation. Medium-priority hygiene items
(`pyproject.toml` package-data fix, Pillow extra, `operating_mode` yaml
comment) are available as small filler tasks either way — see `TODO.md`.

## Current priorities

1. The V2-4-now-or-pause scope decision (user's call).
2. No technical work is currently blocking.

## Blockers

None.
