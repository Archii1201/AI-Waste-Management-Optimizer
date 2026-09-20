"""Route optimization contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import RouteStatus, StopStatus, WasteType


class RouteStopRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bin_id: int
    sequence: int
    status: StopStatus
    priority_score: float | None
    distance_from_previous_km: float
    travel_time_minutes: float
    service_time_minutes: float
    planned_arrival: datetime | None
    actual_arrival: datetime | None
    expected_volume_liters: float
    expected_weight_kg: float
    skip_reason: str | None


class RouteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    vehicle_id: int | None
    planned_for: date
    status: RouteStatus

    total_stops: int
    total_distance_km: float
    total_duration_minutes: float
    planned_volume_liters: float
    planned_weight_kg: float
    estimated_cost: float | None
    baseline_distance_km: float | None

    solver_status: str | None
    solve_time_seconds: float | None
    optimization_params: dict[str, Any] | None
    deferred_bins: list[dict[str, Any]] | None

    dispatched_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None

    stops: list[RouteStopRead] = []

    @computed_field  # type: ignore[prop-decorator]
    def distance_saved_pct(self) -> float | None:
        """Improvement over serving the same bins in unoptimised priority order."""
        if not self.baseline_distance_km or self.baseline_distance_km <= 0:
            return None
        return round((1 - self.total_distance_km / self.baseline_distance_km) * 100.0, 1)

    @computed_field  # type: ignore[prop-decorator]
    def stops_completed(self) -> int:
        return sum(1 for stop in self.stops if stop.status == StopStatus.COLLECTED)


class PlanRequest(BaseModel):
    planned_for: date | None = Field(
        None, description="Day the plan is for; defaults to today"
    )
    zone_id: int | None = None
    vehicle_ids: list[int] | None = Field(
        None, description="Restrict the plan to specific vehicles"
    )
    waste_types: list[WasteType] | None = None
    min_priority_score: float = Field(
        0.35, ge=0, le=1, description="Bins below this are not worth a truck's time"
    )
    max_candidates: int = Field(
        90, ge=1, le=300, description="Cap on bins sent to the solver"
    )
    time_limit_seconds: int | None = Field(None, ge=1, le=300)
    prefer_osrm: bool = Field(
        True, description="Use real road distances; falls back to straight-line"
    )
    replace_existing: bool = Field(
        True, description="Discard existing un-dispatched plans for the same day"
    )


class PlanResponse(BaseModel):
    routes: list[RouteRead]
    deferred_bins: list[dict[str, Any]]
    candidates_considered: int
    solver_status: str
    solve_seconds: float
    matrix_source: str
    total_distance_km: float
    total_stops: int


class StatusRequest(BaseModel):
    status: RouteStatus


class CompleteStopRequest(BaseModel):
    actual_fill_level: float | None = Field(
        None, ge=0, le=100, description="Override the sensor reading if the crew saw different"
    )
    contamination_pct: float | None = Field(None, ge=0, le=100)
    notes: str | None = None


class SkipStopRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class RouteSummary(BaseModel):
    routes: int
    total_stops: int
    total_distance_km: float
    baseline_distance_km: float
    distance_saved_km: float
    distance_saved_pct: float | None
    total_duration_minutes: float
    estimated_cost: float
    planned_volume_liters: float
    planned_weight_kg: float
    by_status: dict[str, int]
    stops_completed: int
