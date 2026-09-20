"""Tests for route optimization.

Every test runs with `prefer_osrm=False`. The solver's job is to respect
capacity, compatibility and shift constraints, and that must be verified
without depending on a public OSRM server being reachable or fast.
"""

from __future__ import annotations

from datetime import time

import pytest

from app.core.exceptions import OptimizationError, ValidationError
from app.models.bin import Bin
from app.models.collection import CollectionEvent
from app.models.enums import (
    BinStatus,
    RouteStatus,
    StopStatus,
    VehicleStatus,
    VehicleType,
    WasteType,
)
from app.models.route import Route
from app.models.vehicle import Vehicle
from app.models.zone import Zone
from app.services import prioritization, route_service
from app.services.routing.distance import (
    URBAN_DETOUR_FACTOR,
    haversine_matrix,
    osrm_matrix,
)
from app.services.routing.solver import VehiclePlan, solve

DEPOT = (19.0760, 72.8777)


@pytest.fixture
def zone_row(db):
    zone = Zone(
        code="RT-Z",
        name="Route Zone",
        zone_type="commercial",
        center_lat=DEPOT[0],
        center_lon=DEPOT[1],
        population_served=20_000,
    )
    db.add(zone)
    db.commit()
    return zone


def make_bin(
    db,
    zone_row,
    code: str,
    *,
    fill: float = 90.0,
    waste_type: WasteType = WasteType.MIXED,
    capacity: float = 660.0,
    lat: float | None = None,
    lon: float | None = None,
    index: int = 0,
) -> Bin:
    bin_obj = Bin(
        code=code,
        zone_id=zone_row.id,
        latitude=lat if lat is not None else DEPOT[0] + 0.004 * (index + 1),
        longitude=lon if lon is not None else DEPOT[1] + 0.004 * (index + 1),
        capacity_liters=capacity,
        waste_type=waste_type,
        status=BinStatus.ACTIVE,
        current_fill_level=fill,
    )
    db.add(bin_obj)
    db.commit()
    return bin_obj


def make_vehicle(
    db,
    code: str,
    *,
    capacity_liters: float = 8000.0,
    capacity_kg: float = 5000.0,
    accepted: list[str] | None = None,
    shift=(time(6, 0), time(14, 0)),
    status: VehicleStatus = VehicleStatus.IDLE,
    is_active: bool = True,
) -> Vehicle:
    vehicle = Vehicle(
        code=code,
        vehicle_type=VehicleType.COMPACTOR,
        capacity_liters=capacity_liters,
        capacity_kg=capacity_kg,
        accepted_waste_types=accepted,
        depot_lat=DEPOT[0],
        depot_lon=DEPOT[1],
        shift_start=shift[0],
        shift_end=shift[1],
        status=status,
        is_active=is_active,
    )
    db.add(vehicle)
    db.commit()
    return vehicle


def plan_from(vehicle: Vehicle) -> VehiclePlan:
    return VehiclePlan(
        vehicle_id=vehicle.id,
        code=vehicle.code,
        depot=(vehicle.depot_lat, vehicle.depot_lon),
        capacity_liters=vehicle.capacity_liters,
        capacity_kg=vehicle.capacity_kg,
        shift_minutes=480.0,
        avg_speed_kmph=vehicle.avg_speed_kmph,
        cost_per_km=vehicle.cost_per_km,
        accepted_waste_types=vehicle.accepted_waste_types,
    )


# ---------------------------------------------------------------------------
# Distance matrix
# ---------------------------------------------------------------------------
def test_haversine_matrix_is_symmetric_with_a_zero_diagonal():
    matrix = haversine_matrix([DEPOT, (19.10, 72.90), (19.05, 72.85)], avg_speed_kmph=20)

    assert matrix.size == 3
    for i in range(3):
        assert matrix.distance_km[i][i] == 0.0
        for j in range(3):
            assert matrix.distance_km[i][j] == matrix.distance_km[j][i]


