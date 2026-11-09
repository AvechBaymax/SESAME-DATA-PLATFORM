"""Landing-zone sources and the Bronze tables they load into.

Each landing object key carries Hive-style partitions (farm_id=..., season_id=...,
crop=...); they become plain columns in Bronze so later layers can filter on them.
"""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LandingSource:
    name: str                     # CLI name, also stored in _source
    table: str                    # Bronze table name (without catalog/layer)
    prefixes: tuple               # landing-zone prefixes to read
    partitions: tuple = ()        # Hive partition keys copied into columns
    columns: dict = field(default_factory=dict)  # column -> {prefix: constant}


SOURCES = {
    s.name: s
    for s in (
        LandingSource("nasa_power", "nasa_power_raw", ("nasa_power",), ("farm_id", "ingest_dt")),
        LandingSource("owm", "owm_raw", ("weather",), ("farm_id", "dt")),
        LandingSource("soilgrids", "soilgrids_raw", ("soilgrids",), ("farm_id",)),
        LandingSource("user_config", "user_config_raw", ("user_config",), ("farm_id",)),
        LandingSource("field_operation", "field_operation_raw", ("field_operation",),
                      ("farm_id", "season_id")),
        LandingSource("reference", "reference_raw", ("fao_reference", "agronomy_reference"),
                      ("crop",), {"reference_type": {"fao_reference": "fao",
                                                      "agronomy_reference": "agronomy"}}),
    )
}


def partition_pattern(key):
    """Regex that captures a Hive partition value from an object path."""
    return rf"/{re.escape(key)}=([^/]+)/"


def parse_partitions(path, keys):
    """{key: value} for the Hive partitions found in a path (missing keys are None)."""
    out = {}
    for key in keys:
        match = re.search(partition_pattern(key), path)
        out[key] = match.group(1) if match else None
    return out
