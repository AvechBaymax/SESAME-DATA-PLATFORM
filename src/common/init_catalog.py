"""Create the Postgres database that backs the Iceberg JDBC catalog, if missing.

Run by the airflow-init container:  python -m common.init_catalog
Postgres creates it on a fresh volume (infrastructure/postgres/init-iceberg-catalog.sql);
this covers volumes that existed before the Iceberg catalog was added.
The catalog tables themselves are created by Iceberg on first use.
"""
import os
import sys

import psycopg2
from psycopg2 import sql


def admin_dsn():
    """Postgres DSN from the Airflow metadata connection (same server, same user)."""
    url = os.getenv("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def main():
    dsn = admin_dsn()
    db_name = os.getenv("ICEBERG_CATALOG_DB", "iceberg_catalog")
    if not dsn.startswith("postgresql://"):
        print("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN is not a Postgres URL", file=sys.stderr)
        return 1
    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # CREATE DATABASE cannot run in a transaction
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                print(f"Iceberg catalog database exists: {db_name}")
            else:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                print(f"Created Iceberg catalog database: {db_name}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
