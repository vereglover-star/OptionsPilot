"""The "is there a newer release?" decision.

Given the running version and the user's update preferences, ask GitHub for the
latest release on the selected channel and decide whether to offer it. This
layer contains *no* network code of its own — it drives a
:class:`GitHubReleases` client — and, critically, **never raises**: any failure
(offline, rate-limited, malformed) becomes an :class:`UpdateCheckResult` with
``error`` set and ``update_available=False``, so a failed check can never delay
startup or interrupt the user.

It also owns two pieces of policy:

  * **channel** — stable vs. beta (whether prereleases are eligible);
  * **skip / frequency** helpers — pure functions the service uses to decide
    whether a check is due and whether a found version was dismissed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from optionspilot.core.logging_setup import get_logger
from optionspilot.update.github_api import DEFAULT_REPO, GitHubReleases
from optionspilot.update.models import UpdateChannel, UpdateCheckResult
from optionspilot.update.version import Version

log = get_logger("update")

# Frequency -> minimum age of the last check before another is due.
_FREQUENCY_INTERVALS = {
    "launch": timedelta(0),           # every launch
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}
VALID_FREQUENCIES = tuple(_FREQUENCY_INTERVALS)


def is_check_due(frequency: str, last_checked: datetime | None,
                 now: datetime | None = None) -> bool:
    """Whether an automatic check should run, given the configured frequency.

    Unknown frequencies fall back to "launch" (fail safe toward checking). A
    missing/future ``last_checked`` always counts as due.
    """
    now = now or datetime.now(timezone.utc)
    interval = _FREQUENCY_INTERVALS.get(frequency, timedelta(0))
    if last_checked is None:
        return True
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)
    if last_checked > now:                # clock skew / edited settings — re-check
        return True
    return (now - last_checked) >= interval


class UpdateChecker:
    """Decides whether a newer release is available for the user's channel."""

    def __init__(self, current: Version | str, *,
                 repo: str = DEFAULT_REPO,
                 client: GitHubReleases | None = None):
        self.current = current if isinstance(current, Version) else Version.parse(current)
        self._client = client or GitHubReleases(repo)

    def check(self, *, channel: UpdateChannel = UpdateChannel.STABLE
              ) -> UpdateCheckResult:
        """Perform one check. Always returns a result; never raises."""
        now = datetime.now(timezone.utc)
        try:
            release = self._client.latest_release(
                include_prereleases=channel.accepts_prereleases)
        except Exception as exc:  # noqa: BLE001 - the checker is the safety net
            # Offline is the common, benign case: log at debug, report quietly.
            log.debug("update check failed: %s", exc)
            return UpdateCheckResult.failure(self.current, str(exc))

        if release is None:
            return UpdateCheckResult(current=self.current, checked_at=now,
                                     latest=None, update_available=False)

        available = release.version > self.current
        return UpdateCheckResult(
            current=self.current,
            checked_at=now,
            latest=release.version,
            release=release,
            update_available=available,
        )
