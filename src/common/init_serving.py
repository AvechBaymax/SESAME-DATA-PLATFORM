"""Create the shared database for the web app and the serving layer (idempotent).

Run by the airflow-init container:  python -m common.init_serving
Print the DDL for the web team:      python -m common.init_serving --print-ddl

On the Postgres server of Airflow (same admin login) it makes sure that:
  - the roles data_platform and web_app exist, with the passwords from the env
  - the database SERVING_DB (default sesame) exists
  - schema serving belongs to data_platform and web_app may read it
  - schema app belongs to web_app (its tables are the web app's) and data_platform may read it
  - the serving tables and the device_health view exist (lakehouse.serving)
"""
import argparse
import os
import sys

import psycopg2
from psycopg2 import sql

from common.init_catalog import admin_dsn
from lakehouse import serving


def _dsn_for(dsn, database):
    base, _, _ = dsn.rpartition("/")
    return f"{base}/{database}"


def ensure_role(cur, role, password):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)), (password,))


def ensure_database(cur, database):
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        print(f"Created database {database}")


def setup_schemas(cur):
    data, web = sql.Identifier(serving.DATA_ROLE), sql.Identifier(serving.WEB_ROLE)
    srv, app = sql.Identifier(serving.SCHEMA), sql.Identifier(serving.APP_SCHEMA)
    statements = [
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(srv, data),
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(app, web),
        # web_app reads serving, now and for tables created later by data_platform
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(srv, web),
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(data, srv, web),
        # data_platform reads app (export of what users enter)
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(app, data),
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(web, app, data),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(app, data),
    ]
    for statement in statements:
        cur.execute(statement)
    # Serving tables belong to data_platform, so it can truncate and refill them
    cur.execute(sql.SQL("SET ROLE {}").format(data))
    for statement in serving.ddl_statements():
        cur.execute(statement)
    cur.execute("RESET ROLE")
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(srv, web))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create the shared database, roles and serving tables")
    ap.add_argument("--print-ddl", action="store_true", help="print the serving DDL and exit")
    args = ap.parse_args(argv)
    if args.print_ddl:
        print(";\n\n".join(serving.ddl_statements()) + ";")
        return 0

    dsn = admin_dsn()
    if not dsn.startswith("postgresql://"):
        print("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN is not a Postgres URL", file=sys.stderr)
        return 1
    database = os.getenv("SERVING_DB", "sesame")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True   # CREATE DATABASE cannot run in a transaction
    try:
        with conn.cursor() as cur:
            ensure_role(cur, serving.DATA_ROLE, os.getenv("SERVING_DB_PASSWORD", "data_platform_dev"))
            ensure_role(cur, serving.WEB_ROLE, os.getenv("WEB_DB_PASSWORD", "web_app_dev"))
            ensure_database(cur, database)
            cur.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}, {}").format(
                sql.Identifier(database), sql.Identifier(serving.DATA_ROLE), sql.Identifier(serving.WEB_ROLE)))
    finally:
        conn.close()

    conn = psycopg2.connect(_dsn_for(dsn, database))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            setup_schemas(cur)
    finally:
        conn.close()
    print(f"Serving ready: database {database}, schemas {serving.SCHEMA} and {serving.APP_SCHEMA}, "
          f"{len(serving.TABLES)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
