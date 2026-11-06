"""The watch config on the account row: the one writer of ``accounts.watch_config_json`` from the
document side. A workspace the user confirmed as the same company binds its domains, aliases and
careers hosts here, so later uploads and reads treat them as the account's own."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text


async def set_account_watch_config(
    s: Any, account_id: UUID, watch_config: Mapping[str, Any]
) -> bool:
    """Replace the account's watch config. True when the account was found."""
    res = await s.execute(
        text(
            "UPDATE accounts SET watch_config_json = CAST(:w AS jsonb), updated_at = now()"
            " WHERE tenant_id = (SELECT app_tenant()) AND id = :a AND deleted_at IS NULL"
        ),
        {"a": account_id, "w": json.dumps(dict(watch_config))},
    )
    return bool(res.rowcount)
