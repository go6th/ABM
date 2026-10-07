"""In-memory fakes of the tenant-plane ports (SYNTHETIC; never used outside tests).

``FakeClock`` (virtual time), ``FakeSecretStore`` (records every read so a test can prove a token
was never read by the wrong path), ``FakeObjectStore`` (put-if-absent, prefix listing),
``FakeMailer`` (records messages), ``FakeScanner`` (the ``spend.upload.ScanPort`` shape, verdict
per key or a global outage).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from account_intelligence.platform.ports import NotConfigured


@dataclass
class FakeClock:
    """Virtual time that only moves when a test advances it."""

    current: datetime = field(default_factory=lambda: datetime(2026, 3, 2, 9, 0, tzinfo=UTC))

    def now(self) -> datetime:
        """The current virtual time."""
        return self.current

    def advance(self, **delta: float) -> datetime:
        """Move virtual time forward."""
        self.current = self.current + timedelta(**delta)
        return self.current


@dataclass
class FakeSecretStore:
    """Secrets in memory; references are opaque and carry no value."""

    values: dict[str, bytes] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    destroyed: set[str] = field(default_factory=set)

    async def put(self, name: str, value: bytes) -> str:
        """Store a new version; the reference hashes name and a counter, never the value."""
        ref = f"secret-ref/{name}/v{len(self.values) + 1}"
        self.values[ref] = value
        return ref

    async def read(self, ref: str) -> bytes:
        """The value; KeyError when missing or destroyed."""
        self.reads.append(ref)
        if ref in self.destroyed:
            raise KeyError(ref)
        return self.values[ref]

    async def destroy(self, ref: str) -> None:
        """Destroy (idempotent)."""
        self.destroyed.add(ref)


class ObjectConflict(ValueError):
    """Different bytes at an existing key."""


@dataclass
class FakeObjectStore:
    """Objects in memory with put-if-absent semantics."""

    objects: dict[str, bytes] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)

    async def put_if_absent(self, key: str, body: bytes, content_type: str) -> bool:
        """Store unless present; identical bytes are a no-op, different bytes a conflict."""
        if key in self.objects:
            if hashlib.sha256(self.objects[key]).digest() != hashlib.sha256(body).digest():
                raise ObjectConflict(key)
            return False
        self.objects[key] = body
        self.types[key] = content_type
        return True

    async def get(self, key: str) -> bytes:
        """The bytes; KeyError when missing."""
        return self.objects[key]

    async def list_prefix(self, prefix: str, limit: int) -> list[str]:
        """Sorted keys under the prefix, at most ``limit``."""
        return sorted(k for k in self.objects if k.startswith(prefix))[:limit]


@dataclass
class FakeMailer:
    """Records every message instead of sending it."""

    sent: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    async def send(self, to: str, template: str, params: Mapping[str, str]) -> str:
        """Record the message; returns a synthetic message reference."""
        self.sent.append((to, template, dict(params)))
        return f"msg-{len(self.sent)}"


@dataclass
class FakeScanner:
    """``spend.upload.ScanPort``: clean unless a key is marked infected; ``down`` raises."""

    infected: set[str] = field(default_factory=set)
    down: bool = False
    scanned: list[str] = field(default_factory=list)

    async def scan(self, key: str) -> bool:
        """True when clean."""
        if self.down:
            raise NotConfigured("scanner unavailable")
        self.scanned.append(key)
        return key not in self.infected


class FakeStaffIdp:
    """The 6th staff IdP: tokens ``<role>:<staff id>``; anything else is invalid."""

    def verify(self, token: str) -> Any:
        from account_intelligence.api.operator import StaffIdentity

        role, _, who = token.partition(":")
        if role not in ("operator", "support") or not who:
            raise PermissionError("invalid staff token")
        return StaffIdentity(who, role)  # type: ignore[arg-type]
