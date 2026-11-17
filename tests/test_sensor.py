"""Sensor stream definitions against the messages the simulator produces."""
import json
from datetime import date, datetime, timedelta, timezone

from common.farm import DEMO_FARMS, FarmProfile
from lakehouse import sensor
from mock_iot.producer import main
from mock_iot.simulator import SensorNode
from mock_iot.weather import ClimatologyWeather, DiurnalWeather


def message():
    farm = FarmProfile(planting_date=date(2025, 11, 15))
    weather = DiurnalWeather(ClimatologyWeather(farm.farm_id), farm.lat, farm.lon, farm.farm_id)
    node = SensorNode(farm, farm.device_ids[0], weather, seed=1)
    t = datetime(2025, 11, 20, 3, tzinfo=timezone.utc)
    return node.step(t, t + timedelta(minutes=15))


def test_every_silver_field_exists_in_a_simulated_message():
    m = message()
    for column, (_, path) in sensor.READING_FIELDS.items():
        assert sensor.message_path(m, path) is not None, f"{column}: {path} missing"


def test_every_message_field_is_in_the_parse_schema():
    # each leaf the simulator writes is declared, so from_json does not drop it silently
    def leaves(d, prefix=""):
        for key, value in d.items():
            if isinstance(value, dict):
                yield from leaves(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}"
    schema = sensor.MESSAGE_SCHEMA.replace(" ", "")
    for leaf in leaves(message()):
        assert leaf.split(".")[-1] in schema, leaf


def test_rules_are_named_uniquely_and_structural_ones_go_to_the_dlq():
    names = [name for name, _, _, _ in sensor.RULES]
    assert len(names) == len(set(names))
    assert sensor.dlq_rules() == ["valid_json", "required_fields"]
    case = sensor.rule_case_sql()
    assert case.startswith("CASE WHEN") and case.count("WHEN") == len(sensor.RULES)


def test_table_ddls_hold_the_keys():
    assert all(f"{k} " in sensor.READING_DDL for k in sensor.READING_KEYS)
    assert all(f"{k} " in sensor.QUARANTINE_DDL for k in sensor.QUARANTINE_KEYS)
    assert "farm_id STRING" in sensor.READING_DDL  # lets Gold aggregate by region


def test_producer_all_farms_simulates_every_demo_device(tmp_path):
    out = tmp_path / "all.jsonl"
    main(["--all-farms", "--mode", "backfill", "--start", "2025-11-20", "--days", "1",
          "--step-minutes", "60", "--sink", "jsonl", "--output", str(out)])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    devices = {d for f in DEMO_FARMS for d in f.device_ids}
    assert {r["device_id"] for r in rows} == devices
    assert len(rows) == 24 * len(devices)
    assert {r["farm_id"] for r in rows} == {f.farm_id for f in DEMO_FARMS}
