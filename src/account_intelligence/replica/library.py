"""The 6th Library: finished account workspaces the platform publishes to chosen clients, which a
client adds as ready-made research (no credits, available at once).

* ``inspect_package``: an upload (a workspace zip, or standing Word and Excel files) is read
  without trusting it: the five client deliverables are found by their standing names
  (``config/output_spec.yaml``); an internal-only file is left out and never stored; the
  client-ready check runs the pre-delivery gate's file checks on each deliverable (no dash used
  as punctuation, no data tool name, credit count or internal reference, no data provider host,
  no plain-http link) and reports a missing deliverable. The account name comes from the
  package's ``MANIFEST.json`` or the upload form, the domain from the form.
* ``store_upload``: the files go to the platform's own library prefix (``library/<upload>/``,
  never a tenant prefix) and the upload and its account are recorded (platform rows, written only
  without a tenant context, ``library_write`` policy).
* ``publish``: inside each chosen client's own transaction, a publication row; only an account
  whose check passed can be published.
* ``available`` and ``add_to_workspace`` run inside the client's transaction: the client sees a
  library account only while it is published to it (row level security), and adding one creates
  the account for the person adding it (it counts against their account cap), records a
  completed library import with the five files copied under the client's own prefix and the
  narrative as the workspace's one minute version, records a library add that used no credits,
  and tells the person. Adding an account already in the workspace changes nothing.

GUARANTEE(test_a_library_account_is_checked_published_and_added_without_credits)
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

KINDS: dict[str, str] = {
    "summary": "account_summary",
    "evidence": "sources_and_evidence",
    "people": "people_list",
    "jobs": "job_postings",
    "tech": "tech_stack_and_teams",
}
FORMATS = {"summary": "docx", "evidence": "docx", "people": "xlsx", "jobs": "xlsx", "tech": "xlsx"}
_INTERNAL = re.compile(r"(^|[/_. -])(internal|operator)[_ -]?only([/_. -]|$)", re.IGNORECASE)
_DASHES = re.compile("[‒–—―]|\\S\\s+-{1,2}\\s+\\S")
_RELATIONSHIP = re.compile(r"<Relationship\b[^>]*>", re.IGNORECASE)
MAX_MEMBERS = 200
MAX_MEMBER_BYTES = 50 * 1024 * 1024


class LibraryError(ValueError):
    """The upload cannot be read as a library package (a plain reason, no file content)."""


@dataclass
class Inspected:
    """What an upload holds and whether it is ready for clients."""

    name: str
    domain: str
    files: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    internal_left_out: bool = False
    issues: list[str] = field(default_factory=list)
    narrative: list[str] = field(default_factory=list)

    @property
    def check_passed(self) -> bool:
        """The client-ready check passed: all five files, and none with an issue."""
        return not self.issues


def safe_filename(name: str, ext: str) -> str:
    """A deliverable file name the store accepts: letters, digits, dot, dash and underscore."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name.rsplit(".", 1)[0]).strip("._-")[:140]
    return f"{stem or 'File'}.{ext}"


def _kind_of(name: str) -> str | None:
    base = name.rsplit("/", 1)[-1].lower().replace(" ", "_")
    for kind, part in KINDS.items():
        if part in base and base.endswith("." + FORMATS[kind]):
            return kind
    return None


