# NEXT_SESSION.md — start here

Concise session-to-session handoff. Keep this current — update it at the end
of every significant session, not "later." For the detailed narrative behind
any of this, see `PROJECT_STATE.md`; for the structured snapshot, see
`PROJECT_STATUS.md`.

**Last updated:** 2026-08-06, on closing **UI V2 · M3 — Home**, C1…C10.

## What to do next

**Start UI V2 · M4 — Trade**, the most valuable milestone in the programme
(`ROADMAP-UI-V2.md` §12 is the authority for which commit comes next).
`verify.ps1` is green across **17 gates** at 2716 tests.

**M3 is complete.** Home is rebuilt on the shell and the legacy dashboard is
deleted. Two things it deliberately did NOT do, both recorded in
`UI_MIGRATION_TRACKER.md` §9, and the first needs a decision before M4-C11:

1. **The legacy navigation and header are still in the file.** They were
   scheduled for M3-C10. Deleting them removes the shell's rollback path and
   `shell_check`'s last three assertions, and the M3 brief forbade touching
   navigation. The one-release requirement (`UI_V2_DESIGN.md` §16 Phase 2) is
   satisfied — v0.12.0 shipped the shell — so it is unblocked, not blocked.
   Replace those three assertions rather than deleting them, or the flag stops
   meaning anything.
2. **The metric cluster wraps below 1280** rather than dropping to four then
   three metrics with `[More]`, per §2.12. Revisit if M7's Surface Level work
   gives a better home for an overflow.

Reusable from M3, and worth reading before writing M4's markup: the
`instrument` component (`.ins`, `.ins-well`, `.ins-cluster`) with its two
invariants enforced in `token_check.py`, and `scripts/home_check.py` as the
model for a destination gate that measures geometry rather than structure.

## What M3 delivered

Home, rebuilt: three bands on a new `instrument` component, one
`/api/v1/home` payload with per-region failure, four states under one shape
invariant, and `home_check.py` as the 17th gate. The legacy dashboard is
deleted; the legacy navigation is not (see above).

## What M2 delivered

Eleven commits, `b380141`…`12510b3`. Five destinations plus Settings over the
existing nine sections, unchanged; frame, nav rail and system strip; command
palette, symbol jump, Flight Status, notification inbox, toast stack, keyboard
map and a Pilot scaffold. The shell is the default; the old navigation is one
toggle away in Settings for one release.

**The checks found three defects that looking would not have**: the legacy
`nav { width:200px }` element selector applied to the new rail and the content
painted over it below 1440px; a palette command named a tutorial id that does
not exist; and `Ctrl+K` was already bound to the help centre. Full detail:
`docs/CHANGELOG.md`, and `docs/UI_MIGRATION_TRACKER.md` §10 per commit.

## What M1 delivered (previous milestone)

Seven commits, `d665ad0`…`ace7a75`. The first milestone a user can feel: type a
symbol once and it is set everywhere — chart, chain, ticket and backtest are
renders of one context, and the selected contract, expiry and timeframe are
server-owned so they survive a restart and a cleared browser profile. Surface
Level (Guided/Focused/Full/Pro) arrives as a presentation-only third axis with
the chain's column set as its first consumer.

**The checks found three real defects that clicking did not.** A slow
`/api/chain` response adopted its own symbol and dragged the whole workspace
back to it; the selected contract had a server-owned home from C2–C3 that the
client never wrote to; and `#tk-spot` kept naming the previous symbol during a
load. `workspace_check.py` went from 21 assertions to 50.

Full detail: `docs/CHANGELOG.md`, and `docs/UI_MIGRATION_TRACKER.md` §10 has
the per-commit reasoning.

The programme is ten milestones, M0…M9, ending at V1.0.0. Four frozen design
documents precede it and **must not be modified**: `UI_V2_DESIGN.md` (why),
`UI_V2_WIREFRAMES.md` (where), `DESIGN_SYSTEM_V2.md` (what it looks like),
`UI_V2_VISUAL_EXPLORATION.md` (§8 is the freeze). If an answer is not in one of
them, `ROADMAP-UI-V2.md` §11 gets a row and the work stops at that boundary
rather than inventing one.

**M0 (V0.10.0) is complete and deliberately unreleased** — the user's decision:
it contains nothing a user can see, so **V0.11.0 is the release that carries
both milestones**, and it has not been cut yet. Nine commits
`8c5586e`…`6f859bb`: three token layers with a one-way dependency, the type
scale in `rem`, the spacing scale adopted where it already matched, a dual
focus ring, and two new static gates (`token_check.py`, `motion_check.py`). The
UI renders as it did before, which was the exit criterion.

**Two product decisions are settled** and recorded in `ROADMAP-UI-V2.md` §11:
Surface Level is **local only** (never synchronised), and the **light theme
ships after V2** — dark first. Six decisions remain open there; each names the
commit it blocks, and the frozen documents' recommendation is the default if
none is taken.

**Four ratchets** carry M0's unpayable debt, all in the gate scripts and all
allowed only to fall: 51 uses of `--legacy-fs-md` (13px), 313 off-scale spacing
values, 3 hardcoded transition durations, 3 prohibited keyframes. They retire
per destination in M3–M6, where the markup is re-authored and the layout can
actually be verified.

**V0.9.3 — a real API v1 — is still unstarted** and is not blocked by any of
this; it simply lost its place in the queue to the UI programme.

## Releasing is now one command

```powershell
.\scripts\release.ps1 0.9.3 -DryRun   # rehearse: every check, nothing modified
.\scripts\release.ps1 0.9.3           # release
```

Preflight → bump → `check_docs.py` + `verify.ps1` → commit + annotated tag →
push → watch `release.yml` and report the Release URL and artifacts (or the
exact failing step). Anything failing before the push rolls the repository back
to where it started. **Do not hand-type `git tag` / `git push` for a release
any more** — and note the two things the script deliberately will not do for
you: write the `docs/CHANGELOG.md` entry (preflight *refuses* to release
without one) and smoke-test the built exe. Guide: `docs/RELEASE.md`.

One thing to know before the first real run: the release branch is configured
in `scripts/lib/ReleaseConfig.ps1` and is set to **`V3-ui`**, because that is
where releases are actually cut today (`v0.9.2` points at the head of `V3-ui`,
which is well ahead of `main`). Change it to `main` when that branch merges.

## What V0.9.2 delivered

`ui/server.py` went from **1,892 to 1,629 lines** and is now a transport: it
decides status codes and response shapes, and nothing else. Every application
decision lives in `optionspilot/services/`.

* **C1** the service error hierarchy; **C2–C5** four extractions
  (`ChartService`, `MarketDataAdminService`, `TradingService`,
  `BacktestService`); **C6** the guide moved into `services/`.
* **C7–C8** closed finding H-7: services raise a coded `ServiceError`, and
  `ui/errors.py` is the ONE place a code becomes a status. Two deliberate
  corrections — an internal defect is now a **500** rather than a
  confidently-wrong **404**, and a client's unparseable timeframe is a **422**
  rather than a **502**.
* **C9** per-key idempotency locking plus request fingerprints (N-1, N-2): a
  key reused for a *different* body is a 409 instead of silently replaying
  someone else's result.
* **C10** a line ceiling on `ui/server.py`, with a second test that fails if an
  extraction leaves the ceiling slack.
* **C11** the registry built **and exercised** in a subprocess with no web
  framework loaded. It found a real defect no static check could see:
  `windows_toasts` arriving transitively through `notify/`.

**No trading-behaviour change.** Per-commit reasoning, including every gate that
passed while testing nothing until an induced-failure demo caught it:
**`docs/reports/V0.9.2.md`**.

## Three practices worth carrying forward

1. **The induced-failure demo is not ceremony — it is the only thing that tests
   the test.** Three separate commits this milestone shipped a gate that passed
   against deliberately broken code, and the demo is what caught each one.
2. **Match structure, not text.** Three text-matching assertions broke on prose
   explaining the rule they enforced (C1, C5, C11). AST every time.
3. **Re-run the same state before bisecting a networked gate.**
   `scripts/chart_check.py` is the only browser gate that hits live providers,
   and a bisect against it produced a confidently wrong conclusion in C3.

**Three things carried into V0.9.3.**

1. **F-1 is out of scope for this whole milestone, not just for one commit.**
   `_maybe_send_summaries` runs outside the server lock on a worker thread
   (`ui/server.py`, in `_background_cycle`). It is *orchestrator thread-safety*,
   which `ROADMAP.md` names explicitly in V0.9.1's "Out of scope" list. The C5
   report scheduled it into C6's review focus, which was wrong. It belongs to
   V0.9.2.
2. **The pool bound is derived, not chosen.** `DEFAULT_MAX_WORKERS` is 4
   because `UIServer` registers four worker tasks. A fifth without raising it
   does not error — the surplus job waits for a slot while its status says
   "running". `test_the_pool_is_large_enough_for_every_task_the_server_registers`
   is the guard.
3. **Extend the soak in the same commit that changes what runs on a lane.**
   Both real defects in C5 and the coverage gap in C6 were found by the soak,
   and both needed it extended first. It now fails a run in which a backtest or
   an intelligence refresh never actually executed — the C5 soak once passed a
   full 30 minutes with `manual ran: 0`.

**Still unassigned, and still not a deferral:** `pip-audit` and Dependabot are
named in V0.9.0's own definition of done (finding H-4) and have never received a
commit. Small and unblocked.

The standing execution protocol applies: one planned commit at a time,
re-validate every assumption against the live tree before implementing, and
demonstrate each new gate failing as well as passing.

## What was completed most recently? (V0.9.1 — runtime & thread ownership)

2158 -> a **2243-test suite** (+85). **Committed**, 11 commits `d92de20`…`0b08ae3`
plus C11. No feature, no trading-behaviour change, no new runtime dependency.
**One deliberate API change:** `/api/runtime` no longer carries `health.memory`.
Full detail: **`docs/CHANGELOG.md`**, entry `2026-08-03 — V0.9.1`.

**The one thing to understand.** `BackgroundRuntime` existed since V0.8 and did
not own what it claimed to. Alongside it ran a raw thread per manual scan, a raw
thread per backtest, a thread `intelligence/` started for itself, a complete
second scheduler nobody called, and an `exit()` guard two threads could both walk
through. Pause, resume, shutdown and health reporting described *intent*.

> ### ⚠ Five things here are load-bearing
>
> 1. **The `coordinator` lane is the default, deliberately.** Until a task opts
>    in, the worker path is never entered and no pool thread is created. That
>    inertness is the rollback story: reverting an activated task is deleting one
>    `lane=` argument.
> 2. **`DEFAULT_MAX_WORKERS` is derived, not chosen.** It is 4 because `UIServer`
>    registers four worker tasks. A fifth without raising it does not error — the
>    surplus job waits for a slot while its status still says "running".
> 3. **Pause never interrupts work in flight** (Decision D-2), so
>    `RuntimeSnapshot.pause_pending` exists and the tray says "Pausing". A client
>    that shows "paused" the moment the request returns is describing itself.
> 4. **`TaskSpec.on_demand` is what lets user-initiated work be a runtime task.**
>    `register` makes every task immediately due by design, so without it,
>    registering a "manual scan" task would run a scan on every construction.
> 5. **Insufficient evidence about your own change is a first-class answer.**
>    C10's comment claimed the polled endpoint was expensive; the benchmark said
>    0.02 ms. The comment was corrected rather than the measurement ignored.

**The three races, all the same shape, all measured:** manual-scan dispatch
(two requests, two cycles); `exit()` (**8 of 8 concurrent callers ran the
shutdown, and Restart spawned 8 successor processes**); and the pre-existing
`MarketDataControl` shape they both echoed. Checking a slot and then claiming it
is not claiming it.

**Verified by a 30-minute soak, not a green suite** — concurrency defects are
not deterministic and this suite has passed over a guaranteed deadlock before.

## What was completed before that? (V0.9.0 — the verification floor)

2065 -> a **2158-test suite** (+93). **Committed**, 11 commits `2707a01`…`e403da6`.
No feature, no trading-behaviour change, no new runtime dependency. Full detail:
**`docs/CHANGELOG.md`**, entry `2026-08-02 — V0.9.0`.

**What it was for.** Every milestone after this one refactors live code, and a
refactor is only as safe as the evidence that it changed nothing. That evidence
did not exist: the version constant had said `0.6.0` through four shipped
releases, dependencies were unpinned on the release path, coverage had never
been measured, `scripts/api_contract_check.py` had been written three milestones
earlier and *never once run*, CI was Windows-only against a portability
abstraction, 3,238 build artifacts sat in git, and an update was verified
against the size GitHub reported for the asset.

**The two findings worth carrying forward**, both from implementation rather
than review:

- **`signtool.exe` is a Windows SDK tool, so the client cannot use it.** The C9
  plan specified `signtool verify` for the updater's signature check; no end
  user has the SDK, so that check would have answered "cannot determine" on
  every real machine — present in the code, inert in production, exactly the
  `RiskManager.approve_manual_entry` failure. It uses `wintrust.dll` instead.
