# NEXT_SESSION.md — start here

Concise session-to-session handoff. Keep this current — update it at the end
of every significant session, not "later." For the detailed narrative behind
any of this, see `PROJECT_STATE.md`; for the structured snapshot, see
`PROJECT_STATUS.md`.

**Last updated:** 2026-07-18, end of the V3.1 chart-stabilization sprint.

## What was completed?

The **V3.1 chart-stabilization sprint**, on branch **`v3-ui`** (still not
merged — the user asked for `v3-ui` to stay isolated until reviewed).
Seven milestones, each root-caused and browser-verified before commit,
which made the charting system the strongest part of the app:

1. **V3.1-1 `b93eac9` — chart reliability.** The "some tickers randomly
   fail / IWM only shows volume" reports traced to three causes: a stored
   drawing with a stray price drove the price scale and crushed the
   candles (drawings now use `autoscaleInfoProvider:null`); NaN volume on
   the forming bar 500'd the endpoint during JSON serialization
   (`validate_candles` now drops NaN/inf/≤0 OHLC bars, zeroes non-finite
   volume, and logs every removal with symbol/tf context); and non-finite
   indicator values (payload runs one `validate_candles` choke point +
   `isfinite` guards). Renderer wrapped in try/catch → error overlay, not
   a half-painted canvas.
2. **V3.1-2 `0d2c870` — 13 timeframes.** 1m/2m/3m/5m/10m/15m/30m/1h/2h/
   4h/1d/1w/1mo, table-driven (`core/models._TF_LABEL`,
   `yfinance_provider._FETCH_SPEC`, `orchestrator._WINDOW_DAYS`,
   `cached.CANDLE_TTL`); a test fails if a member isn't in all four.
3. **V3.1-3 `98551e1` — infinite scroll-back.** The paging merge was
   inverted (replaced the window instead of prepending) and the trigger
   mixed bar-index vs timestamp units. Older bars now prepend with
   indicators in lockstep; viewport/zoom/drawings preserved.
4. **V3.1-4 `917d0c9` — editable drawings.** Overlay-canvas object model
   (`{id,type,tf,points,color,width,text,locked,hidden}`, stored
   `{version:2,items}`, old format migrated): select/drag/resize/color/
   width/lock/hide/duplicate/rename/delete, instant tool arming.
5. **V3.1-5 `edfe2bc` — Trade-tab chart.** The one chart instance
   relocated into a collapsible Trade slot; symbol/tf/drawings/indicators
   shared; preference remembered.
6. **V3.1-6 `5e04506` — live updates + perf.** `chSig` now includes the
   last bar's OHLCV (the forming candle no longer freezes intrabar); a
   `series.update()` fast path renders trailing bars with no flicker/reflow.
7. **V3.1-7 `2bcb84a` — chart test suite.** `scripts/chart_check.py`
   (19 headless-browser checks) wired into `verify.ps1`; verified 10
   tickers × 13 timeframes = 130/130.

Immediately preceding this sprint (same day, `61a2c60`): the packaged exe
shipped without yfinance (lazy `importlib` import invisible to
PyInstaller) — fixed with `--collect-all yfinance`, a `selftest` build
gate, and `tests/test_packaging.py`.

### Earlier: the V3 product-quality sprint (`v3-ui`)

Seven milestones (V3-0 … V3-6) + a pre-merge audit (V3-7), each built →
verified in a real browser → committed separately:

1. **V3-0 `7176843` — chart reliability.** The "app opens with no usable
   charts" bug was root-caused (not guessed): yfinance returns *empty*
   frames on transient failures, `CachedProvider` memoized those empties
   for the full TTL (poisoning retries), the disk cache was never used as
   a fallback, and the frontend had no catch/retry and silently dropped
   mid-load switches. Fixed at all four layers; the canvas can no longer
   be silently blank (loading overlay → error overlay with Retry → stale
   banner), charts auto-refresh every 30s preserving zoom. The engine's
   strict fail-closed data path is unchanged (tested explicitly).
2. **V3-1 `e06031c` — design system.** Type/spacing/elevation tokens,
   inline-SVG icon nav, 56px collapsed rail below 1180px, and a real
   pre-existing flex/grid min-width blowout fixed (`main{min-width:0}`,
   `minmax(0,1fr)`).
3. **V3-2 `641d617` — dashboard.** 2:1 layout, AI-opportunities and
   watchlist-movers side rail, action-oriented empty states.
4. **V3-3 `629c19d` — trade screen.** ATM quick-picks, risk-vs-buying-power
   line, on-tab positions with close-prefill, B/S/+/−/Enter order keys.
5. **V3-4 `a365871` — settings.** Grouped searchable config cards replace
   the JSON dump; live-trading flags visibly locked (🔒 off by design).
6. **V3-5 `776d23d` — analytics.** Coach first-run explainer, journal
   filters + cumulative-P&L curve, backtest drawdown/exit-reason panels,
   learning weight-shift bars.
7. **V3-6 `79138da` — accessibility.** Skip link, toast live region,
   `scope="col"` everywhere, `aria-current`, `?` shortcut overlay.
8. **V3-7 — pre-merge audit fixes.** A full senior-review pass over the
   branch found and fixed three real issues: `CandleCache` was unusable
   from worker threads (`check_same_thread` — the disk cache silently
   never worked in the live app, and V3-0's stale fallback would have
   returned empty in production; fixed with a locked shared connection +
   threading regression test), the chart's 30s timer never auto-retried a
   failed *first* load, and Enter could submit an order from behind the
   `?` overlay. Each fix browser- or thread-verified individually.

The audit that scoped all of this is `ROADMAP-V3-UX.md` (committed with
V3-0).

