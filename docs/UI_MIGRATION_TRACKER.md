# UI_MIGRATION_TRACKER.md — living migration tracker for UI V2

**Single source of truth for what has and has not been migrated.**
Update it in the same commit that changes a row. Plan:
`ROADMAP-UI-V2.md`. Design: `UI_V2_DESIGN.md`, `UI_V2_WIREFRAMES.md`,
`DESIGN_SYSTEM_V2.md`, `UI_V2_VISUAL_EXPLORATION.md`.

**Status legend:** ⬜ Not started · 🟡 In progress · ✅ Complete ·
🗑️ Legacy removed · ➖ N/A

**Last updated:** 2026-08-06, **M2 complete**. The shell is the default.

---

## 1. Feature flags

| Flag | Home | Introduced | Default on | Removed | Controls |
| --- | --- | --- | --- | --- | --- |
| `ui.shell_v2` | `config/runtime.py` (`RuntimeSettings`, live-editable) | M2-C1 | M2-C11 | M9-C7 | The new shell (frame, rail, strip) and, until each destination is rebuilt, which content markup renders |

No other flag is planned. A second flag requires a row here and a reason.

---

## 2. Measured baseline

Numbers taken at M0-C1 so progress is provable rather than asserted.
Re-measure at each milestone close.

| Metric | At M0 start | Target | Now |
| --- | --- | --- | --- |
| `index.html` lines | 8,254 | ≤ 8,254 at M9 (ratchet from M6-C8) | 10,340 |
| Hardcoded `font-size: Npx` | 4 | 0 | **0** ✅ |
| `font-size: var(--fs-*)` uses | 185 | all on roles | **0** — all renamed to `--text-*`; 51 on `--legacy-fs-md` |
| `var(--space-*)` uses | 0 | all rhythm | **173** |
| Off-scale rhythm occurrences (≤48px) | 321 | 0 | **313** — ratcheted, retired per destination in M3–M6 |
| `var(--radius-*)` uses | 56 legacy | all radii | **53** on the new names; legacy deleted |
| `data-tab` refs in browser suites | 38 (53 by M2) | 0 | **0** ✅ |
| `data-tab` refs in `index.html` | 18 | 0 | 21 — the legacy nav's own markup, deleted at M3-C10; the tour's now resolve through the registry at runtime |
| `verify.ps1` gates | 13 | 20 | **16** |
| `workspace_check.py` assertions | 21 | grows with the context | **50** |
| Test count | 2,493 | grows | **2,623** |

**Correction to `ROADMAP-V3-UX.md`:** that audit reported ~14 hardcoded
font sizes. Measured today it is 4 — the type scale was largely adopted
during V3. The unadopted scale is **spacing**, at zero uses. M0's effort
sits in C6, not C5.

---

## 3. Shell surfaces

