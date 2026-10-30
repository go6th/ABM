"""Database readers of the repair stage: one workspace's raw tables as ``records.py`` values.

Raw stores only (the job pipeline's postings, the people pipeline's rows, detections, graded
facts with their evidence, the sources register, the tool-call ledger, the tracker, matched mail
and drive items), the same for every account. Rows are read whole and left to the builders to
de-duplicate and judge; a reader never drops a row for being wrong, it only labels where the row
came from (``origin``, ``source_kind``, ``from_fact``) so the builders can tell research text from
pipeline data. Each query selects ``to_jsonb(row)`` so a column that a database has not migrated
yet is simply absent rather than an error."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from exports.repair.core import WorkspaceProfile
from exports.repair.dates import date_from_text, date_from_url
from exports.repair.keys import canonical_url, host_of
from exports.repair.records import (
    ArticleRecord,
    CoverageRecord,
    FactLine,
    JobRecord,
    LeadershipRecord,
    MailItem,
    PersonRecord,
    RepairInput,
    SearchCall,
    SignalSeed,
    SourceRecord,
    TechRecord,
)

PayloadGet = Callable[[str], Awaitable[bytes]]
READS_IN_FLIGHT = 24
_GRADE = {"confirmed": "confirmed", "probable": "probable"}
_DATE_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _day(value: Any) -> date | None:
    """A date from a stored value (date, datetime or ISO text), else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE_HEAD.match(value):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


async def _fan_out(items: list[Any], work: Callable[[Any], Awaitable[Any]]) -> list[Any]:
    """``work`` over ``items`` with at most ``READS_IN_FLIGHT`` calls at once, in order."""
    gate = asyncio.Semaphore(READS_IN_FLIGHT)

    async def one(item: Any) -> Any:
        async with gate:
            return await work(item)

    return list(await asyncio.gather(*(one(i) for i in items)))


async def read_jobs(s: Any, account_id: UUID, payload: PayloadGet | None) -> list[JobRecord]:
    """Every stored posting of the account (all runs: the builder merges repeats) and the
    research facts about postings (``from_fact``: they carry no posting of their own)."""
    rows = (
        await s.execute(
            text(
                "SELECT to_jsonb(j) AS r FROM job_postings j WHERE j.account_id = :a"
                " ORDER BY j.created_at, j.posting_key"
            ),
            {"a": account_id},
        )
    ).all()
    stored = [dict(r.r) for r in rows]

    async def description(r: Mapping[str, Any]) -> str:
        ref = r.get("text_ref")
        if not ref or payload is None:
            return ""
        try:
            return (await payload(str(ref))).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a missing text is an empty description, not a failure
            return ""

    texts = await _fan_out(stored, description)
    out: list[JobRecord] = []
    for r, body in zip(stored, texts, strict=True):
        meta = r.get("row_json") or {}
        out.append(
            JobRecord(
                key=_text(r.get("posting_key")),
                url=_text(r.get("url")),
                title=_text(r.get("title")),
                other_urls=tuple(_text(u) for u in (r.get("other_urls") or ()) if u),
                location=_text(r.get("location")),
                posted_at=_day(r.get("posted_at")),
                first_seen=_day(r.get("first_seen")) or _day(meta.get("first_seen_at")),
                last_seen=_day(r.get("last_seen")) or _day(meta.get("retrieved_at")),
                status=_text(r.get("status")),
                description=body,
                requisition_id=_text(meta.get("requisition") or meta.get("requisition_id")),
                seniority=_text(r.get("seniority")),
                stored_platforms=tuple(_text(p) for p in (r.get("platforms") or ()) if p),
                stored_class=_text(r.get("posting_class")),
                source_kind=(
                    "live_board" if _text(r.get("status_source")) == "live_board" else "feed"
                ),
                collected=_day(r.get("created_at")),
            )
        )
    facts = (
        await s.execute(
            text(
                "SELECT f.id::text AS id, coalesce(f.value_text, f.value_json->>'text') AS title,"
                " f.value_json AS v, f.created_at, e.url, e.evidence_date FROM facts f"
                " LEFT JOIN LATERAL (SELECT url, evidence_date FROM fact_evidence x WHERE"
                " x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON true"
                " WHERE f.account_id = :a AND f.status = 'current' AND f.attribute_key LIKE"
                " 'jobs.%' AND coalesce(f.value_text, f.value_json->>'text', '') !~ '^[0-9.,% ]+$'"
            ),
            {"a": account_id},
        )
    ).all()
    for f in facts:
        v = f.v if isinstance(f.v, dict) else {}
        out.append(
            JobRecord(
                key="fact:" + f.id,
                url=_text(f.url) or _text(v.get("url")),
                title=_text(f.title),
                location=_text(v.get("location")),
                posted_at=_day(v.get("posted")) or _day(f.evidence_date),
                status=_text(v.get("status")),
                source_kind="fact",
                collected=_day(f.created_at),
                from_fact=True,
            )
        )
    return out


