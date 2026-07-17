# CONTRIBUTING.md — development standards

Practical, human-contributor-facing companion to `CLAUDE.md` (which is the
authoritative rule set for AI coding sessions and covers some of this same
ground in more enforcement-oriented language). Where the two overlap,
`CLAUDE.md` wins — this file explains and expands, it doesn't override.

## Getting set up

```powershell
.\scripts\verify.ps1
```

One command: creates `.venv` if it doesn't exist, installs the package
editable with the `dev`/`ui` extras, runs the full test suite, and checks
the frontend and docs for drift. Should end in `VERIFY: PASS` before you
change anything. See `docs/QUICK_START.md` for the shortest possible path
from a fresh checkout to a running app, and the table below for what each
script under `scripts/` does.

If you'd rather do it by hand (or `scripts/verify.ps1` isn't available in
your environment for some reason):

```powershell
cd optionspilot
python -m venv .venv
.venv\Scripts\pip install -e .[dev,ui]
.venv\Scripts\pip install windows-toasts   # optional: desktop notifications
.venv\Scripts\python -m pytest             # confirm the suite is green before changing anything
```

### The developer scripts

| Script | Responsibility |
|---|---|
| `scripts/dev.ps1` | Start the app for local development (`-Ui` for the desktop window, `-Loop` to also run the live scan loop) |
| `scripts/test.ps1` | Run the test suite; pass-through args go straight to pytest |
| `scripts/verify.ps1` | Run every automated check in one command — tests, HTML id references, doc consistency, `pip check`, and a headless-browser smoke check |
| `scripts/docs.ps1` | Documentation consistency only (also runs as part of `verify.ps1`) |
| `scripts/build.ps1` | Build the Windows exe — refuses to run on a red test suite unless `-SkipTests` is passed |
| `scripts/release.ps1` | Full release-readiness pipeline + report (see `docs/RELEASE_CHECKLIST.md`) |
| `scripts/clean.ps1` | Remove `__pycache__`/`.pytest_cache`/`*.egg-info` clutter (`-Dist` also removes PyInstaller output) |

Each has one clear responsibility and composes with the others rather than
duplicating logic (`verify.ps1` calls `test.ps1`; `build.ps1` calls
`test.ps1` then the existing `scripts/build_exe.ps1`; `release.ps1` calls
`verify.ps1` then `build.ps1`). All of them are safe to re-run — the
environment bootstrap they share (`scripts/_common.ps1`) is idempotent.

## Coding conventions

- **Python 3.12+, standard-library `dataclasses` for domain models
  (`core/models.py`), pydantic v2 for config validation only.** Don't mix
  the two — models are dataclasses, config is pydantic.
- **`analysis/` is pure functions: no I/O, no side effects, no exceptions
  for normal control flow.** This is what lets the same code run in live
  trading, the backtester, and the coach. New analysis functions take data
  in and return data out — never touch a network, a file, or a database.
- **Everything that touches money or positions goes through `RiskManager`
  for entries and `Broker`/`OrderManager` for execution.** Don't let the
  engine, the coach, or the UI call broker methods directly to open a
  position — route through the existing gatekeepers.
- **`managed_by` discipline**: AI positions (`managed_by="ai"`) are only
  ever touched by `PositionManager`. Manual positions (`managed_by="manual"`)
  are only ever touched by `OrderManager` and the user. Don't blur this
  line.
- **Two independent mode axes** (`operating_mode`, `trading_mode`) must
  stay orthogonal. Switching one must never implicitly change the other —
  see `config/runtime.py::RuntimeSettings._apply_mode`'s explicit
  preservation pattern for any new mode-like setting.
- **Deterministic, not ML/LLM-based.** The scorer, the gate, and the coach
  are hand-authored weighted rule systems, chosen for auditability and
  offline operation.
