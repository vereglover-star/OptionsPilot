# UI_V2_DESIGN.md — the OptionsPilot Human Interface Guidelines

**Status:** proposed specification, not yet implemented. **Version:** 1.0.
**Supersedes:** the open findings in `ROADMAP-V3-UX.md` (H5, N2, N4 and
"Long-term ideas"), which were an audit of the existing screens. This
document is not an audit. It defines the interface OptionsPilot should
have, and the existing screens are evaluated against it rather than the
other way round.

**Scope.** This is a product-design and interaction specification. It
contains no implementation code, no markup and no stylesheets by design.
It is the authority for *what* the interface does and *why*; the
engineering docs (`ARCHITECTURE.md`, `ARCHITECTURE-PLATFORM.md`,
`MODULES.md`) remain the authority for how it is built.

**What it does not change.** OptionsPilot is paper-trading only, and
nothing in this document proposes otherwise. Every gate described here —
`RiskManager` on entry, `OrderManager` on execution, the `managed_by`
separation between AI and manual positions, the double flag on live
trading — is preserved exactly. Where this document adds a guardrail, it
adds a *second* one in the interface; it never replaces the first one in
the domain. See `CLAUDE.md`.

---

## 0. How to read this document

### 0.1 The three questions

Every design decision in this document is accountable to three questions,
and any decision that cannot answer all three has not earned its place:

1. **How does this help a first-time trader?**
2. **How does this make an experienced trader faster?**
3. **How does this reinforce that OptionsPilot is one unified trading
   operating system, not a collection of tools?**

Major decisions carry the answers inline, in this form:

> **First-time trader —** the answer.
> **Experienced trader —** the answer.
> **One system —** the answer.

§18 collects every major decision into a single audit table so the
document can be checked against itself.

### 0.2 The vocabulary this document introduces

| Term | Meaning |
| --- | --- |
| **Destination** | One of the six primary places a user can be (§4). Not a "tab" — a tab is a widget; a destination is an answer to a question. |
| **Surface Level** | How much of the interface is currently revealed (§8). A presentation-only axis. Never a risk axis. |
| **The status line** | The single sentence at the top of every destination that states what is true right now (§3.1, §5.2). |
| **Commit gesture** | The deliberate physical act that places an order — hold-to-confirm on desktop, slide-to-confirm on mobile (§6.6, §14.5). |
| **Pilot** | The mentor layer (§9). A role in the product, not a chat window. |
| **Context** | The symbol, timeframe, expiry and contract currently under the user's attention, carried across every destination (§4.5). |

---

## 1. Vision

### 1.1 What OptionsPilot should feel like

**A calm instrument, not a busy dashboard.**

Options trading is an activity where the emotional stakes arrive before
the financial ones. A beginner opens the app already slightly afraid —
of the jargon, of pressing the wrong thing, of the possibility that
everyone else understands something they don't. An experienced trader
opens it in a hurry, mid-thought, with a specific intention that the
software is either helping or obstructing. Both of them are badly served
by an interface that presents everything it knows at equal volume.

OptionsPilot should feel like the moment before takeoff in a
well-designed cockpit: everything you need is in front of you, nothing is
shouting, the important things are where you expect them, and the aircraft
tells you the truth about its state without editorialising. That is the
whole of the aesthetic ambition. It is not a visual style. It is a
*posture*.

### 1.2 The flight-deck philosophy — felt, not seen

We borrow the *discipline* of a flight deck. We borrow none of its
appearance. There are no bezels, no gauges, no aviation typefaces, no
artificial horizons, no runway striping, no "altitude" metaphors in the
copy, and the word "cockpit" never appears in the product. If a user can
point at the screen and say "that's the airplane thing," we have done it
wrong.

What we take:

| Flight-deck principle | How it shows up in OptionsPilot |
| --- | --- |
| **Calm under pressure** | Nothing changes appearance because the market moved. Losses are rendered with the same visual weight as gains. Urgency is expressed by *position in the hierarchy*, never by colour temperature, flashing or size increase. |
| **Intentional information hierarchy** | Every screen has exactly one primary reading, one secondary band and one reference band. Nothing is placed because there was room. |
| **Progressive disclosure** | Instruments are grouped by how often they are consulted, not by how impressive they are. Detail is one deliberate gesture away and never in the default view. |
| **Immediate situational awareness** | The first second of looking at any screen answers: what is my exposure, is anything wrong, and is anything waiting for me. |
| **Guidance without distraction** | Advisory information is available continuously and interrupts never — except for the two cases in §10.4. |
| **Confidence through clarity** | The instrument states what it measured and what it did not. It never fills an unmeasured value with a plausible one. |

That last row is the one this codebase has already committed to, and it
is why the metaphor fits rather than being decorative. `intelligence/`
already refuses to score a dimension it cannot evidence — a metric is
`None`, not `0`; a behaviour is `assessable=False` with the reason quoted,
not `detected=False` (`TRADING_INTELLIGENCE.md` §4.1). A flight
instrument that cannot measure shows a flag, not a zero. The backend
already behaves this way. **The interface's job in V2 is to stop hiding
that integrity behind a layout that renders everything as an equally
confident number.**

### 1.3 Emotional goals, stated per state

A product identity that is only described in the happy state is not an
identity. These are the five states that matter and what each must feel
like:

| State | Must feel | Must never feel |
| --- | --- | --- |
| **Idle** (market closed, nothing open) | Restful. A calm surface with a clear invitation to learn or research. | Empty, broken, or like the app is waiting for you to fix something. |
| **Scanning** (a cycle is running) | Quietly industrious. Progress is legible and unobtrusive. | Blocking. The user must never wait on a scan to do anything else. |
| **In position** (money at risk) | Attentive. Exposure is the most prominent fact on screen. | Anxious. No pulsing, no red washes, no countdown drama. |
| **Drawdown** (losing) | Honest and steady. The number is exact, the context is offered, the tone does not change. | Punishing or consoling. Neither scolding copy nor a cheerful "you've got this." |
| **Error** (a provider failed, an order was rejected) | Specific and actionable. What failed, what it means, what to do. | Vague. "Something went wrong" is a bug, not a message. |

The drawdown row is a real design constraint with teeth. Most retail
platforms shift their entire visual temperature when a portfolio is down,
which trains users to feel the interface rather than read it.
OptionsPilot renders `-$1,240.00` with exactly the same typographic
weight, spacing and animation profile as `+$1,240.00`. Only the sign, the
semantic colour role and the directional glyph differ.

### 1.4 The product philosophy, in one line

> **Professional enough for experienced traders.
> Welcoming enough for someone placing their first option trade.**

The failure mode this line guards against is not "too complex." It is
*bifurcation*: shipping two products in one binary — a simple one that
lies by omission, and a complex one that is the real product. The
resolution is stated in §3.2 as a principle and enforced in §8 as a
mechanism: **complexity is progressively revealed, never separately
implemented.** A beginner and a professional are looking at the same
screen with different amounts of it revealed. They are never looking at
different screens.

### 1.5 Explicit anti-goals

The interface is not, and will not become:

- **A casino.** No streak celebrations, no confetti, no sounds on profit,
  no gamified progression that rewards trading frequency. The existing
  achievements in `intelligence/` reward *discipline* (a maintained stop
  rule, a completed review) and never *activity*; the UI must not invert
  that by making them look like points.
- **A dashboard of dashboards.** Adding a panel is not a feature. Every
  new panel must displace something or justify a scroll.
- **A chat application.** Pilot is a role, not a message thread (§9.3).
- **A social product.** No feeds, no leaderboards, no copy trading.
- **A live brokerage.** Nothing in this design implies or prepares an
  order path to real money.

---

## 2. Target users

Four personas. Each is written with the two things a design spec actually
needs — *what the interface must hide* and *what it must emphasise* —
because a persona that stops at demographics changes no decisions. Names
are labels for a set of behaviours; they/them throughout.

### 2.1 Persona A — The Beginner ("Maya")

**Experience.** Has bought stocks in an app. Has never placed an option
trade. Knows the words "call" and "put" and is not confident about which
one they want. Does not know what delta is and is embarrassed about it.

**Goals.** Understand whether this is something they can do. Place one
paper trade without breaking anything. Find out afterwards whether it was
a good idea.

**Pain points.** Density reads as difficulty. A screen of unfamiliar
columns causes withdrawal, not curiosity. Every unexplained abbreviation
is a small decision to give up. They will not click a "?" they have not
been shown at least once.

**Hide.** The Greeks grid, IV surfaces, evidence weights, backtest
parameters, order types beyond market and limit, engine internals,
anything with a p-value, the entire concept of scan cadence.

**Emphasise.** One clear action per screen. Plain-language restatement of
what a contract is before it is bought. Maximum loss, in dollars, before
committing. The fact that this is paper money and nothing can be lost.

**The single design test for Maya:** *can they place a paper trade in
under three minutes from first launch, without reading documentation, and
correctly state afterwards what they bought and what the worst case was?*

### 2.2 Persona B — The Learning Trader ("Dan")

**Experience.** 20–150 trades in. Understands calls, puts and expiry.
Fuzzy on Greeks. Has developed habits they cannot yet see — trading too
close to expiry, sizing up after a loss, cutting winners early.

**Goals.** Get better. Understand *why* trades worked. Build a rule set
they trust.

**Pain points.** Their real problem is invisible to them, and no interface
they have used has ever named it. They over-consult live P&L and
under-consult their own history. They read generic education that isn't
about them.

**Hide.** Nothing structural — but do not lead with engine internals.
Advanced order types stay one level down until used.

**Emphasise.** *Their own numbers.* This is the persona the
`intelligence/` engine was built for and the one the current UI serves
worst, because its findings are buried in a dashboard panel among six
others of equal weight. Dan is why "What to do next" is a first-class
region on Home (§5.4) and why the Journal is promoted to a primary
destination (§4.2).

**The single design test for Dan:** *within thirty seconds of opening the
app, are they shown one true, evidenced, specific statement about their
own trading that they did not already know?*

### 2.3 Persona C — The Active Trader ("Priya")

**Experience.** Trades several times a week. Comfortable with Greeks,
spreads and expiry mechanics. Uses two monitors. Has muscle memory from
other platforms.

**Goals.** Speed. Get from an idea to a reviewed order in seconds. See
positions and working orders without navigating. Not be slowed down by
teaching.

**Pain points.** Modal dialogs for small choices. Having to re-enter a
symbol they just typed on another screen. Any workflow that requires the
mouse when the hand is on the keyboard. Being taught something they know.

**Hide.** Tutorials, tips, celebratory states, explanatory prose that has
already been read once. Nothing should re-teach after dismissal.

**Emphasise.** Keyboard access to everything on the order path. Persistent
context across destinations. Density. Working orders and open risk
visible without navigation. A command palette.

**The single design test for Priya:** *from anywhere in the app, with
hands on the keyboard, how many keystrokes to a reviewed order on a named
symbol?* Target: symbol jump, strike select, review — under six.

### 2.4 Persona D — The Professional ("Ted")

**Experience.** Trades for a living or close to it. Has used Thinkorswim
and Active Trader Pro and knows exactly what they miss. Evaluates the
software's *honesty* before its features.

**Goals.** Know precisely what the system is doing and why. Audit the
engine. Configure risk to their own rules. Multi-monitor layout that
survives a restart.

**Pain points.** Software that rounds, smooths or hides. A statistic with
no sample size. A model whose parameters cannot be inspected. Layout
state that resets.

**Hide.** Almost nothing. Ted opts into the fully revealed Surface Level
and expects it to stay.

**Emphasise.** Sample sizes and confidence next to every statistic.
Evidence weights and their bounds. Full diagnostics. Pop-out windows.
Exportable records. The documented limitations, stated in the UI rather
than only in the docs.

**The single design test for Ted:** *can they find, for any number the app
displays, what produced it and over how many observations, without
leaving the screen it is on?*

### 2.5 What the four personas share

They are the same person at different times. Priya on a Sunday afternoon
reviewing a bad week is Dan. Ted teaching someone is Maya's advocate. The
interface must therefore never require a *declaration* of identity that
locks a person out of the other modes. Surface Level (§8) is always
adjustable, in one click, in both directions, from a control that is
visible without hunting.

---

## 3. Core design principles

Eleven principles. Each has a **rule** (what it requires) and a **check**
(how a reviewer falsifies a screen against it). A principle without a
check is a slogan.

### P1 — One glance answers the three questions

**Rule.** Every destination answers, in its top third and without
interaction: *What am I looking at? Why does it matter? What can I do
next?*

**Check.** Screenshot the top third at 1920×1080. Show it to someone who
has not seen the app. If they cannot state the three answers, the screen
fails.

### P2 — Progressive disclosure, never progressive implementation

**Rule.** Depth is reached by revealing, not by navigating to a different
feature. Every advanced control exists in the same place as its simple
form, one deliberate gesture down.

**Check.** For any advanced capability, trace the path from its simple
form. If the path crosses a destination boundary, the disclosure is
broken.

### P3 — Never state what cannot be evidenced

**Rule.** The UI renders "insufficient evidence" as a first-class result,
with the reason, in the place the number would have been. It never
substitutes a zero, a dash without explanation, a hidden panel, or a
plausible default. Every statistic that can be derived from a small
sample displays its sample size.

**Check.** Seed a five-trade history. Every score, rate and pattern must
either display with its `n`, or display its reason for being unavailable.
A silently absent panel is a failure. This is the UI half of
`TRADING_INTELLIGENCE.md` §4.1–4.3, and the "Discipline 100/100, grade A
on 20% coverage" incident recorded in `CLAUDE.md` is what it exists to
prevent.

### P4 — Visual weight follows consequence

**Rule.** The visual prominence of an element is proportional to the money
it can move. Open exposure outranks account history. A working stop
outranks a watchlist quote. An engine diagnostic outranks nothing.

**Check.** Rank every element on a screen by "dollars this can affect."
Rank them by visual weight. The two orderings must correlate. On today's
Dashboard they do not: open positions sit below a session equity chart,
and the AI opportunities panel — which can move no money at all until
acted upon — occupies prime right-column real estate above notifications.

