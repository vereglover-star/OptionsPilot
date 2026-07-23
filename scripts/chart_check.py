"""Comprehensive chart-system regression check in a real headless browser.

Launches the app in serve mode against a scratch (throwaway) data directory
and drives the Charts tab through the behaviors the chart system must never
regress on. Each check prints OK/FAIL; any failure (or any browser console
error) fails the run. Covers, per the V3.1 chart-stabilization sprint:

  ticker loading · invalid ticker + renderer recovery · every timeframe ·
  indicator toggling · drawing create/edit/delete/persistence (the editable
  object model) · historical scroll-back · zoom · stale-cache banner ·
  cache invalidation / rapid symbol changes · resize · live intrabar update
  (no view jump) · single-instance / no-leak guard · history loading via a
  real drag with no arming cheat + on-screen stationarity (V3.2.2 Bug 2/5) ·
  Auto Follow toggle/persistence/manual-pan-disables/Latest-re-enables and
  live-update following (V3.2.2 Bug 3/4) · America/New_York time display on the
  x-axis and crosshair (V3.3 Issue 2) · candle countdown timer (V3.3 Issue 3) ·
  drawing creation preview / rubber-band (V3.3 Issue 5) · drawing overlay
  tracking a vertical price-axis drag (V3.3 Issue 4) · a refresh preserving
  paged-in history and holding the viewport (V3.3 Issue 7/8).

Uses Playwright driving the system's installed Edge (channel="msedge" - no
browser download, offline-capable), matching scripts/browser_check.py.
Soft-skips (exit 0) if Playwright isn't installed; pass --require to make a
missing install a hard failure. Never touches the real data/ directory.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent.parent


def wait_for(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001 - just means "not up yet"
            time.sleep(0.3)
    return False


def main() -> int:  # noqa: C901 - a flat sequence of independent checks reads clearest
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--require", action="store_true",
                    help="fail (not skip) if playwright isn't installed")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = ('playwright not installed - run `pip install -e ".[dev,browser]"` '
               "to enable this check.")
        if args.require:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-chart-"))
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "--config", str(ROOT / "config.yaml"),
         "serve", "--port", str(args.port), "--no-loop"],
        cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + label)
        if not cond:
            failures.append(label)

    try:
        if not wait_for(base + "/api/status"):
            print("FAIL: dev server did not come up in time")
            return 1

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.on("console",
                    lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            candle_reqs: list[str] = []
            page.on("request",
                    lambda r: candle_reqs.append(r.url) if "/api/candles" in r.url else None)
            # this suite runs well past the chart's 30s background-refresh
            # cadence; without this the auto-refresh timer fires mid-suite and
            # races whatever page.route() stub is active/being torn down at
            # that moment ("Route is already handled!"). Manual loadChart()
            # calls in the checks below are unaffected — only the timer is.
            page.add_init_script("window.__chNoAutoRefresh = true;")

            page.goto(base)
            page.wait_for_selector("#hero", timeout=20000)
            page.click('nav button[data-tab="charts"]')

            def wait_loaded(sym: str, tf: str, timeout: int = 30000) -> None:
                page.wait_for_function(
                    "([s, t]) => !document.querySelector('#ch-overlay')"
                    ".classList.contains('show') && CH.dataKey === `${s}·${t}`"
                    " && CH.data && CH.data.candles.length > 3",
                    arg=[sym, tf], timeout=timeout)

            # 1. ticker loading
            wait_loaded("SPY", "1d")
            check(True, "ticker loading (SPY 1D renders)")

            # 1b. same-key refresh must not invalidate an in-flight history
            #     request (V3.3.5 regression). The history fetch is still valid
            #     because the chart is still on the same symbol/tf view; only a
            #     genuine switch should cancel it.
            page.evaluate("""
            () => {
              const makeBars = (start, len, step) => Array.from({length: len}, (_, i) => ({
                time: start + i * step,
                open: 100 + i,
                high: 102 + i,
                low: 99 + i,
                close: 101 + i,
                volume: 1000 + i,
              }));
              const origFetch = window.fetch.bind(window);
              let chartRequests = 0;
              window.__historyRegressionFetch = async (url, opts) => {
                const u = new URL(url, window.location.href);
                const hasRange = u.searchParams.has('start') && u.searchParams.has('end');
                if (hasRange) {
                  await new Promise(r => setTimeout(r, 120));
                  return new Response(JSON.stringify({
                    symbol: 'SPY', timeframe: '5m', candles: makeBars(1700000000, 8, 60),
                    indicators: {}, stale: false, market_open: true,
                  }), {status: 200, headers: {'Content-Type': 'application/json'}});
                }
                chartRequests += 1;
                const payload = {
                  symbol: 'SPY', timeframe: '5m',
                  candles: makeBars(1700000000 + 480, 12, 60),
                  indicators: {}, stale: false, market_open: true,
                };
                return new Response(JSON.stringify(payload), {status: 200, headers: {'Content-Type': 'application/json'}});
              };
              window.fetch = (url, opts) => window.__historyRegressionFetch(url, opts);
              window.__origFetch = origFetch;   // restored after this check
              window.__historyRegressionState = { chartRequests };
            }
            """)
            history_regression = page.evaluate("""async () => {
                CH.sym = 'SPY'; CH.tf = '5m'; CH.historyArmed = true;
                CH.historyLoading = false; CH.historyExhausted = false;
                CH.histCtl = null; CH.gen = 0;
                await loadChart('SPY');
                const historyPromise = chLoadHistoryChunk();
                await new Promise(r => setTimeout(r, 20));
                await loadChart('SPY');
                await historyPromise;
                return { bars: CH.data.candles.length, loading: CH.historyLoading };
            }""")
            check(history_regression["bars"] > 12, "same-key refresh preserves an in-flight history load")
            # Un-stub fetch and restore the pre-check baseline: leaving the stub
            # in place feeds every later check fake SPY candles (an invalid
            # ticker would "load" instead of erroring), and the check above also
            # switched CH.tf to 5m, which the checks below don't expect.
            page.evaluate("""async () => {
                window.fetch = window.__origFetch;
                CH.tf = '1d';
                document.querySelectorAll('#ch-tfs button').forEach(b =>
                    b.classList.toggle('active', b.dataset.tf === '1d'));
                await loadChart('SPY');
            }""")
            wait_loaded("SPY", "1d")

            # 2. invalid ticker -> error overlay + Retry
            page.fill("#ch-symbol", "ZZZZZZ9")
            page.press("#ch-symbol", "Enter")
            page.wait_for_function(
                "() => document.querySelector('#ch-overlay').classList.contains('show')"
                " && document.querySelector('#ch-overlay-retry').style.display !== 'none'",
                timeout=30000)
            check(True, "invalid ticker shows error overlay with Retry")

            # 3. renderer recovery
            page.fill("#ch-symbol", "QQQ")
            page.press("#ch-symbol", "Enter")
            wait_loaded("QQQ", "1d")
            check(True, "renderer recovers after an invalid ticker (QQQ loads)")

            # 4. every timeframe
            tfs = ["1m", "2m", "3m", "5m", "10m", "15m", "30m",
                   "1h", "2h", "4h", "1d", "1w", "1mo"]
            tf_ok = True
            for tf in tfs:
                page.click(f'#ch-tfs button[data-tf="{tf}"]')
                try:
                    wait_loaded("QQQ", tf)
                except Exception:  # noqa: BLE001
                    tf_ok = False
                    print(f"       (timeframe {tf} did not load)")
            check(tf_ok, f"all {len(tfs)} timeframes load real data")
            page.click('#ch-tfs button[data-tf="1d"]')
            wait_loaded("QQQ", "1d")

            # 5. indicator toggling
            for ind in ("vwap", "bb", "rsi", "macd"):
                page.click(f'#ch-inds button[data-ind="{ind}"]')
                page.wait_for_timeout(300)
            page.wait_for_timeout(800)
            check(page.evaluate("() => $('ch-legend').textContent.includes('O ')"),
                  "indicators (EMA/VWAP/BB/RSI/MACD) toggle without breaking the chart")

            # 6. drawing creation — one of every type via the model, plus a real
            #    two-click trend placement to exercise the tool path
            page.evaluate("() => chClearDrawings()")
            page.click('#ch-tools button[data-tool="trend"]')
            check(page.evaluate("() => CH.tool") == "trend", "tool arms instantly on click")
            box = page.locator("#ch-main canvas").first.bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.click(cx - 200, cy + 60)
            page.wait_for_timeout(650)
            page.mouse.click(cx + 160, cy - 60)
            page.wait_for_timeout(300)
            check(page.evaluate("() => DRAW.items.length === 1 && DRAW.items[0].type === 'trend'"),
                  "two-click placement creates a trend object")
            page.evaluate("""() => { const t = i => CH.data.candles[i].time;
              for (const it of [
                {type:'hline',tf:'*',points:[{time:0,price:CH.data.candles.at(-1).close}]},
                {type:'fib',tf:CH.tf,points:[{time:t(20),price:CH.data.candles[20].low},{time:t(80),price:CH.data.candles[80].high}]},
                {type:'rect',tf:CH.tf,points:[{time:t(30),price:CH.data.candles[30].low},{time:t(60),price:CH.data.candles[60].high}]},
                {type:'note',tf:CH.tf,points:[{time:t(90),price:0}],text:'n'}])
                chAddItem(it.type, it.tf, it.points, it.text || ''); }""")
            check(page.evaluate("() => new Set(DRAW.items.map(i=>i.type)).size") == 5,
                  "all five drawing types exist as objects")

            # 7. drawing editing (drag a trend endpoint)
            page.evaluate("() => { DRAW.sel = DRAW.items.find(i=>i.type==='trend').id; chDrawRender(); }")
            before_pr = page.evaluate(
                "() => DRAW.items.find(i=>i.type==='trend').points[0].price")
            h = page.evaluate("""() => { const it=DRAW.items.find(i=>i.type==='trend');
                const p=chHandlePixels(it)[0]; return {x:p.x, y:p.y}; }""")
            page.mouse.move(box["x"] + h["x"], box["y"] + h["y"])
            page.mouse.down()
            page.mouse.move(box["x"] + h["x"], box["y"] + h["y"] - 70, steps=6)
            page.mouse.up()
            page.wait_for_timeout(200)
            after_pr = page.evaluate(
                "() => DRAW.items.find(i=>i.type==='trend').points[0].price")
            check(abs(after_pr - before_pr) > 0.01, "drawing endpoint drag reshapes the object")

            # 8. drawing deletion (Delete key)
            n_before = page.evaluate("() => DRAW.items.length")
            page.evaluate("() => { DRAW.sel = DRAW.items[0].id; }")
            page.keyboard.press("Delete")
            page.wait_for_timeout(150)
            check(page.evaluate("() => DRAW.items.length") == n_before - 1,
                  "Delete key removes the selected drawing")

            # 9. drawing persistence across reload
            saved = page.evaluate("() => JSON.parse(localStorage.getItem('chDraw:QQQ')).items.length")
            page.reload()
            page.wait_for_selector("#hero", timeout=20000)
            page.click('nav button[data-tab="charts"]')
            page.wait_for_function("() => CH.data && CH.candle && DRAW.items.length > 0",
                                   timeout=30000)
            check(page.evaluate("() => DRAW.items.length") == saved,
                  f"drawings persist across reload ({saved})")

            # 9b. drawing engine v3: drawings must actually RENDER on every
            #     timeframe — not merely pass the visibility filter (the V3.2.1
            #     bug: a 1m-anchored trend passed chDrawVisible() but chX()
            #     returned 0 for its off-bar timestamps on 5m/1d, so it painted
            #     nothing). We assert the trend's anchor coordinates RESOLVE (are
            #     finite and distinct) across every timeframe. Plus Ray (PART 2)
            #     and the unified programmatic API (PART 5).
            wait_loaded("QQQ", "1d")
            page.evaluate("() => { DRAW.items=[]; DRAW.sel=null; chDrawSave(); chDrawRender(); }")
            box = page.evaluate("() => { const r=$('ch-main').getBoundingClientRect();"
                                " return {x:r.left,y:r.top,w:r.width,h:r.height}; }")
            page.click('#ch-tfs button[data-tf="1m"]'); wait_loaded("QQQ", "1m")
            ax, ay = box["x"]+box["w"]*0.40, box["y"]+box["h"]*0.40
            bx, by = box["x"]+box["w"]*0.62, box["y"]+box["h"]*0.52
            page.click('#ch-tools button[data-tool="trend"]')
            page.mouse.click(ax, ay); page.wait_for_timeout(60)
            page.mouse.click(bx, by); page.wait_for_timeout(120)
            # a coarser timeframe than the 1m the trend was drawn on: its bar
            # times are NOT bars here, so this is exactly the failing case.
            def trend_renders():
                return page.evaluate("""() => { const t=DRAW.items.find(i=>i.type==='trend');
                    if(!t) return false; const x1=chX(t.points[0].time), x2=chX(t.points[1].time);
                    return x1!=null && x2!=null && Number.isFinite(x1) && Number.isFinite(x2)
                        && Math.abs(x1-x2) > 5; }""")   # finite AND a real (non-degenerate) line
            renders = {}
            for t in ("1m", "5m", "15m", "1h", "1d", "1m"):
                page.click(f'#ch-tfs button[data-tf="{t}"]'); wait_loaded("QQQ", t)
                renders[t] = trend_renders()
            tf_independent = all(renders.values())
            # Ray via real mouse (two-click, extends past the 2nd point)
            page.click('#ch-tfs button[data-tf="1m"]'); wait_loaded("QQQ", "1m")
            page.click('#ch-tools button[data-tool="ray"]')
            page.mouse.click(box["x"]+box["w"]*0.45, box["y"]+box["h"]*0.60); page.wait_for_timeout(60)
            page.mouse.click(box["x"]+box["w"]*0.55, box["y"]+box["h"]*0.55); page.wait_for_timeout(120)
            page.click('#ch-tfs button[data-tf="1h"]'); wait_loaded("QQQ", "1h")  # render on another tf
            ray_ok = page.evaluate(
                "() => { const r=DRAW.items.find(i=>i.type==='ray'); if(!r) return false;"
                " const x=chX(r.points[1].time); return x!=null && Number.isFinite(x); }")
            prog = page.evaluate("""() => { const c=CH.data.candles.at(-1).close;
                const it = window.chAddDrawing({type:'hline', points:[{time:0,price:c}],
                    source:'ai', visibility:{min:'1m',max:'5m'}, select:false});
                return it && it.source==='ai' && chDrawVisibleOn(it,'1m')
                    && chDrawVisibleOn(it,'5m') && !chDrawVisibleOn(it,'1d'); }""")
            check(tf_independent and ray_ok and prog,
                  f"drawings RENDER on every timeframe {renders} + Ray + unified API")
            page.evaluate("() => { DRAW.items=[]; DRAW.sel=null; chDrawSave(); chDrawRender(); }")
            page.evaluate("() => chSetTool(null)")

            # 9d. timeframe switch preserves the focal date (V3.2.1 Bug 3): zoom
            #     to a RECENT window on 1h, switch across resolutions, and assert
            #     the visible centre stays on the same date (never jumps to a
            #     random date, the newest candle, or one candle). Recent so the
            #     date exists in every timeframe's (differing) history window.
            page.click('#ch-tfs button[data-tf="1h"]'); wait_loaded("QQQ", "1h")
            page.evaluate("() => { const n=CH.data.candles.length;"
                          " CH.main.timeScale().setVisibleLogicalRange({from:n-28, to:n-8}); }")
            page.wait_for_timeout(250)

            def focal_time():
                return page.evaluate("""() => { const lr=CH.main.timeScale().getVisibleLogicalRange();
                    const mid=Math.round((lr.from+lr.to)/2); const c=CH.data.candles,n=c.length;
                    return c[Math.max(0,Math.min(n-1,mid))].time; }""")
            f0 = focal_time()
            drifts = {}
            for t in ("30m", "15m", "5m", "1h"):
                page.click(f'#ch-tfs button[data-tf="{t}"]'); wait_loaded("QQQ", t)
                page.wait_for_timeout(150)
                drifts[t] = round(abs(focal_time() - f0) / 3600, 1)
            # also assert we did NOT jump to a one-candle sliver
            not_sliver = page.evaluate("""() => { const lr=CH.main.timeScale().getVisibleLogicalRange();
                const n=CH.data.candles.length; return (Math.min(lr.to,n-1)-Math.max(lr.from,0)) >= 8; }""")
            check(all(v < 30 for v in drifts.values()) and not_sliver,
                  f"timeframe switch preserves the focal date {drifts} (no jump, no sliver)")

            # 9e. the chart must not fight the user: panning past the newest
            #     candle then a same-key refresh must NOT snap the viewport
            #     (V3.2.1 Bug 2). Latest / Reset remain the only auto-recenters.
            page.click('#ch-tfs button[data-tf="1h"]'); wait_loaded("QQQ", "1h")
            page.evaluate("() => { const n=CH.data.candles.length;"
                          " CH.main.timeScale().setVisibleLogicalRange({from:n-25, to:n+35}); }")
            page.wait_for_timeout(200)
            vb = page.evaluate("() => { const lr=CH.main.timeScale().getVisibleLogicalRange();"
                               " return [Math.round(lr.from), Math.round(lr.to)]; }")
            page.evaluate("() => loadChart()"); page.wait_for_timeout(500)
            va = page.evaluate("() => { const lr=CH.main.timeScale().getVisibleLogicalRange();"
                               " return [Math.round(lr.from), Math.round(lr.to)]; }")
            stable = abs(vb[0]-va[0]) <= 2 and abs(vb[1]-va[1]) <= 2
            page.evaluate("() => chGoToLatest()"); page.wait_for_timeout(150)
            latest_ok = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange().to"
                                      " >= CH.data.candles.length - 3")
            check(stable and latest_ok,
                  f"viewport not yanked by a refresh while panned past newest {vb}->{va}; Latest still works")
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")

            # 9c. extended hours (PART 4): the toggle adds pre/after-market bars
            #     with session tags on intraday, is a no-op (disabled) on daily,
            #     and the preference is applied. Uses a controlled route so the
            #     session mix is deterministic regardless of the live feed.
            page.click('#ch-tfs button[data-tf="5m"]'); wait_loaded("QQQ", "5m")
            if page.evaluate("() => CH.ext"):        # start from RTH-only
                page.click('#ch-ext'); wait_loaded("QQQ", "5m")
            rth_bars = page.evaluate("() => CH.data.candles.length")
            rth_session = page.evaluate("() => !!CH.data.candles[0].session")

            def ext_route(route):
                url = route.request.url
                body = route.fetch().json()
                if "ext=1" in url and body.get("candles"):
                    # tag alternating sessions so ext mode is deterministic
                    for i, bar in enumerate(body["candles"]):
                        bar["session"] = ("pre", "rth", "post")[i % 3]
                    body["extended_hours"] = True
                try:
                    route.fulfill(json=body)
                except Exception:  # noqa: BLE001 - a concurrent request to the
                    pass            # same URL can already be resolved by the time
                                    # this one lands; the test's own wait_for_function
                                    # assertions are the source of truth, not this.
            page.route("**/api/candles*", ext_route)
            page.click('#ch-ext'); page.wait_for_timeout(200)
            page.wait_for_function("() => CH.data && CH.data.extended_hours === true", timeout=30000)
            ext_flag = page.evaluate("() => CH.data.extended_hours === true")
            ext_session = page.evaluate("() => !!CH.data.candles[0].session")
            ext_active = page.evaluate("() => $('ch-ext').classList.contains('active')")
            no_err_bands = page.evaluate(
                "() => { try { chSessionBands(document.querySelector('#ch-draw').getContext('2d'),"
                " 800, 400); return true; } catch(e){ return false; } }")
            page.unroute("**/api/candles*", ext_route)
            page.click('#ch-ext'); page.wait_for_timeout(150)    # back to RTH-only
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")
            daily_disabled = page.evaluate("() => $('ch-ext').disabled === true")
            check(not rth_session and ext_flag and ext_session and ext_active
                  and no_err_bands and daily_disabled,
                  "extended hours: session tags + shading intraday, disabled on daily")

            # 10. historical scroll-back prepends older bars
            wait_loaded("QQQ", "1d")
            oldest_before = page.evaluate("() => CH.data.candles[0].time")
            count_before = page.evaluate("() => CH.data.candles.length")
            page.evaluate("() => { CH.historyArmed = true; "
                          "CH.main.timeScale().setVisibleLogicalRange({from:-30, to:60}); }")
            page.wait_for_function(
                f"() => CH.data.candles.length > {count_before}", timeout=20000)
            check(page.evaluate("() => CH.data.candles[0].time") < oldest_before,
                  "scrolling left loads and prepends older history")

            # 11. zoom (programmatic visible-range change, chart stays intact)
            page.evaluate("() => CH.main.timeScale().setVisibleLogicalRange("
                          "{from: CH.data.candles.length-40, to: CH.data.candles.length})")
            page.wait_for_timeout(400)
            check(page.evaluate("() => $('ch-legend').textContent.includes('O ')"),
                  "zoom (visible-range change) keeps the chart rendered")

            # 12. stale-cache banner: a controlled route returns GENUINELY-BEHIND
            #     stale bars (trailing bars dropped, so the newest bar is older
            #     than the fresh one we already loaded), then fresh bars on retry.
            #     market_open is forced True: the banner means "behind LIVE
            #     prices", only meaningful while the market is open. Genuinely
            #     behind (not just flagged stale on the same newest bar) is what
            #     the banner is FOR — see check 12b for the anti-flap counterpart.
            # Capture ONE real payload, then serve deterministic copies from it —
            # the routes below never re-hit the live feed (keeps the suite off
            # Yahoo's rate limiter and makes the banner behaviour reproducible).
            import copy
            _baseline = {}

            def _base(route):
                if not _baseline:
                    _baseline["b"] = route.fetch().json()
                return copy.deepcopy(_baseline["b"])

            force_stale = [True]

            def stale_route(route):
                body = _base(route)
                if body.get("candles"):
                    if force_stale[0] and len(body["candles"]) > 6:
                        body["candles"] = body["candles"][:-5]   # drop the freshest bars
                        for k, v in list((body.get("indicators") or {}).items()):
                            if v:
                                body["indicators"][k] = v[:-5]
                        body["stale"] = True
                        body["as_of"] = body["candles"][-1]["time"]
                    else:
                        body["stale"] = False
                        body["as_of"] = None
                    body["market_open"] = True
                route.fulfill(json=body)
            page.route("**/api/candles*", stale_route)
            page.evaluate("() => loadChart()")
            page.wait_for_function(
                "() => document.querySelector('#ch-stale').classList.contains('show')",
                timeout=30000)
            check(True, "stale payload (genuinely behind) shows the cached-bars banner")
            force_stale[0] = False
            page.click("#ch-stale-retry")
            page.wait_for_function(
                "() => !document.querySelector('#ch-stale').classList.contains('show')",
                timeout=30000)
            check(True, "Retry-live clears the stale banner")
            page.unroute("**/api/candles*", stale_route)

            # 12b. anti-flap (Bug 2): once we've loaded a fresh bar, a feed that
            #      flaps stale/fresh on the SAME newest bar must NOT re-raise the
            #      warning — we still hold the current data, one refetch just
            #      failed. Alternate stale/fresh (same newest bar) and assert the
            #      banner never appears. Same captured payload → no live fetches.
            page.evaluate("() => loadChart()")   # fresh load sets the high-water mark
            wait_loaded("QQQ", "1d")
            flap = [True]

            def flap_route(route):
                body = _base(route)
                if body.get("candles"):
                    body["stale"] = flap[0]      # same newest bar either way
                    body["as_of"] = body["candles"][-1]["time"] if flap[0] else None
                    body["market_open"] = True
                route.fulfill(json=body)
            page.route("**/api/candles*", flap_route)
            page.evaluate("""() => { window.__flapShows = 0;
                const bar = document.querySelector('#ch-stale');
                let last = bar.classList.contains('show');
                window.__flapObs = new MutationObserver(() => {
                    const now = bar.classList.contains('show');
                    if (now && !last) window.__flapShows++; last = now;
                }); window.__flapObs.observe(bar, {attributes:true, attributeFilter:['class']}); }""")
            for i in range(6):
                flap[0] = (i % 2 == 0)
                page.evaluate("() => loadChart()")
                page.wait_for_timeout(250)
            flap_shows = page.evaluate("() => window.__flapShows")
            check(flap_shows == 0,
                  f"stale banner does not flap on unchanged data (re-shows={flap_shows})")
            page.unroute("**/api/candles*", flap_route)
            page.evaluate("() => { if (window.__flapObs) window.__flapObs.disconnect(); }")
            page.evaluate("() => loadChart()")
            wait_loaded("QQQ", "1d")

            # 13. cache invalidation / rapid symbol changes
            canvas_before = page.evaluate("() => document.querySelectorAll('#ch-main canvas').length")
            for sym in ("SPY", "META", "IWM", "AAPL", "QQQ"):
                page.fill("#ch-symbol", sym)
                page.press("#ch-symbol", "Enter")
            wait_loaded("QQQ", "1d")
            check(page.evaluate("() => CH.sym") == "QQQ",
                  "rapid symbol changes settle on the last request (generation guard)")

            # 14. resize
            page.set_viewport_size({"width": 1100, "height": 800})
            page.wait_for_timeout(500)
            page.set_viewport_size({"width": 1500, "height": 950})
            page.wait_for_timeout(500)
            check(page.evaluate("() => $('ch-legend').textContent.includes('O ')"),
                  "resize keeps the chart rendered")

            # 15. live intrabar update: forming candle changes with no view jump
            def bump_route(route):
                resp = route.fetch()
                body = resp.json()
                if body.get("candles"):
                    last = body["candles"][-1]
                    last["close"] = round(last["close"] * 1.01, 2)
                    last["volume"] = int(last["volume"]) + 9999
                route.fulfill(json=body)
            pre = page.evaluate("() => ({c: CH.data.candles.at(-1).close, n: CH.data.candles.length, "
                                "r: CH.main.timeScale().getVisibleLogicalRange()})")
            page.route("**/api/candles*", bump_route)
            page.evaluate("() => loadChart()")
            page.wait_for_function(
                f"() => CH.data.candles.at(-1).close !== {pre['c']}", timeout=15000)
            post = page.evaluate("() => ({n: CH.data.candles.length, "
                                 "r: CH.main.timeScale().getVisibleLogicalRange()})")
            jump = abs(post["r"]["from"] - pre["r"]["from"]) + abs(post["r"]["to"] - pre["r"]["to"])
            check(post["n"] == pre["n"] and jump < 0.01,
                  "live intrabar update is in-place (no new bar, no view jump)")
            page.unroute("**/api/candles*")

            # 15b. new-bar append: a fresh trailing bar (the market-hours
            #      bar-rollover case) is appended via the incremental fast path —
            #      candle count grows by exactly one, the view does not jump.
            tf_secs = {"1d": 86400}
            def append_route(route):
                resp = route.fetch()
                body = resp.json()
                cs = body.get("candles")
                if cs:
                    last = cs[-1]
                    nb = dict(last)
                    nb["time"] = last["time"] + tf_secs["1d"]
                    body["candles"] = cs + [nb]
                    for name, series in (body.get("indicators") or {}).items():
                        if series:
                            body["indicators"][name] = series + [series[-1]]
                route.fulfill(json=body)
            pre2 = page.evaluate("() => ({n: CH.data.candles.length, "
                                 "r: CH.main.timeScale().getVisibleLogicalRange()})")
            page.route("**/api/candles*", append_route)
            page.evaluate("() => loadChart()")
            page.wait_for_function(
                f"() => CH.data.candles.length === {pre2['n']} + 1", timeout=15000)
            post2 = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            # a new bar at the realtime edge scrolls the view by ~1 bar to keep
            # the edge in sight (correct, TradingView-like) — assert the chart is
            # not REZOOMED (visible width preserved) and only nudged, not recentred.
            w_pre = pre2["r"]["to"] - pre2["r"]["from"]
            w_post = post2["to"] - post2["from"]
            rezoom = abs(w_post - w_pre)
            shift = abs(post2["from"] - pre2["r"]["from"])
            check(rezoom < 0.5 and shift <= 2.0,
                  "new-bar append grows the series without a view jump")
            page.unroute("**/api/candles*")
            page.evaluate("() => loadChart()")   # restore the real (un-appended) series
            wait_loaded("QQQ", "1d")

            # 16. single-instance / no-leak guard: no extra chart canvases spawned
            canvas_after = page.evaluate("() => document.querySelectorAll('#ch-main canvas').length")
            check(canvas_after == canvas_before and page.evaluate("() => !!CH.main"),
                  "one chart instance throughout (no per-switch canvas leak)")

            # 17. corrupt localStorage must not brick the chart (safeParse) — a
            #     garbage drawings store resets to empty instead of throwing
            page.evaluate("() => { localStorage.setItem('chDraw:QQQ', '{not json'); "
                          "localStorage.setItem('chInds', 'garbage'); }")
            page.reload()
            page.wait_for_selector("#hero", timeout=20000)
            page.click('nav button[data-tab="charts"]')
            try:
                wait_loaded("QQQ", "1d")
                recovered = not page.evaluate(
                    "() => document.querySelector('#ch-overlay').classList.contains('show')")
            except Exception:  # noqa: BLE001
                recovered = False
            check(recovered and page.evaluate("() => Array.isArray(DRAW.items)"),
                  "corrupt localStorage recovers to a working chart (no brick)")

            # 18. payload cache is bounded (LRU) — visiting many symbols can't
            #     grow it without limit
            for sym in ("SPY", "META", "IWM", "AAPL", "NVDA", "TSLA", "MSFT",
                        "AMZN", "GOOGL", "QQQ"):
                for tf in ("1d", "1h", "5m"):
                    page.evaluate("([s, t]) => { CH.sym = s; CH.tf = t; }", [sym, tf])
                    page.evaluate("() => chCacheEntry(chKey())")
            check(page.evaluate("() => CH.cache.size <= CH_CACHE_MAX"),
                  "payload cache is bounded (LRU eviction, no unbounded growth)")

            # normalize state after the cache probe mutated CH.sym/CH.tf
            page.evaluate("() => loadChart('QQQ')")
            page.click('#ch-tfs button[data-tf="1d"]')
            wait_loaded("QQQ", "1d")

            # 19. drawing edit-toolbar actions, driven ENTIRELY BY REAL MOUSE —
            #     draw a trendline by clicking two canvas points, click the line to
            #     select it, then click the toolbar controls, all via page.mouse at
            #     real coordinates. The previous version set DRAW.sel in JS, which
            #     bypassed the real select→toolbar-click path and so could not have
            #     caught the capture-phase deselect bug the user hit manually.
            page.evaluate("() => { DRAW.items = []; DRAW.sel = null; chDrawSave(); chDrawRender(); }")
            box = page.evaluate(
                "() => { const r = $('ch-main').getBoundingClientRect();"
                " return {x:r.left, y:r.top, w:r.width, h:r.height}; }")
            ax, ay = box["x"] + box["w"] * 0.35, box["y"] + box["h"] * 0.35
            bx, by = box["x"] + box["w"] * 0.62, box["y"] + box["h"] * 0.52
            page.click('#ch-tools button[data-tool="trend"]')
            page.mouse.click(ax, ay); page.wait_for_timeout(60)
            page.mouse.click(bx, by); page.wait_for_timeout(120)
            drawn = page.evaluate("() => DRAW.items.length") == 1
            # select by clicking the midpoint of the line (real mouse)
            page.mouse.click((ax + bx) / 2, (ay + by) / 2); page.wait_for_timeout(120)
            selected = page.evaluate("() => !!DRAW.sel") and \
                page.evaluate("() => document.querySelector('#ch-draw-bar').classList.contains('show')")

            def mouse_click_selector(sel):
                r = page.evaluate(
                    "(s) => { const e = document.querySelector(s); if(!e) return null;"
                    " const b = e.getBoundingClientRect();"
                    " return {x:b.left+b.width/2, y:b.top+b.height/2}; }", sel)
                assert r, f"element not found: {sel}"
                page.mouse.click(r["x"], r["y"]); page.wait_for_timeout(100)

            color0 = page.evaluate("() => DRAW.items[0].color")
            target_c = page.evaluate(
                "() => { const cur = DRAW.items[0].color;"
                " const t = [...document.querySelectorAll('#ch-draw-colors i')]"
                ".find(e => e.dataset.c !== cur); return t ? t.dataset.c : null; }")
            mouse_click_selector(f'#ch-draw-colors i[data-c="{target_c}"]')
            recolor_ok = page.evaluate("() => DRAW.items[0].color") == target_c and \
                page.evaluate("() => !!DRAW.sel")            # selection survived the click
            w0 = page.evaluate("() => DRAW.items[0].width")
            mouse_click_selector('#ch-draw-bar button[data-act="width"]')
            width_ok = page.evaluate("() => DRAW.items[0].width") != w0
            mouse_click_selector('#ch-draw-bar button[data-act="dup"]')
            dup_ok = page.evaluate("() => DRAW.items.length") == 2
            # re-select the surviving item, then lock / hide / delete by real mouse
            page.mouse.click((ax + bx) / 2, (ay + by) / 2); page.wait_for_timeout(100)
            if page.evaluate("() => !DRAW.sel"):             # dup copy may sit off the line
                page.evaluate("() => { DRAW.sel = DRAW.items[0].id; chDrawRender(); }")
            mouse_click_selector('#ch-draw-bar button[data-act="lock"]')
            lock_ok = page.evaluate("() => DRAW.items.find(i=>i.id===DRAW.sel).locked") is True
            mouse_click_selector('#ch-draw-bar button[data-act="hide"]')
            hide_ok = page.evaluate("() => DRAW.items.some(i => i.hidden)") is True
            n_before_del = page.evaluate("() => DRAW.items.length")
            page.evaluate("() => { DRAW.sel = DRAW.items[0].id; chDrawRender(); }")
            mouse_click_selector('#ch-draw-bar button[data-act="del"]')
            del_ok = page.evaluate("() => DRAW.items.length") == n_before_del - 1
            check(drawn and selected and recolor_ok and width_ok and dup_ok and
                  lock_ok and hide_ok and del_ok,
                  "toolbar actions via REAL MOUSE (draw→select→recolour/width/dup/lock/hide/delete)")
            page.evaluate("() => { DRAW.items = []; DRAW.sel = null; chDrawSave(); chDrawRender(); }")
            page.click('#ch-tools button[data-tool="trend"]')  # disarm the tool if still armed
            page.evaluate("() => chSetTool(null)")
            page.evaluate("() => { DRAW.items = []; DRAW.sel = null; chDrawSave(); chDrawRender(); }")

            # 20. toggling an indicator subpane must NOT move the main viewport
            #     (regression: the old two-way pane sync let a new pane's auto-fit
            #     shove its range onto main, recentering the chart on every toggle).
            page.evaluate("() => { const n = CH.data.candles.length; "
                          "CH.main.timeScale().setVisibleLogicalRange({from:n-40, to:n-1}); }")
            page.wait_for_timeout(200)
            base_r = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            for ind in ("rsi", "macd"):
                if not page.evaluate(f"() => CH.inds['{ind}']"):
                    page.click(f'#ch-inds button[data-ind="{ind}"]')
                page.wait_for_timeout(300)
            after_r = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            no_jump = (abs(after_r["from"] - base_r["from"]) + abs(after_r["to"] - base_r["to"])) < 1.0
            check(no_jump, "indicator toggle leaves the main viewport put (no random jump)")

            # 21. the chart must never strand the user: after a pan into whitespace
            #     past the last bar, Reset and Go-To-Latest both recover a populated
            #     view. The strand must be DETERMINISTIC: an extreme overscroll
            #     ({n-2, n+120}) is clamped by lightweight-charts by a variable
            #     amount that depends on the current bar spacing (which drifts with
            #     the live bar count), so it only sometimes produced a genuinely
            #     stranded view — a flaky precondition. A narrow window sitting
            #     ENTIRELY in the whitespace past the last bar strands every time.
            def strand():
                page.evaluate("() => { const n = CH.data.candles.length; "
                              "CH.main.timeScale().setVisibleLogicalRange({from:n+8, to:n+18}); }")
            strand(); page.wait_for_timeout(60)
            stranded = page.evaluate("() => chViewportStranded()")
            page.click("#ch-reset"); page.wait_for_timeout(200)
            reset_ok = not page.evaluate("() => chViewportStranded()")
            strand(); page.wait_for_timeout(60)
            page.click("#ch-latest"); page.wait_for_timeout(300)
            latest_ok = not page.evaluate("() => chViewportStranded()")
            check(stranded and reset_ok and latest_ok,
                  "viewport recovery: Reset/Latest rescue a stranded chart")

            # 21b. switching timeframes must never leave a degenerate zoom
            #      (Bug 3: a stale per-tf viewport snapped back onto a handful of
            #      candles). Zoom 5m down to a few bars, flip away and back, and to
            #      other tfs — every landing must show a healthy bar count.
            def visible_bars():
                return page.evaluate("""() => {
                    const lr = CH.main.timeScale().getVisibleLogicalRange();
                    const n = CH.data.candles.length;
                    if (!lr) return 0;
                    return Math.min(lr.to, n-1) - Math.max(lr.from, 0);
                }""")
            def switch_tf(tf):
                page.click(f'#ch-tfs button[data-tf="{tf}"]')
                try:
                    wait_loaded("QQQ", tf)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(150)
            switch_tf("5m")
            page.evaluate("() => { const n = CH.data.candles.length; "
                          "CH.main.timeScale().setVisibleLogicalRange({from:n-5, to:n-1}); }")
            page.wait_for_timeout(150)
            tf_bars = []
            for tf in ("1m", "5m", "3m", "5m", "1d", "5m"):   # includes returns to 5m
                switch_tf(tf)
                tf_bars.append((tf, round(visible_bars(), 1)))
            no_tiny_zoom = all(b >= 10 for _, b in tf_bars)
            check(no_tiny_zoom,
                  f"timeframe switching never zooms into a sliver {tf_bars}")
            page.click('#ch-tfs button[data-tf="1d"]')
            wait_loaded("QQQ", "1d")

            # 24. history loading via a REAL drag pan, no manual "historyArmed"
            #     cheat (V3.2.2 Bug 2): the old wheel/touchstart/pointerdown
            #     listeners armed history a DOM-event tick after the library's
            #     own synchronous range-change during the same pan, so a scroll
            #     into history sometimes silently did nothing. The fix arms
            #     directly off the range-change subscription, so a genuine drag
            #     must reliably trigger a history fetch every time.
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")
            # position near the oldest loaded bar through the SAME controller
            # every sanctioned in-app mover uses (chMoveViewport), so this setup
            # step itself doesn't count as the "real" user-driven change under
            # test — otherwise this line, not the drag below, would be the one
            # that arms and triggers the history fetch.
            page.evaluate("() => { CH.historyArmed = false; CH.historyExhausted = false; "
                          "chMoveViewport(() => CH.main.timeScale()"
                          ".setVisibleLogicalRange({from: 10, to: 50})); }")
            page.wait_for_timeout(200)
            oldest_before2 = page.evaluate("() => CH.data.candles[0].time")
            count_before2 = page.evaluate("() => CH.data.candles.length")
            box2 = page.locator("#ch-main canvas").first.bounding_box()
            cx2, cy2 = box2["x"] + box2["width"] / 2, box2["y"] + box2["height"] / 2
            # capture the on-screen time range right as the drag ends, and again
            # once the merge lands: a history prepend must never move bars
            # already on screen (Bug 5) — only new (older) bars appear at left.
            page.mouse.move(cx2, cy2)
            page.mouse.down()
            page.mouse.move(cx2 + 350, cy2, steps=10)   # drag right -> reveal earlier bars
            page.mouse.up()
            vr_before_merge = page.evaluate(
                "() => { const r = CH.main.timeScale().getVisibleRange(); return r && [r.from, r.to]; }")
            try:
                page.wait_for_function(
                    f"() => CH.data.candles.length > {count_before2}", timeout=15000)
                history_via_real_drag = page.evaluate(
                    "() => CH.data.candles[0].time") < oldest_before2
            except Exception:  # noqa: BLE001
                history_via_real_drag = False
            vr_after_merge = page.evaluate(
                "() => { const r = CH.main.timeScale().getVisibleRange(); return r && [r.from, r.to]; }")
            stationary = bool(vr_before_merge and vr_after_merge
                              and abs(vr_after_merge[0] - vr_before_merge[0]) < 1
                              and abs(vr_after_merge[1] - vr_before_merge[1]) < 1)
            check(history_via_real_drag and stationary,
                  "a real drag (no cheat) arms + loads history; on-screen bars stay stationary")
            page.evaluate("() => loadChart('QQQ')")
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")

            # 25. Auto Follow (V3.2.2 Bug 3/4): OFF by default; toggling it ON
            #     jumps to the newest bar and persists; a real manual drag pan
            #     turns it back OFF; pressing Latest turns it back ON.
            default_off = page.evaluate("() => CH.autoFollow") is False
            page.evaluate("() => { const n = CH.data.candles.length; "
                          "CH.main.timeScale().setVisibleLogicalRange({from:n-80, to:n-40}); }")
            page.wait_for_timeout(150)
            page.click("#ch-follow")
            page.wait_for_timeout(200)
            on_ok = page.evaluate("() => CH.autoFollow") is True
            active_class = page.evaluate("() => $('ch-follow').classList.contains('active')")
            persisted_on = page.evaluate("() => localStorage.getItem('chAutoFollow')") == "1"
            jumped = page.evaluate(
                "() => CH.main.timeScale().getVisibleLogicalRange().to"
                " >= CH.data.candles.length - 3")
            box3 = page.locator("#ch-main canvas").first.bounding_box()
            cx3, cy3 = box3["x"] + box3["width"] / 2, box3["y"] + box3["height"] / 2
            page.mouse.move(cx3, cy3)
            page.mouse.down()
            page.mouse.move(cx3 - 150, cy3, steps=8)   # a real manual pan
            page.mouse.up()
            page.wait_for_timeout(200)
            disabled_by_pan = page.evaluate("() => CH.autoFollow") is False
            disabled_class = not page.evaluate("() => $('ch-follow').classList.contains('active')")
            page.click("#ch-latest")
            page.wait_for_timeout(150)
            latest_reenables = page.evaluate("() => CH.autoFollow") is True
            check(default_off and on_ok and active_class and persisted_on and jumped
                  and disabled_by_pan and disabled_class and latest_reenables,
                  "Auto Follow: OFF by default, toggle jumps+persists, "
                  "manual pan disables, Latest re-enables")
            page.evaluate("() => loadChart('QQQ')")
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")

            # 26. live updates respect Auto Follow (Bug 1 + Bug 3 together): OFF
            #     (default) never moves the viewport on a live tail update; ON
            #     keeps the newest bar in view as prices tick.
            # each route.fetch() re-reads the PRISTINE upstream value, so the
            # multiplier must differ between the two bumps below — reusing one
            # factor would recompute the identical bumped close both times and
            # "close !== previous" would never become true (a test-only trap,
            # not a production bug).
            def make_bump_route(factor):
                def _route(route):
                    resp = route.fetch(); body = resp.json()
                    if body.get("candles"):
                        body["candles"][-1]["close"] = round(body["candles"][-1]["close"] * factor, 2)
                    route.fulfill(json=body)
                return _route
            if page.evaluate("() => CH.autoFollow"):
                page.click("#ch-follow"); page.wait_for_timeout(100)  # force OFF
            page.evaluate("() => { const n = CH.data.candles.length; "
                          "CH.main.timeScale().setVisibleLogicalRange({from:n-40, to:n-10}); }")
            page.wait_for_timeout(150)
            before_off = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            close0 = page.evaluate("() => CH.data.candles.at(-1).close")
            bump_off = make_bump_route(1.01)
            page.route("**/api/candles*", bump_off)
            page.evaluate("() => loadChart()")
            page.wait_for_function(f"() => CH.data.candles.at(-1).close !== {close0}", timeout=15000)
            after_off = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            off_stationary = (abs(after_off["from"] - before_off["from"]) < 0.5
                              and abs(after_off["to"] - before_off["to"]) < 0.5)
            page.unroute("**/api/candles*", bump_off)
            page.click("#ch-follow"); page.wait_for_timeout(150)   # ON: also jumps to latest
            close1 = page.evaluate("() => CH.data.candles.at(-1).close")
            bump_on = make_bump_route(1.02)
            page.route("**/api/candles*", bump_on)
            page.evaluate("() => loadChart()")
            page.wait_for_function(f"() => CH.data.candles.at(-1).close !== {close1}", timeout=15000)
            after_on = page.evaluate("() => CH.main.timeScale().getVisibleLogicalRange()")
            n_now = page.evaluate("() => CH.data.candles.length")
            on_follows = after_on["to"] >= n_now - 3
            page.unroute("**/api/candles*", bump_on)
            check(off_stationary and on_follows,
                  "live updates: Auto Follow OFF never moves the viewport, "
                  "ON keeps the newest bar in view")
            if page.evaluate("() => CH.autoFollow"):
                page.click("#ch-follow"); page.wait_for_timeout(100)  # back to OFF for later checks
            page.evaluate("() => loadChart('QQQ')")
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("QQQ", "1d")

            # 22. stale banner reflects market state: suppressed while the market
            #     is CLOSED (cached bars ARE the last session), shown while OPEN.
            banner = page.evaluate("""() => {
                const ts = CH.data.candles.at(-1).time;
                CH.data.market_open = false; chStale(ts);
                const closed = document.querySelector('#ch-stale').classList.contains('show');
                CH.data.market_open = true; chStale(ts);
                const open = document.querySelector('#ch-stale').classList.contains('show');
                chStale(null);
                return {closed, open};
            }""")
            check(banner["closed"] is False and banner["open"] is True,
                  "stale banner suppressed when market closed, shown when open")

            # 23. stress: rapidly abuse the chart; any console error fails the run
            page.evaluate("() => loadChart('SPY')")
            wait_loaded("SPY", "1d")
            for i in range(24):
                page.evaluate(
                    """(i) => {
                        const n = CH.data.candles.length;
                        CH.main.timeScale().setVisibleLogicalRange(
                            {from: n - 10 - (i*7)%150, to: n - 1 + (i%3)});
                        if (i % 4 === 0) chAddItem('trend', CH.tf,
                            [{time: CH.data.candles.at(-20).time, price: CH.data.candles.at(-20).close},
                             {time: CH.data.candles.at(-1).time, price: CH.data.candles.at(-1).close}]);
                        if (i % 4 === 1 && DRAW.items.length) {
                            DRAW.sel = DRAW.items.at(-1).id; chDrawAct('dup'); }
                        if (i % 4 === 2 && DRAW.items.length) {
                            DRAW.sel = DRAW.items.at(-1).id; chDrawAct('del'); }
                        if (i % 5 === 0) chResetView();
                        if (i % 5 === 3) chGoToLatest();
                    }""", i)
                if i % 6 == 0:
                    page.click(f'#ch-tfs button[data-tf="{["1d","1h","5m","15m"][i//6 % 4]}"]')
                    page.wait_for_timeout(120)
            for ind in ("rsi", "macd", "bb", "vwap"):
                page.click(f'#ch-inds button[data-ind="{ind}"]')
            page.set_viewport_size({"width": 1000, "height": 760})
            page.wait_for_timeout(200)
            page.set_viewport_size({"width": 1500, "height": 950})
            page.wait_for_timeout(200)
            page.evaluate("() => { DRAW.items = []; DRAW.sel = null; chDrawSave(); }")
            page.fill("#ch-symbol", "SPY")
            page.press("#ch-symbol", "Enter")
            page.click('#ch-tfs button[data-tf="1d"]')   # abuse loop left tf elsewhere
            wait_loaded("SPY", "1d")
            check(page.evaluate("() => !!CH.main && CH.data && CH.data.candles.length > 3 && "
                                "!document.querySelector('#ch-overlay').classList.contains('show')"),
                  "chart survives a rapid abuse burst and stays rendered")

            # ── V3.3 (chart stabilization & market validation) ────────────────

            # 27. Timezone (V3.3 Issue 2): x-axis tick + crosshair labels render in
            #     America/New_York, NOT UTC. Fails before the fix (lightweight-charts
            #     defaulted to UTC). Compared against a Python-side ET computation so
            #     it's correct regardless of the CI machine's own timezone.
            page.click('#ch-tfs button[data-tf="5m"]'); wait_loaded("SPY", "5m")
            bar_t = page.evaluate("() => CH.data.candles.at(-1).time")
            tick_et = page.evaluate("(t) => chTickMark(t, 3)", bar_t)          # TickMarkType.Time
            cross_et = page.evaluate("(t) => chCrosshairTime(t)", bar_t)
            want_hm = datetime.fromtimestamp(bar_t, ET).strftime("%H:%M")
            want_utc = datetime.fromtimestamp(bar_t, timezone.utc).strftime("%H:%M")
            check(tick_et == want_hm and want_hm in cross_et and tick_et != want_utc,
                  f"timezone: x-axis + crosshair render in ET ({tick_et}=={want_hm}, not UTC {want_utc})")

            # 28. Countdown timer (V3.3 Issue 3): shown with a valid M:SS while the
            #     market is open on an intraday frame; hidden on daily and when the
            #     market is closed. Driven deterministically (forcing market_open)
            #     so it doesn't depend on the wall-clock session at test time.
            timer = page.evaluate("""() => {
                const el = document.querySelector('#ch-timer');
                CH.data.market_open = true; chUpdateTimer();
                const shownIntraday = el.classList.contains('show');
                const txt = (el.textContent || '').replace(/[^0-9:]/g, '');
                CH.data.market_open = false; chUpdateTimer();
                const hiddenClosed = !el.classList.contains('show');
                return { shownIntraday, txt, hiddenClosed };
            }""")
            page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("SPY", "1d")
            hidden_daily = page.evaluate("""() => { CH.data.market_open = true; chUpdateTimer();
                return !document.querySelector('#ch-timer').classList.contains('show'); }""")
            valid_fmt = bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", timer["txt"]))
            check(timer["shownIntraday"] and valid_fmt and timer["hiddenClosed"] and hidden_daily,
                  f"countdown timer: shows M:SS intraday-open ({timer['txt']}), hidden daily/closed")

            # 29. Drawing creation preview (V3.3 Issue 5), REAL MOUSE: the first click
            #     anchors the drawing and shows it immediately; the second endpoint
            #     rubber-bands to the cursor before the finalizing click. Fails before
            #     the fix (nothing appeared until the second click).
            page.click('#ch-tfs button[data-tf="5m"]'); wait_loaded("SPY", "5m")
            page.evaluate("() => { DRAW.items=[]; DRAW.sel=null; chDrawSave(); chDrawRender(); }")
            box = page.evaluate("() => { const r=$('ch-main').getBoundingClientRect();"
                                " return {x:r.left,y:r.top,w:r.width,h:r.height}; }")
            ax, ay = box["x"]+box["w"]*0.40, box["y"]+box["h"]*0.42
            bx, by = box["x"]+box["w"]*0.60, box["y"]+box["h"]*0.55
            page.click('#ch-tools button[data-tool="trend"]')
            page.mouse.click(ax, ay); page.wait_for_timeout(80)     # FIRST click = anchor
            anchored = page.evaluate("() => DRAW.items.length===0 && !!CH.pendingPoint && !!CH.previewPt")
            page.mouse.move(bx, by, steps=8); page.wait_for_timeout(120)  # rubber-band
            moved = page.evaluate("""() => CH.previewPt && (CH.previewPt.time!==CH.pendingPoint.time
                                     || CH.previewPt.price!==CH.pendingPoint.price)""")
            preview_px = page.evaluate("""() => { const c=document.querySelector('#ch-draw');
                const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                let n=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) n++; return n; }""")
            page.mouse.click(bx, by); page.wait_for_timeout(120)     # SECOND click = finalize
            finalized = page.evaluate("() => DRAW.items.length===1 && DRAW.items[0].type==='trend' && !CH.pendingPoint")
            check(anchored and moved and preview_px > 0 and finalized,
                  f"drawing preview: 1st click anchors + rubber-bands ({preview_px}px), 2nd finalizes")
            page.evaluate("() => { DRAW.items=[]; DRAW.sel=null; chDrawSave(); chSetTool(null); chDrawRender(); }")

            # 30. Overlay tracks a VERTICAL price-axis drag (V3.3 Issue 4), REAL MOUSE.
            #     lightweight-charts fires no event for price-scale changes, so the
            #     drawing overlay used to freeze on a vertical drag and snap later
            #     (reproduced: 0 redraws). The rAF sync loop now redraws every frame
            #     the coordinate mapping changes. Assert the overlay redraws AND a
            #     level's rendered Y actually tracks the rescale.
            page.evaluate("""() => { DRAW.items=[]; DRAW.sel=null;
                chAddItem('hline','*',[{time:0, price:CH.data.candles.at(-1).close}]); chDrawSave(); chDrawRender(); }""")
            page.evaluate("""() => { window.__dc=0; const o=window.chDrawRender;
                window.chDrawRender=function(){ window.__dc++; return o.apply(this,arguments); }; }""")
            px = box["x"]+box["w"]-20; pmid = box["y"]+box["h"]*0.5
            yb = page.evaluate("() => chY(DRAW.items[0].points[0].price)")
            page.evaluate("() => window.__dc=0")
            page.mouse.move(px, pmid); page.mouse.down()
            page.mouse.move(px, pmid+140, steps=12); page.mouse.up(); page.wait_for_timeout(150)
            dc = page.evaluate("() => window.__dc")
            ya = page.evaluate("() => chY(DRAW.items[0].points[0].price)")
            check(dc > 0 and yb is not None and ya is not None and abs(ya - yb) > 5,
                  f"overlay tracks a vertical price-axis drag ({dc} redraws, level Y {yb and round(yb)}->{ya and round(ya)})")
            page.evaluate("() => { DRAW.items=[]; DRAW.sel=null; chDrawSave(); chDrawRender(); }")

            # 31. Refresh preserves paged-in history AND holds the viewport (V3.3
            #     Issue 7/8), REAL MOUSE. The periodic poll re-fetches only the base
            #     window; before the fix it replaced CH.data with it, discarding the
            #     older bars the user scrolled in — collapsing the series and yanking
            #     the viewport. Page in history with a real drag, then refresh.
            page.click("#ch-reset"); page.wait_for_timeout(200)
            for _ in range(3):
                c0 = page.evaluate("() => CH.data.candles.length")
                page.mouse.move(box["x"]+box["w"]*0.25, pmid); page.mouse.down()
                page.mouse.move(box["x"]+box["w"]*0.92, pmid, steps=18); page.mouse.up()
                try:
                    page.wait_for_function(f"() => CH.data.candles.length > {c0}", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
            n_hist = page.evaluate("() => CH.data.candles.length")
            vr0 = page.evaluate("() => { const r=CH.main.timeScale().getVisibleLogicalRange(); return [r.from, r.to]; }")
            page.evaluate("() => loadChart()"); page.wait_for_timeout(700)   # a refresh
            n_after = page.evaluate("() => CH.data.candles.length")
            vr1 = page.evaluate("() => { const r=CH.main.timeScale().getVisibleLogicalRange(); return [r.from, r.to]; }")
            preserved = n_hist > 500 and n_after >= n_hist - 2
            stable = abs(vr0[0]-vr1[0]) < 2 and abs(vr0[1]-vr1[1]) < 2
            check(preserved and stable,
                  f"refresh preserves paged-in history ({n_hist}->{n_after}) and holds viewport")
            page.evaluate("() => loadChart('SPY')"); page.click('#ch-tfs button[data-tf="1d"]'); wait_loaded("SPY", "1d")

            # ── V3.3.1 (chart reliability) ────────────────────────────────────

            # 32. A hung/slow backend must not leave a PERMANENT loading spinner
            #     (V3.3.1 primary bug: "loads blank, stays blank until restart").
            #     A bounded fetch times out into the recoverable error overlay, and
            #     the next poll recovers once the backend responds again. Uses the
            #     test-only __chFetchTimeoutMs override so it runs fast. Fails on
            #     pre-fix code (no timeout → the spinner never resolves).
            page.evaluate("() => { window.__chFetchTimeoutMs = 800; }")
            held = []
            hang = {"on": True}
            def hang_route(route):
                if hang["on"]:
                    held.append(route)                 # hold pending WITHOUT blocking the channel
                else:
                    route.fulfill(json=route.fetch().json())
            page.route("**/api/candles*", hang_route)
            page.fill("#ch-symbol", "COST"); page.press("#ch-symbol", "Enter")   # uncached first-paint
            try:
                page.wait_for_function(
                    "() => document.querySelector('#ch-overlay-retry').style.display !== 'none'",
                    timeout=6000)
                timed_out_to_error = True
            except Exception:  # noqa: BLE001
                timed_out_to_error = False
            hang["on"] = False
            for r in held:
                try: r.fulfill(json=r.fetch().json())
                except Exception: pass  # noqa: BLE001
            page.evaluate("() => loadChart()")
            try:
                wait_loaded("COST", "1d", 15000); recovered = True
            except Exception:  # noqa: BLE001
                recovered = False
            page.unroute("**/api/candles*", hang_route)
            page.evaluate("() => { window.__chFetchTimeoutMs = 0; }")
            check(timed_out_to_error and recovered,
                  "hung backend → bounded-timeout error overlay (not a permanent spinner), then auto-recovers")
            page.evaluate("() => loadChart('SPY')"); wait_loaded("SPY", "1d")

            # 33. Rapid symbol switching ABORTS superseded fetches instead of
            #     letting them all run to completion and pile onto the backend's
            #     serialized yfinance throttle (V3.3.1: the switch pile-up that
            #     starved the wanted symbol). Fails before the fix (0 aborts).
            aborted, finished = [], []
            page.on("requestfailed", lambda r: aborted.append(r.url)
                    if "/api/candles" in r.url and "abort" in ((r.failure or "")).lower() else None)
            burst = ["AAPL","NVDA","META","AMZN","TSLA","MSFT","GOOGL","AVGO","LLY","IWM","AMD"]
            for s in burst:
                page.fill("#ch-symbol", s); page.press("#ch-symbol", "Enter")
                page.wait_for_timeout(30)          # faster than a fetch completes → supersede
            try:
                wait_loaded("AMD", "1d", 15000); final_ok = True
            except Exception:  # noqa: BLE001
                final_ok = False
            page.wait_for_timeout(500)
            check(len(aborted) >= 5 and final_ok,
                  f"rapid switching aborts superseded fetches ({len(aborted)} aborted) and the last symbol loads")

            # 34. A non-monotonic payload (duplicate / out-of-order bar times) must
            #     be sanitized before setData — otherwise lightweight-charts throws
            #     "Value is null" from its own later paint frame, uncatchable by the
            #     setData try/catch (V3.3.1, confirmed via fault injection). The
            #     backend already dedupes+sorts; this is frontend defense-in-depth.
            #     Fails before the fix (an uncaught console error is raised).
            def corrupt_route(route):
                body = route.fetch().json()
                cs = body.get("candles")
                if cs and len(cs) > 6:
                    cs[3] = dict(cs[3]); cs[3]["time"] = cs[2]["time"]   # duplicate ts
                    cs[-1], cs[-2] = cs[-2], cs[-1]                       # out-of-order
                route.fulfill(json=body)
            errs_before = len([e for e in errors if "favicon" not in e])
            page.route("**/api/candles*", corrupt_route)
            page.fill("#ch-symbol", "NFLX"); page.press("#ch-symbol", "Enter")
            page.wait_for_timeout(1500)
            rendered = page.evaluate("() => CH.data && CH.data.candles.length > 3 "
                                     "&& !document.querySelector('#ch-overlay').classList.contains('show')")
            page.unroute("**/api/candles*", corrupt_route)
            new_errs = len([e for e in errors if "favicon" not in e]) - errs_before
            check(rendered and new_errs == 0,
                  f"non-monotonic payload is sanitized (chart renders, {new_errs} new console errors)")
            page.evaluate("() => loadChart('SPY')"); wait_loaded("SPY", "1d")

            browser.close()

        if errors:
            real = [e for e in errors if "favicon" not in e]
            if real:
                for e in real:
                    print(f"  FAIL console error: {e}")
                failures.append(f"{len(real)} console error(s)")

        if failures:
            print(f"\nFAIL: {len(failures)} chart check(s) failed.")
            return 1
        print("\nOK: all chart-system checks passed in a real headless browser.")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        for attempt in range(5):
            try:
                shutil.rmtree(scratch)
                break
            except OSError:
                if attempt == 4:
                    print(f"WARN: could not remove scratch dir {scratch} "
                          "(a file handle may still be open) - safe to delete by hand.")
                else:
                    time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
