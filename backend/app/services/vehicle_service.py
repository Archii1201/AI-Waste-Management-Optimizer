"""Vehicle CRUD and live position tracking."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import VehicleStatus
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehiclePositionUpdate, VehicleUpdate


def list_vehicles(
    db: Session,
    *,
    offset: int,
    limit: int,
    status: VehicleStatus | None = None,
    is_active: bool | None = None,
) -> tuple[list[Vehicle], int]:
    stmt = select(Vehicle)
    if status is not None:
        stmt = stmt.where(Vehicle.status == status)
    if is_active is not None:
        stmt = stmt.where(Vehicle.is_active == is_active)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Vehicle.code).offset(offset).limit(limit)))
    return rows, total


def get_vehicle(db: Session, vehicle_id: int) -> Vehicle:
    found = db.get(Vehicle, vehicle_id)
    if found is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found")
    return found


def create_vehicle(db: Session, payload: VehicleCreate) -> Vehicle:
    if db.scalar(select(Vehicle.id).where(Vehicle.code == payload.code)):
        raise ConflictError(f"Vehicle code '{payload.code}' is already in use")
    if payload.registration_number and db.scalar(
        select(Vehicle.id).where(Vehicle.registration_number == payload.registration_number)
    ):
        raise ConflictError(f"Registration '{payload.registration_number}' is already in use")

    data = payload.model_dump()
    # Enums are stored as plain strings in the JSON column so the value round-trips
    # identically whether it came from the API or from the optimizer.
    if data.get("accepted_waste_types"):
        data["accepted_waste_types"] = [w.value for w in data["accepted_waste_types"]]

    vehicle = Vehicle(**data)
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def update_vehicle(db: Session, vehicle_id: int, payload: VehicleUpdate) -> Vehicle:
    vehicle = get_vehicle(db, vehicle_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("accepted_waste_types"):
        changes["accepted_waste_types"] = [w.value for w in changes["accepted_waste_types"]]

    # The shift-window rule lives on the full model, so re-check it here against
    # the merged result rather than the partial patch.
    start = changes.get("shift_start", vehicle.shift_start)
    end = changes.get("shift_end", vehicle.shift_end)
    if start >= end:
        raise ValidationError("shift_start must be earlier than shift_end")

    for field, value in changes.items():
        setattr(vehicle, field, value)

    db.commit()
    db.refresh(vehicle)
    return vehicle


def delete_vehicle(db: Session, vehicle_id: int) -> None:
    db.delete(get_vehicle(db, vehicle_id))
    db.commit()


def update_position(db: Session, vehicle_id: int, payload: VehiclePositionUpdate) -> Vehicle:
    """Apply a GPS ping. Stale pings are ignored rather than rewinding the map."""
    vehicle = get_vehicle(db, vehicle_id)
    recorded_at = payload.recorded_at or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)

    if vehicle.last_position_at and recorded_at < vehicle.last_position_at:
        raise ValidationError(
            "A newer position is already recorded for this vehicle",
            details={"last_position_at": vehicle.last_position_at.isoformat()},
        )

    vehicle.current_lat = payload.latitude
    vehicle.current_lon = payload.longitude
    vehicle.last_position_at = recorded_at
    if payload.status is not None:
        vehicle.status = payload.status
    if payload.current_load_liters is not None:
        vehicle.current_load_liters = payload.current_load_liters
    if payload.current_load_kg is not None:
        vehicle.current_load_kg = payload.current_load_kg

    db.commit()
    db.refresh(vehicle)
    return vehicle
