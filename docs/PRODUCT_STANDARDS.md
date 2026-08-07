# PRODUCT_STANDARDS.md — permanent engineering standards

**Status:** normative. **Owner:** the project, not a milestone.

This document exists because the alternative is worse. Milestones make
implementation decisions under time pressure, each one reasonable in isolation,
and after six of them the application holds six answers to the same question.
This codebase has paid for that four times already — provider health tracked in
two objects (V0.5.3), the settings ranking derived twice (V0.5.7), the guide
catalogue duplicated (V0.6.1), and days-to-expiry computed in two languages with
two answers on one screen (M4). Every one was invisible in review and obvious in
hindsight.

**How to use it.** Before implementing anything this document covers, read the
relevant section. If you are about to do something it forbids, you have either
found a case it did not anticipate — in which case amend it in the same commit,
with the reasoning — or you are about to reintroduce a defect. If the answer to
a question is not here and not in one of the frozen design documents, that is a
gap: record it rather than resolving it silently.

**What it is not.** Not a roadmap (`ROADMAP.md`, `ROADMAP-UI-V2.md`), not visual
design (`DESIGN_SYSTEM_V2.md`), not market-data architecture (`MARKET_DATA.md`).
It is the set of promises the product makes about *correctness and feel* that no
single milestone owns.

---

## 1. Chart accuracy

**A chart is a measurement instrument. Its only job is to be true.**

Everything else about a chart — how it looks, how smoothly it moves, what tools
sit on top of it — is subordinate to this. A chart that is beautiful and wrong
is worse than no chart, because the user acts on it. This is the same principle
`intelligence/` follows when it refuses to score a metric it cannot evidence and
`services/review.py` follows when it declines to quote a mid the engine will not
fill at.

### 1.1 The guarantees

| # | Guarantee | Status |
| --- | --- | --- |
| G1 | The renderer never fabricates a price. Every value drawn came from a provider. | **Enforced** (§1.2) |
| G2 | A candle's OHLC is internally consistent: `low ≤ min(open, close)` and `max(open, close) ≤ high`. | **NOT enforced** (§1.3) |
| G3 | Bars are strictly ordered in time, with no duplicate timestamps. | **Enforced** (§1.2) |
| G4 | Every drawn value is finite and positive. | **Enforced** (§1.2) |
| G5 | Except across genuine session gaps, a bar opens at the prior bar's close. | **Provider property** (§1.4) |
| G6 | Once established, a forming bar's `open` never changes; `high` is non-decreasing; `low` is non-increasing; `close` moves freely until the bar completes. | **Provider property** (§1.4) |
| G7 | No animation may show a price the data does not contain. | **Enforced by absence** (§2) |

### 1.2 Where the guarantees are enforced today

**`optionspilot/data/base.py::validate_candles`** is the one boundary every
provider's bars cross. It enforces G3 and G4: non-finite and non-positive OHLC
rows are dropped, `NaT` index entries are dropped, duplicate timestamps are
collapsed keeping the last, and the frame is sorted. Every removal is logged
with its `context`, so a chart that lost bars is explainable from `data.log`.

**`optionspilot/ui/static/index.html`, the chart module** enforces G1 by
construction: bars go from `CH.data.candles` to `series.update()` and
`setData()` unchanged. Audited in M4 — there is no `requestAnimationFrame`
loop, no interpolation, no tweening and no random source anywhere in the chart
path. The display-side sanitiser (`chBarUsable`) additionally rejects bars whose
*values* are null, because lightweight-charts treats a null-OHLC bar as
whitespace and reports the render as a success.

**`scripts/chart_check.py`** asserts the rendered outcome rather than the
payload: 65 regressions including that the candles in the visible time window
must intersect the visible *price* window.

**The evidence for those claims is `docs/CHART_CERTIFICATION.md`**, the V0.5.5
end-to-end audit of the pipeline from provider to pixels. That document is a
dated report — what was proven, what was fixed, what remained broken — and this
section is the *standing rule* it produced. When they disagree, this document
is the rule and that one is the history; if a claim here is contradicted by
measurement, fix the claim.

### 1.3 The validator gap (found in the M4 audit, not yet closed)

**G2 is not enforced anywhere.** `validate_candles` checks that OHLC values are
finite and positive; it never checks that they are *consistent with each other*.
A provider bar with `high < close` — which yfinance emits intermittently on the
in-progress bar — passes validation, reaches the renderer, and draws a candle
whose body extends past its own wick. The payload is "valid", the console is
clean, and the picture is impossible.

