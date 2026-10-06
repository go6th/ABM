"""Immutable baselines (E8): the last green ``main`` run, stored with code sha, prompt hashes,
routing hash and golden version. A baseline is never edited: a better one is a new file (or row).

GUARANTEE(test_baselines_are_immutable): E8.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.runner import CaseScores, EvalResult


class BaselineExists(FileExistsError):
    """A baseline file is never overwritten."""


@dataclass(frozen=True)
class Provenance:
    """What a result was computed from."""

    code_sha: str
    prompts_hash: str
    routing_hash: str
    golden_version: str
    mode: str


def to_json(result: EvalResult, prov: Provenance) -> dict[str, Any]:
    """A deterministic JSON form."""
    return {
        "provenance": asdict(prov),
        "metrics": dict(sorted(result.metrics.items())),
        "bases": {k: list(v) for k, v in sorted(result.bases.items())},
        "cases": [
            {"account": c.account, "repeat": c.repeat, "values": dict(sorted(c.values.items()))}
            for c in sorted(result.cases, key=lambda c: (c.account, c.repeat))
        ],
        "excluded_stale_labels": result.excluded_stale_labels,
    }


def from_json(data: dict[str, Any]) -> tuple[EvalResult, Provenance]:
    """The inverse of ``to_json``."""
    result = EvalResult(
        metrics=dict(data["metrics"]),
        bases={k: (int(v[0]), int(v[1])) for k, v in data["bases"].items()},
        cases=tuple(
            CaseScores(c["account"], int(c["repeat"]), dict(c["values"])) for c in data["cases"]
        ),
        excluded_stale_labels=int(data["excluded_stale_labels"]),
    )
    return result, Provenance(**data["provenance"])


def write_new(directory: Path, result: EvalResult, prov: Provenance) -> Path:
    """Write ``baseline-<code_sha>.json``; refuses to overwrite an existing baseline."""
    path = directory / f"baseline-{prov.code_sha}.json"
    if path.exists():
        raise BaselineExists(str(path))
    path.write_text(json.dumps(to_json(result, prov), sort_keys=True, indent=1) + "\n")
    return path
