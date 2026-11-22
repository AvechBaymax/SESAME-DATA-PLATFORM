"""Sensor Bronze -> Silver classification on a local Spark session (skipped without PySpark or Java).

Feeds simulator messages with every injected fault type through classify() and
checks where each ends up: this is also the data-quality catch test.
"""
import json
import random
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("pyspark")
if shutil.which("java") is None:
    pytest.skip("Java is required for Spark", allow_module_level=True)

from common.farm import FarmProfile  # noqa: E402
from jobs.sensor_stream import classify, kafka_to_bronze_rows  # noqa: E402
from mock_iot.producer import inject_fault  # noqa: E402
from mock_iot.simulator import SensorNode  # noqa: E402
from mock_iot.weather import ClimatologyWeather, DiurnalWeather  # noqa: E402

FARM = FarmProfile(planting_date=date(2025, 11, 15))


def messages(n):
    weather = DiurnalWeather(ClimatologyWeather(FARM.farm_id), FARM.lat, FARM.lon, FARM.farm_id)
    node = SensorNode(FARM, FARM.device_ids[0], weather, seed=1)
    t = datetime(2025, 11, 20, tzinfo=timezone.utc)
    out = []
    for _ in range(n):
        out.append(node.step(t, t + timedelta(minutes=15)))
        t += timedelta(minutes=15)
    return out


def kafka_rows(spark, records):
    """records: (value bytes, fault or None) -> a DataFrame shaped like the Kafka source."""
    rows = [("SN", value, "sesame.sensor.raw", 0, offset, datetime(2026, 11, 17),
             [("x-mock-fault", fault.encode())] if fault else None)
            for offset, (value, fault) in enumerate(records)]
    kafka = spark.createDataFrame(rows, "key STRING, value BINARY, topic STRING, partition INT, "
                                        "offset BIGINT, timestamp TIMESTAMP, "
                                        "headers ARRAY<STRUCT<key: STRING, value: BINARY>>")
    return kafka_to_bronze_rows(kafka)


def test_each_fault_type_lands_where_it_should(spark):
    rng = random.Random(7)
    good = messages(10)
    records = [(json.dumps(m).encode(), None) for m in good]          # offsets 0-9: valid
    plan = {"malformed": 0, "missing_field": 1, "out_of_range": 2, "duplicate": 3, "late": 4}
    for fault, i in plan.items():
        records += inject_fault(good[i], fault, rng)
    stranger = dict(good[5], device_id="SN_UNKNOWN_01", timestamp=good[5]["timestamp"] + 1)
    records.append((json.dumps(stranger).encode(), None))

    devices = spark.createDataFrame([(d,) for d in FARM.device_ids], "device_id STRING")
    readings, rejected = classify(spark, kafka_rows(spark, records), devices)
    by_rule = {r["dq_rule"]: r for r in rejected.collect()}

    assert by_rule["valid_json"]["mock_fault"] == "malformed"
    assert by_rule["required_fields"]["mock_fault"] == "missing_field"
    assert any(rule in by_rule for rule in ("vwc_pct 0-60", "rh_pct 0-100", "air_temp_c 5-50"))
    assert by_rule["device_id in dim_device"]["dq_detail"] == "SN_UNKNOWN_01"
    assert len(by_rule) == 4                     # duplicate and late are not rejected
    rows = readings.collect()
    keys = {(r["device_id"], r["ts"]) for r in rows}
    assert len(keys) == len(rows) == 10 + 1      # duplicate dropped, late kept at its own ts
    late_ts = datetime.fromtimestamp(good[4]["timestamp"] - 7200, timezone.utc).replace(tzinfo=None)
    assert (FARM.device_ids[0], late_ts) in keys


def test_duplicate_keys_keep_the_first_message(spark):
    first, second = messages(1)[0], messages(1)[0]
    second["measurements"]["soil"]["vwc_pct"] = 33.3     # same device and ts, other value
    readings, _ = classify(spark, kafka_rows(spark, [(json.dumps(first).encode(), None),
                                                     (json.dumps(second).encode(), None)]))
    rows = readings.collect()
    assert len(rows) == 1 and rows[0]["kafka_offset"] == 0
    assert rows[0]["vwc_pct"] == first["measurements"]["soil"]["vwc_pct"]


def test_reading_columns_follow_the_message(spark):
    m = messages(1)[0]
    readings, rejected = classify(spark, kafka_rows(spark, [(json.dumps(m).encode(), None)]))
    assert rejected.count() == 0
    r = readings.first()
    assert r["device_id"] == m["device_id"] and r["farm_id"] == FARM.farm_id
    assert r["vwc_pct"] == m["measurements"]["soil"]["vwc_pct"]
    assert r["air_temp_c"] == m["measurements"]["weather_micro"]["ambient_temp_c"]
    assert r["irrigation_active"] == m["flags"]["irrigation_active"]
    assert r["kafka_offset"] == 0


def test_saturated_soil_is_a_valid_reading_but_positive_potential_is_not(spark):
    saturated, impossible = messages(2)
    saturated["measurements"]["soil"]["water_potential_kpa"] = -0.0   # what the simulator sends when saturated
    impossible["measurements"]["soil"]["water_potential_kpa"] = 5.0
    readings, rejected = classify(spark, kafka_rows(spark, [(json.dumps(saturated).encode(), None),
                                                            (json.dumps(impossible).encode(), None)]))
    assert readings.count() == 1
    assert [r["dq_rule"] for r in rejected.collect()] == ["water_potential_kpa <= 0"]
