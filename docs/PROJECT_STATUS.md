# PROJECT_STATUS.md — structured project snapshot

A dashboard-style snapshot of the project, meant to be read in under a
minute. For the session-by-session narrative (why things are where they
are, exact stopping points, verification detail), see `PROJECT_STATE.md`.
For "what do I do right now," see `NEXT_SESSION.md`.

**Last verified:** 2026-08-06, **UI V2 · M2 — the shell, CLOSED**.
`verify.ps1` green across **all 16 gates**: full `pytest` suite
(**2636 tests**), ruff, HTML-id, **design-token** and **motion** checks, doc
checks, API contract check, pip check, `scripts/marketdata_stress.py` **88/88**
offline, and the six browser suites (`browser_check`, `chart_check`,
`marketdata_check`, `intelligence_check`, `guide_check`, `workspace_check`,
and the new `shell_check` — 33 assertions, the last three of which are the
rollback) in a real headless browser, **with the new shell on by default**.

**Two browser checks are known-flaky** and both are tracked in `TODO.md`:
`chart_check` is the only gate that fetches from **live providers**, and two of
its checks have failed and then passed at an identical commit. On that gate, the
correct first move is to re-run the same state — a bisect against it produced a
confidently wrong conclusion during V0.9.2-C3.

**Still not verified by hand:** no market-data adapter has ever been exercised
against its real API with a real key — all 2636 tests run against canned
payloads, so the response shapes are as *documented*, not as *observed*. The
84-item market-data manual QA (`docs/QA_MARKET_DATA.md`) has **not** been run.
The ISCC compile + real install/upgrade runs and a live end-to-end update
remain manual/CI (see `docs/AUTO_UPDATER.md`). See `PROJECT_STATE.md`.

---

## Current version

`0.12.0` — single source of truth: `optionspilot/__init__.py::__version__`
(pyproject derives it dynamically). Pre-1.0, actively developed. A `v*` tag
publishes both `OptionsPilot-Setup-vX.Y.Z.exe` (installer) and
`OptionsPilot-vX.Y.Z.zip` (portable) via GitHub Actions, and the installed app
updates itself from GitHub Releases.

`scripts/check_docs.py` compares the version stated in this section against
`__version__` and fails the build if they disagree. That check was added in
V0.9.0-C7 because this file claimed `0.5.0` for four consecutive releases:
the release workflow's tag gate only ever compared the *constant* against the
tag, and nothing looked at the prose, so a document nobody could trust passed
every automated check in the project.

## Current phase

**V0.9.2 — complete the service extraction. CLOSED 2026-08-04**, 12 commits
(`cefe4da`…C12). `ui/server.py` went from 1,892 to **1,629 lines**; the suite
from 2,247 to 2,414 (the suite is **2636 tests** today, after the release
automation that followed). The error hierarchy, four extractions
(`ChartService`, `MarketDataAdminService`, `TradingService`, `BacktestService`),
the guide moved into `services/`, services raising `ServiceError` and the
transport mapping it to a status (closing finding H-7), per-key idempotency
locking with request fingerprints (N-1/N-2), a line ceiling with a ratchet, and
the registry proven constructible *and usable* with no web framework loaded —
which found a real defect no static check could see. **No trading-behaviour
change.** Two deliberate status corrections: an internal defect is a 500 rather
than a confidently-wrong 404, and a bad timeframe is a 422 rather than a 502.
Per-commit reasoning: `docs/reports/V0.9.2.md`.

**Before that — V0.9.1, runtime & thread ownership** (11 commits), and
**V0.9.0 — verification floor. CLOSED 2026-08-02**, 11 commits
(`2707a01`…`e403da6`). The milestone adds no features and changes no trading
behaviour; it makes the build reproducible, the update path verifiable and the
CI gate meaningful, so that the refactoring milestones after it
(V0.9.1 – V0.9.5) can be trusted.

Delivered: the version constant reconciled with the released code (C1); a
dependency lockfile applied to CI *and* the release path (C2); ruff with a
narrow rule set and a documented 573-item backlog (C3); coverage measured at
**91.49%** and ratcheted (C4); the API contract check wired after three
milestones of never running, plus a test banning orphaned gate scripts (C5); a
two-platform CI matrix with Windows canonical (C6); build artifacts untracked
with the documentation reconciled (C7); SHA-256 checksums published per release
and enforced by the updater (C8); and client-side Authenticode verification —
a WinVerifyTrust verdict at the updater's OS boundary (C9-1) behind a
four-state policy in the validation gate (C9-2).

**Deferred by business decision, not incomplete:** C9-3 and C9-4, the
release-side signing pipeline and its operational documentation. Signing
production builds requires a purchased certificate and OptionsPilot is not yet
in public distribution, so it buys nothing today. The client half is complete
and enforcing; an *absent* signature is deliberately tolerated, so unsigned
releases install exactly as before and this can sit deferred indefinitely
without regression. Rationale, revisit trigger and the one hard ordering
constraint: `ROADMAP.md` ▸ Deferred.

**One item omitted rather than deferred:** `pip-audit` and Dependabot appear in
finding H-4's definition of done and never got a commit. Small, unblocked,
tracked in `TODO.md`.

Consequence to state plainly: the milestone's own DoD line *"a tag build
produces a signed installer plus checksums"* is **knowingly not met** — checksums
yes, signature no. V0.9.0 closes with that stated exception rather than by
quietly rewriting the criterion.

Plan of record: the V0.9 Engineering Specification, Revision 2.

**Before that — V0.8.2, production tray lifecycle and platform stability**, and
**V0.8.1, production hardening**. See `CHANGELOG.md`.

**Before that — V0.7.0, platform foundation & cross-platform architecture.** The
desktop UI stopped being the owner of application logic. `optionspilot/services/`
is the platform-independent application layer (portfolio, watchlist, intelligence
projections, notifications, workspace, and the persisted-object sync inventory),
`optionspilot/host/` puts every OS question behind a capability interface,
workspace state moved off `localStorage` onto the server, and seven new
architecture guards enforce the result. No trading-behaviour change, no UI
redesign, no test removed. Full design and the nine remaining platform blockers:
`docs/ARCHITECTURE-PLATFORM.md`.

**Previously — V0.6.0, the Trading Intelligence Engine — also awaiting review.** The analytical brain: one layer that turns everything
already recorded about completed trades into structured, evidence-backed insight
that every other part of the app consumes rather than recomputes. New subpackage
`optionspilot/intelligence/` (17 modules), a `/api/intelligence/*` surface, and
integration into the Dashboard, Coach, Journal and Learning tabs — all
projecting from one `IntelligenceSnapshot`. Imports `core` only, so it sits
*below* the coach. **No trading-behaviour change, no new dependency, no new tab;
never consulted before a trade.** Four defects found by self-audit, each with a
regression test. Design: `docs/TRADING_INTELLIGENCE.md`.

