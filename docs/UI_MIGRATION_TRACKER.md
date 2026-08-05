# UI_MIGRATION_TRACKER.md — living migration tracker for UI V2

**Single source of truth for what has and has not been migrated.**
Update it in the same commit that changes a row. Plan:
`ROADMAP-UI-V2.md`. Design: `UI_V2_DESIGN.md`, `UI_V2_WIREFRAMES.md`,
`DESIGN_SYSTEM_V2.md`, `UI_V2_VISUAL_EXPLORATION.md`.

**Status legend:** ⬜ Not started · 🟡 In progress · ✅ Complete ·
🗑️ Legacy removed · ➖ N/A

**Last updated:** 2026-08-05, after M0-C5.

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
| `index.html` lines | 8,254 | ≤ 8,254 at M9 (ratchet from M6-C8) | 8,254 |
| Hardcoded `font-size: Npx` | 4 | 0 | **0** ✅ |
| `font-size: var(--fs-*)` uses | 185 | all on roles | **0** — all renamed to `--text-*`; 51 on `--legacy-fs-md` |
| `var(--sp-*)` uses | **0** | all spacing | 0 |
| Distinct hardcoded padding/margin/gap px values | 19 | 0 | 19 |
| `var(--r-*)` uses | 56 | all radii | 56 |
| `data-tab` refs in browser suites | 38 | 0 | 38 |
| `data-tab` refs in `index.html` | 18 | 0 | 18 |
| `verify.ps1` gates | 13 | 20 | 13 |
| Test count | 2,493 | grows | 2,493 |

**Correction to `ROADMAP-V3-UX.md`:** that audit reported ~14 hardcoded
font sizes. Measured today it is 4 — the type scale was largely adopted
during V3. The unadopted scale is **spacing**, at zero uses. M0's effort
sits in C6, not C5.

---

## 3. Shell surfaces

| Surface | Legacy location | New owner | Milestone | Status | Legacy deleted | Checks that migrate |
| --- | --- | --- | --- | --- | --- | --- |
| Navigation (9 tabs) | `index.html` `<nav>` L979–1010 | Nav rail, 5 destinations + Pilot + Settings | M2-C2 | ⬜ | M3-C10 | all 6 suites (38 refs) |
| Header bar | `index.html` `<header>` L1013–1050 | Frame (identity, destination, context, status, Pilot) | M2-C2 | ⬜ | M3-C10 | `browser_check` |
| Operating-mode segment | `#op-seg` | Flight Status popover | M2-C5 | ⬜ | M3-C10 | `browser_check`, new `shell_check` |
| Trading-mode segment | `#mode-seg` | Flight Status popover | M2-C5 | ⬜ | M3-C10 | `shell_check` (orthogonality) |
| Market pill / cycle pill | `#market-pill`, `#cycle-pill` | Flight Status popover | M2-C5 | ⬜ | M3-C10 | `browser_check` |
| Scan button | `#scan-btn` | Home primary action + palette + Flight Status | M2-C3 | ⬜ | M3-C10 | `browser_check` |
| Learn button | `#learn-btn` | Contextual help + palette | M2-C3 | ⬜ | M3-C10 | `guide_check` |
| Help menu (6 items) | `#help-menu` | Palette entries | M2-C3 | ⬜ | M3-C10 | `guide_check` |
| Paper badge | `nav .paper-wrap` | Flight Status | M2-C5 | ⬜ | M3-C10 | `browser_check` |
| Version indicator | `#ver` | System strip | M2-C2 | ⬜ | M3-C10 | `browser_check` |
| Halt banner | `#halt-banner` | App-scoped banner | M2-C2 | ⬜ | M3-C10 | `browser_check` |
| Command palette | ➖ new | `index.html` shell | M2-C3 | ⬜ | ➖ | `shell_check` |
| Symbol jump (`/`) | ➖ new | `index.html` shell | M2-C4 | ⬜ | ➖ | `shell_check`, `workspace_check` |
| System strip | ➖ new | `index.html` shell | M2-C2 | ⬜ | ➖ | `shell_check` |
| Surface Level control | ➖ new | System strip | M1-C6 → M2-C2 | ⬜ | ➖ | `shell_check` |
| Notification inbox | `#notifs` (dashboard panel) | Global inbox | M2-C6 | ⬜ | M3-C10 | `shell_check` |
| Toasts | `.notif` / toast helpers | Toast stack, max 3 + `+N more` | M2-C6 | ⬜ | ➖ | `shell_check` |
| Keyboard map + `?` overlay | scattered handlers | One map, one overlay | M2-C7 | ⬜ | ➖ | `shell_check` |
| Pilot panel | ➖ new | `index.html` shell | M8-C3 | ⬜ | ➖ | `pilot_check` |

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
| 3 | Input | bare `input`/`textarea` rules | M0-C8 | ⬜ |
| 4 | Dropdown / select | bare `select` rules | M0-C4 | 🟡 on semantic tokens |
| 5 | Segmented control | `.seg`, `.side-seg` | M0-C4 | 🟡 on semantic tokens |
| 6 | Card / panel → **instrument** | `.panel`, `.cards` | M3-C4 | ⬜ |
| 7 | Table | `.wl-*`, `.chain-wrap`, journal table | M4-C5 | ⬜ |
| 8 | Chart | `#chart-panel`, `CH.*` | M4-C3 | ⬜ |
| 9 | Navigation rail | `nav` | M2-C2 | ⬜ |
| 10 | Tabs (section rails) | ➖ | M6-C1 | ⬜ |
| 11 | Command palette | ➖ | M2-C3 | ⬜ |
| 12 | Search | `#wl-filter`, `#jf-sym` | M6-C4 | ⬜ |
| 13 | Badge | assorted inline | M2-C6 | ⬜ |
| 14 | Tag | `.chips` | M6-C4 | ⬜ |
| 15 | Status pill | `.pill`, `.badge` | M2-C5 | ⬜ |
| 16 | Tooltip | `title=` + `data-tip` | M7-C5 | ⬜ |
| 17 | Popover | `.help-menu` | M2-C5 | ⬜ |
| 18 | Modal | confirm dialogs | M4-C7 | ⬜ |
| 19 | Context menu | ➖ | M5-C2 | ⬜ |
| 20 | Empty state | `.empty`, `.dash-empty` | M3-C8 | ⬜ |
| 21 | Skeleton | skeleton loader rules | M3-C8 | ⬜ |
| 22 | Progress indicator | `#bt-status` | M6-C3 | ⬜ |
| 23 | Toast | `.notif` | M2-C6 | ⬜ |
| 24 | Banner | `#halt-banner` | M2-C2 | ⬜ |
| 25 | Notification inbox | `#notifs` | M2-C6 | ⬜ |
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
| Spacing scale | `--sp-1..6`, **0 uses** | 8 purpose-named steps, fully adopted | M0-C6 | ⬜ |
| Focus ring | single `:focus-visible` outline | dual ring + gap | M0-C8 | ⬜ |
| Input borders | none / `--grid` | `border.control` at 3.32:1 | M0-C8 | ⬜ |
| Elevation shadows | `--sh-1/2/3` | `--shadow-raised/overlay`, `--scrim` | M0-C1 values, M0-C4 adoption | 🟡 values landed |

