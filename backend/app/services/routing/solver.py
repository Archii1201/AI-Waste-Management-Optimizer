"""Vehicle routing with OR-Tools.

The problem is a Capacitated VRP with optional visits: a fleet leaves a depot,
services a subset of bins and returns, respecting each vehicle's volume and
weight capacity, its shift length and which waste streams it can carry.

Why optional visits matter: on a bad day the prioritised list holds more waste
than the fleet can lift. A solver forced to serve every bin would simply report
"infeasible" and produce nothing. Instead each bin gets a *disjunction* — the
solver may drop it at a penalty proportional to its priority score. Low-priority
bins are shed first, the plan stays feasible, and dropped bins are returned
explicitly so a dispatcher sees what was deferred rather than discovering it
from a complaint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.core.config import settings
from app.core.logging import get_logger
from app.services.prioritization import PrioritizedBin
from app.services.routing.distance import TravelMatrix, build_matrix

logger = get_logger(__name__)

# OR-Tools works in integers, so distances are carried in metres and weights in
# grams. Using kilometres directly would round every short hop to zero.
METRES_PER_KM = 1000
GRAMS_PER_KG = 1000

# Cost of dropping a bin, scaled by its priority. Must exceed any plausible
# detour cost, or the solver will happily abandon an urgent bin to save 2km.
BASE_DROP_PENALTY_METRES = 150_000
MAX_DROP_PENALTY_METRES = 2_000_000

# Time spent tipping at the disposal site once the truck fills up.
UNLOAD_MINUTES = 20


@dataclass
class VehiclePlan:
    """A vehicle's parameters as the solver sees them."""

    vehicle_id: int
    code: str
    depot: tuple[float, float]
    capacity_liters: float
    capacity_kg: float
    shift_minutes: float
    avg_speed_kmph: float
    cost_per_km: float
    accepted_waste_types: list[str] | None


@dataclass
class SolvedStop:
    bin_id: int
    sequence: int
    priority_score: float
    distance_from_previous_km: float
    travel_time_minutes: float
    service_time_minutes: float
    cumulative_minutes: float
    expected_volume_liters: float
    expected_weight_kg: float


@dataclass
class SolvedRoute:
    vehicle_id: int
    vehicle_code: str
    stops: list[SolvedStop]
    total_distance_km: float
    total_duration_minutes: float
    planned_volume_liters: float
    planned_weight_kg: float
    estimated_cost: float
    baseline_distance_km: float

    @property
    def stop_count(self) -> int:
        return len(self.stops)


@dataclass
class SolverResult:
    routes: list[SolvedRoute] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)
    status: str = "not_run"
    solve_seconds: float = 0.0
    matrix_source: str = "none"
    params: dict = field(default_factory=dict)

    @property
    def total_distance_km(self) -> float:
        return round(sum(r.total_distance_km for r in self.routes), 3)

    @property
    def baseline_distance_km(self) -> float:
        return round(sum(r.baseline_distance_km for r in self.routes), 3)


def _drop_penalty(priority_score: float) -> int:
    """Higher priority, higher cost to skip."""
    span = MAX_DROP_PENALTY_METRES - BASE_DROP_PENALTY_METRES
    return int(BASE_DROP_PENALTY_METRES + span * min(1.0, max(0.0, priority_score)))


def _compatible(vehicle: VehiclePlan, waste_type: str) -> bool:
    if not vehicle.accepted_waste_types:
        return True
    return waste_type in vehicle.accepted_waste_types


