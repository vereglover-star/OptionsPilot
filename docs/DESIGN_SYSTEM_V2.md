# DESIGN_SYSTEM_V2.md — the OptionsPilot visual language

**Status:** proposed, not implemented. **Version:** 1.0.
**Parents:** `UI_V2_DESIGN.md` (philosophy) and `UI_V2_WIREFRAMES.md`
(layout). Neither is modified by this document.

This is the third and last document before implementation. The first says
what the product should feel like, the second says where things go, and
this says what they look like and how they behave as *components*. It is
platform-neutral by construction: every value is expressed as a token or a
rule, never as a stylesheet, because the same system has to survive a
desktop WebView today and a native mobile client later.

It contains no CSS, HTML, JavaScript, React or implementation code.

**What this document supplies that its parents deferred.**
`UI_V2_DESIGN.md` §11 deliberately defined colour *philosophy* and left the
values to "Phase 1 implementation, recorded as an appendix at that point."
This document is that output. Every colour here has been **computed and
verified**, not chosen by eye: WCAG contrast ratios are measured, and the
categorical palette is validated for colour-vision separation with a
simulation model rather than asserted to be safe. Where a value is
marginal, this document says so and states what compensates for it.

---

## 1. Design principles, expressed visually

The seven principles are the parents'. This section states what each one
*forbids and requires of a pixel*, because a principle that cannot reject a
design is decoration.

### 1.1 Calm instrument

**Requires.** A single visual temperature that does not change with
performance. Losses render with the same typographic weight, spacing,
elevation and motion profile as gains. Urgency is expressed by **position
in the hierarchy**, never by heat, size or movement.

**Forbids.** Red page washes and green glows. Pulsing, flashing, shaking.
Any element that grows, brightens or animates because a number moved. Sound
of any kind.

**The convention this rejects.** Nearly every professional trading platform
flashes a price cell green or red on each tick. We do not, and the reason
is not squeamishness: at the product's ~1s push cadence a flashing cell is
in its transition state a meaningful fraction of the time, which makes it
*less* readable exactly when it is changing fastest. The information a
flash carries — direction of the last change — is carried instead by a
persistent direction glyph and the day-change colour, both of which are
readable at any instant including during a change. See §3.7.

### 1.2 Confidence through clarity

**Requires.** One primary action per view, unmistakably the primary.
Alignment that lets a column of numbers be scanned without reading them.
Labels above fields, never inside them. Every disabled control able to say
why.

**Forbids.** Two accented controls in one view. Placeholder-as-label.
Ambiguous iconography. A control whose affordance depends on hover to be
discovered.

### 1.3 Progressive disclosure

**Requires.** Depth reached by revealing in place. A component's advanced
form occupies the same location as its simple form. Disclosure controls
that state what they will reveal.

**Forbids.** Any component that exists at one Surface Level and not
another. Surface Level changes *columns, annotations and defaults* — never
the component inventory. A "beginner button" and an "expert button" are two
components where there should be one with two configurations.

### 1.4 Evidence before confidence

**Requires.** Every statistic renders its sample size in the same visual
unit as the statistic — not in a tooltip. An unmeasurable value renders as
a stated reason occupying the space the number would have taken. A
component that displays a number must define its *insufficient-evidence*
state before it ships.

**Forbids.** A blank cell standing for zero. A grade over partial coverage.
A progress bar without its denominator. A sparkline with no scale where the
scale changes the reading.

### 1.5 One workspace

**Requires.** A component looks and behaves identically on every
destination. A table row on Portfolio and a table row on Journal are the
same component with different columns. One focus treatment, one selection
treatment, one disabled treatment, product-wide.

**Forbids.** Per-screen variants. A "Journal table" and a "Portfolio table"
as separate components is the beginning of the drift this system exists to
prevent.

### 1.6 Friction proportional to consequence

**Requires.** Visual weight and interaction cost that scale with what an
action can do. Reading is free. Configuring is one click. Committing
capital is a sustained gesture with a legible progress affordance.

**Forbids.** A destructive action styled identically to a benign one. A
commit reachable by a single click or a stray `Enter`. Confirmation
ceremony on actions that risk nothing.

### 1.7 Motion with purpose

**Requires.** Every animation answers one of three questions: where did
this come from, where did it go, did that register. §7's list is closed.

**Forbids.** Everything not on that list.

---

## 2. Colour system

### 2.1 How this palette is organised

Three layers, as `UI_V2_DESIGN.md` §11.1 requires. **Components reference
semantic tokens only and never a primitive.** A token path in this document
is written `color.surface.base`; it is a name, not a syntax.

```
  PRIMITIVE            SEMANTIC                COMPONENT
  raw ramp step        the job it does         where it is used
  ---------------------------------------------------------------
  neutral.850    ->    surface.base       ->   panel background
  blue.500       ->    action.primary     ->   commit fill
  green.500      ->    market.positive    ->   gain value, up candle
```

**Dark is the reference theme.** Light is specified structurally in §2.9
and is a later phase; it is not an inversion and its values are re-chosen,
not derived.

### 2.2 Neutral ramp (dark)

The single most important decision in a dark interface is the neutral
ramp, because ninety percent of the product is made of it. Nine steps, a
slight cool cast, spaced so adjacent surfaces are distinguishable without
borders.

| Token | Value | Purpose |
| --- | --- | --- |
| `neutral.950` | `#0B0D10` | The void behind everything; window background; modal scrim base |
| `neutral.900` | `#0F1115` | Page |
| `neutral.850` | `#14161A` | Surface 1 — panels, the default content surface |
| `neutral.800` | `#1B1E24` | Surface 2 — inset regions, table headers, code |
| `neutral.750` | `#23272E` | Surface 3 — raised: popovers, dropdowns, tooltips |
| `neutral.700` | `#2B2F37` | Hairlines and dividers on surface 1 |
| `neutral.650` | `#333945` | Default borders on interactive elements |
| `neutral.500` | `#626A78` | Control borders where the border is the only boundary |
| `neutral.450` | `#5D646F` | Disabled ink |
| `neutral.400` | `#8A909B` | Muted ink — labels, units, metadata |
| `neutral.200` | `#C3C8D1` | Secondary ink |
| `neutral.050` | `#F2F4F7` | Primary ink |

**Dark is not black.** `neutral.950` is the darkest value in the system and
primary ink is not pure white. Pure black with pure white text vibrates at
the edges and fatigues over a session; the existing product already gets
this right and the ratios are preserved.

### 2.3 Surfaces, elevation and borders

**Elevation is a surface step, not a shadow.** In a dark interface, raising
an element means making it *lighter*, and shadow is nearly invisible on
near-black. Shadow is used only where an element floats free of the layout
— overlays — and then it is one soft, low-opacity layer, never stacked.

| Level | Surface token | Shadow | Used by |
| --- | --- | --- | --- |
| 0 — page | `surface.page` (`neutral.900`) | none | The content area background |
| 1 — panel | `surface.base` (`neutral.850`) | none | Panels, cards, tables |
| 2 — inset | `surface.sunken` (`neutral.800`) | none | Table headers, input fields, code blocks, chart plot area |
| 3 — raised | `surface.raised` (`neutral.750`) | soft, small | Popovers, dropdowns, tooltips, context menus |
| 4 — overlay | `surface.raised` + scrim | soft, large | Modals, the command palette |

The scrim behind an overlay is `neutral.950` at 60% opacity. It **dims and
never blurs**: blur destroys the legibility of the values behind a modal,
and a user reviewing an order may legitimately want to read the chain
underneath it.

**Border tokens are three, and the third exists for a reason:**

| Token | Value | Contrast on `surface.base` | Use |
| --- | --- | --- | --- |
| `border.subtle` | `neutral.700` | 1.26:1 | Dividers between rows and sections. Decorative; contrast is not required. |
| `border.default` | `neutral.650` | 1.56:1 | The edge of a card or panel where a surface step is not enough. Decorative. |
| `border.control` | `neutral.500` | **3.32:1** | Any border that is the *only* thing indicating a control's boundary — an input, an unfilled button, a checkbox. |

`border.control` is not an aesthetic choice. WCAG requires 3:1 for a
non-text element that conveys information, and an input field whose only
boundary is a 1.3:1 hairline is a control a low-vision user cannot find.
The three tokens exist so a designer must decide which case they are in.

### 2.4 Action colour

One accent. It means **"the primary action here"** and appears at most once
per view.

| Token | Value | Contrast | Use |
| --- | --- | --- | --- |
| `action.primary.fill` | `#2F6FE0` | 3.86:1 on `surface.base` | Primary button fill, commit fill |
| `action.primary.ink` | `#FFFFFF` | **4.70:1 on the fill** | Text on the primary fill |
| `action.primary.text` | `#6FA8FF` | 7.52:1 on `surface.base` | Links, text buttons, the active nav indicator |
| `action.primary.tint` | `#111C2E` | — | The wash behind a selected row or an active segment |

**Where the accent may not appear:** inside a chart plot area (§9.1); on
more than one control in a view; as a decorative highlight; as the colour
of a success state. That last one matters — a confirm button is the
**accent**, not green, so that green keeps meaning *gain* everywhere and
never has to be disambiguated by context.

### 2.5 Market colour — and the measurement that constrains it

Two tiers, because text and marks have different requirements. Text needs
4.5:1 against the surface; chart marks need to sit inside the perceptual
lightness band where colour identity survives colour-vision deficiency.

| Token | Value | Contrast on `surface.base` | Use |
| --- | --- | --- | --- |
| `market.positive.text` | `#3DDC97` | 10.25:1 | Gains in text: P&L, percentages, deltas |
| `market.positive.mark` | `#16A97A` | 6.03:1 | Up candles, positive bars, positive line segments |
| `market.positive.tint` | `#0C2318` | — | Row wash, badge background |
| `market.positive.fill` | `#12805C` | 4.92:1 with white ink | Solid badges and pills |
| `market.negative.text` | `#FF6B6B` | 6.53:1 | Losses in text |
| `market.negative.mark` | `#EF4E52` | 5.07:1 | Down candles, negative bars |
| `market.negative.tint` | `#2A1416` | — | Row wash, badge background |
| `market.negative.fill` | `#C43C46` | 5.14:1 with white ink | Solid badges and pills |
| `market.neutral` | `neutral.400` | 5.64:1 | Unchanged, zero, flat |

**The measurement.** Green-against-red is the pair every trading interface
uses and it is close to the worst possible pair for the most common form of
colour-vision deficiency. Measured with the Machado–Oliveira–Fernandes
simulation at full severity, in OKLab ΔE ×100:

