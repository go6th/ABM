"""Shared helpers for the research file database tests (SYNTHETIC files, fake models)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import psycopg
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import LlmResult, LlmUsage
from research_files.blobstore import LocalBlobStore
from research_files.config import ResearchFilesConfig, load_config
from research_files.service import FileServices


def admin(sql: str, *params: object) -> list[tuple[Any, ...]]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return list(cur.fetchall()) if cur.description else []


PRICED_MODEL = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())["allowlist"][0]


@dataclass
class FakeLlm:
    """A model port that answers in schema and reports tokens; ``fail`` raises on given calls."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    fail_after: int | None = None
    error: BaseException | None = None
    answer: str | None = None

    @property
    def routes(self) -> dict[str, Any]:
        """Every tier routes to a priced model from config (so estimates are priced)."""
        return {
            t: SimpleNamespace(model=PRICED_MODEL) for t in ("extractor", "synthesis", "planner")
        }

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            assert self.error is not None
            raise self.error
        self.calls.append((tier, user))
        if "numbered passages" in system:
            text = (
                self.answer
                or '{"answer": "Revenue grew [C1] and more [C9].", "citations": ["C1", "C9"]}'
            )
        else:
            text = '{"summary": "The section reports figures about the company."}'
        return LlmResult(text, PRICED_MODEL, LlmUsage(len(user) // 4, len(text) // 4))


def config(**over: Any) -> ResearchFilesConfig:
    """The real config with nested overrides, e.g. ``config(upload={"part_bytes": 1024})``."""
    cfg = load_config()
    for section, values in over.items():
        cfg = replace_section(cfg, section, values)
    return cfg


def replace_section(
    cfg: ResearchFilesConfig, section: str, values: dict[str, Any]
) -> ResearchFilesConfig:
    current = getattr(cfg, section)
    return cfg.model_copy(update={section: current.model_copy(update=values)})


@dataclass
class Queue:
    """The scheduler port: records enqueued files."""

    files: list[tuple[UUID, UUID]] = field(default_factory=list)

    async def enqueue_file(self, tenant_id: UUID, file_id: UUID) -> None:
        self.files.append((tenant_id, file_id))


def services(
    root: Path, llm: Any = None, cfg: ResearchFilesConfig | None = None, worker: str = "worker-a"
) -> FileServices:
    return FileServices(
        LocalBlobStore(root), tenant_txn, llm, Queue(), worker, cfg or load_config()
    )


async def upload(svc: FileServices, tenant: UUID, name: str, data: bytes) -> Any:
    """Init, every part, complete."""
    opened = await svc.uploads.init(tenant, "user-1", name, len(data), None)
    size = opened["part_size"]
    for n in range(opened["parts_total"]):
        chunk = data[n * size : (n + 1) * size]

        async def body(c: bytes = chunk) -> Any:
            for i in range(0, len(c), 4096):
                yield c[i : i + 4096]

        await svc.uploads.put_part(tenant, UUID(opened["upload_id"]), n, body())
    return await svc.uploads.complete(tenant, UUID(opened["upload_id"]), "user-1")


def file_row(file_id: UUID) -> dict[str, Any]:
    cols = ["status", "stop_reason", "stop_detail", "units_done", "analytics", "estimate"]
    (row,) = admin(f"SELECT {', '.join(cols)} FROM research_files WHERE id = %s", file_id)  # noqa: S608
    return dict(zip(cols, row, strict=True))


def chunks(file_id: UUID) -> list[tuple[Any, ...]]:
    return admin(
        "SELECT seq, kind, location, content FROM research_file_chunks WHERE file_id = %s"
        " ORDER BY seq",
        file_id,
    )


def findings(file_id: UUID) -> list[tuple[Any, ...]]:
    return admin(
        "SELECT severity, check_id, location, message FROM research_file_findings"
        " WHERE file_id = %s ORDER BY seq, check_id, message",
        file_id,
    )


__all__ = ["io", "replace"]
