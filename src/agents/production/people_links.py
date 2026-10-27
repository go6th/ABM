"""The people pipeline's link to the research's own findings (link archive, 2026-10-27).

* ``FactMentions`` (``people.ports.MentionsPort``): every public, current fact that names a
  person in an executive role (the reading step's ``executive_role`` findings) becomes a mention
  with the page that named them and the sentence it was quoted from, so a named executive can
  become a People List row.
* ``ArchiveResolver`` (``people.ports.MentionResolverPort``): the profile pages the research's
  own searches returned (Phase 1 archives every search result it saw, ``account_intelligence.
  archive``), matched by the person's name in the page title (``people.archive_links``). One
  free query per name: the archive does not change between attempts, so a second attempt answers
  nothing. It also gives a record the people source returned without a profile link its page
  (``people.mentions.resolve_unlinked``).

Both live in the agents layer because they read the graph's facts and the entity lock's brand
token; the people package stays free of the agents layer (import contracts).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from people.archive_links import ProfileLink, candidates_for
from people.models import Mention, PersonPage


class ArchiveResolver:
    """``MentionResolverPort`` over the link archive: the profile pages the research's own
    searches returned (Phase 1 archives every search result it saw), matched by the person's
    name in the page title (``people.archive_links``). One free query per name: the archive
    does not change between attempts, so a second attempt answers nothing."""

    def __init__(self, deps: Any, account_name: str, stopwords: tuple[str, ...] = ()) -> None:
        from agents.entity import brand_token

        self.deps = deps
        self.brand = brand_token(account_name, stopwords) if account_name else None
        self._links: list[ProfileLink] | None = None

    async def _load(self) -> list[ProfileLink]:
        from account_intelligence.archive import profile_links

        if self._links is None:
            async with self.deps.txn(self.deps.tenant_id) as s:
                found = await profile_links(s, self.deps.account_id)
            self._links = [
                ProfileLink(x.url, x.title, x.payload_ref, x.archived_at.isoformat()) for x in found
            ]
        return self._links

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        """The archived profile pages whose title names ``name`` (attempt 0 only)."""
        if attempt > 0:
            return PersonPage()
        records = candidates_for(
            name, await self._load(), account_domain=self.deps.domain, brand=self.brand
        )
        return PersonPage(records=tuple(records), next_cursor=None, call_ref=None, complete=True)


class FactMentions:
    """``MentionsPort`` over the account's graded facts: every public, current fact that names
    a person in an executive role (the reading step's ``executive_role`` findings), with the
    page that named them and the sentence it was quoted from."""

    MAX_MENTIONS = 200

    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def mentions(self) -> tuple[Mention, ...]:
        """One mention per named person (the newest fact's first evidence)."""
        from agents.people_names import person_name

        async with self.deps.txn(self.deps.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT DISTINCT ON (f.id) coalesce(f.value_text, f.value_json->>'text')"
                        " AS value, e.url, e.supporting_sentence, f.created_at"
                        " FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
                        " WHERE f.account_id = :a AND f.visibility = 'public'"
                        " AND f.superseded_at IS NULL AND f.attribute_key ~ '[.]executive_role$'"
                        " AND e.evidence_kind <> 'search_proof' AND e.url IS NOT NULL"
                        " ORDER BY f.id, e.id"
                    ),
                    {"a": self.deps.account_id},
                )
            ).all()
        out: dict[str, Mention] = {}
        for row in sorted(rows, key=lambda r: r.created_at, reverse=True):
            name = person_name(str(row.value or ""))
            if name is None or name.casefold() in out or not row.supporting_sentence:
                continue
            out[name.casefold()] = Mention(
                name=name, mention_url=row.url, sentence=str(row.supporting_sentence)[:2000]
            )
            if len(out) >= self.MAX_MENTIONS:
                break
        return tuple(out.values())