| Pair | Deuteranopia ΔE | Normal vision ΔE | Verdict |
| --- | --- | --- | --- |
| `positive.text` ↔ `negative.text` | **8.1** | 33.2 | At the target threshold — no margin |
| `positive.mark` ↔ `negative.mark` | **6.7** | 31.3 | In the floor band; legal **only** with secondary encoding |
| Alternate blue ↔ orange (§2.6) | **27.3** | 32.1 | Four times the separation |

**Therefore three encodings are mandatory and are not stylistic
preferences:**

1. **Sign is always present.** `+$142` / `-$38`, never `$142` in green.
2. **A direction glyph accompanies every P&L value** where horizontal space
   allows, and always in tables.
3. **Position is stable.** Gains and losses occupy the same column, the
   same alignment and the same row position in every layout, so *place*
   carries information independently of hue.

A component that renders a gain or loss without at least the sign is
non-conforming. This is the one rule in this document derived from a
measurement rather than from a judgement.

### 2.6 The alternate market palette

Not a colour-blind "mode" bolted on afterwards — a first-class, one-click
alternate in Settings › Appearance, with its own validated values.

| Token | Default | Alternate |
| --- | --- | --- |
| `market.positive.mark` | `#16A97A` green | `#3987E5` blue |
| `market.negative.mark` | `#EF4E52` red | `#E0701A` orange |
| `action.primary.fill` | `#2F6FE0` blue | `#7C5CF0` violet |

**The accent moves with it, and that is the point.** In the alternate
palette blue means *gain*, so an accent that stayed blue would make every
primary button read as positive. This is precisely why the accent is a
token rather than a constant, and it is the cheapest possible proof that
the three-layer token architecture earns its keep.

### 2.7 Status colour

Reserved. Status never borrows a market colour and never borrows the
accent, and it always ships with an icon and a label — colour alone is
never the state.

| Token | Value | Contrast | Meaning | Glyph |
| --- | --- | --- | --- | --- |
| `status.info` | `#6FA8FF` | 7.52:1 | Something happened; no decision needed | `(i)` |
| `status.caution` | `#F5C044` | 10.79:1 | Needs a decision, not an acknowledgement | `(!)` |
| `status.critical` | `#FF6B6B` | 6.53:1 | A capability is lost or blocked | `(X)` |
| `status.neutral` | `neutral.400` | 5.64:1 | Insufficient evidence; unassessable | `( )` |

`status.neutral` is the token that makes §1.4 visible. It is deliberately
grey, deliberately not empty, and deliberately not the same as disabled —
"we could not measure this" is a finding, not a broken control.

`status.critical` shares its value with `market.negative.text`. This is the
one intentional value collision in the system and it is safe because the
two never appear in the same context: a P&L cell is never a system state,
and a banner is never a number. It is recorded here so that a future change
to one is understood to touch the other.

### 2.8 Focus colour

| Token | Value | Purpose |
| --- | --- | --- |
| `focus.ring` | `#A8C7FF` | The visible ring |
| `focus.gap` | `neutral.950` | A 2px separator between the element and the ring |

**Focus is a dual ring, and this is not decoration.** A focus indicator
must reach 3:1 against whatever it sits on, and an element's own colour is
not known in advance — a focused primary button, a focused chain row and a
focused input all have different backgrounds. Measured:

| Situation | Ratio |
| --- | --- |
| `focus.ring` on `surface.base` | 10.59:1 |
| `focus.ring` on `action.primary.fill` directly | **2.75:1 — fails** |
| `focus.ring` against `focus.gap` | 11.38:1 |
| `focus.gap` against `action.primary.fill` | 4.14:1 |

The dark gap gives the ring a guaranteed backdrop, so the indicator passes
on any surface in the system including a saturated fill. A single-ring
focus tinted from the accent would have failed on the one control where
focus matters most.

**Focus is never removed, never replaced by a background change alone, and
never suppressed for mouse users on controls that can also be reached by
keyboard.**

### 2.9 Light theme (future)

Light is a peer, not an inversion, and its values are re-chosen because
elevation and contrast behave differently. Specified structurally now so
that no component ships without a light definition; validated when the
phase begins.

| Rule | Dark | Light |
| --- | --- | --- |
| Elevation | Higher surfaces are **lighter** | Higher surfaces stay **white** and separate by border and shadow |
| Page | `neutral.900` | An off-white (`#FAFAFB`-class), never pure white |
| Panel | Lighter than page | White, on an off-white page |
| Primary ink | `neutral.050`, not pure white | Near-black, not pure black |
| Market colours | As §2.5 | **Re-derived.** `#3DDC97` fails on white; the light-mode positive must be a darker step |
| Accent | `#2F6FE0` | A darker step, so white ink still clears 4.5:1 |
| Borders | Barely visible, elevation does the work | Load-bearing; `border.default` becomes visible |
| Charts | Grid recedes into the surface | Grid must not disappear into white |

**The failure mode being guarded against:** applying dark's elevation logic
to light produces the grey-on-grey mud that most "light themes" are, and
reusing dark's status colours on white produces illegible green.

### 2.10 Colour usage rules

| # | Rule |
| --- | --- |
| C-1 | Anything with no meaning is neutral. Colour is a channel with a budget; the budget is one accent, two market colours, four status colours. |
| C-2 | Nothing is encoded in hue alone (§2.5). |
| C-3 | The interface does not change temperature with performance. |
| C-4 | The accent is the primary action and nothing else. Success is not green; green is gain. |
| C-5 | Status colours are reserved and never used for a chart series. |
| C-6 | The accent never appears inside a chart plot area. |
| C-7 | A tint is a background, never a text colour; a text token is never used as a fill. |
| C-8 | Every semantic token is defined for both themes at the moment it is created, never patched in later. |
| C-9 | Contrast minimums are 4.5:1 body text, 3:1 large text and meaningful non-text, verified by computation, in both themes, at all four Surface Levels. |
| C-10 | The palette must survive greyscale. A screenshot in a bug report with no colour must still be readable — which follows from C-2 but is worth testing directly. |

---

## 3. Typography

### 3.1 Families

| Role | Family | Note |
| --- | --- | --- |
| Interface | The platform UI face | Segoe UI Variable on Windows 11, SF on macOS/iOS, Roboto on Android |
| Numerals | The same family with **tabular figures enabled** | Not a separate font. Both platform faces have proper tabular figures. |
| Mono | The platform monospace face | Diagnostics, JSON, log output, API keys — nowhere else |

**Open decision (§12.4).** A vendored variable typeface is the single
highest-leverage "premium" signal available and would make desktop and
mobile visually identical. It costs one font file and conflicts with the
project's standing preference against vendored assets. Until that decision
is made, **this system specifies metrics — size, line height, weight,
tracking — rather than appearance**, so switching later is a token change
and not a redesign.

**Tabular figures are mandatory wherever numbers appear in a column.** This
is not a preference: proportional figures make a column of prices
un-scannable, and the current product already gets this right.

### 3.2 The scale

Nine roles on a ~1.2 ratio, expressed in `rem` so the operating system's
text-size setting and the product's large-text mode work by definition
rather than through a parallel token set.

| Token | Size | Line height | Weight | Tracking | Use |
| --- | --- | --- | --- | --- | --- |
| `text.display` | 2.25rem / 36px | 1.1 | 600 | -0.02em | The account value on Home. **One per screen, maximum.** |
| `text.title` | 1.75rem / 28px | 1.2 | 600 | -0.015em | Destination titles, modal titles |
| `text.section` | 1.375rem / 22px | 1.25 | 600 | -0.01em | Band and section headings |
| `text.heading` | 1.125rem / 18px | 1.35 | 600 | -0.005em | Panel headings |
| `text.body-lg` | 1rem / 16px | 1.5 | 400 | 0 | Prose that must be comfortable: review copy, Pilot, empty states |
| `text.body` | 0.875rem / 14px | 1.45 | 400 | 0 | Default interface text and table values |
| `text.body-strong` | 0.875rem / 14px | 600 | 600 | 0 | An emphasised value inline |
| `text.caption` | 0.75rem / 12px | 1.4 | 400 | 0.01em | Labels, units, metadata, help text |
| `text.micro` | 0.6875rem / 11px | 1.3 | 600 | 0.06em | Column headers and badges only. Uppercase. |

### 3.3 Hierarchy rules

| # | Rule |
| --- | --- |
| T-1 | **A number a user acts on is never smaller than `text.body`.** A strike, a price, a P&L, a max loss, a quantity. The current product violates this — several consequential figures render at 11–12px. |
| T-2 | At most four type roles are in play on a screen, plus `text.micro` for table headers. |
| T-3 | Explanatory prose caps at ~72 characters per line. Applies to Pilot, tutorials, empty states, review copy and error messages. |
| T-4 | `text.micro` is uppercase and tracked; no other role is ever uppercased. Uppercase is a structural signal meaning "this is a label for a column," not an emphasis tool. |
| T-5 | Weight carries hierarchy before size does. Prefer 400→600 at the same size over stepping the scale. |
| T-6 | Never more than two weights in a component. |
| T-7 | Italic is not used anywhere in the product. |

### 3.4 Number formatting

Formatting is part of typography here, because in a trading interface the
*shape* of a number is most of its legibility.

| Kind | Format | Example |
| --- | --- | --- |
| Currency, account scale | Grouped, 2 decimals, symbol leading | `$10,412.55` |
| Currency, P&L | Sign always, grouped, 2 decimals | `+$142.00` / `-$38.00` |
| Currency, option premium | 2 decimals, no grouping | `$3.90` |
| Percent | Sign for change, 1–2 decimals, `%` attached | `+18.2%` |
| Percent, probability | 0 decimals | `54%` |
| Strike | No decimals unless the underlying has them | `470` |
| Greeks | 2–3 decimals, leading zero omitted for magnitude ≤1 | `.541`, `-.23` |
| Volume / open interest | Abbreviated above 10,000, one decimal | `22k`, `1.4M` |
| Quantity | Integer, no grouping below 1,000 | `6` |
| Sample size | `n=41`, always adjacent to the statistic | `58%  n=41` |
| Not measured | An em dash plus a reason, never `0` | `--  (0 of 5)` |
| Infinite | Words | `no losing trades yet` |

**Alignment.** Numerics right-align. Text left-aligns. A column mixing
both aligns as its dominant type and does not centre. Decimal points align
because the figures are tabular and the decimal count is fixed per column —
a column that varies its decimal count is a formatting bug.

**The sign occupies space even when positive.** A column where `+$142` and
`$142` both occur has a one-character horizontal jitter that makes the
column unreadable at a glance.

### 3.5 Price typography

The live price is the most-read number in the product and gets an explicit
treatment.

| Context | Role | Composition |
| --- | --- | --- |
| Frame context slot | `text.body` + `text.caption` | `SPY` · price · day change with sign and glyph |
| Home account value | `text.display` | Value on one line, day change beneath in `text.body` |
| Chain price | `text.body`, tabular | Bid/ask/mid, right-aligned |
| Position mark | `text.body` | Mark, then P&L in the market colour with sign |
| Chart price axis | `text.caption`, tabular | Right-aligned, the last price in a filled label |

