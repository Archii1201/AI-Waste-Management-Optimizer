"""Telemetry ingestion endpoints.

These are the REST half of the IoT layer; the MQTT bridge added in the next step
calls the very same service functions, so both transports behave identically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.bin import BinReading
from app.schemas.reading import (
    BulkTelemetryIn,
    BulkTelemetryResult,
    ReadingRead,
    TelemetryIn,
    TelemetryResult,
)
from app.services import telemetry_service

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post(
    "",
    response_model=TelemetryResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest one sensor reading",
)
def ingest(payload: TelemetryIn, db: Session = Depends(get_db)) -> TelemetryResult:
    return telemetry_service.ingest_reading(db, payload)


@router.post(
    "/bulk",
    response_model=BulkTelemetryResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a batch buffered by an offline gateway",
)
def ingest_bulk(payload: BulkTelemetryIn, db: Session = Depends(get_db)) -> BulkTelemetryResult:
    return telemetry_service.ingest_bulk(db, payload.readings)


@router.get(
    "/recent",
    response_model=list[ReadingRead],
    summary="Most recent readings across the whole network",
)
def recent_readings(
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[ReadingRead]:
    rows = db.scalars(
        select(BinReading).order_by(BinReading.recorded_at.desc()).limit(limit)
    )
    return [ReadingRead.model_validate(r) for r in rows]
