# UI_V2_VISUAL_EXPLORATION.md — visual direction, three explorations and a recommendation

**Status:** presentation for decision. **Version:** 1.0.
**Inputs (frozen, unmodified by this document):** `UI_V2_DESIGN.md`
(philosophy), `UI_V2_WIREFRAMES.md` (layout), `DESIGN_SYSTEM_V2.md`
(components and tokens).

This is the last document before engineering starts. Its purpose is to
settle *how OptionsPilot looks* — the part the three frozen documents
deliberately did not decide — and to settle it completely enough that
implementation invents nothing.

It contains no HTML, CSS, JavaScript or implementation code.

---

## 0. What is being explored, and what is not

A visual exploration that arrives *after* a design system has been frozen
is a different exercise from the usual one, and pretending otherwise would
produce three directions that quietly contradict the documents they claim
to serve. So this section is the frame for everything below.

### 0.1 What is already settled and cannot be explored

| Settled by | What it fixes |
| --- | --- |
| `UI_V2_DESIGN.md` | The seven principles. Six destinations. Surface Levels. Pilot's behaviour and silence. The two-case interruption budget. The commit gesture. Success metrics. |
| `UI_V2_WIREFRAMES.md` | Four layout archetypes. The position of every region on every screen. The keyboard map. The responsive matrix. What each screen does when empty, loading or failing. |
| `DESIGN_SYSTEM_V2.md` | Every colour value and its measured contrast. The type scale. The spacing scale. Row heights per density. The 27 components and their states. The closed catalogue of 17 animations and the never-animate list. WCAG 2.2 AA. |

None of that is on the table. A direction that needs to move a region, add
a hue, animate a value, or drop a state is not a direction — it is a
request to unfreeze, and it is priced as one.

### 0.2 What a visual direction is still free to decide

Twelve variables. The frozen system permits a wide range on each and takes
a position on none of them.

| # | Variable | Range the system permits |
| --- | --- | --- |
| V1 | **Surface strategy** | How many of the 12 neutral steps are visibly used, and which. Two-step (page + overlay) through four-step (page → panel → inset → raised). |
| V2 | **Border policy** | Borders decorative and pervasive, functional only, or nearly absent. `border.control` on inputs is mandatory; everything else is a choice. |
| V3 | **Radius character** | `UI_V2_DESIGN.md` §11.6 assigns four radii their meanings (Small, Medium, Large, Full) and `DESIGN_SYSTEM_V2.md` references two of them by token, but **no document gives any of them a value**. A direction sets the values and therefore the character. |
| V4 | **Density expression** | Row heights are fixed (40/28px) but panel padding, gutter usage and the relationship between density and Surface Level are open. |
| V5 | **Type dominance** | Which of the nine roles carry hierarchy — size-led, weight-led, or case-led — and how much uppercase micro labelling appears. |
| V6 | **Panel treatment** | Whether a region is a framed pane, a surface without a frame, or a flush zone separated only by space. |
| V7 | **Card treatment** | Whether metric tiles are separate objects, cells of one object, or unbounded text groups. |
| V8 | **Navigation appearance** | Icon-led, label-led, or balanced. Whether the rail carries its own surface. How the active state is built from the permitted signals. |
| V9 | **Chart chrome weight** | Grid visibility, axis prominence, plot-area surface, crosshair weight. The data marks are fixed; the housing is not. |
| V10 | **Accent budget** | The rule is "at most once per view." A direction may spend less. |
| V11 | **Motion subset** | The catalogue of 17 is a ceiling, not a requirement. A direction may use fewer. |
| V12 | **Iconography presence** | Where icons appear alongside labels, given that §8.3 already fixes where they may *replace* labels. |

Each direction below sets all twelve, plus the eighteen attributes the
brief asks for. Each closes with a **conformance ledger** naming any place
it would need the freeze relaxed, and what that costs.

### 0.3 How to read the mockups

The mockups in §5 do not restate layout — the layout is identical across
all three directions, because it is frozen. They describe **what the eye
does**, in order, and **why the treatment makes it do that**. Proportions
are given as percentages of the content area at 1920×1080. Spacing is given
in scale tokens.

---

## 1. The three directions at a glance

| | **A — Quiet** | **B — Terminal** | **C — Flight Deck** |
| --- | --- | --- | --- |
| One line | A single sheet of dark glass | An instrument grid | Zoned instruments on a dark canvas |
| Reference points | Linear, Arc, Apple system apps | Thinkorswim, Bloomberg, Active Trader Pro | Aircraft avionics, Stripe's dashboards, Figma's canvas |
| Hierarchy from | Space and weight | Rules and framing | Zoning, then value |
| Surfaces used | 2 | 4 | 3 |
| Borders | Nearly none | Everywhere | Functional only |
| Density | Comfortable-led | Compact-led | Both, by Surface Level |
| Accent appearances per view | 0–1 | 1 | 1 |
| Animations used | 6 of 17 | 5 of 17 | 13 of 17 |
| Requires unfreezing | No | **Yes — one** | No |
| First impression on a beginner | Approachable | Intimidating | Composed |
| First impression on a professional | Underpowered | Credible | Credible |

---

## 2. Exploration A — **Quiet**

### 2.1 Overall visual identity

One continuous dark surface. There are no boxes. Regions are separated by
space and announced by a heading, and nothing has an edge unless the edge
does work. The product looks like a document that happens to be alive.

The organising conviction: **if the spacing is right, the boxes are
redundant** — which is `DESIGN_SYSTEM_V2.md` SP-3 taken to its literal
conclusion. Quiet is the direction that treats SP-3 not as a test but as
the design.

### 2.2 First impression

Spacious, expensive, unhurried. A first-time user's shoulders drop. The
screen looks like it contains fewer things than it does, because the things
are not fenced. The eye is drawn to the largest text on screen and then
allowed to wander without obstruction.

### 2.3 Emotional feeling

Relief. Then, for an experienced trader, a moment of doubt: *is this a real
trading application?*

### 2.4 Personality

Understated to the point of reticence. Quiet does not announce its own
sophistication. It is the direction most confident that the user will find
things, and that confidence is also its risk.

### 2.5 The eighteen attributes

