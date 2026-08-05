# ROADMAP-UI-V2.md — implementation plan for the UI V2 redesign

**Status:** proposed plan, not started. **Version:** 1.0.
**Design inputs (frozen):** `UI_V2_DESIGN.md`, `UI_V2_WIREFRAMES.md`,
`DESIGN_SYSTEM_V2.md`, `UI_V2_VISUAL_EXPLORATION.md`.

This is an engineering plan. It contains no design decisions; where a
question of appearance or behaviour arises it is answered by citation, not
by judgement. If a decision is genuinely missing, that is a gap in the
frozen documents and §11 records it rather than resolving it here.

**What it is.** Ten milestones, each independently shippable and
independently releasable, broken into ~85 commits sized like this
repository's existing ones. Every commit leaves the application working,
every milestone leaves it releasable, and no milestone requires the next
one to exist to be useful.

**What it is not.** A schedule. Sizes are relative (S/M/L/XL) on the scale
this repository has already demonstrated across V0.9.0–V0.9.2; dates are
deliberately absent.

---

## 1. The rules every commit obeys

These are the repository's existing rules, restated because this programme
will test all of them.

| # | Rule |
| --- | --- |
| R-1 | **The full suite is green.** `.\scripts\verify.ps1` passes all gates before a commit is considered done. |
| R-2 | **No commit ships UI without its check.** `index.html` has no automated coverage; the check scripts are the only thing between a UI change and a silent regression. A commit that adds a surface adds assertions for it. |
| R-3 | **Behaviour does not change.** This is a presentation programme. A commit that finds itself needing to change a gate, a fill rule, a risk calculation or an order path has found a bug or a scope error — it stops and escalates rather than absorbing it. |
| R-4 | **The application works at every commit**, not merely at every milestone. A half-migrated destination is behind a flag or is not committed. |
| R-5 | **Documentation is updated in the same session as the code** (`CLAUDE.md`), including the commit map in §12 of this file. |
| R-6 | **No new design decisions.** If the answer is not in one of the four frozen documents, §11 gets a row and the work stops at that boundary. |
| R-7 | **Paper-only is untouched.** No commit in this programme goes near `broker/registry.py`, the double-gate, or anything that could place a real order. |
| R-8 | **Client state is server-owned.** New UI state goes to `RuntimeSettings`, never to `localStorage` as its home. `localStorage` remains legal only as a synchronous fast path over a server-owned value. |

**Definition of done, per commit:** suite green · `verify.ps1` green ·
new/updated check assertions present · docs updated · commit-map row
updated in the same commit · commit message in the repository's existing
format.

**Definition of done, per milestone:** all of the above, plus a manual pass
in a real browser (`.\scripts\dev.ps1`) of every screen the milestone
touched, plus a release via `.\scripts\release.ps1`.

---

## 2. The migration strategy

The hard problem is not the design; it is that `optionspilot/ui/static/index.html`
is a single 8,253-line file with no build step, the whole application lives
in it, and the redesign changes its navigation, its layout and its visual
language simultaneously. A naive rewrite is a months-long branch that
cannot be reviewed, cannot be released, and cannot be tested until it is
finished.

**The strategy that avoids that: separate the shell from the content, and
migrate them on different schedules.**

```
   Stage 1   old shell  +  old content      <- today
   Stage 2   NEW shell  +  old content      <- M2, one commit flips it
   Stage 3   NEW shell  +  content migrated one destination at a time
   Stage 4   NEW shell  +  new content, legacy deleted
```

The insight that makes it work: **the new navigation can host the existing
section markup unchanged.** The six destinations map onto the nine existing
sections without touching them:

| New destination | Initially hosts |
| --- | --- |
| Home | `#tab-dashboard` |
| Trade | `#tab-trade` |
| Portfolio | A thin composition of existing positions/orders/history markup |
| Research | `#tab-charts`, `#tab-backtest`, `#tab-watchlist`, `#tab-learning` as sections |
| Journal | `#tab-journal`, `#tab-coach` as sections |
| Settings | `#tab-settings`, regrouped |

