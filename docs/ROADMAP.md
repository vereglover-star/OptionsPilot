# OptionsPilot — Roadmap

This is the top-level, always-current roadmap. For granular per-phase
checklists and acceptance detail on the V2 rewrite, see `ROADMAP-V2.md`
(this file summarizes; that file itemizes). For exact dates, commit hashes,
and prose descriptions of what shipped, see `CHANGELOG.md`.

---

## Completed

### V0.9.0 — Verification floor (2026-08-02, committed `2707a01`…`e403da6`)

The milestone that makes the rest of V0.9 trustworthy. Every milestone after it
refactors live code, and a refactor is only as safe as the evidence that it
changed nothing — so this one built the evidence rather than a feature. The
version constant reconciled with the shipped code and a docs-version gate added;
a dependency lockfile applied to CI *and* the release path; ruff with a narrow
rule set over a documented 573-item backlog; coverage measured for the first
time (91.49%) and ratcheted; the API contract check wired after three milestones
of never being called by anything, plus a test that bans the orphan *class*; a
two-platform CI matrix with Windows canonical; 3,238 build artifacts untracked;
SHA-256 checksums published per release and enforced by the updater, with
`Assurance` reporting *how* a file was verified rather than only whether; and
client-side Authenticode verification (WinVerifyTrust at the updater's OS
boundary, behind a four-state policy in the validation gate). 2065 → 2158 tests.
No feature, no trading-behaviour change, no new runtime dependency.
**C9-3/C9-4 deliberately deferred — see Deferred below.** Full detail:
`CHANGELOG.md`; plan of record: the V0.9 Engineering Specification, Revision 2.

### V0.6.1 — Intelligent UX & interactive onboarding (2026-07-28, uncommitted)

Software that teaches itself. By V0.6.0 the backend was far more sophisticated
than the experience of using it — nothing missing, everything unexplained.
V0.6.1 adds a data-driven tutorial engine (11 walkthroughs, 52 steps; a step is a
selector, a sentence and how it advances, so a new screen's walkthrough is data
rather than code), a spotlight that leaves the page fully interactive so the user
drives the real controls, a 37-term plain-English glossary with adaptive hover
tips, a searchable help centre on `?`/`Ctrl+K`, per-screen Learn buttons,
teaching empty states, an app-wide reduced-motion switch with full keyboard and
screen-reader support, feature-aware tutorial suggestions in the Coach tab, and
**order-ticket guardrails** that make the three combinations `OrderManager.place`
refuses unassemblable. New `optionspilot/ui/guide.py` (pure domain layer) behind
`GET /api/guide` + `POST /api/guide/state`, with progress in `settings.json`
rather than localStorage. **No trading-behaviour change, no new dependency, no
new tab, no validation weakened.** Two defects found by the new suite and fixed.
1849 → 1908 tests; new 135-check `scripts/guide_check.py`. Design:
`docs/ONBOARDING.md`.

### V0.6.0 — Trading Intelligence Engine (2026-07-28, uncommitted)

The analytical brain, and the foundation the AI Coach is meant to become a
presentation layer over. Everything the app records about completed trades lived
in four unrelated stores with four aggregation paths and no answer to the
questions a trader actually asks. V0.6.0 collapses that into one pipeline:
`build_facts()` joins journal + experience + coach into a `TradeFact` once, ten
engines run over it, and the Dashboard, Coach, Journal and Learning tabs all
project from a single `IntelligenceSnapshot`. New subpackage
`optionspilot/intelligence/` (17 modules) covering a 38-metric registry, 22
behavioural detectors, automatic edge discovery over 19 dimensions with
false-discovery control, eight self-explaining composite scores, measurable
goals, 16 triggered lessons, a ranked action list derived entirely from evidence,
an improvement timeline, ten earned achievements, and weekly/monthly prose
reports. Imports `core` only, so it sits *below* the coach in the layering.
**No trading-behaviour change, no new dependency, no new tab; never consulted
before a trade.** Four defects found by self-audit (a grade-A score earned by an
absence of data, thirteen "patterns" from random noise, a circular dimension, and
`nan%` in the narrative). 1468 → 1849 tests; new 54-check
`scripts/intelligence_check.py` and `scripts/intelligence_benchmark.py`
(50,000 trades in 2.9 s, flat per-trade cost). Design:
`docs/TRADING_INTELLIGENCE.md`.

