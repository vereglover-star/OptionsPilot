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
    "notify": {"core", "config"},
    "integrations": {"core"},
}

# Composition roots — allowed to import any subpackage. Still constrained by the
# explicit negative invariants below (a root must not import "up").
_ALL_SUBPACKAGES = set(ALLOWED) | {"orchestrator", "ui", "notify", "integrations"}
COMPOSITION_ROOTS = {"ui"}


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


def test_ui_is_the_only_broad_composition_root_besides_orchestrator():
    """Sanity: `ui` may import broadly, but only from known subpackages (guards
    against a typo'd or renamed subpackage silently slipping in)."""
    ui = _package_imports("ui")
    unknown = ui - _ALL_SUBPACKAGES
    assert not unknown, f"ui/ imports unknown subpackages {sorted(unknown)}"
    assert "ui" in COMPOSITION_ROOTS


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