So M2 ships a genuinely better navigation over content nobody has touched —
which is reviewable, testable, releasable, and reversible by a flag. Every
milestone after it rebuilds exactly one destination's interior, deletes the
legacy markup it replaced, and ships.

**Consequence to accept deliberately:** between M2 and M6 the file holds
both old and new markup and grows by roughly 50–60%. §9-R3 schedules its
retirement and §6-M6 introduces the ratchet that forces it.

### 2.1 The flag lifecycle

One flag, `ui.shell_v2`, in `RuntimeSettings` — live-editable, because it
is the rollback path and a restart-gated rollback is not a rollback.

| Milestone | State |
| --- | --- |
| M2-C1 | Introduced, default **off** |
| M2-C11 | Default **on**, old navigation reachable by toggling off |
| M3-C10 | Old navigation **deleted**; the flag now only guards content that has not yet been rebuilt |
| M9-C7 | Flag and its branches **removed** |

`UI_V2_DESIGN.md` §16 Phase 2 requires the old navigation to remain
available for one release. M2 ships it; M3 removes it. That is the one
release.

---

## 3. Milestone overview

| # | Version | Milestone | Size | Ships |
| --- | --- | --- | --- | --- |
| M0 | 0.10.0 | Foundation — tokens, scales, static gates | M | Nothing visible. Two new gates. |
| M1 | 0.11.0 | Workspace context and Surface Level | M | One symbol context across the whole app |
| M2 | 0.12.0 | The shell — frame, rail, strip, palette | L | New navigation over old content |
| M3 | 0.13.0 | Home | M | The first Flight Deck destination |
| M4 | 0.14.0 | Trade | L | The unified workspace and the commit gesture |
| M5 | 0.15.0 | Portfolio and Journal | L | Two destinations, coach absorbed |
| M6 | 0.16.0 | Research and Settings | M | The last legacy markup deleted |
| M7 | 0.17.0 | Onboarding and Surface Levels applied | M | Under-60s first run |
| M8 | 0.18.0 | Pilot v1 (deterministic) | L | The mentor layer |
| M9 | 1.0.0 | Polish, accessibility, pop-outs, legacy removal | L | 1.0 |

### 3.1 Dependencies

```
  M0 Foundation
   |
   +-- M1 Workspace context
        |
        +-- M2 Shell
             |
             +-- M3 Home ----------+
             +-- M4 Trade ---------+
             +-- M5 Portfolio/Jrnl |
             +-- M6 Research/Setts |
                                   |
                        M7 Onboarding + Levels
                                   |
                             M8 Pilot v1
                                   |
                        M9 Polish + legacy removal
```

M3–M6 are mutually independent and may be reordered or run in parallel by
different sessions. **If resources force a choice, M4 (Trade) outranks M3
(Home)** — `UI_V2_DESIGN.md` §16 says so explicitly, and Trade is the
workflow where cognitive load does financial damage.

---

## 4. New backend surfaces this programme needs

Everything the redesign needs from the service layer, so it can be built
and tested with `pytest` before any pixel depends on it. All of it obeys
the `services/` rules: injected duck-typed collaborators, frozen view
models of primitives, no `ui` import, no transport package.

| Surface | Home | Needed by | Notes |
| --- | --- | --- | --- |
| Open risk (dollars + % of account) | `services/portfolio.py` | M3 | New figure; no current screen states it |
| Status-line view model | `services/` (new module) | M3 | Eight cases enumerated in `UI_V2_WIREFRAMES.md` §2.3; one test per case |
| Workspace symbol / timeframe / selection | `services/workspace.py` | M1 | Extends the existing document; merge semantics already exist |
| Surface Level | `config/runtime.py` | M1 | A third mode-like axis; §9-R6 guards the coupling risk |
| Quick-pick intent resolution | `services/contracts.py` | M4 | ATM call/put, 30-day, weekly → a concrete contract |
| Review view model | `services/trading.py` | M4 | Cost, max loss, breakeven, size %, the "if you do nothing" sentence |
| Portfolio composition | `services/portfolio.py` | M5 | Positions + working + closed-today + exposure in one view model |
| Notification read state | `services/notifications.py` | M2 | Persisted, per-entry, shared with a future mobile client |
| Pilot composition | `services/pilot.py` (new) | M8 | Deterministic; composes glossary, guide, intelligence, coach, gate reasoning |

