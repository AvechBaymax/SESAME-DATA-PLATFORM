import pytest

from lakehouse.sources import SOURCES, parse_partitions
from lakehouse.tables import landing_uri, table


def test_table_names():
    assert table("bronze", "owm_raw") == "lakehouse.bronze.owm_raw"
    with pytest.raises(ValueError):
        table("platinum", "x")


def test_landing_uri(monkeypatch):
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    assert landing_uri("/weather/") == "s3a://landing-zone/weather"


def test_partitions_match_landing_keys_written_by_the_ingesters():
    # Keys as written by nasa_ingest, user_config_mock and mock_fao_db
    nasa = "s3a://landing-zone/nasa_power/farm_id=BINHTHUAN_01/ingest_dt=2026-11-09/data_x.json"
    plan = "s3a://landing-zone/field_operation/farm_id=BINHTHUAN_01/season_id=BINHTHUAN_01_P01_2025DX/plan_x.json"
    fao = "s3a://landing-zone/fao_reference/crop=sesame/fao_56_33_params.json"
    assert parse_partitions(nasa, SOURCES["nasa_power"].partitions) == {
        "farm_id": "BINHTHUAN_01", "ingest_dt": "2026-11-09"}
    assert parse_partitions(plan, SOURCES["field_operation"].partitions)["season_id"] == "BINHTHUAN_01_P01_2025DX"
    assert parse_partitions(fao, SOURCES["reference"].partitions) == {"crop": "sesame"}
    assert parse_partitions("s3a://landing-zone/soilgrids/soilgrids.json", ("farm_id",)) == {"farm_id": None}


def test_every_source_has_a_unique_table_and_constant_columns_cover_its_prefixes():
    tables = [s.table for s in SOURCES.values()]
    assert len(tables) == len(set(tables))
    for source in SOURCES.values():
        for by_prefix in source.columns.values():
            assert set(by_prefix) == set(source.prefixes)
