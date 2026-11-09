"""Sesame crop parameters: FAO references plus the Binh Thuan cultivation guide.

Single source of truth for the FAO reference upload, the IoT simulator and,
later, the Silver/Gold jobs, so all of them use the same Kc and stage lengths.
"""

# FAO-56 Table 12 (Kc), Table 11 (stage lengths), Table 22 (root depth, p).
# Local stage lengths split a 75-day season for "me den 2 vo Binh Thuan";
# the guide does not state the season length, so confirm it with the grower.
SESAME_FAO = {
    "crop_id": "sesame",
    "crop_name": "Mè (Sesame)",
    "source": "FAO Irrigation and Drainage Paper No. 56 & 33",
    "growth_stages_days": {
        "initial": 15,
        "development": 25,
        "mid_season": 25,
        "late_season": 10,
    },
    "fao56_reference_stages_days": {
        # FAO-56 Table 11, sesame, China (sown June): 110-day season
        "initial": 20,
        "development": 30,
        "mid_season": 40,
        "late_season": 20,
    },
    "kc_coefficients": {"kc_ini": 0.35, "kc_mid": 1.10, "kc_end": 0.25},
    "max_crop_height_m": 1.0,
    "root_depth_m": {"min": 1.0, "max": 1.5},
    "water_depletion_fraction": 0.60,
    "yield_response_factor_ky": {
        # Values carried over from the original mock; source not verified yet.
        "vegetative": 0.6,
        "flowering": 1.0,
        "yield_formation": 1.0,
        "ripening": 0.2,
        "total_season": 1.1,
    },
}

# Quy trinh ky thuat canh tac me den 2 vo Binh Thuan (document from the grower).
BINH_THUAN_PRACTICE = {
    "crop_id": "sesame",
    "variety": "Mè đen 2 vỏ Bình Thuận",
    "source": "Quy trình kỹ thuật canh tác mè đen 2 vỏ Bình Thuận",
    "ecology": {
        "temp_optimal_c": [25, 30],
        "temp_slow_growth_below_c": 20,
        "season_rainfall_mm": [500, 600],
        "soil_ph": [5.5, 7.5],
        "soil_textures": ["loamy_sand", "sandy_loam", "alluvial"],
        "waterlogging_sensitive": True,
        "no_drought_during": "flowering",
        "heavy_rain_risk_stages": ["flowering", "harvest"],
    },
    "sowing_windows": [
        {"season": "dong_xuan", "months": [11, 12]},
        {"season": "xuan_he", "months": [2, 3]},
    ],
    "land_preparation": {"plough_depth_cm": 15, "bed_height_cm": [15, 20],
                         "bed_width_m": [0.5, 1.5], "furrow_width_cm": [20, 30]},
    "sowing": {
        "seed_rate_kg_ha": 3,
        "row_spacing_cm": 60,
        "hill_spacing_cm": 15,
        "plants_per_hill": [1, 2],
        "sowing_depth_cm": [4, 5],
        "target_density_plants_m2": [10, 20],
        "thinning_at": "2 true leaves",
    },
    "fertilizer": {
        "nutrients_kg_ha": {"N": 120, "P2O5": 60, "K2O": 60},
        "products_kg_ha": {"urea": 260, "super_lan": 375, "kcl": 100,
                           "lime": 300, "organic_microbial": 1000},
        "alternative_npk_16_16_8_kg_ha": 750,
        "schedule": [
            {"das": 0, "type": "basal",
             "share": {"organic_microbial": 1, "lime": 1, "super_lan": 1,
                       "urea": 1 / 3, "kcl": 1 / 3}},
            {"das": 15, "type": "topdress", "share": {"urea": 1 / 3, "kcl": 1 / 3}},
            {"das": 25, "type": "topdress", "share": {"urea": 1 / 3, "kcl": 1 / 3}},
        ],
    },
    "weed_control_das": [15, 25],
    "irrigation": {"methods": ["sprinkler", "furrow"]},
    "harvest": {"trigger": "2/3 of pods and leaves turned yellow",
                "bundle_stand_days": [3, 5], "sun_dry_days": [2, 3]},
    "seed_standard": {"purity_pct_min": 99, "germination_pct_min": 70,
                      "weed_seeds_per_kg_max": 5, "other_variety_pct_max": 20,
                      "moisture_pct_max": {"normal_bag": 12, "waterproof_bag": 10}},
    "grain_standard": {"moisture_pct_max": 7, "impurity_pct_max": 1},
}


def season_length(params=SESAME_FAO):
    return sum(params["growth_stages_days"].values())


def growth_stage(das, params=SESAME_FAO):
    """FAO stage name for a day-after-sowing, or None outside the season."""
    if das < 0:
        return None
    edge = 0
    for name, length in params["growth_stages_days"].items():
        edge += length
        if das < edge:
            return name
    return None


def crop_coefficient(das, params=SESAME_FAO):
    """FAO-56 single Kc curve: flat initial, linear development, flat mid, linear late.

    Outside the season (fallow) the bare-soil Kc_ini is returned.
    """
    stages = params["growth_stages_days"]
    kc = params["kc_coefficients"]
    l_ini, l_dev, l_mid, l_late = (stages[k] for k in
                                   ("initial", "development", "mid_season", "late_season"))
    if das < 0 or das >= l_ini + l_dev + l_mid + l_late:
        return kc["kc_ini"]
    if das < l_ini:
        return kc["kc_ini"]
    if das < l_ini + l_dev:
        return kc["kc_ini"] + (das - l_ini) / l_dev * (kc["kc_mid"] - kc["kc_ini"])
    if das < l_ini + l_dev + l_mid:
        return kc["kc_mid"]
    late = das - (l_ini + l_dev + l_mid)
    return kc["kc_mid"] + late / l_late * (kc["kc_end"] - kc["kc_mid"])


def in_sowing_window(day, practice=BINH_THUAN_PRACTICE):
    return any(day.month in w["months"] for w in practice["sowing_windows"])
