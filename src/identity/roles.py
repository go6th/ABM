"""Tenant roles as a pure policy function (ID4, D-61 proposal; ``config/roles.yaml``).

``allowed(role, action, owner=...)`` is the only place a tenant role is turned into a decision;
the route catalog derives its role sets from it and a test checks the whole table against a
hand-written oracle. Pure: the table is passed in or loaded once; no clock, no I/O per call.

Membership changes have two rules of their own (``role_change_refusal``): nobody changes their
own role, and the last active admin can be neither demoted nor suspended nor removed.

GUARANTEE(test_role_policy_matches_the_d61_table)
GUARANTEE(test_last_admin_and_self_promotion_are_refused)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import yaml

from account_intelligence.config import CONFIG_DIR

Role = Literal["admin", "analyst", "seller", "viewer"]
ROLES: tuple[Role, ...] = ("admin", "analyst", "seller", "viewer")


class UnknownAction(KeyError):
    """An action the role table does not define (a programming error, never a grant)."""


@dataclass(frozen=True)
class RoleTable:
    """Which roles may take each action; which actions need ownership or a fresh step-up."""

    actions: dict[str, frozenset[str]]
    owner_only: frozenset[str]
    step_up: frozenset[str]


@lru_cache
def load_table() -> RoleTable:
    """``config/roles.yaml`` validated: known roles only, owner and step-up actions defined."""
    raw = yaml.safe_load((CONFIG_DIR / "roles.yaml").read_text())
    if tuple(raw["roles"]) != ROLES:
        raise ValueError("config/roles.yaml must list exactly admin, analyst, seller, viewer")
    actions = {str(a): frozenset(str(r) for r in rs) for a, rs in raw["actions"].items()}
    for name, roles in actions.items():
        if not roles or roles - set(ROLES):
            raise ValueError(f"action {name} names unknown roles or none")
    table = RoleTable(
        actions, frozenset(raw.get("owner_only") or ()), frozenset(raw.get("step_up") or ())
    )
    if (table.owner_only | table.step_up) - set(actions):
        raise ValueError("owner_only and step_up must name defined actions")
    return table


def is_role(value: object) -> bool:
    """True for one of the four tenant roles (never an operator or support role)."""
    return value in ROLES


def allowed(role: str, action: str, *, owner: bool = True, table: RoleTable | None = None) -> bool:
    """May ``role`` take ``action``? ``owner`` says whether the caller owns the resource; it only
    matters for owner-only actions. Unknown roles are refused; unknown actions raise."""
    t = table or load_table()
    if action not in t.actions:
        raise UnknownAction(action)
    if not is_role(role) or role not in t.actions[action]:
        return False
    return owner or action not in t.owner_only


def roles_for(action: str, table: RoleTable | None = None) -> frozenset[str]:
    """Every role that may take ``action`` (the route catalog's role set)."""
    t = table or load_table()
    if action not in t.actions:
        raise UnknownAction(action)
    return t.actions[action]


def needs_step_up(action: str, table: RoleTable | None = None) -> bool:
    """True when ``action`` needs a recent MFA sign-in."""
    t = table or load_table()
    if action not in t.actions:
        raise UnknownAction(action)
    return action in t.step_up


def role_change_refusal(
    *,
    actor: str,
    target: str,
    target_role: str,
    new_role: str | None,
    new_status: str,
    active_admins: int,
) -> str | None:
    """Why a membership change must be refused, or None. ``new_role`` None keeps the role;
    ``new_status`` is active, suspended or removed; ``active_admins`` counts the tenant's active
    admins INCLUDING the target."""
    if actor == target:
        return "self_change"
    if new_role is not None and not is_role(new_role):
        return "unknown_role"
    if new_status not in ("active", "suspended", "removed"):
        return "unknown_status"
    loses_admin = target_role == "admin" and (
        (new_role is not None and new_role != "admin") or new_status != "active"
    )
    if loses_admin and active_admins <= 1:
        return "last_admin"
    return None
