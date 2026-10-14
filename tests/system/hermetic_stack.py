"""The hermetic stack for end-to-end scenario tests (A2): API to worker to database. SYNTHETIC.

A real application (routes, guard, sessions, RLS) over faked external ports: the fake OIDC IdP
for sign-in, scripted synthetic providers (``tests.system.world``) behind the real connector
runtime and ledger, the deterministic ``FakeModel`` selected exactly as the container selects it
(``LLM_FAKE=hermetic``), the real export builder over the harness payload store, and the
in-process scheduler whose queued slices are delivered to the worker endpoint
``/tasks/run-slice`` of the same application (``ALLOW_UNAUTHENTICATED_TASKS=1``, test only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg

from account_intelligence.app.container import hermetic_fake_model
from account_intelligence.platform.hermetic import InProcessScheduler
from exports.wiring import build_export_builder
from tests.conftest import ADMIN_DSN, APP_DSN
from tests.connectors.conftest import Harness
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, make_app, uid
from tests.system.world import FAST_RETRY, SYNTHETIC_BALANCES, install

DOMAIN = "acme.example"


def admin(sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return list(cur.fetchall()) if cur.description else []


@dataclass
class Client:
    """A signed-in browser of one synthetic tenant."""

    tenant: UUID
    browser: Browser


@dataclass
class Stack:
    """One application, one harness, any number of signed-in tenants."""

    plane: Plane
    harness: Harness
    scenario: str
    tasks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def build(cls, harness: Harness, scenario: str = "happy", *, gate: Any = None) -> Stack:
        plane = make_app()
        install(harness)
        c = plane.app.state.container
        c.llm = hermetic_fake_model({"LLM_FAKE": "hermetic", "FAKE_MODEL_SCENARIO": scenario})
        c.connectors = harness.runtime()
        c.payloads = harness.payloads
        c.builder = build_export_builder(harness.payloads, c.clock, {}, "python-interim")
        c.gate = gate
        c.balances = SYNTHETIC_BALANCES
        c.extras["retry_policy"] = FAST_RETRY
        c.scheduler = InProcessScheduler()
        c.checkpoint_dsn = APP_DSN
        return cls(plane, harness, scenario)

    async def client(self, name: str, *, balance: str = "100", policy: Any = None) -> Client:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            tenant: UUID = c.execute(
                "INSERT INTO tenants (name, policy_json) VALUES (%s, %s) RETURNING id",
                (f"{name}-synthetic", json.dumps(policy or {"auto_approve_within_reserve": True})),
            ).fetchone()[0]
            c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, %s)", (tenant, balance))
        slug = f"{name}-{uid()}"
        add_oidc_connection(tenant, self.plane, slug)
        add_member(tenant, f"{slug}:root", "admin")
        browser = Browser(self.plane)
        done = await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
        assert done.status_code == 303, done.text
        return Client(tenant, browser)

    async def start(self, who: Client, domain: str = DOMAIN, **body: Any) -> str:
        """``POST /v1/runs`` and drive every queued slice through the worker endpoint."""
        # provider replies are stamped by the harness clock: keep it after the run's start
        self.harness.clock._now = datetime.now(UTC) + timedelta(seconds=5)
        started = await who.browser.call(
            "POST",
            "/v1/runs",
            json={"domain": domain, **body},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert started.status_code == 202, started.text
        run = str(started.json()["run_id"])
        await self.drain(who, run)
        return run

    async def drain(self, who: Client, run: str, limit: int = 200) -> None:
        """Deliver queued slices to ``/tasks/run-slice``; approve a cap the run waits on."""
        scheduler: InProcessScheduler = self.plane.app.state.container.scheduler
        worker = Browser(self.plane)
        try:
            for _ in range(limit):
                if not scheduler.queued:
                    if await self._approve_if_waiting(who, run):
                        continue
                    break
                tenant, run_id, slice_no = scheduler.queued.pop(0)
                reply = await worker.client.post(
                    "/tasks/run-slice",
                    json={"tenant_id": str(tenant), "run_id": str(run_id), "slice": slice_no},
                )
                self.tasks.append({"slice": slice_no, "status": reply.status_code})
                assert reply.status_code == 200, reply.text
        finally:
            await worker.close()

    async def _approve_if_waiting(self, who: Client, run: str) -> bool:
        status = admin("SELECT status, awaiting_kind FROM account_runs WHERE id = %s", run)
        if not status or status[0][0] != "AWAITING_APPROVAL":
            return False
        cap = await who.browser.call("GET", f"/v1/runs/{run}/cap")
        assert cap.status_code == 200, cap.text
        body = cap.json()
        ok = await who.browser.call(
            "POST",
            f"/v1/runs/{run}/cap/approve",
            json={"version": body["version"], "proposal_hash": body["proposal_hash"]},
        )
        assert ok.status_code == 200, ok.text
        return True

    async def close(self, *clients: Client) -> None:
        for c in clients:
            await c.browser.close()


def run_row(run: str) -> dict[str, Any]:
    (row,) = admin(
        "SELECT status, finished_reason, reserved_credits, spent_usd FROM account_runs"
        " WHERE id = %s",
        run,
    )
    return {"status": row[0], "reason": row[1], "reserved": row[2], "spent": row[3]}


def coverage(run: str) -> dict[str, tuple[Any, ...]]:
    return {
        r[0]: r[1:]
        for r in admin(
            "SELECT c.row_key, c.status, c.reason_code, c.proof_hash, c.priority"
            " FROM coverage_rows c WHERE c.run_id = %s",
            run,
        )
    }


def wallet(run: str) -> dict[str, Any]:
    """The run's credit entries on the client's pool by kind (reserve, settle, release)."""
    return dict(
        admin(
            "SELECT e.kind, e.credits FROM credit_entries e JOIN research_jobs j"
            " ON j.id = e.job_id WHERE j.run_id = %s",
            run,
        )
    )
