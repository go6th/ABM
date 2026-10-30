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

No provider code or raw payload is put into the content. The workspace zip's own records are
read here too (``datamodel.entity_lock``, ``datamodel.run_ledger``): the stored entity lock, the
run's connector calls (operation, query texts, domain, result count, dollars charged; the hosts of
a web search's results come from its stored payload) and the latest sense check. The call
reference and the provider code stay in the database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from urllib.parse import quote, urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence import archive as LA
from account_intelligence.platform.ports import PayloadStore
from account_intelligence.safety.client_output import ClientOutputLeak
from account_intelligence.tracker.adapters import RunInputAdapter
from account_intelligence.tracker.render import render_client_view
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.tally import sort_for_rendering
from crosscut.fanout import fan_out_all
from datamodel.entity_lock import load_entity_lock
from datamodel.hosts import host_of
from datamodel.run_ledger import (
    LedgerCall,
    load_failed_call_count,
    load_ledger_calls,
    load_sense_check,
)
from exports.content import (
    DetectionRow,
    EvidenceRow,
    FactRow,
    FindingRow,
    FoundVia,
    JobsCounts,
    PeopleCounts,
    PersonRow,
    PostingRow,
    PullCall,
    ReleaseRow,
    SectionRow,
    SenseCheckRow,
    SnapshotContent,
    SourceRow,
    StackRow,
    TrackerLine,
    WebHit,
)
from exports.errors import LeakError, MissingContent, RowConservation
from exports.readers.exclusions import apply_exclusions, exclusion_hash
from exports.snapshot import open_snapshot, open_system_lookup, session_factory
from exports.workspace_files import text_names_account
from jobs.config import load_jobs_config
from jobs.counts import render_count_sentence
from jobs.export import is_exported
from jobs.models import CountBlock, NotSearched, PostingCounts
from jobs.repo import PgPostingRepo, register_posting_sources
from people.config import load_people_config
from people.export import build_export
from people.repo import PgPeopleRepo
from tenancy.settings import ExclusionFilter, SettingsInvalid, TenantSettings
from tenancy.settings import current as current_settings

