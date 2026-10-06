#!/usr/bin/env python3
"""
Generate and upload a mock farm/plot configuration (what a user would enter) to MinIO.

The farm id, location, soil and planting date come from the same profile the
IoT simulator uses, so config and sensor data join on farm_id/plot_id/device_id.

Usage:
  python -m mock_iot.user_config_mock
  python -m mock_iot.user_config_mock --farm-id BINHTHUAN_02 --lat 11.10 --lon 108.05 \\
      --planting-date 2026-12-01 --devices 2

Object key layout:
  user_config/farm_id=<farm_id>/config_<UTC timestamp>.json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from common.config import add_minio_args, minio_settings_from_args
from common.crop import BINH_THUAN_PRACTICE, in_sowing_window, season_length
from common.farm import add_farm_args, farm_from_args, ring_area_perimeter
from common.minio_utils import upload_raw_json, utc_now_compact
from mock_iot.simulator import SENSOR_DEPTH_CM


def generate_mock_config(farm, seed_lot=None):
    now = datetime.now(timezone.utc).isoformat()
    ring = farm.polygon()
    area_m2, perimeter_m = ring_area_perimeter(ring)
    sowing = BINH_THUAN_PRACTICE["sowing"]
    seed_lot = seed_lot or {
        # Illustrative lot; must meet the guide's standard (G >= 70 %, P >= 99 %).
        "lot_id": f"LOT-{farm.planting_date:%Y%m}-01",
        "w1000_g": 3.0,
        "germination_pct": 85.0,
        "purity_pct": 99.2,
        "moisture_pct": 7.5,
    }

    fertilizer = BINH_THUAN_PRACTICE["fertilizer"]
    plan = [
        {
            "das": event["das"],
            "planned_date": (farm.planting_date + timedelta(days=event["das"])).isoformat(),
            "type": event["type"],
            "products_kg_ha": {name: round(fertilizer["products_kg_ha"][name] * share, 1)
                               for name, share in event["share"].items()},
        }
        for event in fertilizer["schedule"]
    ]

    return {
        "farm_id": farm.farm_id,
        "plot_id": farm.plot_id,
        "location": {
            "latitude": farm.lat,
            "longitude": farm.lon,
            "province": "Bình Thuận",
            "country": "VN",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [ring],
            "area_m2": round(area_m2, 1),
            "perimeter_m": round(perimeter_m, 1),
            "note": "Illustrative rectangle around the plot centre",
        },
        "soil": {
            "texture_class": farm.soil_texture,
            "reference": "isric_soilgrids_v2",
            "lab_test": None,  # N_soil etc. from a real soil test, when available
        },
        "crop_metadata": {
            "crop_type": BINH_THUAN_PRACTICE["variety"],
            "planting_date": farm.planting_date.isoformat(),
            "season_length_days": season_length(),
            "expected_harvest_date": (farm.planting_date + timedelta(days=season_length())).isoformat(),
            "in_recommended_sowing_window": in_sowing_window(farm.planting_date),
        },
        "management": {
            "row_spacing_cm": sowing["row_spacing_cm"],
            "hill_spacing_cm": sowing["hill_spacing_cm"],
            "plants_per_hill": 1,
            "seed_rate_kg_ha": sowing["seed_rate_kg_ha"],
            "irrigation_method": "sprinkler",
            "fertilizer_plan": plan,
        },
        "seed_lot": seed_lot,
        "devices": [
            {"device_id": device_id, "device_type": "libelium_smart_agriculture_xtreme",
             "soil_probe_depth_cm": SENSOR_DEPTH_CM}
            for device_id in farm.device_ids
        ],
        "system_metadata": {
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "note": "Mock config for PoC Data Lakehouse",
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate and upload mock farm config to MinIO")
    add_farm_args(ap)
    add_minio_args(ap)
    ap.add_argument("--dry-run", action="store_true", help="print the payload without uploading")
    args = ap.parse_args(argv)

    farm = farm_from_args(args)
    payload = generate_mock_config(farm)
    if not payload["crop_metadata"]["in_recommended_sowing_window"]:
        print(f"Warning: planting date {farm.planting_date} is outside the Binh Thuan sowing "
              "windows (Nov-Dec, Feb-Mar)", file=sys.stderr)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    uri = upload_raw_json("user_config", payload, filename=f"config_{utc_now_compact()}.json",
                          settings=minio_settings_from_args(args), indent=2, farm_id=farm.farm_id)
    print(f"Uploaded {uri}")


if __name__ == "__main__":
    main()
