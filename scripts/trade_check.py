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

    # A chain LONGER than the region that shows it — 37 strikes against a
    # body that fits about fourteen. That is deliberate: with a seven-row
    # fixture every row is in view, so "the chain anchors on the strike
    # nearest spot" passes without the anchoring existing. It also makes the
    # spot row genuinely off-screen at the top of the range, which is the
    # condition §6.4's complaint describes ("a chain that opens at the top of
    # the strike range makes every user scroll to the middle before they can
    # think").
    #
    # The strikes bracket 471.20 UNEVENLY: 470 is 1.20 away and 475 is 3.80,
    # so "nearest spot" has exactly one answer and cannot be satisfied by
    # landing on the middle row by luck.
    strikes = [380.0 + 5.0 * i for i in range(37)]      # 380 … 560
    rows = []
    for s in strikes:
        for r in ("call", "put"):
            rows.append({
                "strike": s, "right": r, "bid": 3.85, "ask": 3.95, "mid": 3.90,
                "delta": 0.5, "iv": 0.18, "gamma": 0.021, "theta": -0.134,
                "vega": 0.312, "volume": 1200, "open_interest": 4000,
                "liquidity": 80, "dte": 0,
                "entry": 3.99, "breakeven": (s + 3.99) if r == "call"
                                            else (s - 3.99),
                "chance_itm": 50.0,
                # One row in the chain carries COMPUTED greeks, so the
                # provenance mark (§3.3's D3) has something to mark.
                "greeks_derived": s == 460.0,
            })
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


def _quickpick_over(chain: dict, url: str) -> dict:
    """Resolve a quick pick against the FIXTURE chain, using the real rules.

    The transport is stubbed; the rules are not. `/api/v1/quickpick` on the
    live server resolves against the provider's actual chain — spot $773 on a
    real SPY — while the browser is showing this file's fixture, so the
    honest answer would name a strike the page does not contain. Importing
    `services/quickpick.py` here keeps every decision under test real and
    fakes only which chain it is handed, which is the same bargain
    `guide_check.py` strikes with `/api/chain`.

    (The mismatch itself turned out to be worth having found: the client used
    to select a strike it could not find and return silently, so the chip
    appeared to do nothing. It now says so.)
    """
    sys.path.insert(0, str(ROOT))
    from optionspilot.services import quickpick as qp

    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    key = (q.get("intent") or [""])[0]
    right = (q.get("right") or ["call"])[0]
    spec = qp.BY_KEY.get(key)
    if spec is None:
        return qp.QuickPickView(intent=key, right=right,
                                reason="that is not one of the quick picks"
                                ).to_dict()
    dates = chain.get("expirations") or []
    choice = qp.expiration_for(spec, dates, date.today(),
                               (q.get("expiration") or [""])[0])
    if not choice.ok:
        return qp.QuickPickView(intent=key, right=spec.right or right,
                                reason=choice.reason).to_dict()
    return qp.contract_for(
        spec, chain.get("chain"), chain.get("spot"), current_right=right,
        expiration=choice.expiration, dte=choice.dte,
        symbol=chain.get("symbol", "")).to_dict()


#: The browser logs a console error for any 4xx response, and the commit
#: checks REFUSE every `/api/orders` POST on purpose — a gate that runs
#: against a real server with a real journal must not place paper orders, and
#: refusing is also the only way to reach §6.2's Failed state. Those specific
#: messages are excluded, by exact shape, on the pages that cause them. This
#: is narrow on purpose: a blanket filter would hide the next real one.
_REFUSED_ORDER_NOISE = "Failed to load resource"


