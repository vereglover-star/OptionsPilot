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
(ai/human, independent of trading_mode), `guide_state`/`set_guide_state`
(V0.6.1 — the guided-onboarding document, stored under the `guide` key).
`apply(cfg)` overlays persisted choices onto a freshly loaded config at
bootstrap; `_apply_mode` always preserves the caller's current `operating_mode`
when restoring the baseline for a trading-mode switch. `MAX_WATCHLIST = 30`.
The guide accessors are deliberately dumb storage: the vocabulary and the merge
semantics belong to `ui/guide.py`, and `config/` must not import upward to reach
them.

## Data (`optionspilot/data/`) — full design in `docs/MARKET_DATA.md`
- `MarketDataProvider` ABC — `get_candles/get_quote/get_expirations/get_option_chain`.
- Canonical candle frame: UTC index `ts`, columns `open high low close volume`
  (enforced by `base.validate_candles`).
- `build_provider(cache_db, config=)` — **the composition root**: assembles the
  shipped chain (Yahoo JSON → yfinance → Stooq) with one cache and one
  diagnostics recorder. The orchestrator calls this; nothing else needs to know
  which providers exist or in what order. `config` is the translated
  `market_data:` section (see `config.py` below).
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
- `http_adapter.KeyedHTTPAdapter` (V0.5.4) — the shared base for keyed JSON
  providers: transport, HTTP-status → typed-failure mapping, JSON decoding, and
  `localize()`, which owns **the timezone contract** (intraday converts from the
  exchange-local time the payload names; daily and coarser stamp at 00:00 UTC
  to match Yahoo and Stooq, because the cache is keyed on the timestamp). A
  concrete keyed adapter implements only `_build_url` / `_translate` / `_parse`
  / `_probe`.
- `finnhub_provider.FinnhubAdapter` (40) — 60 req/min, unix-second timestamps
  (so no timezone conversion). **Its free tier no longer serves historical
  candles** — `/stock/candle` moved to the paid tiers and answers a valid free
  key with HTTP 403 (measured 2026-07-27). It therefore sets
  `free_tier_serves_history = False`, raises `ProviderEntitlementError` rather
  than `ProviderAuthError` on a 403, and implements `verify_credentials()`
  against the free `/quote` endpoint so the app can say *"your key is valid, the
  plan is not"* instead of sending the user to regenerate a good key. A paid key
  works normally. Full account: `docs/MARKET_DATA.md` §41.
- `twelvedata_provider.TwelveDataAdapter` (50) — 800/day, 8/min. Native 2h/4h
  intervals; reports errors inside HTTP 200 bodies; newest-first rows.
- `alphavantage_provider.AlphaVantageAdapter` (60) — **25 requests/day**, the
  tightest budget in the app and the reason `ratelimit.py` exists. Errors arrive
  under four different 200-status keys; the series key is dynamic.
- `ratelimit.py` (V0.5.4) — `RateLimitPolicy` / `QuotaTracker` / `QuotaStore`:
  per-provider request budgets enforced BEFORE the network, a real sliding
  minute window, and a daily count persisted to `<data>/quota.json` so a restart
  cannot mint an allowance the plan never granted. `pressure()` feeds the health
  ranking, which is how load moves off a nearly-spent provider without a
  separate scheduler.
- `health.ProviderHealthMonitor` — **the single owner of a provider's
  operational state**: counters (requests/successes/failures/empties/today),
  latency (EWMA + p95 over a rolling window), the per-kind failure breakdown,
  the rate-limit window, the circuit breaker, the rolling quality score, and
  `rank()`. `COUNTS_AGAINST_HEALTH` is the one definition of which failures say
  anything about a provider's health (range and symbol errors do not).
  `snapshot()` is what the dashboard, the export and the benchmark all read.
  **V0.5.7** adds `health_state()` — the one-word answer for a *human*
  (`healthy` / `degraded` / `offline` / `disabled` / `missing_key` /
  `rate_limited` / `circuit_open` / `unavailable` / `unknown`) paired with a
  mandatory plain-English sentence. It is DERIVED on every read and stored
  nowhere: `status()` remains the *gate* the registry reads, and a second
  stored copy of one fact is precisely how the adapter's counters and the
  registry's breaker came to disagree before V0.5.3. `rank(include_latency=)`
  supports hybrid ordering.
