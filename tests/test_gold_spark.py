"""Silver -> Gold builders on a local Spark session (skipped without PySpark or Java)."""
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("pyspark")

from common.crop import SESAME_FAO  # noqa: E402
from common.farm import FarmProfile  # noqa: E402
from jobs.sensor_stream import classify, kafka_to_bronze_rows  # noqa: E402
from jobs.silver_to_gold import build_feat, build_region, build_summary, season_days, sensor_daily  # noqa: E402
from lakehouse import gold, quality  # noqa: E402
from lakehouse.silver import TABLES as SILVER  # noqa: E402
from mock_iot.simulator import SensorNode  # noqa: E402
from mock_iot.weather import ClimatologyWeather, DiurnalWeather  # noqa: E402

FARM = FarmProfile(planting_date=date(2025, 11, 15), device_count=2)
STAGES = SILVER["dim_growth_stage"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})
CROP = SILVER["dim_crop"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})[0]


def season_frame(spark):
    return spark.createDataFrame(
        [(FARM.season_id, FARM.plot_id, FARM.farm_id, "sesame", FARM.planting_date,
          FARM.planting_date + timedelta(days=75), FARM.lat, FARM.soil_texture, "Bình Thuận")],
        "season_id STRING, plot_id STRING, farm_id STRING, crop_id STRING, planting_date DATE, "
        "harvest_date DATE, latitude DOUBLE, soil_texture STRING, province STRING")


def weather_frame(spark, last_day):
    rng = random.Random(1)
    rows, day = [], date(2025, 11, 1)
    while day <= last_day:
        rows.append((FARM.farm_id, day, "nasa_power", 22.0, 31.0, 72.0, 3.5, 20.0,
                     rng.choice([0.0, 0.0, 0.0, 6.0])))
        day += timedelta(days=1)
    return spark.createDataFrame(rows, "farm_id STRING, date DATE, source STRING, tmin_c DOUBLE, "
                                       "tmax_c DOUBLE, rh_pct DOUBLE, wind_2m_m_s DOUBLE, "
                                       "rs_mj_m2 DOUBLE, precip_mm DOUBLE")


def sensor_frame(spark, days):
    """Silver fact_sensor_reading rows from the simulator, via the stream's own classify()."""
    weather = DiurnalWeather(ClimatologyWeather(FARM.farm_id), FARM.lat, FARM.lon, FARM.farm_id)
    nodes = [SensorNode(FARM, d, weather, seed=i) for i, d in enumerate(FARM.device_ids)]
    t = datetime(2025, 11, 14, 17, tzinfo=timezone.utc)   # 00:00 on 15/11 in Vietnam
    records = []
    for _ in range(days * 96):
        for node in nodes:
            records.append((json.dumps(node.step(t, t + timedelta(minutes=15))).encode(), None))
        t += timedelta(minutes=15)
    rows = [("SN", v, "sesame.sensor.raw", 0, i, datetime(2026, 11, 17), None) for i, (v, _) in enumerate(records)]
    kafka = spark.createDataFrame(rows, "key STRING, value BINARY, topic STRING, partition INT, offset BIGINT, "
                                        "timestamp TIMESTAMP, headers ARRAY<STRUCT<key: STRING, value: BINARY>>")
    readings, rejected = classify(spark, kafka_to_bronze_rows(kafka))
    assert rejected.count() == 0
    return readings


def test_sensor_daily_aggregates_per_plot_and_local_day(spark):
    readings = sensor_frame(spark, 2)
    devices = spark.createDataFrame([(d, FARM.plot_id) for d in FARM.device_ids], "device_id STRING, plot_id STRING")
    daily = {r["date"]: r for r in sensor_daily(readings, devices).collect()}
    first = daily[date(2025, 11, 15)]
    assert first["sensor_coverage"] == pytest.approx(1.0)          # 2 devices x 96 readings
    assert 0 < first["vwc_min_pct"] <= first["vwc_mean_pct"] <= first["vwc_max_pct"]
    assert first["irrigation_mm"] >= 0 and first["sensor_rain_mm"] >= 0


def test_feat_summary_and_region_from_silver_frames(spark):
    seasons = season_frame(spark)
    weather = weather_frame(spark, date(2026, 2, 10))
    readings = sensor_frame(spark, 3)
    devices = spark.createDataFrame([(d, FARM.plot_id) for d in FARM.device_ids], "device_id STRING, plot_id STRING")
    days = season_days(seasons, weather, sensor_daily(readings, devices))
    feat = build_feat(spark, seasons, days, {"sesame": STAGES}, {"sesame": CROP}).cache()

    rows = feat.orderBy("date").collect()
    assert len(rows) == 76 and rows[0]["das"] == 0 and rows[-1]["stage"] == "late_season"
    assert rows[0]["rain_source"] == "sensor" and rows[10]["rain_source"] == "nasa_power"
    assert rows[0]["vwc_mean_pct"] is not None and rows[10]["vwc_mean_pct"] is None
    ok = quality.check_table(spark, gold.FEAT_SEASON_DAILY, feat, lambda n: f"default.missing_{n}")
    assert all(r.ok for r in ok if "dim_season" not in r.check)

    summary = build_summary(feat, seasons, weather).first()
    assert summary["days"] == 76 and summary["status"] == "completed"
    assert summary["etc_total_mm"] == pytest.approx(sum(r["etc_mm"] for r in rows))
    assert 0 < summary["eta_etc_ratio"] <= 1
    assert summary["rain_mm_harvest_window"] is not None

    region = build_region(feat, seasons).orderBy("date").collect()
    assert len(region) == 76 and region[0]["farms_active"] == 1 and region[0]["province"] == "Bình Thuận"
