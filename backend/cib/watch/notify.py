"""Pluggable alerting for watch-rule hits.

Channels are chosen with CIB_NOTIFY_CHANNELS (comma-separated). Every channel is stdlib-only and
failures are contained: an unreachable webhook must not stop the poller from recording hits.
"""

from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .. import config


@dataclass
class Alert:
    title: str
    body: str
    url: str | None = None
    campaign_slug: str | None = None
    rule_name: str | None = None

    def as_dict(self) -> dict:
        return {
            "title": self.title, "body": self.body, "url": self.url,
            "campaign_slug": self.campaign_slug, "rule_name": self.rule_name,
        }

    def as_text(self) -> str:
        lines = [self.title, ""]
        if self.campaign_slug:
            lines.append(f"Campaign: {self.campaign_slug}")
        if self.rule_name:
            lines.append(f"Rule: {self.rule_name}")
        if self.url:
            lines.append(f"URL: {self.url}")
        lines += ["", self.body]
        return "\n".join(lines)


def _stdout(alert: Alert) -> None:
    print(f"[ALERT] {alert.as_text()}")


def _file(alert: Alert) -> None:
    path = Path(config.get("CIB_NOTIFY_FILE", "data/alerts.log"))
    if not path.is_absolute():
        path = config.REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(alert.as_dict(), ensure_ascii=False) + "\n")


def _webhook(alert: Alert) -> None:
    url = config.get("CIB_NOTIFY_WEBHOOK_URL").strip()
    if not url:
        raise RuntimeError("CIB_NOTIFY_WEBHOOK_URL is not set")
    data = json.dumps({"text": alert.as_text(), **alert.as_dict()}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15):
        pass


def _email(alert: Alert) -> None:
    host = config.get("CIB_SMTP_HOST").strip()
    to = [a.strip() for a in config.get("CIB_NOTIFY_EMAIL_TO").split(",") if a.strip()]
    sender = config.get("CIB_NOTIFY_EMAIL_FROM").strip()
    if not (host and to and sender):
        raise RuntimeError("CIB_SMTP_HOST, CIB_NOTIFY_EMAIL_FROM and CIB_NOTIFY_EMAIL_TO "
                           "must all be set for the email channel")
    message = EmailMessage()
    message["Subject"] = alert.title
    message["From"] = sender
    message["To"] = ", ".join(to)
    message.set_content(alert.as_text())
    port = int(config.get("CIB_SMTP_PORT", "587") or 587)
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        user, password = config.get("CIB_SMTP_USER"), config.get("CIB_SMTP_PASSWORD")
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


CHANNELS = {"stdout": _stdout, "file": _file, "webhook": _webhook, "email": _email}


def send(alert: Alert, channels: str | None = None) -> dict[str, str]:
    """Deliver an alert to every configured channel. Returns per-channel outcome."""
    names = [c.strip() for c in (channels or config.get("CIB_NOTIFY_CHANNELS", "stdout")).split(",")
             if c.strip()]
    outcome: dict[str, str] = {}
    for name in names:
        handler = CHANNELS.get(name)
        if handler is None:
            outcome[name] = f"unknown channel {name!r}"
            continue
        try:
            handler(alert)
            outcome[name] = "sent"
        except (urllib.error.URLError, OSError, RuntimeError, smtplib.SMTPException) as exc:
            outcome[name] = f"failed: {exc}"
    return outcome
