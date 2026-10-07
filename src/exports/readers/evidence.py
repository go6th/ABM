"""Sources and Evidence mapping (pure). Runs last: it lists EVERY code the other files cite (B5),
each once, in code order, with its link, date, kind and every sentence relied on by a rendered
row. A sentence that contains a restricted term is withheld, counted and listed; the link stays
and the quote is never altered (D-24)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from account_intelligence.grading.product import display_label
from exports.citations import unknown_codes
from exports.models import EvidenceModel, EvidenceSource, EvidenceUse, JobRead, JobsModel, NewsItem
from exports.readers.common import MapContext, Mapped, digest, ref


def _code_number(code: str) -> int:
    return int(code.split("-", 1)[1])


def map_evidence(
    ctx: MapContext,
    cited_by_files: Iterable[str],
    rendered_fact_ids: frozenset[str],
    jobs: JobsModel | None,
) -> Mapped[EvidenceModel]:
    """The Evidence render model over the union of cited codes and rendered facts' sources."""
    c = ctx.content
    where = "evidence"
    facts = {f.id: f for f in c.facts if f.id in rendered_fact_ids}
    uses_by_code: dict[str, list[EvidenceUse]] = defaultdict(list)
    n_uses = 0
    for ev in sorted(c.evidence, key=lambda e: (e.fact_id, e.id)):
        fact = facts.get(ev.fact_id)
        if fact is None or ev.evidence_kind == "search_proof":
            continue
        code = ev.source_code or ctx.index.code_for(ev.url)
        if code is None:
            continue  # recorded as missing_source_code by the Summary mapper
        if not ctx.printable(ev.supporting_sentence, where, ev.id):
            continue
        n_uses += 1
        sentence, withheld = ctx.quote(ev.supporting_sentence, where)
        flags = ("aggregator",) if ev.is_aggregator else ()
        uses_by_code[code].append(
            EvidenceUse(
                row_id=ev.id,
                grade_label=display_label(
                    fact.product_grade,  # type: ignore[arg-type]
                    tuple(sorted({*fact.flags, *flags})),
                    fact.lead_status,
                ),
                sentence=sentence,
                withheld=withheld,
            )
        )
    if ctx.capped(n_uses, ctx.rules.limits.row_caps.evidence_uses, where):
        return Mapped(None)
    codes = set(cited_by_files) | set(uses_by_code)
    ctx.flag(unknown_codes(codes, ctx.index, where))
    known = sorted((x for x in codes if ctx.index.knows(x)), key=_code_number)
    labels = ctx.rules.labels.source_kind
    sources: list[EvidenceSource] = []
    for i, code in enumerate(known, start=1):
        s = ctx.index.by_code[code]
        if s.kind not in labels:
            ctx.fail("source_kind_vocabulary", where, code)
            continue
        sources.append(
            EvidenceSource(
                ref=ref("E", i),
                code=code,
                url=s.url,
                published=s.published,
                kind_label=labels[s.kind],
                uses=tuple(uses_by_code.get(code, ())),
            )
        )
    reads: list[JobRead] = []
    if jobs is not None:
        read_keys = {p.posting_key for p in c.postings if p.full_text_read}
        for j in jobs.rows:
            if j.row_id in read_keys:
                reads.append(
                    JobRead(
                        ref=ref("JD", len(reads) + 1),
                        row_id=j.row_id,
                        title=j.title,
                        url=j.url,
                        code=j.source_code,
                    )
                )
    prefixes = ctx.rules.evidence.news_attribute_prefixes
    news: list[NewsItem] = []
    for fid in sorted(facts):
        f = facts[fid]
        if not any(f.attribute_key.startswith(p) for p in prefixes):
            continue
        for ev in sorted(c.evidence, key=lambda e: e.id):
            code = ev.source_code or ctx.index.code_for(ev.url)
            if ev.fact_id != fid or not ev.url or code is None:
                continue
            sentence, _ = ctx.quote(ev.supporting_sentence, where)
            news.append(
                NewsItem(
                    ref=ref("N", len(news) + 1),
                    row_id=ev.id,
                    sentence=sentence,
                    url=ev.url,
                    code=code,
                    verified=ev.origin == "full_page_read",
                )
            )
    ctx.registry.add(
        "evidence.sources",
        len(sources),
        "sources cited by this file set",
        f"{len(sources)} sources",
    )
    ctx.registry.add(
        "evidence.withheld",
        ctx.withheld,
        "sentences relied on in this file set",
        f"{ctx.withheld} sentences withheld",
    )
    model = EvidenceModel(
        brand=ctx.rendered(c.brand_name, "branding") if c.brand_name else None,
        account=c.account_name,
        as_of=c.as_of,
        sources=tuple(sources),
        withheld=sum(1 for s in sources for u in s.uses if u.withheld),
        job_descriptions=tuple(reads),
        news=tuple(news),
        none_found=ctx.rules.none_found.text,
    )
    keyed = [(s.code, s.ref, digest(s)) for s in sources]
    keyed += [(j.row_id, j.ref, digest(j)) for j in reads]
    keyed += [(n.row_id, n.ref, digest(n)) for n in news]
    return Mapped(
        model,
        row_ids=tuple(k[0] for k in keyed),
        digests=tuple(k[2] for k in keyed),
        refs=tuple(k[1] for k in keyed),
        cited=frozenset(s.code for s in sources),
    )
