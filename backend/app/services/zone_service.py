"""Zone CRUD and per-zone roll-ups."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.bin import Bin
from app.models.enums import BinStatus
from app.models.zone import Zone
from app.schemas.zone import ZoneCreate, ZoneStats, ZoneUpdate


def list_zones(db: Session, *, offset: int, limit: int) -> tuple[list[Zone], int]:
    total = db.scalar(select(func.count()).select_from(Zone)) or 0
    rows = list(db.scalars(select(Zone).order_by(Zone.code).offset(offset).limit(limit)))
    return rows, total


def get_zone(db: Session, zone_id: int) -> Zone:
    found = db.get(Zone, zone_id)
    if found is None:
        raise NotFoundError(f"Zone {zone_id} not found")
    return found


def create_zone(db: Session, payload: ZoneCreate) -> Zone:
    if db.scalar(select(Zone.id).where(Zone.code == payload.code)):
        raise ConflictError(f"Zone code '{payload.code}' is already in use")
    zone = Zone(**payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def update_zone(db: Session, zone_id: int, payload: ZoneUpdate) -> Zone:
    zone = get_zone(db, zone_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    return zone


def delete_zone(db: Session, zone_id: int) -> None:
    """Deleting a zone cascades to its bins, so refuse while bins remain.

    Losing a ward's entire bin history to a mis-click is not recoverable, and
    the cascade exists for test teardown rather than for operators.
    """
    zone = get_zone(db, zone_id)
    bin_count = db.scalar(select(func.count()).select_from(Bin).where(Bin.zone_id == zone_id)) or 0
    if bin_count:
        raise ConflictError(
            f"Zone '{zone.code}' still contains {bin_count} bins; reassign or delete them first",
            details={"bin_count": bin_count},
        )
    db.delete(zone)
    db.commit()


def get_zone_stats(db: Session, zone_id: int) -> ZoneStats:
    zone = get_zone(db, zone_id)
    bins = list(db.scalars(select(Bin).where(Bin.zone_id == zone_id)))

    def threshold_of(b: Bin) -> float:
        if b.fill_threshold_override is not None:
            return b.fill_threshold_override
        return settings.bin_full_threshold

    count = len(bins)
    return ZoneStats(
        zone_id=zone.id,
        zone_code=zone.code,
        zone_name=zone.name,
        zone_type=zone.zone_type,
        total_bins=count,
        active_bins=sum(1 for b in bins if b.status is BinStatus.ACTIVE),
        average_fill_level=round(sum(b.current_fill_level for b in bins) / count, 1) if count else 0.0,
        bins_needing_collection=sum(1 for b in bins if b.current_fill_level >= threshold_of(b)),
        bins_overflowing=sum(
            1 for b in bins if b.current_fill_level >= settings.bin_critical_threshold
        ),
        total_capacity_liters=round(sum(b.capacity_liters for b in bins), 1),
        estimated_current_volume_liters=round(sum(b.current_volume_liters for b in bins), 1),
    )
