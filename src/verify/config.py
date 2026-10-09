"""Load and validate ``config/verify.yaml`` (every value with a source or a status).

GUARANTEE(test_verify_config_is_sourced_and_actions_are_downgrade_only): D-49.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

OUTCOMES = (
    "confirmed",
    "value_mismatch",
    "not_found",
    "unreadable",
    "unavailable",
    "not_independent",
)
CLAIM_ACTIONS = frozenset({"keep", "downgrade", "drop", "flag"})
VERDICTS = ("supported", "overstated", "unsupported", "contradicted")
NARRATIVE_ACTIONS = frozenset({"keep", "rephrase", "remove"})


class VerifyConfigInvalid(ValueError):
    """``config/verify.yaml`` is invalid or unsourced."""


@dataclass(frozen=True)
class VerifyConfig:
    """The validated verifier configuration."""

    enabled: bool
    token_share_pct: int
    max_claims_per_run: int
    max_source_chars: int
    outcome_actions: Mapping[str, str]
    narrative_actions: Mapping[str, str]
    config_hash: str


def _value(data: Mapping[str, Any], key: str) -> Any:
    entry = data.get(key)
    if not isinstance(entry, Mapping) or "value" not in entry:
        raise VerifyConfigInvalid(f"{key} must be a mapping with a value")
    if "source" not in entry and "status" not in entry:
        raise VerifyConfigInvalid(f"{key} has no source or status")
    return entry["value"]


def build(data: Mapping[str, Any], raw: bytes) -> VerifyConfig:
    """Validate (``VerifyConfigInvalid`` on anything wrong)."""
    actions = _value(data, "outcome_actions")
    if set(actions) != set(OUTCOMES) or not set(actions.values()) <= CLAIM_ACTIONS:
        raise VerifyConfigInvalid("outcome_actions must map every outcome to a claim action")
    if actions["confirmed"] != "keep" or any(
        a == "keep" for o, a in actions.items() if o != "confirmed"
    ):
        raise VerifyConfigInvalid("only a confirmed re-read keeps a Confirmed claim (V4)")
    narrative = _value(data, "narrative_actions")
    if set(narrative) != set(VERDICTS) or not set(narrative.values()) <= NARRATIVE_ACTIONS:
        raise VerifyConfigInvalid("narrative_actions must map every verdict to an action")
    if (
        narrative["supported"] != "keep"
        or narrative["unsupported"] == "keep"
        or narrative["contradicted"] == "keep"
    ):
        raise VerifyConfigInvalid("unsupported or contradicted sentences cannot be kept (V6)")
    ints = {}
    for key in ("token_share_pct", "max_claims_per_run", "max_source_chars"):
        v = _value(data, key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise VerifyConfigInvalid(f"{key} must be a positive integer")
        ints[key] = v
    if ints["token_share_pct"] > 100:
        raise VerifyConfigInvalid("token_share_pct is a percentage")
    enabled = _value(data, "enabled")
    if not isinstance(enabled, bool):
        raise VerifyConfigInvalid("enabled must be a boolean")
    return VerifyConfig(
        enabled,
        ints["token_share_pct"],
        ints["max_claims_per_run"],
        ints["max_source_chars"],
        dict(actions),
        dict(narrative),
        hashlib.sha256(raw).hexdigest(),
    )


@lru_cache(maxsize=4)
def load_verify_config(directory: Path | None = None) -> VerifyConfig:
    """Load ``verify.yaml``."""
    path = (
        directory
        or Path(os.environ.get("CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")
    ) / "verify.yaml"
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, Mapping):
        raise VerifyConfigInvalid("verify.yaml must contain a mapping")
    return build(data, raw)
