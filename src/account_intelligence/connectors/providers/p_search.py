"""p_search: open web search and known-page reads. Spec: docs/providers/p_search.md."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from account_intelligence.connectors.base import (
    Operation,
    PageRequest,
    ParsedPage,
    ProviderCode,
    RawResponse,
)
from account_intelligence.connectors.errors import NotSupportedYet
from account_intelligence.connectors.models import (
    OperationArgs,
    ReadPageArgs,
    WebPageRecord,
    WebSearchArgs,
)
from account_intelligence.connectors.providers._common import (
    Strict,
    classify_status,
    decode_json,
    provenance,
    validate,
)

CODE = ProviderCode.P_SEARCH


# Response models: only fields this code relies on. Required/optional follows the
# sourced OpenAPI (V1SearchResponse, V1WebSearchResult, V1ExtractResponse, ...).
class _SearchResult(Strict):
    url: str
    title: str | None = None  # optional in the spec
    publish_date: str | None = None  # optional in the spec
    excerpts: list[str]


class _SearchResponse(Strict):
    search_id: str
    results: list[_SearchResult]


class _ExtractResult(Strict):
    url: str
    title: str | None = None
    publish_date: str | None = None
    excerpts: list[str]
    full_content: str | None = None


class _ExtractError(Strict):
    url: str
    error_type: str
    http_status_code: int | None


class _ExtractResponse(Strict):
    extract_id: str
    results: list[_ExtractResult]
    errors: list[_ExtractError]


class SearchConnector:
    """The p_search adapter: open web search and known-page reads."""

    code = CODE
    _SUPPORTED = frozenset({Operation.WEB_SEARCH, Operation.READ_PAGE})

    def supports(self, op: Operation) -> bool:
        """web_search and read_page."""
        return op in self._SUPPORTED

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """The search or extract request from typed arguments."""
        if op is Operation.WEB_SEARCH:
            assert isinstance(args, WebSearchArgs)
            body: dict[str, Any] = {"search_queries": list(args.queries), "mode": args.mode}
            if args.objective is not None:
                body["objective"] = args.objective
            advanced: dict[str, Any] = {"max_results": args.max_results}
            policy: dict[str, Any] = {}
            if args.include_domains:
                policy["include_domains"] = list(args.include_domains)
            if args.after_date is not None:
                policy["after_date"] = args.after_date.isoformat()
            if policy:
                advanced["source_policy"] = policy
            body["advanced_settings"] = advanced
            return PageRequest("POST", "/v1/search", json_body=body)
        if op is Operation.READ_PAGE:
            assert isinstance(args, ReadPageArgs)
            body = {"urls": [args.url], "advanced_settings": {"full_content": args.full_content}}
            if args.objective is not None:
                body["objective"] = args.objective
            return PageRequest("POST", "/v1/extract", json_body=body)
        raise NotSupportedYet(provider_code=CODE, operation=op)

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """No asynchronous jobs."""
        raise NotSupportedYet("no async jobs", provider_code=CODE, operation=op)

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map documented statuses to typed errors."""
        # resources/warnings-and-errors.md: 401 auth, 402 credits, 403 forbidden (not
        # retryable), 408 retry, 422 validation, 429 rate limited, 500/502/503 retry.
        # VERIFY(p_search): Retry-After on 429 is not documented; the default cooldown applies.
        # VERIFY(p_search): whether failed Search/Extract calls are billed (bill_on_failure).
        return classify_status(raw, CODE, op, auth=frozenset({401, 403}))

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Validate and normalise search or extract results into WebPageRecords."""
        data = decode_json(raw, CODE, op)
        if op is Operation.WEB_SEARCH:
            search = validate(_SearchResponse, data, CODE, op)
            records = tuple(
                WebPageRecord(
                    kind="web_page",
                    provenance=provenance(base, source_url=r.url, page=1, index=i),
                    lineage_hint=None,
                    url=r.url,
                    title_text=r.title,
                    publish_date=r.publish_date,
                    excerpts_text=tuple(r.excerpts),
                    full_content_text=None,
                )
                for i, r in enumerate(search.results)
            )
            return ParsedPage(records=records, items_read=len(search.results), next_cursor=None)
        extract = validate(_ExtractResponse, data, CODE, op)
        records = tuple(
            WebPageRecord(
                kind="web_page",
                provenance=provenance(base, source_url=r.url, page=1, index=i),
                lineage_hint=None,
                url=r.url,
                title_text=r.title,
                publish_date=r.publish_date,
                excerpts_text=tuple(r.excerpts),
                full_content_text=r.full_content,
            )
            for i, r in enumerate(extract.results)
        )
        warnings = tuple(f"page_error:{e.error_type}:{e.http_status_code}" for e in extract.errors)
        return ParsedPage(
            records=records, items_read=len(extract.results), next_cursor=None, warnings=warnings
        )
