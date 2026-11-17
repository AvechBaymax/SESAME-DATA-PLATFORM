"""Sensor stream: table schemas, message schema, field mapping and quality rules.

Plain Python (no pyspark import), so the mapping can be checked against the
messages the simulator produces. jobs/sensor_stream.py uses these definitions:

  Kafka sesame.sensor.raw --(query 1)--> bronze.sensor_raw          (every message, as received)
  bronze.sensor_raw       --(query 2)--> silver.fact_sensor_reading (valid readings)
                                     \\-> silver.quarantine_sensor   (rejected, with the rule)
                                      \\-> Kafka sesame.sensor.raw.dlq (unreadable messages)
"""

BRONZE_TABLE = "sensor_raw"
READING_TABLE = "fact_sensor_reading"
QUARANTINE_TABLE = "quarantine_sensor"
FAULT_HEADER = "x-mock-fault"  # set by the mock producer on injected faults, never by real devices

BRONZE_DDL = ("kafka_key STRING, kafka_topic STRING, kafka_partition INT, kafka_offset BIGINT, "
              "kafka_ts TIMESTAMP, headers MAP<STRING, STRING>, value STRING, _ingested_at TIMESTAMP")
BRONZE_PARTITION = "days(kafka_ts)"  # arrival day: Bronze does not parse the payload

# Schema of a sensor message (see mock_iot/simulator.py SensorNode._message).
MESSAGE_SCHEMA = (
    "schema_version STRING, farm_id STRING, plot_id STRING, device_id STRING, device_type STRING, "
    "timestamp BIGINT, datetime STRING, sample_interval_s INT, "
    "measurements STRUCT<"
    "soil: STRUCT<depth_cm: INT, vwc_pct: DOUBLE, temp_c: DOUBLE, bulk_ec_ds_m: DOUBLE, "
    "water_potential_kpa: DOUBLE, oxygen_level_pct: DOUBLE>, "
    "canopy: STRUCT<leaf_wetness_min: DOUBLE, surface_temp_c: DOUBLE>, "
    "weather_micro: STRUCT<ambient_temp_c: DOUBLE, humidity_pct: DOUBLE, rain_mm: DOUBLE, "
    "wind_speed_m_s: DOUBLE, solar_rad_w_m2: DOUBLE>>, "
    "telemetry STRUCT<battery_pct: DOUBLE, rssi_dbm: INT, error_codes: ARRAY<STRING>>, "
    "flags STRUCT<rain_detected: BOOLEAN, irrigation_active: BOOLEAN>"
)

# Silver column -> (Spark SQL type, path in the parsed message `m`)
READING_FIELDS = {
    "device_id": ("STRING", "device_id"),
    "farm_id": ("STRING", "farm_id"),
    "plot_id": ("STRING", "plot_id"),
    "sample_interval_s": ("INT", "sample_interval_s"),
    "vwc_pct": ("DOUBLE", "measurements.soil.vwc_pct"),
    "soil_temp_c": ("DOUBLE", "measurements.soil.temp_c"),
    "bulk_ec_ds_m": ("DOUBLE", "measurements.soil.bulk_ec_ds_m"),
    "water_potential_kpa": ("DOUBLE", "measurements.soil.water_potential_kpa"),
    "soil_oxygen_pct": ("DOUBLE", "measurements.soil.oxygen_level_pct"),
    "leaf_wetness_min": ("DOUBLE", "measurements.canopy.leaf_wetness_min"),
    "canopy_temp_c": ("DOUBLE", "measurements.canopy.surface_temp_c"),
    "air_temp_c": ("DOUBLE", "measurements.weather_micro.ambient_temp_c"),
    "rh_pct": ("DOUBLE", "measurements.weather_micro.humidity_pct"),
    "rain_mm": ("DOUBLE", "measurements.weather_micro.rain_mm"),
    "wind_m_s": ("DOUBLE", "measurements.weather_micro.wind_speed_m_s"),
    "solar_w_m2": ("DOUBLE", "measurements.weather_micro.solar_rad_w_m2"),
    "battery_pct": ("DOUBLE", "telemetry.battery_pct"),
    "rssi_dbm": ("INT", "telemetry.rssi_dbm"),
    "irrigation_active": ("BOOLEAN", "flags.irrigation_active"),
}

