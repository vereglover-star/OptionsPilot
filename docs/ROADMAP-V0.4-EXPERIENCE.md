# ROADMAP-V0.4 — The AI Experience Engine

**Status:** Phases 1–3 shipped on branch `v3-ui`. Phases 1–2 (Foundation +
Similarity) 2026-07-23, version 0.3.5 → 0.4.0. Phase 3 (integration: centralized
AI snapshot, feature symmetry, historical-similarity explanation, Experience
API) 2026-07-23, version 0.4.0 → 0.4.1. Phases 4–6 scoped below, not started.

This is the design document the V0.4.0 sprint brief asked for as a deliverable:
root architecture, schema, algorithms, scalability analysis, the lifecycle, and
the forward plan — plus the three architectural decisions that shaped it.
See §12 for the Phase 3 integration specifics.

---

## 1. Purpose and non-negotiables

The Experience Engine is the foundation for an AI that **learns from experience
through paper trading** instead of relying only on fixed rules. It records a
rich superset of every completed paper trade, and — given a new setup — can find
the most similar historical trades and summarize how they turned out.

It was built to respect this project's existing non-negotiables, not around
them:

- **Additive, never destructive.** The `TradeJournal` (`data/journal.db`)
  remains the system of record and the *sole* input to the weight-learning
  system. The Experience Engine is a **parallel** store
  (`data/experience.db`) capturing more, for a different question (similarity),
  and it is written *alongside* journaling, never instead of it.
- **Deterministic and auditable.** Similarity is a hand-authored weighted
  distance, not a trained/fitted model. No LLM, no learned parameters on any
  decision path. Same philosophy as the scorer, gate, and coach.
- **The deterministic scorer stays the sole trading input.** See Decision A.
- **Best-effort recording.** A failure inside experience recording is logged
  and swallowed; it can never disrupt journaling, risk accounting, or the
  trading path (`ExperienceEngine.record_trade` catches everything;
  `tests/test_experience.py::test_record_trade_is_best_effort` proves it).
- **Paper trading only.** Nothing here touches broker execution or live money.

## 2. Three decisions made with the user (2026-07-23)

**Decision A — Confidence calibration is ADVISORY / display-only (for now).**
The brief said to *replace* heuristic confidence with an evidence-based blend of
model output and historical win rates. Feeding historical outcomes into the
number the gate trades on is exactly the "statistical model on the trading path"
that `CLAUDE.md` forbids without a dedicated request. So: the calibrated number
is computed and surfaced (explanations, future dashboard) but **never reaches
the gate**. The deterministic scorer remains the only live trading input.
Promotion to a bounded, opt-in, auditable overlay (mirroring `learning.py`'s
guard-rails) is a future decision, not a default.

**Decision B — Exploration is a future third mode axis: `learning_mode`.**
The brief's Conservative/Balanced/Exploration overlaps the existing
`trading_mode` axis (conservative/high_risk/custom). To honor the
mode-orthogonality rule, exploration will become a **new independent axis**
`learning_mode` (`normal` | `exploration`) that only controls whether the AI
takes tagged, strictly risk-limited lower-confidence paper trades — leaving
`operating_mode` and `trading_mode` untouched. Designed now
(`ExperienceRecord.exploration` is already modelled and persisted/queryable);
wired in Phase 4.

**Decision C — Scope split.** This session ships the load-bearing Foundation
(Phase 1) plus the Similarity Engine (Phase 2). Calibration promotion,
exploration mode, the AI Performance dashboard, and strategy-discovery mining
follow in later phases.

## 3. Root architecture

```
                         (existing, unchanged)
   run_cycle ─▶ journal.record(trade) ─▶ TradeJournal (journal.db)
       │                                        └─▶ LearningEngine (weights)
       │
       └─(best-effort, alongside)
             experience.record_trade(trade, entry_ctx, exit_ctx)
                        │
                        ▼
             ┌───────────────────────────────────────────┐
             │            experience/ (NEW)               │
             │                                            │
             │  features.build_experience ──▶ ExperienceRecord
             │        (pure, deterministic)      │        │
             │                                   ▼        │
             │  ExperienceStore  ◀── record ── (SQLite,   │
             │  (experience.db)                migratable)│
             │        ▲                                   │
             │        │ query (indexed coarse filter)     │
             │        ▼                                   │
             │  SimilarityEngine ──▶ SimilarityResult     │
             │  (weighted distance)   (advisory evidence) │
             └───────────────────────────────────────────┘
```

