"""Wiring contract: configuration, credential and request-id glue between the layers.

Hermetic (no database, no network, no keys). These checks back rows of ``make verify-wiring``
(``scripts/verify_wiring.py``); each test names the row it proves.

GUARANTEE(test_env_example_lists_exactly_what_the_code_reads): CONFIG env-example-matches-code.
GUARANTEE(test_provider_is_configured_only_with_every_usable_credential): PROVIDERS configured.
GUARANTEE(test_request_id_reaches_every_log_line): API request-id-in-logs.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"
KEY_LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=")
# Read by the toolchain or the runtime itself, never by our code: not part of the contract.
PLATFORM_VARS = {"NODE_ENV", "PYTHONPATH"}


def _provider_hosts() -> tuple[str, ...]:
    """Every provider host, read from the provider config (no vendor is named in code)."""
    from urllib.parse import urlsplit

    from account_intelligence.connectors.settings import load_providers_config

    return tuple(
        sorted(
            {
                urlsplit(p.base_url).hostname or ""
                for p in load_providers_config().providers.values()
            }
            - {""}
        )
    )


def _example_lines() -> list[str]:
    return EXAMPLE.read_text(encoding="utf-8").splitlines()


def _example_keys() -> dict[str, str]:
    """Every key in the template, active or commented out, with its template value."""
    keys: dict[str, str] = {}
    for line in _example_lines():
        match = KEY_LINE.match(line)
        if match:
            keys[match.group(1)] = line.split("=", 1)[1].strip()
    return keys


def _web_server_env_reads() -> set[str]:
    reads: set[str] = set()
    for folder in ("app", "lib", "components"):
        for path in (ROOT / "web" / folder).rglob("*.ts*"):
            reads |= set(re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", path.read_text("utf-8")))
    for name in ("proxy.ts", "next.config.ts"):
        reads |= set(
            re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", (ROOT / "web" / name).read_text())
        )
    return reads - PLATFORM_VARS


def _registry_names() -> set[str]:
    from account_intelligence.config import registry

    return {row["name"] for row in registry()}


# ---- CONFIG -----------------------------------------------------------------------------------


def test_env_example_lists_exactly_what_the_code_reads() -> None:
    """CONFIG env-example-matches-code: no dead key in the template, no read missing from it."""
    code = _registry_names() | _web_server_env_reads()
    example = set(_example_keys())
    assert not sorted(example - code), f"template keys no code reads: {sorted(example - code)}"
    assert not sorted(code - example), (
        f"read by code, missing from template: {sorted(code - example)}"
    )


def test_env_example_comments_sit_above_their_key() -> None:
    """CONFIG env-example-layout: a comment describes the key BELOW it, never the one above, so
    no key line carries a trailing comment and every provider key has its label directly above."""
    lines = _example_lines()
    for number, line in enumerate(lines):
        if KEY_LINE.match(line) and not line.startswith("#"):
            assert " #" not in line.split("=", 1)[1], f"line {number + 1}: trailing comment"
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    # each provider key's label is the vendor's display name from config (never named in code)
    labels = {
        f"{code.value.upper()}_API_KEY": name
        for code, name in vendor_names(load_providers_config()).items()
    }
    for key, label in labels.items():
        index = next(i for i, line in enumerate(lines) if KEY_LINE.match(line) and key in line)
        assert label.lower() in lines[index - 1].lower(), f"{key}: the line above must name it"


def test_env_example_is_hermetic_and_valid_by_default() -> None:
    """CONFIG env-example-safe-defaults: a fresh copy calls no paid service and validates."""
    keys = _example_keys()
    assert keys["APP_ENV"] == "dev"
    assert keys["CONNECTORS"] == "off", "live providers must be an explicit opt-in"
    for name in (
        "ANTHROPIC_API_KEY",
        "P_SEARCH_API_KEY",
        "P_SCRAPE_API_KEY",
        "P_SIGNALS_API_KEY",
        "P_SIGNALS_API_TOKEN",
        "P_PEOPLE_API_KEY",
        "P_ORG_API_KEY",
    ):
        assert keys[name] == "", f"{name} must ship empty, never a placeholder that looks set"
    assert keys["CHECKPOINT_DATABASE_URL"].startswith("postgresql://"), "checkpointer is psycopg"
    from account_intelligence.config import load_settings

    active = {
        k: v
        for k, v in keys.items()
        if v
        and not any(
            line.startswith(f"#{k}=") or line.startswith(f"# {k}=") for line in _example_lines()
        )
    }
    load_settings(active)  # raises EnvironmentInvalid naming the variable


def test_migrator_url_is_read_only_by_migration_and_ops_code() -> None:
    """CONFIG superuser-not-in-request-path: the owner-role URL never reaches the API or worker."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "config.py" and path.parent.name == "account_intelligence":
            continue  # the registry entry itself
        text = path.read_text(encoding="utf-8")
        if "MIGRATOR_DATABASE_URL" in text or "migrator_database_url" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_production_template_uses_a_non_superuser_owner() -> None:
    """CONFIG prod-migrator-role: deploy templates never migrate as ``postgres``."""
    for path in [*(ROOT / "infra").rglob("*.tf"), *(ROOT / "scripts").glob("deploy*.sh")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"postgres(ql)?://postgres[:@]", text), str(path.relative_to(ROOT))