READING_DDL = ("device_id STRING, farm_id STRING, plot_id STRING, ts TIMESTAMP, "
               + ", ".join(f"{c} {t}" for c, (t, _) in READING_FIELDS.items()
                           if c not in ("device_id", "farm_id", "plot_id"))
               + ", kafka_partition INT, kafka_offset BIGINT, _updated_at TIMESTAMP")
READING_PARTITION = "days(ts)"
READING_KEYS = ("device_id", "ts")

QUARANTINE_DDL = ("kafka_partition INT, kafka_offset BIGINT, device_id STRING, ts TIMESTAMP, "
                  "raw_value STRING, dq_rule STRING, dq_detail STRING, mock_fault STRING, "
                  "quarantined_at TIMESTAMP")
QUARANTINE_KEYS = ("kafka_partition", "kafka_offset")

# Rules in the order they are checked; a message is quarantined under the first
# rule it breaks. (name, SQL condition that must hold, SQL detail when it does not,
# also sent to the DLQ topic). Columns: `m` = parsed message, `value` = raw text.
RULES = (
    ("valid_json", "m IS NOT NULL", "substr(value, 1, 120)", True),
    ("required_fields",
     "m.device_id IS NOT NULL AND m.timestamp IS NOT NULL AND m.measurements.soil IS NOT NULL "
     "AND m.measurements.canopy IS NOT NULL AND m.measurements.weather_micro IS NOT NULL",
     "concat_ws(',', CASE WHEN m.device_id IS NULL THEN 'device_id' END, "
     "CASE WHEN m.timestamp IS NULL THEN 'timestamp' END, "
     "CASE WHEN m.measurements.soil IS NULL THEN 'soil' END, "
     "CASE WHEN m.measurements.canopy IS NULL THEN 'canopy' END, "
     "CASE WHEN m.measurements.weather_micro IS NULL THEN 'weather_micro' END)", True),
    ("vwc_pct 0-60", "m.measurements.soil.vwc_pct BETWEEN 0 AND 60",
     "cast(m.measurements.soil.vwc_pct AS STRING)", False),
    ("rh_pct 0-100", "m.measurements.weather_micro.humidity_pct BETWEEN 0 AND 100",
     "cast(m.measurements.weather_micro.humidity_pct AS STRING)", False),
    ("air_temp_c 5-50", "m.measurements.weather_micro.ambient_temp_c BETWEEN 5 AND 50",
     "cast(m.measurements.weather_micro.ambient_temp_c AS STRING)", False),
    ("water_potential_kpa < 0", "m.measurements.soil.water_potential_kpa < 0",
     "cast(m.measurements.soil.water_potential_kpa AS STRING)", False),
    ("battery_pct 0-100", "m.telemetry.battery_pct BETWEEN 0 AND 100",
     "cast(m.telemetry.battery_pct AS STRING)", False),
)
KNOWN_DEVICE_RULE = "device_id in dim_device"


def rule_case_sql():
    """SQL CASE giving the first broken rule's name, or NULL when every rule holds.

    A NULL condition (missing value) counts as broken here: a reading without
    its key measurements is not usable.
    """
    whens = " ".join(f"WHEN NOT coalesce(({cond}), false) THEN '{name}'" for name, cond, _, _ in RULES)
    return f"CASE {whens} END"


def detail_case_sql():
    whens = " ".join(f"WHEN NOT coalesce(({cond}), false) THEN {detail}" for _, cond, detail, _ in RULES)
    return f"CASE {whens} END"


def dlq_rules():
    return [name for name, _, _, to_dlq in RULES if to_dlq]


def message_path(message, path):
    """Value at a dotted path in a message dict (None when a part is missing)."""
    value = message
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value
