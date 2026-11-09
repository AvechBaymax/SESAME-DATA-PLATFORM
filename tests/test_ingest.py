import pytest

from mock_iot import isric_ingest, nasa_ingest, owm_ingest

MINIO_ENV = {"MINIO_ENDPOINT": "http://m:9000", "MINIO_ACCESS_KEY": "a", "MINIO_SECRET_KEY": "b"}


@pytest.fixture
def captured(monkeypatch):
    for k, v in MINIO_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    calls = {}

    def fake_get_json(url, params=None, **kwargs):
        calls.update(url=url, params=params, kwargs=kwargs)
        return {"ok": True}

    def fake_upload(prefix, payload, filename=None, settings=None, **partitions):
        calls.update(prefix=prefix, filename=filename, partitions=partitions, bucket=settings.bucket)
        return "s3://x"

    for module in (isric_ingest, nasa_ingest, owm_ingest):
        monkeypatch.setattr(module, "get_json", fake_get_json)
        monkeypatch.setattr(module, "upload_raw_json", fake_upload)
    return calls


def test_defaults_match_the_mock_farm(captured):
    nasa_ingest.main([])
    assert captured["params"]["latitude"] == 11.08
    assert captured["params"]["longitude"] == 108.12
    assert captured["params"]["community"] == "ag"
    assert captured["partitions"]["farm_id"] == "BINHTHUAN_01"
    assert captured["prefix"] == "nasa_power"
    assert captured["filename"].startswith("data_") and captured["filename"].endswith("Z.json")


def test_isric_requests_root_zone_properties(captured):
    isric_ingest.main(["--farm-id", "F2", "--lat", "11.1", "--lon", "108.0"])
    assert captured["params"]["lat"] == 11.1
    assert {"phh2o", "nitrogen", "sand", "clay"} <= set(captured["params"]["property"])
    assert captured["filename"] == "soilgrids.json"
    assert captured["partitions"] == {"farm_id": "F2"}


def test_owm_requires_api_key(captured, monkeypatch):
    monkeypatch.delenv("OWM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OWM_API_KEY"):
        owm_ingest.main([])
    monkeypatch.setenv("OWM_API_KEY", "k")
    owm_ingest.main([])
    assert captured["params"]["appid"] == "k"
    assert set(captured["partitions"]) == {"farm_id", "dt"}
