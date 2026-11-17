"""Silver parsers on the payloads the ingesters and mocks actually write."""
from datetime import date, datetime

import pytest

from common.crop import BINH_THUAN_PRACTICE, SESAME_FAO
from common.farm import FarmProfile
from lakehouse.silver import TABLES, coerce, tables_for, to_tuple
from lakehouse.sources import SOURCES as BRONZE_SOURCES
from mock_iot.mock_api_responses import nasa_payload, soilgrids_payload
from mock_iot.user_config_mock import generate_field_operation_plan, generate_mock_config

FARM = FarmProfile(planting_date=date(2025, 11, 15), device_count=2)


def rows(table, payload, **partitions):
    return TABLES[table].parse(payload, partitions)


def test_every_silver_table_reads_a_known_bronze_source_and_has_its_keys_as_columns():
    for t in TABLES.values():
        assert t.source in BRONZE_SOURCES
        assert set(t.keys) <= set(t.column_names)
        for column, ref_table, ref_column in t.references:
            assert column in t.column_names
            assert ref_column in TABLES[ref_table].column_names


def test_referenced_tables_are_built_first():
    order = list(TABLES)
    for t in TABLES.values():
        for _, ref_table, _ in t.references:
            assert order.index(ref_table) < order.index(t.name)
    assert [t.name for t in tables_for("user_config")] == ["dim_farm", "dim_plot", "dim_season", "dim_device"]


def test_user_config_becomes_the_four_dimensions():
    config = generate_mock_config(FARM)
    farm = rows("dim_farm", config, farm_id=FARM.farm_id)
    plot = rows("dim_plot", config)
    season = rows("dim_season", config)
    devices = rows("dim_device", config)

    assert farm[0]["farm_id"] == "BINHTHUAN_01" and farm[0]["latitude"] == 11.08
    assert plot[0]["plot_id"] == "BINHTHUAN_01_P01" and plot[0]["farm_id"] == "BINHTHUAN_01"
    assert plot[0]["geometry"]["type"] == "Polygon"
    assert season[0]["season_id"] == "BINHTHUAN_01_P01_2025DX"
    assert season[0]["planting_date"] == "2025-11-15"
    assert season[0]["seed_rate_kg_ha"] == BINH_THUAN_PRACTICE["sowing"]["seed_rate_kg_ha"]
    assert season[0]["germination_pct"] >= 70
    assert [d["device_id"] for d in devices] == ["SN_BINHTHUAN_01_P01_01", "SN_BINHTHUAN_01_P01_02"]


def test_rows_convert_to_spark_types():
    config = generate_mock_config(FARM)
    season = to_tuple(TABLES["dim_season"], rows("dim_season", config)[0])
    columns = TABLES["dim_season"].column_names
    as_dict = dict(zip(columns, season))
    assert as_dict["planting_date"] == date(2025, 11, 15)
    assert isinstance(as_dict["row_spacing_cm"], float)      # 60 in JSON -> DOUBLE
    assert as_dict["actual_harvest_date"] is None
    plot = dict(zip(TABLES["dim_plot"].column_names,
                    to_tuple(TABLES["dim_plot"], rows("dim_plot", config)[0])))
    assert plot["geometry"].startswith('{"type": "Polygon"')  # GeoJSON kept as a string
    farm = dict(zip(TABLES["dim_farm"].column_names,
                    to_tuple(TABLES["dim_farm"], rows("dim_farm", config)[0])))
    assert isinstance(farm["valid_from"], datetime)


def test_coerce_rejects_unknown_types():
    assert coerce(None, "DOUBLE") is None
    with pytest.raises(ValueError):
        coerce(1, "DECIMAL")


