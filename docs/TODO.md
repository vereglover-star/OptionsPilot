# TODO.md — prioritized work queue

See `PROJECT_STATE.md` for narrative context on why each item is where it
is. This file is the flat, actionable checklist version.

## High Priority

- [x] **V0.9.0 — the verification floor** — done 2026-08-02, 11 commits
      (`2707a01`…`e403da6`), 2065 → 2158 in the suite. Version constant
      reconciled + a docs-version gate; dependency lockfile on CI and the
      release path; ruff; coverage measured (91.49%) and ratcheted; the API
      contract check wired after three milestones of never running, plus a ban
      on orphaned gate scripts; a two-platform CI matrix; 3,238 build artifacts
      untracked; SHA-256 checksums published and enforced; client-side
      Authenticode verification. **C9-3/C9-4 deferred by business decision** —
      see "Deferred by user decision" below.

- [ ] **Finish V0.9.1 — runtime & thread ownership.** In progress, and a
      blocking milestone: it must land before the V0.9.2 service extraction or
      every service owning background work gets extracted against a broken
      ownership model. **C1…C7 committed** — lanes + bounded worker pool, the
      market scan, manual scans, the backtest and the intelligence refresh all
      runtime-owned; real pause/resume/shutdown semantics; `ui/server.py` and
      `intelligence/engine.py` construct no threads at all; `exit()` genuinely
      single-entry. **Remaining for C8…C11:** the dead `_loop` deleted (C8), the
      startup HTTP poll deleted, the tracemalloc monitor removed, a
      GUI-free-assertable `DesktopApplication`. The exit criterion is a
      30-minute soak, not a green suite.

- [ ] **Add `pip-audit` and Dependabot.** Named in V0.9.0 finding H-4's
      definition of done and never given a commit — an **omission, not a
      deferral**, and it should not be allowed to hide inside the C9 deferral.
      Small and unblocked: fold into V0.9.1's first commit or run it standalone
      first.

- [ ] **Fix `CandleCache.close()`'s duplicate definition**
      (`optionspilot/data/cache.py:631`). Two `close` methods; Python keeps the
      last, so the live one is the *less* careful of the two — the earlier one
      also sets `self._conn = None`. Deliberately left in place during V0.9.0
      to avoid scope creep, with a comment marking it. Candidate fold-in for
      V0.9.1.

- [ ] **Render the update `assurance` level in the UI.** `apply_update` returns
      `signature_verified` / `hash_verified` / `size_only`, `index.html` reads
      none of it, and `docs/AUTO_UPDATER.md` §5.1 claims "the UI says so" for
      the degraded case. The claim is not implemented. Pre-existing since
      V0.9.0-C8 and widened by C9-2, which added a fourth level nothing renders.

- [ ] **Click the X button on a real desktop, once.** The only thing the V0.8.2
      audit could not cover. The deadlock was reproduced and verified fixed
      against the real stack with a real `WM_CLOSE` (old: pump dead 40s, window
      never closes; new: 0.0s stall, closes in 1.14s), but nobody clicked the
      button by hand and the audit environment's windows are not on the
      interactive desktop, so the visual symptom was never on screen. Same
      one-minute pass for **tray Restart** (fix is unit-tested, never run end to
      end) and tray Exit.
