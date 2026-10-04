"""The raw rows one snapshot yields: the only input of the pure mapping functions (spec 4).

``SnapshotContent`` is produced by a ``ContentReader`` (the Postgres reader in production, an
in-memory fake in tests) inside ONE ``REPEATABLE READ`` transaction (B1). It holds no provider
code, call reference, cost figure or raw payload: the reader never selects those columns. The
snapshot hash is the SHA-256 of its canonical JSON, so equal content always hashes equally.
"""

from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from exports.models import FileKind


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactRow(_Row):
    """A current graded fact of the run (``facts``, superseded rows excluded by the reader)."""

    id: str
    attribute_key: str
    value_text: str
    lane: str | None
    claim_kind: str | None
    product_grade: str
    engine_grade: str | None
    read_in_primary_this_run: bool
    may_state_to_prospect: bool
    confirming_question: str | None
    flags: tuple[str, ...] = ()
    lead_status: str | None = None
    visibility: str = "public"


class EvidenceRow(_Row):
    """One ``fact_evidence`` row with its source code resolved by the reader."""

    id: str
    fact_id: str
    evidence_kind: str
    url: str | None
    supporting_sentence: str
    source_code: str | None
    is_aggregator: bool
    origin: str | None
    evidence_date: date | None


class SourceRow(_Row):
    """One ``sources`` registry row of the account."""

    code: str
    url: str
    published: date | None
    kind: str


class SectionRow(_Row):
    """One stored ``report_sections`` row (latest version of the run)."""

    id: str
    kind: str
    position: int
    text: str
    codes: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()
    confirming_question: str | None = None
    speaker: str | None = None
    visibility: str = "public"


class PersonRow(_Row):
    """One People List row as the people module's own export selected it (``people.export``):
    in scope, not erased, not internal-only, with a profile link."""

    id: str
    name: str
    title: str
    level: str
    lanes: str
    priority: str
    profile_url: str
    source_code: str | None


class PeopleCounts(_Row):
    """The Summary sheet lines and count sentences from ``people.export`` / ``people.counts``."""

    summary: tuple[tuple[str, str, int], ...]
    sentences: tuple[str, ...]
    in_scope: int = Field(ge=0)


class FindingRow(_Row):
    """A Findings from LinkedIn item (``people.findings``): a claim and one verbatim sentence from
    the person's own post, with the post link."""

    person_id: str
    claim: str
    sentence: str
    url: str


class PostingRow(_Row):
    """One ``job_postings`` row with its stored description (hash checked by the reader)."""

    posting_key: str
    title: str | None
    url: str | None
    status: str
    posting_class: str | None
    platforms: tuple[str, ...] = ()
    platform_sentences: tuple[str, ...] = ()
    description: str = ""
    posted_at: date | None = None
    first_seen: date | None = None
    full_text_read: bool = False
    hostile: bool = False
    window_bucket: str = "in_window"


class JobsCounts(_Row):
    """Posting counts computed by ``jobs.counts`` and stored with the pipeline record."""

    lines: tuple[tuple[str, str, int, str], ...]  # (key, label, value, base)
    sentences: tuple[str, ...]


class DetectionRow(_Row):
    """One ``tech_detections`` row with its source resolved by the reader."""

    id: str
    technology: str
    first_seen: str | None
    last_seen: str | None
    source_url: str | None
    source_code: str | None


class TrackerLine(_Row):
    """One row of the tracker's client view, in the tracker's own order."""

    key: str
    row: str
    status: str
    priority: str
    reason: str | None
    tools: tuple[str, ...]
    results: int | None


class SnapshotContent(_Row):
    """Everything one build reads, from one snapshot."""

    tenant_id: str
    account_id: str
    run_id: str
    account_name: str
    as_of: date
    is_salesforce_partner: bool
    forbidden_terms: tuple[str, ...] = ()
    facts: tuple[FactRow, ...] = ()
    evidence: tuple[EvidenceRow, ...] = ()
    sources: tuple[SourceRow, ...] = ()
    sections: tuple[SectionRow, ...] = ()
    people: tuple[PersonRow, ...] = ()
    # (name, listed) for every named person of the run, any status: the excluded-name guard
    people_names: tuple[tuple[str, bool], ...] = ()
    people_counts: PeopleCounts | None = None
    findings: tuple[FindingRow, ...] = ()
    postings: tuple[PostingRow, ...] = ()
    jobs_counts: JobsCounts | None = None
    detections: tuple[DetectionRow, ...] = ()
    tracker: tuple[TrackerLine, ...] = ()
    tracker_tally: tuple[str, ...] = ()
    # COUNT(*) of the selected rows per file, taken in the same snapshot (B3 reconciliation)
    snapshot_counts: dict[str, int] = Field(default_factory=dict)

    def snapshot_hash(self) -> str:
        """SHA-256 of the canonical JSON of the content (B1, B10)."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


FILE_KINDS: tuple[FileKind, ...] = ("summary", "evidence", "people", "jobs", "tech")
