"""Incidents for the demo: things that go wrong in a season, so the alerts have
something real to catch. Used by producer.py with --scenario demo.

  pump_failure   the farm's sprinkler pump is broken: no irrigation on those days
  storm          extra rain on those days at the farm only (a local storm that the
                 gridded NASA POWER data does not see; the plot's rain gauges do)
  sensor_outage  one device sends nothing on those days

Dates follow each farm's sowing date (DAS = days after sowing), so the incidents
land in the growth stage they are meant for.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from common.farm import demo_farm


@dataclass(frozen=True)
class Incident:
    kind: str                 # pump_failure | storm | sensor_outage
    farm_id: str
    start: date
    end: date                 # inclusive
    rain_mm_per_day: float = 0.0
    device_id: str = ""
    note: str = ""

    def covers(self, day):
        return self.start <= day <= self.end


def _das(farm_id, das_start, das_end):
    planting = demo_farm(farm_id).planting_date
    return planting + timedelta(days=das_start), planting + timedelta(days=das_end)


def demo_incidents():
    """One incident per farm except BINHTHUAN_03, which stays the undisturbed reference."""
    pump = _das("BINHTHUAN_01", 42, 56)
    storm = _das("BINHTHUAN_02", 45, 46)
    outage = _das("BINHTHUAN_04", 30, 31)
    return (
        Incident("pump_failure", "BINHTHUAN_01", *pump,
                 note="Pump broken for 15 days while the crop flowers (DAS 42-56)"),
        Incident("storm", "BINHTHUAN_02", *storm, rain_mm_per_day=60.0,
                 note="Two days of heavy rain at flowering on the poorly drained plot (DAS 45-46)"),
        Incident("sensor_outage", "BINHTHUAN_04", *outage, device_id="SN_BINHTHUAN_04_P01_01",
                 note="The plot's only sensor node is silent for 2 days (DAS 30-31)"),
    )


SCENARIOS = {"none": lambda: (), "demo": demo_incidents}


def for_farm(incidents, farm_id, kind):
    return [i for i in incidents if i.farm_id == farm_id and i.kind == kind]
