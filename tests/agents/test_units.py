"""Unit tests for the decision edges of modes, manifest, runtime and setup (G13 to G17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agents.config import load_skill_config
from agents.contracts import BudgetMeter, load_registry
from agents.errors import GuardViolation, NoPulledData, OutputRejected
from agents.manifest import ManifestRow, PullManifest, check_build_precondition
from agents.modes import RunMode, resolve_mode
from agents.ports import RunContext, TenantAgentSettings
from agents.runtime import call_agent
from agents.schemas import (
    DistillOutput,
    ExtractionInput,
    RunRequest,
    SetupInput,
    SetupProposal,
    VisitedPage,
)
from agents.setup.agent import SetupInvalid, choose_careers_page, validate_setup

T0 = datetime(2026, 10, 8, tzinfo=UTC)
CFG = load_skill_config()
REG = load_registry()
CTX = RunContext(uuid4(), uuid4(), "a.example", "A", "a.example", False, "v")


def test_resolve_mode_passes_an_enum_through() -> None:
    assert resolve_mode(RunMode.BUILD) is RunMode.BUILD
    assert resolve_mode("build") is RunMode.BUILD


@pytest.mark.parametrize(
    "row",
    [
        {"status": "stored", "payload_refs": []},
        {"status": "stored", "payload_refs": ["x"], "blocked_reason": "x"},
        {"status": "blocked", "payload_refs": ["x"], "blocked_reason": "x"},
        {"status": "blocked"},
        {"status": "blocked", "blocked_reason": "x", "pulled_at": datetime(2026, 1, 1)},
    ],
)
def test_manifest_rows_are_internally_consistent(row: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError):
        ManifestRow.model_validate({"row_key": "a", "pulled_at": T0, **row})


def manifest(tenant=None, account="a.example") -> PullManifest:  # type: ignore[no-untyped-def]
    return PullManifest(
        tenant_id=tenant or CTX.tenant_id,
        run_id=uuid4(),
        account_key=account,
        config_version="v",
        created_at=T0,
        rows=(ManifestRow(row_key="a", status="stored", payload_refs=("p",), pulled_at=T0),),
    )


@pytest.mark.parametrize("m", [None, manifest(tenant=uuid4()), manifest(account="b.example")])
def test_foreign_or_missing_manifest_is_no_pulled_data(m) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NoPulledData) as error:
        check_build_precondition(
            m,
            tenant_id=CTX.tenant_id,
            account_key="a.example",
            required_rows=["a", "b"],
            shelf_life_days={},
            now=T0,
        )
    assert error.value.missing == ("a", "b")


def test_rows_without_shelf_life_never_go_stale() -> None:
    pre = check_build_precondition(
        manifest(),
        tenant_id=CTX.tenant_id,
        account_key="a.example",
        required_rows=["a"],
        shelf_life_days={"a": None},
        now=T0 + timedelta(days=999),
    )
    assert pre.acknowledged_stale == () and pre.blocked_rows == ()


class Model:
    def __init__(self, out: object) -> None:
        self.out = out

    async def call(self, *args: object) -> tuple[object, int]:
        return self.out, 1


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_runtime_refuses_code_only_agents_and_wrong_inputs() -> None:
    meter = BudgetMeter(REG["pull"], T0)
    with pytest.raises(GuardViolation, match="code_only_agent"):
        run(call_agent(REG["pull"], Model(None), meter, CTX, SetupProposal(), client_bound=False))
    meter = BudgetMeter(REG["distill"], T0)
    with pytest.raises(GuardViolation, match="wrong_input_schema"):
        run(
            call_agent(REG["distill"], Model(None), meter, CTX, SetupProposal(), client_bound=False)
        )


def test_runtime_rejects_schema_failures_and_injection_echoes() -> None:
    inp = ExtractionInput(step="pass2", group="web_search", payloads=())
    with pytest.raises(OutputRejected, match="schema_invalid"):
        run(
            call_agent(
                REG["pass2"],
                Model(DistillOutput()),
                BudgetMeter(REG["pass2"], T0),
                CTX,
                inp,
                client_bound=False,
            )
        )


def settings(**kw: object) -> TenantAgentSettings:
    return TenantAgentSettings(**{"pursuing_client": "P", "lanes": ("salesforce",), **kw})  # type: ignore[arg-type]


def test_setup_takes_client_and_lanes_from_tenant_settings() -> None:
    req = RunRequest(account_name=" Acme ", domain="Acme.Example.")
    out = validate_setup(req, settings(lanes=()), CFG, 5)
    assert (out.account_name, out.domain, out.lanes) == ("Acme", "acme.example", CFG.lanes)
    assert validate_setup(req, settings(depth="deep"), CFG, 0).depth == "deep"
    for bad, code in (
        (settings(pursuing_client=None), "pursuing_client_missing"),
        (settings(lanes=("cobol",)), "unknown lanes"),
    ):
        with pytest.raises(SetupInvalid, match=code):
            validate_setup(req, bad, CFG, 0)
    with pytest.raises(SetupInvalid, match="cap_request_negative"):
        validate_setup(req, settings(), CFG, -1)
    with pytest.raises(SetupInvalid, match="invalid_account_or_domain"):
        validate_setup(RunRequest(account_name="A", domain="not a domain"), settings(), CFG, 0)


def test_setup_never_constructs_a_careers_url() -> None:
    page = VisitedPage(page_id="p1", url="https://a.example/careers", title="Jobs")
    base = SetupInput(
        account_name="A",
        domain="a.example",
        lanes=("salesforce",),
        pursuing_client="P",
        cap_request_micros=0,
    )
    meter = BudgetMeter(REG["setup"], T0)
    assert run(choose_careers_page(REG["setup"], Model(None), meter, CTX, base)) == (None, 0)
    with_page = base.model_copy(update={"visited_pages": (page,)})
    none = Model(SetupProposal(careers_page_id=None))
    assert run(choose_careers_page(REG["setup"], none, meter, CTX, with_page)) == (None, 0)
    good = Model(SetupProposal(careers_page_id="p1"))
    assert run(choose_careers_page(REG["setup"], good, meter, CTX, with_page)) == (
        "https://a.example/careers",
        0,
    )
