"""Alembic environment: runs the SQL migrations as the migrator role (MIGRATOR_DATABASE_URL)."""

from __future__ import annotations

import os
import re
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, create_engine, event, pool

from account_intelligence.db import models  # noqa: F401  imported for the metadata side effect
from account_intelligence.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Autogenerate sees every model through Base.metadata and emits names that follow
# account_intelligence.db.base NAMING_CONVENTION. compare_type/compare_server_default catch column
# drift between the models and the migrations in `alembic check`.
target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Compare only modelled tables. Starter, library (LangGraph checkpoints) and
    partitioned vector tables exist only in SQL migrations and are not drift."""
    # The account_runs table is an external lifecycle table; Source references its composite key
    # for account-run integrations, but the application does not model the table in this metadata.
    if type_ == "table" and name == "account_runs":
        return False
    return not (type_ == "table" and reflected and compare_to is None)


url = os.environ.get("MIGRATOR_DATABASE_URL")
if not url:
    raise RuntimeError("MIGRATOR_DATABASE_URL must be set for migrations")
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            superuser = connection.exec_driver_sql(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            ).scalar()
            owners: set[str] = set()
            if not superuser:
                _grant_owner_changes(connection, owners)
            context.run_migrations()
            if not superuser:
                for role in sorted(owners):
                    connection.exec_driver_sql(f'REVOKE "{role}" FROM CURRENT_USER')
                connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    engine.dispose()


OWNER_CHANGE = re.compile(r"^ALTER FUNCTION\b[^;]*?\bOWNER TO ([a-z_][a-z0-9_]*)\s*;", re.M)


def _grant_owner_changes(connection: Connection, owners: set[str]) -> None:
    """Let a non-superuser migrator (Cloud SQL's) work on functions the NOLOGIN definer roles own.

    Changing an owner needs the migrator to be able to SET ROLE to the new owner and the new owner
    to hold CREATE on the schema; replacing or dropping a definer's function needs the migrator to
    hold the owner's privileges. A superuser skips all three checks. On PostgreSQL 16 the creator
    of a role holds only ADMIN on it, so for this transaction the migrator is made a member (SET
    and INHERIT) of every definer that already owns a function, and of each new owner just before
    its ``ALTER FUNCTION ... OWNER TO role;``; CREATE goes to PUBLIC. Every migration runs in the
    one transaction above and all of it is revoked before it commits, so no other session ever
    sees it (0070 still grants and revokes its own; revoking again only warns).
    """
    existing = connection.exec_driver_sql(
        "SELECT DISTINCT r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND r.rolname <> current_user AND NOT r.rolcanlogin"
        " AND pg_has_role(current_user, r.oid, 'MEMBER WITH ADMIN OPTION')"
    ).scalars()
    for role in existing:
        owners.add(role)
        connection.exec_driver_sql(f'GRANT "{role}" TO CURRENT_USER WITH SET TRUE, INHERIT TRUE')

    def grant_first(
        conn: Connection, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> tuple[str, Any]:
        def with_grant(m: re.Match[str]) -> str:
            owners.add(m.group(1))
            return (
                f'GRANT "{m.group(1)}" TO CURRENT_USER WITH SET TRUE, INHERIT TRUE;\n{m.group(0)}'
            )

        return OWNER_CHANGE.sub(with_grant, statement), params

    event.listen(connection, "before_cursor_execute", grant_first, retval=True)
    connection.exec_driver_sql("GRANT CREATE ON SCHEMA public TO PUBLIC")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
