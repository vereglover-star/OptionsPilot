"""UpdateService: the whole check -> download -> validate -> backup -> install flow."""

from __future__ import annotations

import time

from optionspilot.update.downloader import Downloader
from optionspilot.update.github_api import GitHubReleases
from optionspilot.update.installer import InstallerLauncher
from optionspilot.update.service import InMemoryPrefs, UpdateService
from optionspilot.update.transport import NetworkError
from tests.update_helpers import FakeOpener, FakeResponse, release_json, releases_response


def _client(*docs, error=None):
    route = error if error is not None else releases_response(*docs)
    return GitHubReleases("owner/repo", api_base="https://api.test",
                          opener=FakeOpener({"/releases": route}),
                          sleep=lambda s: None)


def _service(tmp_path, *, current="0.4.6", body=b"x" * 1000, installer_size=1000,
             docs=None, error=None, spawn=None, backup=None, prefs=None):
    docs = docs if docs is not None else [release_json("v0.5.0",
                                                        installer_size=installer_size)]
    downloader = Downloader(opener=FakeOpener(default=FakeResponse(
        body, headers={"Content-Length": str(len(body))})), chunk_size=128)
    spawn = spawn or (lambda cmd: cmd)
    backup = backup or (lambda paths, label: tmp_path / "backup")
    launcher = InstallerLauncher(spawn=spawn, backup=backup)
    return UpdateService(
        current, prefs or InMemoryPrefs(),
        client=_client(*docs, error=error),
        downloader=downloader, launcher=launcher, download_dir=tmp_path)


