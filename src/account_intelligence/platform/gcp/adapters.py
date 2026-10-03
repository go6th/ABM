"""Google Cloud adapters for the platform ports. Every SDK call below was read from the installed
package's signature (``docs/open-items.md`` lists versions and what is NOT verified):

* GCS: ``storage.Client.bucket(name)``, ``Bucket.blob(name)``, ``Blob.upload_from_string(data,
  content_type=..., if_generation_match=0)``, ``Blob.download_as_bytes()``.
* Cloud Tasks: ``CloudTasksClient.create_task(parent=..., task=...)``, ``queue_path``,
  ``task_path``, ``Task(name, http_request, schedule_time)``, ``HttpRequest(url, http_method,
  headers, body, oidc_token)``, ``OidcToken(service_account_email, audience)``;
  ``google.api_core.exceptions.AlreadyExists`` on a duplicate task name.
* Secret Manager: ``SecretManagerServiceClient.access_secret_version(name=...)`` and
  ``secret_version_path(project, secret, version)``; the value is ``response.payload.data``.
* ID tokens: ``google.oauth2.id_token.verify_oauth2_token(token, request, audience)``.

The blocking SDK calls run in a worker thread so the event loop never blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from account_intelligence.platform.ports import NotConfigured


class GcsPayloadStore:
    """Raw payloads in one bucket. ``if_generation_match=0`` makes a put create-only: keys are
    content hashes, so an existing object already holds the same bytes (pull once)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._bucket = client.bucket(bucket)

    async def put(self, key: str, body: bytes) -> str:
        """Create-only upload of the gzipped body (an existing key already holds the same bytes)."""
        from google.api_core.exceptions import PreconditionFailed

        blob = self._bucket.blob(key)
        data = gzip.compress(body, mtime=0)
        with contextlib.suppress(PreconditionFailed):  # same key, same content: stored
            await asyncio.to_thread(
                blob.upload_from_string,
                data,
                content_type="application/gzip",
                if_generation_match=0,
            )
        return key

    async def get(self, key: str) -> bytes:
        """Download and decompress one payload."""
        blob = self._bucket.blob(key)
        data: bytes = await asyncio.to_thread(blob.download_as_bytes)
        return gzip.decompress(data)


class CloudTasksScheduler:
    """``RunScheduler`` over Cloud Tasks. Task names are deterministic per (run, slice) so a
    duplicate enqueue is refused by the service (``AlreadyExists``) and treated as success.
    Each task POSTs ids only to the worker with an OIDC token for the worker's audience.
    VERIFY(V-TASKS-1): name-based de-duplication window and dispatch deadline limits are
    service behaviour, not checked here."""

    def __init__(
        self,
        client: Any,
        project: str,
        location: str,
        queue: str,
        worker_url: str,
        service_account_email: str,
        audience: str,
    ) -> None:
        self.client = client
        self.project, self.location, self.queue = project, location, queue
        self.worker_url = worker_url.rstrip("/")
        self.sa, self.audience = service_account_email, audience

    def _task(self, name: str, path: str, payload: dict[str, str], at: datetime | None) -> Any:
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2  # type: ignore[import-untyped]

        task = tasks_v2.Task(
            name=self.client.task_path(self.project, self.location, self.queue, name),
            http_request=tasks_v2.HttpRequest(
                url=f"{self.worker_url}{path}",
                http_method=tasks_v2.HttpMethod.POST,
                headers={"Content-Type": "application/json"},
                body=json.dumps(payload, sort_keys=True).encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self.sa, audience=self.audience
                ),
            ),
        )
        if at is not None:
            stamp = timestamp_pb2.Timestamp()
            stamp.FromDatetime(at)
            task.schedule_time = stamp
        return task

    async def _create(self, task: Any) -> None:
        from google.api_core.exceptions import AlreadyExists

        parent = self.client.queue_path(self.project, self.location, self.queue)
        with contextlib.suppress(AlreadyExists):  # the same named task exists: idempotent
            await asyncio.to_thread(self.client.create_task, parent=parent, task=task)

    async def enqueue_slice(
        self, tenant_id: UUID, run_id: UUID, slice_no: int, delay_s: float = 0.0
    ) -> None:
        """Create the named slice task (duplicate names are success)."""
        from datetime import UTC, timedelta

        at = datetime.now(UTC) + timedelta(seconds=delay_s) if delay_s > 0 else None
        payload = {"tenant_id": str(tenant_id), "run_id": str(run_id), "slice": str(slice_no)}
        await self._create(
            self._task(f"run-{run_id}-slice-{slice_no}", "/tasks/run-slice", payload, at)
        )

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        """Create the run's approval-timeout task at the deadline."""
        payload = {"tenant_id": str(tenant_id), "run_id": str(run_id)}
        await self._create(
            self._task(f"run-{run_id}-approval-timeout", "/tasks/approval-timeout", payload, at)
        )


class SecretManagerSecrets:
    """Secrets from Secret Manager (``latest`` version), read once per name and cached in the
    instance. Values never reach logs or exceptions."""

    def __init__(self, client: Any, project: str) -> None:
        self.client, self.project = client, project
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        """The latest version of a secret (cached per instance)."""
        if name not in self._cache:
            path = self.client.secret_version_path(self.project, name, "latest")
            try:
                response = self.client.access_secret_version(name=path)
            except Exception as error:  # noqa: BLE001 - never echo the SDK message (may name paths)
                raise NotConfigured(f"secret {name} is not readable") from error
            self._cache[name] = response.payload.data.decode("utf-8")
        return self._cache[name]


def verify_task_token(token: str, audience: str, allowed_email: str) -> None:
    """Worker endpoint check: a Google-signed ID token for ``audience`` from the scheduler's
    service account. Raises ``PermissionError`` otherwise (the endpoint answers 403)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token, google_requests.Request(), audience
        )
    except ValueError as error:
        raise PermissionError("invalid task token") from error
    if claims.get("email") != allowed_email or not claims.get("email_verified", False):
        raise PermissionError("task token is not from the scheduler service account")


async def cloud_sql_engine(instance: str, user: str, database: str) -> tuple[Any, Any]:
    """Async SQLAlchemy engine through the Cloud SQL connector with IAM database authentication
    (no password, private IP). Returns ``(engine, connector)``; close the connector on shutdown.
    VERIFY(V-SQL-1): IAM DB auth and private-IP reachability need the real instance."""
    from google.cloud.sql.connector import IPTypes, create_async_connector
    from sqlalchemy.ext.asyncio import create_async_engine

    connector = await create_async_connector(ip_type=IPTypes.PRIVATE, enable_iam_auth=True)

    async def creator() -> Any:
        """Open one asyncpg connection through the connector (IAM auth, private IP)."""
        return await connector.connect_async(instance, "asyncpg", user=user, db=database)

    engine = create_async_engine("postgresql+asyncpg://", async_creator=creator)
    return engine, connector
