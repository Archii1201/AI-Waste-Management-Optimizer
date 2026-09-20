"""Alert endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus, AlertType
from app.schemas.alert import (
    AcknowledgeRequest,
    AlertRead,
    AlertSummary,
    DetectRequest,
    DetectResponse,
    ResolveRequest,
)
from app.services import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/summary", response_model=AlertSummary, summary="Alert counts")
def summary(db: Session = Depends(get_db)) -> AlertSummary:
    return AlertSummary(**alert_service.alert_summary(db))


@router.get("", response_model=list[AlertRead], summary="List alerts")
def list_alerts(
    alert_status: AlertStatus | None = Query(None, alias="status"),
    severity: AlertSeverity | None = Query(None),
    alert_type: AlertType | None = Query(None),
    bin_id: int | None = Query(None),
    zone_id: int | None = Query(None),
    open_only: bool = Query(False, description="Only unresolved alerts"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[Alert]:
    return alert_service.list_alerts(
        db,
        status=alert_status,
        severity=severity,
        alert_type=alert_type,
        bin_id=bin_id,
        zone_id=zone_id,
        open_only=open_only,
        limit=limit,
        offset=offset,
    )


@router.post("/detect", response_model=DetectResponse, summary="Run alert detection")
def detect(payload: DetectRequest, db: Session = Depends(get_db)) -> DetectResponse:
    """Sweep every rule across the network.

    Safe to call repeatedly: conditions already flagged are suppressed rather
    than duplicated, and conditions that have cleared are auto-resolved.
    """
    report = alert_service.detect(db, zone_id=payload.zone_id)
    return DetectResponse(
        created=report.created,
        suppressed=report.suppressed,
        auto_resolved=report.auto_resolved,
        by_type=report.by_type,
    )


@router.get("/{alert_id}", response_model=AlertRead, summary="Fetch one alert")
def get_alert(alert_id: int, db: Session = Depends(get_db)) -> Alert:
    return alert_service.get_alert(db, alert_id)


@router.post(
    "/{alert_id}/acknowledge", response_model=AlertRead, summary="Acknowledge an alert"
)
def acknowledge(
    alert_id: int, payload: AcknowledgeRequest, db: Session = Depends(get_db)
) -> Alert:
    return alert_service.acknowledge(db, alert_id, who=payload.acknowledged_by)


@router.post("/{alert_id}/resolve", response_model=AlertRead, summary="Resolve an alert")
def resolve(alert_id: int, payload: ResolveRequest, db: Session = Depends(get_db)) -> Alert:
    return alert_service.resolve(db, alert_id, note=payload.note)