- [x] **V0.8.2 Independent audit of the V0.8/V0.8.1 runtime** — done
      2026-07-30. Ten defects found and fixed at the root, headed by the
      close-button freeze: pywebview runs `closing` handlers on the WinForms
      message pump, and `on_closing` called `evaluate_js`, whose WebView2
      continuation is scheduled on that same pump behind an untimed
      `semaphore.acquire()`. Also: `Restart` could never work, a frozen build
      relaunched itself with its own path as `argv[1]`, the single-instance mutex
      existed twice, the one maintenance slot admitted 8 of 8 concurrent
      workers, an `async def` WebSocket handler could stall every HTTP request,
      `hello.accepted` sent a null timestamp, the idempotency store held a SQLite
      write transaction across a network call, and `tracemalloc` stored ten
      frames per allocation to feed a field that reads none. 2056 → 2065 tests,
      new `tests/test_runtime_lifecycle.py` (nothing previously asserted "no
      thread leaks" or "no scheduler duplication", both certification criteria).
      Root causes and reasoning: `docs/CHANGELOG.md`.
- [x] **V0.7.0 Platform foundation & cross-platform architecture** — done
      2026-07-28. The desktop UI stopped owning application logic. New
      `optionspilot/services/` (portfolio, watchlist, intelligence projections,
      notifications, workspace, sync inventory, frozen view models,
      `ServiceRegistry`) and `optionspilot/host/` (capability profiles per
      target + OS adapter). Workspace state moved off `localStorage` onto the
      server (`GET/POST/DELETE /api/workspace`); `GET /api/host` and
      `GET /api/diagnostics/sync` added. Seven new architecture guards, each
      verified to fail when deliberately broken. 1908 → 2027 tests, new 21-check
      `scripts/workspace_check.py`. **Fixed a three-milestone-old defect:**
      `/api/learning` read its `WeightStore` from a CWD-relative `Path("data")`,
      so the Learning tab reported the wrong learned weights on every real
      install. Full design + the nine remaining platform blockers:
      `docs/ARCHITECTURE-PLATFORM.md`.
- [ ] **Contract hardening for a second client** (`ARCHITECTURE-MOBILE.md` §18
      items 1-3, 6). `/api/v1` aliases, a normalized `{"error": {...}}`
      envelope, `Idempotency-Key` on mutating endpoints, and the
      `{type, v, seq, data}` WebSocket envelope. All four are cheap while the
      server and its only client update in lockstep, and all four become
      migrations the day a client exists that cannot. Nothing in V0.7.0 depends
      on them; they are the next thing that gets more expensive by waiting.
- [ ] **Move chart drawings server-side.** The last client-trapped domain
      (`localStorage: chDraw:<symbol>`, `{version:3, items:[…]}`) — recorded in
      `services/sync.CLIENT_TRAPPED`. Needs a real one-time import path and a
      migration, not a default: these are user work-product and shipping half of
      it would risk the annotations it is meant to protect. Also
      desktop-improving on its own (drawings would survive a WebView reset).
- [ ] **A durable notification store.** `NotificationService` routes and
      catalogues; history is still the `NotificationCenter`'s in-memory ring of
      200, so a restart loses unread items and a device that was asleep cannot
      catch up. Already flagged as H5 in `ROADMAP-V3-UX.md`; V0.7.0 strengthens
      the case rather than resolving it.

- [x] **V0.6.1 Intelligent user experience & interactive onboarding** — done
      2026-07-28. The backend had become far more sophisticated than the
      experience of using it: nothing was missing, everything was unexplained.
      New `optionspilot/ui/guide.py` (pure domain layer: state validation, merge
      semantics, feature-usage → tutorial recommendations) behind
      `GET /api/guide` + `POST /api/guide/state`, with progress persisted in
      `settings.json` rather than localStorage so a reinstall does not greet a
      returning user as a beginner. In `index.html`: a data-driven tutorial
      engine (11 walkthroughs, 52 steps, spotlight + floating card, advancing on
      real clicks on real controls with the page left fully interactive), a
      37-term plain-English glossary with adaptive hover tips, a searchable help
      centre on `?`/`Ctrl+K`, per-screen Learn buttons, teaching empty states,
      an app-wide reduced-motion switch, and **order-ticket guardrails** that
      make the three combinations `OrderManager.place` refuses unassemblable —
      each correction explaining what changed, why, and what to do instead.
      Two defects found by the new browser suite and fixed. 1849 → 1908 tests;
      new 135-check `scripts/guide_check.py`. Design: `docs/ONBOARDING.md`.

- [x] **V0.6.0 Trading Intelligence Engine** — done 2026-07-28. The analytical
      brain: one layer that turns everything already recorded about completed
      trades into structured, evidence-backed insight every other part of the
      app consumes rather than recomputes. The app knew a lot about its trader
      and knew it in four unrelated places (`journal.db`, `experience.db`,
      `data/coach/*.json`, `learning/weights.json`), with no answer at all to
      *what am I good at, what keeps costing me money, am I improving, what
      should I learn next*. New subpackage `optionspilot/intelligence/`
      (17 modules): `facts.py` (the one join → `TradeFact`), `stats.py` (every
      formula), `performance.py` (a 38-metric registry), `behavior.py` (22
      detectors), `patterns.py` (automatic edge discovery over 19 dimensions
      with Benjamini–Hochberg false-discovery control), `risk.py`,
      `confidence.py` (8 self-explaining composite scores), `goals.py`,
      `curriculum.py` (16 triggered lessons), `recommend.py`, `timeline.py`,
      `achievements.py`, `reports.py`, `engine.py`, `store.py`. New
      `/api/intelligence/*` surface; Dashboard, Coach, Journal and Learning all
      project from one snapshot. Imports `core` only, so it sits *below* the
      coach. **No trading-behaviour change, no new dependency, no new tab.**
      Four defects found by self-audit, each with a regression test.
      1468 → 1849 tests; new 54-check `scripts/intelligence_check.py` and
      `scripts/intelligence_benchmark.py`. Design:
      `docs/TRADING_INTELLIGENCE.md`.

- [ ] **Capture MFE/MAE per trade.** `ExperienceRecord` models maximum
      favourable/adverse excursion and nothing populates them, because both need
      intrabar data the app does not have on delayed per-cycle quotes. Until
      they exist, the intelligence layer cannot answer "how much of the move did
      you capture?" — one of the most useful exit-quality questions there is.
      Wants a tick recorder or a streaming provider.

- [ ] **Record the signal-to-entry latency.** It is the missing input for the
      one behaviour the engine permanently declines to assess (hesitation), and
      it would also make "entering too late" measurable directly rather than by
      the RSI proxy. Nothing currently timestamps when a setup first appeared.

- [x] **V0.5.7 Market Data Control Centre** — done 2026-07-27. The entire
      user-facing management layer over the V0.5.2–V0.5.6 market-data
      subsystem, which was production-grade and completely unsteerable: every
      real user question was answered only by reading `logs/data.log` or by
      editing `config.yaml` and restarting. New modules `data/control.py`
      (`MarketDataControl` — dashboard, credentials, ordering, connection
      tests, eight maintenance jobs, recommendations; composed *over* the
      registry, which never learns about it), `data/credentials.py` (owner-only
      `credentials.json`; `environment → stored → config.yaml`; plaintext
      leaves only through `resolve()`) and `data/faults.py` (QA-mode fault
      injection firing inside `fetch_history`, 404-gated and off in every
      shipped build). New `/api/marketdata/*` surface and a Settings ▸ Market
      data panel: a card per provider, a 21-column live dashboard, failover
      summary, recommendations, maintenance with progress + Stop, a
      plain-English explainer, and a gated QA panel. Behaviour changes:
      `enabled: false` now *constructs* and benches a provider (it must be
      listable and re-enableable); `ordering_mode` (static/hybrid/dynamic)
      supersedes `dynamic_ranking`; `monitor.health_state()` is a derived
      human-facing state beside the `status()` gate; reorder rewrites
      priorities 10/20/30. **Five defects found by self-audit**, each with a
      regression test — including a hand-edited `marketdata.json` crashing
      startup. 1257 → **1468 tests** (+184); new 46-check
      `scripts/marketdata_check.py`. Design: `docs/MARKET_DATA.md` §29–41.

- [x] **Exercise the adapters against their real APIs** — done 2026-07-27, and
      it immediately earned its keep. **Twelve Data and Alpha Vantage
      authenticate and serve**; Yahoo and yfinance work normally. **Finnhub
      returns HTTP 403 to a valid free key** because `/stock/candle` moved to
      its paid tiers — and the app was reporting that as "the API key was
      rejected", sending users to regenerate a key that was never wrong. Fixed
      by splitting 401 from 403 (`ProviderEntitlementError`,
      `STATUS_PREMIUM_REQUIRED`, `verify_credentials()` on the free `/quote`
      endpoint) without weakening authentication, plus two knock-on fixes:
      `deepest_earliest` now excludes `monitor.permanently_unusable`, and
      `free_tier_serves_history` stops Finnhub being recommended. 19 regression
      tests. Full account: `docs/MARKET_DATA.md` §41.

- [ ] **Run `scripts/marketdata_benchmark.py --live`** with the working Twelve
      Data / Alpha Vantage keys. The certification above proved authentication
      and one request per provider; this measures latency, quality and
      cross-provider agreement over a real workload, which is what the ranking
      is actually calibrated against.

- [ ] **Reconsider Finnhub's place in the shipped chain.** It sits at priority
      40, ahead of Twelve Data and Alpha Vantage, on the strength of a free
      tier it no longer has. It costs nothing where it is (it benches itself),
      but a user on a paid Finnhub plan is the only one it now helps — so the
      default order is arguably wrong for everyone else. Cheap to change
      (`provider_priority`), worth a decision rather than a drift.

- [x] **V0.5.6 Chart interaction hardening** — done 2026-07-27. Two reported
      bugs, both root-caused from the real `cache.db`. (1) **Every symbol on 1D
      stuck behind "the cached bars failed validation and were discarded"**:
      Yahoo stamped daily bars at the 09:30 ET session open (13:30 UTC) and
      yfinance at exchange midnight (04:00 UTC), so the cache — keyed
      `(symbol, timeframe, ts)` — held a row per convention per trading day
      (SPY: 6,517 rows for ~3,258 days), the frame's spacing read 0.40 intervals
      and validation correctly refused it. Recovery never completed because
      validation ran in `_settle`, *after* the ladder had committed, leaving no
      provider to fall through to and the bad rows on disk, so Retry repeated it
      forever. Fixed at all three points: `base.session_index` (one convention,
      enforced in `HistoryAdapter.fetch_history`), `cache._migration_3` (repairs
      existing installs; 17,957 daily+ rows -> 11,831, intraday untouched), and
      disk tiers that validate before committing and `_quarantine` on failure.
      (2) **Viewport/zoom corruption**: nothing defined a legal viewport, so a
      symbol switch inherited the previous instrument's zoom and a resize left 4
      bars of 281 visible. Six invariants now enforced in `chClampViewport`;
      `CH.restoringViewport` became a depth counter. +19 tests (1257);
      `chart_check` 48 -> 65; new 110-cell browser matrix. Full report:
      `docs/CHART_CERTIFICATION.md` Part II.

- [x] **V0.5.5 Chart production certification** — done 2026-07-27. Ten
      defects found by reproducing them, each a way the chart could fail while
      the backend and every test reported success. Headline: **the price axis
      had no owner** — lightweight-charts disables `autoScale` permanently on
      the first price-axis drag and nothing restored it, so a pinned band
      outlived every symbol switch and rendered other symbols off-screen while
      volume kept painting (the reported "IWM shows only volume" bug). Also:
      Yahoo's 30-minute closing stub bar condemning every 1h frame; a `NaT`
      timestamp 500'ing `/api/candles`; null-OHLC bars rendering as invisible
      whitespace under `state="complete"`; an out-of-order payload collapsing
      to one candle; a malformed indicator wiping the chart; a render failure
      reporting `complete`; and `chart_check.py`/`browser_check.py` running against
      the user's REAL data root (`cwd=scratch` stopped isolating in V0.4.4 —
      they now pass `OPTIONSPILOT_HOME`). +6 tests (1238); `chart_check` 42 -> 48 (the new
      invariant: visible candles must intersect the visible PRICE band); 41
      adversarial scenarios through the real renderer. No version bump, no
      trading-behavior change. Full report: `docs/CHART_CERTIFICATION.md`.