def test_growth_stages_follow_the_fao_kc_curve():
    stages = rows("dim_growth_stage", SESAME_FAO, crop="sesame", reference_type="fao")
    assert [s["stage"] for s in stages] == ["initial", "development", "mid_season", "late_season"]
    assert [(s["das_start"], s["das_end"]) for s in stages] == [(0, 15), (15, 40), (40, 65), (65, 75)]
    assert [(s["kc_start"], s["kc_end"]) for s in stages] == [
        (0.35, 0.35), (0.35, 1.10), (1.10, 1.10), (1.10, 0.25)]
    # the agronomy guide in the same Bronze table yields no stage rows
    assert rows("dim_growth_stage", BINH_THUAN_PRACTICE, crop="sesame", reference_type="agronomy") == []


def test_soil_layers_are_divided_by_d_factor():
    layers = rows("dim_soil_layer", soilgrids_payload(11.08, 108.12), farm_id="BINHTHUAN_01")
    assert [layer["depth"] for layer in layers] == ["0-5cm", "5-15cm", "15-30cm", "30-60cm"]
    top = layers[0]
    assert top["sand_pct"] == pytest.approx(80.5)
    assert top["ph"] == pytest.approx(6.1)
    assert top["bulk_density_g_cm3"] == pytest.approx(1.48)
    assert top["nitrogen_g_kg"] == pytest.approx(0.68)
    assert (top["top_cm"], top["bottom_cm"]) == (0, 5)
    assert top["is_mock"] is True and top["source"] == "soilgrids"


def test_soil_layer_with_a_missing_mean_stays_null():
    payload = soilgrids_payload(11.08, 108.12)
    payload["properties"]["layers"][0]["depths"][0]["values"]["mean"] = None
    assert rows("dim_soil_layer", payload, farm_id="X")[0]["sand_pct"] is None


def nasa(start=date(2025, 11, 1), end=date(2025, 11, 10)):
    return nasa_payload(11.08, 108.12, start, end, seed="BINHTHUAN_01")


def test_nasa_power_gives_one_row_per_day():
    days = rows("fact_weather_daily", nasa(), farm_id="BINHTHUAN_01")
    assert len(days) == 10
    first = days[0]
    assert first["date"] == "2025-11-01" and first["source"] == "nasa_power"
    assert first["tmin_c"] <= first["tmean_c"] <= first["tmax_c"]
    assert first["rs_mj_m2"] > 0 and first["is_mock"] is True


def test_nasa_fill_values_become_null_and_empty_days_are_dropped():
    payload = nasa()
    params = payload["properties"]["parameter"]
    params["RH2M"]["20251101"] = -999.0
    for series in params.values():
        series["20251110"] = -999.0
    days = {d["date"]: d for d in rows("fact_weather_daily", payload, farm_id="F")}
    assert days["2025-11-01"]["rh_pct"] is None and days["2025-11-01"]["tmax_c"] is not None
    assert "2025-11-10" not in days


def test_nasa_radiation_in_kwh_is_converted_and_unknown_units_fail():
    payload = nasa()
    mj = rows("fact_weather_daily", payload, farm_id="F")[0]["rs_mj_m2"]
    payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "kW-hr/m^2/day"
    assert rows("fact_weather_daily", payload, farm_id="F")[0]["rs_mj_m2"] == pytest.approx(mj * 3.6)
    payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "W/m^2"
    with pytest.raises(ValueError):
        rows("fact_weather_daily", payload, farm_id="F")


def test_field_operation_plan_rows():
    plan = {"season_id": FARM.season_id, "operations": generate_field_operation_plan(FARM)}
    ops = rows("fact_field_operation", plan, farm_id=FARM.farm_id, season_id=FARM.season_id)
    assert len({op["operation_id"] for op in ops}) == len(ops)
    assert {op["status"] for op in ops} == {"planned"}
    assert {op["op_type"] for op in ops} == {"sowing", "fertilizer", "weed_control", "harvest"}
    harvest = next(op for op in ops if op["op_type"] == "harvest")
    assert harvest["das"] == 75 and harvest["planned_date"] == "2026-01-29"
