# AI_HANDOFF.md — complete technical orientation

This document is a complete technical orientation to OptionsPilot for an AI
assistant that has never seen this codebase before. If you haven't already,
read `AI_CONTEXT.md` first (vision/philosophy/standards); read this
document second; then `NEXT_SESSION.md` (what to do right now) and
`ARCHITECTURE.md` (how it fits together, with diagrams) — you should not
need to read source files just to get oriented. This is the full reading
order `CLAUDE.md`'s "Before you do anything" section specifies.

## What this project is

**OptionsPilot** is an AI-powered **options paper-trading** desktop
application. It is explicitly **not** a live-trading system — there is no
code path that can place a real order with real money (see "Safety
architecture" below). It:

- Analyzes markets continuously across multiple timeframes using a large
  technical/structural/smart-money analysis library.
- Scores every potential trade 0–100% confidence with a full itemized
  reasoning trail.
- Can trade a simulated account **autonomously** ("AI Mode") *or* let the
  user trade manually with a full order ticket while an AI coach reviews
  every trade ("Human Mode").
- Manages risk (position sizing, daily/weekly loss limits, circuit breakers)
  identically in every mode.
- Journals every trade, learns from the journal (bounded, auditable weight
  adjustments), and can backtest a strategy on historical data.
- Ships as a real Windows desktop app: a single `.exe`, no console window,
  no terminal required to run it.

The user's own words on intent: "a polished, professional desktop trading
platform that combines the best aspects of TradingView, Webull, and
Thinkorswim, while adding an AI trading coach that can both trade
autonomously in AI Mode and teach me in Human Mode."

## Safety architecture (do not weaken this without being asked)

- The only `Broker` implementation is `PaperBroker` — a simulator. Real
  broker adapters (`broker/registry.py`) exist only as named stubs that raise
  `BrokerError` with guidance; there is no live order-placement code at all.
- `BrokerConfig.name` must be `"paper"` unless *both* `live_trading_enabled`
  and `i_understand_the_risks` are set — and even then, no real adapter
  exists to receive the flag. This is enforced by a pydantic validator in
  `config/settings.py`.
- Every subsystem is designed so a future live adapter would slot into the
  same `Broker` interface without touching the engine, risk manager, or UI.
- Do not build a live-trading adapter unless the user explicitly asks for it
  in a dedicated request — it is a deliberate, separate decision gate.

## Application architecture

**Not Electron/Tauri.** This was a deliberate decision (see
`docs/ROADMAP-V2.md` "Architecture decision" section): the backend is Python
(pandas/numpy-heavy analysis engine) and would need to be embedded either
way, so a JS-shell rewrite would only replace the window chrome at the cost
of the existing test suite. The actual shell is:

```
┌─────────────────────────────────────────────────────────┐
│  OptionsPilot.exe (PyInstaller --onedir --windowed)      │
│  ┌─────────────────────────────────────────────────┐    │
│  │ pywebview window  ──HTTP/WS──▶  FastAPI (uvicorn) │    │
│  │ (native OS window,               in a background  │    │
│  │  loads static/index.html)        thread            │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                 │
│                         ▼                                 │
│              UIServer.orch (Orchestrator)                 │
│         owns: engine, risk, broker, orders, journal,       │
│               coach, notifier, provider                    │
└─────────────────────────────────────────────────────────┘
```

Everything lives in **one Python process**. There is no separate frontend
build step — `optionspilot/ui/static/index.html` is a single self-contained
HTML file with inline `<style>` and `<script>` (no React/Vue/bundler, no
`npm`). It talks to the backend exclusively via `fetch()` to `/api/*` REST
endpoints plus one WebSocket (`/ws`) that pushes the full status payload
every second when the payload changed (a tiny heartbeat otherwise, which
the frontend ignores — no re-render).

## Backend architecture

Layered, each layer only depends on layers below it:

```
config/       → layered settings (yaml + env + runtime-mutable overlay)
core/         → domain models (Candle, OptionContract, Signal, TradePlan,
                Position, Order, TradeRecord), logging setup, the shared SQLite
                foundation (core/sqlite.py: connect + PRAGMA user_version
                migrations, V0.4.2), and the storage layer (core/paths.py
                AppPaths — the single source of truth for every filesystem path,
                rooted at %LOCALAPPDATA%\OptionsPilot; core/migration.py —
                one-time legacy import + backups + versioned-migration framework,
                V0.4.4)
data/         → market data provider interface + yfinance implementation,
                candle cache, symbol directory (12k tickers), preset lists
analysis/     → PURE FUNCTIONS ONLY, no I/O: indicators, candlestick
                patterns, market structure (BOS/CHoCH), smart money concepts
                (FVG/order blocks/liquidity), volume analysis, options math
                (Black-Scholes, IV solver). Shared verbatim by live trading
                AND the backtester — this is what guarantees backtest/live
                parity.
engine/       → MultiTimeframeAnalyzer → ConfluenceScorer → TradeGate →
                ContractSelector → TradePlanner, composed by DecisionEngine
risk/         → RiskManager: the ONLY path to the broker. All entries pass
                through approve(); exits never do (a stop must always fire).
broker/       → PaperBroker (simulator), OrderManager (manual orders),
                PositionManager (AI stop/target management), registry.py
                (live-broker stubs)
journal/      → SQLite trade record store
learning/     → performance slicing + bounded, auditable weight tuning
experience/   → the AI's long-term memory (V0.4.0–0.4.1): a rich, expandable,
                100k-scalable superset of every completed trade
                (data/experience.db) recorded ALONGSIDE the journal; a
                deterministic Similarity Engine (find comparable historical
                trades → win rate / return / failure mode / ADVISORY calibrated
                confidence); a centralized build_snapshot capturing the full AI
                decision context at entry (feature-symmetric with manual trades);
                and the Experience API (recent / similar / statistics). Advisory
                only — never touches the gate/risk/execution. See
                docs/ROADMAP-V0.4-EXPERIENCE.md
backtest/     → event-driven replay through the SAME engine/risk/broker
coach/        → TradeCoach (deterministic post-trade review, manual trades) +
                CoachProfile (aggregated strengths/weaknesses). AI Coach 2.0
                (V0.4.3) adds a per-trade 10-category scorecard (categories.py)
                and a mentor dashboard (analytics.py: category trends, streaks,
                pattern detection with confidence, improvement timeline, ≤5
                ranked action items) served on GET /api/coach → `dashboard`
intelligence/ → the Trading Intelligence Engine (V0.6.0). build_facts() joins
                journal + experience + coach into one TradeFact per trade; ten
                engines (performance, behavior, patterns, risk, confidence,
                goals, curriculum, recommendations, timeline, achievements,
                reports) produce ONE IntelligenceSnapshot that the Dashboard,
                Coach, Journal and Learning tabs all project from. Imports
                `core` ONLY — it reads the three record types structurally, not
                by import, which keeps it BELOW the coach so the coach can
                become a presentation layer over it. Never gates a trade.
                See docs/TRADING_INTELLIGENCE.md
notify/       → desktop toast / email notifications
orchestrator.py → composes everything into one scan cycle; the only class
                   the UI and CLI actually drive
ui/           → FastAPI app (server.py), pywebview shell (desktop.py),
                   static/index.html (the entire frontend), guide.py (the
                   guided-onboarding domain layer, V0.6.1: state validation,
                   merge semantics and feature-usage → tutorial
                   recommendations; pure, no I/O. The tutorials themselves are
                   DATA in index.html — a step is a CSS selector plus a
                   sentence, which is not knowledge Python should hold. The two
                   catalogues share IDS only, asserted in both directions by
                   tests/test_guide.py. See docs/ONBOARDING.md)
__main__.py   → CLI: run / ui / serve / scan / status / journal / backtest / learn / selftest
                (_bootstrap builds AppPaths + runs initialize_storage; selftest
                verifies the storage layout is writable + the migration marker is
                valid AND that lazily-imported deps are importable — the
                packaged-bundle gate run by scripts/build_exe.ps1 after every build)
```

### The one-cycle data flow (`Orchestrator.run_cycle`)

Called every `engine.scan_interval_seconds` (default 60) while the market is
open, or on demand via `/api/scan`:

1. Fetch candles for every watchlist symbol × timeframe — in parallel,
   through the `CachedProvider` (timeframe-aware TTLs, so most cycles only
   refetch the entry timeframe and daily bars). In the UI server this phase
   runs OUTSIDE the orchestrator lock, so status reads never block on it.
2. **Manage AI positions**: `PositionManager.review()` checks stop/target/
   CHoCH-invalidation/partial-exit for positions where `managed_by == "ai"`.
   It explicitly ignores `managed_by == "manual"` positions (V2-3 change).
3. **Evaluate working orders**: `OrderManager.evaluate()` checks every
   manual limit/stop/take-profit/trailing-stop order against fresh quotes.
4. **Reconcile manual trades**: capture analysis context for open manual
   positions; when one closes, rebuild the round trip from broker fill
   history and hand it to `TradeCoach.review()` (V2-3 — see below).
5. Mark positions to market, update the risk manager's equity, persist an
   equity snapshot (for max-drawdown / total-return metrics).
6. Surface circuit-breaker halts as notifications.
7. **Scan for entries** — for symbols with no open position:
   - `DecisionEngine.evaluate()` → confidence score + `TradeGate` verdict
     (conservative/high-risk/custom quality-adaptive threshold).
   - **If `operating_mode == "human"`**: a tradeable signal becomes a
     one-time "advice" notification. The AI **never places an order.**
   - **If `operating_mode == "ai"`** (default): tradeable signals go to
     `DecisionEngine.build_plan()` → `RiskManager.approve()` →
     `PaperBroker.open_position()`.
8. Check for large moves (notification only).

Exits are never risk-gated — only entries are. This is intentional: a stop
must always be honorable regardless of the daily loss limit.

## Operating modes (there are TWO independent mode axes — don't conflate them)

1. **`operating_mode`**: `"ai"` (default) or `"human"`. Controls *who places
   entries*. Set via `POST /api/operating_mode`, persisted in
   `data/settings.json`, switches instantly, no restart.
2. **`trading_mode`**: `"conservative"` (default) / `"high_risk"` /
   `"custom"`. Controls *the confidence threshold logic* the AI's gate uses
   — this applies whether the AI is placing the trade itself (AI Mode) or
   just advising (Human Mode advice notifications use the same gate).

These are orthogonal: switching `trading_mode` must never flip
`operating_mode` and vice versa — see `RuntimeSettings._apply_mode()`,
which explicitly preserves `operating_mode` when applying a trading-mode
baseline restore.

## Trading logic overview

`ConfluenceScorer` (`engine/scorer.py`) computes 15 evidence signals (HTF
trend, structure breaks, EMA stack, RSI/MACD/ADX, VWAP, volume pressure,
divergence, candlesticks, premium/discount range position, liquidity grabs,
zone confluence), each scored −1..+1 and weighted, damped 25% during
consolidation. `TradeGate` (`engine/gate.py`) then decides tradeability:

- **Conservative**: fixed `min_confidence` bar (default 80%).
- **High-Risk**: bar adapts to a deterministic *setup quality* classification
  (excellent/good/average/poor) built from evidence composition — poor
  setups never trade at any confidence; entries below the conservative bar
  additionally require risk/reward ≥ `high_risk_min_rr_stretch`.
- **Custom**: user-set fixed thresholds (six tunable risk/engine fields),
  validated through the same pydantic models as `config.yaml`.

`ContractSelector` picks a specific option contract (delta target, DTE
window, liquidity filters). `TradePlanner` builds stop/target/partial levels
from market structure (swing highs/lows) with an ATR fallback.

## Paper trading implementation

`PaperBroker` (`broker/paper.py`) is a SQLite-backed simulator:
- Buys fill at ask + slippage; sells fill at bid − slippage; commission per
  contract on both sides.
- `open_position()` is the AI path (takes a `TradePlan`); `open_manual()` is
  the Human Mode path (no plan, `managed_by="manual"`).
- Persists account (cash, realized P/L), positions (including AI stop/
  target/partials OR manual-managed flag), fill history, and periodic equity
  snapshots (`equity_history` table) for lifetime drawdown/return metrics.
- Survives restarts — positions and account state reload from disk.

`OrderManager` (`broker/orders.py`, **new in V2-2**) is the manual order
book: MARKET (immediate), LIMIT (option premium), STOP_LOSS / TAKE_PROFIT /
TRAILING_STOP (underlying price levels, put-aware — mirrors direction),
DAY (expires 16:00 ET) / GTC time-in-force. Evaluated once per scan cycle
against fresh quotes (no intrabar fills — documented limitation of delayed
data). Persisted to `data/orders.db`; sell orders auto-cancel if the
position closes first; reservation checks prevent overselling a position
across multiple bracket orders.

## Trade Coach implementation (V2-3, newest subsystem)

`coach/coach.py` — `TradeCoach.review()` takes a closed `TradeRecord` plus
entry/exit context snapshots (captured by the orchestrator near the moment
of interest — HTF trend, gate verdict, RSI/ADX/rvol, contract Greeks/IV/DTE,
time of day) and the contract's order history, and produces a `CoachReview`:

- **Before-the-trade findings**: setup quality agreement, trend confirmation,
  chased-entry check (RSI extremes), volume sufficiency, DTE/IV/delta
  sanity, position sizing (% of equity), opening-chop timing, revenge-trade
  detection (entered <15 min after a loss).
- **During-the-trade findings**: was a stop ever placed, was a target
  defined, was the stop moved *against* the position, was the position
  averaged down.
- **After-the-trade analysis**: win/loss/scratch verdict, why (direction vs.
  premium decay), held-loser detection (<-50% premium), cut-winner-early
  detection.
- **Score 0–100 — deliberately scores PROCESS, not outcome.** A disciplined
  stopped-out loser scores well; a reckless winner (no stop, counter-trend,
  chased, oversized) scores low. This is a documented design decision, not
  an accident — see the module docstring and `test_coach.py`'s
  `test_disciplined_loser_scores_well` / `test_reckless_winner_scores_badly`.
- **Mistake taxonomy** (`MISTAKES` dict, 14 tags): each tag carries a label,
  a "what a professional would do" note, and a concrete practice exercise.
- Reviews persist as JSON files under `data/coach/<trade_id>.json`.

`coach/profile.py` — `CoachProfile` aggregates all persisted reviews into:
recurring mistakes ranked by frequency, top strengths, score trend
(improving/declining over time), win rate by setup quality, and the top 3
recommended exercises. Rebuilt fresh from disk every time — never drifts
from the underlying evidence.

**Reconciliation loop** (`Orchestrator._reconcile_manual`): manual round
trips aren't journaled at order-placement time — they're detected by diffing
open `managed_by="manual"` positions cycle-to-cycle. Context is captured
while the position is open (best-effort; survives missing data), and on
close the round trip is rebuilt from `PaperBroker.fills_for()` +
`OrderManager.orders_for()`, coached, and journaled with
`strategy="manual"`, `mistakes`, `lessons` (= coach's improvement
exercises), and `market_conditions["coach_score"]`.

## Journaling system

`journal/journal.py` — SQLite `TradeRecord` store. AI-mode trades are
journaled directly by the orchestrator when a position fully closes (using
`_TradeMeta` restart-safe context in `data/state/open_trades.json`). Manual
trades are journaled by the reconciliation loop above. Both paths converge
on the same `TradeRecord` schema and the same `TradeJournal.record()` call,
so `/api/journal` and the Journal tab show AI and manual trades uniformly
(distinguishable by `strategy` field: engine name vs `"manual"`).

## Human Mode vs AI Mode — the exact behavioral contract

| | AI Mode (default) | Human Mode |
|---|---|---|
| Who places entries | `Orchestrator._scan_symbol` → `RiskManager.approve()` → `PaperBroker.open_position()` | User, via Trade tab → `/api/orders` → `PaperBroker.open_manual()` |
| Does the engine still scan? | Yes | Yes — same `DecisionEngine.evaluate()` call |
| What happens on a tradeable signal | Auto-trades | One-time "advice only" notification (never repeated for the same bar) |
| Who manages exits | `PositionManager` (AI stops/targets, `managed_by="ai"`) | `OrderManager` working orders the user places (`managed_by="manual"`) |
| Risk limits (halt, hours, daily trade limit, max positions, cooldown, max contracts) | Fully enforced via `RiskManager.approve()` | Fully enforced via `RiskManager.approve_manual_entry()` — market buys preflighted in `UIServer.place_order`, delayed limit fills approved at trigger time by `OrderManager.evaluate`'s callback. The %-risk position sizing is advisory only for manual trades (oversizing is the coach's `oversized` tag to flag, not a hard block) |
| Coached? | No (learning system tunes evidence weights instead) | Yes — every closed round trip gets a `TradeCoach` review |

