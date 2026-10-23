"""Fake Google and Microsoft Graph endpoints for the Mail and drive connectors (SYNTHETIC data
only, no network): ``httpx.MockTransport`` handlers that serve the token, revoke and read APIs the
real ``HttpOAuthClient`` and ``HttpMailDriveReader`` call, with paging, incremental cursors
(Gmail history, Drive changes, Graph delta), stale cursors and scripted faults (429 with
Retry-After, 401, 503).

Each fake keeps a change sequence: every added item gets the next number, a cursor is a number
in that sequence, and a page token is ``<cursor>.<offset>``. ``fail`` is a queue of statuses the
read API answers before serving normally (the token endpoints have their own ``token_fail``).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GRAPH = "https://graph.microsoft.com/v1.0"


def _recent() -> datetime:
    """Yesterday (by the real clock: the sync windows are relative to it)."""
    return datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)


@dataclass
class _Entry:
    seq: int
    body: dict[str, Any]
    when: datetime


@dataclass
class _Feed:
    """Items in change order."""

    entries: list[_Entry] = field(default_factory=list)

    @property
    def seq(self) -> int:
        return self.entries[-1].seq if self.entries else 0

    def add(self, body: dict[str, Any], when: datetime) -> None:
        self.entries.append(_Entry(self.seq + 1, body, when))

    def after(self, seq: int, since: datetime | None = None) -> list[_Entry]:
        return [e for e in self.entries if e.seq > seq and (since is None or e.when >= since)]


def _page(rows: list[Any], offset: int, size: int) -> tuple[list[Any], int | None]:
    chunk = rows[offset : offset + size]
    nxt = offset + size
    return chunk, (nxt if nxt < len(rows) else None)


def _fault(queue: deque[int]) -> httpx.Response | None:
    if not queue:
        return None
    status = queue.popleft()
    headers = {"Retry-After": "7"} if status == 429 else {}
    return httpx.Response(status, headers=headers, json={"error": {"code": str(status)}})


class FakeGoogle:
    """Google's token and revoke endpoints, Gmail (messages, metadata, profile, history) and
    Drive (files, changes), recording what they were asked."""

    def __init__(self, *, scope: str = GMAIL_SCOPE, page_size: int = 50) -> None:
        self.scope = scope
        self.page_size = page_size
        self.revoked: list[str] = []
        self.token_forms: list[dict[str, str]] = []
        self.token_fail: deque[int] = deque()
        self.fail: deque[int] = deque()
        self.calls: list[str] = []
        self.mail = _Feed()
        self.files = _Feed()
        self.oldest_history = 0  # a startHistoryId / page token below this is too old (404)
        self.access = "access-1"
        self.transport = httpx.MockTransport(self.handle)
        # the two threads the replica test expects: one matches highmark, one matches nothing
        self.add_mail("m1", "t1", "Pilot scope", "Ann <ann@highmark.example>", "Next steps")
        self.add_mail("m2", "t2", "Lunch", "bob@unrelated.example", "Friday?", dated=False)

    def add_mail(
        self,
        mid: str,
        thread: str,
        subject: str,
        sender: str,
        snippet: str = "",
        *,
        to: str = "",
        when: datetime | None = None,
        dated: bool = True,
        labels: tuple[str, ...] = ("INBOX",),
    ) -> None:
        """One message in the mailbox (the next history id)."""
        when = when or _recent()
        heads = [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}]
        if to:
            heads.append({"name": "To", "value": to})
        if dated:
            heads.append({"name": "Date", "value": when.strftime("%a, %d %b %Y %H:%M:%S +0000")})
        body = {
            "id": mid,
            "threadId": thread,
            "labelIds": list(labels),
            "snippet": snippet,
            "payload": {"headers": heads},
        }
        self.mail.add(body, when)

    def add_file(self, fid: str, name: str, owner: str, *, when: datetime | None = None) -> None:
        """One Drive file (the next change)."""
        when = when or _recent()
        self.files.add(
            {
                "id": fid,
                "name": name,
                "mimeType": "application/pdf",
                "modifiedTime": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "owners": [{"emailAddress": owner}],
                "webViewLink": f"https://drive.example/{fid}",
            },
            when,
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            fault = _fault(self.token_fail)
            if fault is not None:
                return fault
            return httpx.Response(
                200,
                json={
                    "access_token": self.access,
                    "refresh_token": "refresh-1",
                    "scope": self.scope,
                    "expires_in": 3600,
                },
            )
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        self.calls.append(request.url.path)
        fault = _fault(self.fail)
        if fault is not None:
            return fault
        if request.headers.get("authorization") != f"Bearer {self.access}":
            return httpx.Response(401)
        path, q = request.url.path, request.url.params
        if path.endswith("/users/me/profile"):
            return httpx.Response(200, json={"historyId": str(self.mail.seq)})
        if path.endswith("/users/me/messages"):
            since = None
            found = re.search(r"after:(\d+)", q.get("q", ""))
            if found:
                since = datetime.fromtimestamp(int(found.group(1)), UTC)
            rows = [
                {"id": e.body["id"], "threadId": e.body["threadId"]}
                for e in reversed(self.mail.after(0, since))
            ]
            chunk, nxt = _page(rows, int(q.get("pageToken") or 0), self.page_size)
            body: dict[str, Any] = {"messages": chunk}
            if nxt is not None:
                body["nextPageToken"] = str(nxt)
            return httpx.Response(200, json=body)
        if "/users/me/messages/" in path:
            mid = path.rsplit("/", 1)[1]
            for e in self.mail.entries:
                if e.body["id"] == mid:
                    return httpx.Response(200, json=e.body)
            return httpx.Response(404)
        if path.endswith("/users/me/history"):
            start = int(q["startHistoryId"])
            if start < self.oldest_history:
                return httpx.Response(404, json={"error": {"code": 404}})
            rows = [
                {
                    "id": str(e.seq),
                    "messagesAdded": [
                        {
                            "message": {
                                "id": e.body["id"],
                                "threadId": e.body["threadId"],
                                "labelIds": e.body["labelIds"],
                            }
                        }
                    ],
                }
                for e in self.mail.after(start)
            ]
            chunk, nxt = _page(rows, int(q.get("pageToken") or 0), self.page_size)
            body = {"history": chunk, "historyId": str(self.mail.seq)}
            if nxt is not None:
                body["nextPageToken"] = str(nxt)
            return httpx.Response(200, json=body)
        if path.endswith("/changes/startPageToken"):
            return httpx.Response(200, json={"startPageToken": str(self.files.seq)})
        if path.endswith("/drive/v3/files"):
            since = None
            found = re.search(r"modifiedTime > '([^']+)'", q.get("q", ""))
            if found:
                since = datetime.fromisoformat(found.group(1)).replace(tzinfo=UTC)
            rows = [e.body for e in self.files.after(0, since)]
            chunk, nxt = _page(rows, int(q.get("pageToken") or 0), self.page_size)
            body = {"files": chunk}
            if nxt is not None:
                body["nextPageToken"] = str(nxt)
            return httpx.Response(200, json=body)
        if path.endswith("/drive/v3/changes"):
            start, _, raw_offset = q["pageToken"].partition(".")
            if int(start) < self.oldest_history:
                return httpx.Response(404)
            rows = [
                {"fileId": e.body["id"], "removed": False, "file": e.body}
                for e in self.files.after(int(start))
            ]
            chunk, nxt = _page(rows, int(raw_offset or 0), self.page_size)
            body = {"changes": chunk}
            if nxt is not None:
                body["nextPageToken"] = f"{start}.{nxt}"
            else:
                body["newStartPageToken"] = str(self.files.seq)
            return httpx.Response(200, json=body)
        return httpx.Response(404)


class FakeGraph:
    """Microsoft's token endpoint (a new refresh token on every call, Graph scopes as full URLs
    plus sign-in scopes) and Graph: message delta per mail folder, drive root delta and
    insights/used, with next and delta links."""

    TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

    def __init__(
        self,
        *,
        scope: str = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
        " openid profile email offline_access",
        page_size: int = 50,
    ) -> None:
        self.scope = scope
        self.page_size = page_size
        self.issued = 0
        self.no_refresh = False  # answer without a refresh token
        self.token_forms: list[dict[str, str]] = []
        self.token_fail: deque[int] = deque()
        self.fail: deque[int] = deque()
        self.calls: list[str] = []
        self.folders = {"inbox": _Feed(), "sentitems": _Feed()}
        self.drive = _Feed()
        self.used: list[dict[str, Any]] = []
        self.expired: set[str] = set()  # delta tokens the fake no longer accepts (410)
        self.transport = httpx.MockTransport(self.handle)

    @property
    def access(self) -> str:
        return f"ms-access-{self.issued}"

    def add_message(
        self,
        mid: str,
        subject: str,
        sender: str,
        *,
        folder: str = "inbox",
        to: tuple[str, ...] = (),
        when: datetime | None = None,
        preview: str = "",
    ) -> None:
        when = when or _recent()
        self.folders[folder].add(
            {
                "id": mid,
                "conversationId": f"conv-{mid}",
                "subject": subject,
                "bodyPreview": preview,
                "receivedDateTime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "from": {"emailAddress": {"address": sender}},
                "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            },
            when,
        )

    def add_drive_file(
        self, fid: str, name: str, *, when: datetime | None = None, folder: bool = False
    ) -> None:
        when = when or _recent()
        body: dict[str, Any] = {
            "id": fid,
            "name": name,
            "lastModifiedDateTime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "webUrl": f"https://onedrive.example/{fid}",
        }
        body["folder" if folder else "file"] = {}
        self.drive.add(body, when)

    def add_used(self, iid: str, title: str, *, when: datetime | None = None) -> None:
        when = when or _recent()
        self.used.append(
            {
                "id": iid,
                "resourceVisualization": {"title": title},
                "resourceReference": {"webUrl": f"https://sharepoint.example/{iid}"},
                "lastUsed": {"lastAccessedDateTime": when.strftime("%Y-%m-%dT%H:%M:%SZ")},
            }
        )

    def _delta(self, feed: _Feed, base: str, q: Any, since: datetime | None) -> httpx.Response:
        if "$deltatoken" in q:
            token = q["$deltatoken"]
            if token in self.expired:
                return httpx.Response(410, json={"error": {"code": "syncStateNotFound"}})
            start, offset = int(token), 0
        elif "$skiptoken" in q:
            raw_start, _, raw_offset = q["$skiptoken"].partition(".")
            start, offset = int(raw_start), int(raw_offset)
            if "f" in q:
                since = datetime.fromisoformat(q["f"].replace("Z", "+00:00"))
        else:
            start, offset = 0, 0
        rows = [e.body for e in feed.after(start, since if start == 0 else None)]
        chunk, nxt = _page(rows, offset, self.page_size)
        body: dict[str, Any] = {"value": chunk}
        if nxt is not None:
            extra = f"&f={since.strftime('%Y-%m-%dT%H:%M:%SZ')}" if since and start == 0 else ""
            body["@odata.nextLink"] = f"{base}?$skiptoken={start}.{nxt}{extra}"
        else:
            body["@odata.deltaLink"] = f"{base}?$deltatoken={feed.seq}"
        return httpx.Response(200, json=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(self.TOKEN):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            fault = _fault(self.token_fail)
            if fault is not None:
                return fault
            self.issued += 1
            body: dict[str, Any] = {
                "access_token": self.access,
                "scope": self.scope,
                "expires_in": 3600,
            }
            if not self.no_refresh:
                body["refresh_token"] = f"ms-refresh-{self.issued}"
            return httpx.Response(200, json=body)
        self.calls.append(request.url.path)
        fault = _fault(self.fail)
        if fault is not None:
            return fault
        if request.headers.get("authorization") != f"Bearer {self.access}":
            return httpx.Response(401)
        path, q = request.url.path, request.url.params
        base = f"https://graph.microsoft.com{path}"
        folder = re.fullmatch(r"/v1\.0/me/mailFolders/([a-z]+)/messages/delta", path)
        if folder:
            since = None
            found = re.search(r"receivedDateTime ge (\S+)", q.get("$filter", ""))
            if found:
                since = datetime.fromisoformat(found.group(1).replace("Z", "+00:00"))
            return self._delta(self.folders[folder.group(1)], base, q, since)
        if path == "/v1.0/me/drive/root/delta":
            return self._delta(self.drive, base, q, None)
        if path == "/v1.0/me/insights/used":
            offset = int(q.get("$skiptoken") or 0)
            chunk, nxt = _page(self.used, offset, self.page_size)
            used: dict[str, Any] = {"value": chunk}
            if nxt is not None:
                used["@odata.nextLink"] = f"{base}?$skiptoken={nxt}"
            return httpx.Response(200, json=used)
        return httpx.Response(404)


class RecordingSleep:
    """An ``asyncio.sleep`` stand-in that records the waits and never waits."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
