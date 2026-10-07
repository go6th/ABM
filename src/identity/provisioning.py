"""Who may sign in, and with which role (ID2, D-62 proposal). Pure.

* The tenant's group-to-role map is the only source of an SSO user's eligibility: an IdP user
  none of whose groups is mapped gets NO access ("no mapping means no access").
* An existing member signs in with the role the tenant stored (admins manage roles); the IdP
  does not change it. A suspended or removed member is refused.
* An unknown user is provisioned ONLY when the connection enables just-in-time provisioning, the
  email is verified by the IdP and its domain is one the tenant verified; the role is the
  highest mapped group role.

GUARANTEE(test_provisioning_is_deny_by_default)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from identity.roles import ROLES, is_role


@dataclass(frozen=True)
class SignInDecision:
    """Admit (with a role and whether this provisions a member) or refuse (with a reason)."""

    admit: bool
    role: str | None = None
    provision: bool = False
    reason: str | None = None


def mapped_role(groups: Sequence[str], group_role_map: Mapping[str, str]) -> str | None:
    """The most privileged role any of the user's groups maps to, or None."""
    roles = {group_role_map[g] for g in groups if g in group_role_map}
    roles = {r for r in roles if is_role(r)}
    for role in ROLES:  # ROLES is ordered most privileged first
        if role in roles:
            return role
    return None


def email_domain(email: str | None) -> str | None:
    """The lower-cased domain of an email address, or None."""
    if not email or email.count("@") != 1:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain or None


def decide(
    *,
    groups: Sequence[str],
    group_role_map: Mapping[str, str],
    member_role: str | None,
    member_status: str | None,
    jit_enabled: bool,
    email: str | None,
    email_verified: bool,
    verified_domains: Sequence[str],
) -> SignInDecision:
    """The sign-in decision for one verified IdP identity."""
    role = mapped_role(groups, group_role_map)
    if role is None:
        return SignInDecision(False, reason="no_group_mapping")
    if member_status is not None:
        if member_status != "active" or member_role is None:
            return SignInDecision(False, reason="membership_inactive")
        return SignInDecision(True, role=member_role)
    if not jit_enabled:
        return SignInDecision(False, reason="unknown_user")
    domain = email_domain(email)
    if not email_verified or domain is None:
        return SignInDecision(False, reason="email_unverified")
    if domain not in {d.lower() for d in verified_domains}:
        return SignInDecision(False, reason="domain_unverified")
    return SignInDecision(True, role=role, provision=True)