Layering: `experience/` sits beside `journal/`+`learning/`, depends only on
`core/` (models) — no dependency on `engine/`, `broker/`, `ui/`. The
orchestrator is the only composer that drives it.

## 4. Files

**Created:**
- `optionspilot/experience/__init__.py` — package + invariants docstring.
- `optionspilot/experience/models.py` — `ExperienceRecord` (rich, expandable
  via `extra`), `SimilarityResult`, `FEATURE_RANGES` (fixed normalization).
- `optionspilot/experience/features.py` — pure feature extraction
  (`build_experience`, `build_feature_vector`).
- `optionspilot/experience/store.py` — `ExperienceStore`: SQLite,
  `PRAGMA user_version` migrations, indexed columns + JSON payload.
- `optionspilot/experience/similarity.py` — `SimilarityEngine` + `similarity()`.
- `optionspilot/experience/engine.py` — `ExperienceEngine` façade.
- `optionspilot/experience/snapshot.py` — (Phase 3) centralized `build_snapshot`.
- `tests/test_experience.py`, `tests/test_similarity.py`, `tests/test_snapshot.py`
  (Phase 3) — 52 tests across the three files.

**Modified:**
- `optionspilot/orchestrator.py` — construct `self.experience`; call
  `record_trade` after both `journal.record` sites; (Phase 3) build the AI
  snapshot at entry into `_TradeMeta.entry_context`, route `_capture_context`
  through `build_snapshot`, `_attach_historical` + `experience_for_symbol`.
- `optionspilot/ui/server.py` — (Phase 3) `GET /api/experience`,
  `GET /api/experience/similar`.
- `optionspilot/experience/{models,features,store,engine}.py` — (Phase 3)
  enriched record, shared `_entry_fields` extractor, `build_query_record`,
  migration_2, SQL aggregates, the query API.
- `pyproject.toml`, `optionspilot/__init__.py` — version 0.4.0 → 0.4.1.
- Docs (this file + the standard handoff set).

## 5. The `ExperienceRecord` schema

Keyed by `trade_id` (== `TradeRecord.id`) so the journal and experience stores
join. Only identity/outcome fields are guaranteed populated; everything else is
best-effort and nullable — an AI trade carries less indicator context than a
manually-coached one, and MFE/MAE need intrabar data we don't have yet.

Groups: **identity** (ids, symbol, direction, strategy, managed_by) · **trade
shape** (qty, entry/exit ts+price, timeframe) · **outcome** (pnl, return_pct,
is_win, hold_minutes, exit_reason, risk_multiple, mfe/mae [future]) · **decision
context** (confidence in/out, setup_quality, gate_mode, risk_reward) · **market
/session** (session, hour/minute ET, htf_trend, entry_trend, consolidating, rsi,
adx, rvol, pressure, iv, delta, dte, spread_pct) · **reasoning** (entry_reasons,
evidence_names, mistakes, lessons) · **learning** (exploration flag) ·
**expansion** (`extra` JSON blob) · **features** (normalized similarity vector).

**Expandability:** new *per-trade* fields (screenshot refs, news, sentiment)
land in `extra` with **no migration**. Structural changes go through the
versioned migration list. `extra` roundtrip is tested.

## 6. Storage design & scalability (100k+ trades)

Hybrid row: the fields the documented queries filter on
(direction, symbol, strategy, setup_quality, market_session, volatility_bucket,
is_win, entry_ts, exploration) are **real indexed columns**; the complete
record is also stored as a JSON `payload` (authoritative on read, so columns can
never drift). Query pattern:

1. **Coarse SQL filter** on indexed columns bounds the candidate set. At 100k
   rows an indexed equality (e.g. `direction='long'`) returns O(thousands).
2. **Fine Python distance pass** ranks only those candidates. A pure-Python
   weighted distance over a few thousand records is sub-second, and it runs on
   demand (never on the hot scan path).

This is why the design holds at 100k+ without a redesign: the expensive step is
always applied to a pre-pruned set, and the pruning keys are indexed.
`SimilarityEngine.find_similar(restrict_direction=True)` (default) is the
load-bearing prune.

