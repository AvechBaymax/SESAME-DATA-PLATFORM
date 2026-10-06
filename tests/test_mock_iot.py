import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from common.farm import FarmProfile
from mock_iot.producer import inject_fault, main
from mock_iot.simulator import SensorNode
from mock_iot.user_config_mock import generate_mock_config
from mock_iot.weather import ClimatologyWeather, DiurnalWeather, NasaPowerWeather, parse_nasa_power

UTC = timezone.utc


def run_season(planting, start, days, **farm_kwargs):
    farm = FarmProfile(planting_date=planting, **farm_kwargs)
    weather = DiurnalWeather(ClimatologyWeather(farm.farm_id), farm.lat, farm.lon, farm.farm_id)
    node = SensorNode(farm, farm.device_ids[0], weather, seed=1)
    t = datetime(start.year, start.month, start.day, tzinfo=UTC)
    step = timedelta(minutes=15)
    out = []
    for _ in range(days * 96):
        out.append(node.step(t, t + step))
        t += step
    return out


@pytest.fixture(scope="module")
def dry_season():
    return run_season(date(2025, 11, 15), date(2025, 11, 10), 85)


def test_readings_stay_in_physical_ranges(dry_season):
    for m in dry_season:
        soil = m["measurements"]["soil"]
        micro = m["measurements"]["weather_micro"]
        assert 3 < soil["vwc_pct"] < 42
        assert soil["water_potential_kpa"] < 0
        assert 0 <= soil["oxygen_level_pct"] <= 21
        assert 0 <= micro["humidity_pct"] <= 100
        assert 15 < micro["ambient_temp_c"] < 40
        assert micro["rain_mm"] >= 0 and micro["solar_rad_w_m2"] >= 0
        assert 0 <= m["measurements"]["canopy"]["leaf_wetness_min"] <= 15
        assert m["sample_interval_s"] == 900


def test_solar_is_zero_at_night_and_peaks_near_noon(dry_season):
    # Solar noon at 108.12 E is about 04:47 UTC; midnight about 16:47 UTC.
    by_hour = {}
    for m in dry_season:
        hour = int(m["datetime"][11:13])
        by_hour.setdefault(hour, []).append(m["measurements"]["weather_micro"]["solar_rad_w_m2"])
    assert max(by_hour[17]) == 0
    assert max(by_hour, key=lambda h: sum(by_hour[h])) in (4, 5)


def test_irrigation_only_while_crop_is_in_field(dry_season):
    irrigated_days = {m["datetime"][:10] for m in dry_season if m["flags"]["irrigation_active"]}
    assert irrigated_days, "dry season should need irrigation"
    assert all("2025-11-15" <= d < "2026-01-29" for d in irrigated_days)


def test_rain_raises_soil_moisture():
    msgs = run_season(date(2026, 5, 1), date(2026, 5, 1), 60)
    vwc = [m["measurements"]["soil"]["vwc_pct"] for m in msgs]
    rain = [m["measurements"]["weather_micro"]["rain_mm"] for m in msgs]
    rises = [vwc[i + 4] - vwc[i] for i in range(len(msgs) - 4) if sum(rain[i + 1:i + 5]) > 10]
    assert rises
    assert sum(rises) / len(rises) > 1.5


def test_poor_drainage_causes_hypoxia():
    good = run_season(date(2026, 5, 1), date(2026, 5, 1), 75)
    poor = run_season(date(2026, 5, 1), date(2026, 5, 1), 75, drainage="poor")
    o2 = lambda ms: min(m["measurements"]["soil"]["oxygen_level_pct"] for m in ms)  # noqa: E731
    assert o2(good) > 19.5
    assert o2(poor) < 18


def test_simulation_is_reproducible():
    a = run_season(date(2025, 11, 15), date(2025, 11, 15), 2)
    b = run_season(date(2025, 11, 15), date(2025, 11, 15), 2)
    assert a == b


def test_nasa_power_parsing_drops_fill_values_and_falls_back():
    payload = {
        "header": {"fill_value": -999.0},
        "properties": {"parameter": {
            "T2M_MAX": {"20251201": 31.0, "20251202": -999.0},
            "T2M_MIN": {"20251201": 22.0, "20251202": 23.0},
            "PRECTOTCORR": {"20251201": 4.2, "20251202": 0.0},
        }},
    }
    records = parse_nasa_power(payload)
    assert records[date(2025, 12, 1)] == {"tmax_c": 31.0, "tmin_c": 22.0, "precip_mm": 4.2}
    assert "tmax_c" not in records[date(2025, 12, 2)]

    weather = NasaPowerWeather(11, 108, date(2025, 12, 1), date(2025, 12, 2),
                               fetch=lambda *a, **k: payload)
    first = weather.daily(date(2025, 12, 1))
    assert (first.tmax_c, first.precip_mm, first.source) == (31.0, 4.2, "nasa_power+climatology")
    second = weather.daily(date(2025, 12, 2))
    assert second.tmin_c == 23.0
    assert second.tmax_c == ClimatologyWeather().daily(date(2025, 12, 2)).tmax_c
    assert weather.daily(date(2025, 12, 5)).source == "climatology"


def test_fault_injection():
    msg = {"timestamp": 1_700_000_000, "datetime": "x", "telemetry": {"error_codes": []},
           "measurements": {"soil": {"vwc_pct": 12}, "canopy": {}, "weather_micro": {
               "ambient_temp_c": 30, "humidity_pct": 70}}}
    rng = random.Random(0)
    (raw, fault), = inject_fault(msg, "malformed", rng)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert len(inject_fault(msg, "duplicate", rng)) == 2
    (raw, _), = inject_fault(msg, "late", rng)
    assert json.loads(raw)["timestamp"] == msg["timestamp"] - 7200
    (raw, _), = inject_fault(msg, "missing_field", rng)
    assert len(json.loads(raw)["measurements"]) == 2
    assert msg["measurements"]["soil"]["vwc_pct"] == 12  # original untouched


def test_backfill_cli_writes_jsonl(tmp_path):
    out = tmp_path / "s.jsonl"
    main(["--mode", "backfill", "--start", "2025-11-15", "--days", "1", "--devices", "2",
          "--sink", "jsonl", "--output", str(out)])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 2 * 96
    assert {r["device_id"] for r in rows} == {"SN_BINHTHUAN_01_01", "SN_BINHTHUAN_01_02"}


def test_user_config_is_consistent_with_guide():
    farm = FarmProfile()
    cfg = generate_mock_config(farm)
    assert cfg["crop_metadata"]["in_recommended_sowing_window"]
    assert cfg["geometry"]["area_m2"] == pytest.approx(farm.area_m2, rel=0.01)
    totals = {}
    for event in cfg["management"]["fertilizer_plan"]:
        for product, kg in event["products_kg_ha"].items():
            totals[product] = totals.get(product, 0) + kg
    assert totals["urea"] == pytest.approx(260, abs=0.5)
    assert totals["kcl"] == pytest.approx(100, abs=0.5)
    assert totals["super_lan"] == 375
    assert cfg["devices"][0]["device_id"] == farm.device_ids[0]
