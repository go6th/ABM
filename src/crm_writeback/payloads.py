"""What may be written (CW4, CW5): only public-evidence content from delivered, gate-passed
output; never Internal Only, sensitive findings, provider names, credit counts or cost. Payloads
are rendered by template, size-limited, wording-linted and client-scanned; a failure blocks the
write. Tasks are to-dos with preparation wording; nothing here sends anything.

GUARANTEE(test_crm_payloads_are_whitelisted_and_scanned)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from account_intelligence.safety.client_output import scan_text
from alerts import lint, sensitive
from crm_writeback.client import Draft, Kind


class PayloadRefused(ValueError):
    """A payload that must not be written; fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Item:
    """One public, delivered finding."""

    text: str
    source_code: str
    visibility: str
    event_type: str
    gate_passed: bool


def content_hash(kind: Kind, account: str, title: str, body: str) -> str:
    """The idempotency part of a write key."""
    return hashlib.sha256(f"{kind}\x00{account}\x00{title}\x00{body}".encode()).hexdigest()


def render(
    kind: Kind,
    *,
    account_record_id: str,
    account_label: str,
    items: list[Item],
    marker_prefix: str,
    title_chars: int,
    body_chars: int,
) -> Draft:
    """A draft, or PayloadRefused."""
    if not items:
        raise PayloadRefused("nothing_to_write")
    for it in items:
        if it.visibility != "public":
            raise PayloadRefused("not_public")
        if not it.gate_passed:
            raise PayloadRefused("not_delivered")
        if sensitive.category(it.event_type) is not None:
            raise PayloadRefused("sensitive_finding")
    if kind == "task":
        title = f"Review account research for {account_label}"
    elif kind == "note":
        title = f"Account research notes: {account_label}"
    else:
        title = f"Account snapshot: {account_label}"
    lines = [f"- {it.text} [{it.source_code}]" for it in items]
    if kind == "task":
        lines.insert(0, "Preparation: review these findings before your next planned contact.")
    body = "\n".join(lines)
    if len(title) > title_chars or len(body) > body_chars:
        raise PayloadRefused("too_long")
    try:
        lint.check(title)
        lint.check(body)
    except lint.WordingRefused:
        raise PayloadRefused("outreach_wording") from None
    if scan_text(title) or scan_text(body):
        raise PayloadRefused("client_output_scan")
    marker = marker_prefix + content_hash(kind, account_record_id, title, body)[:32]
    return Draft(kind, account_record_id, title, f"{body}\n\n{marker}", marker)