### 3.6 Table typography

| Element | Role | Alignment |
| --- | --- | --- |
| Column header | `text.micro`, uppercase, `neutral.400` | Matches its column |
| Cell, text | `text.body`, `neutral.200` | Left |
| Cell, primary identifier | `text.body-strong`, `neutral.050` | Left |
| Cell, numeric | `text.body` tabular, `neutral.050` | Right |
| Cell, P&L | `text.body` tabular, market colour, sign + glyph | Right |
| Cell, metadata | `text.caption`, `neutral.400` | Left |
| Cell, unmeasured | `text.body`, `neutral.400`, `--` plus reason | As its column |
| Caption / count | `text.caption`, `neutral.400` | Left |

### 3.7 The tick treatment

Replacing the industry's flash-on-tick (§1.1):

- The value updates **instantly**, with no transition.
- A **direction glyph** sits adjacent to the price and reflects the last
  change. It is a shape change, not a colour flash, and it persists until
  the next change.
- The **day-change colour** on the change figure carries session direction
  and is stable.
- Nothing about the cell's size, weight or background changes.

A user who wants tick-by-tick direction has it, permanently and readably. A
user who does not is not being flashed at all day.

---

## 4. Spacing

### 4.1 The scale

A 4px base with steps named by **purpose**, so a component picks a
relationship rather than a number. This extends `UI_V2_DESIGN.md` §11.4
without changing it.

| Token | Value | Purpose |
| --- | --- | --- |
| `space.0` | 0 | Deliberate adjacency (segmented control segments, table cells) |
| `space.1` | 4px | Within a control — icon to label, stepper to field |
| `space.2` | 8px | Between tightly related elements — label to input, chip to chip |
| `space.3` | 12px | Between fields in a group |
| `space.4` | 16px | Between groups inside a panel; default panel padding |
| `space.5` | 24px | Between panels; the layout gutter |
| `space.6` | 32px | Between bands |
| `space.7` | 48px | Page-level separation; the top of an empty state |

**No value outside this scale appears anywhere**, with exactly one class of
exception: **optical adjustment on icons and glyphs**, which may take
sub-4px offsets to sit on a text baseline. Those are properties of the icon
set (§8), not free-form spacing.

### 4.2 Component padding

| Component | Padding | Notes |
| --- | --- | --- |
| Panel / card | `space.4` all sides | `space.5` at Comfortable density |
| Panel heading to body | `space.3` | |
| Button, medium | `space.2` vertical, `space.4` horizontal | |
| Button, small | `space.1` vertical, `space.3` horizontal | |
| Button, large (commit) | `space.3` vertical, `space.5` horizontal | |
| Input | `space.2` vertical, `space.3` horizontal | |
| Dropdown item | `space.2` vertical, `space.3` horizontal | |
| Table cell | `space.2` vertical, `space.3` horizontal | `space.1`/`space.3` at Compact |
| Tooltip | `space.2` / `space.3` | |
| Toast | `space.3` / `space.4` | |
| Modal | `space.5` | `space.6` at the top for the title |
| Popover | `space.4` | |
| Status pill | `space.0` / `space.2` | |

### 4.3 Rhythm rules

| # | Rule |
| --- | --- |
| SP-1 | Vertical gaps come from the scale. If a layout needs 18px, it needs 16 or 24, and the designer decides which relationship it is. |
| SP-2 | The gap between two things expresses their relationship. Two elements `space.2` apart are one object; `space.5` apart they are two. |
| SP-3 | **Remove every border and background and the grouping must still read.** If it does not, boxes are doing the work rhythm should do. |
| SP-4 | Whitespace is never added to fill. An empty region is filled with an empty state (§6.20), not with padding. |
| SP-5 | Density changes the *applied* scale and row heights only. It never changes what is displayed — that is Surface Level, a different axis, and the two must not couple. |
| SP-6 | A panel's internal padding never varies by content. |

### 4.4 Chart spacing

| Element | Value |
| --- | --- |
| Plot area to panel edge | `space.4` left/right, `space.3` top |
| Price axis gutter | 56px fixed, right |
| Time axis gutter | 28px fixed, bottom |
| Plot to axis | `space.2` |
| Legend to plot | `space.3` |
| Right-hand price padding | 8% of the visible price range, so the last candle is never against the axis |
| Between stacked panes (price / volume) | `space.2`, no divider |

### 4.5 Table spacing

| Density | Row height | Cell vertical padding | Header height |
| --- | --- | --- | --- |
| Comfortable (Surface Levels 1–2) | 40px | `space.2` | 32px |
| Compact (Surface Levels 3–4) | 28px | `space.1` | 28px |

Row height is fixed per density. A table whose rows vary in height cannot
be scanned, and it makes virtualised rendering and skeleton sizing
impossible to get right.

---

## 5. Grid

### 5.1 The desktop grid

12 columns, `space.5` (24px) gutter, `space.5` outer margin inside the
content area. The four layout archetypes in `UI_V2_WIREFRAMES.md` §0.2 map
onto it:

| Archetype | Column split |
| --- | --- |
| A — Bands | Full width; band 2 splits 7 / 5 |
| B — Workspace | 8 / 4, with the 8 split horizontally 55 / 45 |
| C — Index + Detail | Fluid list + 320px fixed rail |
| D — Sections | 168px fixed section rail + fluid content |

### 5.2 Fixed and fluid

| Element | Behaviour |
| --- | --- |
| Nav rail | Fixed: 216px expanded (>=1280) / 64px icon-only (<1280) |
| Frame | Fixed 48px tall |
| System strip | Fixed 28px tall |
| Section rail (D) | Fixed 168px |
| Detail rail (C) | Fixed 320px |
| Ticket column (B) | Resizable 300–480px, default 340px, persisted |
| Chart / chain split (B) | Resizable, default 55/45, persisted |
| Everything else | Fluid |

**The rail has two states, not three (M3.5).** It was 200 / 72 / 56 across two
breakpoints, so dragging a window produced two collapse events — and §6.9
forbids animating the collapse, which makes each one a jump. The fix for a
jarring transition is *fewer* transitions. The 72px state drew no label, which
is the whole definition of icon-only, so it was a second icon-only mode
differing from the first by 16px of padding.

64 rather than 56 because, once the rail's own padding is off, 64 leaves a
44px hit target with clearance where 56 left exactly 40px and nothing spare
for the active indicator (WCAG 2.5.8's floor is 24px; 44 is the comfortable
standard). And **216 = 64 + 152**, which is what lets the frame's left zone be
exactly `--rail-w` in *both* states — see §5.5.

### 5.3 Minimum and maximum widths

| Constraint | Value | Why |
| --- | --- | --- |
| Minimum window | 1024px | Below it, a message rather than a degraded layout |
| Minimum content area | 768px | |
| Maximum prose width | 72ch | Review copy, Pilot, empty states, error messages, tutorials |
| Maximum table width | none | Tables fill; they are scanned, not read |
| Maximum panel width | none | A workspace fills its window (`UI_V2_DESIGN.md` §13.1) |

**There is no global maximum content width.** Today's `max-width` on the
main region is a page convention that leaves a 27-inch monitor mostly
empty. Prose is capped; the workspace is not.

### 5.4 Docking and splitters

| Property | Specification |
| --- | --- |
| Splitter target | 8px hit area, 1px visible line |
| Visible state | `border.subtle`; on hover `border.control`; on drag `action.primary.text` |
| Motion | **None.** A splitter follows the pointer exactly; a transition on a drag is lag. |
| Constraints | Each side has a minimum; dragging past it stops rather than collapsing |
| Reset | Double-click restores the default ratio |
| Persistence | Per destination, through `RuntimeSettings`, not client storage |
| Keyboard | Focusable; arrows move by 16px, `Home` resets |
| Pop-out | Chart, ticket and Portfolio can become OS windows; the vacated slot shows a placeholder with `[ Bring back ]`, never a silent reflow |

---

### 5.5 The left column, and the continuous rule

**The frame's wordmark box is exactly `--rail-w` wide and its right border
continues the rail's**, so one unbroken vertical line runs from the top of the
window to the system strip. Below 1280 the wordmark becomes `OP`, because
"OptionsPilot" is 86px wide and clipping it to fit 64px is what made the rail
look like it was sliding *underneath* the header. Two visible marks, one
accessible name carried by `.sr-only` text, so what a screen reader announces
does not change with the viewport.

Cockpits read as engineered because their lines continue. Before M3.5 nothing
in this interface ran the full height of the window.

### 5.6 The destination split

**One ratio, one class, one seam.** A two-column destination body is
`.dest-split`, whose columns come from `--split-major` / `--split-minor`. Home
carried 1.45fr:1fr in band 2 and 2fr:1fr in band 3, so the vertical seam
stepped sideways 124px halfway down the page. The eye tracks vertical edges;
one that moves for no reason reads as misalignment even when a viewer cannot
name what is wrong.

### 5.7 How a destination is shown

**A visibility rule may only ever HIDE. It must never assert a positive
`display` mode.** `display` belongs to the destination's own layout class, and
a rule that sets it will collide — silently, and in the direction that looks
fine. Home shipped M3 with `body.shell-v2 #home { display:block }` (0,1,1,1)
outranking `.home`'s `display:flex` (0,0,1,0), so its `gap:var(--space-6)` was
inert and all three bands rendered touching at 0px. Write the rule as a
negative — `body:not(.shell-v2) #home { display:none }` — and it cannot
compete.

## 6. Component library

Every component specifies: **purpose · variants · states · motion ·
keyboard · accessibility · rules**. A component missing any of these is not
finished. States are always: default, hover, active, focus, disabled,
loading, error — and where a state is not applicable the component says so
rather than omitting it.

### 6.1 Button

**Purpose.** Perform an action. Never navigate — navigation uses a link or
a row.

**Variants.**

| Variant | Fill | Ink | Border | Use |
| --- | --- | --- | --- | --- |
| Primary | `action.primary.fill` | `action.primary.ink` | none | The one primary action in a view |
| Secondary | transparent | `neutral.050` | `border.control` | Ordinary actions |
| Tertiary | transparent | `action.primary.text` | none | Low-weight actions, inline links-as-actions |
| Destructive | transparent | `market.negative.text` | `market.negative.text` at 40% | Close, cancel, delete — always paired with a commit gesture |
| Commit | `action.primary.fill` | `action.primary.ink` | none | Large; hold-to-confirm only (§6.2) |

**Sizes.** Small (24px), Medium (32px, default), Large (40px), Commit
(48px).

**States.**

