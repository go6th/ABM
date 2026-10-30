"""The account's output parity bundle: ``/v1/accounts/{domain}/parity/*``.

One built version serves the screens, the stored data and the downloads alike: ``bundle`` is
the document, ``manifest`` the stored files with their digests and the recalculated Summary
values, ``screen`` everything the account workspace draws in one read, ``tables/{table}`` a page
of one table's rows in the file's own order and text (``parity.tables``), ``files/{name}`` the
stored bytes of one file as an attachment, ``versions`` the history, and ``edits`` an analyst
correction that writes the next version and renders again (``parity.service``).

The internal-only material (``prose.internal_only``, ``meta.pursuing_client_internal`` and the
``*_Internal_Only.docx`` file) is served to an admin of the tenant only, never to any other
role and never through a shared or signed link: every other reader gets the client view.
A version that failed the gate is never served (404 for its files; ``versions`` names it).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.account_workspace import account_id_of
from account_intelligence.api.routes.accounts import exclusion_rule
from account_intelligence.db.session import tenant_txn
from agents.errors import GuardViolation
from agents.naming import sanitize_client_value
from parity import tables as T
from parity.gate.files import refused_name
from parity.service import (
    BundleVersion,
    ParityService,
    ParityStale,
    ParityUnavailable,
    ParityVersions,
)
from tenancy.storage import PathRefused, require_owned

router = APIRouter(prefix="/accounts", tags=["accounts"])
_BASE = "/v1/accounts/{domain}/parity"
register("GET", f"{_BASE}/versions", tenant_route("read_public", "read", step_up=False))
register("GET", f"{_BASE}/bundle", tenant_route("read_public", "read", step_up=False))
register("GET", f"{_BASE}/manifest", tenant_route("read_public", "read", step_up=False))
register("GET", f"{_BASE}/screen", tenant_route("read_public", "read", step_up=False))
register("GET", f"{_BASE}/tables/{{table}}", tenant_route("read_public", "read", step_up=False))
register("GET", f"{_BASE}/files/{{name}}", tenant_route("export_files", "export", step_up=False))
register("POST", f"{_BASE}/edits", tenant_route("edit_shared_views", "run", step_up=False))

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._&'-]{0,180}\.(docx|xlsx|zip|json)$")
_TABLE_ID = re.compile(r"^[a-z0-9_]{1,40}(:[A-Za-z0-9 ().&'-]{1,60})?$")
_COUNT_JOB_FIELDS = ("platforms", "entity", "status", "cls", "title")
_COUNT_PERSON_FIELDS = ("level", "senior", "lane", "priority")


def may_see_internal(ident: Identity) -> bool:
    """Only an admin of the tenant on their own session or token sees the internal-only
    material; nothing else (another role, a signed or shared link) ever does."""
    return ident.role == "admin" and ident.kind in ("session", "jwt")


def client_view(bundle: dict[str, Any], internal: bool) -> dict[str, Any]:
    """The bundle as a reader may see it: the internal-only prose and the pursuing client's
    real name only when ``internal`` is True."""
    if internal:
        return bundle
    out = dict(bundle)
    out["meta"] = {k: v for k, v in bundle["meta"].items() if k != "pursuing_client_internal"}
    out["prose"] = {k: v for k, v in bundle["prose"].items() if k != "internal_only"}
    return out


def _scrubbed(value: Any) -> Any:
    try:
        return sanitize_client_value(value)
    except GuardViolation as error:
        raise APIProblem(
            422,
            "client_output_blocked",
            "Response held back",
            "This response failed a safety check and could not be returned.",
        ) from error


def _versions(request: Request) -> ParityVersions:
    return ParityVersions(request.app.state.container.payloads)


def _service(request: Request) -> ParityService:
    container = request.app.state.container
    builder = getattr(container, "builder", None)
    return ParityService(
        tenant_txn, container.payloads, container.clock, reader=getattr(builder, "reader", None)
    )


async def is_current(s: Any, found: BundleVersion) -> bool:
    """Whether the version is of the research the account's delivered files come from (the
    Files card and the file tabs show the newest stored file of each kind): the account page
    draws two views of the same tables, and they agree only when they are of one research.
    True when no file is delivered yet."""
    from account_intelligence.replica.workspace import newest_files

    newest = await newest_files(s, found.account_id)
    return all(str(r.run_id) == str(found.run_id) for r in newest)


async def _found(
    request: Request, ident: Identity, domain: str, version: int | None
) -> BundleVersion:
    """The built version asked for, or 404 (an unknown or deleted account, an excluded company
    and a version held back (``held_back``) look the same)."""
    async with tenant_txn(ident.tenant_id) as s:
        if (await exclusion_rule(s)).excludes_company(domain):
            raise HTTPException(404, "not found")
        account_id = await account_id_of(s, domain)
        found = await _versions(request).get(s, account_id, version)
        if found is not None and await _versions(request).held_back(s, found) is not None:
            found = None
    if found is None:
        raise HTTPException(404, "not found")
    try:
        require_owned(ident.tenant_id, found.bundle_key)
    except PathRefused:
        raise HTTPException(404, "not found") from None
    return found


def _head(v: BundleVersion) -> dict[str, Any]:
    """The four fields every version reply starts with."""
    return {
        "version": v.version,
        "status": v.status,
        "run_id": str(v.run_id),
        "created_at": v.created_at.isoformat(),
    }


def _info(v: BundleVersion, held: str | None = None) -> dict[str, Any]:
    failures = [
        f"{c.get('name')}: {f.get('message')}"
        for c in v.gate.get("checks", ())
        for f in c.get("findings", ())
        if f.get("severity") == "fail"
    ]
    if v.gate.get("error"):
        failures.append(str(v.gate.get("error")))
    return {
        **_head(v),
        "gate_passed": bool(v.gate.get("passed", False)),
        "gate_failures": failures[:20],
        "held_back": held,
    }


def _files(v: BundleVersion, internal: bool) -> list[dict[str, Any]]:
    """The manifest's files a reader may download (never the internal-only file unless the
    reader is an admin; never the manifest or the bundle document)."""
    out = []
    for f in v.manifest.get("files", ()):
        name = str(f.get("name", ""))
        if not name or name in ("manifest.json", "bundle.json"):
            continue
        if refused_name(name) and not (internal and "Internal_Only" in name):
            continue
        kind = next((k for k, suffix in T.FILE_SUFFIX.items() if name.endswith(suffix)), None)
        out.append(
            {
                "name": name,
                "file": "workspace" if name.endswith("_workspace.zip") else kind,
                "size": int(f.get("size", 0)),
                "sha256": str(f.get("sha256", "")),
                "mime": str(f.get("mime", "application/octet-stream")),
            }
        )
    return out


@router.get("/{domain}/parity/versions", response_model=C.ParityVersions)
async def parity_versions(
    domain: str, request: Request, ident: Identity = Depends(authorize)
) -> Any:
    """Every version of the account's bundle, newest first, built or failed."""
    async with tenant_txn(ident.tenant_id) as s:
        if (await exclusion_rule(s)).excludes_company(domain):
            raise HTTPException(404, "not found")
        account_id = await account_id_of(s, domain)
        versions = await _versions(request).list(s, account_id)
        held = {v.version: await _versions(request).held_back(s, v) for v in versions}
    return {"versions": [_info(v, held[v.version]) for v in versions]}


