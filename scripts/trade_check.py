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
    Two kinds of assertion legitimately cannot, and both are LABELLED in
    their own name rather than left to be discovered by someone re-running
    the exercise: `[preserved]` marks behaviour that was already correct and
    is pinned so a later commit cannot remove it, and `[guard]` marks a
    property of a mechanism the previous build did not have at all. An
    unlabelled assertion is a claim that reverting the commit turns it red.

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


#: A review payload whose every number is deliberately UNREACHABLE from the
#: chain fixture by arithmetic. The fixture's mid is 3.90, so the build this
#: milestone replaces would render `$390.00` for a single contract however the
#: server answered — it multiplied `tkSel.mid` in JavaScript. Nothing here is
#: 390, 385 or 395, so an assertion that the ticket shows THESE numbers is an
#: assertion that the ticket reads the server rather than computing its own.
#:
#: The arithmetic itself is not tested here and must not be: it is pinned in
#: `tests/test_services_review.py` against `broker/paper.py`'s own source,
#: which is a stronger statement than any browser check could make.
REVIEW_STUB = {
    "sentence": "You are BUYING 1 SPY $470 call contract expiring 12 Sep "
                "(7 days from now).",
    "opening": True, "premium": 4.1234, "cost": 412.34, "cost_note": "",
    "proceeds": None, "max_loss": 412.34,
    "max_loss_note": "100% of what you pay — a long option can expire worthless.",
    "breakeven": 474.12, "breakeven_note": "", "spot": 471.20,
    "position_pct": 4.12, "position_note": "",
    "buying_power": 8000.0, "buying_power_pct": 5.15,
    "buying_power_after": 7587.66, "buying_power_note": "",
    "if_nothing": "If you do nothing and SPY closes below $470 on 12 Sep, "
                  "this contract expires worthless and you lose everything "
                  "you paid for it.",
    "fill_note": "Fills on the next scan cycle at the ask, worsened by 1% "
                 "slippage, against a 15-minute delayed quote.",
}


def open_trade(browser, base, *, width=1920, height=1080, console=None,
               chain=None, review=None, review_log=None):
    """A page showing the Trade destination with a known chain.

    `review_log` collects the body of every `/api/v1/review` request, which is
    what lets the fetch-discipline assertions count requests rather than
    trust a comment claiming they are deduplicated.
    """
    page = browser.new_page(viewport={"width": width, "height": height})
    if console is not None:
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
    if chain is not None:
        page.route("**/api/chain*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(chain)))
    if review is not None or review_log is not None:
        def _review(route, request):
            if review_log is not None:
                try:
                    review_log.append(json.loads(request.post_data or "{}"))
                except ValueError:
                    review_log.append({})
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"data": review or REVIEW_STUB,
                                           "meta": {"api_version": "v1"}}))
        page.route("**/api/v1/review", _review)
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


# ── 5-6. What will this trade cost, and what is my actual risk? ──────────────

TICKET_JS = """() => {
  const vis = id => {
    const e = document.getElementById(id);
    if (!e) return false;
    const b = e.getBoundingClientRect();
    return b.width > 0 && b.height > 0;
  };
  const txt = id => (document.getElementById(id)?.textContent || '').trim();
  const sub = document.getElementById('tk-submit');
  return {
    // The order's SHAPE — the controls §6.2's Empty state says must be
    // visible before a contract is chosen.
    kind: vis('tk-kind'), qty: vis('tk-qty'), tif: vis('tk-tif'),
    side: vis('tk-side-seg'), figures: vis('tk-figures'),
    selected: txt('tk-selected'),
    // The four figures, by label and value, read from the rendered <dl> so a
    // renamed id fails rather than silently passing.
    labels: Array.from(document.querySelectorAll('#tk-figures dt'))
                 .map(e => e.textContent.trim()),
    entry: txt('tk-fig-entry'), cost: txt('tk-fig-cost'),
    costLabel: txt('tk-fig-cost-label'),
    bp: txt('tk-fig-bp'), risk: txt('tk-fig-risk'),
    riskTitle: document.getElementById('tk-fig-risk')?.getAttribute('title') || '',
    submitDisabled: !!sub && sub.disabled,
    submitText: txt('tk-submit'),
    blocked: txt('tk-blocked'),
    sizing: txt('tk-risk'),
    sizingHot: !!document.getElementById('tk-risk')
                 ?.classList.contains('hot'),
  };
}"""


