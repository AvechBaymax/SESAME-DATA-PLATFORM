"""Gold water balance (FAO-56) on the Silver reference rows the pipeline builds."""
from datetime import date, timedelta

import pytest

from common.crop import SESAME_FAO, crop_coefficient
from common.et0 import et0_penman_monteith
from common.soil import TEXTURES
from lakehouse import gold
from lakehouse.silver import TABLES as SILVER

STAGES = SILVER["dim_growth_stage"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})
CROP = SILVER["dim_crop"].parse(SESAME_FAO, {"crop": "sesame", "reference_type": "fao"})[0]
PLANTING = date(2025, 11, 15)
SEASON = {"season_id": "S", "farm_id": "F", "plot_id": "P", "planting_date": PLANTING,
          "harvest_date": PLANTING + timedelta(days=75), "latitude": 11.08, "soil_texture": "loamy_sand"}
DRY_DAY = {"tmin_c": 22.0, "tmax_c": 31.0, "rh_pct": 72.0, "wind_2m_m_s": 3.5, "rs_mj_m2": 20.0,
           "nasa_precip_mm": 0.0}


def days(overrides=None, **common):
    out = []
    for i in range(76):
        d = {"date": PLANTING + timedelta(days=i), **DRY_DAY, **common}
        d.update((overrides or {}).get(i, {}))
        out.append(d)
    return out


def test_kc_from_silver_stages_matches_the_common_crop_curve():
    for das in range(0, 80):
        assert gold.stage_kc(STAGES, das)[1] == pytest.approx(crop_coefficient(das) if das < 75
                                                                else SESAME_FAO["kc_coefficients"]["kc_end"])
    assert gold.stage_kc(STAGES, 0)[0] == "initial"
    assert gold.stage_kc(STAGES, 50)[0] == "mid_season"


def test_dim_crop_carries_the_water_balance_constants():
    assert CROP["depletion_fraction_p"] == 0.6 and CROP["max_root_depth_low_m"] == 1.0
    assert CROP["season_length_days"] == 75


def test_roots_grow_until_mid_season():
    assert gold.root_depth_m(STAGES, 0, 1.0) == pytest.approx(gold.ROOT_DEPTH_INITIAL_M)
    assert gold.root_depth_m(STAGES, 20, 1.0) == pytest.approx(0.2 + 0.8 * 20 / 40)
    assert gold.root_depth_m(STAGES, 60, 1.0) == pytest.approx(1.0)


def test_one_row_per_day_with_fao_et0():
    rows = gold.water_balance(SEASON, days(), STAGES, CROP)
    assert len(rows) == 76 and rows[0]["das"] == 0 and rows[-1]["das"] == 75
    expected = et0_penman_monteith(22, 31, 72, 3.5, 20, 11.08, PLANTING.timetuple().tm_yday, gold.ELEVATION_M)
    assert rows[0]["et0_mm"] == pytest.approx(expected)
    assert rows[0]["etc_mm"] == pytest.approx(expected * 0.35)
    fc, wp = TEXTURES["loamy_sand"][:2]
    assert rows[0]["taw_mm"] == pytest.approx(1000 * (fc - wp) * 0.2)


def test_without_water_depletion_grows_until_stress():
    rows = gold.water_balance(SEASON, days(), STAGES, CROP)
    stressed = [r for r in rows if r["water_stress_flag"]]
    assert stressed, "a dry season must reach water stress"
    first = stressed[0]
    previous = rows[first["das"] - 1]
    assert previous["depletion_mm"] > previous["raw_mm"]          # Ks drops once RAW is used up
    assert all(0 <= r["ks"] <= 1 for r in rows)
    assert all(r["eta_mm"] <= r["etc_mm"] + 1e-9 for r in rows)
    assert all(0 <= r["depletion_mm"] <= r["taw_mm"] + 1e-9 for r in rows)


def test_heavy_rain_refills_and_drains_the_rest():
    rows = gold.water_balance(SEASON, days({30: {"nasa_precip_mm": 120.0}}), STAGES, CROP)
    assert rows[30]["depletion_mm"] == 0
    assert rows[30]["deep_percolation_mm"] > 0
    assert rows[30]["rain_source"] == "nasa_power"


def test_light_rain_below_a_fifth_of_et0_is_not_effective():
    rows = gold.water_balance(SEASON, days({10: {"nasa_precip_mm": 0.5}, 11: {"nasa_precip_mm": 8.0}}),
                              STAGES, CROP)
    assert rows[10]["effective_precip_mm"] == 0
    assert rows[11]["effective_precip_mm"] == 8.0


def test_irrigation_prevents_stress():
    irrigated = {i: {"irrigation_mm": 15.0} for i in range(0, 76, 3)}
    rows = gold.water_balance(SEASON, days(irrigated), STAGES, CROP)
    assert not any(r["water_stress_flag"] for r in rows)


def test_plot_rain_gauge_is_used_only_with_enough_readings():
    full = {5: {"sensor_rain_mm": 20.0, "sensor_coverage": 0.95, "nasa_precip_mm": 3.0}}
    sparse = {5: {"sensor_rain_mm": 20.0, "sensor_coverage": 0.5, "nasa_precip_mm": 3.0}}
    assert gold.water_balance(SEASON, days(full), STAGES, CROP)[5]["precip_mm"] == 20.0
    row = gold.water_balance(SEASON, days(sparse), STAGES, CROP)[5]
    assert (row["precip_mm"], row["rain_source"]) == (3.0, "nasa_power")


def test_missing_weather_leaves_the_day_incomplete():
    rows = gold.water_balance(SEASON, days({40: {"rs_mj_m2": None}}), STAGES, CROP)
    assert rows[40]["et0_mm"] is None and rows[40]["weather_complete"] is False
    assert rows[40]["depletion_mm"] == rows[39]["depletion_mm"]   # no ET counted that day


def test_flags_and_degree_days():
    rows = gold.water_balance(SEASON, days({7: {"tmax_c": 36.0}, 8: {"soil_oxygen_min_pct": 6.0}}),
                              STAGES, CROP)
    assert rows[7]["heat_stress_flag"] and not rows[6]["heat_stress_flag"]
    assert rows[8]["waterlog_flag"] and not rows[7]["waterlog_flag"]
    assert rows[0]["gdd"] == pytest.approx((22 + 31) / 2 - gold.GDD_BASE_C)
    assert rows[-1]["gdd_cum"] == pytest.approx(sum(r["gdd"] for r in rows))


def test_gold_tables_hold_their_keys_and_feat_rows_fit_the_schema():
    for table in gold.TABLES.values():
        assert set(table.keys) <= set(table.column_names)
    row = gold.water_balance(SEASON, days(), STAGES, CROP)[0]
    assert set(gold.FEAT_SEASON_DAILY.column_names) - {"_updated_at"} == set(row)
