"""Generate ``docs/people/rubric.md`` and ``docs/people/counts.md`` from the configuration and the
counts contract, so the documents cannot drift. ``python -m people.docs --check`` exits 1 when a
file is stale (a test runs it)."""

from __future__ import annotations

import sys
from pathlib import Path

from people.config import PeopleConfig, load_people_config
from people.models import PeopleCounts

ROOT = Path(__file__).resolve().parents[2]
RUBRIC = ROOT / "docs/people/rubric.md"
COUNTS = ROOT / "docs/people/counts.md"

_BASES = {
    "found": "every record a source returned plus every person named in research",
    "distinct": "found minus merged_away",
    "merged_away": "records that point at another record (merged)",
    "possible_duplicate": "distinct rows flagged, never merged",
    "in_scope": "distinct",
    "out_of_scope": "distinct, by reason",
    "excluded": "distinct, by reason",
    "review": "distinct, by reason",
    "unresolved": "distinct (no profile URL yet)",
    "profiles_requested": "in-scope people whose profile was asked for",
    "profiles_read": "in-scope people with a read or reused profile",
    "profiles_partial": "profiles read with a field of the wrong type",
    "profiles_failed": "profiles requested whose pull failed",
    "alumni": "profiles read (not computed without a reference list)",
    "tenure_known": "profiles read with a join or start date in one bucket",
    "tenure_unknown": "profiles read with no date or a date spanning two buckets",
    "date_invalid": "profiles read with a future date or a role start before the join date",
    "tenure_buckets": "tenure_known",
    "cert_field_provided": "profiles read that provide the certifications field",
    "cert_field_absent": "profiles read that do not provide it (not 'none listed')",
    "certified_by_lane": "cert_field_provided, per lane (never summed)",
    "posts_people": "priority A people with at least one stored post",
    "posts_pulled": "posts stored",
    "activity_unfiltered": "people whose posts were pulled without a last-activity date",
    "hostile": "profiles and posts carrying the canary or an instruction",
    "fields_dropped": "provider fields outside the allow-list, dropped at the boundary",
    "by_lane_in_scope": "in_scope, per lane (never summed)",
    "by_priority": "in_scope",
}


def _cues(rules: object) -> list[str]:
    lines = ["| Rule | Value | Cues | Positive | Negative |", "|---|---|---|---|---|"]
    for r in rules:  # type: ignore[attr-defined]
        lines.append(
            f"| `{r.rule_id}` | {r.value} | {', '.join(r.cues)} | {r.positive} | {r.negative} |"
        )
    return lines


def render_rubric(cfg: PeopleConfig) -> str:
    """The rubric document (levels, bands, passes, exclusion, review, priority, caps)."""
    out = [
        "# People rubric (generated from config; do not edit)",
        "",
        "Source files: `config/people_scope.yaml`, `config/people_rubric.yaml`, lane lexicon",
        "`config/job_lanes.yaml`. Everything not named by the PRD is `status: proposal` (D-21 to",
        f"D-23). Config hash `{cfg.config_hash}`.",
        "",
        "## Levels (most senior first)",
        "",
        f"Order: {', '.join(cfg.level_order)}. In scope: {', '.join(sorted(cfg.levels_in_scope))}.",
        "",
        *_cues(cfg.level_rules),
        "",
        "## Bands and search passes",
        "",
        "| Pass | Band | Levels |",
        "|---|---|---|",
    ]
    for pass_no, band in cfg.passes:
        levels = [lv for lv in cfg.level_order if cfg.band_of_level.get(lv) == band]
        out.append(f"| {pass_no} | {band} | {', '.join(levels)} |")
    out += ["", "## Exclusion (beats inclusion)", "", *_cues(cfg.exclusion_rules)]
    out += ["", "## Review", "", *_cues(cfg.review_rules)]
    out += [
        "",
        "## Priority (first match wins)",
        "",
        "| Rule | Priority | Band | Lane required |",
        "|---|---|---|---|",
    ]
    out += [
        f"| `{r.rule_id}` | {r.priority} | {r.band} | {r.lane_required} |"
        for r in cfg.priority_rules
    ]
    out += ["", "## Lanes (shared lexicon)", "", "| Lane | Terms |", "|---|---|"]
    out += [
        f"| {lane.lane_id} | {', '.join(lane.terms) or 'none yet (D-17)'} |" for lane in cfg.lanes
    ]
    out += ["", "## Tenure buckets (months)", "", "| Bucket | From | Below |", "|---|---|---|"]
    out += [
        f"| {b.id} | {b.min_months} | {b.max_months if b.max_months is not None else 'open'} |"
        for b in cfg.tenure_buckets
    ]
    out += ["", "## Caps", "", "| Cap | Value |", "|---|---|"]
    out += [f"| {name} | {value:g} |" for name, value in sorted(cfg.caps.items())]
    out += [
        "",
        f"Title fallback: `{cfg.title_fallback}`. Profile levels: `{cfg.profile_levels}`.",
        f"Association functions: {', '.join(cfg.association_functions)}. Internal-only providers:"
        f" {', '.join(sorted(cfg.internal_only_providers))}.",
        "",
    ]
    return "\n".join(out)


def render_counts() -> str:
    """The counts contract: every field of ``PeopleCounts`` with its base."""
    fields = list(PeopleCounts.model_fields)
    missing = [f for f in fields if f not in _BASES]
    if missing:
        raise ValueError(f"counts without a documented base: {missing}")
    out = [
        "# People counts contract (generated; do not edit)",
        "",
        "Built only by `people.counts.compute_counts` from rows; no function takes a count.",
        "Invariants: `found = distinct + merged_away`; `distinct = in_scope + out_of_scope +",
        "excluded + review + unresolved`; `tenure_known + tenure_unknown + date_invalid =",
        "profiles_read`; `cert_field_provided + cert_field_absent = profiles_read`.",
        "Unknown is never zero: alumni is `not computed` without a reference list",
        "(reason `waiting_on_account_owner`).",
        "",
        "| Count | Base |",
        "|---|---|",
    ]
    out += [f"| `{f}` | {_BASES[f]} |" for f in fields]
    out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    """Write the documents, or with ``--check`` report whether they are current."""
    wanted = {RUBRIC: render_rubric(load_people_config()), COUNTS: render_counts()}
    if "--check" in argv:
        stale = [
            str(p.relative_to(ROOT))
            for p, body in wanted.items()
            if not p.exists() or p.read_text() != body
        ]
        if stale:
            print("stale: " + ", ".join(stale))
            return 1
        print("current")
        return 0
    for path, body in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
