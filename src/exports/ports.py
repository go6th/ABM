"""Ports of the export builders (spec 1.2). Production adapters that do not exist raise
``NotConfigured``; the run then ends PARTIAL ``builders_not_configured``, never a fake success."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from exports.content import SnapshotContent
from exports.models import BuildKind, Deliverable
from exports.word.contract import DocumentSpec

CheckStatus = Literal["passed", "failed", "not_run"]


class Clock(Protocol):
    """Injected time source: used ONLY for ``built_at`` on the stored record, never in a file."""

    def now(self) -> datetime:
        """The current time (timezone-aware)."""
        ...


class ContentReader(Protocol):
    """Reads every row of a build from ONE snapshot (B1)."""

    async def read(self, tenant_id: str, run_id: str) -> SnapshotContent:
        """The run's content; raises ``MissingContent`` when the run or account is absent."""
        ...


class WordRenderPort(Protocol):
    """Renders a ``DocumentSpec`` to docx bytes (the subprocess renderer in production)."""

    last_exit: str

    def render(self, spec: DocumentSpec) -> bytes:
        """docx bytes (not yet normalised); raises ``RenderError``."""
        ...


@dataclass(frozen=True)
class StoredFile:
    """One file to store: kind, name, bytes and the input hash it is keyed by (B14)."""

    kind: BuildKind
    filename: str
    body: bytes
    content_hash: str
    rows: int


class DeliverableStore(Protocol):
    """Versioned, append-only storage of the file set (B14)."""

    async def find_set(self, tenant_id: str, run_id: str, set_hash: str) -> tuple[Deliverable, ...]:
        """The stored set with this hash for the run (empty when none)."""
        ...

    async def previous_manifest(
        self, tenant_id: str, account_id: str, run_id: str
    ) -> dict[str, object] | None:
        """The manifest of the latest earlier complete set of the account, or None."""
        ...

    async def put_set(
        self,
        tenant_id: str,
        account_id: str,
        run_id: str,
        set_hash: str,
        files: tuple[StoredFile, ...],
        manifest: dict[str, object],
        snapshot_hash: str,
        theme_hash: str,
    ) -> tuple[Deliverable, ...]:
        """Store every file put-if-absent and insert all rows in ONE transaction; each kind gets
        the next version and ``supersedes`` the previous one. Never overwrites."""
        ...


@dataclass(frozen=True)
class CheckResult:
    """An optional integration check: passed, failed (with reasons) or not_run (with reason)."""

    name: str
    status: CheckStatus
    reason: str = ""
    details: tuple[str, ...] = ()


class PdfRenderPort(Protocol):
    """Word to PDF text (for the lost-row check); ``None`` when no converter is installed."""

    def pdf_text(self, docx: bytes) -> str | None:
        """Text of the PDF rendering, or None when the converter is unavailable."""
        ...


class RecalcPort(Protocol):
    """Workbook recalculation; ``None`` when no engine is installed."""

    def recalculated(self, xlsx: bytes) -> bytes | None:
        """The workbook after a full recalculation, or None when unavailable."""
        ...
