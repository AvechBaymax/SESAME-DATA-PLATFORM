"""Bronze -> Silver on a local Spark session (skipped without PySpark or Java).

The MERGE test also needs the Iceberg Spark runtime jar: point ICEBERG_SPARK_JAR at
iceberg-spark-runtime-3.5_2.12-<version>.jar to run it.
"""
import json
import os
import shutil
from datetime import date, datetime

import pytest

pyspark = pytest.importorskip("pyspark")
if shutil.which("java") is None:
    pytest.skip("Java is required for Spark", allow_module_level=True)

from jobs.bronze_to_silver import build  # noqa: E402
from lakehouse import quality  # noqa: E402
from lakehouse.silver import TABLES  # noqa: E402
from lakehouse.spark import create_table_if_missing, merge_into  # noqa: E402
from mock_iot.mock_api_responses import nasa_payload  # noqa: E402

ICEBERG_JAR = os.getenv("ICEBERG_SPARK_JAR", "")


def bronze_nasa(spark, files):
    """A Bronze nasa_power_raw frame: (payload, file path, ingest time) per landing file."""
    rows = [("BINHTHUAN_01", "2026-11-09", json.dumps(payload), "nasa_power", ingested, path)
            for payload, path, ingested in files]
    return spark.createDataFrame(rows, "farm_id STRING, ingest_dt STRING, payload STRING, "
                                      "_source STRING, _ingested_at TIMESTAMP, _file_path STRING")


def payload(start, end, shift=0.0):
    p = nasa_payload(11.08, 108.12, start, end, seed="BINHTHUAN_01")
    for day in p["properties"]["parameter"]["T2M_MAX"]:
        p["properties"]["parameter"]["T2M_MAX"][day] += shift
    return p


def test_overlapping_files_keep_the_newest_value_per_day(spark):
    old = (payload(date(2025, 11, 1), date(2025, 11, 10)), "s3a://l/old.json", datetime(2026, 11, 1))
    new = (payload(date(2025, 11, 6), date(2025, 11, 12), shift=1.0), "s3a://l/new.json",
           datetime(2026, 11, 2))
    df = build(spark, TABLES["fact_weather_daily"], bronze_nasa(spark, [old, new]))
    by_day = {r["date"]: r for r in df.collect()}
    assert len(by_day) == 12                                  # 1-12 Nov, no duplicates
    assert by_day[date(2025, 11, 1)]["_file_path"] == "s3a://l/old.json"
    assert by_day[date(2025, 11, 8)]["_file_path"] == "s3a://l/new.json"
    new_max = new[0]["properties"]["parameter"]["T2M_MAX"]["20251108"]
    assert by_day[date(2025, 11, 8)]["tmax_c"] == pytest.approx(new_max)


def test_failed_error_check_stops_before_writing(spark):
    bad = payload(date(2025, 11, 1), date(2025, 11, 3))
    bad["properties"]["parameter"]["T2M_MIN"]["20251102"] = 45.0   # above tmax
    df = build(spark, TABLES["fact_weather_daily"],
               bronze_nasa(spark, [(bad, "s3a://l/bad.json", datetime(2026, 11, 1))]))
    results = quality.check_table(spark, TABLES["fact_weather_daily"], df, lambda name: name)
    by_name = {r.check: r for r in results}
    assert by_name["tmin <= tmax"].failed == 1
    assert by_name["key farm_id, date, source unique"].ok
    with pytest.raises(quality.QualityError):
        quality.enforce(results)


def test_missing_foreign_key_is_an_error(spark):
    plan = {"operations": [{"operation_id": "OP1", "season_id": "NO_SUCH_SEASON", "status": "planned",
                            "op_type": "sowing", "das": 0, "planned_date": "2025-11-15"}]}
    bronze = spark.createDataFrame(
        [("F", "NO_SUCH_SEASON", json.dumps(plan), "field_operation", datetime(2026, 11, 1), "s3a://l/p.json")],
        "farm_id STRING, season_id STRING, payload STRING, _source STRING, _ingested_at TIMESTAMP, "
        "_file_path STRING")
    df = build(spark, TABLES["fact_field_operation"], bronze)
    results = quality.check_table(spark, TABLES["fact_field_operation"], df,
                                  lambda name: f"default.missing_{name}")
    assert {r.check: r.failed for r in results}["season_id exists in dim_season"] == 1


@pytest.mark.skipif(not ICEBERG_JAR, reason="set ICEBERG_SPARK_JAR to run the Iceberg MERGE test")
def test_merge_is_idempotent_and_updates_from_newer_files(spark):
    silver = TABLES["fact_weather_daily"]
    target = "test.silver.fact_weather_daily"
    spark.sql("CREATE NAMESPACE IF NOT EXISTS test.silver")
    create_table_if_missing(spark, target, silver.ddl(), silver.partitioned_by)
    first = (payload(date(2025, 11, 1), date(2025, 11, 10)), "s3a://l/a.json", datetime(2026, 11, 1))
    df = build(spark, silver, bronze_nasa(spark, [first]))

    merge_into(spark, df, target, silver.keys)
    merge_into(spark, df, target, silver.keys)          # same input again: nothing changes
    assert spark.table(target).count() == 10
    snapshots = spark.sql(f"SELECT * FROM {target}.snapshots").count()

    second = (payload(date(2025, 11, 9), date(2025, 11, 12)), "s3a://l/b.json", datetime(2026, 11, 2))
    merge_into(spark, build(spark, silver, bronze_nasa(spark, [first, second])), target, silver.keys)
    rows = {r["date"]: r["_file_path"] for r in spark.table(target).collect()}
    assert len(rows) == 12
    assert rows[date(2025, 11, 1)] == "s3a://l/a.json" and rows[date(2025, 11, 9)] == "s3a://l/b.json"
    assert spark.sql(f"SELECT * FROM {target}.snapshots").count() > snapshots
    partitions = {r[0] for r in spark.sql(f"SELECT partition.date_month FROM {target}.partitions").collect()}
    assert len(partitions) == 1                          # all in November 2025
