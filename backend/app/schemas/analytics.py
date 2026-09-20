"""Analytics contracts.

Metric blocks are typed as open dicts rather than exhaustive models: they are
read-only aggregates that will grow as the dashboard asks for more, and pinning
every key here would mean editing two files for every new number.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.alert import AlertSummary


class Recommendation(BaseModel):
    category: str
    priority: str
    title: str
    detail: str
    action: str
    evidence: dict[str, Any]


class AnalyticsOverview(BaseModel):
    window_days: int
    collections: dict[str, Any]
    routes: dict[str, Any]
    fill: dict[str, Any]
    waste: dict[str, Any]
    alerts: AlertSummary


class ZoneAnalytics(BaseModel):
    zone_id: int
    zone_code: str
    zone_name: str
    zone_type: str
    bins: int
    avg_fill_level: float
    collections: int
    collected_weight_kg: float
    kg_per_bin_per_day: float
    overflow_events: int
    avg_fill_at_collection: float | None
    open_alerts: int
