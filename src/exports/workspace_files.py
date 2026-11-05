"""The workspace zip's own members, built from the run's stored rows and nothing else (master
prompt v2 sections 2 and 9.6; folders and names after the owner's reference workspace):
``RESTORE.md``, ``data/entity_lock.json``, ``data/web_searches.json``,
``data/techstack_detections.json``, ``build/urls.json``, ``notes/pull_log.md``,
``notes/pass1.md`` and ``notes/sense_check.md``.

``RESTORE.md`` states the layout (the members actually in the zip), how the files are rebuilt,
the canonical query definitions written from the run's own counts (the Platform Ranking, the Job
Postings count lines and class matrix, the People List seniority count) and the entity rules the
run applied (from its stored entity lock).

Why separate from ``workspace``: that module owns the safety of the archive (names, caps, hashes)
and knows nothing of research; these functions own what the archive says. Every figure is counted
from a stored row and every sentence comes from ``config/export_rules.yaml`` (``workspace:``),
so a source with no rows prints its one-line "none recorded" text and never a made-up row. Tool
roles replace provider names, and the cost column is called ``Cost`` because the third-party scan
refuses the word the providers use for their own unit. The entity-lock rule here mirrors
``agents.entity.accept`` over the lock's stored form; the export package may not import the
agents, so the small pure rule is restated and its test pins the two together.

Pure: no I/O, no clock. Ordering is by stored time then by position in the ledger query, so two
builds of one snapshot are byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from account_intelligence.archive_records import redact_contact
from account_intelligence.urls import strip_secrets
from datamodel.hosts import registrable
from exports.config import ExportConfig, WorkspaceRules, provider_hosts
from exports.content import FILE_KINDS, PullCall, ReleaseRow, SnapshotContent, WebHit
from exports.errors import UnsafePackage
from exports.models import FileKind, JobsModel, PeopleModel, TechModel
from exports.policy import denied_link
from jobs.window import months_before

RESTORE = "RESTORE.md"

Clean = Callable[[str], str]
_WORD = re.compile(r"[a-z0-9]+")


def _names(text: str | None, brand: str, legal_name: str = "") -> bool:
    """True when ``text`` names the brand as a whole word (case folded) or the legal name."""
    if not text:
        return False
    folded = text.lower()
    if brand and re.search(rf"(?<![a-z0-9]){re.escape(brand.lower())}(?![a-z0-9])", folded):
        return True
    legal = " ".join(_WORD.findall(legal_name.lower()))
    return bool(legal) and legal in " ".join(_WORD.findall(folded))


def text_names_account(lock: Mapping[str, Any], text: str | None) -> bool | None:
    """The text half of the lock's accept rule for a third-party page: True when the text names
    the account and does not name a blocked look-alike instead, False otherwise, None when no
    text was stored (the caller then keeps the result, since it cannot judge)."""
    if not text:
        return None
    brand, legal = str(lock.get("brand", "")), str(lock.get("legal_name", ""))
    for bad in lock.get("blocked_lookalikes") or ():
        label = str(bad.get("host", "")).split(".", 1)[0]
        if label and _names(text, label) and not _names(text, brand, legal):
            return False
    return _names(text, brand, legal)


def host_kept(lock: Mapping[str, Any] | None, hit: WebHit) -> bool:
    """Whether the entity lock keeps a search result: its host belongs to the account (an allowed
    registrable domain or a verified careers host), else it is a third-party page kept only when
    its text names the account; a look-alike host is never kept."""
    if lock is None:
        return True
    reg = registrable(hit.host)
    if reg in set(lock.get("allowed_domains") or ()) or hit.host in set(
        lock.get("careers_hosts") or ()
    ):
        return True
    if reg in {str(b.get("host", "")) for b in lock.get("blocked_lookalikes") or ()}:
        return False
    return True if hit.names_account is None else hit.names_account


def _is_tool_host(host: str, tools: tuple[str, ...]) -> bool:
    """A data tool's own site is never listed (the third-party scan refuses its host)."""
    return registrable(host) in tools or any(host == t or host.endswith("." + t) for t in tools)


def _dump(value: object, *, sort_keys: bool = True) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=sort_keys, ensure_ascii=False) + "\n").encode()


def entity_lock_member(content: SnapshotContent, rules: WorkspaceRules) -> bytes:
    """``data/entity_lock.json``: the stored lock as written, or the one-line note."""
    if content.entity_lock is None:
        return _dump({"recorded": False, "note": rules.entity_lock.none_recorded})
    return _dump(content.entity_lock)


def _web_calls(content: SnapshotContent) -> list[PullCall]:
    return [c for c in content.pull_calls if c.operation == "web_search" and c.queries]


LinkRule = Callable[[str], str | None]


