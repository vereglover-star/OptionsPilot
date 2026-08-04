"""Architecture guard: the layered dependency graph is maintained by discipline
today; this test makes it executable so a future change can't silently introduce
forbidden coupling (e.g. `engine` importing `broker`, or `experience` importing
trading internals at runtime).

It parses the AST of every module and asserts each subpackage only imports from
its allow-listed set of sibling subpackages. The allow-list IS the documented
layering (see docs/ARCHITECTURE.md §2 and docs/ARCHITECTURE-AUDIT-V0.4.2.md §2.1)
— update it deliberately, with justification, not to make a red test green.
"""

from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "optionspilot"

# Allowed internal (optionspilot.*) subpackage imports, per subpackage.
# `ui` and `orchestrator` are the two composition roots and may import broadly.
ALLOWED: dict[str, set[str]] = {
    "analysis": {"core"},
    "core": {"config"},          # logging_setup imports LoggingConfig (documented inversion)
    "config": {"core"},
    "data": {"core"},
    "engine": {"core", "config", "analysis"},
    "risk": {"core", "config"},
    "broker": {"core", "config"},
    "journal": {"core"},
    "learning": {"core", "journal", "engine"},
    "experience": {"core", "engine"},   # engine is TYPE_CHECKING-only (see snapshot.py)
    "backtest": {"core", "config", "analysis", "engine", "risk", "broker", "journal"},
    "coach": {"core"},
    # Trading Intelligence Engine: consumes journal/experience/coach records
    # structurally (duck-typed in facts.py) rather than by import, so it stays
    # BELOW the coach in the layering — which is what lets the coach become a
    # presentation layer over it instead of a parallel analysis path.
    "intelligence": {"core"},
    "notify": {"core", "config"},
    "integrations": {"core"},
    # Self-updater: reads GitHub, downloads to temp, backs up + launches the
    # installer. Depends only on core (paths, migration.create_backup, logging)
    # plus stdlib — deliberately independent of the trading stack.
    "update": {"core"},
    # V0.7.0 — the host platform abstraction. Everything the app needs from the
    # machine underneath it (storage root, temp space, single-instance lock,
    # opening a URL) plus the declarative capability profiles for every target
    # the architecture is designed against. Core-only, and it must STAY
    # core-only: the point of the package is that a future mobile backend can
    # import it without dragging in the trading stack.
    "host": {"core"},
    # V0.7.0 — the platform-independent application layer. It may import the
    # domain it projects, and it may NOT import `ui` (asserted separately
    # below, along with a ban on transport dependencies). `services` sits
    # between the orchestrator and any transport; a second client's backend
    # imports this and gets the whole application without FastAPI.
    # V0.9.2-C2 widened this by two, deliberately (see the module docstring's
    # standing instruction: update it with justification, never to green a
    # test). `ChartService` computes the Charts tab's indicator series, and the
    # endpoint's entire claim is that they come from the SAME analysis library
    # the engine trades with — injecting that library would let two callers
    # supply different ones and quietly break the parity it promises. `data` is
    # narrower than it looks and is bounded by
    # `test_services_reach_only_the_pure_data_helpers` below.
    # V0.9.2-C4 adds `broker`, bounded by
    # `test_services_never_reach_a_broker_implementation` below. `TradingService`
    # needs the order VOCABULARY — `OrderKind`, `TIF`, `BrokerError` — to turn
    # a client payload into an order. It still routes every execution through
    # the injected orchestrator's `OrderManager` and every entry through
    # `approve_manual_entry`, so it is a caller, not a second gatekeeper.
    "services": {"core", "config", "host", "intelligence", "analysis", "data",
                 "broker"},
}

