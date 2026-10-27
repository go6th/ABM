"""People data consent on PostgreSQL (SYNTHETIC tenants): any member reads where it stands, an
admin grants and withdraws it, and the research graph's consent decision follows the newest row.
GUARANTEE(test_an_admin_grants_and_withdraws_the_people_data_consent)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from tests.phase3.conftest import add_member, make_app, new_tenant
from tests.replica.conftest import _call

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _plane(tenant: UUID, role: str) -> Any:
    from account_intelligence.api.auth import Identity, identity

    user = f"local:{uuid4()}"
    add_member(tenant, user, role)
    plane = make_app()
    now = plane.app.state.container.clock.now()

    async def ident() -> Identity:
        return Identity(user, tenant, role, now, amr=("pwd", "mfa"))

    plane.app.dependency_overrides[identity] = ident
    return plane


async def test_an_admin_grants_and_withdraws_the_people_data_consent() -> None:
    from agents.adapters import PgConsentStore
    from agents.config import load_skill_config
    from agents.guards import consent_decision

    cfg = load_skill_config()
    tenant = new_tenant("Synthetic Consent Co")
    admin = _plane(tenant, "admin")
    reply = await _call(admin, "GET", "/v1/settings/people-consent")
    assert reply.status_code == 200, reply.text
    before = reply.json()
    assert before["granted"] is False and before["text_version"] == cfg.consent_text_version
    assert before["text"] and "contact details" in before["text"]

    granted = await _call(admin, "POST", "/v1/settings/people-consent", json={"accept": True})
    assert granted.status_code == 200, granted.text
    assert granted.json()["granted"] is True and granted.json()["current"] is True
    record = await PgConsentStore().current(tenant, cfg.consent_scope)
    assert consent_decision(record, cfg.consent_scope, cfg.consent_text_version) is None

    withdrawn = await _call(admin, "POST", "/v1/settings/people-consent", json={"accept": False})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["granted"] is False and withdrawn.json()["revoked_at"]
    record = await PgConsentStore().current(tenant, cfg.consent_scope)
    assert (
        consent_decision(record, cfg.consent_scope, cfg.consent_text_version) == "consent_revoked"
    )


async def test_a_viewer_reads_but_cannot_change_the_consent() -> None:
    tenant = new_tenant("Synthetic Consent Co")
    viewer = _plane(tenant, "viewer")
    assert (await _call(viewer, "GET", "/v1/settings/people-consent")).status_code == 200
    refused = await _call(viewer, "POST", "/v1/settings/people-consent", json={"accept": True})
    assert refused.status_code == 403, refused.text
