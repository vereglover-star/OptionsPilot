"""Headless-browser certification of the server-owned workspace (V0.7.0).

Sibling to `guide_check.py`, `chart_check.py`, `marketdata_check.py` and
`intelligence_check.py`, and written because the claim this milestone makes
about the workspace is a claim about what a user SEES after a specific,
awkward-to-reproduce event: they cleared their browser profile, restored a
backup, or reinstalled — and their chart came back anyway.

That claim cannot be tested from Python. `tests/test_services_endpoints.py`
proves the state survives a *server* restart, which is a different and easier
thing: the server never lost it. What had to be proven here is that the state
survives the loss of the CLIENT's storage, which is the case the feature exists
for and the only one where the old behaviour was wrong.

The canonical check is 5-7: wipe `localStorage` entirely, reload, and assert the
symbol and timeframe on screen are the ones chosen before the wipe — read from
the visible controls, not from `CH`, per this project's standing rule that a
check asserts what the user sees.

Entirely offline. Soft-skips (exit 0) if Playwright isn't installed, matching
its siblings.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Checks:
    """Collects every failure rather than stopping at the first."""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  ok   {label}")
            return True
        self.failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        return False

    @property
    def total(self) -> int:
        return self.passed + len(self.failures)


def wait_for(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001 — just means "not up yet"
            time.sleep(0.3)
    return False


def workspace(base: str) -> dict:
    return json.loads(urllib.request.urlopen(base + "/api/workspace").read())


def settle(page, ms: int = 1400) -> None:
    """The mirror is debounced at 600ms; give it room plus the round trip."""
    page.wait_for_timeout(ms)


def seed_onboarded(base: str) -> None:
    """A scratch profile has by definition never been onboarded, and the welcome
    dialog would sit over every assertion. Same treatment every sibling suite
    gives it (browser_check is the deliberate exception)."""
    req = urllib.request.Request(
        base + "/api/guide/state", method="POST",
        data=json.dumps({"onboarded": True}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def run_checks(page, base: str, c: Checks) -> None:
    # ── 1-3: a fresh profile starts on the shipped defaults ──────────────────
    doc = workspace(base)
    c.check("a fresh install reports the shipped defaults",
            doc["symbol"] == "SPY" and doc["timeframe"] == "1d",
            f"{doc['symbol']} {doc['timeframe']}")
    c.check("and no saved layouts or recents", not doc["layouts"]
            and not doc["recent_symbols"])
    c.check("the tab defaults to the dashboard", doc["tab"] == "dashboard",
            doc["tab"])

    # ── 4-6: the client mirrors real interactions up ─────────────────────────
    page.click('nav button[data-tab="charts"]')
    page.wait_for_selector("#tab-charts", state="visible")
    settle(page)
    c.check("switching tab is mirrored to the server",
            workspace(base)["tab"] == "charts", workspace(base)["tab"])

    page.fill("#ch-symbol", "QQQ")
    page.press("#ch-symbol", "Enter")
    page.wait_for_timeout(2500)
    page.click('#ch-tfs button[data-tf="15m"]')
    settle(page, 2500)

    doc = workspace(base)
    c.check("a symbol change reaches the server", doc["symbol"] == "QQQ",
            doc["symbol"])
    c.check("a timeframe change reaches the server", doc["timeframe"] == "15m",
            doc["timeframe"])
    c.check("the symbol is promoted into the recents list",
            doc["recent_symbols"][:1] == ["QQQ"], str(doc["recent_symbols"]))

    # ── one symbol context (UI V2 M1-C4) ─────────────────────────────────────
    # §4.5-1: "There is exactly one active symbol for the workspace. Setting it
    # on the chart sets it for the chain, the ticket, Research and Home's
    # context strip." Before M1-C4 these were three independent boxes that each
    # defaulted to SPY, so charting NVDA and opening Trade offered a SPY chain.
    #
    # Asserted from the BOXES rather than from `Ctx.symbol()`, because the
    # claim is about what the user sees. Driven through the chart box and the
    # backtest box, neither of which fetches an option chain — the chain path
    # is a network call and this suite is meant to be reproducible offline.
    c.check("a chart symbol renders into the ticket's box",
            page.input_value("#tk-symbol").upper() == "QQQ",
            page.input_value("#tk-symbol"))
    c.check("and into the backtest's box",
            page.input_value("#bt-symbol").upper() == "QQQ",
            page.input_value("#bt-symbol"))

    # And the other direction: any box may commit the context, not just the
    # chart's. `change` is the backtest box's commit (blur or Enter).
    page.click('nav button[data-tab="backtest"]')
    page.wait_for_selector("#tab-backtest", state="visible")
    page.fill("#bt-symbol", "IWM")
    page.press("#bt-symbol", "Enter")
    settle(page, 2000)
    c.check("committing a symbol elsewhere moves the chart's box too",
            page.input_value("#ch-symbol").upper() == "IWM",
            page.input_value("#ch-symbol"))
    c.check("and the ticket's",
            page.input_value("#tk-symbol").upper() == "IWM",
            page.input_value("#tk-symbol"))
    c.check("and it reaches the server without a chart ever loading",
            workspace(base)["symbol"] == "IWM", workspace(base)["symbol"])

    # A box left half-edited must not sit there disagreeing with the context.
    # Asserted on `#ch-symbol`, which commits on Enter only — `#bt-symbol`
    # commits on `change`, and a browser fires `change` on blur, so for that
    # box leaving it IS committing it. Writing this check the other way round
    # is what established that: it failed, and it was the test that was wrong.
    page.click('nav button[data-tab="charts"]')
    page.wait_for_selector("#tab-charts", state="visible")
    page.fill("#ch-symbol", "TSL")
    page.press("#ch-symbol", "Tab")        # blur without pressing Enter
    settle(page)
    c.check("an abandoned half-edit is restored from the context on blur",
            page.input_value("#ch-symbol").upper() == "IWM",
            page.input_value("#ch-symbol"))
    c.check("and the abandoned text never reached the server",
            workspace(base)["symbol"] == "IWM", workspace(base)["symbol"])

    # Back to QQQ, so the storage-loss checks below read exactly what they did
    # before this block existed.
    page.fill("#ch-symbol", "QQQ")
    page.press("#ch-symbol", "Enter")
    page.wait_for_timeout(2500)
    settle(page, 2500)

    # ── 7: an indicator toggle, which is a translated field ──────────────────
    before = set(workspace(base)["indicators"])
    page.click('#ch-inds button[data-ind="rsi"]')
    settle(page)
    after = set(workspace(base)["indicators"])
    c.check("an indicator toggle is mirrored as a NAME list, not a bool map",
            after.symmetric_difference(before) == {"rsi"},
            f"{sorted(before)} -> {sorted(after)}")

    # ── 8-11: THE canonical check — survive the loss of client storage ───────
    page.evaluate("localStorage.clear()")
    c.check("client storage really was wiped",
            page.evaluate("localStorage.length") == 0)

    page.reload()
    # NOT `#hero`: it lives on the dashboard, and the whole point of this
    # reload is that adoption may land the user back on the Charts tab.
    page.wait_for_selector('nav button[data-tab="charts"]', timeout=25000)
    page.wait_for_timeout(3000)

    # Read what is ON SCREEN, not what CH holds — the standing rule.
    symbol = page.input_value("#ch-symbol")
    c.check("after a profile wipe the workspace SYMBOL is restored on screen",
            symbol.upper() == "QQQ", symbol)
    active_tf = page.eval_on_selector_all(
        "#ch-tfs button.active", "els => els.map(e => e.dataset.tf)")
    c.check("after a profile wipe the TIMEFRAME control is restored on screen",
            active_tf == ["15m"], str(active_tf))
    c.check("after a profile wipe the TAB is restored",
            page.is_visible("#tab-charts"))
    active_inds = page.eval_on_selector_all(
        "#ch-inds button.active", "els => els.map(e => e.dataset.ind)")
    c.check("after a profile wipe the indicator pills reflect the restored set",
            ("rsi" in active_inds) == ("rsi" in after), str(active_inds))

    # ── 12: adoption re-seeds local storage, so the next launch is fast ──────
    c.check("adoption re-seeds localStorage rather than fetching every launch",
            page.evaluate("localStorage.getItem('chSym')") == "QQQ",
            str(page.evaluate("localStorage.getItem('chSym')")))

    # ── 13: an ESTABLISHED profile is not overwritten by the server ──────────
    # The other direction, and the one that would be a data-loss bug: a client
    # that already has state must teach the server, never be overwritten by it.
    page.evaluate("localStorage.setItem('chSym', 'IWM')")
    page.reload()
    page.wait_for_selector('nav button[data-tab="charts"]', timeout=25000)
    page.wait_for_timeout(3000)

    # An established profile lands on the Dashboard, exactly as it did before
    # V0.7.0. Adoption is a restore-after-LOSS path, not a "reopen where I left
    # off" feature: making every launch resume the last tab would be a change to
    # how the desktop app behaves, and this milestone is explicitly not that.
    # The asymmetry is deliberate and is recorded in ARCHITECTURE-PLATFORM.md §7.
    c.check("an established profile still lands on the Dashboard (unchanged "
            "desktop behaviour)", page.is_visible("#tab-dashboard"))

    page.click('nav button[data-tab="charts"]')
    page.wait_for_selector("#tab-charts", state="visible")
    page.wait_for_timeout(1500)
    c.check("an established profile keeps its OWN state and pushes it up",
            page.input_value("#ch-symbol").upper() == "IWM",
            page.input_value("#ch-symbol"))
    settle(page, 2500)
    c.check("and the server learns it rather than reverting the client",
            workspace(base)["symbol"] == "IWM", workspace(base)["symbol"])

    # ── 14-15: the workspace write must not disturb its file-mates ───────────
    # `data/settings.json#workspace` is a shared_writer entry in the sync
    # inventory; this is the concrete hazard it flags.
    status = json.loads(urllib.request.urlopen(base + "/api/status").read())
    c.check("a workspace write leaves the trading mode alone",
            status["trading_mode"] in ("conservative", "high_risk", "custom"),
            str(status["trading_mode"]))
    c.check("a workspace write leaves the watchlist alone",
            isinstance(status["watchlist"], list) and status["watchlist"])

    # ── 16: reset ────────────────────────────────────────────────────────────
    req = urllib.request.Request(base + "/api/workspace", method="DELETE")
    reset = json.loads(urllib.request.urlopen(req).read())
    c.check("reset returns to the shipped defaults",
            reset["symbol"] == "SPY" and reset["layouts"] == {},
            f"{reset['symbol']} {reset['layouts']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8804)
    ap.add_argument("--require", action="store_true",
                    help="fail (not skip) if playwright isn't installed")
    ap.add_argument("--headed", action="store_true")
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-workspace-"))
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "--config", str(ROOT / "config.yaml"),
         "serve", "--port", str(args.port), "--no-loop"],
        cwd=scratch, env={**os.environ, "OPTIONSPILOT_HOME": str(scratch)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    c = Checks()
    console: list[str] = []
    try:
        if not wait_for(base + "/api/status"):
            print("FAIL: dev server did not come up in time")
            return 1
        seed_onboarded(base)

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on("console",
                    lambda m: console.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(str(e)))
            page.goto(base)
            page.wait_for_selector("#hero", timeout=25000)
            page.wait_for_timeout(1200)
            run_checks(page, base, c)
            browser.close()

        c.check("zero browser console errors", not console, "; ".join(console[:3]))

        if c.failures:
            print(f"\nFAIL: {len(c.failures)} of {c.total} workspace checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"\nOK: {c.passed}/{c.total} workspace checks passed in a real "
              f"headless browser.")
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
                    break
                time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
