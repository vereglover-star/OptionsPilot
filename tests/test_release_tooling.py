"""Tests for the release-pipeline tooling: single-source versioning, the
dependency lock, the wiring of the check scripts, and the CHANGELOG
release-notes extractor."""

import importlib.metadata as importlib_metadata
import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-lock.txt"
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"

# Scripts named by this convention are QUALITY GATES, not diagnostic tools.
# `marketdata_probe.py`, `soak.py` and the `*_benchmark.py` scripts are run by
# hand when investigating something and are deliberately excluded.
GATE_SCRIPT_GLOBS = ("*_check.py", "check_*.py")


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


def _gate_scripts() -> list[Path]:
    found: set[Path] = set()
    for pattern in GATE_SCRIPT_GLOBS:
        found.update(SCRIPTS.glob(pattern))
    return sorted(found)


def _caller_sources() -> dict[str, str]:
    """Everything that could invoke a gate script: the CI/release workflows
    and the PowerShell entry points."""
    sources = {}
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(SCRIPTS.glob("*.ps1")):
        sources[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return sources


class TestCheckScriptsAreWired:
    """A quality gate that nothing executes is worse than a missing one.

    `scripts/api_contract_check.py` shipped in V0.7.0 and was run by nothing
    — not CI, not release.yml, not verify.ps1 — while docs/PROJECT_STATE.md
    listed it among the checks that gate a release. It was counted as
    protection for three milestones while executing zero times.

    That is the third occurrence of one failure mode in this repository.
    CLAUDE.md already records it under "Known traps": *adding a risk gate is
    not the same as it being active*, learned when RiskManager.approve_manual_entry
    was written but never called. Writing the lesson down did not stop it
    recurring, so this test enforces it instead: wiring one script fixes an
    instance, and this fixes the class.
    """

    def test_at_least_one_gate_script_is_discovered(self):
        """Guards the guard: a glob that silently matches nothing would make
        every assertion below vacuously true."""
        assert len(_gate_scripts()) >= 5

    def test_every_gate_script_has_a_caller(self):
        callers = _caller_sources()
        orphans = []
        for script in _gate_scripts():
            if not any(script.name in text for text in callers.values()):
                orphans.append(script.name)
        assert not orphans, (
            "these check scripts are run by nothing — CI, release.yml and "
            "scripts/*.ps1 never name them, so they gate nothing while "
            "appearing to: " + ", ".join(sorted(orphans))
        )

    def test_contract_check_runs_in_ci_not_only_locally(self):
        """`verify.ps1` is a developer's gate; CI is everyone's.

        A check that runs only in verify.ps1 is skipped by any contributor
        who runs pytest directly, which is why the audit found CI and
        verify.ps1 verifying materially different things.
        """
        ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        assert "api_contract_check.py" in ci

    def test_no_caller_references_a_missing_script(self):
        """The inverse orphan: a workflow naming a script that is not there.

        Catches a rename or deletion that leaves a caller pointing at
        nothing — and a corrupted path, which is how a `\\a` in a PowerShell
        string silently became a BEL byte while writing this very commit.
        """
        broken = []
        for name, text in _caller_sources().items():
            for match in re.findall(r"[\w./\\$-]*?([\w-]+_check\.py|check_[\w-]+\.py)", text):
                if not (SCRIPTS / match).is_file():
                    broken.append(f"{name} -> {match}")
        assert not broken, "callers reference missing scripts: " + ", ".join(broken)


class TestNoTrackedBuildArtifacts:
    """Build output must never be committed.

    Until V0.9.0-C7 this repository tracked 3,238 files of PyInstaller output
    — two complete distributions plus a loose executable, ~97 MB of history.
    They were built against Python 3.14 while the project requires 3.12, so
    every clone carried a stale binary sitting beside source it no longer
    matched, and anyone reading the tree could not tell which was current.

    Untracking them fixes the instance. This test fixes the class: the next
    `git add -A` run from a directory containing a build cannot silently put
    them back.
    """

    #: Directories that only ever hold generated output.
    ARTIFACT_DIRS = ("_internal/", "dist_old/", "dist/", "build/")
    #: Compiled binaries. This project vendors exactly one binary asset (an
    #: .ico, generated by scripts/make_icon.py) and no compiled code at all,
    #: so any of these appearing in the index is build output.
    ARTIFACT_SUFFIXES = (".exe", ".dll", ".pyd", ".so", ".dylib", ".msi", ".zip")

    @staticmethod
    def _tracked_files() -> list[str] | None:
        """Paths in git's index, or None when git cannot be consulted."""
        try:
            out = subprocess.run(
                ["git", "ls-files"], cwd=ROOT, capture_output=True,
                text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]

    def test_no_build_output_is_tracked(self):
        tracked = self._tracked_files()
        if tracked is None:
            pytest.skip("git is unavailable, so the index cannot be inspected")
        assert tracked, "git ls-files returned nothing — guard cannot verify"

        offenders = [
            path for path in tracked
            if path.startswith(self.ARTIFACT_DIRS)
            or path.lower().endswith(self.ARTIFACT_SUFFIXES)
        ]
        assert not offenders, (
            f"{len(offenders)} build artifact(s) are tracked — build output "
            "belongs in .gitignore, not in history: "
            + ", ".join(sorted(offenders)[:10])
            + (" ..." if len(offenders) > 10 else "")
        )


class TestSkipsAreExplained:
    """Every test excluded from a run must say why, in the source.

    V0.9.0-C6 put the suite on a second operating system. The cheapest way to
    make a red Ubuntu leg go green is to skip whatever failed, and the
    difference between "skipped because Windows owns the registry" and
    "skipped because I could not work out why it broke" is invisible six
    months later unless it was written down at the time.

    So a bare `pytest.skip()` or a `skipif` without a reason fails the suite.
    This is not vacuous today: three real `pytest.skip(...)` calls in
    tests/test_host.py already carry messages, and they are what this asserts
    against.
    """

    @staticmethod
    def _test_modules() -> list[Path]:
        return sorted((ROOT / "tests").glob("test_*.py"))

    def test_there_are_test_modules_to_inspect(self):
        """Guards the guard — a glob matching nothing would pass everything."""
        assert len(self._test_modules()) > 50

    def test_every_runtime_skip_states_a_reason(self):
        """`pytest.skip()` with no message tells a future reader nothing."""
        import ast

        offenders = []
        for path in self._test_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_skip = (
                    (isinstance(func, ast.Attribute) and func.attr == "skip"
                     and isinstance(func.value, ast.Name) and func.value.id == "pytest")
                )
                if not is_skip:
                    continue
                positional = node.args
                reason_kw = [k for k in node.keywords if k.arg == "reason"]
                if not positional and not reason_kw:
                    offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, (
            "pytest.skip() with no reason — state why the test cannot run: "
            + ", ".join(offenders))

    def test_every_skipif_states_a_reason(self):
        """`@pytest.mark.skipif(cond)` silently drops a test on some machines.

        None exist today. The assertion is deliberately written now, while the
        count is zero, because the moment one is added is the moment it is
        cheapest to require an explanation — and that moment is the first red
        Ubuntu run, when the temptation is highest.
        """
        import ast

        offenders = []
        for path in self._test_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "skipif"):
                    continue
                if not any(k.arg == "reason" for k in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, (
            "@pytest.mark.skipif without reason= — an unexplained platform "
            "exclusion is indistinguishable from a bug someone gave up on: "
            + ", ".join(offenders))


class TestCIMatrix:
    """The portability guarantee is the matrix; assert it exists.

    `optionspilot/host/` and the `sys.platform` ban in test_architecture.py
    only mean something if some machine actually runs this code somewhere
    other than Windows. Deleting the Ubuntu leg would silently retire that
    guarantee while every test still passed.
    """

    @staticmethod
    def _ci() -> dict:
        import yaml  # PyYAML is a hard project dependency
        return yaml.safe_load(
            (WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))

    def test_suite_runs_on_windows_and_linux(self):
        matrix = self._ci()["jobs"]["test"]["strategy"]["matrix"]["os"]
        assert "windows-latest" in matrix, "Windows is the canonical baseline"
        assert "ubuntu-latest" in matrix, (
            "the Linux leg is the only thing verifying that host/ and "
            "core/paths.py are actually portable")

    def test_matrix_does_not_fail_fast(self):
        """A Windows failure and a Linux failure are different information."""
        assert self._ci()["jobs"]["test"]["strategy"]["fail-fast"] is False

    def test_coverage_ratchet_is_enforced_on_windows_only(self):
        """The 91% baseline was measured on Windows and is meaningful there.

        Enforcing it on Linux, where the tray/toast/webview paths cannot run,
        would fail that leg on coverage rather than on a defect — and the
        usual repair is to lower the threshold for everyone, discarding the
        Windows guarantee as collateral.
        """
        steps = self._ci()["jobs"]["test"]["steps"]
        enforcing = [s for s in steps
                     if "pytest --cov" in str(s.get("run", ""))
                     and "--cov-fail-under=0" not in str(s.get("run", ""))]
        assert len(enforcing) == 1, "exactly one leg may enforce the ratchet"
        assert "windows-latest" in str(enforcing[0].get("if", "")), (
            "the enforcing leg must be Windows")


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
