# UI_V2_WIREFRAMES.md — screen-by-screen layout blueprint

**Status:** proposed, not implemented. **Version:** 1.0.
**Parent:** `UI_V2_DESIGN.md` — the product vision, which is
implementation-agnostic and is not modified by this document.

This document is the layer between the vision and the code. Where
`UI_V2_DESIGN.md` says *what the product should feel like and why*, this
says *where every element goes, what it does when touched, and what it
looks like when there is no data, slow data, or wrong data.* It is written
to be implementable without invention: an engineer should be able to build
a screen from it and disagree with nothing that was left unstated.

It contains no HTML, CSS, JavaScript, React, Qt or implementation code of
any kind, by design.

**The seven principles from the parent document bind every layout here**,
and each is cited at the point it constrains a decision: one workspace ·
progressive disclosure · context continuity · the calm instrument ·
evidence before confidence · friction proportional to consequence · motion
with purpose.

---

## 0. How to read this document

### 0.1 Notation

All wireframes are monospace and dimensionally honest at their stated
breakpoint: a region twice as wide in the diagram is twice as wide on
screen. They are **layout diagrams, not visual mockups** — they show
position, grouping, hierarchy and proportion. Colour, typography,
elevation, iconography and spacing values come from the design system
(`UI_V2_DESIGN.md` §11) and are deliberately absent here.

| Symbol | Meaning |
| --- | --- |
| `[ Label ]` | Button. `[ Label ]` with no border in prose means the same. |
| `[value]` | Text input or numeric field |
| `Label  v` | Select / dropdown |
| `[A] B C` | Segmented control, `A` is the selected segment |
| `>` at line start | The currently selected row |
| `>` at line end | Navigates somewhere; the row is a link |
| `(i)` | Informational annotation, in-place, dismissible |
| `(!)` | Attention state — needs a decision, not an acknowledgement |
| `(X)` | Error or blocked state |
| `( )` | Neutral / unassessable — evidence is insufficient (§0.5) |
| `(~)` | Pilot affordance |
| `####` | Skeleton placeholder (loading) or a bar in a bar-chart |
| `_.-'` | A line chart or sparkline |
| `=` | Drag handle |
| `*` | Pinned, or "nearest the money" |
| `x` | Inline close/remove control |

### 0.2 The four layout archetypes

Six destinations, four layouts. Nothing else may be invented; a new screen
picks an archetype or the archetype list changes deliberately.

| Archetype | Structure | Used by |
| --- | --- | --- |
| **A — Bands** | Full-width horizontal bands, ordered by consequence. Band 1 never scrolls. | Home |
| **B — Workspace** | Two persistent columns; the left splits horizontally. Every region visible at once. | Trade |
| **C — Index + Detail** | A list on the left, a detail rail on the right that fills from the selected row. The rail is never a modal. | Portfolio, Journal › Trades |
| **D — Sections** | A secondary section rail inside the destination, plus a content pane. | Research, Journal, Settings |

Two rules that make the archetypes worth having:

1. **A destination never changes archetype based on state.** An empty
   Portfolio is Archetype C with empty regions, not a centred splash.
2. **Archetype C's detail rail is populated or explicitly empty, never
   absent.** A rail that disappears when nothing is selected causes the
   list to reflow, which violates motion rule M-4 (§11).

### 0.3 The measurement basis

All full-screen wireframes are drawn at **1920×1080**, the reference
breakpoint. The proportions below are the specification; pixel values are
derived from them.

| Element | Size | Note |
| --- | --- | --- |
| Frame (top) | 48px, fixed | Never scrolls |
| Nav rail | 200px, fixed | Collapses per §10 |
| System strip (bottom) | 28px, fixed | Never scrolls |
| Content area | remainder | Owns its own scrolling |
| Content gutter | 24px | Between destination regions |
| Archetype C detail rail | 320px, fixed | Not resizable |
| Archetype D section rail | 168px, fixed | Not resizable |
| Archetype B ticket column | 340px, resizable 300–480px | Persisted |
| Archetype B chart/chain split | 55/45 default, resizable | Persisted |

Resizable proportions persist per destination through `RuntimeSettings`,
not `localStorage` (`UI_V2_DESIGN.md` §4.5, guarantee 8).

### 0.4 The per-screen template

Every destination section below follows the same fourteen headings, in the
same order. Where a heading says "inherits", the shell's behaviour (§1)
applies unchanged and the screen adds nothing.

`Purpose · Layout · Information hierarchy · Regions · Navigation ·
Interactions · Keyboard · Loading · Empty · Error · Motion · Responsive ·
Accessibility · Mobile equivalent`

### 0.5 The evidence convention, stated once

This applies to every number on every screen and is the most commonly
violated rule in trading UIs. From `UI_V2_DESIGN.md` P3 and
`TRADING_INTELLIGENCE.md` §4.1:

| Situation | Renders as | Never renders as |
| --- | --- | --- |
| Measured, sufficient sample | `58%  n=41` | `58%` alone |
| Measured, sample below floor | `--  (0 of 5 trades)` | `0%` or a blank cell |
| Not measurable at all | `( ) Not measurable here.  Why? >` | Hiding the row |
| Genuinely infinite | `no losing trades yet` | `inf`, `∞`, `NaN` |
| Composite below coverage floor | `not enough evidence` + coverage % | A grade over partial coverage |

**A blank cell always means "we have not said," never "zero."** Every
screen below applies this without restating it.

---

## 1. The global shell

Everything in this section is present on every destination and behaves
identically everywhere. It is specified once so that no destination
re-specifies it, and so that a difference between destinations is
immediately recognisable as a bug.

### 1.1 Anatomy

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Home             SPY  471.20 +0.84%    Ctrl+K   [PAPER . AI idle]          |
+-----------------+--------------------------------------------------------------------------+
| (o) Home      1 |                                                                          |
| (^) Trade     2 |                                                                          |
| ($) Portfolio 3 |     MAIN                                                                 |
| (?) Research  4 |     The active destination renders here. It owns its own                 |
| (=) Journal   5 |     scrolling. The frame and the rail never scroll.                      |
|                 |                                                                          |
|                 |                                                                          |
|                 |                                                                          |
|                 |                                                                          |
|                 |                                                                          |
| (~) Pilot       |                                                                          |
| (,) Settings    |                                                                          |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

Four permanent parts: **frame** (top), **nav rail** (left), **content**
(centre), **system strip** (bottom). Only content scrolls, ever.

### 1.2 The frame

Left to right: product mark · destination name (and section, as
`Research > Backtest`) · workspace context (symbol, last price, day change)
· `Ctrl+K` hint · Flight Status button · Pilot affordance.

| Element | Behaviour |
| --- | --- |
| Destination name | Static text. Not a control. |
| Workspace context | Click opens symbol jump (§1.6). Absent on destinations with no symbol context (Settings, Journal › Progress). |
| `Ctrl+K` hint | Click opens the palette. Fades to 40% opacity after the palette's first use — the hint is a teaching device, not permanent chrome. |
| Flight Status | Button. Opens the popover (§1.8). Label is generated, never truncated; if the window is too narrow it drops words in a fixed order: market → engine → account. |
| Pilot | Button. Opens the Pilot panel (§1.7). No badge, no dot, no count, ever. |

**The frame carries no destination-specific controls.** A destination that
needs a global action puts it in its own content area or in the palette.
This is the fix for today's eight-control header.

### 1.3 The nav rail

```
+--------------------------------------------------------------------------------------------+
|  >= 1440px            1280-1439px           1024-1279px                                    |
|  +----------------+   +-------+             +-----+                                        |
|  | (o) Home     1 |   | (o) 1 |             | (o) |   icon only, tooltip on                |
|  | (^) Trade    2 |   | (^) 2 |             | (^) |   hover, accessible name               |
|  | ($) Portfoli 3 |   | ($) 3 |             | ($) |   always present                       |
|  | (?) Research 4 |   | (?) 4 |             | (?) |                                        |
|  | (=) Journal  5 |   | (=) 5 |             | (=) |                                        |
|  |                |   |       |             |     |                                        |
|  | (~) Pilot      |   | (~)   |             | (~) |                                        |
|  | (,) Settings   |   | (,)   |             | (,) |                                        |
|  +----------------+   +-------+             +-----+                                        |
|     17 ch                7 ch                 5 ch                                         |
+--------------------------------------------------------------------------------------------+
```

- Five destinations, then a flexible spacer, then Pilot and Settings
  pinned to the bottom. Settings is deliberately **not** numbered 6 — it is
  a utility, reached with `,`, and numbering it would imply it is a peer of
  the five.
- The active item is marked by a **left edge indicator plus a background**,
  never by colour alone (`UI_V2_DESIGN.md` §11.2 rule 1).
- Number hints are visible at ≥1440px and drop with the labels below it.
  Like the `Ctrl+K` hint, they fade after the shortcut has been used three
  times.
- **The rail never collapses to a hamburger.** At every supported width the
  destinations are visible as targets (`UI_V2_DESIGN.md` §13.2).
- `PAPER TRADING` is **not** in the rail (where it is today). It is in
  Flight Status, because it is a status, and status has one owner.

### 1.4 The system strip, and the one-fact-one-owner split

The strip carries four things, none of which appear anywhere else:

| Zone | Content | Interaction |
| --- | --- | --- |
| Left | Active data provider + age of the last successful fetch | Click → Settings › Data |
| Centre-left | Surface Level control (`Guided / Focused / Full / Pro`) | Click → menu. Also `Alt+1..4` |
| Centre-right | Unread notification count + highest current severity glyph | Click → inbox (§1.9) |
| Right | Version | Click → Settings › About |

**The split with Flight Status is deliberate and must not blur.** These are
two status surfaces and the temptation to duplicate between them is exactly
the failure `CLAUDE.md` records for provider health living in two objects.

| Fact | Owner | Never appears in |
| --- | --- | --- |
| Account mode (`PAPER`) | Flight Status | The strip, the rail |
| Who trades (AI / you) | Flight Status | The strip |
| Risk profile | Flight Status | The strip |
| Market open/closed | Flight Status | The strip |
| Scan cadence, last scan | Flight Status | The strip |
| Data provider + freshness | The strip | Flight Status |
| Surface Level | The strip | Settings (which links to it) |
| Unread notifications | The strip | The frame, the rail |
| Version | The strip | The frame |

**Rule:** if a fact needs to appear in a second place, it is *linked to*,
not *copied*.

### 1.5 Command palette — `Ctrl+K`

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Home                                                                       |
|                                                                                            |
|          +---------------------------------------------------------------------+           |
|          |  Search or jump to...                                          Esc  |           |
|          |  > opti|                                                            |           |
|          |                                                                     |           |
|          |  DESTINATIONS                                                       |           |
|          |    Portfolio                                                     3  |           |
|          |                                                                     |           |
|          |  ACTIONS                                                            |           |
|          |    Run a scan now                                                   |           |
|          |    Place an order on SPY                                     Ctrl+2 |           |
|          |    Toggle AI trading                                                |           |
|          |                                                                     |           |
|          |  SETTINGS                                                           |           |
|          |    Market data providers                            Settings > Data |           |
|          |    Add an API key                                   Settings > Data |           |
|          |                                                                     |           |
|          |  MOVED  (old names still work)                                      |           |
|          |    Coach                        ->  Journal > Review                |           |
|          |    Backtest                     ->  Research > Backtest             |           |
|          |    Learning                     ->  Research > Engine               |           |
|          +---------------------------------------------------------------------+           |
|                                                                                            |
|  Page behind is dimmed, not blurred. Palette never moves once open.                        |
+--------------------------------------------------------------------------------------------+
```

The palette is what makes a six-item navigation viable. It is the single
most load-bearing shell component.

| Property | Specification |
| --- | --- |
| Position | Fixed: horizontally centred, top at 18% of viewport height. **It does not move as results change** — a palette that re-centres per keystroke is unusable. |
| Width | 640px fixed at all breakpoints |
| Max height | 60% of viewport; the result list scrolls, the input never leaves view |
| Groups | Destinations · Actions · Settings · Moved · Recent. Group order is fixed; empty groups are omitted. |
| Ranking | Exact match → prefix match → fuzzy → recency. Ties broken by frequency of use. |
| Right column | The keyboard binding, or the destination path. This is how shortcuts are learned. |
| `Moved` group | Old tab names (`Coach`, `Backtest`, `Learning`, `Watchlist`, `Charts`, `Dashboard`) resolve to their new homes **and display the mapping** rather than silently redirecting. |
| Actions | Every action is context-aware: "Place an order on SPY" uses the workspace symbol. |
| Destructive actions | Never execute from the palette. They navigate to the control and focus it. The palette cannot place an order, close a position, or reset a halt. |
| Dismiss | `Esc`, outside click, or selecting a result. Query is cleared on dismiss; the last query is offered as a ghost on reopen. |

> **Beginner:** the palette is how they find something they only know the
> old name of, or the plain-English name of, without learning where it
> lives. **Experienced:** one keystroke replaces all navigation.
> **One system:** a single index over destinations, actions and settings —
> the clearest possible statement that these are one application.

### 1.6 Symbol jump — `/`

```
    +----------------------------------------------------------+
    |  /  nvda|                                                |
    |     NVDA   NVIDIA Corp          892.10   +1.4%   [watch] |
    |     NVDL   GraniteShares 2x     41.22    +2.8%           |
    |     NVDS   Direxion Daily -1x    9.04    -1.4%           |
    |                                                          |
    |  Enter sets the workspace symbol everywhere.             |
    +----------------------------------------------------------+
```

- Opens from anywhere except a text input. Positioned under the frame's
  context slot, anchored left, 480px wide.
- Searches the local symbol table (`data_assets/symbols.csv`), so it works
  offline and returns instantly.
- `Enter` sets **the workspace symbol** — chart, chain, ticket, Research,
  Home's context strip, and the frame, simultaneously
  (`UI_V2_DESIGN.md` §4.5, guarantee 1).
- `[watch]` adds to the watchlist without leaving the current destination.
- **There is no other symbol input in the product.** Every place that today
  has its own symbol box (`#ch-symbol`, `#tk-symbol`, `#bt-symbol`) becomes
  a display of the workspace symbol with this as the only editor.

### 1.7 Pilot panel — `Ctrl+/`

