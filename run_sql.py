import os

import psycopg

with psycopg.connect(os.environ["ADMIN_DSN"]) as conn:
    print(conn.execute(open("scripts/check_rls_catalog.sql").read()).fetchall())