async def read_people(s: Any, account_id: UUID) -> list[PersonRecord]:
    """Profile and people-search rows from the people pipeline, and the people facts (origin
    ``news_fact`` unless the fact came from an uploaded file: ``import``)."""
    out: list[PersonRecord] = []
    rows = (
        await s.execute(
            text(
                "SELECT to_jsonb(p) AS r FROM people p WHERE p.account_id = :a"
                " AND p.merged_into IS NULL AND p.erased_at IS NULL ORDER BY p.created_at, p.id"
            ),
            {"a": account_id},
        )
    ).all()
    for row in rows:
        r = dict(row.r)
        cls = r.get("classification_json") or {}
        out.append(
            PersonRecord(
                key=_text(r.get("person_key")) or _text(r.get("id")),
                name=_text(r.get("name")),
                title=_text(r.get("title")),
                headline=_text(r.get("headline")),
                level=_text(r.get("job_level")),
                lanes=tuple(_text(x) for x in (r.get("lanes") or ()) if x),
                priority=_text(r.get("priority")),
                profile_url=_text(r.get("profile_url_canonical") or r.get("profile_url")),
                location=_text(r.get("location")),
                employer_domain=_text(r.get("employer_domain")),
                origin="people_search" if _text(r.get("origin")) == "people_search" else "profile",
                found_by=_text(r.get("origin") or cls.get("found_by")),
                former_si=_text(cls.get("former_si") or cls.get("si_employers")),
                last_verified=_day(r.get("created_at")),
                note=_text(r.get("scope_reason")),
                excluded=bool(r.get("excluded")),
                internal_only=bool(r.get("internal_only")),
            )
        )
    facts = (
        await s.execute(
            text(
                "SELECT f.id::text AS id, coalesce(f.value_text, f.value_json->>'text') AS name,"
                " f.value_json AS v, f.created_at,"
                " EXISTS (SELECT 1 FROM fact_evidence x WHERE x.fact_id = f.id AND"
                "  x.document_id IS NOT NULL) AS uploaded FROM facts f WHERE f.account_id = :a"
                " AND f.status = 'current' AND f.attribute_key LIKE 'people.%'"
                " AND split_part(f.attribute_key, '.', 3) <> 'company'"
            ),
            {"a": account_id},
        )
    ).all()
    for f in facts:
        v = f.v if isinstance(f.v, dict) else {}
        out.append(
            PersonRecord(
                key="fact:" + f.id,
                name=_text(f.name),
                title=_text(v.get("title")),
                lanes=(_text(v.get("lane")),) if v.get("lane") else (),
                priority=_text(v.get("priority")),
                profile_url=_text(v.get("profile_url") or v.get("link")),
                origin="import" if f.uploaded else "news_fact",
                last_verified=_day(f.created_at),
            )
        )
    return out


