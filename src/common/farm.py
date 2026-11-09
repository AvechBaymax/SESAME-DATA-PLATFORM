"""Farm/plot profile shared by the user-config mock and the IoT simulator."""
import math
from dataclasses import dataclass, field
from datetime import date

EARTH_M_PER_DEG = 111_320.0

# Illustrative plot near Ma Lam, Ham Thuan Bac (Binh Thuan sesame area).
DEFAULT_FARM_ID = "BINHTHUAN_01"
DEFAULT_LAT = 11.08
DEFAULT_LON = 108.12
DEFAULT_PLANTING_DATE = date(2026, 11, 15)  # Dong Xuan sowing window (Nov-Dec)


# Season codes: Dong Xuan (sown Nov-Dec), Xuan He (sown Feb-Mar), KH = outside both windows.
SEASON_CODES = {11: "DX", 12: "DX", 2: "XH", 3: "XH"}


@dataclass
class FarmProfile:
    """IDs nest farm -> plot -> season/device, e.g.
    BINHTHUAN_01 -> BINHTHUAN_01_P01 -> BINHTHUAN_01_P01_2026DX / SN_BINHTHUAN_01_P01_01.
    """

    farm_id: str = DEFAULT_FARM_ID
    plot_id: str = ""  # default: <farm_id>_P01
    lat: float = DEFAULT_LAT
    lon: float = DEFAULT_LON
    area_m2: float = 5000.0
    soil_texture: str = "loamy_sand"
    drainage: str = "good"  # "poor": compacted/low-lying plot that ponds after heavy rain
    planting_date: date = DEFAULT_PLANTING_DATE
    device_count: int = 1
    device_ids: list = field(default_factory=list)  # default: SN_<plot_id>_01..NN

    def __post_init__(self):
        if not self.plot_id:
            self.plot_id = f"{self.farm_id}_P01"
        if not self.device_ids:
            self.device_ids = [f"SN_{self.plot_id}_{i:02d}" for i in range(1, self.device_count + 1)]

    @property
    def season_type(self):
        return SEASON_CODES.get(self.planting_date.month, "KH")

    @property
    def season_id(self):
        return f"{self.plot_id}_{self.planting_date:%Y}{self.season_type}"

    def das(self, day):
        """Days after sowing (negative before sowing)."""
        return (day - self.planting_date).days

    def polygon(self, aspect=2.0):
        """Closed GeoJSON ring of a rectangle (aspect = length/width) centred on the plot."""
        width = math.sqrt(self.area_m2 / aspect)
        length = width * aspect
        dlat = width / 2 / EARTH_M_PER_DEG
        dlon = length / 2 / (EARTH_M_PER_DEG * math.cos(math.radians(self.lat)))
        ring = [
            [self.lon - dlon, self.lat - dlat],
            [self.lon + dlon, self.lat - dlat],
            [self.lon + dlon, self.lat + dlat],
            [self.lon - dlon, self.lat + dlat],
        ]
        ring.append(ring[0])
        return [[round(x, 7), round(y, 7)] for x, y in ring]


def ring_area_perimeter(ring):
    """Area [m2] and perimeter [m] of a lon/lat ring on a local equirectangular projection."""
    lat0 = math.radians(sum(p[1] for p in ring[:-1]) / (len(ring) - 1))
    xy = [(p[0] * EARTH_M_PER_DEG * math.cos(lat0), p[1] * EARTH_M_PER_DEG) for p in ring]
    area = 0.0
    perimeter = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        area += x1 * y2 - x2 * y1
        perimeter += math.hypot(x2 - x1, y2 - y1)
    return abs(area) / 2, perimeter


def add_farm_args(parser):
    parser.add_argument("--farm-id", default=DEFAULT_FARM_ID)
    parser.add_argument("--plot-id", default=None, help="default: <farm-id>_P01")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT)
    parser.add_argument("--lon", type=float, default=DEFAULT_LON)
    parser.add_argument("--area", type=float, default=5000.0, help="Plot area in m2")
    parser.add_argument("--soil-texture", default="loamy_sand",
                        choices=["sand", "loamy_sand", "sandy_loam", "loam"])
    parser.add_argument("--drainage", default="good", choices=["good", "poor"])
    parser.add_argument("--planting-date", type=date.fromisoformat,
                        default=DEFAULT_PLANTING_DATE, help="YYYY-MM-DD")
    parser.add_argument("--devices", type=int, default=1, help="Sensor nodes on the plot")


def farm_from_args(args):
    return FarmProfile(
        farm_id=args.farm_id,
        plot_id=args.plot_id or "",
        lat=args.lat,
        lon=args.lon,
        area_m2=args.area,
        soil_texture=args.soil_texture,
        drainage=args.drainage,
        planting_date=args.planting_date,
        device_count=args.devices,
    )
