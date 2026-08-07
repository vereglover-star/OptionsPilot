"""Headless-browser certification of the UI V2 shell (M2).

Sibling to `workspace_check.py`, `guide_check.py` and the rest, and written for
the same reason: `index.html` has no automated coverage, and the shell is the
one surface every future milestone builds on. A regression here is not one
broken screen, it is every screen.

What it asserts, and why each one rather than something easier:

  * **The four permanent parts exist and only content scrolls.** The frame,
    rail and strip are the fixed cockpit; a shell whose chrome scrolls away is
    not a shell.
  * **The active rail item is never marked by colour alone.** Background AND a
    left edge, per `UI_V2_DESIGN.md` §11.2 rule 1 — asserted from computed
    style, because "we set a class" is not the claim being made.
  * **The palette does not move as results change.** Its top, left and width
    are compared across queries. Height legitimately changes; position must
    not, and §1.5 calls a re-centring palette unusable.
  * **Old names resolve AND display where they went.** The rename has to teach
    rather than strand, so the mapping being visible is the assertion.
  * **The palette cannot place an order.** A command list that spends money two
    keystrokes after a typo is a different product.
  * **The two mode axes stay orthogonal through the UI.** Not through the
    config — through the actual controls a user clicks.
  * **No fact appears in both Flight Status and the strip.** The split is the
    thing most likely to blur, and blurring it is the failure `CLAUDE.md`
    records for provider health living in two objects.
  * **The flag genuinely rolls back.** Turn it off and the legacy navigation is
    back, because that is the entire safety argument for shipping this at all.

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

#: The five destinations plus the utility, in the order §4.2 fixes them.
DESTINATIONS = ["home", "trade", "portfolio", "research", "journal", "settings"]

#: Old tab name -> the text the palette must SHOW for it (§4.3).
MOVED = {"Coach": "Journal", "Backtest": "Research", "Learning": "Research",
         "Charts": "Research", "Dashboard": "Home", "Watchlist": "Research"}

#: Widths the rail's geometry is asserted at. Chosen to straddle every
#: breakpoint the rail has ever had rather than the ones it has today, so a
#: future change to WHERE it collapses cannot quietly stop exercising the
#: collapsed mode. The assertions are about geometry, not about which mode is
#: active, so they hold whatever the breakpoints become.
RAIL_WIDTHS = (1600, 1439, 1300, 1279, 1100, 1024)

#: The accessible name every rail item must expose at EVERY width. Asserted
#: through Playwright's role/name engine, which runs the browser's own
#: accessible-name computation — so it fails if the name comes from a
#: `display:none` label, which contributes nothing, and passes only if
#: something the computation can actually see supplies it. Checking for the
#: presence of an `aria-label` attribute instead would pass on markup that a
#: screen reader still announces as an empty link.
RAIL_NAMES = ("Home", "Trade", "Portfolio", "Research", "Journal", "Settings",
              "Pilot")

#: Every rail icon measured against the rail's own clipping box, plus its
#: horizontal centring when the rail is in icon-only mode. Centring is only
#: meaningful there — in expanded mode the icon is deliberately left-aligned
#: against its label — so the mode is derived from whether the label renders
#: rather than from a hard-coded width.
RAIL_GEOMETRY_JS = """() => {
  const rail = document.querySelector('#shell-rail');
  const rb = rail.getBoundingClientRect();
  // The clipping box is the PADDING box: `overflow:hidden` clips there, not
  // at the border box, and the difference is exactly the border that made the
  // original defect a 1px-off judgement call.
  const left = rb.left, right = rb.left + rail.clientWidth;
  const detail = [], offcentre = [];
  let iconOnly = false;
  rail.querySelectorAll('a').forEach(a => {
    const ic = a.querySelector('.ic');
    if (!ic) return;
    const lbl = a.querySelector('.lbl');
    const labelShown = lbl && getComputedStyle(lbl).display !== 'none';
    if (!labelShown) iconOnly = true;
    const b = ic.getBoundingClientRect();
    const name = (a.dataset.dest || 'pilot');
    if (b.width < 1 || b.height < 1) {
      detail.push(name + ' icon has collapsed to ' +
                  b.width.toFixed(1) + 'x' + b.height.toFixed(1));
    } else if (b.left < left - 0.5 || b.right > right + 0.5) {
      detail.push(name + ' icon spans ' + b.left.toFixed(1) + '->' +
                  b.right.toFixed(1) + ' but the rail clips ' +
                  left.toFixed(1) + '->' + right.toFixed(1));
    }
    if (!labelShown) {
      const slack = ((b.left - left) - (right - b.right));
      if (Math.abs(slack) > 1.5) {
        offcentre.push(name + ' is ' + slack.toFixed(1) + 'px off centre');
      }
    }
  });
  return {clipped: detail.length > 0, detail: detail,
          centred: offcentre.length === 0, offcentre: offcentre,
          iconOnly: iconOnly};
}"""


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


def rect(page, selector: str) -> tuple:
    return tuple(page.eval_on_selector(
        selector,
        "e => {const r = e.getBoundingClientRect(); return [r.top, r.left, r.width];}"))


def run_checks(page, base: str, c: Checks) -> None:
    # ── 1-4: the four permanent parts ────────────────────────────────────────
    for part, label in (("#shell-frame", "frame"), ("#shell-rail", "nav rail"),
                        ("#shell-strip", "system strip")):
        c.check(f"the {label} is present", page.is_visible(part))
    c.check("the legacy navigation is out of the way",
            page.is_hidden('nav[aria-label="Main"]'))

    # ── 5: only content scrolls ──────────────────────────────────────────────
    scrolls = page.evaluate(
        "() => {const m = document.querySelector('body.shell-v2 > main');"
        " return [getComputedStyle(m).overflowY, getComputedStyle(document.body).overflowY];}")
    c.check("only the content region scrolls; the shell does not",
            scrolls[0] in ("auto", "scroll") and scrolls[1] == "hidden", str(scrolls))

    # ── 6-8: the rail ────────────────────────────────────────────────────────
    rail = page.eval_on_selector_all("#shell-rail a[data-dest]",
                                     "e => e.map(x => x.dataset.dest)")
    c.check("the rail lists the six destinations in order",
            rail == DESTINATIONS, str(rail))
    c.check("Settings is not numbered, because it is a utility not a peer",
            page.eval_on_selector_all(
                '#shell-rail a[data-dest="settings"] .kbd', "e => e.length") == 0)
    c.check("Pilot sits with Settings at the foot of the rail",
            page.eval_on_selector_all("#shell-rail-util a", "e => e.length") == 2)

    # ── the rail renders WHOLE at every supported width (M3.5-C1) ────────────
    # The defect this replaces was invisible to every existing assertion: the
    # items were present, ordered, named and clickable, and their icons were
    # sliced vertically in half by `overflow:hidden` because a UA list padding
    # pushed them past the rail's right edge. "The element exists" is not the
    # claim a navigation makes. This measures the icon against the rail's own
    # clipping box, which is the thing the user actually sees.
    for width in RAIL_WIDTHS:
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(200)
        geo = page.evaluate(RAIL_GEOMETRY_JS)
        c.check(f"no rail icon is clipped at {width}px",
                not geo["clipped"], "; ".join(geo["detail"][:3]))
        # Emitted ONLY in icon-only mode. Running it at expanded widths would
        # measure nothing and pass, which is a check that reports success for
        # testing zero elements — the failure mode M3-C9 had to correct twice.
        if geo["iconOnly"]:
            c.check(f"every rail icon is centred in the rail at {width}px",
                    geo["centred"], "; ".join(geo["offcentre"][:3]))
        # M3.5-C2. The name has to survive the label being hidden, which is
        # exactly what it did not do before this milestone.
        rail_el = page.locator("#shell-rail")
        missing = [n for n in RAIL_NAMES
                   if rail_el.get_by_role("link", name=n, exact=True).count() != 1]
        c.check(f"every rail item announces its name at {width}px",
                not missing, f"unnamed: {missing}")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.wait_for_timeout(200)

    # ── 9: the active item is never colour alone ─────────────────────────────
    page.evaluate("Shell.goTo('trade')")
    page.wait_for_timeout(900)
    style = page.eval_on_selector(
        '#shell-rail a[data-dest="trade"]',
        "e => {const s = getComputedStyle(e);"
        " return [s.backgroundColor, s.boxShadow, e.getAttribute('aria-current')];}")
    c.check("the active destination carries a background AND an edge, not "
            "colour alone",
            style[0] not in ("rgba(0, 0, 0, 0)", "transparent")
            and "inset" in (style[1] or "") and style[2] == "page", str(style))

    # ── 10-11: the destination is derived from the section, not stored twice ──
    page.evaluate("switchTab('coach')")
    page.wait_for_timeout(900)
    c.check("a legacy route still lands the shell on the right destination",
            page.text_content("#sf-dest-name") == "Journal",
            page.text_content("#sf-dest-name"))
    c.check("and names the section it is showing",
            page.text_content("#sf-dest-section") == "Review",
            page.text_content("#sf-dest-section"))

    # ── 12-17: the command palette ───────────────────────────────────────────
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette", state="visible", timeout=8000)
    page.wait_for_timeout(400)
    c.check("Ctrl+K opens the palette with focus in its input",
            page.evaluate("document.activeElement.id") == "palette-input",
            page.evaluate("document.activeElement.id"))

    groups = page.eval_on_selector_all(".pal-group", "e => e.map(x => x.textContent)")
    expected = ["DESTINATIONS", "ACTIONS", "SETTINGS"]
    c.check("groups appear in the fixed order, empty ones omitted",
            [g.upper()[:12] for g in groups][:3] == [e[:12] for e in expected],
            str(groups))

    before = rect(page, "#palette")
    page.fill("#palette-input", "journal")
    page.wait_for_timeout(400)
    after = rect(page, "#palette")
    c.check("the palette does not move as results change", before == after,
            f"{before} -> {after}")

    page.fill("#palette-input", "coach")
    page.wait_for_timeout(400)
    row = " ".join(page.eval_on_selector_all(".pal-row", "e => e.map(x => x.textContent)"))
    c.check("an old tab name still resolves", "Coach" in row, row[:80])
    c.check("and the palette SHOWS where it went rather than silently "
            "redirecting", MOVED["Coach"] in row, row[:80])

    page.fill("#palette-input", "")
    page.wait_for_timeout(400)
    names = page.eval_on_selector_all(".pal-name", "e => e.map(x => x.textContent)")
    c.check("no palette entry claims to place an order",
            not any(n.lower().startswith("buy") or n.lower().startswith("sell")
                    for n in names), str(names[:6]))
    order = [n for n in names if "order on" in n]
    c.check("the order entry names the workspace symbol and navigates",
            bool(order) and "SPY" in order[0], str(order))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    c.check("Esc dismisses the palette", page.is_hidden("#palette"))

    # ── 18-20: symbol jump is the one editor ─────────────────────────────────
    page.evaluate("Shell.goTo('home')")
    page.wait_for_timeout(600)
    page.keyboard.press("/")
    page.wait_for_selector("#symjump", state="visible", timeout=8000)
    page.fill("#symjump-input", "QQQ")
    page.wait_for_timeout(1200)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)
    c.check("the symbol jump sets the workspace symbol",
            page.text_content("#sf-ctx-symbol") == "QQQ",
            page.text_content("#sf-ctx-symbol"))
    c.check("and every other symbol render follows it",
            page.input_value("#tk-symbol").upper() == "QQQ"
            and page.input_value("#bt-symbol").upper() == "QQQ",
            f'tk={page.input_value("#tk-symbol")} bt={page.input_value("#bt-symbol")}')

    page.evaluate("Shell.goTo('research','charts')")
    page.wait_for_selector("#ch-symbol", state="visible")
    page.click("#ch-symbol")
    page.keyboard.press("/")
    page.wait_for_timeout(300)
    c.check("`/` inside a text input types a slash rather than hijacking it",
            page.is_hidden("#symjump") and "/" in page.input_value("#ch-symbol"))
    page.press("#ch-symbol", "Escape")

    # ── 21-23: Flight Status and the orthogonality invariant ─────────────────
    page.evaluate("Shell.goTo('home')")
    page.wait_for_timeout(600)
    page.click("#sf-status")
    page.wait_for_selector("#flight-status", state="visible", timeout=8000)
    page.wait_for_timeout(500)
    why = (page.text_content("#fs-op-why") or "") + (page.text_content("#fs-mode-why") or "")
    c.check("each mode axis states that it does not affect the other",
            why.lower().count("does not change") >= 2, why[:90])

    who_before = page.eval_on_selector_all("#fs-op button.active", "e => e.map(x => x.dataset.op)")
    page.click('#fs-mode button[data-mode="high_risk"]')
    page.wait_for_timeout(2500)
    who_after = page.eval_on_selector_all("#fs-op button.active", "e => e.map(x => x.dataset.op)")
    risk_after = page.eval_on_selector_all("#fs-mode button.active", "e => e.map(x => x.dataset.mode)")
    c.check("changing the risk profile leaves who-trades untouched",
            who_before == who_after and risk_after == ["high_risk"],
            f"{who_before} -> {who_after}, risk {risk_after}")
    c.check("and the popover stays open, because these are changed in pairs",
            page.is_visible("#flight-status"))
    page.keyboard.press("Escape")
    page.click("body", position={"x": 400, "y": 500})
    page.wait_for_timeout(400)

    # ── 24-25: one fact, one owner ───────────────────────────────────────────
    strip = (page.text_content("#shell-strip") or "").lower()
    c.check("the strip does not repeat what Flight Status owns",
            not any(word in strip for word in ("paper", "market open", "market closed",
                                               "conservative", "high-risk")),
            strip[:80])
    page.click("#sf-status")
    page.wait_for_timeout(600)
    flight = (page.text_content("#flight-status") or "").lower()
    c.check("and Flight Status does not repeat what the strip owns",
            "v0." not in flight and "guided" not in flight and "full" not in flight,
            flight[:80])
    page.keyboard.press("Escape")
    page.click("body", position={"x": 400, "y": 500})
    page.wait_for_timeout(400)

    # ── 26-27: the Pilot scaffold ────────────────────────────────────────────
    page.click("#sf-pilot")
    page.wait_for_timeout(500)
    c.check("the Pilot panel opens and overlays rather than reflowing",
            page.is_visible("#pilot-panel")
            and page.eval_on_selector("#pilot-panel", "e => getComputedStyle(e).position") == "fixed")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    c.check("and Esc closes it", page.is_hidden("#pilot-panel"))

    # ── 28-30: THE ROLLBACK ──────────────────────────────────────────────────
    # The entire safety argument for shipping a shell mid-migration.
    post(base, "/api/workspace", {"shell_v2": False})
    page.reload()
    page.wait_for_selector('nav[aria-label="Main"]', state="visible", timeout=25000)
    page.wait_for_timeout(2500)
    c.check("turning the flag off brings the legacy navigation back",
            page.is_visible('nav[aria-label="Main"] button[data-tab="dashboard"]'))
    c.check("and takes the shell off the page entirely",
            page.is_hidden("#shell-frame") and page.is_hidden("#shell-rail")
            and page.is_hidden("#shell-strip"))
    c.check("and the legacy header is back with its own controls",
            page.is_visible("#op-seg") and page.is_visible("#mode-seg"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8806)
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-shell-"))
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
            browser = p.chromium.launch(channel="msedge", headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on("console",
                    lambda m: console.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(str(e)))
            page.goto(base)
            page.wait_for_selector("#shell-frame", state="visible", timeout=25000)
            page.wait_for_timeout(2500)
            run_checks(page, base, c)
            browser.close()

        c.check("zero browser console errors", not console, "; ".join(console[:3]))

        if c.failures:
            print(f"\nFAIL: {len(c.failures)} of {c.total} shell checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"\nOK: {c.passed}/{c.total} shell checks passed in a real "
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
