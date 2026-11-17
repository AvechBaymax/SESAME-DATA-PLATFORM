"""Agronomic rules for black sesame (Binh Thuan cultivation guide).

evaluate_rules(context) checks one season against config/sesame_rules.json and
returns one result per rule (R01-R11) plus an overall status. The context is:

  {"as_of": "YYYY-MM-DD", "planting_date": "YYYY-MM-DD",
   "weather": {"series": [{"date", "temperature_c", "rainfall_mm", "humidity_pct"}, ...]}
              or a scenario description (see build_weather_series),
   "soil": {"ph", "sand_g_kg", "clay_g_kg"}, "satellite": [{"date", "ndvi"}, ...]}

Thresholds come from the config file; the numbers below are only fallbacks.
"""
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sesame_rules.json"

STATUS_PRIORITY = {"VIOLATION": 4, "WARN": 3, "NO_DATA": 2, "OK": 1, "INFO": 0, "N/A": 0, "MANUAL": 0}
# Rules that raise the overall status. R08 (sowing window) counts until harvest.
SCORED_RULES = {"R01", "R02", "R03", "R04", "R05", "R06", "R07"}

STAGE_LABELS = {
    "pre_sowing": "trước gieo",
    "initial": "giai đoạn đầu",
    "development": "giai đoạn phát triển",
    "mid_season": "ra hoa",
    "late_season": "chín và thu hoạch",
    "harvest": "thu hoạch",
}


@lru_cache(maxsize=1)
def _read_config():
    if not CONFIG_PATH.exists():
        return {"rules": {}, "manual_checklist": [], "crop": {}}
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _rule_cfg(config, key):
    return config.get("rules", {}).get(key, {})


def _coerce_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---- weather series ------------------------------------------------------------------

def build_weather_series(weather, planting_date, as_of):
    """Daily series from planting_date to as_of for a scenario description.

    weather keys: daily_temp_c / daily_rain_mm / daily_humidity_pct (every day),
    and temp_c / rain_mm / humidity_pct, which replace the last `last_n_days`
    days (every day when last_n_days is null). rain_mm may also be a list of
    daily values from the planting date on (the last value repeats).
    """
    if planting_date is None or as_of is None:
        return []
    default_temp = _safe_number(weather.get("daily_temp_c", 27))
    default_rain = _safe_number(weather.get("daily_rain_mm", 0))
    default_humidity = _safe_number(weather.get("daily_humidity_pct", 75))
    last_n_days = weather.get("last_n_days")
    total_days = (as_of - planting_date).days
    first_override = 0 if last_n_days is None else max(0, total_days - int(last_n_days) + 1)

    def override(name, day_index, default):
        if name not in weather:
            return default
        value = weather[name]
        if isinstance(value, list):  # daily values from the planting date
            if not value:
                return default
            return _safe_number(value[min(day_index, len(value) - 1)])
        if day_index >= first_override and _safe_number(value) is not None:
            return _safe_number(value)
        return default

    series = []
    current = planting_date
    while current <= as_of:
        index = (current - planting_date).days
        temp = override("temp_c", index, default_temp)
        rain = override("rain_mm", index, default_rain)
        humidity = override("humidity_pct", index, default_humidity)
        series.append({
            "date": current.isoformat(),
            "temperature_c": round(temp, 1) if temp is not None else None,
            "rainfall_mm": round(rain, 1) if rain is not None else None,
            "humidity_pct": round(humidity, 1) if humidity is not None else None,
        })
        current += timedelta(days=1)
    return series


def normalize_weather_series(weather, planting_date, as_of):
    """A list of daily dicts from a series, a {"series": [...]} dict or a scenario description."""
    if isinstance(weather, list):
        return [dict(item) for item in weather if isinstance(item, dict)]
    if not isinstance(weather, dict):
        return []
    if isinstance(weather.get("series"), list):
        return [dict(item) for item in weather["series"] if isinstance(item, dict)]
    return build_weather_series(weather, planting_date, as_of)


def _rainfall(item):
    return _safe_number(item.get("rainfall_mm"))


def _dry_streak(series, dry_below_mm):
    """Consecutive dry days at the end of the series; a day without data ends the count."""
    streak = 0
    for item in reversed(series):
        rain = _rainfall(item)
        if rain is None or rain >= dry_below_mm:
            break
        streak += 1
    return streak