async def read_leadership(s: Any, account_id: UUID) -> list[LeadershipRecord]:
    """Leadership facts (appointments, retirements) with their newest evidence."""
    rows = (
        await s.execute(
            text(
                "SELECT coalesce(f.value_text, f.value_json->>'text') AS what, f.value_json AS v,"
                " e.url, e.evidence_date, e.source_tier FROM facts f LEFT JOIN LATERAL ("
                " SELECT url, evidence_date, source_tier FROM fact_evidence x WHERE"
                " x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON true"
                " WHERE f.account_id = :a AND f.status = 'current' AND f.visibility = 'public'"
                " AND (f.attribute_key LIKE 'leadership.%' OR f.attribute_key LIKE"
                " '%.leadership_changes%')"
            ),
            {"a": account_id},
        )
    ).all()
    out: list[LeadershipRecord] = []
    for r in rows:
        v = r.v if isinstance(r.v, dict) else {}
        name = _text(v.get("name") or v.get("person"))
        if not name:
            continue  # the builder's name check decides about a phrase; no name field, no row
        out.append(
            LeadershipRecord(
                name=name,
                role=_text(v.get("role") or v.get("title") or r.what),
                start=_day(v.get("start")) or _day(r.evidence_date),
                end=_day(v.get("end")),
                source_url=_text(r.url),
                source_type=_text(r.source_tier),
            )
        )
    return out


async def read_tech(s: Any, account_id: UUID) -> list[TechRecord]:
    """Technology detections and technology facts."""
    out: list[TechRecord] = []
    rows = (
        await s.execute(
            text(
                "SELECT t.technology, t.source_count, t.first_seen_at, t.last_seen_at,"
                " t.cross_check_result,"
                # source_ref is the page's address (the pipeline stores the address) or its S-code
                " CASE WHEN t.source_ref ~* '^https?://' THEN t.source_ref ELSE"
                "  (SELECT s.url FROM sources s WHERE s.account_id = t.account_id AND"
                "   s.source_code = t.source_ref LIMIT 1) END AS url"
                " FROM tech_detections t WHERE t.account_id = :a ORDER BY t.created_at, t.id"
            ),
            {"a": account_id},
        )
    ).all()
    for r in rows:
        out.append(
            TechRecord(
                technology=_text(r.technology),
                detections=int(r.source_count or 0),
                first_seen=_text(r.first_seen_at),
                last_seen=_text(r.last_seen_at),
                source_url=_text(r.url),
                detected_domain=host_of(_text(r.url)),
                cross_check=_text(r.cross_check_result),
            )
        )
    facts = (
        await s.execute(
            text(
                "SELECT coalesce(f.value_text, f.value_json->>'text') AS tech, e.url,"
                " e.evidence_date FROM facts f LEFT JOIN LATERAL (SELECT url, evidence_date FROM"
                " fact_evidence x WHERE x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST"
                " LIMIT 1) e ON true WHERE f.account_id = :a AND f.status = 'current' AND"
                " f.visibility = 'public' AND f.attribute_key LIKE 'tech.%'"
            ),
            {"a": account_id},
        )
    ).all()
    for r in facts:
        out.append(
            TechRecord(
                technology=_text(r.tech),
                detections=0,
                first_seen=_text(_day(r.evidence_date) or ""),
                last_seen=_text(_day(r.evidence_date) or ""),
                source_url=_text(r.url),
                origin="fact",
                detected_domain=host_of(_text(r.url)),
            )
        )
    return out