9. **Packaging fix (2026-07-18, this session, uncommitted).** The user
   found the freshly built exe unusable: every chart/quote/chain request
   failed with "No module named 'yfinance'". Root cause: the performance
   pass (`f1bae42`) made the yfinance import lazy via
   `importlib.import_module`, which PyInstaller cannot see, so every exe
   built since then silently shipped without yfinance. Fixed with
   `--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
   CLI command that the build script now runs against the fresh exe
   (build fails on an incomplete bundle); and `tests/test_packaging.py`
   (+4 tests — fails the ordinary suite if any dynamic third-party
   import isn't collected). Exe rebuilt and verified live: candles
   (daily + 5m) and a 231-contract chain served from the packaged app;
   full browser flow sweep of the chart system green. **373 tests.**

## What is currently stable?

Everything on both branches. **373 tests pass** (+6 cached-provider tests and a CandleCache threading regression test
added in V3-0, +4 packaging-guard tests added 2026-07-18). `scripts/verify.ps1` ran clean end-to-end as the closing
action of the session, and every milestone additionally got scenario-level
Playwright verification (chart failure states, the full order-ticket flow —
including the manual-entry risk gate visibly rejecting an after-hours
order, which is correct behavior — settings search, a real 25-day backtest
run, the accessibility overlay).

## What should be worked on next?

1. **The user reviews the `v3-ui` branch** (run `.\scripts\dev.ps1` on the
   branch and click through) and decides on merging to `main`. Nothing
   should be built on top of `v3-ui` until that call is made.
2. **Market-hours chart validation** (couldn't be done — market closed):
   confirm live candles/volume/indicators/price-line update during a
   session, and that the forming candle updates in place (the V3.1-6
   fast path was verified with a simulated tick, not a real one). The
   live-update code path and the intrabar `chSig` fix are in place; this
   is confirmation, not new work.
3. Remaining, explicitly-not-done `ROADMAP-V3-UX.md` items if the user
   wants V3 continued: **H5** notification center with persistence (needs
   a small backend store — check `optionspilot/notify/` first), **N2**
   chart↔option-chain cross-links, **N4** toast stacking.
4. One deliberately-skipped verification: the order-ticket **fill** path
   (fill → stop-loss pre-arm → position row) was verified only up to the
   risk-gate rejection because the market was closed — worth one
   market-hours pass.
4. Then the standing scope decision (unchanged from before V3): V2-5
   replay engine, V2-6 journal/improvement dashboard, the deferred V2-4
   workspace layout, or letting paper-trading data accumulate.

## What files are currently important?

- `optionspilot/ui/static/index.html` — the entire frontend; V3/V3.1
  touched every tab. The chart lifecycle is the most intricate code:
  `loadChart`/`chRenderData` (with the `chTailUpdate` live-update fast
  path), the `chSig` signature (now includes last-bar OHLCV), the
  history-paging merge (`chMergeHistory`/`chLoadHistoryChunk`), and the
  editable-drawing overlay system (`DRAW` model, `chDrawRender`,
  `chPointerDown/Move/Up`, `chDrawAct`) rendered on the `#ch-draw` canvas.
- `optionspilot/data/base.py` — `validate_candles` is now the single
  sanitization choke point (drops NaN/inf/≤0 OHLC, zeroes bad volume,
  logs); do not weaken it.
- `scripts/chart_check.py` — the 19-check chart regression suite; run it
  (via `verify.ps1`) after any chart change.
- `optionspilot/data/cached.py` — `EMPTY_CANDLE_TTL` and
  `get_candles_stale_ok()` are new; the strict `get_candles` contract is
  unchanged and must stay that way (fail-closed trading).
- `scripts/verify.ps1` — still the "is the repo healthy" command.
- `docs/ROADMAP-V3-UX.md` — the audit + scope this sprint implemented;
  the unimplemented remainder lives there.

## What should NOT be modified?

See `AI_CONTEXT.md` "Things future AI assistants should never change
without careful review." New this session: **do not relax
`CachedProvider.get_candles` to serve stale data** — the stale fallback
exists only behind `get_candles_stale_ok()` for display surfaces, and the
engine's empty-means-skip behavior is load-bearing for trading safety.

## Known issues

- **`docs/ARCHITECTURE-MOBILE.md` is still untracked** — the mobile
  architecture proposal from a prior planning session; commit or discard
  when the user decides. Everything else this session is committed.
- `OptionsPilot.exe serve` (the windowed exe running the browser-serve
  subcommand) starts its internals but never binds the port —
  pre-existing, discovered 2026-07-18 while verifying the packaging fix.
  Desktop `ui` mode and dev-repo `python -m optionspilot serve` both
  work. Tracked in `TODO.md`.
- The Trade tab's fill-path UX (post-fill stop-loss pre-arm) has no
  market-hours verification from this session (see above).
- Frontend coverage remains shallow relative to the app's size
  (`browser_check.py` is tab-navigation only) — the session's per-flow
  Playwright scripts live in the session scratchpad, not the repo; making
  them permanent is a natural follow-up (see `TODO.md`).
- No CI / linting — still deliberate, still just recommended
  (`CONTRIBUTING.md`).

## Suggested first prompt for the next AI session

> Read `docs/AI_CONTEXT.md`, `CLAUDE.md`, and this file, then run
> `git log --oneline -10`, `git status`, `git branch --show-current`, and
> `git diff --stat` yourself — note that V3 work lives on the `v3-ui`
> branch, not `main` — then run `.\scripts\verify.ps1` to confirm the
> baseline is green. Then: [either "the v3-ui branch is approved — merge
> it to main," or a specific next task]. If no task is given, ask whether
> the V3 branch has been reviewed before proposing anything built on it.
