"""Planned provider calls: validation, execution through ``call_operation``, and failure mapping.

* A planned call is validated before any spend: the operation must belong to one of the stage's
  allowed tool categories, the arguments must parse into the operation's typed model (extra
  fields refused, no tenant field exists), and any ``domain`` argument must be the run's own
  account domain (another account's data is never requested).
* Retry ownership (W6): a ``RetryableProviderError`` whose ledger attempt is below the node
  ``RetryPolicy`` budget is RE-RAISED so the policy retries the node (paid calls already done
  replay from the ledger). On the last attempt it becomes a failed call cited as tracker proof
  (the row ends Blocked with a reason) and the run continues.
* Non-retryable connector errors that reached the ledger are failed calls (proof of a failure);
  errors before the ledger (unsupported operation, policy refusal) are "not attempted" with a
  configured reason. ``BudgetExceeded`` stops further calls for the run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    ConnectorError,
    NotSupportedYet,
    RetryableProviderError,
)
from account_intelligence.connectors.models import ARGS_FOR, Record
from account_intelligence.connectors.service import call_operation
from account_intelligence.grading.config import load_grading_config
from account_intelligence.workflow.deps import RunDeps


def operations_for(categories: Iterable[str]) -> dict[str, str]:
    """Operation name to tool category, for the given categories (from grading config)."""
    allowed = set(categories)
    return {
        op: cat for op, cat in load_grading_config().category_by_operation.items() if cat in allowed
    }


@dataclass
class RowCalls:
    """Everything one row got from this stage's calls."""

    refs: list[str] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    cap_hit: bool = False
    unattempted: str | None = None


@dataclass
class CallBatch:
    """The outcome of executing a plan."""

    rows: dict[str, RowCalls]
    budget_exhausted: bool = False
    refused: int = 0
    calls: int = 0


def validate_plan(
    plan: Mapping[str, Any],
    rows: Iterable[str],
    operations: Mapping[str, str],
    domain: str,
    limit: int,
) -> tuple[list[tuple[str, Operation, Any]], int]:
    """Parse a planner answer into typed calls. Returns ``(calls, refused_count)``; anything not
    valid is refused and counted, never repaired."""
    owned = set(rows)
    calls: list[tuple[str, Operation, Any]] = []
    refused = 0
    raw = plan.get("calls") if isinstance(plan, Mapping) else None
    for item in raw if isinstance(raw, list) else []:
        if len(calls) >= limit:
            refused += 1
            continue
        try:
            row, op_name, args = item["row"], item["operation"], item["args"]
            if row not in owned or op_name not in operations or not isinstance(args, dict):
                raise ValueError("row or operation not allowed")
            if "domain" in args and str(args["domain"]).lower() != domain.lower():
                raise ValueError("another account's domain")
            op = Operation(op_name)
            typed = ARGS_FOR[op].model_validate_json(json.dumps(args))
        except (KeyError, TypeError, ValueError, ValidationError):
            refused += 1
            continue
        calls.append((str(row), op, typed))
    return calls, refused


async def execute(
    deps: RunDeps, calls: list[tuple[str, Operation, Any]], rows: Iterable[str]
) -> CallBatch:
    """Run validated calls in order; map failures as described in the module docstring."""
    batch = CallBatch(rows={row: RowCalls() for row in rows})
    if deps.connectors is None:
        for row_calls in batch.rows.values():
            row_calls.unattempted = "not_available_yet"
        return batch
    for row, op, args in calls:
        target = batch.rows[row]
        try:
            result = await call_operation(op, args, deps.call_context(), deps.connectors)
        except BudgetExceeded:
            batch.budget_exhausted = True
            target.unattempted = target.unattempted or "budget_cap"
            break
        except RetryableProviderError as error:
            if (error.attempt or 1) < deps.retry_max_attempts:
                raise  # the node RetryPolicy owns the retry (W6)
            if error.call_ref:
                target.refs.append(error.call_ref)
            batch.calls += 1
            continue
        except NotSupportedYet:
            target.unattempted = target.unattempted or "not_available_yet"
            continue
        except ConnectorError as error:
            if error.call_ref:
                target.refs.append(error.call_ref)
                batch.calls += 1
            else:
                batch.refused += 1
            continue
        batch.calls += 1
        target.refs.extend(result.call_refs)
        target.records.extend(result.records)
        target.cap_hit |= result.status == "partial"
    return batch
