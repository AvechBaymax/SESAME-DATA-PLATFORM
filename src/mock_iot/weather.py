"""Weather drivers for the IoT simulator.

Daily values come either from a Binh Thuan climatology (deterministic per date)
or from NASA POWER, so simulated sensors agree with what the API ingesters land.
The daily values are then spread over the day (temperature curve, solar
geometry, afternoon showers) to produce instantaneous readings.
"""
import calendar
import math
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from common.et0 import saturation_vapour_pressure, solar_declination
from common.http_utils import get_json

# Approximate monthly climatology for Phan Thiet (Jan..Dec); good enough for mocks.
CLIMATOLOGY = {
    "tmax_c": [30.0, 30.6, 31.6, 32.6, 32.9, 32.0, 31.2, 31.1, 30.9, 30.6, 30.7, 30.1],
    "tmin_c": [21.0, 21.4, 22.9, 24.6, 25.4, 25.0, 24.6, 24.5, 24.2, 23.8, 23.0, 21.9],
    "rain_mm": [1, 1, 3, 25, 150, 140, 170, 165, 175, 185, 70, 15],
    "wet_days": [0.5, 0.3, 0.8, 4, 12, 14, 15, 15, 16, 16, 8, 2.5],
    "rh_pct": [72, 73, 75, 77, 80, 82, 83, 83, 84, 84, 80, 75],
    "wind_2m_m_s": [4.5, 4.8, 4.2, 3.2, 2.5, 2.8, 3.0, 3.0, 2.5, 2.2, 2.8, 4.0],
    "rs_mj_m2": [19.5, 21.5, 23.0, 22.5, 19.5, 18.0, 17.5, 17.5, 17.0, 16.5, 17.0, 17.5],
}

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
# community=AG returns radiation in MJ/m2/day (community=RE uses kWh/m2/day).
NASA_PARAMS = {
    "T2M_MIN": "tmin_c",
    "T2M_MAX": "tmax_c",
    "RH2M": "rh_pct",
    "WS2M": "wind_2m_m_s",
    "ALLSKY_SFC_SW_DWN": "rs_mj_m2",
    "PRECTOTCORR": "precip_mm",
}
NASA_FILL_VALUE = -999.0


@dataclass(frozen=True)
class DailyWeather:
    day: date
    tmin_c: float
    tmax_c: float
    rh_pct: float
    wind_2m_m_s: float
    rs_mj_m2: float
    precip_mm: float
    source: str


class ClimatologyWeather:
    def __init__(self, seed="sesame"):
        self.seed = seed

    @lru_cache(maxsize=512)
    def daily(self, day):
        rng = random.Random(f"{self.seed}:{day.isoformat()}")
        m = day.month - 1
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        wet_prob = CLIMATOLOGY["wet_days"][m] / days_in_month
        precip = 0.0
        if rng.random() < wet_prob:
            mean_wet = CLIMATOLOGY["rain_mm"][m] / CLIMATOLOGY["wet_days"][m]
            precip = round(rng.gammavariate(0.8, mean_wet / 0.8), 1)
        wet = precip > 0
        tmax = CLIMATOLOGY["tmax_c"][m] + rng.gauss(0, 0.8) - (1.5 if wet else 0)
        tmin = CLIMATOLOGY["tmin_c"][m] + rng.gauss(0, 0.6)
        return DailyWeather(
            day=day,
            tmin_c=round(min(tmin, tmax - 4), 1),
            tmax_c=round(tmax, 1),
            rh_pct=round(min(CLIMATOLOGY["rh_pct"][m] + rng.gauss(0, 3) + (8 if wet else 0), 98), 1),
            wind_2m_m_s=round(max(CLIMATOLOGY["wind_2m_m_s"][m] * rng.uniform(0.7, 1.3), 0.3), 2),
            rs_mj_m2=round(CLIMATOLOGY["rs_mj_m2"][m] * rng.uniform(0.85, 1.05) * (0.7 if wet else 1), 2),
            precip_mm=precip,
            source="climatology",
        )


class NasaPowerWeather:
    """Daily drivers from NASA POWER; missing days/fields fall back to climatology.

    NASA POWER lags a few days behind real time and marks gaps with -999.
    """

    def __init__(self, lat, lon, start, end, fallback=None, fetch=get_json):
        self.fallback = fallback or ClimatologyWeather()
        self.records = {}
        payload = fetch(NASA_POWER_URL, params={
            "latitude": lat,
            "longitude": lon,
            "parameters": ",".join(NASA_PARAMS),
            "community": "AG",
            "start": f"{start:%Y%m%d}",
            "end": f"{end:%Y%m%d}",
            "format": "JSON",
        }, max_retries=2, timeout=120)  # the mock falls back to climatology, so don't wait long
        self.records = parse_nasa_power(payload)

    def daily(self, day):
        base = self.fallback.daily(day)
        values = self.records.get(day)
        if not values:
            return base
        merged = replace(base, **values, source="nasa_power")
        if len(values) < len(NASA_PARAMS):
            merged = replace(merged, source="nasa_power+climatology")
        return merged