```
                                          +------------------------------------------------+
                                          |  PILOT                                    Esc  |
                                          |                                                |
                                          |  You asked: what is delta?                     |
                                          |                                                |
                                          |  Delta is roughly the chance this finishes     |
                                          |  in the money. 0.54 means about 54%. It also   |
                                          |  says the option moves about $0.54 for every   |
                                          |  $1 SPY moves.                                 |
                                          |                                                |
                                          |  Related on this screen:                       |
                                          |    What does IV tell me?                       |
                                          |    Why is this contract priced at $3.90?       |
                                          |                                                |
                                          |  Ask about anything on screen...               |
                                          +------------------------------------------------+
```

| Property | Specification |
| --- | --- |
| Position | Right side, 380px, **overlays** the content. It never reflows the workspace — a panel that shoves the chart sideways is a panel users stop opening. |
| Content | Answer, then up to three context-relevant follow-ups drawn from what is currently on screen, then the ask field. |
| Persistence | Conversation state survives destination changes (`UI_V2_DESIGN.md` §4.5, guarantee 7) and restart. |
| Silence | The panel never opens itself. Pilot's four surfaces and eight silence rules are `UI_V2_DESIGN.md` §9.3 and §9.5 and are not restated here. |
| Inline form | The dominant Pilot surface is **not** this panel: it is the in-place explanation attached to a term or a statistic. This panel is for longer answers and for questions. |

### 1.8 Flight Status popover

```
| OptionsPilot   Home                          Ctrl+K   [PAPER . AI idle] <- click           |
|                                 +---------------------------------------------------------+|
|                                 |  FLIGHT STATUS                                          ||
|                                 |                                                         ||
|                                 |  Account        PAPER . $10,000 start . not real money  ||
|                                 |                                                         ||
|                                 |  Who trades     [ AI trades ] [ You trade ]             ||
|                                 |                 The AI opens positions on its own.      ||
|                                 |                 Does not change your risk profile.      ||
|                                 |                                                         ||
|                                 |  Risk profile   [ Conservative ] [ High-Risk ] [Custom] ||
|                                 |                 Sets size and confidence thresholds.    ||
|                                 |                 Does not change who trades.             ||
|                                 |                                                         ||
|                                 |  Scanning       Every 60s . last 12s ago                ||
|                                 |                 [ Scan now ]                            ||
|                                 |                                                         ||
|                                 |  Market         Opens in 42m . 15-minute delayed        ||
|                                 +---------------------------------------------------------+|
```

The two sentences under each segmented control are the entire reason this
popover exists: **each axis states that it does not affect the other.**
That is the orthogonality invariant (`UI_V2_DESIGN.md` §4.7) made visible
to the user rather than only asserted in code. An implementer must not
remove them for tidiness, and must not add a convenience that couples the
axes.

- Width 480px, anchored to the right edge of the Flight Status button.
- Changing `Who trades` or `Risk profile` applies immediately, shows a
  brief inline confirmation in the popover, and **does not close it** —
  because these are settings a user may change in pairs.
- Switching to `High-Risk` shows one inline consequence line describing
  what changes numerically. It is not a confirmation dialog; nothing has
  been risked yet.
- `Scan now` disables itself with a progress indicator while a scan is
  running, and the popover stays open.

### 1.9 Notifications — inbox and toasts

```
+--------------------------------------------------------------------------------------------+
|                                                                                            |
|  +----------------------------------------------------------+                              |
|  |  NOTIFICATIONS                        All  Unread  Esc   |                              |
|  |                                                          |                              |
|  |  TODAY                                                   |                              |
|  |  (!) Stop approaching - AAPL 190P              9:41 am   |                              |
|  |      $0.40 from your stop at $2.10.        [Open >]      |                              |
|  |                                                          |                              |
|  |  (i) 4 orders filled on SPY                   9:32 am    |                              |
|  |      Expand to see each fill.              [Open >]      |                              |
|  |                                                          |                              |
|  |  ( ) Backtest finished - SPY 25 days          9:02 am    |                              |
|  |                                            [Open >]      |                              |
|  |                                                          |                              |
|  |  YESTERDAY                                               |                              |
|  |  (X) Trading halted - daily loss limit      4:02 pm      |                              |
|  |      Resolved 4:04 pm.                     [Open >]      |                              |
|  +----------------------------------------------------------+                              |
|                                                                                            |
|                                                      +------------------------------------+|
|                                                      |  (i) Order filled                x ||
|                                                      |      1 SPY 470C @ $3.92            ||
|                                                      |                        [View >]    ||
|                                                      +------------------------------------+|
|                                                       + 2 more                             |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

| Surface | Specification |
| --- | --- |
| **Inbox** | 480px panel anchored to the strip's notification control. Grouped by day, then coalesced by subject. Filters: All / Unread. Read state per entry, persisted, and shared with mobile. |
| **Entry** | Severity glyph · title · one-line body · timestamp · `[Open >]`. **Every entry navigates to its subject.** An entry with no destination is a specification error. |
| **Coalescing** | Four fills on one symbol are one expandable entry, not four. |
| **Expiry** | Entries expire on *relevance*, not time: "stop approaching" is removed when the position closes. |
| **Toasts** | Bottom-right, max three stacked, older collapsing to `+ N more`. Informational auto-dismiss at 5s; attention persists until acknowledged. Never covers the ticket, the commit control, or the chain. |
| **Suppression** | A toast never fires for something already visible on screen. A fill that lands in the positions region the user is looking at produces an inbox entry and no toast. |

Severity ladder and the two-case interruption budget are
`UI_V2_DESIGN.md` §10.2 and §10.4 and are binding here.

### 1.10 Modal rules

Modals exist for exactly two things (`UI_V2_DESIGN.md` §10.4): the order
review (§3.7), and a critical condition that changes what the user can do.
Everything else — position detail, expiry selection, column configuration,
provider editing, confirmation of non-financial settings — uses a popover,
a detail rail, or an inline region.

Every modal: title · body · one primary action · one dismissal · `Esc`
closes · focus trapped · focus returned to the opener · background dimmed
and inert · **never nested**.

### 1.11 Shell behaviours

**Loading.** The shell renders immediately with the rail, frame and strip
in place; only content shows skeletons. The Flight Status label reads
`[PAPER . ......]` until the runtime reports. The strip reads
`Connecting...`. **The shell never shows a full-screen spinner.**

**Empty.** Not applicable — the shell always has content.

**Error.** Loss of the backend connection is an app-scoped banner below the
frame: *"Not connected to the OptionsPilot engine. Retrying in 5s. Your
positions are still being managed."* The shell stays interactive; content
regions show their own stale-data markers.

**Motion.** Destination changes **cross-fade content only** at `fast`
(~130ms). The rail's active indicator slides at `fast`. The frame's
destination name swaps without animation. Palette, popovers and panels
scale-and-fade from their trigger at `fast`. Nothing in the shell animates
on data arrival.

**Responsive.** §10.

**Accessibility.** One `banner` (frame), one `navigation` (rail), one
`main`, one `contentinfo` (strip). A skip-to-content link is the first
focusable element. The rail is a list of links with `aria-current` on the
active one. Palette is a combobox with an owned listbox, `aria-activedescendant`
tracking the highlighted row. Popovers and panels trap focus and return it.
**One polite live region** lives in the shell and is the single announcement
channel for the whole app, rate-limited to one utterance per three seconds
(`UI_V2_DESIGN.md` §15.1).

---

## 2. Home

### 2.1 Purpose

Answer three questions in the first second, without interaction and
without scrolling: **what is my state, what needs me, and what is the
context.** Home is not a summary of the app — it is the place a user
returns to in order to find out whether they need to do anything.

### 2.2 Layout — Archetype A (Bands)

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Home   SPY  471.20 +0.84%                       Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Good morning. Markets open in 42m. Nothing needs you.                    |
| (^) Trade    2  |                                                                          |
| ($) Portfoli 3  | +-------------+-------------+-------------+-------------+--------+       |
| (?) Research 4  | | ACCOUNT     | TODAY       | OPEN RISK   | BUYING POWER| WIN    |       |
| (=) Journal  5  | | $10,412.55  | +$212.40    | $840        | $8,900      | 58%    |       |
|                 | | +4.1% total | +2.1% _.-'  | 8.1% acct   | 2 queued    | n=41   |       |
|                 | +-------------+-------------+-------------+-------------+--------+       |
|                 |                                                                          |
|                 | POSITIONS (2)           [Manage >]   | WHAT TO DO NEXT                   |
|                 | SPY  470C 12 Sep  +$142  +18.2% x    |                                   |
|                 | AAPL 190P 19 Sep   -$38   -4.1% x    | (!) Your 0-2 DTE trades win       |
|                 |                                      |     31% over 26 (p=0.004).        |
|                 | WORKING ORDERS (1)                   |     Consider a 7-day floor.       |
|                 | NVDA 900C stop @ 878.00    [edit]    |               [Show me >]         |
|                 |                                      |                                   |
|                 | ---------------------------------    |-----------------------------      |
|                 | EQUITY  [30d] 90d All                | 2 setups cleared the gate         |
| (~) Pilot       |       _.-''-._.-'''-.                | QQQ   long  71%          >        |
| (,) Settings    |  _.-''             '-._              | MSFT  long  66%          >        |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

Band 1 (status line + metrics) and band 2 (positions + what to do next)
**fit above the fold at 1920×1080 and at 1440×900**. Band 3 is the only
part permitted to fall below.

### 2.3 Information hierarchy

Ranked by consequence, which is also the visual weight order
(`UI_V2_DESIGN.md` P4):

1. **The status line** — the only sentence in the product a user is
   entitled to trust completely.
2. **Open risk and Today** — money currently moving.
3. **Open positions and working orders** — the objects that money is in.
4. **What to do next** — the one evidenced thing the system knows.
5. **Account value and buying power** — state, not events.
6. **Equity history and the watchlist** — context. Deliberately last, and
   deliberately the part that may scroll.

Note what this demotes relative to today: the session equity chart moves
from the top of the main column to the bottom band, and AI opportunities
move from a large permanent panel to three lines inside a ranked region.

### 2.4 Regions

| # | Region | Content | Source | Refresh |
| --- | --- | --- | --- | --- |
| H1 | Status line | One generated sentence, grammar fixed as *[time]. [market]. [what needs you]* | A status-line view model in `services/` | On state change only, never on tick |
| H2 | Metrics | Account · Today · Open risk · Buying power · Win rate (with `n`) | `PortfolioService` | WebSocket, value-only update |
| H3 | Positions | Open positions, then working orders as a subsection | `PortfolioService` | WebSocket |
| H4 | What to do next | Max **three** ranked items: risk condition → evidenced finding → cleared setups | `intelligence/`, then the engine | On scan cycle |
| H5 | Equity | 30d default, `[30d] 90d All` segmented | `PortfolioService` | On cycle |
| H6 | Watchlist | Symbol · price · change · AI confidence with the required-confidence tick | Scan results | On cycle |

H6 is shown in the wireframe as the right half of band 3 in the populated
state; at 1920 it sits beside the equity chart.

**Region H4 is the specification-critical one.** Its ranking is computed by
`intelligence/` with the false-discovery correction already in place. The
UI renders the top three of that ranking verbatim and **does not re-rank,
filter, or add a fourth** (`UI_V2_DESIGN.md` P10). If fewer than three
qualify, the region says so.

### 2.5 Navigation

| From | To | Trigger |
| --- | --- | --- |
| H3 position row | Portfolio, that row selected | Click the row |
| H3 `x` | Close-position review modal | Click, then commit gesture |
| H3 `[Manage >]` | Portfolio | Click |
| H3 working order `[edit]` | Portfolio, order selected in the rail | Click |
| H4 finding `[Show me >]` | Journal › Progress, that finding expanded | Click |
| H4 setup row | Trade, symbol set, quick pick pre-populated | Click |
| H5 | — | Not navigational |
| H6 symbol | Trade, symbol set | Click |

Every one of these is a **context handover**: the destination that opens
already has the symbol, the row, or the finding selected. Landing on a
destination and having to find the thing you clicked is a defect.

### 2.6 Interactions

| Element | Interaction | Result |
| --- | --- | --- |
| Status line | None | It is text. It is never a button. |
| Metric card | Hover | Tooltip with the definition and the window it covers |
| Metric card | Click | Navigates to where the number is explained (Today → Portfolio; Win rate → Journal › Progress) |
| Position row | Hover | Row highlight; `x` becomes prominent |
| Position row | Click | Portfolio, selected |
| Position `x` | Click | Review modal for closing, then commit gesture |
| Working order `[edit]` | Click | Portfolio with the order in the detail rail |
| Equity range | Click | Changes range; persisted |
| Finding `[Show me >]` | Click | Journal › Progress |
| Setup row | Click | Trade, populated |
| Watchlist row | Click | Trade, symbol set |

**Nothing on Home places, modifies or closes anything without the review +
commit path.** The `x` is a shortcut to the review, not to the close.

### 2.7 Keyboard

| Key | Action |
| --- | --- |
| `1` | Home (from anywhere) |
| `Tab` / `Shift+Tab` | Move between regions, then within |
| `↑` `↓` | Move within the focused list (positions, findings, watchlist) |
| `Enter` | Activate the focused row |
| `Ctrl+.` | Close the focused position (review + commit) |
| `S` | Scan now |
| `E` | Cycle the equity range |

### 2.8 Loading

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Home                                            Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | ############################################                             |
| (^) Trade    2  |                                                                          |
| ($) Portfoli 3  | +-------------+-------------+-------------+-------------+--------+       |
| (?) Research 4  | | ACCOUNT     | TODAY       | OPEN RISK   | BUYING POWER| WIN    |       |
| (=) Journal  5  | | ##########  | ########    | ######      | #######     | ####   |       |
|                 | | #######     | ########    | ########    | ########    | ####   |       |
|                 | +-------------+-------------+-------------+-------------+--------+       |
|                 |                                                                          |
|                 | POSITIONS                            | WHAT TO DO NEXT                   |
|                 | #################################    | ###########################       |
|                 | #################################    | ####################              |
|                 |                                      | ###########################       |
|                 | Skeleton rows match the exact        |                                   |
|                 | height of the rows they replace,     | ##############                    |
|                 | so nothing moves when data lands.    |                                   |
|                 |                                      |                                   |
|                 | ---------------------------------    |-----------------------------      |
|                 | EQUITY                               | ##############                    |
| (~) Pilot       | #################################    | ##############                    |
| (,) Settings    |                                      | ##############                    |
+-----------------+--------------------------------------------------------------------------+
| Connecting...          Focused  v      --                                 v0.9.2           |
+--------------------------------------------------------------------------------------------+
```