Switching `operating_mode` is instant (no restart) and does **not** close
open positions or cancel working orders of the mode you're leaving — an AI
position keeps its AI-managed stop even if you flip to Human Mode mid-trade,
and vice versa. This is intentional (see `PositionManager.review()`'s
`managed_by` guard and `OrderManager`'s independence from `operating_mode`).

## Runtime settings vs config.yaml

Two config layers, by design:

1. **`config.yaml`** (+ `OPTIONSPILOT__SECTION__KEY` env vars) — the
   structural, startup-only configuration: broker, data provider, indicator
   enable flags, logging, integrations. Validated by pydantic in
   `config/settings.py`; invalid values refuse to start.
2. **`data/settings.json`** (via `config/runtime.py::RuntimeSettings`) — the
   in-app-editable overlay: watchlist (+ pinned + favorites), `trading_mode`
   (+ custom-mode tunables), `operating_mode`, updater preferences, the
   guided-onboarding document (`guide`, V0.6.1) and the workspace document
   (`workspace`, V0.7.0 — selected tab, symbol, timeframe, indicators, sidebar,
   recent symbols, saved layouts). Applied on top of the yaml
   config at startup (`RuntimeSettings.apply()`), then mutated live by UI
   actions under the `UIServer.lock`. A `baseline` snapshot (yaml values,
   taken before any runtime overlay) lets mode switches restore exact yaml
   values when leaving `custom` mode.

`operating_mode` is a real, validated `EngineConfig` field and CAN be set
directly in `config.yaml` (`engine: operating_mode: human`) — documented
with an inline comment there since 2026-07-16, matching `trading_mode`.

**`market_data:` (V0.5.3)** is a startup-only `config.yaml` section owning every
operational knob of the data subsystem: per provider `enabled` / `priority` /
`timeout` / `max_attempts` / `retry_backoff` / `min_request_interval` /
`breaker_threshold` / `breaker_base_cooldown` / `breaker_max_cooldown` /
`min_quality_score`, plus `dynamic_ranking`, `memo_max_entries`,
`structured_logging`, `capability_discovery`, `capability_refresh_days` and a
`cache:` block (`enabled`, `retention_days`, `warn_bytes`). Full reference:
`docs/MARKET_DATA.md` §16.

There is a **layering subtlety worth knowing before you touch it**: `data/` may
import only `core/`, and `config/` may not import `data/` (both enforced by
`tests/test_architecture.py`). So the runtime shape is frozen dataclasses in
`data/config.py`, the validated YAML face is
`config/settings.py::MarketDataConfigSection`, and the translation
(`MarketDataConfig.from_mapping(cfg.market_data.model_dump())`) happens in
`orchestrator.py` — the composition root that already imports both. Keys map
1:1 and two tests assert the key sets are identical, so adding a field to one
and forgetting the other fails the suite rather than silently dropping it.
An unknown key raises at startup; an unknown *provider* is accepted, so a
config can pin a future provider's settings before its adapter ships.

**API keys (V0.5.4).** Three providers need credentials: Finnhub, Twelve Data,
Alpha Vantage. Resolution is **environment-first** —
`market_data.providers.<n>.api_key_env`, then the conventional
`<PROVIDER>_API_KEY` (so the common case needs no configuration at all), then
`market_data.providers.<n>.api_key` in the file. A whitespace-only value counts
as absent.

Two things to know before touching this:

1. **A missing key must never crash or fail loudly.** The adapter is still
   constructed and reports `missing_api_key` (plus a signup URL) through
   diagnostics; it is simply never selected. The app ships with zero keys and
   must chart normally in that state. **Since V0.5.7 `enabled: false` behaves
   the same way** — the provider is constructed, benched, listed and
   self-explaining, rather than being dropped from the registry. A provider
   that is not constructed cannot be shown in Settings, cannot explain its own
   absence, and cannot be switched back on without editing a file. It
   contributes no `deepest_earliest` history floor either way.
2. **Keys are redacted by default.** `ProviderConfig.as_dict()` returns `***`
   unless explicitly asked not to, because that dict reaches
   `/api/diagnostics/marketdata`, the JSON export and the text report — all of
   which users are invited to attach to public bug reports. Do not add a new
   path that serialises config without going through it.

**Budgets (V0.5.4).** `market_data.providers.<n>.requests_per_minute` /
`requests_per_day` override a provider's published limits (worth setting only
on a paid plan). `market_data.quota_state_path` is where daily counts persist;
the orchestrator defaults it to `<data>/quota.json` so a restart cannot mint an
allowance the plan never granted.

