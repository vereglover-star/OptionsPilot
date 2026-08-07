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
import urllib.parse
import urllib.request
from pathlib import Path
from shell_nav import goto, home_ready, ready  # noqa: E402

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


#: The ONE stubbed response, added with the M1-C6 Surface Level checks and
#: fulfilled at the HTTP boundary exactly as `guide_check.py` does it: the
#: chain's column set cannot be asserted without an option chain, and an option
#: chain cannot be fetched without a network. Everything downstream of this
#: payload — the column filter, the rendering, the row selection — is real.
#:
#: It answers about the symbol it was ASKED about, which matters more than it
#: looks: `loadChain` adopts the served symbol into the context, so a stub that
#: always said SPY would quietly drag the workspace to SPY and the loop test
#: below would be asserting the stub rather than the app.
def chain_for(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "expiration": "2026-08-21",
        "expirations": ["2026-08-21", "2026-09-18"],
        "spot": 500.0,
        "chain": [
            {"symbol": f"{symbol}260821{'C' if right == 'call' else 'P'}{strike:05d}000",
             "underlying": symbol, "expiration": "2026-08-21",
             "strike": float(strike), "right": right,
             "bid": 4.80, "ask": 5.20, "mid": 5.00,
             "delta": 0.50 if right == "call" else -0.50, "gamma": 0.02,
             "theta": -0.08, "vega": 0.15, "iv": 0.22, "open_interest": 4200,
             "volume": 900, "liquidity": 88.0, "dte": 24}
            for strike in range(490, 511, 5) for right in ("call", "put")
        ],
    }


