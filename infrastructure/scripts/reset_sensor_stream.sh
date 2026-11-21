#!/usr/bin/env bash
# Start the sensor stream over from nothing, e.g. after its settings or message
# schema changed. Deletes:
#   - lakehouse.bronze.sensor_raw, lakehouse.silver.fact_sensor_reading, lakehouse.silver.quarantine_sensor
#   - the stream's checkpoints (volume spark_checkpoints)
#   - every message in the Kafka topics sesame.sensor.raw and sesame.sensor.raw.dlq
# Nothing else is touched (batch Bronze/Silver, Gold, Airflow). Run from anywhere:
#   bash infrastructure/scripts/reset_sensor_stream.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
compose() { docker compose --env-file .env -f infrastructure/docker/docker-compose.yml "$@"; }
kafka_topics() { docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:29092 "$@"; }

read -r -p "This deletes all sensor data (Bronze, Silver, checkpoints, Kafka topics). Type 'reset' to go on: " answer
[ "$answer" = "reset" ] || { echo "Cancelled, nothing changed."; exit 1; }

echo "Stopping the stream..."
compose rm -sf spark-streaming

echo "Dropping the sensor tables..."
docker exec spark-master /opt/spark/bin/spark-sql --master spark://spark-master:7077 -e "
  DROP TABLE IF EXISTS lakehouse.bronze.sensor_raw PURGE;
  DROP TABLE IF EXISTS lakehouse.silver.fact_sensor_reading PURGE;
  DROP TABLE IF EXISTS lakehouse.silver.quarantine_sensor PURGE;" 2>/dev/null

echo "Removing the checkpoints..."
volume=$(docker volume ls -q --filter label=com.docker.compose.volume=spark_checkpoints)
[ -z "$volume" ] || docker volume rm $volume >/dev/null

echo "Emptying the Kafka topics..."
for topic in sesame.sensor.raw sesame.sensor.raw.dlq; do
  kafka_topics --delete --if-exists --topic "$topic"
done
while kafka_topics --list | grep -q '^sesame\.sensor\.raw'; do sleep 1; done   # deletion is asynchronous
compose up -d --force-recreate kafka-init >/dev/null
compose wait kafka-init >/dev/null 2>&1 || sleep 15   # older Compose has no "wait"

echo "Starting the stream again..."
compose up -d spark-streaming
echo "Done. Send readings with: docker exec airflow-scheduler python -m mock_iot.producer --all-farms --mode backfill"
