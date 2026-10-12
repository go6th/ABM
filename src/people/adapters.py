"""Production adapters for the people ports, over the connector layer and the run.

What exists and what does not (nothing here guesses a provider shape):

* Title search (``people_by_title``): the connector layer has the operation, but its provider
  contract is not supplied (``NotSupportedYet``), and the people-source switch
  (``config/people_sources.yaml``, P0-3) may name a source without it. Either way the source ends
  ``not_available_yet``. Pagination happens inside ``call_operation``; the adapter returns the
  whole sequence as one page ([VERIFY] V-PEOPLE-1: a provider cursor is not exposed).
* Association search (``people_by_technology``): resolves the organisation (``org_resolve``),
  pulls its tech stack (``org_tech_stack``) and searches every technology matched to the lane by
  the lane's name (``people.technology``); a lane with no matching technology ends
  ``not_available_yet``.
* Profiles (``profile_enrichment``) and posts: no sourced contract and no posts operation; they
  raise ``not_available_yet`` ([VERIFY] V-PEOPLE-2, V-PEOPLE-3).
* Mentions and their resolution: no stage stores people named in research as structured data
  yet; ``NotConfigured`` (the pipeline then has no mentions).
* Costs come from the ledger (``tool_calls.provider_cost_usd``, ``None`` when unknown).
* Retry ownership (W6): a retryable error below the node attempt budget is re-raised; the
  pipeline adds no retry.
* Only allow-listed operations are called (P14); none returns contact details.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import text

from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    ConnectorError,
    NotSupportedYet,
    RetryableProviderError,
)
from account_intelligence.connectors.models import (
    OrganizationRecord,
    OrgResolveArgs,
    OrgTechStackArgs,
    PeopleByTechnologyArgs,
    PeopleByTitleArgs,
    PersonRecord,
    TechStackRecord,
)
from account_intelligence.connectors.service import call_operation
from account_intelligence.tracker.model import Actor, ReasonCode
from people.config import PeopleConfig
from people.errors import NotConfigured
from people.models import Mention, PersonPage, PostsBatch, RawPerson, RawProfile
from people.ports import SourceUnavailable
from people.technology import technologies_by_lane

ROW_STAGE = "people"


class _Calls:
    def __init__(self, deps: Any, operations: frozenset[str]) -> None:
        self.deps = deps
        self.operations = operations  # operations of the active people source (P0-3)

    async def call(self, op: Operation, args: Any) -> Any:
        """One connector call with the retry-ownership rule; typed failures as SourceUnavailable."""
        if self.deps.connectors is None or op.value not in self.operations:
            raise SourceUnavailable("not_available_yet")
        try:
            return await call_operation(op, args, self.deps.call_context(), self.deps.connectors)
        except NotSupportedYet as error:
            raise SourceUnavailable("not_available_yet") from error
        except BudgetExceeded as error:
            raise SourceUnavailable("budget_cap", error.call_ref) from error
        except RetryableProviderError as error:
            if (error.attempt or 1) < self.deps.retry_max_attempts:
                raise
            raise SourceUnavailable("source_unavailable", error.call_ref) from error
        except ConnectorError as error:
            raise SourceUnavailable("source_unavailable", error.call_ref) from error


def _people_page(result: Any, domain: str) -> PersonPage:
    records = []
    ref = result.call_refs[0] if result.call_refs else None
    for record in result.records:
        if not isinstance(record, PersonRecord):
            continue
        prov = record.provenance
        records.append(
            RawPerson(
                source_record_id=f"{prov.raw_ref}#{prov.page}.{prov.index}",
                origin="title_search",
                provider_code=prov.provider_code.value,
                raw_ref=prov.raw_ref,
                call_ref=ref,
                retrieved_at=prov.retrieved_at.isoformat(),
                name=record.name_text,
                title=record.job_title_text,
                profile_url=record.profile_url,
                location=record.location,
                search_domain=domain,
            )
        )
    return PersonPage(
        records=tuple(records),
        next_cursor=None,  # VERIFY(V-PEOPLE-1): call_operation pages internally
        call_ref=ref,
        complete=result.status == "done",
        reported_count=result.counts.read,
    )


class ConnectorPeopleSearch(_Calls):
    """``PeopleSearchPort`` over ``call_operation``."""

    def __init__(self, deps: Any, operations: frozenset[str], cfg: PeopleConfig) -> None:
        super().__init__(deps, operations)
        self.cfg = cfg

    async def search_titles(
        self, lane: str, pass_no: int, titles: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        """The title search on the account domain (whole sequence in one page)."""
        if cursor is not None:
            return PersonPage()
        result = await self.call(
            Operation.PEOPLE_BY_TITLE, PeopleByTitleArgs(domain=self.deps.domain, titles=titles)
        )
        return _people_page(result, self.deps.domain)

    async def search_association(
        self, lane: str, functions: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        """People by every technology of the lane in the resolved organisation's stack."""
        if cursor is not None:
            return PersonPage()
        org = await self.call(Operation.ORG_RESOLVE, OrgResolveArgs(domain=self.deps.domain))
        found = next(
            (r for r in org.records if isinstance(r, OrganizationRecord) and r.matched), None
        )
        if found is None or found.organization_id is None:
            raise SourceUnavailable(
                "source_unavailable", org.call_refs[0] if org.call_refs else None
            )
        stack = await self.call(
            Operation.ORG_TECH_STACK, OrgTechStackArgs(organization_id=found.organization_id)
        )
        items = [r for r in stack.records if isinstance(r, TechStackRecord)]
        slugs = technologies_by_lane((lane,), items, self.cfg.technologies_per_lane).get(lane, ())
        if not slugs:
            raise SourceUnavailable("not_available_yet")  # the stack has nothing for this lane
        pages = [
            _people_page(
                await self.call(
                    Operation.PEOPLE_BY_TECHNOLOGY,
                    PeopleByTechnologyArgs(
                        organization_id=found.organization_id, technology_slug=slug
                    ),
                ),
                self.deps.domain,
            )
            for slug in slugs
        ]
        return PersonPage(
            records=tuple(
                r.model_copy(update={"origin": "association"}) for p in pages for r in p.records
            ),
            next_cursor=None,
            call_ref=pages[0].call_ref,
            complete=all(p.complete for p in pages),
            reported_count=sum(p.reported_count or 0 for p in pages),
        )


