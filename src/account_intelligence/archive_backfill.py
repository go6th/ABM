"""Re-process the archived page and news copies of an account into cleaned version 2 records.

A copy written before the cleaning (``page-archive/1``, ``news-archive/1``, a bare item) keeps the
page text as it was pulled. This reads each stored copy, rebuilds it as ``page-archive/2`` or
``news-archive/2`` (``archive_records.web_record``), links records that report the same event
(``archive_reconcile``) and writes the result as the newest copy of the address, keeping the
original capture time (the copy table is insert-only: the old copy stays, the new one wins).

Offline: it reads the stored page text and never fetches. A copy that holds no text cannot be
rebuilt without fetching the page again, which is a paid call: it is listed (``needs_refetch``) and
left as it is. Profile, posting and detection copies are not touched.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.archive_facts import Account
from account_intelligence.archive_reconcile import reconcile

PAGE_KINDS = frozenset({"page", "feed_item"})


@dataclass
class Report:
    """What a backfill did, for ``Logs/archive_backfill_report.md``."""

    accounts: int = 0
    processed: int = 0
    already_current: int = 0
    written: int = 0
    boilerplate_removed: int = 0
    duplicates_removed: int = 0
    tables_repaired: int = 0
    ai_flagged: int = 0
    primary_matched: int = 0
    linked_duplicates: int = 0
    linked_corroborations: int = 0
    needs_refetch: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    by_source_type: Counter[str] = field(default_factory=Counter)
    by_grade: Counter[str] = field(default_factory=Counter)

    def markdown(self, *, applied: bool, when: str) -> str:
        """The report as markdown."""
        mode = "applied" if applied else "dry run (nothing written)"
        lines = [
            "# Archive backfill report",
            "",
            f"Run {when}, {mode}. Accounts: {self.accounts}.",
            "",
            "| Measure | Count |",
            "| --- | --- |",
            f"| Records processed | {self.processed} |",
            f"| Records written as the newest copy | {self.written} |",
            f"| Records already current (version 2) | {self.already_current} |",
            f"| Boilerplate lines removed | {self.boilerplate_removed} |",
            f"| Duplicate paragraphs and tables merged | {self.duplicates_removed} |",
            f"| Flattened tables repaired | {self.tables_repaired} |",
            f"| AI-generated summaries flagged | {self.ai_flagged} |",
            f"| Primary sources matched to a summary | {self.primary_matched} |",
            f"| Records linked as duplicate_of | {self.linked_duplicates} |",
            f"| Records linked as corroborates | {self.linked_corroborations} |",
            f"| Records that failed validation | {len(self.failed)} |",
            f"| Copies with no stored text (re-fetch needed) | {len(self.needs_refetch)} |",
            f"| Copies that could not be read | {len(self.errors)} |",
            "",
            "The research stores the page text, not raw HTML, so each record was "
            "rebuilt from the stored page text. A copy with no stored text is listed below and was "
            "left as it was; fetching it again is a paid call and needs your go-ahead.",
            "",
            "## Source types",
            *[f"- {k}: {v}" for k, v in sorted(self.by_source_type.items())],
            "",
            "## Grades",
            *[f"- {k}: {v}" for k, v in sorted(self.by_grade.items())],
        ]
        for title, rows in (
            ("Failed validation", [f"{u} - {r}" for u, r in self.failed]),
            ("No stored text (needs re-fetch)", self.needs_refetch),
            ("Could not be read", [f"{u} - {r}" for u, r in self.errors]),
        ):
            lines += [
                "",
                f"## {title}",
                *(f"- {r}" for r in rows[:500]),
                *(["- (more not shown)"] if len(rows) > 500 else []),
            ]
        return "\n".join(lines) + "\n"


def source_of(data: dict[str, Any]) -> tuple[str, str | None]:
    """``(schema, text)`` of a stored copy: the version, and the page text it holds (a news copy's
    ``article_text``, a page copy's or bare item's ``text``)."""
    schema = str(data.get("schema") or "")
    for key in ("article_text", "text", "body_markdown"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return schema, value
    return schema, None


async def backfill_account(
    archive: LA.LinkArchive,
    account: Account,
    report: Report,
    *,
    apply: bool,
    force: bool = False,
    guard: AR.Guard = AR.default_guard,
) -> None:
    """Rebuild every page and news copy of one account (see the module docstring)."""
    report.accounts += 1
    links: list[LA.ArchivedLink] = []
    offset = 0
    # LOOP(archive index pages): cap=pages of 200 to the account total; exit=empty page or total
    while True:
        async with archive.txn(archive.tenant_id) as s:
            page, total = await LA.list_latest(
                s, archive.account_id, kind=None, offset=offset, limit=LA.MAX_LIST, kinds=PAGE_KINDS
            )
        links += page
        offset += len(page)
        if not page or offset >= total:
            break
    built: dict[str, dict[str, Any]] = {}
    current: set[str] = set()
    meta: dict[str, LA.ArchivedLink] = {}
    for link in links:
        report.processed += 1
        try:
            data = json.loads(await archive.payloads.get(link.payload_ref))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception as error:  # noqa: BLE001 - one unreadable copy never stops the rest
            report.errors.append((link.url, type(error).__name__))
            continue
        schema, text = source_of(data)
        if schema.endswith("/2") and not force:
            report.already_current += 1
            built[link.url] = data
            current.add(link.url)
            meta[link.url] = link
            continue
        if text is None:
            report.needs_refetch.append(link.url)
            continue
        news = schema.startswith("news-archive")
        record = AR.web_record(
            link.url,
            text,
            schema=AR.NEWS2 if news else AR.PAGE2,
            title=link.title or data.get("title"),
            captured_at=link.archived_at,
            kind=link.kind,
            published_date=data.get("published_date"),
            category=data.get("category") if news else None,
            summary=data.get("summary") if news else None,
            account=account,
            guard=guard,
        )
        built[link.url] = record
        meta[link.url] = link
        quality = record["quality"]
        report.boilerplate_removed += quality["boilerplate_removed"]
        report.duplicates_removed += quality["duplicates_removed"]
        report.tables_repaired += quality["tables_repaired"]
        report.ai_flagged += bool(record["is_ai_generated"])
        report.by_source_type[record["source_type"]] += 1
        report.by_grade[record["fact_grade"]] += 1
        if quality.get("flags"):
            report.failed.append((link.url, "; ".join(quality["flags"])))
    linked = reconcile(account.domain, built)
    for url, record in linked.items():
        built[url] = record
        report.linked_duplicates += bool(record.get("duplicate_of"))
        report.linked_corroborations += bool(record.get("corroborates"))
        report.primary_matched += bool(record.get("quality", {}).get("primary_matched"))
    if not apply:
        return
    entries = []
    for url, record in built.items():
        if url in current and url not in linked:
            continue
        link = meta[url]
        entries.append(
            await archive.entry_for_record(
                link.url, link.kind, record, title=link.title, archived_at=link.archived_at
            )
        )
    report.written += await archive.record(entries)
