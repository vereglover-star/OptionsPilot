"""FastAPI /api/update/* endpoints, driven offline via a fake-wired service.

The app is built normally (run_loop=False, so no launch-time network check),
then its UpdateService is replaced with one wired to in-memory fakes but the
*real* RuntimeSettings store, so preference persistence is exercised end to end.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from optionspilot.config.settings import AppConfig
from optionspilot.update.downloader import Downloader
from optionspilot.update.github_api import GitHubReleases
from optionspilot.update.installer import InstallerLauncher
from optionspilot.update.service import UpdateService
from optionspilot.update.transport import NetworkError
from tests.update_helpers import FakeOpener, FakeResponse, release_json, releases_response


def _make_app(tmp_path, *, current="0.4.6", docs=None, error=None,
              body=b"x" * 1000, installer_size=1000, spawn=None):
    from optionspilot.ui.server import create_app
    app = create_app(AppConfig(), run_loop=False, data_dir=tmp_path)
    server = app.state.server

    docs = docs if docs is not None else [
        release_json("v0.5.0", installer_size=installer_size)]
    route = error if error is not None else releases_response(*docs)
    client = GitHubReleases("owner/repo", api_base="https://api.test",
                            opener=FakeOpener({"/releases": route}),
                            sleep=lambda s: None)
    downloader = Downloader(opener=FakeOpener(default=FakeResponse(
        body, headers={"Content-Length": str(len(body))})), chunk_size=128)
    launcher = InstallerLauncher(spawn=spawn or (lambda cmd: cmd),
                                 backup=lambda p, l: tmp_path / "backup")
    # Reuse the real RuntimeSettings store so prefs persistence is real.
    server.updater = UpdateService(current, server.runtime, client=client,
                                   downloader=downloader, launcher=launcher,
                                   download_dir=tmp_path)
    return TestClient(app), server


def _wait(client, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        phase = client.get("/api/update/status").json()["phase"]
        if phase in ("downloaded", "error", "available", "up_to_date"):
            return phase
        time.sleep(0.01)
    return client.get("/api/update/status").json()["phase"]


class TestStatusAndCheck:
    def test_status_no_network(self, tmp_path):
        client, _ = _make_app(tmp_path)
        s = client.get("/api/update/status").json()
        assert s["current_version"] == "0.4.6"
        assert s["phase"] == "idle"

    def test_manual_check_finds_update(self, tmp_path):
        client, _ = _make_app(tmp_path)
        s = client.post("/api/update/check").json()
        assert s["update_available"]
        assert s["release"]["version"] == "0.5.0"
        assert s["release"]["notes_html"]

    def test_check_offline_returns_error_snapshot(self, tmp_path):
        client, _ = _make_app(tmp_path,
                              error=NetworkError("offline", retryable=False))
        s = client.post("/api/update/check").json()
        assert s["error"] and not s["update_available"]


class TestSettings:
    def test_update_settings_persist(self, tmp_path):
        client, server = _make_app(tmp_path)
        r = client.post("/api/update/settings",
                        json={"frequency": "weekly", "channel": "beta"}).json()
        assert r["frequency"] == "weekly" and r["channel"] == "beta"
        # persisted through the real RuntimeSettings store
        assert server.runtime.update_prefs()["frequency"] == "weekly"

    def test_invalid_setting_rejected(self, tmp_path):
        client, _ = _make_app(tmp_path)
        resp = client.post("/api/update/settings", json={"frequency": "hourly"})
        assert resp.status_code == 422

    def test_skip_dismisses_version(self, tmp_path):
        client, _ = _make_app(tmp_path)
        client.post("/api/update/check")
        client.post("/api/update/skip")
        s = client.get("/api/update/status").json()
        assert s["skip_version"] == "0.5.0"
        assert s["dismissed"] and s["update_available"]


class TestDownloadApply:
    def test_download_without_update_conflicts(self, tmp_path):
        client, _ = _make_app(tmp_path, current="0.5.0")   # already latest
        client.post("/api/update/check")
        assert client.post("/api/update/download").status_code == 409

    def test_full_download_and_apply(self, tmp_path):
        commands = []
        client, _ = _make_app(tmp_path, spawn=lambda cmd: commands.append(cmd))
        client.post("/api/update/check")
        assert client.post("/api/update/download").status_code == 200
        assert _wait(client) == "downloaded"
        prog = client.get("/api/update/progress").json()
        assert prog["percent"] == 100.0 or prog["done"]
        r = client.post("/api/update/apply")
        assert r.status_code == 200 and r.json()["ok"]
        assert commands and "/VERYSILENT" in commands[0]

    def test_apply_without_download_422(self, tmp_path):
        client, _ = _make_app(tmp_path)
        client.post("/api/update/check")
        assert client.post("/api/update/apply").status_code == 422

    def test_cancel_endpoint(self, tmp_path):
        client, _ = _make_app(tmp_path)
        r = client.post("/api/update/cancel").json()
        assert r["cancelled"] is True
