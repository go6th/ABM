"""Unit tests for the decision edges of modes, manifest, runtime and setup (G13 to G17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from account_intelligence.connectors.registry_internal import vendor_names
from account_intelligence.connectors.settings import load_providers_config
from agents.config import load_skill_config
from agents.contracts import BudgetMeter, load_registry
from agents.errors import GuardViolation, NoPulledData, OutputRejected
from agents.manifest import ManifestRow, PullManifest, check_build_precondition
from agents.modes import RunMode, resolve_mode
from agents.naming import third_party_domain_hits
from agents.ports import RunContext, TenantAgentSettings
from agents.runtime import call_agent
from agents.schemas import (
    Claim,
    DistillInput,
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
        rows=(
            ManifestRow(
                row_key="a", status="stored", payload_refs=("p",), call_refs=("c",), pulled_at=T0
            ),
        ),
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


def test_client_bound_model_output_is_scrubbed_then_scanned() -> None:
    inp = DistillInput(account_name="A", audience="client", facts=())
    vendor = next(
        name for name in vendor_names(load_providers_config()).values() if name != "Parallel"
    )
    output = DistillOutput(
        narrative=(Claim(text=f"{vendor} found a useful source", fact_ids=("f1",)),)
    )
    clean = run(
        call_agent(
            REG["distill"],
            Model(output),
            BudgetMeter(REG["distill"], T0),
            CTX,
            inp,
            client_bound=True,
        )
    )
    assert clean.narrative[0].text == "6th agents found a useful source"

    from urllib.parse import urlsplit

    provider = next(iter(load_providers_config().providers.values()))
    domain = urlsplit(provider.base_url).hostname
    domain_output = DistillOutput(
        narrative=(Claim(text=f"See {vendor} at {domain} for details", fact_ids=("f1",)),)
    )
    with pytest.raises(OutputRejected, match="third_party_domain"):
        run(
            call_agent(
                REG["distill"],
                Model(domain_output),
                BudgetMeter(REG["distill"], T0),
                CTX,
                inp,
                client_bound=True,
            )
        )
    assert third_party_domain_hits(domain or "")


def test_client_json_answer_scrubs_tool_names_and_blocks_provider_domains() -> None:
    from urllib.parse import urlsplit

    from account_intelligence.api.errors import APIProblem
    from account_intelligence.api.routes.account_workspace import _client_value
    from account_intelligence.connectors.settings import load_providers_config

    vendor = next(
        name for name in vendor_names(load_providers_config()).values() if name != "Parallel"
    )
    clean = _client_value({"answer": f"{vendor} found three matching rows"})
    assert clean == {"answer": "6th agents found three matching rows"}

    host = urlsplit(next(iter(load_providers_config().providers.values())).base_url).hostname
    with pytest.raises(APIProblem) as error:
        _client_value({"answer": f"{vendor} used https://{host}/search"})
    assert error.value.type == "client_output_blocked"


def test_export_csv_scrubs_tool_names_and_blocks_provider_domains() -> None:
    from urllib.parse import urlsplit

    from account_intelligence.connectors.settings import load_providers_config
    from views.packages import csv_bytes, csv_package
    from views.tables import specs

    vendor = next(
        name for name in vendor_names(load_providers_config()).values() if name != "Parallel"
    )
    host = urlsplit(next(iter(load_providers_config().providers.values())).base_url).hostname
    spec = specs()["people"]
    row = {column.key: "" for column in spec.exported_columns}
    key = spec.exported_columns[0].key

    row[key] = f"Found by {vendor}"
    body = csv_bytes(spec, [row])
    assert vendor.lower().encode() not in body.lower()
    assert b"6th agents" in body

    row[key] = f"Found by {vendor} at https://{host}/search"
    package, body = csv_package(uuid4(), uuid4(), "people", [row])
    assert package.status == "blocked" and package.reason == "client_output_refused"
    assert body is None


def settings(**kw: object) -> TenantAgentSettings:
    return TenantAgentSettings(**{"pursuing_client": "P", "lanes": ("salesforce",), **kw})  # type: ignore[arg-type]


def test_setup_takes_client_and_lanes_from_tenant_settings() -> None:
    req = RunRequest(account_name=" Acme ", domain="Acme.Example.")
    out = validate_setup(req, settings(lanes=()), CFG, 5)
    assert (out.account_name, out.domain, out.lanes) == ("Acme", "acme.example", CFG.lanes)
    assert validate_setup(req, settings(depth="deep"), CFG, 0).depth == "deep"
    # no pursuing client: the account is researched by its name alone
    assert validate_setup(req, settings(pursuing_client=None), CFG, 0).pursuing_client is None
    with pytest.raises(SetupInvalid, match="unknown lanes"):
        validate_setup(req, settings(lanes=("cobol",)), CFG, 0)
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
