# MARKET_DATA.md — the market-data subsystem (V0.5.2)

The design, the measurements behind it, and the root causes it eliminates.
Read this before changing anything under `optionspilot/data/`.

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
5. **A user-facing diagnostics panel** in Settings over the existing endpoint.
