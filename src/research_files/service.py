"""Assembly of the research file parts for one process (the container builds it once)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from research_files.blobstore import BlobStore
from research_files.config import ResearchFilesConfig, load_config
from research_files.modelsvc import FileModels
from research_files.pipeline import Processor, TxnFactory
from research_files.upload import Uploads


@dataclass
class FileServices:
    """Uploads, the processor and the model service over one blob store and one LLM port."""

    blobs: BlobStore
    txn: TxnFactory
    llm: Any | None
    scheduler: Any
    worker_id: str = field(default_factory=lambda: f"files-{uuid.uuid4().hex[:12]}")
    cfg: ResearchFilesConfig = field(default_factory=load_config)

    @property
    def uploads(self) -> Uploads:
        """The upload service."""
        return Uploads(self.blobs, self.txn, self.cfg)

    @property
    def models(self) -> FileModels:
        """The model service (``llm`` None: no model is configured)."""
        return FileModels(self.llm, self.cfg, self.txn)

    def processor(self, **overrides: Any) -> Processor:
        """A processor for this worker."""
        return Processor(
            self.blobs,
            self.txn,
            self.models,
            overrides.pop("worker_id", self.worker_id),
            self.cfg,
            **overrides,
        )

    async def enqueue(self, tenant_id: UUID, file_id: UUID) -> None:
        """Ask a worker to process (or resume) the file."""
        await self.scheduler.enqueue_file(tenant_id, file_id)


def usable_llm(llm: Any) -> Any | None:
    """The LLM port when a model is configured, else None (``UnconfiguredLlm`` means none)."""
    from account_intelligence.platform.hermetic import UnconfiguredLlm

    return None if llm is None or isinstance(llm, UnconfiguredLlm) else llm
