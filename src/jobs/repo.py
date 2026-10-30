"""``PostingRepo`` on Postgres through ``tenant_txn`` (spec 6).

``save_run`` writes, in ONE tenant transaction: each posting row, its platform sentences, its
classification, an observation per posting (insert-only source of first and last seen), and the
run-level record. ``save_pass`` writes a gap pass in one transaction: the sentences and
classifications of the postings it re-processed (a reclassification ADDS a row tagged with the
pass number) and the pass record. ``load_run`` returns the latest record (last pass, else the
first), so a re-entered stage makes no paid call (J14) and a resume continues after the last
stored pass. No UPDATE or DELETE is ever issued; the grants do not allow one. The current class of
a posting is its latest classification row; ``job_postings.posting_class`` keeps the first pass.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, time
from functools import partial
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.urls import clean_http_url, normalize_url, url_hash
from jobs import snapshot as SN
from jobs.export import is_exported
from jobs.models import Classification, PlatformSentence, Posting, PreviousPostings
from jobs.pipeline import PipelineResult, result_from_json, result_to_json

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]

# stored texts of one save read side by side (the payload store, no paid call)
_READ_CONCURRENCY = 16

_POSTING = (
    "INSERT INTO job_postings (tenant_id, account_id, run_id, posting_key, row_json, status,"
    " window_bucket, url, other_urls, title, location, posted_at, first_seen, status_source,"
    " text_ref, text_sha256, text_complete, full_text_read, posting_class, lanes, platforms,"
    " title_only, weak_key, undated, hostile, last_seen, seniority, new_since_previous)"
    " VALUES (:tenant, :account, :run, :key,"
    " CAST(:row AS jsonb), :status, :bucket, :url, :urls, :title, :location, :posted, :first,"
    " :status_source, :text_ref, :sha, :complete, :full, :class, :lanes, :platforms, :title_only,"
    " :weak, :undated, :hostile, :last_seen, :seniority, :new_since_previous)"
)


def _when(value: str) -> datetime:
    """Provider retrieval time as an aware datetime (UTC when the source gave no offset)."""
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _seen_at(p: Posting, as_of: datetime) -> datetime:
    """When a research saw a posting: its retrieval time, and for a posting carried from the
    previous research (not pulled again) at least the research date, so first and last seen
    advance with every research that still lists it (review 2026-10-29, finding 25)."""
    when = _when(p.retrieved_at)
    if p.new_since_previous is False and as_of > when:
        return as_of
    return when


def _hash_of(url: str) -> str:
    """The registry's dedupe key of a posting link (the hash of its normalised form)."""
    cleaned = clean_http_url(url)
    return url_hash(normalize_url(cleaned)) if cleaned else ""


async def register_posting_sources(
    s: AsyncSession,
    account_id: UUID,
    run_id: UUID,
    postings: Sequence[Posting],
    *,
    missing_only: bool = False,
) -> int:
    """Register the link of every posting the Job Postings file lists (``is_exported``) in the
    account's source registry, through the one writer of source codes
    (``get_or_create_source``): a posting the model did not cite in a graded fact still gets a
    stable ``S-nnnn`` code, so the file never drops a row for a missing code (e2e 2026-10-29,
    D-1). A link already registered keeps its code and only moves its last-seen reference. A
    link with no usable address is skipped (the file withholds that row and counts it).
    ``missing_only`` leaves a link the registry already holds untouched (a rebuild of an older
    run's files must not move a source's last-seen reference back). Returns how many links were
    registered or confirmed. Call it inside the caller's tenant transaction."""
    from account_intelligence.db.enums import SourceKind
    from account_intelligence.db.source_ids import get_or_create_source

    wanted = [p for p in postings if is_exported(p) and clean_http_url(p.url)]
    if wanted and missing_only:
        held = {
            r.url_hash
            for r in (
                await s.execute(
                    text(
                        "SELECT url_hash FROM sources WHERE account_id = :a AND url_hash = ANY(:h)"
                    ),
                    {"a": account_id, "h": [_hash_of(p.url) for p in wanted]},
                )
            ).all()
        }
        wanted = [p for p in wanted if _hash_of(p.url) not in held]
    if not wanted:
        return 0
    # the same lifecycle rule as the evidence writer: the established API writes both run rows
    # with one id; a harness may have only one of them (the foreign keys differ)
    lifecycle = (
        await s.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM account_runs WHERE id = :r),"
                " EXISTS (SELECT 1 FROM runs WHERE id = :r)"
            ),
            {"r": run_id},
        )
    ).one()
    account_run_id = run_id if lifecycle[0] else None
    legacy_run_id = run_id if not lifecycle[0] and lifecycle[1] else None
    if account_run_id is None and legacy_run_id is None:
        raise LookupError("the posting links reference a run missing from both run lifecycles")
    done = 0
    for p in wanted:
        url = clean_http_url(p.url)
        assert url is not None
        await s.run_sync(
            partial(
                get_or_create_source,
                account_id=account_id,
                run_id=legacy_run_id,
                account_run_id=account_run_id,
                url=url,
                kind=SourceKind.SECONDARY,
                title=(p.title or "").strip()[:500] or None,
                published_at=p.posted_at,
            )
        )
        done += 1
    return done


