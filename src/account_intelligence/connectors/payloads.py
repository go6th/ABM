"""Raw provider payload keys; the stores themselves are platform adapters (audit F-6).

Key: ``tenants/<tenant>/accounts/<account>/runs/<run>/raw/<sha256 of body>.json.gz``.
No provider code or vendor name appears in a key or file name; provider identity lives only in
the internal ledger. Stores: ``platform.hermetic.MemoryPayloadStore`` and
``LocalPayloadStore`` (tests, dev) and ``platform.gcp.adapters.GcsPayloadStore`` (staging and
production). Each put is one atomic write, so a cancelled task never leaves half a payload.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from account_intelligence.platform.hermetic import LocalPayloadStore
from account_intelligence.platform.hermetic import MemoryPayloadStore as InMemoryPayloadStore
from account_intelligence.platform.ports import PayloadStore


def payload_key(tenant_id: UUID, account_id: UUID, run_id: UUID, body: bytes) -> str:
    """Neutral, content-addressed key for one raw payload."""
    digest = hashlib.sha256(body).hexdigest()
    return f"tenants/{tenant_id}/accounts/{account_id}/runs/{run_id}/raw/{digest}.json.gz"


__all__ = ["InMemoryPayloadStore", "LocalPayloadStore", "PayloadStore", "payload_key"]
