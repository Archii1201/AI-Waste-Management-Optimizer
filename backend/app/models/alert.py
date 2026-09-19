"""Alerts raised by the rule engine and the anomaly detector."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import AlertSeverity, AlertStatus, AlertType
from app.models.types import enum_type

if TYPE_CHECKING:
    from app.models.bin import Bin
    from app.models.zone import Zone


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status_severity", "status", "severity"),
        Index("ix_alerts_triggered_at", "triggered_at"),
        Index("ix_alerts_dedup_key", "dedup_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    alert_type: Mapped[AlertType] = mapped_column(enum_type(AlertType, 48), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        enum_type(AlertSeverity), nullable=False, default=AlertSeverity.WARNING
    )
    status: Mapped[AlertStatus] = mapped_column(
        enum_type(AlertStatus), nullable=False, default=AlertStatus.OPEN
    )

    # An alert is about a bin, a zone, or a vehicle; exactly which depends on type.
    bin_id: Mapped[int | None] = mapped_column(
        ForeignKey("bins.id", ondelete="CASCADE"), nullable=True, index=True
    )
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True
    )
    vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured payload (thresholds crossed, z-score, predicted overflow time)
    # so the UI can render a rich card instead of parsing the message string.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Stable identity for "the same problem", e.g. "overflow:bin:142". The alert
    # service refuses to raise a new alert for an open dedup_key inside the
    # cooldown window, which is what stops one full bin generating 50 alerts.
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)

    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How far past normal the observation was, for anomaly-type alerts.
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    bin: Mapped["Bin | None"] = relationship()
    zone: Mapped["Zone | None"] = relationship()

    @property
    def is_open(self) -> bool:
        return self.status in {AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED}

    def __repr__(self) -> str:
        return f"<Alert {self.alert_type.value} {self.severity.value} {self.status.value}>"
