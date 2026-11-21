"""Build the Gold tables from Silver: daily water balance, season summary, regional view.

  1. sensor aggregates per plot and local day, from silver.fact_sensor_reading
  2. one input row per season day: NASA weather (silver.fact_weather_daily) + (1)
  3. gold.feat_season_daily: lakehouse.gold.water_balance() run per season, in date order
  4. gold.agg_season_summary: season totals from (3)
  5. gold.agg_region_daily: farms per province and day from (3)

Each table is recomputed from Silver and replaces the previous version in one
Iceberg commit (older versions stay reachable through snapshots). Checks run
before each write; a failed error-level check stops the job.

  spark-submit silver_to_gold.py
"""
import sys

from pyspark.sql import functions as F

from lakehouse import gold, quality
from lakehouse.spark import create_or_evolve_table, ensure_namespace, get_spark, table_exists
from lakehouse.tables import table

SILVER = {name: table("silver", name) for name in
          ("dim_season", "dim_plot", "dim_farm", "dim_device", "dim_growth_stage", "dim_crop",
           "fact_weather_daily", "fact_sensor_reading")}


def silver_table(name):
    return SILVER.get(name, table("silver", name))


def sensor_daily(readings, devices):
    """Per plot and local day: rain, irrigation, soil moisture and O2, leaf wetness, coverage.

    Totals (rain, irrigation, wet hours) are summed per device, then averaged over
    the plot's devices; coverage compares readings with what the plot's devices
    should have sent at their sample interval.
    """
    # A reading stamped ts covers the interval before it: a 00:00 reading belongs to the day before.
    interval_start = F.timestamp_seconds(F.unix_timestamp("ts") - F.coalesce("sample_interval_s", F.lit(0)))
    readings = readings.withColumn("date", F.to_date(F.from_utc_timestamp(interval_start, gold.TIMEZONE)))
    per_device = readings.groupBy("plot_id", "device_id", "date").agg(
        F.count("*").alias("n"),
        F.avg("sample_interval_s").alias("interval_s"),
        F.sum("rain_mm").alias("rain_mm"),
        F.sum(F.coalesce("irrigation_mm", F.lit(0.0))).alias("irrigation_mm"),
        (F.sum(F.when(F.col("irrigation_active"), F.col("sample_interval_s")).otherwise(0)) / 3600)
        .alias("irrigation_hours"),
        (F.sum("leaf_wetness_min") / 60).alias("leaf_wet_hours"),
        F.avg("vwc_pct").alias("vwc_mean"), F.min("vwc_pct").alias("vwc_min"),
        F.max("vwc_pct").alias("vwc_max"), F.min("soil_oxygen_pct").alias("o2_min"))
    devices = devices.groupBy("plot_id").agg(F.count("*").alias("devices"))
    return (per_device.groupBy("plot_id", "date").agg(
                F.avg("rain_mm").alias("sensor_rain_mm"),
                F.avg("irrigation_mm").alias("irrigation_mm"),
                F.avg("irrigation_hours").alias("irrigation_hours"),
                F.avg("leaf_wet_hours").alias("leaf_wet_hours"),
                (F.sum(F.col("vwc_mean") * F.col("n")) / F.sum("n")).alias("vwc_mean_pct"),
                F.min("vwc_min").alias("vwc_min_pct"), F.max("vwc_max").alias("vwc_max_pct"),
                F.min("o2_min").alias("soil_oxygen_min_pct"),
                F.sum("n").alias("readings"), F.avg("interval_s").alias("interval_s"))
            .join(devices, "plot_id", "left")
            .withColumn("sensor_coverage", F.least(
                F.lit(1.0), F.col("readings") / (F.coalesce("devices", F.lit(1)) * 86400 / F.col("interval_s"))))
            .drop("readings", "interval_s", "devices"))


