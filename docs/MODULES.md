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

## Config (`optionspilot/config/`)
`load_config(yaml_path, environ)` → `AppConfig`. Layered defaults ← YAML ← env
(`OPTIONSPILOT__SECTION__KEY`). Unknown keys / bad values fail at startup.
Sections: `data`, `indicators` (enable flags + params), `engine` (confidence
threshold, delta/DTE/liquidity filters, evidence weight overrides), `risk`
(all limits), `broker` (paper realism + live gate), `notify`, `logging`.

## Data (`optionspilot/data/`)
- `MarketDataProvider` ABC — `get_candles/get_quote/get_expirations/get_option_chain`.
- Canonical candle frame: UTC index `ts`, columns `open high low close volume`
  (enforced by `base.validate_candles`).
- `YFinanceProvider` — free delayed data; 4h resampled from 1h.
- `CandleCache` — SQLite upsert cache keyed (symbol, timeframe, ts).

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
- `MultiTimeframeAnalyzer.analyze({tf: candles})` → `{tf: TimeframeView}`
  (skips timeframes with < 40 bars; respects indicator enable flags).
- `ConfluenceScorer.score(views)` → `ScoreResult(direction, confidence, net,
  evidence)`. 15 evidence types, LONG-perspective scores in [-1,1], weighted
  mean → confidence = |mean|·100, damped 25% in consolidation.
- `ContractSelector.select(direction, chain, spot, today)` → `SelectionResult`
  with per-reason rejection counts.
- `TradePlanner.plan(signal, entry_view, contract, spot)` → `TradePlan | None`.
- `DecisionEngine` — facade: `evaluate()` (always returns the signal, flags
  `tradeable` vs threshold), `build_plan()`.

## Risk (`optionspilot/risk/`)
`RiskManager` — the only path to the broker.
- `approve(plan, open_positions, now)` → `RiskDecision(approved, quantity, veto,
  notes)`. Gate order: halt → weekday/hours (ET) → daily trade limit → max open
  positions → min RR → loss cooldown → sizing.
- Sizing: `equity · risk% / min(premium·100, |delta|·stop_distance·100·1.25)`,
  capped at `max_contracts`.
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
  `broker.update_position_management`.
- `registry.create_broker(config, db_path, cash)` — the only place brokers are
  constructed. `paper` is real; `alpaca`/`tradier`/`webull`/`ibkr` are
  extension slots that raise `BrokerError` with adapter guidance. The live
  gate (two config flags) is re-checked here, defense in depth.

## Journal / Learning / Backtest (`journal/`, `learning/`, `backtest/`)
- `TradeJournal` — SQLite record of every round trip (reasons, evidence names,
  conditions, annotations); `build_trade_record` aggregates partial exits.
- `LearningEngine` — slices by evidence/hour/DTE/confidence/direction/exit;
  `recommend_weights` (min-sample gated, ±20%/cycle, bounded 0.25×–2× default);
  `WeightStore` — versioned weights + rationale at `data/learning/weights.json`.
- `Backtester.run(symbol, candles_by_tf)` — replays through the live engine/
  risk/broker stack; `_slice_closed` guarantees no lookahead; synthetic
  BS-priced chains; `BacktestReport` → JSON + HTML with all metrics.

## Orchestrator (`orchestrator.py`) & Notify (`notify/`)
- `Orchestrator.run_cycle()` — fetch → manage positions → mark/risk → halt
  surfacing → scan entries → large-move alerts. `run_forever()` is the
  market-hours loop. Open-trade journal context persists at
  `data/state/open_trades.json`; risk state is rebuilt from the journal at
  startup. Exits are never risk-gated; entries always are.
- `NotificationCenter.notify(kind, title, body)` — never raises; desktop
  toasts + SMTP email (password via `OPTIONSPILOT_SMTP_PASSWORD`).

## Integrations (`integrations/`)
- `parse_alert(payload, secret)` — validates TradingView webhook JSON
  (constant-time secret compare, symbol normalization, note truncation).
- `Orchestrator.scan_single(symbol)` — what an alert triggers: the full
  engine + risk pipeline for one symbol. Alerts change *when* the system
  looks, never *whether* it trades.
- Config gate: `integrations.tradingview_webhook` + 16-char minimum secret.

## UI (`ui/`) & CLI (`__main__.py`)
- `create_app(config, orchestrator, run_loop)` — FastAPI: `/api/status`,
  `/api/scan`, `/api/journal`, `/api/learning`, `/api/config`,
  `/api/risk/reset_halt`, `/api/backtest` (job slot), `/ws` (2s status push),
  `/webhook/tradingview`. All orchestrator access serialized through
  `UIServer.lock`.
- `ui/static/index.html` — self-contained dark dashboard (no build step).
- `ui/desktop.py` — uvicorn thread + pywebview native window.
- CLI: `run | ui | serve | scan | status | journal | backtest | learn`.
- Packaging: `scripts/build_exe.ps1` → `dist/OptionsPilot/OptionsPilot.exe`
  (args pass through to the CLI; no args opens the desktop app).
- `scripts/soak.py --cycles N` — stability soak: repeated live cycles on a
  scratch data dir, tracking exceptions, heap growth, and cycle times.
