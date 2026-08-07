"""Headless-browser certification of the Home destination (UI V2 M3).

Sibling to `shell_check.py`, and written for the harder half of the problem:
the shell is structure and can be asserted from the DOM, whereas Home's claims
are about what a human SEES in the first second. So the assertions here are
deliberately geometric and textual rather than "a class was applied".

What it asserts, and why each one rather than something easier:

  * **Bands 1 and 2 fit above the fold at 1920x1080 AND 1440x900.** This is a
    hard acceptance criterion (`UI_V2_DESIGN.md` §5.6), not an aspiration, and
    it is measured from `getBoundingClientRect` — the current Dashboard's
    first named fault is that it requires scrolling to reach open positions.
  * **Band 2 never reverses.** Positions stay left of, or above, findings at
    every width, because consequence ordering does not change with viewport.
  * **Every empty region contains a VERB.** P9: an empty state is the first
    step, not the word "None". Asserted against a genuinely empty account.
  * **A short history states its threshold instead of a number.** The win-rate
    card must read "N of 30", never "0%" — a zero win rate on zero trades is a
    false statement about a person, and it is the single easiest thing here to
    regress into by "tidying up" a formatter.
  * **The four states keep one shape.** Loading, empty and error are compared
    against populated: same instruments, same order. §2.9's fourth point is
    that nothing appears or disappears when data lands, and a state that
    reflows is the layout jump this milestone exists to remove.
  * **A failing region does not take the screen with it.** Home never fails as
    a whole (§2.10); the assertion is that its neighbours still render.
  * **The status line is not a live region and not a button.** §2.6 and §2.13
    are explicit, and both are the kind of thing an eager accessibility pass
    "improves" into being wrong.
  * **Open risk marks a floor when a mark is missing.** Stale is always marked
    and never hidden; a number that looks live and is not is a defect.

The states are driven by intercepting `/api/v1/home`, so this suite needs no
seeded journal and no scan. One note learned the hard way and worth keeping:
to reproduce a SLOW backend the intercepted request must be left **hanging** —
a sleep inside a sync-API route handler blocks Playwright's own dispatcher, so
the assertion lands after the response and the loading state is never seen.

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shell_nav import goto, home_ready  # noqa: E402

#: The instruments Home is made of, in the DOM order the eye path requires
#: (§2.3): state, then what needs you, then context.
EYE_PATH = ["home-status", "home-metrics", "home-positions", "home-next",
            "home-equity", "home-watchlist"]

#: A completely fresh account. Every empty region's copy is asserted against
#: this rather than against a mocked-per-region payload, because the empty
#: state's whole claim is that it is coherent as ONE screen.
EMPTY = {
    "status": {"text": "Welcome. Your paper account has $10,000. "
                       "None of it is real.",
               "case": "first_run", "needs_you": False},
    "account": {"equity": 10000.0, "cash": 10000.0, "realized_pnl": 0.0,
                "starting_balance": 10000.0},
    "open_risk": {"dollars": 0.0, "pct_of_account": 0.0, "positions": 0,
                  "marked": 0},
    "today_pnl": 0.0, "buying_power": 10000.0,
    "win_rate": {"rate": None, "trades": 0, "needed": 30, "sufficient": False},
    "positions": [], "working_orders": [], "next_actions": [],
    "equity": [], "watchlist": [], "errors": [],
}


#: The gap between consecutive bands, measured from the rendered boxes and
#: compared against the token's own computed value rather than a literal 32.
#: A hard-coded number here would be a second place holding one fact, and the
#: one it would disagree with is the design system.
RHYTHM_JS = """() => {
  const home = document.getElementById('home');
  const expected = parseFloat(getComputedStyle(home)
    .getPropertyValue('--space-6')) || 0;
  const kids = Array.from(home.children).filter(
    e => e.getBoundingClientRect().height > 0);
  const gaps = [];
  for (let i = 1; i < kids.length; i++) {
    gaps.push(+(kids[i].getBoundingClientRect().top -
                kids[i - 1].getBoundingClientRect().bottom).toFixed(1));
  }
  return {expected: expected, gaps: gaps, count: kids.length,
          ok: expected > 0 && gaps.length >= 3 &&
              gaps.every(g => Math.abs(g - expected) <= 1)};
}"""


#: The left edge of each split band's MINOR column. One seam means these are
#: the same number. Reported as `null` when a band has stacked, which is a
#: legitimate state and not a seam — the check then fails loudly rather than
#: comparing two absent values and passing.
SEAM_JS = """() => {
  const edge = id => {
    const e = document.getElementById(id);
    if (!e) return null;
    const b = e.getBoundingClientRect();
    return b.width > 0 ? +b.left.toFixed(1) : null;
  };
  const b2 = edge('home-next'), b3 = edge('home-watchlist');
  return {b2: b2, b3: b3,
          ok: b2 !== null && b3 !== null && Math.abs(b2 - b3) <= 1};
}"""


def _position(symbol="SPY", strike=470.0, right="call", qty=1, avg=3.0,
              mark=3.5, pnl=50.0):
    return {"contract": f"{symbol}260918C00470000", "underlying": symbol,
            "expiration": "2026-09-18", "strike": strike, "right": right,
            "managed_by": "manual", "direction": "long", "quantity": qty,
            "avg_price": avg, "mark": mark, "unrealized": pnl,
            "entry_spot": 0.0, "stop": None, "target": None,
            "opened_at": "2026-08-05T12:00:00+00:00"}


#: A populated account, used for the geometry and ordering checks.
FULL = dict(
    EMPTY,
    status={"text": "Markets are open. You are up $212 today across 2 positions.",
            "case": "holding", "needs_you": False},
    open_risk={"dollars": 840.0, "pct_of_account": 8.1, "positions": 2,
               "marked": 2},
    today_pnl=212.40, buying_power=8900.0,
    win_rate={"rate": 0.58, "trades": 41, "needed": 30, "sufficient": True},
    positions=[_position(), _position("AAPL", 190.0, "put", 1, 1.2, 0.82, -38.0)],
    working_orders=[{"id": "1", "contract": "NVDA 900C", "kind": "stop",
                     "quantity": 1, "limit": None, "stop": 878.0}],
    next_actions=[
        {"kind": "risk", "text": "Your open risk is 8.1% of the account.",
         "detail": "", "action": None},
        {"kind": "finding",
         "text": "Your 0-2 DTE trades win 31% over 26 trades.",
         "detail": "p=0.004 over 26 trades",
         "action": {"label": "Show me", "tab": "coach"}},
    ],
    equity=[["2026-07-20T12:00:00+00:00", 10000.0],
            ["2026-08-01T12:00:00+00:00", 10200.0],
            ["2026-08-05T12:00:00+00:00", 10412.0]],
    watchlist=[{"symbol": "QQQ", "direction": "long", "confidence": 0.71,
                "required": 0.65, "accepted": True},
               {"symbol": "MSFT", "direction": "long", "confidence": 0.44,
                "required": 0.65, "accepted": False}],
)

#: Open risk that is a FLOOR: positions exist, none of them is priced.
UNPRICED = dict(FULL, open_risk={"dollars": 840.0, "pct_of_account": 8.1,
                                 "positions": 2, "marked": 0})

#: Three regions down at once. `next_actions` is null, not [] — "I could not
#: look" and "no findings" are different answers.
BROKEN = dict(EMPTY, errors=["positions", "watchlist", "next_actions"],
              next_actions=None)


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  ok   {label}")
            return True
        self.failures.append(label + (f" - {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))
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
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def post(base: str, path: str, body: dict) -> None:
    req = urllib.request.Request(
        base + path, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def open_home(browser, base, payload, *, width=1920, height=1080, hang=False,
              console=None):
    """A page showing Home with `payload` served for `/api/v1/home`."""
    page = browser.new_page(viewport={"width": width, "height": height})
    if console is not None:
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
    if hang:
        # Returning without fulfilling leaves the request pending, which is the
        # only thing that reproduces a slow backend — see the module docstring.
        page.route("**/api/v1/home", lambda route: None)
    else:
        page.route("**/api/v1/home", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"data": payload, "meta": {}})))
    page.goto(base, wait_until="domcontentloaded")
    home_ready(page, 25000)
    if not hang:
        goto(page, "dashboard")
    page.wait_for_timeout(1200 if not hang else 900)
    return page


def instrument_order(page) -> list:
    """Home's instruments in **document order**.

    `querySelectorAll` returns document order, which is the point: the first
    version of this filtered the input list by presence and mapped it straight
    back, so it returned `EYE_PATH` whenever every id existed — a check that
    said "the DOM order matches the eye path" while testing only that the
    elements were on the page. Reordering the bands would have passed it.
    """
    return page.evaluate(
        "(ids) => Array.from("
        "  document.querySelectorAll(ids.map(i => '#' + i).join(',')))"
        "  .map(e => e.id)", EYE_PATH)


def bottom_of(page, selector: str) -> float:
    return page.eval_on_selector(
        selector, "e => e.getBoundingClientRect().bottom")


def run_checks(browser, base: str, c: Checks, console: list) -> None:
    # ── 1-4: the no-scroll commitment, at both required sizes ────────────────
    for width, height in ((1920, 1080), (1440, 900)):
        page = open_home(browser, base, FULL, width=width, height=height,
                         console=console)
        b2 = bottom_of(page, ".home-band2")
        c.check(f"bands 1-2 fit above the fold at {width}x{height}",
                b2 <= height, f"band 2 ends at {b2:.0f}px of {height}px")
        # Not just "it fits" — the reason it must is that positions are the
        # highest-consequence objects on the screen (§5.1 fault 1).
        pos = bottom_of(page, "#home-positions")
        c.check(f"open positions are reachable without scrolling at {width}",
                pos <= height, f"positions end at {pos:.0f}px")
        page.close()

    # ── the vertical rhythm actually exists (M3.5-C3) ────────────────────────
    # The counterweight to the fold assertions above. Those reward a page that
    # gets SHORTER, so when `.home`'s `gap` was silently inert and all three
    # bands rendered touching at 0px, every one of them passed more easily.
    # A gate that can only be satisfied by collapsing needs a partner that
    # fails on collapse, or the suite is biased towards the defect.
    page = open_home(browser, base, FULL, console=console)
    rhythm = page.evaluate(RHYTHM_JS)
    c.check("the bands are separated by exactly one --space-6",
            rhythm["ok"], f"expected {rhythm['expected']}px, measured "
                          f"{rhythm['gaps']}")
    page.close()

    # ── one column seam down the whole page (M3.5-C4) ────────────────────────
    # Asserted from rendered edges at both required sizes, not from the CSS.
    # The defect it replaces was two bands carrying their own ratios, which is
    # invisible in a stylesheet review and obvious the moment the two numbers
    # are put side by side.
    for width in (1920, 1440):
        page = open_home(browser, base, FULL, width=width, height=1000,
                         console=console)
        seam = page.evaluate(SEAM_JS)
        c.check(f"band 2 and band 3 share one column seam at {width}px",
                seam["ok"], f"band2 seam at {seam['b2']}, band3 at {seam['b3']}")
        page.close()

    # ── 5-6: band 2 never reverses ───────────────────────────────────────────
    for width in (1920, 1024):
        page = open_home(browser, base, FULL, width=width, height=1000,
                         console=console)
        geom = page.evaluate(
            "() => {const p = document.getElementById('home-positions')"
            "        .getBoundingClientRect();"
            "  const n = document.getElementById('home-next')"
            "        .getBoundingClientRect();"
            "  return {px: p.left, py: p.top, nx: n.left, ny: n.top};}")
        ordered = (geom["py"] < geom["ny"] - 1) or (geom["px"] < geom["nx"] - 1)
        c.check(f"positions come before what-to-do-next at {width}px", ordered,
                f"{geom}")
        page.close()

    # ── 7-9: the eye path is the DOM order ───────────────────────────────────
    page = open_home(browser, base, FULL, console=console)
    c.check("every named instrument is present",
            instrument_order(page) == EYE_PATH,
            str(instrument_order(page)))
    c.check("the status line is a paragraph, not a button",
            page.eval_on_selector("#home-status", "e => e.tagName") == "P")
    # §2.13: announced when Home receives focus, NOT when it changes. A live
    # region would interrupt a screen-reader user on every websocket push.
    c.check("the status line is not a live region",
            page.eval_on_selector(
                "#home-status",
                "e => !e.getAttribute('aria-live') && "
                "!e.closest('[aria-live]')"))

    # ── 10-13: the populated numbers reach the screen ────────────────────────
    c.check("the account value is the one metric at display size",
            page.eval_on_selector(
                "#hm-account-v",
                "e => parseFloat(getComputedStyle(e).fontSize) >= 32"))
    c.check("open risk states its share of the account",
            "8.1%" in page.inner_text("#hm-risk-c"))
    c.check("a sufficient sample shows the win rate with its n",
            "58%" in page.inner_text("#hm-win-v")
            and "41" in page.inner_text("#hm-win-c"))
    c.check("the working-orders subsection appears when there is one",
            page.is_visible("#hp-working-group"))
    page.close()

    # ── 14-16: evidence is IN the item, and the ranking is not re-ordered ────
    page = open_home(browser, base, FULL, console=console)
    next_text = page.inner_text("#hn-body")
    c.check("a finding carries its evidence in the item, not a tooltip",
            "p=0.004" in next_text and "26 trades" in next_text)
    c.check("the ranked items keep the order the engine gave them",
            next_text.index("open risk") < next_text.index("0-2 DTE"))
    c.check("at most three items are shown",
            page.eval_on_selector_all("#hn-body .hn-item", "n => n.length") <= 3)
    page.close()

    # ── 17-19: a floor is marked ON the number ───────────────────────────────
    page = open_home(browser, base, UNPRICED, console=console)
    c.check("an unpriced book marks open risk as a floor",
            "≥" in page.inner_text("#hm-risk-v"),
            page.inner_text("#hm-risk-v"))
    c.check("and says how many positions are unpriced",
            "2 of 2" in page.inner_text("#hm-risk-c"))
    page.close()
    page = open_home(browser, base, FULL, console=console)
    c.check("a fully priced book states open risk plainly",
            "≥" not in page.inner_text("#hm-risk-v"),
            page.inner_text("#hm-risk-v"))
    page.close()

    # ── 20-25: empty — every region offers a verb ────────────────────────────
    page = open_home(browser, base, EMPTY, console=console)
    c.check("an empty account never claims a win rate",
            "%" not in page.inner_text("#hm-win-v")
            and "of 30" in page.inner_text("#hm-win-c"),
            page.inner_text("#hm-win-v") + " / " + page.inner_text("#hm-win-c"))
    for region, verb in (("#hp-body", "trade"), ("#hn-body", "show me"),
                         ("#home-equity", "scan"), ("#hw-body", "add")):
        text = page.inner_text(region).lower()
        c.check(f"the empty {region} offers a verb", verb in text, text[:70])
    c.check("H4 states the evidence threshold rather than staying silent",
            "closed trades" in page.inner_text("#hn-body").lower())
    empty_order = instrument_order(page)
    page.close()

    # ── 26-29: error — regions fail alone ────────────────────────────────────
    page = open_home(browser, base, BROKEN, console=console)
    c.check("a failed positions region explains itself",
            "unavailable" in page.inner_text("#hp-body").lower())
    c.check("'I could not look' is distinguished from 'no findings'",
            "could not read" in page.inner_text("#hn-body").lower())
    c.check("a failing region leaves the metric cluster rendering",
            page.is_visible("#home-metrics")
            and "$" in page.inner_text("#hm-account-v"))
    c.check("and leaves the status line on screen",
            bool(page.inner_text("#home-status").strip()))
    error_order = instrument_order(page)
    page.close()

    # ── 30-32: the shape invariant across all four states ────────────────────
    page = open_home(browser, base, EMPTY, hang=True, console=console)
    c.check("a slow load skeletonises rather than blanking",
            page.get_attribute("#home", "data-state") == "loading")
    c.check("but every region label is already on screen",
            "POSITIONS" in page.inner_text("#home-positions").upper()
            and "WHAT TO DO NEXT" in page.inner_text("#home-next").upper())
    loading_order = instrument_order(page)
    # Drop the hanging route before closing, or Playwright cancels an in-flight
    # handler on teardown and prints a CancelledError traceback that reads like
    # a failure in a gate that passed.
    page.unroute("**/api/v1/home")
    page.close()

    c.check("loading, empty and error keep the populated SHAPE",
            loading_order == empty_order == error_order == EYE_PATH,
            f"loading={loading_order} empty={empty_order} error={error_order}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8808)
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-home-"))
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
            print(f"\nFAIL: {len(c.failures)} of {c.total} Home checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"\nOK: {c.passed}/{c.total} Home checks passed in a real "
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
                time.sleep(0.4)


if __name__ == "__main__":
    raise SystemExit(main())
