"""OneDrive files for a "From a file" research (connectors_client.drive_files, SYNTHETIC), against
an offline Microsoft Graph fake. Only readable files are listed (recent or searched, paged by
nextLink on the Graph host only, never folders or files shared from another drive), a download
follows Graph's redirect without sending the token to the download host, sizes and types are
capped, 429 and 503 wait for Retry-After a bounded number of times, and the routes use only the
caller's own OneDrive connection (none: 409 drive_not_connected)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest

from connectors_client import drive_files as D
from tests.replica.conftest import _call, _plane, world  # noqa: F401 - pytest fixture re-export

GRAPH = "https://graph.test/v1.0"
DOWNLOAD = "https://download.test/blob/abc?tempauth=signed"
SCOPES = ("offline_access", "https://graph.microsoft.com/Files.Read")
PDF = b"%PDF-1.7 synthetic"


def item(n: int, name: str, **over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": f"01ONEDRIVEITEM{n:06d}",
        "name": name,
        "size": 18,
        "file": {"mimeType": "application/octet-stream"},
        "lastModifiedDateTime": f"2026-10-{n % 28 + 1:02d}T09:00:00Z",
        "webUrl": "https://contoso-my.sharepoint.test/personal/x",
    }
    raw.update(over)
    return raw


RECENT = [
    item(1, "Proposal.pdf"),
    item(2, "Board deck.pptx"),
    {"id": "01ONEDRIVEFOLDER0001", "name": "Plans", "folder": {"childCount": 3}},
    item(3, "logo.png"),
    item(4, "Shared.docx", remoteItem={"id": "X!1", "parentReference": {"driveId": "other"}}),
    item(5, "Notes", file={"mimeType": "text/plain"}),  # no extension: cannot be typed
    item(6, "Personal plan.xlsx", id="D4648F06C91D9D3D!54927"),
]


class FakeGraph:
    """Graph's drive endpoints and a separate download host, recording every request."""

    def __init__(self, *, body: bytes = PDF, throttle: int = 0, status: int = 429) -> None:
        self.seen: list[httpx.Request] = []
        self.body = body
        self.throttle = throttle
        self.status = status
        self.items = {raw["id"]: raw for raw in RECENT if "id" in raw}
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.throttle:
            self.throttle -= 1
            return httpx.Response(self.status, headers={"Retry-After": "7"})
        if request.url.host == "download.test":
            return httpx.Response(200, content=self.body)
        assert request.url.host == "graph.test"
        assert request.headers["Authorization"] == "Bearer access-od"
        path = request.url.path
        if path.endswith("/me/drive/recent"):
            return httpx.Response(200, json={"value": RECENT})
        if "/me/drive/root/search(" in path:
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"value": [item(9, "Plan v2.docx")]})
            return httpx.Response(
                200,
                json={
                    "value": [item(8, "Plan v1.docx"), RECENT[2]],
                    "@odata.nextLink": f"{GRAPH}/me/drive/root/search(q='plan')?page=2",
                },
            )
        if path.endswith("/content"):
            return httpx.Response(302, headers={"Location": DOWNLOAD})
        found = self.items.get(path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=found) if found else httpx.Response(404, json={})


def reader(graph: FakeGraph, waits: list[float] | None = None) -> D.HttpOneDriveFiles:
    async def sleep(seconds: float) -> None:
        if waits is not None:
            waits.append(seconds)

    return D.HttpOneDriveFiles(graph.transport, base=GRAPH, sleep=sleep)


def test_recent_files_keep_only_readable_files_of_the_users_own_drive() -> None:
    graph = FakeGraph()
    files = asyncio.run(reader(graph).list("access-od"))
    assert {f.name for f in files} == {"Proposal.pdf", "Board deck.pptx", "Personal plan.xlsx"}
    assert [f.modified for f in files] == sorted((f.modified or "" for f in files), reverse=True)
    pdf = next(f for f in files if f.name == "Proposal.pdf")
    assert (pdf.extension, pdf.size, pdf.export_mime, pdf.import_name) == (
        "pdf", 18, None, "Proposal.pdf",
    )  # fmt: skip
    assert "webUrl" not in repr(pdf)
    assert graph.seen[0].url.path == "/v1.0/me/drive/recent"


def test_search_pages_by_next_link_and_never_leaves_the_graph_host() -> None:
    graph = FakeGraph()
    files = asyncio.run(reader(graph).list("access-od", "Plan's 'deck"))
    assert [f.name for f in files] == ["Plan v2.docx", "Plan v1.docx"]  # the folder is skipped
    first = graph.seen[0]
    assert first.url.raw_path.startswith(b"/v1.0/me/drive/root/search(q='Plans%20deck')")
    assert first.url.params["$top"] == str(D.PAGE_SIZE)
    assert graph.seen[1].url.params["page"] == "2" and len(graph.seen) == 2

    class Elsewhere(FakeGraph):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            link = "https://attacker.test/next"
            return httpx.Response(200, json={"value": [item(1, "a.pdf")], "@odata.nextLink": link})

    away = Elsewhere()
    assert len(asyncio.run(reader(away).list("access-od", "a"))) == 1
    assert [r.url.host for r in away.seen] == ["graph.test"]  # the off-host next link is ignored


