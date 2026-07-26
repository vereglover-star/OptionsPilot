# OptionsPilot — Module Reference

Quick API map for developers. Details live in each module's docstring.

## Core (`optionspilot/core/`)
| Item | Purpose |
|------|---------|
| `models.Candle/Quote/OptionContract` | Market data vocabulary; OCC symbols, spread %, DTE |
| `models.Evidence/Signal` | Scored reasoning items; `Signal.reasons` renders the trail |
| `models.TradePlan` | entry/spot/stop/target/partials/invalidation/RR |
| `models.Order/Fill/Position/TradeRecord` | Execution + journal records |
| `logging_setup.setup_logging` | Rotating per-subsystem logs (`logs/engine.log`, …) |
| `sqlite.connect` / `sqlite.run_migrations` | Shared SQLite foundation (V0.4.2): `check_same_thread=False` + dir creation + optional WAL; ordered `PRAGMA user_version` migrations (refuses a newer schema). Used by every store (cache/journal/orders/paper/experience) so all databases evolve schema the same versioned way |
| `paths.AppPaths` | Storage single source of truth (V0.4.4). Root at `%LOCALAPPDATA%\OptionsPilot` (XDG/`Application Support` elsewhere; `OPTIONSPILOT_HOME` override). Typed `get_data_dir`/`get_journal_db`/`get_coach_dir`/`get_settings_file`/… + `ensure()`. No module constructs the root itself |
| `migration.initialize_storage` | Startup storage init (V0.4.4): creates the layout and imports a legacy CWD/exe-relative install **once**, losslessly (`copy2` timestamps, per-file verify, skip-if-newer, never deletes source); writes `migrations/migration_version.json`. Also `create_backup()` + an empty `MIGRATIONS` versioned framework. See `docs/STORAGE.md` |

## Config (`optionspilot/config/`)
`load_config(yaml_path, environ)` → `AppConfig`. Layered defaults ← YAML ← env
(`OPTIONSPILOT__SECTION__KEY`). Unknown keys / bad values fail at startup.
Sections: `data`, `indicators` (enable flags + params), `engine` (confidence
threshold, delta/DTE/liquidity filters, evidence weight overrides,
`trading_mode`, `operating_mode`), `risk` (all limits), `broker` (paper
realism + live gate), `notify`, `logging`, `integrations`.

`runtime.py::RuntimeSettings` — the in-app-editable overlay on top of the
yaml config, persisted to `data/settings.json`: `set_watchlist`,
`set_pinned`, `save_favorites`, `set_mode` (trading_mode + custom tunables,
validated through `EngineConfig`/`RiskConfig`), `set_operating_mode`
(ai/human, independent of trading_mode). `apply(cfg)` overlays persisted
choices onto a freshly loaded config at bootstrap; `_apply_mode` always
preserves the caller's current `operating_mode` when restoring the baseline
for a trading-mode switch. `MAX_WATCHLIST = 30`.

## Data (`optionspilot/data/`) — full design in `docs/MARKET_DATA.md`
- `MarketDataProvider` ABC — `get_candles/get_quote/get_expirations/get_option_chain`.
- Canonical candle frame: UTC index `ts`, columns `open high low close volume`
  (enforced by `base.validate_candles`).
- `build_provider(cache_db)` — **the composition root**: assembles the shipped
  chain (Yahoo JSON → yfinance → Stooq) with one cache and one diagnostics
  recorder. The orchestrator calls this; nothing else needs to know which
  providers exist or in what order.
- `capabilities.py` — `ProviderCapabilities`: what each provider can serve, per
  interval, including its real history depth **measured from now**
  (`YAHOO_INTERVALS`). Answers "can this request be served at all?" without
  spending a request. `scripts/marketdata_probe.py` re-measures it live.
- `adapter.py` — `HistoryAdapter`, the one shape every source takes. Supplies
  interval mapping, resampling, normalization, window clamping, throttling and
  health bookkeeping, so a concrete adapter is transport + parser only. Typed
  failures (`ProviderRangeError` / `RateLimited` / `SymbolError` /
  `Unavailable`) drive retry-vs-failover.
