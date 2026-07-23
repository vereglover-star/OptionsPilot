# ARCHITECTURE-AUDIT-V0.4.2 — engineering report

**Date:** 2026-07-23 · **Branch:** `v3-ui` · **Scope:** architecture audit +
the three approved low-risk improvements. Package: **~10,565 LOC / 55 files**;
tests **~5,288 LOC / 35 files**; scripts ~1,941 LOC. 470 tests green.

**Implemented in V0.4.2 (behavior-preserving, each separately tested):**
Findings **1** (shared `core/sqlite.py` foundation + versioned migrations,
adopted by all five stores — `cache` → `journal` → `orders` → `paper` →
`experience`; +13 tests `test_sqlite.py`), **3** (`ui/server.py` imports hoisted
to module top; the private `orchestrator._WINDOW_DAYS` reach-through promoted to
a public `WINDOW_DAYS`), and **4** (layering-guard `test_architecture.py`,
+6 tests). Findings 2/5/6 remain optional (below). Version 0.4.1 → 0.4.2.

This report is the deliverable of the V0.4.2 audit sprint. Its bias, per the
sprint brief, is **engineering judgment over churn**: findings are ranked by
measurable benefit, and subsystems that are already well-designed are documented
as *leave-unchanged* with a rationale rather than refactored.

---

## 1. Executive summary

**The codebase is in good architectural health.** The layering is clean and
*empirically verified* (not just claimed), persistence is correctly isolated,
route handlers are thin, configuration follows one consistent pattern, and there
is essentially **zero rot** (0 real TODO/FIXME markers, 0 dead modules found).
The strong test convention (one test file per module) and the deterministic /
advisory boundaries introduced in V0.4.x are intact.

The value in this sprint is therefore **small and surgical**, concentrated in one
genuine future-readiness gap and a few low-risk hygiene items:

| # | Finding | Value | Risk | Verdict |
|---|---|---|---|---|
| 1 | **Persistence: no shared store base; only `experience` has schema migrations** | High | Low | **Recommend implementing** |
| 2 | `orchestrator.py` (985 LOC) — cohesive but large; extractable manual-reconciliation cluster | Med | Med | Stage; optional |
| 3 | `ui/server.py` (994 LOC) — well-organized, but ~20 scattered in-function imports incl. a **private** `orchestrator._WINDOW_DAYS` | Med | Low | Recommend (small) |
| 4 | No **layering-guard test** to prevent dependency regressions | Med | Low | Recommend (cheap) |
| 5 | `core → config` inverted import (`logging_setup`) | Low | Low | Document; optional |
| 6 | `_capture_context_for_symbol` bypasses the centralized `build_snapshot` | Low | Low | Optional tidy |

**Nothing here is urgent, and nothing changes user-visible behavior.** Items 1
and 4 are the ones worth doing before 0.5.0; the rest are optional hygiene.

---

## 2. Architecture assessment

### 2.1 Verified dependency graph

Extracted from actual `from optionspilot.X import` statements across every
subsystem. The layering the docs claim is **real**:

```
                    (pure, no I/O)
      analysis  ───▶ core
         ▲            ▲
         │            │
       engine ───▶ core, config, analysis        (never imports broker/risk/ui)
         ▲
   risk ─┤────▶ core, config                     (never imports broker)
         │
 broker ─┤────▶ core, config                     (never imports ui/engine)
         │
journal ─┤────▶ core
learning ┤────▶ core, journal, engine(weights)
experience────▶ core, engine (TYPE_CHECKING only)  ← no runtime engine dep
         │
backtest ┤────▶ engine+risk+broker+journal (parallel driver, like orchestrator)
coach ───┤────▶ core                              (self-contained, deterministic)
notify ──┤────▶ core, config
         │
orchestrator ─▶ composes engine+risk+broker+journal+coach+experience+notify
         │
        ui ────▶ orchestrator + read models (composition root)
```

**Assessment: strong.** No import points "up" the stack. The two intended
composition roots (`orchestrator`, `ui`) are the only broad importers, which is
correct. `experience` depending on `engine` **only** under `TYPE_CHECKING`
(`snapshot.py:30`) means the AI-memory subsystem carries no runtime coupling to
trading internals — exactly the isolation the sprint asks for.

### 2.2 Persistence isolation — verified

Raw `sqlite3` / `.execute(` appears in **exactly five** modules —
`journal/journal.py`, `data/cache.py`, `broker/paper.py`, `broker/orders.py`,
`experience/store.py`. **Zero** SQL in `ui/` or `orchestrator.py`. The sprint's
"no SQL outside the persistence layer" rule already holds.

