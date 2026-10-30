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
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

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
    RenderError,
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
from exports.policy import Violation, client_rule, leak_hits, leak_rule, sanitise
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
from exports.sweep import sweep_model
from exports.verify import (
    _CITATION,
    cached_values_check,
    doc_checks,
    docx_row_keys,
    docx_rowcheck,
    pdf_row_presence,
    recalc_check,
    scan_package,
    scrub_member,
    third_party_scan,
    third_party_word,
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
        out.append(cls(code, items, rule=code, where=_file_of(grouped[code][0].where)))
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
    # the files the set does not hold, kind -> the rule that kept each out; the rest is delivered
    withheld: dict[FileKind, str] = field(default_factory=dict)
    # strings the last line of defence replaced in a model (``kind:rule`` -> count)
    late_replaced: Counter[str] = field(default_factory=Counter)
    # workspace members whose text was scrubbed (member -> replacements)
    scrubbed: Counter[str] = field(default_factory=Counter)
    # the workspace zip left out for a rule (the five files are still delivered), else None
    workspace_withheld: str | None = None


# Violations that keep the file they came from out of the set instead of ending the research: a
# file whose rows would exceed its cap, a Summary with no narrative (every block withheld by a
# rule, or distill wrote none) or no coverage table. Every other file is built and delivered.
_FILE_RULES = frozenset({"row_cap", "missing_narrative", "missing_tracker"})
_KINDS = frozenset(FILE_KINDS)


class FileHeld(Exception):  # noqa: N818 - a signal inside one build, never an error to a caller
    """One file cannot be made clean: it is left out of the set under ``rule`` (``kind:rule`` in
    the manifest). Raised by ``ExportBuilder._produce``, caught by ``_render_all``."""

    def __init__(self, kind: FileKind, rule: str) -> None:
        super().__init__(f"{kind}: {rule}")
        self.kind = kind
        self.rule = rule


def _file_of(where: str) -> FileKind | None:
    """The file kind a violation's ``where`` names (``summary.lanes`` is the Summary), or None."""
    head = where.split(".", 1)[0]
    return head if head in _KINDS else None


def _third_party_rules(details: tuple[str, ...]) -> str:
    """The rules behind a failed third-party scan, comma-joined: ``tool_name``, ``cost_word`` and
    ``data_tool_host`` (``member: rule`` and ``data_tool_host: host`` details; never the host).
    The cost word's rule is not named by the word: the manifest lists rule names and is scanned
    for it."""
    rules = {
        "data_tool_host"
        if d.startswith("data_tool_host")
        else ("cost_word" if d.endswith(": credits") else d.rsplit(": ", 1)[-1])
        for d in details
    }
    return ",".join(sorted(rules)) or "tool_name"


def _held_rule(error: ExportError) -> str | None:
    """The rule that leaves a file out of the set when ``error`` ends its verification, or None
    for a structural failure that still refuses the set: a restricted term, a data tool's name or
    link, the cost word (``leak``, ``tool_name``, ``cost_word``, ``data_tool_host``), a stale
    phrase, an excluded name, a dash or a citation no source backs, or a renderer that failed."""
    if isinstance(error, RenderError):
        return error.rule or error.code
    if isinstance(error, LeakError | BannedContent | OrphanCitation):
        return error.rule or error.code
    return None


def _scan_rules(hits: tuple[str, ...]) -> str:
    """The rules behind the hits of a package scan (``member: pattern``), comma-joined: neutral
    names only (``leak``, ``tool_name``, ``internal_id``), never the pattern."""
    return ",".join(sorted({leak_rule(h.split(": ", 1)[1]) for h in hits if ": " in h})) or "leak"


def _invalid_model(error: ValidationError) -> SchemaViolation:
    """A render model that failed its own validation (a bug, never a content rule): which model
    and field and what kind of error, never the value."""
    items = sorted(
        {f"{error.title}.{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in error.errors()}
    )
    return SchemaViolation("a render model failed validation", items, rule="model_validation")


def _filename(cfg: ExportConfig, kind: str, account: str, ext: str) -> str:
    return cfg.filenames[kind].replace("{account}", account_slug(account)) + f".{ext}"


def _content_hash(model: Any, cfg: ExportConfig) -> str:
    blob = model.model_dump_json() + cfg.theme_hash + cfg.rules_hash
    return hashlib.sha256(blob.encode()).hexdigest()


def map_snapshot(
    content: SnapshotContent,
    cfg: ExportConfig,
    grading: GradingConfig,
    alias: tuple[str, str] | None = None,
) -> tuple[dict[str, Mapped[Any]], MapContext]:
    """Map one snapshot to every file's model: the ONE mapping the export builders render and
    the client tables (``views.rowsources``) read, so on-screen rows and exported rows come from
    the same function (VW1). Pure; violations are left on the returned context."""
    ctx = MapContext(content, cfg, grading, alias=alias)
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
    if evidence.model is not None:  # no register to reconcile with when Evidence has no rows
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

    def _client_name(
        self, content: SnapshotContent
    ) -> tuple[SnapshotContent, tuple[str, str] | None]:
        """``(content, alias)``: ``content`` with an account name a client file may not print
        replaced by one it may (the name goes in every file's title, header, properties and file
        name, so an account called after a data tool, or with the cost word in its name, refused
        every file), and ``(stored name, printed name)`` so authored text that names the account
        says the printed one. The name's refused words are replaced by the record marker; a name
        that is still refused becomes ``Account`` and a short hash of its domain. A name that is
        clean is untouched (alias None)."""

        def refused(text: str) -> bool:
            return bool(
                leak_hits(sanitise(text))
                or third_party_word(text)
                or client_rule(text)
                or not sanitise(text).strip()
            )

        name = content.account_name
        if not refused(name):
            return content, None
        marker = self.cfg.rules.quote_handling.record_marker
        masked = scrub_member(sanitise(name), (), marker)[0]
        if refused(masked):
            short = hashlib.sha256((content.domain or name).encode()).hexdigest()[:8]
            masked = f"Account {short}"
        return content.model_copy(update={"account_name": masked}), (name, masked)

    # ---- pure-ish assembly (renderer subprocess is the only side effect) ----------------------

    def assemble(self, content: SnapshotContent, previous: BuildManifest | None = None) -> Assembly:
        """Map, render, verify and package one snapshot (no storage). ``previous`` is the
        account's previous delivered manifest, when there is one: the change note is computed
        from it and the mapped rows before rendering, and a re-shared document opens with it."""
        out = Assembly()
        content, alias = self._client_name(content)
        try:
            out.mapped, ctx = map_snapshot(content, self.cfg, self.grading, alias)
        except ValidationError as error:
            out.errors.append(_invalid_model(error))
            return out
        # a file whose rows exceed its cap, or a Summary with no narrative or coverage table, is
        # left out and the others are built; every other violation refuses the set
        for v in ctx.violations:
            kind = _file_of(v.where)
            if v.code in _FILE_RULES and kind is not None:
                out.withheld.setdefault(kind, v.code)
        out.errors.extend(
            errors_of(
                [
                    v
                    for v in ctx.violations
                    if v.code not in _FILE_RULES or _file_of(v.where) is None
                ]
            )
        )
        missing = [k for k, m in out.mapped.items() if m.model is None and k not in out.withheld]
        if missing and not out.errors:
            out.errors.append(MissingContent("a file could not be mapped", missing))
        if out.errors:
            return out
        out.change_note = rows_change_note(
            previous, {k: (out.mapped[k].row_ids, out.mapped[k].digests) for k in FILE_KINDS}
        )
        # the opening line of a re-shared document; a first delivery prints nothing
        change_line = out.change_note if previous is not None else None
        try:
            self._render_all(out, content, ctx, change_line)
        except ValidationError as error:
            out.errors.append(_invalid_model(error))
        if out.errors:
            return out
        if not out.files:
            # nothing is left to deliver: every file was held back, which is a failed build
            out.errors.append(
                MissingContent(
                    "every file was held back",
                    [f"{k}:{r}" for k, r in sorted(out.withheld.items())],
                    rule=",".join(sorted(set(out.withheld.values()))),
                )
            )
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
            rule = ctx.block_rule(text)
            if rule is None:
                return None
            return "leak" if rule == "leak" else "third_party"

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
            if kind in out.withheld:
                obs.file_withheld(content.run_id, kind, out.withheld[kind])
                continue
            mapped = out.mapped[kind]
            model = mapped.model
            assert model is not None
            name = _filename(self.cfg, kind, account, EXTENSIONS[kind])
            title = name.rsplit(".", 1)[0].replace("_", " ")
            with obs.span(f"exports.render.{kind}", content.run_id, rows=len(mapped.row_ids)):
                try:
                    body, parts, model = self._produce(
                        kind, model, title, content, change_line, mapped, ctx, out
                    )
                except FileHeld as held:
                    out.withheld[kind] = held.rule
                    obs.file_withheld(content.run_id, kind, held.rule)
                    obs.file_outcome(kind, "withheld")
                    continue
                except ExportError as error:
                    error.where = error.where or kind
                    out.errors.append(error)
                    obs.file_outcome(kind, "failed")
                    continue
            out.long_parts += parts
            out.mapped[kind] = replace(mapped, model=model)
            out.files[kind] = (name, body)
            out.data[kind] = (model.model_dump_json(indent=1) + "\n").encode()
            out.content_hashes[kind] = _content_hash(model, self.cfg)
            obs.file_outcome(kind, "built")

    def _produce(
        self,
        kind: FileKind,
        model: Any,
        title: str,
        content: SnapshotContent,
        change_line: str | None,
        mapped: Mapped[Any],
        ctx: MapContext,
        out: Assembly,
    ) -> tuple[bytes, int, Any]:
        """Render and verify one file: ``(body, long text parts, the model it was rendered
        from)``. When the final check of the produced bytes refuses the file for its content (a
        restricted term, a data tool's name, the word credits, a stale phrase, an excluded name or
        a citation no source backs) the model's refused strings are replaced by the withheld note
        once and the file is rendered again, and a file that is still refused (or whose refused
        text is in no string of the model: an address, the file's own wording) is left out of the
        set (``FileHeld``). A research is never ended for a file's content. Structural failures
        (row conservation, cached values, schema) still raise: the package would be wrong."""
        swept = False
        # LOOP(file sweep retry): cap=2 renders (the file, then once more with its refused strings
        # replaced); exit=verified, FileHeld (still refused or nothing to replace), or a
        # structural ExportError
        for _ in range(2):
            try:
                body, parts = self._render(kind, model, title, content, change_line)
                self._verify(kind, body, replace(mapped, model=model), ctx, out)
                return body, parts, model
            except ExportError as error:
                rule = _held_rule(error)
                if rule is None:
                    raise
                if swept or isinstance(error, RenderError):
                    raise FileHeld(kind, rule) from error
                swept_model, report = sweep_model(
                    model,
                    self._sweeper(kind, ctx),
                    self.cfg.rules.quote_handling.withheld_text,
                )
                if not report.replaced:
                    raise FileHeld(kind, rule) from error
                for why, n in report.replaced.items():
                    out.late_replaced[f"{kind}:{why}"] += n
                model, swept = swept_model, True
        raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises

    def _sweeper(self, kind: FileKind, ctx: MapContext) -> Any:
        """The test the last line of defence applies to every string of a model: the mapping's
        own client-output rule plus what the document checks refuse (a stale phrase, an excluded
        name, a citation no source backs)."""
        stale = tuple(p for p in self.cfg.rules.stale_phrases.phrases if p)
        excluded = self._excluded_names()
        known = frozenset(self._account_source_ids(ctx))

        def blocked(text: str) -> str | None:
            rule = ctx.block_rule(sanitise(text))
            if rule is not None:
                return rule
            if any(p in text for p in stale):
                return "stale_phrase"
            if any(n in text for n in excluded):
                return "excluded_name"
            if kind in ("summary", "evidence") and any(
                c not in known for c in _CITATION.findall(text)
            ):
                return "orphan_citation"
            return None

        return blocked

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
                raise LeakError(
                    f"{kind}: forbidden content in the produced file",
                    hits,
                    rule=_scan_rules(hits),
                    where=kind,
                )
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
                    where=kind,
                )
            if self.pdf is not None and kind in ("summary", "evidence"):
                result = pdf_row_presence(body, mapped.refs, self.pdf)
                out.checks.append(result)
                if result.status == "failed":
                    raise RowConservation(
                        f"{kind}: rows lost in the PDF rendering", result.details, where=kind
                    )
                result = docx_rowcheck(body, self.pdf)
                out.checks.append(result)
                if result.status == "failed":
                    raise RowConservation(f"{kind}: rowcheck failed", result.details, where=kind)
            if kind in ("people", "jobs", "tech"):
                # the stored result of every formula equals the value computed in code (the
                # file shows numbers in a viewer that does not calculate)
                result = cached_values_check(body, self._expected(kind, mapped))
                out.checks.append(result)
                if result.status == "failed":
                    raise SchemaViolation(
                        f"{kind}: cached formula values", result.details, where=kind
                    )
            if self.recalc is not None and kind in ("people", "jobs", "tech"):
                result = recalc_check(body, self._expected(kind, mapped), self.recalc)
                out.checks.append(result)
                if result.status == "failed":
                    raise SchemaViolation(
                        f"{kind}: workbook recalculation", result.details, where=kind
                    )
            if kind in ("summary", "evidence"):
                dashes = self.cfg.rules.banned_dashes
                result = doc_checks(
                    body,
                    kind,
                    self._account_source_ids(ctx),
                    self.cfg.rules.stale_phrases.phrases,
                    self._excluded_names(),
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
                        f"{kind}: document checks failed",
                        result.details,
                        rule=",".join(rules),
                        where=kind,
                    )
            self._third_party(kind, body, ctx, out)

    def _excluded_names(self) -> tuple[str, ...]:
        """The names the document checks refuse in a Word file (none configured: empty)."""
        rules = getattr(self.cfg.rules, "excluded_names", None)
        return tuple(rules.names) if rules is not None else ()

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
                f"{kind}: third-party scan failed",
                report.result.details,
                rule=_third_party_rules(report.result.details),
                where=kind,
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
        built = [k for k in FILE_KINDS if k in out.files]

        def rows_of(k: FileKind) -> tuple[str, ...]:
            return mapped[k].row_ids if k in out.files else ()

        entries = tuple(
            FileEntry(
                kind=k,
                filename=out.files[k][0],
                sha256=hashlib.sha256(out.files[k][1]).hexdigest(),
                size=len(out.files[k][1]),
                content_hash=out.content_hashes[k],
                rows=len(mapped[k].row_ids),
            )
            for k in built
        )
        summary = mapped["summary"].model if "summary" in out.files else None
        cited = mapped["evidence"].cited if "evidence" in out.files else frozenset()
        return BuildManifest(
            run_id=content.run_id,
            account=content.account_name,
            as_of=content.as_of,
            snapshot_hash=content.snapshot_hash(),
            theme_hash=self.cfg.theme_hash,
            rules_hash=self.cfg.rules_hash,
            rows_per_file={k: len(rows_of(k)) for k in FILE_KINDS},
            snapshot_counts=dict(sorted(content.snapshot_counts.items())),
            row_ids={k: rows_of(k) for k in FILE_KINDS},
            row_digests={k: mapped[k].digests if k in out.files else () for k in FILE_KINDS},
            refs={k: mapped[k].refs if k in out.files else () for k in FILE_KINDS},
            source_codes_cited=tuple(sorted(cited, key=lambda c: int(c.split("-")[1]))),
            numbers=ctx.registry.items(),
            count_sentences=ctx.registry.sentences(),
            tracker_tally=summary.tracker_tally if summary else (),
            withheld_sentences=ctx.withheld,
            withheld_blocks=tuple(f"{w}:{row}:{rule}" for w, row, rule in ctx.withheld_blocks),
            long_text_parts=out.long_parts + ctx.titles_cut,
            formula_like_cells=ctx.formula_like,
            masked_terms=ctx.masked_terms,
            leaks_withheld=ctx.leaks_withheld,
            texts_withheld=obs.count_lines(self._text_parts(ctx)),
            late_replaced=tuple(f"{k}={n}" for k, n in sorted(out.late_replaced.items())),
            files_withheld=tuple(f"{k}:{r}" for k, r in sorted(out.withheld.items())),
            third_party_domains={g: tuple(sorted(h)) for g, h in sorted(out.domains.items())},
            files=entries,
            loop_exits={
                "renderer": getattr(self.renderer, "last_exit", "not_run"),
                "keyset": "converged",
            },
            checks=check_summary(out.checks),
            exclusion_hash=content.exclusion_hash,
        )

    @staticmethod
    def _text_parts(ctx: MapContext) -> Counter[tuple[str, str]]:
        """The texts the mapping replaced by the withheld note, by part of a file and rule."""
        parts: Counter[tuple[str, str]] = Counter()
        for (where, rule), n in ctx.texts_withheld.items():
            parts[(obs.where_part(where), rule)] += n
        return parts

    def _package(self, out: Assembly, content: SnapshotContent, ctx: MapContext) -> None:
        assert out.manifest is not None
        name = _filename(self.cfg, "workspace", content.account_name, "zip")
        records: dict[str, bytes] = {}
        try:
            records = self._scrubbed(self._run_records(content, ctx, out), ctx, out)
            data = self._scrubbed(
                {f"{k}.json": b for k, b in out.data.items()}, ctx, out, count_as="build"
            )
            out.data = {k: data[f"{k}.json"] for k in out.data}
            out.manifest = out.manifest.model_copy(
                update={
                    "members_scrubbed": tuple(f"{m}={n}" for m, n in sorted(out.scrubbed.items()))
                }
            )
            body = self._zip(out, content, ctx, records)
            done = frozenset(hashlib.sha256(b).hexdigest() for _, b in out.files.values())
            hits = scan_package(body, ctx.restricted, scanned=done)
            report = third_party_scan(
                body,
                "workspace",
                account_hosts=self._account_hosts(content),
                aggregator_hosts=self._source_hosts(content, "aggregator"),
                scanned=done,
            )
            out.checks.append(report.result)
            if hits or report.result.status == "failed":
                # nothing left in the zip is text we could still scrub: the zip alone is left out
                # (the five files are delivered) rather than the research ended
                rules = {
                    *(_scan_rules(hits).split(",") if hits else ()),
                    *(
                        _third_party_rules(report.result.details).split(",")
                        if report.result.status == "failed"
                        else ()
                    ),
                }
                out.workspace_withheld = ",".join(sorted(rules))
                obs.file_withheld(content.run_id, "workspace", out.workspace_withheld)
                return
        except ExportError as error:
            error.where = error.where or "workspace"
            out.errors.append(error)
            return
        out.workspace = body
        # the zip also holds the run's own records, so they are part of what its hash covers
        out.content_hashes["workspace"] = hashlib.sha256(
            (
                "".join(out.content_hashes[k] for k in FILE_KINDS if k in out.content_hashes)
                + "".join(hashlib.sha256(records[n]).hexdigest() for n in sorted(records))
            ).encode()
        ).hexdigest()
        out.workspace_name = name

    def _scrubbed(
        self, members: dict[str, bytes], ctx: MapContext, out: Assembly, *, count_as: str = ""
    ) -> dict[str, bytes]:
        """The text members with every restricted term, data tool name, link to a data tool and
        the word credits replaced by the record marker (the run's own records may mention what a
        client file may not print; the member stays, scrubbed). Counted by member name; the
        render-model data (``count_as``) is counted as one group."""
        marker = self.cfg.rules.quote_handling.record_marker
        done: dict[str, bytes] = {}
        for name, body in members.items():
            if not name.endswith((".json", ".md", ".txt")):
                done[name] = body
                continue
            text, n = scrub_member(body.decode("utf-8", "ignore"), ctx.restricted, marker)
            if n:
                out.scrubbed[f"{count_as}/{name}" if count_as else name] += n
                body = text.encode()
            done[name] = body
        return done

    def _scrubbed_value(self, value: Any, ctx: MapContext, out: Assembly) -> Any:
        """``value`` (a JSON document) with every string scrubbed as a workspace member is: the
        manifest the zip embeds carries counts and the registry's sentences, and none of them may
        name a restricted term, a data tool or the cost word."""
        marker = self.cfg.rules.quote_handling.record_marker
        if isinstance(value, str):
            text, n = scrub_member(value, ctx.restricted, marker)
            if n:
                out.scrubbed["MANIFEST.json"] += n
            return text
        if isinstance(value, list):
            return [self._scrubbed_value(v, ctx, out) for v in value]
        if isinstance(value, dict):
            return {k: self._scrubbed_value(v, ctx, out) for k, v in value.items()}
        return value

    def _zip(
        self, out: Assembly, content: SnapshotContent, ctx: MapContext, records: dict[str, bytes]
    ) -> bytes:
        """The workspace zip of the files built so far. A package over its size caps is built
        again without the archived copies (the optional members), listed in the manifest."""
        assert out.manifest is not None
        # LOOP(zip size retry): cap=2 builds (with the archived copies, then without them);
        # exit=built and checked, or UnsafePackage
        for attempt in (0, 1):
            try:
                body = build_workspace(
                    deliverables=out.files,
                    data=out.data,
                    manifest=self._scrubbed_value(
                        json.loads(out.manifest.model_dump_json()), ctx, out
                    ),
                    rules=self.cfg.rules,
                    theme=self.cfg.theme.zip,
                    account=content.account_name,
                    as_of=content.as_of,
                    extras=records,
                    releases=out.releases,
                    withheld=tuple(out.withheld),
                )
                problems = check_zip(body, self.cfg.rules)
                if problems:
                    raise UnsafePackage("workspace zip failed its safety check", problems)
                return body
            except UnsafePackage:
                if attempt or not out.releases:
                    raise
                out.releases = {}
                held = (*out.manifest.withheld_blocks, "workspace.releases:all:size_cap")
                out.manifest = out.manifest.model_copy(update={"withheld_blocks": held})
        raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises

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

        # the planned name of every file (a file left out of the set keeps its name in the pull
        # log's outputs column); only the built ones are listed as members
        filenames: dict[FileKind, str] = {
            k: _filename(self.cfg, k, content.account_name, EXTENSIONS[k]) for k in FILE_KINDS
        }

        def render_model(kind: str) -> Any:
            mapped = out.mapped.get(kind)
            return mapped.model if mapped is not None and kind in out.files else None

        return workspace_members(
            content,
            ws,
            filenames=filenames,
            label_of=label_of,
            clean=lambda t: _mask_tools(ctx.mask(sanitise(t)), ctx.cfg.scope.mask),
            internal_reason_texts=reasons,
            releases=len(out.releases),
            link_rule=ctx.link_rule,
            present=tuple(out.files),
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
        try:
            out = await asyncio.to_thread(
                self.assemble,
                content,
                BuildManifest.model_validate(previous) if previous else None,
            )
        except ExportError as error:  # a typed failure of the mapping (a registry clash, a cap)
            return self._failed(run_id, [error])
        except Exception as error:  # noqa: BLE001 - a bug in a build ends it PARTIAL, named
            # an unexpected exception is reported as a failed build with its class (never its
            # message, which can hold text), not as a crash the run's retries repeat forever
            obs.build_crashed(error)
            return self._failed(
                run_id,
                [
                    ExportError(
                        f"the build raised {type(error).__name__}",
                        rule="internal_error",
                        where=None,
                    )
                ],
            )
        if out.workspace is None and out.workspace_withheld is not None and out.manifest:
            out.manifest = out.manifest.model_copy(
                update={
                    "files_withheld": (
                        *out.manifest.files_withheld,
                        f"workspace:{out.workspace_withheld}",
                    )
                }
            )
        elif out.workspace is None:
            return self._failed(run_id, out.errors or [MissingContent("nothing assembled")])
        if out.errors or out.manifest is None:
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
            if k in out.files
        ) + (
            (
                StoredFile(
                    kind="workspace",
                    filename=out.workspace_name,
                    body=out.workspace,
                    content_hash=out.content_hashes["workspace"],
                    rows=sum(out.manifest.rows_per_file.values()),
                ),
            )
            if out.workspace is not None
            else ()
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
                    if kind in out.files
                    for quote in self._verbatim_quotes(kind, out.mapped[kind].model)
                )
            ),
            withheld_files=out.manifest.files_withheld,
        )
        obs.build_outcome(run_id, "complete", (), out.manifest.rows_per_file)
        for name, outcome in out.manifest.checks.items():
            if outcome.startswith("not_run"):
                obs.check_not_run(run_id, name, outcome)
        if out.manifest.withheld_blocks:
            obs.withheld_blocks(run_id, out.manifest.withheld_blocks)
        if out.manifest.leaks_withheld:
            obs.leaks_withheld(out.manifest.leaks_withheld, out.manifest.texts_withheld)
        if out.manifest.late_replaced:
            obs.late_replaced(run_id, out.manifest.late_replaced)
        if out.manifest.members_scrubbed:
            obs.members_scrubbed(run_id, out.manifest.members_scrubbed)
        return result

    def _failed(self, run_id: str, errors: list[ExportError]) -> BuildResult:
        rules = rules_of(errors)
        # the file, the rule and how many items it fired on, so the log names the cause without
        # text: ``summary.leak=2,evidence.leak=5,jobs.cost_word=1``
        detail = ",".join(
            f"{e.where + '.' if e.where else ''}{e.rule or e.code}={len(e.items) or 1}"
            for e in errors
        )
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
