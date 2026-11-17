"""POST /api/evaluate: run the sesame rules on a test scenario or on a posted context.

Body, one of:
  {"scenario": "<name in config/scenarios.json>", "overrides": {...}}
  {"as_of": ..., "planting_date": ..., "weather": ..., "soil": ..., "satellite": ...}

Overrides are merged into the scenario; nested dicts merge key by key and a
dotted key ("weather.temp_c": 18) sets one nested field. Results are marked
"simulated". Set ALLOW_SIMULATION=0 to turn the endpoint off.
"""
import copy
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # Vercel runs this file directly
    sys.path.insert(0, str(ROOT))

from api._farm import farm_summary  # noqa: E402
from api._rules import _coerce_date, evaluate_rules, normalize_weather_series  # noqa: E402

SCENARIOS_PATH = ROOT / "config" / "scenarios.json"
MAX_BODY_BYTES = 256 * 1024


def load_scenarios():
    if not SCENARIOS_PATH.exists():
        return {}
    with SCENARIOS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def apply_overrides(base, overrides):
    """Deep-merge overrides into a copy of base; "a.b" keys address nested fields."""
    merged = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        *parents, leaf = key.split(".")
        target = merged
        for part in parents:
            if not isinstance(target.get(part), dict):
                target[part] = {}
            target = target[part]
        if isinstance(value, dict) and isinstance(target.get(leaf), dict):
            target[leaf] = apply_overrides(target[leaf], value)
        else:
            target[leaf] = copy.deepcopy(value)
    return merged


def scenario_context(name, overrides=None, scenarios=None):
    scenarios = load_scenarios() if scenarios is None else scenarios
    if name not in scenarios:
        raise KeyError(name)
    scenario = {key: value for key, value in scenarios[name].items() if key != "expected"}
    return apply_overrides(scenario, overrides)


def handle_evaluate_request(data):
    if os.getenv("ALLOW_SIMULATION", "1") != "1":
        return 403, {"error": "simulation disabled"}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return 400, {"error": "request body must be a JSON object"}
    if "scenario" in data:
        scenarios = load_scenarios()
        overrides = data.get("overrides") or {}
        if not isinstance(overrides, dict):
            return 400, {"error": "overrides must be a JSON object"}
        try:
            context = scenario_context(data["scenario"], overrides, scenarios)
        except KeyError:
            return 400, {"error": "scenario not found", "valid_scenarios": sorted(scenarios)}
    else:
        context = copy.deepcopy(data)
    context["simulated"] = True

    planting_date = _coerce_date(context.get("planting_date"))
    as_of = _coerce_date(context.get("as_of"))
    context["weather"] = {"series": normalize_weather_series(context.get("weather") or {}, planting_date, as_of)}
    return 200, {
        "source": "SIMULATION",
        "simulated": True,
        "scenario": data.get("scenario"),
        "farm": farm_summary(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "planting_date": context.get("planting_date"),
        "as_of": context.get("as_of"),
        "weather": context["weather"],
        "soil": context.get("soil"),
        "satellite": context.get("satellite") or [],
        "terrain": None,
        "errors": {},
        "rules": evaluate_rules(context),
    }


def read_json_body(request_handler):
    """(data, error) from a BaseHTTPRequestHandler request body."""
    try:
        length = int(request_handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None, "invalid Content-Length"
    if length > MAX_BODY_BYTES:
        return None, "request body too large"
    raw = request_handler.rfile.read(length) if length else b""
    if not raw:
        return {}, None
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "request body is not valid JSON"


def write_json(request_handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_handler.send_response(status)
    request_handler.send_header("Content-Type", "application/json; charset=utf-8")
    request_handler.send_header("Cache-Control", "no-store")
    request_handler.send_header("Content-Length", str(len(body)))
    request_handler.end_headers()
    request_handler.wfile.write(body)


def handle_post(request_handler):
    data, error = read_json_body(request_handler)
    if error:
        write_json(request_handler, 400, {"error": error})
        return
    status, payload = handle_evaluate_request(data)
    write_json(request_handler, status, payload)


class handler(BaseHTTPRequestHandler):
    """Vercel Python function entry point."""

    def do_POST(self):
        if urlparse(self.path).path != "/api/evaluate":
            self.send_error(404)
            return
        handle_post(self)

    def do_GET(self):
        if urlparse(self.path).path != "/api/evaluate":
            self.send_error(404)
            return
        write_json(self, 200, {"scenarios": sorted(load_scenarios())})