### 2.3 API layer — verified

`ui/server.py` has 32 route handlers in `create_app`, and they are **thin
delegators** to named `UIServer` methods (`status_payload`, `candles_payload`,
`chain_payload`, `place_order`, …). The sprint's "no business logic in route
handlers" concern is largely already satisfied — business logic lives in the
service class, not the routes.

### 2.4 Configuration — verified coherent

`config/settings.py` = one pydantic `_Section` base with per-area subclasses
(`DataConfig`, `EngineConfig`, `RiskConfig`, …) composed into `AppConfig`;
`config/runtime.py` = the in-app-editable overlay (`RuntimeSettings.apply` /
`_apply_mode`). Two layers, one pattern each, no fragmentation. Adding a future
axis (e.g. `learning_mode`) is a field on `EngineConfig` + an overlay key — the
pattern scales without redesign.

---

## 3. Module boundary review (Phase 2)

Each subsystem answers one question, confirmed by reading its public surface:

| Subsystem | Responsibility | Verdict |
|---|---|---|
| `analysis/` | Pure indicators/structure/SMC math | ✅ single, pure |
| `data/` | Fetch + cache market data | ✅ |
| `engine/` | Score a setup → is it a valid trade? | ✅ |
| `risk/` | Protect capital (gate every entry) | ✅ |
| `broker/` | Execute + persist paper positions/orders | ✅ |
| `journal/` | Record what happened | ✅ |
| `learning/` | Tune evidence weights from the journal | ✅ |
| `experience/` | Remember & compare historical trades (advisory) | ✅ |
| `coach/` | Explain a closed trade (deterministic) | ✅ |
| `backtest/` | Replay the live stack over history | ✅ |
| `notify/` | Emit toasts/emails | ✅ |
| `orchestrator.py` | Compose one scan cycle | ⚠️ large but cohesive |
| `ui/` | Serve the app + read models | ⚠️ large but organized |

Only the two composition roots carry multiple *sub*-responsibilities, which is
inherent to their job. No subsystem owns unrelated concerns.

---

## 4. Technical debt inventory (Phases 3–5, 13)

**Debt markers:** 0 real `TODO`/`FIXME`/`XXX`/`HACK` in source (the 3 grep hits
are ticker symbols in `data_assets/symbols.csv`). **Dead code:** none found —
every module is imported and reachable. **Commented-out code:** none material.

### 4.1 Finding 1 (HIGH) — persistence has no shared base or uniform migrations

All five stores independently reimplement the same boilerplate
(`sqlite3.connect(check_same_thread=False)` → `executescript(_SCHEMA)` →
`commit()`), and only `experience/store.py` has a real **`PRAGMA user_version`
migration framework**. `journal.db` (the *system of record*), `paper.db`, and
`orders.db` rely on `CREATE TABLE IF NOT EXISTS` with **no versioned path to
evolve a schema** — the day a `TradeRecord` field changes, there is no
migration mechanism. `WAL` is set only in `cache` and `experience`.

- **Engineering reason:** eliminates ~5× duplicated connection code; gives the
  system-of-record journal the schema-evolution capability it currently lacks;
  standardizes WAL/threading. Directly enables the sprint's "future databases
  (Replay, Live Broker, Analytics) follow the same architecture."
- **Shape (recommended, not yet implemented):** a small
  `optionspilot/core/sqlite.py` (or `data/sqlite.py`) with `connect(path)` (mkdir
  + `check_same_thread=False` + WAL) and `run_migrations(conn, [fn, …])` reusing
  `experience/store.py`'s proven approach. Each existing store's current schema
  becomes its **migration 1**, so the change is byte-for-byte behavior-preserving
  on existing databases.
- **Risk:** touches data. Mitigated by: migration 1 == today's exact `_SCHEMA`;
  no column changes; keep each store's public API identical; 454 tests + new
  round-trip/restart tests must stay green.

### 4.2 Finding 3 (MED, low-risk) — scattered imports + a private-symbol leak in `ui/server.py`

~20 imports are deferred into method/route bodies (`server.py:257–940`). A few
are justified (heavy/optional deps, CLI-style laziness), but most are stable
core imports that belong at module top, and one is a genuine coupling smell:
`from optionspilot.orchestrator import _WINDOW_DAYS` (`server.py:260`) reaches
into a **private** module constant of a lower layer.