class PgPostingRepo:
    """Persistence for one tenant and account."""

    def __init__(
        self,
        txn: TxnFactory,
        tenant_id: UUID,
        account_id: UUID,
        payloads: LA.PayloadStore | None = None,
        guard: SN.Guard = SN.default_guard,
        on_archive_failure: Callable[[str], None] = lambda _posting: None,
    ):
        self.txn = txn
        self.tenant_id = tenant_id
        self.account_id = account_id
        # the payload store the structured archive copies are read from and written to (None:
        # the archive keeps the stored text itself, as before) and the client-output guard
        # every free-text field of a copy passes
        self.payloads = payloads
        self.guard = guard
        # told the key of a posting whose archive copy could not be stored (the package may not
        # import ``obs``: the application passes a logger, as for the people repository)
        self.on_archive_failure = on_archive_failure

    async def load_run(self, run_id: str) -> PipelineResult | None:
        """The latest stored pipeline record of the run (last gap pass, else first), or None."""
        async with self.txn(self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT record_json FROM job_pipeline_passes WHERE run_id = :r"
                        " ORDER BY pass_no DESC LIMIT 1"
                    ),
                    {"r": UUID(run_id)},
                )
            ).first()
            if row is None:
                row = (
                    await s.execute(
                        text("SELECT record_json FROM job_pipeline_runs WHERE run_id = :r"),
                        {"r": UUID(run_id)},
                    )
                ).first()
        return result_from_json(row.record_json) if row is not None else None

    async def load_previous(self, run_id: str) -> PreviousPostings | None:
        """The stored postings of an earlier run of THIS account (its latest pass), carried into
        an incremental re-research; None when the run is not this account's (another account's
        or tenant's run id never resolves: the account is checked here, the tenant and owner by
        the tables' row-level security)."""
        async with self.txn(self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT p.record_json FROM job_pipeline_passes p"
                        " JOIN job_pipeline_runs r ON r.run_id = p.run_id"
                        " WHERE p.run_id = :r AND r.account_id = :a"
                        " ORDER BY p.pass_no DESC LIMIT 1"
                    ),
                    {"r": UUID(run_id), "a": self.account_id},
                )
            ).first()
            if row is None:
                row = (
                    await s.execute(
                        text(
                            "SELECT record_json FROM job_pipeline_runs"
                            " WHERE run_id = :r AND account_id = :a"
                        ),
                        {"r": UUID(run_id), "a": self.account_id},
                    )
                ).first()
        if row is None:
            return None
        record = result_from_json(row.record_json)
        return PreviousPostings(run_id=record.run_id, as_of=record.as_of, postings=record.postings)

    async def save_pass(self, run_id: str, pass_no: int, record: PipelineResult) -> None:
        """Append one gap pass: side rows of the re-processed postings, then the pass record."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            previous = await self._latest_classes(s, run)
            for p in record.postings:
                c = p.classification
                if c is None or previous.get(p.posting_key) == _class_row(c):
                    continue  # untouched by this pass: nothing new to append
                for sentence in p.sentences:
                    await self._sentence(s, run, p.posting_key, sentence, pass_no)
                await self._classification(s, run, p.posting_key, c, pass_no)
            await s.execute(
                text(
                    "INSERT INTO job_pipeline_passes (tenant_id, run_id, pass_no, gap_exit,"
                    " record_json) VALUES (:t, :r, :n, :gap, CAST(:rec AS jsonb))"
                ),
                {
                    "t": self.tenant_id,
                    "r": run,
                    "n": pass_no,
                    "gap": record.exit_reasons.get("gap", "pending"),
                    "rec": json.dumps(result_to_json(record), sort_keys=True),
                },
            )

    async def _latest_classes(self, s: AsyncSession, run: UUID) -> dict[str, tuple[object, ...]]:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT ON (posting_key) posting_key, posting_class, classified_by,"
                    " reason, rule_id, evidence_sentence, second_pass_label"
                    " FROM job_posting_classifications WHERE run_id = :r"
                    " ORDER BY posting_key, pass_no DESC, id DESC"
                ),
                {"r": run},
            )
        ).all()
        return {row.posting_key: tuple(row[1:]) for row in rows}

    async def _sentence(
        self, s: AsyncSession, run: UUID, key: str, sentence: PlatformSentence, pass_no: int
    ) -> None:
        await s.execute(
            text(
                "INSERT INTO job_posting_platform_sentences (tenant_id, run_id,"
                " posting_key, platform, sentence, start_offset, end_offset,"
                " span_sha256, pass_no) VALUES (:t, :r, :k, :p, :s, :a, :b, :h, :n)"
            ),
            {
                "t": self.tenant_id,
                "r": run,
                "k": key,
                "p": sentence.platform,
                "s": sentence.text,
                "a": sentence.start,
                "b": sentence.end,
                "h": sentence.sha256,
                "n": pass_no,
            },
        )

    async def _classification(
        self, s: AsyncSession, run: UUID, key: str, c: Classification, pass_no: int
    ) -> None:
        await s.execute(
            text(
                "INSERT INTO job_posting_classifications (tenant_id, run_id,"
                " posting_key, posting_class, classified_by, reason, rule_id,"
                " evidence_sentence, second_pass_label, pass_no)"
                " VALUES (:t, :r, :k, :c, :by, :why, :rule, :ev, :second, :n)"
            ),
            {
                "t": self.tenant_id,
                "r": run,
                "k": key,
                "c": c.label,
                "by": c.classified_by,
                "why": c.reason,
                "rule": c.rule_id,
                "ev": c.evidence_sentence,
                "second": c.second_pass_label,
                "n": pass_no,
            },
        )

    async def _archive_entries(self, record: PipelineResult) -> list[LA.ArchiveEntry]:
        """The archive entries of the postings with stored text: a ``posting-archive/1`` record
        per posting, written to the payload store under a key made of what the posting said (an
        unchanged posting of a later research reuses the copy it has; changed content is a new
        copy beside the old one). Without a payload store, or when a text cannot be read, the
        stored text itself is the copy (the original behaviour)."""
        postings = [p for p in record.postings if p.text_ref]
        if self.payloads is None:
            return [self._text_entry(p) for p in postings]
        store = self.payloads
        gate = asyncio.Semaphore(_READ_CONCURRENCY)
        # a carried posting whose status was refreshed is captured as of the research date
        as_of = datetime.combine(record.as_of, time.min, tzinfo=UTC)

        async def one(p: Posting) -> tuple[Posting, dict[str, object] | None, str, datetime]:
            when = _seen_at(p, as_of)
            try:
                async with gate:
                    body = (await store.get(str(p.text_ref))).decode("utf-8", "replace")
                snap = SN.build_snapshot(p, body, captured_at=when, guard=self.guard)
            except Exception:  # noqa: BLE001 - an unreadable text keeps the stored text as the copy
                return p, None, "", when
            key = LA.archive_key(
                self.tenant_id, self.account_id, SN.identity_bytes(snap, p.status_since)
            )
            return p, snap, key, when

        built = await asyncio.gather(*(one(p) for p in postings))
        keys = sorted({key for _, snap, key, _ in built if snap is not None})
        async with self.txn(self.tenant_id) as s:
            have = {
                r.payload_ref
                for r in (
                    await s.execute(
                        text(
                            "SELECT payload_ref FROM link_archive"
                            " WHERE account_id = :a AND payload_ref = ANY(:k)"
                        ),
                        {"a": self.account_id, "k": keys},
                    )
                ).all()
            }
        entries: list[LA.ArchiveEntry] = []
        written: set[str] = set()
        for p, snap, key, when in built:
            if snap is None:
                entries.append(self._text_entry(p))
                continue
            if key not in have and key not in written:
                try:
                    await store.put(key, SN.snapshot_bytes(snap))
                except Exception:  # noqa: BLE001 - an object store failure must not fail the research
                    # the posting keeps its stored text as the archive copy (the original
                    # behaviour) and the paid pass is not redone (review 2026-10-29, finding 12)
                    self.on_archive_failure(p.posting_key)
                    entries.append(self._text_entry(p))
                    continue
                written.add(key)
            entries.append(
                LA.ArchiveEntry(
                    url=p.url,
                    kind=SN.KIND,
                    payload_ref=key,
                    archived_at=when,
                    format="json",
                    title=p.title,
                    source_ref=p.raw_ref,
                    # the index fields beside the row: the archive index never opens the body
                    meta=AR.index_meta(snap),
                )
            )
        return entries

    @staticmethod
    def _text_entry(p: Posting) -> LA.ArchiveEntry:
        """The stored posting text as the archive copy (kept for a posting whose structured
        record could not be built)."""
        return LA.ArchiveEntry(
            url=p.url,
            kind=SN.KIND,
            payload_ref=str(p.text_ref),
            archived_at=_when(p.retrieved_at),
            format="text",
            title=p.title,
            source_ref=p.raw_ref,
        )

    async def save_run(self, run_id: str, record: PipelineResult) -> None:
        """Write postings, sentences, classifications, observations and the record in one
        transaction."""
        run = UUID(run_id)
        as_of_dt = datetime.combine(record.as_of, time.min, tzinfo=UTC)
        # every posting whose text was stored is archived under its link as a structured record,
        # so the Job Postings row opens what the posting said once it closes (link archive)
        entries = await self._archive_entries(record)
        async with self.txn(self.tenant_id) as s:
            await LA.record(s, self.tenant_id, self.account_id, run, entries)
            await register_posting_sources(s, self.account_id, run, record.postings)
            for p in record.postings:
                await s.execute(
                    text(_POSTING),
                    {
                        "tenant": self.tenant_id,
                        "account": self.account_id,
                        "run": run,
                        "key": p.posting_key,
                        "row": p.model_dump_json(),
                        "status": p.status,
                        "bucket": p.window_bucket,
                        "url": p.url,
                        "urls": list(p.urls),
                        "title": p.title,
                        "location": p.location,
                        "posted": p.posted_at,
                        "first": p.first_seen_at,
                        "status_source": p.status_source,
                        "text_ref": p.text_ref,
                        "sha": p.text_sha256,
                        "complete": p.text_complete,
                        "full": p.full_text_read,
                        "class": p.posting_class,
                        "lanes": list(p.lanes),
                        "platforms": list(p.platforms),
                        "title_only": p.title_only,
                        "weak": p.weak_key,
                        "undated": p.window_bucket == "undated",
                        "hostile": p.hostile,
                        "last_seen": p.last_seen,
                        "seniority": p.seniority,
                        "new_since_previous": p.new_since_previous,
                    },
                )
                for sentence in p.sentences:
                    await self._sentence(s, run, p.posting_key, sentence, 0)
                if p.classification is not None:
                    await self._classification(s, run, p.posting_key, p.classification, 0)
                await s.execute(
                    text(
                        "INSERT INTO job_posting_observations (tenant_id, account_id, run_id,"
                        " posting_key, seen_at, source_category, status_seen, raw_ref)"
                        " VALUES (:t, :a, :r, :k, :seen, :cat, :st, :raw)"
                    ),
                    {
                        "t": self.tenant_id,
                        "a": self.account_id,
                        "r": run,
                        "k": p.posting_key,
                        "seen": _seen_at(p, as_of_dt),
                        "cat": p.status_source,
                        "st": p.status,
                        "raw": p.raw_ref,
                    },
                )
            await s.execute(
                text(
                    "INSERT INTO job_pipeline_runs (run_id, tenant_id, account_id, as_of,"
                    " window_start, config_hash, record_json, previous_run_id, carried)"
                    " VALUES (:r, :t, :a, :as_of, :start, :hash, CAST(:rec AS jsonb), :prev,"
                    " :carried)"
                ),
                {
                    "r": run,
                    "t": self.tenant_id,
                    "a": self.account_id,
                    "as_of": record.as_of,
                    "start": record.window_start,
                    "hash": record.config_hash,
                    "rec": json.dumps(result_to_json(record), sort_keys=True),
                    "prev": UUID(record.previous_run_id) if record.previous_run_id else None,
                    "carried": record.carried,
                },
            )

    async def first_and_last_seen(self, posting_key: str) -> tuple[object, object] | None:
        """``first_seen`` and ``last_seen`` across this account's runs, from observations."""
        async with self.txn(self.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT min(seen_at) AS first, max(seen_at) AS last FROM"
                        " job_posting_observations WHERE account_id = :a AND posting_key = :k"
                    ),
                    {"a": self.account_id, "k": posting_key},
                )
            ).first()
        return None if row is None or row.first is None else (row.first, row.last)


def _class_row(c: Classification) -> tuple[object, ...]:
    return (
        c.label,
        c.classified_by,
        c.reason,
        c.rule_id,
        c.evidence_sentence,
        c.second_pass_label,
    )
