"""Workspace grounding for chat: the held facts a turn may cite, loaded under tenant RLS.

The model sees only what this workspace holds for the accounts a conversation names. A company
the workspace does not hold is listed as such so the answer says so instead of composing a
brief from general knowledge.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.chat.service import GROUNDING_MAX_CHARS as MAX_CONTEXT_CHARS
from account_intelligence.db.session import tenant_txn

MAX_ACCOUNTS_LISTED = 50
MAX_FACTS_PER_ACCOUNT = 40

_DOMAIN = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b", re.I)


def named_domains(texts: Sequence[str]) -> list[str]:
    """Domains written in the conversation, lower-cased, in first-seen order."""
    seen: dict[str, None] = {}
    for value in texts:
        for match in _DOMAIN.findall(value):
            seen.setdefault(match.lower().removeprefix("www."), None)
    return list(seen)


def _mentions(account: Any, haystack: str, domains: Sequence[str]) -> bool:
    domain = account.domain.lower()
    if domain in domains:
        return True
    stem = domain.split(".")[0]
    if len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", haystack):
        return True
    name = (account.name or "").strip().lower()
    return len(name) >= 4 and name in haystack


async def workspace_context(
    tenant_id: UUID,
    texts: Sequence[str],
    account_id: UUID | None = None,
    *,
    txn: Any = tenant_txn,
) -> str:
    """The grounding block for one turn: held accounts, named accounts' facts, and gaps."""
    haystack = " ".join(texts).lower()
    domains = named_domains(texts)
    async with txn(tenant_id) as session:
        accounts = (
            await session.execute(
                text(
                    "SELECT id, domain, name FROM accounts WHERE tenant_id = :t"
                    " AND deleted_at IS NULL ORDER BY domain LIMIT 500"
                ),
                {"t": tenant_id},
            )
        ).all()
        matched = [a for a in accounts if a.id == account_id or _mentions(a, haystack, domains)]
        facts: dict[UUID, list[Any]] = {}
        for account in matched:
            facts[account.id] = (
                await session.execute(
                    text(
                        "SELECT f.attribute_key, f.value_text, f.value_json, f.product_grade,"
                        " f.created_at,"
                        " (SELECT e.url FROM fact_evidence e WHERE e.fact_id = f.id"
                        "   ORDER BY e.evidence_date DESC NULLS LAST LIMIT 1) AS url,"
                        " (SELECT e.evidence_date FROM fact_evidence e WHERE e.fact_id = f.id"
                        "   ORDER BY e.evidence_date DESC NULLS LAST LIMIT 1) AS evidence_date"
                        " FROM facts f WHERE f.tenant_id = :t AND f.account_id = :a"
                        " AND f.status = 'current'"
                        " ORDER BY f.attribute_key LIMIT :n"
                    ),
                    {"t": tenant_id, "a": account.id, "n": MAX_FACTS_PER_ACCOUNT},
                )
            ).all()

    held = {a.domain.lower() for a in accounts}
    lines = [f"Accounts held in this workspace ({len(accounts)}):"]
    lines += [
        f"- {a.domain}" + (f" ({a.name})" if a.name else "") for a in accounts[:MAX_ACCOUNTS_LISTED]
    ]
    if len(accounts) > MAX_ACCOUNTS_LISTED:
        lines.append(f"- and {len(accounts) - MAX_ACCOUNTS_LISTED} more")
    missing = [d for d in domains if d not in held]
    if missing:
        lines.append("")
        lines.append(
            "Named in this conversation but NOT held in this workspace: " + ", ".join(missing)
        )
    # Accounts without facts come first so a long fact list can never truncate them away.
    unresearched = [a for a in matched if not facts[a.id]]
    if unresearched:
        lines.append("")
        lines += [
            f"Held facts for {_label(a)}: none yet (no research run has completed)."
            for a in unresearched
        ]
    number = 0
    for account in matched:
        rows = facts[account.id]
        if not rows:
            continue
        lines.append("")
        lines.append(f"Held facts for {_label(account)}:")
        for row in rows:
            number += 1
            value = row.value_text or _compact(row.value_json)
            source = f" | source: {row.url}" if row.url else " | source: none recorded"
            dated = row.evidence_date or row.created_at
            when = f" | as of {dated:%Y-%m-%d}" if dated else ""
            grade = f" | grade: {row.product_grade}" if row.product_grade else ""
            lines.append(f"[F{number}] {row.attribute_key}: {value}{grade}{source}{when}")
    block = "\n".join(lines)
    if len(block) > MAX_CONTEXT_CHARS:
        block = block[:MAX_CONTEXT_CHARS].rsplit("\n", 1)[0] + "\n(further held facts omitted)"
    return block


def _label(account: Any) -> str:
    return str(account.domain) + (f" ({account.name})" if account.name else "")


def _compact(value: Any) -> str:
    rendered = str(value) if value is not None else ""
    return rendered if len(rendered) <= 400 else rendered[:400] + "…"