- **Engineering reason:** hidden coupling to a private symbol; scattered imports
  make dependencies hard to see and slow nothing meaningfully (uvicorn already
  imported the world). Hoisting them and promoting `_WINDOW_DAYS` to a public
  location (e.g. `core/models` alongside `Timeframe`, or a public
  `orchestrator.WINDOW_DAYS`) removes the private reach-through.
- **Risk:** trivial; pure import reorganization.

### 4.3 Finding 5 (LOW) — `core → config` inverted import

`core/logging_setup.py:14` imports `LoggingConfig` from `config.settings`, while
`config.settings` imports `core.models` lazily (`settings.py:40`) specifically to
avoid the cycle. No runtime cycle exists (config's side is deferred), but a
base-layer module importing config is a minor inversion. Optional fix: have
`setup_logging` accept plain primitives (level, dir, format) instead of the
pydantic type, so `core` stops importing `config`.

### 4.4 Finding 6 (LOW) — one snapshot bypass

`orchestrator._capture_context_for_symbol` (`orchestrator.py:635`) builds a
minimal exit-context dict by hand instead of going through the centralized
`build_snapshot`. It's a deliberate light subset (exit only needs
spot/confidence/direction for the coach), so this is a small consistency nit, not
a duplication of logic. Optional.

### 4.5 Duplication scan (Phase 5) — essentially clean

Snapshot generation is already centralized (`experience/snapshot.py`, V0.4.1);
feature extraction is centralized (`features._entry_fields`); serialization,
config parsing, and logging each have one home. The only residual is 4.4 above
and the connection boilerplate in 4.1. **No consolidation needed beyond
Finding 1.**

---

## 5. Performance observations (Phase 10) — no action required

- **Hot path = the scan cycle.** Already optimized and documented (~4.5s cold /
  ~0.1s warm for 5 symbols) via `CachedProvider`, parallel candle fetch, and
  per-`(symbol,timeframe)` view memoization on a data fingerprint
  (`views.py:_fingerprint`). Bounded caches (`MEM_CACHE_MAX`, the view memo).
- **Experience similarity** (potential 100k concern): direction-pruned candidate
  set + bounded Python distance pass; **measured <3s at 20k rows**, SQL
  aggregates **<0.5s**. Scales to 100k as designed.
- **Watch item (not a bug):** `TradeJournal.stats()`/`all()` deserialize every
  row; fine at today's trade counts, but at ~100k journaled trades it becomes an
  O(n) full scan per call. The Experience Engine already solved the analogous
  problem with SQL `overview()`; the journal could adopt the same pattern later.
- No memory-growth risks found; caches are bounded and the view memo is keyed.

---

## 6. Testing assessment (Phase 11)

Strong: 454 tests, one test file per module, boundary-condition discipline
(missing data / restart-persistence / rejection paths). Largest suites track the
riskiest areas (`test_ui_server` 524, `test_experience` 475).

**Blind spots worth closing:**
1. **No layering-guard test.** The clean dependency graph (§2.1) is currently
   maintained by discipline alone; nothing fails if a future change makes
   `engine` import `broker` or `experience` import `broker`. A tiny AST/import
   test asserting the allowed-imports matrix is cheap, high-leverage insurance
   for the 6-month horizon. **Recommend (Finding 4).**
2. **No journal-migration test** — because the journal has no migrations
   (Finding 1). Adding the store base closes this automatically.
3. Frontend remains covered only by the separate `browser_check`/`chart_check`
   scripts, not pytest — already documented and out of this sprint's scope.

---

## 7. Documentation assessment (Phase 12)

Documentation is unusually complete and *currently in sync* (the doc-consistency
checker `scripts/check_docs.py` enforces test-count and version agreement and
passes). `AI_HANDOFF`, `ARCHITECTURE`, `MODULES`, `PROJECT_STATE/STATUS`,
`NEXT_SESSION`, `CHANGELOG`, and the V0.4 roadmap were all updated through
V0.4.1. No stale architecture docs found. This report is the only addition.

**One doc improvement recommended:** add a short "Layering & allowed imports"
section to `ARCHITECTURE.md` that the guard test (Finding 4) enforces, so the
rule is both written and executable.

---

## 8. Scalability & future-readiness (Phase 14)

