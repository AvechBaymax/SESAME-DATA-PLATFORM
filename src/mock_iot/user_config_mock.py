#!/usr/bin/env python3
"""
Generate and upload a mock farm configuration (what a user would enter) and the
season's planned field operations to MinIO.

The config's sections map one-to-one to the Silver dimensions (farm -> dim_farm,
plot -> dim_plot, season -> dim_season, devices -> dim_device); the plan rows
become fact_field_operation with status "planned". IDs come from the same
profile the IoT simulator uses, so everything joins on farm_id/plot_id/device_id.

Usage:
  python -m mock_iot.user_config_mock
  python -m mock_iot.user_config_mock --farm-id BINHTHUAN_02 --lat 11.10 --lon 108.05 \\
      --planting-date 2026-12-01 --devices 2
  python -m mock_iot.user_config_mock --dry-run

Object keys:
  user_config/farm_id=<farm_id>/config_<UTC timestamp>.json
  field_operation/farm_id=<farm_id>/season_id=<season_id>/plan_<UTC timestamp>.json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from common.config import add_minio_args, minio_settings_from_args
from common.crop import BINH_THUAN_PRACTICE, SESAME_FAO, in_sowing_window, season_length
from common.farm import add_farm_args, farm_from_args, ring_area_perimeter
from common.minio_utils import upload_raw_json, utc_now_compact
from mock_iot.simulator import DEVICE_TYPE, SENSOR_DEPTH_CM

SCHEMA_VERSION = "2.0"


def default_seed_lot(farm):
    # Illustrative lot; must meet the guide's standard (G >= 70 %, P >= 99 %).
    return {
        "lot_id": f"LOT-{farm.planting_date:%Y%m}-01",
        "w1000_g": 3.0,
        "germination_pct": 85.0,
        "purity_pct": 99.2,
        "moisture_pct": 7.5,
    }


def generate_mock_config(farm, seed_lot=None):
    now = datetime.now(timezone.utc).isoformat()
    ring = farm.polygon()
    area_m2, perimeter_m = ring_area_perimeter(ring)
    sowing = BINH_THUAN_PRACTICE["sowing"]
    length = season_length()

    return {
        "schema_version": SCHEMA_VERSION,
        "farm": {
            "farm_id": farm.farm_id,
            "province": "Bình Thuận",
            "country": "VN",
            "latitude": farm.lat,
            "longitude": farm.lon,
        },
        "plot": {
            "plot_id": farm.plot_id,
            "farm_id": farm.farm_id,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "area_m2": round(area_m2, 1),
            "perimeter_m": round(perimeter_m, 1),
            "soil_texture": farm.soil_texture,
            "drainage": farm.drainage,
            "soil_reference": "isric_soilgrids_v2",
            "note": "Illustrative rectangle around the plot centre",
        },
        "season": {
            "season_id": farm.season_id,
            "plot_id": farm.plot_id,
            "crop_id": SESAME_FAO["crop_id"],
            "variety": BINH_THUAN_PRACTICE["variety"],
            "season_type": farm.season_type,
            "planting_date": farm.planting_date.isoformat(),
            "expected_harvest_date": (farm.planting_date + timedelta(days=length)).isoformat(),
            "actual_harvest_date": None,
            "season_length_days": length,
            "in_recommended_sowing_window": in_sowing_window(farm.planting_date),
            "management": {
                "row_spacing_cm": sowing["row_spacing_cm"],
                "hill_spacing_cm": sowing["hill_spacing_cm"],
                "plants_per_hill": 1,
                "seed_rate_kg_ha": sowing["seed_rate_kg_ha"],
                "irrigation_method": "sprinkler",
            },
            "seed_lot": seed_lot or default_seed_lot(farm),
        },
        "devices": [
            {"device_id": device_id, "plot_id": farm.plot_id, "device_type": DEVICE_TYPE,
             "soil_probe_depth_cm": SENSOR_DEPTH_CM,
             "installed_date": (farm.planting_date - timedelta(days=7)).isoformat()}
            for device_id in farm.device_ids
        ],
        "system_metadata": {
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "note": "Mock config for PoC Data Lakehouse",
        },
    }


def _operation(farm, op_type, das, details):
    return {
        "operation_id": f"{farm.season_id}_{op_type}_{das:03d}",
        "season_id": farm.season_id,
        "plot_id": farm.plot_id,
        "op_type": op_type,
        "das": das,
        "planned_date": (farm.planting_date + timedelta(days=das)).isoformat(),
        "actual_date": None,
        "status": "planned",
        "details": details,
    }


def generate_field_operation_plan(farm):
    """Planned operations from the Binh Thuan guide (rows of fact_field_operation).

    Irrigation is not planned ahead: it follows soil moisture and rain, and is
    recorded as "done" when it happens.
    """
    sowing = BINH_THUAN_PRACTICE["sowing"]
    fertilizer = BINH_THUAN_PRACTICE["fertilizer"]
    ops = [_operation(farm, "sowing", 0, {
        "seed_rate_kg_ha": sowing["seed_rate_kg_ha"],
        "row_spacing_cm": sowing["row_spacing_cm"],
        "hill_spacing_cm": sowing["hill_spacing_cm"],
    })]
    for event in fertilizer["schedule"]:
        ops.append(_operation(farm, "fertilizer", event["das"], {
            "application": event["type"],
            "products_kg_ha": {name: round(fertilizer["products_kg_ha"][name] * share, 1)
                               for name, share in event["share"].items()},
        }))
    for das in BINH_THUAN_PRACTICE["weed_control_das"]:
        ops.append(_operation(farm, "weed_control", das, {"method": "manual or pre-emergent herbicide"}))
    ops.append(_operation(farm, "harvest", season_length(), {
        "trigger": BINH_THUAN_PRACTICE["harvest"]["trigger"],
    }))
    return sorted(ops, key=lambda op: (op["das"], op["op_type"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate and upload mock farm config and field plan to MinIO")
    add_farm_args(ap)
    add_minio_args(ap)
    ap.add_argument("--dry-run", action="store_true", help="print the payloads without uploading")
    args = ap.parse_args(argv)

    farm = farm_from_args(args)
    config = generate_mock_config(farm)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "season_id": farm.season_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operations": generate_field_operation_plan(farm),
    }
    if not config["season"]["in_recommended_sowing_window"]:
        print(f"Warning: planting date {farm.planting_date} is outside the Binh Thuan sowing "
              "windows (Nov-Dec, Feb-Mar)", file=sys.stderr)

    print(json.dumps({"config": config, "field_operation_plan": plan}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    settings = minio_settings_from_args(args)
    stamp = utc_now_compact()
    print("Uploaded " + upload_raw_json(
        "user_config", config, filename=f"config_{stamp}.json",
        settings=settings, indent=2, farm_id=farm.farm_id))
    print("Uploaded " + upload_raw_json(
        "field_operation", plan, filename=f"plan_{stamp}.json",
        settings=settings, indent=2, farm_id=farm.farm_id, season_id=farm.season_id))


if __name__ == "__main__":
    main()
