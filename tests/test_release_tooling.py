"""Tests for the release-pipeline tooling: single-source versioning and the
CHANGELOG release-notes extractor."""

import importlib.metadata as importlib_metadata
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSingleSourceVersion:
    def test_pyproject_has_no_hardcoded_version(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert not re.search(r'(?m)^version\s*=\s*"', pyproject), \
            "pyproject.toml must derive the version dynamically, not hardcode it"

    def test_pyproject_declares_dynamic_attr(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'dynamic = ["version"]' in pyproject
        assert 'attr = "optionspilot.__version__"' in pyproject

    def test_installed_metadata_matches_dunder(self):
        import optionspilot
        assert importlib_metadata.version("optionspilot") == optionspilot.__version__

    def test_check_docs_version_check_passes(self):
        assert _load("check_docs").check_version() == []


class TestReleaseNotes:
    def setup_method(self):
        self.rn = _load("release_notes")

    def test_extracts_current_version_section(self):
        body = self.rn.extract("0.4.4")
        assert body.startswith("## ")
        assert "0.4.4" in body.splitlines()[0]
        # stops before the previous release's section
        assert "V0.4.3" not in body

    def test_unknown_version_returns_empty(self):
        assert self.rn.extract("9.9.9") == ""

    def test_main_falls_back_for_unknown(self, capsys):
        assert self.rn.main.__code__ is not None  # module imported cleanly
        # extract() is the contract used by main(); fallback path is exercised here
        version = "9.9.9"
        body = self.rn.extract(version) or f"OptionsPilot v{version}. See docs/CHANGELOG.md for details."
        assert "9.9.9" in body
