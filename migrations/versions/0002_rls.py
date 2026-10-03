"""Alembic revision 0002: upgrade runs
``0002_rls.sql``; downgrade runs its ``.down.sql`` (where one exists).
The SQL files hold the schema and its comments."""

import os
from pathlib import Path

import psycopg
from alembic import op
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    checkpoint_url = os.environ.get("MIGRATOR_DATABASE_URL")
    if not checkpoint_url:
        raise RuntimeError("MIGRATOR_DATABASE_URL must be set for migration 0002")
    password = os.environ.get("APP_RW_PASSWORD")
    if not password:
        raise RuntimeError("APP_RW_PASSWORD must be set to configure the app_rw runtime role")
    with psycopg.connect(checkpoint_url, autocommit=True, prepare_threshold=0) as connection:
        PostgresSaver(connection).setup()
    migration_sql = Path(__file__).resolve().parents[1] / "0002_rls.sql"
    op.execute(migration_sql.read_text())
    connection = op.get_bind().connection.driver_connection
    connection.execute(sql.SQL("ALTER ROLE app_rw PASSWORD {}").format(sql.Literal(password)))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_runs, chunks CASCADE")
