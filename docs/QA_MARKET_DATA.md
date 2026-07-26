# QA_MARKET_DATA.md — manual QA for the market-data subsystem (V0.5.2)

84 checks covering what automation cannot: how the chart *looks and feels* when
data is slow, missing, stale, or impossible. `pytest`, `marketdata_stress.py`
and `chart_check.py` cover the logic; this covers the experience.

**Setup**

```powershell
.\scripts\verify.ps1                      # must be green before starting
python scripts/marketdata_stress.py --live   # confirms the real chain is up
.\scripts\dev.ps1                         # or: python -m optionspilot serve --port 8787 --no-loop
```

Open DevTools. Keep the **Console** and **Network** tabs visible throughout —
several checks are "and no console errors", which is only observable there.
Note whether the **US market is open**; §H depends on it, and a few checks read
differently when it is closed.

Mark each: **P** pass · **F** fail · **N/A** (say why) · **?** unclear.
Anything that is not a clean P is worth writing down verbatim.

---

## A. First launch and cold start (1–8)

| # | Check | Expect |
|---|---|---|
| 1 | Delete `%LOCALAPPDATA%\OptionsPilot\data\cache.db*`, launch, open Charts | A chart appears within a few seconds; no permanent spinner |
| 2 | Console during that first load | No errors |
| 3 | Network tab: the first `/api/candles` call | One request, 200, completes < 3s |
| 4 | Its response body | `outcome: "live"`, `provider` named, `quality: 100` |
| 5 | Close the app and relaunch immediately, open Charts | Chart paints noticeably faster (warm cache) |
| 6 | That second launch's `/api/candles` response | `outcome` is `cache` or `live` — never `failed` |
| 7 | `#ch-main` in the Elements panel | Carries `data-ch-state="complete"` (or `cached`) |
| 8 | Launch with the machine **offline**, open Charts | Either a red overlay with a clear reason, or cached bars with the stale banner — **never** a blank canvas or an endless spinner |

## B. Symbols (9–17)

| # | Check | Expect |
|---|---|---|
| 9 | Load SPY, QQQ, IWM, DIA in turn | Each renders real bars |
| 10 | Load a mega-cap: NVDA, AAPL, MSFT | Each renders |
| 11 | Load a low-liquidity small cap | Renders, possibly with visible gaps — gaps are normal, not an error |
| 12 | Load `BRK.B` (dotted class share) | Renders — the hyphen variant is tried automatically |
| 13 | Load `ZZZZZZ9` (nonsense) | Red overlay, message names the symbol as possibly invalid, Retry button present |
| 14 | Click Retry on that overlay | Retries; overlay stays (still invalid), no console error |
| 15 | From the invalid symbol, type QQQ and press Enter | Recovers cleanly to a real chart |
| 16 | Load a recently-IPO'd symbol on 1mo | Shows only the bars that exist; no error for the pre-listing period |
| 17 | Load an ETF and a single stock back to back 10x | All 20 loads render; no leak, no slowdown |

## C. Timeframes (18–27)

| # | Check | Expect |
|---|---|---|
| 18 | Step through all 13 timeframes on SPY | Every one renders real bars |
| 19 | 3m, 10m, 2h, 4h specifically (these are resampled, not native) | Bar spacing looks right; no duplicated or half-height candles |
| 20 | 4h on a multi-day range | Two bars per session, an overnight gap — **correct**, not a defect |
| 21 | 1w and 1mo | Years of history; month bars of uneven length are fine |
| 22 | Switch 1m → 1d → 1m rapidly, 10 times | Settles on the last one; no stuck spinner |
| 23 | Switch timeframes while a load is in flight | The superseded load is abandoned silently; no error toast |
| 24 | After each switch, check the legend | O/H/L/C values update to the new resolution |
| 25 | Switch tf while zoomed into a specific date | Roughly the same date region stays centred (no jump to newest) |
| 26 | Switch tf, then check `data-ch-state` | Reaches `complete`, not stuck at `loading` |
| 27 | Compare a 1h candle against the same hour on 1m (sum of 60) | Open/high/low/close agree |

## D. History paging — the core of this release (28–40)

| # | Check | Expect |
|---|---|---|
| 28 | On SPY 1d, scroll left repeatedly | Older bars keep loading; "Loading history…" pill appears and clears |
| 29 | Keep scrolling on 1d until it stops | Years of history load (1990s eventually) |
| 30 | **On SPY 5m, scroll left until it stops** | Stops at ~59 days back with **"◄ Start of available history · 5m data starts <date>"** |
| 31 | That pill's date | Matches roughly 59 days before today |
| 32 | With Network tab open, keep scrolling **after** that pill appears | **Zero further `/api/candles` requests.** This is the headline fix — before, it retried forever |
| 33 | Repeat #30 on 1m | Stops at ~7 days back with the same honest message |
| 34 | Repeat #30 on 1h | Stops at ~729 days back |
| 35 | While scrolling back, watch the bars already on screen | They do **not** move when older bars are prepended |
| 36 | Scroll back a long way, then switch timeframe and back | No error; history re-pages on scroll (it is not expected to be retained across a switch) |
| 37 | Scroll back, then let the auto-refresh tick (~7s intraday) | Paged-in history is **retained**; the chart does not collapse to the base window |
| 38 | Scroll back with a drawing on screen | The drawing stays anchored to its bars |
| 39 | Scroll back fast, releasing mid-load | Viewport stays where you left it; no snap-back |
| 40 | After reaching the start of history, switch symbol and back | The pill resets; scrolling works again on the new symbol |

