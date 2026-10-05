#!/usr/bin/env python3
import json
import time
import random
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from kafka import KafkaProducer

load_dotenv()

# Cấu hình Kafka từ file .env hoặc mặc định localhost
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC = "sesame.raw.iot.telemetry"

def create_kafka_producer():
    """Khởi tạo Kafka Producer với cấu hình parse value sang JSON"""
    try:
        producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=3
        )
        print(f"Connected to Kafka Broker at {KAFKA_BROKER}")
        return producer
    except Exception as e:
        print(f"Failed to connect to Kafka: {e}")
        return None

def generate_sensor_payload(device_id="SN_CUCHI_01", farm_id="CUCHI_02"):
    now = datetime.now(timezone.utc)

    ambient_temp = round(random.uniform(25.0, 35.0), 1)
    is_raining = random.choices([True, False], weights=[0.15, 0.85])[0]
    rain_mm = round(random.uniform(2.0, 15.0), 1) if is_raining else 0.0

    payload = {
        "farm_id": farm_id,          # THÊM MỚI: Để JOIN với file cấu hình User và Weather
        "device_id": device_id,
        "device_type": "libelium_agri_pro",
        "timestamp": int(now.timestamp()),
        "datetime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurements": {
            "soil": {
                "vwc_pct": round(random.uniform(35.0, 45.0) if is_raining else random.uniform(20.0, 30.0), 2),
                "temp_c": round(ambient_temp - random.uniform(1.0, 3.0), 1),
                "ec_ds_m": round(random.uniform(0.6, 1.2), 2),
                "water_potential_kpa": round(random.uniform(-40.0, -10.0), 1),
                "oxygen_level_pct": round(random.uniform(15.0, 21.0), 1)
            },
            "canopy": {
                "leaf_wetness_lvl": random.randint(3, 5) if is_raining else random.randint(0, 1),
                "surface_temp_c": round(ambient_temp + random.uniform(0.5, 2.5), 1)
            },
            "weather_micro": {
                "ambient_temp_c": ambient_temp,
                "humidity_pct": round(random.uniform(80.0, 95.0) if is_raining else random.uniform(50.0, 75.0), 1),
                "rain_mm_15m": rain_mm,
                "wind_speed_m_s": round(random.uniform(0.5, 5.5), 1)
            }
        },
        "telemetry": {
            "battery_pct": random.randint(60, 100),
            "rssi_dbm": random.randint(-85, -60),
            "error_codes": []
        },
        "flags": {
            "irrigation_triggered": not is_raining and random.choice([True, False]),
            "rain_detected": is_raining
        }
    }
    return payload

if __name__ == "__main__":
    print("--- STARTING IoT SESAME FARM DATA MOCK TO KAFKA ---")
    
    producer = create_kafka_producer()
    if not producer:
        exit(1)

    print(f"Target Topic: {KAFKA_TOPIC}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            # Tạo dữ liệu
            mock_data = generate_sensor_payload(device_id="SN_CUCHI_01", farm_id="CUCHI_02")

            # Bắn vào Kafka
            producer.send(KAFKA_TOPIC, value=mock_data)
            
            # Đảm bảo dữ liệu được đẩy đi ngay lập tức
            producer.flush()

            print(f"[{mock_data['datetime']}] Sent telemetry for {mock_data['device_id']}")
            
            # Nghỉ 2 giây cho giống thiết bị thật
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nMock process stopped.")
    finally:
        if producer:
            producer.close()