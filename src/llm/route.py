"""The one routing table: task kind -> tier -> (``config/models.yaml`` task, default effort, role).

Every model call in the product names a task kind (R2) and goes through
``workflow.deps.RunDeps.resolve_model`` to the live port (``platform.llm.ChoiceLlm``). The task kind
fixes the tier and the token limit (R7); the tier fixes the ``models.yaml`` task (whose
``task_settings`` give the default effort and caching, and whose route in the active arm gives
the model) and the role whose model the user picks (research or analysis, ``llm.choice``).

* ``TASK_KINDS``: every task kind a call site names, with its tier and token limit. A test
  (``test_every_registered_task_kind_has_a_call_site``) keeps it free of dead entries.
* ``TIERS``: each tier's ``models.yaml`` task, its role, and whether the user's effort for the
  role applies to it. Extraction and classification never take the user's effort: they keep the
  task's configured effort (low) unless the agent registry (``config/agents.yaml``) names one.
* ``split_mode_problem``: G11, extraction and the analysis agents must run on different models,
  or ``models.yaml`` declares that they share one (``g11_shared_model`` with an ADR link).

GUARANTEE(test_every_registered_task_kind_has_a_call_site)
GUARANTEE(test_the_users_effort_never_reaches_extraction_or_classification)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

Role = Literal["research", "analysis"]


# The routing errors live here, not in ``llm.errors``: that module types provider failures on top
# of the connectors' retryable error, and the platform port (which reads this table) may not
# depend on the connectors.
class UnknownTaskKind(LookupError):
    """A call site named a task kind the routing table does not register (R2)."""


class JudgeNotIndependent(RuntimeError):  # noqa: N818 - the condition, named as G12 names it
    """No Claude model of the allow-list differs from the generator's (G12): the run fails at
    its start, before any paid call, with a full refund (ADR-0118). Never a silent cap."""


# the limit a reply cut off at its task kind's limit is re-asked with: the models' own output
# maximum (128000 on every allow-listed Claude model; models.yaml max_output_tokens). The
# models think and their thinking counts against the limit: staging 2026-11-06 (Olympus, Haiku
# 5.5) the sense check and distill spent 16000 and then 21000 tokens thinking, were cut off
# twice, and the Summary had no narrative (missing_narrative). Doubling up to 21000 was the old
# ceiling, set by the SDK's refusal of a non-streamed call above about 21333 tokens; a call above
# STREAM_ABOVE_TOKENS is now streamed instead (platform.llm), so no ceiling below the model's own
# is needed (owner 2026-11-06: no caps)
MAX_REASK_TOKENS = 128000
# the largest limit a non-streamed call may ask for (the SDK's ten-minute timeout guard)
STREAM_ABOVE_TOKENS = 21000


class Tier(StrEnum):
    """The tiers the live port understands (``platform.llm.load_routes``)."""

    PLANNER = "planner"
    EXTRACTOR = "extractor"
    CLASSIFIER = "classifier"
    SYNTHESIS = "synthesis"
    NARRATIVE = "narrative"
    JUDGE = "judge"  # nosec B105 -- model role label, not a credential.


@dataclass(frozen=True)
class TierRoute:
    """Where a tier goes in ``config/models.yaml`` and whose choice serves it."""

    task: str
    role: Role
    # the user's effort for the role replaces the task's configured effort
    takes_user_effort: bool


@dataclass(frozen=True)
class TaskKind:
    """A registered task kind: its tier and token limit (R7). The configured models think and
    their thinking counts against ``max_tokens``, so no limit is under 2000 (2026-10-12: the judge
    at 1000 and setup at 400 answered no JSON at all)."""

    tier: Tier
    max_tokens: int
    # a reply may be replayed within the run and reused across runs (``llm.reply_cache``)
    cacheable: bool = True


TIERS: Mapping[Tier, TierRoute] = {
    Tier.PLANNER: TierRoute("pull_planning", "research", True),
    Tier.EXTRACTOR: TierRoute("extraction", "research", False),
    Tier.CLASSIFIER: TierRoute("classification", "research", False),
    Tier.SYNTHESIS: TierRoute("synthesis", "analysis", True),
    Tier.NARRATIVE: TierRoute("narrative", "analysis", True),
    Tier.JUDGE: TierRoute("verification", "analysis", True),
}

TASK_KINDS: Mapping[str, TaskKind] = {
    # Workflow v1 (a full run without the ABM agent layer)
    "plan_gaps": TaskKind(Tier.PLANNER, 2000),
    "extract_facts": TaskKind(Tier.EXTRACTOR, 4000),
    "lead_queries": TaskKind(Tier.PLANNER, 2000),
    # replayable since 2026-10-20 (token review item 11): a retried node or a restarted slice
    # gets the stored verdict back instead of paying for the same re-read again
    "source_reread": TaskKind(Tier.JUDGE, 4000),
    # code pipelines and the grading judge (both graphs)
    "classify_posting": TaskKind(Tier.CLASSIFIER, 2000),
    "second_pass_posting": TaskKind(Tier.JUDGE, 4000),
    "classify_person": TaskKind(Tier.EXTRACTOR, 4000),
    "support_judge": TaskKind(Tier.JUDGE, 4000),
    # input mode file (facts from uploaded documents)
    "file_extract_facts": TaskKind(Tier.EXTRACTOR, 8000),
    # the written summary of a file analysis (documents.summarise, 2026-11-03)
    "file_summarise": TaskKind(Tier.PLANNER, 4000),
    # ABM agents (D-95, config/agents.yaml); sense check and distill on the planner tier
    # (ADR-0116: Opus 5.5 writes the Summary, not Fable 5.1)
    "abm_setup": TaskKind(Tier.EXTRACTOR, 4000),
    "abm_pass1_extract": TaskKind(Tier.EXTRACTOR, 16000),
    "abm_pass2_extract": TaskKind(Tier.EXTRACTOR, 16000),
    "abm_people_extract": TaskKind(Tier.EXTRACTOR, 16000),
    "abm_sense_check": TaskKind(Tier.PLANNER, 8000),
    "abm_distill": TaskKind(Tier.PLANNER, 16000),
}


@dataclass(frozen=True)
class Route:
    """One task kind resolved: its tier, ``models.yaml`` task, default effort, role and limit."""

    task_kind: str
    tier: Tier
    task: str
    effort: str | None
    role: Role
    max_tokens: int
    cacheable: bool


def require(task_kind: str) -> TaskKind:
    """The registered task kind (``UnknownTaskKind`` otherwise)."""
    kind = TASK_KINDS.get(task_kind)
    if kind is None:
        raise UnknownTaskKind(task_kind)
    return kind


def route(task_kind: str) -> Route:
    """The task kind's full route; the default effort is its task's ``task_settings`` effort."""
    from llm.choice import task_efforts

    kind = require(task_kind)
    tier = TIERS[kind.tier]
    return Route(
        task_kind,
        kind.tier,
        tier.task,
        task_efforts().get(tier.task),
        tier.role,
        kind.max_tokens,
        kind.cacheable,
    )


def role_of(tier: str) -> Role:
    """The role whose model and effort serve a tier."""
    return TIERS[Tier(tier)].role


def user_effort_applies(tier: str) -> bool:
    """Whether the user's effort for the tier's role replaces the task's configured effort."""
    return TIERS[Tier(tier)].takes_user_effort


