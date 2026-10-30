"""The raw rows one snapshot yields: the only input of the pure mapping functions (spec 4).

``SnapshotContent`` is produced by a ``ContentReader`` (the Postgres reader in production, an
in-memory fake in tests) inside ONE ``REPEATABLE READ`` transaction (B1). It holds no provider
code, call reference or raw payload. The only cost figure is the dollars a call was charged, kept
for the workspace's pull log (the column is named Cost there); the pull log's calls carry the
operation, never the provider behind it. The snapshot hash is the SHA-256 of its canonical JSON,
so equal content always hashes equally.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

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
    # the date the thing happened (``value_json.event_date``, ISO text); the Recent news table
    # and the signal tables date a finding by it, never by the date it was found
    event_date: str | None = None


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
    # the stored page title and publisher (``sources.title`` / ``sources.publisher``), when the
    # registry recorded them; Evidence part D names a source by them before its host
    title: str | None = None
    publisher: str | None = None
    # the absolute address that serves our archived copy of the page (blank: no copy stored)
    archive_url: str = ""


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
    # the lane id of a ``lane_meaning`` section (migration 0084); None for every other kind
    lane: str | None = None


class FoundVia(_Row):
    """One search that found a person: the kind (``people.models.Origin``), the title-search pass,
    the platform lane an association search ran for and the research page a named person came
    from. The People List's Found by cell prints these as the reference does."""

    origin: str
    pass_no: int | None = None
    lane: str | None = None
    mention_url: str | None = None


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
    # how the person was found (people.models.Origin)
    origin: str = "title_search"
    # prior employers from the pulled profile; None when no profile field was provided
    former_employers: tuple[str, ...] | None = None
    # the profile link of a merged duplicate the merge kept beside the canonical one
    second_profile_url: str | None = None
    # the current title on the pulled profile (None when no profile was read); a title that
    # differs from the title search is a status change in the Summary's People section
    profile_title: str | None = None
    # where the person is (the pulled profile's location, else the search record's)
    location: str | None = None
    # the People List's one role-family lane id (empty for a record stored before the rule), and
    # every search that found the person
    role_lane: str = ""
    found_via: tuple[FoundVia, ...] = ()


class PeopleCounts(_Row):
    """The Summary sheet lines and count sentences from ``people.export`` / ``people.counts``,
    and the people excluded by a title rule: (name, reason, title as reported), listed by name
    on the People List Summary sheet (master prompt v2 9.3)."""

    summary: tuple[tuple[str, str, int], ...]
    sentences: tuple[str, ...]
    in_scope: int = Field(ge=0)
    excluded: tuple[tuple[str, str, str], ...] = ()


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
    # where the posting came from: the live careers board or the job feed
    origin: str = "feed"
    # the sentence the class decision stored as its evidence (``job_posting_classifications``)
    class_sentence: str = ""
    # the posting's location as the job pipeline stored it (``job_postings.location``)
    location: str = ""
    # the feed's last seen date and seniority word, as stored by the job pipeline (the reference
    # workbook prints both; blank when the source did not give them)
    last_seen: date | None = None
    seniority: str = ""
    # (platform, sentence) pairs in posting order: the reference prints them grouped per
    # platform (``Platform: s1 | s2 || Other: s3``); ``platform_sentences`` keeps the flat texts
    platform_sentence_pairs: tuple[tuple[str, str], ...] = ()
    # True when this posting was first seen after the account's previous delivered research
    # (an incremental re-research); None when the run had no previous research to compare with
    new_since_previous: bool | None = None
    # the absolute address that serves our archived copy of the posting (blank: no copy stored)
    archive_url: str = ""
    # how the class was decided: ``extractor`` (the whole posting was read and the answer
    # verified: a graded finding), ``heuristic`` or ``rule`` (a keyword rule over the platform
    # sentences: no finding), ``none`` (undecided); blank for a posting stored without one
    classified_by: str = ""


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


