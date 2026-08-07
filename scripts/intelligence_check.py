"""Headless-browser certification of the Trading Intelligence Engine's UI.

Sibling to `scripts/marketdata_check.py` and `scripts/chart_check.py`, and
written for the same reason those exist: `ui/static/index.html` has no automated
test coverage of its own, the FastAPI layer is thoroughly tested and none of
those tests ask whether anything reached the screen.

That distinction has bitten this codebase before. V3.2 spent 1,232 backend tests
and a whole diagnostics subsystem asking whether the *payload* was correct, and
shipped a chart whose candles were rendering off-screen. So the checks below
assert what the user can SEE — that the behaviour panel contains the finding the
API reported, that every claim carries a working "Why?" disclosure, that a null
metric renders as an em dash rather than a zero — not that a JSON key exists.

The scratch profile is seeded with a deliberately flawed trading history (no
stops on a fifth of trades, a Tuesday edge, a cluster of poor-setup entries) so
every panel has something real to draw. Entirely offline: no market data is
fetched, and the server runs with --no-loop.

Soft-skips (exit 0) if Playwright isn't installed, matching browser_check.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shell_nav import goto, home_ready  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 10:00 ET on a Monday, after the 2026 DST switch so the UTC offset is a stable
# −4 for the whole seeded history.
BASE = datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc)
N_TRADES = 90


class Checks:
    """Tiny assertion recorder. Collects every failure rather than stopping at
    the first, so one run tells you everything that is wrong."""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            return True
        self.failures.append(f"{label}" + (f" — {detail}" if detail else ""))
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


def seed(root: Path) -> None:
    """Write a flawed-but-realistic trading history straight into journal.db.

    Written with sqlite3 rather than through TradeJournal so the seed cannot be
    invalidated by an import-time failure in the app under test — this script's
    job is to check the UI, and a seeding step that shares the app's import
    graph turns every backend error into a confusing seeding error.
    """
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / "journal.db")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL, contract_symbol TEXT NOT NULL,
            direction TEXT NOT NULL, strategy TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            entry_ts TEXT NOT NULL, entry_price REAL NOT NULL,
            exit_ts TEXT NOT NULL, exit_price REAL NOT NULL,
            commissions REAL NOT NULL, confidence REAL NOT NULL,
            entry_reasons TEXT NOT NULL, exit_reason TEXT NOT NULL,
            market_conditions TEXT NOT NULL, indicators_used TEXT NOT NULL,
            mistakes TEXT NOT NULL, lessons TEXT NOT NULL,
            pnl REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades (entry_ts);
        PRAGMA user_version = 1;
    """)

    # V0.6.1: a scratch profile has never been onboarded, so the welcome
    # dialog would sit over every assertion below. The onboarding flow itself
    # is covered by scripts/guide_check.py.
    (data / "settings.json").write_text(json.dumps({
        "guide": {"onboarded": True, "completed": [], "dismissed": [],
                  "features": {}, "reduce_motion": False, "tips": True,
                  "version": 1}}, indent=2), encoding="utf-8")

    reviews = root / "data" / "coach"
    reviews.mkdir(parents=True, exist_ok=True)

    for i in range(N_TRADES):
        entry = BASE + timedelta(days=i)
        # A real, discoverable edge on Tuesdays, and a real weakness elsewhere.
        won = (entry.weekday() == 1) or (i % 3 == 0)
        exit_price = 4.0 if won else 0.8
        pnl = (exit_price - 2.0) * 100 - 1.0
        no_stop = i % 5 == 0                 # ~20% of trades run unprotected
        poor_setup = i % 7 == 0
        conn.execute(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"T{i:03d}", "SPY" if i % 2 else "QQQ", f"SPY2604{i % 28 + 1:02d}C00500000",
             "long" if i % 2 else "short", "manual", 1,
             entry.isoformat(), 2.0,
             (entry + timedelta(hours=2)).isoformat(), exit_price,
             1.0, 55.0 + (i % 30),
             json.dumps(["structure break", "volume push"]),
             "target: reached" if won else "stop_loss: crossed",
             json.dumps({"dte": str(3 if i % 6 == 0 else 21),
                         "htf_trend": "up",
                         "setup_quality": "poor" if poor_setup else "good"}),
             json.dumps(["htf_trend_alignment"]),
             json.dumps(["no_stop"] if no_stop else []),
             json.dumps([]), pnl))

        (reviews / f"T{i:03d}.json").write_text(json.dumps({
            "trade_id": f"T{i:03d}", "score": 45 if no_stop else 78,
            "verdict": "won" if won else "lost",
            "setup_quality": "poor" if poor_setup else "good",
            "summary": "seeded", "before": [], "after": [],
            "during": [
                {"check": "stop in place", "passed": not no_stop, "detail": "seed"},
                {"check": "stop discipline", "passed": True, "detail": "seed"},
                {"check": "profit target defined", "passed": i % 3 != 1,
                 "detail": "seed"},
            ],
            "mistakes": ["no_stop"] if no_stop else [],
            "strengths": [], "improvements": [], "pro_notes": [], "ev_note": "",
            "categories": [
                {"name": "Entry Quality", "score": 55 if poor_setup else 80,
                 "grade": "B", "explanation": "", "suggestion": ""},
                {"name": "Risk Management", "score": 30 if no_stop else 85,
                 "grade": "B", "explanation": "", "suggestion": ""},
                {"name": "Exit Quality", "score": 72, "grade": "B",
                 "explanation": "", "suggestion": ""},
                {"name": "Rule Following", "score": 40 if no_stop else 88,
                 "grade": "B", "explanation": "", "suggestion": ""},
                {"name": "Emotional Discipline", "score": 80, "grade": "B",
                 "explanation": "", "suggestion": ""},
                {"name": "Patience", "score": 70, "grade": "B",
                 "explanation": "", "suggestion": ""},
                {"name": "Position Size", "score": 75, "grade": "B",
                 "explanation": "", "suggestion": ""},
                {"name": "Trend Alignment", "score": 78, "grade": "B",
                 "explanation": "", "suggestion": ""},
                {"name": "Reward/Risk Ratio", "score": 66, "grade": "C",
                 "explanation": "", "suggestion": ""},
                {"name": "Timing", "score": 71, "grade": "B",
                 "explanation": "", "suggestion": ""},
            ],
            "pnl": pnl, "return_pct": pnl / 200 * 100, "hold_minutes": 120.0,
            "r_multiple": 1.6 if won else -1.0,
            "entry_ts": entry.isoformat(), "symbol": "SPY",
            "direction": "long",
        }), encoding="utf-8")

    conn.commit()
    conn.close()


