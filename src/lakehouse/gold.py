"""Gold: daily crop water balance per season, season summaries and the regional view.

Plain Python (no pyspark import). jobs/silver_to_gold.py gathers, for every
season, one input row per day (NASA weather + the plot's sensor aggregates)
and runs water_balance() over them in date order: a season's depletion on a
day depends on the day before, which SQL cannot express directly.

Method (FAO-56, chapter 8, single crop coefficient):
  ET0  Penman-Monteith from the daily weather (common.et0)
  Kc   interpolated by days after sowing from silver.dim_growth_stage
  Zr   root depth grows from ROOT_DEPTH_INITIAL_M at sowing to the crop's
       maximum (silver.dim_crop) at the start of mid-season
  TAW  = 1000 (theta_fc - theta_wp) Zr,  RAW = p TAW   (soil from dim_plot)
  Ks   = 1 while depletion <= RAW, then (TAW - Dr) / ((1 - p) TAW)
  ETc  = Kc ET0 (crop need),  ETa = Ks ETc (what the crop could take up)
  Dr   = Dr(yesterday) - effective rain - irrigation + ETa, kept in [0, TAW];
         water above field capacity drains (deep percolation)
"""
from dataclasses import dataclass
from datetime import timedelta

from common.et0 import et0_penman_monteith
from common.soil import TEXTURES

# Assumptions (no number in the grower's guide); kept here so they are easy to revisit.
ROOT_DEPTH_INITIAL_M = 0.20      # FAO-56: Zr at sowing 0.15-0.20 m
ELEVATION_M = 50.0               # Binh Thuan lowland plots; only affects ET0 slightly
EFFECTIVE_RAIN_FRACTION_ET0 = 0.2  # FAO-56: rain below ~0.2 ET0 evaporates from the surface
GDD_BASE_C = 10.0                # base temperature commonly used for sesame
HEAT_STRESS_TMAX_C = 35.0        # guide: optimum 25-30 C; above 35 C counts as heat stress
WATERLOG_O2_PCT = 10.0           # soil O2 below this = waterlogged root zone
SENSOR_MIN_COVERAGE = 0.8        # use the plot's rain gauge when >= 80 % of readings arrived
HARVEST_WINDOW_DAYS = 8          # guide: 3-5 days standing in bundles + 2-3 days sun drying
TIMEZONE = "Asia/Ho_Chi_Minh"    # daily tables use the local date


@dataclass(frozen=True)
class Check:
    name: str
    condition: str
    severity: str = "error"


@dataclass(frozen=True)
class GoldTable:
    name: str
    columns: tuple
    keys: tuple
    checks: tuple = ()
    references: tuple = ()   # ((column, silver table, column), ...)

    @property
    def column_names(self):
        return [name for name, _ in self.columns]

    def ddl(self):
        return ", ".join(f"{name} {kind}" for name, kind in self.columns)


# ---- crop curve -------------------------------------------------------------------------------

def stage_kc(stages, das):
    """(stage, Kc) for a day after sowing from dim_growth_stage rows.

    Each stage runs das_start <= DAS < das_end with Kc linear from kc_start to
    kc_end (flat when they are equal). Days past the last stage keep its end.
    """
    ordered = sorted(stages, key=lambda s: s["stage_order"])
    for stage in ordered:
        if stage["das_start"] <= das < stage["das_end"]:
            span = stage["das_end"] - stage["das_start"]
            fraction = (das - stage["das_start"]) / span
            return stage["stage"], stage["kc_start"] + fraction * (stage["kc_end"] - stage["kc_start"])
    if das >= ordered[-1]["das_end"]:
        return ordered[-1]["stage"], ordered[-1]["kc_end"]
    return ordered[0]["stage"], ordered[0]["kc_start"]


def root_depth_m(stages, das, max_depth_m):
    """Root depth growing linearly until the start of mid-season (FAO-56 eq. 8.69 simplified)."""
    mid_start = next(s["das_start"] for s in stages if s["stage"] == "mid_season")
    fraction = min(max(das / mid_start, 0.0), 1.0) if mid_start else 1.0
    return ROOT_DEPTH_INITIAL_M + fraction * (max_depth_m - ROOT_DEPTH_INITIAL_M)


# ---- water balance ---------------------------------------------------------------------------

def _rain(day):
    """Rain used in the balance: the plot gauge when enough readings arrived, else NASA."""
    coverage = day.get("sensor_coverage") or 0.0
    if day.get("sensor_rain_mm") is not None and coverage >= SENSOR_MIN_COVERAGE:
        return day["sensor_rain_mm"], "sensor"
    if day.get("nasa_precip_mm") is not None:
        return day["nasa_precip_mm"], "nasa_power"
    return None, None


def _et0(day, latitude):
    needed = ("tmin_c", "tmax_c", "rh_pct", "wind_2m_m_s", "rs_mj_m2")
    if any(day.get(k) is None for k in needed):
        return None
    return et0_penman_monteith(day["tmin_c"], day["tmax_c"], day["rh_pct"], day["wind_2m_m_s"],
                               day["rs_mj_m2"], latitude, day["date"].timetuple().tm_yday, ELEVATION_M)