**Migrations:** `PRAGMA user_version` + an ordered `_MIGRATIONS` list. Opening a
DB whose version is *newer* than the build supports refuses loudly (tested)
rather than corrupting data — forward-safe across releases.

## 7. Similarity algorithm

`similarity(query, cand) → [0,1]`. Direction is the mandatory anchor (no shared
direction ⇒ 0). Otherwise a weighted average of per-component distances, over
only the components where **both** sides carry data (a feature the query never
captured is "no information", not a mismatch):

| Component | Weight | Distance |
|---|---|---|
| direction | 3.0 | 0 if equal else 1 |
| evidence set | 3.0 | Jaccard distance of `evidence_names` |
| setup_quality | 1.5 | 0/1 |
| htf_trend | 1.5 | 0/1 |
| timeframe | 1.0 | 0/1 |
| market_session | 0.5 | 0/1 |
| numeric features | 2.0 | mean abs diff over shared normalized features |

`similarity = 1 − Σ(w·d)/Σw`. Direction and evidence composition dominate by
design. Numeric features use the fixed `FEATURE_RANGES` so a record's vector is
**stable for all time** — adding trades never silently re-scales old vectors.

`summarize()` aggregates the matched cohort into a `SimilarityResult`: n,
win_rate, avg return/hold/pnl, most-common exit, typical failure mode (dominant
losing exit reason, falling back to dominant mistake tag), the ranked matches,
and the advisory calibrated confidence.

## 8. Confidence calibration (advisory)

Shrinkage blend of the model's own estimate and the historical win rate:

```
w          = n / (n + K)          # K = 20
calibrated = (1−w)·raw + w·(win_rate·100)   # clamped [0,100]
```

Few similar trades ⇒ stays near the model estimate; a large, consistent cohort
moves it meaningfully. Transparent, bounded, sample-aware — and **advisory
only** (Decision A). `SimilarityResult.explain()` renders the human sentence
("79% confident — resembles 43 historical trades with a 79% win rate").

## 9. Experience lifecycle

1. A round trip closes → orchestrator builds the `TradeRecord` and calls
   `journal.record` (unchanged).
2. Immediately after, `experience.record_trade(record, entry_ctx, exit_ctx)`:
   `build_experience` extracts features from the trade + best-effort analysis
   context snapshots, and `ExperienceStore.record` upserts by `trade_id`.
3. On a future setup, a caller builds a query `ExperienceRecord` (entry context,
   no exit) and calls `summarize_for` to get evidence.
4. `ExperienceEngine.stats()` aggregates the store for the future dashboard.

## 10. Known limitations (honest, by design)

- **MFE/MAE are modelled but unpopulated** — they need intrabar tracking the
  delayed, per-cycle data can't provide. A streaming provider or a tick recorder
  fills them later; the fields and roundtrip already exist.
- **`risk_multiple` (R) is unpopulated** — needs the stop premium, not carried
  on `TradeRecord` today.
- **AI trades carry thinner context than manual ones** — the manual/coach path
  captures a rich entry snapshot (rsi/adx/rvol/…); the AI path currently extracts
  from `market_conditions` only. Phase 3 can capture an AI entry snapshot into
  `_TradeMeta` to close the gap symmetrically.
- **Calibration is advisory** — by decision, not oversight.

## 11. Forward plan

- **Phase 3 — DONE (see §12).** Centralized AI snapshot, feature symmetry,
  historical-similarity explanation on recommendations, and the Experience API.
- **Phase 4 — `learning_mode` axis + Exploration.** New orthogonal axis; tagged,
  strictly risk-limited lower-confidence paper trades to maximize learning. The
  `ExperienceRecord.exploration` field, its indexed store column, the
  `learning_mode` snapshot field, and the exploration→record wiring already
  exist; Phase 4 adds the axis to config/runtime + the gate-bypass-with-risk-cap
  behavior.
- **Phase 5 — AI Performance dashboard.** New tab over `/api/experience`
  (`ExperienceEngine.statistics()` + the Similar Trade Viewer). Backend + API are
  done; Phase 5 is the single-file frontend (manually browser-verified).
- **Phase 6 — Strategy discovery infrastructure.** Group experiences by shared
  characteristics (the `extra["snapshot"]` evidence breakdown is the raw
  material) for later profitable-pattern mining. Infra only — no invented
  strategies yet.
