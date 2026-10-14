"""The replica stack and the hermetic modes, without a database: the stack's documented defaults
are the hermetic model, hermetic providers and the graph research engine on ports of its own;
the hermetic modes are refused outside development and tests; the hermetic providers answer any
account offline; readiness names a missing model key in plain words.

GUARANTEE(test_hermetic_providers_answer_offline_for_any_account)
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from account_intelligence.app.container import RuntimeMisconfigured, build_container
from account_intelligence.connectors import hermetic
from account_intelligence.connectors.base import ProviderCode

ROOT = Path(__file__).resolve().parents[2]


def test_the_replica_stack_runs_on_hermetic_fakes_on_its_own_ports() -> None:
    replica = yaml.safe_load((ROOT / "compose.replica.yml").read_text())
    main = yaml.safe_load((ROOT / "compose.yml").read_text())
    env = replica["x-app-env"]
    assert (env["LLM_FAKE"], env["CONNECTORS"], env["RESEARCH_ENGINE"]) == (
        "hermetic",
        "hermetic",
        "graph",
    )
    assert env["APP_ENV"] == "dev" and "ANTHROPIC_API_KEY" not in env

    def ports(doc: dict) -> set[str]:  # type: ignore[type-arg]
        return {p.split(":")[0] for s in doc["services"].values() for p in s.get("ports", [])}

    assert not ports(replica) & ports(main)  # never collides with the main stack
    assert replica["name"] == "abm-replica"


@pytest.mark.parametrize("env", [{"LLM_FAKE": "hermetic"}, {"CONNECTORS": "hermetic"}])
def test_the_hermetic_modes_are_refused_in_production(env: dict[str, str]) -> None:
    with pytest.raises(RuntimeMisconfigured):
        build_container({"APP_ENV": "production", **env})


@pytest.mark.asyncio
async def test_hermetic_providers_answer_offline_for_any_account() -> None:
    sent: list[str] = []
    transports = hermetic.transports()
    async with httpx.AsyncClient(transport=transports[ProviderCode.P_SIGNALS]) as c:
        r = await c.get("https://feeds.invalid/companies/northwind.example/job_openings")
        sent.append(str(r.request.url))
    job = r.json()["data"][0]["attributes"]
    assert job["url"] == "https://northwind.example/careers/jobs/s1"
    async with httpx.AsyncClient(transport=transports[ProviderCode.P_SEARCH]) as c:
        r = await c.post(
            "https://search.invalid/v1/search",
            content=json.dumps({"search_queries": ['"northwind.example" company news']}),
        )
    assert r.json()["results"][0]["url"].startswith("https://news.northwind.example/")
    assert await hermetic.resolve("api.anything.invalid") == [hermetic.PUBLIC_ADDRESS]


def test_readiness_names_the_missing_model_key_and_the_engine() -> None:
    from account_intelligence.api.routes.health import components

    plain = components(build_container({"APP_ENV": "test"}), {})
    assert plain["llm"] == "not configured (ANTHROPIC_API_KEY)"
    assert plain["research_engine"].startswith("hermetic")
    fake = components(
        build_container({"APP_ENV": "test", "LLM_FAKE": "hermetic", "CONNECTORS": "hermetic"}),
        {"CONNECTORS": "hermetic"},
    )
    assert fake["llm"] == "fake (LLM_FAKE)"
    assert fake["provider.search"].startswith("hermetic")
