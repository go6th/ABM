"""Exports are bound to their rows (DM7, roadmap 11.1: "the hash proves the file matches the rows").

``verify`` checks one delivered package three independent ways:

1. the row hash recomputed from the stored ``view_rows`` (``views.rowsources.common.rows_hash``,
   the single implementation) equals the materialisation's recorded hash;
2. every file's bytes equal their recorded sha256 (a swapped file, a changed formula or reordered
   bytes all fail);
3. the rows re-extracted from each file (``views.parity``: xlsx, docx, csv) equal the stored rows.

The verdict is an insert-only ``export_verifications`` row; a failed verification blocks the
package's downloads. The one writer of ``export_verifications``.

GUARANTEE(test_every_tamper_of_an_export_is_detected)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from views import parity
from views.rowsources.common import ViewRow, rows_hash
from views.tables import specs

Loader = Callable[[str, str], Awaitable[bytes]]  # (store, key) -> bytes


@dataclass(frozen=True)
class Verdict:
    """The verification result."""

    ok: bool
    problems: tuple[str, ...]


async def _stored_rows(s: Any, run_id: UUID) -> dict[Any, list[ViewRow]]:
    rows = (
        await s.execute(
            text(
                "SELECT table_kind, position, ref, row_id, cells, source_code, source_url FROM"
                " view_rows"
                " WHERE run_id = :r ORDER BY table_kind, position"
            ),
            {"r": run_id},
        )
    ).all()
    out: dict[Any, list[ViewRow]] = {}
    for r in rows:
        out.setdefault(r.table_kind, []).append(
            ViewRow(
                r.table_kind,
                int(r.position),
                r.ref,
                r.row_id,
                dict(r.cells),
                r.source_code,
                r.source_url,
            )
        )
    return out


async def verify(s: Any, package_id: UUID, load: Loader, *, now: datetime) -> Verdict:
    """Verify a package and record the verdict."""
    pkg = (
        await s.execute(
            text(
                "SELECT p.run_id, p.kind, p.files, p.rows_hash AS pkg_hash, m.rows_hash AS mat_hash"
                " FROM export_packages p LEFT JOIN view_materializations m ON m.run_id = p.run_id"
                " WHERE p.id = :p"
            ),
            {"p": package_id},
        )
    ).one()
    problems: list[str] = []
    stored = await _stored_rows(s, pkg.run_id)
    recomputed = rows_hash(stored)
    for name, recorded in (("package", pkg.pkg_hash), ("materialisation", pkg.mat_hash)):
        if recorded is not None and recorded != recomputed:
            problems.append(f"{name} row hash differs from the stored rows")
    table_specs = specs()
    for f in pkg.files or []:
        body = await load(f.get("store", "objects"), f["key"])
        if hashlib.sha256(body).hexdigest() != f["sha256"]:
            problems.append(f"{f['name']}: bytes differ from the recorded sha256")
            continue
        ext = f["name"].rsplit(".", 1)[-1]
        if ext not in ("csv", "xlsx", "docx"):
            continue
        for kind, spec in table_specs.items():
            if spec.file_kind != f.get("kind") and kind != f.get("kind"):
                continue
            api = [{"ref": r.ref, **r.cells} for r in stored.get(kind, [])]
            res = parity.check(spec, api, ext, body)
            problems += [f"{f['name']}: {p}" for p in res.problems]
    verdict = Verdict(not problems, tuple(problems))
    await s.execute(
        text(
            "INSERT INTO export_verifications (package_id, ok, problems, at)"
            " VALUES (:p, :ok, CAST(:pr AS jsonb), :now)"
        ),
        {"p": package_id, "ok": verdict.ok, "pr": json.dumps(list(verdict.problems)), "now": now},
    )
    return verdict


async def blocked(s: Any, package_id: UUID) -> bool:
    """Whether the newest verification of a package failed (downloads are refused)."""
    row = (
        await s.execute(
            text(
                "SELECT ok FROM export_verifications WHERE package_id = :p ORDER BY id DESC LIMIT 1"
            ),
            {"p": package_id},
        )
    ).first()
    return row is not None and not row.ok
