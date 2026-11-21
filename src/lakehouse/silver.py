"""Silver tables: schema, keys, data-quality checks and the Bronze payload parsers.

Plain Python (no pyspark import), so every parser can be unit-tested on the
payloads the ingesters and mocks write. The Spark job (jobs/bronze_to_silver.py)
runs the parser of a table over every Bronze row, keeps the newest row per key
and MERGEs the result into lakehouse.silver.<table>.

Columns follow docs/data-model.md. Every table also gets two lineage columns:
_file_path (the landing file the row came from) and _updated_at (Silver write time).
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

LINEAGE_COLUMNS = (("_file_path", "STRING"), ("_updated_at", "TIMESTAMP"))


@dataclass(frozen=True)
class Check:
    """A rule every row must meet. NULL counts as a pass (missing is not wrong)."""
    name: str
    condition: str            # Spark SQL boolean expression
    severity: str = "error"   # "error" stops the job before writing; "warn" only reports


@dataclass(frozen=True)
class SilverTable:
    name: str
    source: str               # Bronze source in lakehouse.sources.SOURCES
    columns: tuple            # ((column, Spark SQL type), ...) without the lineage columns
    keys: tuple               # primary key; the newest Bronze row per key wins
    parse: Callable           # (payload: dict, partitions: dict) -> list of row dicts
    partitioned_by: str = ""  # Iceberg transform, e.g. "months(date)"
    checks: tuple = ()
    references: tuple = ()    # foreign keys: ((column, silver table, column), ...)

    @property
    def column_names(self):
        return [name for name, _ in self.columns]

    def ddl(self):
        return ", ".join(f"{name} {kind}" for name, kind in self.columns + LINEAGE_COLUMNS)


# ---- value conversion ------------------------------------------------------------------

def coerce(value, kind):
    """Convert a JSON value to the Python type Spark expects for a column type."""
    if value is None:
        return None
    if kind == "STRING":
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if kind == "DOUBLE":
        return float(value)
    if kind == "INT":
        return int(value)
    if kind == "BOOLEAN":
        return bool(value)
    if kind == "DATE":
        return value if isinstance(value, date) else date.fromisoformat(value)
    if kind == "TIMESTAMP":
        return value if isinstance(value, datetime) else datetime.fromisoformat(value)
    raise ValueError(f"unsupported column type {kind!r}")


def to_tuple(table, row):
    """A parser row dict as a tuple in the table's column order, with Spark-ready types."""
    return tuple(coerce(row.get(name), kind) for name, kind in table.columns)


# ---- user_config -> dim_farm / dim_plot / dim_season / dim_device ----------------------

def parse_dim_farm(payload, partitions):
    farm = payload["farm"]
    return [{
        "farm_id": farm["farm_id"],
        "province": farm.get("province"),
        "country": farm.get("country"),
        "latitude": farm.get("latitude"),
        "longitude": farm.get("longitude"),
        "valid_from": (payload.get("system_metadata") or {}).get("updated_at"),
    }]


def parse_dim_plot(payload, partitions):
    plot = payload["plot"]
    return [{
        "plot_id": plot["plot_id"],
        "farm_id": plot.get("farm_id") or payload["farm"]["farm_id"],
        "geometry": plot.get("geometry"),
        "area_m2": plot.get("area_m2"),
        "perimeter_m": plot.get("perimeter_m"),
        "soil_texture": plot.get("soil_texture"),
        "drainage": plot.get("drainage"),
    }]


