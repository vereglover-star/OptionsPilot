# CHANGELOG.md

Major features by development phase. Committed history is authoritative for
exact dates/diffs (`git log`); this file summarizes intent and scope for
someone who doesn't want to read 12 commit bodies.

## 2026-08-03 — Packaging fix: the windowed exe could not start

*Independent bugfix, found on the first real EXE launch after V0.9.1. 2243 →
2247 tests (+4).*

The packaged app died before drawing a window with `ValueError: Unable to
configure formatter 'default'`, caused by `AttributeError: 'NoneType' object has
no attribute 'isatty'`.

**A `--windowed` PyInstaller build has `sys.stdout is sys.stderr is None`**, and
`uvicorn.logging.DefaultFormatter.__init__` ends with
`self.use_colors = sys.stdout.isatty()`. `uvicorn.Config.__init__` calls
`configure_logging()`, which runs `dictConfig` over uvicorn's default
`LOGGING_CONFIG` — so merely *constructing* the config was fatal. No bind, no
request, no server.

`use_colors=False` was rejected: it silences the crash and leaves uvicorn's
handlers bound to `ext://sys.stderr`, i.e. to None, trading a loud failure for
records that vanish. Instead uvicorn configures nothing
(`logging_setup.uvicorn_logging_kwargs()` → `log_config=None`), because
`setup_logging` is already the single owner of this application's logging and
has known that stdio can be absent since the first windowed build. It now also
**adopts** uvicorn's loggers onto its own handlers — otherwise, with the root
logger unconfigured, a uvicorn warning would fall to `logging.lastResort` and be
written to the `sys.stderr` that does not exist.

Fixed at **both** uvicorn call sites: the desktop launcher's embedded transport
and `ui/server.py::serve`, which the packaged exe reaches via `OptionsPilot.exe
serve` inside the same windowed process.

**Why nothing caught it.** Every test runs under pytest with real streams
attached, so `sys.stdout.isatty()` returns a boolean and the branch is never
entered. The build script's packaged-selftest gate passed *with the bug present*,
because `selftest` never constructs a uvicorn Config — it proves the bundle's
imports resolve and never touches the launch path. Four regression tests now run
with `sys.stdout`/`sys.stderr` monkeypatched to None, one of which pins that
upstream uvicorn genuinely still crashes, so the guard cannot quietly end up
protecting against nothing.

Verified by rebuilding and launching the real windowed exe: it stayed up,
listened on `127.0.0.1:60171`, ran a live market scan and logged cleanly.

## 2026-08-03 — V0.9.1: runtime & thread ownership

*11 commits (`d92de20`…), 2026-08-02 → 2026-08-03. 2158 → 2243 tests (+85). No
feature, no trading-behaviour change, no new runtime dependency. One deliberate
API change: `/api/runtime` no longer carries `health.memory`.*

`BackgroundRuntime` existed since V0.8 and did not own what it claimed to own.
Pause, resume, shutdown and health reporting described *intent*; alongside them
ran a raw thread per manual scan, a raw thread per backtest, a thread the
intelligence engine started for itself, a complete second scheduler nobody
called, and an `exit()` guard that two threads could both walk through. This
milestone made the claim true.

**The bug it started from.** One coordinator thread ran every task inline, so a
market scan — a full watchlist fetch plus an option chain per symbol — froze
every other task for its duration. The tray tooltip has a 10-second interval
precisely so it stays current, and it was the most visible casualty. C1 stated
that as a failing test before any fix existed; C2 added work lanes over a
bounded pool, **inert by default** so activation is one `lane=` argument and
rollback is deleting it; C3 moved the scan.

**What ownership turned out to mean.** Each of the next four commits found the
runtime lacked a concept, not just a caller. Pause could not be honest without
`pause_pending`, because pause never interrupts work in flight and a UI that
says "paused" the moment the request returns is describing itself (C4). A
user-initiated task could not exist at all without `TaskSpec.on_demand`, since
`register` deliberately makes every task immediately due — so registering a
"manual scan" task would run a scan on every server construction (C5). And the
pool bound stopped being a number and became a derivation: four registered
worker tasks means four slots, because a bound one too small does not error, it
leaves a job at "running" with nothing happening (C6).

**Three races, all the same shape, all measured.** The manual-scan dispatch was
`if not running and not locked: Thread(...).start()` — two concurrent requests
both passed and the second ran a whole extra cycle (C5). `exit()` was `if
self._exited: return` with nothing serialising it, and with eight concurrent
callers **all eight ran the shutdown and Restart spawned eight successor
processes**, unrejected because releasing the single-instance lock is the first
thing a restart does (C7). Checking a slot and then claiming it is not claiming
it; `MarketDataControl` had already shipped this exact shape.

**Then the deletions.** The dead `_loop` — a second scheduler with a docstring
inviting `Thread(target=self._loop)`, over a workload that places trades (C8).
The tracemalloc monitor, which traced every allocation in a pandas/numpy process
to feed one threshold rule and a payload block no client read (C9). The
launcher's HTTP self-poll, which asked over the network a question the object in
the same process answers (C10). And finally `launch()`'s 85 lines of
`# pragma: no cover` wiring became `DesktopApplication`, whose composition is
built from injected collaborators and asserted without a GUI (C11).

**Verified** by a 30-minute soak rather than a green suite, because concurrency
defects are not deterministic: 27,436 coordinator beats at a worst gap of 0.09 s
against a 0.4 s scan, 2,594 scheduled scans, 207 manual scans, 300 backtests
with 600 refused by the single slot, 300 intelligence refreshes, **zero
overlapping anything**, 100 pause/resume cycles with no violations, bounded
shutdown, no leaked threads. Plus 2,400 concurrent `exit()` calls and 150
start/stop lifecycle cycles.

**Two lessons worth carrying.** A soak that passes without exercising the
feature is worse than one that fails — the C5 soak passed a full 30 minutes with
`manual ran: 0`, and it now fails a run in which a path never executed. And a
performance rationale written before measuring is a liability: C10's comment
claimed the polled endpoint was expensive, and the benchmark said 0.02 ms.

## 2026-08-02 — V0.9.0: the verification floor

*11 commits (`2707a01`…`e403da6`), 2026-07-30 → 2026-08-02. 2065 → 2158 tests
(+93). No feature, no trading-behaviour change, no new runtime dependency.*

A milestone about whether this project's own claims can be trusted. Every
milestone after it — V0.9.1 through V0.9.5 — is a refactor of live code, and a
refactor is only as safe as the evidence that it changed nothing. V0.9.0 built
that evidence.

**What was actually wrong.** The version constant said `0.6.0` while the code
had shipped as V0.8.2 — four releases in a row of a number nobody checked,
because the release workflow compared the *tag* to the *constant* and nothing
compared either to the prose. Dependencies were unpinned on the release path, so
two builds of the same tag were not the same software. Coverage had never been
measured, in a codebase with 2,000 tests. `scripts/api_contract_check.py` had
been written three milestones earlier and **never once run** — no workflow, no
script, no wrapper called it. CI ran on Windows only, against a platform
abstraction whose entire purpose is portability. 3,238 build artifacts were
tracked in git, 96 MB per clone. And an update was verified against *the size
GitHub reported for the asset*.

**Delivered:** the version reconciled and a docs-version gate added (C1, C7); a
dependency lockfile applied to CI and the release path (C2); ruff with a narrow
rule set and a documented 573-item backlog (C3); coverage measured at 91.49% and
ratcheted (C4); the API contract check wired, plus a test that fails if any
`scripts/*_check.py` has no caller — the orphan *class*, not just the instance
(C5); a two-platform CI matrix with Windows canonical and coverage enforced only
there (C6); build artifacts untracked and the documentation reconciled (C7);
SHA-256 checksums published per release and verified by the updater, with
`Assurance` reporting *how* a file was checked rather than only whether (C8);
and Authenticode verification on the client — a WinVerifyTrust verdict at the
updater's OS boundary (C9-1) enforced by a four-state policy in the validation
gate (C9-2).

**Two specification corrections came out of implementation, not review.** The
C9 plan called for `signtool verify` on the client; signtool ships with the
Windows SDK, which no end user has, so that check would have returned "cannot
determine" on every real machine — present in the code, inert in production.
It uses `wintrust.dll` instead, the API signtool itself calls. And the plan's
`bool | None` verdict could not express its own Phase 1 policy: *unsigned* and
*invalid* had to be separable, because every release before V0.9.0 is unsigned
and refusing those would strand every existing installation behind an update
they could no longer install. `SignatureVerdict` is four-valued. C8's own
backward-compatibility test is what caught it.

**Deliberately deferred: C9-3 and C9-4, the release-side signing.** Signing
production builds requires a purchased code-signing certificate, and OptionsPilot
is not yet entering public distribution. This is a business decision, not
unfinished engineering — the client-side half is complete, tested and shipping
inert, and the release pipeline work is a day once a certificate exists. See
`docs/ROADMAP.md` ▸ Deferred.

**Also fixed:** `scripts/chart_check.py` had begun failing at its history
scroll-back, taking `verify.ps1` red. Not a chart defect — the test raced the
viewport guard, and because `chCanLoadHistory` is consulted only from the
range-change callback, a settled range emits no further event and the missed
trigger never returns. A real scroll emits a stream of events and cannot hit it.

**Not done, and not deferred:** `pip-audit` and Dependabot are named in the
milestone's own definition of done and never received a commit. That is an
omission, tracked in `docs/TODO.md`.

Verified: 2158 tests, ruff green, coverage 91.56%, `verify.ps1` PASS across all
13 gates.

## [Uncommitted] 2026-07-30 — V0.8.2 hotfix: the tray icon never appeared

*+23 tests. One line of behaviour, one line of cause.*

With the close deadlock fixed, closing the window worked: the dialog appeared,
the window vanished, the process stayed alive, the runtime kept monitoring — and
**no tray icon ever showed up**, in the dev build and the packaged exe alike, not
even in the overflow.

**Root cause.** `PystrayTray._run_icon` passed a custom `setup` callback to
`pystray.Icon.run()` (added in V0.8.1 to close a genuine start/stop race). From
pystray's own docstring:

> *"If not specified, a simple setup function setting `visible` to `True` is
> used. If you specify a custom setup function, you must explicitly set this
> attribute."*

It is an `if/else`, not an addition — `pystray/_base.py::_start_setup`:

```python
def setup_handler():
    self.__queue.get()
    if setup:
        setup(self)          # ours: recorded "active", nothing else
    else:
        self.visible = True  # the ONLY path to _show()
```

`visible = True` is the sole caller of `_show()`, which is the sole caller of
`Shell_NotifyIcon(NIM_ADD)`. So the adapter created a real `Icon`, started a real
thread, entered a real Win32 message loop, reported `lifecycle_state == "active"`,
raised no exception — and **never asked Windows for an icon**. Measured on the
real stack, the old adapter issues `NIM_ADD` exactly **0 times**. The same gate
silently disabled tooltip updates too: `Icon.title`'s setter only calls
`_update_title()` `if self.visible`.

**The second half of the bug, which mattered more.** `start()` returned `True`
after *starting a thread*, not after the icon existed. The launcher stored that
in `tray_started`, and `on_closing` uses `tray_started` to choose between hiding
and exiting — so the app hid itself into a tray that had no icon. `start()` now
waits for readiness and returns whether the icon is genuinely in the notification
area; a failure means close-behaves-as-exit, which is wrong-but-visible rather
than a disappearing application.

