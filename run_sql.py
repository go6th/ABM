import os
from pathlib import Path

import psycopg

with psycopg.connect(os.environ["ADMIN_DSN"]) as conn:
    query = (Path(__file__).parent / "scripts/check_rls_catalog.sql").read_text()
    print(conn.execute(query).fetchall())
