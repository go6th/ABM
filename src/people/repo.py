"""``PeopleRepo`` on Postgres through ``tenant_txn`` (spec 6, migration 0014).

Writes:

* ``save_page``: the raw page goes to the payload store under a neutral key; a row in
  ``person_search_pages`` records the cursor (idempotent on run, source and page).
* ``save_profiles`` / ``save_posts``: append-only, one transaction per batch or person.
* ``save_run``: in ONE tenant transaction, every person row (canonical rows first, merged rows
  pointing at them by id), one ``person_sources`` row per record, a merge or scope event per
  person, and the run record. The run record holds no name, title or text; claims and Findings
  go to the payload store by reference.

``load_run`` rebuilds the full ``PipelineResult`` from the tables, so a re-entered stage makes no
paid call. ``unmerge`` and ``erase`` are the only updates; the triggers of 0014 refuse anything
else, and no DELETE is ever issued (no grant allows one).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence import archive as LA
from account_intelligence.grading.schema import ProposedSourced
from people.merge import MergeEvent
from people.models import (
    Classification,
    Employment,
    Finding,
    PartialDate,
    Person,
    PersonPage,
    Post,
    Profile,
)
from people.pipeline import PipelineResult, RowEvidence

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
KeyFor = Callable[[str, str], str]


@dataclass(frozen=True)
class StoredPage:
    """A search page stored for a run."""

    source_id: str
    page_no: int
    cursor_in: str | None
    page: PersonPage


def _employments(raw: Any) -> tuple[Employment, ...] | None:
    """The stored dated experience entries (``None`` when the source gave none)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    return tuple(
        Employment(
            employer=str(x["employer"]),
            start=PartialDate.parse(x.get("start")),
            end=PartialDate.parse(x.get("end")),
        )
        for x in raw
    )


def _employments_json(value: Sequence[Employment] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        [
            {
                "employer": e.employer,
                "start": e.start.iso() if e.start else None,
                "end": e.end.iso() if e.end else None,
            }
            for e in value
        ]
    )


def _iso(value: PartialDate | None) -> tuple[str | None, str | None]:
    return (value.iso(), value.precision) if value is not None else (None, None)