def solve(
    candidates: list[PrioritizedBin],
    vehicles: list[VehiclePlan],
    *,
    time_limit_seconds: int | None = None,
    prefer_osrm: bool = True,
) -> SolverResult:
    """Assign prioritised bins to vehicles and order each vehicle's stops."""
    if not candidates or not vehicles:
        return SolverResult(status="no_input")

    time_limit = time_limit_seconds or settings.route_solver_time_limit_seconds
    started = time.perf_counter()

    # Node 0..n-1 are depots (one per vehicle), the rest are bins. Modelling a
    # depot per vehicle rather than one shared depot lets a fleet operate from
    # several yards without changing the formulation.
    depot_nodes = list(range(len(vehicles)))
    points = [v.depot for v in vehicles] + [
        (c.bin.latitude, c.bin.longitude) for c in candidates
    ]
    bin_offset = len(vehicles)

    avg_speed = sum(v.avg_speed_kmph for v in vehicles) / len(vehicles)
    matrix = build_matrix(points, avg_speed_kmph=avg_speed, prefer_osrm=prefer_osrm)

    manager = pywrapcp.RoutingIndexManager(
        len(points), len(vehicles), depot_nodes, depot_nodes
    )
    routing = pywrapcp.RoutingModel(manager)

    # ---------- Objective: distance ----------
    def distance_callback(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(matrix.distance_km[i][j] * METRES_PER_KM)

    transit_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    # ---------- Constraint: volume capacity ----------
    def volume_callback(from_index: int) -> int:
        node = manager.IndexToNode(from_index)
        if node < bin_offset:
            return 0
        return int(candidates[node - bin_offset].expected_volume_liters)

    volume_index = routing.RegisterUnaryTransitCallback(volume_callback)
    routing.AddDimensionWithVehicleCapacity(
        volume_index,
        0,
        [int(v.capacity_liters) for v in vehicles],
        True,
        "Volume",
    )

    # ---------- Constraint: weight capacity ----------
    # Modelled separately because light streams fill a truck by volume while
    # glass and organics hit the axle limit long before the body is full.
    def weight_callback(from_index: int) -> int:
        node = manager.IndexToNode(from_index)
        if node < bin_offset:
            return 0
        return int(candidates[node - bin_offset].expected_weight_kg * GRAMS_PER_KG)

    weight_index = routing.RegisterUnaryTransitCallback(weight_callback)
    routing.AddDimensionWithVehicleCapacity(
        weight_index,
        0,
        [int(v.capacity_kg * GRAMS_PER_KG) for v in vehicles],
        True,
        "Weight",
    )

    # ---------- Constraint: shift duration ----------
    service_minutes = settings.default_service_time_minutes

    def time_callback(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        travel = matrix.duration_minutes[i][j]
        service = 0 if i < bin_offset else service_minutes
        return int(round(travel + service))

    time_index = routing.RegisterTransitCallback(time_callback)
    routing.AddDimensionWithVehicleCapacity(
        time_index,
        0,
        [
            int(min(v.shift_minutes, settings.max_route_duration_minutes))
            for v in vehicles
        ],
        True,
        "Time",
    )

    # ---------- Constraint: waste-stream compatibility ----------
    # Expressed by restricting which vehicles may visit each node, rather than
    # by penalising bad assignments, so an incompatible plan is impossible
    # rather than merely expensive.
    for position, candidate in enumerate(candidates):
        node = bin_offset + position
        index = manager.NodeToIndex(node)
        allowed = [
            vehicle_index
            for vehicle_index, vehicle in enumerate(vehicles)
            if _compatible(vehicle, candidate.bin.waste_type.value)
        ]
        if not allowed:
            # No truck in the fleet can take this stream; drop it immediately
            # with a reason rather than letting the solver fail silently.
            routing.AddDisjunction([index], 0)
            continue

        if len(allowed) < len(vehicles):
            routing.SetAllowedVehiclesForIndex(allowed, index)
        routing.AddDisjunction([index], _drop_penalty(candidate.score))

    # ---------- Search ----------
    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    # Guided local search escapes the local optimum that a greedy nearest-
    # neighbour construction always lands in; on city-scale instances it is
    # worth several percent of distance.
    search.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search.time_limit.FromSeconds(time_limit)

    solution = routing.SolveWithParameters(search)
    elapsed = round(time.perf_counter() - started, 3)

    result = SolverResult(
        status=routing.status_name() if hasattr(routing, "status_name") else str(routing.status()),
        solve_seconds=elapsed,
        matrix_source=matrix.source,
        params={
            "time_limit_seconds": time_limit,
            "service_time_minutes": service_minutes,
            "vehicles": len(vehicles),
            "candidates": len(candidates),
            "metaheuristic": "guided_local_search",
        },
    )

    if solution is None:
        result.status = "no_solution"
        result.deferred = [
            _deferred(c, "solver found no feasible plan") for c in candidates
        ]
        return result

    visited: set[int] = set()

    for vehicle_index, vehicle in enumerate(vehicles):
        index = routing.Start(vehicle_index)
        stops: list[SolvedStop] = []
        distance_km = 0.0
        minutes = 0.0
        volume = 0.0
        weight = 0.0
        sequence = 0

        while not routing.IsEnd(index):
            next_index = solution.Value(routing.NextVar(index))
            from_node = manager.IndexToNode(index)
            to_node = manager.IndexToNode(next_index)

            leg_km = matrix.distance_km[from_node][to_node]
            leg_minutes = matrix.duration_minutes[from_node][to_node]
            distance_km += leg_km
            minutes += leg_minutes

            if to_node >= bin_offset:
                candidate = candidates[to_node - bin_offset]
                visited.add(candidate.bin_id)
                sequence += 1
                minutes += service_minutes
                volume += candidate.expected_volume_liters
                weight += candidate.expected_weight_kg

                stops.append(
                    SolvedStop(
                        bin_id=candidate.bin_id,
                        sequence=sequence,
                        priority_score=candidate.score,
                        distance_from_previous_km=round(leg_km, 4),
                        travel_time_minutes=round(leg_minutes, 2),
                        service_time_minutes=float(service_minutes),
                        cumulative_minutes=round(minutes, 2),
                        expected_volume_liters=candidate.expected_volume_liters,
                        expected_weight_kg=candidate.expected_weight_kg,
                    )
                )

            index = next_index

        if not stops:
            continue

        # Baseline: serve the same bins in priority order without optimising
        # the sequence. That is what a fixed-schedule crew effectively does, so
        # the gap between the two is the honest efficiency claim.
        baseline = _baseline_distance(matrix, vehicle_index, stops, candidates, bin_offset)

        result.routes.append(
            SolvedRoute(
                vehicle_id=vehicle.vehicle_id,
                vehicle_code=vehicle.code,
                stops=stops,
                total_distance_km=round(distance_km, 3),
                total_duration_minutes=round(minutes + UNLOAD_MINUTES, 2),
                planned_volume_liters=round(volume, 2),
                planned_weight_kg=round(weight, 3),
                estimated_cost=round(distance_km * vehicle.cost_per_km, 2),
                baseline_distance_km=round(baseline, 3),
            )
        )

    for candidate in candidates:
        if candidate.bin_id not in visited:
            reason = (
                "no vehicle in the fleet accepts this waste stream"
                if not any(
                    _compatible(v, candidate.bin.waste_type.value) for v in vehicles
                )
                else "fleet capacity or shift length exhausted"
            )
            result.deferred.append(_deferred(candidate, reason))

    logger.info(
        "Solved %d routes covering %d bins, %d deferred, %.2fkm total (%s matrix, %.2fs)",
        len(result.routes),
        len(visited),
        len(result.deferred),
        result.total_distance_km,
        matrix.source,
        elapsed,
    )
    return result


def _deferred(candidate: PrioritizedBin, reason: str) -> dict:
    return {
        "bin_id": candidate.bin_id,
        "bin_code": candidate.bin.code,
        "priority_score": candidate.score,
        "tier": candidate.tier.value,
        "fill_level": candidate.bin.current_fill_level,
        "hours_to_full": candidate.hours_to_full,
        "reason": reason,
    }


def _baseline_distance(
    matrix: TravelMatrix,
    vehicle_index: int,
    stops: list[SolvedStop],
    candidates: list[PrioritizedBin],
    bin_offset: int,
) -> float:
    position_of = {c.bin_id: i for i, c in enumerate(candidates)}
    ordered = sorted(stops, key=lambda s: -s.priority_score)
    nodes = [vehicle_index] + [
        bin_offset + position_of[s.bin_id] for s in ordered
    ] + [vehicle_index]
    return matrix.route_distance(nodes)
