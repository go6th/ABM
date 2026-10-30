"""``PayloadPort`` on the platform payload store (neutral, content-addressed keys).

A source item is stored as JSON under ``tenants/<tenant>/accounts/<account>/runs/<run>/raw/
<sha256>.json.gz`` (the connector layer's key scheme: no provider name in any key). ``view``
refuses a key outside the caller's tenant prefix (KeyError), so a forged reference cannot read
another tenant's payload even in a shared bucket. With an ``archive`` the stored item is also
recorded as the archived copy of its address (``account_intelligence.archive``): the item body is
the copy, so a row that cites the page can open it once the live link dies. Private items (the
client's own mail, notes and files) are never archived for the client screens.

GUARANTEE(test_store_payloads_round_trip_and_refuse_other_tenants)
GUARANTEE(test_a_stored_link_is_archived_and_served_back)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

from account_intelligence.archive import (
    ArchiveEntry,
    LinkArchive,
    link_kind,
    on_profile_host,
    title_of,
)
from account_intelligence.archive_records import Guard, default_guard, news_record, page_record
from account_intelligence.connectors.payloads import payload_key
from account_intelligence.platform.ports import PayloadStore
from agents.ports import RunContext, SourceItem
from agents.schemas import PayloadView
from obs.logging import logger
from people.archive_links import parse_profile_title

log = logger(__name__)


@dataclass(frozen=True)
class StorePayloads:
    """See the module docstring. One instance per run (it knows the account)."""

    store: PayloadStore
    account_id: UUID
    archive: LinkArchive | None = None
    # the client-output guard every free-text field of an archived copy passes (None: the
    # platform denylist)
    guard: Guard = default_guard
    # the answer of the people data consent check per tenant, for the person-name searches
    _consent: dict[UUID, bool] = field(default_factory=dict, repr=False, compare=False)

    async def put(self, ctx: RunContext, row_key: str, item: SourceItem, visibility: str) -> str:
        """Store one item; the key is its content address (a replay stores nothing new). A
        public item is recorded as the archived copy of its address (a structured record,
        ``archive_records``)."""
        body = json.dumps(
            {
                "row_key": row_key,
                "url": item.url,
                "text": item.text,
                "visibility": "private" if visibility == "private" else "public",
                "match_kind": item.match_kind,
            },
            sort_keys=True,
        ).encode()
        key = payload_key(ctx.tenant_id, self.account_id, ctx.run_id, body)
        ref = await self.store.put(key, body)
        if (
            self.archive is not None
            and visibility != "private"
            and item.url
            and await self._archivable(ctx, row_key)
        ):
            try:
                await self.archive.record([await self._copy(item)])
            except Exception as error:  # noqa: BLE001 - an archive failure never fails the pull
                # the copy is a convenience for the client screens; the pull, the stored item
                # and the paid call stand (pipeline review 2026-10-29, finding 12)
                log.warning(
                    "archive.write_failed",
                    extra={"where": "phase1", "error": type(error).__name__},
                )
        return ref

    async def _archivable(self, ctx: RunContext, row_key: str) -> bool:
        """False for a result of a person-name search while the tenant has no valid people data
        consent: the search runs before the consent decision is taken (that decision gates only
        the reading of its pages), so nothing about a named person may be archived until it is
        (pipeline review 2026-10-29, finding 26). Every other row archives as before."""
        from agents.people_names import PERSON_ROWS

        if row_key not in PERSON_ROWS:
            return True
        if ctx.tenant_id not in self._consent:
            from agents.adapters import PgConsentStore
            from agents.config import skill_config
            from agents.guards import consent_decision

            cfg = skill_config()
            record = await PgConsentStore().current(ctx.tenant_id, cfg.consent_scope)
            ok = consent_decision(record, cfg.consent_scope, cfg.consent_text_version) is None
            self._consent[ctx.tenant_id] = ok
        return self._consent[ctx.tenant_id]

    async def _copy(self, item: SourceItem) -> ArchiveEntry:
        """The structured archived copy of a public item: a news article (``news-archive/1``)
        or any other page (``page-archive/1``), every free-text field through the output guard."""
        assert self.archive is not None
        when = self.archive.now()
        meta = dict(item.meta)
        title = meta.get("title") or title_of(item.text)
        kind = link_kind(item.url, "feed_item" if item.match_kind else "page")
        if kind != "profile" and on_profile_host(item.url) and parse_profile_title(title):
            # any other page of the profile host that reads as a person's page (a post, a
            # directory entry titled with a name) is an internal profile copy too: never served
            kind = "profile"
        if meta.get("record") == "news":
            record = news_record(
                item.url,
                title=title,
                summary=meta.get("summary"),
                article_text=item.text,
                category=meta.get("category"),
                published_date=meta.get("published"),
                captured_at=when,
                guard=self.guard,
            )
        else:
            record = page_record(
                item.url,
                item.text,
                title=title,
                captured_at=when,
                kind=kind,
                published_date=meta.get("published"),
                guard=self.guard,
            )
        return await self.archive.entry_for_record(
            item.url, kind, record, title=title, archived_at=when
        )

    async def view(self, tenant_id: UUID, ref: str) -> PayloadView:
        """The stored item as a quarantined view (KeyError outside the tenant's prefix)."""
        if not ref.startswith(f"tenants/{tenant_id}/"):
            raise KeyError("payload outside this tenant")
        data = json.loads(await self.store.get(ref))
        return PayloadView.model_validate({**data, "payload_ref": ref})