- **Deferred data:** MFE/MAE and `risk_multiple` still await intrabar tracking /
  stop-premium capture. Bollinger bands and a full volume-profile histogram are
  stored as None until the engine computes them.

## 12. Phase 3 — integration specifics

**Centralized snapshot (`experience/snapshot.py`).** `build_snapshot(decision,
…)` is the ONE place a decision context becomes a record. It duck-types the
`EngineDecision` (engine types imported only under TYPE_CHECKING) so
`experience/` never gains a runtime dependency on `engine/`. It captures, from
the `EngineDecision` + `TimeframeView` + `GateReport` (+ optional plan/contract):
symbol, timeframe, direction, deterministic score, reasoning, higher-timeframe
trend, RSI/ADX/rvol/pressure/ATR/EMA-stack/MACD-hist/VWAP/supertrend/divergence,
contract DTE/delta/IV/spread, the full per-component **evidence breakdown**, the
gate result + confirmations passed/failed, stop/target/entry/RR, operating &
trading modes, and `learning_mode`. Honesty rule: Bollinger and a full
volume-profile histogram are **not** computed by the engine, so they are stored
as `None`, never invented; a NaN indicator (disabled) also becomes `None`.

**Feature symmetry.** Both the AI entry path (`_scan_symbol` → `_register_meta`,
snapshot stored in `_TradeMeta.entry_context`) and the manual/coach path
(`_capture_context`, now also built by `build_snapshot`) funnel through the one
builder, so a manual trade and an AI trade record equivalent feature quality.
`features._entry_fields` is the single shared extractor both `build_experience`
(a closed trade) and `build_query_record` (a live setup) use.

**Historical-similarity explanation (advisory).** For **tradeable** signals only
(hot-path-conscious), `_attach_historical` computes `ExperienceEngine.
explain_setup(snapshot)` and attaches it to the status payload's per-signal
`historical` block and to the Human-Mode advice notification. It is computed
*after* the deterministic decision and never feeds back into it. Best-effort.

**Experience API (`ExperienceEngine`, no SQL outside the store).** `recent`,
`similar_trades` / `similar_to_snapshot` (→ `SimilarTrade` rows: date, ticker,
tf, direction, outcome, return, confidence, similarity %, failure/success
reason), `statistics` (overview + `by_strategy` + `by_regime` + `by_session` +
`failure_modes` + `success_patterns`), `strategy_statistics`,
`regime_statistics`, `failure_modes`, `success_patterns`, `explain_setup`.
Exposed over `GET /api/experience` and `GET /api/experience/similar?symbol=`.

**Storage v2 (`_migration_2`).** Adds indexed `market_regime` (derived: trend ×
IV volatility) plus `return_pct` / `hold_minutes` columns, backfilled from each
row's authoritative JSON payload (a no-op on an empty DB). Aggregate statistics
are pure SQL COUNT/SUM/AVG over indexed columns — they never deserialize a
payload, which is what keeps `statistics()` fast at 100k+.

**Similarity weightings (unchanged from §7) + calibration.** The distance
weights (direction 3.0, evidence 3.0, setup 1.5, HTF 1.5, timeframe 1.0, session
0.5, numerics 2.0) and the calibration shrinkage (K=20) are unchanged. New
context numerics (ATR, MACD histogram) are deliberately **not** added to the
similarity vector: they are price/scale-dependent and would need per-symbol
normalization to compare meaningfully — they are stored context, not a
similarity axis. Success/failure patterns are grounded only in stored fields
(exit reasons, tagged mistakes, supporting evidence rendered via
`EVIDENCE_LABELS`) — never invented.

**Performance characteristics (measured).** At 20,000 rows: a similarity
`summarize` (direction-pruned candidate set + bounded Python distance pass)
completes in well under the 3s test budget; a SQL `aggregate` completes in under
0.5s. The design scales to 100k+ because the expensive distance pass always runs
on an indexed-pre-filtered candidate set, and aggregates are SQL-only. Advisory
similarity is computed only for *tradeable* signals, never for every scanned
symbol, so it adds nothing to the common scan path.

**Safety (unchanged, reaffirmed).** Nothing in Phase 3 touches the gate, risk
manager, sizing, entries, or exits. The deterministic score is the only trading
input; the calibrated/historical numbers are advisory and surfaced for
explanation only. Every new call site (snapshot build, historical attach,
experience record) is best-effort and cannot break the scan or trading path.
