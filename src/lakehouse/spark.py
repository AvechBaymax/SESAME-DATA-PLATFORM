"""SparkSession and Iceberg helpers. Catalog and S3 settings come from spark-defaults.conf."""
from pyspark.sql import SparkSession

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
