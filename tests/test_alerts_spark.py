"""Alert rules and episodes on a local Spark session (skipped without PySpark or Java)."""
import json
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("pyspark")

from common.crop import SESAME_FAO  # noqa: E402
from jobs.silver_to_gold import build_alerts  # noqa: E402
from lakehouse import alerts, gold, quality  # noqa: E402
from lakehouse.silver import TABLES as SILVER  # noqa: E402

STAGES = SILVER["dim_growth_stage"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})
CROP = SILVER["dim_crop"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})[0]
PLANTING = date(2025, 11, 15)
HARVEST = PLANTING + timedelta(days=75)
SEASON = {"season_id": "S1", "farm_id": "F1", "plot_id": "P1", "planting_date": PLANTING,
          "harvest_date": HARVEST, "latitude": 11.08, "soil_texture": "loamy_sand"}
DRY = {"tmin_c": 22.0, "tmax_c": 31.0, "rh_pct": 72.0, "wind_2m_m_s": 3.5, "rs_mj_m2": 20.0,
       "nasa_precip_mm": 0.0, "sensor_coverage": 1.0, "soil_oxygen_min_pct": 20.0}


def feat_frame(spark, overrides):
    """feat_season_daily rows from the real water balance: irrigated every 3 days except the overrides."""
    days = []
    for i in range(76):
        d = {"date": PLANTING + timedelta(days=i), **DRY, "irrigation_mm": 15.0 if i % 3 == 0 else 0.0}
        d.update(overrides.get(i, {}))
        days.append(d)
    rows = gold.water_balance(SEASON, days, STAGES, CROP)
    columns = [c for c in gold.FEAT_SEASON_DAILY.column_names if c != "_updated_at"]
    ddl = ", ".join(f"{n} {k}" for n, k in gold.FEAT_SEASON_DAILY.columns if n != "_updated_at")
    return spark.createDataFrame([tuple(r[c] for c in columns) for r in rows], ddl)


def season_frame(spark):
    return spark.createDataFrame([("S1", "P1", "F1", "sesame", PLANTING, HARVEST, 11.08, "loamy_sand", "Bình Thuận")],
                                 "season_id STRING, plot_id STRING, farm_id STRING, crop_id STRING, "
                                 "planting_date DATE, harvest_date DATE, latitude DOUBLE, soil_texture STRING, "
                                 "province STRING")


def weather_frame(spark, rain_on=()):
    rows, day = [], PLANTING
    while day <= HARVEST + timedelta(days=10):
        rows.append(("F1", day, "nasa_power", 15.0 if day in rain_on else 0.0))
        day += timedelta(days=1)
    return spark.createDataFrame(rows, "farm_id STRING, date DATE, source STRING, precip_mm DOUBLE")


def by_rule(df):
    out = {}
    for r in df.collect():
        out.setdefault(r["rule"], []).append(r)
    return out


@pytest.fixture(scope="module")
def scenario_alerts(spark):
    no_irrigation = {i: {"irrigation_mm": 0.0} for i in range(40, 62)}           # pump broken at flowering
    overrides = dict(no_irrigation)
    overrides[20] = {"soil_oxygen_min_pct": 6.0, "nasa_precip_mm": 0.0}          # waterlogged 2 days
    overrides[21] = {"soil_oxygen_min_pct": 8.0}
    overrides[30] = {"sensor_coverage": None}                                    # node silent 2 days
    overrides[31] = {"sensor_coverage": None}
    overrides[45] = {**no_irrigation[45], "nasa_precip_mm": 40.0}            # heavy rain at flowering
    feat = feat_frame(spark, overrides)
    weather = weather_frame(spark, rain_on={HARVEST + timedelta(days=2)})
    plan = [("S1_fertilizer_015", "S1", "P1", "fertilizer", 15, PLANTING + timedelta(days=15), None, "planned",
             json.dumps({"application": "topdress", "products_kg_ha": {"urea": 86.7, "kcl": 33.3}}))]
    ops = spark.createDataFrame(plan, "operation_id STRING, season_id STRING, plot_id STRING, op_type STRING, "
                                      "das INT, planned_date DATE, actual_date DATE, status STRING, details STRING")
    readings = spark.createDataFrame(
        [("SN1", "P1", datetime(2025, 12, 1, 3), 15.0), ("SN1", "P1", datetime(2025, 12, 2, 3), 12.0)],
        "device_id STRING, plot_id STRING, ts TIMESTAMP, battery_pct DOUBLE")
    return build_alerts(feat, season_frame(spark), weather, ops, readings).cache()


