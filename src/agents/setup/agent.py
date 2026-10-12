"""Setup assistant (step 1). Validation is code; the model only picks a careers page.

* ``validate_setup`` turns the typed run request and the tenant's settings into ``SetupInput``:
  pursuing client (optional: a run researches the account by its name alone) and lanes come
  from tenant settings (they replace the operator's answers, guide
  S16), lanes must be skill lanes (S13), the domain is normalised in code, and the cap request is
  carried to the approval step (never spent here). A tenant id never comes from the request
  (``RunRequest`` has no such field; ``extra`` is forbidden).
* ``choose_careers_page`` asks the model which already-visited page is the careers page and
  returns that page's stored URL. A URL is never constructed; an id that was not visited is
  refused and counted.

GUARANTEE(test_setup_takes_client_and_lanes_from_tenant_settings)
GUARANTEE(test_setup_never_constructs_a_careers_url)
"""

from __future__ import annotations

from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import AgentError, OutputRejected
from agents.ports import AgentModel, RunContext, TenantAgentSettings
from agents.runtime import call_agent
from agents.schemas import RunRequest, SetupInput, SetupProposal, VisitedPage


class SetupInvalid(AgentError):
    """The run cannot start: a required per-client setting or input is missing or invalid."""

    code = "invalid_setup"


def validate_setup(
    request: RunRequest,
    settings: TenantAgentSettings,
    cfg: SkillConfig,
    cap_request_micros: int,
    visited_pages: tuple[VisitedPage, ...] = (),
) -> SetupInput:
    """See the module docstring."""
    lanes = settings.lanes or cfg.lanes
    unknown = set(lanes) - set(cfg.lanes)
    if unknown:
        raise SetupInvalid(f"unknown lanes {sorted(unknown)}")
    if cap_request_micros < 0:
        raise SetupInvalid("cap_request_negative")
    try:
        return SetupInput(
            account_name=request.account_name.strip(),
            domain=request.domain.strip().lower().rstrip("."),
            lanes=tuple(lanes),
            pursuing_client=settings.pursuing_client or None,
            depth="deep" if settings.depth == "deep" else "standard",
            cap_request_micros=cap_request_micros,
            visited_pages=visited_pages,
        )
    except ValueError as error:
        raise SetupInvalid("invalid_account_or_domain") from error


async def choose_careers_page(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    setup: SetupInput,
) -> tuple[str | None, int]:
    """``(careers URL from a visited page or None, rejected proposals)``."""
    if not setup.visited_pages:
        return None, 0
    try:
        out = await call_agent(contract, model, meter, ctx, setup, client_bound=False)
    except OutputRejected:
        return None, 1
    assert isinstance(out, SetupProposal)
    if out.careers_page_id is None:
        return None, 0
    page = next((p for p in setup.visited_pages if p.page_id == out.careers_page_id), None)
    return (page.url, 0) if page is not None else (None, 1)
