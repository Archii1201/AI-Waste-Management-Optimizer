"""Vehicle request/response contracts."""

from __future__ import annotations

from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.enums import VehicleStatus, VehicleType, WasteType


class VehicleBase(BaseModel):
    registration_number: str | None = Field(default=None, max_length=32)
    vehicle_type: VehicleType = VehicleType.COMPACTOR
    capacity_liters: float = Field(gt=0, le=100_000)
    capacity_kg: float = Field(gt=0, le=60_000)
    accepted_waste_types: list[WasteType] | None = Field(
        default=None,
        description="Streams this vehicle may collect; null or empty means all",
    )
    depot_lat: float = Field(ge=-90, le=90)
    depot_lon: float = Field(ge=-180, le=180)
    shift_start: time = time(6, 0)
    shift_end: time = time(14, 0)
    avg_speed_kmph: float = Field(default=22.0, gt=0, le=120)
    cost_per_km: float = Field(default=18.0, ge=0)
    driver_name: str | None = Field(default=None, max_length=120)
    is_active: bool = True

    @model_validator(mode="after")
    def _shift_must_be_ordered(self) -> "VehicleBase":
        if self.shift_start >= self.shift_end:
            raise ValueError("shift_start must be earlier than shift_end")
        return self


class VehicleCreate(VehicleBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class VehicleUpdate(BaseModel):
    registration_number: str | None = Field(default=None, max_length=32)
    vehicle_type: VehicleType | None = None
    capacity_liters: float | None = Field(default=None, gt=0, le=100_000)
    capacity_kg: float | None = Field(default=None, gt=0, le=60_000)
    accepted_waste_types: list[WasteType] | None = None
    depot_lat: float | None = Field(default=None, ge=-90, le=90)
    depot_lon: float | None = Field(default=None, ge=-180, le=180)
    shift_start: time | None = None
    shift_end: time | None = None
    avg_speed_kmph: float | None = Field(default=None, gt=0, le=120)
    cost_per_km: float | None = Field(default=None, ge=0)
    driver_name: str | None = Field(default=None, max_length=120)
    status: VehicleStatus | None = None
    is_active: bool | None = None


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    registration_number: str | None
    vehicle_type: VehicleType
    capacity_liters: float
    capacity_kg: float
    accepted_waste_types: list[str] | None
    depot_lat: float
    depot_lon: float
    shift_start: time
    shift_end: time
    avg_speed_kmph: float
    cost_per_km: float
    status: VehicleStatus
    current_lat: float | None
    current_lon: float | None
    current_load_liters: float
    current_load_kg: float
    last_position_at: datetime | None
    driver_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    def load_utilisation_pct(self) -> float:
        if self.capacity_liters <= 0:
            return 0.0
        return round(min(100.0, self.current_load_liters / self.capacity_liters * 100.0), 1)

    @computed_field  # type: ignore[prop-decorator]
    def remaining_capacity_liters(self) -> float:
        return round(max(0.0, self.capacity_liters - self.current_load_liters), 1)


class VehiclePositionUpdate(BaseModel):
    """Live GPS ping from a vehicle, powering the moving markers on the map."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    status: VehicleStatus | None = None
    current_load_liters: float | None = Field(default=None, ge=0)
    current_load_kg: float | None = Field(default=None, ge=0)
    recorded_at: datetime | None = None