### V0.5.7 — Market Data Control Centre (2026-07-27, uncommitted)

The user-facing management layer over the market-data subsystem. V0.5.2–V0.5.6
built something production-grade and left its owner unable to see or steer any
of it: every real question ("why isn't Finnhub used?", "is my key working?",
"how many requests are left?", "what happens when Yahoo dies?") was answerable
only from `logs/data.log`, and every setting needed a `config.yaml` edit and a
restart. New: `data/control.py` (the administration surface, composed *over*
the registry), `data/credentials.py` (owner-only key storage, environment-first
resolution, masked everywhere but `resolve()`), `data/faults.py` (QA-mode fault
injection, 404-gated, off in every shipped build), the `/api/marketdata/*`
surface, and a Settings ▸ Market data panel with per-provider cards, a
21-column live dashboard, three ordering modes, connection tests, eight
maintenance actions with progress and cancellation, automatic recommendations
and a plain-English explainer. **No trading-behaviour change, no new
dependency, identical shipped defaults.** Five defects found by self-audit.
1257 → 1468 tests; new 46-check `scripts/marketdata_check.py`. Design:
`docs/MARKET_DATA.md` §29–41.

### V0.5.4 — Enterprise provider expansion (2026-07-26, uncommitted)

Three keyed providers — **Finnhub, Twelve Data, Alpha Vantage** — behind the
existing keyless chain, making a Yahoo-wide outage survivable at *intraday*
resolution for the first time. Each adapter is ~150 lines implementing four
methods; health, breakers, ranking, config, replay, benchmark and diagnostics
were all inherited, which is the V0.5.3 extensibility claim being cashed. New
machinery: `data/http_adapter.py` (shared keyed transport + the timezone
contract) and `data/ratelimit.py` (request budgeting — Alpha Vantage allows 25
requests/DAY, persisted across restarts, with budget pressure feeding the
ranking so load moves before a provider is spent). **With no API keys the app
behaves exactly as in V0.5.3.** Keys are redacted from every export. Two
pre-existing defects fixed. 1052 → 1232 tests; stress 65 → 88. Design:
`docs/MARKET_DATA.md` §23–27.

### V0.5.3 — Market data production readiness (2026-07-26, uncommitted)

V0.5.2 built the subsystem; this made it **operable**, as infrastructure work
rather than a feature. Provider health gained a single owner
(`data/health.py`), the provider chain is ordered by measured health instead of
a hard-coded constant (a cold system reproduces the old order exactly), and
**Help ▸ Diagnostics** answers "why did that chart look like that?" from one
page — with JSON/text export and per-request replay across every provider.
Every operational knob moved into `config.yaml`'s `market_data:` section, so
adding or retuning a provider needs no code change; the consolidation also
surfaced and fixed two real accounting bugs (a provider serving unusable bars
was recorded as *succeeding*, and a demoted success could never build a failure
streak). Also: cache intelligence, structured request logging, advisory
capability discovery, and a provider benchmark. **No new provider by design** —
the point was to make adding one cheap. 880 → 1052 tests; stress 41 → 65;
`chart_check` 49 → 52. Design: `docs/MARKET_DATA.md` §13–22.

### V0.5.2 — Market data & chart reliability (2026-07-26, uncommitted)

Chart history — the last inconsistent subsystem — replaced rather than patched.
A capability-driven, multi-provider architecture (Yahoo chart JSON → yfinance →
Stooq) with typed failures, circuit breakers, semantic validation, durable
self-healing storage, and per-request diagnostics. The four conditions that used
to arrive as one empty array (`exhausted` / `empty` / `stale` / `failed`) are now
distinguished end to end, which is what lets the chart say "start of available
history" instead of retrying an impossible window forever. 651 → 880 tests.
Design: `docs/MARKET_DATA.md`. Manual QA: `docs/QA_MARKET_DATA.md`.


### v1 — Original 8-phase build (2026-07-11, commit `40eb1ea`)

