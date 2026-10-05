#!/usr/bin/env python3
"""
Generate and upload mock user/farm configuration data to MinIO.

Usage:
  python mock_user_config.py
  python mock_user_config.py --farm-id BINHTHUAN_01 --lat 11.2 --lon 106.5

Object key layout:
  user_config/farm_id=<farm_id>/config_<timestamp>.json
"""
import argparse
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

load_dotenv()

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
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

def generate_mock_config(farm_id, lat, lon, area_m2, perimeter_m):
    """Tạo payload JSON chứa thông tin cấu hình nông trại."""
    now = datetime.now(timezone.utc).isoformat()
    
    return {
        "farm_id": farm_id, # Tạm thời tự định nghĩa, sau này có thể dùng UUID
        "location": {
            "latitude": lat,
            "longitude": lon,
            "region": "Vietnam"
        },
        "geometry": {
            "area_m2": area_m2,          # Diện tích canh tác
            "perimeter_m": perimeter_m   # Chu vi lô đất
        },
        "crop_metadata": {
            "crop_type": "Mè đen 2 vỏ Bình Thuận",
            "planting_date": "2026-10-01",  # Ngày xuống giống giả định
            "soil_type_ref": "isric_soilgrids_v2", # Tham chiếu đến nguồn dữ liệu đất
            "expected_harvest_days": 75
        },
        "system_metadata": {
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "note": "Mock config for PoC Data Lakehouse"
        }
    }

def main():
    ap = argparse.ArgumentParser(description="Generate and upload mock farm config to MinIO")
    # Các tham số cấu hình nông trại
    ap.add_argument("--farm-id", type=str, default="CUCHI_02")
    ap.add_argument("--lat", type=float, default=11.0)
    ap.add_argument("--lon", type=float, default=106.4)
    ap.add_argument("--area", type=float, default=5000.0, help="Area in square meters")
    ap.add_argument("--perimeter", type=float, default=300.0, help="Perimeter in meters")
    
    # Cấu hình MinIO
    ap.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    ap.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "admin"))
    ap.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "password123"))
    ap.add_argument("--bucket", default="landing-zone")
    args = ap.parse_args()

    # Tạo payload
    payload = generate_mock_config(args.farm_id, args.lat, args.lon, args.area, args.perimeter)

    # Thiết kế Key: Lưu theo farm_id
    now = datetime.now(timezone.utc)
    key = f"user_config/farm_id={args.farm_id}/config_{now:%Y%m%dT%H%M%SZ}.json"

    # Upload lên MinIO
    s3 = get_s3_client(args.endpoint, args.access_key, args.secret_key)
    ensure_bucket(s3, args.bucket)
    upload_json(s3, args.bucket, key, payload)

    print(f"Generated mock config for {args.farm_id}")
    print(f"Uploaded s3://{args.bucket}/{key}")
    print("Payload preview:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()