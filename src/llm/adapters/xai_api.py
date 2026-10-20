"""Grok 4.5 by xAI: the OpenAI-compatible Chat Completions API (owner decision 2026-10-20).

Built from xAI's own documentation (docs.x.ai, read 2026-10-20):

* ``POST https://api.x.ai/v1/chat/completions`` with ``Authorization: Bearer <key>``
  (model-capabilities/legacy/chat-completions: offered as a legacy endpoint beside the Responses
  API, no sunset date). The key comes from ``XAI_API_KEY``; it is held only in this object and is
  never logged, never sent to the browser and never put into a prompt.
* Request: ``model`` (the catalogue id; the Grok 4.5 model page: 500k context, function calling
  and structured outputs), ``messages``, ``max_tokens`` (the Chat Completions name of the output
  cap, text/comparison), ``reasoning_effort`` (guides/reasoning: ``low``, ``medium`` or ``high``
  on Grok 4.5, default ``high``; ``xhigh`` is ``high`` there; reasoning cannot be turned off),
  ``response_format`` (``json_schema``), OpenAI-style ``tools`` and ``stream``. ``stop`` and the
  presence and frequency penalties are refused by reasoning models and are never sent.
* Response: ``choices[0].message.content``; ``usage.prompt_tokens`` (cached tokens included),
  ``prompt_tokens_details.cached_tokens``, ``completion_tokens`` and
  ``completion_tokens_details.reasoning_tokens``. Reasoning tokens are counted apart from
  ``completion_tokens`` (prompt + completion + reasoning = total in the documented example) and
  billed at the output price, so they are added to the output here. xAI bills no cache write.
* Streaming (text/streaming): server-sent ``data:`` lines of ``chat.completion.chunk`` objects,
  each carrying the running ``usage`` (the last one is the total), then ``data: [DONE]``; tool
  calls arrive as ``delta.tool_calls`` and are assembled by index like Kimi K3's
  (``moonshot_api`` helpers: one ``ToolCall`` type, so the Ask tool loop is shared).
* Errors (key-information/debugging): 401 and 403 are ``AuthFailed`` (a 403 or 429 naming the
  team's credits or spending limit is ``ProviderCreditsExhausted``); 429 otherwise is the rate
  limit and 500 / 502 / 503 / 504 are retryable (``Overloaded``: the node RetryPolicy retries,
  W6; this client never retries); 404 is ``ModelUnavailable``; a 400 naming the context window
  or prompt length is ``ContextTooLong``. The error body is ``{"code", "error"}`` with the
  message as a string (an OpenAI-shaped ``{"error": {"message"}}`` is read too).

What Grok 4.5 does not have here, so nothing assumes it: batches, Anthropic cache blocks,
server-side web search (xAI offers it on the Responses API only) and citation blocks. As the
research model it reaches the web only through the platform's data tools and the ledger.

GUARANTEE(test_xai_requests_and_errors_follow_the_documentation)

GUARANTEE(test_grok_streams_tool_calls_and_their_results_go_back)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from account_intelligence.platform.ports import LlmResult, LlmUsage
from llm.adapters.moonshot_api import (
    ToolCall,
    _assemble,
    _collect,
    _json,
    _message,
    assistant_round,
    openai_tools,
    tool_result,
)
from llm.errors import (
    AuthFailed,
    ContentRefused,
    ContextTooLong,
    ModelUnavailable,
    Overloaded,
    ProviderCreditsExhausted,
)

__all__ = ["BASE_URL", "ToolCall", "XaiClient", "assistant_round", "tool_result"]

BASE_URL = "https://api.x.ai/v1"  # the registry default (XAI_BASE_URL)
# Our effort levels onto Grok 4.5's three (low, medium, high; the documented default is high).
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
CREDIT_WORDS = ("credit", "spending limit")


class XaiError(RuntimeError):
    """An unmapped xAI reply (status and error code only, never request text)."""


def _error_of(body: Any) -> tuple[str, str]:
    """The error code and message of a reply in either documented shape."""
    if not isinstance(body, Mapping):
        return "", ""
    error = body.get("error")
    if isinstance(error, Mapping):
        return str(error.get("code") or error.get("type") or ""), str(error.get("message") or "")
    return str(body.get("code") or ""), str(error or "")


def map_status(status: int, body: Any) -> Exception:
    """The typed outcome of a non-2xx reply from the documented status and message."""
    code, message = _error_of(body)
    text = f"{code} {message}".lower()
    if status in (403, 429) and any(word in text for word in CREDIT_WORDS):
        return ProviderCreditsExhausted("model provider credits exhausted")
    if status == 429:
        return Overloaded("model rate limited")
    if status in (500, 502, 503, 504):
        return Overloaded("model temporarily unavailable")
    if status in (401, 403):
        return AuthFailed("model provider refused the credentials")
    if status == 404:
        return ModelUnavailable("model not found at the provider")
    if status == 400 and "content_filter" in text:
        return ContentRefused("model refused the content")
    if status == 400 and any(w in text for w in ("context", "prompt length", "too long")):
        return ContextTooLong("prompt is longer than the context window")
    return XaiError(f"xai replied {status}")


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
    tools: Sequence[Mapping[str, Any]] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """The documented Chat Completions body for one call. ``tools`` are Anthropic-shaped,
    converted here; ``temperature`` (0 for a tabular turn) pins sampling, and 0 pins the
    sampler's ``seed`` too (the eval's determinism bar)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or " "},
            {"role": "user", "content": user or " "},
        ],
        "max_tokens": max_tokens,
    }
    if effort is not None:
        body["reasoning_effort"] = EFFORT.get(effort, "high")
    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": dict(json_schema), "strict": True},
        }
    if tools:
        body["tools"] = openai_tools(tools)
        body["tool_choice"] = "auto"
    if temperature is not None:
        body["temperature"] = temperature
        if temperature == 0:
            body["seed"] = 0
    if stream:
        body["stream"] = True
    return body


