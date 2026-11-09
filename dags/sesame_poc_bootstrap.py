"""PoC bootstrap: load one full simulated season into Landing and Bronze.

Trigger manually once. It uploads the farm/season config and reference data,
fetches SoilGrids and the NASA POWER weather for the season, then loads
everything into Bronze. The season defaults to Dong Xuan 2025 (sown 15/11/2025),
which has real NASA weather.
"""
from datetime import datetime

from airflow import DAG
from airflow.models.param import Param

from sesame_tasks import bronze, python_module

with DAG(
    dag_id="sesame_poc_bootstrap",
    description="Load one simulated season (config, reference, soil, NASA weather) into Bronze",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    tags=["sesame", "poc"],
    params={
        "planting_date": Param("2025-11-15", type="string", description="Sowing date YYYY-MM-DD"),
        "nasa_start": Param("20251101", type="string", description="NASA POWER start YYYYMMDD"),
        "nasa_end": Param("20260215", type="string", description="NASA POWER end YYYYMMDD"),
    },
) as dag:
    config = python_module("upload_user_config", "mock_iot.user_config_mock",
                           "--planting-date {{ params.planting_date }}")
    reference = python_module("upload_reference", "mock_iot.mock_fao_db")
    soil = python_module("ingest_soilgrids", "mock_iot.isric_ingest")
    weather = python_module("ingest_nasa_season", "mock_iot.nasa_ingest",
                            "--start {{ params.nasa_start }} --end {{ params.nasa_end }}")

    config >> [bronze("user_config"), bronze("field_operation")]
    reference >> bronze("reference")
    soil >> bronze("soilgrids")
    weather >> bronze("nasa_power")
