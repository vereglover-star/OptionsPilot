# PROJECT_STATE.md — exactly where this project stands right now

Read `AI_HANDOFF.md` first if you haven't. This file is the "what's done,
what's next" tracker — keep it current as you work.

**Last updated:** 2026-07-18, after the **V3.1 RC2 final chart release
audit** (branch `v3-ui`, pending the merge decision — see "Exact stopping
point" below for the four bugs fixed). Earlier the same day: the V3 chart
follow-up session (chart reliability root-caused and fixed, design system,
and redesigns of every tab — seven commits `7176843`…`79138da` on branch
**`v3-ui`**, kept off `main` pending the user's review). Three earlier sessions also landed
this date: V2-4 finish (`50c75aa`), the docs/AI framework (`1029fb0`),
and developer automation (`7373c51`). As always, trust `git log`, not
this file, for whether anything landed.

## Verified facts about current state (checked 2026-07-18)

- Full test suite: **374 tests, 100% passing** (338 from the V2-4-core
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

**2026-07-18, V3.1 RC2 final chart release audit (branch `v3-ui`).** The
last stabilization pass before the `v3-ui` → `main` merge decision. Four
remaining chart bugs, each reproduced in a real browser before any code
changed, root-caused, fixed at the architecture level, re-verified:

1. **Drawing edit-toolbar actions were dead** (recolour / duplicate / lock
   / hide / width / delete all no-op'd). The toolbar floats inside
   `#ch-main`, so the capture-phase `pointerdown` there fired first; the
   click was on the toolbar, not the drawing, so `chPointerDown` ran its
   "empty space → deselect" branch and cleared `DRAW.sel` before the
   button's click handler saw it. Fix: the capture handler ignores events
   originating in `#ch-draw-bar`.
2. **The "Live data unavailable" banner over-fired / flapped.** It shows on
   any failed live fetch that falls back to disk — but with the market
   CLOSED those disk bars ARE the last session, so it was a false alarm
   that toggled every time a background refresh hit Yahoo's rate limiter.
   Fix: `/api/candles` now reports `market_open` (from the existing
   `Orchestrator.market_open`); the banner is suppressed when closed and
   shown as a real "behind live prices" warning only when open.
3. **The chart could strand the user in whitespace.** Added **Reset view**
   and **Latest** controls (R / L keys), a logical-range `chViewportStranded()`
   detector that tells a stranded view from a deliberate deep zoom, and a
   render-time safety net (switch/first-paint only — never a same-key
   refresh, so it can't yank a chosen viewport).
4. **Random viewport jumps on RSI/MACD toggle** — the two-way subpane sync
   let a new pane's auto-fit shove its full-history range onto main
   ([166,205] → [0,191], reproduced). Fix: the main chart is the sole
   time-range owner; subpanes are one-way followers realigned via
   `chAlignPane`.

Tests: `chart_check.py` → **27 checks** (+ toolbar actions, indicator
no-jump, viewport recovery, market-aware banner, rapid-abuse stress, new-bar
append); `test_ui_server.py` +2 (`market_open`). **376 tests**, `verify.ps1`
green end to end. Performance was audited on evidence (one fetch per load,
no canvas/instance leak across 15 reparents, pane-churn loop removed) — no
speculative optimizations. Market-hours live validation remains the one
open item (market closed); the forming-candle / new-bar / indicator paths
are architecturally verified with simulated ticks. **RC2 committed as
`6f3643d` after `verify.ps1` passed end to end; `v3-ui` is still not merged
to `main` (the user's call).**

### Before that: V3.1 RC1 stabilization polish

**2026-07-18, V3.1 RC1 stabilization polish (branch `v3-ui`, `3a56145`).**
Treated the V3.1 charting work as Release Candidate 1 and ran a full
code/stability/performance audit — no new features, no redesign, the
chart architecture untouched. Findings fixed: removed four orphaned
`CH.*Series/priceLines` arrays left dead by V3.1-4; hardened the two
`JSON.parse(localStorage…)` sites behind a `safeParse` helper so a
corrupt `chInds` (app-init) or drawings store (per-symbol render) resets
to default instead of throwing; guarded the 30s auto-refresh so it never
re-renders mid-drag / mid-tool-placement / mid-history-load; added a
`visibilitychange` refresh so the chart isn't stale for up to a cadence
after wake; bounded the previously-unbounded per-(symbol·tf) payload
cache to a 24-entry LRU; and guarded the WebSocket frame parse. Expanded
coverage: a WS-contract backend test (full-payload-on-connect →
heartbeat, the basis of reconnect catch-up) and two `chart_check`
checks (corrupt-localStorage recovery, LRU bound) → 21 browser checks,
**374 tests**. No source TODO/FIXME/XXX; version consistent at 0.1.0.
Remaining work is genuine market-hours validation (architecture verified
capable) — see the RC1 checklist. **This RC1 pass is uncommitted pending
`verify.ps1` green.**

Before that, 2026-07-18, the V3.1 chart-stabilization sprint (committed
`61a2c60`…`2bcb84a`) made the charting system the strongest part of the app. Seven milestones, each root-caused and
browser-verified before commit: (1) chart reliability — the "some
tickers fail / IWM only shows volume" bug traced to three causes
(drawings driving the price scale, NaN volume 500-ing the endpoint,
non-finite indicator values) and fixed at the data boundary
(`validate_candles` sanitization + logging) and the renderer; (2)
timeframes expanded 6 → 13 (1m…1mo), table-driven; (3) infinite
historical scroll fixed (the merge was inverted — replaced the window
instead of prepending; the trigger mixed bar-index and timestamp units);
(4) editable TradingView-style drawing objects on an overlay canvas
(select/drag/resize/color/width/lock/hide/duplicate/rename/delete +
migration); (5) a collapsible chart on the Trade page (the one instance
relocated between tabs, so everything is shared); (6) live-update
correctness (the forming candle froze because `chSig` ignored intrabar
changes) + a flicker-free `series.update()` fast path; (7) a 19-check
headless-browser regression suite (`scripts/chart_check.py`) wired into
`verify.ps1`. Verified: all 10 required tickers × 13 timeframes return
monotonic real data (130/130); `chart_check` 19/19 green; **374 tests**.
The exe was rebuilt and the packaged charts confirmed working. The
`v3-ui` merge decision remains the user's to make.

Before that, 2026-07-18 packaging-fix session (committed `61a2c60`). The
user found the freshly built exe release-blocked: every chart and
option-chain request failed with "No module named 'yfinance'". Root
cause (traced, not guessed): the performance pass `f1bae42` made the
yfinance import lazy via `importlib.import_module()`, which PyInstaller's
static import scan cannot see — every exe built since then silently
shipped without yfinance and its whole dependency tree. The dev venv was
never affected, and this is not a V3 UI regression (V3-0's error
surfacing is what made it visible instead of a blank canvas). Fixed:
`--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
CLI command (forces the lazy imports, offline) that the build script now
runs against the freshly built exe and fails the build on; and
`tests/test_packaging.py` (+4) failing the ordinary suite if any dynamic
third-party import isn't collected by the build script. Verified: exe
rebuilt (selftest gate PASS, `yfinance 1.5.1` + `curl_cffi` physically
in `_internal`), packaged desktop app served 206 daily SPY candles, 624
SMCI 5m candles, and a 231-contract chain live over HTTP; full
browser-flow sweep of the chart system green (load, indicators,
drawings, trade lines, stale banner, retry, 30s auto-refresh — zero
console errors); `verify.ps1` green end-to-end; **374 tests**. One
pre-existing quirk found and documented (not fixed): `OptionsPilot.exe
serve` from the windowed exe never binds its port (`TODO.md`). Committed
as `61a2c60`; the V3.1 sprint above built on it.

Before that, 2026-07-17, end of the V3 UI sprint (branch `v3-ui`, seven commits,
each browser-verified before committing — see `NEXT_SESSION.md` for the
per-milestone list and `CHANGELOG.md` for detail).** Key facts a next
session needs: the chart blank-canvas bug is fixed at the root
(empty-fetch cache poisoning in `CachedProvider` + missing stale
fallback + uncaught frontend failures + a mid-load switch race); the
engine's strict fail-closed candle path is deliberately unchanged; 351
tests pass; `scripts/verify.ps1` ran clean as the session's closing
action. The branch is **not merged** — that's the user's call. The
order-ticket *fill* path (post-fill stop-loss pre-arm) still needs one
market-hours manual pass; everything else was verified live, including
the risk gate visibly rejecting an after-hours order (correct behavior).

Earlier the same date, the V2-4-finish session ended as follows (kept
for the record):

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

**The exe was rebuilt 2026-07-18** with the packaging fix and verified
serving live data (see the current stopping point above).

## Next recommended task

1. User review of the `v3-ui` branch → merge decision. Don't build on
   the branch until then.
2. If V3 continues: the remaining `ROADMAP-V3-UX.md` items (H5
   notification center, N2 chart↔chain links, N4 toast stacking).
3. Then the standing scope decision: V2-5 (replay engine), V2-6 (journal
   dashboard), the V2-4 three-panel workspace layout, or pause to
   accumulate paper-trading data — user's call.
4. Eventually: rebuild + smoke-test the exe (LAST, once the branch state
   settles).

## Current priorities

1. `v3-ui` review/merge — user's call.
2. The what-next scope decision after that.
3. Exe rebuild deliberately LAST.

## Blockers

None.
