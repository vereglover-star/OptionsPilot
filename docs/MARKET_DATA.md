# MARKET_DATA.md — the market-data subsystem (V0.5.2 · V0.5.3 · V0.5.4)

The design, the measurements behind it, and the root causes it eliminates.
Read this before changing anything under `optionspilot/data/`.

**V0.5.2** replaced the layer: typed provider failures, a capability table
measured from `now`, a tier ladder that distinguishes `exhausted` / `empty` /
`stale` / `failed`, durable storage, and one trace per request.

**V0.5.3** made it operable: one owner for provider health (§13), ranking by
measured health instead of a fixed order (§14), a diagnostics dashboard and
export (§15), configuration without code changes (§16), replay and benchmark
tools (§17), cache metrics (§18), structured logs (§19), and capability
discovery (§20). No behavioural change to what is traded, and the shipped
chain answers exactly as it did before (§21).

**V0.5.4** used that extensibility: three keyed providers — Finnhub, Twelve
Data, Alpha Vantage (§23) — plus the credential handling (§24) and request
budgeting (§25) they need. **With no API keys configured the app behaves
exactly as it did in V0.5.3**, which is the shipped default.

---

## 1. Why this was rebuilt

Chart history was the last subsystem in OptionsPilot that behaved
inconsistently. Every previous attempt fixed a symptom; this one is a
replacement of the layer, because the symptoms all shared one cause:

> **The old stack could not tell the difference between "there is no data",
> "I can't reach the data", and "this data cannot exist".**

`yfinance` returns an **empty DataFrame** for all three. The old
`CachedProvider` received that empty frame, memoized it, and the frontend
received `{"candles": []}` and guessed. Depending on which guess it made, the
user saw a permanent spinner, a red error on a market holiday, a chart that
silently stopped at an arbitrary date, or a scroll-back that retried the same
impossible request forever.

Every bug listed in the V3.x chart sprints (`docs/NEXT_SESSION.md`) is a
variation of that one ambiguity.

---

## 2. The root causes, proven

Each was reproduced from evidence, not inferred.

### 2.1 The depth window was measured from the wrong point *(primary)*

Yahoo's intraday limits run from **now**. Its own 422 body says so:

```
5m data not available for startTime=1777054668 and endTime=1785089868.
The requested range must be within the last 60 days.
```

`yfinance_provider._clamp_history_window` measured that window from the
**request's end** instead:

```python
oldest_allowed = end - timedelta(days=max_days)   # WRONG: `end`, not `now`
```

A history-paging request for 5-minute bars starting 62 days ago but ending 31
days ago therefore passed the clamp untouched, 422'd upstream, and came back
as an empty frame. Straight from `logs/data.log`, 2026-07-23:

```
history fetch empty IWM 5m requested 2026-05-22..2026-06-22
                            (clamped 2026-05-22..2026-06-22)
```

