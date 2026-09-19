"""Collection events: the ground truth for waste estimation and analytics.

Every time a bin is emptied one row lands here. This table answers the
"estimate recyclable and non-recyclable waste collected over time" deliverable
directly, and supplies the labels that the schedule-recommendation engine uses
to detect over- and under-servicing.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import WasteType
from app.models.types import UTCDateTime, bigint_pk, enum_type

if TYPE_CHECKING:
    from app.models.bin import Bin
    from app.models.vehicle import Vehicle


class CollectionEvent(Base):
    __tablename__ = "collection_events"
    __table_args__ = (
        CheckConstraint("volume_collected_liters >= 0", name="volume_non_negative"),
        CheckConstraint("weight_collected_kg >= 0", name="weight_non_negative"),
        Index("ix_collection_events_bin_time", "bin_id", "collected_at"),
        Index("ix_collection_events_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True)
    bin_id: Mapped[int] = mapped_column(
        ForeignKey("bins.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[int | None] = mapped_column(
        ForeignKey("routes.id", ondelete="SET NULL"), nullable=True
    )

    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    # Fill level immediately before emptying. Comparing this against the
    # collection threshold is how over-servicing ("emptied at 30%") is detected.
    fill_level_before: Mapped[float] = mapped_column(Float, nullable=False)

    volume_collected_liters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weight_collected_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ---------- Deliverable: recyclable vs non-recyclable estimation ----------
    recyclable_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    non_recyclable_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    waste_type: Mapped[WasteType] = mapped_column(enum_type(WasteType), nullable=False)

    # Share of a recyclable stream spoiled by wrong-bin disposal. Sourced from
    # the image classifier and used for the recycling-improvement recommendations.
    contamination_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # True if the bin was at or past the critical threshold when serviced, i.e.
    # the system failed to collect it in time. Drives the SLA metrics.
    was_overflowing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    hours_since_previous: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    bin: Mapped["Bin"] = relationship(back_populates="collection_events")
    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="collection_events")

    @property
    def diversion_rate_pct(self) -> float:
        """Share of this collection diverted from landfill into recycling."""
        total = self.recyclable_kg + self.non_recyclable_kg
        return (self.recyclable_kg / total * 100.0) if total > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"<CollectionEvent bin={self.bin_id} {self.weight_collected_kg:.1f}kg "
            f"@ {self.collected_at:%Y-%m-%d %H:%M}>"
        )