- `credentials.CredentialStore` (V0.5.7) — API keys pasted into Settings, in
  their own owner-only `<data>/credentials.json` rather than in
  `settings.json` (which is treated as ordinary, backed-up, shareable user
  data). Resolution is `environment → stored → config.yaml → missing`,
  implemented by `overlay()` filling in the field `ProviderConfig.
  resolve_api_key` already consults *after* the environment — so there is one
  implementation of the precedence, not two. **A plaintext key leaves this
  module only through `resolve()`**; `mask()` (`••••••••abcd`) is what every
  other accessor returns, and `describe()` deliberately has no `redact=False`
  escape hatch.
- `control.MarketDataControl` (V0.5.7) — the administration surface, composed
  *over* the registry and the service. `dashboard()` (one payload: health +
  credentials + quota + capability + order + failover + advice),
  `set_api_key` / `remove_api_key`, `set_enabled`, `move` / `set_order` /
  `reset_order`, `set_ordering_mode`, `test_connection()` (a real request end
  to end, with a closed vocabulary of outcomes each carrying a remedy),
  `start_maintenance()` / `cancel_maintenance()` (eight actions on one
  background job slot with polled progress), `recommendations()`, and the gated
  `qa_*` hooks. It never computes a ranking (it reports `registry.ranking()`
  verbatim) and never returns a plaintext key. `apply_control_state()` folds
  persisted choices into the startup config, type-checking every field — a
  hand-edited preferences file must cost a user their preferences, never their
  app.
- `faults.py` (V0.5.7) — QA-mode fault injection: `outage`, `timeout`,
  `rate_limit`, `quota`, `auth`, `latency` (a real sleep), `empty`, `unusable`.
  Consulted once inside `HistoryAdapter.fetch_history`, raising the genuine
  `ProviderError` subclass, so a simulated failure is handled by exactly the
  machinery a real one would be. Off in every shipped build
  (`market_data.qa_mode` defaults False; the endpoints 404 without it) and one
  boolean read per request when idle.
- `config.py` — `MarketDataConfig` / `ProviderConfig` / `CacheConfig`: every
  operational knob (enabled, priority, timeout, retries, backoff, throttle,
  breaker thresholds, quality floor, ranking, memo cap, cache retention).
  Plain dataclasses because `data/` may not import `config/`; the pydantic
  mirror is `config.settings.MarketDataConfigSection` and the translation
  happens in `orchestrator.py`. Unknown keys raise; unknown providers do not.
  **V0.5.7** adds `ordering_mode` (`static` / `hybrid` / `dynamic`, resolved by
  `ordering()` — `dynamic_ranking: false` still wins and pins to `static`),
  `provider_order`, `credentials_path`, `control_state_path` and `qa_mode`.
- `registry.ProviderRegistry` — ordering, eligibility (interval/symbol/depth
  checks *before* the network), and per-provider circuit breakers with
  half-open recovery. Ordering is by `monitor.rank()` (health-aware; a cold
  system reproduces the static priority order exactly), and `ranking()` /
  `healthiest()` expose it. `dynamic_ranking: false` pins the static order.
  **V0.5.7** adds the live-control mechanism the settings page drives:
  `order()` / `reorder()` / `set_enabled()` / `apply_config()`. `reorder()`
  rewrites priorities **10, 20, 30…** rather than 1, 2, 3, because 10 rank
  points equals one second of latency — consecutive numbers would make dynamic
  ordering almost-static the first time a user pressed Move Up. It also
  collapses repeated names, so a hand-edited order cannot list one provider
  twice. `default_registry` now **constructs disabled providers** and benches
  them, so a switched-off provider can still be listed, explained and switched
  back on; it contributes no `deepest_earliest` floor.
