"""Write benchmark evidence into the evidence slots of docs/decision_log.md.

Each decision has a slot between markers. Writing replaces only that slot, so options,
recommendations and sign-off lines are never touched. Results that are not evidence (fake or
recorded calls, synthetic golden accounts) are refused unless explicitly allowed, and are then
stamped NOT EVIDENCE.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.benchmarks.common import Provenance

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "docs" / "decision_log.md"
DECISIONS = ("workflow_engine", "model_split", "people_source")


class EvidenceRefused(RuntimeError):
    pass


def markers(key: str) -> tuple[str, str]:
    return f"<!-- BEGIN EVIDENCE: {key} -->", f"<!-- END EVIDENCE: {key} -->"


def render_evidence(
    body_markdown: str, provenance: Provenance, *, config_version: str, now: datetime | None = None
) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    banner = "" if provenance.is_evidence else f"> **{provenance.label()}**\n\n"
    header = f"{banner}Generated {stamp}. Config version `{config_version}`."
    return f"{header}\n\n{body_markdown.strip()}\n"


def write_evidence(
    log_path: Path | str,
    key: str,
    body_markdown: str,
    provenance: Provenance,
    *,
    config_version: str,
    allow_non_evidence: bool = False,
    now: datetime | None = None,
) -> None:
    if key not in DECISIONS:
        raise KeyError(f"unknown decision {key!r}")
    if not provenance.is_evidence and not allow_non_evidence:
        raise EvidenceRefused(
            f"refusing to write {key} results into the decision log: {provenance.label()}"
        )
    path = Path(log_path)
    text = path.read_text(encoding="utf-8")
    begin, end = markers(key)
    start = text.find(begin)
    stop = text.find(end)
    if start == -1 or stop == -1 or stop < start:
        raise ValueError(f"decision log has no evidence slot for {key}")
    content = render_evidence(body_markdown, provenance, config_version=config_version, now=now)
    new = text[: start + len(begin)] + "\n" + content + text[stop:]
    path.write_text(new, encoding="utf-8")