def test_list_stops_at_the_page_size() -> None:
    class Many(FakeGraph):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            start = int(request.url.params.get("from", "0"))
            batch = [item(start + i, f"f{start + i}.pdf") for i in range(30)]
            nxt = f"{GRAPH}/me/drive/recent?from={start + 30}"
            return httpx.Response(200, json={"value": batch, "@odata.nextLink": nxt})

    many = Many()
    files = asyncio.run(reader(many).list("access-od"))
    assert len(files) == D.PAGE_SIZE and len(many.seen) == 2


def test_import_follows_the_redirect_without_the_token(tmp_path: Path) -> None:
    graph = FakeGraph()
    od = reader(graph)
    file = asyncio.run(od.get("access-od", "01ONEDRIVEITEM000001"))
    spool = asyncio.run(od.download("access-od", file, max_bytes=1000))
    try:
        assert spool.read_bytes() == PDF and spool.suffix == ".pdf"
    finally:
        spool.unlink()
    content, fetched = graph.seen[-2], graph.seen[-1]
    assert content.url.path.endswith("/me/drive/items/01ONEDRIVEITEM000001/content")
    assert content.headers["Authorization"] == "Bearer access-od"
    assert fetched.url.host == "download.test"
    assert "authorization" not in {k.lower() for k in fetched.headers}

    personal = asyncio.run(od.get("access-od", "D4648F06C91D9D3D!54927"))
    assert personal.import_name == "Personal plan.xlsx" and personal.extension == "xlsx"


def test_an_http_download_url_is_refused() -> None:
    class Plain(FakeGraph):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            return httpx.Response(302, headers={"Location": "http://download.test/x"})

    plain = Plain()
    file = D.DriveFile("01ONEDRIVEITEM000001", "a.pdf", "application/pdf", 5, None)
    with pytest.raises(httpx.HTTPError):
        asyncio.run(reader(plain).download("access-od", file, max_bytes=1000))
    assert [r.url.host for r in plain.seen] == ["graph.test"]


def test_sizes_types_and_ids_are_checked() -> None:
    od = reader(FakeGraph(body=b"x" * 50))
    unknown = D.DriveFile("01ONEDRIVEITEM000001", "a.pdf", "application/pdf", None, None)
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(od.download("access-od", unknown, max_bytes=10))  # streamed past the cap
    assert refused.value.code == "too_large"
    declared = D.DriveFile("01ONEDRIVEITEM000001", "a.pdf", "application/pdf", 99, None)
    graph = FakeGraph()
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(reader(graph).download("access-od", declared, max_bytes=10))
    assert refused.value.code == "too_large" and graph.seen == []  # refused before any request
    with pytest.raises(D.DriveUnavailable) as refused:
        asyncio.run(reader(FakeGraph(body=b"")).download("access-od", unknown, max_bytes=10))
    assert refused.value.code == "empty"
    for bad, code in (
        ("../../me/drive", "drive_file_not_found"),
        ("short", "drive_file_not_found"),
        ("01ONEDRIVEMISSING001", "drive_file_not_found"),
        ("01ONEDRIVEITEM000003", "wrong_type"),  # logo.png
        ("01ONEDRIVEFOLDER0001", "wrong_type"),
    ):
        with pytest.raises(D.DriveUnavailable) as refused:
            asyncio.run(reader(FakeGraph()).get("access-od", bad))
        assert refused.value.code == code, bad


@pytest.mark.parametrize("status", [429, 503])
def test_throttling_waits_for_retry_after_and_is_bounded(status: int) -> None:
    waits: list[float] = []
    graph = FakeGraph(throttle=2, status=status)
    files = asyncio.run(reader(graph, waits).list("access-od"))
    assert len(files) == 3 and waits == [7.0, 7.0]
    waits.clear()
    endless = FakeGraph(throttle=99, status=status)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(reader(endless, waits).list("access-od"))
    assert len(endless.seen) == D.RETRY_ATTEMPTS and len(waits) == D.RETRY_ATTEMPTS - 1
    assert D._retry_after("3600") == D.RETRY_WAIT_CAP_S and D._retry_after("soon") == 1.0


# ------------------------------------------------------------------ routes, on PostgreSQL


