# CHART_CERTIFICATION.md — V0.5.5 chart subsystem certification

**Date:** 2026-07-27 · **Branch:** `feature/providers` · **Not committed.**
**Scope:** the chart pipeline end to end — provider → transport → parsing →
validation → normalization → timezone → cache → paging → API → frontend state
→ TradingView conversion → rendering → indicators → viewport → pixels.
**No new features. No version bump. No trading-behavior change.**

This document is the honest report, not a victory lap. It states what was
proven, what was fixed, what is still broken and cannot be fixed here, and what
remains unverified. Read §7 before trusting any confidence number.

---

## 1. The finding that explains the report

> QQQ loads. SPY loads partially. IWM shows only volume bars. Diagnostics
> report every provider healthy. Backend believes every request succeeded. UI
> still renders blank charts.

All of it is one defect, and it is not in the data layer.

**lightweight-charts turns `autoScale` off permanently the first time the user
drags the right-hand price axis.** Nothing in this application ever turned it
back on — not a symbol switch, not a timeframe switch, not Reset view, not
Latest. The pinned price band therefore outlived every subsequent load. A $290
ETF drawn on a band left over from a $750 one has its candles entirely
off-screen, while the volume histogram — which lives on its own `vol` price
scale — keeps painting normally.

Everything downstream of that is consistent with the report:

| Symptom | Explanation |
|---|---|
| Diagnostics show every provider healthy | They were. The data arrived intact. |
| Backend believes the request succeeded | It did. `CH.data` was correct in every case. |
| `data-ch-state` said `complete` | It was complete. Rendering is not the same as being visible. |
| Console clean | No exception is thrown. Off-screen is a legal view. |
| Some symbols work, others don't | Only symbols whose price happens to fall inside the pinned band. |
| **A restart fixes it** | `autoScale` is not persisted. |

The user's four screenshots corroborate it precisely: an **identical** 480.00 →
660.00 price scale on QQQ 1D (≈$707), QQQ 1m, SPY 3m (≈$748) and IWM 1D
(≈$293). One band, four price levels, three of them off-screen.

**Reproduced before any code changed**, in a real headless browser against the
user's own `cache.db`:

```
=== PASS 1: fresh chart, no user interaction ===
  SPY   1d   ok           data=[629.28,760.4]  scale=[596.92,797.99]   auto=True
  IWM   1d   ok           data=[228.9,302.72]  scale=[210.68,323.89]   auto=True
=== PASS 2: after ONE drag on the price axis ===
  after drag:  auto=False scale=[290.07,297.03]
  SPY   1d   OFF SCREEN   data=[629.28,760.4]  scale=[290.07,297.03]   auto=False
  QQQ   1d   OFF SCREEN   data=[555.6,748.65]  scale=[290.07,297.03]   auto=False
  IWM   1d   CLIPPED 9%   data=[228.9,302.72]  scale=[290.07,297.03]   auto=False
```

**Why every test missed it.** Every check in `scripts/chart_check.py`, every
test in `tests/`, and the whole diagnostics subsystem asked *did the data
arrive*. None asked *can the user see it*. That question is now an invariant
(§4).

---

## 2. Defects found, fixed and verified

Ten. Each was reproduced failing, then re-run passing.

| # | Defect | Severity | Where |
|---|---|---|---|
| **1** | Price axis has no owner: a manual scale survives every switch and is unrecoverable without a restart | **Critical** | `index.html` |
| **2** | One 30-min closing stub bar condemns an entire Yahoo 1h frame as "wrong interval served" | **Critical** | `data/quality.py` |
| **3** | A `NaT` timestamp survives validation and 500s `/api/candles` | **High** | `data/base.py` |
| **4** | Null/NaN OHLC renders as *whitespace* — invisible candles under `state="complete"` | **High** | `index.html` |
| **5** | An out-of-order payload silently collapses to a single candle | **High** | `index.html` |
| **6** | A malformed/oversized indicator array wipes the entire chart to the error overlay | **Medium** | `index.html` |
| **7** | A string indicator value raises an uncaught error from the crosshair handler | **Medium** | `index.html` |
| **8** | A render failure is overwritten with `state="complete"` by the caller | **Medium** | `index.html` |
| **9** | `high < low` bars from a legacy provider draw as nothing | **Low** | `index.html` |
| **10** | The browser suites ran against the user's **real** data root, not a scratch one | **High (tooling)** | `scripts/chart_check.py`, `scripts/browser_check.py` |

