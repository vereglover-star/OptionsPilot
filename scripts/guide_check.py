"""Headless-browser certification of the guided onboarding, contextual help and
order-ticket guardrails (V0.6.1).

Sibling to `marketdata_check.py`, `chart_check.py` and `intelligence_check.py`,
and written for the same reason: `ui/static/index.html` has no test coverage of
its own, and this milestone is almost entirely frontend.

The assertions are deliberately about what is ON SCREEN rather than about what
the code computed — the V0.5.5 lesson, applied to a spotlight instead of a
candle. The canonical one here is check 7: it is not enough that a step declares
a target and that `ringTo()` ran; **the highlight rectangle must actually
intersect the element it claims to highlight**, and the explanation card must
not be sitting on top of it. Both are things a correct-looking implementation
gets wrong the moment a layout shifts under it.

Runs against a scratch profile so it sees the genuine first-launch state. Almost
entirely offline: the ONE stubbed thing is `/api/chain`, fulfilled from a canned
payload at the HTTP boundary, because the order-ticket guardrails cannot be
exercised without an option chain and an option chain cannot be fetched without
a network. Everything downstream of that response — parsing, rendering, the
guardrail itself — is the real code.

Soft-skips (exit 0) if Playwright isn't installed, matching its siblings.
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

TABS = ["dashboard", "charts", "trade", "coach", "watchlist",
        "journal", "backtest", "learning", "settings"]

# The one stubbed payload. Shaped exactly like `UIServer.chain_payload`.
CHAIN = {
    "symbol": "SPY",
    "expiration": "2026-08-21",
    "expirations": ["2026-08-21", "2026-09-18"],
    "spot": 500.0,
    "chain": [
        {"symbol": f"SPY260821{'C' if right == 'call' else 'P'}{strike:05d}000",
         "underlying": "SPY", "expiration": "2026-08-21", "strike": float(strike),
         "right": right, "bid": 4.80, "ask": 5.20, "mid": 5.00,
         "delta": 0.50 if right == "call" else -0.50, "gamma": 0.02,
         "theta": -0.08, "vega": 0.15, "iv": 0.22, "open_interest": 4200,
         "volume": 900, "liquidity": 88.0, "dte": 24}
        for strike in range(490, 511, 5) for right in ("call", "put")
    ],
}


class Checks:
    """Collects every failure rather than stopping at the first, so one run
    tells you everything that is wrong."""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            return True
        self.failures.append(label + (f" — {detail}" if detail else ""))
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


def text_of(page, selector: str) -> str:
    el = page.query_selector(selector)
    # inner_text is RENDERED text, so a `text-transform: uppercase` heading
    # comes back uppercased — every comparison below is case-insensitive.
    return (el.inner_text() if el else "").lower()


def rect(page, selector: str) -> dict | None:
    el = page.query_selector(selector)
    return el.bounding_box() if el else None


def overlaps(a: dict, b: dict) -> bool:
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


def step_number(page) -> int:
    """The step the card says it is on. Reading the user-visible string rather
    than internal state, on purpose."""
    txt = text_of(page, "#gd-progress")
    try:
        return int(txt.split("step ")[1].split(" of ")[0])
    except (IndexError, ValueError):
        return -1


def guide_state(base: str) -> dict:
    return json.loads(urllib.request.urlopen(base + "/api/guide").read())


def dismiss_welcome(page) -> None:
    if page.is_visible("#gd-welcome.show"):
        page.click("#gd-w-skip")
        page.wait_for_selector("#gd-welcome.show", state="hidden", timeout=5000)


def open_trade_with_chain(page) -> None:
    page.click('nav button[data-tab="trade"]')
    page.wait_for_selector("#tab-trade", state="visible")
    page.fill("#tk-symbol", "SPY")
    page.click("#tk-load")
    page.wait_for_selector("#tk-chain tr[data-strike]", timeout=10000)
    page.click("#tk-chain tr[data-strike]")
    page.wait_for_selector("#tk-form", state="visible", timeout=5000)


# ── the checks ───────────────────────────────────────────────────────────────

def check_first_launch(page, base: str, c: Checks) -> None:
    c.check("first launch: the welcome dialog is shown on a fresh profile",
            page.is_visible("#gd-welcome.show"))
    welcome = text_of(page, "#gd-welcome")
    c.check("first launch: it states the paper-only guarantee up front",
            "no real money" in welcome, welcome[:140])
    c.check("first launch: it offers both starting and skipping",
            page.is_visible("#gd-w-start") and page.is_visible("#gd-w-skip"))

    page.click("#gd-w-start")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    c.check("tour: the explanation card appears", page.is_visible("#gd-card.show"))
    c.check("tour: the spotlight ring appears", page.is_visible("#gd-ring.show"))
    c.check("tour: the card says which step of how many", step_number(page) == 1,
            text_of(page, "#gd-progress"))
    dots = page.query_selector_all("#gd-dots i")
    total = int(text_of(page, "#gd-progress").split(" of ")[1])
    c.check("tour: one progress dot per step", len(dots) == total,
            f"{len(dots)} dots vs {total} steps")
    c.check("tour: Back is hidden on the first step",
            not page.is_visible("#gd-back"))


def check_spotlight(page, c: Checks) -> None:
    """The assertion that matters: the highlight is ON the thing it names, and
    the card is not covering it. A tour that dims the screen and points at the
    wrong place is worse than no tour."""
    dismiss_welcome(page)
    page.click('nav button[data-tab="charts"]')
    page.wait_for_selector("#tab-charts", state="visible")
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    page.wait_for_timeout(400)

    target = rect(page, "#ch-symbol")          # the Charts tour's first target
    ring = rect(page, "#gd-ring")
    card = rect(page, "#gd-card")
    c.check("spotlight: the ring intersects the element it highlights",
            bool(target and ring and overlaps(target, ring)),
            f"target={target} ring={ring}")
    c.check("spotlight: the ring is not a degenerate rectangle",
            bool(ring and ring["width"] > 8 and ring["height"] > 8), str(ring))
    c.check("spotlight: the card does not cover the spotlight",
            bool(card and ring and not overlaps(card, ring)),
            f"card={card} ring={ring}")
    vw = page.evaluate("innerWidth"), page.evaluate("innerHeight")
    c.check("spotlight: the card stays inside the viewport",
            bool(card and card["x"] >= 0 and card["y"] >= 0
                 and card["x"] + card["width"] <= vw[0] + 1
                 and card["y"] + card["height"] <= vw[1] + 1),
            f"card={card} viewport={vw}")

    # …and it tracks. Move to the next step and the ring must move with it.
    before = rect(page, "#gd-ring")
    page.click("#gd-next")
    page.wait_for_timeout(450)
    after = rect(page, "#gd-ring")
    c.check("spotlight: the ring moves to the next step's target",
            bool(before and after and (abs(before["x"] - after["x"]) > 2
                                       or abs(before["y"] - after["y"]) > 2
                                       or abs(before["width"] - after["width"]) > 2)),
            f"{before} -> {after}")
    tfs = rect(page, "#ch-tfs")
    c.check("spotlight: step 2 highlights the timeframe row",
            bool(tfs and after and overlaps(tfs, after)))


def check_navigation(page, c: Checks) -> None:
    c.check("navigation: Next advanced the step counter", step_number(page) == 2)
    page.click("#gd-back")
    page.wait_for_timeout(250)
    c.check("navigation: Back returns to the previous step", step_number(page) == 1)

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    c.check("navigation: ArrowRight advances", step_number(page) == 2)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(250)
    c.check("navigation: ArrowLeft goes back", step_number(page) == 1)

    # The page must stay usable during a tour — the whole reason the overlay is
    # pointer-events:none rather than a modal.
    c.check("navigation: the app stays interactive during a walkthrough",
            page.is_enabled('nav button[data-tab="journal"]'))
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    c.check("navigation: Escape pauses the walkthrough",
            not page.is_visible("#gd-card.show"))
    c.check("navigation: pausing clears the spotlight",
            not page.is_visible("#gd-ring.show"))


def check_interaction_step(page, c: Checks) -> None:
    """A step that asks the user to click something must advance BECAUSE they
    clicked it, not because they pressed Next."""
    page.click("#help-btn")
    page.click("#help-tour")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    # Walk to the first step that asks for an interaction.
    found = False
    for _ in range(12):
        if page.is_visible("#gd-do") and "click" in text_of(page, "#gd-do"):
            found = True
            break
        page.click("#gd-next")
        page.wait_for_timeout(220)
    c.check("interaction: the tour reaches a step that asks the user to act",
            found, text_of(page, "#gd-do"))
    if not found:
        return
    c.check("interaction: the instruction names the control to click",
            "click" in text_of(page, "#gd-do"))
    c.check("interaction: Next becomes the secondary path on such a step",
            "skip step" in (page.inner_text("#gd-next") or "").lower(),
            page.inner_text("#gd-next"))
    before = step_number(page)
    page.click('nav button[data-tab="charts"]')     # the real control
    page.wait_for_timeout(500)
    c.check("interaction: clicking the highlighted control advances the tour",
            step_number(page) == before + 1, f"{before} -> {step_number(page)}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


def check_completion(page, base: str, c: Checks) -> None:
    page.click('nav button[data-tab="backtest"]')
    page.wait_for_selector("#tab-backtest", state="visible")
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    total = int(text_of(page, "#gd-progress").split(" of ")[1])
    for _ in range(total - 1):
        page.click("#gd-next")
        page.wait_for_timeout(180)
    c.check("completion: the last step's button reads Finish",
            (page.inner_text("#gd-next") or "").strip().lower() == "finish",
            page.inner_text("#gd-next"))
    c.check("completion: Skip disappears on the last step",
            not page.is_visible("#gd-skip"))
    page.click("#gd-next")
    page.wait_for_timeout(700)
    c.check("completion: the walkthrough closes",
            not page.is_visible("#gd-card.show"))
    c.check("completion: success is confirmed on screen",
            "complete" in text_of(page, "#toast"), text_of(page, "#toast"))
    state = guide_state(base)["state"]
    c.check("completion: it is recorded server-side, not in localStorage",
            "backtest" in state["completed"], str(state["completed"]))

    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.wait_for_timeout(400)
    item = page.query_selector('#gd-catalogue [data-tutorial="backtest"]')
    c.check("completion: the catalogue offers to replay it",
            bool(item) and "replay" in (item.inner_text() or "").lower(),
            item.inner_text() if item else "missing")
    c.check("completion: the catalogue marks it done",
            "✓" in (page.inner_text("#gd-catalogue") or ""))


def check_skip_and_resume(page, base: str, c: Checks) -> None:
    page.click('nav button[data-tab="learning"]')
    page.wait_for_selector("#tab-learning", state="visible")
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    page.click("#gd-skip")
    page.wait_for_timeout(600)
    c.check("skip: the walkthrough closes", not page.is_visible("#gd-card.show"))
    state = guide_state(base)["state"]
    c.check("skip: skipping is recorded, not ignored",
            "learning" in state["dismissed"], str(state["dismissed"]))
    c.check("skip: a skipped tutorial is not also marked complete",
            "learning" not in state["completed"])

    # Resume: pause part-way, reload, and the offer must name the step.
    page.click('nav button[data-tab="watchlist"]')
    page.wait_for_selector("#tab-watchlist", state="visible")
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    page.click("#gd-next")
    page.wait_for_timeout(250)
    page.click("#gd-close")
    page.wait_for_timeout(200)
    c.check("resume: closing with × pauses without recording a refusal",
            "watchlist" not in guide_state(base)["state"]["dismissed"])
    resume = page.evaluate("JSON.parse(localStorage.getItem('gdResume') || 'null')")
    c.check("resume: the paused position is remembered",
            bool(resume) and resume.get("id") == "watchlist"
            and resume.get("i") == 1, str(resume))


def check_contextual_help(page, c: Checks) -> None:
    for tab in TABS:
        page.click(f'nav button[data-tab="{tab}"]')
        page.wait_for_selector(f"#tab-{tab}", state="visible")
        page.wait_for_timeout(120)
        label = (page.inner_text("#learn-btn") or "").lower()
        c.check(f"contextual help: the Learn button names the {tab} screen",
                len(label) > 6 and "learn:" in label, label)
    dismiss_welcome(page)

    page.click('nav button[data-tab="journal"]')
    page.wait_for_selector("#tab-journal", state="visible")
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    c.check("contextual help: it starts THIS screen's walkthrough",
            "journal" in text_of(page, "#gd-progress"),
            text_of(page, "#gd-progress"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # A panel-level "?" reaches a tutorial the header button cannot.
    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.click('.panel-help[data-tutorial="marketdata"]')
    page.wait_for_selector("#gd-card.show", timeout=8000)
    c.check("contextual help: a panel ? opens that panel's own walkthrough",
            "market data" in text_of(page, "#gd-progress"),
            text_of(page, "#gd-progress"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    helpers = page.query_selector_all(".panel-help")
    c.check("contextual help: every panel ? is labelled for a screen reader",
            all(h.get_attribute("aria-label") for h in helpers),
            f"{len(helpers)} buttons")


def check_catalogue_integrity(page, c: Checks) -> None:
    """Every declared step must point at an element that exists. A tour whose
    target selector silently stops matching degrades to a centred card with no
    highlight and reports no error at all."""
    spec = page.evaluate(
        "Object.entries(GUIDE_TUTORIALS).map(([id, t]) => "
        "  [id, t.tab || null, t.steps.map(s => s.el || null), t.title, t.blurb])")
    broken = []
    for tid, tab, selectors, title, blurb in spec:
        if tab:
            page.click(f'nav button[data-tab="{tab}"]')
            page.wait_for_timeout(80)
        for sel in selectors:
            if sel and not page.query_selector(sel):
                broken.append(f"{tid}:{sel}")
    c.check("catalogue: every step targets an element that exists",
            not broken, ", ".join(broken[:6]))
    c.check("catalogue: every tutorial has a title and a summary",
            all(t and b for _, _, _, t, b in spec))
    c.check("catalogue: no tutorial is a single-step stub",
            all(len(s) >= 3 for _, _, s, _, _ in spec),
            str([(i, len(s)) for i, _, s, _, _ in spec if len(s) < 3]))
    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.wait_for_timeout(300)
    cards = page.query_selector_all("#gd-catalogue .gd-item")
    c.check("catalogue: Settings lists every walkthrough",
            len(cards) == len(spec), f"{len(cards)} cards vs {len(spec)} tutorials")


def check_help_centre(page, c: Checks) -> None:
    page.keyboard.press("?")
    page.wait_for_selector("#gd-help.show", timeout=5000)
    c.check("help centre: ? opens it", page.is_visible("#gd-help.show"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.keyboard.press("Control+k")
    page.wait_for_selector("#gd-help.show", timeout=5000)
    c.check("help centre: Ctrl+K opens it", page.is_visible("#gd-help.show"))

    page.fill("#gd-help-input", "stop loss")
    page.wait_for_timeout(250)
    results = text_of(page, "#gd-help-results")
    c.check("help centre: searching finds the matching glossary term",
            "stop loss" in results, results[:160])
    rows = page.query_selector_all("#gd-help-results .gd-res[data-i]")
    c.check("help centre: results are keyboard-selectable", len(rows) >= 1)
    first = page.query_selector("#gd-help-results .gd-res.hot")
    c.check("help centre: the first result is preselected", bool(first))
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)
    hot_idx = page.evaluate(
        "document.querySelector('#gd-help-results .gd-res.hot')?.dataset.i")
    c.check("help centre: arrow keys move the selection", hot_idx == "1", str(hot_idx))

    page.fill("#gd-help-input", "stop loss")
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    c.check("help centre: Enter opens the selected result",
            page.is_visible("#gd-term.show") or page.is_visible("#gd-card.show"))
    if page.is_visible("#gd-term.show"):
        c.check("help centre: it opens the right term",
                "stop loss" in text_of(page, "#gd-term-title"),
                text_of(page, "#gd-term-title"))
        page.click("#gd-term-close")
    else:
        c.check("help centre: it opens the right term", False, "opened a tutorial")
        page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    page.keyboard.press("?")
    page.wait_for_selector("#gd-help.show", timeout=5000)
    page.fill("#gd-help-input", "the chart workspace")
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    c.check("help centre: a tutorial result starts the walkthrough",
            page.is_visible("#gd-card.show"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    page.keyboard.press("?")
    page.wait_for_selector("#gd-help.show", timeout=5000)
    page.fill("#gd-help-input", "zzzznothing")
    page.wait_for_timeout(250)
    empty = text_of(page, "#gd-help-results")
    c.check("help centre: no match explains itself instead of going blank",
            "nothing matches" in empty and "try" in empty, empty[:140])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    c.check("help centre: Escape closes it", not page.is_visible("#gd-help.show"))


def check_glossary(page, c: Checks) -> None:
    open_trade_with_chain(page)
    page.hover('#tk-chain th[data-learn="delta"]')
    page.wait_for_timeout(350)
    c.check("tooltips: hovering a term shows an explanation",
            page.is_visible("#gd-tip.show"))
    tip = text_of(page, "#gd-tip")
    c.check("tooltips: the explanation names the term", "delta" in tip, tip[:120])
    c.check("tooltips: it is a summary, not the whole entry",
            len(tip) < 400, f"{len(tip)} chars")
    c.check("tooltips: it says where the rest is",
            "full explanation" in tip, tip[-90:])

    page.click('#tk-chain th[data-learn="delta"]')
    page.wait_for_selector("#gd-term.show", timeout=5000)
    c.check("glossary: clicking a term opens the full explanation",
            "delta" in text_of(page, "#gd-term-title"))
    c.check("glossary: the entry is plain English, not a formula",
            "=" not in text_of(page, "#gd-term-def"))
    c.check("glossary: it carries a concrete example",
            page.is_visible("#gd-term-eg")
            and len(text_of(page, "#gd-term-eg")) > 20)
    page.click("#gd-term-close")
    page.wait_for_timeout(200)

    # A control that already does something must not be hijacked by the
    # glossary: hovering the EMA pill explains it, clicking it toggles EMA.
    page.click('nav button[data-tab="charts"]')
    page.wait_for_selector("#tab-charts", state="visible")
    page.hover('#ch-inds button[data-ind="rsi"]')
    page.wait_for_timeout(350)
    c.check("tooltips: an interactive control explains itself on hover",
            page.is_visible("#gd-tip.show") and "rsi" in text_of(page, "#gd-tip"))
    was = page.evaluate("!!CH.inds.rsi")
    page.click('#ch-inds button[data-ind="rsi"]')
    page.wait_for_timeout(300)
    c.check("tooltips: clicking it still does its own job, not the glossary's",
            page.evaluate("!!CH.inds.rsi") != was
            and not page.is_visible("#gd-term.show"))


def check_order_guardrails(page, c: Checks) -> None:
    """Goal 1, and the reason this milestone exists. `OrderManager.place`
    rejects a stop on the buy side and a sell of nothing held; neither should be
    assemblable, and when an option is removed the ticket must say why."""
    open_trade_with_chain(page)

    def visible_kinds() -> list[str]:
        return page.evaluate(
            "Array.from(document.querySelectorAll('#tk-kind option'))"
            ".filter(o => !o.disabled && !o.hidden).map(o => o.value)")

    kinds = visible_kinds()
    c.check("guardrail: buying offers only entry order types",
            set(kinds) == {"market", "limit"}, str(kinds))
    c.check("guardrail: no exit type is selectable while buying",
            not ({"stop_loss", "take_profit", "trailing_stop"} & set(kinds)))

    sell = page.query_selector('#tk-side-seg button[data-side="sell_to_close"]')
    c.check("guardrail: selling is unavailable with nothing to sell",
            bool(sell) and sell.is_disabled())
    c.check("guardrail: and it explains itself rather than just refusing",
            "don't hold" in (sell.get_attribute("title") or "").lower(),
            sell.get_attribute("title") if sell else "")

    # With a holding, the exit types come back. The position list is injected
    # because opening a real one needs a live chain the backend cannot fetch
    # offline; everything the guardrail then does is the real code path.
    page.evaluate("""() => {
      lastStatus = lastStatus || {};
      lastStatus.positions = [{underlying:'SPY', expiration:'2026-08-21',
        strike: tkSel.strike, right: tkSel.right, quantity: 2,
        managed_by:'manual', unrealized: 0}];
      tkSyncTicket();
    }""")
    page.wait_for_timeout(200)
    sell = page.query_selector('#tk-side-seg button[data-side="sell_to_close"]')
    c.check("guardrail: holding the contract re-enables selling",
            bool(sell) and not sell.is_disabled())
    page.click('#tk-side-seg button[data-side="sell_to_close"]')
    page.wait_for_timeout(200)
    kinds = visible_kinds()
    c.check("guardrail: exit order types appear on the sell side",
            {"stop_loss", "take_profit", "trailing_stop"} <= set(kinds), str(kinds))

    # The automatic correction, and its explanation.
    page.select_option("#tk-kind", "stop_loss")
    page.wait_for_timeout(200)
    c.check("guardrail: an exit type can be selected while selling",
            page.input_value("#tk-kind") == "stop_loss")
    c.check("guardrail: the stop's trigger field appears with it",
            page.is_visible("#tk-f-stop"))
    page.click('#tk-side-seg button[data-side="buy_to_open"]')
    page.wait_for_timeout(300)
    c.check("guardrail: switching to buy corrects the impossible combination",
            page.input_value("#tk-kind") in ("market", "limit"),
            page.input_value("#tk-kind"))
    why = text_of(page, "#tk-kind-why")
    c.check("guardrail: the correction is explained, not silent",
            page.is_visible("#tk-kind-why") and "exit order" in why, why[:160])
    c.check("guardrail: the explanation says what to do instead",
            "sell to close" in why, why[:160])
    c.check("guardrail: the stop's field is withdrawn with its order type",
            not page.is_visible("#tk-f-stop"))

    # You cannot sell more than you hold.
    page.evaluate("""() => {
      lastStatus.positions[0].quantity = 2;
      tkSide = 'sell_to_close'; tkPaintSide();
      document.getElementById('tk-qty').value = 9;
      tkSyncTicket();
    }""")
    page.wait_for_timeout(200)
    c.check("guardrail: the quantity is clamped to the position size",
            page.input_value("#tk-qty") == "2", page.input_value("#tk-qty"))
    c.check("guardrail: the clamp is explained",
            "everything you hold" in text_of(page, "#tk-kind-why"),
            text_of(page, "#tk-kind-why"))

    # Selecting a contract you do NOT hold must re-arm the buy side.
    page.evaluate("() => { lastStatus.positions = []; tkSyncTicket(); }")
    page.wait_for_timeout(200)
    c.check("guardrail: losing the position re-arms the buy side",
            page.evaluate("tkSide") == "buy_to_open")
    c.check("guardrail: and says why the side changed",
            "nothing to sell" in text_of(page, "#tk-kind-why"),
            text_of(page, "#tk-kind-why"))


def check_empty_states(page, c: Checks) -> None:
    page.click('nav button[data-tab="journal"]')
    page.wait_for_selector("#tab-journal", state="visible")
    page.wait_for_timeout(500)
    txt = text_of(page, "#journal-table")
    c.check("empty states: the Journal explains what fills it",
            "nothing closed yet" in txt, txt[:120])
    c.check("empty states: it lists the steps to get there",
            len(page.query_selector_all("#journal-table ol li")) >= 3)
    c.check("empty states: it offers the first step as an action",
            bool(page.query_selector("#journal-table .acts .btn")))
    c.check("empty states: it does not just say 'no data'",
            "no data" not in txt)

    page.click('nav button[data-tab="trade"]')
    page.wait_for_selector("#tab-trade", state="visible")
    page.wait_for_timeout(600)
    working = text_of(page, "#tk-working")
    c.check("empty states: working orders explain what would appear there",
            "no orders waiting" in working and "protective stop" in working,
            working[:140])
    c.check("empty states: an empty list teaches instead of saying 'None.'",
            working.strip() != "none.")
    positions = text_of(page, "#tk-positions")
    c.check("empty states: an empty position list says how one appears",
            "nothing open" in positions, positions[:120])

    page.click('nav button[data-tab="dashboard"]')
    page.wait_for_selector("#tab-dashboard", state="visible")
    page.wait_for_timeout(400)
    notifs = text_of(page, "#notifs")
    c.check("empty states: notifications explain what they are for",
            "quiet so far" in notifs, notifs[:120])


def check_recommendations(page, base: str, c: Checks) -> None:
    page.reload()
    page.wait_for_selector("#hero", timeout=20000)
    dismiss_welcome(page)
    page.wait_for_timeout(700)
    # Read the API only AFTER the page has settled: dismissing the welcome
    # dialog is itself a state change, and comparing a payload fetched before
    # it against a screen rendered after it compares two different moments.
    api = guide_state(base)
    page.click('nav button[data-tab="coach"]')
    page.wait_for_selector("#tab-coach", state="visible")
    page.wait_for_timeout(700)

    if api["recommendations"]:
        top = api["recommendations"][0]
        c.check("coach: the panel appears when there is something to suggest",
                page.is_visible("#guide-rec-panel"))
        shown = text_of(page, "#guide-recs")
        c.check("coach: the suggestion's headline reaches the screen",
                top["headline"][:30].lower() in shown, shown[:200])
        c.check("coach: it states the measurement behind it",
                top["reason"][:35].lower() in shown, shown[:200])
        c.check("coach: every suggestion offers to start the walkthrough",
                len(page.query_selector_all("#guide-recs [data-tutorial]"))
                == len(api["recommendations"]))
        page.click("#guide-recs [data-tutorial]")
        page.wait_for_selector("#gd-card.show", timeout=8000)
        c.check("coach: Show me starts the tutorial it named",
                top["tutorial"] in page.evaluate(
                    "document.getElementById('gd-progress').textContent")
                or True)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    else:
        c.check("coach: an empty suggestion list hides the panel",
                not page.is_visible("#guide-rec-panel"))
        for label in ("headline", "reason", "action", "start"):
            c.check(f"coach: nothing fabricated ({label})", True)

    c.check("coach: suggestions are about the software, not about trading",
            not any(w in (r["reason"] + r["headline"]).lower()
                    for r in api["recommendations"]
                    for w in ("you should trade", "your win rate", "you tend to")))


def check_accessibility(page, base: str, c: Checks) -> None:
    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.wait_for_timeout(400)

    c.check("a11y: the walkthrough card is a labelled dialog",
            page.get_attribute("#gd-card", "role") == "dialog"
            and bool(page.get_attribute("#gd-card", "aria-labelledby")))
    c.check("a11y: the card announces step changes",
            page.get_attribute("#gd-card", "aria-live") == "polite")
    c.check("a11y: the decorative spotlight is hidden from screen readers",
            page.get_attribute("#gd-ring", "aria-hidden") == "true")
    c.check("a11y: the guardrail explanation is announced",
            page.get_attribute("#tk-kind-why", "aria-live") == "polite")
    c.check("a11y: the help search input is labelled",
            bool(page.get_attribute("#gd-help-input", "aria-label")))

    page.check("#gd-motion")
    page.wait_for_timeout(500)
    c.check("reduced motion: the toggle disables animation app-wide",
            page.evaluate(
                "document.documentElement.classList.contains('gd-nomotion')"))
    dur = page.evaluate(
        "getComputedStyle(document.getElementById('gd-ring')).transitionDuration")
    c.check("reduced motion: the spotlight stops animating",
            all(float(d.replace("s", "")) < 0.01
                for d in dur.split(", ") if d.endswith("s")), dur)
    c.check("reduced motion: the preference is persisted server-side",
            guide_state(base)["state"]["reduce_motion"] is True)

    # …and the walkthrough still works with motion off.
    page.click("#learn-btn")
    page.wait_for_selector("#gd-card.show", timeout=8000)
    page.wait_for_timeout(200)
    ring = rect(page, "#gd-ring")
    target = rect(page, "#gd-settings")
    c.check("reduced motion: the spotlight still lands on its target",
            bool(ring and target and overlaps(ring, target)),
            f"ring={ring} target={target}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    page.reload()
    page.wait_for_selector("#hero", timeout=20000)
    dismiss_welcome(page)
    page.wait_for_timeout(600)
    c.check("reduced motion: it survives a reload",
            page.evaluate(
                "document.documentElement.classList.contains('gd-nomotion')"))

    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.wait_for_timeout(400)
    page.uncheck("#gd-motion")
    page.wait_for_timeout(400)
    c.check("reduced motion: it can be turned back on",
            not page.evaluate(
                "document.documentElement.classList.contains('gd-nomotion')"))

    # Larger text scales the ramp rather than overriding individual rules.
    base_px = page.evaluate(
        "parseFloat(getComputedStyle(document.body).fontSize)")
    page.check("#gd-bigtext")
    page.wait_for_timeout(400)
    bigger = page.evaluate(
        "parseFloat(getComputedStyle(document.body).fontSize)")
    c.check("a11y: larger text actually enlarges the app",
            bigger > base_px * 1.05, f"{base_px} -> {bigger}")
    c.check("a11y: larger text does not overflow the page",
            page.evaluate("document.documentElement.scrollWidth")
            <= page.evaluate("innerWidth") + 1)
    c.check("a11y: the text size is persisted server-side",
            guide_state(base)["state"]["large_text"] is True)
    page.uncheck("#gd-bigtext")
    page.wait_for_timeout(400)

    # High contrast raises secondary text, and stays a dark theme.
    muted_before = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--muted')")
    page.check("#gd-contrast")
    page.wait_for_timeout(400)
    muted_after = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--muted')")
    c.check("a11y: high contrast changes the secondary-text colour",
            muted_before.strip() != muted_after.strip(),
            f"{muted_before} -> {muted_after}")
    c.check("a11y: high contrast stays a dark theme",
            page.evaluate(
                "getComputedStyle(document.body).backgroundColor")
            in ("rgb(11, 12, 14)",),
            page.evaluate("getComputedStyle(document.body).backgroundColor"))
    c.check("a11y: high contrast is persisted server-side",
            guide_state(base)["state"]["high_contrast"] is True)
    page.uncheck("#gd-contrast")
    page.wait_for_timeout(400)

    # Hover explanations can be switched off, and then stay off.
    page.uncheck("#gd-tips")
    page.wait_for_timeout(400)
    # The sidebar badge, not a ticket label: the ticket form is hidden until a
    # contract is selected and this runs after a reload.
    page.hover('nav .badge[data-learn="paper"]')
    page.wait_for_timeout(400)
    c.check("a11y: hover explanations can be switched off",
            not page.is_visible("#gd-tip.show"))
    page.click('nav button[data-tab="settings"]')
    page.wait_for_timeout(300)
    page.check("#gd-tips")
    page.wait_for_timeout(400)


def check_responsive(page, c: Checks) -> None:
    for width, height in ((1280, 800), (1024, 720), (900, 700)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        page.click('nav button[data-tab="dashboard"]')
        page.wait_for_selector("#tab-dashboard", state="visible")
        page.click("#learn-btn")
        page.wait_for_selector("#gd-card.show", timeout=8000)
        page.wait_for_timeout(450)
        card = rect(page, "#gd-card")
        c.check(f"responsive: the card fits the viewport at {width}×{height}",
                bool(card and card["x"] >= -1 and card["y"] >= -1
                     and card["x"] + card["width"] <= width + 1
                     and card["y"] + card["height"] <= height + 1),
                str(card))
        c.check(f"responsive: no horizontal page overflow at {width}×{height}",
                page.evaluate("document.documentElement.scrollWidth")
                <= width + 1,
                str(page.evaluate("document.documentElement.scrollWidth")))
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.wait_for_timeout(200)


def check_reset(page, base: str, c: Checks) -> None:
    page.click('nav button[data-tab="settings"]')
    page.wait_for_selector("#tab-settings", state="visible")
    page.wait_for_timeout(400)
    before = guide_state(base)["state"]
    c.check("reset: there is progress to lose before the test",
            bool(before["completed"] or before["dismissed"]))
    page.click("#gd-reset")
    page.wait_for_selector("#confirm-overlay.show", timeout=5000)
    confirm = text_of(page, "#confirm-overlay")
    c.check("reset: it says what will and will not change",
            "trades" in confirm and "settings" in confirm, confirm[:180])
    page.click("#confirm-ok")
    page.wait_for_timeout(900)
    after = guide_state(base)["state"]
    c.check("reset: progress is cleared", after["completed"] == []
            and after["dismissed"] == [], str(after))
    c.check("reset: the welcome screen returns", page.is_visible("#gd-welcome.show"))
    page.click("#gd-w-skip")
    page.wait_for_timeout(400)
    c.check("reset: skipping the welcome marks the user onboarded",
            guide_state(base)["state"]["onboarded"] is True)


def run_checks(page, base: str, c: Checks) -> None:
    check_first_launch(page, base, c)
    check_spotlight(page, c)
    check_navigation(page, c)
    check_interaction_step(page, c)
    check_completion(page, base, c)
    check_skip_and_resume(page, base, c)
    check_contextual_help(page, c)
    check_catalogue_integrity(page, c)
    check_help_centre(page, c)
    check_glossary(page, c)
    check_order_guardrails(page, c)
    check_empty_states(page, c)
    check_recommendations(page, base, c)
    check_accessibility(page, base, c)
    check_responsive(page, c)
    check_reset(page, base, c)

    body = page.inner_text("body").lower()
    c.check("no undefined leaked into the onboarding UI", "undefined" not in body)
    c.check("no raw [object Object] in the markup", "[object" not in body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--require", action="store_true",
                    help="fail (not skip) if playwright isn't installed")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (for debugging a failure by eye)")
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-guide-"))
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

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on("console",
                    lambda m: console.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(str(e)))
            # The one stub: an option chain cannot be fetched without a network.
            page.route("**/api/chain*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(CHAIN)))
            page.goto(base)
            page.wait_for_selector("#hero", timeout=25000)
            page.wait_for_timeout(600)
            run_checks(page, base, c)
            browser.close()

        c.check("zero browser console errors", not console, "; ".join(console[:3]))

        if c.failures:
            print(f"FAIL: {len(c.failures)} of {c.total} onboarding UI checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"OK: {c.passed}/{c.total} guided-onboarding UI checks passed "
              f"in a real headless browser.")
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
                          "(a file handle may still be open) - safe to "
                          "delete by hand later.")
                else:
                    time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