| Attribute | Quiet |
| --- | --- |
| **Information density** | Low by default. Comfortable rows (40px) at Levels 1–3; Compact only at Level 4, where it becomes a different-looking product. |
| **Typography style** | Weight-led. `text.title` for destinations, `text.heading` at 600 for region headings, `text.body` everywhere else. Uppercase `text.micro` appears **only** on table column headers — nowhere else. Line heights at the generous end. |
| **Spacing style** | `space.6` (32px) between regions, `space.5` inside them, `space.4` panel padding. Space is the primary structural device and is spent freely. |
| **Panel treatment** | No panel. A region is a heading plus its content, sitting on the page surface, separated from its neighbours by `space.6`. |
| **Card treatment** | Only the Home metric tiles get a surface, and only `surface.base` with `radius.lg`. Everything else is flush. |
| **Navigation appearance** | Label-led. Icons at 16px in `neutral.400`, visually recessive. The rail has no surface of its own. Active state: ink to `neutral.050` plus a 2px `action.primary.text` left edge. **No background wash** — the tint would be the only box in the product. |
| **Chart presentation** | The lightest chrome of the three. Horizontal gridlines only, at `border.subtle` and 40% opacity. No vertical grid. Time labels at four positions, not eight. The plot area is the page surface — the chart is not in a box either. The price axis has no background; only the last-price label is filled. |
| **Colour usage** | The most restrained. Accent appears once per view or not at all — several screens have no accent, because their primary action lives elsewhere. Market colours only on numbers, never as fills or washes. Status pills use tint backgrounds; that is the only fill in the product besides the primary button. |
| **Iconography** | Minimal. Icons in the nav, in row actions, and in the chart toolbar. Nowhere else. No icon in a heading, a status pill, an empty state or a button that has a label. |
| **Animation style** | Six of seventeen: M-1 (destination cross-fade), M-3 (popover), M-4 (skeleton), M-8 (modal), M-12 (commit), M-17 (hover/focus). Nav uses instant, not the indicator slide. Rows appear and vanish without a fade. The product barely moves. |
| **Loading experience** | Headings and labels appear instantly; values are very low-contrast skeletons at `neutral.800`, with the shimmer removed entirely. A loading screen looks like an empty screen with the words already in place. |
| **Empty states** | One line of `text.body` in `neutral.200`, one line of `text.caption` in `neutral.400`, `space.4` of air, one tertiary button. Left-aligned, generous. The quietest empty states of the three. |
| **Notifications** | Toasts on `surface.raised` with a `border.subtle` hairline and no shadow beyond the minimum. Severity is a glyph and a 2px left edge; there is no tinted background. |
| **Visual hierarchy** | Size, then weight, then space. Position is a weak signal because nothing is fenced into a position. |

### 2.6 Radius character

`radius.sm` 8px · `radius.med` 12px · `radius.lg` 16px · `radius.pill` full.
Quiet uses the larger end wherever it uses radius at all, which is rarely.
Soft, humane, slightly consumer.

### 2.7 Where Quiet is genuinely the best answer

- **The first sixty seconds.** No other direction gets a beginner from
  launch to "I could use this" faster.
- **Long reading sessions.** Journal › Review, tutorials, Pilot's longer
  answers and the review modal are all better here than in either
  alternative.
- **Ageing.** Borderless, weight-led interfaces have aged better than
  bordered ones over the last fifteen years, consistently.
- **Cost.** Cheapest of the three by a clear margin.
- **Mobile.** Translates almost unchanged; a phone is naturally a
  low-density, single-column, borderless environment.

### 2.8 How Quiet fails

Three ways, and the third is disqualifying.

1. **Scroll boundaries become invisible.** With no panel edges, a user
   cannot tell where a scrollable region ends and the next begins. On the
   Trade screen, the chain scrolls inside a region whose boundary is
   nothing but space — and a chain that has scrolled looks like a chain
   that ends.
2. **Framing carries no information**, because nothing is framed. There is
   no way to say "this thing is different in kind" — and the product has
   several such things: the status line, a critical banner, a guardrail
   message, the selected contract.
3. **It cannot serve Surface Level 4.** With 40px rows and 32px region
   gaps, the Trade workspace at Full shows about eight chain rows in the
   space where a professional expects twenty. The only fix is to compress —
   at which point Quiet has become Terminal, and the product has two visual
   identities depending on a setting. **The frozen product has an axis that
   requires two densities; a direction whose identity survives only one of
   them is incomplete by construction.**

### 2.9 Conformance ledger

| Frozen decision | Status |
| --- | --- |
| All colour values, contrast, evidence rules | Inherited unchanged |
| Row heights 40/28 | Inherited; Compact used only at Level 4 |
| Motion catalogue | Subset of 6; no additions |
| Component states | All defined; several rendered with less chrome |
| `border.control` on inputs | **Honoured, reluctantly.** This is the one place Quiet must draw a box, because the 3:1 requirement is not negotiable. |
| **Unfreezing required** | **None** |

---

## 3. Exploration B — **Terminal**

### 3.1 Overall visual identity

An instrument grid. Every region is a framed pane with a header bar, a
hairline border and a distinct interior surface. Rules divide everything.
Uppercase micro labels sit above every group. Numbers dominate, densely
packed, in tabular columns that run edge to edge.

The organising conviction: **a professional instrument shows its
structure.** A trader should be able to see, without reading a word, how
many things are on the screen and where each one's boundary is.

### 3.2 First impression

Serious. Capable. Immediately credible to anyone who has used a real
trading platform, and immediately alarming to anyone who has not.

### 3.3 Emotional feeling

Competence, and a slight pressure to keep up. Terminal makes a user feel
that the software knows more than they do — which is motivating for one
persona and repellent for another.

### 3.4 Personality

Precise, industrial, unsentimental. Terminal has no interest in making you
comfortable; it is interested in showing you everything at once and
trusting you to cope.

### 3.5 The eighteen attributes

| Attribute | Terminal |
| --- | --- |
| **Information density** | High. Compact (28px rows) is the default from Level 2 upward; Comfortable only at Level 1. Panel padding drops to `space.3`; gutters to `space.4`. |
| **Typography style** | Case-led. Uppercase `text.micro` on every pane header, every group label and every column header. `text.body` for values. `text.display` used nowhere — even the account value is `text.section`, because a 36px number would break the grid. Tight line heights. |
| **Spacing style** | Economical. `space.4` between panes, `space.3` inside them, `space.2` between rows. Space is a cost, not a device; structure is carried by rules. |
| **Panel treatment** | Framed pane: `surface.base` interior, `border.default` all round at `radius.sm`, and a header bar on `surface.sunken` carrying an uppercase micro title left and up to two actions right. |
| **Card treatment** | Metric tiles are not cards. They are cells in one framed strip, divided by `border.subtle` verticals — a single pane containing five compartments. |
| **Navigation appearance** | Icon + label, both at full strength. The rail carries its own `surface.base` and a `border.default` right edge, so it reads as a physically separate column. Active state: `action.primary.tint` background, 2px left edge, `neutral.050` ink. |
| **Chart presentation** | Full chrome. Horizontal and vertical gridlines. Framed plot area on `surface.sunken` with a hairline. Price axis with its own background band. Crosshair with labels on both axes. The volume pane divided from price by a full-width hairline rather than by space. |
| **Colour usage** | Restrained hues, but colour appears in more places: a `market.positive.tint` wash on in-the-money chain rows, tinted P&L cells in tables, prominent confidence bars. Still one accent per view. |
| **Iconography** | Heavy in toolbars, where labels drop entirely (permitted by §8.3). Icons also appear in pane headers as a type indicator. |
| **Animation style** | Five of seventeen: M-3, M-4, M-8, M-12, M-17. Destination changes are **instant** — no cross-fade. Rows appear and disappear instantly. Terminal's position is that an instrument does not animate, and it is internally consistent about it. |
| **Loading experience** | Barely visible. Dense skeleton rows at 28px inside an already-framed pane; the structure was never absent, so almost nothing appears to be missing. The best loading experience of the three by the narrowest definition — least perceived change. |
| **Empty states** | Terse. One line, one action, inside the pane, which keeps its header and frame. No air. |
| **Notifications** | Framed toasts with a header strip matching pane treatment, denser than the other directions, `border.default` all round. |
| **Visual hierarchy** | Position and rules. The eye is directed by the grid, not by size — which is why `text.display` is unused. |

