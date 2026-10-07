"""Delivery channels (AL6): in-app, email, Slack and signed webhooks. Each is a port; production
adapters that do not exist raise ``NotConfigured``. Every message passes the client-output scan
before it leaves (AL7)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from account_intelligence.platform.ports import NotConfigured


class SlackPort(Protocol):
    """Posts one message to the user's connected Slack (minimum content: subject and app link)."""

    async def post(self, recipient: str, text: str, *, idempotency_key: str) -> str:
        """Returns the message reference."""


class WebhookPort(Protocol):
    """POSTs a signed body to the tenant's endpoint."""

    async def post(self, body: bytes, headers: Mapping[str, str]) -> int:
        """Returns the HTTP status."""


class UnconfiguredSlack:
    """Until a Slack app is registered."""

    async def post(self, recipient: str, text: str, *, idempotency_key: str) -> str:
        """Refuses."""
        raise NotConfigured("no Slack app is registered")


class UnconfiguredWebhook:
    """Until the tenant registers an endpoint."""

    async def post(self, body: bytes, headers: Mapping[str, str]) -> int:
        """Refuses."""
        raise NotConfigured("no webhook endpoint is registered")
