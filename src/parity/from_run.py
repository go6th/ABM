"""An AccountBundle from a finished run's stored content.

The run's content arrives as ``exports.content.SnapshotContent`` (the export reader's read of
the people rows, postings, detections, facts, sections, sources and tracker rows). The export
layer's one mapping (``exports.builder.map_snapshot``) turns it into the file models the legacy
builders render; this module translates those models into the bundle, so the labels, the row
order, the entity rule, the requisition parsing and the citation registry are the ones the
product already proves. Every number in ``computed`` is recomputed here from the bundle rows
(``parity.counts``, ``parity.rules``), every fixed sentence comes from ``config/parity/texts.yaml``
and ``config/export_rules.yaml`` (the workbook notes), and the pursuing client's real name is
written to ``meta.pursuing_client_internal`` and nowhere else.

Decisions of this mapping (reported in ``docs/parity/parity_spec.md``, section Wiring): the
bundle's four posting classes are the reference's, so a posting the reading left unclassified,
one that names no lane platform and one read by title only are ``User role`` (the reference's
"everything else") with the reading's label kept in the ``what`` column; a posting's last seen
date is the run date while it is open and its first seen date once closed (the pipeline stores
no last seen date); ``computed.excluded`` carries the people module's removal counts (the rows
it removed are not stored by name, except the security exclusions).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from parity import counts, rules
from parity.bundle import dump_bundle, validate_bundle
from parity.platforms import Platforms, default_platforms
from parity.tables import host_of

SCHEMA_VERSION = "1.0.0"
TEXTS_PATH = CONFIG_DIR / "parity" / "texts.yaml"
DISPLAY_WORD = "Client"
Json = dict[str, Any]
_CODE = re.compile(r"\b([SJ])-0*(\d+)\b")
_SHORT = re.compile(r"[^A-Za-z0-9 .&'-]+")
_FOUR_CLASSES = ("Build role", "Admin and owner role", "Working tool", "User role")


class BundleIncomplete(RuntimeError):
    """The run's content could not be mapped into a bundle (the export mapping refused a file;
    its codes are the message)."""


@dataclass(frozen=True)
class RunFacts:
    """What the bundle needs beside the content: the pursuing client's real name (from the run's
    input or the tenant settings; None when unknown) and the internal notes the run holds."""

    pursuing_client: str | None = None
    internal_items: tuple[tuple[str, str], ...] = ()
    internal_why: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def load_texts() -> dict[str, Any]:
    """``config/parity/texts.yaml``."""
    with open(TEXTS_PATH, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    return data


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, values: Mapping[str, Any]) -> str:
    """A template with its placeholders filled (an unknown placeholder is left as it is)."""
    return template.format_map(_Safe(values))


def code_id(code: str | None) -> str:
    """``S-0007`` as the bundle keys it: ``S7`` (``J-0012`` becomes ``J12``)."""
    return _CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}", code or "")


def cite_text(text: str) -> str:
    """Prose with every ``S-0007`` style citation rewritten to the bundle's ``S7``."""
    return code_id(text)


def cited(text: str, codes: Sequence[str]) -> str:
    """A sentence followed by its source IDs in brackets, as the Word files print them."""
    ids = [code_id(c) for c in codes if c]
    return cite_text(text) + (f" ({', '.join(ids)})" if ids else "")


def short_name(account_name: str, domain: str | None = None) -> str:
    """The file name prefix: the account name reduced to the characters the schema allows."""
    short = _SHORT.sub(" ", account_name or "").strip()
    short = re.sub(r"\s+", " ", short)
    # LOOP(leading punctuation): cap=len(short) characters; exit=first character alphanumeric
    while short and not short[0].isalnum():
        short = short[1:]
    if not short:
        short = (domain or "Account").split(".")[0] or "Account"
        short = _SHORT.sub("", short) or "Account"
    return short[:40].strip()


def long_date(d: date) -> str:
    """``2 March 2026``."""
    return f"{d.day} {d:%B} {d.year}"


def short_date(d: date) -> str:
    """``2 Mar 2026``."""
    return f"{d.day} {d:%b} {d.year}"


def _four_class(label: str) -> str:
    return label if label in _FOUR_CLASSES else "User role"