- [x] **Provider API-key management in Settings** — done in V0.5.7. Paste,
      mask, replace, remove, test, with owner-only persistence in
      `data/credentials.json` and environment variables still taking
      precedence (and the page saying so when one does).

- [x] **Expand the provider health dashboard** — done in V0.5.7. The live
      dashboard in Settings ▸ Market data carries 21 columns including the
      enabled-vs-configured split, key source, per-interval capability and
      current availability. Help ▸ Diagnostics is unchanged and still the
      trace-level view.

- [ ] **Permanent history-loading stress coverage.** Rapid scrolling,
      wheel-holding, jumping between oldest and newest, and history arriving
      while live updates do were exercised through a throwaway viewport harness
      during V0.5.6 but never committed. `scripts/chart_check.py` 49-54 cover
      the viewport invariants; the scroll-stress matrix does not exist.

- [ ] **Get one non-Yahoo provider actually working.** Stooq is **dead** as of
      2026-07-27: it answers every request with a JavaScript proof-of-work
      challenge (verified live on `stooq.com` and `stooq.pl`), which a `urllib`
      client cannot satisfy and which this project will not circumvent. With no
      API keys configured the app now has exactly **one** real source — Yahoo,
      reached by two code paths that share one upstream and one failure domain
      — and Yahoo rate-limits by IP (a 429 was observed during the V0.5.5
      pass). A free Finnhub or Twelve Data key is the only route to genuine
      independence today. Decide between: shipping with a documented single
      point of failure, prompting the user for a free key on first run, or
      replacing Stooq with another keyless source.

