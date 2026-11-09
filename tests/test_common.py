from datetime import date

import pytest

from common.crop import crop_coefficient, growth_stage, in_sowing_window, season_length
from common.et0 import et0_penman_monteith, extraterrestrial_radiation
from common.farm import FarmProfile, ring_area_perimeter
from common.soil import TEXTURES, SoilHydraulics, texture_from_fractions
from common.config import get_minio_settings, require_env
from common.minio_utils import build_key


def test_extraterrestrial_radiation_matches_fao56_example_8():
    # Lat 20 S, 3 September -> Ra = 32.2 MJ m-2 day-1
    assert extraterrestrial_radiation(-20, 246) == pytest.approx(32.2, abs=0.1)


def test_et0_matches_fao56_example_18():
    # Brussels, 6 July: ET0 = 3.9 mm/day (example uses RHmax/RHmin, we use RH mean)
    et0 = et0_penman_monteith(12.3, 21.5, 73.5, 2.078, 22.07, 50.8, 187, elevation_m=100)
    assert et0 == pytest.approx(3.9, abs=0.2)


def test_kc_curve_follows_fao56_shape():
    assert crop_coefficient(0) == 0.35
    assert crop_coefficient(14) == 0.35
    assert crop_coefficient(15 + 12.5) == pytest.approx((0.35 + 1.10) / 2)
    assert crop_coefficient(45) == 1.10
    assert crop_coefficient(season_length() - 1) == pytest.approx(0.25, abs=0.1)
    assert crop_coefficient(-3) == crop_coefficient(season_length()) == 0.35  # fallow
    assert growth_stage(-1) is None
    assert growth_stage(20) == "development"
    assert growth_stage(season_length()) is None


def test_sowing_window():
    assert in_sowing_window(date(2026, 11, 15))
    assert in_sowing_window(date(2027, 2, 10))
    assert not in_sowing_window(date(2026, 10, 1))


@pytest.mark.parametrize("texture", sorted(TEXTURES))
def test_retention_curve_passes_through_fc_and_wp(texture):
    fc, wp, _, fc_kpa, _ = TEXTURES[texture]
    soil = SoilHydraulics(texture)
    assert soil.theta_at(fc_kpa) == pytest.approx(fc, abs=1e-4)
    assert soil.theta_at(1500) == pytest.approx(wp, abs=1e-4)
    assert soil.suction_kpa(fc) == pytest.approx(fc_kpa, rel=0.01)
    assert soil.suction_kpa(fc + 0.05) < fc_kpa < soil.suction_kpa(fc - 0.03)


def test_texture_from_soilgrids_fractions():
    assert texture_from_fractions(90, 3) == "sand"
    assert texture_from_fractions(80, 6) == "loamy_sand"
    assert texture_from_fractions(65, 12) == "sandy_loam"


def test_polygon_area_matches_requested_area():
    farm = FarmProfile(area_m2=5000)
    area, perimeter = ring_area_perimeter(farm.polygon(aspect=2))
    assert area == pytest.approx(5000, rel=0.01)
    assert perimeter == pytest.approx(2 * (50 + 100), rel=0.01)


def test_build_key_layout():
    key = build_key("/weather/", "data.json", dt="2026-01-02", farm_id="F1")
    assert key == "weather/dt=2026-01-02/farm_id=F1/data.json"


def test_minio_settings_have_no_default_credentials(monkeypatch):
    for name in ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="MINIO_ENDPOINT"):
        get_minio_settings()
    with pytest.raises(RuntimeError, match="MINIO_ACCESS_KEY"):
        require_env("MINIO_ACCESS_KEY")
    settings = get_minio_settings("http://m:9000", "a", "b")  # CLI flags win, bucket defaults
    assert (settings.endpoint, settings.bucket) == ("http://m:9000", "landing-zone")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "env-key")
    assert get_minio_settings("http://m:9000", secret_key="s").access_key == "env-key"


def test_upload_raw_json_builds_key_and_checks_bucket_once(monkeypatch):
    from botocore.exceptions import ClientError

    from common import minio_utils

    calls = []

    class FakeS3:
        def head_bucket(self, Bucket):
            calls.append(("head", Bucket))
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        def create_bucket(self, Bucket):
            calls.append(("create", Bucket))

        def put_object(self, Bucket, Key, Body, ContentType):
            calls.append(("put", Bucket, Key, Body))

    monkeypatch.setattr(minio_utils, "_checked_buckets", set())
    monkeypatch.setattr(minio_utils, "get_s3_client", lambda settings: FakeS3())
    settings = get_minio_settings("http://m:9000", "a", "b", "bkt")

    uri = minio_utils.upload_raw_json("user_config", {"vn": "mè"}, filename="c.json",
                                      settings=settings, farm_id="F1")
    minio_utils.upload_raw_json("user_config", {}, filename="d.json", settings=settings)
    assert uri == "s3://bkt/user_config/farm_id=F1/c.json"
    assert [c[0] for c in calls] == ["head", "create", "put", "put"]
    assert "mè".encode() in calls[2][3]  # ensure_ascii=False keeps Vietnamese readable
