"""One line on what changed between two deliveries (spec 8.3). Pure.

Computed from the previous and the new manifest only: per file, rows added (new row ids), removed
(ids no longer present) and changed (same id, different row digest). Files are listed in the fixed
file order; a file with no change says so. The line names counts, never row content.
"""

from __future__ import annotations

from collections.abc import Mapping

from exports.content import FILE_KINDS
from exports.models import BuildManifest

_NAMES = {
    "summary": "Account Summary",
    "evidence": "Sources and Evidence",
    "people": "People List",
    "jobs": "Job Postings",
    "tech": "Tech Stack and Teams",
}


Rows = Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]  # kind -> (row ids, digests)


def _rows(ids: tuple[str, ...], digests: tuple[str, ...], kind: str) -> dict[str, str]:
    if len(ids) != len(digests):
        raise ValueError(f"manifest {kind}: row ids and digests differ in length")
    return dict(zip(ids, digests, strict=True))


def manifest_rows(manifest: BuildManifest) -> Rows:
    """The row ids and digests of a manifest, per file."""
    return {
        k: (manifest.row_ids.get(k, ()), manifest.row_digests.get(k, ())) for k in FILE_KINDS
    }


def rows_change_note(previous: BuildManifest | None, rows: Rows) -> str:
    """The change note for a build whose files map to ``rows`` (computed before rendering, so
    the documents can open with it): ``"First delivery for this account."`` or one line of
    per-file counts against the previous manifest."""
    if previous is None:
        return "First delivery for this account."
    before = manifest_rows(previous)
    parts: list[str] = []
    for kind in FILE_KINDS:
        old = _rows(*before[kind], kind)
        cur = _rows(*rows.get(kind, ((), ())), kind)
        added = sum(1 for k in cur if k not in old)
        removed = sum(1 for k in old if k not in cur)
        changed = sum(1 for k in cur if k in old and cur[k] != old[k])
        if added == removed == changed == 0:
            parts.append(f"{_NAMES[kind]}: no change")
        else:
            parts.append(
                f"{_NAMES[kind]}: {added} rows added, {removed} removed, {changed} changed"
            )
    return "Changes since the previous delivery: " + "; ".join(parts) + "."


def change_note(previous: BuildManifest | None, new: BuildManifest) -> str:
    """The change note between two manifests (``rows_change_note`` over the new one's rows)."""
    return rows_change_note(previous, manifest_rows(new))