- **Region headings and card labels render immediately.** Only values are
  skeletons. The user can read the structure of the screen while the data
  arrives, which is most of the perceived speed.
- Skeletons appear only after **200ms**, so a warm load never flashes.
- Skeleton rows match the exact height of real rows. Nothing shifts.
- Positions skeleton shows **two** rows regardless of the real count. Row
  count is not knowable before data and guessing it would cause a shift.
- If a region's fetch exceeds **10s**, it converts to its error state
  (§2.10) rather than skeletonising indefinitely.

### 2.9 Empty — the first-run state

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Home                                            Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Welcome. Your paper account has $10,000. None of it is real.             |
| (^) Trade    2  |                                                                          |
| ($) Portfoli 3  | +-------------+-------------+-------------+-------------+--------+       |
| (?) Research 4  | | ACCOUNT     | TODAY       | OPEN RISK   | BUYING POWER| WIN    |       |
| (=) Journal  5  | | $10,000.00  | $0.00       | $0          | $10,000     | --     |       |
|                 | | no trades   | no trades   | no positions|             | 0 of 5 |       |
|                 | +-------------+-------------+-------------+-------------+--------+       |
|                 |                                                                          |
|                 | POSITIONS                            | WHAT TO DO NEXT                   |
|                 |                                      |                                   |
|                 | You have no open positions.          | I need about 5 closed trades      |
|                 | One appears the moment an order      | before I can say anything         |
|                 | fills.                               | about your trading.               |
|                 |                                      |                                   |
|                 | [ Place a practice trade ]           | [ Show me around - 2 min ]        |
|                 |                                      |                                   |
|                 | ---------------------------------    |-----------------------------      |
|                 | EQUITY                               | No setups yet.                    |
| (~) Pilot       | Your equity line starts after        | The AI only sees what you         |
| (,) Settings    | your first scan.  [ Run a scan ]     | point it at.   [ Add + ]          |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . never          Guided   v      0 unread                            v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

Four things this state does that the current Dashboard does not:

1. **Every empty region contains a verb** (`UI_V2_DESIGN.md` P9). Three
   distinct first actions are offered, and they are genuinely different
   next steps rather than the same button repeated.
2. **The win-rate card says `0 of 5`, not `0%`.** A zero win rate on zero
   trades is a false statement about the user.
3. **H4 states the evidence threshold** rather than hiding. "I need about
   5 closed trades" is Pilot's voice, and it sets an expectation the user
   can act on.
4. **The layout is identical to the populated state.** Same bands, same
   regions, same positions. Nothing appears or disappears when the first
   trade fills — values simply replace prompts.

Per-region empty copy, which is normative:

| Region | Copy | Action |
| --- | --- | --- |
| H3 positions | "You have no open positions. One appears the moment an order fills." | `[ Place a practice trade ]` |
| H3 working | Section hidden entirely when zero — a working-orders heading with nothing under it is noise, and unlike positions there is no meaningful first action distinct from placing a trade | — |
| H4 | "I need about 5 closed trades before I can say anything about your trading." | `[ Show me around ]` |
| H5 equity | "Your equity line starts after your first scan." | `[ Run a scan ]` |
| H6 watchlist | "The AI only sees what you point it at." | `[ Add + ]` |

### 2.10 Error

Errors on Home are **region-scoped**. Home never fails as a whole, because
its regions have independent sources.

| Failure | Renders |
| --- | --- |
| Quotes unavailable | Metrics show last known values with a staleness marker: `$10,412.55  as of 9:31 am`. The status line says "Quotes are delayed — Yahoo is rate limited and retrying." |
| Scan failed | H4: "The last scan could not complete — no provider answered. Retrying in 40s." + `[ Diagnostics ]` |
| Intelligence unavailable | H4: "I could not read your trade history just now." + `[ Try again ]`. **Never a silent empty panel** — silence would be indistinguishable from "no findings." |
| Equity history unavailable | H5 shows its empty state with an error note; it does not blank the band. |
| Engine disconnected | App-scoped banner (§1.11). Metrics go stale-marked; positions remain visible. |

**Stale is always marked and never hidden.** A number with an "as of" is
useful; a number that looks live and is not is a defect
(`MARKET_DATA.md`, and `UI_V2_DESIGN.md` §14.7).

### 2.11 Motion

| Event | Motion |
| --- | --- |
| Values updating on tick | **None.** Numbers replace instantly. |
| Status line text changing | Cross-fade at `fast`. It is prose, and a hard swap of a sentence reads as a glitch. |
| A position opening | The new row fades in at `fast`. It does not slide, because sliding pushes the rows below it. |
| A position closing | Row fades out, then the list closes the gap at `fast`. Two steps, so the user sees which row left. |
| Skeleton → content | Cross-fade at `fast`. No slide, no layout change. |
| H4 finding replaced | Cross-fade at `medium` — slower deliberately, because a recommendation silently swapping is disorienting. |
| Equity range change | The line re-draws without animating the path. Axis labels cross-fade. |

### 2.12 Responsive

| Width | Behaviour |
| --- | --- |
| ≥1920 | As drawn. Band 3 shows equity and watchlist side by side. |
| 1440–1919 | As drawn, gutters compress to 16px. Band 3's watchlist drops below the equity chart. |
| 1280–1439 | Rail collapses to icons. Metrics drop to **four** cards; win rate moves into the Journal link. Band 2 stays two columns. |
| 1024–1279 | Metrics drop to **three** (Account, Today, Open risk) with `[ More ]`. Band 2 becomes stacked: positions first, then what-to-do-next. Band 3 fully below the fold. |
| <1024 | §10.3 |

**Band 2 never reverses.** Positions stay above findings at every width,
because consequence ordering does not change with viewport.

### 2.13 Accessibility

- The status line is a `<p>` inside the main landmark, **not** a live
  region. It is announced when Home receives focus, not when it changes —
  changes are announced through the shell's single summary region only if
  they qualify as meaningful events.
- Metric cards are a definition list: label, value, context. Screen-reader
  order is label → value → context, which reads as "Today, plus two
  hundred twelve dollars forty, plus two point one percent."
- Positions are a table with `scope` on headers and a caption stating the
  count.
- P&L cells carry the sign in text, always, and a direction word in their
  accessible name (`"up 18.2 percent"`).
- H4's findings are an ordered list; each item's sample size and p-value
  are inside the item, not in a tooltip, because tooltip-only evidence is
  invisible to assistive technology and to keyboard users.
- Every `x` and `[edit]` has an accessible name naming its subject
  ("Close SPY 470 call"), never just "Close."

### 2.14 Mobile equivalent

Mobile Home (§12) is the same information in a single column, reordered
for a phone's use case: portfolio value and today's change at the top,
equity sparkline, positions, then **one** Pilot line. The metrics band
collapses to two figures; open risk and buying power move to Portfolio.
The status line becomes the screen's first line. What-to-do-next shows one
item, not three.

---

## 3. Trade

### 3.1 Purpose

Get from an intention to a reviewed, committed order with the least
possible cognitive load — and let a user who does not yet know what they
want learn the instrument by using it. This is the most important workflow
in the product.

### 3.2 Layout — Archetype B (Workspace)

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Trade   SPY  471.20 +0.84%                      Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | SPY  1m 5m 15m 1h [1D] 1W  ind draw          | ORDER TICKET              |
| (^) Trade    2  |                                              |                           |
| ($) Portfoli 3  |                       _.-'''-.               | SPY 470 CALL              |
| (?) Research 4  |           _.-'''-._.-'       '-.             | 12 Sep . 7 days . $3.90   |
| (=) Journal  5  |      _.-''                     '-.           |                           |
|                 |  _-'  ---- stop 466.00 ------------          | [ Buy ] [ Sell ]          |
|                 |                                              | Type    Market        v   |
|                 | ------ chart / chain splitter -----          | Qty     [-]  1  [+]       |
|                 | CHAIN [Calls] Puts  12 Sep (7d) v            | TIF     Day           v   |
|                 | STRIKE  BID  ASK DELTA  IV   VOL OI          |                           |
|                 | 465    7.10 7.25  .71 18.2% 4.1k12k          | Cost        $395.00       |
|                 | 468    5.05 5.20  .63 17.9% 8.3k21k          | Max loss    $395.00       |
|                 |>470 *  3.85 3.95  .54 17.6%  22k48k          | Size        3.8% acct     |
|                 | 472    2.70 2.80  .45 17.5%  15k33k          |                           |
|                 | 475    1.60 1.70  .33 17.7%  19k41k          | [ Review order ]          |
|                 |                                              | ------------------------- |
|                 | WATCHLIST SPY QQQ AAPL NVDA MSFT +           | YOUR SPY POSITION         |
| (~) Pilot       |                                              | 470C   +$142   +18.2%     |
| (,) Settings    |                                              | stop 466.00     [Close]   |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

**Three assumptions in the current product this layout rejects:**

1. **That charting and trading are different screens.** They are one
   activity; the collapsible chart in today's Trade tab is evidence the
   product already knew.
2. **That the ticket should appear only after a contract is chosen.** The
   ticket is always present, in all five of its states (§3.5), so the shape
   of the decision is visible before the decision.
3. **That all positions belong on the trading screen.** Only *this
   symbol's* position does. All positions are Portfolio's job. This is what
   removes today's four-things-stacked-in-one-scrolling-column problem.

### 3.3 Information hierarchy

1. **Price** — the chart. Largest region, top-left, the thing being reasoned
   about.
2. **The ticket** — permanently on the right. Second-largest by area at
   rest, and the only region with a primary action.
3. **The chain** — the instrument selector. Below the chart because it is
   consulted after forming a view, not before.
4. **This symbol's position** — bottom-right, under the ticket, because it
   changes what a sensible order is.
5. **The watchlist** — a strip at the bottom-left. Context for changing the
   subject, not part of the decision.

### 3.4 Regions

| # | Region | Content | Notes |
| --- | --- | --- | --- |
| T1 | Chart toolbar | Symbol (display only) · timeframes · indicators · drawings · fullscreen | Symbol is set by `/`, never typed here |
| T2 | Chart | Candles, drawings, indicators; the open position and any working order draw as labelled price lines | Owned by the charting layer. §3.11 forbids adding transitions to it. |
| T3 | Splitter | Draggable, persisted, 55/45 default | Double-click resets |
| T4 | Chain header | `[Calls] Puts` · expiry selector with DTE always shown | |
| T5 | Chain | Column set follows Surface Level (§3.6). Spot-anchored on load. Keyboard-navigable. | |
| T6 | Watchlist strip | Symbols with change colour; `+` adds | Click sets the workspace symbol |
| T7 | Ticket | Five states, §3.5 | Always present |
| T8 | Symbol position | Only positions in the current symbol; empty when none | |

### 3.5 The ticket's five states

```
+--------------------------------------------------------------------------------------------+
| EMPTY - the shape of an order is visible before a contract exists                          |
| SELECTED - live cost, max loss and size                                                    |
|                                                                                            |
|  +---------------------------+   +---------------------------+                             |
|  | ORDER TICKET              |   | ORDER TICKET              |                             |
|  |                           |   |                           |                             |
|  | Nothing selected yet.     |   | SPY 470 CALL              |                             |
|  |                           |   | 12 Sep . 7 days . $3.90   |                             |
|  | QUICK PICK                |   |                           |                             |
|  | [ATM call]  [ATM put]     |   | [ Buy ] [ Sell ]          |                             |
|  | [30 day]    [Weekly]      |   |                           |                             |
|  |                           |   | Type    Limit        v    |                             |
|  | Type    Market       v    |   | Limit   [ 3.90 ]          |                             |
|  | Qty     [-]  1  [+]       |   | Qty     [-]  1  [+]       |                             |
|  | TIF     Day          v    |   | TIF     Day          v    |                             |
|  |                           |   |                           |                             |
|  | Cost    --                |   | Cost      $390.00         |                             |
|  |                           |   | Max loss  $390.00         |                             |
|  | [ Review order ] disabled |   | Size      3.8% of acct    |                             |
|  | Pick a contract first.    |   |                           |                             |
|  |                           |   | [ Review order ]          |                             |
|  |                           |   |                           |                             |
|  +---------------------------+   +---------------------------+                             |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------------------------+
| BLOCKED - two guardrails speaking at once. Each says what changed,                         |
| why, and what to do instead. Neither replaces the domain gate.                             |
|                                                                                            |
|  +--------------------------------------+                                                  |
|  | ORDER TICKET                         |                                                  |
|  |                                      |                                                  |
|  | SPY 470 CALL      [ Buy ] [ Sell ]   |                                                  |
|  |                                      |                                                  |
|  | Type    Market              v        |                                                  |
|  | (i) Stop loss was removed: you have  |                                                  |
|  |     no open SPY position to protect. |                                                  |
|  |     Buy first, or switch to Sell.    |                                                  |
|  |                                      |                                                  |
|  | Qty     [-]  6  [+]                  |                                                  |
|  |                                      |                                                  |
|  | Cost      $2,340.00                  |                                                  |
|  | Size      22.4% of account           |                                                  |
|  | (!) Above your 10% per-position cap. |                                                  |
|  |     Reduce to 2 contracts, or raise  |                                                  |
|  |     the cap in Settings > Trading.   |                                                  |
|  |                                      |                                                  |
|  | [ Review order ]  disabled           |                                                  |
|  +--------------------------------------+                                                  |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

| State | Trigger | Renders |
| --- | --- | --- |
| **Empty** | No contract selected | Full order shape with disabled submit and the reason. Quick picks present. |
| **Selected** | A chain row or quick pick chosen | Contract identity, live cost, max loss, size, enabled submit |
| **Blocked** | A guardrail refuses the combination | The offending control is removed or the field marked, with a `(i)` or `(!)` stating what changed, why, and what to do instead |
| **Review** | `[ Review order ]` | The review modal (§3.7). Ticket stays visible behind, dimmed. |
| **Working** | Commit completed | Brief confirmation, then the ticket returns to Empty **with the symbol and expiry retained** — because the common next action is another order on the same underlying |

**The blocked state's copy is normative, not illustrative.** Removing an
impossible option silently is a different confusion, not less of it — this
is the existing `#tk-kind-why` guardrail generalised. And the UI guardrail
is a *second* gate: `OrderManager` still refuses all three impossible
combinations independently (`CLAUDE.md`).