| Surface | Legacy location | New owner | Milestone | Status | Legacy deleted | Checks that migrate |
| --- | --- | --- | --- | --- | --- | --- |
| Navigation (9 tabs) | `index.html` `<nav>` L979–1010 | Nav rail, 5 destinations + Pilot + Settings | M2-C2 | ✅ | M3-C10 | all 6 suites (38 refs) |
| Header bar | `index.html` `<header>` L1013–1050 | Frame (identity, destination, context, status, Pilot) | M2-C2 | ✅ | M3-C10 | `browser_check` |
| Operating-mode segment | `#op-seg` | Flight Status popover | M2-C5 | ✅ | M3-C10 | `browser_check`, new `shell_check` |
| Trading-mode segment | `#mode-seg` | Flight Status popover | M2-C5 | ✅ | M3-C10 | `shell_check` (orthogonality) |
| Market pill / cycle pill | `#market-pill`, `#cycle-pill` | Flight Status popover | M2-C5 | ✅ | M3-C10 | `browser_check` |
| Scan button | `#scan-btn` | Home primary action + palette + Flight Status | M2-C3 | ✅ | M3-C10 | `browser_check` |
| Learn button | `#learn-btn` | Contextual help + palette | M2-C3 | ✅ | M3-C10 | `guide_check` |
| Help menu (6 items) | `#help-menu` | Palette entries | M2-C3 | ✅ all six | M3-C10 | `guide_check` |
| Paper badge | `nav .paper-wrap` | Flight Status | M2-C5 | ✅ | M3-C10 | `browser_check` |
| Version indicator | `#ver` | System strip | M2-C2 | ✅ | M3-C10 | `browser_check` |
| Halt banner | `#halt-banner` | App-scoped banner | M2-C2 | ✅ | M3-C10 | `browser_check` |
| Command palette | ➖ new | `index.html` shell | M2-C3 | ✅ | ➖ | `shell_check` |
| Symbol jump (`/`) | ➖ new | `index.html` shell | M2-C4 | ✅ | ➖ | `shell_check`, `workspace_check` |
| System strip | ➖ new | `index.html` shell | M2-C2 | ✅ | ➖ | `shell_check` |
| Surface Level control | ➖ new | System strip | M1-C6 → M2-C2 | ✅ | ➖ | `shell_check` |
| Notification inbox | `#notifs` (dashboard panel) | Global inbox | M2-C6 | ✅ | M3-C10 | `shell_check` |
| Toasts | `.notif` / toast helpers | Toast stack, max 3 + `+N more` | M2-C6 | ✅ | ➖ | `shell_check` |
| Keyboard map + `?` overlay | scattered handlers | One map, one overlay | M2-C7 | ✅ | ➖ | `shell_check` |
| Pilot panel | ➖ new | `index.html` shell | M2-C2 scaffold / M8-C3 content | 🟡 scaffold | ➖ | `pilot_check` |

---

## 4. Destinations

| Legacy section | New destination | Milestone | Status | Legacy deleted | Checks that migrate |
| --- | --- | --- | --- | --- | --- |
| `#tab-dashboard` | **Home** | M3-C5…C8 | ⬜ | M3-C10 | `browser_check`, `intelligence_check`, new `home_check` |
| `#tab-trade` | **Trade** (ticket + chain) | M4-C3…C9 | ⬜ | M4-C11 | `browser_check`, new `trade_check` |
| `#tab-charts` | **Trade** (chart) + **Research › Explore** | M4-C3 / M6-C2 | ⬜ | M4-C11 | `chart_check` (3 refs) |
| positions/working/history in `#tab-trade` | **Portfolio** | M5-C2 | ⬜ | M4-C11 | `browser_check` |
| `#tab-coach` | **Journal › Review** | M5-C4 | ⬜ | M5-C9 | `intelligence_check` (4 refs) |
| `#tab-journal` | **Journal › Trades** | M5-C4 | ⬜ | M5-C9 | `browser_check` |
| intel panel (`#intel-panel`) | **Journal › Progress** | M5-C6 | ⬜ | M5-C9 | `intelligence_check` |
| `#tab-watchlist` | **Research › Watchlist** + context strips | M6-C4 | ⬜ | M6-C8 | `browser_check`, `workspace_check` |
| `#tab-backtest` | **Research › Backtest** | M6-C3 | ⬜ | M6-C8 | `browser_check` |
| `#tab-learning` | **Research › Engine** | M6-C5 | ⬜ | M6-C8 | `browser_check` |
| `#tab-settings` | **Settings** (5 groups) | M6-C6 | ⬜ | M6-C8 | `marketdata_check` (3 refs) |

---

## 5. Component inventory

`DESIGN_SYSTEM_V2.md` §6 defines 27 components. Column *Legacy* names the
existing CSS class or id it replaces; blank means new.

