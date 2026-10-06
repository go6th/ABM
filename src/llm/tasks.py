"""Task kinds at the call sites (R2) and their legacy ``LlmPort`` tier.

Every model call in the product names a task kind registered in ``config/model_routing.yaml``.
With a router configured the call goes through it (selection, fallback, records, R3 to R11);
without one (hermetic tests, local development) it goes to the legacy ``LlmPort`` under the tier
the legacy adapters understand, listed here (transitional, OI-LLM-2).

GUARANTEE(test_every_model_call_site_names_a_registered_task_kind): R2 static scan.
"""

from __future__ import annotations

from llm.config import TaskKind, load_routing_config
from llm.errors import UnknownTaskKind

LEGACY_TIER: dict[str, str] = {
    "plan_gaps": "planner",
    "extract_facts": "extractor",
    "classify_posting": "classifier",
    "second_pass_posting": "judge",
    "classify_person": "extractor",
    "lead_queries": "planner",
    "synthesise_hypotheses": "planner",
    "write_narrative": "planner",
    "distill": "planner",
    "support_judge": "judge",
    "source_reread": "judge",
    "narrative_reread": "judge",
}


def require(task_kind: str) -> TaskKind:
    """The registered task kind (``UnknownTaskKind`` otherwise)."""
    kind = load_routing_config().task_kinds.get(task_kind)
    if kind is None or task_kind not in LEGACY_TIER:
        raise UnknownTaskKind(task_kind)
    return kind
