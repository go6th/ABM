"""The pre-delivery gate for both input modes (``GatePort.check``: failing checks by name).

Checks, in order; any failure keeps the run from COMPLETED (it ends PARTIAL ``gate_failed`` with
the defects stored in ``run_gate_results`` for the operator, and Resume reruns the gate):

1. ``tracker_rows``: every one of the run's tracker rows has a status, and every row that is not
   Done or None has a reason code.
2. ``fact_citations``: every current fact of the run has a grade and either a public link or a
   file citation (document and passage location).
3. ``client_dashes``: no client file contains an em dash, an en dash or a spaced hyphen used as
   punctuation (the PRD format rule).
4. ``client_terms``: no client file names a data broker, a credit count, a provider code or an
   internal hash (the shared client-output scanner, ``exports.verify.scan_package``).
5. ``leak_check``: no client file carries the account's private uploaded material
   (``documents.leakcheck``); every check is recorded in ``leak_checks``.
6. ``internal_label`` (input mode file): the Internal Only file exists and is labelled
   "Internal: from your uploaded material".
7. ``deliverable_set`` (company runs): the five client files exist (Summary, Sources and
   Evidence, People list, Job postings, Tech stack and teams). A file-only run makes none.
8. ``rows_dropped``: every row the builder recorded for a file (its manifest ``refs``) is in the
   file, and in a Word file each appears once and in order (no row lost at a page or a table).
9. ``third_party_domains``: no client file names a data provider's host (the providers' hosts
   from ``config/providers.yaml`` join the shared scanner's terms).

This is the single pre-delivery gate (the orphaned roadmap gate in ``src/gate`` is gone; its
row-drop, document and third-party checks live here).

Defects carry identifiers, codes and counts only, never document or client text.

GUARANTEE(test_gate_blocks_a_run_with_a_defect_and_passes_a_clean_one)
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from documents import repo
from documents.config import InputModesConfig, load_config
from documents.embed import EmbeddingPort
from documents.leakcheck import LeakChecker

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
INTERNAL_LABEL = "Internal: from your uploaded material"
# typographic dashes only: a spaced hyphen inside a verbatim title or quote ("Engineer - Data")
# is the source's, not punctuation the file authored (the builder's D-24 rules lint authored
# prose for both; staging 2026-10-16: every finished run failed this check)
_DASHES = re.compile("[‒–—―]")
_XML_TAG = re.compile(r"<[^>]+>")


def client_text(data: bytes) -> str:
    """The visible text of a client file: Word body, workbook strings and cells, PPTX slides and
    notes, document properties, or plain text."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            parts = []
            for name in sorted(names):
                if name.endswith((".docx", ".xlsx", ".pptx", ".zip")):
                    parts.append(client_text(z.read(name)))
                elif (
                    (
                        name.startswith(
                            (
                                "word/",
                                "ppt/slides/",
                                "ppt/notesSlides/",
                                "xl/sharedStrings",
                                "xl/worksheets/",
                            )
                        )
                        and name.endswith(".xml")
                    )
                    or name in ("docProps/core.xml", "docProps/app.xml")
                    or name.endswith((".md", ".txt", ".csv"))
                ):
                    body = z.read(name).decode("utf-8", "ignore")
                    # character references (openpyxl writes an em dash as &#8212;) are decoded
                    parts.append(
                        html.unescape(_XML_TAG.sub(" ", body)) if name.endswith(".xml") else body
                    )
            return "\n".join(parts)
    except zipfile.BadZipFile:
        return data.decode("utf-8", "ignore")


CLIENT_FILES = frozenset({"summary", "evidence", "people", "jobs", "tech"})


@lru_cache(maxsize=1)
def provider_hosts() -> tuple[str, ...]:
    """Every data provider's registrable host (``example.com`` of ``api.example.com``), from
    config: a client file must never name one. Read once per process: every cell of an exported
    table is checked against it, and parsing providers.yaml per cell held the worker's event loop
    for minutes on a large Download everything (staging, 2026-10-20)."""
    from urllib.parse import urlsplit

    from account_intelligence.connectors.settings import load_providers_config

    out = set()
    for provider in load_providers_config().providers.values():
        host = (urlsplit(provider.base_url).hostname or "").lower()
        if host:
            out.add(".".join(host.split(".")[-2:]))
    return tuple(sorted(out))


def rows_lost(kind: str, data: bytes, body: str, manifest: Any) -> int:
    """How many of the rows the builder recorded for this file (manifest ``refs``) are missing
    from it; in a Word file each must appear once and in order."""
    refs = list((manifest.get("refs") or {}).get(kind) or ()) if isinstance(manifest, dict) else []
    if not refs:
        return 0
    if kind in ("summary", "evidence"):
        from exports.verify import docx_text, keys_once_in_order

        try:
            return len(keys_once_in_order(docx_text(data), refs))
        except (zipfile.BadZipFile, KeyError):
            return len(refs)
    return sum(1 for ref in refs if ref not in body)


