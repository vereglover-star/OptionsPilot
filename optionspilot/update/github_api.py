"""GitHub Releases API client -> :class:`ReleaseInfo`.

Reads the public Releases API of the configured repository and turns each
published release into a :class:`ReleaseInfo`, keeping only the one asset the
updater is ever allowed to download: the Inno Setup installer named
``OptionsPilot-Setup-vX.Y.Z.exe``. Source ZIPs and every other asset are
ignored by construction (security: the updater never downloads, let alone
executes, an arbitrary asset — only a recognised installer from the configured
repo).

  * Drafts are always skipped (they are not real releases).
  * Prereleases are skipped unless the caller opts into the beta channel.
  * The repo is fixed to the official one by default and only overridable for
    tests; a release from anywhere else is never considered.

All network access goes through an injected ``opener`` (see transport.py), so
the whole client is exercised offline in tests with canned JSON.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from optionspilot.core.logging_setup import get_logger
from optionspilot.update.models import ReleaseAsset, ReleaseInfo
from optionspilot.update.transport import (
    DEFAULT_BACKOFF,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    NetworkError,
    Opener,
    urllib_open,
    with_retries,
)
from optionspilot.update.version import Version
import time as _time

log = get_logger("update")

# The one and only official source. Overridable only so tests can point at a
# fake; production always trusts exactly this repository.
DEFAULT_REPO = "vereglover-star/OptionsPilot"
DEFAULT_API_BASE = "https://api.github.com"

# The single asset pattern the updater will ever download. Anchored to the
# documented installer naming (scripts/build_installer.ps1 / OptionsPilot.iss).
INSTALLER_RE = re.compile(r"OptionsPilot-Setup-v[0-9][0-9A-Za-z.\-]*\.exe$", re.IGNORECASE)


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        # GitHub returns e.g. "2026-07-25T19:42:00Z".
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _select_installer(assets: list[dict]) -> ReleaseAsset | None:
    """Return the installer asset from a release's asset list, or None.

    Rejects everything that is not a recognised setup exe — this is the security
    boundary that keeps the updater from ever fetching a source zip or a
    look-alike asset."""
    for a in assets:
        name = str(a.get("name", ""))
        if not INSTALLER_RE.search(name):
            continue
        url = str(a.get("browser_download_url", ""))
        if not url:
            continue
        return ReleaseAsset(
            name=name,
            size=int(a.get("size", 0) or 0),
            download_url=url,
            content_type=str(a.get("content_type", "")),
        )
    return None


def parse_release(doc: dict) -> ReleaseInfo | None:
    """Convert one GitHub release JSON object into a :class:`ReleaseInfo`.

    Returns ``None`` if the tag is not a parseable semantic version — a release
    the updater cannot reason about is safer ignored than guessed at.
    """
    tag = str(doc.get("tag_name", ""))
    version = Version.try_parse(tag)
    if version is None:
        log.debug("skipping release with unparseable tag %r", tag)
        return None
    return ReleaseInfo(
        version=version,
        tag=tag,
        name=str(doc.get("name") or tag),
        notes=str(doc.get("body") or ""),
        published_at=_parse_dt(doc.get("published_at")),
        prerelease=bool(doc.get("prerelease", False)),
        draft=bool(doc.get("draft", False)),
        html_url=str(doc.get("html_url", "")),
        installer=_select_installer(doc.get("assets") or []),
    )


class GitHubReleases:
    """Thin, injectable client over ``GET /repos/{repo}/releases``."""

    def __init__(self, repo: str = DEFAULT_REPO, *,
                 api_base: str = DEFAULT_API_BASE,
                 opener: Opener = urllib_open,
                 token: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES,
                 backoff: float = DEFAULT_BACKOFF,
                 sleep=_time.sleep):
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        self._opener = opener
        self._token = token
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:                       # unauthenticated works; token only
            h["Authorization"] = f"Bearer {self._token}"  # raises the rate limit
        return h

    def _get_json(self, url: str):
        def attempt():
            with self._opener(url, self._headers(), self._timeout) as resp:
                status = getattr(resp, "status", None)
                if status is None and hasattr(resp, "getcode"):
                    status = resp.getcode()
                if status is not None and status >= 400:
                    raise NetworkError(f"HTTP {status} for {url}", status=status,
                                       retryable=status >= 500)
                return resp.read()
        raw = with_retries(attempt, retries=self._retries, backoff=self._backoff,
                           sleep=self._sleep)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise NetworkError(f"invalid JSON from {url}: {exc}", retryable=False) from exc

    def list_releases(self, per_page: int = 30) -> list[ReleaseInfo]:
        """All published, parseable releases (newest first per GitHub ordering).

        Drafts are dropped here; prerelease filtering is the checker's job so the
        beta channel can be honoured without a second request.
        """
        url = f"{self.api_base}/repos/{self.repo}/releases?per_page={int(per_page)}"
        doc = self._get_json(url)
        if not isinstance(doc, list):
            raise NetworkError(f"expected a JSON array from {url}", retryable=False)
        out: list[ReleaseInfo] = []
        for item in doc:
            if not isinstance(item, dict) or item.get("draft"):
                continue
            rel = parse_release(item)
            if rel is not None:
                out.append(rel)
        return out

    def latest_release(self, *, include_prereleases: bool = False) -> ReleaseInfo | None:
        """The highest-version published release for the channel, or ``None``.

        We sort by parsed :class:`Version` rather than trusting list order or the
        ``/releases/latest`` endpoint (which hides prereleases and can lag), so
        version ordering is always correct and channel-aware.
        """
        releases = self.list_releases()
        candidates = [r for r in releases
                      if include_prereleases or not r.prerelease]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.version)
