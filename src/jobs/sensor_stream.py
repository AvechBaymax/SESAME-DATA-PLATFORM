"""Sensor stream: Kafka -> Bronze -> Silver with Spark Structured Streaming.

Two streaming queries in one long-running application (the spark-streaming
service in docker-compose, not Airflow):

  1. bronze: Kafka topic sesame.sensor.raw -> lakehouse.bronze.sensor_raw
     Every message as received (key, partition, offset, headers, raw value).
     The Iceberg sink commits each micro-batch once, so a restart neither
     loses nor duplicates messages.
  2. silver: lakehouse.bronze.sensor_raw (read as a stream) -> foreachBatch:
       - parse the JSON and apply the rules in lakehouse.sensor.RULES, then
         check the device exists in silver.dim_device
       - valid readings  -> MERGE into silver.fact_sensor_reading on (device_id, ts),
                            so duplicates and replays are dropped
       - rejected        -> MERGE into silver.quarantine_sensor on (kafka_partition, kafka_offset)
       - unreadable ones -> also sent to the Kafka DLQ topic sesame.sensor.raw.dlq

  spark-submit sensor_stream.py                      # runs until stopped
  spark-submit sensor_stream.py --trigger "20 seconds"
  spark-submit sensor_stream.py --available-now      # process what is in Kafka, then stop
"""
import argparse
import os
import sys

from pyspark.sql import Window
from pyspark.sql import functions as F

from lakehouse import sensor
from lakehouse.spark import create_table_if_missing, ensure_namespace, get_spark
from lakehouse.tables import table

BRONZE = table("bronze", sensor.BRONZE_TABLE)
READINGS = table("silver", sensor.READING_TABLE)
QUARANTINE = table("silver", sensor.QUARANTINE_TABLE)
DEVICES = table("silver", "dim_device")


def ensure_tables(spark):
    ensure_namespace(spark, "bronze")
    ensure_namespace(spark, "silver")
    create_table_if_missing(spark, BRONZE, sensor.BRONZE_DDL, sensor.BRONZE_PARTITION)
    create_table_if_missing(spark, READINGS, sensor.READING_DDL, sensor.READING_PARTITION)
    create_table_if_missing(spark, QUARANTINE, sensor.QUARANTINE_DDL)


# ---- query 1: Kafka -> Bronze --------------------------------------------------------------

