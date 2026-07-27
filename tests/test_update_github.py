"""GitHub Releases client: parsing, installer selection, channel, retries."""

from __future__ import annotations

import pytest

from optionspilot.update.github_api import (
    INSTALLER_RE,
    GitHubReleases,
    parse_release,
)
from optionspilot.update.transport import NetworkError
from tests.update_helpers import FakeOpener, release_json, releases_response


class TestInstallerPattern:
    @pytest.mark.parametrize("name", [
        "OptionsPilot-Setup-v0.5.0.exe",
        "OptionsPilot-Setup-v1.0.0-beta.1.exe",
        "optionspilot-setup-v0.5.0.exe",
    ])
    def test_matches_installer(self, name):
        assert INSTALLER_RE.search(name)

    @pytest.mark.parametrize("name", [
        "OptionsPilot-v0.5.0.zip", "Source code (zip)", "checksums.txt",
        "OptionsPilotSetup.msi", "setup.exe",
    ])
    def test_rejects_non_installer(self, name):
        assert not INSTALLER_RE.search(name)


class TestParseRelease:
    def test_selects_installer_asset_only(self):
        doc = release_json("v0.5.0", extra_assets=[
            {"name": "OptionsPilot-v0.5.0.zip", "size": 999,
             "browser_download_url": "https://x/zip"}])
        rel = parse_release(doc)
        assert rel.has_installer
        assert rel.installer.name == "OptionsPilot-Setup-v0.5.0.exe"
        assert rel.installer.size == 1000

    def test_no_installer_when_absent(self):
        rel = parse_release(release_json("v0.5.0", installer_size=None))
        assert rel is not None and not rel.has_installer

    def test_unparseable_tag_returns_none(self):
        assert parse_release({"tag_name": "nightly"}) is None

    def test_published_at_parsed(self):
        rel = parse_release(release_json("v0.5.0"))
        assert rel.published_at is not None
        assert rel.published_at.year == 2026


class TestClient:
    def _client(self, opener):
        return GitHubReleases("owner/repo", api_base="https://api.test",
                              opener=opener, sleep=lambda s: None)

    def test_list_skips_drafts_and_unparseable(self):
        opener = FakeOpener({"/releases": releases_response(
            release_json("v0.5.0"),
            release_json("v0.6.0", draft=True),
            {"tag_name": "weird"},
        )})
        rels = self._client(opener).list_releases()
        assert [str(r.version) for r in rels] == ["0.5.0"]

    def test_latest_stable_ignores_prerelease(self):
        opener = FakeOpener({"/releases": releases_response(
            release_json("v0.5.0"),
            release_json("v0.6.0-beta.1", prerelease=True),
        )})
        latest = self._client(opener).latest_release()
        assert str(latest.version) == "0.5.0"

    def test_beta_channel_includes_prerelease(self):
        opener = FakeOpener({"/releases": releases_response(
            release_json("v0.5.0"),
            release_json("v0.6.0-beta.1", prerelease=True),
        )})
        latest = self._client(opener).latest_release(include_prereleases=True)
        assert str(latest.version) == "0.6.0-beta.1"

    def test_latest_is_highest_version_not_list_order(self):
        opener = FakeOpener({"/releases": releases_response(
            release_json("v0.4.9"), release_json("v0.4.10"), release_json("v0.5.0"),
        )})
        assert str(self._client(opener).latest_release().version) == "0.5.0"

    def test_empty_releases_returns_none(self):
        opener = FakeOpener({"/releases": releases_response()})
        assert self._client(opener).latest_release() is None

    def test_retries_then_succeeds(self):
        # First attempt a retryable failure, second succeeds.
        opener = FakeOpener({"/releases": [
            NetworkError("timeout", retryable=True),
            releases_response(release_json("v0.5.0")),
        ]})
        latest = self._client(opener).latest_release()
        assert str(latest.version) == "0.5.0"
        assert len(opener.calls) == 2

    def test_permanent_failure_not_retried(self):
        opener = FakeOpener({"/releases":
                             NetworkError("404", status=404, retryable=False)})
        with pytest.raises(NetworkError):
            self._client(opener).latest_release()
        assert len(opener.calls) == 1
