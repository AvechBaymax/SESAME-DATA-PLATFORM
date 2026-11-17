#!/usr/bin/env python3
"""
Mock IoT sensor stream for the sesame plots (Libelium node -> Kafka).

Readings come from a stateful simulation (see simulator.py) driven by the same
weather the API ingesters land, so sensor data and API data stay consistent.

Modes:
  live      one message per device every --interval seconds, stamped "now"
  backfill  a past/future period at --step-minutes resolution, sent as fast as possible

Sinks: kafka (default, key=device_id), stdout, or a .jsonl file.

Usage (run from the repo root after `pip install -e .`):
  python -m mock_iot.producer --mode live --interval 5
  python -m mock_iot.producer --mode backfill --planting-date 2025-11-15 \\
      --start 2025-11-10 --days 80 --sink jsonl --output data/sensor_season.jsonl
  python -m mock_iot.producer --mode backfill --start 2025-11-10 --days 80 --weather nasa

--fault-rate injects malformed/out-of-range/missing/duplicate/late messages so the
DLQ and data-quality checks have something to catch. The fault type is sent as
the Kafka header "x-mock-fault" (never inside the payload).
"""
import argparse
import copy
import json
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone

from common.config import get_kafka_broker, get_sensor_topic
from common.farm import add_farm_args, farm_from_args
from mock_iot.simulator import SensorNode
from mock_iot.weather import ClimatologyWeather, DiurnalWeather, NasaPowerWeather

FAULT_TYPES = ("malformed", "out_of_range", "missing_field", "duplicate", "late")