class PgPeopleRepo:
    """Persistence for one tenant and account."""

    def __init__(
        self, txn: TxnFactory, tenant_id: UUID, account_id: UUID, payloads: Any, key_for: KeyFor
    ) -> None:
        self.txn = txn
        self.tenant_id = tenant_id
        self.account_id = account_id
        self.payloads = payloads
        self.key_for = key_for

    # ---- search pages -----------------------------------------------------------------------

    async def load_pages(self, run_id: str) -> list[StoredPage]:
        """Pages stored for the run, in (source, page) order, from the payload store."""
        async with self.txn(self.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT source_id, page_no, cursor_in, page_ref FROM person_search_pages"
                        " WHERE run_id = :r ORDER BY source_id, page_no"
                    ),
                    {"r": UUID(run_id)},
                )
            ).all()
        pages = []
        for row in rows:
            body = await self.payloads.get(row.page_ref)
            pages.append(
                StoredPage(
                    row.source_id, row.page_no, row.cursor_in, PersonPage.model_validate_json(body)
                )
            )
        return pages

    async def save_page(
        self, run_id: str, source_id: str, page_no: int, cursor_in: str | None, page: PersonPage
    ) -> None:
        """Store the raw page (payload store) and its cursor row (idempotent)."""
        name = f"{source_id.replace(':', '-')}-{page_no}.json"
        ref = await self.payloads.put(
            self.key_for("people_pages", name), page.model_dump_json().encode()
        )
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO person_search_pages (tenant_id, account_id, run_id, source_id,"
                    " page_no, cursor_in, next_cursor, page_ref, call_ref, record_count, complete)"
                    " VALUES (:t, :a, :r, :src, :no, :cin, :cout, :ref, :call, :n, :complete)"
                    " ON CONFLICT (tenant_id, run_id, source_id, page_no) DO NOTHING"
                ),
                {
                    "t": self.tenant_id,
                    "a": self.account_id,
                    "r": UUID(run_id),
                    "src": source_id,
                    "no": page_no,
                    "cin": cursor_in,
                    "cout": page.next_cursor,
                    "ref": ref,
                    "call": page.call_ref,
                    "n": len(page.records),
                    "complete": page.complete,
                },
            )

    # ---- profiles and posts -------------------------------------------------------------------

    @staticmethod
    def _profile(row: Any) -> Profile:
        return Profile(
            person_key=row.person_key,
            profile_url=row.profile_url_canonical,
            name=row.name,
            current_title=row.current_title,
            headline=row.headline,
            employer=row.employer,
            prior_employers=tuple(row.prior_employers) if row.prior_employers is not None else None,
            employments=_employments(row.employments),
            join_date=PartialDate.parse(row.join_date),
            role_start=PartialDate.parse(row.role_start),
            certifications=tuple(row.certifications) if row.certifications is not None else None,
            location=row.location,
            own_description=row.own_description,
            own_description_sha256=row.own_description_sha256,
            last_activity_at=row.last_activity_at,
            provider_code=row.provider_code,
            raw_ref=row.raw_ref,
            call_ref=row.call_ref,
            fields_dropped=row.fields_dropped,
            internal_only=row.internal_only,
            partial=row.partial,
            hostile=row.hostile,
        )

    async def stored_profiles(self, profile_urls: Sequence[str]) -> dict[str, Profile]:
        """The latest profile per canonical URL pulled for this account in any run (erased
        profiles included: an erased person is never pulled again)."""
        urls = [u for u in profile_urls if u]
        if not urls:
            return {}
        async with self.txn(self.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT DISTINCT ON (profile_url_canonical) * FROM person_profiles"
                        " WHERE account_id = :a AND profile_url_canonical = ANY(:u)"
                        " ORDER BY profile_url_canonical, created_at DESC, id DESC"
                    ),
                    {"a": self.account_id, "u": urls},
                )
            ).all()
        return {row.profile_url_canonical: self._profile(row) for row in rows}

    async def save_profiles(self, run_id: str, profiles: Sequence[Profile]) -> None:
        """Insert a batch of profiles (one transaction; a replayed batch is ignored)."""
        async with self.txn(self.tenant_id) as s:
            for p in profiles:
                join, join_p = _iso(p.join_date)
                role, role_p = _iso(p.role_start)
                await s.execute(
                    text(
                        "INSERT INTO person_profiles (tenant_id, account_id, run_id,"
                        " profile_url_canonical, person_key, provider_code, raw_ref, call_ref,"
                        " name, current_title, headline, employer, prior_employers, employments,"
                        " join_date, join_precision, role_start, role_precision, certifications,"
                        " certifications_provided, location, own_description,"
                        " own_description_sha256, last_activity_at, fields_dropped,"
                        " internal_only, partial, hostile) VALUES (:t, :a, :r, :url, :key,"
                        " :provider, :raw, :call, :name, :title, :headline, :employer, :prior,"
                        " CAST(:employments AS jsonb), :join, :join_p, :role, :role_p, :certs,"
                        " :certs_provided, :location,"
                        " :desc, :sha, :activity, :dropped, :internal, :partial, :hostile)"
                        " ON CONFLICT (tenant_id, run_id, profile_url_canonical) DO NOTHING"
                    ),
                    {
                        "t": self.tenant_id,
                        "a": self.account_id,
                        "r": UUID(run_id),
                        "url": p.profile_url,
                        "key": p.person_key,
                        "provider": p.provider_code,
                        "raw": p.raw_ref,
                        "call": p.call_ref,
                        "name": p.name,
                        "title": p.current_title,
                        "headline": p.headline,
                        "employer": p.employer,
                        "prior": list(p.prior_employers) if p.prior_employers is not None else None,
                        "employments": _employments_json(p.employments),
                        "join": join,
                        "join_p": join_p,
                        "role": role,
                        "role_p": role_p,
                        "certs": list(p.certifications) if p.certifications is not None else None,
                        "certs_provided": p.certifications is not None,
                        "location": p.location,
                        "desc": p.own_description,
                        "sha": p.own_description_sha256,
                        "activity": p.last_activity_at,
                        "dropped": p.fields_dropped,
                        "internal": p.internal_only,
                        "partial": p.partial,
                        "hostile": p.hostile,
                    },
                )

    async def stored_posts(self, run_id: str) -> dict[str, tuple[Post, ...]]:
        """Posts pulled in this run, by canonical profile URL (a pull with no post included)."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            pulls = (
                await s.execute(
                    text("SELECT profile_url_canonical FROM person_post_pulls WHERE run_id = :r"),
                    {"r": run},
                )
            ).all()
            rows = (
                await s.execute(
                    text("SELECT * FROM person_posts WHERE run_id = :r ORDER BY id"), {"r": run}
                )
            ).all()
        out: dict[str, list[Post]] = {row.profile_url_canonical: [] for row in pulls}
        for row in rows:
            out.setdefault(row.profile_url_canonical, []).append(self._post(row, ""))
        return {url: tuple(posts) for url, posts in out.items()}

    @staticmethod
    def _post(row: Any, person_key: str) -> Post:
        return Post(
            person_key=person_key,
            post_url=row.post_url,
            posted_at=row.posted_at,
            excerpt=row.excerpt,
            excerpt_sha256=row.excerpt_sha256,
            raw_ref=row.raw_ref,
            call_ref=row.call_ref,
            internal_only=row.internal_only,
            hostile=row.hostile,
        )

    async def save_posts(self, run_id: str, profile_url: str, posts: Sequence[Post]) -> None:
        """Insert one person's posts and the pull row, in one transaction."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            for p in posts:
                await s.execute(
                    text(
                        "INSERT INTO person_posts (tenant_id, account_id, run_id,"
                        " profile_url_canonical, post_url, posted_at, excerpt, excerpt_sha256,"
                        " raw_ref, call_ref, internal_only, hostile) VALUES (:t, :a, :r, :url,"
                        " :post, :at, :excerpt, :sha, :raw, :call, :internal, :hostile)"
                        " ON CONFLICT (tenant_id, run_id, profile_url_canonical, post_url)"
                        " DO NOTHING"
                    ),
                    {
                        "t": self.tenant_id,
                        "a": self.account_id,
                        "r": run,
                        "url": profile_url,
                        "post": p.post_url,
                        "at": p.posted_at,
                        "excerpt": p.excerpt,
                        "sha": p.excerpt_sha256,
                        "raw": p.raw_ref,
                        "call": p.call_ref,
                        "internal": p.internal_only,
                        "hostile": p.hostile,
                    },
                )
            await s.execute(
                text(
                    "INSERT INTO person_post_pulls (tenant_id, account_id, run_id,"
                    " profile_url_canonical, post_count) VALUES (:t, :a, :r, :url, :n)"
                    " ON CONFLICT (tenant_id, run_id, profile_url_canonical) DO NOTHING"
                ),
                {
                    "t": self.tenant_id,
                    "a": self.account_id,
                    "r": run,
                    "url": profile_url,
                    "n": len(posts),
                },
            )

    # ---- the run ------------------------------------------------------------------------------

    async def _archive_entries(self, people: Sequence[Person]) -> list[LA.ArchiveEntry]:
        """The archived copy of every person's profile page: the record the source returned
        (name, title, headline, location), stored as a small body so the People List row can
        open what was captured once the live profile link dies (never erased people, never
        internal-only rows)."""
        out: list[LA.ArchiveEntry] = []
        now = datetime.now(UTC)
        for p in people:
            url = p.profile_url_raw or p.profile_url
            if not url or p.internal_only or LA.normalized(url) is None:
                continue
            lines = [p.name, p.title, p.headline, p.location]
            body_text = "\n".join(x for x in lines if x)
            if not body_text:
                continue
            body = LA.archive_body(
                url, body_text, title=p.name, captured_at=now, kind="profile", source_ref=p.raw_ref
            )
            key = LA.archive_key(self.tenant_id, self.account_id, body)
            ref = await self.payloads.put(key, body)
            out.append(
                LA.ArchiveEntry(
                    url=url,
                    kind="profile",
                    payload_ref=ref,
                    archived_at=now,
                    title=p.name,
                    source_ref=p.raw_ref,
                )
            )
        return out

    async def _insert_person(
        self, s: AsyncSession, run: UUID, p: Person, into: UUID | None
    ) -> UUID:
        c = p.classification
        row = await s.execute(
            text(
                "INSERT INTO people (tenant_id, account_id, run_id, profile_url,"
                " provider_person_id,"
                " name, title, job_level, source_ref, excluded, excluded_reason, person_key,"
                " source_record_id, origin, headline, location, employer_domain,"
                " profile_url_canonical, scope_status, scope_reason, rule_id,"
                " classification_json, lanes, priority, merged_into, possible_duplicate,"
                " internal_only, profile_state, profile_failure, posts_requested,"
                " activity_unfiltered, fields_dropped) VALUES (:t, :a, :r, :url, :pid, :name,"
                " :title, :level, :sref, :excluded, :excluded_reason, :key, :rid, :origin,"
                " :headline, :location, :employer, :canonical, :status, :reason, :rule,"
                " CAST(:cls AS jsonb), :lanes, :priority, :into, :dup, :internal, :pstate,"
                " :pfail, :posts, :unfiltered, :dropped) RETURNING id"
            ),
            {
                "t": self.tenant_id,
                "a": self.account_id,
                "r": run,
                "url": p.profile_url if into is None else None,
                "pid": p.source_record_id,
                "name": p.name,
                "title": p.title,
                "level": c.level,
                "sref": p.raw_ref or p.source_record_id,
                "excluded": c.status == "excluded",
                "excluded_reason": c.reason if c.status == "excluded" else None,
                "key": p.person_key,
                "rid": p.source_record_id,
                "origin": p.origin,
                "headline": p.headline,
                "location": p.location,
                "employer": p.employer_domain,
                "canonical": p.profile_url,
                "status": c.status,
                "reason": c.reason,
                "rule": c.rule_id,
                "cls": c.model_dump_json(),
                "lanes": list(c.lanes),
                "priority": c.priority,
                "into": into,
                "dup": p.possible_duplicate,
                "internal": p.internal_only,
                "pstate": p.profile_state,
                "pfail": p.profile_failure,
                "posts": p.posts_requested,
                "unfiltered": p.activity_unfiltered,
                "dropped": p.fields_dropped,
            },
        )
        new_id: UUID = row.scalar_one()
        await s.execute(
            text(
                "INSERT INTO person_sources (tenant_id, account_id, run_id, person_key,"
                " source_record_id, origin, provider_code, raw_ref, call_ref, lane, pass_no, query,"
                " profile_url_raw, mention_url, mention_sentence, fields_dropped) VALUES (:t, :a,"
                " :r, :key, :rid, :origin, :provider, :raw, :call, :lane, :pass, :query, :url,"
                " :murl, :msentence, :dropped)"
                " ON CONFLICT (tenant_id, run_id, source_record_id) DO NOTHING"
            ),
            {
                "t": self.tenant_id,
                "a": self.account_id,
                "r": run,
                "key": p.person_key,
                "rid": p.source_record_id,
                "origin": p.origin,
                "provider": p.provider_code,
                "raw": p.raw_ref,
                "call": p.call_ref,
                "lane": p.lane_searched,
                "pass": p.pass_no,
                "query": p.query,
                "url": p.profile_url_raw,
                "murl": p.mention_url,
                "msentence": p.mention_sentence,
                "dropped": p.fields_dropped,
            },
        )
        return new_id

    async def _event(
        self,
        s: AsyncSession,
        run: UUID,
        key: str,
        kind: str,
        into: str | None,
        rule: str,
        actor: str = "pipeline",
        detail: str | None = None,
    ) -> None:
        await s.execute(
            text(
                "INSERT INTO person_events (tenant_id, account_id, run_id, person_key, kind,"
                " into_key, rule_id, actor, detail) VALUES (:t, :a, :r, :key, :kind, :into,"
                " :rule, :actor, :detail)"
            ),
            {
                "t": self.tenant_id,
                "a": self.account_id,
                "r": run,
                "key": key,
                "kind": kind,
                "into": into,
                "rule": rule,
                "actor": actor,
                "detail": detail,
            },
        )

    async def save_run(self, run_id: str, record: PipelineResult) -> None:
        """Persist people, sources, events, claims (payload store) and the run record."""
        run = UUID(run_id)
        claims_body = json.dumps(
            {
                "claims": [c.model_dump(mode="json") for c in record.claims],
                "findings": [f.model_dump(mode="json") for f in record.findings],
            },
            sort_keys=True,
        ).encode()
        claims_ref = await self.payloads.put(self.key_for("people", "claims.json"), claims_body)
        merge_rule = {e.key: e.rule_id for e in record.merge_events}
        archived = await self._archive_entries(record.people)
        async with self.txn(self.tenant_id) as s:
            ids: dict[str, UUID] = {}
            ordered = sorted(record.people, key=lambda p: (p.merged_into is not None, p.person_key))
            for p in ordered:
                into = ids[p.merged_into] if p.merged_into is not None else None
                ids[p.person_key] = await self._insert_person(s, run, p, into)
                if p.merged_into is not None:
                    rule = merge_rule.get(p.person_key, "merge")
                    await self._event(s, run, p.person_key, "merge", p.merged_into, rule)
                else:
                    await self._event(
                        s, run, p.person_key, "scope", None, p.classification.rule_id or "scope"
                    )
            await LA.record(s, self.tenant_id, self.account_id, run, archived)
            await s.execute(
                text(
                    "INSERT INTO people_pipeline_runs (run_id, tenant_id, account_id, as_of,"
                    " config_hash, record_json, claims_ref) VALUES (:r, :t, :a, :as_of, :hash,"
                    " CAST(:rec AS jsonb), :claims)"
                ),
                {
                    "r": run,
                    "t": self.tenant_id,
                    "a": self.account_id,
                    "as_of": record.as_of,
                    "hash": record.config_hash,
                    "rec": json.dumps(_record_json(record), sort_keys=True),
                    "claims": claims_ref,
                },
            )

    async def load_run(self, run_id: str) -> PipelineResult | None:
        """The stored result of the run, rebuilt from the tables, or None."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            head = (
                await s.execute(
                    text("SELECT * FROM people_pipeline_runs WHERE run_id = :r"), {"r": run}
                )
            ).first()
            if head is None:
                return None
            people = (
                await s.execute(
                    text(
                        "SELECT p.*, t.person_key AS into_key, src.provider_code, src.raw_ref,"
                        " src.call_ref, src.lane, src.pass_no, src.query, src.profile_url_raw,"
                        " src.mention_url, src.mention_sentence FROM people p"
                        " LEFT JOIN people t ON t.id = p.merged_into"
                        " LEFT JOIN person_sources src ON src.run_id = p.run_id"
                        " AND src.source_record_id = p.source_record_id"
                        " WHERE p.run_id = :r ORDER BY p.person_key"
                    ),
                    {"r": run},
                )
            ).all()
            profiles = (
                await s.execute(text("SELECT * FROM person_profiles WHERE run_id = :r"), {"r": run})
            ).all()
            posts = (
                await s.execute(
                    text("SELECT * FROM person_posts WHERE run_id = :r ORDER BY id"), {"r": run}
                )
            ).all()
        rebuilt = [self._person(row) for row in people]
        key_of_url = {
            p.profile_url: p.person_key for p in rebuilt if p.profile_url and p.merged_into is None
        }
        payload = json.loads(await self.payloads.get(head.claims_ref)) if head.claims_ref else {}
        result = _from_record(head.record_json, run_id, head.as_of, head.config_hash)
        reused = await self.stored_profiles(
            [p.profile_url or "" for p in rebuilt if p.profile_state == "reused"]
        )
        own = [self._profile(row) for row in profiles]
        for p in rebuilt:
            if p.profile_state == "reused" and p.profile_url in reused:
                own.append(reused[p.profile_url].model_copy(update={"person_key": p.person_key}))
        own = [
            pr.model_copy(update={"hostile": True})
            if pr.person_key in result.findings_hostile
            else pr
            for pr in own
        ]
        return replace(
            result,
            people=tuple(rebuilt),
            profiles=tuple(
                sorted({p.profile_url: p for p in own}.values(), key=lambda p: p.person_key)
            ),
            posts=tuple(
                sorted(
                    (
                        self._post(row, key_of_url.get(row.profile_url_canonical, ""))
                        for row in posts
                    ),
                    key=lambda post: (post.person_key, post.post_url),
                )
            ),
            findings=tuple(Finding.model_validate(f) for f in payload.get("findings", [])),
            claims=tuple(
                ProposedSourced.model_validate_json(json.dumps(c))
                for c in payload.get("claims", [])
            ),
        )

    @staticmethod
    def _person(row: Any) -> Person:
        return Person(
            person_key=row.person_key,
            source_record_id=row.source_record_id,
            origin=row.origin,
            name=row.name,
            title=row.title,
            headline=row.headline,
            profile_url=row.profile_url_canonical,
            profile_url_raw=row.profile_url_raw,
            employer_domain=row.employer_domain,
            location=row.location,
            provider_code=row.provider_code,
            raw_ref=row.raw_ref,
            call_ref=row.call_ref,
            lane_searched=row.lane,
            pass_no=row.pass_no,
            query=row.query,
            mention_url=row.mention_url,
            mention_sentence=row.mention_sentence,
            merged_into=row.into_key,
            possible_duplicate=row.possible_duplicate,
            internal_only=row.internal_only,
            classification=Classification.model_validate(row.classification_json),
            profile_state=row.profile_state,
            profile_failure=row.profile_failure,
            posts_requested=row.posts_requested,
            activity_unfiltered=row.activity_unfiltered,
            fields_dropped=row.fields_dropped,
        )

    # ---- the only updates: merge reversal and erasure -------------------------------------------

    async def unmerge(self, run_id: str, person_key: str, classification: Classification) -> None:
        """Reverse one merge (the row becomes canonical with the given status); an event records
        it. The 0014 guard refuses a pointer at a merged row or a chain."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE people SET merged_into = NULL, scope_status = :status,"
                    " scope_reason = :reason, rule_id = :rule, priority = :priority,"
                    " job_level = :level, lanes = :lanes, excluded = :excluded,"
                    " excluded_reason = :excluded_reason, classification_json = CAST(:cls AS jsonb)"
                    " WHERE run_id = :r AND person_key = :key"
                ),
                {
                    "status": classification.status,
                    "reason": classification.reason,
                    "rule": classification.rule_id,
                    "priority": classification.priority,
                    "level": classification.level,
                    "lanes": list(classification.lanes),
                    "excluded": classification.status == "excluded",
                    "excluded_reason": classification.reason
                    if classification.status == "excluded"
                    else None,
                    "cls": classification.model_dump_json(),
                    "r": run,
                    "key": person_key,
                },
            )
            await self._event(s, run, person_key, "unmerge", None, "unmerge.manual", "operator")

    async def erase(self, profile_url: str | None, person_keys: Sequence[str], reason: str) -> int:
        """Tombstone erasure across the account (not exposed through the API; D-23): nulls the
        personal fields of the people rows, sources, profiles and posts of one person, keeping keys,
        statuses and the merge chain. Returns the number of people rows erased."""
        now = datetime.now(UTC)
        async with self.txn(self.tenant_id) as s:
            keys = list(person_keys)
            if profile_url:
                found: list[str] = list(
                    (
                        await s.execute(
                            text(
                                "SELECT person_key FROM people WHERE account_id = :a"
                                " AND profile_url_canonical = :u"
                            ),
                            {"a": self.account_id, "u": profile_url},
                        )
                    ).scalars()
                )
                keys.extend(found)
            keys = sorted(set(keys))
            erased = (
                await s.execute(
                    text(
                        "UPDATE people SET name = NULL, title = NULL, headline = NULL,"
                        " location = NULL, erased_at = :now, erasure_reason = :why"
                        " WHERE account_id = :a AND person_key = ANY(:k) AND erased_at IS NULL"
                        " RETURNING run_id, person_key"
                    ),
                    {"a": self.account_id, "k": keys, "now": now, "why": reason},
                )
            ).all()
            await s.execute(
                text(
                    "UPDATE person_sources SET profile_url_raw = NULL, mention_sentence = NULL,"
                    " erased_at = :now, erasure_reason = :why WHERE account_id = :a"
                    " AND person_key = ANY(:k) AND erased_at IS NULL"
                ),
                {"a": self.account_id, "k": keys, "now": now, "why": reason},
            )
            if profile_url:
                await s.execute(
                    text(
                        "UPDATE person_profiles SET name = NULL, current_title = NULL,"
                        " headline = NULL, employer = NULL, prior_employers = NULL,"
                        " employments = NULL, join_date = NULL, join_precision = NULL,"
                        " role_start = NULL,"
                        " role_precision = NULL, certifications = NULL, location = NULL,"
                        " own_description = NULL, own_description_sha256 = NULL,"
                        " last_activity_at = NULL, erased_at = :now, erasure_reason = :why"
                        " WHERE account_id = :a AND profile_url_canonical = :u"
                        " AND erased_at IS NULL"
                    ),
                    {"a": self.account_id, "u": profile_url, "now": now, "why": reason},
                )
                await s.execute(
                    text(
                        "UPDATE person_posts SET excerpt = '', erased_at = :now,"
                        " erasure_reason = :why WHERE account_id = :a"
                        " AND profile_url_canonical = :u AND erased_at IS NULL"
                    ),
                    {"a": self.account_id, "u": profile_url, "now": now, "why": reason},
                )
            for row in erased:
                await self._event(s, row.run_id, row.person_key, "erase", None, "erase", "operator")
        return len(erased)