- **No frontend build step.** `ui/static/index.html` is one self-contained
  file. The single exception is the vendored `lightweight-charts.js`
  (Apache-2.0). No CDN references, no new vendored libraries, no npm
  pipeline without the user's explicit agreement.
- **Module naming**: one word/concept per file (`gate.py`, `orders.py`,
  `coach.py`); classes are the primary export (`TradeGate`, `OrderManager`,
  `TradeCoach`). Follow the existing pattern rather than inventing a new
  convention.
- Don't add features, refactor, or introduce abstractions beyond what a
  task requires. Three similar lines beat a premature abstraction. Don't
  add error handling, fallbacks, or validation for scenarios that can't
  happen — trust internal invariants, validate only at real boundaries
  (user input, external APIs).

## Commit message conventions

Follow the style established in `git log` exactly:

```
<Short imperative summary, <70 chars, no period>

<Prose paragraphs explaining WHAT was built and WHY, organized by
sub-feature if the commit spans more than one. Name the key new
files/classes. Mention the test count at the end of the body, e.g.
"345 tests.">
```

- One commit per coherent unit of work (a phase, or a clearly-scoped
  bugfix) — not one commit per file, not one giant commit spanning
  unrelated work.
- Never use `--no-verify`, never force-push, never amend a commit that
  might already be reflected in something the user has seen — create a new
  commit instead.
- Only commit when explicitly asked, or when it's an explicit part of a
  requested task. Don't commit speculatively.
- If you (an AI assistant) are the author, credit yourself in the body per
  whatever convention the user asks for that session — check recent
  history for the current pattern rather than assuming.

## Testing expectations

- `.\scripts\test.ps1` — full suite, ~13s, must be 100% green before
  considering work done. Prints an explicit `TESTS: PASS`/`TESTS: FAIL`
  line derived from pytest's exit code, not from parsing its printed
  summary — see the note on terminal output capture below.
- `.\scripts\test.ps1 tests\test_orders.py` — one module, for fast
  iteration. `.\scripts\test.ps1 -k manual_entry` also works (pass-through
  args go straight to pytest).
- One test file per module (`broker/orders.py` ↔ `tests/test_orders.py`),
  `class Test<Thing>` / `def test_<behavior>` structure.
- New backend code needs new tests in the matching file. For anything
  touching money/positions/risk, write explicit boundary-condition tests
  (empty positions, zero quantities, missing quotes, restart-persistence).
- If a test fails and the reason isn't understood, investigate the root
  cause — never weaken or delete a test to make it pass.
- **`static/index.html` has a real but shallow automated safety net**:
  `scripts/check_html_ids.py` (static — every `$("id")` reference resolves
  to a real element) and `scripts/browser_check.py` (a real headless
  browser visits every tab and fails on any console error) both run as
  part of `scripts/verify.ps1`. Neither is deep per-flow regression
  coverage (mode toggle, manual order placement, coach review rendering
  specifically) — for any change to a specific flow, still verify it by
  hand in a real browser (`.\scripts\dev.ps1`) before calling it done. See
  `TODO.md` for the open opportunity to extend `browser_check.py` with
  flow-specific coverage.
- Terminal output capture can silently swallow pytest's final summary line
  depending on the shell tool in use — this is exactly what
  `scripts/test.ps1`'s explicit exit-code-based PASS/FAIL line exists to
  defuse. If running pytest directly instead, don't assume failure just
  because you didn't see `N passed in X.XXs` — check for `F`/`E` markers
  first, or just use the script.

## Documentation requirements

After finishing a feature or a phase, in the same session (never deferred):

1. **`docs/CHANGELOG.md`** — append a new dated (or `[Uncommitted]`)
   section in the existing prose style, not a raw diff summary.
2. **`docs/PROJECT_STATE.md`** — move items between not-started/in-progress/
   completed; update "exact stopping point" and "next recommended task."
3. **`docs/PROJECT_STATUS.md`** — update the structured snapshot fields
   that changed (test count, completed milestones, known bugs, priorities).