# ---- fault injection ----------------------------------------------------------------
def inject_fault(message, fault, rng):
    """Return a list of (bytes, fault) records for one simulated message."""
    clean = json.dumps(message).encode("utf-8")
    if fault is None:
        return [(clean, None)]
    if fault == "malformed":
        return [(clean[: rng.randint(10, len(clean) - 10)], fault)]  # truncated JSON
    if fault == "duplicate":
        return [(clean, None), (clean, fault)]
    bad = copy.deepcopy(message)
    if fault == "out_of_range":
        target = rng.choice(["vwc", "temp", "humidity"])
        if target == "vwc":
            bad["measurements"]["soil"]["vwc_pct"] = 150.0
        elif target == "temp":
            bad["measurements"]["weather_micro"]["ambient_temp_c"] = -9999.0  # sensor error sentinel
        else:
            bad["measurements"]["weather_micro"]["humidity_pct"] = 130.0
        bad["telemetry"]["error_codes"].append("E_RANGE")
    elif fault == "missing_field":
        del bad["measurements"][rng.choice(["soil", "canopy", "weather_micro"])]
    elif fault == "late":
        late_ts = bad["timestamp"] - 2 * 3600
        bad["timestamp"] = late_ts
        bad["datetime"] = datetime.fromtimestamp(late_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [(json.dumps(bad).encode("utf-8"), fault)]


def pick_fault(rate, rng):
    return rng.choice(FAULT_TYPES) if rng.random() < rate else None


# ---- sinks --------------------------------------------------------------------------
class KafkaSink:
    def __init__(self, broker, topic):
        from kafka import KafkaProducer

        self.topic = topic
        self.producer = KafkaProducer(
            bootstrap_servers=[broker],
            key_serializer=lambda k: k.encode("utf-8"),
            acks="all",
            retries=5,
            linger_ms=20,
        )
        print(f"Connected to Kafka at {broker}, topic {topic}", file=sys.stderr)

    def send(self, key, value, fault):
        headers = [("x-mock-fault", fault.encode())] if fault else None
        self.producer.send(self.topic, key=key, value=value, headers=headers)

    def flush(self):
        self.producer.flush()

    def close(self):
        self.producer.flush()
        self.producer.close()


class FileSink:
    def __init__(self, stream):
        self.stream = stream

    def send(self, key, value, fault):
        self.stream.write(value.decode("utf-8", errors="replace") + "\n")

    def flush(self):
        self.stream.flush()

    def close(self):
        self.flush()
        if self.stream is not sys.stdout:
            self.stream.close()


def make_sink(args):
    if args.sink == "kafka":
        return KafkaSink(args.broker, args.topic)
    if args.sink == "jsonl":
        if not args.output:
            raise SystemExit("--sink jsonl requires --output")
        return FileSink(open(args.output, "w", encoding="utf-8"))
    return FileSink(sys.stdout)


# ---- runners ------------------------------------------------------------------------
def build_nodes(farm, args):
    climatology = ClimatologyWeather(seed=farm.farm_id)
    daily = climatology
    if args.weather == "nasa":
        start = args.start - timedelta(days=1)
        end = args.start + timedelta(days=args.days + 1)
        try:
            daily = NasaPowerWeather(farm.lat, farm.lon, start, end, fallback=climatology)
            print(f"NASA POWER drivers loaded for {len(daily.records)} days", file=sys.stderr)
        except Exception as exc:  # network/API issue: keep producing from climatology
            print(f"NASA POWER unavailable ({exc}); using climatology", file=sys.stderr)
    weather = DiurnalWeather(daily, farm.lat, farm.lon, seed=farm.farm_id)
    return [SensorNode(farm, device_id, weather, seed=f"{args.seed}:{device_id}")
            for device_id in farm.device_ids]


def emit(sink, node, message, args, rng, stats):
    for value, fault in inject_fault(message, pick_fault(args.fault_rate, rng), rng):
        sink.send(node.device_id, value, fault)
        stats[fault or "ok"] = stats.get(fault or "ok", 0) + 1


def run_backfill(nodes, sink, args, rng):
    step = timedelta(minutes=args.step_minutes)
    start = datetime(args.start.year, args.start.month, args.start.day, tzinfo=timezone.utc)
    end = start + timedelta(days=args.days)
    stats = {}
    t = start
    while t < end:
        for node in nodes:
            emit(sink, node, node.step(t, t + step), args, rng, stats)
        t += step
    sink.flush()
    print(f"Backfill {start:%Y-%m-%d} -> {end:%Y-%m-%d} done: {stats}", file=sys.stderr)


def run_live(nodes, sink, args, rng):
    interval = timedelta(seconds=args.interval)
    last = datetime.now(timezone.utc).replace(microsecond=0) - interval
    stats = {}
    print("Press Ctrl+C to stop.", file=sys.stderr)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for node in nodes:
            message = node.step(last, now)
            emit(sink, node, message, args, rng, stats)
            soil = message["measurements"]["soil"]
            print(f"[{message['datetime']}] {node.device_id} vwc={soil['vwc_pct']}% "
                  f"rain={message['measurements']['weather_micro']['rain_mm']}mm", file=sys.stderr)
        sink.flush()
        last = now
        time.sleep(args.interval)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Simulated sesame field sensors -> Kafka")
    add_farm_args(ap)
    ap.add_argument("--mode", choices=["live", "backfill"], default="live")
    ap.add_argument("--interval", type=float, default=5.0, help="live: seconds between messages")
    ap.add_argument("--start", type=date.fromisoformat, default=None,
                    help="backfill: first day YYYY-MM-DD (default: planting date - 5 days)")
    ap.add_argument("--days", type=int, default=85, help="backfill: number of days")
    ap.add_argument("--step-minutes", type=int, default=15, help="backfill: sample interval")
    ap.add_argument("--weather", choices=["climatology", "nasa"], default="climatology",
                    help="daily drivers: Binh Thuan climatology or NASA POWER (past dates only)")
    ap.add_argument("--fault-rate", type=float, default=0.0, help="share of messages with injected faults")
    ap.add_argument("--seed", default="sesame", help="random seed for reproducible runs")
    ap.add_argument("--sink", choices=["kafka", "stdout", "jsonl"], default="kafka")
    ap.add_argument("--output", help="jsonl sink: output file")
    ap.add_argument("--broker", default=None, help="default: $KAFKA_BROKER or localhost:9092")
    ap.add_argument("--topic", default=None, help="default: $KAFKA_TOPIC_SENSOR_RAW or sesame.sensor.raw")
    args = ap.parse_args(argv)
    if args.start is None:
        args.start = args.planting_date - timedelta(days=5)
    if not 0 <= args.fault_rate <= 1:
        ap.error("--fault-rate must be between 0 and 1")
    args.broker = get_kafka_broker(args.broker)
    args.topic = get_sensor_topic(args.topic)
    return args


def main(argv=None):
    args = parse_args(argv)
    farm = farm_from_args(args)
    rng = random.Random(f"{args.seed}:faults")
    nodes = build_nodes(farm, args)
    sink = make_sink(args)
    try:
        if args.mode == "backfill":
            run_backfill(nodes, sink, args, rng)
        else:
            run_live(nodes, sink, args, rng)
    except KeyboardInterrupt:
        print("\nMock process stopped.", file=sys.stderr)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
