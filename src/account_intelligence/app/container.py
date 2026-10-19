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
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from account_intelligence.dev_limits import dev_uncapped
from account_intelligence.platform.fake_model import FakeModel, load_scenario
from account_intelligence.platform.hermetic import (
    EnvSecrets,
    HttpTaskScheduler,
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
from exports.wiring import build_export_builder
from llm.choice import RunModels

CLOUD_ENVS = frozenset({"staging", "production"})
HERMETIC_TYPES = (
    InProcessScheduler,
    HttpTaskScheduler,
    MemoryPayloadStore,
    LocalPayloadStore,
    EnvSecrets,
    UnconfiguredLlm,
    FakeModel,
)
FAKE_MODEL_DIR = CONFIG_DIR / "hermetic" / "fake_model"


class RuntimeMisconfigured(RuntimeError):
    """A staging or production process was wired with a local or fake adapter (W4)."""


def _unconfigured(port: str) -> Any:
    """The production placeholder of a credit manager v2 port."""
    from spend.pause import UnconfiguredWallet
    from spend.upload import UnconfiguredScanner, UnconfiguredUploadStore

    if port == "wallet":
        return UnconfiguredWallet()
    if port == "upload_store":
        return UnconfiguredUploadStore()
    if port == "scanner":
        return UnconfiguredScanner()
    return _UnconfiguredParser()


class _UnconfiguredParser:
    """No upload parser without a connector runtime (every parse is refused as drift)."""

    def parse(self, provider_code: str, operation: str, args: Any, body: bytes) -> list[Any]:
        """Always refuse."""
        from spend.errors import NotConfigured

        raise NotConfigured("upload parser")


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
    builder: Any | None = None  # BuilderPort (exports.wiring); None ends builds PARTIAL
    # the quality gate (``gate`` package); None: the gate does not run and the run ends PARTIAL
    gate: Any | None = None
    # provider balance snapshots (credit cap K15 preflight); None: every balance reads unknown
    balances: Any | None = None
    # local stack only: approve run caps as tenant policy (DEV_AUTO_APPROVE_CAPS=1)
    dev_auto_approve: bool = False
    # Credit manager v2 ports: production placeholders fail closed (NotConfigured, or an
    # unavailable scanner that rejects every upload) until real adapters exist; never a fake success
    wallet: Any = field(default_factory=lambda: _unconfigured("wallet"))
    upload_store: Any = field(default_factory=lambda: _unconfigured("upload_store"))
    scanner: Any = field(default_factory=lambda: _unconfigured("scanner"))
    upload_parser: Any = field(default_factory=lambda: _unconfigured("upload_parser"))
    # model routing (D-48): a factory deps -> llm.port.Router; None keeps the legacy LlmPort path
    router: Any | None = None
    # research cache: model reply log and cross-run reuse (``llm.reply_cache``); None logs nothing
    reply_cache: Any | None = None
    # uploaded documents for input mode "file" (``documents.service.DocumentService``)
    documents: Any | None = None
    # Phase 3 tenant plane (identity broker, secret and object stores, mailer, scanner): the
    # production default fails closed (NotConfigured) until real adapters exist
    tenant_plane: Any | None = None

    async def deps_for(self, tenant_id: UUID, run_id: UUID) -> RunDeps:
        """One run's dependencies, loaded under the tenant (the run row is the identity)."""
        async with tenant_txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, a.domain, r.run_input, a.careers_url,"
                        " a.careers_url_verified_at, r.models"
                        " FROM account_runs r JOIN accounts a"
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
            builder=self.builder,
            gate=self.gate,
            balances=self.balances,
            # prompts (cap approval waiting, credits exhausted) become outbox rows the relay
            # task delivers (audit 2026-10-15)
            notifier=_outbox_notifier(tenant_id, self.clock),
            dev_auto_approve=self.dev_auto_approve,
            extra_allowed_hosts=frozenset(h for h in hosts if h),
            run_input=dict(row.run_input or {}),
            router=self.router,
            reply_cache=self.reply_cache,
            # the user's research and analysis models, recorded when the run was created; a run
            # from before the model choice has none and keeps the configured routes
            models=RunModels.from_json(row.models) if row.models else None,
        )


