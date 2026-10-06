"""Shared configuration: load .env once and expose typed settings."""
import os
from dataclasses import dataclass
from pathlib import Path
 
from dotenv import load_dotenv
 
# Load .env from the project root (parent of common/).
# Variables already exported in the shell take precedence over .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
 
 
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
 