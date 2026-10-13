"""The Anthropic port sends the configured effort and a cached system block; the request shape and
every model id come from config/models.yaml, and the active arm routes the judge away from the
extractor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform.llm import (
    AnthropicLlm,
    ModelRoute,
    judge_differs_from_generator,
    load_routes,
    request_kwargs,
)

RAW = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())


def test_every_route_is_allowlisted_and_the_judge_differs() -> None:
    routes = load_routes()
    for route in routes.values():
        assert route.model in RAW["allowlist"]
        assert route.model in RAW["models"], "every routed model is priced"
    assert judge_differs_from_generator(routes)
    arm = RAW["arms"][RAW["active_arm"]]["routes"]
    assert routes["extractor"].model == RAW["profiles"][arm["extraction"]]["model"]
    assert routes["extractor"].effort == RAW["task_settings"]["extraction"]["effort"]


def test_request_shape_carries_effort_and_one_cached_system_block() -> None:
    cached = ModelRoute("extractor", "anthropic", "m", effort="low", prompt_cache=True)
    kwargs = request_kwargs(cached, "SYSTEM", "USER", 100)
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == [{"role": "user", "content": "USER"}]
    plain = request_kwargs(ModelRoute("x", "anthropic", "m"), "SYSTEM", "USER", 100)
    assert plain["system"] == "SYSTEM" and "output_config" not in plain


def test_an_unknown_effort_or_unlisted_model_is_refused(tmp_path: Path) -> None:
    bad = dict(RAW, task_settings={**RAW["task_settings"], "extraction": {"effort": "turbo"}})
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError):
        load_routes(path)
    unlisted = dict(RAW, allowlist=[RAW["allowlist"][0]])
    path2 = tmp_path / "models2.yaml"
    path2.write_text(yaml.safe_dump(unlisted))
    with pytest.raises(ValueError):
        load_routes(path2)


class _Client:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

        class _Messages:
            async def create(inner, **kwargs: Any) -> Any:  # noqa: N805
                self.kwargs = kwargs

                class U:
                    input_tokens, output_tokens = 10, 2
                    cache_read_input_tokens, cache_creation_input_tokens = 7, 0

                class B:
                    type, text = "text", '{"ok": true}'

                class M:
                    content, usage, model, stop_reason = [B()], U(), kwargs["model"], "end_turn"

                return M()

        self.messages = _Messages()


@pytest.mark.asyncio
async def test_the_port_passes_the_knobs_through() -> None:
    client = _Client()
    llm = AnthropicLlm(client, load_routes(), "anthropic_api")
    result = await llm.complete("judge", "s", "u", max_tokens=50)
    route = load_routes()["judge"]
    assert client.kwargs["model"] == route.model
    assert client.kwargs["output_config"] == {"effort": route.effort}
    assert result.usage.cache_read_input_tokens == 7


def test_a_credit_balance_400_is_a_terminal_typed_error() -> None:
    from llm.adapters.anthropic_api import map_error
    from llm.errors import NEVER_FALLBACK, ProviderCreditsExhausted

    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://x.invalid"),
        json={"error": {"message": "Your credit balance is too low to access the Anthropic API."}},
    )
    exc = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API.", response=response, body=None
    )
    assert isinstance(map_error(exc), ProviderCreditsExhausted)
    assert "ProviderCreditsExhausted" in NEVER_FALLBACK
