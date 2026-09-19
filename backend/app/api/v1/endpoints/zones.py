"""Zone endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import Page, PaginationParams
from app.schemas.zone import ZoneCreate, ZoneRead, ZoneStats, ZoneUpdate
from app.services import zone_service

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("", response_model=Page[ZoneRead])
def list_zones(
    pagination: PaginationParams = Depends(), db: Session = Depends(get_db)
) -> Page[ZoneRead]:
    rows, total = zone_service.list_zones(db, offset=pagination.offset, limit=pagination.limit)
    return Page.build(
        [ZoneRead.model_validate(r) for r in rows], total, pagination.page, pagination.page_size
    )


@router.post("", response_model=ZoneRead, status_code=status.HTTP_201_CREATED)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)) -> ZoneRead:
    return ZoneRead.model_validate(zone_service.create_zone(db, payload))


@router.get("/{zone_id}", response_model=ZoneRead)
def get_zone(zone_id: int, db: Session = Depends(get_db)) -> ZoneRead:
    return ZoneRead.model_validate(zone_service.get_zone(db, zone_id))


@router.patch("/{zone_id}", response_model=ZoneRead)
def update_zone(zone_id: int, payload: ZoneUpdate, db: Session = Depends(get_db)) -> ZoneRead:
    return ZoneRead.model_validate(zone_service.update_zone(db, zone_id, payload))


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_zone(zone_id: int, db: Session = Depends(get_db)) -> None:
    zone_service.delete_zone(db, zone_id)


@router.get("/{zone_id}/stats", response_model=ZoneStats, summary="Bin roll-up for one zone")
def zone_stats(zone_id: int, db: Session = Depends(get_db)) -> ZoneStats:
    return zone_service.get_zone_stats(db, zone_id)