def open_trade(browser, base, *, width=1920, height=1080, console=None,
               chain=None, review=None, review_log=None,
               allow_refused_orders=False):
    """A page showing the Trade destination with a known chain.

    `review_log` collects the body of every `/api/v1/review` request, which is
    what lets the fetch-discipline assertions count requests rather than
    trust a comment claiming they are deduplicated.
    """
    page = browser.new_page(viewport={"width": width, "height": height})
    if console is not None:
        def _console(m):
            if m.type != "error":
                return
            if allow_refused_orders and _REFUSED_ORDER_NOISE in m.text:
                return
            console.append(m.text)
        page.on("console", _console)
        page.on("pageerror", lambda e: console.append(str(e)))
    if chain is not None:
        page.route("**/api/chain*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(chain)))
        # Resolution runs the real `quickpick` rules over this fixture — see
        # `_quickpick_over`. The CATALOGUE route is deliberately left alone:
        # the chips must come from the live `/api/v1/quickpicks`, because the
        # thing worth asserting there is that the client is not carrying a
        # second copy of `quickpick.INTENTS`.
        page.route("**/api/v1/quickpick?*", lambda route, request:
                   route.fulfill(
                       status=200, content_type="application/json",
                       body=json.dumps({
                           "data": _quickpick_over(chain, request.url),
                           "meta": {"api_version": "v1"}})))
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


# ── 3 (continued). What contracts are available? ─────────────────────────────

CHAIN_JS = """() => {
  const wrap = document.getElementById('tk-chain');
  const rows = Array.from(wrap.querySelectorAll('tr[data-strike]'));
  const heads = Array.from(wrap.querySelectorAll('th'));
  const w = wrap.getBoundingClientRect();
  const focused = document.activeElement;
  const tabbable = rows.filter(r => r.tabIndex === 0);
  const inView = el => {
    const b = el.getBoundingClientRect();
    return b.top >= w.top - 1 && b.bottom <= w.bottom + 1;
  };
  return {
    role: wrap.querySelector('table')?.getAttribute('role') || '',
    labelled: !!wrap.querySelector('table')?.getAttribute('aria-label'),
    columns: heads.map(h => h.textContent.trim()),
    notes: Object.fromEntries(
      heads.map(h => [h.textContent.trim(), h.getAttribute('title') || ''])),
    strikes: rows.map(r => +r.dataset.strike),
    // Exactly one tab stop is the whole roving-tabindex contract.
    tabStops: tabbable.length,
    tabStopStrike: tabbable.length ? +tabbable[0].dataset.strike : null,
    focusedStrike: rows.includes(focused) ? +focused.dataset.strike : null,
    selected: rows.filter(r => r.classList.contains('selrow'))
                  .map(r => +r.dataset.strike),
    ariaSelected: rows.filter(r => r.getAttribute('aria-selected') === 'true')
                      .map(r => +r.dataset.strike),
    anchorInView: (() => {
      const r = rows.find(x => +x.dataset.strike === 470);
      return r ? inView(r) : null;
    })(),
    derivedMarks: rows.filter(r => r.querySelector('.ch-derived'))
                      .map(r => +r.dataset.strike),
    // A control that has been vertically squashed is still "present", still
    // click-targetable and still reports its text — so this is measured as a
    // RATIO of rendered height to natural height, which is the only form of
    // the question a DOM query can answer honestly.
    pillFill: (() => {
      const b = document.querySelector('#tk-exps button');
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return r.height <= 0 ? 0 : +(r.height / b.scrollHeight).toFixed(2);
    })(),
  };
}"""


def check_chain_is_a_grid(browser, base, c: Checks, console: list) -> None:
    """The chain is reachable, and usable, without a mouse (M4-C5).

    Fails against the previous build on every assertion: rows carried a plain
    `onclick` and no `tabindex`, so the chain could not be entered from the
    keyboard at all. §6.4 names this as the point where P8 fails hardest,
    "at the exact point where speed matters most".
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[])
    ch = page.evaluate(CHAIN_JS)

    c.check("the chain exposes itself as a grid", ch["role"] == "grid",
            ch["role"] or "no role")
    c.check("and the grid says which chain it is", ch["labelled"])
    c.check("exactly one row is in the tab order", ch["tabStops"] == 1,
            f"{ch['tabStops']} tab stops")
    # §6.4: "on load, the chain scrolls to and marks the strike nearest spot."
    # Spot is 471.20 and the fixture's strikes straddle it unevenly, so 470 is
    # the only right answer.
    c.check("the tab stop starts on the strike nearest spot",
            ch["tabStopStrike"] == 470, str(ch["tabStopStrike"]))
    # `[preserved]`: the previous build also brought the spot area into view,
    # by calling `scrollIntoView({block:"center"})` on the ATM marker. The
    # OUTCOME was already right; what changed is that the scroll now happens
    # only when the row is out of view (motion catalogue M-14) and targets a
    # real strike rather than the gap between two. Pinned so the rewrite
    # cannot have quietly lost it.
    c.check("[preserved] that strike is on screen without the user scrolling",
            ch["anchorInView"] is True, str(ch["anchorInView"]))
    c.check("nothing is selected until the user selects it",
            ch["selected"] == [], str(ch["selected"]))
    # A defect this fixture found by being long enough to be realistic.
    # `#trade-chain` is a column flex container, so `flex-shrink` acts
    # vertically and defaults to 1: with 37 strikes below it the expiry strip
    # was compressed into a row of half-height slivers under the table's
    # sticky header. Every pill was still present, still readable by
    # `textContent` and still clickable, so the expiry-label checks above went
    # on passing over a control the user could not actually read.
    c.check("a long chain does not squash the expiry strip above it",
            ch["pillFill"] is not None and ch["pillFill"] >= 0.98,
            f"pills rendered at {ch['pillFill']} of their natural height")

    # Everything below needs a keyboard entry point. Without one the section
    # cannot run — and it must report that as a FAILURE rather than raise, or
    # the gate stops being a gate at exactly the moment it has found
    # something. (`page.focus` on a selector that never resolves throws after
    # a 30s timeout, which is how this was discovered.)
    if ch["tabStops"] != 1:
        c.check("the chain can be entered from the keyboard at all", False,
                "no row carries tabindex=0; skipping the keyboard path")
        page.close()
        return

    # §6.7's experienced path, walked exactly: focus the chain, `↓ ↓`, `⏎`.
    # The anchor is 470 and the strikes step by 5, so two downs land on 480.
    page.focus("#tk-chain tr[data-strike][tabindex='0']")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    ch = page.evaluate(CHAIN_JS)
    c.check("arrow keys move the keyboard's position down the chain",
            ch["focusedStrike"] == 480, str(ch["focusedStrike"]))
    c.check("and the tab stop travels with it, staying single",
            ch["tabStops"] == 1 and ch["tabStopStrike"] == 480,
            f"{ch['tabStops']} stops at {ch['tabStopStrike']}")
    c.check("moving the keyboard does not arm the ticket",
            ch["selected"] == [], str(ch["selected"]))

    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    ch = page.evaluate(CHAIN_JS)
    c.check("Enter commits the focused row to the ticket",
            ch["selected"] == [480], str(ch["selected"]))
    c.check("and the selection is announced, not only coloured",
            ch["ariaSelected"] == [480], str(ch["ariaSelected"]))
    # The assertion that caught a real collision: the document-level order
    # shortcuts also bind Enter, and `selectContract` sets the `tkSel` their
    # guard tests — so one press armed the ticket AND opened the review
    # modal, which focused its own button and left the chain's keyboard
    # position nowhere. The chain now owns the keys it handles.
    c.check("selecting does not destroy the keyboard's position",
            ch["focusedStrike"] == 480, str(ch["focusedStrike"]))
    c.check("and one Enter does not also open the review modal",
            not page.is_visible("#confirm-overlay.show"))
    sel = page.text_content("#tk-selected") or ""
    c.check("the ticket followed the keyboard", "$480" in sel, sel[:80])

    page.keyboard.press("Home")
    ch = page.evaluate(CHAIN_JS)
    c.check("Home jumps to the first strike", ch["focusedStrike"] == 380,
            str(ch["focusedStrike"]))
    c.check("and the armed contract stays armed while the keyboard moves",
            ch["selected"] == [480], str(ch["selected"]))

    # Tab leaves the grid for the ticket (§6.4's last keyboard clause).
    page.keyboard.press("Tab")
    landed = page.evaluate(
        "() => !!document.activeElement.closest('#trade-ticket')")
    c.check("Tab leaves the chain for the ticket", landed)
    page.close()


def check_chain_columns_by_level(browser, base, c: Checks,
                                 console: list) -> None:
    """§8.2's column sets, and the two figures they needed (M4-C5).

    The previous build's own comment said it: "§8.2's full progression names
    columns this chain does not carry yet — breakeven and 'chance ITM' at
    Level 1, volume at Level 2, the remaining Greeks at Level 3." A Guided
    user saw Strike/Bid/Ask/Mid and nothing that told them what the contract
    had to do to be worth anything.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[])

    def cols_at(level):
        page.evaluate(f"() => Ctx.setSurfaceLevel({level})")
        page.wait_for_timeout(350)
        return page.evaluate(CHAIN_JS)

    guided = cols_at(1)
    joined = " ".join(guided["columns"]).lower()
    for want in ("strike", "bid", "ask", "mid", "break-even", "chance itm"):
        c.check(f"Guided shows {want!r}", want in joined, joined)
    # §8.1-1: the level changes what is DRAWN, never what exists.
    for hide in ("delta", "iv", "gamma", "theta", "vega"):
        c.check(f"Guided does not show {hide!r}", hide not in joined, joined)
    c.check("Guided still lists every strike the chain holds",
            len(guided["strikes"]) == 37, str(len(guided["strikes"])))

    # P3, and §3.3: chance-ITM is delta read as a percentage, and the screen
    # must not let it be mistaken for a forecast.
    note = guided["notes"].get("Chance ITM", "")
    c.check("chance-ITM states that it is an approximation, not a forecast",
            "not a forecast" in note.lower(), note[:100])
    beven = guided["notes"].get("Break-even", "")
    c.check("break-even says it is priced at the fill, not the mid",
            "not the mid" in beven.lower(), beven[:100])

    focused = " ".join(cols_at(2)["columns"]).lower()
    c.check("Focused adds delta", "delta" in focused, focused)
    c.check("Focused adds volume", "vol" in focused, focused)
    c.check("Focused still does not show the full greek set",
            "gamma" not in focused and "vega" not in focused, focused)

    full = cols_at(3)
    fjoined = " ".join(full["columns"]).lower()
    for want in ("iv", "oi", "gamma", "theta", "vega", "liq"):
        c.check(f"Full shows {want!r}", want in fjoined, fjoined)

    # PRODUCT_STANDARDS.md §3.3's D3, closed. One fixture row carries computed
    # greeks; it is the only one marked.
    c.check("a row whose greeks were computed says so",
            full["derivedMarks"] == [460], str(full["derivedMarks"]))

    pro = " ".join(cols_at(4)["columns"]).lower()
    c.check("Pro is not shown LESS than Full", pro == fjoined, pro)
    page.evaluate("() => Ctx.setSurfaceLevel(3)")
    page.close()


