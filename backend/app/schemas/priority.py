"""Collection prioritisation contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import PriorityTier, WasteType


class PriorityComponentsOut(BaseModel):
    """The weighted terms behind the score, so the ranking is explainable."""

    fill: float
    overflow: float
    waste_type: float
    location: float
    chronic: float


class PrioritizedBinOut(BaseModel):
    bin_id: int
    code: str
    label: str | None
    zone_id: int
    latitude: float
    longitude: float
    waste_type: WasteType
    capacity_liters: float
    fill_level: float

    score: float
    tier: PriorityTier
    components: PriorityComponentsOut
    weights: dict[str, float]

    hours_to_full: float | None
    is_overdue: bool
    distance_km: float | None
    expected_volume_liters: float
    expected_weight_kg: float
    reasons: list[str]


class PrioritySummary(BaseModel):
    total: int
    by_tier: dict[str, int]
    total_volume_liters: float
    total_weight_kg: float
    bins_over_threshold: int
    weights: dict[str, float]


class PriorityQuery(BaseModel):
    zone_id: int | None = None
    origin_lat: float | None = Field(None, ge=-90, le=90)
    origin_lon: float | None = Field(None, ge=-180, le=180)
    min_score: float | None = Field(None, ge=0, le=1)
    limit: int = Field(50, ge=1, le=500)