- **"Unsigned" and "invalid" must be separable.** The plan's `bool | None`
  verdict could not express its own Phase 1 policy. Every release before V0.9.0
  is unsigned; refusing those strands every existing installation behind an
  update they can no longer install. `SignatureVerdict` is four-valued, and
  C8's own backward-compatibility test is what caught it.

**Deferred by business decision — C9-3 and C9-4**, the release-side signing
pipeline and its docs. Signing needs a purchased certificate and this app is not
publicly distributed. **Not unfinished engineering**: the client half is complete
and enforcing, an absent signature is deliberately tolerated, so nothing
regresses by leaving it indefinitely. `ROADMAP.md` ▸ Deferred has the rationale,
the revisit trigger, and the one constraint that will bite whoever resumes it
(**`SHA256SUMS` must be generated after signing**).

## What was completed before that? (V0.8.2 — independent audit)

2056 -> 2065 in the suite (+9). **Not committed.** No feature, no dependency, no
architectural change: an audit of every V0.8/V0.8.1 change that treated the
previous certification as an unverified claim. Full detail:
**`docs/CHANGELOG.md`**, entry `[Uncommitted] 2026-07-30 — V0.8.2`.

**The one thing to understand.** The reported "clicking X freezes the app" was
real, reproducible from first principles, and *caused by a thread-ownership rule
that is invisible at the call site*. pywebview binds its `closing` event as
`Event(window, should_lock=True)` — handlers run **synchronously on the WinForms
message pump**. `_DesktopController.on_closing` called `window.evaluate_js`,
which posts a script to WebView2 and blocks on a semaphore released by a
continuation scheduled on *that same pump*. From the pump it can never arrive:
untimed `semaphore.acquire()`, no traceback, white title bar, "Not Responding".
The default preferences (`close_behavior="tray"`, prompt not yet dismissed) send
a fresh install straight down that branch. `on_closing` now decides and returns;
every consequence runs on a worker via `_defer`. **Read
`_DesktopController`'s class docstring before touching anything in that file.**

**Why the whole suite did not catch it.** The window double in
`tests/test_desktop_tray.py` was a plain recorder — `evaluate_js` appended a
string — so it modelled none of the thread contract, and one test actively
asserted the blocking behaviour (`server.closed == 1` *inside* the handler). The
double now raises `GuiThreadViolation`, a **`BaseException`** (the lifecycle code
wraps these calls in `except Exception`, and a real deadlock is not catchable),
whenever a pump-hostile call arrives on the closing thread.

**Also fixed:** `Restart` could never work (successor spawned before the
single-instance port was released); a frozen build relaunched itself with its own
path as `argv[1]`; two implementations of the single-instance mutex with two
copies of port 8786; the one maintenance slot admitted 8 of 8 concurrent workers
(a check-then-act, and the cause of the intermittent
`test_progress_is_reported_and_ends_at_one` failure); an `async def` WebSocket
handler called the lock-taking `status_payload()` on the event loop and could
stall every HTTP request in the process; `hello.accepted` sent
`"timestamp": null`; the idempotency store held a SQLite write transaction across
a network call; `tracemalloc` kept ten frames per allocation for a field that
reads none.

**New file:** `tests/test_runtime_lifecycle.py` — nothing previously asserted
"no thread leaks" or "no scheduler duplication", both of which were certification
criteria.

### Do this first

**Click the X button on a real desktop, once.** The close path was reproduced
broken and then verified fixed against the *real* stack (real uvicorn, real
`UIServer`, real pystray, real pywebview/WebView2, a real `WM_CLOSE`, with
`SendMessageTimeout(SMTO_ABORTIFHUNG)` as the probe): old handler = pump dead for
40 s and the window never closes; new handler = pump stalls 0.0 s, closes in
1.14 s. What is *not* covered is a human mouse click, and the visual symptom
itself — the audit environment's windows are not on the interactive desktop, so
the white title bar was never on screen to look at, only the pump condition that
produces it. Also worth the same one-minute pass: **tray Restart** (its fix — the
instance lock is now released before the successor spawns — is unit-tested but
never run end to end) and **tray Exit**.

## What was completed before that? (V0.7.0 — platform foundation)

1908 -> **2048** in the suite (+140); a new **21-check** headless-browser suite
(`scripts/workspace_check.py`, wired into `verify.ps1`). **Not committed.**
Full design, decisions and remaining blockers: **`docs/ARCHITECTURE-PLATFORM.md`**
— read it before touching `optionspilot/services/` or `optionspilot/host/`.

**The one thing to understand:** OptionsPilot was already a client-server system
that ships both halves in one process. What it lacked was a boundary between
*the application* and *the desktop transport*. `ui/server.py` held FastAPI
routing and, in the same 1,700 lines, the decisions about what a client is
shown — which twelve of thirty-eight metrics are a headline, how a maximum
drawdown is computed, what four buckets a pasted ticker list falls into. All
correct, none of it reachable without importing a web framework. This milestone
extracted that; **it moved code, it did not rewrite it.** `UIServer` kept every
method name and every wire shape.

**No trading-behaviour change, no new dependency, no new tab, no UI redesign,
and no test removed.** `RiskManager` is still the only entry gate,
`OrderManager` still the only execution path, and `orchestrator.run_cycle()` is
still the only composition of a cycle.

**New packages:**
- `optionspilot/services/` — `PortfolioService`, `WatchlistService`,
  `IntelligenceService`, `NotificationService`, `WorkspaceService`, `sync.py`
  (the persisted-object inventory), `viewmodels.py`, `ServiceRegistry`.
- `optionspilot/host/` — `capabilities.py` (declarative `HostProfile` per target,
  including the three that do not exist yet) and `adapter.py` (`HostAdapter`,
  `DesktopHost`, `HeadlessHost`).

**New API:** `GET/POST/DELETE /api/workspace`, `GET /api/host`,
`GET /api/diagnostics/sync`.

> ### ⚠ Four things here are load-bearing — read before changing any of them
>
> 1. **`services/` may not import `ui/`, and may not import a web or GUI
>    framework at all.** The second rule is the stronger one: `services/` could
>    stay free of `optionspilot.ui` and still `import fastapi` for a response
>    model, at which point a Flutter backend, a CLI or a test pulls a web server
>    in to compute a win rate. Both are asserted, and both were verified to fail
>    when deliberately broken.
> 2. **`host/` is core-only, and a capability question is not a platform
>    question.** `if not host.supports(Capability.TOAST)` survives a port; `if
>    sys.platform == "win32"` is a bug on every platform that is not Windows and
>    a silent one on most. A guard forbids the second outside `core/paths.py`,
>    `host/` and `update/installer.py`.
> 3. **The workspace service holds no second catalogue.** `tab` and indicator
>    names are frontend vocabulary and are checked for type and length only —
>    a Python copy would be the two-catalogue drift `ui/guide.py` exists to
>    avoid. `timeframe` IS validated against `core.models.Timeframe`, because
>    that value is handed straight back to `/api/candles` and an unparseable one
>    502s.
> 4. **`localStorage` is still the synchronous source; the server is the durable
>    one.** `CH.sym`/`CH.tf`/`wlSort`/`tkChartOpen` are read at script-eval time,
>    before any fetch resolves. Making the server synchronous would mean
>    restructuring chart initialisation around an `await` in a 7,900-line file
>    with no per-flow coverage. Writes mirror up; only a profile with NO
>    workspace keys adopts the server's copy.

> ### ⚠ One shipped defect found, and three introduced-then-caught
>
> **Shipped, for three milestones:** `/api/learning` built its `WeightStore`
> from `Path("data") / "learning" / "weights.json"` — relative to the process
> CWD, one of the hardcodes V0.4.4's storage split was meant to remove. The
> engine reads the per-user root, so the Learning tab was reading a *different
> file*: on a real install one that does not exist, and in a dev checkout
> whichever `./data/learning/weights.json` sat next to the process. The
> `effective` column came from the live scorer and really was right, which is
> what made it look plausible. Regression test verified to fail against the old
> code; `test_no_cwd_relative_storage_paths` now forbids the class.
>
> **Introduced by this milestone and caught before it landed:** a bound method
> captured at construction (so a reassigned `_live_symbol_check` was silently
> ignored — an existing test found it); a default tab id of `dash` where the
> frontend uses `dashboard`; and a declared `SyncDomain.WORKSPACE` with no
> entries, so the inventory report omitted the one domain the milestone built.

**Honest limitations** (§7 of `ARCHITECTURE-PLATFORM.md` has all nine): chart
drawings are still `localStorage`-trapped and are the last blocked domain; there
is no API versioning, no error envelope, no idempotency keys and no
authentication; `/ws` still pushes a raw unenveloped payload; notifications have
no durable store; the tab is restored only on adoption, never on every launch
(deliberate — resuming the last tab every launch would change desktop
behaviour); `tkChartOpen` takes effect on the next launch rather than live; and
`sidebar_collapsed` exists in the model with nothing writing it.

**Verified at the time:** the full suite, 21/21 `workspace_check`, 135/135 `guide_check`, 54/54
`intelligence_check`, 46/46 `marketdata_check`, `chart_check` green, 88/88
market-data stress, `browser_check` + `check_html_ids` + `check_docs` green.

## What to do next

Three candidates, in the order they would add most:

1. **Review V0.6.0, V0.6.1 and V0.7.0 and decide on the commits.** None of the
   three is committed. `docs/TRADING_INTELLIGENCE.md`, `docs/ONBOARDING.md` and
   `docs/ARCHITECTURE-PLATFORM.md` are the review documents.
2. **Contract hardening** — `ARCHITECTURE-MOBILE.md` §18 items 1-3 and 6:
   `/api/v1` aliases, a normalized error envelope, idempotency keys on mutating
   endpoints, and the WebSocket envelope. All cheap now and all expensive once
   any client exists that cannot update in lockstep.
3. **Sign the installer** (the plan already sits in `update/validation.py`) —
   removes SmartScreen warnings and closes the updater's last security gap.

## What was completed before that? (V0.6.1 — intelligent UX & onboarding)

1849 -> a **1908-test suite** (+54); a new **135-check** headless-browser suite
(`scripts/guide_check.py`, wired into `verify.ps1`). **Not committed.**
Full design, decisions and limitations: **`docs/ONBOARDING.md`** — read it before
touching `optionspilot/ui/guide.py` or the guided-onboarding block in
`index.html`.

**The one thing to understand:** by V0.6.0 the backend had become substantially
more sophisticated than the experience of using it. Nothing was missing;
everything was unexplained. The app assumed a user who already knew what delta
was, why a stop cannot be a buy order, and what "process score" meant — and for
everyone else the honest answer to *how do I learn this?* was **read the docs or
watch a video**, which is a design failure rather than a documentation gap. The
rule the milestone was built on: when a user becomes confused, the question is
not where to document it, but **why the software was able to let them.**

**No trading-behaviour change, no new dependency, no new tab, and no validation
weakened.** `OrderManager.place` refuses exactly what it refused before; the
ticket now stops you building the order it would refuse.

**New module:** `optionspilot/ui/guide.py` — pure and deterministic (no I/O, no
clock, no network): state validation, merge semantics, and the rules that turn
measured feature usage into a suggested walkthrough. Two endpoints,
`GET /api/guide` and `POST /api/guide/state`. Progress persists through
`RuntimeSettings` into `settings.json` under a `guide` key — **not
localStorage** — so a reinstall, a restored backup or a cleared WebView2 profile
does not greet a returning user as a beginner.

**New frontend subsystem** in `index.html`: a data-driven tutorial engine (11
walkthroughs, 52 steps), a 37-term plain-English glossary with adaptive hover
tips, a searchable help centre on `?` / `Ctrl+K`, per-screen **Learn** buttons,
teaching empty states, an app-wide reduced-motion switch, and the order-ticket
guardrails.

> ### ⚠ Five things here are load-bearing — read before changing any of them
>
> 1. **Ids are the contract; prose is not.** A `Recommendation` names a tutorial
>    by **id only**, and the human title comes from `GUIDE_TUTORIALS` at render
>    time. Two catalogues holding the same titles would be a second place
>    tracking one fact — the failure paid for twice already (`data/health.py` in
>    V0.5.3, the settings ranking in V0.5.7).
>    `tests/test_guide.py::TestCatalogueContract` asserts the two id sets match
>    **in both directions**, because a backend id the frontend lacks renders as
>    nothing and a feature key the frontend never records makes its rule
>    unfireable — and both LOOK implemented.
> 2. **This layer recommends TUTORIALS from FEATURE usage. It never recommends
>    trading behaviour.** That is `intelligence/`'s job, done from the trade
>    record with a false-discovery correction underneath it. *"You have never
>    placed a limit order"* is a fact about the software; *"you should place more
>    limit orders"* is a claim about the trader.
>    `test_no_rule_gives_trading_advice` sweeps every rule and asserts it.
> 3. **The page stays interactive during a tour.** `#gd-ring` is
>    `pointer-events:none` and one enormous spread `box-shadow` does both the
>    dimming and the cutout. Every alternative — a modal, a pointer trap, a
>    cloned "safe" control — turns a walkthrough into a slideshow, and what makes
>    one stick is that the button the user pressed was the real one.
> 4. **`data-learn` and `data-tip` are different on purpose.** `data-learn` is
>    hover *and* click (inert text: labels, table headings). `data-tip` is hover
>    only, for controls that already do something — without the split, clicking
>    the EMA pill would open a glossary card instead of switching on EMA.
> 5. **The UI guardrail is a second gate, never a replacement.** The `CLAUDE.md`
>    lesson *"adding a gate function is not the same as the gate being active"*
>    applies in reverse: adding a UI guardrail must never become a reason to
>    relax `OrderManager.place`.

