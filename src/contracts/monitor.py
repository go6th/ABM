"""The passive contract monitor (CX2, roadmap 11.2; D-88).

Every live response is validated against the newest recorded fixture
fingerprint for its operation. Additive drift is logged and counted; breaking
drift **disables the operation fail closed**: further calls are refused (rows
become Blocked with a reason, the run continues on fallbacks), a drift event is
written, and only an operator recording a new fixture and clearing the status
re-enables the operation. The failing payload is kept for diagnosis, internally
only. No paid canary calls: this is checked on responses the platform already
received. A 200 whose body is an error envelope counts as breaking.

GUARANTEE(test_breaking_drift_disables_the_operation_fail_closed)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from contracts import drift
from contracts.fingerprint import Shape, enum_values, fingerprint, shape_of


class OperationDisabled(RuntimeError):
    """The operation is disabled by contract drift; do not call it."""


@dataclass(frozen=True)
class CheckResult:
    """What one live response showed."""

    severity: drift.Severity
    changes: tuple[drift.Change, ...]
    alert: bool  # breaking: page the operator


def _rules() -> dict[str, Any]:
    doc = yaml.safe_load((CONFIG_DIR / "drift.yaml").read_text())
    if not isinstance(doc, dict):
        raise ValueError("drift.yaml: expected a mapping")
    return {str(key): value for key, value in doc.items()}


def error_body(payload: Any) -> bool:
    """A 200 whose body is an error envelope (keys from config; pure)."""
    cfg = _rules()["error_body"]
    if not isinstance(payload, dict):
        return False
    return any(k in payload and payload[k] for k in cfg["keys"])


def _enum_sets(payload: Any, paths: list[str]) -> dict[str, frozenset[str]]:
    return {p: enum_values(payload, p) for p in paths}


def _enums_config(provider: str, operation: str, cfg: dict[str, Any]) -> list[str]:
    return list(cfg["enum_paths"].get("default", []))


async def record_fixture(
    s: Any,
    *,
    provider: str,
    operation: str,
    payload: Any,
    enum_paths: list[str] | None = None,
    source: str = "fixture",
) -> str:
    """Record a fixture's fingerprint (insert-only; the newest row wins)."""
    cfg = _rules()
    shape = shape_of(payload)
    enums = _enum_sets(payload, enum_paths or _enums_config(provider, operation, cfg))
    fp = fingerprint(payload)
    await s.execute(
        text(
            "INSERT INTO contract_fingerprints (provider_code, operation, fingerprint, shape,"
            " enum_sets, source) VALUES (:p, :o, :f, CAST(:sh AS jsonb), CAST(:es AS jsonb), :src)"
        ),
        {
            "p": provider,
            "o": operation,
            "f": fp,
            "sh": json.dumps(shape),
            "es": json.dumps({k: sorted(v) for k, v in enums.items()}),
            "src": source,
        },
    )
    return fp


async def is_disabled(s: Any, *, provider: str, operation: str) -> bool:
    """Whether contract drift has disabled the operation (fail closed)."""
    row = (
        await s.execute(
            text(
                "SELECT disabled FROM contract_operation_status"
                " WHERE provider_code = :p AND operation = :o"
            ),
            {"p": provider, "o": operation},
        )
    ).first()
    return bool(row and row.disabled)


async def enable(s: Any, *, provider: str, operation: str, enable_it: bool) -> None:
    """The operator's one state change (conditional UPDATE ... WHERE)."""
    await s.execute(
        text(
            "UPDATE contract_operation_status SET disabled = :d,"
            " disabled_at = CASE WHEN :d THEN now() ELSE NULL END,"
            " disabled_reason = CASE WHEN :d THEN disabled_reason ELSE NULL END,"
            " updated_at = now()"
            " WHERE provider_code = :p AND operation = :o AND disabled = :was"
        ),
        {"p": provider, "o": operation, "d": not enable_it, "was": enable_it},
    )


async def check(
    s: Any,
    *,
    provider: str,
    operation: str,
    payload: Any,
    enum_paths: list[str] | None = None,
) -> CheckResult:
    """Validate one live response against the newest recorded fixture.

    No fixture recorded yet: ``ok`` (record one from the first response).
    Additive: logged and counted. Breaking: the event is stored, the operation
    is disabled, the caller must treat the affected rows as Blocked.
    """
    if await is_disabled(s, provider=provider, operation=operation):
        raise OperationDisabled(f"{provider}.{operation} disabled by contract drift")
    cfg = _rules()
    if error_body(payload):
        changes = (drift.Change("$", "type_changed", "200 with an error body"),)
        await _record(s, provider, operation, "breaking", changes, payload)
        await _disable(s, provider, operation)
        return CheckResult("breaking", changes, alert=True)
    row = (
        await s.execute(
            text(
                "SELECT shape, enum_sets FROM contract_fingerprints"
                " WHERE provider_code = :p AND operation = :o"
                " ORDER BY recorded_at DESC, id DESC LIMIT 1"
            ),
            {"p": provider, "o": operation},
        )
    ).first()
    if row is None:
        return CheckResult("same", (), alert=False)
    old_shape: Shape = row.shape if isinstance(row.shape, dict) else json.loads(row.shape)
    old_enums = {
        k: frozenset(v)
        for k, v in (
            row.enum_sets if isinstance(row.enum_sets, dict) else json.loads(row.enum_sets)
        ).items()
    }
    new_enums = _enum_sets(payload, enum_paths or _enums_config(provider, operation, cfg))
    v = drift.classify(
        old_shape,
        shape_of(payload),
        old_enums=old_enums,
        new_enums=new_enums,
        breaking_kinds=frozenset(cfg["rules"]["breaking_kinds"]),
    )
    if v.severity == "same":
        return CheckResult("same", (), alert=False)
    await _record(s, provider, operation, v.severity, v.changes, payload)
    if v.severity == "breaking":
        await _disable(s, provider, operation)
    return CheckResult(v.severity, v.changes, alert=v.severity == "breaking")


async def _record(
    s: Any,
    provider: str,
    operation: str,
    severity: str,
    changes: tuple[drift.Change, ...],
    payload: Any,
) -> None:
    body = json.dumps(payload, sort_keys=True, default=str)
    await s.execute(
        text(
            "INSERT INTO contract_drift_events (provider_code, operation, severity, changes,"
            " payload_sha256, payload) VALUES (:p, :o, :sev, CAST(:ch AS jsonb), :h,"
            " CAST(:pl AS jsonb))"
        ),
        {
            "p": provider,
            "o": operation,
            "sev": severity,
            "ch": json.dumps([c.__dict__ for c in changes]),
            "h": hashlib.sha256(body.encode()).hexdigest(),
            "pl": body,
        },
    )


async def _disable(s: Any, provider: str, operation: str) -> None:
    await s.execute(
        text(
            "INSERT INTO contract_operation_status (provider_code, operation, disabled,"
            " disabled_at, disabled_reason) VALUES (:p, :o, true, now(), 'breaking contract"
            " drift') ON CONFLICT (provider_code, operation) DO UPDATE SET disabled = true,"
            " disabled_at = now(), disabled_reason = 'breaking contract drift', updated_at = now()"
            " WHERE contract_operation_status.disabled = false"
        ),
        {"p": provider, "o": operation},
    )