**Before that: V0.5.7 — the Market Data Control Centre.** The entire user-facing management layer over a
market-data subsystem that was already production-grade and completely
unsteerable: every real question a user has ("why isn't Finnhub being used?",
"is my key working?", "how many requests are left?", "what happens when Yahoo
dies?", "my cache looks wrong, now what?") was answerable only from
`logs/data.log`, and every setting needed a `config.yaml` edit and a restart.

New modules `data/control.py` (the administration surface — dashboard,
credentials, ordering, connection tests, eight maintenance jobs,
recommendations — composed *over* the registry, which never learns about it),
`data/credentials.py` (owner-only key storage; `environment → stored →
config.yaml`; a plaintext key leaves only through `resolve()`) and
`data/faults.py` (QA-mode fault injection firing inside `fetch_history`,
404-gated and off in every shipped build). New `/api/marketdata/*` surface, and
a Settings ▸ Market data panel with per-provider cards, a 21-column live
dashboard, three ordering modes, connection tests, maintenance with progress
and cancellation, automatic recommendations and a plain-English explainer.

Behaviour changes worth knowing: `enabled: false` now *constructs* and benches
a provider (one that is not constructed cannot be listed, explained or switched
back on); `ordering_mode` (static/hybrid/dynamic) supersedes `dynamic_ranking`;
`monitor.health_state()` is a derived human-facing state beside the `status()`
gate; a reorder rewrites priorities 10/20/30 because 10 rank points equals one
second of latency. **No trading-behaviour change, no new runtime dependency,
identical shipped defaults.** Five defects found by self-audit, each with a
regression test. Design: `docs/MARKET_DATA.md` §29–41.

**Before that: V0.5.5 Chart Production Certification.** A failure-elimination
pass over the whole chart
pipeline, provider to pixel. **No new features, no version bump, no
trading-behavior change.** Ten defects found by reproducing them, every one a
way the chart could fail while the backend, the diagnostics dashboard and the
entire test suite reported success. The headline: **the price axis had no
owner** — lightweight-charts turns `autoScale` off permanently on the first
price-axis drag and nothing ever turned it back on, so a pinned band outlived
every symbol switch and rendered other symbols' candles off-screen while the
volume histogram kept painting. That is the reported "IWM shows only volume"
bug, and why a restart fixed it. Also fixed: one 30-minute closing stub bar
condemning every Yahoo 1h frame as "wrong interval served"; a `NaT` timestamp
500'ing `/api/candles`; null-OHLC bars rendering as invisible whitespace under
`state="complete"`; an out-of-order payload collapsing to one candle; and a
render failure reporting `complete`. Detail: `docs/CHART_CERTIFICATION.md`.

**V0.5.6 (same branch)** then fixed the two bugs the user hit next: **every
symbol on 1D stuck behind "the cached bars failed validation and were
discarded"** (Yahoo and yfinance stamped the same trading day at different
instants, so the cache held two rows per day and the frame's spacing read 0.40
intervals — and because validation ran *after* the tier ladder had committed,
there was nothing to fall through to and Retry repeated it forever), and
**viewport/zoom corruption** (nothing defined a legal viewport, so a symbol
switch under Auto Follow inherited the previous instrument's zoom). A
1257-test suite at the time,
**65 chart checks**, 88 stress scenarios, plus a 110-cell browser matrix.

**Before that: V0.5.4 Enterprise Provider Expansion, on branch
`feature/providers` — awaiting user review.** Three keyed providers (Finnhub 40, Twelve Data 50,
Alpha Vantage 60) added behind the keyless chain, making a Yahoo-wide outage
survivable at **intraday** resolution for the first time. New:
`data/http_adapter.py` (shared keyed-HTTP base + the timezone contract) and
`data/ratelimit.py` (request budgeting, persisted across restarts). **With no
API keys configured the app behaves exactly as it did in V0.5.3** — keyed
providers report `missing_api_key` with a signup link and are never selected.
API keys are redacted by default everywhere they could be exported. Two
pre-existing defects fixed (`deepest_earliest` counting unusable providers; the
stale tier reporting zero bars). A then-1232-test suite, 88 stress scenarios. No version
bump, no trading-behavior change. Design: `docs/MARKET_DATA.md` §23–27.

**Before that: V0.5.3 Market Data Production Readiness, on branch `v3-ui` — awaiting user
review.** V0.5.2 built the market-data subsystem; V0.5.3 makes it *operable*.
Provider health has one owner (`data/health.py`), the provider chain is ordered
by **measured health** rather than a hard-coded constant (a cold system keeps
the documented order exactly), and there is a **Help ▸ Diagnostics** dashboard
with JSON/text export and per-request replay. Every operational knob — enabled,
priority, timeout, retries, breaker thresholds, quality floor, cache retention,
ranking on/off — moved into `config.yaml`'s `market_data:` section, so adding or
retuning a provider needs no code change. The consolidation surfaced and fixed
two real accounting bugs: a provider serving consistently-unusable bars was
recorded as *succeeding* and never tripped its breaker, and a demoted success
could never build a failure streak past 1. **No new provider, no version bump,
no trading-behavior change.** Design: `docs/MARKET_DATA.md` §13–22.

**Before that: V0.5.2 Market Data & Chart Reliability.** Chart history was
replaced rather than patched: a capability-driven,
multi-provider architecture (Yahoo chart JSON → yfinance → Stooq) with typed
provider failures, circuit breakers, semantic validation, durable self-healing
storage, and one diagnostics trace per request. The four conditions that used to
arrive as one empty array — `exhausted` / `empty` / `stale` / `failed` — are now
distinguished end to end, which is what lets the chart say "start of available
history" instead of retrying an impossible window forever. No trading behavior
changed. Design: `docs/MARKET_DATA.md`; manual QA: `docs/QA_MARKET_DATA.md`.

**Before that: V0.5.0 Auto-Updater 1.0.** Adds an
in-app self-updater (`optionspilot/update/`): a background launch-time check of
GitHub Releases, a professional update dialog (version diff, release notes,
size/ETA, Update Now / Remind Me Later / Skip), streamed installer download with
progress + cancel, pre-update backup, silent install, and restart. Settings ▸
Software updates (auto-check/frequency/beta) and Help ▸ Check for Updates…. No
trading behavior changed; user data is never touched by an update. The prior
milestone remains: `installer/OptionsPilot.iss` (Inno Setup) —
installs to `C:\Program Files\OptionsPilot` (admin, changeable dir), stable
`AppId` for **in-place upgrades**, Start Menu folder (app + Uninstall), optional
desktop shortcut (default checked), app icon everywhere, Programs-and-Features
registration (publisher/URLs/copyright/version), and an **uninstall-time** prompt
to also delete `%LOCALAPPDATA%\OptionsPilot` (default **No**). Because user data
lives in that separate root, upgrades/reinstalls never touch journal, coach,
settings, trades, watchlists, or backups. New `scripts/build_installer.ps1`
compiles it (stamping the single-source version); `release.yml` now installs Inno
Setup, builds the installer, and uploads it **alongside** the zip (zip retained).
+19 in `test_installer.py` (static config + pipeline guards). Full design in
`docs/INSTALLER.md`.

**Before that: V0.4.5 Professional Release Pipeline 1.0.** GitHub Actions `ci.yml`
(push/PR: tests + selftest + checks, reusable) + `release.yml` (tag `v*`: build →
package `OptionsPilot-vX.Y.Z.zip` → GitHub Release). Single-source `__version__`
(pyproject `dynamic`/`attr`); `scripts/package_release.ps1` + `release_notes.py`.
527-test suite (+7).

**Before that: V0.4.4 persistent storage & automatic data migration.** User data
fully separated from the binaries: new `core/paths.py::AppPaths` (single source
of truth for paths, root `%LOCALAPPDATA%\OptionsPilot`) + `core/migration.py`
(one-time lossless legacy import, backups, versioned framework). 520-test suite
(+28).

**Before that: V0.4.3 AI Coach 2.0 (phase 1).** Turned the Coach into a mentor,
additively (manual trades only): a per-trade 10-category scorecard
(`coach/categories.py`) + outcome snapshot, and a `build_dashboard`
(`coach/analytics.py`) with sub-scores, category trends, streaks, confidence-
scored pattern detection, an improvement timeline, and ≤5 auto-expiring action
items, served on `GET /api/coach → dashboard` and rendered in the coach tab.
492-test suite (+22); backward-compatible.

**Before that: V0.4.2 architecture audit + three refactors.** A read-only audit
(`docs/ARCHITECTURE-AUDIT-V0.4.2.md`) → three behavior-preserving improvements:
a shared `core/sqlite.py` foundation (`connect` + `PRAGMA user_version`
migrations) adopted by all five stores; `ui/server.py` import cleanup + public
`orchestrator.WINDOW_DAYS`; executable layering-guard tests. 470-test suite (+16).

**Before that: V0.4.1 Experience Engine integration (phase 3).** A centralized
`build_snapshot` captures the full deterministic decision context at AI entry
(feature-symmetric with coached manual trades), every *tradeable* AI signal
carries an advisory historical-similarity explanation, and a clean Experience
API (`/api/experience`, `/api/experience/similar`) exposes recent trades,
similar-trade lookup, and strategy/regime/session statistics. Storage schema v2
(indexed `market_regime` + SQL aggregates). Advisory only; nothing touches the
gate/risk/execution. 454-test suite (+30 at the time). Full design in
`docs/ROADMAP-V0.4-EXPERIENCE.md`.

**Before that: V0.4.0 Experience Engine (phases 1–2).** The
`optionspilot/experience/` subsystem — a rich, 100k-scalable `ExperienceStore`
(`data/experience.db`) recorded alongside the journal, plus the deterministic
Similarity Engine. The V0.3.5 distribution fix below also remains part of this
unmerged `v3-ui` branch.

**Before that: V0.3.5 distribution fix, on branch `v3-ui`.**
The packaged exe crashed after zip → GitHub → download → extract with
`RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize` (pywebview →
pythonnet → clr_loader). Root cause reproduced end-to-end: Explorer extraction
of a browser-downloaded zip leaves the Mark-of-the-Web (`Zone.Identifier` ADS)
on every file, and .NET Framework refuses to load MOTW-flagged managed
assemblies (HRESULT 0x80131515) — `Python.Runtime.dll` is the first casualty,
before any OptionsPilot code runs. Local builds carry no flag, which is why the
dev-side exe always worked. Fix: `optionspilot_app.py::unblock_bundle()` strips
the stream from the app's own files at startup (frozen Windows builds only,
before webview loads clr) — the programmatic equivalent of Explorer's
"Unblock". +3 tests (`TestUnblockBundle`); verified by MOTW-flagging a full
release copy outside the repo and launching to a working desktop window.

