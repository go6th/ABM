"""Mail and drive (REPLICA_PROMPT screen 8): per-user, read-only connections to Gmail or Outlook
and Google Drive, OneDrive or SharePoint, and the sync that keeps only what matches the user's
own accounts.

* ``HttpOAuthClient`` is the ``OAuthClient`` for the Google and Microsoft identity platforms:
  the code exchange with PKCE, refresh and (Google only; Microsoft has no revocation endpoint)
  revocation, at the endpoints in ``config/connectors.yaml``. Client ids and secrets come from
  the environment (``MAILDRIVE_GOOGLE_CLIENT_ID`` / ``_SECRET``, ``MAILDRIVE_MICROSOFT_*``);
  without them a source is "Not available yet" and nothing is attempted.
* ``HttpMailDriveReader`` lists recent items through each provider's documented read API
  (subject, preview, date and people for mail; name, date and owners for files). It never reads
  message bodies or file contents and has no write method.
* ``matches``: an item belongs to an account of the user's workspace when one of its people
  has an email address at the account's domain, or the account's name appears as a whole word
  in its title or preview.
* ``sync_connection``: a fresh access token (a revoked or changed grant marks the connection
  broken, fail closed), the items since the last sync (30 days the first time), and one matched
  row per item and account, owned by the user; an item that matches none of the user's accounts
  is never stored. Tokens never leave the server and are never logged; items are private to the
  user who connected them.

[VERIFY] The provider paths and fields follow the providers' public documentation; they are
exercised here against fakes only, until the owner registers real OAuth apps.

GUARANTEE(test_mail_and_drive_keeps_only_matched_items_for_their_owner)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any
from uuid import UUID

import httpx

from connectors_client.ports import TokenRevoked, TokenSet
from connectors_client.scopes import spec

MAIL_KINDS = ("gmail", "outlook")
DRIVE_KINDS = ("google_drive", "onedrive", "sharepoint")
KINDS = MAIL_KINDS + DRIVE_KINDS
LOCATION = {
    "gmail": "Gmail",
    "outlook": "Outlook",
    "google_drive": "Google Drive",
    "onedrive": "OneDrive",
    "sharepoint": "SharePoint",
}
FIRST_SYNC_DAYS = 30
MAX_ITEMS = 200
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def oauth_apps(env: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The registered OAuth apps by provider (client id, client secret); a provider is missing
    until both are set. A secret may be given as a file path (``..._SECRET_FILE``)."""
    out: dict[str, tuple[str, str]] = {}
    for provider in ("google", "microsoft"):
        prefix = f"MAILDRIVE_{provider.upper()}_CLIENT"
        client_id = env.get(f"{prefix}_ID", "")
        secret = env.get(f"{prefix}_SECRET", "")
        path = env.get(f"{prefix}_SECRET_FILE", "")
        if not secret and path:
            try:
                with open(path, encoding="utf-8") as handle:
                    secret = handle.read().strip()
            except OSError:
                continue  # an unreadable file is no secret: the app is not registered
        if client_id and secret:
            out[provider] = (client_id, secret)
    return out


