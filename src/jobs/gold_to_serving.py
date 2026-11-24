"""Copy Gold and Silver into the Postgres serving tables the web app reads.

Every batch table of lakehouse.serving is rebuilt and replaced (JDBC truncate +
insert, so the table definitions and grants stay). sensor_recent is not touched:
the sensor stream keeps it up to date.

  spark-submit gold_to_serving.py            # needs SERVING_JDBC_URL, SERVING_DB_USER, SERVING_DB_PASSWORD
  spark-submit gold_to_serving.py --as-of 2026-01-10   # demo: "today" is a day inside the season

--as-of (or env SERVING_AS_OF) sets the day farm_status and season_info describe,
so a past season can be shown as if it were still in the field.
"""
import argparse
import json
import os
import sys

from pyspark.sql import Window
from pyspark.sql import functions as F

from lakehouse import gold, sensor, serving
from lakehouse.spark import get_spark, table_exists
from lakehouse.tables import table


def t(layer, name):
    return table(layer, name)


def today_stage(season_df, stages, today):
    """Stage of each season on the day `today` (planned / growing stage / harvested)."""
    das = F.datediff(today, F.col("planting_date"))
    stage = F.lit(None).cast("string")
    for s in sorted(stages, key=lambda r: r["stage_order"], reverse=True):
        stage = F.when((das >= s["das_start"]) & (das < s["das_end"]), F.lit(s["stage"])).otherwise(stage)
    return (season_df.withColumn("today_das", das.cast("int"))
            .withColumn("today_stage", F.when(das < 0, "planned")
                        .when(das >= F.col("season_length_days"), "harvested").otherwise(stage)))


def build_season_info(spark, today):
    stages = [r.asDict() for r in spark.table(t("silver", "dim_growth_stage")).collect()]
    stages_json = json.dumps([{k: s[k] for k in ("stage", "stage_order", "das_start", "das_end", "kc_start", "kc_end")}
                              for s in sorted(stages, key=lambda r: r["stage_order"])])
    seasons = (spark.table(t("silver", "dim_season")).alias("s")
               .join(spark.table(t("silver", "dim_plot")).select("plot_id", "farm_id"), "plot_id"))
    return today_stage(seasons, stages, today).withColumn("stages", F.lit(stages_json))


def build_farm_status(spark, feat, alerts, today):
    plots = (spark.table(t("silver", "dim_plot")).alias("p")
             .join(spark.table(t("silver", "dim_farm")).alias("f"), "farm_id")
             .select("p.plot_id", "farm_id", "f.province", "f.latitude", "f.longitude", "p.geometry",
                     "p.soil_texture", "p.drainage"))
    latest = Window.partitionBy("plot_id").orderBy(F.col("planting_date").desc())
    season = (spark.table(t("silver", "dim_season")).withColumn("_rn", F.row_number().over(latest))
              .where("_rn = 1").select("plot_id", "season_id"))
    # the last day with weather, but not after today
    as_of = (feat.where(F.col("weather_complete") & (F.col("date") <= today)).groupBy("season_id")
             .agg(F.max("date").alias("as_of_date")).alias("o"))
    day = (feat.alias("f").join(as_of, (F.col("f.season_id") == F.col("o.season_id"))
                                & (F.col("f.date") == F.col("o.as_of_date")))
           .select("f.season_id", "o.as_of_date", "f.das", "f.stage", "f.depletion_pct_taw", "f.ks",
                   "f.vwc_mean_pct", "f.water_stress_flag", "f.waterlog_flag"))
    active = alerts.where("rule <> 'operation_due'").alias("a")
    open_alerts = (day.alias("d").join(active, (F.col("a.season_id") == F.col("d.season_id"))
                                       & F.col("d.as_of_date").between(F.col("a.start_date"), F.col("a.end_date")))
                   .groupBy("d.season_id").agg(F.count("*").cast("int").alias("open_alerts"),
                                               F.max(F.when(F.col("a.severity") == "critical", 1).otherwise(0))
                                               .alias("_critical_open")))
    critical = (alerts.where("severity = 'critical'").groupBy("season_id")
                .agg(F.count("*").cast("int").alias("season_critical_alerts")))
    status = (F.when(F.col("_critical_open") == 1, "alert").when(F.col("waterlog_flag"), "waterlogged")
              .when(F.col("water_stress_flag"), "water_stress").when(F.col("as_of_date").isNotNull(), "normal"))
    out = (plots.join(season, "plot_id", "left").join(day, "season_id", "left")
           .join(open_alerts, "season_id", "left").join(critical, "season_id", "left")
           .withColumn("status_color", status)
           .withColumn("open_alerts", F.coalesce("open_alerts", F.lit(0)))
           .withColumn("season_critical_alerts", F.coalesce("season_critical_alerts", F.lit(0))))
    readings = t("silver", "fact_sensor_reading")
    if table_exists(spark, readings):
        last = (spark.table(readings).where(F.to_date(F.from_utc_timestamp("ts", gold.TIMEZONE)) <= today)
                .groupBy("plot_id").agg(F.max("ts").alias("last_reading_ts")))
        out = out.join(last, "plot_id", "left")
    return out