- [ ] **Enforce cross-provider agreement, don't just measure it.**
      `quality.disagreement()` computes the median relative close difference
      and the diagnostics replay compares providers on demand, but nothing
      flags a disagreement during normal operation — so a dividend-adjusted and
      an unadjusted series can still be stitched together in the cache without
      comment. Wants its own design (tolerance, what to do on a breach,
      whether to quarantine the cache rows); out of scope for V0.5.5.

- [ ] **Authenticode code signing + checksums** — the remaining security gap:
      sign the setup + app exe in `release.yml` (removes SmartScreen warnings),
      add signature verification to `update/validation.py` (designed as a drop-in
      check), publish a SHA-256 checksums asset the validator enforces, replace
      the placeholder `LICENSE`, and run one manual end-to-end update QA on real
      Windows (`docs/AUTO_UPDATER.md` §7).

- [x] **V0.5.4 Enterprise provider expansion** — done 2026-07-26. Added
      Finnhub (40), Twelve Data (50) and Alpha Vantage (60) behind the keyless
      chain; `data/http_adapter.py` (shared keyed-HTTP base + timezone
      contract) and `data/ratelimit.py` (request budgets, persisted, feeding
      the ranking as pressure). Credentials resolve environment-first and are
      redacted by default in every exportable payload; a missing key disables a
      provider quietly and explains itself in diagnostics. Fixed two
      pre-existing defects: `deepest_earliest` counted providers that could
      never answer, and the stale tier could report `stale` with zero bars.
      +180 tests (1232); stress 65 -> 88 scenarios. No version bump, no
      trading-behavior change. Full design: `docs/MARKET_DATA.md` §23-27.

