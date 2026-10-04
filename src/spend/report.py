"""The end-of-run spend report (internal) and the tenant view (allow-list), pure from a
``Rollup`` (K16, K13).

* every figure comes from the rollup with its base; a missing figure reads ``not measured``,
  never 0; estimates are labelled ``estimate``;
* deterministic: the same rollup gives the same JSON bytes and the same Markdown;
* the operator report names provider codes (internal) and is never a client file; the tenant
  view carries only categories and call counts (no provider name or code, no amounts until
  D-04 sets a credit price).

GUARANTEE(test_report_is_a_pure_deterministic_function_of_the_rollup): same rollup, same bytes.
GUARANTEE(test_tenant_view_is_an_allow_list_projection): no provider, code or amount leaks.
"""

from __future__ import annotations

from collections.abc import Sequence

from spend.models import Rollup, SpendLine, SpendReport, TenantSpendView
from spend.money import format_usd

NOT_MEASURED = "not measured"


def _money(value: int | None) -> str:
    return NOT_MEASURED if value is None else format_usd(value)


def _line(line: SpendLine, total: int, advisory: int | None, threshold: int) -> dict[str, object]:
    share = NOT_MEASURED if total == 0 else f"{line.spent_micros * 100 // total}%"
    variance: object = NOT_MEASURED
    flag = False
    if advisory is not None:
        variance = (
            format_usd(line.spent_micros - advisory)
            if line.spent_micros >= advisory
            else ("-" + format_usd(advisory - line.spent_micros))
        )
        if advisory > 0:
            flag = abs(line.spent_micros - advisory) * 100 > threshold * advisory
        else:
            flag = line.spent_micros > 0
    return {
        "key": line.key,
        "paid_calls": line.paid_calls,
        "replays": NOT_MEASURED if line.replays is None else line.replays,
        "failed_after_charge": line.failed_after_charge,
        "open_reserves": line.open_reserves,
        "credits": NOT_MEASURED if line.credits is None else str(line.credits),
        "usd": format_usd(line.spent_micros),
        "share": share,
        "advisory_usd": _money(advisory),
        "variance": variance,
        "flag": flag,
        "unknown_cost_calls": line.unknown_cost_calls,
        "possible_duplicates": line.possible_duplicates,
        "at_risk_usd": format_usd(line.at_risk_micros),
    }


def build_report(
    rollup: Rollup, problems: Sequence[str], variance_threshold_pct: int
) -> SpendReport:
    """The operator report (JSON body plus Markdown)."""
    ledger = rollup.ledger_micros
    cap = rollup.cap_micros
    remaining = None if cap is None else cap - rollup.spent_micros
    percent = NOT_MEASURED if not cap else f"{rollup.spent_micros * 100 // cap}%"
    body: dict[str, object] = {
        "run_id": rollup.run_id,
        "account_domain": rollup.account_domain,
        "final_status": rollup.status,
        "attended": rollup.attended,
        "cap_versions": [
            {
                "version": v,
                "cap_usd": format_usd(c),
                "approved_by": p or NOT_MEASURED,
                "at": at or NOT_MEASURED,
            }
            for v, c, p, at in rollup.cap_versions
        ],
        "totals": {
            "spent_usd": format_usd(rollup.spent_micros),
            "ledger_usd": format_usd(ledger),
            "cap_usd": _money(cap),
            "remaining_usd": _money(remaining),
            "percent_of_cap": percent,
            "overshoot_usd": format_usd(rollup.overshoot_micros),
        },
        "by_provider": [
            _line(line, ledger, rollup.advisory.get(line.key), variance_threshold_pct)
            for line in rollup.by_provider
        ],
        "by_category": [
            _line(line, ledger, None, variance_threshold_pct) for line in rollup.by_category
        ],
        "by_operation": [
            _line(line, ledger, None, variance_threshold_pct) for line in rollup.by_operation
        ],
        "blocked_rows": {
            reason: list(rows) for reason, rows in sorted(rollup.blocked_rows.items())
        },
        "extensions": rollup.extensions,
        "llm_usd_informational": _money(rollup.llm_micros),
        "balances": {
            "start": [f"{b.provider_code}:{b.status}" for b in rollup.balances_start],
            "end": [f"{b.provider_code}:{b.status}" for b in rollup.balances_end],
        },
        "estimated_avoided_usd": (
            NOT_MEASURED
            if rollup.estimated_avoided_micros is None
            else f"estimate {format_usd(rollup.estimated_avoided_micros)}"
        ),
        "reconciliation": list(problems) or ["consistent"],
    }
    status = "reconciliation_failed" if problems else "reconciled"
    return SpendReport(
        run_id=rollup.run_id, status=status, body=body, markdown=render_markdown(body, status)
    )


def render_markdown(body: dict[str, object], status: str) -> str:
    """A plain Markdown rendering of the body (internal; not a client file)."""
    totals = body["totals"]
    assert isinstance(totals, dict)
    lines = [
        f"# Spend report (internal) for run {body['run_id']}",
        "",
        f"Status: {status}. Final run state: {body['final_status']}.",
        "",
        f"Spent {totals['spent_usd']} of a cap of {totals['cap_usd']} ({totals['percent_of_cap']});"
        f" remaining {totals['remaining_usd']}; overshoot {totals['overshoot_usd']}.",
        "",
        "| Provider | Paid calls | Replays | Spent | Share | Advisory | Variance | Flag |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for line in body["by_provider"]:  # type: ignore[attr-defined]
        lines.append(
            f"| {line['key']} | {line['paid_calls']} | {line['replays']} | {line['usd']} |"
            f" {line['share']} | {line['advisory_usd']} | {line['variance']} |"
            f" {'yes' if line['flag'] else 'no'} |"
        )
    lines.append("")
    for item in body["reconciliation"]:  # type: ignore[attr-defined]
        lines.append(f"Reconciliation: {item}.")
    return "\n".join(lines) + "\n"


def tenant_view(rollup: Rollup) -> TenantSpendView:
    """The allow-listed projection: categories and call counts only (D-30)."""
    return TenantSpendView(
        run_id=rollup.run_id,
        status=rollup.status,
        categories={line.key: line.paid_calls for line in rollup.by_category},
        total_calls=sum(line.paid_calls for line in rollup.by_category),
    )