This is the same shape as every other defect in this file's opening paragraph:
each individual check is correct, and the invariant that matters sits between
them.

**The fix, when it is taken:** in `validate_candles`, after the finiteness
screen, drop rows where `low > min(open, close)` or `high < max(open, close)`,
logged with `context` exactly like the existing removals. It is roughly four
lines. It was deliberately *not* taken during M4 because that function is the
market-data layer's single most load-bearing boundary and `MARKET_DATA.md`
§1–2 must be read first; a UI milestone is the wrong place to change what the
engine sees.

**Do not "fix" this in the renderer.** Clamping a bad bar at draw time would
make the chart lie in a new way — the engine would still be trading on the
inconsistent values, and the two would disagree.

### 1.4 What is a provider property, not ours

G5 and G6 describe the *data*, not the renderer. Intraday bars from a real
venue do open at or very near the prior close, and a forming bar's high and low
are monotone by definition — but this is a property of the feed, and OptionsPilot
cannot manufacture it. Two honest consequences:

* **Gaps are real and must be drawn.** Overnight gaps, halts and session
  boundaries produce a genuine discontinuity. Smoothing one away would be
  fabrication.
* **A provider that violates G6 is a data defect to be detected and reported,
  never corrected in place.** If a refresh returns a forming bar whose `open`
  has moved, the right response is a validation failure with a named provider —
  the machinery for that already exists in `data/health.py`.

---

## 2. Live market motion

**The goal is a living market, not an animated one.**

The distinction is whether motion carries information. A price that moves
because the price moved is alive. A price that eases, pulses, or glides because
easing looks expensive is decoration, and on a trading screen decoration is
noise competing with signal.

### 2.1 Principles

1. **Truthful.** Every frame of movement corresponds to data the application
   actually received. If the feed did not tick, the chart does not move.
2. **No fabricated ticks — ever.** Do not synthesise intermediate prices to make
   a quiet market look busy. This is the single hardest rule here to break by
   accident and the easiest to break deliberately, because it always looks
   better in a demo.
3. **Interpolation only when mathematically honest.** Animating a *rendered
   position* between two real values is legitimate — the eye is being helped to
   follow a change that genuinely occurred. Inventing a *price* between two real
   prices is not. The test: if a user read the number off mid-animation, would
   it be a price the market traded at? If no, the animation may move the mark
   but must not move the number.
4. **Subtle.** Motion is measured in a few hundred milliseconds and a few
   pixels. `DESIGN_SYSTEM_V2.md` §7's closed catalogue is the authority; do not
   add a duration or an easing to the chart that is not in it.
5. **Never at the cost of position.** A moving element must not shift layout.
   `CLAUDE.md` records what a resize-triggered re-clamp cost this project.
6. **Restraint scales with consequence.** The order ticket and the review modal
   are the least animated surfaces in the product, deliberately. The one
   sanctioned exception is the hold-to-confirm fill indicator (§6.6 of
   `UI_V2_DESIGN.md`), where the motion *is* the information.

### 2.2 Reduced motion

Both channels — the OS preference and the in-app toggle — route through one
`html.gd-nomotion` class. Any new motion must be reachable by it, and
`scripts/motion_check.py` verifies this rather than trusting it.

---

## 3. Options presentation

The chain is where a beginner learns the instrument and an experienced trader
works fastest. §1.4 of `UI_V2_DESIGN.md` forbids bifurcating them into two
screens, so the standard is: **one set of numbers, one set of conventions,
density varying by Surface Level.**

### 3.1 Days to expiry — the convention is normative

**DTE is a calendar-day difference between today's date and the expiration
date.** Today is `0`, tomorrow is `1`, three days out is `3`. It is not a
duration, not a rounding of hours remaining, and not affected by the time of
day or the user's timezone.

| Situation | Label | Plain language |
| --- | --- | --- |
| Expires today | `0DTE` | Today |
| Expires tomorrow | `1DTE` | Tomorrow |
| n days out | `{n}DTE` | `{n} days` |
| Already expired | `Expired` | `{n} days ago` |

Both registers are always available — the abbreviation on the control, the plain
wording in its accessible name or title. That is §1.4's anti-bifurcation rule in
its smallest form: the trader's vocabulary and the beginner's are the same
screen, not two.

**One owner: `optionspilot/services/expiry.py`.** The rule is Python, pure and
unit-tested. Do not recompute DTE in JavaScript. The M4 audit found the client
doing exactly that and reading one day high at every hour of every trading day
except after 16:00 on expiration day, while the correct figure sat beside it on
the same screen.

