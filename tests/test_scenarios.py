"""Demo incidents: they change the simulated readings the way they claim to."""
import json
from datetime import date, datetime, timedelta, timezone

from mock_iot.producer import main
from mock_iot.scenarios import demo_incidents
from mock_iot.weather import ClimatologyWeather, StormWeather


def local_day(m):
    return (datetime.fromtimestamp(m["timestamp"], timezone.utc) + timedelta(hours=7)).date()


def run(tmp_path, *args):
    out = tmp_path / "out.jsonl"
    main([*args, "--mode", "backfill", "--sink", "jsonl", "--output", str(out)])
    return [json.loads(line) for line in out.read_text().splitlines()]


def test_demo_incidents_land_in_flowering_and_cover_three_farms():
    incidents = {i.kind: i for i in demo_incidents()}
    assert set(incidents) == {"pump_failure", "storm", "sensor_outage"}
    assert incidents["pump_failure"].start == date(2025, 11, 15) + timedelta(days=42)
    assert incidents["storm"].rain_mm_per_day >= 30          # heavy enough for the heavy-rain rule
    assert "BINHTHUAN_03" not in {i.farm_id for i in demo_incidents()}   # the undisturbed reference


def test_pump_failure_stops_irrigation(tmp_path):
    farm = ["--farm-id", "BINHTHUAN_01", "--planting-date", "2025-11-15", "--devices", "1"]
    pump = demo_incidents()[0]
    window = lambda rows: sum(m["measurements"]["irrigation"]["applied_mm"]  # noqa: E731
                              for m in rows if pump.covers(local_day(m)))
    assert window(run(tmp_path, *farm)) > 0
    assert window(run(tmp_path, *farm, "--scenario", "demo")) == 0


def test_storm_adds_rain_on_its_days_only():
    base = ClimatologyWeather("X")
    storm = StormWeather(base, {date(2026, 1, 9): 60.0})
    assert storm.daily(date(2026, 1, 9)).precip_mm == base.daily(date(2026, 1, 9)).precip_mm + 60.0
    assert storm.daily(date(2026, 1, 8)) == base.daily(date(2026, 1, 8))


def test_sensor_outage_silences_the_device(tmp_path):
    rows = run(tmp_path, "--farm-id", "BINHTHUAN_04", "--planting-date", "2025-12-05", "--devices", "1",
               "--start", "2026-01-03", "--days", "4", "--scenario", "demo")
    days = {local_day(m) for m in rows}
    assert date(2026, 1, 4) not in days and date(2026, 1, 5) not in days
    assert date(2026, 1, 6) in days
