"""Route each task to a model profile from config/models.yaml, so model choice stays reversible."""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.provider import Completion, CompletionRequest, LiveProviderUnavailable, Provider
from app.rules.schemas import ModelProfile, Models


@dataclass(frozen=True)
class Usage:
    task: str
    profile: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class ModelRouter:
    def __init__(
        self,
        models: Models,
        providers: dict[str, Provider],
        *,
        arm: str | None = None,
    ) -> None:
        self.models = models
        self.arm_name = arm or models.active_arm
        if self.arm_name not in models.arms:
            raise KeyError(f"unknown arm {self.arm_name!r}")
        self.providers = providers
        self.usage: list[Usage] = []

    def profile_for(self, task: str) -> tuple[str, ModelProfile]:
        routes = self.models.arms[self.arm_name].routes
        if task not in routes:
            raise KeyError(f"task {task!r} is not routed in arm {self.arm_name!r}")
        name = routes[task]
        return name, self.models.profiles[name]

    def complete(self, task: str, system: str, prompt: str) -> Completion:
        profile_name, profile = self.profile_for(task)
        provider = self.providers.get(profile.provider)
        if provider is None:
            raise LiveProviderUnavailable(
                f"no {profile.provider!r} provider configured; Phase 0 runs offline only"
            )
        result = provider.complete(
            CompletionRequest(task=task, model=profile.model, system=system, prompt=prompt)
        )
        self.usage.append(
            Usage(
                task=task,
                profile=profile_name,
                model=profile.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=cost_usd(profile, result.input_tokens, result.output_tokens),
            )
        )
        return result


def cost_usd(profile: ModelProfile, input_tokens: int, output_tokens: int) -> float | None:
    """Cost from configured prices; None while a price is not set, never a guess."""
    if profile.input_usd_per_mtok is None or profile.output_usd_per_mtok is None:
        return None
    return (
        input_tokens * profile.input_usd_per_mtok + output_tokens * profile.output_usd_per_mtok
    ) / 1_000_000