- [ ] **Verify each keyed adapter against its real API.** All 1232 tests run
      against canned payloads, so the three response shapes are as
      *documented*, not as *observed*. One live run per provider with a real
      key (`python scripts/marketdata_benchmark.py --live`) would confirm the
      parsers and — more importantly — the error translations, which are the
      part most likely to differ from the docs.

- [x] **V0.5.3 Market-data production readiness** — done 2026-07-26. Made the
      V0.5.2 subsystem *operable*: `data/health.py` (`ProviderHealthMonitor` —
      one owner for counters, latency/p95, breaker, per-day totals and the
      ranking score, replacing the split between `adapter.ProviderHealth` and
      `registry._Breaker`), health-ranked provider ordering (cold ranks equal
      priority, so the shipped order is unchanged; `dynamic_ranking: false`
      pins it), **Help ▸ Diagnostics** with JSON/text export and per-request
      replay, `data/config.py` + `market_data:` in `config.yaml` (every
      operational knob, no code edit), cache intelligence, structured
      `key=value` request logging, `data/discovery.py` (advisory capability
      discovery, off by default) and `scripts/marketdata_benchmark.py`.
      Fixed two real accounting bugs found by the consolidation: a provider
      serving consistently-unusable bars was recorded as *succeeding* and never
      tripped its breaker, and a demoted success could never build a failure
      streak past 1. +172 tests (1052); stress 41 → 65 scenarios; `chart_check`
      49 → 52 (three new checks drive the dashboard and a replay in a real
      browser). No new provider, no version bump, no trading-behavior change.
      Full design: `docs/MARKET_DATA.md` §13–22.

- [x] **V0.5.2 Market-data subsystem** — done 2026-07-26. Replaced the chart
      history stack with a capability-driven, multi-provider architecture inside
      `optionspilot/data/`: `capabilities` (measured per-interval depth, from
      *now*), `adapter` (`HistoryAdapter`; adapters raise typed errors instead of
      returning empty frames), three adapters (Yahoo chart JSON / yfinance /
      Stooq) + `LegacyProviderAdapter`, `registry` (ordering, pre-network
      eligibility, circuit breakers with half-open recovery), `service`
      (`MarketDataService`'s tier ladder, and the one place `exhausted` /
      `empty` / `stale` / `failed` are told apart), `quality` (semantic
      validation with a report), `diagnostics` + `/api/diagnostics/marketdata`,
      and a rebuilt `cache` (atomic, integrity-checked, self-healing, versioned,
      provider-attributed). Frontend gained an explicit load state machine and
      an honest "start of available history". +229 tests (870);
      `scripts/marketdata_stress.py` (41 offline scenarios, wired into
      `verify.ps1`) and `scripts/marketdata_probe.py` (re-measures provider
      limits) are new; `chart_check.py` 44 → 49 and now green end to end.
      Full design: `docs/MARKET_DATA.md`. **Manual QA not yet run:**
      `docs/QA_MARKET_DATA.md` (84 checks).

- [x] **A second non-Yahoo intraday provider** — done in V0.5.4, three times
      over (Finnhub, Twelve Data, Alpha Vantage). A Yahoo outage is now
      survivable at intraday resolution given any one API key.

- [ ] **Optional: act on cross-provider disagreement.**
      `quality.disagreement()` measures it, diagnostics record it, and
      `compare_providers` surfaces it per request — but nothing acts on it,
      deliberately: deciding which source is "right" is not something the data
      layer can know. Worth revisiting only with a third independent intraday
      source, where a majority vote would actually mean something.

- [x] **V0.5.0 Auto-Updater 1.0** — done 2026-07-26. Self-contained
      `optionspilot/update/` subpackage (core + stdlib only, `urllib`, no new
      dep): SemVer ordering, GitHub Releases client (installer asset only),
      never-raising checker (stable/beta channel, launch/daily/weekly frequency),
      streamed downloader (progress/cancel, atomic finalize), validation
      (size/hash, Authenticode-ready), installer launcher (mandatory `pre-update`
      backup → `/VERYSILENT` → restart), `UpdateService` state machine.
      `/api/update/*`; launch check gated on `run_loop`; prefs in
      `RuntimeSettings`. Frontend: Settings ▸ Software updates, Help ▸ Check for
      Updates…, update dialog. +105 tests (651), all offline via fakes. Full
      design: `docs/AUTO_UPDATER.md`. **Deferred within scope:** delta updates,
      private update servers, enterprise policies (§8).

