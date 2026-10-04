"""``build_all``: the standing file set from one snapshot (implements ``BuilderPort``).

Steps, each failing closed (B15):

1. read every row from ONE snapshot (``ContentReader``, B1);
2. map rows to render models (pure): People, Jobs, Tech, then the Summary (whose authored text is
   checked against the numbers the other files registered, B6), then Evidence over every code the
   other files cite (B5); any violation stops the build before anything is rendered;
3. render in the fixed order Summary, Evidence, People, Jobs, Tech (Word through the sandboxed
   renderer, workbooks in process) and normalise every package (B10);
4. verify each file from its bytes: leak scan of every member (B9) and the row keys re-read from
   the produced file equal the rows selected, once each and in order (B3); optionally the PDF
   lost-row check and the workbook recalculation (B12) when a converter is configured;
5. build the workspace zip only when all five files passed (D-25, B13);
6. store the set put-if-absent in one transaction (B14): a repeated build returns the stored set.

Every error of every file is returned in ``BuildResult.errors``; a failed build stores nothing.
No model, network or clock is used to produce a file (B2): the clock is read once, by the store,
for ``built_at`` on the database record.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from account_intelligence.grading.config import GradingConfig
from account_intelligence.workflow.ports import BuildIncomplete
from exports import obs
from exports.change_note import change_note
from exports.citations import orphans
from exports.config import ExportConfig
from exports.content import FILE_KINDS, SnapshotContent
from exports.errors import (
    BannedContent,
    ExportError,
    LeakError,
    MissingContent,
    MissingProvenance,
    NotConfigured,
    OrphanCitation,
    RowCapExceeded,
    RowConservation,
    SchemaViolation,
    UnsafePackage,
)
from exports.excel.jobs import render_jobs
from exports.excel.people import render_people
from exports.excel.tech import render_tech
from exports.models import (
    BuildManifest,
    BuildResult,
    FileEntry,
    FileKind,
)
from exports.ooxml import normalise_package
from exports.policy import Violation
from exports.ports import (
    CheckResult,
    ContentReader,
    DeliverableStore,
    PdfRenderPort,
    RecalcPort,
    StoredFile,
    WordRenderPort,
)
from exports.readers.common import MapContext, Mapped
from exports.readers.evidence import map_evidence
from exports.readers.jobs import map_jobs
from exports.readers.people import map_people
from exports.readers.summary import map_summary
from exports.readers.tech import map_tech
from exports.verify import (
    docx_row_keys,
    pdf_row_presence,
    recalc_check,
    scan_package,
    xlsx_row_keys,
)
from exports.word.compose import compose_evidence, compose_summary
from exports.workspace import account_slug, build_workspace, check_zip

_ERROR_OF: dict[str, type[ExportError]] = {
    "missing_url": MissingProvenance,
    "missing_source_code": MissingProvenance,
    "unknown_source_code": OrphanCitation,
    "orphan_citation": OrphanCitation,
    "uncited_graded_row": OrphanCitation,
    "narrative_without_code": OrphanCitation,
    "row_cap": RowCapExceeded,
    "leak": LeakError,
    "non_public_row": LeakError,
    "internal_in_public_file": LeakError,
    "missing_narrative": MissingContent,
    "missing_tracker": MissingContent,
    "grade_vocabulary": SchemaViolation,
    "confirmed_without_primary_read": SchemaViolation,
    "statement_flag_disagrees": SchemaViolation,
    "section_kind_vocabulary": SchemaViolation,
    "section_text_length": SchemaViolation,
    "posting_status_vocabulary": SchemaViolation,
    "posting_class_vocabulary": SchemaViolation,
    "source_kind_vocabulary": SchemaViolation,
    "name_missing": SchemaViolation,
    "text_missing": SchemaViolation,
    "speaker_missing": SchemaViolation,
}
EXTENSIONS: dict[FileKind, str] = {
    "summary": "docx",
    "evidence": "docx",
    "people": "xlsx",
    "jobs": "xlsx",
    "tech": "xlsx",
}


class BuildFailed(ExportError, BuildIncomplete):
    """Raised by ``build`` (the BuilderPort) when the set is not complete; carries the result."""

    code = "build_failed"

    def __init__(self, result: BuildResult) -> None:
        self.result = result
        self.reason = result.reason or "build_failed"
        ExportError.__init__(self, self.reason, result.errors)


def errors_of(violations: list[Violation]) -> list[ExportError]:
    """Group violations into typed errors (one per code), items are row ids or patterns."""
    grouped: dict[str, list[Violation]] = {}
    for v in violations:
        grouped.setdefault(v.code, []).append(v)
    out: list[ExportError] = []
    for code in sorted(grouped):
        cls = _ERROR_OF.get(code, BannedContent)
        items = sorted({f"{v.where}:{v.item}" for v in grouped[code]})
        out.append(cls(code, items))
    return out


@dataclass
class Assembly:
    """Everything produced from one snapshot before storage."""

    files: dict[FileKind, tuple[str, bytes]] = field(default_factory=dict)
    data: dict[FileKind, bytes] = field(default_factory=dict)
    content_hashes: dict[str, str] = field(default_factory=dict)
    mapped: dict[str, Mapped[Any]] = field(default_factory=dict)
    manifest: BuildManifest | None = None
    workspace: bytes | None = None
    workspace_name: str = ""
    errors: list[ExportError] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    long_parts: int = 0


def _filename(cfg: ExportConfig, kind: str, account: str, ext: str) -> str:
    return cfg.filenames[kind].replace("{account}", account_slug(account)) + f".{ext}"


def _content_hash(model: Any, cfg: ExportConfig) -> str:
    blob = model.model_dump_json() + cfg.theme_hash + cfg.rules_hash
    return hashlib.sha256(blob.encode()).hexdigest()


class ExportBuilder:
    """The builders (see the module docstring)."""

    def __init__(
        self,
        reader: ContentReader | None,
        store: DeliverableStore | None,
        renderer: WordRenderPort | None,
        cfg: ExportConfig,
        grading: GradingConfig,
        *,
        pdf: PdfRenderPort | None = None,
        recalc: RecalcPort | None = None,
    ) -> None:
        self.reader = reader
        self.store = store
        self.renderer = renderer
        self.cfg = cfg
        self.grading = grading
        self.pdf = pdf
        self.recalc = recalc

    # ---- pure-ish assembly (renderer subprocess is the only side effect) ----------------------

    def assemble(self, content: SnapshotContent) -> Assembly:
        """Map, render, verify and package one snapshot (no storage)."""
        out = Assembly()
        ctx = MapContext(content, self.cfg, self.grading)
        people = map_people(ctx)
        jobs = map_jobs(ctx)
        tech = map_tech(ctx)
        summary = map_summary(ctx, people.model)
        rendered_facts = frozenset(
            r.row_id for t in (summary.model.lanes if summary.model else ()) for r in t.rows
        )
        cited = set().union(people.cited, jobs.cited, tech.cited, summary.cited)
        evidence = map_evidence(ctx, cited, rendered_facts, jobs.model)
        out.mapped = {
            "summary": summary,
            "evidence": evidence,
            "people": people,
            "jobs": jobs,
            "tech": tech,
        }
        ctx.flag(
            orphans(
                {
                    "summary": summary.cited,
                    "people": people.cited,
                    "jobs": jobs.cited,
                    "tech": tech.cited,
                },
                evidence.cited,
            )
        )
        out.errors.extend(errors_of(ctx.violations))
        missing = [k for k, m in out.mapped.items() if m.model is None]
        if missing and not out.errors:
            out.errors.append(MissingContent("a file could not be mapped", missing))
        if out.errors:
            return out
        self._render_all(out, content, ctx)
        if out.errors:
            return out
        out.manifest = self._manifest(out, content, ctx)
        self._package(out, content, ctx)
        return out

    def _render_all(self, out: Assembly, content: SnapshotContent, ctx: MapContext) -> None:
        account = content.account_name
        for kind in FILE_KINDS:
            mapped = out.mapped[kind]
            model = mapped.model
            assert model is not None
            name = _filename(self.cfg, kind, account, EXTENSIONS[kind])
            title = name.rsplit(".", 1)[0].replace("_", " ")
            with obs.span(f"exports.render.{kind}", content.run_id, rows=len(mapped.row_ids)):
                try:
                    body, parts = self._render(kind, model, title, content)
                    out.long_parts += parts
                    self._verify(kind, body, mapped, ctx, out)
                except ExportError as error:
                    out.errors.append(error)
                    obs.file_outcome(kind, "failed")
                    continue
            out.files[kind] = (name, body)
            out.data[kind] = (model.model_dump_json(indent=1) + "\n").encode()
            out.content_hashes[kind] = _content_hash(model, self.cfg)
            obs.file_outcome(kind, "built")

    def _render(
        self, kind: str, model: Any, title: str, content: SnapshotContent
    ) -> tuple[bytes, int]:
        if kind in ("summary", "evidence"):
            if self.renderer is None:
                raise NotConfigured("no Word renderer")
            spec = (compose_summary if kind == "summary" else compose_evidence)(
                model, self.cfg, title
            )
            raw = self.renderer.render(spec)
            props = self.cfg.rules.document_properties
            z = self.cfg.theme.zip
            return normalise_package(
                raw,
                title=title,
                author=props.author,
                company=props.company,
                as_of=content.as_of,
                entry_time=z.entry_time,
                compression=z.compression,
            ), 0
        if kind == "people":
            return render_people(model, self.cfg, title)
        if kind == "jobs":
            return render_jobs(model, self.cfg, title)
        return render_tech(model, self.cfg, title)

    def _verify(
        self, kind: str, body: bytes, mapped: Mapped[Any], ctx: MapContext, out: Assembly
    ) -> None:
        with obs.span(f"exports.verify.{kind}", ctx.content.run_id):
            hits = scan_package(body, ctx.restricted)
            if hits:
                raise LeakError(f"{kind}: forbidden content in the produced file", hits)
            books = self.cfg.rules.workbooks
            if kind in ("summary", "evidence"):
                keys = docx_row_keys(body)
            else:
                sheet = {
                    "people": books.people.sheet,
                    "jobs": books.jobs.sheet,
                    "tech": books.tech.sheet,
                }[kind]
                keys = xlsx_row_keys(body, sheet)
            if keys != mapped.refs:
                raise RowConservation(
                    f"{kind}: rows in the file differ from the rows selected",
                    (f"file {len(keys)}", f"selected {len(mapped.refs)}"),
                )
            if self.pdf is not None and kind in ("summary", "evidence"):
                result = pdf_row_presence(body, mapped.refs, self.pdf)
                out.checks.append(result)
                if result.status == "failed":
                    raise RowConservation(f"{kind}: rows lost in the PDF rendering", result.details)
            if self.recalc is not None and kind in ("people", "jobs", "tech"):
                result = recalc_check(body, self._expected(kind, mapped), self.recalc)
                out.checks.append(result)
                if result.status == "failed":
                    raise SchemaViolation(f"{kind}: workbook recalculation", result.details)

    def _expected(self, kind: str, mapped: Mapped[Any]) -> dict[tuple[str, str], int]:
        """Formula cells and the counts computed in code they must equal (B12)."""
        books = self.cfg.rules.workbooks
        model = mapped.model
        assert model is not None
        if kind == "people":
            return {
                (books.people.summary_sheet, f"C{len(model.summary) + 2}"): model.in_scope_listed
            }
        sheet = books.jobs.counts_sheet if kind == "jobs" else books.tech.counts_sheet
        return {
            (sheet, f"B{i}"): line.value
            for i, line in enumerate(model.counts, start=2)
            if line.formula != "none"
        }

    def _manifest(self, out: Assembly, content: SnapshotContent, ctx: MapContext) -> BuildManifest:
        mapped = out.mapped
        entries = tuple(
            FileEntry(
                kind=k,
                filename=out.files[k][0],
                sha256=hashlib.sha256(out.files[k][1]).hexdigest(),
                size=len(out.files[k][1]),
                content_hash=out.content_hashes[k],
                rows=len(mapped[k].row_ids),
            )
            for k in FILE_KINDS
        )
        summary = mapped["summary"].model
        return BuildManifest(
            run_id=content.run_id,
            account=content.account_name,
            as_of=content.as_of,
            snapshot_hash=content.snapshot_hash(),
            theme_hash=self.cfg.theme_hash,
            rules_hash=self.cfg.rules_hash,
            rows_per_file={k: len(mapped[k].row_ids) for k in FILE_KINDS},
            snapshot_counts=dict(sorted(content.snapshot_counts.items())),
            row_ids={k: mapped[k].row_ids for k in FILE_KINDS},
            row_digests={k: mapped[k].digests for k in FILE_KINDS},
            refs={k: mapped[k].refs for k in FILE_KINDS},
            source_codes_cited=tuple(
                sorted(mapped["evidence"].cited, key=lambda c: int(c.split("-")[1]))
            ),
            numbers=ctx.registry.items(),
            count_sentences=ctx.registry.sentences(),
            tracker_tally=summary.tracker_tally if summary else (),
            withheld_sentences=ctx.withheld,
            long_text_parts=out.long_parts,
            formula_like_cells=ctx.formula_like,
            files=entries,
            loop_exits={
                "renderer": getattr(self.renderer, "last_exit", "not_run"),
                "keyset": "converged",
            },
        )

    def _package(self, out: Assembly, content: SnapshotContent, ctx: MapContext) -> None:
        assert out.manifest is not None
        name = _filename(self.cfg, "workspace", content.account_name, "zip")
        try:
            body = build_workspace(
                deliverables=out.files,
                data=out.data,
                manifest=json.loads(out.manifest.model_dump_json()),
                rules=self.cfg.rules,
                theme=self.cfg.theme.zip,
                account=content.account_name,
                as_of=content.as_of,
            )
            problems = check_zip(body, self.cfg.rules)
            if problems:
                raise UnsafePackage("workspace zip failed its safety check", problems)
            done = frozenset(hashlib.sha256(b).hexdigest() for _, b in out.files.values())
            hits = scan_package(body, ctx.restricted, scanned=done)
            if hits:
                raise LeakError("workspace zip: forbidden content", hits)
        except ExportError as error:
            out.errors.append(error)
            return
        out.workspace = body
        out.content_hashes["workspace"] = hashlib.sha256(
            "".join(out.content_hashes[k] for k in FILE_KINDS).encode()
        ).hexdigest()
        out.workspace_name = name

    # ---- the port ------------------------------------------------------------------------------

    async def build_all(self, tenant_id: str, run_id: str) -> BuildResult:
        """Build, verify and store the set (see the module docstring)."""
        if self.reader is None or self.store is None or self.renderer is None:
            missing = [
                n
                for n, v in (
                    ("reader", self.reader),
                    ("store", self.store),
                    ("renderer", self.renderer),
                )
                if v is None
            ]
            result = BuildResult(
                status="not_configured",
                reason="builders_not_configured",
                errors=tuple(f"builders_not_configured: {m}" for m in missing),
            )
            obs.build_outcome(run_id, result.status, ("builders_not_configured",), {})
            return result
        try:
            content = await self.reader.read(tenant_id, run_id)
        except ExportError as error:
            return self._failed(run_id, [error])
        out = self.assemble(content)
        if out.errors or out.manifest is None or out.workspace is None:
            return self._failed(run_id, out.errors or [MissingContent("nothing assembled")])
        set_hash = hashlib.sha256(
            json.dumps(out.content_hashes, sort_keys=True).encode()
        ).hexdigest()
        previous = await self.store.previous_manifest(tenant_id, content.account_id, run_id)
        note = change_note(
            BuildManifest.model_validate(previous) if previous else None, out.manifest
        )
        out.manifest = out.manifest.model_copy(update={"change_note": note})
        manifest = json.loads(out.manifest.model_dump_json())
        files = tuple(
            StoredFile(
                kind=k,
                filename=out.files[k][0],
                body=out.files[k][1],
                content_hash=out.content_hashes[k],
                rows=out.manifest.rows_per_file[k],
            )
            for k in FILE_KINDS
        ) + (
            StoredFile(
                kind="workspace",
                filename=out.workspace_name,
                body=out.workspace,
                content_hash=out.content_hashes["workspace"],
                rows=sum(out.manifest.rows_per_file.values()),
            ),
        )
        existing = await self.store.find_set(tenant_id, run_id, set_hash)
        stored = existing or await self.store.put_set(
            tenant_id,
            content.account_id,
            run_id,
            set_hash,
            files,
            manifest,
            out.manifest.snapshot_hash,
            self.cfg.theme_hash,
        )
        result = BuildResult(
            status="complete",
            reason=None,
            deliverables=stored,
            manifest=out.manifest,
            change_note=note,
            reused=bool(existing),
        )
        obs.build_outcome(run_id, "complete", (), out.manifest.rows_per_file)
        return result

    def _failed(self, run_id: str, errors: list[ExportError]) -> BuildResult:
        codes = tuple(e.code for e in errors)
        obs.build_outcome(run_id, "failed", codes, {})
        status = (
            "not_configured" if any(isinstance(e, NotConfigured) for e in errors) else ("failed")
        )
        reason = "builders_not_configured" if status == "not_configured" else "build_failed"
        return BuildResult(status=status, reason=reason, errors=tuple(str(e) for e in errors))

    async def build(self, tenant_id: str, run_id: str) -> list[str]:
        """``BuilderPort``: the stored deliverable ids; ``BuildFailed`` when not complete."""
        result = await self.build_all(tenant_id, run_id)
        if result.status == "not_configured":
            raise NotConfigured("builders_not_configured", result.errors)
        if result.status != "complete":
            raise BuildFailed(result)
        return [d.id for d in result.deliverables]


def error_codes(errors: tuple[str, ...]) -> Counter[str]:
    """Error codes of a result (the prefix before the first colon), counted."""
    return Counter(e.split(":", 1)[0] for e in errors)
