#!/usr/bin/env python3
"""
Offline stand-ins for the NASA POWER and SoilGrids APIs.

They write payloads with the same structure as the real responses to the same
landing keys as nasa_ingest / isric_ingest, so the Bronze/Silver jobs can be
developed and tested when the APIs are unreachable. Every payload carries
"mock": true at the top level so it can be told apart from real data.

Usage:
  python -m mock_iot.mock_api_responses nasa --start 20251101 --end 20260215
  python -m mock_iot.mock_api_responses soilgrids
"""
import argparse
from datetime import datetime, timedelta, timezone

from common.config import get_minio_settings
from common.farm import DEFAULT_FARM_ID, DEFAULT_LAT, DEFAULT_LON
from common.minio_utils import upload_raw_json
from mock_iot.nasa_ingest import DEFAULT_PARAMETERS
from mock_iot.weather import ClimatologyWeather

NASA_UNITS = {  # community=ag
    "T2M": ("C", "Temperature at 2 Meters"),
    "T2M_MAX": ("C", "Temperature at 2 Meters Maximum"),
    "T2M_MIN": ("C", "Temperature at 2 Meters Minimum"),
    "RH2M": ("%", "Relative Humidity at 2 Meters"),
    "WS2M": ("m/s", "Wind Speed at 2 Meters"),
    "ALLSKY_SFC_SW_DWN": ("MJ/m^2/day", "All Sky Surface Shortwave Downward Irradiance"),
    "PRECTOTCORR": ("mm/day", "Precipitation Corrected"),
}

# Loamy sand typical of Binh Thuan, in SoilGrids mapped units (value / d_factor = target unit)
SOIL_LAYERS = {
    # name: (d_factor, mapped_units, target_units, {depth_label: mapped value})
    "sand": (10, "g/kg", "%", {"0-5cm": 805, "5-15cm": 798, "15-30cm": 785, "30-60cm": 760}),
    "clay": (10, "g/kg", "%", {"0-5cm": 78, "5-15cm": 84, "15-30cm": 96, "30-60cm": 120}),
    "silt": (10, "g/kg", "%", {"0-5cm": 117, "5-15cm": 118, "15-30cm": 119, "30-60cm": 120}),
    "phh2o": (10, "pH*10", "-", {"0-5cm": 61, "5-15cm": 60, "15-30cm": 59, "30-60cm": 58}),
    "soc": (10, "dg/kg", "g/kg", {"0-5cm": 62, "5-15cm": 48, "15-30cm": 35, "30-60cm": 22}),
    "nitrogen": (100, "cg/kg", "g/kg", {"0-5cm": 68, "5-15cm": 55, "15-30cm": 42, "30-60cm": 30}),
    "bdod": (100, "cg/cm³", "kg/dm³", {"0-5cm": 148, "5-15cm": 152, "15-30cm": 155, "30-60cm": 158}),
    "cec": (10, "mmol(c)/kg", "cmol(c)/kg", {"0-5cm": 58, "5-15cm": 54, "15-30cm": 50, "30-60cm": 47}),
}
DEPTHS = {"0-5cm": (0, 5), "5-15cm": (5, 15), "15-30cm": (15, 30), "30-60cm": (30, 60)}


def nasa_payload(lat, lon, start, end, seed):
    weather = ClimatologyWeather(seed=seed)
    params = {name: {} for name in DEFAULT_PARAMETERS.split(",")}
    day = start
    while day <= end:
        d = weather.daily(day)
        key = f"{day:%Y%m%d}"
        params["T2M"][key] = round((d.tmin_c + d.tmax_c) / 2, 2)
        params["T2M_MAX"][key] = d.tmax_c
        params["T2M_MIN"][key] = d.tmin_c
        params["RH2M"][key] = d.rh_pct
        params["WS2M"][key] = d.wind_2m_m_s
        params["ALLSKY_SFC_SW_DWN"][key] = d.rs_mj_m2
        params["PRECTOTCORR"][key] = d.precip_mm
        day += timedelta(days=1)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat, 50.0]},
        "properties": {"parameter": params},
        "header": {"title": "NASA/POWER (mock)", "fill_value": -999.0,
                   "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}", "time_standard": "LST"},
        "messages": [],
        "parameters": {name: {"units": NASA_UNITS[name][0], "longname": NASA_UNITS[name][1]}
                       for name in params},
        "mock": True,
    }


def soilgrids_payload(lat, lon):
    layers = []
    for name, (d_factor, mapped, target, values) in SOIL_LAYERS.items():
        layers.append({
            "name": name,
            "unit_measure": {"d_factor": d_factor, "mapped_units": mapped, "target_units": target,
                             "uncertainty_unit": ""},
            "depths": [{"range": {"top_depth": top, "bottom_depth": bottom, "unit_depth": "cm"},
                        "label": label, "values": {"mean": values[label]}}
                       for label, (top, bottom) in DEPTHS.items()],
        })
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"layers": layers}, "query_time_s": 0.0, "mock": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write mock NASA POWER / SoilGrids responses to the landing zone")
    ap.add_argument("api", choices=["nasa", "soilgrids"])
    ap.add_argument("--farm-id", default=DEFAULT_FARM_ID)
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--start", default="20251101", help="nasa: YYYYMMDD")
    ap.add_argument("--end", default="20260215", help="nasa: YYYYMMDD")
    ap.add_argument("--bucket", default=None)
    args = ap.parse_args(argv)
    settings = get_minio_settings(bucket=args.bucket)

    if args.api == "nasa":
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        payload = nasa_payload(args.lat, args.lon, start, end, seed=args.farm_id)
        now = datetime.now(timezone.utc)
        uri = upload_raw_json("nasa_power", payload,
                              filename=f"data_{args.start}_to_{args.end}_{now:%H%M%S}Z_mock.json",
                              settings=settings, farm_id=args.farm_id, ingest_dt=f"{now:%Y-%m-%d}")
    else:
        uri = upload_raw_json("soilgrids", soilgrids_payload(args.lat, args.lon),
                              filename="soilgrids.json", settings=settings, farm_id=args.farm_id)
    print(f"Uploaded {uri}")


if __name__ == "__main__":
    main()