- [x] **V0.4.6 Professional Windows Installer 1.0** — done 2026-07-26. Completed
      `installer/OptionsPilot.iss` (Inno Setup: C:\Program Files install, stable
      AppId in-place upgrades, Start Menu + optional desktop shortcut, app icon,
      Programs-and-Features registration, uninstall-time data prompt default No);
      `scripts/build_installer.ps1`; `release.yml` builds + uploads the setup exe
      alongside the zip. +19 tests (546). Full design: `docs/INSTALLER.md`.
      **Next:** Authenticode code signing (SmartScreen); then auto-updater;
      manual installer QA on real Windows; replace placeholder `LICENSE`.

- [x] **V0.4.5 Professional Release Pipeline 1.0** — done 2026-07-23. GitHub
      Actions `ci.yml` (push/PR, reusable) + `release.yml` (tag `v*`: build +
      package + GitHub Release); single-source `__version__` (pyproject dynamic);
      `scripts/package_release.ps1` + `release_notes.py`; placeholder `LICENSE`;
      unwired Inno Setup installer template. No behavior change; +7 tests (527).
      Full design: `docs/RELEASE.md`. **Next:** wire the installer into
      `release.yml`; replace the placeholder `LICENSE` before a public release.

- [x] **V0.4.4 persistent storage & automatic data migration** — done
      2026-07-23. `core/paths.py::AppPaths` (single source of truth, root at
      `%LOCALAPPDATA%\OptionsPilot`) + `core/migration.py::initialize_storage`
      (one-time lossless legacy import, backups, empty versioned framework);
      bootstrap/Orchestrator/UIServer/selftest wired through it. No behavior
      change; +28 tests (520). Full design: `docs/STORAGE.md`. **Next infra:**
      automatic updater (replace install dir only; back up before applying).

- [x] **V0.4.3 AI Coach 2.0 (phase 1)** — done 2026-07-23. Per-trade 10-category
      scorecard (`coach/categories.py`) + outcome snapshot; mentor dashboard
      (`coach/analytics.py`: sub-scores, category trends, streaks, confidence-
      scored pattern detection, improvement timeline, ≤5 auto-expiring action
      items); `GET /api/coach → dashboard` (cached) + coach-tab UI. Manual trades
      only, additive, backward-compatible. +22 tests (492). Coach 2.1 ideas
      (persist dashboard history, AI-trade coaching, overtrading detection) left
      for a future phase.

- [x] **V0.4.2 architecture audit + three refactors** — done 2026-07-23
      (`docs/ARCHITECTURE-AUDIT-V0.4.2.md`): shared `core/sqlite.py` foundation
      (`connect` + `user_version` migrations) adopted by all five stores;
      `ui/server.py` import cleanup + public `orchestrator.WINDOW_DAYS`;
      executable layering-guard `test_architecture.py`. Behavior-preserving,
      +16 tests (470). Optional follow-ups (orchestrator split, `core→config`
      de-inversion, journal SQL stats) left per the report's judgment-over-churn
      guidance.

- [x] **V0.4.0 Experience Engine — phases 1–2** — done 2026-07-23:
      `optionspilot/experience/` (rich 100k-scalable `ExperienceStore` +
      migrations, deterministic `SimilarityEngine`, advisory calibration),
      recorded alongside the journal, best-effort. +32 tests (424 total).
      See `docs/ROADMAP-V0.4-EXPERIENCE.md`.
- [x] **V0.4.1 — Phase 3: Experience Engine integration** — done 2026-07-23:
      centralized `build_snapshot` (feature symmetry), advisory
      historical-similarity explanation on tradeable signals, Experience API
      (`/api/experience[/similar]`), storage schema v2 (`market_regime` + SQL
      aggregates). +30 tests (454 total). Advisory only — nothing touches
      gate/risk/execution.
- [ ] **V0.4.0 — Phase 4: `learning_mode` axis + Exploration mode.** New
      orthogonal axis (normal/exploration); tagged, strictly risk-limited
      lower-confidence paper trades. `ExperienceRecord.exploration` already exists.
- [ ] **V0.4.0 — Phase 5: AI Performance dashboard.** New tab over
      `ExperienceEngine.stats()` + slices; backend endpoint first, then
      single-file frontend (manually browser-verify — no automated UI coverage).
- [ ] **V0.4.0 — Phase 6: strategy-discovery infrastructure.** Group experiences
      by shared characteristics for later pattern mining. Infra only.
