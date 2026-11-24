"""Serving layer: the Postgres tables the web app reads (schema `serving`).

Plain Python (no pyspark import): one definition used to create the tables
(common.init_serving), to fill them (jobs/gold_to_serving.py, jobs/sensor_stream.py)
and to document them for the web team (`python -m common.init_serving --print-ddl`).

Roles (docs: Web ↔ Data interface spec):
  data_platform  owns schema serving (writes); reads schema app
  web_app        reads schema serving; owns schema app (the web app's own tables)
"""
import os
from dataclasses import dataclass

SCHEMA = "serving"
APP_SCHEMA = "app"
DATA_ROLE = "data_platform"
WEB_ROLE = "web_app"
UPDATED = ("updated_at", "timestamptz")


@dataclass(frozen=True)
class ServingTable:
    name: str
    columns: tuple            # ((column, Postgres type), ...)
    keys: tuple
    refresh: str              # who fills it and how often (documentation)
    description: str

    @property
    def column_names(self):
        return [c for c, _ in self.columns]

    @property
    def qualified(self):
        return f"{SCHEMA}.{self.name}"


def _t(name, keys, refresh, description, *columns, updated=True):
    cols = tuple(columns) + ((UPDATED,) if updated else ())
    return ServingTable(name, cols, keys, refresh, description)


TEXT, DBL, INT, DATE, BOOL, TS = "text", "double precision", "integer", "date", "boolean", "timestamptz"
DAILY = "gold_to_serving after gold_build (bootstrap and daily DAG)"

