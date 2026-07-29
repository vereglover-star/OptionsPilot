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
    "services": {"core", "config", "host", "intelligence"},
}

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
        bad = _top_level_imports(py) & TRANSPORT_PACKAGES
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