---

## 7. Backend surfaces

All are re-presentations of existing computations — no new capability.

| Surface | Home | Milestone | Status | Tests |
| --- | --- | --- | --- | --- |
| Surface Level | `config/runtime.py` | M1-C1 | ⬜ | `tests/test_runtime.py` |
| Workspace symbol / timeframe / selection | `services/workspace.py` | M1-C2 | ⬜ | `tests/test_workspace.py` |
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
| `scripts/token_check.py` | M0-C7 | ⬜ | ⬜ |
| `scripts/motion_check.py` | M0-C9 | ⬜ | ⬜ |
| `scripts/shell_check.py` | M2-C10 | ⬜ | ⬜ |
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
| 2026-08-05 | — | Created, before M0-C1. Baseline measured. |
| 2026-08-05 | M0-C1 | UI V2 primitives landed in the token block: neutral ramp, radius, motion, elevation. Consumed by nothing. |
| 2026-08-05 | M0-C5 | Type scale converted to `rem` and renamed to the frozen roles; the 4 hardcoded sizes tokenised; large-text mode collapsed from nine overrides to one root change. **Deviation:** 13px is not a step in the frozen scale but 51 rules use it, so it survives as `--legacy-fs-md`, labelled legacy rather than blessed as a role. Collapsing it is a density change M0 cannot verify; each destination retires its own uses under rule T-1 as it is rebuilt. |
| 2026-08-05 | M0-C4 | 477 `var()` references repointed onto the semantic layer, property-aware; old token names deleted; `gd-contrast` rewritten. `guide_check.py` read `--muted` by name — both reads went empty and the check passed vacuously, so it now asserts the token is defined. **Debt:** the equity chart's dot uses the accent inside a plot area, which DV rule C-6 forbids; fix in M3. |
| 2026-08-05 | M0-C3 | Semantic layer defined, including status tints (the design system named status colours but not their tint backgrounds — the existing `*-soft` tokens needed a home) and the colour-vision alternate as a scoped override; its Settings toggle lands in M6. |
| 2026-08-05 | M0-C2 | The nine original surface/ink names became aliases onto the ramp; 306 use sites unchanged. `guide_check.py` had one assertion pinned to the literal page colour — rewritten to assert darkness, the property it is named for. |
