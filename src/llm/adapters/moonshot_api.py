"""Kimi K3 by Moonshot AI: the OpenAI-compatible Chat Completions API (owner decision 2026-10-14).

Built from Moonshot's own documentation (read 2026-10-14; platform.moonshot.ai redirects to
platform.kimi.ai):

* ``POST https://api.moonshot.ai/v1/chat/completions`` with ``Authorization: Bearer <key>``
  (/docs/api/chat). The key comes from ``MOONSHOT_API_KEY``; it is held only in this object and is
  never logged, never sent to the browser and never put into a prompt.
* Request: ``model`` (``kimi-k3``), ``messages`` (``system`` and ``user``; content must not be
  empty), ``max_completion_tokens`` (``max_tokens`` is deprecated), ``reasoning_effort``
  (``low``, ``high`` or ``max``; ``kimi-k3`` always reasons and has no ``thinking`` field),
  ``response_format`` (``{"type": "json_schema", "json_schema": {"name", "schema", "strict"}}``),
  ``stream`` with ``stream_options.include_usage``.
* Response: ``choices[0].message.content``; ``usage.prompt_tokens``, ``completion_tokens``,
  ``prompt_tokens_details.cached_tokens`` (cache read) and ``cache_write_tokens``.
* Streaming: server-sent ``data:`` lines of ``chat.completion.chunk`` objects (text in
  ``choices[0].delta.content``), a last chunk with ``usage`` and empty ``choices``, then
  ``data: [DONE]``. An interrupted stream may lose the usage chunk; the caller then prices the
  call from its estimate.
* Errors (/docs/api/errors): 429 ``rate_limit_reached_error`` and ``engine_overloaded_error``, and
  500 / 503 / 504, are retryable (``Overloaded``: the node RetryPolicy retries, W6; this client
  never retries). 429 ``exceeded_current_quota_error`` is ``ProviderCreditsExhausted``. 401 and
  403 are ``AuthFailed``; 404 is ``ModelUnavailable``; 400 ``content_filter`` is
  ``ContentRefused``; a 400 naming the context window is ``ContextTooLong``.

The same OpenAI-compatible API is also served by Ollama Cloud (owner decision 2026-10-15: the
owner's Kimi K3 key is an Ollama Cloud key until Kimi moves to Google Cloud). Ollama differs in
three documented ways (docs.ollama.com/api/openai-compatibility, read 2026-10-15), selected by
``dialect="ollama"``: the base URL ``https://ollama.com/v1``, its own model tag (``kimi-k3:cloud``,
``MOONSHOT_MODEL``), and ``max_tokens`` (``max_completion_tokens`` is not documented there). The
result always names the catalogue's model id (``kimi-k3``), so prices and records stay one model.

What Kimi K3 does not have here, so nothing assumes it: server-side web search, citation blocks,
adaptive thinking and Anthropic prompt caching. As the research model it reaches the web only
through the platform's data tools and the ledger.

GUARANTEE(test_moonshot_requests_and_errors_follow_the_documentation)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from account_intelligence.platform.ports import LlmResult, LlmUsage
from llm.errors import (
    AuthFailed,
    ContentRefused,
    ContextTooLong,
    ModelUnavailable,
    Overloaded,
    ProviderCreditsExhausted,
)

BASE_URL = "https://api.moonshot.ai/v1"  # the registry default (MOONSHOT_BASE_URL)
DIALECTS = ("moonshot", "ollama")  # MOONSHOT_API_DIALECT
# Our effort levels onto Kimi K3's three (low, high, max; the documented default is max).
EFFORT = {"low": "low", "medium": "high", "high": "high", "xhigh": "max", "max": "max"}
RETRYABLE_429 = frozenset({"rate_limit_reached_error", "engine_overloaded_error"})


class MoonshotError(RuntimeError):
    """An unmapped Moonshot reply (status and error type only, never request text)."""


def map_status(status: int, body: Any) -> Exception:
    """The typed outcome of a non-2xx reply from the documented status and error type."""
    error = body.get("error") if isinstance(body, Mapping) else None
    kind = str(error.get("type") or "") if isinstance(error, Mapping) else ""
    message = str(error.get("message") or "") if isinstance(error, Mapping) else ""
    if status == 429:
        if kind == "exceeded_current_quota_error":
            return ProviderCreditsExhausted("model provider quota exhausted")
        return Overloaded("model rate limited or overloaded")
    if status in (500, 502, 503, 504):
        return Overloaded("model temporarily unavailable")
    if status in (401, 403):
        return AuthFailed("model provider refused the credentials")
    if status == 404:
        return ModelUnavailable("model not found at the provider")
    if status == 400 and kind == "content_filter":
        return ContentRefused("model refused the content")
    if status == 400 and ("context" in message.lower() or "too long" in message.lower()):
        return ContextTooLong("prompt is longer than the context window")
    return MoonshotError(f"moonshot replied {status} {kind or 'error'}")


def request_body(
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    *,
    effort: str | None = None,
    json_schema: Mapping[str, Any] | None = None,
    schema_name: str = "output",
    stream: bool = False,
    dialect: str = "moonshot",
) -> dict[str, Any]:
    """The documented Chat Completions body for one call (``dialect`` picks the token field)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or " "},
            {"role": "user", "content": user or " "},
        ],
        ("max_tokens" if dialect == "ollama" else "max_completion_tokens"): max_tokens,
    }
    if effort is not None:
        body["reasoning_effort"] = EFFORT.get(effort, "high")
    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": dict(json_schema), "strict": True},
        }
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def usage_of(raw: Any) -> LlmUsage:
    """Token counts from the documented usage object (zero when missing)."""
    usage = raw if isinstance(raw, Mapping) else {}
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("cached_tokens") or 0)
    written = int(details.get("cache_write_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    return LlmUsage(
        input_tokens=max(0, prompt - cached - written),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=written,
    )


class MoonshotClient:
    """One Moonshot account: completions and streams over one ``httpx.AsyncClient``."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 600.0,
        dialect: str = "moonshot",
        remote_model: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("MOONSHOT_API_KEY is not set")
        if dialect not in DIALECTS:
            raise ValueError(f"MOONSHOT_API_DIALECT must be one of {', '.join(DIALECTS)}")
        self.dialect = dialect
        # the provider's own name for the model (Ollama: kimi-k3:cloud); None sends our id
        self.remote_model = remote_model or None
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._url = base_url.rstrip("/") + "/chat/completions"
        # no transport retries: the node RetryPolicy owns retries (W6)
        self._http = http or httpx.AsyncClient(timeout=timeout_s)

    async def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        effort: str | None = None,
        json_schema: Mapping[str, Any] | None = None,
    ) -> LlmResult:
        """One completion; the text, the model that answered and its usage."""
        body = request_body(
            self.remote_model or model,
            system,
            user,
            max_tokens,
            effort=effort,
            json_schema=json_schema,
            dialect=self.dialect,
        )
        try:
            reply = await self._http.post(self._url, json=body, headers=self._headers)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise Overloaded("model connection failed") from error
        data = _json(reply)
        if reply.status_code >= 300:
            raise map_status(reply.status_code, data)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return LlmResult(
            text=str(message.get("content") or ""),
            # the catalogue id when the provider has its own tag (prices and records), else theirs
            model=model if self.remote_model else str(data.get("model") or model),
            usage=usage_of(data.get("usage")),
            extra={
                "provider": self.dialect,
                "stop_reason": choice.get("finish_reason"),
                "remote_model": str(data.get("model") or self.remote_model or model),
            },
        )

    async def stream(
        self,
        model: str,
        system: str,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int,
        effort: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """("text", delta) for each piece, then exactly one ("usage", LlmUsage)."""
        body = request_body(
            self.remote_model or model,
            system,
            "",
            max_tokens,
            effort=effort,
            stream=True,
            dialect=self.dialect,
        )
        body["messages"] = [{"role": "system", "content": system or " "}] + [
            {"role": m["role"], "content": m["content"] or " "} for m in messages
        ]
        usage = LlmUsage(0, 0)
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=self._headers
            ) as reply:
                if reply.status_code >= 300:
                    await reply.aread()
                    raise map_status(reply.status_code, _json(reply))
                # LOOP(moonshot stream): cap=the server's chunks until [DONE]; exit=[DONE], end
                async for line in reply.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    if chunk.get("usage"):
                        usage = usage_of(chunk["usage"])
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield "text", str(delta)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise Overloaded("model connection failed") from error
        yield "usage", usage

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()


def _json(reply: httpx.Response) -> Any:
    try:
        return reply.json()
    except ValueError:
        return {}
