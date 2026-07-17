# ROADMAP-V3-UX.md — UX & workflow audit, V3 planning

**Status: planning document. Nothing in this file has been implemented.**
No product code changed as part of producing this audit — per instruction,
this session did not touch trading logic, docs automation, or build
tooling. This is the deliverable itself: an audit + a roadmap to get
explicit approval on before any V3 work starts.

**Date:** 2026-07-17. **Scope:** full visual + code audit of
`optionspilot/ui/static/index.html` (the entire frontend — one file,
2015 lines) against the running dev server, benchmarked against
TradingView, Webull Desktop, Robinhood Desktop, Thinkorswim, and Fidelity
Active Trader Pro.

## Methodology

- Launched the existing dev server (already running on `:8791` from a
  prior session) and drove it with Playwright (`channel="msedge"`,
  1600×1000 viewport) to screenshot all 9 tabs full-page, plus a second
  pass at a narrower 1024×768 viewport to probe responsiveness.
- Read the full CSS token block (`:root` custom properties), the nav/tab
  system, the keyboard-shortcut handler, every `aria-*` occurrence, the
  toast/skeleton-loader primitives, and the order-entry/option-chain
  flow in `index.html`.
- Cross-referenced against `docs/ARCHITECTURE.md` and `docs/AI_CONTEXT.md`
  for what's deliberate-and-documented vs. accidental, so this audit
  doesn't re-flag known, intentional trade-offs (e.g. "paper only," "no
  bundler") as bugs.
- Zero console errors, zero network failures on any tab — the app is
  functionally solid. Everything below is a polish/workflow finding, not
  a correctness bug.

## Cross-cutting findings (apply to every screen)

These aren't tied to one tab — they're systemic, and fixing them once
(in the shared CSS/JS) fixes every screen at once.

1. **No responsive breakpoints exist at all.** `nav` is a hardcoded
   `width:200px`, `main` is `max-width:1240px`, and there is exactly one
   `@media` query in the whole stylesheet (`prefers-reduced-motion`). At
   1024px width the top bar's pills wrap onto a second line and the
   layout doesn't reflow — it just clips. Every professional platform in
   the comparison set (TradingView and Webull especially) collapses the
   sidebar to icons or a drawer below a width threshold; this app does
   nothing until content starts overlapping.
2. **No type scale.** Font sizes are hardcoded inline across the
   stylesheet with ~14 distinct values in active use (10/11/12/13/14/15/
   16/17/18/22/26/36px), not derived from a scale variable. It doesn't
   look chaotic in a screenshot because the values cluster tightly, but
   it means every new component invents its own size instead of picking
   from a defined system — the kind of thing that drifts wider over time
   as more UI gets added.
3. **Accessibility is minimal, not absent.** `:focus-visible` is
   correctly defined globally and `prefers-reduced-motion` is respected
   (genuinely better than many hobby trading UIs), but there are only 14
   `aria-*` attributes in a 2000-line SPA — most data tables (positions,
   orders, watchlist price cells) have no `scope`/`aria-label`, there's
   no live region announcing price/P&L updates, and no skip-to-content
   link. A screen-reader user cannot use this app to trade.
4. **Keyboard coverage is nav-only, not action-only.** `1`–`9` switch
   tabs, `F` toggles chart fullscreen, `Esc` cancels a tool/dialog — all
   correctly implemented and non-conflicting with input fields. But
   there's no keyboard path to place, cancel, or modify an order, no
   "jump to symbol" hotkey (TradingView's `/`, Bloomberg-style ticker
   entry), and no shortcut-reference overlay (`?`) to discover what
   exists. Thinkorswim and TradingView both treat the keyboard as a
   first-class order-entry surface; this app treats it as tab navigation
   only.
5. **The frontend has zero desktop-only API dependencies.** Grepped for
   `pywebview`, `window.chrome.webview`, Electron APIs — none found. The
   entire frontend talks to the backend exclusively over `fetch()`
   against relative URLs. This is a significant, already-banked asset for
   the iOS question below — it means the *same* HTML/CSS/JS could run
   inside a `WKWebView` shell with no rewrite, if the backend is
   reachable. The gap for iOS is backend hosting and responsive layout,
   not frontend architecture.