**Credentials and live control (V0.5.7).** Keys pasted into **Settings ▸ Market
data** are stored in `<data>/credentials.json` (`market_data.credentials_path`)
— its own owner-only file, deliberately *not* `settings.json`, because
everything in `settings.json` is treated as ordinary backed-up user data and a
secret needs the opposite defaults. Resolution order is
**`environment → credentials.json → config.yaml → missing`**, and it has one
implementation: `CredentialStore.overlay()` fills in `ProviderConfig.api_key`,
which `resolve_api_key` consults *after* the environment. So a key set in an
environment variable silently outranks one pasted in the UI — which is why the
dashboard reports `credential.source` and the save response says so explicitly.

**A plaintext key leaves `data/credentials.py` only through `resolve()`.** Every
other accessor returns `••••••••abcd`. `tests/test_credentials.py::TestNoLeak`
enumerates every payload this repo invites users to attach to a bug report and
asserts the key is absent; **a new export path belongs in that test before it
ships.**

Live choices (provider order, per-provider enable, ordering mode) persist to
`<data>/marketdata.json` (`market_data.control_state_path`) and are folded into
the startup config by `control.apply_control_state()` — before the providers
are constructed, so a provider disabled last session is off from the first
request rather than from whenever Settings is next opened. Every field is
type-checked on the way in: a hand-edited preferences file must cost a user
their preferences, never their app.