# ── 4. Which contract matches my intent? ─────────────────────────────────────

QUICK_JS = """() => {
  const chips = Array.from(document.querySelectorAll('#tk-quick button'));
  const why = document.getElementById('tk-qp-why');
  const rows = Array.from(
    document.querySelectorAll('#tk-chain tr[data-strike]'));
  return {
    labels: chips.map(b => b.textContent.trim()),
    // A chip that says what it will do BEFORE it is pressed. §6.3's promise
    // that "the chain then teaches them what the chip chose" needs the chip
    // to have said what it was going to choose.
    described: chips.filter(b => (b.title || '').length > 20).length,
    pressed: chips.filter(b => b.getAttribute('aria-pressed') === 'true')
                  .map(b => b.dataset.intent),
    why: (why?.textContent || '').trim(),
    whyShown: !!why && why.classList.contains('show'),
    whyWarns: !!why && why.classList.contains('warn'),
    picked: rows.filter(r => r.classList.contains('qprow'))
                .map(r => +r.dataset.strike),
    selected: rows.filter(r => r.classList.contains('selrow'))
                  .map(r => +r.dataset.strike),
  };
}"""


def check_quick_picks(browser, base, c: Checks, console: list) -> None:
    """§6.3's four chips, and the rule that they are never magic (M4-C6).

    Fails against the previous build throughout: it offered TWO ad-hoc buttons
    ("Nearest ATM call", "Nearest ATM put") whose rule was ten lines of
    JavaScript in `atmPick`, which existed only in the empty state, which
    explained nothing about what they had chosen, and which had no
    relationship to `quickpick.INTENTS` — the catalogue §6.3 says Pilot and
    the AI engine express the same intents through.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[])
    q = page.evaluate(QUICK_JS)

    # The catalogue is the SERVER's. Four chips, in §6.3's order, and this
    # request is not stubbed — it is the real `/api/v1/quickpicks`.
    c.check("all four of §6.3's intents are offered", len(q["labels"]) == 4,
            str(q["labels"]))
    c.check("and in the order §6.3 lists them",
            [x.lower() for x in q["labels"]] ==
            ["atm call", "atm put", "30 day", "weekly"], str(q["labels"]))
    c.check("every chip says what it will do before it is pressed",
            q["described"] == 4, f"{q['described']} of 4 carry a description")
    c.check("no chip claims to have produced the current contract",
            q["pressed"] == [], str(q["pressed"]))

    # Everything below presses a chip. With none rendered, `page.click` waits
    # 30s and then raises, which turns a gate that found something into a gate
    # that crashed. Same guard as the chain's keyboard section, same reason.
    if len(q["labels"]) != 4:
        c.check("the four chips can be pressed at all", False,
                "no chips rendered; skipping the resolution path")
        page.close()
        return

    # Press ATM call. Spot is 471.20, so the answer is 470 and nothing else.
    page.click('#tk-quick button[data-intent="atm_call"]')
    page.wait_for_timeout(900)
    q = page.evaluate(QUICK_JS)
    c.check("a chip arms the ticket with a concrete contract",
            q["selected"] == [470], str(q["selected"]))
    # §6.3: "the resulting selection is highlighted in the chain so the user
    # can see what was picked and why."
    c.check("and the chain marks the row the shortcut chose",
            q["picked"] == [470], str(q["picked"]))
    c.check("the chip itself shows it is the one in effect",
            q["pressed"] == ["atm_call"], str(q["pressed"]))

    # NEVER MAGIC. The explanation names both axes an intent resolves.
    c.check("the pick explains which strike, and against what",
            q["whyShown"] and "$470" in q["why"] and "471.20" in q["why"],
            q["why"][:120])
    c.check("and what it did with the expiry",
            "expiry you already had" in q["why"].lower(), q["why"][:120])
    c.check("an explanation is not painted as a warning", not q["whyWarns"],
            q["why"][:60])

    sel = page.text_content("#tk-selected") or ""
    c.check("the ticket names the contract the chip chose",
            "$470" in sel and "Call" in sel, sel[:70])

    # A chip that names a RIGHT switches sides; ATM put must not stay on calls.
    page.click('#tk-quick button[data-intent="atm_put"]')
    page.wait_for_timeout(900)
    q = page.evaluate(QUICK_JS)
    sel = page.text_content("#tk-selected") or ""
    c.check("an intent that names a right switches the chain to it",
            "Put" in sel, sel[:70])
    c.check("and the previous chip stops claiming to be in effect",
            q["pressed"] == ["atm_put"], str(q["pressed"]))

    # Choosing by hand ends the shortcut — the mark says "a chip put this
    # here", and after a manual click that is no longer true.
    page.click("#tk-chain tr[data-strike='455']")
    page.wait_for_timeout(700)
    q = page.evaluate(QUICK_JS)
    c.check("choosing a row by hand clears the quick-pick mark",
            q["picked"] == [] and q["selected"] == [455],
            f"picked={q['picked']} selected={q['selected']}")
    c.check("and no chip still claims credit for it", q["pressed"] == [],
            str(q["pressed"]))
    page.close()


def check_quick_pick_that_cannot_resolve(browser, base, c: Checks,
                                         console: list) -> None:
    """A shortcut that finds nothing says so (M4-C6).

    `QuickPickView` carries a `reason` precisely because a chip that quietly
    does nothing is one the user presses again. The previous build's `atmPick`
    returned early on an empty side with no output at all.
    """
    thin = _chain_payload(date.today())
    # Calls only. "ATM put" now has a real, explainable reason to fail.
    thin["chain"] = [r for r in thin["chain"] if r["right"] == "call"]
    page = open_trade(browser, base, console=console, chain=thin,
                      review_log=[])
    if not page.query_selector('#tk-quick button[data-intent="atm_put"]'):
        c.check("a shortcut that cannot resolve says why, rather than nothing",
                False, "no quick-pick chips rendered")
        page.close()
        return
    page.click('#tk-quick button[data-intent="atm_put"]')
    page.wait_for_timeout(900)
    q = page.evaluate(QUICK_JS)
    c.check("a shortcut that cannot resolve says why, rather than nothing",
            q["whyShown"] and "puts" in q["why"].lower(), q["why"][:120])
    c.check("and that one IS marked as a problem", q["whyWarns"],
            q["why"][:60])
    c.check("a failed shortcut arms nothing", q["selected"] == [],
            str(q["selected"]))
    page.close()


# ── 6. What is my actual risk? ───────────────────────────────────────────────

REVIEW_JS = """() => {
  // Null-safe throughout. Reverting this commit removes `#review-overlay`
  // entirely, and an evaluate that throws turns a gate which has found
  // something into a gate that crashed — the third time that pattern has
  // cost a run in this file.
  const ov = document.getElementById('review-overlay');
  if (!ov) return {open: false, order: [], sentence: '', costLabel: '',
                   cost: '', maxloss: '', breakeven: '', size: '',
                   nothing: '', guided: '', fill: '', absent: [],
                   missing: true};
  const open = ov.classList.contains('show');
  const txt = id => (document.getElementById(id)?.textContent || '').trim();
  // The five elements read in DOCUMENT ORDER, so the assertion is about the
  // order a person reads them in and not about the ids existing.
  const modal = document.querySelector('#review-overlay .modal');
  const order = modal ? Array.from(
    modal.querySelectorAll('#rv-sentence, #rv-cost, #rv-maxloss,' +
                           ' #rv-breakeven, #rv-size, #rv-nothing'))
    .map(e => e.id) : [];
  return {
    open, order,
    sentence: txt('rv-sentence'),
    costLabel: txt('rv-cost-label'), cost: txt('rv-cost'),
    maxloss: txt('rv-maxloss'), breakeven: txt('rv-breakeven'),
    size: txt('rv-size'), nothing: txt('rv-nothing'),
    guided: txt('rv-guided'), fill: txt('rv-fill'), missing: false,
    // Every element must be RENDERED even when its number does not apply,
    // carrying the reason in place of the figure.
    absent: Array.from(
      document.querySelectorAll('#review-overlay .rv-absent')).map(e => e.id),
  };
}"""


def _open_review(page):
    page.click("#tk-chain tr[data-strike='470']")
    page.wait_for_timeout(500)
    page.click("#tk-submit")
    page.wait_for_timeout(500)


def check_review_states_the_consequences(browser, base, c: Checks,
                                         console: list) -> None:
    """§6.5's five elements, in order (M4-C7).

    What this replaces, measured: the generic `confirmModal`, a key/value
    table of the order's MECHANICS — Contract, Action, Contracts, Time in
    force — closing with one "Estimated cost ≈ $390.00" derived from the mid.
    It never stated the maximum loss, the breakeven, the position size or the
    passive outcome, which are §6.5's elements 2 through 5. Every assertion
    below fails against it.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[])
    _open_review(page)
    r = page.evaluate(REVIEW_JS)

    c.check("submitting opens the review", r["open"])
    if not r["open"]:
        page.close()
        return

    # THE ORDER IS NORMATIVE. §6.5 lists five elements "in this order,
    # always" — the sentence before the numbers because a person reads what
    # they are doing before what it costs, and the passive outcome last
    # because it is what they are still thinking about when it closes.
    c.check("the five elements appear in §6.5's order",
            r["order"] == ["rv-sentence", "rv-cost", "rv-maxloss",
                           "rv-breakeven", "rv-size", "rv-nothing"],
            str(r["order"]))

    # 1. A sentence, with no abbreviation on it.
    s = r["sentence"]
    c.check("element 1 is a sentence naming side, size, contract and expiry",
            "BUYING" in s and "SPY" in s and "call" in s and s.endswith("."),
            s[:110])
    c.check("and it uses no abbreviation on that line",
            "DTE" not in s and " C " not in s and "0DTE" not in s, s[:110])

    # 2. Cost, and maximum loss stated even when it equals cost.
    c.check("element 2 states the cost", "412.34" in r["cost"], r["cost"])
    c.check("and the maximum loss, even though it equals the cost",
            "412.34" in r["maxloss"], r["maxloss"])
    c.check("saying WHY it equals the cost", "worthless" in r["maxloss"].lower(),
            r["maxloss"][:90])

    # 3. Breakeven with spot beside it, so the distance needs no arithmetic.
    c.check("element 3 states the breakeven", "474.12" in r["breakeven"],
            r["breakeven"])
    c.check("with spot beside it rather than in another panel",
            "471.20" in r["breakeven"], r["breakeven"])

    # 4. Position size as a percentage of the account.
    c.check("element 4 states the position size as a share of the account",
            "4.1%" in r["size"] and "account" in r["size"].lower(), r["size"])

    # 5. The passive outcome — the one no retail interface states at commit.
    c.check("element 5 states what happens if you do nothing",
            "do nothing" in r["nothing"].lower()
            and "worthless" in r["nothing"].lower(), r["nothing"][:110])

    # The honesty line beneath the five.
    c.check("and it says how the fill will actually happen",
            "delayed" in r["fill"].lower() and "cycle" in r["fill"].lower(),
            r["fill"][:110])

    c.check("nothing is left as an unexplained blank", r["absent"] == [],
            str(r["absent"]))

    # Cancel means cancel: no order, and focus returns rather than stranding
    # a keyboard user at the top of the page.
    page.click("#rv-cancel")
    page.wait_for_timeout(300)
    r2 = page.evaluate(REVIEW_JS)
    c.check("cancelling closes it", not r2["open"])
    landed = page.evaluate(
        "() => document.activeElement && document.activeElement.id")
    c.check("and gives focus back rather than dropping it on the body",
            landed not in ("", None, "body"), str(landed))
    page.close()