| State | Treatment |
| --- | --- |
| Default | As variant |
| Hover | Primary: fill lightens one step. Secondary: border → `neutral.400`, background → `action.primary.tint` at 40%. Tertiary: text brightens. |
| Active (pressed) | Fill darkens one step; **no scale transform** |
| Focus | Dual ring (§2.8), 2px gap + 2px ring, outside the border box |
| Disabled | Ink `neutral.450`, fill/border at 40%, cursor unchanged. **A reason is always available** via tooltip and `aria-describedby`. |
| Loading | Label persists, dimmed to 60%; a small indeterminate indicator replaces the leading icon slot; width does not change |
| Error | Buttons do not have an error state. The error appears below the button, action-scoped (§6.22). |

**Motion.** Hover and active at `instant` (~100ms). No scale, no shadow
change, no ripple.

**Keyboard.** `Enter` and `Space` activate. `Tab` reaches every enabled
button. A disabled button remains focusable so its reason can be read —
this is deliberate and is the opposite of the common pattern.

**Accessibility.** Icon-only buttons carry an accessible name matching
their tooltip. Loading state sets `aria-busy`. Toggle buttons use
`aria-pressed`, not a colour change alone.

**Rules.** One primary per view. A destructive action is never the primary.
Width never changes between states. Buttons do not carry badges or counts.

### 6.2 Commit control

**Purpose.** The single component through which capital moves. Hold to
confirm on desktop; the Commit Rail on mobile (§11).

**Anatomy.**

```
  +--------------------------------------------------------+
  |  Hold to place order         [==========>          ]   |
  +--------------------------------------------------------+
```

**States.**

| State | Treatment |
| --- | --- |
| Armed | Primary fill; label states the action; the track is empty |
| Holding | The fill sweeps left to right over ~600ms; the label switches at 50% from the action to the maximum loss |
| Released early | The fill retreats at `fast`; the label returns; **no message, no dialog** |
| Qualified | The fill completes; on mobile, one haptic tick |
| Submitting | The label becomes "Placing…"; the control is inert |
| Failed | The control re-arms; an action-scoped error appears beneath |
| Disabled | With the reason stated adjacent, always |

**Motion.** The fill is the only `deliberate` (~600ms) animation in the
product. Under reduced motion the duration is unchanged and the sweep
becomes four discrete steps — it is a timing affordance, not decoration.

**Keyboard.** Hold `Enter`. Identical duration, identical indicator,
identical early-release cancel.

**Accessibility.** Announces three moments — started, qualified, placed.
The instruction is programmatically associated. Progress is exposed as a
progress bar with a value, so a screen-reader user knows how much of the
hold remains.

**Rules.** No success animation follows. Nothing else in the product uses
this component or this duration. It is never reachable by a single click,
a double-click, or an un-held `Enter`.

### 6.3 Input

**Purpose.** Accept a typed value.

**Variants.** Text · Number (with stepper) · Search · Symbol · Textarea ·
Masked (API keys).

**Anatomy.**

```
  Limit price
  +----------------------+
  |  3.90             $  |
  +----------------------+
  Between $3.85 and $3.95
```

Label above. Unit or adornment inside, right. Help text below. Error
replaces help text.

**States.**

| State | Treatment |
| --- | --- |
| Default | `surface.sunken` fill, `border.control`, `neutral.050` ink |
| Hover | Border → `neutral.400` |
| Focus | Dual focus ring; border → `action.primary.text` |
| Filled | Identical to default — a filled field is not a state |
| Disabled | Fill 60%, ink `neutral.450`, reason available |
| Read-only | No border, no fill; reads as text with a copy affordance |
| Loading | Skeleton in place of the field, label still visible |
| Error | Border → `market.negative.text`; message below in `text.caption`, in words |

**Motion.** Border and ring at `instant`. No label float, no placeholder
animation.

**Keyboard.** Number inputs: `↑` `↓` step, `Shift` for ×10. `Esc` reverts
to the last committed value. `Enter` commits.

**Accessibility.** Every field has a real label element. Errors are
associated by `aria-describedby` and announced politely. Masked inputs
announce as "API key, hidden, ending 4f2a" and have no reveal control.

**Rules.** Never a placeholder as the label. Numeric fields are tabular.
Validation runs on blur and on submit, never on every keystroke — a field
that turns red while you are still typing the second character is
punishing.

### 6.4 Dropdown / select

**Purpose.** Choose one value from a bounded list.

**Variants.** Native-behaviour select (≤10 options) · Searchable listbox
(>10) · Grouped listbox.

**States.** Closed states mirror Input. Open: `surface.raised`, soft
shadow, max height 320px with internal scroll, selected item marked with a
check **and** `action.primary.tint`.

**Motion.** Opens with a scale-and-fade from the trigger at `fast`;
closes at `fast`. The list does not slide.

**Keyboard.** `Space` / `Enter` / `↓` opens. `↑` `↓` move. Type-ahead
jumps. `Enter` selects, `Esc` closes without selecting and returns focus.

**Accessibility.** Trigger exposes the current value in its accessible
name. Uses a listbox pattern with `aria-activedescendant`. The open list
traps arrow keys but not `Tab`, which closes and moves on.

**Rules.** Never used for more than one selection — that is a multi-select
or a set of checkboxes. Never used when there are two or three options that
fit — those are a segmented control (§6.10).

### 6.5 Segmented control

**Purpose.** Switch between two to four mutually exclusive views or modes,
where all options should be visible.

**Anatomy.**

```
  +---------+---------+---------+
  |  Calls  |  Puts   |  Both   |
  +---------+---------+---------+
```

**States.** Selected: `surface.raised` fill, `neutral.050` ink, a 1px
`border.control`. Unselected: transparent, `neutral.200` ink. Hover on
unselected: `neutral.050` ink. Focus: ring on the focused segment.
Disabled segment: `neutral.450` with a reason.

**Motion.** The selected indicator slides between segments at `fast`. This
is one of the few permitted slides, because it answers "where did the
selection go."

**Keyboard.** `←` `→` move and select. `Tab` enters and leaves the group as
one stop.

**Accessibility.** Radio group semantics, or tablist where it switches
views. Never checkbox semantics.

**Rules.** Maximum four segments. Labels are one or two words. Never used
for actions — a segment is a state, not a verb.

### 6.6 Card / panel

**Purpose.** Group related content with a heading and, optionally, one
action.

**Anatomy.**

```
  +------------------------------------------+
  |  POSITIONS (2)               [Manage >]  |
  |  --------------------------------------  |
  |  content                                 |
  +------------------------------------------+
```

**Variants.** Panel (a region of a destination) · Metric card (§9.5) ·
Interactive card (mobile lists).

**States.** Panels are not interactive and have no hover state. Interactive
cards get hover (border → `border.control`), focus (ring) and selected
(`action.primary.tint` wash + 2px left edge).

**Motion.** Content changes cross-fade at `fast`. The panel itself never
animates its size.

**Rules.** One heading, optional metadata, a body, at most one action. **A
panel with two competing actions is two panels.** A panel never scrolls
internally unless its content is a list or a table.

**Tiers (added M3.5).** One instrument component reused N times guarantees
consistency and thereby guarantees *uniformity*, and uniformity is the
absence of hierarchy. Measured on Home before M3.5: five regions, every one
`surface.base` at `radius.med` with `space.4` padding, on a canvas they
separate from by about 1.15:1 — five things of different importance rendered
as five things of identical importance, which is the fault §5.1 of
`UI_V2_DESIGN.md` names in the *old* dashboard.

| Tier | Class | Treatment | Budget |
| --- | --- | --- | --- |
| 1 — focal | `.ins--focal` | Elevated one surface step, plus the 2px inset edge the nav rail uses for "you are here" | **At most one per destination** |
| 2 — standard | `.ins` | The default. Unchanged. | Any |
| 3 — quiet | `.ins--quiet` | No housing: transparent, square, grouped by a single top rule | Any |

The levers are **elevation, space and rule** — never a bright fill, never a
glow, never a border all round. Two rules that are decisions:

- **The focal tier gets no extra padding.** It would push its label out of
  line with the instrument below it in the same column, and alignment
  outranks emphasis.
- **The quiet tier keeps its horizontal padding.** Its interiors must stay
  aligned with the tier-2 instruments above them; a quiet region is quieter,
  not offset.

Removing the container is a legitimate hierarchy move and the only one that
costs no ink. ThinkOrSwim groups almost everything this way.

### 6.7 Table

**Purpose.** Compare many rows on the same attributes. The most important
component in the product after the chart.

**Anatomy.**

```
  CONTRACT          QTY   MARK      P&L       %    DTE
  ---------------------------------------------------
> SPY  470C 12 Sep    1   3.92    +$142   +18.2%     7
  AAPL 190P 19 Sep    1   0.88     -$38    -4.1%    14
```

**Structure rules.**

| # | Rule |
| --- | --- |
| TB-1 | Headers are `text.micro`, uppercase, sticky when the body scrolls |
| TB-2 | Numerics right-align with tabular figures; text left-aligns; nothing centres |
| TB-3 | Row height is fixed per density (§4.5) |
| TB-4 | Zebra striping only above ~15 rows, and then at 2% opacity — a hairline is usually better |
| TB-5 | A selected row survives a data refresh |
| TB-6 | Sort indicators are explicit; the sorted column's header is `neutral.050` |
| TB-7 | A table is one tab stop with roving focus; arrows move the selection |
| TB-8 | Column sets change with Surface Level; the **row set never does** |

**States.**

| State | Treatment |
| --- | --- |
| Row hover | `surface.sunken` wash; row actions become visible |
| Row selected | `action.primary.tint` wash + 2px `action.primary.text` left edge |
| Row focused | Focus ring inset on the row |
| Row disabled | Ink `neutral.450`, reason on hover |
| Loading | Skeleton rows at the exact row height, fixed count (§6.21) |
| Empty | The empty state replaces the body; headers remain (§6.20) |
| Error | The error replaces the body; headers remain |

**Motion.** Row enter fades at `fast`. Row exit fades, then the gap closes
at `fast` — two steps, so the user sees which row left. Sorting does not
animate. Values never animate.

**Keyboard.** `↑` `↓` move; `PgUp` `PgDn` by ten; `Home` `End`; `Enter`
activates; `Space` selects in multi-select tables; `Del` where a row
supports removal.

**Accessibility.** Real table semantics with `scope` on headers, a caption
stating contents and count, `aria-rowcount` when virtualised,
`aria-selected` on the selected row. Row actions carry accessible names
naming their subject ("Close SPY 470 call"), never "Close."

### 6.8 Chart

Charts are specified in §9. As a component: the chart owns its own
interaction physics and is **exempt from this system's motion and layout
transitions**. Nothing in this document may add a transition, transform or
resize observer to a chart canvas.

