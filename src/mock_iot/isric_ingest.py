#!/usr/bin/env python3
"""
Fetch soil properties from ISRIC SoilGrids v2.0 and store the raw JSON response in MinIO.
 
Credentials / endpoint come from environment variables (or CLI flags):
  MINIO_ENDPOINT      default http://localhost:9000
  MINIO_ACCESS_KEY    default minioadmin
  MINIO_SECRET_KEY    default minioadmin
 
Usage:
  python ingest_soilgrids.py                                   # default test point
  python ingest_soilgrids.py --lat 11.0 --lon 106.4 --bucket landing-zone
 
Object key layout:
  soilgrids/dt=<YYYY-MM-DD>/lat=<lat>_lon=<lon>_<UTC timestamp>.json
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import boto3
import requests
from botocore.client import Config
from botocore.exceptions import ClientError

load_dotenv()
BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
 
 
def fetch_soilgrids(lat, lon, properties, depths, values, max_retries=5, timeout=60):
    """Call the API for a single point, retrying on 429 / 5xx / network errors."""
    # requests repeats list params: property=clay&property=sand&...
    params = {"lat": lat, "lon": lon, "property": properties, "depth": depths, "value": values}
 
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            wait = min(2 ** attempt * 5, 120)
            print(f"Network error ({exc}), retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue
 
        if resp.status_code == 200:
            return resp.json()
 
        if resp.status_code == 429:  # public API allows ~5 requests/minute
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"Rate limited (429), waiting {wait}s")
            time.sleep(wait)
            continue
 
        if resp.status_code >= 500:
            wait = min(2 ** attempt * 5, 120)
            print(f"Server error {resp.status_code}, retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue
 
        # Other 4xx: bad parameters, do not retry
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
 
    raise RuntimeError("Max retries exceeded")
 
 
def get_s3_client(endpoint, access_key, secret_key):
    """S3 client pointed at MinIO (path-style addressing is required)."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",  # MinIO ignores the region but boto3 needs one
    )
 
 
def ensure_bucket(s3, bucket):
    """Create the bucket if it does not exist yet."""
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
        print(f"Created bucket: {bucket}")
 
 
def upload_json(s3, bucket, key, payload):
    """Upload the payload as a JSON object."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
 
 
def main():
    ap = argparse.ArgumentParser(description="Fetch SoilGrids data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--properties", nargs="+", default=["clay", "sand", "silt"])
    ap.add_argument("--depths", nargs="+", default=["15-30cm"])
    ap.add_argument("--values", nargs="+", default=["mean"])
    ap.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    ap.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "minioadmin"))
    ap.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "minioadmin"))
    ap.add_argument("--bucket", default="landing-zone")
    args = ap.parse_args()
 
    payload = fetch_soilgrids(args.lat, args.lon, args.properties, args.depths, args.values)
 
    now = datetime.now(timezone.utc)
    key = (
        f"soilgrids/farm_id=CUCHI_02/"
        f"data.json"
    )
 
    s3 = get_s3_client(args.endpoint, args.access_key, args.secret_key)
    ensure_bucket(s3, args.bucket)
    upload_json(s3, args.bucket, key, payload)
 
    print(f"Uploaded s3://{args.bucket}/{key}")
 
 
if __name__ == "__main__":
    main()
 