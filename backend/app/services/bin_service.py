"""Bin CRUD, filtered querying, manual collection, and network statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.bin import Bin, BinReading
from app.models.collection import CollectionEvent
from app.models.enums import BinStatus, ReadingSource, WasteType
from app.models.vehicle import Vehicle
from app.models.zone import Zone
from app.schemas.bin import BinCreate, BinEmptyRequest, BinSummary, BinUpdate
from app.services.geo import bounding_box, haversine_km
from app.services.waste import estimate_weight_kg, split_recyclable_kg

SORTABLE_FIELDS = {
    "code": Bin.code,
    "fill_level": Bin.current_fill_level,
    "capacity": Bin.capacity_liters,
    "last_reading_at": Bin.last_reading_at,
    "overflow_count": Bin.overflow_count,
    "created_at": Bin.created_at,
}


@dataclass(slots=True)
class BinFilters:
    """Every filter the bin list endpoint supports, in one object."""

    zone_id: int | None = None
    waste_types: list[WasteType] | None = None
    statuses: list[BinStatus] | None = None
    min_fill: float | None = None
    max_fill: float | None = None
    needs_collection: bool | None = None
    search: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # min_lat, min_lon, max_lat, max_lon
    near: tuple[float, float, float] | None = None  # lat, lon, radius_km
    sort_by: str = "code"
    sort_desc: bool = False


def _effective_threshold_expr():
    """SQL expression for a bin's collection threshold, honouring the override."""
    return func.coalesce(Bin.fill_threshold_override, settings.bin_full_threshold)


def _apply_filters(stmt: Select, filters: BinFilters) -> Select:
    if filters.zone_id is not None:
        stmt = stmt.where(Bin.zone_id == filters.zone_id)
    if filters.waste_types:
        stmt = stmt.where(Bin.waste_type.in_(filters.waste_types))
    if filters.statuses:
        stmt = stmt.where(Bin.status.in_(filters.statuses))
    if filters.min_fill is not None:
        stmt = stmt.where(Bin.current_fill_level >= filters.min_fill)
    if filters.max_fill is not None:
        stmt = stmt.where(Bin.current_fill_level <= filters.max_fill)
    if filters.needs_collection is True:
        stmt = stmt.where(Bin.current_fill_level >= _effective_threshold_expr())
    elif filters.needs_collection is False:
        stmt = stmt.where(Bin.current_fill_level < _effective_threshold_expr())
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        stmt = stmt.where(
            or_(
                Bin.code.ilike(pattern),
                Bin.label.ilike(pattern),
                Bin.address.ilike(pattern),
            )
        )
    if filters.bbox:
        min_lat, min_lon, max_lat, max_lon = filters.bbox
        stmt = stmt.where(
            and_(
                Bin.latitude >= min_lat,
                Bin.latitude <= max_lat,
                Bin.longitude >= min_lon,
                Bin.longitude <= max_lon,
            )
        )
    if filters.near:
        # Index-friendly pre-filter; the exact circle is applied in Python after
        # the query, because a true distance predicate is not indexable here.
        lat, lon, radius_km = filters.near
        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_km)
        stmt = stmt.where(
            and_(
                Bin.latitude >= min_lat,
                Bin.latitude <= max_lat,
                Bin.longitude >= min_lon,
                Bin.longitude <= max_lon,
            )
        )
    return stmt


def list_bins(
    db: Session, filters: BinFilters, *, offset: int, limit: int
) -> tuple[list[Bin], int]:
    """Return one page of bins plus the total matching count."""
    base = _apply_filters(select(Bin), filters)

    if filters.near:
        # The radius filter is exact only after the haversine check, so paginate
        # the refined set rather than the bounding-box set.
        lat, lon, radius_km = filters.near
        candidates = [
            b
            for b in db.scalars(base)
            if haversine_km(lat, lon, b.latitude, b.longitude) <= radius_km
        ]
        candidates.sort(key=lambda b: haversine_km(lat, lon, b.latitude, b.longitude))
        return candidates[offset : offset + limit], len(candidates)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    column = SORTABLE_FIELDS.get(filters.sort_by, Bin.code)
    order = column.desc() if filters.sort_desc else column.asc()
    rows = list(db.scalars(base.order_by(order, Bin.id).offset(offset).limit(limit)))
    return rows, total