@router.get("/{domain}/parity/bundle", response_model=C.ParityBundle)
async def parity_bundle(
    domain: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    ident: Identity = Depends(authorize),
) -> Any:
    """The bundle document of a built version (the newest when no version is given)."""
    found = await _found(request, ident, domain, version)
    bundle = await _versions(request).bundle(found)
    return {
        **_head(found),
        "bundle": _scrubbed(client_view(bundle, may_see_internal(ident))),
    }


@router.get("/{domain}/parity/manifest", response_model=C.ParityManifest)
async def parity_manifest(
    domain: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    ident: Identity = Depends(authorize),
) -> Any:
    """The manifest of a built version: its files and the recalculated Summary values."""
    found = await _found(request, ident, domain, version)
    return {
        **_head(found),
        "bundle_version": str(found.manifest.get("bundle_version", "")),
        "files": _files(found, may_see_internal(ident)),
        "summary": dict(found.manifest.get("summary", {})),
    }


@router.get("/{domain}/parity/screen", response_model=C.ParityScreen)
async def parity_screen(
    domain: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    ident: Identity = Depends(authorize),
) -> Any:
    """What the account workspace draws from a built version in one read."""
    found = await _found(request, ident, domain, version)
    internal = may_see_internal(ident)
    bundle = client_view(await _versions(request).bundle(found), internal)
    files = _files(found, internal)
    async with tenant_txn(ident.tenant_id) as s:
        current = await is_current(s, found)
    refs = []
    for file in T.FILES:
        if file == "internal" and not internal:
            continue
        if file == "internal" and not bundle["prose"].get("internal_only"):
            continue
        if file == "highlights" and not bundle["prose"].get("lane_highlights"):
            continue
        for spec in T.tables_of(bundle, file):
            refs.append({**T.spec_json(spec, bundle), "total": len(spec.rows(bundle))})
    zip_name = next((f["name"] for f in files if f["file"] == "workspace"), None)
    base = f"/v1/accounts/{domain}/parity/files/"
    body = {
        **_head(found),
        "current": current,
        "meta": bundle["meta"],
        "computed": bundle["computed"],
        "ranking": bundle["ranking"],
        "summary": dict(found.manifest.get("summary", {})),
        "tables": refs,
        "prose": bundle["prose"],
        "count_rows": {
            "jobs_rows": [
                {k: r.get(k) for k in _COUNT_JOB_FIELDS} for r in bundle.get("jobs_rows", ())
            ],
            "people_rows": [
                {k: r.get(k) for k in _COUNT_PERSON_FIELDS} for r in bundle.get("people_rows", ())
            ],
        },
        "files": files,
        "download_all": f"{base}{zip_name}?version={found.version}" if zip_name else None,
    }
    return _scrubbed(body)