**None of these is a new capability.** Every one is a re-presentation of
something the engine already computes, which is what keeps R-3 true.

---

## 5. Test and gate plan

`verify.ps1` runs 13 gates today. This programme adds seven and updates
six.

### 5.1 New gates

| Gate | Milestone | Asserts |
| --- | --- | --- |
| `scripts/token_check.py` | M0 | Zero component references to a primitive token; zero spacing values off the scale; ≤9 distinct font sizes; every semantic token defined in both themes. Static — no browser. |
| `scripts/motion_check.py` | M0 | Zero animations outside the catalogue of 17; zero transitions on a value-bearing element; zero transitions, transforms or observers on the chart canvas. Static. |
| `scripts/shell_check.py` | M2 | Palette ranking and the MOVED aliases; symbol jump sets one context; Flight Status leaves the two mode axes orthogonal; inbox read state survives restart; no fact appears in both Flight Status and the system strip. |
| `scripts/home_check.py` | M3 | No vertical scroll for bands 1–2 at 1920×1080 and 1440×900; every empty region contains a verb; a 2-trade history renders coverage reasons rather than numbers; the DOM order matches the eye-path targets. |
| `scripts/trade_check.py` | M4 | The six-action keyboard path completes with no mouse; the chain is spot-anchored on load; the ticket renders all five states; guardrails name what changed and what to do; `OrderManager` still refuses the three impossible combinations. |
| `scripts/a11y_check.py` | M9 | Contrast in both themes at all four Surface Levels and both market palettes; zero interactive elements without an accessible name; deuteranopia/protanopia/tritanopia simulation on every destination; exactly one polite live region; zero live regions on price or P&L. |
| `scripts/shape_check.py` | M9 | The freeze's shape invariant: every destination's loading, empty, populated and failed states produce identical instrument geometry. |

Portfolio, Journal, Research and Settings assertions are added to
`browser_check.py` rather than getting their own scripts — the suite count
is already high and those destinations are less interaction-dense than
Trade.

### 5.2 Existing gates to update

Measured today: **38 `data-tab` selector references across the six browser
suites, and 18 more inside `index.html`'s guided tour.** All of them are
coupled to the nine-tab navigation and all break at M2.

| File | `data-tab` refs | Milestone | Work |
| --- | --- | --- | --- |
| `scripts/guide_check.py` | 23 | M2 | The largest single migration cost in the programme |
| `scripts/intelligence_check.py` | 4 | M2 | Retarget to Journal › Progress |
| `scripts/workspace_check.py` | 4 | M1–M2 | Extend with the one-symbol-context test |
| `scripts/chart_check.py` | 3 | M2, M4 | Retarget; the 65 chart assertions themselves are unaffected |
| `scripts/marketdata_check.py` | 3 | M2, M6 | Retarget to Settings › Data |
| `scripts/browser_check.py` | 1 | M2 | Retarget; extend to six destinations |
| `optionspilot/ui/static/index.html` | 18 | M2 | Guided-tour step targets. **Tutorial ids do not change**, so `tests/test_guide.py::TestCatalogueContract` keeps passing in both directions. |

**Mitigation, and it is a commit in M2:** introduce one selector helper in
each suite that resolves a destination by name and reads the flag, so the
retargeting happens once per file rather than once per reference, and so
both navigations remain drivable during the overlap.

---

## 6. Milestone detail

Each row is one commit. Sizes: S (a sitting), M (a session), L (a long
session), XL (a session that should probably be split).

### M0 — V0.10.0 · Foundation

Invisible by design. Every later milestone consumes this or duplicates it.

