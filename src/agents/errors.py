"""Typed outcomes of the agent layer. Messages carry codes and keys, never prompt or document text.

Every refusal is a ``GuardViolation`` naming the guard (G1 to G17) and a stable ``code``; callers
branch on the class or the code, never on the message.
"""

from __future__ import annotations


class AgentError(RuntimeError):
    """Base class of agent-layer errors."""

    code = "agent_error"


class AgentConfigInvalid(AgentError):
    """A config file under ``config/skill``, ``config/agents.yaml`` or ``config/lessons.yaml`` is
    invalid or unsourced."""

    code = "config_invalid"


class GuardViolation(AgentError):
    """A guard refused an action. ``guard`` is G1..G17, ``code`` is the stable reason."""

    def __init__(self, guard: str, code: str, detail: str = "") -> None:
        self.guard = guard
        self.code = code
        self.detail = detail
        super().__init__(f"{guard} {code}{': ' + detail if detail else ''}")


class InvalidMode(GuardViolation):
    """The run mode is not one of the three explicit values (D-102)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("G14", "invalid_mode", detail)


class NoPulledData(GuardViolation):
    """A build run has no usable pull manifest; ``missing`` lists the rows without data (G16)."""

    def __init__(self, missing: tuple[str, ...], detail: str = "") -> None:
        self.missing = missing
        super().__init__("G16", "no_pulled_data", detail or f"{len(missing)} row(s) missing")


class StalePullsNeedDecision(GuardViolation):
    """Pulls past their shelf life need an acknowledgement or a refresh decision (G16)."""

    def __init__(self, rows: tuple[str, ...]) -> None:
        self.rows = rows
        super().__init__("G16", "stale_pulls", f"{len(rows)} row(s) past shelf life")


class BudgetExceeded(GuardViolation):
    """An agent hit its token, tool-call or time budget (G13): a typed end, never a retry."""

    def __init__(self, agent: str, dimension: str) -> None:
        self.agent = agent
        self.dimension = dimension
        super().__init__("G13", "budget_exceeded", f"{agent} {dimension}")


class ToolNotAllowed(GuardViolation):
    """An agent asked for a tool outside its allow-list (registry contract)."""

    def __init__(self, agent: str, tool: str) -> None:
        super().__init__("G13", "tool_not_allowed", f"{agent} -> {tool}")


class OutputRejected(GuardViolation):
    """A model output failed its schema or a content guard; nothing from it is kept."""
