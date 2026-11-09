"""Table names and storage locations (see docs/data-model.md)."""
import os

CATALOG = os.getenv("LAKEHOUSE_CATALOG", "lakehouse")
LAYERS = ("bronze", "silver", "gold")


def table(layer, name):
    """Full Iceberg table name, e.g. table("bronze", "owm_raw") -> lakehouse.bronze.owm_raw."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; use one of {LAYERS}")
    return f"{CATALOG}.{layer}.{name}"


def landing_uri(prefix):
    """s3a:// URI of a landing-zone prefix, e.g. landing_uri("weather") -> s3a://landing-zone/weather."""
    bucket = os.getenv("MINIO_BUCKET", "landing-zone")
    return f"s3a://{bucket}/{prefix.strip('/')}"