def check_review_renders_every_order_type(browser, base, c: Checks,
                                          console: list) -> None:
    """The shape does not change with the order type (M4-C7).

    A `sell_to_close` has proceeds rather than a cost, no new maximum loss and
    no breakeven. Those elements must still be PRESENT, carrying the reason
    where the number would be — a modal whose shape changes with the order
    type is one a user has to re-read every time, and a missing row is
    indistinguishable from a row that was forgotten.
    """
    closing = dict(REVIEW_STUB)
    closing.update(opening=False, cost=None, proceeds=384.15, max_loss=None,
                   max_loss_note="This order reduces an existing position, so "
                                 "it adds no new risk.",
                   breakeven=None,
                   breakeven_note="Breakeven applies to opening a position, "
                                  "not to closing one.",
                   position_pct=None,
                   position_note="This order closes exposure rather than "
                                 "adding it.",
                   buying_power_pct=None, guided_note="",
                   sentence="You are SELLING 1 SPY $470 call contract "
                            "expiring 12 Sep (7 days from now).")
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review=closing,
                      review_log=[])
    _open_review(page)
    r = page.evaluate(REVIEW_JS)
    if not r["open"]:
        c.check("a closing order can be reviewed", False, "review did not open")
        page.close()
        return

    c.check("a closing order still renders all five elements",
            r["order"] == ["rv-sentence", "rv-cost", "rv-maxloss",
                           "rv-breakeven", "rv-size", "rv-nothing"],
            str(r["order"]))
    c.check("its money is labelled proceeds, not cost",
            "proceeds" in r["costLabel"].lower(), r["costLabel"])
    # The three that do not apply say WHY, in place of the number.
    c.check("maximum loss says it adds no new risk, rather than $0.00",
            "no new risk" in r["maxloss"].lower() and "$0" not in r["maxloss"],
            r["maxloss"][:90])
    c.check("breakeven says it applies to opening, rather than showing 0",
            "opening a position" in r["breakeven"].lower(), r["breakeven"][:90])
    c.check("position size says it closes exposure rather than showing 0%",
            "closes exposure" in r["size"].lower() and "0%" not in r["size"],
            r["size"][:90])
    c.check("and all three are marked as absences, not as figures",
            sorted(r["absent"]) == ["rv-breakeven", "rv-maxloss", "rv-size"],
            str(r["absent"]))
    page.close()