TABLES = {t.name: t for t in (
    _t("farm_status", ("plot_id",), DAILY,
       "One row per plot: its latest season and how it is doing on the last computed day (map, KPIs)",
       ("plot_id", TEXT), ("farm_id", TEXT), ("season_id", TEXT), ("province", TEXT),
       ("latitude", DBL), ("longitude", DBL), ("geometry", TEXT), ("soil_texture", TEXT), ("drainage", TEXT),
       ("as_of_date", DATE), ("das", INT), ("stage", TEXT), ("depletion_pct_taw", DBL), ("ks", DBL),
       ("vwc_mean_pct", DBL), ("status_color", TEXT), ("open_alerts", INT), ("season_critical_alerts", INT),
       ("last_reading_ts", TS)),
    _t("region_daily", ("province", "date"), DAILY, "Farms per province and day (gold.agg_region_daily)",
       ("province", TEXT), ("date", DATE), ("farms_active", INT), ("seasons_active", INT),
       ("farms_water_stress", INT), ("farms_waterlogged", INT), ("farms_heat_stress", INT),
       ("avg_depletion_pct_taw", DBL), ("max_depletion_pct_taw", DBL), ("avg_et0_mm", DBL),
       ("avg_precip_mm", DBL), ("max_precip_mm", DBL), ("avg_vwc_mean_pct", DBL)),
    _t("season_info", ("season_id",), DAILY, "One row per season, with its stages as JSON",
       ("season_id", TEXT), ("plot_id", TEXT), ("farm_id", TEXT), ("crop_id", TEXT), ("variety", TEXT),
       ("season_type", TEXT), ("planting_date", DATE), ("expected_harvest_date", DATE),
       ("actual_harvest_date", DATE), ("season_length_days", INT), ("today_das", INT), ("today_stage", TEXT),
       ("stages", TEXT), ("in_recommended_sowing_window", BOOL), ("seed_rate_kg_ha", DBL),
       ("irrigation_method", TEXT)),
    _t("season_daily", ("season_id", "date"), DAILY, "Daily water balance per season (gold.feat_season_daily)",
       ("season_id", TEXT), ("date", DATE), ("farm_id", TEXT), ("plot_id", TEXT), ("das", INT), ("stage", TEXT),
       ("kc", DBL), ("et0_mm", DBL), ("etc_mm", DBL), ("eta_mm", DBL), ("ks", DBL), ("precip_mm", DBL),
       ("rain_source", TEXT), ("nasa_precip_mm", DBL), ("sensor_rain_mm", DBL), ("effective_precip_mm", DBL),
       ("irrigation_mm", DBL), ("depletion_mm", DBL), ("depletion_pct_taw", DBL), ("raw_mm", DBL),
       ("taw_mm", DBL), ("deep_percolation_mm", DBL), ("tmin_c", DBL), ("tmax_c", DBL), ("gdd_cum", DBL),
       ("vwc_mean_pct", DBL), ("vwc_min_pct", DBL), ("vwc_max_pct", DBL), ("soil_oxygen_min_pct", DBL),
       ("leaf_wet_hours", DBL), ("sensor_coverage_pct", DBL), ("water_stress_flag", BOOL),
       ("heat_stress_flag", BOOL), ("waterlog_flag", BOOL)),
    _t("sensor_recent", ("device_id", "ts"),
       "sensor stream, every micro-batch (about each minute); keeps the last 7 days of data",
       "15-minute readings of the last 7 days of data",
       ("device_id", TEXT), ("plot_id", TEXT), ("farm_id", TEXT), ("ts", TS), ("sample_interval_s", INT),
       ("vwc_pct", DBL), ("soil_temp_c", DBL), ("water_potential_kpa", DBL), ("soil_oxygen_pct", DBL),
       ("air_temp_c", DBL), ("rh_pct", DBL), ("rain_mm", DBL), ("irrigation_mm", DBL), ("battery_pct", DBL),
       ("rssi_dbm", INT), updated=False),
    _t("weather_daily", ("farm_id", "date", "source"), DAILY, "Daily weather per farm: NASA POWER and the plot sensors",
       ("farm_id", TEXT), ("date", DATE), ("source", TEXT), ("tmin_c", DBL), ("tmax_c", DBL),
       ("rh_pct", DBL), ("precip_mm", DBL)),
    _t("field_operations", ("operation_id",), DAILY, "Planned and done field operations (silver.fact_field_operation)",
       ("operation_id", TEXT), ("season_id", TEXT), ("plot_id", TEXT), ("op_type", TEXT), ("das", INT),
       ("planned_date", DATE), ("actual_date", DATE), ("status", TEXT), ("details", TEXT)),
    _t("soil_layers", ("farm_id", "depth", "source"), DAILY, "Soil profile per farm (silver.dim_soil_layer)",
       ("farm_id", TEXT), ("depth", TEXT), ("top_cm", INT), ("bottom_cm", INT), ("sand_pct", DBL),
       ("clay_pct", DBL), ("silt_pct", DBL), ("ph", DBL), ("soc_g_kg", DBL), ("nitrogen_g_kg", DBL),
       ("bulk_density_g_cm3", DBL), ("cec_cmol_kg", DBL), ("source", TEXT), ("is_mock", BOOL)),
    _t("alerts", ("alert_id",), DAILY, "Alerts (gold.alert_event); acknowledgements live in app.alert_acks",
       ("alert_id", TEXT), ("season_id", TEXT), ("farm_id", TEXT), ("plot_id", TEXT), ("device_id", TEXT),
       ("rule", TEXT), ("severity", TEXT), ("start_date", DATE), ("end_date", DATE), ("days", INT),
       ("ongoing", BOOL), ("value", DBL), ("threshold", DBL), ("unit", TEXT), ("message", TEXT)),
    _t("data_quality_daily", ("date", "dq_rule"), DAILY, "Rejected sensor messages per day and rule",
       ("date", DATE), ("dq_rule", TEXT), ("rejected", INT), ("sent_to_dlq", INT)),
    _t("season_summary", ("season_id",), DAILY, "Season totals (gold.agg_season_summary)",
       ("season_id", TEXT), ("farm_id", TEXT), ("plot_id", TEXT), ("planting_date", DATE),
       ("harvest_date", DATE), ("status", TEXT), ("days", INT), ("precip_total_mm", DBL),
       ("effective_precip_total_mm", DBL), ("irrigation_total_mm", DBL), ("irrigation_events", INT),
       ("etc_total_mm", DBL), ("eta_total_mm", DBL), ("eta_etc_ratio", DBL), ("deep_percolation_total_mm", DBL),
       ("water_stress_days", INT), ("stress_days_flowering", INT), ("waterlog_days", INT),
       ("heat_stress_days", INT), ("rain_mm_harvest_window", DBL), ("gdd_total", DBL), ("vwc_mean_pct", DBL),
       ("sensor_coverage_pct", DBL)),
)}

