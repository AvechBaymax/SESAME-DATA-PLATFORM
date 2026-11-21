"""PoC bootstrap: load one full season of every demo farm into Landing, Bronze and Silver.

Trigger manually once. For each farm in common.farm.DEMO_FARMS (one task group
per farm) it uploads the farm/season config and field plan, fetches SoilGrids
and the NASA POWER weather for the season; the reference data is uploaded once.
One Spark task per source then loads all farms into Bronze and builds the
Silver tables, and a last one recomputes Gold. The farm dimensions are built
before the Silver tables that reference them (soil layers, field operations).

The farms are sown between 15/11 and 20/12/2025 (Dong Xuan), so the NASA window
covers 01/11/2025 to 15/03/2026, which has real NASA weather.
"""
from datetime import datetime

from airflow import DAG
from airflow.models.param import Param
from airflow.utils.task_group import TaskGroup

from common.farm import DEMO_FARMS, farm_cli_args
from sesame_tasks import bronze, gold, python_module, silver

with DAG(
    dag_id="sesame_poc_bootstrap",
    description="Load one season of every demo farm (config, soil, NASA weather) and the "
                "reference data into Bronze and Silver, then build Gold",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    tags=["sesame", "poc"],
    params={
        "nasa_start": Param("20251101", type="string", description="NASA POWER start YYYYMMDD"),
        "nasa_end": Param("20260315", type="string", description="NASA POWER end YYYYMMDD"),
    },
) as dag:
    configs, soils, weathers = [], [], []
    for farm in DEMO_FARMS:
        with TaskGroup(group_id=farm.farm_id, tooltip=f"{farm.farm_id}: {farm.lat}, {farm.lon}"):
            location = f"--farm-id {farm.farm_id} --lat {farm.lat} --lon {farm.lon}"
            configs.append(python_module("upload_user_config", "mock_iot.user_config_mock",
                                         farm_cli_args(farm)))
            soils.append(python_module("ingest_soilgrids", "mock_iot.isric_ingest", location))
            weathers.append(python_module(
                "ingest_nasa_season", "mock_iot.nasa_ingest",
                f"{location} --start {{{{ params.nasa_start }}}} --end {{{{ params.nasa_end }}}}"))
    reference = python_module("upload_reference", "mock_iot.mock_fao_db")

    farm_dims = silver("user_config")  # dim_farm, dim_plot, dim_season, dim_device
    field_operations = silver("field_operation")
    soil_layers = silver("soilgrids")
    crop_reference = silver("reference")  # dim_growth_stage, dim_crop
    weather_daily = silver("nasa_power")

    configs >> bronze("user_config", "all_done") >> farm_dims
    configs >> bronze("field_operation", "all_done") >> field_operations
    reference >> bronze("reference") >> crop_reference
    soils >> bronze("soilgrids", "all_done") >> soil_layers
    weathers >> bronze("nasa_power", "all_done") >> weather_daily
    farm_dims >> [field_operations, soil_layers]  # their foreign keys point at the farm dimensions
    [farm_dims, field_operations, soil_layers, crop_reference, weather_daily] >> gold()