| # | Commit | Size |
| --- | --- | --- |
| C1 | Radius, motion, elevation and surface values from the design freeze added to the token block; nothing consumes them yet | S |
| C2 | The 12-step neutral ramp replaces the four ad-hoc surfaces; every existing rule remapped. Values chosen so the visual delta is near-zero | M |
| C3 | The semantic layer introduced — `action.*`, `market.*`, `status.*`, `border.*`, `focus.*` — components still on primitives | M |
| C4 | Components repointed to semantic tokens; zero primitive references remain | L |
| C5 | The nine type roles in `rem`; the ~14 hardcoded sizes replaced; large-text mode becomes a single root change | L |
| C6 | The eight-step spacing scale; off-scale values removed | M |
| C7 | `scripts/token_check.py` + wired into `verify.ps1` | M |
| C8 | Dual focus ring and `border.control` on inputs; contrast assertions in the gate | M |
| C9 | `scripts/motion_check.py` + wired into `verify.ps1` | M |

**Exit:** the current UI renders entirely from semantic tokens, looks
essentially unchanged, and two static gates now fail the build on a
primitive reference or an off-catalogue animation.

**Rollback:** each commit is independently revertible; none changes markup
structure.

### M1 — V0.11.0 · Workspace context and Surface Level

Mostly backend, therefore mostly `pytest`-covered. Its visible outcome is
small and real: type a symbol once and it is set everywhere.

| # | Commit | Size |
| --- | --- | --- |
| C1 | `RuntimeSettings` gains `surface_level` (1–4) with shape validation and persistence; malformed values fall back to default rather than failing startup | M |
| C2 | `services/workspace.py` gains symbol, timeframe and selected-contract context with merge semantics | M |
| C3 | The workspace endpoint extended; `api_contract_check.py` updated | S |
| C4 | One symbol context: `#ch-symbol`, `#tk-symbol` and `#bt-symbol` become renders of the workspace symbol | L |
| C5 | One timeframe context, shared between the Charts tab and the Trade tab's embedded chart | M |
| C6 | Surface Level control (temporary home in Settings) with the chain's column set as its first consumer | M |
| C7 | `workspace_check.py` extended with the loop test: type a symbol once, complete chart → chain → ticket, never retype it | M |

**Exit:** the §4.5 continuity guarantee holds for symbol and timeframe, and
survives a restart. **No layout change.**

**Risk:** C4 touches three input elements that many existing assertions
reference. It is the commit most likely to break `chart_check.py` — which
is also the only gate that fetches from live providers and is known-flaky.
On a failure there, re-run the same state before investigating
(`PROJECT_STATUS.md`).

### M2 — V0.12.0 · The shell

The highest-risk milestone in the programme: it touches every screen and
changes where everything is.

| # | Commit | Size |
| --- | --- | --- |
| C1 | `ui.shell_v2` in `RuntimeSettings`, default off, with a toggle in Settings | S |
| C2 | The frame, nav rail and system strip, rendering six destinations that host the existing section markup unchanged | XL |
| C3 | Command palette: destinations, actions, settings, and the MOVED aliases that resolve old tab names and show the mapping | L |
| C4 | Symbol jump (`/`) as the single symbol editor; M1-C4's temporary affordance retired | M |
| C5 | Flight Status popover; both mode segments move into it; a test asserts each axis leaves the other unchanged | M |
| C6 | Notification inbox with persisted read state, grouping, and toast stacking with `+ N more` | L |
| C7 | The keyboard map and the `?` reference overlay | M |
| C8 | Guided tour retargeted — 18 step targets rewritten; tutorial **ids unchanged** so the catalogue contract still holds | M |
| C9 | One destination-resolving selector helper per browser suite; 38 references migrated; both navigations drivable during the overlap | L |
| C10 | `scripts/shell_check.py` + wired into `verify.ps1` | L |
| C11 | Flag default flipped **on**; old navigation reachable by toggling off | S |

**Exit:** six destinations, one frame, one palette, one symbol jump, one
inbox — over content nobody has rebuilt yet. Every existing capability
reachable, several by their old names.

**Rollback:** toggle the flag. This is why the flag is in `RuntimeSettings`
and not in `settings.py`.

### M3 — V0.13.0 · Home

