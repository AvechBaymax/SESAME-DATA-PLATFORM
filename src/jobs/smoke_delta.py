"""Smoke test: write a small Delta table to MinIO over S3A and read it back.

Run inside the cluster:
  docker exec spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 /opt/spark-jobs/smoke_delta.py
"""
from pyspark.sql import SparkSession

PATH = "s3a://lakehouse/_smoke/delta_check"

spark = SparkSession.builder.appName("smoke-delta").getOrCreate()

spark.range(0, 5).withColumnRenamed("id", "n").write.format("delta").mode("overwrite").save(PATH)
rows = spark.read.format("delta").load(PATH).count()
versions = spark.sql(f"DESCRIBE HISTORY delta.`{PATH}`").count()
assert rows == 5, f"expected 5 rows, got {rows}"
print(f"Delta on MinIO OK: {rows} rows at {PATH}, {versions} table version(s)")

spark.stop()