**Before that: V3.3.1 chart reliability investigation.** A pure root-cause investigation (no new features) of the
intermittent "switch symbols enough times → a chart loads blank and stays blank
until restart." Instrumented the lifecycle, reproduced under load + fault
injection, and traced each cause to a concrete mechanism: **no timeout on the
chart fetch** (a backend throttle backlog — yfinance serializes fetches through
one 0.15s lock, measured 10–15s latency under load — left the first-paint
spinner up forever); **superseded fetches never aborted** (a rapid switch burst
piled requests onto that throttle and starved the wanted symbol); **no backend
request timeout** (a hung Yahoo connection blocked the in-flight slot); a hung
history fetch left `historyLoading` stuck; malformed data threw an uncaught
"Value is null" from the library's own paint frame; and an unbounded backend
`_mem` cache. Fixes: bounded `AbortController` fetches (timeout → recoverable
error + abort-on-switch), backend `REQUEST_TIMEOUT`, `chEnsureMonotonic`
sanitizer + guarded rAF loop, and a bounded `MEM_CACHE_MAX`. Built on **V3.3
chart stabilization & market validation, on branch `v3-ui` — awaiting
user approval/merge.** A correctness sprint verified against LIVE market data
(reproduced during regular trading hours), not just tests. Made the chart match
a professional platform as closely as the free provider allows: adaptive live
refresh cadence, America/New_York time display, a candle countdown timer,
drawing creation previews, an overlay that tracks vertical (price-axis) moves,
and — the key root cause — a periodic refresh that no longer discards paged-in
history or moves the viewport (it merges the fresh recent window onto the
retained older bars). Two behaviours were identified as **yfinance provider
limitations, not app bugs** and documented: no streaming feed, and a forming
bar that arrives as a flat `volume=0` placeholder until it completes. Candle
data matches yfinance bar-for-bar (SPY/AAPL/NVDA). Built on **V3.2.2 viewport
ownership unification + Auto Follow, on branch `v3-ui` —
awaiting user approval/merge and market-hours validation.** Every bug
reported after V3.2.1 (random recentering, history intermittently failing,
losing viewport while scrolling) turned out to be another symptom of the
same conflict: the viewport had no single owner. This sprint audited every
`timeScale()` mutation, routed all of them through one `chMoveViewport`
controller, and found two real root causes — a history-arming race (fixed by
arming directly off the range-change subscription) and a deeper one:
lightweight-charts' `subscribeVisibleLogicalRangeChange` fires on a LATER
animation frame, not synchronously, so the old "reset the guard flag right
after the call" pattern closed its window before that callback ever arrived.
Also added an Auto Follow toggle (OFF by default, TradingView-style
"go to realtime" when ON), discovering along the way that `scrollToRealTime()`
itself runs a multi-frame animation that defeated a fixed-frame guard —
replaced with a single non-animated equivalent. Built on V3.2.1, which fixed
three release-blocker symptoms the user reproduced in the real app that
V3.2's (internal-state) tests missed: drawings still vanished across
timeframes (`chX` returned null for off-bar timestamps — now interpolates
the pixel between bracketing integer bars); timeframe switches lost the
user's place (now preserve the focal date, clamped to the closest candle);
and the viewport snapped on refresh while panned past newest (now preserves
the logical range). Built on V3.2, which finished the chart subsystem: a
timeframe-independent drawing engine
(visibility policy, `createdTf`, `source`, `meta`; one `chAddDrawing` API for
user/AI/replay), a TradingView-style **Ray** tool, and **Extended Hours**
(pre/after-market candles via yfinance `prepost`, session tags + shading,
persisted toggle — display-only, trading path stays RTH-only). Preceded by
RC1–RC3 (chart stabilization: toolbar, stale-banner
flapping, tf-switch tiny-zoom, viewport ownership). Further back:
**V2 rewrite,
post-V2-4.** The original 8-phase v1 roadmap (foundation
through hardening) is complete and stable. V2 layers a professional desktop
trading-platform experience on top: watchlist management, a full manual
paper-trading order book, AI Mode vs. Human Mode with a deterministic trade
coach, and an interactive chart workspace. V2-4's tractable scope (chart
drawing tools + trade lines) shipped 2026-07-17; V2-5 (replay engine) and
V2-6 (journal/improvement dashboard) are not started.