Foundation, analysis suite, AI decision engine, risk manager + paper
broker, journal/learning/backtester, orchestrator + notifications, desktop
UI, hardening (soak harness, TradingView webhook, broker registry stubs,
performance pass). Fully committed, tested, packaged. See `ROADMAP-V2.md`
"Phases" section header for the original phase-by-phase list if needed —
it predates that file and is preserved in git history.

### Trading modes (2026-07-14)

Conservative (fixed confidence bar) and High-Risk (setup-quality-adaptive
bar) `trading_mode`, with identical risk management underneath both.

### V2-0 — Stabilize

Watchlist manager (autocomplete against a bundled 12k-symbol directory,
presets, favorites, pin/reorder), `RuntimeSettings` overlay system, in-app
trading-mode toggle — all live, no-restart.

### V2-1 — True desktop application

`--windowed` no-console PyInstaller build, generated app icon,
single-instance guard, windowed-safe logging.

### V2-2 — Manual trading engine

`OrderManager` (market/limit/stop-loss/take-profit/trailing, DAY/GTC), the
Trade tab, account metrics. `Position.managed_by` separates AI-managed from
user-managed positions — this distinction is load-bearing throughout the
codebase (see `AI_CONTEXT.md`).

### V2-3 — AI Mode vs. Human Mode

The `operating_mode` axis (independent of `trading_mode`), the manual-trade
reconciliation loop, and `TradeCoach` — a deterministic, process-scored
post-trade review system with a 14-tag mistake taxonomy. Live-verified in a
real browser; the exe was rebuilt and smoke-tested the same day.

### Performance & polish pass (2026-07-16)

Scan cycle profiled and optimized end-to-end (14.9s → ~0.1s warm),
non-blocking `/api/scan`, brokerage-style UI redesign.

### V2-4 — Chart workspace

Vendored `lightweight-charts`, `/api/candles` (indicators from the same
`analysis/` code the engine trades with), a five-timeframe interactive
chart with EMA/VWAP/Bollinger overlays and synced RSI/MACD subpanes,
fullscreen, five drawing tools (horizontal level, trend line, fib
retracement, zone rectangle, bar note — all persisted per symbol or
symbol+timeframe in localStorage), position/order price lines on the
chart, and trade-from-chart deep links. **The three-panel workspace layout
and multi-chart layouts are explicitly deferred** — see "Deferred" below.

### V3 product-quality sprint, milestones 0–6 (2026-07-17, branch `v3-ui`)

A UX/reliability sprint, not a feature sprint — scoped by the full audit in
`ROADMAP-V3-UX.md`. Chart reliability root-caused and fixed (never-blank
canvas, stale-data fallback for display only, alias-safe symbol resolution,
cached payload reuse for instant revisits, automatic left-edge history
backfill, 30s zoom-preserving refresh), a design-token system + responsive
icon-rail nav, and redesigns of Dashboard, Trade (ATM quick-picks, risk
context, order-entry keys), Settings (structured cards replace the JSON
dump), the four analytics tabs, and an accessibility pass. Seven commits,
each browser-verified.
**On `v3-ui`, awaiting user review — not merged to `main`.**

---

## In Progress

**V0.9.1 — Runtime & Thread Ownership**, six of eleven commits in. V0.9.0
closed on 2026-08-02 with C9-3/C9-4 deliberately deferred (above). Scope, commit
sequence and acceptance criteria are in the V0.9 Engineering Specification,
Revision 2. See `NEXT_SESSION.md`.

C1…C8 have made `BackgroundRuntime` the single owner of every application
background workload: work lanes over a bounded pool (C2), the market scan (C3),
honest pause/resume/shutdown (C4), manual scans (C5), and the backtest plus the
intelligence refresh (C6); C7 then made `_DesktopController.exit()` genuinely
single-entry, which it was not — eight concurrent callers ran eight shutdowns
and spawned eight successor processes on Restart; and C8 deleted the legacy
`UIServer._loop`, a complete second scheduler that nothing called, leaving the
runtime as the only path to a trading cycle. Remaining: the startup HTTP poll
and the tracemalloc monitor removed, and a `DesktopApplication` assertable
without a GUI.