def parse_dim_season(payload, partitions):
    season = payload["season"]
    management = season.get("management") or {}
    seed_lot = season.get("seed_lot") or {}
    return [{
        "season_id": season["season_id"],
        "plot_id": season.get("plot_id"),
        "crop_id": season.get("crop_id"),
        "variety": season.get("variety"),
        "season_type": season.get("season_type"),
        "planting_date": season.get("planting_date"),
        "expected_harvest_date": season.get("expected_harvest_date"),
        "actual_harvest_date": season.get("actual_harvest_date"),
        "season_length_days": season.get("season_length_days"),
        "in_recommended_sowing_window": season.get("in_recommended_sowing_window"),
        "row_spacing_cm": management.get("row_spacing_cm"),
        "hill_spacing_cm": management.get("hill_spacing_cm"),
        "plants_per_hill": management.get("plants_per_hill"),
        "seed_rate_kg_ha": management.get("seed_rate_kg_ha"),
        "irrigation_method": management.get("irrigation_method"),
        "seed_lot_id": seed_lot.get("lot_id"),
        "w1000_g": seed_lot.get("w1000_g"),
        "germination_pct": seed_lot.get("germination_pct"),
        "purity_pct": seed_lot.get("purity_pct"),
        "seed_moisture_pct": seed_lot.get("moisture_pct"),
    }]


def parse_dim_device(payload, partitions):
    return [{
        "device_id": device["device_id"],
        "plot_id": device.get("plot_id"),
        "device_type": device.get("device_type"),
        "soil_probe_depth_cm": device.get("soil_probe_depth_cm"),
        "installed_date": device.get("installed_date"),
    } for device in payload.get("devices") or []]


# ---- field_operation -> fact_field_operation -------------------------------------------

def parse_fact_field_operation(payload, partitions):
    return [{
        "operation_id": op["operation_id"],
        "season_id": op.get("season_id") or payload.get("season_id"),
        "plot_id": op.get("plot_id"),
        "op_type": op.get("op_type"),
        "das": op.get("das"),
        "planned_date": op.get("planned_date"),
        "actual_date": op.get("actual_date"),
        "status": op.get("status"),
        "details": op.get("details"),
    } for op in payload.get("operations") or []]


# ---- reference (FAO) -> dim_growth_stage -----------------------------------------------

STAGES = ("initial", "development", "mid_season", "late_season")


def parse_dim_growth_stage(payload, partitions):
    """FAO-56 stages: Kc flat in initial and mid-season, linear in development and late season."""
    if partitions.get("reference_type") != "fao":
        return []  # the agronomy guide feeds other tables later
    lengths = payload["growth_stages_days"]
    kc = payload["kc_coefficients"]
    kc_by_stage = {
        "initial": (kc["kc_ini"], kc["kc_ini"]),
        "development": (kc["kc_ini"], kc["kc_mid"]),
        "mid_season": (kc["kc_mid"], kc["kc_mid"]),
        "late_season": (kc["kc_mid"], kc["kc_end"]),
    }
    rows, start = [], 0
    for order, stage in enumerate(STAGES, start=1):
        end = start + lengths[stage]
        rows.append({
            "crop_id": payload.get("crop_id") or partitions.get("crop"),
            "stage": stage,
            "stage_order": order,
            "das_start": start,
            "das_end": end,
            "kc_start": kc_by_stage[stage][0],
            "kc_end": kc_by_stage[stage][1],
        })
        start = end
    return rows


def parse_dim_crop(payload, partitions):
    """Crop constants of the FAO reference that the Gold water balance needs."""
    if partitions.get("reference_type") != "fao":
        return []
    kc = payload["kc_coefficients"]
    roots = payload.get("root_depth_m") or {}
    return [{
        "crop_id": payload.get("crop_id") or partitions.get("crop"),
        "crop_name": payload.get("crop_name"),
        "season_length_days": sum(payload["growth_stages_days"].values()),
        "kc_ini": kc["kc_ini"],
        "kc_mid": kc["kc_mid"],
        "kc_end": kc["kc_end"],
        "max_root_depth_low_m": roots.get("min"),
        "max_root_depth_high_m": roots.get("max"),
        "depletion_fraction_p": payload.get("water_depletion_fraction"),
        "max_crop_height_m": payload.get("max_crop_height_m"),
    }]