def get_bin(db: Session, bin_id: int) -> Bin:
    found = db.get(Bin, bin_id)
    if found is None:
        raise NotFoundError(f"Bin {bin_id} not found")
    return found


def get_bin_by_code(db: Session, code: str) -> Bin:
    found = db.scalar(select(Bin).where(Bin.code == code))
    if found is None:
        raise NotFoundError(f"Bin '{code}' not found")
    return found


def create_bin(db: Session, payload: BinCreate) -> Bin:
    if db.scalar(select(Bin.id).where(Bin.code == payload.code)):
        raise ConflictError(f"Bin code '{payload.code}' is already in use")
    if payload.sensor_id and db.scalar(select(Bin.id).where(Bin.sensor_id == payload.sensor_id)):
        raise ConflictError(f"Sensor '{payload.sensor_id}' is already bound to another bin")
    if db.get(Zone, payload.zone_id) is None:
        raise NotFoundError(f"Zone {payload.zone_id} not found")

    bin_obj = Bin(**payload.model_dump())
    db.add(bin_obj)
    db.commit()
    db.refresh(bin_obj)
    return bin_obj


def update_bin(db: Session, bin_id: int, payload: BinUpdate) -> Bin:
    bin_obj = get_bin(db, bin_id)
    changes = payload.model_dump(exclude_unset=True)

    if "zone_id" in changes and db.get(Zone, changes["zone_id"]) is None:
        raise NotFoundError(f"Zone {changes['zone_id']} not found")
    if changes.get("sensor_id"):
        clash = db.scalar(
            select(Bin.id).where(
                Bin.sensor_id == changes["sensor_id"], Bin.id != bin_id
            )
        )
        if clash:
            raise ConflictError(f"Sensor '{changes['sensor_id']}' is already bound to another bin")

    for field, value in changes.items():
        setattr(bin_obj, field, value)

    db.commit()
    db.refresh(bin_obj)
    return bin_obj


def delete_bin(db: Session, bin_id: int) -> None:
    db.delete(get_bin(db, bin_id))
    db.commit()


def get_readings(
    db: Session,
    bin_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 1000,
) -> list[BinReading]:
    """Fill-level history for a bin, oldest first so charts plot left to right."""
    get_bin(db, bin_id)

    stmt = select(BinReading).where(BinReading.bin_id == bin_id)
    if start:
        stmt = stmt.where(BinReading.recorded_at >= start)
    if end:
        stmt = stmt.where(BinReading.recorded_at <= end)

    # Take the most recent `limit` rows, then flip to chronological order.
    rows = list(db.scalars(stmt.order_by(BinReading.recorded_at.desc()).limit(limit)))
    return list(reversed(rows))


def get_collections(db: Session, bin_id: int, *, limit: int = 100) -> list[CollectionEvent]:
    get_bin(db, bin_id)
    return list(
        db.scalars(
            select(CollectionEvent)
            .where(CollectionEvent.bin_id == bin_id)
            .order_by(CollectionEvent.collected_at.desc())
            .limit(limit)
        )
    )


