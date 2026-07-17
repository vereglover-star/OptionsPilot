"""Headless-browser smoke check: launches the app in serve mode against a
scratch (throwaway) data directory, visits every tab, and fails on any
browser console error.

Uses Playwright driving the system's installed Edge (`channel="msedge"` -
no browser binary download, no CDN, works offline). This formalizes an
approach used ad hoc in a 2026-07-17 session to verify the Charts tab
(screenshots, console-error checking, interaction scripting all worked
well there) into something committed and repeatable.

Soft-skips (exit 0, prints a note) if the `playwright` package isn't
installed - it's the optional `[browser]` extra, not a hard dependency of
the project. Pass --require to make a missing install a hard failure
instead (useful once the extra is standard on every machine that runs
scripts/verify.ps1).

Never touches the real data/ directory: runs from a fresh temporary
working directory so the app's default (packaged) config and a brand-new
paper account are used, then deletes that directory afterward.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABS = ["dashboard", "charts", "trade", "coach", "watchlist",
        "journal", "backtest", "learning", "settings"]


def wait_for(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001 - just means "not up yet"
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--require", action="store_true",
                     help="fail (not skip) if playwright isn't installed")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = ('playwright not installed - run `pip install -e ".[browser]"` '
               "to enable this check.")
        if args.require:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-smoke-"))
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "serve",
         "--port", str(args.port), "--no-loop"],
        cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for(base + "/api/status"):
            print("FAIL: dev server did not come up in time")
            return 1

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console",
                    lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(base)
            page.wait_for_selector("#hero", timeout=20000)
            for tab in TABS:
                page.click(f'nav button[data-tab="{tab}"]')
                page.wait_for_selector(f"#tab-{tab}", state="visible", timeout=10000)
                page.wait_for_timeout(250)
            browser.close()

        if errors:
            print(f"FAIL: {len(errors)} browser console error(s):")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"OK: all {len(TABS)} tabs loaded via a real headless browser, "
              "zero console errors.")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        # SQLite/log file handles can linger briefly past process exit on
        # Windows even after wait() returns - retry instead of silently
        # leaking scratch directories under %TEMP% forever.
        for attempt in range(5):
            try:
                shutil.rmtree(scratch)
                break
            except OSError:
                if attempt == 4:
                    print(f"WARN: could not remove scratch dir {scratch} "
                          "(a file handle may still be open) - safe to "
                          "delete by hand later.")
                else:
                    time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
