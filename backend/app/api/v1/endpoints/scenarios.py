"""Read-only what-if operational scenarios. Never persists a plan."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import scenario_service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class WhatIfRequest(BaseModel):
    extra_rounds: int = Field(0, ge=0, le=2)
    fleet_count: int | None = Field(None, ge=1, le=50)
    min_priority_score: float | None = Field(None, ge=0, le=1)
    horizon_hours: int | None = Field(None)


@router.post("/what-if")
def run_what_if(payload: WhatIfRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return scenario_service.evaluate(
            db,
            extra_rounds=payload.extra_rounds,
            fleet_count=payload.fleet_count,
            min_priority_score=payload.min_priority_score,
            horizon_hours=payload.horizon_hours,
        )
    finally:
        db.rollback()