One item from V0.9.0's own definition of done was **omitted rather than
deferred**: `pip-audit` and Dependabot were named in finding H-4's DoD and never
received a commit. It is small and unblocked — tracked in `TODO.md`, and it has
now passed the point where it could be folded into V0.9.1's first commit, so it
wants a standalone one.

**V0.6.0, V0.6.1 and V0.7.0 are all built, verified and uncommitted, awaiting
the user's review.** V0.7.0 (platform foundation) is the most recent: the
application layer was extracted out of `ui/server.py` into
`optionspilot/services/`, the OS went behind `optionspilot/host/`, workspace
state moved server-side, and every durable object gained a declared
synchronization policy. It changes no trading behaviour and redesigns no UI.
Read `docs/ARCHITECTURE-PLATFORM.md` — §7 lists the nine remaining platform
blockers and §8 scores cross-platform readiness honestly.

**The `v3-ui` branch is awaiting the user's review/merge decision.**
Remaining audit items deliberately not built (see `ROADMAP-V3-UX.md`):
notification center with persistence (H5), chart↔chain cross-links (N2),
toast stacking (N4), and everything under "Long-term ideas." Beyond that,
nothing is actively in progress — each phase ships as a complete, tested,
documented unit before the next begins (see `CLAUDE.md`).

---

## Planned

### V0.9.1 — Runtime & thread ownership (in progress, C1…C8 committed)

Make `BackgroundRuntime` genuinely the one lifecycle owner, so pause, resume,
shutdown and health reporting describe reality rather than intent. Scope: work
lanes plus a worker pool so a long scan cannot starve a short periodic task;
stray threads brought under the runtime; the dead `_loop`, the tracemalloc
monitor and the startup HTTP poll deleted; a single-entry `exit()` guard; and a
`DesktopApplication` whose wiring is assertable without a GUI. Sequenced
*before* the service extraction on purpose — a service that owns background work
would otherwise be extracted against a broken ownership model and need redoing.
Out of scope: uvicorn/tray/`_defer` plumbing, orchestrator thread-safety, and
scan logic itself. Estimate 8–10 days, 11 commits. Concurrency defects are
non-deterministic, so a soak run — not a green suite — is the exit criterion.

---

The V2 phases below are listed in the order they appear in `ROADMAP-V2.md`; no
priority is implied beyond that ordering. Which one comes next is the user's
call.

### V2-5 — Replay engine

- Pick a historical day/session; future candles hidden server-side.
- Play / pause / step-one-candle / speed control.
- Separate replay paper account; orders fill against replay bars.
- `TradeCoach` reviews replay trades exactly like live ones.

### V2-6 — Journal & improvement dashboard

- Chart-context snapshot per trade (candle window + entry/exit markers,
  re-rendered on demand — the deliberate substitute for static
  screenshots, documented in `ROADMAP-V2.md`).
- Notes + emotions fields; filter by strategy/symbol/P&L/date/mistake.
- Improvement dashboard: win-rate trend, weaknesses, best hours/days/
  conditions, mistake frequency over time, recommended exercises.
- **Partial overlap already shipped**: the V2-3 Coach tab's `CoachProfile`
  already covers recurring mistakes, score trend, and win rate by setup
  quality. V2-6 is additive on top of that, not a rebuild — the remaining
  scope is chart snapshots, notes/emotions capture, and journal filtering
  UI specifically.

### V2-4 workspace remainder (optional, large)

The full three-panel layout (top bar / right sidebar / bottom panel) and
multi-chart layouts. Deferred as a deliberate, separate design decision —
the current single-chart-plus-toolbar Charts tab is a complete, usable
substitute, so this is a UI-restructuring project rather than a missing
feature.

---

## Deferred

Explicitly considered and pushed out, with the reason recorded so it isn't
re-litigated by accident:

- **Authenticode signing of release builds (V0.9.0-C9-3 and C9-4)** — deferred
  **by business decision, 2026-08-02. This is not unfinished engineering.**
  Signing production builds requires a *purchased* code-signing certificate, and
  since the CA/Browser Forum tightened its baseline requirements the private key
  for a publicly-trusted certificate must live on certified hardware or in a
  cloud signing service — so the cost is a recurring subscription plus a CI
  design committed to whichever provider is chosen. **OptionsPilot is not yet
  entering public distribution**, so that spend buys nothing today: the only
  thing a signature removes is the SmartScreen warning shown to users who
  download the installer, and there are none.

  What this does *not* mean: the work is not half-done and nothing is left in a
  broken state. The **client half shipped complete** in C9-1 and C9-2 — the
  updater asks Windows about every downloaded installer's signature and refuses
  one that is present and invalid, today, on every install. It is deliberately
  additive: an *absent* signature is tolerated (Phase 1), so unsigned releases
  install exactly as they always have and nothing regresses by leaving this
  deferred indefinitely. Integrity is independently covered by the SHA-256
  manifest from C8.

  What remains is release-side only — signing steps in `release.yml`, the
  `SignTool=` line already sitting commented in `installer/OptionsPilot.iss:75`,
  and the operational documentation. Roughly one engineering day, fully planned.
  **Trigger to revisit: a decision to distribute publicly.** At that point read
  the C9 implementation plan §1 *before* purchasing anything — the certificate
  type determines the CI design, and the common tutorial advice (a `.pfx` in a
  GitHub secret) is no longer permitted for a publicly-trusted certificate. The
  one hard constraint to carry forward: **`SHA256SUMS` must be generated after
  signing**, because signing changes the bytes and C8 now enforces that manifest.

  Phase 2 of the client policy (`REQUIRE_SIGNATURE = True`, planned for
  V0.9.3-C12) is deferred with it and **must not** be enabled while releases are
  unsigned — it would make every build uninstallable.

- **Stock/share (non-option) manual positions** — deferred from V2-2. Would
  need a new "stock leg" position shape and touch `broker/orders.py`,
  `PaperBroker`, and the Trade tab's chain/ticket UI (currently
  options-only).
- **A real live-broker adapter** (Alpaca's options paper API is the natural
  first candidate) — explicitly gated on sustained paper profitability.
  Building this without a direct, dedicated user request is against the
  project's core safety rule; see `CLAUDE.md`.
- **A paid market-data feed** (Polygon/Tradier) — the free yfinance
  provider is adequate for paper trading and strategy development; this is
  the documented upgrade path for serious intraday work, not a current need.
- **News / economic-calendar / sentiment evidence** — would be a new
  `ConfluenceScorer` evidence type; no design work started.
- **Portfolio-level risk** (correlated positions, sector exposure limits) —
  the current `RiskManager` reasons per-position only.
- **Candle cache for the live loop** (incremental fetch + merge) — the
  `CachedProvider` already made this low-urgency (warm cycles are ~0.1s);
  worth revisiting only if yfinance rate-limiting becomes a problem at a
  larger watchlist size.

---

## Long-term Vision

From the user's own framing of the project (see `AI_HANDOFF.md`): *"a
polished, professional desktop trading platform that combines the best
aspects of TradingView, Webull, and Thinkorswim, while adding an AI trading
coach that can both trade autonomously in AI Mode and teach me in Human
Mode."*

That vision has two structural pillars that are not up for casual
revision:

1. **Paper trading only, permanently, unless the user explicitly asks
   otherwise in a dedicated request.** The system is deliberately built so
   a live-broker adapter *could* slot into the existing `Broker` interface
   someday, gated behind sustained paper profitability and a two-flag
   opt-in — but building that adapter is a decision the user makes once,
   explicitly, not a natural next step to infer from "make it better."
2. **Deterministic, auditable logic over ML/LLM black boxes.** The scorer,
   the gate, and the coach are all hand-authored rule systems by design —
   this is what makes every trade decision fully reconstructable from logs
   and every coaching verdict explainable in plain English. An LLM call in
   the trading or coaching path would break that property.

Beyond the currently-scoped V2-5/V2-6 phases, longer-horizon ideas that
have been discussed but not scoped: a desktop app for other platforms
(macOS/Linux — currently Windows-only via pywebview/PyInstaller; see
`AI_CONTEXT.md` "Future desktop plans"), and no mobile plans exist or are
anticipated (the analysis engine is pandas/numpy-heavy and the UI assumes
a desktop-sized viewport; see `AI_CONTEXT.md` "Future mobile plans").
