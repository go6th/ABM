"""WS2 role policy (ID4, D-61) and provisioning (ID2, D-62): the full role-by-action table against
a hand-written oracle, owner-only actions, step-up actions, last admin and self change, and the
deny-by-default sign-in decision. Pure."""

from __future__ import annotations

import itertools

import pytest

from account_intelligence.api.policy import ROUTE_POLICIES
from identity import provisioning
from identity.roles import (
    ROLES,
    UnknownAction,
    allowed,
    load_table,
    needs_step_up,
    role_change_refusal,
    roles_for,
)

ADM, ANA, SEL, VIE = "admin", "analyst", "seller", "viewer"
# Hand-written from D-61 and the ARCH access-control table (not read from config/roles.yaml).
ORACLE: dict[str, set[str]] = {
    "read_public": {ADM, ANA, SEL, VIE},
    "read_internal": {ADM, ANA, SEL},
    "export_files": {ADM, ANA, SEL, VIE},
    "add_account": {ADM, ANA, SEL},
    "start_run": {ADM, ANA, SEL},
    "cancel_run": {ADM, ANA, SEL},
    "approve_run": {ADM, ANA, SEL},
    "configure_alerts": {ADM, ANA, SEL},
    "save_own_view": {ADM, ANA, SEL},
    "edit_shared_views": {ADM, ANA},
    "edit_tracker_notes": {ADM, ANA},
    "give_feedback": {ADM, ANA, SEL, VIE},
    "revoke_own_session": {ADM, ANA, SEL, VIE},
    "manage_own_auth": {ADM, ANA, SEL, VIE},
    "approve_cap": {ADM},
    "manage_users": {ADM},
    "manage_sso": {ADM},
    "manage_settings": {ADM},
    "manage_connectors": {ADM},
    "read_audit": {ADM},
    "export_audit": {ADM},
    "read_usage": {ADM},
    "grant_support": {ADM},
    "request_deletion": {ADM},
}


def test_role_policy_matches_the_d61_table() -> None:
    assert set(load_table().actions) == set(ORACLE)
    for action, role in itertools.product(ORACLE, (*ROLES, "operator", "support", "", "ADMIN")):
        assert allowed(role, action) == (role in ORACLE[action]), (role, action)
        assert roles_for(action) == frozenset(ORACLE[action])
    # analyst is everything a seller can do, plus views and notes; never administration
    seller = {a for a, rs in ORACLE.items() if SEL in rs}
    analyst = {a for a, rs in ORACLE.items() if ANA in rs}
    assert seller <= analyst and analyst - seller == {"edit_shared_views", "edit_tracker_notes"}
    # internal material is never reachable by a viewer
    assert not allowed(VIE, "read_internal")


def test_owner_only_and_step_up_actions() -> None:
    assert allowed(SEL, "save_own_view", owner=True)
    assert not allowed(SEL, "save_own_view", owner=False)
    assert allowed(VIE, "revoke_own_session", owner=True)
    assert not allowed(VIE, "revoke_own_session", owner=False)
    assert allowed(ADM, "manage_users", owner=False)  # ownership only matters where listed
    for action in ("manage_users", "manage_sso", "export_audit", "grant_support", "approve_cap"):
        assert needs_step_up(action)
    assert not needs_step_up("read_public")
    with pytest.raises(UnknownAction):
        allowed(ADM, "launch_rockets")
    with pytest.raises(UnknownAction):
        roles_for("launch_rockets")
    with pytest.raises(UnknownAction):
        needs_step_up("launch_rockets")


def test_catalog_roles_come_from_the_role_policy() -> None:
    for (method, path), policy in ROUTE_POLICIES.items():
        if policy.action is not None:
            assert policy.roles == roles_for(policy.action), (method, path)
        assert not {"operator", "support"} & set(policy.roles), (method, path)


def test_last_admin_and_self_promotion_are_refused() -> None:
    base = {"target_role": ADM, "new_status": "active", "active_admins": 1}
    assert (
        role_change_refusal(actor="u1", target="u1", new_role=ADM, **{**base, "target_role": SEL})
        == "self_change"
    )
    assert role_change_refusal(actor="u1", target="u2", new_role=SEL, **base) == "last_admin"
    assert (
        role_change_refusal(
            actor="u1", target="u2", new_role=None, **{**base, "new_status": "suspended"}
        )
        == "last_admin"
    )
    assert (
        role_change_refusal(
            actor="u1", target="u2", new_role=None, **{**base, "new_status": "removed"}
        )
        == "last_admin"
    )
    assert (
        role_change_refusal(actor="u1", target="u2", new_role=SEL, **{**base, "active_admins": 2})
        is None
    )
    assert role_change_refusal(actor="u1", target="u2", new_role="root", **base) == "unknown_role"
    assert (
        role_change_refusal(
            actor="u1", target="u2", new_role=None, **{**base, "new_status": "frozen"}
        )
        == "unknown_status"
    )
    assert (
        role_change_refusal(actor="u1", target="u2", new_role=ADM, **{**base, "target_role": VIE})
        is None
    )


MAP = {"sales": SEL, "sales-leads": ADM, "readers": VIE, "bogus": "root"}


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"groups": ()}, (False, None, "no_group_mapping")),
        ({"groups": ("bogus",)}, (False, None, "no_group_mapping")),
        ({"groups": ("sales",), "member_role": VIE, "member_status": "active"}, (True, VIE, None)),
        (
            {"groups": ("sales",), "member_role": SEL, "member_status": "suspended"},
            (False, None, "membership_inactive"),
        ),
        (
            {"groups": ("sales",), "member_role": SEL, "member_status": "removed"},
            (False, None, "membership_inactive"),
        ),
        ({"groups": ("sales",)}, (False, None, "unknown_user")),
        (
            {"groups": ("sales",), "jit": True, "email_verified": False},
            (False, None, "email_unverified"),
        ),
        (
            {"groups": ("sales",), "jit": True, "email": "x@other.test"},
            (False, None, "domain_unverified"),
        ),
        (
            {"groups": ("sales",), "jit": True, "email": "not-an-email"},
            (False, None, "email_unverified"),
        ),
        ({"groups": ("sales", "sales-leads", "readers"), "jit": True}, (True, ADM, None)),
        ({"groups": ("readers",), "jit": True}, (True, VIE, None)),
    ],
)
def test_provisioning_is_deny_by_default(
    kw: dict[str, object], expected: tuple[object, ...]
) -> None:
    decision = provisioning.decide(
        groups=kw["groups"],  # type: ignore[arg-type]
        group_role_map=MAP,
        member_role=kw.get("member_role"),  # type: ignore[arg-type]
        member_status=kw.get("member_status"),  # type: ignore[arg-type]
        jit_enabled=bool(kw.get("jit", False)),
        email=kw.get("email", "ann@synthetic.test"),  # type: ignore[arg-type]
        email_verified=bool(kw.get("email_verified", True)),
        verified_domains=["synthetic.test"],
    )
    assert (decision.admit, decision.role, decision.reason) == expected
    assert decision.provision == (expected[0] and "member_status" not in kw)
