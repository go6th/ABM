"""The workspace zip (D-25): deterministic and safe (B10, B13).

Contents, in this fixed order: ``RESTORE.md``, ``MANIFEST.json``, ``NOTES.md``, the five client
deliverables under ``deliverables/``, and the render-model data each was built from under
``data/`` (in place of "build scripts": the builder code is not shipped). Raw broker payloads are
EXCLUDED by default (D-25, pending D-10); the neutral-name copy path is built and tested but off;
payloads from the organisation provider are never included in any case (adapter rule).

Safety: names are relative, sanitised, unique and length-capped; no absolute path, no ``..``, no
backslash, no symlink entries; member, member-size and total-size caps; ``MANIFEST.json`` lists a
SHA-256 for every other member. Only the five client file kinds can be passed in: the Internal
Only document has no kind here and a name check refuses it anyway (B8).
GUARANTEE(test_unsafe_names_are_refused)
GUARANTEE(test_internal_only_document_cannot_enter)
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date

from exports.config import ContentRules, ZipTheme
from exports.content import FILE_KINDS
from exports.errors import UnsafePackage
from exports.models import FileKind

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
# The Internal Only document's name pattern (output_spec ``operator_only`` and the skill's name):
# matched as a name part, so an account merely called "Internal Systems" is not refused.
_FORBIDDEN_NAMES = re.compile(r"(^|[/_.-])(internal|operator)[_ -]?only([/_.-]|$)", re.IGNORECASE)
NEVER_SHIPPED_PROVIDERS = frozenset({"p_org"})


def safe_name(name: str, max_len: int) -> str:
    """Return ``name`` when it is a safe relative member name, else raise ``UnsafePackage``."""
    if (
        not name
        or len(name) > max_len
        or name.startswith("/")
        or "\\" in name
        or ".." in name.split("/")
        or ":" in name
        or not _SAFE.match(name)
    ):
        raise UnsafePackage("unsafe member name", (name[:max_len],))
    if _FORBIDDEN_NAMES.search(name):
        raise UnsafePackage("an internal-only member cannot enter the workspace", (name,))
    return name


def account_slug(account: str) -> str:
    """File-name-safe account name: letters, digits, dot, dash; others become underscores."""
    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", account).strip("._-")
    return slug[:60] or "Account"


def neutral_payload_members(
    payloads: Sequence[tuple[str, bytes]], *, enabled: bool
) -> list[tuple[str, bytes]]:
    """Raw payloads under neutral names (``data/raw/<sha256>.json``) when enabled (D-25: off until
    D-10 is answered). Payloads from a never-shipped provider are dropped even when enabled. The
    provider code never appears in a name."""
    if not enabled:
        return []
    out = []
    for provider_code, body in payloads:
        if provider_code in NEVER_SHIPPED_PROVIDERS:
            continue
        out.append((f"data/raw/{hashlib.sha256(body).hexdigest()}.json", body))
    return sorted(out)


def build_workspace(
    *,
    deliverables: Mapping[FileKind, tuple[str, bytes]],
    data: Mapping[FileKind, bytes],
    manifest: Mapping[str, object],
    rules: ContentRules,
    theme: ZipTheme,
    account: str,
    as_of: date,
    raw: Sequence[tuple[str, bytes]] = (),
) -> bytes:
    """The workspace zip bytes (see the module docstring)."""
    ws = rules.workspace
    if set(deliverables) != set(FILE_KINDS) or set(data) != set(FILE_KINDS):
        raise UnsafePackage("the workspace needs exactly the five client files and their data")
    members: list[tuple[str, bytes]] = [("NOTES.md", ws.notes_text.encode())]
    for kind in FILE_KINDS:
        filename, body = deliverables[kind]
        members.append((f"deliverables/{filename}", body))
    for kind in FILE_KINDS:
        members.append((f"data/{kind}.json", data[kind]))
    members += neutral_payload_members(raw, enabled=ws.raw_payloads.neutral_name_copy_enabled)
    names = [safe_name(n, ws.entry_name_max) for n, _ in members]
    if len(set(names)) != len(names):
        raise UnsafePackage("duplicate member names")
    listing = [
        {"name": n, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in members
    ]
    document: dict[str, object] = {
        "schema_version": 1,
        "account": account,
        "as_of": as_of.isoformat(),
        "members": listing,
        "build": dict(manifest),
    }
    manifest_bytes = (json.dumps(document, indent=1, sort_keys=True, default=str) + "\n").encode()
    ordered = [
        ("RESTORE.md", ws.restore_text.encode()),
        ("MANIFEST.json", manifest_bytes),
        *members,
    ]
    if len(ordered) > ws.max_members:
        raise UnsafePackage("too many members", (str(len(ordered)),))
    total = 0
    for name, b in ordered:
        if len(b) > ws.max_member_bytes:
            raise UnsafePackage("member over the size cap", (name,))
        total += len(b)
    if total > ws.max_total_bytes:
        raise UnsafePackage("package over the total size cap", (str(total),))
    method = zipfile.ZIP_DEFLATED if theme.compression == "deflate" else zipfile.ZIP_STORED
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, b in ordered:
            info = zipfile.ZipInfo(name, date_time=theme.entry_time)
            info.compress_type = method
            info.create_system = 0
            info.external_attr = 0o600 << 16
            z.writestr(info, b, compress_type=method, compresslevel=6)
    return out.getvalue()


def check_zip(data: bytes, rules: ContentRules) -> tuple[str, ...]:
    """Re-open a zip and return every safety problem (empty when safe): unsafe or duplicate
    names, symlink entries, caps, and members whose hash disagrees with MANIFEST.json."""
    ws = rules.workspace
    problems: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            problems.append("duplicate names")
        if len(infos) > ws.max_members:
            problems.append("too many members")
        total = 0
        for info in infos:
            try:
                safe_name(info.filename, ws.entry_name_max)
            except UnsafePackage:
                problems.append(f"unsafe name {info.filename[:80]!r}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                problems.append(f"symlink {info.filename[:80]!r}")
            if info.file_size > ws.max_member_bytes:
                problems.append(f"member over cap {info.filename[:80]!r}")
            total += info.file_size
        if total > ws.max_total_bytes:
            problems.append("total over cap")
        if "MANIFEST.json" in names:
            listing = json.loads(z.read("MANIFEST.json"))["members"]
            listed = {m["name"]: m["sha256"] for m in listing}
            for name in names:
                if name in ("MANIFEST.json", "RESTORE.md"):
                    continue
                if listed.get(name) != hashlib.sha256(z.read(name)).hexdigest():
                    problems.append(f"hash mismatch {name[:80]!r}")
        else:
            problems.append("no MANIFEST.json")
    return tuple(problems)
