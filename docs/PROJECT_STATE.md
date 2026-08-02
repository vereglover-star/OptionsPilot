# PROJECT_STATE.md — exactly where this project stands right now

Read `AI_HANDOFF.md` first if you haven't. This file is the "what's done,
what's next" tracker — keep it current as you work.

**Last updated:** 2026-08-02, on closing **V0.9.0 — the verification floor**
(branch `V3-ui`, committed `2707a01`…`e403da6`). 2065 → **2158 tests** (+93).
Full detail: **`docs/CHANGELOG.md`**, `2026-08-02 — V0.9.0`.

## Exact stopping point

**V0.9.0 is closed. Nothing is in progress. `verify.ps1` is green across all 13
gates** — full suite, ruff, HTML ids, docs consistency, API contract, `pip
check`, market-data stress 88/88, browser smoke, `chart_check` 65/65,
`marketdata_check` 46/46, `intelligence_check` 54/54, `guide_check` 135/135,
`workspace_check` 21/21. Coverage 91.56% over the 91 ratchet.

Eleven commits delivered C1–C8 plus C9-1 and C9-2, and fixed a `chart_check`
race found on the way out. The milestone added no feature and changed no trading
behaviour; it made the build reproducible, the update path verifiable and the CI
gate meaningful, so that V0.9.1–V0.9.5 — all refactors of live code — can be
trusted to have changed nothing.

**Two of the nine planned C9 commits are deliberately deferred, and the
distinction matters for whoever reads this next.** C9-3 (signing steps in
`release.yml` + `installer/OptionsPilot.iss`) and C9-4 (its operational
documentation) require a **purchased** code-signing certificate. OptionsPilot is
not entering public distribution, so that spend buys nothing today — the only
thing a signature removes is a SmartScreen warning shown to downloaders who do
not exist. **This is a business decision, not unfinished engineering.** The
client half shipped complete: the updater asks Windows about every downloaded
installer and refuses one whose signature is present and invalid. It is
deliberately additive — an *absent* signature is tolerated — so unsigned
releases install exactly as they always have and this can sit deferred
indefinitely without regression. Rationale, revisit trigger and the ordering
constraint that will bite whoever resumes it: `ROADMAP.md` ▸ Deferred.

Stated plainly rather than quietly rewritten: the milestone's own DoD line *"a
tag build produces a signed installer plus checksums"* is **knowingly not met**.
Checksums yes, signature no.

**One item was omitted rather than deferred**, and should not be allowed to hide
inside the deferral: `pip-audit` and Dependabot are named in finding H-4's
definition of done and never received a commit. Small, unblocked, in `TODO.md`.

**Next: V0.9.1 — runtime & thread ownership.** Not started. It is the blocking
prerequisite for the V0.9.2 service extraction, because a service that owns
background work would otherwise be extracted against a broken ownership model.
See `NEXT_SESSION.md` for the three things to hold on to before writing any of
it — chiefly that the exit criterion is a 30-minute soak, not a green suite.

## Previously: the exact stopping point at V0.8.2

The audit is complete and every automated suite is green: 2065 pytest tests,
`browser_check` (9 tabs, zero console errors), `chart_check`, `marketdata_check`
46/46, `guide_check` 135/135, `workspace_check` 21/21, `intelligence_check`
54/54, `marketdata_stress` 88/88, `check_html_ids` 254/254, `check_docs`,
`api_contract_check`, `pip check`.

**Ten defects were found in V0.8/V0.8.1 and fixed at the root.** The headline one
— the reported close-button freeze — is an unbreakable deadlock caused by calling
`window.evaluate_js` from pywebview's `closing` handler, which runs on the
WinForms message pump; WebView2 schedules the continuation that would release its
semaphore on that same pump. It is on the branch a fresh install takes by
default. The other nine (broken `Restart`, a frozen build relaunching itself
wrongly, a duplicated single-instance mutex, a maintenance slot that admitted 8
of 8 concurrent workers, a WebSocket handler that could stall every HTTP request,
a null protocol timestamp, a SQLite transaction held across a network call, and
tracemalloc storing ten frames per allocation for nothing) are listed in the
changelog with their root causes.

The close defect was **reproduced and then re-verified fixed on the real stack**
— real uvicorn, real `UIServer`, real pystray, real pywebview/WebView2, a real
`WM_CLOSE`, with `SendMessageTimeout(WM_NULL, SMTO_ABORTIFHUNG|SMTO_BLOCK)` as
the responsiveness probe. On the branch a fresh install takes by default, the old
handler left the pump dead for the whole 40 s budget with the window never
closing; the repaired handler stalls the pump 0.0 s, raises the dialog, and
closes in 1.14 s on the `exit` branch with `launch()` returning cleanly. It is
also covered by regression tests whose doubles reject the old code.

**Next recommended task: one manual click of the X button on a real desktop.**
What the audit could not cover is a human mouse click and the *visual* symptom —
this environment's windows are not on the interactive desktop, so the white title
bar and shell ghost frame were never on screen to observe, only the pump
condition underneath them. **Tray Restart** deserves the same one-minute pass:
its fix (release the single-instance lock before spawning the successor) is
unit-tested but has never been run end to end.

## Previously: V0.7.0 — platform foundation

1908 → **2027 tests** (+119); a new **21-check** headless-browser suite
(`scripts/workspace_check.py`). Full design: **`docs/ARCHITECTURE-PLATFORM.md`**.

**What it is.** OptionsPilot was already a client-server system that ships both
halves in one process; what it lacked was a boundary between the *application*
and the *desktop transport*. `ui/server.py` held FastAPI routing and, in the same
1,700 lines, the decisions about what a client is shown — which twelve of
thirty-eight metrics are a headline, how a maximum drawdown is computed, what
four buckets a pasted ticker list falls into. All correct, none of it reachable
without importing a web framework, and a second client asking "what is my max
drawdown" had two options: import FastAPI, or recompute it and slowly disagree.

V0.7.0 extracted that into `optionspilot/services/` (portfolio, watchlist,
intelligence projections, notifications, workspace, the persisted-object sync
inventory, frozen view models, `ServiceRegistry`), put every OS question behind
`optionspilot/host/` (capability profiles for `desktop`/`headless`/`web`/`ios`/
`android` + an adapter), moved workspace state off `localStorage` onto the server,
and added seven architecture guards — each verified to fail when deliberately
broken. **It moved code; it did not rewrite it.** `UIServer` kept every method
name and every wire shape. No trading-behaviour change, no new dependency, no new
tab, no UI redesign, no test removed.

**One shipped defect found and fixed:** `/api/learning` built its `WeightStore`
from a CWD-relative `Path("data")`, so the Learning tab had been reading a
different file from the engine for three milestones — on a real install one that
does not exist, in a dev checkout whichever `./data/learning/weights.json` sat
next to the process. The `effective` column came from the live scorer and was
right, which is what hid it. **Three more were introduced by this milestone and
caught before landing:** a bound method captured at construction (found by an
existing test), a default tab id of `dash` where the frontend uses `dashboard`,
and a declared `SyncDomain.WORKSPACE` with no entries.

**Nine remaining platform blockers** are listed honestly in
`ARCHITECTURE-PLATFORM.md` §7 — chart drawings are still `localStorage`-trapped,
there is no API versioning / error envelope / idempotency / authentication, `/ws`
is unenveloped, and notifications have no durable store.

## Before that — V0.6.1, intelligent user experience & interactive onboarding

1849 → a **1908-test suite** (+54); a new **135-check** headless-browser suite
(`scripts/guide_check.py`). Full design: **`docs/ONBOARDING.md`**.

**What it is.** By V0.6.0 the backend had become substantially more sophisticated
than the experience of using it: nothing was missing, everything was
unexplained. V0.6.1 is the layer that teaches the app to explain itself — a
data-driven tutorial engine (11 walkthroughs, 52 steps) that spotlights the
*real* controls while leaving the page fully interactive, a 37-term plain-English
glossary with adaptive hover tips, a searchable help centre on `?`/`Ctrl+K`,
per-screen Learn buttons, teaching empty states, an app-wide reduced-motion
switch, and **order-ticket guardrails** that make the three combinations
`OrderManager.place` refuses unassemblable, each correction stating what changed,
why, and what to do instead. **No trading-behaviour change, no new dependency, no
new tab, and no validation weakened** — the backend gate is untouched and still
authoritative.

New module `optionspilot/ui/guide.py` — pure and deterministic — owns state
validation, merge semantics and the rules that turn measured feature usage into a
suggested walkthrough, behind `GET /api/guide` and `POST /api/guide/state`.
Progress persists in `settings.json` rather than localStorage so a reinstall does
not greet a returning user as a beginner. The contract between the two
catalogues is **ids, never prose**, asserted in both directions by
`tests/test_guide.py::TestCatalogueContract`; and the layer recommends
*tutorials* from *feature usage* and never trading behaviour, which is
`intelligence/`'s job.

**Two defects found by the new browser suite**, each with a check that fails
without its fix: a recommendations panel hidden with `display:none` that kept
live clickable buttons for withdrawn advice, and a first tour step that threw the
page to the bottom of the Dashboard because it highlighted an element pinned to
the foot of a full-height sidebar.

## Before that — V0.6.0, the Trading Intelligence Engine

1468 → 1849 tests (+381); a new 54-check headless-browser suite
(`scripts/intelligence_check.py`) and a performance benchmark
(`scripts/intelligence_benchmark.py`). Full design:
**`docs/TRADING_INTELLIGENCE.md`**.

**What it is.** The app already knew a great deal about its trader, and knew it
in four unrelated places — `journal.db`, `experience.db`, `data/coach/*.json`
and `learning/weights.json`. Four stores, four aggregation paths, and no answer
to *what am I good at, what keeps costing me money, am I improving, what should
I learn next*. Worse, every new screen that wanted an answer computed its own,
which is the "two objects tracking one fact will drift" failure this codebase
had already paid for twice. V0.6.0 collapses that into one pipeline:
`build_facts()` joins the three sources into a `TradeFact` once, ten engines run
over it, and everything above projects from a single `IntelligenceSnapshot`.
**No trading-behaviour change, no new runtime dependency, no new tab, and the
engine is never consulted before a trade — `risk/manager.py` is still the only
gate.**

