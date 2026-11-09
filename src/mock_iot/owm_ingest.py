"""
Fetch current weather from OpenWeatherMap and store the raw JSON response in MinIO.

Settings come from .env (see common/config.py):
  OWM_API_KEY   your OpenWeatherMap API key (required, never hardcode it)
  MINIO_*       MinIO endpoint and credentials

Usage (run from the repo root after `pip install -e .`):
  python -m mock_iot.owm_ingest
  python -m mock_iot.owm_ingest --lat 11.08 --lon 108.12 --farm-id BINHTHUAN_01

Object key layout (time-series partitioning):
  weather/farm_id=<farm_id>/dt=<YYYY-MM-DD>/data_<UTC timestamp>.json

Note: wind in this response is measured at 10 m; FAO-56 ET0 needs 2 m
(u2 = u10 * 0.748), so convert before using it.
"""
import argparse

from common.config import get_minio_settings, require_env
from common.farm import DEFAULT_FARM_ID, DEFAULT_LAT, DEFAULT_LON
from common.http_utils import get_json
from common.minio_utils import upload_raw_json, utc_now_compact, utc_today

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch OWM weather data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--farm-id", type=str, default=DEFAULT_FARM_ID)
    ap.add_argument("--units", default="metric")
    ap.add_argument("--bucket", default=None, help="Overrides MINIO_BUCKET from .env")
    args = ap.parse_args(argv)

    params = {
        "lat": args.lat,
        "lon": args.lon,
        "units": args.units,
        "appid": require_env("OWM_API_KEY"),  # fails with a clear message if missing
    }
    payload = get_json(BASE_URL, params=params)

    uri = upload_raw_json(
        "weather", payload,
        filename=f"data_{utc_now_compact()}.json",
        settings=get_minio_settings(bucket=args.bucket),
        farm_id=args.farm_id,
        dt=utc_today(),
    )
    print(f"Uploaded {uri}")


if __name__ == "__main__":
    main()
