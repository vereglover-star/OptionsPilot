"""Navigation helper shared by every headless-browser suite (UI V2 M2-C9).

Six suites drove the nine-tab navigation with `nav button[data-tab="X"]`, and
all of those references break the moment the shell is on. Rather than editing
each reference, each suite calls `goto(page, "charts")` and this file decides
what that means in whichever shell is running.

**One helper, not one per suite.** `ROADMAP-UI-V2.md` §5.2 proposes a selector
helper *per file*; six copies of one rule is six places for it to rot, and the
rule is identical everywhere. A single module keeps the suites drivable against
BOTH navigations for as long as the flag exists, which is what makes the
rollback testable rather than merely available.

**It clicks real controls.** It would be shorter to call `Shell.goTo` through
`page.evaluate`, and it would also stop testing the navigation — a rail that
rendered nothing would still "work". So this clicks the rail item, then the
section tab, exactly as a user would.

The destination for a section is read from the application's own
`DESTINATIONS` registry rather than restated here. A second copy of that
mapping in test code is the drift this project keeps paying for; when M3-M6
move a section between destinations, these suites follow with no edit.
"""
from __future__ import annotations

#: How long to wait for a destination to appear before giving up. Generous
#: because the first navigation of a run can land while the chart is still
#: fetching.
TIMEOUT = 20000


def shell_on(page) -> bool:
    """Is the UI V2 shell rendering? Cheap and synchronous."""
    return bool(page.evaluate(
        "() => !!document.body.classList.contains('shell-v2')"))


def ready(page, timeout: int = 25000) -> None:
    """Block until whichever navigation is in play is on screen.

    Suites used to wait for `nav button[data-tab="charts"]` as a proxy for
    "the page has booted". That selector is invisible under the shell, so the
    wait is expressed against the thing that is always true instead: one of
    the two navigations exists.
    """
    page.wait_for_selector(
        '#shell-rail:not([hidden]), nav[aria-label="Main"] button[data-tab]',
        timeout=timeout)


def home_ready(page, timeout: int = 25000) -> None:
    """Block until the landing destination has rendered something.

    Five suites used `#hero` for this — the legacy dashboard's top block —
    which M3-C5 moved inside `#home-legacy` and hid under `body.shell-v2`.
    All five broke at once, which is the same shape of failure M2-C9 fixed by
    replacing 53 `data-tab` literals with this module: a selector that means
    "the app has booted" belongs in one place, or the next milestone that
    touches the landing page breaks every suite again.

    Expressed as "either Home is up" so it holds on both sides of the flag,
    exactly like `ready()` above, and so M3-C10's deletion of `#hero` is not a
    sixth simultaneous breakage.
    """
    page.wait_for_selector("#home-metrics, #hero", timeout=timeout)


def destination_of(page, tab: str) -> str:
    """Which destination hosts a legacy section, per the app's own registry."""
    return page.evaluate(
        "(tab) => (window.Shell && Shell.destinationFor(tab).id) || ''", tab)


def goto(page, tab: str, *, timeout: int = TIMEOUT) -> None:
    """Show the legacy section `tab`, through whichever navigation is on.

    Under the shell that means clicking the destination in the rail and then,
    where the destination hosts more than one section, the section tab. Under
    the legacy navigation it is the old button. Either way the section named is
    the section showing, which is the only thing a caller cares about.
    """
    if not shell_on(page):
        page.click(f'nav[aria-label="Main"] button[data-tab="{tab}"]')
        page.wait_for_selector(f"#tab-{tab}", state="visible", timeout=timeout)
        return

    dest = destination_of(page, tab)
    if not dest:
        raise AssertionError(
            f"no destination hosts the section {tab!r} — the DESTINATIONS "
            f"registry and this suite disagree, which is the drift M2-C9 "
            f"exists to prevent")
    page.click(f'#shell-rail a[data-dest="{dest}"]')
    page.wait_for_timeout(250)
    # A destination hosting several sections lands on its first one; the
    # section rail is how a user reaches the others, so it is how we do too.
    section = page.query_selector(f'.dest-sections button[data-section="{tab}"]')
    if section:
        section.click()
    page.wait_for_selector(f"#tab-{tab}", state="visible", timeout=timeout)
    page.wait_for_timeout(150)


def visible(page, tab: str) -> bool:
    """Is that section's control reachable? Used by the smoke check."""
    if shell_on(page):
        dest = destination_of(page, tab)
        return bool(dest) and page.is_visible(f'#shell-rail a[data-dest="{dest}"]')
    return page.is_visible(f'nav[aria-label="Main"] button[data-tab="{tab}"]')