def kafka_to_bronze_rows(kafka_df):
    """Kafka source rows -> Bronze columns (nothing parsed, nothing dropped)."""
    headers = F.map_from_entries(F.transform(
        F.coalesce(F.col("headers"), F.array()),
        lambda h: F.struct(h["key"], h["value"].cast("string"))))
    return kafka_df.select(
        F.col("key").cast("string").alias("kafka_key"),
        F.col("topic").alias("kafka_topic"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
        F.col("timestamp").alias("kafka_ts"),
        headers.alias("headers"),
        F.col("value").cast("string").alias("value"),
        F.current_timestamp().alias("_ingested_at"),
    )


def start_bronze(spark, args):
    kafka = (spark.readStream.format("kafka")
             .option("kafka.bootstrap.servers", args.broker)
             .option("subscribe", args.topic)
             .option("startingOffsets", "earliest")
             .option("includeHeaders", "true")
             .option("failOnDataLoss", "false")
             .option("maxOffsetsPerTrigger", args.max_offsets)
             .load())
    writer = (kafka_to_bronze_rows(kafka).writeStream.format("iceberg").outputMode("append")
              .queryName("sensor_bronze")
              .option("checkpointLocation", f"{args.checkpoint_dir}/sensor_raw"))
    return trigger(writer, args).toTable(BRONZE)


# ---- query 2: Bronze -> Silver ---------------------------------------------------------------

def parse(bronze_df):
    """Bronze rows + parsed message `m` (NULL when the value is not valid JSON) and `ts`."""
    valid_json = F.get_json_object("value", "$").isNotNull()
    m = F.when(valid_json, F.from_json("value", sensor.MESSAGE_SCHEMA))
    return (bronze_df.withColumn("m", m)
            .withColumn("ts", F.timestamp_seconds(F.col("m.timestamp")))
            .withColumn("mock_fault", F.col("headers")[sensor.FAULT_HEADER]))


def classify(spark, bronze_df, devices_df=None):
    """Split a Bronze batch into (readings, rejected).

    readings: Silver columns, one row per (device_id, ts) in the batch.
    rejected: quarantine columns, with the first rule each message broke.
    devices_df (one column device_id): known devices; None skips that check.
    """
    df = parse(bronze_df).withColumn("dq_rule", F.expr(sensor.rule_case_sql())) \
                         .withColumn("dq_detail", F.expr(sensor.detail_case_sql()))
    if devices_df is not None:
        known = devices_df.select(F.col("device_id").alias("_known_device")).distinct()
        df = (df.join(known, F.col("m.device_id") == F.col("_known_device"), "left")
                .withColumn("dq_detail", F.when(F.col("dq_rule").isNull() & F.col("_known_device").isNull(),
                                                F.col("m.device_id")).otherwise(F.col("dq_detail")))
                .withColumn("dq_rule", F.when(F.col("dq_rule").isNull() & F.col("_known_device").isNull(),
                                              F.lit(sensor.KNOWN_DEVICE_RULE)).otherwise(F.col("dq_rule")))
                .drop("_known_device"))

    fields = [F.col(f"m.{path}").cast(kind).alias(column)
              for column, (kind, path) in sensor.READING_FIELDS.items()]
    first_arrival = Window.partitionBy(*sensor.READING_KEYS).orderBy("kafka_partition", "kafka_offset")
    readings = (df.where("dq_rule IS NULL")
                  .select(*fields, "ts", "kafka_partition", "kafka_offset")
                  .withColumn("_rn", F.row_number().over(first_arrival))
                  .where("_rn = 1").drop("_rn")              # duplicates: the first message wins
                  .withColumn("_updated_at", F.current_timestamp()))
    readings = readings.select(*[c.split(" ")[0] for c in sensor.READING_DDL.split(", ")])
    rejected = (df.where("dq_rule IS NOT NULL")
                  .select("kafka_partition", "kafka_offset", F.col("m.device_id").alias("device_id"), "ts",
                          F.col("value").alias("raw_value"), "dq_rule", "dq_detail", "mock_fault",
                          F.current_timestamp().alias("quarantined_at")))
    return readings, rejected


def merge_new(spark, df, target, keys, view):
    """Insert rows whose key is not in the table yet (replays and duplicates are skipped)."""
    df.createOrReplaceTempView(view)
    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    spark.sql(f"MERGE INTO {target} t USING {view} s ON {on} WHEN NOT MATCHED THEN INSERT *")


def send_to_dlq(rejected, broker, topic):
    rows = rejected.where(F.col("dq_rule").isin(sensor.dlq_rules()))
    if rows.isEmpty():
        return 0
    (rows.select(F.col("device_id").cast("string").alias("key"),
                 F.col("raw_value").alias("value"),
                 F.array(F.struct(F.lit("x-dq-rule").alias("key"),
                                  F.col("dq_rule").cast("binary").alias("value")),
                         F.struct(F.lit("x-source-offset").alias("key"),
                                  F.concat_ws("/", "kafka_partition", "kafka_offset")
                                   .cast("binary").alias("value"))).alias("headers"))
         .write.format("kafka").option("kafka.bootstrap.servers", broker).option("topic", topic).save())
    return rows.count()


def process_batch(args):
    def handle(batch_df, batch_id):
        # foreachBatch runs on a cloned session: temp views must live in that one
        spark = batch_df.sparkSession
        devices = spark.table(DEVICES).select("device_id") if spark.catalog.tableExists(DEVICES) else None
        if devices is None:
            print(f"[sensor_silver] batch {batch_id}: {DEVICES} not built yet, device check skipped")
        batch_df.persist()
        readings, rejected = classify(spark, batch_df, devices)
        readings.persist()
        rejected.persist()
        merge_new(spark, readings, READINGS, sensor.READING_KEYS, "sensor_readings_batch")
        merge_new(spark, rejected, QUARANTINE, sensor.QUARANTINE_KEYS, "sensor_quarantine_batch")
        dlq = send_to_dlq(rejected, args.broker, args.dlq_topic)
        by_rule = {r["dq_rule"]: r["count"] for r in rejected.groupBy("dq_rule").count().collect()}
        print(f"[sensor_silver] batch {batch_id}: {batch_df.count()} message(s), "
              f"{readings.count()} reading(s), {sum(by_rule.values())} quarantined {by_rule}, "
              f"{dlq} to DLQ")
        for df in (readings, rejected, batch_df):
            df.unpersist()
    return handle


def start_silver(spark, args):
    bronze = (spark.readStream.format("iceberg")
              .option("streaming-skip-delete-snapshots", "true")
              .option("streaming-skip-overwrite-snapshots", "true")
              .load(BRONZE))
    writer = (bronze.writeStream.queryName("sensor_silver")
              .option("checkpointLocation", f"{args.checkpoint_dir}/fact_sensor_reading")
              .foreachBatch(process_batch(args)))
    return trigger(writer, args).start()


def trigger(writer, args):
    return writer.trigger(availableNow=True) if args.available_now else \
        writer.trigger(processingTime=args.trigger)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kafka sensor stream -> Bronze -> Silver")
    ap.add_argument("--broker", default=os.getenv("KAFKA_BROKER", "kafka:29092"))
    ap.add_argument("--topic", default=os.getenv("KAFKA_TOPIC_SENSOR_RAW", "sesame.sensor.raw"))
    ap.add_argument("--dlq-topic", default=os.getenv("KAFKA_TOPIC_SENSOR_DLQ", "sesame.sensor.raw.dlq"))
    ap.add_argument("--checkpoint-dir", default=os.getenv("SENSOR_CHECKPOINT_DIR", "/opt/spark/checkpoints"))
    ap.add_argument("--trigger", default="1 minute", help='micro-batch interval, e.g. "20 seconds"')
    ap.add_argument("--max-offsets", type=int, default=50000, help="Kafka messages per Bronze batch")
    ap.add_argument("--available-now", action="store_true",
                    help="process what is available, then stop (catch-up run)")
    args = ap.parse_args(argv)

    spark = get_spark("sensor-stream")
    ensure_tables(spark)
    bronze = start_bronze(spark, args)
    if args.available_now:
        bronze.awaitTermination()  # Silver then sees every Bronze snapshot written by this run
    silver = start_silver(spark, args)
    if args.available_now:
        silver.awaitTermination()
    else:
        spark.streams.awaitAnyTermination()
    for query in (bronze, silver):
        if query.exception():
            raise query.exception()


if __name__ == "__main__":
    sys.exit(main())
