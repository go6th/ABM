"""p_org: organization resolve, confirmed tech stack, people by technology.

Spec: docs/providers/p_org.md. Adapter rule: output never goes to a hosted file, so
no request ever sets ``file_name`` and a response carrying a ``file`` is refused.
People requests are asynchronous (start, then poll with ``request_id``); credits are
charged once on the first ``succeeded`` poll and polling is free.
"""

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
from account_intelligence.connectors.errors import (
    NotSupportedYet,
    PolicyRefused,
    RetryableProviderError,
    SchemaDriftError,
)
from account_intelligence.connectors.models import (
    OperationArgs,
    OrganizationRecord,
    OrgResolveArgs,
    OrgTechStackArgs,
    PeopleByTechnologyArgs,
    PersonRecord,
    Record,
    TechStackRecord,
)
from account_intelligence.connectors.providers._common import (
    Strict,
    classify_status,
    decode_json,
    provenance,
    validate,
)

CODE = ProviderCode.P_ORG


class _OrgAttrs(Strict):
    id: int | None = None
    slug: str | None = None
    name: str | None = None
    url: str | None = None
    employee_count: int | None = None
    industry: str | None = None
    headquarters_country: str | None = None
    provider_page_url: str | None = None  # renamed on input, see OrgConnector


class _OrgInput(Strict):
    url: str | None = None


class _OrgRow(Strict):
    input: _OrgInput | None = None
    attributes: _OrgAttrs | None = None  # an unmatched input comes back as {} (OpenAPI example)


class _OrgResponse(Strict):
    credits_used: int
    organizations: list[_OrgRow]
    file: dict[str, Any] | None = None


class _TechItem(Strict):
    name: str
    slug: str
    job_post_used_count: int


class _Category(Strict):
    category_slug: str
    category_name: str
    technologies: list[_TechItem]


class _BusinessFunction(Strict):
    business_function: str
    categories: list[_Category]


class _TechResponse(Strict):
    credits_used: int
    organization_id: int
    business_functions: list[_BusinessFunction]
    file: dict[str, Any] | None = None


class _PersonAttrs(Strict):
    name: str | None = None
    linkedin_url: str | None = None
    job_title: str | None = None
    job_function: str | None = None
    job_level: str | None = None
    location: str | None = None


class _PersonRow(Strict):
    person_id: int | None = None
    provider_page_url: str | None = None  # renamed on input, see OrgConnector
    attributes: _PersonAttrs | None = None


class _PeopleResponse(Strict):
    credits_used: int
    request_id: str | None = None
    status: str | None = None
    people: list[_PersonRow] | None = None
    total: int | None = None
    file: dict[str, Any] | None = None


