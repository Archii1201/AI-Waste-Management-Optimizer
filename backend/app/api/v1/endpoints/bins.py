"""Bin endpoints: the monitoring surface the dashboard map and lists read from."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ValidationError
from app.models.enums import BinStatus, WasteType
from app.schemas.bin import BinCreate, BinEmptyRequest, BinRead, BinSummary, BinUpdate
from app.schemas.collection import CollectionEventRead
from app.schemas.common import Page, PaginationParams
from app.schemas.reading import ReadingRead
from app.services import bin_service
from app.services.bin_service import BinFilters

router = APIRouter(prefix="/bins", tags=["bins"])


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    parts = bbox.split(",")
    if len(parts) != 4:
        raise ValidationError("bbox must be 'min_lat,min_lon,max_lat,max_lon'")
    try:
        min_lat, min_lon, max_lat, max_lon = (float(p) for p in parts)
    except ValueError as exc:
        raise ValidationError("bbox values must be numeric") from exc
    if min_lat > max_lat or min_lon > max_lon:
        raise ValidationError("bbox minimum values must not exceed the maximums")
    return min_lat, min_lon, max_lat, max_lon


# Declared before `/{bin_id}` so the literal path is not swallowed by the
# integer path parameter.
@router.get("/summary", response_model=BinSummary, summary="Network-wide bin statistics")
def bin_summary(
    zone_id: int | None = Query(None, description="Restrict the roll-up to one zone"),
    db: Session = Depends(get_db),
) -> BinSummary:
    return bin_service.get_summary(db, zone_id)


@router.get("", response_model=Page[BinRead], summary="List bins with filters")
def list_bins(
    pagination: PaginationParams = Depends(),
    zone_id: int | None = Query(None),
    waste_type: list[WasteType] | None = Query(None, description="Repeatable"),
    bin_status: list[BinStatus] | None = Query(None, alias="status", description="Repeatable"),
    min_fill: float | None = Query(None, ge=0, le=100),
    max_fill: float | None = Query(None, ge=0, le=100),
    needs_collection: bool | None = Query(
        None, description="Filter to bins at or above their collection threshold"
    ),
    search: str | None = Query(None, description="Matches code, label or address"),
    bbox: str | None = Query(None, description="Map viewport as 'min_lat,min_lon,max_lat,max_lon'"),
    lat: float | None = Query(None, ge=-90, le=90, description="Radius search centre"),
    lon: float | None = Query(None, ge=-180, le=180),
    radius_km: float | None = Query(None, gt=0, le=100),
    sort_by: str = Query("code", description="code | fill_level | capacity | last_reading_at | overflow_count | created_at"),
    sort_desc: bool = Query(False),
    db: Session = Depends(get_db),
) -> Page[BinRead]:
    near = None
    if lat is not None or lon is not None or radius_km is not None:
        if lat is None or lon is None or radius_km is None:
            raise ValidationError("Radius search needs lat, lon and radius_km together")
        near = (lat, lon, radius_km)

    if min_fill is not None and max_fill is not None and min_fill > max_fill:
        raise ValidationError("min_fill cannot exceed max_fill")

    filters = BinFilters(
        zone_id=zone_id,
        waste_types=waste_type,
        statuses=bin_status,
        min_fill=min_fill,
        max_fill=max_fill,
        needs_collection=needs_collection,
        search=search,
        bbox=_parse_bbox(bbox),
        near=near,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
    rows, total = bin_service.list_bins(
        db, filters, offset=pagination.offset, limit=pagination.limit
    )
    return Page.build(
        [BinRead.model_validate(r) for r in rows], total, pagination.page, pagination.page_size
    )


@router.post("", response_model=BinRead, status_code=status.HTTP_201_CREATED)
def create_bin(payload: BinCreate, db: Session = Depends(get_db)) -> BinRead:
    return BinRead.model_validate(bin_service.create_bin(db, payload))


@router.get("/by-code/{code}", response_model=BinRead, summary="Look up a bin by its printed code")
def get_bin_by_code(code: str, db: Session = Depends(get_db)) -> BinRead:
    return BinRead.model_validate(bin_service.get_bin_by_code(db, code))


@router.get("/{bin_id}", response_model=BinRead)
def get_bin(bin_id: int, db: Session = Depends(get_db)) -> BinRead:
    return BinRead.model_validate(bin_service.get_bin(db, bin_id))


@router.patch("/{bin_id}", response_model=BinRead)
def update_bin(bin_id: int, payload: BinUpdate, db: Session = Depends(get_db)) -> BinRead:
    return BinRead.model_validate(bin_service.update_bin(db, bin_id, payload))


@router.delete("/{bin_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_bin(bin_id: int, db: Session = Depends(get_db)) -> None:
    bin_service.delete_bin(db, bin_id)


@router.get(
    "/{bin_id}/readings",
    response_model=list[ReadingRead],
    summary="Fill-level history, oldest first",
)
def bin_readings(
    bin_id: int,
    start: datetime | None = Query(None, description="ISO timestamp, inclusive"),
    end: datetime | None = Query(None, description="ISO timestamp, inclusive"),
    limit: int = Query(1000, ge=1, le=10_000),
    db: Session = Depends(get_db),
) -> list[ReadingRead]:
    rows = bin_service.get_readings(db, bin_id, start=start, end=end, limit=limit)
    return [ReadingRead.model_validate(r) for r in rows]


@router.get("/{bin_id}/collections", response_model=list[CollectionEventRead])
def bin_collections(
    bin_id: int,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CollectionEventRead]:
    rows = bin_service.get_collections(db, bin_id, limit=limit)
    return [CollectionEventRead.model_validate(r) for r in rows]


@router.post(
    "/{bin_id}/empty",
    response_model=CollectionEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a manual collection and reset the bin",
)
def empty_bin(
    bin_id: int, payload: BinEmptyRequest, db: Session = Depends(get_db)
) -> CollectionEventRead:
    return CollectionEventRead.model_validate(bin_service.empty_bin(db, bin_id, payload))