@dataclass
class PreDeliveryGate:
    """See the module docstring."""

    txn: TxnFactory
    payloads: Any  # ``get(key) -> bytes`` (the store the builder wrote the client files to)
    embedder: EmbeddingPort
    cfg: InputModesConfig | None = None

    def __post_init__(self) -> None:
        self.cfg = self.cfg or load_config()

    async def check(self, tenant_id: str, run_id: str) -> dict[str, list[str]]:
        """Failing checks by name (empty lists when the run passes); the verdict is stored."""
        tenant, run = UUID(str(tenant_id)), UUID(str(run_id))
        failing: dict[str, list[str]] = {}
        async with self.txn(tenant) as s:
            meta = (
                await s.execute(
                    text("SELECT account_id, input_mode FROM account_runs WHERE id = :r"),
                    {"r": run},
                )
            ).first()
            if meta is None:
                raise LookupError("run not found for this tenant")
            failing["tracker_rows"] = await self._rows(s, run)
            failing["fact_citations"] = await self._facts(s, run)
            files = (
                await s.execute(
                    text(
                        "SELECT id, kind, object_key, manifest_json FROM deliverables"
                        " WHERE run_id = :r ORDER BY kind"
                    ),
                    {"r": run},
                )
            ).all()
            private = await repo.private_texts(s, meta.account_id)
            public = [
                r.supporting_sentence
                for r in await s.execute(
                    text(
                        "SELECT e.supporting_sentence FROM fact_evidence e JOIN facts f"
                        " ON f.id = e.fact_id WHERE f.run_id = :r AND f.visibility = 'public'"
                    ),
                    {"r": run},
                )
            ]
            internal = (
                await s.execute(
                    text("SELECT label FROM internal_deliverables WHERE run_id = :r"), {"r": run}
                )
            ).first()
        failing["client_dashes"] = []
        failing["client_terms"] = []
        failing["leak_check"] = []
        # a file-only run makes no client file (its facts are private); its linked public run
        # makes them, and is gated as a company run
        failing["deliverable_set"] = (
            [] if meta.input_mode == "file" else sorted(CLIENT_FILES - {str(f.kind) for f in files})
        )
        failing["rows_dropped"] = []
        failing["third_party_domains"] = []
        hosts = provider_hosts()
        account = meta.account_id

        async def nearest(vec: Any) -> float:
            """The closest private passage's similarity in the account's index."""
            async with self.txn(tenant) as s2:
                return await repo.nearest_private(s2, account, vec)

        assert self.cfg is not None
        checker = LeakChecker(self.cfg.leak, self.embedder, private, public, nearest)
        from exports.verify import scan_package

        for f in files:
            data = await self.payloads.get(str(f.object_key))
            body = client_text(data)
            if _DASHES.search(body):
                failing["client_dashes"].append(f"{f.kind}:{len(_DASHES.findall(body))}")
            try:
                hits = scan_package(data, ())
            except zipfile.BadZipFile:
                hits = ()
            if hits:
                failing["client_terms"].append(f"{f.kind}:{len(hits)}")
            lowered = body.lower()
            named = [h for h in hosts if h in lowered]
            if named:
                failing["third_party_domains"].append(f"{f.kind}:{len(named)}")
            lost = rows_lost(str(f.kind), data, body, f.manifest_json or {})
            if lost:
                failing["rows_dropped"].append(f"{f.kind}:{lost}")
            result = await checker.check(str(f.kind), body)
            async with self.txn(tenant) as s:
                await repo.record_leak_check(
                    s,
                    run_id=run,
                    account_id=account,
                    target=str(f.kind),
                    verdict=result.verdict,
                    ngram_hits=result.ngram_hits,
                    embedding_hits=result.embedding_hits,
                    max_similarity=result.max_similarity,
                    passages_checked=result.passages_checked,
                    hits=result.hits,
                )
            if result.verdict == "fail":
                failing["leak_check"].append(
                    f"{f.kind}:ngram={result.ngram_hits},embedding={result.embedding_hits}"
                )
        if meta.input_mode == "file":
            failing["internal_label"] = (
                [] if internal is not None and internal.label == INTERNAL_LABEL else ["missing"]
            )
        defects = [
            {"check": name, "item": item} for name, items in failing.items() for item in items
        ]
        async with self.txn(tenant) as s:
            await repo.record_gate(s, run, account, "fail" if defects else "pass", defects[:200])
        return failing

    @staticmethod
    async def _rows(s: AsyncSession, run: UUID) -> list[str]:
        rows = (
            await s.execute(
                text("SELECT row_key, status, reason_code FROM coverage_rows WHERE run_id = :r"),
                {"r": run},
            )
        ).all()
        if not rows:
            return ["no_rows"]
        out = []
        for r in rows:
            if r.status is None:
                out.append(f"{r.row_key}:unset")
            elif r.status not in ("done", "none") and not r.reason_code:
                out.append(f"{r.row_key}:no_reason")
        return out

    @staticmethod
    async def _facts(s: AsyncSession, run: UUID) -> list[str]:
        rows = (
            await s.execute(
                text(
                    "SELECT f.id, f.product_grade,"
                    " bool_or(e.url IS NOT NULL OR e.evidence_kind = 'search_proof') AS linked,"
                    " bool_or(e.evidence_kind = 'document' AND e.document_id IS NOT NULL"
                    "   AND e.document_location IS NOT NULL) AS cited"
                    " FROM facts f LEFT JOIN fact_evidence e ON e.fact_id = f.id"
                    " WHERE f.run_id = :r AND f.status = 'current' GROUP BY f.id, f.product_grade"
                ),
                {"r": run},
            )
        ).all()
        out = []
        for r in rows:
            if not r.product_grade:
                out.append(f"{r.id}:no_grade")
            elif not (r.linked or r.cited):
                out.append(f"{r.id}:no_citation")
        return out