def build_connectors(
    env: Mapping[str, str], payloads: PayloadStore, redis_url: str | None
) -> tuple[Any, Any]:
    """The connector runtime and the node RetryPolicy (the single retry owner for provider 429
    and 5xx, W6). Provider credentials come from the environment (Cloud Run injects them from
    Secret Manager). With ``redis_url`` (``REDIS_URL``, the one Redis setting) the rate limiter
    shares its buckets and cooldowns across instances. VERIFY(V-REDIS-1): Memorystore in-transit
    encryption needs the instance's server CA; the TLS settings are checked against a real
    instance at bootstrap."""
    from account_intelligence.connectors.factory import build_runtime
    from account_intelligence.connectors.ratelimit import RedisRateStore
    from account_intelligence.connectors.service import retry_policy_for
    from account_intelligence.connectors.settings import load_providers_config

    config = load_providers_config()
    rate_store = None
    if redis_url:
        import redis.asyncio as redis

        rate_store = RedisRateStore(redis.Redis.from_url(redis_url))
    runtime = build_runtime(
        config,
        txn=tenant_txn,
        payloads=payloads,
        rate_store=rate_store,
        env=env,
        cache=research_cache(env),
    )
    return with_governance(runtime), retry_policy_for(config)


def _outbox_notifier(tenant_id: UUID, clock: Any) -> Any:
    """The run's notifier: prompts go to the notification outbox (spend.notify)."""
    from spend.notify import OutboxNotifier

    return OutboxNotifier(tenant_txn, tenant_id, clock)


def _with_cloud_objects(plane: Any, client: Any, bucket: str, documents: Any) -> Any:
    """The cloud tenant plane's object store (the payload bucket) and its scanner (the document
    scanner over stored objects); exports, logos and connector quarantine need them."""
    from account_intelligence.api.tenant_plane import GcsObjectStore, ObjectScanner

    plane.objects = GcsObjectStore(client, bucket)
    if documents is not None:
        plane.scanner = ObjectScanner(plane.objects, documents.scanner)
    return plane


def _with_mailer(plane: Any, env: Mapping[str, str]) -> Any:
    """The tenant plane with the SMTP mailer when ``SMTP_HOST`` is set (invites, email
    verification, password reset and alert email); without it mail stays fail closed."""
    from tenancy.smtp_mailer import mailer_from_env

    mailer = mailer_from_env(env)
    if mailer is not None:
        plane.mailer = mailer
    return plane


