# TRADING_INTELLIGENCE.md — the Trading Intelligence Engine

**Status:** V0.6.0, built 2026-07-28. Design document and reference for
`optionspilot/intelligence/`.

This is the analytical brain of OptionsPilot: one layer that turns everything
the system already records about completed trades into structured,
evidence-backed insight, which every other part of the application consumes
rather than recomputes.

---

## 1. Why this exists

Before V0.6.0 the app could already tell a trader a great deal about themselves,
and it told them from four unrelated places:

| Where | What it knew | What it couldn't do |
|---|---|---|
| `journal.db` | every closed round trip, its reasoning chain | no analysis beyond win rate / expectancy / profit factor |
| `experience.db` | the rich per-trade context (IV, delta, DTE, regime, session, indicators) | similarity search for a *live* setup only — nothing retrospective |
| `coach/*.json` | the process review of each manual trade | one trade at a time; the dashboard aggregated reviews, not trades |
| `learning/weights.json` | which evidence types have paid off | tunes the scorer; says nothing to the human |

Four stores, four aggregation paths, and no answer at all to the questions a
trader actually asks — *what am I good at, what keeps costing me money, am I
improving, what should I learn next.* Worse, each new screen that wanted an
answer computed its own, which is the failure mode this codebase has already
paid for twice (`data/health.py` in V0.5.3, the settings ranking in V0.5.7):
**two objects tracking one fact will drift, and the drift hides bugs.**

V0.6.0 collapses that into one pipeline. An insight is generated once and
projected everywhere.

---

## 2. Architecture

```
    journal.db          experience.db          data/coach/*.json
  (TradeRecord)      (ExperienceRecord)         (CoachReview)
        └───────────────────┬───────────────────────┘
                            ▼
                 intelligence/facts.py
              build_facts() → tuple[TradeFact]      ← the ONE join
                            │
                            ▼
             intelligence/engine.py :: TradingIntelligence
                            │
   ┌──────────┬─────────────┼──────────┬───────────┬────────────┐
   ▼          ▼             ▼          ▼           ▼            ▼
Performance Behavior    Pattern     Risk     Confidence     Goal
  Engine     Engine     Engine  Intelligence   Engine       Engine
   │          │             │          │           │            │
   └──────────┴──────┬──────┴──────────┴───────────┴────────────┘
                     ▼
        Recommendation · Curriculum · Timeline · Achievement · Report
                     │
                     ▼
            IntelligenceSnapshot          ← one immutable analysis
                     │
   ┌──────────┬──────┴──────┬────────────┬──────────────────────┐
   ▼          ▼             ▼            ▼                      ▼
Dashboard   Coach       Journal      Learning              Reports
                                                  (future: mobile, cloud, ML)
```

### Layering

`intelligence/` imports **`core` only** — verified by
`tests/test_architecture.py::test_key_isolation_invariants`. It does not import
`journal`, `experience` or `coach`; `facts.py` reads those records
*structurally* (duck-typed), which is what keeps the intelligence layer **below**
the coach in the dependency graph.

That direction is the milestone's central architectural claim: it is what lets
the AI Coach become a presentation layer over this engine rather than a parallel
analysis path. If `intelligence/` ever imports `coach/`, the dependency inverts
and that becomes impossible.

The composition happens in `orchestrator.py` — the only object that already owns
all three sources — via `_intelligence_facts()` and `_intelligence_fingerprint()`.

### Module map

