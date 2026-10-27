"""Build an AccountBundle from a finished workspace in the skill's layout.

The workspace holds ``RESTORE.md`` (account, dates), ``notes/pull_log_phase1.md`` (domain, the
pursuing client), ``build/`` (the scripts' intermediate files: ``content.json``,
``evidence_extra.json``, ``people_merged.json``, ``people_excluded.json``,
``jobs_lane_rows_final.json``, ``jobs_all.json``, ``jobs_bd.json``, ``stack_names.json``,
``drop_salesforce.json``, ``checked.json``, ``profile_findings.json``) and ``data/``
(``techstack_confirmed.json``, ``teams.json``). The strings the scripts hard-coded (glossary,
baseline, interpretations, workbook notes, the evidence paragraphs, the entity rules) come from
``build/prose_extra.json``, written once by hand from the scripts. The ranking is recomputed
with ``parity.rules.rank_rows``; every count in ``computed`` is recomputed from the rows.

    python -m parity.workspace <workspace_dir> <bundle_out.json>

The pursuing client's real name is read from the pull log and written to
``meta.pursuing_client_internal`` only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parity import rules
from parity.bundle import dump_bundle, validate_bundle
from parity.platforms import Platforms, default_platforms
from parity.text import clean_cell

SCHEMA_VERSION = "1.0.0"
Json = dict[str, Any]
_TITLE = re.compile(r"^#\s*(?P<short>[^(\n]+?)\s*\((?P<legal>[^)]+)\)\s*workspace", re.MULTILINE)
_DATES = re.compile(
    r"Phase 1 pulls (?P<pull>[^,]+?),\s*phase 2 analysis and deliverables (?P<run>[^.\n]+)"
)
_DOMAIN = re.compile(r"Domain:\s*(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_CLIENT = re.compile(r"Pursuing client:\s*(?P<client>.+?)(?:\.\s+Mode:|\.?\s*$)", re.MULTILINE)
_STACK_LANES = {
    "salesforce": "Salesforce",
    "mulesoft": "Salesforce (MuleSoft)",
    "servicenow": "ServiceNow",
    "sap": "SAP",
    "tableau": "Tableau and analytics",
    "veeva": "Veeva",
    "workday": "Outside the lanes (Workday)",
}


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _optional_json(path: Path) -> Any:
    return _read_json(path) if path.exists() else None


def _display_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def _short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _iso(display: str) -> str:
    return datetime.strptime(display.strip(), "%d %B %Y").date().isoformat()


def read_meta(workspace: Path, extra: Mapping[str, Any], platforms: Platforms) -> Json:
    """``meta`` from RESTORE.md, the pull log, and the ``meta`` block of prose_extra.json
    (which wins where it names a field)."""
    restore = (workspace / "RESTORE.md").read_text(encoding="utf-8")
    title = _TITLE.search(restore)
    dates = _DATES.search(restore)
    if title is None or dates is None:
        raise ValueError("RESTORE.md does not name the account and the two dates")
    pull_log = workspace / "notes" / "pull_log_phase1.md"
    log = pull_log.read_text(encoding="utf-8") if pull_log.exists() else ""
    domain = _DOMAIN.search(log)
    client = _CLIENT.search(log)
    pull_date = dates["pull"].strip()
    pull_iso = _iso(pull_date)
    window_to = date.fromisoformat(pull_iso)
    window_from = window_to.replace(year=window_to.year - 1)
    meta: Json = {
        "account_short": title["short"].strip(),
        "account_legal": title["legal"].strip(),
        "domain": domain["domain"] if domain else "",
        "run_date": dates["run"].strip(),
        "pull_date": pull_date,
        "pull_date_short": _short_date(pull_iso),
        "window": {"from": window_from.isoformat(), "to": window_to.isoformat()},
        "window_display": {
            "from": _display_date(window_from.isoformat()),
            "to": _display_date(window_to.isoformat()),
        },
        "pursuing_client_display": "Client",
        "pursuing_client_internal": client["client"].strip() if client else None,
        "byline": "Research by 6th agents.",
        "lanes": list(platforms.lane_names),
        "people_lanes": list(rules.PEOPLE_LANES),
        "entities": [{"label": title["short"].strip(), "counted_as_account": True}],
        "out_of_scope_platforms": list(platforms.out_of_scope),
    }
    meta.update(extra.get("meta", {}))
    return meta


def people_rows(merged: Sequence[Mapping[str, Any]]) -> list[Json]:
    """``people_merged.json`` rows as People sheet rows (level as an integer)."""
    out: list[Json] = []
    for v in merged:
        level_number = int(str(v["L"])[0])
        out.append(
            {
                "name": v["name"],
                "title": v["title"],
                "level": level_number,
                "senior": bool(v["senior"]),
                "lane": v["lane"],
                "priority": v["prio"],
                "former_si": v.get("si") or "",
                "found_by": v.get("src") or "",
                "profile_url": v.get("li") or None,
                "second_profile_url": None,
                "location": v.get("loc") or "",
                "note": v.get("note") or "",
                "employer_label": v.get("emp") or "",
            }
        )
    return out


def jobs_rows(rows: Sequence[Mapping[str, Any]]) -> list[Json]:
    """``jobs_lane_rows_final.json`` rows as Jobs sheet rows."""
    return [
        {
            "requisition": r.get("req") or None,
            "title": r["t"],
            "platforms": list(r["_p"]),
            "cls": r["cls"],
            "status": r["status"],
            "first_seen": r["f"],
            "last_seen": r["l"],
            "location": r.get("loc") or "",
            "seniority": r.get("sen") or "",
            "entity": r["ent"],
            "what": r.get("what") or "",
            "platform_sentences": r.get("ps") or "",
            "description": r.get("desc") or "",
            "url": r["url"],
        }
        for r in rows
    ]


def stack_rows(confirmed: Mapping[str, Any]) -> tuple[list[Json], list[str]]:
    """``techstack_confirmed.json`` as Confirmed stack rows (the reference's lane mapping) and
    the raw technology names for the ranking's In confirmed stack column."""
    out: list[Json] = []
    names: set[str] = set()
    for function, categories in confirmed.get("business_functions", {}).items():
        for category, items in categories.items():
            for item in items:
                lane = ""
                for key, label in _STACK_LANES.items():
                    if key in str(item["name"]).lower():
                        lane = label
                out.append(
                    {
                        "business_function": function,
                        "category": category,
                        "technology": clean_cell(item["name"]),
                        "postings": item.get("job_post_used_count"),
                        "lane": lane,
                    }
                )
                names.add(str(item["name"]))
    return out, sorted(names)


def team_rows(teams: Mapping[str, Any], lanes: Sequence[str]) -> list[Json]:
    """``teams.json`` as Platform teams rows (the reference's string building)."""
    slugs = [lane.lower() for lane in lanes]
    out: list[Json] = []
    for team in teams.get("teams", []):
        a = team.get("attributes") or {}
        techs = a.get("technology_list") or []
        lane_techs = ", ".join(
            x["name"] for x in techs if any(s in (x.get("slug") or "").lower() for s in slugs)
        )
        tech_str = "; ".join(f"{x['name']} ({x.get('jobs_count', '')})" for x in techs)
        locs = a.get("location_list") or []
        loc_parts = []
        for loc in locs:
            name = loc.get("name") or loc.get("city") or ""
            country = loc.get("country")
            suffix = f", {country}" if country and country not in name else ""
            loc_parts.append(f"{name}{suffix} ({loc.get('jobs_count', '')})")
        functions = a.get("job_function_list") or []
        jf_str = "; ".join(
            f"{x.get('name')} ({x.get('jobs_count', '')})" if isinstance(x, dict) else str(x)
            for x in functions
        )
        out.append(
            {
                "team": clean_cell(team["name"]),
                "lane_technologies": clean_cell(lane_techs),
                "postings": a.get("jobs_count"),
                "first_activity": str(a.get("first_activity") or "")[:10],
                "last_activity": str(a.get("last_activity") or "")[:10],
                "technologies": clean_cell(tech_str),
                "locations": clean_cell("; ".join(loc_parts)),
                "job_functions": clean_cell(jf_str),
            }
        )
    return out


def ranking_block(
    jobs: Sequence[Mapping[str, Any]],
    platforms: Platforms,
    drops: Sequence[Sequence[str]],
    checked: Sequence[str],
    stack_names: Sequence[str] | None,
) -> Json:
    """The Platform Ranking data, recomputed."""
    ranking = rules.rank_rows(
        jobs,
        platforms,
        {(str(p), str(u)) for p, u in drops},
        set(checked),
        stack_names,
    )
    return {
        "base": ranking.base,
        "rows": [r.__dict__ for r in ranking.rows],
        "seen_once": [s.__dict__ for s in ranking.seen_once],
    }


def _detection(row: Sequence[Any]) -> list[Any]:
    return ["" if x is None else x for x in row]


def build_bundle(workspace: Path, platforms: Platforms | None = None) -> Json:
    """The validated bundle document of ``workspace``."""
    platforms = platforms or default_platforms()
    build = workspace / "build"
    data = workspace / "data"
    extra = _read_json(build / "prose_extra.json")
    content = _read_json(build / "content.json")
    evidence = _read_json(build / "evidence_extra.json")
    meta = read_meta(workspace, extra, platforms)
    counted = {e["label"] for e in meta["entities"] if e["counted_as_account"]}
    lanes: list[str] = meta["lanes"]

    people = people_rows(_read_json(build / "people_merged.json"))
    excluded = _read_json(build / "people_excluded.json")
    jobs = jobs_rows(_read_json(build / "jobs_lane_rows_final.json"))
    stack, raw_stack_names = stack_rows(_read_json(data / "techstack_confirmed.json"))
    teams = team_rows(_read_json(data / "teams.json"), lanes)

    all_jobs = _optional_json(build / "jobs_all.json")
    account_jobs = _optional_json(build / "jobs_bd.json")
    if account_jobs is None and all_jobs is not None:
        account_jobs = [j for j in all_jobs if j.get("ent") in counted]
    stack_names = _optional_json(build / "stack_names.json") or raw_stack_names
    ranking = ranking_block(
        account_jobs or [],
        platforms,
        _optional_json(build / "drop_salesforce.json") or [],
        _optional_json(build / "checked.json") or [],
        stack_names,
    )

    senior = [p for p in people if p["senior"]]
    tracker_p1 = content["tracker_p1"]
    tracker_p2 = content["tracker_p2"]
    ent_counts = (
        dict(Counter(j["ent"] for j in all_jobs)) if all_jobs is not None else content["ent_counts"]
    )
    computed: Json = {
        "jobs_counts": rules.jobs_counts(jobs, lanes, counted),
        "ent_counts": ent_counts,
        "lane_rows_account": sum(1 for j in jobs if j["entity"] in counted),
        "lane_rows_all": len(jobs),
        "people_total": len(people),
        "senior": len(senior),
        "prio": dict(Counter(p["priority"] for p in senior)),
        "excluded": {k: len(v) for k, v in excluded.items()},
        "pf": _optional_json(build / "profile_findings.json") or content.get("pf") or {},
        "tracker_tally": rules.tracker_tally(tracker_p1, tracker_p2),
    }

    px = extra["prose"]
    prose: Json = {
        "glossary": px["glossary"],
        "narrative": content["narrative"],
        "news": content["news"],
        "news_not_covered": content["news_not_covered"],
        "baseline": px["baseline"],
        "signals": content["signals"],
        "platform": content["platform"],
        "hiring_interpretation": px["hiring_interpretation"],
        "people_text": {**content["people_text"], "exclusions": px["people_text_exclusions"]},
        "key_people": [[str(x or "") for x in row] for row in content["key_people"]],
        "profile_findings": content["profile_findings"],
        "status_changes": content["status_changes"],
        "people_interpretation": px["people_interpretation"],
        "hypotheses": content["hypotheses"],
        "cautions": content["cautions"],
        "first_call": content["first_call"],
        "waiting": content["waiting"],
        "tracker_p1": tracker_p1,
        "tracker_p2": tracker_p2,
        "evidence": {
            "extracts": evidence["extracts"],
            "live_rows": evidence["live_rows"],
            "live_lane_count": evidence["live_lane_count"],
            "leads": evidence["leads"],
            "detections": [_detection(r) for r in evidence["detections"]],
            "news_events": evidence["news_events"],
            **px.get("evidence", {}),
        },
        "workbook_notes": px["workbook_notes"],
    }
    if "hiring_base" in px:
        prose["hiring_base"] = px["hiring_base"]
    if px.get("lane_highlights") is not None:
        prose["lane_highlights"] = px["lane_highlights"]
    if px.get("internal_only") is not None:
        prose["internal_only"] = px["internal_only"]

    document: Json = {
        "schema_version": SCHEMA_VERSION,
        "meta": meta,
        "sources": content["sources"],
        "jobs_read": content["jobs"],
        "jobs_rows": jobs,
        "people_rows": people,
        "people_excluded": excluded,
        "stack_rows": stack,
        "team_rows": teams,
        "ranking": ranking,
        "computed": computed,
        "prose": prose,
    }
    if extra.get("overrides"):
        document["overrides"] = extra["overrides"]
    return dump_bundle(validate_bundle(document))


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry: build the bundle of a workspace and write it as JSON."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace", type=Path)
    ap.add_argument("bundle_out", type=Path)
    args = ap.parse_args(argv)
    bundle = build_bundle(args.workspace)
    args.bundle_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.bundle_out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, ensure_ascii=False)
    c = bundle["computed"]
    print(
        f"bundle written: {len(bundle['jobs_rows'])} job rows, {c['people_total']} people, "
        f"{c['senior']} senior, ranking base {bundle['ranking']['base']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