> ### ⚠ Two defects found by the new browser suite — each is a lesson
>
> 1. **A hidden panel kept live buttons.** `renderRecs` set the recommendations
>    panel to `display:none` when there was nothing to suggest but left the
>    previous markup inside it — clickable controls for advice that had been
>    withdrawn, invisible to a user and very much not to a test. **Hide the
>    container *and* clear the body.**
> 2. **The tour's first step threw the page to the bottom.** Step 1 highlighted
>    the PAPER TRADING badge, pinned to the foot of a full-height sidebar, and
>    `scrollIntoView({block:"center"})` obeyed. Fixed twice over: the step targets
>    the sidebar itself, and the engine now scrolls only when a target is **not
>    visible at all** rather than merely off-centre. Caught by *screenshot
>    review*, not by an assertion — the standing lesson about asserting what the
>    user sees.

**The canonical browser assertion** in `guide_check.py` is not "a step declared a
target" but: **the highlight rectangle must intersect the element it claims to
highlight, and the explanation card must not sit on top of it.** Both were
verified to fail by deliberately breaking the code that satisfies them.

**Every other browser suite now seeds `guide.onboarded = true`** into its scratch
profile, because a scratch profile has by definition never been onboarded and the
welcome dialog would otherwise sit over every assertion. `browser_check.py` is
the deliberate exception — it *clicks* the dialog away, so the genuine
first-launch path is covered rather than avoided.

**Verified at the time:** the then-1908-test suite, 135/135 `guide_check`, 54/54 `intelligence_check`,
46/46 `marketdata_check`, `chart_check` green, `browser_check` +
`check_html_ids` + `check_docs` green, plus screenshot review of the welcome
screen, both tour styles, the help centre, the glossary, the guardrail and three
empty states.

**Honest limitations** (§10 of `ONBOARDING.md` has the full list): nothing knows
whether a tutorial was *understood*, only that it was finished; feature marks are
buffered and a hard kill can lose the last few; glossary search has no stemming
or synonyms ("IV" finds implied volatility, "vol" does not); a tour cannot
recover from the user navigating away mid-step (Esc and restart is the recovery);
and `when` predicates are evaluated at tour start, so a panel that appears
*during* a tour gains its step only on a restart.

## What was completed before that? (V0.6.0 — the Trading Intelligence Engine)

1468 → **1849** test cases (+381); a new **54-check** headless-browser suite
(`scripts/intelligence_check.py`, wired into `verify.ps1`) and a performance
benchmark (`scripts/intelligence_benchmark.py`). **Not committed.**
Full design, rules and limitations: **`docs/TRADING_INTELLIGENCE.md`** — read it
before touching `optionspilot/intelligence/`.

**The one thing to understand:** the app already knew a great deal about its
trader, and knew it in four unrelated places — `journal.db` (the round trips),
`experience.db` (the rich per-trade context), `data/coach/*.json` (the process
reviews) and `learning/weights.json` (what has paid off). Four stores, four
aggregation paths, and no answer at all to the questions a trader actually asks:
*what am I good at, what keeps costing me money, am I improving, what should I
learn next.* Worse, every new screen that wanted an answer computed its own —
the "two objects tracking one fact will drift" failure this codebase has already
paid for twice (`data/health.py` in V0.5.3, the settings ranking in V0.5.7).

V0.6.0 collapses that into **one pipeline**: `build_facts()` joins the three
sources into a `TradeFact` once, ten engines run over it, and everything above —
Dashboard, Coach, Journal, Learning, reports — projects from a single
`IntelligenceSnapshot`. **No trading-behaviour change, no new dependency, no new
tab.** The engine is never consulted before a trade; `risk/manager.py` is still
the only gate.

**New subpackage:** `optionspilot/intelligence/` (17 modules) — `facts.py` (the
one join), `stats.py` (every formula), `performance.py` (the 38-metric
registry), `behavior.py` (22 detectors), `patterns.py` (automatic edge discovery
over 19 dimensions), `risk.py`, `confidence.py` (8 composite scores),
`goals.py`, `curriculum.py` (16 triggered lessons), `recommend.py`,
`timeline.py`, `achievements.py`, `reports.py`, `engine.py` (the façade + cache),
`store.py`, `windows.py`, `models.py`.

**Layering, and it is load-bearing:** `intelligence/` imports **`core` only**.
It reads journal/experience/coach records *structurally* rather than by import,
which keeps it **below** the coach — that is what lets the AI Coach become a
presentation layer over it instead of a parallel analysis path. If it ever
imports `coach/`, the dependency inverts and that becomes impossible.
`tests/test_architecture.py` enforces it.

> ### ⚠ Four defects found by attacking it — each is a lesson, not a typo
>
> 1. **A composite score of 100/100 grade A, earned by an absence of data.** A
>    trader with no reviews scored Discipline A, because the one component
>    needing no review (revenge trading, which reads only timestamps) came back
>    clean and 20% coverage was enough to average. Fixed with
>    `confidence.MIN_COVERAGE = 0.35`: below it the score is `None`, not a
>    flattering number under a caveat nobody reads.
> 2. **Thirteen "patterns" out of 100 uniformly random trades.** ~70 bucket
>    tests per run at a raw p≤0.20 threshold produces ~14 false positives by
>    construction; the benchmark measured 13. Fixed with a **Benjamini–Hochberg**
>    false-discovery correction over the whole run (`FDR_Q = 0.10`). The same
>    input now yields ≤3 and a real edge still comes through.
> 3. **A circular dimension.** Exit reason was a pattern dimension, and produced
>    the strongest finding in the system: *"how it ended — stop loss: 0% win rate
>    over 51 trades against 100% elsewhere, p<0.0001"*. True, and a definition —
>    a trade that ended at its stop is a losing trade. It also generated the
>    recommendation *"stop taking stop-loss trades"*. **A dimension must describe
>    a choice made before or during the trade, never a consequence of how it
>    turned out.**
> 4. **`nan%` in the narrative.** Profit factor is legitimately infinite for a
>    period with no losers, and `inf` vs `inf` yields a NaN percentage — both the
>    timeline and the report writer shipped *"your profit factor has declined
>    nan% since March"*. Fixed with `stats.comparable()`, which every narrative
>    comparison now gates on.

**The design rule underneath all four:** insufficient evidence is a first-class
answer. A metric is `None`, not `0`. A score is `None`, not `50`. A behaviour is
`assessable=False` **with the reason stated**, not `detected=False` — because
"not detected" is a claim and it would be unearned. `hesitation` is permanently
unassessable and says so, because measuring it needs signal-to-entry latency
(not recorded) and the setups skipped entirely (which produce no trade).

**Every conclusion carries its evidence**, including up to 25 of the exact trade
IDs behind it, which is what the UI's "Why?" disclosure shows. A finding with no
`trade_ids` is a bug.

**Performance** (measured, `scripts/intelligence_benchmark.py`): 50,000 trades
analysed in 2.9 s; per-trade cost **flat** from 1k to 50k (70 µs → 58 µs), so the
pipeline is sub-quadratic. Nothing is computed at construction, so startup is
unchanged. The cache is keyed on a fingerprint the orchestrator owns
(`journal.revision:experience.revision`) — four cached reads cost 0.001% of one
analysis. A failed analysis returns an empty snapshot and is **never cached as
though it were an answer**.

**New API:** `/api/intelligence`, `/api/intelligence/summary`,
`/api/intelligence/trade/{id}`, `/api/intelligence/reports`, and goal
CRUD at `/api/intelligence/goals`. `/api/coach` gained an `intelligence` block;
`/api/journal` gained a `findings` map so rows can be badged without a request
each.

**New UI**, inside the existing tabs: Dashboard gets the score cards, ranked
action list, risk observations, goals, achievements and improvement timeline;
Coach gets measured behaviour (detected / clean / **unassessable-with-reason**),
discovered patterns and the coaching reports; Journal gets finding badges and a
lazily-loaded per-trade analysis; Learning gets triggered lessons that each say
why they appeared and which statistic fired them.

**Verified:** the full suite at the time (1849), 54/54 `intelligence_check`,
46/46 `marketdata_check`,
65/65 `chart_check`, 88/88 stress, `browser_check` + `check_html_ids` +
`check_docs` green, plus screenshot review of all four integrated tabs.

**Honest limitations** (§12 of `TRADING_INTELLIGENCE.md` has the full list):
hesitation and missed setups are unmeasurable; MFE/MAE need intrabar data the
system does not have; R multiples require a recorded protective stop, so the most
useful risk metric is missing exactly for the traders who most need it; patterns
are correlational, not causal; and all of it analyses **paper** trades, so
slippage and the psychology of real money are not in the data.

## What was completed before that? (V0.5.7 — the Market Data Control Centre)

1257 → a **1468-test suite** (+211); a new **46-check** headless-browser suite
(`scripts/marketdata_check.py`, wired into `verify.ps1`). **Not committed.**
Full design: `docs/MARKET_DATA.md` **§29–42**.

> ### ⚠ Read this before touching the keyed providers
>
> **Finnhub's free tier no longer serves historical candles.** Live
> certification found it returning HTTP 403 to a brand-new, verified, correctly
> pasted key — and the app reported *"the API key was rejected"*, so the user
> regenerated a key that was never wrong, repeatedly. Measured: Finnhub answers
> an **invalid** key with **401** and a **valid but unentitled** one with
> **403**, so **401 is the only status it uses for a key problem and a 403 is
> positive evidence the key is good**. `http_adapter._from_status` had mapped
> `code in (401, 403)` to one `ProviderAuthError`.
>
> Fixed with `ProviderEntitlementError` (deliberately **not** a subclass of
> `ProviderAuthError`), `STATUS_PREMIUM_REQUIRED`, and
> `FinnhubAdapter.verify_credentials()` proving the key on the free `/quote`
> endpoint. **Authentication is not weakened** — 401 still benches the provider
> stickily, verified live. Two knock-on fixes: `deepest_earliest` now excludes
> `monitor.permanently_unusable` (a rejected key or an insufficient plan was
> still contributing a 180-day 5-minute history floor it could never serve), and
> `adapter.free_tier_serves_history` stops the app recommending Finnhub as the
> free provider to add. Full account: **`docs/MARKET_DATA.md` §41**.

**The one thing to understand:** V0.5.2–V0.5.6 built a market-data subsystem
that is genuinely production-grade and gave its owner no way to see or steer
any of it. Every real user question — *why isn't Finnhub being used, is my key
working, how many requests are left, what happens when Yahoo dies, my cache
looks wrong* — was answerable only by reading `logs/data.log` or by editing
`config.yaml` and restarting. This milestone is that entire management layer.
**No trading-behaviour change, no new dependency, and identical shipped
defaults**: with no key, no stored state and no config edit, the chain behaves
exactly as it did in V0.5.6.

**New modules:** `data/control.py` (`MarketDataControl` — the administration
surface, composed *over* the registry, which has never heard of it),
`data/credentials.py` (owner-only key storage, `environment → stored →
config.yaml`, masked everywhere but `resolve()`), `data/faults.py` (QA-mode
fault injection firing inside `fetch_history`, off in every shipped build).
**New UI:** Settings ▸ Market data — a card per provider, a 21-column live
dashboard, failover summary, recommendations, eight maintenance tools with
progress and a Stop button, a plain-English explainer, and a gated QA panel.

**Four behaviour changes worth knowing about before touching `data/`:**

1. **`enabled: false` no longer skips construction.** A disabled provider is
   built, listed, self-explaining and never selected — otherwise the settings
   page has a blind spot exactly where a user needs to act. It contributes no
   `deepest_earliest` floor, so the V0.5.2 retry-forever class stays fixed.
2. **`ordering_mode` supersedes `dynamic_ranking`** with three modes; hybrid is
   the full rank formula **minus its latency term**. `dynamic_ranking: false`
   still wins and pins to `static`.
3. **`monitor.health_state()`** is the human-facing state, derived on every
   read and stored nowhere. `status()` is still the gate.
4. **Provider priorities are rewritten 10, 20, 30…** on reorder, because 10
   rank points equals one second of latency — consecutive numbers would make
   dynamic ordering almost-static.

