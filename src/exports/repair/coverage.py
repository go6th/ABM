"""The Coverage table: the single source of truth for what was researched. Status words are
normalised, a ``Done`` without evidence is downgraded, the workspace's consents and connections
apply as blockers, and the claims of the deliverables are checked against the result."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.records import CoverageRecord, DeliverableClaim

TABLE_KEY = "coverage"
TABLE_TITLE = "Coverage"
CLAIMS_SHEET = "Deliverable claims"
CLAIM_COLUMNS = (
    "Deliverable", "Section", "Coverage item", "Coverage status", "Says done",
    "Partial note present", "Result",
)  # fmt: skip

STATUSES = ("Done", "Partial", "Not done", "Blocked", "None found", "Not checked")
MAIL_ITEMS = (1, 2)
REASON_NO_EVIDENCE = "no evidence rows"
REASON_PEOPLE = "People data consent needed"
REASON_MAIL = "Mail and drive not connected"

_STATUS_WORDS: dict[str, str] = {
    "done": "Done", "complete": "Done", "completed": "Done", "ok": "Done", "yes": "Done",
    "partial": "Partial", "partially done": "Partial", "in progress": "Partial",
    "partially": "Partial",
    "not done": "Not done", "pending": "Not done", "todo": "Not done", "to do": "Not done",
    "not started": "Not done", "skipped": "Not done", "no": "Not done",
    "blocked": "Blocked",
    "none found": "None found", "no results": "None found", "nothing found": "None found",
    "not found": "None found",
    "not checked": "Not checked", "unchecked": "Not checked", "unknown": "Not checked",
    "": "Not checked",
}  # fmt: skip

# generic wording of a coverage item that needs people data
_PEOPLE_ITEM = re.compile(
    r"\b(people|person|persons|stakeholders?|buying (?:group|committee|centre|center)"
    r"|champions?|decision[- ]?makers?|contacts|org(?:anisation|anization)? chart|linkedin"
    r"|platform owners?)\b",
    re.I,
)
_CODE = re.compile(r"^([A-Za-z]*)(\d+)$")


def normalise_status(word: str) -> str | None:
    """One of ``STATUSES`` for a stored status word, or None when the word is not recognised."""
    key = re.sub(r"[_\-]+", " ", (word or "").strip().lower())
    key = " ".join(key.split())
    return _STATUS_WORDS.get(key)


def _code_key(code: str) -> tuple[str, int, str]:
    m = _CODE.match(code)
    return (m.group(1), int(m.group(2)), code) if m else ("~", 0, code)


def _codes(record: CoverageRecord) -> list[str]:
    unique = {c.strip() for c in record.source_codes if c and c.strip()}
    return sorted(unique, key=_code_key)


def evidence_cell(record: CoverageRecord) -> str:
    """``<n> rows; S1, S4``, or ``Not found`` when there is neither a row nor a source."""
    codes = _codes(record)
    rows = max(record.evidence_rows, 0)
    if rows == 0 and not codes:
        return core.NOT_FOUND
    return f"{rows} rows; {', '.join(codes)}" if codes else f"{rows} rows"


def _keep_newer(a: CoverageRecord, b: CoverageRecord) -> CoverageRecord:
    """Of two tracker rows with one key, the one of the later run (the first on a tie)."""
    if b.last_run and (a.last_run is None or b.last_run > a.last_run):
        return b
    return a


def _dedupe(records: Sequence[CoverageRecord]) -> tuple[list[CoverageRecord], list[Exclusion]]:
    chosen: dict[str, CoverageRecord] = {}
    order: list[CoverageRecord | str] = []
    dropped: list[Exclusion] = []
    for rec in records:
        key = rec.row_key.strip()
        if not key:
            order.append(rec)
        elif key in chosen:
            winner = _keep_newer(chosen[key], rec)
            loser = rec if winner is chosen[key] else chosen[key]
            chosen[key] = winner
            dropped.append(Exclusion("coverage", key, "duplicate tracker row", loser.item))
        else:
            chosen[key] = rec
            order.append(key)
    return [chosen[o] if isinstance(o, str) else o for o in order], dropped


def _people_blocked(profile: WorkspaceProfile, tables: Mapping[str, RepairedTable]) -> bool:
    people = tables.get("people")
    return (not profile.people_consent) or bool(people and people.stats.get("consent_missing"))


def _mail_blocked(profile: WorkspaceProfile, tables: Mapping[str, RepairedTable]) -> bool:
    mail = tables.get("mail")
    return (not profile.mail_drive_connected) or bool(mail and mail.stats.get("connected") is False)


def _join_reasons(parts: Sequence[str]) -> str:
    seen: list[str] = []
    for part in parts:
        text = part.strip().rstrip(".")
        if text and text.lower() not in (s.lower() for s in seen):
            seen.append(text)
    return "; ".join(seen)


def build_coverage(
    profile: WorkspaceProfile,
    records: Sequence[CoverageRecord],
    tables: Mapping[str, RepairedTable],
    claims: Sequence[DeliverableClaim] = (),
) -> RepairedTable:
    """Build the Coverage table (sheet ``Coverage``) and the ``Deliverable claims`` sheet."""
    items, exclusions = _dedupe(records)
    people_blocked = _people_blocked(profile, tables)
    mail_blocked = _mail_blocked(profile, tables)
    sheet = Sheet(TABLE_TITLE, core.COVERAGE_COLUMNS)
    by_key: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    downgraded = people_set = mail_set = 0

    for seq, rec in enumerate(items, start=1):
        status = normalise_status(rec.status)
        reasons = [rec.reason]
        if status is None:
            status = "Not checked"
            reasons.append(f"status word not recognised: {rec.status.strip()}")
        if status == "Done" and rec.evidence_rows <= 0 and not _codes(rec):
            status = "Partial"
            reasons.append(REASON_NO_EVIDENCE)
            downgraded += 1

        blockers: list[str] = []
        if mail_blocked and rec.number in MAIL_ITEMS:
            blockers.append(REASON_MAIL)
            mail_set += 1
        if people_blocked and _PEOPLE_ITEM.search(rec.item):
            blockers.append(REASON_PEOPLE)
            people_set += 1
        if blockers:
            if status == "Blocked":
                reasons = [*reasons, *blockers]
            else:
                status, reasons = "Not done", blockers

        if status == "Done":
            reason = core.NOT_APPLICABLE
        else:
            reason = _join_reasons(reasons) or core.not_collected("reason not recorded")
        row = {
            "#": str(rec.number or seq),
            "Research item": rec.item.strip() or core.NOT_FOUND,
            "Priority": rec.priority.strip() or core.NOT_FOUND,
            "Status": status,
            "Reason": reason,
            "Evidence (row counts / source codes)": evidence_cell(rec),
            "Last run": core.iso(rec.last_run),
        }
        sheet.rows.append(row)
        if rec.row_key.strip():
            by_key[rec.row_key.strip()] = row

    sheet.rows.sort(key=lambda r: int(r["#"]) if r["#"].isdigit() else 0)
    counts = {s: sum(1 for r in sheet.rows if r["Status"] == s) for s in STATUSES}
    if downgraded:
        notes.append(
            f"{downgraded} item(s) marked Done had no evidence rows and are shown Partial."
        )
    if people_set:
        notes.append(f"{REASON_PEOPLE}: {people_set} people item(s) cannot be Done.")
    if mail_set:
        notes.append(f"{REASON_MAIL}: Coverage items 1 and 2 are Not done.")
    if not sheet.rows:
        notes.append("No coverage tracker rows were recorded for the latest run.")

    claim_sheet, mismatches, missing = _claims_sheet(claims, by_key)
    stats: dict[str, Any] = {
        "status_counts": counts,
        "items": len(sheet.rows),
        "downgraded": downgraded,
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
        "claims": len(claim_sheet.rows),
        "claims_without_item": missing,
    }
    return RepairedTable(
        key=TABLE_KEY,
        title=TABLE_TITLE,
        sheets=[sheet, claim_sheet],
        exclusions=exclusions,
        stats=stats,
        notes=notes,
    )


def _claims_sheet(
    claims: Sequence[DeliverableClaim], by_key: Mapping[str, Mapping[str, str]]
) -> tuple[Sheet, list[str], list[str]]:
    sheet = Sheet(CLAIMS_SHEET, CLAIM_COLUMNS)
    mismatches: list[str] = []
    missing: list[str] = []
    for claim in claims:
        row = by_key.get(claim.row_key.strip())
        if row is None:
            missing.append(claim.row_key)
        status = row["Status"] if row else "Not checked"
        label = row["Research item"] if row else (claim.row_key.strip() or core.NOT_FOUND)
        ok = status == "Done" or not claim.says_done or claim.carries_partial_note
        result = "OK" if ok else f"Mismatch: section says done but coverage is {status}"
        sheet.rows.append(
            {
                "Deliverable": claim.file.strip() or core.NOT_FOUND,
                "Section": claim.section.strip() or core.NOT_FOUND,
                "Coverage item": label,
                "Coverage status": status,
                "Says done": "Yes" if claim.says_done else "No",
                "Partial note present": "Yes" if claim.carries_partial_note else "No",
                "Result": result,
            }
        )
        if not ok:
            mismatches.append(f"{claim.file} > {claim.section}: {result}")
    return sheet, mismatches, missing


def claim_mismatches(table: RepairedTable) -> list[str]:
    """The deliverable sections that say an area is done while Coverage disagrees (empty: the
    deliverables and Coverage agree). Read from the table's own claims sheet."""
    sheet = next((s for s in table.sheets if s.name == CLAIMS_SHEET), None)
    if sheet is None:
        return list(table.stats.get("mismatches", []))
    return [
        f"{r.get('Deliverable', '')} > {r.get('Section', '')}: {r.get('Result', '')}"
        for r in sheet.rows
        if str(r.get("Result", "")).startswith("Mismatch")
    ]