# ---- PROVIDERS --------------------------------------------------------------------------------


def _cfg(code: str) -> Any:
    from account_intelligence.connectors.base import ProviderCode
    from account_intelligence.connectors.settings import load_providers_config

    return load_providers_config().provider(ProviderCode[code])


@pytest.mark.parametrize(
    ("env", "configured"),
    [
        ({}, False),
        ({"P_SIGNALS_API_KEY": "k" * 24}, False),  # signals needs the token too
        ({"P_SIGNALS_API_TOKEN": "t" * 24}, False),
        ({"P_SIGNALS_API_KEY": "k" * 24, "P_SIGNALS_API_TOKEN": "t" * 24}, True),
        (
            {"P_SIGNALS_API_KEY": "your-signals-api-key", "P_SIGNALS_API_TOKEN": "t" * 24},
            False,
        ),
        ({"P_SIGNALS_API_KEY": "replace-with-key", "P_SIGNALS_API_TOKEN": "t" * 24}, False),
    ],
)
def test_provider_is_configured_only_with_every_usable_credential(
    env: dict[str, str], configured: bool
) -> None:
    """PROVIDERS configured: all credentials present and none a template placeholder."""
    from account_intelligence.connectors.factory import provider_configured

    assert provider_configured(_cfg("P_SIGNALS"), env) is configured


def test_unconfigured_provider_sends_no_credential_at_all() -> None:
    """PROVIDERS no-partial-credential: a half-configured provider sends nothing secret, so the
    call fails as unauthenticated instead of with a partial or placeholder credential."""
    from account_intelligence.connectors.factory import auth_headers

    half = auth_headers(_cfg("P_SIGNALS"), {"P_SIGNALS_API_KEY": "k" * 24})
    assert "x-api-key" not in half and "x-api-token" not in half
    placeholder = auth_headers(
        _cfg("P_SEARCH"), {"P_SEARCH_API_KEY": "your-parallel-search-api-key"}
    )
    assert "x-api-key" not in placeholder
    full = auth_headers(
        _cfg("P_SIGNALS"), {"P_SIGNALS_API_KEY": "k" * 24, "P_SIGNALS_API_TOKEN": "t" * 24}
    )
    assert {"x-api-key", "x-api-token"} <= set(full)


