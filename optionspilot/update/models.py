"""Immutable value objects exchanged between the updater layers.

These are plain dataclasses (matching the codebase's domain-model convention in
`core/models.py`): data in, data out, no I/O. They form the vocabulary the
GitHub client, the checker, the downloader, the service, and the UI all speak,
so each layer can be tested against fixtures instead of live GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from optionspilot.update.version import Version


class UpdateChannel(str, Enum):
    """Which releases the user is willing to receive.

    ``STABLE`` ignores GitHub prereleases and drafts (the default). ``BETA``
    additionally accepts prereleases so early adopters can opt in from Settings.
    Drafts are never offered on any channel — they are not published releases.
    """

    STABLE = "stable"
    BETA = "beta"

    @property
    def accepts_prereleases(self) -> bool:
        return self is UpdateChannel.BETA


class UpdatePhase(str, Enum):
    """The state the update state-machine (service.py) is in, surfaced to the UI
    so the dialog can render the right controls."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VERIFYING = "verifying"
    READY_TO_INSTALL = "ready_to_install"
    INSTALLING = "installing"
    ERROR = "error"


class UpdateError(Exception):
    """A user-presentable updater failure.

    Carries a short, non-scary ``message`` suitable for a dialog and an optional
    ``detail`` for the log. The whole updater is built so that a raw stack trace
    or a Python exception never reaches the user — failures are converted into
    one of these with a clear, actionable message.
    """

    def __init__(self, message: str, *, detail: str | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.retryable = retryable


@dataclass(frozen=True)
class ReleaseAsset:
    """A single downloadable file attached to a GitHub release."""

    name: str
    size: int                      # bytes, as reported by the GitHub API
    download_url: str              # browser_download_url
    content_type: str = ""

    @property
    def is_installer(self) -> bool:
        from optionspilot.update.github_api import INSTALLER_RE
        return bool(INSTALLER_RE.search(self.name))


@dataclass(frozen=True)
class ReleaseInfo:
    """A published GitHub release, reduced to what the updater needs."""

    version: Version
    tag: str
    name: str
    notes: str                     # markdown body of the release
    published_at: datetime | None
    prerelease: bool
    draft: bool
    html_url: str
    installer: ReleaseAsset | None  # the OptionsPilot-Setup-v*.exe asset, if any

    @property
    def has_installer(self) -> bool:
        return self.installer is not None


@dataclass(frozen=True)
class UpdateCheckResult:
    """The outcome of a single "is there an update?" check.

    Never raises out of the checker: on failure ``error`` is set and
    ``update_available`` is ``False`` so the app keeps running normally.
    """

    current: Version
    checked_at: datetime
    latest: Version | None = None
    release: ReleaseInfo | None = None
    update_available: bool = False
    error: str | None = None

    @classmethod
    def failure(cls, current: Version, message: str) -> "UpdateCheckResult":
        return cls(current=current, checked_at=datetime.now(timezone.utc),
                   error=message)


@dataclass(frozen=True)
class DownloadProgress:
    """A snapshot of an in-flight (or finished) download, polled by the UI."""

    downloaded: int = 0
    total: int = 0                 # 0 if the server did not send Content-Length
    speed_bps: float = 0.0         # bytes/sec, smoothed
    eta_seconds: float | None = None
    done: bool = False
    cancelled: bool = False
    error: str | None = None
    path: str | None = None        # populated once the file is fully written

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, self.downloaded / self.total * 100.0)


@dataclass
class DownloadResult:
    """The terminal result of :meth:`Downloader.download`."""

    path: str | None
    size: int
    cancelled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled and self.path is not None