def parse_nasa_power(payload):
    """{date: {field: value}} from a NASA POWER daily point response, dropping fill values."""
    parameters = payload.get("properties", {}).get("parameter", {})
    fill = payload.get("header", {}).get("fill_value", NASA_FILL_VALUE)
    records = {}
    for nasa_name, field_name in NASA_PARAMS.items():
        for ymd, value in parameters.get(nasa_name, {}).items():
            if value is None or value == fill:
                continue
            day = datetime.strptime(ymd, "%Y%m%d").date()
            records.setdefault(day, {})[field_name] = float(value)
    return records


@dataclass(frozen=True)
class RainEvent:
    start: datetime
    end: datetime
    intensity_mm_h: float


class DiurnalWeather:
    """Instantaneous weather at a point, built from daily drivers.

    Times are UTC; diurnal shapes use local solar time (UTC + lon/15 h).
    """

    def __init__(self, daily_source, lat, lon, seed="sesame"):
        self.daily_source = daily_source
        self.lat = lat
        self.lon = lon
        self.seed = seed
        self.solar_offset = timedelta(hours=lon / 15)

    def solar_time(self, t):
        return t + self.solar_offset

    def solar_day(self, t):
        return self.solar_time(t).date()

    def daily(self, day):
        return self.daily_source.daily(day)

    # ---- temperature / humidity / wind -------------------------------------------------
    def air_temp_c(self, t):
        local = self.solar_time(t)
        d = self.daily(local.date())
        s = local.hour + local.minute / 60 + local.second / 3600
        amp = d.tmax_c - d.tmin_c
        if 6 <= s < 14:  # sunrise minimum -> 14h maximum
            temp = d.tmin_c + amp * (1 - math.cos(math.pi * (s - 6) / 8)) / 2
        else:
            since_max = (s - 14) % 24
            temp = d.tmax_c - amp * (1 - math.cos(math.pi * since_max / 16)) / 2
        if self.is_raining(t):
            temp -= 2.0
        return temp

    def dewpoint_c(self, day):
        d = self.daily(day)
        tmean = (d.tmin_c + d.tmax_c) / 2
        ea = saturation_vapour_pressure(tmean) * d.rh_pct / 100
        ln = math.log(ea / 0.6108)
        return min(237.3 * ln / (17.27 - ln), d.tmin_c)

    def rh_pct(self, t):
        if self.is_raining(t):
            return 97.0
        td = self.dewpoint_c(self.solar_day(t))
        rh = 100 * saturation_vapour_pressure(td) / saturation_vapour_pressure(self.air_temp_c(t))
        return min(rh, 100.0)

    def wind_m_s(self, t):
        local = self.solar_time(t)
        s = local.hour + local.minute / 60
        mean = self.daily(local.date()).wind_2m_m_s
        # Calm nights, windier afternoons.
        return max(mean * (1 + 0.45 * math.sin(math.pi * (s - 8) / 12)), 0.1)

    # ---- solar radiation ---------------------------------------------------------------
    def _sin_elevation(self, day, solar_hour):
        phi = math.radians(self.lat)
        delta = solar_declination(day.timetuple().tm_yday)
        omega = math.pi * (solar_hour - 12) / 12
        return math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(omega)

    @lru_cache(maxsize=512)
    def _sin_elevation_integral_s(self, day):
        step_h = 0.1
        return sum(max(self._sin_elevation(day, i * step_h), 0.0)
                   for i in range(int(24 / step_h))) * step_h * 3600

    def solar_w_m2(self, t):
        local = self.solar_time(t)
        day = local.date()
        s = local.hour + local.minute / 60 + local.second / 3600
        sin_h = max(self._sin_elevation(day, s), 0.0)
        if sin_h == 0:
            return 0.0
        rs_j = self.daily(day).rs_mj_m2 * 1e6
        value = rs_j * sin_h / self._sin_elevation_integral_s(day)
        return value * (0.3 if self.is_raining(t) else 1.0)

    # ---- rain --------------------------------------------------------------------------
    @lru_cache(maxsize=512)
    def rain_events(self, day):
        """Split the daily total into 1-2 afternoon/evening showers (UTC times)."""
        total = self.daily(day).precip_mm
        if total <= 0:
            return ()
        rng = random.Random(f"{self.seed}:rain:{day.isoformat()}")
        n = 1 if total < 10 else 2
        shares = [1.0] if n == 1 else [w := rng.uniform(0.3, 0.7), 1 - w]
        midnight_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - self.solar_offset
        events = []
        for share in shares:
            start_h = rng.uniform(13, 21)
            duration_h = rng.uniform(0.5, 2.5)
            start = midnight_utc + timedelta(hours=start_h)
            events.append(RainEvent(start, start + timedelta(hours=duration_h),
                                    total * share / duration_h))
        return tuple(events)

    def _events_near(self, t0, t1):
        day = self.solar_day(t0)
        days = {day - timedelta(days=1), day, self.solar_day(t1)}
        for d in sorted(days):
            yield from self.rain_events(d)

    def rain_mm(self, t0, t1):
        """Rain accumulated in [t0, t1)."""
        total = 0.0
        for ev in self._events_near(t0, t1):
            overlap = (min(t1, ev.end) - max(t0, ev.start)).total_seconds()
            if overlap > 0:
                total += ev.intensity_mm_h * overlap / 3600
        return total

    def is_raining(self, t):
        return any(ev.start <= t < ev.end for ev in self._events_near(t, t))
