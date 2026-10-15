"""Transactional email over SMTP (tenancy.smtp_mailer): every template renders with its values,
nothing half-filled or header-injected is sent, STARTTLS on 587 and TLS on 465, and the mailer
exists only when SMTP_HOST is set."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tenancy import smtp_mailer as M


class _Server:
    instances: list[_Server] = []

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.tls = False
        self.login_as: tuple[str, str] | None = None
        self.sent: list[Any] = []
        _Server.instances.append(self)

    def __enter__(self) -> _Server:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.login_as = (user, password)

    def send_message(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    _Server.instances = []
    monkeypatch.setattr(M.smtplib, "SMTP", _Server)
    monkeypatch.setattr(M.smtplib, "SMTP_SSL", _Server)


def _mailer(port: int = 587) -> M.SmtpMailer:
    return M.SmtpMailer(
        "smtp.example.com", port, "sender@example.com", "app-pass", "sender@example.com"
    )


def test_an_invite_is_sent_over_starttls_with_its_link() -> None:
    ref = asyncio.run(
        _mailer().send(
            "client@example.org",
            "client_invite",
            {
                "client_name": "Acme",
                "invite_url": "https://app.example/invite#t",
                "expires": "2026-10-16",
            },
        )
    )
    server = _Server.instances[0]
    assert server.tls and server.login_as == ("sender@example.com", "app-pass")
    message = server.sent[0]
    assert message["To"] == "client@example.org" and "Account Intelligence" in message["From"]
    assert message["Subject"] == "You're invited to Account Intelligence"
    assert "https://app.example/invite#t" in message.get_content()
    assert "Acme" in message.get_content() and ref == message["Message-ID"]


def test_port_465_uses_implicit_tls_without_starttls() -> None:
    asyncio.run(
        _mailer(465).send("a@example.org", "local_password_reset", {"reset_url": "https://x/r"})
    )
    assert _Server.instances[0].port == 465 and not _Server.instances[0].tls


@pytest.mark.parametrize(
    ("to", "template", "params"),
    [
        ("a@example.org", "no_such_template", {}),
        ("a@example.org", "client_invite", {"client_name": "Acme"}),  # missing values
        ("a@example.org\nBcc: x@evil.example", "local_password_reset", {"reset_url": "u"}),
        ("not-an-address", "local_password_reset", {"reset_url": "u"}),
        ("a@example.org, b@example.org", "local_password_reset", {"reset_url": "u"}),
    ],
)
def test_nothing_half_filled_or_injected_is_sent(
    to: str, template: str, params: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(_mailer().send(to, template, params))
    assert not _Server.instances


def test_every_template_renders_and_an_alert_subject_stays_one_line() -> None:
    values = {
        "client_name": "c", "invite_url": "u", "expires": "e", "verification_url": "u",
        "reset_url": "u", "subject": "Hiring\\nBcc: x@evil.example", "body": "b", "event_id": "1",
    }  # fmt: skip
    for name in M.TEMPLATES:
        subject, body = M.render(name, values)
        assert subject and body and "\n" not in subject
    subject, _ = M.render("signal_alert", {**values, "subject": "line one\nBcc: x@evil.example"})
    assert "\n" not in subject


def test_the_mailer_exists_only_when_smtp_host_is_set() -> None:
    assert M.mailer_from_env({}) is None
    mailer = M.mailer_from_env(
        {"SMTP_HOST": "smtp.gmail.com", "SMTP_USERNAME": "me@gmail.com", "SMTP_PASSWORD": "p"}
    )
    assert mailer is not None and mailer.port == 587 and mailer.sender == "me@gmail.com"
    with pytest.raises(ValueError):
        M.mailer_from_env({"SMTP_HOST": "smtp.gmail.com", "MAIL_FROM": "two@a.co, three@b.co"})
