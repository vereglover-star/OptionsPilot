"""Streaming downloader: progress, cancellation, size, atomic finalize."""

from __future__ import annotations

import threading

from optionspilot.update.downloader import Downloader
from optionspilot.update.models import ReleaseAsset
from tests.update_helpers import FakeOpener, FakeResponse


def _asset(size=2048, name="OptionsPilot-Setup-v0.5.0.exe"):
    return ReleaseAsset(name=name, size=size,
                        download_url="https://example.test/download/" + name)


def _opener(body: bytes, content_length: bool = True):
    headers = {"Content-Length": str(len(body))} if content_length else {}
    return FakeOpener(default=FakeResponse(body, headers=headers))


class TestDownload:
    def test_writes_file_atomically(self, tmp_path):
        body = b"x" * 2048
        result = Downloader(opener=_opener(body), chunk_size=256).download(
            _asset(2048), dest_dir=tmp_path)
        assert result.ok
        out = tmp_path / "OptionsPilot-Setup-v0.5.0.exe"
        assert out.read_bytes() == body
        assert not (tmp_path / (out.name + ".part")).exists()

    def test_progress_reported(self, tmp_path):
        body = b"y" * 1000
        seen = []
        Downloader(opener=_opener(body), chunk_size=100).download(
            _asset(1000), dest_dir=tmp_path, progress_cb=seen.append)
        assert seen, "expected progress callbacks"
        final = seen[-1]
        assert final.done and final.downloaded == 1000
        assert final.path and final.path.endswith(".exe")
        # percent monotonic-ish and bounded
        assert all(0 <= p.percent <= 100 for p in seen)

    def test_cancellation_leaves_no_file(self, tmp_path):
        body = b"z" * 10000
        cancel = threading.Event()

        def cb(p):
            cancel.set()          # cancel as soon as the first chunk lands

        result = Downloader(opener=_opener(body), chunk_size=100).download(
            _asset(10000), dest_dir=tmp_path, progress_cb=cb, cancel=cancel)
        assert result.cancelled and not result.ok
        assert not (tmp_path / "OptionsPilot-Setup-v0.5.0.exe").exists()
        assert not (tmp_path / "OptionsPilot-Setup-v0.5.0.exe.part").exists()

    def test_precancelled_never_starts(self, tmp_path):
        cancel = threading.Event()
        cancel.set()
        opener = _opener(b"data")
        result = Downloader(opener=opener).download(
            _asset(4), dest_dir=tmp_path, cancel=cancel)
        assert result.cancelled
        assert opener.calls == []           # never even opened the connection

    def test_works_without_content_length(self, tmp_path):
        body = b"w" * 500
        result = Downloader(opener=_opener(body, content_length=False),
                            chunk_size=128).download(_asset(500), dest_dir=tmp_path)
        assert result.ok and result.size == 500

    def test_network_error_surfaced_cleanly(self, tmp_path):
        from optionspilot.update.transport import NetworkError
        opener = FakeOpener(default=NetworkError("connection reset"))
        result = Downloader(opener=opener).download(_asset(), dest_dir=tmp_path)
        assert not result.ok and result.error
        assert "connection reset" in result.error

    def test_creates_dest_dir(self, tmp_path):
        nested = tmp_path / "a" / "b"
        result = Downloader(opener=_opener(b"q" * 10)).download(
            _asset(10), dest_dir=nested)
        assert result.ok and (nested / "OptionsPilot-Setup-v0.5.0.exe").exists()
