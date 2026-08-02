"""Immutable value objects exchanged between the updater layers.

These are plain dataclasses (matching the codebase's domain-model convention in
`core/models.py`): data in, data out, no I/O. They form the vocabulary the
GitHub client, the checker, the downloader, the service, and the UI all speak,
so each layer can be tested against fixtures instead of live GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass
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


class SignatureVerdict(str, Enum):
    """What Authenticode had to say about a file (V0.9.0-C9).

    FOUR states, not three, and the fourth is the whole reason this enum exists
    instead of the ``bool | None`` the C9 plan specified. That shape can express
    "trusted", "not trusted" and "could not check" — but Phase 1 policy needs
    to separate **"there is no signature"** from **"there is one and it is
    bad"**, because every release published before V0.9.0 is unsigned. Refusing
    the first would strand every existing installation on its current version,
    permanently: the client doing the checking is the OLD one, so the fix would
    ship in an update they could no longer install.

    It is the same distinction C8 already drew for checksums — *no manifest
    published* (install, at reduced assurance) versus *a manifest that does not
    cover this file* (refuse) — and it needs saying once per mechanism.
    """

    #: Intact signature chaining to a root this machine trusts.
    TRUSTED = "trusted"
    #: No signature at all. Normal for every pre-V0.9.0 release; tolerated in
    #: Phase 1 and refused in Phase 2 (see service.REQUIRE_SIGNATURE).
    UNSIGNED = "unsigned"
    #: A signature exists and is not acceptable — tampered, expired, untrusted
    #: root, explicitly distrusted. Refused in BOTH phases: this is the case
    #: the whole mechanism was built for.
    INVALID = "invalid"
    #: The question could not be asked here — not Windows, or the OS refused.
    #: Never a synonym for a negative verdict.
    UNKNOWN = "unknown"

    @property
    def refuses_install(self) -> bool:
        """Phase 1 policy: only a *bad* signature blocks an install."""
        return self is SignatureVerdict.INVALID

    @property
    def is_trusted(self) -> bool:
        return self is SignatureVerdict.TRUSTED


class Assurance(str, Enum):
    """How strongly a downloaded installer was verified before it is run.

    This exists because "validated" was a single boolean, and a boolean cannot
    distinguish *we confirmed this is the exact file the release publishes*
    from *it is the right size and has a plausible name*. Both used to render
    as "Update verified." — a claim the second one had not earned.

    The level is carried out of :func:`validation.validate` and surfaced to the
    user, so the update dialog can say what was actually checked rather than
    implying a guarantee that was never made.

    Ordered strongest first. The two questions are genuinely different: a
    checksum proves the file matches what the release *published*, a signature
    proves *who* published it — and an attacker able to serve both the
    installer and the manifest satisfies the first completely. That is why
    :attr:`SIGNATURE_VERIFIED` outranks :attr:`HASH_VERIFIED` rather than
    sitting beside it.
    """

    #: Authenticode: the file carries an intact signature chaining to a root
    #: this machine trusts. Strictly stronger than a checksum (V0.9.0-C9).
    SIGNATURE_VERIFIED = "signature_verified"
    #: SHA-256 matched the digest published alongside the release.
    HASH_VERIFIED = "hash_verified"
    #: Name and size matched, but the release published no checksums to compare
    #: against. Correct for every release made before V0.9.0 and the reason
    #: hash verification is not yet mandatory (see docs/AUTO_UPDATER.md).
    SIZE_ONLY = "size_only"
    #: A check failed; the install must not proceed.
    FAILED = "failed"

    @property
    def is_verified(self) -> bool:
        """True only when verification actually happened. Deliberately narrow —
        callers that want to say "verified" must be able to mean it."""
        return self in (Assurance.SIGNATURE_VERIFIED, Assurance.HASH_VERIFIED)

    @property
    def summary(self) -> str:
        return {
            Assurance.SIGNATURE_VERIFIED: (
                "Verified — digitally signed by a trusted publisher."),
            Assurance.HASH_VERIFIED: "Verified against the published checksum.",
            Assurance.SIZE_ONLY: (
                "Integrity data unavailable — this release publishes no "
                "checksum, so only the file name and size were checked."),
            Assurance.FAILED: "This update could not be verified.",
        }[self]


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
    #: The SHA256SUMS manifest published beside the installer, when there is
    #: one. Defaults to None so every release made before V0.9.0 — none of
    #: which carry it — still parses.
    checksums: ReleaseAsset | None = None

    @property
    def has_installer(self) -> bool:
        return self.installer is not None

    @property
    def has_checksums(self) -> bool:
        return self.checksums is not None


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