Note the clamp reporting **no change**. The frontend read the empty response as
"a failed fetch, not proof of exhaustion" (`chLoadHistoryChunk`'s own comment),
deliberately left `historyExhausted` clear, and retried on the next scroll —
forever, at three guaranteed-422 upstream requests per scroll.

Pinned by `test_yfinance_provider.py::test_intraday_depth_is_measured_from_now_
not_from_the_request_end`.

### 2.2 A history-paging request poisoned the live-window memo *(critical)*

The candle memo is keyed by `(symbol, timeframe, session)` — it has to be, or
the live poll (whose `end` advances every few seconds) would never hit it. But
nothing stopped a history-paging request, whose window **ends in the past**,
from writing to that same key. The next live load then found an entry whose
coverage check passed (`entry.start <= start`, and a scroll-back's start is
older), sliced it to the live window, and rendered whatever few bars happened
to overlap.

Reproduced by `scripts/chart_check.py`: **QQQ 1d came back with a single
candle from nine months earlier**, `outcome: memo`, no error anywhere. This is
the "chart suddenly shows one candle" and "scrolled-in history randomly
vanishes" class of report.

Fix: only live-window requests use the memo at all. Pinned by
`test_marketdata_service.py::TestHistoryPagingDoesNotPoisonTheLiveMemo`.

### 2.3 Measured depth limits were wrong

`_HISTORY_MAX_DAYS` used 60 for 5m/15m/30m and 730 for 1h. Both are **one day
past the cliff** — Yahoo rejects exactly those values. A request built at the
boundary got a 422 and looked like an outage. Measured values are in §3.

### 2.4 A corrupt cache crashed the app at startup

`CandleCache` wrapped its integrity check in a recovery block, but
`sqlite_connect` itself raises on a damaged file — the first `PRAGMA` fails
with "file is not a database" — *before* the recovery block was entered. A
damaged `cache.db` therefore took down `Orchestrator` construction. Worse, the
failed connection leaked with the OS file handle open, so on Windows nothing
could then rename or delete the file. Both fixed; pinned by
`test_cache.py::test_a_corrupt_file_is_quarantined_and_replaced_on_open`.

### 2.5 A pandas 3 unit change silently corrupted interval math

`DatetimeIndex.astype("int64")` returns **microseconds** in pandas 3 where
pandas 2 returned nanoseconds. Any spacing computation built on that
assumption is off by 1000x. Caught during development by an existing test;
the code now uses `TimedeltaIndex.total_seconds()`, which is unit-independent.

### 2.6 A history prepend yanked the viewport

`chLoadHistoryChunk` captured the visible range when the request *started* —
mid-drag, since history is armed by the range-change event during a pan — and
restored that stale snapshot when the merge landed, pulling on-screen bars back
to where they were mid-gesture. Invisible while the backend was slow enough
that the merge landed after the drag ended; the new backend's ~0.2s responses
made it reproducible. Now restores the range as it is at merge time.

---

## 3. Measured provider limits

Probed live against Yahoo by walking each interval back day by day until it
422s (`scripts/marketdata_probe.py` reproduces this). SPY, 2026-07-26:

| Interval | Deepest accepted | First rejected | Shipped cap |
|---|---|---|---|
| 1m  | 8 days   | 30 days  | **7**  |
| 2m  | 59 days  | 60 days  | **59** |
| 5m  | 59 days  | 60 days  | **59** |
| 15m | 59 days  | 60 days  | **59** |
| 30m | 59 days  | 60 days  | **59** |
| 90m | 59 days  | 60 days  | *(unused)* |
| 1h  | 729 days | 730 days | **729** |
| 1d  | 1993-01-29 (33 years) | — | **unlimited** |
| 1wk | 1993-01-25 | — | **unlimited** |
| 1mo | 1993-01-01 | — | **unlimited** |

Yahoo states the limit in the 422 body for each (`"Only 8 days worth of 1m
granularity data are allowed"`, `"must be within the last 60 days"`). Shipped
caps sit one day inside each cliff so a request built moments before midnight
UTC cannot land on the far side of it. They live in
`capabilities.YAHOO_INTERVALS` and are asserted by `test_capabilities.py`.

---

## 4. Provider survey

Every realistic source for a **free, offline-capable, single-user desktop app
with no API-key onboarding**, evaluated 2026-07-26.

| Provider | Official API | Daily depth | Intraday | Minute | Rate limit | Free tier | Commercial use | Cost | Reliability | Python | Integration | Maintenance risk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Yahoo chart JSON** (`v8/finance/chart`) | Unofficial, public | 33 yrs | 1m–1mo | 8 d | Soft; 429 under abuse | Yes | Grey area (personal use) | Free | High in practice; occasional 429/outage | stdlib `urllib` | **Trivial** — one JSON call | Medium: unofficial, but shape stable for years |
| **yfinance** | No (scrapes the above) | Same | Same | Same | Same + a global throttle | Yes | Same | Free | Medium — breaks on Yahoo internals changes | Package | Trivial | **High** — frequent breaking releases |
| **Stooq CSV** | Semi-official | 20+ yrs | No (account needed) | No | Unstated; polite | Yes | Permissive for personal | Free | Medium — **anti-bot HTML challenge on some networks** | stdlib | Easy | Low |
| **Alpha Vantage** | Yes | 20+ yrs | 1m–60m | 30 d (free) | **25 req/day** free | Key required | Paid tiers | Free / $50+/mo | High | `requests` | Easy | Low |
| **Twelve Data** | Yes | 30+ yrs | 1m+ | Full | 800 req/day free | Key required | Paid | Free / $29+/mo | High | `requests` | Easy | Low |
| **Polygon.io** | Yes | 20+ yrs | 1m+ | Full, tick | 5 req/min free | Key required | Yes, licensed | Free / $29–199/mo | **Very high** | Official SDK | Easy | Very low |
| **Tiingo** | Yes | 30+ yrs | 1m+ | Full | 500 sym/mo free | Key required | Paid | Free / $10+/mo | High | `requests` | Easy | Low |
| **Databento** | Yes | Full history | Yes | Tick | Usage-priced | No free tier | Yes, licensed | $$$ | Very high | Official SDK | Moderate | Very low |
| **IEX Cloud** | — | — | — | — | — | — | — | — | **Shut down Aug 2024** | — | — | n/a |
| **Nasdaq Data Link** | Yes | Varies | Limited | No | Varies | Limited | Paid | Varies | High | SDK | Easy | Medium |
| **Broker feeds** (Alpaca / Tradier / IBKR) | Yes | 5–20 yrs | 1m+ | Full | Generous | Account required | Yes | Free w/ account | High | SDK | Moderate | Low — **but requires an account, which this app deliberately does not** |

**Recommendations**

- **Best completely free, no onboarding — Yahoo chart JSON.** The only source
  that gives 30+ years of daily and 1-minute intraday with no key, no account
  and no quota. Shipped as the primary. Its weakness (unofficial) is mitigated
  by having two independent code paths to it plus a non-Yahoo tertiary.
- **Best hybrid — Yahoo primary + Tiingo or Twelve Data secondary.** A free key
  buys a genuinely independent, officially-supported feed with ample quota for
  one desktop user. The adapter is ~80 lines; the registry needs one entry.
- **Best inexpensive — Polygon.io Starter ($29/mo).** Licensed, documented,
  unlimited API calls, minute aggregates back 5+ years. The obvious upgrade if
  data quality ever becomes a real cost.
- **Best production — Polygon.io or Databento.** Licensed for commercial
  redistribution, real SLAs, tick data, WebSocket streaming. Required if
  OptionsPilot is ever sold or distributed commercially.

**Not chosen, and why:** Alpha Vantage's 25-requests/day free tier cannot serve
one chart session. IEX Cloud no longer exists. Broker feeds require an account,
which contradicts the app's zero-setup design. Everything paid was excluded
from the *default* chain because the app must work out of the box.

**Streaming remains the one real gap.** Every free source here is poll-only, so
the forming candle advances at the poll cadence (~7s intraday) rather than
tick-by-tick, and Yahoo publishes the in-progress bar with `volume=0` until it
closes. A true tick feed requires a paid provider with a WebSocket; the adapter
interface has room for it (`fetch_latest`) but nothing here fakes it.

---

## 5. Architecture

```
        engine · risk · backtester · coach            ui / Charts tab
                        │                                    │
                        └──────── MarketDataProvider ────────┘
                                        │
                                 CachedProvider          ← the ABC's face
                        quotes/chains │ candles            (data/cached.py)
                          ┌───────────┘ └───────────┐
                   YFinanceProvider          MarketDataService
                   (options data only)        (data/service.py)
                                                    │
             ┌──────────────┬──────────────┬────────┴────────┬──────────────┐
             │              │              │                 │              │
       ProviderRegistry  CandleCache    quality.py      diagnostics.py   capabilities
       (ordering,        (durable,      (semantic       (one trace       (per-provider
        eligibility,      atomic,        validation)     per request)     depth table)
        breakers)         versioned)
             │
     ┌───────┴────────┬─────────────────┐
     │                │                 │
YahooChartAdapter  YFinanceAdapter  StooqAdapter        ← HistoryAdapter
  (priority 10)     (priority 20)   (priority 30)
   urllib + JSON     yfinance pkg     CSV, daily only
```

**Nothing above `MarketDataService` knows a provider exists.** Charts ask for
history; which source answered is an implementation detail carried only in the
diagnostics payload.

### The tier ladder

Each request walks a fixed ladder and stops at the first tier that can answer:

```
memo (hot, TTL'd, request-deduplicated)
  ↓
disk cache (warm start / restart inside the freshness window)
  ↓
provider 1 → provider 2 → provider 3     (retry, then fail over)
  ↓
half-open circuit-breaker probes         (self-heal a total outage)
  ↓
disk cache, knowingly stale              (display surfaces only)
  ↓
an explicit, explained failure
```

### The four conditions that used to be one

| Outcome | Meaning | UI behaviour |
|---|---|---|
| `exhausted` | Older than **any** provider can serve | "◄ Start of available history · 5m data starts May 28, 2026". Stops asking. **Not an error.** |
| `empty` | Providers answered; the window is a holiday or predates listing | Chart left as-is. **Not an error.** |
| `stale` | Nothing live reachable; local bars served | Yellow banner with the as-of time (suppressed while the market is closed) |
| `failed` | Nothing could answer, nothing on disk | Red overlay with the reason and a Retry |

Plus `live` / `memo` / `cache` for the successful tiers. These names are shared
verbatim with the frontend state machine (`data-ch-state` on `#ch-main`) so a
UI state and a backend trace can be compared directly.

---

## 6. The provider adapter contract

A new provider is **one file**. It implements two methods and declares a
capability table; the base class supplies interval mapping, resampling to
non-native intervals, canonical normalization, window clamping, throttling,
health/error state, and the rolling quality score.

```python
class MyAdapter(HistoryAdapter):
    provider_name = "myfeed"
    provider_priority = 15
    capabilities = ProviderCapabilities(intervals={...})

    def _fetch_native(self, symbol, spec, start, end, prepost) -> pd.DataFrame:
        ...          # transport + parse. Raise a typed ProviderError on failure.

    def _probe(self) -> None:
        ...          # a minimal request proving the provider answers
```

Register it in `registry.default_registry()`. That is the whole integration.

**Failures are typed**, because the service reacts differently to each:

| Exception | Retry? | Fail over? | Counts against health? |
|---|---|---|---|
| `ProviderUnavailable` | yes | yes | yes |
| `ProviderRateLimited` | no (skip to next) | yes | yes, with a cooldown |
| `ProviderRangeError` | **no** | yes (a deeper provider may help) | **no** |
| `ProviderSymbolError` | **no** | yes (feeds list different symbols) | **no** |

Range and symbol errors are *correct answers to impossible questions*. Counting
them would trip the circuit breaker on a provider that is working perfectly.

**Adapters raise rather than return empty frames.** That single rule is what
removes the ambiguity described in §1.

---

## 7. Cache design

`data/cache.py`. Not merely an optimisation — it is the last tier before a
blank chart, so it is built like storage, not like a cache.

- **Keyed** by `(symbol, timeframe, ts)`, `WITHOUT ROWID`.
- **Provider attribution** (schema v2): every bar records which provider wrote
  it and when, so "these two sources disagree" is diagnosable later.
- **Atomic writes.** One transaction per store: every bar lands or none does.
- **Integrity checked on open** (`PRAGMA quick_check`, the cheap variant — this
  runs on every launch and the file reaches hundreds of megabytes).
- **Corruption → quarantine + rebuild.** The damaged file is *moved* to
  `cache.db.corrupt-<timestamp>`, never deleted, and a fresh one is created. A
  damaged cache degrades to a *cold* cache instead of crashing the app.
- **Self-healing at runtime.** A `DatabaseError` from any operation triggers the
  same rebuild once and retries. Only genuine corruption markers ("malformed",
  "not a database", "disk image") qualify — an ordinary SQL error surfaces as
  itself rather than silently wiping user state.
- **Versioned** via `core.sqlite.run_migrations`. An existing v1 `cache.db`
  opens, migrates, and keeps every row. A *newer* schema is refused rather than
  rewritten.
- **Validated on read.** The file is untrusted storage; a bad row that reached
  it must not reach a chart.
- **Thread-safe.** One connection, one lock (candle fetches run on
  ThreadPoolExecutor workers and FastAPI threadpool threads concurrently).

---

## 8. Validation

`data/quality.py`. `base.validate_candles` is the *shape* gate; this is the
*meaning* gate, and it returns a **report**, not just a cleaned frame, so the
service can choose between using it, preferring another provider, or refusing
to render it.

Checked: OHLC self-consistency (high is the max, low is the min, within float
tolerance) · time ordering · duplicate timestamps · future timestamps · NaN and
±inf · non-positive prices · negative volume · isolated bad prints · interval
conformance · gap size.

Two design decisions worth knowing:

**Gaps are not defects.** Overnight, weekend and holiday gaps are recorded for
diagnostics and carry **no** score penalty.

**Interval conformance is judged on the tightest spacing, not on a share of
bars sitting on a grid.** A US-equity 4-hour chart has two bars per session and
a ~20-hour overnight gap, so only half its spacings are exactly one interval —
a grid-share test rejects perfectly good data. The tightest-gap test passes it
while still catching the real defect: a provider answering a 1-minute request
with daily bars. (Yahoo does exactly that under load; the adapter also checks
the payload's own `dataGranularity` field, which is the cheaper of the two.)

Repairs are conservative and always reported. Nothing is invented,
interpolated, or smoothed. A frame with a few dropped glitch bars stays usable
(score < 100); an unrepairable one is refused and the service fails over.

---

## 9. Diagnostics

`GET /api/diagnostics/marketdata?traces=25`

The design goal: **a chart complaint should be answerable from one JSON
response, without reproducing it.** Every request records one trace — the
window asked for, every provider tried, why each was skipped or failed, which
tier finally answered, what validation found, and how long each stage took —
into a bounded ring (250 traces, well under a megabyte).

```json
{
  "available": true,
  "providers": [{"name": "yahoo", "available": true, "failure_rate": 0.0,
                 "data_quality_score": 100.0, "avg_latency_ms": 187.3,
                 "circuit_open_for": null, "rate_limit": {...}}, ...],
  "cache": {"bars": 48925, "symbols": 31, "by_provider": {"yahoo": 48925},
            "bytes": 6291456, "schema_version": 2, "rebuilds": 0},
  "requests": {"total_requests": 112, "success_rate": 1.0, "live_rate": 0.41,
               "avg_duration_ms": 61.2, "outcomes": {"live": 46, "memo": 61, ...}},
  "traces": [{"symbol": "SPY", "timeframe": "5m", "outcome": "live",
              "provider": "yahoo", "bars": 476, "retries": 0, "fallbacks": 0,
              "attempts": [{"provider": "yahoo", "outcome": "ok",
                            "duration_ms": 187.3, "bars": 476}],
              "validation": {"score": 100.0, "usable": true, ...}}, ...]
}
```

Every `/api/candles` response also carries its `trace_id`, so a screenshot of a
wrong chart maps to an exact trace.

---

## 10. Testing

| Suite | What it covers | Command |
|---|---|---|
| `pytest` (250 market-data tests) | Capabilities, adapters, validation, registry, breakers, cache, service ladder, diagnostics, API — all offline against scripted providers | `.\scripts\test.ps1` |
| `scripts/marketdata_stress.py` | 41 offline torture scenarios: concurrency, rapid switching, hostile providers, corrupt cache, malformed data, memory, thread safety | `python scripts/marketdata_stress.py` |
| `scripts/marketdata_stress.py --live` | 6 more against the real chain: every timeframe, 20 years of daily, exhaustion, 24 concurrent loads | `python scripts/marketdata_stress.py --live` |
| `scripts/chart_check.py` (49 checks) | The whole chart system in a real headless browser, including the new state machine, exhaustion pill and diagnostics endpoint | `python scripts/chart_check.py` |
| `docs/QA_MARKET_DATA.md` | 84 manual checks for what no automation covers | by hand |

---

## 11. Things not to change without careful thought

- **`get_candles` must never return stale data.** The engine's fail-closed rule
  (no data ⇒ skip the symbol) depends on it. Stale bars exist only behind
  `allow_stale=True` / `get_history`, which display surfaces use and the
  trading path does not.
- **Adapters raise; they do not return empty frames to signal failure.** This is
  the rule that removes the ambiguity the whole rebuild is about.
- **Range and symbol errors must not count against provider health.**
- **Only live-window requests may use the memo** (§2.2).
- **Depth limits are measured from `now`, never from the request's end** (§2.1).
- **The capability table is the source of truth for depth.** Do not re-derive
  limits in an adapter; put them in `capabilities.py` where one test asserts
  them.

---

## 12. Future work

1. **A second non-Yahoo provider with real intraday depth.** Tiingo or Twelve
   Data, behind an optional free key. The chain is already built for it; this
   is one file plus one registry entry.
2. **Streaming.** A WebSocket provider would remove the poll cadence and give a
   true tick-by-tick forming candle. Requires a paid feed.
3. **Cache pre-warming** for watchlist symbols on idle, so a first chart open is
   instant rather than one request away.
4. **Cross-provider reconciliation.** `quality.disagreement()` already measures
   it and diagnostics already record it; nothing acts on it yet, deliberately —
   deciding which source is "right" is not something this layer can know.
5. ~~A user-facing diagnostics panel~~ — shipped in V0.5.3 (§15).

---
---

# V0.5.3 — production readiness

Everything above still holds. This half is about **operating** the subsystem:
knowing which provider is healthy, why a request went the way it did, and being
able to retune any of it without editing code.

---

## 13. Provider health has one owner

Before V0.5.3 a provider's operational state lived in two objects that had to
agree. `adapter.ProviderHealth` counted requests and failures;
`registry._Breaker` decided whether the provider stayed in rotation — and it
decided that **by reading the adapter's failure counter**. One invariant, two
owners, and the policy "a range error is not an outage" re-derived in three
files.

`data/health.py::ProviderHealthMonitor` is now the single owner of:

| | |
|---|---|
| **Volume** | requests, successes, failures, empties, bars, requests today |
| **Failure shape** | timeouts, validation failures, rate limits, per-kind breakdown |
| **Latency** | EWMA average plus a 100-sample window yielding a real p95 |
| **Recency** | last success, last failure, last error, last outcome |
| **Breaker** | state (closed / open / half-open), trips, remaining cooldown |
| **Quality** | rolling data-quality score |
| **Ranking** | the sort key the registry orders by (§14) |

### The failure taxonomy, defined once

Every failure is recorded with a `kind`, and `health.COUNTS_AGAINST_HEALTH` is
the **only** place that decides whether it counts:

```
unavailable · timeout · rate_limited · validation · internal    count
range · symbol                                                  do not
```

A range or symbol error is a *correct answer to an impossible question*.
Counting it would trip the breaker on a provider working perfectly — which is
exactly what used to happen every time a user scrolled a 5-minute chart past
Yahoo's 59-day floor.

### Counting and tripping are separate steps

`record_failure()` counts. `evaluate_breaker()` trips. They are separate
because the caller that sees a transport failure (the adapter) is not the one
that decides the attempt counted against the provider (the service, which also
sees validation rejects and adapter bugs the adapter never hears about).
Merging them double-counted every trip and doubled the first cooldown.

### Two accounting bugs this surfaced

Both existed before and were invisible without a single owner:

1. **A provider serving consistently-unusable bars never tripped.** The adapter
   recorded a *success* as soon as the transport parsed, and the service's
   validation reject was never counted anywhere. Now the service calls
   `demote_last_success(KIND_VALIDATION, …)`, which **moves** the counters
   rather than adding a second request — one upstream call stays one request in
   every total. (Recording a fresh failure instead inflated `requests` and
   halved every provider's apparent failure rate.)
2. **A demotion could only ever reach a streak of 1.** Recording the success had
   zeroed the failure streak, so a provider answering *every* request with
   unusable bars oscillated 0 → 1 → 0 → 1 and never reached the breaker
   threshold. `demote_last_success` now restores the streak from before the
   success.

---

## 14. Ranking: health, not fiat

`ProviderRegistry.candidates()` used to sort by the static `provider_priority`.
It now sorts by `ProviderHealthMonitor.rank()` — **lower is better**:

```
rank = priority                                   the anchor
     + min(avg_latency_ms / 100, 50)              1 priority step == 1s latency
     + recent_failure_rate * 50
     + consecutive_failures * 15
     + min(breaker_trips, 4) * 5
     + max(0, 100 - quality) * 0.5
```

**The scale is the design.** Priorities are spaced 10 apart, and 10 rank points
is one second of latency. So:

| Situation | Yahoo (p10) | Stooq (p30) | Winner |
|---|---|---|---|
| No traffic yet | 10.00 | 30.00 | Yahoo — the documented static order |
| 180ms vs 320ms | 11.80 | 33.20 | Yahoo |
| **2400ms vs 260ms** | 34.00 | 32.60 | **Stooq** |

A cold system reproduces the V0.5.2 order **exactly**, which is what makes this
safe to ship. A provider merely a little slower keeps its place (no thrashing on
noise); one a full second slower loses a step; one that is failing loses its
place immediately rather than after three more chart loads.

### The failure rate is windowed, not lifetime

`rank()` measures failures over the **last 50 attempts** (`OUTCOME_WINDOW`), not
over the provider's lifetime. A lifetime rate never decays: five failures during
a two-minute outage would leave a provider at an 83% failure rate, and because
every later success only moves the denominator it would stay demoted for
thousands of requests after it was demonstrably healthy. The window makes
recovery proportional to how long the provider has been well. Lifetime totals
are still reported — they are just not what orders the chain.

### The escape hatch

`market_data.dynamic_ranking: false` pins the static order exactly. It exists so
that "ranking misbehaved" is a config change, not a rollback.

---

## 15. The diagnostics dashboard and export

**Help ▸ Diagnostics** opens a read-only page over
`GET /api/diagnostics/marketdata`. It shows, per provider: status pill (healthy
/ probing / out of rotation), rank and configured priority, success rate,
average and p95 latency, request totals and today's count, timeouts, validation
failures, rate limits, breaker trips, data-quality meter, last success, last
error, and served intervals — then session request aggregates, cache
intelligence (§18), and the recent-request table with each request's provider
chain.

Two rules govern the page:

- **It computes nothing.** Every number is rendered straight from the payload,
  which is the same payload the exports carry — so a screenshot, an export and
  a log line cannot disagree about what happened.
- **It never polls.** It loads when opened and on an explicit Refresh. A
  diagnostics screen fetching on a timer would become traffic inside the very
  traces it is displaying.

**Export** (`GET /api/diagnostics/marketdata/export?format=text|json`) serves the
payload as a dated attachment. The text rendering lives in `data/report.py` and
is deliberately safe to paste into a public issue tracker: no stack traces, no
filesystem paths, no credentials — and it says so in its own closing line so a
user does not have to audit it first.

**Replay** (`POST /api/diagnostics/marketdata/replay`) is on the same page:
clicking a request re-runs it. See §17.

---

## 16. Configuration without code changes

`config.yaml`'s `market_data:` section. Every operational knob, in one place:

```yaml
market_data:
  dynamic_ranking: true          # false pins the static provider order
  memo_max_entries: 400
  structured_logging: true
  capability_discovery: false    # see §20
  capability_refresh_days: 30
  cache:
    enabled: true
    retention_days: null         # null keeps everything (the default)
    warn_bytes: 536870912
  providers:
    yahoo:
      enabled: true
      priority: 10               # null keeps the adapter's own
      timeout: null              # null keeps the adapter's default_timeout
      max_attempts: 2
      retry_backoff: 0.4
      min_request_interval: null
      breaker_threshold: 3
      breaker_base_cooldown: 15.0
      breaker_max_cooldown: 300.0
      min_quality_score: 0.0
    stooq:
      enabled: false             # drop a provider entirely
```

Before this, a provider's timeout was in its adapter, its retry count was a
constant in `service.py`, its breaker thresholds were constants in
`registry.py`, and its ordering was a class attribute — four files to answer
"why is Stooq being skipped?", and nothing a user could change at all.

**Three deliberate choices:**

- **Unknown keys are a startup error.** A silently-ignored `timout: 30` would do
  nothing for the life of the install.
- **Unknown *providers* are accepted.** That is how a config pins a future
  provider's settings before its adapter ships, and how a config survives a
  downgrade.
- **`timeout: null` means the adapter's own default**, not a global number.
  Stooq's CSV endpoint is reliably slower than Yahoo's JSON one; flattening both
  would either cut Stooq off early or let Yahoo hang.

**Where the schema lives.** `data/` may import only `core/`
(`tests/test_architecture.py` enforces it), so it cannot depend on the pydantic
config layer. The runtime shape is therefore frozen dataclasses in
`data/config.py`, the validated YAML face is `config/settings.py`, and the
translation happens in the composition root that already imports both —
`orchestrator.py`. Two tests assert the key sets line up exactly, so a field
added to one and forgotten in the other fails the suite.

---

## 17. Replay and benchmark

### Replay — `data/replay.py`

```python
replay(service, trace)                  # re-run a recorded request
compare_providers(registry, request)    # ask EVERY provider directly
```

`compare_providers` deliberately bypasses the memo, the disk cache, the failover
chain and (by default) the circuit breaker, because the question is "what does
each source actually say?" — a ladder that stops at the first success answers a
different question. It returns bars, latency, quality and **disagreement against
the first provider that answered**, so "Stooq and Yahoo disagree about last
Tuesday" is a lookup rather than a hunch.

There is deliberately **no separate recorder**. Every request is already
recorded once, in `diagnostics.RequestTrace`, with the symbol, timeframe, window
and session flag replay needs. A second recorder would be a second thing to keep
in sync for no information gain — which is why replay takes a trace id, and why
anything in the dashboard's trace list is clickable.

### Benchmark — `scripts/marketdata_benchmark.py`

```
python scripts/marketdata_benchmark.py            # offline, synthetic providers
python scripts/marketdata_benchmark.py --live     # the real chain
```

Runs the same cases against each provider *directly* (so each column is that
provider's own performance, not the chain's) and reports average / median / p95
latency, bars per second, data quality, cross-provider disagreement, CPU
seconds, memory delta, and **the health rank the registry would give it** —
which is the number that actually decides ordering. Offline by default so it
runs in CI; `--live` is the one to use before re-prioritising a chain.

---

## 18. Cache intelligence

"Is the cache working?" used to be answerable only as "there are N bars in it",
which says nothing about whether any of them were ever served. `CacheMetrics`
adds: reads, hits, misses, hit rate, stale reads, bars read, writes, bars
written, evictions, rebuilds, errors, average age of served bars, and
`provider_requests_saved` — every hit is one upstream request that did not
happen, which is the number worth putting in front of a user.

`stats()` also reports the span actually held (`oldest_bar` / `newest_bar`) and
whether the file has passed its configured warning size.

**Retention** (`cache.retention_days`) prunes bars older than N days at open and
counts the evictions. It is **off by default**: history is small, and the deeper
the cache the better the last tier before a blank chart. It exists for the user
who charts hundreds of symbols and would rather bound the file.

---

## 19. Structured logging

Every request emits one line, `key=value` throughout:

```
history req=412 symbol=SPY tf=5m outcome=live provider=yfinance bars=476
        duration_ms=188 cache=miss memo=miss retries=1 fallbacks=1
        chain=yahoo=RangeError > yfinance=ok quality=100.0 usable=True
```

Greppable rather than JSON on purpose: `logs/data.log` is read by a human
looking at a user's bug report, and `outcome=failed` is something you can find
with Ctrl-F in Notepad. `chain=` answers "who did we ask and what did they say"
in one glance — the question a chart complaint always turns into.

Failed and stale requests log at WARNING, everything else at DEBUG, and **both
carry the same line**, so raising the log level to chase an intermittent problem
gives the same fields as the dashboard rather than a thinner message. The same
string is on the trace payload as `chain`, so a log excerpt and the dashboard
need no translation between them.

---

## 20. Capability discovery

`data/discovery.py` measures a provider's real per-interval depth (ladder walk,
then a binary search of the cliff — about a dozen requests per interval rather
than hundreds), persists it to a JSON `CapabilityStore`, and refreshes it on a
configured cadence. `scripts/marketdata_probe.py` now calls into it, so the app
and the script cannot disagree about how depth is measured.

**It is advisory, and off by default.** It does not rewrite `capabilities.py`.
Three reasons, in order of weight:

1. **A probe costs real upstream requests** — roughly a dozen per interval per
   provider, to re-derive numbers that change maybe once a year.
2. **The shipped table is a deliberate floor**, sitting one day *inside* each
   measured cliff so a request built moments before midnight UTC cannot land on
   the far side of it. A discovery run that measured 60 and wrote 60 would undo
   that margin on every install.
3. **A probe can be wrong.** A network hiccup mid-measurement reads exactly like
   a shallower provider, and a wrong number written to disk is believed until
   something re-measures it.

So discovery reports, and `drift()` turns that into "the table promises more
than the provider serves" — one-directional on purpose, because a conservative
table costs a little depth while an over-promising one produces guaranteed-422
requests on every scroll. What it buys is that a **future** provider need not
have its depth hand-measured before it can ship.

One rule it inherits from §1: **an empty window is not a refusal.** The walk
continues through holidays and weekends; conflating the two would make every
measurement taken on a Sunday wrong.

---

## 21. Adding a provider after V0.5.3

Still one file and one registry entry — and now the operational half is free.
Write the adapter as §6 describes, then:

```python
# data/registry.py::default_registry
classes = [YahooChartAdapter, YFinanceAdapter, TiingoAdapter]
```

The adapter inherits, without writing any of it: a `ProviderHealthMonitor` (so
it appears on the dashboard, in the export, in the benchmark and in the ranking
immediately), a per-provider circuit breaker, configuration under
`market_data.providers.<name>` with no schema change, participation in replay
and comparison, capability discovery, and its own row in every diagnostics
surface.

**A checklist for the adapter itself:**

1. Set `provider_name`, `provider_priority`, `capabilities`, `default_timeout`.
2. Implement `_fetch_native` — **raise a typed `ProviderError`; never return an
   empty frame to signal failure** (§6). Map the provider's own error dialect
   onto the four types; that mapping is the whole integration risk.
3. Set `reports_empty_reliably = True` only if *every* failure path raises.
4. Implement `_probe()` for the health check.
5. Put depth in `capabilities.py`, measured with
   `scripts/marketdata_probe.py --check`, one day inside the cliff.
6. Honour `self.timeout` in the transport.
7. Add `tests/test_<name>_provider.py` against canned bytes (see
   `tests/marketdata_helpers.py::fake_opener`) — offline, like every other
   provider test.

---

## 22. What V0.5.3 does NOT change

Stated explicitly because the whole milestone is infrastructure:

- **No new provider.** Finnhub, Twelve Data, Alpha Vantage and Polygon were
  out of scope by instruction; the point was to make adding them cheap.
- **No trading-behaviour change.** The engine, gate, risk manager and sizing are
  untouched. `get_candles` still never returns stale data.
- **No change to the shipped chain's answers.** Yahoo JSON → yfinance → Stooq,
  same order from cold, same capability table, same validation verdicts.
- **No new runtime dependency.** `psutil` is used by the benchmark *if present*
  and reported as "n/a" if not.

---
---

# V0.5.4 — provider expansion

V0.5.3 claimed adding a provider was one file and one registry entry. This
milestone spent that claim three times and reports what it actually cost.

---

## 23. The six-provider chain

```
  keyless (free, unmetered, no onboarding)     keyed (API key required)
  ┌───────────┬────────────┬─────────┐    ┌──────────┬────────────┬──────────────┐
  │ yahoo  10 │ yfinance 20│ stooq 30│ →  │finnhub 40│twelvedata50│alphavantage60│
  └───────────┴────────────┴─────────┘    └──────────┴────────────┴──────────────┘
```

**Why keyed providers sit behind the keyless ones.** A keyless request costs
nothing; a keyed one spends a metered allowance that cannot be bought back
until tomorrow. Spending scarce budget while a free source is healthy would be
strictly worse. Their value is what they do when the free sources fail: they
are *genuinely independent of Yahoo*, which is what makes a Yahoo-wide outage
survivable **at intraday resolution** for the first time (Stooq is daily-only).

**Why they are ordered among themselves by budget.** Finnhub (60/min, no
published daily cap) can absorb a session; Twelve Data (800/day, 8/min) can
absorb a day; Alpha Vantage (25/day) can absorb about one chart. Ordering by
how much a provider can afford to give is the same reasoning as ordering by
latency — cheapest capable source first.

| | Finnhub | Twelve Data | Alpha Vantage |
|---|---|---|---|
| Free limit | 60/min | 800/day · 8/min | **25/day** · 5/min |
| Intraday | 1/5/15/30/60m | 1/5/15/30/45m, 1/2/4h | 1/5/15/30/60m |
| Feed | real-time | delayed | delayed |
| Timestamps | unix UTC | naive exchange-local | naive US/Eastern |
| Errors in HTTP 200 | sometimes | **always** | **always** |
| Date range honoured | yes | yes | **no** (`full` returns everything) |

### What adding a provider actually cost

Each adapter is ~150 lines and implements exactly four things: `_build_url`,
`_translate`, `_parse`, `_probe`. Everything else — health monitoring, circuit
breaker, ranking, configuration, replay, benchmark, diagnostics row, capability
discovery, cache, validation — was inherited with no per-provider code. The
registry entry is one class in a list.

The honest exception is `data/http_adapter.py`, a **new shared base** for keyed
JSON providers (transport, HTTP-status mapping, JSON decoding, timezone
normalisation). Writing the same 80 lines of `urllib` plumbing three times
would have been the duplication the brief forbids. Yahoo and Stooq were
deliberately **not** retrofitted onto it: their transports do multi-host
failover and HTML-challenge detection respectively, which are the reason those
adapters are reliable rather than boilerplate.

### The timezone contract — the highest-consequence detail

Two of the three send **naive local time in the exchange's timezone**. Reading
those as UTC shifts every intraday bar by 4–5 hours and — because the offset
changes across a DST boundary — by a *different* amount either side of March
and November. The result is not a visibly broken chart. It is a subtly wrong
one that also **poisons the shared cache**, because bars are keyed
`(symbol, timeframe, ts)`.

`http_adapter.localize` is the one place this is handled:

- **intraday** → localise to the timezone the payload names, convert to UTC;
- **daily and coarser** → stamp at **00:00 UTC**, matching Yahoo and Stooq. A
  provider stamping daily bars at 05:00 UTC would write a second row for every
  day already cached, and charts would render doubled candles.

Unknown timezone names fall back to UTC with a warning; a nonexistent DST hour
shifts forward rather than discarding the response.

---

## 24. Credentials

**A missing key is a quiet, explained absence — never a crash.** The app ships
with zero keys and must start, chart and trade exactly as before. A keyed
provider with no key is still *constructed* and still appears in diagnostics
reporting `missing_api_key` and a signup link; it is simply never selected.
That is deliberately different from `enabled: false`, which removes the
provider entirely — "Finnhub needs a key" is information; "Finnhub is absent"
is not.

Resolution order, environment first:

```
market_data.providers.<name>.api_key_env   → an explicitly named variable
<PROVIDER>_API_KEY                         → the conventional one (no config needed)
market_data.providers.<name>.api_key       → the config file (supported, discouraged)
```

Environment first because it is the safer place for a secret and the one an
operator reaches for to override a shipped config. A whitespace-only value
counts as **absent**: a stray `FINNHUB_API_KEY=` in a shell profile is a
missing key, and treating it as present produces a confusing auth failure
instead of the accurate "no API key configured".

### Keys are redacted by default

`ProviderConfig.as_dict()` redacts `api_key` **unless asked not to**. That
payload reaches the diagnostics endpoint, the JSON export and the text report —
all of which users are explicitly invited to attach to public bug reports.
Defaulting to redaction means a leak requires someone to opt in, rather than
requiring every future caller to remember. Three tests assert no key appears in
the health report, the config dump, or a rendered report.

### The status vocabulary

One set of codes (`health.STATUS_*`), shared by the monitor, the API, the text
export and the dashboard, so "why can't I use Finnhub?" has one answer
everywhere:

| Status | Meaning | Remedy |
|---|---|---|
| `ok` | in rotation | — |
| `disabled` | switched off in config | enable it |
| `missing_api_key` | needs a key, none configured | the signup link shown |
| `auth_failure` | key present and rejected | replace the key |
| `quota_exceeded` | plan allowance spent | wait, or upgrade |
| `rate_limited` | told to back off | seconds |
| `temporarily_unavailable` | breaker open after failures | self-heals |

They are checked in order of permanence, so the reported reason is the one the
user would actually act on.

---

## 25. Request budgeting

`data/ratelimit.py`. The keyless chain never needed this — soft limits and a
"back off when told to" window were enough. **Alpha Vantage's 25 requests per
day are not survivable that way**: a session switching symbols spends the whole
allowance in under a minute, and reacting to the error afterwards is too late.

- **Two windows.** A real *sliding* 60-second window (a fixed bucket lets 2×
  through across a boundary — the exact burst that gets a key throttled) plus a
  daily counter. `state()` reports which one is binding.
- **Checked before the network**, in the registry (so an exhausted provider is
  not a candidate) and again in the adapter (closing the race, and covering
  replay/benchmark which reach adapters directly).
- **Counted before the call**, not after: an upstream request consumes quota
  whether or not it succeeds.
- **Persisted** to `<data>/quota.json`. A desktop app restarts; an in-memory
  counter would appear to grant a fresh 25 requests every launch.
- **A live quota error is authoritative** over the local count, which can drift
  low when the same key is used by another install.

### Budgeting distributes load without a scheduler

`QuotaTracker.pressure()` (share of the daily budget spent) feeds the existing
health ranking at `QUOTA_PRESSURE_WEIGHT = 25`. A provider approaching its
ceiling drifts down the ranking and traffic moves to a fresher key **before**
the budget is gone rather than after. That is a four-line change to a formula
rather than a new scheduling subsystem, and the stress harness shows it
working: a provider allowed 5 requests/day served only 3 of 30 requests before
pressure moved the rest elsewhere.

**Known limitation, accepted:** providers reset quotas in their own timezone
(Alpha Vantage at US/Eastern midnight); this tracks UTC days. The local budget
is only ever *more* conservative than the provider's, and a real quota error
still surfaces, so the mismatch costs a few unspent requests rather than
correctness.

---

## 26. Defects V0.5.4 found, and how

Six issues, all pre-existing or introduced-and-caught during the milestone.
Two came from the existing test suite, two from the self-audit, two from tests
written for the new code.

### Found by the existing suite

1. **`deepest_earliest` counted providers that could never answer.** It
   returned the deepest floor across every provider *supporting* an interval,
   regardless of availability. A keyless Finnhub declares 180 days of 5-minute
   history, so the chart would have been told history reached back 180 days
   when only Yahoo's 59 were reachable — and would then have scrolled into a
   window nothing could serve and retried it forever, reviving the exact bug
   class V0.5.2 was built to eliminate. Now *permanently* unusable providers
   (no key, disabled) contribute no floor, while *temporarily* unavailable ones
   (breaker open, rate limited) still do — the reported start of history must
   not lurch about as breakers open and close.

2. **The stale tier could report `stale` with zero bars.** `_settle` trimmed
   every frame to the requested window, including the last-resort stale one
   whose bars are *by definition* older than the request. When the cached bars
   fell entirely outside the window the frame emptied, and the UI showed a
   banner promising the last saved bars with nothing behind it. The stale tier
   now skips the trim; the viewport-stability reason for trimming does not
   apply there, because a stale answer is never memoized.

### Found by the self-audit

3. **Replay and discovery fired real requests at unconfigured providers.**
   Both checked only the circuit breaker, so a replay polled all three keyed
   providers with an empty token — collecting 401s, marking each
   **auth-failed (sticky)**, and poisoning the health of providers the user had
   never configured. Discovery was worse: ~13 doomed requests *per interval*,
   then "served nothing at any depth" warnings that read as an outage rather
   than a missing credential. Fixed by giving the adapter one gate,
   `can_spend_request()`, that **every** request-spending path now consults —
   the service, `fetch_history`, replay and discovery. Centralising the
   question is what stops a fifth path repeating it.

4. **The capability tables were mutable through any adapter.**
   `ProviderCapabilities` is a frozen dataclass, but freezing does not make a
   contained `dict` immutable — and these tables are module-level values
   **shared by reference** (`YAHOO_CAPABILITIES` backs both `YahooChartAdapter`
   and `YFinanceAdapter`). A stray write to one adapter's
   `capabilities.intervals` would have silently corrupted the other's depth
   table, surfacing as unexplained range errors somewhere else entirely.
   Nothing mutated them; `__post_init__` now wraps the mapping in a
   `MappingProxyType` so a future mistake is an immediate `TypeError`.

### Found by the new code's own tests

5. **`spec.resample` is a pandas offset alias, not a timeframe.** All three
   adapters initially passed it to `Timeframe.from_string`, which raises on
   `"10min"` — breaking every resampled timeframe. The raw frame is always at
   the *native* resolution (the base class aggregates afterwards), so the
   native interval is both the correct and the simpler answer.

6. **Alpha Vantage's daily-cap message contains the words "API key".** The
   real text is *"We have detected your API key ... our standard API rate limit
   is 25 requests per day"*. Testing for the credential first therefore
   reported a spent quota as a rejected key — sending the user off to
   regenerate a key that was never the problem, and marking the provider
   auth-failed (sticky) instead of quota-exceeded (clears tomorrow). Quota
   markers are now checked first, and the ordering is commented so it is not
   "tidied" back.

### Audit probes that found nothing

Run against the six-provider chain: deadlock (7 threads contending on the
monitor/quota lock pair for 2s — 0 stalls, 0 exceptions), ranking oscillation
(0 order changes over 200 identical cycles), provider starvation, infinite
retry (1 upstream call across 25 chart loads after an auth failure),
request de-duplication (48 concurrent callers → 1 upstream call), unbounded
memory in the health windows, cross-provider cache collision (the same 10 bars
written by two providers → 10 rows, not 20), and env-var config override.

---

## 27. Adding a fourth keyed provider

Inherit `KeyedHTTPAdapter` and implement four methods:

```python
class TiingoAdapter(KeyedHTTPAdapter):
    provider_name = "tiingo"
    provider_priority = 45
    capabilities = TIINGO_CAPABILITIES
    rate_limit = RateLimitPolicy(per_minute=50, per_day=1000)
    api_key_env_vars = ("TIINGO_API_KEY",)
    signup_url = "https://www.tiingo.com/"

    def _build_url(self, symbol, spec, start, end): ...
    def _translate(self, payload): ...      # their wording -> typed failures
    def _parse(self, payload, spec): ...    # their shape -> canonical frame
    def _probe(self): ...
```

Then add it to `default_registry`. Credentials, budgeting, health, ranking,
breaker, diagnostics, replay, benchmark and configuration all follow with no
further work.

**The three things worth getting right**, because they are where the real risk
is: map their error dialect completely (`_translate`), pass the correct
timezone to `to_frame` (see §23), and declare depth conservatively — an
over-promising capability table produces guaranteed-error requests on every
scroll.

---

## 28. V0.5.5 — what the certification pass changed here

Two data-layer changes came out of `docs/CHART_CERTIFICATION.md`. Both are
policy corrections, not new machinery.

**Interval conformance is judged on the median, not the minimum**
(`quality._interval_stats`). §11 described the test as "the TIGHTEST spacing,
not how many bars sit on a grid" — a deliberate choice that fixed the 4-hour
chart, and that turned out to be one step too strict. **Yahoo closes every US
equity session with a 30-minute stub bar** (15:30 → 16:00 ET, the closing
auction), so a perfectly good 1h frame contains exactly one 0.5-interval gap.
Measured in a real `cache.db`: IWM 60m, 2,180 bars, 1,862 gaps of exactly 1.0
and **one** of 0.5 — and that single bar set `usable = False`, scored the frame
0 and charged the provider a validation failure, on every 1h request including
the last completed session. It failed identically on `yfinance`, which serves
the same upstream.

Conformance is now the **median of the within-session gaps** (those under two
intervals; anything wider is an overnight or weekend break and says nothing
about the interval). When every gap is a session break — a coarse interval with
one bar per session — there is no within-session evidence and the tightest gap
is used as before, which is what still rejects daily bars served for a 1m
request. The rule catches everything the strict test caught, because a
genuinely wrong interval is wrong in the *bulk* of its spacings:

| Served for a 1h request | Median within-session gap | Verdict |
|---|---|---|
| 1h with the closing stub | 1.0 | accepted (was rejected) |
| 30m | 0.5 | rejected |
| 90m — a real Yahoo substitution | 1.5 | rejected |
| 1d — no within-session gaps | 24.0 (fallback) | rejected |

`min_gap_intervals` is still reported on every `HistoryReport`. It is a useful
statistic about the frame; it is no longer a veto.

**`validate_candles` drops `NaT` index entries** (`data/base.py`). Two real
sources put them there: `pd.to_datetime(..., errors="coerce")` in the HTTP
adapters turns any timestamp a provider malforms into `NaT`, and
`http_adapter.localize` maps a DST fall-back ambiguity to `NaT` **by design**
(§23 — better than throwing away a whole response). Nothing dropped them, so
one such bar survived every later stage and detonated at the very end of
`/api/candles`, where `int(ts.timestamp())` raises and 500s the entire
response. One malformed bar, no chart. The single shape-validation choke point
is the right place for it.

**And one finding that is not a code change:** **Stooq no longer works at all.**
Every request now returns a JavaScript proof-of-work challenge page (verified
live against both `stooq.com` and `stooq.pl`), which a `urllib` client cannot
satisfy and which this project will not circumvent. The adapter's HTML-challenge
detection (§4) does exactly the right thing — it refuses rather than parsing
the page as prices — but the practical consequence changes how the whole chain
should be reasoned about: **with no API keys configured there is exactly one
real source.** `yahoo` and `yfinance` are two independent code paths over one
upstream, so they share a failure domain, and Yahoo rate-limits by IP. The
intraday independence §23 claims is real *only* once a key is configured.

---

# V0.5.7 — the Market Data Control Centre

V0.5.2 built the subsystem. V0.5.3 made it *operable*. V0.5.4 gave it real
provider diversity. **V0.5.7 makes it usable by the person who owns it.**

Nothing about how data is fetched changed. What changed is that every decision
the subsystem makes is now visible, every setting it obeys is now editable
without a text editor and a restart, and every failure it can suffer is now
explained in words a user can act on.

## 29. The problem this milestone solves

Before it, the honest answer to each of these was "read `logs/data.log`" or
"edit `config.yaml` and restart":

| The user's question | The old answer |
|---|---|
| Where is my data coming from? | Help ▸ Diagnostics, if you find it |
| Why isn't Finnhub being used? | It needs a key; nothing said so on screen |
| How do I give it one? | Set an environment variable and restart |
| Is my key actually working? | Load a chart and hope |
| How many requests do I have left? | A number buried in a JSON export |
| What happens when Yahoo dies? | Read `docs/MARKET_DATA.md` §5 |
| My cache looks wrong — now what? | Delete `cache.db` by hand |
| Why is this provider first? | Read `data/health.py`'s rank formula |

Every one of those is now answered on screen, by the running system rather than
by documentation that can disagree with it.

## 30. `data/control.py` — administration, not selection

The new module is composed **over** the registry and the service, never inside
them. The direction of dependency is strictly one way: control knows about the
registry; the registry has never heard of control. That separation is why the
hot path did not slow down and why `MarketDataService` did not grow a settings
API.

    MarketDataControl
      dashboard()            one payload: health + credentials + quota +
                             capability + order + failover + advice
      set_api_key/remove     credentials, applied live
      set_enabled            bench a provider without a restart
      move/set_order/reset   the configured order
      set_ordering_mode      static | hybrid | dynamic
      test_connection()      a real request, end to end, with a verdict
      start_maintenance()    eight actions on a background thread, with progress
      cancel_maintenance()   cooperative stop
      recommendations()      specific advice about THIS install
      qa_*                   the developer panel, gated

Three things it deliberately does not do:

1. **It never computes a ranking.** `dashboard()` reports `registry.ranking()`
   verbatim, so the settings page and the chart cannot disagree about which
   provider goes first.
2. **It never returns a plaintext key.** The only `CredentialStore.resolve()`
   call hands the key straight to the adapter that must transmit it.
3. **Nothing a timer can reach spends a request.** The dashboard poll reads
   counters that already exist; every action that costs an upstream request is
   a POST behind an explicit click.

## 31. Credentials — `data/credentials.py`

### Resolution order, with one implementation

    environment variable  ->  credentials.json  ->  config.yaml  ->  missing

Environment wins because that is what an operator reaches for and what a
machine-specific deployment sets. A stored key beats `config.yaml` because
pasting a key into the app is a *later, more deliberate* act than a file that
may have been checked in months ago.

The mechanism is deliberately thin: `CredentialStore.overlay()` writes the
stored key into `ProviderConfig.api_key`, and `resolve_api_key` already
consults the environment *before* that field. So the documented order falls out
of code that already existed, with no second implementation to drift.

The consequence users must be told about: **while an environment variable is
set, a key pasted into the UI has no effect.** Hiding that would produce the
worst possible bug report ("I typed my key in and it still says no key"), so
`key_source` is reported per provider and the save response says so in words.

### Why not `settings.json`

`RuntimeSettings` already owns live-editable preferences and would have been
one fewer file. It is not used, for one reason: everything in `settings.json`
is treated as **ordinary user data** — `core/migration.create_backup()` copies
it, it is small enough that a user will open it in Notepad, and nothing about
it says "this is dangerous to share". A secret needs the opposite defaults.
`credentials.json` is written owner-only, is excluded from every export path by
construction (no export module imports `credentials.py`), and its name tells a
user what it is.

### The masking rule

> **A plaintext key leaves `credentials.py` only through `resolve()`.**
> Every other accessor returns the mask.

There is no `redact=False` on `CredentialStore.describe()`, unlike
`ProviderConfig.as_dict()`. That one needs a round-trip for its own tests; this
one has no legitimate caller wanting plaintext, and the parameter would only
ever be an invitation to leak. `tests/test_credentials.py::TestNoLeak`
enumerates every payload this repo invites users to attach to a bug report and
asserts the key is absent from all of them. **A new export belongs in that test
before it ships.**

## 32. Three ordering modes

`dynamic_ranking: true|false` answered "may health reorder the chain?" and
nothing else — which turned out to be two questions wearing one coat. A user
who sets their own order wants it respected, but not so rigidly that a dead
provider stays at the head of the chain.

| Mode | Meaning | Mechanism |
|---|---|---|
| `static` | Ask in exactly the configured order. Unavailable providers are still skipped — that is not an ordering decision. | `rank = priority` |
| `hybrid` | Your order stands; a provider loses its place only when it is genuinely **failing**, not when it is merely slower. | full rank **minus the latency term** |
| `dynamic` | Fastest healthy provider per request. The shipped default, and what V0.5.3 introduced. | full rank |

Hybrid drops exactly one term, and that is the whole design: **latency is the
only term that reorders two healthy providers.** Failure rate, consecutive
failures, breaker history, quality and budget pressure all still apply.

`dynamic_ranking: false` is honoured as `static` regardless of `ordering_mode`,
because it is the older and more explicit statement — someone who turned
ranking off in `config.yaml` must not be quietly overruled by a newer field
that defaults to `dynamic`.

### Priorities are rewritten 10, 20, 30 — not 1, 2, 3

`registry.reorder()` spaces them ten apart because the rank formula is
calibrated so **10 rank points equals one second of latency**
(`health.LATENCY_MS_PER_RANK_POINT`). Consecutive priorities would mean a
provider 100 ms slower than its neighbour outranked it — dynamic ordering would
silently become almost-static the first time a user pressed Move Up.

## 33. A disabled provider is now CONSTRUCTED

`enabled: false` used to skip construction entirely. It no longer does, and the
change is load-bearing: a provider that is not constructed cannot be listed in
Settings, cannot explain its own absence, and cannot be switched back on
without editing a file and restarting. The control centre would have had a
permanent blind spot exactly where a user needs to act.

A disabled provider is now treated like one with a missing API key — present,
self-explaining, and never selected. `monitor.available()` is False, so
`registry.candidates` skips it and, critically, `deepest_earliest` counts **no
history floor** for it. That last point is not incidental: a benched provider
contributing its declared depth would tell the chart that history reaches
further back than anything reachable, which is precisely the retry-forever bug
class V0.5.2 was built to eliminate.

## 34. The displayed state is DERIVED, never stored

`monitor.status()` answers "may this provider be used, and if not, why". It is
a *gate*, and the registry reads it as one. It is not quite what a person needs
to see, because it has no way to say "in rotation, but struggling": a provider
failing one request in three is `ok` to the gate and alarming to a human.

`health_state()` is the one-word answer for a human — `healthy`, `degraded`,
`offline`, `disabled`, `missing_key`, `rate_limited`, `circuit_open`,
`unavailable`, `unknown` (plus `testing` / `connecting`, which only the UI ever
emits). It is derived from the same counters on every read and **stored
nowhere**: a second stored copy of one fact is exactly how the adapter's
counters and the registry's breaker came to disagree before V0.5.3.

Every state is paired with a sentence (`HEALTH_TEXT`), and every place a state
is displayed must display the sentence. A coloured badge reading "degraded" and
nothing else tells a user they have a problem without telling them what it is,
which is the failure this entire panel exists to prevent.

## 35. Test Connection is end to end, on purpose

`test_connection()` runs a real SPY daily request over the last 21 days through
the **same** `fetch_history` a chart uses: transport, authentication, parsing,
canonical normalization, and semantic validation. A test that stopped at "the
socket opened" would pass for a provider whose response format had changed —
which is the failure most worth catching, because it is the one the chart
cannot route around. `tests/test_marketdata_control.py` drives exactly that
case: a provider answering a daily probe with *weekly* bars fails the test.

Daily, because it is the one interval every provider serves (Stooq has no
intraday at all). 21 days, because a probe that could legitimately return zero
bars cannot tell "working" from "broken".

The result is recorded on the health monitor like any other request. That is
intentional: a successful test genuinely *is* evidence the provider works, and
pretending otherwise would show a green test beside a red provider. A provider
that is out of budget is answered **without** a request — spending an allowance
to learn something already known is exactly the mistake `can_spend_request()`
exists to prevent.

## 36. Maintenance actions, and why they are a job

Eight actions: clear cache, rebuild cache, verify cache integrity, run
validation, run provider replay, run provider benchmark, run diagnostics,
re-measure capabilities.

They run on a background thread with a single slot and polled progress,
mirroring `UIServer.backtest_job`. Background because a capability
re-measurement takes **minutes** — a synchronous endpoint would hold a request
open past any client timeout, leaving the user unable to tell a slow job from a
dead one.

Two properties the V0.5.7 self-audit added:

- **The busy-slot refusal names what is in the way.** "'Re-measure
  capabilities' is still running" is actionable; "another action is running" is
  not.
- **A long action can be stopped.** Cancellation is *cooperative* and checked
  between units of work — `discovery.discover()` is not interruptible
  mid-provider, and killing the thread would leave that provider's counters
  half-written. A stopped job keeps what it measured and reports state
  `cancelled`, not `error`: nothing went wrong.

`CandleCache.verify()` is deliberately more than SQLite's `integrity_check`. A
cache can be structurally perfect and still unusable — that is exactly what the
V0.5.6 daily-bar defect was (two valid rows per trading day, nine and a half
hours apart, which `integrity_check` is delighted with and `validate_history`
correctly refuses). It also counts rows that would fail a read, because "your
cache is fine" and "your cache has 14 bad bars in 400,000" are different
answers.

## 37. Recommendations: advice, not observations

`recommendations()` returns severity-ordered entries that each name a **next
action**. The conditions, and why each is worth surfacing:

- **No usable provider** (critical) — charts cannot load; gives ordered
  recovery steps rather than a status.
- **One independent source** (warning) — and the count is honest: `_family()`
  collapses `yahoo` and `yfinance` into one, because they are two code paths
  over one upstream and one IP rate limiter. Anything treating them as two
  overstates a keyless install's redundancy by exactly one, which would be the
  single most misleading number on the page.
- **Quota nearly spent / spent** (info / warning) — names the alternative
  provider if one is configured, and says when it returns.
- **A provider that keeps failing** (info) — three or more breaker trips;
  suggests switching it off, noting the app already routes around it.
- **A rejected API key** (warning) — explains the stickiness and what fixes it.

A healthy multi-source install gets **nothing**. Advice that fires when nothing
is wrong is advice nobody reads.

## 38. QA mode — `data/faults.py`

Every failure mode this subsystem handles is documented, tested against canned
payloads, and impossible to *see*. "The chart falls back to yfinance when Yahoo
times out" was a sentence and a green test; nobody had ever watched it happen,
because making Yahoo time out on demand meant unplugging a cable.

A fault is armed against a provider name, consulted once inside
`HistoryAdapter.fetch_history`, and raises exactly the `ProviderError` subclass
the real condition would raise — so the health monitor, the breaker, the
ranking, the tier ladder, the diagnostics trace and the frontend state machine
all behave identically to the genuine article. A simulation that took a
shortcut past the error types would prove nothing about the paths it skipped.

Eight faults: `outage`, `timeout`, `rate_limit`, `quota`, `auth`, `latency`
(a real `sleep`, so the measured latency that demotes it is real), `empty`
(a weekend is not an outage), and `unusable` (bars that parse and that
validation must refuse — the V0.5.3 defect, made reproducible on demand).

Safe to ship because: `market_data.qa_mode` defaults False and the endpoints
**404** without it (404 rather than 403 — a 403 confirms the endpoint exists);
the hot path costs one boolean read; and nothing persists, so a simulated
outage cannot outlive the session that asked for it and be mistaken for a real
one later.

The cache-corruption drill deliberately operates on a **copy**. The recovery
path being demonstrated is identical whichever file it runs on, and running it
on a scratch copy lets a maintainer watch it work without gambling the history
they actually have. That is not a weaker test; it is the same test with the
blast radius removed.

## 39. What V0.5.7 found by attacking itself

Five defects, each found by a deliberate attempt to break a surface rather than
by a test written to confirm it worked:

1. **`mask("   ")` returned eight dots** — a whitespace-only value is an absent
   key everywhere else in the module, so the UI would have shown a key that did
   not exist.
2. **A repeated name in a provider order duplicated the provider** — three
   `"yahoo"` entries assigned it three priorities (last winning) and made
   `order()` report a chain longer than the registry.
3. **A hand-edited `marketdata.json` with `providers` as a LIST crashed
   startup** — `[].items()` raised out of the composition root. The app
   refusing to start because a *preferences* file was edited badly is precisely
   what `apply_control_state` promises not to be. Every field is now
   type-checked on the way in, with a parametrised regression test.
4. **The busy-slot refusal did not say what was busy** (see §36).
5. **A multi-minute action could not be stopped** (see §36).

Each has a regression test in `tests/test_marketdata_control.py` or
`tests/test_marketdata_endpoints.py`.

## 40. What V0.5.7 does NOT change

- **No change to how data is fetched.** The tier ladder, the adapters, the
  cache, validation and the capability table are untouched.
- **No change to trading behaviour.** Not one line of `engine/`, `risk/`,
  `broker/` or `coach/` moved.
- **No new runtime dependency.**
- **The shipped defaults are identical.** With no keys, no stored state and no
  `config.yaml` changes, the chain, the order and the behaviour are exactly
  V0.5.6's. Everything here is opt-in by clicking.

## 41. Finnhub requires a PAID plan for candles (live certification, 2026-07-27)

Found by running the first real live-provider certification of the keyed chain.
Twelve Data and Alpha Vantage authenticated and served. **Finnhub returned HTTP
403 to every request, with a brand-new, email-verified key copied straight from
its dashboard.** The app reported *"the API key was rejected"*, so the key was
regenerated — repeatedly, and it could never have helped.

### The measurement

Probed directly against the live API rather than inferred from documentation
(the docs site is a JavaScript app and cannot be read by a fetcher; the
behaviour can be measured in three requests):

| request | status | body |
|---|---|---|
| `/stock/candle` + invalid key | **401** | `{"error":"Invalid API key."}` |
| `/stock/candle` + no key | **401** | `{"error":"Please use an API key."}` |
| `/stock/candle` + valid free key | **403** | `{"error":"You don't have access to this resource."}` |
| `/quote` + invalid key | **401** | `{"error":"Invalid API key."}` |

**401 is the only status Finnhub uses for a key problem.** A 403 from
`/stock/candle` is therefore *positive evidence the key is good*: the server
authenticated it and then declined to serve the data. Finnhub moved historical
OHLC (and intraday resolutions) to its paid tiers; the free tier still covers
`/quote`, `/stock/profile2`, company news and symbol search.

### Why the app got it wrong

One line. `http_adapter._from_status` mapped **401 and 403 to the same failure**:

```python
if code in (401, 403):
    return ProviderAuthError(f"{self.provider_name} rejected the API key")
```

A second contributor was in `finnhub_provider._AUTH_MARKERS`, which contained
the substring `"api key"` *and* `"don't have access"` — so even the 200-body
path classified an entitlement message as a credential failure.

The result is the worst class of diagnostic error: **confidently naming the
wrong cause**, and naming one the user can act on. "Your key was rejected" has
an obvious remedy, the remedy is free to attempt, and it does nothing.

### What changed

- **`ProviderEntitlementError`** (`data/adapter.py`) — deliberately *not* a
  subclass of `ProviderAuthError`, so `except` clauses cannot silently conflate
  them again.
- **`_from_status` splits 401 from 403** for every keyed provider. This is just
  the standard meaning of the two codes — 401 "I don't know who you are", 403
  "I know exactly who you are and you may not have this" — so Twelve Data and
  Alpha Vantage get the correct diagnosis for free.
- **`_AUTH_MARKERS` narrowed** to wordings that mean the key itself is wrong,
  with `_ENTITLEMENT_MARKERS` checked *first*.
- **`FinnhubAdapter.verify_credentials()`** proves the key on `/quote`, which
  the free tier includes. That isolates the variable and turns a strong
  inference into a demonstrated fact:

      /quote 200  +  /stock/candle 403   ->  key good, plan too small
      /quote 401                         ->  key genuinely bad

  Generalised as `HistoryAdapter.can_verify_credentials` /
  `verify_credentials()`; the default is honest about having no cheaper
  endpoint rather than guessing.
- **`STATUS_PREMIUM_REQUIRED` / `HEALTH_PREMIUM_REQUIRED` / `KIND_ENTITLEMENT`**
  — its own status, its own displayed state, its own counter, and a
  recommendation that explicitly says *do not regenerate the key*.
- **`monitor.permanently_unusable`** — and `registry.deepest_earliest` now uses
  it instead of `disabled_reason` alone. This is the load-bearing part: Finnhub
  declares **180 days of 5-minute history** and on a free plan can serve none of
  it. Counting that floor tells the chart history reaches three times further
  than anything reachable, which is exactly the retry-forever bug class V0.5.2
  was built to eliminate. A *rejected key* was being counted the same way and is
  fixed by the same change.

### What did NOT change

**Authentication is not weakened.** A 401, an `"Invalid API key."` body and a
`"Please use an API key."` body all still produce `ProviderAuthError`, still set
`auth_failed`, and still bench the provider stickily — verified against the live
API, not only against canned payloads. The entitlement path is an *additional*
classification, never a fallback for a failed auth check: if the credential
check also fails, `_entitlement_result` reports the auth failure it can prove.

### Operationally

A premium-gated Finnhub is benched exactly like one with no key: never selected,
never retried, contributing no history floor, still listed and self-explaining,
and cleared automatically the moment a different key is pasted (a different key
may be on a different plan). **Charts are unaffected** — the keyless chain sits
in front of it.

Regression coverage: `tests/test_providers.py::TestFinnhubEntitlement` (19
tests), plus the 401/403 split across all three keyed adapters and four
control-centre tests for what the user is told.

## 42. Still true after V0.5.7, and still the biggest limitation

**With no API key configured there is exactly one real source.** Stooq is dead
(§28), and `yahoo` / `yfinance` share an upstream and an IP rate limiter. The
control centre makes this *visible* — the failover summary reports independent
sources, and a single-source install gets a warning naming a free provider to
add — but visibility is not redundancy.

**§41 makes this worse than it was.** Finnhub was the recommended free route to
an independent source, and on a free plan it can no longer serve history at all.
**Twelve Data (800 requests/day) is now the only free keyed provider that
delivers a genuinely independent intraday source**, with Alpha Vantage's 25/day
a distant second. The single-source recommendation now prefers whichever keyed
provider is actually usable rather than naming Finnhub by habit.
