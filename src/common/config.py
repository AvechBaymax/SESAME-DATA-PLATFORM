"""Shared configuration: load .env once and expose typed settings."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (parent of src/).
# Variables already exported in the shell take precedence over .env.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def require_env(name: str) -> str:
    """Return an environment variable or fail loudly (no silent default credentials)."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class MinioSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str


def get_minio_settings(endpoint=None, access_key=None, secret_key=None, bucket=None) -> MinioSettings:
    """
    Build MinIO settings. Explicit arguments (e.g. CLI flags) win over environment variables.
    Endpoint and credentials are required; the bucket falls back to 'landing-zone'.
    """
    return MinioSettings(
        endpoint=endpoint or require_env("MINIO_ENDPOINT"),
        access_key=access_key or require_env("MINIO_ACCESS_KEY"),
        secret_key=secret_key or require_env("MINIO_SECRET_KEY"),
        bucket=bucket or os.getenv("MINIO_BUCKET", "landing-zone"),
    )


def get_kafka_broker(broker=None) -> str:
    """Kafka bootstrap server; defaults to localhost for local dev."""
    return broker or os.getenv("KAFKA_BROKER", "localhost:9092")


def get_sensor_topic(topic=None) -> str:
    """Raw IoT topic; the name follows the architecture slides."""
    return topic or os.getenv("KAFKA_TOPIC_SENSOR_RAW", "sesame.sensor.raw")


def get_sensor_dlq_topic(topic=None) -> str:
    return topic or os.getenv("KAFKA_TOPIC_SENSOR_DLQ", "sesame.sensor.raw.dlq")


def add_minio_args(parser):
    """Register the MinIO flags. They default to None so env vars/.env apply
    through get_minio_settings(args.endpoint, ...) or minio_settings_from_args(args)."""
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--access-key", default=None)
    parser.add_argument("--secret-key", default=None)
    parser.add_argument("--bucket", default=None)


def minio_settings_from_args(args) -> MinioSettings:
    return get_minio_settings(args.endpoint, args.access_key, args.secret_key, args.bucket)
