"""The Mumbai bin network used for development and demos.

Zone centres are real coordinates for the wards they name, so the dashboard map
looks like an actual city operation rather than points scattered in the sea.
Bins are then distributed around each centre with a seeded generator, which
makes the whole network reproducible: the same seed always produces the same
bin codes at the same positions, so screenshots and metrics stay comparable.

This module creates *infrastructure* (zones, bins, vehicles). The historical
telemetry that trains the forecasting model is generated separately.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.models.enums import VehicleType, WasteType, ZoneType

DEFAULT_SEED = 20260919


@dataclass(frozen=True)
class ZoneSpec:
    code: str
    name: str
    zone_type: ZoneType
    lat: float
    lon: float
    radius_km: float
    bin_count: int
    population: int


# Eight wards spanning every land-use type, so the analytics and anomaly
# detection have genuinely different generation patterns to separate.
ZONE_SPECS: tuple[ZoneSpec, ...] = (
    ZoneSpec("AND-E", "Andheri East", ZoneType.MIXED_USE, 19.1136, 72.8697, 2.2, 22, 210_000),
    ZoneSpec("BAN-W", "Bandra West", ZoneType.COMMERCIAL, 19.0596, 72.8295, 1.8, 18, 130_000),
    ZoneSpec("DAD-C", "Dadar Central", ZoneType.MIXED_USE, 19.0178, 72.8478, 1.5, 16, 145_000),
    ZoneSpec("POW-R", "Powai", ZoneType.RESIDENTIAL, 19.1197, 72.9051, 2.0, 16, 120_000),
    ZoneSpec("FOR-C", "Fort & Colaba", ZoneType.COMMERCIAL, 18.9320, 72.8347, 1.6, 15, 95_000),
    ZoneSpec("GOR-R", "Goregaon", ZoneType.RESIDENTIAL, 19.1663, 72.8526, 2.4, 14, 165_000),
    ZoneSpec("VIK-I", "Vikhroli Industrial", ZoneType.INDUSTRIAL, 19.1117, 72.9350, 1.9, 10, 40_000),
    ZoneSpec("IITB-C", "IIT Bombay Campus", ZoneType.INSTITUTIONAL, 19.1334, 72.9133, 1.2, 9, 12_000),
    ZoneSpec("CHW-P", "Girgaon Chowpatty", ZoneType.PUBLIC, 18.9548, 72.8145, 1.0, 10, 30_000),
)

# Standard EU/Indian municipal bin sizes, with the larger ones weighted toward
# busy zones by the placement logic below.
CAPACITY_CHOICES = (240.0, 360.0, 660.0, 1100.0)

# Realistic street-level mix: most bins are still unsegregated general waste,
# with dedicated recycling streams concentrated in richer commercial wards.
WASTE_TYPE_WEIGHTS: dict[WasteType, float] = {
    WasteType.MIXED: 0.40,
    WasteType.ORGANIC: 0.18,
    WasteType.PLASTIC: 0.15,
    WasteType.PAPER: 0.10,
    WasteType.GLASS: 0.07,
    WasteType.METAL: 0.06,
    WasteType.OTHER: 0.04,
}


@dataclass(frozen=True)
class VehicleSpec:
    code: str
    registration: str
    vehicle_type: VehicleType
    capacity_liters: float
    capacity_kg: float
    accepted: tuple[WasteType, ...] | None
    depot_lat: float
    depot_lon: float
    shift: tuple[str, str]
    driver: str


# Two depots covering the western and eastern halves of the network, with a
# deliberate mix of general compactors and dedicated recycling trucks so the
# optimizer has waste-type compatibility constraints to respect.
VEHICLE_SPECS: tuple[VehicleSpec, ...] = (
    VehicleSpec("MUM-CMP-01", "MH01AB1101", VehicleType.COMPACTOR, 9000, 5500, None,
                19.0760, 72.8777, ("06:00:00", "14:00:00"), "R. Sharma"),
    VehicleSpec("MUM-CMP-02", "MH01AB1102", VehicleType.COMPACTOR, 9000, 5500, None,
                19.0760, 72.8777, ("14:00:00", "22:00:00"), "S. Kulkarni"),
    VehicleSpec("MUM-CMP-03", "MH01AB1103", VehicleType.COMPACTOR, 12000, 7000, None,
                19.1180, 72.9090, ("06:00:00", "14:00:00"), "A. Pawar"),
    VehicleSpec("MUM-REC-01", "MH01RC2101", VehicleType.RECYCLING_TRUCK, 10000, 3500,
                (WasteType.PLASTIC, WasteType.PAPER, WasteType.METAL, WasteType.GLASS),
                19.0760, 72.8777, ("07:00:00", "15:00:00"), "N. Desai"),
    VehicleSpec("MUM-REC-02", "MH01RC2102", VehicleType.RECYCLING_TRUCK, 10000, 3500,
                (WasteType.PLASTIC, WasteType.PAPER, WasteType.METAL, WasteType.GLASS),
                19.1180, 72.9090, ("07:00:00", "15:00:00"), "P. Iyer"),
    VehicleSpec("MUM-ORG-01", "MH01OG3101", VehicleType.TIPPER, 6000, 6000,
                (WasteType.ORGANIC, WasteType.MIXED),
                19.0760, 72.8777, ("05:00:00", "13:00:00"), "V. Gaikwad"),
    VehicleSpec("MUM-MIN-01", "MH01MN4101", VehicleType.MINI_TRUCK, 3000, 1800, None,
                18.9320, 72.8347, ("08:00:00", "16:00:00"), "K. Mehta"),
)


def offset_position(
    lat: float, lon: float, radius_km: float, rng: random.Random
) -> tuple[float, float]:
    """Random point inside a circle, uniformly distributed by area.

    Taking sqrt of the random radius matters: sampling the radius directly would
    cluster bins in the middle of every ward and leave the edges empty.
    """
    angle = rng.uniform(0, 2 * math.pi)
    distance = radius_km * math.sqrt(rng.random())

    delta_lat = (distance / 111.32) * math.sin(angle)
    delta_lon = (distance / (111.32 * math.cos(math.radians(lat)))) * math.cos(angle)
    return round(lat + delta_lat, 6), round(lon + delta_lon, 6)


def pick_waste_type(rng: random.Random, zone_type: ZoneType) -> WasteType:
    weights = dict(WASTE_TYPE_WEIGHTS)

    if zone_type is ZoneType.COMMERCIAL:
        # Shops and restaurants generate far more packaging and food waste.
        weights[WasteType.PAPER] += 0.06
        weights[WasteType.PLASTIC] += 0.04
        weights[WasteType.MIXED] -= 0.10
    elif zone_type is ZoneType.INSTITUTIONAL:
        # Campuses run active segregation programmes.
        weights[WasteType.PAPER] += 0.10
        weights[WasteType.ORGANIC] += 0.05
        weights[WasteType.MIXED] -= 0.15
    elif zone_type is ZoneType.INDUSTRIAL:
        weights[WasteType.METAL] += 0.12
        weights[WasteType.OTHER] += 0.06
        weights[WasteType.ORGANIC] -= 0.10
        weights[WasteType.MIXED] -= 0.08
    elif zone_type is ZoneType.PUBLIC:
        # Promenade bins are dominated by drink containers and snack wrappers.
        weights[WasteType.PLASTIC] += 0.10
        weights[WasteType.MIXED] += 0.05
        weights[WasteType.ORGANIC] -= 0.10
        weights[WasteType.METAL] -= 0.03

    options = list(weights)
    magnitudes = [max(0.01, weights[o]) for o in options]
    return rng.choices(options, weights=magnitudes, k=1)[0]


def pick_capacity(rng: random.Random, zone_type: ZoneType) -> float:
    if zone_type in {ZoneType.COMMERCIAL, ZoneType.INDUSTRIAL}:
        return rng.choices(CAPACITY_CHOICES, weights=(0.10, 0.20, 0.40, 0.30))[0]
    if zone_type is ZoneType.PUBLIC:
        return rng.choices(CAPACITY_CHOICES, weights=(0.35, 0.40, 0.20, 0.05))[0]
    return rng.choices(CAPACITY_CHOICES, weights=(0.25, 0.35, 0.30, 0.10))[0]


def total_bin_count() -> int:
    return sum(spec.bin_count for spec in ZONE_SPECS)