| Module | Responsibility |
|---|---|
| `models.py` | The shared vocabulary. `Evidence`, `Metric`, `BehaviorFinding`, `Pattern`, `ScoreCard`, `Goal`, `Recommendation`, `Report`, `IntelligenceSnapshot`. Every one serialises via `to_dict()`. |
| `stats.py` | Every formula in the layer. Expectancy, profit factor, drawdown, Wilson intervals, two-proportion tests, trend slopes, confidence bands. The only module allowed to contain arithmetic. |
| `facts.py` | The one join. `TradeFact` + `build_facts()`. Never raises, never invents. |
| `windows.py` | Period bucketing (day/week/month/quarter/year) and named analysis windows (`last_30d`, `last_20_trades`, …). All calendar decisions in America/New_York. |
| `performance.py` | The **metric registry** — the addressable vocabulary of the whole layer. |
| `behavior.py` | 22 behavioural detectors + the corrective action for each. |
| `patterns.py` | Automatic edge discovery across ~19 declared dimensions, with false-discovery correction. |
| `risk.py` | Backward-looking risk analysis (drawdown, tails, concentration, sizing dispersion). Never gates a trade. |
| `confidence.py` | The eight composite scores, each of which explains itself down to the component that moved it. |
| `goals.py` | Measurable commitments against metric keys, with computed progress. |
| `curriculum.py` | 16 lessons, each selected only because a measured weakness triggered it. |
| `recommend.py` | Derives, prices and ranks the action list. Contains no generic advice. |
| `timeline.py` | The dated improvement narrative. |
| `achievements.py` | 10 achievements, none earnable by a single trade or by luck. |
| `reports.py` | Weekly and monthly coaching reports, in prose. |
| `engine.py` | `TradingIntelligence` — the façade, the cache, the per-trade projection. |
| `store.py` | Goal persistence. The only thing this layer stores. |

---

## 3. Data flow

### 3.1 `TradeFact` — the normalised unit

`build_facts()` joins the three sources once, preferring the experience row
(richest), falling back to the journal row for trades predating the Experience
Engine, and attaching the coach review where one exists.

Three properties are load-bearing:

* **Never invent.** A field the sources cannot supply stays `None`. Every engine
  treats `None` as "no information on this axis" rather than substituting a
  default — a fabricated `0.0` delta would quietly become a "lottery ticket"
  finding.
* **Never raise.** These sources include a user-editable JSON directory and a
  SQLite payload written by an older build. Unparseable records are skipped and
  counted in `FactSet.skipped`; an unreadable review costs one review, not the
  dashboard.
* **Tri-state process fields.** `had_stop` is `True` / `False` / `None`. `False`
  means "observed, and there was no stop". `None` means "nobody reviewed this
  trade". Collapsing them would accuse an unreviewed trader of trading without
  stops.

Order-behaviour observations (`had_stop`, `widened_stop`, `had_target`) are
**read from the coach's `during` findings**, not re-derived. `TradeCoach`
already inspected the real OrderManager history and wrote the answer into a
Finding with a stable `check` name. Re-deriving would need the order history the
intelligence layer deliberately does not depend on, and would be a second place
the same fact is computed.

### 3.2 The pipeline

`TradingIntelligence.analyze(factset)` is pure: same facts in, same snapshot out
(bar the `generated` timestamp). It runs:

1. `PerformanceEngine` — the metric registry, per named window and per calendar
   period, plus month-over-month trends.
2. `BehaviorEngine` over the **recent window** (`last_50_trades`), with the
   previous equally-sized window supplied for trend direction.
3. `PatternEngine` over **all** history.
4. `RiskIntelligence`, `ConfidenceEngine`, `GoalEngine`, `CurriculumEngine`,
   `RecommendationEngine`, `TimelineEngine`, `AchievementEngine`, `ReportEngine`.

Behaviour is windowed and patterns are not, deliberately: a habit the trader has
stopped should drop off the action plan as newer clean trades push it out, while
an edge should be measured over everything available.

---

## 4. The rules that keep it honest

These are the whole point of the design. Each was written because the naive
alternative produces something that looks authoritative and is wrong.

### 4.1 Insufficient evidence is a first-class answer

A metric is `None`, not `0`. A score is `None`, not `50`. A behaviour is
`assessable=False` **with the reason stated**, not `detected=False` — because
"not detected" is a claim, and it would be an unearned one.

`hesitation` is permanently unassessable and says so: measuring it needs the
latency between a setup appearing and the entry being taken (not recorded
anywhere) plus the setups skipped entirely (which by definition produce no
trade). A detector that guessed at it from hold times would be inventing a
psychological claim out of unrelated numbers.