**A past expiry is never clamped to zero.** Clamping is what let a dead contract
render as though it expired today.

### 3.2 Prices

| Field | Standard |
| --- | --- |
| **Bid / Ask** | Shown as the provider gave them. Never averaged into a single number when both are known. |
| **Mark / Mid** | Labelled as an estimate, never presented as a tradeable price. |
| **The price an order will fill at** | **Never the mid.** `PaperBroker` fills a market buy at `ask × (1 + slippage_pct)` and a market sell at `bid × (1 − slippage_pct)`. Any screen that states a cost states *that* number — `services/review.py::estimate_premium` is the single implementation, pinned by test against the broker's own source. |
| **A missing quote** | Produces an absent number with a stated reason, never a fallback to a different field. |

The general rule, which is this codebase's oldest: **a confidently wrong number
is worse than an absent one**, because the user acts on it.

### 3.3 Greeks and IV

* Greeks are shown to three decimals, IV as a percentage to one.
* **Provenance must be visible when the greeks are derived rather than
  supplied.** `analysis/options_metrics.enrich_greeks` computes them when a
  provider returns zeros; a screen showing a computed delta beside a provider
  delta with no distinction is stating a model output as a market observation.
  *This is not currently surfaced in the UI and is recorded as debt in §11.*
* A greek that cannot be computed is blank with a reason. Never `0.000`, which
  is a legitimate value.

### 3.4 Expiration formatting

Date and DTE are two different facts and read as two: `12 Sep` and `7DTE`, not
`12 Sep (7)`. Beginners need the second and cannot derive it; traders scan for
it. Neither is a parenthetical of the other.

---

## 4. Countdown timers

**A countdown is a promise about time. It may only ever count down.**

### 4.1 The standard

1. **Monotonic.** Between two consecutive renders the displayed remaining time
   never increases, with exactly one legitimate exception: the period it counts
   down to has genuinely completed and a new one has begun. That is a *reset*,
   and it is a full-duration jump, never a small one.
2. **Anchored to the exchange, not the machine.** Bar boundaries are properties
   of the trading session. A US intraday session starts at 09:30 ET, so hourly
   bars break at :30 past — deriving a boundary from `floor(now / 3600)` is
   wrong by thirty minutes and looks right.
3. **Stable across live updates.** A poll that replaces the bar array must not
   move the countdown. This is the rule the current implementation cannot
   guarantee (§4.2).
4. **Never negative, and never zero for more than one tick.**

### 4.2 Current status: investigated, root cause not confirmed

`chUpdateTimer` derives the boundary as `lastBar.time + timeframeDuration`, then
advances in whole-duration steps past `now`. This is monotonic *while
`lastBar.time` is stable* — and `lastBar.time` is provider data, which this
repository documents as movable in at least three ways: Yahoo's 30-minute
closing stub bar (so `time + 3600` lands off-boundary on an hourly frame),
out-of-order payloads, and window changes that alter which bar is last.

A reported symptom of `0:58 → 1:02 → 0:57` — a *small* backward step rather
than a full reset — is consistent with the derived boundary moving, but it was
not reproduced offline and **no root cause is claimed**.

### 4.3 Proposed architecture

Do not chase the cause. Make the property structural:

> Remember the boundary currently being counted down to. Recompute it each
> tick, but **accept the new value only if it is later than the remembered
> one.** An earlier value means the data moved backwards, and the previous
> boundary is the better estimate.

This makes G-monotonicity true by construction regardless of which upstream
behaviour caused the movement, preserves the correct full-duration reset when a
bar genuinely completes, and needs no knowledge of session structure beyond what
the current code already has. Roughly six lines, plus an assertion in
`scripts/chart_check.py` that samples the timer across a simulated bar rollover
and fails on any decrease-then-increase.

---

## 5. Chart customisation — the long-term vision

None of this is implemented. It is recorded so that today's architecture does
not foreclose it.

### 5.1 The intended surface

| Area | Intended |
| --- | --- |
| **Drawing** | Freehand pen, arrows, text labels, shapes (rectangle, ellipse, ray, trend line), eraser, per-object styling, multi-select |
| **Appearance** | Chart themes; candle body and wick colours independently; hollow candles; bar, line, area, Heikin-Ashi and baseline styles |
| **Persistence** | Saved templates — an appearance set and an indicator set, named and re-applicable across symbols |

### 5.2 What today's architecture must preserve

