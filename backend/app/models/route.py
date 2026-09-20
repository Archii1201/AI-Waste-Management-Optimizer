"""Optimizer output: routes and their ordered stops.

Persisting the solver result (rather than recomputing on every page load) means
the dashboard, the driver view and the post-hoc analytics all read the same plan,
and lets us measure planned-versus-actual performance afterwards.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RouteStatus, StopStatus
from app.models.types import UTCDateTime, enum_type, json_type

if TYPE_CHECKING:
    from app.models.bin import Bin
    from app.models.vehicle import Vehicle


class Route(Base, TimestampMixin):
    __tablename__ = "routes"
    __table_args__ = (
        Index("ix_routes_date_status", "planned_for", "status"),
        # SQLite recycles the highest rowid after a delete, so a replanned
        # route would silently reuse the id of the plan it replaced. AUTOINCREMENT
        # makes ids monotonic, matching PostgreSQL sequence behaviour.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True, nullable=False)

    vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    planned_for: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RouteStatus] = mapped_column(
        enum_type(RouteStatus), nullable=False, default=RouteStatus.PLANNED
    )

    # ---------- Plan metrics ----------
    total_stops: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_distance_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_duration_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    planned_volume_liters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    planned_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Distance a naive fixed-route "visit every bin in ID order" plan would take.
    # The gap between this and total_distance_km is the headline efficiency gain.
    baseline_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---------- Solver provenance ----------
    solver_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    solve_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimization_params: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)

    # Bins the solver could not fit, each with the reason, so dispatchers are
    # never silently left with uncollected high-priority bins.
    deferred_bins: Mapped[list[dict[str, Any]] | None] = mapped_column(json_type(), nullable=True)

    # Road-following polyline from OSRM for drawing the route on the map.
    geometry: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)

    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="routes")
    stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="RouteStop.sequence",
        passive_deletes=True,
    )

    @property
    def distance_saved_pct(self) -> float | None:
        if not self.baseline_distance_km or self.baseline_distance_km <= 0:
            return None
        return (1 - self.total_distance_km / self.baseline_distance_km) * 100.0

    def __repr__(self) -> str:
        return f"<Route {self.code} {self.total_stops} stops {self.total_distance_km:.1f}km>"


class RouteStop(Base):
    __tablename__ = "route_stops"
    __table_args__ = (
        UniqueConstraint("route_id", "sequence", name="uq_route_stops_route_id_sequence"),
        Index("ix_route_stops_bin", "bin_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    bin_id: Mapped[int] = mapped_column(ForeignKey("bins.id", ondelete="CASCADE"), nullable=False)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StopStatus] = mapped_column(
        enum_type(StopStatus), nullable=False, default=StopStatus.PENDING
    )

    # Snapshot of the priority score at planning time, so the plan stays
    # explainable even after live fill levels move on.
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    distance_from_previous_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    travel_time_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    service_time_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=6.0)

    planned_arrival: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    actual_arrival: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    expected_volume_liters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    skip_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    route: Mapped["Route"] = relationship(back_populates="stops")
    bin: Mapped["Bin"] = relationship()

    def __repr__(self) -> str:
        return f"<RouteStop #{self.sequence} route={self.route_id} bin={self.bin_id}>"