async def read_articles(s: Any, account_id: UUID) -> list[ArticleRecord]:
    """News facts grouped into articles by canonical URL, with the page title, the date the
    source gave (its register row, else the URL) and when the page was collected."""
    archive = await _archive_titles(s)
    by_hash = titled("la.url_hash = s.url_hash") if archive else "NULL"
    rows = (
        await s.execute(
            text(
                "SELECT f.id::text AS fid, coalesce(f.value_text, f.value_json->>'text') AS fact,"
                " f.product_grade AS grade, f.superseded_by::text AS sup, f.lane,"
                " f.attribute_key, e.url, e.supporting_sentence AS quote, e.evidence_date,"
                " e.date_kind, e.source_tier, e.created_at AS collected,"
                f" coalesce(nullif(s.title, ''), {by_hash})"
                " AS title, s.publisher,"
                " s.published_at, s.source_code, s.kind"
                " FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
                " LEFT JOIN sources s ON s.id = e.source_id"
                " WHERE f.account_id = :a AND f.status = 'current' AND f.visibility = 'public'"
                " AND (f.attribute_key LIKE 'news.%' OR split_part(f.attribute_key, '.', 2) IN"
                " ('company_news', 'leadership_changes', 'competitor_moves',"
                " 'regulatory_quality_events', 'events_sponsored')) AND e.url IS NOT NULL"
                " ORDER BY e.url, f.id"
            ),
            {"a": account_id},
        )
    ).all()
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = canonical_url(r.url)
        g = grouped.setdefault(
            key,
            {"url": r.url, "title": "", "published": None, "kind": "", "collected": None,
             "publisher": "", "tier": "", "code": "", "facts": {}},
        )  # fmt: skip
        g["title"] = g["title"] or _text(r.title)
        g["publisher"] = g["publisher"] or _text(r.publisher)
        g["tier"] = g["tier"] or _text(r.source_tier)
        g["code"] = g["code"] or _text(r.source_code)
        if r.collected and not g["collected"]:
            g["collected"] = r.collected
        if not g["published"]:
            real = _day(r.published_at)
            if real:
                g["published"], g["kind"] = real, "page"
            elif _text(r.date_kind) in ("published", "filing", "filed") and r.evidence_date:
                g["published"], g["kind"] = _day(r.evidence_date), "filing"
            else:
                guess = date_from_url(r.url)
                if guess:
                    g["published"], g["kind"] = guess, "url"
        g["facts"].setdefault(
            r.fid,
            FactLine(
                fact_id=r.fid,
                text=_text(r.fact),
                grade=_text(r.grade),
                quote=_text(r.quote),
                attribute_key=_text(r.attribute_key),
                lane=_text(r.lane),
                superseded_by_fact=_text(r.sup),
            ),
        )
    out = []
    for g in grouped.values():
        if not g["published"] and g["title"]:
            guess = date_from_text(g["title"])
            if guess:
                g["published"], g["kind"] = guess, "page"
        out.append(
            ArticleRecord(
                url=g["url"],
                title=g["title"],
                published=g["published"],
                published_kind=g["kind"],
                collected=g["collected"],
                publisher=g["publisher"],
                tier=g["tier"],
                source_code=g["code"],
                facts=tuple(g["facts"].values()),
            )
        )
    return out


async def _archive_titles(s: Any) -> bool:
    """Whether the link archive exists in this database (a database not yet migrated has none)."""
    return bool(await s.scalar(text("SELECT to_regclass('public.link_archive') IS NOT NULL")))


# the address a page-read call was made for
_READ_URL = "t.response->'args'->>'url'"

# a page's title as the link archive captured it when the page was read (never a profile copy,
# which holds a person's name); the fallback for a source stored before it had a title
_ARCHIVED_TITLE = (
    "(SELECT la.title FROM link_archive la WHERE la.account_id = :a AND {match}"
    " AND la.kind <> 'profile' AND coalesce(la.title, '') <> ''"
    " ORDER BY la.archived_at DESC LIMIT 1)"
)


def titled(match: str) -> str:
    """The archived-title subquery for rows matched by ``match`` (see ``_ARCHIVED_TITLE``)."""
    return _ARCHIVED_TITLE.format(match=match)


async def read_sources(s: Any, account_id: UUID) -> list[SourceRecord]:
    """The sources register, the pages a run read that were never graded (code ``Read``) and
    where each coded source is cited (``used_in``: the fact attribute it supports)."""
    out: list[SourceRecord] = []
    archive = await _archive_titles(s)
    by_hash = titled("la.url_hash = s.url_hash") if archive else "NULL"
    rows = (
        await s.execute(
            text(
                "SELECT s.source_code, s.url,"
                f" coalesce(nullif(s.title, ''), {by_hash})"
                " AS title, s.publisher, s.published_at, s.kind,"
                " s.created_at, coalesce((SELECT array_agg(DISTINCT f.attribute_key) FROM"
                " fact_evidence e JOIN facts f ON f.id = e.fact_id WHERE e.source_id = s.id"
                " AND f.status = 'current'), '{}') AS used"
                " FROM sources s WHERE s.account_id = :a ORDER BY s.source_code"
            ),
            {"a": account_id},
        )
    ).all()
    for r in rows:
        out.append(
            SourceRecord(
                code=_text(r.source_code),
                url=_text(r.url),
                title=_text(r.title),
                publisher=_text(r.publisher),
                published=_day(r.published_at),
                kind=_text(r.kind),
                collected=r.created_at,
                used_in=tuple(f"Research findings > {k}" for k in (r.used or ())),
            )
        )
    reads = (
        await s.execute(
            text(
                "SELECT DISTINCT ON (t.response->'args'->>'url') t.response->'args'->>'url' AS url,"
                f" {titled(f'la.url = {_READ_URL}') if archive else 'NULL'} AS title,"
                " t.created_at FROM tool_calls t JOIN account_runs r ON r.id = t.run_id"
                " WHERE r.account_id = :a AND t.tool IN ('p_search:read_page',"
                " 'p_scrape:scrape_job_page') AND t.response->'args'->>'url' IS NOT NULL"
                " AND NOT EXISTS (SELECT 1 FROM sources x WHERE x.account_id = :a AND"
                " x.url = t.response->'args'->>'url')"
                " ORDER BY t.response->'args'->>'url', t.created_at"
            ),
            {"a": account_id},
        )
    ).all()
    for r in reads:
        out.append(
            SourceRecord(
                code="Read",
                url=_text(r.url),
                title=_text(r.title),
                collected=r.created_at,
                read_only=True,
            )
        )
    return out


