# TODO.md — prioritized work queue

See `PROJECT_STATE.md` for narrative context on why each item is where it
is. This file is the flat, actionable checklist version.

## High Priority

- [ ] **V2-4 remaining scope** (if the user wants it next): three-panel
      workspace layout, fib/rectangle/note drawing tools, position/order
      lines drawn on the chart, multi-chart layouts. Core shipped
      2026-07-16 — see `ROADMAP-V2.md` for the per-item status.

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

- [ ] Fix `pyproject.toml` `package-data`: add `"data_assets/*"` so a
      `pip install` of a wheel/sdist ships the bundled symbol directory
      (currently only works via the repo checkout or the PyInstaller build,
      which explicitly `--add-data`s it).
- [ ] Add `Pillow` to a `dev` (or new `assets`) extra in `pyproject.toml` —
      `scripts/make_icon.py` needs it and it was installed ad hoc.
- [ ] Add an inline comment for `engine.operating_mode` in `config.yaml`,
      matching the documentation style already used for `trading_mode`.
- [ ] Decide on and implement stock/share (non-option) manual positions —
      deferred from V2-2. Touches `broker/orders.py`, `PaperBroker`, the
      Trade tab chain/ticket UI (currently options-only).
- [ ] Consider adding at least a handful of browser-driven UI tests
      (Playwright or similar) for the highest-value flows (mode toggle,
      manual order placement, coach review rendering) — there is currently
      zero regression coverage for `static/index.html` beyond static
      ID-reference checking.

## Low Priority

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
