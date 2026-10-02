"""Neutral-key, content-addressed JSON storage for internal raw broker payloads."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError

ROOT = Path(__file__).resolve().parents[2]
DENYLIST_PATH = (
    ROOT
    / ".claude"
    / "skills"
    / "account-intelligence-platform"
    / "assets"
    / "client_output_denylist.txt"
)
STORE_CONFIG_PATH = ROOT / "config" / "storage.yaml"


@dataclass(frozen=True)
class StoredPayload:
    object_key: str
    content_hash: str
    payload: Any


class RawPayloadStore(Protocol):
    async def put_json(
        self, tenant_id: UUID, account_id: UUID, run_id: UUID, payload: Any
    ) -> tuple[str, str]: ...

    async def get_json(self, object_key: str) -> Any: ...


def _denylist_patterns() -> tuple[re.Pattern[str], ...]:
    try:
        lines = DENYLIST_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DataLayerError(f"cannot read client output denylist: {exc}") from exc
    return tuple(
        re.compile(line, re.IGNORECASE)
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


def _assert_neutral_key(value: str) -> None:
    for pattern in _denylist_patterns():
        if pattern.search(value):
            raise DataLayerError(
                "raw payload key or metadata contains a client-output denylist term"
            )


class LocalRawPayloadStore:
    """Local filesystem implementation; keys contain identifiers and hashes only."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    async def put_json(
        self, tenant_id: UUID, account_id: UUID, run_id: UUID, payload: Any
    ) -> tuple[str, str]:
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DataLayerError(f"raw payload is not JSON serializable: {exc}") from exc
        content_hash = hashlib.sha256(encoded).hexdigest()
        object_key = f"{tenant_id}/{account_id}/{run_id}/{content_hash}.json.gz"
        _assert_neutral_key(object_key)
        destination = (self.root / object_key).resolve()
        if not destination.is_relative_to(self.root):
            raise DataLayerError("raw payload object key escapes the configured storage root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        compressed = gzip.compress(encoded, mtime=0)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".pending-", suffix=".tmp", dir=destination.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(compressed)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                existing = gzip.decompress(destination.read_bytes())
                if hashlib.sha256(existing).hexdigest() != content_hash:
                    raise DataLayerError(
                        "content-addressed raw payload key has different content"
                    ) from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return object_key, content_hash

    async def get_json(self, object_key: str) -> Any:
        _assert_neutral_key(object_key)
        candidate = (self.root / object_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise DataLayerError("raw payload object key escapes the configured storage root")
        try:
            raw = gzip.decompress(candidate.read_bytes())
            value = json.loads(raw)
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, ValueError) as exc:
            raise DataLayerError(f"cannot read raw payload object {object_key}: {exc}") from exc
        actual_hash = hashlib.sha256(canonical).hexdigest()
        if not object_key.endswith(f"/{actual_hash}.json.gz"):
            raise DataLayerError("raw payload content hash does not match its neutral key")
        return value


def load_raw_payload_store(config_path: Path = STORE_CONFIG_PATH) -> RawPayloadStore:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataLayerError(f"cannot load raw payload storage config: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("raw_payloads"), dict):
        raise DataLayerError("storage config requires a raw_payloads mapping")
    raw_config = config["raw_payloads"]
    if raw_config.get("backend") != "local":
        raise DataLayerError(
            f"unsupported raw payload backend {raw_config.get('backend')!r}; "
            "only the local backend is implemented"
        )
    root = raw_config.get("local_root")
    if not isinstance(root, str) or not root.strip():
        raise DataLayerError("local raw payload backend requires local_root")
    return LocalRawPayloadStore((ROOT / root).resolve())


async def get_or_store_payload(
    session: AsyncSession,
    store: RawPayloadStore,
    *,
    account_id: UUID,
    run_id: UUID,
    args_hash: str,
    provider_code: str,
    fetch: Callable[[], Awaitable[Any]],
) -> StoredPayload:
    """Serialize same-run/same-args pulls via a transaction advisory lock."""
    if not re.fullmatch(r"[0-9a-f]{64}", args_hash):
        raise DataLayerError("args_hash must be a lowercase SHA-256 digest")
    if not re.fullmatch(r"p_[a-z0-9_]+", provider_code):
        raise DataLayerError("provider identity must use an internal provider code")
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to store raw payloads")
    lock_key = f"{tenant_id}:{run_id}:{args_hash}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    existing = (
        await session.execute(
            text(
                "SELECT object_key, content_hash FROM raw_payloads "
                "WHERE tenant_id = :tenant_id AND run_id = :run_id AND args_hash = :args_hash"
            ),
            {"tenant_id": tenant_id, "run_id": run_id, "args_hash": args_hash},
        )
    ).one_or_none()
    if existing is not None:
        payload = await store.get_json(existing.object_key)
        return StoredPayload(existing.object_key, existing.content_hash, payload)

    payload = await fetch()
    object_key, content_hash = await store.put_json(tenant_id, account_id, run_id, payload)
    await session.execute(
        text(
            "INSERT INTO raw_payloads (tenant_id, account_id, run_id, args_hash, object_key, "
            "content_hash, provider_code) VALUES (:tenant_id, :account_id, :run_id, :args_hash, "
            ":object_key, :content_hash, :provider_code)"
        ),
        {
            "tenant_id": tenant_id,
            "account_id": account_id,
            "run_id": run_id,
            "args_hash": args_hash,
            "object_key": object_key,
            "content_hash": content_hash,
            "provider_code": provider_code,
        },
    )
    return StoredPayload(object_key, content_hash, payload)