def _members(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """The package's files: every member of a workspace zip, or the one standing file."""
    if not filename.lower().endswith(".zip"):
        return [(filename, data)]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_MEMBERS:
                raise LibraryError("the package has too many files")
            out = []
            for info in infos:
                if info.file_size > MAX_MEMBER_BYTES or ".." in info.filename.split("/"):
                    raise LibraryError("the package has an unsafe or oversized file")
                out.append((info.filename, z.read(info)))
            return out
    except zipfile.BadZipFile as error:
        raise LibraryError("the package is not a readable zip file") from error


def _manifest_name(members: list[tuple[str, bytes]]) -> str | None:
    for name, body in members:
        if name.rsplit("/", 1)[-1] == "MANIFEST.json":
            try:
                value = json.loads(body).get("account")
            except (ValueError, AttributeError):
                return None
            return str(value) if value else None
    return None


def _plain_link(rels: bytes) -> bool:
    """A hyperlink in the file that is not https (an external relationship target)."""
    for tag in _RELATIONSHIP.findall(rels.decode("utf-8", "ignore")):
        if 'TargetMode="External"' in tag and 'Target="http://' in tag:
            return True
    return False


def file_issues(kind: str, data: bytes) -> list[str]:
    """The client-ready check of one deliverable (codes only, never file text)."""
    from documents.gate import client_text, provider_hosts
    from exports.verify import scan_package

    out: list[str] = []
    try:
        body = client_text(data)
        hits = scan_package(data, ())
    except zipfile.BadZipFile:
        return [f"{kind}:unreadable"]
    if _DASHES.search(body):
        out.append(f"{kind}:banned_dashes")
    if hits:
        out.append(f"{kind}:tool_names_or_credits")
    lowered = body.lower()
    if any(host in lowered for host in provider_hosts()):
        out.append(f"{kind}:provider_host")
    if any(
        _plain_link(body) for name, body in _members(f"{kind}.zip", data) if name.endswith(".rels")
    ):
        out.append(f"{kind}:insecure_link")
    return out


def _narrative(summary: bytes) -> list[str]:
    """The first plain paragraphs of the Summary (the one minute version), at most four."""
    from docx import Document

    try:
        doc = Document(io.BytesIO(summary))
    except Exception:
        return []
    paragraphs = [p.text.strip() for p in doc.paragraphs if len(p.text.strip()) > 40]
    return paragraphs[:4]


def inspect_package(filename: str, data: bytes, name: str | None, domain: str) -> Inspected:
    """See the module docstring."""
    members = _members(filename, data)
    account = (name or _manifest_name(members) or "").strip()[:200]
    if not account:
        raise LibraryError("name the account (the package has no MANIFEST.json name)")
    out = Inspected(account, domain)
    for member, body in members:
        if _INTERNAL.search(member):
            out.internal_left_out = True
            continue
        kind = _kind_of(member)
        if kind is not None and kind not in out.files:
            out.files[kind] = (member.rsplit("/", 1)[-1], body)
    for kind in KINDS:
        if kind not in out.files:
            out.issues.append(f"{kind}:missing")
        else:
            out.issues.extend(file_issues(kind, out.files[kind][1]))
    if "summary" in out.files:
        out.narrative = _narrative(out.files["summary"][1])
    return out


async def store_upload(
    session: Any, payloads: Any, filename: str, data: bytes, found: Inspected, staff_id: str
) -> UUID:
    """Store the files under the platform's library prefix and record the upload and its
    account (no tenant context: platform rows). Returns the library account id."""
    upload_id = uuid4()
    sha = hashlib.sha256(data).hexdigest()
    files = []
    for kind, (name, body) in sorted(found.files.items()):
        digest = hashlib.sha256(body).hexdigest()
        key = f"library/{upload_id}/{kind}-{digest}.{FORMATS[kind]}"
        await payloads.put(key, body)
        files.append(
            {"kind": kind, "filename": name, "key": key, "sha256": digest, "size": len(body)}
        )
    await session.execute(
        text(
            "INSERT INTO library_uploads (id, filename, size_bytes, sha256, object_key,"
            " uploaded_by) VALUES (:id, :f, :b, :s, :k, :by)"
        ),
        {
            "id": upload_id,
            "f": filename[-255:],
            "b": len(data),
            "s": sha,
            "k": f"library/{upload_id}",
            "by": staff_id,
        },
    )
    account_id = await session.scalar(
        text(
            "INSERT INTO library_accounts (upload_id, name, domain, deliverables_found,"
            " internal_left_out, check_passed, issues, content, files) VALUES (:u, :n, :d, :f,"
            " :i, :ok, CAST(:is AS jsonb), CAST(:c AS jsonb), CAST(:fs AS jsonb)) RETURNING id"
        ),
        {
            "u": upload_id,
            "n": found.name,
            "d": found.domain,
            "f": len(found.files),
            "i": found.internal_left_out,
            "ok": found.check_passed,
            "is": json.dumps(found.issues),
            "c": json.dumps({"narrative": found.narrative}),
            "fs": json.dumps(files),
        },
    )
    return UUID(str(account_id))


async def library_list(session: Any) -> list[dict[str, Any]]:
    """Every current library account with its check and how many clients it is published to
    (platform view, no tenant context)."""
    rows = (
        await session.execute(
            text(
                "SELECT a.id, a.name, a.domain, a.deliverables_found, a.internal_left_out,"
                " a.check_passed, a.issues, a.created_at, u.filename,"
                " coalesce(c.clients, 0) AS clients"
                " FROM library_accounts a JOIN library_uploads u ON u.id = a.upload_id"
                " LEFT JOIN library_publication_counts() c ON c.library_account_id = a.id"
                " WHERE a.superseded_at IS NULL ORDER BY a.created_at DESC"
            )
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "domain": r.domain,
            "deliverables_found": int(r.deliverables_found),
            "internal_left_out": bool(r.internal_left_out),
            "check_passed": bool(r.check_passed),
            "issues": list(r.issues or []),
            "uploaded_at": r.created_at.isoformat(),
            "filename": r.filename,
            "clients": int(r.clients or 0),
        }
        for r in rows
    ]


async def check_passed(session: Any, library_account_id: UUID) -> bool:
    """Whether the library account exists and passed its client-ready check (platform view,
    read without a tenant context)."""
    return bool(
        await session.scalar(
            text(
                "SELECT check_passed FROM library_accounts WHERE id = :a AND superseded_at IS NULL"
            ),
            {"a": library_account_id},
        )
    )


async def publish(session: Any, library_account_id: UUID, staff_id: str) -> bool:
    """Publish one library account to the caller's tenant (inside that tenant's transaction;
    the caller has checked ``check_passed`` first). False when it was already published."""
    done = await session.scalar(
        text(
            "INSERT INTO library_publications (library_account_id, published_by)"
            " VALUES (:a, :by) ON CONFLICT (tenant_id, library_account_id) DO UPDATE"
            " SET revoked_at = NULL WHERE library_publications.revoked_at IS NOT NULL"
            " RETURNING 1"
        ),
        {"a": library_account_id, "by": staff_id},
    )
    return done is not None


async def published_here(session: Any) -> set[str]:
    """The library accounts published to the caller's tenant (Client detail checkboxes)."""
    rows = await session.execute(
        text("SELECT library_account_id FROM library_publications WHERE revoked_at IS NULL")
    )
    return {str(r[0]) for r in rows}


async def unpublish(session: Any, library_account_id: UUID) -> None:
    """Stop offering one library account to the caller's tenant (history kept)."""
    await session.execute(
        text(
            "UPDATE library_publications SET revoked_at = now()"
            " WHERE library_account_id = :a AND revoked_at IS NULL"
        ),
        {"a": library_account_id},
    )


async def available(session: Any) -> list[dict[str, Any]]:
    """The library accounts published to the caller's client, and whether each is already in
    the workspace."""
    rows = (
        await session.execute(
            text(
                "SELECT l.id, l.name, l.domain, l.deliverables_found,"
                " EXISTS (SELECT 1 FROM accounts a WHERE a.library_account_id = l.id"
                "   OR a.domain = l.domain) AS in_workspace"
                " FROM library_accounts l WHERE l.superseded_at IS NULL ORDER BY l.name"
            )
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "domain": r.domain,
            "files": int(r.deliverables_found),
            "in_workspace": bool(r.in_workspace),
        }
        for r in rows
    ]


@dataclass(frozen=True)
class Added:
    """One library account added to the workspace."""

    account_id: UUID
    domain: str
    created: bool


async def add_to_workspace(
    session: Any,
    payloads: Any,
    tenant_id: UUID,
    library_account_id: UUID,
    user_id: str,
    now: datetime,
) -> Added:
    """Copy one published library account into the caller's workspace (see the module
    docstring). ``LookupError`` when it is not published to this client."""
    from account_intelligence.replica.credits import record_library_add
    from account_intelligence.replica.notifications import notify
    from account_intelligence.runs import lifecycle

    lib = (
        await session.execute(
            text(
                "SELECT id, name, domain, content, files FROM library_accounts"
                " WHERE id = :a AND superseded_at IS NULL"
            ),
            {"a": library_account_id},
        )
    ).first()
    if lib is None:
        raise LookupError("not published to this client")
    account_id, created = await lifecycle.add_account(
        session, tenant_id, lib.domain, added_by=user_id
    )
    if not created:
        return Added(account_id, lib.domain, False)
    await lifecycle.name_account(session, account_id, lib.name)
    await lifecycle.mark_library_account(session, account_id, library_account_id)
    run_id = await lifecycle.record_library_run(
        session, tenant_id, account_id, library_account_id=library_account_id, now=now
    )
    narrative = " ".join((lib.content or {}).get("narrative") or ())
    files = []
    for f in lib.files or []:
        body = await payloads.get(str(f["key"]))
        ext = str(f["key"]).rsplit(".", 1)[-1]
        key = f"{tenant_id}/{account_id}/{run_id}/deliverables/{f['sha256']}.{ext}"
        await payloads.put(key, body)
        files.append({**f, "key": key, "filename": safe_filename(str(f["filename"]), ext)})
    await record_library_copy(session, tenant_id, account_id, run_id, narrative, files, now)
    job = await record_library_add(session, account_id, user_id, now)
    from account_intelligence.replica import credits as CR

    await CR.link_run(session, job, run_id)
    await notify(session, user_id=user_id, kind="library_added", account_id=account_id, job_id=job)
    return Added(account_id, lib.domain, True)


async def record_library_copy(
    session: Any,
    tenant_id: UUID,
    account_id: UUID,
    run_id: UUID,
    narrative: str,
    files: list[dict[str, Any]],
    now: datetime,
) -> None:
    """The copied workspace's one minute version and its five files (the only writer of the
    library import's ``report_sections`` and ``deliverables`` rows)."""
    if narrative:
        await session.execute(
            text(
                "INSERT INTO report_sections (tenant_id, account_id, run_id, version, position,"
                " fact_ids, cited_source_codes, visibility, kind, text, created_at) VALUES (:t,"
                " :a, :r, 1, 0, '{}', '{}', 'public', 'narrative', :x, :now)"
            ),
            {"t": tenant_id, "a": account_id, "r": run_id, "x": narrative[:8000], "now": now},
        )
    set_hash = hashlib.sha256("".join(sorted(str(f["sha256"]) for f in files)).encode()).hexdigest()
    for f in files:
        await session.execute(
            text(
                "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
                " filename, sha256, size_bytes, object_key, manifest_json, content_hash,"
                " snapshot_hash, theme_hash, rules_hash, built_at) VALUES (:t, :a, :r, :sh, :k, 1,"
                " :fn, :sha, :b, :key, CAST(:m AS jsonb), :sha, :sh, :sh, :sh, :now)"
            ),
            {
                "t": tenant_id,
                "a": account_id,
                "r": run_id,
                "sh": set_hash,
                "k": f["kind"],
                "fn": f["filename"],
                "sha": f["sha256"],
                "b": int(f["size"]),
                "key": f["key"],
                "m": json.dumps({"source": "library"}),
                "now": now,
            },
        )
