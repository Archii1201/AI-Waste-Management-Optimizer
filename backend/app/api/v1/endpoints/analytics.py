"""Analytics and recommendation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.analytics import AnalyticsOverview, Recommendation, ZoneAnalytics
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])

WindowDays = Query(30, ge=1, le=365, description="Days of history to analyse")


@router.get("/overview", response_model=AnalyticsOverview, summary="Dashboard overview")
def overview(days: int = WindowDays, db: Session = Depends(get_db)) -> dict:
    """Collections, routes, fill, waste and alerts in a single call."""
    return analytics_service.overview(db, days=days)


@router.get("/collections", summary="Collection statistics")
def collections(days: int = WindowDays, db: Session = Depends(get_db)) -> dict[str, Any]:
    return analytics_service.collection_stats(db, days=days)


@router.get("/routes", summary="Route efficiency statistics")
def routes(days: int = WindowDays, db: Session = Depends(get_db)) -> dict[str, Any]:
    return analytics_service.route_stats(db, days=days)


@router.get("/fill", summary="Network fill and forecast accuracy")
def fill(db: Session = Depends(get_db)) -> dict[str, Any]:
    return analytics_service.fill_stats(db)


@router.get("/waste-estimation", summary="Recyclable vs non-recyclable estimate")
def waste_estimation(days: int = WindowDays, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Tonnage recovered against tonnage landfilled, by stream."""
    return analytics_service.waste_stats(db, days=days)


@router.get("/zones", response_model=list[ZoneAnalytics], summary="Per-zone comparison")
def zones(days: int = WindowDays, db: Session = Depends(get_db)) -> list[dict]:
    """Zones ordered by waste generated per bin per day."""
    return analytics_service.zone_stats(db, days=days)


@router.get(
    "/recommendations",
    response_model=list[Recommendation],
    summary="Operational recommendations",
)
def recommendations(days: int = WindowDays, db: Session = Depends(get_db)) -> list[dict]:
    """Concrete advice derived from the metrics, each with its supporting evidence."""
    return analytics_service.recommendations(db, days=days)