def web_searches_member(
    content: SnapshotContent,
    rules: WorkspaceRules,
    clean: Clean,
    link_rule: LinkRule | None = None,
) -> bytes:
    """``data/web_searches.json``: one entry per distinct (query, tool role). A call carries up
    to five queries and stores one result list, so each of its queries shows that list. A result
    host a client file may not link (``link_rule``: another account's site, say) is not listed."""
    role = rules.pull_log.roles.get("web_search", rules.pull_log.default_role)
    lock = content.entity_lock
    tools = provider_hosts()
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for call in _web_calls(content):
        count = call.result_count if call.result_count is not None else len(call.hits)
        domains = {
            registrable(h.host)
            for h in call.hits
            if h.host
            and not _is_tool_host(h.host, tools)
            and not (link_rule and link_rule(f"https://{h.host}/"))
        }
        if count == 0 and not call.hits:
            kept, reason = False, rules.web_searches.dropped_empty
        elif call.hits and not any(host_kept(lock, h) for h in call.hits):
            kept, reason = False, rules.web_searches.dropped_entity
        else:
            kept, reason = True, None
        for q in call.queries:
            key = (q.casefold(), role)
            entry = entries.get(key)
            if entry is None:
                entries[key] = {
                    "q": clean(q),
                    "date": call.at.date().isoformat(),
                    "tool_role": role,
                    "count": count,
                    "domains": sorted(domains),
                    "kept": kept,
                    "dropped_reason": reason,
                }
                continue
            entry["domains"] = sorted({*entry["domains"], *domains})
            if kept and not entry["kept"]:
                entry["kept"], entry["dropped_reason"] = True, None
    return _dump(list(entries.values()), sort_keys=False)


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def _params(call: PullCall, clean: Clean) -> str:
    parts = []
    if call.queries:
        parts.append(", ".join(f'"{clean(q)}"' for q in call.queries))
    if call.terms:
        parts.append(", ".join(clean(t) for t in call.terms))
    if call.domain:
        parts.append(f"domain {clean(call.domain)}")
    if call.since:
        parts.append(f"since {call.since}")
    return _cell("; ".join(parts)) or "none"


def pull_log_member(
    content: SnapshotContent,
    rules: WorkspaceRules,
    filenames: Mapping[FileKind, str],
    clean: Clean,
) -> bytes:
    """``notes/pull_log.md``: the table (columns from config), repeated calls listed once with their
    count, and the completeness section with the base of every figure."""
    pl = rules.pull_log
    groups: dict[tuple[object, ...], list[PullCall]] = {}
    for call in content.pull_calls:
        key = (
            call.operation,
            tuple(sorted(q.casefold() for q in call.queries)),
            tuple(sorted(call.terms)),
            call.domain,
            call.since,
        )
        groups.setdefault(key, []).append(call)
    lines = [f"# {pl.title}", "", pl.intro, ""]
    if not groups:
        lines += [pl.none_recorded, ""]
    else:
        lines += [
            "| " + " | ".join(pl.columns) + " |",
            "| " + " | ".join("---" for _ in pl.columns) + " |",
        ]
        for calls in groups.values():
            first = calls[0]
            params = _params(first, clean)
            if len(calls) > 1:
                params += " " + pl.repeated.format(n=len(calls))
            kind = pl.outputs.get(first.operation)
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(clean(first.step or first.operation)),
                        pl.roles.get(first.operation, pl.default_role),
                        params,
                        pl.not_counted if first.result_count is None else str(first.result_count),
                        f"{sum(c.cost_usd for c in calls):.2f}",
                        _cell(filenames[kind]) if kind and kind in filenames else pl.default_output,
                    )
                )
                + " |"
            )
        lines.append("")
    sent = [q.casefold() for c in content.pull_calls for q in c.queries]
    seen = Counter(sent)
    empty = {q.casefold() for c in content.pull_calls if c.result_count == 0 for q in c.queries}
    lines += [
        f"## {pl.completeness_heading}",
        "",
        pl.calls_total.format(n=len(content.pull_calls)),
        pl.unique_queries.format(n=len(seen), total=len(sent)),
        pl.duplicate_queries.format(n=sum(1 for n in seen.values() if n > 1)),
        pl.zero_result_queries.format(n=len(empty)),
        pl.failed_calls.format(n=content.pull_failed),
        pl.lock_recorded if content.entity_lock is not None else pl.lock_missing,
        "",
    ]
    return "\n".join(lines).encode()


