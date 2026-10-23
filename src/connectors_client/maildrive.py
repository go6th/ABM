"""Mail and drive (REPLICA_PROMPT screen 8): per-user, read-only connections to Gmail or Outlook
and Google Drive, OneDrive or SharePoint, and the sync that keeps only what matches the user's
own accounts.

* ``HttpOAuthClient`` is the ``OAuthClient`` for the Google and Microsoft identity platforms:
  the code exchange with PKCE, refresh and (Google only; Microsoft has no revocation endpoint)
  revocation, at the endpoints in ``config/connectors.yaml``. Client ids and secrets come from
  the environment (``MAILDRIVE_GOOGLE_CLIENT_ID`` / ``_SECRET``, ``MAILDRIVE_MICROSOFT_*``);
  without them a source is "Not available yet" and nothing is attempted. A provider that is
  down or unreachable is ``SourceError("provider_unavailable")``; a refused grant is
  ``TokenRevoked``.
* ``HttpMailDriveReader.read`` lists items through each provider's documented read API
  (subject, preview, date and people for mail; name, date and owners for files), one bounded
  pass at a time, and returns a ``ReadResult``: the items, a resumable cursor and whether the
  pass reached the end. Incremental where the provider offers it (Gmail history, Drive changes,
  Graph delta for Outlook and OneDrive; SharePoint lists recently used files since the last
  sync); a cursor the provider rejects falls back to a time-window resync. Throttling (429, 5xx)
  is retried after Retry-After, bounded; a 401 is ``TokenExpired``. It never reads message
  bodies or file contents and has no write method.
* ``matches``: an item belongs to an account of the user's workspace when one of its people
  has an email address at the account's domain (never the user's own domain or a free-mail
  domain), or the account's name appears as a whole word in its title or preview.
* ``sync_connection``: a fresh access token (a revoked or changed grant marks the connection
  broken, fail closed; a provider that is down is a failed sync, retried next time), one read
  pass, and one matched row per item and account, owned by the user; an item that matches none
  of the user's accounts is never stored. The cursor and ``last_synced_at`` advance only when
  the pass completed; every sync records its state (migration 0082). Tokens never leave the
  server and are never logged; items are private to the user who connected them.

[VERIFY] The provider paths and fields follow the providers' public documentation; they are
exercised here against fakes only, until the owner registers real OAuth apps.

GUARANTEE(test_mail_and_drive_keeps_only_matched_items_for_their_owner)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from account_intelligence.config import get_settings
from connectors_client.ports import SourceError, TokenRevoked, TokenSet
from connectors_client.scopes import spec
from obs.logging import logger

log = logger(__name__)

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
MAX_ITEMS = 200  # items kept per pass; the rest resume from the cursor next time
MAX_PAGES = 50  # provider list pages per pass
PAGE_SIZE = 100
MAX_ATTEMPTS = 4  # per provider call, on 429 / 5xx / a dropped connection
MAX_WAIT_S = 30.0  # the longest single wait, whatever Retry-After says
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
GMAIL_CONCURRENCY = 8
OUTLOOK_FOLDERS = ("inbox", "sentitems")
CURSOR_VERSION = 1
# well-known free-mail domains: a participant there says nothing about which company it is
FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.co.uk",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.de",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "yandex.ru",
        "qq.com",
        "163.com",
        "126.com",
    }
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PROVIDER_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
_CODE = re.compile(r"[a-z_]{1,40}")
_DRIVE_FOLDER = "application/vnd.google-apps.folder"


class TokenExpired(RuntimeError):
    """The provider refused the access token (401): refresh once and read again."""


class _StaleCursor(Exception):
    """The provider no longer accepts the stored cursor: resync from the time window."""


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
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20.0, follow_redirects=False
            ) as client:
                reply = await client.post(
                    str(sp.token_endpoint),
                    data={**form, "client_id": client_id, "client_secret": secret},
                )
        except httpx.TransportError:
            raise SourceError("provider_unavailable") from None
        if reply.status_code in (400, 401, 403):
            raise TokenRevoked(f"{kind}: the grant was refused")
        if reply.status_code == 429 or reply.status_code >= 500:
            raise SourceError("provider_unavailable")
        if reply.status_code >= 300:
            raise SourceError("provider_rejected")
        try:
            body = reply.json()
            access = str(body["access_token"])
        except (ValueError, KeyError, TypeError):
            raise SourceError("provider_rejected") from None
        return TokenSet(
            access_token=access,
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
        """A fresh access token (TokenRevoked when the grant is gone). Microsoft returns a new
        refresh token with each refresh; the caller stores it (``oauth.access_token``)."""
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


@dataclass(frozen=True)
class ReadResult:
    """One read pass: the items, the cursor to store, and whether the pass reached the end.

    ``complete``: the source has nothing more since the cursor, which is now incremental (the
    next pass lists only what changed). Not complete: the pass stopped at its item or page
    bound and ``cursor`` resumes it. ``cursor`` is a small JSON object (``CURSOR_VERSION``,
    the source kind and its own fields) or None when the source keeps no position."""

    items: tuple[Item, ...]
    cursor: str | None
    complete: bool
    waited_s: float = 0.0
    pages: int = 0


# purpose (connector_access_log: list | metadata), outcome (ok | denied | error), resource
CallLog = Callable[[str, str, str | None], None]


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


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(when: datetime | None, since: datetime | None) -> bool:
    """True unless both are known and ``when`` is before ``since``."""
    if when is None or since is None:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when >= since


def _retry_after(reply: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait: Retry-After (seconds or an HTTP date) or 1, 2, 4 ...; at most
    ``MAX_WAIT_S``."""
    default = float(2**attempt)
    raw = reply.headers.get("Retry-After") if reply is not None else None
    if raw:
        try:
            default = float(raw)
        except ValueError:
            when = _date(raw)
            if when is not None:
                default = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, min(default, MAX_WAIT_S))


