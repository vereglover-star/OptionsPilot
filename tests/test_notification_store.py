from datetime import datetime, timezone

from optionspilot.notify.base import NotificationEvent
from optionspilot.notify.store import NotificationStore


def test_notification_store_deduplicates_and_dismisses(tmp_path):
    store = NotificationStore(tmp_path / "notifications.db")
    event = NotificationEvent(
        "goal_achieved", "Goal achieved", "Open dashboard",
        ts=datetime.now(timezone.utc), dedupe_key="goal:1",
        action={"id": "open_dashboard", "label": "Open Dashboard"},
    )
    assert store.append(event)
    assert not store.append(event)
    recent = store.recent()
    assert len(recent) == 1
    assert recent[0].action["id"] == "open_dashboard"
    assert store.dismiss(event.event_id)
    assert store.recent() == []


def test_notification_store_releases_database_file_before_shutdown(tmp_path):
    """Windows must be able to remove the inbox without waiting for GC."""
    path = tmp_path / "notifications.db"
    store = NotificationStore(path)
    assert store.append(NotificationEvent("error", "test", "body"))
    store.close()
    # This would raise WinError 32 when the connection context manager merely
    # committed but left the sqlite handle alive.
    path.unlink()
    assert not path.exists()
