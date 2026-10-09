"""SYNTHETIC whole-run world: a fake account (``acme.example``), scripted providers, a scripted LLM.

Provider replies reuse the recorded cassette SHAPES (docs/providers/contract/cassettes) with
synthetic content, so the real connector parsers, ledger, budget gate and payload store run.
The LLM is ``ScriptedLlm``: the planner proposes calls from the operation menu it is shown, the
extractor copies the first sentence of a record exactly, the classifier and judge answer in
schema. ``lie`` switches the extractor to a lying generator (fabricated URL, paraphrase, a grade
field, the canary) for the whole-run hallucination test (W2).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import psycopg
from langgraph.types import RetryPolicy

from account_intelligence.connectors.base import ProviderCode
from account_intelligence.connectors.errors import RetryableProviderError
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.checkpoint import checkpointer_factory
from account_intelligence.platform.hermetic import InProcessScheduler, ScriptedLlm
from account_intelligence.runs.lifecycle import RunLimits, RunRequest, create_run
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.runner import SliceSettings, execute_slice
from account_intelligence.workflow.spec import load_spec
from tests.connectors.conftest import CASSETTES, Harness, Reply
from tests.tracker.world import admin_dsn, tracker

DOMAIN = "acme.example"
PAD = (
    " This synthetic posting text exists only to test the pipeline and describes no real role." * 3
)
CANARY = "CANARY-7f3a9e"
LEDGER_SUM = (
    "SELECT COALESCE(SUM(e.charged_usd) FILTER (WHERE e.event IN ('done', 'failed', 'abandoned')),"
    " 0) + COALESCE(SUM(e.reserved_usd) FILTER (WHERE e.event = 'started' AND NOT EXISTS (SELECT 1"
    " FROM tool_call_events t WHERE t.run_id = e.run_id AND t.args_hash = e.args_hash AND"
    " t.attempt = e.attempt AND t.event IN ('done', 'failed', 'abandoned'))), 0)"
    " FROM tool_call_events e WHERE e.run_id = %s"
)  # connectors.ledger.Ledger.ledger_sum: abandoned (possibly paid) attempts count as spend
# The production node RetryPolicy shape (retry_on RetryableProviderError, 3 attempts) with
# millisecond intervals so tests do not sleep.
FAST_RETRY = RetryPolicy(
    initial_interval=0.001,
    backoff_factor=1.0,
    max_interval=0.001,
    max_attempts=3,
    jitter=False,
    retry_on=RetryableProviderError,
)


def _cassette(code: str, name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((CASSETTES / code / f"{name}.json").read_text())["response"][
        "body"
    ]
    return copy.deepcopy(body)


def search_reply(request: httpx.Request) -> Reply:
    body = json.loads(request.content)
    if request.url.path.endswith("/extract"):
        out = _cassette("p_search", "read_page")
        url = body["urls"][0]
        out["errors"] = []
        out["results"] = [
            {
                "url": url,
                "title": "Acme Example careers",
                "excerpts": ["Acme Example careers page."],
                "full_content": "Acme Example careers page. Open roles are listed here.",
            }
        ]
        return Reply(body=out)
    query = " ".join(body.get("search_queries") or [])
    domains = (
        (body.get("advanced_settings") or {}).get("source_policy", {}).get("include_domains", [])
    )
    out = _cassette("p_search", "web_search")
    if "careers" in query and DOMAIN in domains:
        hit = {
            "url": f"https://{DOMAIN}/careers",
            "title": "Careers at Acme Example",
            "excerpts": ["Join Acme Example."],
            "publish_date": None,
        }
    elif query.endswith(" jobs"):
        hit = {
            "url": f"https://{DOMAIN}/careers/jobs/s1",
            "title": "Salesforce Developer",
            "excerpts": ["Build Salesforce triggers."],
            "publish_date": None,
        }
    else:
        hit = {
            "url": f"https://news.{DOMAIN}/story-1",
            "title": "Acme Example expands",
            "excerpts": ["Acme Example opened a new engineering office in Pune in 2026."],
            "publish_date": "2026-09-01",
        }
    out["results"] = [hit]
    return Reply(body=out)


def signals_reply(request: httpx.Request) -> Reply:
    path = request.url.path
    if path.endswith("job_openings"):
        out = _cassette("p_signals", "job_openings")
        item = out["data"][0]
        attrs = item["attributes"]
        attrs.update(
            title="Salesforce Developer",
            normalized_title="Salesforce Developer",
            description="You will build Salesforce Apex triggers as a developer." + PAD,
            url=f"https://{DOMAIN}/careers/jobs/s1",
            status="open",
            posted_at="2026-08-01",
            first_seen_at="2026-08-01T00:00:00Z",
            location="Pune, India",
        )
        item["id"] = "s1"
        out["data"] = [item]
    elif path.endswith("news_events"):
        out = _cassette("p_signals", "news_events")
        attrs = out["data"][0]["attributes"]
        attrs.update(
            summary="Acme Example opens an engineering office in Pune.",
            article_sentence="Acme Example opened a new engineering office in Pune in 2026.",
            found_at="2026-09-01T00:00:00Z",
        )
        out["data"] = out["data"][:1]
    else:
        out = _cassette("p_signals", "tech_detections")
        out["data"] = out["data"][:1]
    out.setdefault("meta", {})["count"] = len(out["data"])
    return Reply(body=out)


def scrape_reply(request: httpx.Request) -> Reply:
    body = json.loads(request.content)
    out = _cassette("p_scrape", "scrape_job_page")
    out["data"]["markdown"] = "You will build Salesforce Apex triggers as a developer." + PAD
    out["data"]["html"] = None
    out["data"].setdefault("metadata", {}).update(
        sourceURL=body["url"], url=body["url"], statusCode=200, title="Salesforce Developer"
    )
    return Reply(body=out)


def install(harness: Harness) -> None:
    """Route every provider to its synthetic replies (p_people stays unsupported, p_org unused)."""
    harness.providers[ProviderCode.P_SEARCH].fallback = search_reply
    harness.providers[ProviderCode.P_SIGNALS].fallback = signals_reply
    harness.providers[ProviderCode.P_SCRAPE].fallback = scrape_reply


def _first_sentence(text: str) -> str:
    match = re.search(r"[^.!?\n]+[.!?]", text)
    return (match.group() if match else text).strip()


@dataclass
class Script:
    """The scripted LLM's behaviour. ``lie`` makes the extractor fabricate."""

    lie: bool = False
    calls: dict[str, int] = field(default_factory=dict)

    def __call__(self, tier: str, system: str, user: str) -> str:
        self.calls[tier] = self.calls.get(tier, 0) + 1
        data = json.loads(user.split("\nYour previous", 1)[0])
        if tier == "planner" and "rows" in data:
            calls = []
            for row in data["rows"]:
                ops = data["operations"]
                if "web_search" in ops:
                    query = f"acme example {row['label'][:60]}"
                    calls.append(
                        {"row": row["key"], "operation": "web_search", "args": {"queries": [query]}}
                    )
                elif "tech_detections" in ops:
                    calls.append(
                        {
                            "row": row["key"],
                            "operation": "tech_detections",
                            "args": {"domain": DOMAIN},
                        }
                    )
            return json.dumps({"calls": calls[: data["call_limit"]]})
        if tier == "planner":  # lead queries
            return json.dumps(
                {
                    "calls": [
                        {"operation": "web_search", "args": {"queries": [q]}}
                        for q in ("acme example pune office", "engineering expansion india acme")
                    ]
                }
            )
        if tier == "extractor":
            claims = []
            for i, record in enumerate(data["records"][:2]):
                sentence = _first_sentence(record["text"])
                value = " ".join(sentence.split()[:3])
                claim: dict[str, Any] = {
                    "claim_kind": "sourced",
                    "local_id": f"c{i}",
                    "attribute_key": f"{data['attribute_prefix']}.fact_{i}",
                    "value": value,
                    "evidence": [{"url": record["url"], "supporting_sentence": sentence}],
                }
                if self.lie:
                    claim = {
                        **claim,
                        "evidence": [
                            {
                                "url": "https://made-up.example/x",
                                "supporting_sentence": "Acme doubled revenue.",
                            }
                        ],
                        "grade": "confirmed",
                    }
                claims.append(claim)
            if self.lie:
                claims.append(
                    {
                        "claim_kind": "sourced",
                        "local_id": "canary",
                        "attribute_key": f"{data['attribute_prefix']}.x",
                        "value": CANARY,
                        "evidence": [
                            {"url": data["records"][0]["url"], "supporting_sentence": CANARY}
                        ],
                    }
                )
            return json.dumps({"claims": claims})
        if tier in ("classifier", "judge") and "posting_id" in data:
            return json.dumps(
                {
                    "posting_id": data["posting_id"],
                    "label": "build",
                    "evidence_sentence": _first_sentence(data["text"]),
                }
            )
        if tier == "judge":
            return json.dumps({"supported": True, "reason": "the sentence states it"})
        return "{}"


