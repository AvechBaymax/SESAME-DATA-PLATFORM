#!/usr/bin/env python3
"""
Upload static FAO-56 and FAO-33 reference parameters for Sesame to MinIO.

Usage:
  python mock_fao_db.py
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
        "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1"
    )

def ensure_bucket(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)

def generate_fao_sesame_params():
    """Số hóa thông số chuẩn của cây Mè (Sesame) từ tài liệu FAO-56 và FAO-33"""
    return {
        "crop_id": "sesame",
        "crop_name": "Mè (Sesame)",
        "source": "FAO Irrigation and Drainage Paper No. 56 & 33",
        "growth_stages_days": {
            # Chia tổng 75 ngày sinh trưởng (giống ngắn ngày Bình Thuận) thành 4 giai đoạn FAO
            "initial": 15,      # Giai đoạn đầu (Lên mầm đến 10% độ phủ)
            "development": 25,  # Giai đoạn phát triển (Từ 10% đến che phủ hoàn toàn/ra hoa)
            "mid_season": 25,   # Giai đoạn giữa (Ra hoa đến bắt đầu chín)
            "late_season": 10   # Giai đoạn cuối (Chín đến thu hoạch)
        },
        "kc_coefficients": {
            # Hệ số cây trồng (Kc) theo FAO-56
            "kc_ini": 0.35,
            "kc_mid": 1.10,
            "kc_end": 0.25
        },
        "root_depth_m": {
            # Độ sâu rễ (Zr) tính bằng mét
            "min": 0.3,
            "max": 1.0
        },
        "water_depletion_fraction": 0.5, # Hệ số cạn kiệt cho phép (p) - FAO-56
        "yield_response_factor_ky": {
            # Hệ số Ky theo FAO-33 (Ảnh hưởng của stress nước tới năng suất)
            "vegetative": 0.6,
            "flowering": 1.0,
            "yield_formation": 1.0,
            "ripening": 0.2,
            "total_season": 1.1
        },
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    ap.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "admin"))
    ap.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "password123"))
    ap.add_argument("--bucket", default="landing-zone")
    args = ap.parse_args()

    payload = generate_fao_sesame_params()
    
    # Lưu dạng bảng static Dimension
    key = "fao_reference/crop=sesame/fao_56_33_params.json"

    s3 = get_s3_client(args.endpoint, args.access_key, args.secret_key)
    ensure_bucket(s3, args.bucket)
    
    s3.put_object(
        Bucket=args.bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"Uploaded FAO reference data to s3://{args.bucket}/{key}")

if __name__ == "__main__":
    main()