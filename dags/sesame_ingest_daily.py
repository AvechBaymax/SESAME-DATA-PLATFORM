"""Daily ingest of external weather APIs into Landing and Bronze, NASA POWER on to Silver.

Runs at 23:00 UTC (06:00 in Vietnam). NASA POWER lags a few days, so the
ingester fetches the window from 14 to 7 days ago. OpenWeatherMap is skipped
when OWM_API_KEY is not set. OpenWeatherMap stops at Bronze: one current-weather
snapshot a day is not a daily minimum/maximum/total, so it has no Silver table yet.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

from sesame_tasks import bronze, python_module, silver

with DAG(
    dag_id="sesame_ingest_daily",
    description="NASA POWER and OpenWeatherMap -> Landing -> Bronze (NASA -> Silver)",
    start_date=datetime(2026, 11, 1),
    schedule="0 23 * * *",
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["sesame", "ingest"],
) as dag:
    nasa = python_module("ingest_nasa", "mock_iot.nasa_ingest")
    owm = BashOperator(
        task_id="ingest_owm",
        # exit code 99 marks the task as skipped
        bash_command='[ -n "$OWM_API_KEY" ] || { echo "OWM_API_KEY not set"; exit 99; }; '
                     "python -m mock_iot.owm_ingest",
    )
    nasa >> bronze("nasa_power") >> silver("nasa_power")
    owm >> bronze("owm")