def water_balance(season, days, stages, crop):
    """Daily rows of feat_season_daily for one season.

    season: season_id, farm_id, plot_id, planting_date, harvest_date, latitude, soil_texture
    days:   one dict per date (weather and sensor aggregates, any of them may be None)
    stages: dim_growth_stage rows of the crop; crop: its dim_crop row
    The root zone starts at field capacity (sown after rain or a pre-sowing irrigation).
    """
    fc, wp = TEXTURES[season["soil_texture"]][:2]
    p = crop["depletion_fraction_p"]
    max_root = crop["max_root_depth_low_m"]   # low end of FAO's range: sandy soil, 75-day season
    by_date = {d["date"]: d for d in days}
    depletion, gdd_cum, rows = 0.0, 0.0, []
    day_date = season["planting_date"]
    while day_date <= season["harvest_date"]:
        d = by_date.get(day_date, {"date": day_date})
        das = (day_date - season["planting_date"]).days
        stage, kc = stage_kc(stages, das)
        zr = root_depth_m(stages, das, max_root)
        taw = 1000 * (fc - wp) * zr
        raw = p * taw
        depletion = min(depletion, taw)

        et0 = _et0(d, season["latitude"])
        rain, rain_source = _rain(d)
        effective = 0.0
        if rain is not None and (et0 is None or rain >= EFFECTIVE_RAIN_FRACTION_ET0 * et0):
            effective = rain
        irrigation = d.get("irrigation_mm") or 0.0
        ks = 1.0 if depletion <= raw else max(0.0, (taw - depletion) / ((1 - p) * taw))
        etc = None if et0 is None else kc * et0
        eta = None if etc is None else ks * etc

        new_depletion = depletion - effective - irrigation + (eta or 0.0)
        percolation = max(-new_depletion, 0.0)
        depletion = min(max(new_depletion, 0.0), taw)

        tmin, tmax = d.get("tmin_c"), d.get("tmax_c")
        gdd = None if tmin is None or tmax is None else max((tmin + tmax) / 2 - GDD_BASE_C, 0.0)
        gdd_cum += gdd or 0.0
        o2 = d.get("soil_oxygen_min_pct")
        rows.append({
            "season_id": season["season_id"], "farm_id": season["farm_id"], "plot_id": season["plot_id"],
            "date": day_date, "das": das, "stage": stage, "kc": kc, "root_depth_m": zr,
            "et0_mm": et0, "etc_mm": etc, "ks": ks, "eta_mm": eta,
            "precip_mm": rain, "rain_source": rain_source,
            "nasa_precip_mm": d.get("nasa_precip_mm"), "sensor_rain_mm": d.get("sensor_rain_mm"),
            "effective_precip_mm": effective, "irrigation_mm": irrigation,
            "irrigation_hours": d.get("irrigation_hours") or 0.0,
            "taw_mm": taw, "raw_mm": raw, "depletion_mm": depletion,
            "depletion_pct_taw": 100 * depletion / taw, "deep_percolation_mm": percolation,
            "tmin_c": tmin, "tmax_c": tmax, "gdd": gdd, "gdd_cum": gdd_cum,
            "vwc_mean_pct": d.get("vwc_mean_pct"), "vwc_min_pct": d.get("vwc_min_pct"),
            "vwc_max_pct": d.get("vwc_max_pct"), "soil_oxygen_min_pct": o2,
            "leaf_wet_hours": d.get("leaf_wet_hours"), "sensor_coverage_pct":
                None if d.get("sensor_coverage") is None else 100 * d["sensor_coverage"],
            "water_stress_flag": ks < 1.0,
            "heat_stress_flag": tmax is not None and tmax >= HEAT_STRESS_TMAX_C,
            "waterlog_flag": o2 is not None and o2 < WATERLOG_O2_PCT,
            "weather_complete": et0 is not None,
        })
        day_date += timedelta(days=1)
    return rows


# ---- tables ----------------------------------------------------------------------------------

