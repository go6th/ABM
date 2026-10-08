"""One bounded, validated model call for an agent (G4, G9, G11, G13).

``call_agent``:

1. refuses an agent with no model (the pull agent, build, gate and delivery are code only);
2. charges one call against the agent's budget before the call and its tokens after it;
3. routes by the agent's task kind (split mode is routing, G11): the model port picks the tier;
4. treats a schema failure as a typed rejection (nothing from the output is kept);
5. rejects an output that echoes a canary or an instruction marker (injection echo, G14 of the
   grading spec) or, for client-bound output, names a tool, provider or person (G9).

Prompts are loaded from ``src/agents/<name>/prompt.md`` with their hash; prompt and document text
are never logged or put into an error.

GUARANTEE(test_runtime_rejects_schema_failures_and_injection_echoes)
GUARANTEE(test_prompts_and_documents_are_never_logged)
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.spans import casefold_text
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import GuardViolation, OutputRejected
from agents.naming import client_hits
from agents.ports import AgentModel, RunContext
from llm.errors import SchemaInvalid
from llm.prompts import prompt_hash

M = TypeVar("M", bound=BaseModel)
AGENTS_DIR = Path(__file__).parent


@lru_cache(maxsize=16)
def load_prompt(agent: str) -> tuple[str, str]:
    """``(text, sha256)`` of the agent's versioned prompt."""
    text = (AGENTS_DIR / agent / "prompt.md").read_text()
    return text, prompt_hash(text)


@lru_cache(maxsize=1)
def _markers() -> tuple[str, ...]:
    spans = load_grading_config().spans
    return tuple(m.casefold() for m in (*spans.canary_markers, *spans.instruction_markers))


def strings_of(value: Any) -> Iterator[str]:
    """Every string inside a model output (recursively)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from strings_of(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from strings_of(nested)


def echoes_injection(text: str) -> bool:
    """True when the text carries a canary or an instruction marker."""
    folded = casefold_text(text)
    return any(marker in folded for marker in _markers())


async def call_agent(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    payload: BaseModel,
    *,
    client_bound: bool,
) -> BaseModel:
    """See the module docstring. Returns the validated output of ``contract.output_schema``."""
    if contract.task_kind is None:
        raise GuardViolation("G5", "code_only_agent", contract.name)
    if not isinstance(payload, contract.input_schema):
        raise GuardViolation("G13", "wrong_input_schema", contract.name)
    text, digest = load_prompt(contract.name)
    meter.charge_call()
    try:
        output, tokens = await model.call(
            contract.task_kind,
            text,
            digest,
            payload.model_dump(mode="json"),
            contract.output_schema,
            ctx,
        )
    except (ValidationError, SchemaInvalid) as error:
        raise OutputRejected("G4", "schema_invalid", contract.name) from error
    meter.charge_tokens(tokens)
    if not isinstance(output, contract.output_schema):
        raise OutputRejected("G4", "schema_invalid", contract.name)
    texts = tuple(strings_of(output.model_dump(mode="json")))
    if any(echoes_injection(t) for t in texts):
        raise OutputRejected("G9", "injection_echo", contract.name)
    if client_bound and any(client_hits(t) for t in texts):
        raise OutputRejected("G9", "client_output_leak", contract.name)
    return output
