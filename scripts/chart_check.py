"""Comprehensive chart-system regression check in a real headless browser.

Launches the app in serve mode against a scratch (throwaway) data directory
and drives the Charts tab through the behaviors the chart system must never
regress on. Each check prints OK/FAIL; any failure (or any browser console
error) fails the run. Covers, per the V3.1 chart-stabilization sprint:

  ticker loading · invalid ticker + renderer recovery · every timeframe ·
  indicator toggling · drawing create/edit/delete/persistence (the editable
  object model) · historical scroll-back · zoom · stale-cache banner ·
  cache invalidation / rapid symbol changes · resize · live intrabar update
  (no view jump) · single-instance / no-leak guard.

Uses Playwright driving the system's installed Edge (channel="msedge" - no
browser download, offline-capable), matching scripts/browser_check.py.
Soft-skips (exit 0) if Playwright isn't installed; pass --require to make a
missing install a hard failure. Never touches the real data/ directory.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

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

            # 12. stale-cache banner: a controlled route returns stale bars, then
            #     fresh bars on retry. Fully deterministic — no dependence on live
            #     feed freshness (which rate-limiting can legitimately make stale).
            force_stale = [True]

            def stale_route(route):
                resp = route.fetch()
                body = resp.json()
                if body.get("candles"):
                    body["stale"] = force_stale[0]
                    body["as_of"] = body["candles"][-1]["time"] if force_stale[0] else None
                route.fulfill(json=body)
            page.route("**/api/candles*", stale_route)
            page.evaluate("() => loadChart()")
            page.wait_for_function(
                "() => document.querySelector('#ch-stale').classList.contains('show')",
                timeout=30000)
            check(True, "stale payload shows the cached-bars banner")
            force_stale[0] = False
            page.click("#ch-stale-retry")
            page.wait_for_function(
                "() => !document.querySelector('#ch-stale').classList.contains('show')",
                timeout=30000)
            check(True, "Retry-live clears the stale banner")
            page.unroute("**/api/candles*")

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

            # 16. single-instance / no-leak guard: no extra chart canvases spawned
            canvas_after = page.evaluate("() => document.querySelectorAll('#ch-main canvas').length")
            check(canvas_after == canvas_before and page.evaluate("() => !!CH.main"),
                  "one chart instance throughout (no per-switch canvas leak)")

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