def check_review_explains_at_guided_only(browser, base, c: Checks,
                                         console: list) -> None:
    """§6.5's Guided line, and its absence at Full (M4-C7)."""
    stub = dict(REVIEW_STUB)
    stub["guided_note"] = ("This contract expires TODAY. After the close it "
                           "is worth whatever it is in the money by, or "
                           "nothing at all.")
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review=stub,
                      review_log=[])

    page.evaluate("() => Ctx.setSurfaceLevel(1)")
    page.wait_for_timeout(300)
    _open_review(page)
    r = page.evaluate(REVIEW_JS)
    c.check("Guided adds one line explaining the order's most consequential term",
            "expires TODAY" in r["guided"], r["guided"][:90])
    if not r["open"]:
        c.check("Full does not, because it is explanation and not consequence",
                False, "the review modal did not open")
        page.close()
        return
    page.click("#rv-cancel")
    page.wait_for_timeout(300)

    page.evaluate("() => Ctx.setSurfaceLevel(3)")
    page.wait_for_timeout(300)
    page.click("#tk-submit")
    page.wait_for_timeout(500)
    r = page.evaluate(REVIEW_JS)
    # §6.5: "At Full, it does not." The five elements are unchanged — Surface
    # Level never hides consequence, only explanation (§8.1-2).
    c.check("Full does not, because it is explanation and not consequence",
            r["guided"] == "", r["guided"][:90])
    c.check("and the five elements are identical at both levels",
            "412.34" in r["maxloss"] and "do nothing" in r["nothing"].lower(),
            f"{r['maxloss'][:40]} | {r['nothing'][:40]}")
    page.evaluate("() => Ctx.setSurfaceLevel(3)")
    page.close()