### 3.6 The chain at two Surface Levels

```
+--------------------------------------------------------------------------------------------+
| Same rows. Same data. Same fills. Same order path. Only the columns differ.                |
|                                                                                            |
|  +---------------------------------------------------------------------+                   |
|  | CHAIN   Guided (Surface Level 1)                                    |                   |
|  |                                                                     |                   |
|  | STRIKE    PRICE    BREAKEVEN    CHANCE IT FINISHES IN THE MONEY     |                   |
|  | 465      $7.18     $472.18      71%                                 |                   |
|  | 468      $5.12     $473.12      63%                                 |                   |
|  | 470 *    $3.90     $473.90      54%      <- nearest the price       |                   |
|  | 472      $2.75     $474.75      45%                                 |                   |
|  | 475      $1.65     $476.65      33%                                 |                   |
|  |                                                                     |                   |
|  | [ Show all columns ]   local reveal - does not change your level    |                   |
|  +---------------------------------------------------------------------+                   |
|                                                                                            |
|  +---------------------------------------------------------------------+                   |
|  | CHAIN   Full (Surface Level 3)                                      |                   |
|  |                                                                     |                   |
|  | STRIKE   BID    ASK    MID   DELTA  GAMMA  THETA    IV     VOL   OI |                   |
|  | 465     7.10   7.25   7.18   .712   .021   -.18   18.2%   4.1k  12k |                   |
|  | 468     5.05   5.20   5.12   .634   .026   -.21   17.9%   8.3k  21k |                   |
|  | 470 *   3.85   3.95   3.90   .541   .029   -.23   17.6%    22k  48k |                   |
|  | 472     2.70   2.80   2.75   .448   .028   -.22   17.5%    15k  33k |                   |
|  | 475     1.60   1.70   1.65   .331   .024   -.19   17.7%    19k  41k |                   |
|  |                                                                     |                   |
|  | [ Columns... ]   spread%, OI change and extrinsic available         |                   |
|  +---------------------------------------------------------------------+                   |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

Four rules:

1. **The row set is identical.** Guided does not filter strikes. A beginner
   who scrolls sees every contract an expert sees.
2. **`CHANCE IT FINISHES IN THE MONEY` is delta, restated.** It is labelled
   as an approximation in its tooltip. It is not a different number and not
   a different model.
3. **`[ Show all columns ]` is a local reveal.** It does not change the
   user's Surface Level (`UI_V2_DESIGN.md` §8.3, mechanism 3).
4. **Spot-anchored on load.** The chain scrolls to and marks the nearest
   strike. A chain that opens at the top of the strike range makes every
   user scroll before thinking.

### 3.7 Review and commit

```
+--------------------------------------------------------------------------------------------+
|                                                                                            |
|             +----------------------------------------------------------------+             |
|             |  Review your order                                        [x]  |             |
|             |                                                                |             |
|             |  You are BUYING 1 SPY $470 call expiring 12 Sep (7 days).      |             |
|             |                                                                |             |
|             |  Cost today             $395.00                                |             |
|             |  Most you can lose      $395.00    (100% of what you pay)      |             |
|             |  Breakeven at expiry    $473.95    SPY is $471.20 now          |             |
|             |  Position size          3.8% of account                        |             |
|             |                                                                |             |
|             |  If you do nothing and SPY closes below $470 on 12 Sep, this   |             |
|             |  contract expires worthless.                                   |             |
|             |                                                                |             |
|             |  Fills against a 15-minute delayed quote on the next cycle.    |             |
|             |                                                                |             |
|             |  +----------------------------------------------------------+  |             |
|             |  |  Hold to place order         [==========>            ]   |  |             |
|             |  +----------------------------------------------------------+  |             |
|             |                                                                |             |
|             |  (i) Delta 0.54 - about a 54% chance this finishes in the      |             |
|             |      money.  Shown at Guided level only.               Pilot   |             |
|             |                                                                |             |
|             |                                            Cancel  (Esc)       |             |
|             +----------------------------------------------------------------+             |
|                                                                                            |
|  Everything behind is dimmed and inert. Esc cancels. Focus is trapped.                     |
+--------------------------------------------------------------------------------------------+
```

**The five required elements, in this order, always** — no order type,
Surface Level or window size removes one:

1. The sentence. Side, quantity, symbol, strike, right, expiry, days. No
   abbreviation on this line.
2. Cost **and** maximum loss, stated separately even when equal.
3. Breakeven with current spot beside it.
4. Position size as a percentage of the account.
5. "If you do nothing" — the passive outcome.

Plus the honesty line about how the fill actually happens.

**The commit control:**

| Property | Specification |
| --- | --- |
| Gesture | Press and hold ~600ms. A fill indicator tracks progress. |
| Early release | Cancels. No dialog, no state, no message beyond the indicator resetting. |
| Keyboard | Hold `Enter`. Identical duration, indicator and cancel. |
| Reduced motion | The fill becomes a stepped progress (4 steps). Duration is unchanged — it is a timing affordance, not decoration. |
| Announcements | Three: started, qualified, placed. |
| After commit | The modal closes, the ticket returns to Empty, the position appears in T8 and in Portfolio. **No success animation, no sound, no celebration.** |
| Failure | The modal stays open and shows an action-scoped error under the control (§8.3). The gesture is re-armable without re-reading. |

**Closing a position uses the identical modal and gesture**, with the
sentence inverted ("You are SELLING…") and "Most you can lose" replaced by
"Proceeds today" plus realised P&L. One review component, two subjects.

### 3.8 Navigation

| From | To | Trigger |
| --- | --- | --- |
| Watchlist strip symbol | Same screen, symbol changed | Click |
| `/` | Same screen, symbol changed | Symbol jump |
| Chart `F` | Chart fullscreen (chain and ticket hidden) | Key |
| T8 `[Close]` | Review modal | Click |
| Chain row | Ticket populated (no navigation) | Click / `Enter` |
| Home setup row | Trade with quick pick applied | From Home |

**Changing the symbol preserves:** timeframe, drawings for the new symbol,
the ticket's order type / quantity / TIF, and the Surface Level.
**Changing the symbol clears:** the selected contract, because a strike on
one underlying is meaningless on another. The ticket returns to Empty and
says so.

### 3.9 Interactions

| Element | Interaction | Result |
| --- | --- | --- |
| Timeframe | Click | Changes workspace timeframe everywhere |
| Chart | Scroll / drag | Zoom / pan. **Never clamped during a user gesture.** |
| Chart | Drag price axis | Manual price scale. Persists until reset. |
| Splitter | Drag | Resizes; persisted |
| Expiry selector | Click | Popover listing expiries with DTE |
| Chain header | Click | Sorts. Sort persists per symbol. |
| Chain row | Hover | Row highlight + the strike marked on the chart |
| Chain row | Click / `Enter` | Selects the contract into the ticket |
| Quick pick chip | Click | Resolves the intent, selects in the chain, populates the ticket, scrolls the chain to the selection |
| `[ Buy ]` / `[ Sell ]` | Click / `B` / `S` | Arms the side; re-runs guardrails |
| Qty stepper | Click / `+` `-` | Changes quantity; cost, max loss and size update instantly |
| Order type | Change | Shows/hides the dependent field; re-runs guardrails |
| `[ Review order ]` | Click / `Enter` | Opens the review modal |
| T8 `[Close]` | Click | Review modal, sell side |

**The chart is the one region whose interaction model is owned elsewhere.**
Its viewport rules (one owner, clamped programmatic moves only, the
depth-counter guard, no ResizeObserver re-clamp) are settled and are
`CLAUDE.md`'s. Nothing in this document changes them.

### 3.10 Keyboard — the full order path

This is the specification that closes today's biggest keyboard gap.

| Key | Action |
| --- | --- |
| `/` | Symbol jump |
| `1`–`6` | Timeframe |
| `Tab` | Chart → chain → ticket → watchlist |
| `↑` `↓` | Move the chain selection |
| `PgUp` / `PgDn` | Move the chain selection by 10 |
| `Home` / `End` | First / last strike |
| `Enter` (chain) | Select the contract, move focus to the ticket |
| `C` / `P` | Calls / puts |
| `[` `]` | Previous / next expiry |
| `B` / `S` | Buy / sell |
| `+` / `-` | Quantity |
| `T` | Cycle order type |
| `Enter` (ticket) | Open review |
| Hold `Enter` (review) | Commit |
| `Esc` | Close review → clear selection → blur |
| `F` | Chart fullscreen |
| `Ctrl+Shift+N` | Pop out the chart |

**Full keyboard path, cold start:** `/ S P Y ⏎` · `↓ ↓ ⏎` · `⏎` · hold `⏎`.
Six discrete actions to a reviewed, committed order.

No shortcut fires while focus is inside a text input except `Esc`. No
shortcut places an order without the hold.

### 3.11 Loading

| Region | Loading state |
| --- | --- |
| Chart | **A skeleton, not a blank canvas.** Axis frame and gridlines render immediately; the plot area is a skeleton block. This closes the reproduced defect where the canvas rendered empty for several hundred ms with no indicator. |
| Chain | Skeleton rows — 8 of them — with real column headers. Already the product's best loading pattern; preserved. |
| Ticket | Never a skeleton. It renders its Empty state instantly; it depends on no fetch. |
| T8 position | Skeleton single row |
| Watchlist strip | Symbols render immediately; prices are skeletons |

The ticket rendering instantly while everything else loads is deliberate:
it is the region a user can start using before data arrives.

### 3.12 Empty

| Condition | Renders |
| --- | --- |
| No contract selected | Ticket Empty state (§3.5). Not an error. |
| Expiry has no strikes | Chain: "No contracts listed for 12 Sep." + `[ Pick another expiry ]` |
| Symbol has no chain at all | Chain: "SPY has no listed options at your data providers." + `[ Diagnostics ]`. This is an error, not an empty — routed to §3.13. |
| No position in this symbol | T8: "No position in SPY." No action offered — the action is the ticket above it. |
| Watchlist empty | Strip shows `[ Add symbols + ]` |

### 3.13 Error

```
+--------------------------------------------------------------------------------------------+
| REGION-SCOPED - the rest of the screen is untouched                                        |
| +----------------------------------------------------------------+                         |
| | CHAIN                                                          |                         |
| |                                                                |                         |
| | (!) No chain for SPY right now.                                |                         |
| |     Twelve Data returned no contracts for 12 Sep. Yahoo is     |                         |
| |     rate limited and retrying in 40s.                          |                         |
| |                                                                |                         |
| |     [ Try again ]   [ Pick another expiry ]   [ Diagnostics ]  |                         |
| +----------------------------------------------------------------+                         |
|                                                                                            |
| ACTION-SCOPED - attached to the control that failed                                        |
| +----------------------------------------------------------------+                         |
| | [ Review order ]                                               |                         |
| | (X) Rejected: buying power $390 short of $2,400 needed.        |                         |
| |     Reduce to 3 contracts, or close a position first.          |                         |
| +----------------------------------------------------------------+                         |
|                                                                                            |
| APP-SCOPED - a banner, only when capability is lost                                        |
| +----------------------------------------------------------------+                         |
| | (X) TRADING HALTED - daily loss limit reached.                 |                         |
| |     Open positions are still managed. New entries are blocked. |                         |
| |                                     [ Reset halt (override) ]  |                         |
| +----------------------------------------------------------------+                         |
+--------------------------------------------------------------------------------------------+
```

**Every error names the provider and the actual cause.** The market-data
layer produces typed, distinguishable errors, including the 401-vs-403
distinction that cost real diagnostic work to earn. Flattening them into
"something went wrong" discards that work and, worse, a confidently wrong
cause is more harmful than an admitted unknown (`MARKET_DATA.md` §41).

Trade-specific error mappings:

| Condition | Scope | Message shape |
| --- | --- | --- |
| Chart history unavailable | Region (chart) | "No history for SPY at 1D from any provider. Yahoo is rate limited; Stooq is offline." + `[ Try again ]` `[ Diagnostics ]` |
| Chain unavailable | Region (chain) | As drawn |
| Quote stale during ticket edit | Inline in ticket | "Cost is based on a quote from 9:31 am." Submit stays enabled — the fill is against a delayed quote anyway, and disabling would be theatre. |
| Order rejected by `RiskManager` | Action | The reason string, verbatim, plus a suggested remedy |
| Order rejected by `OrderManager` | Action | As above |
| Halt active | App banner + ticket disabled with the reason | Both. The banner explains; the ticket says why *this* control is off. |

### 3.14 Motion

| Event | Motion |
| --- | --- |
| Chain row selection | Background change at `instant`. No slide. |
| Quick pick → chain scroll | Scroll to the selection at `fast`, **only if it is out of view**. A selection already visible does not scroll the list. |
| Ticket Empty → Selected | Fields cross-fade at `fast`. Field positions do not move — the two states share a layout so nothing jumps. |
| Guardrail removing an option | The option disappears instantly; the `(i)` explanation fades in at `fast`. The explanation is the motion, not the removal. |
| Review modal | Scale-and-fade from the `[ Review order ]` button at `medium`. |
| Commit fill | The only `deliberate` (~600ms) motion in the product. |
| After commit | Modal fades at `fast`; the new position fades into T8. **No pulse, no highlight, no sound.** |
| Chart | **Exempt entirely.** No transitions, transforms or observers may be added to the chart canvas (`CLAUDE.md`). |
| Splitter drag | No transition — direct manipulation follows the pointer exactly. |

### 3.15 Responsive

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot  Trade   SPY 471.20            Ctrl+K   [PAPER . AI idle]                     |
|+------+---------------------------------------+----------------------+                     |
|| (o)1 | SPY  1h [1D] 1W   ind  draw           | [Ticket] Chain       |                     |
|| (^)2 |                                       |                      |                     |
|| ($)3 |              _.-'''-.                 | SPY 470 CALL         |                     |
|| (?)4 |     _.-'''-.'        '-.              | 12 Sep . 7 days      |                     |
|| (=)5 |                                       |                      |                     |
||      |                                       | [Buy] [Sell]         |                     |
||      |                                       | Type   Market   v    |                     |
||      |                                       | Qty    [-] 1 [+]     |                     |
||      |                                       |                      |                     |
||      |                                       | Cost      $395.00    |                     |
|| (~)  |                                       | Max loss  $395.00    |                     |
|| (,)  |                                       | [ Review order ]     |                     |
|+------+---------------------------------------+----------------------+                     |
| The chart never becomes a tab. It is the thing the other two are about.                    |
+--------------------------------------------------------------------------------------------+
```

