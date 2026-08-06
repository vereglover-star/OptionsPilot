"""Tests for the one-command release automation (`scripts/release.ps1`).

A release script is the one piece of tooling whose bugs are expensive and
late: by the time anything is wrong, a tag is public, a Release is published,
and the auto-updater is already offering an installer to people. So the parts
of it that involve a judgement live in `scripts/lib/release_support.py`, and
this file is why they live there.

Two kinds of test:

  * BEHAVIOURAL, against the Python module — version location handling, semver
    ordering, remote-URL parsing, and the failure-report logic that decides
    which job and which step a failed workflow run failed at. These run the
    real code against real and synthetic inputs.
  * STRUCTURAL, against the PowerShell. `release.ps1` cannot be executed from
    pytest (it wants a venv, a remote and six minutes), so what is asserted
    instead is the shape the safety argument depends on: that verification
    happens before the tag, that the tag happens before the push, that every
    mutating step registers an undo, that no git command in the whole pipeline
    carries --force or --no-verify, and that `-SkipVerify` cannot be reached
    without `-DryRun`. Those are exactly the properties that, if they ever
    silently stopped holding, would be discovered by a bad release.
"""

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LIB = SCRIPTS / "lib"
RELEASE_PS1 = SCRIPTS / "release.ps1"

LIB_FILES = (
    "ReleaseConfig.ps1",
    "ReleaseLog.ps1",
    "ReleaseRollback.ps1",
    "ReleaseGit.ps1",
    "ReleaseVersion.ps1",
    "ReleaseGitHub.ps1",
)


def _load(path: Path, name: str):
    """Import a module by path.

    The sys.modules registration is load-bearing: @dataclass resolves a field's
    type through `sys.modules[cls.__module__]`, so a module executed without
    being registered raises AttributeError on its first dataclass.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


support = _load(LIB / "release_support.py", "release_support")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A miniature repository holding just the files the version lives in."""
    (tmp_path / "optionspilot").mkdir()
    (tmp_path / "optionspilot" / "__init__.py").write_text(
        '"""OptionsPilot — AI-powered options paper-trading system."""\n'
        "\n"
        '__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PROJECT_STATUS.md").write_text(
        "# PROJECT_STATUS.md\n"
        "\n"
        "## Current version\n"
        "\n"
        "`1.2.3` — single source of truth: `optionspilot/__init__.py`.\n"
        "\n"
        "## Something else\n"
        "\n"
        "Version 1.2.3 is mentioned here in prose and must NOT be rewritten.\n",
        encoding="utf-8",
    )
    return tmp_path


# ── the version's locations ──────────────────────────────────────────────────

