"""``build_all``: the standing file set from one snapshot (implements ``BuilderPort``).

Steps, each failing closed (B15):

1. read every row from ONE snapshot (``ContentReader``, B1);
2. map rows to render models (pure): People, Jobs, Tech, then the Summary (whose authored text is
   checked against the numbers the other files registered, B6), then Evidence over every code the
   other files cite (B5); any violation stops the build before anything is rendered;
3. render in the fixed order Summary, Evidence, People, Jobs, Tech (Word through the sandboxed
   renderer, workbooks in process) and normalise every package (B10);
4. verify each file from its bytes: leak scan of every member (B9) and the row keys re-read from
   the produced file equal the rows selected, once each and in order (B3); the PDF lost-row check
   (rowcheck), the document checks (stale phrases, excluded names, banned dashes, orphan citations),
   and the workbook recalculation (B12) when a converter is configured;
5. build the workspace zip only when all five files passed (D-25, B13);
6. store the set put-if-absent in one transaction (B14): a repeated build returns the stored set.

Every error of every file is returned in ``BuildResult.errors``; a failed build stores nothing.
No model, network or clock is used to produce a file (B2): the clock is read once, by the store,
for ``built_at`` on the database record.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from account_intelligence.grading.config import GradingConfig
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.workflow.ports import BuildIncomplete
from exports import obs
from exports.change_note import rows_change_note
from exports.citations import orphans
from exports.config import ExportConfig, JobsBook, PeopleBook, TechBook
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
from exports.excel.jobs import expected_jobs, render_jobs
from exports.excel.people import expected_people, render_people
from exports.excel.tech import LIST_HEADER, expected_tech, render_tech
from exports.models import (
    BuildManifest,
    BuildResult,
    FileEntry,
    FileKind,
)
from exports.ooxml import normalise_package
from exports.policy import Violation, leak_hits, sanitise
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
    cached_values_check,
    doc_checks,
    docx_row_keys,
    docx_rowcheck,
    pdf_row_presence,
    recalc_check,
    scan_package,
    third_party_blocked,
    third_party_scan,
    xlsx_row_keys,
    xlsx_stack_keys,
)
from exports.word.compose import compose_evidence, compose_summary
from exports.workspace import account_slug, build_workspace, check_zip
from exports.workspace_files import RestoreInputs, release_members, workspace_members

_ERROR_OF: dict[str, type[ExportError]] = {
    "missing_url": MissingProvenance,
    "bad_url": MissingProvenance,
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
    "origin_vocabulary": SchemaViolation,
    "speaker_missing": SchemaViolation,
    "question_missing": SchemaViolation,
    "aggregator_not_lead": SchemaViolation,
    "section_fact_not_rendered": OrphanCitation,
    "lane_unknown": SchemaViolation,  # a lane meaning naming no configured lane (withheld)
    # the D-24 prose rules and the number rule, named so a failure never reads as a bare
    # ``banned_content`` (they withhold a Summary block today; listed for any other caller)
    "banned_dash": BannedContent,
    "stale_phrase": BannedContent,
    "abbreviation_first_use": BannedContent,
    "partner_framing": BannedContent,
    "unregistered_number": BannedContent,
    "unconfirmed_stated": BannedContent,
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
        # why, by rule (missing_narrative, banned_dash, leak...): the research run records these
        # instead of the bare "build_failed" that told the user nothing (staging 2026-10-15), and
        # the rule rather than the class, which told them little more (staging 2026-10-21)
        self.codes = ",".join(result.codes or sorted(error_codes(result.errors)))[:120]
        ExportError.__init__(self, self.reason, result.errors)


def check_summary(checks: list[CheckResult]) -> dict[str, str]:
    """One line per check name for the manifest: the worst outcome of its runs (``failed`` over
    ``not_run`` over ``passed``), with the distinct reasons of a check that did not run."""
    order = {"passed": 0, "not_run": 1, "failed": 2}
    worst: dict[str, str] = {}
    why: dict[str, list[str]] = {}
    for c in checks:
        if order[c.status] >= order.get(worst.get(c.name, "passed"), 0):
            worst[c.name] = c.status
        if c.status != "passed" and c.reason and c.reason not in why.setdefault(c.name, []):
            why[c.name].append(c.reason)
    out: dict[str, str] = {}
    for name, status in sorted(worst.items()):
        reasons = "; ".join(why.get(name, []))
        out[name] = status if status == "passed" or not reasons else f"{status}: {reasons}"
    return out


def errors_of(violations: list[Violation]) -> list[ExportError]:
    """Group violations into typed errors (one per code), items are row ids or patterns."""
    grouped: dict[str, list[Violation]] = {}
    for v in violations:
        grouped.setdefault(v.code, []).append(v)
    out: list[ExportError] = []
    for code in sorted(grouped):
        cls = _ERROR_OF.get(code, BannedContent)
        items = sorted({f"{v.where}:{v.item}" for v in grouped[code]})
        out.append(cls(code, items, rule=code))
    return out


def rules_of(errors: list[ExportError]) -> tuple[str, ...]:
    """The rules behind a failed build, distinct and sorted: an error's ``rule`` (one or several,
    comma-joined) when it names one, else its class code."""
    rules: set[str] = set()
    for e in errors:
        rules.update((e.rule or e.code).split(","))
    return tuple(sorted(rules))


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
    # data/releases/: member name -> text, one per archived copy the zip carries
    releases: dict[str, bytes] = field(default_factory=dict)
    errors: list[ExportError] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    long_parts: int = 0
    # external hosts of the five files by third-party group (spec 12), for the manifest
    domains: dict[str, set[str]] = field(default_factory=dict)
    # what changed since the previous delivery (spec 8.3), computed from the mapped rows before
    # rendering so a re-shared document opens with it; None until mapping is done
    change_note: str | None = None


def _filename(cfg: ExportConfig, kind: str, account: str, ext: str) -> str:
    return cfg.filenames[kind].replace("{account}", account_slug(account)) + f".{ext}"


def _content_hash(model: Any, cfg: ExportConfig) -> str:
    blob = model.model_dump_json() + cfg.theme_hash + cfg.rules_hash
    return hashlib.sha256(blob.encode()).hexdigest()


def map_snapshot(
    content: SnapshotContent, cfg: ExportConfig, grading: GradingConfig
) -> tuple[dict[str, Mapped[Any]], MapContext]:
    """Map one snapshot to every file's model: the ONE mapping the export builders render and
    the client tables (``views.rowsources``) read, so on-screen rows and exported rows come from
    the same function (VW1). Pure; violations are left on the returned context."""
    ctx = MapContext(content, cfg, grading)
    people = map_people(ctx)
    jobs = map_jobs(ctx)
    tech = map_tech(ctx)
    summary = map_summary(ctx, people.model, jobs.model)
    rendered_facts = frozenset(
        r.row_id for t in (summary.model.lanes if summary.model else ()) for r in t.rows
    )
    cited = set().union(people.cited, jobs.cited, tech.cited, summary.cited)
    evidence = map_evidence(
        ctx, cited, rendered_facts, jobs.model, tech.model, people.model, summary.model
    )
    mapped: dict[str, Mapped[Any]] = {
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
    return mapped, ctx


def _mask_tools(text: str, mask: str) -> str:
    """A query or term that names a data tool (an account called "Parallel", say) shows the scope
    mask in the workspace's pull log instead of failing the whole zip: the name of a data tool
    never reaches a client file, and one account's name must not stop the delivery."""
    from account_intelligence.safety.client_output import load_patterns

    for pattern in load_patterns():
        text = pattern.sub(mask, text)
    return text


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

    def assemble(self, content: SnapshotContent, previous: BuildManifest | None = None) -> Assembly:
        """Map, render, verify and package one snapshot (no storage). ``previous`` is the
        account's previous delivered manifest, when there is one: the change note is computed
        from it and the mapped rows before rendering, and a re-shared document opens with it."""
        out = Assembly()
        out.mapped, ctx = map_snapshot(content, self.cfg, self.grading)
        out.errors.extend(errors_of(ctx.violations))
        missing = [k for k, m in out.mapped.items() if m.model is None]
        if missing and not out.errors:
            out.errors.append(MissingContent("a file could not be mapped", missing))
        if out.errors:
            return out
        out.change_note = rows_change_note(
            previous, {k: (out.mapped[k].row_ids, out.mapped[k].digests) for k in FILE_KINDS}
        )
        # the opening line of a re-shared document; a first delivery prints nothing
        change_line = out.change_note if previous is not None else None
        self._render_all(out, content, ctx, change_line)
        if out.errors:
            return out
        self._releases(out, ctx)
        out.manifest = self._manifest(out, content, ctx)
        self._package(out, content, ctx)
        return out

    def _releases(self, out: Assembly, ctx: MapContext) -> None:
        """The text files of the archived copies (``data/releases/``). A copy a client-output
        rule refuses (a restricted term, a data tool's name or link, the word credits) is left
        out of the zip and listed in the manifest by a hash of its address; it never stops the
        delivery."""
        content = ctx.content

        def blocked(text: str) -> str | None:
            # the rule names are neutral: the manifest is scanned like every other member, and
            # it must not carry the very words (a tool's name, "credits") it reports leaving out
            if leak_hits(text, ctx.restricted):
                return "leak"
            return "third_party" if third_party_blocked(text) else None

        out.releases, left_out = release_members(
            content,
            self.cfg.rules.workspace,
            lambda t: _mask_tools(ctx.mask(sanitise(t)), ctx.cfg.scope.mask),
            blocked,
        )
        for url, why in left_out:
            digest = hashlib.sha256(url.encode()).hexdigest()[:8]
            ctx.withheld_blocks.append(("workspace.releases", digest, why))

    def _render_all(
        self,
        out: Assembly,
        content: SnapshotContent,
        ctx: MapContext,
        change_line: str | None = None,
    ) -> None:
        account = content.account_name
        for kind in FILE_KINDS:
            mapped = out.mapped[kind]
            model = mapped.model
            assert model is not None
            name = _filename(self.cfg, kind, account, EXTENSIONS[kind])
            title = name.rsplit(".", 1)[0].replace("_", " ")
            with obs.span(f"exports.render.{kind}", content.run_id, rows=len(mapped.row_ids)):
                try:
                    body, parts = self._render(kind, model, title, content, change_line)
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
        self,
        kind: str,
        model: Any,
        title: str,
        content: SnapshotContent,
        change_line: str | None = None,
    ) -> tuple[bytes, int]:
        if kind in ("summary", "evidence"):
            if self.renderer is None:
                raise NotConfigured("no Word renderer")
            spec = (compose_summary if kind == "summary" else compose_evidence)(
                model, self.cfg, title, change_line
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
        model = mapped.model
        assert model is not None
        with obs.span(f"exports.verify.{kind}", ctx.content.run_id):
            hits = scan_package(body, ctx.restricted)
            if hits:
                raise LeakError(f"{kind}: forbidden content in the produced file", hits)
            books = self.cfg.rules.workbooks
            if kind in ("summary", "evidence"):
                keys = docx_row_keys(body)
            else:
                by_kind: dict[str, PeopleBook | JobsBook | TechBook] = {
                    "people": books.people,
                    "jobs": books.jobs,
                    "tech": books.tech,
                }
                book = by_kind[kind]
                if kind == "tech":
                    # the Confirmed stack has no Ref column: its rows are keyed by their cells
                    keys = xlsx_stack_keys(body, books.tech.sheet, LIST_HEADER)
                else:
                    keys = xlsx_row_keys(body, book.sheet, books.ref_column(book))
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
                result = docx_rowcheck(body, self.pdf)
                out.checks.append(result)
                if result.status == "failed":
                    raise RowConservation(f"{kind}: rowcheck failed", result.details)
            if kind in ("people", "jobs", "tech"):
                # the stored result of every formula equals the value computed in code (the
                # file shows numbers in a viewer that does not calculate)
                result = cached_values_check(body, self._expected(kind, mapped))
                out.checks.append(result)
                if result.status == "failed":
                    raise SchemaViolation(f"{kind}: cached formula values", result.details)
            if self.recalc is not None and kind in ("people", "jobs", "tech"):
                result = recalc_check(body, self._expected(kind, mapped), self.recalc)
                out.checks.append(result)
                if result.status == "failed":
                    raise SchemaViolation(f"{kind}: workbook recalculation", result.details)
            if kind in ("summary", "evidence"):
                dashes = self.cfg.rules.banned_dashes
                result = doc_checks(
                    body,
                    kind,
                    self._account_source_ids(ctx),
                    self.cfg.rules.stale_phrases.phrases,
                    self.cfg.rules.excluded_names.names
                    if hasattr(self.cfg.rules, "excluded_names")
                    else (),
                    self._verbatim_quotes(kind, model),
                    self._passthrough_texts(kind, model),
                    dashes.characters,
                    dashes.spaced_hyphen_is_punctuation,
                )
                out.checks.append(result)
                if result.status == "failed":
                    rules = sorted({d.split(":", 1)[0] for d in result.details})
                    cls = OrphanCitation if rules == ["orphan_citation"] else BannedContent
                    raise cls(
                        f"{kind}: document checks failed", result.details, rule=",".join(rules)
                    )
            self._third_party(kind, body, ctx, out)

    def _third_party(self, kind: str, body: bytes, ctx: MapContext, out: Assembly) -> None:
        """Spec 12 on one file: a data tool name or host or the word "credits" fails it; the
        external hosts join the manifest's four groups."""
        report = third_party_scan(
            body,
            kind,
            account_hosts=self._account_hosts(ctx.content),
            aggregator_hosts=self._source_hosts(ctx.content, "aggregator"),
        )
        out.checks.append(report.result)
        for group, hosts in report.domains.items():
            out.domains.setdefault(group, set()).update(hosts)
        if report.result.status == "failed":
            raise BannedContent(
                f"{kind}: third-party scan failed", report.result.details, rule="tool_name"
            )

    @staticmethod
    def _source_hosts(content: SnapshotContent, kind: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    (urlsplit(s.url).hostname or "").casefold()
                    for s in content.sources
                    if s.kind == kind and urlsplit(s.url).hostname
                }
            )
        )

    def _account_hosts(self, content: SnapshotContent) -> tuple[str, ...]:
        """The account domain (and its subdomains, matched by suffix), the verified careers host
        and the hosts of the registered primary sources."""
        own = [h for h in (content.domain, content.careers_host) if h]
        return tuple(
            sorted({*(h.casefold() for h in own), *self._source_hosts(content, "primary")})
        )

    def _expected(self, kind: str, mapped: Mapped[Any]) -> dict[tuple[str, str], int | str | float]:
        """Formula cells and the values computed in code they must equal (B12): the planners of
        the workbook renderers, so a cell is checked exactly where it was written."""
        model = mapped.model
        assert model is not None
        if kind == "people":
            return expected_people(model, self.cfg)
        if kind == "jobs":
            return expected_jobs(model, self.cfg)
        return expected_tech(model, self.cfg)

    def _account_source_ids(self, ctx: MapContext) -> tuple[str, ...]:
        """Valid source IDs for the account (keys of the URL map) for doc_checks."""
        return tuple(sorted(ctx.index.by_code.keys()))

    @staticmethod
    def _verbatim_quotes(kind: str, model: Any) -> tuple[str, ...]:
        """Permit exact source text through authored-prose dash checks after quote screening."""
        if kind == "summary":
            return tuple(q.text for q in model.executive_voice) + tuple(
                finding.sentence for finding in model.linkedin_findings if not finding.withheld
            )
        return (
            tuple(
                use.sentence for source in model.sources for use in source.uses if not use.withheld
            )
            + tuple(item.sentence for item in model.news)
            + tuple(q.text for g in model.extracts for q in g.quotes if not q.withheld)
        )

    @staticmethod
    def _passthrough_texts(kind: str, model: Any) -> tuple[str, ...]:
        """Stored texts a document prints as they are (titles, names, findings, questions,
        links): outside the dash rule's scope, authored prose. A job title with a dash is the
        posting's own, not punctuation the platform wrote (a fifth of stored titles carry one)."""
        if kind == "summary":
            rows = [r for table in model.lanes for r in table.rows]
            return tuple(
                t
                for t in (
                    *(r.finding for r in rows),
                    *(r.question or "" for r in rows),
                    *(p.name for p in model.key_people),
                    *(p.title for p in model.key_people),
                    *(str(p.profile_url) for p in model.key_people),
                    *(f.person for f in model.linkedin_findings),
                    *(str(f.url) for f in model.linkedin_findings),
                    *(a.text for a in model.absences),
                    *(a.question for a in model.absences),
                    *(t.row for t in model.tracker),
                    *(t.reason or "" for t in model.tracker),
                    *(t.tools for t in model.tracker),
                    *(t.needs for t in model.tracker),
                    *(t.done for t in model.tracker),
                    *(n.why for n in model.news),
                    *(b.item for b in model.baseline),
                    *(g.meaning for g in model.grade_legend),
                    *model.people_intro,
                    *model.file_names,
                    *(str(s.url) for s in model.source_links),
                    *(s.domain for s in model.source_links),
                    model.version or "",
                    model.domain or "",
                )
                if t
            )
        return tuple(
            t
            for t in (
                *(str(s.url) for s in model.sources),
                *(s.title for s in model.sources),
                *(u.finding for s in model.sources for u in s.uses),
                model.account,
                model.doc_title,
                *(j.title for j in model.job_descriptions),
                *(j.platforms for j in model.job_descriptions),
                *(j.entity for j in model.job_descriptions),
                *(str(j.url) for j in model.job_descriptions),
                *(j.sentences for j in model.job_descriptions),
                *(str(n.url) for n in model.news),
                *(g.heading for g in model.lane_reads),
                *(str(r.url) for g in model.lane_reads for r in g.rows),
                *(r.title for g in model.lane_reads for r in g.rows),
                *(r.reasoning for g in model.lane_reads for r in g.rows),
                *(g.heading for g in model.extracts),
                *(str(g.url) for g in model.extracts if g.url),
                *(q.speaker or "" for g in model.extracts for q in g.quotes),
                *(q.supports or "" for g in model.extracts for q in g.quotes),
                *(b.keyword for b in model.board_captures),
                *(b.read for b in model.board_captures),
                *(x.lead for x in model.leads),
                *(x.queries for x in model.leads),
                *(x.settle for x in model.leads),
                *(d.pull for d in model.data_pulls),
                *(d.base for d in model.data_pulls),
                *(p.posting for p in model.live_postings),
                *(p.platforms for p in model.live_postings),
                *(p.entity for p in model.live_postings),
                *(d.technology for d in model.detections),
                *(d.first_seen for d in model.detections),
                *(d.last_seen for d in model.detections),
                *(e.entity for e in model.entity_split),
                *(str(u.url) for u in model.urls),
            )
            if t
        )

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
            withheld_blocks=tuple(f"{w}:{row}:{rule}" for w, row, rule in ctx.withheld_blocks),
            long_text_parts=out.long_parts + ctx.titles_cut,
            formula_like_cells=ctx.formula_like,
            masked_terms=ctx.masked_terms,
            leaks_withheld=ctx.leaks_withheld,
            third_party_domains={g: tuple(sorted(h)) for g, h in sorted(out.domains.items())},
            files=entries,
            loop_exits={
                "renderer": getattr(self.renderer, "last_exit", "not_run"),
                "keyset": "converged",
            },
            checks=check_summary(out.checks),
            exclusion_hash=content.exclusion_hash,
        )

    def _package(self, out: Assembly, content: SnapshotContent, ctx: MapContext) -> None:
        assert out.manifest is not None
        name = _filename(self.cfg, "workspace", content.account_name, "zip")
        records: dict[str, bytes] = {}
        try:
            records = self._run_records(content, ctx, out)
            body = build_workspace(
                deliverables=out.files,
                data=out.data,
                manifest=json.loads(out.manifest.model_dump_json()),
                rules=self.cfg.rules,
                theme=self.cfg.theme.zip,
                account=content.account_name,
                as_of=content.as_of,
                extras=records,
                releases=out.releases,
            )
            problems = check_zip(body, self.cfg.rules)
            if problems:
                raise UnsafePackage("workspace zip failed its safety check", problems)
            done = frozenset(hashlib.sha256(b).hexdigest() for _, b in out.files.values())
            hits = scan_package(body, ctx.restricted, scanned=done)
            if hits:
                raise LeakError("workspace zip: forbidden content", hits)
            report = third_party_scan(
                body,
                "workspace",
                account_hosts=self._account_hosts(content),
                aggregator_hosts=self._source_hosts(content, "aggregator"),
                scanned=done,
            )
            out.checks.append(report.result)
            if report.result.status == "failed":
                raise BannedContent(
                    "workspace: third-party scan failed", report.result.details, rule="tool_name"
                )
        except ExportError as error:
            out.errors.append(error)
            return
        out.workspace = body
        # the zip also holds the run's own records, so they are part of what its hash covers
        out.content_hashes["workspace"] = hashlib.sha256(
            (
                "".join(out.content_hashes[k] for k in FILE_KINDS)
                + "".join(hashlib.sha256(records[n]).hexdigest() for n in sorted(records))
            ).encode()
        ).hexdigest()
        out.workspace_name = name

    def _run_records(
        self, content: SnapshotContent, ctx: MapContext, out: Assembly
    ) -> dict[str, bytes]:
        """The workspace's own members (entity lock, web searches, pull log, sense check, pass 1
        note, URL map) from the snapshot's stored rows; text is sanitised and masked like any
        other client text."""
        ws = self.cfg.rules.workspace
        tracker = load_tracker_config()
        reasons = frozenset(
            {
                *(
                    tracker.reason_codes[c]
                    for c in ws.pass1.reason_codes
                    if c in tracker.reason_codes
                ),
                *(
                    [str(tracker.consistency["no_internal_access_needs"])]
                    if "no_internal_access_needs" in tracker.consistency
                    else []
                ),
            }
        )

        def label_of(key: str) -> str:
            spec = ctx.attribute(key)
            if spec is None:
                return key.rsplit(".", 1)[-1].replace("_", " ")
            return spec.meaning.split(",")[0].split(" (")[0].strip() or key

        filenames = {kind: name for kind, (name, _body) in out.files.items()}

        def render_model(kind: str) -> Any:
            mapped = out.mapped.get(kind)
            return mapped.model if mapped is not None else None

        return workspace_members(
            content,
            ws,
            filenames=filenames,
            label_of=label_of,
            clean=lambda t: _mask_tools(ctx.mask(sanitise(t)), ctx.cfg.scope.mask),
            internal_reason_texts=reasons,
            releases=len(out.releases),
            restore=RestoreInputs(
                cfg=self.cfg,
                filenames=filenames,
                tech=render_model("tech"),
                jobs=render_model("jobs"),
                people=render_model("people"),
            ),
        )

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
        # the previous delivered manifest first: the change note is computed from it and the
        # mapped rows before rendering, and both documents open with it (spec 8.3)
        previous = await self.store.previous_manifest(tenant_id, content.account_id, run_id)
        # the whole assembly runs off the event loop: it renders, scans and converts files (the
        # LibreOffice checks wait up to 120 s each), and a blocked loop stops the health check
        # and the task heartbeat (security review 2026-10-29, F4)
        out = await asyncio.to_thread(
            self.assemble, content, BuildManifest.model_validate(previous) if previous else None
        )
        if out.errors or out.manifest is None or out.workspace is None:
            return self._failed(run_id, out.errors or [MissingContent("nothing assembled")])
        # the exclusion set is part of the set's identity: the same bytes built under another
        # exclusion list are a new version that records which list it honours (F1)
        set_hash = hashlib.sha256(
            json.dumps(
                {**out.content_hashes, "exclusions": content.exclusion_hash}, sort_keys=True
            ).encode()
        ).hexdigest()
        note = out.change_note or rows_change_note(None, {})
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
            verbatim_quotes=tuple(
                dict.fromkeys(
                    quote
                    for kind in ("summary", "evidence")
                    for quote in self._verbatim_quotes(kind, out.mapped[kind].model)
                )
            ),
        )
        obs.build_outcome(run_id, "complete", (), out.manifest.rows_per_file)
        for name, outcome in out.manifest.checks.items():
            if outcome.startswith("not_run"):
                obs.check_not_run(run_id, name, outcome)
        if out.manifest.withheld_blocks:
            obs.withheld_blocks(run_id, out.manifest.withheld_blocks)
        if out.manifest.leaks_withheld:
            obs.leaks_withheld(out.manifest.leaks_withheld)
        return result

    def _failed(self, run_id: str, errors: list[ExportError]) -> BuildResult:
        rules = rules_of(errors)
        # the rule and how many items it fired on, so the log names the cause without text
        detail = ",".join(f"{e.rule or e.code}={len(e.items) or 1}" for e in errors)
        obs.build_outcome(run_id, "failed", rules, {}, detail)
        status = (
            "not_configured" if any(isinstance(e, NotConfigured) for e in errors) else ("failed")
        )
        reason = "builders_not_configured" if status == "not_configured" else "build_failed"
        return BuildResult(
            status=status, reason=reason, errors=tuple(str(e) for e in errors), codes=rules
        )

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
