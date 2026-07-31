"""Market Data Control Centre regression check, in a real headless browser.

`tests/test_marketdata_endpoints.py` proves the API is correct. This proves the
*page* is — that a user clicking the buttons gets the behaviour the API
promises, which is a different question and the one this repo has historically
been weakest on (`CLAUDE.md`: "static/index.html has no automated test
coverage" was true of every settings screen before this file existed).

What it drives, all through the real UI with real clicks:

  the panel renders · provider cards carry a state AND an explanation ·
  configure a key · the key is masked on screen and absent from the DOM ·
  remove a key · enable / disable a provider · reorder with Move Up ·
  reset the order · switch ordering mode · Test Connection on an unconfigured
  provider · the live dashboard table · maintenance actions run to completion
  with progress · cache rebuild · quota display · recommendations · the QA
  panel is absent without QA mode · auto-refresh does not eat a half-typed key ·
  keyboard reachability

**Everything runs offline.** The one action that would touch the network
(Test Connection on a configured provider) is driven against a provider with
no key, which the backend answers without making a request. Nothing here needs
a live market or a real API key.

Uses Playwright driving the system's installed Edge (channel="msedge" — no
browser download), matching scripts/browser_check.py and scripts/chart_check.py.
Soft-skips (exit 0) if Playwright isn't installed; pass --require to make a
missing install a hard failure. Never touches the real data directory.
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

#: A key that is obviously fake, long enough to be masked rather than blanked,
#: and distinctive enough that finding it anywhere in the DOM is unambiguous.
FAKE_KEY = "op_browsercheck_9f3a2b7c4d1e"


def wait_for(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001 - just means "not up yet"
            time.sleep(0.3)
    return False



def skip_onboarding(root) -> None:
    """Mark the first-launch tour as already seen in a scratch profile.

    V0.6.1 shows a welcome dialog on a profile that has never been onboarded,
    and every check script here starts from exactly that state — so without
    this they would each spend their run fighting a modal that is not their
    subject. `scripts/guide_check.py` is where the welcome flow is tested.
    """
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    settings = data / "settings.json"
    doc = {}
    if settings.exists():
        try:
            doc = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    doc["guide"] = {"onboarded": True, "completed": [], "dismissed": [],
                    "features": {}, "reduce_motion": False, "tips": True,
                    "version": 1}
    settings.write_text(json.dumps(doc, indent=2), encoding="utf-8")

def main() -> int:  # noqa: C901 — a flat sequence of independent checks reads clearest
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--require", action="store_true",
                    help="fail (not skip) if playwright isn't installed")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = ('playwright not installed - run `pip install -e ".[dev,browser]"` '
               "to enable this check.")
        if args.require:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-md-"))
    skip_onboarding(scratch)
    base = f"http://127.0.0.1:{args.port}"
    # OPTIONSPILOT_HOME, not just cwd: the storage root moved off the CWD in
    # V0.4.4, so `cwd=scratch` alone would let this run against the user's real
    # credentials.json — and this check WRITES A KEY. Isolating it is not a
    # tidiness point here, it is the difference between a test and an accident.
    server = subprocess.Popen(
        [sys.executable, "-m", "optionspilot", "--config", str(ROOT / "config.yaml"),
         "serve", "--port", str(args.port), "--no-loop"],
        cwd=scratch, env={**os.environ, "OPTIONSPILOT_HOME": str(scratch)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + label)
        if not cond:
            failures.append(label)

    try:
        if not wait_for(base + "/api/status"):
            print("FAIL: dev server did not come up in time")
            return 1

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.on("console",
                    lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(base)
            page.wait_for_selector("#hero", timeout=20000)
            page.click('nav button[data-tab="settings"]')
            page.wait_for_selector("#md-panel", state="visible", timeout=10000)

            def cards() -> int:
                return page.locator("#md-providers .md-card").count()

            def card(name: str):
                return page.locator(f'.md-card:has(.name:text-is("{name}"))')

            def wait_render(timeout: int = 10000) -> None:
                page.wait_for_function(
                    "() => document.querySelectorAll('#md-providers .md-card')"
                    ".length > 0", timeout=timeout)

            # ── 1. the panel renders every provider ──────────────────────────
            wait_render()
            check(cards() >= 6, f"every provider is listed ({cards()} cards)")

            # ── 2. one state AND one explanation per provider ────────────────
            # A coloured badge that says "degraded" and nothing else tells a
            # user they have a problem without telling them what it is.
            pairs = page.evaluate("""() =>
                [...document.querySelectorAll('#md-providers .md-card')].map(c => ({
                  badge: (c.querySelector('.md-badge')||{}).textContent || '',
                  why: (c.querySelector('.md-why')||{}).textContent || '',
                }))""")
            check(all(p["badge"].strip() and len(p["why"].strip()) > 20
                      for p in pairs),
                  "each provider shows a state AND a plain-English explanation")

            # ── 3. a keyed provider with no key says where to get one ────────
            finnhub = card("finnhub")
            check("API key missing" in finnhub.locator(".md-badge").inner_text(),
                  "an unconfigured keyed provider reports a missing key")
            check(finnhub.locator('a[href^="https://"]').count() >= 1,
                  "…and links to where a free key can be obtained")

            # ── 4. the failover summary is present ───────────────────────────
            # `inner_text()` applies CSS, and these labels are uppercased by a
            # `text-transform` — so the comparison is case-insensitive rather
            # than asserting a presentation choice the stylesheet owns.
            summary = page.locator("#md-summary").inner_text().lower()
            check("serving now" in summary and "independent sources" in summary,
                  "the failover summary says what would happen if one fails")

            # ── 5. configure an API key through the real form ────────────────
            finnhub.locator("input[data-key-for]").fill(FAKE_KEY)
            finnhub.locator("[data-save-key]").click()
            page.wait_for_function(
                """() => {
                     const c = [...document.querySelectorAll('.md-card')]
                       .find(x => x.querySelector('.name').textContent === 'finnhub');
                     return c && !c.querySelector('.md-badge').textContent
                                  .includes('API key missing');
                   }""", timeout=10000)
            check(True, "an API key can be pasted and saved from the page")

            # ── 6. the key is MASKED on screen and absent from the DOM ───────
            shown = card("finnhub").locator(".stored").inner_text()
            check(shown.endswith(FAKE_KEY[-4:]) and FAKE_KEY not in shown,
                  f"the stored key is shown masked ({shown})")
            check(FAKE_KEY not in page.content(),
                  "the plaintext key appears NOWHERE in the rendered document")

            # ── 7. …nor in anything a user is invited to export ──────────────
            leaked = page.evaluate("""async (key) => {
                 const urls = ['/api/marketdata', '/api/diagnostics/marketdata',
                   '/api/diagnostics/marketdata/export?format=json',
                   '/api/diagnostics/marketdata/export?format=text',
                   '/api/config'];
                 for (const u of urls) {
                   const t = await (await fetch(u)).text();
                   if (t.includes(key)) return u;
                 }
                 return '';
               }""", FAKE_KEY)
            check(leaked == "",
                  "the key is absent from every diagnostics/export payload"
                  + (f" (LEAKED in {leaked})" if leaked else ""))

            # ── 8. Test Connection reports a result without a network call ───
            card("twelvedata").locator("[data-test]").click()
            page.wait_for_selector('.md-card:has(.name:text-is("twelvedata")) '
                                   ".md-result", timeout=15000)
            result = card("twelvedata").locator(".md-result").inner_text()
            check("No API key is configured" in result,
                  "Test Connection reports a specific outcome…")
            check("Paste a key" in result,
                  "…together with what to do about it")

            # ── 9. remove the key ────────────────────────────────────────────
            card("finnhub").locator("[data-remove-key]").click()
            page.wait_for_function(
                """() => {
                     const c = [...document.querySelectorAll('.md-card')]
                       .find(x => x.querySelector('.name').textContent === 'finnhub');
                     return c && c.querySelector('.md-badge').textContent
                                  .includes('API key missing');
                   }""", timeout=10000)
            check(True, "an API key can be removed from the page")

            # ── 10. disable a provider ───────────────────────────────────────
            # Clicked on the LABEL, which is what a user clicks: the checkbox
            # itself is visually hidden behind the switch graphic (the standard
            # accessible pattern — the input still owns focus and the
            # screen-reader name). Driving the input directly would test a
            # target no human can hit.
            card("stooq").locator(".md-switch").click()
            page.wait_for_function(
                """() => {
                     const c = [...document.querySelectorAll('.md-card')]
                       .find(x => x.querySelector('.name').textContent === 'stooq');
                     return c && c.querySelector('.md-badge').textContent
                                  .includes('Disabled');
                   }""", timeout=10000)
            check(True, "a provider can be switched off from the page")
            check(cards() >= 6,
                  "…and stays listed so it can be switched back on")

            # ── 11. re-enable it ─────────────────────────────────────────────
            card("stooq").locator(".md-switch").click()
            page.wait_for_function(
                """() => {
                     const c = [...document.querySelectorAll('.md-card')]
                       .find(x => x.querySelector('.name').textContent === 'stooq');
                     return c && !c.querySelector('.md-badge').textContent
                                   .includes('Disabled');
                   }""", timeout=10000)
            check(True, "…and switched back on without a restart")

            # ── 12. reorder with Move Up ─────────────────────────────────────
            first_before = page.locator("#md-providers .md-card .name") \
                               .first.inner_text()
            second_before = page.locator("#md-providers .md-card .name") \
                                .nth(1).inner_text()
            card(second_before).locator('[data-move="down"]').wait_for()
            card(second_before).locator('[data-move="up"]').click()
            page.wait_for_function(
                "(want) => document.querySelector('#md-providers .md-card .name')"
                ".textContent === want", arg=second_before, timeout=10000)
            check(page.locator("#md-providers .md-card .name").first.inner_text()
                  == second_before,
                  f"Move Up reorders the chain ({second_before} above "
                  f"{first_before})")

            # ── 13. the new order actually reaches the backend ───────────────
            backend_order = page.evaluate(
                "async () => (await (await fetch('/api/marketdata')).json()).order")
            check(backend_order[0] == second_before,
                  "…and the backend agrees about the new order")

            # ── 14. reset to default ─────────────────────────────────────────
            page.click("#md-reset-order")
            page.wait_for_function(
                "(want) => document.querySelector('#md-providers .md-card .name')"
                ".textContent === want", arg=first_before, timeout=10000)
            check(True, "Reset to default order restores the shipped chain")

            # ── 15. ordering modes ───────────────────────────────────────────
            modes = page.locator("#md-mode button")
            check(modes.count() == 3, "three ordering modes are offered")
            page.locator('#md-mode button[data-mode="hybrid"]').click()
            page.wait_for_function(
                """() => document.querySelector('#md-mode button[data-mode=hybrid]')
                     .getAttribute('aria-pressed') === 'true'""", timeout=10000)
            explanation = page.locator("#md-mode-why").inner_text()
            check(len(explanation) > 40,
                  "…and the selected one is explained in plain English")
            mode = page.evaluate("async () => (await (await fetch("
                                 "'/api/marketdata')).json()).ordering_mode")
            check(mode == "hybrid", "…and the backend applied it")
            page.locator('#md-mode button[data-mode="dynamic"]').click()

            # ── 16. the live dashboard table ─────────────────────────────────
            page.click("#md-dash-sec > summary")
            page.wait_for_selector("#md-dashboard table", timeout=10000)
            headers = page.locator("#md-dashboard th").count()
            rows = page.locator("#md-dashboard tbody tr").count()
            check(headers >= 15 and rows >= 6,
                  f"the live dashboard renders {rows} providers x {headers} columns")

            # ── 17. maintenance: verify cache runs to completion ─────────────
            page.click("#md-tools-sec > summary")
            page.wait_for_selector('#md-tools [data-action="verify_cache"]',
                                   timeout=10000)
            page.click('#md-tools [data-action="verify_cache"]')
            page.wait_for_function(
                """() => {
                     const j = document.querySelector('#md-job');
                     return j && /sound|unusable/.test(j.textContent);
                   }""", timeout=25000)
            check(True, "a maintenance action runs and reports a summary")
            check(page.locator("#md-job .md-bar > span").count() == 1,
                  "…with a progress bar")

            # ── 18. maintenance: clear cache ─────────────────────────────────
            page.click('#md-tools [data-action="clear_cache"]')
            page.wait_for_function(
                """() => (document.querySelector('#md-job')||{}).textContent
                          ?.includes('cached bars')""", timeout=25000)
            check(True, "Clear chart cache reports what it removed")

            # ── 19. maintenance: rebuild cache ───────────────────────────────
            page.click('#md-tools [data-action="rebuild_cache"]')
            page.wait_for_function(
                """() => /Rebuilt|no local cache/.test(
                     (document.querySelector('#md-job')||{}).textContent || '')""",
                timeout=25000)
            check(True, "Rebuild cache completes and explains what it did")

            # ── 20. every declared tool has a button and a description ───────
            tools = page.evaluate("""() =>
                [...document.querySelectorAll('#md-tools [data-action]')].map(b => ({
                  action: b.dataset.action,
                  title: (b.querySelector('.t')||{}).textContent || '',
                  desc: (b.querySelector('.d')||{}).textContent || '',
                }))""")
            check(len(tools) == 8, f"all 8 maintenance tools are offered "
                                   f"({len(tools)} found)")
            check(all(t["title"] and len(t["desc"]) > 30 for t in tools),
                  "…each with a label and an explanation of what it does")
            check(any("Uses live provider requests" in
                      page.locator(f'#md-tools [data-action="{t["action"]}"]')
                          .inner_text()
                      for t in tools),
                  "…and the ones that spend requests say so before you click")

            # ── 21. quota display for a metered provider ─────────────────────
            page.evaluate("""async () => {
                 await fetch('/api/marketdata/providers/alphavantage/key',
                   {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({api_key: 'quota_display_probe_key'})});
               }""")
            page.click("#md-refresh")
            page.wait_for_function(
                """() => {
                     const c = [...document.querySelectorAll('.md-card')]
                       .find(x => x.querySelector('.name').textContent === 'alphavantage');
                     return c && c.textContent.includes('requests today');
                   }""", timeout=10000)
            budget = card("alphavantage").inner_text()
            check("25" in budget,
                  "a metered provider shows its real budget (25/day)")
            check(card("alphavantage").locator(".md-meter").count() == 1,
                  "…with a meter beside the numbers, never instead of them")
            page.evaluate("""async () => { await fetch(
                 '/api/marketdata/providers/alphavantage/key', {method:'DELETE'}); }""")

            # ── 22. recommendations appear when they should ──────────────────
            page.evaluate("""async () => {
                 for (const p of ['stooq', 'yfinance']) {
                   await fetch(`/api/marketdata/providers/${p}/enabled`,
                     {method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({enabled: false})});
                 }
               }""")
            page.click("#md-refresh")
            page.wait_for_selector("#md-recs .md-rec", timeout=10000)
            rec = page.locator("#md-recs .md-rec").first.inner_text()
            check("independent" in rec.lower(),
                  "a single-source install is warned and told what to add")
            page.evaluate("""async () => {
                 for (const p of ['stooq', 'yfinance']) {
                   await fetch(`/api/marketdata/providers/${p}/enabled`,
                     {method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({enabled: true})});
                 }
               }""")

            # ── 23. the QA panel is absent in a normal build ─────────────────
            check(page.locator("#md-qa .md-qa").count() == 0,
                  "the developer QA panel is not rendered without qa_mode")
            # Issued through Playwright's request context rather than an in-page
            # `fetch`: the 404 is the CORRECT answer here, but a browser logs
            # every failed fetch as a console error, and this suite treats a
            # console error as a failure. Asking from outside the page keeps
            # that signal meaningful instead of teaching the run to ignore 404s.
            qa_status = page.request.get(base + "/api/marketdata/qa").status
            check(qa_status == 404, "…and its endpoint 404s")

            # ── 24. auto-refresh must not eat a half-typed key ───────────────
            # The panel polls every few seconds. A re-render that wiped the key
            # box mid-paste would be worse than having no auto-refresh at all.
            page.click("#md-refresh")
            page.wait_for_timeout(300)
            typed = "half_typed_key_being_entered"
            card("finnhub").locator("input[data-key-for]").fill(typed)
            page.evaluate("() => MarketData.refresh()")
            page.wait_for_timeout(600)
            page.evaluate("() => MarketData.refresh()")
            page.wait_for_timeout(600)
            still = card("finnhub").locator("input[data-key-for]").input_value()
            check(still == typed,
                  "a half-typed API key survives an auto-refresh")
            card("finnhub").locator("input[data-key-for]").fill("")

            # ── 25. accessibility: the controls are reachable and labelled ───
            labels = page.evaluate("""() => {
                 const bad = [];
                 document.querySelectorAll('#md-panel button, #md-panel input, '
                   + '#md-panel select').forEach(el => {
                   const name = el.getAttribute('aria-label') ||
                     el.textContent.trim() ||
                     (el.labels && el.labels.length ? el.labels[0].textContent : '');
                   if (!name) bad.push(el.outerHTML.slice(0, 70));
                 });
                 return bad;
               }""")
            check(labels == [],
                  "every control has an accessible name"
                  + (f" (missing: {labels[:2]})" if labels else ""))
            switches = page.evaluate(
                """() => [...document.querySelectorAll('#md-panel .md-switch input')]
                     .every(i => i.type === 'checkbox')""")
            check(switches,
                  "the on/off switches are real checkboxes (keyboard + SR)")
            table_headers = page.evaluate(
                """() => [...document.querySelectorAll('#md-dashboard th')]
                     .every(th => th.getAttribute('scope') === 'col')""")
            check(table_headers, "the dashboard table has scoped column headers")
            live = page.evaluate(
                """() => (document.querySelector('#md-status')||{})
                     .getAttribute?.('aria-live')""")
            check(live == "polite",
                  "action results are announced through a live region")

            # ── 26. the education section is present and substantial ─────────
            # Opened first: it is a collapsed `<details>`, and `inner_text()`
            # reports only what is actually rendered — so reading it closed
            # would assert on an empty string and pass for the wrong reason.
            page.evaluate(
                """() => [...document.querySelectorAll('#md-panel details')]
                     .forEach(d => d.open = true)""")
            page.wait_for_selector(".md-learn h4", state="visible", timeout=5000)
            learn = page.locator(".md-learn").inner_text()
            for topic in ("Why there is more than one provider",
                          "What happens when one fails",
                          "Why some providers need an API key",
                          "Why Yahoo is first", "Unlimited", "Delayed"):
                check(topic in learn, f"the explainer covers: {topic}")

            # ── 27. polling stops when the panel is off screen ───────────────
            # A settings page that keeps fetching from a background tab becomes
            # a meaningful share of the traffic in the system it reports on.
            page.click('nav button[data-tab="dashboard"]')
            page.wait_for_timeout(200)
            before = page.evaluate("""async () => {
                 window.__mdCount = 0;
                 const orig = window.fetch;
                 window.fetch = (...a) => {
                   if (String(a[0]).startsWith('/api/marketdata')) window.__mdCount++;
                   return orig(...a);
                 };
                 return 0;
               }""")
            page.wait_for_timeout(6500)
            polled = page.evaluate("() => window.__mdCount")
            check(polled == 0,
                  f"the panel stops polling when its tab is not visible "
                  f"({polled} request(s) in 6.5s)")

            # ── 28. settings survive a page reload ───────────────────────────
            page.evaluate("""async () => {
                 await fetch('/api/marketdata/ordering_mode',
                   {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({mode: 'static'})});
               }""")
            page.reload()
            page.wait_for_selector("#hero", timeout=20000)
            page.click('nav button[data-tab="settings"]')
            wait_render()
            page.wait_for_function(
                """() => document.querySelector('#md-mode button[data-mode=static]')
                     ?.getAttribute('aria-pressed') === 'true'""", timeout=10000)
            check(True, "a changed setting survives a page reload")

            browser.close()

        if errors:
            real = [e for e in errors if "favicon" not in e]
            for e in real:
                print(f"  FAIL console error: {e}")
            if real:
                failures.append(f"{len(real)} console error(s)")

        if failures:
            print(f"\nFAIL: {len(failures)} market-data control check(s) failed.")
            return 1
        print("\nOK: all Market Data Control Centre checks passed in a real "
              "headless browser.")
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
                          "(a file handle may still be open) - safe to delete "
                          "by hand.")
                else:
                    time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
