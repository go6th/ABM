"""Pure verdict and stable defect helpers.

Guarantees: any failed or not-run blocking check fails the gate; defect ordering
is stable (GUARANTEE(test_not_run_check_cannot_pass)).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from gate.models import CheckResult, CheckStatus, Defect, Verdict


def package_hash(files: Iterable[tuple[str, bytes]]) -> str:
    """Hash file names and bytes in stable order with unambiguous framing."""
    digest = hashlib.sha256()
    for name, content in sorted(files, key=lambda item: item[0]):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def config_hash(config_bytes: bytes) -> str:
    """Hash the exact gate configuration bytes."""
    return hashlib.sha256(config_bytes).hexdigest()


def make_defect_id(code: str, step: str, file: str | None, location: str | None) -> str:
    """Derive a stable, non-content-derived defect identifier."""
    canonical = json.dumps(
        [code, step, file, location],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:24]


def verdict_for(checks: Iterable[CheckResult]) -> Verdict:
    """PASS only when every required check explicitly passed."""
    values = tuple(checks)
    if not values or any(result.status is not CheckStatus.PASSED for result in values):
        return Verdict.FAIL
    return Verdict.PASS


def order_defects(defects: Iterable[Defect], limit: int) -> tuple[tuple[Defect, ...], bool]:
    """Sort defects deterministically, cap the report, and preserve truncation state."""
    if limit < 1:
        raise ValueError("defect limit must be positive")
    ordered = tuple(
        sorted(
            defects,
            key=lambda defect: (
                defect.rerun_step.value,
                defect.file or "",
                defect.part or "",
                defect.location or "",
                defect.code,
                defect.defect_id,
            ),
        )
    )
    return ordered[:limit], len(ordered) > limit