def _wait_download(service, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if service.snapshot()["phase"] in ("downloaded", "error", "available"):
            return
        time.sleep(0.01)


class TestCheck:
    def test_check_available(self, tmp_path):
        svc = _service(tmp_path)
        svc.check_now()
        snap = svc.snapshot()
        assert snap["update_available"]
        assert snap["latest_version"] == "0.5.0"
        assert snap["phase"] == "available"
        assert snap["release"]["download_size"] == 1000
        assert snap["last_checked"]              # persisted

    def test_up_to_date(self, tmp_path):
        svc = _service(tmp_path, current="0.5.0")
        svc.check_now()
        assert not svc.snapshot()["update_available"]
        assert svc.snapshot()["phase"] == "up_to_date"

    def test_offline_is_quiet(self, tmp_path):
        svc = _service(tmp_path, error=NetworkError("offline", retryable=False))
        svc.check_now()
        snap = svc.snapshot()
        assert snap["error"] and not snap["update_available"]
        assert snap["phase"] == "error"

    def test_no_installer_asset_not_offered(self, tmp_path):
        svc = _service(tmp_path, docs=[release_json("v0.5.0", installer_size=None)])
        svc.check_now()
        assert not svc.snapshot()["update_available"]

    def test_check_async_completes(self, tmp_path):
        svc = _service(tmp_path)
        svc.check_async()
        end = time.time() + 5
        while time.time() < end and svc.snapshot()["phase"] == "checking":
            time.sleep(0.01)
        assert svc.snapshot()["update_available"]


class TestFrequency:
    def test_maybe_check_on_launch_respects_auto_off(self, tmp_path):
        svc = _service(tmp_path, prefs=InMemoryPrefs(auto_check=False))
        assert svc.maybe_check_on_launch() is False

    def test_maybe_check_on_launch_respects_frequency(self, tmp_path):
        from datetime import datetime, timezone
        prefs = InMemoryPrefs(frequency="daily",
                              last_checked=datetime.now(timezone.utc).isoformat())
        svc = _service(tmp_path, prefs=prefs)
        assert svc.maybe_check_on_launch() is False   # just checked, not due


class TestDownloadAndApply:
    def test_full_happy_path(self, tmp_path):
        commands = []
        svc = _service(tmp_path, spawn=lambda cmd: commands.append(cmd))
        svc.check_now()
        assert svc.start_download()
        _wait_download(svc)
        assert svc.snapshot()["phase"] == "downloaded"
        result = svc.apply_update()
        assert result["ok"], result
        assert commands and "/VERYSILENT" in commands[0]
        assert svc.snapshot()["phase"] == "installing"

    def test_backup_runs_before_launch(self, tmp_path):
        order = []
        svc = _service(tmp_path,
                       backup=lambda p, l: order.append("backup") or (tmp_path / "b"),
                       spawn=lambda cmd: order.append("launch"))
        svc.check_now(); svc.start_download(); _wait_download(svc)
        svc.apply_update()
        assert order == ["backup", "launch"]

    def test_backup_failure_aborts(self, tmp_path):
        def boom(p, l):
            raise OSError("disk full")
        launched = []
        svc = _service(tmp_path, backup=boom,
                       spawn=lambda cmd: launched.append(cmd))
        svc.check_now(); svc.start_download(); _wait_download(svc)
        result = svc.apply_update()
        assert not result["ok"]
        assert launched == []                      # never launched the installer
        assert svc.snapshot()["phase"] == "error"

    def test_validation_failure_aborts(self, tmp_path):
        # Body shorter than the advertised installer size -> size check fails.
        launched = []
        svc = _service(tmp_path, body=b"x" * 500, installer_size=1000,
                       spawn=lambda cmd: launched.append(cmd))
        svc.check_now(); svc.start_download(); _wait_download(svc)
        result = svc.apply_update()
        assert not result["ok"] and "incomplete" in result["error"].lower()
        assert launched == []

    def test_apply_without_download_fails(self, tmp_path):
        svc = _service(tmp_path)
        svc.check_now()
        result = svc.apply_update()
        assert not result["ok"]

    def test_install_hook_invoked(self, tmp_path):
        fired = []
        svc = _service(tmp_path)
        svc.set_install_hook(lambda: fired.append(True))
        svc.check_now(); svc.start_download(); _wait_download(svc)
        svc.apply_update()
        assert fired == [True]


class TestPreferencesAndSkip:
    def test_skip_marks_current_version(self, tmp_path):
        svc = _service(tmp_path)
        svc.check_now()
        svc.skip_current()
        snap = svc.snapshot()
        assert snap["skip_version"] == "0.5.0"
        # The update still technically exists, but is now dismissed — the UI
        # gates its badge/dialog on (update_available and not dismissed).
        assert snap["dismissed"] and snap["update_available"]

    def test_set_preferences_validates(self, tmp_path):
        import pytest
        from optionspilot.update.models import UpdateError
        svc = _service(tmp_path)
        svc.set_preferences(frequency="weekly", channel="beta", auto_check=False)
        p = svc.snapshot()
        assert p["frequency"] == "weekly" and p["channel"] == "beta"
        assert p["auto_check"] is False
        with pytest.raises(UpdateError):
            svc.set_preferences(frequency="hourly")
        with pytest.raises(UpdateError):
            svc.set_preferences(channel="nightly")

    def test_beta_channel_offers_prerelease(self, tmp_path):
        svc = _service(tmp_path, current="0.5.0", docs=[
            release_json("v0.5.0"),
            release_json("v0.6.0-beta.1", prerelease=True)],
            prefs=InMemoryPrefs(channel="beta"))
        svc.check_now()
        assert svc.snapshot()["latest_version"] == "0.6.0-beta.1"


# ── V0.9.0-C8: published checksums, end to end ──────────────────────────────

_SUMS_URL = "https://example.test/download/SHA256SUMS"


def _sums_asset(text: bytes) -> dict:
    return {"name": "SHA256SUMS", "size": len(text),
            "browser_download_url": _SUMS_URL, "content_type": "text/plain"}


def _service_with_manifest(tmp_path, *, body=b"x" * 1000, sums_text=None,
                           publish_manifest=True):
    """A service whose release optionally publishes a SHA256SUMS manifest.

    The downloader's opener routes the manifest URL separately from the
    installer URL, so the fetch of one is exercised independently of the other
    exactly as it is in production.
    """
    from optionspilot.update.validation import sha256_file  # noqa: F401  (doc)

    installer_name = "OptionsPilot-Setup-v0.5.0.exe"
    if sums_text is None:
        import hashlib
        digest = hashlib.sha256(body).hexdigest()
        sums_text = f"{digest}  {installer_name}\n"
    encoded = sums_text.encode()

    extra = [_sums_asset(encoded)] if publish_manifest else []
    docs = [release_json("v0.5.0", installer_size=len(body), extra_assets=extra)]

    downloader = Downloader(
        opener=FakeOpener(
            {"SHA256SUMS": FakeResponse(encoded)},
            default=FakeResponse(body, headers={"Content-Length": str(len(body))}),
        ),
        chunk_size=128,
    )
    launcher = InstallerLauncher(spawn=lambda cmd: cmd,
                                 backup=lambda paths, label: tmp_path / "backup")
    return UpdateService("0.4.6", InMemoryPrefs(), client=_client(*docs),
                         downloader=downloader, launcher=launcher,
                         download_dir=tmp_path)


class TestPublishedChecksums:
    """The integrity half of C-1: verify what the release says it published."""

    def test_manifest_asset_is_discovered_on_the_release(self, tmp_path):
        svc = _service_with_manifest(tmp_path)
        result = svc.check_now()
        assert result.release.has_checksums
        assert result.release.checksums.name == "SHA256SUMS"

    def test_matching_digest_installs_at_hash_verified(self, tmp_path):
        svc = _service_with_manifest(tmp_path)
        svc.check_now()
        svc.start_download()
        _wait_download(svc)
        out = svc.apply_update(restart=False)
        assert out["ok"], out
        assert out["assurance"] == "hash_verified"

    def test_corrupted_download_is_refused(self, tmp_path):
        """The manifest describes one file; a different file arrives.

        This is the whole point of the commit: before it, a download that did
        not match what the release published was indistinguishable from one
        that did, provided it was the right length.
        """
        import hashlib
        body = b"y" * 1000
        wrong = hashlib.sha256(b"the file we EXPECTED").hexdigest()
        svc = _service_with_manifest(
            tmp_path, body=body,
            sums_text=f"{wrong}  OptionsPilot-Setup-v0.5.0.exe\n")
        svc.check_now()
        svc.start_download()
        _wait_download(svc)
        out = svc.apply_update(restart=False)
        assert not out["ok"]
        assert "integrity" in out["error"].lower()
        assert svc.snapshot()["phase"] == "error"

    def test_release_without_manifest_still_installs_at_size_only(self, tmp_path):
        """Backward compatibility, and the reason hashes are not yet mandatory.

        Every release published before V0.9.0 carries no manifest. The client
        performing the check is the OLD one, so failing here would strand
        every existing installation on its current version.
        """
        svc = _service_with_manifest(tmp_path, publish_manifest=False)
        result = svc.check_now()
        assert not result.release.has_checksums
        svc.start_download()
        _wait_download(svc)
        out = svc.apply_update(restart=False)
        assert out["ok"], out
        assert out["assurance"] == "size_only"

    def test_manifest_that_omits_the_installer_is_refused(self, tmp_path):
        svc = _service_with_manifest(
            tmp_path, sums_text=f"{'a' * 64}  SomethingElse.zip\n")
        svc.check_now()
        svc.start_download()
        _wait_download(svc)
        out = svc.apply_update(restart=False)
        assert not out["ok"]
        assert "checksum" in out["error"].lower()

    def test_unfetchable_manifest_does_not_break_the_update(self, tmp_path):
        """A manifest that 404s must not be silently ignored NOR crash.

        `fetch_text` returns None, no digest is resolved, and because the
        release did advertise a manifest the update is refused rather than
        quietly downgraded — advertised-then-missing is a discrepancy.
        """
        body = b"z" * 1000
        docs = [release_json("v0.5.0", installer_size=len(body),
                             extra_assets=[_sums_asset(b"unused")])]
        downloader = Downloader(
            opener=FakeOpener({"SHA256SUMS": NetworkError("gone", retryable=False)},
                              default=FakeResponse(
                                  body, headers={"Content-Length": str(len(body))})),
            chunk_size=128)
        launcher = InstallerLauncher(spawn=lambda cmd: cmd,
                                     backup=lambda paths, label: tmp_path / "backup")
        svc = UpdateService("0.4.6", InMemoryPrefs(), client=_client(*docs),
                            downloader=downloader, launcher=launcher,
                            download_dir=tmp_path)
        svc.check_now()
        svc.start_download()
        _wait_download(svc)
        out = svc.apply_update(restart=False)
        assert not out["ok"]