def test_pump_failure_at_flowering_is_one_critical_episode(scenario_alerts):
    rules = by_rule(scenario_alerts)
    drought = rules["drought_flowering"]
    assert len(drought) == 1                                   # consecutive days merged
    episode = drought[0]
    assert episode["severity"] == "critical" and episode["days"] > 1
    assert episode["start_date"] >= PLANTING + timedelta(days=40)
    assert episode["end_date"] <= PLANTING + timedelta(days=64)  # flowering = FAO mid-season
    assert episode["value"] > episode["threshold"] == pytest.approx(60.0)
    assert "Thiếu nước lúc ra hoa" in episode["message"] and "{" not in episode["message"]


def test_water_alerts_never_report_a_value_below_their_threshold(scenario_alerts):
    for rule in ("drought_flowering", "water_stress"):
        for episode in by_rule(scenario_alerts).get(rule, []):
            assert episode["value"] > episode["threshold"]


def test_waterlogging_reports_the_lowest_oxygen(scenario_alerts):
    episode, = by_rule(scenario_alerts)["waterlogging"]
    assert (episode["start_date"], episode["days"]) == (PLANTING + timedelta(days=20), 2)
    assert episode["value"] == pytest.approx(6.0) and episode["severity"] == "critical"
    assert "6.0 %" in episode["message"]


def test_heavy_rain_at_flowering_and_after_harvest(scenario_alerts):
    rules = by_rule(scenario_alerts)
    assert rules["heavy_rain_flowering"][0]["value"] == pytest.approx(40.0)
    assert rules["rain_harvest_window"][0]["start_date"] == HARVEST + timedelta(days=2)


def test_sensor_gap_inside_the_data_range_is_flagged(scenario_alerts):
    episode, = by_rule(scenario_alerts)["low_sensor_coverage"]
    assert (episode["start_date"], episode["days"], episode["value"]) == (PLANTING + timedelta(days=30), 2, 0.0)


def test_device_and_operation_alerts(scenario_alerts):
    rules = by_rule(scenario_alerts)
    battery, = rules["battery_low"]
    assert battery["device_id"] == "SN1" and battery["days"] == 2 and battery["value"] == pytest.approx(12.0)
    due, = rules["operation_due"]
    assert due["severity"] == "info" and due["start_date"] == PLANTING + timedelta(days=15)
    assert due["message"] == "Đến hạn bón thúc (ngày 15 sau gieo): urê 86.7 kg/ha, KCl 33.3 kg/ha."
    assert "operation_overdue" not in rules          # past season: no overdue nagging


def test_alert_table_passes_its_checks_and_ids_are_unique(spark, scenario_alerts):
    results = quality.check_table(spark, gold.ALERT_EVENT, scenario_alerts, lambda n: f"default.missing_{n}")
    assert all(r.ok for r in results if "dim_season" not in r.check)
    assert set(scenario_alerts.columns) == set(gold.ALERT_EVENT.column_names)
    assert {r["rule"] for r in scenario_alerts.collect()} <= set(alerts.all_rule_names())


def test_quiet_season_raises_only_reminders(spark):
    calm = build_alerts(feat_frame(spark, {}), season_frame(spark), weather_frame(spark))
    assert {r["rule"] for r in calm.collect()} == set()
