"""Gmail as a tenant-wide source stays off (decision D-65): a whole client's mailbox is never
connected. Gmail exists only as a personal, read-only Mail and drive source of one user
(``connectors_client.maildrive``, REPLICA_PROMPT screen 8). Any tenant-wide use refuses."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured


def require_enabled() -> None:
    """Refuse: Gmail is never a tenant-wide source (personal Mail and drive only)."""
    raise NotConfigured("Gmail is a personal Mail and drive source only (decision D-65)")
