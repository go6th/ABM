"""The user's model choice (owner decision 2026-10-14, Phase 4B section 1).

* ``GET /v1/models/choices``: every choosable model with its plain line and, when it cannot be
  chosen, why ("Not set up", "Not enabled for your workspace"); and the user's current research
  and analysis models with the platform defaults. The menus grey out what is unavailable, so a
  choice never fails at Send.
* ``PUT /v1/me/model-preferences``: remember the user's research and/or analysis model and the
  effort beside each (medium or high; each one checked first; 422 with the reason otherwise).
  Private to the user (``user_preferences``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.api import contract as C
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import models as M

router = APIRouter(tags=["models"])
register("GET", "/v1/models/choices", tenant_route("read_public", "read", step_up=False))
register("PUT", "/v1/me/model-preferences", tenant_route("save_own_view", "none", step_up=False))


class Preferences(BaseModel):
    """The models to remember (only the fields sent change)."""

    model_config = ConfigDict(extra="forbid")
    research_model: str | None = Field(default=None, min_length=1, max_length=64)
    analysis_model: str | None = Field(default=None, min_length=1, max_length=64)
    # medium or high (``choices.efforts``), owner decision 2026-10-20
    research_effort: str | None = Field(default=None, min_length=1, max_length=16)
    analysis_effort: str | None = Field(default=None, min_length=1, max_length=16)


def refused(error: M.ModelRefused) -> APIProblem:
    """The plain refusal for a model that cannot be used."""
    return APIProblem(
        422,
        "model_unavailable",
        "That model is not available",
        f"{error.reason}. Choose another model.",
    )


@router.get("/models/choices", response_model=C.ModelChoices)
async def choices(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The model menus."""
    async with tenant_txn(ident.tenant_id) as s:
        models, pol = await M.current(s, ident.user_id)
    return M.choices_view(models, pol)


@router.put("/me/model-preferences", response_model=C.ModelChoices)
async def save(body: Preferences, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Remember the user's models."""
    async with tenant_txn(ident.tenant_id) as s:
        try:
            await M.save_preferences(
                s,
                ident.user_id,
                research=body.research_model,
                analysis=body.analysis_model,
                research_effort=body.research_effort,
                analysis_effort=body.analysis_effort,
            )
        except M.ModelRefused as error:
            raise refused(error) from None
        models, pol = await M.current(s, ident.user_id)
    return M.choices_view(models, pol)