def _season_rain(series, season_days):
    """(total mm, projection for the whole season from the daily mean)."""
    total = sum(_rainfall(item) or 0.0 for item in series)
    return total, total / max(len(series), 1) * season_days


# ---- crop stage -------------------------------------------------------------------------

def _stage_for_das(das, config=None):
    if das is None:
        return None
    config = config or _read_config()
    crop = config.get("crop", {})
    if das < 0:
        return "pre_sowing"
    if das >= int(crop.get("harvest_das", crop.get("season_days", 75))):
        return "harvest"
    for stage_name, (start, end) in crop.get("stages_das", {}).items():
        if start <= das <= end:
            return stage_name
    return "harvest"


def _stage_label(stage):
    return STAGE_LABELS.get(stage, stage or "hiện tại")


# ---- results ----------------------------------------------------------------------------

def _rule_result(rule_id, label, status, value=None, unit="", window="", threshold=None,
                 threshold_source=None, doc_ref="", message="", extra=None):
    result = {
        "id": rule_id,
        "label": label,
        "status": status,
        "value": value,
        "unit": unit,
        "window": window,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "doc_ref": doc_ref,
        "message": message,
    }
    if extra:
        result.update(extra)
    return result


def _assumed_thresholds(config):
    """Config fields marked "assumed" (not from the guide), e.g. "R04_waterlogging.thresholds"."""
    outputs = []
    for rule_key, rule_cfg in config.get("rules", {}).items():
        sources = rule_cfg.get("sources", {})
        if isinstance(sources, dict):
            outputs.extend(f"{rule_key}.{field}" for field, source in sources.items() if source == "assumed")
    return sorted(outputs)


# ---- R01-R11 ----------------------------------------------------------------------------

def _evaluate_r01(weather_series, config):
    cfg = _rule_cfg(config, "R01_temperature")
    ok_low, ok_high = cfg.get("ok", [25, 30])
    violation_below = cfg.get("violation_below", 20)
    window_days = int(cfg.get("window_days", 3))
    window = f"trung bình {window_days} ngày gần nhất"

    def result(status, value, message, source="document"):
        threshold = {"ok": [ok_low, ok_high], "violation_below": violation_below,
                     "warning_above": cfg.get("warn_above", ok_high)}
        return _rule_result("R01", "Nhiệt độ không khí", status, value=value, unit="°C", window=window,
                            threshold=threshold, threshold_source=source,
                            doc_ref=cfg.get("doc_ref", "I.1, II.1"), message=message)

    values = [_safe_number(item.get("temperature_c")) for item in weather_series]
    recent = [v for v in values if v is not None][-window_days:]
    if not recent:
        return result("NO_DATA", None, "Thiếu dữ liệu nhiệt độ để đánh giá.")
    value = round(sum(recent) / len(recent), 1)
    if value < violation_below:
        return result("VIOLATION", value,
                      f"Nhiệt độ {value}°C thấp hơn ngưỡng {violation_below}°C, cây sinh trưởng chậm.")
    if value < ok_low:
        return result("WARN", value, f"Nhiệt độ {value}°C dưới mức tối ưu {ok_low}–{ok_high}°C.")
    if value <= ok_high:
        return result("OK", value, f"Nhiệt độ {value}°C nằm trong khoảng thích hợp {ok_low}–{ok_high}°C.")
    return result("WARN", value, f"Nhiệt độ {value}°C cao hơn mức tối ưu {ok_high}°C, cần theo dõi stress nhiệt.",
                  source="assumed")


