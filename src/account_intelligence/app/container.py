"""The single wiring point (integration spec C): every adapter is chosen here and nowhere else.

``APP_ENV`` selects the profile:

* ``test`` and ``dev``: hermetic adapters (in-process scheduler, memory or local payloads,
  environment secrets, the unconfigured LLM unless ``ANTHROPIC_API_KEY`` is set in dev).
* ``staging`` and ``production``: Google Cloud adapters only (Cloud Tasks, Cloud Storage, Secret
  Manager, Cloud SQL through the connector, Claude through Vertex AI or the Anthropic API per
  ``LLM_ACCESS``, decision D-19). ``assert_gcp_runtime`` refuses to start if any hermetic adapter
  is wired there (W4).

Identity never comes from configuration: the tenant comes only from the verified token (API) or
the OIDC-verified task payload resolved against the run row (worker) (W3).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import (
    EnvSecrets,
    InProcessScheduler,
    LocalPayloadStore,
    MemoryPayloadStore,
    SystemClock,
    UnconfiguredLlm,
)
from account_intelligence.platform.ports import (
    Clock,
    LlmPort,
    NotConfigured,
    PayloadStore,
    RunScheduler,
    SecretSource,
)
from account_intelligence.runs.lifecycle import RunLimits
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.runner import SliceSettings
from account_intelligence.workflow.spec import WorkflowSpec, load_spec

CLOUD_ENVS = frozenset({"staging", "production"})
HERMETIC_TYPES = (
    InProcessScheduler,
    MemoryPayloadStore,
    LocalPayloadStore,
    EnvSecrets,
    UnconfiguredLlm,
)


class RuntimeMisconfigured(RuntimeError):
    """A staging or production process was wired with a local or fake adapter (W4)."""


@dataclass
class Container:
    """Everything the API and the worker need, built once per process."""

    env: str
    scheduler: RunScheduler
    payloads: PayloadStore
    secrets: SecretSource
    llm: LlmPort
    clock: Clock
    spec: WorkflowSpec
    limits: RunLimits
    slices: SliceSettings
    checkpoint_dsn: str
    connectors: Any | None = None
    task_audience: str = ""
    task_service_account: str = ""
    verify_task_token: Callable[[str, str, str], None] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    async def deps_for(self, tenant_id: UUID, run_id: UUID) -> RunDeps:
        """One run's dependencies, loaded under the tenant (the run row is the identity)."""
        async with tenant_txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, a.domain, r.run_input, a.careers_url,"
                        " a.careers_url_verified_at FROM account_runs r JOIN accounts a"
                        " ON a.id = r.account_id AND a.tenant_id = r.tenant_id WHERE r.id = :r"
                    ),
                    {"r": run_id},
                )
            ).first()
        if row is None:
            raise LookupError("run not found for this tenant")
        hosts = {row.domain}
        if row.careers_url and row.careers_url_verified_at:
            from urllib.parse import urlsplit

            hosts.add((urlsplit(row.careers_url).hostname or "").lower())
        return RunDeps(
            tenant_id=tenant_id,
            run_id=run_id,
            account_id=row.account_id,
            domain=row.domain,
            txn=tenant_txn,
            llm=self.llm,
            payloads=self.payloads,
            clock=self.clock,
            spec=self.spec,
            connectors=self.connectors,
            extra_allowed_hosts=frozenset(h for h in hosts if h),
            run_input=dict(row.run_input or {}),
        )


def _limits(spec: WorkflowSpec, env: Mapping[str, str]) -> RunLimits:
    settings = spec.settings
    return RunLimits(
        graph_version=str(settings.get("graph_version", spec.version)),
        budget_usd=Decimal(str(env.get("RUN_BUDGET_USD", settings.get("max_budget_usd", 5.0)))),
        tool_call_cap=int(env.get("RUN_TOOL_CALL_CAP", settings.get("max_calls", 60))),
        reserved_credits=Decimal(str(env.get("RUN_RESERVE_CREDITS", "6.0"))),
    )


def _slices(spec: WorkflowSpec) -> SliceSettings:
    s = spec.settings
    return SliceSettings(
        slice_seconds=float(s.get("slice_seconds", 240)),
        max_slices=int(s.get("max_slices", 60)),
        approval_hours=float(s.get("approval_timeout_hours", 24)),
    )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeMisconfigured(f"{name} must be set for APP_ENV={env.get('APP_ENV')}")
    return value


