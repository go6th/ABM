"""p_scrape: plain markdown scrape of a job page. Spec: docs/providers/p_scrape.md.

Adapter rule: plain scrapes only, and only on job pages. JSON extraction and
search-with-scrape are refused (their response shape is not sourced and they cost
5 and 20+ credits per page).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from account_intelligence.connectors.base import (
    Operation,
    PageRequest,
    ParsedPage,
    ProviderCode,
    RawResponse,
)
from account_intelligence.connectors.errors import NonRetryableProviderError, NotSupportedYet
from account_intelligence.connectors.models import (
    OperationArgs,
    ScrapedPageRecord,
    ScrapeJobPageArgs,
)
from account_intelligence.connectors.providers._common import (
    Strict,
    classify_status,
    decode_json,
    provenance,
    validate,
)

CODE = ProviderCode.P_SCRAPE


class _Metadata(Strict):
    source_url: str | None = Field(default=None, alias="sourceURL")
    url: str | None = None  # final URL after redirects
    status_code: int | None = Field(default=None, alias="statusCode")
    title: str | list[str] | None = None
    error: str | None = None


class _Data(Strict):
    markdown: str | None = None  # present when the markdown format was requested
    metadata: _Metadata  # relied on: final URL and target status


class _ScrapeResponse(Strict):
    success: bool
    data: _Data


class ScrapeConnector:
    """The p_scrape adapter: plain scrapes of job pages only (adapter rule)."""

    code = CODE

    def supports(self, op: Operation) -> bool:
        """Only scrape_job_page."""
        return op is Operation.SCRAPE_JOB_PAGE

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """A plain markdown scrape with maxAge 0 (a live read this run, for G5)."""
        if op is not Operation.SCRAPE_JOB_PAGE:
            raise NotSupportedYet(provider_code=CODE, operation=op)
        assert isinstance(args, ScrapeJobPageArgs)
        if args.mode != "plain":
            # Reached only when a caller explicitly enabled the mode with a cost reason;
            # the response contract for these modes is not sourced, so refuse honestly.
            raise NotSupportedYet(
                f"scrape mode {args.mode} has no sourced response contract",
                provider_code=CODE,
                operation=op,
            )
        # maxAge 0: "If a cached version of the page is older than this value, the page will be
        # scraped" (v2 OpenAPI ScrapeOptions.maxAge; default is 2 days). A live read this run is
        # what lets grading treat this fetch as a full page read (G5).
        return PageRequest(
            "POST",
            "/v2/scrape",
            json_body={"url": args.url, "formats": [{"type": "markdown"}], "maxAge": 0},
        )

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """No asynchronous jobs for plain scrapes."""
        raise NotSupportedYet("no async jobs", provider_code=CODE, operation=op)

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map documented statuses to typed errors."""
        # v2 OpenAPI /scrape responses: 400, 402 (insufficient credits), 403 (feature or
        # terms not enabled), 404, 409, 429, 500, 503.
        # VERIFY(p_scrape): plan rate limit (config placeholder) and whether x-request-id
        # idempotency applies to plain scrapes (documented only for alexandria requests).
        return classify_status(raw, CODE, op, auth=frozenset({401, 403}))

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Validate and normalise a scrape response into one ScrapedPageRecord."""
        assert isinstance(args, ScrapeJobPageArgs)
        response = validate(_ScrapeResponse, decode_json(raw, CODE, op), CODE, op)
        if not response.success:
            raise NonRetryableProviderError(
                "provider reported success=false", provider_code=CODE, operation=op
            )
        meta = response.data.metadata
        title = (
            meta.title
            if isinstance(meta.title, str) or meta.title is None
            else " ".join(meta.title)
        )
        record = ScrapedPageRecord(
            kind="scraped_page",
            provenance=provenance(base, source_url=meta.url or meta.source_url, page=1, index=0),
            lineage_hint=None,
            requested_url=meta.source_url,
            final_url=meta.url,
            target_status=meta.status_code,
            markdown_text=response.data.markdown,
            title_text=title,
        )
        # billing.md: a returned document costs 1 credit even when the target said 403/404.
        return ParsedPage(records=(record,), items_read=1, next_cursor=None, document_returned=True)
