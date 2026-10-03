"""Extractor proposals stored by reference (never in the checkpoint).

Each stage writes its proposals to the payload store and puts only the key into the state
(``references["proposals:<stage>"]``). ``ProposalStore.load`` implements the grading
``ProposalSource``: given the manifest reference it returns every stage's proposals in stage
order, so ``distill_grade`` grades the whole run in one batch.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from account_intelligence.platform.ports import PayloadStore

MANIFEST = "claim_proposals"


class ProposalStore:
    """Proposals per stage plus a manifest listing them."""

    def __init__(self, payloads: PayloadStore, key_for: Any) -> None:
        self.payloads = payloads
        self.key_for = key_for  # (kind, name) -> neutral key under the run

    async def put_stage(self, stage: str, proposals: Sequence[Mapping[str, Any]]) -> str:
        """Store one stage's proposals; returns the reference."""
        key = self.key_for("proposals", f"{stage}.json")
        await self.payloads.put(key, json.dumps(list(proposals), sort_keys=True).encode())
        return str(key)

    async def put_manifest(self, references: Mapping[str, Any]) -> str:
        """A manifest of every ``proposals:<stage>`` reference in the state, in key order."""
        refs = [references[k] for k in sorted(references) if str(k).startswith("proposals:")]
        key = self.key_for("proposals", "manifest.json")
        await self.payloads.put(key, json.dumps(refs).encode())
        return str(key)

    async def load(
        self, tenant_id: UUID, run_id: UUID, reference: str
    ) -> Sequence[str | Mapping[str, Any]]:
        """Every proposal named by the manifest (grading ``ProposalSource``)."""
        refs = json.loads(await self.payloads.get(reference))
        out: list[str | Mapping[str, Any]] = []
        for ref in refs:
            out.extend(json.loads(await self.payloads.get(ref)))
        return out
