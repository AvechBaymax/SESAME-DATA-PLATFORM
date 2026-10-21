"""Soil hydraulics by texture class.

Field capacity / wilting point are the mid values of FAO-56 Table 19, so the
simulator and the Gold water-balance jobs share the same TAW. The van Genuchten
retention curve (used for the MPS-6 water potential) is fitted through those
two points, starting from the Carsel & Parrish (1988) theta_s.
"""

KPA_PER_CM_H2O = 0.0980665
WILTING_POINT_KPA = 1500.0

# texture: (field capacity, wilting point, theta_s, field-capacity suction kPa,
#           drainage rate of water above field capacity [1/h])
TEXTURES = {
    "sand": (0.12, 0.045, 0.43, 10.0, 1.2),
    "loamy_sand": (0.15, 0.065, 0.41, 10.0, 0.9),
    "sandy_loam": (0.23, 0.11, 0.41, 33.0, 0.4),
    "loam": (0.25, 0.12, 0.43, 33.0, 0.2),
}


def _theta(suction_kpa, theta_r, theta_s, alpha, n):
    m = 1 - 1 / n
    h_cm = suction_kpa / KPA_PER_CM_H2O
    return theta_r + (theta_s - theta_r) * (1 + (alpha * h_cm) ** n) ** (-m)


def _alpha_for(theta, suction_kpa, theta_r, theta_s, n):
    m = 1 - 1 / n
    se = (theta - theta_r) / (theta_s - theta_r)
    return (se ** (-1 / m) - 1) ** (1 / n) / (suction_kpa / KPA_PER_CM_H2O)


class SoilHydraulics:
    def __init__(self, texture):
        if texture not in TEXTURES:
            raise ValueError(f"Unknown soil texture {texture!r}; use one of {sorted(TEXTURES)}")
        self.texture = texture
        fc, wp, self.theta_s, fc_kpa, self.drainage_rate = TEXTURES[texture]
        self.field_capacity = fc
        self.wilting_point = wp
        self.theta_r = wp / 2
        # Bisection on n so that theta(fc_kpa) = fc once alpha pins theta(1500 kPa) = wp.
        lo, hi = 1.05, 6.0
        for _ in range(80):
            n = (lo + hi) / 2
            alpha = _alpha_for(wp, WILTING_POINT_KPA, self.theta_r, self.theta_s, n)
            if _theta(fc_kpa, self.theta_r, self.theta_s, alpha, n) > fc:
                hi = n  # theta at field-capacity suction grows with n
            else:
                lo = n
        self.n = n
        self.alpha = alpha
        self.m = 1 - 1 / n

    def theta_at(self, suction_kpa):
        return _theta(suction_kpa, self.theta_r, self.theta_s, self.alpha, self.n)

    def suction_kpa(self, theta):
        """Matric suction (positive kPa) for a volumetric water content."""
        se = (theta - self.theta_r) / (self.theta_s - self.theta_r)
        se = min(max(se, 1e-4), 0.9999)
        h_cm = (se ** (-1 / self.m) - 1) ** (1 / self.n) / self.alpha
        return h_cm * KPA_PER_CM_H2O


def texture_from_fractions(sand_pct, clay_pct):
    """Coarse USDA texture class from sand/clay % (enough for the sandy soils here).

    SoilGrids reports sand/clay in g/kg; divide by 10 before calling.
    """
    silt_pct = 100 - sand_pct - clay_pct
    if sand_pct >= 85 and silt_pct + 1.5 * clay_pct < 15:
        return "sand"
    if sand_pct >= 70 and silt_pct + 2 * clay_pct < 30:
        return "loamy_sand"
    if (clay_pct < 20 and sand_pct >= 43) or (clay_pct < 7 and silt_pct < 50):
        return "sandy_loam"
    return "loam"