### 3.6 Radius character

`radius.sm` 4px · `radius.med` 6px · `radius.lg` 6px · `radius.pill` full.
Terminal uses the smallest radii the system permits, and uses `md` and `lg`
identically because a large radius would soften a grid it wants hard.
Engineered, tight, slightly severe.

### 3.7 Where Terminal is genuinely the best answer

- **Information per screen.** At Surface Level 4 it shows meaningfully more
  than either alternative — roughly a third more chain rows in the same
  space.
- **Scroll boundaries and region identity** are unambiguous everywhere,
  which is the exact thing Quiet cannot do.
- **Credibility with Ted and Priya on first launch.** The personas who
  evaluate a trading product's seriousness in three seconds will rate this
  highest.
- **Multi-monitor.** Framed panes pop out into separate windows with no
  visual adjustment, because each already looks like a window.

### 3.8 How Terminal fails

1. **It is the screen Maya closes.** `UI_V2_DESIGN.md` §2.1 states the
   beginner's failure mode precisely — *"density reads as difficulty; a
   screen of unfamiliar columns causes withdrawal, not curiosity."*
   Terminal is that screen. Surface Level 1 mitigates it by removing
   columns, but it cannot remove the *framing*, and the framing is most of
   the intimidation.
2. **A grid of boxes is visually noisy even when every box is calm.** This
   is the direct conflict with the calm-instrument principle: Terminal's
   restfulness depends entirely on the content being restful, and it has no
   reserve when the content is not.
3. **Framing carries no information** — for the opposite reason to Quiet's.
   When everything is framed, a frame cannot mean anything. The status
   line, which `UI_V2_WIREFRAMES.md` §2.3 ranks as the single most
   important element on Home, is unframed prose in a world of frames and
   therefore reads as the *least* important thing on the screen. This is
   not a detail; it inverts the product's stated hierarchy.
4. **It wants a row height the system does not have.** Terminal's identity
   argues for a 24px row at Level 4. The frozen scale stops at 28px.

### 3.9 Conformance ledger

| Frozen decision | Status |
| --- | --- |
| All colour values, contrast, evidence rules | Inherited unchanged |
| Motion catalogue | Subset of 5; no additions |
| Component states | All defined |
| Row heights 40/28 | **Conflict.** Terminal's identity wants a third density step at 24px, which is a change to `DESIGN_SYSTEM_V2.md` §4.5 and a re-validation of every table, skeleton and virtualised list. |
| `text.display` | Defined but unused, which is permitted but wasteful |
| **Unfreezing required** | **One:** a third density tier. Estimated cost: re-specification of §4.5, re-measurement of tap targets and focus rings at 24px, and a re-run of the zoom and large-text checks, because a 24px row at 200% zoom is the case most likely to clip. |

---

## 4. Exploration C — **Flight Deck**

### 4.1 Overall visual identity

A fixed cockpit around a variable instrument field.

Two things are structurally distinct and always were:

- **The cockpit** — the frame, the navigation rail and the system strip.
  It sits on the darkest surface in the system, never moves, never scrolls,
  and is identical on every destination.
- **The instrument field** — everything between them. It is a canvas on
  which *instruments* sit: a chart, a chain, a ticket, a positions table, a
  score cluster. Each instrument is a region with its own surface, a quiet
  label, and a recessed interior where the data lives.

Instruments are separated by space, not by borders. Their surface step is
what gives them edges. Borders appear only where they do work: a splitter
the user can drag, a sticky header that must stay legible over scrolling
content, and the two rails of the cockpit.

The organising conviction, and the reason this direction exists: **framing
must be a signal.** In Quiet nothing is framed, so framing says nothing. In
Terminal everything is framed, so framing says nothing. In Flight Deck,
something that sits directly on the canvas with no instrument around it is
either the most important thing on the screen or explicitly not an
instrument — and the product has exactly such things. The status line. The
critical banner. A guardrail message. The commit control.

Flight Deck is the only one of the three in which the *absence* of a frame
is a design tool.

### 4.2 First impression

Composed. Deliberate. The screen resolves into a small number of large
objects, and the eye lands on one of them immediately rather than sweeping.
It reads as equipment rather than as a page, without reading as a cockpit —
there is no bezel, no gauge, no aviation styling anywhere. The inspiration
is felt in the *organisation*, exactly as `UI_V2_DESIGN.md` §1.2 requires.

### 4.3 Emotional feeling

Being in control of something well made. Not the relief of Quiet, and not
the pressure of Terminal — a steadiness. The interface looks like it is
holding a lot without effort, which is the emotional posture the product
philosophy asks for.

### 4.4 Personality

Assured and quiet. Flight Deck is confident enough to leave the canvas
empty around its instruments and confident enough not to decorate them. It
has the fewest visual flourishes of the three and the most visual
structure.

### 4.5 The eighteen attributes