## Completed milestones

| Milestone | What it shipped | Status |
|---|---|---|
| v1 Phases 1–8 | Full analysis suite, AI decision engine, risk-gated paper broker, journal/learning/backtester, orchestrator + notifications, desktop UI, hardening (soak harness, TradingView webhook, broker registry stubs) | Committed, stable |
| Trading modes (2026-07-14) | Conservative (fixed bar) vs. High-Risk (setup-quality-adaptive bar) `trading_mode` | Committed, stable |
| V2-0 — Stabilize | Watchlist manager (autocomplete, presets, favorites, pin/reorder), `RuntimeSettings` overlay, in-app trading-mode toggle | Committed, stable |
| V2-1 — True desktop app | `--windowed` no-console PyInstaller build, app icon, single-instance guard, windowed-safe logging | Committed, stable |
| V2-2 — Manual trading engine | `OrderManager` (market/limit/stop/take-profit/trailing), Trade tab, account metrics | Committed, stable |
| V2-3 — AI Mode vs. Human Mode | `operating_mode` axis, manual-trade reconciliation loop, `TradeCoach` (process-scored reviews), Coach tab | Committed, live-verified, exe rebuilt |
| Performance & polish pass | Scan cycle 14.9s → ~0.1s warm, non-blocking scans, brokerage-style UI redesign | Committed, stable |
| V2-4 — Chart workspace | Vendored lightweight-charts, `/api/candles`, five-timeframe chart with indicator overlays/subpanes, drawing tools (level/trend/fib/zone/note), position/order trade lines, trade-from-chart | Committed, live-verified |
| Documentation & AI framework | `PROJECT_STATUS.md`/`ROADMAP.md`/`ARCHITECTURE.md` (with diagrams)/`AI_CONTEXT.md`/`NEXT_SESSION.md`/`CONTRIBUTING.md` | Committed |
| Developer scripts & automation | `dev`/`test`/`verify`/`docs`/`build`/`release`/`clean` `.ps1` entry points, `check_html_ids.py`, `check_docs.py`, `browser_check.py`, `chart_check.py`, `bump_version.py` | Committed (`7373c51`) |
| V3-0 — Chart reliability | Root cause of blank charts fixed (empty-fetch cache poisoning, no stale fallback, uncaught frontend failures); never-blank canvas with loading/error/stale states, 30s zoom-preserving refresh | Committed on `v3-ui`, browser-verified |
| V3-1 — Design system | Type/spacing/elevation tokens, inline-SVG icon nav, 56px responsive rail, flex/grid min-width blowout fix | Committed on `v3-ui` |
| V3-2 — Dashboard redesign | 2:1 layout, AI-opportunities + watchlist-movers side rail, action-oriented empty states | Committed on `v3-ui`, live-verified with a real scan |
| V3-3 — Trade screen | ATM quick-picks, risk-vs-buying-power line, on-tab positions with close-prefill, B/S/+/−/Enter order keys | Committed on `v3-ui`, browser-verified |
| V3-4 — Settings redesign | Grouped searchable config cards replace the JSON dump; live-trading flags visibly locked | Committed on `v3-ui`, browser-verified |
| V3-5 — Analytics presentation | Coach first-run explainer, journal filters + cumulative P&L curve, backtest drawdown/exit-reason panels, learning weight-shift bars | Committed on `v3-ui`, real backtest run |
| V3-6 — Accessibility | Skip link, toast live region, `scope="col"` on all 51 headers, `aria-current`, `?` shortcut overlay | Committed on `v3-ui`, browser-verified |
| V0.7.0 — Platform foundation | `optionspilot/services/` (application layer + view models + sync inventory), `optionspilot/host/` (capability profiles + OS adapter), server-owned workspace, 7 architecture guards, `workspace_check.py` | Uncommitted, awaiting review |
| V3-7 — Pre-merge audit fixes | `CandleCache` thread-safety (the disk cache silently never worked in the threaded live app), chart auto-retry for failed first loads, `?`-overlay order-key guard | Committed on `v3-ui`, each fix individually verified |
| Packaging fix (2026-07-18) | Exe shipped without yfinance (lazy `importlib` import invisible to PyInstaller since `f1bae42`): `--collect-all yfinance`, new `selftest` CLI as a post-build bundle gate, `tests/test_packaging.py` dynamic-import guard | `61a2c60`, exe rebuilt + endpoints verified live |
| V3.1-1 — Chart reliability | Per-ticker failures root-caused: drawings no longer drive the price scale (`autoscaleInfoProvider:null`), `validate_candles` drops NaN/inf/≤0 bars + zeroes bad volume + logs, indicator/serialization `isfinite` guards, renderer try/catch surfaces errors | `b93eac9`, reproduced + fixed in a headless browser |
| V3.1-2 — Timeframes | 6 → 13 intervals (1m/2m/3m/5m/10m/15m/30m/1h/2h/4h/1d/1w/1mo), table-driven (`_TF_LABEL`/`_FETCH_SPEC`/`_WINDOW_DAYS`/`CANDLE_TTL`), completeness-enforced by a test | `0d2c870`, all 13 verified against the live provider |
| V3.1-3 — Infinite history | Inverted prepend merge fixed (was replacing the window); logical-index trigger; viewport/zoom/drawings preserved; loading/end pill | `98551e1`, 206 → 407 bars on scroll-left verified |
| V3.1-4 — Editable drawings | Overlay-canvas object model: select/drag/resize/color/width/lock/hide/duplicate/rename/delete, instant tool arming, v1→v2 migration | `917d0c9`, 16-check browser lifecycle green |
| V3.1-5 — Trade-tab chart | The one chart instance relocated into a collapsible Trade slot (symbol/tf/drawings/indicators shared), preference remembered | `edfe2bc`, 10-check browser sync green |
| V3.1-6 — Live updates + perf | `chSig` includes last-bar OHLCV (forming candle no longer freezes); `series.update()` fast path for trailing bars (no flicker, no reflow) | `5e04506`, simulated intrabar tick, zero view jump |
| V3.1-7 — Chart test suite | `scripts/chart_check.py` 19-check headless-browser regression suite wired into `verify.ps1`; 10 tickers × 13 timeframes = 130/130 | `2bcb84a`, 19/19 green |
| V3.1 RC1 — Stabilization polish | Dead-code removal, `safeParse` localStorage-corruption guard, refresh-mid-interaction + wake refreshes, bounded LRU payload cache, WS frame-parse guard + reconnect-contract test; +2 chart_check checks (21) | `3a56145`, 21/21 browser + 374-test suite green |
| V3.1 RC2 — Final chart audit | Drawing-toolbar actions fixed (capture-phase deselect); market-aware stale banner (`market_open` in `/api/candles`); Reset-view / Go-to-latest + stranded-viewport recovery; single-owner viewport (one-way pane sync kills random jumps on indicator toggle); +6 chart_check checks (27) + 2 backend tests | `6f3643d`, 27/27 browser + 376-test suite green |
| V3.1 RC3 — Final release blockers | Toolbar "still broken" root-caused to a STALE EXE (source fixed since RC2) → exe rebuilt; banner-flapping fixed (high-water mark: warn only when genuinely behind); timeframe-switch tiny-zoom fixed (single-owner viewport, fit on switch); stuck loading-overlay/skeleton-legend on rapid switch fixed; real-mouse toolbar test + anti-flap + tf-zoom checks (29) | `60f16a4`, 29/29 browser + 376-test suite green |
| V3.2 — Drawing engine + Ray (PARTS 1/2/5) | Timeframe-INDEPENDENT drawing model (visibility policy, `createdTf`, `source`, `meta`; v1/v2→v3 migration so old drawings stop vanishing on a tf switch); one `chAddDrawing` API for user/AI/replay; TradingView-style **Ray** tool (two-click, infinite one-way extension) reusing the existing edit machinery | `62cbcb4`, browser-verified, chart_check 9b |
| V3.2 — Extended Hours (PART 4) | yfinance `prepost` feasibility confirmed; `extended_hours` display-only flag threaded provider→cache→payload (trading path stays RTH-only); `data/sessions.py` classifier; per-bar session tags + pre/after-market shading + persisted "Ext" toggle (no-op on daily) | `409cfc0`, 31/31 browser + 388-test suite green |
| V3.2.1 — Critical chart regression fixes | Drawings render on every tf (`chX` interpolates between bracketing integer bars — `logicalToCoordinate` rejects fractional); tf switch preserves the focal date (`chCaptureFocal`/`chApplyFocal`, clamp to closest candle); refresh no longer snaps the viewport (preserve LOGICAL range); root cause: `setData` triggered a mid-switch history load — guarded. Tests now assert rendered coordinates/viewport, not internal counts | 0.3.1, 33/33 browser + 388-test suite green |
| V3.2.2 — Viewport ownership unification + Auto Follow | Single `chMoveViewport` controller for every programmatic move; history-arming race fixed (arm off the range-change subscription); deeper root cause found — the subscription fires on a later animation frame, so the guard-reset needed deferring, not just re-timing the arm; new Auto Follow toggle (OFF default, persisted, disabled by manual pan, re-enabled by Latest); `scrollToRealTime()`'s multi-frame animation replaced with a non-animated `chScrollToLatest()` | 0.3.2, 36/36 browser + 388-test suite green |
| V3.3 — Chart stabilization & market validation | Live-verified during trading hours. Adaptive refresh cadence (~7s intraday, forming candle no longer updates in 30s chunks); America/New_York x-axis/crosshair/timer (labels formatted via Intl, timestamps unchanged); candle countdown timer; drawing creation preview (rubber-band); overlay rAF sync loop (drawings track vertical price-axis moves, no snap); **root cause** — periodic refresh merged (`chMergeRefresh`) instead of replacing, so paged-in history + viewport survive a refresh. yfinance limits documented (no streaming; forming bar V=0 until close). Candle correctness matches yfinance bar-for-bar | 0.3.3, 41/41 browser + 388-test suite green |
| V3.3.1 — Chart reliability investigation | Root-caused the intermittent "blank until restart": **no fetch timeout** (backend throttle backlog / hung upstream left the first-paint spinner up forever) → bounded `AbortController` (timeout → recoverable error, not permanent spinner); **superseded fetches not aborted** (rapid-switch pile-up starved the wanted symbol) → abort-on-switch; backend `yfinance.history()` `REQUEST_TIMEOUT`; hung history left `historyLoading` stuck → timeout; uncaught "Value is null" on non-monotonic data → `chEnsureMonotonic` + guarded rAF loop; unbounded `_mem` → `MEM_CACHE_MAX`. No new features | 0.3.4, 44/44 browser + 388-test suite green |
| V0.4.0 (phases 1–2) — AI Experience Engine | New `optionspilot/experience/` subsystem: rich, expandable, 100k-scalable `ExperienceStore` (`data/experience.db`, indexed columns + JSON payload + `user_version` migrations) recording a `TradeRecord` superset alongside the journal; deterministic `SimilarityEngine` (weighted distance → win rate / return / failure mode / **advisory** calibrated confidence). Calibration advisory-only (deterministic scorer unchanged); exploration deferred to a future orthogonal `learning_mode` axis. Backend-only; best-effort recording never touches the trading path | 0.4.0, 424-test suite green (+32) |
| V0.4.1 (phase 3) — Experience Engine integration | Centralized `build_snapshot` captures the full AI decision context at entry (feature-symmetric with coached manual trades); advisory historical-similarity explanation on every tradeable signal; Experience API (`recent`/`similar_trades`/`statistics`/strategy·regime·session stats/failure·success patterns) over `GET /api/experience[/similar]`; storage schema v2 (indexed `market_regime` + SQL aggregates). Advisory only — nothing touches gate/risk/execution | 0.4.1, 454-test suite green (+30) |
| V0.4.2 — architecture audit + refactors | Read-only audit (`docs/ARCHITECTURE-AUDIT-V0.4.2.md`) → three behavior-preserving improvements: shared `core/sqlite.py` foundation (`connect` + `user_version` migrations) adopted by all five stores; `ui/server.py` import cleanup + public `orchestrator.WINDOW_DAYS`; executable layering-guard `test_architecture.py`. No behavior change | 0.4.2, 470-test suite green (+16) |
| V0.4.3 — AI Coach 2.0 (phase 1) | Per-trade 10-category scorecard (`coach/categories.py`) + outcome snapshot on each review; mentor dashboard (`coach/analytics.py`): sub-scores, category trends, streaks, pattern detection w/ confidence, improvement timeline, ≤5 auto-expiring action items; `GET /api/coach → dashboard` (cached) + coach-tab UI. Manual trades only, additive, backward-compatible | 0.4.3, 492-test suite green (+22) |
| V0.4.4 — persistent storage & migration | `core/paths.py::AppPaths` (single source of truth; root at `%LOCALAPPDATA%\OptionsPilot`, `OPTIONSPILOT_HOME` override) + `core/migration.py::initialize_storage` (one-time lossless legacy import: timestamps preserved, verified, never overwrites newer/deletes source; marker; backups; empty versioned framework). Bootstrap/Orchestrator/UIServer/selftest wired through it. No behavior change | 0.4.4, 520-test suite green (+28) |
| V0.4.5 — Professional Release Pipeline 1.0 | GitHub Actions `ci.yml` (push/PR: tests + selftest + checks, pip-cached, reusable) + `release.yml` (tag `v*`: reuse CI → tag/version guard → build → package `OptionsPilot-vX.Y.Z.zip` → GitHub Release). Single-source version (`__version__` via pyproject `dynamic`/`attr`). `scripts/package_release.ps1` + `release_notes.py`; placeholder `LICENSE`; unwired Inno Setup installer template. No behavior change | 0.4.5, 527-test suite green (+7) |
| V0.4.6 — Professional Windows Installer 1.0 | Completed `installer/OptionsPilot.iss` (Inno Setup): installs to `C:\Program Files\OptionsPilot` (admin), stable AppId for in-place upgrades, Start Menu (app + Uninstall) + optional desktop shortcut, app icon everywhere, Programs-and-Features registration, uninstall-time "remove my data?" prompt (default No). `scripts/build_installer.ps1` + `release.yml` now build/upload `OptionsPilot-Setup-vX.Y.Z.exe` alongside the zip. No behavior change | 0.4.6, 546-test suite green (+19) |
| V0.5.2 — Market data & chart reliability | Chart history replaced, not patched. New under `optionspilot/data/`: `capabilities` (per-interval depth **measured from now** — the primary root cause was the old clamp measuring from the request's *end*), `adapter` (`HistoryAdapter`; adapters raise typed errors instead of returning empty frames), `yahoo_provider` (v8 chart JSON over urllib, now primary — it reports *why* it refused), `yfinance_adapter`, `stooq_provider`, `legacy`, `registry` (ordering, pre-network eligibility, circuit breakers + half-open recovery), `service` (`MarketDataService` tier ladder; distinguishes `exhausted`/`empty`/`stale`/`failed`), `quality` (semantic validation + report), `diagnostics` + `/api/diagnostics/marketdata`. `cache.py` rebuilt as durable storage (atomic, integrity-checked, corruption-quarantining, versioned, provider-attributed). Frontend: explicit load state machine + honest "start of available history". Also fixed: a history-paging request poisoning the live memo (QQQ 1d returned ONE candle), a corrupt cache.db crashing startup, and a history prepend restoring a mid-drag viewport. No trading-behavior change | 0.5.1, 880-test suite green (+229); chart_check 44 → 49 and green end to end |
| V0.5.0 — Auto-Updater 1.0 | New self-contained `optionspilot/update/` subpackage (core+stdlib only; `urllib`, no new dep): SemVer ordering, GitHub Releases client (installer asset only), checker (channel/frequency, never raises), streamed downloader (progress/cancel, atomic finalize), validation (size/hash/Authenticode-ready), installer launcher (mandatory `pre-update` backup → `/VERYSILENT` install → restart), `UpdateService` state machine. `/api/update/*` endpoints; launch-time background check gated on `run_loop`; prefs in `RuntimeSettings` (`updates` key). Frontend: Settings ▸ Software updates, Help ▸ Check for Updates…, update dialog. Verified offline via fakes | 0.5.0, 651-test suite green (+105) |

## Features complete

- Multi-timeframe technical/structural/smart-money analysis library (pure functions)
- AI decision engine: confluence scoring → gate → contract selection → trade planning
- Risk-gated paper execution, identical enforcement for AI and manual entries
- SQLite trade journal, bounded/auditable learning system (AI trades only)
- Event-driven backtester sharing the live engine code
- Desktop app: FastAPI + pywebview + PyInstaller, single-file frontend
- Full manual paper-trading order book (market/limit/stop/take-profit/trailing, DAY/GTC)
- AI Mode / Human Mode toggle, deterministic post-trade coaching with a 14-tag mistake taxonomy
- Interactive chart workspace: candles/volume, indicator overlays, drawing tools (5 types), alias-safe cached revisits, automatic left-edge history backfill, position/order trade lines
- Watchlist manager with a bundled 12k-symbol offline directory
- TradingView inbound webhook (scan-trigger only, never places an order)

## Features in progress / partially built

- **V2-4 remainder**: the full three-panel workspace layout (top bar / right sidebar / bottom panel) and multi-chart layouts are explicitly deferred — not started, no code exists.
- **V2-6 scope overlap**: the Coach tab's `CoachProfile` already covers *some* of V2-6's "improvement dashboard" intent (recurring mistakes, score trend, win rate by setup quality) — the full V2-6 spec (chart-context snapshots, notes/emotions fields, journal filtering UI) is not built.

## Known limitations (deliberate, documented — not bugs)

- Free market data is delayed (up to ~15 min for some symbols; liquid ETFs/large-caps measure ~1 min behind) and **intraday history is genuinely shallow**: ~7 days of 1m, ~59 days of 5m/15m/30m, ~729 days of 1h, unlimited daily. Since V0.5.2 these are *measured* limits declared in `data/capabilities.py`, the chart states them plainly when a scroll-back reaches one, and an out-of-depth request costs zero upstream calls (`scripts/marketdata_probe.py` re-measures them). Deeper intraday history requires a paid feed — see `docs/MARKET_DATA.md` §4.
- **`yfinance` serializes all requests through one process-wide throttle** (0.15s min interval, single lock), which under concurrent load pushed fetch latency to 10–15s+. V0.5.2 demoted it to the *secondary* provider behind a direct `urllib` call to Yahoo's chart JSON, which has no such global lock: 24 concurrent live chart loads now complete in **~0.5s with zero blanks**. The throttle still applies whenever the yfinance fallback is actually used, and to option chains/quotes, which still go through it.
- **yfinance is poll-only (no streaming/websocket feed)** and returns the *current forming bar* as a flat placeholder with `volume=0` until it completes. So the chart cannot build the forming candle tick-by-tick like TradingView — it advances as fast as we poll (~7s intraday, V3.3), and the just-forming bar shows no intrabar volume/range until it closes. Completed bars match yfinance to the cent/share. A true real-time forming bar requires a **streaming provider**; the smallest change to support one is a new `MarketDataProvider` adapter that pushes bar updates over the existing `/api/candles` WebSocket path (the frontend already applies trailing-bar updates via `chTailUpdate`), so no chart rewrite is needed. See `docs/AI_HANDOFF.md`.
- No historical option-chain data exists for free; the backtester reconstructs option prices via Black-Scholes.
- Manual/working orders evaluate once per scan cycle against fresh quotes — no intrabar/tick simulation.
- The coach infers behavioral tags (revenge trading, chased entry) from observable timing patterns, not literal intent.
- No live-broker implementation exists anywhere — this is the core safety property of the system, not a gap to close casually (see `CLAUDE.md`).
- **Releases are not Authenticode-signed**, so Windows SmartScreen warns on download and the UAC prompt names an unknown publisher. Deferred by business decision while the app is not publicly distributed (`ROADMAP.md` ▸ Deferred). Download integrity is covered independently by the SHA-256 manifest published with every release since V0.9.0-C8; the updater *does* verify signatures and refuses an invalid one, it simply never sees a valid one to prefer.

## Known bugs

None open. **Fixed in V0.8.2** (independent audit of V0.8/V0.8.1; each found by
reading the platform's own source, each covered by a regression test verified
against the old code):

1. **Clicking X froze the app.** pywebview binds `closing` as
   `Event(should_lock=True)`, so the handler runs on the WinForms message pump.
   `on_closing` called `evaluate_js`, whose WebView2 continuation is scheduled on
   that same pump behind an untimed `semaphore.acquire()` — an unbreakable
   deadlock on the branch a fresh install takes by default. It also ran the whole
   shutdown (up to 7s of thread joins) and a re-entrant `window.destroy()` there.
   The handler now decides and returns; the work runs on a worker.
2. **`Restart` was broken**: the successor was spawned before the single-instance
   port was released, so it lost the race to its own parent.
3. **A frozen build relaunched itself with its own path as `argv[1]`.**
4. **Two implementations of the single-instance mutex** (`ui/desktop.py` and
   `host/adapter.py`), each with its own copy of port 8786.
5. **The one maintenance slot admitted several workers** — a check-then-act,
   measured admitting 8 of 8 concurrent requests. This also caused the
   intermittent `test_progress_is_reported_and_ends_at_one` failure.
6. **A WebSocket client stalled every HTTP request** in the process: the
   `async def` v1 socket handler called the lock-taking `status_payload()`
   directly on the event loop.
7. `hello.accepted` sent `"timestamp": null`; the idempotency store held an open
   SQLite write transaction across a network call; `tracemalloc` kept ten stack
   frames per allocation to feed a field that never reads them.

Defect 1 was **reproduced and then re-verified on the real stack** (real
uvicorn + `UIServer` + pystray + pywebview/WebView2, a real `WM_CLOSE`, and
`SendMessageTimeout(SMTO_ABORTIFHUNG)` as the responsiveness probe): on the
default branch the old handler left the pump dead for the whole 40 s budget with
the window never closing; the repaired handler stalls the pump **0.0 s** and
closes in 1.14 s on the `exit` branch. **Outstanding:** nobody has clicked the
button by hand, and the audit environment's windows are not on the interactive
desktop, so the visual symptom itself was never on screen — only the pump
condition that causes it.

**Fixed in V0.7.0:** `/api/learning` read its `WeightStore` from a
CWD-relative `Path("data")`, so the Learning tab reported the wrong learned
weights on every real install (the `effective` column came from the live scorer
and was right, which is what hid it). Regression test verified against the old
code; `tests/test_architecture.py::test_no_cwd_relative_storage_paths` forbids
the class. **Fixed in V0.6.1** (both found by the new `scripts/guide_check.py`,
both with a check that fails without the fix): (1) the Coach's "learn the app"
panel was hidden by `display:none` when there was nothing to suggest but kept
its previous markup, leaving live clickable buttons for advice that had been
withdrawn; (2) the guided tour's first step highlighted the PAPER TRADING badge,
which is pinned to the foot of a full-height sidebar, so `scrollIntoView` threw
the page to the bottom of the Dashboard before the user had read a word — fixed
by retargeting the step and by scrolling only when a target is not visible *at
all* rather than merely off-centre.

**Fixed in V0.5.2** (each reproduced from evidence before any code
changed, each now covered by a regression test that fails without its fix):
(1) intraday history depth was measured from the *request's end* instead of from
*now*, so every scroll-back into older intraday data 422'd upstream, returned an
empty frame, and was retried on every subsequent scroll — forever;
(2) a history-paging request shared the live-window memo key and overwrote it, so
a subsequent live load rendered the sliced overlap — observed as **QQQ 1d showing
a single candle from nine months earlier**, `outcome: memo`, with no error
anywhere; (3) the shipped depth caps for 5m/15m/30m/1h were each one day *past*
Yahoo's real cliff, so a boundary request looked like an outage; (4) a corrupt
`cache.db` crashed the app during `Orchestrator` construction, and leaked its
Windows file handle so the file could not even be quarantined; (5) a history
prepend restored a viewport captured mid-drag, yanking on-screen bars.

Previously fixed in-session (2026-07-18, packaging-fix session): the
packaged exe shipped without yfinance — every chart/quote/chain request
failed with "No module named 'yfinance'". The performance pass (`f1bae42`)
had made the yfinance import lazy via `importlib.import_module`, which
PyInstaller's static analysis cannot see, so every exe built since then
silently omitted it. Fixed with `--collect-all yfinance` in
`scripts/build_exe.ps1`, a `selftest` CLI command the build now runs
against the fresh exe (fails the build on an incomplete bundle), and
`tests/test_packaging.py` (fails the suite if any dynamic third-party
import isn't collected by the build script). One pre-existing limitation
noted while verifying: `OptionsPilot.exe serve` (the windowed exe running
the browser-serve subcommand) never binds its port — desktop `ui` mode
and dev-repo `serve` both work; tracked in `TODO.md`.

Fixed earlier (2026-07-17, automation session): the
`/favicon.ico` 404 (the one remaining browser console error from the prior
session), found immediately by the new `scripts/browser_check.py`'s first
real run. Fixed the same session before it: a halted paper account could
still place a manual market buy because `UIServer.place_order` never
called the risk preflight that existed but wasn't wired up.

## Current priorities

1. **Begin UI V2 · M2 — the shell.** The next milestone and the highest-risk
   one in the programme: a new frame, nav rail, system strip, command palette
   and notification inbox over six destinations that host the **existing**
   section markup unchanged, behind `ui.shell_v2` (default off until C11). It
   breaks 38 `data-tab` references across six browser suites and 18 more inside
   `index.html`'s guided tour, all at once — C9 exists to migrate them once per
   file rather than once per reference. See `ROADMAP-UI-V2.md` §6 and §12.
2. **Cut V0.11.0** when M2 is ready, or sooner. M0 and M1 are both complete and
   unreleased; M0 was held back deliberately because it contains nothing a user
   can see, so one release is meant to carry both.
3. **Decide where `pip-audit` + Dependabot go** — V0.9.0 scope that never got a
   commit and still has none.
3. **User review of the uncommitted milestones.** V0.6.0, V0.6.1 and V0.7.0 are
   built and verified but not committed; `docs/TRADING_INTELLIGENCE.md`,
   `docs/ONBOARDING.md` and `docs/ARCHITECTURE-PLATFORM.md` are the review
   documents. The `v3-ui` branch merge to `main` is the same kind of call.
4. **Remaining `ROADMAP-V3-UX.md` items**: notification centre with persistence
   (H5), chart↔chain cross-links (N2), toast stacking (N4).
5. **The standing scope decision** once V0.9 completes: V2-5 (replay engine),
   V2-6 (journal/improvement dashboard), the deferred V2-4 workspace layout, or
   pausing feature work to accumulate paper-trading data.
6. Exe rebuild + smoke test — deferred until the V3 branch is approved.

## Current milestone (in progress)

**None.** UI V2 · M2 closed on 2026-08-06; **M3 — Home** is next and has not
started. The ten-milestone plan, its commit map and the six open design
decisions are in `docs/ROADMAP-UI-V2.md`; `docs/UI_MIGRATION_TRACKER.md` §10
carries the per-commit record. The sections below are the historical milestone
narrative, newest first.

### UI V2 · M2 — The shell (complete, `b380141`…`12510b3`)

Eleven commits. Five destinations plus Settings over the nine existing
sections, hosted **unchanged**: frame, nav rail, system strip, command palette
(`Ctrl+K`), symbol jump (`/`), Flight Status with the orthogonality sentences,
notification inbox with server-owned read state, a three-deep toast stack, one
keyboard map generated from the registry, and a Pilot scaffold. `shell_check.py`
is the 16th gate; its last three assertions turn the flag off and check the old
navigation comes back.

One registry (`DESTINATIONS`) feeds the rail, section rails, router, frame
title, palette, keyboard map, tour retargeting and the suites' navigation
helper. 53 `data-tab` references across six suites migrated to one shared
`scripts/shell_nav.py`.

Three defects found by the checks: the legacy `nav { width:200px }` element
selector applied to the new rail, so content painted over it below 1440px; a
palette command named a tutorial id that does not exist; and `Ctrl+K` was
already bound to the help centre, so both dialogs answered it.

### UI V2 · M1 — Workspace context and Surface Level (complete, `d665ad0`…`ace7a75`)

Seven commits. One symbol context across the chart, chain, ticket and backtest;
one timeframe; a server-owned expiry and contract selection; Surface Level
(Guided/Focused/Full/Pro) as a presentation-only third axis, device-local by
decision, with the option chain's column set as its first consumer. The browser
suite runs `UI_V2_DESIGN.md` §4.5's own test — type a symbol once, then chart,
chain and ticket it without typing it again — and grew from 21 assertions to 50.

Three real defects, all found by checks rather than by clicking: a slow
`/api/chain` response adopted its own symbol and dragged the whole workspace
back to it (it reproduced only when the suite beat the response, so it first
read as a flake); the selected contract had a server-owned home from C2–C3 that
the client never wrote to; and `#tk-spot` kept naming the previous symbol during
a load.

### UI V2 · M0 — Foundation (complete, `8c5586e`…`6f859bb`)

Nine commits, nothing visible. Three token layers with a one-way dependency,
the type scale in `rem`, the spacing scale adopted where it already matched, a
dual focus ring, and two new static gates taking `verify.ps1` from 13 to 15.
The gates found five defects that predate the work, including eight `var()`
references to properties this codebase has never defined. Four ratchets carry
the unpayable debt and may only fall.

### V0.9.1 — Runtime & thread ownership (complete)

**V0.9.1 — Runtime & thread ownership.** Make `BackgroundRuntime` genuinely the
one lifecycle owner, so pause, resume, shutdown and health reporting describe
reality: work lanes plus a worker pool so a long scan cannot starve a short
periodic task, stray threads brought under the runtime, the dead `_loop` /
tracemalloc monitor / startup HTTP poll deleted, a single-entry `exit()` guard,
and a `DesktopApplication` assertable without a GUI. Estimate 8–10 days, 11
commits. Concurrency defects are non-deterministic, so the exit criterion is a
30-minute soak, **not** a green suite.

**Committed — C1…C11 (complete)** (`d92de20`…): the starvation bug stated as a
failing test (C1); lanes and a bounded worker pool, inert by default (C2); the
market scan moved onto the worker lane (C3); real pause/resume/shutdown
semantics, including `pause_pending` because pause is not instantaneous (C4);
manual scans brought under one owner and a check-then-act race removed (C5);
the last two unowned jobs — the backtest and the intelligence refresh —
registered as on-demand worker tasks, with the pool bound raised from 2 to 4 to
match the registered workload (C6); `_DesktopController.exit()` made genuinely
single-entry (C7); the legacy `UIServer._loop` deleted (C8); and the
tracemalloc monitor removed (C9); and the launcher's HTTP self-poll replaced by
uvicorn's own readiness flag (C10); and `DesktopApplication` extracted from
`launch()` so the desktop wiring is assertable without a GUI (C11). **`ui/server.py`
and `intelligence/engine.py` now construct no threads at all**, asserted on the
AST, and **`BackgroundRuntime` is the only path to a trading cycle** — every
caller of `run_cycle_now` is named by a test.

C7 closed a measured defect rather than a theoretical one: `exit()`'s guard was
an unlocked check-then-act, and with eight concurrent callers **all eight ran
the shutdown and Restart spawned eight successor processes** — unrejected,
because releasing the single-instance lock is the first thing a restart does.

**V0.9.1 is complete — all eleven commits landed.** See `ROADMAP.md` ▸ V0.9.1 ▸
Commit map for the per-commit table with hashes. **The per-commit sequence is tabulated in `ROADMAP.md` ▸ V0.9.1
▸ Commit map** — keep it current in the same commit that lands a row.

**V0.9.2 is complete — all twelve commits landed.** The per-commit
table with hashes is `ROADMAP.md` ▸ V0.9.2 ▸ Commit map — keep it current in the
same commit that lands a row. Then V0.9.3 (a real API v1). Scope detail: the
V0.9 Engineering Specification, Revision 2, and `ROADMAP.md`.

**Not next:** Authenticode signing of releases. It was the natural follow-up to
the auto-updater and the client half now ships, but the release half is deferred
by business decision — see `ROADMAP.md` ▸ Deferred.

## Test count

**2636 tests, 100% passing** (`.\scripts\test.ps1`, ~95s).

### Backend coverage — 91.49% (baseline recorded V0.9.0-C4)

Measured with `pytest --cov` over `optionspilot/` only: **15,519 statements,
1,320 missed, 91.49% line coverage**. `fail_under = 91` in `pyproject.toml`
is the ratchet — CI fails on any drop below it. The threshold is the
measured number floored to an integer, never an aspiration; raise it when
coverage genuinely improves, and justify any lowering in a commit message.

Until V0.9.0-C4 this project had never measured coverage at all, so a
passing test count carried no information about how much of the code those
tests reach. It now does, and the answer is better than the audit assumed.
(Deliberately phrased without a figure: `scripts/check_docs.py` rewrites
every bare "N tests" claim in this file to the live count, so a number
quoted here as history would silently become a false statement about the
past on the next commit that adds a test.)

Line coverage, not branch coverage: enabling branches will lower the
percentage and must come with a re-measured threshold.

Weakest modules, as a queue rather than a complaint — `core/logging_setup.py`
(37%), `__main__.py` (42%), `data/yfinance_adapter.py` (60%), `ui/api_v1.py`
(65%), `update/ui.py` (68%), `ui/desktop.py` (72%). Two of these are already
scheduled: `ui/api_v1.py` is rewritten in V0.9.3, and `ui/desktop.py` gains
a testable `DesktopApplication` in V0.9.1.

Coverage runs in CI only, not in `scripts/test.ps1` or `verify.ps1` — it
costs ~30s on a ~95s suite, and a slower inner loop gets run less often.

Frontend coverage
is real but shallow: `scripts/check_html_ids.py` (static id-reference
check), `scripts/browser_check.py` (headless browser, every tab, zero
console errors), `scripts/chart_check.py` (65 chart, drawing and history
regressions), `scripts/marketdata_check.py` (46 checks over the Market
Data Control Centre — key management, ordering, maintenance, quota display,
accessibility and secret redaction, all offline) and
`scripts/intelligence_check.py` (54 checks over the Trading Intelligence UI —
score cards, evidence disclosures, unassessable-behaviour reasons, goals,
per-trade journal analysis and lesson triggers, all offline) and
`scripts/guide_check.py` (**135** checks over the guided onboarding, contextual
help, glossary, help search, empty states, accessibility and the order-ticket
guardrails — the spotlight assertions test that the highlight *intersects* the
element it names, not that a step declared one) and `scripts/workspace_check.py`
(**21** checks over the server-owned workspace — the canonical one wipes
`localStorage` in a real browser, reloads, and asserts the symbol and timeframe
that come back are the ones ON SCREEN) run automatically via
`scripts/verify.ps1` — the browser checks are focused regressions, not
exhaustive UI coverage (see `TODO.md`).

## Last verified date

**2026-07-22** (V3.3.1 chart reliability investigation) —
`.\scripts\verify.ps1` end to end: full pytest run (392/392), static
`$("id")` reference check, documentation consistency check, `pip check`, a
headless-browser smoke check across all 9 tabs (Playwright + system Edge)
with zero console errors, and the 44-check chart regression suite
(`chart_check.py`) — plus a 250-symbol-switch stress run (0 blanks / 0 console
errors), fault-injection recovery (7/7: empty / malformed / flapping), a
bounded-fetch timeout→recover check, and a memory-plateau profile.
