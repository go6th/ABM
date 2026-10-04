"""One line on what changed between two deliveries (spec 8.3). Pure.

Computed from the previous and the new manifest only: per file, rows added (new row ids), removed
(ids no longer present) and changed (same id, different row digest). Files are listed in the fixed
file order; a file with no change says so. The line names counts, never row content.
"""

from __future__ import annotations

from exports.content import FILE_KINDS
from exports.models import BuildManifest

_NAMES = {
    "summary": "Account Summary",
    "evidence": "Sources and Evidence",
    "people": "People List",
    "jobs": "Job Postings",
    "tech": "Tech Stack and Teams",
}


def _rows(manifest: BuildManifest, kind: str) -> dict[str, str]:
    ids = manifest.row_ids.get(kind, ())
    digests = manifest.row_digests.get(kind, ())
    if len(ids) != len(digests):
        raise ValueError(f"manifest {kind}: row ids and digests differ in length")
    return dict(zip(ids, digests, strict=True))


def change_note(previous: BuildManifest | None, new: BuildManifest) -> str:
    """``"First delivery for this account."`` or one line of per-file counts."""
    if previous is None:
        return "First delivery for this account."
    parts: list[str] = []
    for kind in FILE_KINDS:
        old, cur = _rows(previous, kind), _rows(new, kind)
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
