"""
Fetch current weather from OpenWeatherMap and store the raw JSON response in MinIO.
 
Settings come from .env (see common/config.py):
  OWM_API_KEY   your OpenWeatherMap API key (required, never hardcode it)
  MINIO_*       MinIO endpoint and credentials
 
Usage (run from the repo root):
  python -m src.mock_iot.owm_ingest
  python -m src.mock_iot.owm_ingest --lat 11.0 --lon 106.4 --farm-id BINHTHUAN_01
 
Object key layout (time-series partitioning):
  weather/farm_id=<farm_id>/dt=<YYYY-MM-DD>/data_<UTC timestamp>.json
"""
import argparse
 
from common.config import get_minio_settings, require_env
from common.http_utils import get_json
from common.minio_utils import upload_raw_json, utc_now_compact, utc_today
 
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
 
 
def main():
    ap = argparse.ArgumentParser(description="Fetch OWM weather data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--farm-id", type=str, default="CUCHI_02")
    ap.add_argument("--units", default="metric")
    ap.add_argument("--bucket", default=None, help="Overrides MINIO_BUCKET from .env")
    args = ap.parse_args()
 
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
 