**Five defects found by attacking it**, each with a regression test — including
a hand-edited `marketdata.json` with `providers` as a list raising an
`AttributeError` **out of the composition root** (the app refusing to start
because a preferences file was edited badly), and a multi-minute capability
probe that could not be stopped.

**Verified:** the full suite at the time (1849), 46/46 `marketdata_check`,
65/65 `chart_check`, 88/88
stress, `browser_check` + `check_html_ids` + `check_docs` green, plus a
40-assertion adversarial audit.

**Still the biggest limitation, and now worse:** with no API key there is
exactly one real source (Stooq is dead; Yahoo and yfinance share an upstream).
Finnhub *was* the recommended free route to an independent one and can no longer
serve history on a free plan — so **Twelve Data (800/day) is now the only free
keyed provider that delivers a genuinely independent intraday source**, with
Alpha Vantage's 25/day a distant second. The panel makes all of this visible and
makes adding a key a thirty-second job, but visibility is not redundancy.

**Live certification status:** Twelve Data and Alpha Vantage **authenticate and
serve** with real keys; Yahoo and yfinance work normally; Finnhub is premium-
gated as above. The 84-item manual QA (`docs/QA_MARKET_DATA.md`) has still never
been run by hand.

## What was completed before that? (V0.5.6 — two reported bugs)

A **1257-test suite** at the time (+19); `chart_check` 48 → **65**; a new
**110-cell** browser
matrix (10 symbols × 11 timeframes). **Not committed.** Full report:
`docs/CHART_CERTIFICATION.md` **Part II** — read it before touching `data/` or
the chart controller.

**Bug 1 — every symbol on 1D showed "the cached bars failed validation and were
discarded", and Retry never helped.** Two defects stacked, and the second is the
one to remember: **validation ran AFTER the tier ladder had committed**
(`_settle`), so an unusable cache became `outcome=failed` with the providers
already behind it and the bad rows still on disk. The data was wrong because
Yahoo stamps a daily bar at the 09:30 ET session open (13:30 UTC) while yfinance
stamps exchange midnight (04:00 UTC) — and the cache is keyed
`(symbol, timeframe, ts)`, so every trading day held two rows 9.5 hours apart
(SPY: 6,517 rows for ~3,258 days), making the frame's spacing 0.40 intervals.
Fixed at three points: `base.session_index` (one convention, applied in
`HistoryAdapter.fetch_history`), `cache._migration_3` (repairs existing
installs), and disk tiers that validate before committing and `_quarantine` on
failure so the ladder falls through to the providers.

**Bug 2 — viewport/zoom corruption.** Nothing defined a *legal* viewport, so a
symbol switch under Auto Follow inherited the previous instrument's zoom and a
resize left 4 bars of 281 on screen. Six invariants (V1–V6) now enforced in
`chClampViewport` via `chMoveViewport`, with `CH_MIN_VISIBLE_BARS` as the single
floor constant. `CH.restoringViewport` is now a **depth counter** — overlapping
guarded moves were clearing each other's protection.

**Do not re-add the ResizeObserver viewport clamp.** It was implemented and
reverted: a price-axis drag changes its own label widths, so the canvas resizes
mid-gesture and the clamp snapped the user's manual price scale back.

**Not implemented, and it should not be assumed done:** the Settings ▸ Market
Data Providers panel for pasting API keys (backend key handling exists and is
unchanged — env-first, per-provider disable, default redaction; a key is
configured today via an environment variable or `config.yaml`), the extra
provider-health dashboard columns, cross-provider disagreement enforcement, and
permanent history-scroll stress coverage. All four are in `docs/TODO.md`.

## What was completed before that? (V0.5.5 — chart certification)

1232 → a **1257-test suite** (+25); `chart_check` 42 → **65 checks**. **Not committed —
awaiting user review.** Full report, matrices and residual risk:
**`docs/CHART_CERTIFICATION.md`** — read it before doing anything with charts.

A failure-elimination pass over the whole chart pipeline, provider to pixel.
**No new features, no version bump, no trading-behavior change.** Ten defects,
each found by *reproducing* it, each a way the chart could fail while the
backend, the diagnostics dashboard and the entire test suite reported success.

**The one thing to understand:** every check this repo had asked whether the
DATA arrived; none asked whether the user could SEE it. That gap is the whole
milestone, and the headline defect lives in it. **lightweight-charts turns
`autoScale` off permanently the first time the user drags the right-hand price
axis, and nothing in this app ever turned it back on** — not a symbol switch,
not a timeframe switch, not Reset view. The pinned band outlived every later
load, so a $290 ETF drawn on a band left over from a $750 one had its candles
entirely off-screen while the volume histogram (its own `vol` price scale) kept
painting. That is precisely the "QQQ loads, SPY partially, IWM shows only
volume, diagnostics healthy" report; the four screenshots show one identical
480–660 band at four different price levels; and a restart "fixed" it because
`autoScale` is not persisted. The price axis now has an owner, mirroring the
time axis (`chMoveViewport`) since V3.2.2 — see `CHART_CERTIFICATION.md` §2.1
for the exact ownership rules, including why a *same-key* refresh must preserve
a manual scale.

**The other eight:** Yahoo's 30-minute closing stub bar (15:30 → 16:00 ET)
condemning every 1h frame as "wrong interval served" — 1 bad gap in 2,180 in
the user's real cache, and fatal on yfinance too since it shares the upstream;
a `NaT` timestamp 500'ing `/api/candles`; **null-OHLC bars being whitespace,
not an error, to lightweight-charts** (invisible candles under
`state="complete"`); an out-of-order payload collapsing to ONE candle; a
malformed indicator array wiping the whole chart; a string indicator value
raising an uncaught error from the crosshair handler; `high < low` bars drawing
as nothing; a render failure being overwritten with `complete`; and — found
while re-running the suite — **`chart_check.py` and `browser_check.py` writing
to the user's REAL data root**, which `cwd=scratch` stopped isolating in V0.4.4.

**Verified:** the 1257-test suite of the day, 65/65 `chart_check` in a real
browser, 88/88 stress,
`browser_check` + `check_html_ids` + `check_docs` green, and **41 adversarial
scenarios** driven through the real renderer in two waves (the second written
to attack the first's fixes). Each of the ten was demonstrated failing before
and passing after.

**Two limitations found that this pass could NOT fix — read these:**

1. **Stooq is dead.** It serves a JavaScript proof-of-work challenge to every
   request now (verified live on `stooq.com` and `stooq.pl`). The adapter
   refuses it correctly, but **with no API keys the app has exactly one real
   source: Yahoo**, via two code paths sharing one upstream and one failure
   domain. And Yahoo rate-limits by IP (a 429 was observed during this pass).
   A free Finnhub or Twelve Data key is the only route to real independence.
2. **Cross-provider agreement is measured but not enforced** — nothing flags a
   dividend-adjusted vs unadjusted disagreement during normal operation, so the
   cache can still stitch two incompatible series. Wants its own design.

Both are now tracked in `docs/TODO.md` under High Priority.

## What was completed before that? (V0.5.4 — three keyed providers)

1052 → a **1232-test suite** (+180); stress 65 → **88 scenarios**. **Not committed —
awaiting user review.** Full design: `docs/MARKET_DATA.md` §23–27.

**The headline:** with **no API keys configured the app behaves exactly as it
did in V0.5.3.** That is the shipped default and the state most installs are
in. Keyed providers are constructed, report `missing_api_key` with a signup
link in diagnostics, and are never selected.

**What was built:**

1. **Finnhub (40), Twelve Data (50), Alpha Vantage (60)** —
   `data/{finnhub,twelvedata,alphavantage}_provider.py`, behind the keyless
   chain. Each ~150 lines implementing four methods; everything else was
   inherited. They are genuinely independent of Yahoo, which makes a Yahoo-wide
   outage survivable at **intraday** resolution for the first time.
2. **`data/http_adapter.py`** — shared transport/status-mapping/JSON/timezone
   base for keyed providers. Yahoo and Stooq were deliberately not retrofitted
   (their transports do multi-host failover and HTML-challenge detection).
3. **`data/ratelimit.py`** — request budgeting. Alpha Vantage allows **25
   requests/day**; budgets are enforced before the request, use a real sliding
   minute window, and persist to `<data>/quota.json` so a restart cannot mint a
   fresh allowance. Budget *pressure* feeds the existing ranking, so load moves
   off a nearly-spent provider before it is spent — no scheduler needed.
4. **Credentials + redaction** — environment-first resolution
   (`FINNHUB_API_KEY` etc. need no config); keys **redacted by default** in
   `as_dict()`, because that payload reaches the diagnostics export.
5. **A status vocabulary** (`health.STATUS_*`) shared by monitor, API, text
   export and dashboard.

**Two pre-existing defects found and fixed:** `deepest_earliest` counted
providers that could never answer (would have revived the retry-forever bug
class); and the stale tier could report `stale` with zero bars.

**The trap to know about:** two of the three providers send **naive local time
in the exchange's timezone**. Reading it as UTC shifts intraday bars by 4–5
hours and by a *different* amount across DST — a subtly wrong chart that also
poisons the shared cache. `http_adapter.localize` is the one place this is
handled; daily+ bars stamp at 00:00 UTC to match Yahoo and Stooq.

**Still open:** the 84-item manual QA (`docs/QA_MARKET_DATA.md`) has still not
been run by hand. **No adapter has been exercised against its real API** — all
1232 of those tests ran against canned payloads, so the response shapes are as
documented, not as verified. One live run per provider with a real key is the
obvious next step (`scripts/marketdata_benchmark.py --live`).

## What was completed before that? (V0.5.3 — production readiness)

V0.5.2 built the market-data subsystem. **V0.5.3 made it operable.** The suite
went 880 → 1052 (+172) at the time; stress 41 → 65; `chart_check` 49 → 52.
**No new provider, no version bump, no trading-behavior change. Not committed —
awaiting user review.** Full design: `docs/MARKET_DATA.md` §13–22.

**Why:** V0.5.2 could serve data correctly but could not tell you *why* it had.
Health lived in two objects, ordering was a hard-coded constant, every knob
needed a source edit, and diagnosing a chart complaint meant reading logs.

**What was built:**

1. **`data/health.py` — one owner for provider health.** State used to live in
   `adapter.ProviderHealth` (counters) *and* `registry._Breaker` (rotation),
   with the breaker's trip condition being a read of the adapter's counter.
   `ProviderHealthMonitor` now owns counters, latency (EWMA + real p95),
   rate-limit window, breaker, per-day totals and the ranking score. The policy
   "a range error is not an outage" lives once, in `COUNTS_AGAINST_HEALTH`.
2. **Dynamic ranking** (`registry.candidates`) — priority as the anchor, moved
   by latency, recent failure rate, consecutive failures, breaker history and
   quality. **10 rank points = 1 second of latency**, so Yahoo at 180ms beats
   Stooq at 320ms but Yahoo at 2.4s loses to Stooq at 260ms. **Cold ranks equal
   priority**, so a fresh system reproduces V0.5.2's order exactly;
   `dynamic_ranking: false` pins it. The rank's failure rate is measured over a
   **moving 50-attempt window** — a lifetime rate never decays.
3. **Help ▸ Diagnostics** — a dashboard over `/api/diagnostics/marketdata`, plus
   `…/export?format=text|json` (dated attachment, safe to paste publicly) and
   `POST …/replay` (re-run a recorded request, poll every provider, compare).
4. **`market_data:` in `config.yaml`** (`data/config.py`) — enabled, priority,
   timeout, retries, backoff, throttle, breaker thresholds, quality floor,
   ranking on/off, memo cap, cache policy/retention. Unknown keys are a startup
   error; unknown *providers* are accepted (so a future provider's settings can
   be pinned before its adapter ships).
5. **Cache intelligence** (`CacheMetrics`), **structured logging** (one
   `key=value` line per request, with the full provider chain), **capability
   discovery** (`data/discovery.py`, advisory + off by default), and
   **`scripts/marketdata_benchmark.py`**.

**Two real bugs the consolidation exposed** (both present since V0.5.2, both
invisible without a single owner):

- A provider serving consistently-unusable bars was recorded by the adapter as a
  **success** and the service's validation reject was counted nowhere — so a
  source answering promptly with garbage kept the head of the chain forever.
- A demoted success could only ever reach a failure streak of **1**, because
  recording the success had already zeroed the streak.

**Still open:** the 84-item manual QA (`docs/QA_MARKET_DATA.md`) is still not
run by hand. Adding a real second non-Yahoo intraday provider (Tiingo / Twelve
Data, free key) is now genuinely one file + one registry entry — see
`docs/MARKET_DATA.md` §21 for the checklist.

## What was completed before that? (V0.5.2 — market-data subsystem)

Chart history was the last subsystem that behaved inconsistently. It was
**replaced**, not patched. 651 → 880 (+229) tests at the time. No version bump,
no trading-behavior change.
Full design + measurements + provider survey: `docs/MARKET_DATA.md`.
84 manual checks: `docs/QA_MARKET_DATA.md`.

**Root causes, proven from evidence (not guessed):**

1. **Depth measured from the wrong point.** Yahoo's intraday limit runs from
   *now* ("must be within the last 60 days", its own 422 body);
   `_clamp_history_window` measured from the *request's end*. A scroll-back
   whose start was 62 days old but whose end was 31 days old passed unclamped,
   422'd, came back empty, and was retried on every scroll forever. Verbatim in
   `logs/data.log` with the clamp reporting no change.
2. **A history-paging request poisoned the live-window memo.** Both share the
   key `(symbol, timeframe, session)`; a past-ending window overwrote the live
   frame, and the next live load rendered the sliced overlap. Found by
   `chart_check.py`: **QQQ 1d returned one candle from nine months earlier**,
   `outcome: memo`, no error anywhere.
3. Shipped depth caps were one day *past* Yahoo's real cliff (60 vs 59, 730 vs
   729) — a boundary request looked like an outage.
