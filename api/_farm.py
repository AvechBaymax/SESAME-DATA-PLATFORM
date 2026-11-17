"""Farm and season settings shared by the dashboard API functions.

Files in api/ whose name starts with "_" are helpers, not Vercel functions.
Every value can be overridden with an environment variable (web/.env locally,
project settings on Vercel).
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# web/.env (dashboard settings, see README) first, then the repository .env.
# Variables already set in the environment always win.
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "web" / ".env")
load_dotenv(ROOT / ".env")

FARM_ID = os.getenv("FARM_ID", "CUCHI_02")
LATITUDE = float(os.getenv("FARM_LAT", "11.0"))
LONGITUDE = float(os.getenv("FARM_LON", "106.4"))
# Sowing date of the season the rules evaluate. The default matches
# DEFAULT_PLANTING_DATE in src/common/farm.py (Dong Xuan 2026).
PLANTING_DATE_TEXT = os.getenv("SEASON_PLANTING_DATE", "2026-11-15")
# Longest season history fetched for the rules (a sesame season is ~75 days).
MAX_SEASON_DAYS = 120


def planting_date():
    """The configured sowing date, or None when SEASON_PLANTING_DATE is empty or invalid."""
    try:
        return datetime.strptime(PLANTING_DATE_TEXT, "%Y-%m-%d").date() if PLANTING_DATE_TEXT else None
    except ValueError:
        return None


AREA_M2 = float(os.getenv("FARM_AREA_M2", "5000"))
# Real field outline: a GeoJSON Polygon (or its coordinates) in lon/lat order.
BOUNDARY_GEOJSON = os.getenv("FARM_BOUNDARY_GEOJSON", "")
EARTH_M_PER_DEG = 111_320.0


def approximate_ring(lat, lon, area_m2, aspect=2.0):
    """Closed lon/lat ring of a rectangle of area_m2 centred on the point
    (the same shape as FarmProfile.polygon in src/common/farm.py)."""
    width = math.sqrt(area_m2 / aspect)
    dlat = width / 2 / EARTH_M_PER_DEG
    dlon = width * aspect / 2 / (EARTH_M_PER_DEG * math.cos(math.radians(lat)))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat]]
    ring.append(ring[0])
    return [[round(x, 7), round(y, 7)] for x, y in ring]


def boundary():
    """(ring, source): the configured outline, or an approximate rectangle of AREA_M2."""
    if BOUNDARY_GEOJSON:
        try:
            value = json.loads(BOUNDARY_GEOJSON)
            coordinates = value.get("coordinates") if isinstance(value, dict) else value
            ring = coordinates[0]
            if len(ring) >= 4 and all(len(point) >= 2 for point in ring):
                return [[float(point[0]), float(point[1])] for point in ring], "configured"
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
    return approximate_ring(LATITUDE, LONGITUDE, AREA_M2), "approximate"


def farm_summary():
    ring, source = boundary()
    return {"id": FARM_ID, "latitude": LATITUDE, "longitude": LONGITUDE, "area_m2": AREA_M2,
            "boundary": {"type": "Polygon", "coordinates": [ring]}, "boundary_source": source}
