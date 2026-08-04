# ONBOARDING.md — the guided-onboarding, contextual-help and guardrail layer (V0.6.1)

Read this before changing anything under the "guided onboarding" banner in
`ui/static/index.html`, or `optionspilot/services/guide.py`. It is the design document
for V0.6.1, the same way `TRADING_INTELLIGENCE.md` is for V0.6.0 and
`MARKET_DATA.md` is for the data subsystem.

---

## 1. What problem this solves

By V0.6.0 the backend had become substantially more sophisticated than the
experience of using it. Nothing was missing; everything was unexplained. The
application assumed a user who already knew what delta was, what a stop loss
does, why a stop cannot be a buy order, why one provider is asked before
another, and what "process score" means. For everyone else the honest answer to
"how do I learn this?" was **read the docs, or go and watch a video** — which is
a design failure, not a documentation gap.

The rule this milestone was built on:

> Every time a user becomes confused, the question is not *"where should we
> document this?"* but *"why was the software able to let them become
> confused?"*

Three consequences follow, and they are the three halves of the milestone
(there are three):

1. **Prevent** — an action that cannot possibly succeed should not be
   assemblable. §5.
2. **Teach in place** — a walkthrough that points at the real control, a
   glossary on hover, an empty state that says what will fill it. §2–4, §6.
3. **Notice** — recommend the walkthrough that this user, specifically, has not
   had. §7.

## 2. Architecture

```
optionspilot/services/guide.py        pure domain layer: state validation, merge
                                semantics, feature→tutorial recommendations
        ▲                       (no I/O, no clock, no network)
        │
optionspilot/ui/server.py       GET /api/guide, POST /api/guide/state
        │                       + _guide_facts(): measures usage from the
        │                       journal, order book, broker and watchlist
        ▼
config/runtime.py               guide_state() / set_guide_state()
                                → settings.json, key "guide"

ui/static/index.html
   GUIDE_TUTORIALS { … }        the CONTENT: 11 tutorials, 52 steps
   GUIDE_TERMS { … }            the GLOSSARY: 37 terms
   GUIDE_FEATURES [ … ]         the instrumentation vocabulary
   Guide (IIFE)                 the ENGINE: spotlight, card, help centre,
                                tooltips, catalogue, persistence
```

### Why the content lives in the frontend and the state lives in the backend

A step is a **CSS selector** plus a sentence plus a condition expressed as a
JavaScript predicate. None of those are things Python should hold, and a
tutorial catalogue in `guide.py` would mean the backend owned strings it could
never validate against the DOM they address.

Progress, by contrast, is **user data**. It lives in `settings.json` through
`RuntimeSettings`, not in the webview's localStorage, for the same reason the
watchlist does: a user who reinstalls, restores a backup or clears their
WebView2 profile should not be greeted as a beginner. It is also what makes the
recommender testable offline in `pytest` rather than only in a browser.

### The contract between them is IDS, never prose

A `Recommendation` names a tutorial **by id**. The human title comes from
`GUIDE_TUTORIALS` at render time; `guide.py` never holds one. Two catalogues
holding the same titles would be a second place tracking one fact — the failure
this codebase has already paid for twice (`data/health.py` in V0.5.3, the
settings ranking in V0.5.7).

Both failure modes here are silent and look implemented:

* a backend id the frontend lacks renders as **nothing at all**;
* a feature key the frontend never records makes its rule **unfireable**.

So both are asserted statically, in both directions, by
`tests/test_guide.py::TestCatalogueContract`, which parses `index.html`.
`scripts/guide_check.py` then asserts the third thing neither can:
**every declared step selector resolves to an element that exists.**

## 3. The tutorial engine

Data-driven, and that is the architectural claim of the milestone: adding a
screen's walkthrough means adding an entry to `GUIDE_TUTORIALS` and touching no
code. `guide_check.py` drives tutorials whose contents it does not know, which
is what makes the claim testable rather than aspirational.

A step:

```js
{ el:      "#ch-tfs",              // CSS selector, or omitted for a centred card
  tab:     "charts",               // switch here first (optional)
  title:   "…",
  body:    "…",                    // authored HTML — never user data
  do:      "Click Charts",         // the nudge shown with an animated arrow
  advance: {click: "selector"},    // default is the Next button
  when:    () => …,                // evaluated at START; false ⇒ step skipped
  before:  () => …  }              // side effect before the step renders
```

**`when` is evaluated once, when the tour starts**, not per step. A tour must
never point at a panel that is hidden for this user (no closed trades, no
contract selected, no analysis yet), and a step count that changed mid-tour
would make the progress indicator lie.

### Three implementation decisions worth not undoing

**The page stays interactive.** `#gd-ring` and `#gd-card` sit outside any
overlay and the ring is `pointer-events: none`. Every alternative — a modal, a
pointer-events trap, a cloned "safe" control — turns a walkthrough into a
slideshow, and the thing that makes a walkthrough stick is that the button the
user pressed was the real one. It is also why a click-advance step can exist at
all.

**One element does the dimming and the cutout.** `#gd-ring` carries
`box-shadow: 0 0 0 9999px rgba(6,7,9,.72)`, so the spread paints everything
outside the ring and the ring's own box is the hole. There is no four-rectangle
backdrop to keep in sync and no SVG mask to hit-test, and `top/left/width/height`
are directly animatable, so the spotlight glides between targets for free.

**The target is tracked, not measured once.** A `requestAnimationFrame` loop
compares the target's rect against the last one and repositions on change,
because targets move underneath a tour constantly: a tab animates in, the chart
resizes, the market-data panel refreshes on its own timer. Measuring once and
hoping is how a spotlight ends up highlighting empty space.

### Scrolling

`scrollIntoView` runs **only when the target is not visible at all** — not when
it is merely off-centre. Centring unconditionally threw the page to the bottom
of the Dashboard on step 1 (the target was the sidebar, which legitimately runs
past the fold), and a tall target being partly below the fold is normal rather
than a problem. This was caught by screenshot review, not by a check; the
lesson is the standing one in this repo — *assert what the user sees*.

## 4. Contextual help, four ways in

| Entry point | Scope | Where |
|---|---|---|
| **Learn: \<screen>** in the header | the active tab's walkthrough | always visible, relabels on every tab switch |
| **?** beside a panel heading | that panel's own walkthrough | Order ticket, Market data, Learning the app |
| **? / Ctrl+K** | searchable help centre | anywhere |
| **Help ▾** | tour · search · shortcuts | header menu |

**`?` was re-pointed, not taken.** It used to open the keyboard-shortcut card;
it now opens the help centre, which lists **Keyboard shortcuts** as a result and
which the shortcut card links back to. Both directions still work, and the key
does what the milestone asked. Nothing was removed.

The help centre indexes tutorials (title + summary + every step title), glossary
terms, and three actions (shortcuts, diagnostics, check for updates). The
updates entry deliberately **clicks the existing menu item** rather than calling
into `Updater`: that module owns the check, the menu close and the dialog, and a
second caller is a second thing to keep in step with it.

## 5. Order-ticket guardrails (Goal 1, and the reason for the milestone)

`OrderManager.place` refuses three combinations outright:

```python
if kind in (STOP_LOSS, TAKE_PROFIT) and side != "sell_to_close":
    raise ValueError(f"{kind.value} orders are exit orders")
if kind is TRAILING_STOP and side != "sell_to_close":
    raise ValueError("trailing stops are exit orders")
if side == "sell_to_close" and held == 0:
    raise BrokerError(f"no open position in {contract.symbol}")
```

Every one of them was reachable in two clicks and discovered only on submit.
`tkSyncTicket()` now reconciles the ticket with what is actually possible:

1. **Exit-only order types are removed from the list while buying** — hidden and
   disabled, not merely greyed. A greyed control with no explanation is a
   different kind of confusion, not less of it.
2. **Sell to close is disabled with nothing held**, with the reason in its
   tooltip, and selecting a contract you do not hold **re-arms the buy side and
   says so**.
3. **Quantity is clamped to the position size** on the sell side.