4. A **corrupt `cache.db` crashed the app at startup** (the connection raised
   before the recovery block, and leaked its Windows file handle so the file
   could not even be quarantined).
5. A history prepend restored a viewport captured **mid-drag**, yanking
   on-screen bars — invisible while the backend was slow, reproducible once it
   was fast.
6. pandas 3 changed `DatetimeIndex.astype("int64")` from nanoseconds to
   **microseconds**; any spacing math built on the old assumption is off 1000x.

**What was built** — all inside `optionspilot/data/`, so the layering and the
`MarketDataProvider` contract are unchanged:

- `capabilities.py` — per-provider, per-interval depth **measured from now**;
  an impossible request is answered from the table for zero network cost.
  `scripts/marketdata_probe.py` re-measures it live and flags drift.
- `adapter.py` — `HistoryAdapter`: one shape per source. **Adapters raise
  instead of returning empty frames**, with typed failures driving
  retry-vs-failover. Adding a provider is one file + one registry entry.
- Three adapters: **`YahooChartAdapter`** (priority 10, `v8/finance/chart` JSON
  over `urllib` — faster, no hidden global throttle, and *it reports why it
  refused*), **`YFinanceAdapter`** (20, same data by an independent code path),
  **`StooqAdapter`** (30, the only non-Yahoo source; daily+ only; refuses HTML
  anti-bot pages rather than parsing them as prices). Plus
  `LegacyProviderAdapter` for any plain `MarketDataProvider`.
- `registry.py` — ordering, pre-network eligibility checks, circuit breakers
  with half-open recovery.
- `service.py` — the tier ladder (memo → disk → providers → half-open probes →
  stale disk → explained failure) and the one place `exhausted` / `empty` /
  `stale` / `failed` are told apart.
- `quality.py` — semantic validation returning a report. Gaps carry no penalty;
  interval conformance is judged on the **tightest** spacing (a 4h chart's
  20-hour overnight gap is not a defect).
- `diagnostics.py` + `GET /api/diagnostics/marketdata` — one trace per request;
  every `/api/candles` response carries its `trace_id`.
- `cache.py` rebuilt as durable storage: atomic writes, integrity check on open,
  corruption quarantined + rebuilt, versioned migrations (a v1 `cache.db` keeps
  every row), provider attribution, validation on read.
- Frontend: an explicit state machine mirrored to `#ch-main`'s `data-ch-state`;
  reaching the start of history shows **"◄ Start of available history · 5m data
  starts May 28, 2026"** and stops requesting.

**Verification at the time:** the then-880-test suite (250 market-data, all offline);
`scripts/marketdata_stress.py` 41 offline scenarios (now in `verify.ps1`) + 6
live behind `--live`; `scripts/chart_check.py` at 49 checks and **green end to
end** — it had been dying at check 12 on `main`, which is how root cause #2 was
found. Measured: **24 concurrent live chart loads in 0.5s, zero blanks**
(10–15s before). V0.5.3 has since taken these to 1044 / 65 / 52.

## What was completed before that? (V0.5.0 — Auto-Updater 1.0)

OptionsPilot now **keeps itself up to date** like a modern desktop app. **No
trading behavior changed; user data is never touched by an update.** 546 →
a **651-test suite** (+105) at the time. Full design: `docs/AUTO_UPDATER.md`.

1. **New subpackage `optionspilot/update/`** (depends only on `core` + stdlib;
   networking is `urllib`, **no new runtime dependency**), layered and each layer
   injectable so the whole thing is tested offline: `version` (correct, non-lexical
   SemVer ordering), `transport` (the only networking — timeouts/retries/backoff/
   proxy), `github_api` (Releases → installer asset only), `checker` (channel +
   frequency; **never raises**), `downloader` (stream to `%TEMP%\OptionsPilotUpdater`,
   progress + cancel, atomic `.part`→final), `validation` (size/hash;
   Authenticode-ready), `installer` (mandatory `pre-update` backup → `/VERYSILENT`
   install → restart), `ui` (presentation), `service` (`UpdateService` facade +
   state machine).
2. **Wiring**: `/api/update/{status,check,download,progress,cancel,apply,skip,
   settings}` in `ui/server.py`; `UIServer` owns an `UpdateService(__version__,
   runtime)` and kicks a background launch-time check **gated on `run_loop`** (so
   tests never hit the network); `ui/desktop.py` registers an install hook to
   close the window cleanly. Prefs persist in `RuntimeSettings` under the
   `updates` key of `settings.json`.
3. **Frontend** (`ui/static/index.html`): Settings ▸ Software updates panel
   (auto-check, frequency, beta channel, current/latest, last-checked, Check now),
   Help ▸ Check for Updates…, and a professional update dialog (version diff,
   rendered release notes, size/ETA, progress bar, Update Now / Remind Me Later /
   Skip This Version). Manual browser QA still required (no automated UI driver).
4. **Tests**: +105 across `test_update_{version,github,checker,downloader,
   validation,installer,service,endpoints}.py` (+ runtime-prefs), all offline via
   `tests/update_helpers.py` fakes; `test_architecture.py` now allow-lists the
   `update` subpackage (core-only).

**Still open before a friction-free public release:** Authenticode **code
signing** (SmartScreen warns until then; also enables signature verification in
`update/validation.py`), a published **SHA-256 checksums** asset the validator can
enforce, replacing the placeholder `LICENSE`, and a **manual end-to-end update
QA** on real Windows (see `docs/AUTO_UPDATER.md` §7). **Do not commit** — awaiting
user review.

## What was completed before that? (V0.4.6 — Professional Windows Installer 1.0)

OptionsPilot became a **professionally installable Windows app**, and the
installer was wired into the release pipeline. **No application behavior changed.**
A **546-test suite** (+19). Full design: `docs/INSTALLER.md`.

1. **Installer** (`installer/OptionsPilot.iss`, Inno Setup — evolved from the
   V0.4.5 template, same stable `AppId`): installs to `C:\Program
   Files\OptionsPilot` (admin, changeable dir); Start Menu folder (app +
   Uninstall) + optional desktop shortcut (default checked); app icon everywhere;
   Programs-and-Features registration; **in-place upgrades** (stable AppId,
   `UsePreviousAppDir`, `CloseApplications`).
2. **Uninstall** asks *"also remove my personal data?"* at uninstall time,
   **default No** (`MB_DEFBUTTON2`); only an explicit Yes deletes
   `%LOCALAPPDATA%\OptionsPilot`. Because user data lives in that separate root,
   upgrades/reinstalls never touch journal/coach/settings/trades/watchlists/backups.
3. **Pipeline**: `scripts/build_installer.ps1` (locates ISCC, reuses
   `dist\OptionsPilot`, stamps the single-source version) →
   `OptionsPilot-Setup-vX.Y.Z.exe`. `release.yml` installs Inno Setup, builds it,
   and uploads it **alongside** the retained zip.
4. **Tests**: +19 (`tests/test_installer.py`) static guards on the `.iss` +
   pipeline wiring. ISCC compile + install/upgrade/uninstall are manual/CI.

**Still open before a friction-free public release:** Authenticode **code
signing** (SmartScreen warns until then; `SignTool` hook stubbed in the `.iss`),
and replacing the placeholder `LICENSE` with a real choice.

## What was completed before that? (V0.4.5 — Professional Release Pipeline 1.0)

A release-automation milestone: a version tag now becomes a downloadable GitHub
Release with **zero manual steps**. **No application behavior changed** — only
how releases are built, tested, packaged, and published. A **527-test suite**
(+7). Full design: `docs/RELEASE.md`.

1. **GitHub Actions** (`.github/workflows/`): `ci.yml` (push/PR + reusable via
   `workflow_call`) installs (pip-cached), runs `pytest` + `selftest` +
   `check_html_ids` + `check_docs`, fails fast; `release.yml` (on `v*` tags)
   reuses CI, verifies the tag matches `__version__`, builds the exe, packages
   the zip, and creates the GitHub Release with CHANGELOG notes.
2. **Single-source version**: `optionspilot/__init__.py::__version__` is the only
   copy; `pyproject.toml` derives it (`dynamic`/`attr`). `bump_version.py` edits
   one file; `check_docs.py` + the release tag-guard enforce it. +7 tests
   (`test_release_tooling.py`).
3. **Packaging**: `scripts/package_release.ps1` → clean `OptionsPilot-vX.Y.Z.zip`
   (app + LICENSE/README/CHANGELOG; excludes user data/source); `release_notes.py`
   extracts the CHANGELOG section. Verified locally (54 MB zip, correct contents).
4. **Groundwork**: placeholder `LICENSE` (flagged — replace before public
   release) and an **unwired** Inno Setup template (`installer/OptionsPilot.iss`)
   with documented paths/shortcuts/AppData/uninstall.

**To actually cut a release:** `python scripts/bump_version.py X.Y.Z`, finalize
the CHANGELOG entry, commit, `git tag vX.Y.Z && git push origin main --tags` —
Actions does the rest. **Before the first public release:** replace the
placeholder `LICENSE` with a real license choice.

## What was completed before that? (V0.4.4 — persistent storage & migration)

A core-infrastructure milestone: **user data is now fully separated from the
binaries**, so a future version can replace the executable without losing paper
history, coach reviews, journal, settings, watchlists, weights, or logs. No
user-visible behavior changed; existing installs migrate automatically, once,
losslessly. A **520-test suite** (+28). Full design: `docs/STORAGE.md`.

1. **`core/paths.py::AppPaths`** — the single source of truth for every
   filesystem path. Storage root moved from the CWD (beside the exe) to a stable
   per-user location: `%LOCALAPPDATA%\OptionsPilot` (XDG / `Application Support`
   elsewhere), overridable via `OPTIONSPILOT_HOME`. Typed helpers
   (`get_data_dir`, `get_journal_db`, `get_coach_dir`, `get_settings_file`, …)
   + `ensure()`. No module constructs the root itself.
2. **`core/migration.py::initialize_storage`** — runs once at startup: creates
   the layout (`data/ logs/ backups/ exports/ migrations/`) and, on first run,
   imports a legacy CWD/exe-relative `data/`+`logs/` install — a **lossless
   copy** (timestamps preserved, each file verified, never overwrites newer,
   never deletes source), recorded in `migrations/migration_version.json`.
   Idempotent + self-healing (partial copies complete; a corrupt marker can't
   lose data). Plus `create_backup()` and an **empty** versioned-migration
   framework (`MIGRATIONS`) for future schema changes.
3. **Wiring**: `__main__._bootstrap` builds `AppPaths` + migrates + points
   logging at `paths.root`; `Orchestrator`/`UIServer`/`create_app`/`serve`/
   `desktop.launch` default to the per-user root; the last CWD-relative
   `Path("data")` hardcodes are gone. `data_dir=` APIs unchanged → nothing broke.
4. **selftest** now verifies dirs exist + writable + marker valid;
   `tests/conftest.py` isolates `OPTIONSPILOT_HOME` so tests never touch real
   AppData. +28 in `test_paths.py` / `test_migration.py`.

**Recommendations before the automatic updater** (see the final report / below):
the storage split is the prerequisite; the updater should only ever replace the
install directory, never write into the storage root, and can lean on
`create_backup()` before applying anything.

## What was completed before that? (V0.4.3 — AI Coach 2.0, phase 1)

Turned the Coach from an information page into a **mentor**, *additively* and
manual-trades-only. No trading-path change, no change to when/whether a review
runs, and fully backward-compatible with pre-2.0 persisted reviews. A
**492-test suite** (+22). `check_html_ids` + headless `browser_check` green.

1. **Per-trade category scorecard** (`coach/categories.py`): every manual
   `CoachReview` now carries 10 categories (Entry/Exit/Risk/Size/Emotional
   Discipline/Rule Following/Patience/Timing/Trend/Reward-Risk), each with a
   0–100 score, a **data-referenced** explanation, and one suggestion — derived
   purely from the before/during `Finding`s and mistake tags the review already
   computes. `context_only` categories report `None` ("not enough data") when no
   near-entry snapshot was captured, rather than a misleading perfect score.
   `CoachReview` also gained a small outcome snapshot (pnl / return_pct /
   hold_minutes / best-effort `r_multiple` / entry_ts / symbol / direction).
