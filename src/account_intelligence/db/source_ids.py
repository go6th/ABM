"""Stable source IDs: one code per (account, normalised URL), assigned once.

``get_or_create_source`` is the only writer of new ``sources`` rows. It is safe
under concurrent callers:

1. Fast path: look the URL up without locking. A known source only has its
   matching last-seen reference moved to the current run; its code never changes.
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

from sqlalchemy import select, text, update
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


def _archived_title(session: Session, account_id: UUID, digest: str) -> str | None:
    """The title of the page as the link archive captured it when the page was read (the newest
    capture that has one). A profile copy never lends its title: it holds a person's name and
    headline, and a source row is shown on client tables."""
    found = session.execute(
        text(
            "SELECT title FROM link_archive WHERE account_id = :a AND url_hash = :h"
            " AND kind <> 'profile' AND coalesce(title, '') <> ''"
            " ORDER BY archived_at DESC LIMIT 1"
        ),
        {"a": account_id, "h": digest},
    ).scalar_one_or_none()
    return str(found).strip()[:500] or None if found else None


def _mark_seen(
    session: Session,
    source: Source,
    *,
    run_id: UUID | None,
    account_run_id: UUID | None,
) -> Source:
    # a source stored without a title (it was registered before its page was archived, or by a
    # step that had none) takes the archived page title now; one that has a title keeps it
    if not source.title:
        title = _archived_title(session, source.account_id, source.url_hash)
        if title:
            source.title = title
    if run_id is not None:
        changed = source.last_seen_run_id != run_id or source.last_seen_account_run_id is not None
        source.last_seen_run_id = run_id
        source.last_seen_account_run_id = None
    else:
        changed = (
            source.last_seen_account_run_id != account_run_id or source.last_seen_run_id is not None
        )
        source.last_seen_run_id = None
        source.last_seen_account_run_id = account_run_id
    if changed:
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
    run_id: UUID | None = None,
    account_run_id: UUID | None = None,
    url: str,
    kind: SourceKind,
    title: str | None = None,
    publisher: str | None = None,
    published_at: date | None = None,
) -> Source:
    """Return the account's source for ``url``, creating it with the next code if new.

    Exactly one of ``run_id`` (legacy lifecycle) or ``account_run_id`` (account lifecycle) is
    required. For an existing source the matching last-seen reference changes, and a blank title is
    filled from the link archive; the metadata arguments apply to new sources (a new source with no
    title takes the archived page title).
    """
    if (run_id is None) == (account_run_id is None):
        raise ValueError("pass exactly one of run_id or account_run_id")
    normalized = normalize_url(url)
    digest = url_hash(normalized)

    existing = _find(session, account_id, digest)
    if existing is not None:
        return _mark_seen(session, existing, run_id=run_id, account_run_id=account_run_id)

    if not (title or "").strip():
        title = _archived_title(session, account_id, digest)
    number = _allocate_number(session, account_id)
    existing = _find(session, account_id, digest)  # a concurrent worker may have won
    if existing is not None:
        return _mark_seen(session, existing, run_id=run_id, account_run_id=account_run_id)

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
        first_seen_account_run_id=account_run_id,
        last_seen_account_run_id=account_run_id,
    )
    session.add(source)
    session.flush()
    return source
