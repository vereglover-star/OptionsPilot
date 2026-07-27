"""Browser-based history probe for the chart pipeline.

Starts the app locally, drives the Charts tab in a real headless browser,
scrolls history back until the chart stops loading older bars, and compares
that oldest visible bar against the oldest bar returned by the provider.

The output is easy to scan and mirrors the request format:

    SPY
    1m  -> oldest available: 2026-07-15
    2m  -> oldest available: 2026-07-15
    ...
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from optionspilot.core.models import Timeframe
from optionspilot.data.yfinance_provider import YFinanceProvider

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["SPY", "QQQ", "NVDA", "AAPL"]
TIMEFRAMES = ["1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d"]


def wait_for(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def tf_enum(name: str) -> Timeframe:
    mapping = {
        "1m": Timeframe.M1,
        "2m": Timeframe.M2,
        "3m": Timeframe.M3,
        "5m": Timeframe.M5,
        "10m": Timeframe.M10,
        "15m": Timeframe.M15,
        "30m": Timeframe.M30,
        "1h": Timeframe.H1,
        "2h": Timeframe.H2,
        "4h": Timeframe.H4,
        "1d": Timeframe.D1,
    }
    return mapping[name]


def as_iso_utc(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--require", action="store_true", help="fail if playwright is missing")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = 'playwright not installed - run `pip install -e ".[dev,browser]"` to enable this check.'
        if args.require:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-history-"))
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "serve", "--port", str(args.port), "--no-loop"],
        cwd=scratch,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    errors: list[str] = []
    provider = YFinanceProvider(min_request_interval=0.0)

    try:
        if not wait_for(base + "/api/status", timeout=45.0):
            print("FAIL: dev server did not come up in time")
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.add_init_script("window.__chNoAutoRefresh = true;")
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(base)
            page.click('nav button[data-tab="charts"]')
            page.wait_for_selector("#ch-symbol", timeout=30000)

            for symbol in SYMBOLS:
                print(symbol)
                for tf in TIMEFRAMES:
                    page.fill("#ch-symbol", symbol)
                    page.press("#ch-symbol", "Enter")
                    page.click(f'#ch-tfs button[data-tf="{tf}"]')
                    page.wait_for_function(
                        "() => !!CH && !!CH.data && CH.data.candles.length > 3",
                        timeout=120000,
                    )

                    # Scroll history left until the UI reports exhaustion or no further
                    # historical bars are discovered. This exercises the same path the browser
                    # uses when the user pans deep into older bars, and it keeps going until
                    # the chart stops advancing rather than stopping after a small handful of
                    # requests.
                    oldest_seen = None
                    for _ in range(80):
                        page.evaluate("""
                        () => {
                          const ts = CH.main.timeScale();
                          ts.setVisibleLogicalRange({ from: 0, to: 80 });
                          CH.historyArmed = true;
                        }
                        """)
                        page.wait_for_function("() => !CH.historyLoading", timeout=60000)
                        oldest_now = page.evaluate("""
                        () => {
                          const candles = CH?.data?.candles || [];
                          return candles.length ? candles[0].time : null;
                        }
                        """)
                        if oldest_seen is not None and oldest_now == oldest_seen and page.evaluate("() => CH.historyExhausted"):
                            break
                        oldest_seen = oldest_now
                        if page.evaluate("() => CH.historyExhausted"):
                            break

                    oldest_visible = page.evaluate("""
                    () => {
                      const candles = CH?.data?.candles || [];
                      if (!candles.length) return null;
                      return candles[0].time;
                    }
                    """)
                    oldest_visible_date = as_iso_utc(oldest_visible)

                    end = datetime.now(timezone.utc)
                    start = end - timedelta(days=4000)
                    df = provider.get_candles(symbol, tf_enum(tf), start, end)
                    if df.empty:
                        oldest_provider_date = None
                    else:
                        oldest_provider_date = df.index[0].to_pydatetime().astimezone(timezone.utc).date().isoformat()

                    blank = page.evaluate("() => !CH?.data?.candles?.length")
                    chart_ok = not blank and oldest_visible_date is not None
                    if not chart_ok:
                        failures.append(f"{symbol} {tf}: blank chart")
                    if oldest_provider_date and oldest_visible_date != oldest_provider_date:
                        failures.append(
                            f"{symbol} {tf}: visible {oldest_visible_date} != provider {oldest_provider_date}"
                        )

                    print(f"{tf:>3} -> oldest available: {oldest_visible_date or 'n/a'}"
                          f" | provider: {oldest_provider_date or 'n/a'}")

                    # Switching away and back should not drop the history already paged in.
                    page.click('#ch-tfs button[data-tf="1d"]')
                    page.wait_for_function(
                        "() => !!CH && !!CH.data && CH.data.candles.length > 3",
                        timeout=120000,
                    )
                    page.click(f'#ch-tfs button[data-tf="{tf}"]')
                    page.wait_for_function(
                        "() => !!CH && !!CH.data && CH.data.candles.length > 3",
                        timeout=120000,
                    )
                    persisted_oldest = page.evaluate("""
                    () => {
                      const candles = CH?.data?.candles || [];
                      if (!candles.length) return null;
                      return candles[0].time;
                    }
                    """)
                    persisted_oldest_date = as_iso_utc(persisted_oldest)
                    if persisted_oldest_date is not None and oldest_visible_date is not None:
                        if persisted_oldest_date > oldest_visible_date:
                            failures.append(
                                f"{symbol} {tf}: history regressed after tf switch ({persisted_oldest_date} > {oldest_visible_date})"
                            )

                print()

            browser.close()

        if errors:
            failures.append(f"browser console errors: {len(errors)}")
        if failures:
            print("FAILURES:")
            for item in failures:
                print(f"  - {item}")
            return 1

        print("OK: chart history reached the provider boundary for every checked symbol/timeframe")
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
                    print(f"WARN: could not remove scratch dir {scratch}")
                else:
                    time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
