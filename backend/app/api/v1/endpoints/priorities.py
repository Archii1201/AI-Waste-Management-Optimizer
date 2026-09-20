"""Collection prioritisation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.enums import WasteType
from app.schemas.priority import (
    PrioritizedBinOut,
    PriorityComponentsOut,
    PrioritySummary,
)
from app.services import prioritization

router = APIRouter(prefix="/priorities", tags=["prioritization"])


def _to_out(item: prioritization.PrioritizedBin) -> PrioritizedBinOut:
    bin_obj = item.bin
    return PrioritizedBinOut(
        bin_id=bin_obj.id,
        code=bin_obj.code,
        label=bin_obj.label,
        zone_id=bin_obj.zone_id,
        latitude=bin_obj.latitude,
        longitude=bin_obj.longitude,
        waste_type=bin_obj.waste_type,
        capacity_liters=bin_obj.capacity_liters,
        fill_level=bin_obj.current_fill_level,
        score=item.score,
        tier=item.tier,
        components=PriorityComponentsOut(**item.components.as_dict()),
        weights=prioritization.WEIGHTS,
        hours_to_full=item.hours_to_full,
        is_overdue=item.is_overdue,
        distance_km=item.distance_km,
        expected_volume_liters=item.expected_volume_liters,
        expected_weight_kg=item.expected_weight_kg,
        reasons=item.reasons,
    )


@router.get(
    "/summary", response_model=PrioritySummary, summary="Priority tier breakdown"
)
def summary(
    zone_id: int | None = Query(None),
    db: Session = Depends(get_db),
) -> PrioritySummary:
    ranked = prioritization.prioritize(db, zone_id=zone_id)

    return PrioritySummary(
        total=len(ranked),
        by_tier=prioritization.tier_counts(ranked),
        total_volume_liters=round(sum(i.expected_volume_liters for i in ranked), 2),
        total_weight_kg=round(sum(i.expected_weight_kg for i in ranked), 3),
        bins_over_threshold=sum(
            1
            for i in ranked
            if i.bin.current_fill_level
            >= (i.bin.fill_threshold_override or settings.bin_full_threshold)
        ),
        weights=prioritization.WEIGHTS,
    )


@router.get("", response_model=list[PrioritizedBinOut], summary="Ranked collection queue")
def list_priorities(
    zone_id: int | None = Query(None, description="Restrict to one zone"),
    waste_type: list[WasteType] | None = Query(None, description="Filter by stream"),
    origin_lat: float | None = Query(None, ge=-90, le=90),
    origin_lon: float | None = Query(None, ge=-180, le=180),
    min_score: float | None = Query(None, ge=0, le=1),
    min_fill: float | None = Query(None, ge=0, le=100),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[PrioritizedBinOut]:
    """Bins ranked by collection priority, most urgent first.

    Supplying `origin_lat`/`origin_lon` (normally a depot) activates the
    proximity term, which breaks ties between bins of comparable urgency.
    """
    origin = (
        (origin_lat, origin_lon)
        if origin_lat is not None and origin_lon is not None
        else None
    )

    ranked = prioritization.prioritize(
        db,
        zone_id=zone_id,
        waste_types=waste_type,
        origin=origin,
        min_score=min_score,
        min_fill=min_fill,
        limit=limit,
    )
    return [_to_out(item) for item in ranked]