### 1 — Price-axis ownership

Ownership now mirrors the time axis, which has had a single owner
(`chMoveViewport`) since V3.2.2:

- a genuine **symbol or timeframe switch resets to autoscale** — the previous
  band described different data and means nothing here;
- a **same-key refresh or history prepend preserves it** — a manual price scale
  is a deliberate act and must survive the data moving under it;
- **Reset view** and **Latest** reset it: they are the "get me back" controls,
  and Reset view previously reset only the *time* axis, which is why a user who
  pinned the price scale had no way back short of a restart;
- `chEnsurePriceVisible()` is the last-resort net. Dragging the price axis
  *scales around the centre*, so a user can never push the data they are
  looking at fully off-screen on purpose — **zero overlap therefore always
  means a stale band from other data**, which is safe and necessary to recover
  from. Deliberate zoom, a flat series and a partially clipped view are all
  left alone.

### 2 — Interval conformance

`_interval_stats` judged conformance on the strict **tightest** gap. Yahoo
closes each US session with a 30-minute stub bar (15:30 → 16:00 ET, the closing
auction). Measured against the user's real cache:

```
tf=  60  n=2180  min=0.500  sub-interval gaps=1  top=[(1.0, 1862), (18.0, 244), ...]
         the one 0.5 gap: 2026-07-24 19:30 UTC -> 20:00 UTC  (15:30 -> 16:00 ET)
```

One bar in 2,180 set `usable = False`, scored the frame 0 and charged the
provider a validation failure — on **every** 1h request that included the last
completed session, and identically on `yfinance`, which serves the same
upstream. Conformance is now the **median of the within-session gaps** (those
under two intervals; wider ones are session breaks and carry no interval
information). This still rejects every defect the strict test caught, because a
genuinely wrong interval is wrong in the *bulk* of its spacings:

| Served for a 1h request | Median within-session gap | Verdict |
|---|---|---|
| 1h with a closing stub | 1.0 | accepted ✅ (was rejected) |
| 30m | 0.5 | rejected ✅ |
| 90m (a real Yahoo substitution) | 1.5 | rejected ✅ |
| 1d (no within-session gaps → falls back to the tightest) | 24.0 | rejected ✅ |

`min_gap` is still reported. It is a useful statistic; it is no longer a veto.

### 3 — `NaT` timestamps

`pd.to_datetime(..., errors="coerce")` in the HTTP adapters turns any malformed
provider timestamp into `NaT`, and `http_adapter.localize` maps a DST fall-back
ambiguity to `NaT` **by design**. Neither was dropped, so one bar reached
`int(ts.timestamp())` at the end of `/api/candles` and 500'd the whole
response. `validate_candles` — the choke point that already exists for exactly
this — now drops them.

### 4–9 — The display layer

The unifying discovery: **lightweight-charts does not treat a bar with null
OHLC as an error. It treats it as whitespace.** `setData` accepts it, the
series draws nothing, the price scale ignores it, and the render reports
success. The old `chEnsureMonotonic` only ever checked bar *times*.

It now (a) drops undrawable bars — non-finite OHLC and `high < low`, the same
invariants the backend enforces; (b) **sorts** out-of-order bars instead of
discarding everything after the first (a fully reversed payload used to render
one candle and report success); and (c) a payload that sanitizes down to
nothing is an explicit failure state, not a blank canvas reporting `complete`.