@router.get("/{domain}/parity/tables/{table}", response_model=C.ParityTablePage)
async def parity_table(
    domain: str,
    table: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=T.PAGE_LIMIT_DEFAULT, ge=1, le=T.PAGE_LIMIT_MAX),
    ident: Identity = Depends(authorize),
) -> Any:
    """A page of one table's rows, in the file's order and with the file's cell text."""
    if not _TABLE_ID.match(table):
        raise HTTPException(404, "not found")
    found = await _found(request, ident, domain, version)
    bundle = client_view(await _versions(request).bundle(found), may_see_internal(ident))
    spec = T.spec_of(table, bundle)
    if spec is None:
        raise HTTPException(404, "not found")
    rows = spec.rows(bundle)
    page = rows[offset : offset + limit]
    body = {
        **T.spec_json(spec, bundle),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rows": page,
    }
    body.pop("sheet", None)
    body.pop("paginated", None)
    return _scrubbed(body)


@router.get("/{domain}/parity/files/{name}")
async def parity_file(
    domain: str,
    name: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    ident: Identity = Depends(authorize),
) -> Response:
    """One stored file of a built version as an attachment, with the right MIME type; the
    internal-only file only for an admin (anyone else sees 404)."""
    if not _FILE_NAME.match(name) or name in ("manifest.json", "bundle.json"):
        raise HTTPException(404, "not found")
    found = await _found(request, ident, domain, version)
    internal = may_see_internal(ident)
    entry = next((f for f in _files(found, internal) if f["name"] == name), None)
    if entry is None:
        raise HTTPException(404, "not found")
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download_parity_file",
            resource_type="account",
            resource_id=account_id,
        )
    try:
        body, _ = await _versions(request).file(found, name)
    except KeyError:
        raise HTTPException(404, "not found") from None
    safe_name = name.replace('"', "")
    return Response(
        body,
        media_type=entry["mime"],
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(body)),
            "X-Content-SHA256": entry["sha256"],
            "Cache-Control": "no-store",
        },
    )


@router.post("/{domain}/parity/edits", response_model=C.ParityVersionInfo, status_code=201)
async def parity_edit(
    domain: str, edit: C.ParityEdit, request: Request, ident: Identity = Depends(authorize)
) -> Any:
    """Apply analyst corrections (priority, lane, note by person) to a built version: the
    corrections are recorded as overrides, every number is recomputed and the files are
    rendered again as the account's next version (built or failed)."""
    found = await _found(request, ident, domain, edit.version)
    service = _service(request)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        await audit_action(
            s,
            request,
            audit_class="run",
            action="parity_edit",
            resource_type="account",
            resource_id=account_id,
        )
    try:
        created = await service.edit(
            ident.tenant_id, found, [o.model_dump(exclude_none=True) for o in edit.overrides]
        )
    except KeyError as unknown:
        raise APIProblem(
            422,
            "unknown_person",
            "No such person on the list",
            "Name a person as the People sheet prints them.",
            extra={"name": str(unknown)[:200]},
        ) from None
    except ParityStale:
        raise APIProblem(
            409,
            "package_stale",
            "The workspace's exclusions changed",
            "The exclusion list changed after this version was built; refresh the research.",
        ) from None
    except ParityUnavailable as missing:
        raise APIProblem(
            503,
            "parity_unavailable",
            "The file renderer is not available",
            f"This server cannot render the files ({missing}).",
        ) from None
    return _info(created)
