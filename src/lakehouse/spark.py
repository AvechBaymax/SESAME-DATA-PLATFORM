"""SparkSession and Iceberg helpers. Catalog and S3 settings come from spark-defaults.conf."""
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

from lakehouse.tables import CATALOG


def get_spark(app_name):
    spark = SparkSession.builder.appName(app_name).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark


def ensure_namespace(spark, layer):
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.{layer}")


def table_exists(spark, full_name):
    return spark.catalog.tableExists(full_name)


def append_or_create(df, full_name, partitioned_by=None):
    """Append to an Iceberg table, creating it from the DataFrame schema on first write."""
    spark = df.sparkSession
    if table_exists(spark, full_name):
        df.writeTo(full_name).append()
        return
    writer = df.writeTo(full_name).using("iceberg").tableProperty("format-version", "2")
    if partitioned_by is not None:
        writer = writer.partitionedBy(partitioned_by)
    writer.create()


def create_table_if_missing(spark, full_name, ddl, partitioned_by=""):
    """Create an Iceberg (format v2) table from a column DDL, e.g. "id STRING, ts TIMESTAMP"."""
    partition = f"PARTITIONED BY ({partitioned_by})" if partitioned_by else ""
    spark.sql(f"CREATE TABLE IF NOT EXISTS {full_name} ({ddl}) USING iceberg {partition} "
              "TBLPROPERTIES ('format-version' = '2')")


def latest_per_key(df, keys, order_by=("_ingested_at", "_file_path")):
    """Keep one row per key: the one with the greatest order_by values (newest file)."""
    window = Window.partitionBy(*keys).orderBy(*[F.col(c).desc() for c in order_by])
    return df.withColumn("_rn", F.row_number().over(window)).where("_rn = 1").drop("_rn")


def merge_into(spark, df, full_name, keys):
    """Upsert df into an Iceberg table on its keys.

    A row is rewritten only when it now comes from another landing file, so a
    re-run without new files leaves the data as it is.
    """
    view = "merge_source_" + full_name.replace(".", "_")
    df.createOrReplaceTempView(view)
    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    spark.sql(f"""
        MERGE INTO {full_name} t
        USING {view} s
        ON {on}
        WHEN MATCHED AND t._file_path <> s._file_path THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)