# ── 7. Should I place this order? ────────────────────────────────────────────

HOLD_JS = """() => {
  const b = document.getElementById('rv-commit');
  const bar = document.getElementById('rv-hold-bar');
  const fill = document.getElementById('rv-hold-fill');
  // The absent shape must carry EVERY key the present one does. It did not
  // carry `reduced`, and reverting the commit turned a clean set of failures
  // into a KeyError three checks later — the fourth time in this file that a
  // gate crashed where it should have reported.
  if (!b || !bar) return {missing: true, role: '', value: null, width: null,
                          trackWidth: null, label: '', says: '', error: '',
                          open: false, disabled: true, orders: 0,
                          reduced: false};
  return {
    missing: false,
    // The accessible value, which §6.2 requires: "progress is exposed as a
    // progress bar with a value, so a screen-reader user knows how much of
    // the hold remains."
    role: bar.getAttribute('role') || '',
    value: +(bar.getAttribute('aria-valuenow') || -1),
    width: parseFloat(getComputedStyle(fill).width),
    trackWidth: parseFloat(getComputedStyle(bar).width),
    label: (document.getElementById('rv-hold-label')?.textContent || '').trim(),
    says: (document.getElementById('rv-hold-say')?.textContent || '').trim(),
    error: (document.getElementById('rv-hold-error')?.textContent || '').trim(),
    open: document.getElementById('review-overlay').classList.contains('show'),
    disabled: b.disabled,
    orders: window.__orders || 0,
    reduced: document.documentElement.classList.contains('gd-nomotion'),
  };
}"""


def _count_orders(page):
    """Count POSTs to /api/orders, and refuse every one of them.

    The gate must never place a paper order — it runs against a real server
    with a real journal. Refusing also exercises §6.2's Failed state, which
    is the half of the contract a happy path can never reach.
    """
    page.evaluate("() => { window.__orders = 0; }")
    page.route("**/api/orders", lambda route: (
        route.fulfill(status=400, content_type="application/json",
                      body=json.dumps({"error": "refused by the check"}))))
    page.on("request", lambda r: page.evaluate(
        "() => { window.__orders = (window.__orders || 0) + 1; }")
        if r.url.endswith("/api/orders") and r.method == "POST" else None)


def check_hold_to_confirm(browser, base, c: Checks, console: list) -> None:
    """The commit gesture (M4-C8).

    Fails against the previous build on every assertion: C7 shipped a plain
    `Place order` button that committed on one click, which is exactly what
    §6.6 forbids — "a click is indistinguishable from a mis-click".
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[],
                      allow_refused_orders=True)
    _count_orders(page)
    _open_review(page)
    h = page.evaluate(HOLD_JS)
    if h["missing"] or not h["open"]:
        c.check("the commit control exists and can be reached", False,
                "no hold control in an open review")
        page.close()
        return

    c.check("the commit exposes its progress as a progress bar with a value",
            h["role"] == "progressbar" and h["value"] == 0,
            f"role={h['role']} value={h['value']}")
    c.check("it is armed, not filled", h["width"] < 2, str(h["width"]))

    # §6.2's last rule: "never reachable by a single click, a double-click, or
    # an un-held Enter." THE assertion of this commit.
    page.click("#rv-commit")
    page.wait_for_timeout(250)
    h = page.evaluate(HOLD_JS)
    c.check("a single click does not place the order",
            h["open"] and h["orders"] == 0,
            f"open={h['open']} orders={h['orders']}")
    page.dblclick("#rv-commit")
    page.wait_for_timeout(250)
    h = page.evaluate(HOLD_JS)
    c.check("nor does a double-click", h["open"] and h["orders"] == 0,
            f"open={h['open']} orders={h['orders']}")
    page.focus("#rv-commit")
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    h = page.evaluate(HOLD_JS)
    c.check("nor does an un-held Enter", h["open"] and h["orders"] == 0,
            f"open={h['open']} orders={h['orders']}")

    # A hold, released early. §6.2: "no message, no dialog."
    box = page.query_selector("#rv-commit").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(260)
    mid = page.evaluate(HOLD_JS)
    c.check("holding fills the indicator", 20 < mid["value"] < 90,
            f"{mid['value']}%")
    c.check("and announces that the hold started",
            "holding" in mid["says"].lower(), mid["says"][:70])
    page.mouse.up()
    page.wait_for_timeout(350)
    h = page.evaluate(HOLD_JS)
    c.check("releasing early cancels it", h["open"] and h["orders"] == 0,
            f"open={h['open']} orders={h['orders']}")
    c.check("with no message and no dialog", h["error"] == "", h["error"][:70])
    c.check("and the indicator returns to empty", h["value"] == 0,
            str(h["value"]))

    # The label switches at 50% to the maximum loss (§6.2's Holding state).
    page.mouse.down()
    page.wait_for_timeout(150)
    early = page.evaluate(HOLD_JS)
    page.wait_for_timeout(260)
    late = page.evaluate(HOLD_JS)
    page.mouse.up()
    page.wait_for_timeout(300)
    c.check("before halfway the label states the action",
            "hold to place" in early["label"].lower(),
            f"{early['label']!r} at {early['value']}%")
    c.check("past halfway it states the maximum loss instead",
            "412.34" in late["label"] and "risk" in late["label"].lower(),
            f"{late['label']!r} at {late['value']}%")
    page.close()


def check_hold_completes_and_can_fail(browser, base, c: Checks,
                                      console: list) -> None:
    """Qualifying, submitting, and §6.2's Failed state (M4-C8).

    The Failed state is the half of the contract a happy path never reaches,
    and it is the reason the placement runs inside the modal at all: a modal
    that closed on qualify could only report a refusal as a toast, which
    leaves before the decision it belongs to.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[],
                      allow_refused_orders=True)
    _count_orders(page)          # every order is refused, deliberately
    _open_review(page)
    if not page.evaluate(HOLD_JS)["open"]:
        c.check("a full hold can be performed", False, "review did not open")
        page.close()
        return

    box = page.query_selector("#rv-commit").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(900)          # past the 600ms qualification
    page.mouse.up()
    page.wait_for_timeout(600)
    h = page.evaluate(HOLD_JS)

    c.check("a completed hold submits the order", h["orders"] == 1,
            f"{h['orders']} order request(s)")
    c.check("a refused order keeps the review open rather than closing on it",
            h["open"])
    # §6.2 Failed: "the control re-arms; an action-scoped error appears
    # beneath." Beneath the control — not a toast.
    c.check("and states the refusal beneath the control",
            "refused" in h["error"].lower(), h["error"][:80])
    c.check("the control re-arms rather than staying inert",
            not h["disabled"] and h["value"] == 0,
            f"disabled={h['disabled']} value={h['value']}")
    c.check("and the refusal is announced, not only shown",
            "not placed" in h["says"].lower(), h["says"][:80])
    page.close()


