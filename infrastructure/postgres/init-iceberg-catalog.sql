-- Runs only when the Postgres volume is created for the first time.
-- Existing volumes get the database from `python -m common.init_catalog` (airflow-init).
CREATE DATABASE iceberg_catalog OWNER airflow;
