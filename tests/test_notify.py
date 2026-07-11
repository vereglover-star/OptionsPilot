from email.message import EmailMessage

import pytest

from optionspilot.config.settings import NotifyConfig
from optionspilot.notify import (
    EmailNotifier, NotificationCenter, NotificationEvent, Notifier,
)


class CollectingNotifier(Notifier):
    name = "collector"

    def __init__(self):
        self.events: list[NotificationEvent] = []

    def send(self, event):
        self.events.append(event)


class ExplodingNotifier(Notifier):
    name = "boom"

    def send(self, event):
        raise RuntimeError("smtp down")


class TestNotificationCenter:
    def test_fan_out(self):
        a, b = CollectingNotifier(), CollectingNotifier()
        center = NotificationCenter(NotifyConfig(), [a, b])
        center.notify("trade_opened", "t", "b")
        assert len(a.events) == len(b.events) == 1
        assert a.events[0].kind == "trade_opened"

    def test_failures_never_propagate(self):
        ok = CollectingNotifier()
        center = NotificationCenter(NotifyConfig(), [ExplodingNotifier(), ok])
        center.notify("trade_closed", "t")     # must not raise
        assert len(ok.events) == 1             # others still delivered

    def test_summary_gating(self):
        sink = CollectingNotifier()
        cfg = NotifyConfig(daily_summary=False, weekly_summary=True)
        center = NotificationCenter(cfg, [sink])
        center.notify("daily_summary", "d")
        center.notify("weekly_summary", "w")
        assert [e.kind for e in sink.events] == ["weekly_summary"]

    def test_history_capped(self):
        center = NotificationCenter(NotifyConfig(), [])
        for i in range(250):
            center.notify("large_move", f"m{i}")
        assert len(center.history) == 200
        assert center.history[-1].title == "m249"


class TestEmailNotifier:
    def test_sends_via_smtp(self, monkeypatch):
        sent: list[EmailMessage] = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                assert (host, port) == ("smtp.test", 587)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, user, password):
                raise AssertionError("no password in env -> no login attempt")

            def send_message(self, msg):
                sent.append(msg)

        monkeypatch.setattr("optionspilot.notify.email.smtplib.SMTP", FakeSMTP)
        monkeypatch.delenv("OPTIONSPILOT_SMTP_PASSWORD", raising=False)
        cfg = NotifyConfig(email=True, email_to="me@test.com", smtp_host="smtp.test")
        EmailNotifier(cfg).send(NotificationEvent("trade_opened", "Opened SPY", "body"))
        assert len(sent) == 1
        assert sent[0]["Subject"] == "[OptionsPilot] Opened SPY"
        assert sent[0]["To"] == "me@test.com"
