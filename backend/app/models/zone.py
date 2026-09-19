"""Geographic zones: the unit of aggregation for analytics and anomaly detection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ZoneType
from app.models.types import enum_type, json_type

if TYPE_CHECKING:
    from app.models.bin import Bin


class Zone(Base, TimestampMixin):
    """A ward, neighbourhood or campus sector containing many bins.

    Zones exist because waste-generation anomalies are only meaningful relative
    to a local baseline: a spike in a commercial zone on a Saturday is normal,
    the same spike in a residential zone is not.
    """

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    zone_type: Mapped[ZoneType] = mapped_column(
        enum_type(ZoneType), nullable=False, default=ZoneType.RESIDENTIAL
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Representative point, used to centre the map and as a routing anchor.
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)

    # GeoJSON polygon ring for map rendering. Kept as JSONB so the project does
    # not require the PostGIS extension, which cloud tiers do not always enable.
    boundary: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)

    population_served: Mapped[int | None] = mapped_column(Integer, nullable=True)

    bins: Mapped[list["Bin"]] = relationship(back_populates="zone", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Zone {self.code} {self.name} ({self.zone_type.value})>"