def maildrive_apps(env: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The Mail and drive OAuth apps registered in the environment (empty until the owner
    registers them: every source then says "Not available yet")."""
    from connectors_client.maildrive import oauth_apps

    return oauth_apps(env)


def hermetic_connectors(env: Mapping[str, str], payloads: PayloadStore) -> tuple[Any, Any]:
    """The connector runtime on synthetic providers (``connectors.hermetic``): the real ledger,
    routing and grading path, with no request leaving the process (dev and test only)."""
    from account_intelligence.connectors import hermetic
    from account_intelligence.connectors.factory import build_runtime
    from account_intelligence.connectors.security import UrlPolicy
    from account_intelligence.connectors.service import retry_policy_for
    from account_intelligence.connectors.settings import load_providers_config

    config = load_providers_config()
    runtime = build_runtime(
        config,
        txn=tenant_txn,
        payloads=payloads,
        transports=hermetic.transports(),
        env={**env, **hermetic.PLACEHOLDER_KEYS},
        url_policy=UrlPolicy(resolver=hermetic.resolve),
        cache=None,
    )
    return with_governance(runtime), retry_policy_for(config)


def research_cache(env: Mapping[str, str]) -> Any:
    """The cross-run research cache (``RESEARCH_CACHE``, default on), indexed in Redis when
    ``REDIS_URL`` is set; None when switched off."""
    from account_intelligence.connectors.cache import (
        ResearchCache,
        load_research_cache_config,
        redis_index,
    )

    if env.get("RESEARCH_CACHE", "on") == "off":
        return None
    cfg = load_research_cache_config()
    if not cfg.enabled:
        return None
    url = env.get("REDIS_URL")
    return ResearchCache(cfg, redis_index(url) if url else None)


def reply_cache(env: Mapping[str, str]) -> Any:
    """The model reply log and cross-run reply reuse (``llm.reply_cache.PgReplyCache``). The log
    is always written; with the research cache off (``RESEARCH_CACHE=off`` or the file) the TTL
    is zero, so only a run that resumes another (``resume_from``) reuses replies."""
    from account_intelligence.connectors.cache import load_research_cache_config
    from llm.reply_cache import PgReplyCache

    cfg = load_research_cache_config()
    reuse = env.get("RESEARCH_CACHE", "on") != "off" and cfg.enabled and cfg.model_replies.enabled
    return PgReplyCache(tenant_txn, cfg.model_replies.ttl_days if reuse else 0.0)


def with_model_choice(llm: Any, env: Mapping[str, str], key: str | None = None) -> Any:
    """The live port with the user's model choice (``platform.llm.ChoiceLlm``): Claude on the
    Anthropic port, Kimi K3 on the Moonshot client when ``MOONSHOT_API_KEY`` (or ``key``, from the
    secret store) is set. The fake model and an unconfigured port are returned as they are."""
    from account_intelligence.platform.llm import AnthropicLlm, ChoiceLlm

    moonshot_key = key or env.get("MOONSHOT_API_KEY")
    if not moonshot_key and env.get("MOONSHOT_API_KEY_FILE"):
        try:
            moonshot_key = Path(env["MOONSHOT_API_KEY_FILE"]).read_text().strip()
        except OSError:
            moonshot_key = None
    moonshot = None
    if moonshot_key:
        from llm.adapters.moonshot_api import BASE_URL, MoonshotClient

        moonshot = MoonshotClient(
            moonshot_key,
            base_url=env.get("MOONSHOT_BASE_URL") or BASE_URL,
            # Ollama Cloud serves Kimi K3 too (owner decision 2026-10-15); see the adapter
            dialect=env.get("MOONSHOT_API_DIALECT") or "moonshot",
            remote_model=env.get("MOONSHOT_MODEL") or None,
        )
    if isinstance(llm, AnthropicLlm):
        return ChoiceLlm(llm, moonshot)
    if moonshot is not None and isinstance(llm, UnconfiguredLlm):
        return ChoiceLlm(None, moonshot)
    return llm


def router_factory(client: Any, access: str) -> Any:
    """``deps -> Router`` over one SDK client for the configured access path (D-19). Only the
    candidates whose ``adapter`` equals ``access`` can be served; any other candidate fails with
    ``AdapterNotConfigured`` (never silently sent through another path)."""
    from llm.adapters.anthropic_api import AnthropicAdapter
    from llm.config import load_routing_config, with_model_split
    from llm.health import PgHealth
    from llm.port import Router
    from llm.record import PgCallStore

    cfg = load_routing_config()
    if cfg.benchmark_report is None:
        raise RuntimeMisconfigured(
            "MODEL_ROUTER=on needs a routing config backed by a benchmark report (D-51)"
        )
    adapters = {access: AnthropicAdapter(client)}
    clock = _WallClock()

    def make(deps: Any) -> Any:
        return Router(
            with_model_split(cfg, getattr(deps, "model_split", "split")),
            adapters,
            PgCallStore(deps.txn, deps.tenant_id, deps.run_id),
            PgHealth(
                deps.txn,
                deps.tenant_id,
                cfg.health_threshold,
                cfg.health_window_s,
                cfg.health_cooldown_s,
            ),
            clock.now,
        )

    return make


class _WallClock:
    """Wall time and an asyncio sleep for the governor's leases and bounded waits."""

    def now(self) -> Any:
        """Aware UTC now."""
        from datetime import UTC, datetime

        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Wait."""
        import asyncio

        await asyncio.sleep(seconds)


def with_governance(runtime: Any) -> Any:
    """The connector runtime with the per-provider governor (Postgres leases, D-43) and in-process
    single flight (P8). Every call then holds a provider slot around its live exchange."""
    from dataclasses import replace

    from parallel.config import load_parallel_config
    from parallel.governor import PostgresGovernor, Shares
    from parallel.singleflight import InProcessSingleFlight

    cfg = load_parallel_config()
    governor = PostgresGovernor(
        tenant_txn,
        cfg.providers,
        Shares(cfg.run_share_pct, cfg.tenant_share_pct, cfg.aging_step_s),
        _WallClock(),
    )
    return replace(runtime, governor=governor, single_flight=InProcessSingleFlight())


def _limits(spec: WorkflowSpec, env: Mapping[str, str]) -> RunLimits:
    settings = spec.settings
    return RunLimits(
        graph_version=str(settings.get("graph_version", spec.version)),
        budget_usd=Decimal(str(env.get("RUN_BUDGET_USD", settings.get("max_budget_usd", 5.0)))),
        tool_call_cap=int(env.get("RUN_TOOL_CALL_CAP", settings.get("max_calls", 60))),
        reserved_credits=Decimal(str(env.get("RUN_RESERVE_CREDITS", "6.0"))),
        cap_required=not dev_uncapped(env),
    )


def _slices(spec: WorkflowSpec) -> SliceSettings:
    s = spec.settings
    from account_intelligence.dev_limits import DEV_SLICE_SECONDS, UNCAPPED

    return SliceSettings(
        # local stack, DEV_UNCAPPED=1: a slice long enough for one long model reply (a 16k-token
        # extraction takes minutes; a call the slice timer cuts off is lost and asked again)
        slice_seconds=DEV_SLICE_SECONDS if dev_uncapped() else float(s.get("slice_seconds", 240)),
        # local stack, DEV_UNCAPPED=1: a run takes as many slices as its research needs
        max_slices=UNCAPPED if dev_uncapped() else int(s.get("max_slices", 60)),
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
    if env.get("LLM_FAKE") and app_env not in ("dev", "test"):
        # the deterministic fake model is hermetic only: refuse before anything else is built
        raise RuntimeMisconfigured(f"LLM_FAKE is hermetic only and refused for APP_ENV={app_env}")
    if env.get("CONNECTORS") == "hermetic" and app_env not in ("dev", "test"):
        raise RuntimeMisconfigured(
            f"CONNECTORS=hermetic is for development and tests and refused for APP_ENV={app_env}"
        )
    from account_intelligence.config import deployment_refusals

    refusals = deployment_refusals(env)  # L13: dev/test on Cloud Run, DEVSTORE_* in the cloud
    if refusals:
        raise RuntimeMisconfigured("; ".join(refusals))
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
    if env.get("LLM_FAKE"):
        llm = hermetic_fake_model(env)
    elif app_env == "dev" and env.get("ANTHROPIC_API_KEY"):
        from account_intelligence.platform.llm import (
            AnthropicLlm,
            anthropic_api_client,
            load_routes,
        )

        llm = AnthropicLlm(
            anthropic_api_client(env["ANTHROPIC_API_KEY"]), load_routes(), "anthropic_api"
        )
    llm = with_model_choice(llm, env)
    connectors, retry = (None, None)
    if env.get("CONNECTORS") == "hermetic":
        connectors, retry = hermetic_connectors(env, payloads)
    if app_env == "dev" and env.get("CONNECTORS") == "on":
        connectors, retry = build_connectors(env, payloads, env.get("REDIS_URL"))
        if dev_uncapped(env):
            from dataclasses import replace

            from account_intelligence.connectors.paginate import PageLimits
            from account_intelligence.dev_limits import DEV_SLICE_SECONDS, UNCAPPED

            # the run lease outlives one (longer) local slice, as the configured one outlives 240 s
            connectors.ledger.lease_ttl_s = int(DEV_SLICE_SECONDS) + 300

            # every page a provider has; repeated cursors and the run deadline still stop a loop
            size = connectors.config.defaults.pagination.max_bytes * 100
            connectors = replace(
                connectors, page_limits_override=PageLimits(UNCAPPED, UNCAPPED, size)
            )
    clock = SystemClock()
    from account_intelligence.api.tenant_plane import DevelopmentSecretStore, unconfigured_plane
    from spend.balances import DevAssumedBalances

    tenant_plane = unconfigured_plane(clock, env.get("PUBLIC_BASE_URL"))
    tenant_plane.secrets = DevelopmentSecretStore()
    tenant_plane = _with_mailer(tenant_plane, env)
    container = Container(
        env=app_env,
        connectors=connectors,
        extras={"retry_policy": retry} if retry is not None else {},
        # the local stack posts slices to its worker container; tests drain in process
        scheduler=(
            HttpTaskScheduler(env["WORKER_URL"])
            if app_env == "dev" and env.get("WORKER_URL")
            else InProcessScheduler()
        ),
        payloads=payloads,
        secrets=EnvSecrets(),
        llm=llm,
        clock=clock,
        builder=build_export_builder(payloads, clock, env, default_renderer="python-interim"),
        spec=spec,
        limits=_limits(spec, env),
        slices=_slices(spec),
        checkpoint_dsn=env.get("CHECKPOINT_DATABASE_URL", ""),
        tenant_plane=tenant_plane,
        # local stack opt-ins (never in CLOUD_ENVS, which return above): fund every provider and
        # approve caps without a person, so a live run goes end to end unattended
        balances=(
            DevAssumedBalances()
            if app_env == "dev" and env.get("DEV_ASSUME_PROVIDER_BALANCE") == "1"
            else None
        ),
        dev_auto_approve=app_env == "dev" and env.get("DEV_AUTO_APPROVE_CAPS") == "1",
        reply_cache=reply_cache(env) if connectors is not None else None,
    )
    container.extras["maildrive_apps"] = maildrive_apps(env)
    blobs = local_blob_store(env.get("PAYLOAD_DIR"))
    container.documents = document_service(blobs, container.scheduler, clock, env, app_env)
    # the pre-delivery gate for both input modes (``documents.gate``): without it no run could
    # ever complete (audit F1)
    container.gate = delivery_gate(payloads, container.documents)
    return container


def document_service(
    blobs: Any, scheduler: Any, clock: Any, env: Mapping[str, str], app_env: str
) -> Any:
    """Uploaded documents (input mode "file"): quarantine, scan, sandboxed parse, index."""
    from documents.config import load_config
    from documents.embed import HashingEmbedder
    from documents.scan import scanner_for
    from documents.service import DocumentService

    cfg = load_config()
    scanner = scanner_for(
        app_env,
        env.get("DOCUMENT_SCANNER", "signature"),
        env.get("CLAMD_HOST"),
        int(env.get("CLAMD_PORT", "3310")),
    )
    embedder = HashingEmbedder(cfg.embedding.dimensions, cfg.embedding.model)
    # retrieval vectors from an on-server transformer (owner decision 2026-10-15) when
    # DOCUMENT_SEMANTIC_EMBEDDER=fastembed; the hashing vectors above stay for the leak gate
    semantic = None
    if env.get("DOCUMENT_SEMANTIC_EMBEDDER") == "fastembed":
        from documents.embed import FastEmbedder

        semantic = FastEmbedder(cache_dir=env.get("FASTEMBED_CACHE_PATH") or None)
    return DocumentService(
        tenant_txn,
        blobs,
        scanner,
        embedder,
        scheduler,
        clock,
        cfg,
        # one part per Cloud Tasks request in the cloud (the 30-minute dispatch maximum), and
        # at most ten minutes of indexing per request (audit 2026-10-16: one request indexed a
        # 37,000-passage file into the hour-long Cloud Run limit six times)
        parts_per_request=1 if app_env in CLOUD_ENVS else None,
        index_seconds_per_request=600.0 if app_env in CLOUD_ENVS else None,
        semantic=semantic,
    )


def delivery_gate(payloads: Any, documents: Any) -> Any:
    """The pre-delivery gate over the client file store and the private document index."""
    from documents.gate import PreDeliveryGate

    return PreDeliveryGate(tenant_txn, payloads, documents.embedder, documents.cfg)


def local_blob_store(payload_dir: str | None) -> Any:
    """Uploaded document objects on the payloads volume (a temporary directory without one)."""
    import tempfile
    from pathlib import Path

    from research_files.blobstore import LocalBlobStore

    root = Path(payload_dir) / "files" if payload_dir else Path(tempfile.mkdtemp(prefix="rf-"))
    root.mkdir(parents=True, exist_ok=True)
    return LocalBlobStore(root)


def hermetic_fake_model(env: Mapping[str, str]) -> FakeModel:
    """The deterministic fake model selected by ``LLM_FAKE=hermetic`` (dev and test only); the
    scenario is a file name under ``config/hermetic/fake_model`` (default ``happy``)."""
    if env.get("LLM_FAKE") != "hermetic":
        raise RuntimeMisconfigured("LLM_FAKE must be 'hermetic' when set")
    name = env.get("FAKE_MODEL_SCENARIO", "happy")
    path = (FAKE_MODEL_DIR / f"{name}.json").resolve()
    if path.parent != FAKE_MODEL_DIR.resolve() or not path.is_file():
        raise RuntimeMisconfigured(f"unknown fake model scenario {name!r}")
    return FakeModel(load_scenario(path))


def _cloud(env: Mapping[str, str], app_env: str, spec: WorkflowSpec) -> Container:
    """Google Cloud adapters (no network call happens while building them)."""
    from google.cloud import secretmanager, tasks_v2
    from google.cloud import storage as gcs  # type: ignore[attr-defined]  # noqa: I001

    from account_intelligence.api.tenant_plane import cloud_plane
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
        documents_queue=env.get("TASKS_QUEUE_DOCUMENTS"),
        jobs_queue=env.get("TASKS_QUEUE_JOBS"),
    )
    payloads = GcsPayloadStore(gcs.Client(project=project), _require(env, "PAYLOAD_BUCKET"))
    access = env.get("LLM_ACCESS", "anthropic_api")
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
    from llm.choice import mark_set_up

    # the Claude client above holds its key (or Vertex AI's identity), so Opus and Fable are set up
    mark_set_up("ANTHROPIC_API_KEY")
    # Kimi K3 (owner decision 2026-10-14): the key from the secret store when MOONSHOT_SECRET
    # names one; without it Kimi K3 is shown "Not set up" and cannot be chosen
    moonshot_secret = env.get("MOONSHOT_SECRET")
    moonshot_key = secrets.get(moonshot_secret) if moonshot_secret else None
    if moonshot_key:
        mark_set_up("MOONSHOT_API_KEY")
    llm = with_model_choice(llm, env, moonshot_key)
    # D-48: the router is switched on with MODEL_ROUTER=on once config/model_routing.yaml names
    # candidates for this access path (V-LLM-2: the Vertex model id format is unverified)
    client = getattr(llm, "client", None)
    router = (
        router_factory(client, access)
        if env.get("MODEL_ROUTER") == "on" and client is not None
        else None
    )
    connectors, retry = build_connectors(env, payloads, _require(env, "REDIS_URL"))
    clock = SystemClock()
    built = Container(
        env=app_env,
        connectors=connectors,
        extras={"retry_policy": retry},
        scheduler=scheduler,
        payloads=payloads,
        secrets=secrets,
        llm=llm,
        clock=clock,
        # D-26: the Node renderer by default; NotConfigured until ported (runs end PARTIAL)
        builder=build_export_builder(payloads, clock, env, default_renderer="python-interim"),
        spec=spec,
        limits=_limits(spec, env),
        slices=_slices(spec),
        checkpoint_dsn=_require(env, "CHECKPOINT_DATABASE_URL"),
        router=router,
        task_audience=_require(env, "TASKS_AUDIENCE"),
        task_service_account=_require(env, "TASKS_SERVICE_ACCOUNT"),
        verify_task_token=verify_task_token,
        tenant_plane=_with_mailer(cloud_plane(secrets, clock, env.get("PUBLIC_BASE_URL")), env),
        reply_cache=reply_cache(env),
    )
    from research_files.blobstore import GcsBlobStore

    blobs = GcsBlobStore(
        gcs.Client(project=project), _require(env, "PAYLOAD_BUCKET"), env.get("BLOB_MOUNT_DIR")
    )
    if env.get("SERVICE_ROLE") == "worker" and not env.get("BLOB_MOUNT_DIR"):
        # the worker parses documents: without the bucket mount every file would be copied into
        # the instance's memory first (Cloud Run has no disk)
        raise RuntimeMisconfigured("BLOB_MOUNT_DIR is required for the worker in the cloud")
    built.extras["maildrive_apps"] = maildrive_apps(env)
    built.documents = document_service(blobs, scheduler, clock, env, app_env)
    built.gate = delivery_gate(built.payloads, built.documents)
    built.tenant_plane = _with_cloud_objects(
        built.tenant_plane,
        gcs.Client(project=project),
        _require(env, "PAYLOAD_BUCKET"),
        built.documents,
    )
    return built


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
    if container.connectors is None or container.extras.get("retry_policy") is None:
        raise RuntimeMisconfigured("the connector runtime and the node RetryPolicy are not wired")
    if not container.checkpoint_dsn:
        raise NotConfigured("checkpoint database is not configured")
