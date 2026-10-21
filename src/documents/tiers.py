"""Platform role tiers counted in code (owner request 2026-10-20): the ``count_tiers`` tool.

"How many Salesforce build roles?" was answered by the model filtering and counting rows itself,
and the count moved between runs. Here the code counts, from the STORED tier, never from the
posting text:

* the tier of a row for a platform is its ``<platform>_role_tier`` cell when the table has that
  column, else its single ``class`` cell, mapped through ``config/platform_tiers.yaml`` onto the
  four tiers (Platform role, Platform adjacent, Platform user, Tools list only); a stored label
  that is no tier (False positive, Competitor or prior experience ...) is ``Not counted (R1)``,
  a missing one ``Unclassified``. One class serves every platform the row names (R3);
* R6 first: closed postings and postings first seen before ``since`` leave the counts (their
  numbers are reported); R5 next: one row per business key, the earliest posting kept
  (``tables._dedupe`` with its keyless re-scrape merging);
* the model may classify Unclassified rows once and call again with ``classified_now``; a row
  with a stored tier is never overridden (the refusal is in the result);
* rows whose stored tier the evidence contradicts are DISPUTED, never recounted: R2 (named only
  in an example or skills list), R4 (a non-technical title with no platform work), R1 (stored
  from a trap phrase such as "self-service cloud"); detection errors (R1 traps, product names
  the platforms column missed, R2 lists stored above Tools list only) carry a proposed rule.

Every count names its base, and the tiers plus Not counted plus Unclassified reconcile to the
rows counted for that platform.

GUARANTEE(test_count_tiers_counts_stored_tiers_and_reconciles)
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.rag.retrieval import typed
from documents import repo
from documents.tables import QueryError, TableInfo, _dedupe

TIER_MAX_ROWS = 20_000
DISPUTED_MAX = 60
UNCLASSIFIED_MAX = 50
EXAMPLES_MAX = 8
PHRASE_CHARS = 220
ALL = "all"


@dataclass(frozen=True)
class TierConfig:
    """``config/platform_tiers.yaml`` as the code reads it."""

    tiers: tuple[str, ...]
    not_counted: str
    unclassified: str
    class_map: dict[str, str]
    platforms: dict[str, tuple[str, ...]]
    case_sensitive: frozenset[str]
    traps: dict[str, str]  # phrase -> the platform it imitates
    example_markers: tuple[str, ...]
    list_markers: frozenset[str]
    build_verbs: tuple[str, ...]
    non_technical_titles: tuple[str, ...]
    columns: dict[str, tuple[str, ...]]


@lru_cache(maxsize=1)
def config() -> TierConfig:
    """The tier configuration (read once)."""
    raw = yaml.safe_load((CONFIG_DIR / "platform_tiers.yaml").read_text(encoding="utf-8"))
    return TierConfig(
        tiers=tuple(raw["tiers"]),
        not_counted=str(raw["not_counted"]),
        unclassified=str(raw["unclassified"]),
        class_map={str(k).casefold(): str(v) for k, v in raw["class_map"].items()},
        platforms={str(k): tuple(str(t) for t in v) for k, v in raw["platforms"].items()},
        case_sensitive=frozenset(str(t) for t in raw.get("case_sensitive") or ()),
        traps={str(k): str(v) for k, v in (raw.get("traps") or {}).items()},
        example_markers=tuple(str(t) for t in raw.get("example_markers") or ()),
        list_markers=frozenset(str(t) for t in raw.get("list_markers") or ()),
        build_verbs=tuple(str(t) for t in raw.get("build_verbs") or ()),
        non_technical_titles=tuple(str(t) for t in raw.get("non_technical_titles") or ()),
        columns={str(k): tuple(str(c) for c in v) for k, v in raw["columns"].items()},
    )


def _tool() -> dict[str, Any]:
    cfg = config()
    return {
        "name": "count_tiers",
        "description": (
            "Count job postings by platform role tier (Platform role, Platform adjacent,"
            " Platform user, Tools list only) from the STORED tier or class column, per"
            " platform, with closed and out-of-window postings excluded and duplicate"
            " requisitions counted once. Also returns disputed rows (stored tier contradicted"
            " by the evidence, with the rule) and detection errors. Use it for any count of"
            " build roles, platform roles or tiers; never count tiers with query_table."
        ),
        "input_schema": {
            "type": "object",
            "required": ["table"],
            "additionalProperties": False,
            "properties": {
                "table": {"type": "string"},
                "platforms": {
                    "type": "array",
                    "items": {"enum": [*cfg.platforms, ALL]},
                    "description": "platforms to count; omit or ['all'] for every platform",
                },
                "since": {
                    "type": "string",
                    "description": "run window start (YYYY-MM-DD); older postings excluded",
                },
                "classified_now": {
                    "type": "array",
                    "description": (
                        "your one-time tier for rows the result listed as unclassified_rows"
                    ),
                    "items": {
                        "type": "object",
                        "required": ["row", "platform", "tier"],
                        "additionalProperties": False,
                        "properties": {
                            "row": {"type": "integer", "minimum": 1},
                            "platform": {"enum": list(cfg.platforms)},
                            "tier": {"enum": list(cfg.tiers)},
                        },
                    },
                },
            },
        },
    }


COUNT_TIERS_TOOL: dict[str, Any] = _tool()


# ---- matching (R1, R2) ---------------------------------------------------------------------


@lru_cache(maxsize=64)
def _term_re(term: str, case_sensitive: bool) -> re.Pattern[str]:
    return re.compile(
        r"(?<![\w/])" + re.escape(term) + r"(?![\w])", 0 if case_sensitive else re.IGNORECASE
    )


@lru_cache(maxsize=1)
def _trap_res() -> tuple[tuple[str, str, re.Pattern[str]], ...]:
    return tuple(
        (t, platform, re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE))
        for t, platform in config().traps.items()
    )


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def detrap(text: str) -> tuple[str, dict[str, str]]:
    """The text with every R1 trap phrase blanked (positions kept), and the traps found with
    the platform each imitates."""
    found: dict[str, str] = {}
    for name, platform, pattern in _trap_res():
        if pattern.search(text):
            found[name] = platform
            text = _blank(text, pattern)
    return text, found


def matches(text: str, platform: str) -> list[tuple[int, int, str]]:
    """The whole-name product matches of ``platform`` in ``text`` (trap phrases already
    blanked by the caller): (start, end, term)."""
    cfg = config()
    out: list[tuple[int, int, str]] = []
    for term in cfg.platforms.get(platform, ()):
        for m in _term_re(term, term in cfg.case_sensitive).finditer(text):
            out.append((m.start(), m.end(), term))
    return sorted(out)


_SENTENCE = re.compile(r"(?<=[.!?])(?<![eE]\.[gG]\.)(?<![iI]\.[eE]\.)\s+(?=[A-Z(+•-])|\s*[|\n•]\s*")


def sentences(text: str) -> list[str]:
    """Evidence split into sentences (``|`` and bullets separate the workbook's sentences;
    the period of "e.g." never ends one)."""
    return [s.strip() for s in _SENTENCE.split(text or "") if s and s.strip()]


_QUALIFIER = re.compile(
    r"\b(experience (?:with|in)|familiarity with|knowledge of|proficiency (?:with|in)|"
    r"exposure to|tools?|preferred|nice to have|a plus|bonus)\b",
    re.IGNORECASE,
)


def in_example_list(sentence: str, start: int, end: int) -> str | None:
    """Why the name at ``start:end`` sits in an example or skills list (R2), or None: after an
    example marker in the same sentence (a list marker such as "including" only when a list
    follows it), next to a slash, or one of three or more comma separated names in a
    qualifications sentence."""
    cfg = config()
    before = sentence[:start].casefold()
    for marker in cfg.example_markers:
        found = list(re.finditer(r"(?<!\w)" + re.escape(marker.casefold()), before))
        if not found:
            continue
        if marker in cfg.list_markers and not re.search(
            r",|/|\bor\b|\band\b", sentence[found[-1].end() :]
        ):
            continue
        return f"after '{marker}'"
    if re.match(r"\s*/", sentence[end:]) or re.search(r"/\s*$", sentence[:start]):
        return "in a slash list"
    if sentence.count(",") >= 2 and _QUALIFIER.search(sentence):
        left = sentence.rfind(",", 0, start)
        right = sentence.find(",", end)
        if left != -1 or right != -1:
            return "in a skills list"
    return None


def names_work(sentence: str) -> str | None:
    """The build verb that makes the sentence a responsibility of platform work (R1/R4)."""
    folded = sentence.casefold()
    return next((v for v in config().build_verbs if re.search(r"\b" + re.escape(v), folded)), None)


# ---- the count ----------------------------------------------------------------------------


@dataclass
class _Columns:
    platforms: str | None
    klass: str | None
    title: str | None
    status: str | None
    date: str | None
    evidence: tuple[str, ...]
    tier_columns: dict[str, str]  # platform -> its stored role-tier column


def _columns(table: TableInfo) -> _Columns:
    cfg = config()
    names = table.names

    def first(kind: str) -> str | None:
        return next((c for c in cfg.columns.get(kind, ()) if c in names), None)

    tier_columns: dict[str, str] = {}
    for platform in cfg.platforms:
        slug = re.sub(r"\s+", "_", platform.casefold())
        for name in (f"{slug}_role_tier", f"{slug}_tier"):
            if name in names:
                tier_columns[platform] = name
                break
    return _Columns(
        platforms=first("platforms"),
        klass=first("class"),
        title=first("title"),
        status=first("status"),
        date=first("date"),
        evidence=tuple(c for c in cfg.columns.get("evidence", ()) if c in names)[:1]
        or tuple(c.name for c in table.columns if c.payload)[:1],
        tier_columns=tier_columns,
    )


def map_label(label: Any) -> str:
    """A stored class or tier label as its tier (``Unclassified`` when empty or unknown)."""
    cfg = config()
    text = " ".join(str(label or "").split()).casefold()
    if not text:
        return cfg.unclassified
    if text in cfg.class_map:
        return cfg.class_map[text]
    tiers = {t.casefold(): t for t in (*cfg.tiers, cfg.not_counted)}
    return tiers.get(text, cfg.unclassified)


def _date(value: Any) -> date | None:
    got = typed(value)
    return got if isinstance(got, date) else None


def _parse(
    table: TableInfo, payload: Mapping[str, Any]
) -> tuple[list[str], date | None, dict[tuple[int, str], str]]:
    cfg = config()
    wanted = payload.get("platforms") or [ALL]
    if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
        raise QueryError("bad_platforms", "platforms must be a list of platform names")
    unknown = [p for p in wanted if p != ALL and p not in cfg.platforms]
    if unknown:
        raise QueryError(
            "unknown_platform",
            f"unknown platform {unknown[0]!r}",
            platforms=[*cfg.platforms, ALL],
        )
    platforms = list(cfg.platforms) if ALL in wanted else list(dict.fromkeys(wanted))
    since: date | None = None
    if payload.get("since"):
        since = _date(str(payload["since"]))
        if since is None:
            raise QueryError("bad_since", "since must be a date, YYYY-MM-DD")
    overrides: dict[tuple[int, str], str] = {}
    for item in payload.get("classified_now") or []:
        if not isinstance(item, Mapping):
            raise QueryError("bad_classified_now", "each item needs row, platform and tier")
        row, platform, tier = item.get("row"), item.get("platform"), item.get("tier")
        if (
            not isinstance(row, int)
            or isinstance(row, bool)
            or platform not in cfg.platforms
            or tier not in cfg.tiers
        ):
            raise QueryError(
                "bad_classified_now",
                "each item needs a row number, a platform and one of the four tiers",
                tiers=list(cfg.tiers),
            )
        overrides[(row, str(platform))] = str(tier)
    return platforms, since, overrides


def _phrase(sentence: str) -> str:
    text = " ".join(sentence.split())
    return text if len(text) <= PHRASE_CHARS else text[: PHRASE_CHARS - 1] + "…"


async def count(session: Any, table: TableInfo, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``count_tiers`` result for one described table (see the module docstring)."""
    cfg = config()
    platforms, since, overrides = _parse(table, payload)
    cols = _columns(table)
    if cols.platforms is None and not cols.tier_columns:
        raise QueryError(
            "no_platform_column",
            "this table has no platforms column and no '<Platform> role tier' column",
            columns=[c.name for c in table.columns],
        )
    found = await repo.table_rows(session, table.id, "", {}, TIER_MAX_ROWS + 1)
    if len(found) > TIER_MAX_ROWS:
        raise QueryError("table_too_large", f"more than {TIER_MAX_ROWS} rows")
    rows: list[dict[str, Any]] = [
        {"row": int(r.row_no), "cells": dict(r.cells or {})} for r in found
    ]

    # R6: closed and out-of-window postings leave the counts (kept in the sheet)
    closed = window = 0
    live: list[dict[str, Any]] = []
    for row in rows:
        cells = row["cells"]
        if cols.status and str(cells.get(cols.status) or "").strip().casefold().startswith(
            "closed"
        ):
            closed += 1
            continue
        posted = _date(cells.get(cols.date)) if cols.date else None
        if since is not None and posted is not None and posted < since:
            window += 1
            continue
        live.append(row)

    # R5: one row per requisition, the earliest posting kept
    def earliest(row: dict[str, Any], held: dict[str, Any]) -> bool:
        """Whether ``row`` was posted before ``held`` (dated first, then sheet order)."""
        a = _date(row["cells"].get(cols.date)) if cols.date else None
        b = _date(held["cells"].get(cols.date)) if cols.date else None
        if a is not None and b is not None and a != b:
            return a < b
        if (a is None) != (b is None):
            return a is not None
        return bool(row["row"] < held["row"])

    removed = 0
    if table.business_key:
        live, removed = _dedupe(live, table.business_key, earliest)

    tally: dict[str, Counter[str]] = {p: Counter() for p in platforms}
    classified_now: Counter[str] = Counter()
    refused: list[dict[str, Any]] = []
    unmapped: Counter[str] = Counter()
    disputed: list[dict[str, Any]] = []
    unclassified_rows: list[dict[str, Any]] = []
    errors: dict[tuple[str, str, str], dict[str, Any]] = {}

    def error(rule: str, platform: str, kind: str, row: dict[str, Any], proposal: str) -> None:
        """Count one rule error against its rule, platform and kind, keeping the row."""
        item = errors.setdefault(
            (rule, platform, kind),
            {"rule": rule, "platform": platform, "kind": kind, "count": 0, "rows": []},
        )
        item["count"] += 1
        item["proposed_rule"] = proposal
        if len(item["rows"]) < EXAMPLES_MAX:
            item["rows"].append(row["row"])

    for row in live:
        cells = row["cells"]
        job_id = cells.get(table.business_key) if table.business_key else None
        title = str(cells.get(cols.title) or "") if cols.title else ""
        named_raw = str(cells.get(cols.platforms) or "") if cols.platforms else ""
        named, named_traps = detrap(named_raw)
        evidence_raw = " | ".join(str(cells.get(c) or "") for c in cols.evidence).strip(" |")
        evidence, evidence_traps = detrap(evidence_raw)
        said = sentences(evidence)
        label = cells.get(cols.klass) if cols.klass else None
        for platform in platforms:
            tier_col = cols.tier_columns.get(platform)
            stored_label = cells.get(tier_col) if tier_col else label
            in_scope = bool(matches(named, platform)) or bool(
                tier_col and str(cells.get(tier_col) or "").strip()
            )
            hits = [(s, m) for s in said for m in matches(s, platform)]
            if not in_scope:
                # R1 miss: the product is named in the evidence, the platforms column misses it
                if hits and cols.platforms:
                    term = hits[0][1][2]
                    error(
                        "R1",
                        platform,
                        "missed_name",
                        row,
                        f"R1: '{cols.platforms}' must list {platform} when the evidence names"
                        f" '{term}' as a whole word; re-run platform detection on these rows",
                    )
                continue
            tier = map_label(stored_label)
            label_text = " ".join(str(stored_label or "").split())
            if (
                tier == cfg.unclassified
                and label_text
                and label_text.casefold() not in cfg.class_map
            ):
                unmapped[label_text] += 1
            key = (row["row"], platform)
            if key in overrides:
                if tier == cfg.unclassified:
                    tier = overrides[key]
                    classified_now[platform] += 1
                else:
                    refused.append({"row": row["row"], "platform": platform, "stored_tier": tier})
            tally[platform][tier] += 1
            base = {
                "row": row["row"],
                "job_id": job_id,
                "title": title,
                "platform": platform,
                "stored_tier": tier,
                "stored_label": None if stored_label is None else str(stored_label),
            }
            if tier == cfg.unclassified:
                if len(unclassified_rows) < UNCLASSIFIED_MAX:
                    unclassified_rows.append(
                        {**base, "phrase": _phrase(hits[0][0]) if hits else None}
                    )
                continue
            # R1: the stored platform rests on a trap phrase only
            traps = [t for t, p in {**named_traps, **evidence_traps}.items() if p == platform]
            if not hits and traps and tier != cfg.not_counted:
                trap = traps[0]
                disputed.append(
                    {
                        **base,
                        "suggested_tier": cfg.not_counted,
                        "rule": "R1",
                        "reason": f"the only mention is the phrase '{trap}', not the product",
                        "phrase": None,
                    }
                )
                error(
                    "R1",
                    platform,
                    "trap",
                    row,
                    f"R1: never read '{trap}' as {platform}",
                )
                continue
            if not hits or tier == cfg.not_counted:
                continue
            listed = [(s, m, in_example_list(s, m[0], m[1])) for s, m in hits]
            outside = [(s, m) for s, m, why in listed if why is None]
            work = next(((s, v) for s, _ in outside if (v := names_work(s))), None)
            if tier in cfg.tiers[:3] and not outside:
                s, m, why = listed[0]
                disputed.append(
                    {
                        **base,
                        "suggested_tier": cfg.tiers[3],
                        "rule": "R2",
                        "reason": f"{platform} is named only {why}",
                        "phrase": _phrase(s),
                    }
                )
                error(
                    "R2",
                    platform,
                    "list_stored_higher",
                    row,
                    "R2: a name found only in an example or skills list ('such as', 'e.g.',"
                    " 'including' a list, a slash or comma list of tools) is Tools list only,"
                    " whatever the posting's class",
                )
                continue
            technical = not any(
                re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", title.casefold())
                for w in cfg.non_technical_titles
            )
            if tier == cfg.tiers[0] and not technical and work is None:
                s = (outside or hits)[0][0]
                disputed.append(
                    {
                        **base,
                        "suggested_tier": cfg.tiers[2],
                        "rule": "R4",
                        "reason": "non-technical title and no responsibility names platform work",
                        "phrase": _phrase(s),
                    }
                )
                continue
            if tier == cfg.tiers[3] and work is not None and technical:
                s, verb = work
                disputed.append(
                    {
                        **base,
                        "suggested_tier": cfg.tiers[0],
                        "rule": "R2",
                        "reason": f"named outside any list, as the object of '{verb}'",
                        "phrase": _phrase(s),
                    }
                )

    counted = []
    for platform in platforms:
        t = tally[platform]
        entry: dict[str, Any] = {
            "platform": platform,
            **{tier: t[tier] for tier in cfg.tiers},
            cfg.not_counted: t[cfg.not_counted],
            cfg.unclassified: t[cfg.unclassified],
            "total_rows": sum(t.values()),
            "build_roles": t[cfg.tiers[0]],
            "build_roles_with_adjacent": t[cfg.tiers[0]] + t[cfg.tiers[1]],
            "classified_now": classified_now[platform],
            "tier_source": (
                f"stored column '{cols.tier_columns[platform]}'"
                if platform in cols.tier_columns
                else f"stored column '{cols.klass}' (one class per row, R3)"
                if cols.klass
                else "none stored"
            ),
        }
        counted.append(entry)
    out: dict[str, Any] = {
        "table": table.name,
        "base": {
            "rows_in_table": len(rows),
            "excluded_closed": closed,
            "excluded_out_of_window": window,
            "duplicates_removed": removed,
            "rows_counted": len(live),
            "deduped_on": table.business_key,
            "since": since.isoformat() if since else None,
        },
        "columns": {
            "platforms": cols.platforms,
            "class": cols.klass,
            "evidence": list(cols.evidence),
        },
        "counts": counted,
        "disputed_rows": disputed[:DISPUTED_MAX],
        "disputed_total": len(disputed),
        "detection_errors": sorted(errors.values(), key=lambda e: (-e["count"], e["rule"])),
        "unclassified_rows": unclassified_rows,
        "classified_now_total": sum(classified_now.values()),
        "note": (
            "Counts use stored tiers. A row naming several platforms counts once under each."
            " Tiers + Not counted + Unclassified = total_rows per platform."
        ),
    }
    if unmapped:
        out["unmapped_labels"] = dict(unmapped.most_common())
    if refused:
        out["refused_classified_now"] = refused
    return out
