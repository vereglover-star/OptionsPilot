"""Packaging guards: what the PyInstaller build must explicitly bundle.

PyInstaller discovers dependencies by statically scanning ``import``
statements. Modules loaded via ``importlib.import_module()`` are invisible to
that scan and silently vanish from the packaged exe: the build succeeds and
the app only fails at runtime, on the first code path that touches the module.

This actually shipped once: the performance pass (f1bae42) deferred the
yfinance import behind ``importlib.import_module`` without updating
``scripts/build_exe.ps1``, so every exe built afterwards had no market data
provider at all — every chart, quote, and option-chain request died with
"No module named 'yfinance'". These tests fail the ordinary test suite (long
before anyone runs a build) if a dynamic third-party import is not explicitly
collected by the build script; ``build_exe.ps1``'s post-build selftest run is
the second, physical layer of the same guard.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "optionspilot"
BUILD_SCRIPT = ROOT / "scripts" / "build_exe.ps1"

_DYNAMIC_IMPORT = re.compile(
    r"""(?:importlib\.import_module|__import__)\(\s*["']([A-Za-z_][\w.]*)["']"""
)


def dynamic_third_party_imports() -> dict[str, list[str]]:
    """Map of third-party top-level module -> source files that lazy-load it."""
    found: dict[str, list[str]] = {}
    for py in PACKAGE_DIR.rglob("*.py"):
        for match in _DYNAMIC_IMPORT.finditer(py.read_text(encoding="utf-8")):
            top = match.group(1).split(".")[0]
            if top == "optionspilot" or top in sys.stdlib_module_names:
                continue
            found.setdefault(top, []).append(str(py.relative_to(ROOT)))
    return found


class TestDynamicImportsAreBundled:
    def test_scanner_sees_the_known_lazy_import(self):
        # If this fails, the scanner regex has rotted and the coverage test
        # below is vacuously green. Fix the regex — don't delete this assert.
        assert "yfinance" in dynamic_third_party_imports()

    def test_every_dynamic_import_is_collected_by_the_build_script(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        missing = {
            mod: files
            for mod, files in dynamic_third_party_imports().items()
            if not re.search(
                rf"--(?:collect-all|collect-submodules|hidden-import)[ =]{re.escape(mod)}\b",
                script,
            )
        }
        assert not missing, (
            f"Dynamically-imported modules not collected by scripts/build_exe.ps1: "
            f"{missing}. PyInstaller cannot see importlib.import_module() calls — "
            f"add a --collect-all flag for each, or the packaged exe will raise "
            f"ModuleNotFoundError at runtime."
        )

    def test_build_script_runs_the_packaged_selftest(self):
        # The flag alone isn't proof the bundle works; the build must actually
        # execute the built exe's selftest (a gate that exists but is never
        # called protects nothing — see CLAUDE.md "Known traps").
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert re.search(r"OptionsPilot\.exe.*selftest|selftest.*OptionsPilot\.exe",
                         script), (
            "scripts/build_exe.ps1 no longer runs the packaged selftest — "
            "a bundle missing a lazy import would build green and fail at runtime."
        )


class TestSelftestCommand:
    def test_selftest_passes_in_dev_environment(self, capsys):
        from optionspilot.__main__ import main

        assert main(["selftest"]) == 0
        assert "SELFTEST PASS" in capsys.readouterr().out