class OrgConnector:
    """The p_org adapter: organizations, tech stack, people by technology (async job), teams."""

    code = CODE
    _SUPPORTED = frozenset(
        {Operation.ORG_RESOLVE, Operation.ORG_TECH_STACK, Operation.PEOPLE_BY_TECHNOLOGY}
    )

    def __init__(
        self,
        *,
        org_attributes: tuple[str, ...],
        people_attributes: tuple[str, ...],
        people_page_limit: int,
        page_url_field: str,
    ) -> None:
        if not 1 <= people_page_limit <= 200:  # people.md: limit default 10, max 200
            raise ValueError("people_page_limit must be within the documented 1..200")
        self.org_attributes = org_attributes
        self.people_attributes = people_attributes
        self.people_page_limit = people_page_limit
        # The provider names its own profile-page URL field after itself; the name lives in
        # config (providers.yaml p_org.page_url_field) so no vendor name is in code.
        self.page_url_field = page_url_field

    def supports(self, op: Operation) -> bool:
        """Whether the operation has a sourced p_org contract."""
        return op in self._SUPPORTED

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """The request for one operation; never asks for a hosted file (adapter rule)."""
        if op is Operation.ORG_RESOLVE:
            assert isinstance(args, OrgResolveArgs)
            org_body: dict[str, Any] = {
                "organizations": [{"url": args.domain}],
                "select": {"attributes": list(self.org_attributes)},
            }
            return _no_file(PageRequest("POST", "/v9/organizations", json_body=org_body))
        if op is Operation.ORG_TECH_STACK:
            assert isinstance(args, OrgTechStackArgs)
            return PageRequest("GET", f"/v9/organizations/{args.organization_id}/techs")
        if op is Operation.PEOPLE_BY_TECHNOLOGY:
            assert isinstance(args, PeopleByTechnologyArgs)
            offset = int(cursor) if cursor is not None else 0
            # Query grammar sourced from people.md PersonAttributes.technologies:
            # "The slug can be used in filter.query (technology EQ '<slug>')". The slug is
            # pattern-validated in the argument model, so no untrusted text reaches it.
            body: dict[str, Any] = {
                "filter": {
                    "organization_ids": [args.organization_id],
                    "query": {"query": f"technology EQ '{args.technology_slug}'"},
                },
                "select": {"attributes": list(self.people_attributes)},
                "limit": self.people_page_limit,
                "offset": offset,
            }
            return _no_file(PageRequest("POST", "/v9/people", json_body=body))
        raise NotSupportedYet(provider_code=CODE, operation=op)

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """Poll the people-by-technology job by its request id."""
        if op is not Operation.PEOPLE_BY_TECHNOLOGY:
            raise NotSupportedYet("no async jobs", provider_code=CODE, operation=op)
        return PageRequest("POST", "/v9/people", json_body={"request_id": job_id})

    def _neutral_keys(self, data: Any) -> Any:
        """Rename the provider's page-URL field to ``provider_page_url`` where we read it."""
        if not isinstance(data, dict):
            return data
        for row in data.get("organizations") or []:
            if isinstance(row, dict):
                _rename_key(row.get("attributes"), self.page_url_field, "provider_page_url")
        for row in data.get("people") or []:
            _rename_key(row, self.page_url_field, "provider_page_url")
        return data

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map p_org statuses to typed errors (202 means the job is still running)."""
        # api-overview.md error table: 202 accepted/in progress (retry soon), 400, 401,
        # 402 insufficient credits, 429, 500, 504 timed out (no credits charged).
        if raw.status_code == 202:
            return RetryableProviderError(
                "accepted, in progress", provider_code=CODE, operation=op, http_status=202
            )
        # VERIFY(p_org): Retry-After on 429 is not documented; the default cooldown applies.
        return classify_status(raw, CODE, op)

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Validate and normalise a p_org response into records with provenance."""
        data = self._neutral_keys(decode_json(raw, CODE, op))
        if op is Operation.ORG_RESOLVE:
            assert isinstance(args, OrgResolveArgs)
            org = validate(_OrgResponse, data, CODE, op)
            _refuse_file(org.file, op)
            # "One result row per entry, in input order": we send one entry and pick its row.
            rows = [
                r for r in org.organizations if r.input is not None and r.input.url == args.domain
            ]
            if len(rows) != 1:
                raise SchemaDriftError(
                    "expected exactly one row echoing the input domain",
                    provider_code=CODE,
                    operation=op,
                )
            a = rows[0].attributes
            matched = a is not None and a.id is not None
            record = OrganizationRecord(
                kind="organization",
                provenance=provenance(
                    base, source_url=a.provider_page_url if a else None, page=1, index=0
                ),
                lineage_hint=None,
                input_domain=args.domain,
                matched=matched,
                organization_id=a.id if a else None,
                slug=a.slug if a else None,
                name=a.name if a else None,
                url=a.url if a else None,
                employee_count=a.employee_count if a else None,
                industry=a.industry if a else None,
                headquarters_country=a.headquarters_country if a else None,
            )
            return ParsedPage((record,), 1, None, provider_credits=float(org.credits_used))
        if op is Operation.ORG_TECH_STACK:
            tech = validate(_TechResponse, data, CODE, op)
            _refuse_file(tech.file, op)
            records: list[Record] = []
            for bf in tech.business_functions:
                for cat in bf.categories:
                    for t in cat.technologies:
                        records.append(
                            TechStackRecord(
                                kind="tech_stack_item",
                                provenance=provenance(
                                    base, source_url=None, page=1, index=len(records)
                                ),
                                lineage_hint=None,
                                organization_id=tech.organization_id,
                                business_function=bf.business_function,
                                category_slug=cat.category_slug,
                                category_name=cat.category_name,
                                technology_name=t.name,
                                technology_slug=t.slug,
                                job_post_used_count=t.job_post_used_count,
                            )
                        )
            return ParsedPage(
                tuple(records), len(records), None, provider_credits=float(tech.credits_used)
            )
        people = validate(_PeopleResponse, data, CODE, op)
        _refuse_file(people.file, op)
        if people.status in ("pending", "running"):
            if people.request_id is None:
                raise SchemaDriftError(
                    "pending job without request_id", provider_code=CODE, operation=op
                )
            return ParsedPage((), 0, None, pending_job_id=people.request_id)
        if people.status == "failed":
            # people.md: "The request errored after retries"; failed jobs cost nothing.
            raise RetryableProviderError("provider job failed", provider_code=CODE, operation=op)
        if people.status != "succeeded" or people.people is None or people.total is None:
            raise SchemaDriftError("unexpected people job state", provider_code=CODE, operation=op)
        offset = int(cursor) if cursor is not None else 0
        page = offset // self.people_page_limit + 1
        out: list[Record] = []
        for index, row in enumerate(people.people):
            if row.person_id is None:
                continue  # unmatched row: no person to record
            pa = row.attributes
            out.append(
                PersonRecord(
                    kind="person",
                    provenance=provenance(
                        base, source_url=row.provider_page_url, page=page, index=index
                    ),
                    lineage_hint=None,
                    provider_person_id=str(row.person_id),
                    profile_url=pa.linkedin_url if pa else None,
                    name_text=pa.name if pa else None,
                    job_title_text=pa.job_title if pa else None,
                    job_function=pa.job_function if pa else None,
                    job_level=pa.job_level if pa else None,
                    location=pa.location if pa else None,
                )
            )
        next_offset = offset + self.people_page_limit
        # people.md: "Continue until offset + limit >= total".
        more = next_offset < people.total and len(people.people) > 0
        return ParsedPage(
            tuple(out),
            len(people.people),
            str(next_offset) if more else None,
            provider_credits=float(people.credits_used),
        )


def _rename_key(obj: Any, old: str, new: str) -> None:
    if isinstance(obj, dict) and old in obj:
        obj[new] = obj.pop(old)


def _no_file(request: PageRequest) -> PageRequest:
    if request.json_body is not None and "file_name" in request.json_body:
        raise PolicyRefused("p_org output must never go to a hosted file", provider_code=CODE)
    return request


def _refuse_file(file: dict[str, Any] | None, op: Operation) -> None:
    if file is not None:
        raise PolicyRefused(
            "p_org returned a hosted file; refused", provider_code=CODE, operation=op
        )
