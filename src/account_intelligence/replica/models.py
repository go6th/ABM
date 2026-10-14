"""Model choice in the workspace (owner decision 2026-10-14, Phase 4B section 1).

* The platform enables models and sets the default research and analysis models
  (``platform_defaults``; NULL means the catalogue's own list and defaults).
* A client is allowed a set of the enabled models (``client_settings.allowed_models``; NULL means
  every enabled model except those that need the client's permission, Kimi K3, whose data would
  go to Moonshot AI).
* A user's two choices live in ``user_preferences`` (owned by the user); a conversation may set
  its own analysis model (``chat_threads.analysis_model``).

``resolve`` is called before any work starts: a model that is not set up or not allowed is
refused there with the reason the user sees, never at the provider.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from llm.choice import NOT_ENABLED, RunModels, availability, catalogue


class ModelRefused(ValueError):
    """A chosen model cannot be used here; ``reason`` is what the user sees."""

    def __init__(self, role: str, reason: str) -> None:
        super().__init__(f"{role}: {reason}")
        self.role = role
        self.reason = reason


@dataclass(frozen=True)
class Policy:
    """What the platform enables and the client allows (ids; None: the catalogue's rule)."""

    enabled: tuple[str, ...]
    allowed: tuple[str, ...] | None
    default_research: str
    default_analysis: str


async def platform_policy(session: Any) -> tuple[tuple[str, ...], str, str]:
    """The enabled models and the platform defaults."""
    cat = catalogue()
    row = (
        await session.execute(
            text(
                "SELECT enabled_models, default_research_model, default_analysis_model"
                " FROM platform_defaults WHERE id = 1"
            )
        )
    ).one()
    enabled = tuple(row.enabled_models) if row.enabled_models is not None else cat.ids
    return (
        tuple(m for m in enabled if cat.get(m) is not None),
        str(row.default_research_model or cat.default_research),
        str(row.default_analysis_model or cat.default_analysis),
    )


async def policy(session: Any) -> Policy:
    """The caller tenant's model policy (inside its transaction)."""
    enabled, research, analysis = await platform_policy(session)
    allowed = await session.scalar(text("SELECT allowed_models FROM client_settings"))
    return Policy(enabled, tuple(allowed) if allowed is not None else None, research, analysis)


def reason(model_id: str, pol: Policy, env: Mapping[str, str] | None = None) -> str | None:
    """None when the model can be used, else why not."""
    return availability(
        model_id,
        enabled=pol.enabled,
        allowed=pol.allowed,
        env=os.environ if env is None else env,
    )


async def preferences(session: Any, user_id: str) -> tuple[str | None, str | None]:
    """The user's saved research and analysis models (None: not chosen)."""
    row = (
        await session.execute(
            text(
                "SELECT research_model, analysis_model FROM user_preferences"
                " WHERE owner_user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    return (row.research_model, row.analysis_model) if row is not None else (None, None)


def _usable(choice: str | None, fallback: str, pol: Policy, env: Mapping[str, str] | None) -> str:
    """The choice when it can be used, else the default (a choice the admin later withdrew
    falls back quietly; an explicit request is checked in ``resolve``)."""
    if choice is not None and reason(choice, pol, env) is None:
        return choice
    return fallback


async def current(
    session: Any, user_id: str, env: Mapping[str, str] | None = None
) -> tuple[RunModels, Policy]:
    """The models the user works with now (saved choice, else the platform default)."""
    pol = await policy(session)
    research, analysis = await preferences(session, user_id)
    return (
        RunModels(
            _usable(research, pol.default_research, pol, env),
            _usable(analysis, pol.default_analysis, pol, env),
        ),
        pol,
    )


async def resolve(
    session: Any,
    user_id: str,
    *,
    research: str | None = None,
    analysis: str | None = None,
    env: Mapping[str, str] | None = None,
) -> RunModels:
    """The models for one run or answer: an explicit request must be usable (``ModelRefused``
    otherwise), else the user's current models."""
    models, pol = await current(session, user_id, env)
    for role, wanted in (("research", research), ("analysis", analysis)):
        if wanted is None:
            continue
        why = reason(wanted, pol, env)
        if why is not None:
            raise ModelRefused(role, why)
        setattr(models, role, wanted)
    return models


async def save_preferences(
    session: Any,
    user_id: str,
    *,
    research: str | None,
    analysis: str | None,
    env: Mapping[str, str] | None = None,
) -> RunModels:
    """Remember the user's choices (each one checked first); returns the models now in use."""
    pol = await policy(session)
    for role, wanted in (("research", research), ("analysis", analysis)):
        if wanted is not None and (why := reason(wanted, pol, env)) is not None:
            raise ModelRefused(role, why)
    await session.execute(
        text(
            "INSERT INTO user_preferences (owner_user_id, research_model, analysis_model)"
            " VALUES (:u, :r, :a) ON CONFLICT (tenant_id, owner_user_id) DO UPDATE SET"
            " research_model = coalesce(EXCLUDED.research_model, user_preferences.research_model),"
            " analysis_model = coalesce(EXCLUDED.analysis_model, user_preferences.analysis_model),"
            " updated_at = now()"
        ),
        {"u": user_id, "r": research, "a": analysis},
    )
    models, _ = await current(session, user_id, env)
    return models


async def conversation_model(
    session: Any,
    user_id: str,
    requested: str | None,
    thread_id: Any | None,
    env: Mapping[str, str] | None = None,
) -> str:
    """The analysis model of one Ask turn: the composer's choice (checked: ``ModelRefused``),
    else the conversation's model while it is still usable, else the user's current one."""
    if requested is not None:
        return (await resolve(session, user_id, analysis=requested, env=env)).analysis
    models, pol = await current(session, user_id, env)
    if thread_id is not None:
        kept = await session.scalar(
            text("SELECT analysis_model FROM chat_threads WHERE id = :t"), {"t": thread_id}
        )
        if kept and reason(str(kept), pol, env) is None:
            return str(kept)
    return models.analysis


def choices_view(
    models: RunModels, pol: Policy, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """What the model menus show: every model with its line and why it is greyed out."""
    return {
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "name": m.name,
                "line": m.line,
                "available": reason(m.id, pol, env) is None,
                "reason": reason(m.id, pol, env),
            }
            for m in catalogue().models
        ],
        "research": models.research,
        "analysis": models.analysis,
        "default_research": pol.default_research,
        "default_analysis": pol.default_analysis,
    }


def admin_view(
    enabled: tuple[str, ...], env: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """The console's model list: enabled, set up, and whether a client must allow it."""
    from llm.choice import set_up

    cat = catalogue()
    env = os.environ if env is None else env
    return [
        {
            "id": m.id,
            "name": m.name,
            "line": m.line,
            "enabled": m.id in enabled,
            "set_up": set_up(m, env),
            "needs_client_permission": m.id in cat.needs_client_permission,
        }
        for m in cat.models
    ]


def check_ids(ids: list[str] | None) -> list[str] | None:
    """Admin input: every id must be a choice (ValueError otherwise)."""
    if ids is None:
        return None
    unknown = [i for i in ids if catalogue().get(i) is None]
    if unknown:
        raise ValueError(f"unknown models: {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(ids))


__all__ = ["NOT_ENABLED", "ModelRefused", "Policy", "current", "resolve", "save_preferences"]