### 6.9 Navigation rail

**Purpose.** Move between the five destinations, plus Pilot and Settings.

**States.**

| State | Treatment |
| --- | --- |
| Default | Icon `neutral.400`, label `neutral.200` |
| Hover | Icon and label `neutral.050`, background `surface.sunken` |
| Active | Icon and label `neutral.050`, background `action.primary.tint`, **2px left edge in `action.primary.text`** |
| Focus | Ring inset |
| Collapsed | Icon only; the label becomes a tooltip after 400ms |

**Motion.** The active left-edge indicator slides between items at `fast`.
Nothing else in the rail animates. Collapse between breakpoints is instant
— an animated rail collapse during a window resize is motion the user did
not ask for.

**Keyboard.** `1`–`5`, `,` for Settings. `Tab` enters the rail as one stop;
arrows move within.

**Accessibility.** A navigation landmark containing a list of links with
`aria-current="page"` on the active one. Collapsed items keep their
accessible names. Number hints are decorative and hidden from assistive
technology.

**Rules.** Never collapses to a hamburger. The active state is never colour
alone — the left edge and the background carry it too.

### 6.10 Tabs

**Purpose.** Switch between sibling views inside a destination (the
section rails in Research, Journal, Settings).

**Variants.** Vertical section rail (default, Archetype D) · Horizontal
segmented (below 1280px, and inside the ticket column when chain and ticket
share a region).

**States.** As the nav rail, minus the number hints. A tab may carry **one
count** — Journal's `Review 2` — and that is the only count permitted in
navigation, because it represents work the user has chosen to do.

**Motion.** Indicator slide at `fast`; content cross-fade at `fast`.

**Keyboard.** `Alt+↑` `Alt+↓` for vertical, `←` `→` for horizontal.
`Home` / `End` jump.

**Accessibility.** Tablist / tab / tabpanel with `aria-controls` and
`aria-selected`. Focus follows selection for lightweight panels; for panels
that fetch, selection follows `Enter` so arrowing through does not trigger
six requests.

### 6.11 Command palette

**Purpose.** Reach any destination, action or setting by name. The
component that makes a six-item navigation viable.

**Anatomy.**

```
  +---------------------------------------------------+
  |  >  opti|                                    Esc  |
  |                                                   |
  |  DESTINATIONS                                     |
  |    Portfolio                                   3  |
  |  ACTIONS                                          |
  |    Run a scan now                                 |
  |  MOVED                                            |
  |    Coach              ->  Journal > Review        |
  +---------------------------------------------------+
```

**Specification.**

| Property | Value |
| --- | --- |
| Width | 640px fixed, all breakpoints |
| Position | Horizontally centred, top at 18% of viewport height, **fixed — it does not re-centre as results change** |
| Max height | 60% of viewport; the list scrolls, the input never leaves view |
| Surface | `surface.raised`, large soft shadow, scrim behind |
| Group headers | `text.micro`, `neutral.400` |
| Row | 36px, icon slot + label + right-aligned binding or path |
| Highlighted row | `action.primary.tint` + focus-style left edge |

**Motion.** Scale-and-fade in at `fast` from centre. Result list changes do
**not** animate — a list that animates while you type is unusable.

**Keyboard.** `Ctrl+K` opens. `↑` `↓` move. `Enter` activates. `Esc`
closes and clears. Type-ahead is the only input.

**Accessibility.** Combobox with an owned listbox and
`aria-activedescendant`. The result count is announced politely on change,
rate-limited. Focus returns to the invoking element on close.

**Rules.** Destructive actions never execute from the palette; they
navigate to the control and focus it. Every row's right column shows its
binding — the palette is how shortcuts are learned.

### 6.12 Search

**Purpose.** Filter a visible set. Distinct from the palette, which
*navigates*.

**States.** As Input, plus: a clear affordance appears once non-empty; a
result count renders adjacent in `text.caption`.

**Motion.** None. Results filter instantly; the list does not animate.

**Keyboard.** `Esc` clears then blurs. `↓` moves into the results.

**Rules.** Search never navigates away from the current screen. Filtering
is debounced at ~120ms for remote sources and is immediate for local ones.

### 6.13 Badge

**Purpose.** A small count or a short static label attached to an object.

**Variants.** Count (numeric) · Label (text) · Dot (presence only).

**States.** Static. Badges have no hover, focus or disabled state; they are
not interactive. A badge on an interactive parent inherits nothing.

**Rules.** Counts cap at `99+`. A badge never appears on a navigation
destination (§6.9) or on Pilot. A dot badge is never the only indicator of
a state that matters.

### 6.14 Tag

**Purpose.** A removable, user-controlled token — watchlist symbols,
filters.

**States.** Default `surface.sunken` + `border.subtle`. Hover reveals the
remove affordance. Focus ring. Selected: `action.primary.tint`.

**Keyboard.** `Tab` reaches the tag; `Del` / `Backspace` removes it; arrows
move between tags in a group.

**Rules.** A tag is always removable — if it cannot be removed it is a
badge.

### 6.15 Status pill

**Purpose.** Express the state of an object in one glanceable unit.

**Anatomy.**

```
  (i) ok        (!) rate limited      (X) no plan       ( ) not measured
```

**Variants.** Info · Caution · Critical · Neutral · Market-positive ·
Market-negative.

**Composition rule.** Glyph + label + tint background. **Never colour
alone, never a bare dot.** This is C-2 applied to the component that would
be most tempted to break it.

**States.** Static, unless the pill is a control — the Flight Status pill
is a button and takes hover, focus and active states from §6.1's secondary
variant.

**Rules.** The label is always present, even when the glyph seems
sufficient. A pill never wraps; if the label does not fit, the container is
wrong.

### 6.16 Tooltip

**Purpose.** Name or briefly explain the element under the pointer.

**Specification.**

| Property | Value |
| --- | --- |
| Delay in | 400ms |
| Delay out | 100ms |
| Surface | `surface.raised`, `border.subtle`, small shadow |
| Type | `text.caption` |
| Max width | 280px |
| Position | Above by default; flips to stay in the viewport; never covers its trigger |

**Motion.** Fade at `fast`. No slide, no scale.

**Keyboard.** Appears on focus with no delay. `Esc` dismisses without
moving focus.

**Accessibility.** A tooltip that only names its trigger is the accessible
name. A tooltip that adds information is `aria-describedby`.

**Rules.** **A tooltip is never the only home for information a decision
needs.** Sample sizes, p-values, maximum loss, guardrail reasons and error
causes are visible text. A tooltip is an accelerator for the sighted mouse
user and nothing more — this is the single rule that most often separates
an accessible trading interface from an inaccessible one.

### 6.17 Popover

**Purpose.** A small, dismissible surface anchored to a trigger, for
choices and secondary detail — Flight Status, expiry picker, column
picker, Surface Level menu.

**States.** Closed / open. The trigger takes an active treatment while
open.

**Motion.** Scale-and-fade from the trigger at `fast`. This is M-3 and it
answers "where did this come from."

**Keyboard.** `Esc` closes and returns focus. `Tab` cycles within.

**Accessibility.** Focus moves in on open and returns on close.
`aria-expanded` on the trigger, `aria-haspopup` where a menu.

**Rules.** Never scroll-locks the page. Repositions to stay in the
viewport. Never nests. **A popover is the default for a small choice —
using a modal for one is a specification error** (`UI_V2_DESIGN.md` §10.4).

### 6.18 Modal

**Purpose.** Two things only: the order review, and a critical condition
that changes what the user can do.

**Anatomy.** Title · body · one primary action · one dismissal.

**Specification.** Centred, 560–680px, `surface.raised`, large soft shadow,
`neutral.950` scrim at 60% — **dimmed, never blurred**.

**Motion.** Scale-and-fade from the opening control at `medium`. Closing
reverses at `fast`.

**Keyboard.** `Esc` closes. Focus is trapped and returned to the opener.
`Tab` cycles within.

**Accessibility.** Dialog semantics, labelled by its title. Background is
inert, not merely covered.

**Rules.** Never nests. Never scrolls the page behind it. Never used for a
small choice.

### 6.19 Context menu

**Purpose.** Actions on a specific object, invoked from the object.

**Specification.** `surface.raised`, small shadow, 32px items, dividers
between groups, destructive items in `market.negative.text` at the bottom
behind a divider.

**Motion.** Fade at `fast`, no slide.

**Keyboard.** `Shift+F10` or the context key opens it on the focused
object. Arrows move, `Enter` activates, `Esc` closes.

**Accessibility.** Menu semantics. Every action also exists somewhere
visible — a context menu is an accelerator, never the only path.

**Rules.** Maximum ~8 items. Destructive actions still require review and
the commit gesture; the menu opens the review, it does not perform the act.

### 6.20 Empty state

**Purpose.** Say what belongs here, why it is not here, and offer the one
action that fills it.

**Anatomy.**

```
  You have no open positions.
  One appears the moment an order fills.

  [ Place a practice trade ]
```

**Specification.** Left-aligned within its region, `text.body` for the
first line in `neutral.200`, `text.caption` for the second in
`neutral.400`, one secondary button. **No illustration, no large icon.**

**Rules.**

| # | Rule |
| --- | --- |
| EM-1 | Every empty state contains a verb |
| EM-2 | Where a threshold exists, state it numerically: "5 closed trades; you have 2" |
| EM-3 | The layout does not change between empty and populated |
| EM-4 | Quiet. Emptiness is not an event, and a celebratory illustration in a region that should contain the user's money is unnerving |
| EM-5 | Distinguish "nothing yet" from "nothing matched a filter" — the second offers to clear the filter |

### 6.21 Skeleton

**Purpose.** Hold the shape of content that is arriving.

**Specification.** `neutral.800` blocks at the exact dimensions of the
content they replace, `radius.sm`. A very slow, very low-contrast shimmer
is permitted — 1.6s, ≤4% luminance amplitude — and is removed entirely
under reduced motion.

**Rules.**

| # | Rule |
| --- | --- |
| SK-1 | Structure renders immediately; only values skeletonise. Headings, labels, column headers and controls are never skeletons |
| SK-2 | Appear only after 200ms |
| SK-3 | Exact height of the real content; nothing shifts on arrival |
| SK-4 | Fixed counts — 2 position rows, 5 trade rows, 8 chain rows — never guessed from prior state |
| SK-5 | Convert to the error state after 10s. An indefinite skeleton is a bug |
| SK-6 | There is no full-screen skeleton anywhere in the product |

### 6.22 Progress indicator

**Purpose.** Show that work is happening and, where possible, how much
remains.

**Variants.**

| Variant | Use |
| --- | --- |
| Determinate bar | Backtests, cache rebuilds — anything with a known denominator |
| Indeterminate line | Actions of unknown duration, at the top of the affected region |
| Inline spinner | Inside a button that is loading |
| Commit fill | §6.2 only |

