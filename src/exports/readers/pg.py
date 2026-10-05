"""``ContentReader`` on PostgreSQL: every row of a build from ONE snapshot (B1).

Inside ``open_snapshot`` (``REPEATABLE READ, READ ONLY`` under ``tenant_txn``):

* facts and their evidence are read as ``public_reader`` (``SET LOCAL ROLE``), so the database's
  own two-output policy filters private rows as well as the query (B8, defence in depth);
* facts, evidence, sources and detections stream by keyset pagination in ``keyset_batch``
  batches until the rows read equal the snapshot's ``count(*)`` (a mismatch fails the build, B3);
* people and postings are loaded through their own repositories (``people.repo``,
  ``jobs.repo``) over the same snapshot session, and selected by their own export rules
  (``people.export.build_export``, ``jobs.export.is_exported``): one implementation per rule;
* the tracker rows and tally are the tracker's client view (``tracker.render``);
* posting descriptions come from the payload store and are checked against their stored hash.

No provider code, call reference, cost figure or raw payload is selected.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.platform.ports import PayloadStore
from account_intelligence.safety.client_output import ClientOutputLeak
from account_intelligence.tracker.adapters import RunInputAdapter
from account_intelligence.tracker.render import render_client_view
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.tally import sort_for_rendering
from exports.content import (
    DetectionRow,
    EvidenceRow,
    FactRow,
    FindingRow,
    JobsCounts,
    PeopleCounts,
    PersonRow,
    PostingRow,
    SectionRow,
    SnapshotContent,
    SourceRow,
    TrackerLine,
)
from exports.errors import LeakError, MissingContent, RowConservation
from exports.snapshot import open_snapshot, session_factory
from jobs.config import load_jobs_config
from jobs.counts import render_count_sentence
from jobs.export import is_exported
from jobs.models import CountBlock, NotSearched, PostingCounts
from jobs.repo import PgPostingRepo
from people.export import build_export
from people.repo import PgPeopleRepo

_OTHER_ACCOUNTS = "SELECT name FROM accounts WHERE id <> :a AND name IS NOT NULL ORDER BY name"
_FACT_COLS = (
    "id, attribute_key, coalesce(value_text, value_json #>> '{}') AS value_text, lane, claim_kind,"
    " product_grade, engine_grade, read_in_primary_this_run, may_state_to_prospect,"
    " confirming_question, flags, lead_status, visibility"
)


async def _keyset(
    s: AsyncSession, sql: str, params: dict[str, Any], batch: int, total: int, what: str
) -> list[Any]:
    """Rows of ``sql`` (which must order by ``id`` and take ``:after``/``:batch``) until the rows
    read equal ``total``; more or fewer rows than the snapshot count fails (B3)."""
    rows: list[Any] = []
    after = ""
    # LOOP(export keyset pagination): cap=ceil(snapshot count / keyset_batch) + 1 batches;
    # exit=converged (rows read equal the count), RowConservation (more or fewer rows)
    for _ in range(total // batch + 2):
        page = (await s.execute(text(sql), {**params, "after": after, "batch": batch})).all()
        rows.extend(page)
        if len(page) < batch:
            break
        after = str(page[-1].id)
    if len(rows) != total:
        raise RowConservation(f"{what}: {len(rows)} rows read, snapshot count {total}", (what,))
    return rows


async def _count(s: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    return int((await s.execute(text(sql), params)).scalar_one())


def _jobs_counts(counts: PostingCounts | None) -> JobsCounts | None:
    """Count lines and sentences from the job pipeline's counts (computed by ``jobs.counts``)."""
    if counts is None:
        return None
    d = counts.distinct
    lines = (
        ("pulled", "Records pulled", counts.pulled, "raw records returned before dedupe"),
        ("deduped", "Distinct postings", counts.deduped, "records pulled, after dedupe"),
        (
            "in_window",
            "Postings analysed",
            counts.in_window,
            "distinct postings in the 12 month window or undated",
        ),
        ("undated", "Undated postings analysed", counts.undated, "postings analysed"),
        ("out_of_window", "Postings outside the window", counts.out_of_window, "distinct postings"),
        ("matched", "Postings naming a platform", d.matched, "postings analysed"),
        ("read", "Matched postings read", d.read, "postings naming a platform"),
        ("read_open", "Read postings open", d.read_status.open, "matched postings read"),
        ("read_closed", "Read postings closed", d.read_status.closed, "matched postings read"),
        *(
            (
                f"class.{k}",
                f"Read postings classed {k.replace('_', ' ')}",
                v,
                "matched postings read",
            )
            for k, v in sorted(d.by_class.items())
        ),
    )
    template = load_jobs_config().count_sentence
    sentences = [render_count_sentence(d, template)]
    for platform, block in sorted(counts.per_platform.items()):
        if isinstance(block, CountBlock):
            sentences.append(f"{platform}: {render_count_sentence(block, template)}")
        elif isinstance(block, NotSearched):
            sentences.append(f"{platform}: not searched")
    return JobsCounts(lines=lines, sentences=tuple(sentences))