def usage_of(raw: Any) -> LlmUsage:
    """Token counts from the documented usage object (zero when missing); reasoning tokens are
    output (billed at the output price)."""
    usage = raw if isinstance(raw, Mapping) else {}
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return LlmUsage(
        input_tokens=max(0, prompt - cached),
        output_tokens=int(usage.get("completion_tokens") or 0) + reasoning,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
    )


class XaiClient:
    """One xAI account: completions and streams over one ``httpx.AsyncClient``. ``complete`` and
    ``stream`` take and yield what ``MoonshotClient``'s do, so the research port and the Ask
    tool loop drive either."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        if not api_key:
            raise ValueError("XAI_API_KEY is not set")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._url = base_url.rstrip("/") + "/chat/completions"
        # no transport retries: the node RetryPolicy owns retries (W6); reasoning streams are
        # slow to start, so the timeout is long (text/streaming)
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
        body = request_body(model, system, user, max_tokens, effort=effort, json_schema=json_schema)
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
            # the catalogue id (prices and records), whatever alias the provider answers with
            model=model,
            usage=usage_of(data.get("usage")),
            extra={
                "provider": "xai",
                "stop_reason": _stop_reason(choice.get("finish_reason")),
                "remote_model": str(data.get("model") or model),
            },
        )

    async def stream(
        self,
        model: str,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int,
        effort: str | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """("text", delta) for each piece; ("tools", [ToolCall]) once, when the round ends in
        tool calls (the caller replies through ``assistant_round`` plus one ``tool_result`` each
        in the next round's ``messages``); then exactly one ("usage", LlmUsage), the last
        running total the stream carried."""
        body = request_body(
            model,
            system,
            "",
            max_tokens,
            effort=effort,
            stream=True,
            tools=tools,
            temperature=temperature,
        )
        body["messages"] = [{"role": "system", "content": system or " "}] + [
            _message(m) for m in messages
        ]
        usage = LlmUsage(0, 0)
        pending: dict[int, dict[str, str]] = {}
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=self._headers
            ) as reply:
                if reply.status_code >= 300:
                    await reply.aread()
                    raise map_status(reply.status_code, _json(reply))
                # LOOP(xai stream): cap=the server's chunks until [DONE]; exit=[DONE], end
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
                        delta = choice.get("delta") or {}
                        if delta.get("tool_calls"):
                            _collect(pending, delta["tool_calls"])
                        elif delta.get("content"):
                            yield "text", str(delta["content"])
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise Overloaded("model connection failed") from error
        if pending:
            yield "tools", _assemble(pending)
        yield "usage", usage

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()


def _stop_reason(finish: Any) -> str | None:
    """The finish reason in the platform's words: ``length`` is a reply cut off at its token
    limit (``max_tokens``), which the schema re-ask doubles."""
    if finish is None:
        return None
    return "max_tokens" if finish == "length" else str(finish)
