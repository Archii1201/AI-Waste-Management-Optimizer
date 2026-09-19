"""Bin request/response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings
from app.models.enums import BinStatus, WasteType


class BinBase(BaseModel):
    label: str | None = Field(default=None, max_length=160)
    address: str | None = None
    zone_id: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    capacity_liters: float = Field(gt=0, le=50_000)
    waste_type: WasteType = WasteType.MIXED
    status: BinStatus = BinStatus.ACTIVE
    fill_threshold_override: float | None = Field(default=None, ge=0, le=100)
    sensor_id: str | None = Field(default=None, max_length=64)
    installed_on: datetime | None = None
    notes: str | None = None


class BinCreate(BinBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class BinUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=160)
    address: str | None = None
    zone_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    capacity_liters: float | None = Field(default=None, gt=0, le=50_000)
    waste_type: WasteType | None = None
    status: BinStatus | None = None
    fill_threshold_override: float | None = Field(default=None, ge=0, le=100)
    sensor_id: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class BinRead(BaseModel):
    """Bin as the map and list views consume it.

    The computed fields exist so every client renders the same status bands from
    the same thresholds, instead of each one reimplementing the rules.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: str | None
    address: str | None
    zone_id: int
    latitude: float
    longitude: float
    capacity_liters: float
    waste_type: WasteType
    status: BinStatus
    current_fill_level: float
    current_weight_kg: float | None
    battery_level: float | None
    last_reading_at: datetime | None
    last_emptied_at: datetime | None
    fill_threshold_override: float | None
    avg_fill_rate_pct_per_hour: float | None
    overflow_count: int
    sensor_id: str | None
    installed_on: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_threshold(self) -> float:
        return (
            self.fill_threshold_override
            if self.fill_threshold_override is not None
            else settings.bin_full_threshold
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_volume_liters(self) -> float:
        return round(self.capacity_liters * self.current_fill_level / 100.0, 1)

    @computed_field  # type: ignore[prop-decorator]
    def needs_collection(self) -> bool:
        return self.current_fill_level >= self.effective_threshold

    @computed_field  # type: ignore[prop-decorator]
    def is_overflowing(self) -> bool:
        return self.current_fill_level >= settings.bin_critical_threshold

    @computed_field  # type: ignore[prop-decorator]
    def is_recyclable_stream(self) -> bool:
        return self.waste_type.is_recyclable

    @computed_field  # type: ignore[prop-decorator]
    def fill_status(self) -> str:
        """Colour band for the map marker."""
        if self.current_fill_level >= settings.bin_critical_threshold:
            return "critical"
        if self.current_fill_level >= self.effective_threshold:
            return "high"
        if self.current_fill_level >= 50:
            return "medium"
        return "low"


class BinSummary(BaseModel):
    """Network-wide counters for the dashboard header cards."""

    total_bins: int
    active_bins: int
    offline_bins: int
    maintenance_bins: int
    bins_needing_collection: int
    bins_overflowing: int
    average_fill_level: float
    total_capacity_liters: float
    estimated_current_volume_liters: float
    by_waste_type: dict[str, int]
    by_fill_band: dict[str, int]
    stale_sensors: int = Field(
        description="Bins with no telemetry in the last 24 hours, i.e. likely dead sensors"
    )


class BinEmptyRequest(BaseModel):
    """Manual collection record, used when a crew empties a bin off-route."""

    collected_at: datetime | None = Field(
        default=None, description="Defaults to now if omitted"
    )
    vehicle_id: int | None = None
    weight_collected_kg: float | None = Field(default=None, ge=0)
    contamination_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = None