# stored objects read at once when a build loads the posting texts and the search payloads
READS_IN_FLIGHT = 16
_OTHER_ACCOUNTS = "SELECT name FROM accounts WHERE id <> :a AND name IS NOT NULL ORDER BY name"
_FACT_COLS = (
    "id, attribute_key, coalesce(value_text, value_json #>> '{}') AS value_text, lane, claim_kind,"
    " product_grade, engine_grade, read_in_primary_this_run, may_state_to_prospect,"
    " confirming_question, flags, lead_status, visibility,"
    " value_json ->> 'event_date' AS event_date"
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


def _web_hits(body: bytes, lock: dict[str, Any] | None) -> tuple[WebHit, ...]:
    """The result hosts of a stored web search payload (``results[].url``), each with whether the
    result's own title and excerpts name the account (None without a lock or without text). A
    payload that is not the expected shape yields no hits: the pull log then shows the call's
    count and the search list judges nothing it cannot see."""
    try:
        results = json.loads(body).get("results") or []
    except (ValueError, AttributeError):
        return ()
    hits = []
    for r in results:
        if not isinstance(r, dict):
            continue
        host = host_of(r.get("url") if isinstance(r.get("url"), str) else None)
        if not host:
            continue
        text = " ".join(
            [str(r.get("title") or ""), *(str(e) for e in r.get("excerpts") or ())]
        ).strip()
        hits.append(
            WebHit(host=host, names_account=text_names_account(lock, text) if lock else None)
        )
    return tuple(hits)


class PgContentReader:
    """``ContentReader`` over PostgreSQL and the payload store (see the module docstring)."""

    def __init__(
        self,
        payloads: PayloadStore,
        *,
        batch: int,
        snapshot: Callable[[UUID], Any] = open_snapshot,
        system_lookup: Callable[[UUID], Any] = open_system_lookup,
        key_for: Callable[..., Awaitable[str]] | None = None,
        archive_base: str | None = None,
        register: Callable[[UUID], Any] | None = None,
        releases_max: int = 2000,
        release_chars: int = 60_000,
        release_kinds: tuple[str, ...] = ("posting", "page", "feed_item"),
    ) -> None:
        self.payloads = payloads
        # the tenant transaction factory the posting links of an older run are registered with
        # (None: the reader registers nothing, it only reads)
        self.register = register
        # the archived copies carried in the workspace zip (``workspace.releases``)
        self.releases_max = releases_max
        self.release_chars = release_chars
        self.release_kinds = frozenset(release_kinds) & LA.CLIENT_KINDS
        self.batch = batch
        self.snapshot = snapshot
        self.system_lookup = system_lookup
        self.key_for = key_for
        # the public web origin the archived-copy links are made absolute on (None: no links)
        self.archive_base = archive_base.rstrip("/") if archive_base else None

    def _archive_url(self, domain: str, url: str | None, found: dict[str, Any]) -> str:
        """The absolute address of the website page showing our archived copy of ``url`` for the
        account (``{public origin}/accounts/{domain}/archive?url=``: the page of the web app,
        which holds the session; not the API path), blank when no copy is stored or no public
        origin is configured."""
        if not self.archive_base or not url or url not in found:
            return ""
        return (
            f"{self.archive_base}/accounts/{quote(domain, safe='')}/archive"
            f"?url={quote(url, safe='')}"
        )

    async def _releases(self, s: AsyncSession, account_id: UUID) -> tuple[ReleaseRow, ...]:
        """The account's archived copies for ``data/releases/``: the newest copy of each address
        (a structured record before a page copy), newest capture first, at most ``releases_max``,
        client kinds only (never a profile copy), each text read from the payload store (no paid
        call) and cut at ``release_chars``. A copy whose body cannot be read is skipped."""
        links: list[LA.ArchivedLink] = []
        offset = 0
        # LOOP(release pages): cap=releases_max / MAX_LIST pages (each page adds a copy or ends
        # the loop); exit=cap reached, an empty page, or every copy listed
        while len(links) < self.releases_max:
            page, total = await LA.list_latest(
                s,
                account_id,
                kind=None,
                offset=offset,
                limit=min(LA.MAX_LIST, self.releases_max - len(links)),
                kinds=self.release_kinds,
            )
            links.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break
        gate = asyncio.Semaphore(16)

        async def one(link: LA.ArchivedLink) -> ReleaseRow | None:
            try:
                async with gate:
                    copy = await LA.archived_copy(self.payloads, link, max_text=self.release_chars)
            except Exception:  # noqa: BLE001 - an unreadable body leaves that copy out
                return None
            seen = (copy.record or {}).get("first_seen")
            try:
                day = date.fromisoformat(str(seen)[:10])
            except ValueError:
                day = link.archived_at.date()
            title = copy.title or link.title or (urlparse(link.url).hostname or link.url)
            return ReleaseRow(kind=link.kind, url=link.url, title=title, day=day, text=copy.text)

        rows = await asyncio.gather(*(one(link) for link in links))
        return tuple(r for r in rows if r is not None)

    async def _register_posting_links(self, tid: UUID, rid: UUID) -> None:
        """Give every posting link of the run a registered source code before the snapshot is
        taken. The research registers them when it saves its postings; a run saved before that
        did not, and a rebuild of its files would drop each posting no graded fact cited. Only
        links the registry does not hold are added (codes of the others stay as they are, and
        nothing is deleted)."""
        if self.register is None:
            return
        async with self.register(tid) as s:
            account = (
                await s.execute(text("SELECT account_id FROM runs WHERE id = :r"), {"r": rid})
            ).scalar_one_or_none()
        if account is None:
            return
        record = await PgPostingRepo(self.register, tid, account).load_run(str(rid))
        if record is None:
            return
        async with self.register(tid) as s:
            await register_posting_sources(s, account, rid, record.postings, missing_only=True)

    async def read(self, tenant_id: str, run_id: str) -> SnapshotContent:
        """All rows of the build (see the module docstring)."""
        tid, rid = UUID(tenant_id), UUID(run_id)
        await self._register_posting_links(tid, rid)
        async with self.snapshot(tid) as s:
            head = (
                await s.execute(
                    text(
                        "SELECT r.account_id, coalesce(ar.created_at, r.created_at) AS created_at,"
                        " coalesce(ar.run_input, '{}'::jsonb) AS run_input, a.name, a.domain,"
                        " CASE WHEN a.careers_url_verified_at IS NOT NULL THEN a.careers_url END"
                        " AS careers_url"
                        " FROM runs r JOIN accounts a ON a.id = r.account_id"
                        " LEFT JOIN account_runs ar ON ar.id = r.id WHERE r.id = :r"
                    ),
                    {"r": rid},
                )
            ).first()
            if head is None:
                raise MissingContent("no such run for this tenant", (run_id,))
            # TN5 render time: the run's pinned settings (branding) and, for the exclusions, the
            # tenant's CURRENT settings (F1: an exclusion added after the run applies to
            # everything built after it)
            run_settings = (
                await s.execute(
                    text(
                        "SELECT v.settings_json FROM account_runs r JOIN tenant_settings_versions"
                        " v ON v.version = r.settings_version WHERE r.id = :r"
                    ),
                    {"r": rid},
                )
            ).scalar_one_or_none()
            try:
                current_exclusions = ExclusionFilter.of((await current_settings(s)).settings)
            except SettingsInvalid:
                raise MissingContent(
                    "the tenant's settings no longer validate", (run_id,)
                ) from None
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
            # shared rule (T9) and their names. Every account of the tenant counts, including a
            # colleague's private workspace, so they are listed in system scope: a rebuild a
            # user asks for would otherwise see only that user's own accounts (leakage review
            # 2026-10-30, F5)
            async with self.system_lookup(tid) as lookup:
                other_names = (
                    await lookup.execute(text(_OTHER_ACCOUNTS), {"a": account_id})
                ).scalars()
                others = (
                    *await RunInputAdapter(lookup).other_account_names(tid, rid),
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
                "SELECT id, source_code, url, published_at, kind, title, publisher FROM sources"
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
                        " confirming_question, speaker, visibility, lane FROM report_sections"
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
            # the organisation tech stack of the run, in the feed's own order (the Confirmed stack)
            counts["tech_stack_items"] = await _count(
                s,
                "SELECT count(*) FROM tech_stack_items WHERE run_id = :r AND account_id = :a",
                p,
            )
            stack = await _keyset(
                s,
                "SELECT id, business_function, category, technology, postings, position"
                " FROM tech_stack_items WHERE run_id = :r AND account_id = :a"
                " AND id::text > :after ORDER BY id::text LIMIT :batch",
                p,
                self.batch,
                counts["tech_stack_items"],
                "tech_stack_items",
            )

            factory = session_factory(s, tid)
            people_part = await self._people(factory, tid, account_id, run_id, s)
            jobs_part = await self._jobs(factory, tid, account_id, run_id, s, head.domain)
            # the archived copies behind the source links (link archive, same snapshot)
            archived_sources = (
                await LA.lookup(
                    s, account_id, [x.url for x in sources if x.url], kinds=LA.CLIENT_KINDS
                )
                if self.archive_base
                else {}
            )
            releases = await self._releases(s, account_id)
            tracker, tally = await self._tracker(s, rid)
            lock = await load_entity_lock(s, run_id=rid)
            ledger = await load_ledger_calls(s, run_id=rid)
            failed = await load_failed_call_count(s, run_id=rid)
            sense = await load_sense_check(s, run_id=rid)
        pulls = await self._pull_calls(ledger, lock)

        def src_code(source_id: Any) -> str | None:
            """The registry code of an evidence row's source id (None when it has none)."""
            return code_of.get(str(source_id)) if source_id else None

        content = SnapshotContent(
            tenant_id=tenant_id,
            account_id=str(account_id),
            run_id=run_id,
            account_name=head.name or head.domain,
            domain=head.domain,
            careers_host=urlparse(head.careers_url).hostname if head.careers_url else None,
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
                    event_date=str(f.event_date) if f.event_date else None,
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
                SourceRow(
                    code=x.source_code,
                    url=x.url,
                    published=x.published_at,
                    kind=x.kind,
                    title=x.title,
                    publisher=x.publisher,
                    archive_url=self._archive_url(head.domain, x.url, archived_sources),
                )
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
                    lane=x.lane,
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
            stack=tuple(
                StackRow(
                    id=str(x.id),
                    business_function=x.business_function,
                    category=x.category,
                    technology=x.technology,
                    postings=int(x.postings),
                )
                for x in sorted(stack, key=lambda x: (x.position, str(x.id)))
            ),
            tracker=tracker,
            tracker_tally=tally,
            entity_lock=lock,
            releases=releases,
            pull_calls=pulls,
            pull_failed=failed,
            sense_check=SenseCheckRow(findings=sense.findings, proposals=sense.proposals)
            if sense
            else None,
            snapshot_counts=counts,
            **people_part,
            **jobs_part,
        )
        content = apply_exclusions(content, current_exclusions)
        if not content.exclusion_hash:
            content = content.model_copy(
                update={"exclusion_hash": exclusion_hash(current_exclusions)}
            )
        if run_settings:
            try:
                tenant_settings = TenantSettings.model_validate(run_settings)
            except ValueError:
                raise MissingContent("the run's settings no longer validate", (run_id,)) from None
            if tenant_settings.branding is not None:
                content = content.model_copy(
                    update={"brand_name": tenant_settings.branding.display_name}
                )
        return content

    async def _pull_calls(
        self, ledger: list[LedgerCall], lock: dict[str, Any] | None
    ) -> tuple[PullCall, ...]:
        """The ledger's calls as content; a web search also lists its result hosts, read from its
        stored payload (a missing payload leaves the call without hosts, never fails the build)."""

        async def hits_of(c: LedgerCall) -> tuple[WebHit, ...]:
            if c.operation == "web_search" and c.response_ref:
                try:
                    return _web_hits(await self.payloads.get(c.response_ref), lock)
                except (LookupError, OSError, ValueError):
                    return ()
            return ()

        # one stored payload read per search call, side by side (a research makes a hundred or
        # more searches; read one after another they were a hundred object-store round trips)
        found = await fan_out_all(ledger, hits_of, limit=READS_IN_FLIGHT)
        out = []
        for c, hits in zip(ledger, found, strict=True):
            out.append(
                PullCall(
                    at=c.at,
                    operation=c.operation,
                    step=c.step,
                    queries=c.queries,
                    terms=c.terms,
                    domain=c.domain,
                    since=c.since,
                    result_count=c.result_count,
                    cost_usd=c.cost_usd,
                    hits=hits,
                )
            )
        return tuple(out)

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
                result.people,
                result.counts,
                result.count_sentences,
                result.source_codes,
                erased,
                result.profiles,
                load_people_config().si_firms,
            )
        except ClientOutputLeak as leak:
            raise LeakError("people export", tuple(h.pattern for h in leak.hits)) from None
        by_url = {
            p.profile_url: p
            for p in result.people
            if p.status == "in_scope" and p.profile_url and p.person_key not in erased
        }
        listed = {by_url[r.profile_url].person_key for r in export.rows}
        # the current title of each pulled profile (the Summary's status changes)
        profile_titles = {pr.profile_url: pr.current_title for pr in result.profiles}
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
                origin=r.origin,
                former_employers=r.former_employers,
                second_profile_url=r.second_profile_url,
                profile_title=profile_titles.get(r.profile_url),
                location=r.location,
                role_lane=r.role_lane,
                found_via=tuple(
                    FoundVia(
                        origin=v.origin,
                        pass_no=v.pass_no,
                        lane=v.lane,
                        mention_url=v.mention_url,
                    )
                    for v in r.found_via
                ),
            )
            for r in export.rows
        )
        return {
            "people": rows,
            "people_names": tuple(
                sorted((p.name, p.person_key in listed) for p in result.people if p.name)
            ),
            "people_counts": PeopleCounts(
                summary=export.summary,
                sentences=export.sentences,
                in_scope=result.counts.in_scope,
                excluded=export.excluded,
            ),
            "findings": tuple(
                FindingRow(person_id=f.person_key, claim=f.claim, sentence=f.sentence, url=f.url)
                for f in sorted(result.findings, key=lambda f: (f.person_key, f.url))
            ),
        }

    async def _jobs(
        self,
        factory: Any,
        tid: UUID,
        account_id: UUID,
        run_id: str,
        s: AsyncSession,
        domain: str,
    ) -> dict[str, Any]:
        record = await PgPostingRepo(factory, tid, account_id).load_run(run_id)
        if record is None:
            return {}
        exported = sorted(
            (p for p in record.postings if is_exported(p)),
            key=lambda p: (p.posted_at or date.min, p.posting_key),
        )
        # the archived copy behind each posting link (the job repo archives every stored text)
        archived = (
            await LA.lookup(
                s, account_id, [p.url for p in exported if p.url], kinds=LA.CLIENT_KINDS
            )
            if self.archive_base
            else {}
        )

        async def description_of(p: Any) -> str:
            if not p.text_ref:
                return ""
            body = await self.payloads.get(p.text_ref)
            if p.text_sha256 and hashlib.sha256(body).hexdigest() != p.text_sha256:
                raise MissingContent("a stored posting text fails its hash", (p.posting_key,))
            return body.decode("utf-8")

        # the posting texts read side by side (up to two thousand objects), in the rows' order
        descriptions = await fan_out_all(exported, description_of, limit=READS_IN_FLIGHT)
        rows = []
        for p, description in zip(exported, descriptions, strict=True):
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
                    # a posting only the live board holds has no feed date: it keeps the day a
                    # research first captured it, so First seen does not move with the file
                    first_seen=p.first_seen_at or p.captured_on,
                    full_text_read=p.full_text_read,
                    hostile=p.hostile,
                    window_bucket=p.window_bucket,
                    origin="live_board" if p.status_source == "live_board" else "feed",
                    class_sentence=(
                        (p.classification.evidence_sentence or "") if p.classification else ""
                    ),
                    location=p.location or "",
                    last_seen=p.last_seen,
                    seniority=p.seniority or "",
                    platform_sentence_pairs=tuple((x.platform, x.text) for x in p.sentences),
                    new_since_previous=p.new_since_previous,
                    archive_url=self._archive_url(domain, p.url, archived),
                    classified_by=p.classification.classified_by if p.classification else "",
                )
            )
        counts = _jobs_counts(record.counts)
        if counts is not None:
            # two readings of "classed": a finding (the whole posting read and the answer
            # verified) and a keyword rule over the sentences (a class, not a finding): both are
            # rows of the file, only the first is graded in the Evidence file
            by_reading = sum(1 for r in rows if r.classified_by == "extractor")
            base = "postings listed on the Jobs sheet"
            counts = counts.model_copy(
                update={
                    "lines": (
                        *counts.lines,
                        (
                            "classed_by_reading",
                            "Postings classed by reading the whole posting (graded findings)",
                            by_reading,
                            base,
                        ),
                        (
                            "classed_by_rule",
                            "Postings classed by a keyword rule over their sentences (no finding)",
                            len(rows) - by_reading,
                            base,
                        ),
                    )
                }
            )
        return {"postings": tuple(rows), "jobs_counts": counts}

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
