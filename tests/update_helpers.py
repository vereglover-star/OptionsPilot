"""Shared fakes for the updater tests — no sockets, no real installer.

A tiny in-memory transport ("opener") satisfies the same structural contract as
``http.client.HTTPResponse`` (context manager + ``read`` + ``status`` +
``getheader``), so the GitHub client and the downloader run fully offline and
deterministically.
"""

from __future__ import annotations

import json

from optionspilot.update.transport import NetworkError


class FakeResponse:
    """A canned HTTP response backed by an in-memory byte buffer."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._buf = body
        self._pos = 0
        self.status = status
        self._headers = {k.lower(): str(v) for k, v in (headers or {}).items()}

    def read(self, amt: int | None = None) -> bytes:
        if amt is None or amt < 0:
            data = self._buf[self._pos:]
            self._pos = len(self._buf)
            return data
        data = self._buf[self._pos:self._pos + amt]
        self._pos += len(data)
        return data

    def getheader(self, name: str, default=None):
        return self._headers.get(name.lower(), default)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        self._pos = 0            # rewind so one canned response can serve repeated GETs
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeOpener:
    """Callable opener that maps a URL to a response, an exception, or a queue.

    ``routes`` maps a URL substring -> a ``FakeResponse``, an ``Exception`` to
    raise, or a *list* consumed one call at a time (for retry/backoff tests).
    Records every requested URL in ``calls``.
    """

    def __init__(self, routes: dict | None = None, default=None):
        self.routes = routes or {}
        self.default = default
        self.calls: list[str] = []

    def __call__(self, url: str, headers: dict | None = None,
                 timeout: float = 10.0):
        self.calls.append(url)
        match = None
        for key, val in self.routes.items():
            if key in url:
                match = val
                break
        if match is None:
            match = self.default
        if isinstance(match, list):
            match = match.pop(0)
        if isinstance(match, BaseException):
            raise match
        if isinstance(match, type) and issubclass(match, BaseException):
            raise match("boom")
        if match is None:
            raise NetworkError(f"no route for {url}", retryable=False)
        return match


def release_json(tag: str, *, name: str | None = None, body: str = "notes",
                 prerelease: bool = False, draft: bool = False,
                 installer_size: int | None = 1000,
                 published_at: str = "2026-07-25T19:42:00Z",
                 extra_assets: list | None = None) -> dict:
    """Build one GitHub release JSON object, with an installer asset by default."""
    version = tag.lstrip("v")
    assets = list(extra_assets or [])
    if installer_size is not None:
        assets.append({
            "name": f"OptionsPilot-Setup-v{version}.exe",
            "size": installer_size,
            "browser_download_url":
                f"https://example.test/download/OptionsPilot-Setup-v{version}.exe",
            "content_type": "application/octet-stream",
        })
    return {
        "tag_name": tag,
        "name": name or f"OptionsPilot {tag}",
        "body": body,
        "prerelease": prerelease,
        "draft": draft,
        "published_at": published_at,
        "html_url": f"https://example.test/releases/{tag}",
        "assets": assets,
    }


def releases_response(*docs: dict) -> FakeResponse:
    return FakeResponse(json.dumps(list(docs)).encode("utf-8"))