| # | Commit | Size |
| --- | --- | --- |
| C1 | Open risk in `PortfolioService`; boundary tests (no positions, missing marks, zero account) | M |
| C2 | The status-line view model; one test per case in the eight-case table | L |
| C3 | The Home view model endpoint; contract check updated | M |
| C4 | The **instrument** component — surface, label, recessed interior, concentric radii. Home is its first consumer and every later milestone reuses it | L |
| C5 | Band 1: status line on the canvas; the metric cluster as one instrument with five compartments | L |
| C6 | Band 2: positions instrument, and what-to-do-next rendering `intelligence/`'s top three verbatim | L |
| C7 | Band 3: equity (30-day default) and the watchlist with its required-confidence tick | M |
| C8 | The four states — loading, empty, error, populated — under the shape invariant | M |
| C9 | `scripts/home_check.py` + wired into `verify.ps1` | L |
| C10 | Legacy dashboard markup deleted; **legacy navigation deleted** (the one release has elapsed) | M |

**Exit:** Home fits without scrolling at 1920×1080 for bands 1–2; a
five-trade history shows coverage reasons rather than numbers.

### M4 — V0.14.0 · Trade

The most valuable milestone in the programme.

| # | Commit | Size |
| --- | --- | --- |
| C1 | Quick-pick intent resolution in `services/contracts.py`; tests including no-chain, no-ATM and thin-chain cases | M |
| C2 | The review view model in `services/trading.py`; tests for every order type and both sides | L |
| C3 | The workspace layout: chart, chain, ticket, symbol position; splitters persisted per destination | L |
| C4 | The ticket, always present, in its empty and selected states | L |
| C5 | The chain: spot-anchored on load, roving-tabindex keyboard navigation, Surface-Level column sets | L |
| C6 | Quick picks wired to the ticket and marked in the chain | M |
| C7 | The review modal with the five required elements, in order, for every order type | L |
| C8 | The hold-to-confirm commit control: pointer and keyboard hold, early-release cancel, reduced-motion stepping, three announcements | L |
| C9 | The ticket's blocked state — guardrails that say what changed, why, and what to do instead. `OrderManager`'s refusals re-asserted unchanged | M |
| C10 | `scripts/trade_check.py` + wired into `verify.ps1` | XL |
| C11 | Legacy trade and charts markup deleted | M |

**Exit:** `/ S P Y ⏎` · `↓ ↓ ⏎` · `⏎` · hold `⏎` places a reviewed order
with no mouse. Every `OrderManager` and `RiskManager` refusal behaves
exactly as before.

**The R-3 tripwire lives here.** If a commit in M4 finds itself wanting to
change what an order does rather than how it is composed, it stops.

### M5 — V0.15.0 · Portfolio and Journal

| # | Commit | Size |
| --- | --- | --- |
| C1 | The Portfolio view model — positions, working, closed-today, exposure by symbol | M |
| C2 | Portfolio as index + detail; the rail is an instrument and keeps its width when empty | L |
| C3 | Close and adjust-stop through review + commit; AI-managed positions disable manual actions **with the reason stated** | M |
| C4 | Journal's three sections; the `Review N` count — the only count permitted in navigation | M |
| C5 | The trade detail rail: engine reasoning, coach review and pattern as three groups of one instrument | L |
| C6 | Progress: the score cluster, with the unassessable compartment rendered as a peer rather than as damage | L |
| C7 | Finding → the trades that produced it | M |
| C8 | Portfolio and Journal assertions added to `browser_check.py` | L |
| C9 | Legacy journal and coach markup deleted | M |

**Exit:** every coach and intelligence surface that exists today is
reachable in the new structure with nothing lost.

### M6 — V0.16.0 · Research and Settings

| # | Commit | Size |
| --- | --- | --- |
| C1 | Research's section rail, continuing the cockpit edge rather than competing with it | M |
| C2 | Explore: chart, AI verdict with passed/failed evidence, symbol facts | L |
| C3 | Backtest rebuilt, with its documented-limitation line | M |
| C4 | Watchlist management preserved wholesale — paste, presets, pin, drag, multi-select, seven sorts, auto-save | L |
| C5 | Engine (was Learning): weights from the injected store, `--` with its reason below the sample floor, no rate below `n=5`, Level 3+ gating whose palette entry explains rather than 404s | L |
| C6 | Settings' five groups; every market-data behaviour preserved — focus-pause, hidden-pause, masking, ranking rendered verbatim, priorities 10 apart, the 401/403 wording | XL |
| C7 | Research and Settings assertions added to `browser_check.py`; `marketdata_check.py` retargeted | L |
| C8 | **All remaining legacy markup deleted**; an `index.html` size ratchet introduced so it cannot regrow | M |