- [ ] **Populate MFE/MAE + `risk_multiple`** once intrabar tracking / stop-premium
      capture exists (fields already modelled; see roadmap doc §10).

- [x] **V3.1 chart-stabilization sprint** — done 2026-07-18 (`61a2c60`…
      `2bcb84a`): per-ticker reliability root-causes fixed, 13 timeframes,
      infinite scroll-back, TradingView-style editable drawing objects,
      collapsible synced Trade-tab chart, flicker-free live updates, and
      a 19-check `chart_check.py` regression suite in `verify.ps1`.
      Remaining chart-adjacent item: one market-hours pass to confirm the
      live-update path against a real feed (verified with a simulated tick).
- [x] **V2-4 drawing/overlay remainder** — done 2026-07-16, then rebuilt
      2026-07-18 (V3.1-4) into an editable overlay-canvas object model:
      fib / zone / note / trend / level, all selectable, draggable,
      resizable, recolorable, lockable, hideable, and persisted.
- [ ] **V2-4 layout remainder** (only if the user wants it): the full
      three-panel workspace layout (top bar / right sidebar / bottom
      panel) and multi-chart layouts — a large UI restructuring, left as
      an explicit user decision. See `ROADMAP-V2.md`.

## Deferred by user decision

- [ ] **Authenticode signing of release builds (V0.9.0-C9-3, C9-4)** — deferred
      2026-08-02. **A business decision, not unfinished engineering.** Signing
      needs a *purchased* certificate whose key must live on certified hardware
      or in a cloud signing service, and OptionsPilot is not entering public
      distribution — so it buys nothing today beyond removing a SmartScreen
      warning shown to downloaders who do not exist. The client half shipped
      complete in C9-1/C9-2 and is enforcing now; an absent signature is
      deliberately tolerated, so nothing regresses by leaving this indefinitely.
      Remaining work is release-side only (`release.yml`, the `SignTool=` line
      already commented at `installer/OptionsPilot.iss:75`, and docs) — roughly
      one engineering day, fully planned.
      **Revisit when: a decision to distribute publicly.** Read the C9
      implementation plan §1 *before purchasing* — the certificate type
      determines the CI design. Two constraints to carry forward:
      **`SHA256SUMS` must be generated AFTER signing** (signing changes the
      bytes and C8 enforces that manifest), and `REQUIRE_SIGNATURE` must stay
      `False` while releases are unsigned or every build becomes uninstallable.