## E. Live updates (41–48) — *market hours*

| # | Check | Expect |
|---|---|---|
| 41 | Watch SPY 1m for two minutes | The forming candle updates every ~7s |
| 42 | Watch for a bar boundary | A new bar appears; the viewport does not jump |
| 43 | The countdown pill | Counts down to the bar close, never negative |
| 44 | Pan away from the newest bar and wait for a refresh | The viewport stays where you put it |
| 45 | Turn Auto Follow on | The newest bar is kept in view across refreshes |
| 46 | Pan manually with Auto Follow on | It switches off; clicking Latest re-enables it |
| 47 | Leave the chart open 15 minutes | Memory in Task Manager plateaus; no creeping growth |
| 48 | Switch browser tabs away 5 minutes and back | Refreshes promptly on return |

## F. Degraded and failure states (49–60)

Use DevTools **Network → throttling** and **offline** for these.

| # | Check | Expect |
|---|---|---|
| 49 | Set Network to "Slow 3G", load a new symbol | Loading overlay, then either data or a clear timeout message — never an endless spinner |
| 50 | Set Network to "Offline", switch symbol | Red overlay with "Couldn't reach the data service", Retry present |
| 51 | Go back Online, click Retry | Recovers to a real chart |
| 52 | Offline, but on a symbol whose bars are cached | Yellow "Live data unavailable — showing cached bars through …" banner, chart still drawn |
| 53 | That banner while the market is **closed** | **Suppressed** — the last session's bars *are* the freshest bars |
| 54 | Go back online, click "Retry live data" | Banner clears |
| 55 | Flap offline/online repeatedly | The banner does not flicker on every tick for unchanged data |
| 56 | Offline with **no** cache (fresh symbol) | Red overlay with a reason, not a blank canvas |
| 57 | Stop the backend process entirely, then interact | Overlay appears within ~15s (bounded), not a permanent spinner |
| 58 | Restart the backend | The chart recovers by itself — **no app restart needed** |
| 59 | Console throughout §F | No uncaught errors |
| 60 | `data-ch-state` in each failure case | `failed` for real failures, `cached` for stale, `exhausted` at the start of history — never wrong |

## G. Cache and storage (61–68)

| # | Check | Expect |
|---|---|---|
| 61 | Load several symbols, then check `data\cache.db` size | Grows |
| 62 | Close the app cleanly, relaunch | Charts paint from cache noticeably faster |
| 63 | **Corrupt the cache**: with the app closed, overwrite `cache.db` with random bytes; launch | The app **starts normally** and charts work (cold) |
| 64 | Look in the data directory after #63 | A `cache.db.corrupt-<timestamp>` file exists — the original was quarantined, not deleted |
| 65 | Delete `cache.db` entirely and launch | Rebuilds silently; no error |
| 66 | Kill the app (Task Manager) mid-chart-load, relaunch | No corruption; charts work |
| 67 | `/api/diagnostics/marketdata` → `cache.schema_version` | Is 2 |
| 68 | Keep an old (pre-upgrade) `cache.db` and launch this build | Opens, migrates, keeps its bars (`rebuilds: 0`) |

## H. Market state (69–74)

| # | Check | Expect |
|---|---|---|
| 69 | During regular hours | Live updates flow; countdown visible on intraday |
| 70 | Pre-market with Ext **off** | Only RTH bars |
| 71 | Pre-market with Ext **on** | Pre-market bars appear, shaded differently |
| 72 | After hours with Ext on | After-hours bars appear and are shaded |
| 73 | Ext toggle on a **daily** chart | Disabled — daily bars are RTH aggregates |
| 74 | On a weekend or holiday | Last session's chart, no stale banner, no error |

## I. Diagnostics (75–79)

| # | Check | Expect |
|---|---|---|
| 75 | Open `/api/diagnostics/marketdata` in a browser tab | Valid JSON, `available: true` |
| 76 | `providers[]` | Lists yahoo, yfinance, stooq with health, latency and breaker state |
| 77 | `requests.success_rate` after normal use | ≥ 0.95 |
| 78 | `traces[0]` | Names the symbol, timeframe, outcome, provider and the attempts made |
| 79 | Cause a failure (go offline, load a new symbol), then re-check | The failure appears as a trace with the reason recorded |

## J. Interaction with the rest of the app (80–84)

| # | Check | Expect |
|---|---|---|
| 80 | Run a scan (Dashboard) while a chart is open | Both work; the chart does not stall waiting on the scan |
| 81 | Open the Trade tab's chart slot | Renders the same symbol/timeframe |
| 82 | Open an option chain, then return to Charts | Chart still live; no console error |
| 83 | Run a backtest, then return to Charts | Chart unaffected |
| 84 | Leave the app running an hour with the scan loop on | No memory creep, no growing error count in the console |

---

## Reporting a failure

For anything that is not a clean **P**, capture:

1. The check number and what you saw instead.
2. The `/api/candles` response body for that load (Network tab → Response).
3. The `trace_id` from that body, and the matching entry from
   `/api/diagnostics/marketdata?traces=50`.
4. Console output, if any.
5. `logs\data.log` (in `%LOCALAPPDATA%\OptionsPilot\logs\`).

Items 2–3 exist precisely so a report can be diagnosed without reproducing it.