class StackRow(_Row):
    """One ``tech_stack_items`` row: a technology of the organisation's stack under its business
    function and category, with the postings count the lookup reports for it."""

    id: str
    business_function: str
    category: str
    technology: str
    postings: int = 0


class TeamRow(_Row):
    """One team whose public records name a lane platform (the reference's Platform teams sheet).
    No pipeline stores teams yet: the workbook prints rows only when this tuple has some. The
    list fields are printed as the reference prints them (``name (count); name (count)``)."""

    id: str
    name: str
    lane_technologies: str = ""
    postings: int = 0
    first_activity: str | None = None
    last_activity: str | None = None
    technologies: str = ""
    locations: str = ""
    job_functions: str = ""


class TrackerLine(_Row):
    """One row of the tracker's client view, in the tracker's own order."""

    key: str
    row: str
    status: str
    priority: str
    reason: str | None
    tools: tuple[str, ...]
    results: int | None


class WebHit(_Row):
    """One result of a web search: its host and whether the result's own text names the account
    (None when the text was not stored). The entity lock decides from the host first, then this."""

    host: str
    names_account: bool | None = None


class PullCall(_Row):
    """One stored connector call as the workspace's pull log and search list show it."""

    at: datetime
    operation: str
    step: str | None = None
    queries: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    domain: str | None = None
    since: str | None = None
    result_count: int | None = None
    cost_usd: float = 0.0
    hits: tuple[WebHit, ...] = ()


class SenseCheckRow(_Row):
    """The latest stored sense check of the run (counts only; no free text is stored)."""

    findings: int
    proposals: int


class ReleaseRow(_Row):
    """One archived copy of a page, news item or job posting, as the workspace zip's
    ``data/releases/`` prints it: the title, address and day, and the text of the copy."""

    kind: str
    url: str
    title: str
    # the day the item is dated (a posting's first seen day, else the day it was captured)
    day: date
    text: str


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
    # the organisation tech stack of the run (the Confirmed stack sheet), and the teams whose
    # records name a lane platform (the Platform teams sheet; empty until a pipeline stores them)
    stack: tuple[StackRow, ...] = ()
    teams: tuple[TeamRow, ...] = ()
    # how many teams matched and the date the postings count from (the Platform teams title)
    teams_matching: int = 0
    teams_since: date | None = None
    tracker: tuple[TrackerLine, ...] = ()
    # the tenant's white-label display name for document headers (VW6); None: no branding
    brand_name: str | None = None
    tracker_tally: tuple[str, ...] = ()
    # COUNT(*) of the selected rows per file, taken in the same snapshot (B3 reconciliation)
    snapshot_counts: dict[str, int] = Field(default_factory=dict)
    # the account's web domain (title block) and its verified careers host (Evidence part C);
    # ``is_public`` is None until the account record carries it (the private-company tracker
    # rule stays dormant on None, master prompt v2 section 11)
    domain: str | None = None
    careers_host: str | None = None
    is_public: bool | None = None
    # the stored entity lock (``EntityLock.as_dict()``), the run's connector calls in time order,
    # how many calls failed with nothing stored, and the latest sense check: the workspace zip's
    # own members are built from these and nothing else
    entity_lock: dict[str, Any] | None = None
    pull_calls: tuple[PullCall, ...] = ()
    pull_failed: int = 0
    sense_check: SenseCheckRow | None = None
    # the account's archived copies the workspace zip carries under data/releases/ (newest first,
    # at most ``workspace.releases.max_files``; client kinds only, never a profile copy)
    releases: tuple[ReleaseRow, ...] = ()
    # which tenant exclusion set the rows were filtered with
    # (``readers.exclusions.exclusion_hash``); empty for a snapshot not read from the settings
    exclusion_hash: str = ""

    def snapshot_hash(self) -> str:
        """SHA-256 of the canonical JSON of the content (B1, B10)."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


FILE_KINDS: tuple[FileKind, ...] = ("summary", "evidence", "people", "jobs", "tech")