async def read_searches(s: Any, account_id: UUID) -> list[SearchCall]:
    """Every web search call of the account's runs, one record per call (the builder folds the
    repeats of one query logged once per provider)."""
    rows = (
        await s.execute(
            text(
                "SELECT t.created_at, t.provider_code,"
                " coalesce((SELECT string_agg(q, ' | ') FROM jsonb_array_elements_text("
                "  CASE WHEN jsonb_typeof(t.response->'args'->'queries') = 'array'"
                "  THEN t.response->'args'->'queries' ELSE '[]'::jsonb END) q),"
                "  t.response->'args'->>'objective', '') AS query,"
                " coalesce((t.response->>'record_count')::int, 0) AS pages,"
                " coalesce((SELECT array_agg(DISTINCT regexp_replace(p->>'url',"
                "  '^https?://(www[.])?([^/:]+).*$', '\\2')) FROM jsonb_array_elements("
                "  CASE WHEN jsonb_typeof(t.response->'pages') = 'array'"
                "  THEN t.response->'pages' ELSE '[]'::jsonb END) p), '{}') AS sites"
                " FROM tool_calls t JOIN account_runs r ON r.id = t.run_id"
                " WHERE r.account_id = :a AND t.tool = 'p_search:web_search'"
                " ORDER BY t.created_at"
            ),
            {"a": account_id},
        )
    ).all()
    out: list[SearchCall] = []
    for r in rows:
        for query in (q.strip() for q in _text(r.query).split(" | ")):
            if query:
                out.append(
                    SearchCall(
                        query=query,
                        provider=_text(r.provider_code),
                        run_at=r.created_at,
                        pages=int(r.pages or 0),
                        sites=tuple(x for x in (r.sites or ()) if x),
                    )
                )
    return out


async def read_signals(s: Any, account_id: UUID) -> list[SignalSeed]:
    """The signals the pipeline stored, with the first source of their fact."""
    rows = (
        await s.execute(
            text(
                "SELECT g.title, g.signal_type, g.grade, g.detected_at,"
                " (SELECT array_agg(DISTINCT e.url) FROM fact_evidence e WHERE e.fact_id ="
                " g.fact_id AND e.url IS NOT NULL) AS urls,"
                " (SELECT max(e.evidence_date) FROM fact_evidence e WHERE e.fact_id = g.fact_id)"
                " AS event FROM signal_events g WHERE g.account_id = :a AND NOT g.sensitive"
                " ORDER BY g.detected_at"
            ),
            {"a": account_id},
        )
    ).all()
    return [
        SignalSeed(
            title=_text(r.title),
            signal_type=_text(r.signal_type),
            grade=_text(r.grade),
            detected=_day(r.detected_at),
            event_date=_day(r.event),
            source_urls=tuple(r.urls or ()),
        )
        for r in rows
    ]