def seasons(spark):
    return (spark.table(SILVER["dim_season"]).alias("s")
            .join(spark.table(SILVER["dim_plot"]).alias("p"), "plot_id")
            .join(spark.table(SILVER["dim_farm"]).alias("f"), F.col("p.farm_id") == F.col("f.farm_id"))
            .select("s.season_id", "s.plot_id", "p.farm_id", "s.crop_id", "s.planting_date",
                    F.coalesce("s.actual_harvest_date", "s.expected_harvest_date").alias("harvest_date"),
                    "f.latitude", "p.soil_texture", "f.province"))


def season_days(season_df, weather, sensors=None):
    """One row per season and day, planting to harvest, with weather and sensor inputs."""
    weather = (weather.where("source = 'nasa_power'")
               .select("farm_id", "date", "tmin_c", "tmax_c", "rh_pct", "wind_2m_m_s", "rs_mj_m2",
                       F.col("precip_mm").alias("nasa_precip_mm")))
    days = (season_df.select("season_id", "plot_id", "farm_id",
                             F.explode(F.sequence("planting_date", "harvest_date")).alias("date"))
            .join(weather, ["farm_id", "date"], "left"))
    if sensors is not None:
        days = days.join(sensors, ["plot_id", "date"], "left")
    return days


def build_feat(spark, season_df, days_df, stages_by_crop, crop_by_id):
    """Run the water balance per season on the executors -> feat_season_daily DataFrame."""
    info = {r["season_id"]: r.asDict() for r in season_df.collect()}
    columns = [c for c in gold.FEAT_SEASON_DAILY.column_names if c != "_updated_at"]

    def run(item):
        season_id, rows = item
        season = info[season_id]
        crop = season["crop_id"]
        for row in gold.water_balance(season, [r.asDict() for r in rows],
                                      stages_by_crop[crop], crop_by_id[crop]):
            yield tuple(row[c] for c in columns)

    pairs = days_df.rdd.map(lambda r: (r["season_id"], r)).groupByKey()
    ddl = ", ".join(f"{n} {k}" for n, k in gold.FEAT_SEASON_DAILY.columns if n != "_updated_at")
    return (spark.createDataFrame(pairs.flatMap(run), schema=ddl)
            .withColumn("_updated_at", F.current_timestamp()))


def build_summary(feat, season_df, weather):
    weather = weather.where("source = 'nasa_power'")
    window_rain = (season_df.alias("s").join(
        weather.alias("w"),
        (F.col("w.farm_id") == F.col("s.farm_id"))
        & F.col("w.date").between(F.col("s.harvest_date"),
                                  F.date_add("s.harvest_date", gold.HARVEST_WINDOW_DAYS)), "left")
        .groupBy("s.season_id").agg(F.sum("w.precip_mm").alias("rain_mm_harvest_window")))
    totals = feat.groupBy("season_id", "farm_id", "plot_id").agg(
        F.count("*").cast("int").alias("days"),
        F.sum(F.col("weather_complete").cast("int")).cast("int").alias("days_weather_complete"),
        F.sum("precip_mm").alias("precip_total_mm"),
        F.sum("effective_precip_mm").alias("effective_precip_total_mm"),
        F.sum("irrigation_mm").alias("irrigation_total_mm"),
        F.sum((F.col("irrigation_mm") > 0).cast("int")).cast("int").alias("irrigation_events"),
        F.sum("etc_mm").alias("etc_total_mm"), F.sum("eta_mm").alias("eta_total_mm"),
        F.sum("deep_percolation_mm").alias("deep_percolation_total_mm"),
        F.sum(F.col("water_stress_flag").cast("int")).cast("int").alias("water_stress_days"),
        # sesame flowers through the FAO mid-season stage
        F.sum((F.col("water_stress_flag") & (F.col("stage") == "mid_season")).cast("int"))
        .cast("int").alias("stress_days_flowering"),
        F.sum(F.col("waterlog_flag").cast("int")).cast("int").alias("waterlog_days"),
        F.sum(F.col("heat_stress_flag").cast("int")).cast("int").alias("heat_stress_days"),
        F.max("gdd_cum").alias("gdd_total"),
        F.avg("vwc_mean_pct").alias("vwc_mean_pct"),
        F.avg("sensor_coverage_pct").alias("sensor_coverage_pct"))
    return (totals.join(season_df.select("season_id", "planting_date", "harvest_date"), "season_id")
            .join(window_rain, "season_id", "left")
            .withColumn("eta_etc_ratio", F.col("eta_total_mm") / F.col("etc_total_mm"))
            .withColumn("status", F.when(F.col("days_weather_complete") == F.col("days"), "completed")
                                   .otherwise("in_progress"))
            .withColumn("_updated_at", F.current_timestamp())
            .select(*gold.AGG_SEASON_SUMMARY.column_names))


