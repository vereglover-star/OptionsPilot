"""SMTP email notifier.

Credentials are never stored in config: the SMTP password (if the server
needs one) comes from the OPTIONSPILOT_SMTP_PASSWORD environment variable,
and the sender account from OPTIONSPILOT_SMTP_USER (defaults to email_to).
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from optionspilot.config.settings import NotifyConfig
from optionspilot.notify.base import NotificationEvent, Notifier

PASSWORD_ENV = "OPTIONSPILOT_SMTP_PASSWORD"
USER_ENV = "OPTIONSPILOT_SMTP_USER"


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, cfg: NotifyConfig):
        self._cfg = cfg

    def send(self, event: NotificationEvent) -> None:
        cfg = self._cfg
        user = os.environ.get(USER_ENV, cfg.email_to)
        msg = EmailMessage()
        msg["Subject"] = f"[OptionsPilot] {event.title}"
        msg["From"] = user
        msg["To"] = cfg.email_to
        msg.set_content(f"{event.body}\n\n-- OptionsPilot {event.kind} @ "
                        f"{event.ts:%Y-%m-%d %H:%M:%S %Z}")
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            password = os.environ.get(PASSWORD_ENV)
            if password:
                smtp.login(user, password)
            smtp.send_message(msg)
