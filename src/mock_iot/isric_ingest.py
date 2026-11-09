"""
Fetch SoilGrids v2.0 for one farm and store the raw JSON in MinIO (landing zone).

Soil data is static, so this runs once per farm (not on a schedule).
Object key layout (re-running for the same farm overwrites the file):
  soilgrids/farm_id=<farm_id>/soilgrids.json

Usage (run from the repo root after `pip install -e .`):
  python -m mock_iot.isric_ingest --farm-id BINHTHUAN_01 --lat 11.08 --lon 108.12
"""
import argparse

from common.config import get_minio_settings
from common.farm import DEFAULT_FARM_ID, DEFAULT_LAT, DEFAULT_LON
from common.http_utils import get_json
from common.minio_utils import upload_raw_json

BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# Texture (clay/sand/silt), pH, organic carbon, total nitrogen, bulk density and
# CEC: what the Binh Thuan guide (pH 5.5-7.5, light soils) and the fertilizer /
# water-balance formulas need. Depths cover the 0-60 cm root zone.
DEFAULT_PROPERTIES = ["clay", "sand", "silt", "phh2o", "soc", "nitrogen", "bdod", "cec"]
DEFAULT_DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch SoilGrids data for a farm and upload raw JSON to MinIO")
    ap.add_argument("--farm-id", default=DEFAULT_FARM_ID)
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--properties", nargs="+", default=DEFAULT_PROPERTIES)
    ap.add_argument("--depths", nargs="+", default=DEFAULT_DEPTHS)
    ap.add_argument("--values", nargs="+", default=["mean"])
    ap.add_argument("--bucket", default=None)
    args = ap.parse_args(argv)

    params = {"lat": args.lat, "lon": args.lon, "property": args.properties,
              "depth": args.depths, "value": args.values}
    payload = get_json(BASE_URL, params=params)

    uri = upload_raw_json(
        "soilgrids", payload,
        filename="soilgrids.json",  # fixed name: one object per farm, idempotent re-runs
        settings=get_minio_settings(bucket=args.bucket),
        farm_id=args.farm_id,
    )
    print(f"Uploaded {uri}")


if __name__ == "__main__":
    main()
