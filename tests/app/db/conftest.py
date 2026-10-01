"""A fresh, empty Postgres 16 database for each test that asks for one.

Where the server comes from, in order:
1. ``TEST_DATABASE_URL``: an admin URL to a running server (CI uses a postgres:16 service).
   A uniquely named database is created per test and dropped afterwards.
2. ``PG_BIN``: a directory holding ``initdb`` and ``pg_ctl``. A throwaway cluster is started
   for the test session in a temp directory, on a free port, and stopped at the end.
3. Neither: database tests are skipped with the reason shown.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

ADMIN_DB = "postgres"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(url, connect_timeout=2):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


@pytest.fixture(scope="session")
def pg_admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Admin URL (psycopg form) to a server where the tests may create databases."""
    given = os.environ.get("TEST_DATABASE_URL", "").strip()
    if given:
        _wait_ready(given)
        yield given
        return

    pg_bin = os.environ.get("PG_BIN", "").strip()
    if not pg_bin:
        pytest.skip("no database: set TEST_DATABASE_URL or PG_BIN (see tests/app/db/conftest.py)")
    initdb, pg_ctl = Path(pg_bin) / "initdb", Path(pg_bin) / "pg_ctl"
    if not (initdb.exists() and pg_ctl.exists()):
        pytest.skip(f"PG_BIN={pg_bin} has no initdb and pg_ctl")

    root = tmp_path_factory.mktemp("pg")
    data, sock = root / "data", root / "sock"
    sock.mkdir()
    port = _free_port()
    subprocess.run(  # noqa: S603
        [str(initdb), "-D", str(data), "-U", "postgres", "-A", "trust", "--no-sync"],
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603
        [
            str(pg_ctl),
            "-D",
            str(data),
            "-l",
            str(root / "server.log"),
            "-w",
            "start",
            "-o",
            f"-p {port} -k {sock} -c fsync=off -c listen_addresses=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    url = f"postgresql://postgres@127.0.0.1:{port}/{ADMIN_DB}"
    try:
        _wait_ready(url)
        yield url
    finally:
        subprocess.run(  # noqa: S603
            [str(pg_ctl), "-D", str(data), "-m", "fast", "stop"], check=False, capture_output=True
        )
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def empty_database_url(pg_admin_url: str) -> Iterator[str]:
    """URL of a brand new, empty database; dropped after the test."""
    name = f"t_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(pg_admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(pg_admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def migrated_database_url(
    empty_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """A fresh database upgraded to the latest data layer migration."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[3] / "app" / "db" / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    command.upgrade(cfg, "head")
    yield empty_database_url
