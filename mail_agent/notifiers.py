"""Where the digest goes: console, files, Outlook mail to yourself, a chat webhook."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, subject: str, text: str, html_body: str, now: datetime) -> str: ...


class ConsoleNotifier:
    name = "console"

    def send(self, subject, text, html_body, now):
        print("\n" + text + "\n", flush=True)
        return "printed"


class FileNotifier:
    name = "file"

    def __init__(self, settings: Settings):
        self.dir = settings.output_dir / "digests"
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, subject, text, html_body, now):
        stem = self.dir / f"digest-{now:%Y-%m-%d}"
        stem.with_suffix(".txt").write_text(text, encoding="utf-8")
        stem.with_suffix(".html").write_text(
            f"<!doctype html><meta charset='utf-8'><title>{subject}</title>{html_body}", encoding="utf-8"
        )
        return str(stem.with_suffix(".html"))


class OutlookMailNotifier:
    """Sends the digest as an Outlook mail (to yourself by default) via Graph sendMail."""

    name = "outlook_mail"

    def __init__(self, graph_client, settings: Settings):
        self.client = graph_client
        self.recipient = settings.digest_recipient

    def send(self, subject, text, html_body, now):
        to = self.recipient or self.client.me().get("mail") or self.client.me().get("userPrincipalName")
        self.client.post("/me/sendMail", {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": to}}],
                "importance": "high",
            },
            "saveToSentItems": False,
        })
        return f"mailed to {to}"


class WebhookNotifier:
    """Posts plain text to a Slack / Discord / Teams-workflow style incoming webhook."""

    name = "webhook"

    def __init__(self, settings: Settings):
        if not settings.webhook_url:
            raise RuntimeError("NOTIFY_WEBHOOK_URL이 비어 있습니다.")
        self.url = settings.webhook_url

    def send(self, subject, text, html_body, now):
        payload = {"text": text, "content": text[:1900]}  # Slack uses text, Discord uses content
        r = httpx.post(self.url, json=payload, timeout=30)
        r.raise_for_status()
        return f"webhook {r.status_code}"


def make_notifiers(settings: Settings, graph_client=None) -> list[Notifier]:
    out: list[Notifier] = []
    for name in settings.notifiers:
        if name == "console":
            out.append(ConsoleNotifier())
        elif name == "file":
            out.append(FileNotifier(settings))
        elif name == "outlook_mail":
            if graph_client is None:
                log.warning("outlook_mail notifier skipped: no Microsoft sign-in")
                continue
            out.append(OutlookMailNotifier(graph_client, settings))
        elif name == "webhook":
            out.append(WebhookNotifier(settings))
        else:
            log.warning("unknown notifier %s", name)
    return out