4. **`docs/NEXT_SESSION.md`** — rewrite it to reflect the new handoff state;
   this file should always be current, never stale by more than one session.
5. **`docs/TODO.md`** — check off or remove completed items, add newly
   discovered ones.
6. **`docs/ROADMAP.md`** and, for V2-scope work, **`docs/ROADMAP-V2.md`** —
   update the relevant checklist items.
7. If a module described in **`docs/MODULES.md`** or **`docs/ARCHITECTURE.md`**
   changed, update those sections too.
8. If new API endpoints, storage files, modes, or dependencies were added,
   update **`docs/AI_HANDOFF.md`** — it's meant to be a complete orientation,
   and an incomplete one is worse than an obviously-stale one.
9. If anything durable about the project's philosophy, standards, or
   things-never-to-change list changed, update **`docs/AI_CONTEXT.md`**.

Never rewrite `CHANGELOG.md`'s existing entries — append only.

## Definition of Done

A change is done when, and only when, all of the following are true:

- [ ] `.\scripts\verify.ps1` passes (tests, HTML id references, doc
      consistency, `pip check`, browser smoke check — all in one command).
- [ ] New tests exist for new behavior, especially boundary conditions on
      anything touching money/positions/risk.
- [ ] If `static/index.html` changed: verified in a real (or scripted
      headless) browser beyond what `browser_check.py`'s tab-navigation
      smoke check covers — zero new console errors either way.
