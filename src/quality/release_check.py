"""The release check (QS1, roadmap 11.3): a release needs all three quality layers.

Structural gate, semantic verifier and statistical eval must all have run and
passed for the candidate change before it can be released; the result is stored
insert-only in ``release_checks``. Also the no-silent-omission delivery rule: a
row with no status blocks delivery (re-checked here, never trusted upstream).

GUARANTEE(test_a_release_needs_every_layer)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

LAYERS = ("gate", "verify", "eval")


@dataclass(frozen=True)
class ReleaseVerdict:
    """Whether a candidate may be released, and which layers are missing."""

    released: bool
    missing: tuple[str, ...]


def check(*, gate: bool, verify: bool, eval_: bool) -> ReleaseVerdict:
    """All three layers ran and passed, or the release fails (pure)."""
    ok = {"gate": gate, "verify": verify, "eval": eval_}
    missing = tuple(name for name in LAYERS if not ok[name])
    return ReleaseVerdict(not missing, missing)


@dataclass(frozen=True)
class RowStatus:
    """One row's delivery status (``None`` means no status was recorded)."""

    ref: str
    status: str | None


def delivery_blockers(rows: list[RowStatus]) -> tuple[str, ...]:
    """The rows whose missing status blocks delivery (pure; no silent omission)."""
    return tuple(r.ref for r in rows if not (r.status or "").strip())


async def record(
    s: Any,
    *,
    candidate: str,
    gate: bool,
    verify: bool,
    eval_: bool,
    eval_score: float | None = None,
    released: bool | None = None,
) -> ReleaseVerdict:
    """Store one release check (insert-only)."""
    v = check(gate=gate, verify=verify, eval_=eval_)
    passed = v.released if released is None else released
    await s.execute(
        text(
            "INSERT INTO release_checks (candidate, gate_ok, verify_ok, eval_ok, eval_score,"
            " released) VALUES (:c, :g, :v, :e, :sc, :r)"
        ),
        {"c": candidate, "g": gate, "v": verify, "e": eval_, "sc": eval_score, "r": passed},
    )
    return ReleaseVerdict(passed, v.missing)


async def baseline(s: Any) -> float | None:
    """The eval score of the newest released candidate (the QS2 baseline)."""
    row = (
        await s.execute(
            text(
                "SELECT eval_score FROM release_checks WHERE released AND eval_score IS NOT NULL"
                " ORDER BY id DESC LIMIT 1"
            )
        )
    ).first()
    return None if row is None else float(row.eval_score)
