#!/usr/bin/env python3
"""
Fetch historical weather/solar data from NASA POWER API and store the raw JSON in MinIO.

Credentials / endpoint come from environment variables (or CLI flags):
  MINIO_ENDPOINT      default http://localhost:9000
  MINIO_ACCESS_KEY    default admin
  MINIO_SECRET_KEY    default password123

Usage:
  python nasa_power_ingest.py
  python nasa_power_ingest.py --lat 11.0 --lon 106.4 --start 20260907 --end 20260919 --farm-id BINHTHUAN_01

Object key layout (Batch Ingestion):
  nasa_power/farm_id=<farm_id>/ingest_dt=<YYYY-MM-DD>/data_<start>_to_<end>_<timestamp>.json
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import boto3
import requests
from botocore.client import Config
from botocore.exceptions import ClientError

load_dotenv()
BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


def fetch_nasa_power(lat, lon, parameters, start, end, community="re", max_retries=5, timeout=120):
    """Call the NASA POWER API for a date range, retrying on network errors."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "parameters": parameters,
        "community": community,
        "start": start,
        "end": end,
        "format": "JSON"
    }

    # NASA POWER API đôi khi phản hồi khá chậm do phải query lượng dữ liệu lớn,
    # nên timeout được set mặc định là 120s.
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

        # Lỗi 4xx (Sai định dạng ngày, tham số không tồn tại...)
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    raise RuntimeError("Max retries exceeded")


def get_s3_client(endpoint, access_key, secret_key):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


def ensure_bucket(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
        print(f"Created bucket: {bucket}")


def upload_json(s3, bucket, key, payload):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def main():
    ap = argparse.ArgumentParser(description="Fetch NASA POWER data and upload raw JSON to MinIO")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--farm-id", type=str, default="CUCHI_02")
    
    # NASA specific arguments
    ap.add_argument("--parameters", type=str, default="ALLSKY_SFC_SW_DWN")
    ap.add_argument("--community", type=str, default="re") # 're' (Renewable Energy) hoặc 'ag' (Agroclimatology)
    
    # Mặc định lấy 7 ngày qua nếu không truyền start/end
    default_end = datetime.now(timezone.utc) - timedelta(days=1)
    default_start = default_end - timedelta(days=7)
    ap.add_argument("--start", type=str, default=default_start.strftime("%Y%m%d"), help="Format YYYYMMDD")
    ap.add_argument("--end", type=str, default=default_end.strftime("%Y%m%d"), help="Format YYYYMMDD")
    
    # Cấu hình MinIO
    ap.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    ap.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "admin"))
    ap.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "password123"))
    ap.add_argument("--bucket", default="landing-zone")
    args = ap.parse_args()

    print(f"Fetching NASA POWER data for {args.farm_id} from {args.start} to {args.end}...")
    payload = fetch_nasa_power(args.lat, args.lon, args.parameters, args.start, args.end, args.community)

    now = datetime.now(timezone.utc)
    
    # Thiết kế Key: Thêm ingest_dt để quản lý luồng nạp batch
    key = (
        f"nasa_power/farm_id={args.farm_id}/ingest_dt={now:%Y-%m-%d}/"
        f"data_{args.start}_to_{args.end}_{now:%H%M%SZ}.json"
    )

    s3 = get_s3_client(args.endpoint, args.access_key, args.secret_key)
    ensure_bucket(s3, args.bucket)
    upload_json(s3, args.bucket, key, payload)

    print(f"Uploaded s3://{args.bucket}/{key}")


if __name__ == "__main__":
    main()