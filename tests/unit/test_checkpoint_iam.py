"""The LangGraph checkpointer signs in like the main engine: Cloud SQL IAM, no password URL
(fix prompt D, task 4a; ADR-0125).

The Cloud SQL Python connector serves asyncpg and pg8000 only; the checkpointer needs a psycopg
``AsyncConnection``. So in the cloud it connects to the instance's private IP with the service's
IAM database user and a short-lived OAuth token (scope ``sqlservice.login``) as the password,
over TLS (``sslmode=verify-ca`` with the server CA when one is mounted, else ``require``; the
instance refuses unencrypted connections). ``CHECKPOINT_DATABASE_URL`` stays for the local
profiles (and as a fallback while a cloud release still carries it).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform import checkpoint
from account_intelligence.platform.checkpoint import (
    CheckpointIam,
    IamTokens,
    checkpoint_target,
    checkpointer_factory,
)


def test_the_cloud_target_is_iam_on_the_private_ip() -> None:
    env = {
        "APP_ENV": "staging",
        "CLOUD_SQL_PRIVATE_IP": "10.20.0.3",
        "DB_IAM_USER": "worker@p.iam",
        "DB_NAME": "ai",
    }
    assert checkpoint_target(env) == CheckpointIam("10.20.0.3", "ai", "worker@p.iam", None)
    env["CLOUD_SQL_CA_FILE"] = "/secrets/sql-ca/ca.pem"
    assert checkpoint_target(env) == CheckpointIam(
        "10.20.0.3", "ai", "worker@p.iam", "/secrets/sql-ca/ca.pem"
    )
    # the local profiles keep their URL; a cloud release that still carries one uses it
    assert checkpoint_target({"APP_ENV": "dev", "CHECKPOINT_DATABASE_URL": "postgresql://x"}) == (
        "postgresql://x"
    )
    assert checkpoint_target({"APP_ENV": "staging"}) == ""


class _Creds:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expiry: datetime | None = None
        self.refreshes = 0

    def refresh(self, request: Any) -> None:
        del request
        self.refreshes += 1
        self.token = f"tok-{self.refreshes}"
        # google-auth keeps expiry naive, in UTC
        self.expiry = (datetime.now(UTC) + timedelta(minutes=60)).replace(tzinfo=None)


def test_the_token_is_reused_until_it_nears_expiry() -> None:
    creds = _Creds()
    tokens = IamTokens(lambda: creds)
    assert asyncio.run(tokens.token()) == "tok-1"
    assert asyncio.run(tokens.token()) == "tok-1"
    creds.expiry = (datetime.now(UTC) + timedelta(minutes=2)).replace(tzinfo=None)
    assert asyncio.run(tokens.token()) == "tok-2"
    assert creds.refreshes == 2


@pytest.mark.parametrize(
    ("ca", "mode"), [(None, "require"), ("/secrets/sql-ca/ca.pem", "verify-ca")]
)
def test_the_iam_saver_signs_in_with_a_token_never_a_stored_password(
    monkeypatch: pytest.MonkeyPatch, ca: str | None, mode: str
) -> None:
    seen: dict[str, Any] = {}

    class _Conn:
        async def execute(self, *a: Any) -> None:
            seen.setdefault("executed", []).append(a)

        async def close(self) -> None:
            seen["closed"] = True

    async def connect(*args: Any, **kwargs: Any) -> _Conn:
        seen["args"], seen["kwargs"] = args, kwargs
        return _Conn()

    async def token(self: Any) -> str:
        return "short-lived"

    monkeypatch.setattr(checkpoint.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(IamTokens, "token", token)
    target = CheckpointIam("10.20.0.3", "ai", "worker@p.iam", ca)
    tenant = uuid4()

    async def go() -> None:
        async with checkpointer_factory(target)(tenant):
            pass

    asyncio.run(go())
    kw = seen["kwargs"]
    assert seen["args"] == ()
    assert {k: kw[k] for k in ("host", "port", "dbname", "user", "password", "sslmode")} == {
        "host": "10.20.0.3",
        "port": 5432,
        "dbname": "ai",
        "user": "worker@p.iam",
        "password": "short-lived",
        "sslmode": mode,
    }
    assert kw.get("sslrootcert") == ca and kw["autocommit"] is True
    assert seen["executed"][0][1] == (str(tenant),) and seen["closed"]


def test_only_the_processes_that_run_slices_need_a_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from account_intelligence.app.container import needs_checkpointer

    assert needs_checkpointer("worker") and needs_checkpointer("all")
    assert needs_checkpointer("job")
    assert not needs_checkpointer("api") and not needs_checkpointer("migrate")