- `quality.py` — semantic validation returning a `HistoryReport`: OHLC
  consistency, ordering, duplicates, future timestamps, non-finite values, bad
  prints, interval conformance. Gaps are recorded, never penalised. Interval
  conformance is judged on the **median within-session gap**, not the tightest
  one: Yahoo closes every US session with a 30-minute stub bar, so one
  0.5-interval gap in ~1,900 used to condemn a whole 1h frame as "wrong
  interval served" (V0.5.5 — `docs/MARKET_DATA.md` §28). `min_gap_intervals` is
  still reported; it is a statistic, not a veto.
- `service.MarketDataService` — the tier ladder (memo → disk → providers →
  half-open probes → stale cache → explained failure) and the one place the
  four distinct "no data" conditions (`exhausted`/`empty`/`stale`/`failed`) are
  told apart. Returns a `HistoryResult`.
- `diagnostics.py` — one `RequestTrace` per request in a bounded ring; served by
  `GET /api/diagnostics/marketdata`. `trace.log_line()` emits the one structured
  `key=value` line per request (including `chain=` — every provider tried and
  its verdict) that `logs/data.log` carries.
- `report.py` — renders a health payload as the plain-text diagnostics report a
  user pastes into a bug report. **It renders, it never computes**: every number
  comes from the payload, so the text, the dashboard and the JSON export cannot
  disagree. Carries no stack traces, paths or credentials by construction.
- `replay.py` — `replay(service, trace)` re-runs a recorded request;
  `compare_providers(registry, request)` asks **every** provider directly
  (bypassing memo, cache, failover and breaker) and reports bars, latency,
  quality and cross-provider disagreement. No separate recorder exists — the
  diagnostics trace already holds everything replay needs.
- `discovery.py` — measures a provider's real per-interval depth (ladder walk +
  binary search), persists it via `CapabilityStore`, refreshes on a cadence, and
  reports `drift()` against the shipped table. **Advisory and off by default**:
  it does not rewrite `capabilities.py`. `scripts/marketdata_probe.py` calls
  into it, so app and script measure depth identically.
- `CandleCache` (`cache.py`) — durable SQLite history keyed
  (symbol, timeframe, ts), with provider attribution, atomic writes, integrity
  check on open, corruption quarantine + rebuild, and versioned migrations.
  `CacheMetrics` tracks hits/misses/hit rate/stale reads/evictions/average age
  and `provider_requests_saved`; optional `retention_days` pruning (off by
  default) bounds the file.
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

## Trading Intelligence (`intelligence/`, V0.6.0) — full design in `docs/TRADING_INTELLIGENCE.md`

Imports `core` only. Reads journal/experience/coach records structurally, never
by import, so it sits **below** the coach in the layering.

| Module | Exports | Responsibility |
|---|---|---|
| `models.py` | `Evidence`, `Metric`, `BehaviorFinding`, `Pattern`, `ScoreCard`, `Goal`, `GoalProgress`, `Recommendation`, `LessonRecommendation`, `TimelineEntry`, `Achievement`, `Report`, `IntelligenceSnapshot`, `Confidence`, `Severity`, `Trend` | The shared vocabulary. Every type serialises via `to_dict()`; `_finite()` keeps `inf`/`NaN` out of every payload. |
| `stats.py` | `expectancy`, `profit_factor`, `max_drawdown`, `recovery_factor`, `sharpe_like`, `consistency`, `wilson_interval`, `two_proportion_p`, `trend_of`, `sample_confidence`, `comparable` | Every formula in the layer. The only module allowed to contain arithmetic. Nothing is annualised. |
| `facts.py` | `TradeFact`, `FactSet`, `build_facts` | The one join across the three stores. Never invents, never raises, tri-state process fields. |
| `windows.py` | `period_key`, `bucket`, `WINDOWS`, `resolve`, `previous_and_latest` | Period bucketing and named analysis windows. All calendar decisions in America/New_York. |
| `performance.py` | `METRIC_SPECS`, `compute`, `PerformanceEngine` | The 38-metric registry — the addressable vocabulary of the whole layer. |
| `behavior.py` | `BEHAVIORS`, `DETECTORS`, `BehaviorEngine` | 22 detectors, each with its corrective action and its "what this requires" declaration. |
| `patterns.py` | `DIMENSIONS`, `PatternEngine` | Automatic edge discovery over 19 dimensions, with Benjamini–Hochberg false-discovery control. |
| `risk.py` | `RiskIntelligence` | Backward-looking risk: drawdown, tails, worst days, sizing dispersion, concentration. Never gates. |
| `confidence.py` | `ConfidenceEngine`, `ScoreInput`, `MIN_COVERAGE` | The eight composite scores, each explaining itself through its components. |
| `goals.py` | `TEMPLATES`, `validate`, `GoalEngine` | Measurable commitments against metric keys, with computed progress. |
| `curriculum.py` | `CURRICULUM`, `CurriculumEngine` | 16 lessons, each summoned only by a measured weakness. |
| `recommend.py` | `RecommendationEngine` | Derives, prices and ranks the action list. Contains no generic advice. |
| `timeline.py` | `TimelineEngine` | The dated improvement narrative — month-over-month, streaks, milestones. |
| `achievements.py` | `SPECS`, `AchievementEngine` | 10 achievements, none earnable by one trade or by luck. Derived on read, stored nowhere. |
| `reports.py` | `ReportEngine` | Weekly and monthly coaching reports, in prose. |
| `engine.py` | `TradingIntelligence`, `empty_snapshot`, `build_evidence_index`, `confidence_of` | The façade: pipeline, fingerprint cache, per-trade projection, goal CRUD. |
| `store.py` | `IntelligenceStore` | Goal persistence. The only thing this layer stores. |

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

