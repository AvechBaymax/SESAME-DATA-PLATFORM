"""Shared settings read from environment variables (.env is loaded once here)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    landing_bucket: str
    kafka_broker: str
    topic_sensor_raw: str
    topic_sensor_dlq: str


def get_settings():
    return Settings(
        minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "admin"),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "password123"),
        landing_bucket=os.getenv("LANDING_BUCKET", "landing-zone"),
        kafka_broker=os.getenv("KAFKA_BROKER", "localhost:9092"),
        # Topic names follow the architecture slides (sesame.sensor.raw / .dlq).
        topic_sensor_raw=os.getenv("KAFKA_TOPIC_SENSOR_RAW", "sesame.sensor.raw"),
        topic_sensor_dlq=os.getenv("KAFKA_TOPIC_SENSOR_DLQ", "sesame.sensor.raw.dlq"),
    )


def add_minio_args(parser, settings=None):
    """Register the --endpoint/--access-key/--secret-key/--bucket flags used by every uploader."""
    settings = settings or get_settings()
    parser.add_argument("--endpoint", default=settings.minio_endpoint)
    parser.add_argument("--access-key", default=settings.minio_access_key)
    parser.add_argument("--secret-key", default=settings.minio_secret_key)
    parser.add_argument("--bucket", default=settings.landing_bucket)