# The only modules of `broker/` a service may import, and this bound carries
# more weight than the `data` one below it.
#
# `broker/registry.py` holds the Alpaca/Tradier/Webull/IBKR entries — named
# stubs that raise `BrokerError` because **this is a paper-trading-only system
# by design** (see CLAUDE.md, "The one rule that overrides everything else").
# Keeping it out of the reusable application layer means no service can ever
# construct a live adapter, and a future mobile backend importing `services/`
# cannot acquire one by accident. `broker/paper.py` and `broker/positions.py`
# are excluded for the ordinary reason: a service must RECEIVE a broker,
# injected, never build one.
SERVICE_BROKER_MODULES = {"base", "orders"}

# The only modules of `data/` a service may import. All three are PURE — no
# socket, no key, no quota, no provider construction — and each is imported
# rather than injected for the same reason: it is the single renderer or
# validator of something, and a second one must not be substitutable.
#
#   base      `validate_candles`, the non-finite-bar sanitizer
#   sessions  `labels`, an ET session labeller
#   report    `render`, the ONE diagnostics renderer the JSON export, the
#             dashboard and the text report all share (stdlib-only)
#
# Everything else in `data/` opens sockets, holds a key, spends a quota or
# constructs a provider — and a service must RECEIVE those, injected and
# duck-typed, never import them. `data/replay.py` is the worked example: it
# reaches the registry, the adapters and the quality checks, so
# `MarketDataAdminService` takes `replay` as a constructor argument instead.
# Without this bound, `data` in the allow-list above would have handed
# `services/` the whole market-data stack.
SERVICE_DATA_MODULES = {"base", "sessions", "report"}

# Composition roots — allowed to import any subpackage. Still constrained by the
# explicit negative invariants below (a root must not import "up").
_ALL_SUBPACKAGES = set(ALLOWED) | {"orchestrator", "ui", "notify", "integrations"}
COMPOSITION_ROOTS = {"ui"}

# Third-party packages that mean "this module knows about a transport or a
# presentation surface". The `services/` ban on these is the V0.7.0 invariant:
# a service reachable only by importing a web framework is a service a second
# client cannot use, which is the entire obstacle this milestone removes.
TRANSPORT_PACKAGES = {
    "fastapi", "starlette", "uvicorn", "webview", "pywebview", "jinja2",
    "httpx", "requests", "flask", "django", "aiohttp", "websockets",
    # Platform/presentation packages are equally forbidden from reusable
    # application services.  A Flutter or cloud host must import these modules
    # without accidentally importing a Windows tray, toast, browser, or GUI.
    "pystray", "PIL", "winreg", "windows_toasts",
}


def _internal_imports(path: pathlib.Path) -> set[str]:
    """Second dotted component of every `optionspilot.<x>...` import in a file."""
    # utf-8-sig tolerates a leading BOM (yfinance_provider.py has one).
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        for name in names:
            parts = name.split(".")
            if parts[0] == "optionspilot" and len(parts) >= 2:
                found.add(parts[1])
    return found


def _package_imports(pkg: str) -> set[str]:
    deps: set[str] = set()
    for py in (PKG / pkg).rglob("*.py"):
        deps |= _internal_imports(py)
    deps.discard(pkg)          # imports within the package are fine
    return deps