def build_region(feat, season_df):
    joined = feat.join(season_df.select("season_id", "province"), "season_id")

    def farms_where(flag):
        return F.countDistinct(F.when(F.col(flag), F.col("farm_id"))).cast("int")
    return (joined.groupBy("province", "date").agg(
                F.countDistinct("farm_id").cast("int").alias("farms_active"),
                F.countDistinct("season_id").cast("int").alias("seasons_active"),
                farms_where("water_stress_flag").alias("farms_water_stress"),
                farms_where("waterlog_flag").alias("farms_waterlogged"),
                farms_where("heat_stress_flag").alias("farms_heat_stress"),
                F.avg("depletion_pct_taw").alias("avg_depletion_pct_taw"),
                F.max("depletion_pct_taw").alias("max_depletion_pct_taw"),
                F.avg("et0_mm").alias("avg_et0_mm"), F.avg("precip_mm").alias("avg_precip_mm"),
                F.max("precip_mm").alias("max_precip_mm"), F.avg("vwc_mean_pct").alias("avg_vwc_mean_pct"))
            .withColumn("_updated_at", F.current_timestamp())
            .select(*gold.AGG_REGION_DAILY.column_names))


def write(spark, gold_table, df):
    """Check, then replace the table's content in one commit."""
    df = df.cache()
    quality.enforce(quality.check_table(spark, gold_table, df, silver_table))
    target = table("gold", gold_table.name)
    create_or_evolve_table(spark, target, gold_table.ddl())
    df.writeTo(target).overwrite(F.lit(True))
    print(f"[{gold_table.name}] {df.count()} row(s) written to {target}")
    df.unpersist()


def main(argv=None):
    spark = get_spark("silver-to-gold")
    try:
        missing = [n for n in ("dim_season", "dim_growth_stage", "dim_crop", "fact_weather_daily")
                   if not table_exists(spark, SILVER[n])]
        if missing:
            raise SystemExit(f"Silver tables missing: {', '.join(missing)} (run the bootstrap DAG first)")
        ensure_namespace(spark, "gold")
        season_df = seasons(spark).cache()
        stages, crops = {}, {}
        for r in spark.table(SILVER["dim_growth_stage"]).collect():
            stages.setdefault(r["crop_id"], []).append(r.asDict())
        for r in spark.table(SILVER["dim_crop"]).collect():
            crops[r["crop_id"]] = r.asDict()

        weather = spark.table(SILVER["fact_weather_daily"])
        sensors = None
        if table_exists(spark, SILVER["fact_sensor_reading"]):
            sensors = sensor_daily(spark.table(SILVER["fact_sensor_reading"]), spark.table(SILVER["dim_device"]))
        feat = build_feat(spark, season_df, season_days(season_df, weather, sensors), stages, crops).cache()
        write(spark, gold.FEAT_SEASON_DAILY, feat)
        write(spark, gold.AGG_SEASON_SUMMARY, build_summary(feat, season_df, weather))
        write(spark, gold.AGG_REGION_DAILY, build_region(feat, season_df))
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
