"""Task kinds at the call sites (R2) and their legacy ``LlmPort`` tier.

Every model call in the product names a task kind registered in ``config/model_routing.yaml``.
With a router configured the call goes through it (selection, fallback, records, R3 to R11);
without one (hermetic tests, local development) it goes to the legacy ``LlmPort`` under the tier
the legacy adapters understand, listed here (transitional, OI-LLM-2).

GUARANTEE(test_every_model_call_site_names_a_registered_task_kind): R2 static scan.
"""

from __future__ import annotations

from typing import Any

from llm.config import TaskKind, load_routing_config
from llm.errors import UnknownTaskKind

# the largest limit a reply cut off at its task kind's limit is re-asked with: the SDK refuses a
# non-streamed call whose limit could outrun its ten-minute timeout (above about 21333 tokens),
# and research calls are not streamed
MAX_REASK_TOKENS = 21000

LEGACY_TIER: dict[str, str] = {
    "plan_gaps": "planner",
    "extract_facts": "extractor",
    "classify_posting": "classifier",
    "second_pass_posting": "judge",  # nosec B105 -- model role label, not a credential.
    "classify_person": "extractor",
    "lead_queries": "planner",
    "synthesise_hypotheses": "planner",
    "write_narrative": "planner",
    "distill": "planner",
    "support_judge": "judge",
    "source_reread": "judge",
    "narrative_reread": "judge",
    "file_summarize_section": "synthesis",
    "file_answer_question": "extractor",
    "file_extract_facts": "extractor",
    "abm_setup": "extractor",
    "abm_pass1_extract": "extractor",
    "abm_pass2_extract": "extractor",
    "abm_people_extract": "extractor",
    "abm_sense_check": "planner",
    "abm_distill": "planner",
}


def require(task_kind: str) -> TaskKind:
    """The registered task kind (``UnknownTaskKind`` otherwise)."""
    kind = load_routing_config().task_kinds.get(task_kind)
    if kind is None or task_kind not in LEGACY_TIER:
        raise UnknownTaskKind(task_kind)
    return kind


async def complete_task(port: Any, task_kind: str, system: str, user: str) -> Any:
    """One call on a legacy ``LlmPort`` for a registered task kind: the tier comes from
    ``LEGACY_TIER`` and the token limit from ``config/model_routing.yaml`` (R2, R7). The only way
    code outside ``workflow.deps`` reaches a model port."""
    kind = require(task_kind)
    return await port.complete(LEGACY_TIER[task_kind], system, user, max_tokens=kind.max_tokens)
