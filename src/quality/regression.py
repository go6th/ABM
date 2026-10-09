"""The regression policy as a CI rule (QS2, roadmap 11.3).

A change to a prompt, model alias, connector, checklist or grading config must
keep the eval score at or above the last release, or it does not merge. A
skipped eval is a failure, not a pass. Which paths need the eval is a
path-and-hash rule in ``config/regression.yaml``; the baseline is the newest
*released* row of ``release_checks``. Pure decision, thin DB helpers.

GUARANTEE(test_regressions_and_skipped_evals_block_the_merge)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Decision:
    """The merge decision and its reason."""

    merge: bool
    reason: str


def rules(path: Path | None = None) -> dict[str, Any]:
    """The watched-path rules (validated at call time; every value has a source)."""
    doc = yaml.safe_load((path or ROOT / "config/regression.yaml").read_text())
    if not isinstance(doc, dict):
        raise ValueError("regression.yaml: expected a mapping")
    watched_paths = doc.get("watched_paths")
    if not isinstance(watched_paths, list) or not watched_paths:
        raise ValueError("regression.yaml: watched_paths must not be empty")
    if any(not isinstance(value, str) or not value for value in watched_paths):
        raise ValueError("regression.yaml: watched_paths must contain non-empty strings")
    skipped_eval_is_failure = doc.get("skipped_eval_is_failure")
    if not isinstance(skipped_eval_is_failure, bool):
        raise ValueError("regression.yaml: skipped_eval_is_failure must be a boolean")
    return {
        "watched_paths": watched_paths,
        "skipped_eval_is_failure": skipped_eval_is_failure,
    }


def needs_eval(changed_paths: list[str], cfg: dict[str, Any] | None = None) -> bool:
    """Whether any changed path is watched by the regression policy (pure)."""
    cfg = cfg or rules()
    for changed in changed_paths:
        for watched in cfg["watched_paths"]:
            if changed == watched or changed.startswith(watched.rstrip("/") + "/"):
                return True
    return False


def decide(
    *, eval_required: bool, eval_ran: bool, eval_score: float | None, baseline: float | None
) -> Decision:
    """Merge, or block with the reason (pure).

    No required change: merge. Required and the eval was skipped: block (a
    skipped eval is a failure, never a pass). Ran: equal or better passes,
    worse blocks. No baseline yet: the first release establishes it.
    """
    if not eval_required:
        return Decision(True, "no watched path changed")
    if not eval_ran or eval_score is None:
        return Decision(False, "eval required for a watched change and was skipped")
    if baseline is None:
        return Decision(True, "first release: baseline established")
    if eval_score >= baseline:
        return Decision(True, f"eval {eval_score} >= baseline {baseline}")
    return Decision(False, f"eval {eval_score} < baseline {baseline}")
