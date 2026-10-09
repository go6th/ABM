"""Inline guardrails: the semantic safety layer (OWASP LLM01).

System-prompt instructions alone are a proven failure
mode — they are bypassed by jailbreaking. A dedicated
classification layer sits transparently between the user
and the generator: every query is evaluated against a
safety taxonomy BEFORE it reaches the LLM, and every
answer is scanned before delivery.

The production classifier is a port (Llama Guard-class
models, NeMo Guardrails orchestration). The rule-based
implementation here is the deterministic floor: known
injection signatures, canary echoes and instruction
markers. A query that trips a rule is refused before
the model ever sees it; an unconfigured classifier is
a configuration error, not a pass-through.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

_INJECTION_PATTERNS = (
    re.compile(
        r"ignore\s+(?:all\s+)?(?:your\s+)?(?:previous|above)\s+(instructions?|prompts?)", re.I
    ),
    re.compile(r"forget\s+(your\s+)?(instructions?|rules?)", re.I),
    re.compile(r"repeat\s+(your\s+)?(system\s+)?(prompt|instructions?)", re.I),
    re.compile(r"print\s+(your\s+)?(system\s+)?(prompt|instructions?)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+are|like)\s+", re.I),
    re.compile(r"do\s+anything\s+now", re.I),
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"system\s*:\s*", re.I),
)
_REFUSAL_MARKERS = ("I must decline", "I cannot", "refused")


@dataclass(frozen=True)
class GuardHit:
    """One rule that fired: the pattern and where, never the text."""

    rule: str
    start: int


@dataclass(frozen=True)
class SafetyDecision:
    """The guardrail verdict: allow, refuse, or escalate."""

    allowed: bool
    reason: str | None = None


def detect_injection(text: str) -> tuple[GuardHit, ...]:
    """Every injection signature present in ``text``."""
    return tuple(
        GuardHit(pattern.pattern, match.start())
        for pattern in _INJECTION_PATTERNS
        for match in pattern.finditer(text)
    )


class GuardrailPort(Protocol):
    """The safety classifier (Llama Guard-class model)."""

    async def classify(self, text: str) -> SafetyDecision:
        """Allow, refuse or escalate ``text``."""
        ...


class RuleBasedGuardrail:
    """The deterministic floor: signatures, canaries, markers.

    Fails closed: a canary echo or an instruction marker
    (the grading layer's hostile-content rules) is a
    hostile input, and hostile input is refused before
    it reaches the generator.
    """

    def __init__(
        self,
        *,
        canary_markers: Iterable[str] = (),
        instruction_markers: Iterable[str] = (),
    ) -> None:
        """Configure the extra markers this deployment guards."""
        self.canary_markers = tuple(canary_markers)
        self.instruction_markers = tuple(instruction_markers)

    def classify_sync(self, text: str) -> SafetyDecision:
        """The synchronous classification (same rules as ``classify``)."""
        if detect_injection(text):
            return SafetyDecision(False, "prompt injection signature")
        folded = text.casefold()
        for marker in self.canary_markers:
            if marker.casefold() in folded:
                return SafetyDecision(False, "canary echo")
        for marker in self.instruction_markers:
            if marker.casefold() in folded:
                return SafetyDecision(False, "instruction echo")
        return SafetyDecision(True)

    async def classify(self, text: str) -> SafetyDecision:
        """Classify ``text`` against every rule, worst first."""
        return self.classify_sync(text)


def is_refusal(text: str) -> bool:
    """True when ``text`` already carries a safe refusal."""
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _REFUSAL_MARKERS)


def scan_output(text: str) -> tuple[GuardHit, ...]:
    """Scan a generated answer before delivery.

    The output side of the guardrail: injection attempts
    embedded in retrieved content that the model may
    echo back. A hit means the answer is rewritten into
    the refusal payload, never delivered as generated.
    """
    return detect_injection(text)


def enforce_output(text: str) -> str:
    """The answer, or the refusal payload when it is unsafe."""
    if scan_output(text):
        return "I must decline: the response could not be verified safe."
    return text


def scope_check(query: str, authorized_topics: Sequence[str]) -> bool:
    """True when the query stays inside the authorized scope.

    The refusal-accuracy gate's input: queries outside
    the deployment's topic scope must be refused, and
    this check is the deterministic part of that decision.
    """
    if not authorized_topics:
        return True
    folded = query.casefold()
    return any(topic.casefold() in folded for topic in authorized_topics)