| Attribute | Flight Deck |
| --- | --- |
| **Information density** | Both, by Surface Level, and the identity survives the switch. Comfortable (40px) at Levels 1–2, Compact (28px) at 3–4. The *zones* do not change; only the row height inside them does. This is the property neither alternative has. |
| **Typography style** | Value-led. The reading is the largest thing in each instrument; the instrument's own label is `text.micro`, uppercase, `neutral.400` — deliberately quieter than its content, on the principle that an instrument's housing does not compete with its needle. `text.display` is used exactly once per product, on the Home account value. |
| **Spacing style** | `space.5` (24px) between instruments, `space.4` inside them, `space.3` between groups within an instrument, `space.2` between rows. Space separates instruments; surface identifies them. Both do their own job and neither substitutes for the other. |
| **Panel treatment** | The signature. An instrument is `surface.base` at `radius.med`, no border, with the label top-left in `text.micro` and at most one action top-right. Where the data is tabular, scrolling, or a canvas, its interior is recessed to `surface.sunken` at `radius.sm` — so a chain, a chart plot and a ticket's field group all read as *inside* something. |
| **Card treatment** | The Home metric strip is **one instrument containing five compartments**, divided by `border.subtle` verticals. Not five cards; not five unbounded text groups. This matters: five cards would read as five objects competing for rank, whereas one cluster reads as a single instrument you scan across — which is what a metric strip is. |
| **Navigation appearance** | Icon + label, both legible, icons `neutral.400` at rest. The rail sits on `surface.page` with no surface of its own and no right border, so the cockpit reads as one continuous frame with the strip beneath it. Active: `action.primary.tint` background, 2px `action.primary.text` left edge, `neutral.050` ink — three signals, none of them colour alone. |
| **Chart presentation** | Asymmetric chrome, and this is the most distinctive visual decision in the direction. The chart is heavily specified where it is *read* and nearly absent where it is not. Read: the price axis, the last-price label, the position/stop/target lines with their labels, the crosshair readout. Not read: vertical gridlines (absent), horizontal gridlines (`border.subtle` at 50%), time labels (six positions, not twelve), the axis frame (none). The plot area is `surface.sunken`, so the chart is inside its instrument. |
| **Colour usage** | Neutral canvas. Market colour on values and marks only, never as a row wash. One accent per view. Status pills are the only tinted fills in the content area. The result: on a typical screen there are between eight and fourteen coloured pixels' worth of hue, and every one of them means something. |
| **Iconography** | Balanced. Nav (icon + label), row actions (icon-only, named), chart toolbar (icon-only, tooltipped), status pills (glyph + label). Nowhere else. No icons in headings, buttons with labels, or empty states. |
| **Animation style** | Thirteen of seventeen — the most of the three, and deliberately so: M-1 through M-4, M-6 through M-8, M-10 through M-15, M-17. It **omits M-5** (row enter fade — a new row simply appears; the two-step exit is where the information is), **M-9's slower timing is kept**, and it **omits M-16** (skeleton shimmer) because a shimmering instrument is a broken instrument. |
| **Loading experience** | The most distinctive of the three. Instrument shells render **instantly and completely** — surface, radius, label, recessed interior, everything except the data. Only the interiors skeletonise. The user reads the shape of the screen while the data arrives, and nothing moves when it lands. A loading Flight Deck screen looks like an instrument panel powering up, which is both accurate and, not incidentally, the metaphor. |
| **Empty states** | The instrument shell stays; the empty state lives in its recessed interior. **The screen never changes shape between empty, loading, populated and failed** — the strongest possible expression of `DESIGN_SYSTEM_V2.md` EM-3, and something neither alternative achieves (Quiet's regions collapse; Terminal's panes keep their frame but their headers imply content that is not there). |
| **Notifications** | Toasts are small instruments: `surface.raised`, `radius.med`, no border, a 2px severity edge on the left, glyph + title + body. The banner is the exception and spans the full frame width, sitting on the canvas rather than in an instrument — because a banner is not an instrument, and Flight Deck's grammar says so. |
| **Visual hierarchy** | Three levels, always, in this order: **zone → value → context.** The eye finds the instrument, then the number inside it, then the qualifier beneath. Every screen in §5 is built to make that sequence automatic. |

### 4.6 Radius character

`radius.sm` 6px · `radius.med` 10px · `radius.lg` 14px · `radius.pill` full.

The values are chosen so that an instrument (`md`, 10px) and its recessed
interior (`sm`, 6px) are visibly concentric with `space.3` between them —
the interior looks *set into* the instrument rather than stuck onto it.
This is the only place in the three directions where two radius values are
chosen in relation to each other rather than independently, and it is what
makes the two-surface instrument treatment read as one object.

### 4.7 The one thing to get right

Flight Deck lives or dies on the size of the step between
`surface.page`, `surface.base` and `surface.sunken`. Too small and it
collapses into Quiet with wasted effort; too large and it becomes Terminal
with rounded corners. The freeze (§8) pins the exact three steps and
forbids substitution.

### 4.8 Conformance ledger

| Frozen decision | Status |
| --- | --- |
| All colour values, contrast, evidence rules | Inherited unchanged |
| Row heights 40/28 | Inherited; both used, per Surface Level, as §12.4 recommends |
| Motion catalogue | Subset of 13; no additions; two deliberate omissions |
| Component states | All defined and all rendered |
| Surface strategy | Uses exactly three content surfaces plus `raised` for overlays — within the four the system defines |
| **Unfreezing required** | **None** |

---

## 5. Screen mockups

Layout is frozen and identical in all three directions; these describe
what the user *sees* and, more importantly, **what the eye does**.
Proportions are percentages of the content area at 1920×1080. The frame is
48px, the rail 200px, the strip 28px, in every direction.

### 5.1 Home

**Fixed:** status line, then a five-metric band, then positions and
what-to-do-next side by side (7/5), then equity and watchlist. Bands 1 and
2 fit above the fold.

---

**A — Quiet.** The content area is 88% air and 12% ink, and it looks it.
The status line sits at `space.5` below the frame in `text.body-lg`,
`neutral.050` — the second-largest text on the screen. Below it, `space.6`
of nothing, then five metric tiles: the only surfaced objects on Home,
`surface.base` at 16px radius, `space.5` padding, separated by `space.4`.
Inside each: an uppercase micro label, then the value at `text.title` (28px)
for Account and `text.section` (22px) for the rest, then the context line.
Below, `space.7` (48px), then the word POSITIONS in `text.heading` with two
rows beneath it, unframed, flush to the left margin. To the right at the
7/5 split, WHAT TO DO NEXT, also unframed. There is no line between them —
only 24px of gutter.

*Eye path.* Status line → account value → **drift**. The five tiles have
equal weight and equal spacing, so the eye crosses them without stopping;
Open Risk, which `UI_V2_WIREFRAMES.md` §2.3 ranks second in consequence,
gets no more attention than Buying Power. From the tiles the eye falls to
the first bold thing below, which is the POSITIONS heading, and then to the
position rows. Total time to "do I need to do anything": fast for the
sentence, slow for the numbers.

*Where it wins.* The status line is unmissable. Nothing else on the screen
competes with a full sentence in near-white on an empty field.

*Where it loses.* Band 2 has no visible structure. Positions and
what-to-do-next are two columns of text sharing a background, and at a
glance a user cannot tell whether they are one region or two.

---

**B — Terminal.** The content area is 68% ink. The status line sits at
`space.3` below the frame in `text.body`, `neutral.200` — unframed prose in
a screen where everything else has a border. The metric band is one framed
strip, 72px tall, five compartments divided by verticals, each with an
uppercase micro label and a `text.heading` value. Below at `space.4`, two
framed panes side by side, each with a `surface.sunken` header bar carrying
POSITIONS (2) and a `[Manage]` action, and WHAT TO DO NEXT. Beneath, a
third row of panes at `space.4`. Every boundary is explicit. Nine visible
rectangles on the screen.

*Eye path.* The metric strip → because it is the most structured object
and the highest-contrast edge in the upper third. Left to right across the
five compartments, stopping at each divider — which is genuinely better
than Quiet, because the dividers create five discrete fixations instead of
one sweep. Then down into the POSITIONS pane header, then its rows. **The
status line is read third or not at all.** In user testing this is the
prediction to check first, and it is the reason Terminal is not
recommended: the product's single most important sentence is styled as its
least important element.

*Where it wins.* Band 2's structure is unambiguous. Metrics get five
distinct fixations. Density means band 3 is fully above the fold at
1440×900, which neither alternative manages.

*Where it loses.* Nine rectangles is nine boundaries to parse before
reading anything. And the status line inversion is a hierarchy failure, not
a taste difference.

---

