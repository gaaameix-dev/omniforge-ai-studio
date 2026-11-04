"""Outbound mail for password reset and security notices.

Backends:
  console - prints and keeps messages in memory (default; no network needed)
  smtp    - real delivery via smtplib in a worker thread
  off     - disabled; callers get False and say so instead of pretending
Nothing here sends credentials: a reset mail carries a one-time link only.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger("omniforge.mailer")


@dataclass
class Mail:
    to: str
    subject: str
    body: str


class Mailer:
    backend = "none"
    available = False

    async def send(self, to: str, subject: str, body: str) -> bool:
        raise NotImplementedError

    def status(self) -> dict:
        return {"backend": self.backend, "available": self.available}


class ConsoleMailer(Mailer):
    backend = "console"
    available = True

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, to: str, subject: str, body: str) -> bool:
        self.outbox.append(Mail(to=to, subject=subject, body=body))
        if len(self.outbox) > 200:
            self.outbox = self.outbox[-200:]
        log.info("mail (console backend) to=%s subject=%s", to, subject)
        print(f"[mail] to={to} subject={subject}\n{body}\n")
        return True


class SMTPMailer(Mailer):
    backend = "smtp"
    available = True

    def __init__(self, host: str, port: int, username: str, password: str, sender: str,
                 use_tls: bool = True) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.use_tls = use_tls

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as client:
            if self.use_tls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)

    async def send(self, to: str, subject: str, body: str) -> bool:
        try:
            await asyncio.to_thread(self._send_sync, to, subject, body)
            return True
        except Exception as exc:  # noqa: BLE001 - delivery failures are reported, not raised
            log.warning("smtp delivery failed: %s", exc)
            return False


class NullMailer(Mailer):
    backend = "off"
    available = False

    async def send(self, to: str, subject: str, body: str) -> bool:
        log.info("mail disabled; would have sent %r to %s", subject, to)
        return False


def build_mailer(settings) -> Mailer:
    backend = (getattr(settings, "mail_backend", "console") or "console").lower()
    if backend == "smtp":
        if not settings.smtp_host:
            log.warning("mail_backend=smtp but SMTP_HOST is empty; falling back to console")
            return ConsoleMailer()
        return SMTPMailer(
            host=settings.smtp_host, port=settings.smtp_port, username=settings.smtp_user,
            password=settings.smtp_password, sender=settings.smtp_from or settings.smtp_user,
            use_tls=settings.smtp_tls,
        )
    if backend == "off":
        return NullMailer()
    return ConsoleMailer()
