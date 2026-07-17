# TODO.md — prioritized work queue

See `PROJECT_STATE.md` for narrative context on why each item is where it
is. This file is the flat, actionable checklist version.

## High Priority

- [x] **V2-4 drawing/overlay remainder** — done 2026-07-16: fib
      retracement / zone rectangle / bar-note drawing tools, and
      position/order lines drawn on the chart (entry/stop/target +
      working-order trigger levels).
- [ ] **V2-4 layout remainder** (only if the user wants it): the full
      three-panel workspace layout (top bar / right sidebar / bottom
      panel) and multi-chart layouts — a large UI restructuring, left as
      an explicit user decision. See `ROADMAP-V2.md`.

## Deferred by user decision

- [ ] **Rebuild and smoke-test the exe** — the packaged app predates all
      2026-07-16 UI/performance work. The user explicitly wants packaging
      LAST, once feature-complete; don't prioritize it.

- [x] **Live-verify the V2-3 frontend in a real browser** — done 2026-07-16
      against a scratch data dir: toggle switch + persistence across reload,
      Coach tab empty state, manual round trip → coach review rendered with
      expandable detail, mode-axis orthogonality, zero console errors.
- [x] **Commit V2-3** — committed 2026-07-16.
- [x] **Rebuild and smoke-test the exe** with V2-3 included — done
      2026-07-16 after the user closed the running app: rebuilt via
      `build_exe.ps1` (app data backed up/restored by the script), then
      smoke-tested the packaged exe in serve mode against a scratch data
      dir: AI→Human toggle, manual SPY round trip, scan → coach review
      rendered in the Coach tab, zero console errors.
- [x] **Update `docs/ROADMAP-V2.md`**: V2-3 checklist flipped to `[x]`.

## Medium Priority

- [x] Fix `pyproject.toml` `package-data`: `"data_assets/*"` added
      2026-07-16.
- [x] Add `Pillow` to the `dev` extra in `pyproject.toml` — done 2026-07-16.
- [x] Inline comment for `engine.operating_mode` in `config.yaml` — done
      2026-07-16.
- [ ] Decide on and implement stock/share (non-option) manual positions —
      deferred from V2-2. Touches `broker/orders.py`, `PaperBroker`, the
      Trade tab chain/ticket UI (currently options-only).
- [x] **Browser smoke check committed** — done 2026-07-17:
      `scripts/browser_check.py` (Playwright driving the system's Edge via
      `channel="msedge"`, no browser download; soft-skips if the optional
      `[browser]` extra isn't installed) launches the app against a scratch
      data dir and visits every tab checking for zero console errors. Runs
      automatically as part of `scripts/verify.ps1`. **Still open**: this
      is tab-navigation-level smoke coverage, not deep per-flow regression
      testing (mode toggle, manual order placement, coach review rendering
      specifically) — extending `browser_check.py` (or adding sibling
      scripts) with those specific flows remains a real opportunity. Gotcha
      worth keeping if you do: lightweight-charts coalesces chart clicks
      faster than ~500ms apart as double-clicks — pace scripted two-point
      drawing-tool clicks ≥700ms apart. **Update 2026-07-17 (V3 session):**
      per-flow Playwright scripts now exist and were used to verify every
      V3 milestone (chart failure states/races, the full order-ticket flow,
      settings search, a real backtest run, the `?` overlay) — but they
      live in the session scratchpad, not the repo. Promoting them into
      `scripts/` as committed regression checks is now the concrete version
      of this item.
- [ ] One market-hours manual pass over the Trade tab's *fill* path (fill →
      stop-loss pre-arm → position row → close-prefill) — the V3 session
      verified everything up to the risk gate's after-hours rejection, but
      no real fill could occur with the market closed.
- [ ] Consider a minimal CI workflow (`.github/workflows/tests.yml`
      running `scripts/verify.ps1` or just the pytest suite on push/PR) and
      `ruff` for linting/formatting — recommended but not installed; see
      `docs/CONTRIBUTING.md` "Automation opportunities" for the reasoning
      on why each is a real decision, not a trivial add.

## Low Priority

- [x] Serve a favicon — done 2026-07-17: `assets/optionspilot.ico` copied
      to `optionspilot/ui/static/favicon.ico` (so it's bundled the same way
      in dev, a wheel, and the exe) and served at `GET /favicon.ico`. Found
      by the new `scripts/browser_check.py`'s first real run.

- [ ] V2-4 groundwork: evaluate bundling `lightweight-charts` (Apache-2.0)
      into `optionspilot/ui/static/` (no CDN — must work fully offline
      inside the PyInstaller bundle).
- [ ] Review whether `CoachProfile` (V2-3) already covers enough of V2-6's
      "improvement dashboard" spec to shrink that phase's scope, or whether
      it's purely additive.

## Future Ideas (unscheduled — see `docs/ROADMAP-V2.md` for the full phase breakdown)

- [ ] **V2-4 — Chart workspace**: TradingView-inspired layout (top bar,
      right sidebar, bottom panel), interactive candlestick chart with
      zoom/pan/crosshair/multi-timeframe/indicator overlays, drawing tools
      (trendline, horizontal line, fib retracement, rectangle, note)
      persisted per symbol, trade-from-chart.
- [ ] **V2-5 — Replay engine**: pick a historical day, hide future candles,
      play/pause/step/speed controls, separate replay paper account, coach
      reviews replay trades identically to live ones.
- [ ] **V2-6 — Journal & improvement dashboard**: chart-context snapshots
      per trade (candle window + entry/exit markers, re-rendered on demand
      — the deliberate substitute for static screenshots), notes/emotions
      capture fields, filtering by strategy/symbol/P&L/date/mistake type.
- [ ] Candle cache for the live loop (incremental fetch + merge) to reduce
      yfinance traffic during long sessions.
- [ ] A real live-broker adapter (Alpaca's options paper API is the natural
      first candidate) — explicitly gated on sustained paper profitability;
      do not build without the user's direct, dedicated request.
- [ ] News / economic-calendar / sentiment inputs as new scorer evidence
      types.
- [ ] Portfolio-level risk (correlated positions, sector exposure limits).
- [ ] A paid market-data feed adapter (Polygon/Tradier) as an alternative to
      the free, ~15-minute-delayed yfinance provider — slots into the
      existing `MarketDataProvider` interface without touching the engine.
