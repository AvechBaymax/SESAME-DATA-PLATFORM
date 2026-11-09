"""FAO-56 reference evapotranspiration (daily Penman-Monteith) and helpers."""
import math

SOLAR_CONSTANT = 0.0820  # MJ m-2 min-1
STEFAN_BOLTZMANN = 4.903e-9  # MJ K-4 m-2 day-1


def saturation_vapour_pressure(temp_c):
    """e0(T) [kPa], FAO-56 eq. 11."""
    return 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def solar_declination(doy):
    return 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)


def extraterrestrial_radiation(lat_deg, doy):
    """Ra [MJ m-2 day-1], FAO-56 eq. 21."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = solar_declination(doy)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (24 * 60 / math.pi) * SOLAR_CONSTANT * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )


def et0_penman_monteith(tmin, tmax, rh_mean, wind_2m, rs, lat_deg, doy, elevation_m=0.0):
    """Daily ET0 [mm/day] from FAO-56 eq. 6.

    tmin/tmax [deg C], rh_mean [%], wind_2m [m/s at 2 m], rs [MJ m-2 day-1].
    Wind measured at 10 m (e.g. OpenWeatherMap) must be converted first:
    u2 = u10 * 0.748.
    """
    tmean = (tmin + tmax) / 2
    pressure = 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26
    gamma = 0.665e-3 * pressure
    delta = 4098 * saturation_vapour_pressure(tmean) / (tmean + 237.3) ** 2
    es = (saturation_vapour_pressure(tmax) + saturation_vapour_pressure(tmin)) / 2
    ea = es * rh_mean / 100

    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = (0.75 + 2e-5 * elevation_m) * ra
    rns = (1 - 0.23) * rs
    rs_rso = min(rs / rso, 1.0) if rso > 0 else 0.0
    rnl = (STEFAN_BOLTZMANN * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2
           * (0.34 - 0.14 * math.sqrt(max(ea, 0.0))) * (1.35 * rs_rso - 0.35))
    rn = rns - rnl

    et0 = (0.408 * delta * rn + gamma * 900 / (tmean + 273) * wind_2m * (es - ea)) / (
        delta + gamma * (1 + 0.34 * wind_2m)
    )
    return max(et0, 0.0)