### 4.2 Nothing is stated without evidence

Every conclusion-shaped object carries an `evidence` tuple of measured
`Evidence` items — a label, the number actually computed, the sample it came
from, and up to 25 of the exact `trade_ids` behind it. That tuple is what the
UI's "Why?" disclosure shows. A finding with no `trade_ids` is a bug.

The citation cap takes the **most recent** occurrences, not the first: the
journal's per-trade view can only flag a trade as evidence if the finding names
it, and users open recent trades far more often than their fiftieth-oldest.

### 4.3 A minimum coverage floor on every composite score

`confidence.MIN_COVERAGE = 0.35`. Without it, a trader who has never had a trade
reviewed scored **Discipline 100/100, grade A** — because the one component that
needs no review (revenge trading, which reads only timestamps) came back clean,
and 20% coverage was enough to average. An A earned by an absence of data is the
most flattering lie this system could tell, and a "measured over 20% of the
intended inputs" caveat under a large green A does not undo it.

### 4.4 Multiplicity is corrected for, not merely acknowledged

Roughly seventy bucket tests run per pattern analysis. At a raw p≤0.20 threshold
that produces about fourteen "patterns" from pure noise — measured at **thirteen**
on 100 uniformly random trades (`scripts/intelligence_benchmark.py`). Every
candidate now passes a **Benjamini–Hochberg** false-discovery correction over the
whole run at `FDR_Q = 0.10`. The same random input now yields ≤3, and a genuinely
concentrated edge still comes through.

Bonferroni was considered and rejected: over seventy tests it demands p<0.0007,
which at the sample sizes a discretionary trader actually produces would report
nothing, ever. A system that can never find a pattern is not more honest, just
useless.

### 4.5 A dimension must describe a choice, never a consequence

Exit reason **was** a dimension, and it produced the strongest-looking findings
in the system:

> *How it ended — stop loss: 0% win rate over 51 trades against 100% elsewhere,
> p<0.0001.*

True, and circular. A trade that ended at its stop is a losing trade **by
definition**, so bucketing on the exit and measuring the win rate can only
rediscover the definition — with a crushing p-value, at the top of the ranking,
pushing every real finding down. It also generated the recommendation *"stop
taking stop-loss trades"*.

The rule: a dimension must describe a choice made **before or during** the trade
(symbol, time of day, strike delta, DTE, size, hold time), never a consequence
of how it turned out.

### 4.6 Impact is a historical counterfactual, and says so

Every `Impact` recomputes the trader's expectancy over **the trades that actually
happened**, with the affected trades removed, and `Impact.basis` states the
assumption in words. The UI renders that basis alongside the number so the claim
cannot be read as a forecast.

The basis also names its window. Behavioural analysis runs over the recent
window, so its baseline is *not* the dashboard's lifetime expectancy — saying
which is which is the difference between a comparison the user can check and two
numbers that look like a contradiction.

### 4.7 Both sides of every comparison must clear the sample floor

"Your average risk has fallen 18% since May" requires a May with enough trades
to have an average worth comparing. `windows.previous_and_latest()` and
`reports.MIN_PERIOD_TRADES` are the gates; a four-trade week is reported as
explicitly provisional and never compared against a full month.

### 4.8 A streak counts only trades that could have broken it

"27 consecutive trades without a stop-loss violation" is a lie if twenty of them
were never reviewed. The streak counters walk only trades whose stop behaviour
was actually observed, and the detail line says so.

### 4.9 Infinity and NaN never reach a payload

Profit factor is legitimately infinite for a period with no losing trades.
`json.dumps` emits `Infinity`, which is not valid JSON and breaks a browser
parse — so `models._finite()` converts it to `None` on the way out.

Related, and found by test: `inf` compared against `inf` produces a NaN
percentage, and both the timeline and the report writer shipped *"your profit
factor has declined nan% since March"* before `stats.comparable()` existed.
Every narrative comparison gates on it.