def check_ticket_empty_state(browser, base, c: Checks, console: list) -> None:
    """The ticket exists before a contract does (M4-C4).

    Fails against the previous build on every assertion for one reason: the
    form carried `style="display:none"` and was revealed by `selectContract`.
    §6.1's second named fault is exactly that — "the ticket does not exist
    until a contract is chosen … the shape of the decision should be visible
    before the decision" — so a user could not see what an order involved
    until after committing to one.
    """
    log: list = []
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=log)
    t = page.evaluate(TICKET_JS)

    for name, key in (("order type", "kind"), ("quantity", "qty"),
                      ("time in force", "tif"), ("side", "side")):
        c.check(f"the empty ticket already shows the {name} control",
                t[key], "not rendered")
    c.check("the empty ticket names its state plainly",
            "nothing selected yet" in t["selected"].lower(),
            t["selected"][:80])

    # §6.2 Disabled: "with the reason stated adjacent, always."
    c.check("the empty ticket's submit is disabled", t["submitDisabled"])
    c.check("and says why, adjacent to it, rather than only being grey",
            "select a contract" in t["blocked"].lower(), t["blocked"][:90])

    # The figures teach the vocabulary before they carry a number.
    c.check("the four figures are labelled before any of them has a value",
            t["figures"] and len(t["labels"]) == 4, str(t["labels"]))
    joined = " | ".join(t["labels"]).lower()
    for want in ("entry", "estimated cost", "buying power", "lose"):
        c.check(f"the figures name {want!r}", want in joined, joined)
    c.check("an unknown figure is a dash, never a zero",
            t["entry"] == "—" and t["cost"] == "—" and t["risk"] == "—",
            f"entry={t['entry']!r} cost={t['cost']!r} risk={t['risk']!r}")

    # A FORWARD guard rather than a delta: the previous build made no request
    # either, because the mechanism did not exist. It is here because the
    # obvious way to write the always-present ticket is to price it on entry,
    # and pricing nothing is a request per visit to Trade for no answer.
    c.check("[guard] an empty ticket asks the server for nothing",
            not log, f"{len(log)} review request(s)")
    page.close()