**C — Flight Deck.** The content area is 74% ink, 26% canvas. The status
line sits at `space.5` below the frame, in `text.body-lg`, `neutral.050`,
**directly on the canvas with no instrument around it** — the only element
on Home treated that way, which is why it reads first. `space.5` below it,
the metric cluster: one instrument, 96px tall, `surface.base` at 10px
radius, containing five compartments divided by `border.subtle` verticals
at 60% opacity. Each compartment: `text.micro` label in `neutral.400`, then
the value — Account at `text.display` (36px, the only display-sized number
in the product), the rest at `text.section` — then the context line in
`text.caption`. `space.5` below, two instruments side by side at the 7/5
split, each with its `text.micro` label top-left and its rows in a recessed
`surface.sunken` interior. `space.5` again, then equity and watchlist.

Five instruments on the screen. Not nine boxes; not zero.

*Eye path.* **Status line → account value → across the metric cluster →
down-left into POSITIONS → across to WHAT TO DO NEXT.** A clean Z into an
F. Each step is caused by a specific treatment: the status line by being
the only unframed element; the account value by being the only 36px number;
the cluster by being one object the eye scans rather than five it must
rank; POSITIONS by being the largest instrument and the top-left of band 2;
what-to-do-next by being the only other instrument at that altitude.

*Where it wins.* Every element of the frozen hierarchy in
`UI_V2_WIREFRAMES.md` §2.3 gets the attention its rank requires, and the
ordering is produced by treatment rather than hoped for.

*Where it loses.* Band 3 falls below the fold at 1440×900, where Terminal
keeps it. That is the price of `space.5` between instruments, and it is
acceptable because band 3 is explicitly the part permitted to scroll.

---

### 5.2 Trade

