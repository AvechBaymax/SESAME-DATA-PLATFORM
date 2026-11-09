"""Create the MinIO buckets the platform needs, waiting until MinIO answers.

Run by the airflow-init container:  python -m common.init_buckets
  landing-zone : raw files from ingesters and mocks (MINIO_BUCKET)
  lakehouse    : Delta tables for Bronze/Silver/Gold (LAKEHOUSE_BUCKET)
"""
import os
import sys
import time

from botocore.exceptions import BotoCoreError, ClientError

from common.config import get_minio_settings
from common.minio_utils import ensure_bucket, get_s3_client


def main(timeout_s=120):
    settings = get_minio_settings()
    buckets = [settings.bucket, os.getenv("LAKEHOUSE_BUCKET", "lakehouse")]
    s3 = get_s3_client(settings)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            s3.list_buckets()
            break
        except (BotoCoreError, ClientError) as exc:
            if time.monotonic() > deadline:
                print(f"MinIO not reachable at {settings.endpoint}: {exc}", file=sys.stderr)
                return 1
            print("waiting for MinIO...")
            time.sleep(2)
    for bucket in buckets:
        ensure_bucket(s3, bucket)
    print(f"Buckets ready: {', '.join(buckets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