# G11 is declared per tier pair: the extraction agents' tier and the analysis agents' tier
SHARED_MODEL_TIERS = (Tier.EXTRACTOR, Tier.PLANNER)
_ADR_LINK = re.compile(r"^docs/decisions/ADR-\d{4}-[a-z0-9-]+\.md$")


def split_mode_problem(
    extraction_model: str, analysis_model: str, declared: Mapping[str, Any] | None
) -> str | None:
    """G11 (split mode): the extraction agents (extractor tier) and the analysis agents (sense
    check and distill, planner tier) run on different models, or ``models.yaml`` says on purpose
    that they share one: ``g11_shared_model: {tiers: [extractor, planner], adr: <the ADR that
    decided it>}``. Silent equality is a problem; None when the configuration holds."""
    if extraction_model != analysis_model:
        return None
    declared = declared or {}
    tiers = [str(t) for t in declared.get("tiers") or ()]
    adr = declared.get("adr")
    pair = [str(t) for t in SHARED_MODEL_TIERS]
    if tiers == pair and isinstance(adr, str) and _ADR_LINK.match(adr):
        return None
    return (
        f"extraction and the analysis agents both run on {extraction_model}: declare"
        " g11_shared_model {tiers: [extractor, planner], adr: docs/decisions/ADR-NNNN-...md}"
        " or route them to different models"
    )