**Also fixed while in there.** Failures were swallowed at `log.debug`/`log.warning`
with no traceback and no way for a caller to ask what went wrong (`last_error`
now exposes it, and the setup callback catches `BaseException` because it runs on
*pystray's* thread, where an escape vanishes into the default excepthook).
`Image.open` is lazy, so a corrupt icon failed on the tray thread after `start()`
had already claimed success — the decode is now forced inside `start()`. Every
menu entry was drawn as an empty **checkbox**, because `checked=` was passed
unconditionally and pystray renders any non-`None` `checked` as one. And no item
was marked `default`, so left-clicking the tray icon did nothing at all; `Open
OptionsPilot` is now the default action.

**Not the cause, checked anyway** (the questions this kind of bug invites):
`run_detached()` is *not* required — on win32 it is literally
`Thread(target=self._run).start()`, so calling it from our own thread would only
add a thread we no longer hold a handle to. pystray performs **no COM
initialisation** anywhere and needs none. pywebview does not own pystray's pump:
`_win32._run` creates its own hidden window and its own thread message queue. And
the `Icon` object is not garbage collected — the adapter holds `_icon` and the
live tray thread's frame holds the same object (asserted by a `weakref` test).

**Verified on real Windows, old vs repaired**, with `Shell_NotifyIcon` wrapped to
capture the return value pystray discards:

| | `start()` | `lifecycle_state` | `icon.visible` | `NIM_ADD` | `NIM_DELETE` on stop |
|---|---|---|---|---|---|
| V0.8.1 | True (thread only) | `active` | **False** | **0 calls** | False (nothing to remove) |
| Repaired | True in 0.47 s | `active` | True | **1, returned True** | **returned True** |

And end-to-end through the real application — real uvicorn, real `UIServer`, real
pywebview/WebView2 window, real `WM_CLOSE`, real pystray — **17/17 steps**:
window appears, tray icon added by the shell, X hides the window while the GUI
thread keeps pumping, icon still present, restore works, five hide/restore cycles
add no second icon, exit removes the icon (`NIM_ADD` count == `NIM_DELETE` count),
no orphan icon, no zombie process, no non-daemon thread left.

**Tests added** (`tests/test_desktop_tray.py`): the fake `pystray.Icon` now models
the real contract — `run(setup)` does **not** show the icon — because a fake that
showed it on `run` is exactly why this shipped. Covers: the icon reaches the
notification area; `start()` is False until it does; the thread stays alive; the
`Icon` survives a `gc.collect()`; repeated start reuses one icon; hide/restore
cycles leave it alone; stop removes it and ends the thread; a missing icon file, a
corrupt one, an unshowable one and a loop that dies immediately each surface a
real error instead of a silent "healthy".

## [Uncommitted] 2026-07-30 — V0.8.2: independent audit of the V0.8/V0.8.1 runtime

*2056 → **2065 tests** (+9). No new feature, no new dependency, no architectural
change. An independent audit of every V0.8/V0.8.1 change, treating the previous
certification as an unverified claim.*

**The headline defect: clicking X froze the app, and the tests said it was fine.**

pywebview binds its `closing` event as `Event(window, should_lock=True)`, which
means handlers run **synchronously on the WinForms message pump**, inside
`Form.FormClosing`. `_DesktopController.on_closing` did three things that cannot
be done from there:

* `window.evaluate_js(...)` — WebView2's `ExecuteScriptAsync` continuation is
  scheduled on `syncContextTaskScheduler`, i.e. that same message pump, and
  pywebview then calls `semaphore.acquire()` with **no timeout**. Called from the
  pump, the release can never arrive. Unbreakable deadlock, no traceback, white
  title bar, "Not Responding" — exactly the reported symptom, and on the branch a
  **fresh install takes by default** (`close_behavior="tray"` with the close
  prompt not yet dismissed).
* `server.close()` and `tray.stop()` — up to five and two seconds of thread
  joins. Windows ghosts a window that stops pumping for five.
* `window.destroy()` — re-entrant `Form.Close()` from inside `FormClosing`.

`on_closing` now *decides* and returns; every consequence runs on a worker
(`_defer`). The class docstring records the thread-ownership rule, because the
rule is invisible from the call site.

**Why the tests missed it.** `tests/test_desktop_tray.py`'s window double was a
plain recorder: `evaluate_js` appended a string, `close()` returned instantly.
It modelled none of the thread contract, so a guaranteed deadlock passed — and
`test_disabled_tray_close_exits_instead_of_hiding_an_orphan_process` actively
asserted the blocking behaviour by requiring `server.closed == 1` *inside* the
handler. The double now raises `GuiThreadViolation` (a `BaseException`, because
the lifecycle code wraps these calls in `except Exception` and a real deadlock is
not catchable) when a pump-hostile call arrives on the closing thread.

**Other defects found and fixed.**

* **`Restart` could not work.** `exit()` spawned the successor *before* releasing
  the single-instance port, so the new process lost the race to its own parent
  and greeted the user with "OptionsPilot is already running". The lock is now
  released first, and the mutex retries briefly to absorb the overlap.
* **A frozen build restarted itself wrongly.** `[sys.executable, *sys.argv]` in a
  PyInstaller build passes the exe its own path as `argv[1]`.
* **Two implementations of the single-instance mutex.** `ui/desktop.py` carried
  its own copy of the socket lock *and* its own copy of port 8786, duplicating
  `host.adapter.DesktopHost` — the drift class this codebase has paid for three
  times. The launcher now asks the host.
* **The one maintenance slot admitted several workers.** `start_maintenance`
  checked `job.running` and then started a thread that claimed the slot *later* —
  a check-then-act, measured admitting 8 of 8 concurrent requests. Two concurrent
  cache rebuilds is the exact thing one slot exists to prevent. `job.claim()` is
  now atomic, and it also fixes the flaky `test_progress_is_reported_and_ends_at_one`
  (which read a job that had been accepted but had not yet begun) and a
  cancellation silently discarded by validation's second `begin`.
* **A WebSocket client could stall every HTTP request in the process.** The v1
  `/api/v1/ws` loop called `server.status_payload()` — which takes `UIServer.lock`
  — directly on the asyncio event loop. FastAPI gives the synchronous REST
  handlers a threadpool for free; an `async def` handler gets no such thing. Now
  `asyncio.to_thread`.
* **`hello.accepted` carried `"timestamp": null`** in every fresh session: it read
  the last health check, which is null until the first six-hourly sweep.
* **The idempotency store held an open SQLite write transaction across the
  callback** — and one of those callbacks is an update check that talks to GitHub.
  The lock still spans the callback (that *is* the contract); the connection no
  longer does.
* **`tracemalloc.start(10)`** stored ten stack frames per live allocation for the
  whole life of a desktop session, to feed a health field that only ever calls
  `get_traced_memory()`. Nothing calls `take_snapshot()`. Now `start(1)`.

**Tests added** (`tests/test_runtime_lifecycle.py`, new): a full session
(start → hide → restore → pause → resume → exit) leaves no worker thread behind;
five restart cycles produce exactly one scheduler and no duplicated task; shutdown
stays bounded when a callback wedges; a permanently failing task gets one retry,
not a hot loop. Nothing previously asserted any of this, though "no thread leaks"
and "no scheduler duplication" were both certification criteria.

**Verified on the real stack, before and after.** A harness
(`scratchpad/live_close_harness.py`, not shipped) runs the real `launch()` — real
uvicorn, real `UIServer`, real pystray tray, real pywebview/WebView2 window, real
`events.closing` wiring — posts a real `WM_CLOSE` (byte-for-byte what the X
button sends) and polls
`SendMessageTimeout(WM_NULL, SMTO_ABORTIFHUNG|SMTO_BLOCK)`, the same predicate the
shell uses to decide a window is not responding:

| Branch | V0.8.1 handler | Repaired handler |
|---|---|---|
| `tray` + prompt not dismissed (**a fresh install's default**) | pump dead for the whole 40 s budget, window never closed | pump stalled **0.0 s**, dialog raised, window responsive |
| `exit` | closed in 1.1 s, no measurable stall | closed in **1.14 s**, pump stalled 0.0 s, `launch()` returned cleanly |
| `tray` + prompt dismissed | — | hid to tray, pump stalled 0.0 s |

The deadlock reproduced exactly as reported and is gone. Note the honest detail:
on the `exit` branch the old code was **not** observably broken in this
environment — shutdown happened to be fast. The blocking-shutdown hazard there is
real by construction (up to 7 s of thread joins against the 5 s the shell ghosts
a window at) but its worst case did not trigger in the measurement, so that part
of the fix is defensive rather than demonstrated.

**Remaining gap.** No human has clicked the button, and the audit environment's
windows are not on the interactive desktop, so the *visual* symptom (white title
bar, shell ghost frame) was never on screen to observe — only the message-pump
condition underneath it, which is what produces it. One manual click on a real
desktop closes this out.

## [Uncommitted] 2026-07-28 — V0.7.0: platform foundation & cross-platform architecture

*1908 → **2027 tests** (+119); a new **21-check** headless-browser suite
(`scripts/workspace_check.py`, wired into `verify.ps1`). **No trading-behaviour
change, no new runtime dependency, no new tab, no UI redesign, and no test
removed.** Full design: `docs/ARCHITECTURE-PLATFORM.md`.*

**The problem.** OptionsPilot was already a client-server system that happens to
ship both halves in one process, but it had no boundary between *the application*
and *the desktop transport*. `ui/server.py` held FastAPI routing and, in the same
1,700 lines, the decisions about what a client should be shown: which twelve of
thirty-eight metrics are a headline, how a maximum drawdown is computed, what
four buckets a pasted list of tickers falls into, how many periods of a five-year
series to ship. All of it correct — and none of it reachable without importing a
web framework. A second client asking *"what is my max drawdown"* had exactly two
options: import FastAPI, or recompute it. The second is how two screens come to
disagree about one number, which is the failure this codebase has already paid
for three times (`data/health.py` V0.5.3, the settings ranking V0.5.7, the guide
catalogue V0.6.1).

**What was built.**

1. **`optionspilot/services/` — the platform-independent application layer.**
   `PortfolioService` (positions, account, realised performance, P&L windows,
   setup history), `WatchlistService` (parse/validate/edit and the four-bucket
   classification), `IntelligenceService` (the snapshot projections),
   `NotificationService`, `WorkspaceService`, `sync.py` (the persisted-object
   inventory), `viewmodels.py` (frozen dataclasses of primitives) and
   `ServiceRegistry` (the one place they are wired). Every service takes
   injected, duck-typed collaborators and returns view models — the concrete
   answer to *"if Flutter needed this tomorrow, what interface would it want?"*
   **Nothing was rewritten**: `UIServer` kept every method name and every wire
   shape, and the bodies delegate.

2. **`optionspilot/host/` — the host platform abstraction.** `capabilities.py`
   is data: a `HostProfile` per target (`desktop`, `headless`, `web`, `ios`,
   `android`) over thirteen `Capability` values, with a stated *reason* for
   every notable absence, and `implemented=False` on the three that do not
   exist. `adapter.py` is behaviour: storage root, temp space, external URLs and
   the single-instance lock (moved out of `ui/desktop.py`, same socket, same
   port). The rule it enforces: a business-logic module may ask a **capability**
   question, never an `sys.platform` question.

3. **The workspace moved off `localStorage`.** Selected symbol, timeframe,
   indicators, extended hours, auto-follow, watchlist sort, tab and saved
   layouts now persist server-side through `RuntimeSettings`
   (`GET/POST/DELETE /api/workspace`). `localStorage` remains the synchronous
   fast path — `CH.sym` and friends are read at script-eval time, long before a
   fetch can resolve — but the server is now the **durable** copy, and a profile
   with no workspace keys adopts it. Proven the only way it can be:
   `scripts/workspace_check.py` wipes `localStorage` in a real browser, reloads,
   and asserts the symbol and timeframe **on screen**.

4. **Synchronization boundaries, classified.** `services/sync.py` inventories 20
   durable objects plus 2 still client-trapped, each with a `SyncDomain` and a
   `SyncPolicy`. Nothing syncs anything — this is the classification that has to
   exist *before* one could, and it is answerable today only because there is
   exactly one device and it therefore cannot yet be wrong. Exposed at
   `GET /api/diagnostics/sync`; `data/credentials.json` is the only `NEVER`.

5. **Notifications gained a catalogue.** Thirteen kinds with severity and a
   `pushable` flag deliberately orthogonal to it, six of them events V0.6.0 and
   V0.6.1 already produced but which could only be discovered by polling.

> ### ⚠ One real defect found and fixed, and it had shipped for three milestones
>
> `/api/learning` built its `WeightStore` from `Path("data") / "learning" /
> "weights.json"` — **relative to the process working directory**, one of the
> CWD-relative hardcodes V0.4.4's storage split was meant to eliminate. The
> engine loads its learned weights from the per-user storage root, so the
> Learning tab was reading a *different file*: on a real install, one that does
> not exist (so it reported no learned weights however much the engine had
> learned), and in a dev checkout, whichever `./data/learning/weights.json`
> happened to be next to the process. The `effective` column came from the live
> scorer and really was right, which is exactly what made it look plausible.
> `tests/test_architecture.py::test_no_cwd_relative_storage_paths` now forbids
> the whole class, and the regression test was verified to fail against the old
> code.

> ### ⚠ Three defects found by attacking this milestone's own work
>
> 1. **A bound method captured at construction.** `ServiceRegistry` first took
>    `self._live_symbol_check` directly, so a later reassignment was silently
>    ignored — an existing test caught it within minutes. The three overridable
>    seams are bound late now.
> 2. **A default that matched nothing.** The workspace's default tab was `dash`;
>    the frontend's landing button is `dashboard`. Every fresh profile called
>    `switchTab("dash")` and relied on that function's unknown-name early return
>    to do nothing — harmless and wrong, which is the combination that survives
>    review.
> 3. **A declared sync domain with no entries.** `SyncDomain.WORKSPACE` was
>    empty because every workspace fact had been folded into the
>    `data/settings.json` PREFERENCES row, so `report()` omitted the domain
>    entirely and the inventory read as complete while saying nothing about the
>    one domain the milestone built. A domain with no entries is not evidence
>    that nothing is in it.

**Also fixed while attacking the frontend:** `typeof X` does not guard a `const`
in its temporal dead zone (it throws), which would have made the first tab click
of every session a console error; adoption echoed its own `localStorage` writes
back to the server; and the "is this a fresh profile" check read `localStorage`
*after* an `await`, so a user reaching the Charts tab mid-fetch would have made a
genuinely fresh profile look established — on the one launch where adoption
matters.

**Seven new architecture guards**, each verified to fail when its invariant is
deliberately broken: `services/` may not import `ui/`; `services/` and `host/`
may import no web or GUI framework at all; `host/` stays core-only; no
`sys.platform` branch outside `core/paths.py`, `host/` and `update/installer.py`;
no CWD-relative storage path anywhere; every `AppPaths` file has a sync policy;
every declared sync domain has at least one object.

**Verified:** 2027 tests, 21/21 `workspace_check`, 135/135 `guide_check`, 54/54
`intelligence_check`, 46/46 `marketdata_check`, `chart_check` green, 88/88
market-data stress, `browser_check` + `check_html_ids` + `check_docs` green.

## [Uncommitted] 2026-07-28 — V0.6.1: intelligent user experience & interactive onboarding

*1849 → **1908 tests** (+54); a new **135-check** headless-browser suite
(`scripts/guide_check.py`, wired into `verify.ps1`). **No trading-behaviour
change, no new runtime dependency, no new tab, and no validation weakened** —
`OrderManager.place` still refuses exactly what it refused before; the ticket
now simply stops you building the order it would refuse. Full design, decisions
and limitations: `docs/ONBOARDING.md`.*

**The problem.** By V0.6.0 the backend was substantially more sophisticated than
the experience of using it. Nothing was missing; everything was unexplained. The
app assumed a user who already knew what delta was, why a stop cannot be a buy
order, and what "process score" meant — and for everyone else the honest answer
to *"how do I learn this?"* was **read the docs or go and watch a video**, which
is a design failure rather than a documentation gap. The rule this milestone was
built on: when a user becomes confused, the question is not where to document
it, but why the software was able to let them.

**What was built.**

1. **`optionspilot/ui/guide.py` — the domain layer.** Pure and deterministic
   (no I/O, no clock, no network): state validation, merge semantics, and the
   rules that turn measured feature usage into a suggested walkthrough. Progress
   persists through `RuntimeSettings` into `settings.json` under a `guide` key —
   **not localStorage** — for the same reason the watchlist does: a user who
   reinstalls, restores a backup or clears their WebView2 profile should not be
   greeted as a beginner. Two new endpoints, `GET /api/guide` and
   `POST /api/guide/state`.

2. **A data-driven tutorial engine, in `index.html`.** Eleven walkthroughs, 52
   steps. A step is a selector, a sentence, how it advances (`Next`, or a real
   click on the real control) and an optional `when` predicate, so **adding a
   screen's walkthrough means adding data, not code** — which
   `scripts/guide_check.py` makes testable by driving tutorials whose contents it
   does not know. The page stays fully interactive during a tour: `#gd-ring` is
   `pointer-events:none` and one enormous spread `box-shadow` does both the
   dimming and the cutout, so the button the user presses is the real one.

3. **Contextual help, four ways in.** A header **Learn: \<screen>** button that
   relabels on every tab switch, a **?** beside dense panel headings, a
   searchable help centre on **?** / **Ctrl+K** indexing every tutorial, all 37
   glossary terms and three actions, and new **Help ▾** entries. `?` was
   *re-pointed, not taken*: the keyboard-shortcut card is a result inside the
   help centre and links back to it, so both directions still work.

4. **A 37-term glossary with adaptive tooltips.** Three to five sentences of
   plain English saying what the thing *tells you*, each with a concrete example,
   no formulas. Two attributes on purpose: `data-learn` is hover **and** click
   (inert text), `data-tip` is hover only (controls that already do something) —
   without the split, clicking the EMA pill would open a glossary card instead of
   switching on EMA.

5. **Order-ticket guardrails.** `OrderManager.place` refuses a stop, target or
   trail on the buy side, and a sell of a contract not held — and every one of
   those was reachable in two clicks and discovered only on submit. Exit-only
   order types are now **removed** while buying (not greyed), Sell to close is
   disabled with nothing held, selecting a contract you do not hold re-arms the
   buy side, quantity is clamped to the position size, and every correction
   states **what changed, why, and what to do instead** in an `aria-live` line.
   The backend validation is untouched and still authoritative.

6. **Empty states that teach.** The Journal, working orders, open positions and
   notifications now say what will fill them and offer the first step, instead of
   "None."

7. **Accessibility.** `html.gd-nomotion` is one switch for the whole app, fed by
   the OS preference and overridable **in both directions**; full keyboard
   navigation of tours and search; `role="dialog"` + `aria-live` on the
   walkthrough card; `aria-hidden` on the decorative spotlight. `aria-modal` is
   deliberately absent, because the page underneath really is interactive and
   claiming otherwise would be a lie to assistive technology.

8. **Feature-aware suggestions in the Coach tab**, from measured usage — *"all 6
   orders you've placed so far were market orders"*. Kept rigorously separate
   from the Trading Intelligence Engine's advice: **this layer recommends
   tutorials from feature usage and never recommends trading behaviour**, which
   is `intelligence/`'s job and is done there with a false-discovery correction
   underneath it. A test sweeps every rule and asserts the line holds.

**Two defects found while building it**, both by the new browser suite:

* **A hidden panel kept live buttons.** `renderRecs` set the recommendations
  panel to `display:none` when there was nothing to suggest but left the previous
  markup inside it — leaving clickable controls for advice that had been
  withdrawn, invisible to a user and very much not to a test.
* **The first step of the tour threw the page to the bottom.** Step 1 highlighted
  the PAPER TRADING badge, which is pinned to the foot of a full-height sidebar,
  and `scrollIntoView({block:"center"})` obeyed. Fixed twice over: the step now
  targets the sidebar itself, and the engine scrolls only when a target is *not
  visible at all* rather than merely off-centre. Caught by screenshot review, not
  by an assertion — the standing lesson in this repo about asserting what the
  user sees.

**Verified:** 1908 tests, 135/135 `guide_check`, 54/54 `intelligence_check`,
46/46 `marketdata_check`, all `chart_check` checks, `browser_check` +
`check_html_ids` + `check_docs` green, plus screenshot review of the welcome
screen, both tour styles, the help centre, the glossary, the guardrail and three
empty states.

## [Uncommitted] 2026-07-28 — V0.6.0: the Trading Intelligence Engine

*1468 → **1849 tests** (+381); a new **54-check** headless-browser suite
(`scripts/intelligence_check.py`, wired into `verify.ps1`) and a performance
benchmark (`scripts/intelligence_benchmark.py`). **No trading-behaviour change,
no new runtime dependency, no new tab, and identical shipped defaults** — the
engine is never consulted before a trade, and `risk/manager.py` remains the only
gate. Full design, rules and limitations: `docs/TRADING_INTELLIGENCE.md`.*

**The problem.** The app already knew a great deal about its trader, and knew it
in four unrelated places: `journal.db` (every closed round trip and its reasoning
chain), `experience.db` (the rich per-trade context — IV, delta, DTE, regime,
session, indicators), `data/coach/*.json` (the process review of each manual
trade) and `learning/weights.json` (which evidence types have paid off). Four
stores, four aggregation paths, and no answer at all to the questions a trader
actually asks — *what am I good at, what keeps costing me money, am I improving,
what should I learn next.* Worse, each new screen that wanted an answer computed
its own, which is the failure this codebase has already paid for twice
(`data/health.py` in V0.5.3, the settings ranking in V0.5.7): two objects
tracking one fact will drift, and the drift hides bugs.

**What was built.** One pipeline. `build_facts()` joins the three sources into a
`TradeFact` once; ten engines run over it; everything above — Dashboard, Coach,
Journal, Learning, reports — projects from a single `IntelligenceSnapshot`.

1. **`intelligence/facts.py` — the one join.** `TradeFact` is the normalised unit
   every engine reads. It never invents (a field the sources cannot supply stays
   `None`, because a fabricated `0.0` delta would quietly become a "lottery
   ticket" finding) and never raises (these sources include a user-editable JSON
   directory; unparseable records are skipped and counted). Process observations
   — was a stop placed, was it widened, was a target defined — are *read from the
   coach's `during` findings* rather than re-derived, so there is no second place
   the same fact is computed. `had_stop` is deliberately tri-state: `False` means
   "observed, and there was no stop"; `None` means "nobody reviewed this trade".

2. **`intelligence/performance.py` — the metric registry.** A flat
   `{key: Metric}` map of 38 metrics that is the addressable vocabulary of the
   whole layer: goals target metrics by key, scorecards cite them by key, the
   report writer looks them up by key, the UI renders them by key. `consistency`
   is measured over *periods* rather than individual trades (weekly totals, then
   daily, then per-trade), because per-trade option results vary enormously for
   everybody and scoring their spread would hand every trader the same ~20.

3. **`intelligence/behavior.py` — 22 detectors.** Revenge trading, overtrading,
   chasing, FOMO entries, averaging down, moving stops, trading without a stop,
   cutting winners short, letting losers run, inconsistent sizing, oversizing,
   opening-chop trading, entering before confirmation, counter-trend trading,
   theta neglect, IV neglect, lottery tickets, tilt after a loss, overconfidence
   after wins, and trading setups the analysis rated poor. Each cites the exact
   trades it counted, prices the habit as a historical counterfactual, and cannot
   be triggered by a single trade.

4. **`intelligence/patterns.py` — automatic edge discovery.** Nineteen declared
   dimensions (weekday, time-of-day block, symbol, direction, strategy,
   timeframe, setup grade, regime, trend, DTE, delta, IV, confidence, relative
   volume, ADX, hold time, position size vs. your usual, session, managed-by),
   every bucket measured against *that trader's own* baseline with a
   two-proportion test and a Benjamini–Hochberg false-discovery correction.

5. **`intelligence/confidence.py` — eight composite scores** (Execution Quality,
   Discipline, Risk Control, Consistency, Planning, Adaptability, Learning
   Progress, Decision Quality), each carrying its weighted components so it can
   explain itself down to the number that moved it.

6. **`goals.py`, `curriculum.py`, `recommend.py`, `timeline.py`,
   `achievements.py`, `reports.py`** — measurable goals against metric keys with
   computed progress; sixteen lessons each summoned only by a measured weakness;
   a ranked action list derived entirely from findings that already carry
   evidence; a dated improvement narrative; ten achievements none of which can be
   earned by one trade or by luck; and weekly/monthly coaching reports in prose.

7. **`engine.py` — the façade.** Nothing is computed at construction, so startup
   is unchanged. The cache is keyed on a fingerprint the orchestrator owns
   (`journal.revision:experience.revision`) rather than a TTL, because a TTL
   would either recompute needlessly or serve a stale verdict about a trade the
   user just closed. A failed analysis returns an empty snapshot, never an
   exception, and is never cached as though it were an answer.

**Layering.** `intelligence/` imports **`core` only** — it reads
journal/experience/coach records structurally rather than by import, which keeps
it *below* the coach in the dependency graph. That is what lets the AI Coach
become a presentation layer over this engine rather than a parallel analysis
path, and `tests/test_architecture.py` enforces it in both directions.

**Four defects found by attacking it**, each with a regression test:

- **A composite score of 100/100 grade A, earned by an absence of data.** A
  trader with no reviews scored Discipline A, because the one component needing
  no review (revenge trading, which reads only timestamps) came back clean and
  20% coverage was enough to average. Fixed with a coverage floor: below 35% of
  the intended inputs the score is `None`, not a flattering number under a caveat
  nobody reads.
- **Thirteen "patterns" out of 100 uniformly random trades.** ~70 bucket tests
  per run at a raw p≤0.20 threshold produces ~14 false positives by construction;
  the benchmark measured 13. Fixed with a Benjamini–Hochberg correction over the
  whole run.
- **A circular dimension.** Exit reason was a pattern dimension and produced the
  strongest finding in the system — *"how it ended — stop loss: 0% win rate over
  51 trades against 100% elsewhere, p<0.0001"* — which is a definition, not an
  edge, and generated the recommendation *"stop taking stop-loss trades"*. A
  dimension must describe a choice made before or during the trade, never a
  consequence of how it turned out.
- **`nan%` in the narrative.** Profit factor is legitimately infinite for a
  period with no losers, and `inf` vs `inf` yields NaN — both the timeline and
  the report writer shipped *"your profit factor has declined nan% since March"*.

**UI**, inside the existing tabs, with no new tab and no build step: Dashboard
gains the score cards, ranked action list, risk observations, goal progress,
achievements and improvement timeline; Coach gains measured behaviour (detected /
clean / **unassessable-with-reason**), discovered patterns and the coaching
reports; Journal gains finding badges and a lazily-loaded per-trade analysis;
Learning gains triggered lessons that each state why they appeared and which
statistic fired them. Explainability uses native `<details>`/`<summary>`, so
"Why?" works with no JS of our own and gets keyboard and screen-reader behaviour
for free.

**Performance** (measured): 50,000 trades analysed in 2.9 s, with per-trade cost
flat from 1k to 50k — the pipeline is sub-quadratic. Four cached reads cost
0.001% of one analysis.

## [Uncommitted] 2026-07-27 — V0.5.7: the Market Data Control Centre

*1257 → **1468 tests** (+211); a new **46-check** headless-browser suite
(`scripts/marketdata_check.py`, wired into `verify.ps1`). **No trading-behavior
change, no new runtime dependency, and identical shipped defaults** — with no
API key, no stored state and no `config.yaml` edit, the provider chain and its
behaviour are exactly V0.5.6's. Full design: `docs/MARKET_DATA.md` §29–41.*

**The problem.** V0.5.2–V0.5.6 built a market-data subsystem that is genuinely
production-grade — six providers, health-ranked failover, circuit breakers,
request budgeting, semantic validation, replay, diagnostics, self-healing — and
gave the person who owns it almost no way to see or steer any of it. Every
question a user actually has ("why isn't Finnhub being used?", "is my key
working?", "how many requests are left?", "what happens when Yahoo dies?",
"my cache looks wrong, now what?") was answered only by reading
`logs/data.log`, or by editing `config.yaml` and restarting. This milestone is
the entire user-facing management layer, built on top of that machinery without
redesigning any of it.

**What was built.**

1. **`data/control.py` — `MarketDataControl`.** The administration surface,
   composed *over* the registry and the service and never inside them, so the
   hot path did not slow down and `MarketDataService` did not grow a settings
   API. It reports the dashboard, applies credential and ordering changes to
   live adapters, runs connection tests and maintenance jobs, and generates
   recommendations. It deliberately never computes a ranking (it reports
   `registry.ranking()` verbatim, so the settings page and the chart cannot
   disagree) and never returns a plaintext key.

2. **`data/credentials.py` — API keys, pasteable and safe.** Keys are stored in
   their own owner-only `credentials.json` rather than in `settings.json`,
   because everything in `settings.json` is treated as ordinary user data
   (backed up, opened in Notepad, safe to share) and a secret needs the
   opposite defaults. Resolution is `environment → stored → config.yaml →
   missing`, implemented by having the store fill in the field
   `resolve_api_key` already consults *after* the environment — so the
   documented order needs no second implementation. **A plaintext key leaves
   the module only through `resolve()`**; every other accessor returns
   `••••••••abcd`, and `TestNoLeak` enumerates every payload this repo invites
   users to attach to a bug report and asserts the key is absent from all of
   them.

3. **Three ordering modes** (`static` / `hybrid` / `dynamic`), explained in the
   UI. `dynamic_ranking: true|false` turned out to be two questions wearing one
   coat: a user who sets their own order wants it respected, but not so rigidly
   that a dead provider keeps the head of the chain. **Hybrid is the full rank
   formula minus its latency term** — latency being the only term that reorders
   two *healthy* providers — so your order stands until something is genuinely
   failing. `dynamic_ranking: false` still wins and pins the chain to `static`,
   so nobody who turned ranking off is quietly overruled.

4. **A disabled provider is now CONSTRUCTED.** `enabled: false` used to skip
   construction entirely, which meant a switched-off provider could not be
   listed, could not explain itself, and could not be switched back on without
   editing a file. It is now treated exactly like one with a missing API key:
   present, self-explaining, never selected, and contributing **no history
   floor** to `deepest_earliest` (the V0.5.2 retry-forever bug class).

5. **A displayed health state, derived and never stored.**
   `monitor.status()` is a *gate* and has no way to say "in rotation, but
   struggling" — a provider failing one request in three is `ok` to it and
   alarming to a person. `health_state()` adds the human answer (`healthy`,
   `degraded`, `offline`, `disabled`, `missing_key`, `rate_limited`,
   `circuit_open`, `unavailable`, `unknown`) with a mandatory plain-English
   sentence beside it, derived from the same counters on every read.

6. **Test Connection, end to end.** A real SPY daily request through the same
   `fetch_history` a chart uses — transport, auth, parsing, normalization *and*
   semantic validation — with a closed vocabulary of outcomes, each carrying
   one sentence of explanation and one recommended action. A test that stopped
   at "the socket opened" would pass for a provider whose response format had
   changed, which is the failure the chart cannot route around; the suite
   drives exactly that case (weekly bars answering a daily probe).

7. **Eight maintenance actions** with live progress on a single background job
   slot: clear cache, rebuild cache, verify cache integrity, run validation,
   run replay, run benchmark, run diagnostics, re-measure capabilities. Each
   declares up front whether it spends upstream requests — on a
   25-per-day key, a user is entitled to know that before pressing the button.
   `CandleCache.verify()` is deliberately more than SQLite's
   `integrity_check`: a cache can be structurally perfect and still unusable,
   which is exactly what the V0.5.6 daily-bar defect was.

8. **Automatic recommendations.** Severity-ordered advice that always names a
   next action, not a condition — and the redundancy count is honest:
   `yahoo` and `yfinance` are collapsed into one family, because they are two
   code paths over one upstream and one IP rate limiter. A healthy multi-source
   install is told nothing at all.

9. **`data/faults.py` — QA mode.** Every failure this subsystem handles was
   documented, tested against canned payloads, and impossible to *watch*. A
   fault now fires inside `HistoryAdapter.fetch_history`, in the exact place a
   real transport failure occurs, raising the genuine `ProviderError` subclass
   — so the breaker, the ranking, the tier ladder, the diagnostics trace and
   the frontend state machine all behave identically to the real thing. Off in
   every shipped build: `market_data.qa_mode` defaults False and the endpoints
   **404** without it. The cache-corruption drill runs on a *copy*.

10. **The UI** — a new Settings ▸ Market data panel: one card per provider
    (state, explanation, feed labels, latency, success rate, quota meter, API
    key management, Test Connection, on/off, move up/down), a live 21-column
    dashboard, the failover summary, recommendations, maintenance tools with a
    progress bar and a Stop button, a plain-English explainer, and the QA panel
    when enabled. It auto-refreshes only while the tab is visible, and never
    over the top of a half-typed API key.

**Five defects found by attacking it, each with a regression test:**
`mask("   ")` returned eight dots for a key that did not exist; a repeated name
in a provider order assigned one provider three priorities and made `order()`
report a chain longer than the registry; a hand-edited `marketdata.json` with
`providers` as a LIST raised an `AttributeError` **out of the composition root**
(the app refusing to start because a preferences file was edited badly — exactly
what `apply_control_state` promises not to be); the busy-slot refusal did not
say what was busy; and a multi-minute capability probe could not be stopped
(cancellation is now cooperative, keeps what it measured, and reports
`cancelled` rather than `error`).

**Then the first LIVE provider certification found a sixth defect — the worst
of them.** Twelve Data and Alpha Vantage authenticated and served. Finnhub
returned **HTTP 403** to every request, with a brand-new, email-verified key
copied straight from its dashboard. The app said *"the API key was rejected"*,
so the key was regenerated — repeatedly, and it could never have helped.

**Finnhub has moved `/stock/candle` to its paid tiers.** Measured live rather
than inferred (its docs site is a JS app and cannot be read by a fetcher):

    /stock/candle + invalid key      401  {"error":"Invalid API key."}
    /stock/candle + no key           401  {"error":"Please use an API key."}
    /stock/candle + valid free key   403  {"error":"You don't have access…"}

**401 is the only status Finnhub uses for a key problem, so a 403 is positive
evidence the key is good.** The bug was one line —
`http_adapter._from_status` mapped `code in (401, 403)` to a single
`ProviderAuthError` — plus a `_AUTH_MARKERS` list broad enough (`"api key"`,
`"don't have access"`) to misclassify the 200-body path too.

Fixed by splitting the two everywhere they are distinguished:
`ProviderEntitlementError` (deliberately **not** a subclass of
`ProviderAuthError`, so an `except` cannot re-merge them), `KIND_ENTITLEMENT`,
`STATUS_PREMIUM_REQUIRED`, `HEALTH_PREMIUM_REQUIRED`, and a recommendation that
says *do not regenerate the key*. `FinnhubAdapter.verify_credentials()` proves
the key on the free `/quote` endpoint, turning a strong inference into a
demonstrated fact — quote 200 + candle 403 means key good, plan too small.
Generalised as `HistoryAdapter.can_verify_credentials`.

Two consequences beyond the message. `registry.deepest_earliest` now excludes
`monitor.permanently_unusable` (disabled ∪ auth-failed ∪ entitlement-failed)
rather than `disabled_reason` alone — Finnhub declares 180 days of 5-minute
history and on a free plan serves none of it, so counting that floor told the
chart history reached three times further than anything reachable, which is the
retry-forever class V0.5.2 exists to prevent; a *rejected key* had the same
defect and is fixed by the same change. And `adapter.free_tier_serves_history`
(a measured fact, held to the same standard as `capabilities.py`) stops the app
recommending Finnhub as the free provider to add, and warns on its card *before*
a user spends ten minutes registering.

**Authentication is not weakened.** 401, `"Invalid API key."` and `"Please use
an API key."` all still produce `ProviderAuthError` and still bench the provider
stickily — verified against the live API, not only canned payloads. If the free
credential check also fails, the auth failure that can be *proven* is what gets
reported. Regression coverage: `TestFinnhubEntitlement` (19 tests) plus the
401/403 split across all three keyed adapters.

**Verified:** **1468 tests**, 46/46 `marketdata_check` in a real browser, 65/65
`chart_check`, 88/88 stress scenarios, `browser_check` + `check_html_ids` +
`check_docs` green, a 40-assertion adversarial audit over key handling,
ordering, quota accounting, failover, maintenance, export redaction and
configuration persistence, and live probes against the real Finnhub API for both
the 401 and 403 paths.

**Unchanged, and now WORSE, as the biggest limitation:** with no API key there
is still exactly one real source — and Finnhub, previously the recommended free
route to an independent one, can no longer serve history on a free plan.
**Twelve Data (800/day) is now the only free keyed provider that delivers a
genuinely independent intraday source**, with Alpha Vantage's 25/day a distant
second. The control centre makes all of this visible and makes fixing it a
thirty-second job — but visibility is not redundancy.

## [Uncommitted] 2026-07-27 — V0.5.6: the 1D validation wall and viewport corruption

*1238 → **1257 tests** (+19); `chart_check` 48 → **65 checks**; a new 110-cell
browser matrix (10 symbols × 11 timeframes). **No new features, no version bump,
no trading-behavior change.** Two reproducible bugs reported against the V0.5.5
build, both root-caused from the real `cache.db` rather than guessed. Full
report: `docs/CHART_CERTIFICATION.md` Part II.*

**1. Every symbol on 1D was stuck behind "the cached bars failed validation and
were discarded" — two defects stacked.**

*The data was genuinely wrong.* A daily bar's identity is its session date, and
a date only becomes an instant relative to a timezone — so each adapter used
whatever its upstream emitted: Yahoo the 09:30 ET session open (`13:30 UTC`),
yfinance exchange midnight (`04:00 UTC`), Stooq and the keyed HTTP providers
`00:00 UTC`. The cache is keyed `(symbol, timeframe, ts)`, so those are three
rows for one trading day. Measured: **SPY held 6,517 daily rows for ~3,258
trading days**, giving a tightest spacing of 0.40 intervals.
`quality.validate_history` then correctly reported "bar spacing does not match
1d — wrong interval served". Validation was working; the data was not. Only 1D
was affected because intraday timestamps are unambiguous epochs.

*Recovery never completed because validation ran too late.* `_settle()` is the
last step of a request, so when the disk tier's frame failed there the ladder
had already passed the provider tiers — and the offending rows stayed on disk.
The providers were never consulted, the next request re-read the same rows, and
**Retry did exactly the same thing forever**. There was no way past it short of
deleting `cache.db` by hand.

Three fixes, at the three mechanisms: `base.session_index()` establishes **one**
convention (00:00 in the exchange's timezone) enforced in
`HistoryAdapter.fetch_history`, with the two date-only sources now localizing
the provider's *date* rather than stamping UTC midnight — which also fixes a
latent off-by-one, since the chart labels every timestamp through an ET
formatter where 00:00 UTC reads as 19:00 the previous day. `cache._migration_3`
repairs installs already poisoned, rewriting rather than deleting so decades of
end-of-day history survive (17,957 daily+ rows collapsed to 11,831 in 0.20s;
intraday untouched). And the disk tiers now validate **before** committing, with
`_quarantine` purging the bad rows so the ladder falls through to the providers
— verified against a cache re-poisoned after migration: one request, `outcome=
live`, 205 bars, no user action. `health()["cache"]["quarantines"]` counts it.

**2. Viewport / zoom corruption.** "One owner" (V3.2.2) said where a viewport
move comes from, never what it may leave on screen. So `chScrollToLatest`
carried the previous view's width onto a **new symbol** (the reported "switching
symbols keeps a strange zoom level"), `chApplyFocal` mapped a date window onto a
coarser resolution and ratcheted narrower on every step of 5m→15m→1h→1d, and a
resize re-derived bar spacing with no floor — measured at **4 bars of 281
visible, logical width 2.3**. Six invariants (V1–V6) are now enforced in
`chClampViewport`, called from `chMoveViewport`, with `CH_MIN_VISIBLE_BARS` as
the single floor constant that `chApplyFocal` also references instead of keeping
its own. They bind programmatic moves only — a user's wheel-zoom is never
clamped, because deliberately zooming to three candles is a legitimate thing to
want. `CH.restoringViewport` also became a **depth counter**: guarded moves
overlap, and a boolean let the first `finally` clear the guard while another
move was in flight, so the next range change was read as a user pan and fired a
spurious history fetch.

**Deliberately not done: the resize path does not re-clamp.** It was implemented
and reverted. Dragging the price axis changes the width of its own labels, so
the canvas resizes a few pixels mid-gesture, and clamping there re-invalidated
the chart and snapped the user's manual price scale back — caught directly by
`chart_check`'s "overlay tracks a vertical price-axis drag" (the level moved 4px
instead of 140). The narrow view a resize can leave is self-correcting on the
next real viewport move; manual price scaling is not worth trading for it. This
is the one viewport violation the harness still reports, and it is intentional.

**Verification.** 1257 tests; `chart_check` 65/65 with zero console errors; a
**110/110** browser matrix over SPY/QQQ/IWM/AAPL/MSFT/AMD/NVDA/META/JPM/AVGO ×
1m…1mo, run against a copy of the real cache forced back to schema v2 so the
migration executed, asserting per cell a terminal state, no validation screen,
candles inside the visible price band, a usable bar count and indicators 1:1
with candles; 88/88 stress; `browser_check`, `check_html_ids`, `check_docs`
green. Running `chart_check` with `index.html` reverted produced exactly the
four price-scale failures and nothing else, confirming each check fails for its
own reason.

**Not implemented, and tracked in `docs/TODO.md` rather than implied:** the
Settings ▸ Market Data Providers panel for pasting API keys (the backend half —
environment-first resolution, per-provider disable, default redaction — already
exists and is unchanged; configuring a key today means an environment variable
or `config.yaml`), the extra provider-health dashboard columns, enforcement of
cross-provider disagreement, and permanent coverage for the history-loading
stress matrix.

## [Uncommitted] 2026-07-27 — V0.5.5: chart production certification

*1232 → **1238 tests** (+6); `scripts/chart_check.py` 42 → **48 checks**. A
failure-elimination pass over the whole chart pipeline, provider to pixel. **No
new features, no version bump, no trading-behavior change.** Ten defects were
found by reproducing them, and every one of them was a way the chart could fail
while every existing test, the diagnostics dashboard and the backend all
reported success.*

**The pattern behind all of them.** Every check this repo had asked whether the
DATA arrived. None asked whether the user could SEE it. That gap is the whole
milestone: three of the nine defects rendered a healthy, correct, fully-cached
payload as a blank canvas with `data-ch-state="complete"`.

**1. The price axis had no owner — the reported bug.** The time axis has had a
single owner (`chMoveViewport`) since V3.2.2; the price axis had none.
lightweight-charts turns `autoScale` **off permanently** the first time the
user drags the right-hand price axis, and nothing in the app ever turned it
back on — not a symbol switch, not a timeframe switch, not Reset view, not
Latest. The pinned band then outlived every subsequent load, so a $290 ETF
drawn on a band left over from a $750 one put its candles entirely off-screen
while the volume histogram (which lives on its own `vol` scale) kept painting.
That is exactly the "QQQ loads, SPY loads partially, IWM shows only volume,
diagnostics say everything is healthy" report, and it explains why restarting
fixed it: `autoScale` is not persisted. Reproduced end to end in a headless
browser: one drag, then SPY/QQQ/IWM at 1d/5m all measured `OFF SCREEN` on an
identical pinned band. Ownership now mirrors the time axis — a genuine
symbol/timeframe switch resets to autoscale, Reset view and Latest reset it, a
same-key refresh or history prepend **preserves** it (a manual scale is a
deliberate act), and `chEnsurePriceVisible` is a last-resort net that restores
autoscale if the on-screen candles ever end up with zero overlap with the
visible band.

**2. One stub bar condemned every 1-hour chart.** `quality._interval_stats`
judged interval conformance on the strict TIGHTEST gap. Yahoo closes each US
session with a **30-minute stub bar** (15:30 → 16:00 ET, the closing auction),
so a perfect 1h frame contains exactly one 0.5-interval gap. Measured against
the user's real `cache.db`: IWM 60m, 2,180 bars, 1,862 gaps of exactly 1.0 and
**one** of 0.5 — and that one bar set `usable = False`, scored the frame 0 and
charged the provider a validation failure, on every 1h request that included
the last completed session. Conformance is now judged on the **median of the
within-session gaps**, which still rejects 30m-served-as-1h (median 0.5), 90m
(1.5) and daily-served-as-1m, because a genuinely wrong interval is wrong in
the bulk of its spacings, not in one bar. `min_gap` is still reported; it is no
longer a veto.

**3. A NaT timestamp 500'd the whole endpoint.** `pd.to_datetime(...,
errors="coerce")` in the HTTP adapters turns any malformed provider timestamp
into `NaT`, and `http_adapter.localize` maps a DST fall-back ambiguity to `NaT`
by design. Neither was ever dropped, so one unparseable bar reached
`int(ts.timestamp())` at the end of `/api/candles` and took the entire chart
down with a 500. `validate_candles` — the existing single choke point for
exactly this job — now drops them.

**4–9. Six display-layer defects**, each found by injecting the payload and
watching the real renderer:

- **Null/NaN OHLC is not an error to lightweight-charts — it is *whitespace*.**
  `setData` accepts it, the series draws nothing, the price scale ignores it,
  and the render reports success. `chEnsureMonotonic` only ever checked bar
  *times*; it now drops undrawable bars the way the backend already does, and
  a payload that sanitizes down to nothing is an explicit failure state instead
  of a blank canvas reporting `complete`.
- **An out-of-order payload silently collapsed to ONE candle.** The old scan
  kept only bars strictly newer than the previous *kept* one, so a fully
  reversed response rendered a single bar and reported success. Out-of-order
  bars are now **sorted**; only genuine duplicates are discarded.
- **`high < low` bars** (reachable through a legacy provider, which only sees
  the shape-level `validate_candles`) drew as nothing. The display layer now
  asserts the same geometric invariant `quality.validate_history` does.
- **A malformed indicator killed the candles.** An indicator array longer than
  `candles`, or `indicators: null`, threw from inside `chRenderData`'s try and
  wiped the whole chart to the red error overlay — a broken accessory taking
  down the primary content. Indicator reads now go through `chInds()` and are
  bounded by both lengths.
- **A string indicator value** raised an uncaught `v.toFixed is not a function`
  out of the crosshair handler, which no try/catch in the render path covers.
- **A render failure reported `complete`.** `loadChart` stamped the terminal
  state unconditionally after `chRenderData`, overwriting the `failed` state a
  renderer exception had just set — so the state machine, which the regression
  suite and a human reading `data-ch-state` both trust, lied about a visibly
  broken chart. The verdict is now honoured, and the renderer-exception path
  sets a terminal state of its own.

**10. The regression suite was not isolated.** Found while re-running it:
`chart_check.py` and `browser_check.py` launch the app with `cwd=scratch`,
which stopped isolating anything in **V0.4.4**, when the storage root moved off
the CWD to `%LOCALAPPDATA%\OptionsPilot`. Every run since has read and *written*
the user's real `cache.db`, journal and logs — against both files' own
docstrings and against `CLAUDE.md`'s rule on the runtime data root. It showed
up as an intermittent 30-second timeout on the first chart load (SQLite lock
contention with the user's running copy of the app) and as a suite whose
outcome depended on what an earlier run left cached. Both now pass
`OPTIONSPILOT_HOME` into the server subprocess.

**Verification.** `scripts/chart_check.py` gained **six** checks built around
the invariant the file was missing — *the candles in the visible time window
must intersect the visible price window* — including the reported bug in the
form a user hits it (drag the axis, switch symbol) and its converse (a
deliberate manual scale must survive a same-key refresh). Two throwaway
adversarial harnesses drove **41 hostile scenarios** through the real renderer
(type confusion, duplicate/unsorted/future/flat/1e9/sub-penny bars, 200k-bar
payloads, invalid JSON, 500/502, every `outcome` value, 24-symbol bursts,
timeframe flip-flops, a symbol switch during an in-flight history load, corrupt
`localStorage`, shrinking payloads, hostile tickers); all 41 pass, and each of
the nine defects above was demonstrated failing before the fix and passing
after. Full suite 1238 green, stress 88/88, `browser_check` and `check_html_ids`
green.

**Two limitations this pass could not fix, now documented rather than
implied:**

- **Stooq is permanently unusable.** It now answers every request with a
  JavaScript proof-of-work challenge page (`"This site requires JavaScript to
  verify your browser"`), which a `urllib` client cannot satisfy and which this
  project will not try to circumvent. The adapter detects and refuses it
  correctly — but with no API keys configured, that leaves **Yahoo as the only
  real source**, reached by two independent code paths (`yahoo` and
  `yfinance`) that share one upstream and therefore one failure domain. A free
  Finnhub or Twelve Data key is the only way to get genuine provider
  independence today.
- **Yahoo rate-limits by IP.** A 429 was observed from a clean client during
  this pass. It is handled (typed error, breaker, failover), but with Stooq
  gone there is nothing keyless to fail over *to*.

## [Uncommitted] 2026-07-26 — V0.5.4: enterprise provider expansion

*1052 → **1232 tests** (+180); market-data stress 65 → **88 scenarios**. Three
new providers — Finnhub, Twelve Data, Alpha Vantage — plus the credential
handling and request budgeting they need. **No version bump, no
trading-behavior change, and with no API keys configured the app behaves
exactly as it did in V0.5.3** — which is the shipped default. Full design:
`docs/MARKET_DATA.md` §23–27.*

**The point of the milestone.** V0.5.3 claimed adding a provider was one file
and one registry entry. This spent that claim three times to find out whether
it was true. It mostly was: each adapter is ~150 lines implementing exactly
four things (`_build_url`, `_translate`, `_parse`, `_probe`), and health
monitoring, circuit breaking, ranking, configuration, replay, benchmarking,
diagnostics and capability discovery all followed with no per-provider code.

**1. Three providers** (`data/{finnhub,twelvedata,alphavantage}_provider.py`),
behind the keyless chain at priorities 40/50/60. Keyed providers sit *behind*
Yahoo/yfinance/Stooq because a keyless request costs nothing while a keyed one
spends an allowance that cannot be bought back until tomorrow; their value is
being **genuinely independent of Yahoo**, which makes a Yahoo-wide outage
survivable at *intraday* resolution for the first time (Stooq is daily-only).
Among themselves they are ordered by how much budget they have to spend.

**2. A shared base for keyed HTTP providers** (`data/http_adapter.py`).
Writing the same 80 lines of `urllib` plumbing, status mapping and JSON
decoding three times would have been exactly the duplication to avoid. Yahoo
and Stooq were deliberately **not** retrofitted onto it — their transports do
multi-host failover and HTML-challenge detection, which are the reason those
adapters are reliable rather than boilerplate.

**3. The timezone contract.** Two of the three providers send **naive local
time in the exchange's timezone**. Reading those as UTC shifts every intraday
bar by 4–5 hours, and by a *different* amount across a DST boundary — not a
visibly broken chart but a subtly wrong one that also poisons the shared cache,
since bars are keyed by timestamp. `http_adapter.localize` handles it in one
place: intraday converts from the named zone, daily and coarser stamp at
**00:00 UTC** to match Yahoo and Stooq (a second convention would write a
duplicate row for every day already cached).

**4. Credentials** (`data/config.py`). A missing key is a **quiet, explained
absence, never a crash**: the adapter is still constructed and still appears in
diagnostics reporting `missing_api_key` with a signup link, but is never
selected. Keys resolve environment-first (`FINNHUB_API_KEY` and friends need no
configuration at all), and a whitespace-only value counts as absent. **Keys are
redacted by default** in `as_dict()` — that payload reaches the diagnostics
endpoint, the JSON export and the text report, all of which users are invited
to attach to public bug reports, so a leak now requires opting in rather than
every future caller remembering.

**5. Request budgeting** (`data/ratelimit.py`). Alpha Vantage's free tier is
**25 requests per day**; a symbol-switching session spends that in under a
minute and reacting to the error afterwards is far too late. Budgets are
enforced *before* the request, use a real sliding minute window (a fixed bucket
lets 2× through across a boundary), count before the call (a failed request
still consumed quota upstream), and **persist to `<data>/quota.json`** so
restarting cannot mint an allowance the plan never granted. Load is distributed
between providers by feeding budget *pressure* into the existing ranking rather
than by adding a scheduler — a provider allowed 5 requests/day served only 3 of
30 requests before pressure moved the rest elsewhere.

**6. A status vocabulary** (`health.STATUS_*`) shared by the monitor, the API,
the text export and the dashboard: `ok` / `disabled` / `missing_api_key` /
`auth_failure` / `quota_exceeded` / `rate_limited` /
`temporarily_unavailable`, checked in order of permanence so the reason
reported is the one the user would act on. An auth failure is **sticky** —
re-testing a rejected key on every chart load spends requests to learn
something already known.

**Two pre-existing defects this surfaced and fixed:**

- **`deepest_earliest` counted providers that could never answer.** A keyless
  Finnhub declares 180 days of 5-minute history, so the chart would have been
  told history reached back 180 days when only Yahoo's 59 were reachable — then
  scrolled into a window nothing could serve and retried forever, reviving the
  exact bug class V0.5.2 eliminated. *Permanently* unusable providers now
  contribute no floor; *temporarily* unavailable ones still do, so the reported
  start of history does not lurch about as breakers open and close.
- **The stale tier could report `stale` with zero bars**, because `_settle`
  trimmed every frame to the requested window — including the last-resort one
  whose bars are by definition older than the request. The UI showed a banner
  promising saved bars with nothing behind it.

**Verification.** 1232 tests, all offline (no key, no network, no sockets);
market-data stress 88/88 including keyless-chain, quota-exhaustion,
auth-failure and budget-concurrency scenarios; `chart_check.py` 52/52 in a real
browser, now driving a six-provider replay. A separate self-audit probed for
deadlocks (0 across 7 contending threads on the monitor/quota lock pair),
ranking oscillation (0 order changes over 200 identical cycles), provider
starvation, infinite retry (1 upstream call across 25 chart loads after an auth
failure) and cache poisoning.

## [Uncommitted] 2026-07-26 — V0.5.3: market-data production readiness

*880 → **1052 tests** (+172); market-data stress 41 → **65 scenarios**;
`chart_check.py` 49 → **52**. No version bump, no new provider, no
trading-behavior change. V0.5.2 built the subsystem; this milestone makes it
**operable** — observable, rankable, configurable, and cheap to extend. Full
design: `docs/MARKET_DATA.md` §13–22.*

**The theme.** V0.5.2 could serve data correctly but could not tell you *why* it
had. Health lived in two objects, ordering was a hard-coded constant, every knob
required a source edit, and diagnosing a user's chart complaint meant reading
their logs. Nothing here changes what is traded or what the shipped chain
answers — a cold system produces byte-identical behaviour to V0.5.2.

**1. One owner for provider health** (`data/health.py`, new). A provider's state
used to live in `adapter.ProviderHealth` (counters) *and* `registry._Breaker`
(rotation) — one invariant, two owners, with the breaker's trip condition being
a read of the adapter's counter. `ProviderHealthMonitor` now owns counters,
latency (EWMA + a real p95 over a 100-sample window), the rate-limit window, the
circuit breaker, per-day totals and the ranking score together. The policy "a
range error is not an outage" now exists once, in `COUNTS_AGAINST_HEALTH`,
rather than being re-derived in three files.

That consolidation immediately exposed **two real accounting bugs**, both
present since V0.5.2 and both invisible without a single owner:

- **A provider serving consistently-unusable bars never tripped its breaker.**
  The adapter recorded a *success* as soon as the transport parsed, and the
  service's validation reject was counted nowhere — so a source answering
  promptly with garbage kept its place at the head of the chain indefinitely.
  Fixed with `demote_last_success`, which *moves* the counters rather than
  adding a second request (recording a fresh failure would have inflated
  `requests` and halved every provider's apparent failure rate).
- **A demotion could only ever reach a failure streak of 1**, because recording
  the success had already zeroed the streak — so a provider failing *every*
  request oscillated 0 → 1 → 0 → 1 and never reached the threshold.

**2. Dynamic provider ranking** (`registry.candidates`). Ordering was
`provider_priority`, a constant. It is now `monitor.rank()`: priority as the
anchor, moved by measured latency, recent failure rate, consecutive failures,
breaker history and data quality. The scale is deliberate — providers are spaced
10 apart and **10 rank points is one second of latency** — so Yahoo at 180ms
still beats Stooq at 320ms, while Yahoo degraded to 2.4s loses to Stooq at
260ms, exactly as intended. **With no traffic recorded every rank equals its
priority**, so a cold system reproduces V0.5.2's order precisely; that is what
makes it safe to ship, and `dynamic_ranking: false` pins it permanently. The
rank's failure rate is measured over a **moving 50-attempt window**, not
lifetime: a lifetime rate never decays, so five failures in a two-minute outage
would demote a provider for thousands of requests after it had recovered.

**3. Diagnostics dashboard + export** (Help ▸ Diagnostics). A read-only page over
the existing endpoint showing every provider's status, rank, latency (avg/p95),
success rate, requests today, timeouts, validation failures, rate limits,
breaker trips, quality, last error and served intervals — plus session
aggregates, cache intelligence and the recent-request table with each request's
provider chain. It computes nothing (every number comes from the payload the
exports carry, so a screenshot and an export cannot disagree) and never polls (a
diagnostics screen fetching on a timer becomes traffic inside the traces it
displays). `GET …/export?format=text|json` downloads it as a dated attachment;
the text rendering (`data/report.py`) is built to be safe to paste into a public
issue tracker — no stack traces, no paths, no credentials.

**4. Replay + provider comparison** (`data/replay.py`). Clicking any request on
the dashboard re-runs it and asks **every** provider directly, reporting bars,
latency, quality and disagreement against the first that answered. It
deliberately bypasses the memo, cache, failover and breaker, because "what does
each source actually say?" is a different question from "what does the ladder
return". There is deliberately **no new recorder** — every request is already
recorded once in `diagnostics.RequestTrace`, so replay takes a trace id.

**5. Configuration without code changes** (`data/config.py` + `market_data:` in
`config.yaml`). Per provider: enabled, priority, timeout, retries, backoff,
throttle, breaker thresholds, minimum quality. Globally: ranking on/off, memo
cap, structured logging, cache policy and retention. Previously a provider's
timeout was in its adapter, its retry count a constant in `service.py`, its
breaker thresholds constants in `registry.py`, and its ordering a class
attribute. Unknown keys are a startup error (a silently-ignored `timout: 30`
would do nothing for the life of the install); unknown *providers* are accepted,
so a config can pin a future provider's settings before its adapter ships.
Because `data/` may not import `config/`, the runtime shape is dataclasses and
the pydantic mirror is translated in `orchestrator.py` — with two tests
asserting the key sets line up exactly.

**6. Cache intelligence** (`cache.CacheMetrics`). "Is the cache working?" used to
be answerable only as "there are N bars in it". Now: reads, hits, misses, hit
rate, stale reads, writes, evictions, rebuilds, errors, average age of served
bars, the span actually held, and `provider_requests_saved` — every hit is one
upstream request that did not happen. Optional `retention_days` pruning bounds
the file; off by default, because the deeper the cache the better the last tier
before a blank chart.

**7. Structured logging.** One `key=value` line per request carrying request id,
symbol, timeframe, outcome, provider, bars, duration, cache/memo hit, retries,
fallbacks, the full provider chain, quality and failure reason. Greppable rather
than JSON on purpose — `logs/data.log` is read by a human looking at a bug
report. Failures log at WARNING and successes at DEBUG, but **both carry the
same line**, so raising the log level to chase an intermittent problem gives the
same fields as the dashboard.

**8. Capability discovery** (`data/discovery.py`) + a benchmark
(`scripts/marketdata_benchmark.py`). Discovery measures a provider's real depth
(ladder walk then binary search — a dozen requests per interval, not hundreds),
persists it to a JSON store and refreshes on a cadence; `marketdata_probe.py`
now calls into it, so the app and the script cannot disagree about how depth is
measured. It is **advisory and off by default**: it does not rewrite
`capabilities.py`, whose numbers sit one day *inside* each measured cliff on
purpose. `drift()` reports one-directionally — a conservative table costs a
little depth, an over-promising one produces guaranteed-422s on every scroll.
The benchmark ranks providers on latency, throughput, quality, disagreement, CPU
and memory, and reports the health rank each would actually receive.

**Adding a provider is now one file and one registry entry, with the entire
operational half free** — dashboard row, breaker, configuration section, replay
participation, benchmark column and ranking, all inherited. See
`docs/MARKET_DATA.md` §21 for the checklist.

**Verification.** 1052 tests (all offline, no network, no flakes); market-data
stress 65/65 scenarios; `chart_check.py` 52/52 in a real headless browser —
including three new checks that open Help ▸ Diagnostics, drive a replay and
assert the chart survives, since the dashboard is new frontend and the frontend
is this project's thinnest coverage. `browser_check.py`, `check_html_ids.py`,
`check_docs.py` and `pip check` all green.

## [Uncommitted] 2026-07-26 — V0.5.2: the market-data subsystem

*651 → **880 tests** (+229). No version bump, no trading-behavior change. The
last inconsistent subsystem — chart history — replaced with a multi-provider
architecture. Full design, measurements and provider survey:
`docs/MARKET_DATA.md`; 84 manual checks: `docs/QA_MARKET_DATA.md`.*

**The root cause.** Every chart-history bug in this project's history shares one
ancestor: the old stack could not distinguish *"there is no data"* from *"I
can't reach the data"* from *"this data cannot exist"*. `yfinance` returns an
empty DataFrame for all three, `CachedProvider` memoized that empty frame, and
the frontend guessed. Two concrete failures were reproduced from evidence rather
than inferred:

1. **The depth window was measured from the wrong point.** Yahoo's intraday
   limits run from *now* — its own 422 body says "must be within the last 60
   days" — but `_clamp_history_window` measured from the *request's end*. A
   scroll-back for 5-minute bars starting 62 days ago but ending 31 days ago
   sailed through unclamped, 422'd upstream, and returned empty; the frontend
   correctly refused to treat that as exhaustion and retried on every subsequent
   scroll, forever, at three guaranteed-422 requests each. Visible verbatim in
   `logs/data.log` with the clamp reporting no change.
2. **A history-paging request poisoned the live-window memo.** The memo is keyed
   `(symbol, timeframe, session)` — it must be, or the live poll would never hit
   it — but nothing stopped a past-ending window writing to that key. The next
   live load found a "valid" entry, sliced it to the live window, and rendered
   the overlap. Caught by `chart_check.py`: **QQQ 1d returned a single candle
   from nine months earlier**, `outcome: memo`, no error anywhere.

Three more were found and fixed along the way: the shipped depth limits were one
day *past* Yahoo's real cliff for 5m/15m/30m/1h; a corrupt `cache.db` crashed the
app during `Orchestrator` construction (the connection raised before the recovery
block, and leaked its Windows file handle so the file could not even be moved);
and a history prepend restored a viewport captured mid-drag, yanking on-screen
bars — invisible while the backend was slow, reproducible once it was fast.

**New architecture** (all inside `optionspilot/data/`, so the layering and the
`MarketDataProvider` contract are unchanged — the engine, risk, broker and
backtester see exactly what they always did):

- **`capabilities.py`** — each provider declares, per interval, what it can serve
  and how far back, *measured from now*. An impossible request is answered from
  this table for zero network cost. The values are measured, not assumed:
  `scripts/marketdata_probe.py` walks each interval back until Yahoo 422s and
  compares the result against the shipped table (1m: 8d, 2m–30m: 59d, 1h: 729d,
  daily+: unlimited back to 1993).
- **`adapter.py`** — `HistoryAdapter`, one shape for every source. The base class
  supplies interval mapping, resampling, canonical normalization, window
  clamping, throttling, health and quality scoring, so a concrete adapter is
  transport + parser only. **Adapters raise instead of returning empty frames**,
  with typed failures (`ProviderRangeError` / `RateLimited` / `SymbolError` /
  `Unavailable`) that drive retry-vs-failover — the rule that ends the ambiguity
  above. Adding a provider is one file plus one registry entry.
- **Three adapters.** `YahooChartAdapter` (priority 10) talks to Yahoo's
  `v8/finance/chart` JSON directly over `urllib` — faster than yfinance, no
  hidden global throttle, and *it reports why it refused*, which is why it is now
  the primary. `YFinanceAdapter` (20) reaches the same data by a completely
  independent code path, so the two fail for independent reasons.
  `StooqAdapter` (30) is the only source not dependent on Yahoo at all
  (daily/weekly/monthly, decades deep); it refuses HTML anti-bot pages rather
  than parsing them as prices. `LegacyProviderAdapter` folds any plain
  `MarketDataProvider` (test fakes, backtest fixtures) into the same ladder.
- **`registry.py`** — ordering, eligibility checks *before* the network, and
  per-provider circuit breakers with exponential cooldown and half-open
  recovery, so one dead provider stops adding its timeout to every chart load
  and comes back by itself without a restart.
- **`service.py`** — `MarketDataService` and its tier ladder: memo → disk cache →
  providers (retry, then fail over) → half-open probes → knowingly-stale disk →
  an explicit, explained failure. This is the one place the four conditions are
  told apart: **`exhausted`** (older than any provider serves — stop asking),
  **`empty`** (a holiday; legitimate, not an error), **`stale`**, **`failed`**.
- **`quality.py`** — semantic validation returning a report, not just a cleaned
  frame: OHLC consistency, ordering, duplicates, future timestamps, non-finite
  values, isolated bad prints, interval conformance. Gaps are recorded with *no*
  penalty (a 4h US-equity chart legitimately has 20-hour overnight gaps —
  judging conformance on a share-of-bars-on-grid basis rejected perfectly good
  data, so it is judged on the tightest spacing instead).
- **`diagnostics.py` + `GET /api/diagnostics/marketdata`** — one trace per
  request (every provider tried, why each was skipped or failed, which tier
  answered, what validation found, timings) in a bounded ring, plus provider
  health and cache stats. Every `/api/candles` response carries its `trace_id`,
  so a screenshot of a wrong chart maps to an exact trace. **A chart complaint
  is now answerable from one JSON response, without reproducing it.**
- **`cache.py` rebuilt as durable storage**, not a disposable cache — it is the
  last tier before a blank chart. Atomic writes, `PRAGMA quick_check` on open,
  corruption quarantined to `cache.db.corrupt-<ts>` and rebuilt (a damaged cache
  degrades to a *cold* cache, never a crash), runtime self-healing, versioned
  migrations (an existing v1 `cache.db` opens and keeps every row), per-bar
  provider attribution, and validation on read.

**Frontend** (`ui/static/index.html`): an explicit load state machine
(`idle → loading → receiving → rendering → complete | cached | empty |
exhausted | failed`), mirrored onto `#ch-main` as `data-ch-state` so a stuck
spinner, a silent timeout and a blank canvas are distinguishable from the
outside. Reaching the start of history now shows **"◄ Start of available history
· 5m data starts May 28, 2026"** and stops requesting; `empty` and `exhausted`
no longer raise the red error overlay.

**Safety unchanged and re-asserted.** `get_candles` still never returns stale
data — the engine's fail-closed rule (no data ⇒ skip the symbol) is covered by
its own tests, and stale bars remain available only behind `get_history()` /
`allow_stale=True`, which display surfaces use and the trading path does not.

**Verification.** 880 tests (250 of them market-data, all offline against
scripted providers); `scripts/marketdata_stress.py` — 41 offline torture
scenarios (concurrency, rapid switching, hostile providers, corrupt cache,
malformed data, memory, thread safety) now part of `verify.ps1`, plus 6 live
ones behind `--live`; `scripts/chart_check.py` grown to 49 real-browser checks,
**and now passing end to end** — it had been dying at check 12 on `main`, which
is how root cause #2 was found. Measured: 24 concurrent live chart loads in
**0.5s with zero blanks**, against 10–15s under yfinance's global throttle.

## [Uncommitted] 2026-07-26 — V0.5.1: updater smoke-test release

*Version 0.5.0 → 0.5.1. A deliberately tiny, cosmetic change to exercise the
V0.5.0 auto-updater end to end: Help ▸ About's toast now reads "Hello from
v0.5.1!" instead of the version/paper-trading blurb. No other behavior
changed.*

## [Uncommitted] 2026-07-26 — V0.5.0: Auto-Updater 1.0

*Version 0.4.6 → 0.5.0. 546 → 651 tests (+105). Makes the installed app
self-updating from GitHub Releases, like Discord/VS Code/Spotify. **No trading
behavior changed; user data is never touched by an update.** Full details in
`docs/AUTO_UPDATER.md`.*

**New subpackage `optionspilot/update/`.** A self-contained, layered updater that
depends only on `core` (paths, `migration.create_backup`, logging) and the
standard library — networking is `urllib`, so there is **no new runtime
dependency**. Every layer takes an injected transport/collaborator, so the whole
thing is verified fully offline with fakes (`tests/update_helpers.py`): no
sockets, no real installer runs. Layers: `version.py` (semantic-version parsing
and **correct, non-lexical ordering** — `0.4.9 < 0.4.10 < 0.5.0 < 1.0.0`,
prereleases below releases); `transport.py` (the only networking — conservative
timeouts, bounded retries with exponential backoff on transient failures, offline
tolerance, proxy support, descriptive User-Agent); `github_api.py` (GitHub
Releases → `ReleaseInfo`, selecting **only** the `OptionsPilot-Setup-vX.Y.Z.exe`
asset — source zips and look-alikes are ignored by construction); `checker.py`
(is-there-a-newer-release decision with channel + frequency helpers; **never
raises** — offline is a quiet, expected outcome); `downloader.py` (streams the
installer to `%TEMP%\OptionsPilotUpdater` with progress/speed/ETA, cancellation,
and an atomic `.part`→final rename so a cancelled/failed download leaves no
partial file); `validation.py` (verifies exists/size/name before anything runs,
structured so SHA-256 and Authenticode checks slot in with no caller changes);
`installer.py` (a **mandatory `pre-update` backup** via `create_backup`, then a
silent `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL` install, then restart);
`ui.py` (pure presentation — human sizes/ETA, safe markdown→HTML for release
notes with everything escaped first); and `service.py` (`UpdateService` — the
app-facing facade and thread-safe state machine).

**Experience.** On launch the app quietly checks GitHub in the background
(respecting the user's auto-check + frequency preferences); if it's current,
nothing happens. If a newer version exists, a professional dialog shows the
version diff, release date, download size, estimated time, and rendered release
notes, with **Update Now / Remind Me Later / Skip This Version**. Update Now
streams the installer with a live progress bar (downloaded/total MB, speed, ETA)
and a Cancel button, then one click validates, backs up, installs silently, and
restarts. Because user data lives in `%LOCALAPPDATA%\OptionsPilot` (V0.4.4) and
the installer only replaces Program Files, an update can never lose the journal,
paper account, coach history, settings, watchlists, or backups.

**Wiring.** `/api/update/{status,check,download,progress,cancel,apply,skip,
settings}` on the FastAPI layer; `UIServer` owns an `UpdateService(__version__,
runtime)` and kicks the launch-time check **gated on `run_loop`** so the test
suite never touches the network; `ui/desktop.py` registers an install hook that
closes the window and releases the single-instance lock so the exe can be
replaced. Preferences (auto-check, frequency `launch|daily|weekly`, `stable|beta`
channel, skipped version, last-checked) persist via `RuntimeSettings` under the
`updates` key of `settings.json`. Frontend (`ui/static/index.html`): a Settings ▸
**Software updates** panel, a header **Help ▸ Check for Updates…** menu, and the
update dialog.

**Security & future-readiness.** Only the configured repository is trusted (fixed
in code), only a recognized installer asset is ever downloaded, and validation
runs before anything executes — with hash and Authenticode verification designed
in as drop-in checks. Downloads land only in a scratch temp dir. Still open before
a public release: code signing, a published checksums asset, and a manual
end-to-end update QA on real Windows (the automated tests can't drive a real Inno
upgrade).

**Tests (+105).** `test_update_version.py`, `test_update_github.py`,
`test_update_checker.py`, `test_update_downloader.py`, `test_update_validation.py`,
`test_update_installer.py`, `test_update_service.py`, `test_update_endpoints.py`,
plus runtime-prefs coverage; `test_architecture.py` allow-lists the new
core-only `update` subpackage.

## [Uncommitted] 2026-07-23 — V0.4.6: Professional Windows Installer 1.0

*Version 0.4.5 → 0.4.6. 527 → 546 tests (+19). Turns OptionsPilot into a
professionally installable Windows desktop app and wires the installer into the
release pipeline. **No application behavior changed** — only how the app is
delivered. Full details in `docs/INSTALLER.md`.*

**Windows installer (`installer/OptionsPilot.iss`).** Completed the Inno Setup
script (evolved from the V0.4.5 template, preserving its stable `AppId`). It:
installs to **`C:\Program Files\OptionsPilot`** by default (`{autopf}`, 64-bit,
admin; the directory page lets the user choose another location); creates a Start
Menu folder **OptionsPilot** with **OptionsPilot** and **Uninstall OptionsPilot**
entries; offers an optional **desktop shortcut, checked by default**; uses the
app icon (`assets/optionspilot.ico`) for the setup exe, shortcuts, and
uninstaller; and registers with Windows **Installed Apps / Programs and Features**
(publisher, version, publisher/support URLs, copyright, auto-computed size).

**Upgrades & data safety.** The stable `AppId` (`{{4C0D3A7E-…}`) lets Windows
recognize an existing install and upgrade it **in place** (`UsePreviousAppDir`),
replacing only application files; `CloseApplications=yes` closes a running
instance first. Because all user data lives in a **separate root**
(`%LOCALAPPDATA%\OptionsPilot`, via `core/paths.py::AppPaths`) that the installer
never writes to, upgrades and reinstalls never touch the journal, coach reviews,
settings, trades, watchlists, logs, or backups.

**Uninstall.** The uninstaller asks, at uninstall time, *"Do you also want to
remove your personal OptionsPilot data?"* defaulting to **No** (`MB_DEFBUTTON2`,
so silent/accidental uninstalls keep data); only an explicit **Yes** deletes
`%LOCALAPPDATA%\OptionsPilot` (via `DelTree` in a `[Code]` block). This replaced
the template's install-time "removedata" task — the decision now happens where it
belongs.

**Pipeline integration (`scripts/build_installer.ps1` + `release.yml`).** New
`scripts/build_installer.ps1` locates `ISCC.exe`, reuses the built
`dist\OptionsPilot\`, and compiles the installer stamping the single-source
version (`/DMyAppVersion=<optionspilot.__version__>`) →
`installer\Output\OptionsPilot-Setup-v<ver>.exe`. `release.yml` now installs Inno
Setup (`choco install innosetup`), runs that script, and uploads the setup exe
**alongside** the retained `OptionsPilot-vX.Y.Z.zip` on the GitHub Release. Added
`/installer/Output/` to `.gitignore`.

**Tests:** +19 (`tests/test_installer.py`) — static guards on the installer's
load-bearing decisions (Program Files target, admin, stable AppId, Start-Menu +
uninstall entries, desktop-icon-default-checked, app icon everywhere,
uninstall-time data prompt defaulting to No, no install-time data removal,
in-place upgrade, versioned output) and the pipeline wiring (Inno install +
installer build + both assets uploaded, zip retained). The ISCC compile and the
fresh-install / upgrade / repair / uninstall runs are **manual/CI** (documented
in `docs/INSTALLER.md`), as there is no headless way to drive a Windows installer.

**Still open (not this milestone):** Authenticode **code signing** of the setup +
exe (SmartScreen warns until then; `SignTool` hook stubbed in the `.iss`), and
the placeholder `LICENSE` still needs a real license choice before a public
release.

## [Uncommitted] 2026-07-23 — V0.4.5: Professional Release Pipeline 1.0

*Version 0.4.4 → 0.4.5. 520 → 527 tests (+7). A release-automation milestone —
**no application behavior changed**; only how releases are built, tested,
packaged, and published. Turns a version tag into a downloadable GitHub Release
with zero manual steps, and lays clean groundwork for a Windows installer +
auto-updater. Full design in `docs/RELEASE.md`.*

**GitHub Actions.** New `.github/workflows/ci.yml` — runs on every branch push
and PR (and is reusable via `workflow_call`): installs the project (pip-cached on
`pyproject.toml`), runs the full `pytest` suite, the storage/bundle `selftest`,
`check_html_ids`, and `check_docs`; fails fast (`concurrency.cancel-in-progress`);
Windows runner (the target platform). New `.github/workflows/release.yml` — runs
on `v*` tags: **reuses the CI test job** (a tag never ships on a red suite), then
verifies the tag matches `optionspilot.__version__`, builds the exe
(`scripts/build.ps1` → PyInstaller + packaged-selftest gate), packages the zip,
and creates a **GitHub Release** with the zip attached and notes drawn from the
CHANGELOG (via the automatic `GITHUB_TOKEN`, no secrets required).

**Single-source versioning.** The version now lives in exactly one place —
`optionspilot/__init__.py::__version__`. `pyproject.toml` derives it dynamically
(`dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr =
"optionspilot.__version__"}`), so there is no second copy to drift.
`scripts/bump_version.py` edits that one line; `scripts/check_docs.py` now fails
if `pyproject.toml` ever hardcodes a version or drops the dynamic wiring; and the
release workflow fails fast if the pushed tag disagrees with `__version__`.

**Artifacts.** `scripts/package_release.ps1` produces a clean, versioned
`dist/OptionsPilot-vX.Y.Z.zip` containing the app bundle plus `LICENSE`,
`README.md`, and `CHANGELOG.md` — and explicitly **excludes** source, tests,
build caches, and any local user state (`data/`, `logs/`) so a release never
ships a developer's paper account. Verified locally against a real build (54 MB
zip; correct top-level entries; no `data/`/`logs/`/source). `scripts/release_notes
.py` extracts the CHANGELOG section for a version as the Release body.

**Groundwork.** Added a placeholder `LICENSE` (a non-granting "all rights
reserved" default, clearly flagged to be replaced with a real license choice
before a public release — the packager bundles whatever it contains). Added an
**unwired** Inno Setup installer template (`installer/OptionsPilot.iss`) plus
documented install paths / shortcuts / AppData usage / uninstall behavior in
`docs/RELEASE.md` — leaning on the V0.4.4 storage split so an installer/updater
only ever touches program files, never user data. Nothing builds the installer
yet (per scope).

**Tests:** +7 (`tests/test_release_tooling.py`) — the single-source-version
invariant (pyproject dynamic, metadata resolves to `__version__`, `check_docs`
agrees) and the release-notes extractor. The PowerShell build/packaging scripts
and YAML workflows were verified by hand (YAML parses; packaging produced a
correct zip; `release_notes.py` extracts the right section).

## [Uncommitted] 2026-07-23 — V0.4.4: persistent storage & automatic data migration

*Version 0.4.3 → 0.4.4. 492 → 520 tests (+28). A core-infrastructure milestone:
user data is now completely separated from application binaries, so a future
version can replace the executable without ever losing paper-trading history,
coach reviews, journal entries, settings, watchlists, learned weights, or logs.
No user-visible behavior changes; existing installs are migrated automatically,
once, with nothing deleted. Full design in `docs/STORAGE.md`.*

**Storage layer (`core/paths.py::AppPaths`, new).** The single source of truth
for every filesystem path. The storage **root** moved out of the
current-working-directory (it used to land beside the exe) to a stable per-user
location: `%LOCALAPPDATA%\OptionsPilot` on Windows (XDG / `Application Support`
elsewhere), overridable with `OPTIONSPILOT_HOME`. `AppPaths` exposes typed
helpers (`get_data_dir`, `get_journal_db`, `get_coach_dir`, `get_settings_file`,
…) and `ensure()`; no module constructs the root itself. Layout under the root:
`data/ logs/ backups/ exports/ migrations/`, with the existing `data/` subtree
(paper.db, journal.db, orders.db, experience.db, cache.db, settings.json,
coach/, state/, learning/, reports/) preserved unchanged.

**Automatic migration (`core/migration.py`, new).** `initialize_storage(paths)`
runs once at startup: it creates the layout and, on first run, imports a legacy
CWD/exe-relative `data/`+`logs/` install into the new root — a **lossless copy**
that preserves timestamps (`copy2`), verifies every file by size, **never
overwrites a newer file**, and **never deletes the source**. Completion is
recorded in `migrations/migration_version.json`. The import is idempotent and
self-healing: a partial copy completes on the next launch, and a missing or
corrupted marker cannot cause data loss (the copy never clobbers newer data).
Verified end-to-end against the real 27-file legacy install: all files copied
byte-for-byte, originals intact, second launch a no-op.

**Backups + versioned framework.** `create_backup()` writes a timestamped
snapshot of `data/` into `backups/` and runs automatically before any versioned
migration. The `Migration(version, description, apply)` registry (`MIGRATIONS`)
is the groundwork for future schema migrations — intentionally **empty** in this
release (framework only; no future migration implemented).

**Wiring.** `__main__._bootstrap` builds `AppPaths`, runs `initialize_storage`,
points logging at `paths.root`, and threads `data_dir=paths.get_data_dir()`
through every command; `Orchestrator`/`UIServer`/`create_app`/`serve`/`desktop
.launch` default to the per-user root when no path is given. Replaced the last
CWD-relative `Path("data")` hardcodes (CLI commands + the UI backtest report
path) with `AppPaths`/`data_dir`-derived paths. Existing `data_dir=` APIs are
unchanged, so all prior code and tests keep working.

**Selftest.** `python -m optionspilot selftest` now also verifies the storage
layout — every directory exists and is writable, and the migration marker is
valid — in addition to the yfinance bundle check.

**Tests:** +28. `tests/test_paths.py` (path algebra, platform roots, env
override, `ensure`) and `tests/test_migration.py` (fresh install, upgrade
import, timestamp preservation, idempotency, many launches, partial migration,
never-overwrite-newer, corrupted marker, existing-AppData skip, backups,
versioned framework, store read/write). A new autouse fixture in
`tests/conftest.py` isolates `OPTIONSPILOT_HOME` to a temp dir so the suite never
touches a developer's real AppData.

## [Uncommitted] 2026-07-23 — V0.4.3: AI Coach 2.0 (phase 1)

*Version 0.4.2 → 0.4.3. 470 → 492 tests (+22). Transforms the Coach from a page
that displays a review into an intelligent, offline, deterministic trading
mentor — **additively**. Manual (Human Mode) trades only; the trading path, risk
manager, and the timing of when a review runs are all untouched, and every change
is backward-compatible with reviews persisted before 2.0.*

**Per-trade category scorecard (`coach/categories.py`, new).** Every manual
`CoachReview` now carries a fixed set of **10 categories** — Entry Quality, Exit
Quality, Risk Management, Position Size, Emotional Discipline, Rule Following,
Patience, Timing, Trend Alignment, Reward/Risk Ratio — each with a 0–100 score, a
**data-referenced** explanation (it quotes the actual `Finding` details: "RSI at
entry: 78", "premium outlay 7.2% of the account", …), and one concrete
suggestion. Scores are derived **entirely from signals the review already
gathered** (the before/during `Finding`s + the 12-tag mistake taxonomy), so the
scorecard adds no new data capture and cannot change what the review observes.
`context_only` categories report `None` ("not enough data") when no near-entry
snapshot was captured, rather than a misleading perfect score. `CoachReview` also
gained a small outcome snapshot — pnl, return_pct, hold_minutes, a best-effort
`r_multiple` (planned premium risk estimated from stop distance × entry delta;
`None` when not derivable — never invented), entry_ts, symbol, direction.

**Mentor dashboard (`coach/analytics.py`, new — pure `build_dashboard(reviews)`).**
Aggregates all reviews into: headline sub-scores (consistency / risk / execution
/ discipline), the category scorecard averaged with **month-over-month trend**,
win/loss **streaks**, **pattern detection with a confidence level and language**
(a one-off is low-confidence "may be developing"; a frequent, consistent pattern
is "appears to be a recurring habit" — confidence scales with sample size and
frequency), a **month-over-month improvement timeline** ("Risk Management
improved 14 points this month"), and a **≤5 ranked action plan**. Patterns and
actions are computed over a **recent window** (last 25 reviews), so a habit the
user has stopped drops off the plan automatically as newer clean trades push it
out. Strengths/weaknesses fall out of the category grades.

**API + UI.** `GET /api/coach` now also returns `dashboard`, cached by review
count (recomputed only when a new review is written — reviews are write-once per
trade), mirroring the existing journal-cache pattern; `profile` and `reviews`
are unchanged for backward compatibility. The coach tab renders the sub-score
cards, the category scorecard (score bars + grades + trend arrows), the action
plan, the improvement timeline, and confidence-tagged recurring patterns —
reusing the existing card/panel styles with no new CSS. Verified via
`check_html_ids` and the headless `browser_check` (9 tabs, zero console errors).

**Architecture note.** Kept inside `coach/` with two focused new modules
(`categories.py`, `analytics.py`) rather than the originally-suggested parallel
`analytics/ review/ services/` tree, to match this codebase's one-concept-per-
module convention. **Tests:** +22 (`test_coach_categories.py`,
`test_coach_analytics.py`, `TestCoachAPI`), covering category scoring + missing
context, confidence/developing-vs-recurring language, streaks, monthly trend,
action-plan capping + auto-expiry, headline scores, and backward compatibility.

## [Uncommitted] 2026-07-23 — V0.4.2: architecture audit + three approved refactors

*Version 0.4.1 → 0.4.2. 454 → 470 tests (+16). An architecture-hardening sprint:
a full read-only audit (documented in `docs/ARCHITECTURE-AUDIT-V0.4.2.md`) found
the codebase in good health — clean verified layering, no SQL outside the
persistence modules, zero real debt markers — so only three low-risk,
behavior-preserving improvements were implemented, each as a separate change
with its own regression tests. No user-visible behavior changed.*

**1. Shared SQLite persistence foundation (`core/sqlite.py`).** The five stores
each reimplemented the connect/schema boilerplate, and only the experience store
had a real migration framework. New `connect()` (dir creation +
`check_same_thread=False` + optional WAL) and `run_migrations()` (ordered
`PRAGMA user_version` migrations, refusing a newer-than-supported schema) are now
used by **all five** stores, adopted incrementally: `cache` (disposable, to
validate the base) → `journal` (the system of record) → `orders` → `paper` →
`experience` (refactored onto the shared base, dedup). Behavior-preserving:
migration 1 of each store is its *exact current schema*, so an existing on-disk
database (at `user_version 0`) runs the idempotent `CREATE TABLE IF NOT EXISTS`
and lands at the same schema it already had. `paper.db`'s `managed_by` ALTER
became an idempotent migration 2 that swallows the duplicate-column error exactly
as the prior ALTER-on-every-open code did. This gives the journal and future
Replay/Analytics/Live-Broker databases the same safe schema-evolution path.
+13 tests (`test_sqlite.py`), incl. the legacy-db and idempotent-ALTER adoption
hazards.

**2. UI/server import cleanup.** ~15 imports scattered into `ui/server.py`
route/method bodies were hoisted to the module top (the UI is the composition
root — everything below it is already importable, no cycles), and the **private**
`from optionspilot.orchestrator import _WINDOW_DAYS` reach-through (in both
`ui/server.py` and `__main__.py`) was removed by promoting the constant to a
public `orchestrator.WINDOW_DAYS`.

**3. Layering-guard tests (`test_architecture.py`).** The clean dependency graph
was previously maintained by discipline alone; it is now executable. An
AST-based allow-list asserts each subpackage imports only its permitted siblings
(`engine` never imports `broker`/`risk`/`ui`; `experience` never depends on
trading internals; `analysis` stays pure), plus guards that the composition roots
don't import upward and that `ui/server.py` keeps no function-level imports.
+6 tests. (This suite immediately paid for itself: it surfaced a UTF-8 BOM in
`yfinance_provider.py` and caught the last stale `_WINDOW_DAYS` reference.)

**Not done (documented as optional in the audit report):** orchestrator
decomposition (Finding 2), the `core→config` inversion (5), and the
snapshot-bypass tidy (6) — none justify the churn/risk today.

## [Uncommitted] 2026-07-23 — V0.4.1 (phase 3): Experience Engine integration

*Version 0.4.0 → 0.4.1. 424 → 454 tests (+30: `test_snapshot.py` +6,
`test_experience.py` +~20, `test_similarity.py` +1, `test_ui_server.py` +3).
Completes the integration of the Experience Engine into the rest of
OptionsPilot: every AI recommendation now carries advisory historical context.
Backend + API only — the dashboard frontend is Phase 5. Full design in
`docs/ROADMAP-V0.4-EXPERIENCE.md` §12.*

**Centralized AI snapshot.** New `experience/snapshot.py::build_snapshot` is the
one place a deterministic decision context is captured — from the
`EngineDecision`/`TimeframeView`/`GateReport` (+ optional plan/contract): score,
reasoning, HTF trend, the full per-component evidence breakdown, gate result +
rejection reasons, RSI/ADX/rvol/ATR/EMA/MACD/VWAP/supertrend/divergence, contract
Greeks, stop/target/RR, and operating/trading/learning modes. It duck-types the
decision so `experience/` keeps no runtime dependency on `engine/`. Fields the
engine doesn't compute (Bollinger, a volume-profile histogram) are stored as
None, never invented.

**Feature symmetry.** Both the AI entry path (snapshot stored in
`_TradeMeta.entry_context`, fed to the experience record at close) and the
manual/coach path (`_capture_context`, now also built by `build_snapshot`) go
through the one builder, so AI and manual trades record equivalent feature
quality. A single shared `features._entry_fields` extractor backs both a closed
trade (`build_experience`) and a live setup (`build_query_record`).

**Historical-similarity explanation (advisory).** For tradeable signals only,
the orchestrator attaches `ExperienceEngine.explain_setup(snapshot)` — n similar,
win rate, avg return/hold, calibrated confidence, and grounded success/failure
patterns — to the status payload and the Human-Mode advice notification. It is
computed AFTER the deterministic decision and never feeds back into it.

**Experience API.** `ExperienceEngine` gains `recent`, `similar_trades` /
`similar_to_snapshot` (→ `SimilarTrade` viewer rows), `statistics` (overview +
by-strategy/regime/session + failure-modes/success-patterns), `strategy_
statistics`, `regime_statistics`, `failure_modes`, `success_patterns`,
`explain_setup`. All SQL stays inside `ExperienceStore`. Exposed over
`GET /api/experience` and `GET /api/experience/similar?symbol=`.

**Storage v2.** `_migration_2` adds an indexed `market_regime` column (derived:
trend × IV volatility) plus `return_pct`/`hold_minutes`, backfilled from each
row's JSON payload. Aggregate statistics are pure SQL (COUNT/SUM/AVG over indexed
columns) — never deserializing payloads — which keeps them fast at 100k+.

**Performance (measured at 20k rows):** similarity `summarize` well under the 3s
budget (direction-pruned candidates + bounded distance pass); SQL `aggregate`
under 0.5s. Advisory similarity runs only for tradeable signals, never per
scanned symbol.

**Safety:** nothing here touches the gate, risk, sizing, entries, or exits. The
deterministic score remains the sole trading input; every new call site is
best-effort and cannot break the trading path. All 424 prior tests still pass.

## [Uncommitted] 2026-07-23 — V0.4.0 (phases 1–2): the AI Experience Engine

*Version 0.3.5 → 0.4.0. 424 tests (+32: `test_experience.py` +20,
`test_similarity.py` +12). The first two phases of the V0.4.0 sprint that turns
the AI from a static analyzer into a system that learns from paper-trading
experience. Backend-only — no frontend change this session. Full design in
`docs/ROADMAP-V0.4-EXPERIENCE.md`.*

**What was built.** A new `optionspilot/experience/` subsystem — the AI's
long-term trading memory — recorded **alongside** the journal, never instead of
it:

- **Experience Engine + store (Phase 1).** `ExperienceRecord` is a rich,
  expandable superset of `TradeRecord` (identity, trade shape, outcome, decision
  context, market/session indicators, reasoning, an exploration flag, and an
  `extra` JSON blob for future fields like screenshots/news with *no* migration).
  `ExperienceStore` is a SQLite store (`data/experience.db`) built for 100k+
  trades without a redesign: a hybrid row of indexed query columns + a
  full-fidelity JSON payload, with a `PRAGMA user_version` migration framework
  that refuses to open a newer-than-supported schema. `features.py` extracts an
  `ExperienceRecord` and a normalized feature vector (fixed ranges, so a
  record's vector is stable for all time) purely from a trade + its best-effort
  analysis context.
- **Similarity Engine (Phase 2).** `SimilarityEngine` finds the most comparable
  historical trades via a hand-authored weighted distance (direction anchor +
  evidence-set Jaccard + setup/trend/timeframe/session + normalized numerics),
  and aggregates the cohort into evidence: win rate, avg return/hold, most-common
  exit, typical failure mode, ranked matches, and an **advisory** calibrated
  confidence (shrinkage blend of model estimate and historical win rate).

**Three decisions with the user** (recorded in the roadmap doc): (A) calibrated
confidence is **advisory / display-only** — the deterministic scorer stays the
sole live-trading input, honoring `CLAUDE.md`'s no-statistical-model-on-the-
trading-path rule; (B) the spec's "Exploration mode" becomes a **future
independent `learning_mode` axis** (orthogonal to `operating_mode`/`trading_mode`)
rather than overloading `trading_mode` — already modelled as
`ExperienceRecord.exploration`; (C) scope this session is Foundation + Similarity.

**Integration.** `Orchestrator` constructs `self.experience` and calls
`record_trade` right after both `journal.record` sites (AI `_finalize_trade`,
manual `_finalize_manual`). Recording is best-effort — any failure is logged and
swallowed, so it can never disturb journaling, risk accounting, or trading
(proved by `test_record_trade_is_best_effort`). No existing behavior changed;
all 392 prior tests still pass.

**Deliberately not populated yet** (honest limitations, modelled for later):
MFE/MAE (need intrabar data), `risk_multiple` (needs stop premium), and richer
AI entry-context symmetry — see the roadmap doc's "Forward plan".

## [Uncommitted] 2026-07-22 — V0.3.5: distribution fix (downloaded release crashed on launch)

*Version 0.3.4 → 0.3.5. 392 tests (+3 in `test_packaging.py`; the previously
recorded 388 was one short of the actual suite). A packaging/runtime investigation, no
feature changes: the exe ran fine from the dev machine's `dist\` folder but,
after being zipped, uploaded to GitHub, downloaded, and extracted elsewhere, it
crashed on launch with `RuntimeError: Failed to resolve
Python.Runtime.Loader.Initialize from Python.Runtime.dll` before any
OptionsPilot code ran.*

**Root cause (reproduced, not guessed):** pywebview's only Windows backend is
WinForms (`webview.platforms.winforms`), which drives WebView2 through
pythonnet — `import clr` hosts the .NET Framework CLR via `clr_loader` and
loads `pythonnet/runtime/Python.Runtime.dll` as a managed assembly. Files
downloaded by a browser and extracted with Explorer all carry the Mark-of-the-
Web (`Zone.Identifier` ADS, ZoneId=3), and **.NET Framework refuses to load a
managed assembly flagged as coming from the internet**
(`NotSupportedException`, HRESULT 0x80131515). clr_loader's native host
swallows that exception and returns NULL, surfacing as the opaque "Failed to
resolve" error. Locally built files carry no zone marker, which is why every
dev-side launch — including from `dist\` — worked. Reproduced end-to-end by
flagging a copy of the release exactly as Explorer extraction does: identical
traceback, byte-for-byte. Isolated mechanism test: MOTW on `Python.Runtime.dll`
alone → the exact error; clean copy → resolves. The documented
`loadFromRemoteSources` config opt-outs were tested (named AppDomain + config
file, host-exe `.config`) and do **not** reach clr_loader 0.3.1's load path, so
the marker itself has to go.

**Fix:** `optionspilot_app.py` gained `unblock_bundle()` — at startup (frozen
Windows builds only, before pywebview can `import clr`) it walks the install
folder and deletes each file's `Zone.Identifier` stream, which is precisely
what Explorer's "Unblock" checkbox does. First launch self-unblocks; later
launches are a no-op. Dev interpreters never touch anything, and unwritable
files are skipped (no worse than before). Tests (`TestUnblockBundle` in
`tests/test_packaging.py`): the stream is removed across the bundle tree, the
dev interpreter is a strict no-op, and the entry point actually calls the gate
before `main()` (a gate that exists but is never called protects nothing —
CLAUDE.md "Known traps"). Verified by rebuilding, MOTW-flagging every file of a
copy outside the repo, and launching: the desktop window opens.

**Also repaired in passing:** the V3.3.2/V3.3.5 "same-key refresh preserves an
in-flight history load" chart check stubbed `window.fetch` and never restored
it (`origFetch` was captured but unused), so every later check ran against the
stub's fake SPY payload — the very next check ("invalid ticker shows error
overlay") failed on every run because ZZZZZZ9 "loaded" fake candles instead of
erroring. The stub is now removed right after its check, and the suite runs
green again.

## [Uncommitted] 2026-07-22 — V3.3.1: chart reliability investigation (blank-chart root cause)

*Version 0.3.3 → 0.3.4. 388 tests (+1 backend), chart_check 41 → 44. A pure
root-cause investigation of the intermittent "switch symbols enough times and a
chart loads blank and stays blank until restart" report. The chart was
instrumented (fetch start/finish/superseded/abort/empty, gen, cache, render,
timers), the failure reproduced under load + fault injection, and each cause
traced to a concrete mechanism before any code changed. No new features.*

**Root causes found (each a lifecycle/resource bug, not a rendering bug):**
- **No timeout on the chart's data fetch → permanent blank.** Under a backend
  throttle backlog (measured: concurrent fetches serialize through yfinance's
  single 0.15s-per-request lock, pushing latency to 10–15s+) or a hung upstream
  connection, an unbounded `fetch()` left the first-paint loading spinner up
  forever — exactly "loads blank, stays blank until restart" (restart cleared
  the backlog). Confirmed: after 6s of a slow backend the overlay was still a
  spinner with no recovery.
- **Superseded fetches were never cancelled.** Each symbol switch fired a fetch;
  a rapid burst left every superseded request running to completion, and because
  each holds a slot in the serialized throttle, the pile-up starved the ONE
  symbol the user actually landed on — "after enough switching, charts stop
  loading." (A 12-switch burst now aborts 11 fetches instead of running all 12.)
- **Backend `yfinance.history()` had no request timeout.** A hung Yahoo
  connection blocked the worker thread while it held the CachedProvider's
  per-key in-flight slot, so every later request for that symbol piled up behind
  it — a second "restart fixes it" path.
- **A hung history fetch left `CH.historyLoading` stuck true forever**, silently
  disabling all further history loading for the session.
- **Malformed payloads (duplicate / out-of-order bar times) threw an uncaught
  "Value is null" from lightweight-charts' own later paint frame** — uncatchable
  by the `setData` try/catch. (The backend `validate_candles` already
  dedupes+sorts, so real data never triggers it; this is defense-in-depth.)
- **Backend `_mem` cache was unbounded** — one entry per distinct
  (symbol, timeframe, ext), accumulating candle DataFrames over a long session.

**Fixes (root-cause, not retries):**
- Frontend chart fetch now uses an `AbortController`: a 15s timeout converts a
  hung/slow load into the normal recoverable error path (error overlay +
  existing auto-retry on first paint; keep the chart on a refresh) instead of a
  permanent spinner, and the previous in-flight fetch is aborted on every new
  load/switch so superseded requests stop consuming the throttle. Same treatment
  for the history fetch (with the timeout also clearing the stuck-flag path).
- Backend `yfinance.history()` gained a `REQUEST_TIMEOUT` (10s) so a hung
  connection can't block the throttle-lock holder; failures fall through to the
  next symbol variant / an empty frame the caller already handles.
- `chEnsureMonotonic()` sanitizes any non-ascending/duplicate bars before
  `setData` (fast O(n) no-alloc scan on the normal clean path), and the rAF
  overlay loop is wrapped so a transient render throw can't kill the loop.
- Backend `_mem` cache is now a bounded LRU (`MEM_CACHE_MAX=400`).
- Tests: `chart_check` +3 (hung-backend timeout→recover, rapid-switch abort,
  non-monotonic sanitize) and a backend `TestMemCacheBounded`. Verified: 250
  rapid symbol switches = 0 blanks / 0 console errors; fault injection (empty /
  malformed / flapping) recovers 7/7; memory plateaus; all prior 41 checks green.

**Provider limitation (confirmed, not fixed here):** yfinance serializes all
requests through one process-wide throttle, so heavy concurrent load (scan loop +
rapid chart switching) still adds latency. The bounded fetch makes this
*recoverable* rather than a permanent blank; a real-time streaming provider (the
documented upgrade path) would remove the serialization entirely.

## [Uncommitted] 2026-07-20 — V3.3: chart stabilization & market validation

*Version 0.3.2 → 0.3.3. 387 tests, chart_check 36 → 41. A correctness sprint
verified against LIVE market data (reproduced Monday during regular trading
hours), not just tests. Every issue was reproduced in a real browser, root-caused,
fixed at the architecture level, and re-verified in the browser and in the
rebuilt exe. Two behaviours turned out to be **yfinance provider limitations,
not app bugs** — documented, not papered over.*

- **Live sync cadence (Issue 1).** The forming candle updated in visible ~30s
  chunks because the refresh was a fixed 30s poll on top of a 20s backend TTL.
  The refresh is now timeframe-adaptive (≈7s for minute frames while the market
  is open, slower for hourly/daily, idle when closed) and re-arms on every load
  so a tf switch adopts the new cadence immediately; `CANDLE_TTL` for fine
  intraday frames was lowered in lockstep so a fast poll returns fresh bars.
  **Provider limit (documented):** yfinance is poll-only (no streaming) and
  returns the *current forming bar* as a flat placeholder with `volume=0` until
  it completes, so a true tick-by-tick forming candle like TradingView's is not
  possible on this feed — it needs a streaming provider. Completed bars match
  yfinance to the cent/share and arrive within one poll of the minute closing.
- **Timezone (Issue 2).** The x-axis and crosshair rendered in UTC (a 13:00-ET
  bar showed "17:00"). Bars carry true UTC-epoch timestamps and are NOT shifted
  (that would ripple into drawings/history/timeIndex); instead the *labels* are
  formatted in America/New_York via `Intl` (`tickMarkFormatter` +
  `localization.timeFormatter`). Daily bars are anchored at ET midnight, so the
  same ET conversion yields the correct calendar date with no off-by-one.
- **Candle countdown timer (Issue 3).** New TradingView-style "time until this
  bar closes" pill, updated every second, computed from the bar boundary and the
  real wall clock (not a faked data timer). Shown for intraday frames while the
  market is open; hidden on daily/closed.
- **Drawing render lag on zoom/pan (Issue 4).** lightweight-charts fires a
  time-range event on horizontal pan but NONE for vertical changes (price-axis
  drag, autoscale), so the drawing overlay froze on vertical moves and snapped
  later (reproduced: 0 overlay redraws during a price-axis drag). Added an
  animation-frame sync loop that redraws the overlay whenever the chart's
  coordinate mapping changes — horizontal or vertical — so drawings stay glued
  to the chart. Idle cost is one cheap compare per frame; skipped when off-screen.
- **Drawing creation preview (Issue 5).** The first click now anchors the drawing
  and shows it immediately, and the second endpoint rubber-bands to the cursor
  until the finalizing click (was: nothing appeared until the second click).
  Preview is purely visual — never added to the model, hit-tested, or persisted.
- **Refresh discarded paged-in history & moved the viewport (Issues 7 & 8, the
  key root cause).** The periodic refresh re-fetches only the base window; it was
  replacing `CH.data` with it, discarding older bars the user had scrolled in —
  collapsing e.g. 2025 bars back to ~470 and shifting every logical index, which
  yanked the viewport and made scrolled history randomly vanish. The refresh now
  MERGES the fresh recent window onto the retained older bars (`chMergeRefresh`),
  and the pre-fetch cache paint is restricted to genuine symbol/timeframe
  switches. History is preserved across refreshes and the viewport holds exactly.
- **Verified-not-regressed (Issues 6, 9, 10, 11, 12).** Drawing persistence
  across timeframes (same object renders on 1m→1d, never duplicated); candle
  correctness (app matches yfinance bar-for-bar across SPY/AAPL/NVDA); blank
  charts (all 13 named symbols incl. BRK.B render; invalid symbols show the error
  overlay, not a silent blank); memory (heap plateaus, canvas count and payload
  cache bounded — no leak across 80 symbol/timeframe switches); Auto Follow
  (OFF by default, manual pan disables, Latest re-enables) — all reproduced with
  real mouse against live data.
- **Tests.** `chart_check` 36 → 41: America/New_York display, countdown timer,
  drawing creation preview, overlay tracking a real vertical price-axis drag, and
  a refresh preserving paged-in history + holding the viewport — each fails on
  pre-V3.3 code. Also hardened the flaky "viewport recovery" check (its extreme
  overscroll strand was clamped non-deterministically by the library depending on
  the live bar count; a narrow whitespace window strands deterministically).

## [Uncommitted] 2026-07-20 — V3.2.2: viewport ownership unification + Auto Follow

*Version 0.3.1 → 0.3.2. 387 tests, chart_check 33 → 36. V3.2.1 fixed three
symptoms (drawings, viewport snapping, tf-context loss); every NEW bug
reported afterward (random recentering, history intermittently failing,
losing viewport while scrolling) was another symptom of the same underlying
conflict — the viewport had no single owner. This sprint finds and fixes the
architecture, not the symptoms.*

- **Viewport ownership audit.** Every `fitContent()` / `setVisibleLogicalRange()`
  / `setVisibleRange()` / `scrollToRealTime()` call site in `static/index.html`
  was enumerated by owner and reason (Reset, Latest, Auto Follow, tf-switch
  focal restore, same-key refresh, history prepend, symbol switch). All of
  them now go through one function, `chMoveViewport()` — no subsystem calls a
  `timeScale()` mutator directly anymore.
- **Bug 4 (one controller).** `chMoveViewport(fn)` is the single gate that sets
  `CH.restoringViewport` around every sanctioned move; the range-change
  subscription is the only place that reads it to tell a programmatic move
  apart from a real user pan/zoom/drag.
- **Real root cause of "history loading intermittently fails" (Bug 2, part
  1).** The old `wheel`/`touchstart`/`pointerdown` listeners armed history a
  DOM-event tick *after* the library's own range-change fired during the same
  pan, so a scroll-into-history sometimes silently did nothing. Fixed by
  arming directly off the range-change subscription itself, in the same
  synchronous pass that decides whether the change was user-driven.
- **Real root cause of "history loading intermittently fails" (Bug 2, part
  2 — the deeper one).** Instrumenting the vendored lightweight-charts'
  actual callback timing showed `subscribeVisibleLogicalRangeChange` does
  **not** fire synchronously inside `setVisibleLogicalRange()`/`fitContent()`
  — it fires on a *later* animation frame. Every "sanctioned" move that reset
  `CH.restoringViewport` synchronously right after its call (the V3.2.1
  pattern) closed the guard window *before that callback ever arrived* — so
  one frame later, every guarded move looked like a user pan: silently
  re-arming history-load and (see Bug 3 below) breaking Auto Follow the
  instant it was enabled. Fixed by deferring the reset two animation frames
  in `chMoveViewport`, past when the callback reliably fires.
- **Auto Follow (Bug 3) — new TradingView-style "go to realtime" toggle.**
  OFF by default: the user owns the viewport; nothing auto-recenters except
  Reset and Latest. ON: the chart always keeps the newest bar in view across
  refreshes, live tail updates, and switches. Manual pan/zoom (detected in
  the range-change subscription) turns it back off; pressing Latest turns it
  back on; the preference persists (`localStorage`). New `#ch-follow` button
  next to Reset/Latest, and the `A` keyboard shortcut.
- **`scrollToRealTime()` discovery.** Auto Follow initially wouldn't stay on:
  turning it on and immediately re-checking showed it already false. Root
  cause: unlike `setVisibleLogicalRange()`/`fitContent()`, `scrollToRealTime()`
  runs a multi-frame SMOOTH-SCROLL ANIMATION — every intermediate animation
  tick fired the range-change subscription, and by the second or third tick
  the (2-frame) guard window had already closed, so the animation's own
  motion was read as a user pan and immediately disabled Auto Follow before
  the scroll even finished. Fixed by replacing `scrollToRealTime()` everywhere
  with `chScrollToLatest()` — a single non-animated `setVisibleLogicalRange`
  call computed to land on the same destination — sidestepping the animation
  entirely instead of chasing its frame count.
- **Bug 5 (history prepend stationarity).** Verified (not just assumed): a
  history prepend must never move bars already on screen — only new, older
  bars appear at the left. Covered by a new regression test that captures the
  on-screen time range immediately before and after a real-drag-triggered
  merge.
- **Tests.** `chart_check` 33 → 36: a real drag pan with no `historyArmed`
  manual-set "cheat" (exercises the exact race the Bug 2 fix closes) plus
  on-screen stationarity; Auto Follow OFF-by-default/toggle/persist/manual-pan-
  disables/Latest-re-enables; live tail updates respecting Auto Follow
  ON vs OFF. Also hardened `chart_check.py` itself: the extended-hours route
  stub could occasionally double-fulfill the same request (a pre-existing
  test-harness race, unrelated to app logic) — now defensively swallowed; and
  added a `window.__chNoAutoRefresh` test-only escape hatch so the chart's
  30s background refresh timer can't race a route stub mid-teardown once a
  suite run's wall-clock time exceeds that cadence.

## [Uncommitted] 2026-07-20 — V3.2.1: critical chart regression fixes

*Version 0.3.0 → 0.3.1. 387 tests, chart_check 31 → 33. Three release-blocker
regressions that the V3.2 tests reported as "fixed" but the real app still hit —
because the tests measured internal state, not what the user sees. The V3.2.1
tests assert user-visible behaviour (actual coordinates, actual viewport).*

- **Drawings STILL disappeared across timeframes (Bug 1).** V3.2 made the
  visibility *filter* timeframe-independent, and the test checked
  `chDrawVisible().length` — which passed. But a drawing anchored on 1m has bar
  times that are NOT bars on 5m/1d, so `chX()` fell through to
  `timeToCoordinate()`, which returns null for a non-bar time → the drawing
  painted nothing (painted_px = 0). Root cause verified by pixel count, not the
  filter. Fix: `chX()` maps any timestamp to a fractional logical index
  (`chLogicalAt`, interpolating between bracketing bars) and — because the
  vendored lightweight-charts' `logicalToCoordinate` returns 0 for FRACTIONAL
  indices but maps INTEGER ones fine (even off-screen, extrapolating) —
  interpolates the pixel between the two bracketing integer-bar coordinates.
  Drawings now render on every timeframe.
- **Timeframe switching lost chart context (Bug 3).** V3.1-RC3 made switches
  `fitContent` (to kill a stale-zoom bug); that threw the user's place away — a
  switch jumped to an unrelated date (measured 82-day drift). Fix: capture the
  focal date region before the switch and re-center the new resolution on it
  (`chCaptureFocal`/`chApplyFocal`), clamping each endpoint to the nearest real
  bar so a finer timeframe's shorter history lands on the "closest candle"
  instead of exploding the window. Recent focal now preserved with ~0 drift; a
  focal older than the destination's history lands on its earliest bar.
- **Viewport auto-reset fought the user (Bug 2).** A same-key refresh restored
  the *time* range, which is null when panned into whitespace past the newest
  candle — so the chart snapped back. Fix: same-key refresh preserves the
  LOGICAL range (always defined), captured before `setData`. The stranded
  auto-fit net now fires only on a symbol-switch/first-paint fallback, never
  over a refresh or a deliberate focal restore — Latest/Reset remain the only
  auto-recenters.
- **Root cause tying Bugs 1+3 together: history-load corruption.** `setData`
  fires the visible-range subscription (its auto-fit) *before*
  `restoringViewport` was set, so `chMaybeLoadHistory` ran mid-switch and
  prepended history, shifting logical indices and corrupting drawings AND the
  viewport (n grew 468→806→1248). Fix: set `restoringViewport` before `setData`,
  and disarm history on a switch (history loads on a user SCROLL, never on a
  timeframe/symbol switch).
- **Tests.** `chart_check` 9b now asserts a drawing's anchor coordinates
  RESOLVE (finite, distinct) on every timeframe — it fails on the old x=0 bug.
  New 9d (focal-date preserved across a cascade, no jump/sliver) and 9e
  (viewport not yanked by a refresh while panned past newest; Latest works).

## [`62cbcb4`+`409cfc0`+`9721e1f`] 2026-07-19 — V3.2: chart-system completion + Extended Hours

*Version bumped 0.1.0 → 0.3.0. 387 tests, chart_check 29 → 31. The final
evolution of the chart subsystem before Replay Mode / AI Visualization /
Mobile / Broker work begins.*

- **Timeframe-independent drawing engine (PARTS 1/2/5).** Drawings used to
  vanish on a timeframe switch because the model was tf-LOCKED
  (`chDrawVisible` filtered on `it.tf === CH.tf`). The v3 model stores each
  drawing once with a `visibility` policy ("all" by default, or {min,max}
  tf-rank bounds), `createdTf` metadata, a `source` tag (user/ai/replay), and
  freeform `meta`; the renderer decides per-timeframe whether to show an object
  and never mutates or destroys it on a switch. Legacy v1/v2 drawings migrate
  to visibility "all". One creation entry point — `chAddDrawing(spec)`, exposed
  on `window` — serves the user tools today and the AI scanner / replay engine
  later, so there is exactly one drawing engine and no parallel implementations.
- **Ray tool (PART 2).** Two-click, starts at the first point, passes through
  the second, and extends infinitely past it (clamped to the canvas edge). It
  reuses the existing select/drag/endpoint/recolor/width/lock/hide/duplicate/
  delete/persist machinery — no isolated implementation.
- **Extended Hours (PART 4).** Confirmed first that yfinance reliably supplies
  pre-/after-market candles via `history(prepost=True)` for every intraday
  interval (04:00–20:00 ET). `extended_hours` is a display-only opt-in threaded
  provider→cache→`candles_payload`→`/api/candles?ext=1`, kept off the trading
  path so paper execution is unchanged; ext frames are cache-keyed separately
  and bypass the on-disk store. `optionspilot/data/sessions.py` classifies each
  bar (pre/rth/post) by US-Eastern time; the payload tags bars and computes
  indicators on the session-correct series. Frontend: an "Ext" toggle
  (persisted, disabled on daily) plus TradingView-style pre-market/after-hours
  shading on the overlay canvas. Architecture is provider-agnostic so a future
  feed (Polygon/broker) can supply the same data without a chart rewrite.
- **Version (PART 8).** `0.1.0` → `0.3.0` in pyproject + `__init__`; the footer
  and About surface read it from `/api/status`.

## [`60f16a4`] 2026-07-18 — V3.1 RC3: final release blockers

*376 tests, chart_check 27 → 29. Reproduced the user's exact manual
workflows before touching code; each fix has a browser test that fails
before it and passes after. The key process lesson: RC2's toolbar test set
`DRAW.sel` in JS, bypassing the real select→click path, so it couldn't have
caught what the user hit — the RC3 tests drive the real mouse.*

- **Drawing toolbar actions "still broken" — root cause was a STALE EXE, not
  the source.** Driving the *real* mouse (draw a trendline, click it to
  select, click the toolbar) confirmed the source fix works. But the shipped
  `dist/OptionsPilot` bundle was built Jul 18 12:02 — before RC1/RC2 — so its
  `index.html` has none of the toolbar/viewport/banner fixes. On that build,
  select/drag/resize work while recolour/duplicate/lock/hide/delete no-op —
  exactly the reported symptoms. Fix: **the exe was rebuilt** from fixed
  source. The regression test was rewritten to drive the real mouse end to
  end (draw→select→recolour/width/duplicate/lock/hide/delete), verified to
  fail on the pre-fix source (colour unchanged, selection cleared) and pass
  after.
- **"Live data unavailable" appeared far too often — banner flapping.** With
  the market open and the feed intermittently rate-limited (stale/fresh/
  stale), the yellow banner re-raised on every stale tick even though the
  newest bar never changed — the same current data, warned about repeatedly.
  Root cause: the banner keyed off each fetch's instantaneous `stale` flag.
  Fix: a per-(symbol·tf) high-water mark (`CH.freshHigh`) of the newest bar
  we've shown from a successful fetch; a stale payload only warns when its
  newest bar is genuinely OLDER than that. Same-or-newer ⇒ we still hold the
  current data ⇒ no banner. Verified: alternating stale/fresh on the same bar
  now yields zero banner re-shows (was 4/8); a genuinely-older bar still
  warns.
- **Timeframe switching zoomed into a sliver (one candle).** Each (symbol·tf)
  cached its own viewport and restoring it on switch snapped the chart back to
  whatever tight zoom you last left there — so 5m→1m→5m dropped you onto ~5
  candles. Fix: viewport restoration now has exactly ONE owner — a switch
  (symbol or timeframe) always lands on a sensible default (fit); only a
  same-key refresh preserves the live viewport. The per-key viewport cache was
  removed entirely (dead once switches stopped restoring it). Verified: every
  switch across 1m/2m/3m/5m/…/1d now shows tens-to-hundreds of bars, never a
  sliver.
- **Stuck loading-overlay / skeleton legend on a rapid symbol burst.** Found
  while hardening the tests: a rapid switch (SPY→…→QQQ) where earlier
  first-paint symbols raise the "loading" overlay + skeleton legend, and the
  final already-cached symbol's refresh comes back empty, left the overlay and
  legend stuck even though data was on screen. Fix: a non-first-paint load now
  clears the overlay and restores the legend from the data it already holds.
- **Tests.** `chart_check.py` → 29 checks: the real-mouse toolbar test
  (replacing the JS-state one), an anti-flap banner test, and a
  timeframe-switch tiny-zoom test; the stale-banner test now simulates a
  GENUINELY-behind feed (dropped trailing bars) and its routes serve one
  captured payload instead of re-hitting the live feed (deterministic).

## [`6f3643d`] 2026-07-18 — V3.1 RC2: final chart release audit

*376 tests (+2), chart_check 27 (+6). The last stabilization pass before
`v3-ui` merges to `main`. Four remaining chart bugs, each reproduced in a
real browser, root-caused, fixed at the architecture level, and re-verified.
No redesign, no new chart library, no feature work.*

- **Drawing edit-toolbar actions were dead** (recolour / duplicate / lock /
  hide / width / delete all no-op'd). Root cause: the edit toolbar floats
  *inside* `#ch-main`, so the capture-phase `pointerdown` there fired before
  a toolbar button's own click. The click landed on the toolbar, not on the
  drawing, so `chPointerDown` took its "clicked empty space → deselect"
  branch and cleared `DRAW.sel`; by the time the button handler ran,
  `chSelItem()` was null. Fix: the capture handler ignores events originating
  in `#ch-draw-bar`, leaving the selection intact for the control's handler.
- **The "Live data unavailable — showing cached bars" banner over-fired.**
  It shows whenever a live fetch fails and disk-cached bars are served, but
  while the market is CLOSED those cached bars already ARE the last session —
  identical to what a live fetch would return — so the banner is a false
  alarm that flaps every time a background refresh trips Yahoo's rate
  limiter. Fix: `/api/candles` now reports `market_open` (computed from the
  existing `Orchestrator.market_open`); the frontend suppresses the banner
  while closed (a closed market shows a normal last-session chart, exactly
  as the non-stale closed case does) and shows it — a genuine "you're behind
  live prices" warning — only while open. Two backend tests lock the field
  and the stale-path behaviour in.
- **The chart could strand the user.** Lightweight-charts clamps pan/zoom so
  the view is never literally empty, but bars could still be shoved to the
  far edge behind a screen of whitespace ("the chart disappeared"). Added
  **Reset view** (fit all bars) and **Latest** (jump to the newest bar)
  controls, bound to **R** and **L**; a whitespace-aware `chViewportStranded()`
  detector (logical-range based, so it survives the whitespace case that
  makes `getVisibleRange()` null) that distinguishes a stranded view from a
  deliberate deep zoom; and a render-time safety net that fits content when a
  restored viewport lands off-data — but never on a same-key refresh, so it
  can't yank a viewport the user chose.
- **Random viewport jumps on indicator toggle.** Enabling RSI/MACD recentred
  the main chart: the two-way main↔subpane range sync let a freshly-created
  pane's auto-fit-on-first-`setData` (full history) push its range back onto
  main. Fix: the **main chart is the sole owner** of the visible time range;
  subpanes are one-way followers, realigned to main's range after their data
  is set (`chAlignPane`). Reproduced as [166,205] → [0,191] before the fix,
  unchanged after.
- **Tests.** `chart_check.py` +6 checks: toolbar actions mutate the object,
  indicator toggle leaves the viewport put, Reset/Latest rescue a stranded
  chart, the banner tracks market state, a rapid-abuse stress burst stays
  render-clean, and a new-bar append (the market-hours rollover) grows the
  series without a view jump. `test_ui_server.py` +2 (`market_open`).
- **Performance / market-hours audit.** Evidence-gathered, not speculative:
  one candle request per symbol load (no duplicate fetches), stable canvas
  count across 15 Trade↔Charts reparents (no leak), single chart instance;
  the pane-sync fix also removed a viewport-churn feedback loop. The live
  forming-candle / new-bar / indicator paths were exercised with simulated
  ticks (market closed); see PROJECT_STATE for the market-hours checklist.

## [`3a56145`] 2026-07-18 — V3.1 RC1: stabilization & hardening polish

*374 tests (+1). A release-candidate polish pass over the V3.1 chart work —
no new features, no redesign. A full code/stability/performance audit,
fixing only legitimate findings; the chart architecture is unchanged.*

- **Dead code removed.** The four `CH.priceLines`/`trendSeries`/`fibLines`/
  `rectSeries` arrays were orphaned when V3.1-4 moved drawings onto the
  overlay canvas — declared but never referenced; removed. No source
  TODO/FIXME/XXX markers exist, and every chart/trade function is called.
- **localStorage corruption can't brick the app or the chart.** `chInds`
  (parsed at script-eval) and per-symbol chart drawings (parsed mid-render)
  went through bare `JSON.parse`; a corrupt/hand-edited value threw — the
  first would fail app init, the second the chart for that symbol. A new
  `safeParse` helper resets a bad key to its default and continues.
- **Refreshes never yank the chart out from under an interaction.** The 30s
  auto-refresh (and the new visibility refresh) now skip while a drawing is
  being dragged, a two-click tool is mid-placement, a note is being typed,
  or history is loading — a full re-render then would have moved the bars
  under the cursor. It runs on the next idle tick.
- **Prompt refresh on wake.** After minimize/sleep/tab-away (when
  `document.hidden` suppressed the interval), a `visibilitychange` handler
  refreshes the chart on return instead of waiting up to a full cadence.
- **Bounded payload cache.** The per-(symbol·timeframe) payload cache was
  unbounded — each entry can hold a full paged candle payload (hundreds of
  KB), so a long session flipping through many symbols grew memory without
  limit. It's now an LRU capped at 24 entries (a re-fetch on eviction, never
  a correctness issue).
- **WebSocket frame parse hardened.** `ws.onmessage`'s `JSON.parse` is now
  guarded (a malformed frame is ignored, not thrown). Confirmed the server
  resends a full payload on every new connection (its change-digest starts
  empty), so a dropped-and-reconnected client catches up automatically — a
  new backend test (`test_ws_sends_full_payload_then_heartbeats_when_idle`)
  locks that contract in.
- **Regression coverage expanded.** `chart_check.py` gains two checks
  (corrupt-localStorage recovery, LRU cache bound) → 21 headless-browser
  checks; the WS-contract backend test brings the suite to 374.

Live market-hours items remaining (architecture verified capable; only real
market data can confirm) are enumerated in the RC1 manual-validation
checklist: forming-candle updates, new-bar creation, indicator recompute,
price/stop/target line movement, and option-chain refresh cadence (the
chain currently refreshes on demand — symbol change / Load / expiration /
post-order — with no auto-refresh timer, a deliberate choice to revisit if
market-hours use warrants it).

## 2026-07-18 — V3.1: chart-system stabilization sprint (`v3-ui`)

*373 tests (+17). A dedicated sprint making the charting system
production-ready — the strongest part of the app instead of the weakest.
Seven committed milestones, each root-caused (not papered over) and
browser-verified before commit.*

- **V3.1-1 chart reliability (`b93eac9`).** Root-caused the "some tickers
  randomly fail / IWM only shows volume" reports. Three distinct causes,
  each reproduced first: a stored drawing with a stray price drove the
  price scale and crushed the candles to a ~4px line (fixed with
  `autoscaleInfoProvider: () => null` — drawings never scale the axis);
  NaN volume on the forming bar raised in `int()` and, worse, during JSON
  serialization *after* the endpoint try/except, 500-ing the chart
  (`validate_candles` now zeroes non-finite volume, drops NaN/inf/≤0 OHLC
  rows, and logs every removal with symbol/timeframe context); and
  non-finite values poisoned computed indicators (payload runs through a
  single `validate_candles` choke point and `math.isfinite` guards). The
  frontend render block is wrapped so a renderer exception surfaces an
  error overlay instead of a half-painted canvas.
- **V3.1-2 expanded timeframes (`0d2c870`).** 1m/2m/3m/5m/10m/15m/30m/
  1h/2h/4h/1d/1w/1mo (6 → 13), table-driven so a new interval is one line
  per layer. A single `_TF_LABEL` table is the source of each wire label;
  `_FETCH_SPEC` maps to native yfinance intervals + resample rules (3m/10m/
  2h/4h are resampled); `_WINDOW_DAYS`/`CANDLE_TTL` gain matching entries;
  a test fails if any enum member isn't wired in all four.
- **V3.1-3 infinite historical scroll (`98551e1`).** The paging machinery
  existed but the merge was inverted — scrolling left *replaced* the
  window with an older one (206 → 202 bars) — and the trigger compared a
  logical bar-index against a Unix timestamp. Fixed: older bars are
  prepended in front of the visible ones with indicator series in lockstep
  (206 → 407), the trigger fires within N bars of the left edge, viewport/
  zoom/drawings are preserved, and a left-edge pill shows "Loading history"
  / "Start of available history".
- **V3.1-4 editable drawing objects (`917d0c9`).** Replaced one-shot,
  uneditable drawings with a first-class object model
  (`{id,type,tf,points,color,width,text,locked,hidden}`, stored
  `{version:2,items:[]}`, old format migrated) rendered on a `#ch-draw`
  overlay canvas: select, drag-move, endpoint-resize, color, width, lock,
  hide, duplicate, rename (notes), delete, persistence. Tools arm
  synchronously (instant). Interaction runs on capture-phase pointer
  listeners that freeze chart pan only while a drawing is grabbed.
- **V3.1-5 collapsible synced Trade chart (`edfe2bc`).** The one chart
  instance is relocated between the Charts tab and a collapsible Trade-tab
  slot, so symbol/timeframe/drawings/indicators are shared for free.
  Hidden by default, preference remembered; follows the ticket symbol.
- **V3.1-6 live-update correctness + performance (`5e04506`).** The
  refresh signature hashed only bar times, so the forming candle froze
  during market hours; `chSig` now includes the last bar's OHLCV. A
  live-update fast path pushes only updated/appended trailing bars via
  `series.update()` — no setData, no reflow, zero flicker; full setData is
  reserved for symbol/tf switches, history prepends, and window slides.
- **V3.1-7 automated chart regression suite (`2bcb84a`).**
  `scripts/chart_check.py` drives 19 headless-browser checks (loading,
  invalid ticker + recovery, all 13 timeframes, indicators, the full
  drawing lifecycle, scroll-back, zoom, stale banner + retry, rapid symbol
  changes, resize, live update, single-instance leak guard); wired into
  `scripts/verify.ps1`. Verified: all 10 required tickers × 13 timeframes
  return monotonic real data (130/130).

## 2026-07-18 — Packaged exe shipped without yfinance: lazy import invisible to PyInstaller

*Release-blocking regression found by the user in the
freshly built exe: every chart, quote, and option-chain request failed
with "No module named 'yfinance'". Root-caused and fixed at the packaging
layer; no trading logic changed.*

- **Root cause.** The performance pass (`f1bae42`, pre-V3, on `main`)
  deferred the yfinance import behind `importlib.import_module()` to cut
  app startup from 3.1s to 0.9s. PyInstaller discovers dependencies by
  statically scanning `import` statements, so the dynamic import is
  invisible to it — from that commit on, every exe built by
  `scripts/build_exe.ps1` silently omitted yfinance (and its entire
  dependency tree, including `curl_cffi`) from the bundle. The build
  itself cannot fail for this: the missing module only surfaces at
  runtime, on the first data request. It stayed latent because no
  post-`f1bae42` exe had its data path exercised until now — the V3
  pre-merge audit rebuilt the exe and verified the build *completed*,
  but the market was closed and launching the exe was left as a listed
  manual test. Not a V3 UI regression: V3-0's error surfacing is what
  made the failure visible (verbatim error + Retry) instead of a
  silently blank canvas, and the dev venv was never affected.
- **Fix.** `--collect-all yfinance` in `scripts/build_exe.ps1`, which
  also pulls yfinance's own (statically declared) dependency tree.
  Verified: `yfinance 1.5.1` and `curl_cffi` physically present in
  `dist/OptionsPilot/_internal`; the rebuilt exe serves 206 daily SPY
  candles, 624 SMCI 5m candles (the exact request from the failure
  logs), and a 231-contract chain, `stale: false` throughout.
- **Why it can't silently regress.** Three independent guards: (1) a new
  `selftest` CLI command (`OptionsPilot.exe selftest`) forces every
  lazily-imported dependency offline and exits nonzero if one is
  missing; `build_exe.ps1` now runs it against the freshly built exe and
  fails the build on a bad bundle. (2) New `tests/test_packaging.py`
  scans the source tree for dynamic third-party imports and fails the
  ordinary test suite if any isn't explicitly collected by the build
  script — catching the bug class (someone adds another lazy import)
  long before anyone builds. (3) A companion test asserts the build
  script still *runs* the packaged selftest, per the "a gate that isn't
  wired in protects nothing" lesson.
- **Chart-system regression sweep** (all green, zero console errors):
  first-load overlay → candles, rapid timeframe-switch race, invalid
  symbol → error overlay → Retry → recovery, indicators
  (EMA/VWAP/BB/RSI/MACD), fib/zone/note drawing tools + persistence +
  clear, position/order price lines, stale-banner path (forced via
  request interception) + return-to-live, and the 30s auto-refresh.
- **Known limitation discovered en route** (pre-existing, not fixed
  here): `OptionsPilot.exe serve` — the *windowed* exe running the
  browser-serve subcommand — starts its internals but never binds the
  port. Desktop `ui` mode (the default; verified) and dev-repo
  `python -m optionspilot serve` (verified) are unaffected. Logged in
  `TODO.md`.

## 2026-07-17 — V3-7: pre-merge audit — cache threading bug, chart auto-retry, key guard

*352 tests (+1). A senior-review pass over the whole `v3-ui` changeset
before merging to `main`, which found and fixed three real issues:*

- **`CandleCache` was unusable from worker threads — the disk candle
  cache silently never worked in the live app.** `sqlite3.connect()`
  defaults to `check_same_thread=True`, and the connection is created on
  the main thread — but every candle fetch in serve/desktop mode runs on
  a ThreadPoolExecutor worker (parallel scans) or a FastAPI threadpool
  thread (`/api/candles`). Every cross-thread `store`/`load` raised
  `ProgrammingError`, which callers' best-effort `except` blocks
  swallowed. Consequences: warm restarts always re-downloaded, and
  V3-0's stale-chart fallback would have returned empty in production
  (blank-chart error state instead of clearly-flagged stale bars).
  Fixed with `check_same_thread=False` plus an explicit lock serializing
  all connection use; proven with a before/after cross-thread script and
  a new multi-threaded regression test (`test_usable_from_other_threads`),
  plus an end-to-end reproduction of the production scenario (provider
  built on main thread, dead network, stale fallback served correctly
  from a worker thread). Pre-existing bug — exposed because the audit
  traced V3-0's fallback path all the way down.
- **Chart auto-retry never fired for a failed *first* load.** The 30s
  refresh loop gated on "data has loaded at least once," so the exact
  scenario V3-0 exists for (app opens, feed down, error overlay shown)
  recovered only via the manual Retry button. The gate is now "chart
  initialized," verified by watching the retry request fire from the
  error state in a real browser.
- **Enter could submit an order from behind the `?` shortcut overlay.**
  The order-entry key guard checked only the confirm modal; it now also
  suppresses B/S/+/−/Enter while the shortcut reference is open.
  Browser-verified both ways.

## 2026-07-17 — V3-6: accessibility & discoverability — skip link, live regions, ? overlay

*351 tests (unchanged — markup/CSS/small JS only).*

- **Skip-to-content link** (first focusable element, visible on focus).
- **Toast messages are now a polite live region** (`role="status"
  aria-live="polite"`) — order fills, rejections, and mode switches get
  announced to screen readers instead of appearing silently.
- **All 51 table headers** across every screen now carry `scope="col"`.
- **`aria-current="page"`** tracks the active nav tab (statically for the
  initial Dashboard state, dynamically on every switch).
- **`?` shortcut-reference overlay**: every keyboard affordance in the
  app (tabs, chart, order entry, watchlist) on one card; Esc or a click
  outside closes it.
- **Watchlist drag-handle affordance**: the ≡ handle now brightens on
  row hover instead of sitting permanently faint — the row's
  drag-to-reorder capability is visible before reading the caption.
- **Verified** in a real browser: ?-overlay open/close, aria-current
  follows tab switches, toast live-region and skip link present in the
  DOM; full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-5: analytics presentation — Coach, Journal, Backtest, Learning

*351 tests (unchanged — all four are frontend presentation over existing
endpoints; every new chart/branch is derived client-side).*

- **Coach**: a first-run explainer replaces the bare "0 reviews" stat —
  three numbered steps (trade in Human Mode → close a round trip → get
  scored on process) with a one-click "Switch to Human Mode" button; it
  disappears permanently once the first review exists.
- **Journal**: a cumulative-P&L curve panel (appears from the second
  closed trade), plus symbol/direction/win-loss filters with a live
  "N of M trades" count — all over the already-loaded trade list, no new
  requests.
- **Backtest**: results now include a drawdown-from-peak chart, a
  win/loss split on the trades card, and a "by exit reason" breakdown
  (count, win rate, total P&L per exit type) — the shape of report a
  desk would actually read. Verified by running a real 25-day SPY
  backtest through the UI (zero trades at the conservative bar is the
  correct outcome; the full layout renders).
- **Learning**: the evidence-weights table gains a centered shift bar per
  row — green right of center where learning has boosted a weight above
  its default, red left where it damped one — making the bounded
  0.25×–2× learning rule visible at a glance.

## 2026-07-17 — V3-4: settings redesign — structured config cards replace the JSON dump

*351 tests (unchanged — frontend only; the config stays read-only in-app
by design, matching the startup-validated `config.yaml` philosophy).*

- The raw `JSON.stringify` dump — the app's single biggest visual outlier
  (flagged in `ROADMAP-V3-UX.md` as C1) — is now a grid of grouped cards,
  one per config section (data/indicators/engine/risk/broker/notify/
  integrations/logging), each with a plain-English description of what
  the section controls and where its live counterpart lives (e.g. "the
  watchlist itself is managed on the Watchlist tab").
- Booleans render as ✓ on / – off; the two live-trading gate flags render
  as 🔒 off with a tooltip stating they're off by design with no live
  adapter in the build — the safety posture is now visible in the UI, not
  buried in a JSON blob.
- A search box filters across every section/key/value, hiding empty
  cards as it narrows.
- The restart-to-change rule is stated once, inline, next to the search —
  only where it's actually true (the Trading-mode panel above it remains
  fully live, unchanged).
- **Verified**: lock rendering and search behavior driven in a real
  browser (search "confidence" → 1/69 rows, only the engine card left);
  full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-3: trade screen — faster contract selection, risk context, order-entry keys

*351 tests (unchanged — frontend only; all order placement still routes
through the existing risk-gated `/api/orders` path).*

- **ATM quick-picks**: the ticket placeholder is no longer inert text —
  once a chain is loaded it offers "Nearest ATM call/put" buttons that
  select the closest-to-the-money contract in one click (with the
  Calls/Puts toggle following along).
- **Risk context in the ticket**: below the estimated cost, a live line
  shows the order as a % of buying power alongside the configured
  per-trade risk budget, turning amber when the order exceeds it —
  advisory only; the backend gate remains authoritative.
- **Open positions on the Trade tab** (new): a compact live list (You/AI
  chip, unrealized P/L) with a "Close…" action that loads the position's
  own expiration chain, selects its exact contract, and arms the ticket
  as sell-to-close with the position's quantity.
- **Order-entry keyboard shortcuts**: with the Trade tab open and a
  contract selected — B/S switch side, +/− step contracts, Enter opens
  the review modal (Esc already closes it). Documented inline under the
  ticket. Guarded against firing while typing in a field or while the
  confirm modal is open.
- **Verified** end-to-end in a real browser: chain load → ATM pick →
  risk line → keyboard flow → Enter → confirm modal → a real submission
  (correctly and *visibly* rejected by the manual-entry risk gate outside
  trading hours — the gate's toast surfaced as designed) → close-prefill
  flow. Full suite + browser smoke check green, zero console errors.

## 2026-07-17 — V3-2: dashboard redesign — trader-first layout, live side rail

*351 tests (unchanged — presentation + one new derived view over existing
status data; no new endpoints, no trading logic).*

- **Two-column layout** (`.dash-grid`, 2:1): main column — equity curve,
  open positions, the per-symbol AI-confidence meters; side rail — three
  new glanceable panels. Collapses to one column below 1000px.
- **AI opportunities** (new): the strongest current signals sorted
  tradeable-first then by confidence, each with direction chip,
  confidence %, and gate state ("✓ tradeable" / "needs N%") — click opens
  that symbol's chart. Derived entirely from the existing status payload.
- **Watchlist movers** (new): biggest daily changes first, price + colored
  ▲/▼ change, click-through to the chart. Uses the per-cycle quote
  snapshots the orchestrator already publishes.
- **Empty states now teach and act**: equity ("Run a scan now" button),
  positions (mode-aware: "Scan for setups" in AI Mode, "Open the Trade
  tab" in Human Mode), opportunities ("Scan the watchlist"), movers.
  Previously all four were inert one-line texts.
- **Verified**: populated end-to-end by running a real scan cycle in the
  scratch browser session (opportunities, movers, meters all live), plus
  full suite + browser smoke check, zero console errors.

## 2026-07-17 — V3-1: design system foundation — tokens, icon nav, responsive layout

*351 tests (unchanged — presentation only). Second V3 milestone: the shared
visual language every subsequent screen redesign builds on, plus a real
layout-overflow bug found and fixed by the new narrow-viewport check.*

- **Design tokens** (`:root`): a nine-step type scale (`--fs-xs`…
  `--fs-hero`) replacing ~75 ad hoc pixel font sizes (13 distinct values
  consolidated to 9 with sub-pixel-class visual drift); a spacing scale
  (`--sp-1`…`--sp-6`); three elevation levels (`--sh-1` resting cards,
  `--sh-2` popovers, `--sh-3` modal/toast) now applied to panels, cards,
  the autocomplete popover, the confirm modal, and toasts; `--r-pill`.
- **Icon navigation**: nine hand-authored inline SVG stroke icons
  (offline-safe, `currentColor`, no icon font or CDN) added to the nav
  rail alongside the labels.
- **Responsive collapse**: below 1180px the sidebar becomes a 56px icon
  rail (tooltips carry the labels, the logo shrinks to "OP", the PAPER
  TRADING badge turns vertical) — the header pills now fit on one row at
  1024px instead of wrapping and clipping.
- **Real bug fixed — flex/grid min-width blowout**: `main` is a flex item,
  and its implicit `min-width:auto` let the option-chain table push the
  whole layout wider than the viewport at ≤1280px (header clipped off
  screen rather than wrapping — pre-existing, exposed by the first
  narrow-viewport screenshot of the Trade tab). Fixed with `min-width:0`
  on `main` and `minmax(0,1fr)` grid columns; wide tables now scroll
  inside their own panel, never the page.
- **Verified**: 351 tests, HTML id check, browser smoke check (9 tabs,
  zero console errors), plus before/after screenshots at 1024/1280/1600px.

## 2026-07-17 — V3-0: chart reliability — root cause fixed, never-blank canvas

*351 tests (+6). First milestone of the V3 product-quality sprint (branch
`v3-ui`, planned in `ROADMAP-V3-UX.md`). The app could open with no usable
chart; instrumented diagnosis (not guesswork) found a three-part root
cause, each part fixed and separately verified.*

- **Root cause 1 — negative-cache poisoning (`data/cached.py`)**: yfinance
  returns an *empty frame* on transient failures (rate limits, hiccups) —
  indistinguishable from "no data" — and `CachedProvider` memoized that
  empty for the full timeframe TTL (up to 60s), so healthy retries kept
  being served the failure. Proven with a controlled fake provider before
  fixing. Empty results now expire in `EMPTY_CANDLE_TTL` (3s) — long
  enough to stop a hammering loop, short enough that recovery is instant.
  Good data keeps the full TTL.
- **Root cause 2 — no stale fallback**: the SQLite candle cache was never
  consulted when the live fetch failed, so disk full of yesterday's bars
  still meant a blank chart. New `CachedProvider.get_candles_stale_ok()`
  (display surfaces only) falls back to disk data of any age, flagged
  `(frame, is_stale)`. The strict `get_candles` path is byte-for-byte
  unchanged for the engine — fail-closed trading semantics preserved and
  covered by a test asserting the strict path still returns empty in the
  exact state where the stale path serves data. `/api/candles` now
  reports `stale`/`as_of`, and the Charts tab shows a warning banner with
  the last bar's date and a "Retry live data" button.
- **Root cause 3 — frontend failure handling**: `loadChart()` had no
  `catch` (a network error left a stuck skeleton and a blank canvas
  forever), no retry affordance, and a `CH.loading` guard that silently
  dropped symbol/timeframe switches issued mid-load. Rewritten around a
  request generation counter: the newest request always wins, rapid
  switches can't interleave or be dropped, every failure path lands in a
  visible state — a loading overlay (spinner + symbol) on first paint, an
  error overlay with a Retry button on failure, and for an
  already-rendered chart a stale banner instead of wiping the canvas.
- **Live refresh**: a visible chart now refreshes every 30s (cadence
  matched to the backend candle TTLs), preserving zoom/pan (`fitContent`
  only on symbol/timeframe change), pausing when the tab is hidden, and
  doubling as an automatic retry after failures. Drawing/trade-line
  restore paths were audited for idempotency under the refresh loop (both
  already fully remove before re-adding — no series leaks).
- **Verified**: full suite green (351), plus a 5-scenario Playwright
  run in real Edge — first-load overlay→candles, rapid-switch race
  (last click wins), invalid-symbol error overlay with working Retry,
  recovery to a valid symbol, and a same-key refresh keeping the chart —
  zero console errors.

## 2026-07-17 — Developer automation: scripts/, browser checks, doc-consistency checks

*345 tests (unchanged — no trading logic touched, per the session's explicit
scope). A repository-wide review for repetitive manual developer tasks,
turned into a `scripts/` automation layer with one clear responsibility per
script.*

- **`scripts/_common.ps1`**: shared bootstrap (`Ensure-Environment`) that
  every other script dot-sources — creates `.venv` if missing, installs the
  package editable with the requested extras, idempotent.
- **`scripts/dev.ps1` / `test.ps1` / `verify.ps1` / `docs.ps1` / `build.ps1`
  / `release.ps1` / `clean.ps1`**: start the app, run tests (with an
  exit-code-derived `TESTS: PASS`/`FAIL` line that can't be fooled by the
  documented terminal-output-swallowing trap), run every automated check in
  one command, check documentation consistency alone, build the exe
  (test-gated, wraps the untouched `build_exe.ps1`), run the full
  release-readiness pipeline (never commits/tags/pushes — prints the exact
  manual commands instead), and remove dev/build clutter without touching
  `data/`/`logs/`.
- **`scripts/check_html_ids.py`**: the static `index.html` `$("id")`
  reference check, previously ad hoc, now committed.
- **`scripts/check_docs.py`**: confirms every `docs/*.md` cross-reference
  resolves, that "current state" docs' claimed test counts match a live
  pytest count, and that `pyproject.toml`'s version agrees with
  `optionspilot/__init__.py`'s. Caught a real stale example on its first
  run (`CLAUDE.md`'s commit-message template hardcoded `"296 tests"`) —
  fixed by making the example describe the process instead of a number.
- **`scripts/browser_check.py`**: a committed, repeatable version of prior
  sessions' ad hoc Playwright verification. Launches the app against a
  scratch data directory, drives the system's installed Edge
  (`channel="msedge"`, no download), visits every tab, fails on any
  console error. Soft-skips if the new optional `[browser]` extra isn't
  installed. Found a real bug on its first run: `/favicon.ico` 404ing was
  the only console error — fixed by copying `assets/optionspilot.ico` into
  `optionspilot/ui/static/favicon.ico` (already bundled everywhere
  `ui/static/*` is) and serving it. Also found and fixed a bug in itself:
  scratch temp directories weren't reliably cleaned up because a Windows
  file handle can linger briefly past `subprocess.wait()` returning —
  fixed with a bounded retry instead of silently swallowing the error.
- **`scripts/bump_version.py`**: keeps `pyproject.toml` and
  `optionspilot/__init__.py`'s version strings in sync — the same class of
  drift `check_docs.py` guards against for test counts.
- Two new optional `pyproject.toml` extras: `build` (`pyinstaller` —
  previously installed ad hoc and undeclared anywhere, the same gap
  `Pillow` had before an earlier session fixed it) and `browser`
  (`playwright`).
- New `docs/QUICK_START.md` (minimum steps to start productive work) and
  `docs/RELEASE_CHECKLIST.md` (the exact release process, automated where
  possible, explicit about what stays a manual, human-approved step).
- `CONTRIBUTING.md`, `AI_CONTEXT.md`, `ARCHITECTURE.md`, `TODO.md`,
  `PROJECT_STATUS.md`, `NEXT_SESSION.md`, `README.md`, and `CLAUDE.md` all
  updated to reference the new scripts consistently rather than the old
  raw commands, and to record the concrete lessons from building this
  (PowerShell's `2>&1`-plus-`-Stop` native-stderr trap; Windows subprocess
  file-handle timing) in `AI_CONTEXT.md` "Common mistakes to avoid."

## 2026-07-17 — Documentation & AI development framework

*345 tests (unchanged — a documentation-and-workflow session, no trading
logic touched). Commit `1029fb0`.*

- New `docs/PROJECT_STATUS.md`: a structured, dashboard-style snapshot
  (version, milestones, features, known bugs/limitations, priorities, test
  count) distinct from `PROJECT_STATE.md`'s session-by-session narrative.
- Rewrote `docs/ROADMAP.md` into a unified Completed / In Progress /
  Planned / Deferred / Long-term Vision structure, absorbing the stale
  v1-only content it previously had; `docs/ROADMAP-V2.md` stays as the
  detailed per-phase checklist it already was.
- Expanded `docs/ARCHITECTURE.md`: an explicit directory tree, five Mermaid
  diagrams (component map, AI-engine pipeline, risk-gate flowchart, cycle
  sequence diagram, build pipeline), and dedicated sections for Charts,
  WebSockets, Settings, and the build pipeline (including
  `optionspilot_app.py`, previously undocumented).
- New `docs/AI_CONTEXT.md`: the permanent-memory document — vision, design
  philosophy, standards, future desktop/mobile plans, and a "Common
  mistakes to avoid" section recording real incidents from this repo's
  history so they don't repeat.
- New `docs/NEXT_SESSION.md`: the concise session-handoff format (what was
  completed, what's stable, what's next, what files matter, what not to
  touch, known issues, a ready-to-paste first prompt).
- New `docs/CONTRIBUTING.md`: coding/commit/testing/documentation
  conventions, Definition of Done, pre-commit checklist, and a first pass
  at automation recommendations (superseded/expanded by the automation
  session above).
- Fixed real staleness found during the audit: `README.md` claimed "Phase 1
  of 8 complete" and 225 tests while omitting the Trade/Coach/Charts tabs
  entirely; `CLAUDE.md` and `AI_HANDOFF.md`'s reading-order pointers
  updated to route through the new files.

## 2026-07-17 — V2-4 finish: trade lines + fib/zone/note tools; manual entries risk-gated

*345 tests. Completes the tractable remainder of the V2-4 drawing/overlay
scope, and finishes the manual-entry risk-gating work found uncommitted
from the 2026-07-16 session. The full three-panel workspace layout and
multi-chart layouts stay deferred (a larger design decision — see
`ROADMAP-V2.md`).*

**Manual entries now pass through the RiskManager** (completing work left
uncommitted and unwired by the previous session — its `_entry_veto`
refactor, `approve_manual_entry`, and `OrderManager.evaluate`'s fill-time
`approve_entry` callback existed but the immediate market-buy path in
`UIServer.place_order` never called them, so a halted account could still
buy):
- `RiskManager.approve_manual_entry`: every hard gate the AI has — halt,
  weekend/hours window, daily trade limit, max open positions (skipped
  when scaling into an already-held contract), cooldown after loss, max
  contracts (counting the existing position) — plus quantity/premium
  validity. The engine's %-risk position sizing is deliberately advisory
  only for manual trades: sizing a user-directed trade is the user's
  call, and oversizing is the coach's job to flag (`oversized` tag), not
  the risk manager's to block. The %-budget comparison is still computed,
  logged, and surfaced in `RiskDecision.notes`.
- `UIServer.place_order` preflights immediate market buys through
  `Orchestrator.approve_manual_entry` (422 with the veto text), and
  delayed working-order fills are approved at trigger time by
  `OrderManager.evaluate`'s callback (rejected orders cancel with the
  veto as the result). Manual fills are recorded against the daily trade
  limit via `register_manual_entry(entry_ts=...)`.
- New `TestManualEntry` unit tests in `tests/test_risk.py` (halt, hours,
  daily limit, max-contracts scaling, oversize-allowed-with-note,
  invalid inputs) alongside the endpoint-level halt test.

- **Position/order lines on the chart**: loading a chart now draws labeled
  price lines for that symbol's open positions — entry spot (blue, solid),
  working stop (red, dashed) and target (green, dashed), all in underlying
  space (`Position.entry_spot`/`stop_current`/`target`) — plus the
  underlying-level triggers of working manual orders (stop-loss/take-profit
  levels and the live trailing-stop level, orange dashed). LIMIT orders are
  premium-space and deliberately not drawn on an underlying chart. Each
  line is labeled with the position/order size and strike (e.g.
  "stop 2× 580C"). Backend: the status payload's positions now include
  `entry_spot` (was already persisted on `Position`, just not exposed).
- **Three new drawing tools** alongside Level/Trend: **Fib** (click swing
  start then swing end → 0/0.236/0.382/0.5/0.618/0.786/1 retracement
  levels as labeled dotted price lines), **Zone** (click two corners → a
  supply/demand rectangle drawn as top/bottom edges spanning the two
  bars), and **Note** (click a bar, type text in an inline input, Enter →
  a labeled square marker above that bar). All persist in localStorage per
  symbol+timeframe like trend lines; the existing Clear button removes
  them; old stored drawings load unchanged (missing keys default empty).
- Esc now cancels the active drawing tool anywhere on the Charts tab.
- Hygiene (from `TODO.md`): `pyproject.toml` `package-data` now ships
  `data_assets/*` in wheels/sdists; `Pillow` added to the `dev` extra
  (needed by `scripts/make_icon.py`); `engine.operating_mode` documented
  inline in `config.yaml` matching `trading_mode`'s comment style.

## 2026-07-16 — V2-4 core: interactive chart workspace

*338 tests. The Charts tab ships the core of the V2-4 roadmap phase.*

- Vendored TradingView's lightweight-charts 4.2.3 (Apache-2.0) at
  `ui/static/lightweight-charts.js` — served locally, fully offline, no
  CDN, bundled into the exe by the existing `--add-data ui\static` line.
- New `GET /api/candles?symbol&tf`: OHLCV plus indicator series (EMA×3,
  VWAP, Bollinger, RSI, MACD) computed by the SAME `analysis/` functions
  the engine trades with — what you see charted is exactly what the scorer
  saw. Provider-only (no orchestrator lock), so chart loads never contend
  with a running scan; ~8ms warm through the CachedProvider.
- New Charts tab (keyboard: 2): candlestick + volume chart with zoom/pan/
  crosshair and an OHLC+change+volume+indicator legend, five timeframes
  (5m–1D), indicator pills (EMA/VWAP/Bollinger overlays, RSI and MACD as
  height-synced subpanes), fullscreen (F), and drawing tools — horizontal
  levels persisted per symbol, trend lines persisted per symbol+timeframe,
  one-click clear.
- Trade-from-chart plus deep links everywhere: watchlist symbols, dashboard
  confidence meters, and position cards all open the chart; "Trade →" jumps
  to the ticket with the symbol loaded.
- Workflow: after a market buy fills, the ticket pre-arms itself as a
  protective stop-loss (side/type preset, level focused) — the single most
  common coach finding (`no_stop`) is now one keystroke to avoid.
- Accessibility: visible focus rings, aria-labels on icon-only controls.

## 2026-07-16 — Performance & polish pass (no new features)

*335 tests. Scan cycle profiled and optimized end-to-end; Trade tab and
dashboard redesigned in a modern-brokerage style; UI never blocks during
scans. Soak: warm cycles 0.1s, zero heap growth.*

**Performance (measured 5-symbol watchlist):**
- Profiled the cycle first: 83% of the old 14.9s was 25 *serial* candle
  fetches gated by the provider's 0.5s self-throttle; the rest was
  re-running the full analysis suite on unchanged frames.
- New `data/cached.py` `CachedProvider`: timeframe-aware candle TTLs,
  5s quote / 30s chain / 1h expirations memos, concurrent-request dedup,
  write-through to the SQLite `CandleCache` (`data/cache.db`) for warm
  restarts. Wraps `YFinanceProvider` by default; fake test providers
  bypass it.
- `Orchestrator.fetch_watchlist_candles()`: all (symbol × timeframe) pairs
  fetch in parallel (8 workers) with a per-symbol progress callback;
  `run_cycle(candles=...)` accepts the prefetched frames. Provider throttle
  lowered 0.5s → 0.15s (request count is now tiny).
- `MultiTimeframeAnalyzer` memoizes one view per (symbol, timeframe) on a
  data fingerprint — unchanged frames skip the entire indicator/pattern/
  smart-money rebuild. `candlesticks.detect_all` computes shared bar
  geometry once instead of per-detector. `evaluate()` ~495ms → ~76ms cold,
  ~0ms warm.
- Cycle time: 14.9s → 4.5s cold, **~0.1s warm** (the "Scan now" case).
- `/api/scan` is now non-blocking by default: the cycle runs on a
  background thread, candle fetching happens OUTSIDE the orchestrator
  lock, and progress (`scan.done/total`) streams over `/ws`; watchlist
  quotes tick in per-symbol while the scan runs. `{"wait": true}` keeps
  the old synchronous behavior for scripts/tests.
- `/ws` pushes at 1s with change detection — full payload only when
  something changed, else a tiny heartbeat the frontend ignores. Journal
  reads for the status payload are cached by a new `TradeJournal.revision`
  counter instead of rescanning SQLite every push.
- Startup: yfinance import deferred to first use; core import time
  ~3.1s → ~0.9s.

**UI/UX (single-file, no-build architecture unchanged):**
- Modern brokerage restyle: refreshed dark palette and type scale, tabular
  numerals everywhere, hover/press transitions, tab-switch animation,
  reduced-motion support.
- Dashboard: portfolio-value hero with today's P/L; open positions as
  cards (big colored P/L, qty/avg/mark/stop/target, Close with a
  confirmation dialog).
- Trade tab redesign: horizontal expiration pills with DTE labels, sticky-
  header chain with colored bid/ask and an inline spot-price row marker
  (auto-scrolled into view), selected-contract card with large mid price,
  Buy/Sell segmented control, quantity stepper, live estimated cost/credit,
  and a full order-confirmation modal before anything is placed.
- Skeleton loaders on chain/journal/coach/learning/metrics; DOM writes
  diffed (`setHTML`) so unchanged sections never re-render; keyboard
  shortcuts 1–8 switch tabs.

## 2026-07-16 — V2-3: AI Mode vs Human Mode

*310 tests. Frontend live-verified in a real browser (mode toggle + persistence
across reload, Coach tab empty state, full manual round trip → coach review
rendered with expandable detail, mode-axis orthogonality) against a scratch
data directory before committing. Exe rebuilt with V2-3 and the packaged
app smoke-tested the same day.*

- `EngineConfig.operating_mode`: `"ai"` (default, autonomous trading) or
  `"human"` (AI scans and advises only; never places an order). Instant,
  no-restart switching via `RuntimeSettings.set_operating_mode()`,
  independent of `trading_mode`.
- `Orchestrator`: in Human Mode, tradeable signals become one-time "advice"
  notifications per bar instead of orders.
- New manual-trade reconciliation loop: detects opened/closed
  `managed_by="manual"` positions cycle-to-cycle, captures analysis context
  while open, rebuilds the round trip from broker fill + order history on
  close, journals it, and generates a `TradeCoach` review.
- New `optionspilot/coach/` package:
  - `coach.py` — `TradeCoach.review()`: before/during/after breakdown,
    14-tag mistake taxonomy (each with a pro-comparison note and a concrete
    exercise), **process-based** 0–100 score (deliberately rewards
    discipline over luck — a stopped-out loser with a plan outscores a
    reckless winner).
  - `profile.py` — `CoachProfile.build()`: aggregates all reviews into
    recurring mistakes, strengths, score trend, win rate by setup quality,
    recommended exercises.
- New API: `POST /api/operating_mode`, `GET /api/coach`.
- New UI: header AI/Human segmented toggle, Coach tab (cards, mistakes
  panel, strengths/exercises panel, expandable review detail).
- New tests: `tests/test_coach.py` (13 tests), `tests/test_human_mode.py`
  (mode switching + full manual round-trip integration).

## 2026-07-16 — V2-1 & V2-2: windowed desktop app + manual trading engine

*Commit `0ce001d`, roadmap update `bec78fb`. 296 tests.*

**V2-1 — true desktop application:**
- PyInstaller `--windowed` build: no console window on launch.
- Generated candlestick app icon (`scripts/make_icon.py` →
  `assets/optionspilot.ico`).
- Single-instance guard: a localhost-port mutex; a second launch shows a
  friendly notice window instead of two processes fighting over the same
  SQLite files.
- Logging skips the console `StreamHandler` when `sys.stderr` is `None`
  (true in a windowed build).

**V2-2 — order engine + manual trading:**
- New `broker/orders.py` `OrderManager`: MARKET, LIMIT (option premium),
  STOP_LOSS / TAKE_PROFIT / TRAILING_STOP (underlying price levels,
  put-aware direction mirroring), DAY (expires 16:00 ET) / GTC time-in-force,
  position scaling, reservation checks (prevents overselling across bracket
  orders), auto-cancel of exit orders when the position closes first,
  SQLite persistence with restart-safe fills (uses live quotes on restart,
  never stale stored prices).
- `Position.managed_by: "ai" | "manual"` — `PositionManager` (AI) now
  explicitly skips manual positions.
- `PaperBroker.open_manual()` — plan-less entry path for manual trades.
- Equity snapshots persisted per cycle for lifetime max-drawdown / total-
  return metrics.
- New API: `GET /api/chain` (option chain with Greeks + liquidity score for
  the order ticket), `GET/POST /api/orders`, `POST /api/orders/cancel`,
  `GET /api/account/metrics` (buying power, portfolio value, unrealized/
  realized/daily P/L, total return %, win rate, avg win/loss, profit factor,
  max drawdown).
- New Trade tab UI: account metric cards, live option chain browser, full
  order ticket (side/type/qty/TIF/limit-or-stop fields), working orders +
  history tables, one-click position close from the Dashboard.

## 2026-07-16 — Watchlist manager + in-app trading mode toggle

*Commit `0bc3955`. 272 tests.*

- New `config/runtime.py` `RuntimeSettings`: overlays `data/settings.json`
  onto the yaml-loaded config at bootstrap, mutates the live config object
  under the server lock so changes apply on the next cycle with **no
  restart**. Baseline snapshot (pre-overlay yaml values) lets `custom` mode
  restore exact yaml values when switching away.
- New `data/symbols.py` + bundled 12,472-symbol NASDAQ/NYSE directory
  (`optionspilot/data_assets/symbols.csv`) for instant, offline ticker validation and
  autocomplete search.
- Watchlist manager: quick-add with autocomplete, bulk paste parsing
  (comma/space/newline), per-symbol valid/duplicate/invalid reporting, 9
  preset lists (Magnificent 7, S&P 500 Leaders, AI Stocks, etc.) + saved
  Favorites, pin/drag-reorder/sort/filter, keyboard shortcuts, 30-symbol
  cap, background name + market-cap metadata fetch.
- Trading-mode segmented control (Conservative / High-Risk / Custom) in the
  header and Settings tab, with an advanced tuning panel for Custom mode
  (six validated risk/engine fields). Switches apply instantly and persist.
- Build script hardening: bundles `data_assets`, backs up/restores the exe's
  `data/` folder across rebuilds, refuses to build over a running instance.

## 2026-07-14 — Trading modes: Conservative and High-Risk

*Commit `70abb06`. 239 tests.*

- New `engine/gate.py` `TradeGate`: Conservative mode keeps the fixed
  `min_confidence` bar (default 80%). High-Risk mode adapts the required
  confidence to a deterministic *setup quality* classification (excellent/
  good/average/poor) built from evidence composition — poor setups
  (opposing HTF trend, 3+ conflicting indicators, or too few core
  confirmations) never trade at any confidence; entries below the
  conservative bar also require risk/reward ≥ a configurable threshold.
- Every gate decision produces a `GateReport` (quality, threshold used,
  passed/failed confirmations, one-line reason) that flows into logs, scan
  summaries, journal `market_conditions`, and the dashboard.
- Conservative mode's behavior is byte-identical to pre-existing behavior —
  this was an additive change, not a rewrite of the scorer.

## 2026-07-11 — Phase 8: hardening

*Commit `268cac9` (+ `30cd974`, `39640ee`). 225 tests.*

- `scripts/soak.py`: repeated live-cycle harness tracking exceptions, heap
  growth, and per-cycle timing — first run: 8 cycles, 0 failures,
  +0.2 MB heap growth, ~15.5s/cycle.
- `/webhook/tradingview`: secret-validated (constant-time compare),
  config-gated inbound alert endpoint. An alert only *triggers a scan* of
  that symbol through the normal engine + risk pipeline — it can never
  place an order directly.
- `broker/registry.py`: `create_broker()` factory with Alpaca/Tradier/
  Webull/IBKR extension slots that raise `BrokerError` with guidance rather
  than silently no-op-ing; the live-trading gate is re-checked at
  construction time as defense in depth.
- Performance: vectorized the smart-money detectors (numpy instead of
  per-row pandas/`iterrows`) and capped `MultiTimeframeAnalyzer` to the
  trailing 400 bars — backtest time on 520 bars dropped from 7.9s to 4.7s
  with identical trade output.

## 2026-07-11 — Initial commit: phases 1–7

*Commit `40eb1ea`. 204 tests.*

The original v1 build in one commit: multi-timeframe technical/structural/
smart-money analysis suite, confluence-scored AI decision engine
(`ConfluenceScorer`), risk-gated paper execution (`RiskManager` +
`PaperBroker`), SQLite trade journal, bounded/auditable learning system
(evidence-weight tuning from journal history), event-driven backtester
sharing the live engine code, orchestrator + desktop/email notifications,
full CLI (`run`/`scan`/`status`/`journal`/`backtest`/`learn`), and a packaged
desktop dashboard (FastAPI + pywebview + PyInstaller). Paper trading only —
no live-broker code path exists anywhere in this codebase by design.