### P5 — Whitespace is structure, density is a setting

**Rule.** Space is used to group and separate, not to fill. Where density
genuinely serves a user (chains, watchlists, order books) it is available
as an explicit density setting — never as the default that everyone gets.

**Check.** Remove every border and background from a screen. The grouping
must still be readable from spacing alone. If it isn't, the layout is
relying on boxes to do the work of rhythm.

### P6 — The interface never surprises anyone about money

**Rule.** Friction is proportional to consequence. Reading is free.
Configuring is cheap. Committing capital requires a deliberate physical
act that cannot be produced by a mis-click, a double-tap or a stray Enter.
Every commit is preceded by a plain-language restatement including the
maximum loss.

**Check.** Enumerate every path that reaches `OrderManager.place`. Each
must pass through the review step and the commit gesture. A keyboard
accelerator that skips review is a defect, not a power feature.

### P7 — Motion explains causality

**Rule.** Animation exists to show where something came from, where it
went, or that an action registered. Nothing animates on data arrival.
Nothing animates to attract attention.

**Check.** For every animation, name the causal question it answers. If
the answer is "it looks nice," delete it. See §12.

### P8 — The keyboard is a surface, not a shortcut list

**Rule.** Every action a mouse can perform on the trading path has a
keyboard equivalent, discoverable from one place, consistent across
destinations.

**Check.** Unplug the mouse. Complete: find a symbol, load a chain, select
a contract, set quantity, review, commit, then cancel a working order. Any
step that is impossible is a failure. Today this fails at "select a
contract."

### P9 — Every empty state is the first step

**Rule.** An empty region states what will fill it, why it is empty, and
offers the single action that fills it. Empty states are designed first,
not last, because a new user's first five minutes are made almost entirely
of them.

**Check.** Wipe the data directory. Screenshot every destination. Each
empty region must contain a verb.

### P10 — One fact, one owner

**Rule.** The UI renders values computed by the service layer; it does not
recompute them. Two surfaces showing the same fact read it from the same
source.

**Check.** Grep the frontend for arithmetic on domain values. Ranking,
scoring, win rates, exposure and health states are computed in
`services/` and `intelligence/` and rendered verbatim. This is the same
lesson the settings page already learned — it renders `registry.ranking()`
rather than deriving its own order, because a page that disagreed with the
chart about provider priority would be undebuggable from either side
(`CLAUDE.md`).

### P11 — One workspace, not nine tools

**Rule.** OptionsPilot is a single environment the user works *inside*,
not a set of utilities they travel *between*. Context — the symbol,
timeframe, expiry, selected contract, drawings, and the user's Surface
Level — is a property of the workspace and persists across every
destination. Moving between destinations changes what you are *doing*,
never what you are *looking at*.

**Check.** Set a symbol, timeframe and drawing anywhere. Visit every other
destination and return. If any of the three was lost, reset, or had to be
re-entered, the workspace is not one workspace. Today this fails: the
Charts tab and the Trade tab maintain separate symbol inputs, and a user
who charts NVDA and then opens Trade is looking at SPY.

**Why this principle is load-bearing.** It is the difference between a
product and a bundle. Every other principle can be satisfied
screen-by-screen; this one can only be satisfied by the architecture of
the whole. The mechanisms that deliver it are enumerated in §4.5 and are
the single highest-leverage change in this document — higher than the
visual system, higher than the dashboard, because it is the one a user
feels continuously rather than once.

---

## 4. Navigation architecture

### 4.1 What is wrong with the current structure

The current sidebar has nine destinations: Dashboard, Charts, Trade,
Coach, Watchlist, Journal, Backtest, Learning, Settings. The problems are
not cosmetic:

1. **They are not the same kind of thing.** Dashboard is an overview,
   Charts is a tool, Trade is an action, Watchlist is a data collection,
   Learning is engine internals. A navigation whose items belong to
   different categories cannot be learned as a system — only memorised as
   a list.
2. **One workflow is split across two destinations.** Charts and Trade are
   the same activity. The product already knows this: the Trade tab
   contains a collapsible chart that explicitly shares drawings and
   indicators with the Charts tab. That collapsible is a workaround for a
   navigation mistake.