**Rules.** Progress is never used for *content* — that is a skeleton.
Determinate progress states the unit ("bar 412 of 1,250"), because a bar
with no number cannot be judged. Progress never blocks the interface: a
running scan or backtest leaves everything else usable.

### 6.23 Toast

**Purpose.** Report something that happened elsewhere.

**Specification.** Bottom-right, 360px, `surface.raised`, `border.subtle`,
soft shadow. Glyph + title + one-line body + optional single action.
Maximum three stacked; older collapse to `+ N more`.

**Timing.** Informational auto-dismiss at 5s. Caution persists until
acknowledged. Critical does not use a toast — it uses a banner.

**Motion.** Enter and exit fade at `fast`. No slide-in from the edge.

**Keyboard.** `Esc` dismisses the top toast. Toasts do not steal focus,
ever.

**Accessibility.** Rendered into the shell's single polite live region,
rate-limited to one utterance per three seconds with coalescing.

**Rules.** **A toast never announces something already visible on
screen.** Never covers the ticket, the commit control or the chain. Every
toast's action leads to the subject.

### 6.24 Banner

**Purpose.** An app-scoped condition that changes what the user can do —
trading halted, engine disconnected, preferences reset.

**Specification.** Full width, directly below the frame, `status.*` tint
background with a 2px top-aligned status edge, glyph + text + at most one
action.

**Motion.** Appears without animation. The layout below shifts down
instantly rather than sliding — a banner that pushes the workspace with a
transition is motion during a crisis.

**Rules.** At most one banner at a time; the most severe wins. Persists
until resolved or explicitly dismissed. Never used for anything a toast or
an inbox entry could carry.

### 6.25 Notification inbox

**Purpose.** The persistent, grouped history of everything that happened.

**Specification.** 480px panel anchored to the system strip's notification
control. Grouped by day, coalesced by subject. Filters: All / Unread. Each
entry: severity glyph · title · one-line body · timestamp · `[Open >]`.

**States.** Unread entries carry a 2px left edge in their severity colour
and `neutral.050` titles; read entries drop to `neutral.200`.

**Motion.** Panel scale-and-fade at `fast`. Entries do not animate in.

**Rules.** Every entry navigates to its subject. Entries expire on
*relevance*, not time. Read state persists and is shared with mobile.

### 6.26 Pilot surfaces

**Purpose.** Explanation, in four forms (`UI_V2_DESIGN.md` §9.3).

| Surface | Component |
| --- | --- |
| Inline explanation | A quiet marker beside a term or statistic; opens a popover in place |
| Home "what to do next" | A list of at most three findings, each with `n` and `p` in visible text |
| Panel | 380px right overlay; **never reflows the workspace** |
| Journal note | Body text within the trade detail |

**Visual treatment.** Pilot has **no avatar, no colour of its own, no
badge, no dot, no bubble.** Its affordance is a word in the frame. Its
content wears ordinary text tokens. The absence of visual identity is the
design: a mentor that decorates itself is a mascot.

**Motion.** Panel slides in from the right edge at `medium` — the one
directional slide in the product, because the panel comes from an edge and
the motion says so. Content changes cross-fade at `fast`.

**Rules.** Never opens itself. Never animates to attract attention. The
eight silence rules bind the component, not just the feature.

### 6.27 Chip / quick pick

**Purpose.** A one-click resolution of an intent — `[ATM call]`,
`[30 day]`, watchlist presets.

**States.** As a secondary button at small size, with `radius.pill`.
Selected chips take `action.primary.tint` and a `border.control`.

**Rules.** A chip is always a shortcut to something also reachable the long
way. A chip never performs a consequential action directly — a quick pick
populates the ticket; it does not place an order.

---

## 7. Motion

### 7.1 Durations

| Token | Duration | Use |
| --- | --- | --- |
| `motion.instant` | 100ms | Hover, focus, press — below deliberate perception |
| `motion.fast` | 130ms | State changes, tooltips, popovers, toggles, cross-fades |
| `motion.medium` | 220ms | Modals, panels, drawers, advice replacement |
| `motion.deliberate` | 600ms | The commit gesture only |

### 7.2 Curves

Three, chosen by physics rather than taste. Values are control points,
given as data.

| Token | Points | Use |
| --- | --- | --- |
| `motion.enter` | `(0.0, 0.0, 0.2, 1.0)` | Entering elements decelerate |
| `motion.exit` | `(0.4, 0.0, 1.0, 1.0)` | Exiting elements accelerate |
| `motion.move` | `(0.4, 0.0, 0.2, 1.0)` | Elements moving within the viewport ease both ends |

**No bounce, no overshoot, no elastic, no spring.** Playfulness in motion
signals "this is a toy," which is precisely wrong for an instrument that
handles money.

### 7.3 The catalogue

The complete list. **An animation not on this list requires a decision, not
an implementation.**

| # | Animation | Duration | Curve | Answers |
| --- | --- | --- | --- | --- |
| M-1 | Destination content cross-fade | `fast` | `move` | The screen changed |
| M-2 | Rail / tab indicator slide | `fast` | `move` | Where the selection went |
| M-3 | Popover, palette, dropdown scale-and-fade from trigger | `fast` | `enter` | This came from that |
| M-4 | Skeleton → content cross-fade | `fast` | `move` | The data arrived |
| M-5 | Row enter (fade) | `fast` | `enter` | This is new |
| M-6 | Row exit (fade, then the gap closes) | `fast` ×2 | `exit`, `move` | This left, and from where |
| M-7 | Detail rail content cross-fade | `fast` | `move` | The selection changed |
| M-8 | Modal scale-and-fade from its opener | `medium` | `enter` | This came from that button |
| M-9 | Advice / finding replacement | `medium` | `move` | The recommendation changed — slower on purpose |
| M-10 | Inline save confirmation | `fast` in, `medium` out | `enter`, `exit` | That registered |
| M-11 | Guardrail explanation fade-in | `fast` | `enter` | Something was removed, and why |
| M-12 | Commit fill | `deliberate` | linear | You are committing; you can still stop |
| M-13 | Toast enter / exit | `fast` | `enter`, `exit` | Something happened elsewhere |
| M-14 | Chain scroll to selection, **only when out of view** | `fast` | `move` | Your selection is here |
| M-15 | Pilot panel slide from the right edge | `medium` | `enter` | This came from the edge |
| M-16 | Skeleton shimmer | 1.6s loop | linear | This is still loading |
| M-17 | Hover / focus / press treatments | `instant` | `move` | That is interactive |

### 7.4 What must never animate

Explicit, because these are the temptations:

1. **Any value change.** Prices, P&L, quantities, counts, percentages,
   progress numbers. No roll-ups, no counters, no flashes, no colour
   pulses. At a ~1s push cadence an animated number is animating
   permanently.
2. **Anything, to attract attention.** No pulsing, glowing, shaking,
   bouncing or breathing. Urgency is hierarchy and position.
3. **Anything, during direct manipulation.** No transitions during a drag,
   a resize, a chart pan, a splitter move or text entry.
4. **Layout.** Panels do not reflow smoothly when content changes. Layout
   animation is the largest single source of perceived slowness.
5. **The chart canvas.** No transitions, transforms or resize observers,
   ever. Its viewport ownership is settled elsewhere and re-clamping from a
   resize observer has already been tried here and reverted, because it
   snapped a user's manual price-axis drag back mid-gesture.
6. **Success.** A placed order becomes a position. That is the
   confirmation. No celebration, no sound, no confetti — this is the exact
   point at which a trading product becomes a slot machine.
7. **Page or destination *transitions* beyond a cross-fade.** No slide
   between destinations, no push, no zoom.

### 7.5 Reduced motion

`prefers-reduced-motion` removes **movement**, never **feedback**.

| Animation | Under reduced motion |
| --- | --- |
| M-1 … M-11, M-13 … M-15, M-17 | Instant state change |
| M-12 (commit) | **Duration unchanged.** The continuous sweep becomes four discrete steps. |
| M-16 (shimmer) | Removed; a static skeleton |

A user with reduced motion enabled must never lose the ability to tell that
an action registered, and must never lose the commit gesture's timing
affordance — that is a safety mechanism, not an effect.

---

## 8. Iconography

### 8.1 Philosophy

Icons in this product are **navigational and structural**, not
decorative. An icon exists to make a repeated target findable at a glance
after the user has already learned what it means. It never introduces a
concept.

### 8.2 Construction

| Property | Value |
| --- | --- |
| Grid | 16×16, drawn on whole pixels |
| Stroke | 1.5px at 16px optical size; scales proportionally |
| Terminals | Round caps, round joins |
| Corner radius | 2px on the 16 grid |
| Fill | Stroke-only by default. Filled variants exist **only** for selected states in the nav rail |
| Optical sizes | 16 (default), 20 (touch and large text), 24 (empty states and mobile) |
| Colour | Inherits the ink of its context; icons carry no colour of their own except when adjacent to a status label |

**One set, one weight, one geometry.** A second icon set is the fastest way
for an interface to read as assembled rather than designed.

### 8.3 When an icon may replace a label

| Situation | Icon alone? |
| --- | --- |
| Nav rail at ≥1280px | **No** — label required |
| Nav rail at ≤1279px | Yes, with a tooltip and an accessible name |
| Row actions in a table (close, edit, remove) | Yes, with an accessible name naming the subject |
| Toolbar tools on the chart | Yes, with a tooltip |
| Any button that places, closes or modifies an order | **Never** |
| Any destructive action | **Never** |
| Status | **Never** — status is glyph **and** label (§6.15) |
| Empty-state actions | **Never** |
| Anything a first-time user meets before they have been taught it | **Never** |

The rule beneath the table: **an icon may replace a label only where the
consequence of a misunderstanding is a wasted click.** Where the
consequence is money, the label stays.

### 8.4 Usage rules

| # | Rule |
| --- | --- |
| IC-1 | One icon, one meaning, product-wide. An icon used for two things is two icons. |
| IC-2 | Every icon-only control has an accessible name that matches its tooltip. |
| IC-3 | Icons are never the sole carrier of state — pair with text, position or shape. |
| IC-4 | No emoji anywhere in the product interface. The current build uses emoji in the mode controls; they render differently on every platform, cannot be recoloured, ignore the type scale, and read as a consumer app. |
| IC-5 | Icons align to the text baseline optically, which is the one permitted exception to the spacing scale (§4.1). |
| IC-6 | No icon is animated. |

---

## 9. Data visualisation

### 9.1 Charts

**Colour, and the one rule that prevents a collision.** Inside a plot area
the accent is never used, so a series can never be confused with a control.
Price charts use market colours; comparison charts use the categorical
palette below.

**Price chart (candles).**