| Width | Behaviour |
| --- | --- |
| ≥1920 | As §3.2. Optional fourth region (all positions) may be revealed by the user; off by default. |
| 1440–1919 | As §3.2. Ticket column at its 340px minimum. |
| 1280–1439 | Rail icon-only. Chain and ticket become a **tabbed pair** in the right column; the chart keeps the full left column. Selecting a chain row auto-switches to the Ticket tab — the one place the tabbing is allowed to move the user, because it is the direct result of their action. |
| 1024–1279 | As above, ticket column 300px, chain columns reduce to Strike / Price / Chance / Delta regardless of Surface Level. |
| <1024 | §10.3 |

**The chart is never tabbed away.** It is the subject; the chain and ticket
are the instruments acting on it.

### 3.16 Accessibility

- The chain is a table with `scope="col"` headers, a caption naming symbol
  and expiry, `aria-rowcount`, and `aria-selected` on the selected row.
- The chain is a single tab stop with roving `tabindex` — arrow keys move
  the selection, `Tab` leaves the table. A 40-row table with 40 tab stops
  is unusable.
- Each chain row's accessible name is a sentence: *"470 call, $3.90, about
  a 54 percent chance of finishing in the money"* — not a concatenation of
  cell values.
- Quick-pick chips have accessible names describing the outcome ("Select
  the at-the-money call") rather than the label alone.
- The ticket is a form with a legend naming the contract. Every field has
  a real label. Guardrail messages are `aria-describedby` on the field they
  concern **and** in a polite live region, because a removed option is a
  change the user did not initiate.
- The commit control is a button with `aria-describedby` pointing at the
  "hold to place" instruction, and announces its three moments.
- The chart canvas has a text alternative summarising the visible window:
  *"SPY, 1 day candles, 12 Jun to 4 Aug, 471.20, up 0.84 percent."*
  Drawings and price lines are enumerated in an adjacent visually-hidden
  list, because a canvas is otherwise entirely opaque.

### 3.17 Mobile equivalent

Mobile Trade (§12) is the same flow in sequential steps rather than
simultaneous regions: chart → intent (`[ Buy a call ]` / `[ Buy a put ]`) →
expiry → strike → ticket sheet → Commit Rail. The chain shows three columns
only. The Greeks are available behind a disclosure on the contract, not in
the list. Order types beyond market and limit are absent from mobile
entirely, and the app says where they are.

---

## 4. Portfolio

### 4.1 Purpose

Answer "what do I hold, what is it doing, and what can I do about it" with
every position, working order and today's closes in one place — and make
managing a position a two-click operation rather than a hunt.

This destination does not exist today. Its content lives in three places:
the Dashboard's positions panel, the Trade tab's stacked
positions/working/history sections, and the Journal's closed trades.
Consolidating them is what allows the Trade screen to stop being four
things in a column.

### 4.2 Layout — Archetype C (Index + Detail)

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Portfolio   SPY  471.20 +0.84%                  Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | $10,412.55      +$212.40 today      $840 at risk (8.1% of account)       |
| (^) Trade    2  |                                                                          |
| ($) Portfoli 3  | [ Open 2 ]  [ Working 1 ]  [ Closed today 3 ]  [ All ]                   |
| (?) Research 4  | -------------------------------------------- |-----------------------    |
| (=) Journal  5  | CONTRACT        QTY  MARK    P&L      %   DTE| SELECTED                  |
|                 |>SPY  470C 12 Sep  1  3.92  +$142  +18.2%   7 |                           |
|                 | AAPL 190P 19 Sep  1  0.88   -$38   -4.1%  14 | SPY 470 CALL              |
|                 |                                              | 12 Sep . 7 days           |
|                 | WORKING ORDERS                               |                           |
|                 | NVDA 900C  stop 878.00  GTC        [edit]    | Entry      $3.50          |
|                 |                                              | Mark       $3.92          |
|                 | CLOSED TODAY                                 | P&L       +$142           |
|                 | QQQ  400C  +$88  target hit    10:14 am      | Stop       466.00         |
|                 | TSLA 250P  -$61  stop           11:02 am     | Managed    by you         |
|                 | META 500C  +$31  manual close    1:20 pm     |                           |
|                 |                                              | [ Close position ]        |
|                 | EXPOSURE BY SYMBOL                           | [ Adjust stop ]           |
| (~) Pilot       | SPY  ####################  46%               | [ Open in Trade ]         |
| (,) Settings    | AAPL ##############        32%               | [ See in Journal ]        |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

### 4.3 Information hierarchy

1. **Total exposure** — the summary line, and specifically "at risk" as a
   dollar amount and a percentage. This is the number no current screen
   states.
2. **Open positions** — the largest region.
3. **The selected position's detail and actions** — the right rail.
4. **Working orders** — resting instructions, second because they are
   conditional.
5. **Closed today** — recent outcomes, third.
6. **Exposure by symbol** — concentration, last, because it is a check
   rather than an action.

### 4.4 Regions

| # | Region | Content |
| --- | --- | --- |
| P1 | Summary line | Account value · today's P&L · at-risk dollars and percent |
| P2 | Filter segments | `Open` `Working` `Closed today` `All`, each with its count |
| P3 | Positions table | Contract · qty · mark · P&L · % · DTE. Sortable. |
| P4 | Working orders | Contract · type · level · TIF · `[edit]` |
| P5 | Closed today | Contract · P&L · exit reason · time |
| P6 | Exposure by symbol | Horizontal bars, percent of at-risk capital |
| P7 | Detail rail | Identity, entry, mark, P&L, stop, `managed_by`, four actions |

**`Managed by` is displayed on every position and is not cosmetic.** An AI
position (`managed_by="ai"`) shows its manual actions as disabled with the
reason: *"This position is managed by the AI. Switch to You trade in
Flight Status to take it over."* This is the `managed_by` discipline
(`CLAUDE.md`) made visible rather than merely enforced.

### 4.5 Navigation

| From | To |
| --- | --- |
| Position row | Selects into P7 (no navigation) |
| P7 `[ Open in Trade ]` | Trade, symbol set, that contract selected in the ticket |
| P7 `[ See in Journal ]` | Journal › Trades, filtered to that symbol (closed positions) |
| P5 closed row | Journal › Trades, that trade selected |
| P6 bar | Filters P3 to that symbol |
| Home position row | Here, that row selected |

### 4.6 Interactions

| Element | Interaction | Result |
| --- | --- | --- |
| Filter segment | Click | Filters P3–P5. Persisted per session. |
| Column header | Click | Sort. Persisted. |
| Row | Click | Select into the rail |
| Row | Double-click | Open in Trade |
| Row | Hover | Highlight; inline `[ Close ]` appears at the row end |
| `[ Close position ]` | Click | Review modal (sell side) → commit gesture |
| `[ Adjust stop ]` | Click | Inline editor **in the rail**, not a modal: a numeric field, the current underlying price for reference, `[ Save ]` / `Esc`. Saving requires the commit gesture because it changes risk. |
| `[edit]` on a working order | Click | Same inline editor pattern |
| Working order row | `Del` | Cancel-order review → commit gesture |
| P6 bar | Click | Filters |

**Every action that changes money or risk passes through review + commit.**
Adjusting a stop qualifies: it changes the loss the user has accepted.

### 4.7 Keyboard

| Key | Action |
| --- | --- |
| `3` | Portfolio |
| `↑` `↓` | Move selection |
| `Enter` | Open in Trade |
| `Ctrl+.` | Close the selected position |
| `Ctrl+,` | Adjust the selected position's stop |
| `Del` | Cancel the selected working order |
| `1`–`4` | Filter segments |
| `/` | Symbol jump (also filters if the symbol is held) |

### 4.8 Loading

Summary line and table headers render immediately. P3 shows three skeleton
rows; P4 and P5 show one each; P6 shows skeleton bars. The detail rail
shows "Select a position" rather than a skeleton — it has nothing to load
until a selection exists.

### 4.9 Empty

```
POSITIONS
You have no open positions.
Positions appear here the moment an order fills.
[ Place a trade ]

WORKING ORDERS
No resting orders.
A stop or a target you set on a position shows here until it triggers.

CLOSED TODAY
Nothing closed today.

EXPOSURE
Nothing at risk.

SELECTED (detail rail)
Select a position to manage it.
```

The rail keeps its width when empty (§0.2 rule 2). The exposure region
keeps its heading and states "Nothing at risk" rather than disappearing,
because a missing region reads as a rendering fault.

### 4.10 Error

| Failure | Renders |
| --- | --- |
| Marks unavailable | Positions render with entry prices and a marker: *"Marks are from 9:31 am — quotes are unavailable."* P&L cells show `--` with the same marker. **P&L is never computed from a stale mark without saying so.** |
| Position list unavailable | Region error with `[ Try again ]`. This is severe: the app cannot tell the user what they hold, so it also raises a critical notification. |
| Close rejected | Action-scoped under the commit control in the review modal |
| Stop adjustment rejected | Action-scoped under the inline editor, with the reason |

### 4.11 Motion

| Event | Motion |
| --- | --- |
| Row selection | Background at `instant`; the rail's content cross-fades at `fast` |
| Position closing | Row fades, list closes the gap at `fast`, the trade fades into "Closed today" — a two-step move that shows where it went (`UI_V2_DESIGN.md` §12.1, question 2) |
| P&L updating | **None.** |
| Exposure bars | Width changes without transition, for the same reason values do not animate |
| Filter change | Content cross-fade at `fast`. The table does not re-sort visibly. |

### 4.12 Responsive

| Width | Behaviour |
| --- | --- |
| ≥1440 | As drawn |
| 1280–1439 | Rail narrows to 280px; DTE column drops from P3 |
| 1024–1279 | The detail rail becomes an **overlay** anchored right, opened by selection and dismissed with `Esc`. The table gets the full width. |
| <1024 | §10.3 |

### 4.13 Accessibility

- P3, P4 and P5 are separate tables, each with a caption stating its
  contents and count. Three small captioned tables beat one table with
  section rows for screen-reader navigation.
- Selection is `aria-selected`; the rail is `aria-live="off"` but is
  labelled by the selected row so a screen reader reads the position name
  on arrival.
- The exposure bars are a table with visually-hidden numeric cells. A bar
  chart with no text equivalent is invisible.
- Disabled actions on AI-managed positions carry their reason in
  `aria-describedby`, not only in a tooltip.
- `[ Adjust stop ]`'s inline editor moves focus into the field and returns
  it to the button on save or cancel.

### 4.14 Mobile equivalent

Mobile Portfolio is a list of position cards; tapping opens a detail sheet
(§12, M2 right). Swipe-left on a card reveals Close and Adjust, and both
still route through the Commit Rail. Working orders and closed-today are
sections below the positions list. Exposure by symbol is omitted from
mobile — it is a check, not an action, and it belongs on a screen with
room.

---

## 5. Research

### 5.1 Purpose

Answer "is this idea any good" before committing to it — and expose what
the engine believes and why. Research is the only destination that
contains no order path, and that is a feature: it is where a user thinks
without the ability to act impulsively.

It absorbs three of today's tabs: Charts (as exploratory charting),
Backtest, and Learning (renamed to Engine — the single worst mislabel in
the current product, because a beginner reads "Learning" as education and
is met with bounded evidence weights).

### 5.2 Layout — Archetype D (Sections)

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Research   SPY  471.20 +0.84%                   Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Explore  | SPY  1m 5m 15m 1h [1D] 1W   ind  draw     [ Trade > ]         |
| (^) Trade    2  | Backtest |                                                               |
| ($) Portfoli 3  | Watchlist|                        _.-'''-.                               |
| (?) Research 4  | Engine   |            _.-'''-._.-'       '-.                             |
| (=) Journal  5  |          |       _.-''                     '-.                           |
|                 |          |                                                               |
|                 |          | ---------------------------------------------------           |
|                 |          | AI VERDICT                                    SPY             |
|                 |          | Confidence 71%   required 65%   direction long                |
|                 |          | passed   trend . volume . momentum                            |
|                 |          | failed   volatility regime                                    |
|                 |          |                                      [ Explain > ]            |
|                 |          | ---------------------------------------------------           |
|                 |          | SYMBOL FACTS                                                  |
|                 |          | Mkt cap 428B . vol 71.2M . IV rank 34%                        |
|                 |          | Earnings in 12 days                                           |
|                 |          |                                                               |
| (~) Pilot       |          | (i) This is the same verdict the engine trades on.            |
| (,) Settings    |          |     It is not a recommendation to you.                        |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

The `(i)` line at the bottom is normative. The AI verdict is what the
engine acts on in AI mode; presenting it in Human mode without that
distinction would make the app appear to be advising a person, which it is
not.

### 5.3 Section — Backtest

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Research > Backtest                             Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Explore  | Symbol [SPY   ]  Days [25]  Min conf [    ]   [ Run ]         |
| (^) Trade    2  | Backtest | Runs bar-by-bar through the same engine, risk manager         |
| ($) Portfoli 3  | Watchlist| and broker as paper trading.                                  |
| (?) Research 4  | Engine   | ---------------------------------------------------           |
| (=) Journal  5  |          | RESULT    SPY . 25 days . 18 trades . 3.1s                    |
|                 |          | +-----------+-----------+-----------+-----------+             |
|                 |          | | NET P&L   | WIN RATE  | PROFIT F. | MAX DD    |             |
|                 |          | | +$412.20  | 61%  n=18 | 1.84      | -6.2%     |             |
|                 |          | +-----------+-----------+-----------+-----------+             |
|                 |          |                                                               |
|                 |          |        _.-''''-._.-'''-.                                      |
|                 |          |  _.-''                 '-._.-'                                |
|                 |          |                                                               |
|                 |          | TRADES                              [ Export CSV ]            |
|                 |          | 03 Aug  long  +$41  target   conf 72%                         |
|                 |          | 04 Aug  long  -$22  stop     conf 66%                         |
|                 |          | 05 Aug  long  +$67  target   conf 74%                         |
| (~) Pilot       |          |                                                               |
| (,) Settings    |          | (i) Options are Black-Scholes priced from realized vol.       |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

The `Symbol` field here is the one exception to "no second symbol input" —
and it is not an exception in practice: it is **pre-filled from the
workspace symbol** and changing it changes the workspace symbol. It is a
display of the workspace symbol with an editing affordance, exactly like
the frame's context slot.

The documented limitation line is required, not optional. A backtest that
reports a profit factor without saying how options were priced is
overstating its own authority.

### 5.4 Section — Watchlist

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Research > Watchlist                            Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Explore  | Type a ticker, or paste a whole list from anywhere            |
| (^) Trade    2  | Backtest | [ AAPL, TSLA, NVDA...                            ]            |
| ($) Portfoli 3  | Watchlist| [Big Tech] [Index ETFs] [Most active] [S&P leaders]           |
| (?) Research 4  | Engine   | ---------------------------------------------------           |
| (=) Journal  5  |          | 6 symbols    [ search... ]        sort [ AI conf  v ]         |
|                 |          | = *  SYM   COMPANY       PRICE    CHG    VOL   CONF           |
|                 |          | = *  SPY   S&P 500 ETF   471.20  +0.8%   71M  ### 71%         |
|                 |          | =    QQQ   Nasdaq ETF    402.11  +1.1%   42M  ### 66%         |
|                 |          | =    NVDA  NVIDIA        892.10  +1.4%   38M  ##  48%         |
|                 |          | =    AAPL  Apple         189.44  -0.3%   51M  #   31%         |
|                 |          | =    MSFT  Microsoft     412.90  +0.2%   22M  ##  55%         |
|                 |          | =    TSLA  Tesla         248.10  -1.2%   88M  #   22%         |
|                 |          |                                                               |
|                 |          | * pins to top . = drags to reorder . click selects            |
|                 |          | Ctrl+click multi . Del removes . saves automatically          |
|                 |          |                                                               |
| (~) Pilot       |          | (i) Prices refresh after each scan cycle, not live.           |
| (,) Settings    |          |                                                               |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

This is management, not consultation. Consultation happens in the Trade
strip and on Home. The current tab's capabilities — paste a list, presets,
pin, drag, multi-select, sort by seven keys, auto-save — are preserved
exactly; only the location changes.

The `CONF` column keeps the existing required-confidence tick, which is
one of the genuinely good pieces of self-documenting UI in the current
product and should not be lost in the move.

### 5.5 Section — Engine

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Research > Engine                               Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Explore  | What the engine has inferred from your own history.           |
| (^) Trade    2  | Backtest | Read-only. Explicit config always overrides a weight.         |
| ($) Portfoli 3  | Watchlist| ---------------------------------------------------           |
| (?) Research 4  | Engine   | EVIDENCE WEIGHTS                  v12 . 41 trades             |
| (=) Journal  5  |          | EVIDENCE     DEFAULT  LEARNED  EFFECTIVE   n                  |
|                 |          | trend          1.00     1.18      1.18    41                  |
|                 |          | volume         1.00     0.82      0.82    41                  |
|                 |          | momentum       1.00      --       1.00     8   (i)            |
|                 |          |   (i) below the minimum sample - not learned yet              |
|                 |          | Bounded 0.25x-2x . moves <= 20% per cycle                     |
|                 |          | ---------------------------------------------------           |
|                 |          | BY HOUR (ET)            BY CONFIDENCE BUCKET                  |
|                 |          | 09  ####   58%  n=12    50-60  ##     41%   n=7               |
|                 |          | 10  #####  64%  n=14    60-70  ####   62%   n=16              |
|                 |          | 11  ##     39%  n=9     70-80  #####  71%   n=14              |
|                 |          | 14  #      --   n=3     80+     --     --    n=4              |
|                 |          |                                                               |
| (~) Pilot       |          | (i) No rate is shown below n=5. An empty cell is a            |
| (,) Settings    |          |     sample size, not a zero.                                  |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

Three specification points:

1. **Visible at Surface Level 3+ only.** At Levels 1–2 the section is
   absent from the section rail, and the palette entry for "evidence
   weights" says *"Available at the Full surface level. [ Switch ]"* rather
   than 404-ing. Hiding is fine; pretending it does not exist is not.
2. **The `--` in the LEARNED column with `(i)` is required.** A learned
   weight that has not met its sample floor must not render as `1.00` in
   the learned column, because that is indistinguishable from "learned, and
   the answer was 1.00."
3. **`n<5` shows no rate.** The bottom `(i)` states this explicitly on the
   screen, so a sparse cell is never read as a bad hour.

The weight table must be read from the same `WeightStore` the live scorer
uses. The current build's Learning tab reads a CWD-relative path and can
show a different file than the engine loaded — a defect fixed in V0.7.0 and
guarded by `tests/test_architecture.py`. This screen inherits that
guarantee and must not reintroduce a second source.

### 5.6 Information hierarchy, per section

| Section | 1st | 2nd | 3rd |
| --- | --- | --- | --- |
| Explore | The chart | The AI verdict with its passed/failed evidence | Symbol facts |
| Backtest | The four result metrics | The equity curve | The trade list |
| Watchlist | The list | Add/preset controls | Sort and search |
| Engine | Effective weights | Sample sizes | Breakdowns |

### 5.7 Navigation

| From | To |
| --- | --- |
| Section rail | Section (URL/state persisted; returning to Research restores the last section) |
| Explore `[ Trade > ]` | Trade, symbol and timeframe carried |
| Explore `[ Explain > ]` | Pilot panel with the verdict explained |
| Backtest trade row | Nothing — backtest trades are not journal trades and must not appear to be |
| Watchlist row | Trade, symbol set |
| Engine weight row | Pilot explanation of that evidence type |

### 5.8 Interactions, keyboard, loading, empty, error

**Interactions.** Standard for each section; the notable ones: `[ Run ]`
disables itself with an inline progress readout while running and the
section stays interactive; Watchlist drag-reorder writes immediately;
Engine is entirely read-only and every control is a disclosure.

**Keyboard.** `4` opens Research. `Alt+↑` `Alt+↓` move between sections.
Within Backtest, `Ctrl+Enter` runs. Within Watchlist, the existing model
(click, `Ctrl+click`, `Ctrl+A`, `Del`) is preserved and `↑` `↓` move
selection.

**Loading.** Chart per §3.11. Backtest shows a determinate progress line
with the bar being processed, because a 3-second wait with no readout feels
broken. Watchlist renders symbols immediately and skeletons prices. Engine
shows skeleton rows with real headers.

**Empty.**

| Section | Empty copy | Action |
| --- | --- | --- |
| Explore | "No symbol selected." | `[ Pick a symbol ]` → symbol jump |
| Backtest | "No backtest yet. A backtest replays the engine over past bars so you can see what it would have done." | `[ Run one on SPY ]` |
| Watchlist | "Your watchlist is empty. The AI only scans what you point it at." | Preset chips |
| Engine | "The engine has not learned anything yet. It needs about 20 closed trades per evidence type." | `[ See your trades ]` |

**Error.** Region-scoped throughout. Backtest failure states the reason
(insufficient history, provider failure, invalid range) and keeps the form
populated so the user can adjust rather than retype.

### 5.9 Motion, responsive, accessibility

**Motion.** Section changes cross-fade content at `fast`; the section
rail's indicator slides at `fast`. Backtest results fade in as one block
when complete — never progressively, which would make partial results look
final. The equity curve draws without path animation.

**Responsive.** ≥1440 as drawn. 1280–1439: section rail narrows to labels
only. 1024–1279: the section rail becomes a horizontal segmented control
above the content. <1024: §10.3.

**Accessibility.** The section rail is a tab list with `aria-controls`;
content is a tab panel. The AI verdict's passed/failed lists are real lists,
not comma-joined strings, so each item is navigable. Engine tables carry
`scope` and captions; the `n` column is never visually hidden, because
sample size is the point. Bar charts (by hour, by bucket, exposure) always
have their numeric values in text.

### 5.10 Mobile equivalent

**Research does not exist on mobile.** Per `UI_V2_DESIGN.md` §14.1 the
phone is a companion: monitor, manage, place simple orders. Backtesting,
engine transparency and watchlist management are desktop work. Mobile
Settings contains one line naming where they are. Attempting to carry them
is how mobile trading apps become unusable.

---

## 6. Journal

### 6.1 Purpose

Answer "what have I done, and what should I learn from it." Journal is
where the product's intelligence pays off, and it is the destination the
Learning Trader persona lives in.

It absorbs today's Journal tab and the entire Coach tab, and it takes the
intelligence panel that currently sits collapsed at the top of the
Dashboard.

### 6.2 Layout — Archetype D + C

Three sections: **Trades** (Archetype C inside D), **Review** (a queue),
**Progress** (the intelligence surface).

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Journal                                         Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Trades   | 41 closed . +$1,204 net . 58% win (n=41) . PF 1.71            |
| (^) Trade    2  | Review 2 | [ sym ] [ all dirs v ] [ wins+losses v ]   [ Export ]         |
| ($) Portfoli 3  | Progress | -------------------------------|-------------------           |
| (?) Research 4  |          | DATE   SYM   DIR    P&L   EXIT | SPY 470 CALL                 |
| (=) Journal  5  |          |>04 Aug SPY   long  +$142 targ  | bought 02 Aug 9:41           |
|                 |          | 03 Aug AAPL  short  -$38 stop  | sold   04 Aug 2:10           |
|                 |          | 02 Aug QQQ   long   +$88 targ  |                              |
|                 |          | 01 Aug TSLA  short  -$61 stop  | WHY IT WAS TAKEN             |
|                 |          | 31 Jul META  long   +$31 man   | conf 71% . req 65%           |
|                 |          |                                | trend volume momentum        |
|                 |          |                                |                              |
|                 |          |                                | COACH REVIEW                 |
|                 |          |                                | Held 22 min past the         |
|                 |          |                                | target. Same as 6 of         |
|                 |          |                                | your last 10 winners.        |
|                 |          |                                |                              |
|                 |          |                                | PATTERN                      |
| (~) Pilot       |          |                                | 4th of 26 trades under       |
| (,) Settings    |          |                                | 2 DTE.          [ > ]        |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

The detail rail's three stacked blocks are the design: **what the engine
thought · what the coach observed · what the pattern engine has since
learned.** Today these live on three different tabs. Putting them on one
row of one screen is the point of the consolidation, because they are three
views of one event.

The `Review 2` label in the section rail carries a count of unreviewed
closed trades. It is the only count in the navigation, and it is permitted
because it represents work the user has said they want to do (reviewing) —
not an inbox the product invented.

### 6.3 Section — Progress

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Journal > Progress                              Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Trades   | Measured over 41 closed trades since 12 Jun.                  |
| (^) Trade    2  | Review 2 | +------------+------------+------------+-----------+          |
| ($) Portfoli 3  | Progress | | DISCIPLINE | TIMING     | RISK       | OVERALL   |          |
| (?) Research 4  |          | | 72     B-  | not enough | 64     C+  | 68    B-  |          |
| (=) Journal  5  |          | | 4 of 4 in  | evidence   | 3 of 4 in  | 61% cov   |          |
|                 |          | +------------+------------+------------+-----------+          |
|                 |          |    Why? >     Why not? >    Why? >       Why? >               |
|                 |          | ---------------------------------------------------           |
|                 |          | WHAT THE NUMBERS SAY                                          |
|                 |          | (!) 0-2 DTE: 31% win over 26 trades (p=0.004)   [>]           |
|                 |          | (!) You size up after a loss: +38% avg          [>]           |
|                 |          | ( ) Hesitation - cannot be measured here. Why? >              |
|                 |          | ---------------------------------------------------           |
|                 |          | GOALS                      ACHIEVEMENTS                       |
|                 |          | Keep every stop   18/20    Reviewed 10 trades                 |
|                 |          | Review each trade 10/41    30 days, no override               |
|                 |          | ---------------------------------------------------           |
| (~) Pilot       |          | YOUR PROGRESS      _.-''-._.-'''   win rate, 30d              |
| (,) Settings    |          |                                                               |
+-----------------+--------------------------------------------------------------------------+
| Yahoo . 12s ago        Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

**This screen is where the evidence convention is most visible, and every
detail of it is normative:**

- `TIMING` reads **"not enough evidence"** where a score would be, with a
  `Why not? >` disclosure. It does **not** show a grey card, a zero, a
  dash, or a hidden panel.
- Every score card states its coverage (`4 of 4 in`, `61% cov`). A
  composite below the coverage floor refuses to produce a number at all.
  This is the direct fix for the recorded "Discipline 100/100, grade A on
  20% coverage" incident (`CLAUDE.md`).
- `( ) Hesitation` is listed as **permanently unassessable, with a reason
  available** — not omitted. Omitting it would let a user assume it was
  measured and clean.
- Findings carry `n` and `p` **in the visible text**, not in a tooltip.
- Goals show progress as `18/20`, never as a percentage bar alone, because
  a bar without its denominator hides the sample.
- Achievements describe **discipline**, never **activity**. No achievement
  may be earned by trading more.

`Why? >` opens the Pilot panel with the component breakdown. This is the
highest-value Pilot surface in the product: it turns a grade into an
explanation on demand, without volunteering one.

### 6.4 Section — Review

A queue of closed trades awaiting the user's own reflection, one per
screen: the trade, the coach's observation, the engine's original
reasoning, and a free-text note field. `[ Next ]` advances; `[ Skip ]`
defers without penalty. When the queue empties: *"Nothing to review. New
closed trades appear here."*

The queue never nags. There is no badge on the destination itself, only the
section count, and no notification is raised for an unreviewed trade.

### 6.5 Information hierarchy

**Trades:** summary stats → the trade list → the selected trade's three
blocks → filters.
**Progress:** coverage statement → score cards → findings → goals →
timeline.
**Review:** the trade → the coach's observation → the note field.

The coverage statement is deliberately *above* the score cards. A grade
read before its coverage is a grade that will be over-trusted.

### 6.6 Navigation, interactions, keyboard

| From | To |
| --- | --- |
| Trade row | Selects into the rail |
| Rail `PATTERN [ > ]` | Progress, that finding expanded |
| Progress finding `[>]` | Trades, filtered to the trades that produced the finding |
| Progress `Why? >` | Pilot panel |
| Home `[Show me >]` | Progress, that finding expanded |
| Portfolio `[ See in Journal ]` | Trades, filtered to that symbol |

The finding→trades link is important: a claim about the user must be
inspectable down to the individual trades that support it. A statistic you
cannot drill into is a statistic you are asked to take on faith, which
`intelligence/` explicitly refuses to ask for.

**Keyboard.** `5` opens Journal. `Alt+↑` `Alt+↓` change section. `↑` `↓`
move the trade selection. `Enter` expands the rail's pattern link. `E`
exports. In Review, `N` next, `S` skip, `Ctrl+Enter` saves the note.

### 6.7 Loading, empty, error

**Loading.** Summary line and headers immediately; five skeleton rows;
score cards render as skeleton cards **with their titles visible**, so the
user knows which four dimensions exist before the numbers land.

**Empty.**

| Region | Copy | Action |
| --- | --- | --- |
| Trades | "No closed trades yet. A trade appears here when a position closes." | `[ Place a trade ]` |
| Review | "Nothing to review." | — |
| Progress | "I need about 5 closed trades before I can score anything. You have 2." | `[ See your open positions ]` |
| Goals | "Goals appear once there is enough history to set them fairly." | — |

The Progress empty state states the exact threshold and the current count.
"Not enough data" without a number is a dead end.

**Error.** Region-scoped. If `intelligence/` fails, Progress says *"I could
not read your trade history just now"* with `[ Try again ]` — never an
empty screen, which would be indistinguishable from "you have no patterns."

### 6.8 Motion, responsive, accessibility

**Motion.** Row selection at `instant`; rail cross-fade at `fast`. Score
cards fade in together, never staggered — staggering implies sequence where
there is none. The progress timeline draws without path animation. Findings
appearing or changing use `medium`, matching Home's H4.

**Responsive.** ≥1440 as drawn. 1280–1439: rail narrows; the `EXIT` column
drops. 1024–1279: rail becomes a right overlay; Progress score cards become
a 2×2 grid. <1024: §10.3.

**Accessibility.** Score cards are a definition list: dimension → score →
coverage. The unassessable card's accessible name is *"Timing: not enough
evidence to score"*, never *"Timing: blank"*. Findings are an ordered list
with `n` and `p` inside the item text. The trade table carries `scope` and
a caption with the filtered count. `Why?` disclosures are buttons, and if
they are ever implemented with a native disclosure element, **the clickable
target is the summary, not the container** — a real bug found in this
repo's own checks, where clicking the wrong element produced a test that
passed while testing nothing (`CLAUDE.md`).

### 6.9 Mobile equivalent

Mobile Journal shows closed trades as cards with P&L and exit reason;
tapping opens the three blocks stacked. Progress shows the score cards in a
2×2 grid and the top two findings. Review is available and is genuinely
good on a phone — it is reading and reflecting, which is exactly what a
phone is for.

---

## 7. Settings

### 7.1 Purpose

Configure the machine, with each group answering a different question, and
with every setting stating what it affects. Settings is not a dumping
ground; a setting that cannot be assigned to one of the five groups is a
setting whose purpose has not been decided.

### 7.2 Layout — Archetype D (Sections)

```
+--------------------------------------------------------------------------------------------+
| OptionsPilot    Settings > Data                                 Ctrl+K   [PAPER . AI idle] |
+-----------------+--------------------------------------------------------------------------+
| (o) Home     1  | Trading  | MARKET DATA PROVIDERS                                         |
| (^) Trade    2  | Automatn | Ranked live by health and latency. Drag to change             |
| ($) Portfoli 3  | Data     | priority; priorities stay 10 apart on purpose.                |
| (?) Research 4  | Appearnc | ---------------------------------------------------           |
| (=) Journal  5  | About    | = 1  Yahoo         ok        210ms   no key needed            |
|                 |          |       98% of 412 requests this hour       [ ^ ][ v ]          |
|                 |          | = 2  Twelve Data   ok        340ms   key ****4f2a             |
|                 |          |       780 of 800 daily calls left         [ ^ ][ v ]          |
|                 |          | = 3  Finnhub       no plan    --     key ****9c11             |
|                 |          |   (!) Your key is valid. The free plan does not               |
|                 |          |       include candles. Contributes no history depth.          |
|                 |          |                                          [ Fix > ]            |
|                 |          | = 4  Stooq         offline    --     no key                   |
|                 |          |       Answers with a challenge page. Benched.                 |
|                 |          | ---------------------------------------------------           |
|                 |          | [ Add a provider ]  [ Rebuild cache ]  [ Diagnostics ]        |
|                 |          |                                                               |
| (~) Pilot       |          | (i) This panel stops refreshing while you type in it          |
| (,) Settings    |          |     and while this window is in the background.               |
+-----------------+--------------------------------------------------------------------------+
| Twelve Data . 3s ago   Focused  v      3 unread  (!)                       v0.9.2          |
+--------------------------------------------------------------------------------------------+
```

### 7.3 The five groups

| Group | Contains | Notes |
| --- | --- | --- |
| **Trading** | Risk parameters, position sizing, daily loss limit, custom-mode advanced settings | Changing a risk parameter requires the commit gesture, because it changes what the system will do with money |
| **Automation** | Operating mode, scan cadence, background workload, window-close behaviour, launch at startup, start minimised, restore workspace, resume monitoring | Operating mode is *also* in Flight Status; this is the one deliberate duplicate, and both write the same runtime setting |
| **Data** | Providers, keys, ordering, quotas, cache maintenance, diagnostics | As drawn |
| **Appearance** | Theme, Surface Level, density, motion, text size, accessibility preferences | Surface Level is also in the system strip; same rule as operating mode |
| **About** | Version, updates, storage locations, export, licences, the documented limitations | The limitations belong in the UI, not only in the docs |

**The two deliberate duplicates are exceptions to §1.4's one-owner rule and
are named here so they cannot spread.** Both are settings whose *control*
appears in two places while the *fact* has one source. Any third instance
requires a decision, not a convenience.

### 7.4 The Data group's normative behaviours

These are not new; they are existing behaviours that must survive the
redesign, each of which exists because of a specific failure:

1. **Polling pauses while focus is inside the panel**, and half-typed
   values are captured before any re-render and restored after. Without it,
   an auto-refresh landing mid-paste wipes an API key with no explanation.
2. **Polling stops when the window is not visible.** A settings page
   fetching from a background window becomes a meaningful share of the
   traffic in the system it is reporting on, and on a metered provider that
   traffic is budget.
3. **Keys are masked everywhere.** The panel shows `****4f2a`. There is no
   reveal control, and nothing on this screen may be added to an export or
   a diagnostics payload without a corresponding leak test
   (`CLAUDE.md`, `credentials.py`).
4. **A benched provider is still listed, still explains itself, and can
   still be re-enabled.** A provider that is not constructed cannot explain
   itself, which leaves the settings page blind exactly where a user needs
   to act.
5. **The order shown is `registry.ranking()`, rendered verbatim.** The page
   does not compute its own ranking. A page that derived its own order
   would eventually disagree with the chart about which provider goes
   first, and that disagreement is undebuggable from either side.
6. **Reordering keeps priorities 10 apart.** Renumbering to 1, 2, 3 would
   collapse dynamic ordering silently, because 10 rank points is calibrated
   to one second of latency.
7. **Finnhub's `no plan` state says the key is valid.** 401 and 403 are
   different failures and conflating them told users to regenerate a
   working key, repeatedly. The copy in the wireframe is the corrected
   wording and is normative.

### 7.5 Interactions, keyboard, states

**Interactions.** Every setting applies immediately and confirms inline
(`Saved` next to the control, fading at `medium`); there is no global Save
button. Exceptions are the risk parameters in Trading, which apply on an
explicit `[ Apply ]` with the commit gesture. Destructive actions (rebuild
cache, reset paper account, clear journal) require typed confirmation of
the object's name **and** the commit gesture.

**Keyboard.** `,` opens Settings. `Alt+↑` `Alt+↓` change group. `Tab`
moves through controls in visual order. The palette can jump directly to a
group or to a named setting.

**Loading.** Groups and control labels render immediately; live values
(provider health, quotas, latency) are skeletons. **Controls are disabled
until their current value is known**, so a user cannot toggle a switch away
from a value they have not yet seen.

**Empty.** No provider configured beyond the keyless default:
*"You are running on Yahoo alone. Yahoo rate-limits by IP, so charts may
pause during heavy use. Adding a free key from another provider gives the
app somewhere to fail over to."* + `[ Add a provider ]`. This is honest
about the actual state of the world: with no keys there is exactly one real
source (`CLAUDE.md`).

**Error.** Setting write failure: inline under the control, with the old
value restored and stated. A corrupt preferences file **never blocks
startup**; the app starts with defaults and Settings shows a banner:
*"Your preferences file could not be read and defaults were used. The old
file was kept at …"* Losing preferences is acceptable; failing to start is
not.

### 7.6 Motion, responsive, accessibility

**Motion.** Group changes cross-fade at `fast`. Toggles animate at
`instant`. Provider reordering: the dragged row follows the pointer with no
transition; other rows shift at `fast`. Inline `Saved` confirmations fade
in at `fast` and out at `medium`.

**Responsive.** As Research (§5.9).

**Accessibility.** Groups are a tab list. Every control has a real label
and a description naming what it affects. Provider rows are a table with
drag handles that are also keyboard-operable (`Space` to lift, arrows to
move, `Space` to drop, `Esc` to cancel) — drag-only reordering is
inaccessible. Masked keys announce as "API key, hidden, ending 4f2a."
Destructive confirmations state consequences in the accessible name, not
only in adjacent text.

### 7.7 Mobile equivalent

Mobile Settings has three groups: Automation (notifications, background),
Appearance (theme, Surface Level, text size), About. Trading and Data are
desktop-only, and the mobile screen says so with a line naming where they
are — because a settings screen that silently lacks a group reads as a bug.

---

## 8. Shared state catalogue

Component-level state contracts referenced by every screen above. A
component that does not define all four states is not finished.

### 8.1 Loading

| Rule | Specification |
| --- | --- |
| L-1 | Structure renders immediately; only values are skeletons. Headings, labels, column headers and controls are never skeletonised. |
| L-2 | Skeletons appear after 200ms, so warm loads never flash. |
| L-3 | A skeleton has the exact height of the content it replaces. Nothing shifts on arrival. |
| L-4 | Row counts in a skeleton are fixed (2 for positions, 5 for trades, 8 for a chain) and never guessed from prior state. |
| L-5 | A fetch exceeding 10s converts to the error state. Indefinite skeletons are a bug. |
| L-6 | Spinners are used only for *actions* with unknown duration (a scan, a backtest, a cache rebuild), never for *content*. |
| L-7 | No full-screen loading state exists anywhere in the product. |

### 8.2 Empty

| Rule | Specification |
| --- | --- |
| E-1 | Three parts: what belongs here · why it is not here · the one action that fills it. |
| E-2 | Every empty state contains a verb. |
| E-3 | Empty states are quiet — one or two lines and a single control. No illustrations, no large icons; emptiness is not an event. |
| E-4 | The layout does not change between empty and populated. Regions keep their position and size. |
| E-5 | A region that is genuinely not applicable (working orders when zero) may be omitted; a region that is applicable but has no data may not. |
| E-6 | Where a threshold exists, state it numerically: "5 closed trades; you have 2." |

### 8.3 Error

Three scopes, and choosing the right one is most of the design:

| Scope | When | Form |
| --- | --- | --- |
| **Region** | One data source failed; the rest of the screen is valid | Replaces the region's content. Keeps the heading. Offers retry and a diagnostic route. |
| **Action** | A user-initiated operation was refused | Appears attached to the control, below it, persists until the input changes |
| **App** | A capability is lost | Banner below the frame, persists until resolved |

| Rule | Specification |
| --- | --- |
| X-1 | Three parts: what failed · what it means now · what to do. |
| X-2 | Name the actual cause, including the provider. Never "an error occurred." |
| X-3 | Never state a cause that has not been established. An admitted unknown beats a confident wrong answer, because the user will act on the wrong answer. |
| X-4 | Stale data is shown, marked, and timestamped — never silently replaced by an error, and never shown as if live. |
| X-5 | An error never blanks a region that could show last-known values. |
| X-6 | A retry control states what it will do and disables itself while retrying. |
| X-7 | Errors that a user cannot act on are logged and shown in Diagnostics, not surfaced as banners. |

### 8.4 Success

| Rule | Specification |
| --- | --- |
| S-1 | Success is stated, not celebrated. No confetti, no sound, no badge. |
| S-2 | The best confirmation is the changed state itself: a placed order appears as a position. |
| S-3 | Where the state change is not visible, an inline confirmation appears next to the control and fades at `medium`. |
| S-4 | A toast is used only when the changed state is off-screen. |

---

## 9. Consolidated keyboard map

Every binding in the product. `?` displays this map; the palette lists each
binding beside its command.

| Scope | Key | Action |
| --- | --- | --- |
| Global | `1`–`5` | Destinations |
| Global | `,` | Settings |
| Global | `Ctrl+K` | Command palette |
| Global | `/` | Symbol jump |
| Global | `Ctrl+/` | Pilot panel |
| Global | `Ctrl+N` | Notification inbox |
| Global | `?` | Keyboard reference |
| Global | `Esc` | Cancel, in order: armed tool → popover → panel → modal → selection |
| Global | `Alt+1`–`4` | Surface Level |
| Global | `Alt+↑` `Alt+↓` | Previous / next section (Archetype D) |
| Global | `Ctrl+Shift+N` | Pop out the focused region |
| Home | `S` | Scan now |
| Home | `E` | Cycle equity range |
| Trade | `1`–`6` | Timeframe |
| Trade | `C` / `P` | Calls / puts |
| Trade | `[` / `]` | Previous / next expiry |
| Trade | `↑` `↓` | Chain selection |
| Trade | `Enter` | Select contract → open review |
| Trade | `B` / `S` | Buy / sell |
| Trade | `+` / `-` | Quantity |
| Trade | `T` | Cycle order type |
| Trade | `F` | Chart fullscreen |
| Review | Hold `Enter` | Commit |
| Portfolio | `↑` `↓` | Selection |
| Portfolio | `Ctrl+.` | Close selected |
| Portfolio | `Ctrl+,` | Adjust stop |
| Portfolio | `Del` | Cancel selected working order |
| Research | `Ctrl+Enter` | Run backtest |
| Journal | `N` / `S` | Review queue: next / skip |
| Journal | `E` | Export |

**Three invariants.** No binding fires while focus is in a text input
except `Esc`. No binding places, closes or modifies an order without the
commit gesture. Every binding is discoverable from `?` and from the
palette.

---

## 10. Responsive matrix

### 10.1 Breakpoints

| Width | Rail | Chrome | Content |
| --- | --- | --- | --- |
| **≥1920** | 200px, labels + numbers | Full | All regions simultaneous; optional extra regions available |
| **1440–1919** | 200px, labels + numbers | Full | All regions; gutters 16px |
| **1280–1439** | 72px, icons + numbers | Flight Status drops the market clause | Archetype C rails narrow; Trade tabs chain/ticket; Home drops to 4 metrics |
| **1024–1279** | 56px, icons only | Flight Status drops the engine clause | Archetype C rails overlay; Home drops to 3 metrics and stacks band 2; chain reduces columns |
| **<1024** | — | — | §10.3 |

### 10.2 What is never sacrificed

At every supported width:

- Open positions and open risk remain visible without navigation.
- The commit gesture and the review's five elements are unchanged.
- The nav rail's five destinations remain visible as targets.
- The status line remains complete.
- No region gains a horizontal page scroll — wide content scrolls inside
  its own container.

### 10.3 Below the minimum

```
+--------------------------------------------------------------------------------------------+
|        +--------------------------------------------------+                                |
|        |                                                  |                                |
|        |   OptionsPilot needs a wider window.             |                                |
|        |                                                  |                                |
|        |   The trading workspace needs at least 1024px.   |                                |
|        |   This window is 880px.                          |                                |
|        |                                                  |                                |
|        |   Resize the window, or use OptionsPilot on      |                                |
|        |   your phone for positions and quick orders.     |                                |
|        |                                                  |                                |
|        |   Your positions are still being managed.        |                                |
|        |                                                  |                                |
|        +--------------------------------------------------+                                |
| Never a degraded single column. A stated minimum is honest; a broken                       |
| layout is not. Background work is unaffected and the message says so.                      |
+--------------------------------------------------------------------------------------------+
```

The last line is the important one: a user who shrinks the window must not
be left wondering whether their stops are still being watched.

### 10.4 Multi-monitor

Pop-out regions are the chart, the ticket, and Portfolio. Each becomes a
real OS window that shares the workspace context (`UI_V2_DESIGN.md` §13.5).
A popped-out chart follows the main window's symbol unless pinned; a pinned
window shows a pin marker in its own frame so the difference is visible.
Window geometry, display assignment and pop-out state persist through
`RuntimeSettings`.

A popped-out region leaves a placeholder in its home layout — *"Chart is
open in another window. [ Bring back ]"* — rather than silently
re-proportioning the destination, which would make the main window's layout
change meaning depending on invisible state.

---

## 11. Motion catalogue

Durations and easing are `UI_V2_DESIGN.md` §12.2–12.3. This is the complete
list of animations permitted in the product. **An animation not on this
list requires a decision, not an implementation.**

| # | Animation | Duration | Answers |
| --- | --- | --- | --- |
| M-1 | Destination content cross-fade | `fast` | "The screen changed" |
| M-2 | Rail / section indicator slide | `fast` | "You are here now" |
| M-3 | Popover, palette, panel scale-and-fade from trigger | `fast` | "This came from that" |
| M-4 | Skeleton → content cross-fade | `fast` | "The data arrived" |
| M-5 | Row enter (fade) | `fast` | "This is new" |
| M-6 | Row exit (fade, then gap closes) | `fast` ×2 | "This left, and here is where it was" |
| M-7 | Detail rail content cross-fade | `fast` | "The selection changed" |
| M-8 | Modal scale-and-fade from its opener | `medium` | "This came from that button" |
| M-9 | Finding / recommendation replacement | `medium` | "The advice changed" — slower on purpose |
| M-10 | Inline save confirmation fade | `fast` in, `medium` out | "That registered" |
| M-11 | Guardrail explanation fade-in | `fast` | "Something was removed, here is why" |
| M-12 | Commit gesture fill | `deliberate` | "You are committing; you can still stop" |
| M-13 | Toast enter / exit | `fast` | "Something happened elsewhere" |
| M-14 | Chain scroll to selection (only when out of view) | `fast` | "Your selection is here" |

**Prohibited, explicitly:** value change animation of any kind · number
roll-ups · attention pulses, glows or shakes · staggered list entrances ·
layout reflow animation · parallax · bounce or elastic easing · progress
animation on data arrival · any transition, transform or observer on the
chart canvas · success celebrations.

**Reduced motion:** M-1 through M-11, M-13 and M-14 become instant state
changes. M-12 keeps its duration and becomes a four-step progress rather
than a continuous fill, because it is a timing affordance, not decoration.

---

## 12. Mobile equivalents

Mobile is specified here only to the depth needed to keep the desktop
design honest about what it is promising. The full mobile specification
follows the hosting decisions in `ARCHITECTURE-MOBILE.md` §17.

```
+--------------------------------------------------------------------------------------------+
| Four destinations. Pilot is a header affordance, never a fifth tab.                        |
|                                                                                            |
|  +----------------------------------------+  +----------------------------------------+    |
|  |  9:41                       .ull  ##   |  |  9:41                       .ull  ##   |    |
|  |                                        |  |                                        |    |
|  |  Portfolio                      (~)    |  |  < SPY   471.20  +0.84%         (~)    |    |
|  |  $10,412.55                            |  |                                        |    |
|  |  +$212.40   +2.1% today                |  |      _.-'''-._.-'''-.                  |    |
|  |                                        |  |  _.-'                '-.               |    |
|  |     _.-'-._.-''-._.-'''-._             |  |  1D  1W  1M                            |    |
|  | _.-'                     '-.           |  |                                        |    |
|  |  1D  1W  1M  3M  1Y  ALL               |  |  [ Buy a call ]   [ Buy a put ]        |    |
|  |                                        |  |                                        |    |
|  |  POSITIONS                             |  |  12 Sep (7d)  19 Sep (14d)  26 Sep >   |    |
|  |  SPY 470C  12 Sep    +$142  +18.2% >   |  |                                        |    |
|  |  AAPL 190P 19 Sep     -$38   -4.1% >   |  |  STRIKE      PRICE     CHANCE ITM      |    |
|  |                                        |  |  468        $5.12          63%    >    |    |
|  |  PILOT                                 |  |  470        $3.90          54%    >    |    |
|  |  Your 0-2 DTE trades win 31% of        |  |  472        $2.75          45%    >    |    |
|  |  the time, over 26 trades.       >     |  |  475        $1.65          33%    >    |    |
|  |                                        |  |                                        |    |
|  |  ---------------------------------     |  |  ---------------------------------     |    |
|  |   Home   Trade  Portfolio  Journal     |  |   Home   Trade  Portfolio  Journal     |    |
|  |    o       .        .         .        |  |    .       o        .         .        |    |
|  +----------------------------------------+  +----------------------------------------+    |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------------------------+
| The rail restates cost, then max loss, as the thumb travels.                               |
|                                                                                            |
|  +----------------------------------------+  +----------------------------------------+    |
|  |                                        |  |  9:41                       .ull  ##   |    |
|  |                                        |  |                                        |    |
|  |  +---------------------------------+   |  |  < SPY 470 CALL                 (~)    |    |
|  |  |            ---                  |   |  |  12 Sep . 7 days                       |    |
|  |  |  SPY 470 CALL                   |   |  |                                        |    |
|  |  |  12 Sep . 7 days                |   |  |  +$142        +18.2%                   |    |
|  |  |                                 |   |  |                                        |    |
|  |  |  Contracts     [-]  1  [+]      |   |  |  Entry            $3.50                |    |
|  |  |                                 |   |  |  Mark             $3.92                |    |
|  |  |  Cost             $395.00       |   |  |  Stop             466.00               |    |
|  |  |  Max loss         $395.00       |   |  |  Managed by       you                  |    |
|  |  |  Breakeven        $473.95       |   |  |                                        |    |
|  |  |  Size          3.8% of acct     |   |  |  [ Close position ]                    |    |
|  |  |                                 |   |  |  [ Adjust stop ]                       |    |
|  |  |  If SPY closes below $470 on    |   |  |                                        |    |
|  |  |  12 Sep this expires worthless. |   |  |  swipe left on the row for the same    |    |
|  |  |                                 |   |  |  two actions without opening this      |    |
|  |  |  +---------------------------+  |   |  |                                        |    |
|  |  |  | (>)  Slide to buy         |  |   |  |  ---------------------------------     |    |
|  |  |  +---------------------------+  |   |  |   Home   Trade  Portfolio  Journal     |    |
|  |  |  Release early to cancel.       |   |  |    .       .        o         .        |    |
|  |  +---------------------------------+   |  +----------------------------------------+    |
|  +----------------------------------------+                                                |
|                                                                                            |
+--------------------------------------------------------------------------------------------+
```

### 12.1 Desktop → mobile mapping

| Desktop | Mobile |
| --- | --- |
| Home (3 bands) | Home: value, sparkline, positions, one Pilot line |
| Trade (simultaneous regions) | Trade: sequential steps, ending at the Commit Rail |
| Portfolio (index + rail) | Portfolio: cards + detail sheet |
| Research | **Absent.** Settings names where it is. |
| Journal | Journal: trade cards, Progress as a 2×2 grid, Review |
| Settings (5 groups) | Settings: 3 groups |
| Nav rail (5 + 2) | Bottom bar (4); Pilot in the header; Settings behind the profile control |
| Command palette | Search in the Home header |
| Flight Status | A row on Home |
| System strip | Absent; freshness shown per value |
| Hold-to-confirm | Slide-to-confirm (the Commit Rail) |
| Review modal | The ticket sheet, with the same five required elements |

### 12.2 The Commit Rail's five differences from its inspiration

Analysed in `UI_V2_DESIGN.md` §14.5 and restated here as build
requirements: the label restates cost then max loss as the thumb travels ·
travel distance scales with position size relative to the account · a
single haptic tick at the commit point and nowhere else in the app · no
success animation, the screen simply becomes the position · a `Place order`
button always exists alongside for assistive technology and for anyone who
cannot perform the gesture.

### 12.3 Gestures

Every gesture has a visible equivalent. Swipe-left on a position reveals
Close and Adjust; tapping the row opens the same actions in a sheet. Pull
to refresh on Home; automatic on foreground. Long-press for quick actions;
tap for the sheet. **Horizontal swipe between bottom-nav destinations is
not used** — it would make every horizontal gesture inside a destination
(chart pan, strike scroll) ambiguous.

---

## 13. What an implementer must not invent

The list of decisions that are *made*, and the list that are *not*. If
something is on neither list, it is a gap in this document and should be
raised rather than resolved locally.

### 13.1 Settled — do not re-decide

1. Six destinations, five numbered, Settings on `,`.
2. Four layout archetypes. A new screen picks one.
3. One frame, one rail, one system strip, one content area. Only content
   scrolls.
4. Exactly one symbol input in the product (§1.6).
5. Flight Status and the system strip own disjoint facts (§1.4). Two named
   exceptions in §7.3; no third.
6. The ticket is always present, in five states.
7. The review's five required elements, in order, always.
8. The commit gesture on every consequential action, with the keyboard
   equivalent.
9. Modals for two things only.
10. Max three items in Home's "what to do next"; the ranking comes from
    `intelligence/` and is rendered verbatim.
11. Every statistic shows its sample size; insufficient evidence renders as
    a stated reason, never as a zero or a blank.
12. The animation list in §11 is closed.
13. The chart canvas is exempt from this design system's motion and layout
    rules; its viewport ownership is settled elsewhere.
14. `<1024px` shows a message, never a degraded layout.
15. Research does not exist on mobile.

### 13.2 Open — needs a decision before the phase that depends on it

These are `UI_V2_DESIGN.md` §19's open decisions, narrowed to the ones that
block a layout:

| # | Question | Blocks |
| --- | --- | --- |
| 1 | Does Pilot get an LLM? | §1.7's ask field. Without it, the panel is answers and follow-ups only, and the field is replaced by a list of answerable topics. |
| 4 | Density default at Levels 3–4 | Row heights in every table |
| 5 | Real OS windows or in-app panes for pop-outs? | §10.4 |
| 6 | Does Surface Level sync across devices? | §12's Settings group |
| 8 | Is Research one destination or two? | §5's section rail |

### 13.3 Verification each phase must ship

`index.html` has no automated test coverage; the browser check scripts are
the only thing standing between a UI change and a silent regression. Each
implementation phase adds checks in the existing style:

| Screen | Must assert |
| --- | --- |
| Shell | Palette opens, ranks, and navigates; every old tab name resolves; the strip and Flight Status show no duplicated fact |
| Home | No vertical scroll for bands 1–2 at 1920×1080 and 1440×900; a 2-trade history renders coverage reasons, not numbers; every empty region contains a verb |
| Trade | The six-action keyboard path completes with no mouse; the chain is spot-anchored on load; every guardrail message names what changed and what to do; `OrderManager` still refuses what it refused before |
| Portfolio | AI-managed positions disable manual actions with a stated reason; stale marks are labelled |
| Research | Engine section absent below Level 3 and its palette entry explains rather than 404s; weights read from the same store as the scorer |
| Journal | An unassessable dimension renders its reason; findings link to the trades that produced them; disclosures are clicked on their summary, not their container |
| Settings | Polling pauses on focus and on hidden; a malformed preferences file starts the app with defaults |
| All | Zero console errors; contrast AA in both themes at all four Surface Levels; no interactive element without an accessible name |

---

## 14. Related documents

| Document | Relationship |
| --- | --- |
| `UI_V2_DESIGN.md` | The vision this implements. Authority for principles, personas, the design system's philosophy, Pilot's behaviour, the roadmap and success metrics. |
| `CLAUDE.md` | Binding constraints. Paper-only, gate discipline, `managed_by`, the known traps this document's error and data states are built around. |
| `ONBOARDING.md` | The guided-help layer preserved by these screens, including the ids-only frontend/backend contract. |
| `TRADING_INTELLIGENCE.md` | The evidence rules §0.5 and §6.3 render. |
| `MARKET_DATA.md` | The typed error vocabulary §8.3 must not flatten. |
| `WORKSPACE_ARCHITECTURE.md` | Why layout, context and Surface Level are server-owned. |
| `ARCHITECTURE-MOBILE.md` | The hosting decisions §12 depends on. |
| `ARCHITECTURE.md`, `MODULES.md` | Where these screens attach. |
| `ROADMAP-V3-UX.md` | The audit that preceded both documents. |
