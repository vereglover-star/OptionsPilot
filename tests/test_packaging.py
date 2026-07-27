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


def _zone_flag(path: Path) -> None:
    """Attach the same Zone.Identifier ADS a browser download carries."""
    with open(str(path) + ":Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=3\n")


def _zone_flagged(path: Path) -> bool:
    try:
        with open(str(path) + ":Zone.Identifier", encoding="utf-8"):
            return True
    except OSError:
        return False


class TestUnblockBundle:
    """The packaged exe must strip the Mark-of-the-Web from its own files.

    A release zip downloaded from GitHub and extracted with Explorer flags
    every file with a Zone.Identifier stream; .NET Framework then refuses to
    load the flagged managed assemblies (pythonnet's Python.Runtime.dll first),
    crashing the desktop shell with "Failed to resolve
    Python.Runtime.Loader.Initialize" before the window opens. Reproduced and
    fixed in V0.3.5 — `optionspilot_app.unblock_bundle()` runs at startup,
    before webview loads clr.
    """

    def _import_app(self):
        sys.path.insert(0, str(ROOT))
        try:
            import optionspilot_app
        finally:
            sys.path.pop(0)
        return optionspilot_app

    def test_removes_zone_identifier_from_bundle_tree(self, tmp_path, monkeypatch):
        if sys.platform != "win32":
            return  # ADS is NTFS-only
        exe = tmp_path / "OptionsPilot.exe"
        exe.write_bytes(b"")
        dll = tmp_path / "_internal" / "pythonnet" / "runtime" / "Python.Runtime.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"")
        _zone_flag(exe)
        _zone_flag(dll)
        assert _zone_flagged(dll), "test setup failed to attach the ADS"

        app = self._import_app()
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        app.unblock_bundle()

        assert not _zone_flagged(exe)
        assert not _zone_flagged(dll)

    def test_noop_when_not_frozen(self, tmp_path, monkeypatch):
        if sys.platform != "win32":
            return
        f = tmp_path / "a.dll"
        f.write_bytes(b"")
        _zone_flag(f)
        app = self._import_app()
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"))
        app.unblock_bundle()
        assert _zone_flagged(f), "dev interpreter must not touch files"

    def test_entry_point_calls_unblock_before_main(self):
        # A gate that exists but is never called protects nothing (CLAUDE.md
        # "Known traps") — the entry script must invoke it before main().
        src = (ROOT / "optionspilot_app.py").read_text(encoding="utf-8")
        body = src.split('if __name__ == "__main__":', 1)[1]
        assert body.index("unblock_bundle()") < body.index("main(args)")