async def read_coverage(
    s: Any, account_id: UUID, labels: Mapping[str, Any]
) -> list[CoverageRecord]:
    """The tracker of the account's latest finished run. ``labels`` is the tracker
    configuration (``label``, ``reason``, ``order`` by row key and reason code)."""
    rows = (
        await s.execute(
            text(
                "SELECT c.row_key, c.priority, c.status, c.reason_code, c.last_checked_at,"
                " c.proof_calls,"
                # the facts the item produced (row key = the second part of their attribute key)
                " (SELECT count(*) FROM facts f WHERE f.account_id = :a AND f.status = 'current'"
                "  AND split_part(f.attribute_key, '.', 2) = c.row_key) AS facts,"
                " coalesce((SELECT array_agg(DISTINCT s.source_code) FROM facts f"
                "  JOIN fact_evidence e ON e.fact_id = f.id JOIN sources s ON s.id = e.source_id"
                "  WHERE f.account_id = :a AND f.status = 'current' AND"
                "  split_part(f.attribute_key, '.', 2) = c.row_key AND s.source_code IS NOT NULL),"
                "  '{}') AS codes"
                " FROM coverage_rows c WHERE c.run_id = (SELECT r.id FROM"
                " account_runs r WHERE r.account_id = :a AND r.status IN ('COMPLETED',"
                " 'PARTIAL', 'BUDGET_EXCEEDED') AND EXISTS (SELECT 1 FROM coverage_rows x"
                " WHERE x.run_id = r.id) ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)"
            ),
            {"a": account_id},
        )
    ).all()
    names = {"done": "Done", "partial": "Partial", "not_done": "Not done", "blocked": "Blocked",
             "none": "None found"}  # fmt: skip
    order = labels.get("order") or {}
    out = [
        CoverageRecord(
            row_key=r.row_key,
            item=_text((labels.get("label") or {}).get(r.row_key)) or r.row_key,
            number=int(order.get(r.row_key) or 0),
            priority=_text(r.priority).title(),
            status=names.get(_text(r.status), "Not checked"),
            reason=_text((labels.get("reason") or {}).get(r.reason_code)),
            last_run=_day(r.last_checked_at),
            evidence_rows=int(r.proof_calls or 0) + int(r.facts or 0),
            source_codes=tuple(sorted(r.codes or ())),
        )
        for r in rows
    ]
    out.sort(key=lambda c: (c.number or 999, c.row_key))
    return [
        CoverageRecord(**{**c.__dict__, "number": c.number or i})
        for i, c in enumerate(out, start=1)
    ]


async def read_mail(s: Any, account_id: UUID, user_id: str) -> list[MailItem]:
    """The user's own matched mail threads and drive files for the account."""
    rows = (
        await s.execute(
            text(
                "SELECT i.title, i.item_kind, i.location, i.item_date, i.snippet"
                " FROM connector_items i WHERE i.account_id = :a AND i.owner_user_id = :u"
                " AND i.state = 'matched' ORDER BY i.item_date DESC NULLS LAST"
            ),
            {"a": account_id, "u": user_id},
        )
    ).all()
    return [
        MailItem(
            title=_text(r.title),
            kind="File" if r.item_kind == "file" else "Thread",
            sender=_text(r.location),
            date=_day(r.item_date),
            summary=_text(r.snippet),
        )
        for r in rows
    ]


async def load_input(
    s: Any,
    profile: WorkspaceProfile,
    account_id: UUID,
    user_id: str,
    *,
    payload: PayloadGet | None,
    tracker_labels: Mapping[str, Any],
) -> RepairInput:
    """Every record of the workspace (see the module docstring). Sequential on one session: the
    queries share a transaction."""
    inp = RepairInput()
    inp.jobs = await read_jobs(s, account_id, payload)
    inp.people = await read_people(s, account_id)
    inp.leadership = await read_leadership(s, account_id)
    inp.tech = await read_tech(s, account_id)
    inp.articles = await read_articles(s, account_id)
    inp.sources = await read_sources(s, account_id)
    inp.searches = await read_searches(s, account_id)
    inp.signal_seeds = await read_signals(s, account_id)
    inp.coverage = await read_coverage(s, account_id, tracker_labels)
    if profile.mail_drive_connected:
        inp.mail = await read_mail(s, account_id, user_id)
    return inp
