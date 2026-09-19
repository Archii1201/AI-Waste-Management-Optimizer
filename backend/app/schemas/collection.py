"""Collection-event contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import WasteType


class CollectionEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bin_id: int
    vehicle_id: int | None
    route_id: int | None
    collected_at: datetime
    fill_level_before: float
    volume_collected_liters: float
    weight_collected_kg: float
    recyclable_kg: float
    non_recyclable_kg: float
    waste_type: WasteType
    contamination_pct: float | None
    was_overflowing: bool
    hours_since_previous: float | None
    notes: str | None

    @computed_field  # type: ignore[prop-decorator]
    def diversion_rate_pct(self) -> float:
        total = self.recyclable_kg + self.non_recyclable_kg
        return round(self.recyclable_kg / total * 100.0, 1) if total > 0 else 0.0


class CollectionEventCreate(BaseModel):
    bin_id: int
    collected_at: datetime | None = None
    vehicle_id: int | None = None
    route_id: int | None = None
    weight_collected_kg: float | None = Field(default=None, ge=0)
    contamination_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = None
