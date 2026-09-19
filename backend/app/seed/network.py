"""Builds the zone, bin and vehicle records from the Mumbai specification."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.bin import Bin
from app.models.enums import BinStatus
from app.models.vehicle import Vehicle
from app.models.zone import Zone
from app.seed.mumbai import (
    DEFAULT_SEED,
    VEHICLE_SPECS,
    ZONE_SPECS,
    offset_position,
    pick_capacity,
    pick_waste_type,
)

logger = get_logger(__name__)


@dataclass
class SeedReport:
    zones_created: int = 0
    bins_created: int = 0
    vehicles_created: int = 0
    skipped_existing: int = 0

    def __str__(self) -> str:
        return (
            f"{self.zones_created} zones, {self.bins_created} bins, "
            f"{self.vehicles_created} vehicles created "
            f"({self.skipped_existing} already existed and were left untouched)"
        )


def seed_network(db: Session, *, seed: int = DEFAULT_SEED) -> SeedReport:
    """Create the network if it is not already present.

    Idempotent by design: re-running it never duplicates a bin or overwrites
    live telemetry, so it is safe to call on a database that is already in use.
    """
    report = SeedReport()
    rng = random.Random(seed)
    installed_on = datetime.now(timezone.utc) - timedelta(days=400)

    for spec in ZONE_SPECS:
        zone = db.scalar(select(Zone).where(Zone.code == spec.code))
        if zone is None:
            zone = Zone(
                code=spec.code,
                name=spec.name,
                zone_type=spec.zone_type,
                center_lat=spec.lat,
                center_lon=spec.lon,
                population_served=spec.population,
                description=f"{spec.name} collection zone, {spec.radius_km} km service radius",
            )
            db.add(zone)
            db.flush()
            report.zones_created += 1

        existing = db.scalar(
            select(func.count()).select_from(Bin).where(Bin.zone_id == zone.id)
        ) or 0
        if existing >= spec.bin_count:
            report.skipped_existing += existing
            # Keep the generator in step so untouched zones do not shift the
            # positions of bins in later zones.
            for _ in range(spec.bin_count):
                offset_position(spec.lat, spec.lon, spec.radius_km, rng)
                pick_waste_type(rng, spec.zone_type)
                pick_capacity(rng, spec.zone_type)
            continue

        for index in range(spec.bin_count):
            code = f"MUM-{spec.code}-{index + 1:03d}"
            lat, lon = offset_position(spec.lat, spec.lon, spec.radius_km, rng)
            waste_type = pick_waste_type(rng, spec.zone_type)
            capacity = pick_capacity(rng, spec.zone_type)

            if db.scalar(select(Bin.id).where(Bin.code == code)):
                report.skipped_existing += 1
                continue

            db.add(
                Bin(
                    code=code,
                    label=f"{spec.name} #{index + 1}",
                    address=f"{spec.name}, Mumbai",
                    zone_id=zone.id,
                    latitude=lat,
                    longitude=lon,
                    capacity_liters=capacity,
                    waste_type=waste_type,
                    status=BinStatus.ACTIVE,
                    sensor_id=f"SNS-{spec.code}-{index + 1:03d}",
                    installed_on=installed_on,
                    battery_level=round(rng.uniform(72.0, 100.0), 1),
                )
            )
            report.bins_created += 1

    for spec in VEHICLE_SPECS:
        if db.scalar(select(Vehicle.id).where(Vehicle.code == spec.code)):
            report.skipped_existing += 1
            continue

        db.add(
            Vehicle(
                code=spec.code,
                registration_number=spec.registration,
                vehicle_type=spec.vehicle_type,
                capacity_liters=spec.capacity_liters,
                capacity_kg=spec.capacity_kg,
                accepted_waste_types=[w.value for w in spec.accepted] if spec.accepted else None,
                depot_lat=spec.depot_lat,
                depot_lon=spec.depot_lon,
                shift_start=time.fromisoformat(spec.shift[0]),
                shift_end=time.fromisoformat(spec.shift[1]),
                driver_name=spec.driver,
                current_lat=spec.depot_lat,
                current_lon=spec.depot_lon,
            )
        )
        report.vehicles_created += 1

    db.commit()
    logger.info("Network seed complete: %s", report)
    return report
