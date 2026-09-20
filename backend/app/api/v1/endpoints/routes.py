"""Route optimization endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import RouteStatus
from app.models.route import Route, RouteStop
from app.schemas.route import (
    CompleteStopRequest,
    PlanRequest,
    PlanResponse,
    RouteRead,
    RouteStopRead,
    RouteSummary,
    SkipStopRequest,
    StatusRequest,
)
from app.services import route_service

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("/summary", response_model=RouteSummary, summary="Fleet plan summary")
def summary(
    planned_for: date | None = Query(None, description="Defaults to all days"),
    db: Session = Depends(get_db),
) -> RouteSummary:
    """Distance, cost and the saving over an unoptimised visit order."""
    return RouteSummary(**route_service.route_summary(db, planned_for=planned_for))


@router.get("", response_model=list[RouteRead], summary="List routes")
def list_routes(
    planned_for: date | None = Query(None),
    route_status: RouteStatus | None = Query(None, alias="status"),
    vehicle_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[Route]:
    return route_service.list_routes(
        db,
        planned_for=planned_for,
        status=route_status,
        vehicle_id=vehicle_id,
        limit=limit,
    )


@router.post(
    "/optimize",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Build an optimised collection plan",
)
def optimize(payload: PlanRequest, db: Session = Depends(get_db)) -> PlanResponse:
    """Prioritise bins, then solve the vehicle routing problem over them.

    Bins the fleet cannot absorb are returned in `deferred_bins` with a reason,
    rather than being dropped silently.
    """
    report = route_service.plan_routes(
        db,
        planned_for=payload.planned_for,
        zone_id=payload.zone_id,
        vehicle_ids=payload.vehicle_ids,
        waste_types=payload.waste_types,
        min_priority_score=payload.min_priority_score,
        max_candidates=payload.max_candidates,
        time_limit_seconds=payload.time_limit_seconds,
        prefer_osrm=payload.prefer_osrm,
        replace_existing=payload.replace_existing,
    )

    return PlanResponse(
        routes=[RouteRead.model_validate(route) for route in report.routes],
        deferred_bins=report.deferred,
        candidates_considered=report.candidates_considered,
        solver_status=report.solver_status,
        solve_seconds=report.solve_seconds,
        matrix_source=report.matrix_source,
        total_distance_km=report.total_distance_km,
        total_stops=report.total_stops,
    )


@router.get("/{route_id}", response_model=RouteRead, summary="Fetch one route")
def get_route(route_id: int, db: Session = Depends(get_db)) -> Route:
    return route_service.get_route(db, route_id)


@router.post(
    "/{route_id}/status", response_model=RouteRead, summary="Advance a route's status"
)
def set_status(
    route_id: int, payload: StatusRequest, db: Session = Depends(get_db)
) -> Route:
    return route_service.set_status(db, route_id, payload.status)


@router.post(
    "/{route_id}/stops/{stop_id}/complete",
    response_model=RouteStopRead,
    summary="Mark a stop collected",
)
def complete_stop(
    route_id: int,
    stop_id: int,
    payload: CompleteStopRequest,
    db: Session = Depends(get_db),
) -> RouteStop:
    """Empties the bin, records the collection event and updates the truck load."""
    return route_service.complete_stop(
        db,
        route_id,
        stop_id,
        actual_fill_level=payload.actual_fill_level,
        contamination_pct=payload.contamination_pct,
        notes=payload.notes,
    )


@router.post(
    "/{route_id}/stops/{stop_id}/skip",
    response_model=RouteStopRead,
    summary="Skip a stop",
)
def skip_stop(
    route_id: int,
    stop_id: int,
    payload: SkipStopRequest,
    db: Session = Depends(get_db),
) -> RouteStop:
    return route_service.skip_stop(db, route_id, stop_id, reason=payload.reason)