## Guided onboarding (`ui/guide.py`, V0.6.1) — full design in `docs/ONBOARDING.md`
Pure and deterministic (no I/O, no clock, no network). Owns three things the
frontend cannot: durable progress, shape-validation of a user-editable
preferences document, and which walkthrough to offer next.
- `TUTORIALS` — the tutorial **ids** (11). The titles and steps live in
  `index.html`; duplicating them here would be a second place tracking one fact.
  `tests/test_guide.py::TestCatalogueContract` asserts the two id sets match in
  both directions.
- `KNOWN_FEATURES` — the feature keys the recommender reads. Others may be
  recorded and are stored uninterpreted, so new instrumentation needs no backend
  change.
- `normalize_state` / `merge_state` — never raise. Unknown tutorial ids are
  dropped, feature keys are shape-checked and capped (`MAX_FEATURE_KEYS`), and
  merge semantics differ per field on purpose: completions **union** (a short
  client list must not un-finish anything), features **increment**, settings
  **replace**. A hand-edited `settings.json` costs a user their guide progress at
  worst, never their app.
- `GuideFacts` — measured by the caller from state that already exists (journal,
  order book, broker, watchlist). `single_data_source` is `bool | None` because
  "could not determine" and "no" are different answers and only `True` may fire a
  rule.
- `recommendations(state, facts)` → ranked `Recommendation`s, each carrying the
  evidence that produced it. **Recommends tutorials from feature usage; never
  trading behaviour** — that is `intelligence/`'s job, and a second, cruder path
  to the same kind of claim is exactly the drift this codebase has paid for
  twice.

## Host platform (`host/`, V0.7.0) — full design in `docs/ARCHITECTURE-PLATFORM.md` §3
Core-only. Everything OptionsPilot needs from the machine underneath it.
- `capabilities.py` is **data**: `Capability` (13 values), `HostProfile` per
  target — `desktop`, `headless`, `web`, `ios`, `android` — and `HOST_PROFILES`.
  The three that do not exist carry `implemented=False`; every notable missing
  capability carries a `notes` entry saying *why*, so a blocker travels with its
  reason instead of living in a commit message. The load-bearing entry: neither
  mobile target has `BIND_LISTENER`, which is where the desktop-as-host model and
  the single-writer paper account both come from.
- `adapter.py` is **behaviour**: `HostAdapter` (abstract), `DesktopHost`,
  `HeadlessHost`, and process-wide `current_host()` / `set_host()`. Owns the
  storage root, temp space, opening an external URL, and the single-instance
  socket mutex moved here from `ui/desktop.py` (same socket, same port 8786).
  No host call raises — every one sits where an OS refusal is a normal state.