def test_fallback_distances_exceed_straight_line():
    """Real roads are never shorter than the crow flies."""
    from app.services.geo import haversine_km

    a, b = DEPOT, (19.10, 72.90)
    matrix = haversine_matrix([a, b], avg_speed_kmph=20)
    straight = haversine_km(a[0], a[1], b[0], b[1])

    assert matrix.distance_km[0][1] == pytest.approx(straight * URBAN_DETOUR_FACTOR, rel=1e-3)
    assert matrix.distance_km[0][1] > straight


def test_durations_follow_from_speed():
    matrix = haversine_matrix([DEPOT, (19.20, 72.90)], avg_speed_kmph=30)

    expected = matrix.distance_km[0][1] / 30 * 60
    assert matrix.duration_minutes[0][1] == pytest.approx(expected, rel=1e-3)


def test_route_distance_sums_the_legs():
    matrix = haversine_matrix([DEPOT, (19.10, 72.90), (19.05, 72.85)], avg_speed_kmph=20)

    total = matrix.route_distance([0, 1, 2, 0])

    assert total == pytest.approx(
        matrix.distance_km[0][1] + matrix.distance_km[1][2] + matrix.distance_km[2][0]
    )


def test_osrm_is_skipped_when_disabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "osrm_enabled", False)

    assert osrm_matrix([DEPOT, (19.1, 72.9)]) is None


def test_osrm_is_skipped_for_oversized_requests():
    points = [(19.0 + i * 0.001, 72.8) for i in range(150)]

    assert osrm_matrix(points) is None


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------
def _candidates(db, zone_row, count: int, **kwargs) -> list:
    for index in range(count):
        make_bin(db, zone_row, f"RB-{index}", index=index, **kwargs)
    return prioritization.prioritize(db, origin=DEPOT)


def test_solver_visits_every_bin_when_capacity_allows(db, zone_row):
    candidates = _candidates(db, zone_row, 6)
    vehicle = make_vehicle(db, "V-1")

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=3, prefer_osrm=False)

    visited = {stop.bin_id for route in result.routes for stop in route.stops}
    assert visited == {c.bin_id for c in candidates}
    assert result.deferred == []


def test_solver_returns_nothing_without_input(db):
    assert solve([], [], prefer_osrm=False).status == "no_input"


def test_stops_are_sequenced_from_one(db, zone_row):
    candidates = _candidates(db, zone_row, 5)
    vehicle = make_vehicle(db, "V-SEQ")

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=3, prefer_osrm=False)
    route = result.routes[0]

    assert [stop.sequence for stop in route.stops] == list(range(1, len(route.stops) + 1))


def test_volume_capacity_is_respected_and_excess_deferred(db, zone_row):
    # Ten full 660L bins is 5,940L; a 1,500L truck cannot take them all.
    candidates = _candidates(db, zone_row, 10, fill=100.0)
    vehicle = make_vehicle(db, "V-SMALL", capacity_liters=1500.0, capacity_kg=100_000.0)

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=5, prefer_osrm=False)

    assert result.deferred
    for route in result.routes:
        assert route.planned_volume_liters <= 1500.0


def test_weight_capacity_binds_independently_of_volume(db, zone_row):
    """Glass fills a truck by mass long before it fills it by volume."""
    for index in range(8):
        make_bin(
            db, zone_row, f"GB-{index}", index=index, fill=100.0, waste_type=WasteType.GLASS
        )
    candidates = prioritization.prioritize(db, origin=DEPOT)

    vehicle = make_vehicle(db, "V-HEAVY", capacity_liters=100_000.0, capacity_kg=400.0)
    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=5, prefer_osrm=False)

    assert result.deferred
    for route in result.routes:
        assert route.planned_weight_kg <= 400.0


def test_a_vehicle_never_receives_a_stream_it_cannot_carry(db, zone_row):
    make_bin(db, zone_row, "RB-P", index=0, waste_type=WasteType.PLASTIC)
    make_bin(db, zone_row, "RB-O", index=1, waste_type=WasteType.ORGANIC)
    candidates = prioritization.prioritize(db, origin=DEPOT)

    recycling_only = make_vehicle(db, "V-REC", accepted=["plastic", "paper"])
    result = solve(
        candidates, [plan_from(recycling_only)], time_limit_seconds=5, prefer_osrm=False
    )

    visited = {stop.bin_id for route in result.routes for stop in route.stops}
    organic = next(c for c in candidates if c.bin.code == "RB-O")

    assert organic.bin_id not in visited
    assert any(d["bin_id"] == organic.bin_id for d in result.deferred)