# Batch tables (everything but the stream's sensor_recent)
BATCH_TABLES = [t for t in TABLES.values() if t.name != "sensor_recent"]
SENSOR_STAGE = f"{SCHEMA}._sensor_stage"   # the stream lands each batch here first
RECENT_DAYS = 7

DEVICE_HEALTH_VIEW = f"""
CREATE OR REPLACE VIEW {SCHEMA}.device_health AS
WITH last AS (
    SELECT DISTINCT ON (device_id) device_id, plot_id, farm_id, ts AS last_seen_ts,
           battery_pct, rssi_dbm, coalesce(sample_interval_s, 900) AS sample_interval_s
    FROM {SCHEMA}.sensor_recent
    ORDER BY device_id, ts DESC
)
SELECT l.device_id, l.plot_id, l.farm_id, l.last_seen_ts, l.battery_pct, l.rssi_dbm,
       round((100.0 * (SELECT count(*) FROM {SCHEMA}.sensor_recent r
                        WHERE r.device_id = l.device_id AND r.ts > l.last_seen_ts - interval '24 hours')
              / (86400.0 / greatest(l.sample_interval_s, 1)))::numeric, 1)::double precision AS coverage_pct_24h,
       round((extract(epoch FROM now() - l.last_seen_ts) / 60)::numeric)::integer AS minutes_since_last
FROM last l
"""


def table_ddl(table):
    cols = ",\n    ".join(f"{c} {t}" for c, t in table.columns)
    return (f"CREATE TABLE IF NOT EXISTS {table.qualified} (\n    {cols},\n"
            f"    PRIMARY KEY ({', '.join(table.keys)})\n)")


def ddl_statements():
    """Idempotent DDL for schema serving: tables, columns added later, the stage table, the view."""
    out = []
    for table in TABLES.values():
        out.append(table_ddl(table))
        out += [f"ALTER TABLE {table.qualified} ADD COLUMN IF NOT EXISTS {c} {t}" for c, t in table.columns]
    recent = TABLES["sensor_recent"]
    stage_cols = ", ".join(f"{c} {t}" for c, t in recent.columns)
    out.append(f"CREATE UNLOGGED TABLE IF NOT EXISTS {SENSOR_STAGE} ({stage_cols})")
    out.append(DEVICE_HEALTH_VIEW.strip())
    return out


def recent_upsert_sql():
    """Move the stage into sensor_recent (replays are ignored), then keep the last RECENT_DAYS of data."""
    cols = ", ".join(TABLES["sensor_recent"].column_names)
    return [
        f"INSERT INTO {SCHEMA}.sensor_recent ({cols}) SELECT {cols} FROM {SENSOR_STAGE} "
        f"ON CONFLICT (device_id, ts) DO NOTHING",
        f"DELETE FROM {SCHEMA}.sensor_recent WHERE ts < "
        f"(SELECT max(ts) FROM {SCHEMA}.sensor_recent) - interval '{RECENT_DAYS} days'",
    ]


def jdbc_settings():
    """Where the data platform writes: JDBC URL and the data_platform login (env)."""
    url = os.getenv("SERVING_JDBC_URL", "")
    return {"url": url, "user": os.getenv("SERVING_DB_USER", DATA_ROLE),
            "password": os.getenv("SERVING_DB_PASSWORD", ""), "driver": "org.postgresql.Driver"} if url else None
