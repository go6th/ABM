"""``call_operation``: the only entry the research graph uses for a broker call.

Pipeline per page: validate args, check policy and URLs, budget reserve plus lease plus
intent (one transaction), circuit breaker, rate-limit acquire, execute, size and type
checks, store the raw payload under a neutral key, validate the schema, normalise,
record the cost, finalise the ledger. A replayed key skips everything after the claim
except parsing, so a replay works even while the provider's circuit is open.

Errors raised after the provider returned a 2xx body (schema drift, a refused hosted
file, a redirect off the allow-list) are recorded as paid ``done`` attempts: the money
was spent and replaying the stored body deterministically raises the same error.
``asyncio.CancelledError`` is never caught, so a cancelled call leaves its ``started``
intent for reconciliation and never a half-written payload (puts are atomic).
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from langgraph.types import RetryPolicy

from account_intelligence.connectors import obs
from account_intelligence.connectors.base import (
    OPERATION_PROVIDER,
    CallContext,
    Clock,
    Connector,
    ConnectorResult,
    Counts,
    Operation,
    ParsedPage,
    ProviderCode,
    RawResponse,
    ResultStatus,
    Sleep,
)
from account_intelligence.connectors.breaker import CircuitBreaker
from account_intelligence.connectors.cost import CostFigure, CostModel
from account_intelligence.connectors.errors import (
    CallInFlight,
    ConnectorError,
    NotSupportedYet,
    PolicyRefused,
    RateLimited,
    RetryableProviderError,
    SchemaDriftError,
    SecurityError,
    UnexpectedContentType,
)
from account_intelligence.connectors.http import HttpSettings, exchange
from account_intelligence.connectors.ledger import (
    CallKey,
    Claimed,
    InFlight,
    Ledger,
    Outcome,
    Replay,
    args_hash,
)
from account_intelligence.connectors.models import (
    ARGS_FOR,
    PAGE_READING,
    OperationArgs,
    ReadPageArgs,
    ScrapedPageRecord,
    ScrapeJobPageArgs,
)
from account_intelligence.connectors.paginate import (
    FetchedPage,
    PageLimits,
    PollLimits,
    digest,
    paginate,
    poll_job,
)
from account_intelligence.connectors.payloads import PayloadStore, payload_key
from account_intelligence.connectors.ratelimit import RateLimiter
from account_intelligence.connectors.security import UrlPolicy, check_final_url, validate_target_url
from account_intelligence.connectors.settings import ProvidersConfig

Hook = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ConnectorRuntime:
    """Everything a call needs, injected. One per worker process; no module state."""

    config: ProvidersConfig
    connectors: Mapping[ProviderCode, Connector]
    clients: Mapping[ProviderCode, httpx.AsyncClient]
    http: Mapping[ProviderCode, HttpSettings]
    limiter: RateLimiter
    breaker: CircuitBreaker
    ledger: Ledger
    payloads: PayloadStore
    cost: CostModel
    clock: Clock
    sleep: Sleep
    rng: random.Random
    url_policy: UrlPolicy
    worker_id: str
    # Fault-injection point for kill tests: runs after the provider responded and before
    # the ledger is finalised. None in production.
    after_response_hook: Hook | None = None
    page_limits_override: PageLimits | None = field(default=None)


@dataclass
class _PageCost:
    figure: CostFigure
    replayed: bool
    possible_duplicate: bool
    call_ref: str = ""  # ledger key (args_hash) of the page: tracker proof cites it (T2)


async def call_operation(
    op: Operation,
    args: OperationArgs,
    ctx: CallContext,
    rt: ConnectorRuntime,
    *,
    route: str | None = None,
    fell_back_from: ProviderCode | None = None,
) -> ConnectorResult:
    """The only entry the graph uses: validate, route, breaker, budget, ledger, pace, call, store,
    normalise."""
    provider = OPERATION_PROVIDER[op]
    expected = ARGS_FOR[op]
    if type(args) is not expected:
        raise SecurityError(f"{op} takes {expected.__name__}", provider_code=provider, operation=op)
    args = expected.model_validate(args.model_dump())  # re-validate: never trust a mutated copy
    connector = rt.connectors[provider]
    if not connector.supports(op):
        # Build once so the connector's own message (what to supply) is raised.
        connector.build_request(op, args, None)
        raise NotSupportedYet(provider_code=provider, operation=op)
    await _policy_checks(op, args, ctx, rt)

    costs: list[_PageCost] = []
    warnings: list[str] = []

    async def fetch(cursor: str | None, page_number: int) -> FetchedPage:
        """Fetch one page through the ledger (replay, claim, call, finalise)."""
        return await _fetch_page(
            op, args, ctx, rt, connector, provider, cursor, route, fell_back_from, costs, warnings
        )

    defaults = rt.config.defaults.pagination
    limits = rt.page_limits_override or PageLimits(
        defaults.max_pages, defaults.max_items, defaults.max_bytes
    )
    with obs.call_span(
        ctx.run_id, {"provider_code": provider, "operation": op, "route": route}
    ) as span:
        result = await paginate(fetch, limits, rt.clock, deadline=ctx.deadline)
        charged = sum(c.figure.charged_usd for c in costs)
        usd = (
            None
            if any(c.figure.usd is None for c in costs)
            else sum(c.figure.usd or 0.0 for c in costs)
        )
        credits = (
            None
            if any(c.figure.credits is None for c in costs)
            else sum(c.figure.credits or 0.0 for c in costs)
        )
        sources = {c.figure.cost_source for c in costs}
        cost_source = next(
            (s for s in ("unknown", "credits_only", "provider_reported") if s in sources),
            "documented",
        )
        status: ResultStatus = "done" if result.complete else "partial"
        span.set_attribute("outcome", status)
        if usd is not None:
            span.set_attribute("cost_usd", usd)
        span.set_attribute("cost_source", cost_source)
    obs.log_event(
        "connector.result",
        provider_code=provider,
        operation=op,
        route=route,
        outcome=status,
        pages=result.pages,
        items_read=result.items_read,
        items_kept=len(result.records),
        stop_reason=result.stop_reason,
        charged_usd=charged,
        cost_source=cost_source,
        run_id=ctx.run_id,
    )
    return ConnectorResult(
        status=status,
        records=tuple(result.records),
        raw_refs=tuple(result.raw_refs),
        counts=Counts(read=result.items_read, matched=len(result.records), pages=result.pages),
        stop_reason=result.stop_reason,
        cost_usd=usd,
        charged_usd=charged,
        credits=credits,
        cost_source=cost_source,
        provider_code=provider,
        operation=op,
        route=route,
        fell_back_from=fell_back_from,
        replayed=bool(costs) and all(c.replayed for c in costs),
        possible_duplicate=any(c.possible_duplicate for c in costs),
        call_refs=tuple(c.call_ref for c in costs),
        extra={"warnings": tuple(warnings)} if warnings else {},
    )


async def _policy_checks(
    op: Operation, args: OperationArgs, ctx: CallContext, rt: ConnectorRuntime
) -> None:
    provider = OPERATION_PROVIDER[op]
    if isinstance(args, ScrapeJobPageArgs) and args.mode != "plain":
        # Adapter rule: plain scrapes only. Expensive modes need an explicit cost reason
        # (enforced by the model) and still have no sourced response contract.
        raise NotSupportedYet(
            f"scrape mode {args.mode} is not available", provider_code=provider, operation=op
        )
    if op in PAGE_READING:
        assert isinstance(args, ReadPageArgs | ScrapeJobPageArgs)
        try:
            await validate_target_url(
                args.url,
                rt.url_policy,
                account_domain=ctx.account_domain,
                extra_allowed=ctx.extra_allowed_hosts,
            )
        except SecurityError as error:
            error.provider_code, error.operation = provider, op
            raise


async def _claim(
    rt: ConnectorRuntime, ctx: CallContext, key: CallKey, estimate: float, cost_known: bool = True
) -> Replay | Claimed:
    cfg = rt.config.defaults.ledger
    waited = 0.0
    # LOOP(in-flight wait): cap=in_flight_wait_s (polls every in_flight_poll_s); exit=claimed,
    # replayed, CallInFlight
    while True:
        claim = await rt.ledger.claim(ctx, rt.worker_id, key, estimate, cost_known=cost_known)
        if not isinstance(claim, InFlight):
            return claim
        if waited >= cfg.in_flight_wait_s:
            raise CallInFlight(attempt=claim.attempt)
        await rt.sleep(cfg.in_flight_poll_s)
        waited += cfg.in_flight_poll_s


def _provenance_base(
    provider: ProviderCode, op: Operation, ref: str, retrieved_at: datetime
) -> dict[str, Any]:
    return {
        "provider_code": provider,
        "operation": op,
        "raw_ref": ref,
        "retrieved_at": retrieved_at,
    }


def _post_checks(op: Operation, args: OperationArgs, parsed: ParsedPage, ctx: CallContext) -> None:
    if isinstance(args, ScrapeJobPageArgs):
        for record in parsed.records:
            assert isinstance(record, ScrapedPageRecord)
            check_final_url(args.url, record.final_url, ctx.account_domain, ctx.extra_allowed_hosts)


async def _fetch_page(
    op: Operation,
    args: OperationArgs,
    ctx: CallContext,
    rt: ConnectorRuntime,
    connector: Connector,
    provider: ProviderCode,
    cursor: str | None,
    route: str | None,
    fell_back_from: ProviderCode | None,
    costs: list[_PageCost],
    warnings: list[str],
) -> FetchedPage:
    key = CallKey(
        args_hash=args_hash(provider, op, {"args": args.model_dump(mode="json"), "cursor": cursor}),
        tool=f"{provider}:{op}",
        provider_code=provider,
        operation=op,
        route=route,
        fell_back_from=fell_back_from,
    )
    estimate = rt.cost.estimate(provider, op, args)
    claim = await _claim(rt, ctx, key, estimate.charged_usd, estimate.cost_source != "unknown")

    if isinstance(claim, Replay):
        meta = claim.response or {}
        if claim.response_ref is None:
            raise SchemaDriftError(
                "ledger row has no payload reference", provider_code=provider, operation=op
            )
        body = await rt.payloads.get(claim.response_ref)
        stored = RawResponse(
            200, str(meta.get("content_type", "application/json")), body, {}, 0, ""
        )
        base = _provenance_base(
            provider, op, claim.response_ref, datetime.fromisoformat(meta["retrieved_at"])
        )
        parsed = connector.parse(op, args, stored, base, cursor)
        _post_checks(op, args, parsed, ctx)
        costs.append(
            _PageCost(
                CostFigure(
                    float(claim.credits) if claim.credits is not None else None,
                    "credit",
                    float(claim.provider_cost_usd) if claim.provider_cost_usd is not None else None,
                    claim.cost_source or "unknown",
                    0.0,  # a replay charges nothing new
                ),
                replayed=True,
                possible_duplicate=claim.possible_duplicate,
                call_ref=key.args_hash,
            )
        )
        warnings.extend(parsed.warnings)
        obs.record_call(provider, op, "replayed", None)
        return FetchedPage(parsed, len(body), digest(body), claim.response_ref)

    raw: RawResponse | None = None
    ref: str | None = None
    retrieved_at: datetime | None = None
    paid_body_received = False
    try:
        await rt.breaker.before_call(provider)
        client = rt.clients[provider]
        settings = rt.http[provider]
        if claim.resume_job_id is not None:
            job_id: str | None = claim.resume_job_id
        else:
            await rt.limiter.acquire(provider)
            raw = await exchange(
                client,
                connector.build_request(op, args, cursor),
                settings,
                deadline=ctx.deadline,
                monotonic=rt.clock.monotonic,
            )
            retrieved_at = rt.clock.now()
            if rt.after_response_hook is not None:
                await rt.after_response_hook()
            _check_response(connector, op, raw, settings)
            paid_body_received = True
            ref = await rt.payloads.put(
                payload_key(ctx.tenant_id, ctx.account_id, ctx.run_id, raw.body), raw.body
            )
            parsed = connector.parse(
                op, args, raw, _provenance_base(provider, op, ref, retrieved_at), cursor
            )
            job_id = parsed.pending_job_id
            if job_id is not None:
                await rt.ledger.mark_submitted(ctx, rt.worker_id, key, claim, job_id)
                paid_body_received = False  # nothing is charged until the job succeeds
        if job_id is not None:
            raw, ref, retrieved_at, parsed = await _poll(
                op, args, ctx, rt, connector, provider, job_id, cursor
            )
            paid_body_received = True
        assert raw is not None and ref is not None and retrieved_at is not None
        _post_checks(op, args, parsed, ctx)
    except ConnectorError as error:
        error.provider_code = error.provider_code or provider
        error.operation = error.operation or op
        error.attempt = claim.attempt
        error.call_ref = key.args_hash
        if isinstance(error, RateLimited):
            await rt.limiter.note_retry_after(provider, error.retry_after)
        if paid_body_received and raw is not None and ref is not None and retrieved_at is not None:
            # 2xx body received and stored: a paid call whose content we refuse.
            if isinstance(error, SchemaDriftError):
                error.raw_ref = ref
            await _finalize_done(
                rt,
                ctx,
                key,
                claim,
                provider,
                op,
                args,
                raw,
                ref,
                retrieved_at,
                None,
                cursor,
                error_class=type(error).__name__,
            )
            await rt.breaker.record_neutral(provider)
            obs.record_call(provider, op, type(error).__name__, raw.latency_ms)
            raise
        charged = (
            claim.reserved_usd
            if (rt.config.provider(provider).bill_on_failure and raw is not None)
            else 0.0
        )
        await rt.ledger.finalize_failed(
            ctx,
            rt.worker_id,
            key,
            claim,
            Outcome(
                charged_usd=charged,
                http_status=error.http_status,
                latency_ms=raw.latency_ms if raw is not None else None,
                error_class=type(error).__name__,
            ),
        )
        if isinstance(error, RetryableProviderError):
            await rt.breaker.record_failure(provider)
        else:
            await rt.breaker.record_neutral(provider)
        obs.record_call(
            provider, op, type(error).__name__, raw.latency_ms if raw is not None else None
        )
        obs.log_event(
            "connector.error",
            provider_code=provider,
            operation=op,
            attempt=claim.attempt,
            error_class=type(error).__name__,
            http_status=error.http_status,
            run_id=ctx.run_id,
        )
        raise
    except Exception as error:
        # A defect in our own code after the claim: close the attempt, never leave money unrecorded.
        setattr(error, "call_ref", key.args_hash)  # noqa: B010 - foreign exception type
        await rt.ledger.finalize_failed(
            ctx,
            rt.worker_id,
            key,
            claim,
            Outcome(charged_usd=0.0, error_class=type(error).__name__),
        )
        raise

    figure = await _finalize_done(
        rt, ctx, key, claim, provider, op, args, raw, ref, retrieved_at, parsed, cursor
    )
    await rt.breaker.record_success(provider)
    costs.append(
        _PageCost(
            figure,
            replayed=False,
            possible_duplicate=claim.possible_duplicate,
            call_ref=key.args_hash,
        )
    )
    warnings.extend(parsed.warnings)
    if figure.usd is None:
        obs.record_unknown_cost(provider, op)
    obs.record_call(provider, op, "done", raw.latency_ms)
    return FetchedPage(parsed, len(raw.body), digest(raw.body), ref)


def _check_response(
    connector: Connector, op: Operation, raw: RawResponse, settings: HttpSettings
) -> None:
    # 202 is "accepted, in progress, retry soon" in the p_org error table: not a result.
    if not 200 <= raw.status_code < 300 or raw.status_code == 202:
        error = connector.classify_error(op, raw)
        if isinstance(error, ConnectorError):
            raise error
        raise RetryableProviderError(f"http {raw.status_code}")
    if raw.content_type not in settings.content_types:
        raise UnexpectedContentType(
            f"content type {raw.content_type or 'missing'}", http_status=raw.status_code
        )


async def _poll(
    op: Operation,
    args: OperationArgs,
    ctx: CallContext,
    rt: ConnectorRuntime,
    connector: Connector,
    provider: ProviderCode,
    job_id: str,
    cursor: str | None,
) -> tuple[RawResponse, str, datetime, ParsedPage]:
    jobs = rt.config.defaults.provider_jobs
    final: list[tuple[RawResponse, str, datetime, ParsedPage]] = []

    async def once() -> ParsedPage | None:
        await rt.limiter.acquire(provider)
        raw = await exchange(
            rt.clients[provider],
            connector.poll_request(op, job_id),
            rt.http[provider],
            deadline=ctx.deadline,
            monotonic=rt.clock.monotonic,
        )
        _check_response(connector, op, raw, rt.http[provider])
        # Status is read before storing: a pending poll is free and not kept.
        probe = connector.parse(
            op, args, raw, _provenance_base(provider, op, "pending", rt.clock.now()), cursor
        )
        if probe.pending_job_id is not None:
            return None
        retrieved_at = rt.clock.now()
        if rt.after_response_hook is not None:
            await rt.after_response_hook()
        ref = await rt.payloads.put(
            payload_key(ctx.tenant_id, ctx.account_id, ctx.run_id, raw.body), raw.body
        )
        parsed = connector.parse(
            op, args, raw, _provenance_base(provider, op, ref, retrieved_at), cursor
        )
        final.append((raw, ref, retrieved_at, parsed))
        return parsed

    await poll_job(
        once,
        PollLimits(jobs.poll_interval_s, jobs.poll_jitter_s, jobs.max_polls, jobs.total_timeout_s),
        rt.clock,
        rt.sleep,
        rt.rng,
        deadline=ctx.deadline,
    )
    return final[-1]


async def _finalize_done(
    rt: ConnectorRuntime,
    ctx: CallContext,
    key: CallKey,
    claim: Claimed,
    provider: ProviderCode,
    op: Operation,
    args: OperationArgs,
    raw: RawResponse,
    ref: str,
    retrieved_at: datetime,
    parsed: ParsedPage | None,
    cursor: str | None = None,
    *,
    error_class: str | None = None,
) -> CostFigure:
    figure = rt.cost.actual(
        provider,
        op,
        args,
        provider_credits=parsed.provider_credits if parsed is not None else None,
        document_returned=parsed.document_returned if parsed is not None else True,
    )
    await rt.ledger.finalize_done(
        ctx,
        rt.worker_id,
        key,
        claim,
        Outcome(
            charged_usd=figure.charged_usd,
            credits=figure.credits,
            provider_cost_usd=figure.usd,
            cost_source=figure.cost_source,
            http_status=raw.status_code,
            latency_ms=raw.latency_ms,
            bytes=len(raw.body),
            response={
                "retrieved_at": retrieved_at.isoformat(),
                "content_type": raw.content_type,
                "http_status": raw.status_code,
                # what was asked, so the stored payload can be re-parsed (grading adapters)
                "args": args.model_dump(mode="json"),
                "cursor": cursor,
                # tracker proof (T2, T3): records kept from this page; None when unknown
                "record_count": len(parsed.records) if parsed is not None else None,
                # a paid body we refused (schema drift): the call is a failure, not a success
                "error_class": error_class,
            },
            response_ref=ref,
        ),
    )
    return figure


def retry_policy_for(config: ProvidersConfig, provider: ProviderCode | None = None) -> RetryPolicy:
    """The node-level RetryPolicy (the single retry owner for broker 429 and 5xx).
    ``provider`` is accepted for a future per-provider override; values come from config."""
    r = config.defaults.retry
    return RetryPolicy(
        initial_interval=r.initial_interval_s,
        backoff_factor=r.backoff_factor,
        max_interval=r.max_interval_s,
        max_attempts=r.max_attempts,
        jitter=r.jitter,
        retry_on=RetryableProviderError,
    )


__all__ = ["ConnectorRuntime", "call_operation", "retry_policy_for", "PolicyRefused"]
