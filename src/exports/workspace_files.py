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
) -> tuple[dict[str, bytes], tuple[tuple[str, str], ...]]:
    """``(members, left_out)``: the text file of each archived copy the reader selected (newest
    capture first, at most ``releases.max_files``), by member name in name order, and ``(url,
    rule)`` for each copy left out because a client-output rule refused it (``blocked`` answers
    the rule name, or None). A copy the rules refuse is never printed, and never stops the
    delivery."""
    out: dict[str, bytes] = {}
    left: list[tuple[str, str]] = []
    for row in content.releases[: rules.releases.max_files]:
        body = release_text(row, rules, clean)
        why = blocked(body)
        if why is not None:
            left.append((row.url, why))
            continue
        out[release_name(row, rules, clean)] = body.encode()
    return dict(sorted(out.items())), tuple(left)


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
        "data/entity_lock.json": entity_lock_member(content, rules),
        "data/web_searches.json": web_searches_member(content, rules, clean, link_rule),
        "data/techstack_detections.json": techstack_detections_member(content, clean, link_rule),
        "build/urls.json": urls_member(content, link_rule),
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