def _evaluate_r02(weather_series, planting_date, as_of, config, stage=None):
    cfg = _rule_cfg(config, "R02_rainfall_season")
    season_min = float(cfg.get("season_min_mm", 500))
    season_max = float(cfg.get("season_max_mm", 600))
    min_das = int(cfg.get("min_das_for_projection", 15))
    season_days = int(config.get("crop", {}).get("season_days", 75))
    ok_months = set(_rule_cfg(config, "R08_sowing_window").get("ok_months", [11, 12, 2, 3]))
    dry_below = float(_rule_cfg(config, "R05_dry_spell_flowering").get("dry_day_mm_below", 1.0))
    dry_warn = int(_rule_cfg(config, "R05_dry_spell_flowering").get("warn_consecutive_days", 4))

    def result(status, message, value=None, extra=None):
        return _rule_result("R02", "Lượng mưa cả vụ", status, value=value, unit="mm",
                            window="từ ngày gieo đến hiện tại",
                            threshold={"season_min_mm": season_min, "season_max_mm": season_max},
                            threshold_source="document", doc_ref=cfg.get("doc_ref", "I.1, II.1"),
                            message=message, extra=extra)

    if not weather_series:
        return result("NO_DATA", "Thiếu dữ liệu mưa để ước tính cả vụ.")
    if planting_date is None or as_of is None:
        return result("NO_DATA", "Chưa có ngày gieo hoặc ngày hiện tại.")
    if len(weather_series) < 7:
        return result("NO_DATA", "Dữ liệu mưa quá ít để ước tính cả vụ.")
    if planting_date.month not in ok_months and stage not in {None, "harvest"}:
        return result("NO_DATA", "Gieo ngoài thời vụ khuyến nghị, ngưỡng mưa cả vụ chưa áp dụng được.")

    das = max((as_of - planting_date).days, 0)
    cumulative = round(sum(_rainfall(item) or 0.0 for item in weather_series), 1)
    if stage == "mid_season" and _dry_streak(weather_series, dry_below) >= dry_warn:
        return result("OK", "Mưa thấp trong giai đoạn ra hoa được đánh giá bằng R05; không cộng cảnh báo mùa vụ ở R02.",
                      value=cumulative)
    if das < min_das:
        return result("NO_DATA", f"Chưa đủ ngày để ước tính; cần ít nhất {min_das} ngày sau gieo.",
                      value=cumulative,
                      extra={"cumulative_mm": cumulative, "projected_season_mm": None, "progress_pct_of_min": None})

    # Mean daily rain over the days that have data, so a lagging source (ERA5-Land
    # is a few days behind) does not look like a dry season.
    projected = round(_season_rain(weather_series, season_days)[1], 1)
    if cumulative > season_max:
        status = "WARN"
        message = (f"Mưa tích lũy {cumulative:.1f} mm vượt {season_max:.0f} mm, có nguy cơ úng nước; "
                   f"dự kiến cả vụ {projected:.1f} mm.")
    elif projected < season_min:
        status = "WARN"
        message = (f"Mưa tích lũy {cumulative:.1f} mm, dự kiến cả vụ {projected:.1f} mm thấp hơn "
                   f"{season_min:.0f} mm, cần tưới bổ sung.")
    else:
        status = "OK"
        message = (f"Mưa tích lũy {cumulative:.1f} mm, dự kiến cả vụ {projected:.1f} mm, "
                   f"không thiếu so với mục tiêu {season_min:.0f}–{season_max:.0f} mm.")
    return result(status, message, value=cumulative,
                  extra={"cumulative_mm": cumulative, "projected_season_mm": projected,
                         "progress_pct_of_min": round(cumulative / season_min * 100, 1)})


def _evaluate_r03(weather_series, stage, config):
    cfg = _rule_cfg(config, "R03_heavy_rain_sensitive_stage")
    active = cfg.get("active_stages", ["mid_season", "late_season"])
    window_days = int(cfg.get("window_days", 3))
    warn_mm = float(cfg.get("warn_mm_per_day", 20))
    violation_mm = float(cfg.get("violation_mm_per_day", 50))
    season_cfg = _rule_cfg(config, "R02_rainfall_season")
    season_min = float(season_cfg.get("season_min_mm", 500))
    season_max = float(season_cfg.get("season_max_mm", 600))
    season_days = int(config.get("crop", {}).get("season_days", 75))
    dry_cfg = _rule_cfg(config, "R05_dry_spell_flowering")
    dry_below = float(dry_cfg.get("dry_day_mm_below", 1.0))
    dry_warn = int(dry_cfg.get("warn_consecutive_days", 4))

    def result(status, message, value=None):
        return _rule_result("R03", "Mưa lớn ở giai đoạn nhạy cảm", status, value=value, unit="mm/ngày",
                            window=f"{window_days} ngày gần nhất",
                            threshold={"warn": warn_mm, "violation": violation_mm}, threshold_source="assumed",
                            doc_ref=cfg.get("doc_ref", "I.1, I.10"), message=message)

    if not weather_series:
        return result("NO_DATA", "Thiếu dữ liệu mưa để kiểm tra mưa lớn.")
    if stage not in active:
        return result("N/A", f"Không áp dụng ở {_stage_label(stage)}.")

    recent = [r for r in (_rainfall(item) for item in weather_series[-window_days:]) if r is not None]
    peak = max(recent) if recent else 0.0
    seasonal_anomaly = False
    if len(weather_series) >= 15:
        total, projection = _season_rain(weather_series, season_days)
        seasonal_anomaly = total > season_max or projection < season_min
    label = _stage_label(stage)
    if peak >= violation_mm:
        return result("VIOLATION", f"Mưa ngày lớn nhất {peak:.1f} mm/ngày ở {label}, vượt ngưỡng "
                                   f"{violation_mm:.0f} mm/ngày, có nguy cơ thất thu.", round(peak, 1))
    if peak >= warn_mm or _dry_streak(weather_series, dry_below) >= dry_warn or seasonal_anomaly:
        return result("WARN", f"Mưa ngày lớn nhất {peak:.1f} mm/ngày ở {label}: trên ngưỡng cảnh báo "
                              f"{warn_mm:.0f} mm/ngày, hoặc có chuỗi ngày khô kéo dài / lượng mưa mùa vụ bất thường.",
                      round(peak, 1))
    return result("OK", f"Mưa ngày lớn nhất {peak:.1f} mm/ngày ở {label}, trong giới hạn chấp nhận.", round(peak, 1))


