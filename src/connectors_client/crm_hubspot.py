"""HubSpot (D-65): objects and fields come from the scope table and the admin's grants; the read
adapter is not built (``ports.UnconfiguredConnectorClient`` raises NotConfigured). No call here
can write to the tenant's portal: the port has no write method."""

from __future__ import annotations

from connectors_client.scopes import spec

KIND = "hubspot"


def readable_objects() -> tuple[str, ...]:
    """The objects the scope table allows an admin to grant."""
    return tuple(sorted(spec(KIND).objects))
