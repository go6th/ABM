"""The workspace zip (D-25): deterministic and safe (B10, B13).

Layout, after the owner's reference workspace (``RESTORE.md`` at the root, then folders):
``RESTORE.md`` (built from the run: layout, how to rebuild, canonical query definitions, entity
rules), ``MANIFEST.json``, the five client deliverables under ``deliverables/``, the pulls as the
run stored them under ``data/``, the render-model data each file was built from and the source
link map under ``build/`` (in place of build scripts: the builder code is not shipped), and the
pull log, pass 1 and sense check under ``notes/``. Members sort by that folder order, then in the
configured order. Raw broker payloads are EXCLUDED by default (D-25, pending D-10); the
neutral-name copy path is built and tested but off; payloads from the organisation provider are
never included in any case (adapter rule).

Safety: names are relative, sanitised, unique and length-capped; no absolute path, no ``..``, no
backslash, no symlink entries; member, member-size and total-size caps; ``MANIFEST.json`` lists a
SHA-256 for every other member, RESTORE.md included. Only the five client file kinds can be
passed in: the Internal Only document has no kind here and a name check refuses it anyway (B8).
GUARANTEE(test_unsafe_names_are_refused)
GUARANTEE(test_internal_only_document_cannot_enter)
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Collection, Mapping, Sequence
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
RESTORE = "RESTORE.md"
# the render-model data of each file: the intermediate files of the reference's build folder
RENDER_DATA_FOLDER = "build"


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
    extras: Mapping[str, bytes],
    raw: Sequence[tuple[str, bytes]] = (),
    releases: Mapping[str, bytes] | None = None,
    withheld: Collection[FileKind] = (),
) -> bytes:
    """The workspace zip bytes (see the module docstring). ``extras`` holds ``RESTORE.md`` and
    exactly the configured ``required_members``. ``withheld`` names the client files the set does
    not hold (a file no client-output rule lets through): the zip holds the others and their
    data, and the five are accounted for between them."""
    ws = rules.workspace
    held = set(withheld)
    if (
        set(deliverables) | held != set(FILE_KINDS)
        or set(deliverables) & held
        or set(data) != set(deliverables)
    ):
        raise UnsafePackage("the workspace needs exactly the five client files and their data")
    if set(extras) != {RESTORE, *ws.required_members}:
        raise UnsafePackage("the workspace needs exactly its configured run records")
    members: list[tuple[str, bytes]] = []
    for kind in FILE_KINDS:
        if kind in deliverables:
            filename, body = deliverables[kind]
            members.append((f"deliverables/{filename}", body))
    members += [
        (f"{RENDER_DATA_FOLDER}/{kind}.json", data[kind]) for kind in FILE_KINDS if kind in data
    ]
    members += [(name, extras[name]) for name in ws.required_members]
    members += neutral_payload_members(raw, enabled=ws.raw_payloads.neutral_name_copy_enabled)
    # one text file per archived copy, after the run's own data files (the stable sort keeps them
    # inside the data folder, in name order)
    prefix = ws.releases.folder + "/"
    for name, body in sorted((releases or {}).items()):
        if not name.startswith(prefix):
            raise UnsafePackage("a release file sits outside its folder", (name[:80],))
        members.append((name, body))
    rank = {folder: i for i, folder in enumerate(ws.folders)}
    members.sort(key=lambda m: rank.get(m[0].split("/", 1)[0], len(rank)))
    names = [safe_name(n, ws.entry_name_max) for n, _ in members]
    if len(set(names)) != len(names):
        raise UnsafePackage("duplicate member names")
    # RESTORE.md is built from the run too, so the manifest covers it with every other member
    listing = [
        {"name": n, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
        for n, b in [(RESTORE, extras[RESTORE]), *members]
    ]
    document: dict[str, object] = {
        "schema_version": 1,
        "account": account,
        "as_of": as_of.isoformat(),
        "members": listing,
        "build": dict(manifest),
    }
    manifest_bytes = (json.dumps(document, indent=1, sort_keys=True, default=str) + "\n").encode()
    ordered = [(RESTORE, extras[RESTORE]), ("MANIFEST.json", manifest_bytes), *members]
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
    names, symlink entries, caps, a required run record that is missing, and members whose hash
    disagrees with MANIFEST.json."""
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
        problems.extend(
            f"missing member {n}" for n in (RESTORE, *ws.required_members) if n not in names
        )
        if "MANIFEST.json" in names:
            listing = json.loads(z.read("MANIFEST.json"))["members"]
            listed = {m["name"]: m["sha256"] for m in listing}
            for name in names:
                if name == "MANIFEST.json":
                    continue
                if listed.get(name) != hashlib.sha256(z.read(name)).hexdigest():
                    problems.append(f"hash mismatch {name[:80]!r}")
        else:
            problems.append("no MANIFEST.json")
    return tuple(problems)
