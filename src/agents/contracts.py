"""The agent registry (``config/agents.yaml``), tool allow-lists and budgets (D-95, G13).

* ``load_registry`` validates every agent: known typed tools only, schemas that exist, at least one
  guard, a task kind exactly when a model tier is set, and no model bound to steps 6 to 8.
* ``ToolBox`` exposes only the tools on an agent's allow-list; anything else is ``ToolNotAllowed``.
* ``BudgetMeter`` charges tokens, tool calls and wall time against the agent's budget; a breach is
  ``BudgetExceeded`` (a typed end, never a retry) and the exit reason is recorded.

GUARANTEE(test_registry_rejects_unknown_tools_schemas_and_missing_guards)
GUARANTEE(test_toolbox_refuses_tools_outside_the_allow_list)
GUARANTEE(test_budget_breach_is_a_typed_end)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from account_intelligence.config import CONFIG_DIR
from account_intelligence.dev_limits import UNCAPPED, dev_uncapped
from agents.errors import AgentConfigInvalid, BudgetExceeded, ToolNotAllowed
from agents.schemas import SCHEMAS

KNOWN_TOOLS = frozenset(
    {
        "settings.read",
        "payloads.read",
        "payloads.put",
        "ledger.call_operation",
        "facts.read",
        "credit.price",
    }
)
GUARD_IDS = frozenset(f"G{i}" for i in range(1, 18))
CODE_ONLY_STEPS = frozenset({6, 7, 8})


@dataclass(frozen=True)
class Budgets:
    """Per-agent limits (G13)."""

    tokens: int
    tool_calls: int
    seconds: int


@dataclass(frozen=True)
class AgentContract:
    """One registered agent."""

    name: str
    purpose: str
    step: int
    model_tier: str | None
    task_kind: str | None
    tools: frozenset[str]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    budgets: Budgets
    stop: tuple[str, ...]
    guards: tuple[str, ...]


def _contract(name: str, spec: Mapping[str, Any]) -> AgentContract:
    tools = frozenset(str(t) for t in spec.get("tools") or ())
    unknown = tools - KNOWN_TOOLS
    if unknown:
        raise AgentConfigInvalid(f"agent {name}: unknown tools {sorted(unknown)}")
    guards = tuple(str(g) for g in spec.get("guards") or ())
    if not guards or set(guards) - GUARD_IDS:
        raise AgentConfigInvalid(f"agent {name}: needs guards from G1..G17")
    for key in ("input_schema", "output_schema"):
        if spec.get(key) not in SCHEMAS:
            raise AgentConfigInvalid(f"agent {name}: unknown {key} {spec.get(key)!r}")
    tier, task = spec.get("model_tier"), spec.get("task_kind")
    if (tier is None) != (task is None):
        raise AgentConfigInvalid(f"agent {name}: model_tier and task_kind go together")
    step = int(spec["step"])
    if step in CODE_ONLY_STEPS and tier is not None:
        raise AgentConfigInvalid(f"agent {name}: step {step} is code only (no model)")
    b = spec["budgets"]
    budgets = Budgets(int(b["tokens"]), int(b["tool_calls"]), int(b["seconds"]))
    if min(budgets.tool_calls, budgets.seconds) <= 0 or budgets.tokens < 0:
        raise AgentConfigInvalid(f"agent {name}: budgets must be positive")
    if tier is not None and budgets.tokens <= 0:
        raise AgentConfigInvalid(f"agent {name}: a model agent needs a token budget")
    if dev_uncapped():  # local stack: no per-agent ceiling (a code-only agent keeps 0 tokens)
        budgets = Budgets(UNCAPPED if budgets.tokens else 0, UNCAPPED, UNCAPPED)
    return AgentContract(
        name=name,
        purpose=str(spec["purpose"]),
        step=step,
        model_tier=tier,
        task_kind=task,
        tools=tools,
        input_schema=SCHEMAS[spec["input_schema"]],
        output_schema=SCHEMAS[spec["output_schema"]],
        budgets=budgets,
        stop=tuple(str(s) for s in spec.get("stop") or ()),
        guards=guards,
    )


def load_registry(path: Path | None = None) -> dict[str, AgentContract]:
    """Validated contracts by agent name (see the module docstring)."""
    doc = yaml.safe_load((path or CONFIG_DIR / "agents.yaml").read_text())
    registry = {str(n): _contract(str(n), s) for n, s in (doc.get("agents") or {}).items()}
    if not registry:
        raise AgentConfigInvalid("no agents registered")
    for item in doc.get("code_only_steps") or ():
        if int(item["step"]) not in CODE_ONLY_STEPS:
            raise AgentConfigInvalid(f"step {item['step']} is not a code-only step")
    return registry


@dataclass
class BudgetMeter:
    """Charges one agent's work in one run against its budget."""

    contract: AgentContract
    started_at: datetime
    tokens: int = 0
    tool_calls: int = 0
    exit_reason: str | None = field(default=None)

    def _breach(self, dimension: str) -> BudgetExceeded:
        self.exit_reason = f"budget_{dimension}"
        return BudgetExceeded(self.contract.name, dimension)

    def charge_call(self) -> None:
        """Count one tool or model call; refuse the call that would exceed the limit."""
        if self.tool_calls + 1 > self.contract.budgets.tool_calls:
            raise self._breach("tool_calls")
        self.tool_calls += 1

    def charge_tokens(self, used: int) -> None:
        """Add tokens a finished call used; a breach ends the agent (the output is discarded)."""
        self.tokens += max(0, used)
        if self.tokens > self.contract.budgets.tokens:
            raise self._breach("tokens")

    def check_time(self, now: datetime) -> None:
        """Refuse further work after the time budget."""
        if (now - self.started_at).total_seconds() > self.contract.budgets.seconds:
            raise self._breach("seconds")


class ToolBox:
    """The only tools an agent's code path may reach (typed callables, allow-listed)."""

    def __init__(
        self,
        contract: AgentContract,
        tools: Mapping[str, Callable[..., Awaitable[Any]]],
        meter: BudgetMeter,
    ) -> None:
        extra = set(tools) - contract.tools
        if extra:
            raise ToolNotAllowed(contract.name, ",".join(sorted(extra)))
        self._contract, self._tools, self._meter = contract, dict(tools), meter

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call an allowed tool, charging the budget first."""
        if name not in self._contract.tools or name not in self._tools:
            raise ToolNotAllowed(self._contract.name, name)
        self._meter.charge_call()
        return await self._tools[name](*args, **kwargs)