- `yahoo_provider.YahooChartAdapter` (priority 10) — Yahoo's `v8/finance/chart`
  JSON over `urllib`; the **primary**, because it reports *why* it refused.
- `yfinance_adapter.YFinanceAdapter` (20) — the same data by an independent
  code path, so the two fail for independent reasons.
- `stooq_provider.StooqAdapter` (30) — the only source not dependent on Yahoo;
  daily/weekly/monthly only, decades deep.
- `legacy.LegacyProviderAdapter` — wraps any plain `MarketDataProvider` (test
  fakes, backtest fixtures) into the same ladder.
- `registry.ProviderRegistry` — ordering, eligibility (interval/symbol/depth
  checks *before* the network), and per-provider circuit breakers with
  half-open recovery.
- `quality.py` — semantic validation returning a `HistoryReport`: OHLC
  consistency, ordering, duplicates, future timestamps, non-finite values, bad
  prints, interval conformance. Gaps are recorded, never penalised.
- `service.MarketDataService` — the tier ladder (memo → disk → providers →
  half-open probes → stale cache → explained failure) and the one place the
  four distinct "no data" conditions (`exhausted`/`empty`/`stale`/`failed`) are
  told apart. Returns a `HistoryResult`.
- `diagnostics.py` — one `RequestTrace` per request in a bounded ring; served by
  `GET /api/diagnostics/marketdata`.
- `CandleCache` (`cache.py`) — durable SQLite history keyed
  (symbol, timeframe, ts), with provider attribution, atomic writes, integrity
  check on open, corruption quarantine + rebuild, and versioned migrations.
- `CachedProvider` (`cached.py`) — the `MarketDataProvider` face: candles go to
  `MarketDataService`; quotes/chains/expirations stay memoized (5s / 30s / 1h)
  over `YFinanceProvider` with in-flight dedup. `get_history()` returns the full
  result for the API; `get_candles()` stays strict (never stale) for the engine.
  `invalidate_quotes()` drops quote/chain memos on demand.
- `symbols.py` — `is_known(symbol)`, `search(query)` (autocomplete), backed by
  the bundled `optionspilot/data_assets/symbols.csv` (12,472 NASDAQ/NYSE tickers).
- `presets.py` — static preset watchlists (`PRESETS: dict[str, list[str]]`).

## Analysis (`optionspilot/analysis/`) — pure functions, no I/O
- `indicators` — SMA/EMA/VWAP/MACD/RSI/StochRSI/ATR/Bollinger/Supertrend/ADX/
  OBV/relative volume. Wilder smoothing is SMA-seeded (matches TA-Lib).
- `candlesticks.detect_all` — 11 patterns as boolean columns, fire on the
  completing bar.
- `structure` — `find_swings` (fractal, `confirmed_ts` = pivot + strength bars),
  `trend_state` (HH+HL / LH+LL), `detect_events` (BOS/CHoCH vs confirmed levels
  only), `is_consolidating`.
- `smart_money` — `find_fvgs`, `find_order_blocks`, `find_equal_levels`,
  `find_liquidity_grabs`, `premium_discount`; `Zone` tracks mitigation.
- `volume` — `volume_spikes`, `pressure` (volume-weighted CLV in [-1,1]),
  `detect_divergence` (price vs OBV).
- `options_metrics` — `bs_greeks` (theta/day, vega/vol-point), `implied_vol`
  (bisection, None = untrustworthy quote), `liquidity_score` (0-100),
  `expected_move`, `enrich_greeks`.

## Engine (`optionspilot/engine/`)
- `MultiTimeframeAnalyzer.analyze({tf: candles}, key=symbol)` →
  `{tf: TimeframeView}` (skips timeframes with < 40 bars; respects
  indicator enable flags). Views are memoized per (key, timeframe) on a
  data fingerprint — an unchanged frame returns the cached view, so
  repeat scans only recompute timeframes whose bars actually changed.
- `ConfluenceScorer.score(views)` → `ScoreResult(direction, confidence, net,
  evidence)`. 15 evidence types, LONG-perspective scores in [-1,1], weighted
  mean → confidence = |mean|·100, damped 25% in consolidation.