### 4.10 A sign change is stated, not expressed as a percentage

"Expectancy declined 114%" is arithmetically true and tells the trader nothing.
Crossing zero produces *"Your expectancy turned negative in June 2026"* instead.

---

## 5. The metric registry

`performance.METRIC_SPECS` is a **public contract**: goals target metrics by key,
scorecards cite them by key, the report writer looks them up by key, and the UI
renders them by key. Adding a key is safe; renaming one is a breaking change.

Each spec declares `(label, unit, higher_is_better, explanation)`. `unit` is what
lets the UI format a value without knowing that `win_rate` is a percentage and
`avg_r` is not; `higher_is_better` is what lets a goal, a trend and a score all
agree on which direction is progress without each restating it.

38 metrics, covering outcome (expectancy, profit factor, payoff ratio, R
multiple), risk (max drawdown, recovery factor, worst day), shape (hold times,
hold asymmetry, position size, sizing consistency) and process (stop discipline,
plan rate, mistake rate, clean-trade rate, average process score).

**`consistency` deserves a note.** It is measured over *periods*, not individual
trades, via a ladder: weekly totals if there are ≥3 populated weeks, else daily
totals if ≥5 days, else per-trade P/L. Per-trade option results vary enormously
for everybody, so scoring their spread would hand every trader the same ~20 and
say nothing. What a trader means by "consistent" is that their **weeks** look
alike.

---

## 6. Behaviour detection

22 behaviours, each with a `BehaviorSpec` declaring what it is, how serious it is
when real, **what data answering it requires** (quoted back when it must
decline), and the corrective action.

Four rules every detector obeys:

1. It measures something, and cites the trades it counted.
2. It states what it could not measure, with the reason.
3. It prices the habit, where the trades are separable.
4. It cannot be triggered by one trade (`MIN_OCCURRENCES = 2`), and confidence is
   capped until a habit repeats.

Relationship to the coach's `MISTAKES` taxonomy: the coach tags **one trade** at
review time; this engine tests **a population** for a repeated tendency. Several
detectors read those tags as one input among several — `chasing` also reads RSI
at entry, `moving_stops` also reads the observed order history. They are
different questions at different altitudes, which is why the intelligence layer
does not import the coach: tags arrive as data, on the fact.

Two detectors state a *comparison* rather than a count — `tilt_after_loss` and
`overconfidence_after_wins` — and both require a material effect plus a real
cohort before reporting. Overconfidence needs **both** halves to be true (sizing
up **and** underperforming); sizing up after wins and then doing fine is not a
problem worth naming.

`_build_context()` precomputes the sequence-sensitive views all detectors share.
`prior_loss_gap` uses a sorted list and a binary search rather than a nested
scan, so a 50,000-trade history costs O(n log n) instead of O(n²) — measured, not
assumed.

---

## 7. Performance

Measured on the development machine with `scripts/intelligence_benchmark.py`:

| Trades | Full analysis | Per trade | Payload |
|---:|---:|---:|---:|
| 100 | 13 ms | 129 µs | 76 KB |
| 1,000 | 70 ms | 70 µs | 84 KB |
| 5,000 | 256 ms | 51 µs | 114 KB |
| 20,000 | 1,080 ms | 54 µs | 232 KB |
| 50,000 | 2,901 ms | 58 µs | 467 KB |

Per-trade cost is **flat** from 1k to 50k, which is the property that matters:
the pipeline is sub-quadratic. Per-engine at 20,000 trades: PatternEngine 47%,
PerformanceEngine 41%, everything else under 5% each.

Operational decisions:

* **Nothing is computed at construction.** Building a `TradingIntelligence`
  touches no data, so the orchestrator owns one unconditionally without
  lengthening startup.
* **The cache key is a fingerprint supplied by the caller**
  (`journal.revision:experience.revision`), not a TTL. The composition root knows
  what changed; this layer does not, and a TTL would either recompute needlessly
  or serve a stale verdict about a trade the user just closed. Measured: four
  cached reads cost 0.001% of one analysis.