Indicator reads go through `chInds()` and are bounded by both array lengths, so
a broken accessory can no longer take the primary content down with it; the
legend formats only finite numbers; and `loadChart` now honours
`chRenderData`'s return value instead of stamping `complete` over a failure.

### 10 — The regression suite was not isolated

Found while re-running the suite: `chart_check.py` and `browser_check.py` both
launch the app with `cwd=scratch`, which isolated them until **V0.4.4 moved the
storage root off the CWD** to `%LOCALAPPDATA%\OptionsPilot`. Every run since has
read *and written* the user's real `cache.db`, journal and logs — directly
against both files' own docstrings ("Never touches the real data/ directory")
and against `CLAUDE.md`'s rule about the runtime data root.

Two consequences, both observed during this pass. The suite contended for
SQLite locks with the user's running copy of the app, which showed up as an
intermittent 30-second timeout on the very *first* chart load — three
consecutive runs died there while the backend served the same request cold in
under a second. And the suite's outcome depended on what an earlier run
happened to leave in the cache, which is the opposite of what a regression
suite is for. The user's real cache grew from 66,927 to 69,217 bars across this
session's runs.

Both now pass `OPTIONSPILOT_HOME` — the documented override — into the server
subprocess. `chart_check` is green and reproducible with it.

---

## 3. What was attacked and held