class HttpOAuthClient:
    """``OAuthClient`` for the mail and drive sources (see the module docstring)."""

    def __init__(
        self, apps: Mapping[str, tuple[str, str]], transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.apps = dict(apps)
        self.transport = transport

    def _app(self, kind: str) -> tuple[Any, str, str]:
        sp = spec(kind)
        if sp.provider not in self.apps or not sp.token_endpoint:
            raise TokenRevoked(f"{kind}: no OAuth app is registered")
        client_id, secret = self.apps[sp.provider]
        return sp, client_id, secret

    async def _token(self, kind: str, form: dict[str, str]) -> TokenSet:
        sp, client_id, secret = self._app(kind)
        async with httpx.AsyncClient(transport=self.transport, timeout=20.0) as client:
            reply = await client.post(
                str(sp.token_endpoint),
                data={**form, "client_id": client_id, "client_secret": secret},
            )
        if reply.status_code in (400, 401, 403):
            raise TokenRevoked(f"{kind}: the grant was refused")
        reply.raise_for_status()
        body = reply.json()
        return TokenSet(
            access_token=str(body["access_token"]),
            refresh_token=body.get("refresh_token") or form.get("refresh_token"),
            scopes=tuple(sorted(str(body.get("scope") or "").split())),
            expires_in=int(body.get("expires_in") or 0),
        )

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        """Exchange the authorization code (PKCE)."""
        return await self._token(
            kind,
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
        )

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        """A fresh access token (TokenRevoked when the grant is gone)."""
        return await self._token(
            kind, {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def revoke(self, kind: str, token: str) -> None:
        """Revoke at the source where it offers revocation (Google); elsewhere the stored token
        is destroyed by the caller, which ends the grant on our side."""
        sp = spec(kind)
        if not sp.revoke_endpoint:
            return
        async with httpx.AsyncClient(transport=self.transport, timeout=20.0) as client:
            reply = await client.post(str(sp.revoke_endpoint), data={"token": token})
        reply.raise_for_status()


@dataclass(frozen=True)
class Item:
    """One recent mail thread or file, as listed (metadata only, never a body)."""

    resource_id: str
    kind: str  # thread | file
    title: str
    snippet: str
    when: datetime | None
    people: tuple[str, ...] = ()
    link: str | None = None


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


class HttpMailDriveReader:
    """Lists recent items through each provider's read API (see the module docstring)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def recent(self, kind: str, access_token: str, since: datetime) -> list[Item]:
        """At most ``MAX_ITEMS`` items changed since ``since``."""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20.0, headers=headers
        ) as client:
            if kind == "gmail":
                return await self._gmail(client, since)
            if kind == "outlook":
                return await self._outlook(client, since)
            if kind == "google_drive":
                return await self._drive(client, since)
            return await self._graph_files(client, kind)

    async def _gmail(self, client: httpx.AsyncClient, since: datetime) -> list[Item]:
        days = max(1, (datetime.now(UTC) - since).days + 1)
        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        listed = await client.get(
            f"{base}/messages", params={"q": f"newer_than:{days}d", "maxResults": MAX_ITEMS}
        )
        listed.raise_for_status()
        out: list[Item] = []
        # LOOP(gmail message metadata): cap=MAX_ITEMS listed messages; exit=end of the list
        for ref in (listed.json().get("messages") or [])[:MAX_ITEMS]:
            meta = await client.get(
                f"{base}/messages/{ref['id']}",
                params=[
                    ("format", "metadata"),
                    *[("metadataHeaders", h) for h in ("Subject", "From", "To", "Cc", "Date")],
                ],
            )
            meta.raise_for_status()
            body = meta.json()
            heads = {
                h["name"].lower(): h["value"] for h in body.get("payload", {}).get("headers", [])
            }
            people = tuple(
                parseaddr(a)[1]
                for key in ("from", "to", "cc")
                for a in str(heads.get(key, "")).split(",")
                if parseaddr(a)[1]
            )
            out.append(
                Item(
                    str(body.get("threadId") or ref["id"]),
                    "thread",
                    str(heads.get("subject") or "(no subject)"),
                    str(body.get("snippet") or ""),
                    _date(heads.get("date")),
                    people,
                )
            )
        return out

    async def _outlook(self, client: httpx.AsyncClient, since: datetime) -> list[Item]:
        reply = await client.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            params={
                "$top": str(MAX_ITEMS),
                "$select": "subject,bodyPreview,receivedDateTime,from,toRecipients,"
                "ccRecipients,conversationId",
                "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            },
        )
        reply.raise_for_status()
        out = []
        for m in (reply.json().get("value") or [])[:MAX_ITEMS]:
            people = []
            for who in [
                m.get("from") or {},
                *(m.get("toRecipients") or []),
                *(m.get("ccRecipients") or []),
            ]:
                address = ((who or {}).get("emailAddress") or {}).get("address")
                if address:
                    people.append(str(address))
            out.append(
                Item(
                    str(m.get("conversationId") or m.get("id")),
                    "thread",
                    str(m.get("subject") or "(no subject)"),
                    str(m.get("bodyPreview") or ""),
                    _date(m.get("receivedDateTime")),
                    tuple(people),
                )
            )
        return out

    async def _drive(self, client: httpx.AsyncClient, since: datetime) -> list[Item]:
        stamp = since.strftime("%Y-%m-%dT%H:%M:%S")
        reply = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": f"modifiedTime > '{stamp}' and trashed = false",
                "fields": "files(id,name,modifiedTime,owners(emailAddress),webViewLink)",
                "pageSize": str(MAX_ITEMS),
            },
        )
        reply.raise_for_status()
        return [
            Item(
                str(f["id"]),
                "file",
                str(f.get("name") or "Untitled"),
                "",
                _date(f.get("modifiedTime")),
                tuple(
                    str(o.get("emailAddress"))
                    for o in f.get("owners") or []
                    if o.get("emailAddress")
                ),
                f.get("webViewLink"),
            )
            for f in (reply.json().get("files") or [])[:MAX_ITEMS]
        ]

    async def _graph_files(self, client: httpx.AsyncClient, kind: str) -> list[Item]:
        path = "me/drive/recent" if kind == "onedrive" else "me/insights/used"
        reply = await client.get(f"https://graph.microsoft.com/v1.0/{path}")
        reply.raise_for_status()
        out = []
        for f in (reply.json().get("value") or [])[:MAX_ITEMS]:
            visual = f.get("resourceVisualization") or {}
            ref = f.get("resourceReference") or {}
            out.append(
                Item(
                    str(f.get("id")),
                    "file",
                    str(f.get("name") or visual.get("title") or "Untitled"),
                    "",
                    _date(
                        f.get("lastModifiedDateTime")
                        or (f.get("lastUsed") or {}).get("lastAccessedDateTime")
                    ),
                    (),
                    f.get("webUrl") or ref.get("webUrl"),
                )
            )
        return out


@dataclass(frozen=True)
class Account:
    """One of the user's accounts, as matching needs it."""

    id: UUID
    domain: str
    name: str


def _word(name: str) -> re.Pattern[str] | None:
    words = " ".join(name.split())
    if len(words) < 4:
        return None
    return re.compile(r"(?<![\w])" + re.escape(words) + r"(?![\w])", re.IGNORECASE)


def matches(item: Item, accounts: Sequence[Account]) -> list[UUID]:
    """The accounts an item belongs to (see the module docstring)."""
    domains = {d.lower().rstrip(".") for d in _EMAIL.findall(" ".join(item.people))}
    found = []
    for acct in accounts:
        dom = acct.domain.lower()
        if any(d == dom or d.endswith("." + dom) for d in domains):
            found.append(acct.id)
            continue
        pattern = _word(acct.name)
        if pattern is not None and pattern.search(f"{item.title} {item.snippet}"):
            found.append(acct.id)
    return found


@dataclass
class SyncResult:
    """What one sync of one connection did (counts only)."""

    listed: int = 0
    matched: int = 0
    broken: str | None = None
    accounts: set[UUID] = field(default_factory=set)


def item_hash(item: Item, account_id: UUID) -> str:
    """The stored row's identity: one row per item and account."""
    return hashlib.sha256(f"{item.resource_id}|{account_id}".encode()).hexdigest()


async def sync_connection(
    txn: Any,
    tenant_id: UUID,
    connection_id: UUID,
    *,
    oauth: Any,
    reader: Any,
    store: Any,
    now: datetime,
) -> SyncResult:
    """One sync of one active per-user connection (see the module docstring)."""
    from sqlalchemy import text

    from connectors_client import framework
    from connectors_client import oauth as consent
    from connectors_client.scopes import load

    result = SyncResult()
    async with txn(tenant_id) as s:
        conn = (
            await s.execute(
                text(
                    "SELECT kind, token_ref, scopes, owner_user_id, last_synced_at"
                    " FROM connector_connections WHERE id = :c AND status = 'active'"
                ),
                {"c": connection_id},
            )
        ).first()
        if conn is None or conn.owner_user_id is None or conn.kind not in KINDS:
            return result
        rows = (
            await s.execute(text("SELECT id, domain, coalesce(name, domain) AS name FROM accounts"))
        ).all()
    accounts = [Account(UUID(str(r.id)), str(r.domain), str(r.name)) for r in rows]
    try:
        tokens = await consent.access_token(
            oauth,
            store,
            kind=conn.kind,
            token_ref=conn.token_ref,
            granted=tuple(conn.scopes or ()),
            attempts=load()[1].refresh_attempts,
        )
    except consent.ConnectorRefused as refused:
        async with txn(tenant_id) as s:
            await consent.mark_broken(s, connection_id, refused.reason)
        result.broken = refused.reason
        return result
    since = conn.last_synced_at or now - timedelta(days=FIRST_SYNC_DAYS)
    items = await reader.recent(conn.kind, tokens.access_token, since)
    result.listed = len(items)
    async with txn(tenant_id) as s:
        for item in items:
            for account_id in matches(item, accounts):
                await framework.store_matched_item(
                    s,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    owner_user_id=str(conn.owner_user_id),
                    account_id=account_id,
                    resource_id=item.resource_id,
                    sha256=item_hash(item, account_id),
                    kind=item.kind,
                    title=item.title[:500],
                    snippet=item.snippet[:2000],
                    when=item.when,
                    location=LOCATION[conn.kind],
                    now=now,
                )
                result.matched += 1
                result.accounts.add(account_id)
        await framework.mark_synced(s, connection_id, now)
    return result
