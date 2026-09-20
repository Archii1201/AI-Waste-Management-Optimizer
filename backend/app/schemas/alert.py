"""Alert contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import AlertSeverity, AlertStatus, AlertType


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    bin_id: int | None
    zone_id: int | None
    title: str
    message: str
    details: dict[str, Any] | None
    dedup_key: str
    triggered_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None
    anomaly_score: float | None

    @computed_field  # type: ignore[prop-decorator]
    def is_open(self) -> bool:
        return self.status in (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)


class DetectRequest(BaseModel):
    zone_id: int | None = None


class DetectResponse(BaseModel):
    created: int
    suppressed: int
    auto_resolved: int
    by_type: dict[str, int]


class AcknowledgeRequest(BaseModel):
    acknowledged_by: str = Field(min_length=1, max_length=120)


class ResolveRequest(BaseModel):
    note: str | None = None


class AlertSummary(BaseModel):
    total: int
    open: int
    critical_open: int
    acknowledged: int
    resolved: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
