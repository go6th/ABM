"""The "From your mail and drive" table: the user's own matched mail threads and drive files. It is
internal (goes to ``Internal - your files/``) and every sheet is private, so the packager prints the
``Internal: do not share`` marker."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.keys import fold
from exports.repair.records import MailItem

TABLE_KEY = "mail"
TABLE_TITLE = "From your mail and drive"
INTERNAL_MARKER = "Internal: do not share"
REASON_NOT_CONNECTED = "mail and drive not connected"
REASON_NO_MATCH = "no matching mail or files found"

_THREAD = {"thread", "mail", "email", "e-mail", "message", "conversation"}
_FILE = {"file", "document", "doc", "drive", "attachment", "spreadsheet", "presentation"}
_SHORT_ALIAS = 3  # aliases this short match case-sensitively ("BD" is not "bd" in "abdomen")


def normalise_kind(kind: str) -> str:
    """``Thread`` / ``File`` from a stored kind word, else ``Not found``."""
    word = (kind or "").strip().lower()
    if word in _THREAD:
        return "Thread"
    if word in _FILE:
        return "File"
    return core.NOT_FOUND


def _names(profile: WorkspaceProfile) -> list[str]:
    seen: dict[str, str] = {}
    for name in (profile.account_name, profile.account_short, profile.label, *profile.aliases):
        text = (name or "").strip()
        if text:
            seen.setdefault(text.lower(), text)
    return list(seen.values())


def mentions_account(profile: WorkspaceProfile, text: str) -> bool:
    """Whether the account's name or an alias appears as a whole word in ``text``."""
    for name in _names(profile):
        flags = 0 if len(name) <= _SHORT_ALIAS else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text or "", flags):
            return True
    return False


def _relevance(profile: WorkspaceProfile, item: MailItem) -> str:
    given = item.relevance.strip()
    if given:
        return given
    if mentions_account(profile, f"{item.title}\n{item.summary}"):
        return "Mentions the account"
    return "Matched by search"


def _placeholder_row(title: str) -> dict[str, str]:
    row = {c: core.NOT_APPLICABLE for c in core.MAIL_COLUMNS}
    row["Title"] = title
    return row


def _merge(old: MailItem, new: MailItem) -> MailItem:
    """The first item with its empty fields filled from a duplicate."""
    return MailItem(
        title=old.title,
        kind=old.kind or new.kind,
        sender=old.sender or new.sender,
        date=old.date or new.date,
        summary=old.summary or new.summary,
        relevance=old.relevance or new.relevance,
        link=old.link or new.link,
    )


def build_mail(profile: WorkspaceProfile, items: Sequence[MailItem]) -> RepairedTable:
    """Build the mail and drive table: a placeholder row when not connected or nothing matched,
    else one row per unique (title, date, sender), newest first."""
    sheet = Sheet(TABLE_TITLE, core.MAIL_COLUMNS, private=True)
    exclusions: list[Exclusion] = []
    stats: dict[str, Any] = {"connected": profile.mail_drive_connected, "items": 0}
    notes = [INTERNAL_MARKER]

    if not profile.mail_drive_connected:
        sheet.rows.append(_placeholder_row(core.not_collected(REASON_NOT_CONNECTED)))
        stats["ignored_items"] = len(items)
        notes.append(
            "Mail and drive are not connected: Coverage items 1 and 2 are Not done. Connect them "
            "to include matching threads and files here."
        )
        return RepairedTable(TABLE_KEY, TABLE_TITLE, [sheet], exclusions, stats, notes, True)

    unique: dict[tuple[str, date | None, str], MailItem] = {}
    for item in items:
        key = (fold(item.title), item.date, fold(item.sender))
        if key in unique:
            unique[key] = _merge(unique[key], item)
            exclusions.append(
                Exclusion(
                    "mail", item.title or core.NOT_FOUND, "duplicate", "same title/date/sender"
                )
            )
        else:
            unique[key] = item

    if not unique:
        sheet.rows.append(_placeholder_row(core.not_collected(REASON_NO_MATCH)))
        notes.append("Mail and drive are connected but nothing matched the account.")
        return RepairedTable(TABLE_KEY, TABLE_TITLE, [sheet], exclusions, stats, notes, True)

    ordered = sorted(
        unique.values(),
        key=lambda i: (i.date is None, -(i.date.toordinal() if i.date else 0), fold(i.title)),
    )
    for item in ordered:
        sheet.rows.append(
            {
                "Title": " ".join(item.title.split()) or core.NOT_FOUND,
                "Kind": normalise_kind(item.kind),
                "From": item.sender.strip() or core.NOT_FOUND,
                "Date": core.iso(item.date),
                "Summary": " ".join(item.summary.split()) or core.NOT_FOUND,
                "Relevance": _relevance(profile, item),
                "Link": item.link.strip() or core.NOT_FOUND,
            }
        )
    stats["items"] = len(sheet.rows)
    return RepairedTable(TABLE_KEY, TABLE_TITLE, [sheet], exclusions, stats, notes, True)
