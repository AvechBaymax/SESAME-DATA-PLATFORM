"""Shared MinIO (S3) helpers used by every ingestion / mock script."""
import json
from datetime import datetime, timezone

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import MinioSettings, get_minio_settings

_checked_buckets: set = set()  # avoid a head_bucket call on every upload


def get_s3_client(settings: MinioSettings | None = None):
    """S3 client pointed at MinIO (path-style addressing is required)."""
    settings = settings or get_minio_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",  # MinIO ignores the region but boto3 needs one
    )


def ensure_bucket(s3, bucket: str) -> None:
    """Create the bucket if it does not exist yet (checked once per process)."""
    if bucket in _checked_buckets:
        return
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise  # e.g. 403 wrong credentials: creating the bucket would fail too
        s3.create_bucket(Bucket=bucket)
        print(f"Created bucket: {bucket}")
    _checked_buckets.add(bucket)


def upload_json(s3, bucket: str, key: str, payload, indent: int | None = None) -> None:
    """Upload a JSON-serializable payload as an object."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=indent).encode("utf-8"),
        ContentType="application/json",
    )


def utc_now_compact() -> str:
    """UTC timestamp for file names, e.g. 20261006T081500Z."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_today() -> str:
    """UTC date for dt= partitions, e.g. 2026-10-06."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_key(prefix: str, filename: str, **partitions) -> str:
    """
    Build a Hive-style object key. Partition order follows keyword order.
      build_key("soilgrids", "x.json", dt="2026-10-06", farm_id="CUCHI_02")
      -> soilgrids/dt=2026-10-06/farm_id=CUCHI_02/x.json
    """
    parts = [prefix.strip("/")] + [f"{k}={v}" for k, v in partitions.items()] + [filename]
    return "/".join(parts)


def upload_raw_json(
    prefix: str,
    payload,
    filename: str | None = None,
    settings: MinioSettings | None = None,
    indent: int | None = None,
    **partitions,
) -> str:
    """
    One-call helper for landing-zone ingestion: build key, ensure bucket, upload.
    Returns the s3:// URI of the new object.

      upload_raw_json("soilgrids", payload, dt=utc_today(), lat=11.0, lon=106.4)
    """
    settings = settings or get_minio_settings()
    filename = filename or f"{utc_now_compact()}.json"
    key = build_key(prefix, filename, **partitions)

    s3 = get_s3_client(settings)
    ensure_bucket(s3, settings.bucket)
    upload_json(s3, settings.bucket, key, payload, indent=indent)
    return f"s3://{settings.bucket}/{key}"
