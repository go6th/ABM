"""Guard decision functions (G1 to G13; G14 to G17 live in ``modes`` and ``manifest``).

Pure: no I/O, no clock, no randomness. Each function is the single place its rule is decided; the
orchestrator calls it and acts on the result. Every function has a positive and a lying-scenario
test in ``tests/agents/test_guards.py`` (100% branch coverage is a gate).

GUARANTEE(test_g1_every_row_needs_a_terminal_status_with_proof)
GUARANTEE(test_g2_unread_match_stays_lead)
GUARANTEE(test_g3_three_phrasings_two_tool_categories)
GUARANTEE(test_g4_evidence_must_be_quoted_from_a_given_payload)
GUARANTEE(test_g6_build_needs_a_matching_sense_check)
GUARANTEE(test_g7_unapproved_spend_pauses_or_is_recorded)
GUARANTEE(test_g8_no_consent_no_people_pull)
GUARANTEE(test_g10_private_facts_never_reach_the_public_renderer)
GUARANTEE(test_g11_split_mode_is_routing)
"""

from __future__ import annotations

import hashlib
import re
import string
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from agents.errors import GuardViolation
from agents.manifest import QueryRecord
from agents.ports import ConsentRecord, InternalHistorySettings, RowOutcome, SenseCheckRecord
from agents.schemas import EvidenceRef, PayloadView, StoredFact
from spend.gate import RunCapState, decide

# G1 ----------------------------------------------------------------------------------------------

TERMINAL = frozenset({"done", "partial", "not_done", "blocked", "none"})
NEEDS_REASON = frozenset({"blocked", "not_done", "partial", "none"})
NEEDS_QUERIES = frozenset({"done", "partial"})


@dataclass(frozen=True)
class CoverageVerdict:
    """Whether delivery may proceed, and which rows stop it."""

    deliverable: bool
    missing: tuple[str, ...]
    unproven: tuple[str, ...]


def coverage_verdict(
    required_rows: Iterable[str], rows: Mapping[str, RowOutcome]
) -> CoverageVerdict:
    """G1: every row ends Done, Partial, Not done, Blocked or None with proof. Done and Partial
    need recorded queries; every status other than Done needs a reason."""
    missing, unproven = [], []
    for key in sorted(set(required_rows)):
        row = rows.get(key)
        if row is None or row.status not in TERMINAL:
            missing.append(key)
        elif (row.status in NEEDS_QUERIES and row.queries <= 0) or (
            row.status in NEEDS_REASON and not row.reason
        ):
            unproven.append(key)
    return CoverageVerdict(not missing and not unproven, tuple(missing), tuple(unproven))


# G1b: a pulled page is about the account --------------------------------------------------------

MIN_NAME_CHARS = 4  # a shorter name ("6th") matches too much text to count on its own


def on_target(domain: str, account_name: str, url: str, text: str) -> bool:
    """Whether a pulled page is about the account: its URL is on the account's domain, or the
    page names the domain, or it names the account (a whole word, when the name is long enough
    to mean something). A page a search returned about another company fails this and is
    never read for facts."""
    host = (domain or "").strip().lower().removeprefix("www.")
    if not host:
        return True
    body = (text or "").lower()
    page_host = urlparse(url if "://" in (url or "") else f"https://{url or ''}").hostname or ""
    page_host = page_host.lower().removeprefix("www.")
    if page_host == host or page_host.endswith("." + host) or host in body:
        return True
    name = " ".join((account_name or "").split())
    if len(name) < MIN_NAME_CHARS:
        return False
    return re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", body) is not None


# G2 ----------------------------------------------------------------------------------------------

MATCH_ORIGINS = frozenset({"keyword_match", "date_range"})


def lead_check(origin: str, read_this_run: bool, grade: str) -> None:
    """G2: a candidate from a keyword match or date range that was not read this run is a Lead.
    Any other engine grade for it is refused (an engine or adapter defect, never kept)."""
    if origin in MATCH_ORIGINS and not read_this_run and grade != "lead":
        raise GuardViolation("G2", "unread_match_promoted", grade)


# G3 ----------------------------------------------------------------------------------------------

_PUNCT = str.maketrans({c: " " for c in string.punctuation})