6. **Empty states are correct but flat.** "No open positions," "Nothing
   yet," "No completed trades yet" are all accurate and appropriately
   terse — but every one of them is unstyled body text with no
   illustration, no suggested next action (e.g. a "Run your first scan"
   CTA), no visual weight. Robinhood/Webull invest specifically here
   because a paper account's first five minutes are all empty states.
7. **One finding that reads as a real (if minor) rendering bug**: the
   Settings tab screenshot shows a small unlabeled green-outlined pill
   floating in the middle of the "Effective configuration" JSON block
   (roughly at the vertical midpoint of the code viewer, no visible
   label or context). It doesn't appear in the DOM tree I read through
   grep, so it's most likely a stray `:focus` ring left on an element
   scrolled behind the code block, or a mispositioned toggle from the
   "Advanced settings (Custom mode)" collapsible section. Worth a 10-minute
   look before V3 starts, independent of the rest of this roadmap.
8. **Loading-state inconsistency.** The options chain uses proper
   skeleton-loader rows while data loads (good — matches the pattern
   professional platforms use). The chart canvas does not: on first tab
   entry it can render fully blank for several hundred milliseconds with
   no skeleton/spinner at all (reproduced directly — a full-page
   screenshot taken 600ms after switching to Charts showed a totally
   empty canvas; the same chart, revisited later in the same session,
   rendered instantly). Same app, two different loading conventions.

## Per-screen audit

### Dashboard
Clean, correctly prioritized (portfolio value → today's P&L → metrics →
equity chart → watchlist confidence → notifications → positions), and the
"yellow tick = required confidence" legend under the watchlist-confidence
bars is a genuinely good piece of self-documenting UI — better than most
retail platforms bother with. Gaps: the equity-history chart has no
placeholder/skeleton while empty (just a large blank panel — see finding
#6 above), and the Notifications panel is a dead-end with no way to
review past notifications once a session has some (no history, no
filtering, no click-through to the related trade).

### Charts
The strongest screen in the app relative to the comparison set — the
toolbar density, indicator pills, and drawing tools (Level/Trend/Fib/
Zone/Note) genuinely read as "TradingView-lite," and the footer hint
line (`Scroll to zoom · drag to pan · ... · F toggles fullscreen ·
open positions and working orders show as labeled price lines`) is
exactly the kind of affordance most competitors bury in a help menu
instead of surfacing inline. Gaps: no multi-chart / split-pane layout
(explicitly deferred per `PROJECT_STATUS.md`, not a new finding), the
blank-canvas loading gap noted above, and no visible bid/ask or
options-flow overlay on the chart itself (charts and the option chain
are fully separate screens with no crossover).

### Trade (order entry + options chain)
Functionally correct two-step flow (select a chain row → order ticket
populates) that mirrors Fidelity ATP's pattern reasonably well. The
metrics row up top (buying power, P&L, win rate, profit factor, avg
win/loss, max drawdown) is a genuinely professional touch most retail
apps don't surface this directly. Gaps: no default/quick-select (e.g.
"nearest ATM call") — a trader has to scan the whole chain manually
every time; the order ticket only appears after a chain row is clicked,
so there's no way to see ticket affordances (order type, TIF, stop/
target fields) until a contract is already chosen, which is one extra
round-trip TOS and Webull skip by keeping the ticket always-visible with
a "select a contract" placeholder state; and Buy/Sell as a *color*
convention (the universal green/red trading affordance) isn't visible
anywhere in this screen's static state — it likely appears once a
contract is selected, but that means the single most important visual
cue in options trading is invisible until two clicks in.

### Watchlist
Excellent workflow ergonomics already: paste-a-whole-list quick-add,
sector preset chips, drag-to-reorder, pin-to-top, multi-select with
Ctrl-click/Ctrl-A/Delete, autosave. This screen is at or above the bar
set by the comparison platforms. The only real gap is discoverability —
none of those interaction affordances (drag handle, Ctrl+click,
Delete-to-remove) are hinted anywhere except the one line of caption
text at the bottom; a first-time user has to read a paragraph of fine
print to learn the whole watchlist is drag-reorderable.

