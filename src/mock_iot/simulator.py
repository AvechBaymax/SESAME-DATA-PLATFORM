"""Stateful simulation of one field sensor node (Libelium gateway + probes).

The soil probe layer (0-30 cm) runs a simple bucket water balance:
rain + irrigation - ETc - drainage, with ETc = ET0 (FAO-56 PM) x Kc(DAS) x Ks.
Every reading is derived from that state and the weather, so the sensors are
mutually consistent (VWC <-> water potential <-> O2 <-> EC <-> canopy temp)
and agree with the weather APIs that drive them.
"""
import math
import random
from datetime import timedelta

from common.crop import BINH_THUAN_PRACTICE, SESAME_FAO, crop_coefficient, season_length
from common.et0 import et0_penman_monteith
from common.soil import SoilHydraulics

SCHEMA_VERSION = "2.0"
DEVICE_TYPE = "libelium_smart_agriculture_xtreme"
SENSOR_DEPTH_CM = 15
LAYER_DEPTH_MM = 300.0
MAX_SUBSTEP = timedelta(minutes=15)


class SensorNode:
    def __init__(self, farm, device_id, weather, seed=None, elevation_m=50.0):
        self.farm = farm
        self.device_id = device_id
        self.weather = weather
        self.elevation_m = elevation_m
        self.rng = random.Random(seed if seed is not None else device_id)
        self.soil = SoilHydraulics(farm.soil_texture)
        # Small spatial variability between nodes on the same plot.
        self.layer_mm = LAYER_DEPTH_MM * self.rng.uniform(0.9, 1.1)
        self.theta = self.soil.field_capacity * self.rng.uniform(0.75, 0.95)
        self.battery = self.rng.uniform(80, 95)
        self.wet_until = None
        self.irrigation_end = None
        self.irrigation_rate = 0.0
        self.irrigated_on = None
        self._et0_cache = {}

        p = SESAME_FAO["water_depletion_fraction"]
        fc, wp = self.soil.field_capacity, self.soil.wilting_point
        self.theta_stress = fc - p * (fc - wp)  # readily available water exhausted
        self.drainage_rate = self.soil.drainage_rate * (0.05 if farm.drainage == "poor" else 1.0)
        self.season_days = season_length()

    # ---- helpers ---------------------------------------------------------------------------
    def das(self, t):
        return self.farm.das(self.weather.solar_day(t))

    def crop_present(self, t):
        return 0 <= self.das(t) < self.season_days

    def et0_mm_day(self, day):
        if day not in self._et0_cache:
            d = self.weather.daily(day)
            self._et0_cache[day] = et0_penman_monteith(
                d.tmin_c, d.tmax_c, d.rh_pct, d.wind_2m_m_s, d.rs_mj_m2,
                self.farm.lat, day.timetuple().tm_yday, self.elevation_m)
        return self._et0_cache[day]

    def water_stress_ks(self):
        wp = self.soil.wilting_point
        if self.theta >= self.theta_stress:
            return 1.0
        return max(0.0, (self.theta - wp) / (self.theta_stress - wp))

    def pore_water_ec(self, t):
        """Pore-water EC with a decaying bump after each scheduled fertilizer application."""
        ec = 1.0
        das = self.das(t)
        for event in BINH_THUAN_PRACTICE["fertilizer"]["schedule"]:
            since = das - event["das"]
            if since >= 0 and self.crop_present(t):
                ec += (1.5 if event["type"] == "basal" else 0.8) * 0.5 ** (since / 7)
        return ec

    # ---- irrigation policy -------------------------------------------------------------
    def _maybe_start_irrigation(self, t):
        """Sprinkler at ~06:00 solar time when readily available water is used up
        and no meaningful rain is expected today (the guide: never let it dry out
        at flowering, never waterlog)."""
        local = self.weather.solar_time(t)
        day = local.date()
        if not self.crop_present(t) or self.irrigated_on == day or not 6 <= local.hour < 7:
            return
        if self.theta >= self.theta_stress or self.weather.daily(day).precip_mm >= 5:
            return
        self.irrigated_on = day
        depth_mm = (self.soil.field_capacity - self.theta) * self.layer_mm
        self.irrigation_end = t + timedelta(hours=1)
        self.irrigation_rate = depth_mm  # mm per hour, applied over one hour

    def irrigating(self, t):
        return self.irrigation_end is not None and t < self.irrigation_end

    # ---- state update ------------------------------------------------------------------
    def _advance(self, t0, t1):
        dt_h = (t1 - t0).total_seconds() / 3600
        self._maybe_start_irrigation(t0)

        rain = self.weather.rain_mm(t0, t1)
        irrigation = self.irrigation_rate * dt_h if self.irrigating(t0) else 0.0
        if irrigation == 0.0:
            self.irrigation_end = None

        day = self.weather.solar_day(t0)
        # Distribute daily ETc over the day following solar radiation.
        rs_day_j = self.weather.daily(day).rs_mj_m2 * 1e6
        solar_share = (self.weather.solar_w_m2(t0) * dt_h * 3600 / rs_day_j) if rs_day_j else 0.0
        kc = crop_coefficient(self.das(t0))
        etc = self.et0_mm_day(day) * kc * self.water_stress_ks() * solar_share

        storage = self.theta * self.layer_mm + rain + irrigation - etc
        theta = storage / self.layer_mm
        saturation = self.soil.theta_s
        theta = min(theta, saturation)  # excess becomes runoff/ponding
        fc = self.soil.field_capacity
        if theta > fc:
            theta -= (theta - fc) * (1 - math.exp(-self.drainage_rate * dt_h))
        self.theta = max(theta, self.soil.theta_r + 0.005)

        if rain > 0 or irrigation > 0:
            self.wet_until = t1 + timedelta(hours=1)
        elif self.weather.rh_pct(t0) >= 96:  # dew
            self.wet_until = t1 + timedelta(minutes=30)

        solar = self.weather.solar_w_m2(t0)
        self.battery += (3.0 * dt_h if solar > 300 else 0.0) - 0.35 * dt_h
        self.battery = min(max(self.battery, 5.0), 100.0)
        return rain, irrigation

    def step(self, t0, t1):
        """Advance the state over [t0, t1) and return one telemetry message stamped t1."""
        rain_total = 0.0
        irrigated = False
        wet_seconds = 0.0
        cursor = t0
        while cursor < t1:
            nxt = min(cursor + MAX_SUBSTEP, t1)
            rain, irrigation = self._advance(cursor, nxt)
            rain_total += rain
            irrigated = irrigated or irrigation > 0
            if self.wet_until is not None and self.wet_until > cursor:
                wet_seconds += (min(nxt, self.wet_until) - cursor).total_seconds()
            cursor = nxt
        return self._message(t1, t1 - t0, rain_total, irrigated, wet_seconds)

    # ---- readings ----------------------------------------------------------------------
    def _noise(self, sigma):
        return self.rng.gauss(0, sigma)

    def _message(self, t, interval, rain_mm, irrigated, wet_seconds):
        air = self.weather.air_temp_c(t)
        day = self.weather.solar_day(t)
        d = self.weather.daily(day)
        tmean = (d.tmin_c + d.tmax_c) / 2
        # 15 cm soil temperature: damped and ~3 h behind the air temperature.
        lagged_air = self.weather.air_temp_c(t - timedelta(hours=3))
        soil_temp = tmean + 1.0 + 0.35 * (lagged_air - tmean) - 4.0 * max(self.theta - 0.15, 0)

        solar = self.weather.solar_w_m2(t)
        solar_frac = min(solar / 900, 1.0)
        if self.crop_present(t):
            ks = self.water_stress_ks()
            canopy = air + solar_frac * (4.0 * (1 - ks) - 1.5 * ks)
        else:  # sensor sees bare soil
            canopy = air + 6.0 * solar_frac
        if solar == 0:
            canopy = air - 1.0

        # Soil O2 falls once air-filled porosity drops below ~20 % (waterlogging).
        air_filled = self.soil.theta_s - self.theta
        oxygen = 20.6 - 15.6 * min(max((0.20 - air_filled) / 0.15, 0.0), 1.0)
        ec_pw = self.pore_water_ec(t)
        bulk_ec = ec_pw * self.theta * (1.3 * self.theta + 0.4) + 0.02

        return {
            "schema_version": SCHEMA_VERSION,
            "farm_id": self.farm.farm_id,
            "plot_id": self.farm.plot_id,
            "device_id": self.device_id,
            "device_type": DEVICE_TYPE,
            "timestamp": int(t.timestamp()),
            "datetime": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sample_interval_s": int(interval.total_seconds()),
            "measurements": {
                "soil": {  # TEROS 12, MPS-6, SO-411
                    "depth_cm": SENSOR_DEPTH_CM,
                    "vwc_pct": round(100 * self.theta + self._noise(0.4), 2),
                    "temp_c": round(soil_temp + self._noise(0.2), 1),
                    "bulk_ec_ds_m": round(max(bulk_ec + self._noise(0.005), 0.0), 3),
                    "water_potential_kpa": round(-self.soil.suction_kpa(self.theta)
                                                 * (1 + self._noise(0.03)), 1),
                    "oxygen_level_pct": round(min(oxygen + self._noise(0.15), 21.0), 1),
                },
                "canopy": {  # Phytos 31, SI-411
                    "leaf_wetness_min": round(wet_seconds / 60, 1),
                    "surface_temp_c": round(canopy + self._noise(0.2), 1),
                },
                "weather_micro": {  # Gill MaxiMet
                    "ambient_temp_c": round(air + self._noise(0.15), 1),
                    "humidity_pct": round(min(self.weather.rh_pct(t) + self._noise(1.0), 100.0), 1),
                    "rain_mm": round(rain_mm, 2),
                    "wind_speed_m_s": round(max(self.weather.wind_m_s(t) + self._noise(0.4), 0.0), 1),
                    "solar_rad_w_m2": round(max(solar + self._noise(5), 0.0) if solar else 0.0, 0),
                },
            },
            "telemetry": {
                "battery_pct": round(self.battery, 1),
                "rssi_dbm": int(-72 + self._noise(4)),
                "error_codes": [],
            },
            "flags": {
                "rain_detected": rain_mm > 0,
                "irrigation_active": irrigated,
            },
        }
