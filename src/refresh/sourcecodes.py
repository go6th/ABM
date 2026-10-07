"""Stable source codes (RF3): refreshes resolve sources through the single allocator
(``account_intelligence.db.source_ids.get_or_create_source``): a source seen before keeps its
code in every later Summary and Evidence file, a new source gets the next code, and a code is
never reused for a different source. This module adds nothing but the call.

GUARANTEE(test_source_codes_are_stable_across_refresh_cycles)
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from account_intelligence.db.enums import SourceKind
from account_intelligence.db.source_ids import get_or_create_source


def resolve(session: Session, *, account_id: UUID, run_id: UUID, url: str, kind: SourceKind) -> str:
    """The source code for ``url`` on this account."""
    return get_or_create_source(
        session, account_id=account_id, run_id=run_id, url=url, kind=kind
    ).source_code