**Exit:** nothing from the old Charts, Backtest, Watchlist, Learning or
Settings tabs is unreachable, and the file has shed its dual-UI weight.

### M7 — V0.17.0 · Onboarding and Surface Levels applied

| # | Commit | Size |
| --- | --- | --- |
| C1 | Surface Level applied across every destination — columns, annotations, defaults, density | L |
| C2 | Local reveal ("show all columns") that does not change the user's level | M |
| C3 | The four onboarding screens, with `Skip setup` prominent on screen 1 | L |
| C4 | The practice-trade path from screen 4 to a completed paper order | M |
| C5 | First-time-only inline explanations, driven by measured feature usage | M |
| C6 | The evidence-based level offer — from feature usage only, **never** from trading behaviour | M |
| C7 | `guide_check.py` extended; a scripted first-run under 60 seconds | L |

### M8 — V0.18.0 · Pilot v1 (deterministic)

| # | Commit | Size |
| --- | --- | --- |
| C1 | `services/pilot.py` — composition over glossary, guide, `intelligence/`, `coach/` and gate reasoning. No model, no network | XL |
| C2 | The inline explanation surface, attached in place to terms and statistics | L |
| C3 | The panel (`Ctrl+/`), overlaying rather than reflowing, state persisted across destinations and restart | L |
| C4 | Home's what-to-do-next voiced by Pilot; the Journal note surface | M |
| C5 | The eight silence rules, enforced in code, each with a test — including the hard cap of two unprompted messages per session | L |
| C6 | `pilot_check.py`: every claim carries its `n`; zero unprompted messages during order composition | L |

**Blocked by:** open decision #1 (does Pilot get an LLM). M8 ships the
deterministic version regardless; the decision only determines whether a
later milestone adds a free-form channel.

### M9 — V1.0.0 · Polish, accessibility, pop-outs, legacy removal

| # | Commit | Size |
| --- | --- | --- |
| C1 | Accessibility pass: skip link, landmarks, the single polite live region, table semantics, the chart's text alternative | XL |
| C2 | `scripts/a11y_check.py` + wired into `verify.ps1` | L |
| C3 | Desktop responsive breakpoints and the sub-1024px message | L |
| C4 | The density setting, independent of Surface Level | M |
| C5 | Pop-out windows for chart, ticket and Portfolio, sharing workspace context. **A close handler decides and returns; the work goes to a worker** — the V0.8.1 message-pump hazard applies directly | XL |
| C6 | Window geometry, display assignment and pop-out state persisted through `RuntimeSettings` | M |
| C7 | `ui.shell_v2` and all its branches removed; the ratchet lowered | M |
| C8 | `scripts/shape_check.py`; the manual keyboard and screen-reader run of the order path | L |
| C9 | Release 1.0.0 | S |

---

## 7. What each milestone gives a user

Because "independently shippable" only means something if each release is
worth installing.

| Milestone | What a user gets |
| --- | --- |
| M0 | Nothing. An honest release note saying so. |
| M1 | Stop retyping the symbol on every screen. |
| M2 | Navigation that can be learned; a command palette; a notification history that survives the session. |
| M3 | A home screen that says whether anything needs them, without scrolling. |
| M4 | Charting and trading on one screen; an order that cannot be placed by accident; a keyboard path. |
| M5 | One place for everything they hold; the coach's review beside the trade it is about. |
| M6 | Research that is not called "Learning"; settings that are findable. |
| M7 | A first run under a minute; an interface that matches their experience. |
| M8 | An expert attached to every confusing word, that stays quiet. |
| M9 | Multi-monitor, full keyboard and screen-reader support, 1.0. |

---

## 8. Rollback

