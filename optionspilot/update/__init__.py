"""OptionsPilot self-update subsystem.

A self-contained, dependency-light auto-updater built around the GitHub
Releases API. The package is deliberately layered so each concern can be
tested in isolation with no network and no real installer execution:

    version.py      semantic-version parsing + correct (non-lexical) ordering
    models.py       immutable value objects passed between the layers
    transport.py    the only place that touches the network (urllib + retries)
    github_api.py   GitHub Releases -> ReleaseInfo (installer asset only)
    checker.py      "is there a newer release?" decision (channel/skip/frequency)
    downloader.py   stream the installer to %TEMP%, progress + cancellation
    validation.py   verify a download before it is ever executed (future signing)
    installer.py    backup -> launch installer silently -> restart
    ui.py           pure presentation helpers (sizes, ETA, dialog payload)
    service.py      the app-facing facade that wires it all together

Nothing here can place a trade or touch the paper account; the updater only
ever reads GitHub, writes to a scratch temp directory, and (on explicit user
confirmation) launches the signed-in-future installer. The user's data root
(%LOCALAPPDATA%\\OptionsPilot) is never touched by this package — only the
installer replaces program files, and by design it leaves the data root alone.
"""

from __future__ import annotations

from optionspilot.update.models import (
    DownloadProgress,
    ReleaseAsset,
    ReleaseInfo,
    UpdateChannel,
    UpdateCheckResult,
    UpdateError,
    UpdatePhase,
)
from optionspilot.update.version import Version

__all__ = [
    "Version",
    "ReleaseAsset",
    "ReleaseInfo",
    "UpdateChannel",
    "UpdateCheckResult",
    "DownloadProgress",
    "UpdatePhase",
    "UpdateError",
]