class FakeDocuments:
    """The document service's import: records what arrived instead of storing it."""

    def __init__(self) -> None:
        self.imported: list[tuple[str, bytes, str | None]] = []

    async def file_limit(self, _tenant: Any) -> int:
        return 1000

    async def import_file(
        self, _tenant: Any, _user: str, name: str, path: Path, mime: str | None, account: Any
    ) -> dict[str, Any]:
        self.imported.append((name, path.read_bytes(), mime))
        return {
            "document_id": str(uuid4()), "account_id": account, "name": name,
            "status": "quarantined", "stage": "scanning", "declared_type": "pdf",
            "detected_type": None, "size_bytes": len(path.read_bytes()), "reason": None,
            "message": None, "passages": 0, "redactions": 0, "redaction_tier": "standard",
            "authoritative": False, "company": None, "needs_company": True,
            "created_at": "2026-10-23T00:00:00+00:00", "parts_total": 0, "parts_done": 0,
            "parts_failed": [], "indexed_passages": 0, "members_read": 0, "members_skipped": [],
        }  # fmt: skip


def _db(sql: str, *params: object) -> list[Any]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _connect(tenant: Any, user: str, ref: str) -> None:
    _db(
        "INSERT INTO connector_connections (id, tenant_id, kind, provider, status, owner_user_id,"
        " scopes, token_ref, created_by, created_at) VALUES (%s, %s, 'onedrive', 'microsoft',"
        " 'active', %s, %s, %s, %s, now())",
        uuid4(), tenant, user, list(SCOPES), ref, user,
    )  # fmt: skip


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_routes_use_only_the_callers_own_onedrive(
    world: dict[str, Any],  # noqa: F811 - the re-exported fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from connectors_client import oauth as consent
    from connectors_client.ports import TokenSet

    tag = uuid4().hex[:6]
    owner, colleague = f"od-owner-{tag}", f"od-colleague-{tag}"
    _connect(world["a"], owner, f"ref-owner-{tag}")
    used: list[str] = []

    async def token(*_a: Any, kind: str, token_ref: str, **_k: Any) -> TokenSet:
        assert kind == "onedrive"
        used.append(token_ref)
        return TokenSet("access-od", None, SCOPES, 3600)

    monkeypatch.setattr(consent, "access_token", token)
    graph = FakeGraph()
    docs = FakeDocuments()
    planes = {who: _plane(world["a"], who, world["payloads"]) for who in (owner, colleague)}
    for plane in planes.values():
        container = plane.app.state.container
        container.extras["maildrive_apps"] = {"microsoft": ("client-id", "client-secret")}
        container.extras["onedrive_files"] = reader(graph)
        container.documents = docs

    # the colleague has no OneDrive of their own: 409, and the owner's token is never read
    refused = await _call(planes[colleague], "GET", "/v1/documents/drive-files?source=onedrive")
    assert refused.status_code == 409, refused.text
    assert refused.json()["type"].endswith("drive_not_connected")
    picked = await _call(
        planes[colleague], "POST", "/v1/documents/from-drive",
        json={"file_id": "01ONEDRIVEITEM000001", "source": "onedrive"},
    )  # fmt: skip
    assert picked.status_code == 409 and used == [] and graph.seen == []
    # Google stays the default source, and the owner has no Google Drive
    google = await _call(planes[owner], "GET", "/v1/documents/drive-files")
    assert google.status_code == 409 and "Google Drive" in google.text

    listed = await _call(planes[owner], "GET", "/v1/documents/drive-files?source=onedrive&q=")
    assert listed.status_code == 200, listed.text
    names = [f["name"] for f in listed.json()["files"]]
    assert sorted(names) == ["Board deck.pptx", "Personal plan.xlsx", "Proposal.pdf"]
    assert all(f["google_native"] is False for f in listed.json()["files"])
    assert "sharepoint" not in listed.text  # no web link reaches the browser

    added = await _call(
        planes[owner], "POST", "/v1/documents/from-drive",
        json={"file_id": "01ONEDRIVEITEM000001", "source": "onedrive"},
    )  # fmt: skip
    assert added.status_code == 201, added.text
    assert docs.imported == [("Proposal.pdf", PDF, "application/pdf")]
    assert used == [f"ref-owner-{tag}", f"ref-owner-{tag}"]
    assert "authorization" not in {k.lower() for k in graph.seen[-1].headers}
    audit = _db(
        "SELECT count(*) FROM audit_events WHERE tenant_id = %s AND actor = %s"
        " AND action = 'run.document_drive_import_onedrive'",
        world["a"], owner,
    )  # fmt: skip
    assert audit == [(1,)]
    bad = await _call(
        planes[owner], "POST", "/v1/documents/from-drive",
        json={"file_id": "01ONEDRIVEITEM000001", "source": "dropbox"},
    )  # fmt: skip
    assert bad.status_code == 422