def check_hold_by_keyboard(browser, base, c: Checks, console: list) -> None:
    """§6.6: "Hold `Enter`. Same duration, same fill indicator, same early-
    release cancel. P8 is not satisfied by a mouse-only gesture."
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[],
                      allow_refused_orders=True)
    _count_orders(page)
    _open_review(page)
    if not page.evaluate(HOLD_JS)["open"]:
        c.check("the commit can be held from the keyboard", False,
                "review did not open")
        page.close()
        return

    # Opening a review puts focus on the commit, so the whole gesture is
    # reachable with no pointer at all.
    focused = page.evaluate("() => document.activeElement.id")
    c.check("opening a review focuses the commit control",
            focused == "rv-commit", focused)

    page.keyboard.down("Enter")
    page.wait_for_timeout(250)
    mid = page.evaluate(HOLD_JS)
    c.check("holding Enter fills the same indicator",
            20 < mid["value"] < 90, f"{mid['value']}%")
    page.keyboard.up("Enter")
    page.wait_for_timeout(350)
    h = page.evaluate(HOLD_JS)
    c.check("releasing Enter early cancels, exactly like the pointer",
            h["open"] and h["orders"] == 0 and h["value"] == 0,
            f"open={h['open']} orders={h['orders']} value={h['value']}")

    page.keyboard.down("Enter")
    page.wait_for_timeout(900)
    page.keyboard.up("Enter")
    page.wait_for_timeout(600)
    h = page.evaluate(HOLD_JS)
    c.check("and holding it to the end submits", h["orders"] == 1,
            f"{h['orders']} order request(s)")
    page.close()


def check_hold_under_reduced_motion(browser, base, c: Checks,
                                    console: list) -> None:
    """§7.5: the duration is UNCHANGED and the sweep becomes four steps.

    The unchanged duration is the part that is easy to get wrong. Shortening
    it under reduced motion would remove the deliberateness the gesture exists
    for — this is a timing affordance, not decoration.
    """
    # Set the PREFERENCE, not the class. `applyDisplay` re-derives
    # `gd-nomotion` from the stored setting on load and on every state
    # refresh, so a class poked in from the test is removed again a moment
    # later — measured, mid-gesture, which made a correct implementation look
    # like a continuous sweep. Driving the real in-app toggle is both the
    # honest test and the stable one.
    post(base, "/api/guide/state", {"reduce_motion": True})
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[],
                      allow_refused_orders=True)
    _count_orders(page)
    _open_review(page)
    if not page.evaluate(HOLD_JS)["reduced"]:
        c.check("the in-app reduced-motion toggle reaches the page", False,
                "gd-nomotion not applied from the stored preference")
        post(base, "/api/guide/state", {"reduce_motion": False})
        page.close()
        return
    if not page.evaluate(HOLD_JS)["open"]:
        c.check("the hold works under reduced motion", False,
                "review did not open")
        page.close()
        return

    # Sample WITHIN one hold that is deliberately released before qualifying —
    # eight samples at ~40ms is under 600ms even with evaluate overhead. The
    # first version of this loop ran past the qualification and placed the
    # order it was about to assert had not been placed.
    box = page.query_selector("#rv-commit").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    seen, still_reduced = set(), True
    for _ in range(8):
        page.wait_for_timeout(40)
        h = page.evaluate(HOLD_JS)
        seen.add(h["value"])
        still_reduced = still_reduced and h["reduced"]
    page.mouse.up()
    page.wait_for_timeout(300)

    c.check("reduced motion stays on for the length of the gesture",
            still_reduced, "gd-nomotion was removed mid-hold")
    steps = sorted(v for v in seen if v is not None and 0 <= v <= 100)
    c.check("under reduced motion the sweep is discrete, not continuous",
            len(set(steps)) <= 5, f"{len(set(steps))} distinct values: {steps}")
    c.check("and every step is one of the four the design names",
            all(v in (0, 25, 50, 75, 100) for v in steps), str(steps))

    # §7.5's harder half: "the duration is UNCHANGED." Shortening it under
    # reduced motion would remove the deliberateness the gesture exists for.
    before = page.evaluate(HOLD_JS)["orders"]
    page.mouse.down()
    page.wait_for_timeout(400)
    page.mouse.up()
    page.wait_for_timeout(300)
    h = page.evaluate(HOLD_JS)
    c.check("the duration is unchanged — 400ms still does not qualify",
            h["orders"] == before and h["open"],
            f"orders {before}->{h['orders']} open={h['open']}")
    # Restore, so a later check does not inherit this one's preference.
    post(base, "/api/guide/state", {"reduce_motion": False})
    page.close()


BLOCK_JS = """() => {
  const sub = document.getElementById('tk-submit');
  const marked = ['tk-limit','tk-stop','tk-trail','tk-trailpct']
    .filter(id => document.getElementById(id)?.classList.contains('tk-invalid'));
  return {
    disabled: !!sub && sub.disabled,
    reason: (document.getElementById('tk-blocked')?.textContent || '').trim(),
    marked,
    invalidAttr: marked.filter(
      id => document.getElementById(id).getAttribute('aria-invalid') === 'true'),
    why: (document.getElementById('tk-kind-why')?.textContent || '').trim(),
  };
}"""


def check_blocked_states(browser, base, c: Checks, console: list) -> None:
    """§6.2's Invalid state (M4-C9).

    "The specific reason, in place, with the offending field marked and the
    impossible option removed — plus a line saying what changed and why."

    The three refusals asserted here were, until this commit, reachable in two
    clicks and discovered only on submit — `OrderManager` raised
    "limit orders need limit_price > 0", which is accurate and useless. Every
    assertion fails against the previous build, where `#tk-submit` was enabled
    with all three fields empty.
    """
    page = open_trade(browser, base, console=console,
                      chain=_chain_payload(date.today()), review_log=[])
    page.click("#tk-chain tr[data-strike='470']")
    page.wait_for_timeout(500)

    b = page.evaluate(BLOCK_JS)
    c.check("[preserved] a complete market order is not blocked",
            not b["disabled"] and b["reason"] == "", b["reason"][:70])

    # 1. A limit order with no price.
    page.select_option("#tk-kind", "limit")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("a limit order with no price cannot be submitted", b["disabled"])
    c.check("and the price field is the one marked",
            b["marked"] == ["tk-limit"], str(b["marked"]))
    c.check("marked for a screen reader too, not only in colour",
            b["invalidAttr"] == ["tk-limit"], str(b["invalidAttr"]))
    # "Actionable guidance", not a restatement of the rule: the ask is on
    # screen and naming it is the difference between an error and an
    # instruction.
    c.check("the reason says what to enter and names a number to use",
            "limit order needs a price" in b["reason"]
            and "3.95" in b["reason"], b["reason"][:120])

    page.fill("#tk-limit", "3.90")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("filling it clears the block", not b["disabled"] and not b["marked"],
            f"disabled={b['disabled']} marked={b['marked']}")
    c.check("and clears the reason with it", b["reason"] == "", b["reason"][:70])

    # 2. A stop with no trigger level. Reached from the SELL side, because
    #    the buy side removes exit types entirely — the V0.6.1 guardrail,
    #    which this commit does not touch.
    # The position list is injected because opening a real one needs a live
    # chain the backend cannot fetch offline; everything the guardrail then
    # does is the real code path. Same technique `guide_check.py` uses.
    page.evaluate("""() => {
      lastStatus = lastStatus || {};
      lastStatus.positions = [{underlying: 'SPY',
        expiration: tkChain.expiration, strike: tkSel.strike,
        right: tkSel.right, quantity: 5, managed_by: 'manual', unrealized: 0}];
      tkSyncTicket();
    }""")
    page.wait_for_timeout(300)
    sell = page.query_selector('#tk-side-seg button[data-side="sell_to_close"]')
    if not sell or sell.is_disabled():
        c.check("a held contract can be armed to sell", False,
                "sell stayed disabled with a position injected")
        page.close()
        return
    page.click('#tk-side-seg button[data-side="sell_to_close"]')
    page.wait_for_timeout(300)
    page.select_option("#tk-kind", "stop_loss")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("a stop loss with no trigger level cannot be submitted",
            b["disabled"])
    c.check("and the trigger field is the one marked",
            b["marked"] == ["tk-stop"], str(b["marked"]))
    c.check("the reason says it triggers on the underlying, and names spot",
            "price of SPY" in b["reason"] and "471.20" in b["reason"],
            b["reason"][:130])

    # 3. A trailing stop needs exactly one of trail / trail percent.
    page.select_option("#tk-kind", "trailing_stop")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("a trailing stop with neither trail nor percent is blocked",
            b["disabled"] and b["marked"] == ["tk-trail"],
            f"disabled={b['disabled']} marked={b['marked']}")
    page.fill("#tk-trail", "2")
    page.fill("#tk-trailpct", "5")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("and one with BOTH is blocked too, naming the second",
            b["disabled"] and b["marked"] == ["tk-trailpct"],
            f"disabled={b['disabled']} marked={b['marked']}")
    c.check("saying to clear one rather than restating the rule",
            "not both" in b["reason"].lower()
            and "clear one" in b["reason"].lower(), b["reason"][:120])
    page.fill("#tk-trailpct", "")
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("clearing one unblocks it", not b["disabled"] and not b["marked"],
            f"disabled={b['disabled']} marked={b['marked']}")

    # The V0.6.1 side guardrails are untouched and must stay that way — they
    # are the other half of the same state, and this commit is where someone
    # would most plausibly rewrite them.
    page.click('#tk-side-seg button[data-side="buy_to_open"]')
    page.wait_for_timeout(400)
    b = page.evaluate(BLOCK_JS)
    c.check("[preserved] switching to buy still withdraws the exit type",
            page.input_value("#tk-kind") in ("market", "limit"),
            page.input_value("#tk-kind"))
    c.check("[preserved] and still says what changed and why",
            "exit order" in b["why"].lower(), b["why"][:110])
    page.close()


def check_workflow_sections(c: Checks) -> None:
    """Every section of this file now has assertions.

    Kept as a function rather than deleted: the next milestone to extend Trade
    adds its section here first, empty, so the gate's coverage stays legible
    from its own output.
    """
    return


def run_checks(browser, base: str, c: Checks, console: list) -> None:
    check_workspace(browser, base, c, console)
    check_seam_matches_home(browser, base, c, console)
    check_expiry_labels(browser, base, c, console)
    check_chain_is_a_grid(browser, base, c, console)
    check_chain_columns_by_level(browser, base, c, console)
    check_ticket_empty_state(browser, base, c, console)
    check_ticket_states_the_engines_numbers(browser, base, c, console)
    check_ticket_does_not_refetch(browser, base, c, console)
    check_quick_picks(browser, base, c, console)
    check_quick_pick_that_cannot_resolve(browser, base, c, console)
    check_review_states_the_consequences(browser, base, c, console)
    check_review_renders_every_order_type(browser, base, c, console)
    check_review_explains_at_guided_only(browser, base, c, console)
    check_hold_to_confirm(browser, base, c, console)
    check_hold_completes_and_can_fail(browser, base, c, console)
    check_hold_by_keyboard(browser, base, c, console)
    check_hold_under_reduced_motion(browser, base, c, console)
    check_blocked_states(browser, base, c, console)
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