41 hostile scenarios driven through the **real renderer** in a headless
browser, in two waves (the second written specifically to attack the first
wave's fixes). All 41 pass.

| Class | Scenarios | Result |
|---|---|---|
| Bar-count extremes | 1, 2, 3, 200 000 bars; a payload shrinking 5 000 → 3 | pass |
| Price extremes | flat (zero range), 1e9, sub-penny, far-future timestamps | pass |
| Ordering | duplicates, fully reversed, mixed usable/unusable | pass |
| Geometry | `high < low`, negative volume, null OHLC | pass |
| Type confusion | `candles` null/object/string; `time` as string; `indicators` null/array; string indicator values | pass |
| Indicator alignment | series shorter than candles; series longer than candles | pass |
| Transport | 200 OK with invalid JSON, HTTP 500, HTTP 502 HTML body | pass |
| Backend outcomes | `failed`, `exhausted`, `empty` | pass |
| Interaction | 24-symbol burst, timeframe flip-flop, resize, tab away/back | pass |
| Races | symbol switch during an in-flight history load | pass |
| Recovery | corrupt `localStorage`, deep scroll-back to the provider floor | pass |
| Input | 64-char ticker, `../../etc/passwd`, `<script>` tag, `BRK.B` | pass |
| Price-scale fixes under attack | manual scale survives 5 refreshes and a history prepend; resets on a switch | pass |

**Two invariants now hold across all of them:**

- **Visibility** — candles in the visible time window intersect the visible
  price window, or an explicit error state is showing. Never both false.
- **Terminal state** — `data-ch-state` always reaches `complete` / `cached` /
  `empty` / `exhausted` / `failed`. No scenario left a spinner or a
  non-terminal state (`loading` / `receiving` / `rendering`).

---

## 4. Automated coverage added

`scripts/chart_check.py` **42 → 48 checks**, built around the invariant the
file was missing:

> The candles in the visible time window must intersect the visible **price**
> window.

| # | Check |
|---|---|
| 43 | a freshly loaded chart's candles are inside the visible price band |
| 44 | a symbol switch recovers from a manually pinned price scale *(the reported bug, in the form a user hits it)* |
| 45 | a timeframe switch recovers the price scale |
| 46 | Reset view restores price autoscale, not just the time axis |
| 47 | Latest restores price autoscale |
| 48 | a same-key refresh **preserves** a deliberate manual price scale *(the converse — the fix must not fight the user)* |

`pytest` **1232 → 1238**: four in `tests/test_quality.py` (the hourly closing
stub bar accepted; 30m, 90m and coarser-than-requested data still rejected —
including a symmetry check that the median cannot be gamed by one good gap) and
two in `tests/test_base.py` (`NaT` dropped; an all-`NaT` frame yields empty, not
an error).

---

## 5. Verification matrix

| Gate | Result |
|---|---|
| `pytest` full suite | **1238 passed**, 0 failed |
| `scripts/chart_check.py` (real headless browser) | **48/48**, 0 console errors |
| `scripts/marketdata_stress.py` | **88/88** scenarios in 41.4 s |
| `scripts/browser_check.py` | 9/9 tabs, 0 console errors |
| `scripts/check_html_ids.py` | 167/167 `$("id")` references resolve |
| `scripts/check_docs.py` | cross-references, counts and version in sync |
| Adversarial wave 1 (19 scenarios) | 19/19 |
| Adversarial wave 2 (22 scenarios) | 22/22 |

Each of the ten defects was demonstrated **failing before** the fix and
**passing after**, in the same harness.

---

## 6. Recovery matrix

| Condition | Behaviour |
|---|---|
| Pinned price scale from another symbol | Autoscale restored on switch; net catches any residual zero-overlap |
| Provider outage | Typed error → next provider in the ranked chain |
| Repeated failures | Circuit breaker trips, half-open probe restores automatically |
| Rate limit / quota | Checked **before** the request; budget persists across restarts |
| Timeout | Bounded fetch (15 s) → recoverable error overlay → auto-retry |
| Superseded request (rapid switching) | Aborted; only the newest generation applies |
| Malformed bars | Dropped at the backend *and* the display layer; all-bad → explicit failure |
| Unparseable timestamp | Dropped in `validate_candles` (was: 500) |
| Wrong interval served | Rejected on the median spacing; provider demoted |
| Corrupt `cache.db` | Quarantined and rebuilt at startup |
| Corrupt `localStorage` | Recovers to a working chart |
| Start of history reached | Stated as a fact from the backend; requesting stops |
| Renderer exception | Error overlay + terminal `failed` state (was: reported `complete`) |

---

## 7. Residual risk — read this before trusting §5

**Limitations that cannot be fixed in this codebase:**

1. **Stooq is permanently unusable.** It now answers every request with a
   JavaScript proof-of-work challenge (`"This site requires JavaScript to
   verify your browser"`), verified live from a clean client on both
   `stooq.com` and `stooq.pl`. A `urllib` client cannot satisfy it, and this
   project will not attempt to circumvent a site's access controls. The adapter
   detects and refuses it correctly — but **with no API keys configured this
   leaves Yahoo as the only real source**, reached by two code paths (`yahoo`,
   `yfinance`) that share one upstream and therefore one failure domain. The
   diagnostics dashboard already reports Stooq at 0% success; it is honest, but
   the user should read it as "your only fallback is gone." A free Finnhub or
   Twelve Data key is the only route to genuine provider independence today.

2. **Yahoo rate-limits by IP.** A `429` was observed from a clean client during
   this pass. It is handled correctly (typed error, breaker, failover) — but
   with Stooq gone there is nothing keyless to fail over *to*.

**Not verified, and therefore not certified:**

3. **No adapter has been exercised against its real API with a real key.**
   Finnhub, Twelve Data and Alpha Vantage are still tested only against canned
   payloads. Their response shapes are as *documented*, not as *observed*. This
   was already the standing V0.5.4 caveat and this pass did not change it.
4. **Market-hours behaviour is unverified.** Everything here ran with the US
   market closed. The forming candle, live tail updates and the intraday
   refresh cadence were exercised with synthetic ticks, not real ones.
5. **The 84-item manual QA (`docs/QA_MARKET_DATA.md`) has still not been run.**
6. **DPI changes, monitor switches, and OS sleep/wake** are not covered — no
   automated harness can drive them, and they were not tested by hand.
7. **Cross-provider agreement is measured but not enforced.**
   `quality.disagreement()` computes the median relative close difference, and
   the diagnostics replay compares providers on demand, but nothing flags a
   disagreement automatically during normal operation. Two providers'
   dividend-adjusted and unadjusted series can still be stitched by the cache
   without comment. This is a real gap; it is out of scope for a
   failure-elimination pass and wants its own design.
8. **Indicator values at a history-paging join** are computed from the start of
   the *fetched window*, not the full series, so an EMA can kink slightly at
   the seam. Standard behaviour for paged charts; noted, not fixed.

**Confidence.** For the specific failure the user reported — a chart that
renders blank or volume-only while the backend reports success — the mechanism
is identified, reproduced, fixed and covered by a permanent regression check
that fails without the fix. That class is closed. For the chart subsystem as a
whole, the honest statement is narrower: **no reproducible blank chart, silent
failure or infinite spinner survives 41 adversarial scenarios, 48 browser
checks and 1238 unit tests** — and items 3–8 above are exactly where the next
one would come from.

---

## 8. Manual QA checklist (cannot be automated)

Run with the **market open**:

- [ ] Load SPY 1m. The forming candle updates in place; volume grows; no flicker.
- [ ] Drag the price axis, then switch symbol. Candles are visible. *(D1)*
- [ ] Drag the price axis, then press **R**. Both axes reset. *(D1)*
- [ ] Drag the price axis, then wait through two refreshes. Your scale is still there.
- [ ] Load a 1h chart after 16:00 ET. It renders (this was defect 2).
- [ ] Scroll back on 5m until the "start of available history" pill appears; keep scrolling. No further requests.
- [ ] Toggle EMA/VWAP/BB/RSI/MACD in every combination. The main chart never recentres.
- [ ] Draw a trendline, switch timeframe, switch back. It is still there and still anchored.
- [ ] Sleep the machine for 5 minutes, wake it. The chart recovers without a reload.
- [ ] Move the window between monitors of different DPI. No blurring or misalignment.
- [ ] Toggle fullscreen (**F**) and back.
- [ ] Open Help ▸ Diagnostics mid-session. Provider health, cache stats and traces all render.

---

## 9. Files changed

| File | Change |
|---|---|
| `optionspilot/ui/static/index.html` | price-axis ownership (`chAutoScalePrice`, `chPriceStranded`, `chEnsurePriceVisible`); payload sanitizer rewritten (`chBarUsable`, sort-not-discard, all-undrawable → failure); `chInds()`; bounded `chSeriesData`; finite-only legend; render verdict honoured; terminal state on renderer exception; empty-payload guards |
| `optionspilot/data/quality.py` | `_interval_stats` judges the median within-session gap, not the strict minimum |
| `optionspilot/data/base.py` | `validate_candles` drops `NaT` index entries |
| `scripts/chart_check.py` | +6 price-axis checks (43–48) and the `chart_visible()` invariant helper; **runs against a scratch `OPTIONSPILOT_HOME`** (defect 10) |
| `scripts/browser_check.py` | runs against a scratch `OPTIONSPILOT_HOME` (defect 10) |
| `tests/test_quality.py` | +4 interval-conformance tests |
| `tests/test_base.py` | +2 `NaT` tests |

No changes to the trading path, the engine, risk, the broker, or any
`managed_by` boundary. The paper-trading-only guarantee is untouched.

---

# Part II — V0.5.6: the 1D validation wall and viewport corruption

**Date:** 2026-07-27 (same session, after two further reproducible bugs were
reported against the V0.5.5 build). **Not committed.**

## 10. Root cause: "the cached bars failed validation and were discarded" on 1D

**Two independent defects stacked. The first produced bad data; the second made
it permanent.**

### 10a. Three providers, three daily-bar conventions, one cache key

The cache is keyed `(symbol, timeframe, ts)`. A daily bar's *identity* is its
session date — but a date only becomes an instant relative to a timezone, and
every adapter used whatever its upstream happened to emit:

| Source | Daily stamp for 2026-07-24 | in ET |
|---|---|---|
| `yahoo` (v8 chart JSON) | `13:30 UTC` | 09:30 — the session open |
| `yfinance` | `04:00 UTC` | 00:00 — exchange midnight |
| `stooq`, `http_adapter` (keyed providers) | `00:00 UTC` | 19:00 — **the previous day** |

Those are three different rows for one trading day. Measured on the real
`cache.db`:

```
SPY 1440: n=6517  times=[('13:30', 2145), ('04:00', 2144), ('05:00', 1114), ('14:30', 1114)]
          min gap = 0.396 days        (04:00 -> 13:30 is 9.5 hours)
JPM 1440: n= 413  same pattern        3,258 trading days occupying 6,517 rows
```

A frame whose tightest spacing is 0.40 intervals is not a 1-day frame, so
`quality.validate_history` correctly reported **"bar spacing does not match 1d —
wrong interval served"**, marked it unusable and discarded it. Validation was
working exactly as designed; the data was genuinely wrong.

This was **not** caused by the V0.5.5 changes: the cache snapshot taken at the
start of that session already showed the dual convention, and the `provider`
column attributes rows to both `yahoo` and `yfinance`. It also explains why only
1D was affected — intraday timestamps are unambiguous epochs.

### 10b. Validation ran *after* the ladder had committed, so there was no way back

`_settle()` is the last step of every request. When the disk tier's frame failed
validation there, the result became `outcome=failed` with a message — but the
ladder had already passed the provider tiers, **and the offending rows stayed on
disk**. So:

- the providers were never consulted, though they would have answered fine;
- the next request re-read the same rows and failed identically;
- **Retry did exactly the same thing, forever.**

That is the whole of "recovery never completes". There was no way past it short
of deleting `cache.db` by hand.

## 11. The fixes

**1. One convention, enforced at the single boundary every adapter passes
through.** `base.session_index()` snaps daily-and-coarser timestamps to **00:00
in the exchange's timezone**, applied in `HistoryAdapter.fetch_history`. The two
date-only sources (`stooq_provider._parse_csv`, `http_adapter.localize`) now
localize the provider's *date* into that zone instead of stamping UTC midnight.

Exchange midnight, not UTC midnight, because the chart labels every timestamp
through an America/New_York formatter (V3.3 Issue 2): 00:00 UTC reads as 19:00
on the *previous* day, so the keyed providers would have drawn every daily bar
one day early. That latent off-by-one is fixed by the same change.

Verified live — both adapters, same instant:

```
YahooChartAdapter ['2026-07-22T04:00:00+00:00', ..., '2026-07-27T04:00:00+00:00']
YFinanceAdapter   ['2026-07-22T04:00:00+00:00', ..., '2026-07-27T04:00:00+00:00']
AGREE: True
```

**2. A cache migration that repairs installs already poisoned.** Fixing the
adapters stops new divergence and does nothing for rows already written.
`cache._migration_3` rewrites every daily+ row onto the convention and collapses
duplicates (attributed rows beat unattributed v1 rows; newer fetches win).
Rewriting rather than deleting keeps decades of end-of-day history — the prices
were never wrong, only the instants they were filed under. On the real cache:

```
17,957 daily+ rows collapsed to 11,831 in 0.20s
SPY  6,517 -> 3,259     min gap 0.396 -> 1.000 days
IWM  1,220 ->   814     JPM 413 -> 208
63,184 intraday rows untouched
```

**3. A rejected tier declines instead of aborting the ladder.** Disk tiers now
validate *before* committing (`_validated`), and on failure `_quarantine` purges
the bad rows for that `(symbol, timeframe)`, invalidates the memo, and the
ladder **falls through to the providers**, which re-download and re-store. The
old check in `_settle` remains as a loudly-logged backstop.

Verified against a cache re-poisoned *after* migration, so only the runtime path
could save it:

```
re-poisoned JPM daily: 208 shadow rows added
1st request: outcome=live bars=205 provider=yahoo quarantines=1
2nd request: outcome=memo bars=205 provider=yahoo quarantines=1
JPM daily rows now: 206      (clean)
```

No user action. No Retry. The quarantine count is exposed in
`health()["cache"]["quarantines"]` — a number that keeps climbing means
something upstream is writing unusable bars, which is a defect to chase rather
than a cost to absorb.

## 12. Root cause: viewport / zoom corruption

The time axis has had a single owner since V3.2.2, but "owned" only said where a
move *comes from* — **nothing defined what a legal viewport is**, so every
programmatic move was free to leave two candles on screen:

- `chScrollToLatest` carried the previous view's width onto a **new symbol** —
  the reported "switching symbols keeps a strange zoom level";
- `chApplyFocal` maps a *date window* onto a new resolution, and a 2-hour window
  is 24 bars at 5m but 2 at 1h, so 5m→15m→1h→1d **ratchets narrower every step**
  with no way back but Reset;
- a window resize re-derives bar spacing with no floor at all.

Measured before the fix (deep zoom, then shrink the window): **4 bars of 281
visible, logical width 2.3.** lightweight-charts accepts all of it.

### Explicit viewport invariants (enforced in `chMoveViewport`)

| | Invariant |
|---|---|
| **V1** | the logical range is finite and has positive width |
| **V2** | at least `CH_MIN_VISIBLE_BARS` (12) bars are on screen, or all of them when the series is shorter |
| **V3** | the range intersects the data |
| **V4** | a **symbol** switch starts from a sane width, never the previous instrument's zoom |
| **V5** | a **same-key** refresh preserves the user's width — that is what Auto Follow is *for* |
| **V6** | candles intersect the visible **price** band (V0.5.5, checks 43–48) |

These bind **programmatic** moves only. A user's own wheel-zoom or drag reaches
the library directly and is never clamped: deliberately zooming to three candles
is a legitimate thing to want, and an app that fights it is worse than one that
occasionally shows too few.

`CH_MIN_VISIBLE_BARS` is now the **one** floor constant — `chApplyFocal`'s
private `MIN_BARS = 12` references it instead of duplicating it, because two
floors that disagree let the clamp settle below what a timeframe switch had
deliberately widened to.

**`CH.restoringViewport` became a depth counter, not a boolean.** Guarded moves
overlap in practice, and with a boolean the first `finally` to land cleared the
flag while another move was still in flight — the next range change was then
read as a user pan, which re-armed history loading and fired a spurious
`/api/candles` request.

### What was deliberately NOT done

**The resize path does not re-clamp.** It was implemented, and then removed.
Dragging the price axis changes the width of its own labels, so the canvas
resizes by a few pixels mid-gesture; re-asserting the viewport there
re-invalidated the chart and snapped the user's manual price scale back —
`chart_check`'s "overlay tracks a vertical price-axis drag" caught it directly
(the level moved 4px instead of 140). It also let a resize masquerade as a
scroll and fire a real request.

What is lost is small and self-correcting: shrinking the window while already
zoomed into a handful of bars leaves the view narrow until the next genuine
viewport move restores it. Breaking manual price scaling to buy that back is a
bad trade. **This is the one viewport violation the harness still reports**, and
it is intentional.

## 13. Verification (Part II)

| Gate | Result |
|---|---|
| `pytest` | **1257 passed** (1232 → 1257 across the whole session, +25) |
| `scripts/chart_check.py` | **65/65**, 0 console errors (42 at session start) |
| Browser matrix: **10 symbols × 11 timeframes** | **110/110 cells**, 0 failures — run against a copy of the real cache forced back to schema v2 so the migration executed |
| Viewport invariant harness (35 operations) | 34/35 — the one intentional exception above |
| `scripts/marketdata_stress.py` | 88/88 |
| `check_docs`, `check_html_ids`, `browser_check` | green |

The matrix asserts, per cell: a terminal load state, no validation screen, no
error overlay, candles present *and* inside the visible price band, a usable
number of visible bars, and indicator arrays 1:1 with candles.

**Proof the new checks fail without the fixes:** running `chart_check` with
`index.html` reverted produced exactly the four price-scale failures (44–47)
while every other check passed — and the 1D check still passed there, because
that fix lives in the backend. Each check fails for its own reason and no other.

## 14. Tests added (Part II, +19)

- `tests/test_base.py::TestSessionIndex` (6) — the Yahoo/yfinance collision
  converging, exchange-vs-UTC midnight, idempotence, both sides of a DST
  boundary, every within-session instant collapsing to one bar, and the explicit
  statement that a UTC-midnight stamp *is* the previous session and cannot be
  rescued downstream (which is why the date-only sources were fixed at source).
- `tests/test_cache.py::TestMigration3CollapsesDailyConventions` (6) — two rows
  per day become one, survivors exactly one day apart, landing on exchange
  midnight, the attributed/newer row winning, intraday untouched, and a clean
  cache surviving unchanged.
- `tests/test_marketdata_service.py::TestAnUnusableCacheHealsItself` (6) — falls
  through to providers instead of failing, quarantines so the second request
  cannot inherit the bad bars, never requires Retry, records the quarantine
  where an operator will see it, still serves a *healthy* cache from disk, and
  refuses to hand an unusable stale frame to the chart.
- `tests/test_providers.py`, `tests/test_stooq_provider.py` — the two tests that
  asserted the old UTC-midnight convention were **corrected, not deleted**, and
  now also assert that the calendar date survives.
- `scripts/chart_check.py` **49–54** — resize floor, timeframe switch out of a
  deep zoom, symbol switch out of a deep zoom, Auto Follow not carrying a zoom
  across symbols, Auto Follow *preserving* it on the same symbol, and every
  symbol rendering on 1D with no validation screen.

## 15. What this pass did NOT do

Stated plainly rather than implied. The brief asked for more than the two
reproducible bugs, and the following were **not implemented**:

1. **API key management UI.** The backend half already exists and is unchanged:
   keys resolve environment-first, a missing key disables only that provider and
   reports a signup link, and `ProviderConfig.as_dict()` redacts by default so a
   key cannot reach diagnostics, exports or reports. What does **not** exist is a
   Settings ▸ Market Data Providers panel to paste a key, persistence of a
   pasted key, or `********abcd` masking in the UI. Configuring a key today
   means an environment variable or `config.yaml`.
2. **Provider health dashboard expansion.** Help ▸ Diagnostics already shows
   provider, status, rank, priority, latency, success rate, breaker trips, quota,
   last success and served intervals. The additional columns requested
   (enabled/configured split, current capability, current availability) were not
   added.
3. **Cross-provider validation.** Still measured (`quality.disagreement`,
   diagnostics replay) and still not enforced during normal operation — carried
   forward from Part I §7.7.
4. **A permanent history-loading stress matrix.** Rapid scrolling, wheel-holding
   and jumping between oldest and newest were exercised through the throwaway
   viewport harness; they were not turned into committed coverage.

Items 1 and 2 are feature work rather than failure elimination; item 3 wants its
own design. All four are tracked in `docs/TODO.md`.

## 16. Confidence (Part II)

For the two reported bugs: **high, and demonstrated rather than argued.** Both
were reproduced from the real `cache.db`, root-caused to a specific mechanism,
fixed at that mechanism, and covered by tests that fail without the fix. 110/110
matrix cells and 65/65 browser checks pass, including **JPM 1D** — the exact
cell in the screenshot.

Residual risks are unchanged from Part I §7, and one is worth restating here:
with no API key configured the app has **one** real data source, and the 1D
defect existed precisely *because* two code paths over that one source disagreed
about a timestamp. A second, genuinely independent provider would have made the
disagreement visible years earlier.
