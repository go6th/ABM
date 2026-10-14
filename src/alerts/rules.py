"""The rule registry (AL2): the ten rules from ``assets/alert_rules.yaml`` (sourced) plus the D-74
additions (``status: proposal``), with the signals each watches and its grade floor. One loader;
a rule naming an unknown grade is refused.

GUARANTEE(test_the_rule_registry_is_the_sourced_ten_plus_marked_proposals)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import yaml

from account_intelligence.config import CONFIG_DIR
from refresh.delta import GRADE_ORDER

# A byte-for-byte copy of the skill's assets/alert_rules.yaml (the source of truth), so the
# installed image has it (test_the_alert_rules_copy_matches_the_skill_asset keeps them equal).
ASSET = CONFIG_DIR / "alert_rules.yaml"
SOURCED_IDS = (
    "new_build_role",
    "leadership_change",
    "tier_one_news",
    "funding_ma",
    "budget",
    "new_tech_detection",
    "exec_speaking",
    "priority_a_posts",
    "lead_regraded_up",
    "confidence_drop",
)


class RulesInvalid(ValueError):
    """The registry breaks a rule."""


@dataclass(frozen=True)
class Rule:
    """One alert rule."""

    id: str
    tier: Literal["immediate", "digest"]
    delivery: tuple[str, ...]
    signals: tuple[str, ...]
    grade_floor: str
    proposal: bool


def build(asset: dict[str, Any], extra: dict[str, Any]) -> dict[str, Rule]:
    """Validate and merge."""
    out: dict[str, Rule] = {}
    entries = [(r, False) for r in asset["rules"]] + [(r, True) for r in extra["extra_rules"]]
    for raw, proposal in entries:
        rid = str(raw["id"])
        if rid in out:
            raise RulesInvalid(f"duplicate rule {rid}")
        if proposal and raw.get("status") != "proposal":
            raise RulesInvalid(f"{rid}: an added rule must be marked status: proposal")
        sig = extra["rule_signals"].get(rid)
        if sig is None:
            raise RulesInvalid(f"{rid}: no signal mapping")
        floor = str(sig["grade_floor"])
        if floor not in GRADE_ORDER:
            raise RulesInvalid(f"{rid}: unknown grade {floor!r}")
        tier = raw["tier"]
        if tier not in ("immediate", "digest"):
            raise RulesInvalid(f"{rid}: unknown tier")
        out[rid] = Rule(rid, tier, tuple(raw["delivery"]), tuple(sig["signals"]), floor, proposal)
    if {r for r, v in out.items() if not v.proposal} != set(SOURCED_IDS):
        raise RulesInvalid("the sourced rules must be exactly the ten of alert_rules.yaml")
    return out


@lru_cache
def load() -> dict[str, Rule]:
    """The registry."""
    asset = yaml.safe_load(ASSET.read_text())
    extra = yaml.safe_load((CONFIG_DIR / "alerts_extra.yaml").read_text())
    return build(asset, extra)


@lru_cache
def extra_config() -> dict[str, Any]:
    """``config/alerts_extra.yaml`` (caps and defaults)."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "alerts_extra.yaml").read_text())
    return data


def rules_for(signal_type: str) -> list[Rule]:
    """The rules watching a signal type (registry order)."""
    return [r for r in load().values() if signal_type in r.signals]