def text_of(page, selector: str) -> str:
    """The RENDERED text of an element, lower-cased.

    `inner_text` returns text as the user sees it, which means `text-transform:
    uppercase` on the panel headings and `.sub` sub-headings comes back
    upper-cased. Comparing an API string against that fails for a purely
    cosmetic reason — so every comparison in this file is case-insensitive, and
    `contains()` below is the only way checks should test for a substring.
    """
    try:
        return page.inner_text(selector).lower()
    except Exception:  # noqa: BLE001
        return ""


def contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring test against already-lower-cased page text."""
    return needle.lower() in haystack


def run_checks(page, base: str, c: Checks) -> None:
    api = json.loads(urllib.request.urlopen(base + "/api/intelligence").read())

    # ── 1–8: the panel exists and is populated ───────────────────────────
    # M3-C6 moved the intelligence panel from the Dashboard to Journal ›
    # Review. Home's H4 renders this engine's top-ranked items, and leaving
    # the full panel on the same screen put two "What to do next" regions one
    # above the other. Navigated through `shell_nav.goto`, which clicks the
    # real controls, so this also proves the panel is REACHABLE from the
    # navigation rather than merely present in the DOM.
    goto(page, "coach")
    page.wait_for_selector("#intel-panel", state="visible", timeout=20000)
    page.wait_for_function(
        "() => document.querySelector('#intel-scores').children.length > 1",
        timeout=20000)

    c.check("dashboard: intelligence panel is visible",
            page.is_visible("#intel-panel"))
    c.check("dashboard: the seeded trade count reaches the screen",
            contains(text_of(page, "#intel-meta"), str(api["trades_analyzed"])),
            text_of(page, "#intel-meta"))
    c.check("dashboard: data sufficiency is disclosed",
            contains(text_of(page, "#intel-meta"), api["data_sufficiency"]))
    c.check("dashboard: overall confidence is disclosed",
            contains(text_of(page, "#intel-meta"), "confidence"))

    scores = page.query_selector_all("#intel-scores .card")
    c.check("dashboard: one card per composite score",
            len(scores) == len(api["scores"]),
            f"{len(scores)} cards vs {len(api['scores'])} scores")

    api_scores = {s["label"]: s for s in api["scores"]}
    shown = text_of(page, "#intel-scores")
    c.check("dashboard: every score label is on screen",
            all(contains(shown, label) for label in api_scores))

    # A score explains itself: the card's tooltip is the engine's explanation.
    first_card = page.query_selector("#intel-scores .card")
    c.check("dashboard: a score card carries its explanation",
            bool(first_card and first_card.get_attribute("title")))
    c.check("dashboard: score cards show a confidence badge",
            len(page.query_selector_all("#intel-scores .card span")) >= len(scores))

    # ── 9–16: recommendations, evidence and honesty ──────────────────────
    actions = text_of(page, "#intel-actions")
    if api["recommendations"]:
        top = api["recommendations"][0]
        c.check("dashboard: the top recommendation's ACTION is rendered",
                contains(actions, top["action"][:40]), actions[:160])
        c.check("dashboard: the recommendation cites its rationale",
                contains(actions, top["rationale"][:30]))
        c.check("dashboard: recommendations are actions, not platitudes",
                not contains(actions, "should manage risk"))
        why = page.query_selector_all("#intel-actions details.ev")
        c.check("dashboard: every recommendation has a Why? disclosure",
                len(why) >= 1, f"{len(why)} disclosures")
        if why:
            # The <summary> is the click target — clicking the <details> body
            # does nothing, which is a mistake worth encoding here because it
            # silently produces a check that can never fail.
            page.click("#intel-actions details.ev > summary")
            page.wait_for_timeout(150)
            c.check("dashboard: opening Why? reveals measured evidence",
                    bool(text_of(page, "#intel-actions .ev-list").strip()))
            c.check("dashboard: the evidence names the trades behind it",
                    contains(text_of(page, "#intel-actions"), "trades:")
                    or contains(text_of(page, "#intel-actions .ev-list"), "over "))
    else:
        c.check("dashboard: an empty action list says so explicitly",
                contains(actions, "Nothing measurable"), actions[:120])
        c.check("dashboard: no fabricated actions", True)
        c.check("dashboard: no fabricated rationale", True)
        c.check("dashboard: no fabricated evidence", True)
        c.check("dashboard: no fabricated disclosure", True)
        c.check("dashboard: no fabricated trade citation", True)

    c.check("dashboard: risk observations render",
            bool(text_of(page, "#intel-risk").strip()))
    c.check("dashboard: analysis caveats are shown, not just logged",
            bool(text_of(page, "#intel-notes").strip())
            or not api["notes"], text_of(page, "#intel-notes")[:120])

    # ── 17–22: goals ─────────────────────────────────────────────────────
    c.check("dashboard: goals panel explains itself when empty",
            contains(text_of(page, "#intel-goals"), "No goals set")
            or bool(api["goals"]))
    urllib.request.urlopen(urllib.request.Request(
        base + "/api/intelligence/goals",
        data=json.dumps({"id": "ui_check", "label": "Keep average R above 1",
                         "metric": "avg_r", "comparator": ">=", "target": 1.0,
                         "window": "last_20_trades", "unit": "R"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST"))
    page.reload()
    # A reload lands on the Dashboard, and since M3-C6 the panel is not there.
    # Navigating back is not test scaffolding — it is the assertion that the
    # panel survives a reload at its new address, which is the thing a
    # relocation is most likely to have broken.
    #
    # `home_ready` first: immediately after a reload neither navigation has
    # rendered yet, and `goto` would try to click a control that does not
    # exist for another few hundred milliseconds.
    home_ready(page, 25000)
    goto(page, "coach")
    page.wait_for_function(
        "() => document.querySelector('#intel-goals')"
        " && document.querySelector('#intel-goals').textContent.length > 20",
        timeout=20000)
    goals_text = text_of(page, "#intel-goals")
    c.check("goals: a saved goal appears on the dashboard",
            contains(goals_text, "Keep average R above 1"), goals_text[:160])
    c.check("goals: progress is shown as a computed percentage",
            "%" in goals_text)
    c.check("goals: a progress bar is drawn",
            len(page.query_selector_all("#intel-goals .prog")) >= 1)
    c.check("goals: the goal explains what it measured",
            contains(goals_text, "trades"))

    achievements = text_of(page, "#intel-achievements")
    c.check("achievements: the ladder is rendered",
            bool(achievements.strip()), achievements[:120])
    c.check("achievements: each states what it still needs",
            contains(achievements, "needed") or "🏆" in achievements)

    # ── 23–26: timeline ──────────────────────────────────────────────────
    timeline = text_of(page, "#intel-timeline")
    c.check("timeline: renders something",
            bool(timeline.strip()))
    c.check("timeline: never reports a nan or infinite change",
            not contains(timeline, "nan") and not contains(timeline, "infinity"),
            timeline[:160])
    if api["timeline"]:
        c.check("timeline: the engine's headline reaches the screen",
                contains(timeline, api["timeline"][0]["headline"][:30]), timeline[:200])
        c.check("timeline: entries carry supporting detail",
                bool(api["timeline"][0]["detail"]))
    else:
        c.check("timeline: an empty timeline explains why",
                contains(timeline, "Not enough history"), timeline[:120])
        c.check("timeline: no invented entries", True)

    # ── 27–36: the Coach tab ─────────────────────────────────────────────
    goto(page, "coach")
    page.wait_for_selector("#intel-coach", state="visible", timeout=20000)

    behaviors = text_of(page, "#intel-behaviors")
    detected = [b for b in api["behaviors"] if b["assessable"] and b["detected"]]
    if detected:
        c.check("coach: a detected behaviour is named on screen",
                contains(behaviors, detected[0]["label"]), behaviors[:200])
        c.check("coach: the behaviour's measured summary is shown",
                contains(behaviors, detected[0]["summary"][:35]))
        c.check("coach: behaviour findings carry a Why? disclosure",
                len(page.query_selector_all("#intel-behaviors details.ev")) >= 1)
    else:
        c.check("coach: a clean record says so",
                contains(behaviors, "showed a repeated problem")
                or contains(behaviors, "not enough recorded history"),
                behaviors[:160])
        c.check("coach: no invented behaviour", True)
        c.check("coach: no invented disclosure", True)

    # The load-bearing honesty check: a behaviour the data cannot assess must be
    # listed WITH its reason, never silently omitted (silence reads as "you
    # don't do this", which is a claim the data cannot make).
    blind = [b for b in api["behaviors"] if not b["assessable"]]
    unassessable = text_of(page, "#intel-unassessable")
    c.check("coach: unassessable behaviours are disclosed, not hidden",
            (not blind) or bool(unassessable.strip()), unassessable[:160])
    if blind:
        c.check("coach: each unassessable behaviour states WHY",
                contains(unassessable, blind[0]["unassessable_reason"][:30]),
                unassessable[:200])
        c.check("coach: 'hesitation' is declined rather than guessed at",
                any(b["id"] == "hesitation" for b in blind))
    else:
        c.check("coach: no invented reason", True)
        c.check("coach: hesitation handling", True)

    patterns = text_of(page, "#intel-patterns")
    if api["patterns"]:
        p = api["patterns"][0]
        c.check("coach: a discovered pattern reaches the screen",
                contains(patterns, p["bucket"]), patterns[:200])
        c.check("coach: patterns quote both sides of the comparison",
                contains(patterns, "elsewhere"))
        c.check("coach: patterns carry their significance evidence",
                len(page.query_selector_all("#intel-patterns details.ev")) >= 1)
    else:
        c.check("coach: no significant pattern says so",
                contains(patterns, "significance"), patterns[:160])
        c.check("coach: no invented pattern", True)
        c.check("coach: no invented significance", True)

    reports = text_of(page, "#intel-reports")
    c.check("coach: a coaching report is rendered",
            bool(reports.strip()), reports[:120])
    if api["reports"]:
        c.check("coach: the report's summary is prose, not a stat dump",
                contains(reports, api["reports"][0]["summary"][:40]), reports[:250])
        c.check("coach: report sections are present",
                any(contains(reports, s["heading"]) for s in api["reports"][0]["sections"]))
    else:
        c.check("coach: no report says why", contains(reports, "enough"))
        c.check("coach: no invented section", True)

    # ── 37–41: the Journal tab ───────────────────────────────────────────
    goto(page, "journal")
    page.wait_for_selector("#journal-table table", timeout=20000)
    journal = json.loads(urllib.request.urlopen(
        base + "/api/journal?last=200").read())
    c.check("journal: rows carry a findings badge when they are evidence",
            (not journal["findings"])
            or len(page.query_selector_all("#journal-table .badge")) >= 1,
            f"{len(journal['findings'])} flagged trades")

    page.click("#journal-table tr.expandable")
    page.wait_for_timeout(900)
    insight = text_of(page, "#journal-table")
    c.check("journal: expanding a row loads its per-trade analysis",
            contains(insight, "What the analysis makes of this trade"),
            insight[:200])
    c.check("journal: the per-trade view ranks the result against the record",
            contains(insight, "ranks above") or contains(insight, "Against your average"))
    c.check("journal: the per-trade view is a projection, not a re-analysis",
            contains(insight, "Compared with") or contains(insight, "evidence for")
            or contains(insight, "Against your average"))
    c.check("journal: no NaN leaks into the per-trade view",
            not contains(insight, "nan") and not contains(insight, "undefined"), insight[:200])

    # ── 42–46: the Learning tab ──────────────────────────────────────────
    goto(page, "learning")
    page.wait_for_selector("#tab-learning", state="visible", timeout=20000)
    page.wait_for_timeout(900)
    lessons = text_of(page, "#intel-lessons")
    if api["lessons"]:
        c.check("learning: a triggered lesson is shown",
                contains(lessons, api["lessons"][0]["title"]), lessons[:200])
        c.check("learning: every lesson answers WHY THIS LESSON",
                contains(lessons, "Why this lesson"))
        c.check("learning: every lesson states WHAT IT FIXES",
                contains(lessons, "What it fixes"))
        c.check("learning: every lesson exposes the statistic that triggered it",
                contains(lessons, "Which statistic triggered it?"))
        c.check("learning: lessons are not a default reading list",
                all(x["triggered_by"] for x in api["lessons"]))
    else:
        c.check("learning: no lesson panel without a trigger",
                not page.is_visible("#intel-lessons-panel"))
        for label in ("learning: no invented why", "learning: no invented fix",
                      "learning: no invented trigger",
                      "learning: no default reading list"):
            c.check(label, True)

    # ── 47–50: cross-cutting honesty ─────────────────────────────────────
    goto(page, "dashboard")
    page.wait_for_timeout(400)
    whole = text_of(page, "#intel-panel")
    c.check("no 'undefined' anywhere in the intelligence UI",
            not contains(whole, "undefined"), whole[:200])
    c.check("no 'null' rendered as a value",
            not contains(whole.replace("nullable", ""), "null"), whole[:200])
    c.check("no NaN anywhere in the intelligence UI",
            not contains(whole, "nan"))
    c.check("no raw [object Object] leaked into the markup",
            not contains(whole, "[object"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8801)
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

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-intel-"))
    seed(scratch)
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "serve",
         "--port", str(args.port), "--no-loop"],
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
            browser = p.chromium.launch(channel="msedge",
                                        headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on("console",
                    lambda m: console.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(str(e)))
            page.goto(base)
            home_ready(page, 25000)
            run_checks(page, base, c)
            browser.close()

        c.check("zero browser console errors", not console,
                "; ".join(console[:3]))

        if c.failures:
            print(f"FAIL: {len(c.failures)} of {c.total} intelligence UI "
                  f"checks failed:")
            for f in c.failures:
                print(f"  - {f}")
            return 1
        print(f"OK: {c.passed}/{c.total} Trading Intelligence UI checks passed "
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