class TestVersionLocations:
    """The table in release_support.LOCATIONS is the whole claim.

    "The version has a single source of truth" is true of the CODE and has
    never been true of the DOCUMENTATION: `docs/PROJECT_STATUS.md` announced
    0.5.0 while `__version__` was 0.8.2, through four releases. That copy is
    enforced by scripts/check_docs.py, so the drift was always detectable and
    simply nobody ran into it at the right moment. Automating the release means
    the bump has to write every copy, and this class is what keeps the list of
    copies honest.
    """

    def test_the_first_location_is_the_source_of_truth(self):
        assert support.LOCATIONS[0].path == "optionspilot/__init__.py"

    def test_every_location_exists_and_matches_exactly_once(self):
        for location in support.LOCATIONS:
            path = ROOT / location.path
            assert path.is_file(), f"{location.path} does not exist"
            found = re.findall(location.pattern, path.read_text(encoding="utf-8"))
            assert len(found) == location.occurrences, (
                f"{location.path}: expected {location.occurrences} version "
                f"literal(s), found {len(found)}")

    def test_every_location_explains_why_it_holds_a_copy(self):
        for location in support.LOCATIONS:
            assert len(location.why) > 30, (
                f"{location.path} holds a duplicate of the version and does not "
                f"say why — the next person will delete it or add a second one")

    def test_the_live_repository_is_consistent(self):
        assert support.check(ROOT) == []

    def test_the_writer_and_the_checker_agree(self):
        """`bump_version` writes PROJECT_STATUS.md; `check_docs` polices it.

        Two regexes over one fact is the drift this repo keeps re-learning
        (health counters in V0.5.3, the settings ranking in V0.5.7). If the
        writer's pattern and the checker's pattern ever stop finding the same
        string, the release bumps a version the gate cannot see — and the
        failure is silent in the direction that matters, because check_docs
        would simply keep passing on the OLD value.
        """
        check_docs = _load(SCRIPTS / "check_docs.py", "check_docs")
        status = (ROOT / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")

        checker = re.compile(
            r"^##\s+Current version\s*$\n+`(?P<claim>\d+\.\d+\.\d+)`", re.M)
        checker_match = checker.search(status)
        assert checker_match, "check_docs.py's own pattern no longer matches"

        writer = next(loc for loc in support.LOCATIONS
                      if loc.path == "docs/PROJECT_STATUS.md")
        writer_match = re.search(writer.pattern, status)
        assert writer_match, "the release's write pattern no longer matches"

        assert writer_match.group("version") == checker_match.group("claim")
        assert checker_match.group("claim") == check_docs.declared_version()

    def test_derived_consumers_are_named(self):
        """The "no duplicated version logic" claim is only checkable if the
        consumers that derive it are written down."""
        assert len(support.DERIVED) >= 5
        blob = " ".join(d.what + d.how for d in support.DERIVED).lower()
        for expected in ("pyproject", "installer", "zip", "tag", "release notes"):
            assert expected in blob, f"{expected} is not named among the derived consumers"


class TestSync:
    def test_writes_every_location(self, repo: Path):
        report = support.sync(repo, "4.5.6")
        assert report["errors"] == []
        assert sorted(report["changed"]) == [
            "docs/PROJECT_STATUS.md", "optionspilot/__init__.py"]
        assert support.read_version(repo) == "4.5.6"
        assert "`4.5.6`" in (repo / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")

    def test_leaves_other_prose_alone(self, repo: Path):
        """The pattern is anchored on the heading, not on the digits.

        A naive whole-file replace would rewrite every "1.2.3" in the document,
        including the historical ones a status file legitimately contains.
        """
        support.sync(repo, "4.5.6")
        status = (repo / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")
        assert "Version 1.2.3 is mentioned here in prose" in status

    def test_is_idempotent(self, repo: Path):
        support.sync(repo, "4.5.6")
        again = support.sync(repo, "4.5.6")
        assert again["errors"] == []
        assert again["changed"] == []
        assert sorted(again["unchanged"]) == [
            "docs/PROJECT_STATUS.md", "optionspilot/__init__.py"]

    def test_dry_run_writes_nothing(self, repo: Path):
        before = {loc.path: (repo / loc.path).read_bytes() for loc in support.LOCATIONS}
        report = support.sync(repo, "4.5.6", dry_run=True)
        assert report["errors"] == []
        assert sorted(report["changed"]) == [
            "docs/PROJECT_STATUS.md", "optionspilot/__init__.py"]
        for path, content in before.items():
            assert (repo / path).read_bytes() == content

    def test_reports_the_previous_version(self, repo: Path):
        assert support.sync(repo, "4.5.6", dry_run=True)["previous"] == "1.2.3"

    @pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "v1.2.3", "1.2.3-rc1", "", "abc"])
    def test_refuses_anything_that_is_not_x_y_z(self, repo: Path, bad: str):
        report = support.sync(repo, bad)
        assert report["errors"]
        assert support.read_version(repo) == "1.2.3"

    def test_a_location_that_stops_matching_is_an_error_not_a_skip(self, repo: Path):
        """The failure mode that would ship a half-bumped release.

        If someone restructures PROJECT_STATUS.md so the heading no longer
        parses, the tolerant behaviour is to write what can be written and move
        on — which produces a release whose code says 4.5.6 and whose
        documentation says 1.2.3, exactly the state this automation exists to
        make impossible. It must fail loudly instead.
        """
        (repo / "docs" / "PROJECT_STATUS.md").write_text(
            "# PROJECT_STATUS.md\n\n## Version\n\n`1.2.3`\n", encoding="utf-8")
        report = support.sync(repo, "4.5.6")
        assert report["errors"], "a non-matching location was silently skipped"
        assert "PROJECT_STATUS" in report["errors"][0]

    def test_a_missing_file_is_an_error(self, repo: Path):
        (repo / "docs" / "PROJECT_STATUS.md").unlink()
        report = support.sync(repo, "4.5.6")
        assert any("not found" in problem for problem in report["errors"])


class TestCheck:
    def test_passes_when_everything_agrees(self, repo: Path):
        assert support.check(repo) == []

    def test_reports_a_disagreeing_location(self, repo: Path):
        status = repo / "docs" / "PROJECT_STATUS.md"
        status.write_text(
            status.read_text(encoding="utf-8").replace("`1.2.3`", "`0.0.1`"),
            encoding="utf-8")
        problems = support.check(repo)
        assert len(problems) == 1
        assert "0.0.1" in problems[0] and "1.2.3" in problems[0]


class TestVersionOrdering:
    def test_newer_older_same(self):
        assert support.compare_versions("0.9.3", "0.9.2") == 1
        assert support.compare_versions("0.9.2", "0.9.3") == -1
        assert support.compare_versions("0.9.2", "0.9.2") == 0

    def test_compares_numerically_not_lexically(self):
        """`"0.9.10" < "0.9.9"` as strings, and a release script that got this
        wrong would refuse the tenth patch release of a minor line."""
        assert support.compare_versions("0.9.10", "0.9.9") == 1
        assert support.compare_versions("0.10.0", "0.9.99") == 1
        assert support.compare_versions("1.0.0", "0.99.99") == 1

    @pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3-rc1", "", "1.2.x"])
    def test_refuses_a_version_it_cannot_order(self, bad: str):
        with pytest.raises(ValueError):
            support.parse_version(bad)


class TestRepoSlug:
    @pytest.mark.parametrize("url", [
        "https://github.com/vereglover-star/OptionsPilot.git",
        "https://github.com/vereglover-star/OptionsPilot",
        "https://github.com/vereglover-star/OptionsPilot/",
        "git@github.com:vereglover-star/OptionsPilot.git",
        "git@github.com:vereglover-star/OptionsPilot",
        "ssh://git@github.com/vereglover-star/OptionsPilot.git",
        "https://user@github.com/vereglover-star/OptionsPilot.git",
    ])
    def test_reads_owner_and_repo_from_every_remote_form(self, url: str):
        assert support.repo_slug(url) == "vereglover-star/OptionsPilot"

    @pytest.mark.parametrize("bad", ["", "   ", "not a url", "OptionsPilot",
                                     "https://github.com/onlyowner"])
    def test_returns_none_rather_than_guessing(self, bad: str):
        """A wrong slug points the monitor at somebody else's repository and
        reports their build. None is the only safe answer."""
        assert support.repo_slug(bad) is None


class TestChangelogGate:
    def test_finds_a_released_version(self):
        heading = support.changelog_heading(ROOT, "0.9.2")
        assert heading is not None and heading.startswith("## ")

    def test_absent_version_returns_none(self):
        assert support.changelog_heading(ROOT, "99.98.97") is None

    def test_uses_the_same_rule_as_the_release_notes_extractor(self):
        """The gate exists to predict what will be published.

        If it used a different rule from `scripts/release_notes.py`, it would
        pass on versions that then publish the generic stub — which is the exact
        thing it is there to warn about.
        """
        release_notes = _load(SCRIPTS / "release_notes.py", "release_notes")
        for version in ("0.9.2", "99.98.97"):
            gate = support.changelog_heading(ROOT, version)
            extracted = release_notes.extract(version)
            assert (gate is None) == (extracted == ""), version
            if gate is not None:
                assert extracted.splitlines()[0].strip() == gate


# ── reading a workflow run ───────────────────────────────────────────────────

def _run(conclusion="failure", status="completed", **extra):
    payload = {
        "id": 42, "run_number": 7, "status": status, "conclusion": conclusion,
        "html_url": "https://github.com/o/r/actions/runs/42",
        "display_title": "Release v0.9.3", "run_attempt": 1,
    }
    payload.update(extra)
    return payload


def _job(name, conclusion, steps=(), job_id=1):
    return {
        "id": job_id, "name": name, "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://github.com/o/r/actions/runs/42/job/{job_id}",
        "steps": list(steps),
    }


def _step(name, conclusion, number):
    return {"name": name, "status": "completed",
            "conclusion": conclusion, "number": number}


#: A realistic GitHub workflow-run id. These passed Int32 years ago — they are
#: around 1.7e10 today — and every test below uses ids in this range on purpose,
#: because the defect this class exists for was invisible to small numbers.
BIG_ID = 17_592_186_044_416


class TestNewestRun:
    """Which of several runs for one head sha is the one just triggered.

    A re-run of the same commit creates a second run against the same sha, so
    this is a real question with an expensive wrong answer: the watcher reports
    a stale run's conclusion as the outcome of the release just pushed.

    It lived in PowerShell as `Sort-Object -Property {[int]$_.id}` until it was
    moved here. `[int]` is `System.Int32`; the cast failed on every element; the
    failure was NON-TERMINATING under the release script's own
    `$ErrorActionPreference = "Continue"`; and `-Descending` over the resulting
    null keys handed back GitHub's newest-first response REVERSED. The
    monitoring therefore watched the oldest run and never said so.
    """

    @staticmethod
    def _runs(*offsets):
        return [{"id": BIG_ID + n, "run_number": n} for n in offsets]

    def test_picks_the_largest_id(self):
        assert support.newest_run(self._runs(2, 1, 0))["id"] == BIG_ID + 2

    def test_picks_it_regardless_of_the_order_it_arrives_in(self):
        for order in ((0, 1, 2), (2, 0, 1), (1, 2, 0)):
            assert support.newest_run(self._runs(*order))["id"] == BIG_ID + 2

    def test_a_re_run_beats_the_original(self):
        """The case the sort existed for, and the one it got wrong."""
        original, rerun = self._runs(0, 5)
        assert support.newest_run([original, rerun])["run_number"] == 5

    def test_ids_far_beyond_int32_are_ordered_numerically(self):
        """Not lexically, and not truncated. 2^31-1 is 2_147_483_647; every id
        here is four orders of magnitude past it."""
        runs = [{"id": 9_999_999_999}, {"id": 17_592_186_044_416},
                {"id": 2_147_483_648}]
        assert support.newest_run(runs)["id"] == 17_592_186_044_416

    def test_accepts_the_whole_api_response(self):
        payload = {"total_count": 2, "workflow_runs": self._runs(0, 3)}
        assert support.newest_run(payload)["id"] == BIG_ID + 3

    def test_accepts_a_single_run_object(self):
        """PowerShell's `ConvertTo-Json` unwraps a one-element array to a bare
        object, and one run is the ORDINARY case — so this shape is not a
        hypothetical, it is what most releases send."""
        assert support.newest_run({"id": BIG_ID, "run_number": 7})["run_number"] == 7

    def test_accepts_a_string_id(self):
        runs = [{"id": str(BIG_ID)}, {"id": str(BIG_ID + 9)}]
        assert support.newest_run(runs)["id"] == str(BIG_ID + 9)

    def test_a_boolean_id_never_wins(self):
        """`isinstance(True, int)` is True in Python, so an unguarded check
        reads `"id": true` as run number 1 — which would then lose to every
        real run, but only by accident."""
        runs = [{"id": True}, {"id": BIG_ID}]
        assert support.newest_run(runs)["id"] == BIG_ID

    def test_an_unreadable_id_never_wins(self):
        runs = [{"id": None}, {"id": "not-a-number"}, {"id": BIG_ID}, {}]
        assert support.newest_run(runs)["id"] == BIG_ID

    def test_falls_back_to_the_first_when_no_id_can_be_read(self):
        """Stated behaviour, not an accident: the caller needs a run and the
        response's own ordering is the only signal left."""
        runs = [{"name": "first"}, {"name": "second"}]
        assert support.newest_run(runs)["name"] == "first"

    def test_a_tie_keeps_githubs_own_order(self):
        runs = [{"id": BIG_ID, "n": "first"}, {"id": BIG_ID, "n": "second"}]
        assert support.newest_run(runs)["n"] == "first"

    @pytest.mark.parametrize("empty", [None, [], {}, "", 0, {"workflow_runs": []},
                                       ["not a dict", 5], {"workflow_runs": None}])
    def test_nothing_to_pick_is_none_rather_than_a_crash(self, empty):
        assert support.newest_run(empty) is None

    def test_the_chosen_run_survives_json_both_ways(self):
        """The id crosses two process boundaries — Python to PowerShell and
        back — and must arrive as the same integer, not as 1.7592186044416E+13.
        """
        chosen = support.newest_run(self._runs(0, 4))
        text = json.dumps(chosen)
        assert str(BIG_ID + 4) in text
        assert json.loads(text)["id"] == BIG_ID + 4


class TestStdinIsBomTolerant:
    """PowerShell 5.1 prefixes a UTF-8 BOM onto a string piped to a native
    process, and `json.load` refuses it outright.

    Every sub-command that reads stdin was affected — `summarize-run` and
    `summarize-release` as well as `pick-run` — and none of the tests around
    them could see it, because pytest hands Python a clean string. It showed up
    the first time the actual PowerShell → Python hand-off was executed rather
    than reasoned about. No release had been cut with this script yet, so the
    failure was waiting in the reporting path of the first real one.
    """

    BOM = "\ufeff"

    @staticmethod
    def _feed(monkeypatch, text: str):
        monkeypatch.setattr(support.sys, "stdin", io.StringIO(text),
                            raising=False)

    def test_a_bom_prefixed_payload_parses(self, monkeypatch):
        self._feed(monkeypatch, self.BOM + json.dumps({"id": BIG_ID}))
        assert support.read_stdin_json() == {"id": BIG_ID}

    def test_a_clean_payload_still_parses(self, monkeypatch):
        self._feed(monkeypatch, json.dumps([{"id": BIG_ID}]))
        assert support.read_stdin_json() == [{"id": BIG_ID}]

    def test_empty_stdin_is_none_rather_than_a_crash(self, monkeypatch):
        for blank in ("", "   ", "\n", self.BOM):
            self._feed(monkeypatch, blank)
            assert support.read_stdin_json() is None

    def test_a_bom_does_not_reach_the_decision(self, monkeypatch):
        """The end-to-end shape: BOM in, correct run out."""
        payload = {"workflow_runs": [{"id": BIG_ID}, {"id": BIG_ID + 1}]}
        self._feed(monkeypatch, self.BOM + json.dumps(payload))
        assert support.newest_run(support.read_stdin_json())["id"] == BIG_ID + 1

    def test_every_stdin_reader_goes_through_it(self):
        """One reader, so a future sub-command cannot reintroduce the bug by
        reaching for the obvious `json.load(sys.stdin)`."""
        source = (LIB / "release_support.py").read_text(encoding="utf-8")
        assert "json.load(sys.stdin)" not in source, \
            "read stdin through read_stdin_json(), which tolerates a BOM"


class TestSummarizeRun:
    def test_success(self):
        summary = support.summarize_run(_run(conclusion="success"), {"jobs": []})
        assert summary["ok"] is True
        assert "successfully" in summary["reason"]
        assert summary["failed_job"] is None

    def test_names_the_failing_job_and_step(self):
        jobs = {"jobs": [
            _job("Test & selftest (windows-latest)", "success",
                 [_step("Run test suite", "success", 4)], job_id=1),
            _job("Build, package & publish", "failure", [
                _step("Set up job", "success", 1),
                _step("Build the executable", "failure", 5),
                _step("Package the release zip", "skipped", 6),
            ], job_id=2),
        ]}
        summary = support.summarize_run(_run(), jobs)
        assert summary["ok"] is False
        assert summary["failed_job"] == "Build, package & publish"
        assert summary["failed_step"] == "Build the executable"
        assert summary["failed_step_number"] == 5
        assert summary["failed_job_id"] == 2
        assert "Build the executable" in summary["reason"]
        assert summary["run_url"].endswith("/runs/42")

    def test_names_the_cause_not_the_consequence(self):
        """When `test` fails, `build` is SKIPPED because of `needs: test`.

        Reporting the skipped downstream job would send someone to look at a
        PyInstaller step that never ran. `skipped` is deliberately absent from
        FAILING_CONCLUSIONS for exactly this.
        """
        jobs = {"jobs": [
            _job("Test & selftest (windows-latest)", "failure",
                 [_step("Run test suite (with coverage ratchet)", "failure", 6)],
                 job_id=1),
            _job("Build, package & publish", "skipped", [], job_id=2),
        ]}
        summary = support.summarize_run(_run(), jobs)
        assert summary["failed_job"] == "Test & selftest (windows-latest)"
        assert summary["failed_step"] == "Run test suite (with coverage ratchet)"

    def test_failure_before_any_step_ran(self):
        summary = support.summarize_run(_run(), {"jobs": []})
        assert summary["ok"] is False
        assert summary["failed_step"] is None
        assert "no job reported a failing step" in summary["reason"]

    def test_job_failed_with_no_failing_step(self):
        jobs = {"jobs": [_job("Build", "failure", [_step("Set up job", "success", 1)])]}
        summary = support.summarize_run(_run(), jobs)
        assert summary["failed_job"] == "Build"
        assert summary["failed_step"] is None
        assert "without any single step" in summary["reason"]

    def test_cancelled_is_reported_as_itself(self):
        jobs = {"jobs": [_job("Build", "cancelled",
                              [_step("Build the executable", "cancelled", 5)])]}
        summary = support.summarize_run(_run(conclusion="cancelled"), jobs)
        assert summary["ok"] is False
        assert summary["failed_step_conclusion"] == "cancelled"
        assert "cancelled" in summary["reason"]

    def test_an_unfinished_run_says_so(self):
        summary = support.summarize_run(
            _run(conclusion=None, status="in_progress"), {"jobs": []})
        assert summary["ok"] is False
        assert "still in_progress" in summary["reason"]

    def test_survives_empty_input(self):
        summary = support.summarize_run(None, None)
        assert summary["ok"] is False
        assert summary["reason"]

    def test_carries_a_log_tail_when_one_was_fetched(self):
        summary = support.summarize_run(_run(), {"jobs": []}, log_tail="boom\ntraceback")
        assert summary["log_tail"] == "boom\ntraceback"


class TestSummarizeRelease:
    def test_lists_assets_with_sizes(self):
        release = {
            "html_url": "https://github.com/o/r/releases/tag/v0.9.3",
            "tag_name": "v0.9.3", "name": "OptionsPilot v0.9.3",
            "draft": False, "prerelease": False,
            "assets": [
                {"name": "OptionsPilot-Setup-v0.9.3.exe", "size": 52428800,
                 "browser_download_url": "https://example/setup"},
                {"name": "SHA256SUMS", "size": 200,
                 "browser_download_url": "https://example/sums"},
            ],
        }
        info = support.summarize_release(release)
        assert info["tag"] == "v0.9.3"
        assert [a["name"] for a in info["assets"]] == [
            "OptionsPilot-Setup-v0.9.3.exe", "SHA256SUMS"]
        assert info["assets"][0]["size_mb"] == 50.0

    def test_flags_draft_and_prerelease(self):
        """Both are states the in-app updater treats specially — a draft is
        always skipped and a prerelease only reaches the beta channel — so a
        release that quietly became one must be visible in the report."""
        info = support.summarize_release({"draft": True, "prerelease": True})
        assert info["draft"] is True and info["prerelease"] is True

    def test_survives_a_release_with_no_assets(self):
        assert support.summarize_release({})["assets"] == []


# ── the PowerShell contract ──────────────────────────────────────────────────

class TestReleaseScriptStructure:
    """What cannot be executed here is at least pinned in place.

    Every assertion below corresponds to a sentence in the safety argument
    `docs/RELEASE.md` makes. They are greps, and greps are shallow — but the
    alternative for each one is nothing at all, and the failure they guard
    against is a public bad release.
    """

    @staticmethod
    def _release() -> str:
        return RELEASE_PS1.read_text(encoding="utf-8")

    @staticmethod
    def _all_lib() -> str:
        return "\n".join(
            (LIB / name).read_text(encoding="utf-8") for name in LIB_FILES)

    @staticmethod
    def _code_only(text: str) -> str:
        """PowerShell source with comments removed.

        `ReleaseGit.ps1` says "Never --no-verify" and "No --force, ever" in the
        comments that explain those rules. A banned-string scan over raw source
        therefore fires on the very prose asserting the rule is followed — and
        the obvious repair is to delete the explanation, which is exactly
        backwards. Scan the code.
        """
        without_blocks = re.sub(r"<#.*?#>", "", text, flags=re.S)
        return "\n".join(
            re.sub(r"#.*$", "", line) for line in without_blocks.splitlines())

    def test_the_entry_point_exists(self):
        assert RELEASE_PS1.is_file()

    def test_every_helper_library_exists(self):
        for name in LIB_FILES:
            assert (LIB / name).is_file(), f"scripts/lib/{name} is missing"
        assert (LIB / "release_support.py").is_file()

    def test_every_helper_library_is_dot_sourced(self):
        text = self._release()
        for name in LIB_FILES:
            assert f"lib\\{name}" in text, f"{name} exists but nothing loads it"

    def test_version_is_positional_so_the_documented_form_works(self):
        """`.\\scripts\\release.ps1 0.9.3` is the whole promise."""
        assert re.search(r"\[Parameter\(Position\s*=\s*0,\s*Mandatory\s*=\s*\$true\)\]\[string\]\$Version",
                         self._release())

    def test_dry_run_is_supported(self):
        assert re.search(r"(?m)^\s*\[switch\]\$DryRun\s*,", self._release())

    def test_the_orchestrator_runs_no_git_command_itself(self):
        """All git goes through scripts/lib/ReleaseGit.ps1.

        This is what makes "does a release ever push before verifying"
        answerable by reading one short file. A `git` invocation that grew back
        into release.ps1 would be outside that argument.
        """
        offenders = [
            line for line in self._release().splitlines()
            if re.match(r"^\s*&?\s*git\s", line)
        ]
        assert not offenders, (
            "release.ps1 invokes git directly: " + "; ".join(offenders))

    def test_verification_precedes_the_tag_which_precedes_the_push(self):
        text = self._release()
        verify = text.index("verify.ps1")
        tag = text.index("New-GitTag")
        push = text.index("Push-GitBranch")
        assert verify < tag < push, (
            "the release phases are out of order — verification must gate the "
            "tag, and the tag must exist before anything is pushed")

    def test_every_mutating_step_registers_an_undo(self):
        text = self._release()
        assert text.count("Register-Rollback") >= 3, (
            "the version bump, the release commit and the tag must each "
            "register a rollback at the moment they succeed")
        for undo in ("Restore-GitFiles", "Reset-GitToSha", "Remove-GitLocalTag"):
            assert undo in text, f"nothing undoes: {undo}"

    def test_rollback_is_disarmed_only_after_the_push(self):
        text = self._release()
        assert "Disarm-Rollback" in text
        assert text.index("Push-GitBranch") < text.index("Disarm-Rollback"), (
            "rollback must stay armed until the commit is actually published")

    def test_skip_verify_cannot_be_reached_without_dry_run(self):
        text = self._release()
        assert re.search(r"\$SkipVerify\s+-and\s+-not\s+\$DryRun", text), (
            "-SkipVerify must be refused outside a dry run — a real release "
            "does not get to skip its own verification gate")

    def test_no_force_push_and_no_hook_bypass_anywhere(self):
        """Standing project rules, asserted rather than remembered.

        Deliberately not matching a bare `-f`: PowerShell's format operator is
        spelled `-f` too, and a guard that fires on `"{0}/{1}" -f $a, $b` is a
        guard someone deletes.
        """
        blob = self._code_only(self._release() + self._all_lib())
        for banned in ("--force", "--no-verify", "push -f", "--force-with-lease"):
            assert banned not in blob, f"the release pipeline contains {banned!r}"

    def test_the_release_branch_is_configured_in_one_place(self):
        config = (LIB / "ReleaseConfig.ps1").read_text(encoding="utf-8")
        assert "ReleaseBranch" in config
        others = [name for name in LIB_FILES if name != "ReleaseConfig.ps1"]
        for name in others:
            assert "ReleaseBranch =" not in (LIB / name).read_text(encoding="utf-8")

    def test_the_tag_prefix_matches_what_the_workflow_triggers_on(self):
        """release.yml fires on `tags: ["v*"]` and its guard compares
        "v$__version__" to the pushed tag. A different prefix here publishes
        nothing, silently."""
        config = (LIB / "ReleaseConfig.ps1").read_text(encoding="utf-8")
        assert re.search(r'TagPrefix\s*=\s*"v"', config)
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert 'tags: ["v*"]' in workflow

    def test_the_watched_workflow_file_exists(self):
        config = (LIB / "ReleaseConfig.ps1").read_text(encoding="utf-8")
        match = re.search(r'WorkflowFile\s*=\s*"([^"]+)"', config)
        assert match, "no WorkflowFile configured"
        assert (ROOT / ".github" / "workflows" / match.group(1)).is_file()

    def test_monitoring_does_not_require_the_github_cli(self):
        """`gh` is not installed on the machine that cuts these releases.

        A monitoring step that degraded to "go and look at the Actions tab"
        precisely there would not be a monitoring step.
        """
        github = (LIB / "ReleaseGitHub.ps1").read_text(encoding="utf-8")
        assert "Invoke-WebRequest" in github
        assert "api.github.com" in github

    def test_the_anonymous_api_rate_limit_is_accounted_for(self):
        """60 requests an hour against a 60-minute watch is 4 seconds of
        budget per minute of build. Polling at the configured interval without
        a token would report a rate limit as a failed release."""
        github = (LIB / "ReleaseGitHub.ps1").read_text(encoding="utf-8")
        assert "Get-GitHubPollInterval" in github

    def test_no_github_identifier_is_cast_to_int32(self):
        """`[int]` in PowerShell is `System.Int32`, and every id GitHub issues
        outgrew it years ago — runs and jobs are both around 1.7e10.

        The specific failure is worse than an overflow crash: PowerShell's cast
        failure inside a `Sort-Object` expression is NON-TERMINATING, and the
        release script runs under `$ErrorActionPreference = "Continue"`, so the
        sort silently returned an unsorted sequence and `-Descending` reversed
        it. The watcher followed the OLDEST run for the tag and reported its
        conclusion as the release's.

        Timeouts, poll intervals, HTTP status codes and step counters are all
        legitimately Int32 and are deliberately not swept up here — the rule is
        about identifiers, because that is where the range actually matters.
        """
        pattern = re.compile(r"\[int\]\s*\$[^\s;)}]*\b(id|Id|ID)\b")
        offenders = []
        for name in LIB_FILES + ("release.ps1",):
            path = LIB / name if name != "release.ps1" else RELEASE_PS1
            for number, line in enumerate(
                    self._code_only(path.read_text(encoding="utf-8")).splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{name}:{number}: {line.strip()}")
        assert not offenders, (
            "GitHub ids must not be cast to [int] (Int32). Use [int64], or "
            "better, decide it in release_support.py:\n  " + "\n  ".join(offenders))

    def test_choosing_which_run_to_watch_is_decided_in_python(self):
        """`release_support.py`'s own docstring says every *judgement* lives
        there so pytest can reach it. Picking one run out of several for a head
        sha is a judgement — it was inline PowerShell, it was wrong, and
        nothing could have caught it there."""
        github = (LIB / "ReleaseGitHub.ps1").read_text(encoding="utf-8")
        assert "pick-run" in github, \
            "Wait-GitHubWorkflowRun must delegate run selection to release_support.py"
        assert "Sort-Object" not in self._code_only(github), \
            "run selection belongs in release_support.py, not in a PowerShell sort"

    def test_a_one_element_run_list_is_not_flattened_on_the_way_out(self):
        """Piping a one-element array to `ConvertTo-Json` unwraps it to a bare
        object. One run is the ORDINARY case, so the pipeline form would have
        broken every release that was not a re-run."""
        github = (LIB / "ReleaseGitHub.ps1").read_text(encoding="utf-8")
        assert re.search(r"ConvertTo-Json\s+-InputObject\s+@\(", github), \
            "serialise the runs with -InputObject @(...), not through the pipeline"

    def test_tls12_is_forced(self):
        """PowerShell 5.1 negotiates TLS 1.0 by default; api.github.com has
        required 1.2 since 2018, and the failure reads like a network outage."""
        github = (LIB / "ReleaseGitHub.ps1").read_text(encoding="utf-8")
        assert "Tls12" in github


class TestDocumentationDescribesOneCommand:
    """The old process was six manual commands and four documents said so."""

    @staticmethod
    def _doc(name: str) -> str:
        path = ROOT / name if "/" in name or name.isupper() else ROOT / name
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("doc", [
        "README.md",
        "docs/RELEASE.md",
        "docs/RELEASE_CHECKLIST.md",
        "docs/AI_HANDOFF.md",
        "docs/ROADMAP.md",
    ])
    def test_the_one_command_is_documented(self, doc: str):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "release.ps1" in text, f"{doc} does not mention scripts/release.ps1"

    def test_release_doc_shows_the_positional_form(self):
        text = (ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
        assert re.search(r"release\.ps1\s+\d+\.\d+\.\d+", text), (
            "docs/RELEASE.md must show the actual invocation, "
            r"e.g. .\scripts\release.ps1 0.9.3")

    def test_release_doc_documents_the_dry_run(self):
        assert "-DryRun" in (ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