def empty_bin(db: Session, bin_id: int, payload: BinEmptyRequest) -> CollectionEvent:
    """Record a manual collection and reset the bin to empty.

    Used when a crew services a bin off-route, or when a bin has no sensor at
    all, so the collection history stays complete either way.
    """
    bin_obj = get_bin(db, bin_id)
    collected_at = payload.collected_at or datetime.now(timezone.utc)
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)

    if payload.vehicle_id is not None and db.get(Vehicle, payload.vehicle_id) is None:
        raise NotFoundError(f"Vehicle {payload.vehicle_id} not found")

    fill_before = bin_obj.current_fill_level
    if fill_before <= 0:
        raise ValidationError("Bin is already empty; nothing to collect")

    volume = bin_obj.capacity_liters * fill_before / 100.0
    weight = (
        payload.weight_collected_kg
        if payload.weight_collected_kg is not None
        else estimate_weight_kg(bin_obj.waste_type, volume)
    )
    recyclable, non_recyclable = split_recyclable_kg(
        bin_obj.waste_type, weight, payload.contamination_pct
    )

    hours_since_previous = None
    if bin_obj.last_emptied_at:
        hours_since_previous = round(
            (collected_at - bin_obj.last_emptied_at).total_seconds() / 3600.0, 2
        )

    event = CollectionEvent(
        bin_id=bin_obj.id,
        vehicle_id=payload.vehicle_id,
        collected_at=collected_at,
        fill_level_before=fill_before,
        volume_collected_liters=round(volume, 2),
        weight_collected_kg=round(weight, 3),
        recyclable_kg=recyclable,
        non_recyclable_kg=non_recyclable,
        waste_type=bin_obj.waste_type,
        contamination_pct=payload.contamination_pct,
        was_overflowing=fill_before >= settings.bin_critical_threshold,
        hours_since_previous=hours_since_previous,
        notes=payload.notes,
    )
    db.add(event)

    # Record the reset as a real reading so the history stays continuous and the
    # forecaster sees the same drop it would have seen from the sensor.
    db.add(
        BinReading(
            bin_id=bin_obj.id,
            recorded_at=collected_at,
            fill_level=0.0,
            weight_kg=0.0,
            source=ReadingSource.MANUAL,
        )
    )
    bin_obj.current_fill_level = 0.0
    bin_obj.current_weight_kg = 0.0
    bin_obj.last_emptied_at = collected_at
    if bin_obj.last_reading_at is None or collected_at >= bin_obj.last_reading_at:
        bin_obj.last_reading_at = collected_at

    db.commit()
    db.refresh(event)
    return event


def get_summary(db: Session, zone_id: int | None = None) -> BinSummary:
    """Network-wide counters powering the dashboard header."""
    scope = select(Bin)
    if zone_id is not None:
        scope = scope.where(Bin.zone_id == zone_id)
    bins = list(db.scalars(scope))

    stale_before = datetime.now(timezone.utc) - timedelta(hours=settings.sensor_stale_hours)

    def threshold_of(b: Bin) -> float:
        if b.fill_threshold_override is not None:
            return b.fill_threshold_override
        return settings.bin_full_threshold

    by_waste_type: dict[str, int] = {}
    by_fill_band = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    needing = overflowing = stale = 0
    total_capacity = current_volume = fill_sum = 0.0

    for b in bins:
        by_waste_type[b.waste_type.value] = by_waste_type.get(b.waste_type.value, 0) + 1
        total_capacity += b.capacity_liters
        current_volume += b.current_volume_liters
        fill_sum += b.current_fill_level

        if b.current_fill_level >= settings.bin_critical_threshold:
            by_fill_band["critical"] += 1
            overflowing += 1
        elif b.current_fill_level >= threshold_of(b):
            by_fill_band["high"] += 1
        elif b.current_fill_level >= 50:
            by_fill_band["medium"] += 1
        else:
            by_fill_band["low"] += 1

        if b.current_fill_level >= threshold_of(b):
            needing += 1
        if b.last_reading_at is None or b.last_reading_at < stale_before:
            stale += 1

    count = len(bins)
    return BinSummary(
        total_bins=count,
        active_bins=sum(1 for b in bins if b.status is BinStatus.ACTIVE),
        offline_bins=sum(1 for b in bins if b.status is BinStatus.OFFLINE),
        maintenance_bins=sum(1 for b in bins if b.status is BinStatus.MAINTENANCE),
        bins_needing_collection=needing,
        bins_overflowing=overflowing,
        average_fill_level=round(fill_sum / count, 1) if count else 0.0,
        total_capacity_liters=round(total_capacity, 1),
        estimated_current_volume_liters=round(current_volume, 1),
        by_waste_type=by_waste_type,
        by_fill_band=by_fill_band,
        stale_sensors=stale,
    )