| Level | Mechanism |
| --- | --- |
| Commit | `git revert`. No commit in this programme depends on a later one to be correct. |
| Milestone | Release the previous tag. `scripts/release.ps1` rolls back everything before the push automatically; after the push the previous version remains installable. |
| M2 through M8 | Toggle `ui.shell_v2` off. This is the only cross-cutting escape hatch and it is why the flag survives until M9. |
| Data | No milestone changes a persisted schema except by addition. `RuntimeSettings` and `services/workspace.py` additions are additive and default when absent, so a downgrade loses preferences and never data. |

---

## 9. Risk register

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| R1 | **38 `data-tab` references across six browser suites break at M2** | High | Measured, not discovered. M2-C9 is a dedicated commit; a selector helper per suite makes it one change per file. |
| R2 | **18 guided-tour step targets break at M2**, and the tour is the first thing a new user sees | High | M2-C8. Tutorial **ids** are untouched, so `TestCatalogueContract` keeps asserting the frontend/backend catalogue match in both directions. |
| R3 | **`index.html` carries both UIs from M2 to M6**, growing ~50–60% | Medium | Accepted deliberately. M6-C8 deletes the legacy markup and introduces the ratchet, the same mechanism used on `ui/server.py`. |
| R4 | **`chart_check.py` is the only gate hitting live providers and is known-flaky** | Medium | On a failure, re-run the same state first. A bisect against this gate produced a confidently wrong conclusion during V0.9.2-C3. |
| R5 | **Flight Status could couple the two orthogonal mode axes** | High | M2-C5 ships with a test asserting each axis leaves the other unchanged, following `RuntimeSettings._apply_mode`'s explicit preservation pattern. |
| R6 | **Surface Level becomes a third coupled axis** | High | M1-C1 lands it as presentation-only with a test that identical inputs produce identical orders, risk decisions and fills at every level. |
| R7 | **`services/` gains a transport import** while new view models are added | Medium | `tests/test_architecture.py` already fails the suite on this in both directions, including imports inside function bodies. |
| R8 | **New client state lands in `localStorage`** | Medium | R-8. `workspace_check.py`'s canonical test — wipe client storage, reload, assert the symbol on screen is the one that comes back — covers every value added to the workspace document. |
| R9 | **A UI regression ships silently** because `index.html` has no automated coverage | High | R-2. Seven new gates, six updated. No commit ships a surface without assertions. |
| R10 | **Pop-out windows reintroduce the pywebview message-pump deadlock** | High | M9-C5. A close handler decides and returns; the work goes to a worker. The V0.8.1 account in `CLAUDE.md` is required reading before that commit. |
| R11 | **A milestone quietly changes trading behaviour** | High | R-3, plus the existing suite: `OrderManager` and `RiskManager` refusals are re-asserted in M4-C9 rather than assumed. |
| R12 | **Context compaction loses the plan mid-milestone** — this has happened three times in this repository | Medium | §12's commit map, updated in the same commit that lands a row. That convention exists because of those three occasions. |

---

## 10. Out of scope

Named so nobody has to ask.

- **The mobile application.** `UI_V2_DESIGN.md` §14 and
  `UI_V2_WIREFRAMES.md` §12 specify it; it is gated on
  `ARCHITECTURE-MOBILE.md` §17's hosting decisions and is not scheduled
  here.
- **The light theme.** Every component defines its light values as it is
  built (a design-system rule), so the theme is a later assembly, not a
  later project.
- **An LLM-backed Pilot.** M8 ships the deterministic version. The
  free-form channel is a separate decision and a separate milestone.
- **Any change to trading behaviour, risk, fills or the engine.** R-3.
- **Live broker support.** R-7 and `CLAUDE.md`'s overriding rule.
- **A build step, a bundler, or additional vendored libraries.** The
  single-file constraint holds throughout.

---

## 11. Decisions needed, and when

Each blocks a specific milestone. None blocks starting.