Whenever the guardrail changes something, `#tk-kind-why` says **what changed,
why, and what to do instead** — the three questions every message in this
milestone has to answer. It is `aria-live="polite"`, because a control silently
vanishing is exactly the case a screen-reader user cannot see.

**The backend validation is untouched and still authoritative.** This is a
second, earlier gate. The V0.4-era lesson in `CLAUDE.md` — *adding a gate
function is not the same as the gate being active* — applies in reverse here:
adding a UI gate must never become a reason to relax the real one.

`tkOptimistic` exists for one reason: after a buy fills, the ticket pre-arms
itself as a protective stop, but the position that makes selling legal is not in
`lastStatus` until the next websocket push up to a second later. Without a
bounded (20 s) optimistic record, the guardrail would immediately undo the
pre-arm — a feature fighting a feature.

## 6. Glossary and adaptive tooltips

37 terms, each **three to five sentences of plain English that say what the
thing tells you**, plus a concrete `eg` — the sentence a beginner actually
remembers ("Bid 1.00 / ask 1.20 means you are down about 17% the second you
fill"). No formulas; the app is not a textbook.

**Two attributes, deliberately:**

* `data-learn="…"` — hover **and** click. For inert text: labels, table
  headings, the PAPER TRADING badge.
* `data-tip="…"` — hover only. For controls that already do something. Without
  the split, clicking the EMA pill would open a glossary card instead of
  switching on EMA.

The hover tip shows the **first two sentences** and says where the rest is. A
tooltip that has to be read is a tooltip that gets in the way.

## 7. Recommendations, and the line they do not cross

> **`services/guide.py` recommends TUTORIALS from FEATURE usage. It never recommends
> trading behaviour.**

That is `intelligence/`'s job, it does it from the trade record with a
false-discovery correction underneath it, and a second, cruder path to the same
kind of claim would be precisely the drift described in §2. The line is
concrete: *"you have never placed a limit order"* is a fact about the software;
*"you should place more limit orders"* is a claim about the trader, and this
module does not make it.
`tests/test_guide.py::test_no_rule_gives_trading_advice` sweeps every rule and
asserts it.

Each rule is a guard plus a sentence, and carries the measurement that produced
it. Nothing fires on an absence of data except the welcome tour, whose whole
purpose is to run before there is any.

| Rule | Fires when | Evidence |
|---|---|---|
| `welcome` | never onboarded | — |
| `trade` (order types) | ≥3 orders placed, **all** market | the order book |
| `trade` (exit orders) | ≥1 open position, no exit order ever used | broker + order book |
| `coach` | ≥1 review written, Coach tab never opened | review count |
| `journal` | ≥1 closed trade, Journal never opened | journal count |
| `charts` | ≥3 chart visits, no indicator ever toggled | recorded feature use |
| `marketdata` | exactly one **independent** source, panel never opened | live failover state |
| `backtest` | no closed trades, Backtest never opened | journal count |
| `watchlist` | ≤3 symbols, Watchlist never opened | the watchlist |

`single_data_source` is **`bool | None`**. `None` means "could not be
determined" — an injected provider double has no chain to inspect — and only
`True` may fire the rule. Answering `False` there would be a claim the data does
not support, and would silently suppress the recommendation that matters most on
a keyless install.

Capped at three, deduplicated by tutorial (two rules reach the Trade tour;
the user must not see it twice), suppressed once completed **or dismissed** —
skipping is recorded, because a tutorial a user walked out of should stop being
offered.

## 8. Accessibility

* **Four display preferences**, all persisted with the profile rather than in
  localStorage, because a setting a user needed once is one they will need again
  on the next machine: **reduce motion**, **larger text**, **high contrast** and
  **hover explanations**. Larger text scales the nine-step `--fs-*` ramp in
  `:root` rather than overriding individual rules — every font size in the app
  already comes from that ramp, so nothing hard-codes a size that would refuse to
  grow; ~1.15× is the largest step that keeps the dense tables inside their
  panels at 1280px. High contrast raises the two things that actually fail a
  contrast check in this palette (secondary/muted text and hairline borders) and
  deliberately **stays a dark theme** — inverting to light would be a different
  product, not an accessibility setting.
* **Reduced motion** — `html.gd-nomotion` is one switch for the whole app. The
  OS preference is the default and the in-app toggle overrides it **in both
  directions**: a user who wants animation on a machine configured without it
  should be able to have it. Every animated rule is written so that removing its
  transition leaves a correct static layout — a reduced-motion user gets the same
  spotlight, instantly.
* **Keyboard** — `→`/`←` step, `Esc` pauses, `?`/`Ctrl+K` search, `↑`/`↓` and
  `Enter` in the help centre. Focus moves to the card on every step.
* **Screen readers** — the card is `role="dialog"` with `aria-labelledby` and
  `aria-live="polite"` so step changes are announced; the decorative spotlight is
  `aria-hidden`; the guardrail explanation is a live region; every icon-only
  control carries an `aria-label`.
* **`aria-modal` is deliberately absent** from the walkthrough card. The page
  underneath *is* interactive — claiming otherwise would be a lie to assistive
  technology, and a worse one than saying nothing.

## 9. Testing

| Suite | Count | What it covers |
|---|---|---|
| `tests/test_guide.py` | 43 | catalogue contract, state validation, merge semantics, display flags, every recommendation rule |
| `tests/test_ui_server.py::TestGuideAPI` | 16 | both endpoints, persistence, malformed patches, measured facts |
| `scripts/guide_check.py` | **135** | the whole UX in a real headless browser |

`guide_check.py` asserts what is **on screen**, following the V0.5.5 lesson
applied to a spotlight instead of a candle. Its canonical check is not "a step
declared a target" but:

> **the highlight rectangle must intersect the element it claims to highlight,
> and the explanation card must not be sitting on top of it.**

Both are things a correct-looking implementation gets wrong the moment a layout
shifts underneath it, and both were verified to fail by deliberately breaking
the code that satisfies them.

It runs **almost entirely offline**. The one stubbed thing is `/api/chain`,
fulfilled from a canned payload at the HTTP boundary, because the order-ticket
guardrails cannot be exercised without an option chain and an option chain
cannot be fetched without a network. Everything downstream of that response —
parsing, rendering, the guardrail itself — is the real code path.

**Every other browser suite now seeds `guide.onboarded = true`** into its
scratch profile (`skip_onboarding()` in `chart_check.py` / `marketdata_check.py`,
inline in `intelligence_check.py`'s `seed()`), because a scratch profile has by
definition never been onboarded and the welcome dialog would otherwise sit over
every assertion they make. `browser_check.py` is the deliberate exception: it
dismisses the dialog by clicking it, which makes the genuine first-launch path
covered rather than avoided.

## 10. Honest limitations

* **The welcome tour is 13 steps and claims "two minutes."** That is a
  reasonable pace for reading, not a measurement.
* **Feature marks are best-effort.** They are buffered and flushed on a 2.5 s
  debounce and on `beforeunload`; a hard kill can lose the last few. They drive
  suggestions only, so the cost of a lost mark is one suggestion appearing once
  more than it needed to.
* **Nothing knows whether a tutorial was understood**, only that it was
  finished. There is no comprehension check and no attempt to infer one — that
  would be exactly the kind of unearned claim about a user that
  `TRADING_INTELLIGENCE.md` §4 forbids elsewhere in this app.
* **The glossary is fixed, not searchable-by-synonym.** "IV" finds implied
  volatility; "vol" does not. Search is substring-scored over title and body,
  with no stemming and no synonym table.
* **A tour cannot recover from the user navigating away mid-step.** Clicking a
  different tab during a non-click step leaves the spotlight pointing at a
  hidden element until the next step; the ring tracks a zero-size rect and the
  card stays put. Pressing Esc and restarting is the recovery, and the resume
  marker makes that cheap.
* **`when` predicates are evaluated at start**, so a panel that appears *during*
  a tour (the first scan completing, say) will not gain its step until the tour
  is restarted. This is a deliberate trade for an honest progress indicator.