def build_weather_daily(spark):
    nasa = (spark.table(t("silver", "fact_weather_daily"))
            .select("farm_id", "date", "source", "tmin_c", "tmax_c", "rh_pct", "precip_mm"))
    readings = t("silver", "fact_sensor_reading")
    if not table_exists(spark, readings):
        return nasa
    start = F.timestamp_seconds(F.unix_timestamp("ts") - F.coalesce("sample_interval_s", F.lit(0)))
    per_device = (spark.table(readings)
                  .withColumn("date", F.to_date(F.from_utc_timestamp(start, gold.TIMEZONE)))
                  .groupBy("farm_id", "device_id", "date")
                  .agg(F.min("air_temp_c").alias("tmin_c"), F.max("air_temp_c").alias("tmax_c"),
                       F.avg("rh_pct").alias("rh_pct"), F.sum("rain_mm").alias("precip_mm")))
    sensors = (per_device.groupBy("farm_id", "date")
               .agg(F.min("tmin_c").alias("tmin_c"), F.max("tmax_c").alias("tmax_c"),
                    F.avg("rh_pct").alias("rh_pct"), F.avg("precip_mm").alias("precip_mm"))
               .withColumn("source", F.lit("sensor")))
    return nasa.unionByName(sensors)


def build_data_quality(spark):
    quarantine = t("silver", "quarantine_sensor")
    if not table_exists(spark, quarantine):
        return None
    day = F.to_date(F.from_utc_timestamp(F.coalesce("ts", "quarantined_at"), gold.TIMEZONE))
    return (spark.table(quarantine).groupBy(day.alias("date"), "dq_rule")
            .agg(F.count("*").cast("int").alias("rejected"),
                 F.sum(F.col("dq_rule").isin(sensor.dlq_rules()).cast("int")).cast("int").alias("sent_to_dlq")))


def build_all(spark, as_of=None):
    """{serving table: DataFrame}; a table whose source does not exist yet is left out."""
    today = F.lit(as_of).cast("date") if as_of else F.current_date()
    feat = spark.table(t("gold", "feat_season_daily")).cache()
    alerts = spark.table(t("gold", "alert_event"))
    frames = {
        "farm_status": build_farm_status(spark, feat, alerts, today),
        "region_daily": spark.table(t("gold", "agg_region_daily")),
        "season_info": build_season_info(spark, today),
        "season_daily": feat,
        "weather_daily": build_weather_daily(spark),
        "field_operations": spark.table(t("silver", "fact_field_operation")),
        "soil_layers": spark.table(t("silver", "dim_soil_layer")),
        "alerts": alerts,
        "data_quality_daily": build_data_quality(spark),
        "season_summary": spark.table(t("gold", "agg_season_summary")),
    }
    out = {}
    for name, df in frames.items():
        if df is None:
            continue
        target = serving.TABLES[name]
        df = df.withColumn("updated_at", F.current_timestamp())
        out[name] = df.select(*[F.col(c) for c in target.column_names])
    return out


def write(df, target, jdbc):
    (df.write.format("jdbc").mode("overwrite").option("truncate", "true")
     .option("url", jdbc["url"]).option("dbtable", target.qualified).option("user", jdbc["user"])
     .option("password", jdbc["password"]).option("driver", jdbc["driver"]).save())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gold/Silver -> Postgres serving tables")
    ap.add_argument("--as-of", default=os.getenv("SERVING_AS_OF") or None,
                    help="YYYY-MM-DD: the day farm_status and season_info describe (default: today)")
    args = ap.parse_args(argv)
    jdbc = serving.jdbc_settings()
    if jdbc is None:
        raise SystemExit("SERVING_JDBC_URL is not set: nowhere to write the serving tables")
    spark = get_spark("gold-to-serving")
    try:
        missing = [n for n in ("feat_season_daily", "alert_event") if not table_exists(spark, t("gold", n))]
        if missing:
            raise SystemExit(f"Gold tables missing: {', '.join(missing)} (run gold_build first)")
        for name, df in build_all(spark, args.as_of).items():
            df = df.cache()
            write(df, serving.TABLES[name], jdbc)
            print(f"[serving.{name}] {df.count()} row(s)")
            df.unpersist()
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
