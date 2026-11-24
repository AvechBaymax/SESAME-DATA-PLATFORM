"""Alert rules: what to warn the grower about, from the Gold daily balance and Silver.

Plain Python (no pyspark import). jobs/silver_to_gold.py evaluates every rule per
season and day, then merges consecutive days of the same rule into one alert
(an episode with a start, an end and its worst value), so a 10-day drought is
one alert rather than ten.

Rules follow the grower's guide (sesame must not lack water at flowering, is
sensitive to waterlogging, and heavy rain at flowering or harvest hurts it) plus
device health. Numbers the guide does not give are marked as assumptions.
"""
from dataclasses import dataclass

SEVERITIES = ("info", "warning", "critical")   # ascending


@dataclass(frozen=True)
class DailyRule:
    """A rule checked on each feat_season_daily row (alias f)."""
    name: str
    severity: str
    condition: str     # Spark SQL on f
    value: str         # Spark SQL: the measured value
    threshold: str     # Spark SQL: the limit it crossed
    unit: str
    worst: str         # "max" or "min": which daily value an episode reports
    message: str       # Vietnamese text; {value}, {threshold}, {days} are filled in
    decimals: int = 0  # digits shown for {value}


DAILY_RULES = (
    DailyRule("drought_flowering", "critical",
              "f.stage = 'mid_season' AND f.ks < 1",
              "f.depletion_start_pct_taw", "100 * f.raw_mm / f.taw_mm", "% TAW", "max",
              "Thiếu nước lúc ra hoa: độ thiếu hụt vùng rễ tới {value} % TAW (ngưỡng {threshold} %), "
              "kéo dài {days} ngày. Cần tưới ngay."),
    DailyRule("water_stress", "warning",
              "f.stage <> 'mid_season' AND f.ks < 1",
              "f.depletion_start_pct_taw", "100 * f.raw_mm / f.taw_mm", "% TAW", "max",
              "Cây thiếu nước: độ thiếu hụt vùng rễ tới {value} % TAW (ngưỡng {threshold} %), {days} ngày."),
    DailyRule("waterlogging", "critical",
              "f.soil_oxygen_min_pct < 10",
              "f.soil_oxygen_min_pct", "10", "% O2", "min",
              "Ngập úng: oxy trong đất xuống {value} % (dưới {threshold} %), {days} ngày. "
              "Mè rất sợ ngập: khơi rãnh thoát nước.", decimals=1),
    DailyRule("heavy_rain_flowering", "warning",
              "f.stage = 'mid_season' AND f.precip_mm >= 30",   # assumption: 30 mm/day counts as heavy
              "f.precip_mm", "30", "mm/ngày", "max",
              "Mưa lớn lúc ra hoa: {value} mm/ngày (ngưỡng {threshold} mm), {days} ngày. "
              "Nguy cơ rụng hoa và ngập úng."),
    DailyRule("heat_stress", "warning",
              "f.tmax_c >= 35",                                  # assumption (guide: optimum 25-30 C)
              "f.tmax_c", "35", "°C", "max",
              "Nắng nóng: nhiệt độ tối đa {value} °C (ngưỡng {threshold} °C), {days} ngày."),
    DailyRule("low_sensor_coverage", "warning",
              "f.coverage_pct < 50",
              "f.coverage_pct", "50", "% bản ghi", "min",
              "Cảm biến của thửa gửi thiếu dữ liệu: chỉ {value} % bản ghi (ngưỡng {threshold} %), "
              "{days} ngày. Kiểm tra nút cảm biến, pin, sóng."),
)

# Rules that are not on feat_season_daily rows; built in the job, listed here for the catalogue.
OTHER_RULES = {
    "rain_harvest_window": ("warning", "mm/ngày", "Mưa {value} mm/ngày trong khung thu hoạch, phơi "
                            "(ngưỡng {threshold} mm), {days} ngày. Hạt dễ ẩm, mốc: che phủ bó mè."),
    "operation_due": ("info", "", "Đến hạn {op}: {details}."),
    "operation_overdue": ("warning", "ngày", "Quá hạn {op} {value} ngày mà chưa ghi nhận thực hiện."),
    "battery_low": ("warning", "%", "Pin cảm biến {device} còn {value} % (ngưỡng {threshold} %), {days} ngày."),
}
HARVEST_RAIN_MM = 10        # assumption: 10 mm/day wets drying bundles
BATTERY_LOW_PCT = 20
OVERDUE_DAYS = 3            # a planned operation not logged 3 days after its date
OPERATION_NAMES = {"fertilizer": "bón phân", "weed_control": "làm cỏ", "sowing": "gieo",
                   "harvest": "thu hoạch", "spraying": "phun thuốc", "irrigation": "tưới"}
FERTILIZER_APPLICATIONS = {"basal": "bón lót", "topdress": "bón thúc"}
FERTILIZER_PRODUCTS = {"urea": "urê", "kcl": "KCl", "super_lan": "super lân", "lime": "vôi",
                       "organic_microbial": "phân hữu cơ vi sinh"}
HARVEST_SIGN = "khi 2/3 số quả và lá đã chuyển vàng"   # the grower's guide


# Rules where a lower value is worse; an episode reports their minimum, the others their maximum.
LOWER_IS_WORSE = tuple(r.name for r in DAILY_RULES if r.worst == "min") + ("battery_low",)


def all_rule_names():
    return [r.name for r in DAILY_RULES] + list(OTHER_RULES)

