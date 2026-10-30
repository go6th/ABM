"""The News table: one row per article or filing (key = canonical URL), the extracted facts on
their own sheet. Headlines are real titles, dates come from the source, and grades follow the
source: Confirmed only from primary sources, Probable from trade press, Lead for everything else."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.dates import date_from_text, date_from_url
from exports.repair.keys import canonical_url, fold, host_of
from exports.repair.records import ArticleRecord, FactLine
from exports.repair.sources import PRIMARY, TRADE, publisher_for, source_type_for, tier_for

CONFIRMED, PROBABLE, LEAD = core.FACT_GRADES
_RANK = {CONFIRMED: 0, PROBABLE: 1, LEAD: 2}
_PROVEN_KINDS = ("page", "url", "filing")
_OPENING_QUOTES = "\"'“‘„«"
_CLOSING_QUOTES = "\"'”’»"
_MAX_TITLE_WITHOUT_PUNCTUATION = 140
_WS = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def is_fragment_title(title: str, fact_texts: Sequence[str] = ()) -> bool:
    """True when ``title`` is an extracted fragment and not a headline: empty, starting lower-case
    mid-sentence, longer than 140 characters without closing punctuation, wholly a quotation, or
    the same text as one of the article's facts."""
    text = _squash(title)
    if not text:
        return True
    first = next((c for c in text if c.isalpha()), "")
    first_word = text.split()[0]
    if first.islower() and not any(c.isupper() for c in first_word):
        return True
    if len(text) > _MAX_TITLE_WITHOUT_PUNCTUATION and text[-1] not in ".!?" + _CLOSING_QUOTES:
        return True
    if text[0] in _OPENING_QUOTES and text[-1] in _CLOSING_QUOTES and len(text) > 2:
        return True
    return fold(text) in {fold(f) for f in fact_texts if f.strip()}


def _stored_grade(grade: str) -> str:
    word = (grade or "").strip().lower()
    return {"confirmed": CONFIRMED, "probable": PROBABLE}.get(word, LEAD)


def source_allowed_grade(tier: str) -> str:
    """The best grade a source of this tier can carry."""
    return CONFIRMED if tier == PRIMARY else PROBABLE if tier == TRADE else LEAD


def regrade(stored: str, tier: str) -> str:
    """A fact's grade after the source check: never above the stored grade or the source's tier."""
    return max(_stored_grade(stored), source_allowed_grade(tier), key=_RANK.__getitem__)


@dataclass
class _Article:
    url: str
    records: list[ArticleRecord] = field(default_factory=list)
    article_id: str = ""
    title: str = ""
    tier: str = ""
    day: date | None = None
    collected: datetime | None = None
    publisher: str = ""
    source_type: str = ""


def _best_title(records: Sequence[ArticleRecord], fact_texts: Sequence[str]) -> str:
    usable = [_squash(r.title) for r in records if not is_fragment_title(r.title, fact_texts)]
    return max(usable, key=lambda t: (len(t), t)) if usable else ""


def _published(records: Sequence[ArticleRecord], title: str) -> date | None:
    """The real publication date: stored when the page, URL or filing proves it, else read from
    the URL or the headline. A stored collection-day date never counts."""
    proven = sorted(
        (_PROVEN_KINDS.index(r.published_kind), r.published)
        for r in records
        if r.published and r.published_kind in _PROVEN_KINDS
    )
    if proven:
        return proven[0][1]
    for r in records:
        found = date_from_url(r.url)
        if found:
            return found
    for text in (title, *(r.title for r in records)):
        found = date_from_text(text)
        if found:
            return found
    return None


def _tier(records: Sequence[ArticleRecord], profile: WorkspaceProfile, url: str) -> str:
    tiers = [tier_for(url, profile, r.tier) for r in records]
    return min(tiers, key=lambda t: {PRIMARY: 0, TRADE: 1}.get(t, 2))