## Services (`services/`, V0.7.0) — full design in `docs/ARCHITECTURE-PLATFORM.md` §2
The platform-independent application layer: between the orchestrator and any
transport. Every service takes **injected, duck-typed** collaborators and returns
**frozen view models of primitives**. Never imports `ui/` or any web framework.
- `errors.py` (V0.9.2-C1) — the classified failure vocabulary: `ServiceError`
  plus **exactly one subclass per member of `contracts.ERROR_CODES`**
  (`ValidationError`, `AuthenticationRequired`, `Forbidden`, `NotFound`,
  `Conflict`, `RateLimited`, `UnavailableProvider`, `InternalError`), rooted at
  `core.errors.OptionsPilotError`. Finding H-7: the transport used to infer a
  status from the *builtin* type, so `except KeyError` turned an internal
  dict-lookup bug into a **404 not found** shown to the user, and a pandas
  `ValueError` into "your request was invalid". A subclass raised deliberately
  is a claim; a leaked builtin is a defect and belongs in the log with a 500.
  **Carries no HTTP status** — `NotFound` becoming 404 is the transport's
  decision (C8) — and `__init_subclass__` refuses a code outside `ERROR_CODES`
  at import time, with `tests/test_service_errors.py` asserting the reverse
  direction so neither catalogue can drift.
- `viewmodels.py` — `PositionView`, `AccountView`, `PerformanceView`,
  `PnLWindowsView`, `WatchlistView`, `WatchlistEditView`, `WorkspaceView`,
  `HostView`. Frozen, because a view model two renderers can both mutate is the
  same "two objects tracking one fact" drift paid for in V0.5.3, V0.5.7 and
  V0.6.1. `finite()` is the `Infinity`/`NaN` boundary.
- `portfolio.py` — positions, account, `performance()` (win rate, profit factor,
  max drawdown), `pnl_windows()`, `setup_history()`. Reproduces exactly which
  reads happen under the orchestrator lock and which do not.
- `watchlist.py` — parse, validate, add/remove/reorder. The four disjoint outcome
  buckets (`added` / `invalid` / `duplicates` / `over_cap`) exist because a user
  who pastes twelve tickers and gets eight must be able to see which four went
  missing and why.
- `charts.py` — `ChartService.candles_payload()`, the Charts tab's OHLCV +
  indicator series (V0.9.2-C2, moved verbatim out of `ui/server.py`). Takes no
  lock, which is a domain property rather than an omission: it reads a provider
  and never orchestrator state, so a chart load cannot contend with a running
  scan. Its collaborators are a provider *source* (a callable, so a provider
  swapped after construction is the one used), the indicator settings, a
  `market_open` predicate, the host's clock and `orchestrator.WINDOW_DAYS` —
  handed down rather than re-declared, so the default history window keeps one
  owner. The one service that returns a `dict` instead of a view model, because
  the payload spreads the market-data layer's own `as_meta()` keys.
- `marketdata.py` — `MarketDataAdminService`: the diagnostics payload, its text
  rendering, trace replay, and the twelve Settings ▸ Market data control calls
  (V0.9.2-C3). **Not** `MarketDataService` — `data/service.py` has owned that
  name since V0.5.2, and two identically-named classes is a permanent cost to
  every import line. Takes no lock, deliberately: a running scan must not be
  able to block the settings page. The renderer (`data/report.py`, pure stdlib)
  is *imported* so a second one cannot be substituted; the replay engine
  (`data/replay.py`, which reaches the registry and the adapters) is *injected*.
  The twelve delegations go through one `_delegate(name, *args)` dispatcher, so
  each control method name appears exactly once.
- `trading.py` — `TradingService`: the manual order path, `chain_payload`,
  `account_metrics`, and the scan cycle with its `scan_state` / `last_summary` /
  `equity_history` (V0.9.2-C4). **Two locks, two jobs**: the orchestrator
  `RLock` is *injected* (shared with the server — a second one would serialise
  against nothing), and `cycle_lock` is a plain `Lock` this service owns, which
  serialises whole cycles so a scheduled scan and a manual one cannot
  interleave. `fetch_watchlist_candles` and `order.to_dict()` run OUTSIDE the
  orchestrator lock, deliberately; `tests/test_trading_service.py` asserts each
  boundary with `RLock._is_owned()` rather than by reading the code. Imports the
  order vocabulary only — never `broker.registry`, which holds the live-broker
  stubs.