class UnavailableProfiles:
    """``ProfilePort``: the profile contract is not supplied ([VERIFY] V-PEOPLE-2)."""

    async def pull(self, profile_url: str) -> RawProfile:
        """Always unavailable until the contract and a recorded sample exist."""
        raise SourceUnavailable("not_available_yet")


class UnavailablePosts:
    """``PostsPort``: no posts operation exists in the connector layer ([VERIFY] V-PEOPLE-3)."""

    async def pull(self, profile_url: str, since: date) -> PostsBatch:
        """Always unavailable until an operation is sourced."""
        raise SourceUnavailable("not_available_yet")


class NoMentions:
    """``MentionsPort`` and ``MentionResolverPort``: not configured (no structured mentions)."""

    async def mentions(self) -> tuple[Mention, ...]:
        """Raises ``NotConfigured``."""
        raise NotConfigured("people named in research are not stored as structured data yet")

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        """Unavailable."""
        raise SourceUnavailable("not_available_yet")


class LedgerCosts:
    """``CostPort`` from the connector ledger of this run."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def cost_usd(self, call_ref: str) -> float | None:
        """The provider-reported cost of the call, ``None`` when unknown."""
        async with self.deps.txn(self.deps.tenant_id) as s:
            value = (
                await s.execute(
                    text(
                        "SELECT provider_cost_usd FROM tool_calls WHERE run_id = :r"
                        " AND args_hash = :h"
                    ),
                    {"r": self.deps.run_id, "h": call_ref},
                )
            ).scalar_one_or_none()
        return float(value) if value is not None else None


class SourceRegistry:
    """``SourceRegistryPort`` over the per-account source code allocator."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def code_for(self, url: str) -> str:
        """The stable source code of ``url`` (allocated once per account)."""
        from account_intelligence.db.enums import SourceKind
        from account_intelligence.db.source_ids import get_or_create_source

        async with self.deps.txn(self.deps.tenant_id) as s:
            source = await s.run_sync(
                lambda sync: get_or_create_source(
                    sync,
                    account_id=self.deps.account_id,
                    run_id=self.deps.run_id,
                    url=url,
                    kind=SourceKind.SECONDARY,
                )
            )
            return str(source.source_code)


