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

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence import archive as LA
from jobs.models import Classification, PlatformSentence
from jobs.pipeline import PipelineResult, result_from_json, result_to_json

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]

_POSTING = (
    "INSERT INTO job_postings (tenant_id, account_id, run_id, posting_key, row_json, status,"
    " window_bucket, url, other_urls, title, location, posted_at, first_seen, status_source,"
    " text_ref, text_sha256, text_complete, full_text_read, posting_class, lanes, platforms,"
    " title_only, weak_key, undated, hostile) VALUES (:tenant, :account, :run, :key,"
    " CAST(:row AS jsonb), :status, :bucket, :url, :urls, :title, :location, :posted, :first,"
    " :status_source, :text_ref, :sha, :complete, :full, :class, :lanes, :platforms, :title_only,"
    " :weak, :undated, :hostile)"
)


def _when(value: str) -> datetime:
    """Provider retrieval time as an aware datetime (UTC when the source gave no offset)."""
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


class PgPostingRepo:
    """Persistence for one tenant and account."""

    def __init__(self, txn: TxnFactory, tenant_id: UUID, account_id: UUID):
        self.txn = txn
        self.tenant_id = tenant_id
        self.account_id = account_id

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

    async def save_run(self, run_id: str, record: PipelineResult) -> None:
        """Write postings, sentences, classifications, observations and the record in one
        transaction."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
            # every posting whose text was stored is archived under its link, so the Job
            # Postings row opens the captured text once the posting closes (link archive)
            await LA.record(
                s,
                self.tenant_id,
                self.account_id,
                run,
                [
                    LA.ArchiveEntry(
                        url=p.url,
                        kind="posting",
                        payload_ref=p.text_ref,
                        archived_at=_when(p.retrieved_at),
                        format="text",
                        title=p.title,
                        source_ref=p.raw_ref,
                    )
                    for p in record.postings
                    if p.text_ref
                ],
            )
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
                        "seen": _when(p.retrieved_at),
                        "cat": p.status_source,
                        "st": p.status,
                        "raw": p.raw_ref,
                    },
                )
            await s.execute(
                text(
                    "INSERT INTO job_pipeline_runs (run_id, tenant_id, account_id, as_of,"
                    " window_start, config_hash, record_json)"
                    " VALUES (:r, :t, :a, :as_of, :start, :hash, CAST(:rec AS jsonb))"
                ),
                {
                    "r": run,
                    "t": self.tenant_id,
                    "a": self.account_id,
                    "as_of": record.as_of,
                    "start": record.window_start,
                    "hash": record.config_hash,
                    "rec": json.dumps(result_to_json(record), sort_keys=True),
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
