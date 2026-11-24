"""Serving tables stay in step with the lakehouse tables they copy."""
from datetime import date

import pytest

from lakehouse import gold, sensor, serving
from lakehouse.silver import TABLES as SILVER

COPIES = {  # serving table -> columns of the lakehouse table it is copied from
    "season_daily": gold.FEAT_SEASON_DAILY.column_names,
    "region_daily": gold.AGG_REGION_DAILY.column_names,
    "season_summary": gold.AGG_SEASON_SUMMARY.column_names,
    "alerts": gold.ALERT_EVENT.column_names,
    "field_operations": SILVER["fact_field_operation"].column_names,
    "soil_layers": SILVER["dim_soil_layer"].column_names,
    "sensor_recent": [c.split(" ")[0] for c in sensor.READING_DDL.split(", ")],
}


@pytest.mark.parametrize("name", sorted(COPIES))
def test_copied_tables_only_use_existing_columns(name):
    missing = set(serving.TABLES[name].column_names) - set(COPIES[name]) - {"updated_at"}
    assert not missing, f"serving.{name} columns not in its source: {missing}"


def test_every_table_has_its_keys_and_an_idempotent_ddl():
    ddl = serving.ddl_statements()
    for table in serving.TABLES.values():
        assert set(table.keys) <= set(table.column_names)
        assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {table.qualified} ") for s in ddl)
    assert all("IF NOT EXISTS" in s or s.startswith("CREATE OR REPLACE VIEW") for s in ddl)
    assert "serving.device_health" in ddl[-1]


def test_stream_moves_the_stage_without_duplicates_and_keeps_seven_days():
    insert, prune = serving.recent_upsert_sql()
    assert "ON CONFLICT (device_id, ts) DO NOTHING" in insert
    assert f"interval '{serving.RECENT_DAYS} days'" in prune
    assert "sensor_recent" not in {t.name for t in serving.BATCH_TABLES}


def test_jdbc_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("SERVING_JDBC_URL", raising=False)
    assert serving.jdbc_settings() is None
    monkeypatch.setenv("SERVING_JDBC_URL", "jdbc:postgresql://db:5432/sesame")
    monkeypatch.setenv("SERVING_DB_PASSWORD", "pw")
    assert serving.jdbc_settings()["user"] == "data_platform"


def test_print_ddl(capsys):
    from common.init_serving import main

    assert main(["--print-ddl"]) == 0
    assert "CREATE TABLE IF NOT EXISTS serving.farm_status" in capsys.readouterr().out


def test_today_stage_follows_the_growth_stages(spark):
    from jobs.gold_to_serving import today_stage
    from common.crop import SESAME_FAO
    from pyspark.sql import functions as F

    stages = SILVER["dim_growth_stage"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})
    seasons = spark.createDataFrame([(date(2025, 11, 15), 75)], "planting_date DATE, season_length_days INT")
    row = lambda d: today_stage(seasons, stages, F.lit(d).cast("date")).first()  # noqa: E731
    assert (row("2025-11-10")["today_stage"], row("2025-11-10")["today_das"]) == ("planned", -5)
    assert row("2025-12-25")["today_stage"] == "mid_season"
    assert row("2026-03-01")["today_stage"] == "harvested"