The chart already carries a **drawing overlay** rendered on its own canvas above
lightweight-charts, synchronised through `chDrawRender` on every viewport move.
That is the correct shape and must be kept, for one architectural reason worth
stating plainly: **lightweight-charts is a rendering library, not a drawing
surface.** Its primitives API is limited and version-coupled; owning the overlay
means the drawing model is ours.

Four constraints for anyone touching the chart before that work lands:

1. **Drawings are model objects, not pixels.** Persist them in chart
   coordinates (time, price), never screen coordinates. A drawing must survive a
   zoom, a resize and a reload.
2. **One viewport owner.** `chMoveViewport` / `chClampViewport` already hold
   the invariants; a drawing tool must not move the viewport by another path.
3. **The overlay must stay independent of the series style.** A hollow-candle
   or Heikin-Ashi mode must not require the overlay to know about it.
4. **Do not adopt library-specific persistence.** Anything saved must be
   readable if lightweight-charts is replaced.

### 5.3 The theming constraint

Candle colours currently come from `CH_COLORS`, resolved from design tokens.
User customisation must **override the token values, not bypass the token
layer** — otherwise `token_check.py`'s contrast floors stop applying to the one
surface where colour carries meaning (up/down). A user-chosen palette should be
validated against the same floors and refused, with a reason, if it fails.

---

## 6. Indicators — the future system

### 6.1 Intended

* **Built-in**, extending the current set, computed by the same `analysis/`
  functions the engine uses — never a second implementation for display.
* **User-created**, defined declaratively (inputs, a computation over OHLCV,
  outputs and their rendering) rather than as executed code.
* **Managed** — add, remove, reorder, configure, per-pane or overlaid.
* **Persistent** — the indicator set is part of the workspace, server-owned.

### 6.2 Architectural goals

1. **One computation, two consumers.** An indicator the user sees and an
   indicator the engine scores on must be the same function. This is why
   `analysis/` is pure and I/O-free, and it is the single most important
   constraint here.
2. **Declarative, not executable.** A user-defined indicator that is a *script*
   is a code-execution surface inside a trading app. A declarative spec
   evaluated by a fixed interpreter is not. Take the second.
3. **Server-owned state.** Per `ROADMAP-UI-V2.md` R-8, the indicator set lives
   in `RuntimeSettings`, with `localStorage` at most a synchronous fast path.
4. **Panes are layout, indicators are data.** Keep the two separable, or
   multi-chart layouts (§5.1) become impossible.

---

## 7. Replay — the future milestone

**What it is:** stepping historical data forward candle by candle, with the full
application reacting as though it were live — including paper trading against
the replayed bar.

**Intended controls:** play / pause, speed (0.5×–60×), step one bar, jump to a
date, resume to live.

**Why it belongs to this product specifically:** OptionsPilot already has a
deterministic engine, a paper broker, a journal and a coach. Replay is the
feature that turns all four into a practice environment, and it is worth more
here than in a charting tool because the coach can review a replayed session
exactly as it reviews a real one.

**Architectural requirements:**

1. **A clock seam.** Every component that asks "what time is it" must ask an
   injected clock. `TradingService`, `PortfolioService` and the orchestrator
   already take one; anything new must too. Replay is that seam, driven.
2. **The engine must not know.** If replay requires an `if replaying:` branch
   in `engine/` or `risk/`, the seam is in the wrong place and the results are
   not comparable to live ones.
3. **Replayed fills are marked in the journal**, permanently and structurally.
   A practice trade that can be mistaken for a real one corrupts every statistic
   `intelligence/` computes — and that subsystem's whole value is that its
   numbers are trustworthy.

---

## 8. Professional UX principles

These are the qualities the interface must express, stated so a reviewer can
name what is wrong rather than only feel it.

| Quality | What it means concretely |
| --- | --- |
| **Accuracy** | Every number is either right or absent with a reason. Never a plausible placeholder. |
| **Clarity** | One region answers one question. A panel with two competing actions is two panels. |
| **Hierarchy** | Rank comes from spacing, typography, elevation and alignment — never from bright colour, borders on everything, or icons. At most one focal region per destination. |
| **Confidence** | The interface states things plainly. No hedging copy, no apologetic empty states, no exclamation marks. |
| **Restraint** | Decoration that carries no information is removed. Whitespace is a feature. |
| **Consistency** | A pattern that exists is reused. A second way of doing something already done is a defect, not a variation. |

**The three standing prohibitions:** visual clutter, unnecessary motion, and
decoration that does not communicate. `DESIGN_SYSTEM_V2.md` §6.6 (the three
instrument tiers) and §5.5–5.7 (the continuous left column, the shared seam, and
the rule that visibility may only ever *hide*) are the concrete expressions.

