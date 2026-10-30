"""Production adapters for ``SourceTextPort`` and ``FetchLedgerPort`` over the connector ledger.

Sources are the normalised records of this run's ledgered calls, re-parsed deterministically
from their stored payloads (the ledger keeps the call arguments). Fetch records come from the
same ledger: ``done`` calls are successful, attempts that only ``failed`` are unsuccessful.
Every query reads under ``tenant_txn`` for the run's tenant (forced RLS) and filters by run id.

Grading loads the run's sources once per batch, and a research grades dozens of batches. Each
load used to read every stored payload again (one object-store read apiece, one after another)
and parse it on the event loop, job feed pages of tens of megabytes included: minutes per batch
and a loop that could not answer its health check. A stored payload never changes (its key is
its content), so the sources parsed from it are kept on the adapter, which belongs to one run's
slice: a load reads and parses only the calls it has not seen, side by side, off the event loop.
The memo is keyed by tenant, run, call and payload, and is filled only from rows the tenant's own
transaction returned.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text

from account_intelligence.connectors.base import Connector, Operation, ProviderCode, RawResponse
from account_intelligence.connectors.models import (
    ARGS_FOR,
    JobOpeningRecord,
    NewsEventRecord,
    OperationArgs,
    OrganizationRecord,
    PersonRecord,
    Record,
    ScrapedPageRecord,
    TechDetectionRecord,
    TechStackRecord,
    WebPageRecord,
)
from account_intelligence.connectors.payloads import PayloadStore
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.ports import FetchRecord, RunInfo, RunSource
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.urls import normalize_url
from crosscut.fanout import fan_out_all

# payloads read and parsed at once when a load meets calls it has not parsed yet
SOURCE_LOADS_IN_FLIGHT = 8


def record_text(r: Record) -> str:
    """The stored text a supporting sentence must be found in (G4)."""
    parts: list[str | None]
    if isinstance(r, WebPageRecord):
        parts = [r.title_text, r.full_content_text, *r.excerpts_text]
    elif isinstance(r, ScrapedPageRecord):
        parts = [r.title_text, r.markdown_text]
    elif isinstance(r, NewsEventRecord):
        parts = [r.article_title_text, r.summary_text, r.article_sentence_text]
    elif isinstance(r, JobOpeningRecord):
        parts = [r.title_text, r.description_text]
    elif isinstance(r, TechDetectionRecord):
        parts = [r.technology_name]
    elif isinstance(r, OrganizationRecord):
        parts = [r.name, r.industry, r.headquarters_country]
    elif isinstance(r, TechStackRecord):
        parts = [r.technology_name, r.category_name, r.business_function]
    elif isinstance(r, PersonRecord):
        parts = [r.name_text, r.job_title_text, r.job_function, r.job_level, r.location]
    else:  # pragma: no cover - every record kind is listed above
        parts = []
    return "\n".join(p for p in parts if p)


def record_published(r: Record) -> datetime | None:
    """The published date a record states, if any (D-14 dating)."""
    raw: str | None = None
    if isinstance(r, NewsEventRecord):
        raw = r.article_published_at or r.found_at
    elif isinstance(r, WebPageRecord):
        raw = r.publish_date
    elif isinstance(r, ScrapedPageRecord):
        raw = r.publish_date  # the article's own metadata (never the day it was read)
    elif isinstance(r, JobOpeningRecord):
        raw = r.posted_at or r.first_seen_at
    if not raw:
        return None
    raw = raw.strip()
    try:
        if len(raw) == 10:  # a calendar date alone (a dateline): midnight UTC
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None  # a time without a zone is not trusted


def call_queries(operation: str, args: Mapping[str, Any]) -> tuple[str, ...]:
    """What a call actually asked: search queries verbatim, otherwise a canonical description."""
    if operation == "web_search":
        return tuple(args.get("queries") or ())
    return (f"{operation} " + json.dumps(args, sort_keys=True, separators=(",", ":")),)


@dataclass
class LedgerAdapter:
    """Grading's SourceTextPort and FetchLedgerPort over the connector ledger and payload store."""

    txn: TxnFactory
    payloads: PayloadStore
    connectors: Mapping[ProviderCode, Connector]
    cfg: GradingConfig
    # the sources parsed from a stored payload, by (tenant, run, call, payload): see the module
    # docstring. One adapter serves one run's slice, so this lives and dies with it.
    _parsed: dict[tuple[Any, Any, str, str], tuple[RunSource, ...]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    async def _rows(self, run: RunInfo) -> list[Any]:
        async with self.txn(run.tenant_id) as s:
            return list(
                (
                    await s.execute(
                        text(
                            "SELECT args_hash, provider_code, operation, response, response_ref,"
                            " created_at"
                            " FROM tool_calls WHERE run_id = :r AND provider_code IS NOT NULL"
                            " ORDER BY args_hash"
                        ),
                        {"r": run.run_id},
                    )
                ).all()
            )

    async def fetches(self, run: RunInfo) -> Mapping[str, FetchRecord]:
        """How each call of this run was obtained (origin, category, success, queries)."""
        out: dict[str, FetchRecord] = {}
        for row in await self._rows(run):
            meta = row.response or {}
            when = (
                datetime.fromisoformat(meta["retrieved_at"])
                if "retrieved_at" in meta
                else row.created_at
            )
            out[row.args_hash] = FetchRecord(
                call_ref=row.args_hash,
                run_id=run.run_id,
                provider_code=row.provider_code,
                operation=row.operation,
                origin=self.cfg.origin_by_operation.get(row.operation, "broker_record"),
                tool_category=self.cfg.category_by_operation.get(row.operation, "web_search"),
                fetched_at=when,
                success=True,
                queries=call_queries(row.operation, meta.get("args") or {}),
            )
        async with self.txn(run.tenant_id) as s:
            failed = (
                await s.execute(
                    text(
                        "SELECT DISTINCT ON (args_hash) args_hash, provider_code, operation,"
                        " created_at"
                        " FROM tool_call_events WHERE run_id = :r AND event = 'failed' AND"
                        " provider_code IS NOT NULL"
                        " ORDER BY args_hash, attempt DESC"
                    ),
                    {"r": run.run_id},
                )
            ).all()
        for row in failed:
            if row.args_hash not in out:
                out[row.args_hash] = FetchRecord(
                    row.args_hash,
                    run.run_id,
                    row.provider_code,
                    row.operation,
                    self.cfg.origin_by_operation.get(row.operation, "broker_record"),
                    self.cfg.category_by_operation.get(row.operation, "web_search"),
                    row.created_at,
                    False,
                )
        return out

    async def sources(self, run: RunInfo) -> Sequence[RunSource]:
        """Every stored source text of this run, re-parsed from its payload (once per payload)."""
        rows = [r for r in await self._rows(run) if self._usable(r)]
        keys = [(run.tenant_id, run.run_id, r.args_hash, r.response_ref) for r in rows]
        todo = [(k, r) for k, r in zip(keys, rows, strict=True) if k not in self._parsed]

        async def load(item: tuple[tuple[Any, Any, str, str], Any]) -> tuple[RunSource, ...]:
            """The sources of one call not parsed yet."""
            return await self._load(run, item[1])

        loaded = await fan_out_all(todo, load, limit=SOURCE_LOADS_IN_FLIGHT)
        for (key, _row), found in zip(todo, loaded, strict=True):
            self._parsed[key] = found
        return [source for key in keys for source in self._parsed[key]]

    @staticmethod
    def _usable(row: Any) -> bool:
        """A row that can be re-parsed: it has a payload and the call's arguments (a claim citing
        any other is refused, ``url_not_in_run``)."""
        return row.response_ref is not None and "args" in (row.response or {})

    async def _load(self, run: RunInfo, row: Any) -> tuple[RunSource, ...]:
        """The sources of one call: its payload read, then parsed off the event loop."""
        meta = row.response or {}
        op = Operation(row.operation)
        args = ARGS_FOR[op].model_validate_json(json.dumps(meta["args"]))
        body = await self.payloads.get(row.response_ref)
        retrieved = datetime.fromisoformat(meta["retrieved_at"])
        connector = self.connectors[ProviderCode(row.provider_code)]
        return await asyncio.to_thread(
            self._parse, run, row, meta, op, args, body, retrieved, connector
        )

    def _parse(
        self,
        run: RunInfo,
        row: Any,
        meta: Mapping[str, Any],
        op: Operation,
        args: Any,
        body: bytes,
        retrieved: datetime,
        connector: Connector,
    ) -> tuple[RunSource, ...]:
        """One payload parsed into its sources (pure: no I/O, no shared state)."""
        base = {
            "provider_code": row.provider_code,
            "operation": op,
            "raw_ref": row.response_ref,
            "retrieved_at": retrieved,
        }
        raw = RawResponse(200, str(meta.get("content_type", "application/json")), body, {}, 0, "")
        parsed = connector.parse(op, cast(OperationArgs, args), raw, base, meta.get("cursor"))
        out: list[RunSource] = []
        for i, record in enumerate(parsed.records):
            url = record.provenance.source_url
            if not url:
                continue
            try:
                norm = normalize_url(url)
            except ValueError:
                continue
            out.append(
                RunSource(
                    source_ref=f"{row.response_ref}#{i}",
                    call_ref=row.args_hash,
                    run_id=run.run_id,
                    url=url,
                    url_normalized=norm,
                    host=(norm.split("/")[2]).split(":")[0],
                    text=record_text(record),
                    origin=self.cfg.origin_by_operation.get(row.operation, "broker_record"),
                    provider_code=row.provider_code,
                    operation=row.operation,
                    fetched_at=retrieved,
                    published_at=record_published(record),
                    lineage_hint=record.lineage_hint,
                )
            )
        return tuple(out)