def tenant(balance: str = "100", auto_approve: bool = True) -> UUID:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        policy = json.dumps({"auto_approve_within_reserve": auto_approve})
        t = c.execute(
            "INSERT INTO tenants (name, policy_json) VALUES ('synthetic', %s) RETURNING id",
            (policy,),
        ).fetchone()[0]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, %s)", (t, balance))
    return UUID(str(t))


@dataclass
class Run:
    tenant: UUID
    run: UUID
    scheduler: InProcessScheduler
    llm: ScriptedLlm
    script: Script


async def start_run(
    harness: Harness,
    script: Script | None = None,
    *,
    auto_approve: bool = True,
    run_input: dict[str, Any] | None = None,
    settings: SliceSettings | None = None,
    spec: Any | None = None,
) -> Run:
    """Create a run through the lifecycle and wire an in-process scheduler to ``execute_slice``.
    ``spec`` overrides the workflow spec (for example ``execution_mode: parallel``)."""
    from tests.conftest import APP_DSN

    install(harness)
    script = script or Script()
    llm = ScriptedLlm(script)
    t = tenant(auto_approve=auto_approve)
    async with tenant_txn(t) as s:
        created = await create_run(
            s,
            t,
            RunRequest(DOMAIN, "standard", f"k-{t}", run_input or {}),
            # created budget $20: the credit cap proposal for the full workflow plan is about $9.75
            # (credit-priced calls are held at the $0.25 reserve), and a tenant policy may only
            # auto-approve a cap within the created budget (spend, proposal D-28)
            RunLimits("v1", Decimal("20.00"), 60, Decimal("6.0")),
            tracker(s),
        )
    runtime = harness.runtime()
    spec = spec or load_spec()

    async def deps_factory(tenant_id: UUID, run_id: UUID) -> RunDeps:
        async with tenant_txn(tenant_id) as s:
            from sqlalchemy import text

            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, a.domain, r.run_input, a.careers_url FROM"
                        " account_runs r"
                        " JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                    ),
                    {"r": run_id},
                )
            ).one()
        hosts = frozenset({DOMAIN, f"news.{DOMAIN}"})
        return RunDeps(
            tenant_id=tenant_id,
            run_id=run_id,
            account_id=row.account_id,
            domain=row.domain,
            txn=tenant_txn,
            llm=llm,
            payloads=harness.payloads,
            clock=harness.clock,
            spec=spec,
            connectors=runtime,
            canary=CANARY,
            extra_allowed_hosts=hosts,
            run_input=dict(row.run_input or {}),
            balances=SYNTHETIC_BALANCES,
        )

    scheduler = InProcessScheduler()
    saver = checkpointer_factory(APP_DSN)

    async def run_slice(tenant_id: UUID, run_id: UUID, slice_no: int) -> None:
        await execute_slice(
            deps_factory,
            saver,
            scheduler,
            tenant_id,
            run_id,
            slice_no,
            settings=settings,
            retry_policy=FAST_RETRY,
        )

    scheduler.runner = run_slice
    await scheduler.enqueue_slice(t, created.run_id, 0)
    return Run(t, created.run_id, scheduler, llm, script)


