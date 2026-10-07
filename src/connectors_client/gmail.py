"""Gmail (D-65): not in ARCH; off behind ``config/connectors.yaml`` (enabled: false) until the
owner decides. Any use refuses."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured
from connectors_client.scopes import spec


def require_enabled() -> None:
    """Refuse while the source is off (label constraints would be enforced here when on)."""
    if not spec("gmail").enabled:
        raise NotConfigured("the Gmail connector is off (decision D-65)")
