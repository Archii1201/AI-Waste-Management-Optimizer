"""Zone request/response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ZoneType


class ZoneBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    zone_type: ZoneType = ZoneType.RESIDENTIAL
    description: str | None = None
    center_lat: float = Field(ge=-90, le=90)
    center_lon: float = Field(ge=-180, le=180)
    boundary: dict[str, Any] | None = Field(
        default=None, description="GeoJSON geometry drawn as the zone outline on the map"
    )
    population_served: int | None = Field(default=None, ge=0)


class ZoneCreate(ZoneBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class ZoneUpdate(BaseModel):
    """Every field optional: this is a PATCH, so omitted fields stay unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    zone_type: ZoneType | None = None
    description: str | None = None
    center_lat: float | None = Field(default=None, ge=-90, le=90)
    center_lon: float | None = Field(default=None, ge=-180, le=180)
    boundary: dict[str, Any] | None = None
    population_served: int | None = Field(default=None, ge=0)


class ZoneRead(ZoneBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    created_at: datetime
    updated_at: datetime


class ZoneStats(BaseModel):
    """Roll-up used by the dashboard's per-zone cards."""

    zone_id: int
    zone_code: str
    zone_name: str
    zone_type: ZoneType
    total_bins: int
    active_bins: int
    average_fill_level: float
    bins_needing_collection: int
    bins_overflowing: int
    total_capacity_liters: float
    estimated_current_volume_liters: float
