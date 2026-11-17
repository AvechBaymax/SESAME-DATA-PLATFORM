"""Dashboard API: /api/evaluate, the rules on Earth Engine data, and the local server."""
import json
import threading
import urllib.error
import urllib.request
from datetime import date

import pytest

from api._rules import classify_texture, evaluate_rules
from api.evaluate import apply_overrides, handle_evaluate_request, load_scenarios

SCENARIOS = load_scenarios()


def _statuses(payload):
    return {rule["id"]: rule["status"] for rule in payload["rules"]["results"]}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_endpoint_matches_scenario_expectations(name):
    """The HTTP path builds the same weather series as the rule tests."""
    status, payload = handle_evaluate_request({"scenario": name})
    assert status == 200
    expected = SCENARIOS[name]["expected"]
    statuses = _statuses(payload)
    for key, value in expected.items():
        actual = payload["rules"]["overall_status"] if key == "overall_status" else statuses[key]
        assert actual == value, f"{name}: {key}"
    assert payload["simulated"] is True


def test_overrides_merge_nested_and_dotted_keys():
    base = {"weather": {"daily_temp_c": 27, "daily_rain_mm": 7}, "soil": {"ph": 6.5}}
    merged = apply_overrides(base, {"weather": {"daily_rain_mm": 0}, "soil.ph": 5.0})
    assert merged == {"weather": {"daily_temp_c": 27, "daily_rain_mm": 0}, "soil": {"ph": 5.0}}
    assert base["soil"]["ph"] == 6.5  # the scenario itself is not changed


def test_override_changes_the_result():
    status, payload = handle_evaluate_request({"scenario": "baseline_optimal",
                                               "overrides": {"weather.temp_c": 15, "weather.last_n_days": 3}})
    assert status == 200
    assert _statuses(payload)["R01"] == "VIOLATION"


def test_bad_requests():
    assert handle_evaluate_request({"scenario": "nope"})[0] == 400
    assert handle_evaluate_request([1, 2])[0] == 400
    assert handle_evaluate_request({"scenario": "baseline_optimal", "overrides": [1]})[0] == 400


def test_simulation_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATION", "0")
    assert handle_evaluate_request({"scenario": "baseline_optimal"})[0] == 403


@pytest.mark.parametrize("sand, clay, expected", [
    (90, 5, "sand"),
    (82, 6, "loamy sand"),      # caught as sandy loam before the fix
    (70, 15, "sandy loam"),
    (40, 20, "loam"),
    (20, 15, "silt loam"),
    (5, 5, "silt"),
    (60, 25, "sandy clay loam"),
    (30, 33, "clay loam"),
    (10, 33, "silty clay loam"),
    (50, 40, "sandy clay"),
    (5, 45, "silty clay"),
    (20, 50, "clay"),
])
def test_usda_texture_classes(sand, clay, expected):
    assert classify_texture(sand, clay, 100 - sand - clay) == expected


def test_before_sowing_is_not_harvest():
    payload = evaluate_rules({"as_of": "2026-11-01", "planting_date": "2026-11-15", "weather": {"series": []}})
    assert payload["stage"] == "pre_sowing"
    assert payload["das"] == -14


# ---- /api/monitor rules (no Earth Engine call) ----------------------------------------

monitor = pytest.importorskip("api.monitor", reason="earthengine-api not installed")


def _weather(start, days, rain=7.0):
    return [{"date": date.fromordinal(start.toordinal() + i).isoformat(), "temperature_c": 27.0,
             "rainfall_mm": rain, "humidity_pct": 75.0} for i in range(days)]


def test_monitor_rules_use_only_the_season():
    sown = date(2026, 11, 10)
    weather = _weather(date(2026, 10, 1), 70)  # 01/10 .. 09/12
    rules = monitor.evaluate_season(date(2026, 12, 10), sown, weather,
                                    {"ph": 6.5, "sand_g_kg": 700, "clay_g_kg": 150}, [])
    assert rules["simulated"] is False
    assert rules["stage"] == "development"
    r02 = next(rule for rule in rules["results"] if rule["id"] == "R02")
    assert r02["cumulative_mm"] == 30 * 7.0  # 10/11 .. 09/12 only


def test_monitor_rules_without_a_current_season():
    rules = monitor.evaluate_season(date(2026, 11, 1), date(2026, 11, 15), _weather(date(2026, 10, 1), 30),
                                    None, [])
    assert rules["stage"] == "pre_sowing"
    assert {r["id"]: r["status"] for r in rules["results"]}["R01"] == "NO_DATA"


# ---- local server -----------------------------------------------------------------------

@pytest.fixture()
def server():
    from http.server import ThreadingHTTPServer

    from web.local_server import LocalHandler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_local_server_evaluate(server):
    status, payload = _request(f"{server}/api/evaluate", {"scenario": "cold_snap"})
    assert status == 200
    assert payload["rules"]["overall_status"] == "VIOLATION"
    status, payload = _request(f"{server}/api/evaluate")
    assert status == 200 and "baseline_optimal" in payload["scenarios"]


def test_local_server_rejects_invalid_json(server):
    request = urllib.request.Request(f"{server}/api/evaluate", data=b"{oops", method="POST")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 400


# ---- field boundary ---------------------------------------------------------------------

def test_approximate_boundary_has_the_configured_area():
    import math

    from api._farm import approximate_ring

    ring = approximate_ring(11.0, 106.4, 5000)
    assert ring[0] == ring[-1] and len(ring) == 5
    lat0 = math.radians(11.0)
    xy = [(lon * 111_320 * math.cos(lat0), lat * 111_320) for lon, lat in ring]
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:]))) / 2
    assert area == pytest.approx(5000, rel=0.01)


def test_configured_boundary_wins(monkeypatch):
    import api._farm as farm

    outline = {"type": "Polygon", "coordinates": [[[106.4, 11.0], [106.401, 11.0], [106.401, 11.001], [106.4, 11.0]]]}
    monkeypatch.setattr(farm, "BOUNDARY_GEOJSON", json.dumps(outline))
    ring, source = farm.boundary()
    assert source == "configured" and ring[1] == [106.401, 11.0]
    monkeypatch.setattr(farm, "BOUNDARY_GEOJSON", "not json")
    assert farm.boundary()[1] == "approximate"