- `intelligence.py` — `payload()` and `summary()`, the projections of one
  snapshot. `PERIOD_LIMITS` and `SUMMARY_METRICS` live here.
- `notifications.py` — `CATALOGUE` (13 kinds; severity + a `pushable` flag
  deliberately orthogonal to it), `NotificationService.recent()` (newest first,
  a service decision so two clients cannot disagree about it). Not a store.
- `workspace.py` — pure `normalize` / `merge` plus `WorkspaceService`. Holds no
  second catalogue: `tab` and indicator names are frontend vocabulary and are
  checked for type and length only; `timeframe` IS validated against
  `core.models.Timeframe` because it is handed back to `/api/candles`.
- `sync.py` — `INVENTORY` (20 durable objects) + `CLIENT_TRAPPED` (2), each with
  a `SyncDomain` and a `SyncPolicy`. **Syncs nothing**; it is the classification
  that must exist before anything could. `data/credentials.json` is the only
  `NEVER`.
- `registry.py` — `ServiceRegistry`, the one place they are wired. Its
  constructor signature is the honest statement of what a second client's
  backend must supply.

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
  `/api/backtest` (job slot, GET/POST), `/api/guide` + `/api/guide/state`
  (V0.6.1 — guided-onboarding progress and tutorial suggestions),
  `/api/update/{status,check,download,progress,cancel,apply,skip,settings}`
  (the self-updater, V0.5.0 — see `update/` above), `/ws` (2s status push),
  `/webhook/tradingview`. All orchestrator access serialized through
  `UIServer.lock`.
- `ui/static/index.html` — self-contained dark dashboard (no build step;
  the one vendored asset is `lightweight-charts.js`). Tabs: Dashboard,
  Charts (interactive candles/volume, EMA/VWAP/BB overlays, RSI/MACD
  subpanes, drawings persisted in localStorage, fullscreen), Trade (manual
  paper trading), Coach, Watchlist, Journal, Backtest, Learning, Settings.
  Keyboard 1–9 switches tabs; F toggles chart fullscreen; `?`/`Ctrl+K` opens
  the searchable help centre. Header has both mode controls (the AI/Human
  segmented toggle and the trading-mode segmented toggle) plus a
  **Learn: \<screen>** button that relabels on every tab switch.
  **Guided onboarding (V0.6.1)** lives here too: `GUIDE_TUTORIALS` (11
  walkthroughs, 52 steps), `GUIDE_TERMS` (37 glossary entries),
  `GUIDE_FEATURES` (the instrumentation vocabulary) and the `Guide` module —
  spotlight, floating card, help centre, adaptive tooltips, tutorial catalogue
  and the order-ticket guardrails (`tkSyncTicket`). See `docs/ONBOARDING.md`.
- `ui/desktop.py` — uvicorn thread + pywebview native window; single-
  instance guard (localhost port mutex); `--windowed` PyInstaller build has
  no console (see `core/logging_setup.py`'s `sys.stderr is None` check).
  **`DesktopApplication` (V0.9.1-C11) owns the wiring**, with `launch()` a thin
  adapter over it. Collaborators (`webview`, `uvicorn`, `create_app`,
  `create_tray`, the instance lock, the port) are **injected**, defaulting to
  lazily-imported real ones, so the composition is assertable without a GUI —
  `acquire()` / `build()` / `shutdown()` are tested; `run()` is the blocking GUI
  loop. This replaced 85 lines inside `launch()` marked `# pragma: no cover`,
  which is where this file's worst defects historically lived (the tray once
  received Uvicorn's transport object instead of the application server).
  `_DesktopController` remains the lifecycle owner — **read its class docstring
  before touching anything on the close path**; `exit()` is single-entry under
  `_exit_lock` (C7), and readiness comes from `uvicorn.Server.started` rather
  than an HTTP self-poll (C10).
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