New subpackage `optionspilot/intelligence/` (17 modules), importing **`core`
only**: it reads journal/experience/coach records structurally rather than by
import, which keeps it *below* the coach so the coach can become a presentation
layer over it rather than a parallel analysis path.
`tests/test_architecture.py` enforces that in both directions.

**Four defects found by attacking it**, each with a regression test: a composite
score reading 100/100 grade A for a trader with no reviews (one component needed
no review, and 20% coverage was enough to average — now a hard coverage floor);
thirteen "patterns" out of 100 uniformly random trades (~70 bucket tests at
p≤0.20 produces ~14 by construction — now Benjamini–Hochberg corrected); exit
reason as a pattern dimension, which is circular by definition and generated the
recommendation *"stop taking stop-loss trades"*; and `nan%` in the improvement
timeline, because profit factor is legitimately infinite for a period with no
losers.

**Measured:** 50,000 trades analysed in 2.9 s with a flat per-trade cost from 1k
to 50k, so the pipeline is sub-quadratic; nothing is computed at construction, so
startup is unchanged; four cached reads cost 0.001% of one analysis.

## Before that: V0.5.7 — the Market Data Control Centre

**Last updated:** 2026-07-27, after **V0.5.7 — the Market Data Control Centre**
(branch `feature/providers`, uncommitted). 1257 → **1468 tests** (+184); a new
**46-check** headless-browser suite (`scripts/marketdata_check.py`). Full
design: `docs/MARKET_DATA.md` **§29–41**.

**What it is.** V0.5.2–V0.5.6 built a production-grade market-data subsystem
and gave its owner no way to see or steer it: every real question was answered
by reading `logs/data.log` or by editing `config.yaml` and restarting. V0.5.7
is the entire user-facing management layer built over that machinery, without
redesigning any of it. **No trading-behaviour change, no new runtime
dependency, identical shipped defaults.**

Three new modules — `data/control.py` (`MarketDataControl`, composed *over* the
registry, which never learns about it), `data/credentials.py` (owner-only key
storage; `environment → stored → config.yaml`; plaintext leaves only through
`resolve()`), `data/faults.py` (QA-mode fault injection, off in every shipped
build) — plus a new Settings ▸ Market data panel, `/api/marketdata/*`, and
three ordering modes.

**Four behaviour changes to know before touching `data/`:** `enabled: false`
now constructs the provider (benched, listed, self-explaining, contributing no
history floor); `ordering_mode` supersedes `dynamic_ranking` (hybrid = the full
rank formula minus its latency term); `monitor.health_state()` is a derived
human-facing state alongside the `status()` gate; and a reorder rewrites
priorities 10/20/30 because 10 rank points is one second of latency.

**Five defects found by self-audit**, each with a regression test — most
seriously a hand-edited `marketdata.json` with `providers` as a list raising an
`AttributeError` out of the composition root, i.e. the app refusing to start
because a *preferences* file was edited badly.

**Exact stopping point:** everything above is implemented, tested and verified;
nothing is committed. **Next recommended task:** configure one real free key
(Finnhub or Twelve Data) through the new panel and run a live Test Connection
plus `scripts/marketdata_benchmark.py --live` — no adapter has ever been
exercised against its real API, so the response shapes are as documented rather
than as verified.

---

## Previously: after **V0.5.6 Chart Interaction Hardening**
(branch `feature/providers`, uncommitted). Two further reproducible bugs, both
root-caused from the real `cache.db`. **1257 tests** (+19); `chart_check` 48 →
**65**; a new **110-cell** browser matrix. Full report:
`docs/CHART_CERTIFICATION.md` **Part II**.

**Bug 1 — every symbol on 1D stuck behind "the cached bars failed validation and
were discarded".** Two defects stacked. The data was genuinely wrong: a daily
bar's identity is its session *date*, and a date is only an instant relative to
a timezone, so Yahoo stamped 13:30 UTC (09:30 ET session open), yfinance 04:00
UTC (exchange midnight) and the date-only sources 00:00 UTC — three rows per
trading day in a cache keyed `(symbol, timeframe, ts)`. SPY held **6,517 daily
rows for ~3,258 trading days**; the tightest spacing read 0.40 intervals and
`validate_history` correctly refused the frame. Recovery never completed because
validation ran in `_settle`, *after* the tier ladder had committed — so the
providers were already behind it, the bad rows stayed on disk, and Retry
repeated it forever. Fixed at all three mechanisms: `base.session_index` (one
convention — exchange midnight — enforced in `HistoryAdapter.fetch_history`),
`cache._migration_3` (repairs poisoned installs in place: 17,957 daily+ rows →
11,831, intraday untouched), and disk tiers that validate *before* committing
and `_quarantine` the rows on failure so the ladder falls through.

**Bug 2 — viewport/zoom corruption.** "One owner" said where a viewport move
comes from, never what it may leave on screen: `chScrollToLatest` carried the
previous view's width onto a new symbol, `chApplyFocal` ratcheted narrower up
the timeframe ladder, and a resize left 4 bars of 281 visible. Six explicit
invariants now live in `chClampViewport`, and `CH.restoringViewport` became a
depth counter (overlapping guarded moves were clearing each other's protection
and firing spurious history fetches).

**Deliberately reverted:** re-clamping the viewport from the ResizeObserver. It
broke manual price scaling — a price-axis drag changes its own label widths, so
the canvas resizes mid-gesture and the clamp snapped the scale back.

**Not implemented** (tracked in `docs/TODO.md`): the Settings panel for pasting
API keys, the extra provider-health dashboard columns, enforcement of
cross-provider disagreement, and permanent history-scroll stress coverage.

**Before that: 2026-07-27, V0.5.5 Chart Production Certification**
(branch `feature/providers`, uncommitted). A failure-elimination pass over the
whole chart pipeline, provider to pixel. **No new features, no version bump, no
trading-behavior change.** 1232 → **1238 tests** (+6); `chart_check` 42 → **48
checks**. Full report, matrices and residual risk: **`docs/CHART_CERTIFICATION.md`**.

**Ten defects, found by reproducing them.** Every one was a way the chart
could fail while the backend, the diagnostics dashboard and the entire test
suite reported success — because every check this repo had asked whether the
*data arrived*, and none asked whether the user could *see it*.

