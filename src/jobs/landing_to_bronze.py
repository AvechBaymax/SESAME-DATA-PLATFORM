"""Load new landing-zone JSON files into the Bronze Iceberg tables.

Each file becomes one row: the raw JSON text in `payload`, the Hive partitions of
its object key as columns, plus _source, _ingested_at and _file_path. Files
already in Bronze (same _file_path) are skipped, so re-runs are safe.

  spark-submit landing_to_bronze.py --source nasa_power
  spark-submit landing_to_bronze.py --source all
"""
import argparse
import sys

from pyspark.errors import AnalysisException
from pyspark.sql import functions as F

from lakehouse.sources import SOURCES, partition_pattern
from lakehouse.spark import append_or_create, ensure_namespace, get_spark, table_exists
from lakehouse.tables import landing_uri, table


def read_prefix(spark, source, prefix):
    """All JSON files under one prefix as (payload, _file_path, partition columns...)."""
    try:
        df = (spark.read.option("recursiveFileLookup", "true").option("pathGlobFilter", "*.json")
              .text(landing_uri(prefix), wholetext=True))
    except AnalysisException as exc:
        if "PATH_NOT_FOUND" in str(exc) or "Path does not exist" in str(exc):
            return None
        raise
    df = df.select(F.col("value").alias("payload"), F.col("_metadata.file_path").alias("_file_path"))
    for key in source.partitions:
        value = F.regexp_extract("_file_path", partition_pattern(key), 1)
        df = df.withColumn(key, F.when(value == "", None).otherwise(value))
    for column, by_prefix in source.columns.items():
        df = df.withColumn(column, F.lit(by_prefix[prefix]))
    return df


def load_source(spark, source):
    target = table("bronze", source.table)
    frames = [df for df in (read_prefix(spark, source, p) for p in source.prefixes) if df is not None]
    if not frames:
        print(f"[{source.name}] no files under {', '.join(source.prefixes)}")
        return 0
    df = frames[0]
    for other in frames[1:]:
        df = df.unionByName(other)

    if table_exists(spark, target):
        seen = spark.table(target).select("_file_path").distinct()
        df = df.join(seen, "_file_path", "left_anti")

    extra = list(source.partitions) + list(source.columns)
    df = (df.withColumn("_source", F.lit(source.name))
            .withColumn("_ingested_at", F.current_timestamp())
            .select(*extra, "payload", "_source", "_ingested_at", "_file_path")
            .cache())
    new_files = df.count()
    if new_files == 0:
        print(f"[{source.name}] nothing new for {target}")
        return 0
    append_or_create(df, target)
    print(f"[{source.name}] loaded {new_files} file(s) into {target}")
    return new_files


def main(argv=None):
    ap = argparse.ArgumentParser(description="Landing zone -> Bronze Iceberg tables")
    ap.add_argument("--source", required=True, choices=sorted(SOURCES) + ["all"])
    args = ap.parse_args(argv)

    spark = get_spark(f"landing-to-bronze-{args.source}")
    ensure_namespace(spark, "bronze")
    names = sorted(SOURCES) if args.source == "all" else [args.source]
    for name in names:
        load_source(spark, SOURCES[name])
    spark.stop()


if __name__ == "__main__":
    sys.exit(main())