def sense_check_member(
    content: SnapshotContent, rules: WorkspaceRules, label_of: Callable[[str], str], clean: Clean
) -> bytes:
    """``notes/sense_check.md``: the stored counts and the facts flagged Conflicting in plain
    words, or the one line saying none was recorded."""
    sc = rules.sense_check
    if content.sense_check is None:
        return (sc.none_recorded + "\n").encode()
    lines = [
        sc.recorded.format(
            findings=content.sense_check.findings, proposals=content.sense_check.proposals
        ),
        "",
        f"## {sc.conflicts_heading}",
        "",
    ]
    conflicts = sorted(
        (f for f in content.facts if "conflicting" in f.flags and f.visibility == "public"),
        key=lambda f: (f.attribute_key, f.id),
    )
    if not conflicts:
        lines.append(sc.conflicts_none)
    for f in conflicts:
        lines.append(
            sc.conflict_line.format(
                label=_cell(clean(label_of(f.attribute_key))),
                value=_cell(clean(f.value_text)),
                grade=f.product_grade.replace("_", " ").capitalize(),
            )
        )
    return ("\n".join(lines) + "\n").encode()


def pass1_member(
    content: SnapshotContent, rules: WorkspaceRules, reason_texts: frozenset[str]
) -> bytes:
    """``notes/pass1.md``: skipped when every named tracker row carries the no-internal-access
    reason (``reason_texts`` is that reason in the tracker's client wording), read when any of
    them does not, and a plain note when the tracker holds none of the rows."""
    p1 = rules.pass1
    rows = [t for t in content.tracker if t.key in p1.row_keys]
    if not rows:
        return (p1.unknown + "\n").encode()
    if all(t.reason in reason_texts for t in rows):
        return (p1.skipped + "\n").encode()
    return (p1.read + "\n").encode()


def urls_member(content: SnapshotContent, link_rule: LinkRule | None = None) -> bytes:
    """``build/urls.json``: the source code to URL map, in code order."""

    def order(code: str) -> tuple[int, str]:
        tail = code.rsplit("-", 1)[-1]
        return (int(tail), code) if tail.isdigit() else (10**12, code)

    # a link naming a data tool is never written out (the client files withhold it too)
    return _dump(
        {
            s.code: s.url
            for s in sorted(content.sources, key=lambda s: order(s.code))
            if not denied_link(s.url) and not (link_rule and link_rule(s.url))
        }
    )


def techstack_detections_member(
    content: SnapshotContent, clean: Clean, link_rule: LinkRule | None = None
) -> bytes:
    """``data/techstack_detections.json``: the detection rows as the run stored them (technology,
    first and last seen, evidence link; a link naming a data tool is left out, as everywhere)."""

    def shown(url: str | None) -> str | None:
        ok = url and not denied_link(url) and not (link_rule and link_rule(url))
        return url if ok else None

    rows = [
        {
            "technology": clean(d.technology),
            "first_seen": d.first_seen,
            "last_seen": d.last_seen,
            "source_url": shown(d.source_url),
        }
        for d in content.detections
    ]
    return _dump({"source": "6th agents", "count": len(rows), "detections": rows}, sort_keys=False)


@dataclass(frozen=True)
class RestoreInputs:
    """What ``RESTORE.md`` is written from beyond the snapshot: the configuration (sheet names
    and headers), the file names and the mapped models whose counts it quotes."""

    cfg: ExportConfig
    filenames: Mapping[FileKind, str]
    tech: TechModel | None = None
    jobs: JobsModel | None = None
    people: PeopleModel | None = None


def _header(columns: Any, key: str) -> str | None:
    return next((c.header for c in columns if c.key == key), None)


def _queries(content: SnapshotContent, r: RestoreInputs, clean: Clean) -> list[str]:
    """The canonical query definitions, each with the figure the run's files show."""
    rs = r.cfg.rules.workspace.restore
    books = r.cfg.rules.workbooks
    names: dict[FileKind, str] = {k: _cell(r.filenames.get(k, k)) for k in FILE_KINDS}
    as_of = content.as_of.isoformat()
    start = months_before(content.as_of, r.cfg.scope.postings_window_months).isoformat()
    lines: list[str] = []
    tech = r.tech
    if tech is not None:
        count_header = _header(books.tech.ranking_columns, "count") or ""
        outside = r.cfg.rules.labels.lane_outside
        for row in (*tech.ranking, *tech.seen_once):
            if row.lane == outside:
                continue
            lines.append(
                rs.query_platform.format(
                    platform=clean(row.platform),
                    tech=names["tech"],
                    jobs=names["jobs"],
                    sheet=books.tech.ranking_sheet,
                    column=count_header,
                    start=start,
                    as_of=as_of,
                    count=row.count,
                    open=row.open_now,
                )
            )
        parts = [
            rs.rank_part.format(
                rank=letter,
                platforms=", ".join(clean(x.platform) for x in tech.ranking if x.rank == letter),
            )
            for letter, n in tech.rank_counts
            if n
        ]
        lines.append(
            rs.query_rank.format(
                tech=names["tech"],
                sheet=books.tech.ranking_sheet,
                base=tech.base,
                ranks=" ".join(parts) or rs.rank_none,
            )
        )
    jobs = r.jobs
    if jobs is not None:
        class_header = _header(books.jobs.columns, "posting_class")
        for j, label in enumerate(jobs.class_labels):
            # a class no posting carries is left out; the zero rule below covers it
            if class_header is None or not any(m.by_class[j] for m in jobs.matrix):
                continue
            counts = ", ".join(
                f"{clean(m.platform)} {m.by_class[j]}" for m in jobs.matrix if m.by_class[j]
            )
            lines.append(
                rs.query_class.format(
                    label=label,
                    jobs=names["jobs"],
                    sheet=books.jobs.sheet,
                    column=class_header,
                    counts=counts,
                )
            )
    counted: tuple[tuple[FileKind, JobsModel | TechModel | None], ...] = (
        ("jobs", jobs),
        ("tech", tech),
    )
    for kind, model in counted:
        for line in model.counts if model is not None else ():
            if line.key.startswith("file."):
                lines.append(
                    rs.query_count.format(
                        label=line.label,
                        file=names[kind],
                        base=line.base,
                        as_of=as_of,
                        value=line.value,
                    )
                )
    people = r.people
    senior_header = _header(books.people.columns, "meets_seniority")
    if people is not None and senior_header is not None:
        lines.append(
            rs.query_senior.format(
                people=names["people"],
                sheet=books.people.sheet,
                column=senior_header,
                yes=r.cfg.rules.labels.yes,
                senior=people.senior_count,
                listed=people.in_scope_listed,
            )
        )
    lines.append(rs.query_zero)
    return lines