def test_an_uncollectable_stream_is_deferred_with_a_reason(db, zone_row):
    make_bin(db, zone_row, "RB-ONLY", index=0, waste_type=WasteType.ORGANIC)
    candidates = prioritization.prioritize(db, origin=DEPOT)
    vehicle = make_vehicle(db, "V-GLASS", accepted=["glass"])

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=3, prefer_osrm=False)

    assert "accepts this waste stream" in result.deferred[0]["reason"]


def test_deferred_entries_carry_enough_context_to_act_on(db, zone_row):
    candidates = _candidates(db, zone_row, 8, fill=100.0)
    vehicle = make_vehicle(db, "V-TINY", capacity_liters=700.0, capacity_kg=100_000.0)

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=5, prefer_osrm=False)

    entry = result.deferred[0]
    assert {"bin_id", "bin_code", "priority_score", "tier", "fill_level", "reason"} <= set(entry)


def test_high_priority_bins_survive_a_capacity_squeeze(db, zone_row):
    """The drop penalty scales with priority, so urgent bins are shed last."""
    make_bin(db, zone_row, "RB-URGENT", index=0, fill=100.0, waste_type=WasteType.ORGANIC)
    for index in range(1, 7):
        make_bin(db, zone_row, f"RB-CALM-{index}", index=index, fill=60.0)

    candidates = prioritization.prioritize(db, origin=DEPOT)
    vehicle = make_vehicle(db, "V-SQUEEZE", capacity_liters=1200.0, capacity_kg=100_000.0)

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=8, prefer_osrm=False)

    visited = {stop.bin_id for route in result.routes for stop in route.stops}
    urgent = next(c for c in candidates if c.bin.code == "RB-URGENT")

    assert result.deferred
    assert urgent.bin_id in visited


def test_work_is_spread_across_multiple_vehicles(db, zone_row):
    candidates = _candidates(db, zone_row, 12, fill=100.0)
    vehicles = [
        plan_from(make_vehicle(db, "V-A", capacity_liters=4000.0)),
        plan_from(make_vehicle(db, "V-B", capacity_liters=4000.0)),
    ]

    result = solve(candidates, vehicles, time_limit_seconds=8, prefer_osrm=False)

    assert len(result.routes) >= 2
    assert result.deferred == []


def test_the_optimised_order_is_no_worse_than_priority_order(db, zone_row):
    """The whole point of the solver: sequencing beats serving by urgency alone."""
    candidates = _candidates(db, zone_row, 8)
    vehicle = make_vehicle(db, "V-OPT")

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=8, prefer_osrm=False)
    route = result.routes[0]

    assert route.total_distance_km <= route.baseline_distance_km + 1e-6


def test_solver_reports_its_provenance(db, zone_row):
    candidates = _candidates(db, zone_row, 4)
    vehicle = make_vehicle(db, "V-PROV")

    result = solve(candidates, [plan_from(vehicle)], time_limit_seconds=3, prefer_osrm=False)

    assert result.matrix_source == "haversine"
    assert result.solve_seconds >= 0
    assert result.params["candidates"] == len(candidates)


# ---------------------------------------------------------------------------
# Planning and persistence
# ---------------------------------------------------------------------------
def test_plan_routes_persists_routes_and_stops(db, zone_row):
    _candidates(db, zone_row, 6)
    make_vehicle(db, "V-PLAN")

    report = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)

    assert report.routes
    route = db.get(Route, report.routes[0].id)
    assert route.total_stops == len(route.stops)
    assert route.status is RouteStatus.PLANNED
    assert route.optimization_params["matrix_source"] == "haversine"


def test_planned_arrivals_increase_along_the_route(db, zone_row):
    _candidates(db, zone_row, 6)
    make_vehicle(db, "V-TIME")

    report = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)
    arrivals = [stop.planned_arrival for stop in report.routes[0].stops]

    assert arrivals == sorted(arrivals)