3. **Three destinations are the same question.** Coach ("what did I do
   wrong"), Journal ("what did I do") and Learning ("what has the engine
   inferred") are all retrospection. Splitting them means a user
   evaluating their own trading has to know which of three places holds
   which half of the answer.
4. **"Learning" is misnamed in the most costly way possible.** It contains
   evidence weights, performance by hour and confidence buckets — engine
   transparency. A beginner reads the label as "where I learn to trade,"
   clicks it, and is met with a bounded-weight table. That is the single
   worst first-click in the product.
5. **Watchlist is a destination but behaves like context.** Its entire
   purpose is to feed other screens. A user does not go *to* the watchlist;
   they use it *while* doing something else.
6. **Nine top-level items plus an overloaded header exceeds the working
   set.** The header simultaneously carries the title, a market pill, a
   two-option operating-mode control, a three-option trading-mode control,
   a cycle pill, Scan now, Learn, and a Help menu. That is eight
   independent controls competing with the content beneath them.

### 4.2 The proposed structure

Six destinations, two persistent surfaces, one utility slot.

```
  PRIMARY DESTINATIONS          THE QUESTION EACH ANSWERS
  ------------------------------------------------------------------
  Home            (1)           What is true right now, and what needs me?
  Trade           (2)           I want to open or manage a position.
  Portfolio       (3)           What do I hold, and what is it doing?
  Research        (4)           Is this idea any good?
  Journal         (5)           What have I done, and what should I learn?

  UTILITY
  Settings        (,)           Configure the machine.

  PERSISTENT SURFACES (available from every destination)
  Command palette (Ctrl+K)      Go anywhere, do anything, by name.
  Pilot           (Ctrl+/)      Explain, teach, summarise, advise.
  Symbol jump     (/)           Change the workspace's symbol context.
```

Each destination is a *question a user has*, not a *feature the codebase
contains*. That is the whole of the reordering rationale, and it is what
makes the set learnable: a user who understands the six questions never
has to remember where a feature lives, because they can derive it.

> **First-time trader —** six items with plain names, each answering a
> question they can already ask in English. They never encounter "Backtest"
> or "Evidence weights" as a top-level choice they must evaluate.
> **Experienced trader —** the two most-used destinations are keys 1 and 2,
> the ones they reach for reflexively; the palette makes every deeper
> surface reachable by name in one keystroke rather than by hunting a
> nine-item list.
> **One system —** the six are phases of a single loop (observe → act →
> hold → investigate → learn → observe), not a menu of applications. The
> nav reads as a workflow, which is what makes returning to Home from
> Journal feel like completing a circuit rather than closing one program
> and opening another.

### 4.3 Where the current nine went

| Today | Becomes | Rationale |
| --- | --- | --- |
| **Dashboard** | **Home** | Renamed and rebuilt (§5). "Dashboard" describes a widget arrangement; "Home" describes a place. |
| **Charts** | **Trade** (primary) and **Research** (exploratory) | The chart is not a destination, it is the principal instrument of two activities. Full-screen charting remains, reachable by `F` and as a pop-out window (§13.5). |
| **Trade** | **Trade** | Rebuilt as one workspace: chart, chain and ticket on a single surface (§6). |
| **Coach** | **Journal › Review** | The coach reviews closed trades. That is the Journal's subject. |
| **Watchlist** | **Context rail**, present in Trade, Research and Home | Promoted in usefulness by being demoted from a destination. Full management (bulk paste, presets, reordering) lives in Research › Watchlist for the rare occasions it is the task itself. |
| **Journal** | **Journal › Trades** | Unchanged in substance; gains siblings. |
| **Backtest** | **Research › Backtest** | It is a research instrument. It is also the correct neighbour for the chart and the chain. |
| **Learning** | **Research › Engine** | Renamed to what it is: engine transparency. This alone removes the worst mislabel in the product. |
| **Settings** | **Settings** | Unchanged, restructured internally (§4.6). |

Nothing is deleted. Everything that exists today is reachable, and eight
of the nine are reachable in one keystroke via the palette by their old
names as aliases — a user who types "coach" or "backtest" arrives at the
right place and sees where it now lives, so the rename teaches rather than
strands.

### 4.4 The chrome: what the frame carries

The frame is one row. It carries four things and nothing else:

```
  OptionsPilot   |  <destination>   <context>        Ctrl+K   [status]  Pilot
```

- **Identity + destination.** Where you are.
- **Context.** The active symbol and its price, when the destination has
  one. Clicking it opens the symbol jump.
- **Status.** One compact cluster: account mode (`PAPER`), engine state
  (`AI paused` / `AI trading` / `scanning…`), and market state. It is a
  *button* that opens the Flight Status popover (§4.7).
- **Pilot.** A single persistent affordance (§9.3).

Everything else currently in the header moves: `Scan now` becomes the
primary action on Home and a palette command everywhere else; `Learn`
becomes contextual help attached to the thing being explained plus a
palette command; the `Help ▾` menu's six items become palette entries,
which is where a user looks for them anyway.

> **First-time trader —** the top of the screen stops being a control
> panel they must decode before reading the content beneath it.
> **Experienced trader —** the reclaimed vertical space is content, and
> everything removed is now one keystroke closer, not further away.
> **One system —** one frame, identical on every destination, with the
> workspace's context living in it. The frame is the evidence that the
> destinations are rooms in a building rather than separate buildings.

### 4.5 Context continuity — the mechanism behind P11

This is the specification that makes the product feel like one system. It
is a list of concrete guarantees, each independently testable.

1. **One symbol context.** There is exactly one active symbol for the
   workspace. Setting it on the chart sets it for the chain, the ticket,
   Research and Home's context strip. There is no second symbol input
   anywhere in the product. `/` sets it from anywhere.
2. **One timeframe context.** Changing the chart timeframe changes it
   everywhere a timeframe applies, and it survives a symbol change.
3. **One contract selection.** Selecting a chain row selects it for the
   ticket, marks it on the chart, and remains selected if the user visits
   Research and returns.
4. **One set of drawings and indicators per symbol**, already true between
   the Charts tab and the Trade tab's embedded chart. Preserved and
   extended to Research.
5. **One Surface Level** (§8), applied uniformly. There is no destination
   that is "advanced" while another is "simple."
6. **One notification inbox** (§10), reachable identically from
   everywhere, with every entry linking back into the destination that
   owns its subject.
7. **One Pilot conversation state.** Asking Pilot about a symbol on Trade
   and then opening Journal does not lose the thread.
8. **Context survives restart.** All of the above is server-owned state,
   not `localStorage`. This is settled architecture, not a proposal: the
   workspace already moved server-side precisely because `localStorage` is
   a cache that a WebView profile reset discards silently
   (`WORKSPACE_ARCHITECTURE.md`, and the `CLAUDE.md` trap "`localStorage`
   is a cache, not storage"). New context added by this design goes to the
   same place, through `RuntimeSettings`.

**The test for the whole section.** Type a symbol once at launch. Complete
a full loop — chart it, chain it, ticket it, review it, hold it, journal
it — and never type that symbol again. If the user has to retype it, the
workspace is not one workspace.

### 4.6 Settings, restructured

Settings is currently a long scroll of unrelated panels. It becomes five
groups, addressable individually from the palette:

| Group | Contains |
| --- | --- |
| **Trading** | Risk parameters, trading mode configuration, position sizing, custom-mode advanced settings. |
| **Automation** | Operating mode, scan cadence, background workload, what happens on window close, launch behaviour. |
| **Data** | Market-data providers, API keys, ordering, quotas, maintenance, diagnostics. |
| **Appearance** | Theme, Surface Level, density, motion, text size, accessibility. |
| **About** | Version, updates, licences, storage locations, export, the documented limitations. |

The market-data panel's existing behaviours are preserved exactly — it
stops polling while focus is inside it and while its tab is hidden, and
half-typed values are captured and restored across re-renders. Those are
not incidental; they are the fix for a settings page eating a pasted API
key (`CLAUDE.md`).

### 4.7 Flight Status — one control for two orthogonal axes

The header currently exposes `operating_mode` (AI trades / You trade) and
`trading_mode` (Conservative / High-Risk / Custom) as two always-visible
segmented controls, five buttons in total, permanently competing for
attention with the content. Most users change these rarely; a beginner
cannot tell from the controls that they are independent, and the labels
invite the inference that "AI + High-Risk" is one setting with four
positions.

**The replacement.** One status button in the frame, reading as a
sentence:

```
   [ PAPER . You trade . Conservative . Market opens 42m ]
```

Clicking it opens a popover with the two axes as separate, labelled
sections, each with a one-line explanation of what it controls and an
explicit statement that the other axis is unaffected. The popover is also
where `Scan now` and scan cadence live.

**The invariant, stated here because a UI has broken it before.** These
two axes are orthogonal and must remain so. Selecting an operating mode
must not alter the trading mode, and vice versa. `RuntimeSettings._apply_mode`
already implements explicit preservation for exactly this reason
(`CLAUDE.md`), and the popover must not become a place where a convenience
("switching to AI sets Conservative") quietly couples them. **A combined
presentation must not become a combined model.**

> **First-time trader —** one sentence in plain English replaces five
> buttons whose relationship was unstated. Opening it teaches the
> distinction rather than assuming it.
> **Experienced trader —** the modes they set once every few weeks stop
> consuming permanent header space, and the status they *do* consult
> constantly (paper, engine state, market clock) is now a single legible
> line.
> **One system —** the app's overall operating state is expressed in one
> place, in one voice, rather than as scattered pills. It reads like the
> system reporting its condition, which is precisely the flight-deck
> posture.

---

## 5. Home

### 5.1 What is wrong with the current Dashboard

Measured against P4 (weight follows consequence) and P1 (one glance), the
current Dashboard has six specific faults, all structural:

1. **It requires scrolling at 1920×1080** to reach open positions, which
   are the highest-consequence objects on the screen.
2. **Everything is a `panel` with an `h2`.** Nine panels of identical
   visual weight means the layout expresses no priority at all — the user
   must supply the hierarchy themselves, every time.
3. **The watchlist confidence panel sits below the equity chart**, so
   forward-looking information is beneath backward-looking information.
4. **The AI opportunities panel occupies the top of the side column** and
   is large, while the trading intelligence panel — the only region that
   says something specific and evidenced about *this user* — is a
   collapsed-by-default block above the main grid.
5. **Notifications are a dead end.** No history, no filtering, no
   click-through to the subject. Acknowledged in `ROADMAP-V3-UX.md` and
   still open as H5.
6. **The equity chart shows session history only**, which for a paper
   account restarted between sessions is frequently the least informative
   large object on the page.

### 5.2 The structure: three bands

Home has exactly three horizontal bands, and the count is the design. The
top band is *the state*, the middle band is *what needs you*, the bottom
band is *context*. Nothing else is admitted to Home; every candidate panel
must displace a resident.

```
+--------------------------------------------------------------------------------------+
| OptionsPilot                              Ctrl+K  Search           Pilot             |
+--------------------------------------------------------------------------------------+
| Good morning. Markets open in 42m. Nothing needs you.     PAPER . AI paused          |
+-----------------+-----------------+-----------------+----------------+---------------+
| ACCOUNT         | TODAY           | OPEN RISK       | BUYING POWER   | WIN RATE      |
| $10,412.55      | +$212.40        | $840            | $8,900         | 58%           |
| +4.1% all time  | +2.1%  _.-'-._  | 8.1% of acct    | 2 orders queued| n=41          |
+-----------------+-----------------+-----------------+----------------+---------------+
| POSITIONS (2)                     [Manage >]     | WHAT TO DO NEXT                   |
|                                                  |                                   |
| SPY  470C  12 Sep    +$142   +18.2%  [x]         | Your 0-2 DTE trades win 31%       |
| AAPL 190P  19 Sep     -$38    -4.1%  [x]         | over 26 trades (p=0.004).         |
|                                                  | Consider a 7-day floor.           |
| WORKING ORDERS (1)                               | Show me >                         |
| NVDA 900C   stop @ 878.00          [edit]        |                                   |
|                                                  | 2 setups cleared the gate.        |
+--------------------------------------------------+-----------------------------------+
|                                                  | QQQ   long   71%          >       |
| EQUITY  30d             _.-''-._.-'              | MSFT  long   66%          >       |
|                                                  |                                   |
|                                                  | Nothing else is waiting.          |
+--------------------------------------------------------------------------------------+
```

### 5.3 Band 1 — the status line and the five metrics

**The status line** is a single sentence, generated by the service layer,
that states what is true right now. It is the most important sentence in
the product because it is the first thing read on every launch. Its
grammar is fixed: *[time context]. [market context]. [what needs you].*

Examples of every case it must cover:

| Situation | Sentence |
| --- | --- |
| Nothing open, market closed | "Good evening. Markets are closed. Nothing needs you." |
| Nothing open, market open | "Markets are open. You have no positions and 2 setups cleared the gate." |
| Positions open, healthy | "Markets are open. You are up $212 today across 2 positions." |
| A stop is close | "AAPL is $0.40 from your stop. Everything else is steady." |
| An order was rejected | "One order was rejected — insufficient buying power. Nothing else needs you." |
| Trading halted | "Trading is halted: daily loss limit reached. Positions are still managed." |
| A provider is degraded | "Quotes are delayed — Yahoo is rate limited and retrying. Trading continues." |
| First launch, empty | "Welcome. Your paper account has $10,000. Start with a chart, or let Pilot show you around." |

The rule that keeps this honest: **the status line never says "nothing
needs you" unless nothing does.** It is derived, not decorative, and it is
the single sentence a user is entitled to trust completely.

**The five metrics** are chosen by consequence, not by availability:
Account value, Today, Open risk, Buying power, Win rate. `Open risk` is
new and is deliberately third — it is the number that answers "how exposed
am I," which no current screen states directly. `Win rate` carries its
sample size inline (`n=41`) per P3, and below the coverage floor it reads
"not enough trades yet (12 of 30)" instead of a number.

> **First-time trader —** the sentence tells them, in English, whether
> they need to do anything. Nothing else on any screen does that.
> **Experienced trader —** five numbers, fixed positions, tabular
> figures, readable in under a second from across a desk. Open risk is
> surfaced without navigating to compute it mentally.
> **One system —** the same status line, in the same voice, appears on
> mobile, in tray tooltips and in notification summaries. It is the
> system's single self-report, reused rather than reinvented per surface.

### 5.4 Band 2 — Positions, and What to do next

**Left: what you hold.** Open positions and working orders, together,
above the fold, always. These are the highest-consequence objects and by
P4 they take the largest region. Each row shows the contract in
human-readable form, unrealised P&L in dollars and percent, and a direct
close action. Working orders are a subsection rather than a separate
panel, because "a stop I have resting" and "a position I hold" are one
mental object.

**Right: what to do next.** This region is the `intelligence/` engine's
front door, and giving it permanent prime real estate is the single
highest-value change on Home. It shows at most **three** items, ranked, in
this priority order:

1. **A risk condition** that is currently true (approaching a stop, a
   concentration, a halt).
2. **One evidenced behavioural finding** about this user, with its `n` and
   its p-value, and a link to the evidence.
3. **Cleared setups** — the AI's current opportunities, compact.

If none qualify, the region says so plainly: "Nothing else is waiting." An
empty right column that says nothing is a bug; one that says "nothing" is
information.

**Why three.** A recommendation list longer than three is a list nobody
reads. The engine already ranks with a false-discovery correction
(`TRADING_INTELLIGENCE.md` §4.4); the UI's job is to trust that ranking and
show the top of it, not to render everything the engine produced. This is
P10 applied to advice.

> **First-time trader —** the app tells them, unprompted, one true thing
> about their own trading. For most people this is the first time software
> has done that.
> **Experienced trader —** the two things they scan for on launch —
> exposure and anything anomalous — are the two things in band 2, and they
> never scroll for either.
> **One system —** the same ranked findings appear inline in the Journal,
> in Pilot's answers and in mobile notifications. One engine, one ranking,
> many surfaces. A user recognises a finding wherever it appears.

### 5.5 Band 3 — context

Equity history (defaulting to 30 days, not session — a session curve on a
freshly launched paper account is noise) and the watchlist with AI
confidence. This band is allowed to be the part that scrolls, because
nothing in it is consequential. That is the point of putting it third.

### 5.6 The no-scroll commitment

**At 1920×1080, Home fits without vertical scrolling, including bands 1
and 2 in full.** At 1440×900, bands 1 and 2 fit and band 3 begins below
the fold. At 1280×800 the metrics band compresses to three metrics plus an
overflow. This is a hard acceptance criterion, verified by a browser check
(§16.9), not an aspiration.

---

## 6. The trading experience

This is the most important workflow in the product and the one where
cognitive load does actual financial damage.

### 6.1 What is wrong today

- **The flow spans two destinations.** Charting happens in one place,
  trading in another, with a collapsible chart in Trade as a bridge.
- **The ticket does not exist until a contract is chosen.** A user cannot
  see what an order involves — types, quantity, time in force, estimated
  cost — until after they have committed to a contract. That is backwards:
  the shape of the decision should be visible before the decision.
- **There is no quick path to a sensible contract.** Every trade requires
  manually scanning a chain, even for the overwhelmingly common intent
  "buy a near-the-money call about a month out."
- **Review is a modal that restates the mechanics, not the consequences.**
- **The chain is not keyboard-navigable**, which breaks P8 at the exact
  point where speed matters most.
- **Positions, working orders and history are stacked below the ticket**,
  so the ticket column becomes a long scroll containing four unrelated
  things.

### 6.2 The workspace

One destination. Three regions that are always present, whose proportions
the user can drag and which persist.

```
+--------------------------------------------------------------------------------------+
| TRADE    SPY  $471.20  +0.84%   15m delayed        Ctrl+K        Pilot               |
+----------------------------------------------------------+---------------------------+
| 1m 5m 15m 1h [1D] 1W    indicators  drawings  +          | ORDER TICKET              |
|                                                          |                           |
|                                                          | Nothing selected yet.     |
|            _.-'''-._            .-'                      |                           |
|        _.-'         '-._   _.-'                          | Quick pick                |
|    _.-'                 '-'                              |  [ATM call]  [ATM put]    |
| _-'                                                      |  [30 day]    [Weekly]     |
|                                                          |                           |
|                                                          | Order type    Market  v   |
| your position and stop draw as labelled price lines      | Contracts     [-] 1 [+]   |
+----------------------------------------------------------+---------------------------+
| CHAIN   SPY   [Calls] Puts     12 Sep (7d)  v            | Time in force  Day    v   |
|                                                          |                           |
| STRIKE   BID    ASK   DELTA   IV     VOL    OI           | Estimated cost     --     |
| 465     7.10   7.25   .71   18.2%   4.1k   12k           |                           |
| 468     5.05   5.20   .63   17.9%   8.3k   21k           |                           |
| 470 *   3.85   3.95   .54   17.6%    22k   48k           |   [ Review order ]        |
| 472     2.70   2.80   .45   17.5%    15k   33k           |                           |
| 475     1.60   1.70   .33   17.7%    19k   41k           | B / S side . Enter        |
|                                                          |                           |
| WATCHLIST   SPY  QQQ  AAPL  NVDA  MSFT  TSLA   +         |                           |
+--------------------------------------------------------------------------------------+
```

**The ticket is always present**, in all five of its states:

| State | What it shows |
| --- | --- |
| **Empty** | The full shape of an order — quick picks, order type, quantity, time in force — with a disabled submit and "Nothing selected yet." The user learns the vocabulary before they need it. |
| **Selected** | The contract, live estimated cost, max loss, and the risk assessment from `RiskManager`. |
| **Invalid** | The specific reason, in place, with the offending field marked and the impossible option removed — plus a line saying what changed and why. This preserves the existing `#tk-kind-why` guardrail exactly. |
| **Review** | The modal in §6.5. |
| **Working** | A confirmation that collapses into the positions rail; the ticket resets to Empty with the symbol retained. |

### 6.3 Quick picks — the intent shortcut

Four chips in the empty ticket: **ATM call**, **ATM put**, **30 day**,
**Weekly**. Each resolves an *intent* into a concrete contract using the
current symbol and spot, selects it in the chain, and populates the
ticket. The chain remains fully available; the chip is a shortcut, never a
replacement, and the resulting selection is highlighted in the chain so
the user can see what was picked and why.

> **First-time trader —** "buy a call on this" becomes one click instead
> of a table of forty rows they must first learn to read. The chain then
> teaches them what the chip chose.
> **Experienced trader —** the most common intent stops costing a manual
> scan on every trade.
> **One system —** the chips are the same intents Pilot uses when it
> suggests a trade and the same the AI engine expresses its opportunities
> in. A cleared setup on Home, opened in Trade, arrives as a populated
> quick pick — the suggestion and the action are the same object.

### 6.4 The chain

- **Keyboard-navigable.** Arrow keys move the selection, `Enter` selects,
  `Tab` moves to the ticket. This closes the P8 failure.
- **Spot-anchored.** On load, the chain scrolls to and marks the strike
  nearest spot. A chain that opens at the top of the strike range makes
  every user scroll to the middle before they can think.
- **Column set follows Surface Level.** Guided shows Strike, Price,
  Break-even and a plain-language "chance of finishing in the money"
  reading of delta. Full shows the professional column set. The
  *underlying data is identical*; only the columns differ. This is P2 and
  §1.4's anti-bifurcation rule in its most concrete form.
- **Expiry as a horizontal strip**, with days-to-expiry always printed
  alongside the date, because "12 Sep" and "7 days" are different facts and
  beginners need the second one.

### 6.5 Review — the consequence restatement

Review is not a confirmation dialog. It is the one place in the product
where the trade is described in the language a person would use to explain
it to a friend, and where the worst case is stated before it can happen.

```
+----------------------------------------------------------------+
|  Review your order                                       [x]   |
|                                                                |
|  You are BUYING 1 SPY $470 call expiring 12 Sep (7 days).      |
|                                                                |
|  Cost today            $395.00                                 |
|  Most you can lose     $395.00    (100% of what you pay)       |
|  Breakeven at expiry   $473.95    SPY is $471.20 now           |
|  Position size         3.8% of account                         |
|                                                                |
|  If you do nothing and SPY closes below $470 on 12 Sep,        |
|  this contract expires worthless.                              |
|                                                                |
|  Fills against a 15-minute delayed quote on the next cycle.    |
|                                                                |
|  +----------------------------------------------------+        |
|  |  Hold to place order          [=======>          ]  |       |
|  +----------------------------------------------------+        |
|                                                                |
|                                          Cancel  (Esc)         |
+----------------------------------------------------------------+
```

Five required elements, in this order, always:

1. **A sentence.** Side, quantity, symbol, strike, right, expiry, and days
   remaining. Never an abbreviation on this line.
2. **Cost and maximum loss**, as dollars, with maximum loss stated even
   when it equals cost — *especially* then, because "you can lose all of
   it" is the fact beginners most often do not know.
3. **Breakeven**, with current spot beside it so the distance is visible
   without arithmetic.
4. **Position size as a percentage of account**, which is how risk is
   actually judged.
5. **"If you do nothing"** — the passive outcome. Options are the
   instrument where doing nothing has a consequence, and no retail
   interface says so at the moment of commitment.

Plus one honesty line: how the fill will actually happen. The system
fills against delayed quotes on the next cycle. Saying so at the point of
commitment is not a disclaimer, it is accuracy — and it prevents the
"why didn't I get that price" confusion that follows every paper fill.

At the Guided Surface Level, Review adds one Pilot line explaining the
single most consequential term in the order (§9.4). At Full, it does not.

### 6.6 Commit — hold to confirm

The commit control is a **hold**: press and keep pressing for ~600ms while
a progress indicator fills, then release. Releasing early cancels with no
side effect and no dialog.

**Why a hold rather than a click.** A click is indistinguishable from a
mis-click; a hold cannot be produced by accident, by a double-tap
overshoot, or by a stray `Enter` on a focused button. It creates an
unmistakable moment of commitment with a built-in escape hatch that
requires no second dialog to undo. And critically, it is *fast* — 600ms is
shorter than reading a confirmation dialog, so the deliberate act is
cheaper than the ceremony it replaces.

**Keyboard equivalent.** Hold `Enter`. Same duration, same fill indicator,
same early-release cancel. P8 is not satisfied by a mouse-only gesture.

**This is the desktop expression of the same idea mobile expresses as a
swipe** (§14.5). One concept, two physical grammars. That is what makes
the mobile app feel related without being a port.

> **First-time trader —** it is impossible to place an order by accident,
> and the deliberateness of the gesture communicates the seriousness of
> the act better than any warning copy.
> **Experienced trader —** 600ms replaces a modal round-trip; the whole
> commit is one uninterrupted gesture from the keyboard.
> **One system —** the identical gesture, with identical semantics,
> confirms every consequential action in the product: placing an order,
> closing a position, cancelling a working order, resetting a halt. The
> user learns "hold means commit" once and it is true everywhere,
> including on mobile in its swipe form.

### 6.7 The full flow, with target counts

```
   Symbol  ->  Chart  ->  Chain  ->  Contract  ->  Ticket  ->  Review  ->  Commit
     /         (auto)     (auto)      1 click     0-2 edits   1 click    1 hold
```

**Beginner path** (mouse, guided): symbol jump, quick pick, review, hold.
**Experienced path** (keyboard): `/SPY⏎`, `↓↓⏎`, `⏎`, hold `⏎`.

**Nothing in this flow crosses a destination boundary.** That is the
single largest workflow improvement in this document, and it is P11 paying
out at the exact point where interruption is most expensive.

---

## 7. First launch — the beginner experience

### 7.1 A correction to the brief, stated up front

The brief lists "login / create account / broker connection" as onboarding
steps. Two of those do not exist in this product and should not be
invented for the sake of a familiar-looking flow:

- **There is no account system, and an account has no purpose yet.**
  OptionsPilot is a local desktop application. `services/sync.py` is a
  classified inventory that *syncs nothing and must not start*
  (`CLAUDE.md`). Asking a user to create an account before there is
  anything to synchronise is friction that buys nothing and costs the
  under-a-minute budget.
- **There is no broker connection, by design.** Offering "connect your
  broker" during onboarding would imply a live-trading path this product
  deliberately does not have, and that is the one implication the product
  must never make.

**What replaces them.** Onboarding defaults to "continue on this device."
Identity is introduced later, in context, and only when it buys something
real — pairing a phone (§14) or synchronising a journal across machines.
That is when an account becomes a feature rather than a toll booth. Where
a broker step would be, we state the truth plainly: this is a paper
account, funded with $10,000, and nothing here can cost real money. For
most first-time users that sentence is the single most reassuring thing
the product can say, and burying it is a wasted asset.

The one genuinely useful setup step — adding a market-data API key — is
*offered* and *skippable*, because the app works without one (Yahoo, no
key) and a mandatory key request in the first minute loses users who have
not yet decided they want the product.

### 7.2 The flow — four screens, under sixty seconds

```
   [1] Welcome          ~8s    Who this is for. Paper money. One sentence.
   [2] Experience       ~12s   Three cards. Sets the initial Surface Level.
   [3] Your list        ~20s   Pick a starter watchlist, or type symbols.
   [4] Ready            ~10s   Account funded. Three doors. Tour offered.
```

**Screen 1 — Welcome.**

> **OptionsPilot**
> A calm place to learn options trading.
> Your account is paper money — $10,000 that isn't real. Nothing here can
> cost you anything.
> `[ Get started ]`   `Skip setup`

`Skip setup` is present, prominent enough to find, and lands directly on
Home with sensible defaults. An onboarding a user cannot escape is a
hostage situation. Everything it would have collected can be set later
from Settings › Appearance, and Pilot will offer the tour once, later,
when it is contextually useful.

**Screen 2 — Experience.** Three cards, one click, no scoring, no quiz:

| Card | Copy | Sets |
| --- | --- | --- |
| **New to options** | "I've bought stocks, but options are new." | Surface Level 1 (Guided) |
| **I know the basics** | "Calls, puts, expiry — I'm comfortable. Still learning the rest." | Surface Level 2 (Focused) |
| **Experienced** | "I trade options regularly. Show me everything." | Surface Level 3 (Full) |

Under the cards, one line that does the most important work on the screen:
*"You can change this any time, and nothing is locked."* The reason a
beginner under-declares their experience on every product that asks is the
fear of being permanently downgraded. Removing that fear costs one
sentence.

**Screen 3 — Your list.** Four preset chips (Big Tech, Index ETFs, Most
active, S&P leaders) and a free-text field that accepts a pasted list —
capability the watchlist already has. Pre-selects Index ETFs so the
screen has a valid state without any input. Skippable.

**Screen 4 — Ready.**

> **You're set.**
> Your paper account has $10,000. Quotes are delayed 15 minutes.
> `[ Show me around — 2 min ]`  `[ Place a practice trade ]`  `[ Just explore ]`

Three doors, because first-run intent genuinely differs: some want a tour,
some want to do the thing, some want to poke around. Offering only a tour
loses the last two groups, and offering nothing loses the first.

### 7.3 What happens after onboarding ends

Onboarding is the *first* minute, not the *only* one. The existing guided
system (`ONBOARDING.md`) is retained wholesale and its contract preserved:
per-screen walkthroughs, glossary tooltips on jargon, empty states that
say what will fill them, and guardrails that remove impossible options
while saying what changed and why. The frontend continues to hold the
tutorial prose while the backend holds only ids and progress, and
`TestCatalogueContract` continues to assert the id sets match in both
directions.

Three additions:

1. **First-time-only inline explanations.** The first time a user opens
   the chain, one dismissible line above it explains what they are looking
   at. Second time, nothing. Tracked by the same measured-feature-usage
   mechanism `guide.py` already uses.
2. **A practice trade path.** From screen 4, a guided walk to a completed
   paper order, ending on the position they just opened. This is the
   fastest possible route to "I did the thing," and it is worth more than
   any tour.
3. **The one-week check-in.** After seven days *and* at least five trades,
   Pilot offers one review of what the user has done. Once. Declining it
   permanently is one click.

> **First-time trader —** under a minute, no account, no broker, nothing
> mandatory, and the first thing they read is that nothing can cost them
> money.
> **Experienced trader —** `Skip setup` on screen 1, then a fully revealed
> app. Total cost: one click.
> **One system —** the Surface Level chosen on screen 2 is the same
> workspace property that governs the chain's columns, the review dialog's
> Pilot line and the mobile app's density. Onboarding does not configure a
> tutorial; it configures the workspace.

---

## 8. Adaptive UI — Surface Levels

### 8.1 The critical constraint, first

The brief proposes several "modes": Beginner, Learning, Trading, Research,
Journal, Professional. Some of those are *destinations* (Research,
Journal) and are already handled in §4. The rest describe **how much of
the interface is revealed**, which is one axis, not five.

This matters architecturally. OptionsPilot already has two orthogonal mode
axes — `operating_mode` and `trading_mode` — and `CLAUDE.md` requires they
never implicitly change each other. **Surface Level is a third axis, and
the same rule binds it, with one addition: Surface Level is
presentation-only.**

**Four invariants, non-negotiable:**

1. **Surface Level never changes behaviour.** It changes what is
   *displayed*. Identical inputs produce identical orders, identical risk
   decisions and identical fills at every level.
2. **Surface Level never hides money, risk or a warning.** Position value,
   unrealised P&L, exposure, max loss, halts and rejections are visible at
   every level. Guided hides *complexity*, never *consequence*.
3. **Surface Level never gates a safety mechanism.** Every gate runs at
   every level.
4. **Surface Level is reversible in one click, in both directions**, from
   a control that does not require hunting.

A design that violates any of these is not progressive disclosure. It is a
crippled edition, and it will teach users that the software is hiding
things from them — which is the exact opposite of the trust this document
is trying to build.

### 8.2 The four levels

| | **1 · Guided** | **2 · Focused** | **3 · Full** | **4 · Pro** |
| --- | --- | --- | --- | --- |
| **For** | First trade | First few months | Regular trading | Power use |
| **Chain columns** | Strike, Price, Breakeven, "chance ITM" | + Delta, Volume | + IV, OI, all Greeks | + custom column set |
| **Order types** | Market, Limit | + Stop loss, Take profit | All | All + presets |
| **Explanations** | Inline, first-time, on jargon | Tooltip on hover | Tooltip on demand | Off by default |
| **Statistics** | Win rate, P&L | + profit factor, avg win/loss | + drawdown, per-setup breakdown | + p-values, coverage, weights |
| **Research** | Ideas only | + backtest | + engine transparency | + diagnostics, QA |
| **Density** | Comfortable | Comfortable | Compact | Compact + custom |
| **Pilot** | Proactive on first encounters | On request + risk conditions | Risk conditions only | Silent unless asked |

### 8.3 How complexity increases

Three mechanisms, in order of preference:

1. **The user moves the level.** Always available. The primary mechanism,
   and the only one that changes anything without asking.
2. **The interface offers, on evidence.** When measured feature usage
   indicates a user has outgrown a level — they have used every control at
   their level, placed enough trades, opened the same tooltip repeatedly —
   Pilot offers once: *"You've been using every control on this screen.
   Want to turn on the full option chain? You can switch back any time."*
   This uses the mechanism `guide.py` already implements, and it obeys the
   same line that document draws: **recommendations come from measured
   feature usage, never from trading behaviour.** Trading behaviour is
   `intelligence/`'s subject and is never a reason to change someone's UI.
3. **Local reveal.** Any panel can be expanded past the current level
   without changing the level. A Guided user who wants to see the Greeks
   once clicks "show all columns" and gets them, and the level does not
   change. This is the pressure-release valve that makes the whole system
   non-patronising: **curiosity is never punished with a settings trip.**

**Downgrade is symmetric and never automatic.** The system never lowers a
user's level. Only the user does.

> **First-time trader —** the first screen they see has six columns, not
> nineteen, and every one of them is a word they know — but nothing is
> locked and one click shows the rest.
> **Experienced trader —** one choice during onboarding and the app is
> fully revealed permanently, with no re-teaching, no tips and no
> re-earning of controls.
> **One system —** one workspace property, one control, applied uniformly
> to every destination and to mobile. There is no "beginner section" and
> no "advanced section" — there is one product at a chosen depth.

---

## 9. Pilot

### 9.1 What Pilot is

Pilot is the mentor layer: a single, consistent, named presence that
explains, teaches, summarises and advises across the whole product. It is
the thing that makes the app's intelligence feel like *one* intelligence
rather than seven features that happen to produce text.

Pilot is **not** a chatbot with a window. It is a role that appears in
several places, in one voice, and it is silent by default.

### 9.2 Architecture: what Pilot is built from, and the decision required

**Pilot v1 is deterministic**, and this is the correct first version. It
is a presentation and composition layer over capabilities that already
exist and are already rigorous:

| Pilot capability | Existing source |
| --- | --- |
| Explains a term (delta, IV, theta, breakeven, assignment) | The glossary and adaptive tooltip catalogue |
| Explains a screen or a control | The tutorial engine (`services/guide.py`) |
| Explains a *pattern in your trading* | `intelligence/` — with FDR correction, coverage floors and evidence citations |
| Reviews a closed trade | `coach/` |
| Explains why the AI took or skipped a setup | `engine/` gate reasoning, already recorded per trade |
| Summarises the market | Watchlist + scan results, stated as measurements |
| Explains a risk decision | `RiskManager`'s reason strings |
| Explains what the app is doing right now | The status line generator (§5.3) |

That composition is genuinely sufficient for every capability the brief
asks of Pilot — teach, explain, answer, recommend, explain Greeks,
summarise markets, help new users — because those questions have bounded,
enumerable answer spaces, and answering them from measured facts is
*better* than generating them: it is auditable, offline, instant, free,
and constitutionally incapable of inventing a statistic about the user.

**The open decision.** The one capability determinism cannot provide is
free-form natural-language Q&A on arbitrary questions. Adding it means
adding an LLM, which is a deliberate architectural change `CLAUDE.md`
requires be requested explicitly rather than implied. **This document does
not decide it — it is Open Decision #1 in §19.** If it is taken, three
constraints are mandatory:

1. **Explain-only.** The model never selects a contract, sizes a position,
   scores a setup or influences a gate. It renders and rephrases; it never
   decides. The trading path stays deterministic.
2. **Opt-in, off by default, and visibly labelled** wherever it is
   producing text, so a user always knows which answers are measured and
   which are generated.
3. **Offline degradation is silent and complete.** With no network or no
   key, Pilot falls back to v1 and loses no *measured* capability.

### 9.3 Where Pilot lives

Four surfaces, in descending order of how often they are seen:

1. **Inline, attached to the thing being explained.** The dominant form.
   A quiet marker beside a term, a statistic or a control; clicking gives
   a short explanation *in place*, not in a panel elsewhere. This is
   already the shape of the existing contextual-help system and it is the
   right one, because an explanation that requires travel is an
   explanation most people skip.
2. **The Home "What to do next" region** (§5.4), where Pilot's most
   consequential output lives permanently and unobtrusively.
3. **A right-side panel, opened deliberately** (`Ctrl+/` or the frame
   affordance), for questions and longer explanations. It overlays; it does
   not reflow the workspace, because a panel that shoves the chart sideways
   is a panel users learn not to open.
4. **The Journal**, as a written note on a closed trade — the highest-value
   place Pilot can speak, because the outcome is known and the lesson is
   concrete.

Pilot has **no floating bubble, no badge count, no proactive pop-up, and
no avatar.** The affordance is a word in the frame.

### 9.4 Personality

**Pilot is a senior colleague who has seen a thousand of these and has
nothing to prove.**

| Trait | In practice |
| --- | --- |
| **Brief** | Two sentences by default. Depth on request, never pre-emptively. |
| **Evidenced** | Every claim about the user carries its sample size. Every claim about the market carries its measurement. |
| **Plain** | Explains a term before using it, once. Never uses jargon to sound credible. |
| **Non-predictive** | Never forecasts a price, never says a trade "should" work. Describes conditions and history. |
| **Unflappable** | Identical register in a drawdown and in a winning streak. |
| **Willing to not know** | "I don't have enough of your trades to say" is a complete, respectable answer, and it is required by P3. |
| **Never flattering** | No "great trade!". A good decision is described, not praised. |
| **Never scolding** | A bad habit is named with its evidence and its cost, not with disapproval. |

**Voice, calibrated:**

> ✅ "Delta is roughly the chance this finishes in the money — 0.54 means
> about 54%. It also tells you the option moves about $0.54 for every
> $1 SPY moves."
> ❌ "Delta (Δ) is the first derivative of option price with respect to
> underlying price."
> ❌ "Delta is basically how much your option cares about the stock! 📈"

> ✅ "You've closed 26 trades with 0–2 days to expiry and won 31% of them,
> against 64% on everything else. That gap is unlikely to be chance
> (p=0.004)."
> ❌ "You're trading too close to expiry — this is hurting your
> performance."

> ✅ "I don't have enough closed trades to say anything about your exit
> timing yet. I'll need about fifteen more."
> ❌ "Your exit timing looks solid!"

> ✅ "This is down 34%. Your stop is at $2.10, about $0.30 away."
> ❌ "Don't panic — drawdowns are a normal part of trading!"

That last pair matters more than it looks. In a loss, a user needs the
numbers and the plan, not emotional management from software. Reassurance
that was not asked for reads as condescension precisely when trust is
thinnest.

### 9.5 When Pilot must stay silent

The silence rules are the most important part of this section, because the
failure mode of every AI assistant ever shipped is talking too much.
Pilot **does not speak** when:

1. **An order is being composed or committed.** From the moment a contract
   is selected to the moment the order is placed, Pilot says nothing
   unprompted. Interrupting a person mid-decision about money is the
   worst possible moment to be helpful. The single exception is a *risk
   condition* — and that surfaces as a risk warning in the ticket, in the
   interface's own voice, not as Pilot commentary.
2. **The user is in a drawdown**, unless asked. No unprompted analysis of
   a losing position. It is not helpful and it reads as gloating.
3. **It would repeat itself.** Any explanation shown and dismissed does
   not reappear unprompted. Ever.
4. **The evidence is insufficient.** Silence is correct where a claim
   would not survive P3. Pilot does not fill a gap with a generality.
5. **The user is typing, drawing or dragging.** No interruption during
   direct manipulation.
6. **A scan, a fill or a price tick occurred.** Data arrival is never a
   reason to speak.
7. **The user has declined that category of help.** Declines are
   permanent, per category, and are respected without a re-offer.
8. **It has already spoken unprompted twice in the session.** A hard cap.
   Beyond it, Pilot is available but never volunteers again until the next
   session.

Rule 8 is worth stating as a design value: **a mentor who speaks twice a
session and is right both times is trusted; one who speaks twenty times is
muted.** The cap is not a limitation on Pilot's usefulness — it is the
mechanism that protects it.

> **First-time trader —** a patient expert is attached to every confusing
> word on screen, and never once makes them feel watched.
> **Experienced trader —** silent by default, two keystrokes away,
> answering with numbers rather than prose. It costs nothing to have on.
> **One system —** every explanatory voice in the product — tooltips,
> tutorials, coach reviews, engine reasoning, the status line, mobile
> notifications — is Pilot. One character, one register, one place the
> user's questions go, regardless of which destination raised them.

---

## 10. Notifications

### 10.1 What is wrong today

The Dashboard's notification panel has no history, no filtering, no
grouping and no click-through to its subject. Once a notification scrolls
away it is gone. The backend is in better shape than the surface — the
notification service already distinguishes kinds, derives severity, knows
what is pushable, and supports dismissal — so this is a presentation
problem, not a capability gap.

### 10.2 The severity ladder

Four levels, and the level determines the surface, not the styling.

| Level | Examples | Surface |
| --- | --- | --- |
| **Ambient** | Scan completed, quote refreshed, watchlist updated | The status line only. Never an entry, never a toast. |
| **Informational** | Order filled, position opened, backtest finished | Inbox entry + brief toast. |
| **Attention** | Stop approaching, order rejected, provider degraded, position down >X% | Inbox entry + persistent toast until acknowledged. |
| **Critical** | Trading halted, daily loss limit reached, order failed after retries, data unavailable for an open position | Inbox entry + a banner that stays until resolved or dismissed. |

### 10.3 The inbox

One inbox, reachable identically from every destination and from mobile,
holding a persistent, grouped, filterable history — closing H5 from
`ROADMAP-V3-UX.md`.

Five rules:

1. **Every entry has a destination.** Clicking a notification navigates to
   the object it is about, with context set. An entry that leads nowhere
   should not have been an entry.
2. **Related events group.** Four fills on one symbol are one expandable
   entry, not four.
3. **Read state is per-entry and persists**, so an inbox opened on the
   desktop is not unread again on the phone.
4. **Entries expire on relevance, not on time.** "Stop approaching"
   disappears when the position closes. A stale warning about a closed
   position is misinformation.
5. **A notification is never the only place a fact lives.** If the account
   is halted, the halt is visible on Home regardless of whether the
   notification was read. Notifications are a *log*, never a *channel*.

### 10.4 The interruption budget

**Modal interruption is permitted for exactly two things:**

1. **The commit review** (§6.5), which the user initiated.
2. **A critical condition that changes what the user can do** — trading
   halted, or an open position for which data can no longer be fetched.

Everything else is a toast, a banner or an inbox entry. This is a hard
budget, not a guideline. Every product that erodes it does so one
justified exception at a time.

### 10.5 Toast behaviour

- Bottom-right, stacking to a maximum of three, oldest collapsing into
  "+2 more" (closing N4).
- Informational toasts auto-dismiss at 5s; attention toasts persist until
  acknowledged.
- **A toast never announces something already visible on screen.** A fill
  that appears in the positions rail the user is looking at does not also
  produce a toast. This rule alone removes most notification clutter.
- Toasts never cover the ticket, the commit control or the chain.

> **First-time trader —** a quiet app that speaks when something actually
> happened, in plain language, with a link to the thing it happened to.
> **Experienced trader —** ambient events stay ambient; the inbox is a
> searchable log rather than a disappearing feed; nothing steals focus
> mid-order.
> **One system —** one inbox, one severity ladder, one voice, shared by
> desktop toasts, the tray tooltip, the banner and mobile push. A user who
> learns what "attention" means on the desktop knows what it means on
> their phone.

---

## 11. The visual design system

Per the brief, this defines **philosophy and structure**, not a palette.
Specific hex values are chosen during Phase 1 implementation against the
constraints below and recorded here as an appendix at that point.

### 11.1 Token architecture — three layers

The current stylesheet has a good instinct (custom properties for colour,
radius, timing and type) and one structural gap: the tokens are a flat
list of *values*, so components pick a value directly and there is no
layer at which meaning is expressed. Three layers:

```
   PRIMITIVE     ->    SEMANTIC        ->    COMPONENT
   raw values          roles                 usage
   ------------------------------------------------------------------
   neutral-900         surface-base          ticket-background
   green-500           value-positive        pnl-gain
   blue-500            action-primary        commit-fill
   space-3             gap-related           ticket-field-gap
```

- **Components reference semantic tokens only.** No component may
  reference a primitive. This is what makes a theme change a token change
  rather than an audit.
- **A new semantic token requires a stated meaning.** "Another grey" is
  not a meaning. This is the discipline that prevents the drift
  `ROADMAP-V3-UX.md` already found in the type scale: fourteen font sizes
  in active use, hardcoded, because every new component invented its own.
- **Every semantic token is defined in both themes** at definition time,
  never patched into the second theme later.

### 11.2 Colour philosophy

**Colour is meaning. Anything with no meaning is neutral.**

The budget:

- **One accent.** It means "the primary action here." It appears at most
  once per view. If two things on a screen are accented, one of them is
  wrong. (Today the accent is used for the primary button, active nav,
  focus rings and links simultaneously — which is defensible for focus and
  links, and is why those get their own semantic roles pointing at the same
  primitive rather than being the same token.)
- **Three status colours** — positive, negative, caution — used *only* for
  value direction and system state. Never for decoration, never for
  category, never for emphasis.
- **A neutral ramp** for everything else: surfaces, text, borders,
  dividers, disabled states, charts, tables.
- **No categorical palette in the core UI.** Where multiple series must be
  distinguished (a backtest comparison), use a sequence derived from the
  neutral ramp plus one accent, differentiated additionally by line
  pattern and by direct labelling.

Four hard rules:

1. **Nothing is encoded in hue alone.** Every positive/negative value
   carries a sign, and where space allows a directional glyph. This is
   §15.2 and it is not merely an accessibility concession — it also makes
   a greyscale screenshot in a bug report readable.
2. **The interface does not change temperature with performance.** No red
   washes, no green glows, no shifting backgrounds. §1.3.
3. **Semantic colour is reserved for semantics.** Green is *gain*. It is
   never "go," never "confirm," never "success" as decoration. A confirm
   button is the accent, not green — otherwise "green means gain" stops
   being true and the user must learn context-dependence.
4. **Contrast targets are minimums, not goals**: 4.5:1 for body text,
   3:1 for large text and meaningful non-text elements, in both themes, at
   every Surface Level, verified rather than eyeballed.

### 11.3 Dark and light

Dark is the default and the reference design, which matches both the
product's context and its current identity. Light is a first-class peer,
not an inversion.

- **Dark mode is not black.** Pure black with pure white text vibrates and
  fatigues. Surfaces sit above a near-black page; text sits below pure
  white. The existing tokens already do this correctly and the ratios are
  worth preserving.
- **Light mode is not inverted dark.** Elevation reads differently:
  in dark, higher surfaces are *lighter*; in light, higher surfaces stay
  white and separate by shadow and border. Applying dark's elevation logic
  to light produces the grey-on-grey mud that most "light themes" are.
- **Status colours are re-chosen per theme**, not reused. A green that
  passes contrast on near-black fails on white.
- **Charts follow the theme**, including grid, axis, baseline and
  drawings, and the candle colours use the same positive/negative semantic
  roles as P&L so the chart and the numbers agree.

### 11.4 Spacing and grid

**A 4px base unit with a named scale.** Named steps by *purpose*, not by
size, so a component picks a relationship rather than a number:

| Step | Value | Purpose |
| --- | --- | --- |
| `space-0` | 0 | Deliberate adjacency |
| `space-1` | 4px | Within a control |
| `space-2` | 8px | Between tightly related elements |
| `space-3` | 12px | Between fields in a group |
| `space-4` | 16px | Between groups in a panel |
| `space-5` | 24px | Between panels |
| `space-6` | 32px | Between bands |
| `space-7` | 48px | Page-level separation |

**A 12-column grid** with a 24px gutter for destination layouts. Home's
bands are 5/5/2 (metrics), 7/5 (positions/next), 7/5 (equity/watchlist).
Trade is 8/4 with a horizontal split in the main column.

**Density is a setting.** Comfortable (default at Levels 1–2) and Compact
(default at 3–4) change the spacing scale's applied multiplier and table
row heights only. They never change what is shown — that is Surface
Level's job, and conflating the two axes would reproduce exactly the
coupling problem §4.7 exists to prevent.

### 11.5 Typography

**One family for interface text, one for numerals.** The numeric family
must be tabular by default so columns align without per-cell overrides,
which the product already does correctly and which must survive the
rebuild.

**A ratio-based scale, named by role, expressed in rem** so OS text-size
settings and the existing large-text mode work by definition rather than
by a parallel set of overrides. Roles:

| Role | Use |
| --- | --- |
| `display` | The account value on Home. One per screen at most. |
| `title` | Destination titles. |
| `heading` | Panel headings. |
| `body` | Default reading size. |
| `body-strong` | Emphasised values inline. |
| `caption` | Labels, units, metadata. |
| `micro` | Column headers, badges. Never for anything a user must read to make a decision. |

**Three rules.**

1. **Numbers a user acts on are never smaller than `body`.** A strike, a
   price, a P&L, a max loss. This is violated today: several consequential
   figures render at 11–12px.
2. **A screen has at most four type roles in play**, plus `micro` for
   table headers. More than that reads as noise regardless of the values.
3. **Line length caps at ~72 characters** for explanatory prose. Pilot's
   panel, tutorials and empty states all obey it.

### 11.6 Elevation, surfaces and radius

**Elevation is layer, not shadow.** Four levels only: page, panel, raised
(popovers, dropdowns), overlay (modals). Each has a defined surface token
and a defined shadow token in each theme. Shadows are soft, low-opacity
and single — never stacked for drama.

**Radius carries meaning**, and the existing four-value scale is close to
right:

| Radius | Applies to | Meaning |
| --- | --- | --- |
| Small | Inputs, table cells, chips | "This is a field" |
| Medium | Buttons, small cards | "This is interactive" |
| Large | Panels, modals | "This is a container" |
| Full | Pills, avatars, toggles | "This is a state" |

A radius never varies within a category. Mixed radii on sibling elements
is the most common way a competent interface reads as amateur.

### 11.7 Component contracts

Each core component has a *contract* — the states it must define before it
ships. A component missing a state is not done.

**Buttons.** Four levels: primary (accent, one per view), secondary
(bordered), tertiary (text), destructive (negative role, always requiring
a commit gesture). Every one defines: default, hover, active, focus,
disabled, loading. **Disabled always has a reason available** on hover or
adjacent — a dead control with no explanation is the single most
frustrating thing an interface can present.

**Text fields.** default, focus, filled, error, disabled, loading. Labels
above, never as placeholders — a placeholder-as-label vanishes exactly
when the user needs to check what they are filling in. Errors sit below
the field, in words, never as a bare red border.

**Tables.** Right-aligned numerics with tabular figures; left-aligned
text; `micro` headers with `scope`; a sticky header on any table that
scrolls; zebra striping only above ~15 rows; row hover; a selected state
that survives a data refresh; an explicit sort indicator. Row density
follows the density setting.

**Cards / panels.** A heading, optional metadata, a body, and an optional
single action. A panel with two competing actions is two panels.

**Modals.** Reserved by §10.4. Every modal defines a title, a body, one
primary action, one dismissal, `Esc` to close, focus trapped, focus
returned on close. Modals never nest.

**Popovers.** For the Flight Status control, symbol jump, expiry picker,
column settings. Dismissed by outside click or `Esc`, positioned to stay
in the viewport, never scroll-locking the page.

**Icons.** One geometric set, one stroke weight, one optical size, drawn
on a consistent grid — as the current inline set already is. Icons are
**never the sole label** on a primary destination or a consequential
action. An icon-only control has an accessible name and a tooltip.

### 11.8 The four state contracts

Every data region defines all four. This is the most commonly skipped part
of a design system and the most visible when skipped.

**Loading.** Skeletons matching the shape of the content, not spinners —
the product already does this well for the chain and must extend it to the
chart, which currently renders a blank canvas for several hundred
milliseconds on first entry. Skeletons appear only after ~200ms, so fast
loads do not flash. Nothing shifts layout when real content arrives.

**Empty.** Per P9: what will be here, why it is not, and the one action
that fills it. Every empty state contains a verb. It is *quiet* — a line
of text and a single action, not an illustration that turns emptiness into
an event.

**Error.** What failed, in one sentence, in the user's terms; what it
means for them right now; what to do. Errors are scoped to the region that
failed — a failed watchlist quote does not blank the page. **Never "an
error occurred."** The market-data layer already produces typed,
distinguishable errors (`MARKET_DATA.md`), including the 401-vs-403
distinction that took real diagnostic work to earn; the UI must not
flatten that back into a generic message. A diagnostic that confidently
names the wrong cause is worse than one that admits uncertainty.

**Success.** Quiet and factual. A placed order shows the order, not a
celebration. Success states never animate beyond a single confirming
transition, and never produce sound.

---

## 12. Motion

### 12.1 The principle

**Motion answers a question about causality. If it answers no question, it
is removed.**

Three legitimate questions:

1. *Where did this come from?* — a popover growing from its trigger, a
   panel sliding from the edge it belongs to.
2. *Where did it go?* — a dismissed toast leaving toward the inbox, a
   closed position animating into the history.
3. *Did that register?* — the commit fill, a button's press, a saved
   field's confirmation.

Everything else — decorative easing, staggered list entrances, parallax,
number roll-ups, attention pulses — is removed. This is the flight-deck
posture expressed in time rather than space: **an instrument that moves is
telling you something moved.**

### 12.2 Durations

The existing two-token approach is right and needs one more:

| Token | Duration | Use |
| --- | --- | --- |
| `instant` | ~100ms | Hover, focus, press. Below conscious perception. |
| `fast` | ~130ms | State changes, tooltips, toggles, popovers. |
| `medium` | ~220ms | Panels, modals, drawer transitions, tab changes. |
| `deliberate` | ~600ms | The commit gesture only. |

`deliberate` is the only duration a user should consciously experience,
and it is the one place slowness is the *feature*: it is the physical
weight of committing capital.

### 12.3 Easing

Three curves, chosen by physics rather than taste: entering elements
decelerate; exiting elements accelerate; elements that move within the
viewport ease both ends. No bounce, no overshoot, no elastic. Playfulness
in motion signals "this is a toy," which is precisely wrong for an
instrument that handles money.

### 12.4 The prohibitions

1. **Nothing animates on data arrival.** Prices, P&L, quantities and
   counts update instantly. A number that animates from one value to
   another is unreadable during the transition — and at the WebSocket's
   ~1s cadence it would be animating permanently.
2. **Nothing animates to attract attention.** No pulsing, no glowing, no
   shaking. Urgency is expressed by hierarchy and position.
3. **Nothing animates while the user is manipulating something.** No
   transitions during a drag, a resize, a chart pan or a text entry.
4. **Layout does not animate.** Panels do not reflow smoothly when content
   changes; content arriving in a skeleton does not slide. Layout animation
   is the largest single source of perceived slowness.
5. **The chart is exempt from all of it.** Chart interaction has its own
   physics owned by the charting layer, and this is settled ground with a
   scar: re-clamping the viewport from a ResizeObserver was tried and
   reverted because it snapped a user's manual price-axis drag back
   mid-gesture (`CLAUDE.md`). **Nothing in this design system may add a
   transition, a transform or an observer to the chart canvas.**

### 12.5 Reduced motion

`prefers-reduced-motion` is already respected and must remain so. The
correct implementation removes *movement*, never *feedback*: transitions
become instant state changes; the commit gesture keeps its 600ms duration
and its fill indicator, because that is a *timing* affordance rather than
decoration, but the fill becomes a stepped progress rather than a
continuous sweep. A user with reduced motion enabled must never lose the
ability to tell that an action registered.

---

## 13. Desktop principles

### 13.1 The window is a workspace, not a page

The single most common failure in desktop apps built with web technology
is designing a *page* and letting it live in a window: centred content,
generous maximum widths, vertical scrolling as the primary axis, and a
narrow column of usable interface surrounded by emptiness on a 27" monitor.

OptionsPilot is a workspace. Regions fill the window, resize with it,
scroll independently, and remember their proportions. The main axis of
navigation is *lateral* (between regions) rather than *vertical* (down a
page). Today's `max-width` constraint on the main content region is a page
convention and should not survive.

### 13.2 Mobile patterns that are forbidden on desktop

- **Hamburger menus.** There is room for the navigation. Show it.
- **Bottom tab bars.**
- **Full-screen modals for small choices.** A dropdown is a dropdown.
- **Pull-to-refresh**, or any gesture-only action without a keyboard and
  menu equivalent.
- **Single-column stacking at desktop widths.** If a layout has one
  column at 1920px, it was designed for a phone.
- **Tap-target sizing applied globally.** 44px targets on a mouse-driven
  interface waste the density that makes a professional tool fast.
  Desktop targets are sized for a cursor; mobile sizes for a thumb (§14).

### 13.3 Responsive behaviour on desktop

Not "mobile responsiveness" — desktop window sizes. The current stylesheet
has exactly one media query in total, and at 1024px wide the header's
controls wrap and the layout clips rather than reflowing.

| Width | Behaviour |
| --- | --- |
| **≥1920px** | Full layout. Home bands 1+2+3 visible. Trade shows chart, chain and ticket simultaneously. Optional fourth region (positions rail) in Trade. |
| **1440–1919px** | Full layout, compressed gutters. Home bands 1+2 above the fold. |
| **1280–1439px** | Navigation collapses to icons with tooltips. Metrics band drops to three plus overflow. Trade's ticket narrows but stays. |
| **1024–1279px** | Navigation is icon-only. Trade splits chain and ticket into a tabbed pair within the right region — the *last* structural sacrifice, made only here. |
| **<1024px** | Not a supported desktop size. A single message states the minimum, rather than degrading into an unusable layout. |

### 13.4 Keyboard

The keyboard is a surface (P8). The full map:

| Key | Action |
| --- | --- |
| `1`–`5` | Destinations |
| `,` | Settings |
| `Ctrl+K` | Command palette |
| `/` | Symbol jump |
| `Ctrl+/` | Pilot panel |
| `?` | Keyboard reference overlay |
| `Esc` | Cancel the current thing: tool, popover, modal, selection — in that order |
| `F` | Chart fullscreen |
| `B` / `S` | Set order side |
| `+` / `-` | Quantity |
| `↑` `↓` | Move chain selection |
| `Enter` | Select contract / open review |
| Hold `Enter` | Commit |
| `Ctrl+.` | Close the focused position (via review + commit) |
| `Ctrl+Shift+N` | New chart window (pop-out) |
| `Alt+1`–`4` | Surface Level |

Three rules: no shortcut fires while focus is in a text input except `Esc`;
no shortcut places an order without the commit gesture; every shortcut is
discoverable from `?` and from the palette, which displays the binding
beside each command it lists — the palette is how shortcuts get *learned*,
not just executed.

**The command palette** is the single most valuable desktop addition in
this document after the trade workspace. It makes the six-destination
navigation viable, because everything that is not a destination is
reachable by name: "backtest", "coach", "api key", "diagnostics", "export
journal", "halt", "scan". It also carries the old tab names as aliases so
the reorganisation teaches itself.

### 13.5 Multi-monitor

Traders use multiple monitors, and this is where a desktop app earns its
existence against a web page.

- **Pop-out windows** for the chart, the ticket and the positions rail.
  Each is a real OS window: movable to another display, resizable,
  independently focusable.
- **A pop-out shares the workspace context** (§4.5). A popped-out chart
  follows the main window's symbol unless explicitly pinned to its own.
  This is what makes multi-monitor feel like one application spread across
  displays rather than several copies of it.
- **Window layout persists** — position, size, display, and which regions
  were popped out — through `RuntimeSettings`, on the same reasoning that
  moved the workspace server-side: a layout stored client-side is a layout
  a profile reset silently discards.
- **Multi-window is additive, never required.** Every capability is
  complete in a single window.

> **First-time trader —** they never encounter any of this; a single
> maximised window is complete, and pop-out is not offered until it is
> plausibly useful.
> **Experienced trader —** a chart on the left display, the ticket on the
> right, positions on a third, all sharing one symbol context, restored on
> launch. This is the reason to run a desktop app instead of a browser tab.
> **One system —** three windows behaving as one workspace, sharing symbol,
> timeframe, selection and Pilot state, is the strongest possible statement
> that OptionsPilot is an environment rather than a program.

---

## 14. The future mobile application

### 14.1 The constraint that determines everything

Mobile design must start from the hosting model, not from the visual
language, because the hosting model bounds what mobile can *be*.

`host/capabilities.py` records that the `ios` and `android` profiles lack
`BIND_LISTENER`, with a stated reason. That is not an incidental gap — it
is the origin of the desktop-as-host model, and `ARCHITECTURE-MOBILE.md`
§1 identifies the hosting decision as the one everything else depends on.

**Therefore: mobile is a companion, not a port.** The engine, the scan
cycle, the journal, the backtester and the data layer live on the desktop
host (or a future cloud host). The phone is a client.

This is a *feature* to design around, not a limitation to hide. It means
mobile does not have to justify carrying the whole product, which is what
makes it possible for mobile to be genuinely simple rather than a
compressed desktop.

**What mobile does:**
monitor positions · manage risk (close, adjust stops) · place simple
orders · receive notifications · read Pilot · review the journal.

**What mobile does not do:**
backtesting · engine transparency · provider configuration · drawing
tools · multi-leg construction · watchlist bulk management.

Attempting the second list is how mobile trading apps become unusable.

### 14.2 Related, not identical

Shared with desktop: the identity, the type scale relationships, the
semantic colour roles, the status line, Pilot's voice, the notification
ladder, the commit-gesture *concept*, and every number's meaning.

Different on mobile: the navigation model, the density, the input grammar,
the information depth per screen, and the physical form of the commit
gesture.

The test: **a user who knows the desktop app should never have to learn a
new vocabulary on mobile — and should never feel the phone is a shrunken
desktop.**

### 14.3 Structure

```
+----------------------------------------+
|  9:41                        .ull  ##  |
|                                        |
|  Portfolio                             |
|  $10,412.55                            |
|  +$212.40  +2.1% today                 |
|                                        |
|      _.-'-._.-''-._.-'''-._            |
|  _.-'                     '-.          |
|                                        |
|  1D  1W  1M  3M  1Y  ALL               |
|                                        |
|  POSITIONS                             |
|  +----------------------------------+  |
|  | SPY 470C  12 Sep    +$142 +18.2% |  |
|  | AAPL 190P 19 Sep     -$38  -4.1% |  |
|  +----------------------------------+  |
|                                        |
|  PILOT                                 |
|  Your 0-2 DTE trades win 31% of        |
|  the time. Worth a look.               |
|                                        |
|  ------------------------------------  |
|   Home    Trade   Portfolio   Journal  |
|    o        .         .          .     |
+----------------------------------------+
```

**Four bottom destinations**, chosen by frequency of use on a phone:

| | Contains |
| --- | --- |
| **Home** | Portfolio value, today, the equity curve, positions, one Pilot line, the status line. |
| **Trade** | Symbol → chain → ticket → commit. Simplified, full-width, one step at a time. |
| **Portfolio** | Positions, working orders, exposure, history. |
| **Journal** | Closed trades, reviews, progress. |

Pilot is a header affordance, not a fifth tab and not a floating bubble.
Settings lives behind a profile control in the header. Research and engine
transparency are absent — deliberately, per §14.1, with a line in Settings
saying where they live.

### 14.4 Gestures — and the rule that governs them

**Every gesture has a visible equivalent.** A gesture is an accelerator for
people who have discovered it, never the only path. This single rule is
what separates a fast mobile app from an undiscoverable one, and it is
also what makes the app usable with assistive technology (§15.4).

| Gesture | Action | Visible equivalent |
| --- | --- | --- |
| Swipe left on a position | Reveal close / adjust | Tap the row → detail sheet |
| Swipe right on a notification | Dismiss | Tap → detail → dismiss |
| Pull down on Home | Refresh | Automatic on foreground |
| Long-press a position | Quick actions | Tap → detail sheet |
| Horizontal swipe between tabs | **Not used** | — |

The last row is deliberate: swiping between top-level destinations makes
every horizontal gesture inside a destination (chart panning, strike
scrolling) ambiguous. The chart alone is sufficient reason to reject it.

### 14.5 Swipe to confirm — why it works, and the original interpretation

The brief names Robinhood's swipe-to-confirm as an inspiration. Copying
its appearance would be both wrong and pointless; what is worth taking is
the *mechanism*, and the mechanism is worth naming precisely because it
solves a real problem that a confirm button does not.

**Why it works — five reasons:**

1. **It cannot be produced by accident.** A tap is one event that a
   mis-touch, a scroll overshoot or a double-tap can generate. A directional
   drag across a distance cannot.
2. **Friction is proportional to consequence** — the gesture takes about a
   second, which is the right price for committing money and the wrong
   price for reading a chart.
3. **It is reversible until the moment it isn't.** Releasing early cancels
   with no dialog, no state and no penalty. Very few confirmations offer a
   graceful abort *during* the confirming act.
4. **It is single-handed and ballistic**, so it works in the physical
   context phones are actually used in.
5. **It replaces a dialog rather than adding to one.** The confirmation
   is fused into the act, so committing is one gesture, not
   tap → read → tap.

**The OptionsPilot interpretation — the Commit Rail:**

```
+----------------------------------------+
|                                        |
|  Buy 1 SPY $470 call                   |
|  12 Sep . 7 days                       |
|                                        |
|  Cost              $395.00             |
|  Max loss          $395.00             |
|  Breakeven         $473.95             |
|  Size              3.8% of account     |
|                                        |
|  ------------------------------------  |
|                                        |
|  +--------------------------------+    |
|  | (>)  Slide to buy              |    |
|  +--------------------------------+    |
|                                        |
|  Release before the end to cancel.     |
|                                        |
+----------------------------------------+
```

Five differences from the inspiration, each deliberate:

1. **The rail restates as it travels.** At rest the label is the action;
   at the midpoint it reads the cost; near the end it reads the maximum
   loss. The user is reading the consequence *while performing* the
   commitment, which is the one moment they are guaranteed to be paying
   attention.
2. **Travel distance scales with position size relative to the account.**
   A 1% position is a short rail. A 20% position is a long one. Physical
   effort tracks financial consequence — a haptic expression of the risk
   model, and something a static control cannot express.
3. **A single haptic tick at the commit point**, before release, so the
   user knows the gesture has qualified without looking. No haptics
   anywhere else in the app; scarcity is what makes it legible.
4. **No success animation.** The rail completes and the screen becomes the
   position. Fills are facts, not achievements — and this is where most
   retail apps quietly become slot machines.
5. **It is not the only path.** A `Place order` button is present for
   assistive technology and for anyone who cannot perform the gesture,
   with an equivalent confirmation step. §14.4's rule has no exceptions on
   the order path.

**And its desktop twin is hold-to-confirm** (§6.6). Same semantics, same
duration philosophy, same early-release cancel, same absence of
celebration — expressed in each platform's native physical grammar.

> **First-time trader —** the most consequential action in the product is
> the one action that is impossible to perform by accident, and the maximum
> loss is under their thumb while they perform it.
> **Experienced trader —** one gesture replaces tap-read-tap, and the rail
> length itself communicates position size before they read a number.
> **One system —** "a deliberate sustained gesture commits capital" is
> learned once and holds on every device the product will ever ship on.

### 14.6 Mobile notifications

Push uses the existing severity ladder (§10.2). Only **attention** and
**critical** are pushed — which the notification service already models
via its pushable predicate, so the phone inherits the desktop's judgement
rather than making its own. Ambient events never push. Read state
synchronises both ways, so an inbox cleared on the desktop is clear on the
phone.

### 14.7 Offline

The phone will be offline regularly, and this must be designed rather than
discovered. Last-known values are shown with an explicit staleness marker
and a timestamp; the order path is disabled with a stated reason rather
than failing on submit; the journal and closed history remain fully
readable because they are static. **A stale number that says it is stale is
useful; a stale number that looks live is a defect** — the same principle
the market-data layer already applies to the difference between "no data"
and "cannot reach data" (`MARKET_DATA.md`).

---

## 15. Accessibility

Current state, measured: focus-visible is correctly implemented globally,
`prefers-reduced-motion` is respected, and there is a large-text mode with
its own token overrides — genuinely better than most hobby trading UIs.
Against that, `ROADMAP-V3-UX.md` found roughly fourteen `aria-*`
attributes across the whole SPA, no live regions, no skip link, and no
keyboard path through the order flow. **A screen-reader user cannot
currently place a trade.** That is the bar this section exists to clear.

### 15.1 Screen readers

- **Landmarks and headings** structure every destination: one banner, one
  navigation, one main, complementary regions for rails, a single logical
  heading order.
- **Every icon-only control has an accessible name**, and it matches the
  visible tooltip.
- **Tables are real tables** with `scope` on headers and a caption. The
  chain, the positions table and the watchlist are the priorities.
- **Every form field has a programmatic label.** No placeholder-as-label —
  which is also §11.7's rule for sighted users.
- **Custom controls announce their state**: segmented controls, the
  expiry strip, the Surface Level control, the commit gesture.

**The live-region rule, which is the subtle one.** The interface receives
WebSocket pushes on a ~1s cadence. Wiring price and P&L updates to a live
region would produce continuous, unusable speech — an accessibility
feature that makes the app *less* usable. Instead:

- Prices and P&L are **not** in a live region. They are readable on
  demand, correctly labelled, at any time.
- **One polite summary region** announces only *meaningful* changes: an
  order filled, a position closed, a stop triggered, trading halted,
  connection lost or restored. Rate-limited to at most one announcement
  per three seconds, coalescing anything that arrives faster.
- **The commit gesture announces its three moments** — started,
  qualified, placed — because a purely visual progress fill is invisible
  otherwise.

### 15.2 Colour vision

Roughly 1 in 12 men has a colour-vision deficiency, and red/green is the
most common form — which is precisely the pair a trading interface uses
for its most important distinction.

- **Never hue alone** (§11.2 rule 1). Sign is always present; direction is
  reinforced by a glyph where space allows.
- **Position matters**: gains and losses occupy consistent positions in
  every layout, so the *place* carries information independent of colour.
- **Charts** distinguish series by pattern and direct label as well as
  colour; candles carry their direction in body fill (hollow/solid) as
  well as hue.
- **A verification pass** simulating deuteranopia, protanopia and
  tritanopia on every destination is part of Phase 1's exit criteria, not
  a later audit.

### 15.3 Large text and zoom

- Type in `rem`, spacing on a scale that grows with it, so the existing
  large-text mode becomes a single root change rather than a parallel token
  set.
- **Layouts survive 200% zoom** without content loss or horizontal
  scrolling of the page. Wide content (chains, tables, charts) scrolls
  inside its own container.
- **No fixed-height containers on text.** A container that clips at large
  text sizes is a bug, and it is the most common one this class of change
  produces.

### 15.4 Keyboard-only

P8 and §13.4 cover the map. Additionally required:

- **A skip-to-content link** as the first focusable element.
- **Focus is trapped in modals and returned on close**, to the element
  that opened them.
- **Focus is never lost.** After a data refresh, a row delete or a panel
  collapse, focus moves somewhere sensible and adjacent — never to the
  document body.
- **Focus is visible on every interactive element**, including custom
  controls, chain rows and chart tools.
- **Tab order follows visual order** in every region.
- **The full order path is keyboard-completable**, end to end, including
  the commit gesture. This is the acceptance test for the whole section.

### 15.5 Reduced motion and reduced transparency

`prefers-reduced-motion` per §12.5 — movement removed, feedback retained.
`prefers-reduced-transparency`, where available, removes blur and
translucency in favour of solid surfaces. Both are honoured automatically
and are also independently settable in Settings › Appearance, because OS
settings are per-machine and a user's needs are not.

### 15.6 The standard

**WCAG 2.2 AA** across every destination, both themes, all four Surface
Levels. Verified by an automated pass in the browser check suite plus a
manual keyboard-and-screen-reader run of the order path per release. The
automated pass is a floor; the manual run is the test that matters,
because "can a screen-reader user place a trade" is not a property any
linter can assert.

---

## 16. Implementation roadmap

Nine phases. Complexity is S (days), M (1–2 weeks), L (2–4 weeks), XL
(4+ weeks) at this project's demonstrated pace. Every phase ships its own
automated browser check, because `index.html` has no automated test
coverage and the existing check scripts are the only thing standing
between a UI change and a silent regression — a rule this codebase already
learned expensively (`chart_check` silently failing for several sessions
before V0.5.2 while a real data-layer defect hid behind it).

### Phase 0 — Foundation and instrumentation · **M** · no dependencies

Token architecture (§11.1) with the three-layer split; the spacing and
type scales replacing the fourteen ad-hoc font sizes; motion tokens;
semantic colour roles defined in both themes; Surface Level plumbed
through `RuntimeSettings` as a workspace property; the context-continuity
store (§4.5). **No visible redesign ships in this phase.**

*Exit:* every existing screen renders from semantic tokens only; a token
audit script fails the build on a primitive referenced from a component;
Surface Level persists across restart; symbol context is shared between
the Charts and Trade surfaces.

*Why first:* every later phase either consumes these or duplicates them.

### Phase 1 — The design system made visible · **M** · needs Phase 0

Component contracts (§11.7) and the four state contracts (§11.8) built as
a real inventory. Light theme brought to parity. Contrast and
colour-vision verification. Reduced-motion audit.

*Exit:* a component inventory page renders every component in every state
in both themes; automated contrast and colour-vision passes are green.

### Phase 2 — Navigation, the frame, and continuity · **L** · needs Phase 0

The six destinations, the command palette, symbol jump, the Flight Status
popover, the keyboard map, `?` overlay, Settings restructured, old names
aliased in the palette. Context continuity guarantees (§4.5) enforced.

*Exit:* the §4.5 test passes — one symbol typed at launch survives a full
loop; every removed nav item is reachable by its old name; the full
keyboard map works and is discoverable.

*Highest risk in the plan.* It touches every screen and it is the phase
that changes where things are. It ships behind a switch with the old
navigation available for one release.

### Phase 3 — Home · **M** · needs Phases 0, 2

The three bands, the status line generator (a new service view model), the
five metrics including Open risk, the What-to-do-next region wired to
`intelligence/`, the no-scroll commitment.

*Exit:* no vertical scroll at 1920×1080 for bands 1–2; the status line
covers every case in §5.3's table; a five-trade history renders coverage
reasons rather than numbers (P3).

### Phase 4 — The trade workspace · **L** · needs Phases 0, 1, 2

The unified workspace, the always-present ticket in five states, quick
picks, the keyboard-navigable spot-anchored chain, Surface-Level column
sets, the review restatement, hold-to-confirm.

*Exit:* the §6.7 keyboard path completes without a mouse; every existing
`OrderManager` guardrail still refuses what it refused before, verified by
the existing tests plus new UI checks; review displays all five required
elements for every order type.

*The most valuable phase in the document.* Prioritise it above Phase 3 if
resources force a choice.

### Phase 5 — Portfolio and Journal · **M** · needs Phases 0, 1, 2

Portfolio as a destination; Journal absorbing Coach reviews and the
progress timeline; `intelligence/` findings inline on individual trades.

*Exit:* every coach and intelligence surface that exists today is reachable
in the new structure with no capability lost.

### Phase 6 — Research · **M** · needs Phases 0, 1, 2

Research as a destination: chart-first exploration, backtest, watchlist
management, engine transparency renamed from "Learning."

*Exit:* nothing from the old Backtest, Watchlist or Learning tabs is
unreachable.

### Phase 7 — Notifications · **S–M** · needs Phases 1, 2

The severity ladder, the persistent inbox with grouping and read state,
toast stacking, the interruption budget enforced. Closes H5 and N4.

*Exit:* read state survives restart; every entry navigates to its subject;
no toast fires for something already on screen.

### Phase 8 — Onboarding and Surface Levels · **M** · needs Phases 1–4

The four onboarding screens, the practice-trade path, first-time inline
explanations, Surface Level applied across every destination, the
evidence-based level offer.

*Exit:* first launch to Home in under 60 seconds including the tour offer;
a Guided user can complete a trade; a Pro user sees no tips.

*Late deliberately.* Onboarding introduces the product; it cannot be built
before the product it introduces exists.

### Phase 9 — Pilot · **L** · needs Phases 2, 3, 5, 8

Pilot v1 (deterministic): the four surfaces, the voice, the silence rules,
the composition layer over glossary, guide, intelligence, coach and engine
reasoning. Pilot v2 only if Open Decision #1 is taken.

### Phase 10 — Desktop polish and multi-monitor · **M** · needs all

Pop-out windows, layout persistence, desktop responsive breakpoints,
density setting, accessibility manual pass, performance verification.

### Mobile — **XL** · needs Phases 0–9 and `ARCHITECTURE-MOBILE.md` §17's decisions

Not scheduled here. `ARCHITECTURE-MOBILE.md` lists the decisions required
before any iOS development begins; §14 of this document is the interface
specification that becomes actionable once those are made.

### 16.1 Dependency summary

```
   Phase 0  Foundation
      |
      +-- Phase 1  Design system
      |      |
      +-- Phase 2  Navigation + continuity
             |
             +-- Phase 3  Home ----------+
             +-- Phase 4  Trade ---------+
             +-- Phase 5  Portfolio/Journal
             +-- Phase 6  Research       |
             +-- Phase 7  Notifications  |
                                         |
                    Phase 8  Onboarding <+
                          |
                    Phase 9  Pilot
                          |
                    Phase 10  Desktop polish
                          |
                    Mobile (gated on ARCHITECTURE-MOBILE.md §17)
```

### 16.2 Sequencing rules

1. **No phase ships without its browser check.** Frontend regressions here
   are silent by construction.
2. **No phase ships with a red suite.** Existing project rule, unchanged.
3. **Every phase is independently shippable.** No phase leaves the product
   in a state that requires the next one to be usable.
4. **The old surface stays reachable for one release** after Phase 2, so a
   navigation change can be reverted without a rollback.
5. **Behaviour does not change.** This is a presentation programme. Any
   phase that finds itself needing to change a gate, a fill rule or a risk
   calculation has found a bug or a scope error, and it is escalated rather
   than absorbed.

---

## 17. Success metrics

Measurable, with a stated measurement method. A metric nobody can compute
is a wish.

### 17.1 First-time user

| Metric | Target | Measured by |
| --- | --- | --- |
| Onboarding completion time | < 60s, median | Timestamps between first launch and Home |
| First paper trade | < 3 min from first launch, ≥ 80% of users who attempt one | Time from launch to first `OrderManager.place` |
| Onboarding abandonment | < 10% | Sessions reaching screen 1 but not Home |
| Comprehension | ≥ 90% can state what they bought and its max loss | Manual usability testing, 5 participants per release |
| Unexplained-term encounters | 0 undefined jargon at Surface Level 1 | Static audit: every term on a Level-1 surface has a glossary entry |

### 17.2 Experienced user

| Metric | Target | Measured by |
| --- | --- | --- |
| Clicks to the trade ticket from anywhere | ≤ 2 | Interaction audit |
| Keystrokes to a reviewed order on a named symbol | ≤ 6 | Keyboard path audit |
| Symbol re-entry across a full loop | 0 | The §4.5 continuity test |
| Destination switches per completed trade | ≤ 1 | Interaction audit (today: ≥ 2) |
| Time from launch to "I know my exposure" | < 2s | The Home no-scroll check |

### 17.3 The interface itself

| Metric | Target | Measured by |
| --- | --- | --- |
| Vertical scroll on Home at 1920×1080 | 0px for bands 1–2 | Browser check |
| Scroll to reach open positions | 0px at ≥ 1440×900 | Browser check |
| Distinct font sizes in the stylesheet | ≤ 8, all from the scale | Token audit script |
| Component references to primitive tokens | 0 | Token audit script |
| Console errors on any destination | 0 | Existing `browser_check` |
| Contrast failures (AA) | 0, both themes, all four levels | Automated pass |
| Interactive elements without an accessible name | 0 | Automated pass |
| Full order path completable by keyboard only | Yes | Manual, per release |
| Full order path completable by screen reader | Yes | Manual, per release |
| Time to first meaningful paint on destination switch | < 100ms | Instrumented |

### 17.4 Pilot and notifications

| Metric | Target | Measured by |
| --- | --- | --- |
| Unprompted Pilot messages per session | ≤ 2, hard cap | Enforced in code, asserted by test |
| Pilot suggestions dismissed without engagement | < 40% | Interaction telemetry (local) |
| Pilot claims without evidence | 0 | Every claim carries `n`; asserted by test |
| Notifications per session (attention + critical) | ≤ 5, median | Notification service counters |
| Toasts duplicating on-screen information | 0 | Audit rule, asserted by test |
| Modal interruptions not in §10.4's two cases | 0 | Audit rule, asserted by test |

### 17.5 The metrics that would indicate failure

Explicit tripwires. If any of these appears, the design is being eroded:

- A new panel added to Home without displacing one.
- A second symbol input anywhere in the product.
- A capability reachable at one Surface Level but absent at another (as
  opposed to *hidden* at another).
- A number rendered without its sample size where a sample size applies.
- A third mode axis that couples to `operating_mode` or `trading_mode`.
- An interruption added outside §10.4's two cases.
- A hue-only encoding of gain/loss.
- Any animation on a value change.

---

## 18. Design review — every major decision against the three questions

Per the requirement that the document be checked against itself. Each row
is a decision from this document; each column is one of §0.1's questions.

| # | Decision | First-time trader | Experienced trader | One unified system |
| --- | --- | --- | --- | --- |
| 1 | **Six destinations, not nine** (§4.2) | Six plain-English questions instead of nine mixed-category features; no "Learning" mislabel to fall into | Two most-used destinations on keys 1–2; everything else by name in the palette | The six are one workflow loop, so the nav *is* the mental model of the product |
| 2 | **Charts merged into Trade** (§4.3, §6.2) | One place to look at a stock and buy an option on it | Zero destination switches per trade, down from at least one | Removes the clearest evidence of "separate tools": a collapsible chart that existed to bridge two tabs |
| 3 | **Watchlist demoted to context** (§4.3) | It is where they need it, when they need it, without a detour | Always visible while trading rather than a destination away | Context belongs to the workspace, not to a screen |
| 4 | **Context continuity** (§4.5) | Never has to retype something they already told the app | Types a symbol once per idea rather than once per screen | The single mechanism that makes six destinations feel like one environment |
| 5 | **Flight Status replaces two header segment controls** (§4.7) | One sentence teaches that the two axes are independent | Reclaims header space; state they consult constantly is one legible line | The system reports its own condition in one voice, in one place |
| 6 | **Home's three bands** (§5.2) | The top of the screen tells them whether they need to do anything | Exposure and anomalies without scrolling, in fixed positions | The status line and the findings are the same objects that appear on mobile and in notifications |
| 7 | **The status line** (§5.3) | The one sentence they can always trust | A one-second read of system state | One self-report reused by desktop, tray, mobile and notifications rather than reinvented per surface |
| 8 | **"What to do next" as permanent real estate** (§5.4) | Software tells them something true about their own trading, unprompted | Ranked, corrected findings with `n` and p — no scanning for anomalies | One engine, one ranking, surfaced in Home, Journal, Pilot and push |
| 9 | **Always-present ticket in five states** (§6.2) | Learns the shape of an order before committing to a contract | No round-trip to discover order affordances | The ticket is a persistent instrument, not a screen that appears |
| 10 | **Quick picks** (§6.3) | "Buy a call" is one click, then the chain explains what it chose | Removes a manual chain scan from the common case | The same intents the AI engine and Pilot speak, so a suggestion opens as a populated ticket |
| 11 | **Keyboard-navigable, spot-anchored chain** (§6.4) | Opens where the interesting strikes are, not at the top | Closes the worst keyboard gap on the order path | The keyboard behaves identically in every table in the product |
| 12 | **Review as consequence restatement** (§6.5) | Max loss and "if you do nothing" stated in English before commitment | Five facts in fixed positions, scannable in a second | Same five elements on desktop and mobile, in Pilot's register |
| 13 | **Hold to confirm** (§6.6) | Cannot place an order by accident | 600ms replaces a modal round-trip, from the keyboard | One commit concept across desktop, mobile and every destructive action |
| 14 | **No account, no broker step in onboarding** (§7.1) | Under a minute, nothing mandatory, and told immediately that nothing can cost money | One click to skip | Onboarding tells the truth about what the product is, so nothing later contradicts it |
| 15 | **Surface Levels** (§8) | A first screen made of words they know | One choice, permanently revealed, no re-teaching | One workspace property applied uniformly — one product at a chosen depth, not two editions |
| 16 | **Local reveal without changing level** (§8.3) | Curiosity costs one click, not a settings trip | Never blocked by someone else's idea of their level | Depth is a property of a moment, not a wall between user classes |
| 17 | **Pilot as a role, not a chat window** (§9.3) | An expert attached to every confusing word, in place | Silent by default, two keystrokes away, answers in numbers | Every explanatory voice in the product is one character with one register |
| 18 | **Pilot's silence rules** (§9.5) | Never watched, never interrupted mid-decision | Costs nothing to leave enabled | A mentor trusted because it is rare — the flight-deck posture as behaviour |
| 19 | **Deterministic Pilot v1** (§9.2) | Instant, offline, and constitutionally unable to invent a statistic about them | Auditable — every claim traces to a measurement | Pilot is a view over the intelligence the system already has, not a separate brain |
| 20 | **Severity ladder + persistent inbox** (§10) | Plain-language events that link to what happened | Ambient stays ambient; a searchable log, not a vanishing feed | One inbox and one ladder shared by toasts, banners, tray and push |
| 21 | **Two-case interruption budget** (§10.4) | Nothing steals attention unless it changed what they can do | Nothing steals focus mid-order | A single enforced rule, so "the app interrupted me" always means something happened |
| 22 | **Semantic token layer** (§11.1) | Consistency they read as quality without noticing | Predictable interface; no per-screen relearning | Mechanically guarantees that every screen is the same product |
| 23 | **Colour is meaning only** (§11.2) | One accent means "the thing to press here" | No decoding decorative colour | Green means gain everywhere, forever — one vocabulary |
| 24 | **Motion answers causality only** (§12) | Nothing moves unexpectedly; nothing feels alarming | Nothing to wait through; values never animate | Calm is a system property, not a per-screen choice |
| 25 | **Desktop is a workspace, not a page** (§13.1) | A maximised window is complete and coherent | Uses the whole monitor; regions persist | The window *is* the environment |
| 26 | **Multi-monitor with shared context** (§13.5) | Never encountered until useful | The reason to run a desktop app at all | Three windows behaving as one workspace is the strongest possible statement of §P11 |
| 27 | **Mobile as companion, not port** (§14.1) | A simple phone app that does the few things phones are good at | Monitor and manage without a compressed desktop | Honest about the hosting model, so mobile never contradicts what desktop is |
| 28 | **The Commit Rail** (§14.5) | Impossible to trade by accident; max loss under their thumb | One gesture replaces tap-read-tap; rail length signals size | The mobile grammar of the desktop's hold — one concept, two physical forms |
| 29 | **Live regions for events, not prices** (§15.1) | — | — | An accessible product is one product; an app that speaks every tick is a different, unusable one |
| 30 | **Every gesture has a visible equivalent** (§14.4) | Nothing is hidden behind a gesture they don't know | Gestures as accelerators once discovered | The same actions exist on every input modality |

**Rows where a column is deliberately empty** (row 29) are recorded rather
than filled, per P3: a decision that does not serve a given persona should
say so rather than invent a benefit.

---

## 19. Open decisions

Decisions this document deliberately does not make, because they are the
product owner's and each changes what gets built.

| # | Decision | Why it matters | Recommendation |
| --- | --- | --- | --- |
| **1** | **Does Pilot get an LLM?** (§9.2) | Determines whether free-form Q&A exists. Changes offline behaviour, dependencies, packaging size, cost model and the deterministic-by-design commitment in `CLAUDE.md`. | **Ship v1 deterministic.** Revisit after Phase 9 with real usage showing which questions users actually ask and whether determinism failed to answer them. |
| **2** | **Does an account system exist, and when?** (§7.1) | Gates mobile pairing and cross-device sync. `services/sync.py` is an inventory that syncs nothing today. | Not before mobile. Introduce it when it buys something, in context, never in onboarding. |
| **3** | **Old navigation kept for one release, or a clean cut?** (§16, Phase 2) | Risk management for the highest-risk phase. | Keep it behind a switch for one release. |
| **4** | **Density default at Surface Levels 3–4** (§11.4) | Compact suits professionals and reduces first-impression quality for everyone else. | Compact at 3–4, comfortable at 1–2, independently overridable. |
| **5** | **Are pop-out windows real OS windows or in-app panes?** (§13.5) | Real windows are the multi-monitor payoff and add meaningful pywebview and lifecycle complexity — an area with known hazards around the message pump (`CLAUDE.md`). | Real windows, in Phase 10, with the close-handler discipline the desktop lifecycle already documents. |
| **6** | **Does Surface Level sync across devices?** (§8, §14.2) | A user may want Guided on a phone and Full on a desktop. | Per-device, with an offer to match on first mobile launch. |
| **7** | **Which telemetry, if any, backs §17's metrics?** | Several targets need instrumentation the product does not have, and this is a privacy-sensitive local-first app. | Local-only counters, no transmission, visible in Settings › About, using the same measured-feature-usage mechanism `guide.py` already has. |
| **8** | **Is Research one destination or two?** (§4.2) | It carries the chart, backtest, watchlist management and engine transparency — the widest scope of the six. | Ship as one with sections; split only if Phase 6 shows it does not cohere. |

---

## 20. Appendix — where everything went

Reference map for anyone navigating between the two structures.

| Today | Tomorrow | Palette alias |
| --- | --- | --- |
| Dashboard tab | Home | "dashboard" |
| Charts tab | Trade (chart region) / Research | "charts", "chart" |
| Trade tab | Trade | "trade", "ticket", "order" |
| Coach tab | Journal › Review | "coach", "review" |
| Watchlist tab | Context rail; Research › Watchlist | "watchlist" |
| Journal tab | Journal › Trades | "journal", "trades" |
| Backtest tab | Research › Backtest | "backtest" |
| Learning tab | Research › Engine | "learning", "weights", "engine" |
| Settings tab | Settings (five groups) | "settings" + each group |
| Header: operating mode | Flight Status popover | "ai mode", "human mode" |
| Header: trading mode | Flight Status popover | "conservative", "high risk", "custom" |
| Header: Scan now | Home primary action + palette | "scan" |
| Header: cycle pill | Flight Status | "scan status" |
| Header: Learn button | Contextual help + palette | "learn", "tour" |
| Header: Help menu | Palette (six entries) | each by name |
| Dashboard: intelligence panel | Home band 2 + Journal › Progress | "intelligence" |
| Dashboard: AI opportunities | Home band 2, ranked | "opportunities" |
| Dashboard: notifications | Notification inbox (global) | "notifications" |
| Trade: positions/working/history | Portfolio + Trade positions rail | "positions", "orders" |

---

## 21. Related documents

| Document | Relationship |
| --- | --- |
| `CLAUDE.md` | Binding constraints. Nothing here overrides them. |
| `AI_CONTEXT.md` | Product vision and the never-change list. §1 of this document extends its "Current UI philosophy" section. |
| `ROADMAP-V3-UX.md` | The audit that preceded this. Its open findings (H5, N2, N4) are absorbed into Phases 2 and 7. |
| `ONBOARDING.md` | The guided-help architecture §7.3 preserves wholesale, including the ids-only frontend/backend contract. |
| `TRADING_INTELLIGENCE.md` | The evidence rules P3 and §5.4 are the UI expression of. |
| `ARCHITECTURE-PLATFORM.md` | Why the service layer can feed a redesigned UI without a rewrite. |
| `ARCHITECTURE-MOBILE.md` | The hosting decisions §14 depends on. |
| `WORKSPACE_ARCHITECTURE.md` | Why context is server-owned state (§4.5). |
| `MARKET_DATA.md` | The typed-error vocabulary §11.8's error states must not flatten. |
| `ARCHITECTURE.md`, `MODULES.md` | Where the redesign attaches. |