def check_ticket_states_the_engines_numbers(browser, base, c: Checks,
                                            console: list) -> None:
    """The ticket's cost is the ENGINE's, not the mid (M4-C4).

    The defect this replaces, measured: `updateEstimate` computed
    `tkSel.mid * qty * 100`. The mid is not a price anything fills at —
    `PaperBroker` crosses to the ask and applies slippage — so the ticket
    stated a cost the system was never going to charge, on the screen where a
    user decides whether they can afford it. PRODUCT_STANDARDS.md §3.2 forbids
    it twice: never the mid, and the fill model has exactly one owner.

    The fixture's mid is 3.90, so the old build renders `$390.00` for one
    contract regardless of what the server says. Every number asserted here is
    unreachable from 3.85/3.90/3.95 by arithmetic, so passing means the ticket
    READ them rather than computed them.
    """
    log: list = []
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=log)
    page.click("#tk-chain tr[data-strike]")
    page.wait_for_timeout(600)
    t = page.evaluate(TICKET_JS)

    c.check("the entry price is the expected FILL, not the mid",
            "4.12" in t["entry"] and "3.90" not in t["entry"], t["entry"])
    c.check("the estimated cost is the server's (the old build showed $390.00)",
            "412.34" in t["cost"] and "390" not in t["cost"], t["cost"])
    c.check("the maximum loss is stated, even though it equals the cost",
            "412.34" in t["risk"], t["risk"])
    c.check("and says WHY it equals the cost, rather than only the number",
            "expire worthless" in t["riskTitle"].lower(), t["riskTitle"][:90])
    # 5.15 renders as 5.2: the server carries two decimals and a percentage on
    # a ticket reads at one. Asserted at the rendered precision deliberately —
    # this is a check on what the user sees.
    c.check("the buying-power impact is shown as a share and a remainder",
            "5.2%" in t["bp"] and "7,587.66" in t["bp"], t["bp"])
    # The SECOND defect this commit closes, and the quieter of the two.
    # `RiskManager` sets the per-trade budget as a share of EQUITY
    # (`risk/manager.py`: `risk_budget = self._equity * risk_per_trade_pct /
    # 100`). The old ticket line computed `cost / buying_power * 100` — cash,
    # not account value — and compared it against that budget, so on any
    # account holding an open position the advisory tripped at the wrong
    # point in the wrong direction. It now reads `position_pct`, which is
    # cost over equity, so the two sides of the comparison share a
    # denominator.
    c.check("the sizing advisory is measured against account value",
            "account value" in t["sizing"].lower()
            and "buying power" not in t["sizing"].lower(), t["sizing"])
    c.check("and marks itself when the order exceeds the risk budget",
            t["sizingHot"], t["sizing"])

    # The request describes the order the button would send.
    c.check("the ticket asked about the order it is displaying",
            bool(log) and log[-1].get("quantity") == 1
            and log[-1].get("side") == "buy_to_open"
            and log[-1].get("right") == "call",
            json.dumps(log[-1]) if log else "no request")

    # PRESERVED, not new — both of these were already true and are asserted so
    # that C9's blocked state cannot quietly take them away. Neither
    # distinguishes this build from the previous one, and saying so is the
    # point: an assertion whose provenance is unstated gets read as coverage
    # it does not provide.
    c.check("[preserved] the submit names the order it will place",
            "1 ×" in t["submitText"] and "SPY" in t["submitText"],
            t["submitText"])
    c.check("[preserved] a selected ticket is not blocked",
            not t["submitDisabled"])
    page.close()


def check_ticket_does_not_refetch(browser, base, c: Checks,
                                  console: list) -> None:
    """A ticket recalculates on every keystroke; it must not re-ask (M4-C4).

    Not a micro-optimisation. The figures are fetched per draft change, and
    without a fingerprint a quantity stepper held down would issue a request
    per repeat — against an endpoint that takes the trading lock and walks a
    provider chain. The assertions are counts, because a comment claiming
    deduplication is not deduplication.
    """
    log: list = []
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=log)
    page.click("#tk-chain tr[data-strike]")
    page.wait_for_timeout(600)
    first = len(log)
    c.check("selecting a contract prices it exactly once",
            first == 1, f"{first} request(s)")

    # Re-selecting the SAME contract changes no part of the draft.
    page.click("#tk-chain tr[data-strike]")
    page.wait_for_timeout(600)
    # `[guard]` because a build that never asks also never asks twice: this
    # only distinguishes anything once the mechanism above it exists.
    c.check("[guard] re-selecting the same contract asks nothing new",
            len(log) == first, f"{len(log) - first} extra request(s)")

    # Three quantity steps in quick succession are ONE question.
    for _ in range(3):
        page.click("#tk-qty-up")
    page.wait_for_timeout(700)
    added = len(log) - first
    c.check("three quick quantity steps coalesce into one request",
            added == 1, f"{added} request(s) for 3 steps")
    c.check("and the one it sent describes the FINAL quantity",
            bool(log) and log[-1].get("quantity") == 4,
            str(log[-1].get("quantity")) if log else "no request")
    page.close()


def check_workflow_sections(c: Checks) -> None:
    """Coverage still to come, named so the gate's gaps are legible."""
    for label in (
        "4. Which contract matches my intent? (M4-C6)",
        "7. Should I place this order? (M4-C8/C9)",
    ):
        c.note(label)


def run_checks(browser, base: str, c: Checks, console: list) -> None:
    check_workspace(browser, base, c, console)
    check_seam_matches_home(browser, base, c, console)
    check_expiry_labels(browser, base, c, console)
    check_ticket_empty_state(browser, base, c, console)
    check_ticket_states_the_engines_numbers(browser, base, c, console)
    check_ticket_does_not_refetch(browser, base, c, console)
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
