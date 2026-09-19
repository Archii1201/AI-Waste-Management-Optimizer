"""Vehicle endpoints, including the live position feed for the map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import VehicleStatus
from app.schemas.common import Page, PaginationParams
from app.schemas.vehicle import (
    VehicleCreate,
    VehiclePositionUpdate,
    VehicleRead,
    VehicleUpdate,
)
from app.services import vehicle_service

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("", response_model=Page[VehicleRead])
def list_vehicles(
    pagination: PaginationParams = Depends(),
    vehicle_status: VehicleStatus | None = Query(None, alias="status"),
    is_active: bool | None = Query(None),
    db: Session = Depends(get_db),
) -> Page[VehicleRead]:
    rows, total = vehicle_service.list_vehicles(
        db,
        offset=pagination.offset,
        limit=pagination.limit,
        status=vehicle_status,
        is_active=is_active,
    )
    return Page.build(
        [VehicleRead.model_validate(r) for r in rows],
        total,
        pagination.page,
        pagination.page_size,
    )


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
def create_vehicle(payload: VehicleCreate, db: Session = Depends(get_db)) -> VehicleRead:
    return VehicleRead.model_validate(vehicle_service.create_vehicle(db, payload))


@router.get("/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(vehicle_id: int, db: Session = Depends(get_db)) -> VehicleRead:
    return VehicleRead.model_validate(vehicle_service.get_vehicle(db, vehicle_id))


@router.patch("/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: int, payload: VehicleUpdate, db: Session = Depends(get_db)
) -> VehicleRead:
    return VehicleRead.model_validate(vehicle_service.update_vehicle(db, vehicle_id, payload))


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_vehicle(vehicle_id: int, db: Session = Depends(get_db)) -> None:
    vehicle_service.delete_vehicle(db, vehicle_id)


@router.post(
    "/{vehicle_id}/position",
    response_model=VehicleRead,
    summary="Report a GPS position for live tracking",
)
def update_position(
    vehicle_id: int, payload: VehiclePositionUpdate, db: Session = Depends(get_db)
) -> VehicleRead:
    return VehicleRead.model_validate(vehicle_service.update_position(db, vehicle_id, payload))
