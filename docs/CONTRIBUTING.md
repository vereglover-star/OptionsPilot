# CONTRIBUTING.md — development standards

Practical, human-contributor-facing companion to `CLAUDE.md` (which is the
authoritative rule set for AI coding sessions and covers some of this same
ground in more enforcement-oriented language). Where the two overlap,
`CLAUDE.md` wins — this file explains and expands, it doesn't override.

## Getting set up

```powershell
cd optionspilot
python -m venv .venv
.venv\Scripts\pip install -e .[dev,ui]
.venv\Scripts\pip install windows-toasts   # optional: desktop notifications
.venv\Scripts\python -m pytest             # confirm 345/345 before changing anything
```

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

- `.venv\Scripts\python -m pytest` — full suite, ~13s, must be 100% green
  before considering work done.
- `.venv\Scripts\python -m pytest tests\test_orders.py` — one module, for
  fast iteration.
- One test file per module (`broker/orders.py` ↔ `tests/test_orders.py`),
  `class Test<Thing>` / `def test_<behavior>` structure.
- New backend code needs new tests in the matching file. For anything
  touching money/positions/risk, write explicit boundary-condition tests
  (empty positions, zero quantities, missing quotes, restart-persistence).
- If a test fails and the reason isn't understood, investigate the root
  cause — never weaken or delete a test to make it pass.
- **No automated frontend test suite exists.** For any `static/index.html`
  change, the minimum bar is: (a) a static check that every `$("id")`
  reference resolves to a real element, and (b) manual verification in an
  actual browser — start the dev server
  (`python -m optionspilot serve --port 8787 --no-loop`) and click through
  the changed flow. See "Automation opportunities" below for a scripted
  alternative that's been used successfully in this repo.
- Terminal output capture can silently swallow pytest's final summary line
  depending on the shell tool in use. Don't assume failure just because you
  didn't see `N passed in X.XXs` — check for `F`/`E` markers first.

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

- [ ] The full test suite passes (345+ tests, 100% green).
- [ ] New tests exist for new behavior, especially boundary conditions on
      anything touching money/positions/risk.
- [ ] If `static/index.html` changed: verified in a real (or scripted
      headless) browser, zero new console errors.
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
2. `.venv\Scripts\python -m pytest` — full suite green.
3. Read the full diff once, end to end, looking for: leftover debug prints
   or temporary logging, commented-out code, TODO markers that should have
   been resolved, placeholder/stub implementations, accidental changes to
   files unrelated to the task, generated artifacts that shouldn't be
   tracked (`dist/`, `build/`, `*.egg-info/`, `OptionsPilot.spec` — these
   are already gitignored, but double-check nothing new slipped in).
4. Confirm the documentation checklist above is satisfied.
5. Draft the commit message per the convention above.
6. Only stage and commit if explicitly asked to.

## Automation opportunities (recommendations, not yet applied)

Reviewed for repetitive developer tasks worth automating. Nothing in this
section has been implemented — these are recommendations for the user to
decide on, in keeping with "don't introduce unnecessary tooling." None of
these are required for the project to keep working as it has.

- **Static `index.html` ID-reference check.** Already used ad hoc (a short
  Python script grepping `id="..."` vs `$("...")` calls). Worth turning
  into a real `scripts/check_html_ids.py` and wiring it into a pre-commit
  or CI step — it's cheap, has already caught nothing but has real
  precedent as a safety net, and needs no new dependency.
- **A minimal CI workflow** (`.github/workflows/tests.yml`) running
  `pip install -e .[dev,ui]` + `pytest` on push/PR. There's a `git remote`
  pointing at GitHub already, so this is a low-cost addition whenever the
  user wants basic build validation without relying on a human remembering
  to run tests locally. Recommended scope: just the test suite — not
  linting/formatting until those are actually adopted (see below), to
  avoid a CI step that's red for reasons unrelated to correctness.
- **Linting/formatting** (`ruff` is the natural single-tool choice — lint +
  format + import sort, one dependency, fast, zero-config-friendly). None
  is configured today; the codebase has stayed consistent by convention.
  Worth adopting only if the user wants it — introducing it retroactively
  on an existing, unlinted codebase means either a large reformatting diff
  or a lot of `# noqa`s, so this is a real decision, not a trivial add.
- **Pre-commit hooks** (`pre-commit` framework) to run the pytest suite (or
  a fast subset) and the HTML ID check automatically before every commit —
  worth adding only after CI exists, so the two reinforce rather than
  duplicate each other.
- **Release/build automation**: `scripts/build_exe.ps1` already handles the
  actual packaging well (backup/restore of `data/`, running-instance guard).
  The remaining manual step is *deciding* to rebuild — that's a deliberate
  human judgment call in this project (exe rebuilds happen last, on
  purpose), so automating the trigger would work against the project's own
  stated workflow. Not recommended.
- **Playwright-based browser verification, made permanent.** A 2026-07-17
  session installed `playwright` ad hoc into `.venv` and drove the system's
  installed Edge (`channel="msedge"`, no browser download needed) to
  verify the Charts tab — screenshots, console-error checking, and
  interaction scripting all worked well. That precedent is worth
  formalizing into a few committed `tests/browser/` scripts for the
  highest-value flows (mode toggle, manual order placement, coach review
  rendering) rather than writing throwaway scripts each time. This is the
  single highest-leverage automation opportunity in the repo, since it's
  the one form of testing that currently doesn't exist at all. Gotcha
  worth preserving in whatever script is written: lightweight-charts
  coalesces clicks faster than ~500ms apart as double-clicks, so scripted
  two-point drawing-tool clicks need ≥700ms pacing.

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