**Ordering has three modes** (`market_data.ordering_mode`): `static` (rank =
priority), `hybrid` (the full rank formula **minus its latency term** — your
order stands until a provider is genuinely failing), and `dynamic` (the
default, full health ranking). `dynamic_ranking: false` is the older, more
explicit spelling of `static` and still wins.

**`market_data.qa_mode`** exposes the developer fault-injection panel. **Off in
every shipped build**, and the `/api/marketdata/qa/*` endpoints return **404**
(not 403 — a 403 confirms the endpoint exists) while it is.

## APIs and endpoints (FastAPI, `optionspilot/ui/server.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves `static/index.html` |
| GET | `/api/diagnostics/marketdata?traces=N` | Everything needed to diagnose a chart complaint without reproducing it (V0.5.2): per-provider health (availability, failure rate, latency, rolling data-quality score, circuit-breaker and rate-limit state), cache stats (bars/symbols/bytes/schema version/rebuilds/bars-by-provider), aggregate request outcomes, and the last N request traces — each naming every provider tried, why each was skipped or failed, which tier answered, and what validation found. Returns `{"available": false}` rather than erroring when the injected provider predates this architecture. **V0.5.3** adds, per provider: `rank` and `state` (closed/open/half_open), `p95_latency_ms`, `success_rate`, `requests_today`, `timeouts`, `validation_failures`, `rate_limits`, `breaker_trips`, `last_success_at`, `intervals`, and the `config` in force; plus top-level `ranking` (ordered, with positions), `memo` (entries/max), `config`, `version`, and richer `cache` metrics (hit rate, stale reads, evictions, average age, `provider_requests_saved`, span held). Each trace also carries `chain` — every provider tried and its verdict, the same string the structured log line uses. This is the payload **Help ▸ Diagnostics** renders |
| GET | `/api/diagnostics/marketdata/export?format=json\|text&traces=N` | The same payload as a dated download (`Content-Disposition: attachment`). `format=text` renders `data/report.py`'s human-readable report — built to be safe to paste into a public issue tracker (no stack traces, no filesystem paths, no credentials) |
| POST | `/api/diagnostics/marketdata/replay` | `{"trace_id": N}` — re-run a recorded request through the live ladder AND poll every provider directly, returning each one's bars, latency, quality and disagreement against the first that answered. POST because it is not free: one upstream request per provider, deliberately bypassing the memo and cache so it measures the real chain. 400 without a `trace_id`, 404 for a trace no longer in the ring or a provider that cannot support replay |
| GET | `/api/marketdata` | **V0.5.7** — the whole Market Data Control Centre in one payload: every provider's health row (as `/api/diagnostics/marketdata`, plus `health_state` + `health_detail`, the human-facing derived state and its mandatory explanation), `credential` (required / source / **masked** key / configured-at / last-success / env vars / `env_overrides` / signup URL), `feed` (cost / limits / latency / key labels), `capability` (intraday/daily/weekly/monthly, intervals, max lookback), `quota`, `enabled` and `position`; plus top-level `ranking`, `order`, `default_order`, `ordering_mode` + `ordering_modes` (each with its plain-English explanation), `cache`, `requests`, `failover` (usable providers, **independent** sources — Yahoo and yfinance count as one — primary, next, `single_point_of_failure`), `recommendations`, `maintenance` (job + the eight available actions, each declaring whether it spends upstream requests), `qa_mode` and `health_text`. **Safe to poll**: every number is a counter that already exists and nothing here touches the network. One request rather than N because the settings page auto-refreshes |
| POST/DELETE | `/api/marketdata/providers/{name}/key` | Store or forget an API key, applied to the live adapter with no restart. The key is written to `credentials.json` and **never echoed back** — the response carries a mask. When an environment variable is shadowing what was just saved, the response says so explicitly (`env_overrides`), because "I typed my key in and it still says no key" is the worst possible bug report. 400 for an unknown provider, a keyless provider, or an empty key |
| POST | `/api/marketdata/providers/{name}/enabled` | `{"enabled": bool}` — bench or restore a provider without a restart. It stays listed either way |
| POST | `/api/marketdata/providers/{name}/test` | Run a real request against one provider and report what happened. POST because it spends an upstream request. End to end on purpose — transport, authentication, parsing, normalization **and** semantic validation — so a provider whose response format has changed fails the test rather than passing it. Returns a closed vocabulary (`connected` / `missing_key` / `authentication_failed` / `rate_limited` / `provider_unreachable` / `network_failure` / `unexpected_response` / `disabled` / `unknown_provider`), each with one sentence of explanation and one recommended action. A provider that is out of budget or has no key is answered **without** a request |
| POST | `/api/marketdata/providers/{name}/move` | `{"direction": "up"\|"down"}` — one place in the configured order. Moving past either end is a no-op, not an error |
| POST | `/api/marketdata/order` · `/api/marketdata/order/reset` | Set the whole order (`{"order": [names]}`, best first) or restore the shipped chain. Unknown names are ignored and omitted providers keep their relative order at the end, so a stale list from another build reorders what it can and breaks nothing. Priorities are rewritten **10, 20, 30…** because 10 rank points equals one second of latency |
| POST | `/api/marketdata/ordering_mode` | `{"mode": "static"\|"hybrid"\|"dynamic"}`. 400 for anything else; the response carries the mode's plain-English explanation |
| POST/GET/DELETE | `/api/marketdata/maintenance` | Start / poll / stop one of eight maintenance actions (clear cache, rebuild cache, verify cache integrity, run validation, run replay, run benchmark, run diagnostics, re-measure capabilities). One background job slot with polled progress — a capability re-measurement takes minutes, so a synchronous endpoint would hold a request open past any client timeout. A busy slot is refused **by name** ("'Re-measure capabilities' is still running"). Cancellation is cooperative, checked between units of work, keeps what it measured, and reports state `cancelled` rather than `error` |
| GET/POST/DELETE | `/api/marketdata/qa/*` | Developer fault injection: `qa` (state), `qa/fault` (arm/clear), `qa/breaker` (force a provider out of rotation), `qa/reset` (clear every breaker), `qa/corrupt_cache` (the corruption-recovery drill, run on a **copy** — the real cache is never touched). **All return 404 unless `market_data.qa_mode` is true**, which it is in no shipped build |
| GET | `/api/status` | Full dashboard payload (account, positions, signals, notifications, watchlist, modes, scan progress) — also pushed over `/ws` |
| POST | `/api/scan` | Run one cycle: non-blocking by default (triggers the `manual_scan` runtime task on the worker lane; progress streams in the status payload's `scan` field); `{"wait": true}` for synchronous. A request arriving while a cycle is in flight is **declined, not queued** |
| GET | `/api/journal` | Trade history + stats, plus a `findings` map (`trade_id → [intelligence finding labels]`) so rows can be badged without a request each (V0.6.0) |
| GET | `/api/learning` | Evidence weights + performance slices |
| GET | `/api/config` | Effective config.yaml values (read-only) |
| GET | `/api/candles?symbol=&tf=&start=&end=` | OHLCV + indicator series for the Charts tab (computed by the same `analysis/` code the engine uses; provider-only, no lock). `tf` is any of the 13 timeframes (1m/2m/3m/5m/10m/15m/30m/1h/2h/4h/1d/1w/1mo). Optional ISO `start`/`end` request an arbitrary window — the UI uses this to prepend history as the user pans left (V3.1-3). Since V3-0 the payload also carries `stale`/`as_of`: when the live fetch fails, disk-cached bars of any age are served flagged stale (display-only fallback — the engine's strict `get_candles` path is unchanged). Since V3.1 RC2 it also carries `market_open` (bool, from `Orchestrator.market_open`): the Charts tab suppresses the "Live data unavailable — showing cached bars" banner when the market is closed (the cached bars ARE the last session, so the banner would be a false alarm) and shows it only when a stale payload arrives during market hours. Bars are sanitized by `validate_candles` (NaN/inf/≤0 dropped, non-finite volume zeroed) so a glitched provider bar can't 500 the endpoint (V3.1-1). Since V3.2, `?ext=1` requests Extended Hours (intraday only): the payload carries `extended_hours` (bool) and each bar a `session` tag (`pre`/`rth`/`post`) from `optionspilot/data/sessions.py`; ext frames are cache-keyed separately and bypass the disk store, and the flag is display-only — the engine/trading path never sets it, so paper execution stays RTH-only . **Since V0.5.2** it also carries the market-data outcome so the frontend never has to infer one from an empty array: `outcome` (`live`/`memo`/`cache`/`stale`/`empty`/`exhausted`/`failed`), `provider`, `quality` (0-100 validation score), `exhausted` (bool — the window predates what ANY provider serves; the chart shows "start of available history" and stops requesting), `earliest_available` (ISO), `message` (a human reason) and `trace_id` (look it up in `/api/diagnostics/marketdata`). Fields are additive; existing consumers are unaffected |
| GET | `/static/lightweight-charts.js` | Vendored chart library (Apache-2.0, offline — the frontend's ONE bundled asset) |
| GET | `/api/chain` | Option chain for a symbol/expiration (Greeks, liquidity) — manual trading ticket data |
| GET/POST | `/api/orders`, `/api/orders/cancel` | Working manual orders: place/list/cancel |
| GET | `/api/account/metrics` | Buying power, P/L windows, win rate, PF, max drawdown |
| GET/POST | `/api/watchlist*` | Add/remove/reorder/pin/favorites/presets, symbol search |
| POST | `/api/mode` | Switch trading_mode (conservative/high_risk/custom) |
| POST | `/api/operating_mode` | Switch AI Mode ↔ Human Mode |
| GET | `/api/coach` | Coach reviews + aggregated profile + an `intelligence` block (the same snapshot every other view reads, minus `periods`), so the Coach tab costs one request and one analysis (V0.6.0) |
| GET | `/api/experience` | Experience Engine statistics (overview + by strategy/regime/session + failure modes/success patterns) + recent experiences (V0.4.1) |
| GET | `/api/experience/similar?symbol=&k=` | Advisory historical-similarity for a symbol's CURRENT setup: the calibrated-confidence explanation + the Similar Trade Viewer rows. Evaluates the symbol deterministically; opens no position and changes no state (V0.4.1) |
| GET | `/api/intelligence?full=` | The complete Trading Intelligence analysis: metrics, behaviours, patterns, scores, risk, goals, recommendations, lessons, timeline, achievements, reports (V0.6.0). `full=false` drops `periods` — the only unbounded section |
| GET | `/api/intelligence/summary` | The dashboard projection of the SAME snapshot: headline metrics, the eight scores, top actions, goals, achievements, timeline, lessons, latest report (V0.6.0) |
| GET | `/api/intelligence/trade/{trade_id}` | Per-trade projection: the patterns it belongs to, the habits it is evidence for, its percentile in your own distribution, the comparable cohort. 200 with `available:false` for an unknown id — a 404 would read as "this trade does not exist" (V0.6.0) |
| GET | `/api/intelligence/reports` | Weekly + monthly coaching reports (V0.6.0) |
| GET | `/api/intelligence/goals` | Active goals with computed progress, the suggested templates, and the metric vocabulary a custom goal may target (V0.6.0) |
| POST | `/api/intelligence/goals` | Create/replace a goal. 422 with the precise reason if it names a metric, comparator or window that could never evaluate (V0.6.0) |
| DELETE | `/api/intelligence/goals/{goal_id}` | Remove a goal; 404 if unknown (V0.6.0) |
| GET | `/api/guide` | Guided-onboarding state, measured feature-usage facts, and which walkthrough to offer next (V0.6.1). Progress lives in `settings.json` under a `guide` key, **not** localStorage, so a reinstall or a cleared webview profile does not greet a returning user as a beginner |
| POST | `/api/guide/state` | Merge a patch (`completed` / `dismissed` union, `features` increment, `onboarded` / `reduce_motion` / `tips` replace, `forget` resets) and return the full state plus fresh suggestions. Deliberately forgiving — an unknown id, an unusable feature key or a garbage body is ignored rather than rejected, because this endpoint records that someone finished a tour and failing it would be a 4xx in the middle of a celebration |
| POST | `/api/risk/reset_halt` | Manual circuit-breaker reset |
| GET/POST | `/api/backtest` | Backtest job (the `backtest` runtime task on the worker lane, polled status). One slot, claimed under `_bt_lock`; a second POST while one runs returns the running job unchanged |
| POST | `/webhook/tradingview` | Inbound TradingView alert → triggers a scan (never a direct order) |
| GET | `/api/workspace` | Where the user was: tab, symbol, timeframe, indicators, extended hours, auto-follow, watchlist sort, ticket chart, recent symbols, saved layouts (V0.7.0). Persisted in `settings.json` under a `workspace` key — **not** localStorage, which is a cache a cleared profile silently discards, and which a second client cannot see at all |
| POST | `/api/workspace` | Merge a **partial** patch and return the full document. Partial by design: a client that only knows about `symbol` must be able to say so without overwriting panel layout it has never heard of. Unusable values fall back to their default rather than 4xx-ing, because this records where someone was looking |
| DELETE | `/api/workspace` | Reset to the shipped defaults, saved layouts included |
| GET | `/api/host` | What this build's host can do: `optionspilot/host/`'s capability profile, so a client decides which surfaces to offer instead of guessing from a user agent. No user data, no secret |
| GET | `/api/diagnostics/sync` | The classified inventory of every durable object the app owns, with its sync domain and policy (V0.7.0). **Nothing syncs anything** — this is the classification that must exist first. `never_sync` names what must not leave the machine. Safe in a public bug report |
| WS | `/ws` | 1s cadence with change detection: full `status_payload()` when something changed, tiny heartbeat otherwise. **Not enveloped** — a known blocker for any client that cannot update in lockstep (`ARCHITECTURE-PLATFORM.md` §7) |

All mutating endpoints acquire `UIServer.lock` (an `RLock`) — the
orchestrator is not thread-safe, and this lock serializes the background
cycle-loop thread against API request threads.

## Database / storage approach

**Storage root (V0.4.4):** all user data lives under a stable per-user root —
`%LOCALAPPDATA%\OptionsPilot` on Windows (XDG/`Application Support` elsewhere),
overridable via `OPTIONSPILOT_HOME` — **not** beside the executable, so
replacing the exe never touches user data. `core/paths.py::AppPaths` is the
single source of truth for every path (`get_data_dir`, `get_journal_db`,
`get_coach_dir`, `get_settings_file`, …); no module constructs the root itself.
At startup `core/migration.py::initialize_storage` creates the layout and, on
first run, imports a legacy CWD/exe-relative `data/`+`logs/` install once
(lossless copy: preserves timestamps, verifies each file, never overwrites a
newer file, never deletes the source), recording completion in
`migrations/migration_version.json`. See `docs/STORAGE.md` for the full design.
The layout under the root is `data/ logs/ backups/ exports/ migrations/`; paths
below are shown relative to `data/`.

Everything is **SQLite + JSON files**, no external database, no ORM. Every
SQLite store opens through the shared `core/sqlite.py` foundation (`connect` +
`run_migrations` on `PRAGMA user_version`), so all five databases evolve their
schema the same versioned way (V0.4.2 — migration 1 of each store is its current
schema, so existing on-disk databases open unchanged):
- `data/paper.db` — account, positions, fills (PaperBroker)
- `data/cache.db` — candle cache (CachedProvider write-through; safe to delete)
- `data/orders.db` — working + historical manual orders
- `data/journal.db` — trade records
- `data/experience.db` — the Experience Engine store (V0.4.0; schema v2 in
  V0.4.1): a rich superset of every completed trade, indexed columns (incl.
  `market_regime`, `return_pct`, `hold_minutes`) + JSON payload, `PRAGMA
  user_version` migrations; written alongside `journal.db`, safe to delete
  (regenerated only for new trades — it is not the system of record)
- `data/settings.json` — runtime-mutable settings (watchlist, modes, updater
  prefs, and since V0.6.1 the `guide` document: tutorials finished/skipped,
  features used, motion and hint preferences). **Treated as ordinary user data**: backed up by `create_backup()`,
  small enough to open in Notepad, safe to share. That is exactly why API keys
  do not live here
- `data/credentials.json` (V0.5.7) — **market-data API keys. The only file in
  the app that holds a secret.** Written owner-only, atomically, and read only
  by `data/credentials.py`; no export path imports that module, so no
  diagnostics payload can carry a key. Deleting it removes every stored key
  (environment variables still work). **Never commit one, never attach one to a
  bug report, and never add a second place a key can be written**
- `data/marketdata.json` (V0.5.7) — control-centre choices: provider order,
  per-provider enable, ordering mode. Holds no secrets; safe to delete (the
  shipped defaults return)
- `data/quota.json` (V0.5.4) — each metered provider's daily request count, so
  a restart cannot mint an allowance the plan never granted. Safe to delete,
  at the cost of over-spending a budget until the next real 429 corrects it
- `data/state/open_trades.json` — AI trade context (restart-safe journaling)
- `data/state/manual_trades.json` — manual trade context (V2-3)
- `data/coach/<trade_id>.json` — one file per coach review
- `data/learning/weights.json` — versioned evidence-weight history
- `data/intelligence/goals.json` (V0.6.0) — **the only thing the Trading
  Intelligence Engine stores.** Every analysis output (scores, behaviours,
  patterns, achievements, reports) is derived on read and persisted nowhere, so
  a stored verdict can never drift from the trades it describes. The file is
  user-editable, so every field's *shape* is validated on load and a malformed
  entry is dropped with a log line — the failure mode is "you lose a goal",
  never "the app will not start". The directory is created on demand, so a user
  who never opens the Goals panel never accumulates it
- `data/reports/` — backtest JSON/HTML reports
- `logs/*.log` — rotating per-subsystem logs

All of `data/` and `logs/` are gitignored — they are per-user runtime state,
never committed.

## Environment variables

- `OPTIONSPILOT__<SECTION>__<KEY>` — overrides any `config.yaml` value, e.g.
  `OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT=0.5`. Parsed via
  `config/settings.py::load_config()`.
- No `.env` file convention exists; no secrets are read from environment
  besides this override mechanism. TradingView webhook secret lives in
  `config.yaml` under `integrations.tradingview_secret`, not an env var.

## Dependencies

Core (`pyproject.toml`): `pandas`, `numpy`, `yfinance`, `pydantic>=2.7`,
`PyYAML`. UI extra: `fastapi`, `uvicorn[standard]`, `pywebview`. Dev extra:
`pytest`, `httpx` (FastAPI TestClient). Notify extra: `windows-toasts`
(optional; falls back to log-only without it). Icon generation
(`scripts/make_icon.py`) needs `Pillow` (not in pyproject — installed ad hoc
during V2-1; **should be added to a `dev` or `assets` extra**, see TODO).

No JS package manager, no `package.json`, no build step for the frontend.
One vendored JS asset: `ui/static/lightweight-charts.js` (TradingView
Lightweight Charts 4.2.3, Apache-2.0) — committed to the repo, served
locally, bundled into the exe by the existing `--add-data ui\static` line.
Chart drawings persist in the webview's localStorage, not in `data/`.

## Build and run instructions

```powershell
# Dev setup
cd optionspilot
python -m venv .venv
.venv\Scripts\pip install -e .[dev,ui]
.venv\Scripts\pip install windows-toasts   # optional, desktop notifications

# Run
.venv\Scripts\python -m optionspilot ui            # desktop window + live loop
.venv\Scripts\python -m optionspilot serve --port 8787   # browser, no window
.venv\Scripts\python -m optionspilot run            # headless loop, no UI
.venv\Scripts\python -m optionspilot scan           # one cycle, print JSON
.venv\Scripts\python -m optionspilot backtest SPY --days 25

# Tests (2363 tests as of this writing, all passing)
.venv\Scripts\python -m pytest

# Package as a Windows exe (no console window; data/ preserved across rebuilds)
.\scripts\build_exe.ps1   # -> dist\OptionsPilot\OptionsPilot.exe
```

`scripts/build_exe.ps1` refuses to build over a running instance (open
SQLite handles) and backs up/restores `dist\OptionsPilot\data\` around the
PyInstaller `--clean` wipe. The app has a single-instance guard — a bound
loopback socket on port 8786, owned by `host.adapter.DesktopHost` and reached
from `ui/desktop.py` via `current_host()` (**one** implementation: it existed
twice until V0.8.2). A second launch shows a friendly "already running" window
instead of corrupting the shared account database.

**Desktop thread ownership (V0.8.2) — read before touching `ui/desktop.py`.**
pywebview binds its `closing` event as `Event(window, should_lock=True)`, which
means handlers run **synchronously on the WinForms message pump**, inside
`Form.FormClosing`. Three things are therefore forbidden inside
`_DesktopController.on_closing`:

| Operation | Why it cannot run on the pump |
|---|---|
| `window.evaluate_js` | WebView2 schedules the `ExecuteScriptAsync` continuation on `syncContextTaskScheduler` — the same pump — and pywebview then blocks on an **untimed** `semaphore.acquire()`. Deadlock, no traceback. |
| `window.hide` / `window.show` / `window.destroy` | All marshal through `Control.Invoke`, which needs the pump. |
| `server.close()` / `tray.stop()` | Up to 5s and 2s of thread joins. Windows ghosts a window that stops pumping for 5s. |

`on_closing` **decides and returns**; every consequence runs on a worker via
`_defer`. The other callbacks — tray menu items (pystray's thread), the JS bridge
(pywebview spawns a thread per call), toast activations, and `refresh_tray` (the
background runtime) — are already off the pump and may call these directly.
`tests/test_desktop_tray.py`'s window double raises `GuiThreadViolation` (a
`BaseException`, so the lifecycle code's `except Exception` cannot swallow it) if
this is violated.

**Release pipeline (V0.4.5).** Releases are automated by GitHub Actions:
`.github/workflows/ci.yml` (push/PR: pytest + selftest + doc/id checks, reusable)
and `.github/workflows/release.yml` (on a `v*` tag: reuse CI → verify tag ==
`__version__` → `scripts/build.ps1` → `scripts/package_release.ps1` →
`OptionsPilot-vX.Y.Z.zip` → GitHub Release). The version has a **single source of
truth** — `optionspilot/__init__.py::__version__`; `pyproject.toml` derives it
via `[tool.setuptools.dynamic] version = {attr = "optionspilot.__version__"}`, so
`scripts/bump_version.py` edits one line. Full guide: `docs/RELEASE.md`.

**Windows installer (V0.4.6).** A `v*` tag also builds and publishes
`OptionsPilot-Setup-vX.Y.Z.exe` (Inno Setup, `installer/OptionsPilot.iss`,
compiled by `scripts/build_installer.ps1`) **alongside** the portable zip. It
installs to `C:\Program Files\OptionsPilot` (admin), registers with Programs and
Features, upgrades in place (stable `AppId`), and prompts before removing user
data on uninstall (default No). User data stays in `%LOCALAPPDATA%\OptionsPilot`,
untouched by upgrades/reinstalls. Code signing is still TODO (SmartScreen warns).
Full guide: `docs/INSTALLER.md`.

**Auto-updater (V0.5.0).** `optionspilot/update/` is a self-contained subpackage
(depends only on `core` + stdlib; no new runtime dep — networking is `urllib`)
that checks GitHub Releases, downloads the installer to `%TEMP%\OptionsPilotUpdater`,
validates it, backs up data (`create_backup(paths, "pre-update")`), and launches
the installer silently (`/VERYSILENT …`) then restarts. Layers: `version`
(SemVer ordering), `transport` (the only networking, retries/backoff/proxy),
`github_api`, `checker`, `downloader`, `validation` (size/hash/Authenticode-ready),
`installer`, `ui` (presentation), `service` (`UpdateService` facade + state
machine). Exposed over `/api/update/{status,check,download,progress,cancel,apply,
skip,settings}`; `UIServer` owns an `UpdateService(__version__, runtime)` and
kicks a background launch-time check **gated on `run_loop`** (so the test suite
never hits the network). Preferences (auto_check/frequency/channel/skip_version/
last_checked) live in `RuntimeSettings` under the `updates` key of settings.json.
Frontend: Settings ▸ Software updates panel, Help ▸ Check for Updates…, and the
update dialog in `index.html`. The updater is verified fully offline via fakes
(`tests/update_helpers.py`); a real Inno upgrade must be QA'd manually. Full
guide: `docs/AUTO_UPDATER.md`.

**Distribution (V0.3.5):** a release zip downloaded from GitHub and extracted
with Explorer stamps every file with the Mark-of-the-Web (`Zone.Identifier`
ADS), and .NET Framework refuses to load MOTW-flagged managed assemblies —
pywebview's WinForms backend then dies inside pythonnet with "Failed to
resolve Python.Runtime.Loader.Initialize" before the window opens.
`optionspilot_app.py::unblock_bundle()` strips the stream from the install
folder at startup (frozen Windows builds only, before webview can `import
clr`), so a downloaded release self-heals on first launch. Guarded by
`TestUnblockBundle` in `tests/test_packaging.py`. OS-provided prerequisites
remain .NET Framework 4.7.2+ and the WebView2 Runtime (both ship with
Windows 10/11 by default).

## Assumptions made during development

- **Free yfinance data is acceptable for v1.** ~15-minute delayed quotes,
  limited intraday history (~60 days of 5m bars). Explicitly documented as
  the upgrade path (paid feed) rather than fixed now.
- **Fills are simulated per scan cycle, not intrabar.** A limit/stop order
  placed between cycles fills (or doesn't) based on the quote fetched at the
  *next* cycle boundary — there is no tick-by-tick simulation.
- **"Screenshots" in the original spec were reinterpreted as re-renderable
  chart-context snapshots** (candle window + entry/exit markers stored as
  data, not PNGs) — this is a deliberate substitution documented in
  `ROADMAP-V2.md`, not yet implemented (V2-6, not started).
- **The AI coach is deterministic, not an LLM call.** Built entirely on the
  existing analysis engine (same code that scores AI trades). No external
  API dependency, fully offline, fully testable.
- **Emotional/behavioral tags (revenge trading, chased entry, etc.) are
  inferred from observable order/timing patterns**, not literal mind-reading
  — documented as an honest limitation in `coach/coach.py`'s module
  docstring.

## Known issues / technical debt

1. (resolved 2026-07-16) `pyproject.toml` `package-data` now includes
   `data_assets/*`, so wheels/sdists ship the 12k-symbol CSV.
2. (resolved 2026-07-16) `Pillow` is declared in the `dev` extra
   (`scripts/make_icon.py` needs it).
3. (resolved 2026-07-16) `operating_mode` now has an inline comment in
   `config.yaml` matching `trading_mode`'s style.
4. V2-2's roadmap line item "stock (share) positions" was explicitly
   deferred — the entire trading engine (chain, orders, coach) is
   options-only. Adding shares would need a new `OptionContract`-shaped
   "stock leg" type and touch `broker/orders.py`, `PaperBroker`, and the
   Trade tab chain UI.
5. Frontend coverage is real but shallow — `tests/test_ui_server.py`
   exercises the FastAPI layer via `TestClient` (2363 tests cover this
   thoroughly), but nothing drives `static/index.html` in a real browser.
   V2-1 through V2-3 frontend surfaces (Trade tab, Coach tab, AI/Human
   toggle) have all been manually live-verified, but there is no regression
   coverage. **If you touch `static/index.html`, manually verify
   in a browser** (see `PROJECT_STATE.md` for the exact verification steps
   used previously) since there's no test safety net for it.
6. (resolved 2026-07-16) The packaged exe now includes V2-3 — rebuilt and
   smoke-tested (mode toggle, manual round trip, coach review) after the
   V2-3 commit. `build_exe.ps1` preserved the app's `data/` across the
   rebuild as designed.

## Future considerations (beyond the current roadmap)

See `docs/ROADMAP-V2.md` "Beyond v1" and "Phases" sections for V2-4 (chart
workspace with `lightweight-charts`), V2-5 (replay engine), V2-6 (journal
screenshots + improvement dashboard) — none of these have been started.
Also: candle cache for the live loop, a real Alpaca paper-API adapter (only
after sustained paper profitability), news/sentiment evidence, portfolio-
level risk (correlated positions).