- `ContractSelector.select(direction, chain, spot, today)` → `SelectionResult`
  with per-reason rejection counts.
- `TradePlanner.plan(signal, entry_view, contract, spot)` → `TradePlan | None`.
- `TradeGate.assess(score_result)` → `GateReport(mode, setup_quality,
  min_confidence_required, accepted, reason, confirmations_passed/failed)`.
  Conservative mode: fixed `min_confidence` bar. High-risk mode: threshold by
  setup quality (excellent base−18 / good base−10 / average base−3, floored at
  `high_risk_floor`; poor never trades). `stretch_rr_ok()` additionally
  requires `high_risk_min_rr_stretch` RR for entries below the base bar
  (enforced in `build_plan`).
- `DecisionEngine` — facade: `evaluate()` (always returns the signal +
  GateReport, flags `tradeable` per the gate), `build_plan()`.

## Risk (`optionspilot/risk/`)
`RiskManager` — the only path to the broker.
- `approve(plan, open_positions, now)` → `RiskDecision(approved, quantity, veto,
  notes)`. Gate order: halt → weekday/hours (ET) → daily trade limit → max open
  positions → min RR → loss cooldown → sizing.
- Sizing: `equity · risk% / min(premium·100, |delta|·stop_distance·100·1.25)`,
  capped at `max_contracts`.
- `approve_manual_entry(quantity, premium, open_positions, now, *,
  is_new_position, existing_quantity)` — Human Mode entries share the same
  hard gates (halt → weekday/hours → daily trade limit → max open positions,
  skipped when scaling into a held contract → loss cooldown → max contracts
  counting the existing position). The %-risk sizing is advisory only here
  (computed into `notes`, never a veto) — oversizing a user-directed trade
  is the coach's `oversized` tag to flag, not a hard block. Wired from
  `UIServer.place_order` (immediate market buys, 422 on veto) and
  `OrderManager.evaluate`'s `approve_entry` callback (delayed fills —
  vetoed orders cancel with the veto text as the result).
- Feeds: `record_entry(ts)`, `record_closed_trade(ts, pnl)`, `update_equity(eq, ts)`.
- Circuit breaker: daily loss & loss streak → halted until next ET day; weekly
  loss → next ET Monday; max drawdown → `reset_halt()` (human) only.

## Broker (`optionspilot/broker/`)
- `Broker` ABC — `open_position(plan, qty, ts)`, `close_position(symbol, qty,
  bid, ts, reason)`, `mark_positions`, `get_positions`, `get_account`.
- `PaperBroker` — fills at ask+slippage / bid−slippage, commission per contract,
  one position per contract symbol (adds average in), SQLite persistence
  (account, positions incl. management fields, full fill log). Raises
  `BrokerError` on impossible orders — fail closed.
- `PositionManager.review(position, spot, ts, opposing_choch)` → `[ExitIntent]`.
  Priority: stop → target → invalidation → partial (half off, stop → breakeven).
  Mutates position management fields; caller persists via
  `broker.update_position_management`. **Only touches `managed_by == "ai"`
  positions** — manual positions belong to the OrderManager.
- `OrderManager` (`broker/orders.py`) — working orders for manual trading:
  MARKET (immediate), LIMIT (option premium), STOP_LOSS / TAKE_PROFIT /
  TRAILING_STOP (underlying levels, put-aware mirroring), DAY (expires 16:00
  ET) / GTC. `place()` validates (position/reservation checks, required
  params), `evaluate(now, get_spot, get_option_quote)` runs once per scan
  cycle and returns fill/expiry/cancel events; sell orders auto-cancel when
  the position closes first. Persisted to `data/orders.db` (restart-safe;
  fills after restart use live quotes, never stored ones).
- `PaperBroker.open_manual(contract, qty, ts, entry_spot)` — plan-less entry
  for Human Mode; `record_equity_snapshot` / `equity_history` persist equity
  for lifetime max-drawdown and return metrics.
