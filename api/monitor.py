import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import ee
from dotenv import load_dotenv


logger = logging.getLogger(__name__)
load_dotenv()
PROJECT_ID = os.getenv("GEE_PROJECT_ID", "gen-lang-client-0646614400")
SERVICE_ACCOUNT_JSON = os.getenv("GEE_SERVICE_ACCOUNT_JSON", "")
FARM_ID = "CUCHI_02"
LATITUDE = 11.0
LONGITUDE = 106.4


def _number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _relative_humidity(temperature_k, dewpoint_k):
    temperature = _number(temperature_k)
    dewpoint = _number(dewpoint_k)
    if temperature is None or dewpoint is None:
        return None
    temperature_c = temperature - 273.15
    dewpoint_c = dewpoint - 273.15
    humidity = 100 * (
        math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
        / math.exp((17.625 * temperature_c) / (243.04 + temperature_c))
    )
    return round(max(0, min(100, humidity)), 1)


def _initialize_earth_engine():
    if not PROJECT_ID:
        raise RuntimeError("Set GEE_PROJECT_ID in the server environment.")
    if not SERVICE_ACCOUNT_JSON:
        if os.getenv("VERCEL"):
            raise RuntimeError(
                "Set GEE_SERVICE_ACCOUNT_JSON in Vercel project environment variables."
            )
        ee.Initialize(project=PROJECT_ID)
        return
    try:
        service_account = json.loads(SERVICE_ACCOUNT_JSON)
        credentials = ee.ServiceAccountCredentials(
            service_account["client_email"], key_data=SERVICE_ACCOUNT_JSON
        )
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("GEE_SERVICE_ACCOUNT_JSON is not a valid service account JSON.") from exc

    ee.Initialize(credentials=credentials, project=PROJECT_ID)


def _get_weather(point, start_date, end_date):
    collection = (
        ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
        .filterDate(start_date, end_date)
        .sort("system:time_start")
    )

    def extract(image):
        stats = image.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=point, scale=11132, maxPixels=1_000_000
        )
        return ee.Feature(
            None,
            {
                "date": image.date().format("YYYY-MM-dd"),
                "temperature_k": stats.get("temperature_2m"),
                "dewpoint_k": stats.get("dewpoint_temperature_2m"),
                "rainfall_m": stats.get("total_precipitation_sum"),
            },
        )

    features = collection.map(extract).getInfo().get("features", [])
    series = []
    for feature in features:
        props = feature.get("properties", {})
        temperature_k = _number(props.get("temperature_k"))
        rainfall_m = _number(props.get("rainfall_m"))
        series.append(
            {
                "date": props.get("date"),
                "temperature_c": round(temperature_k - 273.15, 1)
                if temperature_k is not None
                else None,
                "rainfall_mm": round(rainfall_m * 1000, 1)
                if rainfall_m is not None
                else None,
                "humidity_pct": _relative_humidity(
                    props.get("temperature_k"), props.get("dewpoint_k")
                ),
            }
        )
    return series


def _get_satellite(point, start_date, end_date):
    def mask_clouds(image):
        scl = image.select("SCL")
        clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return image.updateMask(clear).copyProperties(image, ["system:time_start"])

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(point)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50))
        .map(mask_clouds)
        .sort("system:time_start", False)
        .limit(5)
    )

    def extract(image):
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        value = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=point, scale=20, maxPixels=1_000_000
        ).get("NDVI")
        return ee.Feature(
            None, {"date": image.date().format("YYYY-MM-dd"), "ndvi": value}
        )

    features = collection.map(extract).getInfo().get("features", [])
    observations = [
        {
            "date": feature.get("properties", {}).get("date"),
            "ndvi": _number(feature.get("properties", {}).get("ndvi")),
        }
        for feature in features
    ]
    observations = [item for item in observations if item["ndvi"] is not None]
    observations.sort(key=lambda item: item["date"] or "")
    return observations


def _get_soil(point):
    soil = (
        ee.Image("projects/soilgrids-isric/sand_mean")
        .select("sand_0-5cm_mean")
        .addBands(
            ee.Image("projects/soilgrids-isric/clay_mean").select("clay_0-5cm_mean")
        )
        .addBands(
            ee.Image("projects/soilgrids-isric/phh2o_mean").select("phh2o_0-5cm_mean")
        )
    )
    stats = soil.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=point, scale=250, maxPixels=1_000_000
    ).getInfo()
    ph_value = _number(stats.get("phh2o_0-5cm_mean"))
    return {
        "sand_g_kg": _number(stats.get("sand_0-5cm_mean")),
        "clay_g_kg": _number(stats.get("clay_0-5cm_mean")),
        "ph": round(ph_value / 10, 1) if ph_value is not None else None,
    }


def _get_terrain(point):
    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation")
    terrain = elevation.addBands(ee.Terrain.slope(elevation))
    stats = terrain.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=point, scale=30, maxPixels=1_000_000
    ).getInfo()
    return {
        "elevation_m": _number(stats.get("elevation")),
        "slope_deg": _number(stats.get("slope")),
    }


def get_monitor_data(days):
    _initialize_earth_engine()
    today = datetime.now(timezone.utc).date()
    start_date = (today - timedelta(days=days)).isoformat()
    end_date = today.isoformat()
    point = ee.Geometry.Point([LONGITUDE, LATITUDE])

    weather = _get_weather(point, start_date, end_date)
    if not weather:
        raise RuntimeError("ERA5-Land returned no daily weather observations.")

    errors = {}
    optional_data = {}
    for name, fetch in (
        ("satellite", lambda: _get_satellite(point, start_date, end_date)),
        ("soil", lambda: _get_soil(point)),
        ("terrain", lambda: _get_terrain(point)),
    ):
        try:
            optional_data[name] = fetch()
        except Exception as exc:
            logger.exception("Earth Engine %s query failed", name)
            optional_data[name] = None
            errors[name] = "This Earth Engine dataset could not be loaded."

    return {
        "source": "Google Earth Engine",
        "farm": {"id": FARM_ID, "latitude": LATITUDE, "longitude": LONGITUDE},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weather": {"series": weather, "latest": weather[-1]},
        **optional_data,
        "errors": errors,
    }


def handle_request(path):
    query = parse_qs(urlparse(path).query)
    try:
        days = int(query.get("days", ["7"])[0])
    except ValueError:
        return 400, {"error": "days must be 7 or 30."}
    if days not in (7, 30):
        return 400, {"error": "days must be 7 or 30."}

    try:
        return 200, get_monitor_data(days)
    except RuntimeError as exc:
        logger.error("Earth Engine monitor request unavailable: %s", exc)
        return 503, {"error": str(exc)}
    except Exception:
        logger.exception("Earth Engine monitor request failed")
        return 502, {
            "error": "Could not load Earth Engine data. Check server logs and project access."
        }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/api/monitor":
            self.send_error(404)
            return
        status, payload = handle_request(self.path)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Cache-Control",
            "s-maxage=1800, stale-while-revalidate=3600" if status == 200 else "no-store",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)
