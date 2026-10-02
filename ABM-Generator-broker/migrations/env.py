from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

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
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