def _open(status_label: str, labels: Any) -> str:
    return "Open" if status_label == labels.posting_status.get("open") else "Closed"


def _level_number(label: str, order: Sequence[str]) -> int:
    return order.index(label) + 1 if label in order else len(rules.LEVELS)


def build_bundle(
    content: Any,
    facts: RunFacts | None = None,
    *,
    cfg: Any | None = None,
    grading: Any | None = None,
    platforms: Platforms | None = None,
) -> Json:
    """The validated bundle document of a run's content; raises ``BundleIncomplete`` when the
    export mapping could not map every file."""
    from exports.builder import map_snapshot
    from exports.config import default_export_config
    from exports.readers.common import months_before

    facts = facts or RunFacts()
    cfg = cfg or default_export_config()
    if grading is None:
        from account_intelligence.grading.config import load_grading_config

        grading = load_grading_config()
    platforms = platforms or default_platforms()
    texts = load_texts()
    mapped, ctx = map_snapshot(content, cfg, grading)
    missing = [k for k, m in mapped.items() if m.model is None]
    if missing:
        codes = sorted({v.code for v in ctx.violations}) if ctx.violations else []
        raise BundleIncomplete(",".join(codes) or ",".join(f"{k}_unmapped" for k in missing))
    summary: Any = mapped["summary"].model
    evidence: Any = mapped["evidence"].model
    people: Any = mapped["people"].model
    jobs: Any = mapped["jobs"].model
    tech: Any = mapped["tech"].model
    labels = cfg.rules.labels
    sheet = cfg.rules.sheet_texts
    as_of: date = content.as_of
    account = str(content.account_name)
    prefix = short_name(account, content.domain)
    window_from = months_before(as_of, cfg.scope.postings_window_months)
    lanes = list(platforms.lane_names)
    people_lanes = list(dict.fromkeys(labels.people_lane_label(k) for k in labels.people_lane))
    people_lanes.append(labels.lane_none)

    # ---- rows
    jobs_rows: list[Json] = []
    for r in jobs.rows:
        status = _open(r.status, labels)
        first_seen = r.posted_or_seen.isoformat() if r.posted_or_seen else ""
        jobs_rows.append(
            {
                "requisition": r.requisition or None,
                "title": r.title,
                "platforms": [p.strip() for p in r.platforms.split(",") if p.strip()],
                "cls": _four_class(r.posting_class),
                "status": status,
                "first_seen": first_seen,
                "last_seen": as_of.isoformat() if status == "Open" else first_seen,
                "location": r.location or "",
                "seniority": "",
                "entity": r.entity or account,
                "what": r.read_text
                or (r.posting_class if r.posting_class not in _FOUR_CLASSES else ""),
                "platform_sentences": r.platform_sentence[:3000],
                "description": r.description[:32000],
                "url": r.url,
            }
        )
    counted = {account}
    other_entities = [e for e in dict.fromkeys(j["entity"] for j in jobs_rows) if e not in counted]
    people_rows: list[Json] = []
    for p in people.rows:
        people_rows.append(
            {
                "name": p.name,
                "title": p.title,
                "level": _level_number(p.level, labels.people_level_order),
                "senior": bool(p.meets_seniority),
                "lane": p.lanes,
                "priority": p.priority if p.priority in ("A", "B", "C") else "C",
                "former_si": p.former_employers or "",
                "found_by": p.found_by,
                "profile_url": p.profile_url or None,
                "second_profile_url": p.second_profile_url or None,
                "location": p.location or "",
                "note": p.note or "",
                "employer_label": account,
            }
        )
    security_reasons = set(sheet.people.exclusion_reasons)
    security = [
        f"{x.name} ({x.title})" if x.title else x.name
        for x in people.excluded
        if (x.reason or "").casefold() in security_reasons
    ]
    removed = {f"{line.status}|{line.reason}": line.count for line in people.summary}

    def removed_count(*keys: str) -> int:
        return sum(removed.get(k, 0) for k in keys)

    excluded_counts = {
        "domain": removed_count("out of scope|not current"),
        "security": len(security),
        "unresolved": removed_count("no profile link yet|"),
        "below_rule_no_stack": removed_count("out of scope|below scope"),
    }
    stack_rows = [
        {
            "business_function": "",
            "category": t.category,
            "technology": t.technology,
            "postings": int(t.postings),
            "lane": t.lane,
        }
        for t in tech.rows
    ]
    in_stack_words = {labels.yes: "Yes", labels.no: "No", labels.not_pulled: "Not pulled"}
    ranking_rows = [
        {
            "platform": r.platform,
            "category": r.category,
            "lane": r.lane,
            "count": int(r.count),
            "open": int(r.open_now),
            "last_seen": r.last_seen,
            "in_stack": in_stack_words.get(r.in_stack, "Not pulled"),
            "checked": "Yes" if r.read_checked == labels.yes else "No",
        }
        for r in tech.ranking
    ]
    seen_once = [
        {"platform": r.platform, "category": r.category, "lane": r.lane} for r in tech.seen_once
    ]

    # ---- sources and postings read
    sources: dict[str, Json] = {}
    for s in evidence.sources:
        supports = s.supports_note or (s.uses[0].finding if s.uses else "")
        sources[code_id(s.code)] = {
            "name": s.title or host_of(s.url),
            "url": s.url,
            "type": s.type_label or s.kind_label,
            "date": s.published.isoformat() if s.published else labels.not_dated,
            "how": s.retrieved or "",
            "supports": supports,
        }
    jobs_read: dict[str, Json] = {}
    for j in evidence.job_descriptions:
        jobs_read[code_id(j.posting_ref or j.ref)] = {
            "name": j.title,
            "url": j.url,
            "plats": j.platforms,
            "cls": _four_class(j.posting_class),
            "what": j.sentences,
            "status": "Open" if j.status == labels.posting_status.get("open") else "Closed",
            "f": j.first_seen,
            "ent": j.entity or account,
        }

    # ---- meta and computed
    meta: Json = {
        "account_short": prefix,
        "account_legal": account,
        "domain": content.domain or "",
        "careers_site": content.careers_host,
        "run_date": long_date(as_of),
        "pull_date": long_date(as_of),
        "pull_date_short": short_date(as_of),
        "window": {"from": window_from.isoformat(), "to": as_of.isoformat()},
        "window_display": {"from": long_date(window_from), "to": long_date(as_of)},
        "pursuing_client_display": DISPLAY_WORD,
        "pursuing_client_internal": facts.pursuing_client,
        "byline": f"Research by {cfg.rules.attribution.name}.",
        "lanes": lanes,
        "people_lanes": people_lanes,
        "entities": [
            {"label": account, "counted_as_account": True},
            *[
                {"label": e, "counted_as_account": False, "phrase": f"{e} positions"}
                for e in other_entities
            ],
        ],
        "out_of_scope_platforms": list(platforms.out_of_scope),
    }
    if meta["careers_site"] is None:
        del meta["careers_site"]
    partial = {
        "meta": meta,
        "jobs_rows": jobs_rows,
        "people_rows": people_rows,
        "ranking": {"base": int(tech.base), "rows": ranking_rows, "seen_once": seen_once},
    }
    jobs_counts = {
        lane: {
            "total": jc.total,
            "open": jc.open,
            "build": jc.build,
            "admin": jc.admin,
            "tool": jc.tool,
            "user": jc.user,
        }
        for lane, jc in counts.jobs_summary(partial).items()
    }
    senior = [p for p in people_rows if p["senior"]]
    tracker_p1 = [_tracker_row(t) for t in summary.tracker if t.pass_no == 1]
    tracker_p2 = [_tracker_row(t) for t in summary.tracker if t.pass_no != 1]
    computed: Json = {
        "jobs_counts": jobs_counts,
        "ent_counts": dict(Counter(j["entity"] for j in jobs_rows)),
        "lane_rows_account": sum(
            1 for j in jobs_rows if j["entity"] in counted and set(j["platforms"]) & set(lanes)
        ),
        "lane_rows_all": sum(1 for j in jobs_rows if set(j["platforms"]) & set(lanes)),
        "people_total": len(people_rows),
        "senior": len(senior),
        "prio": dict(Counter(str(p["priority"]) for p in senior)),
        "excluded": excluded_counts,
        "tracker_tally": rules.tracker_tally(tracker_p1, tracker_p2),
    }
    values: dict[str, Any] = {
        "account": prefix,
        "legal": account,
        "domain": meta["domain"],
        "run_date": meta["run_date"],
        "pull_date": meta["pull_date"],
        "window_from": meta["window_display"]["from"],
        "window_to": meta["window_display"]["to"],
        "byline": meta["byline"],
        "people_total": computed["people_total"],
        "senior": computed["senior"],
        "prio_a": computed["prio"].get("A", 0),
        "lane_rows_account": computed["lane_rows_account"],
        "months": cfg.scope.postings_window_months,
        "start": meta["window_display"]["from"],
        "end": meta["window_display"]["to"],
        "as_of": meta["run_date"],
        "suffix": labels.entity_to_confirm_suffix.strip(),
        **{f"excluded_{k}": v for k, v in excluded_counts.items()},
    }

    # ---- prose
    signal_keys = {
        "tier1": "Tier 1",
        "tier2": "Tier 2",
        "tier3": "Tier 3",
        "context": "Context and history",
    }
    signals: dict[str, list[list[str]]] = {v: [] for v in signal_keys.values()}
    for table in summary.signals:
        signals[signal_keys[table.key]] = [
            [cite_text(r.finding), r.grade_label, r.lanes, r.date, ", ".join(map(code_id, r.codes))]
            for r in table.rows
        ]
    platform_rows = [
        [
            cite_text(r.item or t.lane),
            cite_text(r.finding),
            r.grade_label,
            cited(r.reasoning, r.codes),
        ]
        for t in summary.lanes
        for r in t.rows
    ]
    waiting = [f"{w.row}: {w.needs} ({w.status})" for w in summary.waiting] or [
        str(texts["waiting_none"])
    ]
    people_notes = [
        fill(sheet.people.what, values),
        fill(str(texts["people_text_exclusions"]), values),
        (
            f"{sheet.people.seniority_prefix}{cfg.scope.seniority_rule}"
            f" {sheet.people.seniority_filter}"
        ).strip(),
        sheet.people.judgment_note,
    ]
    jobs_notes = [fill(p, values) for p in sheet.jobs.paragraphs]
    prose: Json = {
        "glossary": fill(str(texts["glossary"]), values),
        "narrative": {
            "picture": [cited(b.text, b.codes) for b in summary.narrative],
            "stands_out": [cited(b.text, b.codes) for b in summary.stands_out],
            "lanes": [[b.lane, cited(b.text, b.codes)] for b in summary.lane_meanings],
            "wrong": [cited(b.text, b.codes) for b in summary.caveats],
            "sharpen": [cited(b.text, b.codes) for b in summary.decision_changes],
        },
        "news": [
            [n.date, cite_text(n.what), n.read, ", ".join(map(code_id, n.codes)), cite_text(n.why)]
            for n in summary.news
        ],
        "news_not_covered": summary.news_sentence or fill(str(texts["news_not_covered"]), values),
        "baseline": [
            cited(f"{b.item}: {b.what} ({b.grade_label})", b.codes) for b in summary.baseline
        ],
        "signals": signals,
        "platform": platform_rows,
        "hiring_interpretation": fill(str(texts["hiring_interpretation"]), values),
        "people_text": {
            "built": fill(str(texts["people_text_built"]), values),
            "profiles": fill(str(texts["people_text_profiles"]), values),
            "exclusions": fill(str(texts["people_text_exclusions"]), values),
        },
        "key_people": [
            [k.name, k.title, k.lanes, k.priority, k.profile_url or "", k.note or ""]
            for k in summary.key_people
        ],
        "profile_findings": [
            [f.person, cite_text(f.sentence), "Confirmed", cited(f.claim, (f.source_code,))]
            for f in summary.linkedin_findings
            if not f.withheld
        ],
        "status_changes": [
            [s.person, s.search_title, s.profile_title, code_id(s.source_code)]
            for s in summary.status_changes
        ],
        "people_interpretation": [fill(str(t), values) for t in texts["people_interpretation"]],
        "hypotheses": [[cited(h.text, h.codes), cite_text(h.question)] for h in summary.hypotheses],
        "cautions": [fill(str(t), values) for t in texts["cautions"]],
        "first_call": [cited(b.text, b.codes) for b in summary.first_call_questions],
        "waiting": waiting,
        "tracker_p1": tracker_p1,
        "tracker_p2": tracker_p2,
        "evidence": {
            "extracts": [
                {
                    "id": code_id(x.code) if x.code else "",
                    "title": x.heading,
                    "url": x.url or "",
                    "quotes": [q.text for q in x.quotes if not q.withheld],
                }
                for x in evidence.extracts
            ],
            "live_rows": [
                [r.posting, "", r.platforms, r.entity, r.read, r.posting_class]
                for r in evidence.live_postings
            ],
            "live_lane_count": len(evidence.live_postings),
            "leads": [
                [r.lead, r.queries, r.settle or r.grade_label, ", ".join(map(code_id, r.codes))]
                for r in evidence.leads
            ],
            "detections": [
                [d.technology, d.first_seen, d.last_seen, "", int(d.postings), ""]
                for d in evidence.detections
            ],
            "news_events": [
                [
                    "",
                    "Verified at source" if n.verified else "Feed item",
                    cite_text(n.sentence),
                    host_of(n.url),
                ]
                for n in evidence.news
            ],
            "board_paragraph": fill(str(texts["board_paragraph"]), values),
            "part_g": evidence.keep_copies or fill(str(texts["part_g"]), values),
            "detections_note": fill(str(texts["detections_note"]), values),
            "teams_note": evidence.teams_note or fill(str(texts["teams_note"]), values),
        },
        "workbook_notes": {
            "people": people_notes,
            "jobs": jobs_notes,
            "stack": {
                "confirmed_title": fill(sheet.tech.stack_title, values),
                "confirmed_note": fill(sheet.tech.stack_note, values),
                "teams_title": fill(sheet.tech.teams_title, values),
                "teams_note": evidence.teams_note or fill(str(texts["teams_note"]), values),
                "ranking_notes": [
                    sheet.tech.rank_rule,
                    sheet.tech.count_rule,
                    sheet.tech.signal_rule,
                    sheet.tech.research_by,
                ],
            },
        },
    }
    if facts.internal_items:
        prose["internal_only"] = {
            "title_suffix": "",
            "dates": meta["run_date"],
            "items": [[str(k), str(v)] for k, v in facts.internal_items],
            "why": list(facts.internal_why)
            or [
                "These notes come from the pursuing client's own records and are not in any"
                " client file."
            ],
            "instead": "The client facing files state only what public sources confirm.",
        }

    document: Json = {
        "schema_version": SCHEMA_VERSION,
        "meta": meta,
        "sources": sources,
        "jobs_read": jobs_read,
        "jobs_rows": jobs_rows,
        "people_rows": people_rows,
        "people_excluded": {
            "domain": [],
            "security": security,
            "unresolved": [],
            "below_rule_no_stack": [],
        },
        "overrides": [],
        "stack_rows": stack_rows,
        "team_rows": [],
        "ranking": partial["ranking"],
        "computed": computed,
        "prose": prose,
    }
    return dump_bundle(validate_bundle(document))


