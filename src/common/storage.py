"""S3/MinIO helpers and the landing-zone key layout."""
import json

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def get_s3_client(endpoint, access_key, secret_key):
    """S3 client pointed at MinIO/Ceph RGW (path-style addressing is required)."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",  # MinIO ignores the region but boto3 needs one
    )


def s3_client_from_args(args):
    return get_s3_client(args.endpoint, args.access_key, args.secret_key)


def ensure_bucket(s3, bucket):
    """Create the bucket if it does not exist yet."""
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise  # e.g. 403: wrong credentials, creating would fail too
        s3.create_bucket(Bucket=bucket)
        print(f"Created bucket: {bucket}")


def upload_json(s3, bucket, key, payload, indent=None):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=indent).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{key}"


def landing_key(source, filename, farm_id=None, dt=None, **partitions):
    """Build a Hive-style landing key: <source>/farm_id=../dt=../<k>=<v>/<filename>."""
    parts = [source]
    if farm_id is not None:
        parts.append(f"farm_id={farm_id}")
    if dt is not None:
        parts.append(f"dt={dt:%Y-%m-%d}")
    parts.extend(f"{k}={v}" for k, v in partitions.items())
    parts.append(filename)
    return "/".join(parts)