---

## 9. Licensing and attribution

### 9.1 Findings

* **Library:** TradingView Lightweight Charts™ v4.2.3, Apache-2.0, vendored at
  `optionspilot/ui/static/lightweight-charts.js`, unmodified.
* **The logo appears** because `layout.attributionLogo` defaults to `true` and
  the application does not set it. It is a documented, supported option.
* **Apache-2.0 does not require a runtime visual mark.** Its §4 conditions
  concern retaining notices in redistributed source and any NOTICE file.

### 9.2 Decision

**Keep the attribution logo.** It is not required by the licence text, but
TradingView provides the library free and asks for attribution; displaying one
small mark is cheap and being wrong about a vendor's terms is not. **Removing
it is not a cleanup task.** If it is ever removed, that is a deliberate decision
taken against TradingView's then-current terms and recorded in
`THIRD_PARTY_NOTICES.md`.

### 9.3 Documents that should exist

| Document | Location | Status |
| --- | --- | --- |
| `LICENSE` | repo root | Exists — OptionsPilot's own terms |
| `THIRD_PARTY_NOTICES.md` | **repo root** | **Added.** Every shipped third-party component, its copyright, its licence in full, and any modifications |
| Apache-2.0 full text | inside `THIRD_PARTY_NOTICES.md` | **Added**, verbatim |

**Why the repo root**, not `docs/`: a notices file is a legal artefact that
travels with the distribution, and a recipient looks for it beside `LICENSE`.
For the same reason it should be included in the packaged application — recorded
as debt in §11, since `scripts/build_exe.ps1` does not currently ship it.

**The maintenance rule:** any commit that vendors a new asset adds its entry in
the same commit. A notices file that over-claims is harmless; one that
under-claims is a compliance defect.

---

## 10. Where future work belongs

| Work | Recommended home | Why |
| --- | --- | --- |
| **Validator gap (G2)** | Its own commit, before M5 | It is a correctness bug in the engine's input, not a UI task. Small, isolated, and `MARKET_DATA.md` must be read first. |
| **Countdown monotonicity** | With the validator fix | Same area, same reviewer, both need `chart_check` assertions. |
| **Greek provenance (§3.3)** | M4-C5, if the chain is being rebuilt anyway | It is a chain-presentation decision and the chain is being rebuilt. |
| **Chart engine polish** | **M9** (Polish) | It is quality work on a surface that is not changing shape. Doing it before M5–M6 would polish a chart that later milestones still reposition. |
| **Drawing tools** | **A new M10**, after 1.0 | Large enough to be its own milestone and independent of every destination. |
| **Appearance customisation** | **M10**, with drawing tools | Shares the persistence and template model; splitting them means building it twice. |
| **Custom indicators** | **M11** | Depends on the declarative-spec decision (§6.2) and on the workspace persistence M10 establishes. |
| **Replay mode** | **M12** | Depends on the clock seam being complete and on the journal marking replayed fills — both cheaper once the destinations are stable. |

**The ordering principle:** finish the *shape* of the application (M4–M7), make
it trustworthy (M8–M9), then make it powerful (M10–M12). Chart features are the
most visible work available and the most tempting to take early; taken early
they get rebuilt.

---

## 11. Technical debt this document records

| # | Debt | Section |
| --- | --- | --- |
| D1 | `validate_candles` does not enforce OHLC internal consistency | §1.3 |
| D2 | Countdown can move backward when provider bar timestamps shift | §4.2 |
| D3 | Derived greeks are not distinguished from provider greeks in the UI | §3.3 |
| D4 | `THIRD_PARTY_NOTICES.md` is not shipped in the packaged application | §9.3 |
| D5 | Chart candle colours are not user-customisable, and the token-layer constraint that must govern it is unimplemented | §5.3 |

---

## Related documents

| Document | Covers |
| --- | --- |
| `AI_CONTEXT.md` | Vision, philosophy, the never-change list |
| `DESIGN_SYSTEM_V2.md` | Tokens, components, motion catalogue, layout rules |
| `UI_V2_DESIGN.md` | The Flight Deck direction, per-destination intent |
| `MARKET_DATA.md` | Provider architecture, failover, caching, validation |
| `CHART_CERTIFICATION.md` | The V0.5.5 end-to-end chart audit — the evidence behind §1.2, and a worked example of the standard this document sets |
| `ARCHITECTURE.md` / `ARCHITECTURE-PLATFORM.md` | Layering and the service boundary |
| `ROADMAP-UI-V2.md` | The milestone plan and its commit map |