class _SyntheticBalances:
    """Every synthetic provider reports an ``ok`` balance (credit cap K15 preflight)."""

    async def balance(self, provider_code: str) -> Any:
        """An ``ok`` snapshot taken now on the harness clock's date."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from spend.models import BalanceSnapshot

        return BalanceSnapshot(
            provider_code=provider_code,
            status="ok",
            balance=Decimal("1000"),
            unit="credit",
            source="synthetic",
            taken_at=datetime.now(UTC),
        )


SYNTHETIC_BALANCES = _SyntheticBalances()


def admin(sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        return list(c.execute(sql, params).fetchall())


@asynccontextmanager
async def noop() -> AsyncIterator[None]:
    yield None


Unused = Callable[[], None]


async def drive(
    harness: Harness,
    script: Script,
    tenant_id: UUID,
    run_id: UUID,
    *,
    worker_id: str = "worker-a",
    after_response_hook: Any = None,
    settings: SliceSettings | None = None,
    builder: Any | None = None,
    graph_factory: Any | None = None,
) -> InProcessScheduler:
    """Run an EXISTING run's slices to the end with this harness (kill/resume and concurrency
    tests). The payload store is the harness's (a shared LocalPayloadStore across processes)."""
    from tests.conftest import APP_DSN

    install(harness)
    llm = ScriptedLlm(script)
    overrides: dict[str, Any] = {}
    if after_response_hook is not None:
        overrides["after_response_hook"] = after_response_hook
    runtime = harness.runtime(worker_id=worker_id, **overrides)
    spec = load_spec()

    async def deps_factory(t: UUID, r: UUID) -> RunDeps:
        async with tenant_txn(t) as s:
            from sqlalchemy import text

            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, a.domain, r.run_input FROM account_runs r"
                        " JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                    ),
                    {"r": r},
                )
            ).one()
        return RunDeps(
            tenant_id=t,
            run_id=r,
            account_id=row.account_id,
            domain=row.domain,
            txn=tenant_txn,
            llm=llm,
            payloads=harness.payloads,
            clock=harness.clock,
            spec=spec,
            connectors=runtime,
            builder=builder,
            canary=CANARY,
            extra_allowed_hosts=frozenset({DOMAIN, f"news.{DOMAIN}"}),
            run_input=dict(row.run_input or {}),
            balances=SYNTHETIC_BALANCES,
        )

    scheduler = InProcessScheduler()
    saver = checkpointer_factory(APP_DSN)

    async def run_slice(t: UUID, r: UUID, n: int) -> None:
        await execute_slice(
            deps_factory,
            saver,
            scheduler,
            t,
            r,
            n,
            settings=settings,
            retry_policy=FAST_RETRY,
            graph_factory=graph_factory,
        )

    scheduler.runner = run_slice
    await scheduler.enqueue_slice(tenant_id, run_id, 0)
    await scheduler.drain()
    return scheduler


async def create_only(harness: Harness, auto_approve: bool = True) -> tuple[UUID, UUID]:
    """A tenant with a wallet and a created run (nothing executed)."""
    t = tenant(auto_approve=auto_approve)
    async with tenant_txn(t) as s:
        created = await create_run(
            s,
            t,
            RunRequest(DOMAIN, "standard", f"k-{t}", {}),
            # created budget $20: the credit cap proposal for the full workflow plan is about $9.75
            # (credit-priced calls are held at the $0.25 reserve), and a tenant policy may only
            # auto-approve a cap within the created budget (spend, proposal D-28)
            RunLimits("v1", Decimal("20.00"), 60, Decimal("6.0")),
            tracker(s),
        )
    return t, created.run_id
