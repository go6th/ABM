"""Stable source IDs: one code per (account, normalised URL), assigned once.

``get_or_create_source`` is the only writer of new ``sources`` rows. It is safe
under concurrent callers:

1. Fast path: look the URL up without locking. A known source only has its
   ``last_seen_run_id`` moved to the current run; its code never changes.
2. Miss: lock the account's counter row (``SELECT ... FOR UPDATE``), then look
   again. A worker that waited on the lock may find the row a concurrent
   worker just committed, and returns it instead of colliding.
3. Still missing: take ``next_value``, increment the counter, insert the source.
   Counter and source change in one transaction, so a rollback undoes both and
   codes stay gapless.

Call it inside the caller's tenant transaction (``app.tenant_id`` set). Async
callers use ``await session.run_sync(lambda s: get_or_create_source(s, ...))``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from account_intelligence.db.enums import SourceKind
from account_intelligence.db.models import AccountSourceCounter, Source
from account_intelligence.urls import normalize_url, url_hash


def format_source_code(number: int) -> str:
    """``S-0001`` style code; widens past four digits instead of wrapping."""
    if number < 1:
        raise ValueError("source code numbers start at 1")
    return f"S-{number:04d}"


def _find(session: Session, account_id: UUID, digest: str) -> Source | None:
    return session.scalars(
        select(Source).where(Source.account_id == account_id, Source.url_hash == digest)
    ).one_or_none()


def _mark_seen(session: Session, source: Source, run_id: UUID) -> Source:
    if source.last_seen_run_id != run_id:
        source.last_seen_run_id = run_id
        session.flush()
    return source


def _allocate_number(session: Session, account_id: UUID) -> int:
    """Lock the account's counter row and return the number to use next."""
    session.execute(
        insert(AccountSourceCounter)
        .values(account_id=account_id)
        .on_conflict_do_nothing(index_elements=["account_id"])
    )
    return session.scalars(
        select(AccountSourceCounter.next_value)
        .where(AccountSourceCounter.account_id == account_id)
        .with_for_update()
    ).one()


def get_or_create_source(
    session: Session,
    *,
    account_id: UUID,
    run_id: UUID,
    url: str,
    kind: SourceKind,
    title: str | None = None,
    publisher: str | None = None,
    published_at: date | None = None,
) -> Source:
    """Return the account's source for ``url``, creating it with the next code if new.

    For an existing source only ``last_seen_run_id`` changes; the metadata
    arguments apply to new sources.
    """
    normalized = normalize_url(url)
    digest = url_hash(normalized)

    existing = _find(session, account_id, digest)
    if existing is not None:
        return _mark_seen(session, existing, run_id)

    number = _allocate_number(session, account_id)
    existing = _find(session, account_id, digest)  # a concurrent worker may have won
    if existing is not None:
        return _mark_seen(session, existing, run_id)

    session.execute(
        update(AccountSourceCounter)
        .where(AccountSourceCounter.account_id == account_id)
        .values(next_value=number + 1)
    )
    source = Source(
        account_id=account_id,
        source_code=format_source_code(number),
        url=url.strip(),
        url_normalized=normalized,
        url_hash=digest,
        kind=kind.value,
        title=title,
        publisher=publisher,
        published_at=published_at,
        first_seen_run_id=run_id,
        last_seen_run_id=run_id,
    )
    session.add(source)
    session.flush()
    return source