# ---- soilgrids -> dim_soil_layer --------------------------------------------------------

SOIL_PROPERTIES = {  # SoilGrids layer name -> Silver column
    "sand": "sand_pct",
    "clay": "clay_pct",
    "silt": "silt_pct",
    "phh2o": "ph",
    "soc": "soc_g_kg",
    "nitrogen": "nitrogen_g_kg",
    "bdod": "bulk_density_g_cm3",
    "cec": "cec_cmol_kg",
}


def parse_dim_soil_layer(payload, partitions):
    """One row per depth; mapped values are divided by the response's own d_factor."""
    by_depth = {}
    for layer in payload["properties"]["layers"]:
        column = SOIL_PROPERTIES.get(layer["name"])
        if column is None:
            continue
        d_factor = layer["unit_measure"]["d_factor"]
        for depth in layer["depths"]:
            row = by_depth.setdefault(depth["label"], {
                "farm_id": partitions.get("farm_id"),
                "plot_id": None,
                "depth": depth["label"],
                "top_cm": depth["range"]["top_depth"],
                "bottom_cm": depth["range"]["bottom_depth"],
                "source": "soilgrids",
                "is_mock": bool(payload.get("mock", False)),
            })
            mean = depth["values"].get("mean")
            row[column] = None if mean is None else mean / d_factor
    return sorted(by_depth.values(), key=lambda r: r["top_cm"])


# ---- nasa_power -> fact_weather_daily ---------------------------------------------------

NASA_COLUMNS = {
    "T2M_MIN": "tmin_c",
    "T2M_MAX": "tmax_c",
    "T2M": "tmean_c",
    "RH2M": "rh_pct",
    "WS2M": "wind_2m_m_s",
    "ALLSKY_SFC_SW_DWN": "rs_mj_m2",
    "PRECTOTCORR": "precip_mm",
}
# Solar radiation unit by NASA POWER community: AG gives MJ/m^2/day, RE gives kWh/m^2/day.
RADIATION_TO_MJ = {"MJ/m^2/day": 1.0, "kW-hr/m^2/day": 3.6, "kWh/m^2/day": 3.6}


def parse_fact_weather_daily(payload, partitions):
    """One row per day. Fill values (-999) become NULL; days with no value at all are dropped."""
    parameters = payload["properties"]["parameter"]
    fill = payload.get("header", {}).get("fill_value", -999.0)
    rs_factor = 1.0
    if "ALLSKY_SFC_SW_DWN" in parameters:
        unit = payload.get("parameters", {}).get("ALLSKY_SFC_SW_DWN", {}).get("units", "MJ/m^2/day")
        if unit not in RADIATION_TO_MJ:
            raise ValueError(f"unknown NASA POWER radiation unit {unit!r}")
        rs_factor = RADIATION_TO_MJ[unit]

    days = sorted({day for series in parameters.values() for day in series})
    rows = []
    for day in days:
        row = {"farm_id": partitions.get("farm_id"), "date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
               "source": "nasa_power", "is_mock": bool(payload.get("mock", False))}
        has_value = False
        for name, column in NASA_COLUMNS.items():
            value = parameters.get(name, {}).get(day)
            if value is None or value == fill:
                row[column] = None
                continue
            row[column] = value * rs_factor if name == "ALLSKY_SFC_SW_DWN" else value
            has_value = True
        if has_value:
            rows.append(row)
    return rows


# ---- table catalogue (order matters: a table comes after the tables it references) -----