* **A failed analysis returns an empty snapshot, never an exception.** This is
  advisory intelligence attached to an application that manages positions. A
  malformed review file must cost a dashboard panel, not a session. A failure is
  never cached as though it were an answer.
* **`refresh_in_background()`** exists for callers that want to warm the cache
  off the request path.

---

## 8. API surface

| Route | Purpose |
|---|---|
| `GET /api/intelligence` | The complete analysis. `?full=false` drops `periods` (the only unbounded section). |
| `GET /api/intelligence/summary` | The dashboard projection: headline metrics, scores, top actions, goals, achievements, timeline, lessons, latest report. |
| `GET /api/intelligence/trade/{id}` | Per-trade projection: patterns it belongs to, habits it is evidence for, its percentile, the comparable cohort. |
| `GET /api/intelligence/reports` | Weekly + monthly reports. |
| `GET /api/intelligence/goals` | Active goals with progress, suggested templates, and the metric vocabulary a custom goal may target. |
| `POST /api/intelligence/goals` | Create/replace a goal. 422 with the precise reason if it could never evaluate. |
| `DELETE /api/intelligence/goals/{id}` | Remove a goal. 404 if unknown. |

Also extended: `GET /api/coach` gains an `intelligence` block (so the Coach tab
costs one request and one analysis), and `GET /api/journal` gains a `findings`
map (`trade_id → [finding labels]`) so the list can badge rows without a request
per row.

Period series are trimmed **at the transport boundary** (`PERIOD_LIMITS` in
`ui/server.py`), not in the engine — nothing in `intelligence/` has to know a UI
exists.

---

## 9. UI integration

No new tab. The engine surfaces inside the four tabs the milestone named:

* **Dashboard** — `#intel-panel`: eight score cards, the ranked action list,
  risk observations, goal progress, achievements, and the improvement timeline.
  Polls every 60 s, and only while the tab is actually visible.
* **Coach** — `#intel-coach`: measured behaviour (with detected, clean, and
  **unassessable-with-reason** sections), discovered patterns, and the coaching
  reports. Sits beside the existing per-review scorecard rather than replacing
  it; the two answer different questions over different windows and the tab
  labels which is which.
* **Journal** — a badge on any row that is evidence for a finding, and the
  per-trade analysis lazily loaded on first expand (a 200-row journal must not
  issue 200 requests).
* **Learning** — lesson recommendations, each showing *why this lesson*, *which
  statistic triggered it*, *what problem it solves* and *what it is worth*.

Explainability uses native `<details>`/`<summary>`, so "Why?" works with no JS of
our own and gets correct keyboard and screen-reader behaviour for free.

Two rendering traps worth knowing about:

* `.notif .b` is `white-space: pre-line`, so a template literal broken across
  source lines renders those breaks. Impact strings stay on one line.
* Panel headings and `.sub` sub-headings are `text-transform: uppercase`, and
  Playwright's `inner_text` returns **rendered** text. Every assertion in
  `scripts/intelligence_check.py` is case-insensitive for that reason.

---

## 10. Testing

* **377 new pytest tests** across nine files (`tests/test_intelligence_*.py`),
  taking the suite from 1,468 to 1,845.
* **`scripts/intelligence_check.py`** — 54 checks in a real headless browser
  against a seeded, deliberately flawed 90-trade history. It asserts what is on
  screen, not what is in a payload: that the behaviour panel contains the finding
  the API reported, that "Why?" opens and reveals measured evidence, that
  unassessable behaviours appear *with* their reason, that no `undefined`, `null`
  or `NaN` reaches the page. Wired into `scripts/verify.ps1`.
* **`scripts/intelligence_benchmark.py`** — scaling, per-engine cost, payload
  size and cache effectiveness. Not a pytest test because its absolute numbers
  are machine-dependent; `TestScale` holds the portable assertions.

Edge cases covered explicitly: zero trades, one trade, all wins, all losses, all
flat, every trade at the same instant, no reviews at all, no indicator context at
all, enormous outliers, corrupt records of every shape, 5,000-trade scaling and
non-quadratic growth.