def _evaluate_r04(weather_series, config, stage=None):
    cfg = _rule_cfg(config, "R04_waterlogging")
    window_days = int(cfg.get("window_days", 3))
    warn_mm = float(cfg.get("warn_mm_sum", 60))
    violation_mm = float(cfg.get("violation_mm_sum", 100))
    heavy_rain_stages = _rule_cfg(config, "R03_heavy_rain_sensitive_stage").get(
        "active_stages", ["mid_season", "late_season"])

    def result(status, message, value=None):
        return _rule_result("R04", "Nguy cơ úng", status, value=value, unit="mm",
                            window=f"tổng {window_days} ngày gần nhất",
                            threshold={"warn": warn_mm, "violation": violation_mm}, threshold_source="assumed",
                            doc_ref=cfg.get("doc_ref", "I.4, I.7, I.9"), message=message)

    if not weather_series:
        return result("NO_DATA", f"Thiếu dữ liệu mưa trong {window_days} ngày gần nhất.")
    total = round(sum(_rainfall(item) or 0.0 for item in weather_series[-window_days:]), 1)
    if stage in heavy_rain_stages:
        # Heavy rain in these stages is scored once, by R03, so it does not count twice.
        return result("OK", f"Tổng mưa {window_days} ngày gần nhất là {total:.1f} mm; ở {_stage_label(stage)} "
                            "mưa lớn được đánh giá bằng R03.", total)
    if total >= violation_mm:
        return result("VIOLATION", f"Tổng mưa {window_days} ngày gần nhất là {total:.1f} mm, vượt ngưỡng úng "
                                   f"{violation_mm:.0f} mm.", total)
    if total >= warn_mm:
        return result("WARN", f"Tổng mưa {window_days} ngày gần nhất là {total:.1f} mm, ở mức cảnh báo úng "
                              f"{warn_mm:.0f} mm.", total)
    return result("OK", f"Tổng mưa {window_days} ngày gần nhất là {total:.1f} mm, chưa ở mức úng nước.", total)