def build_container(env: Mapping[str, str] | None = None) -> Container:
    """Build the container for ``APP_ENV`` (default ``dev``)."""
    env = dict(os.environ if env is None else env)
    app_env = env.get("APP_ENV", "dev")
    spec = load_spec()
    if app_env in CLOUD_ENVS:
        container = _cloud(env, app_env, spec)
        assert_gcp_runtime(container)
        return container
    if app_env not in ("dev", "test"):
        raise RuntimeMisconfigured(f"unknown APP_ENV {app_env!r}")
    payloads: PayloadStore = (
        LocalPayloadStore(env["PAYLOAD_DIR"]) if env.get("PAYLOAD_DIR") else MemoryPayloadStore()
    )
    llm: LlmPort = UnconfiguredLlm()
    if app_env == "dev" and env.get("ANTHROPIC_API_KEY"):
        from account_intelligence.platform.llm import (
            AnthropicLlm,
            anthropic_api_client,
            load_routes,
        )

        llm = AnthropicLlm(
            anthropic_api_client(env["ANTHROPIC_API_KEY"]), load_routes(), "anthropic_api"
        )
    return Container(
        env=app_env,
        scheduler=InProcessScheduler(),
        payloads=payloads,
        secrets=EnvSecrets(),
        llm=llm,
        clock=SystemClock(),
        spec=spec,
        limits=_limits(spec, env),
        slices=_slices(spec),
        checkpoint_dsn=env.get("CHECKPOINT_DATABASE_URL", ""),
    )


def _cloud(env: Mapping[str, str], app_env: str, spec: WorkflowSpec) -> Container:
    """Google Cloud adapters (no network call happens while building them)."""
    from google.cloud import secretmanager, tasks_v2
    from google.cloud import storage  # type: ignore[attr-defined]

    from account_intelligence.platform.gcp.adapters import (
        CloudTasksScheduler,
        GcsPayloadStore,
        SecretManagerSecrets,
        verify_task_token,
    )
    from account_intelligence.platform.llm import (
        AnthropicLlm,
        anthropic_api_client,
        load_routes,
        vertex_client,
    )

    project = _require(env, "GCP_PROJECT")
    region = _require(env, "GCP_REGION")
    secrets = SecretManagerSecrets(secretmanager.SecretManagerServiceClient(), project)
    scheduler = CloudTasksScheduler(
        tasks_v2.CloudTasksClient(),
        project,
        region,
        _require(env, "TASKS_QUEUE"),
        _require(env, "WORKER_URL"),
        _require(env, "TASKS_SERVICE_ACCOUNT"),
        _require(env, "TASKS_AUDIENCE"),
    )
    payloads = GcsPayloadStore(storage.Client(project=project), _require(env, "PAYLOAD_BUCKET"))
    access = env.get("LLM_ACCESS", "vertex_ai")
    if access == "vertex_ai":
        llm: LlmPort = AnthropicLlm(
            vertex_client(project, env.get("VERTEX_REGION", region)), load_routes(), "vertex_ai"
        )
    elif access == "anthropic_api":
        llm = AnthropicLlm(
            anthropic_api_client(secrets.get(env.get("ANTHROPIC_SECRET", "anthropic-api-key"))),
            load_routes(),
            "anthropic_api",
        )
    else:
        raise RuntimeMisconfigured("LLM_ACCESS must be vertex_ai or anthropic_api (D-19)")
    return Container(
        env=app_env,
        scheduler=scheduler,
        payloads=payloads,
        secrets=secrets,
        llm=llm,
        clock=SystemClock(),
        spec=spec,
        limits=_limits(spec, env),
        slices=_slices(spec),
        checkpoint_dsn=_require(env, "CHECKPOINT_DATABASE_URL"),
        task_audience=_require(env, "TASKS_AUDIENCE"),
        task_service_account=_require(env, "TASKS_SERVICE_ACCOUNT"),
        verify_task_token=verify_task_token,
    )


def assert_gcp_runtime(container: Container) -> None:
    """W4: staging and production refuse local or fake adapters and an unverifiable worker."""
    if container.env not in CLOUD_ENVS:
        return
    wired = {
        "scheduler": container.scheduler,
        "payloads": container.payloads,
        "secrets": container.secrets,
        "llm": container.llm,
    }
    bad = sorted(name for name, adapter in wired.items() if isinstance(adapter, HERMETIC_TYPES))
    if bad:
        raise RuntimeMisconfigured(f"hermetic adapters wired in {container.env}: {bad}")
    if container.verify_task_token is None or not container.task_audience:
        raise RuntimeMisconfigured("worker task authentication is not configured")
    if not container.checkpoint_dsn:
        raise NotConfigured("checkpoint database is not configured")