| # | Component | Legacy | Milestone | Status |
| --- | --- | --- | --- | --- |
| 1 | Button | `.btn`, `.btn.primary`, `.btn.buy` | M0-C4 | 🟡 on semantic tokens; states M4 |
| 2 | Commit control | ➖ | M4-C8 | ⬜ |
| 3 | Input | bare `input`/`textarea` rules | M0-C8 | 🟡 border + focus done; states M4 |
| 4 | Dropdown / select | bare `select` rules | M0-C4 | 🟡 on semantic tokens |
| 5 | Segmented control | `.seg`, `.side-seg` | M0-C4 | 🟡 on semantic tokens |
| 6 | Card / panel → **instrument** | `.panel`, `.cards` | M3-C4 | ⬜ |
| 7 | Table | `.wl-*`, `.chain-wrap`, journal table | M4-C5 | ⬜ |
| 8 | Chart | `#chart-panel`, `CH.*` | M4-C3 | ⬜ |
| 9 | Navigation rail | `nav` | M2-C2 | ✅ |
| 10 | Tabs (section rails) | ➖ | M2-C2 | ✅ |
| 11 | Command palette | ➖ | M2-C3 | ✅ |
| 12 | Search | `#wl-filter`, `#jf-sym` | M6-C4 | ⬜ |
| 13 | Badge | assorted inline | M2-C6 | ✅ |
| 14 | Tag | `.chips` | M6-C4 | ⬜ |
| 15 | Status pill | `.pill`, `.badge` | M2-C5 | ✅ |
| 16 | Tooltip | `title=` + `data-tip` | M7-C5 | ⬜ |
| 17 | Popover | `.help-menu` | M2-C5 | ✅ Flight Status |
| 18 | Modal | confirm dialogs | M4-C7 | ⬜ |
| 19 | Context menu | ➖ | M5-C2 | ⬜ |
| 20 | Empty state | `.empty`, `.dash-empty` | M3-C8 | ⬜ |
| 21 | Skeleton | skeleton loader rules | M3-C8 | ⬜ |
| 22 | Progress indicator | `#bt-status` | M6-C3 | ⬜ |
| 23 | Toast | `.notif` | M2-C6 | ✅ |
| 24 | Banner | `#halt-banner` | M2-C2 | ✅ app-scoped |
| 25 | Notification inbox | `#notifs` | M2-C6 | ✅ |
| 26 | Pilot surfaces | `data-learn`, glossary | M8-C2 | ⬜ |
| 27 | Chip / quick pick | `.chips`, `.exp-pills` | M4-C6 | ⬜ |

---

## 6. Design tokens

| Token group | Legacy | New | Milestone | Status |
| --- | --- | --- | --- | --- |
| Radius values | `--r-sm/md/lg/pill` (8/12/14/999) | `--radius-sm/med/lg/pill` 6/10/14/999 | M0-C1 | ✅ values landed; adoption M0-C4 |
| Motion durations | `--t-fast/.13`, `--t-med/.22` | `--dur-instant/fast/medium/deliberate` | M0-C1 | ✅ values landed; adoption M0-C4 |
| Motion curves | ➖ | `--ease-enter/exit/move` | M0-C1 | ✅ |
| Surfaces | `--page`, `--surface`, `--surface-2/3` | 12-step ramp `--n-950…--n-050` | M0-C1 ramp, M0-C2 remap | ✅ nine names are now aliases onto the ramp; deleted M0-C4 |
| Semantic layer | ➖ | `--surface-*`, `--ink-*`, `--border-*`, `--action-*`, `--market-*`, `--status-*`, `--focus-*` | M0-C3 | ✅ defined; consumers M0-C4 |
| Component→semantic repoint | components use primitives | zero primitive refs | M0-C4 | ✅ 477 replacements; old names deleted |
| Type scale | `--fs-*` in px (9 steps, 4 stragglers) | 8 roles in `rem` + `--legacy-fs-md` | M0-C5 | ✅ roles landed; the 13px legacy step retires per destination in M3–M6 |
| Spacing scale | `--sp-1..6`, **0 uses** | `--space-0..7`, 8 purpose-named steps | M0-C6 | ✅ scale landed, 173 exact matches adopted; 313 off-scale retire per destination |
| Focus ring | single `:focus-visible` outline | dual ring + gap, 2px offset | M0-C8 | ✅ |
| Input borders | none / `--grid` | `--border-control` at 3.32:1 | M0-C8 | ✅ base rule + field rules upgraded |
| Elevation shadows | `--sh-1/2/3` | `--shadow-raised/overlay`, `--scrim` | M0-C1 values, M0-C4 adoption | 🟡 values landed |

---

## 7. Backend surfaces

All are re-presentations of existing computations — no new capability.

