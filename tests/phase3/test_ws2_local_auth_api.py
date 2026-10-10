"""First-party credential enrollment and sign-in through the real FastAPI routes."""

from __future__ import annotations

from urllib.parse import urlsplit

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import (
    BASE,
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    new_tenant,
    uid,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_sso_member_can_enroll_verify_and_use_a_local_password(
    plane: Plane,
    browser: Browser,
) -> None:
    tenant = new_tenant("local-password")
    slug = f"local-{uid()}"
    subject = f"{slug}:ann"
    email = f"ann-{uid()}@synthetic.test"
    add_oidc_connection(tenant, plane, slug)
    add_member(tenant, subject, "seller")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            "INSERT INTO users (tenant_id, subject, email) VALUES (%s, %s, %s)",
            (tenant, subject, email),
        )

    signed_in = await browser.oidc_sign_in(
        slug,
        "ann",
        extra={"email": email, "email_verified": True},
    )
    assert signed_in.status_code == 303

    password = "Long-unique-blue-credential-9842"
    enrolled = await browser.call(
        "POST",
        "/v1/auth/password/enroll",
        json={"password": password},
        headers={"Origin": BASE},
    )
    assert enrolled.status_code == 202, enrolled.text
    assert plane.mailer.sent[-1][0:2] == (email, "local_email_verification")
    verification_url = plane.mailer.sent[-1][2]["verification_url"]
    assert urlsplit(verification_url).scheme == "https"
    assert urlsplit(verification_url).path == "/verify-email"
    token = urlsplit(verification_url).fragment
    assert token and token not in verification_url.split("#", 1)[0]

    verified = await browser.client.post(
        "/v1/auth/email/verify",
        json={"token": token},
        headers={"Origin": BASE},
    )
    assert verified.status_code == 200, verified.text
    assert (
        await browser.client.post(
            "/v1/auth/email/verify",
            json={"token": token},
            headers={"Origin": BASE},
        )
    ).status_code == 400

    local_login = await browser.client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": BASE},
    )
    assert local_login.status_code == 200, local_login.text
    with psycopg.connect(ADMIN_DSN) as admin:
        rotated = admin.execute(
            "SELECT count(*) FROM auth_sessions WHERE tenant_id = %s"
            " AND user_id = %s AND revoke_reason = 'rotated'",
            (tenant, subject),
        ).fetchone()[0]
    assert rotated == 1
    current = await browser.client.get("/v1/sso/session")
    assert current.status_code == 200
    assert current.json()["user_id"] == subject
    assert current.json()["credential"] == "session"

    requested = await browser.client.post(
        "/v1/auth/password/reset/request",
        json={"email": email},
        headers={"Origin": BASE},
    )
    assert requested.status_code == 202, requested.text
    assert requested.json() == {"accepted": True}
    assert plane.mailer.sent[-1][0:2] == (email, "local_password_reset")
    reset_url = plane.mailer.sent[-1][2]["reset_url"]
    assert urlsplit(reset_url).path == "/reset"
    reset_token = urlsplit(reset_url).fragment
    assert reset_token and reset_token not in reset_url.split("#", 1)[0]

    new_password = "Fresh-password-without-account-context-127"
    reset = await browser.client.post(
        "/v1/auth/password/reset",
        json={"token": reset_token, "password": new_password},
        headers={"Origin": BASE},
    )
    assert reset.status_code == 200, reset.text
    assert (
        await browser.client.post(
            "/v1/auth/password/reset",
            json={"token": reset_token, "password": new_password},
            headers={"Origin": BASE},
        )
    ).status_code == 400
    assert (
        await browser.client.post(
            "/v1/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": BASE},
        )
    ).status_code == 401
    assert (
        await browser.client.post(
            "/v1/auth/login",
            json={"email": email, "password": new_password},
            headers={"Origin": BASE},
        )
    ).status_code == 200
    with psycopg.connect(ADMIN_DSN) as admin:
        revoked = admin.execute(
            "SELECT count(*) FROM auth_sessions WHERE tenant_id = %s"
            " AND user_id = %s AND revoke_reason = 'password_reset'",
            (tenant, subject),
        ).fetchone()[0]
    assert revoked == 1