FEAT_SEASON_DAILY = GoldTable(
    name="feat_season_daily", keys=("season_id", "date"),
    columns=(
        ("season_id", "STRING"), ("farm_id", "STRING"), ("plot_id", "STRING"), ("date", "DATE"),
        ("das", "INT"), ("stage", "STRING"), ("kc", "DOUBLE"), ("root_depth_m", "DOUBLE"),
        ("et0_mm", "DOUBLE"), ("etc_mm", "DOUBLE"), ("ks", "DOUBLE"), ("eta_mm", "DOUBLE"),
        ("precip_mm", "DOUBLE"), ("rain_source", "STRING"), ("nasa_precip_mm", "DOUBLE"),
        ("sensor_rain_mm", "DOUBLE"), ("effective_precip_mm", "DOUBLE"), ("irrigation_mm", "DOUBLE"),
        ("irrigation_hours", "DOUBLE"), ("taw_mm", "DOUBLE"), ("raw_mm", "DOUBLE"),
        ("depletion_mm", "DOUBLE"), ("depletion_pct_taw", "DOUBLE"), ("deep_percolation_mm", "DOUBLE"),
        ("tmin_c", "DOUBLE"), ("tmax_c", "DOUBLE"), ("gdd", "DOUBLE"), ("gdd_cum", "DOUBLE"),
        ("vwc_mean_pct", "DOUBLE"), ("vwc_min_pct", "DOUBLE"), ("vwc_max_pct", "DOUBLE"),
        ("soil_oxygen_min_pct", "DOUBLE"), ("leaf_wet_hours", "DOUBLE"),
        ("sensor_coverage_pct", "DOUBLE"), ("water_stress_flag", "BOOLEAN"),
        ("heat_stress_flag", "BOOLEAN"), ("waterlog_flag", "BOOLEAN"), ("weather_complete", "BOOLEAN"),
        ("_updated_at", "TIMESTAMP"),
    ),
    checks=(
        Check("das >= 0", "das >= 0"),
        Check("Kc in range", "kc BETWEEN 0 AND 2"),
        Check("Ks in 0-1", "ks BETWEEN 0 AND 1"),
        Check("ET0 plausible", "et0_mm BETWEEN 0 AND 15"),
        Check("ETa <= ETc", "eta_mm <= etc_mm + 1e-9"),
        Check("depletion within TAW", "depletion_mm BETWEEN 0 AND taw_mm + 1e-9"),
        Check("weather for every day", "weather_complete", "warn"),
    ),
    references=(("season_id", "dim_season", "season_id"),),
)

AGG_SEASON_SUMMARY = GoldTable(
    name="agg_season_summary", keys=("season_id",),
    columns=(
        ("season_id", "STRING"), ("farm_id", "STRING"), ("plot_id", "STRING"),
        ("planting_date", "DATE"), ("harvest_date", "DATE"), ("status", "STRING"),
        ("days", "INT"), ("days_weather_complete", "INT"),
        ("precip_total_mm", "DOUBLE"), ("effective_precip_total_mm", "DOUBLE"),
        ("irrigation_total_mm", "DOUBLE"), ("irrigation_events", "INT"),
        ("etc_total_mm", "DOUBLE"), ("eta_total_mm", "DOUBLE"), ("eta_etc_ratio", "DOUBLE"),
        ("deep_percolation_total_mm", "DOUBLE"), ("water_stress_days", "INT"),
        ("stress_days_flowering", "INT"), ("waterlog_days", "INT"), ("heat_stress_days", "INT"),
        ("rain_mm_harvest_window", "DOUBLE"), ("gdd_total", "DOUBLE"),
        ("vwc_mean_pct", "DOUBLE"), ("sensor_coverage_pct", "DOUBLE"), ("_updated_at", "TIMESTAMP"),
    ),
    checks=(
        Check("totals not negative", "precip_total_mm >= 0 AND irrigation_total_mm >= 0 "
                                     "AND etc_total_mm >= 0"),
        Check("ETa/ETc in 0-1", "eta_etc_ratio BETWEEN 0 AND 1 + 1e-9"),
        Check("known status", "status IN ('in_progress', 'completed')"),
    ),
    references=(("season_id", "dim_season", "season_id"),),
)

AGG_REGION_DAILY = GoldTable(
    name="agg_region_daily", keys=("province", "date"),
    columns=(
        ("province", "STRING"), ("date", "DATE"), ("farms_active", "INT"), ("seasons_active", "INT"),
        ("farms_water_stress", "INT"), ("farms_waterlogged", "INT"), ("farms_heat_stress", "INT"),
        ("avg_depletion_pct_taw", "DOUBLE"), ("max_depletion_pct_taw", "DOUBLE"),
        ("avg_et0_mm", "DOUBLE"), ("avg_precip_mm", "DOUBLE"), ("max_precip_mm", "DOUBLE"),
        ("avg_vwc_mean_pct", "DOUBLE"), ("_updated_at", "TIMESTAMP"),
    ),
    checks=(Check("counts consistent", "farms_water_stress <= farms_active "
                                       "AND farms_waterlogged <= farms_active"),),
)

ALERT_EVENT = GoldTable(
    name="alert_event", keys=("alert_id",),
    columns=(
        ("alert_id", "STRING"), ("season_id", "STRING"), ("farm_id", "STRING"), ("plot_id", "STRING"),
        ("device_id", "STRING"), ("rule", "STRING"), ("severity", "STRING"), ("start_date", "DATE"),
        ("end_date", "DATE"), ("days", "INT"), ("ongoing", "BOOLEAN"), ("value", "DOUBLE"),
        ("threshold", "DOUBLE"), ("unit", "STRING"), ("message", "STRING"), ("_updated_at", "TIMESTAMP"),
    ),
    checks=(
        Check("known severity", "severity IN ('info', 'warning', 'critical')"),
        Check("end on or after start", "end_date >= start_date"),
        Check("days match the dates", "days BETWEEN 1 AND datediff(end_date, start_date) + 1"),
        Check("message present", "length(message) > 0"),
    ),
    references=(("season_id", "dim_season", "season_id"),),
)

TABLES = {t.name: t for t in (FEAT_SEASON_DAILY, AGG_SEASON_SUMMARY, AGG_REGION_DAILY, ALERT_EVENT)}

