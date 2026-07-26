"""UpdateChecker + frequency throttling. The checker must never raise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from optionspilot.update.checker import UpdateChecker, is_check_due
from optionspilot.update.github_api import GitHubReleases
from optionspilot.update.models import UpdateChannel
from optionspilot.update.transport import NetworkError
from tests.update_helpers import FakeOpener, release_json, releases_response


def _client(*docs, error=None):
    route = error if error is not None else releases_response(*docs)
    return GitHubReleases("owner/repo", api_base="https://api.test",
                          opener=FakeOpener({"/releases": route}),
                          sleep=lambda s: None)


class TestIsCheckDue:
    def test_never_checked_is_due(self):
        assert is_check_due("daily", None)

    def test_launch_always_due(self):
        now = datetime(2026, 7, 25, tzinfo=timezone.utc)
        assert is_check_due("launch", now - timedelta(seconds=1), now)

    def test_daily_not_due_within_day(self):
        now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
        assert not is_check_due("daily", now - timedelta(hours=3), now)
        assert is_check_due("daily", now - timedelta(hours=25), now)

    def test_weekly(self):
        now = datetime(2026, 7, 25, tzinfo=timezone.utc)
        assert not is_check_due("weekly", now - timedelta(days=3), now)
        assert is_check_due("weekly", now - timedelta(days=8), now)

    def test_future_last_checked_is_due(self):
        now = datetime(2026, 7, 25, tzinfo=timezone.utc)
        assert is_check_due("daily", now + timedelta(days=1), now)

    def test_naive_datetime_tolerated(self):
        now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
        naive = datetime(2026, 7, 25, 11)   # no tzinfo
        assert not is_check_due("daily", naive, now)


class TestCheck:
    def test_update_available(self):
        checker = UpdateChecker("0.4.6", client=_client(release_json("v0.5.0")))
        result = checker.check()
        assert result.update_available
        assert str(result.latest) == "0.5.0"
        assert result.release.has_installer
        assert result.error is None

    def test_up_to_date(self):
        checker = UpdateChecker("0.5.0", client=_client(release_json("v0.5.0")))
        result = checker.check()
        assert not result.update_available
        assert result.error is None

    def test_running_newer_than_release(self):
        checker = UpdateChecker("0.6.0", client=_client(release_json("v0.5.0")))
        assert not checker.check().update_available

    def test_offline_never_raises(self):
        checker = UpdateChecker(
            "0.4.6", client=_client(error=NetworkError("offline", retryable=False)))
        result = checker.check()
        assert result.error is not None
        assert not result.update_available
        assert str(result.current) == "0.4.6"

    def test_no_releases(self):
        checker = UpdateChecker("0.4.6", client=_client())
        result = checker.check()
        assert result.latest is None and not result.update_available

    def test_beta_channel(self):
        checker = UpdateChecker("0.5.0", client=_client(
            release_json("v0.5.0"),
            release_json("v0.6.0-beta.1", prerelease=True)))
        assert not checker.check(channel=UpdateChannel.STABLE).update_available
        assert checker.check(channel=UpdateChannel.BETA).update_available
