"""Call-note sources (D-65; D-08 names Grain first): no source is chosen; off behind
``config/connectors.yaml`` (enabled: false). Any use refuses."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured
from connectors_client.scopes import spec


def require_enabled() -> None:
    """Refuse while no call-note source is chosen and enabled."""
    if not spec("call_notes").enabled:
        raise NotConfigured("no call-note source is enabled (decision D-65)")