The headline defect explains the whole user report ("QQQ loads, SPY partially,
IWM shows only volume, diagnostics healthy"): **the price axis had no owner.**
lightweight-charts turns `autoScale` off permanently the first time the price
axis is dragged, and nothing in this app ever turned it back on — not a symbol
switch, not a timeframe switch, not Reset view. The pinned band outlived every
later load, putting other symbols' candles off-screen while the volume
histogram (own price scale) kept painting. The user's four screenshots show an
identical 480–660 band at four different price levels, and a restart "fixed" it
because `autoScale` is not persisted. Ownership now mirrors the time axis: a
genuine switch resets it, Reset/Latest reset it, a same-key refresh preserves
it (a manual scale is deliberate), and a zero-overlap net catches the rest.

Also fixed: **one 30-minute closing stub bar** (15:30 → 16:00 ET) condemning
every Yahoo 1h frame as "wrong interval served" — measured in the user's real
cache as 1 bad gap in 2,180, and fatal on both Yahoo and yfinance since they
share an upstream; a **`NaT` timestamp** 500'ing `/api/candles`; **null-OHLC
bars rendering as invisible whitespace** under `state="complete"` (they are not
an error to lightweight-charts, they are whitespace); an **out-of-order payload
collapsing to one candle**; a malformed indicator **wiping the whole chart**; a
string indicator value raising an uncaught error from the crosshair handler;
and a **render failure being overwritten with `complete`**. The tenth was in
the test harness itself: `chart_check.py` and `browser_check.py` have been
running against the user's **real** `%LOCALAPPDATA%` data root since V0.4.4
moved the storage root off the CWD, so the suite both polluted live data and
produced results that depended on what an earlier run had cached.

**Two limitations found that cannot be fixed here.** Stooq now serves a
JavaScript proof-of-work challenge to every request (verified live on both
`stooq.com` and `stooq.pl`) — the adapter refuses it correctly, but with no API
keys configured that leaves **Yahoo as the only real source**, via two code
paths sharing one upstream. And Yahoo rate-limits by IP (a 429 was observed
during this pass). A free Finnhub or Twelve Data key is the only route to real
provider independence today.

**Before that: 2026-07-26, V0.5.4 Enterprise Provider Expansion**
(branch `feature/providers`, uncommitted). Three keyed providers — **Finnhub,
Twelve Data, Alpha Vantage** — added behind the existing keyless chain, plus
the credential handling and request budgeting they require. 1052 → **1232
tests** (+180), stress 65 → **88 scenarios**. No version bump, no
trading-behavior change, and **with no API keys configured the app behaves
exactly as it did in V0.5.3** — the shipped default.

Each adapter is ~150 lines implementing four methods (`_build_url`,
`_translate`, `_parse`, `_probe`); health monitoring, circuit breaking,
ranking, configuration, replay, benchmarking and diagnostics all followed with
no per-provider code, which is what V0.5.3's extensibility claim promised. The
genuinely new machinery is `data/http_adapter.py` (shared keyed-HTTP transport
and the timezone contract) and `data/ratelimit.py` (request budgeting —
Alpha Vantage allows 25 requests/DAY, which cannot be managed by reacting to
errors after the fact). Budget *pressure* feeds the existing ranking, so load
moves off a nearly-exhausted provider before it is exhausted.

**Two pre-existing defects surfaced and fixed:** `deepest_earliest` counted
providers that could never answer (a keyless Finnhub declares 180 days of
5-minute history, which would have told the chart history reached back further
than anything could serve — reviving the retry-forever bug class V0.5.2
eliminated); and the stale tier could report `stale` with zero bars. Full
design: `docs/MARKET_DATA.md` §23–27.

**Prior update:** 2026-07-26, after **V0.5.3 Market Data Production Readiness**
(branch `v3-ui`, uncommitted). V0.5.2 built the market-data subsystem; V0.5.3
makes it **operable**, and is deliberately infrastructure work rather than a
feature — **no new provider, no version bump, no trading-behavior change, and a
cold system answers exactly as V0.5.2 did.**

Provider health had been split between `adapter.ProviderHealth` (counters) and
`registry._Breaker` (rotation), with the breaker's trip condition being a *read
of the adapter's counter* — one invariant, two owners. `data/health.py`'s
`ProviderHealthMonitor` now owns counters, latency (EWMA + real p95), the
rate-limit window, the breaker, per-day totals and the ranking score, and
`COUNTS_AGAINST_HEALTH` is the single definition of which failures say anything
about a provider's health. **That consolidation immediately exposed two real
bugs**, both present since V0.5.2 and both invisible while the state was split:
a provider serving consistently-unusable bars was recorded by the adapter as a
*success* and its validation reject counted nowhere, so it kept the head of the
chain indefinitely; and a demoted success could only ever reach a failure streak
of 1, because recording the success had already zeroed the streak.

On top of that: **health-ranked provider ordering** (priority as the anchor,
moved by latency, recent failure rate and quality — scaled so one priority step
equals one second of latency, with cold ranks equal to priority so the shipped
order is unchanged, and `dynamic_ranking: false` to pin it); a **Help ▸
Diagnostics** dashboard with JSON/text export and per-request replay across
every provider; a **`market_data:` config section** carrying every operational
knob so retuning or disabling a provider is not a source edit; cache
intelligence; structured `key=value` request logging; advisory capability
discovery; and a provider benchmark. 880 → **1052 tests** (+172), stress 41 →
**65** scenarios, `chart_check` 49 → **52**. Full design: `docs/MARKET_DATA.md`
§13–22.

**Prior update:** 2026-07-26, after **V0.5.2 Market Data & Chart Reliability**
(branch `v3-ui`, uncommitted). Chart history — the last subsystem that behaved
inconsistently — was **replaced**, not patched, with a capability-driven
multi-provider architecture inside `optionspilot/data/` (Yahoo chart JSON →
yfinance → Stooq, typed failures, circuit breakers, semantic validation, durable
self-healing storage, per-request diagnostics). The primary root cause was
proven from `logs/data.log`, not inferred: Yahoo's intraday depth limit runs from
*now*, and the old clamp measured it from the *request's end*, so every
scroll-back into older intraday data 422'd upstream, returned an empty frame,
and was retried forever. A second, equally damaging cause was found by
`chart_check.py` — a history-paging request overwrote the live-window memo, so
QQQ 1d could come back as a **single candle from nine months earlier** with no
error anywhere. 651 → **880 tests** (+229). No version bump, no trading-behavior
change. Full design: `docs/MARKET_DATA.md`; 84 manual checks (**not yet run**):
`docs/QA_MARKET_DATA.md`.

**Prior update:** 2026-07-26, after **V0.5.0 Auto-Updater 1.0** (branch `v3-ui`,
uncommitted — see "Exact stopping point" below). The installed app now
**self-updates from GitHub Releases** with **no trading behavior change** and
without ever touching user data. New self-contained `optionspilot/update/`
subpackage (core + stdlib only; `urllib`, no new dependency): SemVer ordering,
GitHub Releases client (installer asset only), checker (channel/frequency, never
raises), streamed downloader (progress/cancel), validation (size/hash/Authenticode-
ready), installer launcher (mandatory `pre-update` backup → `/VERYSILENT` install
→ restart), and an `UpdateService` state machine. `/api/update/*` endpoints; a
launch-time background check gated on `run_loop`; prefs in `RuntimeSettings`
(`updates` key). Frontend: Settings ▸ Software updates, Help ▸ Check for Updates…,
and an update dialog. 651 tests (+105), all updater tests offline via fakes; a
real Inno upgrade / live end-to-end update is manual/CI. Full design:
`docs/AUTO_UPDATER.md`.

**Prior update:** 2026-07-26, after **V0.4.6 Professional Windows Installer 1.0** —
turned OptionsPilot into a professionally installable Windows app with **no
application behavior change**. Completed `installer/OptionsPilot.iss` (Inno Setup):
installs to `C:\Program Files\OptionsPilot` (admin), stable `AppId` for in-place
upgrades, Start Menu (app + Uninstall) + optional desktop shortcut, app icon
everywhere, Programs-and-Features registration, and an uninstall-time "remove my
data?" prompt (default No). New `scripts/build_installer.ps1`; `release.yml`
builds + uploads `OptionsPilot-Setup-vX.Y.Z.exe` alongside the zip. 546 tests
(+19). Full design: `docs/INSTALLER.md`.

**Prior update:** 2026-07-23, after **V0.4.5 Professional Release Pipeline 1.0** —
GitHub Actions `ci.yml` + `release.yml` (tag → build → `OptionsPilot-vX.Y.Z.zip`
→ GitHub Release), single-source `__version__`, `scripts/package_release.ps1` +
`release_notes.py`. 527-test suite (+7). Full design: `docs/RELEASE.md`.

**Prior update:** 2026-07-23, after **V0.4.4 persistent storage & data
migration** — user data fully separated from binaries via `core/paths.py::
AppPaths` (root `%LOCALAPPDATA%\OptionsPilot`) + `core/migration.py`
(one-time lossless legacy import, backups, versioned framework). 520-test suite
(+28). Full design: `docs/STORAGE.md`.

**Prior update:** 2026-07-23, after **V0.4.3 AI Coach 2.0 (phase 1)** — a
per-trade 10-category scorecard (`coach/categories.py`) + outcome snapshot and a
pure `build_dashboard` (`coach/analytics.py`: sub-scores, trends, streaks,
confidence-scored patterns, improvement timeline, ≤5 action items), served on
`GET /api/coach → dashboard` and rendered in the coach tab. Manual trades only;
backward-compatible. 492-test suite (+22).

**Prior update:** 2026-07-23, after **V0.4.2 architecture audit + three
refactors** — a shared `core/sqlite.py` foundation adopted by all five stores;
`ui/server.py` import cleanup + public `orchestrator.WINDOW_DAYS`; executable
layering-guard tests. Behavior-preserving. 470-test suite (+16).

**Prior update:** 2026-07-23, after **V0.4.1 Experience Engine phase 3** — a
centralized `build_snapshot`, advisory historical-similarity on tradeable
signals, the Experience API, and storage schema v2. 454-test suite (+30).

**Prior update:** 2026-07-23, after **V0.4.0 Experience Engine phases 1–2** — the
`optionspilot/experience/` subsystem (rich 100k-scalable `ExperienceStore` +
deterministic Similarity Engine) recorded alongside the journal.

**Prior update:** 2026-07-22, after the **V0.3.5 distribution fix** (branch
`v3-ui`, pending the merge decision).
V0.3.5 root-caused the "downloaded release crashes on launch" report: the exe
worked from the dev machine's `dist\` but a zip → GitHub → download → extract
copy died with `RuntimeError: Failed to resolve
Python.Runtime.Loader.Initialize from Python.Runtime.dll` before any app code
ran. Mechanism (reproduced end-to-end by MOTW-flagging a release copy):
pywebview's only Windows backend (WinForms → WebView2) needs pythonnet, and
.NET Framework refuses to load managed assemblies carrying the Mark-of-the-Web
(`Zone.Identifier` ADS, HRESULT 0x80131515) that Explorer stamps on every file
extracted from a browser-downloaded zip; clr_loader swallows the exception
into the opaque "Failed to resolve" error. Local builds carry no flag — hence
"works here, crashes there". `loadFromRemoteSources` config opt-outs were
tested and don't reach clr_loader 0.3.1's load path, so the fix strips the
marker itself: `optionspilot_app.py::unblock_bundle()` (frozen Windows only,
runs before webview can `import clr`, the programmatic twin of Explorer's
"Unblock" checkbox). +3 tests in `test_packaging.py` (stream removed across
the tree; dev interpreter strictly a no-op; entry point provably calls the
gate before `main()`). 0.3.4 → 0.3.5, 392 tests. Before it: V3.3.1 root-caused the intermittent "switch symbols
enough → chart loads blank and stays blank until restart": no timeout on the
chart fetch (a backend throttle backlog / hung upstream left the first-paint
spinner up forever), superseded fetches never aborted (rapid-switch pile-up on
the serialized yfinance throttle), no backend request timeout, a hung history
fetch leaving `historyLoading` stuck, an uncaught "Value is null" on
non-monotonic data, and an unbounded backend cache. Fixed with bounded
`AbortController` fetches (timeout→recoverable + abort-on-switch), a backend
`REQUEST_TIMEOUT`, `chEnsureMonotonic` + guarded rAF loop, and `MEM_CACHE_MAX`
(0.3.3 → 0.3.4, no new features). Before it: V3.3 was a correctness sprint
verified against LIVE market data during regular trading hours: adaptive live refresh cadence,
America/New_York time display, a candle countdown timer, drawing creation
previews, an overlay that tracks vertical price-axis moves, and the key root
cause — a periodic refresh that no longer discards paged-in history or moves
the viewport (it merges the fresh recent window onto the retained older bars).
Two behaviours were identified as **yfinance provider limitations, not app
bugs** (no streaming feed; forming bar arrives as a flat `volume=0` placeholder)
and documented with the smallest recommended path to a real-time provider.
Version 0.3.2 → 0.3.3. Before it: V3.2.2 audited every viewport mutation,
routed them through one controller, found two root causes (a history-arming
race and an async-callback-timing bug), and added Auto Follow (0.3.1 → 0.3.2).
Before that: V3.2.1 fixed three release-blocker symptoms
(drawings, viewport snapping, tf-context loss); V3.2 made the drawing engine
timeframe-independent (drawings no longer vanish on a tf switch), added a
Ray tool and one unified `chAddDrawing` API for user/AI/replay, shipped
Extended Hours (pre/after-market candles + session shading, display-only),
and bumped the version 0.1.0 → 0.3.0. Before that, RC1–RC3 stabilized the
chart. Earlier the same week: the V3 chart
follow-up session (chart reliability root-caused and fixed, design system,
and redesigns of every tab — seven commits `7176843`…`79138da` on branch
**`v3-ui`**, kept off `main` pending the user's review). Three earlier sessions also landed
this date: V2-4 finish (`50c75aa`), the docs/AI framework (`1029fb0`),
and developer automation (`7373c51`). As always, trust `git log`, not
this file, for whether anything landed.

## Verified facts about current state (checked 2026-07-18)

- Full test suite: **374 tests, 100% passing** (338 from the V2-4-core
  commit, plus the endpoint-level halt test and the new `TestManualEntry`
  suite in `tests/test_risk.py`). Static `$("id")` reference check clean.
- **A `git status` printed "working tree clean" this session while
  `git diff --stat` showed 13 dirty files** (the 2026-07-16 session's
  uncommitted manual-risk-gating work). The output-capture trap in
  `CLAUDE.md` applies to git too — cross-check `git status` with
  `git diff --stat` before trusting either.
- **V2-4-finish work live-verified 2026-07-17** in serve mode against
  scratch data dirs, including a real headless-browser drive (Playwright
  driving system Edge): fib/zone/note tools drawn, persisted across
  reload, cleared; Esc disarm; entry + stop-loss price lines rendered on
  the chart after a real manual buy + protective stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  quantity vetoes surfaced as 422s through the real endpoint. Only
  console error: the pre-existing missing `/favicon.ico` (now in TODO).
- Earlier verified milestones (V2-3 frontend, performance pass, V2-3 exe
  rebuild + smoke test) are recorded in `CHANGELOG.md` and the git log —
  all committed and unchanged by this session.

## Completed (phases 1–8, the original v1 roadmap — see `docs/ROADMAP.md`)

All 8 original phases are done: foundation, analysis suite, AI decision
engine, risk manager + paper broker, journal/learning/backtester,
orchestrator + notifications, desktop UI, hardening (perf pass, TradingView
webhook, broker registry stubs, soak-test harness). This was a prior
session's work and is fully committed, tested, and packaged.

## Completed in the V2 rewrite

### V2-0 — Stabilize (committed)
Watchlist manager (quick add, bulk paste, autocomplete against a bundled
12k-symbol directory, 9 preset lists, favorites, pin/drag/sort/filter,
30-symbol cap) + `RuntimeSettings` overlay system + in-app trading-mode
toggle, all with no-restart live application. 272 tests at the time.

### V2-1 — True desktop application (committed, `0ce001d`)
`--windowed` no-console PyInstaller build, generated app icon
(`scripts/make_icon.py` → `assets/optionspilot.ico`), single-instance guard
(localhost-port mutex; second launch shows a friendly notice window instead
of corrupting the shared SQLite files), windowed-safe logging (skips the
console `StreamHandler` when `sys.stderr is None`).

### V2-2 — Trading engine: orders, manual trading, account metrics (committed, `0ce001d`)
`OrderManager` (`broker/orders.py`): MARKET / LIMIT / STOP_LOSS /
TAKE_PROFIT / TRAILING_STOP orders, DAY/GTC time-in-force, position scaling,
reservation checks (can't oversell a position across bracket orders),
auto-cancel on position close, full SQLite persistence, restart-safe (fills
after restart use live quotes, never stale stored ones). Manual trading API
(`/api/chain`, `/api/orders*`, `/api/account/metrics`) and the Trade tab UI
(account cards, live option chain with Greeks, order ticket, working orders
+ history, one-click position close). `Position.managed_by` field
(`"ai"`/`"manual"`) separates AI-managed from user-managed positions.
Deferred: stock/share positions (options only for now).

### V2-3 — AI Mode vs Human Mode (committed, verified 2026-07-16)

- `EngineConfig.operating_mode: "ai" | "human"` (default `"ai"`), validated,
  independent of `trading_mode` (switching one never flips the other — see
  `RuntimeSettings._apply_mode`'s explicit preservation of
  `operating_mode`).
- `RuntimeSettings.set_operating_mode()` — instant, persisted, no restart.
- `Orchestrator._scan_symbol`: in Human Mode, a tradeable signal becomes a
  one-time "advice only" notification per bar instead of an order — the AI
  genuinely never calls `open_position()` in this mode.
- `Orchestrator._reconcile_manual` / `_capture_context` /
  `_capture_context_for_symbol` / `_finalize_manual`: detects manual
  position open/close by diffing `managed_by="manual"` positions
  cycle-to-cycle, captures analysis context while open (best-effort),
  rebuilds the round trip from `PaperBroker.fills_for()` +
  `OrderManager.orders_for()` on close, and journals it with a
  `TradeCoach` review attached.
- `coach/coach.py` — `TradeCoach.review()`: full before/during/after
  breakdown, 14-tag mistake taxonomy (each with a "what a pro would do" note
  and a concrete exercise), process-based score 0–100 (NOT outcome-based —
  this is load-bearing and tested explicitly:
  `test_disciplined_loser_scores_well` / `test_reckless_winner_scores_badly`
  in `tests/test_coach.py`).
- `coach/profile.py` — `CoachProfile.build()`: aggregates all persisted
  reviews into recurring mistakes, top strengths, score trend, win rate by
  setup quality, top-3 recommended exercises.
- API: `POST /api/operating_mode`, `GET /api/coach`.
- UI: header segmented control (`#op-seg`, "🤖 AI trades" / "👤 You trade"),
  new Coach tab (`#tab-coach`) with cards, recurring-mistakes panel,
  strengths/exercises panel, expandable review-detail table. **Live-verified
  in a real browser this session — see "Verified facts" above.**
- Tests: `tests/test_coach.py` (13 tests) and `tests/test_human_mode.py`.
  All passing (310 total).

## Not started

- **V2-4 — Chart workspace**: bundling `lightweight-charts`, the
  TradingView-inspired layout (top bar/right sidebar/bottom panel), drawing
  tools overlay, trade-from-chart. Nothing exists yet beyond the roadmap
  entry.
- **V2-5 — Replay engine**: historical day replay with hidden future
  candles, play/pause/step/speed, separate replay account, coach review of
  replay trades. Nothing exists yet.
- **V2-6 — Journal & improvement dashboard**: chart-context snapshots per
  trade (the deliberate re-renderable-data substitute for screenshots),
  notes/emotions fields, filtering by strategy/symbol/P&L/date/mistake, and
  a dedicated improvement-trend dashboard (the Coach tab built in V2-3
  covers *some* of this via `CoachProfile`, but the full V2-6 spec — journal
  filtering UI, notes/emotions capture, chart snapshots — is not built).
- Stock/share positions (deferred from V2-2).
- Everything in `ROADMAP-V2.md`'s "Beyond v1" section: live-loop candle
  cache, a real Alpaca paper-API adapter, news/sentiment evidence,
  portfolio-level risk.

## Exact stopping point

**2026-07-28, V0.7.0 Platform Foundation (branch `feature/v0.7`,
uncommitted).** Two new packages exist and are wired: `optionspilot/host/`
(`capabilities.py` — 13 `Capability` values and a `HostProfile` for each of
`desktop`/`headless`/`web`/`ios`/`android`, the last three marked
`implemented=False`; `adapter.py` — `HostAdapter`/`DesktopHost`/`HeadlessHost`
plus the single-instance socket mutex moved out of `ui/desktop.py`) and
`optionspilot/services/` (`viewmodels.py`, `portfolio.py`, `watchlist.py`,
`intelligence.py`, `notifications.py`, `workspace.py`, `sync.py`,
`registry.py`). `UIServer` constructs one `ServiceRegistry` and its methods
delegate — every method name and wire shape is unchanged, except that a
notification now additionally carries `severity` (purely additive).
`config/runtime.py` gained `workspace_state()` / `set_workspace_state()`.
`ui/server.py` lost ~180 lines of computation and gained three routes
(`/api/workspace` GET/POST/DELETE, `/api/host`, `/api/diagnostics/sync`); four
now-dead imports were removed. `index.html` gained one `window.Workspace` module
(~110 lines) that mirrors localStorage writes up through a single
`Storage.prototype.setItem` interception and adopts the server's copy when a
profile has no workspace keys, plus one line in `switchTab`. New tests:
`test_host.py`, `test_services_workspace.py`, `test_services_portfolio.py`,
`test_services_notifications.py`, `test_services_endpoints.py`, and seven guards
added to `test_architecture.py`. New script `scripts/workspace_check.py`, wired
into `verify.ps1` as a sixth browser suite. 2027 tests green; 21/21 workspace,
135/135 guide, 54/54 intelligence, 46/46 market-data, chart_check green,
browser_check green, 88/88 stress, check_html_ids + check_docs green.

**Previously: 2026-07-26, V0.5.2 Market Data & Chart Reliability (branch `v3-ui`,
uncommitted).** The chart pipeline was traced end to end and instrumented before
any code changed; both primary root causes were reproduced from evidence (a live
`logs/data.log` line for the depth-clamp bug; a `chart_check.py` run for the
memo-poisoning bug) and each now has a regression test that fails without its
fix. New modules under `optionspilot/data/`: `capabilities`, `adapter`,
`yahoo_provider`, `yfinance_adapter`, `stooq_provider`, `legacy`, `registry`,
`quality`, `service`, `diagnostics`; `cache.py` rebuilt; `cached.py` reduced to a
facade over `MarketDataService`; `build_provider()` added as the composition root
and adopted by `Orchestrator`. `ui/server.py` gained the richer `/api/candles`
payload and `GET /api/diagnostics/marketdata`; `ui/static/index.html` gained the
explicit load state machine and the honest end-of-history pill. Two fixes landed
outside `data/`: `core/sqlite.connect` now closes a connection whose first PRAGMA
fails (a corrupt db otherwise leaked a Windows file handle and could not be
quarantined), and `chLoadHistoryChunk` restores the viewport as it is at merge
time rather than a mid-drag snapshot. New scripts: `marketdata_stress.py` (41
offline torture scenarios, wired into `verify.ps1`; 6 more behind `--live`) and
`marketdata_probe.py` (re-measures provider depth and flags table drift).
`chart_check.py` 44 → 49 checks — and **green end to end for the first time**;
it had been dying at check 12 on `main`, which is how the memo bug surfaced.
880 tests green, `check_html_ids` green, `check_docs` green, JS `node --check`
passes, live stress green (24 concurrent chart loads in 0.5s, zero blanks).
**Not done:** the 84-item manual QA in `docs/QA_MARKET_DATA.md` (several checks
need market hours and DevTools throttling), and the exe has not been rebuilt.

**Prior stopping point — 2026-07-26, V0.5.0 Auto-Updater 1.0 (branch `v3-ui`, uncommitted at time of
writing).** After inspecting the storage/backup layer (`core/paths.py`,
`core/migration.py::create_backup`), config (`settings.py`/`runtime.py`), the UI
server/desktop wiring, and the installer's silent-install support, built a
self-contained `optionspilot/update/` subpackage (10 modules: `version`,
`models`, `transport`, `github_api`, `checker`, `downloader`, `validation`,
`installer`, `ui`, `service`) that depends only on `core` + stdlib (networking is
`urllib` — **no new runtime dependency**). Each layer takes an injected transport/
collaborator so the whole updater is tested fully offline with fakes
(`tests/update_helpers.py`). Extended `RuntimeSettings` with `update_prefs()`/
`set_update_prefs()` (persisted under the `updates` key, defaults in
`DEFAULT_UPDATE_PREFS`). Wired `/api/update/{status,check,download,progress,cancel,
apply,skip,settings}` into `ui/server.py`; `UIServer` constructs
`UpdateService(__version__, runtime)` and `create_app` kicks a launch-time
background check **gated on `run_loop`** (tests never hit the network);
`ui/desktop.py` sets an install hook that closes the window + releases the
single-instance lock. Frontend (`ui/static/index.html`): CSS + a Settings ▸
Software updates panel, a header Help ▸ Check for Updates… menu, a `#ver` update
dot, and the update dialog, all driven by an `Updater` JS module polling
`/api/update/status`. Bumped 0.4.6 → 0.5.0. +105 tests (8 `test_update_*.py`
files + runtime-prefs); `test_architecture.py` allow-lists the core-only `update`
subpackage. 651 total green; `check_html_ids` green; JS `node --check` passes.
**The ISCC compile and a real launch→download→install→restart update were NOT
executed here (Inno Setup not installed; no live GitHub release with an installer
asset) — they are manual/CI** (checklist in `docs/AUTO_UPDATER.md` §7). Nothing
committed (user hasn't asked). **Next infra:** Authenticode code signing (removes
SmartScreen warnings + enables signature verification in `update/validation.py`);
a published SHA-256 checksums asset; replace the placeholder `LICENSE`.

**Before that: 2026-07-26, V0.4.6 Professional Windows Installer 1.0 (branch `v3-ui`,
uncommitted at time of writing).** After inspecting the V0.4.5 installer template
+ release pipeline, completed the Windows installer and wired it in.
`installer/OptionsPilot.iss` evolved from per-user template to a production
installer: `DefaultDirName={autopf}\OptionsPilot` (C:\Program Files, admin,
x64), stable `AppId` retained for in-place upgrades (`UsePreviousAppDir`,
`CloseApplications`), Start Menu group (app + Uninstall shortcut), optional
desktop shortcut (default checked), `SetupIconFile`/app-icon everywhere,
Programs-and-Features metadata (publisher, URLs, copyright, `UninstallDisplayName`
/`Icon`), and a `[Code] CurUninstallStepChanged` prompt that deletes
`%LOCALAPPDATA%\OptionsPilot` only on explicit Yes (default No). Removed the old
install-time `removedata` task + `[UninstallDelete]`. New
`scripts/build_installer.ps1` (finds ISCC, reuses `dist\OptionsPilot`, stamps
`/DMyAppVersion=<__version__>` → `installer\Output\OptionsPilot-Setup-v<ver>.exe`).
`release.yml` gained: install Inno Setup (choco) → `build_installer.ps1` → upload
the setup exe alongside the retained zip. Added `/installer/Output/` to
`.gitignore`. Bumped 0.4.5 → 0.4.6. +19 tests (`tests/test_installer.py`, static
`.iss` + pipeline guards), 546 total green; `check_docs` green; workflow YAML
parses. **Inno Setup is not installed here, so the ISCC compile and the
fresh-install/upgrade/repair/uninstall runs were NOT executed — they are
manual/CI** (checklist in `docs/INSTALLER.md`). Nothing committed (user hasn't
asked). **Next infra:** Authenticode code signing (SmartScreen); then the
auto-updater; replace the placeholder `LICENSE` before public release.

**Before that: 2026-07-23, V0.4.5 Professional Release Pipeline 1.0 (branch
`v3-ui`, uncommitted at time of writing).** After auditing the existing build/release
setup (`scripts/build_exe.ps1`, `build.ps1`, `release.ps1`, `bump_version.py`,
`OptionsPilot.spec`), implemented release automation with no app behavior change.
New `.github/workflows/ci.yml` (push/PR + `workflow_call`: install pip-cached →
`pytest` → `selftest` → `check_html_ids` → `check_docs`) and `release.yml` (on
`v*` tags: `uses: ci.yml` → tag/`__version__` guard → `scripts/build.ps1` →
`scripts/package_release.ps1` → `gh release create`). Made `__version__` the
single source of truth (`pyproject.toml` `dynamic = ["version"]` +
`[tool.setuptools.dynamic] version = {attr = "optionspilot.__version__"}`);
`scripts/bump_version.py` now edits one file and `scripts/check_docs.py` enforces
the invariant. New `scripts/package_release.ps1` (clean `OptionsPilot-vX.Y.Z.zip`:
app + LICENSE/README/CHANGELOG, excludes `data/`/`logs/`/source) and
`scripts/release_notes.py` (CHANGELOG excerpt). Added a placeholder `LICENSE`
(flagged) and an **unwired** `installer/OptionsPilot.iss` Inno Setup template with
paths/shortcuts/AppData/uninstall documented in `docs/RELEASE.md`. Bumped
0.4.4 → 0.4.5. Verified locally: YAML parses, `release_notes.py` extracts the
right section, `package_release.ps1` produced a correct 54 MB zip (top level
`OptionsPilot/ LICENSE README.md CHANGELOG.md`, no `data/`/`logs/`/source),
`check_docs` green. +7 tests (`test_release_tooling.py`), 527 total green.
Nothing committed (user hasn't asked). **Next infra:** wire the Inno Setup
installer into `release.yml` (compile + upload), then the auto-updater (replace
the install dir only; back up via `create_backup` first); and replace the
placeholder `LICENSE` before any public release.

**Before that: 2026-07-23, V0.4.4 persistent storage & data migration (branch
`v3-ui`, uncommitted at time of writing).** After a full filesystem-usage audit,
implemented the storage-separation milestone. New `optionspilot/core/paths.py`
(`AppPaths` — single source of truth; platform root `%LOCALAPPDATA%\OptionsPilot`
/ XDG / `Application Support`, `OPTIONSPILOT_HOME` override; typed `get_*`
helpers; `ensure()`) and `optionspilot/core/migration.py` (`initialize_storage`
— layout creation + one-time lossless legacy import with timestamp preservation,
per-file verification, skip-if-newer, no source deletion; `create_backup`;
empty `MIGRATIONS` versioned framework; `migration_version.json` marker). Wired
`__main__._bootstrap` (builds AppPaths, migrates, logs to `paths.root`, threads
`data_dir` through all commands), `Orchestrator`/`UIServer`/`create_app`/`serve`/
`desktop.launch` defaults, and the UI backtest report path; removed the last
CWD-relative `Path("data")` hardcodes. `data_dir=` APIs unchanged. Extended
`selftest` with storage checks; added `tests/conftest.py` `OPTIONSPILOT_HOME`
isolation. Tests +28 (`test_paths.py`, `test_migration.py`), 520 total green;
`selftest` PASS; end-to-end migration of the real 27-file legacy `./data`+`./logs`
verified byte-for-byte lossless with originals intact and a no-op second launch.
Nothing committed (user hasn't asked). **Next:** the automatic updater (replace
the install dir only, never the storage root; back up via `create_backup` first)
— recommendations in the final report / `NEXT_SESSION.md`.

**Before that: 2026-07-23, V0.4.3 AI Coach 2.0 phase 1 (branch `v3-ui`,
uncommitted at time of writing).** Implemented Phase 1 of AI Coach 2.0 after a full inspection of the
existing Coach/Journal/Experience wiring. New: `coach/categories.py` (10-category
per-trade scorecard, derived from the review's existing findings/mistakes; pure)
and `coach/analytics.py` (`build_dashboard`: sub-scores, category monthly trend,
streaks, confidence-scored pattern detection, improvement timeline, ≤5
recent-window action items; pure). `coach/coach.py`'s `CoachReview` gained
`categories` + an outcome snapshot (pnl/return_pct/hold_minutes/best-effort
`r_multiple`/entry_ts/symbol/direction), computed inside the existing `review()`.
`GET /api/coach` returns a `dashboard` block (cached by review count in
`UIServer._coach_dashboard`); the coach tab renders it (no new CSS). Manual
trades only; the trading path and *when* a review runs are unchanged; everything
is backward-compatible with pre-2.0 reviews. Tests +22 (492 total, green);
`check_html_ids` + headless `browser_check` green. Nothing committed (user hasn't
asked). **Next:** Coach 2.1 ideas in `NEXT_SESSION.md` (persist dashboard
history, AI-trade coaching, overtrading detection) or V0.4 Phase 4
(`learning_mode`). 

**Before that: 2026-07-23, V0.4.2 architecture audit + three refactors (branch
`v3-ui`, uncommitted at time of writing).** Ran a full read-only architecture audit
(report: `docs/ARCHITECTURE-AUDIT-V0.4.2.md`) and implemented the three approved
low-risk, behavior-preserving improvements, each as a separate change with its
own regression tests: (1) new `core/sqlite.py` (`connect` + `run_migrations` on
`PRAGMA user_version`) adopted by all five stores — `cache` → `journal` →
`orders` → `paper` (idempotent `managed_by` migration) → `experience` (refactored
onto the base); migration 1 of each store is its exact current schema, so
existing `data/*.db` files open unchanged; +13 tests (`test_sqlite.py`). (2)
`ui/server.py` imports hoisted to module top, and the private
`orchestrator._WINDOW_DAYS` promoted to public `WINDOW_DAYS` (also updated
`__main__.py` + the `test_models` wiring test). (3) `tests/test_architecture.py`
(+6) makes the layering executable. Version 0.4.1 → 0.4.2, **470 tests green**,
`selftest` PASS. No user-visible behavior changed. Nothing committed (the user
hasn't asked). **Next:** optional audit follow-ups (report §11) or V0.4 Phase 4
(`learning_mode` axis) — see `docs/ROADMAP-V0.4-EXPERIENCE.md` §11.

**Before that: 2026-07-23, V0.4.1 Experience Engine phase 3 (branch `v3-ui`,
uncommitted at time of writing).** Integrated the Experience Engine into the app. New
`experience/snapshot.py` (`build_snapshot`) is the single centralized capture of
an AI decision context, used by both the AI entry path (`_scan_symbol` →
`_register_meta`, stored in `_TradeMeta.entry_context`) and the manual/coach path
(`_capture_context` now routes through it) for feature symmetry. Tradeable
signals get an advisory `_attach_historical` block (surfaced in the status
payload + advice notification); `Orchestrator.experience_for_symbol` +
`GET /api/experience[/similar]` expose the Experience API. `ExperienceRecord`
gained the full snapshot fields; `ExperienceStore` migrated to schema v2
(`market_regime`/`return_pct`/`hold_minutes` + SQL aggregates). New tests:
`tests/test_snapshot.py` (+6), plus additions to `test_experience.py`,
`test_similarity.py`, `test_ui_server.py`. Version 0.4.0 → 0.4.1, **454 tests
green**. Advisory only — nothing touches the gate/risk/execution. Nothing
committed (the user hasn't asked). No frontend change, so `verify.ps1`'s
browser/chart checks were not re-run (the `pytest` suite was). **Next:** Phase 4
(`learning_mode` axis + Exploration) — see the roadmap doc §11.

**Before that: 2026-07-23, V0.4.0 Experience Engine phases 1–2 (branch `v3-ui`,
uncommitted at time of writing).** Built the two load-bearing phases of the
V0.4.0 sprint: the Experience Engine + store (Phase 1) and the Similarity Engine
(Phase 2). New `optionspilot/experience/` package (`models.py`, `features.py`,
`store.py`, `similarity.py`, `engine.py`, `__init__.py`); `tests/test_experience.py`
(+20) and `tests/test_similarity.py` (+12); wired into `Orchestrator` after both
`journal.record` sites, best-effort. Version 0.3.5 → 0.4.0, **424 tests green**.
Three decisions taken with the user (calibration advisory-only; exploration →
future orthogonal `learning_mode` axis; scope = Foundation + Similarity) are
recorded in `docs/ROADMAP-V0.4-EXPERIENCE.md` §2. Nothing was committed (the
user hasn't asked). No frontend change, so `verify.ps1`'s browser/chart checks
were not re-run this session (the `pytest` suite was). **Next:** Phase 3
(calibration surfacing + AI entry-context capture) — see the roadmap doc §11.

**Before that: 2026-07-22, V3.3.1 chart reliability investigation (branch
`v3-ui`, uncommitted at time of writing).** A pure root-cause investigation (no new
features) of the intermittent "blank chart until restart." The market was
CLOSED, so the live-load failure couldn't be reproduced directly; instead the
lifecycle was instrumented (fetch start/finish/superseded/abort/empty, gen,
cache, render, timers) and the failure characterized under (a) a 250-switch
real-provider stress run, (b) a direct concurrent-load backend hammer, and (c)
deterministic fault injection. Findings — each a concrete mechanism:

- **No timeout on the chart fetch → permanent spinner.** A single cold fetch is
  ~0.2s, but under concurrent load yfinance's one 0.15s-per-request throttle
  lock pushed latency to 10–15s+ (measured), and a hung upstream is unbounded;
  the un-bounded `fetch()` left the first-paint loading overlay up forever
  (confirmed: still a spinner after 6s of a slow backend). "Restart fixes it" =
  restart clears the backlog. Fix: `AbortController` with a 15s timeout that
  routes into the existing recoverable error path.
- **Superseded fetches never aborted** → a rapid switch burst piled requests on
  the serialized throttle and starved the wanted symbol. Fix: abort the previous
  fetch on every new load/switch (a 12-switch burst now aborts 11).
- **Backend `yfinance.history()` had no request timeout** (hang blocked the
  in-flight slot) → added `REQUEST_TIMEOUT=10s`.
- **Hung history fetch left `historyLoading` stuck** → history-fetch timeout.
- **Non-monotonic payload → uncaught "Value is null" from the library's paint
  frame** → `chEnsureMonotonic()` before setData + guarded rAF loop (backend
  `validate_candles` already dedupes+sorts; this is defense-in-depth).
- **Unbounded backend `_mem` cache** → `MEM_CACHE_MAX=400` LRU.

Verified: 250 switches = 0 blanks / 0 console errors; fault injection 7/7
recover; hung backend → recoverable error → auto-recovers; memory plateaus;
chart_check 41 → 44 (all green); +1 backend test (`TestMemCacheBounded`).
Version 0.3.3 → 0.3.4. Remaining limitation: yfinance's global throttle still
adds latency under heavy concurrent load — now recoverable, not a permanent
blank; a streaming provider removes the serialization.

### Before that: V3.3 chart stabilization & market validation

**2026-07-20, V3.3 chart stabilization & market validation (branch `v3-ui`,
uncommitted at time of writing).** A correctness sprint run while the US market
was OPEN, so every issue was reproduced and re-verified against live data in a
real browser (and finally in the rebuilt v0.3.3 exe). 13 issues addressed:

- **Fixed at the architecture level:** live refresh cadence (adaptive ~7s
  intraday poll + lowered `CANDLE_TTL`, re-armed on every load); ET display
  (Intl label formatters, timestamps unchanged); candle countdown timer;
  drawing creation preview (rubber-band); drawing overlay rAF sync loop
  (tracks vertical price-axis moves — lightweight-charts fires no price-scale
  event); and **the key root cause** behind "history randomly vanishes /
  viewport jumps on refresh": the periodic refresh was replacing `CH.data` with
  the base window, discarding paged-in history and shifting logical indices —
  now it MERGES the fresh recent window onto retained older bars
  (`chMergeRefresh`), and the pre-fetch cache paint is limited to real switches.
- **Verified not-regressed with real mouse against live data:** drawing
  persistence across timeframes (same object 1m→1d, never duplicated); candle
  correctness (SPY/AAPL/NVDA match yfinance bar-for-bar); blank charts (13
  named symbols incl. BRK.B render; invalid → error overlay); memory (heap
  plateaus, canvas/cache bounded across 80 switches — no leak); Auto Follow.
- **Provider limitations (documented, not app bugs):** yfinance is poll-only
  (no streaming) and returns the forming bar as a flat `volume=0` placeholder
  until it completes — so a true tick-by-tick forming candle needs a streaming
  provider (smallest path: a new `MarketDataProvider` adapter pushing bar
  updates over a WebSocket; the frontend already applies trailing-bar updates).
- **Tests:** `chart_check` 36 → 41 (ET display, timer, drawing preview,
  vertical-drag overlay tracking, refresh-preserves-history — each fails on
  pre-V3.3 code); hardened the flaky viewport-recovery strand. `verify.ps1`
  green; exe rebuilt (v0.3.3, selftest PASS) and driven by hand.

Version 0.3.2 → 0.3.3. Known limitations unchanged from below plus the yfinance
streaming/forming-bar note now recorded in `PROJECT_STATUS.md`.

### Before that: V3.2.2 viewport ownership unification + Auto Follow

**2026-07-20, V3.2.2 viewport ownership unification + Auto Follow (branch
`v3-ui`, uncommitted at time of writing).** V3.2.1 fixed three symptoms;
every new bug reported afterward (random recentering, history intermittently
failing, losing viewport while scrolling) was another symptom of the same
underlying conflict — the viewport had no single owner. This sprint audited
every viewport mutation in `static/index.html`, routed all of them through
one controller, and found two genuine root causes (not just the arming race
suspected going in):

- **One controller (`chMoveViewport`).** Every sanctioned mover (Reset,
  Latest, Auto Follow, tf-switch focal restore, same-key refresh, history
  prepend, symbol switch) now routes through a single function instead of
  each caller remembering the `restoringViewport` convention individually.
- **Root cause 1 (history-load arming race).** The old wheel/touchstart/
  pointerdown listeners armed history a DOM-event tick after the library's
  own range-change fired during the same pan — a scroll-into-history
  sometimes silently did nothing. Fixed by arming directly off the
  range-change subscription itself.
- **Root cause 2 (the deeper one — async subscription timing).**
  Instrumented the vendored lightweight-charts' real callback timing:
  `subscribeVisibleLogicalRangeChange` fires on a LATER animation frame, not
  synchronously inside `setVisibleLogicalRange()`/`fitContent()`. Resetting
  `restoringViewport` synchronously right after the call (the V3.2.1 pattern)
  closed the guard window before that callback ever arrived — one frame
  later, every sanctioned move looked like a user pan, silently re-arming
  history-load and breaking Auto Follow the instant it was enabled. Fixed by
  deferring the reset two animation frames.
- **Auto Follow (new toggle).** OFF by default (user owns the viewport,
  nothing auto-recenters except Reset/Latest); ON keeps the newest bar in
  view across refreshes/live updates/switches; manual pan disables it;
  Latest re-enables it; persisted. `scrollToRealTime()` turned out to run a
  multi-frame animation (each tick misread as a user pan under the 2-frame
  guard) — replaced everywhere with `chScrollToLatest()`, a single
  non-animated `setVisibleLogicalRange` landing on the same destination.
- Verified: history loads via a real drag pan (no manual-arm cheat) every
  time; on-screen bars stay stationary during a prepend; Auto Follow
  toggles/persists/disables-on-pan/re-enables-on-Latest; live updates respect
  Auto Follow ON vs OFF. `chart_check` 33 → 36. 388 tests.

Version 0.3.1 → 0.3.2. Known limitations: same as V3.2.1 below, unchanged —
extended-hours VWAP not separately RTH-anchored; session classification
time-of-day only; live-update paths still market-hours-unverified.

### Before that: V3.2.1 critical chart regression fixes

**2026-07-20, V3.2.1 critical chart regression fixes (branch `v3-ui`,
uncommitted at time of writing).** Three release-blockers the user reproduced in
the real app that V3.2's tests had reported "fixed" — the tests measured
internal state, not user-visible behaviour. Reproduced each by pixel/viewport
inspection before touching code:

- **Bug 1 — drawings still vanished across timeframes.** V3.2 fixed the
  visibility *filter*, but a 1m-anchored drawing's bar times aren't bars on
  5m/1d, so `chX()` fell to `timeToCoordinate()` → null → painted_px = 0.
  Probed the vendored lightweight-charts: `logicalToCoordinate` returns 0 for
  FRACTIONAL indices but maps INTEGER ones (even off-screen, extrapolating).
  Fix: `chX()` interpolates the pixel between the two bracketing integer bars
  (`chLogicalAt`). Renders on every tf now (verified by painted-pixel count +
  distinct/finite coordinates).
- **Bug 3 — timeframe switch lost context.** RC3's fit-on-switch jumped to an
  unrelated date (82-day drift). Fix: `chCaptureFocal`/`chApplyFocal` capture
  the focal date and re-center the new resolution on it, clamping each endpoint
  to the nearest real bar (finer tfs have shorter history → closest candle).
  Recent focal drift ~0 across a 1h→30m→15m→5m cascade.
- **Bug 2 — viewport auto-reset fought the user.** Same-key refresh restored the
  *time* range, null in whitespace past newest → snap. Fix: preserve the LOGICAL
  range (captured before `setData`); stranded auto-fit only on a symbol-switch
  fallback. Latest/Reset unaffected.
- **Shared root cause:** `setData` fires the range subscription before
  `restoringViewport` was set → `chMaybeLoadHistory` ran mid-switch, prepending
  history and shifting logical indices (n 468→1248), corrupting drawings AND
  viewport. Fixed by guarding before `setData` + disarming history on a switch.

Version 0.3.0 → 0.3.1. chart_check 31 → 33 (9b now asserts drawings RENDER, +9d
focal preservation, +9e viewport stability). 388 tests. Known limitations:
extended-hours VWAP not separately RTH-anchored; session classification
time-of-day only; live-update paths still market-hours-unverified.

### Before that: V3.2 chart-system completion + Extended Hours

**2026-07-19, V3.2 chart-system completion + Extended Hours (branch `v3-ui`,
committed `62cbcb4` + `409cfc0` + docs/version).** The final chart subsystem
sprint before Replay/AI-Viz/Mobile/Broker work:

- **Drawing engine v3 (PARTS 1/2/5, `62cbcb4`).** Root cause of drawings
  vanishing on a tf switch: the model was timeframe-LOCKED (`chDrawVisible`
  filtered `it.tf === CH.tf`). Now every drawing is stored once with a
  `visibility` policy ("all" default / {min,max} tf bounds), `createdTf`,
  `source` (user/ai/replay), and `meta`; the renderer decides visibility
  per-tf and never destroys the object. Legacy drawings migrate to "all". One
  `chAddDrawing` API (on `window`) is the sole creation path for user, AI, and
  replay drawings. **Ray** tool added (two-click, infinite one-way extension),
  reusing the edit machinery. Verified real-mouse in a browser: a 1m trend
  stays visible on 5m/1d; Ray hit-tests its extension; programmatic AI drawing
  + {min,max} policy render correctly; all persist across reload.
- **Extended Hours (PART 4, `409cfc0`).** Confirmed FIRST that yfinance
  supplies pre/after-market bars via `prepost=True` for all intraday intervals
  (04:00–20:00 ET). `extended_hours` is a display-only flag threaded
  provider→cache→payload→`/api/candles?ext=1`, kept OFF the trading path (paper
  execution unchanged); ext frames are cache-keyed separately and skip the disk
  store. `data/sessions.py` labels bars pre/rth/post; the payload tags them and
  computes indicators on the session-correct series. Frontend: persisted "Ext"
  toggle (disabled on daily) + overlay session shading. Verified: ext 5m = 1134
  bars {pre,rth,post} vs 468 RTH; daily forces it off; zero console errors.
- **Version (PART 8):** 0.1.0 → 0.3.0.

Known limitations: session classification is time-of-day only (no holiday/
half-day calendar — documented in `data/sessions.py`); extended-hours VWAP is
computed over the displayed series (not separately RTH-anchored); live-update
correctness for ext bars is architecturally ready but market-hours-unverified.

### Before that: V3.1 RC3 final release blockers

**2026-07-18, V3.1 RC3 final release blockers (branch `v3-ui`, committed
`60f16a4`).** Three bugs the user hit in MANUAL testing that the
green automated suite had missed — reproduced by driving the real mouse/UI
before any code changed:

1. **Toolbar actions "still broken."** The real-mouse workflow (draw → click
   to select → click toolbar) proved the RC2 source fix works; the failure
   was a **stale packaged exe** (`dist/OptionsPilot` built Jul 18 12:02,
   before RC1/RC2 — its bundled `index.html` has none of the fixes; on it
   select/drag/resize work but the toolbar actions no-op, exactly as
   reported). The RC2 regression test had set `DRAW.sel` in JS, bypassing the
   real select→click path, so it couldn't catch this. Fix: **rebuilt the
   exe**; rewrote the test to drive the real mouse (verified fail-before:
   colour unchanged, selection cleared).
2. **Stale banner flapping ("appears far too often").** Market open + a
   rate-limited feed alternating stale/fresh re-raised the warning on every
   stale tick though the newest bar never changed (4 re-shows in 8 refreshes).
   Fix: a per-(symbol·tf) high-water mark (`CH.freshHigh`); a stale payload
   warns only when its newest bar is genuinely older than the freshest bar
   already shown. Verified: 0 re-shows on unchanged data, still warns when
   genuinely behind.
3. **Timeframe switch zoomed into one candle.** Per-key cached viewports were
   restored on switch, snapping to a stale tight zoom. Fix: viewport
   restoration has ONE owner — a switch (symbol or tf) fits; only a same-key
   refresh preserves the live viewport. The per-key viewport cache was
   removed. Verified: every switch shows tens-to-hundreds of bars.

Also fixed (found while hardening tests): a rapid symbol burst ending on an
already-cached symbol left the loading overlay + skeleton legend stuck when
that symbol's refresh returned empty — a non-first-paint load now clears the
overlay and restores the legend. `chart_check.py` → 29 checks (real-mouse
toolbar, anti-flap, tf tiny-zoom). **376 tests**, all green.

### Before that: V3.1 RC2 final chart release audit

**2026-07-18, V3.1 RC2 final chart release audit (branch `v3-ui`).** The
last stabilization pass before the `v3-ui` → `main` merge decision. Four
remaining chart bugs, each reproduced in a real browser before any code
changed, root-caused, fixed at the architecture level, re-verified:

1. **Drawing edit-toolbar actions were dead** (recolour / duplicate / lock
   / hide / width / delete all no-op'd). The toolbar floats inside
   `#ch-main`, so the capture-phase `pointerdown` there fired first; the
   click was on the toolbar, not the drawing, so `chPointerDown` ran its
   "empty space → deselect" branch and cleared `DRAW.sel` before the
   button's click handler saw it. Fix: the capture handler ignores events
   originating in `#ch-draw-bar`.
2. **The "Live data unavailable" banner over-fired / flapped.** It shows on
   any failed live fetch that falls back to disk — but with the market
   CLOSED those disk bars ARE the last session, so it was a false alarm
   that toggled every time a background refresh hit Yahoo's rate limiter.
   Fix: `/api/candles` now reports `market_open` (from the existing
   `Orchestrator.market_open`); the banner is suppressed when closed and
   shown as a real "behind live prices" warning only when open.
3. **The chart could strand the user in whitespace.** Added **Reset view**
   and **Latest** controls (R / L keys), a logical-range `chViewportStranded()`
   detector that tells a stranded view from a deliberate deep zoom, and a
   render-time safety net (switch/first-paint only — never a same-key
   refresh, so it can't yank a chosen viewport).
4. **Random viewport jumps on RSI/MACD toggle** — the two-way subpane sync
   let a new pane's auto-fit shove its full-history range onto main
   ([166,205] → [0,191], reproduced). Fix: the main chart is the sole
   time-range owner; subpanes are one-way followers realigned via
   `chAlignPane`.

Tests: `chart_check.py` → **27 checks** (+ toolbar actions, indicator
no-jump, viewport recovery, market-aware banner, rapid-abuse stress, new-bar
append); `test_ui_server.py` +2 (`market_open`). **376 tests**, `verify.ps1`
green end to end. Performance was audited on evidence (one fetch per load,
no canvas/instance leak across 15 reparents, pane-churn loop removed) — no
speculative optimizations. Market-hours live validation remains the one
open item (market closed); the forming-candle / new-bar / indicator paths
are architecturally verified with simulated ticks. **RC2 committed as
`6f3643d` after `verify.ps1` passed end to end; `v3-ui` is still not merged
to `main` (the user's call).**

### Before that: V3.1 RC1 stabilization polish

**2026-07-18, V3.1 RC1 stabilization polish (branch `v3-ui`, `3a56145`).**
Treated the V3.1 charting work as Release Candidate 1 and ran a full
code/stability/performance audit — no new features, no redesign, the
chart architecture untouched. Findings fixed: removed four orphaned
`CH.*Series/priceLines` arrays left dead by V3.1-4; hardened the two
`JSON.parse(localStorage…)` sites behind a `safeParse` helper so a
corrupt `chInds` (app-init) or drawings store (per-symbol render) resets
to default instead of throwing; guarded the 30s auto-refresh so it never
re-renders mid-drag / mid-tool-placement / mid-history-load; added a
`visibilitychange` refresh so the chart isn't stale for up to a cadence
after wake; bounded the previously-unbounded per-(symbol·tf) payload
cache to a 24-entry LRU; and guarded the WebSocket frame parse. Expanded
coverage: a WS-contract backend test (full-payload-on-connect →
heartbeat, the basis of reconnect catch-up) and two `chart_check`
checks (corrupt-localStorage recovery, LRU bound) → 21 browser checks,
**374 tests**. No source TODO/FIXME/XXX; version consistent at 0.1.0.
Remaining work is genuine market-hours validation (architecture verified
capable) — see the RC1 checklist. **This RC1 pass is uncommitted pending
`verify.ps1` green.**

Before that, 2026-07-18, the V3.1 chart-stabilization sprint (committed
`61a2c60`…`2bcb84a`) made the charting system the strongest part of the app. Seven milestones, each root-caused and
browser-verified before commit: (1) chart reliability — the "some
tickers fail / IWM only shows volume" bug traced to three causes
(drawings driving the price scale, NaN volume 500-ing the endpoint,
non-finite indicator values) and fixed at the data boundary
(`validate_candles` sanitization + logging) and the renderer; (2)
timeframes expanded 6 → 13 (1m…1mo), table-driven; (3) infinite
historical scroll fixed (the merge was inverted — replaced the window
instead of prepending; the trigger mixed bar-index and timestamp units);
(4) editable TradingView-style drawing objects on an overlay canvas
(select/drag/resize/color/width/lock/hide/duplicate/rename/delete +
migration); (5) a collapsible chart on the Trade page (the one instance
relocated between tabs, so everything is shared); (6) live-update
correctness (the forming candle froze because `chSig` ignored intrabar
changes) + a flicker-free `series.update()` fast path; (7) a 19-check
headless-browser regression suite (`scripts/chart_check.py`) wired into
`verify.ps1`. Verified: all 10 required tickers × 13 timeframes return
monotonic real data (130/130); `chart_check` 19/19 green; **374 tests**.
The exe was rebuilt and the packaged charts confirmed working. The
`v3-ui` merge decision remains the user's to make.

Before that, 2026-07-18 packaging-fix session (committed `61a2c60`). The
user found the freshly built exe release-blocked: every chart and
option-chain request failed with "No module named 'yfinance'". Root
cause (traced, not guessed): the performance pass `f1bae42` made the
yfinance import lazy via `importlib.import_module()`, which PyInstaller's
static import scan cannot see — every exe built since then silently
shipped without yfinance and its whole dependency tree. The dev venv was
never affected, and this is not a V3 UI regression (V3-0's error
surfacing is what made it visible instead of a blank canvas). Fixed:
`--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
CLI command (forces the lazy imports, offline) that the build script now
runs against the freshly built exe and fails the build on; and
`tests/test_packaging.py` (+4) failing the ordinary suite if any dynamic
third-party import isn't collected by the build script. Verified: exe
rebuilt (selftest gate PASS, `yfinance 1.5.1` + `curl_cffi` physically
in `_internal`), packaged desktop app served 206 daily SPY candles, 624
SMCI 5m candles, and a 231-contract chain live over HTTP; full
browser-flow sweep of the chart system green (load, indicators,
drawings, trade lines, stale banner, retry, 30s auto-refresh — zero
console errors); `verify.ps1` green end-to-end; **374 tests**. One
pre-existing quirk found and documented (not fixed): `OptionsPilot.exe
serve` from the windowed exe never binds its port (`TODO.md`). Committed
as `61a2c60`; the V3.1 sprint above built on it.

Before that, 2026-07-17, end of the V3 UI sprint (branch `v3-ui`, seven commits,
each browser-verified before committing — see `NEXT_SESSION.md` for the
per-milestone list and `CHANGELOG.md` for detail).** Key facts a next
session needs: the chart blank-canvas bug is fixed at the root
(empty-fetch cache poisoning in `CachedProvider` + missing stale
fallback + uncaught frontend failures + a mid-load switch race); the
engine's strict fail-closed candle path is deliberately unchanged; 351
tests pass; `scripts/verify.ps1` ran clean as the session's closing
action. The branch is **not merged** — that's the user's call. The
order-ticket *fill* path (post-fill stop-loss pre-arm) still needs one
market-hours manual pass; everything else was verified live, including
the risk gate visibly rejecting an after-hours order (correct behavior).

Earlier the same date, the V2-4-finish session ended as follows (kept
for the record):

- **Chart trade lines**: `loadChart` draws labeled price lines for the
  charted symbol — position entry (`entry_spot`, newly exposed in the
  status payload), AI stop/target, and working manual orders'
  underlying-level triggers (stop/take-profit/trailing; LIMIT orders are
  premium-space and deliberately not drawn).
- **Three new drawing tools**: Fib retracement, Zone rectangle, and bar
  Notes (inline text input → chart marker) — persisted per
  symbol+timeframe in localStorage like trend lines; Esc cancels an armed
  tool; old stored drawings load unchanged.
- **Manual-entry risk gating completed**: the 2026-07-16 session left
  `RiskManager.approve_manual_entry` + `OrderManager.evaluate`'s
  fill-time approval callback uncommitted AND unwired for immediate
  market buys — `UIServer.place_order` now preflights them (422 + veto
  text). The hard %-risk sizing veto was converted to an advisory note
  (it blocked nearly all manual buys at default settings and broke a
  committed test; sizing discipline is the coach's `oversized` tag's
  job). New `TestManualEntry` suite in `tests/test_risk.py`.
- **Hygiene cleared**: `pyproject.toml` ships `data_assets/*`, `Pillow`
  in the `dev` extra, `operating_mode` documented inline in `config.yaml`.
- **345 tests, 100% passing.** Static `$("id")` check clean.
- **Live-verified 2026-07-17** in serve mode against scratch data dirs,
  including a real headless-browser drive (Playwright + system Edge,
  installed ad hoc into `.venv` — not a project dependency): fib/zone/
  note drawn, persisted across reload, cleared; Esc disarm; entry +
  stop-loss lines rendered on the chart after a real manual buy + stop
  (screenshot-confirmed); manual round trip → coach review; cooldown and
  qty-0 vetoes observed as 422s through the real endpoint. Only console
  error: the pre-existing missing `/favicon.ico`.
- Note for future browser driving: lightweight-charts coalesces clicks
  faster than ~500ms as double-clicks — pace scripted two-point drawing
  clicks ≥700ms apart.

**The exe was rebuilt 2026-07-18** with the packaging fix and verified
serving live data (see the current stopping point above).

## Next recommended task

0. **Review V0.6.0, V0.6.1 and V0.7.0 and decide on the commits.** All three are
   built, verified and uncommitted. `docs/TRADING_INTELLIGENCE.md`,
   `docs/ONBOARDING.md` and `docs/ARCHITECTURE-PLATFORM.md` are the review
   documents.
0b. **Contract hardening** (`ARCHITECTURE-MOBILE.md` §18 items 1-3, 6): `/api/v1`
   aliases, a normalized error envelope, idempotency keys on mutating endpoints,
   the WebSocket envelope. Cheap while the server and its only client update in
   lockstep; a migration the day one exists that cannot.
1. **Run the market-data manual QA** (`docs/QA_MARKET_DATA.md`, 84 checks).
   Everything automatable is automated and green; what remains genuinely needs a
   human, market hours, and DevTools throttling. Sections F (degraded states)
   and D (history paging) carry the most value.
1. **Authenticode code signing** (the natural follow-up to the auto-updater):
   sign the setup + app exe in `release.yml`, add a signature-verification check
   to `update/validation.py`, and publish a SHA-256 checksums asset the validator
   can enforce. Also: one manual end-to-end update QA on real Windows
   (`docs/AUTO_UPDATER.md` §7) and replace the placeholder `LICENSE`.
2. **V0.4.0 Phase 4** — the `learning_mode` axis (normal/exploration) added to
   `config/settings.py` + `config/runtime.py` (orthogonal to
   operating_mode/trading_mode), plus exploration-mode tagged, risk-capped
   lower-confidence paper trades. Plumbing already exists
   (`ExperienceRecord.exploration`, snapshot `learning_mode`). Then Phases 5–6
   (AI Performance dashboard frontend, strategy discovery). Phase 3 (integration)
   is done. Full plan: `docs/ROADMAP-V0.4-EXPERIENCE.md` §11.
3. User review of the `v3-ui` branch → merge decision (V0.4.0 also lives here,
   uncommitted).
4. If V3 continues: the remaining `ROADMAP-V3-UX.md` items (H5 notification
   center, N2 chart↔chain links, N4 toast stacking).
5. Eventually: rebuild + smoke-test the exe (LAST, once the branch state
   settles).

## Current priorities

1. `v3-ui` review/merge — user's call.
2. The what-next scope decision after that.
3. Exe rebuild deliberately LAST.

## Blockers

None.