def phrasing_key(text: str) -> frozenset[str]:
    """A query's wording as a set of folded tokens: reordering or re-casing the same words, or
    adding punctuation, is not a different phrasing."""
    folded = unicodedata.normalize("NFKC", text).casefold().translate(_PUNCT)
    return frozenset(folded.split())


@dataclass(frozen=True)
class DropDecision:
    """Whether a lead may be dropped and why not."""

    allowed: bool
    reason: str | None
    phrasings: int
    tool_categories: int


def lead_drop_decision(
    attempts: Sequence[QueryRecord], min_phrasings: int, min_tool_categories: int
) -> DropDecision:
    """G3: a lead may be dropped only after ``min_phrasings`` differently worded queries across
    ``min_tool_categories`` tool categories, each recorded with its result count."""
    keys = {phrasing_key(a.text) for a in attempts} - {frozenset()}
    categories = {a.tool_category for a in attempts}
    if len(keys) < min_phrasings:
        return DropDecision(False, "too_few_phrasings", len(keys), len(categories))
    if len(categories) < min_tool_categories:
        return DropDecision(False, "too_few_tool_categories", len(keys), len(categories))
    return DropDecision(True, None, len(keys), len(categories))


# G4 ----------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def evidence_problem(
    evidence: Sequence[EvidenceRef], given: Mapping[str, PayloadView]
) -> str | None:
    """G4: every evidence item cites a payload this call was given, with an http(s) link, and
    quotes it verbatim (whitespace and case folded). The first problem, or None. Fabricated quotes
    and URLs fail here before the grading engine sees them."""
    if not evidence:
        return "no_evidence"
    for ref in evidence:
        payload = given.get(ref.payload_ref)
        if payload is None:
            return "unknown_payload"
        if not re.match(r"^https?://", payload.url):
            return "no_link"
        if _norm(ref.quote) not in _norm(payload.text):
            return "quote_not_in_payload"
    return None


def quoted(value: str, quotes: Iterable[str]) -> bool:
    """True when ``value`` appears verbatim (folded) in one of ``quotes`` (titles, tenure)."""
    needle = _norm(value)
    return bool(needle) and any(needle in _norm(q) for q in quotes)


# G6 ----------------------------------------------------------------------------------------------


def facts_digest(facts: Iterable[StoredFact]) -> str:
    """sha256 over the sorted (fact id, grade) pairs: the sense check's fact input."""
    joined = "\n".join(sorted(f"{f.fact_id}:{f.grade}" for f in facts))
    return hashlib.sha256(joined.encode()).hexdigest()


def require_sense_check(
    record: SenseCheckRecord | None, manifest_digest: str, current_facts_digest: str
) -> None:
    """G6: build may start only with a stored sense check over the same manifest and facts."""
    if record is None:
        raise GuardViolation("G6", "sense_check_missing")
    if record.manifest_digest != manifest_digest or record.facts_digest != current_facts_digest:
        raise GuardViolation("G6", "sense_check_stale")


# G7 ----------------------------------------------------------------------------------------------

SpendRoute = Literal["run", "pause", "record"]


def spend_route(
    cap: RunCapState,
    op_key: str,
    estimate_micros: int,
    cost_known: bool,
    free_operations: frozenset[str],
    attended: bool,
) -> tuple[SpendRoute, str]:
    """G7: a call the approved cap covers runs; otherwise an attended run pauses for approval and
    an unattended run records the recommendation (the row stays Partial with the reason). The
    allow decision is the credit gate's (``spend.gate.decide``); no model decides to spend."""
    decision = decide(cap, op_key, estimate_micros, cost_known, free_operations)
    if decision.allowed:
        return "run", decision.reason
    return ("pause" if attended else "record"), decision.reason


# G8 ----------------------------------------------------------------------------------------------


def consent_decision(
    record: ConsentRecord | None, scope: str, current_text_version: str | None
) -> str | None:
    """G8 (D-97): None when a people pull may run; otherwise the detail of why the row is Blocked
    ``consent_required``. With no approved consent text there is no valid consent."""
    if current_text_version is None:
        return "consent_text_not_approved"
    if record is None:
        return "no_consent_record"
    if record.revoked_at is not None:
        return "consent_revoked"
    if scope not in record.scopes:
        return "consent_scope_missing"
    if record.text_version != current_text_version:
        return "consent_text_outdated"
    return None