def _entity_rules(content: SnapshotContent, r: RestoreInputs, clean: Clean) -> list[str]:
    """The entity rules the run applied, from its stored lock and the configured windows."""
    rs = r.cfg.rules.workspace.restore
    lock = content.entity_lock
    lines: list[str] = []
    if lock is None:
        lines.append(rs.entity_none)
    else:
        domains = ", ".join(clean(str(d)) for d in lock.get("allowed_domains") or ())
        hosts = ", ".join(clean(str(h)) for h in lock.get("careers_hosts") or ())
        careers = rs.entity_careers.format(hosts=hosts) if hosts else ""
        lines.append(rs.entity_domains.format(domains=domains, careers=careers))
        subs = [
            clean(str(s.get("name", "")))
            for s in lock.get("subsidiaries") or ()
            if isinstance(s, dict) and s.get("name")
        ]
        if subs:
            lines.append(rs.entity_subsidiaries.format(names=", ".join(subs)))
        blocked = lock.get("blocked_lookalikes") or ()
        if blocked:
            lines.append(rs.entity_lookalikes.format(n=len(blocked)))
    if r.cfg.scope.never_report:
        lines.append(rs.out_of_scope)
    start = months_before(content.as_of, r.cfg.scope.postings_window_months)
    lines.append(rs.window.format(start=start.isoformat()))
    return lines


_NOT_NAME = re.compile(r"[^a-z0-9]+")


def release_name(row: ReleaseRow, rules: WorkspaceRules, clean: Clean | None = None) -> str:
    """The member name of one archived copy: ``data/releases/<day>_<title words>-<hash>.md``. The
    hash is of the address, so the name of a copy never changes while it is kept and two copies
    never share one. The title words are the cleaned title's (``clean``): a data tool's name in
    a page title never reaches a member name."""
    rel = rules.releases
    title = clean(row.title) if clean is not None else row.title
    words = _NOT_NAME.sub("-", title.casefold()).strip("-")[: rel.slug_chars].strip("-")
    short = hashlib.sha256(row.url.encode()).hexdigest()[:8]
    stem = f"{row.day.isoformat()}_{words}-{short}" if words else f"{row.day.isoformat()}_{short}"
    return f"{rel.folder}/{stem}{rel.extension}"


def release_text(row: ReleaseRow, rules: WorkspaceRules, clean: Clean) -> str:
    """One archived copy as the reference prints it: ``title:``, ``url:``, ``date:`` and
    ``source:`` lines, a blank line, then the text (cut at ``max_chars``)."""
    rel = rules.releases

    def one(t: str) -> str:
        """A field on one line."""
        return " ".join(clean(t).split())

    # contact details are raw payload only (F4): the archive removes them when it stores and
    # serves a copy, and the release text is cleaned again here
    text = redact_contact(clean(row.text)).strip()
    if len(text) > rel.max_chars:
        text = text[: rel.max_chars].rstrip()
    head = (
        f"title: {one(redact_contact(row.title))}\nurl: {strip_secrets(row.url)}\n"
        f"date: {row.day.isoformat()}\nsource: {rel.source}"
    )
    return f"{head}\n\n{text}\n"