| Surface | Home | Milestone | Status | Tests |
| --- | --- | --- | --- | --- |
| Surface Level | `config/runtime.py` | M1-C1 | ✅ | `tests/test_runtime_settings.py` |
| Workspace symbol / timeframe / selection | `services/workspace.py` | M1-C2 | ✅ | `tests/test_services_workspace.py` |
| Open risk | `services/portfolio.py` | M3-C1 | ⬜ | `tests/test_portfolio.py` |
| Status-line view model | `services/` (new) | M3-C2 | ⬜ | new test file |
| Quick-pick intent resolution | `services/contracts.py` | M4-C1 | ⬜ | `tests/test_contracts.py` |
| Review view model | `services/trading.py` | M4-C2 | ⬜ | `tests/test_trading.py` |
| Portfolio composition | `services/portfolio.py` | M5-C1 | ⬜ | `tests/test_portfolio.py` |
| Notification read state | `services/notifications.py` | M2-C6 | ⬜ | `tests/test_notifications.py` |
| Pilot composition | `services/pilot.py` (new) | M8-C1 | ⬜ | new test file |

---

## 8. Gates

### 8.1 New

| Gate | Milestone | Status | Wired into `verify.ps1` |
| --- | --- | --- | --- |
| `scripts/token_check.py` | M0-C7 | ✅ | ✅ gate 4/8 |
| `scripts/motion_check.py` | M0-C9 | ✅ | ✅ gate 5/9 |
| `scripts/shell_check.py` | M2-C10 | ✅ | ✅ 7th browser suite, 33 checks |
| `scripts/home_check.py` | M3-C9 | ⬜ | ⬜ |
| `scripts/trade_check.py` | M4-C10 | ⬜ | ⬜ |
| `scripts/a11y_check.py` | M9-C2 | ⬜ | ⬜ |
| `scripts/shape_check.py` | M9-C8 | ⬜ | ⬜ |

### 8.2 Existing, to retarget

| Gate | `data-tab` refs | Milestone | Status |
| --- | --- | --- | --- |
| `scripts/guide_check.py` | 23 | M2-C9 | ⬜ |
| `scripts/intelligence_check.py` | 4 | M2-C9 | ⬜ |
| `scripts/workspace_check.py` | 4 | M1-C7, M2-C9 | ⬜ |
| `scripts/chart_check.py` | 3 | M2-C9, M4 | ⬜ |
| `scripts/marketdata_check.py` | 3 | M2-C9, M6 | ⬜ |
| `scripts/browser_check.py` | 1 | M2-C9 | ⬜ |
| `index.html` guided-tour targets | 18 | M2-C8 | ⬜ |
| `scripts/check_html_ids.py` | ➖ | continuous | ➖ |
| `scripts/guide_check.py` colour literal | ➖ | M0-C2 | ✅ un-pinned from `rgb(11, 12, 14)` |

---

## 9. Legacy deletion schedule

Nothing is deleted before its replacement ships and its checks pass.

| Milestone | Deleted |
| --- | --- |
| M3-C10 | Legacy navigation, header, dashboard markup |
| M4-C11 | Legacy trade + charts markup |
| M5-C9 | Legacy journal + coach markup |
| M6-C8 | Legacy watchlist, backtest, learning, settings markup; `index.html` ratchet introduced |
| M9-C7 | `ui.shell_v2` flag and all its branches |

---

## 10. Tracker changelog