# D-98 --------------------------------------------------------------------------------------------


def internal_history_allowed(settings: InternalHistorySettings, default_enabled: bool) -> bool:
    """D-98: off unless the tenant switched it on through the connectors with admin consent,
    named folders and a connected connector. The default may only ever be off."""
    if default_enabled:
        raise GuardViolation("G10", "internal_history_default_on")
    return (
        settings.enabled
        and settings.admin_consent
        and bool(settings.named_folders)
        and settings.connector_connected
    )


# G10 ---------------------------------------------------------------------------------------------

Role = Literal["public", "internal"]


def renderable(visibility: str, role: Role) -> None:
    """G10: the public renderer reads public facts only; the Internal Only view reads both."""
    if visibility not in ("public", "private"):
        raise GuardViolation("G10", "unknown_visibility", visibility)
    if role == "public" and visibility != "public":
        raise GuardViolation("G10", "private_to_public_renderer")


def for_audience(facts: Iterable[StoredFact], role: Role) -> tuple[StoredFact, ...]:
    """The facts a renderer of ``role`` may see."""
    return tuple(f for f in facts if role == "internal" or f.visibility == "public")


# G11 ---------------------------------------------------------------------------------------------

PULL_CLASS = frozenset({"setup", "pass1", "pass2", "people"})
ANALYSIS_CLASS = frozenset({"sense_check", "distill"})


def routing_violations(
    agent_tasks: Mapping[str, str | None],
    task_tiers: Mapping[str, str],
    cheaper_tier: str,
    stronger_tier: str,
) -> tuple[str, ...]:
    """G11: extraction agents route to the cheaper tier and analysis agents to the stronger one,
    by task kind in the router config; a missing task kind is a violation."""
    out = []
    for agent, task in sorted(agent_tasks.items()):
        if task is None:
            continue
        tier = task_tiers.get(task)
        want = (
            cheaper_tier
            if agent in PULL_CLASS
            else stronger_tier
            if agent in ANALYSIS_CLASS
            else None
        )
        if tier is None or (want is not None and tier != want):
            out.append(f"{agent}:{task}->{tier}")
    return tuple(out)


# G9 / content checks shared by the runtime -------------------------------------------------------

_GRADE_WORDS = "confirmed|probable|hypothesis|lead|conflicting"
_GRADE_LABEL = re.compile(
    # "grade B", "grade: B", "graded as Probable", "rated Confirmed"
    rf"\bgrade[sd]?\s*:?\s*([a-d]\b|(as\s+)?({_GRADE_WORDS})\b)"
    rf"|\b(rated|classified|marked|labell?ed)\s+(as\s+)?({_GRADE_WORDS})\b"
    # "a Confirmed fact", "probable claim", "conflicting evidence", "hypothesis status"
    rf"|\b({_GRADE_WORDS})\s+(fact|facts|grade|grades|claim|claims|evidence|status|finding|"
    rf"findings|signal|signals)\b"
    # "(Confirmed)", "[Probable]", "Probable: ...", "Lead - ..."
    rf"|[\(\[]\s*({_GRADE_WORDS})\s*[\)\]]"
    rf"|(^|[.!?]\s+)({_GRADE_WORDS})\s*[:\u2013-]",
    re.I,
)
# A capitalised grade word inside a sentence ("This is Confirmed by filings") names the engine's
# grade; the lower-case verb ("the company confirmed a new office") is ordinary English and stays,
# and so does a capitalised word that starts a name or a title ("a Lead Data Engineer",
# "the Probable Cause podcast"): a grade label is never followed by another capitalised word.
_GRADE_CAPITALISED = re.compile(
    r"(?<![.!?]\s)(?<!^)\b(Confirmed|Probable|Hypothesis|Lead|Conflicting)\b(?!\s+[A-Z])"
)


def restates_grade(text: str) -> bool:
    """A distilled claim may not restate a grade (the renderer shows the engine's grade). Matches
    grade labels and grade phrasing, not the everyday verbs "confirmed" or "lead"; before
    2026-10-13 any sentence containing "confirmed" was dropped, which emptied whole narratives
    (``missing_narrative`` build failures)."""
    if _GRADE_LABEL.search(text):
        return True
    stripped = text.lstrip()
    return any(m.start() > 0 for m in _GRADE_CAPITALISED.finditer(stripped))