2. **Mentor dashboard** (`coach/analytics.py`, pure `build_dashboard(reviews)`):
   headline sub-scores (consistency / risk / execution / discipline), the
   category scorecard averaged with **month-over-month trend**, win/loss streaks,
   **pattern detection with confidence** (a one-off is "low / may be developing";
   frequent + consistent is "high / recurring habit"), an improvement timeline
   ("Risk Management improved 14 points this month"), and **≤5 ranked action
   items** computed over a recent window so they auto-expire as habits improve.
3. **API + UI**: `GET /api/coach` now also returns `dashboard` (cached by review
   count, recomputed only when a new review lands). The coach tab renders the
   sub-score cards, category scorecard (score bars + grades + trend), action
   plan, improvement timeline, and confidence-tagged recurring patterns —
   reusing existing card/panel styles, no new CSS.

Files: **new** `coach/categories.py`, `coach/analytics.py`,
`tests/test_coach_categories.py`, `tests/test_coach_analytics.py`; **modified**
`coach/coach.py`, `coach/__init__.py`, `ui/server.py`, `ui/static/index.html`,
`tests/test_ui_server.py`.

**Optional Coach 2.1 ideas (NOT implemented):** persist the dashboard to disk for
history beyond current reviews; extend coaching to AI-mode trades; overtrading
detection from entry-time density; per-category weighting of the headline score;
a shareable monthly report.

## What was completed before that? (V0.4.2 — architecture audit + three refactors)

A read-only architecture audit (full report: `docs/ARCHITECTURE-AUDIT-V0.4.2.md`)
concluded the codebase is in good health — clean *verified* layering, no SQL
outside the persistence modules, thin route handlers, zero real debt markers — so
only **three** low-risk, behavior-preserving improvements were implemented, each
a separate change with its own regression tests. **No user-visible behavior
changed.** Version 0.4.1 → 0.4.2, a **470-test suite** (+16).

1. **Shared SQLite foundation** (`core/sqlite.py`): `connect()` + versioned
   `run_migrations()` (`PRAGMA user_version`), adopted by **all five** stores in
   order `cache → journal → orders → paper → experience`. Migration 1 of each
   store is its *exact current schema*, so existing `data/*.db` files open
   unchanged. `paper.db`'s `managed_by` ALTER became an idempotent migration 2.
   This gives the journal/paper/orders databases (and future Replay/Analytics/
   Live-Broker DBs) the safe schema-evolution the experience store already had.
   +13 in `test_sqlite.py`, incl. legacy-db + idempotent-ALTER hazards.
2. **UI/server import cleanup**: ~15 imports hoisted from `ui/server.py` bodies
   to the module top; the **private** `orchestrator._WINDOW_DAYS` reach-through
   (in `ui/server.py` and `__main__.py`) removed by promoting it to public
   `orchestrator.WINDOW_DAYS`.
3. **Layering-guard tests** (`test_architecture.py`, +6): the dependency graph
   is now executable — an AST allow-list asserts each subpackage imports only its
   permitted siblings, composition roots don't import upward, and `ui/server.py`
   stays free of function-level imports.

**Optional / not done** (per the audit report, judgment over churn): orchestrator
decomposition (Finding 2), `core→config` inversion (5), snapshot-bypass tidy (6).

## What was completed before that? (V0.4.1 phase 3 — Experience Engine integration)

Completed the integration of the Experience Engine into the rest of OptionsPilot:
every AI recommendation now carries advisory historical context. **Backend + API
only — no frontend** (dashboard is Phase 5). Version 0.4.0 → 0.4.1, a **454-test
suite** (+30). Full design in `docs/ROADMAP-V0.4-EXPERIENCE.md` §12.

1. **Centralized AI snapshot** (`experience/snapshot.py::build_snapshot`) — the
   ONE place a deterministic decision context is captured (score, reasoning, HTF
   trend, full evidence breakdown, gate result + rejection reasons, RSI/ADX/rvol/
   ATR/EMA/MACD/VWAP/supertrend/divergence, contract Greeks, stop/target/RR,
   operating/trading/learning modes). Duck-types the `EngineDecision` (no runtime
   `engine/` dependency). Uncomputed fields (Bollinger, volume-profile histogram)
   stored as None — never invented.
2. **Feature symmetry** — AI entry (`_scan_symbol`→`_register_meta`, snapshot in
   `_TradeMeta.entry_context`) and the manual/coach path (`_capture_context`, now
   also built by `build_snapshot`) go through the one builder. Shared
   `features._entry_fields` backs both a closed trade and a live query.
3. **Advisory historical-similarity explanation** — for *tradeable* signals only,
   `_attach_historical` attaches `explain_setup(snapshot)` (n similar / win rate /
   return / calibrated confidence / grounded success & failure patterns) to the
   status payload and the Human-Mode advice notification. Computed AFTER the
   deterministic decision; never feeds back into it.
4. **Experience API** (`ExperienceEngine`, no SQL past the store): `recent`,
   `similar_trades`/`similar_to_snapshot` (→ `SimilarTrade` rows), `statistics`,
   `strategy_statistics`, `regime_statistics`, `failure_modes`,
   `success_patterns`, `explain_setup`. Over `GET /api/experience` and
   `GET /api/experience/similar?symbol=`.
5. **Storage v2** (`_migration_2`) — indexed `market_regime` (trend × IV vol) +
   `return_pct`/`hold_minutes`, backfilled from payloads; SQL-only aggregates.

**Safety reaffirmed:** nothing touches the gate/risk/sizing/entries/exits; the
deterministic score is the sole trading input; every new call site is
best-effort. All 424 prior tests still pass. Measured perf at 20k rows:
similarity summarize well under 3s, SQL aggregate under 0.5s.

## What was completed before that? (V0.4.0 phases 1–2 — the AI Experience Engine)

The first two phases of the V0.4.0 sprint that turns the AI from a static
analyzer into a system that learns from paper-trading experience. **Backend
only — no frontend change** (so the shallow-frontend-coverage risk doesn't
apply this session). Version 0.3.5 → 0.4.0. 392 → **424-test suite** (+32). Full
design/rationale/forward-plan in **`docs/ROADMAP-V0.4-EXPERIENCE.md`** — read it
before continuing this line of work.

**What was built** — a new `optionspilot/experience/` subsystem, the AI's
long-term memory, recorded **alongside** the journal (never instead of it; the
journal stays the system of record and the sole learning input):

1. **Experience Engine + store (Phase 1).** `ExperienceRecord` = a rich,
   expandable superset of `TradeRecord` (outcome, decision context,
   market/session indicators, reasoning, an exploration flag, and an `extra`
   JSON blob for future fields — screenshots/news — with *no* migration).
   `ExperienceStore` (`data/experience.db`) is built for 100k+ trades without a
   redesign: indexed query columns + full-fidelity JSON payload + a
   `PRAGMA user_version` migration framework (refuses a newer-than-supported
   schema). `features.py` extracts the record + a fixed-range normalized feature
   vector, purely.
2. **Similarity Engine (Phase 2).** `SimilarityEngine` — deterministic weighted
   distance (direction anchor + evidence Jaccard + setup/trend/tf/session +
   numerics) → the k most similar historical trades, aggregated into evidence:
   win rate, avg return/hold, most-common exit, typical failure mode, and an
   **advisory** calibrated confidence (shrinkage blend of model + history).

**Three decisions taken with the user** (see the roadmap doc §2): (A) calibrated
confidence is **advisory/display-only** — the deterministic scorer remains the
sole live-trading input; (B) "Exploration mode" becomes a **future orthogonal
`learning_mode` axis**, not a `trading_mode` value (already modelled as
`ExperienceRecord.exploration`); (C) this session = Foundation + Similarity only.

**Integration:** `Orchestrator` builds `self.experience` and calls
`record_trade` after both `journal.record` sites (AI + manual). Best-effort — a
failure is logged and swallowed, never disturbing journaling/risk/trading
(`test_record_trade_is_best_effort`). All 392 prior tests still pass unchanged.

## What was completed before that? (V0.3.5 — downloaded release crashed on launch)

The exe worked from the dev machine's `dist\` folder but crashed on another
machine (or any re-downloaded copy) with `RuntimeError: Failed to resolve
Python.Runtime.Loader.Initialize from Python.Runtime.dll` before any app code
ran. Root cause (reproduced end-to-end, not guessed): pywebview's only Windows
backend is WinForms, which drives WebView2 through pythonnet (`import clr`),
and a browser-downloaded zip extracted with Explorer flags every file with the
Mark-of-the-Web (`Zone.Identifier` ADS) — **.NET Framework refuses to load a
MOTW-flagged managed assembly** (HRESULT 0x80131515); clr_loader swallows the
exception into the opaque "Failed to resolve" error. Locally built files carry
no flag, which is why every dev-side launch worked. `loadFromRemoteSources`
config opt-outs were tested and do NOT reach clr_loader 0.3.1's load path.
Fix: `optionspilot_app.py::unblock_bundle()` deletes the `Zone.Identifier`
stream from the app's own files at startup (frozen Windows only, before
webview loads clr) — programmatically identical to Explorer's "Unblock".
Tests: `TestUnblockBundle` (+3, in `tests/test_packaging.py`). Version
0.3.4 → 0.3.5, 392-test suite. Verified by MOTW-flagging a full release copy
outside the repo and launching: the desktop window opens.

## What was completed before that? (V3.3.1 — chart reliability investigation)

A pure root-cause investigation (NO new features) of the intermittent
"switch symbols enough times → a chart loads blank and stays blank until
restart." The lifecycle was instrumented and the failure reproduced under
load + fault injection before any code changed. Version 0.3.3 → 0.3.4.
chart_check 41 → 44. 388-test suite (recount: 389).

**Root causes (all lifecycle/resource, not rendering):**
1. **No timeout on the chart fetch → permanent blank.** yfinance serializes
   every fetch through one 0.15s-per-request throttle lock; under concurrent
   load (scan loop + rapid switching) latency was measured at 10–15s+, and a
   hung upstream connection is unbounded — the first-paint spinner stayed up
   forever ("restart fixes it" = restart clears the backlog).
2. **Superseded fetches were never cancelled.** A rapid switch burst left every
   superseded request running and holding a throttle slot, starving the symbol
   the user actually landed on.
3. **Backend `yfinance.history()` had no request timeout** — a hung Yahoo
   connection blocked the in-flight slot for that key.
4. **A hung history fetch left `historyLoading` stuck true** — history loading
   silently disabled for the session.
5. **Non-monotonic data threw an uncaught "Value is null"** from
   lightweight-charts' own paint frame (backend already sanitizes; frontend
   didn't).
6. **Backend `_mem` cache was unbounded.**

**Fixes (root-cause, not blind retries):** bounded `AbortController` chart +
history fetches (15s timeout → the existing recoverable error path, and
abort-on-switch so superseded fetches stop consuming the throttle); backend
`REQUEST_TIMEOUT=10s` on `yfinance.history()`; `chEnsureMonotonic()` sanitizer
before `setData` + a guarded rAF overlay loop; bounded `MEM_CACHE_MAX=400`.

**Verified:** 250 rapid symbol switches = 0 blanks / 0 console errors;
fault injection (empty / malformed / flapping) recovers 7/7; a hung backend
now times out to a recoverable error and auto-recovers (no restart); a
12-switch burst aborts 11 fetches; memory plateaus; all prior 41 checks green.
Files: `optionspilot/ui/static/index.html`, `optionspilot/data/yfinance_provider.py`,
`optionspilot/data/cached.py`, `scripts/chart_check.py` (+3), `tests/test_cached.py` (+1).

**Remaining provider limitation:** yfinance's single global throttle still adds
latency under heavy concurrent load — now *recoverable* (bounded fetch) rather
than a permanent blank. A streaming provider (documented upgrade path) removes
the serialization entirely.

## What was completed before that? (V3.3 — chart stabilization & market validation)

A correctness sprint verified against LIVE market data (the US market was open),
not just tests. Every issue was reproduced in a real browser, root-caused, fixed
at the architecture level, and re-verified in the browser and the rebuilt exe.
Version 0.3.2 → 0.3.3. chart_check 36 → 41. `verify.ps1` green.

1. **Live sync cadence (Issue 1).** Timeframe-adaptive refresh (~7s intraday
   while open, slower for hourly/daily, idle when closed), re-armed on every
   load; `CANDLE_TTL` for fine frames lowered in lockstep so a fast poll returns
   fresh bars. **yfinance is poll-only and gives the forming bar as a flat V=0
   placeholder until it closes** — a true tick-by-tick forming candle needs a
   streaming provider (documented). Completed bars match yfinance bar-for-bar.
2. **Timezone (Issue 2).** x-axis + crosshair now render in America/New_York via
   `Intl` label formatters (timestamps unchanged, so drawings/history/timeIndex
   are untouched). Daily bars sit at ET midnight → no off-by-one date.
