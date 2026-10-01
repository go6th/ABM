"""People source benchmark (roadmap s6): BitScale alone versus BitScale plus Sumble.

INTERNAL: output names brokers and credits and must not reach client-facing material.
Uses recorded responses only; there is no live path in Phase 0.

    python -m app.benchmarks.people_source --out build/benchmarks
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.benchmarks.common import Cell, Provenance, to_markdown, write_tables
from app.brokers import BrokerRequest, BrokerSession, Connector, RecordedConnector
from app.decision_log import write_evidence
from app.golden import GoldenAccount, load_golden_accounts
from app.people import PersonRecord, merge_people
from app.rules import RuleBundle, default_rules

COLUMNS = [
    "account",
    "expected_people",
    "found_bitscale_only_arm",
    "found_combined_arm",
    "overlap",
    "added_by_sumble",
    "excluded",
    "credits_bitscale_only_arm",
    "credits_combined_arm",
    "credits_per_added_person",
    "paid_pulls",
    "duplicate_paid_pulls",
]


def bitscale_request(domain: str) -> BrokerRequest:
    return BrokerRequest(
        broker="bitscale", operation="find_people", params={"domain": domain, "scope": "default"}
    )


def sumble_request(domain: str) -> BrokerRequest:
    return BrokerRequest(
        broker="sumble",
        operation="people_by_technology",
        params={"domain": domain, "attributes": ["title"]},
    )


def _records(payload: dict[str, object], source: str) -> list[PersonRecord]:
    people = payload.get("people")
    if not isinstance(people, list):
        raise ValueError(f"{source} response has no people list")
    return [
        PersonRecord(
            name=str(p["name"]),
            title=str(p["title"]),
            profile_url=p.get("profile_url"),
            source=source,
        )
        for p in people
        if isinstance(p, dict)
    ]


def compare_account(
    golden: GoldenAccount, rules: RuleBundle, connectors: dict[str, Connector]
) -> dict[str, Cell]:
    session = BrokerSession(connectors)
    domain = golden.account.domain
    bit = session.call(bitscale_request(domain))
    sum_ = session.call(sumble_request(domain))
    bit_records = _records(bit.payload, "bitscale")
    sum_records = _records(sum_.payload, "sumble")

    arm_a = merge_people(bit_records, rules.people_scope)
    arm_b = merge_people(bit_records + sum_records, rules.people_scope)
    overlap = sum(1 for p in arm_b.people if {"bitscale", "sumble"} <= p.sources)
    added = len(arm_b.people) - len(arm_a.people)
    credits_a = bit.cost_credits
    credits_b = bit.cost_credits + sum_.cost_credits
    return {
        "account": golden.golden_id,
        "expected_people": golden.expected_people.total,
        "found_bitscale_only_arm": len(arm_a.people),
        "found_combined_arm": len(arm_b.people),
        "overlap": overlap,
        "added_by_sumble": added,
        "excluded": len(arm_b.excluded),
        "credits_bitscale_only_arm": credits_a,
        "credits_combined_arm": credits_b,
        "credits_per_added_person": (credits_b - credits_a) / added if added else None,
        "paid_pulls": session.paid_pulls(),
        "duplicate_paid_pulls": len(session.duplicate_paid_pulls()),
    }


def run(
    *,
    golden_dir: Path | None = None,
    recordings_dir: Path | None = None,
    rules: RuleBundle | None = None,
) -> list[dict[str, Cell]]:
    rules = rules or default_rules()
    goldens = load_golden_accounts(golden_dir) if golden_dir else load_golden_accounts()
    kwargs = {"root": recordings_dir} if recordings_dir else {}
    connectors: dict[str, Connector] = {
        "bitscale": RecordedConnector("bitscale", **kwargs),
        "sumble": RecordedConnector("sumble", **kwargs),
    }
    rows = [compare_account(g, rules, connectors) for g in goldens]
    totals: dict[str, Cell] = {"account": "ALL (total)"}
    for col in COLUMNS[1:]:
        vals = [r[col] for r in rows]
        nums = [v for v in vals if isinstance(v, int | float)]
        totals[col] = None if len(nums) != len(vals) else sum(nums)
    a, b = totals["credits_bitscale_only_arm"], totals["credits_combined_arm"]
    added_total = totals["added_by_sumble"]
    totals["credits_per_added_person"] = (
        (b - a) / added_total
        if isinstance(a, int | float)
        and isinstance(b, int | float)
        and isinstance(added_total, int | float)
        and added_total
        else None
    )
    return [*rows, totals]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/benchmarks"))
    parser.add_argument("--golden-dir", type=Path, default=None)
    parser.add_argument("--recordings-dir", type=Path, default=None)
    parser.add_argument("--decision-log", type=Path, default=None)
    parser.add_argument("--allow-non-evidence", action="store_true")
    args = parser.parse_args(argv)

    rules = default_rules()
    goldens = load_golden_accounts(args.golden_dir) if args.golden_dir else load_golden_accounts()
    provenance = Provenance(
        calls="recorded", data="synthetic" if any(g.synthetic for g in goldens) else "real"
    )
    rows = run(golden_dir=args.golden_dir, recordings_dir=args.recordings_dir, rules=rules)
    csv_path, md_path = write_tables(
        rows,
        COLUMNS,
        args.out,
        "people_source",
        provenance=provenance,
        config_version=rules.version,
    )
    print(f"wrote {csv_path} and {md_path}")
    print(provenance.label())
    if args.decision_log:
        write_evidence(
            args.decision_log,
            "people_source",
            to_markdown(rows, COLUMNS),
            provenance,
            config_version=rules.version,
            allow_non_evidence=args.allow_non_evidence,
        )
        print(f"updated {args.decision_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