| # | Decision | Blocks | Needed by |
| --- | --- | --- | --- |
| 1 | Vendored variable typeface, or platform faces? | Type token values | **M0-C5** |
| 2 | Does the light theme ship with V2 or after? | Whether M0 defines both themes or only dark | **M0-C3** |
| 3 | Compact or Comfortable default at Surface Levels 3–4? | Row heights everywhere | **M1-C6** |
| 4 | Real OS windows or in-app panes for pop-outs? | Scope and hazard profile | **M9-C5** |
| 5 | Does Surface Level sync across devices? | The shape of the persisted value | **M1-C1** |
| 6 | Is Research one destination or two? | The section rail | **M6-C1** |
| 7 | Does Pilot get an LLM? | Whether the panel has an ask field | **M8-C3** |
| 8 | Which local telemetry, if any, backs the success metrics? | Whether M3+ instruments anything | **M3-C1** |

Recommendations for all eight are already recorded in the frozen documents
(`UI_V2_DESIGN.md` §19, `DESIGN_SYSTEM_V2.md` §12.4,
`UI_V2_WIREFRAMES.md` §13.2, `UI_V2_VISUAL_EXPLORATION.md` §9). Adopting
the recommendations unblocks everything and is the default if no decision
is taken.

---

## 12. Commit map

**Update this table in the same commit that lands a row.** This convention
exists because three sessions have had to stop and ask which commit came
next after context compaction, and nothing in the repository recorded the
mapping.

### Active milestone — M0 · V0.10.0 Foundation

| Commit | Status | Description | Hash |
| --- | --- | --- | --- |
| C1 | ✅ | UI V2 primitives added to the token block, consumed by nothing | `8c5586e` |
| C2 | ✅ | The neutral ramp replaces the four ad-hoc surfaces | `f416e01` |
| C3 | ✅ | The semantic layer | `b60daa1` |
| C4 | ✅ | Components repointed; zero primitive references | `26a35d0` |
| C5 | ✅ | Type roles in `rem`; large text becomes one root change | `9082c8c` |
| C6 | ✅ | The spacing scale, adopted where it already matched | `pending` |
| C7 | ⬜ | `scripts/token_check.py` | — |
| C8 | ⬜ | Dual focus ring and `border.control` on inputs | — |
| C9 | ⬜ | `scripts/motion_check.py` | — |

### All milestones

| Milestone | Commits | Status | Hashes |
| --- | --- | --- | --- |
| M0 · V0.10.0 Foundation | C1–C9 | 🟡 in progress | see above |
| M1 · V0.11.0 Workspace context | C1–C7 | ⬜ not started | — |
| M2 · V0.12.0 Shell | C1–C11 | ⬜ not started | — |
| M3 · V0.13.0 Home | C1–C10 | ⬜ not started | — |
| M4 · V0.14.0 Trade | C1–C11 | ⬜ not started | — |
| M5 · V0.15.0 Portfolio & Journal | C1–C9 | ⬜ not started | — |
| M6 · V0.16.0 Research & Settings | C1–C8 | ⬜ not started | — |
| M7 · V0.17.0 Onboarding & Levels | C1–C7 | ⬜ not started | — |
| M8 · V0.18.0 Pilot v1 | C1–C6 | ⬜ not started | — |
| M9 · V1.0.0 Polish & removal | C1–C9 | ⬜ not started | — |

Per-milestone reasoning goes in `docs/reports/` following the V0.9.2
precedent, one file per milestone, written as the milestone closes.

---

## 13. Related documents

| Document | Relationship |
| --- | --- |
| `UI_V2_DESIGN.md` | Frozen. Its §16 phases are the coarse form of this plan; where the two differ, this file is the finer decomposition and the phase order is unchanged. |
| `UI_V2_WIREFRAMES.md` | Frozen. Its §13.3 lists the per-screen assertions that become §5's gates. |
| `DESIGN_SYSTEM_V2.md` | Frozen. Its §12.6 health checks become M0's two static gates and M9's accessibility gate. |
| `UI_V2_VISUAL_EXPLORATION.md` | Frozen. Its §8 freeze supplies every value M0 lands. |
| `ROADMAP.md` | The project roadmap; this milestone series is its next entry. |
| `CONTRIBUTING.md` | The documentation requirements every commit here must satisfy. |
| `RELEASE.md` | How each milestone ships. |
| `CLAUDE.md` | The rules in §1, and the hazard accounts behind R4, R10 and R12. |
