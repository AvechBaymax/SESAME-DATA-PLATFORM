#!/usr/bin/env python3
"""
Fetch weather data from OpenWeatherMap API and store the raw JSON response in MinIO.

Credentials / endpoint come from environment variables (or CLI flags):
  MINIO_ENDPOINT      default http://localhost:9000
  MINIO_ACCESS_KEY    default admin
  MINIO_SECRET_KEY    default password123
  OWM_API_KEY         (Your OpenWeatherMap App ID)

Usage:
  python weather_ingest.py                                  # default test point
  python weather_ingest.py --lat 11.0 --lon 106.4 --farm-id BINHTHUAN_01

Object key layout (Time-series Partitioning):
  weather/farm_id=<farm_id>/dt=<YYYY-MM-DD>/data_<UTC timestamp>.json
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
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


def fetch_weather(lat, lon, appid, units="metric", max_retries=5, timeout=60):
    """Call the OWM API for a single point, retrying on network errors."""
    params = {
        "lat": lat,
        "lon": lon,
        "units": units,
        "appid": appid
    }

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

        if resp.status_code == 429:  # Rate limit
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"Rate limited (429), waiting {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            wait = min(2 ** attempt * 5, 120)
            print(f"Server error {resp.status_code}, retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue

        # Other 4xx: bad parameters, unauthorized (wrong API key), do not retry
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
    ap = argparse.ArgumentParser(description="Fetch OWM Weather data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--farm-id", type=str, default="CUCHI_02")
    ap.add_argument("--appid", default=os.getenv("OWM_API_KEY", "a642a83341e3e52f2a36c5e4bf353077"))
    ap.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    ap.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "admin"))
    ap.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "password123"))
    ap.add_argument("--bucket", default="landing-zone")
    args = ap.parse_args()

    payload = fetch_weather(args.lat, args.lon, args.appid)

    now = datetime.now(timezone.utc)
    
    key = (
        f"weather/farm_id={args.farm_id}/dt={now:%Y-%m-%d}/"
        f"data_{now:%Y%m%dT%H%M%SZ}.json"
    )

    s3 = get_s3_client(args.endpoint, args.access_key, args.secret_key)
    ensure_bucket(s3, args.bucket)
    upload_json(s3, args.bucket, key, payload)

    print(f"Uploaded s3://{args.bucket}/{key}")


if __name__ == "__main__":
    main()