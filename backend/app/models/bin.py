"""Bins and their telemetry time-series.

`Bin` carries a denormalised snapshot of the latest reading (fill level, weight,
battery) so the dashboard map can render hundreds of bins with a single indexed
query, while `BinReading` keeps the full history that the forecasting model and
the analytics engine are trained on.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
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
from app.models.enums import BinStatus, ReadingSource, WasteType
from app.models.types import UTCDateTime, bigint_pk, enum_type, json_type

if TYPE_CHECKING:
    from app.models.collection import CollectionEvent
    from app.models.zone import Zone


class Bin(Base, TimestampMixin):
    __tablename__ = "bins"
    __table_args__ = (
        CheckConstraint("capacity_liters > 0", name="capacity_positive"),
        CheckConstraint("current_fill_level >= 0 AND current_fill_level <= 100", name="fill_level_range"),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="latitude_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="longitude_range"),
        Index("ix_bins_zone_status", "zone_id", "status"),
        Index("ix_bins_fill_level", "current_fill_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Human-readable identifier painted on the physical bin, e.g. "MUM-AND-0142".
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---------- Deliverable: bin location ----------
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # ---------- Deliverable: bin capacity ----------
    capacity_liters: Mapped[float] = mapped_column(Float, nullable=False, default=240.0)

    # ---------- Deliverable: waste type ----------
    waste_type: Mapped[WasteType] = mapped_column(
        enum_type(WasteType), nullable=False, default=WasteType.MIXED, index=True
    )

    status: Mapped[BinStatus] = mapped_column(
        enum_type(BinStatus), nullable=False, default=BinStatus.ACTIVE
    )

    # ---------- Deliverable: current fill level (latest telemetry snapshot) ----------
    current_fill_level: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_reading_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, index=True
    )
    last_emptied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Per-bin override of the global collection threshold; NULL falls back to the
    # value in settings. Lets operators treat a hospital bin differently.
    fill_threshold_override: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Rolling statistic maintained by the prediction job; also the cold-start
    # fallback used before enough history exists to train on.
    avg_fill_rate_pct_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How often this bin has actually overflowed. Feeds the prioritisation score
    # so chronic problem bins get serviced before first-time offenders.
    overflow_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    sensor_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    installed_on: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    zone: Mapped["Zone"] = relationship(back_populates="bins")
    readings: Mapped[list["BinReading"]] = relationship(
        back_populates="bin", cascade="all, delete-orphan", passive_deletes=True
    )
    collection_events: Mapped[list["CollectionEvent"]] = relationship(
        back_populates="bin", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def current_volume_liters(self) -> float:
        """Estimated litres currently held, used for vehicle capacity planning."""
        return self.capacity_liters * (self.current_fill_level / 100.0)

    @property
    def is_recyclable_stream(self) -> bool:
        return self.waste_type.is_recyclable

    def __repr__(self) -> str:
        return f"<Bin {self.code} {self.current_fill_level:.0f}% {self.waste_type.value}>"


class BinReading(Base):
    """One telemetry sample from a bin sensor.

    This is an append-only time-series. There is deliberately no `updated_at`:
    readings are facts about a moment and are never edited.
    """

    __tablename__ = "bin_readings"
    __table_args__ = (
        # Idempotent ingestion: a device retrying an MQTT publish must not create
        # duplicate history, which would corrupt the learned fill rates.
        UniqueConstraint("bin_id", "recorded_at", name="uq_bin_readings_bin_id_recorded_at"),
        CheckConstraint("fill_level >= 0 AND fill_level <= 100", name="fill_level_range"),
        Index("ix_bin_readings_bin_recorded", "bin_id", "recorded_at"),
        Index("ix_bin_readings_recorded_at", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True)
    bin_id: Mapped[int] = mapped_column(
        ForeignKey("bins.id", ondelete="CASCADE"), nullable=False
    )

    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fill_level: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_level: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[ReadingSource] = mapped_column(
        enum_type(ReadingSource), nullable=False, default=ReadingSource.REST
    )

    # Original device payload, retained for debugging sensor firmware issues.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)

    bin: Mapped["Bin"] = relationship(back_populates="readings")

    def __repr__(self) -> str:
        return f"<BinReading bin={self.bin_id} {self.fill_level:.0f}% @ {self.recorded_at:%Y-%m-%d %H:%M}>"
