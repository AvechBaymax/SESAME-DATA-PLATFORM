#!/usr/bin/env python3
"""
Fetch historical weather/solar data from NASA POWER and store the raw JSON in MinIO.
 
MinIO settings come from .env (see common/config.py).
 
Usage (run from the repo root):
  python -m src.mock_iot.nasa_ingest
  python -m src.mock_iot.nasa_ingest --lat 11.0 --lon 106.4 --start 20260907 --end 20260919 --farm-id BINHTHUAN_01
 
Object key layout (batch ingestion):
  nasa_power/farm_id=<farm_id>/ingest_dt=<YYYY-MM-DD>/data_<start>_to_<end>_<HHMMSS>Z.json
"""
import argparse
from datetime import datetime, timedelta, timezone
 
from common.config import get_minio_settings
from common.http_utils import get_json
from common.minio_utils import upload_raw_json
 
BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
 
# Daily variables needed for ET0 (FAO-56 Penman-Monteith) and water-balance features:
#   T2M / T2M_MAX / T2M_MIN : air temperature (mean / max / min), deg C
#   RH2M                    : relative humidity at 2 m, %
#   WS2M                    : wind speed at 2 m, m/s
#   ALLSKY_SFC_SW_DWN       : incoming shortwave radiation, kWh/m2/day
#   PRECTOTCORR             : bias-corrected precipitation, mm/day
DEFAULT_PARAMETERS = "T2M,T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN,PRECTOTCORR"
 
 
def main():
    ap = argparse.ArgumentParser(description="Fetch NASA POWER data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--farm-id", type=str, default="CUCHI_02")
 
    # NASA-specific arguments
    ap.add_argument("--parameters", type=str, default=DEFAULT_PARAMETERS,
                    help="Comma-separated NASA POWER parameter names")
    ap.add_argument("--community", type=str, default="ag",
                    help="'ag' (Agroclimatology) or 're' (Renewable Energy)")
 
    # Default window: the last 7 days, ending 7 days ago, because NASA POWER lags
    # real time by a few days and returns -999 (fill value) for dates not yet processed.
    default_end = datetime.now(timezone.utc) - timedelta(days=7)
    default_start = default_end - timedelta(days=7)
    ap.add_argument("--start", type=str, default=default_start.strftime("%Y%m%d"), help="Format YYYYMMDD")
    ap.add_argument("--end", type=str, default=default_end.strftime("%Y%m%d"), help="Format YYYYMMDD")
 
    ap.add_argument("--bucket", default=None, help="Overrides MINIO_BUCKET from .env")
    args = ap.parse_args()
 
    print(f"Fetching NASA POWER data for {args.farm_id} from {args.start} to {args.end}...")
    params = {
        "latitude": args.lat,
        "longitude": args.lon,
        "parameters": args.parameters,
        "community": args.community,
        "start": args.start,
        "end": args.end,
        "format": "JSON",
    }
    # NASA POWER can be slow on large date ranges, hence the longer timeout
    payload = get_json(BASE_URL, params=params, timeout=120)
 
    now = datetime.now(timezone.utc)
    uri = upload_raw_json(
        "nasa_power", payload,
        filename=f"data_{args.start}_to_{args.end}_{now:%H%M%S}Z.json",
        settings=get_minio_settings(bucket=args.bucket),
        farm_id=args.farm_id,
        ingest_dt=f"{now:%Y-%m-%d}",
    )
    print(f"Uploaded {uri}")
 
 
if __name__ == "__main__":
    main()
 