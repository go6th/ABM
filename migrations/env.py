"""Alembic environment.

Runs migrations as the migrator (owner) role - MIGRATOR_DATABASE_URL - and executes
LangGraph checkpointer setup() once the schema exists (ADR-002). Never run the
application itself against the migrator URL.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

MIGRATOR_URL = os.environ.get(
    "MIGRATOR_DATABASE_URL",
    "postgresql://postgres:dev@localhost:5432/ai",
)


def _checkpointer_setup() -> None:
    """Create LangGraph checkpoint tables if the library is available.

    Kept optional so migrations also work without the langgraph dependency; the
    checkpoint prefix policy (I2/T10) is created defensively for the standard
    table names either way (0001_initial.py does this unconditionally).
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
    except ImportError:
        return

    import psycopg

    dsn = os.environ.get("CHECKPOINT_DATABASE_URL", MIGRATOR_URL)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        PostgresSaver(conn).setup()
        conn.commit()


def run_migrations_offline() -> None:
    context.configure(
        url=MIGRATOR_URL,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(MIGRATOR_URL, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
    if os.environ.get("ALEMBIC_RUN_CHECKPOINT_SETUP", "0") == "1":
        _checkpointer_setup()
