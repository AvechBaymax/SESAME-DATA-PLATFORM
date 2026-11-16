"""Build the Silver Iceberg tables from Bronze.

For every Silver table fed by a Bronze source it parses each Bronze payload
(lakehouse.silver), keeps the row from the newest file per primary key, runs
the data-quality checks (lakehouse.quality) and MERGEs into
lakehouse.silver.<table>. A failed error-level check stops the job before the
table is written. Re-running with no new Bronze files changes nothing.

  spark-submit bronze_to_silver.py --source user_config   # dim_farm, dim_plot, dim_season, dim_device
  spark-submit bronze_to_silver.py --source nasa_power    # fact_weather_daily
  spark-submit bronze_to_silver.py --source all
"""
import argparse
import json
import sys

from pyspark.sql import functions as F

from lakehouse import quality
from lakehouse.silver import SOURCES as SILVER_SOURCES
from lakehouse.silver import tables_for, to_tuple
from lakehouse.sources import SOURCES as BRONZE_SOURCES
from lakehouse.spark import (create_table_if_missing, ensure_namespace, get_spark, latest_per_key,
                             merge_into, table_exists)
from lakehouse.tables import table


def silver_table(name):
    return table("silver", name)


def parse_bronze(spark, silver, bronze_df):
    """Run the table's parser over every Bronze row -> DataFrame with lineage columns."""
    partition_columns = [c for c in bronze_df.columns
                         if c not in ("payload", "_source", "_ingested_at", "_file_path")]

    def explode(row):
        partitions = {c: row[c] for c in partition_columns}
        for parsed in silver.parse(json.loads(row["payload"]), partitions):
            yield to_tuple(silver, parsed) + (row["_file_path"], row["_ingested_at"])

    schema = silver.ddl().replace("_updated_at TIMESTAMP", "_ingested_at TIMESTAMP")
    return spark.createDataFrame(bronze_df.rdd.flatMap(explode), schema=schema)


def build(spark, silver, bronze_df):
    """Parsed, de-duplicated rows ready to MERGE (newest landing file wins per key)."""
    rows = latest_per_key(parse_bronze(spark, silver, bronze_df), silver.keys)
    return rows.drop("_ingested_at").withColumn("_updated_at", F.current_timestamp())


def load(spark, silver):
    bronze_name = table("bronze", BRONZE_SOURCES[silver.source].table)
    target = silver_table(silver.name)
    if not table_exists(spark, bronze_name):
        print(f"[{silver.name}] {bronze_name} does not exist yet, skipped")
        return 0
    df = build(spark, silver, spark.table(bronze_name)).cache()
    quality.enforce(quality.check_table(spark, silver, df, silver_table))

    create_table_if_missing(spark, target, silver.ddl(), silver.partitioned_by)
    before = spark.table(target).count()
    merge_into(spark, df, target, silver.keys)
    after = spark.table(target).count()
    print(f"[{silver.name}] {df.count()} row(s) from {bronze_name}; {target} has {after} "
          f"row(s) ({after - before:+d})")
    df.unpersist()
    return after


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bronze -> Silver Iceberg tables")
    ap.add_argument("--source", required=True, choices=list(SILVER_SOURCES) + ["all"],
                    help="Bronze source whose Silver tables to build")
    args = ap.parse_args(argv)

    spark = get_spark(f"bronze-to-silver-{args.source}")
    ensure_namespace(spark, "silver")
    sources = SILVER_SOURCES if args.source == "all" else [args.source]
    try:
        for source in sources:
            for silver in tables_for(source):
                load(spark, silver)
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