def _top_level_imports(path: pathlib.Path) -> set[str]:
    """First dotted component of EVERY import in a file, internal or not."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
    return found


def test_subpackage_import_allowlist():
    """Every subpackage imports only from its allow-listed siblings."""
    violations = []
    for pkg, allowed in ALLOWED.items():
        actual = _package_imports(pkg)
        forbidden = actual - allowed
        if forbidden:
            violations.append(
                f"{pkg}/ imports {sorted(forbidden)} — allowed: {sorted(allowed)}")
    assert not violations, "layering violations:\n  " + "\n  ".join(violations)


def test_composition_roots_do_not_import_upward():
    """The orchestrator (a composition root) must not import the UI above it."""
    orch = _internal_imports(PKG / "orchestrator.py")
    assert "ui" not in orch, "orchestrator.py must not import ui/"


def test_key_isolation_invariants():
    """The load-bearing separations, asserted explicitly for clear failures."""
    engine = _package_imports("engine")
    assert not ({"broker", "risk", "ui"} & engine), \
        f"engine/ must not import broker/risk/ui (found {engine})"

    broker = _package_imports("broker")
    assert not ({"ui", "engine"} & broker), \
        f"broker/ must not import ui/engine (found {broker})"

    experience = _package_imports("experience")
    assert not ({"broker", "risk", "ui"} & experience), \
        f"experience/ must not depend on trading internals (found {experience})"

    analysis = _package_imports("analysis")
    assert analysis <= {"core"}, \
        f"analysis/ must stay pure (core only), found {analysis}"

    # The intelligence layer must sit BELOW everything that presents it. If it
    # ever imports coach/ or ui/, the dependency inverts and the coach can no
    # longer be built on top of it (V0.6.0's central architectural claim).
    intelligence = _package_imports("intelligence")
    assert intelligence <= {"core"}, \
        f"intelligence/ must stay pure (core only), found {intelligence}"


def test_ui_is_the_only_broad_composition_root_besides_orchestrator():
    """Sanity: `ui` may import broadly, but only from known subpackages (guards
    against a typo'd or renamed subpackage silently slipping in)."""
    ui = _package_imports("ui")
    unknown = ui - _ALL_SUBPACKAGES
    assert not unknown, f"ui/ imports unknown subpackages {sorted(unknown)}"
    assert "ui" in COMPOSITION_ROOTS


def test_services_never_import_the_ui():
    """The V0.7.0 invariant, stated on its own so the failure is unambiguous.

    A service that imports `ui` has inverted the layering: the transport would
    then sit below the application layer, and a second client could not use one
    without the other. This is the single assertion the whole milestone rests
    on.
    """
    services = _package_imports("services")
    assert "ui" not in services, \
        f"services/ must not import ui/ (found {sorted(services)})"


def test_services_have_no_transport_dependency():
    """No module under `services/` may import a web or GUI framework.

    Stronger than the `ui` ban and for a different reason: `services` could
    stay free of `optionspilot.ui` and still `import fastapi` for a response
    model, at which point a Flutter backend, a CLI or a test would be pulling a
    web server in to compute a win rate. The point of the layer is that its
    dependencies are the standard library plus this project's domain.
    """
    offenders = []
    for py in (PKG / "services").rglob("*.py"):
        bad = _top_level_imports(py) & TRANSPORT_PACKAGES
        if bad:
            offenders.append(f"{py.name} imports {sorted(bad)}")
    assert not offenders, \
        "services/ must stay transport-free:\n  " + "\n  ".join(offenders)


def _service_submodule_imports(package: str) -> list[str]:
    """Every `optionspilot.<package>.<module>` reached from `services/`.

    Both spellings resolve to the same thing: `from optionspilot.x import y`
    names the module in the alias, `from optionspilot.x.y import z` in
    `node.module`. Handling only one leaves a hole exactly where the tidiest
    import style sits.
    """
    found: list[str] = []
    for py in (PKG / "services").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.extend(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            for name in names:
                parts = name.split(".")
                if parts[:2] == ["optionspilot", package] and len(parts) >= 3:
                    found.append(f"{py.name}:{node.lineno}:{parts[2]}")
    return found


def test_services_never_reach_a_broker_implementation():
    """A service may name an order kind. It may not reach a broker.

    The load-bearing exclusion is `broker/registry.py`, which holds the
    live-broker stubs. This project is paper-trading-only by design, and a
    reusable application layer that cannot import the registry is a layer in
    which no future host can construct a live adapter by accident.
    """
    offenders = [entry for entry in _service_submodule_imports("broker")
                 if entry.rsplit(":", 1)[1] not in SERVICE_BROKER_MODULES]
    assert not offenders, (
        "services/ may import only "
        + ", ".join(f"broker.{m}" for m in sorted(SERVICE_BROKER_MODULES))
        + " (never broker.registry — this is a paper-trading-only system):\n  "
        + "\n  ".join(offenders))


def test_services_reach_only_the_pure_data_helpers():
    """`services` may import `data`, but only its two pure helpers.

    The coarse package-level allow-list cannot express this, and the difference
    matters: a service that imports `data.credentials` has put a secret in the
    reusable layer, and one that imports a provider module has acquired a
    collaborator it was supposed to be given. Stated as an allow-list rather
    than a ban so a new provider module is forbidden by default.
    """
    offenders = []
    for py in (PKG / "services").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            # `from optionspilot.data import sessions` names the module in the
            # alias, not in `node.module` — both spellings must be resolved to
            # the same thing or the allow-list has a hole exactly where the
            # tidiest import style sits.
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.extend(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            for name in names:
                parts = name.split(".")
                if parts[:2] != ["optionspilot", "data"] or len(parts) < 3:
                    continue
                if parts[2] not in SERVICE_DATA_MODULES:
                    offenders.append(f"{py.name}:{node.lineno} imports "
                                     f"{'.'.join(parts[:3])}")
    assert not offenders, (
        "services/ may import only " + ", ".join(f"data.{m}" for m in
                                                 sorted(SERVICE_DATA_MODULES))
        + ":\n  " + "\n  ".join(offenders))


def test_host_stays_core_only():
    """`host/` is the bottom of the platform stack.

    It answers OS questions for everything above it, so anything it imported
    would become a dependency of every future client host. Core-only keeps the
    package importable on its own.
    """
    host = _package_imports("host")
    assert host <= {"core"}, f"host/ must stay core-only, found {sorted(host)}"

    offenders = []
    for py in (PKG / "host").rglob("*.py"):
        # The host adapter is the one intentional OS boundary.  Its Win32
        # registry implementation is permitted here, but nowhere in reusable
        # services/contracts/runtime code.
        bad = (_top_level_imports(py) & TRANSPORT_PACKAGES) - {"winreg"}
        if bad:
            offenders.append(f"{py.name} imports {sorted(bad)}")
    assert not offenders, \
        "host/ must stay transport-free:\n  " + "\n  ".join(offenders)


def test_no_module_outside_core_and_host_decides_the_storage_root():
    """`sys.platform` branching for storage belongs in exactly two places.

    `core/paths.py` resolves the root; `host/` wraps it. A third module that
    grew its own `if sys.platform == "win32"` would be the beginning of the
    scattered CWD-relative paths V0.4.4 spent a milestone removing — and the
    `/api/learning` weights read, fixed in V0.7.0, is proof the class recurs.
    """
    allowed = {
        PKG / "core" / "paths.py",
        PKG / "host" / "adapter.py",
        PKG / "host" / "capabilities.py",
        PKG / "update" / "installer.py",   # launching an installer IS OS-specific
    }
    offenders = []
    for py in PKG.rglob("*.py"):
        if py in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            # `sys.platform` / `os.name` as real attribute access. Matched on
            # the AST rather than the text so a docstring explaining the rule
            # does not violate it — the first version of this test failed on
            # its own explanation.
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and (node.value.id, node.attr) in
                    (("sys", "platform"), ("os", "name"))):
                offenders.append(str(py.relative_to(PKG)))
                break
    assert not offenders, (
        "platform branching outside core/paths.py and host/: "
        + ", ".join(sorted(offenders))
        + " — ask the host adapter a CAPABILITY question instead")


def test_no_cwd_relative_storage_paths():
    """Nothing may build a storage path from the current working directory.

    `Path("data")` was the pre-V0.4.4 convention and it survived in
    `ui/server.py`'s `/api/learning` handler until V0.7.0, where it read a
    weights file that does not exist on any real install — so the Learning tab
    reported no learned weights however much the engine had learned. The read
    looked right, and `effective` (taken from the live scorer) really was
    right, which is exactly why it went unnoticed for three milestones.
    """
    roots = {"data", "logs", "backups", "exports", "migrations"}
    offenders = []
    for py in PKG.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            # `Path("data")` as a real call, not as prose describing one.
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "Path"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in roots):
                offenders.append(
                    f"{py.relative_to(PKG)}:{node.lineno} "
                    f"Path({node.args[0].value!r})")
    assert not offenders, (
        "CWD-relative storage paths (use AppPaths / the injected data_dir): "
        + ", ".join(sorted(offenders)))


def test_window_days_is_public_and_complete():
    """The candle history window is a public constant (V0.4.2 removed the
    private `_WINDOW_DAYS` reach-through from ui/server.py and __main__.py)."""
    from optionspilot.core.models import Timeframe
    from optionspilot.orchestrator import WINDOW_DAYS

    assert set(WINDOW_DAYS) == set(Timeframe)


def test_ui_server_has_no_function_level_optionspilot_imports():
    """The UI composition root must not scatter optionspilot imports into
    function/method bodies (guards the V0.4.2 server import cleanup)."""
    tree = ast.parse((PKG / "ui" / "server.py").read_text(encoding="utf-8-sig"))
    nested = []
    for func in (n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for child in ast.walk(func):
            if (isinstance(child, ast.ImportFrom) and child.module
                    and child.module.startswith("optionspilot")):
                nested.append(f"{func.name} -> {child.module}")
    assert not nested, \
        "server.py has function-level optionspilot imports (hoist to top): " \
        + ", ".join(sorted(set(nested)))


# ── the transport size ceiling (V0.9.2-C10) ──────────────────────────────────
#
# `ui/server.py` was 1,892 lines at the start of V0.9.2 and held five services'
# worth of application logic. C2–C6 moved it out. This is the ratchet that
# stops it coming back.
#
# **Raising this number is not the fix for a failing test here.** The fix is to
# move the logic into `services/`, which is where the last five commits put the
# charts, the market-data console, the trading surface, the backtest slot and
# the guide. A transport decides status codes and shapes; it does not decide
# what a client is shown.
MAX_UI_SERVER_LINES = 1650

#: How far below the ceiling the file may sit before the ceiling itself is
#: stale. A ratchet that is never tightened stops being one — it silently
#: re-grants every line a past extraction won back. Generous enough not to
#: fight ordinary work.
CEILING_SLACK = 120


def _ui_server_lines() -> int:
    return len((PKG / "ui" / "server.py")
               .read_text(encoding="utf-8-sig").splitlines())


def test_ui_server_stays_under_its_ceiling():
    """The transport does not regrow the application layer.

    If this fails, extract the new logic into `optionspilot/services/` rather
    than raising `MAX_UI_SERVER_LINES`. Every service in that package was once
    a section of this file.
    """
    lines = _ui_server_lines()
    assert lines <= MAX_UI_SERVER_LINES, (
        f"ui/server.py is {lines} lines, over the {MAX_UI_SERVER_LINES} "
        "ceiling. Move the new logic into services/ — do not raise the "
        "ceiling. See docs/ARCHITECTURE-PLATFORM.md section 2.")


def test_the_ceiling_is_still_a_ratchet():
    """A ceiling far above the file is not a constraint, it is a comment.

    When an extraction lands, this fails and the ceiling comes down with it —
    which is the only thing that makes the number mean anything a year from
    now.
    """
    lines = _ui_server_lines()
    assert MAX_UI_SERVER_LINES - lines <= CEILING_SLACK, (
        f"ui/server.py is {lines} lines but the ceiling is "
        f"{MAX_UI_SERVER_LINES}. An extraction has left the ratchet slack: "
        f"lower MAX_UI_SERVER_LINES to about {lines + 20}.")