3. **Countdown timer (Issue 3).** TradingView-style "time to bar close" pill,
   1s tick, from the real clock; intraday-open only.
4. **Drawing render lag (Issue 4).** Added an rAF overlay sync loop — the chart
   library fires no price-scale event, so drawings used to freeze on a vertical
   drag and snap; now they track every frame the coordinate mapping changes.
5. **Drawing creation preview (Issue 5).** First click anchors + rubber-bands the
   second endpoint to the cursor; second click finalizes.
6. **Refresh discarded history + moved viewport (Issues 7 & 8 — key root cause).**
   The periodic refresh re-fetches only the base window and was REPLACING
   `CH.data`, discarding paged-in history and shifting logical indices (viewport
   jump). Now it MERGES the fresh recent window onto retained older bars
   (`chMergeRefresh`); the pre-fetch cache paint is limited to real switches.
7. **Verified not-regressed (6/9/10/11/12):** persistence across tf, candle
   correctness, blank charts (13 symbols + BRK.B + invalid), memory (no leak),
   Auto Follow — all with real mouse against live data.

Files: `optionspilot/ui/static/index.html` (formatters, timer, rAF loop, preview,
`chMergeRefresh`, adaptive refresh), `optionspilot/data/cached.py` (TTLs),
`scripts/chart_check.py` (+5 checks, hardened strand). Exe rebuilt v0.3.3.

## What was completed before that? (V3.2.2 — viewport ownership unification + Auto Follow)

Every bug reported after V3.2.1 (random recentering, history intermittently
failing, losing viewport while scrolling) was another symptom of one
underlying conflict: the viewport had no single owner. Version 0.3.1 → 0.3.2.
chart_check 33 → 36.

1. **Audit + one controller (Bug 4).** Every `fitContent()`/
   `setVisibleLogicalRange()`/`setVisibleRange()`/`scrollToRealTime()` call
   site was enumerated by owner and reason; all of them now route through
   one function, `chMoveViewport()`, instead of each caller remembering the
   `restoringViewport` convention individually.
2. **History-arming race (Bug 2, part 1).** The old wheel/touchstart/
   pointerdown listeners armed history a DOM-event tick after the library's
   own range-change fired during the same pan, so a scroll into history
   sometimes silently did nothing. Fix: arm directly off the range-change
   subscription itself.
3. **The deeper root cause (Bug 2, part 2).** Instrumented the vendored
   lightweight-charts' real callback timing: `subscribeVisibleLogicalRangeChange`
   fires on a LATER animation frame, not synchronously inside
   `setVisibleLogicalRange()`/`fitContent()`. Resetting the guard flag
   synchronously right after the call (the V3.2.1 pattern) closed the window
   before that callback ever arrived — one frame later every sanctioned move
   looked like a user pan, silently re-arming history-load. Fix: defer the
   reset two animation frames in `chMoveViewport`.
4. **Auto Follow (Bug 3) — new toggle.** OFF by default (user owns the
   viewport, nothing auto-recenters except Reset/Latest); ON keeps the
   newest bar in view across refreshes/live updates/switches; manual pan
   disables it; Latest re-enables it; persisted (`localStorage`). New
   `#ch-follow` button + `A` shortcut.
5. **`scrollToRealTime()` animation discovery.** Auto Follow wouldn't stay
   on: `scrollToRealTime()` runs a multi-frame smooth-scroll animation, and
   each intermediate tick was misread as a user pan once the 2-frame guard
   window closed — disabling Auto Follow before the animation even
   finished. Fix: `chScrollToLatest()`, a single non-animated
   `setVisibleLogicalRange` computed to the same destination, used
   everywhere instead.
6. **Bug 5 verified.** A history prepend never moves on-screen bars — only
   new (older) bars appear at the left; covered by a regression test
   capturing the on-screen time range immediately before/after a real-drag
   merge.

Tests: `chart_check` 33 → 36 (real-drag history load with no arming cheat +
stationarity; Auto Follow OFF-default/toggle/persist/manual-pan-disables/
Latest-re-enables; live updates respecting Auto Follow ON vs OFF). Also
hardened `chart_check.py` itself: the extended-hours route stub could
occasionally double-fulfill the same request (pre-existing test-harness
race) — now defensively swallowed; added a `window.__chNoAutoRefresh`
test-only flag so the chart's 30s background-refresh timer can't race a
route stub once a suite run's wall-clock time exceeds that cadence.
**388-test suite unchanged**, `verify.ps1` green end to end, exe rebuilt and
driven by hand.

## What was completed before that? (V3.2.1 — critical chart regression fixes)

Three release-blocker regressions the user still hit in the real app despite
V3.2's passing tests — because those tests measured internal state, not
user-visible behaviour. Version 0.3.0 → 0.3.1. chart_check 31 → 33.

1. **Drawings still disappeared across timeframes (Bug 1).** V3.2 fixed the
   visibility *filter* (`chDrawVisible().length` passed) but a 1m-anchored
   drawing's bar times aren't bars on 5m/1d, so `chX()` → `timeToCoordinate()`
   returned null and it painted nothing. Fix: `chX()` interpolates the pixel
   between the two bracketing INTEGER-bar coordinates (the vendored
   lightweight-charts `logicalToCoordinate` returns 0 for fractional indices but
   maps integers fine, even off-screen). Now renders on every timeframe.
2. **Timeframe switch lost context (Bug 3).** RC3's "fit on switch" threw the
   user's place away. Fix: capture the focal date before the switch and
   re-center the new resolution on it (`chCaptureFocal`/`chApplyFocal`), clamping
   each endpoint to the nearest real bar (finer tfs have shorter history → land
   on the closest candle). Recent focal preserved with ~0 drift.
3. **Viewport fought the user (Bug 2).** Same-key refresh restored the time
   range (null in whitespace past newest → snap). Fix: preserve the LOGICAL
   range (captured before `setData`); the stranded auto-fit fires only on a
   symbol-switch fallback. Latest/Reset remain the only auto-recenters.

Underlying both 1+3: `setData` fired the range-change subscription before
`restoringViewport` was set, so history loaded mid-switch and corrupted logical
indices (n grew 468→1248). Fixed by setting the guard before `setData` and
disarming history on a switch. Tests now assert real coordinates/viewport.

## What was completed before that? (V3.2 — chart completion + Extended Hours)

The final evolution of the chart subsystem, on branch **`v3-ui`** (still not
merged). Version bumped **0.1.0 → 0.3.0**. a **388-test suite**, chart_check **31**,
`verify.ps1` green; the exe was rebuilt and driven by hand.

1. **Timeframe-independent drawing engine (PARTS 1/2/5).** Drawings vanished on
   a tf switch because the model was tf-LOCKED. The v3 model stores each drawing
   once with a `visibility` policy ("all" default, or {min,max} tf bounds),
   `createdTf`, a `source` tag, and `meta`; the renderer decides per-tf whether
   to show it and never destroys it. Legacy drawings migrate to visibility
   "all". One `chAddDrawing(spec)` API (on `window`) serves user tools now and
   the AI scanner / replay engine later — one engine, no special cases.
2. **Ray tool (PART 2).** Two-click, extends infinitely past the second point;
   reuses the existing edit/persist machinery.
3. **Extended Hours (PART 4).** Confirmed yfinance supplies pre-/after-market
   candles via `prepost=True`. `extended_hours` is a display-only flag threaded
   provider→cache→payload→`/api/candles?ext=1` (trading path stays RTH-only);
   `data/sessions.py` classifies bars; the frontend has a persisted "Ext"
   toggle (disabled on daily) with pre/after-market session shading.

**Recommended next:** Replay Mode (inherits the drawing engine + session
architecture), then AI Visualization (draws via `chAddDrawing({source:"ai"})`),
then Mobile / Broker integrations. See `ROADMAP-V2.md`.

## What was completed before that? (V3.1 RC3 — final release blockers)

Three user-reported bugs from **manual** testing that the passing automated
suite had missed, each reproduced by driving the real mouse/UI before any fix:

1. **"Toolbar actions STILL don't work."** Reproducing with the real mouse
   (draw a trendline → click it to select → click the toolbar) showed the
   *source* fix from RC2 works. The culprit was the **packaged exe**: the
   `dist/OptionsPilot` bundle was built Jul 18 12:02, before RC1/RC2, so its
   `index.html` predates every toolbar/viewport/banner fix — on that build
   select/drag/resize work but recolour/duplicate/lock/hide/delete no-op,
   exactly as reported. Fix: **the exe was rebuilt.** The regression test was
   rewritten to drive the real mouse end to end and verified to fail on the
   pre-fix source.
2. **Stale banner "appears far too often."** With the market open and the
   feed flapping stale/fresh (rate-limiting), the banner re-raised on every
   stale tick for data whose newest bar never changed. Fix: a per-(symbol·tf)
   high-water mark (`CH.freshHigh`) — a stale payload only warns when its
   newest bar is genuinely older than the freshest bar already shown.
3. **Timeframe switching zoomed into one candle.** Per-(symbol·tf) cached
   viewports were restored on switch, snapping back to a stale tight zoom.
   Fix: one owner for viewport restoration — a switch always fits; only a
   same-key refresh preserves the live viewport. The per-key viewport cache
   was removed.

Also fixed while hardening the tests: a rapid symbol burst ending on an
already-cached symbol could leave the "loading" overlay and skeleton legend
stuck if that symbol's refresh came back empty — a non-first-paint load now
clears the overlay and restores the legend. `chart_check.py` → **29 checks**
(real-mouse toolbar, anti-flap banner, tf-switch tiny-zoom). **376-test suite.**

## What was completed before that? (V3.1 RC2 — final chart audit)

The **RC2 final chart release audit**, on branch **`v3-ui`** (still not
merged). Four remaining chart bugs, each reproduced in a real browser,
root-caused, fixed at the architecture level, and re-verified:

1. **Drawing toolbar actions were dead.** The capture-phase `pointerdown`
   on `#ch-main` fired before a toolbar button's click; because the click
   landed on the floating toolbar (not the drawing), `chPointerDown` took
   its "empty space → deselect" branch and cleared `DRAW.sel`, so every
   toolbar action (recolour/duplicate/lock/hide/width/delete) no-op'd.
   Fix: the capture handler now ignores events originating in `#ch-draw-bar`.
2. **The "Live data unavailable" banner over-fired.** It fires whenever a
   live fetch fails and disk-cached bars are served — but while the market
   is CLOSED those cached bars ARE the last session, so the banner is a
   false alarm that flaps whenever a background refresh trips Yahoo's rate
   limiter. Fix: `/api/candles` now reports `market_open`; the banner is
   suppressed when closed, shown (a real "behind live prices" warning) when
   open.
