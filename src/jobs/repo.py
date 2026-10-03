"""``PostingRepo`` on Postgres through ``tenant_txn`` (spec 6).

``save_run`` writes, in ONE tenant transaction: each posting row, its platform sentences, its
classification (append-only; a reclassification in a later pass adds a row), an observation per
posting (insert-only source of first and last seen), and the run-level record. ``load_run``
returns the stored record, so a re-entered stage makes no paid call (J14). No UPDATE or DELETE is
ever issued; the grants do not allow one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
        """The stored pipeline record of the run, or None."""
        async with self.txn(self.tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT record_json FROM job_pipeline_runs WHERE run_id = :r"),
                    {"r": UUID(run_id)},
                )
            ).first()
        return result_from_json(row.record_json) if row is not None else None

    async def save_run(self, run_id: str, record: PipelineResult) -> None:
        """Write postings, sentences, classifications, observations and the record in one
        transaction."""
        run = UUID(run_id)
        async with self.txn(self.tenant_id) as s:
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
                    await s.execute(
                        text(
                            "INSERT INTO job_posting_platform_sentences (tenant_id, run_id,"
                            " posting_key, platform, sentence, start_offset, end_offset,"
                            " span_sha256) VALUES (:t, :r, :k, :p, :s, :a, :b, :h)"
                        ),
                        {
                            "t": self.tenant_id,
                            "r": run,
                            "k": p.posting_key,
                            "p": sentence.platform,
                            "s": sentence.text,
                            "a": sentence.start,
                            "b": sentence.end,
                            "h": sentence.sha256,
                        },
                    )
                if p.classification is not None:
                    c = p.classification
                    await s.execute(
                        text(
                            "INSERT INTO job_posting_classifications (tenant_id, run_id,"
                            " posting_key, posting_class, classified_by, reason, rule_id,"
                            " evidence_sentence, second_pass_label)"
                            " VALUES (:t, :r, :k, :c, :by, :why, :rule, :ev, :second)"
                        ),
                        {
                            "t": self.tenant_id,
                            "r": run,
                            "k": p.posting_key,
                            "c": c.label,
                            "by": c.classified_by,
                            "why": c.reason,
                            "rule": c.rule_id,
                            "ev": c.evidence_sentence,
                            "second": c.second_pass_label,
                        },
                    )
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