### Coach
Clear, well-organized layout (Recurring mistakes / Strengths & exercises
/ Trade reviews), and the empty-state copy ("Switch to You trade and
close a trade — every manual round trip gets a full AI review here") is
a good example of an empty state that teaches instead of just stating
absence — better than the flatter empty states elsewhere in the app.
No screen-level issues found beyond the general empty-state polish gap.

### Journal
Minimal by design at zero trades — nothing wrong found, but nothing to
evaluate yet either; revisit once real trade history exists to check
filtering/search ergonomics (mentioned as explicitly out of V2-6 scope
already in `PROJECT_STATUS.md`).

### Backtest
Single-purpose form, correctly scoped, includes an inline disclosure of
its own limitation ("Options are Black-Scholes priced from realized
volatility (documented limitation)") — good trust-building pattern,
consistent with the project's stated design philosophy in
`AI_CONTEXT.md`. No results-state was evaluated (would need a real run).

### Learning
The evidence-weights table (default/learned/effective, with the bounding
rule spelled out in the caption) is unusually transparent for an
"AI trading" feature — most competitors would hide this as an
unexplainable black box. This is a differentiator worth protecting, not
changing. The four "by X" breakdown panels are correctly laid out but
untestable at zero trades.

### Settings
The biggest outlier in the app. Every other screen is a purpose-built
UI; Settings is a raw pretty-printed JSON dump of the effective config
with a footer note to "edit `config.yaml`... and restart." That's
honest and consistent with the project's config philosophy
(`config/settings.py` is startup-only, read-only-in-app is the correct
mental model) — but visually and functionally it reads as a debug view
that leaked into production, not a settings screen. Every comparison
platform (even Robinhood, which has the fewest settings) presents
structured, editable controls. This is the single largest visual-polish
gap in the app.

### Notifications
Not a real screen — the Dashboard's "Notifications" panel is the entire
surface, and it's a single unpaginated "Nothing yet" with no persistence
model visible from the UI. No notification center, no unread state, no
per-notification action (e.g. "view the trade that triggered this").

### Navigation
Left rail, 9 items, number-key shortcuts, active-state indicator (left
accent bar + background) — clean and consistent across every tab, and
the persistent top bar (market status, AI/You-trade toggle,
Conservative/High-Risk/Custom, scan status, Scan-now button) staying
identical across all 9 tabs is a real strength — most competitors let
chrome drift screen-to-screen. The only gap is the fixed-width sidebar
covered in cross-cutting finding #1.

## Prioritized roadmap

Each item: **why it matters** / **user impact** / **complexity** /
**dependencies** / **iOS effect**.

### 1. Critical UX issues

These block the app from reading as "finished" to an experienced trader
opening it for the first time, or actively work against the eventual
iOS port.

**C1. Fix the Settings screen — replace raw JSON dump with structured,
grouped controls (even if most remain read-only with an explicit
"restart to apply" affordance).**
- *Why:* it's the one screen that visibly contradicts the polish level
  of the rest of the app; a trader who opens Settings after using Charts
  or Trade will assume the app is unfinished.
- *Impact:* high — first impressions matter disproportionately here,
  and Settings is usually an early-visit screen.
- *Complexity:* medium — needs a schema-driven renderer (group by the
  existing `data`/`indicators`/`engine`/`risk`/`broker`/`notify`/
  `integrations`/`logging` keys already in the config) but no backend
  change since it's already read-only-by-design.
- *Dependencies:* none blocking; purely frontend.
- *iOS:* easier — a card-based settings UI ports directly to an iOS
  settings pattern; a JSON dump does not (unreadable on a phone width).

**C2. Add real responsive breakpoints (collapse sidebar to icon rail
under ~1100px, stack the top-bar pills instead of wrapping raggedly).**
- *Why:* the app currently visibly breaks (not gracefully, just clips/
  wraps) below its comfortable width, and this is a *desktop* app users
  will resize.
- *Impact:* high for desktop users on smaller/split-screen monitors;
  it's also the direct prerequisite for iOS.
- *Complexity:* medium — CSS-only for the desktop-width cases; the
  icon-only nav rail needs a small JS toggle and tooltips.
- *Dependencies:* none.
- *iOS:* **directly enables it** — this is the single highest-leverage
  item for the iOS question. Do this one regardless of anything else.

**C3. Make the chart's loading state consistent with the rest of the
app (skeleton/spinner instead of a blank canvas on first paint).**
- *Why:* Charts is the flagship screen; a blank canvas reads as broken,
  not loading.
- *Impact:* medium-high — affects the single most-used tab.
- *Complexity:* low — the skeleton-loader CSS pattern already exists
  and is used elsewhere (option chain); this is applying it, not
  inventing it.
- *Dependencies:* none.
- *iOS:* neutral — same fix needed either platform.

### 2. High-impact improvements

Not blocking, but the items an experienced trader would notice within
the first real session and silently judge the app against competitors
for lacking.

**H1. Order-entry keyboard shortcuts and an always-visible order ticket
(placeholder state before a contract is selected).**
- *Why:* every comparison platform treats the keyboard as a trading
  surface, not just a navigation one; and hiding the ticket until a
  contract is picked adds a needless round-trip to the single most
  important workflow in the app.
- *Impact:* high for active/frequent traders — this is the workflow
  they'll use the most.
- *Complexity:* medium — ticket layout already exists, needs a
  "no contract selected" placeholder variant plus a few hotkeys (e.g.
  `B`/`S` focus buy/sell, `Enter` submits with confirm-dialog already in
  place).
- *Dependencies:* none technical; should go through the existing
  `RiskManager`/`OrderManager` gates unchanged — this is presentation
  layer only.
- *iOS:* the keyboard shortcuts are desktop-only value-add (harmless,
  unused on iOS); the always-visible ticket placeholder is a net
  positive for iOS too (avoids a jarring "ticket appears" transition on
  a small screen).

**H2. Discoverability pass: a `?` shortcut-reference overlay, and
surface the Watchlist's hidden interactions (drag-to-reorder,
multi-select) as visible affordances instead of caption-text-only.**
- *Why:* several genuinely good interaction patterns already exist in
  this app (Watchlist multi-select, drawing tools, keyboard nav) but are
  discoverable only by reading fine print — competitors surface these
  with visible icons/handles/tooltips.
- *Impact:* medium-high — doesn't add capability, multiplies the value
  of capability that already exists.
- *Complexity:* low-medium — mostly visual affordances (drag handles
  already render as `≡`, just need better contrast/hover state) plus
  one new overlay component.
- *Dependencies:* none.
- *iOS:* the `?` overlay is desktop-specific (replace with a help/info
  icon on iOS); drag-to-reorder needs touch-gesture equivalents on iOS
  regardless, so making the pattern more explicit now pays off twice.

**H3. Accessibility baseline: label every data table (`scope`/
`aria-label` on positions, orders, watchlist, chain rows), add an
`aria-live="polite"` region for price/P&L updates and toast messages,
add a skip-to-content link.**
- *Why:* currently a screen-reader user cannot meaningfully use this
  app; this is a correctness gap dressed as a polish gap.
- *Impact:* medium (narrow but real user population; also a general
  code-quality signal).
- *Complexity:* low-medium — mechanical, no architecture change, mostly
  markup additions across existing components.
- *Dependencies:* none.
- *iOS:* directly reusable — iOS VoiceOver reads the same ARIA
  semantics as desktop screen readers in a WKWebView.

**H4. A defined type scale (replace ad hoc pixel values with 5–6 scale
steps as CSS variables) and a spacing scale (same treatment for the
padding/margin values, which show similarly wide ad hoc variation).**
- *Why:* invisible today because the app is small and one person is
  writing all the CSS, but it's exactly the kind of debt that makes
  every future screen slower to build consistently.
- *Impact:* low near-term visual impact, medium-high long-term
  velocity/consistency impact.
- *Complexity:* low — define the scale, then a mechanical pass
  replacing values (can be done incrementally, screen by screen).
- *Dependencies:* ideally do this *before* C1 (Settings rebuild) and H1
  (order ticket rework) so those new components are built on the scale
  rather than needing a second pass.
- *iOS:* strongly positive — a defined scale is what makes "the same
  design system, different breakpoints" tractable across form factors;
  ad hoc pixel values don't.

**H5. Notification center: give the Dashboard's notification panel
actual persistence/history, unread state, and click-through to the
related trade/order.**
- *Why:* right now it's decorative until the first notification ever
  fires, and even then has no history.
- *Impact:* medium — becomes valuable specifically once a user has been
  running the app for a while, which is exactly when they most need it.
- *Complexity:* medium — needs a small backend list/read-state
  endpoint; frontend is straightforward given the toast system already
  exists as a base.
- *Dependencies:* backend: a lightweight notification-log store (check
  whether `optionspilot/notify/` already persists anything before
  building a new one — worth a quick look at implementation time, not
  now).
- *iOS:* high value — push-notification-style history is a pattern iOS
  users expect natively; building the data model now means the iOS
  client is just another consumer of it later.

### 3. Nice-to-have improvements

Real polish, lower urgency — good V3-adjacent or V3.1 candidates.

**N1. Friendlier empty states with a suggested next action** ("Run your
first scan" CTA on the Dashboard, "Load a chain to get started" on
Trade) instead of plain text. *Impact:* medium, mostly first-session
impression. *Complexity:* low. *iOS:* positive — empty states matter
disproportionately more on small screens where there's less other
content to look at.

**N2. Cross-link Charts and the options chain** — e.g. a "view chain"
action from a chart's symbol header, or overlaying open-position price
lines' option context on hover. *Impact:* medium, workflow-efficiency
for active use. *Complexity:* medium (real feature work, not styling).
*iOS:* neutral.

**N3. Buy/Sell color convention made visible earlier in the order flow**
(e.g. color the selected-contract row or the "Trade SPY →" button by
side) rather than only appearing once the ticket is fully populated.
*Impact:* medium, this is the industry-standard visual shorthand and its
absence is the kind of thing an experienced trader would consciously
notice. *Complexity:* low. *iOS:* neutral, but worth doing before the
iOS port since color coding is even more load-bearing on small screens
with less room for text labels.

**N4. Toast queueing/stacking** for the rare case of multiple rapid
events (e.g. several fills in one scan cycle) rather than one toast
overwriting/racing another. *Impact:* low-medium, edge case but a real
one given the orchestrator processes a whole scan cycle at once.
*Complexity:* low. *iOS:* neutral.

### 4. Long-term ideas

Bigger bets, explicitly not V3 scope — listed so they're not lost, not
because they're being greenlit.

**L1. Multi-panel/multi-chart workspace layout** — already tracked as
deferred V2-4 remainder scope in `PROJECT_STATUS.md`; re-evaluate after
V3's polish pass, since a messy single-pane experience is a worse
foundation for a multi-pane one. *iOS:* harder — multi-pane desktop
layouts are the opposite of what a phone-width iOS client wants; if this
is ever built, it should be explicitly desktop-only and gated behind a
width check rather than assumed universal.

**L2. Theming beyond the current single dark palette** — not clearly
valuable (every comparison platform's power users default to dark, and
the existing palette is already solid) but worth a deliberate "yes/no"
decision rather than silence, since Settings-screen work (C1) is a
natural place a "light mode" toggle would eventually live if ever added.

**L3. A genuine cross-platform UI layer decision for iOS** — see the
dedicated section below. This is bigger than a UX line item; it's an
architecture decision that the V3 polish pass should make *easier*, not
attempt to resolve itself.

**L4. Real per-flow browser regression coverage** (already flagged as a
known gap in `TODO.md`/`NEXT_SESSION.md`, not new) — once V3's UI
changes land, this becomes more valuable than it is today, since more
surface area means more to regress.

## Top 10 for V3 (recommended)

In priority order, mixing "must fix" with "best ratio of impact to
effort":

1. **C2** — responsive breakpoints (sidebar collapse, top-bar reflow).
   Highest leverage item in this whole audit; blocks nothing else and
   unblocks the iOS conversation.
2. **C1** — rebuild Settings as structured controls.
3. **C3** — fix chart blank-canvas loading state.
4. **H4** — define type + spacing scales (do this early so C1/H1 are
   built on top of it, not retrofitted).
5. **H1** — order-entry keyboard shortcuts + always-visible ticket
   placeholder.
6. **H3** — accessibility baseline pass (tables, live regions, skip
   link).
7. **H2** — discoverability pass (shortcut overlay, visible Watchlist
   affordances).
8. **N3** — earlier buy/sell color signal in the order flow.
9. **N1** — friendlier, action-oriented empty states.
10. **H5** — notification center with persistence/history.

Everything else in this document (N2, N4, L1–L4) is real and worth
revisiting, just not what should define V3's scope.

## iOS compatibility assessment

**Bottom line: the frontend architecture is already close to
iOS-portable — the gap is responsive layout and backend hosting, not a
rewrite.**

What's already in the bank:
- Zero desktop-only JS APIs in the frontend (verified by grep — no
  `pywebview`, no Electron/webview-specific calls). Every interaction is
  `fetch()` against relative API paths.
- The vendored chart library (`lightweight-charts.js`, Apache-2.0) is a
  pure JS/canvas library with no desktop dependency — renders identically
  in a `WKWebView`.
- No build step, no bundler — a `WKWebView` can load this file (or a
  served version of it) with no toolchain changes.

What's missing, mapped to the roadmap above:
- **Layout** (C2, H4) — the fixed 200px sidebar and lack of breakpoints
  is the *entire* blocker for a phone-width layout. This is exactly why
  C2 is ranked #1: it's simultaneously the top desktop-polish item and
  the top iOS-enablement item. No other item in this audit does double
  duty that cleanly.
- **Touch targets** — several controls (watchlist row `×`/`★` buttons,
  chain rows, chart toolbar pills) are sized for mouse precision, not
  the ~44×44pt minimum iOS Human Interface Guidelines recommend for
  touch. Not flagged as its own roadmap item above because it's a
  natural side effect of doing C2/H4 properly (a defined spacing scale
  with touch-safe minimums baked in) rather than a separate pass — but
  call it out explicitly during C2/H4 implementation, not after.
- **Backend hosting model** — this is the part that isn't a UX fix at
  all. The desktop app spawns FastAPI as a local child process via
  pywebview. iOS cannot spawn arbitrary local server processes the way
  a desktop app can. An iOS client needs either (a) the same backend
  reachable over the network (the user's desktop app, or a hosted
  instance) with the iOS app as a pure client, or (b) a fundamentally
  different embedded-runtime approach. This is a real architecture
  decision — bigger than this audit, and explicitly **not** something to
  decide as a side effect of a UX polish pass. Flagged here as L3 so
  it's not forgotten, not resolved.
- **Keyboard-only affordances** (H1's hotkeys, the `?` overlay) are
  desktop-additive and iOS-neutral — they don't need an iOS equivalent,
  they just don't run there. No conflict.

**Recommendation:** do C2 and H4 as designed for desktop-with-narrow-
window-support (not "build two layouts") — a single responsive system
that collapses gracefully is strictly better for desktop users on
smaller monitors *today*, and is most of what an iOS port would need
from the frontend *later*. This is the "improves both, sacrifices
neither" framing the request asked for, and it's concretely true for
this specific item, not just a good-sounding generality.

## What this roadmap deliberately does not include

- No new trading features, no scoring/engine changes, no risk-logic
  changes — this audit found zero correctness issues; every finding
  above is presentation-layer or workflow-ergonomics.
- No CI/linting/automation recommendations — already covered in
  `CONTRIBUTING.md`'s "Automation: what's implemented vs. still just
  recommended" section from the prior session; out of scope for this
  one per this session's explicit instruction.
- No committed decision on iOS beyond "make the frontend layout
  responsive" — the backend-hosting question (L3) needs its own
  dedicated discussion, not a line item here.

---

**Waiting on approval before any implementation begins**, per this
session's explicit instruction. Once a scope is chosen (the Top 10 as
listed, a subset, or a reordering), the next session should follow this
repo's standard practice: backend/logic changes first with tests (where
applicable — most of this is frontend-only, so this mainly applies to
H5's notification persistence), then frontend, then manual verification
in a real browser per `CLAUDE.md`, then the standard documentation
update pass.
