"""The routing benchmark (D-48, D-51): a candidate per task kind, run over the golden accounts,
reported with intervals plus a PROPOSED routing diff. Nothing in ``config/model_routing.yaml``
changes from it without the D-51 sign-off. Without analyst-labelled accounts, model access and
credits the report ships with an empty results table and status Blocked.

GUARANTEE(test_bench_template_is_blocked_without_inputs): section 9.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

MAX_MATRIX = 64  # task kinds x candidates per benchmark (bounded, proposal)


def matrix(
    task_kinds: Sequence[str], candidates: Mapping[str, Sequence[str]]
) -> list[tuple[str, str]]:
    """Every (task kind, candidate) pair to measure, capped."""
    pairs = [(t, c) for t in sorted(task_kinds) for c in sorted(candidates.get(t, ()))]
    if len(pairs) > MAX_MATRIX:
        raise ValueError(f"benchmark matrix of {len(pairs)} exceeds {MAX_MATRIX}")
    return pairs


def template(report_id: str, pairs: Sequence[tuple[str, str]], blocked_reason: str) -> str:
    """The report with an empty results table."""
    rows = [f"| {t} | {c} |  |  |  |  |  |  |  |" for t, c in pairs]
    return "\n".join(
        [
            f"# Routing benchmark {report_id}",
            "",
            f"Status: **Blocked: {blocked_reason}**",
            "",
            "| task kind | candidate | coverage | citation validity | grade accuracy |"
            " unsupported rate | verifier disagreement | cost | time |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            "Proposed routing diff: none (no measurement).",
            "",
        ]
    )
