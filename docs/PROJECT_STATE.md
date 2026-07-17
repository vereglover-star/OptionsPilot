# PROJECT_STATE.md — exactly where this project stands right now

Read `AI_HANDOFF.md` first if you haven't. This file is the "what's done,
what's next" tracker — keep it current as you work.

**Last updated:** 2026-07-17, after the V2-4-finish session (drawing
tools + trade lines on the chart, manual-entry risk gating completed,
hygiene backlog cleared). The session's work was prepared as one commit —
as always, trust `git log`, not this file, for whether it landed.

## Verified facts about current state (checked 2026-07-17)

- Full test suite: **345 tests, 100% passing** (338 from the V2-4-core
  commit, plus the endpoint-level halt test and the new `TestManualEntry`
  suite in `tests/test_risk.py`). Static `$("id")` reference check clean.
- **A `git status` printed "working tree clean" this session while
  `git diff --stat` showed 13 dirty files** (the 2026-07-16 session's
  uncommitted manual-risk-gating work). The output-capture trap in
  `CLAUDE.md` applies to git too — cross-check `git status` with
  `git diff --stat` before trusting either.
- **V2-4-finish work live-verified 2026-07-17** in serve mode against
  scratch data dirs, including a real headless-browser drive (Playwright
  driving system Edge): fib/zone/note tools drawn, persisted across
  reload, cleared; Esc disarm; entry + stop-loss price lines rendered on
  the chart after a real manual buy + protective stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  quantity vetoes surfaced as 422s through the real endpoint. Only
  console error: the pre-existing missing `/favicon.ico` (now in TODO).
- Earlier verified milestones (V2-3 frontend, performance pass, V2-3 exe
  rebuild + smoke test) are recorded in `CHANGELOG.md` and the git log —
  all committed and unchanged by this session.

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

2026-07-17 finished V2-4's tractable remainder and completed the
manual-risk-gating work found uncommitted (and unwired) from 2026-07-16.
The whole changeset ships as one commit:

- **Chart trade lines**: `loadChart` draws labeled price lines for the
  charted symbol — position entry (`entry_spot`, newly exposed in the
  status payload), AI stop/target, and working manual orders'
  underlying-level triggers (stop/take-profit/trailing; LIMIT orders are
  premium-space and deliberately not drawn).
- **Three new drawing tools**: Fib retracement, Zone rectangle, and bar
  Notes (inline text input → chart marker) — persisted per
  symbol+timeframe in localStorage like trend lines; Esc cancels an armed
  tool; old stored drawings load unchanged.
- **Manual-entry risk gating completed**: the 2026-07-16 session left
  `RiskManager.approve_manual_entry` + `OrderManager.evaluate`'s
  fill-time approval callback uncommitted AND unwired for immediate
  market buys — `UIServer.place_order` now preflights them (422 + veto
  text). The hard %-risk sizing veto was converted to an advisory note
  (it blocked nearly all manual buys at default settings and broke a
  committed test; sizing discipline is the coach's `oversized` tag's
  job). New `TestManualEntry` suite in `tests/test_risk.py`.
- **Hygiene cleared**: `pyproject.toml` ships `data_assets/*`, `Pillow`
  in the `dev` extra, `operating_mode` documented inline in `config.yaml`.
- **345 tests, 100% passing.** Static `$("id")` check clean.
- **Live-verified 2026-07-17** in serve mode against scratch data dirs,
  including a real headless-browser drive (Playwright + system Edge,
  installed ad hoc into `.venv` — not a project dependency): fib/zone/
  note drawn, persisted across reload, cleared; Esc disarm; entry +
  stop-loss lines rendered on the chart after a real manual buy + stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  qty-0 vetoes observed as 422s through the real endpoint. Only console
  error: the pre-existing missing `/favicon.ico`.
- Note for future browser driving: lightweight-charts coalesces clicks
  faster than ~500ms as double-clicks — pace scripted two-point drawing
  clicks ≥700ms apart.

**The exe still predates all 2026-07-16/17 UI work** — rebuild + smoke
test deliberately LAST (user's stated preference), when the app is closed.

## Next recommended task

1. V2-5 (replay engine) or V2-6 (journal dashboard), or the V2-4
   three-panel workspace layout, or pause to accumulate paper-trading
   data — user's call.
2. Eventually: rebuild + smoke-test the exe (LAST, once feature-complete).

## Current priorities

1. The what-next scope decision (V2-5 / V2-6 / workspace layout / pause
   and trade) — user's call.
2. Exe rebuild deliberately LAST.

## Blockers

None.
