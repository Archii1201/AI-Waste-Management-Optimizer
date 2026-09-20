"""Read-only operational what-if analysis.

Reuses prioritisation and the OR-Tools solver. Never writes bins, routes,
alerts, collections, vehicles or predictions.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.services import prioritization, route_service
from app.services.route_service import DEFAULT_MIN_PRIORITY_SCORE


def evaluate(
    db: Session,
    *,
    extra_rounds: int = 0,
    fleet_count: int | None = None,
    min_priority_score: float | None = None,
    horizon_hours: int | None = None,
) -> dict:
    if extra_rounds not in (0, 1, 2):
        raise ValidationError("extra_rounds must be 0, 1 or 2")
    if horizon_hours is not None and horizon_hours not in (24, 48, 72):
        raise ValidationError("horizon_hours must be 24, 48 or 72")

    all_vehicles = route_service._available_vehicles(db, None)
    if fleet_count is not None:
        if fleet_count < 1 or fleet_count > len(all_vehicles):
            raise ValidationError(
                f"fleet_count must be between 1 and {len(all_vehicles)} for the current fleet"
            )
        scenario_ids = [vehicle.id for vehicle in all_vehicles[:fleet_count]]
    else:
        scenario_ids = [vehicle.id for vehicle in all_vehicles]

    score = (
        DEFAULT_MIN_PRIORITY_SCORE
        if min_priority_score is None
        else min_priority_score
    )

    saved = route_service.list_routes(db, limit=50)
    origin = (
        (all_vehicles[0].depot_lat, all_vehicles[0].depot_lon) if all_vehicles else None
    )
    current_ranked = prioritization.prioritize(
        db,
        origin=origin,
        min_score=DEFAULT_MIN_PRIORITY_SCORE,
        limit=500,
    )
    current_overflow = sum(
        1
        for item in current_ranked
        if item.is_overdue or (item.hours_to_full is not None and item.hours_to_full <= 24)
    )

    scenario = route_service.preview_plan(
        db,
        vehicle_ids=scenario_ids,
        min_priority_score=score,
        extra_rounds=extra_rounds,
        horizon_hours=float(horizon_hours) if horizon_hours else None,
    )

    baseline = {
        "vehicles": len(all_vehicles),
        "routes": len(saved),
        "stops": sum(route.total_stops for route in saved),
        "distance_km": round(sum(route.total_distance_km for route in saved), 1),
        "overflow_risk_bins": current_overflow,
        "priority_bins": len(current_ranked),
        "collection_workload": sum(route.total_stops for route in saved),
    }
    proposed = {
        "vehicles": scenario["vehicles"],
        "routes": len(scenario["routes"]),
        "stops": scenario["total_stops"],
        "distance_km": round(scenario["total_distance_km"], 1),
        "overflow_risk_bins": scenario["unassigned_overflow_risk_bins"],
        "priority_bins": scenario["priority_bins"],
        "collection_workload": scenario["total_stops"],
    }

    return {
        "read_only": True,
        "label": "WHAT-IF — NOT DISPATCHED",
        "inputs": {
            "extra_rounds": extra_rounds,
            "fleet_count": len(scenario_ids),
            "min_priority_score": score,
            "horizon_hours": horizon_hours,
        },
        "baseline": baseline,
        "scenario": proposed,
        "routes": scenario["routes"],
        "deferred_bins": scenario["deferred_bins"],
        "solver_status": scenario["solver_status"],
        "notes": {
            "overflow_risk_bins": "Baseline: bins forecast to overflow within 24h. Scenario: those still unassigned after the what-if plan.",
            "collection_workload": "Stops on current stored routes versus stops in the what-if plan.",
            "distance_km": "Kilometres on stored routes versus the unsaved what-if solver output.",
        },
    }