class PgContentReader:
    """``ContentReader`` over PostgreSQL and the payload store (see the module docstring)."""

    def __init__(
        self,
        payloads: PayloadStore,
        *,
        batch: int,
        snapshot: Callable[[UUID], Any] = open_snapshot,
        key_for: Callable[..., Awaitable[str]] | None = None,
    ) -> None:
        self.payloads = payloads
        self.batch = batch
        self.snapshot = snapshot
        self.key_for = key_for

    async def read(self, tenant_id: str, run_id: str) -> SnapshotContent:
        """All rows of the build (see the module docstring)."""
        tid, rid = UUID(tenant_id), UUID(run_id)
        async with self.snapshot(tid) as s:
            head = (
                await s.execute(
                    text(
                        "SELECT r.account_id, coalesce(ar.created_at, r.created_at) AS created_at,"
                        " coalesce(ar.run_input, '{}'::jsonb) AS run_input, a.name, a.domain"
                        " FROM runs r JOIN accounts a ON a.id = r.account_id"
                        " LEFT JOIN account_runs ar ON ar.id = r.id WHERE r.id = :r"
                    ),
                    {"r": rid},
                )
            ).first()
            if head is None:
                raise MissingContent("no such run for this tenant", (run_id,))
            account_id: UUID = head.account_id
            as_of: date = head.created_at.date()
            policy = (
                await s.execute(
                    text(
                        "SELECT coalesce(policy_json, '{}'::jsonb) AS p FROM tenants WHERE id = :t"
                    ),
                    {"t": tid},
                )
            ).scalar_one()
            partner = bool((policy or {}).get("salesforce_partner", False))
            # other accounts never appear (skill rule 6): their domains through the tracker's
            # shared rule (T9) and their names
            other_names = (await s.execute(text(_OTHER_ACCOUNTS), {"a": account_id})).scalars()
            others = (
                *await RunInputAdapter(s).other_account_names(tid, rid),
                *other_names.all(),
            )
            p = {"r": rid, "a": account_id}
            counts: dict[str, int] = {}

            # facts and evidence as public_reader (B8 at the database layer)
            await s.execute(text("SET LOCAL ROLE public_reader"))
            fact_where = (
                "FROM facts WHERE run_id = :r AND account_id = :a"
                " AND superseded_at IS NULL AND visibility = 'public'"
            )
            counts["facts"] = await _count(s, f"SELECT count(*) {fact_where}", p)
            facts = await _keyset(
                s,
                f"SELECT {_FACT_COLS} {fact_where} AND id::text > :after ORDER BY id::text"
                " LIMIT :batch",
                p,
                self.batch,
                counts["facts"],
                "facts",
            )
            ev_where = (
                "FROM fact_evidence WHERE fact_id IN (SELECT id FROM facts WHERE"
                " run_id = :r AND account_id = :a AND superseded_at IS NULL"
                " AND visibility = 'public')"
            )
            counts["fact_evidence"] = await _count(s, f"SELECT count(*) {ev_where}", p)
            evidence = await _keyset(
                s,
                "SELECT id, fact_id, evidence_kind, url, supporting_sentence, source_id,"
                f" is_aggregator, origin, evidence_date {ev_where} AND id::text > :after"
                " ORDER BY id::text LIMIT :batch",
                p,
                self.batch,
                counts["fact_evidence"],
                "fact_evidence",
            )
            await s.execute(text("RESET ROLE"))

            counts["sources"] = await _count(
                s, "SELECT count(*) FROM sources WHERE account_id = :a", p
            )
            sources = await _keyset(
                s,
                "SELECT id, source_code, url, published_at, kind FROM sources"
                " WHERE account_id = :a AND id::text > :after ORDER BY id::text LIMIT :batch",
                p,
                self.batch,
                counts["sources"],
                "sources",
            )
            code_of = {str(r.id): r.source_code for r in sources}

            sections = (
                await s.execute(
                    text(
                        "SELECT id, kind, position, text, cited_source_codes, fact_ids,"
                        " confirming_question, speaker, visibility FROM report_sections"
                        " WHERE run_id = :r AND account_id = :a AND version = (SELECT max(version)"
                        " FROM report_sections WHERE run_id = :r AND account_id = :a)"
                        " ORDER BY kind, position, id"
                    ),
                    p,
                )
            ).all()
            counts["report_sections"] = len(sections)

            counts["tech_detections"] = await _count(
                s, "SELECT count(*) FROM tech_detections WHERE run_id = :r AND account_id = :a", p
            )
            detections = await _keyset(
                s,
                "SELECT id, technology, first_seen_at, last_seen_at, source_ref"
                " FROM tech_detections WHERE run_id = :r AND account_id = :a"
                " AND id::text > :after ORDER BY id::text LIMIT :batch",
                p,
                self.batch,
                counts["tech_detections"],
                "tech_detections",
            )

            factory = session_factory(s, tid)
            people_part = await self._people(factory, tid, account_id, run_id, s)
            jobs_part = await self._jobs(factory, tid, account_id, run_id)
            tracker, tally = await self._tracker(s, rid)

        def src_code(source_id: Any) -> str | None:
            """The registry code of an evidence row's source id (None when it has none)."""
            return code_of.get(str(source_id)) if source_id else None

        content = SnapshotContent(
            tenant_id=tenant_id,
            account_id=str(account_id),
            run_id=run_id,
            account_name=head.name or head.domain,
            as_of=as_of,
            is_salesforce_partner=partner,
            forbidden_terms=tuple(sorted(set(others))),
            facts=tuple(
                FactRow(
                    id=str(f.id),
                    attribute_key=f.attribute_key,
                    value_text=f.value_text or "",
                    lane=f.lane,
                    claim_kind=f.claim_kind,
                    product_grade=f.product_grade,
                    engine_grade=f.engine_grade,
                    read_in_primary_this_run=f.read_in_primary_this_run,
                    may_state_to_prospect=f.may_state_to_prospect,
                    confirming_question=f.confirming_question,
                    flags=tuple(sorted(f.flags or ())),
                    lead_status=f.lead_status,
                    visibility=f.visibility,
                )
                for f in facts
            ),
            evidence=tuple(
                EvidenceRow(
                    id=str(e.id),
                    fact_id=str(e.fact_id),
                    evidence_kind=e.evidence_kind,
                    url=e.url,
                    supporting_sentence=e.supporting_sentence,
                    source_code=src_code(e.source_id),
                    is_aggregator=e.is_aggregator,
                    origin=e.origin,
                    evidence_date=e.evidence_date.date() if e.evidence_date else None,
                )
                for e in evidence
            ),
            sources=tuple(
                SourceRow(code=x.source_code, url=x.url, published=x.published_at, kind=x.kind)
                for x in sorted(sources, key=lambda x: x.source_code)
            ),
            sections=tuple(
                SectionRow(
                    id=str(x.id),
                    kind=x.kind,
                    position=x.position,
                    text=x.text,
                    codes=tuple(x.cited_source_codes or ()),
                    fact_ids=tuple(str(f) for f in (x.fact_ids or ())),
                    confirming_question=x.confirming_question,
                    speaker=x.speaker,
                    visibility=x.visibility,
                )
                for x in sections
            ),
            detections=tuple(
                DetectionRow(
                    id=str(d.id),
                    technology=d.technology,
                    first_seen=d.first_seen_at,
                    last_seen=d.last_seen_at,
                    source_url=d.source_ref
                    if str(d.source_ref).startswith(("http://", "https://"))
                    else None,
                    source_code=None,
                )
                for d in sorted(detections, key=lambda d: (d.technology.casefold(), str(d.id)))
            ),
            tracker=tracker,
            tracker_tally=tally,
            snapshot_counts=counts,
            **people_part,
            **jobs_part,
        )
        return content

    async def _people(
        self, factory: Any, tid: UUID, account_id: UUID, run_id: str, s: AsyncSession
    ) -> dict[str, Any]:
        repo = PgPeopleRepo(factory, tid, account_id, self.payloads, self.key_for)  # type: ignore[arg-type]
        result = await repo.load_run(run_id)
        if result is None:
            return {}
        erased: frozenset[str] = frozenset(
            (
                await s.execute(
                    text(
                        "SELECT person_key FROM people WHERE run_id = :r AND erased_at IS NOT NULL"
                        " AND person_key IS NOT NULL"
                    ),
                    {"r": UUID(run_id)},
                )
            )
            .scalars()
            .all()
        )
        if result.counts is None:
            raise MissingContent("the people record has no counts (invariant error)", (run_id,))
        try:
            export = build_export(
                result.people, result.counts, result.count_sentences, result.source_codes, erased
            )
        except ClientOutputLeak as leak:
            raise LeakError("people export", tuple(h.pattern for h in leak.hits)) from None
        by_url = {
            p.profile_url: p
            for p in result.people
            if p.status == "in_scope" and p.profile_url and p.person_key not in erased
        }
        listed = {by_url[r.profile_url].person_key for r in export.rows}
        rows = tuple(
            PersonRow(
                id=by_url[r.profile_url].person_key,
                name=r.name,
                title=r.title,
                level=r.level,
                lanes=r.lanes,
                priority=r.priority,
                profile_url=r.profile_url,
                source_code=r.source_code or None,
            )
            for r in export.rows
        )
        return {
            "people": rows,
            "people_names": tuple(
                sorted((p.name, p.person_key in listed) for p in result.people if p.name)
            ),
            "people_counts": PeopleCounts(
                summary=export.summary, sentences=export.sentences, in_scope=result.counts.in_scope
            ),
            "findings": tuple(
                FindingRow(person_id=f.person_key, claim=f.claim, sentence=f.sentence, url=f.url)
                for f in sorted(result.findings, key=lambda f: (f.person_key, f.url))
            ),
        }

    async def _jobs(self, factory: Any, tid: UUID, account_id: UUID, run_id: str) -> dict[str, Any]:
        record = await PgPostingRepo(factory, tid, account_id).load_run(run_id)
        if record is None:
            return {}
        rows = []
        for p in sorted(
            (p for p in record.postings if is_exported(p)),
            key=lambda p: (p.posted_at or date.min, p.posting_key),
        ):
            description = ""
            if p.text_ref:
                body = await self.payloads.get(p.text_ref)
                if p.text_sha256 and hashlib.sha256(body).hexdigest() != p.text_sha256:
                    raise MissingContent("a stored posting text fails its hash", (p.posting_key,))
                description = body.decode("utf-8")
            rows.append(
                PostingRow(
                    posting_key=p.posting_key,
                    title=p.title,
                    url=p.url,
                    status=p.status,
                    posting_class=p.posting_class,
                    platforms=p.platforms,
                    platform_sentences=tuple(x.text for x in p.sentences),
                    description=description,
                    posted_at=p.posted_at,
                    first_seen=p.first_seen_at,
                    full_text_read=p.full_text_read,
                    hostile=p.hostile,
                    window_bucket=p.window_bucket,
                )
            )
        return {"postings": tuple(rows), "jobs_counts": _jobs_counts(record.counts)}

    async def _tracker(
        self, s: AsyncSession, rid: UUID
    ) -> tuple[tuple[TrackerLine, ...], tuple[str, ...]]:
        repo = CoverageRepo(s)
        rows = await repo.get_all_rows(rid)
        if not rows:
            return (), ()
        proofs = await repo.get_proofs(rid)
        view = render_client_view(rows, proofs)
        ordered = list(sort_for_rendering(rows))
        lines = tuple(
            TrackerLine(
                key=r.key,
                row=v["row"],
                status=v["status"],
                priority=v["priority"],
                reason=v["reason"],
                tools=tuple(v["tools"]),
                results=v["results"],
            )
            for r, v in zip(ordered, view["rows"], strict=True)
        )
        return lines, tuple(view["tally"])