def release_members(
    content: SnapshotContent,
    rules: WorkspaceRules,
    clean: Clean,
    blocked: Callable[[str], str | None],
    scrub: Callable[[str], str] | None = None,
) -> tuple[dict[str, bytes], tuple[tuple[str, str], ...], int]:
    """``(members, left_out, scrubbed)``: the text file of each archived copy the reader selected
    (newest capture first, at most ``releases.max_files``), by member name in name order,
    ``(url, rule)`` for each copy left out because a client-output rule refused it (``blocked``
    answers the rule name, or None), and how many copies were kept scrubbed. A copy a rule
    refuses is first scrubbed (``scrub``: the refused words replaced by a marker, as the run's
    own records are) and kept when the scrubbed text scans clean; a copy still refused is left
    out. Twelve of a run's archived pages were left out whole for one word each (staging
    2026-11-04). Nothing here stops the delivery."""
    out: dict[str, bytes] = {}
    left: list[tuple[str, str]] = []
    scrubbed = 0
    for row in content.releases[: rules.releases.max_files]:
        body = release_text(row, rules, clean)
        why = blocked(body)
        if why is not None and scrub is not None:
            mended = scrub(body)
            if blocked(mended) is None:
                body, why = mended, None
                scrubbed += 1
        if why is not None:
            left.append((row.url, why))
            continue
        out[release_name(row, rules, clean)] = body.encode()
    return dict(sorted(out.items())), tuple(left), scrubbed


def restore_member(
    content: SnapshotContent,
    r: RestoreInputs,
    members: Sequence[str],
    clean: Clean,
    releases: int = 0,
) -> bytes:
    """``RESTORE.md``: layout (the members of this zip by folder), restore steps, canonical
    query definitions and entity rules (wording from ``workspace.restore``)."""
    ws = r.cfg.rules.workspace
    rs = ws.restore
    lines = [
        rs.title.format(account=clean(content.account_name)),
        "",
        rs.intro.format(as_of=content.as_of.isoformat()),
        "",
        rs.layout_heading,
    ]
    for folder in ws.folders:
        files = [n.split("/", 1)[1] for n in members if n.startswith(folder + "/")]
        if folder == "data" and releases:
            files.append(rs.releases_line.format(n=releases))
        if files:
            what = rs.folders[folder]
            lines.append(rs.folder_line.format(folder=folder, what=what, files=", ".join(files)))
    lines += [rs.manifest_line, "", rs.restore_heading, *rs.restore_steps, ""]
    lines += [rs.queries_heading, *_queries(content, r, clean), ""]
    lines += [rs.entity_heading, *_entity_rules(content, r, clean), ""]
    return "\n".join(lines).encode()


# ---- the data/ members after the owner's reference workspace (2026-11-05) -----------------------
#
# Each member carries the owner's file name and record shape, built from the run's stored rows
# and nothing else; a source with no rows prints the one-line note (``data_files.none_recorded``)
# and never an invented row. Text goes through ``clean`` (sanitised, masked) and every address
# through the link rules, as the other members do. Provider payloads (profiles, posts,
# connections) are not among them: D-25 keeps them out until D-10 is decided.


def _shown(url: str | None, link_rule: LinkRule | None) -> str | None:
    ok = url and not denied_link(url) and not (link_rule and link_rule(url))
    return url if ok else None


def _none(rules: WorkspaceRules, what: str) -> bytes:
    df = rules.data_files
    return _dump(
        {"source": df.source, "count": 0, "note": df.none_recorded.format(what=what)},
        sort_keys=False,
    )