def _evaluate_r05(weather_series, stage, config):
    cfg = _rule_cfg(config, "R05_dry_spell_flowering")
    active = cfg.get("active_stages", ["mid_season"])
    dry_below = float(cfg.get("dry_day_mm_below", 1.0))
    warn_days = int(cfg.get("warn_consecutive_days", 4))
    violation_days = int(cfg.get("violation_consecutive_days", 7))
    heavy_warn_mm = float(_rule_cfg(config, "R03_heavy_rain_sensitive_stage").get("warn_mm_per_day", 20))
    season_min = float(_rule_cfg(config, "R02_rainfall_season").get("season_min_mm", 500))
    season_days = int(config.get("crop", {}).get("season_days", 75))

    def result(status, message, value=None):
        return _rule_result("R05", "Hạn khi ra hoa", status, value=value, unit="ngày",
                            window="đếm ngày khô liên tiếp",
                            threshold={"warn": warn_days, "violation": violation_days}, threshold_source="assumed",
                            doc_ref=cfg.get("doc_ref", "I.7, II.7"), message=message)

    if not weather_series:
        return result("NO_DATA", "Thiếu dữ liệu mưa để đánh giá hạn hán ra hoa.")
    if stage not in active:
        return result("N/A", "Không áp dụng ở giai đoạn hiện tại.")
    if len(weather_series) < 7:
        return result("N/A", "Dữ liệu quá ít để xác định chuỗi khô trong giai đoạn ra hoa.", 0)

    streak = _dry_streak(weather_series, dry_below)
    seasonal_total, seasonal_projection = _season_rain(weather_series, season_days)
    recent_max = max((_rainfall(item) or 0.0 for item in weather_series[-3:]), default=0.0)
    no_irrigation = "dữ liệu không có thông tin tưới, cần kiểm tra tưới thực tế."
    if streak >= violation_days:
        return result("VIOLATION", f"Có {streak} ngày khô liên tiếp trong giai đoạn ra hoa, vượt ngưỡng "
                                   f"{violation_days} ngày; {no_irrigation}", streak)
    if streak >= warn_days:
        return result("WARN", f"Có {streak} ngày khô liên tiếp trong giai đoạn ra hoa, ở mức cảnh báo "
                              f"{warn_days} ngày; {no_irrigation}", streak)
    if recent_max >= heavy_warn_mm:
        return result("WARN", f"Mưa gần đây {recent_max:.1f} mm/ngày ở giai đoạn ra hoa, cần theo dõi "
                              "độ ẩm đất cùng R03.", round(recent_max, 1))
    if seasonal_total < season_min and seasonal_projection < season_min:
        return result("WARN", f"Lượng mưa tích lũy {seasonal_total:.1f} mm và dự báo cả vụ "
                              f"{seasonal_projection:.1f} mm thấp hơn mục tiêu {season_min:.0f} mm "
                              "trong giai đoạn ra hoa.", round(seasonal_total, 1))
    return result("N/A", "Không có chuỗi ngày khô đủ dài để xác định hạn khi ra hoa; theo dõi theo R02/R03.",
                  round(seasonal_total, 1))


def _evaluate_r06(soil, config):
    cfg = _rule_cfg(config, "R06_soil_ph")
    low, high = cfg.get("ok", [5.5, 7.5])
    margin = float(cfg.get("edge_margin", 0.2))

    def result(status, message, value=None):
        return _rule_result("R06", "pH đất", status, value=value, unit="pH", window="mẫu đất 0–5 cm",
                            threshold={"ok": [low, high], "edge_margin": margin}, threshold_source="document",
                            doc_ref=cfg.get("doc_ref", "I.2, II.2"), message=message)

    ph = _safe_number(soil.get("ph")) if isinstance(soil, dict) else None
    if ph is None:
        return result("NO_DATA", "Thiếu dữ liệu pH đất.")
    ph = round(ph, 1)
    if ph < low or ph > high:
        return result("VIOLATION", f"pH đất {ph:.1f} nằm ngoài khoảng thích hợp {low}–{high}.", ph)
    if low < ph < low + margin or high - margin < ph < high:
        return result("WARN", f"pH đất {ph:.1f} ở mép giới hạn {low}–{high}, cần theo dõi.", ph)
    return result("OK", f"pH đất {ph:.1f} nằm trong khoảng thích hợp {low}–{high}.", ph)


def classify_texture(sand_pct, clay_pct, silt_pct):
    """USDA soil texture class (all 12 classes of the texture triangle)."""
    if silt_pct + 1.5 * clay_pct < 15:
        return "sand"
    if silt_pct + 2 * clay_pct < 30:
        return "loamy sand"
    if clay_pct >= 40:
        if silt_pct >= 40:
            return "silty clay"
        if sand_pct <= 45:
            return "clay"
    if clay_pct >= 35 and sand_pct > 45:
        return "sandy clay"
    if 27 <= clay_pct < 40:
        if sand_pct <= 20:
            return "silty clay loam"
        if sand_pct <= 45:
            return "clay loam"
    if 20 <= clay_pct < 35 and silt_pct < 28 and sand_pct > 45:
        return "sandy clay loam"
    if 7 <= clay_pct < 27 and 28 <= silt_pct < 50 and sand_pct <= 52:
        return "loam"
    if silt_pct >= 80 and clay_pct < 12:
        return "silt"
    if silt_pct >= 50:
        return "silt loam"
    return "sandy loam"