def test_planning_without_vehicles_is_an_error(db, zone_row):
    _candidates(db, zone_row, 3)

    with pytest.raises(OptimizationError, match="No vehicles"):
        route_service.plan_routes(db, prefer_osrm=False)


def test_planning_with_nothing_worth_collecting_is_an_error(db, zone_row):
    make_bin(db, zone_row, "RB-EMPTY", index=0, fill=2.0)
    make_vehicle(db, "V-IDLE")

    with pytest.raises(OptimizationError, match="scored above"):
        route_service.plan_routes(db, min_priority_score=0.9, prefer_osrm=False)


def test_vehicles_in_maintenance_are_not_scheduled(db, zone_row):
    _candidates(db, zone_row, 3)
    make_vehicle(db, "V-DOWN", status=VehicleStatus.MAINTENANCE)
    make_vehicle(db, "V-UP")

    report = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)

    assert {r.vehicle.code for r in report.routes} == {"V-UP"}


def test_replanning_replaces_an_undispatched_plan(db, zone_row):
    _candidates(db, zone_row, 5)
    make_vehicle(db, "V-RE")

    first = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)
    route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)

    assert db.get(Route, first.routes[0].id) is None
    assert len(route_service.list_routes(db)) == 1


def test_replanning_leaves_a_dispatched_plan_alone(db, zone_row):
    """A truck already on the road must not have its plan deleted underneath it."""
    _candidates(db, zone_row, 5)
    make_vehicle(db, "V-LIVE")

    first = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)
    route_service.set_status(db, first.routes[0].id, RouteStatus.DISPATCHED)

    route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)

    assert db.get(Route, first.routes[0].id) is not None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@pytest.fixture
def planned_route(db, zone_row):
    _candidates(db, zone_row, 5)
    make_vehicle(db, "V-LC")
    report = route_service.plan_routes(db, time_limit_seconds=5, prefer_osrm=False)
    return report.routes[0]


def test_status_advances_through_the_lifecycle(db, planned_route):
    dispatched = route_service.set_status(db, planned_route.id, RouteStatus.DISPATCHED)
    assert dispatched.dispatched_at is not None
    assert dispatched.vehicle.status is VehicleStatus.EN_ROUTE

    started = route_service.set_status(db, planned_route.id, RouteStatus.IN_PROGRESS)
    assert started.started_at is not None

    done = route_service.set_status(db, planned_route.id, RouteStatus.COMPLETED)
    assert done.completed_at is not None
    assert done.vehicle.status is VehicleStatus.IDLE


def test_illegal_status_jumps_are_rejected(db, planned_route):
    with pytest.raises(ValidationError, match="Cannot move a route"):
        route_service.set_status(db, planned_route.id, RouteStatus.COMPLETED)


def test_completing_a_stop_empties_the_bin_and_records_the_collection(db, planned_route):
    stop = planned_route.stops[0]
    bin_before = db.get(Bin, stop.bin_id)
    fill_before = bin_before.current_fill_level

    route_service.set_status(db, planned_route.id, RouteStatus.DISPATCHED)
    completed = route_service.complete_stop(db, planned_route.id, stop.id)

    event = db.query(CollectionEvent).filter_by(bin_id=stop.bin_id).one()

    assert completed.status is StopStatus.COLLECTED
    assert db.get(Bin, stop.bin_id).current_fill_level == 0.0
    assert db.get(Bin, stop.bin_id).last_emptied_at is not None
    assert event.route_id == planned_route.id
    assert event.fill_level_before == pytest.approx(fill_before, abs=0.1)
    assert event.recyclable_kg + event.non_recyclable_kg == pytest.approx(
        event.weight_collected_kg, abs=0.01
    )


def test_completing_a_stop_loads_the_vehicle(db, planned_route):
    stop = planned_route.stops[0]

    route_service.complete_stop(db, planned_route.id, stop.id)

    assert planned_route.vehicle.current_load_liters > 0
    assert planned_route.vehicle.current_load_kg > 0


