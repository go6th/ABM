"""Route a research need to its first provider and, when allowed, its fallback.

Fallback happens only when the first provider is unavailable: ``CreditsExhausted``,
``CircuitOpen`` (an outage: the breaker opens when a node's retries are exhausted,
because its threshold equals the retry budget) or ``NotSupportedYet``. Never on
``SchemaDriftError`` or ``SecurityError``. Other errors go to the node's RetryPolicy.
A need with no available provider returns a Blocked result with a neutral reason.
Every fallback taken is recorded on the call (``route``, ``fell_back_from``).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from account_intelligence.connectors.base import (
    OPERATION_PROVIDER,
    CallContext,
    ConnectorResult,
    Operation,
    ProviderCode,
)
from account_intelligence.connectors.errors import (
    CircuitOpen,
    ConnectorError,
    CreditsExhausted,
    NotSupportedYet,
    PolicyRefused,
)
from account_intelligence.connectors.models import OperationArgs, ReadPageArgs, ScrapeJobPageArgs
from account_intelligence.connectors.service import ConnectorRuntime, call_operation

UNAVAILABLE = (CreditsExhausted, CircuitOpen, NotSupportedYet)

Translate = Callable[[OperationArgs], OperationArgs]


def _scrape_to_read(args: OperationArgs) -> OperationArgs:
    assert isinstance(args, ScrapeJobPageArgs)
    return ReadPageArgs(url=args.url)


# (need, fallback operation) -> argument translation. Only translations that keep the
# meaning of the request exist; anything else needs the planner and is unavailable.
TRANSLATIONS: dict[tuple[str, Operation], Translate] = {
    ("job_boards_and_pages", Operation.READ_PAGE): _scrape_to_read,
}


class Sink(StrEnum):
    INTERNAL_RAW = "internal_raw"  # neutral-key raw payload store
    INTERNAL_TABLE = "internal_table"  # internal ledger and fact tables
    CLIENT_EXPORT = "client_export"  # anything a client can download or view
    HOSTED_FILE = "hosted_file"  # a file hosted by a provider or a public URL


def assert_sink_allowed(provider: ProviderCode, sink: Sink) -> None:
    """Adapter rules: no provider output is ever written to a hosted file, and p_org
    output never reaches a client-visible store."""
    if sink is Sink.HOSTED_FILE:
        raise PolicyRefused(
            "provider output is never written to a hosted file", provider_code=provider
        )
    if provider is ProviderCode.P_ORG and sink is Sink.CLIENT_EXPORT:
        raise PolicyRefused(
            "p_org output never reaches a client-visible store", provider_code=provider
        )


def _blocked(
    reason: str, need: str, provider: ProviderCode | None, op: Operation | None
) -> ConnectorResult:
    return ConnectorResult(
        status="blocked", blocked_reason=reason, route=need, provider_code=provider, operation=op
    )


async def call_need(
    need: str,
    args: OperationArgs,
    ctx: CallContext,
    rt: ConnectorRuntime,
    *,
    operation: Operation | None = None,
) -> ConnectorResult:
    route = rt.config.route(need)
    if route.first.builtin is not None or route.first.provider is None:
        return _blocked(
            f"{need}: {route.first.builtin} is not part of the broker layer", need, None, None
        )
    first_op = route.first.operation or operation
    if first_op is None or OPERATION_PROVIDER[first_op] != route.first.provider:
        raise ValueError(f"{need} needs an operation of {route.first.provider}")
    try:
        return await call_operation(first_op, args, ctx, rt, route=need)
    except UNAVAILABLE as error:
        primary: ConnectorError = error
    fallback = route.fallback
    if fallback is None:
        return _blocked(
            f"{primary.blocked_reason}; no fallback", need, route.first.provider, first_op
        )
    if fallback.builtin is not None or fallback.operation is None:
        return _blocked(
            f"{primary.blocked_reason}; fallback {fallback.builtin} is a chat-only tool,"
            " unavailable here",
            need,
            route.first.provider,
            first_op,
        )
    translate = TRANSLATIONS.get((need, fallback.operation))
    if translate is None:
        return _blocked(
            f"{primary.blocked_reason}; fallback {fallback.provider}.{fallback.operation} needs "
            "planner-issued arguments",
            need,
            route.first.provider,
            first_op,
        )
    try:
        return await call_operation(
            fallback.operation,
            translate(args),
            ctx,
            rt,
            route=need,
            fell_back_from=route.first.provider,
        )
    except UNAVAILABLE as error:
        return _blocked(
            f"{primary.blocked_reason}; fallback: {error.blocked_reason}",
            need,
            fallback.provider,
            fallback.operation,
        )