def _evaluate_r07(soil, config):
    cfg = _rule_cfg(config, "R07_soil_texture")
    ok_classes = cfg.get("ok_classes", ["sandy loam", "loamy sand", "loam", "sand"])
    # Part II of the guide: alluvial soils also suit sesame when they drain (no waterlogging).
    drained_classes = cfg.get("ok_if_drained_classes", [])

    def result(status, message, value=None):
        return _rule_result("R07", "Loại đất", status, value=value, unit="", window="USDA texture triangle",
                            threshold={"ok_classes": ok_classes, "ok_if_drained_classes": drained_classes},
                            threshold_source="derived", doc_ref=cfg.get("doc_ref", "I.2, II.2"), message=message)

    sand = _safe_number(soil.get("sand_g_kg")) if isinstance(soil, dict) else None
    clay = _safe_number(soil.get("clay_g_kg")) if isinstance(soil, dict) else None
    if sand is None or clay is None:
        return result("NO_DATA", "Thiếu dữ liệu thành phần cát/sét để phân loại đất.")
    sand_pct, clay_pct = sand / 10.0, clay / 10.0
    silt_pct = 100.0 - sand_pct - clay_pct
    if silt_pct < 0:
        return result("NO_DATA", "Tỷ lệ cát + sét vượt 100 %, dữ liệu đất không hợp lệ.")
    label = classify_texture(sand_pct, clay_pct, silt_pct)
    if label in ok_classes:
        return result("OK", f"Đất được phân loại là {label}, thuộc nhóm đất thịt pha cát / cát pha / đất cát "
                            "phù hợp theo khuyến cáo.", label)
    if label in drained_classes:
        return result("OK", f"Đất được phân loại là {label} (kiểu đất phù sa); phù hợp nếu không phèn mặn và "
                            "thoát nước tốt. Vùng khó thoát nước nên lên luống cao 15–20 cm.", label)
    return result("WARN", f"Đất được phân loại là {label}, đất sét nặng dễ úng; mè rất sợ úng. Cần lên luống "
                          "cao 15–20 cm, rãnh 20–30 cm và kiểm tra thoát nước thực tế.", label)


def _evaluate_r08(planting_date, config):
    cfg = _rule_cfg(config, "R08_sowing_window")
    ok_months = cfg.get("ok_months", [11, 12, 2, 3])

    def result(status, message, value=None):
        return _rule_result("R08", "Thời vụ gieo", status, value=value, unit="tháng", window="tháng gieo",
                            threshold={"ok_months": ok_months}, threshold_source="document",
                            doc_ref=cfg.get("doc_ref", "I.3, II.3"), message=message)

    if planting_date is None:
        return result("NO_DATA", "Không có ngày gieo để kiểm tra thời vụ.")
    month = planting_date.month
    if month in ok_months:
        return result("OK", f"Tháng gieo {month} thuộc hai vụ khuyến cáo ở Bình Thuận.", month)
    return result("WARN", "Ngoài hai vụ khuyến cáo ở Bình Thuận (Đông xuân 11–12, Xuân hè 2–3). Mục II.3 cho "
                          "phép gieo quanh năm nếu tránh úng mùa mưa và tưới mùa khô.", month)


def _evaluate_r09(as_of, planting_date, config):
    cfg = _rule_cfg(config, "R09_care_schedule")
    upcoming_before = int(cfg.get("upcoming_days_before", 3))
    due_after = int(cfg.get("due_days_after", 2))

    def result(message, tasks, manual):
        return _rule_result("R09", "Lịch chăm sóc", "INFO", window="lịch canh tác",
                            doc_ref=cfg.get("doc_ref", "I.6, II.6"), message=message,
                            extra={"tasks": tasks, "manual_tasks": manual})

    if as_of is None or planting_date is None:
        return result("Thiếu ngày gieo hoặc ngày hiện tại để tính lịch chăm sóc.", [], [])
    tasks = []
    for task in cfg.get("tasks", []):
        das = int(task.get("das", 0))
        target_date = planting_date + timedelta(days=das)
        days_to_target = (target_date - as_of).days
        if days_to_target > upcoming_before:
            state = "FUTURE"
        elif days_to_target >= 1:
            state = "UPCOMING"
        elif days_to_target >= -due_after:
            state = "DUE"
        else:
            state = "PAST"
        tasks.append({"id": task.get("id"), "label": task.get("label"), "das": das,
                      "target_date": target_date.isoformat(), "state": state,
                      "days_until_target": days_to_target, "source": task.get("source")})
    manual = [{"id": item.get("id"), "label": item.get("label"), "trigger": item.get("trigger"),
               "state": "MANUAL", "source": item.get("source")} for item in cfg.get("manual_tasks", [])]
    return result("Lịch chăm sóc tính theo ngày gieo; xác nhận ngày thực hiện ngoài đồng.", tasks, manual)