| Element | Token |
| --- | --- |
| Up candle body | `market.positive.mark` — **hollow** |
| Down candle body | `market.negative.mark` — **solid** |
| Wicks | The same colour, 1px |
| Volume histogram | The same colours at 35% opacity, on its own scale |
| Grid | `neutral.700`, 1px, recessive |
| Axis labels | `text.caption`, `neutral.400`, tabular |
| Crosshair | `neutral.400` dashed, 1px |
| Last-price label | Filled with the direction colour, ink chosen for contrast |
| Position line | `action.primary.text`, solid, labelled |
| Stop line | `market.negative.text`, dashed, labelled |
| Target line | `market.positive.text`, dashed, labelled |

**Hollow-up / solid-down is a second channel on top of hue**, so direction
survives greyscale, colour-vision deficiency and a printed bug report. It
is the same idea as the mandatory sign on a P&L value (§2.5).

**Line charts (equity, cumulative P&L).** 2px stroke. Positive-region fill
at 8% opacity beneath, negative above, only where the baseline is
meaningful. No point markers unless there are fewer than ~30 points. No
area gradient on a chart whose zero is not at the axis.

**Categorical palette — validated, not asserted.** Used only where multiple
independent series must be distinguished: backtest comparisons and
evidence breakdowns. Eight hues in a fixed order, stepped for the dark
surface.

| Slot | Hue | Dark value |
| --- | --- | --- |
| 1 | blue | `#3987E5` |
| 2 | orange | `#D95926` |
| 3 | aqua | `#199E70` |
| 4 | yellow | `#C98500` |
| 5 | magenta | `#D55181` |
| 6 | green | `#008300` |
| 7 | violet | `#9085E9` |
| 8 | red | `#E66767` |

Validated against `surface.base` (`#14161A`): every slot inside the dark
lightness band, chroma above the identity floor, all ≥3:1 against the
surface, worst adjacent colour-vision separation **ΔE 8.4**, worst
normal-vision separation **ΔE 19.3**. For chart forms where any two marks
can sit adjacent — scatter, small multiples — **only the first three slots
are validated**; beyond three, fold to "Other" or facet rather than adding
a ninth hue.

**Rules.**

| # | Rule |
| --- | --- |
| DV-1 | Hues are assigned in fixed slot order and never cycled. Colour follows the entity, never its rank — a filter that removes a series does not repaint the survivors. |
| DV-2 | **Never a dual-axis chart.** Two measures of different scale become two charts, small multiples, or an indexed common base. |
| DV-3 | Two or more series always carry a legend; four or fewer are also directly labelled, so identity is never colour alone. A single series needs no legend — the title names it. |
| DV-4 | Text in a chart wears text tokens, never the series colour. A colour swatch beside the label carries identity. |
| DV-5 | Status colours never become a series colour. |
| DV-6 | Grid and axes are recessive; the data is the only thing with weight. |
| DV-7 | Every chart has a table equivalent reachable from it. |
| DV-8 | Sequential ramps are one hue, light to dark. Diverging ramps are two hues with a neutral grey midpoint. Never a rainbow, never a hue at the midpoint. |

### 9.2 Stat tile

The metric cards on Home and the four result metrics on a backtest.

**Anatomy.**

```
  +---------------------+
  |  TODAY              |   text.micro, neutral.400
  |  +$212.40           |   text.display or text.title, market colour
  |  +2.1%   _.-'-._    |   text.body + optional sparkline
  +---------------------+
```

| Rule | Specification |
| --- | --- |
| One number per tile | A tile with two equal numbers is two tiles |
| The label is above the value | Never below; the reading order is label → value → context |
| Context line is optional but its slot is not | The tile's height does not change when context is absent |
| A statistic carries its `n` in the context line | `58%` above, `n=41` below |
| Insufficient evidence | The value slot reads `--` and the context line states the threshold: `0 of 5 trades` |
| Sparkline | Optional, ≤32px tall, no axes, no markers, single colour from the value's semantic token |
| Never interactive by default | A tile that navigates says so with a chevron |

### 9.3 Confidence visualisation

The AI-confidence bar in the watchlist, and the required-confidence tick —
one of the genuinely good pieces of self-documenting UI in the current
product, and it must survive the redesign unchanged in behaviour.

```
  QQQ   ###############|#####      71%
                       ^ required 65%
```

| Element | Token |
| --- | --- |
| Filled portion, above required | `market.positive.mark` |
| Filled portion, below required | `neutral.400` |
| Track | `surface.sunken` |
| Required tick | `status.caution`, 2px, full height, above the fill |
| Value | `text.body` tabular, right-aligned |

**Rules.** The tick is always drawn, even when the fill exceeds it. The
numeric value is always present — a bar alone is not a measurement. Below
the sample floor the bar is not drawn at all and the row reads
`not enough data`.

### 9.4 Heatmap

Used for the by-hour and by-confidence-bucket breakdowns in Research ›
Engine.

| Rule | Specification |
| --- | --- |
| Ramp | One hue, sequential, light→dark; **not** a market colour, because these cells are rates, not gains |
| Midpoint | Where the metric has a meaningful centre (a win rate around 50%), use a diverging ramp with a **neutral grey** midpoint, never a hue |
| Cell value | Always printed in the cell — a heatmap without numbers is a mood board |
| Insufficient sample | The cell renders `--` with the `n` beneath, in `neutral.400`, and is **excluded from the ramp scaling** so a sparse cell cannot distort the scale |
| Legend | Always present, with its numeric range |

### 9.5 Risk indicators

| Indicator | Treatment |
| --- | --- |
| Position size vs cap | A bar with the cap marked, like the confidence tick; over-cap fills in `status.caution` and the value states both numbers |
| Open risk as % of account | A stat tile with the number primary and the percentage as context |
| Exposure by symbol | Horizontal bars, `neutral.400` fill, percentage printed; concentration above a threshold marks that row with `status.caution` and a label |
| Distance to stop | Text, never a gauge. `$0.40 from your stop at $2.10` |

**No gauges, no dials, no speedometers, no traffic lights.** They occupy a
great deal of space to communicate one number imprecisely, and a
speedometer is the most literal possible violation of "the inspiration
should be felt rather than seen."

### 9.6 Data-visualisation states

| State | Treatment |
| --- | --- |
| Loading | Axis frame and gridlines render immediately; the plot area is a skeleton block. **Never a blank canvas** — the current product's chart renders empty for several hundred milliseconds on first entry, and that is the defect this closes. |
| Empty | The axes remain; a centred `text.body` line states what will fill it and offers one action. Never a blank panel. |
| Error | The axes remain; the message names the provider and the actual cause, with retry and diagnostics. Region-scoped — a failed chart never blanks its destination. |
| Stale | The chart renders with a marker on the last bar and a caption stating the timestamp. **A stale chart that looks live is a defect**; a stale chart that says so is useful. |
| Partial | Where some of a range is available, the available part draws and the missing part is left as visible gap with a note. Never interpolated across a gap. |

---

## 10. Accessibility

The current build has focus-visible implemented globally, respects reduced
motion, and has a large-text mode — genuinely better than most hobby
trading UIs. Against that, roughly fourteen ARIA attributes across the
whole application, no live regions, no skip link, and no keyboard path
through the order flow. **A screen-reader user cannot currently place a
trade.** That is the bar this section clears.

### 10.1 Standard

**WCAG 2.2 AA**, across every destination, both themes, all four Surface
Levels, both market palettes. The automated pass is a floor; the manual
keyboard-and-screen-reader run of the order path is the test that matters,
because "can a screen-reader user place a trade" is not a property a linter
can assert.

### 10.2 Contrast — measured

Every ratio below is computed, not estimated.

| Pair | Ratio | Requirement | Result |
| --- | --- | --- | --- |
| `ink.primary` on `surface.base` | 16.44:1 | 4.5 | Pass |
| `ink.secondary` on `surface.base` | 10.78:1 | 4.5 | Pass |
| `ink.muted` on `surface.base` | 5.64:1 | 4.5 | Pass |
| `ink.muted` on `surface.raised` | 4.67:1 | 4.5 | Pass |
| `action.primary.text` on `surface.base` | 7.52:1 | 4.5 | Pass |
| `action.primary.ink` on `action.primary.fill` | 4.70:1 | 4.5 | Pass |
| `action.primary.fill` on `surface.base` | 3.86:1 | 3.0 | Pass |
| `market.positive.text` on `surface.base` | 10.25:1 | 4.5 | Pass |
| `market.negative.text` on `surface.base` | 6.53:1 | 4.5 | Pass |
| `status.caution` on `surface.base` | 10.79:1 | 4.5 | Pass |
| `market.positive.mark` on `surface.base` | 6.03:1 | 3.0 | Pass |
| `market.negative.mark` on `surface.base` | 5.07:1 | 3.0 | Pass |
| `border.control` on `surface.base` | 3.32:1 | 3.0 | Pass |
| `focus.ring` on `surface.base` | 10.59:1 | 3.0 | Pass |
| `focus.ring` against `focus.gap` | 11.38:1 | 3.0 | Pass |
| `focus.gap` against `action.primary.fill` | 4.14:1 | 3.0 | Pass |
| `ink.disabled` on `surface.base` | 3.03:1 | exempt | Noted |

`ink.disabled` sits at 3.03:1. Disabled controls are exempt from the
contrast requirement, and this system does not lean on that exemption: a
disabled control here always carries a **reason** in text at full contrast,
so the state is legible even where the label is dim.

### 10.3 Colour vision

- No information is carried by hue alone (§2.5, C-2).
- The default market pair is measured at ΔE 6.7–8.1 under deuteranopia —
  at or below the target — and the alternate palette (ΔE 27.3) is one click
  away in Settings › Appearance.
- Charts add a second channel: hollow/solid candle bodies, line patterns
  and direct labels.
- A verification pass simulating deuteranopia, protanopia and tritanopia on
  every destination is part of the design system's exit criteria, not a
  later audit.

### 10.4 Keyboard

- Every action on the order path has a keyboard equivalent (§6.2, and
  `UI_V2_WIREFRAMES.md` §9).
- A skip-to-content link is the first focusable element.
- Focus is visible on every interactive element including custom controls,
  chain rows and chart tools.
- Focus is trapped in modals and returned to the opener.
- **Focus is never lost.** After a refresh, a row deletion or a panel
  collapse, focus moves somewhere adjacent and sensible — never to the
  document body.
- Tab order follows visual order in every region.
- Tables are one tab stop with roving focus. A 40-row chain with 40 tab
  stops is unusable.
- No shortcut fires while focus is in a text input except `Esc`.

### 10.5 Screen readers