**Fixed:** chart top-left (~55% of the left column's height), chain beneath
it, ticket in a right column of 340px, this symbol's position beneath the
ticket, watchlist strip at the bottom-left.

---

**A — Quiet.** Chart and chain occupy one continuous field with a 32px gap
and a splitter that is invisible until hovered. The chart has no plot
surface — candles float on the page. The chain's column headers are the
only uppercase text. The ticket column is separated from the left by 24px
of nothing; its fields are the only bordered objects on the screen, because
`border.control` is mandatory.

*Eye path.* Chart → chain → and then the eye has to **search** for the
ticket, because a column of unframed form fields on the right of a large
chart does not announce itself. This is Quiet's worst screen.

*Assessment.* The chart looks superb — genuinely the best-looking chart of
the three, unencumbered and calm. Everything else on the screen is worse.
The ticket, which is the destination's whole purpose, is the least visually
present thing on it.

---

**B — Terminal.** Four framed panes: chart, chain, ticket, position. Each
with a header bar. The chart's plot area is framed and gridded on both
axes; the price axis has its own background band; the volume pane is
divided by a full-width hairline. The chain is 28px rows edge to edge, 20
rows visible. The ticket is a framed pane of labelled fields with a
`border.subtle` divider between every field group.

*Eye path.* Chart → **ticket** (its frame and its primary button make it the
second-strongest object) → chain → position. That is a *better* order than
Quiet's for an experienced user, who wants to know the ticket is there
before choosing a contract.

*Assessment.* Terminal's best screen. Twenty chain rows against Flight
Deck's sixteen and Quiet's eight. If Trade were the only screen in the
product, Terminal would win the recommendation.

---

**C — Flight Deck.** Three instruments in the left column-and-a-half —
chart, chain, watchlist strip — and two in the right: ticket and position.
The chart instrument's interior is `surface.sunken`, so the candles sit in
a recessed well with the price axis on its right edge and no frame around
it. Horizontal gridlines only, at half strength. The chain instrument's
interior is also recessed, with a sticky header on `surface.base` and a
`border.subtle` underline — the one border on the screen that is not a
splitter, and it earns its place because the header must stay legible over
scrolling rows. Sixteen chain rows visible at Compact.

The ticket is an instrument whose interior is a single recessed field
group, so the fields read as being set into it rather than floating in a
column. Its `[ Review order ]` button is the only accent-filled object on
the screen.

*Eye path.* **Chart → the ticket's accent button → chain → back to the
ticket.** The single accent pulls the eye to the ticket early — which is
correct, because knowing the order is possible should precede choosing what
to order — and the chain then reads as the thing that feeds it. The loop
closes on the ticket, which is where the action is.

*Assessment.* Four fewer chain rows than Terminal, and a clearer causal
story about what the screen is for. The recessed-well chart is the second
most distinctive image in the direction after the metric cluster.

---

### 5.3 Portfolio

**Fixed:** summary line, filter segments, positions table, working orders,
closed today, exposure bars, and a 320px detail rail on the right.

**A — Quiet.** The table has no frame and no zebra; rows are separated by
40px of height and nothing else. The detail rail is a column of text at the
right margin with no boundary — which means when nothing is selected, the
right third of the screen is simply empty, and the user cannot tell whether
that is a state or a bug.

**B — Terminal.** Three framed panes stacked on the left (positions,
working, closed) and a framed rail on the right. Explicit, scannable,
slightly relentless: seven headers in a column. Empty rail keeps its frame
and header, which is better than Quiet.

**C — Flight Deck.** Three instruments stacked left, one instrument right.
The detail rail is an instrument like any other, so an empty rail is a
labelled instrument with an empty interior reading *"Select a position to
manage it"* — visually identical to a populated one except for its
contents. The exposure bars sit in their own short instrument at the
bottom, which prevents them being read as part of the closed-today list.

*Eye path, all three.* Summary → positions table → the selected row → the
rail. Flight Deck's advantage is at the last step: the rail is a
destination the eye can find because it is an object, not a margin.

---

### 5.4 Research

**Fixed:** a 168px section rail (Explore / Backtest / Watchlist / Engine)
and a content pane.

**A — Quiet.** Section rail is a list of words at the left of the content
area with a 2px active edge and no background. The chart in Explore is
unframed and beautiful. The AI verdict below it is a heading and three
lines of text. The whole screen reads as an article.

**B — Terminal.** Section rail has its own surface and border, making three
vertical columns on screen (nav, sections, content) with two visible
dividers. The verdict is a framed pane with a header. Backtest results are
four framed metric cells and a framed chart. Dense and businesslike; the
three-column division at the left edge is Terminal's least attractive
moment, because two adjacent bordered rails read as a mistake.

**C — Flight Deck.** The section rail sits on the canvas with no surface —
same treatment as the nav rail, so the two rails read as one continuous
cockpit edge rather than as two competing columns. This is a small decision
with a large effect: it is the difference between "a nav rail and a section
rail" and "a cockpit that got one step deeper." The content is two
instruments — the chart and the verdict — with the verdict's passed/failed
lists in a recessed interior.

*Eye path.* Chart → verdict → the failed line. Flight Deck emphasises the
failed evidence by placing it last in a recessed field, which is correct:
the reason a setup did *not* clear is the more informative half.

---

### 5.5 Journal

**Fixed:** section rail (Trades / Review / Progress), a trade list, and a
detail rail carrying three stacked blocks — engine reasoning, coach review,
pattern.

**A — Quiet.** The best Journal of the three for reading. The three detail
blocks are headings and prose with 32px between them; Pilot's language has
room to breathe. The weakness is that the three blocks read as one
continuous essay, and they are three different *kinds* of statement — a
machine's reasoning, an observation, a statistical finding.

**B — Terminal.** Three framed sub-panes in the rail, each with a header.
The distinction between the three kinds is unmistakable. But 12px of
padding around prose makes the coach's language feel cramped, and the
Progress screen — four score cards, a findings list, goals, achievements
and a timeline — becomes fourteen rectangles.

**C — Flight Deck.** One instrument in the rail containing three groups
separated by `space.4` and a `border.subtle` divider, with each group's
label in `text.micro`. Three kinds, one object — which is accurate, because
they are three views of one trade.

Progress is where Flight Deck's treatment matters most. The four score
cards are one instrument with four compartments, exactly like Home's metric
cluster. **The TIMING card, which reads "not enough evidence," is therefore
visually identical in weight to the three that carry grades** — it is a
compartment of the same cluster, not a greyed-out card that reads as
broken. That is the visual expression of `DESIGN_SYSTEM_V2.md` §1.4, and it
is the single strongest argument in this document that the direction and
the principles are the same thing.

*Eye path.* Coverage statement → score cluster (left to right, all four
read, including the unassessable one) → findings → goals.

---

### 5.6 Settings

**Fixed:** five groups in a section rail, a content pane, and the provider
list with its health states.

**A — Quiet.** A long, calm column of labelled controls with 32px between
groups. Genuinely pleasant. The provider list, which is the most
information-dense thing in Settings, loses structure: four providers each
with a status, latency, quota and two actions, with nothing separating one
from the next but space.

**B — Terminal.** A framed table of providers with columns and rules.
Precise and immediately scannable — Terminal's second-best screen. The
Finnhub `(!)` explanation, which runs to three lines of prose, sits
awkwardly inside a dense table row.

**C — Flight Deck.** Each provider is its own small instrument, stacked
with `space.3` between them: label, status pill, latency and key on the
first line, quota on the second, and — where there is something to explain
— a recessed interior holding the explanation and its `[ Fix > ]` action.
So a healthy provider is a two-line instrument and a broken one grows a
recessed well containing its reason.

*This is the treatment that best serves the product's most-repeated lesson.*
The Finnhub case exists because 401 and 403 were conflated and users
regenerated a working key repeatedly. In Flight Deck the explanation has a
place that is visually *part of* the provider and visually distinct from
its status line, at whatever length it needs. In Terminal it is a table row
that has grown too tall; in Quiet it is prose adrift.

*Eye path.* Group rail → provider list → the one provider with a caution
pill → its recessed explanation → `[ Fix > ]`.

---

## 6. Comparison

### 6.1 Advantages and disadvantages

| | **A — Quiet** | **B — Terminal** | **C — Flight Deck** |
| --- | --- | --- | --- |
| **Advantages** | Lowest intimidation. Best first sixty seconds. Best for reading — Journal, Pilot, review copy, tutorials. Ages best. Cheapest. Translates to mobile almost unchanged. Most beautiful chart. | Highest information density — a third more chain rows at Level 4. Unambiguous region boundaries and scroll edges. Most credible to Ted and Priya on first launch. Pops out to multi-monitor with no adjustment. Best Trade screen. | Framing is a signal, so hierarchy is produced rather than hoped for. The only direction whose identity survives both densities. Screen shape never changes across loading/empty/populated/failed. Renders unassessable evidence as a peer rather than as damage. Requires no unfreezing. |
| **Disadvantages** | Scroll boundaries invisible. Framing carries no information. Cannot serve Level 4 without becoming Terminal. Worst Trade screen — the ticket is the least present object on its own destination. Empty regions are indistinguishable from faults. | Intimidating exactly where the product must not be. A grid of boxes is noisy even when calm. Framing carries no information, and the status line is styled as the least important element on Home. Two adjacent bordered rails on Research read as a mistake. Needs a token extension. | Four fewer chain rows than Terminal at Level 4. Band 3 of Home falls below the fold at 1440×900. Depends critically on three surface steps being exactly right — too subtle and it is Quiet with wasted effort, too strong and it is Terminal with round corners. |

### 6.2 Complexity and cost

Cost is expressed against `UI_V2_DESIGN.md` §16's phases, as relative
effort over the baseline the frozen documents already require.

| | **A** | **B** | **C** |
| --- | --- | --- | --- |
| Token work (Phase 0) | Baseline | Baseline + a third density tier + re-validation | Baseline |
| Component work (Phase 1) | **−15%** — fewer surfaces, fewer borders, six animations | **+10%** — pane headers, frame treatments, denser variants of every table | **+8%** — the two-surface instrument treatment and its concentric radii |
| Chart chrome (Phase 4) | **−20%** — the least chrome | **+15%** — the most | Baseline — less chrome overall than Terminal but asymmetric, so more specification |
| Loading states | Baseline | **−5%** — dense skeletons in frames that already exist | **+5%** — shells render fully, interiors skeletonise separately |
| Accessibility verification | Baseline | **+20%** — 24px rows must be re-verified at 200% zoom and large text, which is the case most likely to clip | Baseline |
| Mobile translation | **−10%** | **+25%** — a dense framed grid does not become a phone screen without redrawing | Baseline — instruments become cards one-to-one |
| **Overall** | **Lowest** | **Highest** | **Middle, and closest to the baseline the frozen docs assume** |

### 6.3 Persona and context support

Scored 1–5 against the four questions the brief asks, with the reasoning
rather than the number carrying the argument.

| | **A — Quiet** | **B — Terminal** | **C — Flight Deck** |
| --- | --- | --- | --- |
| **Beginners** | **5** — nothing on screen implies expertise is required | **2** — this is the screen `UI_V2_DESIGN.md` §2.1 describes as causing withdrawal | **4** — five large objects, plain labels, and a sentence at the top; structured without being dense |
| **Experienced traders** | **2** — insufficient density and a ticket that does not announce itself | **5** — maximum information, unambiguous structure, the fastest Trade screen | **4** — one third fewer rows than Terminal, offset by a clearer causal path from chart to commit |
| **Long sessions** | **3** — restful but low-yield; more scrolling is more fatigue | **3** — high yield but a grid of boxes accumulates visual noise over hours | **5** — the least hue on screen, the fewest edges to parse, and a fixed cockpit that never moves so the eye always knows where it is |
| **Future mobile** | **5** — a phone is naturally borderless and low-density | **2** — a framed dense grid must be redrawn, not adapted | **5** — an instrument becomes a card one-to-one, and the recessed interior becomes the card body |
| **Total** | 15 | 12 | **18** |

The score is a summary, not the argument. The argument is §7.

### 6.4 Which direction each frozen principle prefers

The most objective comparison available: score each direction against the
seven principles it must serve, using only what the frozen documents say.

| Principle | Prefers | Why |
| --- | --- | --- |
| Calm instrument | **C**, then A | Fewest coloured pixels and fewest edges *that carry no meaning*. Terminal's edges are noise by this definition. |
| Confidence through clarity | **C** | One accent per view is most legible when the rest of the screen has no competing fills. |
| Progressive disclosure | **C** | The only direction whose visual identity is unchanged between Surface Level 1 and 4. |
| Evidence before confidence | **C** | An unassessable score as a compartment of a cluster reads as a peer; as a card it reads as broken; as unframed text it reads as absent. |
| One workspace | **C** | The fixed cockpit is identical on all six destinations, and Research's section rail continues it rather than competing with it. |
| Friction proportional to consequence | **C**, then B | A single accent-filled commit control on an otherwise neutral screen is the strongest available signal. Terminal's washes and tints spend some of that. |
| Motion with purpose | **A**, then C | Quiet moves least. But Quiet also omits M-6, the two-step row exit, which is the one animation that answers a question a trader actually asks — *which position just closed?* |

Six of seven prefer C. The seventh prefers A on a technicality that costs
information.

---

## 7. Recommendation

### 7.1 The recommendation

**Exploration C — Flight Deck.**

### 7.2 The argument that decides it

Two of the three directions are eliminated by the frozen specification
rather than by taste, and it is worth stating that plainly because it makes
the decision durable.

**Surface Level is a product axis, not a preference.** `UI_V2_DESIGN.md` §8
requires the same product to serve a first-time user at Level 1 and a
professional at Level 4, with the invariant that **complexity is
progressively revealed, never separately implemented** — a beginner and a
professional must be looking at the same screen with different amounts of
it revealed, never at different screens.

- **Quiet cannot render Level 4.** Its identity is space, and Level 4 needs
  the space back. Compress it and it is Terminal.
- **Terminal cannot render Level 1.** Its identity is framing, and framing
  is most of what intimidates a beginner. Remove the frames and it is
  Quiet.
- **Flight Deck's identity is neither space nor framing — it is
  zoning.** The zones do not change between Levels 1 and 4; only the row
  height inside them does. A Level 1 user and a Level 4 user see the same
  five instruments on Home, arranged identically, containing different
  amounts of detail.

That is precisely the invariant §1.4 of the vision document demands, and
Flight Deck is the only one of the three that satisfies it structurally
rather than by compromise.

### 7.3 The second argument, which is the aesthetic one

**Framing must be a signal.** In Quiet nothing is framed; in Terminal
everything is. In both cases a frame communicates nothing, because it is
constant. Flight Deck reserves the canvas — an element with no instrument
around it is either the most important thing on the screen or explicitly
not an instrument.

The product has exactly four such elements, and each is one the frozen
documents rank as critical:

1. **The status line** — the one sentence `UI_V2_WIREFRAMES.md` §2.3 calls
   the most important element on Home, and which Terminal styles as the
   least important.
2. **The critical banner** — a condition that changes what the user can do.
3. **A guardrail message** — the voice that says what changed and why.
4. **The commit control** — the only place capital moves.

A direction in which those four are visually distinguishable *by
construction* is worth more than a third more chain rows.

### 7.4 What Flight Deck gives up, stated honestly

- **Four chain rows at Level 4**, against Terminal. A real cost to Priya
  and Ted, mitigated by the fact that the chain is spot-anchored on load
  (`UI_V2_WIREFRAMES.md` §3.6), so the sixteen visible rows are the
  sixteen that matter.
- **Band 3 of Home below the fold at 1440×900.** Acceptable: the frozen
  no-scroll commitment covers bands 1 and 2 only, precisely because band 3
  is context.
- **A harder specification.** Three surface steps, two concentric radii and
  an asymmetric chart. Terminal is easier to get consistently right because
  a border is a border; Flight Deck's instrument treatment has to be
  correct everywhere or it reads as sloppy. §8 exists to remove that risk.

### 7.5 What Flight Deck takes from the other two

This is a synthesis and says so:

- **From Quiet:** space as the separator between instruments, the
  restraint on colour, and the near-absence of decorative borders.
- **From Terminal:** the recessed interior, which is what gives a scrolling
  region a legible boundary; and the metric strip as one divided object
  rather than five loose ones.
- **Its own:** the reserved canvas, the asymmetric chart, the shell-renders-
  first loading model, and the guarantee that a screen's shape never
  changes across its four states.

### 7.6 If the recommendation is rejected

Should the decision go the other way, the fallbacks in order:

1. **Terminal, with Level 1 restyled toward Quiet.** Accepts two visual
   identities, and accepts the 24px unfreeze. Highest cost, highest density.
2. **Quiet, with Level 4 accepting Terminal's density.** Same compromise
   from the other end, lower cost, and it leaves the Trade screen — the
   product's most important workflow — as the weakest screen in the
   product. This is the worse of the two fallbacks.

---

## 8. Design freeze

Everything below is settled. Implementation begins from here and invents
nothing. Anything not listed is either already frozen in the three parent
documents or is listed as open in §9.

### 8.1 Direction

**F-1.** The visual direction is **Flight Deck**. The cockpit (frame, nav
rail, system strip) is fixed and identical on every destination; the
content area is a canvas carrying instruments.

### 8.2 Surfaces

**F-2.** Exactly four surfaces are used, and no others:

| Role | Token | Where |
| --- | --- | --- |
| Cockpit | `neutral.950` | Frame, nav rail, system strip |
| Canvas | `neutral.900` | The content area background |
| Instrument | `neutral.850` | Every panel, card, toast and rail |
| Interior | `neutral.800` | The recessed area inside an instrument: chart plot, table body, ticket field group, input fields, code |
| Overlay | `neutral.750` | Popovers, dropdowns, tooltips, context menus, modals, the palette |

**F-3.** The step from canvas to instrument is what gives an instrument its
edge. **An instrument never has a border.** Substituting a border for the
surface step, anywhere, is a conformance failure.

**F-4.** An instrument's interior is recessed **only** when its content
scrolls, is tabular, is a canvas, or is a group of input fields. A short
static instrument (a status summary, an exposure list) has no recessed
interior.

### 8.3 Borders

**F-5.** Borders appear in exactly five places and nowhere else:

1. `border.control` on inputs, unfilled buttons and checkboxes — mandatory
   for contrast.
2. `border.subtle` under a sticky table header.
3. `border.subtle` as the vertical divider between compartments of a
   cluster instrument, at 60% opacity.
4. The splitter line, per `DESIGN_SYSTEM_V2.md` §5.4.
5. The cockpit's two edges: below the frame, above the strip.

### 8.4 Radius

**F-6.** `radius.sm` = 6px · `radius.med` = 10px · `radius.lg` = 14px ·
`radius.pill` = full.

**F-7.** Instruments take `radius.med`; recessed interiors take `radius.sm`
with `space.3` of instrument padding between them, so the two are visibly
concentric. Modals and the palette take `radius.lg`. Chips, status pills
and toggles take `radius.pill`. **No other radius values exist.**

### 8.5 Density

**F-8.** Comfortable (40px rows, `space.5` instrument padding) at Surface
Levels 1–2. Compact (28px rows, `space.4` instrument padding) at Levels
3–4. **No third density tier.** Instrument zoning, spacing between
instruments, and every radius are identical in both.

### 8.6 Spacing

**F-9.** Between instruments `space.5`. Instrument padding `space.5`
(Comfortable) or `space.4` (Compact). Between groups inside an instrument
`space.3`. Between rows `space.2`. Canvas margin `space.5`. Status line to
the frame `space.5`; status line to the first instrument `space.5`.

### 8.7 Typography assignment

**F-10.** Value-led. Fixed assignments:

| Element | Role |
| --- | --- |
| Home account value | `text.display` — **the only display-sized number in the product** |
| Destination title in the frame | `text.body`, weight 600 |
| Status line | `text.body-lg`, `neutral.050` |
| Instrument label | `text.micro`, uppercase, `neutral.400` |
| Metric value | `text.section` |
| Metric context line | `text.caption`, `neutral.400` |
| Table column header | `text.micro`, uppercase, `neutral.400` |
| Table value | `text.body`, tabular |
| Table primary identifier | `text.body-strong` |
| Prose (Pilot, review, empty states, errors) | `text.body-lg`, capped at 72ch |
| Help and metadata | `text.caption` |

**F-11.** Uppercase appears **only** on instrument labels and table column
headers. Nowhere else, ever.

### 8.8 Navigation

**F-12.** Icon + label. The rail sits on the cockpit surface with no
border and no surface of its own. Active state is three simultaneous
signals: `action.primary.tint` background, 2px `action.primary.text` left
edge, `neutral.050` ink. The section rails in Research, Journal and
Settings take the identical treatment, so the two rails read as one
continuous cockpit edge.

### 8.9 Chart chrome

**F-13.** Asymmetric, per this table. It is complete; nothing else is
drawn.

| Element | Treatment |
| --- | --- |
| Plot area | `surface.sunken`, `radius.sm`, no border |
| Horizontal gridlines | `border.subtle` at 50% opacity |
| Vertical gridlines | **None** |
| Axis frame | **None** |
| Time labels | Six positions maximum |
| Price axis | No background band; labels in `text.caption`, tabular, right-aligned |
| Last-price label | Filled with the direction colour, full weight |
| Position / stop / target lines | Full weight, always labelled |
| Crosshair | `neutral.400` dashed, with a readout on both axes |
| Volume pane | Separated from price by `space.2` and no divider |

### 8.10 Colour budget

**F-14.** Per screen: one accent-filled element maximum; market colour on
values and marks only; **no row washes or tinted table cells anywhere**;
status pills are the only tinted fills in the content area. The accent
never appears inside a plot area.

### 8.11 Motion

**F-15.** Thirteen of the seventeen catalogued animations are used: M-1,
M-2, M-3, M-4, M-6, M-7, M-8, M-10, M-11, M-12, M-13, M-14, M-15,
M-17. **M-5** (row enter fade) and **M-16** (skeleton shimmer) are
deliberately omitted — a new row appears without ceremony, and a shimmering
instrument reads as a malfunctioning one. **M-9** keeps its deliberately
slower `medium` timing. No animation outside the catalogue exists.

### 8.12 Loading

**F-16.** Instrument shells render instantly and completely — surface,
radius, label, recessed interior, actions. Only interior *values*
skeletonise, after 200ms, with no shimmer, at the exact height of the
content they replace.

### 8.13 The shape invariant

**F-17.** A screen's shape does not change between its four states.
Loading, empty, populated and failed all render the same instruments in the
same positions at the same sizes. Only interiors differ. **A region never
disappears because it has nothing in it.**

### 8.14 The canvas reservation

**F-18.** Exactly four elements sit directly on the canvas with no
instrument around them, and no fifth may be added without amending this
document: the **status line**, the **critical banner**, an **inline
guardrail message**, and the **commit control** while a review modal is
open.

### 8.15 Iconography

**F-19.** Icons appear in the nav rail (with labels), in table row actions
(icon-only, accessibly named), in the chart toolbar (icon-only,
tooltipped), and in status pills (glyph with label). **Nowhere else** — no
icons in headings, in buttons that already have labels, in empty states, or
in metric compartments. No emoji anywhere in the product.

### 8.16 Per-screen eye-path targets

**F-20.** These are acceptance criteria, verified by review at each
phase's exit, not aspirations.

| Screen | First fixation | Second | Third |
| --- | --- | --- | --- |
| Home | Status line | Account value | Positions instrument |
| Trade | Chart | The ticket's accent button | Chain |
| Portfolio | Summary line | Positions table | Detail rail |
| Research › Explore | Chart | AI verdict | The failed-evidence line |
| Journal › Trades | Trade list | Selected row | Detail instrument |
| Journal › Progress | Coverage statement | Score cluster, **all four compartments** | Findings |
| Settings › Data | Section rail | The provider with a caution pill | Its recessed explanation |

### 8.17 Conformance

**F-21.** These design-system health checks
(`DESIGN_SYSTEM_V2.md` §12.6) gain three direction-specific assertions:

| Check | Assertion |
| --- | --- |
| Instrument borders | Zero borders on any element with an instrument surface |
| Canvas reservation | Exactly four element types render directly on the canvas |
| Shape invariant | Every destination's four states produce identical instrument geometry |

---

## 9. What remains open

Not settled by this document, and each needs a decision before the phase
that depends on it.

| # | Question | Blocks | Recommendation |
| --- | --- | --- | --- |
| 1 | **Vendored variable typeface or platform faces?** (`DESIGN_SYSTEM_V2.md` §12.4-1) | How identical desktop and mobile can look | Ship on platform faces. Flight Deck depends on structure rather than on a typeface, so it survives either answer. |
| 2 | **Does the light theme ship with V2 or after?** | Phase 1 scope | After. Flight Deck's translation is the harder of the three — in light, the canvas→instrument step must be carried by shadow and border rather than by lightness, which is a genuine re-derivation. |
| 3 | **Exact opacity of the compartment divider** (F-5 item 3 says 60%) | Nothing; it is a single value | Verify on hardware during Phase 1. It is the one value in this freeze chosen by judgement rather than measurement, and it is the difference between a cluster reading as one object and as five. |
| 4 | **Does the popped-out chart keep its instrument shell?** | Phase 10 | It does not — a pop-out window *is* the instrument, so it renders its interior edge to edge. Confirm when pop-outs are built. |
| 5 | **Is `radius.lg` used anywhere besides modals and the palette?** | Nothing today | Leave it defined and nearly unused. A radius value with one caller is cheaper to keep than to re-derive. |

---

## 10. Related documents

| Document | Relationship |
| --- | --- |
| `UI_V2_DESIGN.md` | Frozen. The philosophy this direction expresses. §7.2's argument is drawn entirely from its §8. |
| `UI_V2_WIREFRAMES.md` | Frozen. The layouts this direction renders. The eye-path targets in F-20 are its hierarchies, achieved by treatment. |
| `DESIGN_SYSTEM_V2.md` | Frozen. The tokens and components this direction configures. §0.2 lists exactly which of its variables were open. |
| `CLAUDE.md` | Binding constraints, including no build step and no CDN — which is why §9-1's typeface question is a real decision rather than a formality. |
| `ONBOARDING.md` | The contextual-help layer whose tooltips and guardrail messages inherit F-18's canvas reservation. |
| `TRADING_INTELLIGENCE.md` | The evidence rules whose visual expression is §5.5's score cluster. |