- [x] **Downloaded release crashed on launch (V0.3.5)** — done 2026-07-22:
      a GitHub-downloaded, Explorer-extracted release died with
      "Failed to resolve Python.Runtime.Loader.Initialize" because .NET
      Framework refuses to load Mark-of-the-Web-flagged managed assemblies
      (pythonnet's Python.Runtime.dll). Reproduced end-to-end, fixed with
      `optionspilot_app.py::unblock_bundle()` (self-unblock at startup),
      +3 regression tests, verified against a MOTW-flagged release copy.

- [x] **Rebuild and smoke-test the exe** — done 2026-07-18 as part of the
      yfinance packaging fix: rebuilt with `--collect-all yfinance`,
      packaged selftest gate PASS, then verified live from the packaged
      desktop app (206 daily SPY candles, 624 SMCI 5m candles,
      231-contract chain over HTTP against a scratch data dir).

- [ ] **`OptionsPilot.exe serve` from the windowed exe never binds its
      port** — discovered 2026-07-18 while verifying the packaging fix:
      the process starts (broker/orchestrator threads spin up) but
      uvicorn never listens, with no log output. Pre-existing, unrelated
      to yfinance; desktop `ui` mode and dev-repo `python -m optionspilot
      serve` both work, so nothing user-facing is broken. Diagnose the
      windowed-stdio + `uvicorn.run` interaction if `exe serve` ever
      needs to be a supported path (or document it as unsupported and
      make it exit with a clear message).

- [x] **Live-verify the V2-3 frontend in a real browser** — done 2026-07-16
      against a scratch data dir: toggle switch + persistence across reload,
      Coach tab empty state, manual round trip → coach review rendered with
      expandable detail, mode-axis orthogonality, zero console errors.
- [x] **Commit V2-3** — committed 2026-07-16.
- [x] **Rebuild and smoke-test the exe** with V2-3 included — done
      2026-07-16 after the user closed the running app: rebuilt via
      `build_exe.ps1` (app data backed up/restored by the script), then
      smoke-tested the packaged exe in serve mode against a scratch data
      dir: AI→Human toggle, manual SPY round trip, scan → coach review
      rendered in the Coach tab, zero console errors.
- [x] **Update `docs/ROADMAP-V2.md`**: V2-3 checklist flipped to `[x]`.

## Medium Priority

- [x] Fix `pyproject.toml` `package-data`: `"data_assets/*"` added
      2026-07-16.
- [x] Add `Pillow` to the `dev` extra in `pyproject.toml` — done 2026-07-16.
- [x] Inline comment for `engine.operating_mode` in `config.yaml` — done
      2026-07-16.
- [ ] Decide on and implement stock/share (non-option) manual positions —
      deferred from V2-2. Touches `broker/orders.py`, `PaperBroker`, the
      Trade tab chain/ticket UI (currently options-only).
- [x] **Browser smoke check committed** — done 2026-07-17:
      `scripts/browser_check.py` (Playwright driving the system's Edge via
      `channel="msedge"`, no browser download; soft-skips if the optional
      `[browser]` extra isn't installed) launches the app against a scratch
      data dir and visits every tab checking for zero console errors. Runs
      automatically as part of `scripts/verify.ps1`. **Still open**: this
      is tab-navigation-level smoke coverage, not deep per-flow regression
      testing (mode toggle, manual order placement, coach review rendering
      specifically) — extending `browser_check.py` (or adding sibling
      scripts) with those specific flows remains a real opportunity. Gotcha
      worth keeping if you do: lightweight-charts coalesces chart clicks
      faster than ~500ms apart as double-clicks — pace scripted two-point
      drawing-tool clicks ≥700ms apart. **Update 2026-07-17 (V3 session):**
      per-flow Playwright scripts now exist and were used to verify every
      V3 milestone (chart failure states/races, the full order-ticket flow,
      settings search, a real backtest run, the `?` overlay) — but they
      live in the session scratchpad, not the repo. Promoting them into
      `scripts/` as committed regression checks is now the concrete version
      of this item.
- [ ] One market-hours manual pass over the Trade tab's *fill* path (fill →
      stop-loss pre-arm → position row → close-prefill) — the V3 session
      verified everything up to the risk gate's after-hours rejection, but
      no real fill could occur with the market closed.
- [ ] Consider a minimal CI workflow (`.github/workflows/tests.yml`
      running `scripts/verify.ps1` or just the pytest suite on push/PR) and
      `ruff` for linting/formatting — recommended but not installed; see
      `docs/CONTRIBUTING.md` "Automation opportunities" for the reasoning
      on why each is a real decision, not a trivial add.

## Low Priority

- [x] Serve a favicon — done 2026-07-17: `assets/optionspilot.ico` copied
      to `optionspilot/ui/static/favicon.ico` (so it's bundled the same way
      in dev, a wheel, and the exe) and served at `GET /favicon.ico`. Found
      by the new `scripts/browser_check.py`'s first real run.

- [ ] V2-4 groundwork: evaluate bundling `lightweight-charts` (Apache-2.0)
      into `optionspilot/ui/static/` (no CDN — must work fully offline
      inside the PyInstaller bundle).
- [ ] Review whether `CoachProfile` (V2-3) already covers enough of V2-6's
      "improvement dashboard" spec to shrink that phase's scope, or whether
      it's purely additive.

## Future Ideas (unscheduled — see `docs/ROADMAP-V2.md` for the full phase breakdown)

- [ ] **V2-4 — Chart workspace**: TradingView-inspired layout (top bar,
      right sidebar, bottom panel), interactive candlestick chart with
      zoom/pan/crosshair/multi-timeframe/indicator overlays, drawing tools
      (trendline, horizontal line, fib retracement, rectangle, note)
      persisted per symbol, trade-from-chart.
- [ ] **V2-5 — Replay engine**: pick a historical day, hide future candles,
      play/pause/step/speed controls, separate replay paper account, coach
      reviews replay trades identically to live ones.
- [ ] **V2-6 — Journal & improvement dashboard**: chart-context snapshots
      per trade (candle window + entry/exit markers, re-rendered on demand
      — the deliberate substitute for static screenshots), notes/emotions
      capture fields, filtering by strategy/symbol/P&L/date/mistake type.
- [ ] Candle cache for the live loop (incremental fetch + merge) to reduce
      yfinance traffic during long sessions.
- [ ] A real live-broker adapter (Alpaca's options paper API is the natural
      first candidate) — explicitly gated on sustained paper profitability;
      do not build without the user's direct, dedicated request.
- [ ] News / economic-calendar / sentiment inputs as new scorer evidence
      types.
- [ ] Portfolio-level risk (correlated positions, sector exposure limits).
- [ ] A paid market-data feed adapter (Polygon/Tradier) as an alternative to
      the free, ~15-minute-delayed yfinance provider — slots into the
      existing `MarketDataProvider` interface without touching the engine.
