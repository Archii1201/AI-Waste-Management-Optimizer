"""Dashboard-driven live simulation using the existing fleet simulator."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import simulation_control

router = APIRouter(prefix="/simulation", tags=["simulation"])


class SimulationStatus(BaseModel):
    running: bool
    ticks: int
    device_count: int
    last_update: datetime | str | None = None
    bins_updated: int | None = None
    predictions_refreshed: int | None = None
    new_alerts: int | None = None
    affected_routes: int | None = None


@router.get("/status", response_model=SimulationStatus)
def get_status() -> dict:
    return simulation_control.status()


@router.post("/start", response_model=SimulationStatus)
def start_simulation(db: Session = Depends(get_db)) -> dict:
    return simulation_control.start(db)


@router.post("/stop", response_model=SimulationStatus)
def stop_simulation() -> dict:
    return simulation_control.stop()


@router.post("/tick", response_model=SimulationStatus)
def tick_simulation(db: Session = Depends(get_db)) -> dict:
    return simulation_control.tick(db)
