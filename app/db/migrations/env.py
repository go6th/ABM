"""Alembic environment for data layer v1.

- Connection URL from DATABASE_URL only (never from alembic.ini).
- Imports every model so autogenerate sees the full metadata.
- Limited to the ``intel`` and ``intel_internal`` schemas; legacy ``public`` tables are ignored,
  so autogenerate can never propose dropping them.
- Own version table, ``public.intel_alembic_version``, so it never collides with the legacy chain.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base
from app.db.migrations_support import VERSION_TABLE, VERSION_TABLE_SCHEMA, include_name
from app.db.url import database_url_from_env

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

CONFIGURE_ARGS: dict[str, Any] = {
    "target_metadata": target_metadata,
    "include_schemas": True,
    "include_name": include_name,
    "compare_type": True,
    "compare_server_default": True,
    "version_table": VERSION_TABLE,
    "version_table_schema": VERSION_TABLE_SCHEMA,
}


def run_migrations_offline() -> None:
    context.configure(
        url=database_url_from_env().render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_ARGS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, **CONFIGURE_ARGS)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url_from_env(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **CONFIGURE_ARGS)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