- [ ] No trading-logic safety rule was weakened (see `CLAUDE.md` "The one
      rule that overrides everything else").
- [ ] Every doc in "Documentation requirements" above that's affected has
      been updated — not deferred.
- [ ] `git status` and `git diff --stat` were both checked (not just one —
      see `AI_CONTEXT.md` "Common mistakes to avoid") before claiming the
      working tree's state.
- [ ] The commit (if one is made) follows the message convention above and
      was only made because it was explicitly requested.

## Pre-commit checklist

Run through this before proposing or making a commit:

1. `git status` **and** `git diff --stat` — confirm what's actually
   changed, don't trust either alone.
2. `.\scripts\verify.ps1` — the whole automated gate in one command (tests,
   HTML id references, doc consistency, `pip check`, browser smoke check).
   This subsumes "run the test suite" — no separate step needed.
3. Read the full diff once, end to end, looking for: leftover debug prints
   or temporary logging, commented-out code, TODO markers that should have
   been resolved, placeholder/stub implementations, accidental changes to
   files unrelated to the task, generated artifacts that shouldn't be
   tracked (`dist/`, `build/`, `*.egg-info/`, `OptionsPilot.spec` — these
   are already gitignored, but double-check nothing new slipped in; `.\scripts\clean.ps1`
   removes local dev clutter if you want a clean-room check).
4. Confirm the documentation checklist above is satisfied.
5. Draft the commit message per the convention above.
6. Only stage and commit if explicitly asked to.

## Automation: what's implemented vs. still just recommended

A 2026-07-17 session reviewed the repo for repetitive developer tasks and
built a `scripts/` automation layer (see "The developer scripts" above) —
this section records what actually got built, and, separately, what's
still a recommendation the user hasn't decided on. Keep this distinction
honest as things change: don't let "recommended" items silently start
sounding implemented, or vice versa.

### Implemented (2026-07-17)

- **`scripts/check_html_ids.py`** — the static `index.html` ID-reference
  check, previously an ad hoc one-off script, now committed and run by
  `verify.ps1`/`docs.ps1` every time.
- **`scripts/check_docs.py`** — documentation consistency: cross-referenced
  doc files exist, "current state" docs' test-count claims match a live
  pytest count, `pyproject.toml`'s version agrees with
  `optionspilot/__init__.py`'s. Caught a real stale example in `CLAUDE.md`
  on its first run (a commit-message template hardcoded `"296 tests"`).
- **`scripts/browser_check.py`** — a committed, repeatable version of the
  ad hoc Playwright verification from earlier sessions: launches the app
  against a scratch data directory, drives the system's installed Edge
  (`channel="msedge"`, no browser download), visits every tab, and fails
  on any console error. Found a real bug on its first run (a missing
  favicon, fixed the same session) and a real cleanup bug in itself
  (leftover scratch directories from a Windows file-handle race, also
  fixed the same session — see `AI_CONTEXT.md` "Common mistakes to
  avoid"). This is a smoke check (does every tab load cleanly?), not deep
  per-flow regression coverage — see `TODO.md` for the remaining
  opportunity to extend it.
- **`scripts/bump_version.py`** — keeps `pyproject.toml` and
  `optionspilot/__init__.py`'s version strings from drifting apart, the
  same class of bug `check_docs.py` guards against for test counts.
- **The `dev.ps1`/`test.ps1`/`verify.ps1`/`docs.ps1`/`build.ps1`/
  `release.ps1`/`clean.ps1` orchestration layer** itself, plus two new
  optional `pyproject.toml` extras (`build` = `pyinstaller`, `browser` =
  `playwright`) so `pyinstaller` — previously installed ad hoc and
  undeclared anywhere, the same gap `Pillow` had before a prior session
  fixed it — is now reproducible.

### Still just recommended (not installed, real decisions for the user)

- **A minimal CI workflow** (`.github/workflows/tests.yml`) running
  `scripts/verify.ps1` (or just the pytest suite) on push/PR. There's a
  `git remote` pointing at GitHub already, so this is a low-cost addition
  whenever the user wants basic build validation without relying on a
  human remembering to run `verify.ps1` locally. Recommended scope: tests
  + the doc/HTML checks — not the browser check (needs a real browser
  binary in the CI image, more setup) and not linting/formatting until
  those are actually adopted (see below), to avoid a CI step that's red
  for reasons unrelated to correctness.
- **Linting/formatting** (`ruff` is the natural single-tool choice — lint +
  format + import sort, one dependency, fast, zero-config-friendly). None
  is configured today; the codebase has stayed consistent by convention.
  Worth adopting only if the user wants it — introducing it retroactively
  on an existing, unlinted codebase means either a large reformatting diff
  or a lot of `# noqa`s, so this is a real decision, not a trivial add.
- **Pre-commit hooks** (`pre-commit` framework) to run `scripts/verify.ps1`
  (or a fast subset of it) automatically before every commit — worth
  adding only after CI exists, so the two reinforce rather than duplicate
  each other.
- **Deep per-flow browser regression tests** (mode toggle, manual order
  placement, coach review rendering, each as its own scripted flow) beyond
  `browser_check.py`'s tab-navigation smoke check — the single remaining
  highest-leverage gap, since it's the one form of testing that still
  doesn't exist at all for `static/index.html`.
- **Automating the *decision* to rebuild the exe.** `scripts/build.ps1`
  already automates the *mechanics* safely (test-gated, wraps the
  data-preserving `build_exe.ps1`). Automating *when* a rebuild happens
  (e.g. on every merge to main) would work against this project's own
  stated workflow — exe rebuilds happen last, deliberately, as a human
  judgment call. Not recommended.

## What was deliberately NOT recommended

- Restructuring the `optionspilot/` package layout — the current one
  module-per-concern layout is clear, consistently followed, and any
  reshuffling would touch imports across the entire codebase for no
  measurable benefit.
- Splitting `docs/` further or merging any of the existing files — the
  current set (after the 2026-07-17 documentation pass) has each file
  answering a distinct question (see the "Documentation" list in
  `README.md`); consolidating them would just make each one longer without
  reducing the number of concepts a reader needs.
- Switching test runners, package managers, or the build/packaging
  toolchain — nothing about the current choices is causing friction.