def test_a_stop_cannot_be_completed_twice(db, planned_route):
    stop = planned_route.stops[0]
    route_service.complete_stop(db, planned_route.id, stop.id)

    with pytest.raises(ValidationError, match="already marked collected"):
        route_service.complete_stop(db, planned_route.id, stop.id)


def test_finishing_every_stop_completes_the_route(db, planned_route):
    for stop in list(planned_route.stops):
        route_service.complete_stop(db, planned_route.id, stop.id)

    refreshed = route_service.get_route(db, planned_route.id)

    assert refreshed.status is RouteStatus.COMPLETED
    assert refreshed.completed_at is not None


def test_skipping_a_stop_records_the_reason(db, planned_route):
    stop = planned_route.stops[0]

    skipped = route_service.skip_stop(db, planned_route.id, stop.id, reason="Road closed")

    assert skipped.status is StopStatus.SKIPPED
    assert skipped.skip_reason == "Road closed"


def test_an_unknown_stop_is_a_404(db, planned_route):
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        route_service.complete_stop(db, planned_route.id, 999999)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def test_summary_is_empty_but_well_formed_without_routes(db):
    summary = route_service.route_summary(db)

    assert summary["routes"] == 0
    assert summary["distance_saved_pct"] is None


def test_summary_reports_the_efficiency_gain(db, planned_route):
    summary = route_service.route_summary(db)

    assert summary["routes"] == 1
    assert summary["total_distance_km"] > 0
    assert summary["distance_saved_km"] == pytest.approx(
        summary["baseline_distance_km"] - summary["total_distance_km"], abs=1e-6
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_optimize_endpoint_returns_a_plan(client, api_prefix, db, zone_row):
    _candidates(db, zone_row, 6)
    make_vehicle(db, "V-API")

    response = client.post(
        f"{api_prefix}/routes/optimize",
        json={"prefer_osrm": False, "time_limit_seconds": 5},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["routes"]
    assert body["matrix_source"] == "haversine"
    assert body["routes"][0]["stops"]
    assert body["routes"][0]["distance_saved_pct"] is not None


def test_optimize_endpoint_reports_no_vehicles(client, api_prefix, db, zone_row):
    _candidates(db, zone_row, 3)

    response = client.post(f"{api_prefix}/routes/optimize", json={"prefer_osrm": False})

    assert response.status_code == 422


def test_route_endpoints_round_trip(client, api_prefix, db, zone_row):
    _candidates(db, zone_row, 5)
    make_vehicle(db, "V-RT")

    created = client.post(
        f"{api_prefix}/routes/optimize",
        json={"prefer_osrm": False, "time_limit_seconds": 5},
    ).json()
    route_id = created["routes"][0]["id"]
    stop_id = created["routes"][0]["stops"][0]["id"]

    assert client.get(f"{api_prefix}/routes/{route_id}").status_code == 200
    assert len(client.get(f"{api_prefix}/routes").json()) == 1

    dispatched = client.post(
        f"{api_prefix}/routes/{route_id}/status", json={"status": "dispatched"}
    )
    assert dispatched.json()["status"] == "dispatched"

    completed = client.post(
        f"{api_prefix}/routes/{route_id}/stops/{stop_id}/complete", json={}
    )
    assert completed.json()["status"] == "collected"

    summary = client.get(f"{api_prefix}/routes/summary").json()
    assert summary["stops_completed"] == 1


def test_skip_endpoint(client, api_prefix, db, zone_row):
    _candidates(db, zone_row, 4)
    make_vehicle(db, "V-SKIP")

    created = client.post(
        f"{api_prefix}/routes/optimize",
        json={"prefer_osrm": False, "time_limit_seconds": 5},
    ).json()
    route_id = created["routes"][0]["id"]
    stop_id = created["routes"][0]["stops"][0]["id"]

    response = client.post(
        f"{api_prefix}/routes/{route_id}/stops/{stop_id}/skip",
        json={"reason": "Bin inaccessible"},
    )

    assert response.status_code == 200
    assert response.json()["skip_reason"] == "Bin inaccessible"


def test_unknown_route_returns_404(client, api_prefix):
    assert client.get(f"{api_prefix}/routes/999999").status_code == 404
