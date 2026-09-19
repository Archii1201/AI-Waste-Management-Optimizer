"""Reference data endpoint.

The dashboard fetches its dropdown options, colour bands and map centre from
here instead of hardcoding them. That way changing a threshold in `.env` updates
the UI too, and the two halves of the system can never disagree about what
"needs collection" means.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    BinStatus,
    PriorityTier,
    RouteStatus,
    VehicleStatus,
    VehicleType,
    WasteType,
    ZoneType,
)
from app.services.waste import BULK_DENSITY_KG_PER_LITER

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("", summary="Vocabularies, thresholds and map defaults")
def reference_data() -> dict:
    return {
        "waste_types": [
            {
                "value": w.value,
                "label": w.value.replace("_", " ").title(),
                "recyclable": w.is_recyclable,
                "decay_factor": w.decay_factor,
                "density_kg_per_liter": BULK_DENSITY_KG_PER_LITER[w],
            }
            for w in WasteType
        ],
        "zone_types": [z.value for z in ZoneType],
        "bin_statuses": [s.value for s in BinStatus],
        "vehicle_types": [v.value for v in VehicleType],
        "vehicle_statuses": [v.value for v in VehicleStatus],
        "route_statuses": [r.value for r in RouteStatus],
        "priority_tiers": [p.value for p in PriorityTier],
        "alert_types": [a.value for a in AlertType],
        "alert_severities": [a.value for a in AlertSeverity],
        "alert_statuses": [a.value for a in AlertStatus],
        "thresholds": {
            "bin_full": settings.bin_full_threshold,
            "bin_critical": settings.bin_critical_threshold,
            "overflow_alert_horizon_hours": settings.overflow_alert_horizon_hours,
            "sensor_stale_hours": settings.sensor_stale_hours,
        },
        "fill_bands": {
            "low": [0, 50],
            "medium": [50, settings.bin_full_threshold],
            "high": [settings.bin_full_threshold, settings.bin_critical_threshold],
            "critical": [settings.bin_critical_threshold, 100],
        },
        "map": {
            "city": settings.city_name,
            "center": [settings.city_center_lat, settings.city_center_lon],
            "timezone": settings.city_timezone,
        },
    }