def _day(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _text(clean: Clean, value: str | None) -> str | None:
    return clean(value) if value else value


def company_record_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/company_record.json``: the account as the run resolved it (name, domain, careers
    host, listing, the lock's legal name, home page, allowed domains and subsidiaries)."""
    lock = content.entity_lock or {}
    record = {
        "source": rules.data_files.source,
        "domain": content.domain,
        "company_name": clean(content.account_name),
        "brand": _text(clean, content.brand_name),
        "legal_name": _text(clean, lock.get("legal_name")),
        "careers_host": content.careers_host,
        "is_public": content.is_public,
        "home_url": _shown(lock.get("home_url"), link_rule),
        "research_date": content.as_of.isoformat(),
        "allowed_domains": list(lock.get("allowed_domains") or ()),
        "subsidiary_domains_reported": [
            d for d in (lock.get("allowed_domains") or ()) if d != lock.get("primary_domain")
        ],
        "subsidiaries_reported": lock.get("subsidiaries") or [],
    }
    return _dump(record, sort_keys=False)


def subsidiary_domains_member(content: SnapshotContent, rules: WorkspaceRules) -> bytes:
    """``data/subsidiary_domains.json``: the domains the lock lets count as the account beside
    the primary one, each with the careers hosts it brought."""
    lock = content.entity_lock
    if lock is None:
        return _none(rules, "subsidiary domains")
    primary = lock.get("primary_domain")
    careers = list(lock.get("careers_hosts") or ())
    domains = [
        {
            "domain": d,
            "kept": True,
            "careers_hosts": [h for h in careers if registrable(h) == registrable(d)],
        }
        for d in (lock.get("allowed_domains") or ())
        if d != primary
    ]
    return _dump(
        {
            "source": rules.data_files.source,
            "params": {"domain": primary or content.domain},
            "count": len(domains),
            "domains": domains,
        },
        sort_keys=False,
    )


def news_events_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/news_events.json``: the run's stored news events, newest first."""
    if not content.news:
        return _none(rules, "news events")
    rows = [
        {
            "headline": clean(n.headline),
            "summary": _text(clean, n.summary),
            "category": n.category,
            "found_at": _day(n.found_at),
            "effective_date": _day(n.effective_date),
            "confidence": n.confidence,
            "source_url": _shown(n.source_url, link_rule),
            "location": _text(clean, n.location),
            "article_sentence": _text(clean, n.article_sentence),
        }
        for n in content.news
    ]
    return _dump(
        {"source": rules.data_files.source, "count": len(rows), "events": rows}, sort_keys=False
    )


def _posting_record(
    p: Any, clean: Clean, link_rule: LinkRule | None, *, description: bool
) -> dict[str, Any]:
    record = {
        "id": p.posting_key,
        "title": _text(clean, p.title),
        "url": _shown(p.url, link_rule),
        "status": p.status,
        "posted_at": _day(p.posted_at),
        "first_seen": _day(p.first_seen),
        "last_seen": _day(p.last_seen),
        "location": _text(clean, p.location),
        "seniority": p.seniority,
        "platforms": list(p.platforms),
        "class": p.posting_class,
        "source": p.origin,
        "window": p.window_bucket,
    }
    if description:
        record["description"] = clean(p.description) if p.description else ""
    return record


def jobs_window_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/jobs_12mo.json``: every posting the run stored for its window, with the text it
    read (the owner's feed pull, in the product's own rows)."""
    if not content.postings:
        return _none(rules, "job postings")
    months = rules.data_files.jobs_window_months
    jobs = [
        _posting_record(p, clean, link_rule, description=True)
        for p in sorted(content.postings, key=lambda p: (str(p.first_seen or ""), p.posting_key))
    ]
    return _dump(
        {
            "source": rules.data_files.source,
            "domain": content.domain,
            "params": {
                "first_seen_from": months_before(content.as_of, months).isoformat(),
                "as_of": content.as_of.isoformat(),
                "active_only": False,
            },
            "count_saved": len(jobs),
            "jobs": jobs,
        },
        sort_keys=False,
    )


def live_postings_index_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/live_postings_index.json``: the postings seen on the live careers board, each with
    the archived copy it has under ``data/releases/``."""
    live = [p for p in content.postings if p.origin == "live_board"]
    if not live:
        return _none(rules, "live board postings")
    by_url = {r.url: release_name(r, rules, clean) for r in content.releases if r.kind == "posting"}
    rows = [
        {
            "req": p.posting_key,
            "title": _text(clean, p.title),
            "url": _shown(p.url, link_rule),
            "in_feed": False,
            "captured_file": by_url.get(p.url or ""),
        }
        for p in sorted(live, key=lambda p: p.posting_key)
    ]
    return _dump(rows, sort_keys=False)


def techstack_confirmed_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean
) -> bytes:
    """``data/techstack_confirmed.json``: the organisation's confirmed stack by business
    function, in the stored order."""
    if not content.stack:
        return _none(rules, "confirmed stack items")
    functions: dict[str, list[dict[str, Any]]] = {}
    for x in content.stack:
        functions.setdefault(clean(x.business_function), []).append(
            {
                "category": clean(x.category),
                "technology": clean(x.technology),
                "postings": x.postings,
            }
        )
    return _dump(
        {
            "source": rules.data_files.source,
            "count": len(content.stack),
            "stack": [
                {"business_function": f, "technologies": items} for f, items in functions.items()
            ],
        },
        sort_keys=False,
    )


def teams_member(content: SnapshotContent, rules: WorkspaceRules, clean: Clean) -> bytes:
    """``data/teams.json``: the platform teams of the run, most postings first."""
    if not content.teams:
        return _none(rules, "platform teams")
    rows = [
        {
            "name": clean(t.name),
            "jobs_count": t.postings,
            "lane_technologies": clean(t.lane_technologies),
            "technologies": clean(t.technologies),
            "locations": clean(t.locations),
            "job_functions": clean(t.job_functions),
            "first_activity": t.first_activity,
            "last_activity": t.last_activity,
        }
        for t in content.teams
    ]
    return _dump(
        {
            "source": rules.data_files.source,
            "total_matching": content.teams_matching,
            "since": content.teams_since.isoformat() if content.teams_since else None,
            "count": len(rows),
            "teams": rows,
        },
        sort_keys=False,
    )


def _tsv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    def cell(v: Any) -> str:
        return " ".join(str(v if v is not None else "").split())

    lines = ["\t".join(header), *("\t".join(cell(v) for v in r) for r in rows)]
    return ("\n".join(lines) + "\n").encode()


def people_grid_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/people_grid.txt``: one tab-separated line per person of the People List, in the
    list's order (the owner's grid: row id, name, title, headline, profile link, employer, lane,
    level, priority, how found)."""
    if not content.people:
        return rules.data_files.none_recorded.format(what="people").encode() + b"\n"
    header = (
        "row_id", "name", "title", "headline", "linkedin_url", "employer", "lane", "level",
        "priority", "source",
    )  # fmt: skip
    rows = [
        (
            i,
            clean(p.name),
            clean(p.title),
            _text(clean, p.profile_title) or "",
            _shown(p.profile_url, link_rule) or "",
            clean(content.account_name),
            clean(p.lanes),
            clean(p.level),
            p.priority,
            p.origin,
        )
        for i, p in enumerate(content.people, start=1)
    ]
    return _tsv(header, rows)


def releases_index_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/releases_index.json``: every archived copy under ``data/releases/`` with its
    address, title, day, kind and file name."""
    if not content.releases:
        return _none(rules, "captured pages")
    rows = [
        {
            "url": _shown(r.url, link_rule),
            "title": clean(r.title),
            "date": r.day.isoformat(),
            "kind": r.kind,
            "file": release_name(r, rules, clean),
        }
        for r in content.releases
    ]
    return _dump(rows, sort_keys=False)