TABLES = {t.name: t for t in (
    SilverTable(
        name="dim_farm", source="user_config", keys=("farm_id",), parse=parse_dim_farm,
        columns=(("farm_id", "STRING"), ("province", "STRING"), ("country", "STRING"),
                 ("latitude", "DOUBLE"), ("longitude", "DOUBLE"), ("valid_from", "TIMESTAMP")),
        checks=(Check("latitude in range", "latitude BETWEEN -90 AND 90"),
                Check("longitude in range", "longitude BETWEEN -180 AND 180")),
    ),
    SilverTable(
        name="dim_plot", source="user_config", keys=("plot_id",), parse=parse_dim_plot,
        columns=(("plot_id", "STRING"), ("farm_id", "STRING"), ("geometry", "STRING"),
                 ("area_m2", "DOUBLE"), ("perimeter_m", "DOUBLE"), ("soil_texture", "STRING"),
                 ("drainage", "STRING")),
        checks=(Check("area positive", "area_m2 > 0"),
                Check("known soil texture", "soil_texture IN ('sand', 'loamy_sand', 'sandy_loam', 'loam')"),
                Check("known drainage", "drainage IN ('good', 'poor')")),
        references=(("farm_id", "dim_farm", "farm_id"),),
    ),
    SilverTable(
        name="dim_season", source="user_config", keys=("season_id",), parse=parse_dim_season,
        columns=(("season_id", "STRING"), ("plot_id", "STRING"), ("crop_id", "STRING"),
                 ("variety", "STRING"), ("season_type", "STRING"), ("planting_date", "DATE"),
                 ("expected_harvest_date", "DATE"), ("actual_harvest_date", "DATE"),
                 ("season_length_days", "INT"), ("in_recommended_sowing_window", "BOOLEAN"),
                 ("row_spacing_cm", "DOUBLE"), ("hill_spacing_cm", "DOUBLE"),
                 ("plants_per_hill", "INT"), ("seed_rate_kg_ha", "DOUBLE"),
                 ("irrigation_method", "STRING"), ("seed_lot_id", "STRING"), ("w1000_g", "DOUBLE"),
                 ("germination_pct", "DOUBLE"), ("purity_pct", "DOUBLE"),
                 ("seed_moisture_pct", "DOUBLE")),
        checks=(Check("planting date set", "planting_date IS NOT NULL"),
                Check("harvest after planting", "expected_harvest_date > planting_date"),
                Check("sown in a recommended window", "in_recommended_sowing_window", "warn"),
                Check("germination >= 70 %", "germination_pct >= 70", "warn"),
                Check("purity >= 99 %", "purity_pct >= 99", "warn")),
        references=(("plot_id", "dim_plot", "plot_id"),),
    ),
    SilverTable(
        name="dim_device", source="user_config", keys=("device_id",), parse=parse_dim_device,
        columns=(("device_id", "STRING"), ("plot_id", "STRING"), ("device_type", "STRING"),
                 ("soil_probe_depth_cm", "INT"), ("installed_date", "DATE")),
        references=(("plot_id", "dim_plot", "plot_id"),),
    ),
    SilverTable(
        name="dim_growth_stage", source="reference", keys=("crop_id", "stage"),
        parse=parse_dim_growth_stage,
        columns=(("crop_id", "STRING"), ("stage", "STRING"), ("stage_order", "INT"),
                 ("das_start", "INT"), ("das_end", "INT"), ("kc_start", "DOUBLE"),
                 ("kc_end", "DOUBLE")),
        checks=(Check("stage has days", "das_end > das_start"),
                Check("Kc in range", "kc_start BETWEEN 0 AND 2 AND kc_end BETWEEN 0 AND 2")),
    ),
    SilverTable(
        name="dim_crop", source="reference", keys=("crop_id",), parse=parse_dim_crop,
        columns=(("crop_id", "STRING"), ("crop_name", "STRING"), ("season_length_days", "INT"),
                 ("kc_ini", "DOUBLE"), ("kc_mid", "DOUBLE"), ("kc_end", "DOUBLE"),
                 ("max_root_depth_low_m", "DOUBLE"), ("max_root_depth_high_m", "DOUBLE"),
                 ("depletion_fraction_p", "DOUBLE"), ("max_crop_height_m", "DOUBLE")),
        checks=(Check("p between 0 and 1", "depletion_fraction_p > 0 AND depletion_fraction_p < 1"),
                Check("root depth plausible", "max_root_depth_low_m BETWEEN 0.2 AND 3 "
                                              "AND max_root_depth_low_m <= max_root_depth_high_m")),
    ),
    SilverTable(
        name="dim_soil_layer", source="soilgrids", keys=("farm_id", "depth", "source"),
        parse=parse_dim_soil_layer,
        columns=(("farm_id", "STRING"), ("plot_id", "STRING"), ("depth", "STRING"),
                 ("top_cm", "INT"), ("bottom_cm", "INT"), ("sand_pct", "DOUBLE"),
                 ("clay_pct", "DOUBLE"), ("silt_pct", "DOUBLE"), ("ph", "DOUBLE"),
                 ("soc_g_kg", "DOUBLE"), ("nitrogen_g_kg", "DOUBLE"),
                 ("bulk_density_g_cm3", "DOUBLE"), ("cec_cmol_kg", "DOUBLE"),
                 ("source", "STRING"), ("is_mock", "BOOLEAN")),
        checks=(Check("pH in range", "ph BETWEEN 3 AND 10"),
                Check("fractions in range", "sand_pct BETWEEN 0 AND 100 AND clay_pct BETWEEN 0 AND 100 "
                                            "AND silt_pct BETWEEN 0 AND 100"),
                Check("sand + clay + silt about 100 %", "abs(sand_pct + clay_pct + silt_pct - 100) <= 3",
                      "warn"),
                Check("bulk density plausible", "bulk_density_g_cm3 BETWEEN 0.5 AND 2.2", "warn")),
        references=(("farm_id", "dim_farm", "farm_id"),),
    ),
    SilverTable(
        name="fact_weather_daily", source="nasa_power", keys=("farm_id", "date", "source"),
        parse=parse_fact_weather_daily, partitioned_by="months(date)",
        columns=(("farm_id", "STRING"), ("date", "DATE"), ("source", "STRING"),
                 ("tmin_c", "DOUBLE"), ("tmax_c", "DOUBLE"), ("tmean_c", "DOUBLE"),
                 ("rh_pct", "DOUBLE"), ("wind_2m_m_s", "DOUBLE"), ("rs_mj_m2", "DOUBLE"),
                 ("precip_mm", "DOUBLE"), ("is_mock", "BOOLEAN")),
        checks=(Check("tmin <= tmax", "tmin_c <= tmax_c"),
                Check("precipitation >= 0", "precip_mm >= 0"),
                Check("humidity 0-100 %", "rh_pct BETWEEN 0 AND 100"),
                Check("radiation >= 0", "rs_mj_m2 >= 0"),
                Check("wind >= 0", "wind_2m_m_s >= 0"),
                Check("temperature plausible", "tmin_c > -10 AND tmax_c < 50", "warn")),
    ),
    SilverTable(
        name="fact_field_operation", source="field_operation", keys=("operation_id",),
        parse=parse_fact_field_operation,
        columns=(("operation_id", "STRING"), ("season_id", "STRING"), ("plot_id", "STRING"),
                 ("op_type", "STRING"), ("das", "INT"), ("planned_date", "DATE"),
                 ("actual_date", "DATE"), ("status", "STRING"), ("details", "STRING")),
        checks=(Check("known status", "status IN ('planned', 'done')"),
                Check("done has a date", "status <> 'done' OR actual_date IS NOT NULL"),
                Check("known operation", "op_type IN ('sowing', 'fertilizer', 'weed_control', "
                                         "'irrigation', 'spraying', 'harvest')")),
        references=(("season_id", "dim_season", "season_id"),),
    ),
)}


def tables_for(source):
    """Silver tables built from one Bronze source, in dependency order."""
    return [t for t in TABLES.values() if t.source == source]


SOURCES = tuple(dict.fromkeys(t.source for t in TABLES.values()))
