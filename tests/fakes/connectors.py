"""Fake connector servers (SYNTHETIC; tests only).

``FakeOAuth`` mints a token set per code (PKCE and redirect URI enforced), refreshes (or refuses,
or changes scopes on demand) and records revocations. ``FakeCrm`` serves records with more fields
than were asked for. Every call is counted so a test
can match calls to access-log rows. Tokens carry a canary so a test can prove they never leak.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from connectors_client.ports import CrmRecord, Page, TokenRevoked, TokenSet

TOKEN_CANARY = "TOKEN-CANARY-c0ffee"


@dataclass
class FakeOAuth:
    """A source's OAuth endpoints."""

    scopes: tuple[str, ...] = ("api", "refresh_token")
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_mode: str = "ok"  # ok | revoked | widen | flaky
    revoked: list[str] = field(default_factory=list)
    revoke_fails: bool = False
    refreshes: int = 0

    def consent(self, authorize_url: str) -> tuple[str, str]:
        """What the source does when the admin consents: returns (state, code)."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"challenge": q["code_challenge"], "redirect": q["redirect_uri"]}
        return q["state"], code

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        entry = self.codes.pop(code, None)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if entry is None or entry["challenge"] != challenge or entry["redirect"] != redirect_uri:
            raise TokenRevoked("bad code")
        return TokenSet(f"access-{TOKEN_CANARY}", f"refresh-{TOKEN_CANARY}", self.scopes, 3600)

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        self.refreshes += 1
        if self.refresh_mode == "revoked":
            raise TokenRevoked("revoked")
        if self.refresh_mode == "flaky":
            raise RuntimeError("transient")
        scopes = self.scopes + (("extra.scope",) if self.refresh_mode == "widen" else ())
        return TokenSet(f"access-{TOKEN_CANARY}", None, scopes, 3600)

    async def revoke(self, kind: str, token: str) -> None:
        if self.revoke_fails:
            raise RuntimeError("source unreachable")
        self.revoked.append(token)


@dataclass
class FakeCrm:
    """CRM records; each record also carries a field nobody asked for."""

    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def list_records(
        self, access_token: str, obj: str, fields: tuple[str, ...], page_token: str | None
    ) -> Page:
        assert TOKEN_CANARY in access_token
        self.calls.append(("list", obj))
        rows = self.records.get(obj, [])
        return Page(
            tuple(CrmRecord(obj, str(r["Id"]), {**r, "SecretNotes": "CRM-CANARY"}) for r in rows)
        )