def _tracker_row(t: Any) -> list[str]:
    return [t.row, t.status, t.priority, t.needs, t.done, t.remains]


def apply_overrides(bundle: Json, overrides: Sequence[Mapping[str, Any]]) -> Json:
    """A new bundle document with analyst corrections applied to the people rows by name
    (priority, lane, note), the corrections recorded under ``overrides`` and every number in
    ``computed`` recomputed from the corrected rows."""
    doc = validate_bundle(bundle)
    data = dump_bundle(doc)
    by_name = {str(p["name"]).casefold(): p for p in data["people_rows"]}
    recorded = list(data.get("overrides") or [])
    for o in overrides:
        row = by_name.get(str(o.get("name", "")).casefold())
        if row is None:
            raise KeyError(str(o.get("name", "")))
        entry: Json = {"name": row["name"]}
        for key in ("priority", "lane", "note"):
            if o.get(key) is not None:
                row[key] = o[key]
                entry[key] = o[key]
        recorded.append(entry)
    data["overrides"] = recorded
    senior = [p for p in data["people_rows"] if p["senior"]]
    data["computed"]["senior"] = len(senior)
    data["computed"]["prio"] = dict(Counter(str(p["priority"]) for p in senior))
    data["computed"]["people_total"] = len(data["people_rows"])
    return dump_bundle(validate_bundle(data))
