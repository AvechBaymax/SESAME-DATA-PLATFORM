"""Smoke test: Iceberg table through the JDBC catalog, stored on RustFS over S3A.

Run inside the cluster:
  docker exec spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 /opt/spark-jobs/smoke_iceberg.py
"""
from pyspark.sql import SparkSession

TABLE = "lakehouse.smoke.sensor_check"

spark = SparkSession.builder.appName("smoke-iceberg").getOrCreate()

spark.sql("CREATE NAMESPACE IF NOT EXISTS lakehouse.smoke")
spark.sql(f"DROP TABLE IF EXISTS {TABLE} PURGE")
# Hidden partitioning: queries filter on ts, Iceberg prunes by day on its own.
spark.sql(f"""
    CREATE TABLE {TABLE} (device_id STRING, ts TIMESTAMP, vwc_pct DOUBLE)
    USING iceberg
    PARTITIONED BY (days(ts))
""")
spark.sql(f"""
    INSERT INTO {TABLE} VALUES
      ('SN_BINHTHUAN_01_P01_01', TIMESTAMP '2025-11-15 01:00:00', 12.4),
      ('SN_BINHTHUAN_01_P01_01', TIMESTAMP '2025-11-15 01:15:00', 12.3),
      ('SN_BINHTHUAN_01_P01_01', TIMESTAMP '2025-11-16 01:00:00', 11.9)
""")
# Second commit, to check snapshots and MERGE (used later for the dim_* tables)
spark.sql(f"""
    MERGE INTO {TABLE} t
    USING (SELECT 'SN_BINHTHUAN_01_P01_01' AS device_id, TIMESTAMP '2025-11-16 01:00:00' AS ts,
                  CAST(12.0 AS DOUBLE) AS vwc_pct) s
    ON t.device_id = s.device_id AND t.ts = s.ts
    WHEN MATCHED THEN UPDATE SET vwc_pct = s.vwc_pct
""")

rows = spark.table(TABLE).count()
day = spark.table(TABLE).where("ts >= '2025-11-16' AND ts < '2025-11-17'").collect()
partitions = spark.sql(f"SELECT * FROM {TABLE}.partitions").count()
snapshots = spark.sql(f"SELECT * FROM {TABLE}.snapshots").count()
location = spark.sql(f"DESCRIBE TABLE EXTENDED {TABLE}").where("col_name = 'Location'").first()[1]

assert rows == 3, f"expected 3 rows, got {rows}"
assert len(day) == 1 and day[0]["vwc_pct"] == 12.0, f"MERGE did not update: {day}"
assert partitions == 2, f"expected 2 day partitions, got {partitions}"
assert snapshots == 2, f"expected 2 snapshots, got {snapshots}"
print(f"Iceberg OK: {rows} rows, {partitions} day partitions, {snapshots} snapshots at {location}")

spark.stop()
