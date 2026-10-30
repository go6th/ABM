"""The account's CURRENT row of each thing the research records per run (SQL fragments).

A research that resumes or repeats an account records its facts, people, postings and technology
detections again under a new run: a resume re-grades, an incremental re-research carries the
previous rows into the new run. The delivered files read ONE run, so they never repeat; every
reader that selects by account (the account page's counts and key findings, the live sheets, Ask,
the CRM items) shares the one rule here instead: of the rows that are the same thing, only the
newest is read (leakage review 2026-10-30, F2). The fragments are ``AND`` clauses over the alias
of the row they test.
"""

from __future__ import annotations


def newest_only(table: str, alias: str, same: str) -> str:
    """SQL (an ``AND`` clause) keeping, of the rows of one account that are the same thing
    (``same`` compares ``n``, the other row, with ``alias``), only the newest: the research that
    resumes or repeats an account records its facts, people and postings again under a new run,
    and every reader that selects by account must show each once, not once per run (leakage
    review 2026-10-30, F2; the delivered files read one run, so they never repeat)."""
    return (
        f" AND NOT EXISTS (SELECT 1 FROM {table} n WHERE n.account_id = {alias}.account_id"
        f" AND {same} AND (n.created_at, n.id) > ({alias}.created_at, {alias}.id))"
    )


# the account's CURRENT row of each thing the research records per run
NEWEST_FACT = newest_only(
    "facts",
    "f",
    "n.fingerprint = f.fingerprint AND n.status = 'current' AND n.superseded_at IS NULL"
    " AND n.visibility = f.visibility",
)
NEWEST_POSTING = newest_only("job_postings", "j", "n.posting_key = j.posting_key")
NEWEST_PERSON = newest_only(
    "people", "p", "p.person_key IS NOT NULL AND n.person_key = p.person_key"
)
NEWEST_DETECTION = newest_only("tech_detections", "t", "n.technology = t.technology")
