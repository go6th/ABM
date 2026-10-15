"""Transactional email over SMTP (owner decision 2026-10-15): the ``tenancy.ports.Mailer`` for
staging and production once ``SMTP_HOST`` is set. Any SMTP provider works (Gmail or Google
Workspace with an app password, SendGrid, Brevo): STARTTLS on 587, implicit TLS on 465.

* Every message comes from a fixed template (``TEMPLATES``): a subject and a plain-text body with
  ``{name}`` placeholders filled from ``params``. An unknown template or a missing parameter is a
  ``ValueError`` before anything is sent, so no half-filled mail leaves.
* Recipients are one address each, with no line breaks (header injection is refused).
* The password comes from Secret Manager (``SMTP_PASSWORD``) and is never logged; the network
  call runs in a worker thread with a timeout so a slow server cannot hold the event loop.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

PRODUCT = "Account Intelligence by 6th"
TEMPLATES: dict[str, tuple[str, str]] = {
    "client_invite": (
        "You're invited to Account Intelligence",
        "Hello,\n\n"
        "You have been invited to {client_name} on Account Intelligence by 6th.\n\n"
        "Open this link to choose your password and sign in:\n{invite_url}\n\n"
        "The link works once and expires on {expires}.\n"
        "If you were not expecting this, you can ignore this email.\n",
    ),
    "local_email_verification": (
        "Confirm your email for Account Intelligence",
        "Hello,\n\n"
        "Confirm your email address for Account Intelligence by 6th:\n{verification_url}\n\n"
        "The link expires in 30 minutes. If you did not ask for this, ignore this email.\n",
    ),
    "local_password_reset": (
        "Reset your Account Intelligence password",
        "Hello,\n\n"
        "Someone asked to reset the password for this email on Account Intelligence by 6th.\n"
        "Choose a new password here:\n{reset_url}\n\n"
        "The link expires in 20 minutes. If it was not you, ignore this email; your password "
        "stays the same.\n",
    ),
    "signal_alert": ("{subject}", "{body}\n\nReference: {event_id}\n"),
    # an outbox notification, already rendered from its allow-listed template (spend.notify)
    "notification": ("{subject}", "{body}\n"),
}
_ADDRESS = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")
_PLACEHOLDER = re.compile(r"{([a-z_]+)}")


def render(template: str, params: Mapping[str, str]) -> tuple[str, str]:
    """The subject and body of one template; ``ValueError`` when unknown or a value is missing."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown mail template {template!r}")
    subject, body = TEMPLATES[template]
    needed = set(_PLACEHOLDER.findall(subject + body))
    missing = needed - set(params)
    if missing:
        raise ValueError(f"mail template {template!r} needs {sorted(missing)}")
    values = {k: str(params[k]) for k in needed}
    # a subject is one line whatever a parameter holds
    return " ".join(subject.format(**values).split())[:200], body.format(**values)


@dataclass
class SmtpMailer:
    """See the module docstring."""

    host: str
    port: int
    username: str
    password: str
    sender: str
    sender_name: str = PRODUCT
    timeout_s: float = 20.0

    def __post_init__(self) -> None:
        if not _ADDRESS.match(self.sender):
            raise ValueError("MAIL_FROM is not a single email address")

    async def send(self, to: str, template: str, params: Mapping[str, str]) -> str:
        """Send one message; returns its Message-ID."""
        address = to.strip()
        if not _ADDRESS.match(address):
            raise ValueError("the recipient is not a single email address")
        subject, body = render(template, params)
        message = EmailMessage()
        message["From"] = formataddr((self.sender_name, self.sender))
        message["To"] = address
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[1])
        message.set_content(body)
        await asyncio.to_thread(self._deliver, message)
        return str(message["Message-ID"])

    def _deliver(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.port == 465:
            with smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout_s, context=context
            ) as server:
                self._login_and_send(server, message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as server:
                server.starttls(context=context)
                self._login_and_send(server, message)

    def _login_and_send(self, server: smtplib.SMTP, message: EmailMessage) -> None:
        if self.username:
            server.login(self.username, self.password)
        server.send_message(message)


def mailer_from_env(env: Mapping[str, str]) -> SmtpMailer | None:
    """The SMTP mailer when ``SMTP_HOST`` is set (None keeps the fail-closed stand-in)."""
    host = (env.get("SMTP_HOST") or "").strip()
    if not host:
        return None
    username = (env.get("SMTP_USERNAME") or "").strip()
    return SmtpMailer(
        host=host,
        port=int(env.get("SMTP_PORT") or 587),
        username=username,
        password=env.get("SMTP_PASSWORD") or "",
        sender=(env.get("MAIL_FROM") or username).strip(),
        sender_name=(env.get("MAIL_FROM_NAME") or PRODUCT).strip(),
    )