- One `banner`, one `navigation`, one `main`, one `contentinfo`.
- Every icon-only control has an accessible name matching its tooltip.
- Tables are real tables with `scope`, captions and counts.
- Row-level accessible names are sentences, not concatenated cells:
  *"470 call, $3.90, about a 54 percent chance of finishing in the money."*
- **Prices and P&L are not in a live region.** At a ~1s cadence that would
  produce continuous, unusable speech. There is **one** polite summary
  region in the shell, announcing only meaningful events — a fill, a close,
  a stop triggered, a halt, connection lost or restored — rate-limited to
  one utterance per three seconds with coalescing.
- The commit gesture announces started, qualified and placed.
- The chart canvas has a text alternative summarising the visible window,
  with drawings and price lines enumerated in an adjacent hidden list.

### 10.6 Zoom and text size

- Type in `rem`; spacing on a scale that grows with it, so the large-text
  mode is a single root change and not a parallel token set.
- Layouts survive 200% zoom with no content loss and no horizontal page
  scroll. Wide content scrolls inside its own container.
- **No fixed-height container on text.** A container that clips at large
  text sizes is a bug, and it is the most common one this class of change
  produces.

### 10.7 Other preferences

`prefers-reduced-motion` per §7.5. `prefers-reduced-transparency`, where
available, removes translucency in favour of solid surfaces. Both are
honoured automatically **and** independently settable in Settings ›
Appearance, because an OS setting is per-machine and a person's needs are
not.

---

## 11. Mobile adaptation

Components, not layouts. Each row states what the component becomes, and
what must not change.

| Component | On mobile | Invariant |
| --- | --- | --- |
| Button | Minimum 44pt target; primary actions become full-width at the bottom of a sheet | Variants, hierarchy and the one-primary rule are unchanged |
| Commit control | Becomes the **Commit Rail** — a horizontal slide gesture, travel scaling with position size, restating cost then max loss as the thumb travels | Same semantics, same early-release cancel, same absence of celebration. A `Place order` button always exists alongside for assistive technology. |
| Input | 44pt height; numeric inputs open a numeric keypad | Label above, error below, never a placeholder label |
| Dropdown | Becomes a bottom sheet | Same selection semantics; the selected item is marked, not merely coloured |
| Segmented control | Unchanged; maximum three segments instead of four | |
| Card | Becomes a full-width tap target with a chevron | One action per card |
| Table | Becomes a **list of cards**; each row's three most consequential values become the card | Never a horizontally scrolling table. A table that must be scrolled sideways on a phone is a table that should have been cards. |
| Chart | Touch pan and pinch zoom; the toolbar becomes a single sheet | No drawing tools on mobile |
| Tooltip | **Removed.** There is no hover. | Any information that was in a tooltip becomes visible text or moves into a tap-to-open popover. This is why §6.16's rule exists. |
| Popover | Becomes a bottom sheet or an inline expansion | Same dismissal semantics |
| Modal | Becomes a full-height sheet with a grab handle | Still only two permitted uses |
| Context menu | Becomes a long-press action sheet | Every action still exists somewhere visible |
| Nav rail | Becomes a four-item bottom tab bar | Labels always visible; never icon-only |
| Command palette | Becomes a search field in the Home header | Same index |
| Toast | Becomes a top banner | Same severity ladder, same suppression rule |
| Banner | Unchanged in role; full-width below the header | |
| Status pill | Unchanged | Glyph and label both, always |
| Skeleton | Unchanged | Same 200ms delay, same fixed counts |
| Empty state | Centred rather than left-aligned; one action, full-width | Still contains a verb |
| Pilot | A header affordance and a sheet; **never a floating bubble, never a tab** | Same silence rules |

**Three rules governing every transformation:**

1. **Every gesture has a visible equivalent.** A gesture is an accelerator
   for those who have found it, never the only path.
2. **Density is looser, information is not.** Mobile has fewer *columns*,
   never fewer *facts about risk*. Max loss, position size and the "if you
   do nothing" line survive to the smallest screen.
3. **Nothing gains a state on mobile that it lacks on desktop.** If a
   component has no loading state on desktop it does not invent one here.

---

## 12. Component governance

### 12.1 The default is reuse

Before a new component is proposed, three questions, in order:

1. **Does an existing component do this with different content?** A table
   on Journal and a table on Portfolio are one component. A card holding a
   metric and a card holding a position are one component.
2. **Does an existing component do this with a new variant?** A variant is
   a change of *appearance or emphasis* within the same behaviour and
   semantics.
3. **Does an existing component do this with a new configuration?** Column
   sets, density, size, and whether an action slot is filled are
   configurations, not variants.

Only if all three are no is a new component justified.

### 12.2 When a variant is right, and when it is not

| Situation | Verdict |
| --- | --- |
| Same behaviour, same semantics, different emphasis | **Variant** (primary vs secondary button) |
| Same behaviour, different content shape | **Configuration** (a table's column set) |
| Different behaviour, same appearance | **New component** — appearance is never the reason to share |
| Different semantics, same appearance | **New component** (a badge and a status pill look alike and mean different things) |
| Same component, one screen wants it slightly different | **Neither.** Change the screen or change the component for everyone. |

That last row is the rule that protects §1.5. A per-screen exception is how
a design system dies, and it dies quietly.

### 12.3 Admitting a new component

A proposal must supply all of the following before it can be built:

1. **Purpose** in one sentence, and what it is *not*.
2. **Why the three questions in §12.1 all answered no.**
3. **All seven states** — default, hover, active, focus, disabled, loading,
   error — or an explicit statement of which do not apply and why.
4. **Both themes**, defined at the same time. Never dark first and light
   later.
5. **Keyboard interaction**, complete.
6. **Accessible semantics**: role, name, state, and how each is exposed.
7. **Motion**, drawn from §7.3's catalogue, or a justified addition to it.
8. **Mobile transformation** per §11.
9. **Tokens only** — no primitive references, no values outside the scales.
10. **A second real use case.** A component with one caller is a screen,
    not a component. Build it inline; promote it when the second caller
    appears.

Rule 10 is the most useful one. Most premature components are one screen's
layout wearing a general name.

### 12.4 Open decisions

| # | Decision | Blocks | Recommendation |
| --- | --- | --- | --- |
| 1 | **Vendored variable typeface or platform faces?** | §3.1, and how identical desktop and mobile can look | Ship on platform faces with specified metrics. Revisit once the rest of the system is in place — switching later is a token change, not a redesign, precisely because §3 specifies metrics rather than appearance. |
| 2 | **Is the alternate market palette offered during onboarding, or only in Settings?** | §2.6 | Settings only. An onboarding question about colour vision in the first minute is intrusive; Pilot can offer it once if a user ever changes the theme. |
| 3 | **Comfortable or Compact default at Surface Levels 3–4?** | §4.5 row heights | Compact at 3–4, Comfortable at 1–2, independently overridable. |
| 4 | **Does the light theme ship with V2 or after?** | §2.9 | After. Every component defines its light values at build time (§12.3 rule 4) so the theme is an assembly, not a project. |
| 5 | **Do icons get a bespoke set or an existing open-licence set?** | §8.2 | The current inline set is already consistent and geometric; extend it rather than adopting a third-party set, which would arrive with a different grid and stroke. |

### 12.5 Deprecation

A component is deprecated by being marked here with its replacement and
the date, never by being deleted. Deletion happens when no caller remains.
A deprecated component keeps working; it does not degrade, and it does not
warn the user — a user should never see evidence of the team's internal
housekeeping.

### 12.6 The health checks

The design system is verified by the same standard as the code, and these
run in the browser-check suite alongside the existing gates:

| Check | Assertion |
| --- | --- |
| Token audit | Zero component references to a primitive token |
| Scale audit | Zero spacing values outside `space.*`; ≤9 distinct font sizes, all from `text.*` |
| Contrast | Every semantic pair passes its target in both themes, all four Surface Levels, both market palettes |
| Colour-vision | Every destination passes a deuteranopia / protanopia / tritanopia simulation |
| Colour-alone | No gain/loss, status or selection state encoded by hue only |
| Motion | Zero animations outside §7.3's catalogue; zero transitions on any value-bearing element; zero transitions on the chart canvas |
| Component inventory | Every component renders in every state, both themes, on one page |
| Accessible names | Zero interactive elements without one |
| Live regions | Exactly one polite region in the shell; zero live regions on price or P&L |

---

## 13. What changes from the current build

For the implementer, the delta — because most of this is an evolution of
tokens that already exist and are already reasonable.

| Today | Becomes | Why |
| --- | --- | --- |
| 4 surface values, ad hoc | 12-step neutral ramp with defined roles | Elevation needs steps, and borders need three distinct jobs |
| `--accent` used for primary, active nav, focus and links | Four distinct semantic tokens over the same ramp | A focus ring that is a lighter accent is indistinguishable from "this is primary" — measured at 2.75:1 on the accent fill |
| `--good` / `--bad` single values | Text / mark / tint / fill tiers per market direction | Text needs 4.5:1; chart marks need the perceptual lightness band. One value cannot do both. |
| No colour-vision alternate | A validated first-class alternate that moves the accent with it | Default green/red measures ΔE 6.7–8.1 under deuteranopia |
| ~14 hardcoded font sizes | 9 roles on a ratio scale, in `rem` | Large-text mode becomes one root change instead of a parallel token set |
| No spacing scale | 8 purpose-named steps on a 4px base | So a gap expresses a relationship rather than a measurement |
| Emoji in mode controls | Icons from the one set, with labels | Emoji ignore the type scale, cannot be recoloured, and render differently on every platform |
| 2 timing tokens | 4 durations, 3 curves, a closed catalogue of 17 animations | So "should this animate" has an answer |
| Focus ring, single | Dual ring with a gap | Passes on saturated fills, where a single ring fails |
| Blank canvas while a chart loads | Axis frame plus a plot skeleton | A reproduced defect, closed by specification |
| P&L in colour with a sign | P&L with sign, glyph and stable position, colour last | Because the measurement says colour cannot be trusted to carry it |

---

## 14. Related documents

| Document | Relationship |
| --- | --- |
| `UI_V2_DESIGN.md` | The philosophy this expresses visually. Authority for principles, personas, Pilot, the roadmap and success metrics. |
| `UI_V2_WIREFRAMES.md` | The layouts these components fill. Authority for where each component appears and in which state. |
| `CLAUDE.md` | Binding constraints — paper-only, no build step, no CDN, the traps this system's loading, error and chart rules are built around. |
| `ONBOARDING.md` | The contextual-help layer whose tooltips, glossary and guardrail messages wear these tokens. |
| `TRADING_INTELLIGENCE.md` | The evidence rules §1.4, §9.2 and §9.3 render. |
| `MARKET_DATA.md` | The typed error vocabulary §9.6's error states must not flatten. |
| `ARCHITECTURE-MOBILE.md` | The hosting decisions §11 depends on. |