def serve_chain(route):
    query = urllib.parse.urlparse(route.request.url).query
    symbol = urllib.parse.parse_qs(query).get("symbol", ["SPY"])[0].upper()
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps(chain_for(symbol)))


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
    goto(page, "charts")
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
    goto(page, "backtest")
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
    goto(page, "charts")
    page.wait_for_selector("#tab-charts", state="visible")
    page.fill("#ch-symbol", "TSL")
    page.press("#ch-symbol", "Tab")        # blur without pressing Enter
    settle(page)
    c.check("an abandoned half-edit is restored from the context on blur",
            page.input_value("#ch-symbol").upper() == "IWM",
            page.input_value("#ch-symbol"))
    c.check("and the abandoned text never reached the server",
            workspace(base)["symbol"] == "IWM", workspace(base)["symbol"])

    # ── one timeframe context (UI V2 M1-C5) ──────────────────────────────────
    # §4.5-2: "Changing the chart timeframe changes it everywhere a timeframe
    # applies, and it survives a symbol change."
    #
    # The second half is the one worth asserting, because it is the one a
    # careless symbol handler breaks: the timeframe is a statement about how
    # you read a market, not about which market.
    page.fill("#ch-symbol", "AMD")
    page.press("#ch-symbol", "Enter")
    page.wait_for_timeout(2500)
    active_tf = page.eval_on_selector_all(
        "#ch-tfs button.active", "els => els.map(e => e.dataset.tf)")
    c.check("the timeframe survives a symbol change", active_tf == ["15m"],
            str(active_tf))
    settle(page, 2500)
    c.check("and the server still holds it",
            workspace(base)["timeframe"] == "15m", workspace(base)["timeframe"])

    # And "everywhere a timeframe applies" — asserted structurally, because the
    # reason it holds is structural: the Trade tab's chart is not a second
    # chart, it is THE chart relocated into a second slot, so its timeframe
    # control is the same control. A test that read a copied value would pass
    # just as happily against two charts that agreed by luck.
    goto(page, "trade")
    page.wait_for_selector("#tab-trade", state="visible")
    expanded = page.get_attribute("#tk-chart-toggle", "aria-expanded")
    if expanded != "true":
        page.click("#tk-chart-toggle")
    page.wait_for_timeout(2500)
    c.check("the Trade tab hosts THE chart, not a second one",
            page.eval_on_selector(
                "#tk-chart-slot", "el => !!el.querySelector('#ch-tfs')"))
    trade_tf = page.eval_on_selector_all(
        "#tk-chart-slot #ch-tfs button.active", "els => els.map(e => e.dataset.tf)")
    c.check("so the timeframe on Trade is the timeframe on Charts",
            trade_tf == ["15m"], str(trade_tf))

    # Park the chart back on the Charts tab if this check is what moved it.
    # Leaving it in the Trade slot means `#ch-symbol` is inside a hidden tab
    # for everything below — which is exactly how this block first failed.
    if expanded != "true":
        page.click("#tk-chart-toggle")
        page.wait_for_timeout(1000)

    # Back to QQQ on the Charts tab, so the storage-loss checks below read
    # exactly what they did before this block existed.
    #
    # This transition is load-bearing, not tidy-up. Visiting Trade starts an
    # option-chain fetch for the PREVIOUS symbol, and typing a new one before
    # it returns is what caught M1-C4's staleness bug: the late response
    # adopted its own symbol and dragged the whole workspace back to it. The
    # storage-loss checks below are what noticed, several assertions later.
    goto(page, "charts")
    page.wait_for_selector("#tab-charts", state="visible")
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
    ready(page, timeout=25000)
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
    ready(page, timeout=25000)
    page.wait_for_timeout(3000)

    # An established profile lands on the Dashboard, exactly as it did before
    # V0.7.0. Adoption is a restore-after-LOSS path, not a "reopen where I left
    # off" feature: making every launch resume the last tab would be a change to
    # how the desktop app behaves, and this milestone is explicitly not that.
    # The asymmetry is deliberate and is recorded in ARCHITECTURE-PLATFORM.md §7.
    c.check("an established profile still lands on the Dashboard (unchanged "
            "desktop behaviour)", page.is_visible("#tab-dashboard"))

    goto(page, "charts")
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

    # ── Surface Level (UI V2 M1-C6) ──────────────────────────────────────────
    # §8's four invariants are what these assert, in the order they matter:
    # the level changes what is DISPLAYED, it never hides money, it is
    # reversible in both directions, and it is one level applied uniformly.
    goto(page, "settings")
    page.wait_for_selector("#sl-levels", state="visible")
    active = page.eval_on_selector_all(
        "#sl-levels button.active", "els => els.map(e => e.dataset.level)")
    c.check("Surface Level defaults to Full for an existing install",
            active == ["3"], str(active))

    def chain_headers():
        goto(page, "trade")
        page.wait_for_selector("#tk-chain table", timeout=15000)
        return page.eval_on_selector_all(
            "#tk-chain th", "els => els.map(e => e.textContent.trim())")

    def chain_rows():
        return page.eval_on_selector_all(
            "#tk-chain tr[data-strike]", "els => els.length")

    def set_level(n):
        goto(page, "settings")
        page.wait_for_selector("#sl-levels", state="visible")
        page.click(f'#sl-levels button[data-level="{n}"]')
        settle(page, 1200)

    full = chain_headers()
    full_rows = chain_rows()
    c.check("at Full the chain carries every column it measures",
            {"IV", "OI", "Delta"} <= set(full), str(full))

    set_level(1)
    guided = chain_headers()
    c.check("Guided hides the columns a first-time trader has no use for",
            not ({"IV", "OI", "Delta"} & set(guided)), str(guided))
    # The invariant that makes this progressive disclosure rather than a
    # crippled edition. Guided hides COMPLEXITY, never CONSEQUENCE.
    c.check("but never the money — strike and price are shown at every level",
            {"Strike", "Bid", "Ask", "Mid"} <= set(guided), str(guided))
    # §8.1-1: the level changes what is displayed, never what is available.
    # Hiding a column must not hide a contract.
    c.check("and every strike is still there to select",
            chain_rows() == full_rows and full_rows > 0,
            f"{chain_rows()} rows at Guided vs {full_rows} at Full")

    set_level(2)
    focused = chain_headers()
    c.check("Focused adds delta and nothing beyond it",
            "Delta" in focused and "IV" not in focused, str(focused))

    set_level(3)
    c.check("and the change is reversible in both directions",
            set(chain_headers()) == set(full), str(chain_headers()))

    set_level(1)
    settle(page, 1500)
    c.check("the level reaches the server",
            workspace(base)["surface_level"] == 1,
            str(workspace(base)["surface_level"]))

    page.reload()
    ready(page, timeout=25000)
    page.wait_for_timeout(2500)
    goto(page, "settings")
    page.wait_for_selector("#sl-levels", state="visible")
    active = page.eval_on_selector_all(
        "#sl-levels button.active", "els => els.map(e => e.dataset.level)")
    c.check("and survives a reload, on a client that keeps no local copy",
            active == ["1"], str(active))
    c.check("the chain comes back at the restored level too",
            not ({"IV", "OI", "Delta"} & set(chain_headers())),
            str(chain_headers()))

    # Back to Full so the reset assertion below reads the shipped state.
    set_level(3)

    # ── THE LOOP TEST (UI V2 M1-C7) ──────────────────────────────────────────
    # §4.5 states its own test, and this is it: "Type a symbol once at launch.
    # Complete a full loop — chart it, chain it, ticket it, review it, hold it,
    # journal it — and never type that symbol again. If the user has to retype
    # it, the workspace is not one workspace."
    #
    # M1 can reach chart → chain → ticket. Review, hold and journal are M4's
    # commit gesture and are added to this loop there, deliberately as an
    # EXTENSION of these assertions rather than a second test of the same
    # claim. The rule below is what makes the whole thing meaningful: after the
    # single `fill`, no assertion may type a symbol anywhere.
    goto(page, "charts")
    page.wait_for_selector("#tab-charts", state="visible")
    page.fill("#ch-symbol", "AMD")                  # ← the ONE time it is typed
    page.press("#ch-symbol", "Enter")
    page.wait_for_timeout(3000)
    c.check("LOOP: charted it", page.input_value("#ch-symbol").upper() == "AMD",
            page.input_value("#ch-symbol"))

    # Waited on the RESPONSE, not on the table: a table from the previous
    # symbol is already on screen, so `wait_for_selector` returns instantly and
    # the assertion reads the old render. That is how this check first failed —
    # it reported the previous symbol's spot, which was true at the moment it
    # looked.
    with page.expect_response(lambda r: "/api/chain" in r.url, timeout=20000):
        goto(page, "trade")
    page.wait_for_selector("#tk-chain table", timeout=15000)
    page.wait_for_timeout(800)
    spot = page.text_content("#tk-spot") or ""
    c.check("LOOP: chained it, without typing it again", "AMD" in spot, spot)

    page.click("#tk-chain tr[data-strike]")
    page.wait_for_selector("#tk-form", state="visible", timeout=10000)
    selected = page.text_content("#tk-selected") or ""
    c.check("LOOP: ticketed it, still without typing it again",
            "AMD" in selected, selected.strip()[:60])

    settle(page, 2000)
    stored = workspace(base).get("contract") or {}
    c.check("the selection is server-owned, like everything else here",
            stored.get("symbol") == "AMD" and stored.get("strike"),
            str(stored))

    # §4.5-3, the half that is easy to lose: "...and remains selected if the
    # user visits Research and returns."
    goto(page, "journal")
    page.wait_for_selector("#tab-journal", state="visible")
    goto(page, "trade")
    page.wait_for_selector("#tk-form", state="visible", timeout=10000)
    c.check("LOOP: and it is still selected after leaving and coming back",
            "AMD" in (page.text_content("#tk-selected") or ""),
            (page.text_content("#tk-selected") or "").strip()[:60])

    # §4.5-8: all of the above is server-owned state, so it survives the loss
    # of the client. This is the assertion that would fail if the selection had
    # been kept in `localStorage` — which is where every fact in this file used
    # to live, and why this suite exists.
    strike_before = stored.get("strike")
    page.evaluate("localStorage.clear()")
    page.reload()
    ready(page, timeout=25000)
    page.wait_for_timeout(3000)
    goto(page, "trade")
    page.wait_for_selector("#tk-chain table", timeout=15000)
    page.wait_for_timeout(1500)
    restored_sel = page.text_content("#tk-selected") or ""
    c.check("LOOP: the contract comes back after client storage is wiped",
            "AMD" in restored_sel and str(int(strike_before)) in restored_sel,
            restored_sel.strip()[:70])

    # And the rule that keeps the loop honest in the other direction: a
    # contract belongs to an underlying. The server enforces this too — two
    # gates, one rule.
    goto(page, "charts")
    page.wait_for_selector("#tab-charts", state="visible")
    page.fill("#ch-symbol", "NVDA")
    page.press("#ch-symbol", "Enter")
    settle(page, 2500)
    c.check("moving the symbol drops the selection, on the server too",
            workspace(base).get("contract") is None,
            str(workspace(base).get("contract")))

    # ── 16: reset ────────────────────────────────────────────────────────────
    req = urllib.request.Request(base + "/api/workspace", method="DELETE")
    reset = json.loads(urllib.request.urlopen(req).read())
    c.check("reset returns to the shipped defaults",
            reset["symbol"] == "SPY" and reset["layouts"] == {},
            f"{reset['symbol']} {reset['layouts']}")
    c.check("but leaves Surface Level alone — it is not part of the document",
            reset["surface_level"] == 3, str(reset["surface_level"]))


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
            page.route("**/api/chain*", serve_chain)
            page.goto(base)
            home_ready(page, 25000)
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