def _own_hosts(content: SnapshotContent) -> set[str]:
    lock = content.entity_lock or {}
    hosts = {registrable(d) for d in (lock.get("allowed_domains") or ()) if d}
    if content.domain:
        hosts.add(registrable(content.domain))
    return {h for h in hosts if h}


def _host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split("@")[-1].split(":")[0].casefold()


def site_map_news_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/site_map_news.txt``: the account's own pages the run read (news items, pages and
    sources on the account's domains), one ``address<TAB>title`` line each, by address."""
    own = _own_hosts(content)
    seen: dict[str, str] = {}
    for r in content.releases:
        if r.kind != "posting" and registrable(_host_of(r.url)) in own:
            seen.setdefault(r.url, clean(r.title))
    for s in content.sources:
        if s.url and registrable(_host_of(s.url)) in own:
            seen.setdefault(s.url, clean(s.title or s.publisher or s.code))
    lines = [f"{u}\t{t}" for u, t in sorted(seen.items()) if _shown(u, link_rule)]
    if not lines:
        return rules.data_files.none_recorded.format(what="own-site pages").encode() + b"\n"
    return ("\n".join(lines) + "\n").encode()


def derived_postings_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/derived/postings.json``: the merged postings the Job Postings file was rendered
    from, in the owner's compact record (``t`` title, ``d`` description, ``f`` first seen, ``l``
    last seen, ``status``, ``url``, ``loc``, ``sen``, ``req``, ``src``) plus the platforms and
    class the file shows."""
    if not content.postings:
        return _none(rules, "job postings")
    rows = [
        {
            "t": _text(clean, p.title),
            "d": clean(p.description) if p.description else "",
            "f": _day(p.first_seen),
            "l": _day(p.last_seen),
            "status": p.status,
            "url": _shown(p.url, link_rule),
            "loc": _text(clean, p.location),
            "sen": p.seniority,
            "req": p.posting_key,
            "src": p.origin,
            "platforms": list(p.platforms),
            "class": p.posting_class,
        }
        for p in content.postings
    ]
    return _dump(rows, sort_keys=False)


def derived_people_member(
    content: SnapshotContent, rules: WorkspaceRules, clean: Clean, link_rule: LinkRule | None
) -> bytes:
    """``data/derived/people.json``: the people the People List was rendered from, in the
    owner's record (``rid``, ``name``, ``title``, ``level``, ``lane``, ``url``, ``src``,
    ``priority``, ``loc``, ``si``), and the exclusions the list's Summary states."""
    counts = content.people_counts
    excluded = [list(x) for x in (counts.excluded if counts else ())]
    if not content.people and not excluded:
        return _none(rules, "people")
    people = [
        {
            "rid": str(i),
            "name": clean(p.name),
            "title": clean(p.title),
            "level": clean(p.level),
            "lane": clean(p.lanes),
            "url": _shown(p.profile_url, link_rule),
            "src": p.origin,
            "priority": p.priority,
            "loc": _text(clean, p.location),
            "si": [clean(e) for e in p.former_employers] if p.former_employers else [],
            "profile_title": _text(clean, p.profile_title),
            "second_url": _shown(p.second_profile_url, link_rule),
            "role_lane": p.role_lane,
        }
        for i, p in enumerate(content.people, start=1)
    ]
    return _dump({"people": people, "excluded": excluded}, sort_keys=False)


