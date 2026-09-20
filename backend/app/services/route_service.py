"""Route planning, persistence and lifecycle.

Ties Step 7 to Step 8: prioritisation picks *which* bins are worth collecting,
the solver decides *who* collects them and *in what order*, and this module
stores the result so the dashboard, the driver view and the after-the-fact
analytics all read one agreed plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import settings
from app.core.exceptions import NotFoundError, OptimizationError, ValidationError
from app.core.logging import get_logger
from app.models.bin import Bin
from app.models.collection import CollectionEvent
from app.models.enums import (
    RouteStatus,
    StopStatus,
    VehicleStatus,
    WasteType,
)
from app.models.route import Route, RouteStop
from app.models.vehicle import Vehicle
from app.services import prioritization
from app.services.routing.solver import VehiclePlan, solve
from app.services.waste import estimate_weight_kg, split_recyclable_kg

logger = get_logger(__name__)

# Only bins worth a truck's time enter the solver. Sending every bin would make
# the problem needlessly large and invite the solver to add near-empty stops
# just because they sit conveniently on the way.
DEFAULT_MIN_PRIORITY_SCORE = 0.35
DEFAULT_MAX_CANDIDATES = 90


@dataclass
class PlanReport:
    routes: list[Route]
    deferred: list[dict]
    candidates_considered: int
    solver_status: str
    solve_seconds: float
    matrix_source: str

    @property
    def total_distance_km(self) -> float:
        return round(sum(r.total_distance_km for r in self.routes), 3)

    @property
    def total_stops(self) -> int:
        return sum(r.total_stops for r in self.routes)

    def __str__(self) -> str:
        return (
            f"{len(self.routes)} routes, {self.total_stops} stops, "
            f"{self.total_distance_km:.1f}km, {len(self.deferred)} deferred "
            f"({self.matrix_source} distances, {self.solve_seconds:.1f}s)"
        )


def _shift_minutes(vehicle: Vehicle) -> float:
    start = datetime.combine(date.today(), vehicle.shift_start)
    end = datetime.combine(date.today(), vehicle.shift_end)
    if end <= start:
        # An overnight shift wraps past midnight.
        end += timedelta(days=1)
    return (end - start).total_seconds() / 60.0


def _available_vehicles(db: Session, vehicle_ids: list[int] | None) -> list[Vehicle]:
    stmt = select(Vehicle).where(
        Vehicle.is_active.is_(True),
        Vehicle.status.notin_([VehicleStatus.MAINTENANCE, VehicleStatus.OFFLINE]),
    )
    if vehicle_ids:
        stmt = stmt.where(Vehicle.id.in_(vehicle_ids))
    return list(db.scalars(stmt.order_by(Vehicle.code)))


def _next_code(db: Session, planned_for: date, vehicle: Vehicle) -> str:
    """Route code for a vehicle-day, suffixed if one already exists.

    A dispatched route is never replaced, so replanning the same vehicle and
    day must not collide with the code it already holds.
    """
    base = f"RT-{planned_for:%Y%m%d}-{vehicle.code}"
    if db.scalar(select(Route.id).where(Route.code == base)) is None:
        return base

    suffix = 2
    while db.scalar(select(Route.id).where(Route.code == f"{base}-{suffix}")) is not None:
        suffix += 1
    return f"{base}-{suffix}"


def plan_routes(
    db: Session,
    *,
    planned_for: date | None = None,
    zone_id: int | None = None,
    vehicle_ids: list[int] | None = None,
    waste_types: list[WasteType] | None = None,
    min_priority_score: float = DEFAULT_MIN_PRIORITY_SCORE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    time_limit_seconds: int | None = None,
    prefer_osrm: bool = True,
    replace_existing: bool = True,
) -> PlanReport:
    """Build and store an optimised collection plan for one day."""
    planned_for = planned_for or datetime.now(timezone.utc).date()

    vehicles = _available_vehicles(db, vehicle_ids)
    if not vehicles:
        raise OptimizationError(
            "No vehicles available to plan with. Check the fleet is active and "
            "not all in maintenance."
        )

    # Distances are measured from the first depot, so the location term in the
    # priority score reflects the yard the fleet actually leaves from.
    origin = (vehicles[0].depot_lat, vehicles[0].depot_lon)

    candidates = prioritization.prioritize(
        db,
        zone_id=zone_id,
        waste_types=waste_types,
        origin=origin,
        min_score=min_priority_score,
        limit=max_candidates,
    )
    if not candidates:
        raise OptimizationError(
            f"No bins scored above {min_priority_score}. Nothing needs collecting, "
            "or predictions have not been refreshed yet."
        )

    plans = [
        VehiclePlan(
            vehicle_id=vehicle.id,
            code=vehicle.code,
            depot=(vehicle.depot_lat, vehicle.depot_lon),
            capacity_liters=vehicle.capacity_liters,
            capacity_kg=vehicle.capacity_kg,
            shift_minutes=_shift_minutes(vehicle),
            avg_speed_kmph=vehicle.avg_speed_kmph,
            cost_per_km=vehicle.cost_per_km,
            accepted_waste_types=vehicle.accepted_waste_types,
        )
        for vehicle in vehicles
    ]

    result = solve(
        candidates,
        plans,
        time_limit_seconds=time_limit_seconds,
        prefer_osrm=prefer_osrm,
    )

    if replace_existing:
        _clear_unstarted_routes(db, planned_for, [v.id for v in vehicles])

    by_id = {v.id: v for v in vehicles}
    stored: list[Route] = []

    for solved in result.routes:
        vehicle = by_id[solved.vehicle_id]
        start_at = datetime.combine(
            planned_for, vehicle.shift_start, tzinfo=timezone.utc
        )

        route = Route(
            code=_next_code(db, planned_for, vehicle),
            vehicle_id=vehicle.id,
            planned_for=planned_for,
            status=RouteStatus.PLANNED,
            total_stops=solved.stop_count,
            total_distance_km=solved.total_distance_km,
            total_duration_minutes=solved.total_duration_minutes,
            planned_volume_liters=solved.planned_volume_liters,
            planned_weight_kg=solved.planned_weight_kg,
            estimated_cost=solved.estimated_cost,
            baseline_distance_km=solved.baseline_distance_km,
            solver_status=result.status,
            solve_time_seconds=result.solve_seconds,
            optimization_params={**result.params, "matrix_source": result.matrix_source},
            deferred_bins=result.deferred or None,
        )

        for stop in solved.stops:
            route.stops.append(
                RouteStop(
                    bin_id=stop.bin_id,
                    sequence=stop.sequence,
                    status=StopStatus.PENDING,
                    priority_score=stop.priority_score,
                    distance_from_previous_km=stop.distance_from_previous_km,
                    travel_time_minutes=stop.travel_time_minutes,
                    service_time_minutes=stop.service_time_minutes,
                    planned_arrival=start_at + timedelta(minutes=stop.cumulative_minutes),
                    expected_volume_liters=stop.expected_volume_liters,
                    expected_weight_kg=stop.expected_weight_kg,
                )
            )

        db.add(route)
        stored.append(route)

    db.commit()
    for route in stored:
        db.refresh(route)

    report = PlanReport(
        routes=stored,
        deferred=result.deferred,
        candidates_considered=len(candidates),
        solver_status=result.status,
        solve_seconds=result.solve_seconds,
        matrix_source=result.matrix_source,
    )
    logger.info("Planned: %s", report)
    return report


def _clear_unstarted_routes(db: Session, planned_for: date, vehicle_ids: list[int]) -> None:
    """Replace only plans nobody has acted on.

    A route already dispatched or in progress reflects a truck physically on the
    road; silently deleting it would desynchronise the plan from reality.
    """
    stale_ids = list(
        db.scalars(
            select(Route.id).where(
                Route.planned_for == planned_for,
                Route.vehicle_id.in_(vehicle_ids),
                Route.status.in_([RouteStatus.PLANNED, RouteStatus.CANCELLED]),
            )
        )
    )
    if not stale_ids:
        return

    # Stops are removed first and flushed separately. Leaving it to the cascade
    # lets the new plan's stops be inserted before the old ones are gone, which
    # trips the per-route sequence uniqueness constraint.
    db.execute(delete(RouteStop).where(RouteStop.route_id.in_(stale_ids)))
    db.flush()

    db.execute(delete(Route).where(Route.id.in_(stale_ids)))
    db.flush()
    db.expire_all()


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def get_route(db: Session, route_id: int) -> Route:
    route = db.scalar(
        select(Route)
        .options(selectinload(Route.stops).joinedload(RouteStop.bin), joinedload(Route.vehicle))
        .where(Route.id == route_id)
    )
    if route is None:
        raise NotFoundError(f"Route {route_id} not found")
    return route


def list_routes(
    db: Session,
    *,
    planned_for: date | None = None,
    status: RouteStatus | None = None,
    vehicle_id: int | None = None,
    limit: int = 50,
) -> list[Route]:
    stmt = select(Route).options(
        selectinload(Route.stops), joinedload(Route.vehicle)
    )
    if planned_for is not None:
        stmt = stmt.where(Route.planned_for == planned_for)
    if status is not None:
        stmt = stmt.where(Route.status == status)
    if vehicle_id is not None:
        stmt = stmt.where(Route.vehicle_id == vehicle_id)

    return list(
        db.scalars(stmt.order_by(Route.planned_for.desc(), Route.code).limit(limit))
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
_ALLOWED_TRANSITIONS = {
    RouteStatus.PLANNED: {RouteStatus.DISPATCHED, RouteStatus.CANCELLED},
    RouteStatus.DISPATCHED: {RouteStatus.IN_PROGRESS, RouteStatus.CANCELLED},
    RouteStatus.IN_PROGRESS: {RouteStatus.COMPLETED, RouteStatus.CANCELLED},
    RouteStatus.COMPLETED: set(),
    RouteStatus.CANCELLED: set(),
}


def set_status(db: Session, route_id: int, status: RouteStatus) -> Route:
    """Advance a route through its lifecycle, rejecting impossible jumps."""
    route = get_route(db, route_id)

    if status not in _ALLOWED_TRANSITIONS[route.status]:
        raise ValidationError(
            f"Cannot move a route from {route.status.value} to {status.value}"
        )

    now = datetime.now(timezone.utc)
    route.status = status

    if status is RouteStatus.DISPATCHED:
        route.dispatched_at = now
        if route.vehicle:
            route.vehicle.status = VehicleStatus.EN_ROUTE
    elif status is RouteStatus.IN_PROGRESS:
        route.started_at = now
        if route.vehicle:
            route.vehicle.status = VehicleStatus.COLLECTING
    elif status is RouteStatus.COMPLETED:
        route.completed_at = now
        if route.vehicle:
            route.vehicle.status = VehicleStatus.IDLE
            route.vehicle.current_load_liters = 0.0
            route.vehicle.current_load_kg = 0.0
    elif status is RouteStatus.CANCELLED and route.vehicle:
        route.vehicle.status = VehicleStatus.IDLE

    db.commit()
    db.refresh(route)
    return route


def complete_stop(
    db: Session,
    route_id: int,
    stop_id: int,
    *,
    actual_fill_level: float | None = None,
    contamination_pct: float | None = None,
    notes: str | None = None,
) -> RouteStop:
    """Mark a stop collected, empty the bin and record the collection event.

    This is the point where a plan becomes measured reality: the bin is reset,
    a `CollectionEvent` is written for the tonnage analytics, and the vehicle's
    running load is updated so capacity checks stay honest mid-shift.
    """
    route = get_route(db, route_id)
    stop = next((s for s in route.stops if s.id == stop_id), None)
    if stop is None:
        raise NotFoundError(f"Stop {stop_id} is not part of route {route_id}")
    if stop.status is StopStatus.COLLECTED:
        raise ValidationError("Stop is already marked collected")

    bin_obj = db.get(Bin, stop.bin_id)
    if bin_obj is None:
        raise NotFoundError(f"Bin {stop.bin_id} no longer exists")

    now = datetime.now(timezone.utc)
    fill_before = (
        actual_fill_level if actual_fill_level is not None else bin_obj.current_fill_level
    )
    volume = bin_obj.capacity_liters * fill_before / 100.0
    weight = estimate_weight_kg(bin_obj.waste_type, volume)
    recyclable, non_recyclable = split_recyclable_kg(
        bin_obj.waste_type, weight, contamination_pct
    )

    hours_since_previous = None
    if bin_obj.last_emptied_at is not None:
        hours_since_previous = round(
            (now - bin_obj.last_emptied_at).total_seconds() / 3600.0, 2
        )

    db.add(
        CollectionEvent(
            bin_id=bin_obj.id,
            vehicle_id=route.vehicle_id,
            route_id=route.id,
            collected_at=now,
            fill_level_before=round(fill_before, 1),
            volume_collected_liters=round(volume, 2),
            weight_collected_kg=weight,
            recyclable_kg=recyclable,
            non_recyclable_kg=non_recyclable,
            waste_type=bin_obj.waste_type,
            contamination_pct=contamination_pct,
            was_overflowing=fill_before >= settings.bin_critical_threshold,
            hours_since_previous=hours_since_previous,
            notes=notes or f"Collected on optimised route {route.code}",
        )
    )

    bin_obj.current_fill_level = 0.0
    bin_obj.current_weight_kg = 0.0
    bin_obj.last_emptied_at = now

    stop.status = StopStatus.COLLECTED
    stop.actual_arrival = now

    if route.vehicle:
        route.vehicle.current_load_liters += volume
        route.vehicle.current_load_kg += weight
        route.vehicle.current_lat = bin_obj.latitude
        route.vehicle.current_lon = bin_obj.longitude
        route.vehicle.last_position_at = now

    # Finishing the last outstanding stop completes the route without the
    # dispatcher having to remember to close it.
    if all(s.status in (StopStatus.COLLECTED, StopStatus.SKIPPED) for s in route.stops):
        route.status = RouteStatus.COMPLETED
        route.completed_at = now
        if route.vehicle:
            route.vehicle.status = VehicleStatus.IDLE

    db.commit()
    db.refresh(stop)
    return stop


def skip_stop(db: Session, route_id: int, stop_id: int, *, reason: str) -> RouteStop:
    route = get_route(db, route_id)
    stop = next((s for s in route.stops if s.id == stop_id), None)
    if stop is None:
        raise NotFoundError(f"Stop {stop_id} is not part of route {route_id}")

    stop.status = StopStatus.SKIPPED
    stop.skip_reason = reason
    db.commit()
    db.refresh(stop)
    return stop


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def route_summary(db: Session, *, planned_for: date | None = None) -> dict:
    """Fleet-level view of a day's plan, including the efficiency claim."""
    stmt = select(Route)
    if planned_for is not None:
        stmt = stmt.where(Route.planned_for == planned_for)
    routes = list(db.scalars(stmt))

    if not routes:
        return {
            "routes": 0,
            "total_stops": 0,
            "total_distance_km": 0.0,
            "baseline_distance_km": 0.0,
            "distance_saved_km": 0.0,
            "distance_saved_pct": None,
            "total_duration_minutes": 0.0,
            "estimated_cost": 0.0,
            "planned_volume_liters": 0.0,
            "planned_weight_kg": 0.0,
            "by_status": {},
            "stops_completed": 0,
        }

    optimised = sum(r.total_distance_km for r in routes)
    baseline = sum(r.baseline_distance_km or 0.0 for r in routes)

    by_status: dict[str, int] = {}
    for route in routes:
        by_status[route.status.value] = by_status.get(route.status.value, 0) + 1

    completed = db.scalar(
        select(func.count())
        .select_from(RouteStop)
        .where(
            RouteStop.route_id.in_([r.id for r in routes]),
            RouteStop.status == StopStatus.COLLECTED,
        )
    )

    return {
        "routes": len(routes),
        "total_stops": sum(r.total_stops for r in routes),
        "total_distance_km": round(optimised, 3),
        "baseline_distance_km": round(baseline, 3),
        "distance_saved_km": round(baseline - optimised, 3),
        "distance_saved_pct": (
            round((1 - optimised / baseline) * 100.0, 1) if baseline > 0 else None
        ),
        "total_duration_minutes": round(sum(r.total_duration_minutes for r in routes), 1),
        "estimated_cost": round(sum(r.estimated_cost or 0.0 for r in routes), 2),
        "planned_volume_liters": round(sum(r.planned_volume_liters for r in routes), 2),
        "planned_weight_kg": round(sum(r.planned_weight_kg for r in routes), 3),
        "by_status": by_status,
        "stops_completed": completed or 0,
    }