class PeopleTrackerEvidence:
    """``CoverageEvidencePort`` over the tracker (stage ``people`` owns the three rows)."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        """Report one owned row; the tracker derives its status from the ledger."""
        async with self.deps.txn(self.deps.tenant_id) as session:
            await self.deps.tracker(session).record_outcome(
                self.deps.tenant_id,
                self.deps.run_id,
                row_key,
                list(call_refs),
                Actor.STAGE,
                None
                if call_refs
                else ReasonCode(not_attempted_reason or ReasonCode.NOT_AVAILABLE_YET.value),
                stage=ROW_STAGE,
                expected_calls=expected_calls,
                cap_hit=cap_hit,
            )


class PeopleProposalSink:
    """``GradingPort``: templated claims and Findings stored as the stage's proposals."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.reference: str | None = None

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        """Store the claims as the people stage's proposals for ``distill_grade``."""
        if claims:
            self.reference = await self.store.put_stage(
                ROW_STAGE, [c.model_dump(mode="json") for c in claims]
            )


class RunPeopleInput:
    """``RunInputPort`` from the run row (``run_input`` JSON and the creation date)."""

    def __init__(self, run_input: dict[str, Any], as_of: date, budget_usd: float | None) -> None:
        self.data, self._as_of, self._budget = run_input, as_of, budget_usd

    async def lanes_off(self) -> frozenset[str]:
        """Lanes switched off in the run input."""
        return frozenset(self.data.get("lanes_off") or ())

    async def as_of(self) -> date:
        """The run's creation date."""
        return self._as_of

    async def regions(self) -> tuple[str, ...]:
        """``people_regions`` from the run input (empty: global, PRD 3.3)."""
        return tuple(str(r) for r in self.data.get("people_regions") or ())

    async def excluded_people(self) -> tuple[str, ...]:
        """The tenant's excluded people the run input carries (settings version of the run)."""
        return tuple(str(p) for p in self.data.get("excluded_people") or ())

    async def reference_employers(self) -> tuple[str, ...] | None:
        """The pursuing client plus ``reference_employers``; ``None`` when neither is given."""
        names = [self.data.get("pursuing_client"), *(self.data.get("reference_employers") or ())]
        kept = tuple(str(n) for n in names if n)
        return kept or None

    async def people_budget_usd(self) -> float | None:
        """This stage's share of the run budget."""
        return self._budget


class LlmPeopleExtractor:
    """The tool-less extractor for Findings (task kind ``classify_person``)."""

    def __init__(self, deps: Any, prompt_name: str = "people_findings.v1") -> None:
        self.deps, self.prompt_name = deps, prompt_name

    async def __call__(self, person_key: str, text: str, canary: str) -> object:
        """One model call; the raw (untrusted) answer is validated by ``people.findings``."""
        from account_intelligence.agents.output import parse_json
        from account_intelligence.agents.prompts import prompt

        system = prompt(self.prompt_name).replace("{canary}", canary)
        user = json.dumps({"person_key": person_key, "text": text})
        result = await self.deps.model("classify_person", system, user)
        parsed = parse_json(result.text)
        return parsed if parsed is not None else result.text