- `registry.create_broker(config, db_path, cash)` — the only place brokers are
  constructed. `paper` is real; `alpaca`/`tradier`/`webull`/`ibkr` are
  extension slots that raise `BrokerError` with adapter guidance. The live
  gate (two config flags) is re-checked here, defense in depth.

## Coach (`optionspilot/coach/`) — new in V2-3
- `TradeCoach.review(trade, entry_context, exit_context, orders, ...)` →
  `CoachReview(score, verdict, setup_quality, before, during, after,
  mistakes, strengths, improvements, pro_notes, ev_note)`. Score is
  **process-based, not outcome-based** — see module docstring. `MISTAKES`
  dict: 14 tags, each `(label, pro_comparison_note, exercise)`. Persists to
  `data/coach/<trade_id>.json`; `load(trade_id)` / `load_all()` reload.
- `CoachProfile(reviews).build()` → aggregated recurring mistakes (ranked by
  frequency), top strengths, `score_trend` (late-half avg − early-half avg),
  win rate by setup quality, top-3 `recommended_exercises`. Rebuilt fresh
  from all persisted reviews every call.
- **AI Coach 2.0 (V0.4.3):** `score_categories(findings, mistakes, verdict,
  had_context)` (`categories.py`) → 10 `CategoryScore`s (score/grade/explanation/
  suggestion) from the review's own findings+mistakes; `CoachReview` now carries
  `categories` + an outcome snapshot (pnl/return_pct/hold_minutes/r_multiple/
  entry_ts). `build_dashboard(reviews)` (`analytics.py`) → mentor dashboard:
  sub-scores (consistency/risk/execution/discipline), per-category avg+monthly
  trend, streaks, pattern detection with confidence ("developing" vs "recurring
  habit"), improvement timeline, and ≤5 recent-window action items. Both are pure
  functions; served on `GET /api/coach → dashboard` (cached by review count).
- Only reviews `strategy="manual"` trades — AI trades are tuned by
  `learning/` instead; the two feedback loops are deliberately separate.

## Journal / Learning / Backtest (`journal/`, `learning/`, `backtest/`)
- `TradeJournal` — SQLite record of every round trip (reasons, evidence names,
  conditions, annotations); `build_trade_record` aggregates partial exits.
- `LearningEngine` — slices by evidence/hour/DTE/confidence/direction/exit;
  `recommend_weights` (min-sample gated, ±20%/cycle, bounded 0.25×–2× default);
  `WeightStore` — versioned weights + rationale at `data/learning/weights.json`.
- `Backtester.run(symbol, candles_by_tf)` — replays through the live engine/
  risk/broker stack; `_slice_closed` guarantees no lookahead; synthetic
  BS-priced chains; `BacktestReport` → JSON + HTML with all metrics.

## Experience Engine (`experience/`, V0.4.0–0.4.1)
The AI's long-term trading memory — recorded ALONGSIDE the journal, never
instead of it. Deterministic, auditable, best-effort, advisory, paper-only. See
`docs/ROADMAP-V0.4-EXPERIENCE.md` for the full design.
- `ExperienceRecord` (`models.py`) — a rich, nullable, expandable superset of
  `TradeRecord` (outcome, full decision snapshot — ATR/EMA/MACD/VWAP/stop/target/
  modes/regime, reasoning, exploration flag, `extra` JSON blob incl. the verbose
  evidence breakdown, similarity `features`). `SimilarityResult` (+ grounded
  `common_successes`/`common_failures`) and `SimilarTrade` (viewer row).
- `build_snapshot` (`snapshot.py`) — THE centralized capture of a decision
  context (duck-typed `EngineDecision`; no runtime `engine/` dependency). Both
  AI entry and the manual/coach path use it → feature symmetry.
- `build_experience` / `build_query_record` / `build_feature_vector` /
  `market_regime` (`features.py`) — pure extraction; the shared `_entry_fields`
  backs a closed trade and a live setup; fixed-range normalization so a record's
  vector is stable forever.
- `ExperienceStore` (`store.py`) — SQLite (`data/experience.db`) built for 100k+:
  indexed query columns + full-fidelity JSON payload, `PRAGMA user_version`
  migrations (v2 adds `market_regime`/`return_pct`/`hold_minutes` with backfill;
  refuses a newer-than-supported schema); SQL-only aggregates (`overview`,
  `aggregate`, `exit_reason_counts`) that never deserialize payloads.
- `SimilarityEngine` (`similarity.py`) — weighted-distance similarity (direction
  anchor 3.0 + evidence Jaccard 3.0 + setup/HTF/tf/session + numerics 2.0);
  `find_similar` / `summarize` (win rate, return, failure mode, grounded
  success/failure patterns, ADVISORY calibrated confidence — never fed to the
  gate).
- `ExperienceEngine` (`engine.py`) — the façade the orchestrator + API drive
  (no SQL leaks past it): `record_trade` (best-effort, never raises),
  `explain_setup` / `summarize_for`, `similar_trades` / `similar_to_snapshot`,
  `recent`, `statistics` / `strategy_statistics` / `regime_statistics` /
  `failure_modes` / `success_patterns`.

## Orchestrator (`orchestrator.py`) & Notify (`notify/`)
- `Orchestrator.fetch_watchlist_candles(symbols, on_symbol=None)` — parallel
  (symbol × timeframe) candle fetch (8 workers), provider-only (safe to call
  WITHOUT the UI lock); fires `on_symbol` per completed symbol for
  progressive display. `run_cycle(now, candles=...)` accepts the result.
- `Orchestrator.run_cycle()` — fetch → manage AI positions → evaluate manual
  orders (`_evaluate_orders`) → reconcile manual round trips + coach
  (`_reconcile_manual`) → mark/risk → halt surfacing → scan entries
  (gated by `operating_mode`: AI trades, Human gets an advice notification
  only) → large-move alerts. `run_forever()` is the market-hours loop.
- AI-trade journal context persists at `data/state/open_trades.json`;
  manual-trade context at `data/state/manual_trades.json`
  (`register_manual_entry` tracks fast round trips opened via the API).
  Risk state is rebuilt from the journal at startup. Exits are never
  risk-gated; entries always are.
- `NotificationCenter.notify(kind, title, body)` — never raises; desktop
  toasts + SMTP email (password via `OPTIONSPILOT_SMTP_PASSWORD`).

## Integrations (`integrations/`)
- `parse_alert(payload, secret)` — validates TradingView webhook JSON
  (constant-time secret compare, symbol normalization, note truncation).
- `Orchestrator.scan_single(symbol)` — what an alert triggers: the full
  engine + risk pipeline for one symbol. Alerts change *when* the system
  looks, never *whether* it trades.
- Config gate: `integrations.tradingview_webhook` + 16-char minimum secret.

## Update (`update/`, V0.5.0) — the self-updater
- Self-contained; depends only on `core` + stdlib (`urllib` — no new runtime
  dep). Every layer takes an injected transport/collaborator → fully offline
  tests. Full guide: `docs/AUTO_UPDATER.md`.
- `version.Version` — SemVer parse (`parse`/`try_parse`, leading `v` ok) +
  correct non-lexical ordering (`0.4.10 > 0.4.9`; prerelease < release).
- `transport.urllib_open` / `with_retries` — the ONLY networking: 10s
  timeouts, bounded exponential backoff on transient failures, proxy env
  support; raises `NetworkError` (retryable flag), never bare urllib errors.
- `github_api.GitHubReleases` — `list_releases()` (drops drafts),
  `latest_release(include_prereleases)` (max by parsed Version).
  `INSTALLER_RE` selects only `OptionsPilot-Setup-vX.Y.Z.exe` assets.
- `checker.UpdateChecker.check(channel)` — never raises; returns
  `UpdateCheckResult`. `is_check_due(frequency, last_checked)` throttles
  (launch/daily/weekly).
- `downloader.Downloader.download(asset, dest_dir, progress_cb, cancel)` —
  streams to `%TEMP%\OptionsPilotUpdater`, `.part` → atomic rename, progress
  snapshots (bytes/speed/ETA), `threading.Event` cancellation.
- `validation.validate(path, expected_size, expected_sha256)` — the gate
  before execution (exists/name/size[/hash]); Authenticode slots in here.
- `installer.InstallerLauncher` — `create_pre_update_backup()` (mandatory;
  `create_backup(paths, "pre-update")`), `launch(path)` with
  `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL [/RESTARTAPPLICATIONS]`,
  `relaunch_app()` fallback.
- `service.UpdateService` — facade + thread-safe `UpdatePhase` state machine;
  `maybe_check_on_launch()` (background, never blocks), `check_now()`,
  `start_download()`/`cancel_download()`, `apply_update()` (validate → backup
  → launch → install hook), `snapshot()` (the UI payload),
  `set_preferences()`. Prefs live in `RuntimeSettings.update_prefs()`.
- `ui.py` — pure formatting: `format_bytes/speed/eta`, safe
  `render_release_notes_html` (escape-first markdown subset), dialog payloads.

## UI (`ui/`) & CLI (`__main__.py`)
- `create_app(config, orchestrator, run_loop, runtime)` — FastAPI app.
  `/api/scan` is non-blocking by default (background cycle; progress in the
  status payload's `scan` field); POST `{"wait": true}` for the synchronous
  form. `/ws` pushes at 1s with change detection (full payload on change,
  heartbeat otherwise). Journal-derived views cache on
  `TradeJournal.revision`.
  `/api/candles?symbol&tf` returns OHLCV + indicator series (EMA/VWAP/
  Bollinger/RSI/MACD via `analysis/indicators.py`) for the Charts tab —
  provider-only, no lock. `/static/lightweight-charts.js` serves the
  vendored chart library (Apache-2.0, offline).
  Endpoints: `/api/status` (full dashboard payload), `/api/scan`,
  `/api/journal`, `/api/learning`, `/api/config`, `/api/chain` (option
  chain + greeks for the order ticket), `/api/orders` (GET list / POST
  place), `/api/orders/cancel`, `/api/account/metrics`, `/api/watchlist*`
  (add/remove/reorder/pin/favorites/presets), `/api/symbols/search`,
  `/api/mode` (trading_mode switch), `/api/operating_mode` (ai/human
  switch), `/api/coach` (reviews + profile), `/api/risk/reset_halt`,
  `/api/backtest` (job slot, GET/POST),
  `/api/update/{status,check,download,progress,cancel,apply,skip,settings}`
  (the self-updater, V0.5.0 — see `update/` above), `/ws` (2s status push),
  `/webhook/tradingview`. All orchestrator access serialized through
  `UIServer.lock`.
- `ui/static/index.html` — self-contained dark dashboard (no build step;
  the one vendored asset is `lightweight-charts.js`). Tabs: Dashboard,
  Charts (interactive candles/volume, EMA/VWAP/BB overlays, RSI/MACD
  subpanes, drawings persisted in localStorage, fullscreen), Trade (manual
  paper trading), Coach, Watchlist, Journal, Backtest, Learning, Settings.
  Keyboard 1–9 switches tabs; F toggles chart fullscreen. Header has both
  mode controls: the AI/Human segmented toggle and the trading-mode
  segmented toggle.
- `ui/desktop.py` — uvicorn thread + pywebview native window; single-
  instance guard (localhost port mutex); `--windowed` PyInstaller build has
  no console (see `core/logging_setup.py`'s `sys.stderr is None` check).
- CLI: `run | ui | serve | scan | status | journal | backtest | learn`.
  `_bootstrap()` returns `(config, runtime)` — every command applies
  `RuntimeSettings` before running.
- Packaging: `scripts/build_exe.ps1` → `dist/OptionsPilot/OptionsPilot.exe`
  (args pass through to the CLI; no args opens the desktop app). Backs up/
  restores `data/` across rebuilds; refuses to build over a running
  instance; bundles `data_assets/` and the app icon (`assets/optionspilot.ico`,
  generated by `scripts/make_icon.py`).
- `scripts/soak.py --cycles N` — stability soak: repeated live cycles on a
  scratch data dir, tracking exceptions, heap growth, and cycle times.
