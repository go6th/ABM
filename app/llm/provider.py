"""Provider-agnostic model access (roadmap s12 LLM access, s6 model split benchmark)."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str
    model: str
    system: str
    prompt: str
    max_tokens: int = Field(default=4096, gt=0)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class Provider(Protocol):
    name: str

    def complete(self, request: CompletionRequest) -> Completion: ...


class LiveProviderUnavailable(RuntimeError):
    """Live model calls are not wired in Phase 0; they arrive with Phase 1 adapters."""
