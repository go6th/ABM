"""The pre-delivery gate for both input modes (``GatePort.check``: failing checks by name).

Checks, in order. A failing check is run-level (a step without proof, a finding without a
source, a client file missing) or file-level (``FILE_CHECKS``: its items read
``<file kind>:<detail>``). A run-level failure holds every file back (PARTIAL ``gate_failed``);
a file-level failure holds back that file only and the rest are delivered (PARTIAL
``files_withheld``, ``withheld_files``).
Both store the defects in ``run_gate_results`` for the operator, and Resume reruns the gate.

One rule has one owner. The export builder enforces the format rules on the files it makes
(``exports.verify``: the dash rule on authored prose with the titles, names and quotes it prints
blanked; the client-output denylist; the third-party scan) and records the build in each file's
manifest. The gate verifies that record by integrity (the stored SHA-256 equals the bytes it
reads, ``_builder_verified``) and does not re-run those rules on such a file: a second scan
without the builder's exemptions failed what the builder allowed (staging 2026-10-21: a key
person's title with an en dash). A file whose bytes no longer match its stored hash is scanned
whole.

1. ``tracker_rows``: every one of the run's tracker rows has a status, and every row that is not
   Done or None has a reason code.
2. ``fact_citations``: every current fact of the run has a grade and either a public link or a
   file citation (document and passage location).
3. ``client_dashes`` (file-level, files without a builder record): no client document contains
   an em dash, an en dash or a spaced hyphen used as punctuation (the PRD format rule). The
   rule's scope is authored prose: workbooks are tables of stored values and are not scanned.
4. ``client_terms`` (file-level, files without a builder record): no client file names a data
   broker, a credit count, a provider code or an internal hash (``exports.verify.scan_package``).
5. ``leak_check``: no client file carries the account's private uploaded material
   (``documents.leakcheck``); every check is recorded in ``leak_checks`` with the ids of the
   documents it matched. This product's own earlier deliverables that the client uploaded
   (the previous workspace zip) are not private material, and word runs that the account's
   public facts and evidence carry are public (``documents.repo.private_passages``).
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

import hashlib
import html
import io
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping
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


def _strings(value: Any) -> list[str]:
    """Every string leaf of a JSON value, in document order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


# the checks whose items name one client file (``<file kind>:<detail>``): a failure holds back
# that file only (see the module docstring); every other check is run-level
FILE_CHECKS = frozenset(
    {"client_dashes", "client_terms", "third_party_domains", "rows_dropped", "leak_check"}
)


def withheld_files(defects: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """The client files a stored verdict's defects hold back, by file kind, each with the checks
    that failed it (``[{"check": "leak_check", "item": "people:ngram=2,..."}]`` ->
    ``{"people": ["leak_check"]}``). Run-level defects and items naming no client file count
    for nothing here."""
    held: dict[str, list[str]] = {}
    for d in defects:
        check, item = str(d.get("check") or ""), str(d.get("item") or "")
        kind = item.split(":", 1)[0]
        if check in FILE_CHECKS and kind in CLIENT_FILES and check not in held.setdefault(kind, []):
            held[kind].append(check)
    return {k: v for k, v in held.items() if v}


def run_level_failed(defects: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a stored FAIL verdict holds back every file of its run: a defect of a run-level
    check, or so many defects that the stored list (200) was cut and may have lost one."""
    items = list(defects)
    return len(items) >= 200 or any(str(d.get("check") or "") not in FILE_CHECKS for d in items)


def _builder_verified(row: Any, data: bytes) -> bool:
    """Whether ``data`` is a file the export builder made, or a copy of a published library
    account, and is unchanged since: the deliverable carries the snapshot it was built from
    (its ``snapshot_hash`` column, which every builder row has; the per-file ``manifest_json``
    of a Word or Excel file holds only its row count and content hash, so it is not the
    record), or its manifest says ``library``; and the stored SHA-256 equals the bytes read
    back. Such a file passed the builder's own format rules (see the module docstring); any
    other file is scanned whole."""
    manifest = row.manifest_json if isinstance(row.manifest_json, dict) else {}
    built = bool(str(row.snapshot_hash or "")) or manifest.get("source") == "library"
    if not built:
        return False
    return hashlib.sha256(data).hexdigest() == str(row.sha256 or "")


def _is_workbook(data: bytes) -> bool:
    """Whether the file is an Excel workbook (a table of source values, not authored prose)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "xl/workbook.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


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
                        "SELECT id, kind, object_key, manifest_json, sha256, snapshot_hash"
                        " FROM deliverables"
                        " WHERE run_id = :r ORDER BY kind"
                    ),
                    {"r": run},
                )
            ).all()
            private = await repo.private_passages(s, meta.account_id)
            # the account's public material: what the files render comes from its current
            # public facts (earlier runs included) and their evidence, so a word run shared
            # with an upload is public when it is in here
            public = []
            for r in await s.execute(
                text(
                    "SELECT f.value_json, e.supporting_sentence FROM facts f"
                    " LEFT JOIN fact_evidence e ON e.fact_id = f.id"
                    " WHERE f.account_id = :a AND f.visibility = 'public'"
                    " AND f.status = 'current'"
                ),
                {"a": meta.account_id},
            ):
                if r.supporting_sentence:
                    public.append(str(r.supporting_sentence))
                public.append(" ".join(_strings(r.value_json)))
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

        async def nearest(vec: Any) -> tuple[float, str | None]:
            """The closest private passage's similarity in the account's index, and its document."""
            async with self.txn(tenant) as s2:
                return await repo.nearest_private(s2, account, vec)

        assert self.cfg is not None
        checker = LeakChecker(self.cfg.leak, self.embedder, private, public, nearest)
        from exports.verify import scan_package, third_party_scan

        for f in files:
            data = await self.payloads.get(str(f.object_key))
            body = client_text(data)
            # the dash rule's scope is authored prose (export_rules.yaml banned_dashes.scope): a
            # workbook's cells are stored job titles, names and labels printed as they are, and
            # the builder already scans the written documents with those blanked (staging
            # 2026-10-21: a 896 row job postings run failed here on source titles with a dash)
            # the builder's format rules, only on a file it did not make and verify itself
            if not _builder_verified(f, data):
                if not _is_workbook(data) and _DASHES.search(body):
                    failing["client_dashes"].append(f"{f.kind}:{len(_DASHES.findall(body))}")
                try:
                    hits = scan_package(data, ())
                except zipfile.BadZipFile:
                    hits = ()
                if hits:
                    failing["client_terms"].append(f"{f.kind}:{len(hits)}")
            lowered = body.lower()
            named = [h for h in hosts if h in lowered]
            if not _builder_verified(f, data):
                # a file the builder did not make: the host check covers every member (hyperlink
                # targets, comments, workbook names, custom properties, nested files), as the
                # builder's own scan does, not only the visible text (F11)
                try:
                    report = third_party_scan(data, str(f.kind))
                except zipfile.BadZipFile:
                    report = None
                if report is not None:
                    named += [
                        d.split(": ", 1)[1]
                        for d in report.result.details
                        if d.startswith("data_tool_host: ")
                    ]
                    named = sorted(set(named))
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
                    f",documents={len(result.documents)}"
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