def _evaluate_r10(satellite):
    observations = [item for item in satellite or [] if isinstance(item, dict)
                    and _safe_number(item.get("ndvi")) is not None]
    if not observations:
        return _rule_result("R10", "NDVI", "NO_DATA", unit="NDVI", window="quan sát gần nhất",
                            message="Không có dữ liệu NDVI để đánh giá.",
                            extra={"latest_ndvi": None, "date": None, "delta_vs_previous": None})
    latest = observations[-1]
    ndvi = _safe_number(latest["ndvi"])
    delta = round(ndvi - _safe_number(observations[-2]["ndvi"]), 3) if len(observations) >= 2 else None
    return _rule_result("R10", "NDVI", "INFO", value=ndvi, unit="NDVI", window="quan sát gần nhất",
                        message="NDVI gần nhất cho thấy tín hiệu thực vật của ruộng.",
                        extra={"latest_ndvi": ndvi, "date": latest.get("date"), "delta_vs_previous": delta})


def _evaluate_r11(weather_series):
    latest = next((item for item in reversed(weather_series)
                   if _safe_number(item.get("humidity_pct")) is not None), None)
    if latest is None:
        return _rule_result("R11", "Độ ẩm không khí", "NO_DATA", unit="%", window="ngày gần nhất",
                            message="Thiếu dữ liệu độ ẩm không khí.",
                            extra={"latest_humidity_pct": None, "date": None})
    value = round(_safe_number(latest["humidity_pct"]), 1)
    return _rule_result("R11", "Độ ẩm không khí", "INFO", value=value, unit="%", window="ngày gần nhất",
                        message=f"Độ ẩm không khí gần nhất là {value}%.",
                        extra={"latest_humidity_pct": value, "date": latest.get("date")})


def evaluate_rules(context):
    context = context or {}
    config = _read_config()
    as_of = _coerce_date(context.get("as_of")) or date.today()
    planting_date = _coerce_date(context.get("planting_date"))
    weather_series = normalize_weather_series(context.get("weather") or {}, planting_date, as_of)
    soil = context.get("soil")
    das = (as_of - planting_date).days if planting_date else None
    stage = _stage_for_das(das, config)
    rules = [
        _evaluate_r01(weather_series, config),
        _evaluate_r02(weather_series, planting_date, as_of, config, stage=stage),
        _evaluate_r03(weather_series, stage, config),
        _evaluate_r04(weather_series, config, stage),
        _evaluate_r05(weather_series, stage, config),
        _evaluate_r06(soil, config),
        _evaluate_r07(soil, config),
        _evaluate_r08(planting_date, config),
        _evaluate_r09(as_of, planting_date, config),
        _evaluate_r10(context.get("satellite")),
        _evaluate_r11(weather_series),
    ]
    overall_status = "OK"
    for rule in rules:
        scored = rule["id"] in SCORED_RULES or (rule["id"] == "R08" and stage != "harvest")
        status = rule["status"]
        if scored and status in {"VIOLATION", "WARN"} and STATUS_PRIORITY[status] > STATUS_PRIORITY[overall_status]:
            overall_status = status
    return {
        "as_of": as_of.isoformat(),
        "planting_date": planting_date.isoformat() if planting_date else None,
        "das": das,
        "stage": stage,
        "stage_label": _stage_label(stage) if stage else None,
        "overall_status": overall_status,
        "simulated": bool(context.get("simulated", False)),
        "results": rules,
        "manual_checklist": config.get("manual_checklist", []),
        "assumed_thresholds": _assumed_thresholds(config),
    }