def _record_json(record: PipelineResult) -> dict[str, object]:
    """The non-personal part of the run record."""
    return {
        "counts": record.counts.model_dump(mode="json") if record.counts else None,
        "count_sentences": list(record.count_sentences),
        "invariant_error": record.invariant_error,
        "exit_reasons": dict(record.exit_reasons),
        "evidence": [
            {
                "row_key": e.row_key,
                "call_refs": list(e.call_refs),
                "expected_calls": e.expected_calls,
                "cap_hit": e.cap_hit,
                "not_attempted_reason": e.not_attempted_reason,
            }
            for e in record.evidence
        ],
        "merge_events": [e.__dict__ for e in record.merge_events],
        "lanes_not_searched": dict(record.lanes_not_searched),
        "source_codes": dict(record.source_codes),
        "drops": dict(record.drops),
        "flags": dict(record.flags),
        "findings_hostile": list(record.findings_hostile),
    }


def _from_record(
    data: dict[str, Any], run_id: str, as_of: date, config_hash: str
) -> PipelineResult:
    from people.models import PeopleCounts

    return PipelineResult(
        run_id=run_id,
        as_of=as_of,
        config_hash=config_hash,
        people=(),
        profiles=(),
        posts=(),
        findings=(),
        merge_events=tuple(MergeEvent(**e) for e in data["merge_events"]),
        counts=PeopleCounts.model_validate(data["counts"]) if data["counts"] else None,
        count_sentences=tuple(data["count_sentences"]),
        invariant_error=data["invariant_error"],
        exit_reasons=dict(data["exit_reasons"]),
        evidence=tuple(
            RowEvidence(
                e["row_key"],
                tuple(e["call_refs"]),
                e["expected_calls"],
                e["cap_hit"],
                e["not_attempted_reason"],
            )
            for e in data["evidence"]
        ),
        claims=(),
        lanes_not_searched=dict(data["lanes_not_searched"]),
        source_codes=dict(data["source_codes"]),
        drops=dict(data["drops"]),
        flags=dict(data["flags"]),
        findings_hostile=tuple(data["findings_hostile"]),
    )
