"""Collection vehicles: the resources the route optimizer schedules."""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import VehicleStatus, VehicleType
from app.models.types import UTCDateTime, enum_type, json_type

if TYPE_CHECKING:
    from app.models.collection import CollectionEvent
    from app.models.route import Route


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint("capacity_liters > 0", name="capacity_liters_positive"),
        CheckConstraint("capacity_kg > 0", name="capacity_kg_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    registration_number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    vehicle_type: Mapped[VehicleType] = mapped_column(
        enum_type(VehicleType), nullable=False, default=VehicleType.COMPACTOR
    )

    # ---------- Routing constraint: vehicle capacity ----------
    # Both dimensions are modelled because waste is volume-limited for light
    # streams (plastic, paper) and weight-limited for heavy ones (glass, organic).
    capacity_liters: Mapped[float] = mapped_column(Float, nullable=False, default=8000.0)
    capacity_kg: Mapped[float] = mapped_column(Float, nullable=False, default=5000.0)

    # ---------- Routing constraint: waste-type compatibility ----------
    # A dedicated recycling truck must not be assigned organic bins. Stored as a
    # JSONB array of WasteType values; empty/NULL means "accepts everything".
    accepted_waste_types: Mapped[list[str] | None] = mapped_column(json_type(), nullable=True)

    # ---------- Routing constraint: depot and shift window ----------
    depot_lat: Mapped[float] = mapped_column(Float, nullable=False)
    depot_lon: Mapped[float] = mapped_column(Float, nullable=False)
    shift_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(6, 0))
    shift_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(14, 0))

    avg_speed_kmph: Mapped[float] = mapped_column(Float, nullable=False, default=22.0)
    cost_per_km: Mapped[float] = mapped_column(Float, nullable=False, default=18.0)

    # ---------- Live telemetry, shown as moving markers on the dashboard ----------
    status: Mapped[VehicleStatus] = mapped_column(
        enum_type(VehicleStatus), nullable=False, default=VehicleStatus.IDLE
    )
    current_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_load_liters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_load_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_position_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    driver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    routes: Mapped[list["Route"]] = relationship(back_populates="vehicle")
    collection_events: Mapped[list["CollectionEvent"]] = relationship(back_populates="vehicle")

    @property
    def remaining_capacity_liters(self) -> float:
        return max(0.0, self.capacity_liters - self.current_load_liters)

    @property
    def load_utilisation_pct(self) -> float:
        if self.capacity_liters <= 0:
            return 0.0
        return min(100.0, self.current_load_liters / self.capacity_liters * 100.0)

    def accepts(self, waste_type: str) -> bool:
        if not self.accepted_waste_types:
            return True
        return waste_type in self.accepted_waste_types

    def __repr__(self) -> str:
        return f"<Vehicle {self.code} {self.vehicle_type.value} {self.load_utilisation_pct:.0f}% loaded>"
