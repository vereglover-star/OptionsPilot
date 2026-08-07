"""Headless-browser certification of the Trade destination (UI V2 M4).

**Written before the destination it certifies**, deliberately. M2 brought its
equivalent forward out of the planned order because it is the only commit that
protects everything the others build, and M4's frontend tier is seven
consecutive commits of new surface. Seven commits with no behavioural coverage
is how a destination ships broken.

So this file is organised around the WORKFLOW rather than around the markup,
and the section headings below are the questions §6 says Trade exists to
answer. Sections marked (M4-Cn) are filled in by the commit that builds the
behaviour; a section with no assertions yet says so out loud rather than
existing as an empty function that reports success.

    1. What symbol am I looking at?          C3
    2. What is happening right now?          C3
    3. What contracts are available?         C5   <- expiry labelling: HERE
    4. Which contract matches my intent?     C6
    5. What will this trade cost?            C4/C7
    6. What is my actual risk?               C7
    7. Should I place this order?            C8/C9

Two rules this file holds itself to, both learned the hard way in M3:

  * **No existence tests.** "The element is present" passed all through the
    period when the nav rail's icons were being sliced in half and its links
    were announcing no name at all. Assert the property a user experiences.
  * **Every assertion must fail against the previous build.** Where that is
    not obvious, the check says what the old behaviour measured — see the
    expiry-label checks, which carry the numbers the old JavaScript produced.

Entirely offline. Soft-skips (exit 0) if Playwright isn't installed, matching
its siblings.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shell_nav import goto, home_ready  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    @property
    def total(self) -> int:
        return self.passed + len(self.failures)

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  ok   {label}")
            return True
        self.failures.append(f"{label}{' - ' + detail if detail else ''}")
        print(f"  FAIL {label}{' - ' + detail if detail else ''}")
        return False

    def note(self, label: str) -> None:
        """A section that has no behaviour to assert yet.

        Printed rather than silently skipped, so the gate's coverage is
        legible from its own output instead of from this file's source.
        """
        print(f"  --   {label} (not yet built)")


def wait_for(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def post(base: str, path: str, body: dict) -> None:
    req = urllib.request.Request(
        base + path, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def _chain_payload(today: date) -> dict:
    """A chain whose expiries sit at known distances from today.

    Built relative to the browser's own date so the labels asserted below are
    facts about the convention rather than about a frozen fixture.
    """
    def iso(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    rows = [{"strike": s, "right": r, "bid": 3.85, "ask": 3.95, "mid": 3.90,
             "delta": 0.5, "iv": 0.18, "volume": 1200, "open_interest": 4000,
             "liquidity": 80, "dte": 0}
            for s in (465.0, 470.0, 475.0) for r in ("call", "put")]
    return {
        "symbol": "SPY", "spot": 471.20, "expiration": iso(0),
        "expirations": [iso(0), iso(1), iso(3), iso(30)],
        "expiries": [
            {"date": iso(0), "dte": 0, "label": "0DTE", "plain": "Today",
             "expired": False},
            {"date": iso(1), "dte": 1, "label": "1DTE", "plain": "Tomorrow",
             "expired": False},
            {"date": iso(3), "dte": 3, "label": "3DTE", "plain": "3 days",
             "expired": False},
            {"date": iso(30), "dte": 30, "label": "30DTE", "plain": "30 days",
             "expired": False},
        ],
        "chain": rows,
    }


def open_trade(browser, base, *, width=1920, height=1080, console=None,
               chain=None):
    """A page showing the Trade destination with a known chain."""
    page = browser.new_page(viewport={"width": width, "height": height})
    if console is not None:
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
    if chain is not None:
        page.route("**/api/chain*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(chain)))
    page.goto(base, wait_until="domcontentloaded")
    home_ready(page, 25000)
    goto(page, "trade")
    page.wait_for_timeout(900)
    return page


# ── 1-2. What symbol am I looking at, and what is happening? ─────────────────

WORKSPACE_JS = """() => {
  const box = id => {
    const e = document.getElementById(id);
    if (!e) return null;
    const b = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return {x: +b.left.toFixed(1), r: +b.right.toFixed(1),
            y: +b.top.toFixed(1), b: +b.bottom.toFixed(1),
            w: +b.width.toFixed(1), h: +b.height.toFixed(1),
            area: +(b.width * b.height).toFixed(0),
            visible: cs.display !== 'none' && cs.visibility !== 'hidden'
                     && b.width > 0 && b.height > 0,
            focal: e.classList.contains('ins--focal')};
  };
  return {chart: box('trade-chart'), chain: box('trade-chain'),
          ticket: box('trade-ticket'),
          focals: document.querySelectorAll('#tab-trade .ins--focal').length,
          pfInTicket: !!document.querySelector('#trade-ticket #pf-blocks')
                      && getComputedStyle(
                           document.querySelector('#trade-ticket #pf-blocks')
                         ).display !== 'none'};
}"""


def check_workspace(browser, base, c: Checks, console: list) -> None:
    """The three regions of §6.2, all present at once (M4-C3).

    Fails against the previous build on every assertion: the chart lived
    behind a collapsed toggle that defaulted to CLOSED, so a user arriving at
    Trade saw a chain and a ticket and had to know to expand a chart — §6.1's
    first named fault, that the flow spans two destinations with a
    collapsible chart as the bridge.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()))
    w = page.evaluate(WORKSPACE_JS)

    for name in ("chart", "chain", "ticket"):
        c.check(f"the {name} is present without being expanded first",
                w[name] is not None and w[name]["visible"],
                str(w[name]))
    if not all(w[k] and w[k]["visible"] for k in ("chart", "chain", "ticket")):
        page.close()
        return

    # The chart is dominant by AREA and the ticket is focal by ELEVATION.
    # Two channels; asserting both is what stops one being traded for the
    # other in a later "tidy-up".
    c.check("the chart is the largest region on the screen",
            w["chart"]["area"] > w["chain"]["area"]
            and w["chart"]["area"] > w["ticket"]["area"],
            f"chart={w['chart']['area']} chain={w['chain']['area']} "
            f"ticket={w['ticket']['area']}")
    c.check("exactly one region is focal, and it is the ticket",
            w["focals"] == 1 and w["ticket"]["focal"],
            f"{w['focals']} focal regions; ticket focal={w['ticket']['focal']}")

    # Workflow order: chart above chain, ticket to the right of both.
    c.check("the chain sits below the chart, not beside it",
            w["chain"]["y"] >= w["chart"]["b"] - 1,
            f"chart ends {w['chart']['b']}, chain starts {w['chain']['y']}")
    c.check("the ticket is the last step, to the right of the workspace",
            w["ticket"]["x"] >= w["chart"]["r"] - 1,
            f"chart ends {w['chart']['r']}, ticket starts {w['ticket']['x']}")

    # §6.1's last named fault: the ticket column became a long scroll holding
    # four unrelated things.
    c.check("positions, orders and history are not stacked in the ticket",
            not w["pfInTicket"])

    page.close()