def _load_cursor(kind: str, cursor: str | None) -> dict[str, Any]:
    """The stored position, or an empty one (a fresh window) when absent or not ours."""
    if not cursor:
        return {}
    try:
        state = json.loads(cursor)
    except ValueError:
        return {}
    if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
        return {}
    if state.get("kind") != kind:
        return {}
    return {k: v for k, v in state.items() if k not in ("v", "kind")}


def _dump_cursor(kind: str, state: Mapping[str, Any]) -> str | None:
    kept = {k: v for k, v in state.items() if v is not None}
    if not kept:
        return None
    return json.dumps({"v": CURSOR_VERSION, "kind": kind, **kept}, separators=(",", ":"))


@dataclass
class _Page:
    """One provider list page: the raw entries kept, the next page and the final marker."""

    entries: list[Any]
    next: str | None
    final: Any = None


class _Session:
    """The provider calls of one read pass: bounded retries, the call log, the page budget."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]],
        on_call: CallLog | None,
        max_pages: int,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.on_call = on_call
        self.max_pages = max_pages
        self.waited = 0.0
        self.pages = 0

    def _log(self, purpose: str, outcome: str, resource: str | None) -> None:
        if self.on_call is not None:
            self.on_call(purpose, outcome, resource)

    async def get(
        self,
        url: str,
        *,
        resource: str,
        params: Any = None,
        headers: Mapping[str, str] | None = None,
        stale: frozenset[int] = frozenset(),
    ) -> dict[str, Any]:
        """A list call (see ``call``)."""
        body = await self.call(url, resource=resource, params=params, headers=headers, stale=stale)
        return body or {}

    async def call(
        self,
        url: str,
        *,
        resource: str,
        params: Any = None,
        headers: Mapping[str, str] | None = None,
        purpose: str = "list",
        stale: frozenset[int] = frozenset(),
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        """GET ``url`` as JSON. 401: ``TokenExpired``; a status in ``stale``: the cursor is
        rejected; 404 with ``missing_ok``: None; 429 / 5xx / a dropped connection: retried after
        Retry-After, at most ``MAX_ATTEMPTS`` times (then ``provider_unavailable``)."""
        # LOOP(mail and drive provider call): cap=MAX_ATTEMPTS attempts, each wait at most
        # MAX_WAIT_S; exit=an answer that is not 429 or 5xx, a 401, or the attempts are spent
        for attempt in range(MAX_ATTEMPTS):
            reply: httpx.Response | None
            try:
                reply = await self.client.get(url, params=params, headers=headers)
            except httpx.TransportError:
                reply = None
            if reply is not None:
                status = reply.status_code
                if status < 300:
                    self._log(purpose, "ok", resource)
                    try:
                        body = reply.json()
                    except ValueError:
                        raise SourceError("provider_rejected") from None
                    return body if isinstance(body, dict) else {}
                if status == 401:
                    self._log(purpose, "denied", resource)
                    raise TokenExpired(resource)
                if status in stale:
                    self._log(purpose, "error", resource)
                    raise _StaleCursor(resource)
                if status == 404 and missing_ok:
                    self._log(purpose, "error", resource)
                    return None
                if status not in RETRY_STATUSES:
                    self._log(purpose, "error", resource)
                    raise SourceError("provider_rejected")
            if attempt + 1 == MAX_ATTEMPTS:
                break
            wait = _retry_after(reply, attempt)
            await self.sleep(wait)
            self.waited += wait
        self._log(purpose, "error", resource)
        raise SourceError("provider_unavailable")

    async def walk(
        self,
        state: dict[str, Any],
        limit: int,
        taken: list[Any],
        fetch: Callable[[str | None], Awaitable[_Page]],
    ) -> tuple[bool, _Page | None]:
        """Pages from ``state['page']`` (the first page when absent) into ``taken`` until the
        last page (True and that page) or the item / page bound (False; ``state`` then holds the
        page to resume from and how many of its entries were already taken)."""
        # LOOP(mail and drive pages): cap=max_pages list pages and `limit` entries per pass;
        # exit=the last page, the item limit, the page cap (the cursor resumes)
        while True:
            room = limit - len(taken)
            if room <= 0 or self.pages >= self.max_pages:
                return False, None
            page = await fetch(state.get("page"))
            self.pages += 1
            skip = int(state.pop("skip", 0) or 0)
            fresh = page.entries[skip:]
            if len(fresh) > room:
                taken.extend(fresh[:room])
                state["skip"] = skip + room
                return False, None
            taken.extend(fresh)
            if page.next:
                state["page"] = page.next
                continue
            state.pop("page", None)
            return True, page


class HttpMailDriveReader:
    """Lists items through each provider's read API (see the module docstring)."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self.transport = transport
        self.sleep = sleep
        self.max_pages = max_pages

    async def recent(self, kind: str, access_token: str, since: datetime) -> list[Item]:
        """At most ``MAX_ITEMS`` items changed since ``since`` (one fresh pass, no cursor)."""
        page = await self.read(kind, access_token, since=since, cursor=None, limit=MAX_ITEMS)
        return list(page.items)

    async def read(
        self,
        kind: str,
        access_token: str,
        *,
        since: datetime,
        cursor: str | None = None,
        limit: int = MAX_ITEMS,
        on_call: CallLog | None = None,
    ) -> ReadResult:
        """One bounded pass from ``cursor`` (a window from ``since`` when there is none, or when
        the provider rejects it). ``on_call`` sees every provider call (never a token)."""
        if kind not in KINDS:
            raise SourceError("unknown_source")
        headers = {"Authorization": f"Bearer {access_token}"}
        # never follows a redirect: the bearer token stays on the provider's host
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20.0, headers=headers, follow_redirects=False
        ) as client:
            session = _Session(client, self.sleep, on_call, self.max_pages)
            state = _load_cursor(kind, cursor)
            try:
                items, complete = await self._pass(session, kind, state, since, limit)
            except _StaleCursor:
                state = {}  # resync from the time window, once
                try:
                    items, complete = await self._pass(session, kind, state, since, limit)
                except _StaleCursor:
                    raise SourceError("cursor_rejected") from None
        return ReadResult(
            tuple(items), _dump_cursor(kind, state), complete, session.waited, session.pages
        )

    async def _pass(
        self, s: _Session, kind: str, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        if kind == "gmail":
            return await self._gmail(s, state, since, limit)
        if kind == "outlook":
            return await self._outlook(s, state, since, limit)
        if kind == "google_drive":
            return await self._drive(s, state, since, limit)
        if kind == "onedrive":
            return await self._onedrive(s, state, since, limit)
        return await self._sharepoint(s, state, since, limit)

    # Gmail: messages.list over the window (taking the mailbox historyId first, so nothing
    # falls between), then users.history.list from that historyId (404: too old, resync).

    async def _gmail(
        self, s: _Session, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        base = get_settings().gmail_api_base.rstrip("/") + "/users/me"
        refs: list[dict[str, Any]] = []
        if state.get("mode") == "history":

            async def history(page: str | None) -> _Page:
                params: dict[str, Any] = {
                    "startHistoryId": state["hid"],
                    "historyTypes": "messageAdded",
                    "maxResults": PAGE_SIZE,
                }
                if page:
                    params["pageToken"] = page
                body = await s.get(
                    f"{base}/history",
                    params=params,
                    resource="gmail:history",
                    stale=frozenset({404}),
                )
                if body.get("historyId"):
                    state["next_hid"] = str(body["historyId"])
                added = [
                    m["message"]
                    for h in body.get("history") or []
                    for m in h.get("messagesAdded") or []
                    if isinstance(m.get("message"), dict)
                    and not {"SPAM", "TRASH", "DRAFT"} & set(m["message"].get("labelIds") or ())
                ]
                return _Page(added, body.get("nextPageToken"))

            complete, _ = await s.walk(state, limit, refs, history)
            if complete:
                state["hid"] = state.pop("next_hid", state["hid"])
        else:
            state["mode"] = "window"
            if not state.get("hid"):
                profile = await s.get(f"{base}/profile", resource="gmail:profile")
                state["hid"] = str(profile.get("historyId") or "") or None
            state.setdefault("q", f"after:{int(since.timestamp())}")

            async def window(page: str | None) -> _Page:
                params: dict[str, Any] = {"q": state["q"], "maxResults": PAGE_SIZE}
                if page:
                    params["pageToken"] = page
                body = await s.get(f"{base}/messages", params=params, resource="gmail:messages")
                return _Page(list(body.get("messages") or []), body.get("nextPageToken"))

            complete, _ = await s.walk(state, limit, refs, window)
            if complete:
                hid = state.get("hid")
                state.clear()
                if hid:  # incremental from here on; without one the next pass is a window
                    state.update(mode="history", hid=hid)
        return await self._gmail_items(s, base, refs), complete

    async def _gmail_items(self, s: _Session, base: str, refs: list[dict[str, Any]]) -> list[Item]:
        seen: set[str] = set()
        ids = []
        for ref in refs:
            mid = str(ref.get("id") or "")
            if _PROVIDER_ID.fullmatch(mid) and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        gate = asyncio.Semaphore(GMAIL_CONCURRENCY)
        params = [
            ("format", "metadata"),
            *[("metadataHeaders", h) for h in ("Subject", "From", "To", "Cc", "Date")],
        ]

        async def one(mid: str) -> Item | None:
            async with gate:
                body = await s.call(
                    f"{base}/messages/{mid}",
                    params=params,
                    purpose="metadata",
                    resource=mid,
                    missing_ok=True,  # deleted since it was listed
                )
            return None if body is None else _gmail_item(body, mid)

        # bounded: at most GMAIL_CONCURRENCY metadata calls in flight, `limit` in all
        done = await asyncio.gather(*(one(mid) for mid in ids), return_exceptions=True)
        failures = [d for d in done if isinstance(d, BaseException)]
        if failures:
            raise next((f for f in failures if isinstance(f, TokenExpired)), failures[0])
        return [d for d in done if isinstance(d, Item)]

    # Outlook: Graph message delta per folder (inbox, sent items); the first round is limited
    # to the window; a 410 means the delta token expired (resync).

    async def _outlook(
        self, s: _Session, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        graph = get_settings().microsoft_graph_api_base.rstrip("/")
        links: dict[str, str] = dict(state.get("links") or {})
        out: list[Item] = []
        # the first round (no delta link yet, or still finishing it) is limited to the window
        window = state.get("since") or (None if links else _iso(since))
        # LOOP(outlook folders): cap=len(OUTLOOK_FOLDERS) folders; exit=all folders read, or a
        # folder stopped at the pass bound (the cursor resumes it)
        for index in range(int(state.get("folder") or 0), len(OUTLOOK_FOLDERS)):
            folder = OUTLOOK_FOLDERS[index]
            state["folder"] = index
            start = links.get(folder)

            async def fetch(
                page: str | None, folder: str = folder, start: str | None = start
            ) -> _Page:
                if page or start:
                    url, params = _graph_link(graph, str(page or start)), None
                else:
                    url = f"{graph}/me/mailFolders/{folder}/messages/delta"
                    params = {
                        "$select": "subject,bodyPreview,receivedDateTime,from,toRecipients,"
                        "ccRecipients,conversationId",
                    }
                    if window:
                        params["$filter"] = f"receivedDateTime ge {window}"
                body = await s.get(
                    url,
                    params=params,
                    headers={"Prefer": f"odata.maxpagesize={PAGE_SIZE}"},
                    resource=f"outlook:{folder}",
                    stale=frozenset({410}),
                )
                floor = _date(window) if window else None
                kept = [
                    item
                    for m in body.get("value") or []
                    if "@removed" not in m
                    for item in [_outlook_item(m)]
                    if _after(item.when, floor)
                ]
                return _Page(kept, body.get("@odata.nextLink"), body.get("@odata.deltaLink"))

            if window:
                state["since"] = window
            complete, last = await s.walk(state, limit, out, fetch)
            if not complete:
                state["links"] = links or None
                return out, False
            if last is not None and last.final:
                links[folder] = str(last.final)
            state["links"] = links
        state.pop("folder", None)
        state.pop("since", None)
        return out, True

    # Google Drive: files.list over the window (taking changes.getStartPageToken first), then
    # changes.list from that token (an invalid token: resync).

    async def _drive(
        self, s: _Session, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        base = get_settings().google_drive_api_base.rstrip("/")
        file_fields = "id,name,mimeType,modifiedTime,trashed,owners(emailAddress),webViewLink"
        out: list[Item] = []
        if state.get("mode") == "changes":

            async def changes(page: str | None) -> _Page:
                body = await s.get(
                    f"{base}/changes",
                    params={
                        "pageToken": page or state["token"],
                        "pageSize": str(PAGE_SIZE),
                        "includeRemoved": "false",
                        "spaces": "drive",
                        "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,"
                        f"file({file_fields}))",
                    },
                    resource="google_drive:changes",
                    stale=frozenset({400, 404, 410}),
                )
                kept = [
                    _drive_item(c["file"])
                    for c in body.get("changes") or []
                    if not c.get("removed") and _drive_file(c.get("file"))
                ]
                return _Page(kept, body.get("nextPageToken"), body.get("newStartPageToken"))

            complete, last = await s.walk(state, limit, out, changes)
            if complete and last is not None and last.final:
                state["token"] = str(last.final)
            return out, complete
        state["mode"] = "window"
        if not state.get("token"):
            started = await s.get(
                f"{base}/changes/startPageToken", resource="google_drive:start_page_token"
            )
            state["token"] = str(started.get("startPageToken") or "") or None
        state.setdefault("since", since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"))

        async def files(page: str | None) -> _Page:
            params = {
                "q": f"modifiedTime > '{state['since']}' and trashed = false",
                "fields": f"nextPageToken,files({file_fields})",
                "pageSize": str(PAGE_SIZE),
            }
            if page:
                params["pageToken"] = page
            body = await s.get(f"{base}/files", params=params, resource="google_drive:files")
            kept = [_drive_item(f) for f in body.get("files") or [] if _drive_file(f)]
            return _Page(kept, body.get("nextPageToken"))

        complete, _ = await s.walk(state, limit, out, files)
        if complete:
            token = state.get("token")
            state.clear()
            if token:
                state.update(mode="changes", token=token)
        return out, complete

    # OneDrive: Graph drive delta from the root; the first enumeration keeps files changed in
    # the window, later rounds every changed file; a 410 means resync.

    async def _onedrive(
        self, s: _Session, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        graph = get_settings().microsoft_graph_api_base.rstrip("/")
        link = state.get("link")
        window = None if link else (state.get("since") or _iso(since))
        floor = _date(window) if window else None

        async def fetch(page: str | None) -> _Page:
            if page or link:
                url, params = _graph_link(graph, str(page or link)), None
            else:
                url = f"{graph}/me/drive/root/delta"
                params = {
                    "$select": "id,name,lastModifiedDateTime,webUrl,file,folder,deleted,"
                    "createdBy,lastModifiedBy"
                }
            body = await s.get(
                url, params=params, resource="onedrive:delta", stale=frozenset({410})
            )
            kept = [
                item
                for f in body.get("value") or []
                if "file" in f and "deleted" not in f and "@removed" not in f
                for item in [_graph_file_item(f)]
                if _after(item.when, floor)
            ]
            return _Page(kept, body.get("@odata.nextLink"), body.get("@odata.deltaLink"))

        out: list[Item] = []
        if window:
            state["since"] = window
        complete, last = await s.walk(state, limit, out, fetch)
        if complete:
            state.clear()
            if last is not None and last.final:
                state["link"] = str(last.final)
        return out, complete

    # SharePoint: the files the user used recently (Graph insights), kept when used since the
    # last sync; no provider cursor beyond the page being read.

    async def _sharepoint(
        self, s: _Session, state: dict[str, Any], since: datetime, limit: int
    ) -> tuple[list[Item], bool]:
        graph = get_settings().microsoft_graph_api_base.rstrip("/")
        floor = _date(state.get("since")) or since
        state["since"] = _iso(floor)

        async def fetch(page: str | None) -> _Page:
            url = _graph_link(graph, page) if page else f"{graph}/me/insights/used"
            body = await s.get(url, resource="sharepoint:insights")
            kept = [
                item
                for f in body.get("value") or []
                for item in [_insight_item(f)]
                if _after(item.when, floor)
            ]
            return _Page(kept, body.get("@odata.nextLink"))

        out: list[Item] = []
        complete, _ = await s.walk(state, limit, out, fetch)
        if complete:
            state.clear()
        return out, complete


def _graph_link(graph: str, url: str) -> str:
    """A next or delta link, only on the Graph host (the bearer token never goes elsewhere)."""
    base, there = urlsplit(graph), urlsplit(url)
    if (there.scheme, there.netloc) != (base.scheme, base.netloc):
        raise SourceError("provider_rejected")
    return url


def _gmail_item(body: Mapping[str, Any], mid: str) -> Item:
    heads = {
        str(h.get("name", "")).lower(): h.get("value", "")
        for h in (body.get("payload") or {}).get("headers") or []
    }
    people = tuple(
        parseaddr(a)[1]
        for key in ("from", "to", "cc")
        for a in str(heads.get(key, "")).split(",")
        if parseaddr(a)[1]
    )
    return Item(
        str(body.get("threadId") or mid),
        "thread",
        str(heads.get("subject") or "(no subject)"),
        str(body.get("snippet") or ""),
        _date(heads.get("date")),
        people,
    )


def _outlook_item(m: Mapping[str, Any]) -> Item:
    people = []
    for who in [
        m.get("from") or {},
        *(m.get("toRecipients") or []),
        *(m.get("ccRecipients") or []),
    ]:
        address = ((who or {}).get("emailAddress") or {}).get("address")
        if address:
            people.append(str(address))
    return Item(
        str(m.get("conversationId") or m.get("id")),
        "thread",
        str(m.get("subject") or "(no subject)"),
        str(m.get("bodyPreview") or ""),
        _date(m.get("receivedDateTime")),
        tuple(people),
    )


def _drive_file(f: Any) -> bool:
    return (
        isinstance(f, dict)
        and bool(f.get("id"))
        and not f.get("trashed")
        and f.get("mimeType") != _DRIVE_FOLDER
    )


def _drive_item(f: Mapping[str, Any]) -> Item:
    return Item(
        str(f["id"]),
        "file",
        str(f.get("name") or "Untitled"),
        "",
        _date(f.get("modifiedTime")),
        tuple(str(o.get("emailAddress")) for o in f.get("owners") or [] if o.get("emailAddress")),
        f.get("webViewLink"),
    )


def _graph_file_item(f: Mapping[str, Any]) -> Item:
    people = tuple(
        str(who["user"]["email"])
        for who in (f.get("createdBy"), f.get("lastModifiedBy"))
        if isinstance(who, dict) and isinstance(who.get("user"), dict) and who["user"].get("email")
    )
    return Item(
        str(f.get("id")),
        "file",
        str(f.get("name") or "Untitled"),
        "",
        _date(f.get("lastModifiedDateTime")),
        people,
        f.get("webUrl"),
    )


def _insight_item(f: Mapping[str, Any]) -> Item:
    visual = f.get("resourceVisualization") or {}
    ref = f.get("resourceReference") or {}
    return Item(
        str(f.get("id")),
        "file",
        str(f.get("name") or visual.get("title") or "Untitled"),
        "",
        _date(
            f.get("lastModifiedDateTime") or (f.get("lastUsed") or {}).get("lastAccessedDateTime")
        ),
        (),
        f.get("webUrl") or ref.get("webUrl"),
    )


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


def _under(domain: str, parents: Iterable[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in parents)


def matches(
    item: Item, accounts: Sequence[Account], *, exclude_domains: Iterable[str] = ()
) -> list[UUID]:
    """The accounts an item belongs to (see the module docstring). A participant at a free-mail
    domain or at one of ``exclude_domains`` (the user's own) never matches by domain."""
    skip = FREE_MAIL_DOMAINS | {d.lower().rstrip(".") for d in exclude_domains if d}
    domains = {
        d
        for d in (d.lower().rstrip(".") for d in _EMAIL.findall(" ".join(item.people)))
        if not _under(d, skip)
    }
    found = []
    for acct in accounts:
        dom = acct.domain.lower()
        if any(_under(d, (dom,)) for d in domains):
            found.append(acct.id)
            continue
        pattern = _word(acct.name)
        if pattern is not None and pattern.search(f"{item.title} {item.snippet}"):
            found.append(acct.id)
    return found


SyncState = Literal["ok", "partial", "failed", "broken", "skipped"]


@dataclass
class SyncResult:
    """What one sync of one connection did (counts and a fixed reason code only).

    ``state``: ``ok`` (read to the end), ``partial`` (stopped at the pass bound; the next sync
    resumes), ``failed`` (the provider could not be read; still active, retried next time),
    ``broken`` (the grant is revoked or changed; reconnect), ``skipped`` (not synced: no OAuth
    app, no secret store, or no longer active)."""

    listed: int = 0
    matched: int = 0
    broken: str | None = None
    skipped: bool = False
    accounts: set[UUID] = field(default_factory=set)
    state: SyncState = "ok"
    reason: str | None = None


def item_hash(item: Item, account_id: UUID) -> str:
    """The stored row's identity: one row per item and account."""
    return hashlib.sha256(f"{item.resource_id}|{account_id}".encode()).hexdigest()


def _code(value: str) -> str:
    return value if _CODE.fullmatch(value) else "sync_error"


async def _read(
    reader: Any,
    kind: str,
    access_token: str,
    *,
    since: datetime,
    cursor: str | None,
    limit: int,
    on_call: CallLog,
) -> ReadResult:
    """``reader.read`` (a reader with only ``recent`` reads one uncursored window)."""
    read = getattr(reader, "read", None)
    if read is None:
        items = await reader.recent(kind, access_token, since)
        return ReadResult(tuple(items[:limit]), None, True)
    result: ReadResult = await read(
        kind, access_token, since=since, cursor=cursor, limit=limit, on_call=on_call
    )
    return result


async def sync_connection(
    txn: Any,
    tenant_id: UUID,
    connection_id: UUID,
    *,
    oauth: Any,
    reader: Any,
    store: Any,
    now: datetime,
    apps: Mapping[str, Any] | None = None,
    limit: int = MAX_ITEMS,
) -> SyncResult:
    """One sync of one active per-user connection (see the module docstring). With ``apps``, a
    connection whose provider has no registered OAuth app is skipped: it cannot be refreshed, and
    that is "not available", not a broken grant. Never raises for a provider failure (the state
    says what happened); an unexpected error is recorded as ``sync_error`` and re-raised."""
    from sqlalchemy import text

    from account_intelligence.platform.ports import NotConfigured
    from connectors_client import oauth as consent
    from connectors_client.scopes import load

    result = SyncResult()
    async with txn(tenant_id) as s:
        conn = (
            await s.execute(
                text(
                    "SELECT kind, token_ref, scopes, owner_user_id, provider, last_synced_at,"
                    " cursor FROM connector_connections WHERE id = :c AND status = 'active'"
                ),
                {"c": connection_id},
            )
        ).first()
        # a personal Mail and drive source names its provider; an admin's client-wide source
        # (owned by that admin since migration 0063) is not synced here
        if conn is None or conn.provider is None or conn.kind not in KINDS:
            result.state, result.skipped, result.reason = "skipped", True, "not_active"
            return result
        if apps is not None and spec(conn.kind).provider not in apps:
            # no OAuth app registered: not available, not broken
            result.state, result.skipped, result.reason = "skipped", True, "no_oauth_app"
            return result
        if not conn.token_ref:  # an active grant with no stored token cannot sync: fail closed
            await consent.mark_broken(s, connection_id, "token_missing")
            result.state, result.broken, result.reason = "broken", "token_missing", "token_missing"
            return result
        owner = str(conn.owner_user_id)
        # the connection owner's own accounts only: the sync runs in system scope, where every
        # colleague's private accounts are visible (audit 2026-10-15)
        rows = (
            await s.execute(
                text(
                    "SELECT id, domain, coalesce(name, domain) AS name FROM accounts"
                    " WHERE owner_user_id = :u"
                ),
                {"u": owner},
            )
        ).all()
        email = (
            await s.execute(text("SELECT email FROM users WHERE subject = :u"), {"u": owner})
        ).scalar_one_or_none()
    accounts = [Account(UUID(str(r.id)), str(r.domain), str(r.name)) for r in rows]
    own = str(email).rpartition("@")[2].lower() if email and "@" in str(email) else None
    kind = str(conn.kind)
    calls: list[tuple[str, str, str | None]] = []
    ref = str(conn.token_ref)

    def on_call(purpose: str, outcome: str, resource: str | None) -> None:
        """Remember one provider call for the access log (written with the sync's result)."""
        calls.append((purpose, outcome, resource))

    async def rotated(new_ref: str) -> bool:
        """Point the connection at the rotated refresh token (False: it is no longer active)."""
        nonlocal ref
        async with txn(tenant_id) as s:
            kept = await consent.replace_token_ref(s, connection_id, new_ref)
        if kept:
            ref = new_ref
        return kept

    async def token() -> TokenSet:
        """A fresh access token by refresh (a rotated refresh token is written back first)."""
        tokens: TokenSet = await consent.access_token(
            oauth,
            store,
            kind=kind,
            token_ref=ref,
            granted=tuple(conn.scopes or ()),
            attempts=load()[1].refresh_attempts,
            secret_name=f"tenants/{tenant_id}/connectors/{connection_id}",
            on_rotated=rotated,
        )
        on_call("token_refresh", "ok", None)
        return tokens

    since = conn.last_synced_at or now - timedelta(days=FIRST_SYNC_DAYS)
    page: ReadResult | None = None
    try:
        access = (await token()).access_token
        try:
            page = await _read(
                reader, kind, access, since=since, cursor=conn.cursor, limit=limit, on_call=on_call
            )
        except TokenExpired:
            # the access token was refused: refresh once and read once more
            access = (await token()).access_token
            try:
                page = await _read(
                    reader,
                    kind,
                    access,
                    since=since,
                    cursor=conn.cursor,
                    limit=limit,
                    on_call=on_call,
                )
            except TokenExpired:
                raise consent.ConnectorRefused("token_revoked") from None
    except NotConfigured:
        # the secret store (or the OAuth app) is not configured here: not available, not failed
        result.state, result.skipped, result.reason = "skipped", True, "not_configured"
    except consent.ConnectorRefused as refused:
        if refused.reason == "connection_gone":
            result.state, result.skipped, result.reason = "skipped", True, "not_active"
        elif refused.transient:
            on_call("token_refresh", "error", None)
            result.state, result.reason = "failed", refused.reason
        else:
            on_call("token_refresh", "denied", None)
            result.state, result.broken, result.reason = "broken", refused.reason, refused.reason
    except SourceError as error:
        result.state, result.reason = "failed", _code(error.code)
    except Exception:  # recorded as a failed sync, then re-raised for the caller to log
        result.state, result.reason = "failed", "sync_error"
        await _record(txn, tenant_id, connection_id, kind, owner, result, None, [], calls, now)
        raise
    items: list[tuple[Item, list[UUID]]] = []
    if page is not None:
        result.state = "ok" if page.complete else "partial"
        result.listed = len(page.items)
        exclude = (own,) if own else ()
        items = [(item, matches(item, accounts, exclude_domains=exclude)) for item in page.items]
    await _record(txn, tenant_id, connection_id, kind, owner, result, page, items, calls, now)
    finished: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "kind": kind,
        "state": result.state,
        "listed": result.listed,
        "matched": result.matched,
        "waited_s": round(page.waited_s, 1) if page is not None else 0.0,
    }
    if result.reason:
        finished["reason"] = result.reason
    log.info("maildrive.sync_finished", extra=finished)
    return result


async def _record(
    txn: Any,
    tenant_id: UUID,
    connection_id: UUID,
    kind: str,
    owner: str,
    result: SyncResult,
    page: ReadResult | None,
    items: list[tuple[Item, list[UUID]]],
    calls: list[tuple[str, str, str | None]],
    now: datetime,
) -> None:
    """Write one sync in one transaction: the access log, the matched items and the sync state.
    A connection that ended meanwhile (disconnected) gets the log only: nothing is stored for
    it again."""
    from sqlalchemy import text

    from connectors_client import access_log, framework
    from connectors_client import oauth as consent

    async with txn(tenant_id) as s:
        status = (
            await s.execute(
                text("SELECT status FROM connector_connections WHERE id = :c FOR UPDATE"),
                {"c": connection_id},
            )
        ).scalar_one_or_none()
        for purpose, outcome, resource in calls:
            await access_log.log(
                s,
                connection_id=connection_id,
                principal=owner,
                kind=kind,
                purpose=purpose,
                outcome=outcome,
                at=now,
                resource_id=resource,
            )
        if status != "active" or result.state == "skipped":
            return
        if result.state == "broken":
            await consent.mark_broken(s, connection_id, str(result.broken))
        if page is not None:
            for item, account_ids in items:
                for account_id in account_ids:
                    await framework.store_matched_item(
                        s,
                        tenant_id=tenant_id,
                        connection_id=connection_id,
                        owner_user_id=owner,
                        account_id=account_id,
                        resource_id=item.resource_id,
                        sha256=item_hash(item, account_id),
                        kind=item.kind,
                        title=item.title[:500],
                        snippet=item.snippet[:2000],
                        when=item.when,
                        location=LOCATION[kind],
                        now=now,
                    )
                    result.matched += 1
                    result.accounts.add(account_id)
        await framework.record_sync(
            s,
            connection_id,
            now=now,
            status="failed" if result.state in ("failed", "broken") else result.state,
            listed=result.listed,
            matched=result.matched,
            cursor=page.cursor if page is not None else None,
            complete=page is not None and page.complete,
            error=result.reason if result.state in ("failed", "broken") else None,
        )