3. **The chart could strand the user.** Lightweight-charts clamps pan/zoom
   so it's never literally empty, but bars could be shoved to the far edge
   with a screen of whitespace ("the chart disappeared"). Fix: **Reset view**
   (fitContent) and **Latest** (scrollToRealTime) buttons + **R**/**L**
   keys, a whitespace-aware `chViewportStranded()` detector, and a
   render-time safety net that recovers a stranded restored viewport.
4. **Random viewport jumps.** Toggling RSI/MACD recentred the main chart:
   the two-way subpane sync let a freshly-created pane's auto-fit shove its
   full-history range back onto main. Fix: the **main chart is now the sole
   owner** of the time range — panes are one-way followers (`chAlignPane`).

Tests: `scripts/chart_check.py` grew to **27 checks** (+ toolbar actions,
indicator-no-jump, viewport recovery, market-aware banner, a rapid-abuse
stress burst, and a new-bar-append proxy for the market-hours rollover);
`tests/test_ui_server.py` gained 2 backend tests for the `market_open`
field. **376-test suite**, `verify.ps1` green end to end.

## What was completed before that? (V3.1 chart-stabilization sprint)

The **V3.1 chart-stabilization sprint**, on branch **`v3-ui`** (still not
merged — the user asked for `v3-ui` to stay isolated until reviewed).
Seven milestones, each root-caused and browser-verified before commit,
which made the charting system the strongest part of the app:

1. **V3.1-1 `b93eac9` — chart reliability.** The "some tickers randomly
   fail / IWM only shows volume" reports traced to three causes: a stored
   drawing with a stray price drove the price scale and crushed the
   candles (drawings now use `autoscaleInfoProvider:null`); NaN volume on
   the forming bar 500'd the endpoint during JSON serialization
   (`validate_candles` now drops NaN/inf/≤0 OHLC bars, zeroes non-finite
   volume, and logs every removal with symbol/tf context); and non-finite
   indicator values (payload runs one `validate_candles` choke point +
   `isfinite` guards). Renderer wrapped in try/catch → error overlay, not
   a half-painted canvas.
2. **V3.1-2 `0d2c870` — 13 timeframes.** 1m/2m/3m/5m/10m/15m/30m/1h/2h/
   4h/1d/1w/1mo, table-driven (`core/models._TF_LABEL`,
   `yfinance_provider._FETCH_SPEC`, `orchestrator._WINDOW_DAYS`,
   `cached.CANDLE_TTL`); a test fails if a member isn't in all four.
3. **V3.1-3 `98551e1` — infinite scroll-back.** The paging merge was
   inverted (replaced the window instead of prepending) and the trigger
   mixed bar-index vs timestamp units. Older bars now prepend with
   indicators in lockstep; viewport/zoom/drawings preserved.
4. **V3.1-4 `917d0c9` — editable drawings.** Overlay-canvas object model
   (`{id,type,tf,points,color,width,text,locked,hidden}`, stored
   `{version:2,items}`, old format migrated): select/drag/resize/color/
   width/lock/hide/duplicate/rename/delete, instant tool arming.
5. **V3.1-5 `edfe2bc` — Trade-tab chart.** The one chart instance
   relocated into a collapsible Trade slot; symbol/tf/drawings/indicators
   shared; preference remembered.
6. **V3.1-6 `5e04506` — live updates + perf.** `chSig` now includes the
   last bar's OHLCV (the forming candle no longer freezes intrabar); a
   `series.update()` fast path renders trailing bars with no flicker/reflow.
7. **V3.1-7 `2bcb84a` — chart test suite.** `scripts/chart_check.py`
   (19 headless-browser checks) wired into `verify.ps1`; verified 10
   tickers × 13 timeframes = 130/130.

Immediately preceding this sprint (same day, `61a2c60`): the packaged exe
shipped without yfinance (lazy `importlib` import invisible to
PyInstaller) — fixed with `--collect-all yfinance`, a `selftest` build
gate, and `tests/test_packaging.py`.

### Earlier: the V3 product-quality sprint (`v3-ui`)

Seven milestones (V3-0 … V3-6) + a pre-merge audit (V3-7), each built →
verified in a real browser → committed separately:

1. **V3-0 `7176843` — chart reliability.** The "app opens with no usable
   charts" bug was root-caused (not guessed): yfinance returns *empty*
   frames on transient failures, `CachedProvider` memoized those empties
   for the full TTL (poisoning retries), the disk cache was never used as
   a fallback, and the frontend had no catch/retry and silently dropped
   mid-load switches. Fixed at all four layers; the canvas can no longer
   be silently blank (loading overlay → error overlay with Retry → stale
   banner), charts auto-refresh every 30s preserving zoom. The engine's
   strict fail-closed data path is unchanged (tested explicitly).
2. **V3-1 `e06031c` — design system.** Type/spacing/elevation tokens,
   inline-SVG icon nav, 56px collapsed rail below 1180px, and a real
   pre-existing flex/grid min-width blowout fixed (`main{min-width:0}`,
   `minmax(0,1fr)`).
3. **V3-2 `641d617` — dashboard.** 2:1 layout, AI-opportunities and
   watchlist-movers side rail, action-oriented empty states.
4. **V3-3 `629c19d` — trade screen.** ATM quick-picks, risk-vs-buying-power
   line, on-tab positions with close-prefill, B/S/+/−/Enter order keys.
5. **V3-4 `a365871` — settings.** Grouped searchable config cards replace
   the JSON dump; live-trading flags visibly locked (🔒 off by design).
6. **V3-5 `776d23d` — analytics.** Coach first-run explainer, journal
   filters + cumulative-P&L curve, backtest drawdown/exit-reason panels,
   learning weight-shift bars.
7. **V3-6 `79138da` — accessibility.** Skip link, toast live region,
   `scope="col"` everywhere, `aria-current`, `?` shortcut overlay.
8. **V3-7 — pre-merge audit fixes.** A full senior-review pass over the
   branch found and fixed three real issues: `CandleCache` was unusable
   from worker threads (`check_same_thread` — the disk cache silently
   never worked in the live app, and V3-0's stale fallback would have
   returned empty in production; fixed with a locked shared connection +
   threading regression test), the chart's 30s timer never auto-retried a
   failed *first* load, and Enter could submit an order from behind the
   `?` overlay. Each fix browser- or thread-verified individually.

The audit that scoped all of this is `ROADMAP-V3-UX.md` (committed with
V3-0).

9. **Packaging fix (2026-07-18, this session, uncommitted).** The user
   found the freshly built exe unusable: every chart/quote/chain request
   failed with "No module named 'yfinance'". Root cause: the performance
   pass (`f1bae42`) made the yfinance import lazy via
   `importlib.import_module`, which PyInstaller cannot see, so every exe
   built since then silently shipped without yfinance. Fixed with
   `--collect-all yfinance` in `scripts/build_exe.ps1`; a new `selftest`
   CLI command that the build script now runs against the fresh exe
   (build fails on an incomplete bundle); and `tests/test_packaging.py`
   (+4 tests — fails the ordinary suite if any dynamic third-party
   import isn't collected). Exe rebuilt and verified live: candles
   (daily + 5m) and a 231-contract chain served from the packaged app;
   full browser flow sweep of the chart system green. **376-test suite.**

## What is currently stable?

Everything on both branches. **376-test suite passes** (+6 cached-provider tests and a CandleCache threading regression test
added in V3-0, +4 packaging-guard tests + 2 `market_open` tests added 2026-07-18). `scripts/verify.ps1` ran clean end-to-end as the closing
action of the session, and every milestone additionally got scenario-level
Playwright verification (chart failure states, the full order-ticket flow —
including the manual-entry risk gate visibly rejecting an after-hours
order, which is correct behavior — settings search, a real 25-day backtest
run, the accessibility overlay).

## What should be worked on next?

**First, the two decisions V0.5.5 surfaced (both in `docs/TODO.md`):**

1. **The app has one real data source.** Stooq is gone (JS proof-of-work
   challenge), so a keyless install depends entirely on Yahoo, reached by two
   code paths that share one upstream — and Yahoo rate-limits by IP. The
   failover machinery built in V0.5.2–V0.5.4 is correct and has nothing to fail
   over to. Decide between shipping with a documented single point of failure,
   prompting for a free Finnhub/Twelve Data key on first run, or finding
   another keyless source. **This is a user decision, not a code decision.**
2. **Verify one keyed adapter against its real API.** Still the standing gap:
   all three keyed adapters are tested only against canned payloads, so their
   response shapes are as *documented*, not as *observed*. One live run
   (`scripts/marketdata_benchmark.py --live`) with a real key closes it — and
   it is now also the fastest way to act on decision 1.

**Then, still open from before:**

**Release delivery — remaining infra.** The installer is built and wired
(`docs/INSTALLER.md`). Next, in order: (1) **Authenticode code signing** of the
setup + app exe (removes the SmartScreen warning — a real prerequisite for a
frictionless public release; `SignTool` hook is stubbed in `installer/
OptionsPilot.iss`, and it needs a cert secret in `release.yml`). (2) **Automatic
updater** — check GitHub Releases for a newer tag, download the installer, run it
(replaces the install dir only; never the storage root; `create_backup` first).
(3) Optional installer polish: branded wizard bitmaps (`WizardImageFile`).
**Before any public release:** replace the placeholder `LICENSE`. Also do the
**manual installer QA** in `docs/INSTALLER.md` (fresh/upgrade/repair/uninstall on
a real Windows box) — it can't be automated.

**Optional architecture follow-ups (from the V0.4.2 audit, `docs/ARCHITECTURE-
AUDIT-V0.4.2.md` §11).** None urgent: Finding 2 (extract a `ManualTradeReconciler`
from `orchestrator.py`) only if that file keeps growing; Finding 5 (de-invert
`core→config` in `logging_setup`); a journal `overview()` SQL path when journaled
trades approach five figures. Leave unless there's a reason.

**V0.4.x continuation (Experience Engine).** The design doc
`docs/ROADMAP-V0.4-EXPERIENCE.md` §11 has the full forward plan. Phase 3 is done.
In order:

- **Phase 4 — `learning_mode` axis + Exploration.** Add the third orthogonal
  mode axis (normal/exploration) to `config/settings.py` + `config/runtime.py`
  following the `trading_mode`/`operating_mode` orthogonality pattern; in
  exploration mode take tagged, strictly risk-limited lower-confidence paper
  trades. The plumbing already exists: `ExperienceRecord.exploration`, its store
  column, the `learning_mode` snapshot field, and the exploration→record wiring.
  (Promoting calibration into the gate is a *separate*, dedicated decision —
  Decision A; do not fold it into Phase 4.)
- **Phase 5 — AI Performance dashboard.** New tab over `/api/experience` +
  `/api/experience/similar` (both already built). This is the **single-file
  frontend** — **manually browser-verify** (no automated UI coverage).
- **Phase 6 — Strategy discovery infrastructure.** Group experiences by shared
  characteristics (the `extra["snapshot"]` evidence breakdown is the raw
  material) for later pattern mining. Infra only.

**Pre-existing, still open:**

1. **The user reviews the `v3-ui` branch** (run `.\scripts\dev.ps1` on the
   branch and click through) and decides on merging to `main`. The V0.4.0
   Experience Engine work also lives on `v3-ui`, uncommitted.
2. **Market-hours chart validation** (couldn't be done — market closed):
   confirm live candles/volume/indicators/price-line update during a
   session, and that the forming candle updates in place (the V3.1-6
   fast path was verified with a simulated tick, not a real one). The
   live-update code path and the intrabar `chSig` fix are in place; this
   is confirmation, not new work.
3. Remaining, explicitly-not-done `ROADMAP-V3-UX.md` items if the user
   wants V3 continued: **H5** notification center with persistence (needs
   a small backend store — check `optionspilot/notify/` first), **N2**
   chart↔option-chain cross-links, **N4** toast stacking.
4. One deliberately-skipped verification: the order-ticket **fill** path
   (fill → stop-loss pre-arm → position row) was verified only up to the
   risk-gate rejection because the market was closed — worth one
   market-hours pass.
4. Then the standing scope decision (unchanged from before V3): V2-5
   replay engine, V2-6 journal/improvement dashboard, the deferred V2-4
   workspace layout, or letting paper-trading data accumulate.

## What files are currently important?

- `optionspilot/ui/static/index.html` — the entire frontend; V3/V3.1
  touched every tab. The chart lifecycle is the most intricate code:
  `loadChart`/`chRenderData` (with the `chTailUpdate` live-update fast
  path), the `chSig` signature (now includes last-bar OHLCV), the
  history-paging merge (`chMergeHistory`/`chLoadHistoryChunk`), and the
  editable-drawing overlay system (`DRAW` model, `chDrawRender`,
  `chPointerDown/Move/Up`, `chDrawAct`) rendered on the `#ch-draw` canvas.
- `optionspilot/data/base.py` — `validate_candles` is now the single
  sanitization choke point (drops NaN/inf/≤0 OHLC, zeroes bad volume,
  logs); do not weaken it.
- `scripts/chart_check.py` — the 41-check chart regression suite; run it
  (via `verify.ps1`) after any chart change.
- `optionspilot/data/cached.py` — `EMPTY_CANDLE_TTL` and
  `get_candles_stale_ok()` are new; the strict `get_candles` contract is
  unchanged and must stay that way (fail-closed trading).
- `scripts/verify.ps1` — still the "is the repo healthy" command.
- `docs/ROADMAP-V3-UX.md` — the audit + scope this sprint implemented;
  the unimplemented remainder lives there.

## What should NOT be modified?

See `AI_CONTEXT.md` "Things future AI assistants should never change
without careful review." New this session: **do not relax
`CachedProvider.get_candles` to serve stale data** — the stale fallback
exists only behind `get_candles_stale_ok()` for display surfaces, and the
engine's empty-means-skip behavior is load-bearing for trading safety.

## Known issues

- **`docs/ARCHITECTURE-MOBILE.md` is still untracked** — the mobile
  architecture proposal from a prior planning session; commit or discard
  when the user decides. Everything else this session is committed.
- `OptionsPilot.exe serve` (the windowed exe running the browser-serve
  subcommand) starts its internals but never binds the port —
  pre-existing, discovered 2026-07-18 while verifying the packaging fix.
  Desktop `ui` mode and dev-repo `python -m optionspilot serve` both
  work. Tracked in `TODO.md`.
- The Trade tab's fill-path UX (post-fill stop-loss pre-arm) has no
  market-hours verification from this session (see above).
- Frontend coverage remains shallow relative to the app's size
  (`browser_check.py` is tab-navigation only) — the session's per-flow
  Playwright scripts live in the session scratchpad, not the repo; making
  them permanent is a natural follow-up (see `TODO.md`).
- No CI / linting — still deliberate, still just recommended
  (`CONTRIBUTING.md`).

## Suggested first prompt for the next AI session

> Read `docs/AI_CONTEXT.md`, `CLAUDE.md`, and this file, then run
> `git log --oneline -10`, `git status`, `git branch --show-current`, and
> `git diff --stat` yourself — note that V3 work lives on the `v3-ui`
> branch, not `main` — then run `.\scripts\verify.ps1` to confirm the
> baseline is green. Then: [either "the v3-ui branch is approved — merge
> it to main," or a specific next task]. If no task is given, ask whether
> the V3 branch has been reviewed before proposing anything built on it.