def check_seam_matches_home(browser, base, c: Checks, console: list) -> None:
    """Trade's column seam is Home's column seam (M4-C3).

    The point of making `--split-major` a token in M3.5-C4 was that the next
    destination would inherit the line rather than pick its own. This is the
    assertion that the inheritance actually happened — and it is the one most
    likely to be broken by a future destination written in a hurry.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()))
    trade_seam = page.eval_on_selector(
        "#trade-ticket", "e => +e.getBoundingClientRect().left.toFixed(1)")
    goto(page, "dashboard")
    page.wait_for_timeout(900)
    home_seam = page.eval_on_selector(
        "#home-next", "e => +e.getBoundingClientRect().left.toFixed(1)")
    c.check("Trade's column seam is the same line as Home's",
            abs(trade_seam - home_seam) <= 1,
            f"Trade {trade_seam}, Home {home_seam}")
    page.close()


# ── 3. What contracts are available? ─────────────────────────────────────────

def check_expiry_labels(browser, base, c: Checks, console: list) -> None:
    """The expiry strip, against the options-platform convention (M4).

    The behaviour this replaces, measured: the strip computed its own
    days-to-expiry as a TIME delta rounded up, so on expiration day it read
    "1d", tomorrow read "2d", three days out read "4d" and thirty days out
    read "31d" — one high at every hour of the trading day except after 16:00
    local on expiration day itself. Every assertion below fails against that
    build.
    """
    today = date.today()
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(today))

    # The whole rendered text of each pill, not the contents of a span this
    # milestone introduced. Asserting on `.exp-dte` alone would fail against
    # the old build merely because that element did not exist, which is an
    # existence test wearing a behavioural one's clothes. Reading the full
    # text means the old build fails on its VALUE — it rendered "Aug 7 · 1d"
    # where the convention requires 0DTE.
    pills = page.eval_on_selector_all(
        "#tk-exps button",
        "els => els.map(e => ({text: (e.textContent || '').trim(),"
        " title: e.getAttribute('title') || ''}))")

    c.check("the expiry strip renders one pill per listed expiration",
            len(pills) == 4, f"got {len(pills)}")
    if len(pills) != 4:
        page.close()
        return

    texts = [p["text"] for p in pills]
    # `expected` is what the convention requires; `was` is what the previous
    # build measurably rendered, kept in the failure message so a regression
    # names the defect it re-introduced rather than just a mismatch.
    for i, (expected, was, when) in enumerate((
            ("0DTE", "1d", "today"),
            ("1DTE", "2d", "tomorrow"),
            ("3DTE", "4d", "three days out"),
            ("30DTE", "31d", "thirty days out"))):
        c.check(f"{when} is labelled {expected} (the old build showed {was})",
                expected in texts[i] and f"·{was}" not in texts[i].replace(" ", ""),
                f"rendered {texts[i]!r}")

    # §1.4's anti-bifurcation rule: one screen for both audiences. The trader's
    # abbreviation is on the pill, the beginner's wording in its tooltip.
    c.check("same-day expiry says 'Today' in plain language too",
            "Today" in pills[0]["title"], pills[0]["title"])
    c.check("next-day expiry says 'Tomorrow' in plain language too",
            "Tomorrow" in pills[1]["title"], pills[1]["title"])

    # The defect that made this worth a module: two calculations, one screen.
    # The chain rows carry the server's `dte`; the pill must not disagree.
    row_dte = page.evaluate(
        "() => (window.tkChain && tkChain.chain && tkChain.chain.length)"
        " ? tkChain.chain[0].dte : null")
    c.check("the strip and the chain rows agree about days to expiry",
            row_dte is None or f"{row_dte}DTE" in texts[0],
            f"row says {row_dte} DTE, pill says {texts[0]!r}")

    page.close()


def check_workflow_sections(c: Checks) -> None:
    """Coverage still to come, named so the gate's gaps are legible."""
    for label in (
        "4. Which contract matches my intent? (M4-C6)",
        "5. What will this trade cost? (M4-C4/C7)",
        "6. What is my actual risk? (M4-C7)",
        "7. Should I place this order? (M4-C8/C9)",
    ):
        c.note(label)


def run_checks(browser, base: str, c: Checks, console: list) -> None:
    check_workspace(browser, base, c, console)
    check_seam_matches_home(browser, base, c, console)
    check_expiry_labels(browser, base, c, console)
    check_workflow_sections(c)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8809)
    ap.add_argument("--require", action="store_true")
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-trade-"))
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
        post(base, "/api/guide/state", {"onboarded": True})
        post(base, "/api/workspace", {"shell_v2": True, "symbol": "SPY"})

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge",
                                        headless=not args.headed)
            run_checks(browser, base, c, console)
            browser.close()

        c.check("zero browser console errors", not console, "; ".join(console[:3]))

        if c.failures:
            print(f"\nFAIL: {len(c.failures)} of {c.total} Trade checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"\nOK: {c.passed}/{c.total} Trade checks passed in a real "
              f"headless browser.")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:  # noqa: BLE001
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