def derived_stats_member(content: SnapshotContent, rules: WorkspaceRules) -> bytes:
    """``data/derived/stats.json``: the counts the files quote, each from a stored row set."""
    people = {
        (f"{label} ({detail})" if detail else label): value
        for label, detail, value in (content.people_counts.summary if content.people_counts else ())
    }
    jobs = {
        key: {"label": label, "value": value, "base": base}
        for key, label, value, base in (content.jobs_counts.lines if content.jobs_counts else ())
    }
    return _dump(
        {
            "source": rules.data_files.source,
            "as_of": content.as_of.isoformat(),
            "people": {"listed": len(content.people), **people},
            "jobs": {"stored": len(content.postings), **jobs},
            "detections": len(content.detections),
            "confirmed_stack_items": len(content.stack),
            "teams": len(content.teams),
            "news_events": len(content.news),
            "captured_pages": len(content.releases),
            "sources": len(content.sources),
            "facts": len(content.facts),
        },
        sort_keys=False,
    )


def captured_urls_member(
    content: SnapshotContent, rules: WorkspaceRules, link_rule: LinkRule | None
) -> bytes:
    """``data/derived/captured_urls.txt``: every captured address, one per line, in order."""
    urls = sorted({u for r in content.releases if (u := _shown(r.url, link_rule))})
    if not urls:
        return rules.data_files.none_recorded.format(what="captured addresses").encode() + b"\n"
    return ("\n".join(urls) + "\n").encode()


def readme_member(content: SnapshotContent, rules: WorkspaceRules, clean: Clean) -> bytes:
    """``notes/README.md``: what this package is and what each folder holds."""
    rd = rules.readme
    account = clean(content.account_name)
    lines = [
        rd.title.format(account=account),
        "",
        rd.intro.format(
            account=account, domain=content.domain or "", as_of=content.as_of.isoformat()
        ),
        "",
        rd.contents_heading,
        *(
            rd.folder_line.format(folder=folder, what=rules.restore.folders[folder])
            for folder in rules.folders
        ),
    ]
    return ("\n".join(lines) + "\n").encode()


def workspace_members(
    content: SnapshotContent,
    rules: WorkspaceRules,
    *,
    filenames: Mapping[FileKind, str],
    label_of: Callable[[str], str],
    clean: Clean,
    internal_reason_texts: frozenset[str],
    restore: RestoreInputs,
    releases: int = 0,
    link_rule: LinkRule | None = None,
    present: Collection[FileKind] = FILE_KINDS,
) -> dict[str, bytes]:
    """``RESTORE.md`` and every required member, in the configured order (a name the config does
    not list, or one this module does not build, is an error). ``present`` names the files the
    zip holds (a file left out of the set is not listed); ``link_rule`` keeps out of the records
    a link a client file may not carry."""
    built: dict[str, bytes] = {
        "data/company_record.json": company_record_member(content, rules, clean, link_rule),
        "data/subsidiary_domains.json": subsidiary_domains_member(content, rules),
        "data/entity_lock.json": entity_lock_member(content, rules),
        "data/news_events.json": news_events_member(content, rules, clean, link_rule),
        "data/jobs_12mo.json": jobs_window_member(content, rules, clean, link_rule),
        "data/live_postings_index.json": live_postings_index_member(
            content, rules, clean, link_rule
        ),
        "data/techstack_detections.json": techstack_detections_member(content, clean, link_rule),
        "data/techstack_confirmed.json": techstack_confirmed_member(content, rules, clean),
        "data/teams.json": teams_member(content, rules, clean),
        "data/people_grid.txt": people_grid_member(content, rules, clean, link_rule),
        "data/web_searches.json": web_searches_member(content, rules, clean, link_rule),
        "data/releases_index.json": releases_index_member(content, rules, clean, link_rule),
        "data/site_map_news.txt": site_map_news_member(content, rules, clean, link_rule),
        "data/derived/postings.json": derived_postings_member(content, rules, clean, link_rule),
        "data/derived/people.json": derived_people_member(content, rules, clean, link_rule),
        "data/derived/stats.json": derived_stats_member(content, rules),
        "data/derived/captured_urls.txt": captured_urls_member(content, rules, link_rule),
        "build/urls.json": urls_member(content, link_rule),
        "notes/README.md": readme_member(content, rules, clean),
        "notes/pull_log.md": pull_log_member(content, rules, filenames, clean),
        "notes/pass1.md": pass1_member(content, rules, internal_reason_texts),
        "notes/sense_check.md": sense_check_member(content, rules, label_of, clean),
    }
    if set(built) != set(rules.required_members):
        raise UnsafePackage("the workspace members differ from the configured list")
    # the layout lists every member the zip will hold: the five files, their render data under
    # build/ and the run records
    listed = [
        *(f"deliverables/{filenames[k]}" for k in FILE_KINDS if k in present),
        *(f"build/{k}.json" for k in FILE_KINDS if k in present),
        *rules.required_members,
    ]
    out = {RESTORE: restore_member(content, restore, listed, clean, releases)}
    out.update((name, built[name]) for name in rules.required_members)
    return out