| Future capability | Ready today? | Why / what's needed |
|---|---|---|
| Streaming market data | ✅ | `MarketDataProvider` interface abstracts the source; swap the impl. |
| Professional options chain | ✅ | Same provider seam + `OptionContract` model. |
| Broker adapters | ✅ (by design) | `Broker` interface + `registry.py` stubs behind the double-gate; slots in without touching engine/risk/ui. |
| Replay mode | Mostly | Reuses the drawing engine + `backtest` stack; new store should adopt the **SqliteStore base (Finding 1)**. |
| Strategy discovery | ✅ | Raw material already stored (`extra["snapshot"]` evidence breakdown). |
| `learning_mode` axis | ✅ | Config pattern + `ExperienceRecord.exploration`/snapshot `learning_mode` already modelled. |
| Multi-provider / plugins | Partial | Provider seam exists; a formal plugin registry is future work, not blocked. |
| Analytics DB | Partial | Should follow the SqliteStore base (Finding 1). |

**Verdict:** the seams for the big future features already exist. The single
architectural investment that materially improves readiness is the **uniform
persistence/migration base**.

---

## 9. What to explicitly LEAVE UNCHANGED (and why)

Per the brief, these are well-designed and should **not** be refactored:

- **The dependency layering** (§2.1) — clean and verified; change only guarded by
  a test, never "reorganized."
- **`config/` two-layer design** — one consistent pattern; scales by addition.
- **`analysis/` purity, `risk/` gatekeeper monopoly, `broker/` managed_by
  discipline, `experience/` advisory isolation** — these are the load-bearing
  invariants; the audit confirms they hold.
- **Route-handler thinness** in `ui/server.py` — already delegates; do not
  "extract controllers" for its own sake.
- **`coach/`, `journal/`, `learning/`, `notify/`, `backtest/`** — small,
  single-responsibility, well-tested; no change warranted.
- **The deterministic-vs-advisory boundary** — untouched by this sprint.

---

## 10. Risks of acting on the recommendations

- **Finding 1 (persistence base)** touches databases. The only safe path is:
  migration 1 == the *exact* current `_SCHEMA` per store, no column changes,
  identical public APIs, and new restart/round-trip tests proving existing
  `data/*.db` files open unchanged. Done that way it is behavior-preserving; done
  carelessly it risks the user's real paper account/journal — so it must be
  staged store-by-store with tests, starting with the lowest-risk (`cache`, which
  is disposable) to validate the base, then `journal`.
- **Finding 2 (orchestrator extraction)** risks the documented "second code path"
  trap (`CLAUDE.md`). Only safe as a *pure move* of existing methods into a
  collaborator the orchestrator owns and calls, with tests unchanged. Recommend
  deferring unless the file keeps growing.
- All other findings are trivial/low-risk.

---

## 11. Recommendations before 0.5.0 (prioritized)

1. **[Do] Shared `SqliteStore` base + migrations**, adopted incrementally:
   `cache` (validate) → `journal` (the important one) → `paper`/`orders`.
   Behavior-preserving; unlocks schema evolution for Replay/Analytics DBs.
2. **[Do] Layering-guard test** (Finding 4) + a short "allowed imports" section in
   `ARCHITECTURE.md`. Cheap, protects the codebase's best property.
3. **[Do, small] Hoist `ui/server.py` imports** and remove the private
   `orchestrator._WINDOW_DAYS` reach-through (Finding 3).
4. **[Optional] Extract `ManualTradeReconciler`** from `orchestrator.py` only if
   it continues to grow; move `_TradeMeta`/`_MetaStore`/`_JsonStore` to an
   `orchestrator_state.py` if so.
5. **[Optional] De-invert `core→config`** in `logging_setup` (Finding 5).
6. **[Later] Journal `overview()` SQL path** mirroring the Experience Engine, when
   journaled-trade counts approach five figures (Finding, §5).

## 12. Remaining technical debt after this plan

If Findings 1–4 are implemented, the remaining known debt is: the journal's
O(n) stats path (deferred, §5/#6), the `core→config` inversion (cosmetic), and
the standing frontend-test gap (out of scope, documented). None block 0.5.0.

---

## 13. Success-criteria check

- Behaves identically — ✅ (audit made no changes; recommendations are all
  behavior-preserving).
- Easier to understand / navigable — ✅ once §11.2 (guard test + written layering)
  lands; the graph is already clean.
- Clear responsibilities — ✅ confirmed (§3).
- Reduced technical debt — achieved by §11.1 (the one real gap).
- Stronger architecture / future-ready — the persistence base (§11.1) is the
  single highest-leverage step; every other seam is already in place.