def build_news(profile: WorkspaceProfile, articles: Sequence[ArticleRecord]) -> RepairedTable:
    """The News table (``News`` and ``News facts``). One row per canonical URL: duplicates merge
    (best title and date, facts united). Article IDs ``A-0001``... follow the order of the rows
    (newest first, undated last, then URL). An article with no URL is dropped and logged."""
    exclusions: list[Exclusion] = []
    by_url: dict[str, _Article] = {}
    with_url = 0
    for rec in articles:
        canon = canonical_url(rec.url)
        if not canon or not host_of(canon):
            exclusions.append(
                Exclusion("news", _squash(rec.title) or "(untitled)", "no source URL")
            )
            continue
        with_url += 1
        by_url.setdefault(canon, _Article(canon)).records.append(rec)

    for canon, art in by_url.items():
        recs = art.records
        fact_texts = [_squash(f.text) for r in recs for f in r.facts]
        art.title = _best_title(recs, fact_texts)
        art.day = _published(recs, art.title)
        art.tier = _tier(recs, profile, canon)
        art.collected = min((r.collected for r in recs if r.collected), default=None)
        given_pub = next((r.publisher for r in recs if r.publisher.strip()), "")
        given_type = next((r.source_type for r in recs if r.source_type.strip()), "")
        art.publisher = publisher_for(canon, profile, given_pub)
        art.source_type = source_type_for(canon, profile, given=given_type)
    built = sorted(
        by_url.values(), key=lambda a: (a.day is None, -(a.day.toordinal()) if a.day else 0, a.url)
    )
    for index, art in enumerate(built, start=1):
        art.article_id = f"A-{index:04d}"

    fact_owner: dict[str, str] = {}
    for art in built:
        for r in art.records:
            for f in r.facts:
                if f.fact_id:
                    fact_owner.setdefault(f.fact_id, art.article_id)

    rows: list[dict[str, str]] = []
    fact_rows: list[dict[str, str]] = []
    window_from, window_to = profile.window_from, profile.window_to
    unknown_dates = outside = 0
    grades = dict.fromkeys(core.FACT_GRADES, 0)
    fact_grades = dict.fromkeys(core.FACT_GRADES, 0)
    for art in built:
        day = art.day
        article_facts = _article_facts(art, art.tier)
        best = min((f["Fact grade"] for f in article_facts), key=_RANK.__getitem__, default=LEAD)
        grades[best] += 1
        for entry in article_facts:
            fact_grades[entry["Fact grade"]] += 1
        fact_rows.extend(article_facts)
        superseders = {
            fact_owner[f.superseded_by_fact]
            for r in art.records
            for f in r.facts
            if f.superseded_by_fact in fact_owner
        } - {art.article_id}
        newer = sorted(a for a in superseders if a < art.article_id)
        if day is None:
            within = core.UNKNOWN
            unknown_dates += 1
        elif window_from <= day <= window_to:
            within = "Y"
        else:
            within = "N"
            outside += 1
        rows.append(
            {
                "Article ID": art.article_id,
                "Headline": art.title or core.NOT_FOUND,
                "Published": core.iso(day),
                "Collected": (
                    art.collected.date().isoformat()
                    if art.collected
                    else core.not_collected("collection time not recorded")
                ),
                "Within window (Y/N)": within,
                "Superseded by": newer[0] if newer else core.NOT_APPLICABLE,
                "Fact grade": best,
                "Publisher": art.publisher,
                "Source type": art.source_type,
                "Source URL": art.url,
            }
        )

    stats = {
        "articles": len(rows),
        "facts": len(fact_rows),
        "duplicates_removed": with_url - len(by_url),
        "unknown_dates": unknown_dates,
        "outside_window": outside,
        "graded": grades,
        "fact_grades": fact_grades,
        "headlines_not_found": sum(1 for r in rows if r["Headline"] == core.NOT_FOUND),
        "dropped": len(exclusions),
        "article_ids": {a.url: a.article_id for a in built},
    }
    sheets = [
        Sheet("News", core.NEWS_COLUMNS, rows),
        Sheet("News facts", core.NEWS_FACT_COLUMNS, fact_rows),
    ]
    return RepairedTable("news", "News", sheets, exclusions, stats)


def _article_facts(art: _Article, tier: str) -> list[dict[str, str]]:
    """The article's facts, de-duplicated on their text, re-graded by the source, best first."""
    merged: dict[str, tuple[str, list[FactLine]]] = {}
    for r in art.records:
        for f in r.facts:
            text = _squash(f.text)
            if text:
                merged.setdefault(fold(text), (text, []))[1].append(f)
    out: list[dict[str, str]] = []
    for key, (text, lines) in merged.items():
        grade = min((regrade(ln.grade, tier) for ln in lines), key=_RANK.__getitem__)
        quotes = sorted(q for q in (_squash(ln.quote) for ln in lines) if q)
        out.append(
            {
                "Article ID": art.article_id,
                "Fact": text,
                "Fact grade": grade,
                "Quote": quotes[0] if quotes else core.NOT_FOUND,
                "_key": key,
            }
        )
    out.sort(key=lambda row: (_RANK[row["Fact grade"]], row["_key"]))
    for row in out:
        del row["_key"]
    return out