def test_provider_hosts_are_reached_only_by_the_broker_and_adapters() -> None:
    """PROVIDERS routing: no module outside the connectors package names a provider host, and
    the web app never does."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if "connectors" in path.relative_to(ROOT / "src").parts:
            continue
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.relative_to(ROOT)}: {h}" for h in _provider_hosts() if h in text]
    for folder in ("app", "lib", "components", "design"):
        for path in (ROOT / "web" / folder).rglob("*"):
            if path.suffix in {".ts", ".tsx", ".css", ".mjs"}:
                text = path.read_text(encoding="utf-8")
                offenders += [
                    f"{path.relative_to(ROOT)}: {h}" for h in _provider_hosts() if h in text
                ]
    assert offenders == []


# ---- API: request id ---------------------------------------------------------------------------


def test_request_id_reaches_every_log_line() -> None:
    """API request-id-in-logs: a line logged while a request is handled carries its id."""
    from obs.catalog import load_catalog
    from obs.logging import REQUEST_ID, JsonFormatter
    from obs.setup import drops

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(load_catalog(), drops(), "api"))
    log = logging.getLogger("wiring.test")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    token = REQUEST_ID.set("6f1c2a52-6b8e-4a52-9a43-2a4a6f3c1d10")
    try:
        log.info("wiring.probe")
    finally:
        REQUEST_ID.reset(token)
        log.removeHandler(handler)
    line = json.loads(stream.getvalue().strip())
    assert line["request_id"] == "6f1c2a52-6b8e-4a52-9a43-2a4a6f3c1d10"


def test_local_task_hop_forwards_the_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """API request-id-to-worker: the slice the API posts to the worker carries the same id."""
    import httpx

    from account_intelligence.platform.hermetic import HttpTaskScheduler
    from obs.logging import REQUEST_ID

    sent: list[dict[str, str]] = []

    async def fake_post(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        sent.append(dict(kwargs.get("headers") or {}))
        return httpx.Response(202)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def scenario() -> None:
        from uuid import uuid4

        scheduler = HttpTaskScheduler("http://worker:8081")
        token = REQUEST_ID.set("0b6c7f0e-4a7d-4c33-9a8b-6e2b4b0a9d11")
        try:
            await scheduler.enqueue_slice(uuid4(), uuid4(), 0)
        finally:
            REQUEST_ID.reset(token)
        await asyncio.gather(*scheduler.tasks)

    asyncio.run(scenario())
    assert sent and sent[0].get("X-Request-ID") == "0b6c7f0e-4a7d-4c33-9a8b-6e2b4b0a9d11"


# ---- API: readiness ----------------------------------------------------------------------------


def test_readiness_reports_every_component_by_status_word_only() -> None:
    """API readyz-components: payload store, rate-limit store, LLM and each provider are reported
    as status words; no value of any variable appears."""
    from account_intelligence.api.routes.health import components

    secret = "k" * 30
    env = {
        "CONNECTORS": "on",
        "P_SEARCH_API_KEY": secret,
        "P_SIGNALS_API_KEY": secret,  # no token: unconfigured
        "ANTHROPIC_API_KEY": secret,
    }

    class _Container:
        connectors = object()
        payloads = None
        llm = type("AnthropicLlm", (), {})()

    report = components(_Container(), env)
    assert report["provider.search"] == "configured"
    assert report["provider.signals"] == "unconfigured"
    assert report["provider.people"].startswith("disabled (needs the owner's people-search grid")
    assert report["llm"] == "present"
    assert {"payload_store", "rate_limit_store"} <= set(report)
    assert secret not in json.dumps(report)


def test_the_cloud_api_says_provider_keys_are_checked_on_the_worker() -> None:
    """The provider keys are mounted on the worker only; the api must not call them missing."""
    from account_intelligence.api.routes.health import components

    class _Container:
        connectors = object()
        payloads = None
        llm = type("AnthropicLlm", (), {})()

    env = {"CONNECTORS": "on", "SERVICE_ROLE": "api", "APP_ENV": "production"}
    report = components(_Container(), env)
    assert report["provider.search"] == "checked on the worker"
    assert report["provider.people"].startswith("disabled")


def test_readiness_marks_providers_off_when_connectors_are_off() -> None:
    """API readyz-components: hermetic default shows every provider off, never configured."""
    from account_intelligence.api.routes.health import components

    class _Container:
        connectors = None
        payloads = None
        llm = type("UnconfiguredLlm", (), {})()

    report = components(_Container(), {"P_SEARCH_API_KEY": "k" * 30})
    assert {v for k, v in report.items() if k.startswith("provider.")} == {"off"}
    assert report["llm"] == "not configured (ANTHROPIC_API_KEY)"


# ---- secrets profile ---------------------------------------------------------------------------


def test_dev_never_builds_a_secret_manager_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONFIG dev-no-secret-manager: the dev container reads env only."""
    import sys

    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", None)  # any import would fail
    from account_intelligence.app.container import build_container

    container = build_container({"APP_ENV": "dev"})
    assert type(container.secrets).__name__ == "EnvSecrets"


def test_cloud_secret_read_fails_closed_when_the_secret_is_absent() -> None:
    """CONFIG cloud-secret-fail-closed: a missing ``anthropic-api-key`` is NotConfigured, with no
    SDK message (it may name paths) and no fallback value."""
    from account_intelligence.platform.gcp.adapters import SecretManagerSecrets

    class _Missing:
        def secret_version_path(self, project: str, name: str, version: str) -> str:
            return f"projects/{project}/secrets/{name}/versions/{version}"

        def access_secret_version(self, name: str) -> Any:
            raise LookupError(f"404 {name} not found")

    with pytest.raises(Exception) as caught:
        SecretManagerSecrets(_Missing(), "p").get("anthropic-api-key")
    assert type(caught.value).__name__ == "NotConfigured"
    assert "projects/" not in str(caught.value)


def test_rate_limits_and_readiness_read_the_one_redis_setting() -> None:
    """API readyz-redis: the shared limiter and readiness both follow ``REDIS_URL`` (compose and
    Terraform set it); without it the limiter is in-process and readiness says so."""
    from account_intelligence.api.routes.health import components

    class _Container:
        connectors = None
        payloads = None
        llm = None

    assert components(_Container(), {"REDIS_URL": "redis://redis:6379/0"})["rate_limit_store"] == (
        "redis"
    )
    assert components(_Container(), {})["rate_limit_store"] == "in_process"
    compose = (ROOT / "compose.yml").read_text()
    terraform = (ROOT / "infra" / "stack" / "main.tf").read_text()
    assert "REDIS_URL" in compose and "REDIS_URL" in terraform
    assert "REDIS_HOST" not in compose + terraform


def test_readiness_needs_the_schema_revision_of_the_code() -> None:
    """Readiness compares the database revision with the newest migration the code was written
    with; the constant moves with every new migration."""
    from account_intelligence.api.routes.health import EXPECTED_REVISION, revision_check

    newest = max(
        p.name.split("_", 1)[0] for p in (ROOT / "migrations" / "versions").glob("0*.py")
    )
    assert EXPECTED_REVISION == newest
    assert revision_check(EXPECTED_REVISION) == "ok"
    assert revision_check(str(int(EXPECTED_REVISION) + 1).zfill(4)) == "ok"
    assert revision_check(str(int(EXPECTED_REVISION) - 1).zfill(4)).startswith("failed")
    assert revision_check(None).startswith("failed")
