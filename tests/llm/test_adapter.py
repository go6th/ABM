"""The Anthropic adapter against the installed SDK's own exception classes (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from llm.adapters.anthropic_api import AnthropicAdapter, map_error
from llm.errors import AuthFailed, ContentRefused, ContextTooLong, ModelUnavailable, Overloaded
from tests.llm.conftest import candidate

REQ = httpx.Request("POST", "https://api.invalid/v1/messages")


def status_error(cls: type[anthropic.APIStatusError], code: int, msg: str = "x") -> Exception:
    return cls(msg, response=httpx.Response(code, request=REQ), body=None)


@pytest.mark.parametrize(
    ("exc", "typed"),
    [
        (status_error(anthropic.RateLimitError, 429), Overloaded),
        (status_error(anthropic.InternalServerError, 500), Overloaded),
        (anthropic.APITimeoutError(REQ), Overloaded),
        (anthropic.APIConnectionError(request=REQ), Overloaded),
        (status_error(anthropic.NotFoundError, 404), ModelUnavailable),
        (
            status_error(anthropic.BadRequestError, 400, "prompt is too long: 999 tokens"),
            ContextTooLong,
        ),
        (status_error(anthropic.AuthenticationError, 401), AuthFailed),
        (status_error(anthropic.PermissionDeniedError, 403), AuthFailed),
    ],
)
def test_anthropic_adapter_maps_sdk_errors_to_typed_classes(exc: Exception, typed: type) -> None:
    assert isinstance(map_error(exc), typed)


def test_an_unrelated_bad_request_is_not_mapped() -> None:
    exc = status_error(anthropic.BadRequestError, 400, "invalid field")
    assert map_error(exc) is exc


class Client:
    def __init__(self, outcome: Any) -> None:
        self.outcome, self.kwargs = outcome, {}
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def message(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model="synthetic-big",
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=7,
            cache_creation_input_tokens=None,
        ),
    )


@pytest.mark.asyncio
async def test_adapter_call_shape_cache_control_and_refusal() -> None:
    from llm.config import _candidate

    cand = _candidate("big", candidate("fam_a", "synthetic-big"))
    client = Client(message('{"a": 1}'))
    reply = await AnthropicAdapter(client).complete(
        cand, "sys", "usr", max_tokens=5, temperature=None, cache_prefix=True
    )
    assert reply.text == '{"a": 1}' and reply.usage.cache_read_tokens == 7
    assert client.kwargs["cache_control"] == {"type": "ephemeral"}
    assert client.kwargs["model"] == "synthetic-big" and "temperature" not in client.kwargs
    with pytest.raises(ContentRefused):
        await AnthropicAdapter(Client(message("", "refusal"))).complete(
            cand, "s", "u", max_tokens=5, temperature=None, cache_prefix=False
        )
    with pytest.raises(ValueError, match="temperature"):
        await AnthropicAdapter(client).complete(
            cand, "s", "u", max_tokens=5, temperature=0.0, cache_prefix=False
        )
    with pytest.raises(Overloaded):
        await AnthropicAdapter(Client(status_error(anthropic.RateLimitError, 429))).complete(
            cand, "s", "u", max_tokens=5, temperature=None, cache_prefix=False
        )
