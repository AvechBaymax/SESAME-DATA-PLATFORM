"""
Fetch SoilGrids v2.0 for one farm and store the raw JSON in MinIO (landing zone).
 
Soil data is static, so this runs once per farm (not on a schedule).
Object key layout (re-running for the same farm overwrites the file):
  soilgrids/farm_id=<farm_id>/soilgrids.json
"""
import argparse
 
from common.config import get_minio_settings
from common.http_utils import get_json
from common.minio_utils import upload_raw_json
 
BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
 
 
def main():
    ap = argparse.ArgumentParser(description="Fetch SoilGrids data for a farm and upload raw JSON to MinIO")
    ap.add_argument("--farm-id", default="CUCHI_02")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--properties", nargs="+", default=["clay", "sand", "silt"])
    ap.add_argument("--depths", nargs="+", default=["15-30cm"])
    ap.add_argument("--values", nargs="+", default=["mean"])
    ap.add_argument("--bucket", default=None)
    args = ap.parse_args()
 
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