| Date | Milestone | Change |
| --- | --- | --- |
| 2026-08-06 | M3-C1…C3 | **Home's backend tier, complete; no pixel has moved yet.** `open_risk` — the figure no screen states today — as a MAXIMUM, because every position this broker can hold is long (`open_position` refuses `quantity < 1`) so the premium in it is the most it can lose; loss-to-stop was rejected because the only delta a `Position` persists is its ENTRY snapshot, and a live risk figure from a stale greek states what cannot be evidenced. The status line as a pure module with **the precedence §5.3 leaves open** — halt > rejected > stop > degraded, all four above the first-run welcome, because a greeting that hid a halt would be the sentence lying on the launch where the user has least context to catch it. `HomeService` + `GET /api/v1/home`: six regions from four owners in ONE request (six round trips is six independently shifting regions, the layout jump the milestone exists to remove) but failing **per region**, with `next_actions: None` distinguishing "I could not look" from "no findings". **Spec conflict recorded:** §5.3 says the win-rate floor is 30, §2.9 draws the same card as "0 of 5"; 5 is `MIN_SAMPLE_LOW` and belongs to H4, not to this metric — implemented at 30 (`stats.MIN_SAMPLE_HIGH`, the ladder `intelligence/` already judges itself against). One test deleted for being vacuous: a `FakeBroker` subclass that deletes `current_marks` still inherits it. |
| 2026-08-06 | M2-C6…C9, C11 | **M2 complete; the shell is the default.** Notification inbox with server-owned read state and a three-deep toast stack; one keyboard map rendered from the registry; the tour retargeted (selectors AND instruction text derived from `DESTINATIONS`); **53** `data-tab` references across six suites replaced by one shared `scripts/shell_nav.py` that clicks real controls. **Three defects found by the suites:** the legacy `nav { width:200px }` element selector was pinning the new rail to 200px inside a 72px track so content painted over it below 1440px (13 rules now scoped to `nav[aria-label="Main"]`); a palette command named a tutorial id that does not exist; three Help-menu items had no palette home. |
| 2026-08-06 | M2-C1…C5, C10 | **The shell exists, behind a flag that is OFF by default.** Frame, nav rail and system strip as siblings of the legacy chrome, so `body.shell-v2` re-lays-out what is already on the page — no section moves and the flag is a true rollback, asserted as the gate's last three checks. One `DESTINATIONS` registry that the rail, section rails, router, frame title, keyboard map and gate all read. Command palette (Ctrl+K), symbol jump (`/`), Flight Status with the orthogonality sentences. `shell_check.py` landed as the 7th browser suite (33 checks), brought forward from C10's place in the order because it protects everything the rest builds. **Defects found: `Ctrl+K` was already bound to the help centre (V0.6.1) so both dialogs answered it; the shell rendered placeholder text when switched on mid-session because the websocket only pushes on change.** C6–C9 and C11 remain. |
| 2026-08-05 | M1-C7 | The loop test: one `fill`, then charted, chained and ticketed with no further typing. Writing it found that C2/C3 had given the selected contract a server-owned home that **the client never wrote to** — `tkSel` lived and died in the page. `selectContract` now persists it and the first matching chain load restores it. Also fixed a stale `#tk-spot` that only became reachable in C4. `workspace_check.py` 43 → 50. **M1 complete: 7 commits, 15 gates, 2,564 tests.** |
| 2026-08-05 | M1-C6 | Surface Level control (temporary home in Settings) with the chain's column set as its first consumer: Guided shows strike + prices, Focused adds delta, Full adds IV/OI/liquidity, Pro is deliberately identical to Full until a custom set exists. §8.1-2 asserted directly — hiding Mid at Guided fails the suite. `workspace_check.py` 32 → 43, and it gains the `/api/chain` stub. |
| 2026-08-05 | M1-C5 | One writer for the timeframe (`Ctx.setTimeframe`); the Charts/Trade sharing was already structural — one chart in two slots — and is now asserted as such. `workspace_check.py` 28 → 32. **The new assertions found a real C4 defect:** a slow `/api/chain` response adopted its own symbol and dragged the whole workspace back to it after the user had moved on. Reproduced only when the suite beat the response, so it first read as a flake. |
| 2026-08-05 | M1-C4 | `#ch-symbol`, `#tk-symbol` and `#bt-symbol` became renders of one context (new `Ctx` module). Charting NVDA and opening Trade used to offer a SPY chain; it now offers NVDA. Persistence moved into the setter, so a symbol survives a FAILED chart load — previously it was written only on a successful render. `workspace_check.py` 21 → 28. The three boxes stay for one more milestone; M2-C4 replaces all of them with the `/` symbol jump. |
| 2026-08-05 | M1-C3 | `/api/workspace` now carries all five context facts. Surface Level is served here but stored under its own key — transport and storage are allowed to disagree. Composed in `WorkspaceService` (its store widens from two duck-typed methods to four) so `ui/server.py` gained **zero** lines against a ceiling with 21 to spare. `api_contract_check.py` upgraded from path-presence to a full round trip: the endpoint had been in `REQUIRED_PATHS` since it shipped with nothing ever asking it for a payload. |
| 2026-08-05 | M1-C2 | The workspace document gained `expiry` and `contract`, completing §4.5's definition of context (symbol, timeframe, expiry, contract). One **cross-field** invariant, checked in `normalize` rather than on the symbol-change path so it holds for a hand-edited file too: a contract whose symbol is not the workspace symbol is dropped. `expiry` is validated as a real ISO date and `right` against `OptionRight`, on the same test that already admits `Timeframe` — the domain's own vocabulary, defined in one place. |
| 2026-08-05 | M1-C1 | `surface_level` landed in `RuntimeSettings` (1–4, default **3 Full** — the only default that removes nothing from an installation that predates the field). Two product decisions recorded in `ROADMAP-UI-V2.md` §11: Surface Level is **local only** (its own `sync.py` inventory row, `PREFERENCES`/`DEVICE_ONLY`, kept OUT of the workspace document because that one is meant to follow a user to a second client), and the **light theme ships after V2**. |
| 2026-08-05 | — | Created, before M0-C1. Baseline measured. |
| 2026-08-05 | M0-C1 | UI V2 primitives landed in the token block: neutral ramp, radius, motion, elevation. Consumed by nothing. |
| 2026-08-05 | M0-C9 | `motion_check.py` landed as gate 5/9: closed keyframe catalogue, chart canvas kept exempt, reduced motion verified on both channels, two ratchets. It found that this app routes the OS preference and the in-app toggle through one `html.gd-nomotion` class rather than a media query — a better mechanism than the gate first assumed. **M0 complete: 9 commits, 15 gates.** |
| 2026-08-05 | M0-C8 | Dual focus ring (2px offset + a guaranteed dark gap on filled controls) and `--border-control` on every form field. `token_check` now recomputes **16 contrast floors** from the tokens in the file, so a ramp tweak fails the build rather than an audit. |
| 2026-08-05 | M0-C7 | `token_check.py` landed and wired as gate 4/8. It found **8 dangling `var()` references on its first run** — `--danger`, `--success`, `--elev-2`, `--text-dim`, `--panel` were never defined in this codebase, so those declarations had been rendering unthemed; two more hid behind hex fallbacks. All 16 occurrences repaired. Also closes the radius/motion/shadow adoption C4 left behind. |
| 2026-08-05 | M0-C6 | Spacing scale replaced with the frozen eight steps and adopted at all 173 occurrences that already matched a step exactly — zero visual delta. **Deviation:** 313 off-scale occurrences (6, 10, 14, 18px…) are NOT snapped; that is a whole-app density change no gate can verify, and it retires per destination in M3–M6. |
| 2026-08-05 | M0-C5 | Type scale converted to `rem` and renamed to the frozen roles; the 4 hardcoded sizes tokenised; large-text mode collapsed from nine overrides to one root change. **Deviation:** 13px is not a step in the frozen scale but 51 rules use it, so it survives as `--legacy-fs-md`, labelled legacy rather than blessed as a role. Collapsing it is a density change M0 cannot verify; each destination retires its own uses under rule T-1 as it is rebuilt. |
| 2026-08-05 | M0-C4 | 477 `var()` references repointed onto the semantic layer, property-aware; old token names deleted; `gd-contrast` rewritten. `guide_check.py` read `--muted` by name — both reads went empty and the check passed vacuously, so it now asserts the token is defined. **Debt:** the equity chart's dot uses the accent inside a plot area, which DV rule C-6 forbids; fix in M3. |
| 2026-08-05 | M0-C3 | Semantic layer defined, including status tints (the design system named status colours but not their tint backgrounds — the existing `*-soft` tokens needed a home) and the colour-vision alternate as a scoped override; its Settings toggle lands in M6. |
| 2026-08-05 | M0-C2 | The nine original surface/ink names became aliases onto the ramp; 306 use sites unchanged. `guide_check.py` had one assertion pinned to the literal page colour — rewritten to assert darkness, the property it is named for. |
