"""The user's own purge (migration 0113): Delete in the app erases the caller's own deleted
account through ``purge_own_deleted_account`` and nothing else (SYNTHETIC data only).

GUARANTEE(test_only_the_callers_own_deleted_account_in_the_callers_tenant_is_erased)
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors

from tests.conftest import ADMIN_DSN, APP_DSN
from tests.db.purge_world import plant_scope

pytestmark = pytest.mark.requires_postgres

OWN = "SELECT purge_own_deleted_account(%s, %s, %s)"


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    """Autocommit superuser connection to the scratch database."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        yield conn


def _tenant(admin: psycopg.Connection[Any]) -> UUID:
    tid: UUID = admin.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"own-purge-{uuid4().hex[:6]}",)
    ).fetchone()[0]  # type: ignore[index]
    admin.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tid,))
    return tid


def _owner(admin: psycopg.Connection[Any], account: UUID) -> str:
    row = admin.execute("SELECT owner_user_id FROM accounts WHERE id = %s", (account,)).fetchone()
    assert row is not None
    return str(row[0])


def _as_user(
    tenant: UUID | None, user: str | None, account: UUID, execute: bool, scope: str = "user"
) -> dict[str, Any]:
    """One call as the application role in its own transaction, with the session settings the
    API's tenant transaction sets (``db.session.bind_tenant``)."""
    with psycopg.connect(APP_DSN) as app, app.transaction():
        app.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true),"
            " set_config('app.scope', %s, true)",
            (str(tenant) if tenant else "", user or "", scope),
        )
        row = app.execute(OWN, (account, execute, False)).fetchone()
    assert row is not None
    out: dict[str, Any] = row[0]
    return out


def test_only_the_callers_own_deleted_account_in_the_callers_tenant_is_erased(
    admin: psycopg.Connection[Any],
) -> None:
    t1, t2 = _tenant(admin), _tenant(admin)
    domain = f"own-{secrets.token_hex(4)}.example"
    gone = plant_scope(admin, t1, domain, deleted=True)
    live = plant_scope(admin, t1, f"live-{domain}", deleted=False)
    owner, live_owner = _owner(admin, gone.account), _owner(admin, live.account)

    # no signed-in user transaction: no tenant, no user, or the system scope
    for tenant, user, scope in ((None, owner, "user"), (t1, None, "user"), (t1, owner, "system")):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_user(tenant, user, gone.account, False, scope)
    # someone else's account, the right account in another tenant, a live account, a stranger
    for tenant, user, account in (
        (t1, live_owner, gone.account),
        (t2, owner, gone.account),
        (t1, live_owner, live.account),
        (t1, owner, uuid4()),
    ):
        with pytest.raises(errors.NoDataFound):
            _as_user(tenant, user, account, True)
    # the operator's function stays out of the application's reach
    with psycopg.connect(APP_DSN) as app, pytest.raises(errors.InsufficientPrivilege):
        app.execute("SELECT purge_deleted_account(%s, %s, false, 'x', false)", (t1, gone.account))
    assert admin.execute(
        "SELECT count(*) FROM facts WHERE account_id = %s", (gone.account,)
    ).fetchone() != (0,)

    dry = _as_user(t1, owner, gone.account, False)
    assert dry["executed"] is False and dry["rows_total"] > 0
    done = _as_user(t1, owner, gone.account, True)
    assert done["executed"] is True and done["rows_total"] == dry["rows_total"]
    row = admin.execute("SELECT domain FROM accounts WHERE id = %s", (gone.account,)).fetchone()
    assert row is not None and str(row[0]).startswith("deleted-")
    # the content goes; the billing record of the run stays, emptied (config/deletion.yaml)
    for account, facts in ((gone.account, False), (live.account, True)):
        found = admin.execute("SELECT count(*) FROM facts WHERE account_id = %s", (account,))
        assert (found.fetchone() != (0,)) is facts
    run = admin.execute("SELECT run_input FROM account_runs WHERE account_id = %s", (gone.account,))
    assert run.fetchone() == ({},)
    actor = admin.execute(
        "SELECT count(*) FROM audit_events WHERE tenant_id = %s AND actor = %s",
        (t1, f"user:{owner}"),
    ).fetchone()
    assert actor is not None and actor[0] >= 1
