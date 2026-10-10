"""First-party credential storage stays behind the narrow identity definer functions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from argon2 import PasswordHasher

from tests.conftest import ADMIN_DSN, APP_DSN

pytestmark = pytest.mark.requires_postgres


def test_app_role_uses_only_narrow_functions_for_global_credentials() -> None:
    """The runtime cannot directly read credential tables; resolver roles never bypass RLS."""
    with psycopg.connect(ADMIN_DSN) as admin:
        grants = admin.execute(
            "SELECT has_table_privilege('app_rw', 'local_identities', 'SELECT'),"
            " has_table_privilege('app_rw', 'local_identities', 'INSERT'),"
            " has_table_privilege('app_rw', 'local_auth_tokens', 'SELECT'),"
            " has_table_privilege('app_rw', 'local_auth_events', 'SELECT'),"
            " (SELECT rolbypassrls FROM pg_roles WHERE rolname = 'identity_authenticator')"
        ).fetchone()
    assert grants == (False, False, False, False, False)

    email = f"local-{uuid4().hex}@synthetic.test"
    now = datetime.now(UTC)
    password_hash = PasswordHasher().hash("synthetic password hash input")
    with psycopg.connect(APP_DSN) as app, app.transaction():
        user_id = app.execute(
            "SELECT local_auth_create(%s, %s, %s)", (email, password_hash, now)
        ).fetchone()[0]
        lookup = app.execute("SELECT * FROM local_auth_lookup(%s)", (email.upper(),)).fetchone()
        assert lookup[0] == user_id
        assert lookup[1] == password_hash
        assert lookup[2] == "pending_verification"


def test_email_verification_and_membership_lookup_are_single_use_and_scoped() -> None:
    """Verification activates one identity and membership lookup returns its active tenants."""
    email = f"verify-{uuid4().hex}@synthetic.test"
    now = datetime.now(UTC)
    password_hash = PasswordHasher().hash("synthetic password hash input")
    tenant_ids = [uuid4(), uuid4()]
    token_hash = hashlib.sha256(uuid4().bytes).hexdigest()

    with psycopg.connect(ADMIN_DSN) as admin, admin.transaction():
        admin.execute(
            "INSERT INTO tenants (id, name) VALUES (%s, %s), (%s, %s)",
            (tenant_ids[0], "local-auth-a", tenant_ids[1], "local-auth-b"),
        )

    with psycopg.connect(APP_DSN) as app, app.transaction():
        user_id = app.execute(
            "SELECT local_auth_create(%s, %s, %s)", (email, password_hash, now)
        ).fetchone()[0]
        assert app.execute(
            "SELECT local_auth_issue_token(%s, 'email_verification', %s, %s, %s)",
            (user_id, token_hash, now + timedelta(minutes=30), now),
        ).fetchone()[0]
        verified = app.execute(
            "SELECT local_auth_verify_email(%s, %s)", (token_hash, now)
        ).fetchone()[0]
        replay = app.execute(
            "SELECT local_auth_verify_email(%s, %s)", (token_hash, now)
        ).fetchone()[0]
        assert verified == user_id
        assert replay is None

    with psycopg.connect(ADMIN_DSN) as admin, admin.transaction():
        admin.execute(
            "INSERT INTO users (tenant_id, subject, email)"
            " VALUES (%s, %s, %s), (%s, 'other:subject', %s)",
            (tenant_ids[0], str(user_id), email, tenant_ids[1], email),
        )
        admin.execute(
            "INSERT INTO memberships (tenant_id, user_id, role, status)"
            " VALUES (%s, %s, 'seller', 'active'), (%s, 'other:subject', 'viewer', 'suspended')",
            (tenant_ids[0], str(user_id), tenant_ids[1]),
        )

    with psycopg.connect(APP_DSN) as app, app.transaction():
        memberships = app.execute(
            "SELECT tenant_id, user_id, role FROM local_auth_memberships(%s)", (user_id,)
        ).fetchall()
        assert memberships == [(tenant_ids[0], str(user_id), "seller")]
        assert app.execute("SELECT local_auth_success(%s, %s)", (user_id, now)).fetchone()[0]
        assert app.execute("SELECT local_auth_memberships(%s)", (uuid4(),)).fetchone() is None


def test_login_failures_apply_progressive_lockout_without_storing_email_in_events() -> None:
    """Failure thresholds are atomic and auth events contain identifiers, never email values."""
    email = f"lock-{uuid4().hex}@synthetic.test"
    unknown_email = f"unknown-{uuid4().hex}@synthetic.test"
    now = datetime.now(UTC)
    password_hash = PasswordHasher().hash("synthetic password hash input")
    with psycopg.connect(APP_DSN) as app, app.transaction():
        app.execute("SELECT local_auth_create(%s, %s, %s)", (email, password_hash, now))
        rows = [
            app.execute(
                "SELECT * FROM local_auth_failure(%s, %s)", (email, now + timedelta(seconds=n))
            ).fetchone()
            for n in range(1, 6)
        ]
        assert [row[0] for row in rows] == [1, 2, 3, 4, 5]
        assert rows[-1][1] == now + timedelta(seconds=5) + timedelta(minutes=1)
        assert (
            app.execute(
                "SELECT local_auth_success((SELECT user_id FROM local_auth_lookup(%s)), %s)",
                (email, now + timedelta(minutes=2)),
            ).fetchone()[0]
            is False
        )
        assert (
            app.execute("SELECT * FROM local_auth_failure(%s, %s)", (unknown_email, now)).fetchone()
            is None
        )

    with psycopg.connect(ADMIN_DSN) as admin:
        events = admin.execute(
            "SELECT event, reason_code FROM local_auth_events WHERE user_id = "
            "(SELECT id FROM local_identities WHERE email = %s)",
            (email,),
        ).fetchall()
    assert events[-1] == ("login_locked", "progressive_lockout")
    with psycopg.connect(ADMIN_DSN) as admin:
        unknown_failure = admin.execute(
            "SELECT count(*) FROM local_auth_events WHERE user_id IS NULL"
            " AND event = 'login_failed' AND reason_code = 'invalid_credentials' AND at = %s",
            (now,),
        ).fetchone()[0]
        columns = {
            row[0]
            for row in admin.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'local_auth_events'"
            )
        }
    assert "email" not in columns
    assert unknown_failure == 1
