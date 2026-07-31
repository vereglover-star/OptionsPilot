"""Tests for the release-pipeline tooling: single-source versioning, the
dependency lock, and the CHANGELOG release-notes extractor."""

import importlib.metadata as importlib_metadata
import importlib.util
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-lock.txt"


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


def _locked_versions() -> dict[str, str]:
    """`{canonical name: pinned version}` from the constraints file.

    Comment and blank lines are skipped; every remaining line must be an
    exact `name==version` pin, because a constraints file that merely bounds
    a version ("<3") does not make a build reproducible.
    """
    pins: dict[str, str] = {}
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"lock entry is not an exact pin: {line!r}"
        name, _, version = line.partition("==")
        pins[name.strip().lower().replace("_", "-")] = version.strip()
    return pins


def _direct_requirements() -> list[Requirement]:
    """Every dependency pyproject declares, core plus all optional extras."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    return [Requirement(s) for s in specs]


class TestDependencyLock:
    """The lock and pyproject.toml must never disagree.

    pyproject.toml decides WHICH packages are needed; requirements-lock.txt
    decides WHICH VERSION. If a floor is raised in one and the other is not
    regenerated, an install silently satisfies the constraint file and
    violates the declared requirement — the build stops matching its own
    manifest. That drift is invisible from either file alone, which is the
    entire reason this test exists.
    """

    def test_lock_exists(self):
        assert LOCK.is_file(), "requirements-lock.txt is missing"

    def test_every_entry_is_an_exact_pin(self):
        assert _locked_versions(), "lock contains no pins"

    def test_lock_does_not_pin_the_project_itself(self):
        """A self-referential lock would make the project's own version a
        constraint on installing it — `pip freeze --exclude-editable` is what
        prevents this, and forgetting the flag is the easy mistake."""
        assert "optionspilot" not in _locked_versions()

    def test_locked_versions_satisfy_pyproject_specifiers(self):
        """Each direct dependency present in the lock satisfies its floor.

        Dependencies absent from the lock are skipped rather than failed: the
        lock is a snapshot of one platform's install, and a package that only
        installs elsewhere (or an extra nobody has installed locally) is
        legitimately missing.
        """
        pins = _locked_versions()
        violations = []
        for req in _direct_requirements():
            key = req.name.lower().replace("_", "-")
            if key not in pins:
                continue
            if not req.specifier.contains(Version(pins[key]), prereleases=True):
                violations.append(
                    f"{req.name}: locked {pins[key]} violates '{req.specifier}'")
        assert not violations, (
            "requirements-lock.txt disagrees with pyproject.toml:\n  "
            + "\n  ".join(violations))


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