---

## 11. Design decisions worth restating

**Why not store the analysis?** Because two objects tracking one fact drift, and
here the drift would take the form of a dashboard showing yesterday's verdict
about today's trades. Recomputing costs milliseconds; being wrong costs trust.
The only thing persisted is what the *user* decided: their goals.

**Why is `intelligence/` below `coach/`?** So the coach can become a presentation
layer over it. The reverse dependency would make that impossible forever.

**Why isn't this consulted before a trade?** `risk/manager.py` is the gate; this
is analysis. Merging them would put a heavyweight statistical pass on the trading
hot path and would tempt someone to let an analysis result block a trade — a
trading-behaviour change this milestone deliberately does not make.

**Why deterministic rather than ML?** Same reason as the scorer, the gate and the
coach: auditability and offline operation. Every number here can be traced to the
trades that produced it, which is what makes "Why?" possible at all.

---

## 12. Limitations

Stated plainly, because the engine's own contract is to say what it cannot do.

1. **Hesitation and missed setups are unmeasurable.** Nothing records the latency
   between a signal and an entry, and a skipped setup produces no trade. The
   engine declines rather than guessing.
2. **MFE/MAE are not available.** `ExperienceRecord` models them, but they need
   intrabar data the system does not have on delayed per-cycle quotes. Several
   exit-quality questions ("how much of the move did you capture?") stay
   unanswerable until a tick recorder or streaming provider exists.
3. **R multiples depend on a recorded protective stop.** A trader who never
   places one gets `avg_r = None` — correct, but it means the most useful risk
   metric is missing exactly for the traders who most need it.
4. **Behavioural detection is inference from observable behaviour, not
   mind-reading.** "Revenge trading" is an entry inside fifteen minutes of a
   losing exit. That is a real, measurable, and *named* proxy — but it is a proxy.
5. **Patterns are correlational.** The engine reports that a bucket differs from
   the trader's baseline, with a significance test and a false-discovery
   correction. It does not establish cause, and a trader who concentrates on a
   discovered strength may find it was regime, not skill.
6. **Composite score weights are judgements.** The anchors in `confidence.py`
   (a 40% stop-discipline rate is bad; a payoff ratio of 3.0 is exceptional) are
   trading judgements, not statistics. They are the one place an external
   standard is applied rather than the trader's own baseline, and they are
   documented at each call site.
7. **No cross-account or peer comparison.** Everything is measured against the
   trader's own history. There is no notion of "good for a trader at your stage".
8. **The analysis is of paper trades.** Slippage, partial fills and the
   psychology of real money are not in the data, and the engine cannot know it.

---

## 13. Extension points

* **A new metric** — add a `METRIC_SPECS` entry and compute it in
  `performance.compute()`. Goals, scorecards, the timeline and the UI pick it up
  automatically.
* **A new behaviour** — add a `BehaviorSpec` (including its `action`) and a
  detector function to `DETECTORS`. `tests/test_intelligence_behavior.py`'s
  universal-guarantee tests apply to it immediately.
* **A new pattern dimension** — add a `Dimension` to `DIMENSIONS`. Check it
  describes a **choice**, not a consequence (§4.5).
* **A new lesson** — add a `Lesson` to `CURRICULUM` with at least one trigger.
* **A new achievement** — add a `_Spec` to `achievements.SPECS`. It must not be
  earnable by a single trade or by luck.
* **A new consumer** (mobile app, cloud sync, an ML model) — read
  `IntelligenceSnapshot`. It is the whole contract, it serialises to JSON, and
  nothing else in the layer needs to know the consumer exists.

Prepared-for but not built: cloud synchronisation (the snapshot is already a
pure function of the fact set, so it can be computed anywhere the facts are),
brokerage integration (facts come from the journal, whatever fills it), and ML
enhancement (the fact set is a clean, labelled, feature-rich training corpus —
but any model must sit *beside* the deterministic engines, never inside them, or
"Why?" stops working).
