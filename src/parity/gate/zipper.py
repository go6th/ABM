"""The workspace zip: ``{prefix}_workspace.zip`` with the allow-list and scrubbed copies.

Members, in sorted order: ``RESTORE.md`` generated from the bundle; ``deliverables/`` with the
standing files present in the deliverables folder and the Lane Highlights when present;
``data/``, ``build/`` and ``notes/`` copied from the workspace with every text file scrubbed
(``scrub.scrub_tree``). A file whose name is refused (``files.refused_name``) is left out and
listed in the result. Every member has the same fixed timestamp (the research window's end
date, else 1980-01-01), the same permissions and the same compression, so one input gives one
zip, byte for byte.

``RESTORE.md`` states the layout, the restore steps, the canonical query definitions with the
computed numbers (postings naming each lane, senior people, platform rank with its base) and
the entity rules of ``meta.entities``.
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from parity import counts
from parity.gate.files import Bundle, optional_names, prefix_of, refused_name, standing_names
from parity.gate.scrub import ScrubSummary, scrub_tree, walk

SCRUBBED_ROOTS = ("data", "build", "notes")
_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass
class ZipResult:
    """What the zip holds and what was left out."""

    path: Path
    members: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    scrub: ScrubSummary = field(default_factory=ScrubSummary)


def _fmt(n: int) -> str:
    return f"{n:,}"


def fixed_timestamp(bundle: Bundle) -> tuple[int, int, int, int, int, int]:
    """The zip timestamp: the window's end date at midnight, else 1980-01-01."""
    window = (bundle.get("meta") or {}).get("window") or {}
    try:
        y, m, d = (int(x) for x in str(window.get("to")).split("-"))
        return (max(y, 1980), m, d, 0, 0, 0)
    except (TypeError, ValueError):
        return _EPOCH


def restore_md(bundle: Bundle) -> str:
    """The RESTORE.md text for a bundle."""
    meta = bundle["meta"]
    prefix = prefix_of(bundle)
    display = str(meta.get("pursuing_client_display") or "Client")
    names = standing_names(prefix)
    lines = [
        f"# {prefix} ({meta.get('account_legal', prefix)}) workspace",
        "",
        f"{str(meta.get('byline') or 'Research by 6th agents').rstrip('.')}. Phase 1 pulls "
        f"{meta.get('pull_date', '')}, phase 2 analysis and deliverables "
        f"{meta.get('run_date', '')}. Pursuing client: {display}.",
        "",
        "## Layout",
        f"- deliverables/: {', '.join(names)}"
        + (f", {optional_names(prefix)[0]} when present" if optional_names(prefix) else ""),
        "- data/: raw pulls (one file per source pulled in phase 1; email addresses and the"
        " pursuing client's name replaced by the display word)",
        "- build/: the scripts and intermediate files that turn data into the deliverables",
        "- notes/: the pull log (every call, parameters, counts), the pass history and the"
        " phase 2 sense check, scrubbed the same way",
        "",
        "## Restore",
        "1. Unzip. The deliverables are in deliverables/ as delivered.",
        "2. To rebuild them, run the build scripts from build/ in the order the pull log names;"
        " they read data/ and write deliverables/.",
        "3. Every count in the deliverables is a formula or a value the bundle computed; the"
        " canonical definitions below are the wording to use for any question against this"
        " workspace.",
        "",
        "## Canonical query definitions (use this wording for any question against this workspace)",
    ]
    jobs = counts.jobs_summary(bundle)
    for lane, jc in jobs.items():
        lines.append(
            f'- "Postings naming {lane}": rows in {names[3]}, sheet Jobs, where Platforms named'
            f" contains {lane} and Entity starts with {prefix}. Answer on "
            f"{meta.get('run_date', '')}: {_fmt(jc.total)} ({_fmt(jc.open)} open)."
        )
    computed = bundle.get("computed") or {}
    totals = counts.jobs_row_totals(bundle)
    lines.append(
        f'- "Rows on the Jobs sheet": {_fmt(totals.all_rows)} in all, {_fmt(totals.account_rows)}'
        f" {prefix} positions, {_fmt(totals.other_rows)} other entities."
    )
    lines.append(
        f'- "Senior people": {names[2]}, Meets seniority rule equals Yes:'
        f" {_fmt(counts.meets_seniority(bundle))} of {_fmt(len(bundle.get('people_rows') or ()))}."
        f" Priority A senior: {_fmt(int((computed.get('prio') or {}).get('A') or 0))}."
    )
    ranking = counts.platform_ranking(bundle)
    base = int((bundle.get("ranking") or {}).get("base") or 0)
    by_rank = {
        letter: [r.platform for r in ranking if r.rank == letter] for letter in ("A", "B", "C")
    }
    lines.append(
        f'- "Platform rank": {names[4]}, sheet Platform Ranking, base {_fmt(base)} postings naming'
        " any platform in the dictionary. "
        + " ".join(f"{letter}: {', '.join(p) or 'none'}." for letter, p in by_rank.items())
    )
    lines.append(
        "- A strict query that returns zero is reported as zero, then the nearest relaxed set is"
        " shown and labeled."
    )
    lines += ["", "## Entity rules applied"]
    ent_counts = computed.get("ent_counts") or {}
    for entity in meta.get("entities") or ():
        label = str(entity.get("label"))
        counted = (
            "counted as " + prefix
            if entity.get("counted_as_account")
            else "never counted as " + prefix
        )
        n = ent_counts.get(label)
        count = f" ({_fmt(int(n))} rows)" if isinstance(n, int) else ""
        note = f" {entity['note']}" if entity.get("note") else ""
        lines.append(f"- {label}: {counted}{count}.{note}")
    return "\n".join(lines) + "\n"


def build_workspace_zip(
    bundle: Bundle, workspace_dir: Path, deliverables_dir: Path, out_path: Path | None = None
) -> ZipResult:
    """Write the zip (``out_path`` or ``{prefix}_workspace.zip`` beside the deliverables)."""
    prefix = prefix_of(bundle)
    path = out_path or deliverables_dir / f"{prefix}_workspace.zip"
    result = ZipResult(path)
    stamp = fixed_timestamp(bundle)
    entries: list[tuple[str, bytes | Path]] = [("RESTORE.md", restore_md(bundle).encode("utf-8"))]
    for name in standing_names(prefix) + optional_names(prefix):
        candidate = deliverables_dir / name
        if candidate.is_file():
            entries.append((f"deliverables/{name}", candidate))
    with tempfile.TemporaryDirectory() as tmp:
        scrubbed = Path(tmp)
        for root in SCRUBBED_ROOTS:
            src = workspace_dir / root
            if not src.is_dir():
                continue
            summary = scrub_tree(src, scrubbed / root, bundle)
            result.scrub.files += summary.files
            result.scrub.text_files += summary.text_files
            result.scrub.replacements += summary.replacements
            result.scrub.dropped_lines += summary.dropped_lines
            result.scrub.binary_copied += [f"{root}/{b}" for b in summary.binary_copied]
            for file in walk(scrubbed / root):
                rel = f"{root}/{file.relative_to(scrubbed / root).as_posix()}"
                entries.append((rel, file))
        result.refused = [name for name, _ in entries if refused_name(name)]
        entries = [e for e in entries if e[0] not in result.refused]
        entries.sort(key=lambda e: e[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as z:
            for name, source in entries:
                info = zipfile.ZipInfo(name, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o644 & 0xFFFF) << 16
                info.create_system = 3
                data = source if isinstance(source, bytes) else source.read_bytes()
                z.writestr(info, data, compresslevel=9)
                result.members.append(name)
    return result
