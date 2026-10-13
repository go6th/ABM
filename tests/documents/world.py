"""Shared helpers for the document tests (SYNTHETIC data, local storage, deterministic models)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from account_intelligence.db.session import tenant_txn
from documents.config import load_config
from documents.embed import HashingEmbedder
from documents.scan import ScanPort, SignatureScanner
from documents.service import DocumentService
from research_files.blobstore import LocalBlobStore
from tests.research_files.world import admin

__all__ = ["admin"]


@dataclass
class Queue:
    """The scheduler port: records enqueued documents."""

    documents: list[tuple[UUID, UUID]] = field(default_factory=list)

    async def enqueue_document(self, tenant_id: UUID, document_id: UUID) -> None:
        self.documents.append((tenant_id, document_id))


class Clock:
    """Wall clock (uploads expire against it)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def service(root: Path, scanner: ScanPort | None = None) -> DocumentService:
    cfg = load_config()
    return DocumentService(
        tenant_txn,
        LocalBlobStore(root),
        scanner or SignatureScanner(),
        HashingEmbedder(cfg.embedding.dimensions, cfg.embedding.model),
        Queue(),
        Clock(),
        cfg,
    )


async def upload(
    svc: DocumentService, tenant: UUID, name: str, data: bytes, account: UUID | None = None
) -> dict[str, Any]:
    """Start, PUT through the token route stand-in, complete."""
    started = await svc.start_upload(tenant, "user-1", name, len(data), None, account)
    token = parse_qs(urlparse(started["upload_url"]).query)["token"][0]

    async def body() -> Any:
        for i in range(0, len(data), 4096):
            yield data[i : i + 4096]

    await svc.receive(tenant, UUID(started["document_id"]), token, body())
    return await svc.complete(tenant, UUID(started["document_id"]))


def doc_row(document_id: UUID | str) -> dict[str, Any]:
    cols = ["status", "reject_reason", "stop_detail", "account_id", "passages", "detected_company"]
    (row,) = admin(f"SELECT {', '.join(cols)} FROM documents WHERE id = %s", str(document_id))  # noqa: S608
    return dict(zip(cols, row, strict=True))
