import json
from pathlib import Path

import pytest

from api._rules import evaluate_rules

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "config" / "scenarios.json").read_text(encoding="utf-8"))


def _scenario_context(name):
    data = dict(SCENARIOS[name])
    data.pop("expected", None)
    return data


def _results(payload):
    return {rule["id"]: rule for rule in payload["results"]}


@pytest.mark.parametrize("name", sorted(SCENARIOS.keys()))
def test_scenarios(name):
    payload = evaluate_rules(_scenario_context(name))
    expected = SCENARIOS[name]["expected"]
    rules = _results(payload)
    for key, value in expected.items():
        if key.startswith("R"):
            assert rules[key]["status"] == value, f"{name}: {key} should be {value}"
        elif key == "overall_status":
            assert payload["overall_status"] == value, f"{name}: overall should be {value}"


def test_null_inputs_no_exception():
    payload = evaluate_rules({
        "as_of": "2026-12-25",
        "planting_date": "2026-11-10",
        "weather": {"series": [None]},
        "soil": None,
        "satellite": None,
    })
    assert payload["overall_status"] in {"OK", "WARN", "VIOLATION", "NO_DATA"}


def test_assumed_thresholds_list():
    payload = evaluate_rules({
        "as_of": "2026-12-25",
        "planting_date": "2026-11-10",
        "weather": {"series": [{"date": "2026-12-25", "temperature_c": 27, "rainfall_mm": 5, "humidity_pct": 75}]},
        "soil": {"ph": 6.5, "sand_g_kg": 700, "clay_g_kg": 150},
        "satellite": [{"date": "2026-12-25", "ndvi": 0.75}],
    })
    assert any(item.startswith("R01") for item in payload["assumed_thresholds"])
    assert any(item.startswith("R03") for item in payload["assumed_thresholds"])
    assert any(item.startswith("R04") for item in payload["assumed_thresholds"])
    assert any(item.startswith("R05") for item in payload["assumed_thresholds"])
    assert any(item.startswith("R06") for item in payload["assumed_thresholds"])


def test_stage_boundaries_and_exact_levels():
    payload = evaluate_rules({
        "as_of": "2026-12-25",
        "planting_date": "2026-11-10",
        "weather": {"series": [{"date": "2026-12-25", "temperature_c": 25.0, "rainfall_mm": 0.0, "humidity_pct": 75}]},
        "soil": {"ph": 5.5, "sand_g_kg": 700, "clay_g_kg": 150},
        "satellite": [{"date": "2026-12-25", "ndvi": 0.60}],
    })
    assert payload["stage"] == "mid_season"
    assert payload["overall_status"] == "OK"
    assert payload["results"][0]["status"] == "OK"
    assert payload["results"][5]["status"] == "OK"


def _rule(payload, rule_id):
    return next(rule for rule in payload["results"] if rule["id"] == rule_id)


@pytest.mark.parametrize("sand, clay, status", [
    (700, 150, "OK"),    # sandy loam: recommended (I.2)
    (900, 30, "OK"),     # sand: II.2 lists poor sandy soils
    (300, 330, "OK"),    # clay loam: alluvial-type soil, fine when drained (II.2)
    (200, 500, "WARN"),  # clay: heavy, waterlogging risk
])
def test_soil_texture_follows_the_guide(sand, clay, status):
    payload = evaluate_rules({"as_of": "2026-12-10", "planting_date": "2026-11-10",
                              "soil": {"ph": 6.5, "sand_g_kg": sand, "clay_g_kg": clay}})
    assert _rule(payload, "R07")["status"] == status


def test_care_schedule_includes_pre_sowing_work():
    payload = evaluate_rules({"as_of": "2026-11-13", "planting_date": "2026-11-15"})
    tasks = {task["id"]: task for task in _rule(payload, "R09")["tasks"]}
    assert tasks["land_prep"]["target_date"] == "2026-11-12" and tasks["land_prep"]["state"] == "DUE"
    assert tasks["pre_emergence"]["state"] == "UPCOMING"
    assert tasks["seed_treatment"]["das"] == 0
    manual = {task["id"] for task in _rule(payload, "R09")["manual_tasks"]}
    assert {"thin", "pest_scout", "drain"} <= manual


def test_heavy_rain_counts_at_harvest():
    payload = evaluate_rules({"as_of": "2026-12-16", "planting_date": "2026-10-01",
                              "weather": {"series": [{"date": "2026-12-16", "rainfall_mm": 55}]}})
    assert payload["stage"] == "harvest"
    assert _rule(payload, "R03")["status"] == "VIOLATION"
