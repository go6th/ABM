"""The Anthropic error mapping against the installed SDK's own exception classes (no network)."""

from __future__ import annotations

import anthropic
import httpx
import pytest

from llm.adapters.anthropic_api import map_error
from llm.errors import AuthFailed, ContextTooLong, ModelUnavailable, Overloaded

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
